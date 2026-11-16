"""Show orthoslices and per-axis clipping ranges on a 3D rendered layer.

'Full volume' shows the default full volume rendering of the layer.
'Slice' shows a clipping plane pair per axis, defining a single plane (unlike the default napari plane view).
In this mode the Union is drawn, so you can see the plane for each activated axis simultaneously. This requires changing the vispy shader.
'Range' crops the volume according to all activated clipping plane pairs, so you see the intersection of these clipping ranges.
The settings are stored per layer in the metadata ('orthoslices'), and are restored when switching between the different modes.
"""

from collections.abc import Callable

import numpy as np
from napari._qt.qt_resources import QColoredSVGIcon
from napari.components.viewer_model import ViewerModel
from napari.layers import Layer
from napari.layers.utils.plane import ClippingPlane
from psygnal import Signal
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from superqt import QLabeledRangeSlider, QLabeledSlider

from napari_orthogonal_views.cross_hair_overlay import Crosshairs

VOLUME, SLICE, RANGE = "Full volume", "Slice", "Range"
MODE_TOOLTIPS = {
    VOLUME: "Show the full volume of the active layer (no slicing)",
    SLICE: "Show a single slice per checked axis (orthoslices)",
    RANGE: "Crop the volume to a range per checked axis",
}

# Vispy trickery of the rendering, to enable showing the union of the clipping planes instead of the intersection when in 'slice' mode.
# The default function takes the minimum of the distance to all planes, which is the intersection of the slabs.
# It can be replaced by a customized function that takes the maximum of the minimums of consecutive pairs of planes, which is the union of the slabs.

# modified version from vispy.visuals.volume. A volume is drawn by ray casting with sampling.
# The function returns a value that is ≥0 if the sample is kept, and <0 if it should be skipped (sample is outside clipping range).
_UNION_TEMPLATE = """
    float clip_planes(vec3 loc, vec3 vol_shape) {{
        vec3 loc_transf = $clip_transform(vec4(loc * vol_shape, 1)).xyz;
        float distance_from_clip = {start};
        {slabs}
        return distance_from_clip;
    }}
"""

_SLAB_TEMPLATE = """
        distance_from_clip = max(distance_from_clip, min(
            dot(loc_transf - $clipping_plane_pos{a}, $clipping_plane_norm{a}),
            dot(loc_transf - $clipping_plane_pos{b}, $clipping_plane_norm{b})));
"""


def _union_of_slabs_glsl(n_planes: int) -> str:
    """Clipping function drawing the union of slabs, from consecutive pairs of planes."""

    if n_planes == 0:
        return _UNION_TEMPLATE.format(
            start="3.4e38", slabs=""
        )  # means clip nothing

    slabs = "".join(
        _SLAB_TEMPLATE.format(a=i, b=min(i + 1, n_planes - 1))
        for i in range(0, n_planes, 2)
    )
    return _UNION_TEMPLATE.format(
        start="-3.4e38", slabs=slabs
    )  # start from nothing inside, then add the slabs


def set_slab_union(volume_node, union: bool) -> None:
    """Switch a vispy volume node between intersecting its clipping planes (default) and
    drawing the union of the slabs formed by pairs of planes."""

    is_union = "_build_clipping_planes_glsl" in volume_node.__dict__
    if union == is_union:
        return  # do not recompile shader if already in the 'union' mode.
    volume_node.unfreeze()  # unfreeze to allow changing the shader function
    if union:
        volume_node._build_clipping_planes_glsl = _union_of_slabs_glsl
    else:
        del (
            volume_node._build_clipping_planes_glsl
        )  # delete the custom function to revert to default
    volume_node.freeze()
    # rebuild the shader function with the current planes
    volume_node.clipping_planes = volume_node.clipping_planes


def layer_state(layer: Layer) -> dict:
    """The orthoslice settings of a layer: mode, and per axis (negative numbering)
    whether it is on in Slice and in Range mode, and its range, in dims steps. Slices
    always follow the current point."""

    # default setting at startup will be volume mode without any plane clipping
    return layer.metadata.setdefault(
        "orthoslices", {"mode": VOLUME, "planes": {}}
    )


def axis_on(axis_state: dict, mode: str) -> bool:
    """The state of the different axes is stored per mode. Return whether the axis is
    activated for the given mode (not volume)."""

    return mode != VOLUME and axis_state["on"].get(mode, True)


