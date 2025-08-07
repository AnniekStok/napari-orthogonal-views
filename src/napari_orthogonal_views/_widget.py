import time

import napari
from napari.components.viewer_model import ViewerModel
from napari.layers import Labels, Layer, Points, Shapes, Tracks
from napari.qt import QtViewer
from napari.utils.events import Event
from napari.utils.events.event import WarningEmitter
from qtpy.QtWidgets import (
    QVBoxLayout,
    QWidget,
)


def copy_layer(layer: Layer, name: str = ""):
    if isinstance(
        layer, Tracks
    ):  # instead of showing the tracks (not very useful on 3D data because they are collapsed to a single frame),
        # use an empty shapes layer as substitute to ensure that the layer indices in the orthogonal viewer models
        # match with those in the main viewer
        res_layer = Shapes(
            name=layer.name,
            data=[],
        )

    elif isinstance(layer, Labels):
        res_layer = Labels(
            data=layer.data,
            name=layer.name,
            opacity=layer.opacity,
            scale=layer.scale,
        )
    elif isinstance(layer, Points):
        res_layer = Points(
            data=layer.data,
            name=layer.name,
            symbol=layer.symbol,
            # face_color=layer.face_color,
            size=layer.size,
            properties=layer.properties,
            # border_color=layer.border_color,
            scale=layer.scale,
            blending="translucent_no_depth",
        )
    else:
        res_layer = Layer.create(*layer.as_layer_data_tuple())

    res_layer.metadata["viewer_name"] = name
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
    A container that holds a ViewerModel and manages synchronization across different views.
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
        if not isinstance(orig_layer, Tracks):  # ignore Tracks layers
            for property_name in get_property_names(orig_layer):
                # sync forward (from original layer to copied layer)
                if not (
                    isinstance(orig_layer, Points) and property_name == "data"
                ):  # we will sync data separately on Points as we need finer control
                    getattr(orig_layer.events, property_name).connect(
                        own_partial(
                            self.sync_property,
                            property_name,
                            orig_layer,
                            copied_layer,
                        )
                    )

                # in the case of a Labels or Points layer, sync only specific properties backwards. Otherwise, sync all properties
                if not isinstance(
                    orig_layer, (Labels | Points)
                ) or property_name in (
                    "mode",
                    "selected_label",
                    "n_edit_dimensions",
                    "brush_size",
                ):
                    getattr(copied_layer.events, property_name).connect(
                        own_partial(
                            self.sync_property,
                            property_name,
                            copied_layer,
                            orig_layer,
                        )
                    )

        # forward click events and key binds in the case of Labels and Points layers
        # if isinstance(orig_layer, (Labels | Points)):

        # def click_wrapper(layer, event):
        #     # Access orig_layer here
        #     return self.click(orig_layer, layer, event)

        # copied_layer.mouse_drag_callbacks.append(click_wrapper)

        # copied_layer.bind_key("q")(orig_layer.tracks_viewer.toggle_display_mode)
        # copied_layer.bind_key("z")(orig_layer.tracks_viewer.undo)
        # copied_layer.bind_key("r")(orig_layer.tracks_viewer.redo)

        if isinstance(orig_layer, Labels):
            # if the original layer is a normal labels layer, we still want to connect to the paint event,
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

        # if the original layer is a Points layer, make sure the visible points are synced (when switching between 'all' and 'lineage' mode)
        # and make sure that moving a point forwards the event to the original layer for processing (or resetting, if a seg_layer is present)
        elif isinstance(orig_layer, Points):

            def shown_points_wrapper(event):
                return self.sync_shown_points(orig_layer, copied_layer)

            orig_layer.events.border_color.connect(shown_points_wrapper)

            # def receive_data_wrapper():
            #     return self.receive_data(orig_layer, copied_layer)

            # orig_layer.data_updated.connect(receive_data_wrapper)

            def sync_data_wrapper(event):
                return self.sync_data_event(orig_layer, copied_layer, event)

            copied_layer._sync_data_wrapper = sync_data_wrapper
            copied_layer.events.data.connect(sync_data_wrapper)

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
        target.refresh()

    def receive_data(self, orig_layer: Points, copied_layer: Points) -> None:
        """Respond to signal from the original layer, to update the data"""

        copied_layer.events.data.disconnect(copied_layer._sync_data_wrapper)
        copied_layer.data = orig_layer.data
        copied_layer.events.data.connect(copied_layer._sync_data_wrapper)

    def sync_data_event(
        self, orig_layer: Points, copied_layer: Points, event: Event
    ) -> None:
        """Send the event that is emitted when a point is moved or deleted to the original layer"""

        if hasattr(event, "action") and event.action in (
            "added",
            "changed",
            "removed",
        ):
            with (
                orig_layer.events.blocker_all()
            ):  # try to suppress updating visibility
                orig_layer.selected_data = (
                    copied_layer.selected_data
                )  # make sure the same data is selected
            orig_layer._update_data(event)

    def sync_shown_points(
        self, orig_layer: Points, copied_layer: Points
    ) -> None:
        """Sync the visible points between original Points layer and Points layers in ViewerModel instances (this is not a synced property)"""

        with copied_layer.events.blocker_all():
            copied_layer.size = orig_layer.size
            copied_layer.shown = orig_layer.shown

        copied_layer.refresh()

    def sync_name(self, orig_layer: Layer, copied_layer: Layer, event: Event):
        """Forward the renaming event from original layer to copied layer"""

        copied_layer.name = orig_layer.name

    def sync_paint(self, orig_layer: Labels, event: Event):
        """Sync paint event to original Labels instance"""

        orig_layer._on_paint(event)

    def click(
        self,
        orig_layer: Labels | Points,
        layer: Labels | Points,
        event: Event,
    ):
        """Forward the click event from the ViewerModel to the original TracksLabels layer
        args:
            orig_layer: original Labels or Points layer
            layer: the Labels or Points layer on this ViewerModel
            event: the click event
        """
        if layer.mode == "pan_zoom":
            mouse_press_time = time.time()
            dragged = False
            yield
            # on move
            while event.type == "mouse_move":
                dragged = True
                yield
            if dragged and time.time() - mouse_press_time < 0.5:
                dragged = (
                    False  # suppress micro drag events and treat them as click
                )
            # on release
            if not dragged:
                if isinstance(layer, Labels):
                    label = layer.get_value(
                        event.position,
                        view_direction=event.view_direction,
                        dims_displayed=event.dims_displayed,
                        world=True,
                    )
                    orig_layer.process_click(event, label)

                if isinstance(layer, Points):
                    point_index = layer.get_value(
                        event.position,
                        view_direction=event.view_direction,
                        dims_displayed=event.dims_displayed,
                        world=True,
                    )
                    orig_layer.process_point_click(point_index, event)

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


