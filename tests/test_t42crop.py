import os
import tempfile
import unittest

from teletext.packet import Packet
from teletext.gui.t42crop import (
    T42Insertion,
    add_row_to_subpage_entries,
    collect_t42_headers,
    collect_page_entries,
    collect_row_entries,
    collect_subpage_entries,
    edited_t42_entries,
    frame_preview_text,
    full_header_preview_text,
    header_preview_text,
    load_t42_entries,
    merge_page_in_entries,
    merge_subpage_in_entries,
    move_page_in_entries,
    move_subpage_in_entries,
    parse_page_identifier,
    parse_subpage_identifier,
    replace_page_in_entries,
    replace_subpage_in_entries,
    selected_row_zero_text,
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

    def test_selected_row_zero_text_returns_header_for_page_or_subpage(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100A'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 0, 0x00, 0x0002, 'PAGE 100B'))
            path = handle.name

        try:
            entries = load_t42_entries(path)
        finally:
            os.unlink(path)

        self.assertIn('P100:0001', selected_row_zero_text(entries, 0x100, 0x0001))
        self.assertIn('P100:0001', selected_row_zero_text(entries, 0x100))

    def test_frame_preview_text_lists_all_rows_in_current_frame(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 2, text='ROW2'))
            handle.write(_make_packet(1, 0, 0x01, 0x0001, 'PAGE 101'))
            path = handle.name

        try:
            entries = load_t42_entries(path)
        finally:
            os.unlink(path)

        text = frame_preview_text(entries, 1)
        self.assertIn('Frame preview (all rows):', text)
        self.assertIn('P100:0001 r00 PAGE 100', text)
        self.assertIn('P100:0001 r01 ROW1', text)
        self.assertIn('P100:0001 r02 ROW2', text)
        self.assertNotIn('P101:0001 r00 PAGE 101', text)

    def test_full_header_preview_text_lists_all_row_zero_packets(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 0, 0x01, 0x0001, 'PAGE 101'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            handle.write(_make_packet(1, 0, 0x02, 0x0001, 'PAGE 102'))
            path = handle.name

        try:
            entries = load_t42_entries(path)
        finally:
            os.unlink(path)

        text = full_header_preview_text(entries, collect_t42_headers(entries), 2)
        self.assertIn('Row 0 preview (full file):', text)
        self.assertIn('P100:0001 PAGE 100', text)
        self.assertIn('P101:0001 PAGE 101', text)
        self.assertIn('P102:0001 PAGE 102', text)

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

    def test_replace_page_entries_retargets_imported_page(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as base_handle:
            base_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            base_handle.write(_make_packet(1, 1, text='ROW1'))
            base_handle.write(_make_packet(1, 0, 0x01, 0x0001, 'PAGE 101'))
            base_handle.write(_make_packet(1, 1, text='ROW2'))
            base_path = base_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(2, 0, 0x34, 0x0007, 'PAGE 234'))
            source_handle.write(_make_packet(2, 1, text='SRC'))
            source_path = source_handle.name

        try:
            base_entries = load_t42_entries(base_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(base_path)
            os.unlink(source_path)

        replaced = replace_page_in_entries(base_entries, collect_page_entries(source_entries, 0x234), target_page_number=0x101)
        summary = summarise_t42_pages(replaced)
        self.assertEqual([page['page_number'] for page in summary], [0x100, 0x101])
        self.assertEqual(summary[1]['header_title'], 'PAGE 234')

    def test_replace_subpage_entries_retargets_selected_slot(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as base_handle:
            base_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100A'))
            base_handle.write(_make_packet(1, 1, text='ROW1'))
            base_handle.write(_make_packet(1, 0, 0x00, 0x0002, 'PAGE 100B'))
            base_handle.write(_make_packet(1, 1, text='ROW2'))
            base_path = base_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(2, 0, 0x34, 0x0007, 'PAGE 234Z'))
            source_handle.write(_make_packet(2, 1, text='SRC'))
            source_path = source_handle.name

        try:
            base_entries = load_t42_entries(base_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(base_path)
            os.unlink(source_path)

        replaced = replace_subpage_in_entries(
            base_entries,
            collect_subpage_entries(source_entries, 0x234, 0x0007),
            target_page_number=0x100,
            target_subpage_number=0x0002,
        )
        summary = summarise_t42_pages(replaced)
        self.assertEqual(summary[0]['subpages'][1]['subpage_number'], 0x0002)
        self.assertEqual(summary[0]['subpages'][1]['header_title'], 'PAGE 234Z')

    def test_merge_subpage_in_entries_adds_missing_rows(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as target_handle:
            target_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            target_handle.write(_make_packet(1, 1, text='ROW1'))
            target_path = target_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            source_handle.write(_make_packet(1, 2, text='ROW2'))
            source_path = source_handle.name

        try:
            target_entries = load_t42_entries(target_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(target_path)
            os.unlink(source_path)

        merged = merge_subpage_in_entries(
            target_entries,
            collect_subpage_entries(source_entries, 0x100, 0x0001),
            0x100,
            0x0001,
        )
        rows = [entry.row for entry in collect_subpage_entries(merged, 0x100, 0x0001)]
        self.assertEqual(rows, [0, 1, 2])

    def test_merge_page_in_entries_merges_subpages_by_number(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as target_handle:
            target_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100A'))
            target_handle.write(_make_packet(1, 1, text='A1'))
            target_handle.write(_make_packet(1, 0, 0x00, 0x0002, 'PAGE 100B'))
            target_handle.write(_make_packet(1, 1, text='B1'))
            target_path = target_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(2, 0, 0x34, 0x0001, 'PAGE 234A'))
            source_handle.write(_make_packet(2, 2, text='A2'))
            source_handle.write(_make_packet(2, 0, 0x34, 0x0002, 'PAGE 234B'))
            source_handle.write(_make_packet(2, 2, text='B2'))
            source_path = source_handle.name

        try:
            target_entries = load_t42_entries(target_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(target_path)
            os.unlink(source_path)

        merged = merge_page_in_entries(target_entries, source_entries, 0x234, 0x100)
        subpage_a_rows = [entry.row for entry in collect_subpage_entries(merged, 0x100, 0x0001)]
        subpage_b_rows = [entry.row for entry in collect_subpage_entries(merged, 0x100, 0x0002)]
        self.assertEqual(subpage_a_rows, [0, 1, 2])
        self.assertEqual(subpage_b_rows, [0, 1, 2])

    def test_add_row_to_subpage_entries_can_retarget_row_number(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as target_handle:
            target_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            target_handle.write(_make_packet(1, 1, text='ROW1'))
            target_path = target_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(1, 0, 0x02, 0x0001, 'PAGE 102'))
            source_handle.write(_make_packet(1, 5, text='ROW5'))
            source_path = source_handle.name

        try:
            target_entries = load_t42_entries(target_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(target_path)
            os.unlink(source_path)

        source_row = collect_row_entries(source_entries, 0x102, 0x0001, 5)[0]
        source_header = collect_subpage_entries(source_entries, 0x102, 0x0001)[0]
        updated = add_row_to_subpage_entries(
            target_entries,
            source_row,
            0x100,
            0x0001,
            3,
            source_header_entry=source_header,
        )
        rows = [entry.row for entry in collect_subpage_entries(updated, 0x100, 0x0001)]
        self.assertEqual(rows, [0, 1, 3])

    def test_add_row_to_subpage_entries_can_replace_header_with_row_zero(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as target_handle:
            target_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            target_handle.write(_make_packet(1, 1, text='ROW1'))
            target_path = target_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(1, 0, 0x02, 0x0001, 'PAGE 102'))
            source_handle.write(_make_packet(1, 5, text='ROW5'))
            source_path = source_handle.name

        try:
            target_entries = load_t42_entries(target_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(target_path)
            os.unlink(source_path)

        source_header = collect_row_entries(source_entries, 0x102, 0x0001, 0)[0]
        updated = add_row_to_subpage_entries(
            target_entries,
            source_header,
            0x100,
            0x0001,
            0,
        )
        header = collect_row_entries(updated, 0x100, 0x0001, 0)[0]
        self.assertIn('P100:0001', header.header_text)
        self.assertIn('PAGE 102', header.header_text)

    def test_add_row_to_subpage_entries_rejects_mapping_row_zero_to_non_header(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as target_handle:
            target_handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            target_path = target_handle.name
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as source_handle:
            source_handle.write(_make_packet(1, 0, 0x02, 0x0001, 'PAGE 102'))
            source_path = source_handle.name

        try:
            target_entries = load_t42_entries(target_path)
            source_entries = load_t42_entries(source_path)
        finally:
            os.unlink(target_path)
            os.unlink(source_path)

        source_header = collect_row_entries(source_entries, 0x102, 0x0001, 0)[0]
        with self.assertRaisesRegex(ValueError, 'Row 0 can only be copied to target row 0'):
            add_row_to_subpage_entries(
                target_entries,
                source_header,
                0x100,
                0x0001,
                1,
            )

    def test_move_page_entries_changes_page_number(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            source_path = handle.name

        try:
            entries = load_t42_entries(source_path)
        finally:
            os.unlink(source_path)

        moved = move_page_in_entries(entries, 0x100, 0x101)
        self.assertEqual([entry.page_number for entry in moved if entry.row == 0], [0x101])

    def test_move_subpage_entries_changes_page_and_subpage(self):
        with tempfile.NamedTemporaryFile(suffix='.t42', delete=False) as handle:
            handle.write(_make_packet(1, 0, 0x00, 0x0001, 'PAGE 100A'))
            handle.write(_make_packet(1, 1, text='ROW1'))
            source_path = handle.name

        try:
            entries = load_t42_entries(source_path)
        finally:
            os.unlink(source_path)

        moved = move_subpage_in_entries(entries, 0x100, 0x0001, 0x101, 0x0003)
        headers = [entry for entry in moved if entry.row == 0]
        self.assertEqual(headers[0].page_number, 0x101)
        self.assertEqual(headers[0].subpage_number, 0x0003)

    def test_parse_page_and_subpage_identifiers(self):
        self.assertEqual(parse_page_identifier('P1AF'), 0x1AF)
        self.assertEqual(parse_subpage_identifier('0007'), 0x0007)
