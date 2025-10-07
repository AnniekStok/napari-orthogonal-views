import numpy as np
from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari.components.overlays.base import SceneOverlay
from vispy.scene import Line


class Crosshairs(Line):
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

    _base_colors = np.array(
        [
            [1, 1, 0, 1],  # yellow (Z)
            [1, 1, 0, 1],
            [1, 0, 1, 1],  # magenta (Y)
            [1, 0, 1, 1],
            [0, 1, 1, 1],  # cyan (X)
            [0, 1, 1, 1],
        ]
    )

    def __init__(self, axis_order=(0, 1, 2)):
        axis_order = list(axis_order)
        axis_order[1], axis_order[2] = (
            axis_order[2],
            axis_order[1],
        )  # swap because cross-
        # hairs should move along their axis, not point along it.
        self.axis_order = tuple(axis_order)
        self._colors = self._reorder_colors()

        super().__init__(
            self._base_segments,
            connect="segments",
            color=self._colors,
        )
        self.set_position(np.zeros(3))

    def _reorder_colors(self):
        """Return colors permuted to match viewer axis order."""
        pairs = [(0, 1), (2, 3), (4, 5)]
        new_order = [
            i for pair in [pairs[i] for i in self.axis_order] for i in pair
        ]
        return self._base_colors[new_order]

    def set_position(self, value: np.ndarray) -> None:
        self.set_data(
            pos=self._base_segments + value, color=self._colors, width=3
        )


class VispyCrosshairOverlay(ViewerOverlayMixin, VispySceneOverlay):
    """Overlay indicating the position of the crosshair in the world."""

    def __init__(self, *, viewer, overlay, parent=None) -> None:
        axis_order = getattr(overlay, "axis_order", (0, 1, 2))
        node = Crosshairs(axis_order=axis_order)
        super().__init__(
            node=node, viewer=viewer, overlay=overlay, parent=parent
        )
        self.viewer = viewer
        self.viewer.dims.events.current_step.connect(self._move_crosshairs)
        super().reset()  # reset to make sure the overlay is not visible too early

    def _move_crosshairs(self) -> None:
        """Move the crosshairs to the current viewer step."""
        step_size = [dim_range.step for dim_range in self.viewer.dims.range]
        position = [
            pos * step
            for pos, step in zip(
                self.viewer.dims.current_step, step_size, strict=False
            )
        ]

        displayed = list(self.viewer.dims.displayed[::-1])
        not_displayed = list(self.viewer.dims.not_displayed[::-1])

        if len(not_displayed) == 0:
            not_displayed = [0]
        if len(displayed) == 2:
            displayed = np.concatenate([displayed, [not_displayed[0]]])

        self.node.set_position(np.array(position)[displayed])


class CrosshairOverlay(SceneOverlay):
    """Overlay that displays where the cursor is located in the world."""

    axis_order: tuple[int, int, int] = (0, 1, 2)

    def __init__(self, *, axis_order=(0, 1, 2), **kwargs):
        super().__init__(**kwargs)
        object.__setattr__(self, "axis_order", tuple(axis_order))
