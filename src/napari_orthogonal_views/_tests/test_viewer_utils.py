from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from napari.components.viewer_model import ViewerModel
from napari.utils.key_bindings import KeymapHandler
from qtpy.QtCore import QPoint
from qtpy.QtWidgets import QWidget

from napari_orthogonal_views import viewer_utils
from napari_orthogonal_views.ortho_view_manager import (
    _get_manager,
    init_actions,
    show_orthogonal_views,
)
from napari_orthogonal_views.viewer_utils import (
    center_cross_on_mouse,
    register_canvas,
    viewer_model_under_mouse,
)


@pytest.fixture
def hoverable_canvases(qtbot):
    """Two visible widgets standing in for canvases, each with its own viewer model.

    Registers them through register_canvas, and unregisters them again so the module
    level registry does not leak into other tests.
    """

    canvases = []
    models = []
    # side by side, so that a point inside one is outside the other
    for x, cursor_position in [(0, (1, 2, 3)), (300, (7, 8, 9))]:
        canvas = QWidget()
        qtbot.addWidget(canvas)
        canvas.setGeometry(x, 0, 100, 100)
        canvas.show()
        qtbot.waitExposed(canvas)

        model = ViewerModel()
        model.add_image(np.zeros((10, 10, 10), dtype=np.uint8))
        model.cursor.position = cursor_position

        # stand in for the QtViewer, which register_canvas only reaches into for
        # its canvas widget and its viewer model
        register_canvas(
            SimpleNamespace(
                canvas=SimpleNamespace(native=canvas), viewer=model
            )
        )
        canvases.append(canvas)
        models.append(model)

    yield canvases, models

    for canvas in canvases:
        viewer_utils._HOVERABLE_CANVASES.pop(canvas, None)


def hover(canvas: QWidget | None):
    """Patch the global cursor position onto the centre of ``canvas``, or far outside
    every canvas when it is None."""

    pos = (
        canvas.mapToGlobal(canvas.rect().center())
        if canvas is not None
        else QPoint(-10000, -10000)
    )
    return patch.object(viewer_utils.QCursor, "pos", staticmethod(lambda: pos))


def test_hovered_canvas_resolves_to_its_viewer_model(hoverable_canvases):
    """The hovered canvas determines the viewer model, regardless of focus."""

    canvases, models = hoverable_canvases

    for canvas, model in zip(canvases, models, strict=True):
        with hover(canvas):
            assert viewer_model_under_mouse() is model

    with hover(None):
        assert viewer_model_under_mouse() is None


def test_center_cross_uses_hovered_model_not_dispatching_model(
    hoverable_canvases,
):
    """Centering follows the mouse, not the model that received the key press.

    Reproduces the case where a widget outside the canvas holds the focus, so napari's
    main window forwards 'T' to the main viewer while the mouse is over an orthogonal
    view: the hovered model has to be stepped, and the dispatching one left alone.
    """

    canvases, models = hoverable_canvases
    dispatching_model, hovered_model = models
    dispatching_step = tuple(dispatching_model.dims.current_step)

    with hover(canvases[1]):
        center_cross_on_mouse(dispatching_model)

    # cursor position (7, 8, 9) on a unit-step range maps onto that same step
    assert tuple(hovered_model.dims.current_step) == (7, 8, 9)
    assert tuple(dispatching_model.dims.current_step) == dispatching_step


def test_center_cross_is_a_noop_off_canvas(hoverable_canvases):
    """Without a canvas under the mouse there is no position to center on, so nothing
    moves rather than jumping to a stale one."""

    _, models = hoverable_canvases
    steps_before = [tuple(model.dims.current_step) for model in models]

    with hover(None):
        center_cross_on_mouse(models[0])

    assert [tuple(model.dims.current_step) for model in models] == steps_before


def test_shortcut_dispatches_through_the_napari_keymap(hoverable_canvases):
    """Pressing T reaches center_cross_on_mouse with the signature napari calls it with.

    napari binds the action onto its keymap provider with types.MethodType, so the
    command is always handed the viewer model that received the key press even though it
    is unused. Dropping it from the signature raises a TypeError on every press, which no
    other test would catch.
    """

    canvases, models = hoverable_canvases
    init_actions()

    handler = KeymapHandler()
    handler.keymap_providers = [models[0]]

    with hover(canvases[1]):
        handler.press_key("T")

    assert tuple(models[1].dims.current_step) == (7, 8, 9)


def test_ortho_view_canvases_are_registered(make_napari_viewer, qtbot):
    """Showing the orthogonal views registers every canvas, so the shortcut can be
    routed to whichever one the mouse is over."""

    viewer = make_napari_viewer()
    viewer.add_image(np.zeros((10, 10, 10), dtype=np.uint8))
    m = _get_manager(viewer)
    show_orthogonal_views(viewer)
    qtbot.waitUntil(lambda: m.is_shown(), timeout=1000)

    registered = set(viewer_utils._HOVERABLE_CANVASES.values())
    assert viewer in registered
    assert m.right_widget.vm_container.viewer_model in registered
    assert m.bottom_widget.vm_container.viewer_model in registered
