import sys
import types
import unittest

import numpy as np


if 'enchant' not in sys.modules:
    enchant_module = types.ModuleType('enchant')

    class FakeDict(object):
        WORDS_BY_LANGUAGE = {
            'en_GB': {'news', 'sport'},
            'pl_PL': {'nowosti', 'nawosti', 'sport'},
            'de_DE': {'fuss', 'groesse'},
        }
        SUGGESTIONS_BY_LANGUAGE = {
            'pl_PL': {
                'ngwosti': ['nowosti', 'nawosti'],
            },
        }

        def __init__(self, language):
            self.language = language

        def check(self, word):
            return word.lower() in self.WORDS_BY_LANGUAGE.get(self.language, set())

        def suggest(self, word):
            return list(self.SUGGESTIONS_BY_LANGUAGE.get(self.language, {}).get(word.lower(), ()))

    enchant_module.Dict = FakeDict
    sys.modules['enchant'] = enchant_module


from teletext.coding import parity_encode
from teletext.spellcheck import TeletextCodec, analyze_page_packets, infer_localcodepage, spellcheck_page_packets
from teletext.subpage import Subpage


def make_subpage_with_lines(*lines, page=0x00, subpage_number=0x0000):
    subpage = Subpage(prefill=True, magazine=1)
    subpage.mrag.magazine = 1
    subpage.header.page = page
    subpage.header.subpage = subpage_number
    for row, line in enumerate(lines):
        subpage.displayable.place_string(line, x=0, y=row)
    return list(subpage.packets)


class TestSpellcheckHelpers(unittest.TestCase):
    def test_infer_localcodepage_from_language(self):
        self.assertEqual(infer_localcodepage('pl_PL'), 'pol')
        self.assertEqual(infer_localcodepage('de_DE'), 'deu')
        self.assertIsNone(infer_localcodepage('en_GB'))

    def test_codec_roundtrips_polish_character_with_local_codepage(self):
        codec = TeletextCodec()
        byte = codec.encode_character('\u0141', localcodepage='pol', codepage=1)
        line = np.array([parity_encode(byte)], dtype=np.uint8)

        decoded = codec.decode_cells(line, localcodepage='pol', codepage=1)

        self.assertEqual(decoded, ('\u0141',))


class TestTeletextSpellcheck(unittest.TestCase):
    def test_spellcheck_page_packets_corrects_word_using_weighted_candidates(self):
        original_packets = make_subpage_with_lines('NOWOSTI SPORT', 'NGWOSTI SPORT')

        corrected_packets = next(spellcheck_page_packets(iter([original_packets]), language='pl_PL'))
        codec = TeletextCodec()

        corrected_line = ''.join(codec.decode_cells(corrected_packets[2].displayable, codepage=0)).strip()

        self.assertEqual(corrected_line, 'NOWOSTI SPORT')

    def test_spellcheck_page_packets_does_not_mutate_original_packet_list(self):
        original_packets = make_subpage_with_lines('NGWOSTI SPORT')
        codec = TeletextCodec()

        corrected_packets = next(spellcheck_page_packets(iter([original_packets]), language='pl_PL'))

        original_line = ''.join(codec.decode_cells(original_packets[1].displayable, codepage=0)).strip()
        corrected_line = ''.join(codec.decode_cells(corrected_packets[1].displayable, codepage=0)).strip()

        self.assertEqual(original_line, 'NGWOSTI SPORT')
        self.assertEqual(corrected_line, 'NOWOSTI SPORT')

    def test_spellcheck_page_packets_uses_service_lexicon_across_pages(self):
        reference_packets = make_subpage_with_lines('NOWOSTI SPORT', page=0x00)
        noisy_packets = make_subpage_with_lines('NIWOSTI SPORT', page=0x01)
        codec = TeletextCodec()

        corrected_pages = list(spellcheck_page_packets(
            iter([reference_packets, noisy_packets]),
            language='pl_PL',
        ))

        corrected_line = ''.join(codec.decode_cells(corrected_pages[1][1].displayable, codepage=0)).strip()

        self.assertEqual(corrected_line, 'NOWOSTI SPORT')

    def test_spellcheck_page_packets_uses_duplicate_slot_majority_for_rare_words(self):
        first_packets = make_subpage_with_lines('AVTOMOBILH', 'SPORT')
        second_packets = make_subpage_with_lines('AVTOMOBILH', 'SPORT')
        noisy_packets = make_subpage_with_lines('ASTOMOBILH', 'SPORT')
        codec = TeletextCodec()

        corrected_pages = list(spellcheck_page_packets(
            iter([first_packets, second_packets, noisy_packets]),
            language='en_GB',
        ))

        corrected_line = ''.join(codec.decode_cells(corrected_pages[2][1].displayable, codepage=0)).strip()
        preserved_line = ''.join(codec.decode_cells(corrected_pages[0][1].displayable, codepage=0)).strip()

        self.assertEqual(corrected_line, 'AVTOMOBILH')
        self.assertEqual(preserved_line, 'AVTOMOBILH')

    def test_spellcheck_page_packets_allows_multi_error_slot_majority(self):
        first_packets = make_subpage_with_lines('KANALE', 'SPORT')
        second_packets = make_subpage_with_lines('KANALE', 'SPORT')
        noisy_packets = make_subpage_with_lines('QENALF', 'SPORT')
        codec = TeletextCodec()

        corrected_pages = list(spellcheck_page_packets(
            iter([first_packets, second_packets, noisy_packets]),
            language='en_GB',
        ))

        corrected_line = ''.join(codec.decode_cells(corrected_pages[2][1].displayable, codepage=0)).strip()
        preserved_line = ''.join(codec.decode_cells(corrected_pages[0][1].displayable, codepage=0)).strip()

        self.assertEqual(corrected_line, 'KANALE')
        self.assertEqual(preserved_line, 'KANALE')


class TestSpellcheckAnalysis(unittest.TestCase):
    def test_analyze_page_packets_reports_variant_pairs(self):
        reference_packets = make_subpage_with_lines('KANALE', 'SPORT')
        noisy_packets = make_subpage_with_lines('QENALF', 'SPORT')

        analysis = analyze_page_packets(
            [reference_packets, noisy_packets],
            min_word_length=3,
            max_differences=3,
        )

        self.assertEqual(analysis['page_count'], 2)
        self.assertEqual(analysis['variant_slot_count'], 1)
        self.assertEqual(analysis['variant_words'][('kanale', 'qenalf')], 1)
        self.assertEqual(analysis['char_pairs'][('a', 'e')], 1)

        report = analysis['variant_reports'][0]
        self.assertEqual(report.leader, 'KANALE')
        self.assertEqual(report.variant, 'QENALF')
        self.assertEqual(report.row, 1)
