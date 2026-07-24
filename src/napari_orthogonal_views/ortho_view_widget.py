import contextlib
import warnings
from collections.abc import Callable, Iterator
from functools import partial
from types import MethodType
from typing import Any

import napari
from napari.components.viewer_model import ViewerModel
from napari.layers import Labels, Layer, Points
from napari.qt import QtViewer
from napari.utils.colormaps import Colormap
from napari.utils.events import Event, EventEmitter
from psygnal.containers import Selection
from qtpy.QtWidgets import (
    QHBoxLayout,
    QWidget,
)

from napari_orthogonal_views.cross_hair_overlay import CrosshairOverlay
from napari_orthogonal_views.viewer_utils import activate_on_hover


def copy_layer(layer: Layer, name: str = "") -> Layer:
    """Clone a napari layer from its data (shallow copy, both original layer and copied
    layer share the same underlying data)."""

    copied_layer = Layer.create(*layer.as_layer_data_tuple())
    copied_layer.metadata["viewer_name"] = name

    # connect to the same undo/redo history in the case of labels layers
    if isinstance(layer, Labels):
        copied_layer._undo_history = layer._undo_history
        copied_layer._redo_history = layer._redo_history

    return copied_layer


def _sync_guard(*args: Any, **kwargs: Any) -> None:
    """No-op placeholder slot used to protect real sync handlers from being skipped."""


def _connect_sync_signal(
    signal: Any, handler: Callable
) -> list[tuple[Any, Callable]]:
    """Connect ``handler`` to ``signal``, guarding against psygnal's mid-emit skip.

    For psygnal signals (napari >= 0.7) a throwaway :func:`_sync_guard` slot is
    connected first so it, rather than the real ``handler``, absorbs the slot that
    napari skips when it disconnects its lazy overlay callback during emission.

    Returns the ``(signal, slot)`` pairs that were connected so the caller can
    track them and disconnect them again during cleanup.
    """

    connected: list[tuple[Any, Callable]] = []

    # psygnal SignalInstance exposes ``_slots``; napari 0.6.x EventEmitter does not
    # and is unaffected by the skip, so the guard is psygnal-only.
    if hasattr(signal, "_slots"):
        with contextlib.suppress(TypeError, ValueError):
            signal.connect(_sync_guard, unique=True, check_nargs=False)
            connected.append((signal, _sync_guard))
    signal.connect(handler)
    connected.append((signal, handler))
    return connected


def _iter_event_signals(events_obj: Any) -> Iterator[tuple[str, Any]]:
    """Yield (name, signal) pairs across napari versions. Napari 0.6.x has a dictionary
    with emitters, but napari >= 0.7.x uses SignalGroups, so we need to check for both.
    """
    # napari 0.6.x: dict-like emitters
    if hasattr(events_obj, "emitters"):
        yield from events_obj.emitters.items()
        return

    # napari 0.7.x: SignalGroup (attribute-based)
    for name in dir(events_obj):
        if name.startswith("_"):
            continue
        sig = getattr(events_obj, name, None)
        if hasattr(sig, "connect"):
            yield name, sig


def get_property_names(
    obj: Any, include_nested: bool = True
) -> list[str | dict[str, list[str]]]:
    """
    Return properties that emit events on the given object.

    For Layer objects with include_nested=True, automatically discovers nested
    EventedModel objects like TextManager and returns their properties as dicts:
    {nested_attr: [properties]}

    For layers: only include settable properties.
    For nested objects: include public properties. Colormap and Selection are skipped
    because these cannot sync reliably as properties and need their own functions instead.

    Args:
        obj: The object to analyze (Layer or nested EventedModel)
        include_nested: If True and obj is a Layer, auto-discover nested EventedModels

    Returns:
        List mixing strings (property names) and dicts ({nested_attr: [properties]})
    """

    emitter_list: list[str | dict[str, list[str]]] = []

    if not hasattr(obj, "events"):
        return emitter_list

    # Skip specific properties that cannot sync because they are not shown on ortho views
    skip_props = {"thumbnail", "name", "scale_factor"}
    added_props = set()

    klass = obj.__class__

    # Collect signal-backed properties
    for event_name, _signal in _iter_event_signals(obj.events):

        if event_name in skip_props or event_name.startswith("_"):
            continue

        if event_name in added_props:
            continue

        # For napari layers only include real settable properties
        klass_attr = getattr(klass, event_name, None)
        if isinstance(klass_attr, property):
            if klass_attr.fset is not None:
                emitter_list.append(event_name)
                added_props.add(event_name)

        # Non-Layer EventedModel-like objects
        elif not isinstance(obj, Layer) and hasattr(obj, event_name):
            emitter_list.append(event_name)
            added_props.add(event_name)

    # Nested EventedModels
    if include_nested and isinstance(obj, Layer):
        # find all public attributes
        for attr_name in dir(obj):
            # skip  private attributes, special cases, already added properties and constants
            if (
                attr_name.startswith("_")
                or attr_name in skip_props
                or attr_name in added_props
                or attr_name.isupper()
            ):
                continue

            attr = getattr(obj, attr_name)

            # Skip Colormap objects (points.face_colormap, border_colormap) because these
            # cannot sync reliably. Image layers should not be affected by this. Also skip
            # the Points 'selected_data' selection, because it only syncs the 'active'
            # element, which does not work for multi-selection.
            if isinstance(attr, Colormap | Selection):
                continue

            # detect nested evented objects
            if (
                hasattr(attr, "events")
                and not isinstance(attr, Layer)
                and not callable(attr)
            ):
                nested_props = get_property_names(attr, include_nested=False)
                if nested_props:
                    emitter_list.append({attr_name: nested_props})
    return emitter_list


