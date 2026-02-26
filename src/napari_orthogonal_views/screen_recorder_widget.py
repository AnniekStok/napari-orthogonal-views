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


class ScreenRecorderWidget(QWidget):
    """Widget to control screen recording of main view and orthogonal views"""

    def __init__(self, screenshot_callback=None, screenrecord_callback=None):
        super().__init__()

        # callbacks for screenshot and screen record functions
        self.screenshot_callback = screenshot_callback
        self.screenrecord_callback = screenrecord_callback

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
        self.moving_axis.addItems(["0"])
        moving_axis_layout = QHBoxLayout()
        moving_axis_layout.addWidget(QLabel("Moving axis"))
        moving_axis_layout.addWidget(self.moving_axis)

        # Timestamp options
        time_stamp_layout = QHBoxLayout()
        self.incl_timestamp = QCheckBox("Include timestamp")
        self.incl_timestamp.setChecked(False)
        time_stamp_layout.addWidget(self.incl_timestamp)
        self.incl_timestamp.toggled.connect(self.toggle_time_step_and_suffix)

        # Step and suffix (show only when a time stamp is included)
        time_step_and_suffix_layout = QVBoxLayout()
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
        time_step_and_suffix_layout.addLayout(time_step_layout)
        time_step_and_suffix_layout.addLayout(suffix_layout)
        self.time_step_and_suffix_widget = QWidget()
        self.time_step_and_suffix_widget.setLayout(time_step_and_suffix_layout)
        self.time_step_and_suffix_widget.setVisible(False)

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
        recorder_layout.addLayout(time_stamp_layout)
        recorder_layout.addWidget(self.time_step_and_suffix_widget)
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
        scroll_layout.addWidget(scroll_area)
        self.setLayout(scroll_layout)

    def toggle_time_step_and_suffix(self, checked):
        """Enable/disable time step and suffix inputs based on whether timestamp is included"""

        self.time_step_and_suffix_widget.setVisible(checked)

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

        path = QFileDialog.getSaveFileName(
            self,
            "Save screen recording",
            filter="AVI files (*.avi);;All files (*.*)",
        )
        if path[0]:
            print(f"Recording along axis {moving_axis}")
            if self.screenrecord_callback:
                fps = self.fps_spinbox.value()
                incl_timestamp = self.incl_timestamp.isChecked()
                time_step = self.time_step.value()
                suffix = self.suffix.text()
                self.screenrecord_callback(
                    path=path[0],
                    axis=moving_axis,
                    incl_right=include_right,
                    incl_bottom=include_bottom,
                    fps=fps,
                    incl_timestamp=incl_timestamp,
                    step=time_step,
                    suffix=suffix,
                )
