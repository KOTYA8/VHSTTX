import html
import inspect
import os
import re
import time
import numpy as np

from teletext.gui.vbicrop import (
    DEFAULT_FRAME_RATE,
    FrameRangeSlider,
    MAX_PLAYBACK_SPEED,
    MIN_PLAYBACK_SPEED,
    IMPORT_ERROR,
    _ensure_app,
    _run_dialog_window,
)
from teletext.vbi.rangeprofiles import format_tuning_range_label, normalise_tuning_ranges


if IMPORT_ERROR is None:
    from PyQt5 import QtCore, QtGui, QtWidgets


_DIAGNOSTIC_FONT_FAMILY = None
_ANSI_PATTERN = re.compile(r'\x1b\[([0-9;]+)m')
_ANSI_COLOURS = {
    0: '#000000',
    1: '#ff3b30',
    2: '#40ff40',
    3: '#ffd60a',
    4: '#4c7dff',
    5: '#ff4df2',
    6: '#3df2ff',
    7: '#f5f5f5',
}


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


def _diagnostic_font_family():
    global _DIAGNOSTIC_FONT_FAMILY
    if IMPORT_ERROR is not None:
        return None
    if _DIAGNOSTIC_FONT_FAMILY is not None:
        return _DIAGNOSTIC_FONT_FAMILY

    font_path = os.path.join(os.path.dirname(__file__), 'teletext2.ttf')
    if os.path.exists(font_path):
        font_id = QtGui.QFontDatabase.addApplicationFont(font_path)
        if font_id != -1:
            families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
            if families:
                _DIAGNOSTIC_FONT_FAMILY = families[0]
                return _DIAGNOSTIC_FONT_FAMILY
    return None


def _ansi_text_to_html(text, font_family=None):
    text = str(text or '')
    parts = []
    fg = 7
    bg = 0
    index = 0
    span_open = False

    def open_span():
        nonlocal span_open
        parts.append(
            f'<span style="color:{_ANSI_COLOURS.get(fg, _ANSI_COLOURS[7])};'
            f'background-color:{_ANSI_COLOURS.get(bg, _ANSI_COLOURS[0])};">'
        )
        span_open = True

    def close_span():
        nonlocal span_open
        if span_open:
            parts.append('</span>')
            span_open = False

    open_span()
    for match in _ANSI_PATTERN.finditer(text):
        if match.start() > index:
            parts.append(html.escape(text[index:match.start()]))
        codes = [int(code or 0) for code in match.group(1).split(';') if code != '']
        if not codes:
            codes = [0]
        for code in codes:
            if code == 0:
                fg = 7
                bg = 0
            elif 30 <= code <= 37:
                fg = code - 30
            elif 40 <= code <= 47:
                bg = code - 40
        close_span()
        open_span()
        index = match.end()
    if index < len(text):
        parts.append(html.escape(text[index:]))
    close_span()
    family = html.escape(font_family or 'monospace')
    return (
        '<html><body style="margin:0; background:#000000;">'
        f'<pre style="margin:0; padding:8px; white-space:pre; font-family:{family}; font-size:12pt;">'
        + ''.join(parts) +
        '</pre></body></html>'
    )


if IMPORT_ERROR is None:
    class _DiagnosticsWorker(QtCore.QObject):
        result_ready = QtCore.pyqtSignal(int, object)
        progress_ready = QtCore.pyqtSignal(int, object)

        def __init__(self, diagnostics_callback):
            super().__init__()
            self._diagnostics_callback = diagnostics_callback

        @QtCore.pyqtSlot(int, int, str, int, str, str, bool, int)
        def process(self, request_id, frame_index, view_mode, row, page, subpage, hide_noisy, row0_range_frames):
            try:
                current_thread = QtCore.QThread.currentThread()

                payload_provider = getattr(self._diagnostics_callback, 'describe_payload', None)
                if callable(payload_provider):
                    def report_progress(current, total, detail=None):
                        self.progress_ready.emit(
                            int(request_id),
                            {
                                'current': int(current),
                                'total': int(total),
                                'detail': detail,
                            },
                        )

                    def is_cancelled():
                        return bool(current_thread.isInterruptionRequested())

                    payload = payload_provider(
                        frame_index,
                        view_mode,
                        row,
                        page,
                        subpage,
                        row0_range_frames=row0_range_frames,
                        hide_noisy=hide_noisy,
                        progress_callback=report_progress,
                        cancel_callback=is_cancelled,
                    )
                else:
                    payload = {
                        'text': self._diagnostics_callback(frame_index, view_mode, row, page, subpage, row0_range_frames=row0_range_frames),
                        'summary': 'Current page/subpage: --',
                    }
            except Exception as exc:  # pragma: no cover - GUI path
                payload = {
                    'text': f'Diagnostics failed:\n{exc}',
                    'summary': 'Current page/subpage: --',
                }
            if bool(current_thread.isInterruptionRequested()):
                payload = {
                    'text': str(payload.get('text', '')),
                    'summary': str(payload.get('summary', 'Current page/subpage: --')),
                    'cancelled': True,
                }
            self.result_ready.emit(int(request_id), payload)


if IMPORT_ERROR is None:
    class _ClickableLabel(QtWidgets.QLabel):
        clicked = QtCore.pyqtSignal()

        def mousePressEvent(self, event):  # pragma: no cover - GUI path
            if event.button() == QtCore.Qt.LeftButton:
                self.clicked.emit()
                event.accept()
                return
            super().mousePressEvent(event)


if IMPORT_ERROR is None:
    class _RepairDeconvolveDialog(QtWidgets.QDialog):
        def __init__(
            self,
            *,
            start_frame=0,
            frame_count=1,
            initial_page='100',
            initial_row=0,
            parent=None,
        ):
            super().__init__(parent)
            self.setWindowFlags(_standard_window_flags())
            self.setModal(True)
            self.setWindowTitle('Deconvolve')
            self.resize(480, 180)

            self._start_frame = max(int(start_frame), 0)
            self._frame_count = max(int(frame_count), 1)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            range_label = QtWidgets.QLabel(
                f'Frames: {self._start_frame}..{self._start_frame + self._frame_count - 1}'
            )
            root.addWidget(range_label)

            form = QtWidgets.QGridLayout()
            form.setColumnStretch(1, 1)
            root.addLayout(form)

            form.addWidget(QtWidgets.QLabel('Mode'), 0, 0)
            self._mode_box = QtWidgets.QComboBox()
            self._mode_box.addItem('All Pages', 'all')
            self._mode_box.addItem('Page', 'page')
            self._mode_box.addItem('Row', 'row')
            self._mode_box.currentIndexChanged.connect(self._mode_changed)
            form.addWidget(self._mode_box, 0, 1)

            self._page_label = QtWidgets.QLabel('Page')
            form.addWidget(self._page_label, 1, 0)
            self._page_box = QtWidgets.QLineEdit(str(initial_page or '100').strip().upper() or '100')
            self._page_box.setMaximumWidth(100)
            self._page_box.setPlaceholderText('100')
            self._page_box.setInputMask('>HHH;_')
            self._page_box.textChanged.connect(self._sync_default_output_name)
            form.addWidget(self._page_box, 1, 1, alignment=QtCore.Qt.AlignLeft)

            self._row_label = QtWidgets.QLabel('Row')
            form.addWidget(self._row_label, 2, 0)
            self._row_box = QtWidgets.QSpinBox()
            self._row_box.setRange(0, 31)
            self._row_box.setValue(max(min(int(initial_row), 31), 0))
            self._row_box.valueChanged.connect(self._sync_default_output_name)
            form.addWidget(self._row_box, 2, 1, alignment=QtCore.Qt.AlignLeft)

            form.addWidget(QtWidgets.QLabel('Output'), 3, 0)
            output_row = QtWidgets.QHBoxLayout()
            self._output_box = QtWidgets.QLineEdit()
            output_row.addWidget(self._output_box, 1)
            browse_button = QtWidgets.QPushButton('Browse...')
            browse_button.clicked.connect(self._browse_output)
            output_row.addWidget(browse_button)
            form.addLayout(output_row, 3, 1)

            button_row = QtWidgets.QHBoxLayout()
            root.addLayout(button_row)
            button_row.addStretch(1)
            self._start_button = QtWidgets.QPushButton('Start')
            self._start_button.clicked.connect(self._accept_if_valid)
            button_row.addWidget(self._start_button)
            close_button = QtWidgets.QPushButton('Close')
            close_button.clicked.connect(self.reject)
            button_row.addWidget(close_button)

            self._mode_changed()
            self._sync_default_output_name()

        def _mode(self):
            return str(self._mode_box.currentData() or 'all')

        def _default_output_name(self):
            mode = self._mode()
            if mode == 'page':
                return f'P{self._page_box.text().strip().upper() or "100"}-deconvolved.t42'
            if mode == 'row':
                return f'R{int(self._row_box.value()):02d}-deconvolved.t42'
            return 'deconvolved.t42'

        def _sync_default_output_name(self):
            current = self._output_box.text().strip()
            if current and os.path.basename(current) != self._default_output_name():
                return
            self._output_box.setText(os.path.join(os.getcwd(), self._default_output_name()))

        def _mode_changed(self):
            mode = self._mode()
            page_visible = mode == 'page'
            row_visible = mode == 'row'
            self._page_label.setVisible(page_visible)
            self._page_box.setVisible(page_visible)
            self._row_label.setVisible(row_visible)
            self._row_box.setVisible(row_visible)
            self._sync_default_output_name()

        def _browse_output(self):
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Deconvolved Output',
                self._output_box.text().strip() or os.path.join(os.getcwd(), self._default_output_name()),
                'T42 files (*.t42);;All files (*)',
            )
            if filename:
                self._output_box.setText(filename)

        def _accept_if_valid(self):
            if not self._output_box.text().strip():
                QtWidgets.QMessageBox.warning(self, 'Deconvolve', 'Choose an output file first.')
                return
            if self._mode() == 'page' and not self._page_box.text().strip().upper():
                QtWidgets.QMessageBox.warning(self, 'Deconvolve', 'Enter a page value.')
                return
            self.accept()

        def values(self):
            return {
                'mode': self._mode(),
                'page_text': self._page_box.text().strip().upper() or '100',
                'row': int(self._row_box.value()),
                'output_path': self._output_box.text().strip(),
                'start_frame': int(self._start_frame),
                'frame_count': int(self._frame_count),
            }


if IMPORT_ERROR is None:
    class _StabilizeWorker(QtCore.QObject):
        progress_ready = QtCore.pyqtSignal(int, int)
        result_ready = QtCore.pyqtSignal(object)
        error_ready = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal()

        def __init__(self, stabilize_callback, kwargs):
            super().__init__()
            self._stabilize_callback = stabilize_callback
            self._kwargs = dict(kwargs)

        @QtCore.pyqtSlot()
        def process(self):
            try:
                def report_progress(current, total):
                    self.progress_ready.emit(int(current), int(total))

                kwargs = dict(self._kwargs)
                kwargs['progress_callback'] = report_progress
                result = self._stabilize_callback(**kwargs)
            except Exception as exc:  # pragma: no cover - GUI path
                self.error_ready.emit(str(exc))
            else:
                self.result_ready.emit(result)
            finally:
                self.finished.emit()


if IMPORT_ERROR is None:
    class _StabilizeAnalysisWorker(QtCore.QObject):
        progress_ready = QtCore.pyqtSignal(int, int)
        result_ready = QtCore.pyqtSignal(object)
        error_ready = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal()

        def __init__(self, analysis_callback, kwargs):
            super().__init__()
            self._analysis_callback = analysis_callback
            self._kwargs = dict(kwargs)

        @QtCore.pyqtSlot()
        def process(self):
            try:
                def report_progress(current, total):
                    self.progress_ready.emit(int(current), int(total))

                kwargs = dict(self._kwargs)
                kwargs['progress_callback'] = report_progress
                result = self._analysis_callback(**kwargs)
            except Exception as exc:  # pragma: no cover - GUI path
                self.error_ready.emit(str(exc))
            else:
                self.result_ready.emit(result)
            finally:
                self.finished.emit()


if IMPORT_ERROR is None:
    class _DeconvolveWorker(QtCore.QObject):
        progress_ready = QtCore.pyqtSignal(int, int)
        result_ready = QtCore.pyqtSignal(object)
        error_ready = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal()

        def __init__(self, deconvolve_callback, kwargs):
            super().__init__()
            self._deconvolve_callback = deconvolve_callback
            self._kwargs = dict(kwargs)

        @QtCore.pyqtSlot()
        def process(self):
            try:
                def report_progress(current, total):
                    self.progress_ready.emit(int(current), int(total))

                kwargs = dict(self._kwargs)
                kwargs['progress_callback'] = report_progress
                result = self._deconvolve_callback(**kwargs)
            except Exception as exc:  # pragma: no cover - GUI path
                self.error_ready.emit(str(exc))
            else:
                self.result_ready.emit(result)
            finally:
                self.finished.emit()


