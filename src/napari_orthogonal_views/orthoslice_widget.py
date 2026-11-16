"""Orthoslices and per-axis clipping ranges on a layer, in 3D.
'Slice' mode replaces the clipping function of the layer's volume node with one that
treats the clipping planes as pairs (one slab each) and draws the union of the slabs.
The 'Range' mode keeps the regular intersection, which crops the volume to a box.

The settings are stored per layer in the metadata ('orthoslices'), so every layer
remembers its own mode, planes and positions.
"""

from collections.abc import Callable

import numpy as np
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
    QVBoxLayout,
    QWidget,
)
from superqt import QLabeledRangeSlider, QLabeledSlider

from napari_orthogonal_views.cross_hair_overlay import Crosshairs

METADATA_KEY = "orthoslices"
VOLUME, SLICE, RANGE = "Full volume", "Slice", "Range"
MODE_TOOLTIPS = {
    VOLUME: "Show the full volume of the active layer (no slicing)",
    SLICE: "Show a single slice per checked axis (orthoslices)",
    RANGE: "Crop the volume to a range per checked axis",
}

_UNION_TEMPLATE = """
    float clip_planes(vec3 loc, vec3 vol_shape) {{
        vec3 loc_transf = $clip_transform(vec4(loc * vol_shape, 1)).xyz;
        float distance_from_clip = {start};
        {slabs}
        return distance_from_clip;
    }}
"""

# inside the slab when on the positive side of both planes, inside the union when
# inside any slab
_SLAB_TEMPLATE = """
        distance_from_clip = max(distance_from_clip, min(
            dot(loc_transf - $clipping_plane_pos{a}, $clipping_plane_norm{a}),
            dot(loc_transf - $clipping_plane_pos{b}, $clipping_plane_norm{b})));
"""


def _union_of_slabs_glsl(n_planes: int) -> str:
    """Clipping function drawing the union of slabs, from consecutive pairs of planes."""

    if n_planes == 0:
        return _UNION_TEMPLATE.format(start="3.4e38", slabs="")
    # every plane must appear in the shader. A list that is being filled can have an
    # odd length for a moment, its last plane then clips on its own.
    slabs = "".join(
        _SLAB_TEMPLATE.format(a=i, b=min(i + 1, n_planes - 1))
        for i in range(0, n_planes, 2)
    )
    return _UNION_TEMPLATE.format(start="-3.4e38", slabs=slabs)


def set_slab_union(volume_node, union: bool) -> None:
    """Switch a vispy volume node between intersecting its clipping planes (vispy's
    default) and drawing the union of the slabs formed by pairs of planes."""

    is_union = "_build_clipping_planes_glsl" in volume_node.__dict__
    if union == is_union:
        return
    volume_node.unfreeze()
    if union:
        volume_node._build_clipping_planes_glsl = _union_of_slabs_glsl
    else:
        del volume_node._build_clipping_planes_glsl
    volume_node.freeze()
    # rebuild the shader function with the current planes
    volume_node.clipping_planes = volume_node.clipping_planes


def layer_state(layer: Layer) -> dict:
    """The orthoslice settings of a layer: mode, and per axis (negative numbering)
    whether it is on in Slice and in Range mode, and its range, in dims steps. Slices
    always follow the current point."""

    return layer.metadata.setdefault(
        METADATA_KEY, {"mode": VOLUME, "planes": {}}
    )


def axis_on(axis_state: dict, mode: str) -> bool:
    """Whether an axis is on in a mode. Every axis starts on the first time a mode is
    used, after that the mode remembers its own choice."""

    return mode != VOLUME and axis_state["on"].get(mode, True)