class MultipleViewerWidget(QWidget):
    """The main widget of the example."""

    def __init__(self, viewer: napari.Viewer):
        super().__init__()
        self.viewer = viewer
        self.viewer.axes.visible = True
        self.viewer.axes.events.visible.connect(self.set_orth_views_dims_order)
        self.viewer_model1 = ViewerModelContainer(
            title="model1", rel_order=(-2, -3, -1)
        )
        self.viewer_model2 = ViewerModelContainer(
            title="model2", rel_order=(-1, -2, -3)
        )
        self.qt_viewer1 = QtViewer(self.viewer_model1.viewer_model)
        self.qt_viewer2 = QtViewer(self.viewer_model2.viewer_model)
        viewer_splitter = QVBoxLayout()
        viewer_splitter.addWidget(self.qt_viewer1)
        viewer_splitter.addWidget(self.qt_viewer2)
        viewer_splitter.setContentsMargins(0, 0, 0, 0)

        self.setLayout(viewer_splitter)

        # Add the layers currently in the viewer
        for i, layer in enumerate(self.viewer.layers):
            self.viewer_model1.add_layer(layer, i)
            self.viewer_model2.add_layer(layer, i)

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
        self.viewer_model2.viewer_model.dims.events.current_step.connect(
            self._update_current_step
        )

        # Adjust dimensions for orthogonal views
        self.set_orth_views_dims_order()

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

            # model 2 axis order (e.g. yz view)
            m2_order = list(order)
            m2_order[-3:] = (
                m2_order[self.viewer_model2.rel_order[0]],
                m2_order[self.viewer_model2.rel_order[1]],
                m2_order[self.viewer_model2.rel_order[2]],
            )

            self.viewer_model2.viewer_model.dims.order = m2_order

        if len(order) == 3:  # assume we have zyx axes
            self.viewer.dims.axis_labels = axis_labels[1:]
            self.viewer_model1.viewer_model.dims.axis_labels = axis_labels[1:]
            self.viewer_model2.viewer_model.dims.axis_labels = axis_labels[1:]
        elif len(order) == 4:  # assume we have tzyx axes
            self.viewer.dims.axis_labels = axis_labels
            self.viewer_model1.viewer_model.dims.axis_labels = axis_labels
            self.viewer_model2.viewer_model.dims.axis_labels = axis_labels

        # whether or not the axis should be visible
        self.viewer_model1.viewer_model.axes.visible = self.viewer.axes.visible
        self.viewer_model2.viewer_model.axes.visible = self.viewer.axes.visible

    def _reset_view(self):
        """Propagate the reset view event"""

        self.viewer_model1.viewer_model.reset_view()
        self.viewer_model2.viewer_model.reset_view()

    def _layer_selection_changed(self, event):
        """Update of current active layers"""

        if event.value is None:
            self.viewer_model1.viewer_model.layers.selection.active = None
            self.viewer_model2.viewer_model.layers.selection.active = None
            return

        if event.value.name in self.viewer_model1.viewer_model.layers:
            self.viewer_model1.viewer_model.layers.selection.active = (
                self.viewer_model1.viewer_model.layers[event.value.name]
            )
        if event.value.name in self.viewer_model2.viewer_model.layers:
            self.viewer_model2.viewer_model.layers.selection.active = (
                self.viewer_model2.viewer_model.layers[event.value.name]
            )

    def _update_current_step(self, event):
        """Sync the current step between different viewer models"""

        for model in [
            self.viewer,
            self.viewer_model1.viewer_model,
            self.viewer_model2.viewer_model,
        ]:
            if model.dims is event.source:
                continue
            model.dims.current_step = event.value

    def _layer_added(self, event):
        """Add layer to additional other viewer models"""

        if event.value.name not in self.viewer_model1.viewer_model.layers:
            self.viewer_model1.add_layer(event.value, event.index)

        if event.value.name not in self.viewer_model2.viewer_model.layers:
            self.viewer_model2.add_layer(event.value, event.index)

        self.set_orth_views_dims_order()

    def _layer_removed(self, event):
        """Remove layer in all viewer models"""

        layer_name = event.value.name
        if layer_name in self.viewer_model1.viewer_model.layers:
            self.viewer_model1.viewer_model.layers.pop(layer_name)
        if layer_name in self.viewer_model2.viewer_model.layers:
            self.viewer_model2.viewer_model.layers.pop(layer_name)

        self.set_orth_views_dims_order()

    def _layer_moved(self, event):
        """Update order of layers in all viewer models"""

        dest_index = (
            event.new_index
            if event.new_index < event.index
            else event.new_index + 1
        )
        self.viewer_model1.viewer_model.layers.move(event.index, dest_index)
        self.viewer_model2.viewer_model.layers.move(event.index, dest_index)