if IMPORT_ERROR is None:
    class VBIStabilizeDialog(QtWidgets.QDialog):
        def __init__(
            self,
            stabilize_callback,
            default_output_path='',
            *,
            line_count=32,
            total_frames=1,
            current_frame_provider=None,
            analysis_callback=None,
            preview_callback=None,
            clear_preview_callback=None,
            parent=None,
        ):
            super().__init__(parent)
            self._stabilize_callback = stabilize_callback
            self._line_count = max(int(line_count), 1)
            self._total_frames = max(int(total_frames), 1)
            self._current_frame_provider = current_frame_provider
            self._analysis_callback = analysis_callback
            self._preview_callback = preview_callback
            self._clear_preview_callback = clear_preview_callback
            self._running = False
            self._last_analysis = None
            self._progress_started_at = None
            self._analysis_started_at = None
            self._stabilize_thread = None
            self._stabilize_worker = None
            self._stabilize_output_path = ''
            self._stabilize_error_message = None
            self._analysis_thread = None
            self._analysis_worker = None
            self._analysis_pending = False
            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.setWindowTitle('Stabilize VBI')
            self.setMinimumSize(640, 420)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            info = QtWidgets.QLabel(
                'Use line 1 as the reference right wall for the whole KGI.\n'
                'Every selected line is shifted so its right edge aligns to that wall.\n'
                'This builds one ровный квадрат КГИ without manual per-line moves.'
            )
            info.setWordWrap(True)
            root.addWidget(info)
            info.setText(
                'The yellow wall defines one shared right border for the KGI.\n'
                'Before Preview, the dialog shows the original frame with right-edge markers for each line. '
                'After Preview, it shows the stabilized result for the selected frame.'
            )
            info.setText(
                'Analyze the original VBI frame and choose a reference wall for the whole KGI.\n'
                'Every selected line is shifted so its right edge aligns to that wall.\n'
                'This builds one stable KGI block without manual per-line moves.'
            )
            self._info_label = info

            content_row = QtWidgets.QHBoxLayout()
            content_row.setSpacing(12)
            root.addLayout(content_row, 1)

            settings_column = QtWidgets.QVBoxLayout()
            settings_column.setSpacing(10)
            content_row.addLayout(settings_column, 0)

            diagnostics_column = QtWidgets.QVBoxLayout()
            diagnostics_column.setSpacing(8)
            content_row.addLayout(diagnostics_column, 1)
            content_row.setStretch(0, 0)
            content_row.setStretch(1, 1)

            form = QtWidgets.QFormLayout()
            settings_column.addLayout(form)

            self._mode_box = QtWidgets.QComboBox()
            self._mode_box.addItem('Full File', 'full')
            self._mode_box.addItem('Preview', 'quick')
            self._mode_box.currentIndexChanged.connect(self._mode_changed)
            form.addRow('Mode', self._mode_box)

            self._stabilize_mode_box = QtWidgets.QComboBox()
            self._stabilize_mode_box.addItem('Reference Analysis', 'reference')
            self._stabilize_mode_box.addItem('TBC Right Wall', 'tbc')
            self._stabilize_mode_box.addItem('Reference Frame TBC', 'tbc_frame')
            self._stabilize_mode_box.addItem('Linear TBC', 'tbc_linear')
            self._stabilize_mode_box.currentIndexChanged.connect(self._mode_changed)
            form.addRow('Stabilize Mode', self._stabilize_mode_box)

            self._reference_mode_box = QtWidgets.QComboBox()
            self._reference_mode_box.addItem('Best Lines Median', 'median')
            self._reference_mode_box.addItem('Reference Line', 'line')
            self._reference_mode_box.currentIndexChanged.connect(self._mode_changed)
            form.addRow('Reference Mode', self._reference_mode_box)

            self._reference_line_box = QtWidgets.QSpinBox()
            self._reference_line_box.setRange(1, self._line_count)
            self._reference_line_box.setValue(1)
            form.addRow('Reference Line', self._reference_line_box)

            self._reference_frame_box = QtWidgets.QSpinBox()
            self._reference_frame_box.setRange(0, self._total_frames - 1)
            if callable(self._current_frame_provider):
                try:
                    current_reference_frame = max(min(int(self._current_frame_provider()), self._total_frames - 1), 0)
                except Exception:
                    current_reference_frame = 0
            else:
                current_reference_frame = 0
            self._reference_frame_box.setValue(current_reference_frame)
            form.addRow('Reference Frame', self._reference_frame_box)

            self._tolerance_box = QtWidgets.QSpinBox()
            self._tolerance_box.setRange(0, 32)
            self._tolerance_box.setValue(3)
            self._tolerance_box.setSuffix(' samples')
            form.addRow('Tolerance', self._tolerance_box)

            self._right_wall_sensitivity_box = QtWidgets.QSpinBox()
            self._right_wall_sensitivity_box.setRange(0, 100)
            self._right_wall_sensitivity_box.setValue(50)
            form.addRow('Right Wall Sensitivity', self._right_wall_sensitivity_box)

            manual_shift_group = QtWidgets.QGroupBox('Manual Line Shift')
            manual_shift_layout = QtWidgets.QVBoxLayout(manual_shift_group)
            manual_shift_form = QtWidgets.QFormLayout()
            self._manual_shift_line_box = QtWidgets.QSpinBox()
            self._manual_shift_line_box.setRange(1, self._line_count)
            manual_shift_form.addRow('Edit Line', self._manual_shift_line_box)
            self._manual_shift_value_box = QtWidgets.QSpinBox()
            self._manual_shift_value_box.setRange(-256, 256)
            self._manual_shift_value_box.setValue(0)
            self._manual_shift_value_box.setSuffix(' samples')
            manual_shift_form.addRow('Extra Shift', self._manual_shift_value_box)
            manual_shift_layout.addLayout(manual_shift_form)
            manual_shift_buttons = QtWidgets.QHBoxLayout()
            self._manual_shift_set_button = QtWidgets.QPushButton('Set')
            self._manual_shift_set_button.clicked.connect(self._set_manual_shift_for_line)
            manual_shift_buttons.addWidget(self._manual_shift_set_button)
            self._manual_shift_clear_button = QtWidgets.QPushButton('Clear')
            self._manual_shift_clear_button.clicked.connect(self._clear_manual_shift_for_line)
            manual_shift_buttons.addWidget(self._manual_shift_clear_button)
            manual_shift_buttons.addStretch(1)
            manual_shift_layout.addLayout(manual_shift_buttons)
            self._manual_shift_list = QtWidgets.QListWidget()
            self._manual_shift_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self._manual_shift_list.setMaximumHeight(96)
            manual_shift_layout.addWidget(self._manual_shift_list)
            self._manual_shift_map = {}
            settings_column.addWidget(manual_shift_group)

            quick_row = QtWidgets.QHBoxLayout()
            self._quick_frames_box = QtWidgets.QSpinBox()
            self._quick_frames_box.setRange(1, 5000)
            self._quick_frames_box.setValue(300)
            self._quick_frames_box.setSuffix(' frames')
            quick_row.addWidget(self._quick_frames_box)
            quick_row.addStretch(1)
            quick_container = QtWidgets.QWidget()
            quick_container.setLayout(quick_row)
            self._quick_container = quick_container
            form.addRow('Frames', quick_container)

            self._preview_box = QtWidgets.QCheckBox('Preview in VBI Viewer')
            self._preview_box.setChecked(False)
            self._preview_box.toggled.connect(self._preview_toggled)
            form.addRow('Preview', self._preview_box)

            self._show_diagnostics_box = QtWidgets.QCheckBox('Show diagnostics')
            self._show_diagnostics_box.setChecked(True)
            self._show_diagnostics_box.toggled.connect(self._diagnostics_toggled)
            form.addRow('Diagnostics', self._show_diagnostics_box)

            output_row = QtWidgets.QHBoxLayout()
            self._output_path_edit = QtWidgets.QLineEdit(str(default_output_path or ''))
            output_row.addWidget(self._output_path_edit, 1)
            browse_button = QtWidgets.QPushButton('Browse...')
            browse_button.clicked.connect(self._browse_output_path)
            output_row.addWidget(browse_button)
            self._browse_button = browse_button
            output_container = QtWidgets.QWidget()
            output_container.setLayout(output_row)
            form.addRow('Output', output_container)

            self._status_label = QtWidgets.QLabel('Ready. Showing original VBI.')
            diagnostics_column.addWidget(self._status_label)

            self._progress_bar = QtWidgets.QProgressBar()
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)
            diagnostics_column.addWidget(self._progress_bar)

            self._analysis_box = QtWidgets.QPlainTextEdit()
            self._analysis_box.setReadOnly(True)
            self._analysis_box.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            diagnostic_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self._analysis_box.setFont(diagnostic_font)
            self._analysis_box.setPlaceholderText('Analysis results will appear here.')
            self._analysis_box.setMinimumWidth(420)
            diagnostics_column.addWidget(self._analysis_box, 1)

            buttons = QtWidgets.QHBoxLayout()
            root.addLayout(buttons)
            buttons.addStretch(1)
            self._reset_button = QtWidgets.QPushButton('Reset')
            self._reset_button.clicked.connect(self._reset_values)
            buttons.addWidget(self._reset_button)
            self._start_button = QtWidgets.QPushButton('Start')
            self._start_button.clicked.connect(self._start_stabilization)
            buttons.addWidget(self._start_button)
            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            buttons.addWidget(self._close_button)

            self._preview_timer = QtCore.QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(220)
            self._preview_timer.timeout.connect(self._emit_preview_update)

            self._analysis_timer = QtCore.QTimer(self)
            self._analysis_timer.setSingleShot(True)
            self._analysis_timer.setInterval(220)
            self._analysis_timer.timeout.connect(self._emit_analysis_update)

            for widget, signal_name in (
                (self._mode_box, 'currentIndexChanged'),
                (self._stabilize_mode_box, 'currentIndexChanged'),
                (self._reference_mode_box, 'currentIndexChanged'),
                (self._reference_line_box, 'valueChanged'),
                (self._reference_frame_box, 'valueChanged'),
                (self._tolerance_box, 'valueChanged'),
                (self._right_wall_sensitivity_box, 'valueChanged'),
                (self._quick_frames_box, 'valueChanged'),
            ):
                getattr(widget, signal_name).connect(self._schedule_analysis_update)
                getattr(widget, signal_name).connect(self._schedule_preview_update)

            self._manual_shift_line_box.valueChanged.connect(self._manual_shift_line_changed)
            self._manual_shift_list.currentRowChanged.connect(self._manual_shift_list_selected)

            self._mode_changed()
            self._diagnostics_toggled(self._show_diagnostics_box.isChecked())
            self._update_manual_shift_list()
            self._set_analysis(None)
            if callable(self._clear_preview_callback):
                self._clear_preview_callback()
            self._schedule_analysis_update()

        def _browse_output_path(self):
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Stabilized VBI',
                self._output_path_edit.text().strip() or os.path.join(os.getcwd(), 'stabilized.vbi'),
                'VBI files (*.vbi);;All files (*)',
            )
            if filename:
                self._output_path_edit.setText(filename)

        def _set_running(self, running):
            running = bool(running)
            self._running = running
            self._start_button.setEnabled(not running)
            self._browse_button.setEnabled(not running)
            self._output_path_edit.setEnabled(not running)
            self._mode_box.setEnabled(not running)
            self._stabilize_mode_box.setEnabled(not running)
            self._reference_mode_box.setEnabled(not running)
            reference_mode = str(self._reference_mode_box.currentData() or 'median')
            stabilize_mode = str(self._stabilize_mode_box.currentData() or 'reference')
            self._reference_line_box.setEnabled(
                (not running)
                and stabilize_mode in ('reference', 'tbc')
                and reference_mode == 'line'
            )
            self._reference_frame_box.setEnabled((not running) and stabilize_mode in ('tbc_frame', 'tbc_linear'))
            self._tolerance_box.setEnabled((not running) and stabilize_mode == 'reference')
            self._right_wall_sensitivity_box.setEnabled((not running) and stabilize_mode in ('tbc', 'tbc_frame', 'tbc_linear'))
            self._quick_frames_box.setEnabled(not running and self._mode_box.currentData() == 'quick')
            self._preview_box.setEnabled(not running and self._preview_callback is not None)
            self._show_diagnostics_box.setEnabled(not running)
            self._manual_shift_line_box.setEnabled(not running)
            self._manual_shift_value_box.setEnabled(not running)
            self._manual_shift_set_button.setEnabled(not running)
            self._manual_shift_clear_button.setEnabled(not running)
            self._manual_shift_list.setEnabled(not running)
            self._reset_button.setEnabled(not running)
            self._close_button.setEnabled(not running)

        def _update_info_text(self):
            stabilize_mode = str(self._stabilize_mode_box.currentData() or 'reference')
            if stabilize_mode == 'tbc':
                self._info_label.setText(
                    'TBC Right Wall locks only the KGI geometry.\n'
                    'Each selected line is shifted so its right edge follows one common wall.\n'
                    'Use this when you want one stable square KGI block without signal repair.'
                )
                return
            if stabilize_mode == 'tbc_frame':
                self._info_label.setText(
                    'Reference Frame TBC uses one chosen frame as a geometry template.\n'
                    'Each selected line in every frame follows the matching line from that reference frame.\n'
                    'Use this when you already have one visually straight VBI frame.'
                )
                return
            if stabilize_mode == 'tbc_linear':
                self._info_label.setText(
                    'Linear TBC fits one straight right-wall line from a chosen reference frame.\n'
                    'Each selected line is then shifted toward that ideal straight wall.\n'
                    'Use this when you want a mathematically straight KGI edge.'
                )
                return
            self._info_label.setText(
                'Analyze the original VBI frame and choose a reference wall for the whole KGI.\n'
                'Every selected line is shifted so its right edge aligns to that wall.\n'
                'This builds one stable KGI block without manual per-line moves.'
            )

        def _mode_changed(self):
            quick = self._mode_box.currentData() == 'quick'
            self._quick_container.setVisible(bool(quick))
            self._quick_frames_box.setEnabled(bool(quick))
            reference_mode = str(self._reference_mode_box.currentData() or 'median')
            stabilize_mode = str(self._stabilize_mode_box.currentData() or 'reference')
            uses_reference_line = stabilize_mode in ('reference', 'tbc')
            self._reference_mode_box.setEnabled((not self._running) and uses_reference_line)
            self._reference_line_box.setEnabled((not self._running) and uses_reference_line and reference_mode == 'line')
            self._reference_frame_box.setEnabled((not self._running) and stabilize_mode in ('tbc_frame', 'tbc_linear'))
            self._tolerance_box.setEnabled((not self._running) and stabilize_mode == 'reference')
            self._right_wall_sensitivity_box.setEnabled((not self._running) and stabilize_mode in ('tbc', 'tbc_frame', 'tbc_linear'))
            self._update_info_text()

        def _update_manual_shift_list(self):
            current_line = int(self._manual_shift_line_box.value())
            self._manual_shift_list.blockSignals(True)
            self._manual_shift_list.clear()
            selected_row = -1
            for row_index, logical_line in enumerate(sorted(self._manual_shift_map)):
                shift = int(self._manual_shift_map[logical_line])
                self._manual_shift_list.addItem(f'L{logical_line:02d}: {shift:+d}')
                if logical_line == current_line:
                    selected_row = row_index
            if selected_row >= 0:
                self._manual_shift_list.setCurrentRow(selected_row)
            self._manual_shift_list.blockSignals(False)

        def _manual_shift_line_changed(self, value):
            logical_line = int(value)
            self._manual_shift_value_box.blockSignals(True)
            self._manual_shift_value_box.setValue(int(self._manual_shift_map.get(logical_line, 0)))
            self._manual_shift_value_box.blockSignals(False)
            self._update_manual_shift_list()

        def _manual_shift_list_selected(self, row):
            if row < 0:
                return
            item = self._manual_shift_list.item(int(row))
            if item is None:
                return
            text = str(item.text() or '')
            if not text.startswith('L'):
                return
            try:
                logical_line = int(text[1:3])
            except ValueError:
                return
            self._manual_shift_line_box.setValue(logical_line)

        def _set_manual_shift_for_line(self):
            logical_line = int(self._manual_shift_line_box.value())
            shift = int(self._manual_shift_value_box.value())
            if shift == 0:
                self._manual_shift_map.pop(logical_line, None)
            else:
                self._manual_shift_map[logical_line] = shift
            self._update_manual_shift_list()
            self._schedule_analysis_update()
            self._schedule_preview_update()

        def _clear_manual_shift_for_line(self):
            logical_line = int(self._manual_shift_line_box.value())
            self._manual_shift_map.pop(logical_line, None)
            self._manual_shift_value_box.setValue(0)
            self._update_manual_shift_list()
            self._schedule_analysis_update()
            self._schedule_preview_update()

        def _diagnostics_toggled(self, checked):
            self._analysis_box.setVisible(bool(checked))
            if checked:
                self._render_analysis()
                self._schedule_analysis_update()

        def _preview_settings(self):
            if callable(self._current_frame_provider):
                start_frame = max(int(self._current_frame_provider()), 0)
            else:
                start_frame = 0
            quick_mode = self._mode_box.currentData() == 'quick'
            return {
                'global_shift': 0,
                'lock_mode': str(self._stabilize_mode_box.currentData() or 'reference'),
                'target_center': 0,
                'target_right_edge': 0,
                'reference_mode': str(self._reference_mode_box.currentData() or 'median'),
                'reference_line': int(self._reference_line_box.value()),
                'reference_frame': int(self._reference_frame_box.value()),
                'tolerance': int(self._tolerance_box.value()),
                'right_wall_sensitivity': int(self._right_wall_sensitivity_box.value()),
                'manual_shift_map': dict(self._manual_shift_map),
                'quick_preview': bool(quick_mode),
                'preview_frames': int(self._quick_frames_box.value()),
                'start_frame': start_frame,
            }

        def _format_analysis_text(self, analysis):
            if not analysis:
                return 'No analysis yet.'
            lock_mode = str(analysis.get('lock_mode', 'reference') or 'reference')
            reference_mode = str(analysis.get('reference_mode', 'line') or 'line')
            reference_line = int(analysis.get('reference_line', 1))
            reference_frame = int(analysis.get('reference_frame', self._reference_frame_box.value()))
            reference_lines = [int(line) for line in analysis.get('reference_lines', [])]
            tolerance = float(analysis.get('tolerance', 0.0))
            right_wall_sensitivity = int(analysis.get('right_wall_sensitivity', self._right_wall_sensitivity_box.value()))
            reference_left = float(analysis.get('reference_left', 0.0))
            reference_right = float(analysis.get('reference_right', 0.0))
            reference_width = float(analysis.get('reference_width', 0.0))
            mode_label = 'Best Lines Median' if reference_mode == 'median' else 'Reference Line'
            if lock_mode == 'tbc':
                lines = [
                    'Stabilize mode: TBC Right Wall',
                    f'Reference mode: {mode_label}',
                    f'Reference line: L{reference_line:02d}',
                    f'Right wall sensitivity: {right_wall_sensitivity}',
                    f'Right wall: {reference_right:.1f}',
                    f'Reference box: L={reference_left:.1f}  R={reference_right:.1f}  W={reference_width:.1f}',
                    '',
                ]
            elif lock_mode == 'tbc_frame':
                lines = [
                    'Stabilize mode: Reference Frame TBC',
                    f'Reference frame: {reference_frame}',
                    f'Right wall sensitivity: {right_wall_sensitivity}',
                    f'Reference box: L={reference_left:.1f}  R={reference_right:.1f}  W={reference_width:.1f}',
                    '',
                ]
            elif lock_mode == 'tbc_linear':
                slope = float(analysis.get('linear_slope', 0.0))
                intercept = float(analysis.get('linear_intercept', reference_right))
                lines = [
                    'Stabilize mode: Linear TBC',
                    f'Reference frame: {reference_frame}',
                    f'Right wall sensitivity: {right_wall_sensitivity}',
                    f'Linear fit: right = {slope:+.3f} * line + {intercept:.1f}',
                    f'Reference box: L={reference_left:.1f}  R={reference_right:.1f}  W={reference_width:.1f}',
                    '',
                ]
            else:
                lines = [
                    'Stabilize mode: Reference Analysis',
                    f'Reference mode: {mode_label}',
                    f'Reference line: L{reference_line:02d}',
                    f'Reference box: L={reference_left:.1f}  R={reference_right:.1f}  W={reference_width:.1f}',
                    f'Tolerance: {tolerance:.1f} samples',
                    '',
                ]
            if reference_mode == 'median' and reference_lines:
                preview_lines = ', '.join(f'L{line:02d}' for line in reference_lines[:10])
                if len(reference_lines) > 10:
                    preview_lines += ', ...'
                lines.insert(2, f'Reference lines: {preview_lines}')
            per_line = dict(analysis.get('per_line', {}))
            if not per_line:
                lines.append('No analyzed lines.')
                return '\n'.join(lines)
            for logical_line in sorted(per_line):
                entry = per_line[logical_line]
                status = str(entry.get('status', 'ok'))
                shift = int(entry.get('shift', 0))
                gap_left = float(entry.get('gap_left', 0.0))
                overflow_left = float(entry.get('overflow_left', 0.0))
                if lock_mode in ('tbc', 'tbc_frame', 'tbc_linear'):
                    lines.append(
                        f'L{int(logical_line):02d} shift {shift:+d}'
                        f'  manual={int(entry.get("manual_shift", 0)):+d}'
                        f'  right={float(entry.get("right", 0.0)):.1f}'
                        f'  wall={float(entry.get("target_right", entry.get("shifted_right", 0.0))):.1f}'
                    )
                else:
                    lines.append(
                        f'L{int(logical_line):02d} shift {shift:+d} {status}'
                        f'  gap={gap_left:.1f}  overflow={overflow_left:.1f}'
                    )
            return '\n'.join(lines)

        def _render_analysis(self):
            self._analysis_box.setPlainText(self._format_analysis_text(self._last_analysis))

        def _set_analysis(self, analysis):
            self._last_analysis = analysis
            if self._show_diagnostics_box.isChecked():
                self._render_analysis()

        def _preview_toggled(self, checked):
            if not checked:
                self._preview_timer.stop()
                if callable(self._clear_preview_callback):
                    self._clear_preview_callback()
                self._status_label.setText('Ready. Showing original VBI.')
                return
            self._schedule_preview_update()

        def _schedule_analysis_update(self, *args):
            if (
                not callable(self._analysis_callback)
                or not self._show_diagnostics_box.isChecked()
                or self._running
            ):
                return
            self._analysis_timer.start()

        def _schedule_preview_update(self, *args):
            if (
                not callable(self._preview_callback)
                or not self._preview_box.isChecked()
                or self._running
            ):
                return
            self._preview_timer.start()

        def _emit_preview_update(self):
            if (
                not callable(self._preview_callback)
                or not self._preview_box.isChecked()
                or self._running
            ):
                return
            try:
                self._preview_callback(**self._preview_settings())
                self._status_label.setText('Preview updated from original VBI.')
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.warning(self, 'Stabilize VBI', f'Preview failed:\n{exc}')
                self._preview_box.setChecked(False)

        def _emit_analysis_update(self):
            if (
                not callable(self._analysis_callback)
                or not self._show_diagnostics_box.isChecked()
                or self._running
            ):
                return
            if self._analysis_thread is not None:
                self._analysis_pending = True
                return
            self._analysis_pending = False
            self._analysis_started_at = time.monotonic()
            self._status_label.setText('Analyzing original VBI...')
            self._progress_bar.setRange(0, 0)
            worker_kwargs = self._preview_settings()
            thread = QtCore.QThread(self)
            worker = _StabilizeAnalysisWorker(self._analysis_callback, worker_kwargs)
            worker.moveToThread(thread)
            thread.started.connect(worker.process)
            worker.progress_ready.connect(self._handle_analysis_progress)
            worker.result_ready.connect(self._handle_analysis_result)
            worker.error_ready.connect(self._handle_analysis_error)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(self._analysis_thread_finished)
            thread.finished.connect(thread.deleteLater)
            self._analysis_thread = thread
            self._analysis_worker = worker
            thread.start()

        def _reset_values(self):
            self._mode_box.setCurrentIndex(self._mode_box.findData('full'))
            self._stabilize_mode_box.setCurrentIndex(self._stabilize_mode_box.findData('reference'))
            self._reference_mode_box.setCurrentIndex(self._reference_mode_box.findData('median'))
            self._reference_line_box.setValue(1)
            self._reference_frame_box.setValue(0)
            self._tolerance_box.setValue(3)
            self._right_wall_sensitivity_box.setValue(50)
            self._manual_shift_map = {}
            self._manual_shift_line_box.setValue(1)
            self._manual_shift_value_box.setValue(0)
            self._update_manual_shift_list()
            self._quick_frames_box.setValue(300)
            self._show_diagnostics_box.setChecked(True)
            self._preview_box.setChecked(False)
            self._set_analysis(None)
            if callable(self._clear_preview_callback):
                self._clear_preview_callback()
            if not self._preview_box.isChecked():
                self._status_label.setText('Ready. Showing original VBI.')
            self._schedule_analysis_update()
            self._schedule_preview_update()

        def _format_eta(self, seconds):
            total = max(int(round(float(seconds))), 0)
            hours, rem = divmod(total, 3600)
            minutes, secs = divmod(rem, 60)
            if hours:
                return f'{hours:02d}:{minutes:02d}:{secs:02d}'
            return f'{minutes:02d}:{secs:02d}'

        def _start_stabilization(self):
            output_path = self._output_path_edit.text().strip()
            if not output_path:
                QtWidgets.QMessageBox.warning(self, 'Stabilize VBI', 'Choose an output .vbi file first.')
                return
            if self._running:
                return
            preview_frames = int(self._quick_frames_box.value())
            quick_mode = self._mode_box.currentData() == 'quick'
            if callable(self._current_frame_provider):
                current_frame = max(int(self._current_frame_provider()), 0)
            else:
                current_frame = 0
            self._set_running(True)
            self._progress_started_at = time.monotonic()
            self._status_label.setText('Starting stabilization...')
            self._progress_bar.setRange(0, 0)
            QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))
            self._stabilize_output_path = output_path
            self._stabilize_error_message = None
            worker_kwargs = {
                'output_path': output_path,
                'global_shift': 0,
                'lock_mode': str(self._stabilize_mode_box.currentData() or 'reference'),
                'target_center': 0,
                'target_right_edge': 0,
                'reference_mode': str(self._reference_mode_box.currentData() or 'median'),
                'reference_line': int(self._reference_line_box.value()),
                'reference_frame': int(self._reference_frame_box.value()),
                'tolerance': int(self._tolerance_box.value()),
                'quick_preview': bool(quick_mode),
                'preview_frames': preview_frames,
                'start_frame': current_frame,
            }
            thread = QtCore.QThread(self)
            worker = _StabilizeWorker(self._stabilize_callback, worker_kwargs)
            worker.moveToThread(thread)
            thread.started.connect(worker.process)
            worker.progress_ready.connect(self._handle_stabilize_progress)
            worker.result_ready.connect(self._handle_stabilize_result)
            worker.error_ready.connect(self._handle_stabilize_error)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(self._stabilize_thread_finished)
            thread.finished.connect(thread.deleteLater)
            self._stabilize_thread = thread
            self._stabilize_worker = worker
            thread.start()

        def _handle_stabilize_progress(self, current, total):
            total = max(int(total), 1)
            current = max(0, min(int(current), total))
            percent = int(round((float(current) / float(total)) * 100.0))
            elapsed = max(time.monotonic() - float(self._progress_started_at or time.monotonic()), 0.0)
            remaining = ((elapsed / float(current)) * float(total - current)) if current > 0 else 0.0
            self._status_label.setText(
                f'Stabilizing... {percent}% ({current}/{total}) '
                f'[{self._format_eta(elapsed)}<{self._format_eta(remaining)}]'
            )
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)

        def _handle_stabilize_result(self, analysis):
            return

        def _handle_stabilize_error(self, message):
            self._stabilize_error_message = str(message or 'Unknown stabilization error.')

        def _handle_analysis_progress(self, current, total):
            if self._running:
                return
            total = max(int(total), 1)
            current = max(0, min(int(current), total))
            percent = int(round((float(current) / float(total)) * 100.0))
            elapsed = max(time.monotonic() - float(self._analysis_started_at or time.monotonic()), 0.0)
            remaining = ((elapsed / float(current)) * float(total - current)) if current > 0 else 0.0
            self._status_label.setText(
                f'Analyzing original VBI... {percent}% ({current}/{total}) '
                f'[{self._format_eta(elapsed)}<{self._format_eta(remaining)}]'
            )
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)

        def _handle_analysis_result(self, analysis):
            if not self._running:
                self._set_analysis(analysis)

        def _handle_analysis_error(self, message):
            if self._running:
                return
            self._status_label.setText('Analysis failed.')
            QtWidgets.QMessageBox.warning(self, 'Stabilize VBI', str(message or 'Analysis failed.'))

        def _stabilize_thread_finished(self):
            try:
                QtWidgets.QApplication.restoreOverrideCursor()
            except Exception:  # pragma: no cover - GUI path
                pass
            self._stabilize_thread = None
            self._stabilize_worker = None
            self._set_running(False)
            if self._stabilize_error_message:
                self._status_label.setText('Failed.')
                QtWidgets.QMessageBox.critical(self, 'Stabilize VBI', self._stabilize_error_message)
                return
            elapsed = max(time.monotonic() - float(self._progress_started_at or time.monotonic()), 0.0)
            self._status_label.setText(f'Done in {self._format_eta(elapsed)}.')
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(1)
            QtWidgets.QMessageBox.information(
                self,
                'Stabilize VBI',
                f'Saved stabilized VBI to:\n{self._stabilize_output_path}',
            )
            self._schedule_analysis_update()

        def _analysis_thread_finished(self):
            self._analysis_thread = None
            self._analysis_worker = None
            if self._running:
                return
            if self._analysis_pending:
                self._analysis_pending = False
                self._schedule_analysis_update()
                return
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)
            if self._preview_box.isChecked():
                self._status_label.setText('Ready. Preview enabled.')
            else:
                self._status_label.setText('Ready. Showing original VBI.')

        def closeEvent(self, event):  # pragma: no cover - GUI path
            if self._running or self._analysis_thread is not None:
                event.ignore()
                return
            self._analysis_timer.stop()
            self._preview_timer.stop()
            if callable(self._clear_preview_callback):
                self._clear_preview_callback()
            super().closeEvent(event)


