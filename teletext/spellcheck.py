import itertools
import unicodedata
from collections import Counter
from dataclasses import dataclass

import enchant

from .charset import g0
from .coding import parity_encode
from .packet import Packet
from .parser import Parser


LANGUAGE_CODEPAGE_HINTS = {
    'de': 'deu',
    'fr': 'fra',
    'it': 'ita',
    'nl': 'nld',
    'pl': 'pol',
    'ru': 'cyr',
    'sv': 'swe',
    'uk': 'cyr',
}


def infer_localcodepage(language, localcodepage=None):
    if localcodepage:
        return localcodepage
    if not language:
        return None
    return LANGUAGE_CODEPAGE_HINTS.get(language.split('_', 1)[0].lower())


def strip_diacritics(text):
    return ''.join(
        character for character in unicodedata.normalize('NFD', text)
        if unicodedata.category(character) != 'Mn'
    )


def _make_weight_table():
    table = {}

    def add_pair(cost, left, right):
        key = tuple(sorted((left.lower(), right.lower())))
        table[key] = min(cost, table.get(key, cost))

    # Legacy teletext confusions from the original spellchecker.
    for pair in (
        'ab', 'bd', 'ce', 'dh', 'ef', 'ei', 'ej', 'er', 'fj', 'ij', 'jl', 'jr', 'jt',
        'km', 'ks', 'ku', 'lt', 'mn', 'qr', 'rt', 'tx', 'uv', 'uy', 'uz', 'vz', 'yz',
    ):
        add_pair(0.22, pair[0], pair[1])

    # Additional VHS/teletext confusions seen in practice.
    for pair in (
        'go', 'cg', 'oq', 'vw', 'vv', 'rn', 'il', 'it', 'ao', 'sx', 'hv', 'mw',
    ):
        add_pair(0.30, pair[0], pair[1])

    return table


SUBSTITUTION_WEIGHTS = _make_weight_table()


@dataclass(frozen=True)
class WordToken:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PageToken:
    page_key: tuple[int, int, int]
    row: int
    start: int
    end: int
    text: str
    codepage: int


class LineCellsParser(Parser):
    def __init__(self, tt, localcodepage=None, codepage=0):
        self.characters = []
        super().__init__(tt, localcodepage=localcodepage, codepage=codepage)

    def emitcharacter(self, c):
        self.characters.append(c)


class TeletextCodec(object):
    def __init__(self):
        self._reverse_maps = {name: self._build_reverse_map(mapping) for name, mapping in g0.items()}

    @staticmethod
    def _build_reverse_map(mapping):
        reverse = {}
        for byte, character in mapping.items():
            reverse.setdefault(character, byte)
        return reverse

    def active_codepage(self, localcodepage=None, codepage=0):
        return localcodepage if localcodepage and codepage else 'default'

    def decode_cells(self, displayable, localcodepage=None, codepage=0):
        parser = LineCellsParser(displayable[:], localcodepage=localcodepage, codepage=codepage)
        return tuple(parser.characters)

    def encode_character(self, character, localcodepage=None, codepage=0):
        active = self.active_codepage(localcodepage=localcodepage, codepage=codepage)
        for mapping_name in (active, 'default'):
            byte = self._reverse_maps[mapping_name].get(character)
            if byte is not None:
                return byte
        return None


