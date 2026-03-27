import datetime
import os
import re

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from .packet import Packet


@dataclass(frozen=True)
class FastextLinkInfo:
    page_number: int | None
    subpage_number: int | None
    label: str
    enabled: bool


@dataclass(frozen=True)
class OverviewEntry:
    page_number: int
    subpage_number: int
    page_label: str
    subpage_label: str


@dataclass(frozen=True)
class ServiceMetadata:
    file_path: str | None
    file_name: str | None
    file_size: int | None
    modified_at: str | None
    page_count: int
    subpage_count: int
    magazine_counts: tuple[tuple[int, int], ...]
    codepages: tuple[int, ...]
    broadcast_present: bool
    initial_page: str | None
    broadcast_network: str | None
    broadcast_country: str | None
    broadcast_date: str | None
    broadcast_time: str | None
    likely_broadcaster: str | None
    likely_language: str | None
    likely_country: str | None
    confidence: str | None
    evidence: tuple[str, ...]
    sample_titles: tuple[tuple[str, str], ...]


_HEADER_TRANSLATION = str.maketrans({
    '$': 'T',
    '0': 'O',
    '1': 'I',
    '2': 'Z',
    '3': 'E',
    '4': 'A',
    '5': 'S',
    '6': 'G',
    '7': 'T',
    '8': 'B',
    '9': 'G',
    '@': 'A',
    '|': 'I',
    '!': 'I',
    "'": ' ',
    '"': ' ',
    '`': ' ',
    '*': ' ',
    '+': ' ',
    '-': ' ',
    '_': ' ',
    '/': ' ',
    '\\': ' ',
    ',': ' ',
    '.': ' ',
    ';': ' ',
    ':': ' ',
    '(': ' ',
    ')': ' ',
    '[': ' ',
    ']': ' ',
    '{': ' ',
    '}': ' ',
    '<': ' ',
    '>': ' ',
    '#': ' ',
    '%': ' ',
    '&': ' ',
    '=': ' ',
    '?': ' ',
})

_BROADCASTER_PROFILES = (
    {
        'name': 'ORT',
        'aliases': ('ORT', '1TV', 'CHANNELONE', 'PERVIY'),
        'country': 'Russia',
        'language': 'Russian',
        'keywords': ('NOVOSTI', 'SPORTA', 'POLITIKI', 'TELEINF', 'MOSKVA'),
    },
    {
        'name': 'RTR',
        'aliases': ('RTR', 'ROSSIYA', 'ROSSIJA'),
        'country': 'Russia',
        'language': 'Russian',
        'keywords': ('NOVOSTI', 'VESTI', 'ROSSIYA', 'SPORTA', 'MOSKVA'),
    },
    {
        'name': 'TVP',
        'aliases': ('TVP',),
        'country': 'Poland',
        'language': 'Polish',
        'keywords': ('WIADOMOSCI', 'SPORT', 'POLITYKI', 'GAZETA'),
    },
    {
        'name': 'RAI',
        'aliases': ('RAI',),
        'country': 'Italy',
        'language': 'Italian',
        'keywords': ('NOTIZIE', 'TELEVIDEO', 'SPORT', 'PROGRAMMA'),
    },
    {
        'name': 'ZDF',
        'aliases': ('ZDF',),
        'country': 'Germany',
        'language': 'German',
        'keywords': ('NACHRICHTEN', 'SPORT', 'PROGRAMM', 'WETTER'),
    },
    {
        'name': 'ARD',
        'aliases': ('ARD',),
        'country': 'Germany',
        'language': 'German',
        'keywords': ('NACHRICHTEN', 'SPORT', 'PROGRAMM', 'WETTER'),
    },
    {
        'name': 'TF1',
        'aliases': ('TF1', 'FRANCE2', 'FRANCE3'),
        'country': 'France',
        'language': 'French',
        'keywords': ('JOURNAL', 'INFOS', 'PROGRAMME', 'SPORT'),
    },
    {
        'name': 'NOS',
        'aliases': ('NOS', 'NPO'),
        'country': 'Netherlands',
        'language': 'Dutch',
        'keywords': ('NIEUWS', 'TELETEKST', 'PROGRAMMA', 'SPORT'),
    },
    {
        'name': 'SVT',
        'aliases': ('SVT',),
        'country': 'Sweden',
        'language': 'Swedish',
        'keywords': ('NYHETER', 'TEXTTV', 'SPORT', 'PROGRAM'),
    },
)

