from qtpy.QtCore import Qt
from qtpy.QtGui import QImage, QPixmap
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt import QLabeledRangeSlider


class ScreenRecorderWidget(QWidget):
    """Widget to control screen recording of main view and orthogonal views"""

    def __init__(
        self,
        ndim: int = 3,
        screenshot_callback=None,
        screenrecord_callback=None,
        axis_length_callback=None,
    ):
        super().__init__()

        # callbacks for screenshot and screen record functions
        self.screenshot_callback = screenshot_callback
        self.screenrecord_callback = screenrecord_callback

        # callback returning the number of slices along a given axis
        self.axis_length_callback = axis_length_callback

        # Choose the views to include
        view_group = QGroupBox("Views to include")
        self.right_view = QCheckBox("Right")
        self.right_view.setChecked(True)
        self.bottom_view = QCheckBox("Bottom")
        self.bottom_view.setChecked(True)
        view_layout = QHBoxLayout()
        view_layout.addWidget(self.right_view)
        view_layout.addWidget(self.bottom_view)
        view_group.setLayout(view_layout)

        # Screenshot controls
        screenshot_group = QGroupBox("Screenshot")
        to_clipboard = QPushButton("Copy to clipboard")
        to_clipboard.clicked.connect(self.copy_to_clipboard)
        save_btn = QPushButton("Save...")
        save_btn.clicked.connect(self.save_screenshot)
        screenshot_layout = QHBoxLayout()
        screenshot_layout.addWidget(to_clipboard)
        screenshot_layout.addWidget(save_btn)
        screenshot_group.setLayout(screenshot_layout)

        # Screen recorder controls
        recorder_group = QGroupBox("Screen recording")

        # Axis to slide along
        self.moving_axis = QComboBox()
        self.moving_axis.addItems([str(i) for i in range(ndim)])
        self.moving_axis.currentTextChanged.connect(self.update_slice_range)
        moving_axis_layout = QHBoxLayout()
        moving_axis_layout.addWidget(QLabel("Moving axis"))
        moving_axis_layout.addWidget(self.moving_axis)

        # Range of slices to record along the moving axis (inclusive)
        self.slice_range = QLabeledRangeSlider(Qt.Horizontal)
        self.slice_range.setRange(0, 0)
        self.slice_range.setValue((0, 0))
        slice_range_layout = QHBoxLayout()
        slice_range_layout.addWidget(QLabel("Slice range"))
        slice_range_layout.addWidget(self.slice_range)
        self.update_slice_range()

        # Timestamp options
        time_stamp_layout = QHBoxLayout()
        self.incl_timestamp = QCheckBox("Include timestamp")
        self.incl_timestamp.setChecked(False)
        time_stamp_layout.addWidget(self.incl_timestamp)
        self.incl_timestamp.toggled.connect(self.toggle_timestamp_options)

        # Start, step and suffix (show only when a time stamp is included)
        timestamp_options_layout = QVBoxLayout()
        self.time_start = QDoubleSpinBox()
        self.time_start.setRange(-1e6, 1e6)
        self.time_start.setValue(0)
        self.time_start.setToolTip(
            "Timestamp of the first to be recorded frame of the moving axis"
        )
        time_start_layout = QHBoxLayout()
        time_start_layout.addWidget(QLabel("Start from"))
        time_start_layout.addWidget(self.time_start)
        self.time_step = QDoubleSpinBox()
        self.time_step.setRange(0.01, 100)
        self.time_step.setValue(1)
        time_step_layout = QHBoxLayout()
        time_step_layout.addWidget(QLabel("Time step"))
        time_step_layout.addWidget(self.time_step)
        self.suffix = QLineEdit("hrs")
        suffix_layout = QHBoxLayout()
        suffix_layout.addWidget(QLabel("Suffix"))
        suffix_layout.addWidget(self.suffix)
        timestamp_options_layout.addLayout(time_start_layout)
        timestamp_options_layout.addLayout(time_step_layout)
        timestamp_options_layout.addLayout(suffix_layout)
        self.timestamp_options_widget = QWidget()
        self.timestamp_options_widget.setLayout(timestamp_options_layout)
        self.timestamp_options_widget.setVisible(False)

        # Frames per second option
        frames_per_second_layout = QHBoxLayout()
        self.fps_spinbox = QSpinBox()
        self.fps_spinbox.setRange(1, 60)
        self.fps_spinbox.setValue(7)
        frames_per_second_layout.addWidget(QLabel("FPS"))
        frames_per_second_layout.addWidget(self.fps_spinbox)

        # Record button
        record_btn = QPushButton("Record")
        record_btn.clicked.connect(self.record)

        # Assemble everything
        recorder_layout = QVBoxLayout()
        recorder_layout.addLayout(moving_axis_layout)
        recorder_layout.addLayout(slice_range_layout)
        recorder_layout.addLayout(time_stamp_layout)
        recorder_layout.addWidget(self.timestamp_options_widget)
        recorder_layout.addLayout(frames_per_second_layout)
        recorder_layout.addWidget(record_btn)
        recorder_group.setLayout(recorder_layout)

        layout = QVBoxLayout()
        layout.addWidget(view_group)
        layout.addWidget(screenshot_group)
        layout.addWidget(recorder_group)
        widget = QWidget()
        widget.setLayout(layout)

        # Add it to a scrollable area to allow resizing of the napari viewers
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(widget)
        scroll_layout = QVBoxLayout()
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addWidget(scroll_area)
        self.setLayout(scroll_layout)

    def update_slice_range(self):
        """Span the slice range slider over the full extent of the moving axis"""

        axis = self.moving_axis.currentText()
        if self.axis_length_callback is None or axis == "":
            return

        n_slices = int(self.axis_length_callback(int(axis)))
        last_slice = max(n_slices - 1, 0)
        self.slice_range.setRange(0, last_slice)
        self.slice_range.setValue((0, last_slice))

    def toggle_timestamp_options(self, checked):
        """Show/hide the start, time step and suffix inputs based on whether a timestamp
        is included"""

        self.timestamp_options_widget.setVisible(checked)

    def copy_to_clipboard(self):
        """Copy current view as screenshot to clipboard"""

        if self.screenshot_callback:
            screenshot = self.screenshot_callback(
                path=None,
                include_right=self.right_view.isChecked(),
                include_bottom=self.bottom_view.isChecked(),
            )
            height, width, _ = screenshot.shape
            bytes_per_line = 4 * width
            q_image = QImage(
                screenshot.data,
                width,
                height,
                bytes_per_line,
                QImage.Format_RGBA8888,
            )
            clipboard = QApplication.clipboard()
            clipboard.setPixmap(QPixmap.fromImage(q_image))

    def save_screenshot(self):
        """Save current view as screenshot to file"""

        path = QFileDialog.getSaveFileName(
            self,
            "Save screenshot",
            filter="PNG files (*.png);;All files (*.*)",
        )
        if path[0] and self.screenshot_callback:
            self.screenshot_callback(
                path=path[0],
                include_right=self.right_view.isChecked(),
                include_bottom=self.bottom_view.isChecked(),
            )

    def record(self):
        """Move along the specified axis and record the orthogonal views as a video"""

        moving_axis = int(self.moving_axis.currentText())
        include_right = self.right_view.isChecked()
        include_bottom = self.bottom_view.isChecked()
        first_slice, last_slice = (int(v) for v in self.slice_range.value())

        path = QFileDialog.getSaveFileName(
            self,
            "Save screen recording",
            filter="AVI files (*.avi);;All files (*.*)",
        )
        if path[0]:
            print(
                f"Recording along axis {moving_axis} from slice {first_slice} to {last_slice}"
            )
            if self.screenrecord_callback:
                fps = self.fps_spinbox.value()
                incl_timestamp = self.incl_timestamp.isChecked()
                time_start = self.time_start.value()
                time_step = self.time_step.value()
                suffix = self.suffix.text()
                self.screenrecord_callback(
                    path=path[0],
                    axis=moving_axis,
                    incl_right=include_right,
                    incl_bottom=include_bottom,
                    fps=fps,
                    incl_timestamp=incl_timestamp,
                    start=time_start,
                    step=time_step,
                    suffix=suffix,
                    first_slice=first_slice,
                    last_slice=last_slice,
                )