class ViewerModelContainer:
    """
    A container that holds a ViewerModel and manages synchronization.
    """

    def __init__(self, title: str, order: tuple[int], sync_filters=None):
        self.title = title
        self.viewer_model = ViewerModel(title)
        self.viewer_model.axes.visible = True
        self._block = False
        self._layer_hooks: dict[type, list[Callable]] = {}
        self.sync_filters = sync_filters or {}

        # Track every (signal, slot) connection made on the original layers and
        # their nested objects, keyed by id(orig_layer), so they can be
        # disconnected again when the layer is removed.
        self._layer_connections: dict[int, list[tuple[Any, Callable]]] = {}

        # Add crosshair overlays (initially invisible)
        self.crosshair_overlay = CrosshairOverlay(
            blending="translucent_no_depth", axis_order=order
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.viewer_model._overlays["crosshairs"] = self.crosshair_overlay

    def _setup_property_sync(
        self,
        source_obj: Any,
        target_obj: Any,
        prop_name: str,
        forward: bool = True,
        reverse: bool = True,
        bucket: list[tuple[Any, Callable]] | None = None,
    ) -> None:
        """Set up bidirectional syncing for a single property between two objects.

        Args:
            source_obj (Layer or EventedModel): Object to sync from
            target_obj (Layer or EventedModel): Object to sync to
            prop_name (str): Name of the property to sync
            forward (bool): whether to set up forward syncing
            reverse (bool): whether to set up reverse syncing
            bucket (list): optional list to record created (signal, slot)
                connections in, so they can be disconnected on cleanup.
        """
        if not hasattr(source_obj, "events") or not hasattr(
            source_obj.events, prop_name
        ):
            return

        def get_signal(obj: Any, name: str) -> Any | None:
            if not hasattr(obj, "events"):
                return None

            events = obj.events

            # 0.6.x
            if hasattr(events, "emitters"):
                return events.emitters.get(name, None)

            # >=0.7.0
            return getattr(events, name, None)

        # Forward sync from source -> target
        if forward:

            # initial sync (skip current_size because it triggers immediate updates on a
            # layer that is not ready yet)
            if prop_name != "current_size" and hasattr(target_obj, prop_name):
                setattr(
                    target_obj,
                    prop_name,
                    getattr(source_obj, prop_name),
                )

            signal = get_signal(source_obj, prop_name)

            if signal is not None and hasattr(signal, "connect"):
                made = _connect_sync_signal(
                    signal,
                    partial(
                        self._sync_property,
                        prop_name,
                        source_obj,
                        target_obj,
                    ),
                )
                if bucket is not None:
                    bucket.extend(made)

        # Reverse sync: target → source
        if reverse and hasattr(target_obj, "events"):

            signal = get_signal(target_obj, prop_name)

            if signal is not None and hasattr(signal, "connect"):
                made = _connect_sync_signal(
                    signal,
                    partial(
                        self._sync_property,
                        prop_name,
                        target_obj,
                        source_obj,
                    ),
                )
                if bucket is not None:
                    bucket.extend(made)

    def _sync_layer_properties(
        self,
        orig_layer: Layer,
        copied_layer: Layer,
        bucket: list[tuple[Any, Callable]] | None = None,
    ) -> None:
        """Sync properties between orig_layer and copied_layer, applying optional
        sync_filters. Automatically discovers and syncs nested object properties.

        Args:
            orig_layer: Original layer to sync from
            copied_layer: Copied layer to sync to
            bucket (list): optional list to record created (signal, slot)
                connections in, so they can be disconnected on cleanup.
        """

        def is_excluded(layer, prop, direction):
            """Check whether to skip syncing a property in a given direction."""
            for cls, rules in self.sync_filters.items():
                if isinstance(layer, cls):
                    excluded = rules.get(f"{direction}_exclude", set())
                    if excluded == "*":  # block all
                        return True
                    if prop in excluded:
                        return True
            return False

        # Sync layer properties (including nested properties automatically discovered)
        for item in get_property_names(orig_layer):
            if isinstance(item, dict):
                # Handle nested properties: {nested_attr_name: [properties]}
                for nested_attr, nested_props in item.items():
                    if hasattr(orig_layer, nested_attr) and hasattr(
                        copied_layer, nested_attr
                    ):
                        orig_nested = getattr(orig_layer, nested_attr)
                        copied_nested = getattr(copied_layer, nested_attr)

                        for prop_name in nested_props:
                            if hasattr(orig_nested, prop_name) and hasattr(
                                copied_nested, prop_name
                            ):
                                self._setup_property_sync(
                                    orig_nested,
                                    copied_nested,
                                    prop_name,
                                    bucket=bucket,
                                )
            else:
                # Handle regular layer properties
                property_name = item
                forward_sync = not is_excluded(
                    orig_layer, property_name, "forward"
                )
                reverse_sync = not is_excluded(
                    orig_layer, property_name, "reverse"
                )
                self._setup_property_sync(
                    orig_layer,
                    copied_layer,
                    property_name,
                    forward_sync,
                    reverse_sync,
                    bucket=bucket,
                )

    def _update_data(self, source: Labels, target: Labels) -> None:
        """Copy data from source layer to target layer, which triggers a data event on
        the target layer. Block syncing to itself (VM1 -> orig -> VM1 is blocked, but
        VM1 -> orig -> VM2 is not blocked)
        Args:
            source: the source Labels layer
            target: the target Labels layer"""

        self._block = True  # no syncing to itself is necessary
        target.data = (
            source.data
        )  # trigger data event so that it can sync to other viewer models (only if
        # target layer is orig_layer)
        self._block = False

    def _sync_name(
        self, orig_layer: Layer, copied_layer: Layer, event: Event
    ) -> None:
        """Forward the renaming event from original layer to copied layer"""

        copied_layer.name = orig_layer.name

    def _sync_property(
        self,
        property_name: str,
        source_layer: Layer,
        target_layer: Layer,
        _event: Event,
    ) -> None:
        """Sync a property of a layer in this viewer model.

        Args:
            property_name (str): name of the to be synced property.
            source_layer (napari.layers.Layer): layer to copy from.
            target_layer (napari.layers.Layer): layer to copy to.

        """

        if self._block:
            return

        self._block = True
        setattr(
            target_layer,
            property_name,
            getattr(source_layer, property_name),
        )
        self._block = False

    def _sync_selected_data(
        self, source_layer: Points, target_layer: Points, _event: Event
    ) -> None:
        """Special sync function to sync the full point selection between two Points
        layers. ``points.selected_data`` is a Selection (an evented set). The whole set
        has to be synced, rather than its single ``active`` element, because ``active``
        is None as soon as more than one point is selected, which would drop
        multi-selections.

        Args:
            source_layer (Points)
            target_layer (Points)
        """

        if self._block:
            return

        self._block = True
        target_layer.selected_data = set(source_layer.selected_data)
        self._block = False

    def set_layer_hooks(self, hooks: dict[type, list[Callable]]) -> None:
        """Replace current hook mapping to allow special handling of certain layer types."""

        self._layer_hooks = hooks

    def add_layer(self, orig_layer: Layer, index: int) -> None:
        """Set the layers of the contained ViewerModel."""

        self.viewer_model.layers.insert(
            index, copy_layer(orig_layer, self.title)
        )
        copied_layer = self.viewer_model.layers[orig_layer.name]

        # Collect every connection made for this layer so it can be cleaned up
        # again when the layer is removed (see remove_layer_connections).
        bucket = self._layer_connections.setdefault(id(orig_layer), [])

        # sync name
        def sync_name_wrapper(event):
            return self._sync_name(orig_layer, copied_layer, event)

        orig_layer.events.name.connect(sync_name_wrapper)
        bucket.append((orig_layer.events.name, sync_name_wrapper))

        # sync properties
        self._sync_layer_properties(orig_layer, copied_layer, bucket)

        # Special relevant labels layer syncing
        if isinstance(orig_layer, Labels):

            # Calling the Undo/Redo function on the labels layer should also refresh the
            # other views.
            def wrap_undo_redo(
                source_layer: Labels, target_layer: Labels, update_fn: Callable
            ):
                """Wrap undo and redo methods to trigger syncing via update_fn"""
                orig_undo = source_layer.undo
                orig_redo = source_layer.redo

                def wrapped_undo(self):
                    orig_undo()
                    update_fn(source=self, target=target_layer)

                def wrapped_redo(self):
                    orig_redo()
                    update_fn(source=self, target=target_layer)

                # Replace methods on the instance
                source_layer.undo = MethodType(wrapped_undo, source_layer)
                source_layer.redo = MethodType(wrapped_redo, source_layer)

            # Wrap undo/redo
            wrap_undo_redo(copied_layer, orig_layer, self._update_data)
            wrap_undo_redo(orig_layer, copied_layer, self._update_data)

            # if the original layer is a labels layer, we want to connect to the paint
            # event, because we need it in order to invoke syncing between the different
            # viewers. (Paint event does not trigger 'data' event by itself).
            # We do not need to connect to the eraser and fill bucket separately.
            def copied_paint(_event):
                # copy data from copied_layer to orig_layer (orig_layer emits signal,
                # which triggers update on other viewer models, if present)
                return self._update_data(
                    source=copied_layer, target=orig_layer
                )

            def orig_paint(_event):
                # copy data from orig_layer to copied_layer (copied_layer emits signal
                # but we don't process it)
                return self._update_data(
                    source=orig_layer, target=copied_layer
                )

            copied_layer.events.paint.connect(copied_paint)
            orig_layer.events.paint.connect(orig_paint)
            bucket.append((copied_layer.events.paint, copied_paint))
            bucket.append((orig_layer.events.paint, orig_paint))

        # # Special relevant 'selected_data' syncing for points layers
        if isinstance(orig_layer, Points):

            def orig_selection(event):
                return self._sync_selected_data(
                    orig_layer, copied_layer, event
                )

            def copied_selection(event):
                return self._sync_selected_data(
                    copied_layer, orig_layer, event
                )

            orig_signal = orig_layer.selected_data.events.items_changed
            copied_signal = copied_layer.selected_data.events.items_changed
            orig_signal.connect(orig_selection)
            copied_signal.connect(copied_selection)
            bucket.append((orig_signal, orig_selection))
            bucket.append((copied_signal, copied_selection))

            # initial sync
            self._sync_selected_data(orig_layer, copied_layer, None)

        # Special hooks based on layer type
        for hook_type, hooks in self._layer_hooks.items():
            if isinstance(orig_layer, hook_type):
                for hook in hooks:
                    hook(orig_layer, copied_layer)

    def remove_layer_connections(self, orig_layer: Layer) -> None:
        """Disconnect and forget every sync connection made for ``orig_layer``.

        Called when a layer is removed from the main viewer so the connections on
        the original layer (and its nested objects) do not outlive the copied layer
        and keep mutating/referencing it.
        """

        for signal, handler in self._layer_connections.pop(id(orig_layer), []):
            with contextlib.suppress(ValueError, RuntimeError, TypeError):
                signal.disconnect(handler)

    def disconnect_all(self) -> None:
        """Disconnect every tracked sync connection across all layers."""

        for key in list(self._layer_connections):
            for signal, handler in self._layer_connections.pop(key, []):
                with contextlib.suppress(ValueError, RuntimeError, TypeError):
                    signal.disconnect(handler)


class OrthoViewWidget(QWidget):
    """Secondary viewer widget to hold another canvas showing the same data as the viewer
    but in a different orientation."""

    def __init__(
        self,
        viewer: napari.Viewer,
        order=(-2, -3, -1),
        sync_axes: list[int] | None = None,
        sync_filters: dict | None = None,
        layer_hooks: dict | None = None,
    ):
        super().__init__()
        self.viewer = viewer
        self.viewer.axes.visible = True
        self.viewer.axes.events.visible.connect(
            self._set_orth_views_dims_order
        )
        self.order = order
        if sync_axes is None:
            sync_axes = [0]
        self.sync_axes = sync_axes
        self._grid_syncing = False
        self._block_center = False
        self._block_step = False
        self._layer_hooks = layer_hooks

        # create container to store viewer model in
        self.vm_container = ViewerModelContainer(
            title="orthogonal view", order=order, sync_filters=sync_filters
        )

        # Add optional layer hooks
        self.vm_container.set_layer_hooks(self._layer_hooks)

        # Create QtViewer instance with viewer model
        self.qt_viewer = QtViewer(self.vm_container.viewer_model)
        activate_on_hover(self.qt_viewer)  # activate without clicking
        self.qt_viewer.setAcceptDrops(False)  # no drag and drop here

        # Set layout
        layout = QHBoxLayout()
        layout.addWidget(self.qt_viewer)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Add the layers currently in the viewer
        for i, layer in enumerate(self.viewer.layers):
            self.vm_container.add_layer(layer, i)

        # Ensure the layer with the same index is active
        active_layer = self.viewer.layers.selection.active
        if active_layer is not None:
            layer_index = self.viewer.layers.index(active_layer)
            self.vm_container.viewer_model.layers.selection.active = (
                self.vm_container.viewer_model.layers[layer_index]
            )

        # Connect to events
        self._connections = []

        # Layer events
        self._connect(self.viewer.layers.events.inserted, self._layer_added)
        self._connect(self.viewer.layers.events.removed, self._layer_removed)
        self._connect(self.viewer.layers.events.moved, self._layer_moved)
        self._connect(
            self.viewer.layers.selection.events.active,
            self._layer_selection_changed,
        )

        # Viewer events
        self._connect(self.viewer.events.reset_view, self._reset_view)
        self._connect(
            self.viewer.dims.events.current_step, self._update_current_step
        )
        self._connect(
            self.vm_container.viewer_model.dims.events.current_step,
            self._update_current_step,
        )  # reverse dims sync

        # Adjust dimension order for orthogonal views
        self._set_orth_views_dims_order()

    def _connect(self, emitter: EventEmitter, handler: Callable) -> None:
        """Connect an event emitter to a function handler and add it to the list of
        connections."""

        emitter.connect(handler)
        self._connections.append((emitter, handler))

    def _disconnect(self, emitter: EventEmitter, handler: Callable) -> None:
        """Disconnect an event emitter to a function handler and remove it from the list
        of connections."""

        with contextlib.suppress(ValueError):
            emitter.disconnect(handler)
            self._connections.remove((emitter, handler))

    def _set_orth_views_dims_order(self) -> None:
        """Set the order of the c, t, z, y, x dims in the orthogonal views, using the
        axis order attribute."""

        # TODO: allow the user to provide the dimension order and names.
        axis_labels = (
            "c",
            "t",
            "z",
            "y",
            "x",
        )  # assume default axis labels for now
        order = list(self.viewer.dims.order)

        if len(order) > 2:
            # model axis order (e.g. xz view)
            m_order = list(order)
            m_order[-3:] = (
                m_order[self.order[0]],
                m_order[self.order[1]],
                m_order[self.order[2]],
            )
            self.vm_container.viewer_model.dims.order = m_order

        self.viewer.dims.axis_labels = axis_labels[
            len(axis_labels) - len(order) :
        ]
        self.vm_container.viewer_model.dims.axis_labels = axis_labels[
            len(axis_labels) - len(order) :
        ]

        # whether or not the axis should be visible
        self.vm_container.viewer_model.axes.visible = self.viewer.axes.visible

    def _reset_view(self) -> None:
        """Propagate the reset view event"""

        self.vm_container.viewer_model.reset_view()

    def _layer_selection_changed(self, event: Event) -> None:
        """Update of current active layers"""

        if event.value is None:
            self.vm_container.viewer_model.layers.selection.active = None
            return

        if event.value.name in self.vm_container.viewer_model.layers:
            self.vm_container.viewer_model.layers.selection.active = (
                self.vm_container.viewer_model.layers[event.value.name]
            )

    def _layer_added(self, event: Event) -> None:
        """Add layer to additional other viewer models"""

        if event.value.name not in self.vm_container.viewer_model.layers:
            self.vm_container.add_layer(event.value, event.index)

        self._set_orth_views_dims_order()

    def _layer_removed(self, event: Event) -> None:
        """Remove layer in all viewer models"""

        # Disconnect the sync connections made on the original layer so they don't
        # outlive the removed copied layer.
        self.vm_container.remove_layer_connections(event.value)

        layer_name = event.value.name
        if layer_name in self.vm_container.viewer_model.layers:
            self.vm_container.viewer_model.layers.pop(layer_name)
        self._set_orth_views_dims_order()

    def _layer_moved(self, event: Event) -> None:
        """Update order of layers in all viewer models"""

        dest_index = (
            event.new_index
            if event.new_index < event.index
            else event.new_index + 1
        )
        self.vm_container.viewer_model.layers.move(event.index, dest_index)

    def _update_current_step(self, event: Event) -> None:
        """Sync the current step between different viewer models.

        We sync using world coordinates (dims.point) rather than step indices
        (current_step) because each viewer model may have different dims.range
        values due to different layer scales or orientations. Syncing step
        indices directly would result in incorrect world positions.
        """

        if self._block_center:
            return

        self._block_center = True

        # Convert source step indices to world coordinates
        source = event.source
        world_coords = tuple(
            source.range[i].start + event.value[i] * source.range[i].step
            for i in range(len(event.value))
        )

        for model in [
            self.viewer,
            self.vm_container.viewer_model,
        ]:
            if model.dims.order is event.source.order:
                continue

            # Set world coordinates - napari will convert to appropriate steps
            # for this model's dims.range
            model.dims.point = world_coords

            # check if the camera center is in the field of view, if not, adjust
            camera_center = list(model.camera.center)
            new_y_center, new_x_center = check_center(
                model, model.dims.current_step
            )
            camera_center[-2] = new_y_center
            camera_center[-1] = new_x_center
            model.camera.center = camera_center

        self._block_center = False

    def sync_event(
        self,
        source_emitter: EventEmitter,
        target_callable: Callable,
        sync: bool,
        key_label: str | None = None,
    ) -> None:
        """
        Connect or disconnect an event from a source emitter to a target callable.

        Args:
            source_emitter (napari EventEmitter)
                The source event emitter (e.g., viewer.camera.events.zoom).
            target_callable (callable)
                Function to call when the source event fires.
                Signature: target_callable(event)
            sync (bool):
                True to connect, False to disconnect.
            key_label (str): optional name to store this connection by.
        """

        if not hasattr(self, "_sync_handlers"):
            # maps key_label -> (emitter, handler)
            self._sync_handlers = {}

        if key_label is None:
            key_label = id(target_callable)

        if sync:
            if key_label in self._sync_handlers:
                return  # do not allow duplicate connections

            def handler(event, _fn=target_callable):
                _fn(event)

            # Store the actual emitter reference
            self._sync_handlers[key_label] = (source_emitter, handler)
            self._connect(source_emitter, handler)
        else:
            if key_label not in self._sync_handlers:
                return

            emitter, handler = self._sync_handlers.pop(key_label)
            self._disconnect(emitter, handler)

    def cleanup(self) -> None:
        """Disconnect from all signals and clear the list"""

        for sig, handler in self._connections:
            with contextlib.suppress(ValueError):
                sig.disconnect(handler)

        self._connections.clear()

        # Also disconnect the per-layer sync connections made on the original
        # layers (and their nested objects), which are tracked separately.
        self.vm_container.disconnect_all()


def check_center(model: ViewerModel, coords: list[int]) -> tuple[int, int]:
    """Check if the given coordinates are in the current field of view, and if not adjust
    the camera center

    Args:
        coords (list[int]): list of current step coordinates to check.

    Returns:
        tuple [int, int]: (updated) y and x center coordinates to ensure the coordinates
        are visible.
    """

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        view_box = model._get_viewbox_size()
    zoom = model.camera.zoom
    center = model.camera.center
    h = view_box[0] / zoom
    w = view_box[1] / zoom
    min_h = center[-2] - (h / 2)
    max_h = center[-2] + (h / 2)
    min_w = center[-1] - (w / 2)
    max_w = center[-1] + (w / 2)

    order = model.dims.order
    step = [model.dims.range[r].step for r in range(len(order))]
    coords_reordered = [coords[i] * step[i] for i in order]

    y_in_view = coords_reordered[-2] > min_h and coords_reordered[-2] < max_h
    x_in_view = coords_reordered[-1] > min_w and coords_reordered[-1] < max_w

    new_x_center = coords_reordered[-1] if not x_in_view else center[-1]
    new_y_center = coords_reordered[-2] if not y_in_view else center[-2]

    return new_y_center, new_x_center
