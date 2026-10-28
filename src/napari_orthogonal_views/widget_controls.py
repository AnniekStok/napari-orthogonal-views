from psygnal import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from napari_orthogonal_views.attribute_helpers import get_camera, get_grid
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
        self.camera_widget = CameraWidget(widgets=widgets)
        self.display_mode_widget = DisplayModeWidget(widgets=widgets)

        layout = QVBoxLayout()
        layout.addWidget(self.cross_widget)
        layout.addWidget(self.show_axes)
        layout.addWidget(self.camera_widget)
        layout.addWidget(self.display_mode_widget)
        label = QLabel("Press T to center view on mouse")
        label.setWordWrap(True)
        font = label.font()
        font.setItalic(True)
        label.setFont(font)
        layout.addWidget(label)
        self.setLayout(layout)


class DisplayModeWidget(QCheckBox):
    """Checkbox to sync/unsync display modes: 2D/3D view and grid view"""

    def __init__(self, widgets: list[OrthoViewWidget]) -> None:
        super().__init__("Sync display mode")
        self.setToolTip(
            "Sync the display mode (2D/3D view, grid view) between different views"
        )
        self.widgets = widgets
        self.stateChanged.connect(self.set_display_sync)

    @staticmethod
    def _copy_grid(src, dst) -> None:
        """Copy all relevant grid properties."""

        dst.enabled = src.enabled
        dst.shape = getattr(src, "shape", dst.shape)
        dst.stride = getattr(src, "stride", dst.stride)
        dst.spacing = getattr(src, "spacing", getattr(dst, "spacing", None))

    def _viewer_to_vm(self, widget: OrthoViewWidget, _event=None) -> None:
        """Sync the viewer grid to an ortho view. Grid syncing involves multiple properties, use a flag to prevent retriggering new syncing before all properties are synced"""

        if getattr(widget, "_grid_syncing", False):
            return
        widget._grid_syncing = True
        try:
            self._copy_grid(
                get_grid(widget.viewer),
                get_grid(widget.vm_container.viewer_model),
            )
        finally:
            widget._grid_syncing = False

    def _vm_to_viewer(self, widget: OrthoViewWidget, _event=None) -> None:
        """Sync the ortho view grid to the viewer grid. Grid syncing involves multiple properties, use a flag to prevent retriggering new syncing before all properties are synced"""

        if getattr(widget, "_grid_syncing", False):
            return
        widget._grid_syncing = True
        try:
            self._copy_grid(
                get_grid(widget.vm_container.viewer_model),
                get_grid(widget.viewer),
            )
        finally:
            widget._grid_syncing = False

    def set_display_sync(self, state: int) -> None:
        """Enable/disable display syncing across all widgets."""

        for widget in self.widgets:

            # push initial state when enabling
            if state == 2:
                self._copy_grid(
                    get_grid(widget.viewer),
                    get_grid(widget.vm_container.viewer_model),
                )
                widget.vm_container.viewer_model.dims.ndisplay = (
                    widget.viewer.dims.ndisplay
                )

            # forward syncing viewer -> vm
            # grid
            widget.sync_event(
                get_grid(widget.viewer).events,
                lambda e, w=widget: self._viewer_to_vm(w, e),
                state,
                key_label="grid_viewer_to_vm",
            )

            # sync display
            widget.sync_event(
                widget.viewer.dims.events.ndisplay,
                lambda e, src=widget.viewer.dims, dst=widget.vm_container.viewer_model.dims: setattr(
                    dst, "ndisplay", src.ndisplay
                ),
                state,
                key_label="ndisplay_viewer_to_vm",
            )

            # reverse syncing vm -> viewer
            # grid
            widget.sync_event(
                get_grid(widget.vm_container.viewer_model).events,
                lambda e, w=widget: self._vm_to_viewer(w, e),
                state,
                key_label="grid_vm_to_viewer",
            )

            # sync display
            widget.sync_event(
                widget.vm_container.viewer_model.dims.events.ndisplay,
                lambda e, src=widget.vm_container.viewer_model.dims, dst=widget.viewer.dims: setattr(
                    dst, "ndisplay", src.ndisplay
                ),
                state,
                key_label="ndisplay_vm_to_viewer",
            )


class CameraWidget(QCheckBox):
    """Checkbox to sync/unsync camera zoom, angles, and center"""

    def __init__(self, widgets=list[OrthoViewWidget]):
        super().__init__("Sync camera")
        self.setToolTip(
            "Sync the camera zoom, angles, and center (of displayed axes) between different views"
        )
        self.widgets = widgets
        self.stateChanged.connect(self.set_camera_sync)

    def set_camera_sync(self, state: bool) -> None:
        """Connect or disconnect camera zoom, center, and angle syncing on each of the ortho view widgets."""

        for widget in self.widgets:

            viewer_camera = get_camera(widget.viewer)
            vm_camera = get_camera(widget.vm_container.viewer_model)

            # immediately sync the current states
            if state == 2:
                vm_camera.zoom = viewer_camera.zoom
                vm_camera.angles = viewer_camera.angles
                viewer_center = list(viewer_camera.center)
                vm_center = list(vm_camera.center)
                for axis in widget.sync_axes:
                    vm_center[axis] = viewer_center[axis]
                vm_camera.center = vm_center

            # create handler to sync specific axes
            def make_handler(w, source_camera, target_camera):
                def handler(event=None):
                    if w._block_center:
                        return
                    w._block_center = True
                    try:
                        src_center = list(source_camera.center)
                        tgt_center = list(target_camera.center)
                        for ax in w.sync_axes:
                            # to ensure cross hairs are aligned
                            tgt_center[ax] = src_center[ax]
                        target_camera.center = tuple(tgt_center)
                    finally:
                        w._block_center = False

                return handler

            # Forward syncing of main viewer to ortho view
            # zoom
            widget.sync_event(
                viewer_camera.events.zoom,
                lambda e, src=viewer_camera, dst=vm_camera: setattr(
                    dst, "zoom", src.zoom
                ),
                state,
                key_label="cam_zoom_viewer_to_vm",
            )
            # angles
            widget.sync_event(
                viewer_camera.events.angles,
                lambda e, src=viewer_camera, dst=vm_camera: setattr(
                    dst, "angles", src.angles
                ),
                state,
                key_label="cam_angles_viewer_to_vm",
            )

            # center (viewed axes only)
            widget.sync_event(
                viewer_camera.events.center,
                make_handler(widget, viewer_camera, vm_camera),
                state,
                key_label=f"cam_center_viewer_to_vm_{id(widget)}",
            )

            # Reverse syncing from ortho view to main view
            # zoom
            widget.sync_event(
                vm_camera.events.zoom,
                lambda e, src=vm_camera, dst=viewer_camera: setattr(
                    dst, "zoom", src.zoom
                ),
                state,
                key_label="cam_zoom_vm_to_viewer",
            )
            # angles
            widget.sync_event(
                vm_camera.events.angles,
                lambda e, src=vm_camera, dst=viewer_camera: setattr(
                    dst, "angles", src.angles
                ),
                state,
                key_label="cam_angles_vm_to_viewer",
            )

            # center (viewed axes only)
            widget.sync_event(
                vm_camera.events.center,
                make_handler(widget, vm_camera, viewer_camera),
                state,
                key_label=f"cam_center_vm_to_viewer_{id(widget)}",
            )
