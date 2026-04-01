from __future__ import annotations

import bisect
import os
import pathlib
from dataclasses import dataclass

from teletext.file import FileChunker
from teletext.packet import Packet

from . import vbicrop as _vbicrop


IMPORT_ERROR = _vbicrop.IMPORT_ERROR
QtCore = _vbicrop.QtCore
QtGui = _vbicrop.QtGui
QtWidgets = _vbicrop.QtWidgets
FrameRangeSlider = getattr(_vbicrop, 'FrameRangeSlider', None)

_ensure_app = _vbicrop._ensure_app
_clamp = _vbicrop._clamp
normalise_cut_ranges = _vbicrop.normalise_cut_ranges
count_cut_frames = _vbicrop.count_cut_frames
selection_end_targets = _vbicrop.selection_end_targets

PACKET_SIZE = 42


@dataclass(frozen=True)
class T42PacketEntry:
    packet_index: int
    raw: bytes
    magazine: int | None
    row: int | None
    page_number: int | None
    subpage_number: int | None
    header_text: str | None


@dataclass(frozen=True)
class T42Insertion:
    after_packet: int
    path: str
    packet_count: int
    entries: tuple[T42PacketEntry, ...]


@dataclass(frozen=True)
class T42HeaderPreview:
    packet_index: int
    page_number: int
    subpage_number: int
    text: str


def _header_title_from_text(header_text):
    if not header_text:
        return ''
    parts = str(header_text).strip().split(maxsplit=2)
    if len(parts) >= 3:
        return parts[2]
    return str(header_text).strip()


def _compose_page_number(magazine, page):
    return (int(magazine) << 8) | int(page)


def _page_label(page_number):
    magazine = int(page_number) >> 8
    page = int(page_number) & 0xFF
    return f'P{magazine}{page:02X}'


def _sanitise_ascii(data):
    values = data if isinstance(data, bytes) else bytes(data)
    chars = []
    for value in values:
        chars.append(chr(value) if 32 <= int(value) <= 126 else ' ')
    return ''.join(chars).strip()


def load_t42_entries(path):
    current_page = {}
    entries = []

    with open(path, 'rb') as handle:
        for packet_index, data in FileChunker(handle, PACKET_SIZE):
            raw = bytes(data)
            packet = Packet(raw, packet_index)
            magazine = int(packet.mrag.magazine)
            row = int(packet.mrag.row)
            page_number = None
            subpage_number = None
            header_text = None

            if packet.type == 'header':
                page_number = _compose_page_number(magazine, int(packet.header.page))
                subpage_number = int(packet.header.subpage)
                current_page[magazine] = (page_number, subpage_number)
                title = _sanitise_ascii(packet.to_bytes_no_parity())
                header_text = f'{packet_index:7d} {_page_label(page_number)}:{subpage_number:04X} {title}'.rstrip()
            else:
                page_number, subpage_number = current_page.get(magazine, (None, None))

            entries.append(T42PacketEntry(
                packet_index=int(packet_index),
                raw=raw,
                magazine=magazine,
                row=row,
                page_number=page_number,
                subpage_number=subpage_number,
                header_text=header_text,
            ))

    return tuple(entries)


def normalise_t42_insertions(insertions, total_packets):
    total_packets = max(int(total_packets), 1)
    maximum = total_packets - 1
    normalised = []
    for insertion in insertions:
        normalised.append(T42Insertion(
            after_packet=_clamp(insertion.after_packet, 0, maximum),
            path=insertion.path,
            packet_count=max(int(insertion.packet_count), 0),
            entries=tuple(insertion.entries),
        ))
    return tuple(sorted(normalised, key=lambda item: (item.after_packet, item.path.lower())))


def count_inserted_packets(insertions):
    return sum(int(insertion.packet_count) for insertion in insertions)