if IMPORT_ERROR is None:
    class _WallLockPreviewWidget(QtWidgets.QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._image = None
            self._payload = {}
            self.setMinimumSize(560, 300)

        def set_preview_payload(self, payload):
            payload = dict(payload or {})
            self._payload = payload
            line_arrays = tuple(payload.get('line_arrays', ()))
            self._image = self._build_image(line_arrays)
            self.update()

        def set_wall_x(self, wall_x):
            self._payload['wall_x'] = float(wall_x)
            self.update()

        def _build_image(self, line_arrays):
            if not line_arrays:
                return None
            height = max(len(line_arrays), 1)
            width = max((len(row) for row in line_arrays), default=1)
            if width <= 0:
                width = 1
            canvas = np.zeros((height, width), dtype=np.uint8)
            for row_index, row in enumerate(line_arrays):
                if not row:
                    continue
                values = np.asarray(row, dtype=np.uint8)
                canvas[row_index, :values.size] = values
            red = np.clip((canvas.astype(np.float32) * 0.78) + 36.0, 0.0, 255.0).astype(np.uint8)
            green = np.clip((canvas.astype(np.float32) * 0.58) + 18.0, 0.0, 255.0).astype(np.uint8)
            blue = np.clip((canvas.astype(np.float32) * 0.42) + 10.0, 0.0, 255.0).astype(np.uint8)
            rgb = np.dstack((red, green, blue))
            image = QtGui.QImage(rgb.data, width, height, width * 3, QtGui.QImage.Format_RGB888)
            return image.copy()

        def _target_rect(self):
            rect = self.rect().adjusted(10, 10, -34, -10)
            return QtCore.QRect(
                rect.x(),
                rect.y(),
                max(rect.width(), 1),
                max(rect.height(), 1),
            )

        def paintEvent(self, event):  # pragma: no cover - GUI path
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor('#000000'))
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            target = self._target_rect()
            if self._image is not None:
                painter.drawImage(target, self._image)
            sample_count = max(int(self._payload.get('sample_count', 1)), 1)
            line_count = max(int(self._payload.get('line_count', 1)), 1)
            right_edges = dict(self._payload.get('right_edges', {}))
            wall_x = float(self._payload.get('wall_x', 0.0))
            if wall_x <= 0.0:
                if right_edges:
                    wall_x = max(float(value) for value in right_edges.values())
                else:
                    wall_x = float(sample_count - 1)
            wall_x = min(max(wall_x, 0.0), float(sample_count - 1))
            painter.setPen(QtGui.QPen(QtGui.QColor('#3df2ff'), 2))
            for logical_line, right_edge in right_edges.items():
                line_index = max(int(logical_line) - 1, 0)
                clamped_edge = min(max(float(right_edge), 0.0), float(sample_count - 1))
                marker_x = target.left() + int(round((clamped_edge / float(sample_count)) * max(target.width() - 1, 1)))
                marker_y = target.top() + int(round(((line_index + 0.5) / float(line_count)) * max(target.height() - 1, 1)))
                painter.drawLine(marker_x, marker_y - 6, marker_x, marker_y + 6)
            painter.setPen(QtGui.QPen(QtGui.QColor('#ffd400'), 2))
            wall_pos = target.left() + int(round((wall_x / float(sample_count)) * max(target.width() - 1, 1)))
            painter.drawLine(wall_pos, target.top(), wall_pos, target.bottom())
            painter.setPen(QtGui.QPen(QtGui.QColor('#f5f5f5')))
            for logical_line in range(1, line_count + 1):
                marker_y = target.top() + int(round((((logical_line - 1) + 0.5) / float(line_count)) * max(target.height() - 1, 1)))
                painter.drawText(target.right() + 8, marker_y + 4, str(int(logical_line)))
            mode_text = 'Preview' if str(self._payload.get('mode', 'source')) == 'preview' else 'Original'
            painter.setPen(QtGui.QPen(QtGui.QColor('#9ed59e')))
            painter.drawText(12, 20, mode_text)


