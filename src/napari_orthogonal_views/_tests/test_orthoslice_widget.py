import numpy as np

from napari_orthogonal_views.controls import vispy_patches
from napari_orthogonal_views.controls.orthoslice_widget import (
    RANGE,
    SLICE,
    VOLUME,
)
from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    hide_orthogonal_views,
    show_orthogonal_views,
)


def test_vispy_patches_supported():
    """The vispy patches of the orthoslices match the installed vispy and napari. If
    this fails after an update, the orthoslices lose a feature (see vispy_patches).
    """

    assert (
        dict.fromkeys(vispy_patches.SUPPORTED, True) == vispy_patches.SUPPORTED
    )


def _ortho_viewer(make_napari_viewer, qtbot):
    """A 3D viewer with orthogonal views, an image and a points layer, both selected."""

    viewer = make_napari_viewer()
    rng = np.random.default_rng(0)
    image = viewer.add_image(rng.random((20, 30, 30)))
    points = viewer.add_points(rng.uniform(0, 20, (50, 3)))
    show_orthogonal_views(viewer)
    manager = _get_manager(viewer)
    qtbot.waitUntil(manager.is_shown, timeout=1000)
    viewer.dims.ndisplay = 3
    viewer.layers.selection.update({image, points})
    viewer.layers.selection._current = image
    return viewer, manager, image, points


def _planes(manager, layer):
    """The number of clipping planes per vispy node (main viewer first)."""

    return [
        len(node.clipping_planes) for _, node in manager._clip_nodes(layer)
    ]


def test_orthoslices_linked(make_napari_viewer, qtbot):
    """Linked layers are sliced together, and only clipped in 3D views."""

    viewer, manager, image, points = _ortho_viewer(make_napari_viewer, qtbot)
    widget = manager.orthoslice_widget
    assert widget.link_checkbox.isVisibleTo(widget)
    widget.link_checkbox.setChecked(True)

    widget.mode_buttons[SLICE].click()
    for layer in (image, points):
        assert layer.metadata["orthoslices"]["mode"] == SLICE
        assert len(layer.experimental_clipping_planes) == 6
    assert _planes(manager, points) == [6, 0, 0]  # the orthogonal views are 2D
    viewer.screenshot(canvas_only=True)  # the patched shaders compile

    widget.mode_buttons[RANGE].click()
    widget.range_sliders[0].setValue((5, 10))
    assert points.metadata["orthoslices"]["planes"] == (
        image.metadata["orthoslices"]["planes"]
    )
    viewer.screenshot(canvas_only=True)

    # deselected linked layers keep following
    viewer.layers.selection.clear()
    viewer.layers.selection.add(image)
    viewer.layers.selection.active = image
    widget.range_sliders[0].setValue((2, 12))
    assert points.metadata["orthoslices"]["planes"][-3]["range"] == (2, 12)

    widget.mode_buttons[VOLUME].click()
    assert _planes(manager, points) == [0, 0, 0]

    hide_orthogonal_views(viewer)
    assert image.experimental_clipping_planes == []
    manager.cleanup()


def test_failing_patch_is_contained(make_napari_viewer, qtbot, monkeypatch):
    """A vispy patch that fails turns off its feature, without breaking the widget."""

    viewer, manager, image, points = _ortho_viewer(make_napari_viewer, qtbot)
    widget = manager.orthoslice_widget

    def broken(*args, **kwargs):
        raise RuntimeError("vispy changed")

    monkeypatch.setitem(vispy_patches.SUPPORTED, "union", True)
    monkeypatch.setattr(vispy_patches, "_union_of_slabs_glsl", broken)
    widget.mode_buttons[SLICE].click()

    assert not vispy_patches.SUPPORTED["union"]
    assert not widget.mode_buttons[SLICE].isEnabled()
    assert len(image.experimental_clipping_planes) == 6  # vispy's own clipping
    viewer.dims.current_step = (3, 3, 3)
    viewer.screenshot(canvas_only=True)
    manager.cleanup()
