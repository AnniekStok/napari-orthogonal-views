import collections

import numpy as np
from napari.layers import Image, Labels, Points

from napari_orthogonal_views.cross_hair_overlay import CrosshairOverlay
from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    show_orthogonal_views,
)
from napari_orthogonal_views.ortho_view_widget import (
    OrthoViewWidget,
    get_property_names,
)


def test_add_move_remove_layer(make_napari_viewer, qtbot):
    """Test that adding, moving, and removing layers is correctly synced between the main
    viewer and the orthogonal views. Verify that attributes such as brush_size are
    correctly copied when creating a new layer from an existing one.
    """

    # Create viewer and orthoview manager
    viewer = make_napari_viewer()
    m = _get_manager(viewer)

    # Add a test layer first, before showing the ortho views to test initial copy of
    # attributes
    labels = Labels(np.zeros((2, 2, 2), dtype=np.uint8))
    labels.name = "test_labels_layer"
    viewer.add_layer(labels)
    labels.brush_size = (
        50  # change value to test if this property is copied correctly
    )

    # Show ortho views, ensure layer is copied, and that the brush_size value is copied
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)

    assert (
        "test_labels_layer" in m.right_widget.vm_container.viewer_model.layers
    )
    assert (
        "test_labels_layer" in m.bottom_widget.vm_container.viewer_model.layers
    )

    assert isinstance(
        m.right_widget.vm_container.viewer_model.layers["test_labels_layer"],
        Labels,
    )
    assert isinstance(
        m.bottom_widget.vm_container.viewer_model.layers["test_labels_layer"],
        Labels,
    )

    assert (
        m.right_widget.vm_container.viewer_model.layers[0].brush_size
        == labels.brush_size
        == 50
    )

    # Test image layer
    layer = Image(np.zeros((2, 2, 2)))
    layer.name = "test_layer"
    viewer.add_layer(layer)

    # Check that the layer was added correctly to both viewer models
    assert "test_layer" in m.right_widget.vm_container.viewer_model.layers
    assert "test_layer" in m.bottom_widget.vm_container.viewer_model.layers
    assert isinstance(
        m.right_widget.vm_container.viewer_model.layers["test_layer"], Image
    )
    assert isinstance(
        m.bottom_widget.vm_container.viewer_model.layers["test_layer"], Image
    )

    # Move layer and check the order
    viewer.layers.move(1, 0)
    assert viewer.layers[0].name == "test_layer"
    assert viewer.layers[1].name == "test_labels_layer"

    assert (
        m.right_widget.vm_container.viewer_model.layers[1].name
        == "test_labels_layer"
    )
    assert (
        m.right_widget.vm_container.viewer_model.layers[0].name == "test_layer"
    )
    assert (
        m.bottom_widget.vm_container.viewer_model.layers[1].name
        == "test_labels_layer"
    )
    assert (
        m.bottom_widget.vm_container.viewer_model.layers[0].name
        == "test_layer"
    )

    # Test renaming
    viewer.layers[0].name = "layer1_renamed"
    assert viewer.layers[0].name == "layer1_renamed"
    assert (
        m.right_widget.vm_container.viewer_model.layers[0].name
        == "layer1_renamed"
    )
    assert (
        m.bottom_widget.vm_container.viewer_model.layers[0].name
        == "layer1_renamed"
    )

    # Check that the layer is removed in all viewers
    viewer.layers.remove(layer)

    assert "test_layer" not in viewer.layers
    assert "test_layer" not in m.right_widget.vm_container.viewer_model.layers
    assert "test_layer" not in m.bottom_widget.vm_container.viewer_model.layers

    m.cleanup()