def iterate_t42_entries(base_entries, cut_ranges=(), insertions=()):
    cut_ranges = tuple(sorted(cut_ranges))
    insertions = tuple(sorted(insertions, key=lambda item: (int(item.after_packet), item.path.lower())))
    cut_index = 0
    insertion_index = 0

    def emit_insertions(after_packet):
        nonlocal insertion_index
        while insertion_index < len(insertions) and int(insertions[insertion_index].after_packet) == after_packet:
            yield from insertions[insertion_index].entries
            insertion_index += 1

    for entry in base_entries:
        packet_index = int(entry.packet_index)
        while cut_index < len(cut_ranges) and packet_index > int(cut_ranges[cut_index][1]):
            cut_index += 1
        cut_packet = False
        if cut_index < len(cut_ranges):
            cut_start, cut_end = cut_ranges[cut_index]
            cut_packet = int(cut_start) <= packet_index <= int(cut_end)
        if not cut_packet:
            yield entry
        yield from emit_insertions(packet_index)

    while insertion_index < len(insertions):
        yield from insertions[insertion_index].entries
        insertion_index += 1


def filter_deleted_t42_entries(entries, deleted_pages=(), deleted_subpages=()):
    deleted_pages = frozenset(int(page_number) for page_number in deleted_pages)
    deleted_subpages = frozenset((int(page_number), int(subpage_number)) for page_number, subpage_number in deleted_subpages)
    for entry in entries:
        if entry.page_number is not None:
            if entry.page_number in deleted_pages:
                continue
            if entry.subpage_number is not None and (entry.page_number, entry.subpage_number) in deleted_subpages:
                continue
        yield entry


def edited_t42_entries(base_entries, cut_ranges=(), insertions=(), deleted_pages=(), deleted_subpages=()):
    combined = iterate_t42_entries(base_entries, cut_ranges=cut_ranges, insertions=insertions)
    return tuple(filter_deleted_t42_entries(
        combined,
        deleted_pages=deleted_pages,
        deleted_subpages=deleted_subpages,
    ))


def collect_t42_headers(entries):
    headers = []
    for entry in entries:
        if entry.header_text and entry.page_number is not None and entry.subpage_number is not None:
            headers.append(T42HeaderPreview(
                packet_index=int(entry.packet_index),
                page_number=int(entry.page_number),
                subpage_number=int(entry.subpage_number),
                text=str(entry.header_text),
            ))
    return tuple(headers)


def header_preview_text(entries, headers, current_packet, radius=4):
    if not entries:
        return 'No packets loaded.'

    current_packet = _clamp(current_packet, 0, len(entries) - 1)
    current_entry = entries[current_packet]
    lines = [f'Current packet: {current_packet + 1}/{len(entries)}']

    if current_entry.page_number is not None:
        lines.append(
            f'Current page: {_page_label(current_entry.page_number)}'
            + (f' / {current_entry.subpage_number:04X}' if current_entry.subpage_number is not None else '')
        )
    else:
        lines.append('Current page: unknown')

    if current_entry.row is not None:
        lines.append(f'Current row: {current_entry.row}')
    lines.append('')
    lines.append('Row 0 preview (-r 0):')

    if not headers:
        lines.append('No row 0 packets found.')
        return '\n'.join(lines)

    header_positions = [header.packet_index for header in headers]
    pivot = bisect.bisect_left(header_positions, current_packet)
    start = max(0, pivot - radius)
    end = min(len(headers), pivot + radius + 1)
    for header in headers[start:end]:
        marker = '>' if header.packet_index <= current_packet < (header.packet_index + 1) else ' '
        lines.append(f'{marker} {header.text}')
    return '\n'.join(lines)