class LegacySpellChecker(object):
    common_errors = set(itertools.chain.from_iterable(
        itertools.permutations(s, 2) for s in (
            'ab', 'bd', 'ce', 'dh', 'ef', 'ei', 'ej', 'er', 'fj', 'ij', 'jl', 'jr', 'jt',
            'km', 'ks', 'ku', 'lt', 'mn', 'qr', 'rt', 'tx', 'uv', 'uy', 'uz', 'vz', 'yz',
        )
    ))

    def __init__(self, language='en_GB'):
        self.dictionary = enchant.Dict(language)

    def check_pair(self, x, y):
        if x == y or (x, y) in self.common_errors:
            return 0
        return 1

    def weighted_hamming(self, a, b):
        return sum(self.check_pair(x, y) for x, y in zip(a, b))

    @staticmethod
    def case_match(word, src):
        return ''.join(c.lower() if d.islower() else c.upper() for c, d in zip(word, src))

    def suggest(self, word):
        if len(word) > 1:
            lcword = word.lower()
            if not self.dictionary.check(lcword):
                for suggestion in self.dictionary.suggest(lcword):
                    if len(suggestion) == len(lcword) and self.weighted_hamming(suggestion.lower(), lcword) == 0:
                        return self.case_match(suggestion, word)
        return word

    def spellcheck(self, displayable):
        words = ''.join(c if c.isalpha() else ' ' for c in displayable.to_ansi(colour=False)).split(' ')
        words = [self.suggest(w) for w in words]

        line = ' '.join(words).encode('ascii')
        for index, byte in enumerate(line):
            if byte != ord(b' '):
                displayable[index] = parity_encode(byte)


