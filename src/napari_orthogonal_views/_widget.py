from collections.abc import Callable
from types import MethodType

import napari
from napari.components.viewer_model import ViewerModel
from napari.layers import Labels, Layer
from napari.qt import QtViewer
from napari.utils.action_manager import action_manager
from napari.utils.events import Event
from napari.utils.events.event import WarningEmitter
from qtpy.QtWidgets import (
    QHBoxLayout,
    QSplitter,
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


action_manager.bind_shortcut("napari:move_point", "C")


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
    A dockable container that holds a ViewerModel and manages synchronization.
    """

    def __init__(
        self, title: str, rel_order: tuple[int], transpose: bool = False
    ):
        self.title = title
        self.rel_order = rel_order
        self.viewer_model = ViewerModel(title)
        self.viewer_model.axes.visible = True
        if transpose:
            self.viewer_model.dims.transpose()
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
        self.viewer_model1 = ViewerModelContainer(
            title="model1", rel_order=order
        )

        self.sync_axes = sync_axes
        self._block_zoom = False
        self._block_center = False  # Separate flag from zoom

        self.qt_viewer1 = QtViewer(self.viewer_model1.viewer_model)
        viewer_splitter = QSplitter()

        layout = QHBoxLayout()
        layout.addWidget(self.qt_viewer1)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Add the layers currently in the viewer
        for i, layer in enumerate(self.viewer.layers):
            self.viewer_model1.add_layer(layer, i)

        # Connect to events
        self.viewer.layers.events.inserted.connect(self._layer_added)
        self.viewer.layers.events.removed.connect(self._layer_removed)
        self.viewer.layers.events.moved.connect(self._layer_moved)
        self.viewer.layers.selection.events.active.connect(
            self._layer_selection_changed
        )
        self.viewer.events.reset_view.connect(self._reset_view)
        self.viewer.dims.events.current_step.connect(self._update_current_step)
        self.viewer_model1.viewer_model.dims.events.current_step.connect(
            self._update_current_step
        )

        self.viewer.camera.events.zoom.connect(
            lambda e: self.sync_camera(
                "zoom",
                self.viewer.camera,
                self.viewer_model1.viewer_model.camera,
            )
        )
        self.viewer_model1.viewer_model.camera.events.zoom.connect(
            lambda e: self.sync_camera(
                "zoom",
                self.viewer_model1.viewer_model.camera,
                self.viewer.camera,
            )
        )

        # Sync camera center
        self.viewer.camera.events.center.connect(self._on_main_center)
        self.viewer_model1.viewer_model.camera.events.center.connect(
            self._on_ortho_center
        )

        # Adjust dimensions for orthogonal views
        self.set_orth_views_dims_order()

    def sync_camera(
        self, property_name: str, source: ViewerModel, target: ViewerModel
    ):
        """Sync a camera property from source to target"""
        if self._block_zoom:
            return

        self._block_zoom = True
        try:
            setattr(target, property_name, getattr(source, property_name))
        finally:
            self._block_zoom = False

    def _on_main_center(self, event=None):
        if self._block_center:
            return

        self._block_center = True
        try:
            main_center = list(self.viewer.camera.center)
            ortho_center = list(self.viewer_model1.viewer_model.camera.center)

            for ax in self.sync_axes:
                ortho_center[ax] = main_center[ax]

            self.viewer_model1.viewer_model.camera.center = tuple(ortho_center)
        finally:
            self._block_center = False

    def _on_ortho_center(self, event=None):
        if self._block_center:
            return

        self._block_center = True
        try:
            ortho_center = list(self.viewer_model1.viewer_model.camera.center)
            main_center = list(self.viewer.camera.center)

            for ax in self.sync_axes:
                main_center[ax] = ortho_center[ax]

            self.viewer.camera.center = tuple(main_center)
        finally:
            self._block_center = False

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
                m1_order[self.viewer_model1.rel_order[0]],
                m1_order[self.viewer_model1.rel_order[1]],
                m1_order[self.viewer_model1.rel_order[2]],
            )
            self.viewer_model1.viewer_model.dims.order = m1_order

        if len(order) == 3:  # assume we have zyx axes
            self.viewer.dims.axis_labels = axis_labels[1:]
            self.viewer_model1.viewer_model.dims.axis_labels = axis_labels[1:]
        elif len(order) == 4:  # assume we have tzyx axes
            self.viewer.dims.axis_labels = axis_labels
            self.viewer_model1.viewer_model.dims.axis_labels = axis_labels

        # whether or not the axis should be visible
        self.viewer_model1.viewer_model.axes.visible = self.viewer.axes.visible

    def _reset_view(self):
        """Propagate the reset view event"""

        self.viewer_model1.viewer_model.reset_view()

    def _layer_selection_changed(self, event):
        """Update of current active layers"""

        if event.value is None:
            self.viewer_model1.viewer_model.layers.selection.active = None
            return

        if event.value.name in self.viewer_model1.viewer_model.layers:
            self.viewer_model1.viewer_model.layers.selection.active = (
                self.viewer_model1.viewer_model.layers[event.value.name]
            )

    def _update_current_step(self, event):
        """Sync the current step between different viewer models"""

        for model in [
            self.viewer,
            self.viewer_model1.viewer_model,
        ]:
            if model.dims is event.source:
                continue
            model.dims.current_step = event.value

    def _layer_added(self, event):
        """Add layer to additional other viewer models"""

        if event.value.name not in self.viewer_model1.viewer_model.layers:
            self.viewer_model1.add_layer(event.value, event.index)

        self.set_orth_views_dims_order()

    def _layer_removed(self, event):
        """Remove layer in all viewer models"""

        layer_name = event.value.name
        if layer_name in self.viewer_model1.viewer_model.layers:
            self.viewer_model1.viewer_model.layers.pop(layer_name)
        self.set_orth_views_dims_order()

    def _layer_moved(self, event):
        """Update order of layers in all viewer models"""

        dest_index = (
            event.new_index
            if event.new_index < event.index
            else event.new_index + 1
        )
        self.viewer_model1.viewer_model.layers.move(event.index, dest_index)
