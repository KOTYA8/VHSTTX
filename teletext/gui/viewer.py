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
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            toolbar = QtWidgets.QHBoxLayout()
            toolbar.setSpacing(8)

            self._open_button = QtWidgets.QPushButton('Open .t42')
            self._open_button.clicked.connect(self.open_dialog)
            toolbar.addWidget(self._open_button)

            self._screenshot_button = QtWidgets.QPushButton('Screenshot')
            self._screenshot_button.clicked.connect(self.save_screenshot)
            toolbar.addWidget(self._screenshot_button)

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
            self._no_hex_pages_action = self._settings_menu.addAction('No Hex Pages')
            self._no_hex_pages_action.setCheckable(True)
            self._no_hex_pages_action.toggled.connect(self._set_no_hex_pages)
            self._settings_button.setMenu(self._settings_menu)
            toolbar.addWidget(self._settings_button)

            toolbar.addWidget(QtWidgets.QLabel('Page'))
            self._page_input = QtWidgets.QLineEdit()
            self._page_input.setMaxLength(4)
            self._page_input.setFixedWidth(80)
            self._page_input.setPlaceholderText('100')
            self._page_input.returnPressed.connect(self.go_to_page_text)
            toolbar.addWidget(self._page_input)

            self._go_button = QtWidgets.QPushButton('Go')
            self._go_button.clicked.connect(self.go_to_page_text)
            toolbar.addWidget(self._go_button)

            self._auto_toggle = QtWidgets.QCheckBox('Auto')
            self._auto_toggle.toggled.connect(self._set_auto_scroll)
            self._auto_toggle.setChecked(True)
            toolbar.addWidget(self._auto_toggle)

            self._stretch_toggle = QtWidgets.QCheckBox('Stretch')
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

            root.addLayout(toolbar)

            self._decoder_widget = QtQuickWidgets.QQuickWidget()
            self._decoder_widget.setResizeMode(QtQuickWidgets.QQuickWidget.SizeViewToRootObject)
            self._decoder_widget.setClearColor(QtGui.QColor('black'))
            self._decoder_widget.setFocusPolicy(QtCore.Qt.NoFocus)
            self._decoder = Decoder(self._decoder_widget, font_family=self._font_family)
            self._decoder.zoom = self.base_zoom
            self._decoder_widget.setFixedSize(self._decoder.size())
            self._single_height_action.setChecked(True)
            self._single_width_action.setChecked(True)
            self._no_flash_action.setChecked(True)
            root.addWidget(self._decoder_widget, 0, QtCore.Qt.AlignCenter)

            nav = QtWidgets.QHBoxLayout()
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

            root.addLayout(nav)

            fastext = QtWidgets.QHBoxLayout()
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
            root.addLayout(fastext)

            self.setCentralWidget(central)
            self.statusBar().showMessage('Open a .t42 file to start.')

        def _build_shortcuts(self):
            QtWidgets.QShortcut(QtGui.QKeySequence('Up'), self, activated=self.next_page)
            QtWidgets.QShortcut(QtGui.QKeySequence('Down'), self, activated=self.prev_page)
            QtWidgets.QShortcut(QtGui.QKeySequence('Left'), self, activated=self.prev_subpage)
            QtWidgets.QShortcut(QtGui.QKeySequence('Right'), self, activated=self.next_subpage)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+O'), self, activated=self.open_dialog)
            QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+S'), self, activated=self.save_screenshot)

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

        def _clear_decoder(self):
            self._decoder[:] = np.full((25, 40), fill_value=0x20, dtype=np.uint8)

        def _sync_decoder_size(self):
            self._decoder_widget.setFixedSize(self._decoder.size())
            if self.centralWidget() is not None:
                self.centralWidget().adjustSize()
            self.adjustSize()
            self.resize(self.sizeHint())

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

        def _set_stretch(self, enabled):
            self._decoder.zoom = self.stretch_zoom if enabled else self.base_zoom
            self._sync_decoder_size()

        def _set_single_height(self, enabled):
            self._decoder.doubleheight = not enabled
            self._sync_decoder_size()

        def _set_single_width(self, enabled):
            self._decoder.doublewidth = not enabled
            self._sync_decoder_size()

        def _set_no_flash(self, enabled):
            self._decoder.flashenabled = not enabled

        def _set_no_hex_pages(self, enabled):
            if self._navigator is None:
                return
            self._navigator.set_hex_pages_enabled(not enabled)
            self._direct_page_buffer.clear()
            self._render_current_subpage()

        def _set_crt_effect(self, enabled):
            self._decoder.crteffect = enabled

        def _set_navigation_enabled(self, enabled):
            if not enabled:
                self._auto_scroll_timer.stop()
            for widget in (
                self._screenshot_button,
                self._settings_button,
                self._go_button,
                self._page_input,
                self._auto_toggle,
                self._stretch_toggle,
                self._crt_toggle,
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
                self._no_hex_pages_action,
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
                self._navigator.set_hex_pages_enabled(not self._no_hex_pages_action.isChecked())
                self._render_current_subpage()
                self._set_navigation_enabled(True)
                self.setWindowTitle(f'Teletext Viewer - {os.path.basename(self._filename)}')
                self.statusBar().showMessage(self._filename)

        def _service_failed(self, message):  # pragma: no cover - GUI error path
            self._navigator = None
            self._clear_decoder()
            self._reset_direct_page_buffer()
            QtWidgets.QMessageBox.critical(self, 'Teletext Viewer', message)
            self.statusBar().showMessage(message)

        def _service_finished(self):
            if self._loader is not None:
                self._loader.deleteLater()
                self._loader = None

        def _render_current_subpage(self):
            subpage = self._navigator.current_subpage
            header = np.full((40,), fill_value=0x20, dtype=np.uint8)
            magazine, page = self._navigator.split_page_number(self._navigator.current_page_number)
            header[3:7] = np.fromstring(f'P{magazine}{page:02X}', dtype=np.uint8)
            header[8:] = subpage.header.displayable[:]
            self._decoder[0] = header
            self._decoder[1:] = subpage.displayable[:]
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
                and self._navigator.current_subpage_count > 1
            ):
                self._auto_scroll_timer.start()
            else:
                self._auto_scroll_timer.stop()

        def _auto_advance_subpage(self):
            if self._navigator is None or self._navigator.current_subpage_count <= 1:
                self._auto_scroll_timer.stop()
                return
            self._navigator.go_next_subpage()
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
