import os
import sys

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
    from teletext.service import Service
    from teletext.viewer import DirectPageBuffer, ServiceNavigator


if QtCore is not None:
    class ServiceLoader(QtCore.QThread):
        loaded = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)

        def __init__(self, filename):
            super().__init__()
            self._filename = filename

        def run(self):
            try:
                with open(self._filename, 'rb') as handle:
                    service = Service.from_file(handle)
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
            self._metadata_cache = None
            self._overview_dirty = True
            self._overview_signature = None
            self._preview_widget = None
            self._preview_decoder = None
            self._windowed_pos = None
            self._windowed_was_maximized = False
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

            self._open_button = QtWidgets.QPushButton('Open .t42')
            self._open_button.clicked.connect(self.open_dialog)
            toolbar.addWidget(self._open_button)

            self._screenshot_button = QtWidgets.QPushButton('Screenshot')
            self._screenshot_button.clicked.connect(self.save_screenshot)
            toolbar.addWidget(self._screenshot_button)

            self._overview_button = QtWidgets.QPushButton('Overview')
            self._overview_button.clicked.connect(self.show_overview)
            toolbar.addWidget(self._overview_button)

            self._info_button = QtWidgets.QPushButton('Info')
            self._info_button.clicked.connect(self.show_info)
            toolbar.addWidget(self._info_button)

            self._fullscreen_button = QtWidgets.QPushButton('Fullscreen')
            self._fullscreen_button.setCheckable(True)
            self._fullscreen_button.toggled.connect(self._set_fullscreen)
            toolbar.addWidget(self._fullscreen_button)

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
            decoder_layout = QtWidgets.QVBoxLayout(self._decoder_area)
            decoder_layout.setContentsMargins(0, 0, 0, 0)
            decoder_layout.addWidget(self._decoder_widget, 0, QtCore.Qt.AlignCenter)
            root.addWidget(self._decoder_area, 0, QtCore.Qt.AlignCenter)
            self._decoder.doubleheight = False
            self._decoder.doublewidth = False
            self._decoder.flashenabled = False
            for action in (
                self._single_height_action,
                self._single_width_action,
                self._no_flash_action,
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
            header[3:7] = np.fromstring(f'P{magazine}{page:02X}', dtype=np.uint8)
            header[8:] = subpage.header.displayable[:]
            return header

        def _apply_decoder_preferences(self, decoder, preview=False):
            decoder.doubleheight = not self._single_height_action.isChecked()
            decoder.doublewidth = not self._single_width_action.isChecked()
            decoder.flashenabled = not self._no_flash_action.isChecked()
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
                return

            self._decoder.fullscreenmode = False
            self._decoder.fullscreenstretch = False
            self._decoder_widget.setFixedSize(self._decoder.size())
            self._decoder_area.setFixedSize(self._decoder_widget.size())
            if self.centralWidget() is not None:
                self.centralWidget().adjustSize()

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
            self._sync_decoder_size()
            self._invalidate_overview_cache()

        def _set_single_width(self, enabled):
            self._decoder.doublewidth = not enabled
            self._sync_decoder_size()
            self._invalidate_overview_cache()

        def _set_no_flash(self, enabled):
            self._decoder.flashenabled = not enabled
            self._invalidate_overview_cache()

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
            )

        def _set_navigation_enabled(self, enabled):
            if not enabled:
                self._auto_scroll_timer.stop()
            for widget in (
                self._screenshot_button,
                self._overview_button,
                self._info_button,
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
                self._single_height_action,
                self._single_width_action,
                self._no_flash_action,
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
            self._metadata_cache = None
            self._overview_dirty = True
            self._overview_signature = None
            if self._overview_dialog is not None:
                self._overview_dialog.hide()
            if self._info_dialog is not None:
                self._info_dialog.hide()
            self._reset_direct_page_buffer()
            self._set_navigation_enabled(False)
            self.statusBar().showMessage(f'Loading {os.path.basename(filename)}...')

            self._loader = ServiceLoader(filename)
            self._loader.loaded.connect(self._service_loaded)
            self._loader.failed.connect(self._service_failed)
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
            QtWidgets.QMessageBox.critical(self, 'Teletext Viewer', message)
            self.statusBar().showMessage(message)

        def _service_finished(self):
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
            if (
                watched is self._page_input
                and self._navigator is not None
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