def test_sync(make_napari_viewer, qtbot):
    """Test that the sync connection between the main viewer and the orthogonal views is
    setup correctly.
        - Test viewer dimension step syncing.
        - Test fowward and reverse syncing of layer properties such as contour, opacity,
            visibility.
        - Test syncing of layer data.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)

    # Test image layer
    layer = Image(np.zeros((10, 50, 50, 50)))
    layer.name = "test_layer"
    viewer.add_layer(layer)

    # test labels layer
    labels = Labels(np.zeros((10, 50, 50, 50), dtype=np.uint8))
    labels.name = "test_labels_layer"
    viewer.add_layer(labels)

    # test points layer
    points = Points([[8, 10, 10, 10], [7, 8, 8, 8]])
    points.name = "test_points_layer"
    viewer.add_layer(points)

    # Update current step and check that the viewer models follow
    viewer.dims.current_step = (1, 1, 0, 0)
    assert viewer.dims.current_step == (1, 1, 0, 0)
    assert m.right_widget.vm_container.viewer_model.dims.current_step == (
        1,
        1,
        0,
        0,
    )
    assert m.bottom_widget.vm_container.viewer_model.dims.current_step == (
        1,
        1,
        0,
        0,
    )

    m.right_widget.vm_container.viewer_model.dims.current_step = (0, 0, 1, 1)
    assert viewer.dims.current_step == (0, 0, 1, 1)
    assert m.right_widget.vm_container.viewer_model.dims.current_step == (
        0,
        0,
        1,
        1,
    )
    assert m.bottom_widget.vm_container.viewer_model.dims.current_step == (
        0,
        0,
        1,
        1,
    )

    # Check syncing of properties
    viewer.layers[0].visible = False
    m.right_widget.vm_container.viewer_model.layers[1].opacity = 0.5
    m.bottom_widget.vm_container.viewer_model.layers[1].contour = 1
    viewer.layers[2].text.size = 23  # change text size (nested property)
    viewer.layers[0].bounding_box.visible = (
        True  # change bounding box visibility
    )

    assert viewer.layers[0].visible is False
    assert m.right_widget.vm_container.viewer_model.layers[0].visible is False
    assert m.bottom_widget.vm_container.viewer_model.layers[0].visible is False
    assert viewer.layers[1].opacity == 0.5
    assert m.right_widget.vm_container.viewer_model.layers[1].opacity == 0.5
    assert m.bottom_widget.vm_container.viewer_model.layers[1].opacity == 0.5
    assert viewer.layers[1].contour == 1
    assert m.right_widget.vm_container.viewer_model.layers[1].contour == 1
    assert m.bottom_widget.vm_container.viewer_model.layers[1].contour == 1
    assert m.bottom_widget.vm_container.viewer_model.layers[2].text.size == 23
    assert m.right_widget.vm_container.viewer_model.layers[2].text.size == 23
    assert viewer.layers[0].bounding_box.visible is True
    assert (
        m.bottom_widget.vm_container.viewer_model.layers[
            0
        ].bounding_box.visible
        is True
    )
    assert (
        m.right_widget.vm_container.viewer_model.layers[0].bounding_box.visible
        is True
    )

    # Sync data
    m.right_widget.vm_container.viewer_model.layers[1].data[
        2, 10:20, 10:20, 10:20
    ] = 5
    expected = np.zeros((10, 50, 50, 50))
    expected[2, 10:20, 10:20, 10:20] = 5
    np.testing.assert_array_equal(viewer.layers[1].data, expected)
    np.testing.assert_array_equal(
        m.right_widget.vm_container.viewer_model.layers[1].data, expected
    )
    np.testing.assert_array_equal(
        m.bottom_widget.vm_container.viewer_model.layers[1].data, expected
    )

    m.cleanup()


def test_sync_repeated_toggles(make_napari_viewer, qtbot):
    """Regression test for the psygnal mid-emit slot skip (napari >= 0.7).

    napari's VispyCanvas._update_layer_overlays disconnects itself from an
    overlay's ``visible`` event while that event is emitting (the first time the
    overlay becomes visible). Under psygnal this shifts the remaining slots and
    silently skips the slot right after napari's callback, which is the
    first-connected view's (right_widget) sync handler. A no-op guard slot is
    connected first to absorb that skip.

    This test verifies that:
        - Normal (non-overlay) properties are *not* absorbed by the guard.
        - Overlay ``visible`` syncs on the first toggle for *both* views.
        - Syncing remains correct across repeated toggles, in both the
          forward (orig -> copies) and reverse (a copy -> orig + other copy)
          directions.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)

    img = Image(np.zeros((5, 20, 20, 20)))
    img.name = "img"
    viewer.add_layer(img)

    labels = Labels(np.zeros((5, 20, 20, 20), dtype=np.uint8))
    labels.name = "labels"
    viewer.add_layer(labels)

    points = Points([[1, 1, 1, 1], [2, 2, 2, 2]])
    points.name = "points"
    viewer.add_layer(points)

    def right(i):
        return m.right_widget.vm_container.viewer_model.layers[i]

    def bottom(i):
        return m.bottom_widget.vm_container.viewer_model.layers[i]

    for rep in range(4):
        # --- forward sync of normal properties (must not be absorbed) ---
        img.opacity = 0.1 + 0.2 * rep
        assert right(0).opacity == img.opacity
        assert bottom(0).opacity == img.opacity

        img.visible = rep % 2 == 0
        assert right(0).visible is img.visible
        assert bottom(0).visible is img.visible

        labels.contour = rep
        assert right(1).contour == labels.contour
        assert bottom(1).contour == labels.contour

        points.text.size = 10 + rep  # nested property
        assert right(2).text.size == points.text.size
        assert bottom(2).text.size == points.text.size

        # --- reverse sync from the right copy -> orig + bottom copy ---
        right(0).gamma = 0.5 + 0.1 * rep
        assert img.gamma == right(0).gamma
        assert bottom(0).gamma == right(0).gamma

        # --- overlay visible: forward, must sync for BOTH views on toggle ---
        img.bounding_box.visible = rep % 2 == 0
        assert (
            right(0).bounding_box.visible is img.bounding_box.visible
        ), f"right bb.visible not synced (forward, rep {rep})"
        assert (
            bottom(0).bounding_box.visible is img.bounding_box.visible
        ), f"bottom bb.visible not synced (forward, rep {rep})"

        # --- overlay visible: reverse from right copy ---
        right(0).bounding_box.visible = rep % 2 == 1
        assert img.bounding_box.visible is right(0).bounding_box.visible
        assert bottom(0).bounding_box.visible is right(0).bounding_box.visible

        # --- overlay visible: reverse from bottom copy ---
        bottom(0).bounding_box.visible = rep % 2 == 0
        assert img.bounding_box.visible is bottom(0).bounding_box.visible
        assert right(0).bounding_box.visible is bottom(0).bounding_box.visible

    m.cleanup()


