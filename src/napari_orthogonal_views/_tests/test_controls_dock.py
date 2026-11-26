from napari_orthogonal_views.ortho_view_manager import (
    OrthoViewControlsWidget,
    ShowOrthoViewsWidget,
    _get_manager,
    show_orthogonal_views,
)


def _in_slot(m) -> bool:
    return m.controls_tab.parent() is m._controls_slot


def test_pop_out_and_dock_back(make_napari_viewer, qtbot):
    """The controls can move to a napari dock widget and back to their slot."""

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(m.is_shown, timeout=1000)
    assert _in_slot(m)
    # the bottom splitter keeps two children, so it stays in sync with the top one
    assert m.h_splitter_bottom.count() == 2

    m._dock_button.click()
    assert m._controls_dock is not None
    assert "Ortho view controls" in viewer.window.dock_widgets
    # docked on the right, like any other napari widget
    assert not m._controls_dock.isFloating()
    assert m.h_splitter_bottom.count() == 2

    m._dock_button.click()
    assert m._controls_dock is None
    assert _in_slot(m)
    qtbot.waitUntil(
        lambda: "Ortho view controls" not in viewer.window.dock_widgets
    )
    m.cleanup()


def test_closing_dock_returns_controls(make_napari_viewer, qtbot):
    """Closing the dock widget (its 'x' button) puts the controls back in their
    slot."""

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    m.dock_controls()
    qtbot.waitUntil(lambda: m._controls_dock is not None, timeout=1000)

    # what the close button of napari's dock title bar does
    m._controls_dock.destroyOnClose()
    qtbot.waitUntil(lambda: m._controls_dock is None, timeout=1000)
    assert _in_slot(m)
    m.cleanup()


def test_close_viewer_with_popped_out_controls(make_napari_viewer, qtbot):
    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(m.is_shown, timeout=1000)
    m.dock_controls()
    viewer.close()


def test_launcher_widgets(make_napari_viewer, qtbot):
    """The plugin menu entries act on the manager and then remove themselves."""

    viewer = make_napari_viewer()
    _, widget = viewer.window.add_plugin_dock_widget(
        "napari-orthogonal-views", "Show orthogonal views"
    )
    assert isinstance(widget, ShowOrthoViewsWidget)
    m = _get_manager(viewer)
    # the manager holds the real viewer, not the plugin's PublicOnlyProxy
    assert m.viewer is viewer
    assert type(m.main_window).__name__ != "PublicOnlyProxy"
    qtbot.waitUntil(m.is_shown, timeout=1000)
    qtbot.waitUntil(lambda: len(viewer.window.dock_widgets) == 0, timeout=1000)

    # hidden popped out controls are shown again by the controls menu entry
    m.dock_controls()
    m._controls_dock.close()
    assert m._controls_dock.isHidden()
    _, widget = viewer.window.add_plugin_dock_widget(
        "napari-orthogonal-views", "Ortho view controls"
    )
    assert isinstance(widget, OrthoViewControlsWidget)
    qtbot.waitUntil(lambda: not m._controls_dock.isHidden(), timeout=1000)
    qtbot.waitUntil(
        lambda: list(viewer.window.dock_widgets) == ["Ortho view controls"],
        timeout=1000,
    )
    m.cleanup()