if IMPORT_ERROR is None:
    class VBIWallLockDialog(QtWidgets.QDialog):
        def __init__(
            self,
            stabilize_callback,
            preview_callback,
            default_output_path='',
            *,
            line_count=32,
            total_frames=1,
            current_frame_provider=None,
            parent=None,
        ):
            super().__init__(parent)
            self._stabilize_callback = stabilize_callback
            self._preview_callback = preview_callback
            self._current_frame_provider = current_frame_provider
            self._line_count = max(int(line_count), 1)
            self._total_frames = max(int(total_frames), 1)
            self._running = False
            self._last_preview_payload = None
            self._manual_shift_map = {}
            self._worker_thread = None
            self._worker = None
            self._worker_error = None
            self._worker_result = None
            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.setWindowTitle('Stabilize VBI')
            self.resize(1120, 760)
            self.setMinimumSize(980, 680)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            info = QtWidgets.QLabel(
                'Жёлтая стенка задаёт общую правую границу КГИ.\n'
                'До Preview показывается оригинальный кадр с метками правого края строк. '
                'После Preview показывается уже готовый результат на кадре.'
            )
            info.setWordWrap(True)
            root.addWidget(info)
            info.setText(
                'The yellow wall defines one shared right border for the KGI.\n'
                'Before Preview, the dialog shows the original frame with right-edge markers for each line. '
                'After Preview, it shows the stabilized result for the selected frame.'
            )

            content = QtWidgets.QHBoxLayout()
            content.setSpacing(12)
            root.addLayout(content, 1)

            controls = QtWidgets.QFormLayout()
            controls.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            controls_widget = QtWidgets.QWidget()
            controls_widget.setLayout(controls)
            controls_widget.setMaximumWidth(330)
            content.addWidget(controls_widget, 0)

            self._preview_widget = _WallLockPreviewWidget()
            content.addWidget(self._preview_widget, 1)

            current_frame = 0
            if callable(self._current_frame_provider):
                try:
                    current_frame = max(min(int(self._current_frame_provider()), self._total_frames - 1), 0)
                except Exception:
                    current_frame = 0

            self._reference_frame_box = QtWidgets.QSpinBox()
            self._reference_frame_box.setRange(0, self._total_frames - 1)
            self._reference_frame_box.setValue(current_frame)
            controls.addRow('Reference Frame', self._reference_frame_box)

            self._preview_frame_box = QtWidgets.QSpinBox()
            self._preview_frame_box.setRange(0, self._total_frames - 1)
            self._preview_frame_box.setValue(current_frame)
            controls.addRow('Preview Frame', self._preview_frame_box)

            self._scope_box = QtWidgets.QComboBox()
            self._scope_box.addItem('Reference -> End', 'to_end')
            self._scope_box.addItem('Reference -> N Frames', 'frames')
            self._scope_box.addItem('Whole File', 'whole')
            controls.addRow('Apply To', self._scope_box)

            self._frames_count_box = QtWidgets.QSpinBox()
            self._frames_count_box.setRange(1, self._total_frames)
            self._frames_count_box.setValue(min(300, self._total_frames))
            self._frames_count_box.setSuffix(' frames')
            controls.addRow('Frames Count', self._frames_count_box)

            self._wall_x_box = QtWidgets.QDoubleSpinBox()
            self._wall_x_box.setRange(0.0, 8192.0)
            self._wall_x_box.setDecimals(1)
            self._wall_x_box.setSingleStep(1.0)
            controls.addRow('Wall X', self._wall_x_box)

            self._block_shift_box = QtWidgets.QSpinBox()
            self._block_shift_box.setRange(-512, 512)
            self._block_shift_box.setValue(0)
            self._block_shift_box.setSuffix(' samples')
            controls.addRow('Block Shift', self._block_shift_box)

            self._right_wall_sensitivity_box = QtWidgets.QSpinBox()
            self._right_wall_sensitivity_box.setRange(0, 100)
            self._right_wall_sensitivity_box.setValue(50)
            controls.addRow('Right Wall Sensitivity', self._right_wall_sensitivity_box)

            manual_shift_group = QtWidgets.QGroupBox('Manual Line Shift')
            manual_shift_layout = QtWidgets.QVBoxLayout(manual_shift_group)
            manual_shift_form = QtWidgets.QFormLayout()
            self._manual_shift_line_box = QtWidgets.QSpinBox()
            self._manual_shift_line_box.setRange(1, self._line_count)
            manual_shift_form.addRow('Edit Line', self._manual_shift_line_box)
            self._manual_shift_value_box = QtWidgets.QSpinBox()
            self._manual_shift_value_box.setRange(-256, 256)
            self._manual_shift_value_box.setValue(0)
            self._manual_shift_value_box.setSuffix(' samples')
            manual_shift_form.addRow('Extra Shift', self._manual_shift_value_box)
            manual_shift_layout.addLayout(manual_shift_form)
            manual_shift_buttons = QtWidgets.QHBoxLayout()
            self._manual_shift_set_button = QtWidgets.QPushButton('Set')
            self._manual_shift_set_button.clicked.connect(self._set_manual_shift_for_line)
            manual_shift_buttons.addWidget(self._manual_shift_set_button)
            self._manual_shift_clear_button = QtWidgets.QPushButton('Clear')
            self._manual_shift_clear_button.clicked.connect(self._clear_manual_shift_for_line)
            manual_shift_buttons.addWidget(self._manual_shift_clear_button)
            manual_shift_buttons.addStretch(1)
            manual_shift_layout.addLayout(manual_shift_buttons)
            self._manual_shift_list = QtWidgets.QListWidget()
            self._manual_shift_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self._manual_shift_list.setMaximumHeight(96)
            manual_shift_layout.addWidget(self._manual_shift_list)
            controls.addRow(manual_shift_group)

            output_row = QtWidgets.QHBoxLayout()
            self._output_path_edit = QtWidgets.QLineEdit(str(default_output_path or ''))
            output_row.addWidget(self._output_path_edit, 1)
            self._browse_button = QtWidgets.QPushButton('Browse...')
            self._browse_button.clicked.connect(self._browse_output_path)
            output_row.addWidget(self._browse_button)
            output_widget = QtWidgets.QWidget()
            output_widget.setLayout(output_row)
            controls.addRow('Output', output_widget)

            self._status_label = QtWidgets.QLabel('Ready. Showing original frame with wall markers.')
            root.addWidget(self._status_label)

            self._progress_bar = QtWidgets.QProgressBar()
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)
            root.addWidget(self._progress_bar)

            buttons = QtWidgets.QHBoxLayout()
            root.addLayout(buttons)
            buttons.addStretch(1)
            self._original_button = QtWidgets.QPushButton('Original')
            self._original_button.clicked.connect(self._show_original)
            buttons.addWidget(self._original_button)
            self._preview_button = QtWidgets.QPushButton('Preview')
            self._preview_button.clicked.connect(self._run_preview)
            buttons.addWidget(self._preview_button)
            self._start_button = QtWidgets.QPushButton('Start')
            self._start_button.clicked.connect(self._start_apply)
            buttons.addWidget(self._start_button)
            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            buttons.addWidget(self._close_button)

            self._scope_box.currentIndexChanged.connect(self._scope_changed)
            self._reference_frame_box.valueChanged.connect(self._reload_source_preview)
            self._preview_frame_box.valueChanged.connect(self._reload_source_preview)
            self._right_wall_sensitivity_box.valueChanged.connect(self._reload_source_preview)
            self._wall_x_box.valueChanged.connect(self._wall_overlay_changed)
            self._block_shift_box.valueChanged.connect(self._wall_overlay_changed)
            self._manual_shift_line_box.valueChanged.connect(self._manual_shift_line_changed)
            self._manual_shift_list.currentRowChanged.connect(self._manual_shift_list_selected)

            self._scope_changed()
            self._update_manual_shift_list()
            self._reload_source_preview()

        def _browse_output_path(self):  # pragma: no cover - GUI path
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Stabilized VBI',
                self._output_path_edit.text().strip() or '',
                'VBI files (*.vbi);;All files (*)',
            )
            if filename:
                self._output_path_edit.setText(filename)

        def _scope_changed(self):
            self._frames_count_box.setVisible(str(self._scope_box.currentData() or 'to_end') == 'frames')

        def _update_manual_shift_list(self):
            current_line = int(self._manual_shift_line_box.value())
            self._manual_shift_list.blockSignals(True)
            self._manual_shift_list.clear()
            selected_row = -1
            for row_index, logical_line in enumerate(sorted(self._manual_shift_map)):
                shift = int(self._manual_shift_map[logical_line])
                self._manual_shift_list.addItem(f'L{logical_line:02d}: {shift:+d}')
                if logical_line == current_line:
                    selected_row = row_index
            if selected_row >= 0:
                self._manual_shift_list.setCurrentRow(selected_row)
            self._manual_shift_list.blockSignals(False)

        def _manual_shift_line_changed(self, value):
            logical_line = int(value)
            self._manual_shift_value_box.blockSignals(True)
            self._manual_shift_value_box.setValue(int(self._manual_shift_map.get(logical_line, 0)))
            self._manual_shift_value_box.blockSignals(False)
            self._update_manual_shift_list()

        def _manual_shift_list_selected(self, row):
            if row < 0:
                return
            item = self._manual_shift_list.item(int(row))
            if item is None:
                return
            text = str(item.text() or '')
            if not text.startswith('L'):
                return
            try:
                logical_line = int(text[1:3])
            except ValueError:
                return
            self._manual_shift_line_box.setValue(logical_line)

        def _refresh_preview_mode(self):
            if dict(self._last_preview_payload or {}).get('mode') == 'preview':
                self._run_preview()
            else:
                self._reload_source_preview()

        def _set_manual_shift_for_line(self):
            logical_line = int(self._manual_shift_line_box.value())
            shift = int(self._manual_shift_value_box.value())
            if shift == 0:
                self._manual_shift_map.pop(logical_line, None)
            else:
                self._manual_shift_map[logical_line] = shift
            self._update_manual_shift_list()
            self._refresh_preview_mode()

        def _clear_manual_shift_for_line(self):
            logical_line = int(self._manual_shift_line_box.value())
            self._manual_shift_map.pop(logical_line, None)
            self._manual_shift_value_box.setValue(0)
            self._update_manual_shift_list()
            self._refresh_preview_mode()

        def _preview_settings(self, *, preview_enabled=False):
            return {
                'reference_frame': int(self._reference_frame_box.value()),
                'preview_frame': int(self._preview_frame_box.value()),
                'wall_x': float(self._wall_x_box.value()),
                'block_shift': int(self._block_shift_box.value()),
                'right_wall_sensitivity': int(self._right_wall_sensitivity_box.value()),
                'manual_shift_map': dict(self._manual_shift_map),
                'preview_enabled': bool(preview_enabled),
            }

        def _reload_source_preview(self):
            if not callable(self._preview_callback):
                return
            try:
                payload = self._preview_callback(**self._preview_settings(preview_enabled=False))
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.warning(self, 'Stabilize VBI', f'Failed to load source preview:\n{exc}')
                return
            if payload is None:
                return
            self._last_preview_payload = dict(payload)
            self._wall_x_box.blockSignals(True)
            self._wall_x_box.setValue(float(payload.get('base_wall_x', payload.get('wall_x', 0.0))))
            self._wall_x_box.blockSignals(False)
            payload = dict(payload)
            payload['wall_x'] = float(self._wall_x_box.value()) + float(self._block_shift_box.value())
            self._preview_widget.set_preview_payload(payload)
            self._status_label.setText('Ready. Showing original frame with wall markers.')
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)

        def _wall_overlay_changed(self):
            if self._last_preview_payload is None:
                return
            payload = dict(self._last_preview_payload)
            payload['wall_x'] = float(self._wall_x_box.value()) + float(self._block_shift_box.value())
            self._preview_widget.set_preview_payload(payload)
            self._status_label.setText('Wall updated. Press Preview to see the stabilized result.')

        def _show_original(self):
            self._reload_source_preview()

        def _run_preview(self):
            if not callable(self._preview_callback):
                return
            try:
                payload = self._preview_callback(**self._preview_settings(preview_enabled=True))
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.warning(self, 'Stabilize VBI', f'Preview failed:\n{exc}')
                return
            if payload is None:
                return
            self._last_preview_payload = dict(payload)
            self._preview_widget.set_preview_payload(payload)
            self._status_label.setText('Preview shows the stabilized result for the selected frame.')

        def _set_running(self, running):
            self._running = bool(running)
            for widget in (
                self._reference_frame_box,
                self._preview_frame_box,
                self._scope_box,
                self._frames_count_box,
                self._wall_x_box,
                self._block_shift_box,
                self._right_wall_sensitivity_box,
                self._manual_shift_line_box,
                self._manual_shift_value_box,
                self._manual_shift_set_button,
                self._manual_shift_clear_button,
                self._manual_shift_list,
                self._output_path_edit,
                self._browse_button,
                self._original_button,
                self._preview_button,
            ):
                widget.setEnabled(not self._running)
            self._start_button.setEnabled(not self._running)
            self._close_button.setEnabled(not self._running)

        def _start_apply(self):
            output_path = self._output_path_edit.text().strip()
            if not output_path:
                QtWidgets.QMessageBox.warning(self, 'Stabilize VBI', 'Choose an output .vbi file first.')
                return
            if self._running or not callable(self._stabilize_callback):
                return
            kwargs = {
                'output_path': output_path,
                'reference_frame': int(self._reference_frame_box.value()),
                'scope_mode': str(self._scope_box.currentData() or 'to_end'),
                'frame_count': int(self._frames_count_box.value()),
                'wall_x': float(self._wall_x_box.value()),
                'block_shift': int(self._block_shift_box.value()),
                'right_wall_sensitivity': int(self._right_wall_sensitivity_box.value()),
                'manual_shift_map': dict(self._manual_shift_map),
            }
            self._set_running(True)
            self._status_label.setText('Applying wall lock stabilization...')
            self._progress_bar.setRange(0, 0)
            thread = QtCore.QThread(self)
            worker = _StabilizeWorker(self._stabilize_callback, kwargs)
            worker.moveToThread(thread)
            thread.started.connect(worker.process)
            worker.progress_ready.connect(self._handle_progress)
            worker.result_ready.connect(self._handle_result)
            worker.error_ready.connect(self._handle_error)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._worker_finished)
            self._worker_thread = thread
            self._worker = worker
            thread.start()

        def _handle_progress(self, current, total):
            total = max(int(total), 1)
            current = max(0, min(int(current), total))
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            percent = int(round((float(current) / float(total)) * 100.0))
            self._status_label.setText(f'Applying wall lock stabilization... {percent}% ({current}/{total})')

        def _handle_result(self, result):
            self._worker_result = dict(result or {})

        def _handle_error(self, message):
            self._worker_error = str(message or 'Unknown stabilization error.')

        def _worker_finished(self):
            self._set_running(False)
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(1)
            error = getattr(self, '_worker_error', None)
            result = getattr(self, '_worker_result', None)
            self._worker_thread = None
            self._worker = None
            self._worker_error = None
            self._worker_result = None
            if error:
                self._status_label.setText('Failed.')
                QtWidgets.QMessageBox.critical(self, 'Stabilize VBI', error)
                return
            self._status_label.setText('Done.')
            if result:
                QtWidgets.QMessageBox.information(
                    self,
                    'Stabilize VBI',
                    f"Saved stabilized VBI to:\n{self._output_path_edit.text().strip()}",
                )

        def closeEvent(self, event):  # pragma: no cover - GUI path
            if self._running:
                event.ignore()
                return
            super().closeEvent(event)