_LANGUAGE_KEYWORDS = {
    'Russian': ('NOVOSTI', 'SPORTA', 'POLITIKI', 'MOSKVA', 'TELEINF', 'GAZETA', 'PROGRAMMA'),
    'Polish': ('WIADOMOSCI', 'POLITYKI', 'SPORT', 'GAZETA', 'PROGRAM'),
    'German': ('NACHRICHTEN', 'SPORT', 'PROGRAMM', 'WETTER', 'FERNSEHEN'),
    'French': ('JOURNAL', 'INFOS', 'PROGRAMME', 'SPORT', 'METEO'),
    'Italian': ('NOTIZIE', 'SPORT', 'TELEVIDEO', 'PROGRAMMA', 'METEO'),
    'Dutch': ('NIEUWS', 'TELETEKST', 'PROGRAMMA', 'SPORT', 'WEER'),
    'Swedish': ('NYHETER', 'TEXTTV', 'PROGRAM', 'SPORT', 'VADER'),
}

_PREFERRED_SAMPLE_PAGES = (0x100, 0x101, 0x102, 0x104, 0x105, 0x150, 0x151, 0x152, 0x153, 0x154)


def _compose_page_number(magazine, page):
    return (int(magazine) << 8) | int(page)


def _page_label(page_number):
    magazine = int(page_number) >> 8
    page = int(page_number) & 0xff
    return f'P{magazine}{page:02X}'


def _clean_ascii_text(text):
    if not text:
        return ''
    if isinstance(text, bytes):
        text = text.decode('ascii', errors='ignore')
    text = ''.join(character if 32 <= ord(character) < 127 else ' ' for character in text.upper())
    return re.sub(r'\s+', ' ', text).strip()