class TeletextSpellChecker(object):
    def __init__(
        self,
        language='en_GB',
        localcodepage=None,
        min_word_length=3,
        max_candidates=12,
        max_cost=1.35,
        min_margin=0.20,
    ):
        self.dictionary = enchant.Dict(language)
        self.localcodepage = infer_localcodepage(language, localcodepage)
        self.min_word_length = min_word_length
        self.max_candidates = max_candidates
        self.max_cost = max_cost
        self.min_margin = min_margin
        self.codec = TeletextCodec()
        self._suggestion_cache = {}
        self._trusted_service_frequency = 2

    @staticmethod
    def case_match(word, src):
        return ''.join(c.lower() if d.islower() else c.upper() for c, d in zip(word, src))

    def is_valid_word(self, word):
        return len(word) >= self.min_word_length and self.dictionary.check(word.lower())

    def extract_tokens(self, characters):
        tokens = []
        start = None

        for index, character in enumerate(characters):
            if character.isalpha():
                if start is None:
                    start = index
            elif start is not None:
                token = ''.join(characters[start:index])
                tokens.append(WordToken(start=start, end=index, text=token))
                start = None

        if start is not None:
            token = ''.join(characters[start:])
            tokens.append(WordToken(start=start, end=len(characters), text=token))

        return tuple(tokens)

    def substitution_cost(self, left, right):
        if left == right:
            return 0.0

        left = left.lower()
        right = right.lower()
        if left == right:
            return 0.0

        if strip_diacritics(left) == strip_diacritics(right):
            return 0.18

        return SUBSTITUTION_WEIGHTS.get(tuple(sorted((left, right))), 1.0)

    def weighted_hamming(self, source, candidate):
        if len(source) != len(candidate):
            return float('inf'), float('inf')

        total = 0.0
        hard_errors = 0
        for left, right in zip(source, candidate):
            cost = self.substitution_cost(left, right)
            total += cost
            if cost >= 1.0:
                hard_errors += 1
        return total, hard_errors

    def build_page_lexicon(self, packet_list):
        lexicon = Counter()
        for token in self.page_tokens(packet_list):
            if self.is_valid_word(token.text):
                lexicon[token.text.lower()] += 1
        return lexicon

    def page_key(self, packet_list):
        header_packet = packet_list[0]
        return (
            header_packet.mrag.magazine,
            header_packet.header.page,
            header_packet.header.subpage,
        )

    def page_tokens(self, packet_list):
        page_key = self.page_key(packet_list)
        page_codepage = packet_list[0].header.codepage if packet_list and packet_list[0].type == 'header' else 0

        for packet in packet_list:
            if packet.type == 'header':
                displayable = packet.header.displayable
                codepage = packet.header.codepage
            elif packet.type == 'display':
                displayable = packet.displayable
                codepage = page_codepage
            else:
                continue

            characters = self.codec.decode_cells(displayable, localcodepage=self.localcodepage, codepage=codepage)
            for token in self.extract_tokens(characters):
                yield PageToken(
                    page_key=page_key,
                    row=packet.mrag.row,
                    start=token.start,
                    end=token.end,
                    text=token.text,
                    codepage=codepage,
                )

    def build_service_lexicon(self, page_tokens):
        lexicon = Counter()
        for token in page_tokens:
            if self.is_valid_word(token.text):
                lexicon[token.text.lower()] += 1
        return lexicon

    @staticmethod
    def build_duplicate_slot_lexicon(page_tokens):
        slots = {}
        for token in page_tokens:
            key = (token.page_key, token.row, token.start, token.end)
            slots.setdefault(key, Counter())[token.text.lower()] += 1
        return slots

    def apply_word(self, displayable, token, replacement, codepage):
        encoded = []
        for character in replacement:
            byte = self.codec.encode_character(character, localcodepage=self.localcodepage, codepage=codepage)
            if byte is None:
                return False
            encoded.append(byte)

        for index, byte in zip(range(token.start, token.end), encoded):
            displayable[index] = parity_encode(byte)
        return True

    def candidate_words(self, word, page_lexicon, service_lexicon=None, slot_lexicon=None):
        lower_word = word.lower()
        cache_key = lower_word
        slot_word_count = slot_lexicon.get(lower_word, 0) if slot_lexicon else 0
        if cache_key not in self._suggestion_cache:
            suggestions = []
            seen = set()
            for suggestion in self.dictionary.suggest(lower_word):
                candidate = suggestion.strip()
                if not candidate or len(candidate) != len(lower_word):
                    continue
                if not all(character.isalpha() for character in candidate):
                    continue
                lowered = candidate.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                suggestions.append(lowered)
                if len(suggestions) >= self.max_candidates:
                    break
            self._suggestion_cache[cache_key] = tuple(suggestions)

        candidates = list(self._suggestion_cache[cache_key])
        seen = set(candidates)

        for lexicon in (page_lexicon, service_lexicon, slot_lexicon):
            if not lexicon:
                continue
            for candidate, count in lexicon.most_common():
                if len(candidate) != len(lower_word):
                    continue
                if candidate == lower_word:
                    continue
                if candidate in seen:
                    continue
                if lexicon is slot_lexicon:
                    if count <= slot_word_count and not self.dictionary.check(candidate):
                        continue
                elif count < self._trusted_service_frequency and not self.dictionary.check(candidate):
                    continue
                if lexicon is slot_lexicon or count >= self._trusted_service_frequency or self.dictionary.check(candidate):
                    seen.add(candidate)
                    candidates.append(candidate)
                if len(candidates) >= self.max_candidates * 3:
                    break
        return tuple(candidates)

    def score_candidate(self, word, candidate, rank, page_lexicon, service_lexicon=None, slot_lexicon=None):
        total_cost, hard_errors = self.weighted_hamming(word.lower(), candidate.lower())
        if hard_errors > 1:
            return None

        score = total_cost + (rank * 0.05)
        if candidate[0].lower() == word[0].lower():
            score -= 0.05
        if candidate[-1].lower() == word[-1].lower():
            score -= 0.05

        score -= min(0.35, 0.12 * page_lexicon.get(candidate.lower(), 0))
        if service_lexicon:
            score -= min(0.55, 0.08 * service_lexicon.get(candidate.lower(), 0))
        if slot_lexicon:
            score -= min(0.90, 0.40 * slot_lexicon.get(candidate.lower(), 0))
        return max(0.0, score)

    def suggest(self, word, page_lexicon, service_lexicon=None, slot_lexicon=None):
        if len(word) < self.min_word_length:
            return word

        lower_word = word.lower()
        if self.dictionary.check(lower_word):
            return word

        scored = []
        for rank, candidate in enumerate(self.candidate_words(
            word,
            page_lexicon,
            service_lexicon=service_lexicon,
            slot_lexicon=slot_lexicon,
        )):
            score = self.score_candidate(
                word,
                candidate,
                rank,
                page_lexicon,
                service_lexicon=service_lexicon,
                slot_lexicon=slot_lexicon,
            )
            if score is None:
                continue
            scored.append((score, candidate))

        if not scored:
            return word

        scored.sort()
        best_score, best_candidate = scored[0]
        if best_score > self.max_cost:
            return word

        if len(scored) > 1 and (scored[1][0] - best_score) < self.min_margin:
            return word

        return self.case_match(best_candidate, word)

    def spellcheck_page(self, packet_list, service_lexicon=None, duplicate_slot_lexicon=None):
        page_key = self.page_key(packet_list)
        page_lexicon = self.build_page_lexicon(packet_list)
        page_codepage = packet_list[0].header.codepage if packet_list and packet_list[0].type == 'header' else 0

        for packet in packet_list:
            if packet.type == 'header':
                if duplicate_slot_lexicon is not None:
                    characters = self.codec.decode_cells(
                        packet.header.displayable,
                        localcodepage=self.localcodepage,
                        codepage=packet.header.codepage,
                    )
                    tokens = self.extract_tokens(characters)
                    row_slot_lexicons = {
                        (token.start, token.end): duplicate_slot_lexicon.get((page_key, packet.mrag.row, token.start, token.end))
                        for token in tokens
                    }
                else:
                    row_slot_lexicons = {}

                self._spellcheck_packet_displayable(
                    packet.header.displayable,
                    packet.header.codepage,
                    page_lexicon,
                    service_lexicon,
                    row_slot_lexicons,
                )
            elif packet.type == 'display':
                if duplicate_slot_lexicon is not None:
                    characters = self.codec.decode_cells(
                        packet.displayable,
                        localcodepage=self.localcodepage,
                        codepage=page_codepage,
                    )
                    tokens = self.extract_tokens(characters)
                    row_slot_lexicons = {
                        (token.start, token.end): duplicate_slot_lexicon.get((page_key, packet.mrag.row, token.start, token.end))
                        for token in tokens
                    }
                else:
                    row_slot_lexicons = {}

                self._spellcheck_packet_displayable(
                    packet.displayable,
                    page_codepage,
                    page_lexicon,
                    service_lexicon,
                    row_slot_lexicons,
                )
        return packet_list

    def _spellcheck_packet_displayable(self, displayable, codepage, page_lexicon, service_lexicon, row_slot_lexicons):
        characters = list(self.codec.decode_cells(displayable, localcodepage=self.localcodepage, codepage=codepage))

        for token in self.extract_tokens(characters):
            replacement = self.suggest(
                token.text,
                page_lexicon,
                service_lexicon=service_lexicon,
                slot_lexicon=row_slot_lexicons.get((token.start, token.end)),
            )
            if replacement == token.text:
                continue

            if self.apply_word(displayable, token, replacement, codepage):
                for index, character in zip(range(token.start, token.end), replacement):
                    characters[index] = character
                page_lexicon[replacement.lower()] += 1
                if service_lexicon is not None:
                    service_lexicon[replacement.lower()] += 1


def spellcheck_packets(packets, language='en_GB'):
    spellchecker = LegacySpellChecker(language)

    for packet in packets:
        packet_type = packet.type
        if packet_type == 'display':
            spellchecker.spellcheck(packet.displayable)
        elif packet_type == 'header':
            spellchecker.spellcheck(packet.header.displayable)
        yield packet


def spellcheck_page_packets(packet_lists, language='en_GB', localcodepage=None):
    spellchecker = TeletextSpellChecker(language=language, localcodepage=localcodepage)
    packet_lists = [list(packet_list) for packet_list in packet_lists]
    corrected_packet_lists = [
        [Packet(packet.to_bytes(), number=packet.number) for packet in packet_list]
        for packet_list in packet_lists
    ]
    page_tokens = [token for packet_list in packet_lists for token in spellchecker.page_tokens(packet_list)]
    service_lexicon = spellchecker.build_service_lexicon(page_tokens)
    duplicate_slot_lexicon = spellchecker.build_duplicate_slot_lexicon(page_tokens)

    for corrected_packet_list in corrected_packet_lists:
        yield spellchecker.spellcheck_page(
            corrected_packet_list,
            service_lexicon=service_lexicon,
            duplicate_slot_lexicon=duplicate_slot_lexicon,
        )