if IMPORT_ERROR is None:
    class TeletextMonitorWindow(QtWidgets.QDialog):
        _diagnostic_request = QtCore.pyqtSignal(int, int, str, int, str, str, bool, int)

        def __init__(
            self,
            state,
            total_frames,
            frame_rate=DEFAULT_FRAME_RATE,
            diagnostics_callback=None,
            viewer_process=None,
            title='Teletext Monitor',
            parent=None,
        ):
            super().__init__(parent)
            self._state = state
            self._total_frames = max(int(total_frames), 1)
            self._frame_rate = float(frame_rate)
            self._diagnostics_callback = diagnostics_callback
            self._viewer_process = viewer_process
            self._updating = False
            self._last_diagnostics_text = None
            self._last_diagnostics_summary = None
            self._diagnostic_request_counter = 0
            self._diagnostic_worker_busy = False
            self._active_diagnostic_request_id = None
            self._pending_diagnostic_request = None
            self._diagnostic_worker_thread = None
            self._diagnostic_worker = None
            self._deconvolve_thread = None
            self._deconvolve_worker = None
            self._deconvolve_progress_dialog = None
            self._deconvolve_error_message = None
            self._deconvolve_result = None
            self._deconvolve_values = None
            self._deconvolve_running = False
            self._current_page_entries = ()
            self._diagnostic_busy_started_at = None
            self._close_when_idle = False

            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.setWindowTitle(str(title))
            self.resize(860, 520)
            self.setMinimumSize(720, 420)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            self._status_label = QtWidgets.QLabel('')
            root.addWidget(self._status_label)

            self._current_page_label = _ClickableLabel('Current page/subpage: --')
            self._current_page_label.setWordWrap(True)
            self._current_page_label.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            self._current_page_label.setToolTip('Click to select the currently transmitted page/subpage.')
            self._current_page_label.clicked.connect(self._show_current_page_menu)
            root.addWidget(self._current_page_label)

            mode_layout = QtWidgets.QHBoxLayout()
            root.addLayout(mode_layout)

            mode_layout.addWidget(QtWidgets.QLabel('View'))
            self._diagnostic_mode_box = QtWidgets.QComboBox()
            self._diagnostic_mode_box.addItem('Packets', 'packets')
            self._diagnostic_mode_box.addItem('Row 0 Range', 'row0range')
            self._diagnostic_mode_box.addItem('Row', 'row')
            self._diagnostic_mode_box.addItem('Page', 'page')
            self._diagnostic_mode_box.currentIndexChanged.connect(self._diagnostic_mode_changed)
            mode_layout.addWidget(self._diagnostic_mode_box)

            self._row_label = QtWidgets.QLabel('Row')
            mode_layout.addWidget(self._row_label)
            self._row_box = QtWidgets.QSpinBox()
            self._row_box.setRange(0, 31)
            self._row_box.setValue(0)
            self._row_box.valueChanged.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._row_box)

            self._page_label = QtWidgets.QLabel('Page')
            mode_layout.addWidget(self._page_label)
            self._page_box = QtWidgets.QLineEdit('100')
            self._page_box.setMaximumWidth(80)
            self._page_box.setPlaceholderText('100')
            self._page_box.setInputMask('>HHH;_')
            self._page_box.textChanged.connect(self._schedule_diagnostics)
            self._page_model = QtCore.QStringListModel(self)
            self._page_completer = QtWidgets.QCompleter(self._page_model, self)
            self._page_completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            self._page_completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            self._page_box.setCompleter(self._page_completer)
            mode_layout.addWidget(self._page_box)
            self._page_auto_button = QtWidgets.QPushButton('Auto')
            self._page_auto_button.setCheckable(True)
            self._page_auto_button.toggled.connect(self._page_auto_toggled)
            mode_layout.addWidget(self._page_auto_button)

            self._subpage_label = QtWidgets.QLabel('Subpage')
            mode_layout.addWidget(self._subpage_label)
            self._subpage_box = QtWidgets.QLineEdit('')
            self._subpage_box.setMaximumWidth(80)
            self._subpage_box.setPlaceholderText('best')
            self._subpage_box.setMaxLength(4)
            self._subpage_validator = QtGui.QRegularExpressionValidator(QtCore.QRegularExpression('[0-9A-Fa-f]{0,4}'), self)
            self._subpage_box.setValidator(self._subpage_validator)
            self._subpage_box.textChanged.connect(self._schedule_diagnostics)
            self._subpage_model = QtCore.QStringListModel(self)
            self._subpage_completer = QtWidgets.QCompleter(self._subpage_model, self)
            self._subpage_completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            self._subpage_completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            self._subpage_box.setCompleter(self._subpage_completer)
            mode_layout.addWidget(self._subpage_box)
            self._subpage_auto_button = QtWidgets.QPushButton('Auto')
            self._subpage_auto_button.setCheckable(True)
            self._subpage_auto_button.toggled.connect(self._subpage_auto_toggled)
            mode_layout.addWidget(self._subpage_auto_button)

            self._row0_range_label = QtWidgets.QLabel('Range')
            mode_layout.addWidget(self._row0_range_label)
            self._row0_range_box = QtWidgets.QSpinBox()
            self._row0_range_box.setRange(1, self._total_frames)
            self._row0_range_box.setValue(min(15, self._total_frames))
            self._row0_range_box.setSuffix(' frames')
            self._row0_range_box.valueChanged.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._row0_range_box)

            self._noise_box = QtWidgets.QCheckBox('Noise')
            self._noise_box.setChecked(False)
            self._noise_box.toggled.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._noise_box)

            mode_layout.addWidget(QtWidgets.QLabel('Update'))
            self._diagnostic_update_mode_box = QtWidgets.QComboBox()
            self._diagnostic_update_mode_box.addItem('Auto', 'auto')
            self._diagnostic_update_mode_box.addItem('Manual', 'manual')
            self._diagnostic_update_mode_box.currentIndexChanged.connect(self._diagnostic_update_mode_changed)
            mode_layout.addWidget(self._diagnostic_update_mode_box)

            mode_layout.addWidget(QtWidgets.QLabel('Delay'))
            self._diagnostic_delay_box = QtWidgets.QSpinBox()
            self._diagnostic_delay_box.setRange(0, 2000)
            self._diagnostic_delay_box.setSingleStep(50)
            self._diagnostic_delay_box.setSuffix(' ms')
            self._diagnostic_delay_box.setValue(220)
            self._diagnostic_delay_box.valueChanged.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._diagnostic_delay_box)

            self._refresh_button = QtWidgets.QPushButton('Refresh')
            self._refresh_button.clicked.connect(lambda: self._schedule_diagnostics(force=True))
            self._refresh_button.setEnabled(False)
            mode_layout.addWidget(self._refresh_button)
            mode_layout.addStretch(1)

            self._diagnostic_hint = QtWidgets.QLabel(
                'Packets shows decoded rows from the current frame. '
                'Row and Page use the selected frame range. '
                'Row 0 Range scans row 0 packets from the current frame over the selected frame window.'
            )
            self._diagnostic_hint.setWordWrap(True)
            root.addWidget(self._diagnostic_hint)

            font_family = _diagnostic_font_family()
            if font_family is not None:
                font = QtGui.QFont(font_family)
                font.setStyleHint(QtGui.QFont.TypeWriter)
                font.setPointSize(12)
            else:
                font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
                font.setPointSize(max(font.pointSize(), 10))

            self._diagnostic_view_group = QtWidgets.QGroupBox('Teletext Monitor')
            self._diagnostic_view_group.setStyleSheet(
                'QGroupBox {'
                'border: 1px solid #2f5f2f;'
                'border-radius: 2px;'
                'margin-top: 8px;'
                'padding-top: 10px;'
                '}'
                'QGroupBox::title {'
                'subcontrol-origin: margin;'
                'left: 10px;'
                'padding: 0 4px;'
                'color: #9ed59e;'
                '}'
            )
            diagnostic_view_layout = QtWidgets.QVBoxLayout(self._diagnostic_view_group)
            diagnostic_view_layout.setContentsMargins(6, 10, 6, 6)
            diagnostic_view_layout.setSpacing(0)
            self._diagnostic_text = QtWidgets.QTextBrowser()
            self._diagnostic_text.setReadOnly(True)
            self._diagnostic_text.setOpenLinks(False)
            self._diagnostic_text.setOpenExternalLinks(False)
            self._diagnostic_text.setUndoRedoEnabled(False)
            self._diagnostic_text.setFont(font)
            self._diagnostic_text.setStyleSheet(
                'QTextBrowser {'
                'background-color: #000000;'
                'color: #f5f5f5;'
                'selection-background-color: #1d551d;'
                'selection-color: #ffffff;'
                'border: 1px solid #244024;'
                '}'
            )
            diagnostic_view_layout.addWidget(self._diagnostic_text)
            root.addWidget(self._diagnostic_view_group, 1)

            button_row = QtWidgets.QHBoxLayout()
            root.addLayout(button_row)
            button_row.addStretch(1)

            self._diagnostic_busy_label = QtWidgets.QLabel('Updating...')
            self._diagnostic_busy_label.setStyleSheet('color: #6ea86e;')
            self._diagnostic_busy_label.hide()
            button_row.addWidget(self._diagnostic_busy_label)

            self._diagnostic_progress = QtWidgets.QProgressBar()
            self._diagnostic_progress.setRange(0, 0)
            self._diagnostic_progress.setTextVisible(False)
            self._diagnostic_progress.setFixedWidth(96)
            self._diagnostic_progress.hide()
            button_row.addWidget(self._diagnostic_progress)

            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            button_row.addWidget(self._close_button)

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(120)
            self._timer.timeout.connect(self._sync_from_state)
            self._timer.start()

            self._diagnostic_timer = QtCore.QTimer(self)
            self._diagnostic_timer.setSingleShot(True)
            self._diagnostic_timer.timeout.connect(self._trigger_diagnostics_request)

            if self._diagnostics_callback is not None:
                self._diagnostic_worker_thread = QtCore.QThread(self)
                self._diagnostic_worker = _DiagnosticsWorker(self._diagnostics_callback)
                self._diagnostic_worker.moveToThread(self._diagnostic_worker_thread)
                self._diagnostic_worker.result_ready.connect(self._handle_diagnostic_result)
                self._diagnostic_worker.progress_ready.connect(self._handle_diagnostic_progress)
                self._diagnostic_request.connect(self._diagnostic_worker.process, QtCore.Qt.QueuedConnection)
                self._diagnostic_worker_thread.start()

            self._diagnostic_mode_changed()
            self._sync_from_state()

        def _format_time(self, frame_index):
            seconds = max(float(frame_index) / self._frame_rate, 0.0)
            minutes = int(seconds // 60)
            whole_seconds = int(seconds % 60)
            centiseconds = int(round((seconds - int(seconds)) * 100))
            return f'{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}'

        def _sync_from_state(self):
            viewer_process = self._viewer_process() if callable(self._viewer_process) else self._viewer_process
            current = int(self._state.current_frame())
            summary = f'Frame {current} | Time {self._format_time(current)}'
            if viewer_process is not None and not viewer_process.is_alive():
                summary += ' | Viewer stopped'
            if summary != self._status_label.text():
                self._status_label.setText(summary)
            self._schedule_diagnostics()

        def _diagnostic_mode(self):
            return str(self._diagnostic_mode_box.currentData() or 'packets')

        def _diagnostic_update_mode(self):
            return str(self._diagnostic_update_mode_box.currentData() or 'auto')

        def _diagnostic_mode_changed(self):
            mode = self._diagnostic_mode()
            row_visible = mode == 'row'
            page_visible = mode == 'page'
            row0_range_visible = mode in ('row', 'page', 'row0range')
            self._row_label.setVisible(row_visible)
            self._row_box.setVisible(row_visible)
            self._page_label.setVisible(page_visible)
            self._page_box.setVisible(page_visible)
            self._page_auto_button.setVisible(page_visible)
            self._subpage_label.setVisible(page_visible)
            self._subpage_box.setVisible(page_visible)
            self._subpage_auto_button.setVisible(page_visible)
            self._row0_range_label.setVisible(row0_range_visible)
            self._row0_range_box.setVisible(row0_range_visible)
            self._schedule_diagnostics(force=not self._state.is_playing())

        def _page_auto_toggled(self, checked):
            if checked:
                self._apply_auto_page_suggestion()

        def _apply_auto_page_suggestion(self, suggestions=None):
            if not self._page_auto_button.isChecked():
                return
            items = tuple(str(value).strip().upper() for value in (suggestions or ()) if str(value).strip())
            if not items:
                return
            target = items[0]
            if self._page_box.text().strip().upper() == target:
                return
            self._page_box.blockSignals(True)
            self._page_box.setText(target)
            self._page_box.blockSignals(False)
            self._schedule_diagnostics()

        def _subpage_auto_toggled(self, checked):
            if checked:
                self._apply_auto_subpage_suggestion()

        def _apply_auto_subpage_suggestion(self, suggestions=None):
            if not self._subpage_auto_button.isChecked():
                return
            items = tuple(str(value).strip().upper() for value in (suggestions or ()) if str(value).strip())
            if not items:
                return
            target = items[0]
            if self._subpage_box.text().strip().upper() == target:
                return
            self._subpage_box.blockSignals(True)
            self._subpage_box.setText(target)
            self._subpage_box.blockSignals(False)
            self._schedule_diagnostics()

        def _show_current_page_menu(self):
            entries = tuple(self._current_page_entries or ())
            if not entries:
                return
            menu = QtWidgets.QMenu(self)
            for page_text, subpage_text in entries:
                action = menu.addAction(f'P{page_text}/{subpage_text}')
                action.triggered.connect(
                    lambda checked=False, p=page_text, s=subpage_text: self._select_current_page_entry(p, s)
                )
            menu.exec_(QtGui.QCursor.pos())

        def _select_current_page_entry(self, page_text, subpage_text):
            self._page_auto_button.setChecked(False)
            self._subpage_auto_button.setChecked(False)
            if self._diagnostic_mode() != 'page':
                self._diagnostic_mode_box.setCurrentIndex(self._diagnostic_mode_box.findData('page'))
            self._page_box.blockSignals(True)
            self._subpage_box.blockSignals(True)
            self._page_box.setText(str(page_text).strip().upper())
            self._subpage_box.setText(str(subpage_text).strip().upper())
            self._page_box.blockSignals(False)
            self._subpage_box.blockSignals(False)
            self._schedule_diagnostics(force=not self._state.is_playing())

        def _diagnostic_update_mode_changed(self):
            manual = self._diagnostic_update_mode() == 'manual'
            self._diagnostic_delay_box.setEnabled(not manual)
            self._refresh_button.setEnabled(manual)
            if manual:
                self._diagnostic_timer.stop()
            self._schedule_diagnostics(force=not manual)

        def _diagnostic_delay_ms(self):
            delay = int(self._diagnostic_delay_box.value())
            if self._state.is_playing():
                delay = max(delay, 300)
                if self._diagnostic_mode() == 'page':
                    delay = max(delay, 650)
                elif self._diagnostic_mode() == 'row0range':
                    delay = max(delay, 900)
            elif self._diagnostic_mode() == 'page':
                delay = max(delay, 120)
            elif self._diagnostic_mode() == 'row0range':
                delay = max(delay, 250)
            return delay

        def _schedule_diagnostics(self, *args, force=False):
            if self._diagnostics_callback is None:
                return
            if getattr(self, '_deconvolve_running', False):
                return
            if force:
                self._diagnostic_timer.stop()
                self._trigger_diagnostics_request()
                return
            if self._diagnostic_update_mode() == 'manual':
                return
            if self._diagnostic_timer.isActive():
                return
            self._diagnostic_timer.start(self._diagnostic_delay_ms())

        def _format_elapsed(self, seconds):
            seconds = max(float(seconds), 0.0)
            minutes = int(seconds // 60)
            whole_seconds = int(seconds % 60)
            return f'{minutes:02d}:{whole_seconds:02d}'

        def _set_diagnostic_busy(self, busy, current=0, total=0, detail=None):
            if busy and total > 0:
                percent = int(round((float(current) / float(total)) * 100.0))
                parts = [f'Updating... {percent}%']
                if detail:
                    parts.append(str(detail))
                elif self._diagnostic_busy_started_at is not None:
                    parts.append(self._format_elapsed(time.monotonic() - self._diagnostic_busy_started_at))
                self._diagnostic_busy_label.setText(' | '.join(parts))
                self._diagnostic_progress.setRange(0, max(int(total), 1))
                self._diagnostic_progress.setValue(max(0, min(int(current), int(total))))
            else:
                self._diagnostic_busy_started_at = None
                self._diagnostic_busy_label.setText('Updating...')
                self._diagnostic_progress.setRange(0, 0)
            self._diagnostic_busy_label.setVisible(bool(busy))
            self._diagnostic_progress.setVisible(bool(busy))

        def _next_diagnostic_request_id(self):
            self._diagnostic_request_counter += 1
            return self._diagnostic_request_counter

        def _dispatch_pending_diagnostic_request(self):
            if self._pending_diagnostic_request is None or self._diagnostic_worker is None:
                return
            request = self._pending_diagnostic_request
            self._pending_diagnostic_request = None
            self._active_diagnostic_request_id = request[0]
            self._diagnostic_worker_busy = True
            self._diagnostic_busy_started_at = time.monotonic()
            self._set_diagnostic_busy(True, 0, 0)
            self._diagnostic_request.emit(*request)

        def _trigger_diagnostics_request(self):
            if self._diagnostics_callback is None:
                return
            request = (
                self._next_diagnostic_request_id(),
                int(self._state.current_frame()),
                self._diagnostic_mode(),
                int(self._row_box.value()),
                self._page_box.text().strip() or '100',
                self._subpage_box.text().strip().upper(),
                not bool(self._noise_box.isChecked()),
                int(self._row0_range_box.value()),
            )
            if self._diagnostic_worker is None:
                payload_provider = getattr(self._diagnostics_callback, 'describe_payload', None)
                if callable(payload_provider):
                    payload = payload_provider(
                        request[1],
                        request[2],
                        request[3],
                        request[4],
                        request[5],
                        row0_range_frames=request[7],
                        hide_noisy=request[6],
                    )
                else:
                    payload = {
                        'text': self._diagnostics_callback(
                            request[1],
                            request[2],
                            request[3],
                            request[4],
                            request[5],
                            row0_range_frames=request[7],
                        ),
                        'summary': 'Current page/subpage: --',
                    }
                self._handle_diagnostic_result(request[0], payload)
                return
            self._pending_diagnostic_request = request
            if not self._diagnostic_worker_busy:
                self._dispatch_pending_diagnostic_request()

        def _handle_diagnostic_progress(self, request_id, payload):
            if int(request_id) != int(self._active_diagnostic_request_id or request_id):
                return
            if isinstance(payload, dict):
                current = int(payload.get('current', 0))
                total = int(payload.get('total', 0))
                detail = payload.get('detail')
            else:
                current = int(payload)
                total = 0
                detail = None
            self._set_diagnostic_busy(True, current, total, detail)

        def _handle_diagnostic_result(self, request_id, payload):
            if int(request_id) != int(self._active_diagnostic_request_id or request_id):
                return
            self._diagnostic_worker_busy = False
            self._active_diagnostic_request_id = None
            self._set_diagnostic_busy(False)
            if not isinstance(payload, dict):
                payload = {
                    'text': str(payload),
                    'summary': 'Current page/subpage: --',
                }
            summary = str(payload.get('summary') or 'Current page/subpage: --')
            text = str(payload.get('text', ''))
            if summary != self._last_diagnostics_summary:
                self._current_page_label.setText(summary)
                self._last_diagnostics_summary = summary
            current_page_entries = []
            for entry in payload.get('current_page_entries', ()):
                if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                    continue
                page_text = str(entry[0]).strip().upper()
                subpage_text = str(entry[1]).strip().upper()
                if not page_text or not subpage_text:
                    continue
                current_page_entries.append((page_text, subpage_text))
            self._current_page_entries = tuple(current_page_entries)
            page_suggestions = [str(value).strip().upper() for value in payload.get('page_suggestions', ()) if str(value).strip()]
            self._page_model.setStringList(page_suggestions)
            self._page_box.setPlaceholderText(page_suggestions[0] if page_suggestions else '100')
            page_auto_suggestions = [str(value).strip().upper() for value in payload.get('page_auto_suggestions', ()) if str(value).strip()]
            self._apply_auto_page_suggestion(page_auto_suggestions or page_suggestions)
            subpage_suggestions = [str(value).strip().upper() for value in payload.get('subpage_suggestions', ()) if str(value).strip()]
            self._subpage_model.setStringList(subpage_suggestions)
            self._subpage_box.setPlaceholderText(subpage_suggestions[0] if subpage_suggestions else 'best')
            subpage_auto_suggestions = [str(value).strip().upper() for value in payload.get('subpage_auto_suggestions', ()) if str(value).strip()]
            self._apply_auto_subpage_suggestion(subpage_auto_suggestions or subpage_suggestions)
            if text != self._last_diagnostics_text:
                self._diagnostic_text.setHtml(_ansi_text_to_html(text, font_family=_diagnostic_font_family()))
                self._last_diagnostics_text = text
            if self._close_when_idle:
                self._close_when_idle = False
                self._shutdown_diagnostic_worker()
                QtCore.QTimer.singleShot(0, self.close)
            elif self._pending_diagnostic_request is not None:
                self._dispatch_pending_diagnostic_request()

        def _shutdown_diagnostic_worker(self):
            self._pending_diagnostic_request = None
            self._active_diagnostic_request_id = None
            self._diagnostic_worker_busy = False
            worker = self._diagnostic_worker
            thread = self._diagnostic_worker_thread
            self._diagnostic_worker = None
            self._diagnostic_worker_thread = None
            if worker is not None:
                try:
                    self._diagnostic_request.disconnect(worker.process)
                except Exception:
                    pass
                try:
                    worker.result_ready.disconnect()
                except Exception:
                    pass
                try:
                    worker.progress_ready.disconnect()
                except Exception:
                    pass
                worker.deleteLater()
            if thread is not None:
                thread.requestInterruption()
                thread.quit()
                if not thread.wait(10000):
                    thread.requestInterruption()
                    thread.quit()
                    thread.wait(30000)
                thread.deleteLater()

        def closeEvent(self, event):  # pragma: no cover - GUI path
            self._timer.stop()
            self._diagnostic_timer.stop()
            self._pending_diagnostic_request = None
            if self._diagnostic_worker_busy:
                self._close_when_idle = True
                if self._diagnostic_worker_thread is not None:
                    self._diagnostic_worker_thread.requestInterruption()
                self.hide()
                event.ignore()
                return
            self._shutdown_diagnostic_worker()
            super().closeEvent(event)


if IMPORT_ERROR is None:
    class VBIRepairWindow(QtWidgets.QDialog):
        _diagnostic_request = QtCore.pyqtSignal(int, int, str, int, str, str, bool, int)

        def __init__(
            self,
            state,
            total_frames,
            frame_rate=DEFAULT_FRAME_RATE,
            save_callback=None,
            stabilize_callback=None,
            stabilize_default_path='',
            stabilize_line_count=32,
            stabilize_analysis_callback=None,
            stabilize_preview_callback=None,
            clear_stabilize_preview_callback=None,
            save_page_callback=None,
            deconvolve_page_callback=None,
            live_tune_callback=None,
            monitor_callback=None,
            capture_tuning_range_callback=None,
            tuning_ranges_changed_callback=None,
            initial_tuning_ranges=(),
            viewer_process=None,
            diagnostics_callback=None,
            parent=None,
        ):
            super().__init__(parent)
            self._state = state
            self._total_frames = max(int(total_frames), 1)
            self._frame_rate = float(frame_rate)
            self._save_callback = save_callback
            self._stabilize_callback = stabilize_callback
            self._stabilize_default_path = str(stabilize_default_path or '')
            self._stabilize_line_count = max(int(stabilize_line_count), 1)
            self._stabilize_analysis_callback = stabilize_analysis_callback
            self._stabilize_preview_callback = stabilize_preview_callback
            self._clear_stabilize_preview_callback = clear_stabilize_preview_callback
            self._save_page_callback = save_page_callback
            self._deconvolve_page_callback = deconvolve_page_callback
            self._live_tune_callback = live_tune_callback
            self._monitor_callback = monitor_callback
            self._capture_tuning_range_callback = capture_tuning_range_callback
            self._tuning_ranges_changed_callback = tuning_ranges_changed_callback
            self._viewer_process = viewer_process
            self._diagnostics_callback = diagnostics_callback
            self._updating = False
            self._last_diagnostics_text = None
            self._last_diagnostics_summary = None
            self._diagnostic_request_counter = 0
            self._diagnostic_worker_busy = False
            self._active_diagnostic_request_id = None
            self._pending_diagnostic_request = None
            self._diagnostic_worker_thread = None
            self._diagnostic_worker = None
            self._current_page_entries = ()
            self._diagnostic_busy_started_at = None
            self._close_when_idle = False
            self._tuning_ranges = list(normalise_tuning_ranges(initial_tuning_ranges, total_frames=self._total_frames))
            self._selected_tuning_range_index = None
            self._selection_history = []
            self._selection_history_index = -1
            self._selection_history_restoring = False

            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.setWindowTitle('VBI Repair')
            self.resize(860, 860)
            self.setMinimumSize(860, 700)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            self._status_label = QtWidgets.QLabel('')
            root.addWidget(self._status_label)

            self._current_page_label = _ClickableLabel('Current page/subpage: --')
            self._current_page_label.setWordWrap(True)
            self._current_page_label.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            self._current_page_label.setToolTip('Click to select the currently transmitted page/subpage.')
            self._current_page_label.clicked.connect(self._show_current_page_menu)
            root.addWidget(self._current_page_label)

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
            controls_layout.addStretch(1)

            selection_group = QtWidgets.QGroupBox('Selection')
            selection_layout = QtWidgets.QGridLayout(selection_group)
            selection_layout.setColumnStretch(1, 1)
            selection_layout.setColumnStretch(3, 1)
            root.addWidget(selection_group)

            self._selection_slider = FrameRangeSlider(0, self._total_frames - 1, 0, self._total_frames - 1)
            self._selection_slider.rangeChanged.connect(self._selection_slider_changed)
            selection_layout.addWidget(self._selection_slider, 0, 0, 1, 9)

            selection_layout.addWidget(QtWidgets.QLabel('Start'), 1, 0)
            self._selection_start_box = QtWidgets.QSpinBox()
            self._selection_start_box.setRange(0, self._total_frames - 1)
            self._selection_start_box.setMaximumWidth(96)
            self._selection_start_box.valueChanged.connect(self._selection_box_changed)
            selection_layout.addWidget(self._selection_start_box, 1, 1)

            selection_layout.addWidget(QtWidgets.QLabel('End'), 1, 2)
            self._selection_end_box = QtWidgets.QSpinBox()
            self._selection_end_box.setRange(0, self._total_frames - 1)
            self._selection_end_box.setMaximumWidth(96)
            self._selection_end_box.valueChanged.connect(self._selection_box_changed)
            selection_layout.addWidget(self._selection_end_box, 1, 3)

            self._selection_mark_start_button = QtWidgets.QPushButton('Mark Start')
            self._selection_mark_start_button.clicked.connect(self._selection_mark_start)
            selection_layout.addWidget(self._selection_mark_start_button, 1, 4)

            self._selection_mark_end_button = QtWidgets.QPushButton('Mark End')
            self._selection_mark_end_button.clicked.connect(self._selection_mark_end)
            selection_layout.addWidget(self._selection_mark_end_button, 1, 5)

            self._selection_reset_button = QtWidgets.QPushButton('Reset')
            self._selection_reset_button.clicked.connect(self._reset_selection)

            self._selection_undo_button = QtWidgets.QPushButton('Undo')
            self._selection_undo_button.clicked.connect(self._selection_undo)
            selection_layout.addWidget(self._selection_undo_button, 1, 6)

            self._selection_redo_button = QtWidgets.QPushButton('Redo')
            self._selection_redo_button.clicked.connect(self._selection_redo)
            selection_layout.addWidget(self._selection_redo_button, 1, 7)

            selection_layout.addWidget(self._selection_reset_button, 1, 8)

            selection_layout.addWidget(QtWidgets.QLabel('Save Frames'), 2, 7)
            self._selection_save_frames_box = QtWidgets.QSpinBox()
            self._selection_save_frames_box.setRange(1, self._total_frames)
            self._selection_save_frames_box.setValue(self._total_frames)
            self._selection_save_frames_box.setSuffix(' frames')
            self._selection_save_frames_box.valueChanged.connect(self._selection_save_frames_changed)
            selection_layout.addWidget(self._selection_save_frames_box, 2, 8)
            self._selection_save_span = self._total_frames

            self._selection_apply_button = QtWidgets.QPushButton('Add Range')
            self._selection_apply_button.clicked.connect(self._add_tuning_range)
            self._selection_apply_button.setEnabled(self._capture_tuning_range_callback is not None)
            self._selection_update_button = QtWidgets.QPushButton('Update Range')
            self._selection_update_button.clicked.connect(self._update_tuning_range)
            self._selection_update_button.setEnabled(False)

            self._selection_remove_button = QtWidgets.QPushButton('Delete Range')
            self._selection_remove_button.clicked.connect(self._remove_tuning_range)
            self._selection_remove_button.setEnabled(False)

            self._tuning_ranges_group = QtWidgets.QGroupBox('Tuning Ranges')
            self._tuning_ranges_group.setCheckable(True)
            self._tuning_ranges_group.setChecked(False)
            self._tuning_ranges_group.setSizePolicy(
                QtWidgets.QSizePolicy.Preferred,
                QtWidgets.QSizePolicy.Maximum,
            )
            self._tuning_ranges_group.toggled.connect(self._tuning_ranges_visibility_changed)
            tuning_ranges_outer_layout = QtWidgets.QVBoxLayout(self._tuning_ranges_group)
            tuning_ranges_outer_layout.setContentsMargins(8, 8, 8, 8)

            self._tuning_ranges_container = QtWidgets.QWidget()
            self._tuning_ranges_container.setSizePolicy(
                QtWidgets.QSizePolicy.Preferred,
                QtWidgets.QSizePolicy.Maximum,
            )
            tuning_ranges_outer_layout.addWidget(self._tuning_ranges_container)
            tuning_ranges_layout = QtWidgets.QGridLayout(self._tuning_ranges_container)
            tuning_ranges_layout.setColumnStretch(0, 1)

            self._tuning_ranges_list = QtWidgets.QListWidget()
            self._tuning_ranges_list.setMinimumHeight(60)
            self._tuning_ranges_list.setMaximumHeight(96)
            self._tuning_ranges_list.currentRowChanged.connect(self._tuning_range_selected)
            tuning_ranges_layout.addWidget(self._tuning_ranges_list, 0, 0, 3, 1)

            tuning_ranges_buttons = QtWidgets.QVBoxLayout()
            tuning_ranges_buttons.setContentsMargins(0, 0, 0, 0)
            tuning_ranges_buttons.setSpacing(6)
            tuning_ranges_buttons.addWidget(self._selection_apply_button)
            tuning_ranges_buttons.addWidget(self._selection_update_button)
            tuning_ranges_buttons.addWidget(self._selection_remove_button)
            tuning_ranges_buttons.addStretch(1)
            tuning_ranges_layout.addLayout(tuning_ranges_buttons, 0, 1, 3, 1)

            selection_layout.addWidget(self._tuning_ranges_group, 3, 0, 1, 9)

            diagnostics_group = QtWidgets.QGroupBox('Diagnostics')
            diagnostics_layout = QtWidgets.QVBoxLayout(diagnostics_group)
            root.addWidget(diagnostics_group, 1)

            mode_layout = QtWidgets.QHBoxLayout()
            diagnostics_layout.addLayout(mode_layout)

            mode_layout.addWidget(QtWidgets.QLabel('View'))
            self._diagnostic_mode_box = QtWidgets.QComboBox()
            self._diagnostic_mode_box.addItem('Packets', 'packets')
            self._diagnostic_mode_box.addItem('Row 0 Range', 'row0range')
            self._diagnostic_mode_box.addItem('Row', 'row')
            self._diagnostic_mode_box.addItem('Page', 'page')
            self._diagnostic_mode_box.currentIndexChanged.connect(self._diagnostic_mode_changed)
            mode_layout.addWidget(self._diagnostic_mode_box)

            self._row_label = QtWidgets.QLabel('Row')
            mode_layout.addWidget(self._row_label)
            self._row_box = QtWidgets.QSpinBox()
            self._row_box.setRange(0, 31)
            self._row_box.setValue(0)
            self._row_box.valueChanged.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._row_box)

            self._page_label = QtWidgets.QLabel('Page')
            mode_layout.addWidget(self._page_label)
            self._page_box = QtWidgets.QLineEdit('100')
            self._page_box.setMaximumWidth(80)
            self._page_box.setPlaceholderText('100')
            self._page_box.setInputMask('>HHH;_')
            self._page_box.textChanged.connect(self._schedule_diagnostics)
            self._page_model = QtCore.QStringListModel(self)
            self._page_completer = QtWidgets.QCompleter(self._page_model, self)
            self._page_completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            self._page_completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            self._page_box.setCompleter(self._page_completer)
            mode_layout.addWidget(self._page_box)
            self._page_auto_button = QtWidgets.QPushButton('Auto')
            self._page_auto_button.setCheckable(True)
            self._page_auto_button.toggled.connect(self._page_auto_toggled)
            mode_layout.addWidget(self._page_auto_button)

            self._subpage_label = QtWidgets.QLabel('Subpage')
            mode_layout.addWidget(self._subpage_label)
            self._subpage_box = QtWidgets.QLineEdit('')
            self._subpage_box.setMaximumWidth(80)
            self._subpage_box.setPlaceholderText('best')
            self._subpage_box.setMaxLength(4)
            self._subpage_validator = QtGui.QRegularExpressionValidator(QtCore.QRegularExpression('[0-9A-Fa-f]{0,4}'), self)
            self._subpage_box.setValidator(self._subpage_validator)
            self._subpage_box.textChanged.connect(self._schedule_diagnostics)
            self._subpage_model = QtCore.QStringListModel(self)
            self._subpage_completer = QtWidgets.QCompleter(self._subpage_model, self)
            self._subpage_completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            self._subpage_completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            self._subpage_box.setCompleter(self._subpage_completer)
            mode_layout.addWidget(self._subpage_box)
            self._subpage_auto_button = QtWidgets.QPushButton('Auto')
            self._subpage_auto_button.setCheckable(True)
            self._subpage_auto_button.toggled.connect(self._subpage_auto_toggled)
            mode_layout.addWidget(self._subpage_auto_button)

            self._row0_range_label = QtWidgets.QLabel('Range')
            mode_layout.addWidget(self._row0_range_label)
            self._row0_range_box = QtWidgets.QSpinBox()
            self._row0_range_box.setRange(1, self._total_frames)
            self._row0_range_box.setValue(min(15, self._total_frames))
            self._row0_range_box.setSuffix(' frames')
            self._row0_range_box.valueChanged.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._row0_range_box)

            self._noise_box = QtWidgets.QCheckBox('Noise')
            self._noise_box.setChecked(False)
            self._noise_box.toggled.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._noise_box)

            mode_layout.addWidget(QtWidgets.QLabel('Update'))
            self._diagnostic_update_mode_box = QtWidgets.QComboBox()
            self._diagnostic_update_mode_box.addItem('Auto', 'auto')
            self._diagnostic_update_mode_box.addItem('Manual', 'manual')
            self._diagnostic_update_mode_box.currentIndexChanged.connect(self._diagnostic_update_mode_changed)
            mode_layout.addWidget(self._diagnostic_update_mode_box)

            mode_layout.addWidget(QtWidgets.QLabel('Delay'))
            self._diagnostic_delay_box = QtWidgets.QSpinBox()
            self._diagnostic_delay_box.setRange(0, 2000)
            self._diagnostic_delay_box.setSingleStep(50)
            self._diagnostic_delay_box.setSuffix(' ms')
            self._diagnostic_delay_box.setValue(220)
            self._diagnostic_delay_box.valueChanged.connect(self._schedule_diagnostics)
            mode_layout.addWidget(self._diagnostic_delay_box)

            self._refresh_button = QtWidgets.QPushButton('Refresh')
            self._refresh_button.clicked.connect(lambda: self._schedule_diagnostics(force=True))
            self._refresh_button.setEnabled(False)
            mode_layout.addWidget(self._refresh_button)
            mode_layout.addStretch(1)

            self._diagnostic_hint = QtWidgets.QLabel(
                'Packets shows decoded rows from the current frame. '
                'Row and Page use the selected frame range. '
                'Row 0 Range scans row 0 packets from the current frame over the selected frame window.'
            )
            self._diagnostic_hint.setWordWrap(True)
            diagnostics_layout.addWidget(self._diagnostic_hint)

            font_family = _diagnostic_font_family()
            if font_family is not None:
                font = QtGui.QFont(font_family)
                font.setStyleHint(QtGui.QFont.TypeWriter)
                font.setPointSize(12)
            else:
                font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
                font.setPointSize(max(font.pointSize(), 10))
            self._diagnostic_view_group = QtWidgets.QGroupBox('Teletext Monitor')
            self._diagnostic_view_group.setStyleSheet(
                'QGroupBox {'
                'border: 1px solid #2f5f2f;'
                'border-radius: 2px;'
                'margin-top: 8px;'
                'padding-top: 10px;'
                '}'
                'QGroupBox::title {'
                'subcontrol-origin: margin;'
                'left: 10px;'
                'padding: 0 4px;'
                'color: #9ed59e;'
                '}'
            )
            diagnostic_view_layout = QtWidgets.QVBoxLayout(self._diagnostic_view_group)
            diagnostic_view_layout.setContentsMargins(6, 10, 6, 6)
            diagnostic_view_layout.setSpacing(0)
            self._diagnostic_text = QtWidgets.QTextBrowser()
            self._diagnostic_text.setReadOnly(True)
            self._diagnostic_text.setOpenLinks(False)
            self._diagnostic_text.setOpenExternalLinks(False)
            self._diagnostic_text.setUndoRedoEnabled(False)
            self._diagnostic_text.setFont(font)
            self._diagnostic_text.setStyleSheet(
                'QTextBrowser {'
                'background-color: #000000;'
                'color: #f5f5f5;'
                'selection-background-color: #1d551d;'
                'selection-color: #ffffff;'
                'border: 1px solid #244024;'
                '}'
            )
            diagnostic_view_layout.addWidget(self._diagnostic_text)
            diagnostics_layout.addWidget(self._diagnostic_view_group, 1)

            button_row = QtWidgets.QHBoxLayout()
            root.addLayout(button_row)

            self._live_tune_button = QtWidgets.QPushButton('VBI Tune Live')
            self._live_tune_button.clicked.connect(self._open_live_tune_dialog)
            self._live_tune_button.setEnabled(self._live_tune_callback is not None)
            button_row.addWidget(self._live_tune_button)

            self._save_button = QtWidgets.QPushButton('Save VBI...')
            self._save_button.clicked.connect(self._save_vbi)
            self._save_button.setEnabled(self._save_callback is not None)
            button_row.addWidget(self._save_button)

            self._save_page_button = QtWidgets.QPushButton('Save Page T42...')
            self._save_page_button.clicked.connect(self._save_page_t42)
            self._save_page_button.setEnabled(self._save_page_callback is not None and self._diagnostic_mode() == 'page')
            button_row.addWidget(self._save_page_button)

            self._stabilize_button = QtWidgets.QPushButton('Stabilize VBI...')
            self._stabilize_button.clicked.connect(self._stabilize_vbi)
            self._stabilize_button.setEnabled(self._stabilize_callback is not None)
            button_row.addWidget(self._stabilize_button)

            button_row.addStretch(1)

            self._diagnostic_busy_label = QtWidgets.QLabel('Updating...')
            self._diagnostic_busy_label.setStyleSheet('color: #6ea86e;')
            self._diagnostic_busy_label.hide()
            button_row.addWidget(self._diagnostic_busy_label)

            self._diagnostic_progress = QtWidgets.QProgressBar()
            self._diagnostic_progress.setRange(0, 0)
            self._diagnostic_progress.setTextVisible(False)
            self._diagnostic_progress.setFixedWidth(96)
            self._diagnostic_progress.hide()
            button_row.addWidget(self._diagnostic_progress)

            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            button_row.addWidget(self._close_button)

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(120)
            self._timer.timeout.connect(self._sync_from_state)
            self._timer.start()

            self._diagnostic_timer = QtCore.QTimer(self)
            self._diagnostic_timer.setSingleShot(True)
            self._diagnostic_timer.timeout.connect(self._trigger_diagnostics_request)

            if self._diagnostics_callback is not None:
                self._diagnostic_worker_thread = QtCore.QThread(self)
                self._diagnostic_worker = _DiagnosticsWorker(self._diagnostics_callback)
                self._diagnostic_worker.moveToThread(self._diagnostic_worker_thread)
                self._diagnostic_worker.result_ready.connect(self._handle_diagnostic_result)
                self._diagnostic_worker.progress_ready.connect(self._handle_diagnostic_progress)
                self._diagnostic_request.connect(self._diagnostic_worker.process, QtCore.Qt.QueuedConnection)
                self._diagnostic_worker_thread.start()

            self._diagnostic_mode_changed()
            self._tuning_ranges_visibility_changed(self._tuning_ranges_group.isChecked())
            self._sync_from_state()
            self._record_selection_history_state(reset_redo=True)

            QtWidgets.QShortcut(QtGui.QKeySequence.Undo, self, activated=self._selection_undo)
            QtWidgets.QShortcut(QtGui.QKeySequence.Redo, self, activated=self._selection_redo)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+Shift+Z'), self, activated=self._selection_redo)

        def _format_time(self, frame_index):
            seconds = max(float(frame_index) / self._frame_rate, 0.0)
            minutes = int(seconds // 60)
            whole_seconds = int(seconds % 60)
            centiseconds = int(round((seconds - int(seconds)) * 100))
            return f'{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}'

        def _sync_from_state(self):
            viewer_process = self._viewer_process() if callable(self._viewer_process) else self._viewer_process

            self._updating = True
            current = self._state.current_frame()
            selection_start, selection_end = self._state.selection_range()
            self._frame_slider.setValue(current)
            self._frame_box.setValue(current)
            self._frame_time_label.setText(self._format_time(current))
            self._selection_slider.setValues(selection_start, selection_end)
            self._selection_start_box.setValue(selection_start)
            self._selection_end_box.setValue(selection_end)
            self._sync_selection_save_frames(selection_start, selection_end)
            self._speed_box.setValue(self._state.playback_speed())
            playing = self._state.is_playing()
            direction = self._state.playback_direction()
            self._play_button.setText('Pause' if playing and direction > 0 else 'Play')
            self._reverse_button.setText('Pause Rev' if playing and direction < 0 else 'Reverse')

            elapsed = self._format_time(current)
            remaining_frames = max((self._total_frames - 1) - current, 0)
            remaining = self._format_time(remaining_frames)
            status_text = f'{current + 1}/{self._total_frames} [{elapsed}<{remaining}]'
            if viewer_process is not None and not viewer_process.is_alive():
                status_text += ' | Viewer stopped'
            self._status_label.setText(status_text)
            self._updating = False
            self._refresh_tuning_ranges()
            self._schedule_diagnostics()

        def _sync_selection_save_frames(self, selection_start, selection_end):
            span = max(int(selection_end) - int(selection_start) + 1, 1)
            current = int(self._selection_save_frames_box.value())
            previous_span = max(int(getattr(self, '_selection_save_span', span)), 1)
            self._selection_save_frames_box.blockSignals(True)
            self._selection_save_frames_box.setRange(1, span)
            if current > span or current == previous_span:
                current = span
            self._selection_save_frames_box.setValue(max(1, min(int(current), span)))
            self._selection_save_frames_box.blockSignals(False)
            self._selection_save_span = span

        def _selection_snapshot(self):
            start, end = self._state.selection_range()
            return (
                int(start),
                int(end),
                int(max(1, min(self._selection_save_frames_box.value(), self._selection_save_span))),
            )

        def _update_selection_history_buttons(self):
            self._selection_undo_button.setEnabled(self._selection_history_index > 0)
            self._selection_redo_button.setEnabled(
                0 <= self._selection_history_index < (len(self._selection_history) - 1)
            )

        def _record_selection_history_state(self, reset_redo=False):
            if self._selection_history_restoring:
                return
            snapshot = self._selection_snapshot()
            if reset_redo:
                self._selection_history = [snapshot]
                self._selection_history_index = 0
                self._update_selection_history_buttons()
                return
            if self._selection_history_index >= 0 and self._selection_history[self._selection_history_index] == snapshot:
                self._update_selection_history_buttons()
                return
            if self._selection_history_index < len(self._selection_history) - 1:
                self._selection_history = self._selection_history[:self._selection_history_index + 1]
            self._selection_history.append(snapshot)
            if len(self._selection_history) > 200:
                overflow = len(self._selection_history) - 200
                self._selection_history = self._selection_history[overflow:]
                self._selection_history_index = max(self._selection_history_index - overflow, -1)
            self._selection_history_index = len(self._selection_history) - 1
            self._update_selection_history_buttons()

        def _restore_selection_snapshot(self, snapshot):
            try:
                start, end, save_frames = snapshot
            except Exception:
                return
            self._selection_history_restoring = True
            try:
                self._state.set_playing(False)
                self._state.set_selection_range(int(start), int(end))
                self._selection_save_span = max(int(end) - int(start) + 1, 1)
                self._selection_save_frames_box.blockSignals(True)
                self._selection_save_frames_box.setRange(1, self._selection_save_span)
                self._selection_save_frames_box.setValue(max(1, min(int(save_frames), self._selection_save_span)))
                self._selection_save_frames_box.blockSignals(False)
                self._sync_from_state()
            finally:
                self._selection_history_restoring = False
            self._update_selection_history_buttons()

        def _selection_undo(self):
            if self._selection_history_index <= 0:
                return
            self._selection_history_index -= 1
            self._restore_selection_snapshot(self._selection_history[self._selection_history_index])

        def _selection_redo(self):
            if self._selection_history_index < 0 or self._selection_history_index >= len(self._selection_history) - 1:
                return
            self._selection_history_index += 1
            self._restore_selection_snapshot(self._selection_history[self._selection_history_index])

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

        def _selection_slider_changed(self, start, end):
            if self._updating:
                return
            self._state.set_selection_range(start, end)
            self._sync_from_state()
            self._record_selection_history_state()

        def _selection_box_changed(self, _value):
            if self._updating:
                return
            self._state.set_selection_range(self._selection_start_box.value(), self._selection_end_box.value())
            self._sync_from_state()
            self._record_selection_history_state()

        def _selection_mark_start(self):
            self._state.set_selection_to_current_start()
            self._sync_from_state()
            self._record_selection_history_state()

        def _selection_mark_end(self):
            self._state.set_selection_to_current_end()
            self._sync_from_state()
            self._record_selection_history_state()

        def _reset_selection(self):
            self._state.set_playing(False)
            self._state.set_selection_range(0, self._total_frames - 1)
            self._selection_save_span = self._total_frames
            self._selection_save_frames_box.blockSignals(True)
            self._selection_save_frames_box.setRange(1, self._total_frames)
            self._selection_save_frames_box.setValue(self._total_frames)
            self._selection_save_frames_box.blockSignals(False)
            self._sync_from_state()
            self._record_selection_history_state()

        def _selection_save_frames_changed(self, _value):
            if self._updating or self._selection_history_restoring:
                return
            self._record_selection_history_state()

        def _refresh_tuning_ranges(self):
            if not hasattr(self, '_tuning_ranges_list'):
                return
            current_index = self._selected_tuning_range_index
            self._tuning_ranges_list.blockSignals(True)
            self._tuning_ranges_list.clear()
            for index, entry in enumerate(self._tuning_ranges):
                item = QtWidgets.QListWidgetItem(format_tuning_range_label(entry, index=index))
                item.setToolTip(
                    f"{int(entry['start_frame'])}..{int(entry['end_frame'])}\n"
                    f"lines: {','.join(str(line) for line in sorted(entry.get('line_selection') or ())) or 'default'}"
                )
                self._tuning_ranges_list.addItem(item)
            if current_index is not None and 0 <= int(current_index) < len(self._tuning_ranges):
                self._tuning_ranges_list.setCurrentRow(int(current_index))
            else:
                self._selected_tuning_range_index = None
                self._tuning_ranges_list.setCurrentRow(-1)
            self._tuning_ranges_list.blockSignals(False)
            has_selection = self._selected_tuning_range_index is not None
            self._selection_update_button.setEnabled(has_selection and self._capture_tuning_range_callback is not None)
            self._selection_remove_button.setEnabled(has_selection)

        def _tuning_ranges_visibility_changed(self, visible):
            if hasattr(self, '_tuning_ranges_container'):
                self._tuning_ranges_container.setVisible(bool(visible))
            self.updateGeometry()
            if bool(visible):
                def grow_only():
                    hint = self.sizeHint()
                    self.resize(
                        max(self.width(), hint.width()),
                        max(self.height(), hint.height()),
                    )
                QtCore.QTimer.singleShot(0, grow_only)
            else:
                def shrink_vertical_only():
                    hint = self.sizeHint()
                    self.resize(
                        self.width(),
                        max(self.minimumHeight(), hint.height()),
                    )
                QtCore.QTimer.singleShot(0, shrink_vertical_only)

        def _apply_tuning_ranges(self):
            self._tuning_ranges = list(normalise_tuning_ranges(self._tuning_ranges, total_frames=self._total_frames))
            self._refresh_tuning_ranges()
            if self._tuning_ranges_changed_callback is not None:
                self._tuning_ranges_changed_callback(tuple(self._tuning_ranges))

        def _capture_tuning_range_entry(self):
            if self._capture_tuning_range_callback is None:
                raise ValueError('VBI tuning is not available for this window.')
            start, end = self._state.selection_range()
            snapshot = dict(self._capture_tuning_range_callback() or {})
            snapshot['start_frame'] = int(start)
            snapshot['end_frame'] = int(end)
            snapshot.setdefault('label', '')
            return snapshot

        def _add_tuning_range(self):
            try:
                entry = self._capture_tuning_range_entry()
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.warning(self, 'VBI Repair', str(exc))
                return
            self._tuning_ranges.append(entry)
            self._selected_tuning_range_index = len(self._tuning_ranges) - 1
            self._apply_tuning_ranges()

        def _update_tuning_range(self):
            if self._selected_tuning_range_index is None:
                return
            try:
                entry = self._capture_tuning_range_entry()
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.warning(self, 'VBI Repair', str(exc))
                return
            self._tuning_ranges[int(self._selected_tuning_range_index)] = entry
            self._apply_tuning_ranges()

        def _remove_tuning_range(self):
            if self._selected_tuning_range_index is None:
                return
            del self._tuning_ranges[int(self._selected_tuning_range_index)]
            self._selected_tuning_range_index = None
            self._apply_tuning_ranges()

        def _tuning_range_selected(self, row):
            if row < 0 or row >= len(self._tuning_ranges):
                self._selected_tuning_range_index = None
            else:
                self._selected_tuning_range_index = int(row)
                entry = self._tuning_ranges[int(row)]
                self._state.set_playing(False)
                self._state.set_selection_range(int(entry['start_frame']), int(entry['end_frame']))
            self._sync_from_state()

        def _diagnostic_mode(self):
            return str(self._diagnostic_mode_box.currentData() or 'packets')

        def _diagnostic_update_mode(self):
            return str(self._diagnostic_update_mode_box.currentData() or 'auto')

        def _diagnostic_mode_changed(self):
            mode = self._diagnostic_mode()
            row_visible = mode == 'row'
            page_visible = mode == 'page'
            row0_range_visible = mode in ('row', 'page', 'row0range')
            self._row_label.setVisible(row_visible)
            self._row_box.setVisible(row_visible)
            self._page_label.setVisible(page_visible)
            self._page_box.setVisible(page_visible)
            self._page_auto_button.setVisible(page_visible)
            self._subpage_label.setVisible(page_visible)
            self._subpage_box.setVisible(page_visible)
            self._subpage_auto_button.setVisible(page_visible)
            self._row0_range_label.setVisible(row0_range_visible)
            self._row0_range_box.setVisible(row0_range_visible)
            if hasattr(self, '_save_page_button'):
                self._save_page_button.setEnabled(self._save_page_callback is not None and page_visible)
            self._schedule_diagnostics(force=not self._state.is_playing())

        def _page_auto_toggled(self, checked):
            if checked:
                self._apply_auto_page_suggestion()

        def _apply_auto_page_suggestion(self, suggestions=None):
            if not self._page_auto_button.isChecked():
                return
            items = tuple(str(value).strip().upper() for value in (suggestions or ()) if str(value).strip())
            if not items:
                return
            target = items[0]
            current = self._page_box.text().strip().upper()
            if current == target:
                return
            self._page_box.blockSignals(True)
            self._page_box.setText(target)
            self._page_box.blockSignals(False)
            self._schedule_diagnostics()

        def _subpage_auto_toggled(self, checked):
            if checked:
                self._apply_auto_subpage_suggestion()

        def _show_current_page_menu(self):
            entries = tuple(self._current_page_entries or ())
            if not entries:
                return
            menu = QtWidgets.QMenu(self)
            for page_text, subpage_text in entries:
                label = f'P{page_text}/{subpage_text}'
                action = menu.addAction(label)
                action.triggered.connect(
                    lambda checked=False, p=page_text, s=subpage_text: self._select_current_page_entry(p, s)
                )
            menu.exec_(QtGui.QCursor.pos())

        def _select_current_page_entry(self, page_text, subpage_text):
            self._page_auto_button.setChecked(False)
            self._subpage_auto_button.setChecked(False)
            if self._diagnostic_mode() != 'page':
                self._diagnostic_mode_box.setCurrentIndex(self._diagnostic_mode_box.findData('page'))
            self._page_box.blockSignals(True)
            self._subpage_box.blockSignals(True)
            self._page_box.setText(str(page_text).strip().upper())
            self._subpage_box.setText(str(subpage_text).strip().upper())
            self._page_box.blockSignals(False)
            self._subpage_box.blockSignals(False)
            self._schedule_diagnostics(force=not self._state.is_playing())

        def _apply_auto_subpage_suggestion(self, suggestions=None):
            if not self._subpage_auto_button.isChecked():
                return
            items = tuple(str(value).strip().upper() for value in (suggestions or ()) if str(value).strip())
            if not items:
                return
            target = items[0]
            current = self._subpage_box.text().strip().upper()
            if current == target:
                return
            self._subpage_box.blockSignals(True)
            self._subpage_box.setText(target)
            self._subpage_box.blockSignals(False)
            self._schedule_diagnostics()

        def _diagnostic_update_mode_changed(self):
            manual = self._diagnostic_update_mode() == 'manual'
            self._diagnostic_delay_box.setEnabled(not manual)
            self._refresh_button.setEnabled(manual)
            if manual:
                self._diagnostic_timer.stop()
            self._schedule_diagnostics(force=not manual)

        def _diagnostic_delay_ms(self):
            delay = int(self._diagnostic_delay_box.value())
            if self._state.is_playing():
                delay = max(delay, 300)
                if self._diagnostic_mode() == 'page':
                    delay = max(delay, 650)
                elif self._diagnostic_mode() == 'row0range':
                    delay = max(delay, 900)
            elif self._diagnostic_mode() == 'page':
                delay = max(delay, 120)
            elif self._diagnostic_mode() == 'row0range':
                delay = max(delay, 250)
            return delay

        def _schedule_diagnostics(self, *args, force=False):
            if self._diagnostics_callback is None:
                return
            if force:
                self._diagnostic_timer.stop()
                self._trigger_diagnostics_request()
                return
            if self._diagnostic_update_mode() == 'manual':
                return
            if self._diagnostic_timer.isActive():
                return
            self._diagnostic_timer.start(self._diagnostic_delay_ms())

        def _format_elapsed(self, seconds):
            seconds = max(float(seconds), 0.0)
            minutes = int(seconds // 60)
            whole_seconds = int(seconds % 60)
            return f'{minutes:02d}:{whole_seconds:02d}'

        def _set_diagnostic_busy(self, busy, current=0, total=0, detail=None):
            if busy and total > 0:
                percent = int(round((float(current) / float(total)) * 100.0))
                parts = [f'Updating... {percent}%']
                if detail:
                    parts.append(str(detail))
                elif self._diagnostic_busy_started_at is not None:
                    parts.append(self._format_elapsed(time.monotonic() - self._diagnostic_busy_started_at))
                self._diagnostic_busy_label.setText(' | '.join(parts))
                self._diagnostic_progress.setRange(0, max(int(total), 1))
                self._diagnostic_progress.setValue(max(0, min(int(current), int(total))))
            else:
                self._diagnostic_busy_started_at = None
                self._diagnostic_busy_label.setText('Updating...')
                self._diagnostic_progress.setRange(0, 0)
            self._diagnostic_busy_label.setVisible(bool(busy))
            self._diagnostic_progress.setVisible(bool(busy))

        def _next_diagnostic_request_id(self):
            self._diagnostic_request_counter += 1
            return self._diagnostic_request_counter

        def _dispatch_pending_diagnostic_request(self):
            if self._pending_diagnostic_request is None or self._diagnostic_worker is None:
                return
            request = self._pending_diagnostic_request
            self._pending_diagnostic_request = None
            self._active_diagnostic_request_id = request[0]
            self._diagnostic_worker_busy = True
            self._diagnostic_busy_started_at = time.monotonic()
            self._set_diagnostic_busy(True, 0, 0)
            self._diagnostic_request.emit(*request)

        def _trigger_diagnostics_request(self):
            if self._diagnostics_callback is None:
                return
            request = (
                self._next_diagnostic_request_id(),
                int(self._state.current_frame()),
                self._diagnostic_mode(),
                int(self._row_box.value()),
                self._page_box.text().strip() or '100',
                self._subpage_box.text().strip().upper(),
                not bool(self._noise_box.isChecked()),
                int(self._row0_range_box.value()),
            )
            if self._diagnostic_worker is None:
                payload_provider = getattr(self._diagnostics_callback, 'describe_payload', None)
                if callable(payload_provider):
                    payload = payload_provider(
                        request[1],
                        request[2],
                        request[3],
                        request[4],
                        request[5],
                        row0_range_frames=request[7],
                        hide_noisy=request[6],
                    )
                else:
                    payload = {
                        'text': self._diagnostics_callback(
                            request[1],
                            request[2],
                            request[3],
                            request[4],
                            request[5],
                            row0_range_frames=request[7],
                        ),
                        'summary': 'Current page/subpage: --',
                    }
                self._handle_diagnostic_result(request[0], payload)
                return
            self._pending_diagnostic_request = request
            if not self._diagnostic_worker_busy:
                self._dispatch_pending_diagnostic_request()

        def _handle_diagnostic_progress(self, request_id, payload):
            if int(request_id) != int(self._active_diagnostic_request_id or request_id):
                return
            if isinstance(payload, dict):
                current = int(payload.get('current', 0))
                total = int(payload.get('total', 0))
                detail = payload.get('detail')
            else:
                current = int(payload)
                total = 0
                detail = None
            self._set_diagnostic_busy(True, current, total, detail)

        def _handle_diagnostic_result(self, request_id, payload):
            if int(request_id) != int(self._active_diagnostic_request_id or request_id):
                return
            self._diagnostic_worker_busy = False
            self._active_diagnostic_request_id = None
            self._set_diagnostic_busy(False)
            if not isinstance(payload, dict):
                payload = {
                    'text': str(payload),
                    'summary': 'Current page/subpage: --',
                }
            summary = str(payload.get('summary') or 'Current page/subpage: --')
            text = str(payload.get('text', ''))
            if summary != self._last_diagnostics_summary:
                self._current_page_label.setText(summary)
                self._last_diagnostics_summary = summary
            current_page_entries = []
            for entry in payload.get('current_page_entries', ()):
                if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                    continue
                page_text = str(entry[0]).strip().upper()
                subpage_text = str(entry[1]).strip().upper()
                if not page_text or not subpage_text:
                    continue
                current_page_entries.append((page_text, subpage_text))
            self._current_page_entries = tuple(current_page_entries)
            page_suggestions = [
                str(value).strip().upper()
                for value in payload.get('page_suggestions', ())
                if str(value).strip()
            ]
            self._page_model.setStringList(page_suggestions)
            self._page_box.setPlaceholderText(page_suggestions[0] if page_suggestions else '100')
            page_auto_suggestions = [
                str(value).strip().upper()
                for value in payload.get('page_auto_suggestions', ())
                if str(value).strip()
            ]
            self._apply_auto_page_suggestion(page_auto_suggestions or page_suggestions)
            subpage_suggestions = [
                str(value).strip().upper()
                for value in payload.get('subpage_suggestions', ())
                if str(value).strip()
            ]
            self._subpage_model.setStringList(subpage_suggestions)
            self._subpage_box.setPlaceholderText(subpage_suggestions[0] if subpage_suggestions else 'best')
            subpage_auto_suggestions = [
                str(value).strip().upper()
                for value in payload.get('subpage_auto_suggestions', ())
                if str(value).strip()
            ]
            self._apply_auto_subpage_suggestion(subpage_auto_suggestions or subpage_suggestions)
            if text != self._last_diagnostics_text:
                self._diagnostic_text.setHtml(_ansi_text_to_html(text, font_family=_diagnostic_font_family()))
                self._last_diagnostics_text = text
            if self._close_when_idle:
                self._close_when_idle = False
                self._shutdown_diagnostic_worker()
                QtCore.QTimer.singleShot(0, self.close)
            elif self._pending_diagnostic_request is not None:
                self._dispatch_pending_diagnostic_request()

        def _shutdown_diagnostic_worker(self):
            self._pending_diagnostic_request = None
            self._active_diagnostic_request_id = None
            self._diagnostic_worker_busy = False
            worker = self._diagnostic_worker
            thread = self._diagnostic_worker_thread
            self._diagnostic_worker = None
            self._diagnostic_worker_thread = None
            if worker is not None:
                try:
                    self._diagnostic_request.disconnect(worker.process)
                except Exception:
                    pass
                try:
                    worker.result_ready.disconnect()
                except Exception:
                    pass
                try:
                    worker.progress_ready.disconnect()
                except Exception:
                    pass
                worker.deleteLater()
            if thread is not None:
                thread.requestInterruption()
                thread.quit()
                if not thread.wait(10000):
                    thread.requestInterruption()
                    thread.quit()
                    thread.wait(30000)
                thread.deleteLater()

        def _open_live_tune_dialog(self):
            if self._live_tune_callback is None:
                return
            try:
                self._live_tune_callback()
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Repair', str(exc))

        def _open_monitor_dialog(self):
            if self._monitor_callback is None:
                return
            try:
                self._monitor_callback()
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Repair', str(exc))

        def _save_vbi(self):
            if self._save_callback is None:
                return
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Repaired VBI',
                os.path.join(os.getcwd(), 'repaired.vbi'),
                'VBI files (*.vbi);;All files (*)',
            )
            if not filename:
                return
            start_frame, end_frame = self._state.selection_range()
            selection_frame_count = max(int(end_frame) - int(start_frame) + 1, 1)
            frame_count = max(1, min(int(self._selection_save_frames_box.value()), selection_frame_count))
            try:
                callback = self._save_callback
                try:
                    signature = inspect.signature(callback)
                except (TypeError, ValueError):
                    signature = None

                if signature is not None:
                    params = list(signature.parameters.values())
                    has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params)
                    positional_params = [
                        param for param in params
                        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    ]
                    if has_varargs or len(positional_params) >= 3:
                        callback(filename, int(start_frame), int(frame_count))
                    elif len(positional_params) >= 1:
                        callback(filename)
                    else:
                        callback()
                else:
                    try:
                        callback(filename, int(start_frame), int(frame_count))
                    except TypeError:
                        callback(filename)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Repair', str(exc))
                return
            QtWidgets.QMessageBox.information(
                self,
                'VBI Repair',
                (
                    f'Saved repaired VBI to:\n{filename}\n\n'
                    f'Frames: {int(start_frame)}..{int(start_frame + frame_count - 1)}'
                ),
            )

        def _save_page_t42(self):
            if self._save_page_callback is None:
                return
            page_text = self._page_box.text().strip().upper() or '100'
            subpage_text = self._subpage_box.text().strip().upper()
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Current Page as T42',
                os.path.join(os.getcwd(), f'P{page_text}-{subpage_text or "auto"}.t42'),
                'T42 files (*.t42);;All files (*)',
            )
            if not filename:
                return
            try:
                result = self._save_page_callback(
                    int(self._state.current_frame()),
                    page_text,
                    subpage_text,
                    bool(self._noise_box.isChecked()),
                    filename,
                )
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Repair', str(exc))
                return
            saved_subpage = ''
            if isinstance(result, dict):
                if result.get('subpage_hex'):
                    saved_subpage = f" / {str(result.get('subpage_hex')).strip().upper()}"
                elif 'subpage' in result:
                    saved_subpage = f" / {int(result.get('subpage', 0)):04X}"
            QtWidgets.QMessageBox.information(
                self,
                'VBI Repair',
                f'Saved page P{page_text}{saved_subpage} to:\n{filename}',
            )

        def _deconvolve_t42(self):
            if self._deconvolve_page_callback is None:
                return
            if not hasattr(self, '_deconvolve_thread'):
                self._deconvolve_thread = None
                self._deconvolve_worker = None
                self._deconvolve_progress_dialog = None
                self._deconvolve_error_message = None
                self._deconvolve_result = None
                self._deconvolve_values = None
            start_frame, end_frame = self._state.selection_range()
            selection_frame_count = max(int(end_frame) - int(start_frame) + 1, 1)
            frame_count = max(1, min(int(self._selection_save_frames_box.value()), selection_frame_count))
            dialog = _RepairDeconvolveDialog(
                start_frame=int(start_frame),
                frame_count=int(frame_count),
                initial_page=self._page_box.text().strip().upper() or '100',
                initial_row=int(self._row_box.value()),
                parent=self,
            )
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return
            values = dialog.values()
            if getattr(self, '_deconvolve_thread', None) is not None:
                return
            self._deconvolve_values = dict(values)
            self._deconvolve_result = None
            self._deconvolve_error_message = None
            self._deconvolve_running = True
            self._diagnostic_timer.stop()
            self._pending_diagnostic_request = None

            progress_dialog = QtWidgets.QProgressDialog('Deconvolving...', '', 0, max(int(values['frame_count']), 1), self)
            progress_dialog.setWindowTitle('Deconvolve')
            progress_dialog.setWindowModality(QtCore.Qt.WindowModal)
            progress_dialog.setCancelButton(None)
            progress_dialog.setMinimumDuration(0)
            progress_dialog.setValue(0)
            progress_dialog.show()
            self._deconvolve_progress_dialog = progress_dialog

            worker_kwargs = {
                'start_frame': int(values['start_frame']),
                'frame_count': int(values['frame_count']),
                'output_path': str(values['output_path']),
                'mode': str(values['mode']),
                'page_text': str(values.get('page_text', '100')),
                'row': int(values.get('row', 0)),
                'include_noise': bool(self._noise_box.isChecked()),
            }
            thread = QtCore.QThread(self)
            worker = _DeconvolveWorker(self._deconvolve_page_callback, worker_kwargs)
            worker.moveToThread(thread)
            thread.started.connect(worker.process)
            worker.progress_ready.connect(self._handle_deconvolve_progress)
            worker.result_ready.connect(self._handle_deconvolve_result)
            worker.error_ready.connect(self._handle_deconvolve_error)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(self._deconvolve_thread_finished)
            thread.finished.connect(thread.deleteLater)
            self._deconvolve_thread = thread
            self._deconvolve_worker = worker
            thread.start()

        def _handle_deconvolve_progress(self, current, total):
            dialog = getattr(self, '_deconvolve_progress_dialog', None)
            if dialog is None:
                return
            total = max(int(total), 1)
            current = max(0, min(int(current), total))
            dialog.setMaximum(total)
            dialog.setValue(current)
            dialog.setLabelText(f'Deconvolving... {current}/{total}')

        def _handle_deconvolve_result(self, result):
            self._deconvolve_result = result

        def _handle_deconvolve_error(self, message):
            self._deconvolve_error_message = str(message or 'Unknown deconvolve error.')

        def _deconvolve_thread_finished(self):
            dialog = getattr(self, '_deconvolve_progress_dialog', None)
            self._deconvolve_progress_dialog = None
            self._deconvolve_thread = None
            self._deconvolve_worker = None
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()

            if self._deconvolve_error_message:
                self._deconvolve_running = False
                QtWidgets.QMessageBox.critical(self, 'VBI Repair', self._deconvolve_error_message)
                self._deconvolve_error_message = None
                self._deconvolve_result = None
                self._deconvolve_values = None
                self._schedule_diagnostics(force=not self._state.is_playing())
                return

            values = dict(self._deconvolve_values or {})
            result = self._deconvolve_result
            details = []
            if isinstance(result, dict):
                mode = str(result.get('mode') or values.get('mode') or 'all')
                if mode == 'page':
                    page_hex = str(result.get('page_hex') or values.get('page_text') or '').strip().upper()
                    if page_hex:
                        details.append(f'Page: P{page_hex}')
                elif mode == 'row':
                    details.append(f'Row: {int(result.get("row", values.get("row", 0))):02d}')
                else:
                    details.append('Mode: All Pages')
                details.append(
                    f'Frames: {int(result.get("start_frame", values.get("start_frame", 0)))}'
                    f'..{int(result.get("end_frame", values.get("start_frame", 0) + values.get("frame_count", 1) - 1))}'
                )
                details.append(f'Packets: {int(result.get("packet_count", 0))}')
            QtWidgets.QMessageBox.information(
                self,
                'VBI Repair',
                (
                    f'Deconvolved output saved to:\n{values.get("output_path", "")}'
                    + (f'\n\n' + '\n'.join(details) if details else '')
                ),
            )
            self._deconvolve_running = False
            self._deconvolve_result = None
            self._deconvolve_values = None
            self._schedule_diagnostics(force=not self._state.is_playing())
            if self._close_when_idle and not self._diagnostic_worker_busy:
                self._close_when_idle = False
                self._shutdown_diagnostic_worker()
                QtCore.QTimer.singleShot(0, self.close)

        def _stabilize_vbi(self):
            if self._stabilize_callback is None or self._stabilize_preview_callback is None:
                return
            dialog = VBIWallLockDialog(
                self._stabilize_callback,
                self._stabilize_preview_callback,
                default_output_path=self._stabilize_default_path,
                line_count=self._stabilize_line_count,
                total_frames=self._total_frames,
                current_frame_provider=self._state.current_frame,
                parent=self,
            )
            _run_dialog_window(dialog)

        def closeEvent(self, event):  # pragma: no cover - GUI path
            self._timer.stop()
            self._diagnostic_timer.stop()
            self._pending_diagnostic_request = None
            if self._diagnostic_worker_busy or getattr(self, '_deconvolve_thread', None) is not None:
                self._close_when_idle = True
                if self._diagnostic_worker_thread is not None:
                    self._diagnostic_worker_thread.requestInterruption()
                if getattr(self, '_deconvolve_thread', None) is not None:
                    self.hide()
                self.hide()
                event.ignore()
                return
            self._shutdown_diagnostic_worker()
            super().closeEvent(event)


