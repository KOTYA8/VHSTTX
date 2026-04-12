import faulthandler
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter, deque

import numpy as np

from teletext import charset as teletext_charset
from teletext.coding import byte_reverse, hamming8_encode
from teletext.file import FileChunker
from teletext.packet import Packet
from teletext.service import Service
from teletext.viewer import count_html_outputs, count_split_t42_outputs, export_html, export_selected_html
from teletext.gui.t42crop import (
    T42SourceDialog,
    add_row_to_subpage_entries,
    build_t42_entries,
    collect_page_entries,
    collect_row_entries,
    collect_subpage_occurrence_entries,
    collect_subpage_entries,
    convert_subpage_occurrence_to_real,
    move_subpage_in_entries,
    move_subpage_occurrence_in_entries,
    parse_page_identifier,
    parse_subpage_identifier,
    page_subpage_occurrences,
    replace_page_in_entries,
    replace_subpage_occurrence_in_entries,
    replace_subpage_in_entries,
    summarise_t42_pages,
    write_t42_entries,
)


try:
    from PyQt5 import QtCore, QtGui, QtWidgets, QtQuickWidgets
except ImportError:
    print('PyQt5 is not installed. TeleText Editor not available.')
else:
    from teletext.gui.decoder import Decoder
    from teletext.subpage import Subpage
    from teletext.viewer import ServiceNavigator


PACKET_SIZE = 42
EDITOR_APP_NAME = 'TeleText Editor'

TRANSLITERATION_CHAR_MAP = {
    'А': 'A', 'а': 'a', 'Б': 'B', 'б': 'b', 'В': 'V', 'в': 'v',
    'Г': 'G', 'г': 'g', 'Д': 'D', 'д': 'd', 'Е': 'E', 'е': 'e',
    'Ё': 'Yo', 'ё': 'yo', 'Ж': 'Zh', 'ж': 'zh', 'З': 'Z', 'з': 'z',
    'И': 'I', 'и': 'i', 'Й': 'Y', 'й': 'y', 'К': 'K', 'к': 'k',
    'Л': 'L', 'л': 'l', 'М': 'M', 'м': 'm', 'Н': 'N', 'н': 'n',
    'О': 'O', 'о': 'o', 'П': 'P', 'п': 'p', 'Р': 'R', 'р': 'r',
    'С': 'S', 'с': 's', 'Т': 'T', 'т': 't', 'У': 'U', 'у': 'u',
    'Ф': 'F', 'ф': 'f', 'Х': 'Kh', 'х': 'kh', 'Ц': 'Ts', 'ц': 'ts',
    'Ч': 'Ch', 'ч': 'ch', 'Ш': 'Sh', 'ш': 'sh', 'Щ': 'Shch', 'щ': 'shch',
    'Ъ': '"', 'ъ': '"', 'Ы': 'Y', 'ы': 'y', 'Ь': "'", 'ь': "'",
    'Э': 'E', 'э': 'e', 'Ю': 'Yu', 'ю': 'yu', 'Я': 'Ya', 'я': 'ya',
    'Є': 'Ye', 'є': 'ye', 'І': 'I', 'і': 'i', 'Ї': 'Yi', 'ї': 'yi',
    'Ґ': 'G', 'ґ': 'g',
    'Ą': 'A', 'ą': 'a', 'Ć': 'C', 'ć': 'c', 'Ę': 'E', 'ę': 'e',
    'Ł': 'L', 'ł': 'l', 'Ń': 'N', 'ń': 'n', 'Ó': 'O', 'ó': 'o',
    'Ś': 'S', 'ś': 's', 'Ź': 'Z', 'ź': 'z', 'Ż': 'Z', 'ż': 'z',
    'Č': 'C', 'č': 'c', 'Š': 'S', 'š': 's', 'Ž': 'Z', 'ž': 'z',
    'Đ': 'D', 'đ': 'd', 'Æ': 'AE', 'æ': 'ae', 'Ø': 'O', 'ø': 'o',
    'Å': 'A', 'å': 'a', 'Þ': 'Th', 'þ': 'th', 'Ð': 'D', 'ð': 'd',
    'ß': 'ss',
    'Γ': 'G', 'γ': 'g', 'Δ': 'D', 'δ': 'd', 'Θ': 'Th', 'θ': 'th',
    'Λ': 'L', 'λ': 'l', 'Ξ': 'X', 'ξ': 'x', 'Π': 'P', 'π': 'p',
    'Σ': 'S', 'σ': 's', 'ς': 's', 'Φ': 'F', 'φ': 'f', 'Ψ': 'Ps', 'ψ': 'ps',
    'Ω': 'O', 'ω': 'o',
}

try:
    faulthandler.enable(all_threads=True)
except Exception:
    pass


def _copy_text_to_system_clipboard(text):
    text = '' if text is None else str(text)
    commands = ()
    if sys.platform.startswith('linux'):
        commands = (
            ('wl-copy',),
            ('xclip', '-selection', 'clipboard', '-in'),
            ('xsel', '--clipboard', '--input'),
        )
    elif sys.platform == 'darwin':
        commands = (('pbcopy',),)
    elif os.name == 'nt':
        commands = (('clip',),)
    for command in commands:
        executable = shutil.which(command[0])
        if not executable:
            continue
        try:
            subprocess.run(
                (executable, *command[1:]),
                input=text.encode('utf-8'),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )
            return True
        except Exception:
            continue
    return False


def _transliterate_editor_text(text):
    text = '' if text is None else str(text)
    result = []
    for char in text:
        if char in EDITOR_CONTROL_LOOKUP or char in DEFAULT_EDITOR_CHAR_TO_BYTE:
            result.append(char)
            continue
        mapped = TRANSLITERATION_CHAR_MAP.get(char)
        if mapped is None:
            normalized = unicodedata.normalize('NFKD', char)
            stripped = ''.join(
                piece for piece in normalized
                if not unicodedata.combining(piece)
            )
            ascii_only = ''.join(piece for piece in stripped if ord(piece) < 0x80)
            mapped = ascii_only
        if not mapped:
            if char in '\r\n\t':
                mapped = ' '
            elif 0x20 <= ord(char) < 0x7F:
                mapped = char
            else:
                mapped = '?'
        result.append(mapped)
    return ''.join(result)


def _clean_broadcast_label_text(value):
    text = '' if value is None else str(value)
    cleaned = []
    for char in text.replace('\r', ' ').replace('\n', ' '):
        codepoint = ord(char)
        cleaned.append(char if 0x20 <= codepoint < 0x7F else ' ')
    return ''.join(cleaned)[:20]


def _broadcast_label_from_packet(packet):
    try:
        raw = bytes(packet.broadcast.displayable.bytes_no_parity)
    except Exception:
        return ''
    return _clean_broadcast_label_text(raw.decode('ascii', errors='ignore')).rstrip()


