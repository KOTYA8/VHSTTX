import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

from teletext import pipeline
from teletext.file import FileChunker
from teletext.packet import Packet
from teletext.subpage import Subpage

try:
    from PyQt5 import QtCore, QtGui, QtWidgets, QtQuickWidgets
except ImportError as exc:
    QtCore = None
    QtGui = None
    QtWidgets = None
    QtQuickWidgets = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None

if IMPORT_ERROR is None:
    from teletext.gui.decoder import Decoder


_APP = None


def _ensure_app():
    global _APP
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1] or ['teletext'])
    _APP = app
    return app


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


def _format_page_number(page_number):
    page_number = int(page_number)
    return f'{page_number >> 8}{page_number & 0xFF:02X}'


def _build_page_header(page_number, subpage):
    header = np.full((40,), fill_value=0x20, dtype=np.uint8)
    magazine = int(page_number) >> 8
    page = int(page_number) & 0xFF
    header[3:7] = np.frombuffer(f'P{magazine}{page:02X}'.encode('ascii'), dtype=np.uint8)
    header[8:] = subpage.header.displayable[:]
    return header


def _squash_page(packet_lists, settings, page_number=None):
    squash_mode = settings['squash_mode']
    if page_number is not None:
        squash_mode = settings.get('page_mode_overrides', {}).get(int(page_number), squash_mode)
    return tuple(
        pipeline.subpage_squash(
            packet_lists,
            threshold=settings['threshold'],
            min_duplicates=settings['min_duplicates'],
            ignore_empty=settings['ignore_empty'],
            best_of_n=settings['best_of_n'],
            use_confidence=settings['use_confidence'],
            squash_mode=squash_mode,
            v1_iterations=settings['v1_iterations'],
            squash_profile=settings['squash_profile'],
        )
    )


def _write_subpages(handle, subpages):
    for subpage in subpages:
        for packet in subpage.packets:
            handle.write(packet.to_bytes())


def _page_matches_search(page_number, label, search_text):
    tokens = [token for token in re.split(r'[\s,;]+', str(search_text or '').upper()) if token]
    if not tokens:
        return True
    page_code = _format_page_number(page_number).upper()
    haystacks = (page_code, f'P{page_code}', str(label).upper())
    return all(any(token in haystack for haystack in haystacks) for token in tokens)


if IMPORT_ERROR is None:
    class SquashTuneLoader(QtCore.QThread):
        loaded = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)
        progress = QtCore.pyqtSignal(str, int, int)

        def __init__(self, filename, parent=None):
            super().__init__(parent)
            self._filename = filename

        def run(self):  # pragma: no cover - GUI thread path
            try:
                raw_packets = []
                with open(self._filename, 'rb') as handle:
                    chunks = FileChunker(handle, 42)
                    total = len(chunks) if hasattr(chunks, '__len__') else 0
                    for index, (_, data) in enumerate(chunks, start=1):
                        raw_packets.append(bytes(data))
                        if total and (index == 1 or index == total or index % 2048 == 0):
                            self.progress.emit('Reading packets', index, total)

                packet_lists = tuple(
                    tuple(packet_list)
                    for packet_list in pipeline.paginate(
                        Packet(raw, number)
                        for number, raw in enumerate(raw_packets)
                    )
                    if len(packet_list) > 1
                )

                page_packet_lists = defaultdict(list)
                page_occurrences = defaultdict(list)
                page_order = []
                occurrence_counts = defaultdict(Counter)
                total_lists = len(packet_lists)
                for index, packet_list in enumerate(packet_lists, start=1):
                    subpage = Subpage.from_packets(packet_list, ignore_empty=False)
                    page_number = (int(subpage.mrag.magazine) << 8) | int(subpage.header.page)
                    if page_number not in page_packet_lists:
                        page_order.append(page_number)
                    page_packet_lists[page_number].append(packet_list)
                    subpage_number = int(subpage.header.subpage)
                    occurrence_counts[page_number][subpage_number] += 1
                    page_occurrences[page_number].append({
                        'label': f'{subpage_number:04X} #{occurrence_counts[page_number][subpage_number]}',
                        'subpage': subpage,
                        'subpage_number': subpage_number,
                    })
                    if total_lists and (index == 1 or index == total_lists or index % 256 == 0):
                        self.progress.emit('Building pages', index, total_lists)
            except Exception as exc:
                self.failed.emit(str(exc))
                return

            self.loaded.emit({
                'filename': self._filename,
                'page_order': tuple(page_order),
                'page_packet_lists': {key: tuple(value) for key, value in page_packet_lists.items()},
                'page_occurrences': {key: tuple(value) for key, value in page_occurrences.items()},
            })


    class SquashSaveWorker(QtCore.QThread):
        progress = QtCore.pyqtSignal(int, int, str)
        completed = QtCore.pyqtSignal(str)
        failed = QtCore.pyqtSignal(str)

        def __init__(self, output_path, page_order, page_packet_lists, settings, parent=None):
            super().__init__(parent)
            self._output_path = output_path
            self._page_order = tuple(page_order)
            self._page_packet_lists = page_packet_lists
            self._settings = dict(settings)

        def run(self):  # pragma: no cover - GUI thread path
            try:
                with open(self._output_path, 'wb') as handle:
                    total = len(self._page_order)
                    for index, page_number in enumerate(self._page_order, start=1):
                        packet_lists = self._page_packet_lists.get(page_number, ())
                        _write_subpages(handle, _squash_page(packet_lists, self._settings, page_number=page_number))
                        self.progress.emit(index, total, _format_page_number(page_number))
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.completed.emit(self._output_path)


    class SquashStatsWorker(QtCore.QThread):
        progress = QtCore.pyqtSignal(int, int, str)
        completed = QtCore.pyqtSignal(int, int, int, object)
        failed = QtCore.pyqtSignal(int, str)

        def __init__(self, request_id, page_order, page_packet_lists, settings, parent=None):
            super().__init__(parent)
            self._request_id = int(request_id)
            self._page_order = tuple(page_order)
            self._page_packet_lists = page_packet_lists
            self._settings = dict(settings)

        def run(self):  # pragma: no cover - GUI thread path
            try:
                total_pages = 0
                total_subpages = 0
                page_summaries = []
                total = len(self._page_order)
                for index, page_number in enumerate(self._page_order, start=1):
                    packet_lists = self._page_packet_lists.get(page_number, ())
                    subpages = _squash_page(packet_lists, self._settings, page_number=page_number)
                    page_summaries.append((int(page_number), len(packet_lists), len(subpages)))
                    if subpages:
                        total_pages += 1
                        total_subpages += len(subpages)
                    self.progress.emit(index, total, _format_page_number(page_number))
            except Exception as exc:
                self.failed.emit(self._request_id, str(exc))
                return
            self.completed.emit(self._request_id, total_pages, total_subpages, tuple(page_summaries))


