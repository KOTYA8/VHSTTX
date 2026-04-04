import unittest
import tempfile
import os
import types
import unittest.mock

import numpy as np

from teletext.cli.teletext import (
    VBIRepairDiagnostics,
    _count_frame_ranges,
    _repair_header_entries,
    _repair_recent_page_suggestions,
    _repair_recent_subpage_suggestions,
    _parse_repair_page_value,
    _parse_repair_subpage_value,
    _repair_page_suggestions,
    _repair_requested_page_summary,
    _repair_recent_header_summary,
    _repair_subpage_suggestions,
    _configure_repair_lines,
    _render_repair_subpage_text,
    _repair_current_header_summary,
    _merge_frame_ranges,
    _vbicrop_line_looks_erased,
    infer_vbicrop_error_ranges,
    summarise_vbicrop_error_zones,
)
from teletext.subpage import Subpage
from teletext.vbi.config import Config
from teletext.gui.vbituner import (
    ANALYSIS_KIND_AUTO_TUNE,
    ARGS_APPLY_SCOPE_ALL,
    ARGS_APPLY_SCOPE_SELECTED,
    ANALYSIS_MODE_FULL,
    ANALYSIS_MODE_QUICK,
    DEFAULT_CONTROLS,
    _args_text_mentions_line_selection,
    _resolve_args_line_selection,
    analyse_vbi_file,
    _analysis_frame_indices,
    _sample_analysis_preview_lines,
    _section_display_title,
    _section_tooltip,
    auto_lock_preview,
    _normalise_signal_controls_tuple,
    auto_tune_preview,
    load_analysis_history_entry,
    delete_local_preset,
    default_analysis_history_store_path,
    format_decoder_tuning,
    format_tuning_args,
    load_local_preset_entries,
    load_local_presets,
    load_preset_payload,
    load_preset_text,
    parse_decoder_tuning_args,
    parse_signal_controls_args,
    parse_tuning_args,
    save_analysis_history_entry,
    save_local_preset,
    save_local_presets,
    save_preset_text,
    suggest_head_switch_crop,
)
from teletext.gui.vbicrop import advance_playback_position, create_crop_state, selection_end_targets
from teletext.vbi.line import (
    Line,
    apply_signal_controls,
    eye_pattern_clock_stats,
    histogram_black_level_stats,
    process_frame_bytes,
    process_line_bytes,
    process_lines,
    quality_meter_stats,
)


