import os
import subprocess
import sys
import time

import numpy as np

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
    from teletext.file import FileChunker
    from teletext.packet import Packet
    from teletext.service import Service
    from teletext.viewer import (
        DirectPageBuffer,
        ServiceNavigator,
        build_split_pattern,
        count_html_outputs,
        count_split_t42_outputs,
        export_html,
        export_selected_html,
        export_selected_t42,
        export_split_t42,
    )


if QtCore is not None:
    class ServiceLoader(QtCore.QThread):
        loaded = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)
        progress = QtCore.pyqtSignal(int, int, float)

        def __init__(self, filename):
            super().__init__()
            self._filename = filename

        def run(self):
            try:
                with open(self._filename, 'rb') as handle:
                    chunks = FileChunker(handle, 42)
                    total = len(chunks) if hasattr(chunks, '__len__') else 0
                    started_at = time.monotonic()
                    processed = 0
                    last_emitted = 0

                    def packets():
                        nonlocal processed, last_emitted
                        for number, data in chunks:
                            processed += 1
                            if total and (processed == 1 or processed - last_emitted >= 4096 or processed == total):
                                last_emitted = processed
                                self.progress.emit(processed, total, time.monotonic() - started_at)
                            yield Packet(data, number)

                    service = Service.from_packets(packets())
                    if total:
                        self.progress.emit(total, total, time.monotonic() - started_at)
            except Exception as exc:  # pragma: no cover - GUI error path
                self.failed.emit(str(exc))
            else:
                self.loaded.emit(service)


    class PageOverviewDialog(QtWidgets.QDialog):
        selectionRequested = QtCore.pyqtSignal(int, int)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._entries = ()
            self._preview_callback = None
            self._icon_cache = {}
            self._thumbnail_queue = []
            self._loaded_count = 0
            self._current_entries = ()
            self._last_scroll_value = 0
            self.setWindowTitle('Teletext Pages')
            self.resize(920, 620)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            controls = QtWidgets.QHBoxLayout()
            controls.setSpacing(8)
            controls.addWidget(QtWidgets.QLabel('Filter'))

            self._filter_input = QtWidgets.QLineEdit()
            self._filter_input.setPlaceholderText('100, 1AF, 0001')
            self._filter_input.textChanged.connect(self._rebuild_items)
            controls.addWidget(self._filter_input, 1)

            self._subpages_toggle = QtWidgets.QCheckBox('Subpages')
            self._subpages_toggle.setChecked(True)
            self._subpages_toggle.toggled.connect(self._rebuild_items)
            controls.addWidget(self._subpages_toggle)

            self._hex_pages_toggle = QtWidgets.QCheckBox('Hex Pages')
            self._hex_pages_toggle.setChecked(True)
            self._hex_pages_toggle.toggled.connect(self._rebuild_items)
            controls.addWidget(self._hex_pages_toggle)

            self._open_button = QtWidgets.QPushButton('Open')
            self._open_button.clicked.connect(self._open_current_item)
            controls.addWidget(self._open_button)

            root.addLayout(controls)

            self._stack = QtWidgets.QStackedWidget()

            self._loading_widget = QtWidgets.QWidget()
            loading_layout = QtWidgets.QVBoxLayout(self._loading_widget)
            loading_layout.setContentsMargins(0, 0, 0, 0)
            loading_layout.addStretch(1)
            self._loading_label = QtWidgets.QLabel('Loading previews...')
            self._loading_label.setAlignment(QtCore.Qt.AlignCenter)
            loading_layout.addWidget(self._loading_label)
            loading_layout.addStretch(1)
            self._stack.addWidget(self._loading_widget)

            self._list = QtWidgets.QListWidget()
            self._list.setViewMode(QtWidgets.QListView.IconMode)
            self._list.setResizeMode(QtWidgets.QListView.Adjust)
            self._list.setMovement(QtWidgets.QListView.Static)
            self._list.setWrapping(True)
            self._list.setSpacing(10)
            self._list.setUniformItemSizes(True)
            self._list.setWordWrap(True)
            self._list.setIconSize(QtCore.QSize(160, 120))
            self._list.setGridSize(QtCore.QSize(190, 160))
            self._list.itemActivated.connect(self._activate_item)
            self._list.itemDoubleClicked.connect(self._activate_item)
            self._stack.addWidget(self._list)
            root.addWidget(self._stack, 1)

            self._thumbnail_timer = QtCore.QTimer(self)
            self._thumbnail_timer.setInterval(0)
            self._thumbnail_timer.timeout.connect(self._populate_thumbnail_batch)

        @property
        def include_subpages(self):
            return self._subpages_toggle.isChecked()

        @property
        def include_hex_pages(self):
            return self._hex_pages_toggle.isChecked()

        def clear_icon_cache(self):
            self._icon_cache.clear()

        def populate(self, entries, preview_callback, include_subpages=None, include_hex_pages=None):
            self._entries = tuple(entries)
            self._preview_callback = preview_callback
            if include_subpages is not None:
                blocked = self._subpages_toggle.blockSignals(True)
                self._subpages_toggle.setChecked(include_subpages)
                self._subpages_toggle.blockSignals(blocked)
            if include_hex_pages is not None:
                blocked = self._hex_pages_toggle.blockSignals(True)
                self._hex_pages_toggle.setChecked(include_hex_pages)
                self._hex_pages_toggle.blockSignals(blocked)
            self._begin_loading()

        def _filtered_entries(self):
            pattern = self._filter_input.text().strip().upper()
            include_subpages = self._subpages_toggle.isChecked()
            include_hex_pages = self._hex_pages_toggle.isChecked()
            filtered = []
            seen_pages = set()

            for entry in self._entries:
                if not include_hex_pages and not ServiceNavigator.is_decimal_page(entry.page_number):
                    continue
                if not include_subpages and entry.page_number in seen_pages:
                    continue
                text = f'{entry.page_label} {entry.subpage_label}'.upper()
                if pattern and pattern not in text:
                    continue
                filtered.append(entry)
                seen_pages.add(entry.page_number)

            return filtered

        def _begin_loading(self):
            self._thumbnail_timer.stop()
            scrollbar = self._list.verticalScrollBar()
            self._last_scroll_value = scrollbar.value() if scrollbar is not None else 0
            self._current_entries = tuple(self._filtered_entries())
            self._thumbnail_queue = [
                entry for entry in self._current_entries
                if (entry.page_number, entry.subpage_number) not in self._icon_cache
            ]
            self._loaded_count = len(self._current_entries) - len(self._thumbnail_queue)
            self._open_button.setEnabled(False)

            if not self._current_entries:
                self._build_list_items()
                return

            if self._thumbnail_queue:
                self._update_loading_label()
                self._stack.setCurrentWidget(self._loading_widget)
                self._thumbnail_timer.start()
            else:
                self._build_list_items()

        def _rebuild_items(self):
            self._begin_loading()

        def _update_loading_label(self):
            total = len(self._current_entries)
            self._loading_label.setText(f'Loading previews {self._loaded_count}/{total}')

        def _build_list_items(self):
            current_item = self._list.currentItem()
            current_key = current_item.data(QtCore.Qt.UserRole) if current_item is not None else None
            self._list.clear()

            for entry in self._current_entries:
                text = f'{entry.page_label}\n{entry.subpage_label}'
                item = QtWidgets.QListWidgetItem(text)
                key = (entry.page_number, entry.subpage_number)
                item.setData(QtCore.Qt.UserRole, key)
                item.setToolTip(text.replace('\n', ' '))
                cached_icon = self._icon_cache.get(key)
                if cached_icon is not None:
                    item.setIcon(cached_icon)
                self._list.addItem(item)
                if current_key == key:
                    self._list.setCurrentItem(item)

            if self._list.count() and self._list.currentItem() is None:
                self._list.setCurrentRow(0)
            self._open_button.setEnabled(self._list.count() > 0)
            self._stack.setCurrentWidget(self._list)
            scrollbar = self._list.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(min(self._last_scroll_value, scrollbar.maximum()))

        def _populate_thumbnail_batch(self):
            if self._preview_callback is None or not self.isVisible():
                self._thumbnail_timer.stop()
                return

            for _ in range(12):
                if not self._thumbnail_queue:
                    self._thumbnail_timer.stop()
                    self._build_list_items()
                    return
                entry = self._thumbnail_queue.pop(0)
                page_number = entry.page_number
                subpage_number = entry.subpage_number
                key = (page_number, subpage_number)
                icon = self._preview_callback(page_number, subpage_number, self._list.iconSize())
                if icon is not None:
                    self._icon_cache[key] = icon
                self._loaded_count += 1
            self._update_loading_label()

        def _activate_item(self, item):
            if item is None:
                return
            page_number, subpage_number = item.data(QtCore.Qt.UserRole)
            self.selectionRequested.emit(int(page_number), int(subpage_number))
            self.accept()

        def _open_current_item(self):
            self._activate_item(self._list.currentItem())

        def showEvent(self, event):  # pragma: no cover - GUI event path
            super().showEvent(event)
            if self._thumbnail_queue:
                self._stack.setCurrentWidget(self._loading_widget)
                self._update_loading_label()
                self._thumbnail_timer.start()

        def hideEvent(self, event):  # pragma: no cover - GUI event path
            self._thumbnail_timer.stop()
            super().hideEvent(event)


    class ServiceInfoDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle('Teletext Info')
            self.resize(760, 560)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            self._report = QtWidgets.QPlainTextEdit()
            self._report.setReadOnly(True)
            self._report.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            root.addWidget(self._report, 1)

            buttons = QtWidgets.QHBoxLayout()
            buttons.addStretch(1)

            self._copy_button = QtWidgets.QPushButton('Copy')
            self._copy_button.clicked.connect(self.copy_report)
            buttons.addWidget(self._copy_button)

            close_button = QtWidgets.QPushButton('Close')
            close_button.clicked.connect(self.close)
            buttons.addWidget(close_button)

            root.addLayout(buttons)

        def set_report(self, text):
            self._report.setPlainText(text)
            self._report.moveCursor(QtGui.QTextCursor.Start)

        def copy_report(self):
            QtWidgets.QApplication.clipboard().setText(self._report.toPlainText())


    class SplitExportDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle('Split Export')
            self.resize(760, 460)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(10)

            single_group = QtWidgets.QGroupBox('Single Page / Subpage')
            single_layout = QtWidgets.QGridLayout(single_group)

            single_layout.addWidget(QtWidgets.QLabel('Page'), 0, 0)
            self._single_page_input = QtWidgets.QLineEdit()
            self._single_page_input.setMaxLength(3)
            self._single_page_input.setPlaceholderText('100')
            single_layout.addWidget(self._single_page_input, 0, 1)

            single_layout.addWidget(QtWidgets.QLabel('Subpage'), 0, 2)
            self._single_subpage_input = QtWidgets.QLineEdit()
            self._single_subpage_input.setMaxLength(4)
            self._single_subpage_input.setPlaceholderText('0000 or empty')
            single_layout.addWidget(self._single_subpage_input, 0, 3)

            hint = QtWidgets.QLabel('Leave subpage empty to export the whole page.')
            single_layout.addWidget(hint, 1, 0, 1, 4)

            button_row = QtWidgets.QHBoxLayout()
            self.single_t42_button = QtWidgets.QPushButton('Save T42...')
            button_row.addWidget(self.single_t42_button)
            self.single_html_button = QtWidgets.QPushButton('Save HTML...')
            button_row.addWidget(self.single_html_button)
            self.current_t42_button = QtWidgets.QPushButton('Current T42...')
            button_row.addWidget(self.current_t42_button)
            self.current_html_button = QtWidgets.QPushButton('Current HTML...')
            button_row.addWidget(self.current_html_button)
            button_row.addStretch(1)
            single_layout.addLayout(button_row, 2, 0, 1, 4)

            root.addWidget(single_group)

            bulk_group = QtWidgets.QGroupBox('Bulk Export')
            bulk_layout = QtWidgets.QGridLayout(bulk_group)

            self._export_t42_toggle = QtWidgets.QCheckBox('Export T42')
            self._export_t42_toggle.setChecked(True)
            bulk_layout.addWidget(self._export_t42_toggle, 0, 0)

            self._t42_dir_input = QtWidgets.QLineEdit()
            bulk_layout.addWidget(self._t42_dir_input, 0, 1)
            t42_browse = QtWidgets.QPushButton('Browse...')
            t42_browse.clicked.connect(lambda: self._browse_directory(self._t42_dir_input))
            bulk_layout.addWidget(t42_browse, 0, 2)

            flags_row = QtWidgets.QHBoxLayout()
            flags_row.addWidget(QtWidgets.QLabel('T42 Flags'))
            self._flag_m = QtWidgets.QCheckBox('m')
            self._flag_m.setChecked(True)
            self._flag_p = QtWidgets.QCheckBox('p')
            self._flag_p.setChecked(True)
            self._flag_s = QtWidgets.QCheckBox('s')
            self._flag_s.setChecked(True)
            self._flag_c = QtWidgets.QCheckBox('c')
            self._flag_c.setChecked(True)
            for checkbox in (self._flag_m, self._flag_p, self._flag_s, self._flag_c):
                checkbox.toggled.connect(self._update_pattern_preview)
                flags_row.addWidget(checkbox)
            flags_row.addStretch(1)
            bulk_layout.addLayout(flags_row, 1, 1, 1, 2)

            self._pattern_preview = QtWidgets.QLabel()
            bulk_layout.addWidget(self._pattern_preview, 2, 1, 1, 2)

            self._export_html_toggle = QtWidgets.QCheckBox('Export HTML')
            self._export_html_toggle.setChecked(True)
            bulk_layout.addWidget(self._export_html_toggle, 3, 0)

            self._html_dir_input = QtWidgets.QLineEdit()
            bulk_layout.addWidget(self._html_dir_input, 3, 1)
            html_browse = QtWidgets.QPushButton('Browse...')
            html_browse.clicked.connect(lambda: self._browse_directory(self._html_dir_input))
            bulk_layout.addWidget(html_browse, 3, 2)

            html_mode_row = QtWidgets.QHBoxLayout()
            html_mode_row.addWidget(QtWidgets.QLabel('HTML Mode'))
            self._html_pages_radio = QtWidgets.QRadioButton('Pages only')
            self._html_pages_radio.setChecked(True)
            html_mode_row.addWidget(self._html_pages_radio)
            self._html_subpages_radio = QtWidgets.QRadioButton('Pages + Subpages')
            html_mode_row.addWidget(self._html_subpages_radio)
            html_mode_row.addStretch(1)
            bulk_layout.addLayout(html_mode_row, 4, 1, 1, 2)

            codepage_row = QtWidgets.QHBoxLayout()
            codepage_row.addWidget(QtWidgets.QLabel('HTML Language'))
            self._html_codepage_combo = QtWidgets.QComboBox()
            for key, label in (
                ('', 'Default'),
                ('cyr', 'Cyrillic'),
                ('swe', 'Swedish'),
                ('ita', 'Italian'),
                ('deu', 'German'),
                ('fra', 'French'),
                ('pol', 'Polish'),
                ('nld', 'Dutch'),
            ):
                self._html_codepage_combo.addItem(label, key)
            codepage_row.addWidget(self._html_codepage_combo)
            codepage_row.addStretch(1)
            bulk_layout.addLayout(codepage_row, 5, 1, 1, 2)

            export_row = QtWidgets.QHBoxLayout()
            export_row.addStretch(1)
            self.export_all_button = QtWidgets.QPushButton('Export All')
            export_row.addWidget(self.export_all_button)
            bulk_layout.addLayout(export_row, 6, 0, 1, 3)

            root.addWidget(bulk_group)
            self._update_pattern_preview()

        def _browse_directory(self, line_edit):
            directory = QtWidgets.QFileDialog.getExistingDirectory(self, 'Choose export directory', line_edit.text() or '')
            if directory:
                line_edit.setText(directory)

        def _update_pattern_preview(self):
            pattern = build_split_pattern(
                include_magazine=self._flag_m.isChecked(),
                include_page=self._flag_p.isChecked(),
                include_subpage=self._flag_s.isChecked(),
                include_count=self._flag_c.isChecked(),
                extension='.t42',
            )
            self._pattern_preview.setText(f'Pattern: {pattern}')

        def set_current_selection(self, page_text, subpage_number):
            self._single_page_input.setText(page_text)
            self._single_subpage_input.setText(f'{subpage_number:04X}')

        def set_default_directories(self, t42_dir, html_dir):
            if t42_dir:
                self._t42_dir_input.setText(t42_dir)
            if html_dir:
                self._html_dir_input.setText(html_dir)

        def set_html_localcodepage(self, localcodepage):
            index = self._html_codepage_combo.findData(localcodepage or '')
            if index >= 0:
                self._html_codepage_combo.setCurrentIndex(index)

        def single_page_number(self):
            return ServiceNavigator.parse_page_number(self._single_page_input.text())

        def single_subpage_number(self):
            text = self._single_subpage_input.text().strip().upper()
            if not text:
                return None
            return int(text, 16)

        def t42_enabled(self):
            return self._export_t42_toggle.isChecked()

        def html_enabled(self):
            return self._export_html_toggle.isChecked()

        def t42_directory(self):
            return self._t42_dir_input.text().strip()

        def html_directory(self):
            return self._html_dir_input.text().strip()

        def html_include_subpages(self):
            return self._html_subpages_radio.isChecked()

        def html_localcodepage(self):
            value = self._html_codepage_combo.currentData()
            return value or None

        def split_pattern(self):
            return build_split_pattern(
                include_magazine=self._flag_m.isChecked(),
                include_page=self._flag_p.isChecked(),
                include_subpage=self._flag_s.isChecked(),
                include_count=self._flag_c.isChecked(),
                extension='.t42',
            )


    class HtmlPreviewDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle('HTML Preview')
            self.resize(960, 700)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            self._path_label = QtWidgets.QLabel()
            self._path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            root.addWidget(self._path_label)

            self._browser = QtWidgets.QTextBrowser()
            self._browser.setOpenExternalLinks(True)
            root.addWidget(self._browser, 1)

            buttons = QtWidgets.QHBoxLayout()
            buttons.addStretch(1)
            close_button = QtWidgets.QPushButton('Close')
            close_button.clicked.connect(self.close)
            buttons.addWidget(close_button)
            root.addLayout(buttons)

        def open_html(self, filename):
            filename = os.path.abspath(filename)
            self._path_label.setText(filename)
            self.setWindowTitle(f'HTML Preview - {os.path.basename(filename)}')
            self._browser.clear()
            self._browser.setSearchPaths([os.path.dirname(filename)])
            self._browser.setSource(QtCore.QUrl.fromLocalFile(filename))


    class FileBrowserDialog(QtWidgets.QDialog):
        fileRequested = QtCore.pyqtSignal(str)

        def __init__(self, title, file_glob, parent=None):
            super().__init__(parent)
            self._directory = ''
            self._file_glob = file_glob
            self.setWindowTitle(title)
            self.resize(760, 560)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel('Filter'))
            self._filter_input = QtWidgets.QLineEdit()
            self._filter_input.setPlaceholderText('page, subpage, filename')
            self._filter_input.textChanged.connect(self._rebuild_list)
            controls.addWidget(self._filter_input, 1)
            root.addLayout(controls)

            self._location_label = QtWidgets.QLabel()
            self._location_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            root.addWidget(self._location_label)

            self._list = QtWidgets.QListWidget()
            self._list.itemActivated.connect(self._open_current_item)
            self._list.itemDoubleClicked.connect(self._open_current_item)
            root.addWidget(self._list, 1)

            buttons = QtWidgets.QHBoxLayout()
            self._open_button = QtWidgets.QPushButton('Open')
            self._open_button.clicked.connect(lambda: self._open_current_item(self._list.currentItem()))
            buttons.addWidget(self._open_button)
            buttons.addStretch(1)
            close_button = QtWidgets.QPushButton('Close')
            close_button.clicked.connect(self.close)
            buttons.addWidget(close_button)
            root.addLayout(buttons)

        def set_directory(self, directory):
            self._directory = os.path.abspath(directory)
            self._location_label.setText(self._directory)
            self._rebuild_list()

        def _matching_files(self):
            if not self._directory or not os.path.isdir(self._directory):
                return []
            entries = []
            for path in sorted(
                os.path.join(self._directory, name)
                for name in os.listdir(self._directory)
            ):
                if not os.path.isfile(path):
                    continue
                if not QtCore.QDir.match(self._file_glob, os.path.basename(path)):
                    continue
                entries.append(path)
            pattern = self._filter_input.text().strip().lower()
            if not pattern:
                return entries
            return [path for path in entries if pattern in os.path.basename(path).lower()]

        def _rebuild_list(self):
            current = self._list.currentItem()
            current_path = current.data(QtCore.Qt.UserRole) if current is not None else None
            self._list.clear()
            for path in self._matching_files():
                name = os.path.basename(path)
                item = QtWidgets.QListWidgetItem(name)
                item.setData(QtCore.Qt.UserRole, path)
                item.setToolTip(path)
                self._list.addItem(item)
                if current_path == path:
                    self._list.setCurrentItem(item)
            if self._list.count() and self._list.currentItem() is None:
                self._list.setCurrentRow(0)
            self._open_button.setEnabled(self._list.count() > 0)

        def _open_current_item(self, item):
            if item is None:
                return
            self.fileRequested.emit(item.data(QtCore.Qt.UserRole))
            self.accept()


    class TeletextViewerWindow(QtWidgets.QMainWindow):
        fastext_colours = (
            ('#c62828', '#ffffff'),
            ('#2e7d32', '#ffffff'),
            ('#f9a825', '#000000'),
            ('#00838f', '#ffffff'),
        )
        base_zoom = 2
        stretch_zoom = 3

        def __init__(self, filename=None):
            super().__init__()
            self._filename = None
            self._navigator = None
            self._loader = None
            self._overview_dialog = None
            self._info_dialog = None
            self._split_dialog = None
            self._html_preview_dialog = None
            self._t42_browser_dialog = None
            self._html_browser_dialog = None
            self._selection_overlay = None
            self._metadata_cache = None
            self._user_t42_directory = None
            self._user_html_directory = None
            self._overview_dirty = True
            self._overview_signature = None
            self._preview_widget = None
            self._preview_decoder = None
            self._windowed_pos = None
            self._windowed_was_maximized = False
            self._loading_started_at = None
            self._selection_overlay_enabled = False
            self._normal_layout_margins = (12, 12, 12, 12)
            self._normal_layout_spacing = 10
            self._icon_path = self._resource_path('teletext.png')
            self._font_family = self._load_font_family()
            self._direct_page_buffer = DirectPageBuffer()
            self._direct_page_timer = QtCore.QTimer(self)
            self._direct_page_timer.setInterval(1500)
            self._direct_page_timer.setSingleShot(True)
            self._direct_page_timer.timeout.connect(self._reset_direct_page_buffer)
            self._auto_scroll_timer = QtCore.QTimer(self)
            self._auto_scroll_timer.setInterval(3500)
            self._auto_scroll_timer.timeout.connect(self._auto_advance_subpage)

            self.setWindowTitle('Teletext Viewer')
            self.setAcceptDrops(True)
            if os.path.exists(self._icon_path):
                self.setWindowIcon(QtGui.QIcon(self._icon_path))
            self._build_ui()
            self._build_shortcuts()
            self._clear_decoder()
            self._set_navigation_enabled(False)

            if filename is not None:
                QtCore.QTimer.singleShot(0, lambda: self.open_file(filename))
            else:
                QtCore.QTimer.singleShot(0, self.open_dialog)

        def _build_ui(self):
            central = QtWidgets.QWidget()
            root = QtWidgets.QVBoxLayout(central)
            root.setContentsMargins(*self._normal_layout_margins)
            root.setSpacing(self._normal_layout_spacing)
            self._root_layout = root

            self._toolbar_widget = QtWidgets.QWidget()
            toolbar = QtWidgets.QHBoxLayout(self._toolbar_widget)
            toolbar.setContentsMargins(0, 0, 0, 0)
            toolbar.setSpacing(8)

            self._open_action = QtWidgets.QAction('Open .t42...', self)
            self._open_action.triggered.connect(self.open_dialog)
            self._open_t42_folder_action = QtWidgets.QAction('Open T42 Folder', self)
            self._open_t42_folder_action.triggered.connect(self.open_t42_folder)
            self._open_html_folder_action = QtWidgets.QAction('Open HTML Folder', self)
            self._open_html_folder_action.triggered.connect(self.open_html_folder)
            self._open_html_file_action = QtWidgets.QAction('Open HTML File...', self)
            self._open_html_file_action.triggered.connect(self.open_html_file)

            self._open_button = QtWidgets.QToolButton()
            self._open_button.setText('Open')
            self._open_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self._open_menu = QtWidgets.QMenu(self._open_button)
            self._open_menu.addAction(self._open_action)
            self._open_menu.addSeparator()
            self._open_menu.addAction(self._open_t42_folder_action)
            self._open_menu.addAction(self._open_html_folder_action)
            self._open_menu.addAction(self._open_html_file_action)
            self._open_button.setMenu(self._open_menu)
            toolbar.addWidget(self._open_button)

            self._split_export_action = QtWidgets.QAction('Export...', self)
            self._split_export_action.triggered.connect(self.show_split_dialog)
            self._split_button = QtWidgets.QToolButton()
            self._split_button.setText('Split')
            self._split_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self._split_menu = QtWidgets.QMenu(self._split_button)
            self._split_menu.addAction(self._split_export_action)
            self._split_button.setMenu(self._split_menu)
            toolbar.addWidget(self._split_button)

            self._screenshot_action = QtWidgets.QAction('Screenshot...', self)
            self._screenshot_action.triggered.connect(self.save_screenshot)
            self._overview_action = QtWidgets.QAction('Overview', self)
            self._overview_action.triggered.connect(self.show_overview)
            self._info_action = QtWidgets.QAction('Info', self)
            self._info_action.triggered.connect(self.show_info)
            self._fullscreen_action = QtWidgets.QAction('Fullscreen', self)
            self._fullscreen_action.setCheckable(True)
            self._fullscreen_action.toggled.connect(self._toggle_fullscreen_from_action)

            self._functions_button = QtWidgets.QToolButton()
            self._functions_button.setText('Functions')
            self._functions_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self._functions_menu = QtWidgets.QMenu(self._functions_button)
            self._functions_menu.addAction(self._screenshot_action)
            self._functions_menu.addAction(self._overview_action)
            self._functions_menu.addAction(self._info_action)
            self._functions_menu.addSeparator()
            self._functions_menu.addAction(self._fullscreen_action)
            self._functions_button.setMenu(self._functions_menu)
            toolbar.addWidget(self._functions_button)

            self._fullscreen_button = QtWidgets.QPushButton('Fullscreen')
            self._fullscreen_button.setCheckable(True)
            self._fullscreen_button.toggled.connect(self._set_fullscreen)
            self._fullscreen_button.hide()

            self._settings_button = QtWidgets.QToolButton()
            self._settings_button.setText('Settings')
            self._settings_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self._settings_menu = QtWidgets.QMenu(self._settings_button)
            self._single_height_action = self._settings_menu.addAction('Single Height')
            self._single_height_action.setCheckable(True)
            self._single_height_action.toggled.connect(self._set_single_height)
            self._single_width_action = self._settings_menu.addAction('Single Width')
            self._single_width_action.setCheckable(True)
            self._single_width_action.toggled.connect(self._set_single_width)
            self._no_flash_action = self._settings_menu.addAction('No Flash')
            self._no_flash_action.setCheckable(True)
            self._no_flash_action.toggled.connect(self._set_no_flash)
            self._highlight_text_action = self._settings_menu.addAction('Highlight Characters')
            self._highlight_text_action.setCheckable(True)
            self._highlight_text_action.toggled.connect(self._set_highlight_text)
            self._mouse_wheel_pages_action = self._settings_menu.addAction('Mouse Wheel Pages')
            self._mouse_wheel_pages_action.setCheckable(True)
            self._mouse_wheel_pages_action.setChecked(True)
            self._settings_menu.addSeparator()
            self._auto_menu = self._settings_menu.addMenu('Auto')
            self._auto_subpages_action = self._auto_menu.addAction('Subpages')
            self._auto_subpages_action.setCheckable(True)
            self._auto_subpages_action.setChecked(True)
            self._auto_subpages_action.toggled.connect(lambda checked=False: self._sync_auto_scroll())
            self._auto_pages_action = self._auto_menu.addAction('Pages')
            self._auto_pages_action.setCheckable(True)
            self._auto_pages_action.setChecked(True)
            self._auto_pages_action.toggled.connect(lambda checked=False: self._sync_auto_scroll())
            self._fullscreen_layout_menu = self._settings_menu.addMenu('Fullscreen Layout')
            self._fullscreen_layout_group = QtWidgets.QActionGroup(self)
            self._fullscreen_layout_group.setExclusive(True)
            self._fullscreen_43_action = self._fullscreen_layout_menu.addAction('4:3')
            self._fullscreen_43_action.setCheckable(True)
            self._fullscreen_43_action.setChecked(True)
            self._fullscreen_43_action.toggled.connect(
                lambda checked=False: self._set_fullscreen_layout('4:3', checked)
            )
            self._fullscreen_layout_group.addAction(self._fullscreen_43_action)
            self._fullscreen_stretch_action = self._fullscreen_layout_menu.addAction('Fill Screen')
            self._fullscreen_stretch_action.setCheckable(True)
            self._fullscreen_stretch_action.toggled.connect(
                lambda checked=False: self._set_fullscreen_layout('stretch', checked)
            )
            self._fullscreen_layout_group.addAction(self._fullscreen_stretch_action)
            self._settings_menu.addSeparator()
            self._no_hex_pages_action = self._settings_menu.addAction('No Hex Pages')
            self._no_hex_pages_action.setCheckable(True)
            self._no_hex_pages_action.toggled.connect(self._set_no_hex_pages)
            self._language_menu = self._settings_menu.addMenu('Language')
            self._language_action_group = QtWidgets.QActionGroup(self)
            self._language_action_group.setExclusive(True)
            self._language_actions = {}
            for key, label in (
                ('default', 'Default'),
                ('cyr', 'Cyrillic'),
                ('swe', 'Swedish'),
                ('ita', 'Italian'),
                ('deu', 'German'),
                ('fra', 'French'),
                ('pol', 'Polish'),
                ('nld', 'Dutch'),
            ):
                action = self._language_menu.addAction(label)
                action.setCheckable(True)
                action.toggled.connect(lambda checked=False, item=key: self._set_language(item, checked))
                self._language_action_group.addAction(action)
                self._language_actions[key] = action
            self._settings_button.setMenu(self._settings_menu)
            toolbar.addWidget(self._settings_button)

            toolbar.addWidget(QtWidgets.QLabel('Page'))
            self._page_input = QtWidgets.QLineEdit()
            self._page_input.setMaxLength(4)
            self._page_input.setFixedWidth(80)
            self._page_input.setPlaceholderText('100')
            self._page_input.returnPressed.connect(self.go_to_page_text)
            self._page_input.installEventFilter(self)
            toolbar.addWidget(self._page_input)

            self._go_button = QtWidgets.QPushButton('Go')
            self._go_button.clicked.connect(self.go_to_page_text)
            toolbar.addWidget(self._go_button)

            self._auto_toggle = QtWidgets.QCheckBox('Auto')
            self._auto_toggle.toggled.connect(self._set_auto_scroll)
            self._auto_toggle.setChecked(True)
            toolbar.addWidget(self._auto_toggle)

            self._stretch_toggle = QtWidgets.QCheckBox('Zoom')
            self._stretch_toggle.toggled.connect(self._set_stretch)
            toolbar.addWidget(self._stretch_toggle)

            self._crt_toggle = QtWidgets.QCheckBox('CRT')
            self._crt_toggle.setChecked(True)
            self._crt_toggle.toggled.connect(self._set_crt_effect)
            toolbar.addWidget(self._crt_toggle)

            toolbar.addStretch(1)

            self._page_label = QtWidgets.QLabel('Page: ---')
            toolbar.addWidget(self._page_label)

            self._subpage_label = QtWidgets.QLabel('Subpage: --/--')
            toolbar.addWidget(self._subpage_label)

            root.addWidget(self._toolbar_widget)

            self._decoder_widget = QtQuickWidgets.QQuickWidget()
            self._decoder_widget.setResizeMode(QtQuickWidgets.QQuickWidget.SizeViewToRootObject)
            self._decoder_widget.setClearColor(QtGui.QColor('black'))
            self._decoder_widget.setFocusPolicy(QtCore.Qt.NoFocus)
            self._decoder = Decoder(self._decoder_widget, font_family=self._font_family)
            self._decoder.zoom = self.base_zoom
            self._decoder_widget.setFixedSize(self._decoder.size())
            self._decoder_area = QtWidgets.QWidget()
            self._decoder_area.installEventFilter(self)
            decoder_layout = QtWidgets.QGridLayout(self._decoder_area)
            decoder_layout.setContentsMargins(0, 0, 0, 0)
            decoder_layout.setSpacing(0)
            self._decoder_layout = decoder_layout
            decoder_layout.addWidget(self._decoder_widget, 0, 0, QtCore.Qt.AlignCenter)
            self._decoder_widget.installEventFilter(self)
            root.addWidget(self._decoder_area, 0, QtCore.Qt.AlignCenter)
            self._decoder.doubleheight = False
            self._decoder.doublewidth = False
            self._decoder.flashenabled = False
            self._decoder.highlighttext = False
            for action in (
                self._single_height_action,
                self._single_width_action,
                self._no_flash_action,
                self._mouse_wheel_pages_action,
                self._language_actions['default'],
            ):
                blocked = action.blockSignals(True)
                action.setChecked(True)
                action.blockSignals(blocked)

            self._nav_widget = QtWidgets.QWidget()
            nav = QtWidgets.QHBoxLayout(self._nav_widget)
            nav.setContentsMargins(0, 0, 0, 0)
            nav.setSpacing(8)

            self._next_page_button = QtWidgets.QPushButton('\u2191 Next Page')
            self._next_page_button.clicked.connect(self.next_page)
            nav.addWidget(self._next_page_button)

            self._prev_page_button = QtWidgets.QPushButton('\u2193 Prev Page')
            self._prev_page_button.clicked.connect(self.prev_page)
            nav.addWidget(self._prev_page_button)

            self._prev_subpage_button = QtWidgets.QPushButton('\u2190 Prev Subpage')
            self._prev_subpage_button.clicked.connect(self.prev_subpage)
            nav.addWidget(self._prev_subpage_button)

            self._next_subpage_button = QtWidgets.QPushButton('\u2192 Next Subpage')
            self._next_subpage_button.clicked.connect(self.next_subpage)
            nav.addWidget(self._next_subpage_button)

            nav.addStretch(1)
            nav.addWidget(QtWidgets.QLabel('Keyboard: Up/Down = page, Left/Right = subpage'))

            root.addWidget(self._nav_widget)

            self._fastext_widget = QtWidgets.QWidget()
            fastext = QtWidgets.QHBoxLayout(self._fastext_widget)
            fastext.setContentsMargins(0, 0, 0, 0)
            fastext.setSpacing(8)
            self._fastext_buttons = []
            for index, (background, foreground) in enumerate(self.fastext_colours):
                button = QtWidgets.QPushButton('---')
                button.setMinimumHeight(44)
                button.setMinimumWidth(110)
                button.setStyleSheet(
                    'QPushButton {'
                    f'background-color: {background};'
                    f'color: {foreground};'
                    'font-weight: bold;'
                    'border: none;'
                    'padding: 8px 14px;'
                    '}'
                    'QPushButton:disabled {'
                    'color: rgba(255, 255, 255, 0.55);'
                    '}'
                )
                button.clicked.connect(lambda checked=False, item=index: self.go_to_fastext(item))
                self._fastext_buttons.append(button)
                fastext.addWidget(button)
            root.addWidget(self._fastext_widget)

            self.setCentralWidget(central)
            self.statusBar().showMessage('Open a .t42 file to start.')

        def _build_shortcuts(self):
            QtWidgets.QShortcut(QtGui.QKeySequence('Up'), self, activated=self.next_page)
            QtWidgets.QShortcut(QtGui.QKeySequence('Down'), self, activated=self.prev_page)
            QtWidgets.QShortcut(QtGui.QKeySequence('Left'), self, activated=self.prev_subpage)
            QtWidgets.QShortcut(QtGui.QKeySequence('Right'), self, activated=self.next_subpage)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+I'), self, activated=self.show_info)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+O'), self, activated=self.open_dialog)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+P'), self, activated=self.show_overview)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+S'), self, activated=self.save_screenshot)
            QtWidgets.QShortcut(QtGui.QKeySequence('F11'), self, activated=self._toggle_fullscreen_shortcut)
            QtWidgets.QShortcut(QtGui.QKeySequence('Escape'), self, activated=self._leave_fullscreen_shortcut)

        def _resource_path(self, filename):
            return os.path.join(os.path.dirname(__file__), filename)

        def _load_font_family(self):
            font_path = self._resource_path('teletext2.ttf')
            if os.path.exists(font_path):
                font_id = QtGui.QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        return families[0]
            return 'teletext2'

        def _current_language_key(self):
            for key, action in self._language_actions.items():
                if action.isChecked():
                    return key
            return 'default'

        def _header_row(self, page_number, subpage):
            header = np.full((40,), fill_value=0x20, dtype=np.uint8)
            magazine, page = self._navigator.split_page_number(page_number)
            header[3:7] = np.frombuffer(f'P{magazine}{page:02X}'.encode('ascii'), dtype=np.uint8)
            header[8:] = subpage.header.displayable[:]
            return header

        def _apply_decoder_preferences(self, decoder, preview=False):
            decoder.doubleheight = not self._single_height_action.isChecked()
            decoder.doublewidth = not self._single_width_action.isChecked()
            decoder.flashenabled = not self._no_flash_action.isChecked()
            decoder.highlighttext = self._highlight_text_action.isChecked()
            decoder.language = self._current_language_key()
            decoder.crteffect = False if preview else self._crt_toggle.isChecked()

        def _paint_decoder(self, decoder, page_number, subpage_number):
            subpage = self._navigator.subpage(page_number, subpage_number)
            decoder.pagecodepage = subpage.codepage
            decoder[0] = self._header_row(page_number, subpage)
            decoder[1:] = subpage.displayable[:]
            return subpage

        def _ensure_preview_renderer(self):
            if self._preview_decoder is not None:
                return
            self._preview_widget = QtQuickWidgets.QQuickWidget()
            if hasattr(QtCore.Qt, 'WA_DontShowOnScreen'):
                self._preview_widget.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
            self._preview_widget.setResizeMode(QtQuickWidgets.QQuickWidget.SizeViewToRootObject)
            self._preview_widget.setClearColor(QtGui.QColor('black'))
            self._preview_widget.setFocusPolicy(QtCore.Qt.NoFocus)
            self._preview_decoder = Decoder(self._preview_widget, font_family=self._font_family)
            self._preview_decoder.zoom = 1

        def _make_overview_icon(self, page_number, subpage_number, icon_size):
            if self._navigator is None:
                return None
            try:
                self._ensure_preview_renderer()
                self._preview_decoder.zoom = 1
                self._apply_decoder_preferences(self._preview_decoder, preview=True)
                self._paint_decoder(self._preview_decoder, page_number, subpage_number)
                self._preview_decoder.fullscreenmode = False
                self._preview_decoder.fullscreenstretch = False
                self._preview_widget.setFixedSize(self._preview_decoder.size())
                self._preview_widget.show()
                QtWidgets.QApplication.processEvents()
                if hasattr(self._preview_widget, 'grabFramebuffer'):
                    pixmap = QtGui.QPixmap.fromImage(self._preview_widget.grabFramebuffer())
                else:
                    pixmap = self._preview_widget.grab()
                pixmap = pixmap.scaled(
                    icon_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.FastTransformation,
                )
                self._preview_widget.hide()
            except Exception:  # pragma: no cover - GUI fallback path
                return None
            return QtGui.QIcon(pixmap)

        def _clear_decoder(self):
            self._decoder[:] = np.full((25, 40), fill_value=0x20, dtype=np.uint8)
            if self._selection_overlay is not None:
                self._selection_overlay.clear()

        def _ensure_selection_overlay(self):
            if self._selection_overlay is not None:
                return self._selection_overlay

            overlay = QtWidgets.QPlainTextEdit()
            overlay.setReadOnly(True)
            overlay.setFrameShape(QtWidgets.QFrame.NoFrame)
            overlay.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            overlay.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            overlay.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            overlay.setWordWrapMode(QtGui.QTextOption.NoWrap)
            overlay.document().setDocumentMargin(0)
            overlay.setCursorWidth(0)
            overlay_font = QtGui.QFont(self._font_family)
            overlay_font.setStyleStrategy(QtGui.QFont.NoSubpixelAntialias)
            overlay_font.setHintingPreference(QtGui.QFont.PreferNoHinting)
            overlay_font.setPixelSize(self.base_zoom * 10)
            overlay_font.setStretch(int(round(self._decoder.horizontalscale * 100)))
            overlay.setFont(overlay_font)
            overlay.setStyleSheet(
                'QPlainTextEdit {'
                'background: transparent;'
                'color: transparent;'
                'selection-background-color: rgba(120, 160, 255, 150);'
                'selection-color: white;'
                'border: none;'
                'padding: 0;'
                '}'
            )
            overlay.hide()
            overlay.installEventFilter(self)
            self._decoder_layout.addWidget(overlay, 0, 0, QtCore.Qt.AlignCenter)
            self._selection_overlay = overlay
            return overlay

        def _resize_window_to_content(self):
            if self._fullscreen_button.isChecked() or self.isMaximized():
                return
            if self.centralWidget() is not None:
                self.centralWidget().adjustSize()
            self.adjustSize()
            self.resize(self.sizeHint())

        def _sync_decoder_size(self):
            if self._fullscreen_button.isChecked():
                area_size = self.centralWidget().size() if self.centralWidget() is not None else self.size()
                if area_size.width() <= 0 or area_size.height() <= 0:
                    return
                self._decoder.fullscreenmode = True
                self._decoder.fullscreenstretch = self._fullscreen_stretch_action.isChecked()
                self._decoder.set_viewport_size(area_size.width(), area_size.height())
                self._decoder_widget.setFixedSize(self._decoder.size())
                self._decoder_area.setFixedSize(area_size)
                self._sync_selection_overlay()
                return

            self._decoder.fullscreenmode = False
            self._decoder.fullscreenstretch = False
            self._decoder_widget.setFixedSize(self._decoder.size())
            self._decoder_area.setFixedSize(self._decoder_widget.size())
            self._sync_selection_overlay()
            if self.centralWidget() is not None:
                self.centralWidget().adjustSize()

        def _sync_selection_overlay(self):
            if self._selection_overlay is None:
                return
            font = self._selection_overlay.font()
            font.setPixelSize(self._decoder.zoom * 10)
            font.setStretch(int(round(self._decoder.horizontalscale * 100)))
            self._selection_overlay.setFont(font)
            self._selection_overlay.setFixedSize(self._decoder_widget.size())
            self._selection_overlay.setVisible(self._selection_overlay_enabled and self._navigator is not None)
            if self._selection_overlay.isVisible():
                self._selection_overlay.raise_()

        def _update_selection_overlay_text(self):
            if not self._selection_overlay_enabled:
                if self._selection_overlay is not None:
                    self._selection_overlay.clear()
                return
            overlay = self._ensure_selection_overlay()
            if self._navigator is None:
                overlay.clear()
                return
            subpage = self._navigator.current_subpage
            text = render_subpage_text(
                self._navigator.current_page_number,
                subpage,
                localcodepage=self._default_localcodepage(),
                doubleheight=not self._single_height_action.isChecked(),
                doublewidth=not self._single_width_action.isChecked(),
                flashenabled=not self._no_flash_action.isChecked(),
            )
            if overlay.toPlainText() != text:
                overlay.setPlainText(text)
                overlay.moveCursor(QtGui.QTextCursor.Start)

        def _reset_direct_page_buffer(self):
            self._direct_page_buffer.clear()
            self._direct_page_timer.stop()
            if self._navigator is not None:
                self._page_input.setText(self._navigator.current_page_label[1:])
            else:
                self._page_input.clear()

        def _restore_navigation_focus(self):
            self._page_input.clearFocus()
            self.centralWidget().setFocus(QtCore.Qt.OtherFocusReason)
            self.activateWindow()

        def _set_auto_scroll(self, enabled):
            if not enabled:
                self._auto_scroll_timer.stop()
            self._sync_auto_scroll()

        def _set_fullscreen_layout(self, layout_key, checked):
            if not checked:
                return
            if layout_key == 'stretch':
                self._fullscreen_stretch_action.setChecked(True)
            else:
                self._fullscreen_43_action.setChecked(True)
            self._sync_decoder_size()

        def _set_fullscreen(self, enabled):
            blocked = self._fullscreen_action.blockSignals(True)
            self._fullscreen_action.setChecked(enabled)
            self._fullscreen_action.blockSignals(blocked)
            if enabled:
                self._windowed_pos = self.pos()
                self._windowed_was_maximized = self.isMaximized()
                self._toolbar_widget.hide()
                self._nav_widget.hide()
                self._fastext_widget.hide()
                self.statusBar().hide()
                self._root_layout.setContentsMargins(0, 0, 0, 0)
                self._root_layout.setSpacing(0)
                self.showFullScreen()
                QtCore.QTimer.singleShot(0, self._sync_decoder_size)
            else:
                self.showNormal()
                self._toolbar_widget.show()
                self._nav_widget.show()
                self._fastext_widget.show()
                self.statusBar().show()
                self._root_layout.setContentsMargins(*self._normal_layout_margins)
                self._root_layout.setSpacing(self._normal_layout_spacing)

                def restore_window():
                    self._decoder.fullscreenmode = False
                    self._decoder.fullscreenstretch = False
                    QtWidgets.QApplication.processEvents()
                    windowed_pos = self._windowed_pos
                    windowed_was_maximized = self._windowed_was_maximized
                    self._windowed_pos = None
                    self._windowed_was_maximized = False
                    self._sync_decoder_size()
                    if windowed_was_maximized:
                        self.showMaximized()
                    else:
                        self._resize_window_to_content()
                    if windowed_pos is not None and not windowed_was_maximized:
                        self.move(windowed_pos)

                QtCore.QTimer.singleShot(0, restore_window)

        def _toggle_fullscreen_from_action(self, enabled):
            if self._fullscreen_button.isChecked() != enabled:
                self._fullscreen_button.setChecked(enabled)

        def _toggle_fullscreen_shortcut(self):
            if self._fullscreen_button.isEnabled() or self._fullscreen_button.isChecked():
                self._fullscreen_button.toggle()

        def _leave_fullscreen_shortcut(self):
            if self._fullscreen_button.isChecked():
                self._fullscreen_button.setChecked(False)

        def _set_stretch(self, enabled):
            self._decoder.zoom = self.stretch_zoom if enabled else self.base_zoom
            self._sync_decoder_size()
            self._resize_window_to_content()

        def _set_single_height(self, enabled):
            self._decoder.doubleheight = not enabled
            self._update_selection_overlay_text()
            self._sync_decoder_size()
            self._invalidate_overview_cache()

        def _set_single_width(self, enabled):
            self._decoder.doublewidth = not enabled
            self._update_selection_overlay_text()
            self._sync_decoder_size()
            self._invalidate_overview_cache()

        def _set_no_flash(self, enabled):
            self._decoder.flashenabled = not enabled
            self._update_selection_overlay_text()
            self._invalidate_overview_cache()

        def _set_highlight_text(self, enabled):
            self._decoder.highlighttext = enabled
            self._invalidate_overview_cache()

        def _set_selection_overlay(self, enabled):
            self._selection_overlay_enabled = bool(enabled)
            if not self._selection_overlay_enabled:
                if self._selection_overlay is not None:
                    self._selection_overlay.setVisible(False)
                    self._selection_overlay.clearFocus()
            else:
                self._update_selection_overlay_text()
                self._sync_selection_overlay()

        def _set_no_hex_pages(self, enabled):
            if self._navigator is None:
                return
            self._navigator.set_hex_pages_enabled(not enabled)
            self._direct_page_buffer.clear()
            self._render_current_subpage()
            if self._overview_dialog is not None and self._overview_dialog.isVisible():
                self.show_overview()

        def _set_language(self, language_key, checked):
            if not checked:
                return
            self._decoder.language = language_key
            self._update_selection_overlay_text()
            self._invalidate_overview_cache()
            if self._navigator is not None:
                self._render_current_subpage()

        def _set_crt_effect(self, enabled):
            self._decoder.crteffect = enabled

        def _invalidate_overview_cache(self):
            self._overview_dirty = True
            self._overview_signature = None
            if self._overview_dialog is not None:
                self._overview_dialog.clear_icon_cache()

        def _overview_state_signature(self):
            return (
                id(self._navigator),
                self._no_hex_pages_action.isChecked(),
                self._single_height_action.isChecked(),
                self._single_width_action.isChecked(),
                self._no_flash_action.isChecked(),
                self._decoder.language,
                self._highlight_text_action.isChecked(),
            )

        def _set_navigation_enabled(self, enabled):
            if not enabled:
                self._auto_scroll_timer.stop()
            for widget in (
                self._split_button,
                self._functions_button,
                self._settings_button,
                self._go_button,
                self._page_input,
                self._auto_toggle,
                self._stretch_toggle,
                self._crt_toggle,
                self._fullscreen_button,
                self._prev_page_button,
                self._next_page_button,
                self._prev_subpage_button,
                self._next_subpage_button,
            ):
                widget.setEnabled(enabled)
            for action in (
                self._screenshot_action,
                self._overview_action,
                self._info_action,
                self._fullscreen_action,
                self._split_export_action,
            ):
                action.setEnabled(enabled)
            for action in (
                self._single_height_action,
                self._single_width_action,
                self._no_flash_action,
                self._highlight_text_action,
                self._mouse_wheel_pages_action,
                self._auto_subpages_action,
                self._auto_pages_action,
                self._fullscreen_43_action,
                self._fullscreen_stretch_action,
                self._no_hex_pages_action,
                *self._language_actions.values(),
            ):
                action.setEnabled(enabled)
            for button in self._fastext_buttons:
                button.setEnabled(enabled)

        def open_dialog(self):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Open teletext capture',
                '',
                'Teletext Files (*.t42);;All Files (*)',
            )
            if filename:
                self.open_file(filename)

        def open_file(self, filename):
            if self._loader is not None and self._loader.isRunning():
                return

            self._filename = filename
            self._loading_started_at = time.monotonic()
            self._metadata_cache = None
            self._overview_dirty = True
            self._overview_signature = None
            if self._overview_dialog is not None:
                self._overview_dialog.hide()
            if self._info_dialog is not None:
                self._info_dialog.hide()
            if self._split_dialog is not None:
                self._split_dialog.hide()
            self._reset_direct_page_buffer()
            self._set_navigation_enabled(False)
            self.statusBar().showMessage(f'Loading {os.path.basename(filename)}...')

            self._loader = ServiceLoader(filename)
            self._loader.loaded.connect(self._service_loaded)
            self._loader.failed.connect(self._service_failed)
            self._loader.progress.connect(self._service_progress)
            self._loader.finished.connect(self._service_finished)
            self._loader.start()

        def _service_loaded(self, service):
            try:
                self._navigator = ServiceNavigator(service)
            except ValueError as exc:
                self._navigator = None
                self._clear_decoder()
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', str(exc))
                self.statusBar().showMessage(str(exc))
            else:
                self._metadata_cache = None
                self._overview_dirty = True
                self._overview_signature = None
                self._navigator.set_hex_pages_enabled(not self._no_hex_pages_action.isChecked())
                self._render_current_subpage()
                self._set_navigation_enabled(True)
                self.setWindowTitle(f'Teletext Viewer - {os.path.basename(self._filename)}')
                self.statusBar().showMessage(self._filename)

        def _service_failed(self, message):  # pragma: no cover - GUI error path
            self._navigator = None
            self._metadata_cache = None
            self._overview_dirty = True
            self._overview_signature = None
            self._clear_decoder()
            self._reset_direct_page_buffer()
            if self._overview_dialog is not None:
                self._overview_dialog.hide()
            if self._info_dialog is not None:
                self._info_dialog.hide()
            if self._split_dialog is not None:
                self._split_dialog.hide()
            QtWidgets.QMessageBox.critical(self, 'Teletext Viewer', message)
            self.statusBar().showMessage(message)

        def _service_finished(self):
            self._loading_started_at = None
            if self._loader is not None:
                self._loader.deleteLater()
                self._loader = None

        def _render_current_subpage(self):
            self._apply_decoder_preferences(self._decoder)
            self._paint_decoder(
                self._decoder,
                self._navigator.current_page_number,
                self._navigator.current_subpage_number,
            )
            self._update_selection_overlay_text()
            self._sync_decoder_size()

            current_subpage, total_subpages = self._navigator.current_subpage_position
            self._page_label.setText(f'Page: {self._navigator.current_page_label}')
            self._subpage_label.setText(
                f'Subpage: {current_subpage:02d}/{total_subpages:02d} ({self._navigator.current_subpage_number:04X})'
            )
            if not self._direct_page_buffer.text:
                self._page_input.setText(self._navigator.current_page_label[1:])

            for button, link in zip(self._fastext_buttons, self._navigator.fastext_links()):
                button.setText(link.label)
                button.setEnabled(link.enabled)
                button.setToolTip(f'Go to {link.label}')
            self._sync_auto_scroll()

        def show_overview(self):
            if self._navigator is None:
                return
            if self._overview_dialog is None:
                self._overview_dialog = PageOverviewDialog(self)
                self._overview_dialog.selectionRequested.connect(self._open_overview_selection)
                include_subpages = True
                include_hex_pages = not self._no_hex_pages_action.isChecked()
            else:
                include_subpages = self._overview_dialog.include_subpages
                include_hex_pages = self._overview_dialog.include_hex_pages and not self._no_hex_pages_action.isChecked()
            signature = self._overview_state_signature()
            if self._overview_dirty or self._overview_signature != signature:
                self._overview_dialog.populate(
                    self._navigator.overview_entries(
                        include_subpages=True,
                        include_hex_pages=not self._no_hex_pages_action.isChecked(),
                    ),
                    self._make_overview_icon,
                    include_subpages=include_subpages,
                    include_hex_pages=include_hex_pages,
                )
                self._overview_dirty = False
                self._overview_signature = signature

            self._overview_dialog.show()
            self._overview_dialog.raise_()
            self._overview_dialog.activateWindow()

        def _capture_directory(self):
            return os.path.dirname(self._filename) if self._filename else os.getcwd()

        def _t42_export_directory(self):
            return self._user_t42_directory or os.path.join(self._capture_directory(), 't42')

        def _html_export_directory(self):
            return self._user_html_directory or os.path.join(self._capture_directory(), 'html')

        def _set_t42_export_directory(self, path):
            self._user_t42_directory = path or None

        def _set_html_export_directory(self, path):
            self._user_html_directory = path or None

        def _default_localcodepage(self):
            language = self._current_language_key()
            return None if language == 'default' else language

        def _open_local_path(self, path):
            if not path or not os.path.exists(path):
                QtWidgets.QMessageBox.information(self, 'Teletext Viewer', f'Path does not exist yet:\n{path}')
                return False

            url = QtCore.QUrl.fromLocalFile(path)
            if QtGui.QDesktopServices.openUrl(url):
                return True

            try:
                if os.name == 'nt':  # pragma: no cover - platform-specific GUI path
                    os.startfile(path)
                elif sys.platform == 'darwin':  # pragma: no cover - platform-specific GUI path
                    subprocess.Popen(['open', path])
                else:  # pragma: no cover - platform-specific GUI path
                    subprocess.Popen(['xdg-open', path])
            except Exception as exc:  # pragma: no cover - GUI error path
                QtWidgets.QMessageBox.warning(
                    self,
                    'Teletext Viewer',
                    f'Could not open path:\n{path}\n\n{exc}',
                )
                return False
            return True

        def _choose_directory(self, title, start_path):
            return QtWidgets.QFileDialog.getExistingDirectory(
                self,
                title,
                start_path or self._capture_directory(),
            )

        def _ensure_html_preview_dialog(self):
            if self._html_preview_dialog is None:
                self._html_preview_dialog = HtmlPreviewDialog(self)
            return self._html_preview_dialog

        def _ensure_t42_browser_dialog(self):
            if self._t42_browser_dialog is None:
                self._t42_browser_dialog = FileBrowserDialog('T42 Files', '*.t42', self)
                self._t42_browser_dialog.fileRequested.connect(self.open_file)
            return self._t42_browser_dialog

        def _ensure_html_browser_dialog(self):
            if self._html_browser_dialog is None:
                self._html_browser_dialog = FileBrowserDialog('HTML Files', '*.html', self)
                self._html_browser_dialog.fileRequested.connect(self._open_html_preview)
            return self._html_browser_dialog

        def _show_browser_dialog(self, dialog, directory):
            dialog.set_directory(directory)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        def _open_html_preview(self, filename):
            if not filename or not os.path.exists(filename):
                QtWidgets.QMessageBox.information(self, 'Teletext Viewer', f'HTML file does not exist:\n{filename}')
                return
            self._set_html_export_directory(os.path.dirname(filename))
            dialog = self._ensure_html_preview_dialog()
            dialog.open_html(filename)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        def open_t42_folder(self):
            directory = self._t42_export_directory()
            if not directory or not os.path.exists(directory):
                chosen = self._choose_directory('Choose T42 folder', directory)
                if not chosen:
                    return
                directory = chosen
                self._set_t42_export_directory(directory)
            self._show_browser_dialog(self._ensure_t42_browser_dialog(), directory)

        def open_html_folder(self):
            directory = self._html_export_directory()
            if not directory or not os.path.exists(directory):
                chosen = self._choose_directory('Choose HTML folder', directory)
                if not chosen:
                    return
                directory = chosen
                self._set_html_export_directory(directory)
            self._show_browser_dialog(self._ensure_html_browser_dialog(), directory)

        def open_html_file(self):
            directory = self._html_export_directory()
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Open HTML file',
                directory,
                'HTML Files (*.html);;All Files (*)',
            )
            if filename:
                self._open_html_preview(filename)

        def _suggest_single_t42_path(self, page_number, subpage_number):
            basename = _page_label = f'{page_number >> 8}{page_number & 0xff:02x}'
            if subpage_number is None:
                filename = f'{basename}.t42'
            else:
                filename = f'{basename}-{subpage_number:04x}.t42'
            return os.path.join(self._t42_export_directory(), filename)

        def _suggest_single_html_path(self, page_number, subpage_number):
            basename = f'{page_number >> 8}{page_number & 0xff:02x}'
            if subpage_number is None:
                filename = f'{basename}.html'
            else:
                filename = f'{basename}-{subpage_number:04x}.html'
            return os.path.join(self._html_export_directory(), filename)

        def show_split_dialog(self):
            if self._navigator is None:
                return
            if self._split_dialog is None:
                self._split_dialog = SplitExportDialog(self)
                self._split_dialog.single_t42_button.clicked.connect(self._export_selected_t42_from_dialog)
                self._split_dialog.single_html_button.clicked.connect(self._export_selected_html_from_dialog)
                self._split_dialog.current_t42_button.clicked.connect(self._export_current_t42)
                self._split_dialog.current_html_button.clicked.connect(self._export_current_html)
                self._split_dialog.export_all_button.clicked.connect(self._export_all_from_dialog)
                self._split_dialog.set_html_localcodepage(self._default_localcodepage())
            self._split_dialog.set_current_selection(
                self._navigator.current_page_label[1:],
                self._navigator.current_subpage_number,
            )
            self._split_dialog.set_default_directories(
                self._t42_export_directory(),
                self._html_export_directory(),
            )
            self._split_dialog.show()
            self._split_dialog.raise_()
            self._split_dialog.activateWindow()

        def _dialog_page_selection(self):
            page_number = self._split_dialog.single_page_number()
            subpage_number = self._split_dialog.single_subpage_number()
            return page_number, subpage_number

        def _current_page_selection(self):
            return self._navigator.current_page_number, self._navigator.current_subpage_number

        def _export_selected_t42_from_dialog(self):
            try:
                page_number, subpage_number = self._dialog_page_selection()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', str(exc))
                return

            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save teletext page',
                self._suggest_single_t42_path(page_number, subpage_number),
                'Teletext Files (*.t42)',
            )
            if not filename:
                return
            if not filename.lower().endswith('.t42'):
                filename += '.t42'

            self._set_t42_export_directory(os.path.dirname(filename))
            export_selected_t42(
                self._navigator.service,
                filename,
                page_number,
                subpage_number=subpage_number,
            )
            self.statusBar().showMessage(f'Saved T42 to {filename}', 5000)

        def _export_selected_html_from_dialog(self):
            try:
                page_number, subpage_number = self._dialog_page_selection()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', str(exc))
                return

            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save teletext HTML',
                self._suggest_single_html_path(page_number, subpage_number),
                'HTML Files (*.html)',
            )
            if not filename:
                return
            if not filename.lower().endswith('.html'):
                filename += '.html'

            self._set_html_export_directory(os.path.dirname(filename))
            export_selected_html(
                self._navigator.service,
                filename,
                page_number,
                subpage_number=subpage_number,
                localcodepage=self._split_dialog.html_localcodepage(),
            )
            self.statusBar().showMessage(f'Saved HTML to {filename}', 5000)

        def _export_current_t42(self):
            page_number, subpage_number = self._current_page_selection()
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save current teletext view',
                self._suggest_single_t42_path(page_number, subpage_number),
                'Teletext Files (*.t42)',
            )
            if not filename:
                return
            if not filename.lower().endswith('.t42'):
                filename += '.t42'
            self._set_t42_export_directory(os.path.dirname(filename))
            export_selected_t42(
                self._navigator.service,
                filename,
                page_number,
                subpage_number=subpage_number,
            )
            self.statusBar().showMessage(f'Saved current T42 to {filename}', 5000)

        def _export_current_html(self):
            page_number, subpage_number = self._current_page_selection()
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save current teletext HTML',
                self._suggest_single_html_path(page_number, subpage_number),
                'HTML Files (*.html)',
            )
            if not filename:
                return
            if not filename.lower().endswith('.html'):
                filename += '.html'
            self._set_html_export_directory(os.path.dirname(filename))
            export_selected_html(
                self._navigator.service,
                filename,
                page_number,
                subpage_number=subpage_number,
                localcodepage=self._split_dialog.html_localcodepage() if self._split_dialog is not None else self._default_localcodepage(),
            )
            self.statusBar().showMessage(f'Saved current HTML to {filename}', 5000)

        def _export_all_from_dialog(self):
            if self._split_dialog is None:
                return
            if not self._split_dialog.t42_enabled() and not self._split_dialog.html_enabled():
                QtWidgets.QMessageBox.information(self, 'Teletext Viewer', 'Enable T42 or HTML export first.')
                return

            written = []
            total_steps = 0
            try:
                if self._split_dialog.t42_enabled():
                    t42_dir = self._split_dialog.t42_directory()
                    if not t42_dir:
                        raise ValueError('Choose a T42 export directory.')
                    self._set_t42_export_directory(t42_dir)
                    total_steps += count_split_t42_outputs(self._navigator.service)

                if self._split_dialog.html_enabled():
                    html_dir = self._split_dialog.html_directory()
                    if not html_dir:
                        raise ValueError('Choose an HTML export directory.')
                    self._set_html_export_directory(html_dir)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', str(exc))
                return

            total_steps += (
                count_html_outputs(
                    self._navigator.service,
                    include_subpages=self._split_dialog.html_include_subpages(),
                )
                if self._split_dialog.html_enabled()
                else 0
            )

            progress = QtWidgets.QProgressDialog('Exporting teletext...', None, 0, max(1, total_steps), self)
            progress.setWindowTitle('Split Export')
            progress.setCancelButton(None)
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            completed = 0

            def advance_progress(label, current, total):
                progress.setLabelText(f'{label}\n{current}/{total}')
                progress.setValue(current)
                QtWidgets.QApplication.processEvents()

            try:
                if self._split_dialog.t42_enabled():
                    t42_dir = self._split_dialog.t42_directory()
                    written.extend(export_split_t42(
                        self._navigator.service,
                        t42_dir,
                        pattern=self._split_dialog.split_pattern(),
                        progress_callback=lambda current, path: advance_progress(
                            f'Exporting T42: {path.name}',
                            completed + current,
                            total_steps,
                        ),
                    ))
                    completed = len(written)

                if self._split_dialog.html_enabled():
                    html_dir = self._split_dialog.html_directory()
                    written.extend(export_html(
                        self._navigator.service,
                        html_dir,
                        include_subpages=self._split_dialog.html_include_subpages(),
                        localcodepage=self._split_dialog.html_localcodepage(),
                        progress_callback=lambda current, path: advance_progress(
                            f'Exporting HTML: {path.name}',
                            completed + current,
                            total_steps,
                        ),
                    ))
            except Exception as exc:
                progress.close()
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', str(exc))
                return

            progress.setValue(total_steps)
            progress.close()

            self.statusBar().showMessage(f'Exported {len(written)} files.', 5000)

        def _format_file_size(self, size):
            if size is None:
                return 'unavailable'
            units = ('B', 'KB', 'MB', 'GB')
            value = float(size)
            unit = units[0]
            for unit in units:
                if value < 1024 or unit == units[-1]:
                    break
                value /= 1024.0
            if unit == 'B':
                return f'{int(value)} {unit}'
            return f'{value:.2f} {unit}'

        def _format_duration(self, seconds):
            seconds = max(0, int(seconds))
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours:
                return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
            return f'{minutes:02d}:{seconds:02d}'

        def _service_progress(self, current, total, elapsed):
            if total <= 0:
                self.statusBar().showMessage(f'Loading {os.path.basename(self._filename)}...')
                return
            percent = int((current * 100) / total)
            remaining = ((total - current) * elapsed / current) if current else 0
            self.statusBar().showMessage(
                f'Loading teletext {percent}% | {current}/{total} [{self._format_duration(elapsed)}<{self._format_duration(remaining)}]'
            )

        def _format_metadata_report(self, metadata):
            def display(value, missing='unavailable'):
                return value if value not in (None, '', ()) else missing

            magazines = ', '.join(f'M{magazine}={count}' for magazine, count in metadata.magazine_counts) or 'unavailable'
            codepages = ', '.join(str(codepage) for codepage in metadata.codepages) or 'unavailable'

            lines = [
                'Extracted',
                f'File: {display(metadata.file_name)}',
                f'Path: {display(metadata.file_path)}',
                f'Size: {self._format_file_size(metadata.file_size)}',
                f'Modified: {display(metadata.modified_at)}',
                f'Pages: {metadata.page_count}',
                f'Subpages: {metadata.subpage_count}',
                f'Magazines: {magazines}',
                f'Codepages: {codepages}',
                f'Broadcast 8/30: {"present" if metadata.broadcast_present else "missing"}',
                f'Initial Page: {display(metadata.initial_page)}',
                f'Broadcast Label: {display(metadata.broadcast_label)}',
                f'Broadcast Network: {display(metadata.broadcast_network)}',
                f'Broadcast Country: {display(metadata.broadcast_country)}',
                f'Broadcast Date: {display(metadata.broadcast_date)}',
                f'Broadcast Time: {display(metadata.broadcast_time)}',
                '',
                'Inferred',
                f'Likely Broadcaster: {display(metadata.likely_broadcaster)}',
                f'Likely Language: {display(metadata.likely_language)}',
                f'Likely Country: {display(metadata.likely_country)}',
                f'Confidence: {display(metadata.confidence)}',
                '',
                'Evidence',
            ]
            if metadata.evidence:
                lines.extend(f'- {item}' for item in metadata.evidence)
            else:
                lines.append('- unavailable')

            lines.append('')
            lines.append('Sample Titles')
            if metadata.sample_titles:
                lines.extend(f'- {page_label}: {title}' for page_label, title in metadata.sample_titles)
            else:
                lines.append('- unavailable')

            return '\n'.join(lines)

        def show_info(self):
            if self._navigator is None:
                return
            if self._info_dialog is None:
                self._info_dialog = ServiceInfoDialog(self)
            if self._metadata_cache is None:
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                try:
                    self._metadata_cache = self._navigator.metadata(self._filename)
                except Exception as exc:  # pragma: no cover - GUI error path
                    QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', f'Could not read teletext info: {exc}')
                    return
                finally:
                    QtWidgets.QApplication.restoreOverrideCursor()
            self._info_dialog.set_report(self._format_metadata_report(self._metadata_cache))
            self._info_dialog.show()
            self._info_dialog.raise_()
            self._info_dialog.activateWindow()

        def _open_overview_selection(self, page_number, subpage_number):
            if self._navigator is None:
                return
            if self._navigator.go_to_page(page_number, subpage_number):
                self._direct_page_buffer.clear()
                self._render_current_subpage()
                self._restore_navigation_focus()
                self.statusBar().showMessage(
                    f'Opened {self._navigator.current_page_label} / {self._navigator.current_subpage_number:04X}.',
                    3000,
                )

        def _suggest_screenshot_path(self):
            directory = os.path.dirname(self._filename) if self._filename else os.getcwd()
            base_name = os.path.splitext(os.path.basename(self._filename or 'teletext'))[0]
            if self._navigator is None:
                return os.path.join(directory, f'{base_name}.png')
            return os.path.join(
                directory,
                f'{base_name}-{self._navigator.current_page_label}-{self._navigator.current_subpage_number:04X}.png',
            )

        def save_screenshot(self):
            if self._navigator is None:
                return

            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save teletext screenshot',
                self._suggest_screenshot_path(),
                'PNG Image (*.png)',
            )
            if not filename:
                return
            if not filename.lower().endswith('.png'):
                filename += '.png'

            if self._decoder_widget.grab().save(filename, 'PNG'):
                self.statusBar().showMessage(f'Screenshot saved to {filename}', 5000)
            else:  # pragma: no cover - GUI error path
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', f'Could not save screenshot to {filename}.')

        def _sync_auto_scroll(self):
            if (
                self._navigator is not None
                and self._auto_toggle.isChecked()
                and self._navigator.can_auto_advance(
                    subpages_enabled=self._auto_subpages_action.isChecked(),
                    pages_enabled=self._auto_pages_action.isChecked(),
                )
            ):
                self._auto_scroll_timer.start()
            else:
                self._auto_scroll_timer.stop()

        def _auto_advance_subpage(self):
            if self._navigator is None:
                self._auto_scroll_timer.stop()
                return
            movement = self._navigator.auto_advance(
                subpages_enabled=self._auto_subpages_action.isChecked(),
                pages_enabled=self._auto_pages_action.isChecked(),
            )
            if movement is None:
                self._auto_scroll_timer.stop()
                return
            self._direct_page_buffer.clear()
            self._render_current_subpage()

        def go_to_page_text(self):
            if self._navigator is None:
                return False
            self._direct_page_timer.stop()
            try:
                success = self._navigator.go_to_page_text(self._page_input.text())
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Teletext Viewer', str(exc))
                self._reset_direct_page_buffer()
                self._restore_navigation_focus()
                return False
            if not success:
                QtWidgets.QMessageBox.information(
                    self,
                    'Teletext Viewer',
                    f'Page {self._page_input.text().strip().upper()} is not present in this file.',
                )
                self._reset_direct_page_buffer()
                self._restore_navigation_focus()
                return False
            self._direct_page_buffer.clear()
            self._render_current_subpage()
            self._restore_navigation_focus()
            return True

        def prev_page(self):
            if self._navigator is None:
                return
            self._navigator.go_prev_page()
            self._direct_page_buffer.clear()
            self._render_current_subpage()

        def next_page(self):
            if self._navigator is None:
                return
            self._navigator.go_next_page()
            self._direct_page_buffer.clear()
            self._render_current_subpage()

        def prev_subpage(self):
            if self._navigator is None:
                return
            self._navigator.go_prev_subpage()
            self._direct_page_buffer.clear()
            self._render_current_subpage()

        def next_subpage(self):
            if self._navigator is None:
                return
            self._navigator.go_next_subpage()
            self._direct_page_buffer.clear()
            self._render_current_subpage()

        def go_to_fastext(self, index):
            if self._navigator is None:
                return
            if self._navigator.go_to_fastext(index):
                self._direct_page_buffer.clear()
                self._render_current_subpage()

        def dragEnterEvent(self, event):  # pragma: no cover - GUI event path
            if event.mimeData().hasUrls():
                event.acceptProposedAction()

        def dropEvent(self, event):  # pragma: no cover - GUI event path
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    self.open_file(url.toLocalFile())
                    event.acceptProposedAction()
                    return

        def eventFilter(self, watched, event):  # pragma: no cover - GUI event path
            decoder_area = getattr(self, '_decoder_area', None)
            decoder_widget = getattr(self, '_decoder_widget', None)
            selection_overlay = getattr(self, '_selection_overlay', None)
            page_input = getattr(self, '_page_input', None)
            navigator = getattr(self, '_navigator', None)
            mouse_wheel_action = getattr(self, '_mouse_wheel_pages_action', None)
            if (
                watched in (decoder_area, decoder_widget, selection_overlay)
                and event.type() == QtCore.QEvent.Wheel
                and navigator is not None
                and mouse_wheel_action is not None
                and mouse_wheel_action.isChecked()
            ):
                delta = event.angleDelta().y()
                if delta > 0:
                    self.next_page()
                    event.accept()
                    return True
                if delta < 0:
                    self.prev_page()
                    event.accept()
                    return True
            if (
                watched is page_input
                and navigator is not None
                and event.type() in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress)
            ):
                key = event.key()
                if event.type() == QtCore.QEvent.ShortcutOverride:
                    if key in (
                        QtCore.Qt.Key_Up,
                        QtCore.Qt.Key_Down,
                        QtCore.Qt.Key_Left,
                        QtCore.Qt.Key_Right,
                    ):
                        event.accept()
                    return False
                if key == QtCore.Qt.Key_Up:
                    self.next_page()
                    return True
                if key == QtCore.Qt.Key_Down:
                    self.prev_page()
                    return True
                if key == QtCore.Qt.Key_Left:
                    self.prev_subpage()
                    return True
                if key == QtCore.Qt.Key_Right:
                    self.next_subpage()
                    return True
            return super().eventFilter(watched, event)

        def resizeEvent(self, event):  # pragma: no cover - GUI event path
            super().resizeEvent(event)
            if self._fullscreen_button.isChecked():
                self._sync_decoder_size()

        def keyPressEvent(self, event):  # pragma: no cover - GUI event path
            if self._navigator is not None and self.focusWidget() is not self._page_input:
                if event.key() == QtCore.Qt.Key_Backspace and self._direct_page_buffer.backspace():
                    self._page_input.setText(self._direct_page_buffer.text or self._navigator.current_page_label[1:])
                    self._direct_page_timer.start()
                    event.accept()
                    return

                text = event.text().upper()
                if self._direct_page_buffer.push(text):
                    self._page_input.setText(self._direct_page_buffer.text)
                    self._direct_page_timer.start()
                    if self._direct_page_buffer.complete:
                        if self.go_to_page_text():
                            self.statusBar().showMessage(
                                f'Page {self._navigator.current_page_label} selected from keyboard.',
                                3000,
                            )
                        else:
                            self._direct_page_buffer.clear()
                    event.accept()
                    return

            super().keyPressEvent(event)


def main(argv=None):
    if IMPORT_ERROR is not None:
        print(f'PyQt5 is not installed. Qt teletext viewer not available. ({IMPORT_ERROR})')
        return 1

    argv = list(sys.argv if argv is None else argv)
    app = QtWidgets.QApplication(argv)
    filename = argv[1] if len(argv) > 1 else None
    window = TeletextViewerWindow(filename=filename)
    if window.windowIcon().isNull() and os.path.exists(window._icon_path):
        app.setWindowIcon(QtGui.QIcon(window._icon_path))
    else:
        app.setWindowIcon(window.windowIcon())
    window.show()
    return app.exec_()


if __name__ == '__main__':  # pragma: no cover - GUI entrypoint
    raise SystemExit(main())
