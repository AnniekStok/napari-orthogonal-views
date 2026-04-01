import numpy as np
from napari.components.viewer_model import ViewerModel
from napari.qt import QtViewer
from qtpy.QtCore import QEvent, QObject, Qt


def center_cross_on_mouse(
    viewer_model: ViewerModel,
):
    """Center the viewer dimension step to the mouse position"""

    step = tuple(
        np.round(
            [
                max(min_, min(p, max_)) / step
                for p, (min_, max_, step) in zip(
                    viewer_model.cursor.position,
                    viewer_model.dims.range,
                    strict=False,
                )
            ]
        ).astype(int)
    )
    viewer_model.dims.current_step = step


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
