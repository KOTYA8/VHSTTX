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

    def test_best_of_n_page_rebuild_prefers_highest_confidence_duplicate(self):
        packet_lists = [
            _subpage_packets('A', 15),
            _subpage_packets('B', 60),
            _subpage_packets('C', 95),
        ]

        subpage = next(iter(pipeline.subpage_squash(packet_lists, min_duplicates=1, best_of_n=1, use_confidence=True)))
        expected_packet = next(packet for packet in packet_lists[2] if packet.mrag.row == 1)

        self.assertEqual(int(subpage.packet(1)[2]), int(expected_packet[2]))
