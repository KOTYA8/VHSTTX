import os
import tempfile
import unittest

from teletext.packet import Packet
from teletext.gui.t42crop import (
    T42Insertion,
    edited_t42_entries,
    header_preview_text,
    load_t42_entries,
    summarise_t42_pages,
    write_t42_entries,
)


def _make_packet(magazine, row, page=0x00, subpage=0x0000, text=''):
    packet = Packet()
    packet.mrag.magazine = magazine
    packet.mrag.row = row
    if row == 0:
        packet.header.page = page
        packet.header.subpage = subpage
        if text:
            packet.header.displayable.place_string(text.ljust(32)[:32])
    elif row < 26 and text:
        packet.displayable.place_string(text.ljust(40)[:40])
    return packet.to_bytes()


class TestT42CropHelpers(unittest.TestCase):

    def test_load_entries_tracks_page_and_subpage(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 0, 0x01, 0x0002, 'PAGE 101'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            path = handle.name

        try:
            entries = load_t42_entries(path)
        finally:
            os.unlink(path)

        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].page_number, 0x100)
        self.assertEqual(entries[1].page_number, 0x100)
        self.assertEqual(entries[2].page_number, 0x101)
        self.assertEqual(entries[3].subpage_number, 0x0002)
        self.assertIn('P100:0001', entries[0].header_text)

    def test_edited_entries_apply_cut_insert_and_delete(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as base_handle:
            base_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            base_handle.write(_make_packet(1, 1, text='ROW1'))
            base_handle.write(_make_packet(1, 0, 0x01, 0x0001, 'PAGE 101'))
            base_handle.write(_make_packet(1, 1, text='ROW1'))
            base_path = base_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as insert_handle:
            insert_handle.write(_make_packet(1, 0, 0x02, 0x0001, 'PAGE 102'))
            insert_path = insert_handle.name

        try:
            base_entries = load_t42_entries(base_path)
            insert_entries = load_t42_entries(insert_path)
        finally:
            os.unlink(base_path)
            os.unlink(insert_path)

        edited = edited_t42_entries(
            base_entries,
            cut_ranges=((1, 1),),
            insertions=(T42Insertion(
                after_packet=1,
                path='insert.t42',
                packet_count=len(insert_entries),
                entries=insert_entries,
            ),),
            deleted_pages={0x101},
            deleted_subpages=(),
        )

        self.assertEqual([entry.page_number for entry in edited], [0x100, 0x102])

    def test_header_preview_text_uses_current_packet_context(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 0, 0x01, 0x0001, 'PAGE 101'))
            path = handle.name

        try:
            entries = load_t42_entries(path)
        finally:
            os.unlink(path)

        text = header_preview_text(entries, tuple(entry for entry in []), 1)
        self.assertIn('Current packet: 2/3', text)
        self.assertIn('Current page: P100 / 0001', text)

    def test_summarise_pages_reflects_remaining_entries(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 0, 0x00, 0x0002, 'PAGE 100B'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            path = handle.name

        try:
            entries = load_t42_entries(path)
        finally:
            os.unlink(path)

        summary = summarise_t42_pages(entries)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['page_number'], 0x100)
        self.assertEqual(summary[0]['header_title'], 'PAGE 100')
        self.assertEqual(len(summary[0]['subpages']), 2)
        self.assertEqual(summary[0]['subpages'][0]['header_title'], 'PAGE 100')
        self.assertEqual(summary[0]['subpages'][1]['header_title'], 'PAGE 100B')

    def test_write_entries_round_trips_packet_count(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            source_path = handle.name

        target_path = None
        try:
            entries = load_t42_entries(source_path)
            with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as out:
                target_path = out.name
            write_t42_entries(entries, target_path)
            self.assertEqual(os.path.getsize(target_path), len(entries) * 42)
        finally:
            os.unlink(source_path)
            if target_path and os.path.exists(target_path):
                os.unlink(target_path)
