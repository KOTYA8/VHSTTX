import sys
import os
import pathlib
import tempfile
import types
import unittest


tqdm_module = types.ModuleType('tqdm')
tqdm_module.tqdm = lambda iterable=None, **kwargs: iterable
sys.modules.setdefault('tqdm', tqdm_module)

from teletext.service import Service
from teletext.subpage import Subpage
from teletext.viewer import DirectPageBuffer, ServiceNavigator, describe_service_metadata
from teletext.viewer import (
    build_split_pattern,
    count_html_outputs,
    ensure_html_assets,
    count_split_t42_outputs,
    extract_html_preview_entries,
    export_html,
    export_selected_html,
    export_selected_t42,
    export_split_t42,
    list_html_folder_entries,
    load_service_from_t42_directory,
    nearest_html_pages,
    normalise_html_subpage_fragment,
    render_subpage_text,
)


class TestDirectPageBuffer(unittest.TestCase):
    def test_collects_three_hex_digits_and_marks_complete(self):
        buffer = DirectPageBuffer()

        self.assertTrue(buffer.push('1'))
        self.assertTrue(buffer.push('a'))
        self.assertTrue(buffer.push('F'))

        self.assertEqual(buffer.text, '1AF')
        self.assertTrue(buffer.complete)

    def test_rejects_invalid_first_digit_and_supports_backspace(self):
        buffer = DirectPageBuffer()

        self.assertFalse(buffer.push('0'))
        self.assertEqual(buffer.text, '')
        self.assertTrue(buffer.push('2'))
        self.assertTrue(buffer.push('0'))
        self.assertTrue(buffer.backspace())
        self.assertEqual(buffer.text, '2')

    def test_starts_new_entry_after_three_digits(self):
        buffer = DirectPageBuffer()

        for character in '100':
            self.assertTrue(buffer.push(character))
        self.assertEqual(buffer.text, '100')
        self.assertTrue(buffer.push('2'))
        self.assertEqual(buffer.text, '2')


def make_subpage(magazine, page, subpage, fastext_links=(), header_text=None):
    result = Subpage(prefill=True, magazine=magazine)
    result.mrag.magazine = magazine
    result.header.page = page
    result.header.subpage = subpage
    if header_text is not None:
        result.header.displayable.place_string(header_text[:32].ljust(32))
    result.init_packet(27, 0, magazine)

    for link, (link_magazine, link_page, link_subpage) in zip(result.fastext.links[:4], fastext_links):
        link.page = link_page
        link.subpage = link_subpage
        link.magazine = link_magazine

    return result