class OrthosliceWidget(QWidget):
    """Controls for ortho slicing. The user can choose between full volume rendering,
    slice views (union), and range views (clipping planes, intersection). For slice and
    range views, each axis can be turned on individually. A box overlay is optional for
    the range view."""

    # (axis, low, high) in world coordinates, per axis cropped by the active layer
    boxes_changed = Signal(tuple)

    def __init__(
        self,
        viewers: list[ViewerModel],
        volume_nodes: Callable[[Layer], list],
    ) -> None:
        super().__init__()

        self.viewer = viewers[
            0
        ]  # the main viewer, owning the layers and the dims
        self._viewers = viewers  # main viewer and orthogonal views
        self._volume_nodes = volume_nodes  # nodes of a layer and of its copies
        self.layer = None
        self._axes: list[int] = []  # the 3D displayed axes, negative numbering
        self._hidden_boxes: set[int] = set()  # axes whose box is hidden

        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        mode_row = QHBoxLayout()
        for mode, tooltip in MODE_TOOLTIPS.items():
            button = QRadioButton(mode)
            button.setToolTip(tooltip)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            mode_row.addWidget(button)
        mode_row.addStretch()
        self.mode_buttons[VOLUME].setChecked(True)
        self.mode_group.buttonClicked.connect(self._store_and_apply)

        # one row per displayed axis: checkbox, slice slider, range slider and a
        # button to show/hide the box of the range
        self.checkboxes, self.sliders, self.range_sliders = [], [], []
        self.box_buttons: list[QToolButton] = []
        rows = QVBoxLayout()
        for i in range(3):
            checkbox = QCheckBox()
            checkbox.setMinimumWidth(40)
            checkbox.toggled.connect(self._store_and_apply)
            slider = QLabeledSlider(Qt.Horizontal)
            slider.valueChanged.connect(self._on_slider)
            range_slider = QLabeledRangeSlider(Qt.Horizontal)
            range_slider.valueChanged.connect(self._store_and_apply)
            box_button = QToolButton()
            box_button.setCheckable(True)
            box_button.setChecked(True)
            box_button.setToolTip(
                "Toggle visibility of the box for this range"
            )
            box_button.toggled.connect(
                lambda shown, i=i: self._on_box_button(i, shown)
            )
            self._set_box_icon(box_button)
            row = QHBoxLayout()
            for widget in (checkbox, slider, range_slider, box_button):
                row.addWidget(widget, alignment=Qt.AlignmentFlag.AlignBottom)
            rows.addLayout(row)
            self.checkboxes.append(checkbox)
            self.sliders.append(slider)
            self.range_sliders.append(range_slider)
            self.box_buttons.append(box_button)
        self._inputs = [
            *self.mode_buttons.values(),
            *self.checkboxes,
            *self.sliders,
            *self.range_sliders,
        ]

        self.message = QLabel()
        self.message.setWordWrap(True)
        font = self.message.font()
        font.setItalic(True)
        self.message.setFont(font)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addLayout(rows)
        layout.addWidget(self.message)
        layout.addStretch()

        dims = self.viewer.dims

        # gather all connections related to the viewer and its dims, so they can be disconnected on cleanup.
        self._connections = []

        # dims events (order, ndim, axis labels, and range) should trigger axis updates
        self._connections.extend(
            [
                *(
                    (getattr(dims.events, name), self._update_axes)
                    for name in ("order", "ndim", "axis_labels", "range")
                )
            ]
        )

        # current step updates should trigger the slice sliders and planes
        self._connections.extend(
            [(dims.events.current_step, self._on_current_step)]
        )

        # active layer changes should trigger the widget to show the new layer's settings
        self._connections.extend(
            [
                (
                    self.viewer.layers.selection.events.active,
                    self._on_active_layer,
                )
            ]
        )

        # ndisplay changes in any of the viewers should enable/disable plane sliders.
        self._connections.extend(
            [
                *(
                    (v.dims.events.ndisplay, self._on_active_layer)
                    for v in viewers
                )
            ]
        )

        # connect all handlers
        for emitter, handler in self._connections:
            emitter.connect(handler)

        self._update_axes()
        self._on_active_layer()

    def _on_active_layer(self, event=None) -> None:
        """Show the settings of the active layer, if all conditions are met."""

        layer = self.viewer.layers.selection.active
        in_3d = any(viewer.dims.ndisplay == 3 for viewer in self._viewers)
        ok = (
            in_3d
            and layer is not None
            and hasattr(layer, "experimental_clipping_planes")
            and layer.ndim >= 3
            and len(self._volume_nodes(layer)) > 0
        )
        self.layer = layer if ok else None

        if not in_3d:
            text = "Switch one of the views to 3D to use orthoslices."
        elif self.layer is None:
            text = "Select an image or labels layer with 3 or more dims."
        else:
            text = f"Settings of '{self.layer.name}'."
        self.message.setText(text)
        for button in self.mode_buttons.values():
            button.setEnabled(ok)
        self._load()

    def _update_axes(self, event=None) -> None:
        """Assign the rows to the last three axes of the dims order, which are the
        displayed axes in 3D, in all views. This ensures that the widget displays the
        correct labels and total span (world)."""

        dims = self.viewer.dims
        self._axes = [axis - dims.ndim for axis in dims.order[-3:]]
        for axis, checkbox, slider, range_slider in zip(
            self._axes,
            self.checkboxes,
            self.sliders,
            self.range_sliders,
            strict=False,
        ):
            checkbox.setText(str(dims.axis_labels[axis]))
            r, g, b, _ = (Crosshairs.axis_color(axis) * 255).astype(int)
            checkbox.setStyleSheet(f"color: rgb({r}, {g}, {b});")
            for widget in (slider, range_slider):
                widget.blockSignals(True)
                widget.setRange(0, int(dims.nsteps[axis]) - 1)
                widget.blockSignals(False)
        for axis, box_button in zip(
            self._axes, self.box_buttons, strict=False
        ):
            box_button.blockSignals(True)
            box_button.setChecked(axis not in self._hidden_boxes)
            box_button.blockSignals(False)
            self._set_box_icon(box_button)
        self._load()

    def _set_box_icon(self, box_button: QToolButton) -> None:
        """An open eye while the box is shown, a crossed out eye while hidden."""

        name = "visibility" if box_button.isChecked() else "visibility_off"
        icon = QColoredSVGIcon.from_resources(name)
        box_button.setIcon(icon.colored(theme=self.viewer.theme))

    def _on_box_button(self, slot: int, shown: bool) -> None:
        """Show or hide the box of the axis in this row."""

        axis = self._axes[slot]
        if shown:
            self._hidden_boxes.discard(axis)
        else:
            self._hidden_boxes.add(axis)
        self._set_box_icon(self.box_buttons[slot])
        self._emit_boxes()

    def _mode(self) -> str:
        """Return the selected button text (Full volume, Slice, Range)."""
        return self.mode_group.checkedButton().text()

    def _axis_state(self, layer: Layer, axis: int) -> dict:
        """The settings of one axis of a layer: on per mode (unset until the mode is
        first used), and its range, starting at the full range."""

        top = (
            int(self.viewer.dims.nsteps[axis]) - 1
        )  # full range is used if not set yet
        return layer_state(layer)["planes"].setdefault(
            axis, {"on": {}, "range": (0, top)}
        )

    def _load(self) -> None:
        """Show the settings of the active layer in the widgets (without applying, the layer is already configured)."""

        for widget in self._inputs:
            widget.blockSignals(True)
        if self.layer is not None:
            mode = layer_state(self.layer)["mode"]
            self.mode_buttons[mode].setChecked(
                True
            )  # make sure that button state corresponds to layer state
            for i, axis in enumerate(self._axes):
                axis_state = self._axis_state(self.layer, axis)
                self.checkboxes[i].setChecked(axis_on(axis_state, mode))
                self.range_sliders[i].setValue(axis_state["range"])
        for axis, slider in zip(self._axes, self.sliders, strict=False):
            slider.setValue(self.viewer.dims.current_step[axis])
        for widget in self._inputs:
            widget.blockSignals(False)

        mode = self._mode()
        for i, (checkbox, slider, range_slider, box_button) in enumerate(
            zip(
                self.checkboxes,
                self.sliders,
                self.range_sliders,
                self.box_buttons,
                strict=True,
            )
        ):
            used = i < len(self._axes)
            checkbox.setVisible(used and mode != VOLUME)
            slider.setVisible(used and mode == SLICE)
            range_slider.setVisible(used and mode == RANGE)
            box_button.setVisible(used and mode == RANGE)
            checkbox.setEnabled(self.layer is not None)
            active = self.layer is not None and checkbox.isChecked()
            slider.setEnabled(active)
            range_slider.setEnabled(active)
            box_button.setEnabled(active)
        self._emit_boxes()

    def _store(self) -> None:
        """Write the widgets to the settings of the active layer."""

        state = layer_state(self.layer)
        previous, mode = state["mode"], self._mode()
        state["mode"] = mode
        for i, axis in enumerate(self._axes):
            axis_state = self._axis_state(self.layer, axis)
            axis_state["range"] = tuple(self.range_sliders[i].value())
            if mode == previous and mode != VOLUME:
                axis_state["on"][mode] = self.checkboxes[i].isChecked()

    def _store_and_apply(self, value=None) -> None:
        """Store the widget settings to the active layer, and apply the changes to the layer, and ensure the settings are correctly displayed in the widgets."""

        if self.layer is None:
            return
        self._store()
        self.apply(self.layer)
        self._load()

    def _on_slider(self, value=None) -> None:
        """Read the values from the current sliders, and use it to move the current point via viewer.dims.current_step"""

        step = list(self.viewer.dims.current_step)
        for axis, slider in zip(self._axes, self.sliders, strict=False):
            step[axis] = slider.value()
        self.viewer.dims.current_step = step

    def _on_current_step(self, event=None) -> None:
        """Move the slices of every layer in Slice mode to the current point."""

        for layer in self.viewer.layers:
            state = layer.metadata.get("orthoslices")
            if state is not None and state["mode"] == SLICE:
                self.apply(layer)
        self._load()

    def apply(self, layer: Layer) -> None:
        """Write one pair of clipping planes per checked axis to a layer."""

        slice_mode = layer_state(layer)["mode"] == SLICE
        for node in self._volume_nodes(layer):
            set_slab_union(node, slice_mode)  # turn union mode on or off.

        # the plane vectors follow the 3D displayed axes, in world units
        planes = []
        for axis, (low, high) in self._ranges(layer).items():
            normal = np.zeros(3)
            normal[self._axes.index(axis)] = 1
            planes.append(ClippingPlane(position=low * normal, normal=normal))
            planes.append(
                ClippingPlane(position=high * normal, normal=-normal)
            )

        if (
            slice_mode
            and planes
            and getattr(layer, "depiction", None) == "plane"
        ):
            # plane depiction only samples a single plane, the slabs need the volume
            layer.depiction = "volume"

        layer.experimental_clipping_planes = planes  # an empty list (volume, or all axes turned off, means no clipping)

    def _ranges(self, layer: Layer) -> dict[int, tuple[float, float]]:
        """World range per checked axis of a layer. A slice extends half a step to
        either side, so it is one slice thick. Since all dims are synced, the slice position
        can be read directly from the viewer.dims.current_step. The range is read from the
        range sliders."""

        dims = self.viewer.dims
        mode = layer_state(layer)["mode"]
        ranges = {}
        for axis in self._axes:
            axis_state = self._axis_state(layer, axis)
            if not axis_on(
                axis_state, mode
            ):  # volume mode is skipped, as well as any axis that is turned off
                continue
            if mode == SLICE:
                low = high = dims.current_step[
                    axis
                ]  # low and high are the same, read from current_step
            else:
                low, high = axis_state[
                    "range"
                ]  # read the axis state for the current layer.
            start, _, step = dims.range[axis]
            ranges[axis] = (
                start + (low - 0.5) * step,
                start + (high + 0.5) * step,
            )
        return ranges

    def _emit_boxes(self) -> None:
        """Tell the boxes overlays which axes the active layer crops in Range mode,
        leaving out the boxes the user hid."""

        boxes = ()
        if self.layer is not None and layer_state(self.layer)["mode"] == RANGE:
            boxes = tuple(
                (axis, *bounds)
                for axis, bounds in self._ranges(self.layer).items()
                if axis not in self._hidden_boxes
            )
        self.boxes_changed.emit(boxes)

    def cleanup(self) -> None:
        """Disconnect, and restore the clipping of the layers this widget changed."""

        for emitter, handler in self._connections:
            emitter.disconnect(handler)
        for layer in self.viewer.layers:
            if layer.metadata.pop("orthoslices", None) is None:
                continue
            for node in self._volume_nodes(layer):
                set_slab_union(node, False)
            layer.experimental_clipping_planes = []
