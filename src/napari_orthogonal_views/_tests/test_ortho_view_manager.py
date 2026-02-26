import os
import tempfile

import numpy as np
from qtpy.QtWidgets import QWidget

from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    hide_orthogonal_views,
    show_orthogonal_views,
)
from napari_orthogonal_views.ortho_view_widget import OrthoViewWidget


def test_orthoview_manager(make_napari_viewer, qtbot):
    """Test initialization of the ortho view manager and its show/hide functions."""

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)
    hide_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: not m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, QWidget)
    m.cleanup()


def test_sync_camera(make_napari_viewer, qtbot):
    """Test synchronization of the camera events between the orthoviews."""

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)

    # Test zoom sync
    m.set_zoom_sync(True)
    w = m.right_widget
    zoom_emitter = m.viewer.camera.events.zoom

    # Check if any (emitter, handler) in _connections has this emitter
    assert any(em == zoom_emitter for em, _ in w._connections)

    # Check if the connection is removed
    m.set_zoom_sync(False)
    assert not any(em == zoom_emitter for em, _ in w._connections)

    # Test center sync
    m.set_center_sync(True)
    center_emitter = m.viewer.camera.events.center
    assert any(em == center_emitter for em, _ in w._connections)
    m.set_center_sync(False)
    assert not any(em == center_emitter for em, _ in w._connections)

    m.cleanup()


def test_update_dims_order_with_4d_data(make_napari_viewer, qtbot):
    """Test that update_dims_order correctly updates dimension order and crosshair order."""

    # Create viewer with 4D data (T, Z, Y, X)
    viewer = make_napari_viewer()
    data = np.random.rand(3, 10, 32, 32)  # T, Z, Y, X
    viewer.add_image(data, name="4D_data")

    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    assert isinstance(m.right_widget, OrthoViewWidget)
    assert isinstance(m.bottom_widget, OrthoViewWidget)

    # Initially, dims.order should be (0, 1, 2, 3) for T, Z, Y, X
    assert viewer.dims.order == (0, 1, 2, 3)

    # Check initial axis orders (last 3 dims in inverse notation)
    # For main viewer: (0, 1, 2, 3) -> (-4, -3, -2, -1) -> last 3: (-3, -2, -1)
    assert m.cursor_overlay.axis_order == (-3, -2, -1)

    # Test changing dimension order in the main viewer
    # Reorder to (1, 0, 2, 3) - swap T and Z
    viewer.dims.order = (1, 0, 2, 3)
    qtbot.wait(100)  # Allow time for event to propagate

    # After reordering, update_dims_order should have been called
    # view_order = [1, 0, 2, 3] -> in relative notation: [-3, -4, -2, -1]
    # last 3 are [-4, -2, -1]
    expected_axis_order = (-4, -2, -1)
    assert m.cursor_overlay.axis_order == expected_axis_order

    # Verify the right and bottom widget's crosshair axis order was updated
    right_axis_order = m.right_widget.vm_container.cursor_overlay.axis_order
    assert right_axis_order == (-1, -2, -4)
    bottom_axis_order = m.bottom_widget.vm_container.cursor_overlay.axis_order
    assert bottom_axis_order == (-2, -4, -1)

    # Verify that dimension orders in orthogonal views are updated
    # Right widget uses order (-1, -2, -3), bottom uses (-2, -3, -1)
    assert m.right_widget.qt_viewer.dims.dims.order == (1, 3, 2, 0)
    assert m.bottom_widget.qt_viewer.dims.dims.order == (1, 2, 0, 3)

    m.cleanup()


def test_screenshot_main_view_only(make_napari_viewer, qtbot):
    """Test screenshot with only the main view."""
    viewer = make_napari_viewer()
    data = np.random.rand(32, 32, 32)
    viewer.add_image(data, name="test_data")

    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # Get screenshot with only main view
    screenshot = m.screenshot(include_right=False, include_bottom=False)

    # Should be 2D array with RGBA channels
    assert screenshot.ndim == 3
    assert screenshot.shape[2] == 4
    assert screenshot.dtype == np.uint8
    # Height and width should match main viewer
    assert screenshot.shape[0] > 0
    assert screenshot.shape[1] > 0

    m.cleanup()


def test_screenshot_with_all_views(make_napari_viewer, qtbot):
    """Test screenshot with main, right, and bottom views."""
    viewer = make_napari_viewer()
    data = np.random.rand(32, 32, 32)
    viewer.add_image(data, name="test_data")

    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # Get screenshot with all views
    screenshot = m.screenshot(include_right=True, include_bottom=True)

    assert screenshot.ndim == 3
    assert screenshot.shape[2] == 4
    assert screenshot.dtype == np.uint8
    # Should be larger than main view alone
    assert screenshot.shape[0] > 0
    assert screenshot.shape[1] > 0

    m.cleanup()


def test_screenshot_save_to_file(make_napari_viewer, qtbot):
    """Test saving screenshot to a file."""
    viewer = make_napari_viewer()
    data = np.random.rand(32, 32, 32)
    viewer.add_image(data, name="test_data")

    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # Save screenshot to temporary file
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "test_screenshot.png")
        screenshot = m.screenshot(
            path=filepath, include_right=False, include_bottom=False
        )

        # Check that file was created
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0

        # Check that returned array matches what would be saved
        assert screenshot.ndim == 3
        assert screenshot.shape[2] == 4

    m.cleanup()


def test_screenshot_with_different_view_combinations(
    make_napari_viewer, qtbot
):
    """Test screenshot with different combinations of views."""
    viewer = make_napari_viewer()
    data = np.random.rand(32, 32, 32)
    viewer.add_image(data, name="test_data")

    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # Get screenshots with different view combinations
    main_only = m.screenshot(include_right=False, include_bottom=False)
    with_right = m.screenshot(include_right=True, include_bottom=False)
    with_bottom = m.screenshot(include_right=False, include_bottom=True)
    with_both = m.screenshot(include_right=True, include_bottom=True)

    # All should be valid
    for screenshot in [main_only, with_right, with_bottom, with_both]:
        assert screenshot.ndim == 3
        assert screenshot.shape[2] == 4
        assert screenshot.dtype == np.uint8

    # Incrementally larger screenshots
    assert (
        main_only.shape[0] * main_only.shape[1]
        < with_right.shape[0] * with_right.shape[1]
    )
    assert (
        main_only.shape[0] * main_only.shape[1]
        < with_bottom.shape[0] * with_bottom.shape[1]
    )
    assert (
        with_right.shape[0] * with_right.shape[1]
        < with_both.shape[0] * with_both.shape[1]
    )

    m.cleanup()


def test_screen_record_with_views(make_napari_viewer, qtbot):
    """Test screen recording with multiple views included."""
    viewer = make_napari_viewer()
    data = np.random.rand(3, 32, 32, 32)  # T, Z, Y, X
    viewer.add_image(data, name="test_data")

    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "test_recording_views.avi")
        m.screen_record(
            path=filepath,
            axis=0,
            incl_right=True,
            incl_bottom=True,
            fps=7,
        )

        # Check that file was created
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0

    m.cleanup()
