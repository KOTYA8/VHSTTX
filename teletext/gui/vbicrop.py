import multiprocessing as mp
import os
import sys
import time


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
DEFAULT_PLAYBACK_SPEED = 1.0
MIN_PLAYBACK_SPEED = 0.1
MAX_PLAYBACK_SPEED = 8.0


def _ensure_app():
    global _APP
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1] or ['teletext-vbitool'])
    _APP = app
    return app


def _run_dialog_window(dialog):
    loop = QtCore.QEventLoop()
    dialog.finished.connect(loop.quit)
    dialog.setModal(False)
    dialog.setWindowModality(QtCore.Qt.NonModal)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    loop.exec_()
    return dialog.result()


def _standard_window_flags():
    return (
        QtCore.Qt.Window
        | QtCore.Qt.CustomizeWindowHint
        | QtCore.Qt.WindowSystemMenuHint
        | QtCore.Qt.WindowTitleHint
        | QtCore.Qt.WindowCloseButtonHint
        | QtCore.Qt.WindowMinimizeButtonHint
        | QtCore.Qt.WindowMaximizeButtonHint
        | QtCore.Qt.WindowMinMaxButtonsHint
    ) & ~QtCore.Qt.WindowContextHelpButtonHint


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, int(value)))


def _clamp_playback_speed(value):
    return max(MIN_PLAYBACK_SPEED, min(MAX_PLAYBACK_SPEED, float(value)))


def _format_eta(seconds):
    seconds = max(int(round(float(seconds))), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


def advance_playback_position(current, steps, total_frames, direction):
    current = int(current)
    steps = max(int(steps), 0)
    total_frames = max(int(total_frames), 1)
    direction = -1 if int(direction) < 0 else 1
    maximum = total_frames - 1
    if steps <= 0:
        return _clamp(current, 0, maximum), False
    if direction < 0:
        updated = current - steps
        if updated <= 0:
            return 0, True
        return updated, False
    updated = current + steps
    if updated >= maximum:
        return maximum, True
    return updated, False


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
    SPEED_TENTHS_INDEX = 4
    DIRECTION_INDEX = 5

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

    def playback_speed(self):
        return max(int(self._shared_values[self.SPEED_TENTHS_INDEX]), 1) / 10.0

    def set_playback_speed(self, value):
        self._shared_values[self.SPEED_TENTHS_INDEX] = int(round(_clamp_playback_speed(value) * 10))

    def playback_direction(self):
        return -1 if int(self._shared_values[self.DIRECTION_INDEX]) < 0 else 1

    def set_playback_direction(self, direction):
        self._shared_values[self.DIRECTION_INDEX] = -1 if int(direction) < 0 else 1

    def toggle_playback(self, direction=1):
        direction = -1 if int(direction) < 0 else 1
        if self.is_playing() and self.playback_direction() == direction:
            self.set_playing(False)
            return
        self.set_playback_direction(direction)
        self.set_playing(True)

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


def create_crop_state(total_frames, current_frame=0, playing=False, start_frame=0, end_frame=None, playback_speed=DEFAULT_PLAYBACK_SPEED, playback_direction=1):
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
            int(round(_clamp_playback_speed(playback_speed) * 10)),
            -1 if int(playback_direction) < 0 else 1,
        ],
        lock=False,
    )
    return CropStateHandle(shared_values, total_frames)


if IMPORT_ERROR is None:
    class _ErrorScanWorker(QtCore.QObject):
        result_ready = QtCore.pyqtSignal(object)
        progress_ready = QtCore.pyqtSignal(int, int)

        def __init__(self, scan_callback):
            super().__init__()
            self._scan_callback = scan_callback

        @QtCore.pyqtSlot()
        def process(self):
            try:
                payload = self._scan_callback(progress_callback=lambda current, total: self.progress_ready.emit(int(current), int(total)))
            except Exception as exc:  # pragma: no cover - GUI path
                payload = {
                    'ranges': (),
                    'summary': f'Errors: scan failed ({exc})',
                }
            self.result_ready.emit(payload)