class TestSignalControls(unittest.TestCase):

    class _FakeRepairPacket:
        def __init__(self, magazine, row, page, subpage, text='PACKET', confidence=50.0):
            self.mrag = types.SimpleNamespace(magazine=magazine, row=row)
            self.header = types.SimpleNamespace(page=page, subpage=subpage)
            self._text = text
            self._line_confidence = float(confidence)

        def to_ansi(self, colour=False):
            return self._text

    def test_repair_page_parser_accepts_hex_page(self):
        self.assertEqual(_parse_repair_page_value('1af'), (0x1AF, '1AF'))

    def test_repair_subpage_parser_accepts_hex_subpage(self):
        self.assertEqual(_parse_repair_subpage_value('2a'), (0x002A, '002A'))

    def test_repair_page_renderer_includes_page_and_text(self):
        subpage = Subpage(prefill=True, magazine=1)
        subpage.header.page = 0x23
        subpage.header.subpage = 0x0001
        subpage.packet(1).displayable.place_string('HELLO WORLD')

        rendered = _render_repair_subpage_text(subpage, subpage_count=2, buffered_frames=12)

        self.assertIn('Page P123', rendered)
        self.assertIn('Buffered frames 12', rendered)
        self.assertIn('HELLO WORLD', rendered)

    def test_repair_header_summary_includes_current_page_and_subpage(self):
        packets = (
            self._FakeRepairPacket(1, 0, 0x00, 0x0001),
            self._FakeRepairPacket(1, 3, 0x00, 0x0001),
            self._FakeRepairPacket(2, 0, 0x34, 0x0002),
        )

        summary = _repair_current_header_summary(packets)

        self.assertEqual(summary, 'Current page/subpage: P100/0001, P234/0002')

    def test_repair_recent_header_summary_falls_back_to_history(self):
        current_packets = ()
        frame_history = (
            (6, (self._FakeRepairPacket(3, 0, 0x30, 0x3003),)),
            (7, (self._FakeRepairPacket(3, 0, 0x59, 0x3003),)),
        )

        summary = _repair_recent_header_summary(current_packets, frame_history)

        self.assertEqual(summary, 'Current page/subpage: P359/3003')

    def test_repair_requested_page_summary_prefers_watched_page(self):
        self.assertEqual(_repair_requested_page_summary('359', '3003'), 'Watching page/subpage: P359/3003')
        self.assertEqual(_repair_requested_page_summary('359', ''), 'Watching page/subpage: P359')

    def test_repair_header_entries_keep_current_then_recent_order(self):
        current_packets = (
            self._FakeRepairPacket(3, 0, 0x59, 0x3003),
            self._FakeRepairPacket(3, 0, 0x5A, 0x004E),
        )
        frame_history = (
            (7, (self._FakeRepairPacket(3, 0, 0x30, 0x0001),)),
            (8, current_packets),
        )

        entries = _repair_header_entries(current_packets, frame_history, limit=4)

        self.assertEqual(entries[:3], (('359', '3003'), ('35A', '004E'), ('330', '0001')))

    def test_repair_recent_page_suggestions_prefer_current_headers(self):
        current_packets = (
            self._FakeRepairPacket(3, 0, 0x59, 0x3003),
            self._FakeRepairPacket(3, 0, 0x5A, 0x004E),
        )
        frame_history = (
            (7, (self._FakeRepairPacket(3, 0, 0x30, 0x0001),)),
            (8, current_packets),
        )

        suggestions = _repair_recent_page_suggestions(current_packets, frame_history, limit=4)

        self.assertEqual(suggestions[:3], ('359', '35A', '330'))

    def test_repair_recent_subpage_suggestions_follow_current_page_headers(self):
        current_packets = (
            self._FakeRepairPacket(3, 0, 0x59, 0x3003),
            self._FakeRepairPacket(3, 0, 0x59, 0x3004),
        )
        frame_history = (
            (7, (self._FakeRepairPacket(3, 0, 0x59, 0x0001),)),
            (8, current_packets),
        )

        suggestions = _repair_recent_subpage_suggestions(current_packets, 0x359, frame_history, limit=4)

        self.assertEqual(suggestions[:3], ('3003', '3004', '0001'))

    def test_repair_page_suggestions_include_recent_headers(self):
        current_packets = (
            self._FakeRepairPacket(1, 0, 0x00, 0x0001),
            self._FakeRepairPacket(2, 0, 0x34, 0x0002),
        )
        frame_history = (
            (0, (self._FakeRepairPacket(1, 0, 0x01, 0x0001),)),
            (1, (self._FakeRepairPacket(2, 0, 0x34, 0x0002),)),
        )

        suggestions = _repair_page_suggestions(current_packets, frame_history, limit=4)

        self.assertEqual(suggestions[:3], ('234', '100', '101'))

    def test_repair_subpage_suggestions_include_recent_headers(self):
        current_packets = (
            self._FakeRepairPacket(1, 0, 0x00, 0x0002),
            self._FakeRepairPacket(1, 0, 0x00, 0x0003),
            self._FakeRepairPacket(2, 0, 0x34, 0x0001),
        )
        frame_history = (
            (0, (self._FakeRepairPacket(1, 0, 0x00, 0x0001),)),
            (1, (self._FakeRepairPacket(1, 0, 0x00, 0x0002),)),
        )

        suggestions = _repair_subpage_suggestions(current_packets, 0x100, frame_history, limit=4)

        self.assertEqual(suggestions[:3], ('0002', '0003', '0001'))

    def test_repair_packet_results_can_hide_noisy_lines(self):
        diagnostics = object.__new__(VBIRepairDiagnostics)
        diagnostics._mode = 'deconvolve'
        good_packet = self._FakeRepairPacket(1, 1, 0x00, 0x0001, text='GOOD', confidence=70.0)
        noisy_packet = self._FakeRepairPacket(1, 2, 0x00, 0x0001, text='NOISY', confidence=20.0)
        results = (
            {'logical_line': 1, 'status': 'packet', 'packet': good_packet, 'quality': 70, 'reason': ''},
            {'logical_line': 2, 'status': 'packet', 'packet': noisy_packet, 'quality': 20, 'reason': ''},
            {'logical_line': 3, 'status': 'rejected', 'quality': 10, 'reason': 'no sync'},
        )

        rendered = diagnostics._render_packet_results(
            5,
            results,
            (good_packet, noisy_packet),
            hide_noisy=True,
            quality_threshold=50,
            current_headers='Current page/subpage: P100/0001',
        )

        self.assertIn('Current page/subpage: P100/0001', rendered)
        self.assertIn('GOOD', rendered)
        self.assertNotIn('NOISY', rendered)
        self.assertNotIn('rejected', rendered)

    def test_repair_page_history_rebuilds_after_packets_mode(self):
        diagnostics = VBIRepairDiagnostics('dummy.vbi', lambda: {}, page_history_frames=3)
        diagnostics._signature_for_runtime = lambda runtime: 'test-signature'
        diagnostics._decode_current_frame = lambda frame_index, runtime: (
            (),
            (self._FakeRepairPacket(3, 0, 0x30, 0x0001, text=f'P330 F{frame_index}'),),
        )

        diagnostics._ensure_history(1, {}, require_history=False)
        self.assertEqual([index for index, _ in diagnostics._frame_history], [1])

        diagnostics._ensure_history(1, {}, require_history=True)
        self.assertEqual([index for index, _ in diagnostics._frame_history], [0, 1])

        diagnostics._ensure_history(2, {}, require_history=True)
        self.assertEqual([index for index, _ in diagnostics._frame_history], [0, 1, 2])

    def test_repair_page_renderer_reports_missing_page_without_fallback(self):
        diagnostics = object.__new__(VBIRepairDiagnostics)
        diagnostics._frame_history = ()
        diagnostics._last_page_render_key = None
        diagnostics._last_page_render_text = None

        rendered = diagnostics._render_page_results(
            9,
            '359',
            '3003',
            hide_noisy=False,
            quality_threshold=50,
            current_headers='Current page/subpage: --',
        )

        self.assertIn('Page P359 not assembled yet.', rendered)

    def test_repair_describe_payload_page_mode_uses_requested_page_summary(self):
        diagnostics = object.__new__(VBIRepairDiagnostics)
        diagnostics._runtime = lambda: {'decoder_tuning': {'quality_threshold': 50}}
        diagnostics._ensure_history = lambda *args, **kwargs: None
        diagnostics._last_frame_packets = (self._FakeRepairPacket(3, 0, 0x30, 0x0001),)
        diagnostics._last_frame_results = ()
        diagnostics._frame_history = ()
        diagnostics._render_page_results = lambda *args, **kwargs: 'PAGE VIEW'

        payload = diagnostics.describe_payload(9, view_mode='page', page='359', subpage='3003')

        self.assertEqual(payload['summary'], 'Watching page/subpage: P359/3003\nCurrent page/subpage: P330/0001')
        self.assertEqual(payload['text'], 'PAGE VIEW')

    def test_repair_describe_payload_uses_recent_header_auto_suggestions(self):
        diagnostics = object.__new__(VBIRepairDiagnostics)
        diagnostics._runtime = lambda: {'decoder_tuning': {'quality_threshold': 50}}
        diagnostics._ensure_history = lambda *args, **kwargs: None
        diagnostics._last_frame_packets = (
            self._FakeRepairPacket(3, 0, 0x59, 0x3003),
            self._FakeRepairPacket(3, 0, 0x5A, 0x004E),
        )
        diagnostics._last_frame_results = ()
        diagnostics._frame_history = (
            (7, (self._FakeRepairPacket(3, 0, 0x30, 0x0001),)),
        )
        diagnostics._render_page_results = lambda *args, **kwargs: 'PAGE VIEW'

        payload = diagnostics.describe_payload(9, view_mode='page', page='359', subpage='3003')

        self.assertEqual(payload['page_auto_suggestions'][:3], ('359', '35A', '330'))
        self.assertEqual(payload['subpage_auto_suggestions'][:1], ('3003',))
        self.assertEqual(payload['current_page_entries'][:3], (('359', '3003'), ('35A', '004E'), ('330', '0001')))

    def test_repair_export_page_t42_writes_current_subpage(self):
        diagnostics = object.__new__(VBIRepairDiagnostics)
        diagnostics._runtime = lambda: {'decoder_tuning': {'quality_threshold': 50}}
        diagnostics._ensure_history = lambda *args, **kwargs: None
        subpage = Subpage(prefill=True, magazine=3)
        subpage.header.page = 0x59
        subpage.header.subpage = 0x3003
        subpage.packet(1).displayable.place_string('HELLO WORLD')
        diagnostics._collect_page_subpages = lambda *args, **kwargs: (0x359, '359', 0x3003, '3003', [subpage])

        with tempfile.NamedTemporaryFile(delete=False, suffix='.t42') as handle:
            path = handle.name
        try:
            result = diagnostics.export_page_t42(9, '359', '3003', path, hide_noisy=False)
            with open(path, 'rb') as saved:
                content = saved.read()
            self.assertGreaterEqual(len(content), 42)
            self.assertEqual(result['page'], 0x359)
            self.assertEqual(result['subpage'], 0x3003)
            self.assertEqual(result['subpage_hex'], '3003')
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_parse_decoder_tuning_args_accepts_decimal_per_line_shift(self):
        values = parse_decoder_tuning_args(
            '-pls 6:+1.5 -pls 7:-0.25',
            defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (40, 120),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
                'show_quality': False,
                'show_rejects': False,
                'show_start_clock': False,
                'show_clock_visuals': False,
                'show_alignment_visuals': False,
                'show_quality_meter': False,
                'show_histogram_graph': False,
                'show_eye_pattern': False,
            },
            tape_formats=('vhs',),
        )
        self.assertAlmostEqual(values['per_line_shift'][6], 1.5)
        self.assertAlmostEqual(values['per_line_shift'][7], -0.25)

    def test_format_decoder_tuning_formats_decimal_per_line_shift(self):
        text = format_decoder_tuning({
            'tape_format': 'vhs',
            'extra_roll': 0,
            'line_start_range': (40, 120),
            'quality_threshold': 50,
            'quality_threshold_coeff': 1.0,
            'clock_lock': 50,
            'clock_lock_coeff': 1.0,
            'start_lock': 50,
            'start_lock_coeff': 1.0,
            'adaptive_threshold': 0,
            'adaptive_threshold_coeff': 1.0,
            'dropout_repair': 0,
            'dropout_repair_coeff': 1.0,
            'wow_flutter_compensation': 0,
            'wow_flutter_compensation_coeff': 1.0,
            'auto_line_align': 0,
            'per_line_shift': {6: 1.5, 7: -2.0},
            'show_quality': False,
            'show_rejects': False,
            'show_start_clock': False,
            'show_clock_visuals': False,
            'show_alignment_visuals': False,
            'show_quality_meter': False,
            'show_histogram_graph': False,
            'show_eye_pattern': False,
        }, defaults={
            'tape_format': 'vhs',
            'extra_roll': 0,
            'line_start_range': (40, 120),
            'quality_threshold': 50,
            'quality_threshold_coeff': 1.0,
            'clock_lock': 50,
            'clock_lock_coeff': 1.0,
            'start_lock': 50,
            'start_lock_coeff': 1.0,
            'adaptive_threshold': 0,
            'adaptive_threshold_coeff': 1.0,
            'dropout_repair': 0,
            'dropout_repair_coeff': 1.0,
            'wow_flutter_compensation': 0,
            'wow_flutter_compensation_coeff': 1.0,
            'auto_line_align': 0,
            'per_line_shift': {},
            'show_quality': False,
            'show_rejects': False,
            'show_start_clock': False,
            'show_clock_visuals': False,
            'show_alignment_visuals': False,
            'show_quality_meter': False,
            'show_histogram_graph': False,
            'show_eye_pattern': False,
        })
        self.assertIn('-pls 6:+1.5', text)
        self.assertIn('-pls 7:-2', text)

    def test_parse_and_format_decoder_tuning_supports_clock_and_alignment_visuals(self):
        values = parse_decoder_tuning_args(
            '--show-clock-visuals --show-alignment-visuals',
            defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (40, 120),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
                'show_quality': False,
                'show_rejects': False,
                'show_start_clock': False,
                'show_clock_visuals': False,
                'show_alignment_visuals': False,
                'show_quality_meter': False,
                'show_histogram_graph': False,
                'show_eye_pattern': False,
            },
            tape_formats=('vhs',),
        )

        self.assertTrue(values['show_clock_visuals'])
        self.assertTrue(values['show_alignment_visuals'])

        formatted = format_decoder_tuning(
            values,
            defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (40, 120),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
                'show_quality': False,
                'show_rejects': False,
                'show_start_clock': False,
                'show_clock_visuals': False,
                'show_alignment_visuals': False,
                'show_quality_meter': False,
                'show_histogram_graph': False,
                'show_eye_pattern': False,
            },
        )
        self.assertIn('--show-clock-visuals', formatted)
        self.assertIn('--show-alignment-visuals', formatted)

    def test_selection_end_targets_cover_remaining_timeline(self):
        self.assertEqual(selection_end_targets(0, 101), (0, 50, 100))
        self.assertEqual(selection_end_targets(40, 101), (40, 70, 100))
        self.assertEqual(selection_end_targets(100, 101), (100, 100, 100))

    def test_vbicrop_erased_line_detection_prefers_signal_loss(self):
        line = types.SimpleNamespace(
            is_teletext=False,
            diagnostic_quality=8,
            reject_reason='Signal max is 12.0',
            _signal_max=12.0,
            _noisefloor=9.0,
        )
        self.assertTrue(_vbicrop_line_looks_erased(line))

    def test_merge_frame_ranges_joins_small_gaps(self):
        self.assertEqual(_merge_frame_ranges((1, 2, 4, 8), gap=1), ((1, 4), (8, 8)))

    def test_count_frame_ranges_counts_all_frames(self):
        self.assertEqual(_count_frame_ranges(((2, 3), (8, 10))), 5)

    def test_infer_vbicrop_error_ranges_flags_quality_drop_zone(self):
        frame_metrics = (
            {'frame_index': 0, 'line_count': 8, 'teletext_count': 7, 'mean_quality': 72.0, 'erased_count': 0},
            {'frame_index': 1, 'line_count': 8, 'teletext_count': 7, 'mean_quality': 70.0, 'erased_count': 0},
            {'frame_index': 2, 'line_count': 8, 'teletext_count': 2, 'mean_quality': 18.0, 'erased_count': 4},
            {'frame_index': 3, 'line_count': 8, 'teletext_count': 1, 'mean_quality': 16.0, 'erased_count': 5},
            {'frame_index': 4, 'line_count': 8, 'teletext_count': 7, 'mean_quality': 71.0, 'erased_count': 0},
        )

        self.assertEqual(infer_vbicrop_error_ranges(frame_metrics), ((2, 3),))

    def test_infer_vbicrop_error_ranges_flags_vertical_shift_zone(self):
        frame_metrics = (
            {'frame_index': 0, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 72.0, 'erased_count': 0, 'noisy_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 17},
            {'frame_index': 1, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 71.0, 'erased_count': 0, 'noisy_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 17},
            {'frame_index': 2, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 60.0, 'erased_count': 2, 'noisy_count': 1, 'longest_erased_run': 2, 'first_teletext_line': 8, 'last_teletext_line': 15},
            {'frame_index': 3, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 59.0, 'erased_count': 2, 'noisy_count': 1, 'longest_erased_run': 2, 'first_teletext_line': 8, 'last_teletext_line': 15},
            {'frame_index': 4, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 73.0, 'erased_count': 0, 'noisy_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 17},
        )

        self.assertEqual(infer_vbicrop_error_ranges(frame_metrics), ((2, 3),))

    def test_summarise_vbicrop_error_zones_includes_reason_text(self):
        frame_metrics = (
            {'frame_index': 0, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 72.0, 'erased_count': 0, 'noisy_count': 0, 'severe_noise_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 17},
            {'frame_index': 1, 'line_count': 10, 'teletext_count': 8, 'mean_quality': 71.0, 'erased_count': 0, 'noisy_count': 0, 'severe_noise_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 17},
            {'frame_index': 2, 'line_count': 10, 'teletext_count': 4, 'mean_quality': 42.0, 'erased_count': 4, 'noisy_count': 6, 'severe_noise_count': 4, 'longest_erased_run': 3, 'first_teletext_line': 8, 'last_teletext_line': 15},
            {'frame_index': 3, 'line_count': 10, 'teletext_count': 4, 'mean_quality': 41.0, 'erased_count': 4, 'noisy_count': 5, 'severe_noise_count': 3, 'longest_erased_run': 3, 'first_teletext_line': 8, 'last_teletext_line': 15},
        )

        zones = summarise_vbicrop_error_zones(frame_metrics, ((2, 3),), frame_rate=25.0)

        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0]['start_frame'], 2)
        self.assertEqual(zones[0]['level'], 'bad')
        self.assertEqual(zones[0]['kind'], 'mixed')
        self.assertGreaterEqual(zones[0]['teletext_loss_count'], 0)
        self.assertGreaterEqual(zones[0]['shift_distance'], 0)
        self.assertTrue(zones[0]['has_noise'])
        self.assertIn('noise', zones[0]['reason'])

    def test_summarise_vbicrop_error_zones_marks_critical_for_extreme_noise_and_full_loss(self):
        frame_metrics = (
            {'frame_index': 0, 'line_count': 12, 'teletext_count': 9, 'mean_quality': 74.0, 'erased_count': 0, 'noisy_count': 0, 'severe_noise_count': 0, 'extreme_noise_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 18},
            {'frame_index': 1, 'line_count': 12, 'teletext_count': 0, 'mean_quality': 11.0, 'erased_count': 7, 'noisy_count': 8, 'severe_noise_count': 6, 'extreme_noise_count': 4, 'longest_erased_run': 6, 'first_teletext_line': None, 'last_teletext_line': None},
        )

        zones = summarise_vbicrop_error_zones(frame_metrics, ((1, 1),), frame_rate=25.0)

        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0]['level'], 'critical')
        self.assertEqual(zones[0]['kind'], 'mixed')
        self.assertIn('190+ noise', zones[0]['reason'])
        self.assertIn('teletext loss', zones[0]['reason'])

    def test_summarise_vbicrop_error_zones_keeps_four_line_loss_as_bad(self):
        frame_metrics = (
            {'frame_index': 0, 'line_count': 32, 'teletext_count': 8, 'mean_quality': 74.0, 'erased_count': 0, 'noisy_count': 0, 'severe_noise_count': 0, 'extreme_noise_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 18},
            {'frame_index': 1, 'line_count': 32, 'teletext_count': 8, 'mean_quality': 73.0, 'erased_count': 0, 'noisy_count': 0, 'severe_noise_count': 0, 'extreme_noise_count': 0, 'longest_erased_run': 0, 'first_teletext_line': 10, 'last_teletext_line': 18},
            {'frame_index': 2, 'line_count': 32, 'teletext_count': 4, 'mean_quality': 20.0, 'erased_count': 28, 'noisy_count': 1, 'severe_noise_count': 0, 'extreme_noise_count': 0, 'longest_erased_run': 15, 'first_teletext_line': 10, 'last_teletext_line': 14},
        )

        zones = summarise_vbicrop_error_zones(frame_metrics, ((2, 2),), frame_rate=25.0)

        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0]['level'], 'bad')
        self.assertIn('teletext loss 4 lines', zones[0]['reason'])

    def test_advance_playback_position_supports_reverse(self):
        self.assertEqual(advance_playback_position(10, 3, 100, 1), (13, False))
        self.assertEqual(advance_playback_position(10, 3, 100, -1), (7, False))
        self.assertEqual(advance_playback_position(1, 5, 100, -1), (0, True))
        self.assertEqual(advance_playback_position(98, 5, 100, 1), (99, True))

    def test_crop_state_tracks_playback_speed_and_direction(self):
        state = create_crop_state(total_frames=10, playback_speed=1.5, playback_direction=-1)

        self.assertEqual(state.playback_speed(), 1.5)
        self.assertEqual(state.playback_direction(), -1)
        state.toggle_playback(direction=-1)
        self.assertTrue(state.is_playing())
        self.assertEqual(state.playback_direction(), -1)
        state.set_playback_speed(2.4)
        self.assertEqual(state.playback_speed(), 2.4)

    def test_neutral_controls_leave_samples_unchanged(self):
        samples = np.array([12, 48, 96, 144, 192, 220], dtype=np.float32)

        adjusted = apply_signal_controls(samples)

        np.testing.assert_array_equal(adjusted, samples)

    def test_brightness_gain_and_contrast_change_signal(self):
        samples = np.array([32, 64, 96, 128, 160, 192], dtype=np.float32)

        brighter = apply_signal_controls(samples, brightness=70)
        amplified = apply_signal_controls(samples, gain=70)
        contrasted = apply_signal_controls(samples, contrast=70)

        self.assertGreater(float(brighter.mean()), float(samples.mean()))

    def test_process_lines_accepts_live_decoder_tuning_with_per_line_shift(self):
        config = Config(card='bt8x8')
        captured = []
        original_line = process_lines.__globals__['Line']

        class DummyLine:
            @staticmethod
            def configure(*args, **kwargs):
                captured.append(dict(kwargs))

            @staticmethod
            def set_signal_controls(**kwargs):
                return None

            def __init__(self, chunk, number):
                self.chunk = chunk
                self.number = number

            def slice(self, mags, rows, eight_bit):
                return ('ok', self.number, len(self.chunk))

        decoder_values = {
            'tape_format': 'vhs',
            'extra_roll': 1,
            'line_start_range': (60, 130),
            'quality_threshold': 50,
            'quality_threshold_coeff': 1.0,
            'clock_lock': 50,
            'clock_lock_coeff': 1.0,
            'start_lock': 50,
            'start_lock_coeff': 1.0,
            'adaptive_threshold': 0,
            'adaptive_threshold_coeff': 1.0,
            'dropout_repair': 0,
            'dropout_repair_coeff': 1.0,
            'wow_flutter_compensation': 0,
            'wow_flutter_compensation_coeff': 1.0,
            'auto_line_align': 0,
            'per_line_shift': {4: 3},
        }
        try:
            process_lines.__globals__['Line'] = DummyLine
            result = next(process_lines(
                [(0, b'\x00' * config.line_bytes)],
                'slice',
                config,
                decoder_tuning=lambda: decoder_values,
            ))
        finally:
            process_lines.__globals__['Line'] = original_line

        self.assertEqual(result, ('ok', 0, config.line_bytes))
        self.assertTrue(captured)
        self.assertIn('per_line_shift', captured[-1])
        self.assertEqual(captured[-1]['per_line_shift'], {4: 3})
        self.assertGreater(float(amplified.max()), float(samples.max()))
        self.assertGreater(float(np.ptp(contrasted)), float(np.ptp(samples)))

    def test_noise_reduction_changes_signal(self):
        samples = np.array([20, 180, 24, 176, 28, 172, 32, 168], dtype=np.float32)

        reduced = apply_signal_controls(samples, noise_reduction=80)

        self.assertFalse(np.array_equal(reduced, samples))
        self.assertLess(float(np.ptp(np.diff(reduced))), float(np.ptp(np.diff(samples))))

    def test_impulse_filter_reduces_single_spike(self):
        samples = np.array([48, 50, 49, 210, 51, 50, 49], dtype=np.float32)

        filtered = apply_signal_controls(samples, impulse_filter=100)

        self.assertLess(float(filtered[3]), float(samples[3]))
        self.assertLess(float(abs(filtered[3] - np.median(samples[[2, 3, 4]]))), float(abs(samples[3] - np.median(samples[[2, 3, 4]]))))

    def test_impulse_filter_coefficient_changes_strength(self):
        samples = np.array([48, 50, 49, 210, 51, 50, 49], dtype=np.float32)

        milder = apply_signal_controls(samples, impulse_filter=100, impulse_filter_coeff=0.5)
        stronger = apply_signal_controls(samples, impulse_filter=100, impulse_filter_coeff=2.0)

        self.assertLess(float(abs(stronger[3] - np.median(samples[[2, 3, 4]]))), float(abs(milder[3] - np.median(samples[[2, 3, 4]]))))

    def test_noise_reduction_coefficient_changes_strength(self):
        samples = np.array([20, 180, 24, 176, 28, 172, 32, 168], dtype=np.float32)

        milder = apply_signal_controls(samples, noise_reduction=80, noise_reduction_coeff=0.5)
        stronger = apply_signal_controls(samples, noise_reduction=80, noise_reduction_coeff=2.0)

        self.assertLess(float(np.ptp(np.diff(stronger))), float(np.ptp(np.diff(milder))))

    def test_auto_black_level_reduces_background_offset(self):
        config = Config(card='bt8x8')
        samples = np.full((config.line_length,), 90, dtype=np.float32)
        samples[config.start_slice.start:config.start_slice.start + 32] = 150

        adjusted = apply_signal_controls(samples, auto_black_level=100, config=config)

        self.assertLess(float(np.mean(adjusted[:config.start_slice.start])), float(np.mean(samples[:config.start_slice.start])))

    def test_sharpness_changes_edge_profile(self):
        samples = np.array(([40] * 8) + ([80] * 8) + ([180] * 8) + ([220] * 8), dtype=np.float32)

        sharpened = apply_signal_controls(samples, sharpness=80)
        softened = apply_signal_controls(samples, sharpness=20)

        self.assertFalse(np.array_equal(sharpened, samples))
        self.assertFalse(np.array_equal(softened, samples))
        self.assertGreater(float(np.ptp(np.diff(sharpened))), float(np.ptp(np.diff(softened))))

    def test_sharpness_coefficient_changes_strength(self):
        samples = np.array(([40] * 8) + ([80] * 8) + ([180] * 8) + ([220] * 8), dtype=np.float32)

        milder = apply_signal_controls(samples, sharpness=80, sharpness_coeff=1.5)
        stronger = apply_signal_controls(samples, sharpness=80, sharpness_coeff=4.5)

        self.assertGreater(float(np.ptp(np.diff(stronger))), float(np.ptp(np.diff(milder))))

    def test_sharpness_coefficient_changes_softening_strength(self):
        samples = np.array(([40] * 8) + ([80] * 8) + ([180] * 8) + ([220] * 8), dtype=np.float32)

        milder = apply_signal_controls(samples, sharpness=0, sharpness_coeff=1.5)
        stronger = apply_signal_controls(samples, sharpness=0, sharpness_coeff=6.0)

        self.assertLess(float(np.ptp(np.diff(stronger))), float(np.ptp(np.diff(milder))))

    def test_process_line_bytes_round_trips_neutral_settings(self):
        config = Config(card='bt8x8')
        raw = bytes(range(32))

        adjusted = process_line_bytes(raw, config)

        self.assertEqual(adjusted, raw)

    def test_process_frame_bytes_preserves_bt8x8_tail(self):
        config = Config(card='bt8x8')
        frame_size = config.line_length * config.field_lines * 2
        tail = b'\x11\x22\x33\x44'
        frame = (b'\x60' * (frame_size - len(tail))) + tail

        adjusted = process_frame_bytes(frame, config, brightness=70, preserve_tail=len(tail))

        self.assertEqual(adjusted[-len(tail):], tail)
        self.assertNotEqual(adjusted[:-len(tail)], frame[:-len(tail)])

    def test_parse_signal_controls_args_supports_short_options(self):
        values = parse_signal_controls_args(
            '-bn 55/40 -sp 66/4.5 -gn 57/0.75 -ct 61/1.25 '
            '-if 11/1.25 -td 21/0.75 -nr 22/1.5 -hm 33/2.5 -abl 44/3.5 '
            '-hsm 12/1.75 -lls 13/0.5 -agc 14/2.0'
        )

        self.assertEqual(
            values,
            (
                55, 66, 57, 61,
                40.0, 4.5, 0.75, 1.25,
                11, 21, 22, 33, 44, 12, 13, 14,
                1.25, 0.75, 1.5, 2.5, 3.5, 1.75, 0.5, 2.0,
            ),
        )

    def test_parse_signal_controls_args_supports_value_only_defaults(self):
        values = parse_signal_controls_args('-nr 10 -sp 60')

        self.assertEqual(values[1], 60)
        self.assertEqual(values[5], 3.0)
        self.assertEqual(values[10], 10)
        self.assertEqual(values[18], 1.0)

    def test_legacy_signal_control_tuple_is_expanded_for_new_controls(self):
        expanded = _normalise_signal_controls_tuple((55, 66, 57, 61, 40.0, 4.5, 0.75, 1.25, 11, 22, 33, 44, 1.25, 1.5, 2.5, 3.5))

        self.assertEqual(len(expanded), 24)
        self.assertEqual(expanded[8], 11)
        self.assertEqual(expanded[9], 0)
        self.assertEqual(expanded[13], 1.25)
        self.assertEqual(expanded[16], 2.5)
        self.assertEqual(expanded[21], 1.0)

    def test_parse_decoder_tuning_args_supports_auto_line_align_and_per_line_shift(self):
        values = parse_decoder_tuning_args(
            '--line-quality 60 --clock-lock 70 --auto-line-align 55 -pls 4:+3 -pls 6:-2',
            defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (0, 0),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
            },
            tape_formats=['vhs'],
        )

        self.assertEqual(values['quality_threshold'], 60)
        self.assertEqual(values['clock_lock'], 70)
        self.assertEqual(values['auto_line_align'], 55)
        self.assertEqual(values['per_line_shift'], {4: 3, 6: -2})

    def test_parse_decoder_tuning_args_supports_short_extra_roll_and_line_start_range(self):
        values = parse_decoder_tuning_args(
            '-er 2 -lsr 70 145',
            defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (60, 130),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
            },
            tape_formats=['vhs'],
        )

        self.assertEqual(values['extra_roll'], 2)
        self.assertEqual(values['line_start_range'], (70, 145))

    def test_format_decoder_tuning_includes_auto_align_and_per_line_shift(self):
        text = format_decoder_tuning({
            'tape_format': 'vhs',
            'extra_roll': 0,
            'line_start_range': (10, 20),
            'quality_threshold': 50,
            'clock_lock': 50,
            'start_lock': 50,
            'adaptive_threshold': 0,
            'dropout_repair': 0,
            'wow_flutter_compensation': 0,
            'auto_line_align': 42,
            'per_line_shift': {3: 2, 5: -1},
        })

        self.assertIn('-ala 42', text)
        self.assertIn('-pls 3:+2', text)
        self.assertIn('-pls 5:-1', text)

    def test_format_decoder_tuning_omits_defaults(self):
        text = format_decoder_tuning(
            {
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (70, 145),
                'quality_threshold': 50,
                'clock_lock': 50,
                'start_lock': 50,
                'adaptive_threshold': 0,
                'dropout_repair': 0,
                'wow_flutter_compensation': 0,
                'auto_line_align': 0,
                'per_line_shift': {},
            },
            defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (70, 145),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
            },
        )

        self.assertEqual(text, '')

    def test_format_tuning_args_omits_default_signal_controls(self):
        text = format_tuning_args(
            DEFAULT_CONTROLS,
            decoder_tuning={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (70, 145),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
                'show_quality': False,
                'show_rejects': False,
                'show_start_clock': False,
                'show_quality_meter': False,
                'show_histogram_graph': False,
                'show_eye_pattern': False,
            },
            decoder_defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (70, 145),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'clock_lock_coeff': 1.0,
                'start_lock': 50,
                'start_lock_coeff': 1.0,
                'adaptive_threshold': 0,
                'adaptive_threshold_coeff': 1.0,
                'dropout_repair': 0,
                'dropout_repair_coeff': 1.0,
                'wow_flutter_compensation': 0,
                'wow_flutter_compensation_coeff': 1.0,
                'auto_line_align': 0,
                'per_line_shift': {},
                'show_quality': False,
                'show_rejects': False,
                'show_start_clock': False,
                'show_quality_meter': False,
                'show_histogram_graph': False,
                'show_eye_pattern': False,
            },
            line_selection=frozenset(range(1, 33)),
            fix_capture_card={'enabled': False, 'seconds': 2, 'interval_minutes': 3, 'device': '/dev/video0'},
        )

        self.assertEqual(text, '')

    def test_configure_repair_lines_passes_auto_align_and_per_line_shift(self):
        config = Config(card='bt8x8')
        with unittest.mock.patch('teletext.vbi.line.Line.configure') as configure_mock:
            _configure_repair_lines(
                config,
                'vhs',
                DEFAULT_CONTROLS,
                {
                    'quality_threshold': 50,
                    'quality_threshold_coeff': 1.0,
                    'clock_lock': 50,
                    'clock_lock_coeff': 1.0,
                    'start_lock': 50,
                    'start_lock_coeff': 1.0,
                    'adaptive_threshold': 0,
                    'adaptive_threshold_coeff': 1.0,
                    'dropout_repair': 0,
                    'dropout_repair_coeff': 1.0,
                    'wow_flutter_compensation': 0,
                    'wow_flutter_compensation_coeff': 1.0,
                    'auto_line_align': 37,
                    'per_line_shift': {4: 3},
                },
            )

        kwargs = configure_mock.call_args.kwargs
        self.assertEqual(kwargs['auto_line_align'], 37)
        self.assertEqual(kwargs['per_line_shift'], {4: 3})

    def test_auto_line_align_moves_start_toward_recent_history(self):
        line = object.__new__(Line)
        line.auto_line_align = 100
        line._start = 130
        line._temporal_state = {'_auto_line_align_recent': [100.0, 102.0, 101.0]}
        line.temporal_line_count = 32

        Line.apply_auto_line_align(line)

        self.assertLess(line._start, 130)

    def test_per_line_shift_applies_manual_shift_for_logical_line(self):
        line = object.__new__(Line)
        line._start = 120
        line._number = 4
        line.temporal_line_count = 32
        line._resampled = np.zeros((2048,), dtype=np.float32)
        line.per_line_shift_map = {5: -3}

        Line.apply_per_line_shift(line)

        self.assertEqual(line._start, 117)

    def test_parse_tuning_args_supports_decoder_controls(self):
        values, decoder_tuning, line_selection, fix_capture_card = parse_tuning_args(
            '-bn 55/40 -sp 66/4.5 -gn 57/0.75 -ct 61/1.25 '
            '-if 11/1.25 -td 21/0.75 -nr 22/1.5 -hm 33/2.5 -abl 44/3.5 '
            '-hsm 12/1.75 -lls 13/0.5 -agc 14/2.0 '
            '-f hd630sp --extra-roll 2 --line-start-range 70 145 --line-quality 61 --clock-lock 68 --start-lock 62 --adaptive-threshold 73 --dropout-repair 37 --auto-line-align 54 -pls 4:+3 -pls 8:-2 '
            '--show-quality --show-rejects --show-start-clock '
            '-ul 4,5 -fcc 2 3',
            decoder_defaults={'tape_format': 'vhs', 'extra_roll': 0, 'line_start_range': (60, 130), 'quality_threshold': 50, 'quality_threshold_coeff': 1.0, 'clock_lock': 50, 'start_lock': 50, 'adaptive_threshold': 0, 'dropout_repair': 0, 'show_quality': False, 'show_rejects': False, 'show_start_clock': False},
            tape_formats=Config.tape_formats,
            line_defaults=frozenset(range(1, 33)),
            fix_capture_card_defaults={'enabled': False, 'seconds': 5, 'interval_minutes': 7, 'device': '/dev/video0'},
        )

        self.assertEqual(
            values,
            (
                55, 66, 57, 61,
                40.0, 4.5, 0.75, 1.25,
                11, 21, 22, 33, 44, 12, 13, 14,
                1.25, 0.75, 1.5, 2.5, 3.5, 1.75, 0.5, 2.0,
            ),
        )
        self.assertEqual(decoder_tuning, {
            'tape_format': 'hd630sp',
            'extra_roll': 2,
            'line_start_range': (70, 145),
            'quality_threshold': 61,
            'quality_threshold_coeff': 1.0,
            'clock_lock': 68,
            'clock_lock_coeff': 1.0,
            'start_lock': 62,
            'start_lock_coeff': 1.0,
            'adaptive_threshold': 73,
            'adaptive_threshold_coeff': 1.0,
            'dropout_repair': 37,
            'dropout_repair_coeff': 1.0,
            'wow_flutter_compensation': 0,
            'wow_flutter_compensation_coeff': 1.0,
            'auto_line_align': 54,
            'per_line_shift': {4: 3, 8: -2},
            'show_quality': True,
            'show_rejects': True,
            'show_start_clock': True,
            'show_clock_visuals': False,
            'show_alignment_visuals': False,
            'show_quality_meter': False,
            'show_histogram_graph': False,
            'show_eye_pattern': False,
        })
        self.assertEqual(line_selection, frozenset({4, 5}))
        self.assertEqual(fix_capture_card, {
            'enabled': True,
            'seconds': 2,
            'interval_minutes': 3,
            'device': '/dev/video0',
        })

    def test_parse_tuning_args_supports_negative_diagnostic_flags(self):
        _, decoder_tuning, _, _ = parse_tuning_args(
            '--no-show-quality --no-show-rejects --no-show-start-clock',
            decoder_defaults={
                'tape_format': 'vhs',
                'extra_roll': 0,
                'line_start_range': (60, 130),
                'quality_threshold': 50,
                'quality_threshold_coeff': 1.0,
                'clock_lock': 50,
                'start_lock': 50,
                'adaptive_threshold': 0,
                'dropout_repair': 0,
                'show_quality': True,
                'show_rejects': True,
                'show_start_clock': True,
            },
            tape_formats=Config.tape_formats,
            line_defaults=frozenset(range(1, 33)),
            fix_capture_card_defaults={'enabled': False, 'seconds': 5, 'interval_minutes': 7, 'device': '/dev/video0'},
        )

        self.assertFalse(decoder_tuning['show_quality'])
        self.assertFalse(decoder_tuning['show_rejects'])
        self.assertFalse(decoder_tuning['show_start_clock'])

    def test_analysis_frame_indices_support_quick_and_full(self):
        self.assertEqual(_analysis_frame_indices(0, ANALYSIS_MODE_QUICK, 10), ())
        self.assertEqual(_analysis_frame_indices(4, ANALYSIS_MODE_FULL, 2), (0, 1, 2, 3))

        indices = _analysis_frame_indices(10, ANALYSIS_MODE_QUICK, 3)

        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 9)
        self.assertEqual(len(indices), 3)

    def test_analyse_vbi_file_auto_tune_uses_sampled_frames(self):
        config = Config(card='bt8x8')
        frame_size = config.line_bytes * config.field_lines * 2
        decoder_defaults = {
            'tape_format': 'vhs',
            'extra_roll': 0,
            'line_start_range': (60, 130),
            'quality_threshold': 50,
            'quality_threshold_coeff': 1.0,
            'clock_lock': 50,
            'clock_lock_coeff': 1.0,
            'start_lock': 50,
            'start_lock_coeff': 1.0,
            'adaptive_threshold': 0,
            'adaptive_threshold_coeff': 1.0,
            'dropout_repair': 0,
            'dropout_repair_coeff': 1.0,
            'wow_flutter_compensation': 0,
            'wow_flutter_compensation_coeff': 1.0,
            'auto_line_align': 0,
            'per_line_shift': {},
            'show_quality': False,
            'show_rejects': False,
            'show_start_clock': False,
            'show_quality_meter': False,
            'show_histogram_graph': False,
            'show_eye_pattern': False,
        }
        fd, path = tempfile.mkstemp()
        os.close(fd)
        progress = []
        sampled_line_counts = []
        original = analyse_vbi_file.__globals__['auto_tune_preview']
        try:
            with open(path, 'wb') as handle:
                handle.write((b'\x55' * frame_size) * 6)

            def fake_auto(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None, progress_callback=None):
                sampled_line_counts.append(len(preview_lines))
                updated = list(controls)
                updated[10] = 33
                decoder = dict(decoder_tuning)
                decoder['clock_lock'] = 67
                if callable(progress_callback):
                    progress_callback(2, 4, 'Evaluating Auto Tune')
                return tuple(updated), decoder, {
                    'score': 123.0,
                    'teletext_lines': 10,
                    'analysed_lines': len(preview_lines),
                }

            analyse_vbi_file.__globals__['auto_tune_preview'] = fake_auto
            result = analyse_vbi_file(
                ANALYSIS_KIND_AUTO_TUNE,
                path,
                config,
                'vhs',
                DEFAULT_CONTROLS,
                decoder_tuning=decoder_defaults,
                mode=ANALYSIS_MODE_QUICK,
                quick_frames=3,
                progress_callback=progress.append,
            )
        finally:
            analyse_vbi_file.__globals__['auto_tune_preview'] = original
            os.unlink(path)

        self.assertEqual(result['controls'][10], 33)
        self.assertEqual(result['decoder_tuning']['clock_lock'], 67)
        self.assertEqual(result['frames_analysed'], 3)
        self.assertTrue(sampled_line_counts)
        self.assertLessEqual(sampled_line_counts[0], 256)
        self.assertTrue(any(item['phase'] == 'Scanning frames' for item in progress))
        self.assertTrue(any(item['phase'] == 'Evaluating Auto Tune' for item in progress))
        self.assertEqual(progress[-1]['percent'], 100)

    def test_sample_analysis_preview_lines_limits_work(self):
        preview_lines = tuple((index, bytes([index % 256])) for index in range(2000))

        sampled = _sample_analysis_preview_lines(preview_lines, limit=100)

        self.assertEqual(len(sampled), 100)
        self.assertEqual(sampled[0], preview_lines[0])
        self.assertEqual(sampled[-1], preview_lines[-1])

    def test_section_display_titles_mark_vbi_and_deconvolve_areas(self):
        self.assertEqual(_section_display_title('Signal Controls'), 'Signal Controls (VBI)')
        self.assertEqual(_section_display_title('Decoder Tuning'), 'Decoder Tuning (Deconvolve)')
        self.assertIn('captured signal', _section_tooltip('Signal Controls'))

    def test_args_line_scope_detects_line_selection_flags(self):
        self.assertTrue(_args_text_mentions_line_selection('-ul 4,5 -bn 55'))
        self.assertTrue(_args_text_mentions_line_selection('--ignore-line 7'))
        self.assertFalse(_args_text_mentions_line_selection('-bn 55 -sp 60'))

    def test_args_line_scope_can_force_all_or_selected_line(self):
        parsed = frozenset((4, 5))

        self.assertEqual(
            _resolve_args_line_selection(parsed, '-bn 55', ARGS_APPLY_SCOPE_ALL, 7, line_count=8),
            frozenset(range(1, 9)),
        )
        self.assertEqual(
            _resolve_args_line_selection(parsed, '-ul 4,5', ARGS_APPLY_SCOPE_ALL, 7, line_count=8),
            frozenset((4, 5)),
        )
        self.assertEqual(
            _resolve_args_line_selection(parsed, '-ul 4,5', ARGS_APPLY_SCOPE_SELECTED, 7, line_count=8),
            frozenset((7,)),
        )

    def test_auto_lock_preview_prefers_stable_clock_and_start(self):
        class DummyLine:
            def __init__(self, start, teletext=True):
                self.start = start
                self.is_teletext = teletext

        original_build = auto_lock_preview.__globals__['_build_preview_line_objects']
        original_evaluate = auto_lock_preview.__globals__['_evaluate_preview_candidate']
        try:
            def fake_build(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None):
                return [
                    DummyLine(100),
                    DummyLine(101),
                    DummyLine(99),
                    DummyLine(None, teletext=False),
                ], None, {'analysed_lines': 4, 'teletext_lines': 3}

            def fake_evaluate(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None):
                clock_score = 100 - abs(int(decoder_tuning['clock_lock']) - 80)
                start_score = 100 - abs(int(decoder_tuning['start_lock']) - 65)
                centre = sum(int(value) for value in decoder_tuning['line_start_range']) / 2.0
                centre_score = 100 - abs(centre - 100.0)
                total = float((clock_score * 4) + (start_score * 3) + centre_score)
                return total, 3, 4

            auto_lock_preview.__globals__['_build_preview_line_objects'] = fake_build
            auto_lock_preview.__globals__['_evaluate_preview_candidate'] = fake_evaluate
            decoder_tuning, stats = auto_lock_preview(
                preview_lines=((0, b'line0'),),
                config=object(),
                tape_format='vhs',
                controls=(50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
                decoder_tuning={
                    'tape_format': 'vhs',
                    'extra_roll': 0,
                    'line_start_range': (60, 130),
                    'quality_threshold': 50,
                    'quality_threshold_coeff': 1.0,
                    'clock_lock': 50,
                    'start_lock': 50,
                    'adaptive_threshold': 0,
                    'dropout_repair': 0,
                    'wow_flutter_compensation': 0,
                    'show_quality': False,
                    'show_rejects': False,
                    'show_start_clock': False,
                    'show_clock_visuals': False,
                    'show_alignment_visuals': False,
                    'show_quality_meter': False,
                    'show_histogram_graph': False,
                    'show_eye_pattern': False,
                },
                line_selection=frozenset({1, 2, 3}),
            )
        finally:
            auto_lock_preview.__globals__['_build_preview_line_objects'] = original_build
            auto_lock_preview.__globals__['_evaluate_preview_candidate'] = original_evaluate

        self.assertEqual(decoder_tuning['clock_lock'], 80)
        self.assertEqual(decoder_tuning['start_lock'], 65)
        self.assertLessEqual(decoder_tuning['line_start_range'][0], 100)
        self.assertGreaterEqual(decoder_tuning['line_start_range'][1], 100)
        self.assertEqual(stats['teletext_lines'], 3)

    def test_suggest_head_switch_crop_detects_noisy_tail(self):
        class DummyLine:
            def __init__(self, values, teletext=True):
                self.resampled = np.asarray(values, dtype=np.float32)
                self.is_teletext = teletext

        original_build = suggest_head_switch_crop.__globals__['_build_preview_line_objects']
        try:
            def fake_build(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None):
                clean = np.concatenate((np.full(160, 80, dtype=np.float32), np.linspace(82, 90, 24, dtype=np.float32)))
                noisy_tail = np.concatenate((np.full(160, 80, dtype=np.float32), np.array([40, 120] * 12, dtype=np.float32)))
                return [DummyLine(clean), DummyLine(noisy_tail)], None, {'analysed_lines': 2, 'teletext_lines': 2}

            suggest_head_switch_crop.__globals__['_build_preview_line_objects'] = fake_build
            suggested, stats = suggest_head_switch_crop(
                preview_lines=((0, b'line0'),),
                config=object(),
                tape_format='vhs',
                controls=(50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
            )
        finally:
            suggest_head_switch_crop.__globals__['_build_preview_line_objects'] = original_build

        self.assertGreater(suggested, 0)
        self.assertEqual(stats['teletext_lines'], 2)

    def test_quality_histogram_and_eye_helpers_return_stats(self):
        class DummyLine:
            def __init__(self, values, start=8, teletext=True, quality=70):
                self.resampled = np.asarray(values, dtype=np.float32)
                self.start = start
                self.is_teletext = teletext
                self.diagnostic_quality = quality

        lines = [
            DummyLine(np.linspace(20, 220, 64), start=8, teletext=True, quality=72),
            DummyLine(np.linspace(30, 210, 64), start=8, teletext=True, quality=68),
            DummyLine(np.linspace(40, 200, 64), start=8, teletext=False, quality=32),
        ]
        config = types.SimpleNamespace(start_slice=slice(8, 16))

        quality = quality_meter_stats(lines)
        histogram = histogram_black_level_stats(lines, config=config, bins=16)
        eye = eye_pattern_clock_stats(lines, width=32)

        self.assertEqual(quality['teletext_lines'], 2)
        self.assertEqual(quality['rejects'], 1)
        self.assertGreater(quality['average_quality'], 0.0)
        self.assertEqual(histogram['histogram'].shape[0], 16)
        self.assertGreaterEqual(histogram['black_level'], 0.0)
        self.assertIsNotNone(eye)
        self.assertEqual(len(eye['average']), 32)
        self.assertEqual(eye['segment_count'], 2)

    def test_temporal_denoise_blends_same_line_across_frames(self):
        base = np.array([48, 60, 72, 84, 96, 108, 120, 132], dtype=np.float32)
        noisy_first = base + np.array([0, 18, -12, 10, -8, 14, -6, 0], dtype=np.float32)
        noisy_second = base + np.array([0, -16, 10, -12, 8, -10, 6, 0], dtype=np.float32)
        temporal_state = {}

        first = apply_signal_controls(
            noisy_first,
            temporal_denoise=100,
            temporal_denoise_coeff=1.0,
            temporal_state=temporal_state,
            temporal_key=7,
        )
        second = apply_signal_controls(
            noisy_second,
            temporal_denoise=100,
            temporal_denoise_coeff=1.0,
            temporal_state=temporal_state,
            temporal_key=7,
        )

        self.assertTrue(np.allclose(first, noisy_first))
        self.assertLess(float(np.mean(np.abs(second - base))), float(np.mean(np.abs(noisy_second - base))))

    def test_auto_tune_preview_prefers_more_teletext_lines(self):
        class FakeConfig:
            def __init__(self):
                self.extra_roll = 0
                self.line_start_range = (0, 0)

            def retuned(self, extra_roll=0, line_start_range=(0, 0)):
                updated = FakeConfig()
                updated.extra_roll = int(extra_roll)
                updated.line_start_range = tuple(line_start_range)
                return updated

        original_evaluate = auto_tune_preview.__globals__['_evaluate_preview_candidate']
        try:
            def fake_evaluate(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None):
                teletext_lines = 5 + int(controls[8] >= 30) + int(controls[9] >= 15) + int(controls[10] >= 15)
                score = float(teletext_lines * 1000)
                return score, teletext_lines, len(preview_lines)

            auto_tune_preview.__globals__['_evaluate_preview_candidate'] = fake_evaluate
            values, decoder_tuning, stats = auto_tune_preview(
                preview_lines=((0, b'line0'), (1, b'line1')),
                config=FakeConfig(),
                tape_format='vhs',
                controls=(
                    50, 50, 50, 50,
                    48.0, 3.0, 0.5, 0.5,
                    0, 0, 0, 0, 0, 0, 0, 0,
                    1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
                ),
                decoder_tuning=None,
                line_selection=frozenset({1, 2}),
            )
        finally:
            auto_tune_preview.__globals__['_evaluate_preview_candidate'] = original_evaluate

        self.assertEqual(len(values), 24)
        self.assertGreaterEqual(values[8], 30)
        self.assertGreaterEqual(values[9], 15)
        self.assertGreaterEqual(values[10], 15)
        self.assertIsNone(decoder_tuning)
        self.assertGreaterEqual(stats['teletext_lines'], 8)

    def test_adaptive_threshold_changes_local_normalisation(self):
        dummy = object.__new__(Line)
        samples = np.array([42, 48, 56, 64, 72, 160, 84, 92, 100, 108, 116, 124], dtype=np.float32)

        dummy.adaptive_threshold = 0
        global_only = dummy.adaptive_normalise(samples)

        dummy.adaptive_threshold = 100
        adaptive = dummy.adaptive_normalise(samples)

        self.assertEqual(global_only.dtype, np.float32)
        self.assertEqual(adaptive.dtype, np.float32)
        self.assertFalse(np.allclose(global_only, adaptive))

    def test_start_lock_biases_rough_start_toward_expected_range(self):
        dummy = object.__new__(Line)
        dummy.config = types.SimpleNamespace(start_slice=slice(100, 130))
        dummy._gstart = np.zeros((30,), dtype=np.float32)
        dummy._gstart[2] = 12.0
        dummy._gstart[10:21] = np.linspace(0.0, 20.0, 11, dtype=np.float32)

        dummy.start_lock = 50
        neutral = dummy.rough_start_from_gradient()

        dummy.start_lock = 0
        unlocked = dummy.rough_start_from_gradient()

        dummy.start_lock = 100
        locked = dummy.rough_start_from_gradient()

        expected = int(round((dummy.config.start_slice.start + dummy.config.start_slice.stop) / 2.0))
        self.assertLess(unlocked, neutral)
        self.assertLess(abs(locked - expected), abs(neutral - expected))

    def test_dropout_repair_reduces_isolated_drop(self):
        dummy = object.__new__(Line)
        dummy.dropout_repair = 100
        samples = np.array([198, 202, 205, 44, 201, 204, 200], dtype=np.float32)

        repaired = dummy.repair_dropouts(samples)
        target = float(np.median(samples[1:6]))

        self.assertGreater(float(repaired[3]), float(samples[3]))
        self.assertLess(abs(float(repaired[3]) - target), abs(float(samples[3]) - target))

    def test_wow_flutter_compensation_stabilizes_start(self):
        state = {}
        Line.temporal_line_count = 32

        first = object.__new__(Line)
        first.wow_flutter_compensation = 100
        first._start = 120
        first._number = 4
        first._temporal_state = state
        first.apply_wow_flutter_compensation()

        second = object.__new__(Line)
        second.wow_flutter_compensation = 100
        second._start = 140
        second._number = 4
        second._temporal_state = state
        second.apply_wow_flutter_compensation()

        self.assertEqual(first._start, 120)
        self.assertLess(second._start, 140)

    def test_config_retuned_updates_line_start_range_and_extra_roll(self):
        config = Config(card='bt8x8')

        updated = config.retuned(extra_roll=2, line_start_range=(70, 145))

        self.assertEqual(updated.extra_roll, 2)
        self.assertEqual(updated.line_start_range, (70, 145))
        self.assertEqual(updated.sample_rate, config.sample_rate)

    def test_preset_text_round_trip(self):
        fd, path = tempfile.mkstemp(suffix='.vtnargs')
        os.close(fd)
        try:
            save_preset_text(path, '-bn 55 -sp 66 -ul 4,5 -fcc 2 3')
            self.assertEqual(load_preset_text(path), '-bn 55 -sp 66 -ul 4,5 -fcc 2 3')
        finally:
            os.unlink(path)

    def test_preset_loader_ignores_comments(self):
        fd, path = tempfile.mkstemp(suffix='.vtnargs')
        os.close(fd)
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('# comment\n')
                handle.write('-bn 55 -sp 66\n')
                handle.write('  --extra-roll 2\n')
            self.assertEqual(load_preset_text(path), '-bn 55 -sp 66 --extra-roll 2')
        finally:
            os.unlink(path)

    def test_preset_payload_round_trip_preserves_disabled_functions(self):
        fd, path = tempfile.mkstemp(suffix='.vtnargs')
        os.close(fd)
        try:
            save_preset_text(path, '-bn 55 -sp 66 -ul 4,5', disabled_functions=('noise_reduction', 'clock_lock'))
            self.assertEqual(load_preset_text(path), '-bn 55 -sp 66 -ul 4,5')
            self.assertEqual(load_preset_payload(path), {
                'args': '-bn 55 -sp 66 -ul 4,5',
                'disabled_functions': ('noise_reduction', 'clock_lock'),
            })
        finally:
            os.unlink(path)

    def test_local_presets_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'presets.json')
            save_local_preset('Tape A', '-bn 55 -sp 66', path=path)
            save_local_preset('Tape B', '-bn 44 -sp 33 -fcc 2 3', path=path)

            self.assertEqual(load_local_presets(path), {
                'Tape A': '-bn 55 -sp 66',
                'Tape B': '-bn 44 -sp 33 -fcc 2 3',
            })

    def test_local_preset_entries_preserve_disabled_functions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'presets.json')
            save_local_preset(
                'Tape A',
                '-bn 55 -sp 66',
                path=path,
                disabled_functions=('noise_reduction', 'adaptive_threshold'),
            )

            self.assertEqual(load_local_preset_entries(path), {
                'Tape A': {
                    'args': '-bn 55 -sp 66',
                    'disabled_functions': ('noise_reduction', 'adaptive_threshold'),
                },
            })

    def test_delete_local_preset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'presets.json')
            save_local_presets({
                'Tape A': '-bn 55 -sp 66',
                'Tape B': '-bn 44 -sp 33',
            }, path=path)

            delete_local_preset('Tape A', path=path)

            self.assertEqual(load_local_presets(path), {
                'Tape B': '-bn 44 -sp 33',
            })

    def test_analysis_history_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = default_analysis_history_store_path(base_dir=tmpdir)
            result = {
                'kind': ANALYSIS_KIND_AUTO_TUNE,
                'title': 'Auto Tune',
                'mode': ANALYSIS_MODE_QUICK,
                'input_path': os.path.join(tmpdir, 'sample.vbi'),
                'total_frames': 100,
                'frames_analysed': 25,
                'controls': DEFAULT_CONTROLS,
                'decoder_tuning': {
                    'tape_format': 'vhs',
                    'extra_roll': 0,
                    'line_start_range': (60, 130),
                    'quality_threshold': 50,
                    'quality_threshold_coeff': 1.0,
                    'clock_lock': 67,
                    'clock_lock_coeff': 1.0,
                    'start_lock': 61,
                    'start_lock_coeff': 1.0,
                    'adaptive_threshold': 20,
                    'adaptive_threshold_coeff': 1.0,
                    'dropout_repair': 0,
                    'dropout_repair_coeff': 1.0,
                    'wow_flutter_compensation': 0,
                    'wow_flutter_compensation_coeff': 1.0,
                    'auto_line_align': 0,
                    'per_line_shift': {4: 2},
                    'show_quality': False,
                    'show_rejects': False,
                    'show_start_clock': False,
                    'show_quality_meter': False,
                    'show_histogram_graph': False,
                    'show_eye_pattern': False,
                },
                'line_selection': (4, 5),
                'stats': {'score': 123.0},
                'summary': 'Saved suggestion',
            }

            save_analysis_history_entry(
                ANALYSIS_KIND_AUTO_TUNE,
                result['input_path'],
                ANALYSIS_MODE_QUICK,
                250,
                result,
                path=path,
            )

            entry = load_analysis_history_entry(
                ANALYSIS_KIND_AUTO_TUNE,
                result['input_path'],
                path=path,
            )

            self.assertEqual(entry['mode'], ANALYSIS_MODE_QUICK)
            self.assertEqual(entry['quick_frames'], 250)
            self.assertEqual(entry['result']['summary'], 'Saved suggestion')
            self.assertEqual(entry['result']['decoder_tuning']['clock_lock'], 67)
            self.assertEqual(entry['result']['line_selection'], (4, 5))
