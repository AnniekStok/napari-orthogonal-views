from psygnal import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from napari_orthogonal_views.ortho_view_widget import (
    OrthoViewWidget,
)


class MainControlsWidget(QWidget):
    """Main controls widget to turn orthogonal views on or off"""

    show_orth_views = Signal(bool)

    def __init__(self):
        super().__init__()

        self.show_checkbox = QCheckBox("Show orthogonal views")
        self.show_checkbox.stateChanged.connect(self.set_show_views)
        self.controls_widget = QWidget()

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.main_layout.addWidget(self.show_checkbox)
        self.main_layout.addWidget(self.controls_widget)
        self.main_layout.addStretch()

        self.setLayout(self.main_layout)

        # Allow this widget to shrink to 0 height
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_show_views(self, state: bool) -> None:
        """Emit signal to show/hide orth views"""

        self.show_orth_views.emit(state)

    def add_controls(self, widgets: list[OrthoViewWidget]) -> None:
        """Add a ControlsWidget with additional controls"""

        old_widget = self.controls_widget
        self.controls_widget = ControlsWidget(widgets=widgets)
        self.main_layout.replaceWidget(old_widget, self.controls_widget)
        self.adjustSize()

    def remove_controls(self) -> None:
        """Remove ControlsWidget from the layout"""

        if isinstance(self.controls_widget, ControlsWidget):
            self.controls_widget.cross_widget.setChecked(False)
            self.controls_widget.show_axes.setChecked(False)
        old_widget = self.controls_widget
        self.controls_widget = QWidget()
        self.main_layout.replaceWidget(old_widget, self.controls_widget)
        old_widget.deleteLater()
        self.adjustSize()


class ControlsWidget(QWidget):
    """QWidget holding QCheckboxes for crosshairs, and syncing of zoom and camera center."""

    def __init__(self, widgets: list[OrthoViewWidget]):
        super().__init__()

        self.cross_widget = QCheckBox("Show cross hairs")
        self.show_axes = QCheckBox("Show axes")
        self.show_axes.setChecked(True)
        self.zoom_widget = ZoomWidget(widgets=widgets)
        self.center_widget = CenterWidget(widgets=widgets)
        self.grid_widget = GridWidget(widgets=widgets)

        layout = QVBoxLayout()
        layout.addWidget(self.cross_widget)
        layout.addWidget(self.show_axes)
        layout.addWidget(self.zoom_widget)
        layout.addWidget(self.center_widget)
        layout.addWidget(self.grid_widget)
        label = QLabel("Press T to center view on mouse")
        label.setWordWrap(True)
        font = label.font()
        font.setItalic(True)
        label.setFont(font)
        layout.addWidget(label)
        self.setLayout(layout)


class GridWidget(QCheckBox):
    """Checkbox to sync/unsync grid view"""

    def __init__(self, widgets: list[QWidget]):
        super().__init__("Sync grid")
        self.widgets = widgets
        self.stateChanged.connect(self.set_grid_sync)

        # internal guard to prevent recursion
        self._syncing = False

    @staticmethod
    def _copy_grid(src, dst):
        """Copy all relevant grid properties."""
        dst.enabled = src.enabled
        dst.shape = getattr(src, "shape", dst.shape)
        dst.stride = getattr(src, "stride", dst.stride)
        dst.spacing = getattr(src, "spacing", getattr(dst, "spacing", None))

    def _viewer_to_vm(self, widget, _event=None):
        if self._syncing:
            return
        self._syncing = True
        try:
            self._copy_grid(
                widget.viewer.grid,
                widget.vm_container.viewer_model.grid,
            )
        finally:
            self._syncing = False

    def _vm_to_viewer(self, widget, _event=None):
        if self._syncing:
            return
        self._syncing = True
        try:
            self._copy_grid(
                widget.vm_container.viewer_model.grid,
                widget.viewer.grid,
            )
        finally:
            self._syncing = False

    def set_grid_sync(self, state: int) -> None:
        """Enable/disable grid sync across all widgets."""

        sync = state == 2

        for widget in self.widgets:

            # push initial state when enabling
            if sync:
                self._copy_grid(
                    widget.viewer.grid,
                    widget.vm_container.viewer_model.grid,
                )

            # viewer -> vm
            widget.sync_event(
                widget.viewer.grid.events,
                lambda e, w=widget: self._viewer_to_vm(w, e),
                sync,
                key_label="grid_viewer_to_vm",
            )

            # vm -> viewer
            widget.sync_event(
                widget.vm_container.viewer_model.grid.events,
                lambda e, w=widget: self._vm_to_viewer(w, e),
                sync,
                key_label="grid_vm_to_viewer",
            )


