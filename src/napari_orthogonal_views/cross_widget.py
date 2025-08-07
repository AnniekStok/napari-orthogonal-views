import napari
import numpy as np
from napari.components.layerlist import Extent
from napari.components.viewer_model import ViewerModel
from napari.layers import Vectors
from napari.utils.action_manager import action_manager
from napari.utils.notifications import show_info
from qtpy.QtWidgets import (
    QCheckBox,
)
from superqt.utils import qthrottled


def center_cross_on_mouse(
    viewer_model: napari.components.viewer_model.ViewerModel,
):
    """move the cross to the mouse position"""

    if not getattr(viewer_model, "mouse_over_canvas", True):
        show_info(
            "Mouse is not over the canvas. You may need to click on the canvas."
        )
        return

    viewer_model.dims.current_step = tuple(
        np.round(
            [
                max(min_, min(p, max_)) / step
                for p, (min_, max_, step) in zip(
                    viewer_model.cursor.position,
                    viewer_model.dims.range,
                    strict=False,
                )
            ]
        ).astype(int)
    )


action_manager.register_action(
    name="napari:move_point",
    command=center_cross_on_mouse,
    description="Move dims point to mouse position",
    keymapprovider=ViewerModel,
)

action_manager.bind_shortcut("napari:move_point", "T")


class CrossWidget(QCheckBox):
    """
    Widget to control the cross layer. because of the performance reason
    the cross update is throttled
    """

    def __init__(self, viewer: napari.Viewer):
        super().__init__("Add cross layer")
        self.viewer = viewer
        self.setChecked(False)
        self.stateChanged.connect(self._update_cross_visibility)
        self.layer = None
        self.viewer.dims.events.order.connect(self.update_cross)
        self.viewer.dims.events.ndim.connect(self._update_ndim)
        self.viewer.dims.events.current_step.connect(self.update_cross)
        self._extent = None

        self._update_extent()
        self.viewer.dims.events.connect(self._update_extent)

    @qthrottled(leading=False)
    def _update_extent(self):
        """
        Calculate the extent of the data.

        Ignores the cross layer itself in calculating the extent.
        """

        extent_list = [
            layer.extent
            for layer in self.viewer.layers
            if layer is not self.layer
        ]
        self._extent = Extent(
            data=None,
            world=self.viewer.layers._get_extent_world(extent_list),
            step=self.viewer.layers._get_step_size(extent_list),
        )
        self.update_cross()

    def _update_ndim(self, event):
        if self.layer in self.viewer.layers:
            self.viewer.layers.remove(self.layer)
        self.layer = Vectors(name=".cross", ndim=event.value)
        self.layer.vector_style = "line"
        self.layer.edge_width = 2
        self.update_cross()

    def _update_cross_visibility(self, state):
        if state:
            if self.layer is None:
                self.layer = Vectors(name=".cross", ndim=self.viewer.dims.ndim)
                self.layer.vector_style = "line"
                self.layer.edge_width = 2
            self.viewer.layers.append(self.layer)
        else:
            self.viewer.layers.remove(self.layer)
        self.update_cross()
        if not np.any(self.layer.edge_color):
            self.layer.edge_color = "red"
            self.layer.vector_style = "line"

    def update_cross(self):
        if self.layer not in self.viewer.layers:
            self.setChecked(False)
            return

        point = self.viewer.dims.current_step
        vec = []
        for i, (lower, upper) in enumerate(self._extent.world.T):
            if (upper - lower) / self._extent.step[i] == 1:
                continue
            point1 = list(point)
            point1[i] = (lower + self._extent.step[i] / 2) / self._extent.step[
                i
            ]
            point2 = [0 for _ in point]
            point2[i] = (upper - lower) / self._extent.step[i]
            vec.append((point1, point2))
        if np.any(self.layer.scale != self._extent.step):
            self.layer.scale = self._extent.step
        self.layer.data = vec
