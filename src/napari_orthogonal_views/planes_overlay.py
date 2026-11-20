"""Overlay marking the current dims point with one plane per axis.

A plane normal to an axis marks the position along that axis.
In a 2D view, this is represented as crosshair lines (displayed axes) or as a colored border (sliced, non-displayed axis).
For a 3D view, the entire plane is shown (translucent, with opaque outline), and the place where the different planes intersect correspond to the crosshair intersection in 2D.
"""

import numpy as np
from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari._vispy.utils.gl import BLENDING_MODES
from napari.components.overlays.base import SceneOverlay
from napari.components.viewer_model import ViewerModel
from vispy.scene import Line, Mesh
from vispy.scene.node import Node

# axis color, indexed by the axis in negative numbering
_AXIS_COLORS = {
    -1: (0, 1, 1, 1),  # cyan (X)
    -2: (1, 0, 1, 1),  # magenta (Y)
    -3: (1, 1, 0, 1),  # yellow (Z)
    -4: (1, 0, 0, 1),  # red (T)
    -5: (0, 1, 0, 1),  # green (C)
}


def axis_color(axis: int) -> np.ndarray:
    """Return the RGBA color of an axis, given in negative numbering."""

    return np.array(_AXIS_COLORS.get(axis, (1, 1, 1, 1)), dtype=float)


def current_position(viewer: ViewerModel) -> np.ndarray:
    """Return the world position of the current dims step, for all dims."""

    step_size = [dim_range.step for dim_range in viewer.dims.range]
    return np.array(
        [
            pos * step
            for pos, step in zip(
                viewer.dims.current_step, step_size, strict=False
            )
        ]
    )


class PlanesOverlay(SceneOverlay):
    """Model side representation of the planes overlay, holding the axes (negative
    numbering) whose plane is switched on."""

    visible_planes: tuple[int, ...] = ()


class VispyPlanesOverlay(ViewerOverlayMixin, VispySceneOverlay):
    """Overlay drawing the planes through the current dims point: as planes in 3D, and
    as crosshairs plus a canvas border in 2D."""

    def __init__(
        self,
        *,
        viewer: ViewerModel,
        overlay: PlanesOverlay,
        parent: Node | None = None,
        **kwargs,
    ) -> None:

        # colored planes in 3D, consisting of an outline and a fill
        node = Mesh()  # for the fill
        self.outline = Line(connect="segments", parent=node)

        # crosshairs in 2D, always drawn on top of the layers
        self.crosshairs = Line(connect="segments", parent=node)
        self.crosshairs.set_gl_state(**BLENDING_MODES["translucent_no_depth"])

        super().__init__(
            node=node, viewer=viewer, overlay=overlay, parent=parent, **kwargs
        )
        self.outline.order = self.node.order
        self.crosshairs.order = self.node.order

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
            color=axis_color(dims.order[-3] - dims.ndim),
        )
        self.border.visible = True

    def close(self) -> None:
        """Remove the border from the canvas as well."""

        if self.border.parent is not None:
            self.border.parent.events.resize.disconnect(self._update_border)
            self.border.parent = None
        super().close()

    def _on_blending_change(self) -> None:
        """Apply the overlay blending to the outlines as well as the fill. The
        crosshairs keep their own blending, so that they stay on top in 2D."""

        super()._on_blending_change()
        self.node.set_gl_state(
            **BLENDING_MODES[self.overlay.blending], depth_mask=True
        )
        self.outline.set_gl_state(
            **BLENDING_MODES[self.overlay.blending], depth_mask=True
        )

    def _plane_axes(self) -> list[tuple[int, int]]:
        """Return (displayed index, axis) for each displayed axis whose plane is turned on"""

        ndim = self.viewer.dims.ndim
        return [
            (i, axis - ndim)
            for i, axis in enumerate(self.viewer.dims.displayed)
            if axis - ndim in self.overlay.visible_planes
        ]

    def _should_be_visible(self) -> bool:
        """Show the planes (3D) or crosshairs (2D) of the displayed axes that are
        switched on. The base class uses this to set node.visible. The border is
        handled separately, as it does not live in the scene."""

        if not self.overlay.visible or len(self._plane_axes()) == 0:
            return False
        # the planes span the layer extent, which needs at least one layer
        return self.viewer.dims.ndisplay == 2 or len(self.viewer.layers) > 0

    def _update_planes(self) -> None:
        """Draw the switched on planes through the current point: as planes spanning
        the world extent of all layers (3D), or as crosshairs (2D)."""

        self._on_visible_change()
        if not self._should_be_visible():
            return

        is_3d = self.viewer.dims.ndisplay == 3
        self.outline.visible = is_3d
        self.crosshairs.visible = not is_3d

        if is_3d:
            self._draw_planes()
        else:
            self._draw_crosshairs()

    def _draw_crosshairs(self) -> None:
        """Draw a line through the current point for every displayed axis whose plane
        is switched on, running along the other displayed axis."""

        # no fill in 2D
        self.node.set_data(vertices=None, faces=None)

        displayed = list(self.viewer.dims.displayed)
        point = current_position(self.viewer)[displayed].astype(float)

        pos, colors = [], []
        for normal, axis in self._plane_axes():
            along = 1 - normal  # the other displayed axis
            for sign in (-1, 1):
                end = point.copy()
                end[along] = sign * 1e6
                pos.append(end)
            colors.extend([axis_color(axis)] * 2)

        # vispy expects (x, y), napari displayed order is (y, x)
        self.crosshairs.set_data(
            pos=np.array(pos)[:, ::-1], color=np.array(colors), width=2
        )

    def _draw_planes(self) -> None:
        """Draw a translucent plane with an opaque outline for every displayed axis
        whose plane is switched on, spanning the world extent of all layers."""

        displayed = list(self.viewer.dims.displayed)
        extent = self.viewer.layers.extent.world[:, displayed]
        point = current_position(self.viewer)[displayed]

        vertices, faces, face_colors = [], [], []
        pos, colors = [], []
        for normal, axis in self._plane_axes():
            u, v = (i for i in range(3) if i != normal)
            corners = []
            for cu, cv in [(0, 0), (1, 0), (1, 1), (0, 1)]:
                corner = point.astype(float)
                corner[u] = extent[cu, u]
                corner[v] = extent[cv, v]
                corners.append(corner)
            color = axis_color(axis)
            fill_color = color.copy()
            fill_color[3] = 0.15  # plane alpha

            # fill: two triangles per plane, with low alpha
            offset = len(vertices)
            vertices.extend(corners)
            faces.extend(
                [
                    [offset, offset + 1, offset + 2],
                    [offset, offset + 2, offset + 3],
                ]
            )
            face_colors.extend([fill_color] * 2)

            # outline at the current point, opaque
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
