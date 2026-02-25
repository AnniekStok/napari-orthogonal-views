from unittest.mock import MagicMock, Mock, patch

import numpy as np
from qtpy.QtWidgets import QApplication

from napari_orthogonal_views.screen_recorder_widget import ScreenRecorderWidget


def test_screen_recorder_widget_initialization(qtbot):
    """Test that ScreenRecorderWidget initializes correctly."""
    widget = ScreenRecorderWidget()
    qtbot.addWidget(widget)

    assert widget.screenshot_callback is None
    assert widget.screenrecord_callback is None
    assert widget.right_view.isChecked()
    assert widget.bottom_view.isChecked()
    assert not widget.incl_timestamp.isChecked()
    assert widget.fps_spinbox.value() == 7
    assert widget.time_step.value() == 1.0
    assert widget.suffix.text() == "hrs"


def test_screen_recorder_widget_with_callbacks(qtbot):
    """Test that ScreenRecorderWidget accepts callbacks."""
    screenshot_cb = Mock()
    screenrecord_cb = Mock()

    widget = ScreenRecorderWidget(
        screenshot_callback=screenshot_cb,
        screenrecord_callback=screenrecord_cb,
    )
    qtbot.addWidget(widget)

    assert widget.screenshot_callback is screenshot_cb
    assert widget.screenrecord_callback is screenrecord_cb


def test_copy_to_clipboard_with_callback(qtbot):
    """Test copy_to_clipboard calls screenshot callback and sets clipboard."""
    screenshot_array = np.random.randint(0, 255, (100, 100, 4), dtype=np.uint8)
    screenshot_cb = Mock(return_value=screenshot_array)

    widget = ScreenRecorderWidget(screenshot_callback=screenshot_cb)

    # Mock the clipboard
    with patch.object(QApplication, "clipboard") as mock_clipboard:
        mock_clip = MagicMock()
        mock_clipboard.return_value = mock_clip

        widget.copy_to_clipboard()

        # Verify callback was called with correct parameters
        screenshot_cb.assert_called_once_with(
            path=None, include_right=True, include_bottom=True
        )

        # Verify clipboard was updated
        mock_clip.setPixmap.assert_called_once()


def test_save_screenshot_with_callback(qtbot):
    """Test save_screenshot calls screenshot callback with file path."""
    screenshot_cb = Mock()

    widget = ScreenRecorderWidget(screenshot_callback=screenshot_cb)
    widget.right_view.setChecked(True)
    widget.bottom_view.setChecked(False)

    test_path = "/tmp/test_screenshot.png"

    with patch(
        "napari_orthogonal_views.screen_recorder_widget.QFileDialog.getSaveFileName"
    ) as mock_dialog:
        mock_dialog.return_value = (test_path, "PNG files (*.png)")

        widget.save_screenshot()

        # Verify callback was called with correct parameters
        screenshot_cb.assert_called_once_with(
            path=test_path, include_right=True, include_bottom=False
        )


def test_save_screenshot_cancelled(qtbot):
    """Test save_screenshot handles cancelled dialog."""
    screenshot_cb = Mock()

    widget = ScreenRecorderWidget(screenshot_callback=screenshot_cb)

    with patch(
        "napari_orthogonal_views.screen_recorder_widget.QFileDialog.getSaveFileName"
    ) as mock_dialog:
        mock_dialog.return_value = ("", "")

        widget.save_screenshot()

        # Verify callback was not called
        screenshot_cb.assert_not_called()


def test_record_with_callback(qtbot):
    """Test record calls screenrecord callback with correct parameters."""
    screenrecord_cb = Mock()

    widget = ScreenRecorderWidget(screenrecord_callback=screenrecord_cb)
    widget.moving_axis.clear()
    widget.moving_axis.addItems(["0", "1", "2"])
    widget.moving_axis.setCurrentIndex(1)
    widget.right_view.setChecked(True)
    widget.bottom_view.setChecked(False)
    widget.fps_spinbox.setValue(15)
    widget.incl_timestamp.setChecked(True)
    widget.time_step.setValue(2.5)
    widget.suffix.setText("min")

    test_path = "/tmp/test_video.avi"

    with patch(
        "napari_orthogonal_views.screen_recorder_widget.QFileDialog.getSaveFileName"
    ) as mock_dialog:
        mock_dialog.return_value = (test_path, "AVI files (*.avi)")

        widget.record()

        # Verify callback was called with correct parameters
        screenrecord_cb.assert_called_once_with(
            path=test_path,
            axis=1,
            incl_right=True,
            incl_bottom=False,
            fps=15,
            incl_timestamp=True,
            step=2.5,
            suffix="min",
        )


def test_record_cancelled(qtbot):
    """Test record handles cancelled dialog."""
    screenrecord_cb = Mock()

    widget = ScreenRecorderWidget(screenrecord_callback=screenrecord_cb)

    with patch(
        "napari_orthogonal_views.screen_recorder_widget.QFileDialog.getSaveFileName"
    ) as mock_dialog:
        mock_dialog.return_value = ("", "")

        widget.record()

        # Verify callback was not called
        screenrecord_cb.assert_not_called()
