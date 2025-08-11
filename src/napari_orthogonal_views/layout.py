from typing import Dict, Tuple

from napari.viewer import Viewer
from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import (
    QLayout,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from napari_orthogonal_views._widget import OrthViewerWidget
from napari_orthogonal_views.cross_widget import CrossWidget

# Keep managers per viewer (so multiple Napari windows are supported)
_MANAGERS: Dict[int, "OrthoViewManager"] = {}


class OrthoViewManager:
    """Manage insertion/removal of orthogonal views without replacing the main central widget.

    Behavior:
    - Keeps the original canvas widget and reinserts it on hide()
    - Inserts a container (splitter layout) into the same index in the central widget's layout
      so the QMainWindow geometry is preserved.
    """

    def __init__(self, viewer: Viewer):
        self.viewer = viewer
        self.main_window = viewer.window._qt_window
        self._central = self.main_window.centralWidget()
        self._original_canvas: QWidget | None = None
        self._original_index: int | None = None
        self._container: QWidget | None = None
        self._splitter_handlers: list[Tuple[QSplitter, object]] = []
        self._shown = False
        self._saved_window_size: QSize | None = None

    def is_shown(self) -> bool:
        return self._shown

    def show(self) -> None:

        if self._shown:
            return

        central = self.main_window.centralWidget()
        layout: QLayout = central.layout()
        if layout is None or layout.count() == 0:
            raise RuntimeError(
                "Central widget has no layout / widgets to attach to."
            )

        # Save window size as a fallback to restore if the window shrinks unexpectedly
        self._saved_window_size = self.main_window.size()

        # Find and remove the current canvas widget
        self._original_index = 0
        self._original_canvas = layout.itemAt(self._original_index).widget()
        if self._original_canvas is None:
            raise RuntimeError(
                "Couldn't locate canvas widget in central layout."
            )
        layout.removeWidget(self._original_canvas)
        self._original_canvas.setParent(None)  # detach

        # Build orthogonal layout (splitters + widgets)
        self.right_widget = OrthViewerWidget(
            self.viewer, order=(-1, -2, -3), sync_axes=[1]
        )
        h_splitter_top = QSplitter(Qt.Horizontal)
        h_splitter_top.addWidget(self._original_canvas)
        h_splitter_top.addWidget(self.right_widget)

        self.bottom_widget = OrthViewerWidget(
            self.viewer, order=(-2, -3, -1), sync_axes=[2]
        )
        self.cross_widget = CrossWidget(self.viewer)
        bottom_right_widget = QWidget()
        br_layout = QVBoxLayout()
        br_layout.setContentsMargins(0, 0, 0, 0)
        br_layout.addWidget(self.cross_widget)
        bottom_right_widget.setLayout(br_layout)

        h_splitter_bottom = QSplitter(Qt.Horizontal)
        h_splitter_bottom.addWidget(self.bottom_widget)
        h_splitter_bottom.addWidget(bottom_right_widget)

        # Compute sensible starting sizes
        central_width = max(1, central.width())
        central_height = max(1, central.height())
        side_width = max(100, int(central_width * 0.3))
        bottom_height = max(100, int(central_height * 0.3))

        # top: keep most space for the canvas and give side_width to the orthoview
        h_splitter_top.setSizes([central_width - side_width, side_width])
        h_splitter_bottom.setSizes([central_width - side_width, side_width])
        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.addWidget(h_splitter_top)
        v_splitter.addWidget(h_splitter_bottom)
        v_splitter.setSizes([central_height - bottom_height, bottom_height])

        # --- insert the container into the original central widget layout at the same position ---
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(v_splitter)
        container.setLayout(container_layout)

        layout.insertWidget(self._original_index, container)
        self._container = container

        # Sync the two horizontal splitters so user movement mirrors to the other
        def _connect_sync(source: QSplitter, target: QSplitter):
            def handler(*args, **kwargs):
                sizes = source.sizes()
                target.setSizes(sizes)

            source.splitterMoved.connect(handler)
            self._splitter_handlers.append((source, handler))

        _connect_sync(h_splitter_top, h_splitter_bottom)
        _connect_sync(h_splitter_bottom, h_splitter_top)

        central.layout().activate()
        central.update()

        self._shown = True

    def hide(self) -> None:
        if not self._shown:
            return

        if self._container is not None:
            v_splitter = self._container.layout().itemAt(0).widget()
            h_splitter_top = v_splitter.widget(0)
            canvas = h_splitter_top.widget(0)

            # Detach the canvas so deleting the container doesn't destroy it
            canvas.setParent(None)

        # Restore original central widget layout
        central = self.main_window.centralWidget()
        layout = central.layout()
        if layout is None:
            layout = QVBoxLayout()
            central.setLayout(layout)

        # Remove the orthogonal container
        if self._container is not None:
            self.right_widget.cleanup()
            self.bottom_widget.cleanup()
            self.cross_widget.cleanup()
            layout.removeWidget(self._container)
            self._container.deleteLater()
            self._container = None

        # Insert the canvas back
        layout.insertWidget(self._original_index, canvas)

        # Disconnect splitter handlers
        for source, handler in self._splitter_handlers:
            try:
                source.splitterMoved.disconnect(handler)
            except Exception:
                pass
        self._splitter_handlers.clear()

        self._shown = False
        central.layout().activate()
        central.update()


# Module-level helpers for napari.yaml entrypoints
def _get_manager(viewer: Viewer) -> OrthoViewManager:
    key = id(viewer)
    if key not in _MANAGERS:
        _MANAGERS[key] = OrthoViewManager(viewer)
    return _MANAGERS[key]


def show_orthogonal_views(viewer: Viewer) -> None:
    """Show orthogonal views (entrypoint for Napari)."""
    _get_manager(viewer).show()


def hide_orthogonal_views(viewer: Viewer) -> None:
    """Hide orthogonal views (entrypoint for Napari)."""
    _get_manager(viewer).hide()


def toggle_orthogonal_views(viewer: Viewer) -> None:
    """Toggle orthogonal views"""
    manager = _get_manager(viewer)
    print(manager.is_shown())
    if manager.is_shown():
        manager.hide()
    else:
        manager.show()