def _bcd8_encode_safe(value):
    value = int(value)
    if value < 0 or value > 99:
        raise ValueError('BCD value must be between 0 and 99.')
    tens = ((value // 10) + 1) & 0x0F
    units = ((value % 10) + 1) & 0x0F
    return (tens << 4) | units


def _parse_service_830_network(text, *, format2=False):
    cleaned = ''.join(ch for ch in str(text or '').strip().upper() if ch in '0123456789ABCDEF')
    if not cleaned:
        return 0
    max_digits = 2 if format2 else 4
    if len(cleaned) > max_digits:
        raise ValueError(
            f'8/30 network must be {max_digits} hexadecimal digits or fewer for this format.'
        )
    value = int(cleaned, 16)
    if format2 and not 0 <= value <= 0xFF:
        raise ValueError('8/30 Format 2 network must be between 00 and FF.')
    if not format2 and not 0 <= value <= 0xFFFF:
        raise ValueError('8/30 Format 1 network must be between 0000 and FFFF.')
    return value


def _parse_service_830_country(text):
    cleaned = ''.join(ch for ch in str(text or '').strip().upper() if ch in '0123456789ABCDEF')
    if not cleaned:
        return 0
    if len(cleaned) > 2:
        raise ValueError('8/30 country must be two hexadecimal digits or fewer.')
    value = int(cleaned, 16)
    if not 0 <= value <= 0xFF:
        raise ValueError('8/30 country must be between 00 and FF.')
    return value


def _parse_service_830_date_format1(text):
    cleaned = str(text or '').strip()
    if not cleaned:
        return datetime.date(2000, 1, 1)
    try:
        return datetime.date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError('8/30 Format 1 date must use YYYY-MM-DD.') from exc


def _parse_service_830_date_format2(text):
    cleaned = str(text or '').strip()
    if not cleaned:
        return 1, 1
    for separator in ('/', '-', '.'):
        if separator in cleaned:
            left, right = cleaned.split(separator, 1)
            break
    else:
        raise ValueError('8/30 Format 2 date must use DD/MM.')
    try:
        day = int(left)
        month = int(right)
    except ValueError as exc:
        raise ValueError('8/30 Format 2 date must use numeric DD/MM.') from exc
    if not 1 <= day <= 31:
        raise ValueError('8/30 day must be between 1 and 31.')
    if not 1 <= month <= 12:
        raise ValueError('8/30 month must be between 1 and 12.')
    return day, month


def _parse_service_830_time(text, *, include_seconds):
    cleaned = str(text or '').strip()
    if not cleaned:
        return (0, 0, 0) if include_seconds else (0, 0)
    parts = cleaned.split(':')
    expected = 3 if include_seconds else 2
    if len(parts) not in {2, 3}:
        raise ValueError('8/30 time must use HH:MM or HH:MM:SS.')
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError as exc:
        raise ValueError('8/30 time must use numeric HH:MM or HH:MM:SS.') from exc
    if not 0 <= hour <= 23:
        raise ValueError('8/30 hour must be between 00 and 23.')
    if not 0 <= minute <= 59:
        raise ValueError('8/30 minute must be between 00 and 59.')
    if not 0 <= second <= 59:
        raise ValueError('8/30 second must be between 00 and 59.')
    return (hour, minute, second) if include_seconds else (hour, minute)


def _parse_service_830_offset(text):
    cleaned = str(text or '').strip().replace(',', '.')
    if not cleaned:
        return 0.0
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError('8/30 UTC offset must be a number like 0, 1, -3 or 3.5.') from exc
    if (value * 2.0) != round(value * 2.0):
        raise ValueError('8/30 UTC offset must use 0.5-hour steps.')
    if abs(value) > 15.5:
        raise ValueError('8/30 UTC offset must be between -15.5 and +15.5.')
    return float(value)


def _encode_service_830_format1(packet, *, network, date_value, hour, minute, second, offset):
    format1 = packet.broadcast.format1
    network = int(network) & 0xFFFF
    format1._array[0] = int(byte_reverse((network >> 8) & 0xFF))
    format1._array[1] = int(byte_reverse(network & 0xFF))
    magnitude = int(round(abs(float(offset)) * 2.0)) & 0x1F
    sign = 0x40 if float(offset) < 0 else 0x00
    format1._array[2] = sign | (magnitude << 1)
    mjd = (date_value - format1.epoch).days
    mjd_digits = f'{int(mjd):05d}'
    format1._array[3] = (int(mjd_digits[0]) + 1) & 0x0F
    format1._array[4] = ((int(mjd_digits[1]) + 1) << 4) | ((int(mjd_digits[2]) + 1) & 0x0F)
    format1._array[5] = ((int(mjd_digits[3]) + 1) << 4) | ((int(mjd_digits[4]) + 1) & 0x0F)
    format1._array[6] = _bcd8_encode_safe(hour)
    format1._array[7] = _bcd8_encode_safe(minute)
    format1._array[8] = _bcd8_encode_safe(second)


def _encode_service_830_format2(packet, *, network, country, day, month, hour, minute):
    format2 = packet.broadcast.format2
    nibbles = [0] * 11
    country_rev = int(byte_reverse(int(country) & 0xFF))
    network_rev = int(byte_reverse(int(network) & 0xFF))
    day_rev = (int(byte_reverse((int(day) & 0x1F) << 3)) >> 3) & 0x1F
    month_rev = (int(byte_reverse((int(month) & 0x0F) << 4)) >> 4) & 0x0F
    hour_rev = (int(byte_reverse((int(hour) & 0x1F) << 3)) >> 3) & 0x1F
    minute_rev = (int(byte_reverse((int(minute) & 0x3F) << 2)) >> 2) & 0x3F

    nibbles[2] = country_rev & 0x0F
    nibbles[8] = (nibbles[8] & 0x03) | (((country_rev >> 4) & 0x03) << 2)
    nibbles[9] = (nibbles[9] & 0x0C) | ((country_rev >> 6) & 0x03)

    nibbles[3] = (nibbles[3] & 0x0C) | (network_rev & 0x03)
    nibbles[9] = (nibbles[9] & 0x03) | ((network_rev >> 2) & 0x0C)
    nibbles[10] = (network_rev >> 4) & 0x0F

    nibbles[3] = (nibbles[3] & 0x03) | ((day_rev & 0x03) << 2)
    nibbles[4] = (nibbles[4] & 0x08) | ((day_rev >> 2) & 0x07)

    nibbles[4] = (nibbles[4] & 0x07) | ((month_rev & 0x01) << 3)
    nibbles[5] = (nibbles[5] & 0x08) | ((month_rev >> 1) & 0x07)

    nibbles[5] = (nibbles[5] & 0x07) | ((hour_rev & 0x01) << 3)
    nibbles[6] = (hour_rev >> 1) & 0x0F

    nibbles[7] = minute_rev & 0x0F
    nibbles[8] = (nibbles[8] & 0x0C) | ((minute_rev >> 4) & 0x03)

    for index in range(2, 11):
        format2._array[index] = hamming8_encode(int(nibbles[index]) & 0x0F)

PREVIEW_CONTROL_GLYPHS = {
    0x00: 'K',
    0x01: 'R',
    0x02: 'G',
    0x03: 'Y',
    0x04: 'B',
    0x05: 'M',
    0x06: 'C',
    0x07: 'W',
    0x08: 'F',
    0x09: 'S',
    0x0A: ']',
    0x0B: '[',
    0x0C: 'n',
    0x0D: 'h',
    0x0E: 'w',
    0x0F: 'd',
    0x10: 'k',
    0x11: 'r',
    0x12: 'g',
    0x13: 'y',
    0x14: 'b',
    0x15: 'm',
    0x16: 'c',
    0x17: 'w',
    0x18: 'o',
    0x19: '#',
    0x1A: ':',
    0x1B: 'e',
    0x1C: 'k',
    0x1D: 'N',
    0x1E: 'H',
    0x1F: 'R',
}
EDITOR_CONTROL_CHARS = {code: chr(0x2400 + code) for code in range(0x20)}
EDITOR_CONTROL_LOOKUP = {glyph: code for code, glyph in EDITOR_CONTROL_CHARS.items()}
DEFAULT_EDITOR_CHAR_TO_BYTE = {}
for _byte, _glyph in teletext_charset.g0['default'].items():
    DEFAULT_EDITOR_CHAR_TO_BYTE.setdefault(_glyph, _byte)

CONTROL_KEYS_HTML = """
<div style="font-family: monospace;">
<b>Control Keys</b><br/>
Enable <b>Show Control Codes</b> to make hidden registers visible in the text grid.<br/><br/>
<b>Preview Glyphs</b><br/>
R/G/Y/B/M/C/W: alpha colours<br/>
r/g/y/b/m/c/t/s: mosaic colours<br/>
F/S: flash / steady<br/>
[ / ]: start / end box<br/>
n/h/w/d: normal / double height / double width / double size<br/>
o: conceal<br/>
# / :: contiguous / separated mosaic<br/>
e: switch character set<br/>
k / N: black background / new background<br/>
H / R: hold / release mosaic<br/><br/>
<b>Editor Text</b><br/>
When control codes are shown in the editor, raw registers appear as Unicode control pictures
 so they can be preserved while editing plain text around them.
</div>
"""

CONTROL_CODE_MENU = (
    ('Alpha Colors', (
        ('Alpha Black', 0x00),
        ('Alpha Red', 0x01),
        ('Alpha Green', 0x02),
        ('Alpha Yellow', 0x03),
        ('Alpha Blue', 0x04),
        ('Alpha Magenta', 0x05),
        ('Alpha Cyan', 0x06),
        ('Alpha White', 0x07),
    )),
    ('Alpha Attributes', (
        ('Flash', 0x08),
        ('Steady', 0x09),
        ('End Box', 0x0A),
        ('Start Box', 0x0B),
        ('Normal Height', 0x0C),
        ('Double Height', 0x0D),
        ('Double Width', 0x0E),
        ('Double Size', 0x0F),
    )),
    ('Mosaic Colors', (
        ('Mosaic Black', 0x10),
        ('Mosaic Red', 0x11),
        ('Mosaic Green', 0x12),
        ('Mosaic Yellow', 0x13),
        ('Mosaic Blue', 0x14),
        ('Mosaic Magenta', 0x15),
        ('Mosaic Cyan', 0x16),
        ('Mosaic White', 0x17),
    )),
    ('Mosaic Attributes', (
        ('Conceal', 0x18),
        ('Contiguous Mosaic', 0x19),
        ('Separated Mosaic', 0x1A),
        ('Switch Charset', 0x1B),
        ('Black Background', 0x1C),
        ('New Background', 0x1D),
        ('Hold Mosaic', 0x1E),
        ('Release Mosaic', 0x1F),
    )),
)

CONTROL_CODE_LABELS = {
    int(code): str(label)
    for _section_title, actions in CONTROL_CODE_MENU
    for label, code in actions
}

CHARACTER_SET_OPTIONS = (
    (0, 'English', 'default'),
    (1, 'Cyrillic', 'cyr'),
    (2, 'Swedish', 'swe'),
    (3, 'French', 'fra'),
    (4, 'German', 'deu'),
    (5, 'Italian', 'ita'),
    (6, 'Polish', 'pol'),
    (7, 'Dutch', 'nld'),
)

FASTEXT_FIELDS = (
    ('Red', '#d83b3b'),
    ('Green', '#19a519'),
    ('Yellow', '#c7a200'),
    ('Cyan', '#0d9aa8'),
)

CONTROL_CODE_HINTS = {
    0x00: '0',
    0x01: '1',
    0x02: '2',
    0x03: '3',
    0x04: '4',
    0x05: '5',
    0x06: '6',
    0x07: '7',
    0x08: 'f',
    0x09: 'F',
    0x0A: 'x',
    0x0B: 'X',
    0x0C: 'n',
    0x0D: 'd',
    0x0E: 'ctrl-d',
    0x0F: 'ctrl-D',
    0x10: '8',
    0x11: '!',
    0x12: '"',
    0x13: '#',
    0x14: '$',
    0x15: '%',
    0x16: '^',
    0x17: '&',
    0x18: 'o',
    0x19: 'j',
    0x1A: 's',
    0x1B: 'ctrl-s',
    0x1C: 'n/N',
    0x1D: 'N',
    0x1E: 'H',
    0x1F: 'h',
}

CONTROL_CODE_BUTTON_COLORS = {
    0x00: '#101010',
    0x01: '#ff4a4a',
    0x02: '#34d058',
    0x03: '#ffd84d',
    0x04: '#4d7dff',
    0x05: '#ff4dff',
    0x06: '#46d9ff',
    0x07: '#f5f5f5',
    0x10: '#101010',
    0x11: '#ff4a4a',
    0x12: '#34d058',
    0x13: '#ffd84d',
    0x14: '#4d7dff',
    0x15: '#ff4dff',
    0x16: '#46d9ff',
    0x17: '#f5f5f5',
}

EDITOR_QUICK_ACTIONS = (
    ('Toggle Grid', 'a', 'toggle_show_grid'),
    ('Toggle Control Codes', 'q', 'toggle_show_control_codes'),
    ('Insert Row', 'i', 'insert_selected_row'),
    ('Remove Row', 'I', 'delete_selected_row'),
    ('Duplicate Row', 'u', 'duplicate_selected_row'),
    ('Clear Page', 'z', 'clear_page_content'),
    ('Reset Flags', 'Z', 'reset_page_flags'),
    ('Help', '?', 'show_control_keys_help'),
)


class T42RowTextDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QtWidgets.QLineEdit(parent)
        editor.setFrame(False)
        editor.setMaxLength(32 if index.row() == 0 else 40)
        editor.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        editor.setPlaceholderText('Header text' if index.row() == 0 else 'Row text')
        return editor


class T42EditorLoader(QtCore.QThread):
    loaded = QtCore.pyqtSignal(str, object, object, object)
    failed = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int, int, float)

    def __init__(self, filename):
        super().__init__()
        self._filename = os.path.abspath(filename)

    def run(self):
        raw_packets = []
        try:
            with open(self._filename, 'rb') as handle:
                chunks = FileChunker(handle, PACKET_SIZE)
                total = len(chunks) if hasattr(chunks, '__len__') else 0
                started_at = time.monotonic()
                processed = 0
                last_emitted = 0

                def packets():
                    nonlocal processed, last_emitted
                    for number, data in chunks:
                        raw = bytes(data)
                        raw_packets.append(raw)
                        processed += 1
                        if total and (processed == 1 or processed - last_emitted >= 4096 or processed == total):
                            last_emitted = processed
                            self.progress.emit(processed, total, time.monotonic() - started_at)
                        yield Packet(raw, number)

                service = Service.from_packets(packets())
                entries = build_t42_entries(raw_packets)
                page_summary = summarise_t42_pages(entries)
                if total:
                    self.progress.emit(total, total, time.monotonic() - started_at)
        except Exception as exc:  # pragma: no cover - GUI error path
            self.failed.emit(str(exc))
        else:
            self.loaded.emit(self._filename, tuple(entries), service, tuple(page_summary))


class TraceImageOverlay(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._opacity = 0.35
        self._x_offset = 0
        self._y_offset = 0
        self._scale = 1.0
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._rotation = 0.0
        self._flip_x = False
        self._flip_y = False
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.hide()

    def clear_source(self):
        self._pixmap = None
        self.hide()
        self.update()

    def set_source_pixmap(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else None
        self.setVisible(self._pixmap is not None)
        self.update()

    def set_adjustment(
        self,
        *,
        opacity=None,
        x_offset=None,
        y_offset=None,
        scale=None,
        scale_x=None,
        scale_y=None,
        rotation=None,
        flip_x=None,
        flip_y=None,
    ):
        if opacity is not None:
            self._opacity = max(0.0, min(float(opacity), 1.0))
        if x_offset is not None:
            self._x_offset = int(x_offset)
        if y_offset is not None:
            self._y_offset = int(y_offset)
        if scale is not None:
            self._scale = max(float(scale), 0.05)
        if scale_x is not None:
            self._scale_x = max(float(scale_x), 0.05)
        if scale_y is not None:
            self._scale_y = max(float(scale_y), 0.05)
        if rotation is not None:
            self._rotation = float(rotation)
        if flip_x is not None:
            self._flip_x = bool(flip_x)
        if flip_y is not None:
            self._flip_y = bool(flip_y)
        self.update()

    def paintEvent(self, event):  # pragma: no cover - GUI paint path
        super().paintEvent(event)
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(self._opacity)
        center_x = (self.width() / 2.0) + self._x_offset
        center_y = (self.height() / 2.0) + self._y_offset
        painter.translate(center_x, center_y)
        if self._rotation:
            painter.rotate(self._rotation)
        final_scale_x = self._scale * self._scale_x * (-1.0 if self._flip_x else 1.0)
        final_scale_y = self._scale * self._scale_y * (-1.0 if self._flip_y else 1.0)
        painter.scale(final_scale_x, final_scale_y)
        painter.drawPixmap(
            QtCore.QPointF(-self._pixmap.width() / 2.0, -self._pixmap.height() / 2.0),
            self._pixmap,
        )


class PreviewCursorOverlay(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = None
        self._col = None
        self._left = 0.0
        self._top = 0.0
        self._cell_width = 1.0
        self._cell_height = 1.0
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.hide()

    def set_grid_metrics(self, left, top, cell_width, cell_height):
        self._left = float(left)
        self._top = float(top)
        self._cell_width = max(float(cell_width), 1.0)
        self._cell_height = max(float(cell_height), 1.0)
        self.update()

    def clear_cursor(self):
        self._row = None
        self._col = None
        self.hide()
        self.update()

    def set_cursor(self, row, col):
        if row is None or col is None:
            self.clear_cursor()
            return
        self._row = max(0, min(int(row), 24))
        self._col = max(0, min(int(col), 39))
        self.show()
        self.update()

    def paintEvent(self, event):  # pragma: no cover - GUI paint path
        super().paintEvent(event)
        if self._row is None or self._col is None:
            return
        rect = QtCore.QRectF(
            self._left + (self._col * self._cell_width),
            self._top + (self._row * self._cell_height),
            self._cell_width,
            self._cell_height,
        )
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.fillRect(rect, QtGui.QColor(255, 255, 255, 38))
        painter.setPen(QtGui.QPen(QtGui.QColor('#ffcc33'), 2))
        painter.drawRect(rect)


class T42EditorWindow(QtWidgets.QMainWindow):
    def __init__(self, filename=None):
        super().__init__()
        self._filename = ''
        self._entries = ()
        self._service = None
        self._navigator = None
        self._page_summary = ()
        self._loader = None
        self._font_family = self._load_font_family()
        self._thumbnail_cache = {}
        self._thumbnail_queue = deque()
        self._thumbnail_total = 0
        self._split_dialog = None
        self._source_dialog = None
        self._source_preview_windows = []
        self._source_preview_temp_paths = set()
        self._preview_decoder = None
        self._preview_widget = None
        self._current_page_number = None
        self._current_subpage_number = None
        self._current_subpage_occurrence = 1
        self._editor_dirty = False
        self._editor_loading = False
        self._editor_row_presence = {}
        self._editor_original_text = {}
        self._editor_original_bytes = {}
        self._editor_live_bytes = {}
        self._tree_selection_locked = False
        self._trace_source_path = ''
        self._preview_cursor_row = None
        self._preview_cursor_col = None
        self._control_code_buttons = {}
        self._mouse_draw_active = False
        self._mouse_draw_erase = False
        self._mouse_draw_changed = False
        self._mouse_draw_last_target = None
        self._editor_history = []
        self._editor_history_index = -1
        self._editor_initial_snapshot = None
        self._editor_initial_signature = None
        self._editor_history_locked = False
        self._editor_drafts = {}
        self._document_history = []
        self._document_history_index = -1
        self._document_initial_snapshot = None
        self._document_history_locked = False
        self._editor_enabled_base = False
        self._row_clipboard_bytes = None
        self._row_clipboard_presence = False
        self._row_clipboard_row = None
        self._page_clipboard_subpage = None
        self._page_clipboard_label = ''
        self._modified_pages = set()
        self._modified_subpages = set()
        self._enabled_subpage_occurrences = set()
        self._tree_item_change_locked = False
        self._subpage_combo_locked = False
        self._language_options = (
            ('default', 'Default'),
            ('cyr', 'Cyrillic'),
            ('swe', 'Swedish'),
            ('ita', 'Italian'),
            ('deu', 'German'),
            ('fra', 'French'),
            ('pol', 'Polish'),
            ('nld', 'Dutch'),
        )
        self._character_set_options = CHARACTER_SET_OPTIONS

        self.setWindowTitle(EDITOR_APP_NAME)
        self.resize(1280, 820)
        self.setMinimumSize(1080, 680)
        self._build_ui()

        if filename:
            self.open_file(filename)
        else:
            self.new_empty_document()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self._toolbar_widget = QtWidgets.QWidget()
        self._toolbar_widget.hide()
        toolbar = QtWidgets.QHBoxLayout(self._toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._toolbar_widget)

        self._open_button = QtWidgets.QPushButton('Open .t42')
        self._open_button.clicked.connect(self.open_dialog)
        toolbar.addWidget(self._open_button)

        self._save_button = QtWidgets.QPushButton('Save .t42')
        self._save_button.clicked.connect(self.save_file)
        toolbar.addWidget(self._save_button)

        self._save_as_button = QtWidgets.QPushButton('Save As...')
        self._save_as_button.clicked.connect(self.save_file_as)
        toolbar.addWidget(self._save_as_button)

        self._save_page_button = QtWidgets.QPushButton('Save Page...')
        self._save_page_button.clicked.connect(self.save_current_page)
        toolbar.addWidget(self._save_page_button)

        self._save_subpage_button = QtWidgets.QPushButton('Save Subpage...')
        self._save_subpage_button.clicked.connect(self.save_current_subpage)
        toolbar.addWidget(self._save_subpage_button)

        self._split_button = QtWidgets.QPushButton('Split')
        self._split_button.clicked.connect(self.show_split_dialog)
        toolbar.addWidget(self._split_button)

        toolbar.addStretch(1)

        toolbar.addWidget(QtWidgets.QLabel('Zoom'))
        self._zoom_box = QtWidgets.QDoubleSpinBox()
        self._zoom_box.setRange(0.5, 4.0)
        self._zoom_box.setDecimals(1)
        self._zoom_box.setSingleStep(0.1)
        self._zoom_box.setValue(2.0)
        self._zoom_box.setSuffix('x')
        self._zoom_box.valueChanged.connect(self._zoom_changed)
        toolbar.addWidget(self._zoom_box)

        self._crt_toggle = QtWidgets.QCheckBox('CRT')
        self._crt_toggle.setChecked(True)
        self._crt_toggle.toggled.connect(self._update_decoder_preferences)
        toolbar.addWidget(self._crt_toggle)

        self._path_label = QtWidgets.QLabel('No file loaded.')
        self._path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        root.addWidget(self._path_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(splitter, 1)

        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        filter_row = QtWidgets.QHBoxLayout()
        left_layout.addLayout(filter_row)
        filter_row.addWidget(QtWidgets.QLabel('Filter'))
        self._filter_input = QtWidgets.QLineEdit()
        self._filter_input.setPlaceholderText('100, 1AF, 0001, title...')
        self._filter_input.textChanged.connect(lambda _text='': self._rebuild_tree())
        filter_row.addWidget(self._filter_input, 1)
        self._preview_toggle = QtWidgets.QCheckBox('Preview')
        self._preview_toggle.setChecked(False)
        self._preview_toggle.setToolTip('Show page preview thumbnails in the tree.')
        self._preview_toggle.toggled.connect(self._set_tree_previews_enabled)
        filter_row.addWidget(self._preview_toggle)
        self._tree_checkboxes_toggle = QtWidgets.QCheckBox('Checkboxes')
        self._tree_checkboxes_toggle.setChecked(False)
        self._tree_checkboxes_toggle.setToolTip('Show include/exclude checkboxes in the tree.')
        self._tree_checkboxes_toggle.toggled.connect(lambda _checked=False: self._rebuild_tree())
        filter_row.addWidget(self._tree_checkboxes_toggle)
        self._show_hidden_subpages_toggle = QtWidgets.QCheckBox('Hidden Subpages')
        self._show_hidden_subpages_toggle.toggled.connect(lambda _checked=False: self._rebuild_tree())
        filter_row.addWidget(self._show_hidden_subpages_toggle)
        filter_row.addWidget(QtWidgets.QLabel('Mode'))
        self._hidden_subpages_mode_combo = QtWidgets.QComboBox()
        self._hidden_subpages_mode_combo.addItem('Legacy', 'legacy')
        self._hidden_subpages_mode_combo.addItem('Exact', 'raw')
        self._hidden_subpages_mode_combo.currentIndexChanged.connect(self._hidden_subpages_mode_changed)
        filter_row.addWidget(self._hidden_subpages_mode_combo)

        self._tree_status_label = QtWidgets.QLabel('')
        self._tree_status_label.hide()
        left_layout.addWidget(self._tree_status_label)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(['Entry', 'Packets', 'At', 'Page/Sub/C', 'Title'])
        self._tree.setIconSize(QtCore.QSize(144, 108))
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._tree.installEventFilter(self)
        self._tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self._tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self._tree.itemDoubleClicked.connect(self._tree_item_activated)
        self._tree.itemChanged.connect(self._tree_item_changed)
        header = self._tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self._tree.setColumnWidth(0, 260)
        left_layout.addWidget(self._tree, 1)
        splitter.addWidget(left_panel)

        center_panel = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)

        self._selection_label = QtWidgets.QLabel('Page: ---')
        center_layout.addWidget(self._selection_label)

        self._decoder_widget = QtQuickWidgets.QQuickWidget()
        self._decoder_widget.setResizeMode(QtQuickWidgets.QQuickWidget.SizeViewToRootObject)
        self._decoder_widget.setClearColor(QtGui.QColor('black'))
        self._decoder_widget.setFocusPolicy(QtCore.Qt.ClickFocus)
        self._decoder_widget.installEventFilter(self)
        self._decoder = Decoder(self._decoder_widget, font_family=self._font_family)
        self._decoder.zoom = 2

        self._decoder_container = QtWidgets.QWidget()
        decoder_layout = QtWidgets.QHBoxLayout(self._decoder_container)
        decoder_layout.setContentsMargins(0, 0, 0, 0)
        decoder_layout.addStretch(1)
        self._preview_stage = QtWidgets.QWidget()
        self._preview_stage.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._preview_stage.installEventFilter(self)
        self._preview_stage_layout = QtWidgets.QGridLayout(self._preview_stage)
        self._preview_stage_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_stage_layout.setSpacing(0)
        self._preview_stage_layout.addWidget(self._decoder_widget, 0, 0, QtCore.Qt.AlignCenter)
        self._trace_overlay = TraceImageOverlay(self._preview_stage)
        self._preview_stage_layout.addWidget(self._trace_overlay, 0, 0, QtCore.Qt.AlignCenter)
        self._preview_cursor_overlay = PreviewCursorOverlay(self._preview_stage)
        self._preview_stage_layout.addWidget(self._preview_cursor_overlay, 0, 0, QtCore.Qt.AlignCenter)
        decoder_layout.addWidget(self._preview_stage)
        decoder_layout.addStretch(1)

        self._decoder_scroll = QtWidgets.QScrollArea()
        self._decoder_scroll.setWidgetResizable(True)
        self._decoder_scroll.setAlignment(QtCore.Qt.AlignCenter)
        self._decoder_scroll.setBackgroundRole(QtGui.QPalette.Dark)
        self._decoder_scroll.setWidget(self._decoder_container)
        center_layout.addWidget(self._decoder_scroll, 1)
        splitter.addWidget(center_panel)

        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._editor_group = QtWidgets.QGroupBox('Basic Edit')
        editor_layout = QtWidgets.QVBoxLayout(self._editor_group)
        editor_layout.setContentsMargins(8, 8, 8, 8)
        editor_layout.setSpacing(6)

        self._editor_controls_scroll = QtWidgets.QScrollArea()
        self._editor_controls_scroll.setWidgetResizable(True)
        self._editor_controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._editor_controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._editor_controls_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._editor_controls_widget = QtWidgets.QWidget()
        editor_controls_layout = QtWidgets.QVBoxLayout(self._editor_controls_widget)
        editor_controls_layout.setContentsMargins(0, 0, 0, 0)
        editor_controls_layout.setSpacing(6)
        editor_controls_layout.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        self._editor_controls_scroll.setWidget(self._editor_controls_widget)
        editor_layout.addWidget(self._editor_controls_scroll, 1)

        self._editor_hint_label = QtWidgets.QLabel(
            'Row 0 edits only the header text area (32 chars). Rows 1-24 edit 40-character display rows.'
        )
        self._editor_hint_label.setWordWrap(True)
        editor_controls_layout.addWidget(self._editor_hint_label)

        editor_options = QtWidgets.QGridLayout()
        editor_options.setHorizontalSpacing(10)
        editor_options.setVerticalSpacing(4)
        self._all_symbols_toggle = QtWidgets.QCheckBox('All Symbols')
        self._all_symbols_toggle.toggled.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._all_symbols_toggle, 0, 0)
        self._show_control_codes_toggle = QtWidgets.QCheckBox('Show Control Codes')
        self._show_control_codes_toggle.toggled.connect(self._show_control_codes_changed)
        editor_options.addWidget(self._show_control_codes_toggle, 0, 1)
        self._show_grid_toggle = QtWidgets.QCheckBox('Show Grid')
        self._show_grid_toggle.toggled.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._show_grid_toggle, 0, 2)
        self._single_height_toggle = QtWidgets.QCheckBox('Single Height')
        self._single_height_toggle.toggled.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._single_height_toggle, 0, 3)
        self._single_width_toggle = QtWidgets.QCheckBox('Single Width')
        self._single_width_toggle.toggled.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._single_width_toggle, 1, 0)
        self._no_flash_toggle = QtWidgets.QCheckBox('No Flash')
        self._no_flash_toggle.toggled.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._no_flash_toggle, 1, 1)
        self._mouse_draw_toggle = QtWidgets.QCheckBox('Mouse Draw')
        editor_options.addWidget(self._mouse_draw_toggle, 1, 2)
        self._widescreen_toggle = QtWidgets.QCheckBox('Widescreen Display')
        self._widescreen_toggle.toggled.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._widescreen_toggle, 1, 3)
        self._allow_header_edit_toggle = QtWidgets.QCheckBox('Allow Header Editing')
        self._allow_header_edit_toggle.toggled.connect(self._update_header_editing_state)
        editor_options.addWidget(self._allow_header_edit_toggle, 2, 0, 1, 2)
        editor_options.addWidget(QtWidgets.QLabel('Language'), 2, 2)
        self._language_combo = QtWidgets.QComboBox()
        for key, label in self._language_options:
            self._language_combo.addItem(label, key)
        self._language_combo.currentIndexChanged.connect(self._update_decoder_preferences)
        editor_options.addWidget(self._language_combo, 2, 3)
        editor_options.setColumnStretch(3, 1)
        editor_controls_layout.addLayout(editor_options)

        page_options_group = QtWidgets.QGroupBox('Page Options')
        page_options_layout = QtWidgets.QHBoxLayout(page_options_group)
        page_options_layout.setContentsMargins(8, 8, 8, 8)
        page_options_layout.setSpacing(8)
        page_options_layout.addWidget(QtWidgets.QLabel('Page'))
        self._page_option_page_input = QtWidgets.QLineEdit()
        self._page_option_page_input.setMaxLength(3)
        self._page_option_page_input.setFixedWidth(70)
        self._page_option_page_input.setPlaceholderText('100')
        page_options_layout.addWidget(self._page_option_page_input)
        page_options_layout.addWidget(QtWidgets.QLabel('Subpage'))
        self._page_option_subpage_input = QtWidgets.QLineEdit()
        self._page_option_subpage_input.setMaxLength(4)
        self._page_option_subpage_input.setFixedWidth(84)
        self._page_option_subpage_input.setPlaceholderText('0000')
        page_options_layout.addWidget(self._page_option_subpage_input)
        self._apply_page_options_button = QtWidgets.QPushButton('Apply Options')
        self._apply_page_options_button.clicked.connect(self.apply_page_options)
        page_options_layout.addWidget(self._apply_page_options_button)
        self._reset_page_options_button = QtWidgets.QPushButton('Reset Options')
        self._reset_page_options_button.clicked.connect(self.reset_page_options)
        page_options_layout.addWidget(self._reset_page_options_button)
        page_options_layout.addStretch(1)
        editor_controls_layout.addWidget(page_options_group)
        self._page_options_group = page_options_group
        self._make_group_collapsible(self._page_options_group, expanded=True)

        page_flags_group = QtWidgets.QGroupBox('Page Flags')
        page_flags_layout = QtWidgets.QGridLayout(page_flags_group)
        page_flags_layout.setContentsMargins(8, 8, 8, 8)
        page_flags_layout.setHorizontalSpacing(12)
        page_flags_layout.setVerticalSpacing(4)

        self._erase_page_toggle = QtWidgets.QCheckBox('Erase Page')
        page_flags_layout.addWidget(self._erase_page_toggle, 0, 0)
        self._newsflash_toggle = QtWidgets.QCheckBox('Newsflash')
        page_flags_layout.addWidget(self._newsflash_toggle, 0, 1)
        self._subtitle_toggle = QtWidgets.QCheckBox('Subtitle')
        page_flags_layout.addWidget(self._subtitle_toggle, 0, 2)
        self._suppress_header_toggle = QtWidgets.QCheckBox('Suppress Header')
        page_flags_layout.addWidget(self._suppress_header_toggle, 0, 3)

        self._update_page_toggle = QtWidgets.QCheckBox('Update Page')
        page_flags_layout.addWidget(self._update_page_toggle, 1, 0)
        self._interrupted_sequence_toggle = QtWidgets.QCheckBox('Interrupted Sequence')
        page_flags_layout.addWidget(self._interrupted_sequence_toggle, 1, 1)
        self._inhibit_display_toggle = QtWidgets.QCheckBox('Inhibit Display')
        page_flags_layout.addWidget(self._inhibit_display_toggle, 1, 2)
        self._magazine_serial_toggle = QtWidgets.QCheckBox('Magazine Serial')
        page_flags_layout.addWidget(self._magazine_serial_toggle, 1, 3)

        page_flags_layout.addWidget(QtWidgets.QLabel('Page Region'), 2, 0)
        self._page_region_spin = QtWidgets.QSpinBox()
        self._page_region_spin.setRange(0, 7)
        page_flags_layout.addWidget(self._page_region_spin, 2, 1)
        page_flags_layout.addWidget(QtWidgets.QLabel('Character Set'), 3, 0)
        self._character_set_combo = QtWidgets.QComboBox()
        for codepage, label, _language_key in self._character_set_options:
            self._character_set_combo.addItem(label, codepage)
        self._character_set_combo.currentIndexChanged.connect(self._character_set_changed)
        page_flags_layout.addWidget(self._character_set_combo, 3, 1, 1, 2)
        for widget in (
            self._erase_page_toggle,
            self._newsflash_toggle,
            self._subtitle_toggle,
            self._suppress_header_toggle,
            self._update_page_toggle,
            self._interrupted_sequence_toggle,
            self._inhibit_display_toggle,
            self._magazine_serial_toggle,
        ):
            widget.toggled.connect(self._editor_meta_changed)
        self._page_region_spin.valueChanged.connect(self._editor_meta_changed)
        self._page_region_spin.valueChanged.connect(self._sync_character_set_from_page_region)
        self._reset_page_flags_button = QtWidgets.QPushButton('Reset Flags')
        self._reset_page_flags_button.clicked.connect(self.reset_page_flags)
        page_flags_layout.addWidget(self._reset_page_flags_button, 3, 3)
        page_flags_layout.setColumnStretch(4, 1)
        editor_controls_layout.addWidget(page_flags_group)
        self._page_flags_group = page_flags_group
        self._make_group_collapsible(self._page_flags_group, expanded=False)

        service_830_group = QtWidgets.QGroupBox('Service 8/30')
        service_830_layout = QtWidgets.QGridLayout(service_830_group)
        service_830_layout.setContentsMargins(8, 8, 8, 8)
        service_830_layout.setHorizontalSpacing(8)
        service_830_layout.setVerticalSpacing(6)

        self._service_830_enabled_toggle = QtWidgets.QCheckBox('Enable 8/30')
        self._service_830_enabled_toggle.toggled.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_enabled_toggle, 0, 0, 1, 2)

        service_830_layout.addWidget(QtWidgets.QLabel('Designation'), 0, 2)
        self._service_830_dc_combo = QtWidgets.QComboBox()
        self._service_830_dc_combo.addItem('0 - Format 1', 0)
        self._service_830_dc_combo.addItem('1 - Format 1', 1)
        self._service_830_dc_combo.addItem('2 - Format 2', 2)
        self._service_830_dc_combo.addItem('3 - Format 2', 3)
        self._service_830_dc_combo.currentIndexChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_dc_combo, 0, 3)

        service_830_layout.addWidget(QtWidgets.QLabel('Initial Page'), 1, 0)
        self._service_830_page_input = QtWidgets.QLineEdit()
        self._service_830_page_input.setMaxLength(3)
        self._service_830_page_input.setFixedWidth(70)
        self._service_830_page_input.setPlaceholderText('100')
        self._service_830_page_input.textChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_page_input, 1, 1)

        service_830_layout.addWidget(QtWidgets.QLabel('Label'), 1, 2)
        self._service_830_label_input = QtWidgets.QLineEdit()
        self._service_830_label_input.setMaxLength(20)
        self._service_830_label_input.setPlaceholderText('Service line text')
        self._service_830_label_input.textChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_label_input, 1, 3)

        service_830_layout.addWidget(QtWidgets.QLabel('Network'), 2, 0)
        self._service_830_network_input = QtWidgets.QLineEdit()
        self._service_830_network_input.setMaxLength(4)
        self._service_830_network_input.setPlaceholderText('0000 / 00')
        self._service_830_network_input.textChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_network_input, 2, 1)

        service_830_layout.addWidget(QtWidgets.QLabel('Country / UTC Offset'), 2, 2)
        self._service_830_country_offset_input = QtWidgets.QLineEdit()
        self._service_830_country_offset_input.setMaxLength(8)
        self._service_830_country_offset_input.setPlaceholderText('00 / 0')
        self._service_830_country_offset_input.textChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_country_offset_input, 2, 3)

        service_830_layout.addWidget(QtWidgets.QLabel('Date'), 3, 0)
        self._service_830_date_input = QtWidgets.QLineEdit()
        self._service_830_date_input.setMaxLength(10)
        self._service_830_date_input.setPlaceholderText('YYYY-MM-DD / DD/MM')
        self._service_830_date_input.textChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_date_input, 3, 1)

        service_830_layout.addWidget(QtWidgets.QLabel('Time'), 3, 2)
        self._service_830_time_input = QtWidgets.QLineEdit()
        self._service_830_time_input.setMaxLength(8)
        self._service_830_time_input.setPlaceholderText('HH:MM:SS / HH:MM')
        self._service_830_time_input.textChanged.connect(self._service_830_changed)
        service_830_layout.addWidget(self._service_830_time_input, 3, 3)

        self._reset_service_830_button = QtWidgets.QPushButton('Reset 8/30')
        self._reset_service_830_button.clicked.connect(self.reset_service_830)
        service_830_layout.addWidget(self._reset_service_830_button, 4, 2)

        self._service_830_summary_label = QtWidgets.QLabel('No 8/30 packet in the current file.')
        self._service_830_summary_label.setWordWrap(True)
        self._service_830_summary_label.setStyleSheet('color: #666666;')
        service_830_layout.addWidget(self._service_830_summary_label, 4, 0, 1, 2)
        service_830_layout.setColumnStretch(3, 1)

        editor_controls_layout.addWidget(service_830_group)
        self._service_830_group = service_830_group
        self._make_group_collapsible(self._service_830_group, expanded=False)

        fastext_group = QtWidgets.QGroupBox('Fastext')
        fastext_layout = QtWidgets.QGridLayout(fastext_group)
        fastext_layout.setContentsMargins(8, 8, 8, 8)
        fastext_layout.setHorizontalSpacing(8)
        fastext_layout.setVerticalSpacing(6)
        fastext_layout.addWidget(QtWidgets.QLabel('Color'), 0, 0)
        fastext_layout.addWidget(QtWidgets.QLabel('Page'), 0, 1)
        fastext_layout.addWidget(QtWidgets.QLabel('Subpage'), 0, 2)
        self._fastext_inputs = []
        for row, (label, colour) in enumerate(FASTEXT_FIELDS, start=1):
            color_label = QtWidgets.QLabel(label)
            color_label.setStyleSheet(f'color: {colour}; font-weight: 600;')
            fastext_layout.addWidget(color_label, row, 0)
            page_input = QtWidgets.QLineEdit()
            page_input.setMaxLength(3)
            page_input.setFixedWidth(70)
            page_input.setPlaceholderText('100')
            page_input.textChanged.connect(self._editor_meta_changed)
            fastext_layout.addWidget(page_input, row, 1)
            subpage_input = QtWidgets.QLineEdit()
            subpage_input.setMaxLength(4)
            subpage_input.setFixedWidth(84)
            subpage_input.setPlaceholderText('0000')
            subpage_input.textChanged.connect(self._editor_meta_changed)
            fastext_layout.addWidget(subpage_input, row, 2)
            self._fastext_inputs.append((page_input, subpage_input))
        self._reset_fastext_button = QtWidgets.QPushButton('Reset Fastext')
        self._reset_fastext_button.clicked.connect(self.reset_fastext)
        fastext_layout.addWidget(self._reset_fastext_button, len(FASTEXT_FIELDS) + 1, 1, 1, 2)
        fastext_layout.setColumnStretch(3, 1)
        editor_controls_layout.addWidget(fastext_group)
        self._fastext_group = fastext_group
        self._make_group_collapsible(self._fastext_group, expanded=False)

        trace_group = QtWidgets.QGroupBox('Trace Image')
        trace_layout = QtWidgets.QGridLayout(trace_group)
        trace_layout.setContentsMargins(8, 8, 8, 8)
        trace_layout.setHorizontalSpacing(8)
        trace_layout.setVerticalSpacing(6)

        self._trace_select_button = QtWidgets.QPushButton('Select File')
        self._trace_select_button.clicked.connect(self.select_trace_image)
        trace_layout.addWidget(self._trace_select_button, 0, 0)

        self._trace_clear_button = QtWidgets.QPushButton('Clear Image')
        self._trace_clear_button.clicked.connect(self.clear_trace_image)
        trace_layout.addWidget(self._trace_clear_button, 0, 1)

        trace_layout.addWidget(QtWidgets.QLabel('Opacity'), 1, 0)
        self._trace_opacity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._trace_opacity_slider.setRange(0, 100)
        self._trace_opacity_slider.setValue(35)
        self._trace_opacity_slider.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_opacity_slider, 1, 1, 1, 3)

        trace_layout.addWidget(QtWidgets.QLabel('X'), 2, 0)
        self._trace_x_offset = QtWidgets.QSpinBox()
        self._trace_x_offset.setRange(-2000, 2000)
        self._trace_x_offset.setSingleStep(1)
        self._trace_x_offset.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_x_offset, 2, 1)

        trace_layout.addWidget(QtWidgets.QLabel('Y'), 2, 2)
        self._trace_y_offset = QtWidgets.QSpinBox()
        self._trace_y_offset.setRange(-2000, 2000)
        self._trace_y_offset.setSingleStep(1)
        self._trace_y_offset.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_y_offset, 2, 3)

        trace_layout.addWidget(QtWidgets.QLabel('Scale'), 3, 0)
        self._trace_scale = QtWidgets.QDoubleSpinBox()
        self._trace_scale.setRange(0.05, 8.0)
        self._trace_scale.setDecimals(2)
        self._trace_scale.setSingleStep(0.05)
        self._trace_scale.setValue(1.0)
        self._trace_scale.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_scale, 3, 1)

        trace_layout.addWidget(QtWidgets.QLabel('Rotation'), 3, 2)
        self._trace_rotation = QtWidgets.QDoubleSpinBox()
        self._trace_rotation.setRange(-360.0, 360.0)
        self._trace_rotation.setDecimals(1)
        self._trace_rotation.setSingleStep(1.0)
        self._trace_rotation.setValue(0.0)
        self._trace_rotation.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_rotation, 3, 3)

        trace_layout.addWidget(QtWidgets.QLabel('Scale X'), 4, 0)
        self._trace_scale_x = QtWidgets.QDoubleSpinBox()
        self._trace_scale_x.setRange(0.05, 8.0)
        self._trace_scale_x.setDecimals(2)
        self._trace_scale_x.setSingleStep(0.05)
        self._trace_scale_x.setValue(1.0)
        self._trace_scale_x.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_scale_x, 4, 1)

        trace_layout.addWidget(QtWidgets.QLabel('Scale Y'), 4, 2)
        self._trace_scale_y = QtWidgets.QDoubleSpinBox()
        self._trace_scale_y.setRange(0.05, 8.0)
        self._trace_scale_y.setDecimals(2)
        self._trace_scale_y.setSingleStep(0.05)
        self._trace_scale_y.setValue(1.0)
        self._trace_scale_y.valueChanged.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_scale_y, 4, 3)

        self._trace_flip_x = QtWidgets.QCheckBox('Flip X')
        self._trace_flip_x.toggled.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_flip_x, 5, 0, 1, 2)

        self._trace_flip_y = QtWidgets.QCheckBox('Flip Y')
        self._trace_flip_y.toggled.connect(self._update_trace_overlay)
        trace_layout.addWidget(self._trace_flip_y, 5, 2, 1, 2)

        self._trace_reset_button = QtWidgets.QPushButton('Reset Trace')
        self._trace_reset_button.clicked.connect(self.reset_trace_adjustments)
        trace_layout.addWidget(self._trace_reset_button, 6, 0, 1, 4)

        self._trace_status_label = QtWidgets.QLabel('No trace image selected.')
        self._trace_status_label.setStyleSheet('color: #666666;')
        self._trace_status_label.setWordWrap(True)
        trace_layout.addWidget(self._trace_status_label, 7, 0, 1, 4)
        editor_controls_layout.addWidget(trace_group)
        self._trace_group = trace_group
        self._make_group_collapsible(self._trace_group, expanded=False)

        row_tools_group = QtWidgets.QGroupBox('Row Tools')
        row_tools_layout = QtWidgets.QGridLayout(row_tools_group)
        row_tools_layout.setContentsMargins(8, 8, 8, 8)
        row_tools_layout.setHorizontalSpacing(8)
        row_tools_layout.setVerticalSpacing(6)

        self._insert_row_button = QtWidgets.QPushButton('Insert Row')
        self._insert_row_button.clicked.connect(self.insert_selected_row)
        row_tools_layout.addWidget(self._insert_row_button, 0, 0)
        self._remove_row_button = QtWidgets.QPushButton('Remove Row')
        self._remove_row_button.clicked.connect(self.delete_selected_row)
        row_tools_layout.addWidget(self._remove_row_button, 0, 1)
        self._duplicate_row_button = QtWidgets.QPushButton('Duplicate Row')
        self._duplicate_row_button.clicked.connect(self.duplicate_selected_row)
        row_tools_layout.addWidget(self._duplicate_row_button, 1, 0)
        self._clear_row_button = QtWidgets.QPushButton('Delete Row')
        self._clear_row_button.clicked.connect(self.clear_selected_row)
        row_tools_layout.addWidget(self._clear_row_button, 1, 1)
        self._black_row_button = QtWidgets.QPushButton('Black Row')
        self._black_row_button.clicked.connect(self.black_selected_row)
        row_tools_layout.addWidget(self._black_row_button, 2, 0)
        self._move_row_up_button = QtWidgets.QPushButton('Move Up')
        self._move_row_up_button.clicked.connect(self.move_selected_row_up)
        row_tools_layout.addWidget(self._move_row_up_button, 2, 1)
        self._move_row_down_button = QtWidgets.QPushButton('Move Down')
        self._move_row_down_button.clicked.connect(self.move_selected_row_down)
        row_tools_layout.addWidget(self._move_row_down_button, 3, 0)
        self._copy_row_button = QtWidgets.QPushButton('Copy Row')
        self._copy_row_button.clicked.connect(self.copy_selected_row)
        row_tools_layout.addWidget(self._copy_row_button, 3, 1)
        self._copy_row_text_button = QtWidgets.QPushButton('Copy Row Text')
        self._copy_row_text_button.clicked.connect(self.copy_selected_row_text)
        row_tools_layout.addWidget(self._copy_row_text_button, 4, 0)
        self._paste_row_button = QtWidgets.QPushButton('Paste Row')
        self._paste_row_button.clicked.connect(self.paste_selected_row)
        row_tools_layout.addWidget(self._paste_row_button, 4, 1)
        self._cut_row_button = QtWidgets.QPushButton('Cut Row')
        self._cut_row_button.clicked.connect(self.cut_selected_row)
        row_tools_layout.addWidget(self._cut_row_button, 5, 0)

        editor_controls_layout.addWidget(row_tools_group)
        self._row_tools_group = row_tools_group
        self._make_group_collapsible(self._row_tools_group, expanded=False)

        page_tools_group = QtWidgets.QGroupBox('Page Tool')
        page_tools_layout = QtWidgets.QGridLayout(page_tools_group)
        page_tools_layout.setContentsMargins(8, 8, 8, 8)
        page_tools_layout.setHorizontalSpacing(8)
        page_tools_layout.setVerticalSpacing(6)
        self._copy_page_button = QtWidgets.QPushButton('Copy Page')
        self._copy_page_button.clicked.connect(self.copy_current_page)
        page_tools_layout.addWidget(self._copy_page_button, 0, 0)
        self._paste_page_button = QtWidgets.QPushButton('Paste Page')
        self._paste_page_button.clicked.connect(self.paste_current_page)
        page_tools_layout.addWidget(self._paste_page_button, 0, 1)
        self._copy_page_text_button = QtWidgets.QPushButton('Copy Page Text')
        self._copy_page_text_button.clicked.connect(self.copy_current_page_text)
        page_tools_layout.addWidget(self._copy_page_text_button, 1, 0)
        self._save_screenshot_button = QtWidgets.QPushButton('Save Screenshot')
        self._save_screenshot_button.clicked.connect(self.save_screenshot)
        page_tools_layout.addWidget(self._save_screenshot_button, 1, 1)
        self._copy_screenshot_button = QtWidgets.QPushButton('Copy Screenshot')
        self._copy_screenshot_button.clicked.connect(self.copy_screenshot)
        page_tools_layout.addWidget(self._copy_screenshot_button, 2, 0, 1, 2)
        self._clear_page_button = QtWidgets.QPushButton('Clear Page')
        self._clear_page_button.clicked.connect(self.clear_page_content)
        page_tools_layout.addWidget(self._clear_page_button, 3, 0, 1, 2)
        editor_controls_layout.addWidget(page_tools_group)
        self._page_tools_group = page_tools_group
        self._make_group_collapsible(self._page_tools_group, expanded=False)

        import_group = QtWidgets.QGroupBox('Import T42')
        import_layout = QtWidgets.QGridLayout(import_group)
        import_layout.setContentsMargins(8, 8, 8, 8)
        import_layout.setHorizontalSpacing(8)
        import_layout.setVerticalSpacing(6)
        self._import_t42_button = QtWidgets.QPushButton('Import T42...')
        self._import_t42_button.clicked.connect(self.open_import_dialog)
        import_layout.addWidget(self._import_t42_button, 0, 0, 1, 2)
        import_hint = QtWidgets.QLabel('Open one source dialog to import/replace page, subpage or row.')
        import_hint.setWordWrap(True)
        import_hint.setStyleSheet('color: #666666;')
        import_layout.addWidget(import_hint, 1, 0, 1, 2)
        editor_controls_layout.addWidget(import_group)
        self._import_group = import_group
        self._make_group_collapsible(self._import_group, expanded=False)

        control_keys_group = QtWidgets.QGroupBox('Control Keys')
        control_keys_layout = QtWidgets.QVBoxLayout(control_keys_group)
        control_keys_layout.setContentsMargins(8, 8, 8, 8)
        control_keys_layout.setSpacing(6)
        self._control_keys_hint_label = QtWidgets.QLabel('Hover buttons for hints. Click to apply a control code or action.')
        self._control_keys_hint_label.setWordWrap(True)
        self._control_keys_hint_label.setStyleSheet('color: #666666;')
        control_keys_layout.addWidget(self._control_keys_hint_label)
        self._control_keys_sections = []
        self._build_control_keys_panel(control_keys_layout)
        editor_controls_layout.addWidget(control_keys_group)
        self._control_keys_group = control_keys_group
        self._make_group_collapsible(self._control_keys_group, expanded=False)
        editor_controls_layout.addStretch(1)

        text_group = QtWidgets.QGroupBox('Text')
        text_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        text_layout = QtWidgets.QVBoxLayout(text_group)
        text_layout.setContentsMargins(8, 8, 8, 8)
        text_layout.setSpacing(6)
        self._text_group = text_group
        self._make_group_collapsible(self._text_group, expanded=True)

        self._editor_table = QtWidgets.QTableWidget(25, 1)
        self._editor_table.setAlternatingRowColors(True)
        self._editor_table.setItemDelegate(T42RowTextDelegate(self._editor_table))
        self._editor_table.setHorizontalHeaderLabels(['Text'])
        self._editor_table.horizontalHeader().setStretchLastSection(True)
        self._editor_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self._editor_table.verticalHeader().setDefaultSectionSize(24)
        self._editor_table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self._editor_table.setVerticalHeaderLabels(
            ['00 Header'] + [f'{row:02d}' for row in range(1, 25)]
        )
        self._editor_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._editor_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._editor_table.setMinimumHeight(300)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self._editor_table.setFont(fixed_font)
        for row in range(25):
            item = QtWidgets.QTableWidgetItem('')
            item.setFont(fixed_font)
            if row == 0:
                item.setToolTip('Header text only. Page/subpage numbers stay unchanged.')
            else:
                item.setToolTip(f'Display row {row}.')
            self._editor_table.setItem(row, 0, item)
        text_layout.addWidget(self._editor_table, 1)

        editor_actions = QtWidgets.QHBoxLayout()
        self._editor_status_label = QtWidgets.QLabel('No subpage selected.')
        self._editor_status_label.setStyleSheet('color: #666666;')
        editor_actions.addWidget(self._editor_status_label, 1)
        self._undo_edit_button = QtWidgets.QPushButton('Undo')
        self._undo_edit_button.clicked.connect(self.undo_current_edits)
        editor_actions.addWidget(self._undo_edit_button)
        self._redo_edit_button = QtWidgets.QPushButton('Redo')
        self._redo_edit_button.clicked.connect(self.redo_current_edits)
        editor_actions.addWidget(self._redo_edit_button)
        self._reset_edit_button = QtWidgets.QPushButton('Reset')
        self._reset_edit_button.clicked.connect(self.reset_current_edits)
        editor_actions.addWidget(self._reset_edit_button)
        self._apply_edit_button = QtWidgets.QPushButton('Apply')
        self._apply_edit_button.clicked.connect(self.apply_current_edits)
        editor_actions.addWidget(self._apply_edit_button)
        text_layout.addLayout(editor_actions)
        editor_layout.addWidget(self._text_group, 0)
        self._editor_table.itemChanged.connect(self._editor_item_changed)

        right_layout.addWidget(self._editor_group, 1)
        right_panel.setMaximumWidth(860)
        right_panel.setMinimumWidth(520)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([360, 860, 500])

        self._thumbnail_timer = QtCore.QTimer(self)
        self._thumbnail_timer.setInterval(5)
        self._thumbnail_timer.timeout.connect(self._populate_thumbnail_batch)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedWidth(220)
        self.statusBar().addPermanentWidget(self._progress)

        self._build_menus()
        self._set_loaded_state(False)
        self._clear_decoder()

    def _build_menus(self):
        menu = self.menuBar()

        file_menu = menu.addMenu('File')
        new_action = file_menu.addAction('New Empty T42')
        new_action.setShortcut(QtGui.QKeySequence.New)
        new_action.triggered.connect(self.new_empty_document)
        file_menu.addSeparator()
        open_action = file_menu.addAction('Open .t42...')
        open_action.triggered.connect(self.open_dialog)
        file_menu.addSeparator()
        save_action = file_menu.addAction('Save .t42')
        save_action.triggered.connect(self.save_file)
        save_as_action = file_menu.addAction('Save .t42 As...')
        save_as_action.triggered.connect(self.save_file_as)
        file_menu.addSeparator()
        save_page_action = file_menu.addAction('Save Current Page...')
        save_page_action.triggered.connect(self.save_current_page)
        save_subpage_action = file_menu.addAction('Save Current Subpage...')
        save_subpage_action.triggered.connect(self.save_current_subpage)
        file_menu.addSeparator()
        save_screenshot_action = file_menu.addAction('Save Screenshot...')
        save_screenshot_action.triggered.connect(self.save_screenshot)
        copy_screenshot_action = file_menu.addAction('Copy Screenshot')
        copy_screenshot_action.triggered.connect(self.copy_screenshot)
        file_menu.addSeparator()
        import_t42_action = file_menu.addAction('Import T42...')
        import_t42_action.triggered.connect(self.open_import_dialog)
        file_menu.addSeparator()
        split_action = file_menu.addAction('Split...')
        split_action.triggered.connect(self.show_split_dialog)
        file_menu.addSeparator()
        close_action = file_menu.addAction('Close')
        close_action.triggered.connect(self.close)

        self._file_actions = (
            save_action,
            save_as_action,
            save_page_action,
            save_subpage_action,
            save_screenshot_action,
            copy_screenshot_action,
            import_t42_action,
            split_action,
        )

        page_menu = menu.addMenu('Page')
        self._new_page_action = page_menu.addAction('Add Blank Page...')
        self._new_page_action.triggered.connect(self.add_blank_page)
        self._new_subpage_action = page_menu.addAction('Add Blank Subpage...')
        self._new_subpage_action.triggered.connect(self.add_blank_subpage)
        self._new_hidden_subpage_action = page_menu.addAction('Add Blank Hidden Subpage...')
        self._new_hidden_subpage_action.triggered.connect(self.add_hidden_subpage)
        page_menu.addSeparator()
        self._duplicate_page_action = page_menu.addAction('Duplicate Current Page...')
        self._duplicate_page_action.triggered.connect(self.duplicate_current_page)
        self._duplicate_subpage_action = page_menu.addAction('Duplicate Current Subpage...')
        self._duplicate_subpage_action.triggered.connect(self.duplicate_current_subpage)
        self._duplicate_hidden_subpage_action = page_menu.addAction('Duplicate Current Hidden Subpage...')
        self._duplicate_hidden_subpage_action.triggered.connect(self.duplicate_current_hidden_subpage)
        self._convert_all_hidden_subpages_action = page_menu.addAction('Convert All Hidden Subpages to Real')
        self._convert_all_hidden_subpages_action.triggered.connect(self._convert_all_hidden_subpages_to_real)
        page_menu.addSeparator()
        self._delete_page_action = page_menu.addAction('Delete Current Page')
        self._delete_page_action.triggered.connect(self.delete_current_page)
        self._delete_subpage_action = page_menu.addAction('Delete Current Subpage')
        self._delete_subpage_action.triggered.connect(self.delete_current_subpage)
        self._page_actions = (
            self._new_page_action,
            self._new_subpage_action,
            self._new_hidden_subpage_action,
            self._duplicate_page_action,
            self._duplicate_subpage_action,
            self._duplicate_hidden_subpage_action,
            self._convert_all_hidden_subpages_action,
            self._delete_page_action,
            self._delete_subpage_action,
        )

        edit_menu = menu.addMenu('Edit')
        self._undo_edit_action = edit_menu.addAction('Undo')
        self._undo_edit_action.setShortcut(QtGui.QKeySequence.Undo)
        self._undo_edit_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._undo_edit_action.triggered.connect(self.undo_current_edits)
        self._redo_edit_action = edit_menu.addAction('Redo')
        self._redo_edit_action.setShortcut(QtGui.QKeySequence.Redo)
        self._redo_edit_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._redo_edit_action.triggered.connect(self.redo_current_edits)
        self._reset_edit_action = edit_menu.addAction('Reset Current')
        self._reset_edit_action.setShortcut(QtGui.QKeySequence('Ctrl+R'))
        self._reset_edit_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._reset_edit_action.triggered.connect(self.reset_current_edits)
        edit_menu.addSeparator()
        self._apply_edit_action = edit_menu.addAction('Apply Current')
        self._apply_edit_action.triggered.connect(self.apply_current_edits)
        edit_menu.addSeparator()
        self._insert_row_action = edit_menu.addAction('Insert Row')
        self._insert_row_action.triggered.connect(self.insert_selected_row)
        self._delete_row_action = edit_menu.addAction('Remove Row')
        self._delete_row_action.triggered.connect(self.delete_selected_row)
        self._duplicate_row_action = edit_menu.addAction('Duplicate Row')
        self._duplicate_row_action.triggered.connect(self.duplicate_selected_row)
        self._black_row_action = edit_menu.addAction('Black Row')
        self._black_row_action.triggered.connect(self.black_selected_row)
        edit_menu.addSeparator()
        self._copy_row_action = edit_menu.addAction('Copy Row')
        self._copy_row_action.setShortcut(QtGui.QKeySequence('Ctrl+Shift+C'))
        self._copy_row_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._copy_row_action.triggered.connect(self.copy_selected_row)
        self._copy_row_text_action = edit_menu.addAction('Copy Row Text')
        self._copy_row_text_action.setShortcut(QtGui.QKeySequence('Ctrl+Shift+T'))
        self._copy_row_text_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._copy_row_text_action.triggered.connect(self.copy_selected_row_text)
        self._copy_page_action = edit_menu.addAction('Copy Current Page')
        self._copy_page_action.setShortcut(QtGui.QKeySequence('Ctrl+Alt+C'))
        self._copy_page_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._copy_page_action.triggered.connect(self.copy_current_page)
        self._copy_page_text_action = edit_menu.addAction('Copy Current Page Text')
        self._copy_page_text_action.setShortcut(QtGui.QKeySequence('Ctrl+Alt+T'))
        self._copy_page_text_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._copy_page_text_action.triggered.connect(self.copy_current_page_text)
        self._paste_page_action = edit_menu.addAction('Paste Current Page')
        self._paste_page_action.setShortcut(QtGui.QKeySequence('Ctrl+Alt+V'))
        self._paste_page_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._paste_page_action.triggered.connect(self.paste_current_page)
        self._paste_row_action = edit_menu.addAction('Paste Row')
        self._paste_row_action.setShortcut(QtGui.QKeySequence('Ctrl+Shift+V'))
        self._paste_row_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._paste_row_action.triggered.connect(self.paste_selected_row)
        self._cut_row_action = edit_menu.addAction('Cut Row')
        self._cut_row_action.setShortcut(QtGui.QKeySequence('Ctrl+Shift+X'))
        self._cut_row_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._cut_row_action.triggered.connect(self.cut_selected_row)
        edit_menu.addSeparator()
        self._move_row_up_action = edit_menu.addAction('Move Row Up')
        self._move_row_up_action.setShortcut(QtGui.QKeySequence('Alt+Up'))
        self._move_row_up_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._move_row_up_action.triggered.connect(self.move_selected_row_up)
        self._move_row_down_action = edit_menu.addAction('Move Row Down')
        self._move_row_down_action.setShortcut(QtGui.QKeySequence('Alt+Down'))
        self._move_row_down_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._move_row_down_action.triggered.connect(self.move_selected_row_down)
        self._clear_row_action = edit_menu.addAction('Delete Row')
        self._clear_row_action.triggered.connect(self.clear_selected_row)
        self._clear_page_action = edit_menu.addAction('Clear Page')
        self._clear_page_action.triggered.connect(self.clear_page_content)
        self._edit_actions = (
            self._undo_edit_action,
            self._redo_edit_action,
            self._reset_edit_action,
            self._apply_edit_action,
            self._insert_row_action,
            self._delete_row_action,
            self._duplicate_row_action,
            self._black_row_action,
            self._copy_row_action,
            self._copy_row_text_action,
            self._copy_page_action,
            self._copy_page_text_action,
            self._paste_page_action,
            self._paste_row_action,
            self._cut_row_action,
            self._move_row_up_action,
            self._move_row_down_action,
            self._clear_row_action,
            self._clear_page_action,
        )

        view_menu = menu.addMenu('View')

        self._preview_action = view_menu.addAction('Preview')
        self._preview_action.setCheckable(True)
        self._preview_action.setChecked(self._preview_toggle.isChecked())
        self._preview_action.toggled.connect(self._preview_toggle.setChecked)
        self._preview_toggle.toggled.connect(self._preview_action.setChecked)

        all_symbols_action = view_menu.addAction('All Symbols')
        all_symbols_action.setCheckable(True)
        all_symbols_action.toggled.connect(self._all_symbols_toggle.setChecked)
        self._all_symbols_toggle.toggled.connect(all_symbols_action.setChecked)

        crt_action = view_menu.addAction('CRT')
        crt_action.setCheckable(True)
        crt_action.setChecked(True)
        crt_action.toggled.connect(self._crt_toggle.setChecked)
        self._crt_toggle.toggled.connect(crt_action.setChecked)

        control_codes_action = view_menu.addAction('Show Control Codes')
        control_codes_action.setCheckable(True)
        control_codes_action.toggled.connect(self._show_control_codes_toggle.setChecked)
        self._show_control_codes_toggle.toggled.connect(control_codes_action.setChecked)

        show_grid_action = view_menu.addAction('Show Grid')
        show_grid_action.setCheckable(True)
        show_grid_action.toggled.connect(self._show_grid_toggle.setChecked)
        self._show_grid_toggle.toggled.connect(show_grid_action.setChecked)

        mouse_draw_action = view_menu.addAction('Mouse Draw')
        mouse_draw_action.setCheckable(True)
        mouse_draw_action.toggled.connect(self._mouse_draw_toggle.setChecked)
        self._mouse_draw_toggle.toggled.connect(mouse_draw_action.setChecked)

        single_height_action = view_menu.addAction('Single Height')
        single_height_action.setCheckable(True)
        single_height_action.toggled.connect(self._single_height_toggle.setChecked)
        self._single_height_toggle.toggled.connect(single_height_action.setChecked)

        single_width_action = view_menu.addAction('Single Width')
        single_width_action.setCheckable(True)
        single_width_action.toggled.connect(self._single_width_toggle.setChecked)
        self._single_width_toggle.toggled.connect(single_width_action.setChecked)

        no_flash_action = view_menu.addAction('No Flash')
        no_flash_action.setCheckable(True)
        no_flash_action.toggled.connect(self._no_flash_toggle.setChecked)
        self._no_flash_toggle.toggled.connect(no_flash_action.setChecked)

        widescreen_action = view_menu.addAction('Widescreen Display')
        widescreen_action.setCheckable(True)
        widescreen_action.toggled.connect(self._widescreen_toggle.setChecked)
        self._widescreen_toggle.toggled.connect(widescreen_action.setChecked)

        language_menu = view_menu.addMenu('Language')
        self._language_actions = {}
        self._language_action_group = QtWidgets.QActionGroup(self)
        self._language_action_group.setExclusive(True)
        for index, (key, label) in enumerate(self._language_options):
            action = language_menu.addAction(label)
            action.setCheckable(True)
            action.toggled.connect(lambda checked=False, item=index: self._set_language_from_action(item, checked))
            self._language_action_group.addAction(action)
            self._language_actions[key] = action
        self._language_actions['default'].setChecked(True)

        zoom_menu = view_menu.addMenu('Zoom')
        for zoom in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
            action = zoom_menu.addAction(f'{zoom:.1f}x')
            action.triggered.connect(lambda checked=False, value=zoom: self._zoom_box.setValue(value))

    def _make_group_collapsible(self, group, expanded=True):
        group.setCheckable(True)
        group.setChecked(bool(expanded))
        title_height = max(group.fontMetrics().height() + 18, 24)

        def apply_state(checked):
            group.setMaximumHeight(16777215 if checked else title_height)
            group.setMinimumHeight(title_height if checked else title_height)
            group.updateGeometry()

        group.toggled.connect(apply_state)
        apply_state(bool(expanded))

    def _tree_checkboxes_enabled(self):
        return bool(getattr(self, '_tree_checkboxes_toggle', None) and self._tree_checkboxes_toggle.isChecked())

    def _hidden_subpages_mode(self):
        combo = getattr(self, '_hidden_subpages_mode_combo', None)
        if combo is None:
            return 'legacy'
        return str(combo.currentData() or 'legacy')

    def _hidden_subpages_mode_changed(self, _index):
        self._apply_hidden_subpages_mode_to_navigator()
        self._rebuild_tree()

    def _apply_hidden_subpages_mode_to_navigator(self):
        if self._navigator is not None:
            self._navigator.set_hidden_subpages_mode(self._hidden_subpages_mode())

    def _control_button_style(self, color=None):
        accent = color or '#4d4d4d'
        border = '#7a7a7a' if color is None else accent
        highlight_border = '#f2c94c'
        if color is None:
            text = '#f3f3f3'
            highlight_border = '#f2c94c'
        else:
            normalized = str(color).strip().lower()
            if normalized.startswith('#'):
                hex_value = normalized[1:]
                if len(hex_value) == 3:
                    hex_value = ''.join(ch * 2 for ch in hex_value)
                try:
                    red = int(hex_value[0:2], 16)
                    green = int(hex_value[2:4], 16)
                    blue = int(hex_value[4:6], 16)
                    luminance = (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
                    text = '#f8f8f8' if luminance < 128 else '#111111'
                    highlight_border = '#f8f8f8' if luminance < 128 else '#111111'
                except (TypeError, ValueError):
                    text = '#111111'
                    highlight_border = '#111111'
            elif normalized in {'black'}:
                text = '#f8f8f8'
                highlight_border = '#f8f8f8'
            elif normalized in {'white'}:
                text = '#111111'
                highlight_border = '#111111'
            else:
                text = '#111111'
                highlight_border = '#111111'
        return (
            'QToolButton {'
            f'background: {accent};'
            f'border: 1px solid {border};'
            'border-radius: 4px;'
            'padding: 4px 6px;'
            f'color: {text};'
            'font-weight: 600;'
            '}'
            'QToolButton:hover {'
            'border-width: 2px;'
            'padding: 3px 5px;'
            '}'
            'QToolButton:pressed {'
            'background: #e6e6e6;'
            'color: #111111;'
            '}'
            'QToolButton:checked {'
            f'border: 3px solid {highlight_border};'
            'padding: 2px 4px;'
            'font-weight: 800;'
            '}'
        )

    def _make_control_code_button(self, label, code):
        button = QtWidgets.QToolButton()
        hint = CONTROL_CODE_HINTS.get(int(code), '')
        color = CONTROL_CODE_BUTTON_COLORS.get(int(code))
        safe_label = str(label).replace('&', '&&')
        safe_hint = str(hint).replace('&', '&&')
        button.setText(f'{safe_label}\n{safe_hint}' if safe_hint else safe_label)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        button.setAutoRaise(False)
        button.setCheckable(True)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        button.setMinimumHeight(42)
        button.setStyleSheet(self._control_button_style(color))
        button.setToolTip(f'Apply {label} ({hint})')
        button.clicked.connect(lambda _checked=False, value=int(code): self._apply_preview_byte(value, control_code=True))
        self._control_code_buttons[int(code)] = button
        return button

    def _make_control_action_button(self, label, hint, handler_name):
        button = QtWidgets.QToolButton()
        button.setText(f'{label}\n{hint}' if hint else label)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        button.setAutoRaise(False)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        button.setMinimumHeight(42)
        button.setStyleSheet(self._control_button_style())
        button.setToolTip(f'{label} ({hint})')
        button.clicked.connect(getattr(self, handler_name))
        return button

    def _build_control_keys_panel(self, parent_layout):
        for section_title, actions in CONTROL_CODE_MENU:
            section_group = QtWidgets.QGroupBox(section_title)
            section_layout = QtWidgets.QGridLayout(section_group)
            section_layout.setContentsMargins(6, 6, 6, 6)
            section_layout.setHorizontalSpacing(6)
            section_layout.setVerticalSpacing(6)
            for index, (label, code) in enumerate(actions):
                section_layout.addWidget(self._make_control_code_button(label, code), index // 4, index % 4)
            parent_layout.addWidget(section_group)
            self._control_keys_sections.append(section_group)

        quick_group = QtWidgets.QGroupBox('Quick Actions')
        quick_layout = QtWidgets.QGridLayout(quick_group)
        quick_layout.setContentsMargins(6, 6, 6, 6)
        quick_layout.setHorizontalSpacing(6)
        quick_layout.setVerticalSpacing(6)
        for index, (label, hint, handler_name) in enumerate(EDITOR_QUICK_ACTIONS):
            quick_layout.addWidget(self._make_control_action_button(label, hint, handler_name), index // 4, index % 4)
        parent_layout.addWidget(quick_group)
        self._control_keys_sections.append(quick_group)

    def toggle_show_grid(self):
        self._show_grid_toggle.toggle()

    def toggle_show_control_codes(self):
        self._show_control_codes_toggle.toggle()

    def show_control_keys_help(self):
        if hasattr(self, '_control_keys_group'):
            self._control_keys_group.setChecked(True)
            self._control_keys_group.raise_()
        self.statusBar().showMessage('Control Keys panel opened.', 1500)

    def _sync_control_keys_selection(self):
        active_code = None
        if self._preview_cursor_row is not None and self._preview_cursor_col is not None:
            value = self._preview_byte_at(self._preview_cursor_row, self._preview_cursor_col)
            if value is not None and 0 <= int(value) < 0x20:
                active_code = int(value)
        for code, button in getattr(self, '_control_code_buttons', {}).items():
            blocked = button.blockSignals(True)
            try:
                button.setChecked(int(code) == int(active_code) if active_code is not None else False)
            finally:
                button.blockSignals(blocked)
        if hasattr(self, '_control_keys_hint_label'):
            if active_code is None:
                self._control_keys_hint_label.setText('Hover buttons for hints. Click to apply a control code or action.')
            else:
                label = CONTROL_CODE_LABELS.get(int(active_code), f'Control {int(active_code):02X}')
                hint = CONTROL_CODE_HINTS.get(int(active_code), '')
                self._control_keys_hint_label.setText(
                    f'Current control code: {label}'
                    + (f' ({hint})' if hint else '')
                )

    def _sync_character_set_from_page_region(self, *_args):
        if not hasattr(self, '_character_set_combo'):
            return
        codepage = int(self._page_region_spin.value())
        index = self._character_set_combo.findData(codepage)
        if index < 0 or index == self._character_set_combo.currentIndex():
            return
        was_blocked = self._character_set_combo.blockSignals(True)
        try:
            self._character_set_combo.setCurrentIndex(index)
        finally:
            self._character_set_combo.blockSignals(was_blocked)

    def _character_set_changed(self, index):
        if index < 0 or not hasattr(self, '_page_region_spin'):
            return
        codepage = int(self._character_set_combo.itemData(index) or 0)
        if int(self._page_region_spin.value()) != codepage:
            self._page_region_spin.setValue(codepage)

    def _clear_fastext_link(self, link):
        link.magazine = 8
        link.page = 0xFF
        link.subpage = 0x3F7F

    def _populate_default_fastext(self, subpage, magazine):
        if not subpage.has_packet(27, 0):
            subpage.init_packet(27, 0, magazine)
        fastext = subpage.fastext
        fastext.dc = 0
        fastext.control = 0
        for link in fastext.links:
            self._clear_fastext_link(link)
        return fastext

    def _clear_fastext_fields(self):
        for page_input, subpage_input in getattr(self, '_fastext_inputs', ()):
            page_input.clear()
            subpage_input.clear()

    def _load_fastext_from_subpage(self, subpage):
        self._clear_fastext_fields()
        if subpage is None or not subpage.has_packet(27, 0):
            return
        for (page_input, subpage_input), link in zip(self._fastext_inputs, subpage.fastext.links[:4]):
            page_input.setText(f'{int(link.magazine)}{int(link.page):02X}')
            subpage_input.setText(f'{int(link.subpage):04X}')

    def _apply_fastext_to_subpage(self, editable, page_number, strict=False):
        if editable is None:
            return
        has_input = any(page_input.text().strip() or subpage_input.text().strip() for page_input, subpage_input in self._fastext_inputs)
        if not editable.has_packet(27, 0) and not has_input:
            return
        fastext = editable.fastext if editable.has_packet(27, 0) else self._populate_default_fastext(editable, page_number >> 8)
        for (page_input, subpage_input), link in zip(self._fastext_inputs, fastext.links[:4]):
            page_text = self._sanitize_editor_text(page_input.text().upper(), 3)
            subpage_text = self._sanitize_editor_text(subpage_input.text().upper(), 4)
            if not page_text:
                self._clear_fastext_link(link)
                continue
            try:
                target_page_number = parse_page_identifier(page_text)
            except ValueError:
                if strict:
                    raise ValueError(f'Invalid Fastext page "{page_text}".')
                self._clear_fastext_link(link)
                continue
            link.magazine = target_page_number >> 8
            link.page = target_page_number & 0xFF
            if not subpage_text:
                link.subpage = 0x0000
                continue
            try:
                link.subpage = parse_subpage_identifier(subpage_text)
            except ValueError:
                if strict:
                    raise ValueError(f'Invalid Fastext subpage "{subpage_text}" for page {page_text}.')
                self._clear_fastext_link(link)

    def reset_fastext(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return
        subpage = self._resolve_subpage_variant(
            self._current_page_number,
            self._current_subpage_number,
            self._current_subpage_occurrence,
        )
        self._editor_loading = True
        try:
            self._load_fastext_from_subpage(subpage)
        finally:
            self._editor_loading = False
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()
        self.statusBar().showMessage('Fastext reset to the current subpage.', 2500)

    def _find_service_830_entry(self, entries=None):
        search_entries = self._entries if entries is None else tuple(entries)
        for entry in search_entries:
            if int(getattr(entry, 'magazine', 0) or 0) != 8 or int(getattr(entry, 'row', -1) or -1) != 30:
                continue
            try:
                packet = Packet(entry.raw, number=int(getattr(entry, 'packet_index', -1)))
            except Exception:
                continue
            if packet.type == 'broadcast':
                return entry, packet
        return None, None

    def _clear_service_830_fields(self):
        self._service_830_source_raw = None
        self._service_830_enabled_toggle.setChecked(False)
        self._service_830_dc_combo.setCurrentIndex(0)
        self._service_830_page_input.clear()
        self._service_830_label_input.clear()
        self._service_830_network_input.clear()
        self._service_830_country_offset_input.clear()
        self._service_830_date_input.clear()
        self._service_830_time_input.clear()
        self._service_830_summary_label.setText('No 8/30 packet in the current file.')

    def _update_service_830_summary(self, packet=None):
        if not getattr(self, '_service_830_enabled_toggle', None):
            return
        if not self._service_830_enabled_toggle.isChecked():
            self._service_830_summary_label.setText('8/30 disabled. Uncheck to remove it from the saved file.')
            return
        if packet is not None:
            try:
                label = _broadcast_label_from_packet(packet) or '(blank)'
                initial_page = f'P{int(packet.broadcast.initial_page.magazine)}{int(packet.broadcast.initial_page.page):02X}'
                designation = int(packet.broadcast.dc)
                if designation in (0, 1):
                    format1 = packet.broadcast.format1
                    summary = (
                        f'DC={designation} | Initial={initial_page} | Network={int(format1.network):04X} '
                        f'| Date={format1.date.isoformat()} | Time={int(format1.hour):02d}:{int(format1.minute):02d}:{int(format1.second):02d} '
                        f'| UTC{float(format1.offset):+g} | Label="{label}"'
                    )
                else:
                    format2 = packet.broadcast.format2
                    summary = (
                        f'DC={designation} | Initial={initial_page} | Network={int(format2.network):02X} '
                        f'| Country={int(format2.country):02X} | Date={int(format2.day):02d}/{int(format2.month):02d} '
                        f'| Time={int(format2.hour):02d}:{int(format2.minute):02d} | Label="{label}"'
                    )
                self._service_830_summary_label.setText(summary)
                return
            except Exception:
                pass
        page_text = self._sanitize_editor_text(self._service_830_page_input.text().upper(), 3) or '100'
        label_text = _clean_broadcast_label_text(self._service_830_label_input.text()).rstrip() or '(blank)'
        designation = int(self._service_830_dc_combo.currentData() or 0)
        if designation in (0, 1):
            network_text = self._sanitize_editor_text(self._service_830_network_input.text().upper(), 4) or '0000'
            date_text = str(self._service_830_date_input.text() or '2000-01-01')
            time_text = str(self._service_830_time_input.text() or '00:00:00')
            offset_text = str(self._service_830_country_offset_input.text() or '0')
            summary = (
                f'DC={designation} | Initial=P{page_text} | Network={network_text} '
                f'| Date={date_text} | Time={time_text} | UTC{offset_text} | Label="{label_text}"'
            )
        else:
            network_text = self._sanitize_editor_text(self._service_830_network_input.text().upper(), 2) or '00'
            country_text = self._sanitize_editor_text(self._service_830_country_offset_input.text().upper(), 2) or '00'
            date_text = str(self._service_830_date_input.text() or '01/01')
            time_text = str(self._service_830_time_input.text() or '00:00')
            summary = (
                f'DC={designation} | Initial=P{page_text} | Network={network_text} '
                f'| Country={country_text} | Date={date_text} | Time={time_text} | Label="{label_text}"'
            )
        self._service_830_summary_label.setText(summary)

    def _load_service_830_from_entries(self, entries=None):
        self._editor_loading = True
        try:
            entry, packet = self._find_service_830_entry(entries)
            if packet is None:
                self._clear_service_830_fields()
                return
            self._service_830_source_raw = bytes(packet.to_bytes())
            self._service_830_enabled_toggle.setChecked(True)
            index = self._service_830_dc_combo.findData(int(packet.broadcast.dc))
            self._service_830_dc_combo.setCurrentIndex(index if index >= 0 else 0)
            self._service_830_page_input.setText(
                f'{int(packet.broadcast.initial_page.magazine)}{int(packet.broadcast.initial_page.page):02X}'
            )
            self._service_830_label_input.setText(_broadcast_label_from_packet(packet))
            designation = int(packet.broadcast.dc)
            if designation in (0, 1):
                format1 = packet.broadcast.format1
                self._service_830_network_input.setText(f'{int(format1.network):04X}')
                self._service_830_country_offset_input.setText(f'{float(format1.offset):+g}')
                self._service_830_date_input.setText(format1.date.isoformat())
                self._service_830_time_input.setText(
                    f'{int(format1.hour):02d}:{int(format1.minute):02d}:{int(format1.second):02d}'
                )
            else:
                format2 = packet.broadcast.format2
                self._service_830_network_input.setText(f'{int(format2.network):02X}')
                self._service_830_country_offset_input.setText(f'{int(format2.country):02X}')
                self._service_830_date_input.setText(f'{int(format2.day):02d}/{int(format2.month):02d}')
                self._service_830_time_input.setText(f'{int(format2.hour):02d}:{int(format2.minute):02d}')
            self._update_service_830_summary(packet)
        finally:
            self._editor_loading = False

    def _capture_service_830_snapshot(self):
        return {
            'enabled': bool(self._service_830_enabled_toggle.isChecked()),
            'dc': int(self._service_830_dc_combo.currentData() or 0),
            'page': str(self._service_830_page_input.text() or ''),
            'label': str(self._service_830_label_input.text() or ''),
            'network': str(self._service_830_network_input.text() or ''),
            'country_offset': str(self._service_830_country_offset_input.text() or ''),
            'date': str(self._service_830_date_input.text() or ''),
            'time': str(self._service_830_time_input.text() or ''),
        }

    def _apply_service_830_snapshot(self, snapshot):
        snapshot = snapshot or {}
        self._service_830_enabled_toggle.setChecked(bool(snapshot.get('enabled', False)))
        index = self._service_830_dc_combo.findData(int(snapshot.get('dc', 0)))
        self._service_830_dc_combo.setCurrentIndex(index if index >= 0 else 0)
        self._service_830_page_input.setText(str(snapshot.get('page', '')))
        self._service_830_label_input.setText(str(snapshot.get('label', '')))
        self._service_830_network_input.setText(str(snapshot.get('network', '')))
        self._service_830_country_offset_input.setText(str(snapshot.get('country_offset', '')))
        self._service_830_date_input.setText(str(snapshot.get('date', '')))
        self._service_830_time_input.setText(str(snapshot.get('time', '')))
        self._update_service_830_summary()

    def _build_service_830_packet(self, strict=False):
        if not self._service_830_enabled_toggle.isChecked():
            return None
        page_text = self._sanitize_editor_text(self._service_830_page_input.text().upper(), 3)
        if not page_text:
            if strict:
                raise ValueError('8/30 initial page must be three hexadecimal digits, for example 100.')
            target_page_number = int(self._current_page_number or 0x100)
        else:
            try:
                target_page_number = parse_page_identifier(page_text)
            except ValueError as exc:
                if strict:
                    raise ValueError(str(exc)) from exc
                target_page_number = int(self._current_page_number or 0x100)
        source_raw = getattr(self, '_service_830_source_raw', None)
        if source_raw:
            packet = Packet(np.frombuffer(source_raw, dtype=np.uint8).copy())
        else:
            packet = Packet(np.zeros((42,), dtype=np.uint8))
        packet.mrag.magazine = 8
        packet.mrag.row = 30
        broadcast = packet.broadcast
        broadcast.dc = int(self._service_830_dc_combo.currentData() or 0)
        broadcast.initial_page.magazine = target_page_number >> 8
        broadcast.initial_page.page = target_page_number & 0xFF
        try:
            broadcast.initial_page.subpage = int(broadcast.initial_page.subpage)
        except Exception:
            broadcast.initial_page.subpage = 0x3F7F
        label_text = _clean_broadcast_label_text(self._service_830_label_input.text())
        broadcast.displayable.place_string((' ' * 20).encode('ascii'))
        broadcast.displayable.place_string(label_text.ljust(20).encode('ascii'))
        designation = int(self._service_830_dc_combo.currentData() or 0)
        if designation in (0, 1):
            network = _parse_service_830_network(self._service_830_network_input.text(), format2=False)
            date_value = _parse_service_830_date_format1(self._service_830_date_input.text())
            hour, minute, second = _parse_service_830_time(self._service_830_time_input.text(), include_seconds=True)
            offset = _parse_service_830_offset(self._service_830_country_offset_input.text())
            _encode_service_830_format1(
                packet,
                network=network,
                date_value=date_value,
                hour=hour,
                minute=minute,
                second=second,
                offset=offset,
            )
        else:
            network = _parse_service_830_network(self._service_830_network_input.text(), format2=True)
            country = _parse_service_830_country(self._service_830_country_offset_input.text())
            day, month = _parse_service_830_date_format2(self._service_830_date_input.text())
            hour, minute = _parse_service_830_time(self._service_830_time_input.text(), include_seconds=False)
            _encode_service_830_format2(
                packet,
                network=network,
                country=country,
                day=day,
                month=month,
                hour=hour,
                minute=minute,
            )
        return packet

    def _apply_service_830_to_entries(self, entries, strict=False):
        entries = tuple(entries)
        packet = self._build_service_830_packet(strict=strict)
        target_index = None
        filtered_raw_packets = []
        for index, entry in enumerate(entries):
            is_service_830 = False
            if int(getattr(entry, 'magazine', 0) or 0) == 8 and int(getattr(entry, 'row', -1) or -1) == 30:
                try:
                    is_service_830 = Packet(entry.raw).type == 'broadcast'
                except Exception:
                    is_service_830 = False
            if is_service_830:
                if target_index is None:
                    target_index = len(filtered_raw_packets)
                continue
            filtered_raw_packets.append(entry.raw)
        if packet is None:
            return build_t42_entries(filtered_raw_packets)
        if target_index is None:
            target_index = 0
        updated_raw_packets = (
            filtered_raw_packets[:target_index]
            + [packet.to_bytes()]
            + filtered_raw_packets[target_index:]
        )
        return build_t42_entries(updated_raw_packets)

    def reset_service_830(self):
        self._load_service_830_from_entries(self._entries)
        snapshot = self._capture_editor_snapshot()
        dirty = self._snapshot_signature(snapshot) != self._editor_initial_signature
        self._update_editor_dirty_state(dirty)
        self._record_editor_snapshot()
        self.statusBar().showMessage('Service 8/30 reset to the current file.', 2500)

    def _service_830_changed(self, *_args):
        if self._editor_loading:
            return
        self._update_service_830_summary()
        self._update_editor_dirty_state(True)
        self._record_editor_snapshot()

    def _load_font_family(self):
        font_path = os.path.join(os.path.dirname(__file__), 'teletext2.ttf')
        if os.path.exists(font_path):
            font_id = QtGui.QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    return families[0]
        return 'teletext2'

    def _blank_subpage_entries(self, page_number=0x100, subpage_number=0x0000):
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

    def _document_display_name(self):
        return os.path.basename(self._filename) if self._filename else 'Untitled'

    def _set_document_caption(self):
        name = self._document_display_name()
        self._path_label.setText(self._filename if self._filename else 'Untitled TeleText (unsaved)')
        self._path_label.setToolTip(self._filename if self._filename else 'Unsaved blank T42 document')
        self.setWindowTitle(f'{EDITOR_APP_NAME} - {name}')

    def _has_subpage(self, page_number, subpage_number):
        if self._navigator is None:
            return False
        try:
            page = self._navigator._page(int(page_number))  # noqa: SLF001
        except Exception:
            return False
        return int(subpage_number) in page.subpages

    def _has_page(self, page_number):
        if self._navigator is None:
            return False
        try:
            self._navigator._page(int(page_number))  # noqa: SLF001
        except Exception:
            return False
        return True

    def _prompt_page_identifier(self, title, label, default_text):
        text, accepted = QtWidgets.QInputDialog.getText(self, title, label, text=str(default_text or ''))
        if not accepted:
            return None
        try:
            return parse_page_identifier(text)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return None

    def _prompt_subpage_identifier(self, title, label, default_text):
        text, accepted = QtWidgets.QInputDialog.getText(self, title, label, text=str(default_text or ''))
        if not accepted:
            return None
        try:
            return parse_subpage_identifier(text)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return None

    def new_empty_document(self):
        if self._loader is not None and self._loader.isRunning():
            return
        self._progress.setVisible(False)
        self._filename = ''
        self._modified_pages.clear()
        self._modified_subpages.clear()
        self._enabled_subpage_occurrences.clear()
        self._editor_drafts.clear()
        self._document_history = []
        self._document_history_index = -1
        self._document_initial_snapshot = None
        self._thumbnail_cache.clear()
        self._thumbnail_queue = deque()
        self._thumbnail_total = 0
        self._thumbnail_timer.stop()
        self._tree.clear()
        self._tree_status_label.hide()
        self._set_document_caption()
        self._rebuild_from_entries(
            self._blank_subpage_entries(0x100, 0x0000),
            focus_page_number=0x100,
            focus_subpage_number=0x0000,
            preserve_enabled_occurrences=False,
        )
        self._seed_document_history()
        self._set_loaded_state(True)
        self.statusBar().showMessage('Created new blank T42 document.', 4000)

    def add_blank_page(self):
        if self._navigator is None:
            self.new_empty_document()
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        default_page = self._page_label(self._current_page_number)[1:] if self._current_page_number is not None else '100'
        page_number = self._prompt_page_identifier('Add Blank Page', 'Page (hex):', default_page)
        if page_number is None:
            return
        subpage_number = 0x0000
        if self._has_subpage(page_number, subpage_number):
            self._rebuild_tree(selection_key=('subpage', int(page_number), int(subpage_number), 1))
            self.statusBar().showMessage(
                f'{self._page_label(page_number)} / {subpage_number:04X} already exists.',
                3000,
            )
            return
        updated_entries = replace_subpage_in_entries(
            self._entries,
            self._blank_subpage_entries(page_number, subpage_number),
            target_page_number=page_number,
            target_subpage_number=subpage_number,
        )
        self._mark_modified_subpage(page_number, subpage_number)
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=page_number,
            focus_subpage_number=subpage_number,
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Added blank page {self._page_label(page_number)} / {subpage_number:04X}.',
            4000,
        )

    def add_blank_subpage(self):
        if self._navigator is None:
            self.new_empty_document()
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        page_number = self._current_page_number if self._current_page_number is not None else 0x100
        default_subpage = 0x0000
        if self._current_subpage_number is not None:
            default_subpage = min(int(self._current_subpage_number) + 1, 0x3F7F)
        subpage_number = self._prompt_subpage_identifier(
            'Add Blank Subpage',
            f'Subpage for {self._page_label(page_number)} (hex):',
            f'{default_subpage:04X}',
        )
        if subpage_number is None:
            return
        if self._has_subpage(page_number, subpage_number):
            self._rebuild_tree(selection_key=('subpage', int(page_number), int(subpage_number), 1))
            self.statusBar().showMessage(
                f'{self._page_label(page_number)} / {subpage_number:04X} already exists.',
                3000,
            )
            return
        updated_entries = replace_subpage_in_entries(
            self._entries,
            self._blank_subpage_entries(page_number, subpage_number),
            target_page_number=page_number,
            target_subpage_number=subpage_number,
        )
        self._mark_modified_subpage(page_number, subpage_number)
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=page_number,
            focus_subpage_number=subpage_number,
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Added blank subpage {self._page_label(page_number)} / {subpage_number:04X}.',
            4000,
        )

    def _insert_hidden_subpage_occurrence(self, entries, page_number, subpage_number, new_entries):
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

    def add_hidden_subpage(self):
        if self._navigator is None:
            self.new_empty_document()
            return
        if self._editor_dirty:
            self.apply_current_edits()
        page_number = self._current_page_number if self._current_page_number is not None else 0x100
        subpage_number = self._current_subpage_number if self._current_subpage_number is not None else 0x0000
        page_number = self._prompt_page_identifier(
            'Add Hidden Subpage',
            'Page (hex):',
            self._page_label(page_number)[1:],
        )
        if page_number is None:
            return
        subpage_number = self._prompt_subpage_identifier(
            'Add Hidden Subpage',
            f'Subpage for {self._page_label(page_number)} (hex):',
            f'{int(subpage_number):04X}',
        )
        if subpage_number is None:
            return
        updated_entries = self._insert_hidden_subpage_occurrence(
            self._entries,
            page_number,
            subpage_number,
            self._blank_subpage_entries(page_number, subpage_number),
        )
        occurrence_number = len(self._page_subpage_occurrences_for_entries(updated_entries, page_number).get(subpage_number, ()))
        self._mark_modified_subpage(page_number, subpage_number)
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=page_number,
            focus_subpage_number=subpage_number,
            selection_key=('subpage', int(page_number), int(subpage_number), int(occurrence_number)),
            record_history=True,
        )
        self._enabled_subpage_occurrences.add((int(page_number), int(subpage_number), int(occurrence_number)))
        self._record_document_snapshot(selection_key=('subpage', int(page_number), int(subpage_number), int(occurrence_number)))
        self._rebuild_tree(selection_key=('subpage', int(page_number), int(subpage_number), int(occurrence_number)))
        self.statusBar().showMessage(
            f'Added hidden subpage {self._page_label(page_number)} / {subpage_number:04X} ({occurrence_number}).',
            4000,
        )

    def duplicate_current_hidden_subpage(self):
        if self._navigator is None or self._current_page_number is None or self._current_subpage_number is None:
            return
        source_occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        if source_occurrence_number <= 1:
            return
        if self._editor_dirty:
            self.apply_current_edits()
        page_number = int(self._current_page_number)
        subpage_number = int(self._current_subpage_number)
        source_entries = collect_subpage_occurrence_entries(
            self._entries,
            page_number,
            subpage_number,
            source_occurrence_number,
        )
        if not source_entries:
            return
        updated_entries = self._insert_hidden_subpage_occurrence(
            self._entries,
            page_number,
            subpage_number,
            source_entries,
        )
        occurrence_number = len(
            tuple(
                self._page_subpage_occurrences_for_entries(updated_entries, page_number).get(subpage_number, ())
            )
        )
        self._mark_modified_subpage(page_number, subpage_number)
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=page_number,
            focus_subpage_number=subpage_number,
            selection_key=('subpage', page_number, subpage_number, int(occurrence_number)),
            record_history=True,
        )
        self._enabled_subpage_occurrences.add((page_number, subpage_number, int(occurrence_number)))
        self._record_document_snapshot(selection_key=('subpage', page_number, subpage_number, int(occurrence_number)))
        self._rebuild_tree(selection_key=('subpage', page_number, subpage_number, int(occurrence_number)))
        self.statusBar().showMessage(
            f'Duplicated hidden subpage {self._page_label(page_number)} / {subpage_number:04X} ({occurrence_number}).',
            4000,
        )

    def _confirm_overwrite_target(self, title, message):
        return (
            QtWidgets.QMessageBox.question(
                self,
                title,
                message,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            ) == QtWidgets.QMessageBox.Yes
        )

    def duplicate_current_page(self):
        if self._navigator is None or self._current_page_number is None:
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        source_page_number = int(self._current_page_number)
        default_target = f'{min(source_page_number + 1, 0x8FF):03X}'
        target_page_number = self._prompt_page_identifier('Duplicate Current Page', 'Target page (hex):', default_target)
        if target_page_number is None:
            return
        if target_page_number == source_page_number:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, 'Target page must be different from the current page.')
            return
        if self._has_page(target_page_number) and not self._confirm_overwrite_target(
            'Replace page?',
            f'{self._page_label(target_page_number)} already exists. Replace it with a copy of {self._page_label(source_page_number)}?',
        ):
            return
        page_entries = collect_page_entries(self._entries, source_page_number)
        if not page_entries:
            return
        self._mark_modified_page(target_page_number)
        updated_entries = replace_page_in_entries(
            self._entries,
            page_entries,
            target_page_number=target_page_number,
        )
        self._rebuild_from_entries(updated_entries, focus_page_number=target_page_number, record_history=True)
        self.statusBar().showMessage(
            f'Duplicated {self._page_label(source_page_number)} to {self._page_label(target_page_number)}.',
            4000,
        )

    def duplicate_current_subpage(self):
        if self._navigator is None or self._current_page_number is None or self._current_subpage_number is None:
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        source_page_number = int(self._current_page_number)
        source_subpage_number = int(self._current_subpage_number)
        editable = self._current_subpage_copy()
        if editable is None:
            return
        default_target_page = f'{source_page_number:03X}'
        default_target_subpage = f'{min(source_subpage_number + 1, 0x3F7F):04X}'
        target_page_number = self._prompt_page_identifier(
            'Duplicate Current Subpage',
            'Target page (hex):',
            default_target_page,
        )
        if target_page_number is None:
            return
        target_subpage_number = self._prompt_subpage_identifier(
            'Duplicate Current Subpage',
            f'Target subpage for {self._page_label(target_page_number)} (hex):',
            default_target_subpage,
        )
        if target_subpage_number is None:
            return
        if target_page_number == source_page_number and target_subpage_number == source_subpage_number:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, 'Target page/subpage must be different from the current one.')
            return
        if self._has_subpage(target_page_number, target_subpage_number) and not self._confirm_overwrite_target(
            'Replace subpage?',
            f'{self._page_label(target_page_number)} / {target_subpage_number:04X} already exists. Replace it?',
        ):
            return
        duplicate_entries = build_t42_entries(packet.to_bytes() for packet in editable.packets)
        self._mark_modified_subpage(target_page_number, target_subpage_number)
        updated_entries = replace_subpage_in_entries(
            self._entries,
            duplicate_entries,
            target_page_number=target_page_number,
            target_subpage_number=target_subpage_number,
        )
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=target_page_number,
            focus_subpage_number=target_subpage_number,
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Duplicated {self._page_label(source_page_number)} / {source_subpage_number:04X} to {self._page_label(target_page_number)} / {target_subpage_number:04X}.',
            4000,
        )

    def delete_current_page(self):
        if self._navigator is None or self._current_page_number is None:
            return
        page_number = int(self._current_page_number)
        if QtWidgets.QMessageBox.question(
            self,
            'Delete page?',
            f'Delete {self._page_label(page_number)} and all its subpages?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        ) != QtWidgets.QMessageBox.Yes:
            return
        self._editor_drafts = {
            key: value
            for key, value in self._editor_drafts.items()
            if int(key[0]) != page_number
        }
        updated_entries = tuple(entry for entry in self._entries if int(entry.page_number or -1) != page_number)
        self._modified_pages.discard(page_number)
        self._modified_subpages = {key for key in self._modified_subpages if int(key[0]) != page_number}
        if not updated_entries:
            self._modified_pages.clear()
            self._modified_subpages.clear()
            self._rebuild_from_entries(
                (),
                record_history=True,
                preserve_enabled_occurrences=False,
            )
            self.statusBar().showMessage(f'Deleted {self._page_label(page_number)}.', 4000)
            return
        self._rebuild_from_entries(updated_entries, record_history=True)
        self.statusBar().showMessage(f'Deleted {self._page_label(page_number)}.', 4000)

    def delete_current_subpage(self):
        if self._navigator is None or self._current_page_number is None or self._current_subpage_number is None:
            return
        page_number = int(self._current_page_number)
        subpage_number = int(self._current_subpage_number)
        occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        if QtWidgets.QMessageBox.question(
            self,
            'Delete subpage?',
            (
                f'Delete {self._page_label(page_number)} / {subpage_number:04X} ({occurrence_number})?'
                if occurrence_number > 1
                else f'Delete {self._page_label(page_number)} / {subpage_number:04X}?'
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        ) != QtWidgets.QMessageBox.Yes:
            return
        self._editor_drafts = {
            key: value
            for key, value in self._editor_drafts.items()
            if not (int(key[0]) == page_number and int(key[1]) == subpage_number and int(key[2]) == occurrence_number)
        }
        removed_entries = collect_subpage_occurrence_entries(
            self._entries,
            page_number,
            subpage_number,
            occurrence_number,
        )
        removed_packets = {int(entry.packet_index) for entry in removed_entries}
        updated_entries = tuple(
            entry for entry in self._entries
            if int(entry.packet_index) not in removed_packets
        )
        self._modified_subpages.discard((page_number, subpage_number))
        if not updated_entries:
            self._modified_pages.clear()
            self._modified_subpages.clear()
            self._rebuild_from_entries(
                (),
                record_history=True,
                preserve_enabled_occurrences=False,
            )
            self.statusBar().showMessage(
                f'Deleted {self._page_label(page_number)} / {subpage_number:04X}.',
                4000,
            )
            return
        self._rebuild_from_entries(updated_entries, focus_page_number=page_number, record_history=True)
        self.statusBar().showMessage(
            (
                f'Deleted {self._page_label(page_number)} / {subpage_number:04X} ({occurrence_number}).'
                if occurrence_number > 1
                else f'Deleted {self._page_label(page_number)} / {subpage_number:04X}.'
            ),
            4000,
        )

    def _delete_selected_tree_entries(self):
        contexts = self._selected_tree_contexts()
        if not contexts or self._navigator is None:
            return
        selected_pages = sorted({
            int(context['page_number'])
            for context in contexts
            if context['type'] == 'page'
        })
        selected_subpages = [
            context for context in contexts
            if context['type'] == 'subpage' and int(context['page_number']) not in selected_pages
        ]
        if not selected_pages and not selected_subpages:
            return
        summary_parts = []
        if selected_pages:
            summary_parts.append(f'{len(selected_pages)} page(s)')
        if selected_subpages:
            summary_parts.append(f'{len(selected_subpages)} subpage(s)')
        if QtWidgets.QMessageBox.question(
            self,
            'Delete selected entries?',
            'Delete ' + ' and '.join(summary_parts) + '?',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        ) != QtWidgets.QMessageBox.Yes:
            return
        removed_packets = set()
        for page_number in selected_pages:
            for entry in collect_page_entries(self._entries, page_number):
                removed_packets.add(int(entry.packet_index))
            self._modified_pages.discard(page_number)
            self._modified_subpages = {
                key for key in self._modified_subpages
                if int(key[0]) != page_number
            }
        for context in selected_subpages:
            removed_entries = collect_subpage_occurrence_entries(
                self._entries,
                context['page_number'],
                context['subpage_number'],
                context.get('occurrence_number', 1),
            )
            for entry in removed_entries:
                removed_packets.add(int(entry.packet_index))
            self._modified_subpages.discard((int(context['page_number']), int(context['subpage_number'])))
        updated_entries = tuple(
            entry for entry in self._entries
            if int(entry.packet_index) not in removed_packets
        )
        if not updated_entries:
            self._modified_pages.clear()
            self._modified_subpages.clear()
            self._rebuild_from_entries(
                (),
                record_history=True,
                preserve_enabled_occurrences=False,
            )
            self.statusBar().showMessage('Deleted selected entries.', 4000)
            return
        focus_page = None
        if selected_pages:
            remaining_pages = sorted({int(entry.page_number) for entry in updated_entries if entry.page_number is not None})
            if remaining_pages:
                focus_page = remaining_pages[0]
        elif selected_subpages:
            focus_page = int(selected_subpages[0]['page_number'])
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=focus_page,
            record_history=True,
        )
        self.statusBar().showMessage('Deleted selected entries.', 4000)

    def _tree_context(self):
        item = self._tree.currentItem()
        if item is None:
            return None
        item_type = item.data(0, QtCore.Qt.UserRole)
        page_number = item.data(0, QtCore.Qt.UserRole + 1)
        subpage_number = item.data(0, QtCore.Qt.UserRole + 2)
        occurrence_number = int(item.data(0, QtCore.Qt.UserRole + 3) or 1)
        if item_type == 'page':
            return {
                'type': 'page',
                'page_number': int(page_number),
                'subpage_number': None,
                'occurrence_number': 1,
            }
        if item_type == 'subpage':
            return {
                'type': 'subpage',
                'page_number': int(page_number),
                'subpage_number': int(subpage_number),
                'occurrence_number': occurrence_number,
            }
        return None

    def _selected_tree_contexts(self):
        items = list(self._tree.selectedItems() or ())
        if not items:
            current = self._tree.currentItem()
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
                    'page_number': int(item.data(0, QtCore.Qt.UserRole + 1)),
                    'subpage_number': None,
                    'occurrence_number': 1,
                })
            elif item_type == 'subpage':
                key = (
                    'subpage',
                    int(item.data(0, QtCore.Qt.UserRole + 1)),
                    int(item.data(0, QtCore.Qt.UserRole + 2)),
                    int(item.data(0, QtCore.Qt.UserRole + 3) or 1),
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

    def _entries_for_tree_context(self, context):
        if context is None:
            return ()
        if context['type'] == 'page':
            return collect_page_entries(self._entries, context['page_number'])
        return collect_subpage_occurrence_entries(
            self._entries,
            context['page_number'],
            context['subpage_number'],
            context.get('occurrence_number', 1),
        ) or collect_subpage_entries(
            self._entries,
            context['page_number'],
            context['subpage_number'],
        )

    def _save_entries_as_html(self, entries, page_number, subpage_number, default_name, title):
        entries = tuple(entries)
        if not entries:
            QtWidgets.QMessageBox.information(self, EDITOR_APP_NAME, 'Nothing to save.')
            return
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            title,
            self._suggest_output_path(default_name),
            'HTML Files (*.html)',
        )
        if not target:
            return
        if not target.lower().endswith('.html'):
            target += '.html'
        packets = (Packet(entry.raw, number=index) for index, entry in enumerate(entries))
        service = Service.from_packets(packets)
        export_selected_html(
            service,
            target,
            int(page_number),
            None if subpage_number is None else int(subpage_number),
        )
        self.statusBar().showMessage(f'Saved HTML to {target}', 5000)

    def _save_tree_selection_as_t42(self):
        context = self._tree_context()
        if context is None:
            return
        entries = self._entries_for_tree_context(context)
        if context['type'] == 'page':
            page_number = int(context['page_number'])
            default_name = f'{self._page_label(page_number)[1:]}.t42'
            target, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save selected page',
                self._suggest_output_path(default_name),
                'Teletext Files (*.t42)',
            )
        else:
            page_number = int(context['page_number'])
            subpage_number = int(context['subpage_number'])
            occurrence_number = int(context.get('occurrence_number') or 1)
            suffix = f'-occ{occurrence_number}' if occurrence_number > 1 else ''
            default_name = f'{self._page_label(page_number)[1:]}-{subpage_number:04X}{suffix}.t42'
            target, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save selected subpage',
                self._suggest_output_path(default_name),
                'Teletext Files (*.t42)',
            )
        if not target:
            return
        if not target.lower().endswith('.t42'):
            target += '.t42'
        self._run_progress_task(
            'Save T42',
            'Writing packets',
            lambda report: write_t42_entries(entries, target, progress_callback=report),
        )
        self.statusBar().showMessage(f'Saved {target}', 5000)

    def _save_tree_selection_as_html(self):
        context = self._tree_context()
        if context is None:
            return
        entries = self._entries_for_tree_context(context)
        page_number = int(context['page_number'])
        if context['type'] == 'page':
            self._save_entries_as_html(
                entries,
                page_number,
                None,
                f'{self._page_label(page_number)[1:]}.html',
                'Save selected page HTML',
            )
            return
        subpage_number = int(context['subpage_number'])
        occurrence_number = int(context.get('occurrence_number') or 1)
        suffix = f'-occ{occurrence_number}' if occurrence_number > 1 else ''
        self._save_entries_as_html(
            entries,
            page_number,
            subpage_number,
            f'{self._page_label(page_number)[1:]}-{subpage_number:04X}{suffix}.html',
            'Save selected subpage HTML',
        )

    def _page_subpage_occurrences_for_entries(self, entries, page_number):
        return self._all_page_subpage_occurrences_for_entries(entries).get(int(page_number), {})

    def _page_has_hidden_subpages(self, page_number):
        return any(len(tuple(variants)) > 1 for variants in self._page_subpage_occurrences(page_number).values())

    def _convert_selected_hidden_subpage_to_real(self):
        context = self._tree_context()
        if context is None or context['type'] != 'subpage' or int(context['occurrence_number']) <= 1:
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        updated_entries, new_subpage_number = convert_subpage_occurrence_to_real(
            self._entries,
            context['page_number'],
            context['subpage_number'],
            context['occurrence_number'],
        )
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=context['page_number'],
            focus_subpage_number=new_subpage_number,
            selection_key=('subpage', int(context['page_number']), int(new_subpage_number), 1),
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Converted hidden subpage to real {int(new_subpage_number):04X}.',
            3000,
        )

    def _convert_selected_hidden_subpages_to_real(self):
        contexts = tuple(
            context for context in self._selected_tree_contexts()
            if context['type'] == 'subpage' and int(context.get('occurrence_number') or 1) > 1
        )
        if not contexts:
            self.statusBar().showMessage('No hidden subpages selected.', 2500)
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        working_entries = tuple(self._entries)
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
            self.statusBar().showMessage('No hidden subpages converted.', 2500)
            return
        focus_page_number, focus_subpage_number = converted[-1]
        self._rebuild_from_entries(
            working_entries,
            focus_page_number=focus_page_number,
            focus_subpage_number=focus_subpage_number,
            selection_key=('subpage', focus_page_number, focus_subpage_number, 1),
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Converted {len(converted)} selected hidden subpages to real subpages.',
            4000,
        )

    def _convert_page_hidden_subpages_to_real(self):
        context = self._tree_context()
        if context is None:
            return
        page_number = int(context['page_number'])
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        working_entries = tuple(self._entries)
        converted = []
        for base_subpage_number in sorted(self._page_subpage_occurrences_for_entries(working_entries, page_number)):
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
        self._rebuild_from_entries(
            working_entries,
            focus_page_number=page_number,
            selection_key=('page', page_number, None, 1),
            record_history=True,
        )
        self.statusBar().showMessage(
            'Converted hidden subpages: ' + ', '.join(f'{value:04X}' for value in converted),
            4000,
        )

    def _convert_all_hidden_subpages_to_real(self):
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        working_entries = tuple(self._entries)
        converted = []
        all_occurrences = self._all_page_subpage_occurrences_for_entries(working_entries)
        for page_number in sorted(all_occurrences):
            for base_subpage_number in sorted(all_occurrences[page_number]):
                while len(tuple(
                    self._page_subpage_occurrences_for_entries(working_entries, page_number).get(base_subpage_number, ())
                )) > 1:
                    working_entries, new_subpage_number = convert_subpage_occurrence_to_real(
                        working_entries,
                        page_number,
                        base_subpage_number,
                        2,
                    )
                    converted.append((int(page_number), int(new_subpage_number)))
        if not converted:
            self.statusBar().showMessage('No hidden subpages to convert.', 2500)
            return
        focus_page_number, focus_subpage_number = converted[-1]
        self._rebuild_from_entries(
            working_entries,
            focus_page_number=focus_page_number,
            focus_subpage_number=focus_subpage_number,
            selection_key=('subpage', focus_page_number, focus_subpage_number, 1),
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Converted {len(converted)} hidden subpages to real subpages.',
            4000,
        )

    def _change_selected_hidden_subpage_sequence(self):
        context = self._tree_context()
        if context is None or context['type'] != 'subpage':
            return
        occurrence_number = max(int(context.get('occurrence_number') or 1), 1)
        if occurrence_number <= 1:
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        variants = tuple(
            self._page_subpage_occurrences_for_entries(
                self._entries,
                int(context['page_number']),
            ).get(int(context['subpage_number']), ())
        )
        total_occurrences = len(variants)
        if total_occurrences <= 1:
            return
        target_occurrence_number, accepted = QtWidgets.QInputDialog.getInt(
            self,
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
            self._entries,
            context['page_number'],
            context['subpage_number'],
            occurrence_number,
            target_occurrence_number,
        )
        self._mark_modified_subpage(context['page_number'], context['subpage_number'])
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=int(context['page_number']),
            focus_subpage_number=int(context['subpage_number']),
            selection_key=(
                'subpage',
                int(context['page_number']),
                int(context['subpage_number']),
                target_occurrence_number,
            ),
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Hidden subpage moved to sequence {target_occurrence_number}.',
            4000,
        )

    def _show_tree_context_menu(self, position):
        item = self._tree.itemAt(position)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        context = self._tree_context()
        if context is None:
            return
        selected_contexts = self._selected_tree_contexts()
        has_multi_selection = len(selected_contexts) > 1
        menu = QtWidgets.QMenu(self)
        save_t42_action = menu.addAction('Save as T42...')
        save_t42_action.triggered.connect(self._save_tree_selection_as_t42)
        save_html_action = menu.addAction('Save as HTML...')
        save_html_action.triggered.connect(self._save_tree_selection_as_html)
        menu.addSeparator()
        if context['type'] == 'page':
            add_page_action = menu.addAction('Add Blank Page...')
            add_page_action.triggered.connect(self.add_blank_page)
            add_subpage_action = menu.addAction('Add Blank Subpage...')
            add_subpage_action.triggered.connect(self.add_blank_subpage)
            add_hidden_action = menu.addAction('Add Blank Hidden Subpage...')
            add_hidden_action.triggered.connect(self.add_hidden_subpage)
            menu.addSeparator()
            duplicate_page_action = menu.addAction('Duplicate Current Page...')
            duplicate_page_action.triggered.connect(self.duplicate_current_page)
            convert_all_hidden_action = menu.addAction('Convert All Hidden Subpages to Real')
            convert_all_hidden_action.triggered.connect(self._convert_all_hidden_subpages_to_real)
            menu.addSeparator()
            delete_action = menu.addAction('Delete Page')
            delete_action.triggered.connect(self.delete_current_page)
            if self._page_has_hidden_subpages(context['page_number']):
                convert_page_action = menu.addAction('Convert Hidden Subpages to Real')
                convert_page_action.triggered.connect(self._convert_page_hidden_subpages_to_real)
        else:
            add_subpage_action = menu.addAction('Add Blank Subpage...')
            add_subpage_action.triggered.connect(self.add_blank_subpage)
            add_hidden_action = menu.addAction('Add Blank Hidden Subpage...')
            add_hidden_action.triggered.connect(self.add_hidden_subpage)
            menu.addSeparator()
            duplicate_subpage_action = menu.addAction('Duplicate Current Subpage...')
            duplicate_subpage_action.triggered.connect(self.duplicate_current_subpage)
            menu.addSeparator()
            if int(context['occurrence_number']) > 1:
                duplicate_hidden_action = menu.addAction('Duplicate Current Hidden Subpage...')
                duplicate_hidden_action.triggered.connect(self.duplicate_current_hidden_subpage)
                sequence_action = menu.addAction('Change Hidden Sequence...')
                sequence_action.triggered.connect(self._change_selected_hidden_subpage_sequence)
                menu.addSeparator()
            delete_action = menu.addAction('Delete Subpage')
            delete_action.triggered.connect(self.delete_current_subpage)
            if int(context['occurrence_number']) > 1:
                convert_action = menu.addAction('Convert Hidden to Real Subpage')
                convert_action.triggered.connect(self._convert_selected_hidden_subpage_to_real)
        if has_multi_selection:
            menu.addSeparator()
            delete_selected_action = menu.addAction('Delete Selected')
            delete_selected_action.triggered.connect(self._delete_selected_tree_entries)
            if any(
                selected_context['type'] == 'subpage' and int(selected_context.get('occurrence_number') or 1) > 1
                for selected_context in selected_contexts
            ):
                convert_selected_action = menu.addAction('Convert Selected Hidden to Real')
                convert_selected_action.triggered.connect(self._convert_selected_hidden_subpages_to_real)
        menu.exec_(self._tree.viewport().mapToGlobal(position))

    def _set_loaded_state(self, loaded):
        enabled = bool(loaded)
        for widget in (
            self._save_button,
            self._save_as_button,
            self._save_page_button,
            self._save_subpage_button,
            self._split_button,
            self._filter_input,
            self._show_hidden_subpages_toggle,
            self._zoom_box,
            self._crt_toggle,
            self._tree,
            self._all_symbols_toggle,
            self._show_control_codes_toggle,
            self._show_grid_toggle,
            self._single_height_toggle,
            self._single_width_toggle,
            self._no_flash_toggle,
            self._widescreen_toggle,
            self._language_combo,
            self._allow_header_edit_toggle,
            self._copy_page_button,
            self._paste_page_button,
            self._copy_page_text_button,
            self._save_screenshot_button,
            self._copy_screenshot_button,
        ):
            widget.setEnabled(enabled)
        for action in self._file_actions:
            action.setEnabled(enabled)
        for action in getattr(self, '_page_actions', ()):
            action.setEnabled(enabled)
        self._set_editor_enabled(enabled and self._current_subpage_number is not None)
        for action in self._edit_actions:
            action.setEnabled(enabled and self._current_subpage_number is not None)

    def _set_editor_enabled(self, enabled):
        self._editor_enabled_base = bool(enabled)
        self._editor_group.setEnabled(self._editor_enabled_base)
        self._editor_table.setEnabled(self._editor_enabled_base)
        self._update_editor_read_only_state()

    def _zoom_changed(self):
        self._decoder.zoom = float(self._zoom_box.value() or 2.0)
        self._decoder_widget.setFixedSize(self._decoder.size())
        self._sync_preview_stage()
        self._render_current_selection()

    def _current_language_key(self):
        if not hasattr(self, '_language_combo'):
            return 'default'
        return str(self._language_combo.currentData() or 'default')

    def _set_language_from_action(self, index, checked):
        if not checked:
            return
        if 0 <= int(index) < self._language_combo.count():
            self._language_combo.setCurrentIndex(int(index))

    def _sync_language_action_state(self):
        key = self._current_language_key()
        action = getattr(self, '_language_actions', {}).get(key)
        if action is None:
            return
        was_blocked = action.blockSignals(True)
        try:
            action.setChecked(True)
        finally:
            action.blockSignals(was_blocked)

    def _show_control_codes_changed(self):
        if self._navigator is not None and self._current_page_number is not None and self._current_subpage_number is not None:
            self._refresh_editor_table_display()
        self._update_decoder_preferences()

    def _editing_locked_for_current_subpage(self):
        return False

    def _ensure_editable_current_subpage(self):
        return True

    def _update_editor_read_only_state(self):
        base_enabled = bool(getattr(self, '_editor_enabled_base', False))
        editable = base_enabled and not self._editing_locked_for_current_subpage()
        edit_triggers = (
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        ) if editable else QtWidgets.QAbstractItemView.NoEditTriggers
        self._editor_table.setEditTriggers(edit_triggers)
        for widget in (
            self._page_options_group,
            self._page_flags_group,
            self._row_tools_group,
            self._import_group,
            self._apply_edit_button,
            self._reset_edit_button,
            self._black_row_button,
            self._move_row_up_button,
            self._move_row_down_button,
            self._import_t42_button,
        ):
            widget.setEnabled(editable)
        history_actions = {
            getattr(self, '_undo_edit_action', None),
            getattr(self, '_redo_edit_action', None),
            getattr(self, '_reset_edit_action', None),
        }
        for action in getattr(self, '_edit_actions', ()):
            if action in history_actions:
                continue
            action.setEnabled(editable)
        self._update_editor_history_actions()

    def _modified_entry_marker(self, page_number, subpage_number=None):
        page_number = int(page_number)
        current_dirty = (
            self._editor_dirty
            and self._current_page_number is not None
            and int(self._current_page_number) == page_number
            and (
                subpage_number is None
                or (
                    self._current_subpage_number is not None
                    and int(self._current_subpage_number) == int(subpage_number)
                )
            )
        )
        if subpage_number is None:
            page_modified = page_number in self._modified_pages or any(
                int(current_page) == page_number for current_page, _ in self._modified_subpages
            )
            return ' *' if page_modified or current_dirty else ''
        key = (page_number, int(subpage_number))
        return ' *' if key in self._modified_subpages or current_dirty else ''

    def _mark_modified_page(self, page_number):
        self._modified_pages.add(int(page_number))

    def _mark_modified_subpage(self, page_number, subpage_number):
        self._modified_pages.add(int(page_number))
        self._modified_subpages.add((int(page_number), int(subpage_number)))

    def _page_subpage_labels(self, page_number):
        page_number = int(page_number)
        labels = []
        counts = Counter()
        for entry in self._entries:
            if (
                entry.page_number == page_number
                and entry.row == 0
                and entry.subpage_number is not None
            ):
                subpage_number = int(entry.subpage_number)
                counts[subpage_number] += 1
                occurrence = counts[subpage_number]
                label = f'{subpage_number:04X}'
                if occurrence > 1:
                    label = f'{label} ({occurrence})'
                labels.append((label, subpage_number))
        if labels:
            return tuple(labels)
        if self._navigator is None:
            return ()
        try:
            return tuple(
                (f'{int(subpage_number):04X}', int(subpage_number))
                for subpage_number in sorted(self._navigator._page(page_number).subpages)  # noqa: SLF001
            )
        except Exception:
            return ()

    def _page_subpage_occurrences(self, page_number):
        return self._all_page_subpage_occurrences_for_entries().get(int(page_number), {})

    def _all_page_subpage_occurrences_for_entries(self, entries=None):
        entries = self._entries if entries is None else tuple(entries)
        return page_subpage_occurrences(entries, mode=self._hidden_subpages_mode())

    def _legacy_page_subpage_occurrences_for_entries(self, entries=None):
        entries = self._entries if entries is None else tuple(entries)
        if entries is self._entries or tuple(entries) == tuple(self._entries):
            service = self._service
        else:
            packets = (
                Packet(entry.raw, number=index)
                for index, entry in enumerate(entries)
            )
            service = Service.from_packets(packets)
        occurrences = {}
        if service is None:
            return occurrences
        for magazine_number, magazine in sorted(service.magazines.items()):
            for page_number, page in sorted(magazine.pages.items()):
                if not page.subpages:
                    continue
                full_page_number = (int(magazine_number) << 8) | int(page_number)
                page_occurrences = occurrences.setdefault(full_page_number, {})
                for subpage_number, subpage in sorted(page.subpages.items()):
                    variants = [subpage] + list(getattr(subpage, 'duplicates', ()))
                    bucket = []
                    for occurrence_number, variant in enumerate(variants, start=1):
                        raw_header = bytes(variant.header.displayable.bytes_no_parity).decode('ascii', errors='ignore').strip()
                        bucket.append({
                            'label': f'{int(subpage_number):04X}' if occurrence_number == 1 else f'{int(subpage_number):04X} ({occurrence_number})',
                            'occurrence': occurrence_number,
                            'header_title': raw_header,
                        })
                    page_occurrences[int(subpage_number)] = bucket
        return occurrences

    def _all_subpage_occurrence_keys(self, entries=None):
        occurrences = self._all_page_subpage_occurrences_for_entries(entries)
        return tuple(
            (int(page_number), int(subpage_number), int(variant.get('occurrence') or 1))
            for page_number in sorted(occurrences)
            for subpage_number in sorted(occurrences[page_number])
            for variant in occurrences[page_number][subpage_number]
        )

    def _sync_enabled_subpage_occurrences(self, entries=None, *, preserve=True):
        available = self._all_subpage_occurrence_keys(entries)
        if not preserve:
            self._enabled_subpage_occurrences = {
                (page_number, subpage_number, occurrence_number)
                for page_number, subpage_number, occurrence_number in available
                if int(occurrence_number) == 1
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

    def _effective_entries(self):
        if not self._entries:
            return ()
        if not self._enabled_subpage_occurrences:
            return tuple(self._entries)
        counts = Counter()
        current_occurrence = {}
        filtered = []
        for entry in self._entries:
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
            if (page_number, subpage_number, occurrence_number) in self._enabled_subpage_occurrences:
                filtered.append(entry)
        return tuple(filtered)

    def _capture_document_snapshot(self):
        return {
            'entries': tuple(self._entries),
            'modified_pages': tuple(sorted(int(page) for page in self._modified_pages)),
            'modified_subpages': tuple(sorted((int(page), int(subpage)) for page, subpage in self._modified_subpages)),
            'selection_key': self._current_tree_key(),
            'editor_target': (
                int(self._current_page_number),
                int(self._current_subpage_number),
                max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1),
            ) if self._current_page_number is not None and self._current_subpage_number is not None else None,
            'editor_cursor': (
                self._preview_cursor_row,
                self._preview_cursor_col,
            ),
            'editor_selected_row': self._selected_editor_row() if hasattr(self, '_editor_table') else 1,
            'enabled_occurrences': tuple(sorted(
                (int(page), int(subpage), int(occurrence))
                for page, subpage, occurrence in self._enabled_subpage_occurrences
            )),
        }

    def _document_snapshot_signature(self, snapshot):
        return (
            tuple(snapshot.get('entries') or ()),
            tuple(snapshot.get('modified_pages') or ()),
            tuple(snapshot.get('modified_subpages') or ()),
            tuple(snapshot.get('enabled_occurrences') or ()),
        )

    def _seed_document_history(self):
        snapshot = self._capture_document_snapshot()
        self._document_initial_snapshot = snapshot
        self._document_history = [snapshot]
        self._document_history_index = 0

    def _record_document_snapshot(self, selection_key=None):
        if self._document_history_locked:
            return
        snapshot = self._capture_document_snapshot()
        if selection_key is not None:
            snapshot['selection_key'] = selection_key
        signature = self._document_snapshot_signature(snapshot)
        if self._document_history and self._document_snapshot_signature(
            self._document_history[self._document_history_index]
        ) == signature:
            return
        if self._document_history_index < len(self._document_history) - 1:
            self._document_history = self._document_history[:self._document_history_index + 1]
        self._document_history.append(snapshot)
        if len(self._document_history) > 80:
            self._document_history.pop(0)
        self._document_history_index = len(self._document_history) - 1

    def _apply_document_snapshot(self, snapshot):
        if not snapshot:
            return
        selection_key = snapshot.get('selection_key')
        editor_target = snapshot.get('editor_target')
        editor_cursor = tuple(snapshot.get('editor_cursor') or (None, None))
        editor_selected_row = int(snapshot.get('editor_selected_row', 1))
        self._document_history_locked = True
        try:
            self._modified_pages = set(int(page) for page in snapshot.get('modified_pages') or ())
            self._modified_subpages = {
                (int(page), int(subpage))
                for page, subpage in snapshot.get('modified_subpages') or ()
            }
            self._enabled_subpage_occurrences = {
                (int(page), int(subpage), int(occurrence))
                for page, subpage, occurrence in snapshot.get('enabled_occurrences') or ()
            }
            self._rebuild_from_entries(
                tuple(snapshot.get('entries') or ()),
                selection_key=selection_key,
                record_history=False,
            )
        finally:
            self._document_history_locked = False
        if (
            editor_target
            and self._current_page_number is not None
            and self._current_subpage_number is not None
            and (
                int(self._current_page_number),
                int(self._current_subpage_number),
                max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1),
            ) == tuple(int(part) for part in editor_target)
        ):
            self._select_editor_row_from_preview(editor_selected_row, start_edit=False)
            cursor_row, cursor_col = editor_cursor
            if cursor_row is None or cursor_col is None:
                self._clear_preview_cursor()
            else:
                self._set_preview_cursor(cursor_row, cursor_col)

    def _resolve_subpage_variant(self, page_number, subpage_number, occurrence_number=1):
        if self._navigator is None:
            raise KeyError((page_number, subpage_number, occurrence_number))
        occurrence_number = max(int(occurrence_number or 1), 1)
        if self._hidden_subpages_mode() == 'raw':
            if occurrence_number > 1:
                raw_occurrence_entries = collect_subpage_occurrence_entries(
                    self._entries,
                    int(page_number),
                    int(subpage_number),
                    occurrence_number,
                )
                if raw_occurrence_entries:
                    packets = (
                        Packet(entry.raw, number=int(entry.packet_index))
                        for entry in raw_occurrence_entries
                    )
                    return Subpage.from_packets(packets)
            return self._navigator.subpage(int(page_number), int(subpage_number), occurrence_number)
        page = self._navigator._page(int(page_number))  # noqa: SLF001
        base_subpage = page.subpages[int(subpage_number)]
        if occurrence_number <= 1:
            return base_subpage
        duplicate_index = occurrence_number - 2
        if 0 <= duplicate_index < len(base_subpage.duplicates):
            return base_subpage.duplicates[duplicate_index]
        return base_subpage

    def _subpage_variant_total(self, page_number, subpage_number):
        return len(tuple(self._page_subpage_occurrences(int(page_number)).get(int(subpage_number), ()))) or 1

    def _update_subpage_combo(self, page_number, current_subpage_number):
        if not hasattr(self, '_subpage_combo'):
            return
        blocked = self._subpage_combo.blockSignals(True)
        self._subpage_combo_locked = True
        try:
            self._subpage_combo.clear()
            if page_number is None:
                return
            labels = self._page_subpage_labels(page_number)
            for label, subpage_number in labels:
                self._subpage_combo.addItem(label, int(subpage_number))
            if current_subpage_number is not None:
                for index in range(self._subpage_combo.count()):
                    if int(self._subpage_combo.itemData(index)) == int(current_subpage_number):
                        self._subpage_combo.setCurrentIndex(index)
                        break
        finally:
            self._subpage_combo_locked = False
            self._subpage_combo.blockSignals(blocked)

    def _subpage_combo_changed(self, index):
        if not hasattr(self, '_subpage_combo'):
            return
        if self._subpage_combo_locked or index < 0:
            return
        page_number = self._current_page_number
        if page_number is None:
            return
        data = self._subpage_combo.itemData(index)
        if data is None:
            return
        subpage_number = int(data)
        if (
            self._current_page_number is not None
            and self._current_subpage_number is not None
            and int(self._current_page_number) == int(page_number)
            and int(self._current_subpage_number) == subpage_number
        ):
            return
        self._restore_tree_selection(('subpage', int(page_number), subpage_number))

    def _update_decoder_preferences(self):
        self._decoder.showallsymbols = self._all_symbols_toggle.isChecked()
        self._decoder.reveal = self._all_symbols_toggle.isChecked()
        self._decoder.showcontrolcodes = self._show_control_codes_toggle.isChecked()
        self._decoder.showgrid = self._show_grid_toggle.isChecked()
        self._decoder.crteffect = self._crt_toggle.isChecked()
        self._decoder.doubleheight = not self._single_height_toggle.isChecked()
        self._decoder.doublewidth = not self._single_width_toggle.isChecked()
        self._decoder.flashenabled = not self._no_flash_toggle.isChecked()
        self._decoder.horizontalscale = 1.15 if self._widescreen_toggle.isChecked() else 0.95
        self._decoder.language = self._current_language_key()
        self._sync_language_action_state()
        self._sync_preview_stage()
        self._render_current_selection()
        if (
            self._navigator is not None
            and self._current_page_number is not None
            and self._current_subpage_number is not None
        ):
            if self._editor_dirty:
                self._render_editor_preview()
            else:
                self._load_editor_for_subpage(
                    self._current_page_number,
                    self._current_subpage_number,
                    self._current_subpage_occurrence,
                )
        self._refresh_thumbnail_generation()

    def _set_tree_previews_enabled(self, visible):
        visible = bool(visible)
        if visible:
            self._queue_tree_thumbnails()
            self._refresh_thumbnail_generation()
        else:
            self._thumbnail_queue = deque()
            self._thumbnail_total = 0
            self._thumbnail_timer.stop()
            self._clear_tree_icons()
            self._tree_status_label.hide()

    def _sync_preview_stage(self):
        size = self._decoder.size()
        self._decoder_widget.setFixedSize(size)
        self._preview_stage.setFixedSize(size)
        self._trace_overlay.setFixedSize(size)
        self._preview_cursor_overlay.setFixedSize(size)
        left, top, cell_width, cell_height = self._preview_grid_metrics()
        self._preview_cursor_overlay.set_grid_metrics(left, top, cell_width, cell_height)
        self._update_trace_overlay()

    def _preview_grid_metrics(self):
        zoom = float(self._decoder.zoom)
        horizontal_scale = float(self._decoder.horizontalscale)
        border = 6.0 * zoom
        cell_width = 8.0 * zoom * horizontal_scale
        cell_height = 10.0 * zoom
        return border, border, cell_width, cell_height

    def _update_trace_overlay(self):
        if not hasattr(self, '_trace_overlay'):
            return
        self._trace_overlay.set_adjustment(
            opacity=(self._trace_opacity_slider.value() / 100.0) if hasattr(self, '_trace_opacity_slider') else 0.35,
            x_offset=self._trace_x_offset.value() if hasattr(self, '_trace_x_offset') else 0,
            y_offset=self._trace_y_offset.value() if hasattr(self, '_trace_y_offset') else 0,
            scale=self._trace_scale.value() if hasattr(self, '_trace_scale') else 1.0,
            scale_x=self._trace_scale_x.value() if hasattr(self, '_trace_scale_x') else 1.0,
            scale_y=self._trace_scale_y.value() if hasattr(self, '_trace_scale_y') else 1.0,
            rotation=self._trace_rotation.value() if hasattr(self, '_trace_rotation') else 0.0,
            flip_x=self._trace_flip_x.isChecked() if hasattr(self, '_trace_flip_x') else False,
            flip_y=self._trace_flip_y.isChecked() if hasattr(self, '_trace_flip_y') else False,
        )

    def reset_trace_adjustments(self):
        self._trace_opacity_slider.setValue(35)
        self._trace_x_offset.setValue(0)
        self._trace_y_offset.setValue(0)
        self._trace_scale.setValue(1.0)
        self._trace_scale_x.setValue(1.0)
        self._trace_scale_y.setValue(1.0)
        self._trace_rotation.setValue(0.0)
        self._trace_flip_x.setChecked(False)
        self._trace_flip_y.setChecked(False)
        self._update_trace_overlay()

    def select_trace_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            'Select trace image',
            os.path.dirname(self._filename) if self._filename else os.getcwd(),
            'Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*)',
        )
        if not path:
            return
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, 'Unable to load the selected image.')
            return
        self._trace_source_path = os.path.abspath(path)
        self._trace_overlay.set_source_pixmap(pixmap)
        self._trace_status_label.setText(os.path.basename(self._trace_source_path))
        self._update_trace_overlay()

    def clear_trace_image(self):
        self._trace_source_path = ''
        self._trace_overlay.clear_source()
        self._trace_status_label.setText('No trace image selected.')

    def _update_header_editing_state(self):
        header_item = self._editor_table.item(0, 0)
        if header_item is None:
            return
        flags = header_item.flags() | QtCore.Qt.ItemIsEditable
        if not self._allow_header_edit_toggle.isChecked():
            flags &= ~QtCore.Qt.ItemIsEditable
            header_item.setToolTip('Enable "Allow Header Editing" to edit row 0 header text.')
        else:
            header_item.setToolTip('Header text only. Page/subpage numbers stay unchanged.')
        header_item.setFlags(flags)

    def _header_row(self, page_number, subpage):
        header = np.full((40,), fill_value=0x20, dtype=np.uint8)
        magazine, page = ServiceNavigator.split_page_number(page_number)
        header[3:7] = np.frombuffer(f'P{magazine}{page:02X}'.encode('ascii'), dtype=np.uint8)
        header[8:] = subpage.header.displayable[:]
        return header

    def _compose_preview_buffer(self, page_number, subpage):
        buffer = np.full((25, 40), fill_value=0x20, dtype=np.uint8)
        buffer[0] = self._header_row(page_number, subpage)
        for row in range(1, 25):
            if subpage.has_packet(row):
                buffer[row] = subpage.packet(row).displayable[:]
        return buffer

    def _paint_decoder(self, decoder, page_number, subpage_number, occurrence_number=1):
        subpage = self._resolve_subpage_variant(page_number, subpage_number, occurrence_number)
        decoder.pagecodepage = subpage.codepage
        decoder[:] = self._compose_preview_buffer(page_number, subpage)
        return subpage

    def _clear_decoder(self):
        blank = np.full((25, 40), fill_value=0x20, dtype=np.uint8)
        self._decoder.pagecodepage = 0
        self._decoder[:] = blank
        self._sync_preview_stage()
        self._selection_label.setText('Page: ---')
        self._current_page_number = None
        self._current_subpage_number = None
        self._current_subpage_occurrence = 1
        self._clear_editor()

    def _clear_editor(self):
        self._editor_loading = True
        try:
            self._editor_row_presence = {}
            self._editor_original_text = {}
            self._editor_original_bytes = {}
            self._editor_live_bytes = {}
            self._mouse_draw_active = False
            self._mouse_draw_erase = False
            self._mouse_draw_changed = False
            self._mouse_draw_last_target = None
            self._editor_history = []
            self._editor_history_index = -1
            self._editor_initial_snapshot = None
            self._editor_initial_signature = None
            self._page_option_page_input.clear()
            self._page_option_subpage_input.clear()
            for toggle in (
                self._erase_page_toggle,
                self._newsflash_toggle,
                self._subtitle_toggle,
                self._suppress_header_toggle,
                self._update_page_toggle,
                self._interrupted_sequence_toggle,
                self._inhibit_display_toggle,
                self._magazine_serial_toggle,
            ):
                toggle.setChecked(False)
            self._page_region_spin.setValue(0)
            self._clear_fastext_fields()
            self._clear_service_830_fields()
            for row in range(25):
                item = self._editor_table.item(row, 0)
                if item is not None:
                    item.setText('')
            self._clear_preview_cursor()
            self._editor_status_label.setText('No subpage selected.')
            self._update_editor_dirty_state(False)
            self._set_editor_enabled(False)
            for action in getattr(self, '_edit_actions', ()):
                action.setEnabled(False)
        finally:
            self._editor_loading = False

    def _update_editor_dirty_state(self, dirty):
        self._editor_dirty = bool(dirty)
        if self._current_page_number is None or self._current_subpage_number is None:
            self._editor_status_label.setText('No subpage selected.')
            self._apply_edit_button.setEnabled(False)
            self._undo_edit_button.setEnabled(False)
            self._redo_edit_button.setEnabled(False)
            self._reset_edit_button.setEnabled(False)
            for action in getattr(self, '_edit_actions', ()):
                action.setEnabled(False)
            return

        status = (
            f'Editing {self._page_label(self._current_page_number)} / {self._current_subpage_number:04X}'
        )
        if int(getattr(self, '_current_subpage_occurrence', 1) or 1) > 1:
            status += f' ({int(self._current_subpage_occurrence)})'
        if dirty:
            status += ' | unapplied changes'
        self._editor_status_label.setText(status)
        self._apply_edit_button.setEnabled(dirty)
        for action in getattr(self, '_edit_actions', ()):
            action.setEnabled(True)
        self._update_editor_history_actions()

    def _sanitize_editor_text(self, text, limit):
        text = (text or '').replace('\r', ' ').replace('\n', ' ')
        cleaned = []
        for char in text:
            if char in EDITOR_CONTROL_LOOKUP or char in DEFAULT_EDITOR_CHAR_TO_BYTE:
                cleaned.append(char)
                continue
            codepoint = ord(char)
            cleaned.append(char if 0x20 <= codepoint < 0x7F else '?')
        return ''.join(cleaned)[:int(limit)]

    def _sanitize_editor_display_text(self, text, limit):
        return self._sanitize_editor_text(_transliterate_editor_text(text), limit)

    def _raw_row_bytes_from_subpage(self, subpage, row):
        if row == 0:
            return bytes(subpage.header.displayable.bytes_no_parity)
        if subpage.has_packet(row):
            return bytes(subpage.packet(row).displayable.bytes_no_parity)
        return b''

    def _bytes_to_editor_text(self, raw_bytes, show_control_codes):
        chars = []
        for value in raw_bytes:
            value = int(value) & 0x7F
            if value < 0x20:
                chars.append(EDITOR_CONTROL_CHARS[value] if show_control_codes else ' ')
            else:
                chars.append(teletext_charset.g0['default'].get(value, chr(value) if value < 0x7F else '?'))
        return ''.join(chars).rstrip(' ')

    def _editor_text_to_bytes(self, text, limit):
        payload = []
        for char in self._sanitize_editor_text(text, limit):
            if char in EDITOR_CONTROL_LOOKUP:
                payload.append(EDITOR_CONTROL_LOOKUP[char])
            elif char in DEFAULT_EDITOR_CHAR_TO_BYTE:
                payload.append(DEFAULT_EDITOR_CHAR_TO_BYTE[char])
            else:
                payload.append(ord(char) if ord(char) < 0x80 else 0x3F)
        return bytes(payload[:int(limit)])

    def _row_limit(self, row):
        return 32 if int(row) == 0 else 40

    def _blank_row_bytes(self, row):
        return b' ' * self._row_limit(row)

    def _normalize_row_bytes(self, row, raw_bytes):
        limit = self._row_limit(row)
        return bytes(raw_bytes or b'')[:limit].ljust(limit, b' ')

    def _editor_text_index(self, row, col):
        row = int(row)
        col = int(col)
        if row == 0:
            if col < 8:
                return None
            return min(col - 8, 31)
        return min(max(col, 0), 39)

    def _set_preview_cursor(self, row, col):
        row = max(0, min(int(row), 24))
        col = max(0, min(int(col), 39))
        if row == 0 and col < 8:
            col = 8
        self._preview_cursor_row = row
        self._preview_cursor_col = col
        self._preview_cursor_overlay.set_cursor(row, col)
        self._sync_control_keys_selection()
        self._update_current_history_cursor_state()

    def _clear_preview_cursor(self):
        self._preview_cursor_row = None
        self._preview_cursor_col = None
        self._preview_cursor_overlay.clear_cursor()
        self._sync_control_keys_selection()
        self._update_current_history_cursor_state()

    def _focus_preview_input(self):
        preview_stage = getattr(self, '_preview_stage', None)
        decoder_widget = getattr(self, '_decoder_widget', None)
        if preview_stage is not None and preview_stage.isVisible():
            preview_stage.setFocus(QtCore.Qt.OtherFocusReason)
            return
        if decoder_widget is not None and decoder_widget.isVisible():
            decoder_widget.setFocus(QtCore.Qt.OtherFocusReason)

    def _update_current_history_cursor_state(self):
        selected_row = self._selected_editor_row() if hasattr(self, '_editor_table') else 1
        cursor_value = (self._preview_cursor_row, self._preview_cursor_col)
        if self._editor_history and 0 <= self._editor_history_index < len(self._editor_history):
            snapshot = dict(self._editor_history[self._editor_history_index])
            snapshot['cursor'] = cursor_value
            snapshot['selected_row'] = int(selected_row)
            self._editor_history[self._editor_history_index] = snapshot
            if self._editor_history_index == 0:
                self._editor_initial_snapshot = snapshot
                self._editor_initial_signature = self._snapshot_signature(snapshot)
        if self._document_history and 0 <= self._document_history_index < len(self._document_history):
            snapshot = dict(self._document_history[self._document_history_index])
            snapshot['editor_cursor'] = cursor_value
            snapshot['editor_selected_row'] = int(selected_row)
            self._document_history[self._document_history_index] = snapshot

    def _refresh_editor_row_display(self, row):
        item = self._editor_table.item(row, 0)
        if item is None:
            return
        row_bytes = self._editor_live_bytes.get(row, self._blank_row_bytes(row))
        row_text = self._bytes_to_editor_text(row_bytes, self._show_control_codes_toggle.isChecked())
        self._editor_loading = True
        try:
            item.setText(row_text)
        finally:
            self._editor_loading = False

    def _refresh_editor_table_display(self):
        for row in range(25):
            self._refresh_editor_row_display(row)

    def _capture_editor_snapshot(self):
        selected_row = self._selected_editor_row()
        return {
            'live_bytes': {row: bytes(self._editor_live_bytes.get(row, self._blank_row_bytes(row))) for row in range(25)},
            'row_presence': {row: bool(self._editor_row_presence.get(row, False)) for row in range(25)},
            'page_text': self._page_option_page_input.text(),
            'subpage_text': self._page_option_subpage_input.text(),
            'fastext': tuple(
                (page_input.text(), subpage_input.text())
                for page_input, subpage_input in self._fastext_inputs
            ),
            'service_830': self._capture_service_830_snapshot(),
            'flags': {
                'erase_page': self._erase_page_toggle.isChecked(),
                'newsflash': self._newsflash_toggle.isChecked(),
                'subtitle': self._subtitle_toggle.isChecked(),
                'suppress_header': self._suppress_header_toggle.isChecked(),
                'update_page': self._update_page_toggle.isChecked(),
                'interrupted_sequence': self._interrupted_sequence_toggle.isChecked(),
                'inhibit_display': self._inhibit_display_toggle.isChecked(),
                'magazine_serial': self._magazine_serial_toggle.isChecked(),
                'page_region': int(self._page_region_spin.value()),
            },
            'cursor': (self._preview_cursor_row, self._preview_cursor_col),
            'selected_row': selected_row,
        }

    def _current_editor_draft_key(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return None
        return (
            int(self._current_page_number),
            int(self._current_subpage_number),
            max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1),
        )

    def _store_current_editor_draft(self):
        key = self._current_editor_draft_key()
        if key is None or self._editor_loading:
            return
        snapshot = self._capture_editor_snapshot()
        self._editor_drafts[key] = {
            'snapshot': snapshot,
            'history': list(self._editor_history),
            'history_index': int(self._editor_history_index),
            'initial_snapshot': self._editor_initial_snapshot,
            'initial_signature': self._editor_initial_signature,
        }

    def _restore_editor_draft(self, page_number, subpage_number, occurrence_number):
        key = (
            int(page_number),
            int(subpage_number),
            max(int(occurrence_number or 1), 1),
        )
        draft = self._editor_drafts.get(key)
        if not draft:
            return False
        history = list(draft.get('history') or ())
        history_index = int(draft.get('history_index', 0))
        initial_snapshot = draft.get('initial_snapshot')
        initial_signature = draft.get('initial_signature')
        snapshot = draft.get('snapshot')
        if not history and snapshot:
            history = [snapshot]
            history_index = 0
        self._editor_initial_snapshot = initial_snapshot
        self._editor_initial_signature = initial_signature
        self._editor_history = history
        self._editor_history_index = max(0, min(history_index, len(history) - 1)) if history else -1
        if snapshot:
            self._apply_editor_snapshot(snapshot)
        else:
            self._update_editor_dirty_state(False)
        self._update_editor_history_actions()
        return True

    def _snapshot_signature(self, snapshot):
        flags = snapshot.get('flags', {})
        return (
            tuple((row, bytes(snapshot['live_bytes'].get(row, self._blank_row_bytes(row)))) for row in range(25)),
            tuple((row, bool(snapshot['row_presence'].get(row, False))) for row in range(25)),
            str(snapshot.get('page_text', '')),
            str(snapshot.get('subpage_text', '')),
            tuple((str(page_text), str(subpage_text)) for page_text, subpage_text in snapshot.get('fastext', ())),
            tuple(sorted((snapshot.get('service_830') or {}).items())),
            tuple(sorted(flags.items())),
            tuple(snapshot.get('cursor') or (None, None)),
            int(snapshot.get('selected_row', 1)),
        )

    def _update_editor_history_actions(self):
        has_subpage = self._current_page_number is not None and self._current_subpage_number is not None
        can_doc_undo = self._document_history_index > 0
        can_doc_redo = 0 <= self._document_history_index < (len(self._document_history) - 1)
        if not has_subpage:
            self._undo_edit_button.setEnabled(False)
            self._redo_edit_button.setEnabled(False)
            self._reset_edit_button.setEnabled(False)
            if hasattr(self, '_undo_edit_action'):
                self._undo_edit_action.setEnabled(False)
            if hasattr(self, '_redo_edit_action'):
                self._redo_edit_action.setEnabled(False)
            if hasattr(self, '_reset_edit_action'):
                self._reset_edit_action.setEnabled(False)
            return
        if self._editing_locked_for_current_subpage() and not (can_doc_undo or can_doc_redo):
            self._undo_edit_button.setEnabled(False)
            self._redo_edit_button.setEnabled(False)
            self._reset_edit_button.setEnabled(False)
            if hasattr(self, '_undo_edit_action'):
                self._undo_edit_action.setEnabled(False)
            if hasattr(self, '_redo_edit_action'):
                self._redo_edit_action.setEnabled(False)
            if hasattr(self, '_reset_edit_action'):
                self._reset_edit_action.setEnabled(False)
            return
        can_undo = self._editor_history_index > 0 or can_doc_undo
        can_redo = 0 <= self._editor_history_index < (len(self._editor_history) - 1) or can_doc_redo
        can_reset = self._editor_dirty
        self._undo_edit_button.setEnabled(can_undo)
        self._redo_edit_button.setEnabled(can_redo)
        self._reset_edit_button.setEnabled(can_reset)
        if hasattr(self, '_undo_edit_action'):
            self._undo_edit_action.setEnabled(can_undo)
        if hasattr(self, '_redo_edit_action'):
            self._redo_edit_action.setEnabled(can_redo)
        if hasattr(self, '_reset_edit_action'):
            self._reset_edit_action.setEnabled(can_reset)

    def _seed_editor_history(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            self._editor_history = []
            self._editor_history_index = -1
            self._editor_initial_snapshot = None
            self._editor_initial_signature = None
            self._update_editor_history_actions()
            return
        snapshot = self._capture_editor_snapshot()
        self._editor_initial_snapshot = snapshot
        self._editor_history = [snapshot]
        self._editor_history_index = 0
        self._editor_initial_signature = self._snapshot_signature(snapshot)
        self._update_editor_history_actions()

    def _record_editor_snapshot(self):
        if (
            self._editor_loading
            or self._editor_history_locked
            or self._current_page_number is None
            or self._current_subpage_number is None
        ):
            return
        snapshot = self._capture_editor_snapshot()
        signature = self._snapshot_signature(snapshot)
        if self._editor_history and self._snapshot_signature(self._editor_history[self._editor_history_index]) == signature:
            self._update_editor_history_actions()
            return
        if self._editor_history_index < len(self._editor_history) - 1:
            self._editor_history = self._editor_history[:self._editor_history_index + 1]
        self._editor_history.append(snapshot)
        if len(self._editor_history) > 200:
            if len(self._editor_history) > 1:
                self._editor_history.pop(1)
            else:
                self._editor_history.pop(0)
        self._editor_history_index = len(self._editor_history) - 1
        self._update_editor_history_actions()

    def _apply_editor_snapshot(self, snapshot):
        if not snapshot:
            return
        self._editor_history_locked = True
        self._editor_loading = True
        try:
            self._editor_live_bytes = {
                row: bytes(snapshot['live_bytes'].get(row, self._blank_row_bytes(row)))
                for row in range(25)
            }
            self._editor_row_presence = {
                row: bool(snapshot['row_presence'].get(row, False))
                for row in range(25)
            }
            self._page_option_page_input.setText(str(snapshot.get('page_text', '')))
            self._page_option_subpage_input.setText(str(snapshot.get('subpage_text', '')))
            fastext_rows = tuple(snapshot.get('fastext', ()))
            for index, (page_input, subpage_input) in enumerate(self._fastext_inputs):
                page_text = ''
                subpage_text = ''
                if index < len(fastext_rows):
                    page_text = str(fastext_rows[index][0])
                    subpage_text = str(fastext_rows[index][1])
                page_input.setText(page_text)
                subpage_input.setText(subpage_text)
            self._apply_service_830_snapshot(snapshot.get('service_830', {}))
            flags = snapshot.get('flags', {})
            self._erase_page_toggle.setChecked(bool(flags.get('erase_page', False)))
            self._newsflash_toggle.setChecked(bool(flags.get('newsflash', False)))
            self._subtitle_toggle.setChecked(bool(flags.get('subtitle', False)))
            self._suppress_header_toggle.setChecked(bool(flags.get('suppress_header', False)))
            self._update_page_toggle.setChecked(bool(flags.get('update_page', False)))
            self._interrupted_sequence_toggle.setChecked(bool(flags.get('interrupted_sequence', False)))
            self._inhibit_display_toggle.setChecked(bool(flags.get('inhibit_display', False)))
            self._magazine_serial_toggle.setChecked(bool(flags.get('magazine_serial', False)))
            self._page_region_spin.setValue(int(flags.get('page_region', 0)))
            self._refresh_editor_table_display()
        finally:
            self._editor_loading = False
            self._editor_history_locked = False
        selected_row = int(snapshot.get('selected_row', 1))
        self._select_editor_row_from_preview(selected_row, start_edit=False)
        cursor_row, cursor_col = snapshot.get('cursor', (None, None))
        if cursor_row is None or cursor_col is None:
            self._clear_preview_cursor()
        else:
            self._set_preview_cursor(cursor_row, cursor_col)
        dirty = self._snapshot_signature(snapshot) != self._editor_initial_signature
        self._update_editor_dirty_state(dirty)
        self._render_editor_preview()
        self._focus_preview_input()

    def undo_current_edits(self):
        if self._editor_history_index > 0:
            self._editor_history_index -= 1
            self._apply_editor_snapshot(self._editor_history[self._editor_history_index])
            self.statusBar().showMessage('Undo edit.', 2000)
            return
        if self._document_history_index > 0:
            self._document_history_index -= 1
            self._apply_document_snapshot(self._document_history[self._document_history_index])
            self.statusBar().showMessage('Undo document change.', 2000)

    def redo_current_edits(self):
        if self._editor_history_index >= 0 and self._editor_history_index < len(self._editor_history) - 1:
            self._editor_history_index += 1
            self._apply_editor_snapshot(self._editor_history[self._editor_history_index])
            self.statusBar().showMessage('Redo edit.', 2000)
            return
        if 0 <= self._document_history_index < len(self._document_history) - 1:
            self._document_history_index += 1
            self._apply_document_snapshot(self._document_history[self._document_history_index])
            self.statusBar().showMessage('Redo document change.', 2000)

    def reset_current_edits(self):
        if not self._editor_initial_snapshot:
            return
        self._editor_history_index = 0
        self._apply_editor_snapshot(self._editor_initial_snapshot)
        self.statusBar().showMessage('Reset current edits.', 3000)

    def _build_editor_subpage(self):
        editable = self._current_subpage_copy()
        if editable is None:
            return None
        page_number = int(self._current_page_number)
        subpage_number = int(self._current_subpage_number)
        magazine = page_number >> 8
        editable.packet(0).mrag.magazine = magazine
        editable.header.page = page_number & 0xFF
        editable.header.subpage = subpage_number
        editable.header.control = self._header_control_from_widgets()
        editable.header.displayable.place_string(self._editor_live_bytes.get(0, self._blank_row_bytes(0)))
        for row in range(1, 25):
            row_bytes = self._editor_live_bytes.get(row, self._blank_row_bytes(row))
            keep_row = bool(self._editor_row_presence.get(row, False)) or bool(bytes(row_bytes).strip())
            if not keep_row:
                self._remove_subpage_packet(editable, row, 0)
                continue
            if not editable.has_packet(row):
                editable.init_packet(row, 0, magazine)
            editable.packet(row).displayable.place_string(row_bytes)
        self._apply_fastext_to_subpage(editable, page_number, strict=False)
        return editable

    def _render_editor_preview(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return
        subpage = self._build_editor_subpage()
        if subpage is None:
            return
        self._decoder.pagecodepage = subpage.codepage
        self._decoder[:] = self._compose_preview_buffer(self._current_page_number, subpage)
        self._sync_preview_stage()

    def _move_preview_cursor(self, delta_row=0, delta_col=0):
        if self._preview_cursor_row is None or self._preview_cursor_col is None:
            self._set_preview_cursor(0, 8)
            return
        self._set_preview_cursor(self._preview_cursor_row + delta_row, self._preview_cursor_col + delta_col)

    def _apply_preview_byte(self, value, *, advance=True, control_code=False, record_history=True):
        if not self._ensure_editable_current_subpage():
            return False
        if self._preview_cursor_row is None or self._preview_cursor_col is None:
            return False
        row = int(self._preview_cursor_row)
        col = int(self._preview_cursor_col)
        text_index = self._editor_text_index(row, col)
        if text_index is None:
            if row == 0 and not control_code and not self._allow_header_edit_toggle.isChecked():
                self.statusBar().showMessage('Enable "Allow Header Editing" to type on row 0.', 3000)
            return False
        if row == 0 and not control_code and not self._allow_header_edit_toggle.isChecked():
            self.statusBar().showMessage('Enable "Allow Header Editing" to type on row 0.', 3000)
            return False
        row_bytes = bytearray(self._editor_live_bytes.get(row, self._blank_row_bytes(row)))
        row_bytes[text_index] = int(value) & 0x7F
        self._editor_live_bytes[row] = bytes(row_bytes)
        if row > 0:
            self._editor_row_presence[row] = True
        self._refresh_editor_row_display(row)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._sync_control_keys_selection()
        if record_history:
            self._record_editor_snapshot()
        if advance:
            self._move_preview_cursor(delta_col=1)
        return True

    def _preview_byte_at(self, row, col):
        text_index = self._editor_text_index(row, col)
        if text_index is None:
            return None
        row_bytes = self._editor_live_bytes.get(int(row), self._blank_row_bytes(int(row)))
        if text_index >= len(row_bytes):
            return None
        return int(row_bytes[text_index]) & 0x7F

    def _mosaic_mask_from_code(self, code):
        code = int(code) & 0x7F
        if code < 0x20 or 0x40 <= code < 0x60:
            return None
        return (code & 0x1F) | ((code & 0x40) >> 1)

    def _mosaic_code_from_mask(self, mask):
        mask = max(0, min(int(mask), 0x3F))
        return 0x20 + (mask & 0x1F) + (0x40 if mask & 0x20 else 0)

    def _row_mosaic_active_at(self, row, col):
        if row <= 0:
            return False
        row_bytes = self._editor_live_bytes.get(int(row), self._blank_row_bytes(int(row)))
        limit = max(0, min(int(col), len(row_bytes)))
        mosaic = False
        for value in row_bytes[:limit]:
            value = int(value) & 0x7F
            high = value & 0xF0
            low = value & 0x0F
            if high == 0x00 and low < 0x08:
                mosaic = False
            elif high == 0x10 and low < 0x08:
                mosaic = True
        return mosaic

    def _preview_mosaic_target(self, watched, event):
        decoder_widget = getattr(self, '_decoder_widget', None)
        preview_stage = getattr(self, '_preview_stage', None)
        if decoder_widget is None:
            return None
        if watched is preview_stage:
            origin = decoder_widget.mapTo(preview_stage, QtCore.QPoint(0, 0))
            x = float(event.pos().x() - origin.x())
            y = float(event.pos().y() - origin.y())
        else:
            x = float(event.pos().x())
            y = float(event.pos().y())
        left, top, cell_width, cell_height = self._preview_grid_metrics()
        inner_x = x - left
        inner_y = y - top
        if not (0 <= inner_x < cell_width * 40 and 0 <= inner_y < cell_height * 25):
            return None
        row = max(0, min(int(inner_y / float(cell_height)), 24))
        col = max(0, min(int(inner_x / float(cell_width)), 39))
        local_x = inner_x - (col * float(cell_width))
        local_y = inner_y - (row * float(cell_height))
        subcol = 0 if local_x < (float(cell_width) / 2.0) else 1
        third = float(cell_height) / 3.0
        if local_y < third:
            subrow = 0
        elif local_y < (third * 2.0):
            subrow = 1
        else:
            subrow = 2
        bit = 1 << ((subrow * 2) + subcol)
        return row, col, bit

    def _start_mouse_draw(self, row, col, bit, *, erase=False):
        if not self._mouse_draw_toggle.isChecked():
            return False
        if not self._ensure_editable_current_subpage():
            return False
        if row <= 0:
            return False
        text_index = self._editor_text_index(row, col)
        if text_index is None:
            return False
        current_value = self._preview_byte_at(row, col)
        if current_value is None or current_value < 0x20:
            return False
        if not self._row_mosaic_active_at(row, col):
            return False
        if self._mosaic_mask_from_code(current_value) is None:
            return False
        self._mouse_draw_active = True
        self._mouse_draw_erase = bool(erase)
        self._mouse_draw_changed = False
        self._mouse_draw_last_target = None
        self.statusBar().showMessage(
            'Mouse Draw: left button paints mosaic blocks, right button clears them.',
            1500,
        )
        return self._apply_mouse_draw_target(row, col, bit)

    def _stop_mouse_draw(self):
        changed = bool(self._mouse_draw_changed)
        self._mouse_draw_active = False
        self._mouse_draw_erase = False
        self._mouse_draw_last_target = None
        self._mouse_draw_changed = False
        if changed:
            self._record_editor_snapshot()

    def _apply_mouse_draw_target(self, row, col, bit):
        if not self._mouse_draw_active:
            return False
        target = (int(row), int(col), int(bit))
        if self._mouse_draw_last_target == target:
            return False
        if row <= 0:
            return False
        if not self._row_mosaic_active_at(row, col):
            return False
        current_value = self._preview_byte_at(row, col)
        if current_value is None or current_value < 0x20:
            return False
        current_mask = self._mosaic_mask_from_code(current_value)
        if current_mask is None:
            return False
        new_mask = current_mask & ~int(bit) if self._mouse_draw_erase else current_mask | int(bit)
        if new_mask == current_mask:
            self._mouse_draw_last_target = target
            return False
        self._set_preview_cursor(row, col)
        self._select_editor_row_from_preview(row, start_edit=False)
        applied = self._apply_preview_byte(
            self._mosaic_code_from_mask(new_mask),
            advance=False,
            control_code=False,
            record_history=False,
        )
        if applied:
            self._mouse_draw_last_target = target
            self._mouse_draw_changed = True
        return applied

    def _clear_preview_cell(self, *, move_back=False):
        if self._preview_cursor_row is None or self._preview_cursor_col is None:
            return
        if move_back:
            self._move_preview_cursor(delta_col=-1)
        self._apply_preview_byte(0x20, advance=False)

    def _preview_insert_text(self, text):
        inserted = False
        for char in _transliterate_editor_text(text):
            if ord(char) < 0x20:
                continue
            value = DEFAULT_EDITOR_CHAR_TO_BYTE.get(char)
            if value is None:
                value = ord(char) if ord(char) < 0x80 else 0x3F
            inserted = self._apply_preview_byte(value, advance=True) or inserted
        return inserted

    def _header_control_from_widgets(self):
        control = 0
        if self._erase_page_toggle.isChecked():
            control |= 0x001
        if self._newsflash_toggle.isChecked():
            control |= 0x002
        if self._subtitle_toggle.isChecked():
            control |= 0x004
        if self._suppress_header_toggle.isChecked():
            control |= 0x008
        if self._update_page_toggle.isChecked():
            control |= 0x010
        if self._interrupted_sequence_toggle.isChecked():
            control |= 0x020
        if self._inhibit_display_toggle.isChecked():
            control |= 0x040
        if self._magazine_serial_toggle.isChecked():
            control |= 0x080
        control |= (int(self._page_region_spin.value()) & 0x7) << 8
        return control

    def _load_page_flags_from_subpage(self, subpage):
        control = int(subpage.header.control)
        self._erase_page_toggle.setChecked(bool(control & 0x001))
        self._newsflash_toggle.setChecked(bool(control & 0x002))
        self._subtitle_toggle.setChecked(bool(control & 0x004))
        self._suppress_header_toggle.setChecked(bool(control & 0x008))
        self._update_page_toggle.setChecked(bool(control & 0x010))
        self._interrupted_sequence_toggle.setChecked(bool(control & 0x020))
        self._inhibit_display_toggle.setChecked(bool(control & 0x040))
        self._magazine_serial_toggle.setChecked(bool(control & 0x080))
        self._page_region_spin.setValue((control >> 8) & 0x7)

    def _row_text_from_subpage(self, subpage, row):
        raw = self._raw_row_bytes_from_subpage(subpage, row)
        return self._bytes_to_editor_text(raw, self._show_control_codes_toggle.isChecked())

    def _load_editor_for_subpage(self, page_number, subpage_number, occurrence_number=1):
        if self._navigator is None:
            self._clear_editor()
            return
        subpage = self._resolve_subpage_variant(page_number, subpage_number, occurrence_number)
        self._editor_loading = True
        try:
            self._editor_row_presence = {0: True}
            self._editor_original_text = {}
            self._editor_original_bytes = {}
            self._editor_live_bytes = {}
            self._page_option_page_input.setText(self._page_label(page_number)[1:])
            self._page_option_subpage_input.setText(f'{int(subpage_number):04X}')
            self._load_page_flags_from_subpage(subpage)
            self._load_fastext_from_subpage(subpage)
            for row in range(1, 25):
                self._editor_row_presence[row] = bool(subpage.has_packet(row))
            for row in range(25):
                item = self._editor_table.item(row, 0)
                if item is not None:
                    row_bytes = self._normalize_row_bytes(row, self._raw_row_bytes_from_subpage(subpage, row))
                    row_text = self._bytes_to_editor_text(row_bytes, self._show_control_codes_toggle.isChecked())
                    self._editor_original_bytes[row] = bytes(row_bytes)
                    self._editor_original_text[row] = row_text
                    self._editor_live_bytes[row] = bytes(row_bytes)
                    item.setText(row_text)
        finally:
            self._editor_loading = False
        self._current_subpage_occurrence = max(int(occurrence_number or 1), 1)
        self._update_header_editing_state()
        self._set_editor_enabled(True)
        self._update_editor_dirty_state(False)
        self._clear_preview_cursor()
        if not self._restore_editor_draft(page_number, subpage_number, occurrence_number):
            self._seed_editor_history()
        self._update_header_editing_state()
        self._set_editor_enabled(True)

    def _editor_item_changed(self, item):
        if self._editor_loading or item is None:
            return
        limit = 32 if item.row() == 0 else 40
        clean_text = self._sanitize_editor_display_text(item.text(), limit)
        if clean_text != item.text():
            self._editor_loading = True
            try:
                item.setText(clean_text)
            finally:
                self._editor_loading = False
        self._editor_live_bytes[item.row()] = self._normalize_row_bytes(
            item.row(),
            self._editor_text_to_bytes(clean_text, limit),
        )
        if item.row() > 0:
            self._editor_row_presence[item.row()] = bool(self._editor_row_presence.get(item.row(), False)) or bool(
                self._editor_live_bytes[item.row()].strip()
            )
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._sync_control_keys_selection()
        self._record_editor_snapshot()

    def _editor_meta_changed(self, *_args):
        if self._editor_loading:
            return
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def _current_subpage_copy(self):
        if self._navigator is None or self._current_page_number is None or self._current_subpage_number is None:
            return None
        source = self._resolve_subpage_variant(
            self._current_page_number,
            self._current_subpage_number,
            self._current_subpage_occurrence,
        )
        editable = Subpage(array=np.array(source._array, copy=True), numbers=source.numbers.copy())
        if hasattr(source, '_confidences'):
            editable._confidences = np.array(source._confidences, copy=True)
        return editable

    def _remove_subpage_packet(self, subpage, row, dc=0):
        if subpage is None:
            return
        try:
            slot = subpage._slot(int(row), int(dc))
        except Exception:
            return
        try:
            subpage._numbers[slot] = -100
        except Exception:
            pass
        try:
            subpage._array[slot, :] = 0
        except Exception:
            pass
        if hasattr(subpage, '_confidences'):
            try:
                subpage._confidences[slot] = -1.0
            except Exception:
                pass

    def _rebuild_from_entries(
        self,
        entries,
        *,
        focus_page_number=None,
        focus_subpage_number=None,
        selection_key=None,
        record_history=False,
        preserve_enabled_occurrences=True,
    ):
        self._entries = tuple(entries)
        packets = (
            Packet(entry.raw, number=index)
            for index, entry in enumerate(self._entries)
        )
        self._service = Service.from_packets(packets)
        self._navigator = ServiceNavigator(self._service, raw_entries=self._entries)
        self._apply_hidden_subpages_mode_to_navigator()
        self._page_summary = tuple(summarise_t42_pages(self._entries))
        self._load_service_830_from_entries(self._entries)
        self._sync_enabled_subpage_occurrences(
            self._entries,
            preserve=bool(preserve_enabled_occurrences),
        )
        self._thumbnail_cache.clear()
        self._thumbnail_queue = deque()
        self._thumbnail_total = 0
        active_selection_key = selection_key
        if active_selection_key is None and focus_page_number is not None:
            active_selection_key = (
                'subpage' if focus_subpage_number is not None else 'page',
                int(focus_page_number),
                None if focus_subpage_number is None else int(focus_subpage_number),
                1,
            )
        self._rebuild_tree(selection_key=active_selection_key)
        if record_history:
            self._record_document_snapshot(selection_key=active_selection_key)

    def revert_current_edits(self):
        self.reset_current_edits()

    def apply_current_edits(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return
        editable = self._current_subpage_copy()
        if editable is None:
            return

        page_number = int(self._current_page_number)
        subpage_number = int(self._current_subpage_number)
        occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        current_draft_key = self._current_editor_draft_key()
        magazine = page_number >> 8
        editable.packet(0).mrag.magazine = magazine
        editable.header.page = page_number & 0xFF
        editable.header.subpage = subpage_number
        editable.header.control = self._header_control_from_widgets()

        header_bytes = self._editor_live_bytes.get(0, self._blank_row_bytes(0))
        editable.header.displayable.place_string(header_bytes)

        for row in range(1, 25):
            row_bytes = self._editor_live_bytes.get(row, self._blank_row_bytes(row))
            keep_row = bool(self._editor_row_presence.get(row, False)) or bool(bytes(row_bytes).strip())
            if not keep_row:
                self._remove_subpage_packet(editable, row, 0)
                continue
            if not editable.has_packet(row):
                editable.init_packet(row, 0, magazine)
            editable.packet(row).displayable.place_string(row_bytes)
        try:
            self._apply_fastext_to_subpage(editable, page_number, strict=True)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return

        replacement_entries = build_t42_entries(packet.to_bytes() for packet in editable.packets)
        self._mark_modified_subpage(page_number, subpage_number)
        if current_draft_key is not None:
            self._editor_drafts.pop(current_draft_key, None)
        if occurrence_number > 1:
            updated_entries = replace_subpage_occurrence_in_entries(
                self._entries,
                replacement_entries,
                page_number,
                subpage_number,
                occurrence_number,
                target_page_number=page_number,
                target_subpage_number=subpage_number,
            )
        else:
            updated_entries = replace_subpage_in_entries(
                self._entries,
                replacement_entries,
                target_page_number=page_number,
                target_subpage_number=subpage_number,
            )
        try:
            updated_entries = self._apply_service_830_to_entries(updated_entries, strict=True)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=page_number,
            focus_subpage_number=subpage_number,
            selection_key=('subpage', page_number, subpage_number, occurrence_number),
            record_history=True,
        )
        self.statusBar().showMessage(
            (
                f'Applied edits to {self._page_label(page_number)} / {subpage_number:04X} ({occurrence_number}).'
                if occurrence_number > 1
                else f'Applied edits to {self._page_label(page_number)} / {subpage_number:04X}.'
            ),
            5000,
        )

    def apply_page_options(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return

        page_text = self._sanitize_editor_text(self._page_option_page_input.text().upper(), 3)
        subpage_text = self._sanitize_editor_text(self._page_option_subpage_input.text().upper(), 4)
        try:
            target_page_number = parse_page_identifier(page_text)
            target_subpage_number = parse_subpage_identifier(subpage_text)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return

        if self._editor_dirty:
            self.apply_current_edits()

        source_page_number = int(self._current_page_number)
        source_subpage_number = int(self._current_subpage_number)
        source_occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        target_occurrence_number = 1
        if target_page_number == source_page_number and target_subpage_number == source_subpage_number:
            self.statusBar().showMessage('Page options unchanged.', 3000)
            return

        if source_occurrence_number > 1:
            source_entries = collect_subpage_occurrence_entries(
                self._entries,
                source_page_number,
                source_subpage_number,
                source_occurrence_number,
            )
            updated_entries = replace_subpage_occurrence_in_entries(
                self._entries,
                source_entries,
                source_page_number,
                source_subpage_number,
                source_occurrence_number,
                target_page_number=target_page_number,
                target_subpage_number=target_subpage_number,
            )
            target_occurrence_number = max(
                len(
                    tuple(
                        self._page_subpage_occurrences_for_entries(updated_entries, target_page_number).get(
                            target_subpage_number,
                            (),
                        )
                    )
                ),
                1,
            )
        else:
            updated_entries = move_subpage_in_entries(
                self._entries,
                source_page_number,
                source_subpage_number,
                target_page_number,
                target_subpage_number,
            )
        self._mark_modified_subpage(target_page_number, target_subpage_number)
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=target_page_number,
            focus_subpage_number=target_subpage_number,
            selection_key=('subpage', target_page_number, target_subpage_number, target_occurrence_number),
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Moved subpage to {self._page_label(target_page_number)} / {target_subpage_number:04X}.',
            5000,
        )

    def reset_page_options(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return
        self._editor_loading = True
        try:
            self._page_option_page_input.setText(self._page_label(self._current_page_number)[1:])
            self._page_option_subpage_input.setText(f'{int(self._current_subpage_number):04X}')
        finally:
            self._editor_loading = False
        self._update_editor_dirty_state(True)
        self._record_editor_snapshot()
        self.statusBar().showMessage('Page options reset to the current subpage.', 2500)

    def reset_page_flags(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return
        subpage = self._resolve_subpage_variant(
            self._current_page_number,
            self._current_subpage_number,
            self._current_subpage_occurrence,
        )
        self._editor_loading = True
        try:
            self._load_page_flags_from_subpage(subpage)
        finally:
            self._editor_loading = False
        self._update_editor_dirty_state(True)
        self._record_editor_snapshot()
        self.statusBar().showMessage('Page flags reset to the current subpage.', 2500)

    def _selected_editor_row(self):
        item = self._editor_table.currentItem()
        if item is not None:
            return int(item.row())
        ranges = self._editor_table.selectedRanges()
        if ranges:
            return int(ranges[0].topRow())
        return 1

    def _shift_rows_down(self, start_row):
        for row in range(24, start_row, -1):
            self._editor_live_bytes[row] = bytes(self._editor_live_bytes.get(row - 1, self._blank_row_bytes(row - 1)))
            self._editor_row_presence[row] = bool(self._editor_row_presence.get(row - 1, False))
            self._refresh_editor_row_display(row)
        self._editor_live_bytes[start_row] = self._blank_row_bytes(start_row)
        self._editor_row_presence[start_row] = False
        self._refresh_editor_row_display(start_row)

    def _shift_rows_up(self, start_row):
        for row in range(start_row, 24):
            self._editor_live_bytes[row] = bytes(self._editor_live_bytes.get(row + 1, self._blank_row_bytes(row + 1)))
            self._editor_row_presence[row] = bool(self._editor_row_presence.get(row + 1, False))
            self._refresh_editor_row_display(row)
        self._editor_live_bytes[24] = self._blank_row_bytes(24)
        self._editor_row_presence[24] = False
        self._refresh_editor_row_display(24)

    def insert_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = max(1, min(self._selected_editor_row(), 24))
        self._shift_rows_down(row)
        self._select_editor_row_from_preview(row, start_edit=False)
        self._set_preview_cursor(row, 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def delete_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = max(1, min(self._selected_editor_row(), 24))
        self._shift_rows_up(row)
        self._select_editor_row_from_preview(row, start_edit=False)
        self._set_preview_cursor(row, 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def duplicate_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = max(1, min(self._selected_editor_row(), 24))
        source = bytes(self._editor_live_bytes.get(row, self._blank_row_bytes(row)))
        source_presence = bool(self._editor_row_presence.get(row, False))
        if row < 24:
            self._shift_rows_down(row + 1)
            self._editor_live_bytes[row + 1] = source
            self._editor_row_presence[row + 1] = source_presence
            self._refresh_editor_row_display(row + 1)
            self._select_editor_row_from_preview(row + 1, start_edit=False)
            self._set_preview_cursor(row + 1, 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def move_selected_row_up(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = max(1, min(self._selected_editor_row(), 24))
        if row <= 1:
            return
        current_bytes = bytes(self._editor_live_bytes.get(row, self._blank_row_bytes(row)))
        current_presence = bool(self._editor_row_presence.get(row, False))
        above_bytes = bytes(self._editor_live_bytes.get(row - 1, self._blank_row_bytes(row - 1)))
        above_presence = bool(self._editor_row_presence.get(row - 1, False))
        self._editor_live_bytes[row - 1] = current_bytes
        self._editor_row_presence[row - 1] = current_presence
        self._editor_live_bytes[row] = above_bytes
        self._editor_row_presence[row] = above_presence
        self._refresh_editor_row_display(row - 1)
        self._refresh_editor_row_display(row)
        self._select_editor_row_from_preview(row - 1, start_edit=False)
        self._set_preview_cursor(row - 1, max(self._preview_cursor_col or 0, 0))
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def move_selected_row_down(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = max(1, min(self._selected_editor_row(), 24))
        if row >= 24:
            return
        current_bytes = bytes(self._editor_live_bytes.get(row, self._blank_row_bytes(row)))
        current_presence = bool(self._editor_row_presence.get(row, False))
        below_bytes = bytes(self._editor_live_bytes.get(row + 1, self._blank_row_bytes(row + 1)))
        below_presence = bool(self._editor_row_presence.get(row + 1, False))
        self._editor_live_bytes[row + 1] = current_bytes
        self._editor_row_presence[row + 1] = current_presence
        self._editor_live_bytes[row] = below_bytes
        self._editor_row_presence[row] = below_presence
        self._refresh_editor_row_display(row)
        self._refresh_editor_row_display(row + 1)
        self._select_editor_row_from_preview(row + 1, start_edit=False)
        self._set_preview_cursor(row + 1, max(self._preview_cursor_col or 0, 0))
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def clear_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = self._selected_editor_row()
        if row == 0:
            self._editor_live_bytes[0] = self._blank_row_bytes(0)
        else:
            self._editor_live_bytes[row] = self._blank_row_bytes(row)
            self._editor_row_presence[row] = False
        self._refresh_editor_row_display(row)
        self._select_editor_row_from_preview(row, start_edit=False)
        self._set_preview_cursor(row, 8 if row == 0 else 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def black_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        row = max(1, min(self._selected_editor_row(), 24))
        self._editor_live_bytes[row] = self._blank_row_bytes(row)
        self._editor_row_presence[row] = True
        self._refresh_editor_row_display(row)
        self._select_editor_row_from_preview(row, start_edit=False)
        self._set_preview_cursor(row, 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def clear_page_content(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        for row in range(1, 25):
            self._editor_live_bytes[row] = self._blank_row_bytes(row)
            self._editor_row_presence[row] = False
            self._refresh_editor_row_display(row)
        self._select_editor_row_from_preview(1, start_edit=False)
        self._set_preview_cursor(1, 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()

    def _current_subpage_for_copy(self):
        if self._current_page_number is None or self._current_subpage_number is None:
            return None
        subpage = self._build_editor_subpage()
        if subpage is not None:
            return subpage
        return self._resolve_subpage_variant(
            self._current_page_number,
            self._current_subpage_number,
            self._current_subpage_occurrence,
        )

    def _current_page_text(self):
        subpage = self._current_subpage_for_copy()
        if subpage is None or self._current_page_number is None:
            return ''
        return render_subpage_text(
            self._current_page_number,
            subpage,
            localcodepage=self._current_language_key(),
            doubleheight=not self._single_height_toggle.isChecked(),
            doublewidth=not self._single_width_toggle.isChecked(),
            flashenabled=not self._no_flash_toggle.isChecked(),
            reveal=self._all_symbols_toggle.isChecked(),
        )

    def _current_page_text_lines(self):
        text = self._current_page_text()
        if not text:
            return []
        return text.splitlines()

    def _current_editor_row_text(self, row):
        item = self._editor_table.item(int(row), 0) if hasattr(self, '_editor_table') else None
        if item is None:
            return ''
        return str(item.text() or '')

    def _current_editor_page_text(self):
        lines = [self._current_editor_row_text(row) for row in range(25)]
        return '\n'.join(lines)

    def _set_clipboard_text(self, text, success_message=''):
        if _copy_text_to_system_clipboard(text):
            if success_message:
                self.statusBar().showMessage(success_message, 2500)
            return True
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is None:
            self.statusBar().showMessage('Clipboard is not available.', 3000)
            return False
        text = '' if text is None else str(text)
        try:
            clipboard.setText(text, mode=clipboard.Clipboard)
            if clipboard.supportsSelection():
                clipboard.setText(text, mode=clipboard.Selection)
            QtWidgets.QApplication.processEvents()
            if success_message:
                self.statusBar().showMessage(success_message, 2500)
            return True
        except Exception:
            self.statusBar().showMessage('Clipboard copy failed.', 4000)
            return False

    def copy_selected_row(self):
        if self._current_subpage_number is None:
            return
        row = self._selected_editor_row()
        self._row_clipboard_bytes = bytes(self._editor_live_bytes.get(row, self._blank_row_bytes(row)))
        self._row_clipboard_presence = True if row == 0 else bool(self._editor_row_presence.get(row, False))
        self._row_clipboard_row = int(row)
        self.statusBar().showMessage(f'Copied row {int(row):02d} for paste.', 2500)

    def copy_selected_row_text(self):
        if self._current_subpage_number is None:
            return
        row = self._selected_editor_row()
        text = self._current_editor_row_text(row)
        if text:
            self._set_clipboard_text(text, f'Copied row {int(row):02d} text to clipboard.')
        else:
            self.statusBar().showMessage(f'Row {int(row):02d} has no text to copy.', 2500)

    def copy_current_page(self):
        subpage = self._current_subpage_for_copy()
        if subpage is None or self._current_page_number is None or self._current_subpage_number is None:
            return
        self._page_clipboard_subpage = Subpage(array=np.array(subpage._array, copy=True), numbers=subpage.numbers.copy())
        if hasattr(subpage, '_confidences'):
            self._page_clipboard_subpage._confidences = np.array(subpage._confidences, copy=True)
        occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        label = f'{self._page_label(int(self._current_page_number))} / {int(self._current_subpage_number):04X}'
        if occurrence_number > 1:
            label += f' ({occurrence_number})'
        self._page_clipboard_label = label
        self.statusBar().showMessage(f'Copied {label} for paste.', 2500)

    def copy_current_page_text(self):
        text = self._current_editor_page_text()
        if not text:
            return
        self._set_clipboard_text(text, 'Copied current page text to clipboard.')

    def paste_current_page(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_page_number is None or self._current_subpage_number is None:
            return
        source = self._page_clipboard_subpage
        if source is None:
            self.statusBar().showMessage('Page clipboard is empty.', 2500)
            return
        editable = Subpage(array=np.array(source._array, copy=True), numbers=source.numbers.copy())
        if hasattr(source, '_confidences'):
            editable._confidences = np.array(source._confidences, copy=True)
        page_number = int(self._current_page_number)
        subpage_number = int(self._current_subpage_number)
        occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        magazine = page_number >> 8
        editable.packet(0).mrag.magazine = magazine
        editable.header.page = page_number & 0xFF
        editable.header.subpage = subpage_number
        replacement_entries = build_t42_entries(packet.to_bytes() for packet in editable.packets)
        if occurrence_number > 1:
            updated_entries = replace_subpage_occurrence_in_entries(
                self._entries,
                replacement_entries,
                page_number,
                subpage_number,
                occurrence_number,
                target_page_number=page_number,
                target_subpage_number=subpage_number,
            )
        else:
            updated_entries = replace_subpage_in_entries(
                self._entries,
                replacement_entries,
                target_page_number=page_number,
                target_subpage_number=subpage_number,
            )
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=page_number,
            focus_subpage_number=subpage_number,
            selection_key=('subpage', page_number, subpage_number, occurrence_number),
            record_history=True,
        )
        source_label = self._page_clipboard_label or 'page clipboard'
        self.statusBar().showMessage(
            f'Pasted {source_label} into {self._page_label(page_number)} / {subpage_number:04X}.',
            3500,
        )

    def paste_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        if self._row_clipboard_bytes is None:
            self.statusBar().showMessage('Row clipboard is empty.', 2500)
            return
        row = self._selected_editor_row()
        self._editor_live_bytes[row] = self._normalize_row_bytes(row, self._row_clipboard_bytes)
        self._editor_row_presence[row] = True if row == 0 else bool(self._row_clipboard_presence)
        self._refresh_editor_row_display(row)
        self._select_editor_row_from_preview(row, start_edit=False)
        self._set_preview_cursor(row, 8 if row == 0 else 0)
        self._update_editor_dirty_state(True)
        self._render_editor_preview()
        self._record_editor_snapshot()
        self.statusBar().showMessage(
            f'Pasted row {int(getattr(self, "_row_clipboard_row", row) or row):02d} into row {int(row):02d}.',
            2500,
        )

    def cut_selected_row(self):
        if not self._ensure_editable_current_subpage():
            return
        if self._current_subpage_number is None:
            return
        self.copy_selected_row()
        self.clear_selected_row()

    def _ensure_source_dialog(self):
        if self._source_dialog is None or self._source_dialog.parent() is not self:
            if self._source_dialog is not None:
                self._source_dialog.close()
            self._source_dialog = T42SourceDialog(self)
        return self._source_dialog

    def _prepare_import_target(self):
        if self._editor_dirty:
            self.apply_current_edits()
        target_page_number = None if self._current_page_number is None else int(self._current_page_number)
        target_subpage_number = None if self._current_subpage_number is None else int(self._current_subpage_number)
        target_row_number = max(0, min(self._selected_editor_row(), 24))
        return target_page_number, target_subpage_number, target_row_number

    def _cleanup_source_preview_window(self, window, temp_path):
        self._source_preview_windows = [candidate for candidate in self._source_preview_windows if candidate is not window]
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            self._source_preview_temp_paths.discard(temp_path)

    def _schedule_source_preview_navigation(self, window, page_number, subpage_number, remaining_attempts=80):
        if remaining_attempts <= 0 or getattr(window, '_navigator', None) is None:
            return
        try:
            success = window._navigator.go_to_page(int(page_number), None if subpage_number is None else int(subpage_number))
        except Exception:
            success = False
        if success:
            if hasattr(window, '_render_current_selection'):
                window._render_current_selection()
            return
        QtCore.QTimer.singleShot(
            120,
            lambda: self._schedule_source_preview_navigation(window, page_number, subpage_number, remaining_attempts - 1),
        )

    def _show_source_preview_window(self, entries, source_name, page_number=None, subpage_number=None):
        if not entries:
            return
        try:
            from teletext.gui import viewer as viewer_module
        except Exception as exc:  # pragma: no cover - GUI path
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return
        if getattr(viewer_module, 'IMPORT_ERROR', None) is not None:
            QtWidgets.QMessageBox.warning(
                self,
                EDITOR_APP_NAME,
                f'Qt teletext viewer is not available. ({viewer_module.IMPORT_ERROR})',
            )
            return
        temp_handle = tempfile.NamedTemporaryFile(prefix='t42editor-preview-', suffix='.t42', delete=False)
        temp_handle.close()
        temp_path = temp_handle.name
        write_t42_entries(entries, temp_path)
        self._source_preview_temp_paths.add(temp_path)
        window = viewer_module.TeletextViewerWindow(filename=temp_path)
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        if page_number is not None:
            self._schedule_source_preview_navigation(window, page_number, subpage_number)
        if source_name:
            window.setWindowTitle(f'Teletext Preview - {os.path.basename(source_name)}')
        window.destroyed.connect(
            lambda _obj=None, current_window=window, current_path=temp_path: self._cleanup_source_preview_window(current_window, current_path)
        )
        self._source_preview_windows.append(window)
        window.show()
        window.raise_()
        window.activateWindow()

    def _show_source_dialog(self):
        dialog = self._ensure_source_dialog()
        target_page_number, target_subpage_number, target_row_number = self._prepare_import_target()
        dialog.configure(
            apply_page_callback=self._apply_imported_page,
            apply_subpage_callback=self._apply_imported_subpage,
            add_row_callback=self._add_imported_row,
            preview_callback=self._show_source_preview_window,
            default_page_number=target_page_number,
            default_subpage_number=target_subpage_number,
            default_row_number=target_row_number,
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_import_dialog(self):
        if not self._ensure_editable_current_subpage():
            return
        self._show_source_dialog()

    def _apply_imported_page(self, source_entries, source_page_number, target_page_number):
        page_entries = collect_page_entries(source_entries, source_page_number)
        if not page_entries:
            return
        self._mark_modified_page(target_page_number)
        for entry in page_entries:
            if entry.subpage_number is not None:
                self._mark_modified_subpage(target_page_number, int(entry.subpage_number))
        updated_entries = replace_page_in_entries(
            self._entries,
            page_entries,
            target_page_number=target_page_number,
        )
        self._rebuild_from_entries(updated_entries, focus_page_number=int(target_page_number), record_history=True)
        self.statusBar().showMessage(f'Imported page {self._page_label(target_page_number)}.', 4000)

    def _apply_imported_subpage(
        self,
        source_entries,
        source_page_number,
        source_subpage_number,
        target_page_number,
        target_subpage_number,
    ):
        subpage_entries = collect_subpage_entries(source_entries, source_page_number, source_subpage_number)
        if not subpage_entries:
            return
        target_page_number = int(target_page_number)
        target_subpage_number = int(target_subpage_number)
        if (
            self._current_page_number is not None
            and self._current_subpage_number is not None
            and target_page_number == int(self._current_page_number)
            and target_subpage_number == int(self._current_subpage_number)
        ):
            packets = (
                Packet(entry.raw, number=index)
                for index, entry in enumerate(subpage_entries)
            )
            service = Service.from_packets(packets)
            navigator = ServiceNavigator(service)
            imported_subpage = navigator.subpage(int(source_page_number), int(source_subpage_number))
            self._editor_loading = True
            try:
                self._page_option_page_input.setText(self._page_label(target_page_number)[1:])
                self._page_option_subpage_input.setText(f'{target_subpage_number:04X}')
                self._load_page_flags_from_subpage(imported_subpage)
                self._load_fastext_from_subpage(imported_subpage)
                self._editor_row_presence = {0: True}
                for row in range(1, 25):
                    self._editor_row_presence[row] = bool(imported_subpage.has_packet(row))
                for row in range(25):
                    row_bytes = self._normalize_row_bytes(row, self._raw_row_bytes_from_subpage(imported_subpage, row))
                    self._editor_live_bytes[row] = bytes(row_bytes)
                    self._refresh_editor_row_display(row)
            finally:
                self._editor_loading = False
            self._select_editor_row_from_preview(1, start_edit=False)
            self._set_preview_cursor(1, 0)
            self._update_editor_dirty_state(True)
            self._render_editor_preview()
            self._record_editor_snapshot()
            self.statusBar().showMessage(
                f'Imported subpage into current editor: {self._page_label(target_page_number)} / {target_subpage_number:04X}.',
                4000,
            )
            return
        self._mark_modified_subpage(target_page_number, target_subpage_number)
        updated_entries = replace_subpage_in_entries(
            self._entries,
            subpage_entries,
            target_page_number=target_page_number,
            target_subpage_number=target_subpage_number,
        )
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=target_page_number,
            focus_subpage_number=target_subpage_number,
            record_history=True,
        )
        self.statusBar().showMessage(
            f'Imported subpage {self._page_label(target_page_number)} / {target_subpage_number:04X}.',
            4000,
        )

    def _add_imported_row(
        self,
        source_entries,
        source_page_number,
        source_subpage_number,
        source_row_number,
        target_page_number,
        target_subpage_number,
        target_row_number,
    ):
        source_row_entries = collect_row_entries(
            source_entries,
            source_page_number,
            source_subpage_number,
            source_row_number,
        )
        if not source_row_entries:
            QtWidgets.QMessageBox.warning(
                self,
                EDITOR_APP_NAME,
                f'Source subpage does not contain row {int(source_row_number)}.',
            )
            return
        source_entry = source_row_entries[0]
        target_page_number = int(target_page_number)
        target_subpage_number = int(target_subpage_number)
        target_row_number = int(target_row_number)
        if (
            self._current_page_number is not None
            and self._current_subpage_number is not None
            and target_page_number == int(self._current_page_number)
            and target_subpage_number == int(self._current_subpage_number)
        ):
            packet = Packet(source_entry.raw, 0)
            if target_row_number == 0:
                row_bytes = bytes(packet.header.displayable.bytes_no_parity)
            else:
                row_bytes = bytes(packet.displayable.bytes_no_parity)
                self._editor_row_presence[target_row_number] = True
            self._editor_live_bytes[target_row_number] = self._normalize_row_bytes(target_row_number, row_bytes)
            self._refresh_editor_row_display(target_row_number)
            self._select_editor_row_from_preview(target_row_number, start_edit=False)
            self._set_preview_cursor(target_row_number, 0 if target_row_number > 0 else 8)
            self._update_editor_dirty_state(True)
            self._render_editor_preview()
            self._record_editor_snapshot()
            self.statusBar().showMessage(
                f'Imported row {int(source_row_number):02d} into current editor row {target_row_number:02d}.',
                4000,
            )
            return
        source_header_entry = next((
            entry for entry in collect_subpage_entries(source_entries, source_page_number, source_subpage_number)
            if entry.row == 0
        ), None)
        try:
            updated_entries = add_row_to_subpage_entries(
                self._entries,
                source_entry,
                target_page_number,
                target_subpage_number,
                target_row_number,
                source_header_entry=source_header_entry,
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, str(exc))
            return
        self._mark_modified_subpage(target_page_number, target_subpage_number)
        self._rebuild_from_entries(
            updated_entries,
            focus_page_number=target_page_number,
            focus_subpage_number=target_subpage_number,
            record_history=True,
        )
        self._select_editor_row_from_preview(target_row_number, start_edit=False)
        self._set_preview_cursor(target_row_number, 0 if target_row_number > 0 else 8)
        self.statusBar().showMessage(
            f'Imported row {int(source_row_number):02d} into {self._page_label(target_page_number)} / {int(target_subpage_number):04X} row {int(target_row_number):02d}.',
            4000,
        )

    def open_dialog(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            'Open T42 file',
            os.path.dirname(self._filename) if self._filename else os.getcwd(),
            'Teletext Files (*.t42);;All Files (*)',
        )
        if filename:
            self.open_file(filename)

    def open_file(self, filename):
        filename = os.path.abspath(filename)
        if self._loader is not None and self._loader.isRunning():
            return
        self._filename = filename
        self._entries = ()
        self._service = None
        self._navigator = None
        self._page_summary = ()
        self._modified_pages.clear()
        self._modified_subpages.clear()
        self._thumbnail_cache.clear()
        self._thumbnail_queue = deque()
        self._thumbnail_total = 0
        self._thumbnail_timer.stop()
        self._tree.clear()
        self._tree_status_label.hide()
        self._set_loaded_state(False)
        self._clear_decoder()
        self._set_document_caption()
        self.statusBar().showMessage(f'Loading {os.path.basename(filename)}...')
        self._progress.reset()
        self._progress.setVisible(True)

        self._loader = T42EditorLoader(filename)
        self._loader.progress.connect(self._loading_progress)
        self._loader.failed.connect(self._loading_failed)
        self._loader.loaded.connect(self._loading_done)
        self._loader.finished.connect(self._loading_finished)
        self._loader.start()

    def _loading_progress(self, current, total, elapsed):
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(current)
            message = f'Loading {os.path.basename(self._filename)}... {current}/{total}'
            if current and elapsed > 0:
                rate = current / elapsed
                if rate > 0 and current < total:
                    remaining = max(0, int(round((total - current) / rate)))
                    minutes, seconds = divmod(remaining, 60)
                    message += f' | left {minutes:02d}:{seconds:02d}'
            self.statusBar().showMessage(message)

    def _loading_failed(self, message):
        self._entries = ()
        self._service = None
        self._navigator = None
        self._page_summary = ()
        self._modified_pages.clear()
        self._modified_subpages.clear()
        self._enabled_subpage_occurrences.clear()
        self._editor_drafts.clear()
        self._progress.setVisible(False)
        self._set_loaded_state(False)
        self._clear_decoder()
        QtWidgets.QMessageBox.critical(self, EDITOR_APP_NAME, message)
        self.statusBar().showMessage(message)

    def _loading_done(self, filename, entries, service, page_summary):
        self._filename = filename
        self._entries = tuple(entries)
        self._service = service
        self._navigator = ServiceNavigator(service, raw_entries=self._entries)
        self._apply_hidden_subpages_mode_to_navigator()
        self._page_summary = tuple(page_summary)
        self._modified_pages.clear()
        self._modified_subpages.clear()
        self._editor_drafts.clear()
        self._sync_enabled_subpage_occurrences(self._entries, preserve=False)
        self._set_document_caption()
        self._set_loaded_state(True)
        self._rebuild_tree()
        self._seed_document_history()
        self.statusBar().showMessage(
            f'Loaded {os.path.basename(filename)} | pages {len(self._page_summary)} | packets {len(self._entries)}',
            5000,
        )

    def _loading_finished(self):
        self._progress.setVisible(False)
        if self._loader is not None:
            self._loader.deleteLater()
            self._loader = None

    def _page_label(self, page_number):
        magazine, page = ServiceNavigator.split_page_number(page_number)
        return f'P{magazine}{page:02X}'

    def _filter_matches(self, page_summary, subpage_summary, query):
        page_label = self._page_label(page_summary['page_number']).upper()
        page_title = str(page_summary.get('header_title') or '').upper()
        subpage_label = f"{int(subpage_summary['subpage_number']):04X}"
        subpage_title = str(subpage_summary.get('header_title') or '').upper()
        return (
            query in page_label
            or query in page_title
            or query in subpage_label
            or query in subpage_title
        )

    def _current_tree_key(self):
        item = self._tree.currentItem()
        if item is None:
            return None
        return (
            item.data(0, QtCore.Qt.UserRole),
            item.data(0, QtCore.Qt.UserRole + 1),
            item.data(0, QtCore.Qt.UserRole + 2),
            item.data(0, QtCore.Qt.UserRole + 3),
        )

    def _restore_tree_selection(self, key):
        if key is None:
            return
        if len(key) >= 4:
            target_type, page_number, subpage_number, occurrence_number = key[:4]
        else:
            target_type, page_number, subpage_number = key
            occurrence_number = 1
        for page_index in range(self._tree.topLevelItemCount()):
            page_item = self._tree.topLevelItem(page_index)
            if (
                target_type == 'page'
                and page_item.data(0, QtCore.Qt.UserRole) == 'page'
                and page_item.data(0, QtCore.Qt.UserRole + 1) == page_number
            ):
                self._tree.setCurrentItem(page_item)
                return
            for child_index in range(page_item.childCount()):
                child = page_item.child(child_index)
                if (
                    target_type == 'subpage'
                    and child.data(0, QtCore.Qt.UserRole) == 'subpage'
                    and child.data(0, QtCore.Qt.UserRole + 1) == page_number
                    and child.data(0, QtCore.Qt.UserRole + 2) == subpage_number
                    and int(child.data(0, QtCore.Qt.UserRole + 3) or 1) == int(occurrence_number or 1)
                ):
                    self._tree.setCurrentItem(child)
                    return

    def _iter_tree_items(self):
        for page_index in range(self._tree.topLevelItemCount()):
            page_item = self._tree.topLevelItem(page_index)
            yield page_item
            for child_index in range(page_item.childCount()):
                yield page_item.child(child_index)

    def _clear_tree_icons(self):
        empty_icon = QtGui.QIcon()
        for item in self._iter_tree_items():
            item.setIcon(0, empty_icon)
            item.setData(0, QtCore.Qt.DecorationRole, None)
        self._tree.viewport().update()

    def _queue_tree_thumbnails(self):
        self._thumbnail_timer.stop()
        self._thumbnail_queue = deque()
        self._thumbnail_total = 0
        for item in self._iter_tree_items():
            page_number = item.data(0, QtCore.Qt.UserRole + 1)
            subpage_number = item.data(0, QtCore.Qt.UserRole + 2)
            occurrence_number = item.data(0, QtCore.Qt.UserRole + 3)
            if page_number is None or subpage_number is None:
                continue
            self._thumbnail_queue.append((item, int(page_number), int(subpage_number), int(occurrence_number or 1)))
        self._thumbnail_total = len(self._thumbnail_queue)

    def _rebuild_tree(self, selection_key=None):
        selected_key = selection_key if selection_key is not None else self._current_tree_key()
        query = self._filter_input.text().strip().upper()
        previews_enabled = bool(self._preview_toggle.isChecked())
        all_occurrences = self._all_page_subpage_occurrences_for_entries()
        self._thumbnail_timer.stop()
        self._thumbnail_queue = deque()
        self._thumbnail_total = 0
        self._tree_item_change_locked = True
        self._tree.setUpdatesEnabled(False)
        self._tree.clear()

        try:
            for page_summary in self._page_summary:
                subpages = tuple(page_summary.get('subpages') or ())
                occurrences = all_occurrences.get(int(page_summary['page_number']), {})
                if query:
                    page_label = self._page_label(page_summary['page_number']).upper()
                    page_title = str(page_summary.get('header_title') or '').upper()
                    page_match = query in page_label or query in page_title
                    visible_subpages = []
                    for subpage_summary in subpages:
                        if not (page_match or self._filter_matches(page_summary, subpage_summary, query)):
                            continue
                        visible_subpages.append(subpage_summary)
                    if not page_match and not visible_subpages:
                        continue
                    visible_subpages = tuple(visible_subpages)
                else:
                    visible_subpages = subpages

                page_item = QtWidgets.QTreeWidgetItem([
                    f"{self._page_label(page_summary['page_number'])}{self._modified_entry_marker(page_summary['page_number'])}",
                    str(page_summary['packet_count']),
                    str(int(page_summary.get('first_packet', 0))),
                    f"{self._page_label(page_summary['page_number'])}/*/{len(visible_subpages)}",
                    str(page_summary.get('header_title') or ''),
                ])
                if self._tree_checkboxes_enabled():
                    page_item.setFlags(page_item.flags() | QtCore.Qt.ItemIsUserCheckable)
                page_item.setData(0, QtCore.Qt.UserRole, 'page')
                page_item.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                first_subpage_number = None
                if visible_subpages:
                    first_subpage_number = int(visible_subpages[0]['subpage_number'])
                page_item.setData(0, QtCore.Qt.UserRole + 2, first_subpage_number)
                page_item.setData(0, QtCore.Qt.UserRole + 3, 1)
                page_item.setToolTip(4, str(page_summary.get('header_title') or ''))
                self._tree.addTopLevelItem(page_item)

                page_occurrence_keys = []
                if previews_enabled and first_subpage_number is not None:
                    self._thumbnail_queue.append((page_item, int(page_summary['page_number']), first_subpage_number, 1))

                for subpage_summary in visible_subpages:
                    subpage_number = int(subpage_summary['subpage_number'])
                    variants = occurrences.get(subpage_number) or ({
                        'label': f'{subpage_number:04X}',
                        'occurrence': 1,
                        'header_title': str(subpage_summary.get('header_title') or ''),
                    },)
                    if not self._show_hidden_subpages_toggle.isChecked():
                        variants = variants[:1]
                    for variant in variants:
                        occurrence_number = int(variant.get('occurrence') or 1)
                        packet_count = int(variant.get('packet_count') or subpage_summary['packet_count'])
                        first_packet = int(variant.get('first_packet') or subpage_summary.get('first_packet', 0))
                        page_occurrence_keys.append((int(page_summary['page_number']), subpage_number, occurrence_number))
                        child = QtWidgets.QTreeWidgetItem([
                            f"{variant['label']}{self._modified_entry_marker(page_summary['page_number'], subpage_number)}",
                            str(packet_count),
                            str(first_packet),
                            f"{self._page_label(page_summary['page_number'])}/{subpage_number:04X}/{occurrence_number}",
                            str(variant.get('header_title') or subpage_summary.get('header_title') or ''),
                        ])
                        if self._tree_checkboxes_enabled():
                            child.setFlags(child.flags() | QtCore.Qt.ItemIsUserCheckable)
                        child.setData(0, QtCore.Qt.UserRole, 'subpage')
                        child.setData(0, QtCore.Qt.UserRole + 1, int(page_summary['page_number']))
                        child.setData(0, QtCore.Qt.UserRole + 2, subpage_number)
                        child.setData(0, QtCore.Qt.UserRole + 3, occurrence_number)
                        child.setToolTip(4, str(variant.get('header_title') or subpage_summary.get('header_title') or ''))
                        if self._tree_checkboxes_enabled():
                            child.setCheckState(
                                0,
                                QtCore.Qt.Checked if self._is_subpage_occurrence_enabled(
                                    int(page_summary['page_number']),
                                    subpage_number,
                                    occurrence_number,
                                ) else QtCore.Qt.Unchecked,
                            )
                        page_item.addChild(child)
                        if previews_enabled:
                            self._thumbnail_queue.append((
                                child,
                                int(page_summary['page_number']),
                                subpage_number,
                                occurrence_number,
                            ))

                enabled_count = sum(
                    1
                    for key in page_occurrence_keys
                    if key in self._enabled_subpage_occurrences
                )
                if self._tree_checkboxes_enabled():
                    if page_occurrence_keys and enabled_count == len(page_occurrence_keys):
                        page_item.setCheckState(0, QtCore.Qt.Checked)
                    elif enabled_count == 0:
                        page_item.setCheckState(0, QtCore.Qt.Unchecked)
                    else:
                        page_item.setCheckState(0, QtCore.Qt.PartiallyChecked)
                page_item.setText(3, f"{self._page_label(page_summary['page_number'])}/*/{len(page_occurrence_keys) or len(visible_subpages)}")
                page_item.setExpanded(
                    bool(query)
                    or (
                        selected_key is not None
                        and len(selected_key) > 1
                        and int(selected_key[1]) == int(page_summary['page_number'])
                    )
                    or self._tree.topLevelItemCount() == 1
                )
        finally:
            self._tree_item_change_locked = False
            self._tree.setUpdatesEnabled(True)

        self._thumbnail_total = len(self._thumbnail_queue)
        self._restore_tree_selection(selected_key)
        if self._tree.topLevelItemCount() and self._tree.currentItem() is None:
            self._tree.setCurrentItem(self._tree.topLevelItem(0))
        self._render_current_selection()
        self._refresh_thumbnail_generation()

    def _tree_item_activated(self, item, _column):
        self._tree.setCurrentItem(item)
        self._render_current_selection()

    def _update_page_tree_item_check_state(self, page_item):
        if page_item is None or not self._tree_checkboxes_enabled():
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

    def _tree_item_changed(self, item, column):
        if item is None or int(column) != 0 or self._tree_item_change_locked or not self._tree_checkboxes_enabled():
            return
        item_type = item.data(0, QtCore.Qt.UserRole)
        if item_type == 'page':
            page_number = item.data(0, QtCore.Qt.UserRole + 1)
            if page_number is None:
                return
            checked = item.checkState(0) != QtCore.Qt.Unchecked
            updated = set(self._enabled_subpage_occurrences)
            available = self._all_subpage_occurrence_keys()
            for key in available:
                if int(key[0]) != int(page_number):
                    continue
                if checked:
                    updated.add(key)
                else:
                    updated.discard(key)
            self._enabled_subpage_occurrences = updated
            self._record_document_snapshot(selection_key=self._current_tree_key())
            self._tree_item_change_locked = True
            try:
                for child_index in range(item.childCount()):
                    child = item.child(child_index)
                    child.setCheckState(0, QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
            finally:
                self._tree_item_change_locked = False
            self.statusBar().showMessage(
                f'{"Enabled" if checked else "Disabled"} {self._page_label(int(page_number))} for main T42 save.',
                2500,
            )
            return
        if item_type != 'subpage':
            return
        page_number = item.data(0, QtCore.Qt.UserRole + 1)
        subpage_number = item.data(0, QtCore.Qt.UserRole + 2)
        occurrence_number = item.data(0, QtCore.Qt.UserRole + 3)
        if page_number is None or subpage_number is None:
            return
        key = (int(page_number), int(subpage_number), max(int(occurrence_number or 1), 1))
        updated = set(self._enabled_subpage_occurrences)
        checked = item.checkState(0) == QtCore.Qt.Checked
        if checked:
            updated.add(key)
        else:
            updated.discard(key)
        self._enabled_subpage_occurrences = updated
        self._record_document_snapshot(selection_key=self._current_tree_key())
        self._tree_item_change_locked = True
        try:
            self._update_page_tree_item_check_state(item.parent())
        finally:
            self._tree_item_change_locked = False
        self.statusBar().showMessage(
            f'{"Enabled" if checked else "Disabled"} {self._page_label(int(page_number))} / {int(subpage_number):04X}'
            f' ({int(occurrence_number or 1)}) for main T42 save.',
            2500,
        )

    def _tree_selection_changed(self):
        if self._tree_selection_locked:
            return
        if self._current_page_number is not None and self._current_subpage_number is not None:
            self._store_current_editor_draft()
        self._render_current_selection()

    def _selected_page_subpage(self):
        item = self._tree.currentItem()
        if item is None:
            return None, None, None
        item_type = item.data(0, QtCore.Qt.UserRole)
        page_number = item.data(0, QtCore.Qt.UserRole + 1)
        subpage_number = item.data(0, QtCore.Qt.UserRole + 2)
        occurrence_number = int(item.data(0, QtCore.Qt.UserRole + 3) or 1)
        if item_type == 'page':
            return int(page_number), None if subpage_number is None else int(subpage_number), 1
        if item_type == 'subpage':
            return int(page_number), int(subpage_number), occurrence_number
        return None, None, None

    def _render_current_selection(self):
        if self._navigator is None:
            self._clear_decoder()
            return
        page_number, selected_subpage_number, selected_occurrence_number = self._selected_page_subpage()
        if page_number is None:
            self._clear_decoder()
            return
        try:
            self._resolve_subpage_variant(page_number, selected_subpage_number, selected_occurrence_number)
            subpage_numbers = sorted(self._navigator._page(page_number).subpages)  # noqa: SLF001
        except Exception:
            self._clear_decoder()
            return

        current_subpage_number = selected_subpage_number
        if current_subpage_number is None:
            current_subpage_number = int(subpage_numbers[0])
        current_occurrence_number = max(int(selected_occurrence_number or 1), 1)

        self._decoder.showallsymbols = self._all_symbols_toggle.isChecked()
        self._decoder.reveal = self._all_symbols_toggle.isChecked()
        self._decoder.showgrid = self._show_grid_toggle.isChecked()
        self._decoder.crteffect = self._crt_toggle.isChecked()
        self._decoder.showcontrolcodes = self._show_control_codes_toggle.isChecked()
        self._decoder.doubleheight = not self._single_height_toggle.isChecked()
        self._decoder.doublewidth = not self._single_width_toggle.isChecked()
        self._decoder.flashenabled = not self._no_flash_toggle.isChecked()
        self._decoder.horizontalscale = 1.15 if self._widescreen_toggle.isChecked() else 0.95
        self._decoder.language = self._current_language_key()
        self._paint_decoder(self._decoder, page_number, current_subpage_number, current_occurrence_number)
        self._sync_preview_stage()

        position = subpage_numbers.index(current_subpage_number) + 1
        total = len(subpage_numbers)
        selection_text = (
            f'Page: {self._page_label(page_number)}   '
            f'Subpage: {position:02d}/{total:02d} ({current_subpage_number:04X})'
        )
        if current_occurrence_number > 1:
            selection_text += f'   Hidden: ({current_occurrence_number})'
        self._selection_label.setText(selection_text)
        self._current_page_number = int(page_number)
        self._current_subpage_number = int(current_subpage_number)
        self._current_subpage_occurrence = current_occurrence_number
        self._load_editor_for_subpage(
            self._current_page_number,
            self._current_subpage_number,
            self._current_subpage_occurrence,
        )

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

    def _make_thumbnail_icon(self, page_number, subpage_number, occurrence_number=1):
        key = (
            int(page_number),
            int(subpage_number),
            int(occurrence_number or 1),
            self._all_symbols_toggle.isChecked(),
            self._show_control_codes_toggle.isChecked(),
            self._single_height_toggle.isChecked(),
            self._single_width_toggle.isChecked(),
            self._no_flash_toggle.isChecked(),
            self._widescreen_toggle.isChecked(),
            self._current_language_key(),
        )
        icon = self._thumbnail_cache.get(key)
        if icon is not None:
            return icon
        try:
            self._ensure_preview_renderer()
            self._preview_decoder.zoom = 1
            self._preview_decoder.showallsymbols = self._all_symbols_toggle.isChecked()
            self._preview_decoder.reveal = self._all_symbols_toggle.isChecked()
            self._preview_decoder.showcontrolcodes = self._show_control_codes_toggle.isChecked()
            self._preview_decoder.showgrid = False
            self._preview_decoder.crteffect = False
            self._preview_decoder.doubleheight = not self._single_height_toggle.isChecked()
            self._preview_decoder.doublewidth = not self._single_width_toggle.isChecked()
            self._preview_decoder.flashenabled = not self._no_flash_toggle.isChecked()
            self._preview_decoder.horizontalscale = 1.15 if self._widescreen_toggle.isChecked() else 0.95
            self._preview_decoder.language = self._current_language_key()
            self._paint_decoder(
                self._preview_decoder,
                int(page_number),
                int(subpage_number),
                int(occurrence_number or 1),
            )
            self._preview_widget.setFixedSize(self._preview_decoder.size())
            self._preview_widget.show()
            QtWidgets.QApplication.processEvents()
            if hasattr(self._preview_widget, 'grabFramebuffer'):
                pixmap = QtGui.QPixmap.fromImage(self._preview_widget.grabFramebuffer())
            else:
                pixmap = self._preview_widget.grab()
            self._preview_widget.hide()
            pixmap = pixmap.scaled(
                self._tree.iconSize(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.FastTransformation,
            )
        except Exception:  # pragma: no cover - GUI fallback path
            return None
        icon = QtGui.QIcon(pixmap)
        self._thumbnail_cache[key] = icon
        return icon

    def _refresh_thumbnail_generation(self):
        if not self._preview_toggle.isChecked():
            self._thumbnail_timer.stop()
            self._thumbnail_queue = deque()
            self._thumbnail_total = 0
            self._clear_tree_icons()
            self._tree_status_label.hide()
            return
        if not self._thumbnail_queue:
            self._tree_status_label.hide()
            return
        self._tree_status_label.setText(f'Loading previews 0/{self._thumbnail_total}')
        self._tree_status_label.show()
        self._thumbnail_timer.start()

    def _populate_thumbnail_batch(self):
        if not self._preview_toggle.isChecked():
            self._thumbnail_queue = deque()
            self._thumbnail_total = 0
            self._thumbnail_timer.stop()
            self._tree_status_label.hide()
            return
        if not self.isVisible():
            self._thumbnail_timer.stop()
            return
        processed = 0
        while processed < 8 and self._thumbnail_queue:
            item, page_number, subpage_number, occurrence_number = self._thumbnail_queue.popleft()
            icon = self._make_thumbnail_icon(page_number, subpage_number, occurrence_number)
            if icon is not None:
                item.setIcon(0, icon)
            processed += 1
        loaded = self._thumbnail_total - len(self._thumbnail_queue)
        if self._thumbnail_total:
            self._tree_status_label.setText(f'Loading previews {loaded}/{self._thumbnail_total}')
            self._tree_status_label.show()
        if not self._thumbnail_queue:
            self._thumbnail_timer.stop()
            self._tree_status_label.hide()

    def _select_editor_row_from_preview(self, row, start_edit=False):
        if row < 0 or row > 24:
            return
        self._editor_table.selectRow(row)
        item = self._editor_table.item(row, 0)
        if item is None:
            return
        self._editor_table.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
        self._editor_table.setCurrentItem(item)
        if start_edit and (row != 0 or self._allow_header_edit_toggle.isChecked()):
            self._editor_table.setFocus(QtCore.Qt.MouseFocusReason)
            self._editor_table.editItem(item)
        self._update_current_history_cursor_state()

    def _preview_cell_at(self, watched, event):
        decoder_widget = getattr(self, '_decoder_widget', None)
        preview_stage = getattr(self, '_preview_stage', None)
        if decoder_widget is None:
            return None, None
        if watched is preview_stage:
            origin = decoder_widget.mapTo(preview_stage, QtCore.QPoint(0, 0))
            x = int(event.pos().x() - origin.x())
            y = int(event.pos().y() - origin.y())
        else:
            x = int(event.pos().x())
            y = int(event.pos().y())
        left, top, cell_width, cell_height = self._preview_grid_metrics()
        inner_x = x - left
        inner_y = y - top
        if not (0 <= inner_x < cell_width * 40 and 0 <= inner_y < cell_height * 25):
            return None, None
        row = max(0, min(int(inner_y / float(cell_height)), 24))
        col = max(0, min(int(inner_x / float(cell_width)), 39))
        return row, col

    def _show_preview_control_code_menu(self, global_pos):
        if not self._ensure_editable_current_subpage():
            return
        row = self._preview_cursor_row
        col = self._preview_cursor_col
        if row is None or col is None:
            return
        if row == 0 and col < 8:
            self.statusBar().showMessage('Header prefix is not editable.', 3000)
            return
        menu = QtWidgets.QMenu(self)
        for section_title, actions in CONTROL_CODE_MENU:
            section_menu = menu.addMenu(section_title)
            for label, code in actions:
                action = section_menu.addAction(label)
                action.triggered.connect(
                    lambda checked=False, value=code: self._apply_preview_byte(value, control_code=True)
                )
        menu.addSeparator()
        clear_action = menu.addAction('Clear Cell')
        clear_action.triggered.connect(lambda: self._apply_preview_byte(0x20, advance=False))
        menu.exec_(global_pos)

    def eventFilter(self, watched, event):  # pragma: no cover - GUI interaction path
        decoder_widget = getattr(self, '_decoder_widget', None)
        preview_stage = getattr(self, '_preview_stage', None)
        editor_table = getattr(self, '_editor_table', None)
        tree = getattr(self, '_tree', None)
        allow_header_toggle = getattr(self, '_allow_header_edit_toggle', None)
        if watched is tree and tree is not None and event.type() == QtCore.QEvent.KeyPress:
            if event.key() == QtCore.Qt.Key_Delete:
                item = tree.currentItem()
                if item is not None:
                    if item.data(0, QtCore.Qt.UserRole) == 'page':
                        self.delete_current_page()
                    else:
                        self.delete_current_subpage()
                    return True
        if (
            watched in (decoder_widget, preview_stage)
            and decoder_widget is not None
            and editor_table is not None
            and allow_header_toggle is not None
        ):
            if event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                row, col = self._preview_cell_at(watched, event)
                if row is not None and col is not None:
                    self._set_preview_cursor(row, col)
                    self._select_editor_row_from_preview(
                        row,
                        start_edit=(event.type() == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton),
                    )
                    if watched is preview_stage:
                        preview_stage.setFocus(QtCore.Qt.MouseFocusReason)
                    elif watched is decoder_widget:
                        decoder_widget.setFocus(QtCore.Qt.MouseFocusReason)
                    if self._mouse_draw_toggle.isChecked() and event.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
                        target = self._preview_mosaic_target(watched, event)
                        if target is not None:
                            draw_row, draw_col, draw_bit = target
                            if self._start_mouse_draw(
                                draw_row,
                                draw_col,
                                draw_bit,
                                erase=(event.button() == QtCore.Qt.RightButton),
                            ):
                                return True
                    if event.button() == QtCore.Qt.RightButton:
                        self._stop_mouse_draw()
                        self._show_preview_control_code_menu(event.globalPos())
                        return True
            elif event.type() == QtCore.QEvent.MouseMove:
                if (
                    self._mouse_draw_active
                    and event.buttons() & (QtCore.Qt.LeftButton | QtCore.Qt.RightButton)
                ):
                    target = self._preview_mosaic_target(watched, event)
                    if target is not None:
                        row, col, bit = target
                        self._apply_mouse_draw_target(row, col, bit)
                        return True
            elif event.type() == QtCore.QEvent.MouseButtonRelease:
                if event.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
                    self._stop_mouse_draw()
            elif event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                if key == QtCore.Qt.Key_Left:
                    self._move_preview_cursor(delta_col=-1)
                    return True
                if key == QtCore.Qt.Key_Right:
                    self._move_preview_cursor(delta_col=1)
                    return True
                if key == QtCore.Qt.Key_Up:
                    self._move_preview_cursor(delta_row=-1)
                    return True
                if key == QtCore.Qt.Key_Down:
                    self._move_preview_cursor(delta_row=1)
                    return True
                if key == QtCore.Qt.Key_Backspace:
                    self._clear_preview_cell(move_back=True)
                    return True
                if key == QtCore.Qt.Key_Delete:
                    self._clear_preview_cell(move_back=False)
                    return True
                if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    self._move_preview_cursor(delta_row=1)
                    return True
                text = event.text()
                if text and not event.modifiers() & QtCore.Qt.ControlModifier:
                    if self._preview_insert_text(text):
                        return True
            elif event.type() == QtCore.QEvent.Wheel:
                if event.modifiers() & QtCore.Qt.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta:
                        updated = round(self._zoom_box.value() + (0.1 if delta > 0 else -0.1), 1)
                        updated = max(self._zoom_box.minimum(), min(self._zoom_box.maximum(), updated))
                        self._zoom_box.setValue(updated)
                        return True
        return super().eventFilter(watched, event)

    def _run_progress_task(self, title, label, task):
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
            result = task(report)
        finally:
            progress.setValue(progress.maximum())
            progress.close()
        return result

    def _suggest_output_path(self, filename):
        base_dir = os.path.dirname(self._filename) if self._filename else os.getcwd()
        return os.path.join(base_dir, filename)

    def _current_screenshot_pixmap(self):
        stage = getattr(self, '_preview_stage', None)
        if stage is None:
            return QtGui.QPixmap()
        return stage.grab()

    def _suggest_screenshot_path(self):
        base_directory = os.path.dirname(self._filename) if self._filename else os.getcwd()
        base_name = os.path.splitext(os.path.basename(self._filename or 'teletext'))[0]
        page_label = self._page_label(self._current_page_number)[1:] if self._current_page_number is not None else '100'
        subpage_label = f'-{int(self._current_subpage_number):04X}' if self._current_subpage_number is not None else ''
        occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        occurrence_suffix = f'-occ{occurrence_number}' if occurrence_number > 1 else ''
        return os.path.join(base_directory, f'{base_name}-{page_label}{subpage_label}{occurrence_suffix}.png')

    def _suggest_screenshot_name(self):
        return os.path.basename(self._suggest_screenshot_path())

    def save_screenshot(self):
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Save screenshot',
            self._suggest_screenshot_path(),
            'PNG Image (*.png)',
        )
        if not filename:
            return
        if not filename.lower().endswith('.png'):
            filename += '.png'
        pixmap = self._current_screenshot_pixmap()
        if pixmap.isNull():
            return
        if pixmap.save(filename, 'PNG'):
            self.statusBar().showMessage(f'Screenshot saved to {filename}', 5000)
        else:  # pragma: no cover - GUI path
            QtWidgets.QMessageBox.warning(self, EDITOR_APP_NAME, f'Could not save screenshot to {filename}.')

    def copy_screenshot(self):
        pixmap = self._current_screenshot_pixmap()
        if pixmap.isNull():
            return
        mime = QtCore.QMimeData()
        mime.setImageData(pixmap.toImage())
        mime.setText(self._suggest_screenshot_name())
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage('Screenshot copied to clipboard.', 2500)

    def save_file(self):
        if not self._filename:
            self.save_file_as()
            return
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        target = self._filename or self._suggest_output_path('teletext.t42')
        entries = tuple(self._entries)
        self._run_progress_task(
            'Save T42',
            'Writing packets',
            lambda report: write_t42_entries(entries, target, progress_callback=report),
        )
        self._filename = target
        self._set_document_caption()
        self.statusBar().showMessage(f'Saved {target}', 5000)

    def save_file_as(self):
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Save T42 file',
            self._suggest_output_path(os.path.basename(self._filename) if self._filename else 'teletext.t42'),
            'Teletext Files (*.t42)',
        )
        if not target:
            return
        if not target.lower().endswith('.t42'):
            target += '.t42'
        if self._editor_dirty and not self._editing_locked_for_current_subpage():
            self.apply_current_edits()
        entries = tuple(self._entries)
        self._run_progress_task(
            'Save T42',
            'Writing packets',
            lambda report: write_t42_entries(entries, target, progress_callback=report),
        )
        self._filename = target
        self._set_document_caption()
        self.statusBar().showMessage(f'Saved {target}', 5000)

    def save_current_page(self):
        if not self._entries or self._current_page_number is None:
            return
        default_name = f'{self._page_label(self._current_page_number)[1:]}.t42'
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Save current page',
            self._suggest_output_path(default_name),
            'Teletext Files (*.t42)',
        )
        if not target:
            return
        if not target.lower().endswith('.t42'):
            target += '.t42'
        page_entries = collect_page_entries(self._entries, self._current_page_number)
        self._run_progress_task(
            'Save Page',
            'Writing packets',
            lambda report: write_t42_entries(page_entries, target, progress_callback=report),
        )
        self.statusBar().showMessage(f'Saved page to {target}', 5000)

    def save_current_subpage(self):
        if not self._entries or self._current_page_number is None or self._current_subpage_number is None:
            return
        occurrence_number = max(int(getattr(self, '_current_subpage_occurrence', 1) or 1), 1)
        suffix = f'-occ{occurrence_number}' if occurrence_number > 1 else ''
        default_name = f'{self._page_label(self._current_page_number)[1:]}-{self._current_subpage_number:04X}{suffix}.t42'
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Save current subpage',
            self._suggest_output_path(default_name),
            'Teletext Files (*.t42)',
        )
        if not target:
            return
        if not target.lower().endswith('.t42'):
            target += '.t42'
        subpage_entries = collect_subpage_occurrence_entries(
            self._entries,
            self._current_page_number,
            self._current_subpage_number,
            occurrence_number,
        ) or collect_subpage_entries(
            self._entries,
            self._current_page_number,
            self._current_subpage_number,
        )
        self._run_progress_task(
            'Save Subpage',
            'Writing packets',
            lambda report: write_t42_entries(subpage_entries, target, progress_callback=report),
        )
        self.statusBar().showMessage(f'Saved subpage to {target}', 5000)

    def _ensure_split_dialog(self):
        if self._split_dialog is not None:
            return self._split_dialog
        from teletext.gui import viewer as viewer_module

        self._split_dialog = viewer_module.SplitExportDialog(self)
        self._split_dialog.single_t42_button.clicked.connect(self._export_selected_t42_from_dialog)
        self._split_dialog.single_html_button.clicked.connect(self._export_selected_html_from_dialog)
        self._split_dialog.current_t42_button.clicked.connect(self._export_current_t42_from_dialog)
        self._split_dialog.current_html_button.clicked.connect(self._export_current_html_from_dialog)
        self._split_dialog.export_all_button.clicked.connect(self._export_all_from_dialog)
        return self._split_dialog

    def show_split_dialog(self):
        if self._service is None or self._navigator is None:
            return
        dialog = self._ensure_split_dialog()
        dialog.set_current_selection(
            self._page_label(self._current_page_number)[1:] if self._current_page_number is not None else '100',
            self._current_subpage_number or 0,
        )
        base_dir = os.path.dirname(self._filename) if self._filename else os.getcwd()
        dialog.set_default_directories(
            os.path.join(base_dir, 't42'),
            os.path.join(base_dir, 'html'),
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _dialog_page_selection(self):
        dialog = self._ensure_split_dialog()
        page_number = dialog.single_page_number()
        subpage_number = dialog.single_subpage_number()
        return int(page_number), None if subpage_number is None else int(subpage_number)

    def _export_selected_t42_to_path(self, output_path, page_number, subpage_number):
        if subpage_number is None:
            entries = collect_page_entries(self._entries, page_number)
        else:
            entries = collect_subpage_entries(self._entries, page_number, subpage_number)
        self._run_progress_task(
            'Export T42',
            'Writing packets',
            lambda report: write_t42_entries(entries, output_path, progress_callback=report),
        )

    def _export_selected_t42_from_dialog(self):
        if self._service is None:
            return
        page_number, subpage_number = self._dialog_page_selection()
        suffix = '' if subpage_number is None else f'-{subpage_number:04X}'
        default_name = f'{self._page_label(page_number)[1:]}{suffix}.t42'
        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Export selected T42',
            self._suggest_output_path(default_name),
            'Teletext Files (*.t42)',
        )
        if not output_path:
            return
        if not output_path.lower().endswith('.t42'):
            output_path += '.t42'
        self._export_selected_t42_to_path(output_path, page_number, subpage_number)
        self.statusBar().showMessage(f'Exported {output_path}', 5000)

    def _export_selected_html_from_dialog(self):
        if self._service is None:
            return
        from teletext.gui import viewer as viewer_module

        dialog = self._ensure_split_dialog()
        page_number, subpage_number = self._dialog_page_selection()
        suffix = '' if subpage_number is None else f'-{subpage_number:04X}'
        default_name = f'{self._page_label(page_number)[1:]}{suffix}.html'
        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Export selected HTML',
            self._suggest_output_path(default_name),
            'HTML Files (*.html)',
        )
        if not output_path:
            return
        if not output_path.lower().endswith('.html'):
            output_path += '.html'
        viewer_module.export_selected_html(
            self._service,
            output_path,
            page_number,
            subpage_number=subpage_number,
            localcodepage=dialog.html_localcodepage(),
        )
        self.statusBar().showMessage(f'Exported {output_path}', 5000)

    def _export_current_t42_from_dialog(self):
        if self._current_page_number is None:
            return
        suffix = '' if self._current_subpage_number is None else f'-{self._current_subpage_number:04X}'
        default_name = f'{self._page_label(self._current_page_number)[1:]}{suffix}.t42'
        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Export current T42',
            self._suggest_output_path(default_name),
            'Teletext Files (*.t42)',
        )
        if not output_path:
            return
        if not output_path.lower().endswith('.t42'):
            output_path += '.t42'
        self._export_selected_t42_to_path(output_path, self._current_page_number, self._current_subpage_number)
        self.statusBar().showMessage(f'Exported {output_path}', 5000)

    def _export_current_html_from_dialog(self):
        if self._service is None or self._current_page_number is None:
            return
        from teletext.gui import viewer as viewer_module

        dialog = self._ensure_split_dialog()
        suffix = '' if self._current_subpage_number is None else f'-{self._current_subpage_number:04X}'
        default_name = f'{self._page_label(self._current_page_number)[1:]}{suffix}.html'
        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            'Export current HTML',
            self._suggest_output_path(default_name),
            'HTML Files (*.html)',
        )
        if not output_path:
            return
        if not output_path.lower().endswith('.html'):
            output_path += '.html'
        viewer_module.export_selected_html(
            self._service,
            output_path,
            self._current_page_number,
            subpage_number=self._current_subpage_number,
            localcodepage=dialog.html_localcodepage(),
        )
        self.statusBar().showMessage(f'Exported {output_path}', 5000)

    def _export_all_from_dialog(self):
        if self._service is None:
            return
        dialog = self._ensure_split_dialog()
        total = 0
        if dialog.t42_enabled():
            total += count_split_t42_outputs(self._service)
        if dialog.html_enabled():
            total += count_html_outputs(self._service, include_subpages=dialog.html_include_subpages())
        total = max(total, 1)
        completed = {'count': 0}

        def step():
            completed['count'] += 1
            return completed['count'], total

        def task(report):
            if dialog.t42_enabled():
                from teletext.gui import viewer as viewer_module
                viewer_module.export_split_t42(
                    self._service,
                    dialog.t42_directory(),
                    pattern=dialog.split_pattern(),
                    include_magazine=dialog._flag_m.isChecked(),  # noqa: SLF001
                    include_page=dialog._flag_p.isChecked(),  # noqa: SLF001
                    include_subpage=dialog._flag_s.isChecked(),  # noqa: SLF001
                    include_count=dialog._flag_c.isChecked(),  # noqa: SLF001
                    progress_callback=lambda current, path: report(*step()),
                )
            if dialog.html_enabled():
                export_html(
                    self._service,
                    dialog.html_directory(),
                    include_subpages=dialog.html_include_subpages(),
                    localcodepage=dialog.html_localcodepage(),
                    progress_callback=lambda current, path: report(*step()),
                )

        self._run_progress_task('Split Export', 'Exporting outputs', task)
        self.statusBar().showMessage('Split export finished.', 5000)

    def closeEvent(self, event):  # pragma: no cover - GUI cleanup path
        self._thumbnail_timer.stop()
        if self._preview_widget is not None:
            self._preview_widget.hide()
        super().closeEvent(event)


EditorWindow = T42EditorWindow


def main():
    app = QtWidgets.QApplication(sys.argv)
    filename = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
    window = T42EditorWindow(filename=filename)
    window.show()
    app.exec_()


if __name__ == '__main__':
    main()