def summarise_t42_pages(entries):
    pages = {}
    for edited_index, entry in enumerate(entries):
        if entry.page_number is None:
            continue
        page_info = pages.setdefault(entry.page_number, {
            'packet_count': 0,
            'first_packet': edited_index,
            'subpages': {},
            'header_title': _header_title_from_text(entry.header_text) if entry.header_text else '',
        })
        page_info['packet_count'] += 1
        page_info['first_packet'] = min(page_info['first_packet'], edited_index)
        if entry.subpage_number is not None:
            subpage_info = page_info['subpages'].setdefault(entry.subpage_number, {
                'packet_count': 0,
                'first_packet': edited_index,
                'header_title': _header_title_from_text(entry.header_text) if entry.header_text else '',
            })
            subpage_info['packet_count'] += 1
            subpage_info['first_packet'] = min(subpage_info['first_packet'], edited_index)
            if entry.header_text and not subpage_info['header_title']:
                subpage_info['header_title'] = _header_title_from_text(entry.header_text)
        if entry.header_text and not page_info['header_title']:
            page_info['header_title'] = _header_title_from_text(entry.header_text)

    result = []
    for page_number in sorted(pages):
        page_info = pages[page_number]
        subpages = tuple(
            {
                'subpage_number': subpage_number,
                'packet_count': data['packet_count'],
                'first_packet': data['first_packet'],
                'header_title': data['header_title'],
            }
            for subpage_number, data in sorted(page_info['subpages'].items())
        )
        result.append({
            'page_number': page_number,
            'packet_count': page_info['packet_count'],
            'first_packet': page_info['first_packet'],
            'header_title': page_info['header_title'],
            'subpages': subpages,
        })
    return tuple(result)


def packet_count_to_megabytes(packet_count):
    return (max(int(packet_count), 0) * PACKET_SIZE) / (1024 * 1024)


def write_t42_entries(entries, output_path):
    with open(output_path, 'wb') as handle:
        for entry in entries:
            handle.write(entry.raw)


