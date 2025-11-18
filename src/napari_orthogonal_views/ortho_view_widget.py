import contextlib
import warnings
from collections.abc import Callable
from types import MethodType

import napari
from napari.components.viewer_model import ViewerModel
from napari.layers import Labels, Layer
from napari.qt import QtViewer
from napari.utils.events import Event, EventEmitter
from napari.utils.events.event import WarningEmitter
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QHBoxLayout,
    QWidget,
)

from napari_orthogonal_views.cross_hair_overlay import CrosshairOverlay


def activate_on_hover(qt_viewer: QtViewer):
    """Activate mouse tracking on the canvas, so that it is not necessary to click first."""
    canvas = qt_viewer.canvas.native
    canvas.setMouseTracking(True)

    def on_enter(event):
        canvas.setFocus(Qt.MouseFocusReason)
        return super(type(canvas), canvas).enterEvent(event)

    canvas.enterEvent = on_enter


def copy_layer(layer: Layer, name: str = "") -> Layer:
    """Clone a napari layer from its data (shallow copy, both original layer and copied
    layer share the same underlying data)."""

    copied_layer = Layer.create(*layer.as_layer_data_tuple())
    copied_layer.metadata["viewer_name"] = name

    # connect to the same undo/redo history in the case of labels layers
    if isinstance(layer, Labels):
        copied_layer._undo_history = layer._undo_history
        copied_layer._redo_history = layer._redo_history

    return copied_layer


