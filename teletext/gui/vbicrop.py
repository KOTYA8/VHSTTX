import multiprocessing as mp
import os
import sys


try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    QtCore = None
    QtGui = None
    QtWidgets = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None
    _APP = None


DEFAULT_FRAME_RATE = 25.0


def _ensure_app():
    global _APP
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1] or ['teletext-vbicrop'])
    _APP = app
    return app


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, int(value)))


def normalise_cut_ranges(cut_ranges, total_frames):
    total_frames = max(int(total_frames), 1)
    merged = []
    for start, end in sorted(
        (
            (_clamp(start, 0, total_frames - 1), _clamp(end, 0, total_frames - 1))
            for start, end in cut_ranges
        ),
        key=lambda item: item[0],
    ):
        if start > end:
            start, end = end, start
        if not merged or start > (merged[-1][1] + 1):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def count_cut_frames(cut_ranges):
    return sum((end - start) + 1 for start, end in cut_ranges)


def normalise_insertions(insertions, total_frames):
    total_frames = max(int(total_frames), 1)
    normalised = []
    for insertion in insertions:
        after_frame = _clamp(insertion['after_frame'], 0, total_frames - 1)
        normalised.append({
            'after_frame': after_frame,
            'path': insertion['path'],
            'frame_count': max(int(insertion['frame_count']), 0),
        })
    return tuple(sorted(normalised, key=lambda item: (item['after_frame'], item['path'])))


def count_inserted_frames(insertions):
    return sum(int(insertion['frame_count']) for insertion in insertions)


