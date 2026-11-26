"""The keyboard shortcut that centers every view on the mouse position.

The action is registered with napari's ``action_manager`` on ``ViewerModel``, so a
single shortcut covers the main viewer and the orthogonal views, in every window.
Defaults to "T" and can be changed with ``set_center_shortcut``.
"""

from __future__ import annotations

from napari.components.viewer_model import ViewerModel
from napari.utils.action_manager import action_manager
from napari.utils.interactions import Shortcut

from napari_orthogonal_views.viewer_utils import center_cross_on_mouse

CENTER_ACTION = "napari:move_point"
DEFAULT_CENTER_SHORTCUT = "T"

_shortcut_chosen = False  # to make sure the default is only applied once


def _register_action() -> None:
    """Register the action."""
    action_manager.register_action(
        name=CENTER_ACTION,
        command=center_cross_on_mouse,
        description="Move dims point to mouse position",
        keymapprovider=ViewerModel,
    )


def init_actions(key: str | None = None) -> None:
    """Register the center-on-mouse action and make sure it has a shortcut.

    Args:
        key (str | None): Shortcut to bind. None keeps the current shortcut, or binds the
        default ("T") if none was ever chosen.
    """
    _register_action()
    if key is not None:
        set_center_shortcut(key)
    elif not _shortcut_chosen:
        set_center_shortcut(DEFAULT_CENTER_SHORTCUT)


def set_center_shortcut(key: str) -> None:
    """Bind centering on the mouse to `key`, replacing its previous shortcut.
    Args:
        key (str): Shortcut in napari's notation (e.g. "T", "Shift-T")
    """
    global _shortcut_chosen
    _shortcut_chosen = True
    if CENTER_ACTION not in action_manager._actions:
        _register_action()
    if center_shortcuts() == ([key] if key else []):
        return
    action_manager.unbind_shortcut(CENTER_ACTION)
    if key:
        action_manager.bind_shortcut(CENTER_ACTION, key)


def center_shortcuts() -> list[str]:
    """The shortcuts currently bound to centering on the mouse."""
    return [
        str(key) for key in action_manager._shortcuts.get(CENTER_ACTION, [])
    ]


def center_shortcut_label() -> str:
    """One-line hint for the controls, e.g. "Press T to center view on mouse"."""
    shortcuts = center_shortcuts()
    if not shortcuts:
        return "No shortcut set to center view on mouse"
    return f"Press {Shortcut(shortcuts[0]).platform} to center view on mouse"