def test_colormap_sync(make_napari_viewer, qtbot):
    """Colormap syncing.

    Image/Labels expose ``colormap`` as a settable layer property with an
    ``events.colormap`` emitter, so it is synced as a flat, whole-object property
    (forward and reverse), even though napari replaces the whole Colormap object
    on assignment.

    Points ``face_colormap`` / ``border_colormap`` are nested Colormap value
    objects that napari replaces wholesale *without* emitting any layer-level
    event. Connecting to the current colormap object's field events would silently
    go stale on replacement, so they are deliberately *excluded* from nested
    discovery rather than synced unreliably.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # Points face/border colormaps must NOT be discovered as nested objects.
    points = Points([[1, 1, 1], [2, 2, 2]])
    nested = [
        list(d.keys())[0]
        for d in get_property_names(points)
        if isinstance(d, dict)
    ]
    assert "face_colormap" not in nested
    assert "border_colormap" not in nested

    # Image colormap is a flat property and must still sync both ways.
    img = Image(np.zeros((5, 20, 20)))
    img.name = "img"
    viewer.add_layer(img)

    right = m.right_widget.vm_container.viewer_model.layers[0]
    bottom = m.bottom_widget.vm_container.viewer_model.layers[0]

    img.colormap = "magma"
    assert right.colormap.name == "magma"
    assert bottom.colormap.name == "magma"

    # reverse: setting on a copy propagates to orig and the other copy
    right.colormap = "viridis"
    assert img.colormap.name == "viridis"
    assert bottom.colormap.name == "viridis"

    m.cleanup()


def test_sync_filters_apply_to_nested_properties(make_napari_viewer, qtbot):
    """sync_filters must cover nested properties, not just the layer's own ones.

    A layer that is filtered out entirely ("*") should end up with no property
    connections at all -- previously its nested overlays (bounding box, name overlay,
    text, ...) were still wired up in both directions. Individual nested properties can
    be named as "<attr>.<property>".
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    m.set_sync_filters(
        {
            Image: {"forward_exclude": "*", "reverse_exclude": "*"},
            Points: {"forward_exclude": {"text.size"}},
        }
    )
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    img = Image(np.zeros((5, 20, 20)))
    img.name = "img"
    viewer.add_layer(img)
    points = Points(
        [[1, 1, 1], [2, 2, 2]], name="points", text={"string": "x"}
    )
    viewer.add_layer(points)

    container = m.right_widget.vm_container
    copied_img = container.viewer_model.layers["img"]
    copied_points = container.viewer_model.layers["points"]

    # nothing at all is synced for the fully excluded layer, nested included
    img.opacity = 0.25
    img.bounding_box.visible = True
    assert copied_img.opacity != 0.25
    assert copied_img.bounding_box.visible is False

    # the individually named nested property is not synced ...
    points.text.size = 23
    assert copied_points.text.size != 23

    # ... while its siblings still are
    points.text.visible = False
    assert copied_points.text.visible is False
    points.bounding_box.visible = True
    assert copied_points.bounding_box.visible is True

    m.cleanup()