def run_repair_window(
    state,
    total_frames,
    frame_rate=DEFAULT_FRAME_RATE,
    save_callback=None,
    stabilize_callback=None,
    stabilize_default_path='',
    stabilize_line_count=32,
    stabilize_analysis_callback=None,
    stabilize_preview_callback=None,
    clear_stabilize_preview_callback=None,
    save_page_callback=None,
    deconvolve_page_callback=None,
    live_tune_callback=None,
    monitor_callback=None,
    capture_tuning_range_callback=None,
    tuning_ranges_changed_callback=None,
    initial_tuning_ranges=(),
    viewer_process=None,
    diagnostics_callback=None,
):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    window = VBIRepairWindow(
        state=state,
        total_frames=total_frames,
        frame_rate=frame_rate,
        save_callback=save_callback,
        stabilize_callback=stabilize_callback,
        stabilize_default_path=stabilize_default_path,
        stabilize_line_count=stabilize_line_count,
        stabilize_analysis_callback=stabilize_analysis_callback,
        stabilize_preview_callback=stabilize_preview_callback,
        clear_stabilize_preview_callback=clear_stabilize_preview_callback,
        save_page_callback=save_page_callback,
        deconvolve_page_callback=deconvolve_page_callback,
        live_tune_callback=live_tune_callback,
        monitor_callback=monitor_callback,
        capture_tuning_range_callback=capture_tuning_range_callback,
        tuning_ranges_changed_callback=tuning_ranges_changed_callback,
        initial_tuning_ranges=initial_tuning_ranges,
        viewer_process=viewer_process,
        diagnostics_callback=diagnostics_callback,
    )
    _run_dialog_window(window)


def open_monitor_window(
    state,
    total_frames,
    frame_rate=DEFAULT_FRAME_RATE,
    diagnostics_callback=None,
    viewer_process=None,
    title='Teletext Monitor',
):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    window = TeletextMonitorWindow(
        state=state,
        total_frames=total_frames,
        frame_rate=frame_rate,
        diagnostics_callback=diagnostics_callback,
        viewer_process=viewer_process,
        title=title,
    )
    window.show()
    window.raise_()
    window.activateWindow()
    return window
