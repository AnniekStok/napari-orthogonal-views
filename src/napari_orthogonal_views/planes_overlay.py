import numpy as np
from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari._vispy.utils.gl import BLENDING_MODES
from napari.components.overlays.base import SceneOverlay
from napari.components.viewer_model import ViewerModel
from vispy.scene import Line, Mesh
from vispy.scene.node import Node

from napari_orthogonal_views.cross_hair_overlay import (
    Crosshairs,
    current_position,
)


class PlanesOverlay(SceneOverlay):
    """Model side representation of the planes overlay."""

    visible_planes: tuple[int, ...] = ()


class VispyPlanesOverlay(ViewerOverlayMixin, VispySceneOverlay):
    """Overlay drawing the planes through the current dims point in 3D, and setting a 2D
    border around the scene in 2D."""

    def __init__(
        self,
        *,
        viewer: ViewerModel,
        overlay: PlanesOverlay,
        parent: Node | None = None,
        **kwargs,
    ) -> None:

        # colored plane in 3D
        node = Mesh()  # for the fill
        self.outline = Line(
            connect="segments", parent=node
        )  # for the outlines
        super().__init__(
            node=node, viewer=viewer, overlay=overlay, parent=parent, **kwargs
        )
        self.outline.order = self.node.order

        self.viewer = viewer
        self.overlay = overlay
        self.overlay.blending = "translucent"  # set again to ensure that the planes can be obscured by the object in front of it

        # colored frame around the canvas in 2D
        self.border = Mesh()
        self.border.order = self.node.order
        self.border.set_gl_state("translucent", depth_test=False)

        # connect to dims events to move plane location
        self.viewer.dims.events.current_step.connect(self._update_planes)
        self.viewer.dims.events.ndisplay.connect(self._update_planes)
        self.viewer.dims.events.order.connect(self._update_planes)
        # connect to layer events because the layer extent sets the size of the planes
        self.viewer.layers.events.inserted.connect(self._update_planes)
        self.viewer.layers.events.removed.connect(self._update_planes)
        self.overlay.events.visible_planes.connect(self._update_planes)

        super().reset()
        self._update_planes()

    def _on_visible_change(self) -> None:
        """Show or hide the canvas border together with the planes."""

        super()._on_visible_change()
        self._update_border()

    def _update_border(self, event=None) -> None:
        """Color the canvas border by the axis this view looks along, in 2D only, if
        the plane of that axis is switched on."""

        dims = self.viewer.dims
        if not (
            self.overlay.visible
            and dims.ndisplay == 2  # 2D only
            and dims.ndim > 2  # at least 3 dims are needed for ortho views
            and dims.order[-3] - dims.ndim
            in self.overlay.visible_planes  # user asked for this plane
        ):
            self.border.visible = False
            return

        canvas = self.node.canvas
        if canvas is None:
            return

        widget = canvas.central_widget
        if self.border.parent is not widget:
            self.border.parent = widget
            widget.events.resize.connect(
                self._update_border
            )  # widget can stretch/shrink, so border should move with it

        # frame between the outer and inner rectangle, as 4 quads of 2 triangles
        w, h = widget.size
        b = 1  # border width
        outer = [[0, 0], [w, 0], [w, h], [0, h]]
        inner = [[b, b], [w - b, b], [w - b, h - b], [b, h - b]]
        faces = []
        for i in range(4):
            j = (i + 1) % 4
            faces.extend([[i, j, 4 + j], [i, 4 + j, 4 + i]])

        self.border.set_data(
            vertices=np.array(outer + inner, dtype=float),
            faces=np.array(faces),
            color=Crosshairs.axis_color(dims.order[-3] - dims.ndim),
        )
        self.border.visible = True

    def close(self) -> None:
        """Remove the border from the canvas as well."""

        if self.border.parent is not None:
            self.border.parent.events.resize.disconnect(self._update_border)
            self.border.parent = None
        super().close()

    def _on_blending_change(self) -> None:
        """Apply the overlay blending to the outlines as well as the fill."""

        super()._on_blending_change()
        self.outline.set_gl_state(**BLENDING_MODES[self.overlay.blending])

    def _plane_axes(self) -> list[tuple[int, int]]:
        """Return (displayed index, axis) for each displayed axis with its plane on."""

        ndim = self.viewer.dims.ndim
        return [
            (i, axis - ndim)
            for i, axis in enumerate(self.viewer.dims.displayed)
            if axis - ndim in self.overlay.visible_planes
        ]

    def _should_be_visible(self) -> bool:
        """Show the plane mesh only in 3D (the base class uses this to set node.visible, so redefine it here based on the custom criteria)."""
        return (
            self.overlay.visible
            and self.viewer.dims.ndisplay == 3
            and len(self.viewer.layers) > 0
            and len(self._plane_axes()) > 0
        )

    def _update_planes(self) -> None:
        """Draw the switched on planes through the current point, spanning the world
        extent of all layers (3D), or the canvas border (2D)."""

        self._on_visible_change()
        if not self._should_be_visible():
            return

        displayed = list(self.viewer.dims.displayed)
        extent = self.viewer.layers.extent.world[:, displayed]
        point = current_position(self.viewer)[displayed]

        vertices, faces, face_colors = [], [], []
        pos, colors = [], []
        for normal, axis in self._plane_axes():
            u, v = (i for i in range(3) if i != normal)
            offset = len(vertices)
            corners = []
            for cu, cv in [(0, 0), (1, 0), (1, 1), (0, 1)]:
                corner = point.astype(float)
                corner[u] = extent[cu, u]
                corner[v] = extent[cv, v]
                corners.append(corner)
            color = Crosshairs.axis_color(axis).astype(float)

            # fill: two triangles per plane, with low alpha
            vertices.extend(corners)
            faces.extend(
                [
                    [offset, offset + 1, offset + 2],
                    [offset, offset + 2, offset + 3],
                ]
            )
            fill_color = color.copy()
            fill_color[3] = 0.15  # plane alpha
            face_colors.extend([fill_color] * 2)

            # outline: edges 0-1, 1-2, 2-3, 3-0, opaque
            for k in range(4):
                pos.extend([corners[k], corners[(k + 1) % 4]])
            colors.extend([color] * 8)  # one color per point

        # vispy expects (x, y, z), napari displayed order is (z, y, x)
        self.node.set_data(
            vertices=np.array(vertices)[:, ::-1],
            faces=np.array(faces),
            face_colors=np.array(face_colors),
        )
        self.outline.set_data(
            pos=np.array(pos)[:, ::-1], color=np.array(colors), width=2
        )
