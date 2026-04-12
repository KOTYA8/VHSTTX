from __future__ import annotations

import bisect
import os
import pathlib
import tempfile
import time
from collections import Counter
from dataclasses import dataclass

from teletext.file import FileChunker
from teletext.packet import Packet
from teletext.service import Service
from teletext.subpage import Subpage

try:
    from . import vbicrop as _vbicrop
except Exception as exc:  # pragma: no cover - import fallback for headless test environments
    _vbicrop = None
    IMPORT_ERROR = exc
    QtCore = None
    QtGui = None
    QtWidgets = None
    FrameRangeSlider = None

    def _ensure_app():
        raise IMPORT_ERROR

    def _run_dialog_window(dialog):
        raise IMPORT_ERROR

    def _clamp(value, minimum, maximum):
        return max(minimum, min(maximum, int(value)))

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

    DEFAULT_PLAYBACK_SPEED = 1.0
    MIN_PLAYBACK_SPEED = 0.1
    MAX_PLAYBACK_SPEED = 8.0

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

    def selection_end_targets(start_frame, total_frames):
        total_frames = max(int(total_frames), 1)
        maximum = total_frames - 1
        start = _clamp(start_frame, 0, maximum)
        middle = start + ((maximum - start) // 2)
        return start, middle, maximum
else:
    IMPORT_ERROR = _vbicrop.IMPORT_ERROR
    QtCore = _vbicrop.QtCore
    QtGui = _vbicrop.QtGui
    QtWidgets = _vbicrop.QtWidgets
    FrameRangeSlider = getattr(_vbicrop, 'FrameRangeSlider', None)

    _ensure_app = _vbicrop._ensure_app
    _run_dialog_window = _vbicrop._run_dialog_window
    _clamp = _vbicrop._clamp
    advance_playback_position = _vbicrop.advance_playback_position
    DEFAULT_PLAYBACK_SPEED = _vbicrop.DEFAULT_PLAYBACK_SPEED
    MIN_PLAYBACK_SPEED = _vbicrop.MIN_PLAYBACK_SPEED
    MAX_PLAYBACK_SPEED = _vbicrop.MAX_PLAYBACK_SPEED
    normalise_cut_ranges = _vbicrop.normalise_cut_ranges
    count_cut_frames = _vbicrop.count_cut_frames
    normalise_keep_ranges = _vbicrop.normalise_keep_ranges
    keep_ranges_to_cut_ranges = _vbicrop.keep_ranges_to_cut_ranges
    selection_end_targets = _vbicrop.selection_end_targets


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


def _configure_tree_widget_columns(tree):
    if tree is None or QtWidgets is None:
        return
    header = tree.header()
    if header is not None:
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        header.setMinimumSectionSize(40)
    tree.setColumnWidth(0, 86)
    tree.setColumnWidth(1, 58)
    tree.setColumnWidth(2, 72)


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


def build_t42_entries(raw_packets):
    current_page = {}
    entries = []

    for packet_index, data in enumerate(raw_packets):
        raw = bytes(data)
        if len(raw) != PACKET_SIZE:
            continue
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


def load_t42_entries(path):
    with open(path, 'rb') as handle:
        return build_t42_entries(bytes(data) for _packet_index, data in FileChunker(handle, PACKET_SIZE))


def collect_page_entries(entries, page_number):
    page_number = int(page_number)
    return tuple(entry for entry in entries if entry.page_number == page_number)


def collect_subpage_entries(entries, page_number, subpage_number):
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    return tuple(
        entry for entry in entries
        if entry.page_number == page_number and entry.subpage_number == subpage_number
    )


def collect_subpage_occurrence_entries(entries, page_number, subpage_number, occurrence_number=1):
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    occurrence_number = max(int(occurrence_number or 1), 1)
    matching_headers = [
        entry for entry in entries
        if (
            entry.page_number == page_number
            and entry.subpage_number == subpage_number
            and entry.row is not None
            and int(entry.row) == 0
        )
    ]
    if not matching_headers:
        return ()
    if occurrence_number > len(matching_headers):
        occurrence_number = len(matching_headers)
    start_packet = int(matching_headers[occurrence_number - 1].packet_index)
    end_packet = None
    if occurrence_number < len(matching_headers):
        end_packet = int(matching_headers[occurrence_number].packet_index)
    return tuple(
        entry for entry in entries
        if (
            entry.page_number == page_number
            and entry.subpage_number == subpage_number
            and int(entry.packet_index) >= start_packet
            and (end_packet is None or int(entry.packet_index) < end_packet)
        )
    )


def collect_row_entries(entries, page_number, subpage_number, row_number):
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    row_number = int(row_number)
    return tuple(
        entry for entry in entries
        if (
            entry.page_number == page_number
            and entry.subpage_number == subpage_number
            and entry.row == row_number
        )
    )


def selected_row_zero_text(entries, page_number, subpage_number=None):
    page_number = int(page_number)
    if subpage_number is None:
        for entry in entries:
            if entry.page_number == page_number and entry.row == 0 and entry.header_text:
                return str(entry.header_text)
        return ''
    subpage_number = int(subpage_number)
    for entry in entries:
        if (
            entry.page_number == page_number
            and entry.subpage_number == subpage_number
            and entry.row == 0
            and entry.header_text
        ):
            return str(entry.header_text)
    return ''


def parse_page_identifier(value):
    text = str(value or '').strip().upper()
    if text.startswith('P'):
        text = text[1:]
    if len(text) != 3:
        raise ValueError('Page number must be three hexadecimal digits, for example 100 or 1AF.')
    try:
        magazine = int(text[0], 16)
        page = int(text[1:], 16)
    except ValueError as exc:
        raise ValueError('Page number must be hexadecimal, for example 100 or 1AF.') from exc
    if magazine < 1 or magazine > 8:
        raise ValueError('Page magazine must be between 1 and 8.')
    return _compose_page_number(magazine, page)


def parse_subpage_identifier(value):
    text = str(value or '').strip().upper()
    if len(text) != 4:
        raise ValueError('Subpage number must be four hexadecimal digits, for example 0001.')
    try:
        subpage = int(text, 16)
    except ValueError as exc:
        raise ValueError('Subpage number must be hexadecimal, for example 0001.') from exc
    if subpage < 0 or subpage > 0x3F7F:
        raise ValueError('Subpage number must be between 0000 and 3F7F.')
    return subpage


def blank_subpage_entries(page_number=0x100, subpage_number=0x0000):
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    magazine = page_number >> 8
    subpage = Subpage(prefill=True, magazine=magazine)
    subpage.packet(0).mrag.magazine = magazine
    subpage.header.page = page_number & 0xFF
    subpage.header.subpage = subpage_number
    subpage.header.control = 1 << 0
    subpage.header.displayable[:] = 0x20
    subpage.displayable[:] = 0x20
    return build_t42_entries(packet.to_bytes() for packet in subpage.packets)


def insert_hidden_subpage_occurrence(entries, page_number, subpage_number, new_entries):
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    new_entries = tuple(new_entries)
    existing_occurrence = collect_subpage_occurrence_entries(entries, page_number, subpage_number, 999999)
    if existing_occurrence:
        last_packet_index = max(int(entry.packet_index) for entry in existing_occurrence)
        insert_index = next(
            (
                index + 1
                for index, entry in enumerate(entries)
                if int(entry.packet_index) == last_packet_index
            ),
            len(entries),
        )
    else:
        page_positions = [index for index, entry in enumerate(entries) if entry.page_number == page_number]
        insert_index = (page_positions[-1] + 1) if page_positions else len(entries)
    return build_t42_entries(
        [entry.raw for entry in entries[:insert_index]]
        + [entry.raw for entry in new_entries]
        + [entry.raw for entry in entries[insert_index:]]
    )


def move_subpage_occurrence_in_entries(entries, page_number, subpage_number, source_occurrence_number, target_occurrence_number):
    entries = tuple(entries)
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    source_occurrence_number = max(int(source_occurrence_number or 1), 1)
    target_occurrence_number = max(int(target_occurrence_number or 1), 1)
    all_occurrences = tuple(page_subpage_occurrences(entries, page_number).get(subpage_number, ()))
    if len(all_occurrences) <= 1:
        return entries
    target_occurrence_number = min(target_occurrence_number, len(all_occurrences))
    if source_occurrence_number == target_occurrence_number:
        return entries

    source_entries = collect_subpage_occurrence_entries(
        entries,
        page_number,
        subpage_number,
        source_occurrence_number,
    )
    if not source_entries:
        return entries

    removed_packet_indices = {int(entry.packet_index) for entry in source_entries}
    remaining_entries = tuple(
        entry for entry in entries
        if int(entry.packet_index) not in removed_packet_indices
    )
    remaining_occurrences = tuple(
        page_subpage_occurrences(remaining_entries, page_number).get(subpage_number, ())
    )

    if target_occurrence_number <= len(remaining_occurrences):
        target_entries = collect_subpage_occurrence_entries(
            remaining_entries,
            page_number,
            subpage_number,
            target_occurrence_number,
        )
        if target_entries:
            first_packet = min(int(entry.packet_index) for entry in target_entries)
            insert_index = next(
                (
                    index
                    for index, entry in enumerate(remaining_entries)
                    if int(entry.packet_index) == first_packet
                ),
                len(remaining_entries),
            )
        else:
            insert_index = _subpage_insert_index(remaining_entries, page_number, subpage_number)
    elif remaining_occurrences:
        last_entries = collect_subpage_occurrence_entries(
            remaining_entries,
            page_number,
            subpage_number,
            len(remaining_occurrences),
        )
        if last_entries:
            last_packet = max(int(entry.packet_index) for entry in last_entries)
            insert_index = next(
                (
                    index + 1
                    for index, entry in enumerate(remaining_entries)
                    if int(entry.packet_index) == last_packet
                ),
                len(remaining_entries),
            )
        else:
            insert_index = _subpage_insert_index(remaining_entries, page_number, subpage_number)
    else:
        page_positions = [
            index for index, entry in enumerate(remaining_entries)
            if entry.page_number == page_number
        ]
        insert_index = (page_positions[-1] + 1) if page_positions else len(remaining_entries)

    return build_t42_entries(
        [entry.raw for entry in remaining_entries[:insert_index]]
        + [entry.raw for entry in source_entries]
        + [entry.raw for entry in remaining_entries[insert_index:]]
    )


def retarget_t42_entries(entries, page_number=None, subpage_number=None):
    target_page_number = None if page_number is None else int(page_number)
    target_subpage_number = None if subpage_number is None else int(subpage_number)
    raw_packets = []
    for entry in entries:
        packet = Packet(entry.raw)
        if target_page_number is not None:
            packet.mrag.magazine = target_page_number >> 8
            if packet.type == 'header':
                packet.header.page = target_page_number & 0xFF
        if target_subpage_number is not None and packet.type == 'header':
            packet.header.subpage = target_subpage_number
        raw_packets.append(packet.to_bytes())
    return build_t42_entries(raw_packets)


def _retarget_t42_entry(entry, page_number=None, subpage_number=None, row_number=None):
    packet = Packet(entry.raw)
    if page_number is not None:
        page_number = int(page_number)
        packet.mrag.magazine = page_number >> 8
        if packet.type == 'header':
            packet.header.page = page_number & 0xFF
    if subpage_number is not None and packet.type == 'header':
        packet.header.subpage = int(subpage_number)
    if row_number is not None:
        packet.mrag.row = int(row_number)
    return build_t42_entries([packet.to_bytes()])[0]


def _replace_entry_slice(entries, match, replacements, insert_index):
    filtered = [entry for entry in entries if not match(entry)]
    insert_index = max(0, min(int(insert_index), len(filtered)))
    return build_t42_entries(
        [entry.raw for entry in filtered[:insert_index]]
        + [entry.raw for entry in replacements]
        + [entry.raw for entry in filtered[insert_index:]]
    )


def replace_page_in_entries(entries, replacement_entries, target_page_number=None):
    if not replacement_entries:
        return tuple(entries)
    if target_page_number is None:
        page_numbers = [entry.page_number for entry in replacement_entries if entry.page_number is not None]
        if not page_numbers:
            return tuple(entries)
        target_page_number = int(page_numbers[0])
    else:
        target_page_number = int(target_page_number)

    positions = [index for index, entry in enumerate(entries) if entry.page_number == target_page_number]
    insert_index = positions[0] if positions else len(entries)
    replacements = retarget_t42_entries(replacement_entries, page_number=target_page_number)
    return _replace_entry_slice(
        entries,
        lambda entry: entry.page_number == target_page_number,
        replacements,
        insert_index,
    )


def replace_subpage_in_entries(entries, replacement_entries, target_page_number=None, target_subpage_number=None):
    if not replacement_entries:
        return tuple(entries)
    if target_page_number is None or target_subpage_number is None:
        page_numbers = [entry.page_number for entry in replacement_entries if entry.page_number is not None]
        subpage_numbers = [entry.subpage_number for entry in replacement_entries if entry.subpage_number is not None]
        if not page_numbers or not subpage_numbers:
            return tuple(entries)
        if target_page_number is None:
            target_page_number = int(page_numbers[0])
        if target_subpage_number is None:
            target_subpage_number = int(subpage_numbers[0])
    else:
        target_page_number = int(target_page_number)
        target_subpage_number = int(target_subpage_number)

    positions = [
        index for index, entry in enumerate(entries)
        if entry.page_number == target_page_number and entry.subpage_number == target_subpage_number
    ]
    if positions:
        insert_index = positions[0]
    else:
        page_positions = [index for index, entry in enumerate(entries) if entry.page_number == target_page_number]
        insert_index = (page_positions[-1] + 1) if page_positions else len(entries)

    replacements = retarget_t42_entries(
        replacement_entries,
        page_number=target_page_number,
        subpage_number=target_subpage_number,
    )
    return _replace_entry_slice(
        entries,
        lambda entry: entry.page_number == target_page_number and entry.subpage_number == target_subpage_number,
        replacements,
        insert_index,
    )


def replace_subpage_occurrence_in_entries(
    entries,
    replacement_entries,
    source_page_number,
    source_subpage_number,
    source_occurrence_number=1,
    target_page_number=None,
    target_subpage_number=None,
):
    if not replacement_entries:
        return tuple(entries)
    source_page_number = int(source_page_number)
    source_subpage_number = int(source_subpage_number)
    source_occurrence_number = max(int(source_occurrence_number or 1), 1)
    if target_page_number is None or target_subpage_number is None:
        page_numbers = [entry.page_number for entry in replacement_entries if entry.page_number is not None]
        subpage_numbers = [entry.subpage_number for entry in replacement_entries if entry.subpage_number is not None]
        if not page_numbers or not subpage_numbers:
            return tuple(entries)
        if target_page_number is None:
            target_page_number = int(page_numbers[0])
        if target_subpage_number is None:
            target_subpage_number = int(subpage_numbers[0])
    else:
        target_page_number = int(target_page_number)
        target_subpage_number = int(target_subpage_number)
    source_entries = collect_subpage_occurrence_entries(
        entries,
        source_page_number,
        source_subpage_number,
        source_occurrence_number,
    )
    replacements = retarget_t42_entries(
        replacement_entries,
        page_number=target_page_number,
        subpage_number=target_subpage_number,
    )
    if not source_entries:
        return _replace_entry_slice(
            entries,
            lambda entry: False,
            replacements,
            _subpage_insert_index(entries, target_page_number, target_subpage_number),
        )
    removed_packet_indices = {int(entry.packet_index) for entry in source_entries}
    positions = [
        index for index, entry in enumerate(entries)
        if int(entry.packet_index) in removed_packet_indices
    ]
    insert_index = positions[0] if positions else _subpage_insert_index(entries, target_page_number, target_subpage_number)
    return _replace_entry_slice(
        entries,
        lambda entry: int(entry.packet_index) in removed_packet_indices,
        replacements,
        insert_index,
    )


def _subpage_insert_index(entries, target_page_number, target_subpage_number):
    target_page_number = int(target_page_number)
    target_subpage_number = int(target_subpage_number)
    positions = [
        index for index, entry in enumerate(entries)
        if entry.page_number == target_page_number and entry.subpage_number == target_subpage_number
    ]
    if positions:
        return positions[0]
    page_positions = [index for index, entry in enumerate(entries) if entry.page_number == target_page_number]
    return (page_positions[-1] + 1) if page_positions else len(entries)


def _merge_subpage_packets(existing_entries, source_entries, target_page_number, target_subpage_number):
    target_page_number = int(target_page_number)
    target_subpage_number = int(target_subpage_number)

    existing_map = {
        int(entry.row): _retarget_t42_entry(
            entry,
            page_number=target_page_number,
            subpage_number=target_subpage_number,
        )
        for entry in existing_entries
        if entry.row is not None
    }
    source_map = {
        int(entry.row): _retarget_t42_entry(
            entry,
            page_number=target_page_number,
            subpage_number=target_subpage_number,
        )
        for entry in source_entries
        if entry.row is not None
    }

    if 0 in source_map:
        if 0 not in existing_map:
            existing_map[0] = source_map[0]
    elif 0 not in existing_map:
        return tuple(existing_entries)

    merged = dict(existing_map)
    for row_number, entry in source_map.items():
        if row_number == 0 and 0 in existing_map:
            continue
        merged[int(row_number)] = entry

    return tuple(merged[row_number] for row_number in sorted(merged))


def merge_subpage_in_entries(entries, source_entries, target_page_number, target_subpage_number):
    if not source_entries:
        return tuple(entries)
    target_page_number = int(target_page_number)
    target_subpage_number = int(target_subpage_number)
    existing_entries = collect_subpage_entries(entries, target_page_number, target_subpage_number)
    merged_entries = _merge_subpage_packets(
        existing_entries,
        source_entries,
        target_page_number,
        target_subpage_number,
    )
    insert_index = _subpage_insert_index(entries, target_page_number, target_subpage_number)
    return _replace_entry_slice(
        entries,
        lambda entry: entry.page_number == target_page_number and entry.subpage_number == target_subpage_number,
        merged_entries,
        insert_index,
    )


def merge_subpage_occurrence_in_entries(entries, source_entries, target_page_number, target_subpage_number, target_occurrence_number=1):
    target_occurrence_number = max(int(target_occurrence_number or 1), 1)
    if target_occurrence_number <= 1:
        return merge_subpage_in_entries(entries, source_entries, target_page_number, target_subpage_number)
    existing_entries = collect_subpage_occurrence_entries(
        entries,
        target_page_number,
        target_subpage_number,
        target_occurrence_number,
    )
    if not existing_entries:
        return merge_subpage_in_entries(entries, source_entries, target_page_number, target_subpage_number)
    merged_entries = _merge_subpage_packets(
        existing_entries,
        source_entries,
        target_page_number,
        target_subpage_number,
    )
    return replace_subpage_occurrence_in_entries(
        entries,
        merged_entries,
        target_page_number,
        target_subpage_number,
        target_occurrence_number,
        target_page_number=target_page_number,
        target_subpage_number=target_subpage_number,
    )


def merge_page_in_entries(entries, source_entries, source_page_number, target_page_number):
    source_page_number = int(source_page_number)
    target_page_number = int(target_page_number)
    if not source_entries:
        return tuple(entries)

    working_entries = tuple(entries)
    subpage_numbers = sorted({
        int(entry.subpage_number)
        for entry in source_entries
        if entry.page_number == source_page_number and entry.subpage_number is not None
    })
    for subpage_number in subpage_numbers:
        working_entries = merge_subpage_in_entries(
            working_entries,
            collect_subpage_entries(source_entries, source_page_number, subpage_number),
            target_page_number,
            subpage_number,
        )
    return working_entries


def add_row_to_subpage_entries(entries, source_entry, target_page_number, target_subpage_number, target_row_number, source_header_entry=None):
    if source_entry is None:
        return tuple(entries)
    target_page_number = int(target_page_number)
    target_subpage_number = int(target_subpage_number)
    target_row_number = int(target_row_number)
    source_row_number = -1 if source_entry.row is None else int(source_entry.row)
    if target_row_number < 0 or target_row_number > 31:
        raise ValueError('Target row must be between 0 and 31.')
    if source_row_number == 0 and target_row_number != 0:
        raise ValueError('Row 0 can only be copied to target row 0.')
    if source_row_number != 0 and target_row_number == 0:
        raise ValueError('Only source row 0 can be copied to target row 0.')

    existing_entries = collect_subpage_entries(entries, target_page_number, target_subpage_number)
    header_entry = next((entry for entry in existing_entries if entry.row == 0), None)
    if target_row_number != 0 and header_entry is None:
        source_header = source_header_entry
        if source_header is None:
            raise ValueError('Target subpage has no header. Merge the whole subpage first or choose a source subpage with a header.')
        source_header = _retarget_t42_entry(
            source_header,
            page_number=target_page_number,
            subpage_number=target_subpage_number,
        )
        existing_entries = (source_header,) + tuple(entry for entry in existing_entries if entry.row != 0)

    updated_row = _retarget_t42_entry(
        source_entry,
        page_number=target_page_number,
        subpage_number=target_subpage_number,
        row_number=target_row_number,
    )
    merged_rows = []
    inserted = False
    for entry in sorted(existing_entries, key=lambda item: -1 if item.row is None else int(item.row)):
        if entry.row == target_row_number:
            if not inserted:
                merged_rows.append(updated_row)
                inserted = True
            continue
        merged_rows.append(entry)
    if not inserted:
        merged_rows.append(updated_row)
    merged_rows = tuple(sorted(
        merged_rows,
        key=lambda item: -1 if item.row is None else int(item.row),
    ))

    insert_index = _subpage_insert_index(entries, target_page_number, target_subpage_number)
    return _replace_entry_slice(
        entries,
        lambda entry: entry.page_number == target_page_number and entry.subpage_number == target_subpage_number,
        merged_rows,
        insert_index,
    )


def add_row_to_subpage_occurrence_in_entries(
    entries,
    source_entry,
    target_page_number,
    target_subpage_number,
    target_row_number,
    target_occurrence_number=1,
    source_header_entry=None,
):
    target_occurrence_number = max(int(target_occurrence_number or 1), 1)
    if target_occurrence_number <= 1:
        return add_row_to_subpage_entries(
            entries,
            source_entry,
            target_page_number,
            target_subpage_number,
            target_row_number,
            source_header_entry=source_header_entry,
        )
    existing_entries = collect_subpage_occurrence_entries(
        entries,
        target_page_number,
        target_subpage_number,
        target_occurrence_number,
    )
    if not existing_entries:
        return add_row_to_subpage_entries(
            entries,
            source_entry,
            target_page_number,
            target_subpage_number,
            target_row_number,
            source_header_entry=source_header_entry,
        )
    updated_occurrence_entries = add_row_to_subpage_entries(
        existing_entries,
        source_entry,
        target_page_number,
        target_subpage_number,
        target_row_number,
        source_header_entry=source_header_entry,
    )
    return replace_subpage_occurrence_in_entries(
        entries,
        updated_occurrence_entries,
        target_page_number,
        target_subpage_number,
        target_occurrence_number,
        target_page_number=target_page_number,
        target_subpage_number=target_subpage_number,
    )


def move_page_in_entries(entries, source_page_number, target_page_number):
    source_page_number = int(source_page_number)
    target_page_number = int(target_page_number)
    if source_page_number == target_page_number:
        return tuple(entries)
    replacements = collect_page_entries(entries, source_page_number)
    if not replacements:
        return tuple(entries)
    positions = [
        index for index, entry in enumerate(entries)
        if entry.page_number in {source_page_number, target_page_number}
    ]
    insert_index = positions[0] if positions else len(entries)
    replacements = retarget_t42_entries(replacements, page_number=target_page_number)
    return _replace_entry_slice(
        entries,
        lambda entry: entry.page_number in {source_page_number, target_page_number},
        replacements,
        insert_index,
    )


def move_subpage_in_entries(entries, source_page_number, source_subpage_number, target_page_number, target_subpage_number):
    source_page_number = int(source_page_number)
    source_subpage_number = int(source_subpage_number)
    target_page_number = int(target_page_number)
    target_subpage_number = int(target_subpage_number)
    if (source_page_number, source_subpage_number) == (target_page_number, target_subpage_number):
        return tuple(entries)
    replacements = collect_subpage_entries(entries, source_page_number, source_subpage_number)
    if not replacements:
        return tuple(entries)
    positions = [
        index for index, entry in enumerate(entries)
        if (
            entry.page_number == source_page_number and entry.subpage_number == source_subpage_number
        ) or (
            entry.page_number == target_page_number and entry.subpage_number == target_subpage_number
        )
    ]
    if positions:
        insert_index = positions[0]
    else:
        page_positions = [index for index, entry in enumerate(entries) if entry.page_number == target_page_number]
        insert_index = (page_positions[-1] + 1) if page_positions else len(entries)

    replacements = retarget_t42_entries(
        replacements,
        page_number=target_page_number,
        subpage_number=target_subpage_number,
    )
    return _replace_entry_slice(
        entries,
        lambda entry: (
            (entry.page_number == source_page_number and entry.subpage_number == source_subpage_number)
            or
            (entry.page_number == target_page_number and entry.subpage_number == target_subpage_number)
        ),
        replacements,
        insert_index,
    )


def next_available_subpage_number(entries, page_number, preferred_start=None):
    page_number = int(page_number)
    used = {
        int(entry.subpage_number)
        for entry in entries
        if entry.page_number == page_number and entry.subpage_number is not None
    }
    if preferred_start is None:
        preferred_start = 0
    start = max(int(preferred_start), 0)
    for candidate in range(start, 0x3F80):
        if candidate not in used:
            return candidate
    raise ValueError('No free subpage numbers are available in this page.')


def convert_subpage_occurrence_to_real(entries, page_number, subpage_number, occurrence_number=2, target_subpage_number=None):
    page_number = int(page_number)
    subpage_number = int(subpage_number)
    occurrence_number = max(int(occurrence_number or 1), 1)
    if occurrence_number <= 1:
        return tuple(entries), subpage_number
    source_entries = collect_subpage_occurrence_entries(entries, page_number, subpage_number, occurrence_number)
    if not source_entries:
        return tuple(entries), subpage_number
    if target_subpage_number is None:
        target_subpage_number = next_available_subpage_number(entries, page_number, preferred_start=subpage_number + 1)
    else:
        target_subpage_number = int(target_subpage_number)
    replacements = retarget_t42_entries(
        source_entries,
        page_number=page_number,
        subpage_number=target_subpage_number,
    )
    removed_packet_indices = {int(entry.packet_index) for entry in source_entries}
    remaining_entries = tuple(
        entry for entry in entries
        if int(entry.packet_index) not in removed_packet_indices
    )
    insert_index = _subpage_insert_index(remaining_entries, page_number, target_subpage_number)
    updated_entries = build_t42_entries(
        [entry.raw for entry in remaining_entries[:insert_index]]
        + [entry.raw for entry in replacements]
        + [entry.raw for entry in remaining_entries[insert_index:]]
    )
    return updated_entries, target_subpage_number



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


def filter_enabled_occurrence_entries(entries, enabled_occurrences=()):
    enabled_occurrences = frozenset(
        (
            int(page_number),
            int(subpage_number),
            max(int(occurrence_number or 1), 1),
        )
        for page_number, subpage_number, occurrence_number in enabled_occurrences
    )
    if not enabled_occurrences:
        return tuple(entries)
    counts = Counter()
    current_occurrence = {}
    filtered = []
    for entry in tuple(entries):
        if entry.page_number is None or entry.subpage_number is None:
            filtered.append(entry)
            continue
        page_number = int(entry.page_number)
        subpage_number = int(entry.subpage_number)
        key = (page_number, subpage_number)
        if entry.row is not None and int(entry.row) == 0:
            counts[key] += 1
            current_occurrence[key] = int(counts[key])
        occurrence_number = int(current_occurrence.get(key, max(counts.get(key, 0), 1)))
        if (page_number, subpage_number, occurrence_number) in enabled_occurrences:
            filtered.append(entry)
    return tuple(filtered)


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


def full_header_preview_text(entries, headers, current_packet):
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
    lines.append('Row 0 preview (full file):')

    if not headers:
        lines.append('No row 0 packets found.')
        return '\n'.join(lines)

    header_positions = [header.packet_index for header in headers]
    current_header_index = bisect.bisect_right(header_positions, current_packet) - 1
    for index, header in enumerate(headers):
        marker = '>' if index == current_header_index else ' '
        lines.append(f'{marker} {header.text}')
    return '\n'.join(lines)


def _packet_preview_line(edited_index, entry):
    row_label = '--' if entry.row is None else f'{int(entry.row):02d}'
    if entry.page_number is not None:
        page_label = _page_label(entry.page_number)
        if entry.subpage_number is not None:
            page_label += f':{int(entry.subpage_number):04X}'
    else:
        page_label = 'unknown'
    text = _sanitise_ascii(Packet(entry.raw, edited_index).to_bytes_no_parity())
    line = f'{int(edited_index):7d} {page_label} r{row_label}'
    if text:
        line += f' {text}'
    return line


def frame_preview_text(entries, current_packet):
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
    lines.append('Frame preview (all rows):')

    start = current_packet
    while start > 0 and entries[start].row != 0:
        start -= 1

    end = current_packet
    while end + 1 < len(entries) and entries[end + 1].row != 0:
        end += 1

    for packet_index in range(start, end + 1):
        marker = '>' if packet_index == current_packet else ' '
        lines.append(f'{marker} {_packet_preview_line(packet_index, entries[packet_index])}')
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


def _exact_page_subpage_occurrences(entries, page_number=None):
    occurrences = {}
    counts = {}
    active_occurrences = {}
    for entry in entries:
        if entry.page_number is None or entry.subpage_number is None:
            continue
        current_page_number = int(entry.page_number)
        if page_number is not None and current_page_number != int(page_number):
            continue
        subpage_number = int(entry.subpage_number)
        key = (current_page_number, subpage_number)
        if entry.row is not None and int(entry.row) == 0:
            counts[key] = int(counts.get(key, 0)) + 1
            title = _header_title_from_text(entry.header_text) if entry.header_text else ''
            occurrence = {
                'label': f'{subpage_number:04X}' if counts[key] == 1 else f'{subpage_number:04X} ({counts[key]})',
                'occurrence': counts[key],
                'header_title': title,
                'first_packet': int(entry.packet_index),
                'packet_count': 0,
            }
            occurrences.setdefault(current_page_number, {}).setdefault(subpage_number, []).append(occurrence)
            active_occurrences[key] = occurrence
        occurrence = active_occurrences.get(key)
        if occurrence is not None:
            occurrence['packet_count'] = int(occurrence.get('packet_count') or 0) + 1
    return occurrences


def legacy_page_subpage_occurrences(entries, page_number=None):
    entries = tuple(entries)
    packets = (
        Packet(entry.raw, number=index)
        for index, entry in enumerate(entries)
    )
    service = Service.from_packets(packets)
    exact_occurrences = _exact_page_subpage_occurrences(entries, page_number=page_number)
    summaries = {
        (int(page_summary['page_number']), int(subpage_summary['subpage_number'])): subpage_summary
        for page_summary in summarise_t42_pages(entries)
        if page_number is None or int(page_summary['page_number']) == int(page_number)
        for subpage_summary in page_summary['subpages']
    }
    occurrences = {}
    for magazine_number, magazine in sorted(service.magazines.items()):
        for local_page_number, page in sorted(magazine.pages.items()):
            if not page.subpages:
                continue
            full_page_number = (int(magazine_number) << 8) | int(local_page_number)
            if page_number is not None and full_page_number != int(page_number):
                continue
            page_occurrences = occurrences.setdefault(full_page_number, {})
            for subpage_number, subpage in sorted(page.subpages.items()):
                variants = [subpage] + list(getattr(subpage, 'duplicates', ()))
                exact_variants = tuple(exact_occurrences.get(full_page_number, {}).get(int(subpage_number), ()))
                summary = summaries.get((full_page_number, int(subpage_number)), {})
                bucket = []
                for occurrence_number, variant in enumerate(variants, start=1):
                    exact_variant = exact_variants[occurrence_number - 1] if occurrence_number <= len(exact_variants) else {}
                    raw_header = bytes(variant.header.displayable.bytes_no_parity).decode('ascii', errors='ignore').strip()
                    bucket.append({
                        'label': f'{int(subpage_number):04X}' if occurrence_number == 1 else f'{int(subpage_number):04X} ({occurrence_number})',
                        'occurrence': occurrence_number,
                        'header_title': raw_header or str(exact_variant.get('header_title') or summary.get('header_title') or ''),
                        'first_packet': int(exact_variant.get('first_packet') or summary.get('first_packet') or 0),
                        'packet_count': int(exact_variant.get('packet_count') or summary.get('packet_count') or 0),
                    })
                page_occurrences[int(subpage_number)] = bucket
    return occurrences


def page_subpage_occurrences(entries, page_number=None, mode='raw'):
    if str(mode or 'raw') != 'raw':
        return legacy_page_subpage_occurrences(entries, page_number=page_number)
    return _exact_page_subpage_occurrences(entries, page_number=page_number)


def packet_count_to_megabytes(packet_count):
    return (max(int(packet_count), 0) * PACKET_SIZE) / (1024 * 1024)


def write_t42_entries(entries, output_path, progress_callback=None):
    entries = tuple(entries)
    total = max(len(entries), 1)
    if callable(progress_callback):
        progress_callback(0, total)
    with open(output_path, 'wb') as handle:
        for index, entry in enumerate(entries, start=1):
            handle.write(entry.raw)
            if callable(progress_callback) and (((index - 1) % 64) == 0 or index == len(entries)):
                progress_callback(index, total)


if IMPORT_ERROR is None:
    class T42SourceDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._entries = ()
            self._source_path = ''
            self._apply_page_callback = None
            self._apply_subpage_callback = None
            self._merge_page_callback = None
            self._merge_subpage_callback = None
            self._add_row_callback = None
            self._preview_callback = None
            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.setWindowTitle('Source T42')
            self.resize(650, 560)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(10, 10, 10, 10)
            root.setSpacing(8)

            file_row = QtWidgets.QHBoxLayout()
            root.addLayout(file_row)
            self._file_label = QtWidgets.QLabel('No source file loaded.')
            self._file_label.setWordWrap(True)
            file_row.addWidget(self._file_label, 1)
            self._open_button = QtWidgets.QPushButton('Open Source .t42...')
            self._open_button.clicked.connect(self._open_source_file)
            file_row.addWidget(self._open_button)
            file_row.addStretch(1)
            self._show_hidden_subpages_toggle = QtWidgets.QCheckBox('Hidden Subpages')
            self._show_hidden_subpages_toggle.toggled.connect(lambda _checked=False: self._rebuild_tree())
            file_row.addWidget(self._show_hidden_subpages_toggle)
            file_row.addWidget(QtWidgets.QLabel('Mode'))
            self._hidden_subpages_mode_combo = QtWidgets.QComboBox()
            self._hidden_subpages_mode_combo.addItem('Legacy', 'legacy')
            self._hidden_subpages_mode_combo.addItem('Exact', 'raw')
            self._hidden_subpages_mode_combo.currentIndexChanged.connect(lambda _index=0: self._rebuild_tree())
            file_row.addWidget(self._hidden_subpages_mode_combo)

            self._tree = QtWidgets.QTreeWidget()
            self._tree.setHeaderLabels(['Entry', 'Packets', 'At', 'Row 0'])
            self._tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            _configure_tree_widget_columns(self._tree)
            self._tree.itemDoubleClicked.connect(self._preview_selected)
            self._tree.itemSelectionChanged.connect(self._sync_buttons)
            root.addWidget(self._tree, 1)

            target_group = QtWidgets.QGroupBox('Target')
            target_layout = QtWidgets.QGridLayout(target_group)
            root.addWidget(target_group)

            target_layout.addWidget(QtWidgets.QLabel('Page'), 0, 0)
            self._target_page_input = QtWidgets.QLineEdit()
            self._target_page_input.setPlaceholderText('100')
            target_layout.addWidget(self._target_page_input, 0, 1)

            target_layout.addWidget(QtWidgets.QLabel('Subpage'), 0, 2)
            self._target_subpage_input = QtWidgets.QLineEdit()
            self._target_subpage_input.setPlaceholderText('0001')
            target_layout.addWidget(self._target_subpage_input, 0, 3)

            target_layout.addWidget(QtWidgets.QLabel('Source Row'), 1, 0)
            self._source_row_box = QtWidgets.QSpinBox()
            self._source_row_box.setRange(0, 31)
            target_layout.addWidget(self._source_row_box, 1, 1)

            target_layout.addWidget(QtWidgets.QLabel('Target Row'), 1, 2)
            self._target_row_box = QtWidgets.QSpinBox()
            self._target_row_box.setRange(0, 31)
            target_layout.addWidget(self._target_row_box, 1, 3)

            button_row = QtWidgets.QHBoxLayout()
            root.addLayout(button_row)
            self._import_page_button = QtWidgets.QPushButton('Add / Replace Page')
            self._import_page_button.clicked.connect(self._import_page)
            button_row.addWidget(self._import_page_button)
            self._import_subpage_button = QtWidgets.QPushButton('Add / Replace Subpage')
            self._import_subpage_button.clicked.connect(self._import_subpage)
            button_row.addWidget(self._import_subpage_button)
            self._merge_button = QtWidgets.QPushButton('Merge All Data')
            self._merge_button.clicked.connect(self._merge_selected)
            button_row.addWidget(self._merge_button)
            self._add_row_button = QtWidgets.QPushButton('Add Single Row')
            self._add_row_button.clicked.connect(self._add_row)
            button_row.addWidget(self._add_row_button)
            self._preview_button = QtWidgets.QPushButton('Preview')
            self._preview_button.clicked.connect(self._preview_selected)
            button_row.addWidget(self._preview_button)
            button_row.addStretch(1)
            self._close_button = QtWidgets.QPushButton('Close')
            self._close_button.clicked.connect(self.close)
            button_row.addWidget(self._close_button)

            self._sync_buttons()

        def configure(self, *, apply_page_callback=None, apply_subpage_callback=None, merge_page_callback=None, merge_subpage_callback=None, add_row_callback=None, preview_callback=None, default_page_number=None, default_subpage_number=None, default_row_number=None):
            self._apply_page_callback = apply_page_callback
            self._apply_subpage_callback = apply_subpage_callback
            self._merge_page_callback = merge_page_callback
            self._merge_subpage_callback = merge_subpage_callback
            self._add_row_callback = add_row_callback
            self._preview_callback = preview_callback
            self._target_page_input.setText('' if default_page_number is None else f"{(int(default_page_number) >> 8):X}{(int(default_page_number) & 0xFF):02X}")
            self._target_subpage_input.setText('' if default_subpage_number is None else f'{int(default_subpage_number):04X}')
            self._source_row_box.setValue(0 if default_row_number is None else max(0, min(31, int(default_row_number))))
            self._target_row_box.setValue(0 if default_row_number is None else max(0, min(31, int(default_row_number))))
            self._sync_buttons()

        def _sync_buttons(self):
            item = self._tree.currentItem()
            item_type = None if item is None else item.data(0, QtCore.Qt.UserRole)
            self._import_page_button.setEnabled(item_type == 'page' and callable(self._apply_page_callback))
            self._import_subpage_button.setEnabled(item_type == 'subpage' and callable(self._apply_subpage_callback))
            self._merge_button.setEnabled(
                (item_type == 'page' and callable(self._merge_page_callback))
                or
                (item_type == 'subpage' and callable(self._merge_subpage_callback))
            )
            self._add_row_button.setEnabled(item_type == 'subpage' and callable(self._add_row_callback))
            self._preview_button.setEnabled(item_type in {'page', 'subpage'})

        def _open_source_file(self):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Open T42 File',
                os.getcwd(),
                'Teletext packet files (*.t42);;All files (*)',
            )
            if not filename:
                return
            try:
                entries = load_t42_entries(filename)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'Source T42', str(exc))
                return
            if not entries:
                QtWidgets.QMessageBox.warning(self, 'Source T42', 'Selected file does not contain any complete packets.')
                return
            self._source_path = filename
            self._entries = tuple(entries)
            self._file_label.setText(filename)
            self._file_label.setToolTip(filename)
            self._rebuild_tree()

        def _rebuild_tree(self):
            self._tree.clear()
            occurrences = page_subpage_occurrences(self._entries, mode=self._hidden_subpages_mode())
            for page_summary in summarise_t42_pages(self._entries):
                page_item = QtWidgets.QTreeWidgetItem([
                    _page_label(page_summary['page_number']),
                    str(page_summary['packet_count']),
                    str(int(page_summary.get('first_packet', 0))),
                    page_summary['header_title'],
                ])
                page_item.setData(0, QtCore.Qt.UserRole, 'page')
                page_item.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                self._tree.addTopLevelItem(page_item)
                for subpage_summary in page_summary['subpages']:
                    subpage_number = int(subpage_summary['subpage_number'])
                    variants = tuple(
                        occurrences.get(int(page_summary['page_number']), {}).get(subpage_number, ())
                    ) or ({
                        'label': f'{subpage_number:04X}',
                        'occurrence': 1,
                        'header_title': subpage_summary['header_title'],
                    },)
                    if not self._show_hidden_subpages_toggle.isChecked():
                        variants = variants[:1]
                    for variant in variants:
                        child = QtWidgets.QTreeWidgetItem([
                            str(variant['label']),
                            str(int(variant.get('packet_count') or subpage_summary['packet_count'])),
                            str(int(variant.get('first_packet') or subpage_summary.get('first_packet', 0))),
                            str(variant.get('header_title') or subpage_summary['header_title']),
                        ])
                        child.setData(0, QtCore.Qt.UserRole, 'subpage')
                        child.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                        child.setData(0, QtCore.Qt.UserRole + 2, subpage_number)
                        child.setData(0, QtCore.Qt.UserRole + 3, int(variant.get('occurrence') or 1))
                        page_item.addChild(child)
                page_item.setExpanded(True)
            if self._tree.topLevelItemCount():
                self._tree.setCurrentItem(self._tree.topLevelItem(0))
            self._sync_buttons()

        def _hidden_subpages_mode(self):
            return str(self._hidden_subpages_mode_combo.currentData() or 'legacy')

        def _selected_context(self):
            item = self._tree.currentItem()
            if item is None:
                return None
            item_type = item.data(0, QtCore.Qt.UserRole)
            if item_type == 'page':
                return ('page', int(item.data(0, QtCore.Qt.UserRole + 1)), None, 1)
            if item_type == 'subpage':
                return (
                    'subpage',
                    int(item.data(0, QtCore.Qt.UserRole + 1)),
                    int(item.data(0, QtCore.Qt.UserRole + 2)),
                    int(item.data(0, QtCore.Qt.UserRole + 3) or 1),
                )
            return None

        def _entries_for_context(self, context):
            if context is None:
                return ()
            item_type, page_number, subpage_number, occurrence_number = context
            if item_type == 'page':
                return collect_page_entries(self._entries, page_number)
            if item_type == 'subpage':
                return collect_subpage_occurrence_entries(
                    self._entries,
                    page_number,
                    subpage_number,
                    occurrence_number,
                ) or collect_subpage_entries(self._entries, page_number, subpage_number)
            return ()

        def _preview_selected(self, *_args):
            context = self._selected_context()
            if context is None or not callable(self._preview_callback):
                return
            item_type, page_number, subpage_number, _occurrence_number = context
            entries = self._entries_for_context(context)
            if item_type == 'page':
                subpage_number = None
            self._preview_callback(entries or self._entries, self._source_path, page_number, subpage_number)

        def _import_page(self):
            context = self._selected_context()
            if context is None or context[0] != 'page' or not callable(self._apply_page_callback):
                return
            _item_type, source_page_number, _subpage_number, _occurrence_number = context
            try:
                target_page_number = parse_page_identifier(self._target_page_input.text()) if self._target_page_input.text().strip() else source_page_number
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Source T42', str(exc))
                return
            self._apply_page_callback(self._entries, source_page_number, target_page_number)

        def _import_subpage(self):
            context = self._selected_context()
            if context is None or context[0] != 'subpage' or not callable(self._apply_subpage_callback):
                return
            _item_type, source_page_number, source_subpage_number, _occurrence_number = context
            try:
                target_page_number = parse_page_identifier(self._target_page_input.text()) if self._target_page_input.text().strip() else source_page_number
                target_subpage_number = parse_subpage_identifier(self._target_subpage_input.text()) if self._target_subpage_input.text().strip() else source_subpage_number
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Source T42', str(exc))
                return
            self._apply_subpage_callback(
                self._entries_for_context(context) or self._entries,
                source_page_number,
                source_subpage_number,
                target_page_number,
                target_subpage_number,
            )

        def _merge_selected(self):
            context = self._selected_context()
            if context is None:
                return
            item_type, source_page_number, source_subpage_number, _occurrence_number = context
            try:
                target_page_number = parse_page_identifier(self._target_page_input.text()) if self._target_page_input.text().strip() else source_page_number
                target_subpage_number = parse_subpage_identifier(self._target_subpage_input.text()) if self._target_subpage_input.text().strip() else source_subpage_number
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Source T42', str(exc))
                return

            if item_type == 'page':
                if callable(self._merge_page_callback):
                    self._merge_page_callback(self._entries, source_page_number, target_page_number)
                return

            if callable(self._merge_subpage_callback):
                self._merge_subpage_callback(
                    self._entries_for_context(context) or self._entries,
                    source_page_number,
                    source_subpage_number,
                    target_page_number,
                    target_subpage_number,
                )

        def _add_row(self):
            context = self._selected_context()
            if context is None or context[0] != 'subpage' or not callable(self._add_row_callback):
                return
            _item_type, source_page_number, source_subpage_number, _occurrence_number = context
            try:
                target_page_number = parse_page_identifier(self._target_page_input.text()) if self._target_page_input.text().strip() else source_page_number
                target_subpage_number = parse_subpage_identifier(self._target_subpage_input.text()) if self._target_subpage_input.text().strip() else source_subpage_number
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'Source T42', str(exc))
                return
            self._add_row_callback(
                self._entries_for_context(context) or self._entries,
                source_page_number,
                source_subpage_number,
                int(self._source_row_box.value()),
                target_page_number,
                target_subpage_number,
                int(self._target_row_box.value()),
            )


    class T42ToolWindow(QtWidgets.QDialog):
        def __init__(self, input_path, entries, save_callback=None, parent=None):
            super().__init__(parent)
            self._input_path = input_path
            self._entries = tuple(entries)
            self._initial_input_path = str(input_path) if input_path else None
            self._initial_entries = tuple(entries)
            self._headers = collect_t42_headers(self._entries)
            self._total_packets = max(len(self._entries), 1)
            self._current_packet = 0
            self._selection_start = 0
            self._selection_end = max(self._total_packets - 1, 0)
            self._cut_ranges = ()
            self._keep_ranges = ()
            self._insertions = ()
            self._deleted_pages = frozenset()
            self._deleted_subpages = frozenset()
            self._enabled_subpage_occurrences = set()
            self._save_callback = save_callback
            self._updating = False
            self._history = []
            self._redo_history = []
            self._cache_dirty = True
            self._page_tree_dirty = True
            self._pages_hidden = False
            self._page_tree_item_change_locked = False
            self._pending_tree_selection = None
            self._selected_cut_index = None
            self._cuts_render_state = None
            self._selected_insertion_index = None
            self._insertions_render_state = None
            self._source_dialog = None
            self._preview_windows = []
            self._preview_temp_paths = set()
            self._playing = False
            self._playback_direction = 1
            self._playback_speed = DEFAULT_PLAYBACK_SPEED
            self._playback_last_tick = time.monotonic()
            self._sync_ui_scheduled = False
            self._cached_combined_entries = ()
            self._cached_edited_entries = ()
            self._cached_deleted_packet_count = 0
            self._preview_mode = 'row0'
            self._terminal_window = None

            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)

            self.setWindowTitle(f'T42 Tool - {self._window_display_name()}')
            self.resize(960, 660)
            self.setMinimumSize(900, 520)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            self._status_label = QtWidgets.QLabel('')
            root.addWidget(self._status_label)

            timeline_group = QtWidgets.QGroupBox('Current Packet')
            timeline_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
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
            selection_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            selection_layout = QtWidgets.QGridLayout(selection_group)
            selection_layout.setColumnStretch(4, 1)
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
            selection_layout.addWidget(self._mark_start_button, 1, 5)

            self._mark_end_button = QtWidgets.QPushButton('Mark End')
            self._mark_end_button.clicked.connect(self._mark_end)
            selection_layout.addWidget(self._mark_end_button, 1, 6)

            self._delete_button = QtWidgets.QPushButton('Delete Selection')
            self._delete_button.clicked.connect(self._delete_selection)
            selection_layout.addWidget(self._delete_button, 1, 7)

            self._keep_button = QtWidgets.QPushButton('Keep Selection')
            self._keep_button.clicked.connect(self._keep_selection)
            selection_layout.addWidget(self._keep_button, 1, 8)

            self._selection_start_button = QtWidgets.QPushButton('Sel Start')
            self._selection_start_button.clicked.connect(self._jump_selection_start)
            selection_layout.addWidget(self._selection_start_button, 2, 6)

            self._selection_mid_button = QtWidgets.QPushButton('Sel Mid')
            self._selection_mid_button.clicked.connect(self._jump_selection_middle)
            selection_layout.addWidget(self._selection_mid_button, 2, 7)

            self._selection_end_button = QtWidgets.QPushButton('Sel End')
            self._selection_end_button.clicked.connect(self._jump_selection_end)
            selection_layout.addWidget(self._selection_end_button, 2, 8)

            selection_layout.addWidget(QtWidgets.QLabel('Cuts'), 3, 0)
            self._cuts_scroll = QtWidgets.QScrollArea()
            self._cuts_scroll.setWidgetResizable(True)
            self._cuts_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            self._cuts_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._cuts_scroll.setMinimumHeight(40)
            self._cuts_scroll.setMaximumHeight(48)
            self._cuts_container = QtWidgets.QWidget()
            self._cuts_layout = QtWidgets.QHBoxLayout(self._cuts_container)
            self._cuts_layout.setContentsMargins(0, 0, 0, 0)
            self._cuts_layout.setSpacing(6)
            self._cuts_scroll.setWidget(self._cuts_container)
            selection_layout.addWidget(self._cuts_scroll, 3, 1, 1, 8)

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
            self._insertions_scroll.setMinimumHeight(40)
            self._insertions_scroll.setMaximumHeight(48)
            self._insertions_container = QtWidgets.QWidget()
            self._insertions_layout = QtWidgets.QHBoxLayout(self._insertions_container)
            self._insertions_layout.setContentsMargins(0, 0, 0, 0)
            self._insertions_layout.setSpacing(6)
            self._insertions_scroll.setWidget(self._insertions_container)
            selection_layout.addWidget(self._insertions_scroll, 5, 1, 1, 8)

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
            root.addStretch(1)

            terminal_row = QtWidgets.QHBoxLayout()
            root.addLayout(terminal_row)
            self._terminal_button = QtWidgets.QPushButton('Terminal')
            self._terminal_button.clicked.connect(self._show_terminal_window)
            terminal_row.addWidget(self._terminal_button)
            terminal_row.addStretch(1)

            self._build_terminal_window()

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

            self._playback_timer = QtCore.QTimer(self)
            self._playback_timer.setInterval(40)
            self._playback_timer.timeout.connect(self._advance_playback)
            self._playback_timer.start()

            self._sync_enabled_subpage_occurrences(self._entries, preserve=False)
            self._record_history_state(reset_redo=True)
            self._sync_ui()
            QtCore.QTimer.singleShot(0, self._show_terminal_window)

        def _window_display_name(self):
            if self._input_path:
                return os.path.basename(self._input_path)
            return 'Untitled'

        def _dialog_parent(self):
            return self._terminal_window if self._terminal_window is not None else self

        def _save_entries_as_t42(self, entries, default_name, title):
            if not entries:
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Nothing to save.')
                return
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self._dialog_parent(),
                title,
                os.path.join(os.getcwd(), default_name),
                'Teletext packet files (*.t42);;All files (*)',
            )
            if not filename:
                return
            try:
                write_t42_entries(entries, filename)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', f'Saved T42 to:\n{filename}')

        def _save_entries_as_html(self, entries, page_number, subpage_number, default_name, title):
            entries = tuple(entries)
            if not entries:
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Nothing to save.')
                return
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self._dialog_parent(),
                title,
                os.path.join(os.getcwd(), default_name),
                'HTML files (*.html);;All files (*)',
            )
            if not filename:
                return
            if not filename.lower().endswith('.html'):
                filename += '.html'
            try:
                packets = (Packet(entry.raw, number=index) for index, entry in enumerate(entries))
                service = Service.from_packets(packets)
                from teletext.viewer import export_selected_html
                export_selected_html(
                    service,
                    filename,
                    int(page_number),
                    None if subpage_number is None else int(subpage_number),
                )
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', f'Saved HTML to:\n{filename}')

        def _entries_for_selected_tree_context(self, context):
            if context is None:
                return ()
            self._ensure_edit_cache()
            page_number = int(context['page_number'])
            if context['type'] == 'page':
                return collect_page_entries(self._cached_combined_entries, page_number)
            subpage_number = int(context['subpage_number'])
            occurrence_number = int(context.get('occurrence_number') or 1)
            return collect_subpage_occurrence_entries(
                self._cached_combined_entries,
                page_number,
                subpage_number,
                occurrence_number,
            ) or collect_subpage_entries(
                self._cached_combined_entries,
                page_number,
                subpage_number,
            )

        def _save_selected_page(self):
            context = self._selected_tree_context()
            if context is None:
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Select a page or subpage first.')
                return
            page_number = int(context['page_number'])
            entries = self._entries_for_selected_tree_context({'type': 'page', 'page_number': page_number})
            self._save_entries_as_t42(entries, f'{_page_label(page_number)}.t42', 'Save Page T42')

        def _save_selected_subpage(self):
            context = self._selected_tree_context()
            if context is None or context['type'] != 'subpage':
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Select a subpage first.')
                return
            page_number = int(context['page_number'])
            subpage_number = int(context['subpage_number'])
            occurrence_number = int(context.get('occurrence_number') or 1)
            entries = self._entries_for_selected_tree_context(context)
            self._save_entries_as_t42(
                entries,
                f'{_page_label(page_number)}-{subpage_number:04X}{"-occ%d" % occurrence_number if occurrence_number > 1 else ""}.t42',
                'Save Subpage T42',
            )

        def _save_selected_page_html(self):
            context = self._selected_tree_context()
            if context is None:
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Select a page first.')
                return
            page_number = int(context['page_number'])
            entries = self._entries_for_selected_tree_context({'type': 'page', 'page_number': page_number})
            self._save_entries_as_html(entries, page_number, None, f'{_page_label(page_number)}.html', 'Save Page HTML')

        def _save_selected_subpage_html(self):
            context = self._selected_tree_context()
            if context is None or context['type'] != 'subpage':
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Select a subpage first.')
                return
            page_number = int(context['page_number'])
            subpage_number = int(context['subpage_number'])
            occurrence_number = int(context.get('occurrence_number') or 1)
            entries = self._entries_for_selected_tree_context(context)
            self._save_entries_as_html(
                entries,
                page_number,
                subpage_number,
                f'{_page_label(page_number)}-{subpage_number:04X}{"-occ%d" % occurrence_number if occurrence_number > 1 else ""}.html',
                'Save Subpage HTML',
            )

        def _save_selected_entry(self):
            context = self._selected_tree_context()
            if context is None:
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Select a page or subpage first.')
                return
            if context['type'] == 'subpage':
                self._save_selected_subpage()
            else:
                self._save_selected_page()

        def _target_occurrence_number(self, target_page_number, target_subpage_number):
            context = self._selected_tree_context()
            if context is None or context['type'] != 'subpage':
                return 1
            if (
                int(context['page_number']) != int(target_page_number)
                or int(context['subpage_number']) != int(target_subpage_number)
            ):
                return 1
            return max(int(context.get('occurrence_number') or 1), 1)

        def _build_terminal_window(self):
            terminal_window = QtWidgets.QDialog(self)
            terminal_window.setWindowFlags(_standard_window_flags())
            terminal_window.setModal(False)
            terminal_window.setWindowModality(QtCore.Qt.NonModal)
            terminal_window.setWindowTitle('T42 Terminal')
            terminal_window.resize(900, 620)
            terminal_window.setMinimumSize(620, 420)
            self._terminal_window = terminal_window

            terminal_root = QtWidgets.QVBoxLayout(terminal_window)
            terminal_root.setContentsMargins(10, 10, 10, 10)
            terminal_root.setSpacing(8)

            split_controls = QtWidgets.QHBoxLayout()
            terminal_root.addLayout(split_controls)
            split_controls.addStretch(1)
            self._terminal_undo_button = QtWidgets.QPushButton('Undo')
            self._terminal_undo_button.clicked.connect(self._undo)
            split_controls.addWidget(self._terminal_undo_button)
            self._terminal_redo_button = QtWidgets.QPushButton('Redo')
            self._terminal_redo_button.clicked.connect(self._redo)
            split_controls.addWidget(self._terminal_redo_button)
            self._terminal_reset_button = QtWidgets.QPushButton('Reset')
            self._terminal_reset_button.clicked.connect(self._reset_selection)
            split_controls.addWidget(self._terminal_reset_button)
            self._show_hidden_subpages_toggle = QtWidgets.QCheckBox('Hidden Subpages')
            self._show_hidden_subpages_toggle.toggled.connect(self._hidden_subpages_toggled)
            split_controls.addWidget(self._show_hidden_subpages_toggle)
            split_controls.addWidget(QtWidgets.QLabel('Mode'))
            self._hidden_subpages_mode_combo = QtWidgets.QComboBox()
            self._hidden_subpages_mode_combo.addItem('Legacy', 'legacy')
            self._hidden_subpages_mode_combo.addItem('Exact', 'raw')
            self._hidden_subpages_mode_combo.currentIndexChanged.connect(self._hidden_subpages_mode_changed)
            split_controls.addWidget(self._hidden_subpages_mode_combo)
            self._show_tree_checkboxes_toggle = QtWidgets.QCheckBox('Checkboxes')
            self._show_tree_checkboxes_toggle.setChecked(False)
            self._show_tree_checkboxes_toggle.toggled.connect(self._toggle_page_tree_checkboxes)
            split_controls.addWidget(self._show_tree_checkboxes_toggle)

            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            self._splitter = split
            split.setMinimumHeight(260)
            terminal_root.addWidget(split, 1)

            preview_group = QtWidgets.QGroupBox('Packet Preview')
            preview_group.setMinimumHeight(240)
            preview_layout = QtWidgets.QVBoxLayout(preview_group)
            preview_mode_row = QtWidgets.QHBoxLayout()
            preview_layout.addLayout(preview_mode_row)
            preview_mode_row.addWidget(QtWidgets.QLabel('Mode'))
            self._preview_mode_box = QtWidgets.QComboBox()
            self._preview_mode_box.addItem('All Rows', 'rows')
            self._preview_mode_box.addItem('Row 0 (-r 0)', 'row0')
            self._preview_mode_box.addItem('Row 0 (Full)', 'row0full')
            self._preview_mode_box.currentIndexChanged.connect(self._preview_mode_changed)
            preview_mode_row.addWidget(self._preview_mode_box)
            preview_mode_row.addStretch(1)
            self._preview_text = QtWidgets.QPlainTextEdit()
            self._preview_text.setReadOnly(True)
            self._preview_text.setMinimumHeight(220)
            preview_layout.addWidget(self._preview_text)
            split.addWidget(preview_group)

            pages_group = QtWidgets.QGroupBox('Pages / Subpages')
            self._pages_group = pages_group
            pages_group.setMinimumHeight(240)
            pages_layout = QtWidgets.QVBoxLayout(pages_group)
            self._page_tree = QtWidgets.QTreeWidget()
            self._page_tree.setMinimumHeight(220)
            self._page_tree.setHeaderLabels(['Entry', 'Packets', 'At', 'Row 0'])
            self._page_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            _configure_tree_widget_columns(self._page_tree)
            self._page_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self._page_tree.customContextMenuRequested.connect(self._show_page_tree_context_menu)
            self._page_tree.itemChanged.connect(self._page_tree_item_changed)
            self._page_tree.itemSelectionChanged.connect(self._update_page_selection_buttons)
            self._page_tree.itemPressed.connect(self._page_tree_pressed)
            self._page_tree.itemDoubleClicked.connect(self._page_tree_item_double_clicked)
            pages_layout.addWidget(self._page_tree, 1)

            tree_button_row = QtWidgets.QGridLayout()
            tree_button_row.setHorizontalSpacing(6)
            tree_button_row.setVerticalSpacing(6)
            tree_button_row.setColumnStretch(3, 1)
            pages_layout.addLayout(tree_button_row)
            self._source_button = QtWidgets.QPushButton('Source T42...')
            self._source_button.clicked.connect(self._open_source_dialog)
            tree_button_row.addWidget(self._source_button, 0, 0)
            self._import_page_button = QtWidgets.QPushButton('Import/Replace Page...')
            self._import_page_button.clicked.connect(self._import_page)
            tree_button_row.addWidget(self._import_page_button, 0, 1)
            self._import_subpage_button = QtWidgets.QPushButton('Import/Replace Subpage...')
            self._import_subpage_button.clicked.connect(self._import_subpage)
            tree_button_row.addWidget(self._import_subpage_button, 0, 2)
            self._edit_page_button = QtWidgets.QPushButton('Edit Page/Subpage...')
            self._edit_page_button.clicked.connect(self._edit_selected_page_entry)
            tree_button_row.addWidget(self._edit_page_button, 1, 0)
            self._delete_page_button = QtWidgets.QPushButton('Delete Page/Subpage')
            self._delete_page_button.clicked.connect(self._delete_selected_page_entry)
            tree_button_row.addWidget(self._delete_page_button, 1, 1)
            self._save_entry_button = QtWidgets.QPushButton('Save Page/Subpage...')
            self._save_entry_button.clicked.connect(self._save_selected_entry)
            tree_button_row.addWidget(self._save_entry_button, 1, 2)
            self._add_blank_page_button = QtWidgets.QPushButton('Add Blank Page...')
            self._add_blank_page_button.clicked.connect(self._add_blank_page)
            tree_button_row.addWidget(self._add_blank_page_button, 2, 0)
            self._add_blank_subpage_button = QtWidgets.QPushButton('Add Blank Subpage...')
            self._add_blank_subpage_button.clicked.connect(self._add_blank_subpage)
            tree_button_row.addWidget(self._add_blank_subpage_button, 2, 1)
            self._add_hidden_subpage_button = QtWidgets.QPushButton('Add Hidden Subpage...')
            self._add_hidden_subpage_button.clicked.connect(self._add_blank_hidden_subpage)
            tree_button_row.addWidget(self._add_hidden_subpage_button, 2, 2)
            self._delete_page_button.setEnabled(False)
            self._edit_page_button.setEnabled(False)
            self._save_entry_button.setEnabled(False)

            split.addWidget(pages_group)
            split.setStretchFactor(0, 3)
            split.setStretchFactor(1, 2)
            split.setSizes([640, 420])
            self._toggle_page_tree_checkboxes(self._show_tree_checkboxes_toggle.isChecked())

        def _position_terminal_window(self):
            if self._terminal_window is None:
                return
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            main_frame = self.frameGeometry()
            width = min(max(self._terminal_window.width(), 900), max(available.width() - 40, 620))
            height = min(self._terminal_window.height(), max(available.height() - 40, 420))
            target_x = main_frame.x() + (main_frame.width() - width) // 2
            target_y = main_frame.y() + (main_frame.height() - height) // 2
            target_x = max(available.left(), min(target_x, available.right() - width + 1))
            target_y = max(available.top(), min(target_y, available.bottom() - height + 1))
            self._terminal_window.resize(width, height)
            self._terminal_window.move(target_x, target_y)

        def _show_terminal_window(self):
            if self._terminal_window is None:
                return
            if self._page_tree_dirty:
                self._refresh_page_tree()
            self._position_terminal_window()
            self._terminal_window.show()
            self._terminal_window.raise_()
            self._terminal_window.activateWindow()

        def _toggle_page_tree_checkboxes(self, enabled):
            if not hasattr(self, '_page_tree'):
                return
            if enabled:
                self._page_tree.setStyleSheet('')
            else:
                self._page_tree.setStyleSheet(
                    'QTreeView::indicator { width: 0px; height: 0px; }'
                )

        def _preview_mode_changed(self, _index):
            if self._updating:
                return
            self._preview_mode = str(self._preview_mode_box.currentData() or 'rows')
            self._sync_ui()

        def _current_preview_text(self, current_packet):
            if str(self._preview_mode) == 'row0':
                return header_preview_text(self._entries, self._headers, current_packet)
            if str(self._preview_mode) == 'row0full':
                return full_header_preview_text(self._entries, self._headers, current_packet)
            return frame_preview_text(self._entries, current_packet)

        def _capture_snapshot(self):
            return (
                str(self._input_path) if self._input_path else '',
                tuple(self._entries),
                int(self._current_packet),
                int(self._selection_start),
                int(self._selection_end),
                tuple(self._cut_ranges),
                tuple(self._keep_ranges),
                tuple(self._insertions),
                frozenset(self._deleted_pages),
                frozenset(self._deleted_subpages),
                tuple(sorted(
                    (int(page), int(subpage), int(occurrence))
                    for page, subpage, occurrence in self._enabled_subpage_occurrences
                )),
            )

        def _record_history_state(self, reset_redo=False):
            snapshot = self._capture_snapshot()
            if not self._history or self._history[-1] != snapshot:
                self._history.append(snapshot)
            if reset_redo:
                self._redo_history.clear()
            self._update_history_buttons()

        def _restore_snapshot(self, snapshot):
            self._updating = True
            self._input_path = str(snapshot[0]) or None
            self._entries = tuple(snapshot[1])
            self._headers = collect_t42_headers(self._entries)
            self._total_packets = max(len(self._entries), 1)
            self.setWindowTitle(f'T42 Tool - {self._window_display_name()}')
            self._packet_slider.setRange(0, self._total_packets - 1)
            self._packet_box.setRange(0, self._total_packets - 1)
            self._start_box.setRange(0, self._total_packets - 1)
            self._end_box.setRange(0, self._total_packets - 1)
            self._range_slider.setRange(0, self._total_packets - 1)
            self._current_packet = int(snapshot[2])
            self._selection_start = int(snapshot[3])
            self._selection_end = int(snapshot[4])
            self._cut_ranges = tuple(snapshot[5])
            if len(snapshot) > 9:
                self._keep_ranges = tuple(snapshot[6])
                self._insertions = tuple(snapshot[7])
                self._deleted_pages = frozenset(snapshot[8])
                self._deleted_subpages = frozenset(snapshot[9])
                self._enabled_subpage_occurrences = {
                    (int(page), int(subpage), int(occurrence))
                    for page, subpage, occurrence in snapshot[10]
                } if len(snapshot) > 10 else set()
            else:
                self._keep_ranges = ()
                self._insertions = tuple(snapshot[6])
                self._deleted_pages = frozenset(snapshot[7])
                self._deleted_subpages = frozenset(snapshot[8])
                self._enabled_subpage_occurrences = set()
            self._selected_cut_index = None
            self._selected_insertion_index = None
            self._pending_tree_selection = None
            self._cache_dirty = True
            self._page_tree_dirty = True
            self._updating = False
            self._sync_ui()

        def _rebase_entries(
            self,
            entries,
            *,
            focus_page_number=None,
            focus_subpage_number=None,
            focus_occurrence_number=1,
            enable_occurrences=(),
        ):
            self._updating = True
            self._entries = tuple(entries)
            self._headers = collect_t42_headers(self._entries)
            self._total_packets = max(len(self._entries), 1)
            self._cut_ranges = ()
            self._insertions = ()
            self._selected_cut_index = None
            self._selected_insertion_index = None
            self._deleted_pages = frozenset()
            self._deleted_subpages = frozenset()
            self._sync_enabled_subpage_occurrences(self._entries, preserve=True)
            for page_number, subpage_number, occurrence_number in tuple(enable_occurrences or ()):
                self._enabled_subpage_occurrences.add(
                    (int(page_number), int(subpage_number), max(int(occurrence_number), 1))
                )
            self._packet_slider.setRange(0, self._total_packets - 1)
            self._packet_box.setRange(0, self._total_packets - 1)
            self._start_box.setRange(0, self._total_packets - 1)
            self._end_box.setRange(0, self._total_packets - 1)
            self._range_slider.setRange(0, self._total_packets - 1)
            self._selection_start = 0
            self._selection_end = max(self._total_packets - 1, 0)
            self._current_packet = 0
            self._pending_tree_selection = None
            if focus_page_number is not None:
                focus_page_number = int(focus_page_number)
                focus_occurrence_number = max(int(focus_occurrence_number or 1), 1)
                if focus_subpage_number is None:
                    self._pending_tree_selection = ('page', focus_page_number, None)
                else:
                    self._pending_tree_selection = ('subpage', focus_page_number, int(focus_subpage_number), focus_occurrence_number)
                for entry in self._entries:
                    if entry.page_number != focus_page_number:
                        continue
                    if focus_subpage_number is not None and entry.subpage_number != int(focus_subpage_number):
                        continue
                    self._current_packet = int(entry.packet_index)
                    self._selection_start = self._current_packet
                    self._selection_end = self._current_packet
                    break
            self._mark_cache_dirty()
            self._updating = False
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _update_history_buttons(self):
            can_undo = len(self._history) > 1
            can_redo = len(self._redo_history) > 0
            self._undo_button.setEnabled(can_undo)
            self._redo_button.setEnabled(can_redo)
            if hasattr(self, '_terminal_undo_button'):
                self._terminal_undo_button.setEnabled(can_undo)
            if hasattr(self, '_terminal_redo_button'):
                self._terminal_redo_button.setEnabled(can_redo)

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
            self._set_playing(False)
            self._current_packet = int(start)
            self._selection_start = int(start)
            self._selection_end = int(end)
            self._sync_ui()

        def _update_selected_cut(self):
            current_cut = self._current_selected_cut()
            if current_cut is None:
                return
            updated_range = (min(int(self._selection_start), int(self._selection_end)), max(int(self._selection_start), int(self._selection_end)))
            cut_ranges = list(self._cut_ranges)
            cut_ranges[int(self._selected_cut_index)] = updated_range
            self._keep_ranges = ()
            self._cut_ranges = normalise_cut_ranges(tuple(cut_ranges), self._total_packets)
            self._selected_cut_index = None
            for index, cut_range in enumerate(self._cut_ranges):
                if cut_range == updated_range:
                    self._selected_cut_index = index
                    break
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _remove_selected_cut(self):
            current_cut = self._current_selected_cut()
            if current_cut is None:
                return
            cut_ranges = list(self._cut_ranges)
            cut_ranges.pop(int(self._selected_cut_index))
            self._keep_ranges = ()
            self._cut_ranges = tuple(cut_ranges)
            if not self._cut_ranges:
                self._selected_cut_index = None
            elif int(self._selected_cut_index) >= len(self._cut_ranges):
                self._selected_cut_index = len(self._cut_ranges) - 1
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

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
                        int(insertion.after_packet),
                        str(insertion.path),
                        int(insertion.packet_count),
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
                    label = f'{pathlib.Path(insertion.path).name} @ {int(insertion.after_packet)}'
                    button = QtWidgets.QPushButton(label)
                    button.setCheckable(True)
                    button.setChecked(insertion_index == self._selected_insertion_index)
                    button.setToolTip(
                        f'{str(insertion.path)}\n'
                        f'After packet: {int(insertion.after_packet)}\n'
                        f'Packets: {int(insertion.packet_count)}'
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
            after_packet = int(insertion.after_packet)
            self._set_playing(False)
            self._current_packet = after_packet
            self._selection_start = after_packet
            self._selection_end = after_packet
            self._sync_ui()

        def _update_selected_insertion(self):
            current_insertion = self._current_selected_insertion()
            if current_insertion is None:
                return
            updated_insertion = T42Insertion(
                after_packet=int(self._selection_end),
                path=current_insertion.path,
                packet_count=int(current_insertion.packet_count),
                entries=tuple(current_insertion.entries),
            )
            insertions = list(self._insertions)
            insertions[int(self._selected_insertion_index)] = updated_insertion
            self._insertions = normalise_t42_insertions(tuple(insertions), self._total_packets)
            self._selected_insertion_index = None
            for index, insertion in enumerate(self._insertions):
                if insertion == updated_insertion:
                    self._selected_insertion_index = index
                    break
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

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
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _mark_cache_dirty(self):
            self._cache_dirty = True
            self._page_tree_dirty = True

        def _schedule_sync_ui(self):
            if self._sync_ui_scheduled:
                return
            self._sync_ui_scheduled = True
            QtCore.QTimer.singleShot(0, self._run_scheduled_sync_ui)

        def _run_scheduled_sync_ui(self):
            self._sync_ui_scheduled = False
            self._sync_ui()

        def _all_subpage_occurrence_keys(self, entries=None):
            entries = self._entries if entries is None else tuple(entries)
            counts = Counter()
            keys = []
            for entry in entries:
                if (
                    entry.page_number is None
                    or entry.subpage_number is None
                    or entry.row is None
                    or int(entry.row) != 0
                ):
                    continue
                page_number = int(entry.page_number)
                subpage_number = int(entry.subpage_number)
                counts[(page_number, subpage_number)] += 1
                keys.append((page_number, subpage_number, int(counts[(page_number, subpage_number)])))
            return tuple(keys)

        def _sync_enabled_subpage_occurrences(self, entries=None, *, preserve=True):
            available = self._all_subpage_occurrence_keys(entries)
            if not preserve:
                self._enabled_subpage_occurrences = {
                    (page_number, subpage_number, occurrence_number)
                    for page_number, subpage_number, occurrence_number in available
                }
                return
            current = set(self._enabled_subpage_occurrences)
            updated = set()
            for key in available:
                if key in current:
                    updated.add(key)
                elif int(key[2]) == 1:
                    updated.add(key)
            self._enabled_subpage_occurrences = updated

        def _is_subpage_occurrence_enabled(self, page_number, subpage_number, occurrence_number=1):
            return (
                int(page_number),
                int(subpage_number),
                max(int(occurrence_number or 1), 1),
            ) in self._enabled_subpage_occurrences

        def _ensure_edit_cache(self):
            if not self._cache_dirty:
                return
            self._cached_combined_entries = tuple(iterate_t42_entries(
                self._entries,
                cut_ranges=self._cut_ranges,
                insertions=self._insertions,
            ))
            enabled_entries = filter_enabled_occurrence_entries(
                self._cached_combined_entries,
                self._enabled_subpage_occurrences,
            )
            self._cached_edited_entries = tuple(filter_deleted_t42_entries(
                enabled_entries,
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
            self._speed_box.setValue(self._playback_speed)
            preview_index = max(self._preview_mode_box.findData(self._preview_mode), 0)
            self._preview_mode_box.setCurrentIndex(preview_index)
            self._play_button.setText('Pause' if self._playing and self._playback_direction > 0 else 'Play')
            self._reverse_button.setText('Pause Rev' if self._playing and self._playback_direction < 0 else 'Reverse')

            current_entry = self._entries[current_packet] if self._entries else None
            if current_entry is not None and current_entry.page_number is not None:
                label = _page_label(current_entry.page_number)
                if current_entry.subpage_number is not None:
                    label += f' / {current_entry.subpage_number:04X}'
            elif self._entries:
                label = 'unknown'
            else:
                label = 'empty'
            self._packet_page_label.setText(label)

            selection_packets = 0 if not self._entries else (selection_end - selection_start) + 1
            cut_packets = count_cut_frames(self._cut_ranges)
            inserted_packets = count_inserted_packets(self._insertions)
            deleted_packets = self._cached_deleted_packet_count
            edited_packets = len(self._cached_edited_entries)

            if self._entries:
                self._status_label.setText(f'{current_packet + 1}/{len(self._entries)} packets')
            else:
                self._status_label.setText('Empty project')
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
                selected_insertion = self._current_selected_insertion()
                if selected_insertion is not None:
                    self._insertions_label.setText(
                        'Insertions: '
                        f'{pathlib.Path(selected_insertion.path).name} -> after {int(selected_insertion.after_packet)} '
                        f'({int(selected_insertion.packet_count)} packets, {packet_count_to_megabytes(int(selected_insertion.packet_count)):.2f} MB) | '
                        f'{str(selected_insertion.path)}'
                    )
                else:
                    self._insertions_label.setText(
                        'Insertions: ' + ', '.join(
                            f'{pathlib.Path(insertion.path).name} -> after {insertion.after_packet} ({insertion.packet_count} packets)'
                            for insertion in self._insertions[-4:]
                        )
                    )
            else:
                self._insertions_label.setText('Insertions: none')
            self._refresh_cut_buttons()
            self._refresh_insertion_buttons()
            self._update_page_selection_buttons()

            self._preview_text.setPlainText(self._current_preview_text(current_packet))
            if self._page_tree_dirty:
                self._refresh_page_tree()

            self._updating = False
            self._update_history_buttons()

        def _refresh_page_tree(self):
            current_data = self._pending_tree_selection
            if current_data is None:
                current_item = self._page_tree.currentItem()
            else:
                current_item = None
            if current_item is not None:
                current_data = (
                    current_item.data(0, QtCore.Qt.UserRole),
                    current_item.data(0, QtCore.Qt.UserRole + 1),
                    current_item.data(0, QtCore.Qt.UserRole + 2),
                    current_item.data(0, QtCore.Qt.UserRole + 4),
                )

            self._page_tree_item_change_locked = True
            self._page_tree.clear()
            try:
                occurrences = page_subpage_occurrences(
                    self._cached_edited_entries,
                    mode=self._hidden_subpages_mode(),
                )
                for page_summary in summarise_t42_pages(self._cached_edited_entries):
                    page_number = int(page_summary['page_number'])
                    page_item = QtWidgets.QTreeWidgetItem([
                        _page_label(page_number),
                        str(page_summary['packet_count']),
                        str(int(page_summary.get('first_packet', 0))),
                        page_summary['header_title'],
                    ])
                    page_item.setFlags(page_item.flags() | QtCore.Qt.ItemIsUserCheckable)
                    page_item.setData(0, QtCore.Qt.UserRole, 'page')
                    page_item.setData(0, QtCore.Qt.UserRole + 1, page_number)
                    page_item.setData(0, QtCore.Qt.UserRole + 2, int(page_summary['first_packet']))
                    page_item.setData(0, QtCore.Qt.UserRole + 4, 1)
                    self._page_tree.addTopLevelItem(page_item)

                    visible_children = 0
                    checked_children = 0
                    seen_subpages = set()
                    for subpage_summary in page_summary['subpages']:
                        subpage_number = int(subpage_summary['subpage_number'])
                        variants = tuple(
                            occurrences.get(page_number, {}).get(subpage_number, ())
                        ) or ({
                            'label': f'{subpage_number:04X}',
                            'occurrence': 1,
                            'header_title': subpage_summary['header_title'],
                            'first_packet': int(subpage_summary['first_packet']),
                            'packet_count': int(subpage_summary['packet_count']),
                        },)
                        if not self._show_hidden_subpages_toggle.isChecked():
                            variants = variants[:1]
                        for variant in variants:
                            occurrence_number = max(int(variant.get('occurrence') or 1), 1)
                            child_checked = (
                                page_number not in self._deleted_pages
                                and (
                                    occurrence_number > 1
                                    or (page_number, subpage_number) not in self._deleted_subpages
                                )
                                and self._is_subpage_occurrence_enabled(
                                    page_number,
                                    subpage_number,
                                    occurrence_number,
                                )
                            )
                            child = QtWidgets.QTreeWidgetItem([
                                str(variant.get('label') or f'{subpage_number:04X}'),
                                str(int(variant.get('packet_count') or subpage_summary['packet_count'])),
                                str(int(variant.get('first_packet') or subpage_summary.get('first_packet', 0))),
                                str(variant.get('header_title') or subpage_summary['header_title']),
                            ])
                            child.setFlags(child.flags() | QtCore.Qt.ItemIsUserCheckable)
                            child.setData(0, QtCore.Qt.UserRole, 'subpage')
                            child.setData(0, QtCore.Qt.UserRole + 1, page_number)
                            child.setData(0, QtCore.Qt.UserRole + 2, subpage_number)
                            child.setData(0, QtCore.Qt.UserRole + 3, int(variant.get('first_packet') or subpage_summary['first_packet']))
                            child.setData(0, QtCore.Qt.UserRole + 4, occurrence_number)
                            child.setCheckState(0, QtCore.Qt.Checked if child_checked else QtCore.Qt.Unchecked)
                            page_item.addChild(child)
                            if subpage_number not in seen_subpages:
                                seen_subpages.add(subpage_number)
                                visible_children += 1
                            if child_checked:
                                checked_children += 1

                    total_children = page_item.childCount()
                    if total_children and checked_children == total_children:
                        page_item.setCheckState(0, QtCore.Qt.Checked)
                    elif checked_children == 0:
                        page_item.setCheckState(0, QtCore.Qt.Unchecked)
                    else:
                        page_item.setCheckState(0, QtCore.Qt.PartiallyChecked)
                    page_item.setExpanded(True)
            finally:
                self._page_tree_item_change_locked = False

            if current_data is not None:
                self._restore_tree_selection(current_data)
            self._pending_tree_selection = None
            self._page_tree_dirty = False
            _configure_tree_widget_columns(self._page_tree)

        def _hidden_subpages_toggled(self, _checked):
            self._page_tree_dirty = True
            self._refresh_page_tree()

        def _hidden_subpages_mode(self):
            return str(self._hidden_subpages_mode_combo.currentData() or 'legacy')

        def _hidden_subpages_mode_changed(self, _index):
            self._page_tree_dirty = True
            self._refresh_page_tree()

        def _restore_tree_selection(self, current_data):
            if len(current_data) >= 4:
                item_type, value1, value2, occurrence_number = current_data[:4]
            else:
                item_type, value1, value2 = current_data
                occurrence_number = 1
            for page_index in range(self._page_tree.topLevelItemCount()):
                page_item = self._page_tree.topLevelItem(page_index)
                if item_type == 'page' and (
                    page_item.data(0, QtCore.Qt.UserRole) == 'page'
                    and page_item.data(0, QtCore.Qt.UserRole + 1) == value1
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
                            and int(child.data(0, QtCore.Qt.UserRole + 4) or 1) == int(occurrence_number or 1)
                        ):
                            self._page_tree.setCurrentItem(child)
                            return

        def _selected_tree_context(self):
            item = self._page_tree.currentItem()
            if item is None:
                return None
            item_type = item.data(0, QtCore.Qt.UserRole)
            if item_type == 'page':
                return {
                    'type': 'page',
                    'page_number': int(item.data(0, QtCore.Qt.UserRole + 1)),
                    'subpage_number': None,
                }
            if item_type == 'subpage':
                return {
                    'type': 'subpage',
                    'page_number': int(item.data(0, QtCore.Qt.UserRole + 1)),
                    'subpage_number': int(item.data(0, QtCore.Qt.UserRole + 2)),
                    'occurrence_number': int(item.data(0, QtCore.Qt.UserRole + 4) or 1),
                }
            return None

        def _selected_tree_contexts(self):
            items = list(self._page_tree.selectedItems() or ())
            if not items:
                current = self._page_tree.currentItem()
                if current is not None:
                    items = [current]
            contexts = []
            seen = set()
            for item in items:
                item_type = item.data(0, QtCore.Qt.UserRole)
                if item_type == 'page':
                    key = ('page', int(item.data(0, QtCore.Qt.UserRole + 1)))
                    if key in seen:
                        continue
                    seen.add(key)
                    contexts.append({
                        'type': 'page',
                        'page_number': key[1],
                        'subpage_number': None,
                        'occurrence_number': 1,
                    })
                elif item_type == 'subpage':
                    key = (
                        'subpage',
                        int(item.data(0, QtCore.Qt.UserRole + 1)),
                        int(item.data(0, QtCore.Qt.UserRole + 2)),
                        int(item.data(0, QtCore.Qt.UserRole + 4) or 1),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    contexts.append({
                        'type': 'subpage',
                        'page_number': key[1],
                        'subpage_number': key[2],
                        'occurrence_number': key[3],
                    })
            return tuple(contexts)

        def _page_subpage_occurrences_for_entries(self, entries, page_number):
            page_number = int(page_number)
            return page_subpage_occurrences(
                tuple(entries),
                page_number,
                mode=self._hidden_subpages_mode(),
            ).get(page_number, {})

        def _page_has_hidden_subpages_terminal(self, page_number):
            self._ensure_edit_cache()
            return any(
                len(tuple(variants)) > 1
                for variants in self._page_subpage_occurrences_for_entries(
                    self._cached_combined_entries,
                    page_number,
                ).values()
            )

        def _convert_selected_hidden_subpage_to_real(self):
            contexts = tuple(
                context for context in self._selected_tree_contexts()
                if context['type'] == 'subpage' and int(context.get('occurrence_number') or 1) > 1
            )
            if not contexts:
                return
            self._ensure_edit_cache()
            working_entries = tuple(self._cached_combined_entries)
            converted = []
            for page_number, subpage_number in sorted({
                (int(context['page_number']), int(context['subpage_number']))
                for context in contexts
            }):
                selected_occurrences = sorted(
                    {
                        int(context['occurrence_number'])
                        for context in contexts
                        if int(context['page_number']) == page_number and int(context['subpage_number']) == subpage_number
                    },
                    reverse=True,
                )
                for occurrence_number in selected_occurrences:
                    variants = tuple(self._page_subpage_occurrences_for_entries(working_entries, page_number).get(subpage_number, ()))
                    if occurrence_number <= 1 or occurrence_number > len(variants):
                        continue
                    working_entries, new_subpage_number = convert_subpage_occurrence_to_real(
                        working_entries,
                        page_number,
                        subpage_number,
                        occurrence_number,
                    )
                    converted.append((int(page_number), int(new_subpage_number)))
            if not converted:
                return
            focus_page_number, focus_subpage_number = converted[-1]
            self._rebase_entries(
                working_entries,
                focus_page_number=focus_page_number,
                focus_subpage_number=focus_subpage_number,
                focus_occurrence_number=1,
            )
            QtWidgets.QMessageBox.information(
                self._dialog_parent(),
                'T42 Tool',
                f'Converted {len(converted)} selected hidden subpage(s) to real.',
            )

        def _convert_page_hidden_subpages_to_real(self):
            context = self._selected_tree_context()
            if context is None:
                return
            page_number = int(context['page_number'])
            self._ensure_edit_cache()
            working_entries = tuple(self._cached_combined_entries)
            converted = []
            base_occurrences = self._page_subpage_occurrences_for_entries(working_entries, page_number)
            for base_subpage_number in sorted(base_occurrences):
                while len(tuple(self._page_subpage_occurrences_for_entries(working_entries, page_number).get(base_subpage_number, ()))) > 1:
                    working_entries, new_subpage_number = convert_subpage_occurrence_to_real(
                        working_entries,
                        page_number,
                        base_subpage_number,
                        2,
                    )
                    converted.append(int(new_subpage_number))
            if not converted:
                return
            self._rebase_entries(working_entries, focus_page_number=page_number)
            QtWidgets.QMessageBox.information(
                self._dialog_parent(),
                'T42 Tool',
                'Converted hidden subpages: ' + ', '.join(f'{value:04X}' for value in converted),
            )

        def _show_page_tree_context_menu(self, position):
            item = self._page_tree.itemAt(position)
            if item is None:
                return
            self._page_tree.setCurrentItem(item)
            context = self._selected_tree_context()
            if context is None:
                return
            selected_contexts = self._selected_tree_contexts()
            has_multi_selection = len(selected_contexts) > 1
            menu = QtWidgets.QMenu(self._page_tree)
            save_t42_action = menu.addAction('Save as T42...')
            save_t42_action.triggered.connect(self._save_selected_entry)
            save_html_action = menu.addAction('Save as HTML...')
            if context['type'] == 'subpage':
                save_html_action.triggered.connect(self._save_selected_subpage_html)
            else:
                save_html_action.triggered.connect(self._save_selected_page_html)
            menu.addSeparator()
            if context['type'] == 'page':
                add_blank_page_action = menu.addAction('Add Blank Page...')
                add_blank_page_action.triggered.connect(self._add_blank_page)
                add_blank_subpage_action = menu.addAction('Add Blank Subpage...')
                add_blank_subpage_action.triggered.connect(self._add_blank_subpage)
                add_hidden_action = menu.addAction('Add Blank Hidden Subpage...')
                add_hidden_action.triggered.connect(self._add_blank_hidden_subpage)
                menu.addSeparator()
                delete_action = menu.addAction('Delete Page')
                delete_action.triggered.connect(self._delete_selected_page_entry)
                if self._page_has_hidden_subpages_terminal(context['page_number']):
                    convert_action = menu.addAction('Convert Hidden Subpages to Real')
                    convert_action.triggered.connect(self._convert_page_hidden_subpages_to_real)
            else:
                add_blank_subpage_action = menu.addAction('Add Blank Subpage...')
                add_blank_subpage_action.triggered.connect(self._add_blank_subpage)
                add_hidden_action = menu.addAction('Add Blank Hidden Subpage...')
                add_hidden_action.triggered.connect(self._add_blank_hidden_subpage)
                menu.addSeparator()
                rename_action = menu.addAction('Change Page/Subpage Number...')
                rename_action.triggered.connect(self._edit_selected_page_entry)
                if int(context.get('occurrence_number') or 1) > 1:
                    move_action = menu.addAction('Change Hidden Sequence...')
                    move_action.triggered.connect(self._change_selected_hidden_subpage_sequence)
                menu.addSeparator()
                delete_action = menu.addAction('Delete Subpage')
                delete_action.triggered.connect(self._delete_selected_page_entry)
                if int(context.get('occurrence_number') or 1) > 1:
                    convert_action = menu.addAction('Convert Hidden to Real Subpage')
                    convert_action.triggered.connect(self._convert_selected_hidden_subpage_to_real)
            if has_multi_selection:
                menu.addSeparator()
                delete_selected_action = menu.addAction('Delete Selected')
                delete_selected_action.triggered.connect(self._delete_selected_page_entry)
                if any(
                    selected_context['type'] == 'subpage' and int(selected_context.get('occurrence_number') or 1) > 1
                    for selected_context in selected_contexts
                ):
                    convert_selected_action = menu.addAction('Convert Selected Hidden to Real')
                    convert_selected_action.triggered.connect(self._convert_selected_hidden_subpage_to_real)
            menu.exec_(self._page_tree.viewport().mapToGlobal(position))

        def _update_page_tree_item_check_state(self, page_item):
            if page_item is None:
                return
            enabled = 0
            disabled = 0
            for child_index in range(page_item.childCount()):
                child = page_item.child(child_index)
                if child.checkState(0) == QtCore.Qt.Checked:
                    enabled += 1
                else:
                    disabled += 1
            if enabled and not disabled:
                page_item.setCheckState(0, QtCore.Qt.Checked)
            elif disabled and not enabled:
                page_item.setCheckState(0, QtCore.Qt.Unchecked)
            else:
                page_item.setCheckState(0, QtCore.Qt.PartiallyChecked)

        def _page_tree_item_changed(self, item, column):
            if item is None or int(column) != 0 or self._page_tree_item_change_locked:
                return
            item_type = item.data(0, QtCore.Qt.UserRole)
            if item_type == 'page':
                page_number = item.data(0, QtCore.Qt.UserRole + 1)
                if page_number is None:
                    return
                page_number = int(page_number)
                checked = item.checkState(0) != QtCore.Qt.Unchecked
                updated_occurrences = set(self._enabled_subpage_occurrences)
                if checked:
                    self._deleted_pages = frozenset(
                        candidate for candidate in self._deleted_pages
                        if int(candidate) != page_number
                    )
                    self._deleted_subpages = frozenset(
                        key for key in self._deleted_subpages
                        if int(key[0]) != page_number
                    )
                    for key in self._all_subpage_occurrence_keys():
                        if int(key[0]) == page_number:
                            updated_occurrences.add(key)
                else:
                    self._deleted_pages = frozenset(set(self._deleted_pages) | {page_number})
                    self._deleted_subpages = frozenset(
                        key for key in self._deleted_subpages
                        if int(key[0]) != page_number
                    )
                    updated_occurrences = {
                        key for key in updated_occurrences
                        if int(key[0]) != page_number
                    }
                self._enabled_subpage_occurrences = updated_occurrences
                self._page_tree_item_change_locked = True
                try:
                    for child_index in range(item.childCount()):
                        child = item.child(child_index)
                        occurrence_number = int(child.data(0, QtCore.Qt.UserRole + 4) or 1)
                        child.setCheckState(
                            0,
                            QtCore.Qt.Checked
                            if (
                                checked
                                and self._is_subpage_occurrence_enabled(
                                    int(child.data(0, QtCore.Qt.UserRole + 1)),
                                    int(child.data(0, QtCore.Qt.UserRole + 2)),
                                    occurrence_number,
                                )
                            )
                            else QtCore.Qt.Unchecked,
                        )
                finally:
                    self._page_tree_item_change_locked = False
                self._mark_cache_dirty()
                self._record_history_state(reset_redo=True)
                self._schedule_sync_ui()
                return
            if item_type != 'subpage':
                return
            page_number = item.data(0, QtCore.Qt.UserRole + 1)
            subpage_number = item.data(0, QtCore.Qt.UserRole + 2)
            if page_number is None or subpage_number is None:
                return
            page_number = int(page_number)
            subpage_number = int(subpage_number)
            occurrence_number = int(item.data(0, QtCore.Qt.UserRole + 4) or 1)
            checked = item.checkState(0) == QtCore.Qt.Checked
            updated_occurrences = set(self._enabled_subpage_occurrences)
            if occurrence_number == 1:
                self._deleted_pages = frozenset(
                    candidate for candidate in self._deleted_pages
                    if int(candidate) != page_number
                )
                updated = set(self._deleted_subpages)
                if checked:
                    updated.discard((page_number, subpage_number))
                    updated_occurrences.add((page_number, subpage_number, 1))
                else:
                    updated.add((page_number, subpage_number))
                    updated_occurrences = {
                        key for key in updated_occurrences
                        if not (int(key[0]) == page_number and int(key[1]) == subpage_number)
                    }
                self._deleted_subpages = frozenset(updated)
            else:
                if checked:
                    updated_occurrences.add((page_number, subpage_number, occurrence_number))
                else:
                    updated_occurrences.discard((page_number, subpage_number, occurrence_number))
            self._enabled_subpage_occurrences = updated_occurrences
            self._page_tree_item_change_locked = True
            try:
                parent = item.parent()
                if parent is not None:
                    for child_index in range(parent.childCount()):
                        child = parent.child(child_index)
                        if (
                            int(child.data(0, QtCore.Qt.UserRole + 1)) == page_number
                            and int(child.data(0, QtCore.Qt.UserRole + 2)) == subpage_number
                        ):
                            child_occurrence = int(child.data(0, QtCore.Qt.UserRole + 4) or 1)
                            child.setCheckState(
                                0,
                                QtCore.Qt.Checked
                                if self._is_subpage_occurrence_enabled(page_number, subpage_number, child_occurrence)
                                and page_number not in self._deleted_pages
                                and (
                                    child_occurrence > 1
                                    or (page_number, subpage_number) not in self._deleted_subpages
                                )
                                else QtCore.Qt.Unchecked,
                            )
                    self._update_page_tree_item_check_state(parent)
            finally:
                self._page_tree_item_change_locked = False
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._schedule_sync_ui()

        def _update_page_selection_buttons(self):
            context = self._selected_tree_context()
            has_selection = context is not None
            is_subpage = has_selection and context['type'] == 'subpage'
            self._delete_page_button.setEnabled(has_selection)
            self._edit_page_button.setEnabled(has_selection)
            if hasattr(self, '_save_entry_button'):
                self._save_entry_button.setEnabled(has_selection)
            if hasattr(self, '_add_hidden_subpage_button'):
                self._add_hidden_subpage_button.setEnabled(True)
            if hasattr(self, '_add_blank_page_button'):
                self._add_blank_page_button.setEnabled(True)
            if hasattr(self, '_add_blank_subpage_button'):
                self._add_blank_subpage_button.setEnabled(True)

        def _add_blank_page(self):
            self._ensure_edit_cache()
            context = self._selected_tree_context()
            default_page_number = 0x100
            if context is not None:
                default_page_number = int(context['page_number'])
            page_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Add Blank Page',
                'Page (hex):',
                text=f"{(int(default_page_number) >> 8):X}{(int(default_page_number) & 0xFF):02X}",
            )
            if not accepted:
                return
            try:
                target_page_number = parse_page_identifier(page_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            target_subpage_number = 0x0000
            if tuple(self._page_subpage_occurrences_for_entries(self._cached_combined_entries, target_page_number).get(target_subpage_number, ())):
                self._rebase_entries(
                    self._cached_combined_entries,
                    focus_page_number=target_page_number,
                    focus_subpage_number=target_subpage_number,
                    focus_occurrence_number=1,
                )
                QtWidgets.QMessageBox.information(
                    self._dialog_parent(),
                    'T42 Tool',
                    f'{_page_label(target_page_number)} / {target_subpage_number:04X} already exists.',
                )
                return
            updated_entries = replace_subpage_in_entries(
                self._cached_combined_entries,
                blank_subpage_entries(target_page_number, target_subpage_number),
                target_page_number=target_page_number,
                target_subpage_number=target_subpage_number,
            )
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=1,
                enable_occurrences=((target_page_number, target_subpage_number, 1),),
            )
            QtWidgets.QMessageBox.information(
                self._dialog_parent(),
                'T42 Tool',
                f'Added blank page {_page_label(target_page_number)} / {target_subpage_number:04X}.',
            )

        def _add_blank_subpage(self):
            self._ensure_edit_cache()
            context = self._selected_tree_context()
            default_page_number = 0x100
            default_subpage_number = 0x0000
            if context is not None:
                default_page_number = int(context['page_number'])
                if context['type'] == 'subpage':
                    default_subpage_number = min(int(context['subpage_number']) + 1, 0x3F7F)
            page_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Add Blank Subpage',
                'Page (hex):',
                text=f"{(int(default_page_number) >> 8):X}{(int(default_page_number) & 0xFF):02X}",
            )
            if not accepted:
                return
            try:
                target_page_number = parse_page_identifier(page_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            subpage_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Add Blank Subpage',
                f'Subpage for {_page_label(target_page_number)} (hex):',
                text=f'{int(default_subpage_number):04X}',
            )
            if not accepted:
                return
            try:
                target_subpage_number = parse_subpage_identifier(subpage_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            if tuple(self._page_subpage_occurrences_for_entries(self._cached_combined_entries, target_page_number).get(target_subpage_number, ())):
                self._rebase_entries(
                    self._cached_combined_entries,
                    focus_page_number=target_page_number,
                    focus_subpage_number=target_subpage_number,
                    focus_occurrence_number=1,
                )
                QtWidgets.QMessageBox.information(
                    self._dialog_parent(),
                    'T42 Tool',
                    f'{_page_label(target_page_number)} / {target_subpage_number:04X} already exists.',
                )
                return
            updated_entries = replace_subpage_in_entries(
                self._cached_combined_entries,
                blank_subpage_entries(target_page_number, target_subpage_number),
                target_page_number=target_page_number,
                target_subpage_number=target_subpage_number,
            )
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=1,
                enable_occurrences=((target_page_number, target_subpage_number, 1),),
            )
            QtWidgets.QMessageBox.information(
                self._dialog_parent(),
                'T42 Tool',
                f'Added blank subpage {_page_label(target_page_number)} / {target_subpage_number:04X}.',
            )

        def _add_blank_hidden_subpage(self):
            self._ensure_edit_cache()
            context = self._selected_tree_context()
            default_page_number = 0x100
            default_subpage_number = 0x0000
            if context is not None:
                default_page_number = int(context['page_number'])
                if context['type'] == 'subpage':
                    default_subpage_number = int(context['subpage_number'])
            current_page_text = f"{(int(default_page_number) >> 8):X}{(int(default_page_number) & 0xFF):02X}"
            page_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Add Blank Hidden Subpage',
                'Page (hex):',
                text=current_page_text,
            )
            if not accepted:
                return
            try:
                target_page_number = parse_page_identifier(page_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            subpage_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Add Blank Hidden Subpage',
                f'Subpage for {_page_label(target_page_number)} (hex):',
                text=f'{int(default_subpage_number):04X}',
            )
            if not accepted:
                return
            try:
                target_subpage_number = parse_subpage_identifier(subpage_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return
            updated_entries = insert_hidden_subpage_occurrence(
                self._cached_combined_entries,
                target_page_number,
                target_subpage_number,
                blank_subpage_entries(target_page_number, target_subpage_number),
            )
            occurrence_number = max(
                len(
                    tuple(
                        self._page_subpage_occurrences_for_entries(
                            updated_entries,
                            target_page_number,
                        ).get(target_subpage_number, ())
                    )
                ),
                1,
            )
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=occurrence_number,
                enable_occurrences=((target_page_number, target_subpage_number, occurrence_number),),
            )
            QtWidgets.QMessageBox.information(
                self._dialog_parent(),
                'T42 Tool',
                f'Added hidden subpage {_page_label(target_page_number)} / {int(target_subpage_number):04X} ({occurrence_number}).',
            )

        def _tree_packet_index(self, item):
            if item is None:
                return None
            item_type = item.data(0, QtCore.Qt.UserRole)
            if item_type == 'page':
                return int(item.data(0, QtCore.Qt.UserRole + 2))
            if item_type == 'subpage':
                return int(item.data(0, QtCore.Qt.UserRole + 3))
            return None

        def _page_tree_pressed(self, item, _column):
            if item is None:
                return
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            if not (modifiers & QtCore.Qt.AltModifier):
                return
            packet_index = self._tree_packet_index(item)
            if packet_index is None:
                return
            self._set_playing(False)
            self._current_packet = packet_index
            self._sync_ui()

        def _page_tree_item_double_clicked(self, item, _column):
            if item is None:
                return
            context = self._selected_tree_context()
            if context is None:
                return
            self._ensure_edit_cache()
            page_number = int(context['page_number'])
            subpage_number = None if context['type'] == 'page' else int(context['subpage_number'])
            entries = self._cached_combined_entries
            if context['type'] == 'subpage':
                occurrence_number = int(context.get('occurrence_number') or 1)
                entries = collect_subpage_occurrence_entries(
                    self._cached_combined_entries,
                    page_number,
                    subpage_number,
                    occurrence_number,
                ) or collect_subpage_entries(self._cached_combined_entries, page_number, subpage_number)
            self._show_preview_window(entries, self._input_path or 'current.t42', page_number, subpage_number)

        def _cleanup_preview_window(self, window, temp_path):
            self._preview_windows = [candidate for candidate in self._preview_windows if candidate is not window]
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                self._preview_temp_paths.discard(temp_path)

        def _show_preview_window(self, entries, source_name, page_number=None, subpage_number=None):
            if not entries:
                return
            try:
                from teletext.gui import viewer as viewer_module
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.warning(self, 'T42 Tool', str(exc))
                return
            if getattr(viewer_module, 'IMPORT_ERROR', None) is not None:
                QtWidgets.QMessageBox.warning(self, 'T42 Tool', f'Qt teletext viewer is not available. ({viewer_module.IMPORT_ERROR})')
                return

            temp_handle = tempfile.NamedTemporaryFile(prefix='t42tool-preview-', suffix='.t42', delete=False)
            temp_handle.close()
            temp_path = temp_handle.name
            write_t42_entries(entries, temp_path)
            self._preview_temp_paths.add(temp_path)

            window = viewer_module.TeletextViewerWindow(filename=temp_path)
            window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            if page_number is not None:
                self._schedule_preview_navigation(window, page_number, subpage_number)
            if source_name:
                window.setWindowTitle(f'Teletext Preview - {os.path.basename(source_name)}')
            window.destroyed.connect(lambda _obj=None, current_window=window, current_path=temp_path: self._cleanup_preview_window(current_window, current_path))
            self._preview_windows.append(window)
            window.show()
            window.raise_()
            window.activateWindow()

        def _schedule_preview_navigation(self, window, page_number, subpage_number, remaining_attempts=100):
            if window is None or remaining_attempts <= 0:
                return
            navigator = getattr(window, '_navigator', None)
            if navigator is None:
                QtCore.QTimer.singleShot(
                    50,
                    lambda current_window=window, current_page=page_number, current_subpage=subpage_number, retries=remaining_attempts - 1:
                    self._schedule_preview_navigation(current_window, current_page, current_subpage, retries),
                )
                return
            try:
                success = navigator.go_to_page(int(page_number), None if subpage_number is None else int(subpage_number))
            except Exception:
                success = False
            if success:
                if hasattr(window, '_render_current_subpage'):
                    window._render_current_subpage()

        def _apply_imported_page(self, source_entries, source_page_number, target_page_number):
            page_entries = collect_page_entries(source_entries, source_page_number)
            if not page_entries:
                return
            self._ensure_edit_cache()
            updated_entries = replace_page_in_entries(
                self._cached_combined_entries,
                page_entries,
                target_page_number=target_page_number,
            )
            self._rebase_entries(updated_entries, focus_page_number=target_page_number)

        def _apply_imported_subpage(self, source_entries, source_page_number, source_subpage_number, target_page_number, target_subpage_number):
            subpage_entries = collect_subpage_entries(source_entries, source_page_number, source_subpage_number)
            if not subpage_entries:
                return
            self._ensure_edit_cache()
            target_occurrence_number = self._target_occurrence_number(target_page_number, target_subpage_number)
            if target_occurrence_number > 1:
                updated_entries = replace_subpage_occurrence_in_entries(
                    self._cached_combined_entries,
                    subpage_entries,
                    target_page_number,
                    target_subpage_number,
                    target_occurrence_number,
                    target_page_number=target_page_number,
                    target_subpage_number=target_subpage_number,
                )
            else:
                updated_entries = replace_subpage_in_entries(
                    self._cached_combined_entries,
                    subpage_entries,
                    target_page_number=target_page_number,
                    target_subpage_number=target_subpage_number,
                )
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=target_occurrence_number,
            )

        def _merge_imported_page(self, source_entries, source_page_number, target_page_number):
            self._ensure_edit_cache()
            updated_entries = merge_page_in_entries(
                self._cached_combined_entries,
                source_entries,
                source_page_number,
                target_page_number,
            )
            self._rebase_entries(updated_entries, focus_page_number=target_page_number)

        def _merge_imported_subpage(self, source_entries, source_page_number, source_subpage_number, target_page_number, target_subpage_number):
            subpage_entries = collect_subpage_entries(source_entries, source_page_number, source_subpage_number)
            if not subpage_entries:
                return
            self._ensure_edit_cache()
            target_occurrence_number = self._target_occurrence_number(target_page_number, target_subpage_number)
            updated_entries = merge_subpage_occurrence_in_entries(
                self._cached_combined_entries,
                subpage_entries,
                target_page_number,
                target_subpage_number,
                target_occurrence_number,
            )
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=target_occurrence_number,
            )

        def _add_imported_row(self, source_entries, source_page_number, source_subpage_number, source_row_number, target_page_number, target_subpage_number, target_row_number):
            source_row_entries = collect_row_entries(
                source_entries,
                source_page_number,
                source_subpage_number,
                source_row_number,
            )
            if not source_row_entries:
                QtWidgets.QMessageBox.warning(
                    self,
                    'T42 Tool',
                    f'Source subpage does not contain row {int(source_row_number)}.',
                )
                return
            source_entry = source_row_entries[0]
            source_header_entry = next((
                entry for entry in collect_subpage_entries(source_entries, source_page_number, source_subpage_number)
                if entry.row == 0
            ), None)
            self._ensure_edit_cache()
            try:
                target_occurrence_number = self._target_occurrence_number(target_page_number, target_subpage_number)
                updated_entries = add_row_to_subpage_occurrence_in_entries(
                    self._cached_combined_entries,
                    source_entry,
                    target_page_number,
                    target_subpage_number,
                    target_row_number,
                    target_occurrence_number,
                    source_header_entry=source_header_entry,
                )
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'T42 Tool', str(exc))
                return
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=target_occurrence_number,
            )

        def _open_source_dialog(self):
            dialog_parent = self._terminal_window if self._terminal_window is not None else self
            if self._source_dialog is None or self._source_dialog.parent() is not dialog_parent:
                if self._source_dialog is not None:
                    self._source_dialog.close()
                self._source_dialog = T42SourceDialog(dialog_parent)
            context = self._selected_tree_context()
            default_page_number = context['page_number'] if context is not None else None
            default_subpage_number = context['subpage_number'] if context is not None and context['type'] == 'subpage' else None
            default_row_number = None
            if self._entries:
                current_entry = self._entries[_clamp(self._current_packet, 0, self._total_packets - 1)]
                if current_entry.row is not None and 0 <= int(current_entry.row) <= 31:
                    default_row_number = int(current_entry.row)
            self._source_dialog.configure(
                apply_page_callback=self._apply_imported_page,
                apply_subpage_callback=self._apply_imported_subpage,
                merge_page_callback=self._merge_imported_page,
                merge_subpage_callback=self._merge_imported_subpage,
                add_row_callback=self._add_imported_row,
                preview_callback=self._show_preview_window,
                default_page_number=default_page_number,
                default_subpage_number=default_subpage_number,
                default_row_number=default_row_number,
            )
            if self._source_dialog.isMinimized():
                self._source_dialog.showNormal()
            self._source_dialog.show()
            self._source_dialog.raise_()
            self._source_dialog.activateWindow()

        def _pick_source_item(self, source_path, entries, *, selection_mode):
            summary = summarise_t42_pages(entries)
            if not summary:
                QtWidgets.QMessageBox.warning(self, 'T42 Tool', 'Selected file does not contain any page headers.')
                return None

            if selection_mode == 'page' and len(summary) == 1:
                return ('page', int(summary[0]['page_number']), None, 1)

            occurrences = page_subpage_occurrences(entries)
            available_subpages = [
                (
                    int(page_summary['page_number']),
                    int(subpage_summary['subpage_number']),
                    int(variant.get('occurrence') or 1),
                )
                for page_summary in summary
                for subpage_summary in page_summary['subpages']
                for variant in (
                    tuple(occurrences.get(int(page_summary['page_number']), {}).get(int(subpage_summary['subpage_number']), ()))
                    or ({'occurrence': 1},)
                )
            ]
            if selection_mode == 'subpage' and len(available_subpages) == 1:
                page_number, subpage_number, occurrence_number = available_subpages[0]
                return ('subpage', page_number, subpage_number, occurrence_number)

            dialog = QtWidgets.QDialog(self._terminal_window if self._terminal_window is not None else self)
            dialog.setWindowTitle(f'Select {selection_mode.title()} - {pathlib.Path(source_path).name}')
            dialog.resize(640, 420)
            layout = QtWidgets.QVBoxLayout(dialog)
            layout.addWidget(QtWidgets.QLabel(f'Select a {selection_mode} from {source_path}:'))

            tree = QtWidgets.QTreeWidget()
            tree.setHeaderLabels(['Entry', 'Packets', 'Row 0'])
            _configure_tree_widget_columns(tree)
            layout.addWidget(tree, 1)

            for page_summary in summary:
                page_item = QtWidgets.QTreeWidgetItem([
                    _page_label(page_summary['page_number']),
                    str(page_summary['packet_count']),
                    page_summary['header_title'],
                ])
                page_item.setData(0, QtCore.Qt.UserRole, 'page')
                page_item.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                if selection_mode == 'page':
                    page_item.setFlags(page_item.flags() | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                else:
                    page_item.setFlags(QtCore.Qt.ItemIsEnabled)
                tree.addTopLevelItem(page_item)
                for subpage_summary in page_summary['subpages']:
                    subpage_number = int(subpage_summary['subpage_number'])
                    variants = tuple(
                        occurrences.get(int(page_summary['page_number']), {}).get(subpage_number, ())
                    ) or ({
                        'label': f'{subpage_number:04X}',
                        'occurrence': 1,
                        'header_title': subpage_summary['header_title'],
                        'packet_count': int(subpage_summary['packet_count']),
                    },)
                    for variant in variants:
                        child = QtWidgets.QTreeWidgetItem([
                            str(variant.get('label') or f'{subpage_number:04X}'),
                            str(int(variant.get('packet_count') or subpage_summary['packet_count'])),
                            str(variant.get('header_title') or subpage_summary['header_title']),
                        ])
                        child.setData(0, QtCore.Qt.UserRole, 'subpage')
                        child.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                        child.setData(0, QtCore.Qt.UserRole + 2, subpage_number)
                        child.setData(0, QtCore.Qt.UserRole + 3, int(variant.get('occurrence') or 1))
                        if selection_mode == 'subpage':
                            child.setFlags(child.flags() | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                        else:
                            child.setFlags(QtCore.Qt.ItemIsEnabled)
                        page_item.addChild(child)
                page_item.setExpanded(True)

            button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
            ok_button.setEnabled(False)
            button_box.accepted.connect(dialog.accept)
            button_box.rejected.connect(dialog.reject)
            layout.addWidget(button_box)

            def refresh_accept_state():
                item = tree.currentItem()
                ok_button.setEnabled(item is not None and item.data(0, QtCore.Qt.UserRole) == selection_mode)

            tree.itemSelectionChanged.connect(refresh_accept_state)

            def handle_double_click(item, _column):
                if item is not None and item.data(0, QtCore.Qt.UserRole) == selection_mode:
                    dialog.accept()

            tree.itemDoubleClicked.connect(handle_double_click)
            refresh_accept_state()
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return None

            item = tree.currentItem()
            if item is None:
                return None
            item_type = item.data(0, QtCore.Qt.UserRole)
            if item_type == 'page':
                return ('page', int(item.data(0, QtCore.Qt.UserRole + 1)), None, 1)
            if item_type == 'subpage':
                return (
                    'subpage',
                    int(item.data(0, QtCore.Qt.UserRole + 1)),
                    int(item.data(0, QtCore.Qt.UserRole + 2)),
                    int(item.data(0, QtCore.Qt.UserRole + 3) or 1),
                )
            return None

        def _choose_source_entries(self, selection_mode):
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self._dialog_parent(),
                'Open T42 File',
                os.getcwd(),
                'Teletext packet files (*.t42);;All files (*)',
            )
            if not filename:
                return None
            try:
                entries = load_t42_entries(filename)
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'T42 Tool', str(exc))
                return None
            if not entries:
                QtWidgets.QMessageBox.warning(self, 'T42 Tool', 'Selected file does not contain any complete packets.')
                return None
            choice = self._pick_source_item(filename, entries, selection_mode=selection_mode)
            if choice is None:
                return None
            return filename, entries, choice

        def _import_page(self):
            source = self._choose_source_entries('page')
            if source is None:
                return
            _filename, source_entries, choice = source
            _kind, source_page_number, _source_subpage_number, _source_occurrence_number = choice
            self._ensure_edit_cache()
            context = self._selected_tree_context()
            target_page_number = source_page_number
            if context is not None:
                target_page_number = int(context['page_number'])
            self._apply_imported_page(source_entries, source_page_number, target_page_number)

        def _import_subpage(self):
            source = self._choose_source_entries('subpage')
            if source is None:
                return
            _filename, source_entries, choice = source
            _kind, source_page_number, source_subpage_number, source_occurrence_number = choice
            self._ensure_edit_cache()
            context = self._selected_tree_context()
            target_page_number = source_page_number
            target_subpage_number = source_subpage_number
            if context is not None:
                target_page_number = int(context['page_number'])
                if context['type'] == 'subpage':
                    target_subpage_number = int(context['subpage_number'])
            selected_source_entries = collect_subpage_occurrence_entries(
                source_entries,
                source_page_number,
                source_subpage_number,
                source_occurrence_number,
            ) or collect_subpage_entries(
                source_entries,
                source_page_number,
                source_subpage_number,
            )
            self._apply_imported_subpage(
                selected_source_entries,
                source_page_number,
                source_subpage_number,
                target_page_number,
                target_subpage_number,
            )

        def _edit_selected_page_entry(self):
            context = self._selected_tree_context()
            if context is None:
                QtWidgets.QMessageBox.information(self._dialog_parent(), 'T42 Tool', 'Select a page or subpage first.')
                return
            self._ensure_edit_cache()
            current_page_text = f"{(int(context['page_number']) >> 8):X}{(int(context['page_number']) & 0xFF):02X}"
            page_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Edit Page',
                'Page (hex):',
                text=current_page_text,
            )
            if not accepted:
                return
            try:
                target_page_number = parse_page_identifier(page_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return

            if context['type'] == 'page':
                updated_entries = move_page_in_entries(
                    self._cached_combined_entries,
                    context['page_number'],
                    target_page_number,
                )
                self._rebase_entries(updated_entries, focus_page_number=target_page_number)
                return

            current_subpage_text = f"{int(context['subpage_number']):04X}"
            subpage_text, accepted = QtWidgets.QInputDialog.getText(
                self._dialog_parent(),
                'Edit Subpage',
                'Subpage (hex):',
                text=current_subpage_text,
            )
            if not accepted:
                return
            try:
                target_subpage_number = parse_subpage_identifier(subpage_text)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self._dialog_parent(), 'T42 Tool', str(exc))
                return

            occurrence_number = int(context.get('occurrence_number') or 1)
            if occurrence_number > 1:
                source_entries = collect_subpage_occurrence_entries(
                    self._cached_combined_entries,
                    context['page_number'],
                    context['subpage_number'],
                    occurrence_number,
                )
                updated_entries = replace_subpage_occurrence_in_entries(
                    self._cached_combined_entries,
                    source_entries,
                    context['page_number'],
                    context['subpage_number'],
                    occurrence_number,
                    target_page_number=target_page_number,
                    target_subpage_number=target_subpage_number,
                )
                focus_occurrence_number = max(
                    len(tuple(self._page_subpage_occurrences_for_entries(updated_entries, target_page_number).get(target_subpage_number, ()))),
                    1,
                )
            else:
                updated_entries = move_subpage_in_entries(
                    self._cached_combined_entries,
                    context['page_number'],
                    context['subpage_number'],
                    target_page_number,
                    target_subpage_number,
                )
                focus_occurrence_number = 1
            self._rebase_entries(
                updated_entries,
                focus_page_number=target_page_number,
                focus_subpage_number=target_subpage_number,
                focus_occurrence_number=focus_occurrence_number,
            )

        def _change_selected_hidden_subpage_sequence(self):
            context = self._selected_tree_context()
            if context is None or context['type'] != 'subpage':
                return
            occurrence_number = max(int(context.get('occurrence_number') or 1), 1)
            if occurrence_number <= 1:
                return
            self._ensure_edit_cache()
            variants = tuple(
                self._page_subpage_occurrences_for_entries(
                    self._cached_combined_entries,
                    int(context['page_number']),
                ).get(int(context['subpage_number']), ())
            )
            total_occurrences = len(variants)
            if total_occurrences <= 1:
                return
            target_occurrence_number, accepted = QtWidgets.QInputDialog.getInt(
                self._dialog_parent(),
                'Change Hidden Sequence',
                'Sequence number:',
                value=occurrence_number,
                min=1,
                max=total_occurrences,
            )
            if not accepted:
                return
            target_occurrence_number = int(target_occurrence_number)
            if target_occurrence_number == occurrence_number:
                return
            updated_entries = move_subpage_occurrence_in_entries(
                self._cached_combined_entries,
                context['page_number'],
                context['subpage_number'],
                occurrence_number,
                target_occurrence_number,
            )
            self._rebase_entries(
                updated_entries,
                focus_page_number=int(context['page_number']),
                focus_subpage_number=int(context['subpage_number']),
                focus_occurrence_number=target_occurrence_number,
            )
            QtWidgets.QMessageBox.information(
                self._dialog_parent(),
                'T42 Tool',
                f'Hidden subpage moved to sequence {target_occurrence_number}.',
            )

        def _packet_slider_changed(self, value):
            if self._updating:
                return
            self._set_playing(False)
            self._current_packet = int(value)
            self._sync_ui()

        def _packet_box_changed(self, value):
            if self._updating:
                return
            self._set_playing(False)
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
            self._set_playing(False)
            self._current_packet = _clamp(self._current_packet + int(delta), 0, self._total_packets - 1)
            self._sync_ui()

        def _jump_start(self):
            self._set_playing(False)
            self._current_packet = 0
            self._sync_ui()

        def _jump_end(self):
            self._set_playing(False)
            self._current_packet = self._total_packets - 1
            self._sync_ui()

        def _set_playing(self, playing, direction=None):
            self._playing = bool(playing)
            if direction is not None:
                self._playback_direction = -1 if int(direction) < 0 else 1
            self._playback_last_tick = time.monotonic()

        def _toggle_play(self):
            if self._playing and self._playback_direction > 0:
                self._set_playing(False)
            else:
                self._set_playing(True, direction=1)
            self._sync_ui()

        def _toggle_reverse_play(self):
            if self._playing and self._playback_direction < 0:
                self._set_playing(False)
            else:
                self._set_playing(True, direction=-1)
            self._sync_ui()

        def _speed_changed(self, value):
            if self._updating:
                return
            self._playback_speed = max(MIN_PLAYBACK_SPEED, min(MAX_PLAYBACK_SPEED, float(value)))
            self._playback_last_tick = time.monotonic()
            self._sync_ui()

        def _advance_playback(self):
            if not self._playing:
                self._playback_last_tick = time.monotonic()
                return
            now = time.monotonic()
            elapsed = now - self._playback_last_tick
            step_rate = _vbicrop.DEFAULT_FRAME_RATE * self._playback_speed
            steps = int(elapsed * step_rate)
            if steps <= 0:
                return
            current_packet, reached_end = advance_playback_position(
                self._current_packet,
                steps,
                self._total_packets,
                self._playback_direction,
            )
            self._current_packet = current_packet
            self._playback_last_tick += steps / step_rate
            if reached_end:
                self._set_playing(False)
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
            self._keep_ranges = ()
            self._cut_ranges = normalise_cut_ranges(self._cut_ranges + ((start, end),), self._total_packets)
            self._selected_cut_index = None
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _keep_selection(self):
            start = int(self._selection_start)
            end = int(self._selection_end)
            self._keep_ranges = normalise_keep_ranges(self._keep_ranges + ((start, end),), self._total_packets)
            self._cut_ranges = keep_ranges_to_cut_ranges(self._keep_ranges, self._total_packets)
            self._selected_cut_index = None
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _delete_selected_page_entry(self):
            contexts = self._selected_tree_contexts()
            if not contexts:
                return
            selected_pages = {
                int(context['page_number'])
                for context in contexts
                if context['type'] == 'page'
            }
            selected_subpages = [
                context for context in contexts
                if context['type'] == 'subpage' and int(context['page_number']) not in selected_pages
            ]
            updated_deleted_pages = set(self._deleted_pages)
            updated_deleted_subpages = set(self._deleted_subpages)
            updated_occurrences = set(self._enabled_subpage_occurrences)
            for page_number in selected_pages:
                updated_deleted_pages.add(page_number)
                updated_deleted_subpages = {
                    key for key in updated_deleted_subpages
                    if int(key[0]) != int(page_number)
                }
                updated_occurrences = {
                    key for key in updated_occurrences
                    if int(key[0]) != int(page_number)
                }
            for context in selected_subpages:
                page_number = int(context['page_number'])
                subpage_number = int(context['subpage_number'])
                occurrence_number = int(context.get('occurrence_number') or 1)
                if occurrence_number == 1:
                    updated_deleted_subpages.add((page_number, subpage_number))
                    updated_occurrences = {
                        key for key in updated_occurrences
                        if not (int(key[0]) == page_number and int(key[1]) == subpage_number)
                    }
                else:
                    updated_occurrences.discard((page_number, subpage_number, occurrence_number))
            self._deleted_pages = frozenset(updated_deleted_pages)
            self._deleted_subpages = frozenset(updated_deleted_subpages)
            self._enabled_subpage_occurrences = updated_occurrences
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
            self._input_path = self._initial_input_path
            self._entries = tuple(self._initial_entries)
            self._headers = collect_t42_headers(self._entries)
            self._total_packets = max(len(self._entries), 1)
            self.setWindowTitle(f'T42 Tool - {self._window_display_name()}')
            self._packet_slider.setRange(0, self._total_packets - 1)
            self._packet_box.setRange(0, self._total_packets - 1)
            self._start_box.setRange(0, self._total_packets - 1)
            self._end_box.setRange(0, self._total_packets - 1)
            self._range_slider.setRange(0, self._total_packets - 1)
            self._current_packet = 0
            self._selection_start = 0
            self._selection_end = self._total_packets - 1
            self._cut_ranges = ()
            self._keep_ranges = ()
            self._insertions = ()
            self._selected_cut_index = None
            self._selected_insertion_index = None
            self._deleted_pages = frozenset()
            self._deleted_subpages = frozenset()
            self._sync_enabled_subpage_occurrences(self._entries, preserve=False)
            self._pending_tree_selection = None
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
                QtWidgets.QMessageBox.critical(self, 'T42 Tool', str(exc))
                return
            if not entries:
                QtWidgets.QMessageBox.warning(self, 'T42 Tool', 'Selected file does not contain any complete packets.')
                return
            insertion = T42Insertion(
                after_packet=int(self._selection_end),
                path=filename,
                packet_count=len(entries),
                entries=tuple(entries),
            )
            self._insertions = normalise_t42_insertions(self._insertions + (insertion,), self._total_packets)
            self._selected_insertion_index = None
            for index, current_insertion in enumerate(self._insertions):
                if current_insertion == insertion:
                    self._selected_insertion_index = index
                    break
            self._mark_cache_dirty()
            self._record_history_state(reset_redo=True)
            self._sync_ui()

        def _run_progress_task(self, title, label, callback):
            progress = QtWidgets.QProgressDialog(label, None, 0, 1, self)
            progress.setWindowTitle(title)
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setCancelButton(None)
            progress.setValue(0)

            def report(current, total):
                total = max(int(total), 1)
                current = max(0, min(int(current), total))
                progress.setMaximum(total)
                progress.setValue(current)
                progress.setLabelText(f'{label} {current}/{total}')
                QtWidgets.QApplication.processEvents()

            try:
                return callback(report)
            finally:
                progress.setValue(progress.maximum())
                progress.close()

        def _save_file(self):
            if self._save_callback is None:
                return
            default_name = 'edited.t42' if not self._input_path else f'{pathlib.Path(self._input_path).stem}-edited.t42'
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save T42',
                os.path.join(os.getcwd(), default_name),
                'Teletext packet files (*.t42);;All files (*)',
            )
            if not filename:
                return
            self._ensure_edit_cache()
            try:
                self._run_progress_task(
                    'Saving T42',
                    'Saving T42 packets...',
                    lambda progress_callback: self._save_callback(
                        filename,
                        tuple(self._cached_edited_entries),
                        progress_callback=progress_callback,
                    ),
                )
            except Exception as exc:  # pragma: no cover - GUI path
                QtWidgets.QMessageBox.critical(self, 'T42 Tool', str(exc))
                return
            QtWidgets.QMessageBox.information(self, 'T42 Tool', f'Saved T42 to:\n{filename}')

        def closeEvent(self, event):  # pragma: no cover - GUI path
            if self._source_dialog is not None:
                self._source_dialog.close()
            if self._terminal_window is not None:
                self._terminal_window.close()
            for window in tuple(self._preview_windows):
                try:
                    window.close()
                except Exception:
                    pass
            for temp_path in tuple(self._preview_temp_paths):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            self._preview_temp_paths.clear()
            super().closeEvent(event)


def run_t42_tool_window(input_path, entries, save_callback=None):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    window = T42ToolWindow(
        input_path=input_path,
        entries=entries,
        save_callback=save_callback,
    )
    _run_dialog_window(window)


if IMPORT_ERROR is None:
    T42CropWindow = T42ToolWindow
    run_t42_crop_window = run_t42_tool_window
