"""Helpers for compatibility with different napari versions."""

from typing import Any

try:
    from napari.components.scene import (
        Scene,  # noqa: F401 (use as marker to detect napari >= 0.9)
    )

    HAS_SCENE_CANVAS_MODELS = True
except ImportError:  # napari < 0.9
    HAS_SCENE_CANVAS_MODELS = False


def get_camera(viewer: Any) -> Any:
    """Return the camera of a viewer (``viewer.scene.camera`` on napari >= 0.9)."""

    return viewer.scene.camera if HAS_SCENE_CANVAS_MODELS else viewer.camera


def get_grid(viewer: Any) -> Any:
    """Return the grid settings of a viewer (``viewer.canvas.grid`` on napari >= 0.9)."""

    return viewer.canvas.grid if HAS_SCENE_CANVAS_MODELS else viewer.grid


def get_scene_overlays(viewer: Any) -> Any:
    """Return the mapping holding the viewer's scene-space overlays.

    On napari >= 0.9 that is the public ``viewer.scene.overlays``; before that all
    overlays shared the private ``viewer._overlays`` dict.
    """

    return (
        viewer.scene.overlays if HAS_SCENE_CANVAS_MODELS else viewer._overlays
    )


def get_canvas_overlays(viewer: Any) -> Any:
    """Return the mapping holding the viewer's canvas-space overlays.

    On napari >= 0.9 that is the public ``viewer.canvas.overlays``; before that all
    overlays shared the private ``viewer._overlays`` dict.
    """

    return (
        viewer.canvas.overlays if HAS_SCENE_CANVAS_MODELS else viewer._overlays
    )


def set_scene_overlay(viewer: Any, name: str, overlay: Any) -> None:
    """Register ``overlay`` on ``viewer`` under ``name``, in scene space."""

    get_scene_overlays(viewer)[name] = overlay


def get_scene_overlay(viewer: Any, name: str) -> Any:
    """Return the scene-space overlay registered on ``viewer`` under ``name``."""

    return get_scene_overlays(viewer)[name]


def get_viewbox_size(viewer: Any) -> tuple[float, float]:
    """Return the (height, width) in pixels of a single viewbox of this viewer.

    This accounts for grid mode, where the canvas is divided over several viewboxes.
    """

    if HAS_SCENE_CANVAS_MODELS:
        return tuple(viewer.canvas.viewbox_size(viewer.layers))

    # napari 0.6.5 - 0.8 expose the same calculation privately
    return tuple(viewer._get_viewbox_size())