class TestServiceNavigator(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.service.insert_page(make_subpage(1, 0x00, 0x0000, (
            (1, 0x01, 0x0000),
            (2, 0x00, 0x0000),
            (1, 0x00, 0x0001),
            (8, 0xFF, 0x0000),
        )))
        self.service.insert_page(make_subpage(1, 0x00, 0x0001))
        self.service.insert_page(make_subpage(1, 0x01, 0x0000))
        self.service.insert_page(make_subpage(2, 0x00, 0x0000))
        self.navigator = ServiceNavigator(self.service)

    def test_initial_position_uses_first_page_and_first_subpage(self):
        self.assertEqual(self.navigator.current_page_number, 0x100)
        self.assertEqual(self.navigator.current_page_label, 'P100')
        self.assertEqual(self.navigator.current_subpage_number, 0x0000)
        self.assertEqual(self.navigator.current_subpage_position, (1, 2))

    def test_page_navigation_wraps_across_available_pages(self):
        self.navigator.go_prev_page()
        self.assertEqual(self.navigator.current_page_number, 0x200)

        self.navigator.go_next_page()
        self.assertEqual(self.navigator.current_page_number, 0x100)

    def test_subpage_navigation_wraps_within_current_page(self):
        self.navigator.go_next_subpage()
        self.assertEqual(self.navigator.current_subpage_number, 0x0001)
        self.assertEqual(self.navigator.current_subpage_position, (2, 2))

        self.navigator.go_next_subpage()
        self.assertEqual(self.navigator.current_subpage_number, 0x0000)

        self.navigator.go_prev_subpage()
        self.assertEqual(self.navigator.current_subpage_number, 0x0001)

    def test_auto_advance_moves_to_next_page_after_last_subpage(self):
        self.assertEqual(self.navigator.current_page_number, 0x100)
        self.assertEqual(self.navigator.current_subpage_number, 0x0000)

        self.assertEqual(self.navigator.auto_advance(subpages_enabled=True, pages_enabled=True), 'subpage')
        self.assertEqual(self.navigator.current_page_number, 0x100)
        self.assertEqual(self.navigator.current_subpage_number, 0x0001)

        self.assertEqual(self.navigator.auto_advance(subpages_enabled=True, pages_enabled=True), 'page')
        self.assertEqual(self.navigator.current_page_number, 0x101)
        self.assertEqual(self.navigator.current_subpage_number, 0x0000)

    def test_auto_advance_wraps_subpages_when_page_advance_disabled(self):
        self.navigator.go_next_subpage()
        self.assertEqual(self.navigator.current_subpage_number, 0x0001)

        self.assertEqual(self.navigator.auto_advance(subpages_enabled=True, pages_enabled=False), 'subpage')
        self.assertEqual(self.navigator.current_page_number, 0x100)
        self.assertEqual(self.navigator.current_subpage_number, 0x0000)

    def test_auto_advance_can_step_pages_without_subpages(self):
        self.navigator.go_to_page(0x101)

        self.assertEqual(self.navigator.auto_advance(subpages_enabled=False, pages_enabled=True), 'page')
        self.assertEqual(self.navigator.current_page_number, 0x200)

    def test_overview_entries_include_all_pages_and_subpages(self):
        entries = self.navigator.overview_entries()

        self.assertEqual(
            [(entry.page_label, entry.subpage_number) for entry in entries],
            [('P100', 0x0000), ('P100', 0x0001), ('P101', 0x0000), ('P200', 0x0000)],
        )
        self.assertEqual(entries[0].subpage_label, '01/02 (0000)')
        self.assertEqual(entries[1].subpage_label, '02/02 (0001)')

    def test_overview_entries_follow_hex_page_filter(self):
        self.service.insert_page(make_subpage(1, 0xAF, 0x0000))
        navigator = ServiceNavigator(self.service)
        navigator.set_hex_pages_enabled(False)

        self.assertEqual(
            [entry.page_label for entry in navigator.overview_entries()],
            ['P100', 'P100', 'P101', 'P200'],
        )

    def test_overview_entries_can_hide_subpages(self):
        entries = self.navigator.overview_entries(include_subpages=False)

        self.assertEqual(
            [(entry.page_label, entry.subpage_number) for entry in entries],
            [('P100', 0x0000), ('P101', 0x0000), ('P200', 0x0000)],
        )

    def test_overview_entries_can_hide_hex_pages_without_global_filter(self):
        self.service.insert_page(make_subpage(1, 0xAF, 0x0000))
        navigator = ServiceNavigator(self.service)

        entries = navigator.overview_entries(include_hex_pages=False)

        self.assertEqual(
            [entry.page_label for entry in entries],
            ['P100', 'P100', 'P101', 'P200'],
        )

    def test_go_to_page_text_accepts_plain_and_prefixed_hex(self):
        self.assertTrue(self.navigator.go_to_page_text('200'))
        self.assertEqual(self.navigator.current_page_number, 0x200)

        self.assertTrue(self.navigator.go_to_page_text('P101'))
        self.assertEqual(self.navigator.current_page_number, 0x101)

    def test_nearest_pages_returns_previous_and_next_existing_pages(self):
        self.assertEqual(self.navigator.nearest_pages(0x180), (0x101, 0x200))
        self.assertEqual(self.navigator.nearest_pages(0x100), (None, 0x101))
        self.assertEqual(self.navigator.nearest_pages(0x800), (0x200, None))

    def test_parse_page_number_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            ServiceNavigator.parse_page_number('99')

        with self.assertRaises(ValueError):
            ServiceNavigator.parse_page_number('G00')

        with self.assertRaises(ValueError):
            ServiceNavigator.parse_page_number('900')

    def test_fastext_links_report_targets_and_enabled_state(self):
        links = self.navigator.fastext_links()

        self.assertEqual([link.label for link in links], ['P101', 'P200', 'P100', 'P8FF'])
        self.assertEqual([link.enabled for link in links], [True, True, True, False])

    def test_go_to_fastext_uses_available_target(self):
        self.assertTrue(self.navigator.go_to_fastext(0))
        self.assertEqual(self.navigator.current_page_number, 0x101)

        self.navigator.go_to_page(0x100)
        self.assertTrue(self.navigator.go_to_fastext(2))
        self.assertEqual(self.navigator.current_page_number, 0x100)
        self.assertEqual(self.navigator.current_subpage_number, 0x0001)

        self.navigator.go_to_page(0x100)
        self.assertFalse(self.navigator.go_to_fastext(3))
        self.assertEqual(self.navigator.current_page_number, 0x100)

    def test_disabling_hex_pages_skips_letter_pages_in_navigation(self):
        self.service.insert_page(make_subpage(1, 0xAF, 0x0000))
        self.service.insert_page(make_subpage(1, 0xB0, 0x0000))
        navigator = ServiceNavigator(self.service)

        navigator.set_hex_pages_enabled(False)

        self.assertEqual(navigator.page_numbers, (0x100, 0x101, 0x200))
        self.assertFalse(navigator.go_to_page_text('1AF'))

        navigator.go_prev_page()
        self.assertEqual(navigator.current_page_number, 0x200)

        navigator.go_next_page()
        self.assertEqual(navigator.current_page_number, 0x100)

    def test_hex_page_filter_disables_fastext_targets(self):
        hex_service = Service()
        hex_service.insert_page(make_subpage(1, 0x00, 0x0000, (
            (1, 0xAF, 0x0000),
            (2, 0x00, 0x0000),
            (1, 0x00, 0x0001),
            (8, 0xFF, 0x0000),
        )))
        hex_service.insert_page(make_subpage(1, 0x00, 0x0001))
        hex_service.insert_page(make_subpage(1, 0xAF, 0x0000))
        hex_service.insert_page(make_subpage(2, 0x00, 0x0000))
        navigator = ServiceNavigator(hex_service)

        navigator.set_hex_pages_enabled(False)
        links = navigator.fastext_links()

        self.assertEqual([link.enabled for link in links], [False, True, True, False])

    def test_service_from_packets_handles_header_pagination_without_overflow(self):
        subpage = make_subpage(1, 0x00, 0x0000, header_text='100 INDEX PAGE')
        service = Service.from_packets(iter(subpage.packets))

        navigator = ServiceNavigator(service)
        self.assertEqual(navigator.current_page_label, 'P100')


class TestServiceMetadata(unittest.TestCase):
    def make_temp_capture(self, stem='capture'):
        fd, path = tempfile.mkstemp(suffix=f'-{stem}.t42')
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(b'\x00' * 42)
        except Exception:
            os.close(fd)
            raise
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_metadata_infers_ort_from_filename_and_titles_without_830(self):
        service = Service()
        service.insert_page(make_subpage(1, 0x00, 0x0000, header_text='100 TELEINF 00/01 08:11:19'))
        service.insert_page(make_subpage(1, 0x01, 0x0000, header_text='101 NOVOSTI 08:11:20'))
        service.insert_page(make_subpage(1, 0x05, 0x0000, header_text='105 BEZ POLITIKI 08:11:21'))
        service.insert_page(make_subpage(1, 0x50, 0x0000, header_text='150 NOVOSTI SPORTA 08:11:22'))

        metadata = describe_service_metadata(service, self.make_temp_capture('ort'))

        self.assertEqual(metadata.page_count, 4)
        self.assertEqual(metadata.subpage_count, 4)
        self.assertFalse(metadata.broadcast_present)
        self.assertEqual(metadata.likely_broadcaster, 'ORT')
        self.assertEqual(metadata.likely_language, 'Russian')
        self.assertEqual(metadata.likely_country, 'Russia')
        self.assertEqual(metadata.confidence, 'medium')
        self.assertTrue(any('filename stem: ORT' == item for item in metadata.evidence))
        self.assertIn(('P101', 'NOVOSTI'), metadata.sample_titles)

    def test_metadata_falls_back_to_filename_stem_for_unknown_service(self):
        service = Service()
        service.insert_page(make_subpage(1, 0x00, 0x0000, header_text='100 INDEX PAGE'))

        metadata = describe_service_metadata(service, self.make_temp_capture('demo'))

        self.assertFalse(metadata.broadcast_present)
        self.assertEqual(metadata.likely_broadcaster, 'DEMO')
        self.assertIsNone(metadata.likely_language)
        self.assertEqual(metadata.confidence, 'low')

    def test_metadata_does_not_infer_russia_from_single_weak_hint(self):
        service = Service()
        service.insert_page(make_subpage(1, 0x00, 0x0000, header_text='100 TELEINF'))

        metadata = describe_service_metadata(service, self.make_temp_capture('unknown'))

        self.assertEqual(metadata.likely_broadcaster, 'UNKNOWN')
        self.assertIsNone(metadata.likely_language)
        self.assertIsNone(metadata.likely_country)
        self.assertEqual(metadata.confidence, 'low')

    def test_metadata_does_not_use_numeric_split_filename_as_broadcaster(self):
        service = Service()
        service.insert_page(make_subpage(1, 0x26, 0x0030, header_text='126 INDEX PAGE'))
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, '126-0030-0001.t42')
            with open(path, 'wb') as handle:
                handle.write(b'\x00' * 42)
            metadata = describe_service_metadata(service, path)

        self.assertIsNone(metadata.likely_broadcaster)
        self.assertIsNone(metadata.likely_language)
        self.assertIsNone(metadata.likely_country)

    def test_metadata_infers_bbc2_and_english_from_ceefax_headers(self):
        service = Service()
        service.insert_page(make_subpage(1, 0x00, 0x0000, header_text='CEEFAX 2 100 Fri 21 May 18:35/19'))
        service.insert_page(make_subpage(1, 0x01, 0x0000, header_text='CEEFAX 2 101 Fri 21 May 18:31/16'))

        metadata = describe_service_metadata(service, self.make_temp_capture('BBC2-19990521-sq'))

        self.assertFalse(metadata.broadcast_present)
        self.assertEqual(metadata.likely_broadcaster, 'BBC2')
        self.assertEqual(metadata.likely_language, 'English')
        self.assertEqual(metadata.likely_country, 'United Kingdom')
        self.assertEqual(metadata.confidence, 'medium')


