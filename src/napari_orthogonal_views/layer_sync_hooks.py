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

    hook(orig_layer, copied_layer)

Whatever a hook changes has to be undoable again, because layers can be removed while
the orthogonal views stay open. A hook therefore returns an iterable mixing:

- ``(signal, handler)`` pairs it connected, which get disconnected on cleanup, and
- zero-argument callables that undo anything else it did.

Returning ``None`` is allowed, and means the hook left nothing behind.

Most syncing needs nothing more than the right event: the orthogonal views already sync
``data`` like any other layer property, so an edit that napari performs without emitting
``data`` only has to be announced (see :func:`emit_data`) for the views to pick it up.
A hook that instead writes to the other layer itself has to make sure its own write does
not come straight back at it - see :func:`sync_points_selection` for the pattern.
"""

import contextlib
from collections.abc import Callable
from typing import Any

from napari.layers import Labels, Layer, Points

_MISSING = object()


def emit_data(layer: Layer) -> None:
    """Announce that ``layer``'s array changed in place.

    napari does not emit ``data`` for every edit it makes to a layer (painting and
    undo/redo change the array without it), and the orthogonal views sync ``data`` like
    any other property, so such an edit stays invisible to them. Emitting the event is
    all a hook has to do - the property syncing carries it to every other view from
    there, in whichever direction the edit was made.
    """

    layer.events.data(value=layer.data)


class _LayerPatch:
    """A patch installed on a layer once, however many orthogonal views want it.

    Every view asks for the same behaviour on the same layer, so the patch is shared and
    reference counted: :meth:`attach` returns the callable that drops one view's claim
    on it, and the last one to let go puts the layer back the way it was.
    """

    #: attribute the patch parks itself under, on the layer it patches
    key: str = ""

    def __init__(self, layer: Layer) -> None:
        self.layer = layer
        self._uses = 0

    @classmethod
    def attach(cls, layer: Layer) -> Callable[[], None]:
        """Install the patch on ``layer`` if it is not there yet, and return the
        callable that releases this caller's claim on it."""

        patch = layer.__dict__.get(cls.key)
        if patch is None:
            patch = cls(layer)
            layer.__dict__[cls.key] = patch
            patch._install()
        patch._uses += 1
        return patch._release

    def _release(self) -> None:
        self._uses -= 1
        if self._uses > 0:
            return
        if self.layer.__dict__.get(self.key) is self:
            self.layer.__dict__.pop(self.key, None)
        self._uninstall()

    def _install(self) -> None:
        raise NotImplementedError

    def _uninstall(self) -> None:
        raise NotImplementedError


class _PaintPatch(_LayerPatch):
    """Emits ``data`` whenever a Labels layer is painted.

    Painting emits a ``paint`` event but not a ``data`` event, so the edit would
    otherwise never reach the other views. The eraser and the fill bucket emit the same
    event, so they need no separate handling.
    """

    key = "_ortho_paint_patch"

    def _install(self) -> None:
        def on_paint(_event) -> None:
            emit_data(self.layer)

        self._handler = on_paint
        self.layer.events.paint.connect(on_paint)

    def _uninstall(self) -> None:
        with contextlib.suppress(ValueError, RuntimeError, TypeError):
            self.layer.events.paint.disconnect(self._handler)


class _UndoRedoPatch(_LayerPatch):
    """Emits ``data`` after ``undo``/``redo`` on a Labels layer.

    Undo and redo restore the array in place and emit nothing at all, so they are
    wrapped on the instance. The original layer and its copies share one undo/redo
    history (see ``copy_layer``), so the stacks stay in step by themselves; only the
    news of the change has to travel.

    The original methods are put back as soon as the last orthogonal view is gone,
    unless something else wrapped them in the meantime.
    """

    key = "_ortho_undo_redo_patch"
    _METHODS = ("undo", "redo")

    def _install(self) -> None:
        self._original = {
            name: getattr(self.layer, name) for name in self._METHODS
        }
        self._previous = {
            name: self.layer.__dict__.get(name, _MISSING)
            for name in self._METHODS
        }
        for name in self._METHODS:
            self.layer.__dict__[name] = self._wrap(name)
        self._installed = {
            name: self.layer.__dict__[name] for name in self._METHODS
        }

    def _wrap(self, name: str) -> Callable:
        def wrapper() -> None:
            self._original[name]()
            emit_data(self.layer)

        return wrapper

    def _uninstall(self) -> None:
        for name in self._METHODS:
            if self.layer.__dict__.get(name) is not self._installed[name]:
                continue  # something wrapped it since; leave that wrapper alone
            if self._previous[name] is _MISSING:
                self.layer.__dict__.pop(name, None)
            else:
                self.layer.__dict__[name] = self._previous[name]


def sync_labels_undo_redo(
    orig_layer: Labels, copied_layer: Labels
) -> list[Callable]:
    """Make undo/redo on either layer reach the other views.

    Args:
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The two release callables, to be run on cleanup.
    """

    return [
        _UndoRedoPatch.attach(orig_layer),
        _UndoRedoPatch.attach(copied_layer),
    ]


def sync_labels_paint(
    orig_layer: Labels, copied_layer: Labels
) -> list[Callable]:
    """Make painting on either layer reach the other views.

    Args:
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The two release callables, to be run on cleanup.
    """

    return [
        _PaintPatch.attach(orig_layer),
        _PaintPatch.attach(copied_layer),
    ]


def sync_points_selection(
    orig_layer: Points, copied_layer: Points
) -> list[tuple[Any, Callable]]:
    """Mirror the point selection between the two layers, and sync it once up front.

    ``points.selected_data`` is a Selection (an evented set) rather than a plain
    property, so it is synced here instead of through the generic property syncing,
    which would only carry its single ``active`` element.

    Unlike the Labels hooks, this one writes to the other layer itself, which makes that
    layer emit and would sync straight back. ``syncing`` is what stops that: it is per
    layer pair, so the other orthogonal view still hears about the change.

    Args:
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The ``(signal, handler)`` pairs that were connected.
    """

    syncing = False

    def push(source: Points, target: Points) -> None:
        nonlocal syncing
        if syncing:
            return  # our own write, coming back at us
        syncing = True
        try:
            target.selected_data = set(source.selected_data)
        finally:
            syncing = False

    def orig_selection(_event=None) -> None:
        push(orig_layer, copied_layer)

    def copied_selection(_event=None) -> None:
        push(copied_layer, orig_layer)

    orig_signal = orig_layer.selected_data.events.items_changed
    copied_signal = copied_layer.selected_data.events.items_changed
    orig_signal.connect(orig_selection)
    copied_signal.connect(copied_selection)

    orig_selection()  # initial sync

    return [(orig_signal, orig_selection), (copied_signal, copied_selection)]


# The built-in hooks. They seed the layer hook registry, which is keyed by name so a
# single one can be replaced or disabled without disturbing the others.
DEFAULT_LAYER_HOOKS: dict[str, tuple[type, Callable]] = {
    "labels_undo_redo": (Labels, sync_labels_undo_redo),
    "labels_paint": (Labels, sync_labels_paint),
    "points_selection": (Points, sync_points_selection),
}
