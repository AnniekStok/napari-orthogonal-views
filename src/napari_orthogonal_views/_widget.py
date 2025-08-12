import contextlib
from collections.abc import Callable
from types import MethodType

import napari
from napari.components.viewer_model import ViewerModel
from napari.layers import Labels, Layer
from napari.qt import QtViewer
from napari.utils.events import Event
from napari.utils.events.event import WarningEmitter
from qtpy.QtWidgets import (
    QHBoxLayout,
    QWidget,
)


def copy_layer(layer: Layer, name: str = ""):
    res_layer = Layer.create(*layer.as_layer_data_tuple())
    res_layer.metadata["viewer_name"] = name

    # connect to the same undo/redo history in the case of labels layers
    if isinstance(layer, Labels):
        res_layer._undo_history = layer._undo_history
        res_layer._redo_history = layer._redo_history
    return res_layer


def get_property_names(layer: Layer):
    klass = layer.__class__
    res = []
    for event_name, event_emitter in layer.events.emitters.items():
        if isinstance(event_emitter, WarningEmitter):
            continue
        if event_name in ("thumbnail", "name"):
            continue
        if (
            isinstance(getattr(klass, event_name, None), property)
            and getattr(klass, event_name).fset is not None
        ):
            res.append(event_name)
    return res