def selection_end_targets(start_frame, total_frames):
    total_frames = max(int(total_frames), 1)
    maximum = total_frames - 1
    start = _clamp(start_frame, 0, maximum)
    middle = start + ((maximum - start) // 2)
    return start, middle, maximum


class CropStateHandle:
    CURRENT_INDEX = 0
    PLAYING_INDEX = 1
    START_INDEX = 2
    END_INDEX = 3

    def __init__(self, shared_values, total_frames):
        self._shared_values = shared_values
        self.total_frames = max(int(total_frames), 1)

    def current_frame(self):
        return _clamp(self._shared_values[self.CURRENT_INDEX], 0, self.total_frames - 1)

    def set_current_frame(self, value):
        value = _clamp(value, 0, self.total_frames - 1)
        self._shared_values[self.CURRENT_INDEX] = value
        start, end = self.selection_range()
        if value < start:
            self.set_selection_range(value, end)
        elif value > end:
            self.set_selection_range(start, value)

    def is_playing(self):
        return bool(int(self._shared_values[self.PLAYING_INDEX]))

    def set_playing(self, playing):
        self._shared_values[self.PLAYING_INDEX] = 1 if playing else 0

    def selection_range(self):
        start = _clamp(self._shared_values[self.START_INDEX], 0, self.total_frames - 1)
        end = _clamp(self._shared_values[self.END_INDEX], 0, self.total_frames - 1)
        if start > end:
            start, end = end, start
        return start, end

    def set_selection_range(self, start, end):
        start = _clamp(start, 0, self.total_frames - 1)
        end = _clamp(end, 0, self.total_frames - 1)
        if start > end:
            start, end = end, start
        self._shared_values[self.START_INDEX] = start
        self._shared_values[self.END_INDEX] = end

    def jump_to_start(self):
        self.set_playing(False)
        self.set_current_frame(0)

    def jump_to_end(self):
        self.set_playing(False)
        self.set_current_frame(self.total_frames - 1)

    def step(self, delta):
        self.set_playing(False)
        self.set_current_frame(self.current_frame() + int(delta))

    def set_selection_to_current_start(self):
        _, end = self.selection_range()
        self.set_selection_range(self.current_frame(), end)

    def set_selection_to_current_end(self):
        start, _ = self.selection_range()
        self.set_selection_range(start, self.current_frame())

    def restore_state(self, current_frame, start_frame, end_frame, playing=False):
        current = _clamp(current_frame, 0, self.total_frames - 1)
        start = _clamp(start_frame, 0, self.total_frames - 1)
        end = _clamp(end_frame, 0, self.total_frames - 1)
        if start > end:
            start, end = end, start
        self._shared_values[self.CURRENT_INDEX] = current
        self._shared_values[self.START_INDEX] = start
        self._shared_values[self.END_INDEX] = end
        self._shared_values[self.PLAYING_INDEX] = 1 if playing else 0


def create_crop_state(total_frames, current_frame=0, playing=False, start_frame=0, end_frame=None):
    total_frames = max(int(total_frames), 1)
    if end_frame is None:
        end_frame = total_frames - 1
    ctx = mp.get_context('spawn')
    shared_values = ctx.Array(
        'q',
        [
            _clamp(current_frame, 0, total_frames - 1),
            1 if playing else 0,
            _clamp(start_frame, 0, total_frames - 1),
            _clamp(end_frame, 0, total_frames - 1),
        ],
        lock=False,
    )
    return CropStateHandle(shared_values, total_frames)


if IMPORT_ERROR is None:
    class FrameRangeSlider(QtWidgets.QWidget):
        rangeChanged = QtCore.pyqtSignal(int, int)

        def __init__(self, minimum=0, maximum=100, start=0, end=100, parent=None):
            super().__init__(parent)
            self._minimum = int(minimum)
            self._maximum = max(int(maximum), self._minimum)
            self._start = int(start)
            self._end = int(end)
            self._cuts = ()
            self._insert_markers = ()
            self._dragging = None
            self.setMinimumHeight(34)
            self.setMouseTracking(True)

        def minimum(self):
            return self._minimum

        def maximum(self):
            return self._maximum

        def setRange(self, minimum, maximum):
            self._minimum = int(minimum)
            self._maximum = max(int(maximum), self._minimum)
            self.setValues(self._start, self._end)

        def values(self):
            return self._start, self._end

        def setValues(self, start, end):
            start = _clamp(start, self._minimum, self._maximum)
            end = _clamp(end, self._minimum, self._maximum)
            if start > end:
                start, end = end, start
            changed = (start != self._start) or (end != self._end)
            self._start = start
            self._end = end
            self.update()
            if changed:
                self.rangeChanged.emit(self._start, self._end)

        def setCuts(self, cut_ranges):
            self._cuts = tuple(cut_ranges)
            self.update()

        def setInsertMarkers(self, markers):
            self._insert_markers = tuple(markers)
            self.update()

        def _handle_rect(self, value):
            margin = 12
            usable = max(self.width() - (margin * 2), 1)
            ratio = 0.0 if self._maximum == self._minimum else (value - self._minimum) / (self._maximum - self._minimum)
            center_x = margin + int(ratio * usable)
            return QtCore.QRect(center_x - 6, 6, 12, self.height() - 12)

        def _value_from_pos(self, x):
            margin = 12
            usable = max(self.width() - (margin * 2), 1)
            ratio = (x - margin) / usable
            ratio = max(0.0, min(1.0, ratio))
            return int(round(self._minimum + ((self._maximum - self._minimum) * ratio)))

        def paintEvent(self, event):  # pragma: no cover - GUI path
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            track_rect = QtCore.QRect(12, (self.height() // 2) - 3, max(self.width() - 24, 1), 6)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor('#9a9a9a'))
            painter.drawRoundedRect(track_rect, 3, 3)

            painter.setBrush(QtGui.QColor('#d94b4b'))
            for start, end in self._cuts:
                start_rect = self._handle_rect(start)
                end_rect = self._handle_rect(end)
                cut_rect = QtCore.QRect(
                    start_rect.center().x(),
                    track_rect.y(),
                    max(end_rect.center().x() - start_rect.center().x(), 1),
                    track_rect.height(),
                )
                painter.drawRoundedRect(cut_rect, 3, 3)

            painter.setPen(QtGui.QPen(QtGui.QColor('#f39c12'), 2))
            for marker in self._insert_markers:
                marker_rect = self._handle_rect(marker)
                x = marker_rect.center().x()
                painter.drawLine(x, track_rect.y() - 5, x, track_rect.bottom() + 5)

            start_rect = self._handle_rect(self._start)
            end_rect = self._handle_rect(self._end)
            selected_rect = QtCore.QRect(
                start_rect.center().x(),
                track_rect.y(),
                max(end_rect.center().x() - start_rect.center().x(), 1),
                track_rect.height(),
            )
            painter.setBrush(QtGui.QColor('#3a84ff'))
            painter.drawRoundedRect(selected_rect, 3, 3)

            # Draw cut markers on top so they stay visible even when covered by the blue selection.
            painter.setPen(QtGui.QPen(QtGui.QColor('#c62828'), 2))
            painter.setBrush(QtGui.QBrush(QtGui.QColor('#c62828')))
            for start, end in self._cuts:
                start_marker = self._handle_rect(start).center().x()
                end_marker = self._handle_rect(end).center().x()
                painter.drawLine(start_marker, track_rect.y() - 8, start_marker, track_rect.bottom() + 8)
                painter.drawLine(end_marker, track_rect.y() - 8, end_marker, track_rect.bottom() + 8)
                painter.drawPolygon(
                    QtGui.QPolygon([
                        QtCore.QPoint(start_marker, track_rect.y() - 10),
                        QtCore.QPoint(start_marker - 4, track_rect.y() - 4),
                        QtCore.QPoint(start_marker + 4, track_rect.y() - 4),
                    ])
                )
                painter.drawPolygon(
                    QtGui.QPolygon([
                        QtCore.QPoint(end_marker, track_rect.bottom() + 10),
                        QtCore.QPoint(end_marker - 4, track_rect.bottom() + 4),
                        QtCore.QPoint(end_marker + 4, track_rect.bottom() + 4),
                    ])
                )

            for rect, color in ((start_rect, '#ffffff'), (end_rect, '#ffffff')):
                painter.setBrush(QtGui.QColor(color))
                painter.setPen(QtGui.QPen(QtGui.QColor('#444444')))
                painter.drawRoundedRect(rect, 3, 3)

        def mousePressEvent(self, event):  # pragma: no cover - GUI path
            if event.button() != QtCore.Qt.LeftButton:
                return
            start_rect = self._handle_rect(self._start)
            end_rect = self._handle_rect(self._end)
            if start_rect.contains(event.pos()):
                self._dragging = 'start'
                return
            if end_rect.contains(event.pos()):
                self._dragging = 'end'
                return
            value = self._value_from_pos(event.x())
            if abs(value - self._start) <= abs(value - self._end):
                self._dragging = 'start'
                self.setValues(value, self._end)
            else:
                self._dragging = 'end'
                self.setValues(self._start, value)

        def mouseMoveEvent(self, event):  # pragma: no cover - GUI path
            if self._dragging is None:
                return
            value = self._value_from_pos(event.x())
            if self._dragging == 'start':
                self.setValues(value, self._end)
            else:
                self.setValues(self._start, value)

        def mouseReleaseEvent(self, event):  # pragma: no cover - GUI path
            self._dragging = None


    class VBICropWindow(QtWidgets.QDialog):
        def __init__(self, state, total_frames, frame_rate=DEFAULT_FRAME_RATE, save_callback=None, live_tune_callback=None, viewer_process=None, frame_size_bytes=0, parent=None):
            super().__init__(parent)
            self._state = state
            self._total_frames = max(int(total_frames), 1)
            self._frame_rate = float(frame_rate)
            self._save_callback = save_callback
            self._live_tune_callback = live_tune_callback
            self._viewer_process = viewer_process
            self._frame_size_bytes = int(frame_size_bytes)
            self._updating = False
            self._history = []
            self._redo_history = []
            self._cut_ranges = ()
            self._insertions = ()

            self.setWindowTitle('VBI Crop')
            self.resize(760, 280)
            self.setMinimumWidth(680)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            self._status_label = QtWidgets.QLabel('')
            root.addWidget(self._status_label)

            timeline_group = QtWidgets.QGroupBox('Current Frame')
            timeline_layout = QtWidgets.QGridLayout(timeline_group)
            root.addWidget(timeline_group)

            self._frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self._frame_slider.setRange(0, self._total_frames - 1)
            self._frame_slider.valueChanged.connect(self._frame_slider_changed)
            timeline_layout.addWidget(self._frame_slider, 0, 0, 1, 4)

            timeline_layout.addWidget(QtWidgets.QLabel('Frame'), 1, 0)
            self._frame_box = QtWidgets.QSpinBox()
            self._frame_box.setRange(0, self._total_frames - 1)
            self._frame_box.valueChanged.connect(self._frame_box_changed)
            timeline_layout.addWidget(self._frame_box, 1, 1)

            timeline_layout.addWidget(QtWidgets.QLabel('Time'), 1, 2)
            self._frame_time_label = QtWidgets.QLabel('00:00.00')
            timeline_layout.addWidget(self._frame_time_label, 1, 3)

            controls_layout = QtWidgets.QHBoxLayout()
            root.addLayout(controls_layout)
            self._home_button = QtWidgets.QPushButton('|<')
            self._home_button.clicked.connect(self._jump_start)
            controls_layout.addWidget(self._home_button)
            self._prev_button = QtWidgets.QPushButton('<')
            self._prev_button.clicked.connect(lambda: self._step(-1))
            controls_layout.addWidget(self._prev_button)
            self._play_button = QtWidgets.QPushButton('Play')
            self._play_button.clicked.connect(self._toggle_play)
            controls_layout.addWidget(self._play_button)
            self._next_button = QtWidgets.QPushButton('>')
            self._next_button.clicked.connect(lambda: self._step(1))
            controls_layout.addWidget(self._next_button)
            self._end_button = QtWidgets.QPushButton('>|')
            self._end_button.clicked.connect(self._jump_end)
            controls_layout.addWidget(self._end_button)
            controls_layout.addStretch(1)

            selection_group = QtWidgets.QGroupBox('Selection')
            selection_layout = QtWidgets.QGridLayout(selection_group)
            selection_layout.setColumnStretch(4, 1)
            root.addWidget(selection_group)

            self._range_slider = FrameRangeSlider(0, self._total_frames - 1, 0, self._total_frames - 1)
            self._range_slider.rangeChanged.connect(self._range_slider_changed)
            selection_layout.addWidget(self._range_slider, 0, 0, 1, 10)

            selection_layout.addWidget(QtWidgets.QLabel('Start'), 1, 0)
            self._start_box = QtWidgets.QSpinBox()
            self._start_box.setRange(0, self._total_frames - 1)
            self._start_box.valueChanged.connect(self._range_box_changed)
            selection_layout.addWidget(self._start_box, 1, 1)

            selection_layout.addWidget(QtWidgets.QLabel('End'), 1, 2)
            self._end_box = QtWidgets.QSpinBox()
            self._end_box.setRange(0, self._total_frames - 1)
            self._end_box.valueChanged.connect(self._range_box_changed)
            selection_layout.addWidget(self._end_box, 1, 3)

            self._mark_start_button = QtWidgets.QPushButton('Mark Start')
            self._mark_start_button.clicked.connect(self._mark_start)
            selection_layout.addWidget(self._mark_start_button, 1, 5)

            self._mark_end_button = QtWidgets.QPushButton('Mark End')
            self._mark_end_button.clicked.connect(self._mark_end)
            selection_layout.addWidget(self._mark_end_button, 1, 6)

            self._delete_button = QtWidgets.QPushButton('Delete Selection')
            self._delete_button.clicked.connect(self._delete_selection)
            selection_layout.addWidget(self._delete_button, 1, 7)

            self._selection_start_button = QtWidgets.QPushButton('Sel Start')
            self._selection_start_button.clicked.connect(self._jump_selection_start)
            selection_layout.addWidget(self._selection_start_button, 2, 5)

            self._selection_mid_button = QtWidgets.QPushButton('Sel Mid')
            self._selection_mid_button.clicked.connect(self._jump_selection_middle)
            selection_layout.addWidget(self._selection_mid_button, 2, 6)

            self._selection_end_button = QtWidgets.QPushButton('Sel End')
            self._selection_end_button.clicked.connect(self._jump_selection_end)
            selection_layout.addWidget(self._selection_end_button, 2, 7)

            selection_layout.addWidget(QtWidgets.QLabel('Minutes'), 2, 0)
            self._duration_minutes_box = QtWidgets.QSpinBox()
            self._duration_minutes_box.setRange(0, int(self._total_frames / self._frame_rate) // 60 + 60)
            self._duration_minutes_box.setAccelerated(True)
            self._duration_minutes_box.valueChanged.connect(self._duration_changed)
            selection_layout.addWidget(self._duration_minutes_box, 2, 1)

            selection_layout.addWidget(QtWidgets.QLabel('Seconds'), 2, 2)
            self._duration_seconds_box = QtWidgets.QDoubleSpinBox()
            self._duration_seconds_box.setRange(0.04, 59.96)
            self._duration_seconds_box.setDecimals(2)
            self._duration_seconds_box.setSingleStep(0.04)
            self._duration_seconds_box.setAccelerated(True)
            self._duration_seconds_box.valueChanged.connect(self._duration_changed)
            selection_layout.addWidget(self._duration_seconds_box, 2, 3)

            self._selection_label = QtWidgets.QLabel('')
            root.addWidget(self._selection_label)
            self._size_label = QtWidgets.QLabel('')
            root.addWidget(self._size_label)
            self._edited_label = QtWidgets.QLabel('')
            root.addWidget(self._edited_label)
            self._insertions_label = QtWidgets.QLabel('')
            root.addWidget(self._insertions_label)

            button_row = QtWidgets.QHBoxLayout()
            root.addLayout(button_row)

            self._undo_button = QtWidgets.QPushButton('Undo')
            self._undo_button.clicked.connect(self._undo)
            button_row.addWidget(self._undo_button)

            self._redo_button = QtWidgets.QPushButton('Redo')
            self._redo_button.clicked.connect(self._redo)
            button_row.addWidget(self._redo_button)

            self._undo_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+Z'), self)
            self._undo_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
            self._undo_shortcut.activated.connect(self._undo)

            self._redo_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+X'), self)
            self._redo_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
            self._redo_shortcut.activated.connect(self._redo)

            self._reset_button = QtWidgets.QPushButton('Reset')
            self._reset_button.clicked.connect(self._reset_selection)
            button_row.addWidget(self._reset_button)

            self._live_tune_button = QtWidgets.QPushButton('VBI Tune Live')
            self._live_tune_button.clicked.connect(self._open_live_tune_dialog)
            button_row.addWidget(self._live_tune_button)

            button_row.addStretch(1)

            self._add_file_button = QtWidgets.QPushButton('Add File...')
            self._add_file_button.clicked.connect(self._add_file)
            button_row.addWidget(self._add_file_button)

            self._save_button = QtWidgets.QPushButton('Save File...')
            self._save_button.clicked.connect(self._save_selection)
            button_row.addWidget(self._save_button)

            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            button_row.addWidget(self._close_button)

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(100)
            self._timer.timeout.connect(self._sync_from_state)
            self._timer.start()
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

        def _format_time(self, frame_index):
            seconds = max(float(frame_index) / self._frame_rate, 0.0)
            minutes = int(seconds // 60)
            whole_seconds = int(seconds % 60)
            centiseconds = int(round((seconds - int(seconds)) * 100))
            return f'{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}'

        def _split_duration_seconds(self, total_seconds):
            total_seconds = max(float(total_seconds), 0.04)
            minutes = int(total_seconds // 60)
            seconds = total_seconds - (minutes * 60)
            return minutes, seconds

        def _format_duration_value(self, frame_count):
            total_seconds = max(float(frame_count) / self._frame_rate, 0.0)
            minutes = int(total_seconds // 60)
            seconds = total_seconds - (minutes * 60)
            return f'{minutes:02d}:{seconds:05.2f}'

        def _format_megabytes(self, frame_count):
            size_bytes = max(int(frame_count), 0) * self._frame_size_bytes
            return f'{size_bytes / (1024 * 1024):.2f} MB'

        def _capture_snapshot(self):
            current = self._state.current_frame()
            start, end = self._state.selection_range()
            return (current, start, end, tuple(self._cut_ranges), tuple(self._insertions))

        def _record_history_state(self, reset_redo=False):
            snapshot = self._capture_snapshot()
            if not self._history or self._history[-1] != snapshot:
                self._history.append(snapshot)
            if reset_redo:
                self._redo_history.clear()
            self._update_history_buttons()

        def _restore_snapshot(self, snapshot):
            self._cut_ranges = tuple(snapshot[3])
            self._insertions = tuple(snapshot[4])
            self._state.restore_state(snapshot[0], snapshot[1], snapshot[2], playing=False)
            self._sync_from_state()

        def _update_history_buttons(self):
            self._undo_button.setEnabled(len(self._history) > 1)
            self._redo_button.setEnabled(len(self._redo_history) > 0)

        def _sync_from_state(self):
            viewer_process = self._viewer_process() if callable(self._viewer_process) else self._viewer_process
            if viewer_process is not None and not viewer_process.is_alive():
                self.close()
                return

            self._updating = True
            current = self._state.current_frame()
            start, end = self._state.selection_range()
            self._frame_slider.setValue(current)
            self._frame_box.setValue(current)
            self._frame_time_label.setText(self._format_time(current))
            self._range_slider.setValues(start, end)
            self._range_slider.setCuts(self._cut_ranges)
            self._range_slider.setInsertMarkers(insertion['after_frame'] for insertion in self._insertions)
            self._start_box.setValue(start)
            self._end_box.setValue(end)
            selection_seconds = max(((end - start) + 1) / self._frame_rate, 0.04)
            duration_minutes, duration_seconds = self._split_duration_seconds(selection_seconds)
            self._duration_minutes_box.setValue(duration_minutes)
            self._duration_seconds_box.setValue(duration_seconds)
            self._play_button.setText('Pause' if self._state.is_playing() else 'Play')

            elapsed = self._format_time(current)
            remaining_frames = max((self._total_frames - 1) - current, 0)
            remaining = self._format_time(remaining_frames)
            self._status_label.setText(f'{current + 1}/{self._total_frames} [{elapsed}<{remaining}]')
            selection_frames = (end - start) + 1
            cut_frames = count_cut_frames(self._cut_ranges)
            inserted_frames = count_inserted_frames(self._insertions)
            edited_frames = max((self._total_frames - cut_frames) + inserted_frames, 0)
            self._selection_label.setText(
                f'Selection: {start}..{end} ({selection_frames} frames, {selection_seconds:.2f}s) | Cuts: {len(self._cut_ranges)} | Inserts: {len(self._insertions)}'
            )
            self._size_label.setText(
                f'Selected: {self._format_megabytes(selection_frames)} | '
                f'Cuts total: {self._format_megabytes(cut_frames)} | '
                f'Inserted total: {self._format_megabytes(inserted_frames)} | '
                f'Edited file: {self._format_megabytes(edited_frames)}'
            )
            self._edited_label.setText(
                f'Edited total: {edited_frames} frames | {self._format_duration_value(edited_frames)}'
            )
            if self._insertions:
                self._insertions_label.setText(
                    'Insertions: ' + ', '.join(
                        f"{os.path.basename(insertion['path'])} -> after {insertion['after_frame']} ({insertion['frame_count']}f)"
                        for insertion in self._insertions[-4:]
                    )
                )
            else:
                self._insertions_label.setText('Insertions: none')
            self._updating = False
            self._update_history_buttons()

        def _frame_slider_changed(self, value):
            if self._updating:
                return
            self._state.set_playing(False)
            self._state.set_current_frame(value)
            self._sync_from_state()

        def _frame_box_changed(self, value):
            if self._updating:
                return
            self._state.set_playing(False)
            self._state.set_current_frame(value)
            self._sync_from_state()

        def _range_slider_changed(self, start, end):
            if self._updating:
                return
            self._state.set_selection_range(start, end)
            self._sync_from_state()

        def _range_box_changed(self, _value):
            if self._updating:
                return
            self._state.set_selection_range(self._start_box.value(), self._end_box.value())
            self._sync_from_state()

        def _duration_changed(self, _value):
            if self._updating:
                return
            seconds = (self._duration_minutes_box.value() * 60) + float(self._duration_seconds_box.value())
            start = self._start_box.value()
            frame_count = max(int(round(float(seconds) * self._frame_rate)), 1)
            end = min(start + frame_count - 1, self._total_frames - 1)
            self._state.set_selection_range(start, end)
            self._sync_from_state()

        def _toggle_play(self):
            self._state.set_playing(not self._state.is_playing())
            self._sync_from_state()

        def _step(self, delta):
            self._state.step(delta)
            self._sync_from_state()

        def _jump_start(self):
            self._state.jump_to_start()
            self._sync_from_state()

        def _jump_end(self):
            self._state.jump_to_end()
            self._sync_from_state()

        def _mark_start(self):
            self._state.set_selection_to_current_start()
            self._sync_from_state()

        def _mark_end(self):
            self._state.set_selection_to_current_end()
            self._sync_from_state()

        def _jump_selection_start(self):
            start, _ = self._state.selection_range()
            self._state.set_playing(False)
            self._state.set_selection_range(start, start)
            self._sync_from_state()

        def _jump_selection_middle(self):
            start, end = self._state.selection_range()
            self._state.set_playing(False)
            _, middle, _ = selection_end_targets(start, self._total_frames)
            self._state.set_selection_range(start, middle)
            self._sync_from_state()

        def _jump_selection_end(self):
            start, _ = self._state.selection_range()
            self._state.set_playing(False)
            _, _, end = selection_end_targets(start, self._total_frames)
            self._state.set_selection_range(start, end)
            self._sync_from_state()

        def _reset_selection(self):
            self._state.set_playing(False)
            self._state.set_current_frame(0)
            self._state.set_selection_range(0, self._total_frames - 1)
            self._cut_ranges = ()
            self._insertions = ()
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

        def _undo(self):
            if len(self._history) <= 1:
                return
            current = self._history.pop()
            self._redo_history.append(current)
            self._restore_snapshot(self._history[-1])

        def _redo(self):
            if not self._redo_history:
                return
            snapshot = self._redo_history.pop()
            self._history.append(snapshot)
            self._restore_snapshot(snapshot)

        def _open_live_tune_dialog(self):
            if self._live_tune_callback is None:
                return
            try:
                self._live_tune_callback()
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Crop', str(exc))

        def _save_selection(self):
            if self._save_callback is None:
                return
            default_name = 'edited.vbi'
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Edited VBI',
                os.path.join(os.getcwd(), default_name),
                'VBI files (*.vbi);;All files (*)',
            )
            if not filename:
                return
            try:
                self._save_callback(filename, self._cut_ranges, self._insertions)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Crop', str(exc))
                return
            QtWidgets.QMessageBox.information(
                self,
                'VBI Crop',
                f'Saved edited VBI to:\n{filename}',
            )

        def _delete_selection(self):
            start, end = self._state.selection_range()
            self._cut_ranges = normalise_cut_ranges(self._cut_ranges + ((start, end),), self._total_frames)
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

        def _add_file(self):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Add VBI File',
                os.getcwd(),
                'VBI files (*.vbi);;All files (*)',
            )
            if not filename:
                return
            try:
                file_size = os.path.getsize(filename)
            except OSError as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Crop', str(exc))
                return
            if self._frame_size_bytes <= 0:
                QtWidgets.QMessageBox.critical(self, 'VBI Crop', 'Frame size is unknown, cannot add file.')
                return
            frame_count = file_size // self._frame_size_bytes
            if frame_count <= 0:
                QtWidgets.QMessageBox.warning(self, 'VBI Crop', 'Selected file does not contain complete VBI frames.')
                return
            _, end = self._state.selection_range()
            self._insertions = normalise_insertions(
                self._insertions + ({
                    'after_frame': end,
                    'path': filename,
                    'frame_count': frame_count,
                },),
                self._total_frames,
            )
            self._record_history_state(reset_redo=True)
            self._sync_from_state()


def run_crop_window(state, total_frames, frame_rate=DEFAULT_FRAME_RATE, save_callback=None, live_tune_callback=None, viewer_process=None, frame_size_bytes=0):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    window = VBICropWindow(
        state=state,
        total_frames=total_frames,
        frame_rate=frame_rate,
        save_callback=save_callback,
        live_tune_callback=live_tune_callback,
        viewer_process=viewer_process,
        frame_size_bytes=frame_size_bytes,
    )
    window.show()
    window.raise_()
    window.activateWindow()
    window.exec_()
