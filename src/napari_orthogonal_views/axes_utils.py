"""Helpers to display the axes indicating the orientation of a viewer.

napari >= 0.8 has a floating axes overlay that is pinned to a corner of the canvas, so it
stays in view when zooming in or panning. Use this overlay if available, otherwise use the
old axes overlay that is drawn at world origin.

napari >= 0.9 keeps the same two overlays but reaches them through the split viewer
model: the pinned one is ``viewer.canvas.overlays.axes`` and the world-origin one is
``viewer.scene.overlays.axes``.
"""

from typing import Any

from napari.components.viewer_model import ViewerModel

from napari_orthogonal_views.attribute_helpers import (
    HAS_SCENE_CANVAS_MODELS,
    get_canvas_overlays,
)

# napari 0.8 can pin the axes overlay to a corner of the canvas via its own property
HAS_FLOATING_AXES = hasattr(ViewerModel, "floating_axes")

# whether the axes overlay in use is the one pinned to the canvas
PINNED_AXES = HAS_SCENE_CANVAS_MODELS or HAS_FLOATING_AXES

# corner of the canvas to pin the floating axes to
AXES_POSITION = "top_left"


def get_axes(viewer: ViewerModel) -> Any:
    """Return the axes overlay used to indicate the orientation of the viewer."""

    if HAS_SCENE_CANVAS_MODELS:
        return get_canvas_overlays(viewer)["axes"]
    return viewer.floating_axes if HAS_FLOATING_AXES else viewer.axes


def axes_visible(viewer: ViewerModel) -> bool:
    """Return whether the axes of the viewer are visible."""

    return get_axes(viewer).visible


def set_axes_visible(viewer: ViewerModel, visible: bool) -> None:
    """Show or hide the axes of the viewer, in the top left corner if possible."""

    axes = get_axes(viewer)
    if PINNED_AXES:
        axes.position = AXES_POSITION
    axes.visible = visible
