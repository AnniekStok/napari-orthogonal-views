import contextlib
import warnings
import weakref
from collections.abc import Callable

import numpy as np
from napari._vispy.utils.visual import overlay_to_visual
from napari.components.viewer_model import ViewerModel
from napari.layers import Layer
from napari.utils.action_manager import action_manager
from napari.utils.notifications import show_info, show_warning
from napari.viewer import Viewer
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QLayout,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from napari_orthogonal_views.cross_hair_overlay import (
    CrosshairOverlay,
    VispyCrosshairOverlay,
)
from napari_orthogonal_views.ortho_view_widget import (
    OrthoViewWidget,
    activate_on_hover,
)
from napari_orthogonal_views.widget_controls import MainControlsWidget

overlay_to_visual[CrosshairOverlay] = VispyCrosshairOverlay


def center_cross_on_mouse(
    viewer_model: ViewerModel,
):
    """Center the viewer dimension step to the mouse position"""

    if not getattr(viewer_model, "mouse_over_canvas", True):
        show_info(
            "Mouse is not over the canvas. You may need to click on the canvas."
        )
        return

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


def init_actions():
    action_manager.register_action(
        name="napari:move_point",
        command=center_cross_on_mouse,
        description="Move dims point to mouse position",
        keymapprovider=ViewerModel,
    )
    action_manager.bind_shortcut("napari:move_point", "T")