class ErrorRangeSlider(QtWidgets.QSlider):
    errorRangeActivated = QtCore.pyqtSignal(int, int)

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._error_ranges = ()
        self._errors_hidden = False

    def setErrorRanges(self, error_ranges):
        maximum = max(int(self.maximum()), int(self.minimum()))
        minimum = min(int(self.maximum()), int(self.minimum()))
        normalised = []
        for start, end in error_ranges or ():
            start = _clamp(start, minimum, maximum)
            end = _clamp(end, minimum, maximum)
            if start > end:
                start, end = end, start
            normalised.append((start, end))
        self._error_ranges = tuple(normalised)
        self.update()

    def setErrorsHidden(self, hidden):
        hidden = bool(hidden)
        if hidden == self._errors_hidden:
            return
        self._errors_hidden = hidden
        self.update()

    def _range_rect(self, start, end, groove):
        minimum = int(self.minimum())
        maximum = int(self.maximum())
        if maximum <= minimum:
            return QtCore.QRect(groove.left(), groove.top() - 3, groove.width(), groove.height() + 6)
        usable = max(groove.width(), 1)
        start_ratio = (int(start) - minimum) / float(maximum - minimum)
        end_ratio = (int(end) - minimum) / float(maximum - minimum)
        left = groove.left() + int(round(start_ratio * usable))
        right = groove.left() + int(round(end_ratio * usable))
        return QtCore.QRect(
            min(left, right),
            groove.top() - 4,
            max(abs(right - left), 3),
            groove.height() + 8,
        )

    def _groove_rect(self):
        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QtWidgets.QStyle.CC_Slider,
            option,
            QtWidgets.QStyle.SC_SliderGroove,
            self,
        )

    def paintEvent(self, event):  # pragma: no cover - GUI path
        super().paintEvent(event)
        if self._errors_hidden or not self._error_ranges:
            return

        groove = self._groove_rect()
        if not groove.isValid():
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(214, 63, 63, 110))
        for start, end in self._error_ranges:
            painter.drawRoundedRect(self._range_rect(start, end, groove), 3, 3)

        painter.setPen(QtGui.QPen(QtGui.QColor('#b71c1c'), 1))
        for start, end in self._error_ranges:
            rect = self._range_rect(start, end, groove)
            painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())

    def mousePressEvent(self, event):  # pragma: no cover - GUI path
        super().mousePressEvent(event)
        if event.button() != QtCore.Qt.LeftButton or self._errors_hidden or not self._error_ranges:
            return
        groove = self._groove_rect()
        if not groove.isValid():
            return
        for start, end in self._error_ranges:
            if self._range_rect(start, end, groove).contains(event.pos()):
                self.errorRangeActivated.emit(int(start), int(end))
                break


class VBICropErrorsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        state,
        total_frames,
        frame_rate=DEFAULT_FRAME_RATE,
        start_scan_callback=None,
        delete_zone_callback=None,
        delete_all_callback=None,
        select_zone_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        self._state = state
        self._total_frames = max(int(total_frames), 1)
        self._frame_rate = max(float(frame_rate), 0.001)
        self._payload = {'ranges': (), 'zones': (), 'summary': 'Errors: not scanned'}
        self._start_scan_callback = start_scan_callback
        self._delete_zone_callback = delete_zone_callback
        self._delete_all_callback = delete_all_callback
        self._select_zone_callback = select_zone_callback
        self._syncing_frame = False
        self._filtered_zones = ()

        self.setWindowTitle('VBI Tool Errors')
        self.resize(900, 420)
        self.setMinimumSize(640, 300)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._summary_label = QtWidgets.QLabel('Errors: not scanned')
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        self._current_label = QtWidgets.QLabel('Current frame: 0 (00:00.00)')
        root.addWidget(self._current_label)

        filters = QtWidgets.QHBoxLayout()
        root.addLayout(filters)
        filters.addWidget(QtWidgets.QLabel('Levels:'))

        self._warning_check = QtWidgets.QCheckBox('Warning')
        self._warning_check.setChecked(True)
        self._warning_check.toggled.connect(self._apply_filters)
        filters.addWidget(self._warning_check)

        self._bad_check = QtWidgets.QCheckBox('Bad')
        self._bad_check.setChecked(True)
        self._bad_check.toggled.connect(self._apply_filters)
        filters.addWidget(self._bad_check)

        self._critical_check = QtWidgets.QCheckBox('Critical')
        self._critical_check.setChecked(True)
        self._critical_check.toggled.connect(self._apply_filters)
        filters.addWidget(self._critical_check)

        filters.addSpacing(12)
        filters.addWidget(QtWidgets.QLabel('Filter:'))
        filters.addWidget(QtWidgets.QLabel('Lost >='))

        self._lost_lines_spin = QtWidgets.QSpinBox()
        self._lost_lines_spin.setRange(0, 32)
        self._lost_lines_spin.setValue(0)
        self._lost_lines_spin.valueChanged.connect(self._apply_filters)
        filters.addWidget(self._lost_lines_spin)

        filters.addWidget(QtWidgets.QLabel('Lost ='))

        self._lost_lines_exact_spin = QtWidgets.QSpinBox()
        self._lost_lines_exact_spin.setRange(0, 32)
        self._lost_lines_exact_spin.setValue(0)
        self._lost_lines_exact_spin.setSpecialValueText('Any')
        self._lost_lines_exact_spin.valueChanged.connect(self._apply_filters)
        filters.addWidget(self._lost_lines_exact_spin)

        self._noise_check = QtWidgets.QCheckBox('Noise')
        self._noise_check.setChecked(False)
        self._noise_check.toggled.connect(self._apply_filters)
        filters.addWidget(self._noise_check)

        self._shift_check = QtWidgets.QCheckBox('Shift')
        self._shift_check.setChecked(False)
        self._shift_check.toggled.connect(self._apply_filters)
        filters.addWidget(self._shift_check)

        self._filtered_count_label = QtWidgets.QLabel('0 errors')
        self._filtered_count_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        filters.addStretch(1)
        filters.addWidget(self._filtered_count_label)

        navigation = QtWidgets.QHBoxLayout()
        root.addLayout(navigation)

        self._prev_button = QtWidgets.QPushButton('<')
        self._prev_button.clicked.connect(lambda: self._step_frame(-1))
        navigation.addWidget(self._prev_button)

        self._next_button = QtWidgets.QPushButton('>')
        self._next_button.clicked.connect(lambda: self._step_frame(1))
        navigation.addWidget(self._next_button)
        navigation.addStretch(1)

        self._slider = ErrorRangeSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(0, self._total_frames - 1)
        self._slider.setEnabled(True)
        self._slider.valueChanged.connect(self._slider_changed)
        self._slider.errorRangeActivated.connect(self._activate_error_range)
        root.addWidget(self._slider)

        self._table = QtWidgets.QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(('Level', 'Type', 'Start', 'End', 'Duration', 'Details'))
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self._table.itemDoubleClicked.connect(lambda _item: self._jump_to_selected())
        self._table.itemSelectionChanged.connect(self._update_buttons)
        root.addWidget(self._table, 1)

        buttons = QtWidgets.QHBoxLayout()
        root.addLayout(buttons)

        self._delete_selected_button = QtWidgets.QPushButton('Delete Selected Error')
        self._delete_selected_button.clicked.connect(self._delete_selected_error)
        self._delete_selected_button.setEnabled(False)
        buttons.addWidget(self._delete_selected_button)

        self._delete_all_button = QtWidgets.QPushButton('Delete All Errors')
        self._delete_all_button.clicked.connect(self._delete_all_errors)
        self._delete_all_button.setEnabled(False)
        buttons.addWidget(self._delete_all_button)

        self._jump_button = QtWidgets.QPushButton('Jump To Zone')
        self._jump_button.clicked.connect(self._jump_to_selected)
        self._jump_button.setEnabled(False)
        buttons.addWidget(self._jump_button)

        self._selection_button = QtWidgets.QPushButton('To Selection')
        self._selection_button.clicked.connect(self._move_selected_to_selection)
        self._selection_button.setEnabled(False)
        buttons.addWidget(self._selection_button)

        buttons.addStretch(1)

        self._close_button = QtWidgets.QPushButton('Close')
        self._close_button.clicked.connect(self.close)
        buttons.addWidget(self._close_button)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._sync_current_frame)
        self._timer.start()

    def _format_time(self, frame_index):
        seconds = max(float(frame_index) / self._frame_rate, 0.0)
        minutes = int(seconds // 60)
        whole_seconds = int(seconds % 60)
        centiseconds = int(round((seconds - int(seconds)) * 100))
        return f'{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}'

    def _sync_current_frame(self):
        current = self._state.current_frame()
        self._syncing_frame = True
        self._slider.setValue(current)
        self._syncing_frame = False
        self._current_label.setText(f'Current frame: {current} ({self._format_time(current)})')

    def _slider_changed(self, value):
        if self._syncing_frame:
            return
        self._state.set_playing(False)
        self._state.set_current_frame(int(value))
        self._sync_current_frame()

    def _step_frame(self, delta):
        self._state.set_playing(False)
        self._state.step(int(delta))
        self._sync_current_frame()

    def _jump_start(self):
        self._state.set_playing(False)
        self._state.jump_to_start()
        self._sync_current_frame()

    def _jump_end(self):
        self._state.set_playing(False)
        self._state.jump_to_end()
        self._sync_current_frame()

    def _zone_level_text(self, level):
        labels = {
            'warning': 'Warning',
            'bad': 'Bad',
            'critical': 'Critical',
        }
        return labels.get(str(level).lower(), str(level).title())

    def _zone_kind_text(self, kind):
        return str(kind or 'signal').replace('-', ' ').title()

    def _zone_brush(self, level):
        palette = {
            'warning': QtGui.QColor('#fff3cd'),
            'bad': QtGui.QColor('#f8d7da'),
            'critical': QtGui.QColor('#f1b0b7'),
        }
        return QtGui.QBrush(palette.get(str(level).lower(), QtGui.QColor('#ffffff')))

    def _selected_zone(self):
        zones = self._selected_zones()
        if zones:
            return zones[0]
        row = self._table.currentRow()
        filtered = tuple(self._filtered_zones or ())
        if row < 0 or row >= len(filtered):
            return None
        return filtered[row]

    def _selected_rows(self):
        model = self._table.selectionModel()
        if model is None:
            return ()
        return tuple(sorted(index.row() for index in model.selectedRows()))

    def _selected_zones(self):
        filtered = tuple(self._filtered_zones or ())
        selected = []
        for row in self._selected_rows():
            if 0 <= row < len(filtered):
                selected.append(filtered[row])
        return tuple(selected)

    def _enabled_levels(self):
        levels = set()
        if self._warning_check.isChecked():
            levels.add('warning')
        if self._bad_check.isChecked():
            levels.add('bad')
        if self._critical_check.isChecked():
            levels.add('critical')
        return levels

    def _update_buttons(self):
        zones = self._selected_zones()
        has_zones = bool(tuple(self._filtered_zones or ()))
        self._jump_button.setEnabled(bool(zones))
        self._delete_selected_button.setEnabled(bool(zones) and self._delete_zone_callback is not None)
        self._delete_all_button.setEnabled(has_zones and self._delete_all_callback is not None)
        self._selection_button.setEnabled(bool(zones) and self._select_zone_callback is not None)

    def _select_zone_rows(self, row_indexes, *, replace=True):
        selection_model = self._table.selectionModel()
        if selection_model is None:
            return
        flags = QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows
        if replace:
            selection_model.clearSelection()
        for row_index in row_indexes:
            item = self._table.item(int(row_index), 0)
            if item is None:
                continue
            selection_model.select(self._table.model().index(int(row_index), 0), flags)
        if row_indexes:
            self._table.setCurrentCell(int(row_indexes[0]), 0)

    def _activate_error_range(self, start_frame, end_frame):
        matching_rows = [
            row_index
            for row_index, zone in enumerate(tuple(self._filtered_zones or ()))
            if int(zone.get('start_frame', 0)) == int(start_frame) and int(zone.get('end_frame', 0)) == int(end_frame)
        ]
        if matching_rows:
            self._select_zone_rows(matching_rows, replace=True)

    def _apply_filters(self):
        payload = self._payload if isinstance(self._payload, dict) else {'ranges': (), 'zones': (), 'summary': str(self._payload)}
        enabled_levels = self._enabled_levels()
        min_lost_lines = int(self._lost_lines_spin.value())
        exact_lost_lines = int(self._lost_lines_exact_spin.value())
        noise_only = self._noise_check.isChecked()
        shift_only = self._shift_check.isChecked()
        zones = tuple(
            zone for zone in tuple(payload.get('zones') or ())
            if (
                str(zone.get('level') or 'warning').lower() in enabled_levels
                and int(zone.get('teletext_loss_count', 0)) >= min_lost_lines
                and (exact_lost_lines <= 0 or int(zone.get('teletext_loss_count', 0)) == exact_lost_lines)
                and ((not noise_only) or bool(zone.get('has_noise')))
                and ((not shift_only) or int(zone.get('shift_distance', 0)) > 0)
            )
        )
        self._filtered_zones = zones

        filtered_ranges = tuple(
            (int(zone.get('start_frame', 0)), int(zone.get('end_frame', 0)))
            for zone in zones
        )
        self._filtered_count_label.setText(f'{len(zones)} errors')
        self._slider.setErrorRanges(filtered_ranges)
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.clearContents()
        self._table.setRowCount(len(zones))
        try:
            for row_index, zone in enumerate(zones):
                start_frame = int(zone.get('start_frame', 0))
                end_frame = int(zone.get('end_frame', start_frame))
                duration_seconds = float(zone.get('duration_seconds', 0.0))
                duration_frames = int(zone.get('duration_frames', (end_frame - start_frame) + 1))
                level = str(zone.get('level') or 'warning')
                kind = str(zone.get('kind') or 'signal')
                details = str(zone.get('reason') or 'severe signal disruption')
                brush = self._zone_brush(level)

                level_item = QtWidgets.QTableWidgetItem(self._zone_level_text(level))
                kind_item = QtWidgets.QTableWidgetItem(self._zone_kind_text(kind))
                start_item = QtWidgets.QTableWidgetItem(f"{start_frame} ({self._format_time(start_frame)})")
                start_item.setData(QtCore.Qt.UserRole, start_frame)
                end_item = QtWidgets.QTableWidgetItem(f"{end_frame} ({self._format_time(end_frame)})")
                duration_item = QtWidgets.QTableWidgetItem(f"{duration_frames}f / {duration_seconds:.2f}s")
                detail_item = QtWidgets.QTableWidgetItem(details)

                for item in (level_item, kind_item, start_item, end_item, duration_item, detail_item):
                    item.setBackground(brush)

                self._table.setItem(row_index, 0, level_item)
                self._table.setItem(row_index, 1, kind_item)
                self._table.setItem(row_index, 2, start_item)
                self._table.setItem(row_index, 3, end_item)
                self._table.setItem(row_index, 4, duration_item)
                self._table.setItem(row_index, 5, detail_item)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)

        if zones:
            self._table.selectRow(0)
        else:
            self._table.clearSelection()
        self._update_buttons()
        self._sync_current_frame()

    def setPayload(self, payload):
        if not isinstance(payload, dict):
            payload = {'ranges': (), 'zones': (), 'summary': str(payload)}
        self._payload = payload
        self._summary_label.setText(str(payload.get('summary') or 'Errors: none detected'))
        self._apply_filters()

    def _jump_to_selected(self):
        zone = self._selected_zone()
        if zone is None:
            return
        start_frame = int(zone.get('start_frame', 0))
        self._state.set_playing(False)
        self._state.set_current_frame(start_frame)
        self._sync_current_frame()

    def _move_selected_to_selection(self):
        zone = self._selected_zone()
        if zone is None or self._select_zone_callback is None:
            return
        start_frame = int(zone.get('start_frame', 0))
        end_frame = int(zone.get('end_frame', start_frame))
        self._select_zone_callback(start_frame, end_frame)

    def _delete_selected_error(self):
        zones = self._selected_zones()
        if not zones:
            return
        ranges = tuple(
            (int(zone.get('start_frame', 0)), int(zone.get('end_frame', 0)))
            for zone in zones
        )
        if self._delete_all_callback is not None:
            self._delete_all_callback(ranges)
            return
        if self._delete_zone_callback is None:
            return
        for start_frame, end_frame in ranges:
            self._delete_zone_callback(start_frame, end_frame)

    def _delete_all_errors(self):
        if self._delete_all_callback is None:
            return
        self._delete_all_callback(
            tuple(
                (int(zone.get('start_frame', 0)), int(zone.get('end_frame', 0)))
                for zone in tuple(self._filtered_zones or ())
            )
        )

    def closeEvent(self, event):  # pragma: no cover - GUI path
        self._timer.stop()
        super().closeEvent(event)


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
    _error_scan_request = QtCore.pyqtSignal()

    def __init__(self, state, total_frames, frame_rate=DEFAULT_FRAME_RATE, save_callback=None, viewer_process=None, frame_size_bytes=0, error_scan_callback=None, parent=None):
            super().__init__(parent)
            self._state = state
            self._total_frames = max(int(total_frames), 1)
            self._frame_rate = float(frame_rate)
            self._save_callback = save_callback
            self._viewer_process = viewer_process
            self._frame_size_bytes = int(frame_size_bytes)
            self._error_scan_callback = error_scan_callback
            self._updating = False
            self._history = []
            self._redo_history = []
            self._cut_ranges = ()
            self._insertions = ()
            self._selected_cut_index = None
            self._cuts_render_state = None
            self._selected_insertion_index = None
            self._insertions_render_state = None
            self._error_ranges = ()
            self._error_scan_summary = 'Errors: not scanned'
            self._error_scan_busy = False
            self._error_scan_completed = False
            self._error_scan_started_at = None
            self._error_scan_thread = None
            self._error_scan_worker = None
            self._error_payload = {'ranges': (), 'zones': (), 'summary': 'Errors: not scanned'}
            self._errors_dialog = None

            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)

            self.setWindowTitle('VBI Tool')
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

            self._frame_slider = ErrorRangeSlider(QtCore.Qt.Horizontal)
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
            self._reverse_button = QtWidgets.QPushButton('Reverse')
            self._reverse_button.clicked.connect(self._toggle_reverse_play)
            controls_layout.addWidget(self._reverse_button)
            self._play_button = QtWidgets.QPushButton('Play')
            self._play_button.clicked.connect(self._toggle_play)
            controls_layout.addWidget(self._play_button)
            self._next_button = QtWidgets.QPushButton('>')
            self._next_button.clicked.connect(lambda: self._step(1))
            controls_layout.addWidget(self._next_button)
            self._end_button = QtWidgets.QPushButton('>|')
            self._end_button.clicked.connect(self._jump_end)
            controls_layout.addWidget(self._end_button)
            controls_layout.addWidget(QtWidgets.QLabel('Speed'))
            self._speed_box = QtWidgets.QDoubleSpinBox()
            self._speed_box.setRange(MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED)
            self._speed_box.setDecimals(1)
            self._speed_box.setSingleStep(0.1)
            self._speed_box.setSuffix('x')
            self._speed_box.valueChanged.connect(self._speed_changed)
            controls_layout.addWidget(self._speed_box)

            self._errors_button = QtWidgets.QPushButton('Errors...')
            self._errors_button.clicked.connect(self._open_errors_dialog)
            self._errors_button.setEnabled(self._error_scan_callback is not None)
            controls_layout.addWidget(self._errors_button)

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

            selection_layout.addWidget(QtWidgets.QLabel('Cuts'), 3, 0)
            self._cuts_scroll = QtWidgets.QScrollArea()
            self._cuts_scroll.setWidgetResizable(True)
            self._cuts_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            self._cuts_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._cuts_scroll.setMinimumHeight(54)
            self._cuts_scroll.setMaximumHeight(64)
            self._cuts_container = QtWidgets.QWidget()
            self._cuts_layout = QtWidgets.QHBoxLayout(self._cuts_container)
            self._cuts_layout.setContentsMargins(0, 0, 0, 0)
            self._cuts_layout.setSpacing(6)
            self._cuts_scroll.setWidget(self._cuts_container)
            selection_layout.addWidget(self._cuts_scroll, 3, 1, 1, 7)

            self._update_cut_button = QtWidgets.QPushButton('Update Cut')
            self._update_cut_button.clicked.connect(self._update_selected_cut)
            self._update_cut_button.setEnabled(False)
            selection_layout.addWidget(self._update_cut_button, 4, 5)

            self._remove_cut_button = QtWidgets.QPushButton('Delete Cut')
            self._remove_cut_button.clicked.connect(self._remove_selected_cut)
            self._remove_cut_button.setEnabled(False)
            selection_layout.addWidget(self._remove_cut_button, 4, 6)

            selection_layout.addWidget(QtWidgets.QLabel('Inserts'), 5, 0)
            self._insertions_scroll = QtWidgets.QScrollArea()
            self._insertions_scroll.setWidgetResizable(True)
            self._insertions_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            self._insertions_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._insertions_scroll.setMinimumHeight(54)
            self._insertions_scroll.setMaximumHeight(64)
            self._insertions_container = QtWidgets.QWidget()
            self._insertions_layout = QtWidgets.QHBoxLayout(self._insertions_container)
            self._insertions_layout.setContentsMargins(0, 0, 0, 0)
            self._insertions_layout.setSpacing(6)
            self._insertions_scroll.setWidget(self._insertions_container)
            selection_layout.addWidget(self._insertions_scroll, 5, 1, 1, 7)

            self._update_insertion_button = QtWidgets.QPushButton('Update Insert')
            self._update_insertion_button.clicked.connect(self._update_selected_insertion)
            self._update_insertion_button.setEnabled(False)
            selection_layout.addWidget(self._update_insertion_button, 6, 5)

            self._remove_insertion_button = QtWidgets.QPushButton('Delete Insert')
            self._remove_insertion_button.clicked.connect(self._remove_selected_insertion)
            self._remove_insertion_button.setEnabled(False)
            selection_layout.addWidget(self._remove_insertion_button, 6, 6)

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
            self._selected_cut_index = None
            self._selected_insertion_index = None
            self._state.restore_state(snapshot[0], snapshot[1], snapshot[2], playing=False)
            self._sync_from_state()

    def _update_history_buttons(self):
            self._undo_button.setEnabled(len(self._history) > 1)
            self._redo_button.setEnabled(len(self._redo_history) > 0)

    def _current_selected_cut(self):
            if self._selected_cut_index is None:
                return None
            if not (0 <= int(self._selected_cut_index) < len(self._cut_ranges)):
                self._selected_cut_index = None
                return None
            return self._cut_ranges[int(self._selected_cut_index)]

    def _refresh_cut_buttons(self):
            render_state = (tuple(self._cut_ranges), self._selected_cut_index)
            if render_state == self._cuts_render_state:
                return
            self._cuts_render_state = render_state
            while self._cuts_layout.count():
                item = self._cuts_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            if not self._cut_ranges:
                empty = QtWidgets.QLabel('No cuts')
                empty.setStyleSheet('color: #666;')
                self._cuts_layout.addWidget(empty)
                self._cuts_layout.addStretch(1)
            else:
                for cut_index, (start, end) in enumerate(self._cut_ranges):
                    button = QtWidgets.QPushButton(f'{start}..{end}')
                    button.setCheckable(True)
                    button.setChecked(cut_index == self._selected_cut_index)
                    button.clicked.connect(lambda _checked=False, index=cut_index: self._select_cut(index))
                    self._cuts_layout.addWidget(button)
                self._cuts_layout.addStretch(1)
            has_cut = self._current_selected_cut() is not None
            self._update_cut_button.setEnabled(has_cut)
            self._remove_cut_button.setEnabled(has_cut)

    def _select_cut(self, cut_index):
            if not (0 <= int(cut_index) < len(self._cut_ranges)):
                self._selected_cut_index = None
                self._refresh_cut_buttons()
                return
            self._selected_cut_index = int(cut_index)
            start, end = self._cut_ranges[self._selected_cut_index]
            self._state.set_playing(False)
            self._state.set_selection_range(start, end)
            self._sync_from_state()

    def _update_selected_cut(self):
            current_cut = self._current_selected_cut()
            if current_cut is None:
                return
            start, end = self._state.selection_range()
            updated_range = (min(start, end), max(start, end))
            cut_ranges = list(self._cut_ranges)
            cut_ranges[int(self._selected_cut_index)] = updated_range
            self._cut_ranges = normalise_cut_ranges(tuple(cut_ranges), self._total_frames)
            self._selected_cut_index = None
            for index, cut_range in enumerate(self._cut_ranges):
                if cut_range == updated_range:
                    self._selected_cut_index = index
                    break
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

    def _remove_selected_cut(self):
            current_cut = self._current_selected_cut()
            if current_cut is None:
                return
            cut_ranges = list(self._cut_ranges)
            cut_ranges.pop(int(self._selected_cut_index))
            self._cut_ranges = tuple(cut_ranges)
            if not self._cut_ranges:
                self._selected_cut_index = None
            elif int(self._selected_cut_index) >= len(self._cut_ranges):
                self._selected_cut_index = len(self._cut_ranges) - 1
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

    def _current_selected_insertion(self):
            if self._selected_insertion_index is None:
                return None
            if not (0 <= int(self._selected_insertion_index) < len(self._insertions)):
                self._selected_insertion_index = None
                return None
            return self._insertions[int(self._selected_insertion_index)]

    def _refresh_insertion_buttons(self):
            render_state = (
                tuple(
                    (
                        int(insertion['after_frame']),
                        str(insertion['path']),
                        int(insertion['frame_count']),
                    )
                    for insertion in self._insertions
                ),
                self._selected_insertion_index,
            )
            if render_state == self._insertions_render_state:
                return
            self._insertions_render_state = render_state
            while self._insertions_layout.count():
                item = self._insertions_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            if not self._insertions:
                empty = QtWidgets.QLabel('No inserts')
                empty.setStyleSheet('color: #666;')
                self._insertions_layout.addWidget(empty)
                self._insertions_layout.addStretch(1)
            else:
                for insertion_index, insertion in enumerate(self._insertions):
                    label = f"{os.path.basename(str(insertion['path']))} @ {int(insertion['after_frame'])}"
                    button = QtWidgets.QPushButton(label)
                    button.setCheckable(True)
                    button.setChecked(insertion_index == self._selected_insertion_index)
                    button.setToolTip(
                        f"{str(insertion['path'])}\n"
                        f"After frame: {int(insertion['after_frame'])}\n"
                        f"Frames: {int(insertion['frame_count'])}"
                    )
                    button.clicked.connect(lambda _checked=False, index=insertion_index: self._select_insertion(index))
                    self._insertions_layout.addWidget(button)
                self._insertions_layout.addStretch(1)
            has_insertion = self._current_selected_insertion() is not None
            self._update_insertion_button.setEnabled(has_insertion)
            self._remove_insertion_button.setEnabled(has_insertion)

    def _select_insertion(self, insertion_index):
            if not (0 <= int(insertion_index) < len(self._insertions)):
                self._selected_insertion_index = None
                self._refresh_insertion_buttons()
                return
            self._selected_insertion_index = int(insertion_index)
            insertion = self._insertions[self._selected_insertion_index]
            after_frame = int(insertion['after_frame'])
            self._state.set_playing(False)
            self._state.set_current_frame(after_frame)
            self._state.set_selection_range(after_frame, after_frame)
            self._sync_from_state()

    def _update_selected_insertion(self):
            current_insertion = self._current_selected_insertion()
            if current_insertion is None:
                return
            _, end = self._state.selection_range()
            updated_insertion = {
                'after_frame': int(end),
                'path': current_insertion['path'],
                'frame_count': int(current_insertion['frame_count']),
            }
            insertions = list(self._insertions)
            insertions[int(self._selected_insertion_index)] = updated_insertion
            self._insertions = normalise_insertions(tuple(insertions), self._total_frames)
            self._selected_insertion_index = None
            for index, insertion in enumerate(self._insertions):
                if (
                    str(insertion['path']) == str(updated_insertion['path'])
                    and int(insertion['after_frame']) == int(updated_insertion['after_frame'])
                    and int(insertion['frame_count']) == int(updated_insertion['frame_count'])
                ):
                    self._selected_insertion_index = index
                    break
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

    def _remove_selected_insertion(self):
            current_insertion = self._current_selected_insertion()
            if current_insertion is None:
                return
            insertions = list(self._insertions)
            insertions.pop(int(self._selected_insertion_index))
            self._insertions = tuple(insertions)
            if not self._insertions:
                self._selected_insertion_index = None
            elif int(self._selected_insertion_index) >= len(self._insertions):
                self._selected_insertion_index = len(self._insertions) - 1
            self._record_history_state(reset_redo=True)
            self._sync_from_state()

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
            self._speed_box.setValue(self._state.playback_speed())
            playing = self._state.is_playing()
            direction = self._state.playback_direction()
            self._play_button.setText('Pause' if playing and direction > 0 else 'Play')
            self._reverse_button.setText('Pause Rev' if playing and direction < 0 else 'Reverse')

            elapsed = self._format_time(current)
            remaining_frames = max((self._total_frames - 1) - current, 0)
            remaining = self._format_time(remaining_frames)
            self._status_label.setText(f'{current + 1}/{self._total_frames} [{elapsed}<{remaining}]')
            self._frame_slider.setErrorRanges(())
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
                selected_insertion = self._current_selected_insertion()
                if selected_insertion is not None:
                    self._insertions_label.setText(
                        'Insertions: '
                        f"{os.path.basename(str(selected_insertion['path']))} -> after {int(selected_insertion['after_frame'])} "
                        f"({int(selected_insertion['frame_count'])}f, {self._format_megabytes(int(selected_insertion['frame_count']))}) | "
                        f"{str(selected_insertion['path'])}"
                    )
                else:
                    self._insertions_label.setText(
                        'Insertions: ' + ', '.join(
                            f"{os.path.basename(insertion['path'])} -> after {insertion['after_frame']} ({insertion['frame_count']}f)"
                            for insertion in self._insertions[-4:]
                        )
                    )
            else:
                self._insertions_label.setText('Insertions: none')
            self._refresh_cut_buttons()
            self._refresh_insertion_buttons()
            self._updating = False
            self._update_history_buttons()

    def _handle_error_scan_progress(self, current, total):
        self._error_scan_busy = True
        eta_text = ''
        if self._error_scan_started_at is not None and int(current) > 0:
            elapsed = max(time.monotonic() - float(self._error_scan_started_at), 0.0)
            remaining = max((float(total) - float(current)) * (elapsed / float(current)), 0.0)
            eta_text = f' (~{_format_eta(remaining)} left)'
        self._error_payload = {
            'ranges': self._error_ranges,
            'zones': tuple(self._error_payload.get('zones') or ()),
            'summary': f'Errors: scanning {int(current)}/{max(int(total), 1)}{eta_text}',
        }
        self._error_scan_summary = self._error_payload['summary']
        if self._errors_dialog is not None:
            self._errors_dialog.setPayload(self._error_payload)

    def _handle_error_scan_result(self, payload):
        self._error_scan_busy = False
        self._error_scan_completed = True
        self._error_scan_started_at = None
        if not isinstance(payload, dict):
            payload = {
                'ranges': (),
                'zones': (),
                'summary': str(payload),
            }
        self._error_payload = payload
        self._error_ranges = tuple(payload.get('ranges') or ())
        self._error_scan_summary = str(payload.get('summary') or 'Errors: none detected')
        self._frame_slider.setErrorRanges(())
        if self._errors_dialog is not None:
            self._errors_dialog.setPayload(self._error_payload)

    def _ensure_error_scan_worker(self):
        if self._error_scan_callback is None or self._error_scan_worker is not None:
            return
        self._error_scan_thread = QtCore.QThread(self)
        self._error_scan_worker = _ErrorScanWorker(self._error_scan_callback)
        self._error_scan_worker.moveToThread(self._error_scan_thread)
        self._error_scan_worker.progress_ready.connect(self._handle_error_scan_progress)
        self._error_scan_worker.result_ready.connect(self._handle_error_scan_result)
        self._error_scan_request.connect(self._error_scan_worker.process, QtCore.Qt.QueuedConnection)
        self._error_scan_thread.start()

    def _start_error_scan(self, force=False):
        if self._error_scan_callback is None:
            return
        if self._error_scan_busy:
            return
        if (not force) and self._error_scan_completed:
            return
        self._ensure_error_scan_worker()
        self._error_scan_busy = True
        self._error_scan_started_at = time.monotonic()
        if force:
            self._error_scan_completed = False
        self._error_payload = {
            'ranges': self._error_ranges,
            'zones': tuple(self._error_payload.get('zones') or ()),
            'summary': 'Errors: scanning...',
        }
        if self._errors_dialog is not None:
            self._errors_dialog.setPayload(self._error_payload)
        self._error_scan_request.emit()

    def _open_errors_dialog(self):
        if self._errors_dialog is None:
            self._errors_dialog = VBICropErrorsDialog(
                state=self._state,
                total_frames=self._total_frames,
                frame_rate=self._frame_rate,
                start_scan_callback=self._start_error_scan,
                delete_zone_callback=self._delete_error_zone,
                delete_all_callback=self._delete_all_errors,
                select_zone_callback=self._set_selection_from_error_zone,
                parent=self,
            )
        self._errors_dialog.setPayload(self._error_payload)
        self._errors_dialog.show()
        self._errors_dialog.raise_()
        self._errors_dialog.activateWindow()
        self._start_error_scan(force=not self._error_scan_completed)

    def _apply_cut_ranges(self, cut_ranges):
        cut_ranges = tuple(
            (
                _clamp(int(start), 0, self._total_frames - 1),
                _clamp(int(end), 0, self._total_frames - 1),
            )
            for start, end in (cut_ranges or ())
        )
        if not cut_ranges:
            return
        merged = normalise_cut_ranges(self._cut_ranges + cut_ranges, self._total_frames)
        if merged == self._cut_ranges:
            return
        self._state.set_playing(False)
        self._cut_ranges = merged
        self._selected_cut_index = None
        self._record_history_state(reset_redo=True)
        self._sync_from_state()

    def _delete_error_zone(self, start_frame, end_frame):
        self._apply_cut_ranges(((start_frame, end_frame),))

    def _delete_all_errors(self, ranges=None):
        if ranges is None:
            zones = tuple(self._error_payload.get('zones') or ())
            ranges = tuple(
                (int(zone.get('start_frame', 0)), int(zone.get('end_frame', 0)))
                for zone in zones
            )
        self._apply_cut_ranges(tuple(ranges or ()))

    def _set_selection_from_error_zone(self, start_frame, end_frame):
        start_frame = _clamp(int(start_frame), 0, self._total_frames - 1)
        end_frame = _clamp(int(end_frame), 0, self._total_frames - 1)
        if end_frame < start_frame:
            start_frame, end_frame = end_frame, start_frame
        self._state.set_playing(False)
        self._state.set_current_frame(start_frame)
        self._state.set_selection_range(start_frame, end_frame)
        self._sync_from_state()

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
        self._state.toggle_playback(direction=1)
        self._sync_from_state()

    def _toggle_reverse_play(self):
        self._state.toggle_playback(direction=-1)
        self._sync_from_state()

    def _speed_changed(self, value):
        if self._updating:
            return
        self._state.set_playback_speed(value)
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
        self._selected_cut_index = None
        self._selected_insertion_index = None
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
            QtWidgets.QMessageBox.critical(self, 'VBI Tool', str(exc))
            return
        QtWidgets.QMessageBox.information(
            self,
            'VBI Tool',
            f'Saved edited VBI to:\n{filename}',
        )

    def _delete_selection(self):
        start, end = self._state.selection_range()
        self._cut_ranges = normalise_cut_ranges(self._cut_ranges + ((start, end),), self._total_frames)
        self._selected_cut_index = None
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
            QtWidgets.QMessageBox.critical(self, 'VBI Tool', str(exc))
            return
        if self._frame_size_bytes <= 0:
            QtWidgets.QMessageBox.critical(self, 'VBI Tool', 'Frame size is unknown, cannot add file.')
            return
        frame_count = file_size // self._frame_size_bytes
        if frame_count <= 0:
            QtWidgets.QMessageBox.warning(self, 'VBI Tool', 'Selected file does not contain complete VBI frames.')
            return
        _, end = self._state.selection_range()
        inserted = {
                'after_frame': end,
                'path': filename,
                'frame_count': frame_count,
            }
        self._insertions = normalise_insertions(
            self._insertions + (inserted,),
            self._total_frames,
        )
        self._selected_insertion_index = None
        for index, insertion in enumerate(self._insertions):
            if (
                str(insertion['path']) == str(inserted['path'])
                and int(insertion['after_frame']) == int(inserted['after_frame'])
                and int(insertion['frame_count']) == int(inserted['frame_count'])
            ):
                self._selected_insertion_index = index
                break
        self._record_history_state(reset_redo=True)
        self._sync_from_state()

    def closeEvent(self, event):  # pragma: no cover - GUI path
            self._timer.stop()
            if self._errors_dialog is not None:
                self._errors_dialog.close()
            if self._error_scan_thread is not None:
                self._error_scan_thread.quit()
                self._error_scan_thread.wait(2000)
            super().closeEvent(event)


def run_crop_window(state, total_frames, frame_rate=DEFAULT_FRAME_RATE, save_callback=None, viewer_process=None, frame_size_bytes=0, error_scan_callback=None):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    window = VBICropWindow(
        state=state,
        total_frames=total_frames,
        frame_rate=frame_rate,
        save_callback=save_callback,
        viewer_process=viewer_process,
        frame_size_bytes=frame_size_bytes,
        error_scan_callback=error_scan_callback,
    )
    _run_dialog_window(window)