class ZoomWidget(QCheckBox):
    """Checkbox to sync/unsync camera zoom"""

    def __init__(self, widgets=list[QWidget]):
        super().__init__("Sync zoom")
        self.widgets = widgets
        self.stateChanged.connect(self.set_zoom_sync)

    def set_zoom_sync(self, state: bool) -> None:
        """Connect or disconnect camera zoom syncing on each of the ortho view widgets."""

        for widget in self.widgets:

            if state == 2:
                widget.vm_container.viewer_model.camera.zoom = (
                    widget.viewer.camera.zoom
                )

            # main viewer to ortho view
            widget.sync_event(
                widget.viewer.camera.events.zoom,
                lambda e, w=widget: setattr(
                    w.vm_container.viewer_model.camera,
                    "zoom",
                    w.viewer.camera.zoom,
                ),
                state,
                key_label="zoom_viewer_to_vm",
            )

            # Reverse sync from ortho view to main view
            widget.sync_event(
                widget.vm_container.viewer_model.camera.events.zoom,
                lambda e, w=widget: setattr(
                    w.viewer.camera,
                    "zoom",
                    w.vm_container.viewer_model.camera.zoom,
                ),
                state,
                key_label="zoom_vm_to_viewer",
            )


class CenterWidget(QCheckBox):
    """Checkbox to sync/unsync camera center for specific axes"""

    def __init__(self, widgets=list[QWidget]):
        super().__init__("Sync center")
        self.widgets = widgets
        self.stateChanged.connect(self.set_center_sync)

    def set_center_sync(self, state: bool) -> None:
        """Connect or disconnect camera center syncing on each of the ortho view widgets."""

        for widget in self.widgets:

            # create handler to sync specific axis
            def make_handler(w, source_viewer, target_viewer):
                def handler(event=None):
                    if w._block_center:
                        return
                    w._block_center = True
                    try:
                        src_center = list(source_viewer.camera.center)
                        tgt_center = list(target_viewer.camera.center)
                        for ax in w.sync_axes:
                            # to ensure cross hairs are aligned
                            tgt_center[ax] = src_center[ax]
                        target_viewer.camera.center = tuple(tgt_center)
                    finally:
                        w._block_center = False

                return handler

            # Forward sync
            widget.sync_event(
                widget.viewer.camera.events.center,
                make_handler(
                    widget,
                    widget.viewer,
                    widget.vm_container.viewer_model,
                ),
                state,
                key_label=f"center_viewer_to_vm_{id(widget)}",
            )

            # Reverse sync
            widget.sync_event(
                widget.vm_container.viewer_model.camera.events.center,
                make_handler(
                    widget,
                    widget.vm_container.viewer_model,
                    widget.viewer,
                ),
                state,
                key_label=f"center_vm_to_viewer_{id(widget)}",
            )

            if state == 2:
                # Align camera centers immediately
                viewer_center = list(widget.viewer.camera.center)
                viewer_center_reordered = [
                    viewer_center[i] for i in widget.order
                ]
                widget_center = list(
                    widget.vm_container.viewer_model.camera.center
                )
                widget_center[-2] = viewer_center_reordered[-2]
                widget_center[-1] = viewer_center_reordered[-1]
                widget.vm_container.viewer_model.camera.center = widget_center
