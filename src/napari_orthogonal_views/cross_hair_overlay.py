import napari
import numpy as np
from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari.components.overlays.base import SceneOverlay
from napari.components.viewer_model import ViewerModel
from napari.utils.action_manager import action_manager
from napari.utils.notifications import show_info
from vispy.scene import Line


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


def init_actions():
    action_manager.register_action(
        name="napari:move_point",
        command=center_cross_on_mouse,
        description="Move dims point to mouse position",
        keymapprovider=ViewerModel,
    )
    action_manager.bind_shortcut("napari:move_point", "T")


class Cursor(Line):
    _base_segments = (
        np.array(
            [
                [0, 0, 1],
                [0, 0, -1],
                [0, 1, 0],
                [0, -1, 0],
                [1, 0, 0],
                [-1, 0, 0],
            ]
        )
        * 1e6
    )

    def __init__(self):
        super().__init__(self._base_segments, connect="segments", color="red")
        self.set_position(np.zeros(3))

    def set_position(self, value: np.ndarray) -> None:
        self.set_data(pos=self._base_segments + value)


class VispyCursorOverlay(ViewerOverlayMixin, VispySceneOverlay):
    """Overlay indicating the position of the crosshair in the world."""

    def __init__(self, *, viewer, overlay, parent=None) -> None:
        super().__init__(
            node=Cursor(), viewer=viewer, overlay=overlay, parent=parent
        )
        self.viewer = viewer
        self.viewer.dims.events.current_step.connect(self._move_crosshairs)

    def _move_crosshairs(self) -> None:
        """Move the crosshairs to the current viewer step"""

        position = self.viewer.dims.current_step

        displayed = list(self.viewer.dims.displayed[::-1])
        not_displayed = list(self.viewer.dims.not_displayed[::-1])

        if len(not_displayed) == 0:
            not_displayed = [0]
        if len(displayed) == 2:
            displayed = np.concatenate([displayed, [not_displayed[0]]])

        self.node.set_position(np.array(position)[displayed])


class CursorOverlay(SceneOverlay):
    """
    Overlay that displays where the cursor is located in the world.
    """

    init_actions()