def test_layer_reference_to_viewer_is_not_synced(make_napari_viewer, qtbot):
    """A viewer held by a layer must not be discovered as a nested evented object.

    Layer subclasses sometimes keep a reference to the viewer they live in. That is a
    whole viewer's state (theme, status, title, ...), not a layer property, and syncing
    it would wire the ortho viewer models to the main viewer.
    """

    viewer = make_napari_viewer()

    class LayerWithViewer(Image):
        pass

    layer = LayerWithViewer(np.zeros((5, 20, 20)))
    layer.viewer = viewer

    nested = [
        list(item.keys())[0]
        for item in get_property_names(layer)
        if isinstance(item, dict)
    ]
    assert "viewer" not in nested
    assert "bounding_box" in nested  # ... other nested models are still found


def test_sync_connections_cleaned_up(make_napari_viewer, qtbot):
    """Removing a layer must disconnect the sync connections made on the original
    layer, so they do not outlive the removed copied layer and keep mutating it.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    img = Image(np.zeros((5, 20, 20)))
    img.name = "img"
    viewer.add_layer(img)

    container = m.right_widget.vm_container
    copied = container.viewer_model.layers[0]  # keep a reference to the copy

    # Connections were tracked for this layer.
    assert id(img) in container._layer_connections
    assert len(container._layer_connections[id(img)]) > 0

    # Remove the layer; tracked connections must be dropped.
    viewer.layers.remove(img)
    assert id(img) not in container._layer_connections
    assert "img" not in container.viewer_model.layers

    # Mutating the (still referenced) original layer must NOT touch the orphaned
    # copied layer anymore.
    copied.opacity = 1.0
    copied.bounding_box.visible = False
    img.opacity = 0.25
    img.bounding_box.visible = True

    assert copied.opacity == 1.0
    assert copied.bounding_box.visible is False

    m.cleanup()


def test_layer_hook_connections_cleaned_up(make_napari_viewer, qtbot):
    """Connections a layer hook makes on the *original* layer must be released again.

    A hook closes over the copied layer, so a connection that survives hiding the
    orthogonal views keeps a discarded copy alive and updated: the work (and the
    memory) then grows with every hide/show cycle.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)

    labels = Labels(np.zeros((5, 20, 20), dtype=np.uint8))
    labels.name = "labels"
    viewer.add_layer(labels)

    notified = []

    def hook(orig_layer, copied_layer):
        """Hook reporting its connections, so they can be cleaned up again."""

        def on_opacity(event):
            notified.append(copied_layer)

        orig_layer.events.opacity.connect(on_opacity)
        return [(orig_layer.events.opacity, on_opacity)]

    m.register_layer_hook(Labels, hook)

    def bump_opacity():
        notified.clear()
        labels.opacity = 0.5 if labels.opacity != 0.5 else 0.6
        return len(notified)

    assert "undo" not in labels.__dict__
    assert bump_opacity() == 0  # no views yet

    for _ in range(3):
        show_orthogonal_views(viewer)
        qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

        # exactly one live hook connection per orthogonal view, never more
        assert bump_opacity() == 2
        assert "undo" in labels.__dict__  # undo/redo wrapped for syncing

        m.hide()

        # ... and nothing left behind afterwards, however often this is repeated
        assert bump_opacity() == 0
        assert "undo" not in labels.__dict__

    m.cleanup()


