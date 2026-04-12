import json
import tempfile
import unittest

from teletext import pipeline
from teletext.subpage import Subpage


def _subpage_packets(fill_char, confidence, magazine=1, page=0x00, subpage=0x0000):
    subpage_obj = Subpage(prefill=True, magazine=magazine)
    subpage_obj.mrag.magazine = magazine
    subpage_obj.header.page = page
    subpage_obj.header.subpage = subpage
    subpage_obj.packet(1).displayable.place_string((fill_char * 40)[:40])
    packets = list(subpage_obj.packets)
    for packet in packets:
        packet._line_confidence = float(confidence)
    return packets


def _subpage_packets_rows(rows, confidence, magazine=1, page=0x00, subpage=0x0000):
    subpage_obj = Subpage(prefill=True, magazine=magazine)
    subpage_obj.mrag.magazine = magazine
    subpage_obj.header.page = page
    subpage_obj.header.subpage = subpage
    for row, text in rows.items():
        subpage_obj.packet(row).displayable.place_string(text.ljust(40)[:40])
    packets = list(subpage_obj.packets)
    for packet in packets:
        packet._line_confidence = float(confidence)
    return packets


class TestPipelineConsensus(unittest.TestCase):
    def test_confidence_weighted_duplicate_consensus_prefers_higher_confidence(self):
        packet_lists = [
            _subpage_packets('A', 20),
            _subpage_packets('A', 20),
            _subpage_packets('B', 90),
        ]

        subpage = next(iter(pipeline.subpage_squash(packet_lists, min_duplicates=1, use_confidence=True)))
        expected_packet = next(packet for packet in packet_lists[2] if packet.mrag.row == 1)

        self.assertEqual(int(subpage.packet(1)[2]), int(expected_packet[2]))

    def test_v1_squash_separates_different_content_with_same_subpage_code(self):
        packet_lists = [
            _subpage_packets('A', 40, subpage=0x0001),
            _subpage_packets('A', 35, subpage=0x0001),
            _subpage_packets('B', 40, subpage=0x0001),
            _subpage_packets('B', 35, subpage=0x0001),
        ]

        v3_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='v3'))
        v1_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='v1'))

        self.assertEqual(len(v3_subpages), 1)
        self.assertEqual(len(v1_subpages), 2)
        rendered = sorted(
            subpage.packet(1).displayable.bytes_no_parity[:1].decode('ascii')
            for subpage in v1_subpages
        )
        self.assertEqual(rendered, ['A', 'B'])

    def test_auto_squash_prefers_v1_when_subpage_codes_look_broken(self):
        packet_lists = [
            _subpage_packets('A', 40, subpage=0x0001),
            _subpage_packets('A', 35, subpage=0x0001),
            _subpage_packets('B', 40, subpage=0x0001),
            _subpage_packets('B', 35, subpage=0x0001),
        ]

        auto_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='auto'))

        self.assertEqual(len(auto_subpages), 2)
        rendered = sorted(
            subpage.packet(1).displayable.bytes_no_parity[:1].decode('ascii')
            for subpage in auto_subpages
        )
        self.assertEqual(rendered, ['A', 'B'])

    def test_auto_squash_keeps_v3_when_subpage_codes_are_distinct(self):
        packet_lists = [
            _subpage_packets('A', 50, subpage=0x0001),
            _subpage_packets('A', 45, subpage=0x0002),
        ]

        auto_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='auto'))

        self.assertEqual(len(auto_subpages), 2)
        subcodes = sorted(int(subpage.header.subpage) for subpage in auto_subpages)
        self.assertEqual(subcodes, [0x0001, 0x0002])

    def test_auto_squash_keeps_page_when_v1_splits_below_min_duplicates(self):
        packet_lists = [
            _subpage_packets('A', 50, subpage=0x0001),
            _subpage_packets('A', 45, subpage=0x0001),
            _subpage_packets('B', 40, subpage=0x0001),
        ]

        v3_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=3, squash_mode='v3'))
        v1_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=3, squash_mode='v1'))
        auto_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=3, squash_mode='auto'))

        self.assertEqual(len(v3_subpages), 1)
        self.assertEqual(len(v1_subpages), 0)
        self.assertEqual(len(auto_subpages), 1)

    def test_custom_squash_can_group_by_content_across_subpage_codes(self):
        packet_lists = [
            _subpage_packets_rows({1: 'MATCHED PAGE', 2: 'BODY SAME'}, 50, subpage=0x0001),
            _subpage_packets_rows({1: 'MATCHED PAGE', 2: 'BODY SAME'}, 45, subpage=0x0002),
        ]

        v3_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='v3'))
        custom_subpages = list(
            pipeline.subpage_squash(
                packet_lists,
                min_duplicates=1,
                squash_mode='custom',
                squash_profile={
                    'match_threshold': 0.70,
                    'subcode_match_bonus': 0.0,
                    'subcode_mismatch_penalty': 0.0,
                },
            )
        )

        self.assertEqual(len(v3_subpages), 2)
        self.assertEqual(len(custom_subpages), 1)

    def test_load_squash_profile_from_json_file(self):
        with tempfile.NamedTemporaryFile('w+', suffix='.json', delete=False, encoding='utf-8') as handle:
            json.dump({
                'match_threshold': 0.81,
                'header_weight': 0.8,
                'iterations': 5,
            }, handle)
            path = handle.name

        try:
            profile = pipeline.load_squash_profile(path)
        finally:
            import os
            os.unlink(path)

        self.assertEqual(profile['iterations'], 5)
        self.assertAlmostEqual(profile['match_threshold'], 0.81)
        self.assertAlmostEqual(profile['header_weight'], 0.8)
        self.assertIn('body_weight', profile)

    def test_builtin_squash_profile_can_be_loaded_by_name(self):
        profile = pipeline.get_builtin_squash_profile('broken-subcodes')
        self.assertAlmostEqual(profile['subcode_match_bonus'], 0.0)
        self.assertAlmostEqual(profile['subcode_mismatch_penalty'], 0.0)
        self.assertGreaterEqual(profile['iterations'], 4)

    def test_auto_squash_post_merge_can_take_better_row_from_alternate_mode(self):
        packet_lists = [
            _subpage_packets_rows({1: 'AAAAAAAAAAAA', 2: 'ROW2 P'}, 55, subpage=0x0001),
            _subpage_packets_rows({1: 'AAAAAAAAAAAA', 2: 'ROW2 Q'}, 45, subpage=0x0001),
            _subpage_packets_rows({1: 'BBBBBBBBBBBB', 2: 'ROW2 S'}, 60, subpage=0x0001),
            _subpage_packets_rows({1: 'BBBBBBBBBBBB', 2: 'ROW2 S'}, 60, subpage=0x0001),
        ]

        v1_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='v1'))
        auto_subpages = list(pipeline.subpage_squash(packet_lists, min_duplicates=1, squash_mode='auto'))

        v1_rendered = sorted(
            (
                subpage.packet(1).displayable.bytes_no_parity[:12].decode('ascii'),
                subpage.packet(2).displayable.bytes_no_parity[:6].decode('ascii'),
            )
            for subpage in v1_subpages
        )
        auto_rendered = sorted(
            (
                subpage.packet(1).displayable.bytes_no_parity[:12].decode('ascii'),
                subpage.packet(2).displayable.bytes_no_parity[:6].decode('ascii'),
            )
            for subpage in auto_subpages
        )

        self.assertTrue(
            any(row1 == 'AAAAAAAAAAAA' and row2 in {'ROW2 P', 'ROW2 Q'} for row1, row2 in v1_rendered)
        )
        self.assertIn(('AAAAAAAAAAAA', 'ROW2 S'), auto_rendered)
        self.assertIn(('BBBBBBBBBBBB', 'ROW2 S'), auto_rendered)

    def test_best_of_n_page_rebuild_prefers_highest_confidence_duplicate(self):
        packet_lists = [
            _subpage_packets('A', 15),
            _subpage_packets('B', 60),
            _subpage_packets('C', 95),
        ]

        subpage = next(iter(pipeline.subpage_squash(packet_lists, min_duplicates=1, best_of_n=1, use_confidence=True)))
        expected_packet = next(packet for packet in packet_lists[2] if packet.mrag.row == 1)

        self.assertEqual(int(subpage.packet(1)[2]), int(expected_packet[2]))
