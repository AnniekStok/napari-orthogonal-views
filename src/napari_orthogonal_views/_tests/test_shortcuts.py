import numpy as np
import pytest
from napari.utils.interactions import Shortcut
from napari.utils.key_bindings import KeymapHandler

from napari_orthogonal_views import shortcuts
from napari_orthogonal_views._tests.test_viewer_utils import (  # noqa: F401
    hover,
    hoverable_canvases,
)
from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    show_orthogonal_views,
)
from napari_orthogonal_views.shortcuts import (
    center_shortcuts,
    init_actions,
    set_center_shortcut,
)


@pytest.fixture(autouse=True)
def default_shortcut():
    """The shortcut lives in napari's process-wide action manager: start each test
    from, and put back, the state a fresh session has."""
    shortcuts._shortcut_chosen = False
    init_actions()
    yield
    shortcuts._shortcut_chosen = False
    set_center_shortcut(shortcuts.DEFAULT_CENTER_SHORTCUT)
    shortcuts._shortcut_chosen = False


def _press(models, canvases, key: str) -> bool:
    """Press `key` with the mouse over the second canvas, and return whether that
    centered its viewer model on the cursor.

    The step is not reset first: napari moves the cursor along with the slice, so
    resetting it would also move the point the view is centered on.
    """
    handler = KeymapHandler()
    handler.keymap_providers = [models[0]]
    with hover(canvases[1]):
        handler.press_key(key)
    return tuple(models[1].dims.current_step) == (7, 8, 9)


def test_rebind_moves_the_shortcut(hoverable_canvases):  # noqa: F811
    """After a rebind the new key centers the view, and "T" no longer does."""
    canvases, models = hoverable_canvases

    set_center_shortcut("G")

    assert center_shortcuts() == ["G"]
    assert not _press(models, canvases, "T")
    assert _press(models, canvases, "G")


def test_cleared_shortcut_does_nothing(hoverable_canvases):  # noqa: F811
    canvases, models = hoverable_canvases

    set_center_shortcut("")

    assert center_shortcuts() == []
    assert not _press(models, canvases, "T")


def test_new_manager_keeps_a_chosen_shortcut():
    """init_actions runs for every OrthoViewManager: it must not put "T" back
    after the shortcut was changed or cleared."""
    set_center_shortcut("G")
    init_actions()
    assert center_shortcuts() == ["G"]

    set_center_shortcut("")
    init_actions()
    assert center_shortcuts() == []


def test_init_actions_with_key_rebinds():
    init_actions("Shift-T")
    assert center_shortcuts() == ["Shift-T"]


def test_controls_label_follows_the_shortcut(make_napari_viewer, qtbot):
    """The hint in the controls shows the current shortcut, also after a rebind."""
    viewer = make_napari_viewer()
    viewer.add_image(np.zeros((10, 10, 10), dtype=np.uint8))
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    label = m.main_controls_widget.controls_widget.center_shortcut_label

    assert label.text() == "Press T to center view on mouse"

    set_center_shortcut("Shift-G")
    assert label.text() == (
        f"Press {Shortcut('Shift-G').platform} to center view on mouse"
    )

    set_center_shortcut("")
    assert label.text() == "No shortcut set to center view on mouse"