def test_points_selection_sync(make_napari_viewer, qtbot):
    """Point ``selected_data`` must sync as a whole set in order to work for both single
    and multi-selections.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    points = Points([[1, 1], [2, 2], [3, 3], [4, 4]])
    viewer.add_layer(points)

    right = m.right_widget.vm_container.viewer_model.layers[0]
    bottom = m.bottom_widget.vm_container.viewer_model.layers[0]

    def selections():
        return (
            set(points.selected_data),
            set(right.selected_data),
            set(bottom.selected_data),
        )

    # single selection on the main viewer
    points.selected_data = {1}
    assert selections() == ({1}, {1}, {1})

    # multi selection on the main viewer (this is what used to be dropped)
    points.selected_data = {0, 2, 3}
    assert selections() == ({0, 2, 3}, {0, 2, 3}, {0, 2, 3})

    # multi selection made in an ortho view propagates back to the main viewer
    right.selected_data = {0, 1}
    assert selections() == ({0, 1}, {0, 1}, {0, 1})

    bottom.selected_data = {1, 2, 3}
    assert selections() == ({1, 2, 3}, {1, 2, 3}, {1, 2, 3})

    # clearing the selection also syncs
    points.selected_data = set()
    assert selections() == (set(), set(), set())

    m.cleanup()


def test_labels_undo_redo_sync(make_napari_viewer, qtbot):
    """Undo/redo on a Labels layer must sync the data across the main viewer and
    both ortho views.

    Undo/redo do not emit a paint/data event, so the plugin wraps the layers'
    ``undo``/``redo`` methods to explicitly re-sync via ``_update_data``. This test
    exercises that path in both directions (main -> ortho and ortho -> main).
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    labels = Labels(np.zeros((20, 20, 20), dtype=np.uint8))
    viewer.add_layer(labels)

    right = m.right_widget.vm_container.viewer_model.layers[0]
    bottom = m.bottom_widget.vm_container.viewer_model.layers[0]

    def counts():
        """Number of voxels equal to 5 in each of the main view and ortho views."""
        return (
            int((labels.data == 5).sum()),
            int((right.data == 5).sum()),
            int((bottom.data == 5).sum()),
        )

    assert counts() == (0, 0, 0)

    # An undoable edit on the main layer syncs to both ortho views.
    idx = (np.array([5, 6, 7]), np.array([5, 6, 7]), np.array([5, 6, 7]))
    labels.data_setitem(idx, 5)
    assert counts() == (3, 3, 3)

    # Undo on the main layer reverts the edit everywhere.
    labels.undo()
    assert counts() == (0, 0, 0)

    # Redo on the main layer restores the edit everywhere.
    labels.redo()
    assert counts() == (3, 3, 3)

    # Reverse direction: an undoable edit made on an ortho view layer, then
    # undone from that same ortho layer, must also sync back to the main viewer.
    right.data_setitem(
        (np.array([1, 2, 3]), np.array([1, 2, 3]), np.array([1, 2, 3])), 5
    )
    assert counts() == (6, 6, 6)

    right.undo()
    assert counts() == (3, 3, 3)

    m.cleanup()


