import contextlib
from collections.abc import Callable

import napari
import numpy as np
from napari.components.layerlist import Extent
from napari.components.viewer_model import ViewerModel
from napari.layers import Vectors
from napari.utils.action_manager import action_manager
from napari.utils.events import EmitterGroup
from napari.utils.notifications import show_info
from qtpy.QtWidgets import (
    QCheckBox,
)


def center_cross_on_mouse(
    viewer_model: napari.components.viewer_model.ViewerModel,
):
    """move the cross to the mouse position"""

    if not getattr(viewer_model, "mouse_over_canvas", True):
        show_info(
            "Mouse is not over the canvas. You may need to click on the canvas."
        )
        return

    viewer_model.dims.current_step = tuple(
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


action_manager.register_action(
    name="napari:move_point",
    command=center_cross_on_mouse,
    description="Move dims point to mouse position",
    keymapprovider=ViewerModel,
)

action_manager.bind_shortcut("napari:move_point", "T")


class CrossWidget(QCheckBox):
    """
    Widget to control the presence of a crosshairs layer. Activating the checkbox is only
    allowed when there are at least 3 dims in the viewer. When activated, connected to
    viewer event signals to update the cross layer when the viewer dims change.
    """

    def __init__(self, viewer: napari.Viewer):
        super().__init__("Show crosshairs")
        self.viewer = viewer
        self.setChecked(False)
        self.setEnabled(
            len(self.viewer.layers) > 0 and self.viewer.dims.ndim > 2
        )

        self._connections = []
        self.color = "red"
        self.layer = None
        self._extent = None

        # checkbox availability
        self._connect(
            self.viewer.dims.events, "ndim", self._update_checkbox_enabled
        )

        # checkbox state
        self._connect(self, "stateChanged", self._update_cross_visibility)

    def _update_checkbox_enabled(self) -> None:
        """Update whether the checkbox is enabled depending on the number of dims and
        layers in the viewer."""

        # Check if a new layer is needed (>2 dims)
        n_valid_layers = len(
            [layer for layer in self.viewer.layers if layer != self.layer]
        )
        self.setEnabled(n_valid_layers > 0 and self.viewer.dims.ndim > 2)
        if self.isChecked() and (
            n_valid_layers == 0 or self.viewer.dims.ndim < 3
        ):
            self.setChecked(False)
            return

    def _connect_signals(self) -> None:
        """Connect to viewer dims changes and inserting/deleting layers"""

        # dims changes
        self._connect(self.viewer.dims.events, "order", self._update_cross)
        self._connect(
            self.viewer.dims.events, "current_step", self._update_cross
        )

        # layer changes
        self._connect(
            self.viewer.layers.events, "inserted", self._on_extent_change
        )
        self._connect(
            self.viewer.layers.events, "removed", self._on_extent_change
        )

        self._connect(self.viewer.dims.events, "range", self._on_range_change)

    def _disconnect_signals(self) -> None:
        """Disconnect from viewer dims changes and inserting/deleting layers"""

        # dims changes
        self._disconnect(self.viewer.dims.events, "order", self._update_cross)
        self._disconnect(
            self.viewer.dims.events, "current_step", self._update_cross
        )

        # layer changes
        self._disconnect(
            self.viewer.layers.events, "inserted", self._on_extent_change
        )
        self._disconnect(
            self.viewer.layers.events, "removed", self._on_extent_change
        )
        self._disconnect(
            self.viewer.dims.events, "range", self._on_range_change
        )

    def _connect(
        self,
        emitter: EmitterGroup,
        signal_name: str,
        handler: Callable[..., object],
    ) -> None:
        """Connect to a signal by name."""

        sig = getattr(emitter, signal_name)
        sig.connect(handler)
        self._connections.append((sig, handler))

    def _disconnect(
        self,
        emitter: EmitterGroup,
        signal_name: str,
        handler: Callable[..., object],
    ) -> None:
        """Disconnect from a signal by name"""

        sig = getattr(emitter, signal_name)
        with contextlib.suppress(ValueError):
            sig.disconnect(handler)
            self._connections.remove((sig, handler))

    def _on_range_change(self, event=None) -> None:
        """Check if the new range step is different from the scaling on self.layer, if so
        recompute the extent."""

        viewer_scale = [r.step for r in self.viewer.dims.range]
        if self.layer is not None and not np.all(
            viewer_scale == self.layer.scale
        ):
            self._on_extent_change()

    def _on_extent_change(self, event=None) -> None:
        """Removes the cross layer and re-inserts it if necessary, to ensure it matches
        the new dimensions and range. If the cross signal itself was removed, the widget
        is turned off."""

        # If the cross_layer itself was removed, turn off the checkbox.
        if event is not None:
            layer = getattr(event, "value", None)
            if layer is self.layer:
                if event.type == "removed":
                    self.setChecked(False)
                return

        self._disconnect_signals()
        # Remove old layer if present
        if self.layer is not None and self.layer in self.viewer.layers:
            self.color = self.layer.edge_color[0]
            self.viewer.layers.remove(self.layer)
            self.layer = None

        # Create new layer if the user has crosshairs turned on
        if self.isChecked() and self.isEnabled():
            self.layer = Vectors(name=".cross", ndim=self.viewer.dims.ndim)
            self.viewer.layers.append(self.layer)
            self._update_extent()
            self._connect_signals()

    # @qthrottled(leading=False)
    def _update_extent(self) -> None:
        """Compute the range the cross layer should cover, then update it."""

        extent_list = [
            layer.extent
            for layer in self.viewer.layers
            if layer is not self.layer
        ]

        if not extent_list:
            ndim = self.viewer.dims.ndim
            world = np.array(
                [[0] * ndim, [1] * ndim], dtype=float
            ).T  # shape (ndim, 2)
            step = np.ones(ndim, dtype=float)
        else:
            world = self.viewer.layers._get_extent_world(extent_list)
            step = self.viewer.layers._get_step_size(extent_list)

        self._extent = Extent(
            data=None,
            world=world,
            step=step,
        )

        # update cross layer data
        self._update_cross()

        # update cross layer color and line type
        self._update_cross_aesthetics()

    def _update_cross_aesthetics(self) -> None:
        """Update the layer edge color (set by user) and line vector_style and edge_width
        (default settings)."""

        if self.layer is not None and self.layer in self.viewer.layers:
            self.layer.edge_color = self.color
            self.layer.vector_style = "line"
            self.layer.edge_width = 2

    def _update_cross_visibility(self, state: bool) -> None:
        """Activate/deactivate the crosshairs layer."""

        if state:
            if self.layer is None:
                self.layer = Vectors(name=".cross", ndim=self.viewer.dims.ndim)

            self.viewer.layers.append(self.layer)
            self._update_extent()
            self._update_cross_aesthetics()
            self._connect_signals()

        else:
            self._disconnect_signals()
            if self.layer is not None and self.layer in self.viewer.layers:
                self.color = self.layer.edge_color[
                    0
                ]  # store color to restore later
                self.viewer.layers.remove(self.layer)
            self.layer = None

    def _update_cross(self) -> None:
        """Update the data in the cross layer according to the current extent and
        position"""

        # Check if layer exists
        if (
            self.layer is None
            or self.layer not in self.viewer.layers
            or self._extent is None
        ):
            return

        # Compute the new orientation
        point = list(self.viewer.dims.current_step)
        vec = []

        for i, (lower, upper) in enumerate(self._extent.world.T):
            axis_range = upper - lower
            if axis_range / self._extent.step[i] == 1:
                continue
            p1 = list(point)
            p1[i] = (lower + self._extent.step[i] / 2) / self._extent.step[i]
            p2 = [0] * self.viewer.dims.ndim
            p2[i] = axis_range / self._extent.step[i]
            vec.append((p1, p2))

        layer_scale = np.array(self.layer.scale)
        extent_step = np.array(self._extent.step)

        if extent_step.shape != layer_scale.shape:
            extent_step = np.pad(
                extent_step,
                (0, layer_scale.size - extent_step.size),
                constant_values=1,
            )

        if not np.allclose(layer_scale, extent_step):
            self.layer.scale = extent_step

        self.layer.data = vec

    def cleanup(self) -> None:
        """Turn off checkbox, clean up all signal connections."""

        for sig, handler in self._connections:
            with contextlib.suppress(ValueError):
                sig.disconnect(handler)
        self._connections.clear()
        if self.layer is not None and self.layer in self.viewer.layers:
            self.viewer.layers.remove(self.layer)