if IMPORT_ERROR is None:
    class TeletextPreviewFrame(QtWidgets.QGroupBox):
        def __init__(self, title, parent=None):
            super().__init__(title, parent)
            self._note_label = QtWidgets.QLabel('')
            self._note_label.setWordWrap(True)

            self._decoder_widget = QtQuickWidgets.QQuickWidget()
            self._decoder_widget.setResizeMode(QtQuickWidgets.QQuickWidget.SizeViewToRootObject)
            self._decoder_widget.setClearColor(QtGui.QColor('black'))
            self._decoder_widget.setFocusPolicy(QtCore.Qt.NoFocus)
            self._decoder = Decoder(self._decoder_widget, font_family='teletext2')
            self._decoder.zoom = 2
            self._decoder.flashenabled = False
            self._decoder.highlighttext = False
            self._decoder_widget.setFixedSize(self._decoder.size())

            self._decoder_area = QtWidgets.QWidget()
            self._decoder_area.setStyleSheet('background-color: black;')
            layout = QtWidgets.QGridLayout(self._decoder_area)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self._decoder_widget, 0, 0, QtCore.Qt.AlignCenter)
            self._decoder_area.setFixedSize(self._decoder_widget.size())

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(8, 8, 8, 8)
            root.setSpacing(6)
            root.addWidget(self._decoder_area, 0, QtCore.Qt.AlignCenter)
            root.addWidget(self._note_label)
            self.clear()

        def set_zoom(self, zoom):
            self._decoder.zoom = zoom
            self._decoder_widget.setFixedSize(self._decoder.size())
            self._decoder_area.setFixedSize(self._decoder_widget.size())

        def set_render_settings(self, single_height=False, single_width=False, no_flash=False):
            self._decoder.doubleheight = not bool(single_height)
            self._decoder.doublewidth = not bool(single_width)
            self._decoder.flashenabled = not bool(no_flash)

        def clear(self, note='No preview'):
            self._decoder[:] = np.full((25, 40), fill_value=0x20, dtype=np.uint8)
            self._note_label.setText(str(note))

        def set_subpage(self, page_number, subpage, note=''):
            if subpage is None:
                self.clear(note or 'No preview')
                return
            self._decoder.pagecodepage = subpage.codepage
            self._decoder[0] = _build_page_header(page_number, subpage)
            self._decoder[1:] = subpage.displayable[:]
            self._note_label.setText(note)