class TestServiceExportHelpers(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.service.insert_page(make_subpage(1, 0x00, 0x0000, header_text='100 TELEINF 08:11:19'))
        self.service.insert_page(make_subpage(1, 0x00, 0x0001, header_text='100 TELEINF 08:11:20'))
        self.service.insert_page(make_subpage(1, 0x01, 0x0000, header_text='101 NOVOSTI 08:11:21'))
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def test_build_split_pattern_matches_flag_selection(self):
        self.assertEqual(build_split_pattern(True, True, True, True), '{m}{p}-{s}-{c}.t42')
        self.assertEqual(build_split_pattern(True, True, False, False), '{m}{p}.t42')
        self.assertEqual(build_split_pattern(False, True, True, False), '{p}-{s}.t42')
        self.assertEqual(build_split_pattern(False, False, False, False), 'capture.t42')

    def test_export_split_t42_can_group_subpages_by_page(self):
        paths = export_split_t42(
            self.service,
            self.tempdir.name,
            include_magazine=True,
            include_page=True,
            include_subpage=False,
            include_count=False,
        )

        self.assertEqual(
            sorted(path.name for path in paths),
            ['100.t42', '101.t42'],
        )
        self.assertTrue((pathlib.Path(self.tempdir.name) / '100.t42').stat().st_size > 0)

    def test_export_selected_helpers_write_single_files(self):
        t42_path = pathlib.Path(self.tempdir.name) / 'one-subpage.t42'
        html_path = pathlib.Path(self.tempdir.name) / 'one-subpage.html'

        export_selected_t42(self.service, t42_path, 0x100, subpage_number=0x0001)
        export_selected_html(self.service, html_path, 0x100, subpage_number=0x0001, localcodepage='cyr')

        self.assertTrue(t42_path.exists())
        self.assertTrue(html_path.exists())
        html = html_path.read_text(encoding='utf-8')
        self.assertIn('Page 100-0001', html)
        self.assertIn('teletext2.ttf', html)

    def test_export_html_can_split_subpages(self):
        paths = export_html(self.service, self.tempdir.name, include_subpages=True)

        self.assertEqual(
            sorted(path.name for path in paths),
            ['100-0000.html', '100-0001.html', '101-0000.html'],
        )
        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext.css').exists())
        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext2.ttf').exists())
        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext4.ttf').exists())

    def test_ensure_html_assets_copies_styles_and_fonts(self):
        ensure_html_assets(self.tempdir.name)

        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext.css').exists())
        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext-noscanlines.css').exists())
        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext2.ttf').exists())
        self.assertTrue((pathlib.Path(self.tempdir.name) / 'teletext4.ttf').exists())

    def test_export_html_helpers_handle_duplicate_subpages(self):
        duplicate = make_subpage(1, 0x00, 0x0000, header_text='100 TELEINF DUPLICATE')
        self.service.magazines[1].pages[0x00].subpages[0x0000].duplicates.append(duplicate)

        html_path = pathlib.Path(self.tempdir.name) / 'duplicates.html'
        export_selected_html(self.service, html_path, 0x100)
        paths = export_html(self.service, self.tempdir.name, include_subpages=False)

        self.assertTrue(html_path.exists())
        self.assertIn('Page 100', html_path.read_text(encoding='utf-8'))
        self.assertIn(pathlib.Path(self.tempdir.name) / '100.html', paths)

    def test_export_count_helpers_include_duplicate_subpages(self):
        duplicate = make_subpage(1, 0x00, 0x0000, header_text='100 TELEINF DUPLICATE')
        self.service.magazines[1].pages[0x00].subpages[0x0000].duplicates.append(duplicate)

        self.assertEqual(count_split_t42_outputs(self.service), 4)
        self.assertEqual(count_html_outputs(self.service, include_subpages=False), 2)
        self.assertEqual(count_html_outputs(self.service, include_subpages=True), 4)

    def test_render_subpage_text_includes_header_and_body(self):
        subpage = self.service.magazines[1].pages[0x00].subpages[0x0000]
        subpage.displayable.place_string('HELLO WORLD', y=0)

        text = render_subpage_text(0x100, subpage)
        lines = text.splitlines()

        self.assertEqual(len(lines), 25)
        self.assertIn('P100', lines[0])
        self.assertIn('HELLO WORLD', lines[1])

    def test_render_subpage_text_can_reveal_concealed_text(self):
        subpage = self.service.magazines[1].pages[0x00].subpages[0x0000]
        subpage.displayable[0, 0] = 0x18
        subpage.displayable[0, 1] = ord('A')

        hidden = render_subpage_text(0x100, subpage)
        revealed = render_subpage_text(0x100, subpage, reveal=True)

        self.assertNotIn('A', hidden.splitlines()[1][:2])
        self.assertIn('A', revealed.splitlines()[1][:2])

    def test_extract_html_preview_entries_preserves_duplicate_labels(self):
        html = '''
            <html><body>
            <div class="subpage" id="0000">A</div>
            <div class="subpage" id="0000">B</div>
            <div class="subpage" id="0001">C</div>
            </body></html>
        '''

        entries = extract_html_preview_entries(html)

        self.assertEqual([entry.label for entry in entries], ['0000', '0000 (2)', '0001'])
        self.assertEqual(entries[0].identifier, '0000')
        self.assertIn('A', entries[0].html)

    def test_normalise_html_subpage_fragment_makes_rows_block_elements(self):
        fragment = '''
            <div class="subpage" id="0000">
                <span class="row"><span class="f7">A</span><span class="f2">B</span></span>
                <span class="row"><span class="f7">C</span></span>
            </div>
        '''

        normalised = normalise_html_subpage_fragment(fragment)

        self.assertNotIn('<span class="row">', normalised)
        self.assertEqual(normalised.count('<div class="row">'), 2)
        self.assertIn('<div class="subpage" id="0000">', normalised)
        self.assertIn('<span class="f7">A</span><span class="f2">B</span></div>', normalised)

    def test_list_html_folder_entries_sorts_pages_and_subpages(self):
        directory = pathlib.Path(self.tempdir.name)
        for name in ('101.html', '100-0001.html', '100.html', 'misc.html'):
            (directory / name).write_text('<html></html>', encoding='utf-8')

        entries = list_html_folder_entries(directory)

        self.assertEqual(
            [entry.file_name for entry in entries],
            ['100.html', '100-0001.html', '101.html', 'misc.html'],
        )
        self.assertEqual(entries[0].label, 'P100')
        self.assertEqual(entries[1].label, 'P100 / 0001')

    def test_nearest_html_pages_returns_previous_and_next_existing_pages(self):
        directory = pathlib.Path(self.tempdir.name)
        for name in ('100.html', '101-0001.html', '200.html', 'misc.html'):
            (directory / name).write_text('<html></html>', encoding='utf-8')

        entries = list_html_folder_entries(directory)

        self.assertEqual(nearest_html_pages(entries, 0x180), (0x101, 0x200))
        self.assertEqual(nearest_html_pages(entries, 0x100), (None, 0x101))
        self.assertEqual(nearest_html_pages(entries, 0x800), (0x200, None))

    def test_load_service_from_t42_directory_collects_all_pages(self):
        directory = pathlib.Path(self.tempdir.name)
        export_selected_t42(self.service, directory / '100.t42', 0x100)
        export_selected_t42(self.service, directory / '101.t42', 0x101)

        loaded = load_service_from_t42_directory(directory)

        navigator = ServiceNavigator(loaded)
        self.assertEqual(navigator.page_numbers, (0x100, 0x101))