def test_paint_makes_every_other_view_redraw(make_napari_viewer, qtbot):
    """Painting in one view must make all the other views redraw.

    The copied layers hold the very same array as the original, so comparing their data
    cannot tell whether the others were notified -- an edit is visible in all of them
    either way. Count their redraws instead. This matters because ``_update_data`` skips
    re-assigning an array a layer already holds, and has to emit the data event itself
    for the remaining views to hear about the edit.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    labels = Labels(np.zeros((20, 20, 20), dtype=np.uint8))
    viewer.add_layer(labels)

    right = m.right_widget.vm_container.viewer_model.layers[0]
    bottom = m.bottom_widget.vm_container.viewer_model.layers[0]
    assert right.data is labels.data
    assert bottom.data is labels.data

    redraws = collections.Counter()
    for name, layer in (
        ("main", labels),
        ("right", right),
        ("bottom", bottom),
    ):
        layer.events.set_data.connect(
            lambda event, name=name: redraws.update([name])
        )

    # painting in the main viewer reaches both ortho views
    labels.data_setitem((np.array([5]), np.array([5]), np.array([5])), 5)
    assert redraws["right"] > 0
    assert redraws["bottom"] > 0

    # painting in one ortho view reaches the main viewer and the *other* ortho view
    redraws.clear()
    right.data_setitem((np.array([6]), np.array([6]), np.array([6])), 5)
    assert redraws["main"] > 0
    assert redraws["bottom"] > 0

    m.cleanup()


def test_layer_hook(make_napari_viewer, qtbot):
    """Test setting optional custom layer hooks. This is to forward specific
    events/outcomes to the original layer (could be a subclass) for further downstream
    processing. In this test, a function is created that captures a click event on the
    copied layer and changes a value on the original layer.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    assert isinstance(m.right_widget, OrthoViewWidget)

    # Test whether we can elicit a response on the source layer by clicking on a copied
    # layer.
    def test_hook(orig_layer: Labels, copied_layer: Labels):

        # define the click behavior the layer should respond to
        def click(orig_layer, layer, event):

            if isinstance(layer, Labels):
                label = layer.get_value(
                    event.position,
                    view_direction=event.view_direction,
                    dims_displayed=event.dims_displayed,
                    world=True,
                )

                # update the selected label to the value that was clicked on
                orig_layer.selected_label = label

        # Wrap and attach click callback
        def click_wrapper(layer, event):
            return click(orig_layer, layer, event)

        copied_layer.mouse_drag_callbacks.append(click_wrapper)

    m.register_layer_hook(Labels, test_hook)

    # test labels layer
    labels = Labels(np.zeros((50, 50, 50), dtype=np.uint8))
    labels.name = "test_labels_layer"
    labels.data[10:20, 10:20, 10:20] = 5
    labels.data[25:30, 30:40, 30:40] = 10
    viewer.add_layer(labels)

    assert (
        "test_labels_layer" in m.right_widget.vm_container.viewer_model.layers
    )
    assert labels.data[15, 15, 15] == 5
    assert (
        m.right_widget.vm_container.viewer_model.layers[0].data[15, 15, 15]
        == 5
    )
    m.right_widget.vm_container.viewer_model.dims.current_step = (
        15,
        15,
        15,
    )  # to refresh

    # pretend to click on m.right_widget.vm_container.viewer_model.layers[0]
    class DummyEvent:
        def __init__(
            self,
            position,
            view_direction=None,
            dims_displayed=None,
            world: bool = True,
        ):
            self.position = position
            self.view_direction = view_direction
            self.dims_displayed = dims_displayed
            self.world = False

    # Find the click_wrapper callback
    for cb in m.right_widget.vm_container.viewer_model.layers[
        0
    ].mouse_drag_callbacks:
        if hasattr(cb, "__name__") and cb.__name__ == "click_wrapper":
            callback = cb
            break

    # Simulate a click at a known label position
    event = DummyEvent(
        position=(15, 15, 15),
        view_direction=None,
        dims_displayed=list(
            m.right_widget.vm_container.viewer_model.dims.displayed
        ),
        world=True,
    )
    callback(m.right_widget.vm_container.viewer_model.layers[0], event)

    # Now the original layer's selected_label should be 5
    assert viewer.layers[0].selected_label == 5

    m.cleanup()


