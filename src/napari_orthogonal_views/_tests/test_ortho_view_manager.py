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
