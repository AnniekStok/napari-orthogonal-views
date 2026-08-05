"""Built-in, per-layer-type syncing behaviour, expressed as layer hooks.

These functions are the default behaviour of the orthogonal views, and are applied to
every layer that matches their type. They are also the reference implementation of the
hook contract.

They are registered like any other hook, under the names in ``DEFAULT_LAYER_HOOKS``, so
an application can add its own alongside them
(``manager.register_layer_hook(layer_type, hook)``), replace one
(``manager.set_layer_hook("labels_paint", my_hook)``), or switch one off when it handles
that behaviour itself (``manager.set_layer_hook("labels_paint", None)``).

Hook contract
-------------
A hook is called once per layer, per orthogonal view, in registration order, as::

    hook(container, orig_layer, copied_layer)

where ``container`` is the :class:`ViewerModelContainer` that owns ``copied_layer``.
It exists so a hook can reach the shared syncing primitives -
``container.update_data``, ``container.sync_selected_data`` and
``container.blocked()``.

Whatever a hook changes has to be undoable again, because layers can be removed while
the orthogonal views stay open. A hook therefore returns an iterable mixing:

- ``(signal, handler)`` pairs it connected, which get disconnected on cleanup, and
- zero-argument callables that undo anything else it did.

Returning ``None`` is allowed, and means the hook left nothing behind.

"""

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from napari.layers import Labels, Layer, Points

if TYPE_CHECKING:
    from napari_orthogonal_views.ortho_view_widget import ViewerModelContainer

_UNDO_REDO_HOOK = "_ortho_undo_redo_hook"


class _UndoRedoHook:
    """Replaces ``undo``/``redo`` on a layer so they also sync to other layers.

    Installed at most once per layer: every orthogonal view registers a
    ``(target_layer, update_fn)`` subscription on the same hook instead of wrapping
    the methods again.

    The original methods are put back as soon as the last subscription is gone.
    """

    def __init__(self, layer: Layer) -> None:
        self.layer = layer
        self.subscriptions: list[tuple[Layer, Callable]] = []
        self._original = {
            name: getattr(layer, name) for name in ("undo", "redo")
        }
        self._had_own = {
            name: name in layer.__dict__ for name in ("undo", "redo")
        }
        self._previous = {
            name: layer.__dict__.get(name) for name in ("undo", "redo")
        }
        for name in ("undo", "redo"):
            layer.__dict__[name] = self._make_wrapper(name)
        self._installed = {
            name: layer.__dict__[name] for name in ("undo", "redo")
        }
        layer.__dict__[_UNDO_REDO_HOOK] = self

    def _make_wrapper(self, name: str) -> Callable:
        def wrapper() -> None:
            self._original[name]()
            for target, update_fn in list(self.subscriptions):
                update_fn(source=self.layer, target=target)

        return wrapper

    def subscribe(self, target: Layer, update_fn: Callable) -> None:
        self.subscriptions.append((target, update_fn))

    def unsubscribe(self, target: Layer, update_fn: Callable) -> None:
        with contextlib.suppress(ValueError):
            self.subscriptions.remove((target, update_fn))
        if self.subscriptions:
            return
        self._uninstall()

    def _uninstall(self) -> None:
        """Restore the layer's own undo/redo, unless something wrapped them since."""

        for name in ("undo", "redo"):
            if self.layer.__dict__.get(name) is not self._installed[name]:
                continue
            if self._had_own[name]:
                self.layer.__dict__[name] = self._previous[name]
            else:
                self.layer.__dict__.pop(name, None)
        if self.layer.__dict__.get(_UNDO_REDO_HOOK) is self:
            self.layer.__dict__.pop(_UNDO_REDO_HOOK, None)


def _subscribe_undo_redo(
    source_layer: Layer, target_layer: Layer, update_fn: Callable
) -> Callable:
    """Make ``source_layer.undo()``/``redo()`` also update ``target_layer``.

    Returns a callable that removes the subscription again, so an original layer never
    keeps syncing to a copied layer that no longer exists.
    """

    hook = source_layer.__dict__.get(_UNDO_REDO_HOOK)
    if hook is None:
        hook = _UndoRedoHook(source_layer)
    hook.subscribe(target_layer, update_fn)

    def unsubscribe() -> None:
        hook.unsubscribe(target_layer, update_fn)

    return unsubscribe


def sync_labels_undo_redo(
    container: "ViewerModelContainer",
    orig_layer: Labels,
    copied_layer: Labels,
) -> list[Callable]:
    """Make undo/redo on either layer refresh the other view.

    Both layers share one undo/redo history (see ``copy_layer``), so the stacks stay in
    step by themselves, but the layer that did not have the method called on it never
    learns that its data changed underneath it.

    Args:
        container: the ViewerModelContainer owning ``copied_layer``.
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The two unsubscribe callables, to be run on cleanup.
    """

    return [
        _subscribe_undo_redo(copied_layer, orig_layer, container.update_data),
        _subscribe_undo_redo(orig_layer, copied_layer, container.update_data),
    ]


def sync_labels_paint(
    container: "ViewerModelContainer",
    orig_layer: Labels,
    copied_layer: Labels,
) -> list[tuple]:
    """Mirror painting between the two layers.

    Painting is connected explicitly because the paint event does not trigger a 'data'
    event by itself, and syncing between viewers hangs off the latter. The eraser and
    the fill bucket emit the same event, so they need no separate connection.

    Args:
        container: the ViewerModelContainer owning ``copied_layer``.
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The ``(signal, handler)`` pairs that were connected.
    """

    def copied_paint(_event) -> None:
        # copy data from copied_layer to orig_layer (orig_layer emits signal,
        # which triggers update on other viewer models, if present)
        container.update_data(source=copied_layer, target=orig_layer)

    def orig_paint(_event) -> None:
        # copy data from orig_layer to copied_layer (copied_layer emits signal
        # but we don't process it)
        container.update_data(source=orig_layer, target=copied_layer)

    copied_layer.events.paint.connect(copied_paint)
    orig_layer.events.paint.connect(orig_paint)

    return [
        (copied_layer.events.paint, copied_paint),
        (orig_layer.events.paint, orig_paint),
    ]


def sync_points_selection(
    container: "ViewerModelContainer",
    orig_layer: Points,
    copied_layer: Points,
) -> list[tuple]:
    """Mirror the point selection between the two layers, and sync it once up front.

    ``points.selected_data`` is a Selection (an evented set) rather than a plain
    property, so it is synced here instead of through the generic property syncing,
    which would only carry its single ``active`` element.

    Args:
        container: the ViewerModelContainer owning ``copied_layer``.
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The ``(signal, handler)`` pairs that were connected.
    """

    def orig_selection(_event) -> None:
        container.sync_selected_data(orig_layer, copied_layer)

    def copied_selection(_event) -> None:
        container.sync_selected_data(copied_layer, orig_layer)

    orig_signal = orig_layer.selected_data.events.items_changed
    copied_signal = copied_layer.selected_data.events.items_changed
    orig_signal.connect(orig_selection)
    copied_signal.connect(copied_selection)

    # initial sync
    container.sync_selected_data(orig_layer, copied_layer)

    return [(orig_signal, orig_selection), (copied_signal, copied_selection)]


# The built-in hooks. They seed the layer hook registry, which is keyed by name so a
# single one can be replaced or disabled without disturbing the others.
DEFAULT_LAYER_HOOKS: dict[str, tuple[type, Callable]] = {
    "labels_undo_redo": (Labels, sync_labels_undo_redo),
    "labels_paint": (Labels, sync_labels_paint),
    "points_selection": (Points, sync_points_selection),
}
