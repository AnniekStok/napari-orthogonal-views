"""Built-in, per-layer-type syncing behaviour, expressed as layer hooks.

These functions are the default behaviour of the orthogonal views, and are applied to
every layer that matches their type. They are registered under the names in
``DEFAULT_LAYER_HOOKS``. Specifically, they ensure that undo/redo and painting on Labels
layers, and selection on Points layers, are mirrored to the other views. This would
otherwise fail, because these operations do not emit events that the standard property
syncing can pick up.

The active tool and the layer visibility are handled here as well, because changing the
visibility also changes the tool, which needs to be kept in sync.

Custom hooks, that change the behavior of an ortho view for a particular layer type, can
be added via manager.register_layer_hook(layer_type, hook).

Default hooks can be replaced with a custom one via
manager.set_layer_hook("labels_paint", my_hook), or switched off entirely via
manager.set_layer_hook("labels_paint", None).

A hook is defined as a callable that takes two arguments, the original layer and its
copy in the orthogonal view, and returns an iterable of cleanup callables.
The callable defines the behaviour that should be applied to the layer.

Example:
def my_hook(orig_layer, copied_layer):
    # do something to the layers, e.g. connect a signal to a handler
    orig_layer.events.some_signal.connect(my_handler)
    # return a cleanup callable that disconnects the signal again
    return [lambda: orig_layer.events.some_signal.disconnect(my_handler)]

A hook is called once per layer, per orthogonal view, in registration order, as:

    hook(orig_layer, copied_layer)

Whatever a hook changes has to be undoable again, because layers can be removed while
the orthogonal views stay open. A hook therefore returns an iterable mixing:

- ``(signal, handler)`` pairs it connected, which get disconnected on cleanup, and
- zero-argument callables that undo anything else it did.

Returning ``None`` is allowed, and means the hook left nothing behind.

"""

import contextlib
from collections.abc import Callable
from typing import Any

from napari.layers import Labels, Layer, Points

_MISSING = object()

#: Layer properties that napari does not treat as plain state. They are kept synced by sync_layer_tool.
TOOL_PROPERTIES = frozenset({"mode", "visible"})


def emit_data(layer: Layer) -> None:
    """Announce that ``layer``'s array changed in place.

    napari does not emit ``data`` for every edit it makes to a layer (painting and
    undo/redo change the array without it). The hook emits the event so that the existing
    property syncing picks it up and carries it to the other views.
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
    history, but they need to be notified when the array has updated so that they can
    refresh.

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


def sync_layer_tool(
    orig_layer: Layer, copied_layer: Layer
) -> list[tuple[Any, Callable]]:
    """Keep the active tool (the layer mode) and the layer visibility identical on a
    layer and its copies in the orthogonal views.

    Both are synced here instead of through the generic property syncing, because napari
    does not treat them as plain state and syncing them as such lets the views drift
    apart: layer.mode silently becomes pan_zoom when the layer is invisible, and assigning a layer the mode it already holds emits nothing.
    The original layer is the single source of truth. A change on either side is applied
    to the other, and the tool is re-applied after every visibility change.

    Args:
        orig_layer: the layer on the main viewer.
        copied_layer: its counterpart in the orthogonal view.

    Returns:
        The ``(signal, handler)`` pairs that were connected.
    """

    # prevent syncing back to itself
    syncing = False

    # Only layers of the same type can have their mode synced. Visibility should always be synced.
    sync_mode = type(orig_layer)._modeclass is type(copied_layer)._modeclass

    def reconcile() -> None:
        """Bring the copy in line with the original: visibility first, then the tool."""

        if copied_layer.visible != orig_layer.visible:
            copied_layer.visible = orig_layer.visible
        if sync_mode and copied_layer.mode != orig_layer.mode:
            copied_layer.mode = orig_layer.mode

    def from_orig(_event=None) -> None:
        """Apply a change made on the original layer to this orthogonal view."""

        nonlocal syncing
        if syncing:
            return
        syncing = True
        try:
            reconcile()
        finally:
            syncing = False

    def from_copy(_event=None) -> None:
        """Apply a change made in this orthogonal view to the original layer."""

        nonlocal syncing
        if syncing:
            return
        syncing = True
        try:
            if orig_layer.visible != copied_layer.visible:
                orig_layer.visible = copied_layer.visible
            # A mode assigned to a hidden layer is coerced to pan_zoom, so leave the
            # original alone while it is hidden, and let the visibility change carry the
            # tool over instead.
            if (
                sync_mode
                and orig_layer.visible
                and orig_layer.mode != copied_layer.mode
            ):
                orig_layer.mode = copied_layer.mode
            reconcile()
        finally:
            syncing = False

        # Explicitely reread the visibility and mode from the original instead of relying on the signal that may not be have emitted (if there was no change).
        orig_layer.events.visible()
        if sync_mode:
            orig_layer.events.mode(mode=str(orig_layer.mode))

    connections: list[tuple[Any, Callable]] = []
    for layer, handler in ((orig_layer, from_orig), (copied_layer, from_copy)):
        for name in sorted(TOOL_PROPERTIES):
            signal = getattr(layer.events, name)
            signal.connect(handler)
            connections.append((signal, handler))

    from_orig()

    return connections


def sync_points_selection(
    orig_layer: Points, copied_layer: Points
) -> list[tuple[Any, Callable]]:
    """Mirror the point selection between the two layers, and sync it once up front.

    ``points.selected_data`` is a Selection (an evented set) rather than a plain
    property, so it is synced here instead of through the generic property syncing,
    which would only carry its single ``active`` element.

    Unlike the Labels hooks, this writes to the other layer itself, which makes that
    layer emit and would sync straight back, so a flag (``syncing``) is needed to prevent
     that.

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
            return  # do not sync back
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


# Define built-in hooks
DEFAULT_LAYER_HOOKS: dict[str, tuple[type, Callable]] = {
    "layer_tool": (Layer, sync_layer_tool),
    "labels_undo_redo": (Labels, sync_labels_undo_redo),
    "labels_paint": (Labels, sync_labels_paint),
    "points_selection": (Points, sync_points_selection),
}
