"""Experimental: show orthoslices and per-axis clipping ranges on a 3D rendered layer.

'Full volume' shows the default full volume rendering of the layer.
'Slice' shows a clipping plane pair per axis, defining a plane of one unit thick. Showing
these planes simultaneously requires patching the vispy shader (src/napari_orthogonal_views/controls/vispy_patches.py)
to draw the Union of these planes instead of the Intersection.
'Range' crops the volume according to all activated clipping plane pairs, so you see the intersection of these clipping ranges,
identical to the default behavior of the experimental_clipping_planes.
The settings are stored per layer in the metadata ('orthoslices'), and are restored when switching between the different modes.
Image and labels layers are clipped in their volume shader, other layers with clipping planes (points, shapes,
vectors, surfaces, tracks) by a clipping filter, since they are rendered differently.
Points are clipped by their centers, so they are drawn whole (otherwise you see only clipped fragments).
The changes to vispy are in vispy_patches, each of them is turned off if it fails with the installed vispy.

These functionalities are all considered experimental since they touch private vispy and napari functions, which may not be accessible in this way in the future.
"""

from collections.abc import Callable
from copy import deepcopy

import numpy as np
from napari.components.viewer_model import ViewerModel
from napari.layers import Layer
from napari.layers.utils.plane import ClippingPlane
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

from napari_orthogonal_views.controls.vispy_patches import (
    SUPPORTED,
    can_clip,
    clip_in_3d_only,
    clip_points_whole,
    fast_volume_shader,
    restore_napari_handler,
    set_slab_union,
)
from napari_orthogonal_views.planes_overlay import axis_color