class own_partial:
    """
    Workaround for deepcopy not copying partial functions
    (Qt widgets are not serializable)
    """

    def __init__(self, func, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def __call__(self, *args, **kwargs):
        return self.func(*(self.args + args), **{**self.kwargs, **kwargs})


class ViewerModelContainer:
    """
    A container that holds a ViewerModel and manages synchronization.
    """

    def __init__(self, title: str, rel_order: tuple[int]):
        self.title = title
        self.rel_order = rel_order
        self.viewer_model = ViewerModel(title)
        self.viewer_model.axes.visible = True
        self._block = False

    def add_layer(self, orig_layer: Layer, index: int):
        """Set the layers of the contained ViewerModel."""
        self.viewer_model.layers.insert(
            index, copy_layer(orig_layer, self.title)
        )
        copied_layer = self.viewer_model.layers[orig_layer.name]

        # sync name
        def sync_name_wrapper(event):
            return self.sync_name(orig_layer, copied_layer, event)

        orig_layer.events.name.connect(sync_name_wrapper)

        # sync properties
        for property_name in get_property_names(orig_layer):
            # sync in both directions (from original layer to copied layer and back)
            getattr(orig_layer.events, property_name).connect(
                own_partial(
                    self.sync_property,
                    property_name,
                    orig_layer,
                    copied_layer,
                )
            )

            getattr(copied_layer.events, property_name).connect(
                own_partial(
                    self.sync_property,
                    property_name,
                    copied_layer,
                    orig_layer,
                )
            )

        if isinstance(orig_layer, Labels):

            # Calling the Undo/Redo function on the labels layer should also refresh the
            # other views.
            def wrap_undo_redo(
                source_layer: Labels, target_layer: Labels, update_fn: Callable
            ):
                """Wrap undo and redo methods to trigger syncing via update_fn"""
                orig_undo = source_layer.undo
                orig_redo = source_layer.redo

                def wrapped_undo(self):
                    orig_undo()
                    update_fn(source=self, target=target_layer, event=None)

                def wrapped_redo(self):
                    orig_redo()
                    update_fn(source=self, target=target_layer, event=None)

                # Replace methods on the instance
                source_layer.undo = MethodType(wrapped_undo, source_layer)
                source_layer.redo = MethodType(wrapped_redo, source_layer)

            # Wrap undo/redo
            wrap_undo_redo(copied_layer, orig_layer, self.update_data)
            wrap_undo_redo(orig_layer, copied_layer, self.update_data)

            # if the original layer is a labels layer, we want to connect to the paint event,
            # because we need it in order to invoke syncing between the different viewers.
            # (Paint event does not trigger 'data' event by itself).
            # We do not need to connect to the eraser and fill bucket separately.
            copied_layer.events.paint.connect(
                lambda event: self.update_data(
                    source=copied_layer, target=orig_layer, event=event
                )  # copy data from copied_layer to orig_layer (orig_layer emits signal, which triggers update on other viewer models, if present)
            )
            orig_layer.events.paint.connect(
                lambda event: self.update_data(
                    source=orig_layer, target=copied_layer, event=event
                )  # copy data from orig_layer to copied_layer (copied_layer emits signal but we don't process it)
            )

    def update_data(
        self, source: Labels, target: Labels, event: Event
    ) -> None:
        """Copy data from source layer to target layer, which triggers a data event on the target layer. Block syncing to itself (VM1 -> orig -> VM1 is blocked, but VM1 -> orig -> VM2 is not blocked)
        Args:
            source: the source Labels layer
            target: the target Labels layer
            event: the event to be triggered (not used)"""

        self._block = True  # no syncing to itself is necessary
        target.data = (
            source.data
        )  # trigger data event so that it can sync to other viewer models (only if target layer is orig_layer)
        self._block = False

    def sync_name(self, orig_layer: Layer, copied_layer: Layer, event: Event):
        """Forward the renaming event from original layer to copied layer"""

        copied_layer.name = orig_layer.name

    def sync_property(
        self,
        property_name: str,
        source_layer: Layer,
        target_layer: Layer,
        event: Event,
    ):
        """Sync a property of a layer in this viewer model."""

        if self._block:
            return

        self._block = True
        setattr(
            target_layer,
            property_name,
            getattr(source_layer, property_name),
        )
        self._block = False


class OrthViewerWidget(QWidget):
    """Secondary viewer widget to hold another canvas showing the same data as the viewer but in a different orientation."""

    def __init__(
        self,
        viewer: napari.Viewer,
        order=(-2, -3, -1),
        sync_axes: list[int] = [0],
    ):
        super().__init__()
        self.viewer = viewer
        self.viewer.axes.visible = True
        self.viewer.axes.events.visible.connect(self.set_orth_views_dims_order)
        self.vm_container = ViewerModelContainer(
            title="orthogonal view", rel_order=order
        )

        self.sync_axes = sync_axes
        self._block_center = False  # Separate flag from zoom
        self._block_step = False

        self.qt_viewer = QtViewer(self.vm_container.viewer_model)

        layout = QHBoxLayout()
        layout.addWidget(self.qt_viewer)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Add the layers currently in the viewer
        for i, layer in enumerate(self.viewer.layers):
            self.vm_container.add_layer(layer, i)

        # Connect to events
        self._connections = []
        self._connect(self.viewer.layers.events.inserted, self._layer_added)
        self._connect(self.viewer.layers.events.removed, self._layer_removed)
        self._connect(self.viewer.layers.events.moved, self._layer_moved)
        self._connect(
            self.viewer.layers.selection.events.active,
            self._layer_selection_changed,
        )
        self._connect(self.viewer.events.reset_view, self._reset_view)
        self._connect(
            self.viewer.dims.events.current_step, self._update_current_step
        )
        self._connect(
            self.vm_container.viewer_model.dims.events.current_step,
            self._update_current_step,
        )

        # Adjust dimensions for orthogonal views
        self.set_orth_views_dims_order()

    def sync_event(
        self, source_emitter, target_callable, sync: bool, key_label=None
    ):
        """
        Connect or disconnect an event from a source emitter to a target callable.

        Parameters
        ----------
        source_emitter : napari EventEmitter
            The source event emitter (e.g., viewer.camera.events.zoom).
        target_callable : callable
            Function to call when the source event fires.
            Signature: target_callable(event)
        sync : bool
            True to connect, False to disconnect.
        """
        if not hasattr(self, "_sync_handlers"):
            self._sync_handlers = {}

        if key_label is None:
            key_label = id(target_callable)

        key = (id(source_emitter), key_label)

        if sync:
            if key in self._sync_handlers:
                return

            def handler(event, _fn=target_callable):
                _fn(event)

            self._sync_handlers[key] = handler
            self._connect(source_emitter, handler)
        else:
            if key not in self._sync_handlers:
                return
            handler = self._sync_handlers.pop(key)
            self._disconnect(source_emitter, handler)

    def _connect(self, emitter, handler):
        emitter.connect(handler)
        self._connections.append((emitter, handler))

    def _disconnect(self, emitter, handler):
        with contextlib.suppress(ValueError):
            emitter.disconnect(handler)
            self._connections.remove((emitter, handler))

    def cleanup(self):
        for sig, handler in self._connections:
            try:
                sig.disconnect(handler)
            except Exception:
                pass
        self._connections.clear()

    def set_orth_views_dims_order(self):
        """The the order of the z,y,x dims in the orthogonal views, by using the rel_order attribute of the viewer models"""

        # TODO: allow the user to provide the dimension order and names.
        axis_labels = (
            "t",
            "z",
            "y",
            "x",
        )  # assume default axis labels for now
        order = list(self.viewer.dims.order)

        if len(order) > 2:
            # model 1 axis order (e.g. xz view)
            m1_order = list(order)
            m1_order[-3:] = (
                m1_order[self.vm_container.rel_order[0]],
                m1_order[self.vm_container.rel_order[1]],
                m1_order[self.vm_container.rel_order[2]],
            )
            self.vm_container.viewer_model.dims.order = m1_order

        if len(order) == 3:  # assume we have zyx axes
            self.viewer.dims.axis_labels = axis_labels[1:]
            self.vm_container.viewer_model.dims.axis_labels = axis_labels[1:]
        elif len(order) == 4:  # assume we have tzyx axes
            self.viewer.dims.axis_labels = axis_labels
            self.vm_container.viewer_model.dims.axis_labels = axis_labels

        # whether or not the axis should be visible
        self.vm_container.viewer_model.axes.visible = self.viewer.axes.visible

    def _reset_view(self):
        """Propagate the reset view event"""

        self.vm_container.viewer_model.reset_view()

    def _layer_selection_changed(self, event):
        """Update of current active layers"""

        if event.value is None:
            self.vm_container.viewer_model.layers.selection.active = None
            return

        if event.value.name in self.vm_container.viewer_model.layers:
            self.vm_container.viewer_model.layers.selection.active = (
                self.vm_container.viewer_model.layers[event.value.name]
            )

    def _update_current_step(self, event):
        """Sync the current step between different viewer models"""

        if self._block_center:
            return

        self._block_center = True
        for model in [
            self.viewer,
            self.vm_container.viewer_model,
        ]:
            if model.dims is event.source:
                continue
            model.dims.current_step = event.value
        self._block_center = False

    def _layer_added(self, event):
        """Add layer to additional other viewer models"""

        if event.value.name not in self.vm_container.viewer_model.layers:
            self.vm_container.add_layer(event.value, event.index)

        self.set_orth_views_dims_order()

    def _layer_removed(self, event):
        """Remove layer in all viewer models"""

        layer_name = event.value.name
        if layer_name in self.vm_container.viewer_model.layers:
            self.vm_container.viewer_model.layers.pop(layer_name)
        self.set_orth_views_dims_order()

    def _layer_moved(self, event):
        """Update order of layers in all viewer models"""

        dest_index = (
            event.new_index
            if event.new_index < event.index
            else event.new_index + 1
        )
        self.vm_container.viewer_model.layers.move(event.index, dest_index)
