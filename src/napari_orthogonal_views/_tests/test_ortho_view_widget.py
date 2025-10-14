import numpy as np
from napari.layers import Image, Labels

from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    show_orthogonal_views,
)
from napari_orthogonal_views.ortho_view_widget import OrthoViewWidget


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

    assert viewer.layers[0].visible is False
    assert m.right_widget.vm_container.viewer_model.layers[0].visible is False
    assert m.bottom_widget.vm_container.viewer_model.layers[0].visible is False
    assert viewer.layers[1].opacity == 0.5
    assert m.right_widget.vm_container.viewer_model.layers[1].opacity == 0.5
    assert m.bottom_widget.vm_container.viewer_model.layers[1].opacity == 0.5
    assert viewer.layers[1].contour == 1
    assert m.right_widget.vm_container.viewer_model.layers[1].contour == 1
    assert m.bottom_widget.vm_container.viewer_model.layers[1].contour == 1

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
