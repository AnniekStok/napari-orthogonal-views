from itertools import product

import numpy as np
from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari._vispy.utils.gl import BLENDING_MODES
from napari.components.overlays.base import SceneOverlay
from napari.components.viewer_model import ViewerModel
from vispy.scene import Mesh
from vispy.scene.node import Node

from napari_orthogonal_views.cross_hair_overlay import Crosshairs

# Box definition: there are 8 corners, defined with 0=low, and 1= high per axis.
# There are 6 sides (quads), front, back, top, bottom, left, right.
# Meshes need triangles, so two triangles (faces) per quad.

_CORNERS = list(
    product((0, 1), repeat=3)
)  # (0,1) for (low, high), repeat=3 because we have 3 axes
_QUADS = [
    [i for i, corner in enumerate(_CORNERS) if corner[dim] == side]
    for dim in range(3)
    for side in (0, 1)
]
_FACES = np.array(
    [tri for q in _QUADS for tri in ((q[0], q[1], q[3]), (q[0], q[3], q[2]))]
)


class BoxesOverlay(SceneOverlay):
    """Model side of the boxes overlay: (axis, low, high) in world coordinates, per
    axis cropped to a range."""

    boxes: tuple[tuple[int, float, float], ...] = ()


class VispyBoxesOverlay(ViewerOverlayMixin, VispySceneOverlay):
    """Overlay drawing a translucent box per cropped axis, spanning the cropped range
    along that axis and the extent of all layers along the other axes (3D only).
    """

    def __init__(
        self,
        *,
        viewer: ViewerModel,
        overlay: BoxesOverlay,
        parent: Node | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            node=Mesh(),
            viewer=viewer,
            overlay=overlay,
            parent=parent,
            **kwargs,
        )
        self.viewer = viewer
        self.overlay = overlay

        self.viewer.dims.events.ndisplay.connect(self._update_boxes)
        self.viewer.dims.events.order.connect(self._update_boxes)
        # the layer extent sets the size of the boxes
        self.viewer.layers.events.inserted.connect(self._update_boxes)
        self.viewer.layers.events.removed.connect(self._update_boxes)
        self.overlay.events.boxes.connect(self._update_boxes)

        super().reset()
        self._update_boxes()

    def _on_blending_change(self) -> None:
        """Translucent faces that don't write depth, so they mix with each other and
        with what is drawn inside them."""

        self.node.set_gl_state(
            **BLENDING_MODES[self.overlay.blending], depth_mask=False
        )

    def _displayed_boxes(self) -> list[tuple[int, float, float]]:
        ndim = self.viewer.dims.ndim
        displayed = self.viewer.dims.displayed
        return [
            box for box in self.overlay.boxes if box[0] + ndim in displayed
        ]

    def _should_be_visible(self) -> bool:
        return (
            self.overlay.visible
            and self.viewer.dims.ndisplay == 3
            and len(self.viewer.layers) > 0
            and len(self._displayed_boxes()) > 0
        )

    def _update_boxes(self) -> None:
        self._on_visible_change()
        if not self._should_be_visible():
            return

        dims = self.viewer.dims
        displayed = list(dims.displayed)
        extent = self.viewer.layers.extent.world[:, displayed]
        vertices, faces, face_colors = [], [], []
        for axis, low, high in self._displayed_boxes():
            bounds = extent.astype(float)  # (low, high) per displayed axis
            bounds[:, displayed.index(axis + dims.ndim)] = (low, high)
            offset = len(vertices)
            vertices.extend(bounds[corner, range(3)] for corner in _CORNERS)
            faces.extend(_FACES + offset)
            color = Crosshairs.axis_color(axis).astype(float)
            color[3] = 0.15  # box alpha
            face_colors.extend([color] * len(_FACES))

        # vispy expects (x, y, z), napari displayed order is (z, y, x)
        self.node.set_data(
            vertices=np.array(vertices)[:, ::-1],
            faces=np.array(faces),
            face_colors=np.array(face_colors),
        )