class OrthoViewManager:
    """Replace the main central widget, to allow insertion and removal of orthogonal
    views.

    Behavior:
    Inserts a container (splitter layout) with the original canvas, plus two orthogonal
     views and a controls widget into the same index in the central widget's layout so the
     QMainWindow geometry is preserved.
    """

    def __init__(self, viewer: Viewer):
        self.viewer = viewer
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.main_window = viewer.window._qt_window
        self._central = self.main_window.centralWidget()
        self._container: QWidget | None = None
        self._splitter_handlers: list[tuple[QSplitter, object]] = []
        self._shown = False
        self.sync_filters = None
        self.activate_checkboxes = (
            False  # automatically activate checkboxes when shown
        )
        init_actions()

        # Add crosshairs overlay to main viewer
        cursor_overlay = CrosshairOverlay(
            blending="translucent_no_depth", axis_order=(0, 1, 2)
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.viewer._overlays["crosshairs"] = cursor_overlay

        # make sure the viewer activates on hover
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            activate_on_hover(self.viewer.window.qt_viewer)

        # initialize layer hooks
        self._layer_hooks: dict[type, list[Callable]] = {}

        # get layout of central widget
        central = self.main_window.centralWidget()
        layout: QLayout = central.layout()
        if layout is None or layout.count() == 0:
            raise RuntimeError(
                "Central widget has no layout / widgets to attach to."
            )

        # Find and remove the current canvas widget
        self._original_qt_viewer = layout.itemAt(0).widget()
        self._original_qt_viewer.canvas.native.setMouseTracking(True)
        if self._original_qt_viewer is None:
            raise RuntimeError(
                "Couldn't locate canvas widget in central layout."
            )
        layout.removeWidget(self._original_qt_viewer)

        # widgets holding orthoviews and controls
        self.right_widget = QWidget()  # empty widget placeholder
        self.bottom_widget = QWidget()  # empty widget placeholder

        self.main_controls_widget = MainControlsWidget()
        self.main_controls_widget.show_orth_views.connect(
            self.set_show_orth_views
        )

        # Build orthogonal layout (splitters + widgets)
        self.h_splitter_top = QSplitter(Qt.Horizontal)
        self.h_splitter_top.addWidget(self._original_qt_viewer)
        self.h_splitter_top.addWidget(self.right_widget)

        self.h_splitter_bottom = QSplitter(Qt.Horizontal)
        self.h_splitter_bottom.addWidget(self.bottom_widget)
        self.h_splitter_bottom.addWidget(self.main_controls_widget)

        self.v_splitter = QSplitter(Qt.Vertical)
        self.v_splitter.addWidget(self.h_splitter_top)
        self.v_splitter.addWidget(self.h_splitter_bottom)

        # Sync the two horizontal splitters so user movement mirrors to the other
        def _connect_sync(source: QSplitter, target: QSplitter):
            def handler(*args, **kwargs):
                sizes = source.sizes()
                target.setSizes(sizes)

            source.splitterMoved.connect(handler)
            self._splitter_handlers.append((source, handler))

        _connect_sync(self.h_splitter_top, self.h_splitter_bottom)
        _connect_sync(self.h_splitter_bottom, self.h_splitter_top)

        # insert the container into the original central widget layout at the same
        # position
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(self.v_splitter)
        container.setLayout(container_layout)

        layout.insertWidget(0, container)
        self._set_splitter_sizes(
            0.01, 0.01
        )  # minimal size for right and bottom

        if len(self.viewer.layers) > 0:
            show_warning(
                "Blending of labels layers may not display correctly. You may have to set blending to 'translucent_no_depth' manually for new layers. To ensure correct blending of layers in the main viewer, call OrthoViewManager before adding layers to the viewer."
            )
            for (
                _,
                value,
            ) in self._original_qt_viewer.canvas.layer_to_visual.items():
                value.node.set_gl_state(blend=True, depth_test=False)
            for layer in self.viewer.layers:
                layer.blending = "translucent_no_depth"

        self._container = container

    def set_cross_hairs(self, state: bool = True) -> None:
        """Activate/deactivate the checkbox to set the crosshairs."""

        if not self.is_shown():
            return
        self.main_controls_widget.controls_widget.cross_widget.setChecked(
            state
        )

    def show_cross_hairs(self, state: int) -> None:
        """Show or hide the crosshairs overlay on all viewers"""

        state = state == 2

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.viewer._overlays["crosshairs"].visible = state
            if isinstance(self.right_widget, OrthoViewWidget):
                self.right_widget.vm_container.viewer_model._overlays[
                    "crosshairs"
                ].visible = state
            if isinstance(self.bottom_widget, OrthoViewWidget):
                self.bottom_widget.vm_container.viewer_model._overlays[
                    "crosshairs"
                ].visible = state

    def set_zoom_sync(self, state: bool = True) -> None:
        """Activate the zoom syncing in the controls widget"""
        if not self.is_shown():
            return

        self.main_controls_widget.controls_widget.zoom_widget.setChecked(state)

    def set_center_sync(self, state: bool = True) -> None:
        """Activate the zoom syncing in the controls widget"""
        if not self.is_shown():
            return

        self.main_controls_widget.controls_widget.center_widget.setChecked(
            state
        )

    def register_layer_hook(self, layer_type: type, hook: Callable) -> None:
        """Register a hook to be applied to any matching layer type."""

        self._layer_hooks.setdefault(layer_type, []).append(hook)

    def set_sync_filters(
        self, sync_filters: dict[type[Layer], dict[str, set[str] | str]]
    ) -> None:
        """Provide a dictionary with layer types as keys and dictionaries as values:
        {
            LayerType: {
                "forward_exclude": set[str] | str,
                "reverse_exclude": set[str] | str,
            }
        }
        """

        self.sync_filters = sync_filters

    def is_shown(self) -> bool:
        """Return True if orthoviews are shown."""

        return self._shown

    def set_show_orth_views(self, show: bool) -> None:
        """Show or hide ortho views."""

        if show:
            self.show()
        else:
            self.hide()

    def show(self) -> None:
        """Show ortho views by creating an OrthoViewWidget for two orthogonal views and
        assign them to the central widget. Also show sync controls widget."""

        # Ensure checkbox is checked and return early if orth views are shown already.
        self.main_controls_widget.show_checkbox.blockSignals(True)
        self.main_controls_widget.show_checkbox.setChecked(True)
        self.main_controls_widget.show_checkbox.blockSignals(False)
        if self._shown:
            return

        # Replace right widget with OrthoViewWidget
        new_right = OrthoViewWidget(
            self.viewer,
            order=(-1, -2, -3),
            sync_axes=[1],
            sync_filters=self.sync_filters,
            layer_hooks=self._layer_hooks,
        )

        old_right = self.right_widget
        idx = self.h_splitter_top.indexOf(old_right)
        self.h_splitter_top.replaceWidget(idx, new_right)
        self.right_widget = new_right
        old_right.deleteLater()

        # Replace bottom widget with OrthoViewWidget
        new_bottom = OrthoViewWidget(
            self.viewer,
            order=(-2, -3, -1),
            sync_axes=[2],
            sync_filters=self.sync_filters,
            layer_hooks=self._layer_hooks,
        )
        old_bottom = self.bottom_widget
        idx = self.h_splitter_bottom.indexOf(old_bottom)
        self.h_splitter_bottom.replaceWidget(idx, new_bottom)
        self.bottom_widget = new_bottom
        old_bottom.deleteLater()

        # Add controls to main_controls widget
        self.main_controls_widget.add_controls(
            widgets=[self.right_widget, self.bottom_widget]
        )
        self.main_controls_widget.controls_widget.cross_widget.stateChanged.connect(
            self.show_cross_hairs
        )

        # assign 30% of window width and height to orth views
        self._set_splitter_sizes(0.3, 0.3)

        self._shown = True

        # activate checkboxes by default
        if self.activate_checkboxes:
            self.set_cross_hairs(True)
            self.set_zoom_sync(True)
            self.set_center_sync(True)

    def hide(self) -> None:
        """Remove the OrthoViewWidgets and replace with empty QWidget placeholders. Make
        sure that all signals are cleaned up and the main canvas is expanded back.
        """

        self.main_controls_widget.show_checkbox.blockSignals(True)
        self.main_controls_widget.show_checkbox.setChecked(False)
        self.main_controls_widget.show_checkbox.blockSignals(False)

        if not self._shown:
            return

        if isinstance(self.right_widget, OrthoViewWidget):
            self.right_widget.cleanup()
        if isinstance(self.bottom_widget, OrthoViewWidget):
            self.bottom_widget.cleanup()

        # Replace right widget
        new_right = QWidget()
        old_right = self.right_widget
        idx = self.h_splitter_top.indexOf(old_right)
        self.h_splitter_top.replaceWidget(idx, new_right)
        self.right_widget = new_right
        old_right.deleteLater()

        # Replace bottom widget
        new_bottom = QWidget()
        old_bottom = self.bottom_widget
        idx = self.h_splitter_bottom.indexOf(old_bottom)
        self.h_splitter_bottom.replaceWidget(idx, new_bottom)
        self.bottom_widget = new_bottom
        old_bottom.deleteLater()

        # Removes controls and resize widgets.
        self.main_controls_widget.remove_controls()
        self._set_splitter_sizes(
            0.01, 0.01
        )  # minimal size for right and bottom

        # remove axis labels
        self.viewer.axes.visible = False

        self._shown = False

    def _set_splitter_sizes(
        self, side_fraction: float, bottom_fraction: float
    ) -> None:
        """Adjust the size of the right and bottom part of the splitters."""

        central = self._central

        central_width = max(100, central.width())
        central_height = max(100, central.height())
        side_width = max(1, int(central_width * side_fraction))
        bottom_height = max(1, int(central_height * bottom_fraction))

        self.h_splitter_top.setSizes([central_width - side_width, side_width])
        self.h_splitter_bottom.setSizes(
            [central_width - side_width, side_width]
        )
        self.v_splitter.setSizes(
            [central_height - bottom_height, bottom_height]
        )

    def cleanup(self) -> None:
        """Restore original layout and free all widgets."""

        # first hide to cleanup all connections
        self.hide()

        # remove the widgets and restore original canvas
        if self._container is not None:
            layout = self._central.layout()
            if layout is not None:
                layout.removeWidget(self._container)
                self._container.deleteLater()
                self._container = None

            # Put the original canvas back
            if self._original_qt_viewer is not None:
                layout.insertWidget(0, self._original_qt_viewer)

        # Disconnect all splitter signal handlers
        for splitter, handler in self._splitter_handlers:
            with contextlib.suppress(TypeError, RuntimeError):
                splitter.splitterMoved.disconnect(handler)
        self._splitter_handlers.clear()

        # Delete the extra widgets if still there
        for w in (
            self.right_widget,
            self.bottom_widget,
            self.main_controls_widget,
            getattr(self, "h_splitter_top", None),
            getattr(self, "h_splitter_bottom", None),
            getattr(self, "v_splitter", None),
        ):
            if w is not None:
                w.deleteLater()

        # Drop reference from the global dict to avoid leaks
        _VIEWER_MANAGERS.pop(self.viewer, None)


# Module-level helpers for napari.yaml entrypoints
_VIEWER_MANAGERS = weakref.WeakKeyDictionary()


def _get_manager(viewer: Viewer) -> OrthoViewManager:
    """Return reference to OrthoViewManager"""

    if viewer not in _VIEWER_MANAGERS:
        _VIEWER_MANAGERS[viewer] = OrthoViewManager(viewer)
    return _VIEWER_MANAGERS[viewer]


def show_orthogonal_views(viewer: Viewer) -> None:
    """Show orthogonal views (entrypoint for Napari)."""

    m = _get_manager(viewer)
    QTimer.singleShot(0, m.show)


def hide_orthogonal_views(viewer: Viewer) -> None:
    """Hide orthogonal views (entrypoint for Napari)."""

    m = _get_manager(viewer)
    QTimer.singleShot(0, m.hide)


def toggle_orthogonal_views(viewer: Viewer) -> None:
    """Toggle orthogonal views"""

    m = _get_manager(viewer)
    if m.is_shown():
        QTimer.singleShot(0, m.hide)
    else:
        QTimer.singleShot(0, m.show)


def delete_and_cleanup(viewer: Viewer) -> None:
    """Remove orthoview manager and clean up all connections"""

    m = _get_manager(viewer)
    m.cleanup()
