import warnings
from collections.abc import Callable

from napari.components.camera import Camera
from psygnal import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from scipy.spatial.transform import Rotation

from napari_orthogonal_views.attribute_helpers import get_camera, get_grid
from napari_orthogonal_views.ortho_view_widget import (
    OrthoViewWidget,
)

warnings.filterwarnings(
    "ignore", message="Gimbal lock detected", module="napari._vispy.camera"
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
            for (
                checkbox
            ) in self.controls_widget.planes_widget.checkboxes.values():
                checkbox.setChecked(False)
            self.controls_widget.planes_widget.cleanup()
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
        self.planes_widget = PlanesWidget(widgets=widgets)
        self.show_axes = QCheckBox("Show axes")
        self.show_axes.setChecked(True)
        self.camera_widget = CameraWidget(widgets=widgets)
        self.display_mode_widget = DisplayModeWidget(widgets=widgets)

        layout = QVBoxLayout()
        layout.addWidget(self.cross_widget)
        layout.addWidget(self.planes_widget)
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


class PlanesWidget(QWidget):
    """One checkbox per plane, for the last three axes of the dims order. A plane is
    drawn in 3D views, and as a colored canvas border in the 2D view showing it.
    """

    plane_toggled = Signal(
        int, int
    )  # axis (negative numbering), checkbox state

    def __init__(self, widgets: list[OrthoViewWidget]) -> None:
        super().__init__()
        self.setToolTip(
            "Show the planes through the current point: as planes in 3D views, as "
            "a colored border in 2D views"
        )

        # the main viewer dims decide which axes get a checkbox
        self._dims = [widgets[0].viewer.dims] if widgets else []

        # axes (negative numbering) with their plane switched on. Negative numbering
        # stays the same when layers add or remove leading dims.
        self._visible: set[int] = set()
        self._axes: list[int] = []  # the axis of each checkbox slot
        self.checkboxes: dict[int, QCheckBox] = {}  # axis -> checkbox

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        main_checkbox = QCheckBox("Show planes")
        main_checkbox.stateChanged.connect(self._toggle_all)
        layout.addWidget(main_checkbox)
        self._slots = []
        for i in range(3):
            checkbox = QCheckBox()
            checkbox.stateChanged.connect(
                lambda state, i=i: self._on_toggled(i, state)
            )
            self._slots.append(checkbox)
            layout.addWidget(checkbox)
        layout.addStretch()
        self.setLayout(layout)

        if self._dims:
            for event in ("order", "ndim", "axis_labels"):
                getattr(self._dims[0].events, event).connect(self._update_axes)
        self._update_axes()

    def _update_axes(self, event=None) -> None:
        """Assign the checkboxes to the last three axes of the dims order."""

        if not self._dims:
            return
        dims = self._dims[0]
        self._axes = [axis - dims.ndim for axis in dims.order[-3:]]
        self.checkboxes = {}
        for i, checkbox in enumerate(self._slots):
            checkbox.setVisible(i < len(self._axes))
            if i >= len(self._axes):
                continue
            axis = self._axes[i]
            self.checkboxes[axis] = checkbox
            checkbox.setText(str(dims.axis_labels[axis]))
            checkbox.setToolTip(
                f"Plane marking the position along axis {axis}"
            )
            # the planes overlays already know this state, so do not emit
            checkbox.blockSignals(True)
            checkbox.setChecked(axis in self._visible)
            checkbox.blockSignals(False)

    def _on_toggled(self, slot: int, state: int) -> None:
        """Remember the state of the plane and emit it for the axis in this slot."""

        axis = self._axes[slot]
        if state == 2:
            self._visible.add(axis)
        else:
            self._visible.discard(axis)
        self.plane_toggled.emit(axis, state)

    def _toggle_all(self, state: int) -> None:
        """Check or uncheck all the plane checkboxes."""

        for checkbox in self.checkboxes.values():
            checkbox.setChecked(state == 2)

    def cleanup(self) -> None:
        """Disconnect from the viewers, before this widget is removed."""

        if self._dims:
            for event in ("order", "ndim", "axis_labels"):
                getattr(self._dims[0].events, event).disconnect(
                    self._update_axes
                )


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

        def sync_camera(
            w, source_camera: Camera, target_camera: Camera, rotation: Rotation
        ) -> Callable:
            """Match the target camera to the source camera for the zoom level, the center
            (synced axes for 2D, all for 3D), and the viewing angles (3D only). In 3D all
            views share the same camera orientation, but they use rotated angles.

            Args:
                source_camera (Camera): Camera of the ViewerModel emitting the signal
                target_camera (Camera): Camera of the ViewerModel that should update
                rotation (Rotation): camera rotation angles that should be applied to make the
                right ortho view 90 degrees rotated along y, and the bottom orthoview
                widget rotated 90 degrees around x.
            """

            def handler(event=None):
                if w._block_camera:
                    return
                with w._camera_sync_blocked():

                    # sync the zoom level
                    target_camera.zoom = source_camera.zoom

                    # set rotated angles, but only if all views are 3D renderings of the same dimensions
                    shared_3d_order = (
                        w.viewer.dims.ndisplay == 3
                        and w.vm_container.viewer_model.dims.ndisplay == 3
                        and tuple(w.viewer.dims.displayed)
                        == tuple(w.vm_container.viewer_model.dims.displayed)
                    )
                    if (
                        shared_3d_order
                    ):  # all views are 3D and share the same axes, so get the current angles and rotate by 90 degrees along y or z depending on the view.
                        source = Rotation.from_euler(
                            "xyz", source_camera.angles, degrees=True
                        )
                        target = Rotation.from_euler(
                            "xyz", target_camera.angles, degrees=True
                        )
                        new = rotation * source
                        # skip if this is the same rotation already
                        if (new * target.inv()).magnitude() > 1e-6:
                            with warnings.catch_warnings():
                                warnings.filterwarnings(
                                    "ignore", message="Gimbal lock detected"
                                )
                                target_camera.angles = tuple(
                                    new.as_euler("xyz", degrees=True)
                                )

                    # sync the center
                    src_center = list(source_camera.center)
                    tgt_center = list(target_camera.center)
                    for ax in range(3) if shared_3d_order else w.sync_axes:
                        tgt_center[ax] = src_center[ax]
                    target_camera.center = tuple(tgt_center)

            return handler

        def reset_views(
            w: OrthoViewWidget,
            viewer_camera: Camera,
            vm_camera: Camera,
            rotation: Rotation,
        ) -> Callable:
            """Reset the main viewer and the orthogonal view together, whichever
            of the two was reset, and line the orthogonal view up again."""

            line_up = sync_camera(w, viewer_camera, vm_camera, rotation)

            def handler(event):
                if w._block_camera:
                    return
                with w._camera_sync_blocked():
                    if event.source is not w.viewer:
                        w.viewer.reset_view()
                    w.vm_container.viewer_model.reset_view()
                line_up()

            return handler

        for widget in self.widgets:

            viewer_camera = get_camera(widget.viewer)
            vm_camera = get_camera(widget.vm_container.viewer_model)

            # rotation from the main camera to this view's camera in 3D. Euler
            # angles turn about the scene axes (scene x, y, z are data z, y, x)
            if widget.order == (-1, -2, -3):
                # right view: turn about y, to look along x
                offset = Rotation.from_euler("y", -90, degrees=True)
            elif widget.order == (-2, -3, -1):
                # bottom view: turn about (data) x, to look along y
                offset = Rotation.from_euler("z", 90, degrees=True)
            else:
                offset = Rotation.identity()

            forward = sync_camera(widget, viewer_camera, vm_camera, offset)
            reverse = sync_camera(
                widget, vm_camera, viewer_camera, offset.inv()
            )
            reset = reset_views(widget, viewer_camera, vm_camera, offset)

            # immediately sync the current states
            if state == 2:
                forward()

            for name in ("zoom", "angles", "center"):
                # Forward syncing of main viewer to ortho view
                widget.sync_event(
                    getattr(viewer_camera.events, name),
                    forward,
                    state,
                    key_label=f"cam_{name}_viewer_to_vm",
                )
                # Reverse syncing from ortho view to main view
                widget.sync_event(
                    getattr(vm_camera.events, name),
                    reverse,
                    state,
                    key_label=f"cam_{name}_vm_to_viewer",
                )

            # switching to 3D changes the dims order of the orthogonal view, after
            # which the cameras can be lined up again
            widget.sync_event(
                widget.vm_container.viewer_model.dims.events.order,
                forward,
                state,
                key_label="cam_layout_vm_order",
            )

            # resetting one view resets all
            widget.sync_event(
                widget.viewer.events.reset_view,
                reset,
                state,
                key_label="cam_reset_viewer",
            )
            widget.sync_event(
                widget.vm_container.viewer_model.events.reset_view,
                reset,
                state,
                key_label="cam_reset_vm",
            )