VOLUME, SLICE, RANGE = "Full volume", "Slice", "Range"
MODE_TOOLTIPS = {
    VOLUME: "Show the full volume of the active layer (no slicing)",
    SLICE: "Show a single slice per checked axis (orthoslices)",
    RANGE: "Crop the volume to a range per checked axis",
}


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
    range views, each axis can be turned on individually. The settings of selected layers can be linked.
    """

    def __init__(
        self,
        viewers: list[ViewerModel],
        clip_nodes: Callable[[Layer], list],
    ) -> None:
        super().__init__()

        self.viewer = viewers[
            0
        ]  # the main viewer, owning the layers and the dims
        self._viewers = viewers  # main viewer and orthogonal views
        self._clip_nodes = clip_nodes  # nodes clipping a layer and its copies
        self.layer = None
        self._axes: list[int] = []  # the 3D displayed axes, negative numbering
        self._linked: set[Layer] = set()  # linked layers, also when deselected
        self._slice_steps = None  # current step of the displayed axes

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

        # one row per displayed axis: checkbox, slice slider and range slider
        self.checkboxes, self.sliders, self.range_sliders = [], [], []
        rows = QVBoxLayout()
        for _ in range(3):
            checkbox = QCheckBox()
            checkbox.setMinimumWidth(40)
            checkbox.toggled.connect(self._store_and_apply)
            slider = QLabeledSlider(Qt.Horizontal)
            slider.valueChanged.connect(self._on_slider)
            range_slider = QLabeledRangeSlider(Qt.Horizontal)
            range_slider.valueChanged.connect(self._store_and_apply)

            row = QHBoxLayout()
            for widget in (checkbox, slider, range_slider):
                row.addWidget(widget, alignment=Qt.AlignmentFlag.AlignBottom)
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

        # apply the settings of the active layer to all eligible selected layers
        self.link_checkbox = QCheckBox("Link the selected layers")
        self.link_checkbox.setToolTip(
            "When checked, applies the orthoslice settings to all selected layers"
        )
        self.link_checkbox.toggled.connect(self._on_link)

        self.message = QLabel()
        self.message.setWordWrap(True)
        font = self.message.font()
        font.setItalic(True)
        self.message.setFont(font)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addWidget(self.link_checkbox)
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

        # active layer and selection changes should trigger the widget to show the new
        # layer's settings, and whether layers can be linked
        self._connections.extend(
            [
                (
                    self.viewer.layers.selection.events.active,
                    self._on_active_layer,
                ),
                (
                    self.viewer.layers.selection.events.changed,
                    self._on_active_layer,
                ),
                (
                    self.viewer.layers.selection.events._current,
                    self._on_active_layer,
                ),
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

        selection = self.viewer.layers.selection
        layer = selection.active or (
            selection._current if selection._current in selection else None
        )  # the active layer or the last active one if it is included in multiselection
        in_3d = any(viewer.dims.ndisplay == 3 for viewer in self._viewers)
        ok = in_3d and self._eligible(layer)
        self.layer = layer if ok else None
        linked = self.layer is not None and self.layer in self._linked
        self.link_checkbox.setVisible(
            ok and (linked or len(self._selected()) > 1)
        )
        self.link_checkbox.blockSignals(True)
        self.link_checkbox.setChecked(linked)
        self.link_checkbox.blockSignals(False)

        if not in_3d:
            text = "Switch one of the views to 3D to use orthoslices."
        elif self.layer is None:
            text = "Select a layer with 3 or more dims that supports clipping planes."
        else:
            names = [f"'{layer.name}'" for layer in self._linked_layers()]
            shown = 1  # list names up to about 60 characters
            while shown < len(names) and len(", ".join(names[:shown])) < 60:
                shown += 1
            text = "Settings of " + ", ".join(names[:shown])
            if shown < len(names):
                text += f" and {len(names) - shown} other layer(s)"
            text += "."
        self.message.setText(text)
        for mode, button in self.mode_buttons.items():
            # Slice mode needs the union of the slabs, a vispy patch
            button.setEnabled(ok and (mode != SLICE or SUPPORTED["union"]))
        self._load()

    def _eligible(self, layer: Layer | None) -> bool:
        """Whether a layer can be orthosliced: it has 3 or more dims, and is drawn by a
        volume (images, labels) or by a visual with a clipping filter (points, shapes,
        vectors, surfaces, tracks)."""

        if layer is None or layer.ndim < 3:
            return False
        nodes = [node for _, node in self._clip_nodes(layer)]
        return len(nodes) > 0 and all(can_clip(node) for node in nodes)

    def _selected(self) -> list[Layer]:
        """The eligible selected layers, in layer list order."""

        selection = self.viewer.layers.selection
        return [
            layer
            for layer in self.viewer.layers
            if layer in selection and self._eligible(layer)
        ]

    def _linked_layers(self) -> list[Layer]:
        """All layers following the controls (the active layer, and the layers linked
        to it), in layer list order."""

        if self.layer is None:
            return []
        if self.layer not in self._linked:
            return [self.layer]
        return [layer for layer in self.viewer.layers if layer in self._linked]

    def _on_link(self, checked: bool) -> None:
        """Link the selected layers (replacing an earlier group), or unlink the group
        of the active layer."""

        self._linked = set(self._selected()) if checked else set()
        self._store_and_apply()

    def _update_axes(self, event=None) -> None:
        """Assign the rows to the last three axes of the dims order, which are the
        displayed axes in 3D, in all views. This ensures that the widget displays the
        correct labels and total span (world)."""

        dims = self.viewer.dims
        self._axes = [axis - dims.ndim for axis in dims.order[-3:]]
        self._slice_steps = None
        for axis, checkbox, slider, range_slider in zip(
            self._axes,
            self.checkboxes,
            self.sliders,
            self.range_sliders,
            strict=False,
        ):
            checkbox.setText(str(dims.axis_labels[axis]))
            r, g, b, _ = (axis_color(axis) * 255).astype(int)
            checkbox.setStyleSheet(f"color: rgb({r}, {g}, {b});")
            for widget in (slider, range_slider):
                widget.blockSignals(True)
                widget.setRange(0, int(dims.nsteps[axis]) - 1)
                widget.blockSignals(False)
        self._load()

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
        for i, (checkbox, slider, range_slider) in enumerate(
            zip(
                self.checkboxes,
                self.sliders,
                self.range_sliders,
                strict=True,
            )
        ):
            used = i < len(self._axes)
            checkbox.setVisible(used and mode != VOLUME)
            slider.setVisible(used and mode == SLICE)
            range_slider.setVisible(used and mode == RANGE)
            checkbox.setEnabled(self.layer is not None)
            active = self.layer is not None and checkbox.isChecked()
            slider.setEnabled(active)
            range_slider.setEnabled(active)

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
        state = layer_state(self.layer)
        for layer in self._linked_layers():
            if layer is not self.layer:
                layer.metadata["orthoslices"] = deepcopy(state)
            self.apply(layer)
        self._on_active_layer()  # update the message, and show the settings

    def _on_slider(self, value=None) -> None:
        """Read the values from the current sliders, and use it to move the current point via viewer.dims.current_step"""

        step = list(self.viewer.dims.current_step)
        for axis, slider in zip(self._axes, self.sliders, strict=False):
            step[axis] = slider.value()
        self.viewer.dims.current_step = step

    def _on_current_step(self, event=None) -> None:
        """Move the slices of every layer in Slice mode to the current point."""

        steps = tuple(
            self.viewer.dims.current_step[axis] for axis in self._axes
        )
        if steps == self._slice_steps:
            return  # the slices did not move (the step happened in another dim)
        self._slice_steps = steps
        for layer in self.viewer.layers:
            state = layer.metadata.get("orthoslices")
            if state is not None and state["mode"] == SLICE:
                self.apply(layer)
        self._load()

    def apply(self, layer: Layer) -> None:
        """Write one pair of clipping planes per checked axis to a layer."""

        slice_mode = layer_state(layer)["mode"] == SLICE
        nodes = self._clip_nodes(layer)
        for visual, node in nodes:
            if hasattr(node, "_clip_filter"):
                clip_in_3d_only(visual)
            else:
                fast_volume_shader(node)
            set_slab_union(node, slice_mode)  # turn union mode on or off.
            if hasattr(node, "points_markers"):
                clip_points_whole(node)

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
            # the slices are made with clipping planes, which need the volume depiction
            layer.depiction = "volume"

        current = layer.experimental_clipping_planes
        if len(current) == len(planes):
            for plane, new_plane in zip(current, planes, strict=True):
                plane.update(
                    new_plane.dict()
                )  # update in place to not trigger expensive signals
        else:
            layer.experimental_clipping_planes = planes

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

    def cleanup(self) -> None:
        """Disconnect, and restore the clipping of the layers this widget changed."""

        for emitter, handler in self._connections:
            emitter.disconnect(handler)
        for layer in self.viewer.layers:
            if layer.metadata.pop("orthoslices", None) is None:
                continue
            layer.experimental_clipping_planes = []
            for visual, node in self._clip_nodes(layer):
                set_slab_union(node, False)
                restore_napari_handler(visual)
