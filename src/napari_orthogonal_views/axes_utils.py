"""Helpers to display the axes indicating the orientation of a viewer.

napari >= 0.8 has a floating axes overlay that is pinned to a corner of the canvas, so it
stays in view when zooming in or panning. Use this overlay if available, otherwise use the
old axes overlay that is drawn at world origin.
"""

from typing import Any

from napari.components.viewer_model import ViewerModel

# napari >= 0.8 can pin axes overlay a corner of the canvas
HAS_FLOATING_AXES = hasattr(ViewerModel, "floating_axes")

# corner of the canvas to pin the floating axes to
AXES_POSITION = "top_left"


def get_axes(viewer: ViewerModel) -> Any:
    """Return the axes overlay used to indicate the orientation of the viewer."""

    return viewer.floating_axes if HAS_FLOATING_AXES else viewer.axes


def axes_visible(viewer: ViewerModel) -> bool:
    """Return whether the axes of the viewer are visible."""

    return get_axes(viewer).visible


def set_axes_visible(viewer: ViewerModel, visible: bool) -> None:
    """Show or hide the axes of the viewer, in the top left corner if possible."""

    axes = get_axes(viewer)
    if HAS_FLOATING_AXES:
        axes.position = AXES_POSITION
    axes.visible = visible