class OrthosliceWidget(QWidget):
    """Tab with a mode and one checkbox and slider per displayed axis, clipping the
    active layer to a single slice per axis (orthoslices) or to a range per axis (crop
    box). Active while any of the views renders in 3D."""

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

        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode"))
        for mode, tooltip in MODE_TOOLTIPS.items():
            button = QRadioButton(mode)
            button.setToolTip(tooltip)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            mode_row.addWidget(button)
        mode_row.addStretch()
        self.mode_buttons[VOLUME].setChecked(True)
        self.mode_group.buttonClicked.connect(self._on_input)

        # one row per displayed axis: checkbox, slice slider and range slider
        self.checkboxes, self.sliders, self.range_sliders = [], [], []
        rows = QVBoxLayout()
        for _ in range(3):
            checkbox = QCheckBox()
            checkbox.setMinimumWidth(40)
            checkbox.toggled.connect(self._on_input)
            slider = QLabeledSlider(Qt.Horizontal)
            slider.valueChanged.connect(self._on_slider)
            range_slider = QLabeledRangeSlider(Qt.Horizontal)
            range_slider.valueChanged.connect(self._on_input)
            row = QHBoxLayout()
            for widget in (checkbox, slider, range_slider):
                row.addWidget(widget)
            rows.addLayout(row)
            self.checkboxes.append(checkbox)
            self.sliders.append(slider)
            self.range_sliders.append(range_slider)
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
        self._connections = [
            *(
                (getattr(dims.events, name), self._update_axes)
                for name in ("order", "ndim", "axis_labels", "range")
            ),
            (dims.events.current_step, self._on_current_step),
            (
                self.viewer.layers.selection.events.active,
                self._on_active_layer,
            ),
            *(
                (v.dims.events.ndisplay, self._on_active_layer)
                for v in viewers
            ),
        ]
        for emitter, handler in self._connections:
            emitter.connect(handler)

        self._update_axes()
        self._on_active_layer()

    # active layer and axes

    def _on_active_layer(self, event=None) -> None:
        """Show the settings of the active layer, if it can be clipped in 3D."""

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
        displayed axes in 3D, in all views."""

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
        self._load()

    # per layer settings

    def _mode(self) -> str:
        return self.mode_group.checkedButton().text()

    def _axis_state(self, layer: Layer, axis: int) -> dict:
        """The settings of one axis of a layer: on per mode (unset until the mode is
        first used), and its range, starting at the full range."""

        top = int(self.viewer.dims.nsteps[axis]) - 1
        return layer_state(layer)["planes"].setdefault(
            axis, {"on": {}, "range": (0, top)}
        )

    def _load(self) -> None:
        """Show the settings of the active layer in the widgets, without applying."""

        for widget in self._inputs:
            widget.blockSignals(True)
        if self.layer is not None:
            mode = layer_state(self.layer)["mode"]
            self.mode_buttons[mode].setChecked(True)
            for i, axis in enumerate(self._axes):
                axis_state = self._axis_state(self.layer, axis)
                self.checkboxes[i].setChecked(axis_on(axis_state, mode))
                self.range_sliders[i].setValue(axis_state["range"])
        for axis, slider in zip(self._axes, self.sliders, strict=False):
            slider.setValue(self.viewer.dims.current_step[axis])
        for widget in self._inputs:
            widget.blockSignals(False)

        # show the rows of the mode, a slider only acts while its axis is checked
        mode = self._mode()
        for i, (checkbox, slider, range_slider) in enumerate(
            zip(self.checkboxes, self.sliders, self.range_sliders, strict=True)
        ):
            used = i < len(self._axes)
            checkbox.setVisible(used and mode != VOLUME)
            slider.setVisible(used and mode == SLICE)
            range_slider.setVisible(used and mode == RANGE)
            checkbox.setEnabled(self.layer is not None)
            active = self.layer is not None and checkbox.isChecked()
            slider.setEnabled(active)
            range_slider.setEnabled(active)
        self._emit_boxes()

    def _store(self) -> None:
        """Write the widgets to the settings of the active layer."""

        state = layer_state(self.layer)
        previous, mode = state["mode"], self._mode()
        state["mode"] = mode
        for i, axis in enumerate(self._axes):
            axis_state = self._axis_state(self.layer, axis)
            axis_state["range"] = tuple(self.range_sliders[i].value())
            # while switching mode the checkboxes still show the previous mode, the
            # new mode restores its own (all on the first time it is used)
            if mode == previous and mode != VOLUME:
                axis_state["on"][mode] = self.checkboxes[i].isChecked()

    # user input

    def _on_input(self, value=None) -> None:
        if self.layer is None:
            return
        self._store()
        self.apply(self.layer)
        self._load()  # show the rows of the mode

    def _on_slider(self, value=None) -> None:
        """Move the current point, which moves the slices via _on_current_step."""

        step = list(self.viewer.dims.current_step)
        for axis, slider in zip(self._axes, self.sliders, strict=False):
            step[axis] = slider.value()
        self.viewer.dims.current_step = step

    def _on_current_step(self, event=None) -> None:
        """Move the slices of every layer in Slice mode to the current point."""

        for layer in self.viewer.layers:
            state = layer.metadata.get(METADATA_KEY)
            if state is not None and state["mode"] == SLICE:
                self.apply(layer)
        self._load()

    # applying to the layer

    def apply(self, layer: Layer) -> None:
        """Write one pair of clipping planes per checked axis to a layer."""

        slice_mode = layer_state(layer)["mode"] == SLICE
        for node in self._volume_nodes(layer):
            set_slab_union(node, slice_mode)

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

        layer.experimental_clipping_planes = planes

    def _ranges(self, layer: Layer) -> dict[int, tuple[float, float]]:
        """World range per checked axis of a layer. A slice extends half a step to
        either side, so it is one slice thick."""

        dims = self.viewer.dims
        mode = layer_state(layer)["mode"]
        ranges = {}
        for axis in self._axes:
            axis_state = self._axis_state(layer, axis)
            if not axis_on(axis_state, mode):
                continue
            if mode == SLICE:
                low = high = dims.current_step[axis]
            else:
                low, high = axis_state["range"]
            start, _, step = dims.range[axis]
            ranges[axis] = (
                start + (low - 0.5) * step,
                start + (high + 0.5) * step,
            )
        return ranges

    def _emit_boxes(self) -> None:
        """Tell the planes overlays which axes the active layer crops, in Range mode."""

        boxes = ()
        if self.layer is not None and layer_state(self.layer)["mode"] == RANGE:
            boxes = tuple(
                (axis, *bounds)
                for axis, bounds in self._ranges(self.layer).items()
            )
        self.boxes_changed.emit(boxes)

    def cleanup(self) -> None:
        """Disconnect, and restore the clipping of the layers this widget changed."""

        for emitter, handler in self._connections:
            emitter.disconnect(handler)
        for layer in self.viewer.layers:
            if layer.metadata.pop(METADATA_KEY, None) is None:
                continue
            for node in self._volume_nodes(layer):
                set_slab_union(node, False)
            layer.experimental_clipping_planes = []