if IMPORT_ERROR is None:
    class SquashTuneWindow(QtWidgets.QMainWindow):
        def __init__(self, filename=None, parent=None):
            super().__init__(parent)
            self.setWindowFlags(_standard_window_flags())
            self.setWindowTitle('Squash Tool')
            self.resize(1600, 960)

            self._filename = None
            self._all_page_order = ()
            self._page_packet_lists = {}
            self._page_occurrences = {}
            self._page_enabled_occurrences = {}
            self._page_mode_overrides = {}
            self._current_squashed = ()
            self._loader = None
            self._save_worker = None
            self._stats_worker = None
            self._stats_request_id = 0
            self._stats_dirty = False
            self._page_output_summaries = ()
            self._loaded_profile_cache = {}

            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            root = QtWidgets.QHBoxLayout(central)
            root.setContentsMargins(8, 8, 8, 8)
            root.setSpacing(8)

            controls_panel = QtWidgets.QWidget()
            controls_panel.setMaximumWidth(400)
            controls_layout = QtWidgets.QVBoxLayout(controls_panel)
            controls_layout.setContentsMargins(0, 0, 0, 0)
            controls_layout.setSpacing(8)
            root.addWidget(controls_panel, 0)

            options_panel = QtWidgets.QWidget()
            options_panel.setMaximumWidth(400)
            options_layout_root = QtWidgets.QVBoxLayout(options_panel)
            options_layout_root.setContentsMargins(0, 0, 0, 0)
            options_layout_root.setSpacing(8)

            file_row = QtWidgets.QHBoxLayout()
            self._open_button = QtWidgets.QPushButton('Open .t42')
            self._open_button.clicked.connect(self._open_file_dialog)
            file_row.addWidget(self._open_button)
            self._settings_button = QtWidgets.QToolButton()
            self._settings_button.setText('Settings')
            self._settings_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self._settings_menu = QtWidgets.QMenu(self._settings_button)
            self._single_height_action = self._settings_menu.addAction('Single Height')
            self._single_height_action.setCheckable(True)
            self._single_height_action.toggled.connect(self._update_preview_settings)
            self._single_width_action = self._settings_menu.addAction('Single Width')
            self._single_width_action.setCheckable(True)
            self._single_width_action.toggled.connect(self._update_preview_settings)
            self._no_flash_action = self._settings_menu.addAction('No Flash')
            self._no_flash_action.setCheckable(True)
            self._no_flash_action.toggled.connect(self._update_preview_settings)
            self._settings_button.setMenu(self._settings_menu)
            file_row.addWidget(self._settings_button)
            file_row.addStretch(1)
            controls_layout.addLayout(file_row)

            save_row = QtWidgets.QHBoxLayout()
            self._save_page_button = QtWidgets.QPushButton('Save Current Page...')
            self._save_page_button.clicked.connect(self._save_current_page)
            self._save_page_button.setEnabled(False)
            save_row.addWidget(self._save_page_button)
            self._save_button = QtWidgets.QPushButton('Save Squashed...')
            self._save_button.clicked.connect(self._save_squashed_file)
            self._save_button.setEnabled(False)
            save_row.addWidget(self._save_button)
            controls_layout.addLayout(save_row)

            self._file_label = QtWidgets.QLabel('No file loaded.')
            self._file_label.setWordWrap(True)
            controls_layout.addWidget(self._file_label)

            filter_group = QtWidgets.QGroupBox('Pages')
            filter_layout = QtWidgets.QGridLayout(filter_group)
            filter_layout.addWidget(QtWidgets.QLabel('Search'), 0, 0)
            self._search_edit = QtWidgets.QLineEdit()
            self._search_edit.setPlaceholderText('100, 1AF, P150...')
            self._search_edit.textChanged.connect(self._rebuild_page_list)
            filter_layout.addWidget(self._search_edit, 0, 1)
            filter_layout.addWidget(QtWidgets.QLabel('Sort'), 1, 0)
            self._sort_combo = QtWidgets.QComboBox()
            self._sort_combo.addItem('Default', 'default')
            self._sort_combo.addItem('Ascending', 'ascending')
            self._sort_combo.addItem('Descending', 'descending')
            self._sort_combo.currentIndexChanged.connect(self._rebuild_page_list)
            filter_layout.addWidget(self._sort_combo, 1, 1)
            controls_layout.addWidget(filter_group)

            self._page_list = QtWidgets.QListWidget()
            self._page_list.currentItemChanged.connect(self._page_changed)
            controls_layout.addWidget(self._page_list, 1)

            capture_group = QtWidgets.QGroupBox('Captures For Squash')
            capture_layout = QtWidgets.QVBoxLayout(capture_group)
            capture_buttons = QtWidgets.QHBoxLayout()
            self._captures_all_on_button = QtWidgets.QPushButton('All On')
            self._captures_all_on_button.clicked.connect(lambda: self._set_all_capture_checks(True))
            capture_buttons.addWidget(self._captures_all_on_button)
            self._captures_all_off_button = QtWidgets.QPushButton('All Off')
            self._captures_all_off_button.clicked.connect(lambda: self._set_all_capture_checks(False))
            capture_buttons.addWidget(self._captures_all_off_button)
            capture_layout.addLayout(capture_buttons)
            self._capture_list = QtWidgets.QListWidget()
            self._capture_list.itemChanged.connect(self._capture_item_changed)
            self._capture_list.setMaximumHeight(180)
            capture_layout.addWidget(self._capture_list)
            controls_layout.addWidget(capture_group)

            selection_group = QtWidgets.QGroupBox('Preview Selection')
            selection_layout = QtWidgets.QGridLayout(selection_group)
            selection_layout.addWidget(QtWidgets.QLabel('Original'), 0, 0)
            self._source_combo = QtWidgets.QComboBox()
            self._source_combo.currentIndexChanged.connect(self._render_original_preview)
            selection_layout.addWidget(self._source_combo, 0, 1)
            selection_layout.addWidget(QtWidgets.QLabel('Squashed'), 1, 0)
            self._result_combo = QtWidgets.QComboBox()
            self._result_combo.currentIndexChanged.connect(self._render_squashed_preview)
            selection_layout.addWidget(self._result_combo, 1, 1)
            selection_layout.addWidget(QtWidgets.QLabel('Zoom'), 2, 0)
            self._zoom_spin = QtWidgets.QDoubleSpinBox()
            self._zoom_spin.setRange(1.0, 4.0)
            self._zoom_spin.setSingleStep(0.1)
            self._zoom_spin.setValue(2.0)
            self._zoom_spin.valueChanged.connect(self._zoom_changed)
            selection_layout.addWidget(self._zoom_spin, 2, 1)
            controls_layout.addWidget(selection_group)

            changed_group = QtWidgets.QGroupBox('Changed Pages')
            changed_layout = QtWidgets.QVBoxLayout(changed_group)
            self._changed_pages_list = QtWidgets.QListWidget()
            self._changed_pages_list.setMaximumHeight(120)
            self._changed_pages_list.itemActivated.connect(self._jump_to_changed_page)
            changed_layout.addWidget(self._changed_pages_list)
            controls_layout.addWidget(changed_group)

            options_group = QtWidgets.QGroupBox('Squash Options')
            options_layout = QtWidgets.QGridLayout(options_group)
            row = 0
            options_layout.addWidget(QtWidgets.QLabel('All Mode'), row, 0)
            self._mode_combo = QtWidgets.QComboBox()
            for label, value in (
                ('V3', 'v3'),
                ('V1', 'v1'),
                ('Auto', 'auto'),
                ('Custom', 'custom'),
                ('Profile', 'profile'),
            ):
                self._mode_combo.addItem(label, value)
            self._mode_combo.currentIndexChanged.connect(self._mode_changed)
            options_layout.addWidget(self._mode_combo, row, 1)
            row += 1

            options_layout.addWidget(QtWidgets.QLabel('Page Mode'), row, 0)
            self._page_mode_combo = QtWidgets.QComboBox()
            self._page_mode_combo.addItem('[All Mode]', '')
            for label, value in (
                ('V3', 'v3'),
                ('V1', 'v1'),
                ('Auto', 'auto'),
                ('Custom', 'custom'),
                ('Profile', 'profile'),
            ):
                self._page_mode_combo.addItem(label, value)
            self._page_mode_combo.currentIndexChanged.connect(self._page_mode_changed)
            options_layout.addWidget(self._page_mode_combo, row, 1)
            row += 1

            self._min_duplicates_spin = QtWidgets.QSpinBox()
            self._min_duplicates_spin.setRange(1, 99)
            self._min_duplicates_spin.setValue(3)
            self._min_duplicates_spin.valueChanged.connect(self._schedule_settings_refresh)
            options_layout.addWidget(QtWidgets.QLabel('Min Duplicates'), row, 0)
            options_layout.addWidget(self._min_duplicates_spin, row, 1)
            row += 1

            self._threshold_spin = QtWidgets.QSpinBox()
            self._threshold_spin.setRange(-1, 10000)
            self._threshold_spin.setValue(-1)
            self._threshold_spin.valueChanged.connect(self._schedule_settings_refresh)
            options_layout.addWidget(QtWidgets.QLabel('Threshold'), row, 0)
            options_layout.addWidget(self._threshold_spin, row, 1)
            row += 1

            self._v1_iterations_spin = QtWidgets.QSpinBox()
            self._v1_iterations_spin.setRange(0, 12)
            self._v1_iterations_spin.setValue(3)
            self._v1_iterations_spin.valueChanged.connect(self._schedule_settings_refresh)
            options_layout.addWidget(QtWidgets.QLabel('V1 Iterations'), row, 0)
            options_layout.addWidget(self._v1_iterations_spin, row, 1)
            row += 1

            self._best_of_n_spin = QtWidgets.QSpinBox()
            self._best_of_n_spin.setRange(0, 99)
            self._best_of_n_spin.setValue(0)
            self._best_of_n_spin.setSpecialValueText('Off')
            self._best_of_n_spin.valueChanged.connect(self._schedule_settings_refresh)
            options_layout.addWidget(QtWidgets.QLabel('Best of N'), row, 0)
            options_layout.addWidget(self._best_of_n_spin, row, 1)
            row += 1

            self._use_confidence_toggle = QtWidgets.QCheckBox('Use Confidence')
            self._use_confidence_toggle.toggled.connect(self._schedule_settings_refresh)
            options_layout.addWidget(self._use_confidence_toggle, row, 0, 1, 2)
            row += 1

            self._ignore_empty_toggle = QtWidgets.QCheckBox('Ignore Empty')
            self._ignore_empty_toggle.toggled.connect(self._schedule_settings_refresh)
            options_layout.addWidget(self._ignore_empty_toggle, row, 0, 1, 2)
            row += 1

            options_layout.addWidget(QtWidgets.QLabel('Profile Name'), row, 0)
            self._profile_name_combo = QtWidgets.QComboBox()
            self._profile_name_combo.addItem('[None]', '')
            for name in pipeline.builtin_squash_profile_names():
                self._profile_name_combo.addItem(name, name)
            self._profile_name_combo.currentIndexChanged.connect(self._schedule_settings_refresh)
            options_layout.addWidget(self._profile_name_combo, row, 1)
            row += 1

            options_layout.addWidget(QtWidgets.QLabel('Profile File'), row, 0)
            profile_path_row = QtWidgets.QHBoxLayout()
            self._profile_path_edit = QtWidgets.QLineEdit()
            self._profile_path_edit.textChanged.connect(self._schedule_settings_refresh)
            profile_path_row.addWidget(self._profile_path_edit, 1)
            browse_button = QtWidgets.QPushButton('...')
            browse_button.setMaximumWidth(32)
            browse_button.clicked.connect(self._browse_profile)
            profile_path_row.addWidget(browse_button)
            options_layout.addLayout(profile_path_row, row, 1)
            row += 1

            self._custom_controls = {}
            for label, key, minimum, maximum, value in (
                ('Match Threshold', 'match_threshold', 0.0, 2.0, 0.74),
                ('Header Weight', 'header_weight', 0.0, 3.0, 0.55),
                ('Body Weight', 'body_weight', 0.0, 3.0, 1.0),
                ('Footer Weight', 'footer_weight', 0.0, 3.0, 0.45),
                ('Subcode Bonus', 'subcode_match_bonus', 0.0, 1.0, 0.12),
                ('Subcode Penalty', 'subcode_mismatch_penalty', 0.0, 1.0, 0.04),
            ):
                spin = QtWidgets.QDoubleSpinBox()
                spin.setDecimals(3)
                spin.setRange(minimum, maximum)
                spin.setSingleStep(0.01)
                spin.setValue(value)
                spin.valueChanged.connect(self._schedule_settings_refresh)
                options_layout.addWidget(QtWidgets.QLabel(label), row, 0)
                options_layout.addWidget(spin, row, 1)
                self._custom_controls[key] = spin
                row += 1

            self._profile_iterations_spin = QtWidgets.QSpinBox()
            self._profile_iterations_spin.setRange(0, 12)
            self._profile_iterations_spin.setValue(3)
            self._profile_iterations_spin.valueChanged.connect(self._schedule_settings_refresh)
            options_layout.addWidget(QtWidgets.QLabel('Profile Iterations'), row, 0)
            options_layout.addWidget(self._profile_iterations_spin, row, 1)
            row += 1

            self._rebuild_button = QtWidgets.QPushButton('Rebuild Page')
            self._rebuild_button.clicked.connect(self._recompute_current_page)
            options_layout.addWidget(self._rebuild_button, row, 0, 1, 2)
            options_layout_root.addWidget(options_group)

            errors_group = QtWidgets.QGroupBox('Output')
            errors_layout = QtWidgets.QVBoxLayout(errors_group)
            self._summary_label = QtWidgets.QLabel('Output count: no file loaded.')
            self._summary_label.setWordWrap(True)
            errors_layout.addWidget(self._summary_label)
            output_mode_row = QtWidgets.QHBoxLayout()
            output_mode_row.addWidget(QtWidgets.QLabel('Display'))
            self._output_mode_combo = QtWidgets.QComboBox()
            self._output_mode_combo.addItem('All Pages', 'all')
            self._output_mode_combo.addItem('Unaccepted Pages', 'lost')
            self._output_mode_combo.setCurrentIndex(1)
            self._output_mode_combo.currentIndexChanged.connect(self._refresh_lost_pages_list)
            output_mode_row.addWidget(self._output_mode_combo, 1)
            errors_layout.addLayout(output_mode_row)
            errors_sort_row = QtWidgets.QHBoxLayout()
            errors_sort_row.addWidget(QtWidgets.QLabel('Sort'))
            self._errors_sort_combo = QtWidgets.QComboBox()
            self._errors_sort_combo.addItem('Default', 'default')
            self._errors_sort_combo.addItem('Ascending', 'ascending')
            self._errors_sort_combo.addItem('Descending', 'descending')
            self._errors_sort_combo.currentIndexChanged.connect(self._refresh_lost_pages_list)
            errors_sort_row.addWidget(self._errors_sort_combo, 1)
            errors_layout.addLayout(errors_sort_row)
            self._lost_pages_list = QtWidgets.QListWidget()
            self._lost_pages_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            errors_layout.addWidget(self._lost_pages_list, 1)
            options_layout_root.addWidget(errors_group, 1)

            self._status_label = QtWidgets.QLabel('Open a .t42 file to start.')
            self._status_label.setWordWrap(True)
            options_layout_root.addWidget(self._status_label)
            options_layout_root.addStretch(0)

            preview_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            self._original_preview = TeletextPreviewFrame('Original')
            self._squashed_preview = TeletextPreviewFrame('Squashed')
            preview_splitter.addWidget(self._original_preview)
            preview_splitter.addWidget(self._squashed_preview)
            preview_splitter.setStretchFactor(0, 1)
            preview_splitter.setStretchFactor(1, 1)
            root.addWidget(preview_splitter, 1)
            root.addWidget(options_panel, 0)

            self._progress_label = QtWidgets.QLabel('')
            self._progress_bar = QtWidgets.QProgressBar()
            self._progress_bar.setTextVisible(True)
            self._progress_bar.hide()
            self._progress_label.hide()
            self.statusBar().addPermanentWidget(self._progress_label)
            self.statusBar().addPermanentWidget(self._progress_bar, 1)

            self._recompute_timer = QtCore.QTimer(self)
            self._recompute_timer.setSingleShot(True)
            self._recompute_timer.setInterval(250)
            self._recompute_timer.timeout.connect(self._recompute_current_page)

            self._stats_timer = QtCore.QTimer(self)
            self._stats_timer.setSingleShot(True)
            self._stats_timer.setInterval(300)
            self._stats_timer.timeout.connect(self._start_stats_refresh)

            self._mode_changed()
            self._update_preview_settings()
            if filename:
                self.load_file(filename)

        def _set_progress(self, label='', current=0, total=0):
            if not total:
                self._progress_label.hide()
                self._progress_bar.hide()
                return
            self._progress_label.setText(str(label))
            self._progress_label.show()
            self._progress_bar.setRange(0, int(total))
            self._progress_bar.setValue(int(current))
            self._progress_bar.show()

        def _clear_progress(self):
            self._set_progress('', 0, 0)

        def _open_file_dialog(self):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Open T42 File',
                os.path.dirname(self._filename) if self._filename else os.getcwd(),
                'Teletext (*.t42);;All Files (*)',
            )
            if filename:
                self.load_file(filename)

        def load_file(self, filename):
            self._filename = os.path.abspath(filename)
            self._file_label.setText(self._filename)
            self._page_list.clear()
            self._capture_list.clear()
            self._source_combo.clear()
            self._result_combo.clear()
            self._changed_pages_list.clear()
            self._current_squashed = ()
            self._save_page_button.setEnabled(False)
            self._original_preview.clear('Loading...')
            self._squashed_preview.clear('Loading...')
            self._summary_label.setText('Output count: loading...')
            self._lost_pages_list.clear()
            self._status_label.setText('Loading .t42...')
            self._save_button.setEnabled(False)
            self._clear_progress()
            self._loader = SquashTuneLoader(self._filename, self)
            self._loader.progress.connect(self._loader_progress)
            self._loader.loaded.connect(self._loader_finished)
            self._loader.failed.connect(self._loader_failed)
            self._loader.start()

        def _loader_progress(self, label, current, total):
            self._set_progress(label, current, total)

        def _loader_finished(self, payload):
            self._clear_progress()
            self._all_page_order = tuple(payload['page_order'])
            self._page_packet_lists = dict(payload['page_packet_lists'])
            self._page_occurrences = dict(payload['page_occurrences'])
            self._page_enabled_occurrences = {
                int(page_number): [True] * len(self._page_occurrences.get(page_number, ()))
                for page_number in self._all_page_order
            }
            self._page_mode_overrides = {}
            self._save_button.setEnabled(bool(self._all_page_order))
            self._rebuild_page_list()
            self._status_label.setText(
                f'Loaded {len(self._all_page_order)} pages from {os.path.basename(self._filename)}.'
            )
            self._schedule_stats_refresh()

        def _loader_failed(self, message):
            self._clear_progress()
            self._status_label.setText(str(message))
            self._summary_label.setText('Output count: unavailable.')
            QtWidgets.QMessageBox.warning(self, 'Squash Tool', str(message))

        def _rebuild_page_list(self):
            current_page = self._current_page_number()
            search_text = self._search_edit.text().strip()
            sort_mode = str(self._sort_combo.currentData() or 'default')
            page_order = list(self._all_page_order)
            if sort_mode == 'ascending':
                page_order.sort()
            elif sort_mode == 'descending':
                page_order.sort(reverse=True)

            self._page_list.clear()
            for page_number in page_order:
                occurrences = self._page_occurrences.get(page_number, ())
                label = f'P{_format_page_number(page_number)} ({len(occurrences)} captures)'
                if not _page_matches_search(page_number, label, search_text):
                    continue
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, int(page_number))
                self._page_list.addItem(item)

            if self._page_list.count() == 0:
                self._capture_list.clear()
                self._source_combo.clear()
                self._result_combo.clear()
                self._current_squashed = ()
                self._original_preview.clear('No matching pages')
                self._squashed_preview.clear('No matching pages')
                self._save_page_button.setEnabled(False)
                return

            target_row = 0
            if current_page is not None:
                for row in range(self._page_list.count()):
                    if int(self._page_list.item(row).data(QtCore.Qt.UserRole)) == int(current_page):
                        target_row = row
                        break
            self._page_list.setCurrentRow(target_row)

        def _page_changed(self, current, _previous):
            page_number = None if current is None else current.data(QtCore.Qt.UserRole)
            self._sync_page_mode_combo(page_number)
            self._populate_capture_list(page_number)
            self._populate_source_combo(page_number)
            self._schedule_recompute()

        def _sync_page_mode_combo(self, page_number):
            previous_state = self._page_mode_combo.blockSignals(True)
            if page_number is None:
                self._page_mode_combo.setCurrentIndex(0)
                self._page_mode_combo.setEnabled(False)
            else:
                self._page_mode_combo.setEnabled(True)
                override = self._page_mode_overrides.get(int(page_number), '')
                index = self._page_mode_combo.findData(override)
                self._page_mode_combo.setCurrentIndex(0 if index < 0 else index)
            self._page_mode_combo.blockSignals(previous_state)

        def _page_mode_changed(self):
            page_number = self._current_page_number()
            if page_number is None:
                return
            mode = str(self._page_mode_combo.currentData() or '')
            if mode:
                self._page_mode_overrides[int(page_number)] = mode
            else:
                self._page_mode_overrides.pop(int(page_number), None)
            self._refresh_changed_pages_list()
            self._schedule_settings_refresh()

        def _refresh_changed_pages_list(self):
            self._changed_pages_list.clear()
            for page_number in self._all_page_order:
                override = self._page_mode_overrides.get(int(page_number))
                if override:
                    self._changed_pages_list.addItem(f'P{_format_page_number(page_number)} -> {override}')

        def _jump_to_changed_page(self, item):
            if item is None:
                return
            text = item.text().strip().upper()
            match = re.match(r'^P?([0-9][0-9A-F]{2})', text)
            if not match:
                return
            target = match.group(1)
            for row in range(self._page_list.count()):
                page_number = int(self._page_list.item(row).data(QtCore.Qt.UserRole))
                if _format_page_number(page_number).upper() == target:
                    self._page_list.setCurrentRow(row)
                    break

        def _populate_capture_list(self, page_number):
            previous_state = self._capture_list.blockSignals(True)
            self._capture_list.clear()
            if page_number is None:
                self._capture_list.blockSignals(previous_state)
                return
            occurrences = self._page_occurrences.get(int(page_number), ())
            enabled = list(self._page_enabled_occurrences.get(int(page_number), [True] * len(occurrences)))
            if len(enabled) < len(occurrences):
                enabled.extend([True] * (len(occurrences) - len(enabled)))
            for index, entry in enumerate(occurrences):
                item = QtWidgets.QListWidgetItem(entry['label'])
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setData(QtCore.Qt.UserRole, int(index))
                item.setCheckState(QtCore.Qt.Checked if enabled[index] else QtCore.Qt.Unchecked)
                self._capture_list.addItem(item)
            self._capture_list.blockSignals(previous_state)

        def _capture_item_changed(self, item):
            page_number = self._current_page_number()
            if page_number is None or item is None:
                return
            index = int(item.data(QtCore.Qt.UserRole))
            enabled = self._page_enabled_occurrences.setdefault(
                int(page_number),
                [True] * len(self._page_occurrences.get(int(page_number), ())),
            )
            if index >= len(enabled):
                enabled.extend([True] * (index - len(enabled) + 1))
            enabled[index] = bool(item.checkState() == QtCore.Qt.Checked)
            self._schedule_settings_refresh()

        def _set_all_capture_checks(self, enabled):
            page_number = self._current_page_number()
            if page_number is None:
                return
            state = QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked
            previous_state = self._capture_list.blockSignals(True)
            for row in range(self._capture_list.count()):
                self._capture_list.item(row).setCheckState(state)
            self._capture_list.blockSignals(previous_state)
            self._page_enabled_occurrences[int(page_number)] = [bool(enabled)] * self._capture_list.count()
            self._schedule_settings_refresh()

        def _label_for_mode(self, mode_value):
            index = self._mode_combo.findData(mode_value)
            if index >= 0:
                return self._mode_combo.itemText(index)
            return str(mode_value)

        def _populate_source_combo(self, page_number):
            self._source_combo.clear()
            if page_number is None:
                self._original_preview.clear('No page selected')
                return
            for entry in self._page_occurrences.get(int(page_number), ()):
                self._source_combo.addItem(entry['label'])
            if self._source_combo.count():
                self._source_combo.setCurrentIndex(0)
            self._render_original_preview()

        def _current_page_number(self):
            item = self._page_list.currentItem()
            if item is None:
                return None
            return int(item.data(QtCore.Qt.UserRole))

        def _render_original_preview(self):
            page_number = self._current_page_number()
            if page_number is None:
                self._original_preview.clear('No page selected')
                return
            index = self._source_combo.currentIndex()
            occurrences = self._page_occurrences.get(page_number, ())
            if index < 0 or index >= len(occurrences):
                self._original_preview.clear('No source occurrence')
                return
            entry = occurrences[index]
            self._original_preview.set_subpage(
                page_number,
                entry['subpage'],
                note=f'Raw occurrence {entry["label"]}',
            )

        def _render_squashed_preview(self):
            page_number = self._current_page_number()
            if page_number is None:
                self._squashed_preview.clear('No page selected')
                return
            index = self._result_combo.currentIndex()
            if index < 0 or index >= len(self._current_squashed):
                self._squashed_preview.clear('No squashed result')
                self._save_page_button.setEnabled(False)
                return
            subpage = self._current_squashed[index]
            note = f'Squashed result {index + 1}/{len(self._current_squashed)}'
            self._squashed_preview.set_subpage(page_number, subpage, note=note)
            self._save_page_button.setEnabled(True)

        def _zoom_changed(self, value):
            self._original_preview.set_zoom(float(value))
            self._squashed_preview.set_zoom(float(value))

        def _update_preview_settings(self):
            single_height = bool(self._single_height_action.isChecked())
            single_width = bool(self._single_width_action.isChecked())
            no_flash = bool(self._no_flash_action.isChecked())
            self._original_preview.set_render_settings(single_height, single_width, no_flash)
            self._squashed_preview.set_render_settings(single_height, single_width, no_flash)

        def _mode_changed(self):
            mode = self._mode_combo.currentData() or 'v3'
            custom_enabled = mode in {'custom', 'profile'}
            self._profile_name_combo.setEnabled(custom_enabled)
            self._profile_path_edit.setEnabled(custom_enabled)
            self._profile_iterations_spin.setEnabled(custom_enabled)
            for control in self._custom_controls.values():
                control.setEnabled(custom_enabled)
            self._schedule_settings_refresh()

        def _browse_profile(self):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Open Squash Profile',
                os.path.dirname(self._profile_path_edit.text().strip()) if self._profile_path_edit.text().strip() else os.getcwd(),
                'JSON (*.json);;All Files (*)',
            )
            if filename:
                self._profile_path_edit.setText(filename)

        def _schedule_settings_refresh(self):
            self._schedule_recompute()
            self._schedule_stats_refresh()

        def _schedule_recompute(self):
            if self._current_page_number() is None:
                return
            self._recompute_timer.start()

        def _schedule_stats_refresh(self):
            if not self._all_page_order:
                self._summary_label.setText('Output count: no file loaded.')
                return
            self._stats_request_id += 1
            self._stats_dirty = True
            self._summary_label.setText('Output count: counting...')
            self._stats_timer.start()

        def _enabled_packet_lists_for_page(self, page_number):
            packet_lists = tuple(self._page_packet_lists.get(int(page_number), ()))
            enabled = list(self._page_enabled_occurrences.get(int(page_number), [True] * len(packet_lists)))
            if len(enabled) < len(packet_lists):
                enabled.extend([True] * (len(packet_lists) - len(enabled)))
            return tuple(packet_list for packet_list, keep in zip(packet_lists, enabled) if keep)

        def _build_filtered_page_packet_lists(self):
            return {
                int(page_number): self._enabled_packet_lists_for_page(page_number)
                for page_number in self._all_page_order
            }

        def _load_profile_path(self, path):
            path = str(path or '').strip()
            if not path:
                return None
            cached = self._loaded_profile_cache.get(path)
            if cached is not None:
                return dict(cached)
            profile = pipeline.load_squash_profile(path)
            self._loaded_profile_cache[path] = dict(profile)
            return dict(profile)

        def _current_squash_settings(self):
            mode = str(self._mode_combo.currentData() or 'v3')
            profile = None
            if mode in {'custom', 'profile'}:
                if self._profile_path_edit.text().strip():
                    profile = self._load_profile_path(self._profile_path_edit.text())
                if self._profile_name_combo.currentData():
                    profile = pipeline.normalise_squash_profile({
                        **(profile or {}),
                        **pipeline.get_builtin_squash_profile(self._profile_name_combo.currentData()),
                    })
                profile = pipeline.normalise_squash_profile({
                    **(profile or {}),
                    'match_threshold': self._custom_controls['match_threshold'].value(),
                    'header_weight': self._custom_controls['header_weight'].value(),
                    'body_weight': self._custom_controls['body_weight'].value(),
                    'footer_weight': self._custom_controls['footer_weight'].value(),
                    'subcode_match_bonus': self._custom_controls['subcode_match_bonus'].value(),
                    'subcode_mismatch_penalty': self._custom_controls['subcode_mismatch_penalty'].value(),
                    'iterations': self._profile_iterations_spin.value(),
                })

            return {
                'squash_mode': mode,
                'threshold': int(self._threshold_spin.value()),
                'min_duplicates': int(self._min_duplicates_spin.value()),
                'ignore_empty': bool(self._ignore_empty_toggle.isChecked()),
                'best_of_n': int(self._best_of_n_spin.value()) or None,
                'use_confidence': bool(self._use_confidence_toggle.isChecked()),
                'v1_iterations': int(self._v1_iterations_spin.value()),
                'squash_profile': profile,
                'page_mode_overrides': dict(self._page_mode_overrides),
            }

        def _start_stats_refresh(self):
            if not self._stats_dirty:
                return
            if self._stats_worker is not None and self._stats_worker.isRunning():
                return
            try:
                settings = self._current_squash_settings()
            except Exception as exc:
                self._summary_label.setText(f'Output count: error ({exc}).')
                self._stats_dirty = False
                return

            self._stats_dirty = False
            request_id = self._stats_request_id
            filtered_packet_lists = self._build_filtered_page_packet_lists()
            self._stats_worker = SquashStatsWorker(
                request_id,
                self._all_page_order,
                filtered_packet_lists,
                settings,
                self,
            )
            self._stats_worker.progress.connect(self._stats_progress)
            self._stats_worker.completed.connect(self._stats_completed)
            self._stats_worker.failed.connect(self._stats_failed)
            self._stats_worker.start()

        def _stats_progress(self, current, total, page_label):
            self._summary_label.setText(
                f'Output count: counting {current}/{total} (P{page_label})...'
            )

        def _stats_completed(self, request_id, total_pages, total_subpages, page_summaries):
            self._stats_worker = None
            if int(request_id) != int(self._stats_request_id):
                if self._stats_dirty:
                    self._start_stats_refresh()
                return
            self._page_output_summaries = tuple(page_summaries)
            lost_pages = tuple(
                summary for summary in self._page_output_summaries
                if int(summary[1]) > 0 and int(summary[2]) == 0
            )
            self._summary_label.setText(
                f'Output count: {total_pages} pages, {total_subpages} squashed results. Lost: {len(lost_pages)}.'
            )
            self._refresh_lost_pages_list()
            if self._stats_dirty:
                self._start_stats_refresh()

        def _stats_failed(self, request_id, message):
            self._stats_worker = None
            if int(request_id) == int(self._stats_request_id):
                self._summary_label.setText(f'Output count: error ({message}).')
                self._page_output_summaries = ()
                self._lost_pages_list.clear()
            if self._stats_dirty:
                self._start_stats_refresh()

        def _refresh_lost_pages_list(self):
            self._lost_pages_list.clear()
            output_mode = str(self._output_mode_combo.currentData() or 'all')
            page_summaries = list(self._page_output_summaries)
            if output_mode == 'lost':
                page_summaries = [
                    summary for summary in page_summaries
                    if int(summary[1]) > 0 and int(summary[2]) == 0
                ]
            sort_mode = str(self._errors_sort_combo.currentData() or 'default')
            if sort_mode == 'ascending':
                page_summaries.sort(key=lambda item: int(item[0]))
            elif sort_mode == 'descending':
                page_summaries.sort(key=lambda item: int(item[0]), reverse=True)
            for page_number, enabled_count, result_count in page_summaries:
                total_count = len(self._page_occurrences.get(int(page_number), ()))
                self._lost_pages_list.addItem(
                    f'P{_format_page_number(page_number)}: {enabled_count}/{total_count} captures -> {result_count} results'
                )

        def _recompute_current_page(self):
            page_number = self._current_page_number()
            if page_number is None:
                return
            packet_lists = self._enabled_packet_lists_for_page(page_number)
            if not packet_lists:
                self._result_combo.clear()
                self._current_squashed = ()
                self._squashed_preview.clear('No enabled captures for this page')
                self._save_page_button.setEnabled(False)
                return
            try:
                settings = self._current_squash_settings()
                subpages = _squash_page(packet_lists, settings, page_number=page_number)
            except Exception as exc:
                self._current_squashed = ()
                self._result_combo.clear()
                self._squashed_preview.clear('Rebuild failed')
                self._save_page_button.setEnabled(False)
                self._status_label.setText(f'Rebuild failed: {exc}')
                return

            self._current_squashed = subpages
            blocked = self._result_combo.blockSignals(True)
            self._result_combo.clear()
            for index, subpage in enumerate(subpages, start=1):
                self._result_combo.addItem(f'{int(subpage.header.subpage):04X} #{index}')
            self._result_combo.blockSignals(blocked)
            if self._result_combo.count():
                self._result_combo.setCurrentIndex(0)
            self._render_squashed_preview()
            self._status_label.setText(
                f'P{_format_page_number(page_number)}: {len(packet_lists)} captures -> {len(subpages)} squashed results '
                f'using {self._label_for_mode(settings["page_mode_overrides"].get(int(page_number), settings["squash_mode"]))}.'
            )

        def _save_current_page(self):
            page_number = self._current_page_number()
            if page_number is None or not self._current_squashed:
                return
            suggested = (
                os.path.splitext(self._filename or 'page.t42')[0]
                + f'-P{_format_page_number(page_number)}-squashed.t42'
            )
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Current Squashed Page',
                suggested,
                'Teletext (*.t42);;All Files (*)',
            )
            if not filename:
                return
            try:
                with open(filename, 'wb') as handle:
                    _write_subpages(handle, self._current_squashed)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, 'Squash Tool', str(exc))
                return
            self.statusBar().showMessage(f'Saved current page to {filename}', 5000)

        def _save_squashed_file(self):
            if not self._all_page_order:
                return
            suggested = os.path.splitext(self._filename or 'squashed.t42')[0] + '-squashed.t42'
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Squashed T42',
                suggested,
                'Teletext (*.t42);;All Files (*)',
            )
            if not filename:
                return
            try:
                settings = self._current_squash_settings()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, 'Squash Tool', str(exc))
                return
            self._save_button.setEnabled(False)
            self._save_page_button.setEnabled(False)
            self._set_progress('Saving squashed pages', 0, len(self._all_page_order))
            filtered_packet_lists = self._build_filtered_page_packet_lists()
            self._save_worker = SquashSaveWorker(
                filename,
                self._all_page_order,
                filtered_packet_lists,
                settings,
                self,
            )
            self._save_worker.progress.connect(self._save_progress)
            self._save_worker.completed.connect(self._save_completed)
            self._save_worker.failed.connect(self._save_failed)
            self._save_worker.start()

        def _save_progress(self, current, total, page_label):
            self._set_progress(f'Saving P{page_label}', current, total)

        def _save_completed(self, filename):
            self._clear_progress()
            self._save_button.setEnabled(True)
            self._save_page_button.setEnabled(bool(self._current_squashed))
            self.statusBar().showMessage(f'Saved squashed file to {filename}', 5000)

        def _save_failed(self, message):
            self._clear_progress()
            self._save_button.setEnabled(True)
            self._save_page_button.setEnabled(bool(self._current_squashed))
            QtWidgets.QMessageBox.warning(self, 'Squash Tool', str(message))


def main(argv=None):
    if IMPORT_ERROR is not None:
        print(f'PyQt5 is not installed. Squash Tool is not available. ({IMPORT_ERROR})')
        return 1

    argv = list(sys.argv if argv is None else argv)
    app = _ensure_app()
    filename = argv[1] if len(argv) > 1 else None
    window = SquashTuneWindow(filename=filename)
    window.show()
    return app.exec_()


if __name__ == '__main__':  # pragma: no cover - GUI entrypoint
    raise SystemExit(main())