def crosshair_position(qt_viewer):
    """Return the world position the crosshair visual is currently drawn at."""

    for overlay, visual in qt_viewer.canvas._overlay_to_visual.items():
        if isinstance(overlay, CrosshairOverlay):
            visual = visual[0] if isinstance(visual, list) else visual
            return np.asarray(visual.node._pos)[0]
    raise AssertionError("no crosshair overlay on this canvas")


def test_ortho_views_open_where_the_main_viewer_is_looking(
    make_napari_viewer, qtbot
):
    """Showing the orthogonal views must position them, and the crosshairs, sensibly.

    The copied layers are inserted into the viewer models directly, which skips the
    positioning napari does when a first layer is added, and the crosshair visual starts
    at the world origin until a step event moves it. Together that opened the orthogonal
    views on an arbitrary (usually empty) slice with the crosshairs in a corner.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)

    labels = Labels(np.zeros((5, 30, 40, 50), dtype=np.uint8))
    viewer.add_layer(labels)

    # navigate the sliders somewhere specific first
    viewer.dims.current_step = (3, 7, 0, 0)
    before = list(viewer.dims.current_step)

    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # the displayed axes are centred on the data ...
    dims = viewer.dims
    for axis in dims.displayed:
        assert dims.current_step[axis] == int((dims.nsteps[axis] - 1) / 2)

    # ... while the sliders stay where the user left them
    for axis in dims.not_displayed:
        assert dims.current_step[axis] == before[axis]

    # both orthogonal views look at the same position as the main viewer
    for widget in (m.right_widget, m.bottom_widget):
        assert widget.vm_container.viewer_model.dims.point == dims.point

    # and every crosshair is drawn there, not at the world origin
    m.set_cross_hairs(True)
    for qt_viewer in (
        viewer.window._qt_viewer,
        m.right_widget.qt_viewer,
        m.bottom_widget.qt_viewer,
    ):
        assert not np.allclose(crosshair_position(qt_viewer)[:2], 0)

    m.cleanup()


def test_register_layer_hook_is_idempotent(make_napari_viewer, qtbot):
    """Registering the same hook twice must not run it twice per layer.

    The manager is cached per viewer, so an application's setup routine can easily run
    again for the same viewer.
    """

    viewer = make_napari_viewer()
    m = _get_manager(viewer)

    calls = []

    def hook(orig_layer, copied_layer):
        calls.append(copied_layer)

    m.register_layer_hook(Labels, hook)
    m.register_layer_hook(Labels, hook)
    assert m._layer_hooks[Labels] == [hook]

    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)
    viewer.add_layer(Labels(np.zeros((5, 20, 20), dtype=np.uint8)))

    assert len(calls) == 2  # once per orthogonal view, not twice

    m.cleanup()


def test_crosshair_overlay_visibility(make_napari_viewer, qtbot):
    """Regression test: setting the crosshair overlay visible must not crash with
    TypeError when napari passes font_manager to VispyCrosshairOverlay.__init__.

    napari 0.7.0 changed _add_viewer_overlay to pass font_manager to all
    overlays. VispyCrosshairOverlay did not accept **kwargs, causing a crash
    whenever the CrosshairOverlay's .visible was set to True (which triggers
    lazy initialization of the vispy overlay via create_vispy_overlay).

    The CrosshairOverlay lives in the ortho viewer model's _overlays, so the
    crash is triggered by setting visible on the ortho viewer's crosshair_overlay —
    not on the main viewer, which has no CrosshairOverlay.
    """
    viewer = make_napari_viewer()
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    # Triggering visible=True on the ortho viewer's CrosshairOverlay causes
    # napari 0.7.0 to call create_vispy_overlay(font_manager=...) for it,
    # which crashes because VispyCrosshairOverlay.__init__ has no **kwargs.
    m.right_widget.vm_container.crosshair_overlay.visible = True
    m.right_widget.vm_container.crosshair_overlay.visible = False

    m.cleanup()
