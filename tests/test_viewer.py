import sys
import types
import unittest


tqdm_module = types.ModuleType('tqdm')
tqdm_module.tqdm = lambda iterable=None, **kwargs: iterable
sys.modules.setdefault('tqdm', tqdm_module)

from teletext.service import Service
from teletext.subpage import Subpage
from teletext.viewer import DirectPageBuffer, ServiceNavigator


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


def make_subpage(magazine, page, subpage, fastext_links=()):
    result = Subpage(prefill=True, magazine=magazine)
    result.mrag.magazine = magazine
    result.header.page = page
    result.header.subpage = subpage
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
