import html
import os
import re
import time

from teletext.gui.vbicrop import (
    DEFAULT_FRAME_RATE,
    MAX_PLAYBACK_SPEED,
    MIN_PLAYBACK_SPEED,
    IMPORT_ERROR,
    _ensure_app,
    _run_dialog_window,
)


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
        progress_ready = QtCore.pyqtSignal(int, int, int)

        def __init__(self, diagnostics_callback):
            super().__init__()
            self._diagnostics_callback = diagnostics_callback

        @QtCore.pyqtSlot(int, int, str, int, str, str, bool)
        def process(self, request_id, frame_index, view_mode, row, page, subpage, hide_noisy):
            try:
                payload_provider = getattr(self._diagnostics_callback, 'describe_payload', None)
                if callable(payload_provider):
                    def report_progress(current, total):
                        self.progress_ready.emit(int(request_id), int(current), int(total))

                    payload = payload_provider(
                        frame_index,
                        view_mode,
                        row,
                        page,
                        subpage,
                        hide_noisy=hide_noisy,
                        progress_callback=report_progress,
                    )
                else:
                    payload = {
                        'text': self._diagnostics_callback(frame_index, view_mode, row, page, subpage),
                        'summary': 'Current page/subpage: --',
                    }
            except Exception as exc:  # pragma: no cover - GUI path
                payload = {
                    'text': f'Diagnostics failed:\n{exc}',
                    'summary': 'Current page/subpage: --',
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
    class VBIStabilizeDialog(QtWidgets.QDialog):
        def __init__(
            self,
            stabilize_callback,
            default_output_path='',
            *,
            line_count=32,
            current_frame_provider=None,
            analysis_callback=None,
            preview_callback=None,
            clear_preview_callback=None,
            parent=None,
        ):
            super().__init__(parent)
            self._stabilize_callback = stabilize_callback
            self._line_count = max(int(line_count), 1)
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
                'Analyze the original VBI frame and choose a reference wall for the whole KGI.\n'
                'Every selected line is shifted so its right edge aligns to that wall.\n'
                'This builds one stable KGI block without manual per-line moves.'
            )

            form = QtWidgets.QFormLayout()
            root.addLayout(form)

            self._mode_box = QtWidgets.QComboBox()
            self._mode_box.addItem('Full File', 'full')
            self._mode_box.addItem('Preview', 'quick')
            self._mode_box.currentIndexChanged.connect(self._mode_changed)
            form.addRow('Mode', self._mode_box)

            self._reference_mode_box = QtWidgets.QComboBox()
            self._reference_mode_box.addItem('Best Lines Median', 'median')
            self._reference_mode_box.addItem('Reference Line', 'line')
            self._reference_mode_box.currentIndexChanged.connect(self._mode_changed)
            form.addRow('Reference Mode', self._reference_mode_box)

            self._reference_line_box = QtWidgets.QSpinBox()
            self._reference_line_box.setRange(1, self._line_count)
            self._reference_line_box.setValue(1)
            form.addRow('Reference Line', self._reference_line_box)

            self._tolerance_box = QtWidgets.QSpinBox()
            self._tolerance_box.setRange(0, 32)
            self._tolerance_box.setValue(3)
            self._tolerance_box.setSuffix(' samples')
            form.addRow('Tolerance', self._tolerance_box)

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
            root.addWidget(self._status_label)

            self._progress_bar = QtWidgets.QProgressBar()
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)
            root.addWidget(self._progress_bar)

            self._analysis_box = QtWidgets.QPlainTextEdit()
            self._analysis_box.setReadOnly(True)
            self._analysis_box.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            diagnostic_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self._analysis_box.setFont(diagnostic_font)
            self._analysis_box.setPlaceholderText('Analysis results will appear here.')
            root.addWidget(self._analysis_box, 1)

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
                (self._reference_mode_box, 'currentIndexChanged'),
                (self._reference_line_box, 'valueChanged'),
                (self._tolerance_box, 'valueChanged'),
                (self._quick_frames_box, 'valueChanged'),
            ):
                getattr(widget, signal_name).connect(self._schedule_analysis_update)
                getattr(widget, signal_name).connect(self._schedule_preview_update)

            self._mode_changed()
            self._diagnostics_toggled(self._show_diagnostics_box.isChecked())
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
            self._reference_mode_box.setEnabled(not running)
            reference_mode = str(self._reference_mode_box.currentData() or 'median')
            self._reference_line_box.setEnabled((not running) and reference_mode == 'line')
            self._tolerance_box.setEnabled(not running)
            self._quick_frames_box.setEnabled(not running and self._mode_box.currentData() == 'quick')
            self._preview_box.setEnabled(not running and self._preview_callback is not None)
            self._show_diagnostics_box.setEnabled(not running)
            self._reset_button.setEnabled(not running)
            self._close_button.setEnabled(not running)

        def _mode_changed(self):
            quick = self._mode_box.currentData() == 'quick'
            self._quick_container.setVisible(bool(quick))
            self._quick_frames_box.setEnabled(bool(quick))
            reference_mode = str(self._reference_mode_box.currentData() or 'median')
            self._reference_line_box.setEnabled(reference_mode == 'line' and not self._running)

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
                'lock_mode': 'reference',
                'target_center': 0,
                'target_right_edge': 0,
                'reference_mode': str(self._reference_mode_box.currentData() or 'median'),
                'reference_line': int(self._reference_line_box.value()),
                'tolerance': int(self._tolerance_box.value()),
                'quick_preview': bool(quick_mode),
                'preview_frames': int(self._quick_frames_box.value()),
                'start_frame': start_frame,
            }

        def _format_analysis_text(self, analysis):
            if not analysis:
                return 'No analysis yet.'
            reference_mode = str(analysis.get('reference_mode', 'line') or 'line')
            reference_line = int(analysis.get('reference_line', 1))
            reference_lines = [int(line) for line in analysis.get('reference_lines', [])]
            tolerance = float(analysis.get('tolerance', 0.0))
            reference_left = float(analysis.get('reference_left', 0.0))
            reference_right = float(analysis.get('reference_right', 0.0))
            reference_width = float(analysis.get('reference_width', 0.0))
            mode_label = 'Best Lines Median' if reference_mode == 'median' else 'Reference Line'
            lines = [
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
            self._reference_mode_box.setCurrentIndex(self._reference_mode_box.findData('median'))
            self._reference_line_box.setValue(1)
            self._tolerance_box.setValue(3)
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
                'lock_mode': 'reference',
                'target_center': 0,
                'target_right_edge': 0,
                'reference_mode': str(self._reference_mode_box.currentData() or 'median'),
                'reference_line': int(self._reference_line_box.value()),
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
    class VBIRepairWindow(QtWidgets.QDialog):
        _diagnostic_request = QtCore.pyqtSignal(int, int, str, int, str, str, bool)

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
            live_tune_callback=None,
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
            self._live_tune_callback = live_tune_callback
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

            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.setWindowTitle('VBI Repair')
            self.resize(900, 640)
            self.setMinimumSize(760, 520)

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

            diagnostics_group = QtWidgets.QGroupBox('Diagnostics')
            diagnostics_layout = QtWidgets.QVBoxLayout(diagnostics_group)
            root.addWidget(diagnostics_group, 1)

            mode_layout = QtWidgets.QHBoxLayout()
            diagnostics_layout.addLayout(mode_layout)

            mode_layout.addWidget(QtWidgets.QLabel('View'))
            self._diagnostic_mode_box = QtWidgets.QComboBox()
            self._diagnostic_mode_box.addItem('Packets', 'packets')
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
                'Page uses a rolling frame buffer to assemble a teletext page.'
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
            self._sync_from_state()

        def _format_time(self, frame_index):
            seconds = max(float(frame_index) / self._frame_rate, 0.0)
            minutes = int(seconds // 60)
            whole_seconds = int(seconds % 60)
            centiseconds = int(round((seconds - int(seconds)) * 100))
            return f'{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}'

        def _sync_from_state(self):
            viewer_process = self._viewer_process() if callable(self._viewer_process) else self._viewer_process
            if viewer_process is not None and not viewer_process.is_alive():
                self.close()
                return

            self._updating = True
            current = self._state.current_frame()
            self._frame_slider.setValue(current)
            self._frame_box.setValue(current)
            self._frame_time_label.setText(self._format_time(current))
            self._speed_box.setValue(self._state.playback_speed())
            playing = self._state.is_playing()
            direction = self._state.playback_direction()
            self._play_button.setText('Pause' if playing and direction > 0 else 'Play')
            self._reverse_button.setText('Pause Rev' if playing and direction < 0 else 'Reverse')

            elapsed = self._format_time(current)
            remaining_frames = max((self._total_frames - 1) - current, 0)
            remaining = self._format_time(remaining_frames)
            self._status_label.setText(f'{current + 1}/{self._total_frames} [{elapsed}<{remaining}]')
            self._updating = False
            self._schedule_diagnostics()

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

        def _diagnostic_mode(self):
            return str(self._diagnostic_mode_box.currentData() or 'packets')

        def _diagnostic_update_mode(self):
            return str(self._diagnostic_update_mode_box.currentData() or 'auto')

        def _diagnostic_mode_changed(self):
            mode = self._diagnostic_mode()
            row_visible = mode == 'row'
            page_visible = mode == 'page'
            self._row_label.setVisible(row_visible)
            self._row_box.setVisible(row_visible)
            self._page_label.setVisible(page_visible)
            self._page_box.setVisible(page_visible)
            self._page_auto_button.setVisible(page_visible)
            self._subpage_label.setVisible(page_visible)
            self._subpage_box.setVisible(page_visible)
            self._subpage_auto_button.setVisible(page_visible)
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
            elif self._diagnostic_mode() == 'page':
                delay = max(delay, 120)
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

        def _set_diagnostic_busy(self, busy, current=0, total=0):
            if busy and total > 0:
                percent = int(round((float(current) / float(total)) * 100.0))
                self._diagnostic_busy_label.setText(f'Updating... {percent}%')
                self._diagnostic_progress.setRange(0, max(int(total), 1))
                self._diagnostic_progress.setValue(max(0, min(int(current), int(total))))
            else:
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
                        ),
                        'summary': 'Current page/subpage: --',
                    }
                self._handle_diagnostic_result(request[0], payload)
                return
            self._pending_diagnostic_request = request
            if not self._diagnostic_worker_busy:
                self._dispatch_pending_diagnostic_request()

        def _handle_diagnostic_progress(self, request_id, current, total):
            if int(request_id) != int(self._active_diagnostic_request_id or request_id):
                return
            self._set_diagnostic_busy(True, int(current), int(total))

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
            if self._pending_diagnostic_request is not None:
                self._dispatch_pending_diagnostic_request()

        def _open_live_tune_dialog(self):
            if self._live_tune_callback is None:
                return
            try:
                self._live_tune_callback()
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
            try:
                self._save_callback(filename)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'VBI Repair', str(exc))
                return
            QtWidgets.QMessageBox.information(
                self,
                'VBI Repair',
                f'Saved repaired VBI to:\n{filename}',
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

        def _stabilize_vbi(self):
            if self._stabilize_callback is None:
                return
            dialog = VBIStabilizeDialog(
                self._stabilize_callback,
                default_output_path=self._stabilize_default_path,
                line_count=self._stabilize_line_count,
                current_frame_provider=self._state.current_frame,
                analysis_callback=getattr(self, '_stabilize_analysis_callback', None),
                preview_callback=getattr(self, '_stabilize_preview_callback', None),
                clear_preview_callback=getattr(self, '_clear_stabilize_preview_callback', None),
                parent=self,
            )
            _run_dialog_window(dialog)

        def closeEvent(self, event):  # pragma: no cover - GUI path
            self._timer.stop()
            self._diagnostic_timer.stop()
            if self._diagnostic_worker_thread is not None:
                self._diagnostic_worker_thread.quit()
                self._diagnostic_worker_thread.wait(2000)
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
    live_tune_callback=None,
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
        live_tune_callback=live_tune_callback,
        viewer_process=viewer_process,
        diagnostics_callback=diagnostics_callback,
    )
    _run_dialog_window(window)