if IMPORT_ERROR is None:
    class T42CropWindow(QtWidgets.QDialog):
        def __init__(self, input_path, entries, save_callback=None, parent=None):
            super().__init__(parent)
            self._input_path = input_path
            self._entries = tuple(entries)
            self._headers = collect_t42_headers(self._entries)
            self._total_packets = max(len(self._entries), 1)
            self._current_packet = 0
            self._selection_start = 0
            self._selection_end = max(self._total_packets - 1, 0)
            self._cut_ranges = ()
            self._insertions = ()
            self._deleted_pages = frozenset()
            self._deleted_subpages = frozenset()
            self._save_callback = save_callback
            self._updating = False
            self._history = []
            self._redo_history = []
            self._cache_dirty = True
            self._cached_combined_entries = ()
            self._cached_edited_entries = ()
            self._cached_deleted_packet_count = 0

            self.setWindowTitle(f'T42 Crop - {os.path.basename(input_path)}')
            self.resize(1024, 760)
            self.setMinimumSize(860, 620)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            self._status_label = QtWidgets.QLabel('')
            root.addWidget(self._status_label)

            timeline_group = QtWidgets.QGroupBox('Current Packet')
            timeline_layout = QtWidgets.QGridLayout(timeline_group)
            root.addWidget(timeline_group)

            self._packet_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self._packet_slider.setRange(0, self._total_packets - 1)
            self._packet_slider.valueChanged.connect(self._packet_slider_changed)
            timeline_layout.addWidget(self._packet_slider, 0, 0, 1, 4)

            timeline_layout.addWidget(QtWidgets.QLabel('Packet'), 1, 0)
            self._packet_box = QtWidgets.QSpinBox()
            self._packet_box.setRange(0, self._total_packets - 1)
            self._packet_box.valueChanged.connect(self._packet_box_changed)
            timeline_layout.addWidget(self._packet_box, 1, 1)

            timeline_layout.addWidget(QtWidgets.QLabel('Page'), 1, 2)
            self._packet_page_label = QtWidgets.QLabel('unknown')
            timeline_layout.addWidget(self._packet_page_label, 1, 3)

            controls_layout = QtWidgets.QHBoxLayout()
            root.addLayout(controls_layout)
            self._home_button = QtWidgets.QPushButton('|<')
            self._home_button.clicked.connect(self._jump_start)
            controls_layout.addWidget(self._home_button)
            self._prev_button = QtWidgets.QPushButton('<')
            self._prev_button.clicked.connect(lambda: self._step(-1))
            controls_layout.addWidget(self._prev_button)
            self._next_button = QtWidgets.QPushButton('>')
            self._next_button.clicked.connect(lambda: self._step(1))
            controls_layout.addWidget(self._next_button)
            self._end_button = QtWidgets.QPushButton('>|')
            self._end_button.clicked.connect(self._jump_end)
            controls_layout.addWidget(self._end_button)
            controls_layout.addStretch(1)

            selection_group = QtWidgets.QGroupBox('Selection')
            selection_layout = QtWidgets.QGridLayout(selection_group)
            root.addWidget(selection_group)

            self._range_slider = FrameRangeSlider(0, self._total_packets - 1, 0, self._total_packets - 1)
            self._range_slider.rangeChanged.connect(self._range_slider_changed)
            selection_layout.addWidget(self._range_slider, 0, 0, 1, 10)

            selection_layout.addWidget(QtWidgets.QLabel('Start'), 1, 0)
            self._start_box = QtWidgets.QSpinBox()
            self._start_box.setRange(0, self._total_packets - 1)
            self._start_box.valueChanged.connect(self._range_box_changed)
            selection_layout.addWidget(self._start_box, 1, 1)

            selection_layout.addWidget(QtWidgets.QLabel('End'), 1, 2)
            self._end_box = QtWidgets.QSpinBox()
            self._end_box.setRange(0, self._total_packets - 1)
            self._end_box.valueChanged.connect(self._range_box_changed)
            selection_layout.addWidget(self._end_box, 1, 3)

            self._mark_start_button = QtWidgets.QPushButton('Mark Start')
            self._mark_start_button.clicked.connect(self._mark_start)
            selection_layout.addWidget(self._mark_start_button, 1, 4)

            self._mark_end_button = QtWidgets.QPushButton('Mark End')
            self._mark_end_button.clicked.connect(self._mark_end)
            selection_layout.addWidget(self._mark_end_button, 1, 5)

            self._delete_button = QtWidgets.QPushButton('Delete Selection')
            self._delete_button.clicked.connect(self._delete_selection)
            selection_layout.addWidget(self._delete_button, 1, 6)

            self._selection_start_button = QtWidgets.QPushButton('Sel Start')
            self._selection_start_button.clicked.connect(self._jump_selection_start)
            selection_layout.addWidget(self._selection_start_button, 2, 4)

            self._selection_mid_button = QtWidgets.QPushButton('Sel Mid')
            self._selection_mid_button.clicked.connect(self._jump_selection_middle)
            selection_layout.addWidget(self._selection_mid_button, 2, 5)

            self._selection_end_button = QtWidgets.QPushButton('Sel End')
            self._selection_end_button.clicked.connect(self._jump_selection_end)
            selection_layout.addWidget(self._selection_end_button, 2, 6)

            self._selection_label = QtWidgets.QLabel('')
            root.addWidget(self._selection_label)
            self._size_label = QtWidgets.QLabel('')
            root.addWidget(self._size_label)
            self._edited_label = QtWidgets.QLabel('')
            root.addWidget(self._edited_label)
            self._insertions_label = QtWidgets.QLabel('')
            root.addWidget(self._insertions_label)

            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            root.addWidget(split, 1)

            preview_group = QtWidgets.QGroupBox('Row 0 Preview')
            preview_layout = QtWidgets.QVBoxLayout(preview_group)
            self._preview_text = QtWidgets.QPlainTextEdit()
            self._preview_text.setReadOnly(True)
            preview_layout.addWidget(self._preview_text)
            split.addWidget(preview_group)

            pages_group = QtWidgets.QGroupBox('Pages / Subpages')
            pages_layout = QtWidgets.QVBoxLayout(pages_group)
            self._page_tree = QtWidgets.QTreeWidget()
            self._page_tree.setHeaderLabels(['Entry', 'Packets', 'Row 0'])
            pages_layout.addWidget(self._page_tree, 1)

            tree_button_row = QtWidgets.QHBoxLayout()
            pages_layout.addLayout(tree_button_row)
            self._delete_page_button = QtWidgets.QPushButton('Delete Page/Subpage')
            self._delete_page_button.clicked.connect(self._delete_selected_page_entry)
            tree_button_row.addWidget(self._delete_page_button)
            tree_button_row.addStretch(1)

            split.addWidget(pages_group)
            split.setStretchFactor(0, 3)
            split.setStretchFactor(1, 2)

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
            self._save_button.clicked.connect(self._save_file)
            button_row.addWidget(self._save_button)

            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            button_row.addWidget(self._close_button)

            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _capture_snapshot(self):
            return (
                int(self._current_packet),
                int(self._selection_start),
                int(self._selection_end),
                tuple(self._cut_ranges),
                tuple(self._insertions),
                frozenset(self._deleted_pages),
                frozenset(self._deleted_subpages),
            )

        def _record_history_state(self, reset_redo=False):
            snapshot = self._capture_snapshot()
            if not self._history or self._history[-1] != snapshot:
                self._history.append(snapshot)
            if reset_redo:
                self._redo_history.clear()
            self._update_history_buttons()

        def _restore_snapshot(self, snapshot):
            self._current_packet = int(snapshot[0])
            self._selection_start = int(snapshot[1])
            self._selection_end = int(snapshot[2])
            self._cut_ranges = tuple(snapshot[3])
            self._insertions = tuple(snapshot[4])
            self._deleted_pages = frozenset(snapshot[5])
            self._deleted_subpages = frozenset(snapshot[6])
            self._cache_dirty = True
            self._sync_ui()

        def _update_history_buttons(self):
            self._undo_button.setEnabled(len(self._history) > 1)
            self._redo_button.setEnabled(len(self._redo_history) > 0)

        def _mark_cache_dirty(self):
            self._cache_dirty = True

        def _ensure_edit_cache(self):
            if not self._cache_dirty:
                return
            self._cached_combined_entries = tuple(iterate_t42_entries(
                self._entries,
                cut_ranges=self._cut_ranges,
                insertions=self._insertions,
            ))
            self._cached_edited_entries = tuple(filter_deleted_t42_entries(
                self._cached_combined_entries,
                deleted_pages=self._deleted_pages,
                deleted_subpages=self._deleted_subpages,
            ))
            self._cached_deleted_packet_count = len(self._cached_combined_entries) - len(self._cached_edited_entries)
            self._cache_dirty = False

        def _sync_ui(self):
            self._ensure_edit_cache()
            self._updating = True

            current_packet = _clamp(self._current_packet, 0, self._total_packets - 1)
            selection_start = _clamp(self._selection_start, 0, self._total_packets - 1)
            selection_end = _clamp(self._selection_end, 0, self._total_packets - 1)
            if selection_start > selection_end:
                selection_start, selection_end = selection_end, selection_start
            self._current_packet = current_packet
            self._selection_start = selection_start
            self._selection_end = selection_end

            self._packet_slider.setValue(current_packet)
            self._packet_box.setValue(current_packet)
            self._range_slider.setValues(selection_start, selection_end)
            self._range_slider.setCuts(self._cut_ranges)
            self._range_slider.setInsertMarkers(insertion.after_packet for insertion in self._insertions)
            self._start_box.setValue(selection_start)
            self._end_box.setValue(selection_end)

            current_entry = self._entries[current_packet]
            if current_entry.page_number is not None:
                label = _page_label(current_entry.page_number)
                if current_entry.subpage_number is not None:
                    label += f' / {current_entry.subpage_number:04X}'
            else:
                label = 'unknown'
            self._packet_page_label.setText(label)

            selection_packets = (selection_end - selection_start) + 1
            cut_packets = count_cut_frames(self._cut_ranges)
            inserted_packets = count_inserted_packets(self._insertions)
            deleted_packets = self._cached_deleted_packet_count
            edited_packets = len(self._cached_edited_entries)

            self._status_label.setText(f'{current_packet + 1}/{self._total_packets} packets')
            self._selection_label.setText(
                f'Selection: {selection_start}..{selection_end} ({selection_packets} packets, {packet_count_to_megabytes(selection_packets):.2f} MB)'
            )
            self._size_label.setText(
                f'Cuts total: {packet_count_to_megabytes(cut_packets):.2f} MB | '
                f'Inserted total: {packet_count_to_megabytes(inserted_packets):.2f} MB | '
                f'Deleted pages/subpages: {packet_count_to_megabytes(deleted_packets):.2f} MB | '
                f'Edited file: {packet_count_to_megabytes(edited_packets):.2f} MB'
            )
            self._edited_label.setText(
                f'Edited total: {edited_packets} packets | Pages: {len(summarise_t42_pages(self._cached_edited_entries))}'
            )
            if self._insertions:
                self._insertions_label.setText(
                    'Insertions: ' + ', '.join(
                        f'{pathlib.Path(insertion.path).name} -> after {insertion.after_packet} ({insertion.packet_count} packets)'
                        for insertion in self._insertions[-4:]
                    )
                )
            else:
                self._insertions_label.setText('Insertions: none')

            self._preview_text.setPlainText(header_preview_text(self._entries, self._headers, current_packet))
            self._refresh_page_tree()

            self._updating = False
            self._update_history_buttons()

        def _refresh_page_tree(self):
            current_data = None
            current_item = self._page_tree.currentItem()
            if current_item is not None:
                current_data = (
                    current_item.data(0, QtCore.Qt.UserRole),
                    current_item.data(0, QtCore.Qt.UserRole + 1),
                    current_item.data(0, QtCore.Qt.UserRole + 2),
                )

            self._page_tree.clear()
            for page_summary in summarise_t42_pages(self._cached_edited_entries):
                page_item = QtWidgets.QTreeWidgetItem([
                    _page_label(page_summary['page_number']),
                    str(page_summary['packet_count']),
                    page_summary['header_title'],
                ])
                page_item.setData(0, QtCore.Qt.UserRole, 'page')
                page_item.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                page_item.setData(0, QtCore.Qt.UserRole + 2, int(page_summary['first_packet']))
                self._page_tree.addTopLevelItem(page_item)

                for subpage_summary in page_summary['subpages']:
                    child = QtWidgets.QTreeWidgetItem([
                        f"{subpage_summary['subpage_number']:04X}",
                        str(subpage_summary['packet_count']),
                        subpage_summary['header_title'],
                    ])
                    child.setData(0, QtCore.Qt.UserRole, 'subpage')
                    child.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                    child.setData(0, QtCore.Qt.UserRole + 2, int(subpage_summary['subpage_number']))
                    child.setData(0, QtCore.Qt.UserRole + 3, int(subpage_summary['first_packet']))
                    page_item.addChild(child)
                page_item.setExpanded(True)

            if current_data is not None:
                self._restore_tree_selection(current_data)

        def _restore_tree_selection(self, current_data):
            item_type, value1, value2 = current_data
            for page_index in range(self._page_tree.topLevelItemCount()):
                page_item = self._page_tree.topLevelItem(page_index)
                if item_type == 'page' and (
                    page_item.data(0, QtCore.Qt.UserRole) == 'page'
                    and page_item.data(0, QtCore.Qt.UserRole + 1) == value1
                    and page_item.data(0, QtCore.Qt.UserRole + 2) == value2
                ):
                    self._page_tree.setCurrentItem(page_item)
                    return
                if item_type == 'subpage':
                    for child_index in range(page_item.childCount()):
                        child = page_item.child(child_index)
                        if (
                            child.data(0, QtCore.Qt.UserRole) == 'subpage'
                            and child.data(0, QtCore.Qt.UserRole + 1) == value1
                            and child.data(0, QtCore.Qt.UserRole + 2) == value2
                        ):
                            self._page_tree.setCurrentItem(child)
                            return

        def _packet_slider_changed(self, value):
            if self._updating:
                return
            self._current_packet = int(value)
            self._sync_ui()

        def _packet_box_changed(self, value):
            if self._updating:
                return
            self._current_packet = int(value)
            self._sync_ui()

        def _range_slider_changed(self, start, end):
            if self._updating:
                return
            self._selection_start = int(start)
            self._selection_end = int(end)
            self._sync_ui()

        def _range_box_changed(self, _value):
            if self._updating:
                return
            self._selection_start = int(self._start_box.value())
            self._selection_end = int(self._end_box.value())
            self._sync_ui()

        def _step(self, delta):
            self._current_packet = _clamp(self._current_packet + int(delta), 0, self._total_packets - 1)
            self._sync_ui()

        def _jump_start(self):
            self._current_packet = 0
            self._sync_ui()

        def _jump_end(self):
            self._current_packet = self._total_packets - 1
            self._sync_ui()

        def _mark_start(self):
            self._selection_start = int(self._current_packet)
            if self._selection_start > self._selection_end:
                self._selection_end = self._selection_start
            self._sync_ui()

        def _mark_end(self):
            self._selection_end = int(self._current_packet)
            if self._selection_end < self._selection_start:
                self._selection_start = self._selection_end
            self._sync_ui()

        def _jump_selection_start(self):
            self._selection_end = self._selection_start
            self._sync_ui()

        def _jump_selection_middle(self):
            _, middle, _ = selection_end_targets(self._selection_start, self._total_packets)
            self._selection_end = middle
            self._sync_ui()

        def _jump_selection_end(self):
            _, _, end = selection_end_targets(self._selection_start, self._total_packets)
            self._selection_end = end
            self._sync_ui()

        def _delete_selection(self):
            start = int(self._selection_start)
            end = int(self._selection_end)
            self._cut_ranges = normalise_cut_ranges(self._cut_ranges + ((start, end),), self._total_packets)
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _delete_selected_page_entry(self):
            item = self._page_tree.currentItem()
            if item is None:
                return
            item_type = item.data(0, QtCore.Qt.UserRole)
            if item_type == 'page':
                page_number = int(item.data(0, QtCore.Qt.UserRole + 1))
                self._deleted_pages = frozenset(set(self._deleted_pages) | {page_number})
                self._deleted_subpages = frozenset(
                    key for key in self._deleted_subpages
                    if int(key[0]) != page_number
                )
            elif item_type == 'subpage':
                page_number = int(item.data(0, QtCore.Qt.UserRole + 1))
                subpage_number = int(item.data(0, QtCore.Qt.UserRole + 2))
                self._deleted_subpages = frozenset(set(self._deleted_subpages) | {(page_number, subpage_number)})
            else:
                return
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

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

        def _reset_selection(self):
            self._current_packet = 0
            self._selection_start = 0
            self._selection_end = self._total_packets - 1
            self._cut_ranges = ()
            self._insertions = ()
            self._deleted_pages = frozenset()
            self._deleted_subpages = frozenset()
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _add_file(self):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Add T42 File',
                os.getcwd(),
                'Teletext packet files (*.t42);;All files (*)',
            )
            if not filename:
                return
            try:
                entries = load_t42_entries(filename)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'T42 Crop', str(exc))
                return
            if not entries:
                QtWidgets.QMessageBox.warning(self, 'T42 Crop', 'Selected file does not contain any complete packets.')
                return
            insertion = T42Insertion(
                after_packet=int(self._selection_end),
                path=filename,
                packet_count=len(entries),
                entries=tuple(entries),
            )
            self._insertions = normalise_t42_insertions(self._insertions + (insertion,), self._total_packets)
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _save_file(self):
            if self._save_callback is None:
                return
            default_name = 'edited.t42'
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save Edited T42',
                os.path.join(os.getcwd(), default_name),
                'Teletext packet files (*.t42);;All files (*)',
            )
            if not filename:
                return
            try:
                self._save_callback(
                    filename,
                    self._cut_ranges,
                    self._insertions,
                    self._deleted_pages,
                    self._deleted_subpages,
                )
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'T42 Crop', str(exc))
                return
            QtWidgets.QMessageBox.information(self, 'T42 Crop', f'Saved edited T42 to:\n{filename}')


def run_t42_crop_window(input_path, entries, save_callback=None):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    window = T42CropWindow(
        input_path=input_path,
        entries=entries,
        save_callback=save_callback,
    )
    window.show()
    window.raise_()
    window.activateWindow()
    window.exec_()
