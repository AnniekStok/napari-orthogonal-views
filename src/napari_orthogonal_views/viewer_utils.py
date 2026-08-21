import contextlib
import weakref

import numpy as np
from napari.components.viewer_model import ViewerModel
from napari.qt import QtViewer
from qtpy.QtCore import QEvent, QObject, Qt
from qtpy.QtGui import QCursor
from qtpy.QtWidgets import QWidget

# Canvases registered by register_canvas, mapped to the viewer model they show.
# Weakly keyed so closed viewers drop out on their own.
_HOVERABLE_CANVASES: weakref.WeakKeyDictionary[QWidget, ViewerModel] = (
    weakref.WeakKeyDictionary()
)


def register_canvas(qt_viewer: QtViewer) -> None:
    """Make a canvas a candidate for shortcuts that act on the hovered viewer model."""

    _HOVERABLE_CANVASES[qt_viewer.canvas.native] = qt_viewer.viewer


def viewer_model_under_mouse() -> ViewerModel | None:
    """Return the viewer model whose canvas the mouse is currently over, if any."""

    pos = QCursor.pos()
    for canvas, viewer_model in _HOVERABLE_CANVASES.items():
        with contextlib.suppress(RuntimeError):
            if not canvas.isVisible():
                continue
            if canvas.underMouse() or canvas.rect().contains(
                canvas.mapFromGlobal(pos)
            ):
                return viewer_model
    return None


def center_cross_on_mouse(_viewer_model: ViewerModel):
    """Center the viewer dimension step of the hovered canvas to the mouse position.

    Instead of relying on which viewer_model received the signal, which may be outdated
    due to focus loss, check which viewer_model currently has the cursor, and use that to
    center the cross. Do not move the dims.step at all when the mouse cursor is not on
    any canvas.
    """

    target = viewer_model_under_mouse()
    if target is None:
        return

    step = tuple(
        np.round(
            [
                max(min_, min(p, max_)) / step
                for p, (min_, max_, step) in zip(
                    target.cursor.position,
                    target.dims.range,
                    strict=False,
                )
            ]
        ).astype(int)
    )
    target.dims.current_step = step


def activate_on_hover(qt_viewer: QtViewer):
    """Activate mouse tracking on the canvas using event filtering,
    without breaking napari's overlay event system.

    """
    canvas = qt_viewer.canvas.native
    canvas.setMouseTracking(True)

    class CanvasEventFilter(QObject):
        """Event filter to handle mouse enter without breaking overlay events."""

        def __init__(self, canvas_widget):
            super().__init__()
            self.canvas_widget = canvas_widget

        def eventFilter(self, obj, event):
            # Only handle Enter events for the canvas
            if obj is self.canvas_widget and event.type() == QEvent.Enter:
                self.canvas_widget.setFocus(Qt.MouseFocusReason)
            # Always return False to allow normal event processing
            return False

    # Install the event filter instead of replacing the method
    filter_obj = CanvasEventFilter(canvas)
    canvas.installEventFilter(filter_obj)
    # Keep a reference to prevent garbage collection
    canvas._hover_event_filter = filter_obj
