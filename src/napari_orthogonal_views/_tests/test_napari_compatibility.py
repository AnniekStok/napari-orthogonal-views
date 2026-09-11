"""Tests for the parts of the viewer model that moved between napari versions.

These pin down the accessors in ``attribute_helpers`` (and the axes helpers built on them)
against whichever napari is installed, so a future napari release that moves one of them
again fails here rather than somewhere deep in the syncing code.
"""

import warnings

import numpy as np
import pytest
from napari.components.viewer_model import ViewerModel
from napari.layers import Image, Points
from napari.utils.events.event import WarningEmitter

from napari_orthogonal_views.attribute_helpers import (
    get_camera,
    get_canvas_overlays,
    get_grid,
    get_scene_overlay,
    get_scene_overlays,
    get_viewbox_size,
    set_scene_overlay,
)
from napari_orthogonal_views.axes_utils import (
    AXES_POSITION,
    PINNED_AXES,
    axes_visible,
    get_axes,
    set_axes_visible,
)
from napari_orthogonal_views.ortho_view_widget import (
    check_center,
    get_property_names,
)


@pytest.fixture
def model():
    viewer_model = ViewerModel("compat")
    viewer_model.add_image(np.zeros((10, 20, 30)))
    return viewer_model


def test_camera_is_reachable(model):
    """The camera moved to viewer.scene in napari 0.9."""

    camera = get_camera(model)
    assert len(camera.center) == 3
    camera.zoom = 2.0
    assert get_camera(model).zoom == 2.0


def test_grid_is_reachable(model):
    """The grid moved to viewer.canvas in napari 0.9."""

    grid = get_grid(model)
    assert grid.enabled is False
    grid.enabled = True
    assert get_grid(model).enabled is True


def test_overlay_containers_hold_the_built_in_overlays(model):
    """Both overlay containers must be mappings napari itself populates."""

    assert "axes" in get_scene_overlays(model)
    assert "axes" in get_canvas_overlays(model)


def test_a_custom_scene_overlay_round_trips(model):
    """The crosshairs are registered this way, so it has to survive the 0.9 split of
    _overlays into scene and canvas overlays."""

    from napari_orthogonal_views.cross_hair_overlay import CrosshairOverlay

    overlay = CrosshairOverlay(
        blending="translucent_no_depth", axis_order=(-3, -2, -1)
    )
    set_scene_overlay(model, "crosshairs", overlay)

    assert get_scene_overlay(model, "crosshairs") is overlay
    assert get_scene_overlay(model, "crosshairs").visible is False
    get_scene_overlay(model, "crosshairs").visible = True
    assert overlay.visible is True


def test_viewbox_size_is_the_canvas_when_not_gridded(model):
    """_get_viewbox_size() was replaced by canvas.viewbox_size(layers) in 0.9."""

    assert tuple(get_viewbox_size(model)) == tuple(_canvas_size(model))


def test_viewbox_size_shrinks_in_grid_mode(model):
    """Grid mode divides the canvas over several viewboxes, and check_center relies on
    getting the size of one of them rather than the whole canvas."""

    model.add_image(np.zeros((10, 20, 30)))
    full = get_viewbox_size(model)

    get_grid(model).enabled = True
    gridded = get_viewbox_size(model)

    assert gridded != full
    assert all(g <= f for g, f in zip(gridded, full, strict=True))


def _canvas_size(viewer):
    """The full canvas size, however the installed napari spells it."""

    canvas = getattr(viewer, "canvas", None)
    return canvas.size if canvas is not None else viewer._canvas_size


def test_check_center_reports_the_camera_centre_when_in_view(model):
    """check_center reaches for both the viewbox size and the camera."""

    camera = get_camera(model)
    y, x = check_center(model, list(model.dims.current_step))

    assert (y, x) == (camera.center[-2], camera.center[-1])


def test_axes_overlay_is_pinned_to_a_canvas_corner(model):
    """napari >= 0.8 can pin the axes to a corner; 0.9 moved that overlay to
    viewer.canvas.overlays.axes."""

    set_axes_visible(model, True)

    assert axes_visible(model) is True
    if PINNED_AXES:
        assert get_axes(model).position == AXES_POSITION

    set_axes_visible(model, False)
    assert axes_visible(model) is False


def _deprecated_emitter_names(layer):
    """The layer properties napari has flagged with a WarningEmitter.

    Which properties those are differs per napari version (out_of_slice_display was
    only deprecated in 0.9, interpolation much earlier), so the tests ask the layer
    rather than hard-coding names.
    """

    return {
        name
        for name, emitter in layer.events.emitters.items()
        if isinstance(emitter, WarningEmitter)
    }


def test_deprecated_properties_are_not_synced():
    """napari marks a deprecated property with a WarningEmitter. Syncing one warns on
    every assignment and writes to something on its way out, so discovery skips it while
    still picking up its replacement."""

    layers = [Image(np.zeros((4, 4))), Points(np.zeros((2, 3)))]

    # guard against the assertion below passing for lack of anything to skip
    assert any(_deprecated_emitter_names(layer) for layer in layers)

    for layer in layers:
        # discovery mixes plain property names with {nested_attr: [props]} dicts
        names = {
            item for item in get_property_names(layer) if isinstance(item, str)
        }
        assert not _deprecated_emitter_names(layer).intersection(names)

    # the replacement for a deprecated property is still discovered
    assert "projection_mode" in get_property_names(Points(np.zeros((2, 3))))


def test_discovering_properties_does_not_warn():
    """Reading a deprecated property warns, so discovery must not touch one."""

    for layer in (Image(np.zeros((4, 4))), Points(np.zeros((2, 3)))):
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            warnings.simplefilter("error", DeprecationWarning)
            get_property_names(layer)