def get_property_names(layer: Layer) -> list[str]:
    """Return a list of all the layer properties (opacity, mode, ...) that emit events."""

    klass = layer.__class__
    emitter_list = []
    for event_name, event_emitter in layer.events.emitters.items():
        if isinstance(event_emitter, WarningEmitter):
            continue
        if event_name in ("thumbnail", "name"):
            continue
        if (
            isinstance(getattr(klass, event_name, None), property)
            and getattr(klass, event_name).fset is not None
        ):
            emitter_list.append(event_name)
    return emitter_list


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

    def __init__(self, title: str, rel_order: tuple[int], sync_filters=None):
        self.title = title
        self.rel_order = rel_order
        self.viewer_model = ViewerModel(title)
        self.viewer_model.axes.visible = True
        self._block = False
        self._layer_hooks: dict[type, list[Callable]] = {}
        self.sync_filters = sync_filters or {}

        # Add crosshair overlays (initially invisible)
        cursor_overlay = CrosshairOverlay(
            blending="translucent_no_depth", axis_order=self.rel_order
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.viewer_model._overlays["crosshairs"] = cursor_overlay

    def _sync_layer_properties(
        self, orig_layer: Layer, copied_layer: Layer
    ) -> None:
        """Sync properties between orig_layer and copied_layer, applying optional
        sync_filters."""

        def is_excluded(layer, prop, direction):
            """Check whether to skip syncing a property in a given direction."""
            for cls, rules in self.sync_filters.items():
                if isinstance(layer, cls):
                    excluded = rules.get(f"{direction}_exclude", set())
                    if excluded == "*":  # block all
                        return True
                    if prop in excluded:
                        return True
            return False

        for property_name in get_property_names(orig_layer):
            # Forward sync: orig_layer → copied_layer
            if not is_excluded(orig_layer, property_name, "forward"):

                # first copy the value immediately
                if (
                    property_name != "current_size"
                ):  # skip initially (special case)
                    setattr(
                        copied_layer,
                        property_name,
                        getattr(orig_layer, property_name),
                    )

                # set up syncing
                getattr(orig_layer.events, property_name).connect(
                    own_partial(
                        self._sync_property,
                        property_name,
                        orig_layer,
                        copied_layer,
                    )
                )

            # Reverse sync: copied_layer → orig_layer
            if not is_excluded(orig_layer, property_name, "reverse"):
                getattr(copied_layer.events, property_name).connect(
                    own_partial(
                        self._sync_property,
                        property_name,
                        copied_layer,
                        orig_layer,
                    )
                )

    def _update_data(
        self, source: Labels, target: Labels, event: Event
    ) -> None:
        """Copy data from source layer to target layer, which triggers a data event on
        the target layer. Block syncing to itself (VM1 -> orig -> VM1 is blocked, but
        VM1 -> orig -> VM2 is not blocked)
        Args:
            source: the source Labels layer
            target: the target Labels layer
            event: the event to be triggered (not used)"""

        self._block = True  # no syncing to itself is necessary
        target.data = (
            source.data
        )  # trigger data event so that it can sync to other viewer models (only if
        # target layer is orig_layer)
        self._block = False

    def _sync_name(
        self, orig_layer: Layer, copied_layer: Layer, event: Event
    ) -> None:
        """Forward the renaming event from original layer to copied layer"""

        copied_layer.name = orig_layer.name

    def _sync_property(
        self,
        property_name: str,
        source_layer: Layer,
        target_layer: Layer,
        event: Event,
    ) -> None:
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

    def set_layer_hooks(self, hooks: dict[type, list[Callable]]) -> None:
        """Replace current hook mapping."""

        self._layer_hooks = hooks

    def add_layer(self, orig_layer: Layer, index: int) -> None:
        """Set the layers of the contained ViewerModel."""

        self.viewer_model.layers.insert(
            index, copy_layer(orig_layer, self.title)
        )
        copied_layer = self.viewer_model.layers[orig_layer.name]

        # sync name
        def sync_name_wrapper(event):
            return self._sync_name(orig_layer, copied_layer, event)

        orig_layer.events.name.connect(sync_name_wrapper)

        # sync properties
        self._sync_layer_properties(orig_layer, copied_layer)

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
            wrap_undo_redo(copied_layer, orig_layer, self._update_data)
            wrap_undo_redo(orig_layer, copied_layer, self._update_data)

            # if the original layer is a labels layer, we want to connect to the paint
            # event, because we need it in order to invoke syncing between the different
            # viewers. (Paint event does not trigger 'data' event by itself).
            # We do not need to connect to the eraser and fill bucket separately.
            copied_layer.events.paint.connect(
                lambda event: self._update_data(
                    source=copied_layer, target=orig_layer, event=event
                )  # copy data from copied_layer to orig_layer (orig_layer emits signal,
                # which triggers update on other viewer models, if present)
            )
            orig_layer.events.paint.connect(
                lambda event: self._update_data(
                    source=orig_layer, target=copied_layer, event=event
                )  # copy data from orig_layer to copied_layer (copied_layer emits signal
                # but we don't process it)
            )

        # Special hooks based on layer type
        for hook_type, hooks in self._layer_hooks.items():
            if isinstance(orig_layer, hook_type):
                for hook in hooks:
                    hook(orig_layer, copied_layer)


class OrthoViewWidget(QWidget):
    """Secondary viewer widget to hold another canvas showing the same data as the viewer
    but in a different orientation."""

    def __init__(
        self,
        viewer: napari.Viewer,
        order=(-2, -3, -1),
        sync_axes: list[int] | None = None,
        sync_filters: dict | None = None,
        layer_hooks: dict | None = None,
    ):
        super().__init__()
        self.viewer = viewer
        self.viewer.axes.visible = True
        self.viewer.axes.events.visible.connect(
            self._set_orth_views_dims_order
        )
        if sync_axes is None:
            sync_axes = [0]
        self.sync_axes = sync_axes
        self._block_center = False
        self._block_step = False
        self._layer_hooks = layer_hooks

        # create container to store viewer model in
        self.vm_container = ViewerModelContainer(
            title="orthogonal view", rel_order=order, sync_filters=sync_filters
        )

        # Add optional layer hooks
        self.vm_container.set_layer_hooks(self._layer_hooks)

        # Create QtViewer instance with viewer model
        self.qt_viewer = QtViewer(self.vm_container.viewer_model)
        activate_on_hover(self.qt_viewer)  # activate without clicking
        self.qt_viewer.setAcceptDrops(False)  # no drag and drop here

        # Set layout
        layout = QHBoxLayout()
        layout.addWidget(self.qt_viewer)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Add the layers currently in the viewer
        for i, layer in enumerate(self.viewer.layers):
            self.vm_container.add_layer(layer, i)

        # Ensure the layer with the same index is active
        active_layer = self.viewer.layers.selection.active
        if active_layer is not None:
            layer_index = self.viewer.layers.index(active_layer)
            self.vm_container.viewer_model.layers.selection.active = (
                self.vm_container.viewer_model.layers[layer_index]
            )

        # Connect to events
        self._connections = []

        # Layer events
        self._connect(self.viewer.layers.events.inserted, self._layer_added)
        self._connect(self.viewer.layers.events.removed, self._layer_removed)
        self._connect(self.viewer.layers.events.moved, self._layer_moved)
        self._connect(
            self.viewer.layers.selection.events.active,
            self._layer_selection_changed,
        )

        # Viewer events
        self._connect(self.viewer.events.reset_view, self._reset_view)
        self._connect(
            self.viewer.dims.events.current_step, self._update_current_step
        )
        self._connect(
            self.vm_container.viewer_model.dims.events.current_step,
            self._update_current_step,
        )  # reverse dims sync

        # Adjust dimension order for orthogonal views
        self._set_orth_views_dims_order()

    def _connect(self, emitter: EventEmitter, handler: Callable) -> None:
        """Connect an event emitter to a function handler and add it to the list of
        connections."""

        emitter.connect(handler)
        self._connections.append((emitter, handler))

    def _disconnect(self, emitter: EventEmitter, handler: Callable) -> None:
        """Disconnect an event emitter to a function handler and remove it from the list
        of connections."""

        with contextlib.suppress(ValueError):
            emitter.disconnect(handler)
            self._connections.remove((emitter, handler))

    def _set_orth_views_dims_order(self) -> None:
        """The the order of the z,y,x dims in the orthogonal views, by using the
        rel_order attribute of the viewer models"""

        # TODO: allow the user to provide the dimension order and names.
        axis_labels = (
            "t",
            "z",
            "y",
            "x",
        )  # assume default axis labels for now
        order = list(self.viewer.dims.order)

        if len(order) > 2:
            # model axis order (e.g. xz view)
            m_order = list(order)
            m_order[-3:] = (
                m_order[self.vm_container.rel_order[0]],
                m_order[self.vm_container.rel_order[1]],
                m_order[self.vm_container.rel_order[2]],
            )
            self.vm_container.viewer_model.dims.order = m_order

        if len(order) == 3:  # assume we have zyx axes
            self.viewer.dims.axis_labels = axis_labels[1:]
            self.vm_container.viewer_model.dims.axis_labels = axis_labels[1:]
        elif len(order) == 4:  # assume we have tzyx axes
            self.viewer.dims.axis_labels = axis_labels
            self.vm_container.viewer_model.dims.axis_labels = axis_labels

        # whether or not the axis should be visible
        self.vm_container.viewer_model.axes.visible = self.viewer.axes.visible

    def _reset_view(self) -> None:
        """Propagate the reset view event"""

        self.vm_container.viewer_model.reset_view()

    def _layer_selection_changed(self, event: Event) -> None:
        """Update of current active layers"""

        if event.value is None:
            self.vm_container.viewer_model.layers.selection.active = None
            return

        if event.value.name in self.vm_container.viewer_model.layers:
            self.vm_container.viewer_model.layers.selection.active = (
                self.vm_container.viewer_model.layers[event.value.name]
            )

    def _layer_added(self, event: Event) -> None:
        """Add layer to additional other viewer models"""

        if event.value.name not in self.vm_container.viewer_model.layers:
            self.vm_container.add_layer(event.value, event.index)

        self._set_orth_views_dims_order()

    def _layer_removed(self, event: Event) -> None:
        """Remove layer in all viewer models"""

        layer_name = event.value.name
        if layer_name in self.vm_container.viewer_model.layers:
            self.vm_container.viewer_model.layers.pop(layer_name)
        self._set_orth_views_dims_order()

    def _layer_moved(self, event: Event) -> None:
        """Update order of layers in all viewer models"""

        dest_index = (
            event.new_index
            if event.new_index < event.index
            else event.new_index + 1
        )
        self.vm_container.viewer_model.layers.move(event.index, dest_index)

    def _update_current_step(self, event: Event) -> None:
        """Sync the current step between different viewer models"""

        if self._block_center:
            return

        self._block_center = True
        for model in [
            self.viewer,
            self.vm_container.viewer_model,
        ]:
            if model.dims.order is event.source.order:
                continue
            model.dims.current_step = event.value

            # check if the camera center is in the field of view, if not, adjust
            camera_center = list(model.camera.center)
            new_y_center, new_x_center = check_center(
                model, model.dims.current_step
            )
            camera_center[-2] = new_y_center
            camera_center[-1] = new_x_center
            model.camera.center = camera_center

        self._block_center = False

    def sync_event(
        self,
        source_emitter: EventEmitter,
        target_callable: Callable,
        sync: bool,
        key_label: str | None = None,
    ) -> None:
        """
        Connect or disconnect an event from a source emitter to a target callable.

        Args:
            source_emitter (napari EventEmitter)
                The source event emitter (e.g., viewer.camera.events.zoom).
            target_callable (callable)
                Function to call when the source event fires.
                Signature: target_callable(event)
            sync (bool):
                True to connect, False to disconnect.
            key_label (str): optional name to store this connection by.
        """

        if not hasattr(self, "_sync_handlers"):
            # maps key_label -> (emitter, handler)
            self._sync_handlers = {}

        if key_label is None:
            key_label = id(target_callable)

        if sync:
            if key_label in self._sync_handlers:
                return  # do not allow duplicate connections

            def handler(event, _fn=target_callable):
                _fn(event)

            # Store the actual emitter reference
            self._sync_handlers[key_label] = (source_emitter, handler)
            self._connect(source_emitter, handler)
        else:
            if key_label not in self._sync_handlers:
                return

            emitter, handler = self._sync_handlers.pop(key_label)
            self._disconnect(emitter, handler)

    def cleanup(self) -> None:
        """Disconnect from all signals and clear the list"""

        for sig, handler in self._connections:
            with contextlib.suppress(ValueError):
                sig.disconnect(handler)

        self._connections.clear()


def check_center(model: ViewerModel, coords: list[int]) -> tuple[int, int]:
    """Check if the given coordinates are in the current field of view, and if not adjust
    the camera center

    Args:
        coords (list[int]): list of current step coordinates to check.

    Returns:
        tuple [int, int]: (updated) y and x center coordinates to ensure the coordinates
        are visible.
    """

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        view_box = model._get_viewbox_size()
    zoom = model.camera.zoom
    center = model.camera.center
    h = view_box[0] / zoom
    w = view_box[1] / zoom
    min_h = center[-2] - (h / 2)
    max_h = center[-2] + (h / 2)
    min_w = center[-1] - (w / 2)
    max_w = center[-1] + (w / 2)

    order = model.dims.order
    step = [model.dims.range[r].step for r in range(len(order))]
    coords_reordered = [coords[i] * step[i] for i in order]

    y_in_view = coords_reordered[-2] > min_h and coords_reordered[-2] < max_h
    x_in_view = coords_reordered[-1] > min_w and coords_reordered[-1] < max_w

    new_x_center = coords_reordered[-1] if not x_in_view else center[-1]
    new_y_center = coords_reordered[-2] if not y_in_view else center[-2]

    return new_y_center, new_x_center