def _header_topic(text):
    text = _clean_ascii_text(text)
    text = re.sub(r'^[* ]*[P]?[1-8][0-9A-F]{2}\s+', '', text)
    text = re.sub(r'^\d{2}/\d{2}\s+', '', text)
    text = re.sub(r'\b\d{2}:\d{2}(?::\d{2})?\b.*$', '', text)
    text = re.sub(r'\b\d{2}/\d{2}\b', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -')
    return text or _clean_ascii_text(text)


def _normalise_for_matching(text):
    text = _header_topic(text).translate(_HEADER_TRANSLATION)
    text = re.sub(r'[^A-Z0-9 ]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _extract_tokens(text):
    return tuple(token for token in re.findall(r'[A-Z]{3,}', _normalise_for_matching(text)) if token)


def _similarity(left, right):
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _best_keyword_matches(tokens, keywords, threshold=0.74):
    matches = []
    for keyword in keywords:
        best_score = 0.0
        for token in tokens:
            if abs(len(token) - len(keyword)) > 4:
                continue
            score = _similarity(token, keyword)
            if token == keyword:
                score = 1.0
            elif keyword in token or token in keyword:
                score = max(score, 0.85)
            best_score = max(best_score, score)
        if best_score >= threshold:
            matches.append((keyword, best_score))
    matches.sort(key=lambda item: (-item[1], item[0]))
    return matches


def _header_score(text):
    topic = _header_topic(text)
    letters = sum(character.isalpha() for character in topic)
    digits = sum(character.isdigit() for character in topic)
    noise = sum(not (character.isalnum() or character.isspace()) for character in topic)
    bonus = len(_best_keyword_matches(_extract_tokens(topic), ('NOVOSTI', 'SPORTA', 'POLITIKI', 'TELEINF', 'PROGRAMMA', 'GAZETA')))
    return (letters * 2) + digits + (bonus * 8) - (noise * 2)


def _iter_service_subpages(service):
    for magazine_number, magazine in sorted(service.magazines.items()):
        for page_number, page in sorted(magazine.pages.items()):
            if not page.subpages:
                continue
            full_page_number = _compose_page_number(magazine_number, page_number)
            for subpage_number, subpage in sorted(page.subpages.items()):
                yield magazine_number, full_page_number, subpage_number, subpage


def _collect_titles(service):
    raw_titles = defaultdict(list)
    for _, page_number, _, subpage in _iter_service_subpages(service):
        title = _header_topic(subpage.header.displayable.bytes_no_parity)
        if title:
            raw_titles[page_number].append(title)

    titles = {}
    for page_number, page_titles in raw_titles.items():
        titles[page_number] = max(page_titles, key=_header_score)
    return titles


def _scan_capture_file(filename):
    info = {
        'broadcast_present': False,
        'initial_page': None,
        'broadcast_network': None,
        'broadcast_country': None,
        'broadcast_date': None,
        'broadcast_time': None,
        'file_name': None,
        'file_size': None,
        'modified_at': None,
    }
    if not filename or not os.path.exists(filename):
        return info

    info['file_name'] = os.path.basename(filename)
    try:
        stat_result = os.stat(filename)
    except OSError:
        stat_result = None
    if stat_result is not None:
        info['file_size'] = stat_result.st_size
        info['modified_at'] = datetime.datetime.fromtimestamp(stat_result.st_mtime).strftime('%Y-%m-%d %H:%M:%S')

    try:
        with open(filename, 'rb') as handle:
            while True:
                chunk = handle.read(42)
                if len(chunk) < 42:
                    break
                try:
                    packet = Packet(np.frombuffer(chunk, dtype=np.uint8).copy())
                except Exception:
                    continue
                if packet.type != 'broadcast':
                    continue
                try:
                    broadcast = packet.broadcast
                except Exception:
                    continue
                info['broadcast_present'] = True
                if info['initial_page'] is None:
                    try:
                        info['initial_page'] = f'P{broadcast.initial_page.magazine}{broadcast.initial_page.page:02X}'
                    except Exception:
                        pass
                try:
                    designation = int(broadcast.dc)
                except Exception:
                    designation = None
                if designation in (0, 1):
                    try:
                        format1 = broadcast.format1
                        if info['broadcast_network'] is None:
                            info['broadcast_network'] = f'0x{int(format1.network):04X}'
                        if info['broadcast_date'] is None:
                            info['broadcast_date'] = format1.date.isoformat()
                        if info['broadcast_time'] is None:
                            offset = format1.offset
                            info['broadcast_time'] = f'{format1.hour:02d}:{format1.minute:02d}:{format1.second:02d} (UTC{offset:+g})'
                    except Exception:
                        pass
                elif designation in (2, 3):
                    try:
                        format2 = broadcast.format2
                        if info['broadcast_network'] is None:
                            info['broadcast_network'] = f'0x{int(format2.network):02X}'
                        if info['broadcast_country'] is None:
                            info['broadcast_country'] = f'0x{int(format2.country):02X}'
                        if info['broadcast_date'] is None:
                            info['broadcast_date'] = f'{int(format2.day):02d}/{int(format2.month):02d}'
                        if info['broadcast_time'] is None:
                            info['broadcast_time'] = f'{int(format2.hour):02d}:{int(format2.minute):02d}'
                    except Exception:
                        pass
    except OSError:
        pass

    return info


def _infer_from_titles(titles, filename=None):
    evidence = []
    best_broadcaster = None
    best_broadcaster_profile = None
    best_broadcaster_score = 0.0
    filename_token = ''
    filename_tokens = ()

    if filename:
        stem = os.path.splitext(os.path.basename(filename))[0]
        filename_tokens = tuple(token for token in re.findall(r'[A-Z0-9]+', stem.upper()) if token)
        if filename_tokens:
            filename_token = filename_tokens[-1]

    title_tokens = []
    for title in titles.values():
        title_tokens.extend(_extract_tokens(title))
    title_tokens = tuple(title_tokens)

    for profile in _BROADCASTER_PROFILES:
        score = 0.0
        alias_hit = None
        for token in filename_tokens:
            if token in profile['aliases']:
                score += 3.5
                alias_hit = token
                break
            if any(alias in token or token in alias for alias in profile['aliases']):
                score += 2.5
                alias_hit = token
                break

        keyword_matches = _best_keyword_matches(title_tokens, profile['keywords'], threshold=0.78)
        score += sum(match_score for _, match_score in keyword_matches[:3])

        if score > best_broadcaster_score:
            best_broadcaster = profile['name']
            best_broadcaster_profile = profile
            best_broadcaster_score = score
            evidence = []
            if alias_hit is not None:
                evidence.append(f'filename stem: {alias_hit}')
            if keyword_matches:
                evidence.append(
                    'header hints: ' + ', '.join(keyword for keyword, _ in keyword_matches[:3])
                )

    best_language = None
    best_language_score = 0.0
    language_matches = ()
    for language, keywords in _LANGUAGE_KEYWORDS.items():
        matches = _best_keyword_matches(title_tokens, keywords)
        score = sum(match_score for _, match_score in matches[:3])
        if score > best_language_score:
            best_language = language
            best_language_score = score
            language_matches = matches

    likely_country = None
    confidence = None

    if best_broadcaster_profile is not None and best_broadcaster_score >= 3.5:
        confidence = 'medium'
        likely_country = best_broadcaster_profile['country']
        best_language = best_broadcaster_profile['language']
    elif best_broadcaster_profile is not None and best_broadcaster_score >= 1.8:
        confidence = 'low'
        likely_country = best_broadcaster_profile['country']
        if best_language is None:
            best_language = best_broadcaster_profile['language']
    elif filename_token:
        best_broadcaster = filename_token
        confidence = 'low'
        evidence = [f'filename stem: {filename_token}']

    if likely_country is None and best_language in {
        'Russian': 'Russia',
        'Polish': 'Poland',
        'German': 'Germany',
        'French': 'France',
        'Italian': 'Italy',
        'Dutch': 'Netherlands',
        'Swedish': 'Sweden',
    }:
        likely_country = {
            'Russian': 'Russia',
            'Polish': 'Poland',
            'German': 'Germany',
            'French': 'France',
            'Italian': 'Italy',
            'Dutch': 'Netherlands',
            'Swedish': 'Sweden',
        }[best_language]

    if best_language is not None and language_matches and not any(item.startswith('header hints:') for item in evidence):
        evidence.append('header hints: ' + ', '.join(keyword for keyword, _ in language_matches[:3]))

    if confidence is None:
        if best_language_score >= 2.2:
            confidence = 'low'
        elif best_broadcaster is not None or best_language is not None:
            confidence = 'low'

    return {
        'likely_broadcaster': best_broadcaster,
        'likely_language': best_language,
        'likely_country': likely_country,
        'confidence': confidence,
        'evidence': tuple(dict.fromkeys(item for item in evidence if item)),
    }


def describe_service_metadata(service, filename=None):
    pages = []
    subpage_count = 0
    magazine_counts = Counter()
    codepages = set()

    for magazine_number, page_number, _, subpage in _iter_service_subpages(service):
        if page_number not in pages:
            pages.append(page_number)
            magazine_counts[magazine_number] += 1
        subpage_count += 1
        codepages.add(int(subpage.header.codepage))

    titles = _collect_titles(service)
    sample_page_numbers = [page_number for page_number in _PREFERRED_SAMPLE_PAGES if page_number in titles]
    for page_number in sorted(titles):
        if page_number not in sample_page_numbers:
            sample_page_numbers.append(page_number)
        if len(sample_page_numbers) >= 8:
            break
    sample_titles = tuple((_page_label(page_number), titles[page_number]) for page_number in sample_page_numbers[:8])

    file_info = _scan_capture_file(filename)
    inferred = _infer_from_titles(titles, filename=filename)
    return ServiceMetadata(
        file_path=filename,
        file_name=file_info['file_name'],
        file_size=file_info['file_size'],
        modified_at=file_info['modified_at'],
        page_count=len(pages),
        subpage_count=subpage_count,
        magazine_counts=tuple(sorted(magazine_counts.items())),
        codepages=tuple(sorted(codepages)),
        broadcast_present=file_info['broadcast_present'],
        initial_page=file_info['initial_page'],
        broadcast_network=file_info['broadcast_network'],
        broadcast_country=file_info['broadcast_country'],
        broadcast_date=file_info['broadcast_date'],
        broadcast_time=file_info['broadcast_time'],
        likely_broadcaster=inferred['likely_broadcaster'],
        likely_language=inferred['likely_language'],
        likely_country=inferred['likely_country'],
        confidence='high' if file_info['broadcast_present'] else inferred['confidence'],
        evidence=inferred['evidence'],
        sample_titles=sample_titles,
    )


class DirectPageBuffer:
    valid_digits = '0123456789ABCDEF'
    valid_first_digits = '12345678'

    def __init__(self):
        self.clear()

    def clear(self):
        self._text = ''

    @property
    def text(self):
        return self._text

    def push(self, character):
        character = character.strip().upper()
        if len(character) != 1 or character not in self.valid_digits:
            return False
        if not self._text and character not in self.valid_first_digits:
            return False
        if len(self._text) >= 3:
            self._text = ''
        self._text += character
        return True

    def backspace(self):
        if not self._text:
            return False
        self._text = self._text[:-1]
        return True

    @property
    def complete(self):
        return len(self._text) == 3


class ServiceNavigator:
    def __init__(self, service):
        self._service = service
        self._pages = self._collect_pages(service)
        self._hex_pages_enabled = True
        if not self._pages:
            raise ValueError('Teletext service does not contain any pages.')
        self._current_page_number = self._pages[0]
        self._current_subpage_number = self._subpage_numbers(self._current_page_number)[0]

    @staticmethod
    def compose_page_number(magazine, page):
        return (int(magazine) << 8) | int(page)

    @staticmethod
    def split_page_number(page_number):
        page_number = int(page_number)
        return page_number >> 8, page_number & 0xff

    @staticmethod
    def parse_page_number(text):
        text = text.strip().upper()
        if text.startswith('P'):
            text = text[1:]
        if len(text) != 3:
            raise ValueError('Page number must be three hexadecimal digits, e.g. 100 or 1AF.')
        try:
            page_number = int(text, 16)
        except ValueError as exc:
            raise ValueError('Page number must contain hexadecimal digits only.') from exc
        magazine = page_number >> 8
        if magazine < 1 or magazine > 8:
            raise ValueError('Magazine number must be between 1 and 8.')
        return page_number

    @property
    def page_numbers(self):
        return tuple(self._navigable_pages())

    @property
    def current_page_number(self):
        return self._current_page_number

    @property
    def current_page_label(self):
        magazine, page = self.split_page_number(self._current_page_number)
        return f'P{magazine}{page:02X}'

    @property
    def current_subpage_number(self):
        return self._current_subpage_number

    @property
    def current_subpage(self):
        return self.subpage(self._current_page_number, self._current_subpage_number)

    @property
    def current_subpage_index(self):
        return self._subpage_numbers(self._current_page_number).index(self._current_subpage_number)

    @property
    def current_subpage_count(self):
        return len(self._subpage_numbers(self._current_page_number))

    @property
    def current_subpage_position(self):
        return self.current_subpage_index + 1, self.current_subpage_count

    @property
    def page_count(self):
        return len(self._navigable_pages())

    def go_to_page_text(self, text):
        return self.go_to_page(self.parse_page_number(text))

    def subpage(self, page_number, subpage_number=None):
        subpages = self._page(page_number).subpages
        if subpage_number is None:
            subpage_number = sorted(subpages)[0]
        return subpages[subpage_number]

    @staticmethod
    def is_decimal_page(page_number):
        _, page = ServiceNavigator.split_page_number(page_number)
        return all(character.isdigit() for character in f'{page:02X}')

    @property
    def hex_pages_enabled(self):
        return self._hex_pages_enabled

    def set_hex_pages_enabled(self, enabled):
        self._hex_pages_enabled = bool(enabled)
        navigable_pages = self._navigable_pages()
        if self._current_page_number not in navigable_pages:
            self._current_page_number = self._closest_navigable_page()
            self._current_subpage_number = self._subpage_numbers(self._current_page_number)[0]

    def go_to_page(self, page_number, subpage_number=None):
        if page_number not in self._navigable_pages():
            return False

        subpages = self._subpage_numbers(page_number)
        self._current_page_number = page_number
        if subpage_number in subpages:
            self._current_subpage_number = subpage_number
        else:
            self._current_subpage_number = subpages[0]
        return True

    def go_next_page(self):
        pages = self._navigable_pages()
        index = pages.index(self._current_page_number)
        return self.go_to_page(pages[(index + 1) % len(pages)])

    def go_prev_page(self):
        pages = self._navigable_pages()
        index = pages.index(self._current_page_number)
        return self.go_to_page(pages[(index - 1) % len(pages)])

    def go_next_subpage(self):
        subpages = self._subpage_numbers(self._current_page_number)
        index = subpages.index(self._current_subpage_number)
        self._current_subpage_number = subpages[(index + 1) % len(subpages)]
        return True

    def go_prev_subpage(self):
        subpages = self._subpage_numbers(self._current_page_number)
        index = subpages.index(self._current_subpage_number)
        self._current_subpage_number = subpages[(index - 1) % len(subpages)]
        return True

    def can_auto_advance(self, subpages_enabled=True, pages_enabled=False):
        if subpages_enabled and self.current_subpage_count > 1:
            return True
        return pages_enabled and self.page_count > 1

    def auto_advance(self, subpages_enabled=True, pages_enabled=False):
        if subpages_enabled and self.current_subpage_count > 1:
            subpages = self._subpage_numbers(self._current_page_number)
            index = subpages.index(self._current_subpage_number)
            if index + 1 < len(subpages):
                self._current_subpage_number = subpages[index + 1]
                return 'subpage'
            if pages_enabled and self.page_count > 1:
                self.go_next_page()
                return 'page'
            self._current_subpage_number = subpages[0]
            return 'subpage'

        if pages_enabled and self.page_count > 1:
            self.go_next_page()
            return 'page'

        return None

    def overview_entries(self, include_subpages=True, include_hex_pages=None):
        entries = []
        if include_hex_pages is None:
            page_numbers = self._navigable_pages()
        elif include_hex_pages:
            page_numbers = list(self._pages)
        else:
            page_numbers = [page_number for page_number in self._pages if self.is_decimal_page(page_number)]

        for page_number in page_numbers:
            page_label = self._page_label(page_number)
            subpages = self._subpage_numbers(page_number)
            total = len(subpages)
            if not include_subpages:
                subpages = subpages[:1]
            for index, subpage_number in enumerate(subpages, start=1):
                entries.append(OverviewEntry(
                    page_number=page_number,
                    subpage_number=subpage_number,
                    page_label=page_label,
                    subpage_label=f'{index:02d}/{total:02d} ({subpage_number:04X})',
                ))
        return tuple(entries)

    def fastext_links(self):
        subpage = self.current_subpage
        if not subpage.has_packet(27, 0):
            return tuple(FastextLinkInfo(None, None, '---', False) for _ in range(4))

        links = []
        for link in subpage.fastext.links[:4]:
            page_number = self.compose_page_number(link.magazine, link.page)
            links.append(FastextLinkInfo(
            page_number=page_number,
            subpage_number=link.subpage,
            label=f'P{link.magazine}{link.page:02X}',
            enabled=page_number in self._navigable_pages(),
        ))
        return tuple(links)

    def metadata(self, filename=None):
        return describe_service_metadata(self._service, filename=filename)

    def go_to_fastext(self, index):
        link = self.fastext_links()[index]
        if not link.enabled or link.page_number is None:
            return False
        return self.go_to_page(link.page_number, link.subpage_number)

    def _page(self, page_number):
        magazine_number, page = self.split_page_number(page_number)
        magazine = self._service.magazines.get(magazine_number)
        if magazine is None:
            raise KeyError(page_number)
        result = magazine.pages.get(page)
        if result is None:
            raise KeyError(page_number)
        return result

    def _subpage_numbers(self, page_number):
        return tuple(sorted(self._page(page_number).subpages))

    def _navigable_pages(self):
        pages = [page_number for page_number in self._pages if self._page_allowed(page_number)]
        return pages if pages else list(self._pages)

    def _page_allowed(self, page_number):
        return self._hex_pages_enabled or self.is_decimal_page(page_number)

    def _closest_navigable_page(self):
        pages = self._navigable_pages()
        current_index = self._pages.index(self._current_page_number)
        for offset in range(len(self._pages)):
            candidate = self._pages[(current_index + offset) % len(self._pages)]
            if candidate in pages:
                return candidate
        return pages[0]

    @staticmethod
    def _collect_pages(service):
        pages = []
        for magazine_number, magazine in sorted(service.magazines.items()):
            for page_number, page in sorted(magazine.pages.items()):
                if page.subpages:
                    pages.append(ServiceNavigator.compose_page_number(magazine_number, page_number))
        return pages

    def _page_label(self, page_number):
        magazine, page = self.split_page_number(page_number)
        return f'P{magazine}{page:02X}'
