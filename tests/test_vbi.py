import unittest

import numpy as np

from teletext.vbi.config import Config
from teletext.gui.vbituner import parse_signal_controls_args, parse_tuning_args
from teletext.vbi.line import (
    apply_signal_controls,
    process_frame_bytes,
    process_line_bytes,
)


class TestSignalControls(unittest.TestCase):

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
        self.assertGreater(float(amplified.max()), float(samples.max()))
        self.assertGreater(float(np.ptp(contrasted)), float(np.ptp(samples)))

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
        values = parse_signal_controls_args('-bn 55 -sp 66 -gn 57 -ct 61 -bncf 40 -spcf 4.5 -gncf 0.75 -ctcf 1.25')

        self.assertEqual(values, (55, 66, 57, 61, 40.0, 4.5, 0.75, 1.25))

    def test_parse_tuning_args_supports_decoder_controls(self):
        values, decoder_tuning, line_selection, fix_capture_card = parse_tuning_args(
            '-bn 55 -sp 66 -gn 57 -ct 61 -bncf 40 -spcf 4.5 -gncf 0.75 -ctcf 1.25 '
            '-f hd630sp --extra-roll 2 --line-start-range 70 145 -ul 4,5 -fcc 2 3',
            decoder_defaults={'tape_format': 'vhs', 'extra_roll': 0, 'line_start_range': (60, 130)},
            tape_formats=Config.tape_formats,
            line_defaults=frozenset(range(1, 33)),
            fix_capture_card_defaults={'enabled': False, 'seconds': 5, 'interval_minutes': 7, 'device': '/dev/video0'},
        )

        self.assertEqual(values, (55, 66, 57, 61, 40.0, 4.5, 0.75, 1.25))
        self.assertEqual(decoder_tuning, {
            'tape_format': 'hd630sp',
            'extra_roll': 2,
            'line_start_range': (70, 145),
        })
        self.assertEqual(line_selection, frozenset({4, 5}))
        self.assertEqual(fix_capture_card, {
            'enabled': True,
            'seconds': 2,
            'interval_minutes': 3,
            'device': '/dev/video0',
        })

    def test_config_retuned_updates_line_start_range_and_extra_roll(self):
        config = Config(card='bt8x8')

        updated = config.retuned(extra_roll=2, line_start_range=(70, 145))

        self.assertEqual(updated.extra_roll, 2)
        self.assertEqual(updated.line_start_range, (70, 145))
        self.assertEqual(updated.sample_rate, config.sample_rate)
