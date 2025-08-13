from qtpy.QtWidgets import QWidget

from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    hide_orthogonal_views,
    show_orthogonal_views,
)
from napari_orthogonal_views.ortho_view_widget import OrthoViewWidget


def test_orthoview_manager(make_napari_viewer, qtbot):
    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)
    hide_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: not m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, QWidget)
    m.cleanup()
