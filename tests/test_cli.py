import unittest
import tempfile
import os
from unittest import mock

import click
from click.testing import CliRunner

import teletext.cli.teletext
import teletext.cli.training
import teletext.gui.vbituner
from teletext.cli.livepause import PauseController
from teletext.vbi.config import Config


class TestCommandTeletext(unittest.TestCase):
    cmd = teletext.cli.teletext.teletext

    def setUp(self):
        self.runner = CliRunner()

    def test_help(self):
        result = self.runner.invoke(self.cmd, ['--help'])
        self.assertEqual(result.exit_code, 0)


class TestCmdFilter(TestCommandTeletext):
    cmd = teletext.cli.teletext.filter


class TestCmdDiff(TestCommandTeletext):
    cmd = teletext.cli.teletext.diff


class TestCmdFinders(TestCommandTeletext):
    cmd = teletext.cli.teletext.finders


class TestCmdSquash(TestCommandTeletext):
    cmd = teletext.cli.teletext.squash

    def test_help_lists_squash_modes(self):
        result = self.runner.invoke(self.cmd, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-md, --mode', result.output)
        self.assertIn('v1', result.output)
        self.assertIn('v3', result.output)
        self.assertIn('auto', result.output)


class TestCmdSpellcheck(TestCommandTeletext):
    cmd = teletext.cli.teletext.spellcheck


class TestCmdSpellcheckAnalyze(TestCommandTeletext):
    cmd = teletext.cli.teletext.spellcheck_analyze


class TestCmdService(TestCommandTeletext):
    cmd = teletext.cli.teletext.service


class TestCmdInteractive(TestCommandTeletext):
    cmd = teletext.cli.teletext.interactive


class TestCmdUrls(TestCommandTeletext):
    cmd = teletext.cli.teletext.urls


class TestCmdHtml(TestCommandTeletext):
    cmd = teletext.cli.teletext.html


class TestCmdRecord(TestCommandTeletext):
    cmd = teletext.cli.teletext.record


class TestCmdVBIView(TestCommandTeletext):
    cmd = teletext.cli.teletext.vbiview


class TestCmdVBIReset(TestCommandTeletext):
    cmd = teletext.cli.teletext.vbireset


class TestCmdVBICrop(TestCommandTeletext):
    cmd = teletext.cli.teletext.vbicrop


class TestCmdVBITool(TestCommandTeletext):
    cmd = teletext.cli.teletext.vbitool


class TestCmdVBIRepair(TestCommandTeletext):
    cmd = teletext.cli.teletext.vbirepair


class TestCmdT42Crop(TestCommandTeletext):
    cmd = teletext.cli.teletext.t42crop


class TestCmdT42Tool(TestCommandTeletext):
    cmd = teletext.cli.teletext.t42tool


class TestCmdDeconvolve(TestCommandTeletext):
    cmd = teletext.cli.teletext.deconvolve


class TestSignalControlOptions(TestCommandTeletext):

    def test_record_help_omits_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.record, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-vt, --vbi-terminate-reset', result.output)
        self.assertIn('-vs, --vbi-start', result.output)
        self.assertIn('--vbi-count', result.output)
        self.assertIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-tm, --timer', result.output)
        self.assertNotIn('-bn, --brightness', result.output)
        self.assertNotIn('-sp, --sharpness', result.output)
        self.assertNotIn('-gn, --gain', result.output)
        self.assertNotIn('-ct, --contrast', result.output)
        self.assertNotIn('-if, --impulse-filter', result.output)
        self.assertNotIn('-td, --temporal-denoise', result.output)
        self.assertNotIn('-nr, --noise-reduction', result.output)
        self.assertNotIn('-hm, --hum-removal', result.output)
        self.assertNotIn('-abl, --auto-black-level', result.output)
        self.assertNotIn('-hsm, --head-switching-mask', result.output)
        self.assertNotIn('-lls, --line-to-line-stabilization', result.output)
        self.assertNotIn('-agc, --auto-gain-contrast', result.output)
        self.assertNotIn('VALUE[/COEFF]', result.output)

    def test_deconvolve_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.deconvolve, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-u, --urxvt', result.output)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-vt, --vbi-terminate-reset', result.output)
        self.assertIn('-vs, --vbi-start', result.output)
        self.assertIn('-vc, --vbi-count', result.output)
        self.assertIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-tm, --timer', result.output)
        self.assertIn('-t, --threads', result.output)
        self.assertIn('--threads', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-if, --impulse-filter', result.output)
        self.assertIn('-td, --temporal-denoise', result.output)
        self.assertIn('-nr, --noise-reduction', result.output)
        self.assertIn('-hm, --hum-removal', result.output)
        self.assertIn('-abl, --auto-black-level', result.output)
        self.assertIn('-lq, --line-quality', result.output)
        self.assertIn('-er, --extra-roll', result.output)
        self.assertIn('-lsr, --line-start-range', result.output)
        self.assertIn('-cl, --clock-lock', result.output)
        self.assertIn('-sl, --start-lock', result.output)
        self.assertIn('-at, --adaptive-threshold', result.output)
        self.assertIn('-dr, --dropout-repair', result.output)
        self.assertIn('-wf, --wow-flutter-compensation', result.output)
        self.assertIn('-ala, --auto-line-align', result.output)
        self.assertIn('-pls, --per-line-shift', result.output)
        self.assertIn('--show-quality', result.output)
        self.assertIn('--show-rejects', result.output)
        self.assertIn('--show-start-clock', result.output)
        self.assertIn('--show-clock-visuals', result.output)
        self.assertIn('--show-alignment-visuals', result.output)
        self.assertIn('--show-quality-meter', result.output)
        self.assertIn('--show-histogram-graph', result.output)
        self.assertIn('--show-eye-pattern', result.output)
        self.assertIn('--duplicate-consensus', result.output)
        self.assertIn('--per-line-confidence', result.output)
        self.assertIn('--best-of-n-page-rebuild', result.output)
        self.assertIn('VALUE[/COEFF]', result.output)
        self.assertNotIn('-lqcf, --line-quality-coeff', result.output)
        self.assertNotIn('-ifcf, --impulse-filter-coeff', result.output)
        self.assertNotIn('-bncf, --brightness-coeff', result.output)
        self.assertIn('-vtn, --vbi-tune', result.output)
        self.assertIn('-vtnl, --vbi-tune-live', result.output)
        self.assertIn('while deconvolving in the terminal', result.output)

    def test_vbiview_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.vbiview, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-vt, --vbi-terminate-reset', result.output)
        self.assertIn('-vs, --vbi-start', result.output)
        self.assertIn('-vc, --vbi-count', result.output)
        self.assertNotIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-if, --impulse-filter', result.output)
        self.assertIn('-td, --temporal-denoise', result.output)
        self.assertIn('-nr, --noise-reduction', result.output)
        self.assertIn('-hm, --hum-removal', result.output)
        self.assertIn('-abl, --auto-black-level', result.output)
        self.assertIn('-lq, --line-quality', result.output)
        self.assertIn('-er, --extra-roll', result.output)
        self.assertIn('-lsr, --line-start-range', result.output)
        self.assertIn('-cl, --clock-lock', result.output)
        self.assertIn('-sl, --start-lock', result.output)
        self.assertIn('-at, --adaptive-threshold', result.output)
        self.assertIn('-dr, --dropout-repair', result.output)
        self.assertIn('-wf, --wow-flutter-compensation', result.output)
        self.assertIn('-ala, --auto-line-align', result.output)
        self.assertIn('-pls, --per-line-shift', result.output)
        self.assertIn('--show-quality', result.output)
        self.assertIn('--show-rejects', result.output)
        self.assertIn('--show-start-clock', result.output)
        self.assertIn('--show-clock-visuals', result.output)
        self.assertIn('--show-alignment-visuals', result.output)
        self.assertIn('--show-quality-meter', result.output)
        self.assertIn('--show-histogram-graph', result.output)
        self.assertIn('--show-eye-pattern', result.output)
        self.assertIn('VALUE[/COEFF]', result.output)
        self.assertNotIn('-lqcf, --line-quality-coeff', result.output)
        self.assertNotIn('-ifcf, --impulse-filter-coeff', result.output)
        self.assertNotIn('-bncf, --brightness-coeff', result.output)
        self.assertIn('-vtnl, --vbi-tune-live', result.output)

    def test_vbicrop_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.vbicrop, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertNotIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-if, --impulse-filter', result.output)
        self.assertIn('-td, --temporal-denoise', result.output)
        self.assertIn('-nr, --noise-reduction', result.output)
        self.assertIn('-hm, --hum-removal', result.output)
        self.assertIn('-abl, --auto-black-level', result.output)
        self.assertIn('-lq, --line-quality', result.output)
        self.assertIn('-er, --extra-roll', result.output)
        self.assertIn('-lsr, --line-start-range', result.output)
        self.assertIn('-cl, --clock-lock', result.output)
        self.assertIn('-sl, --start-lock', result.output)
        self.assertIn('-at, --adaptive-threshold', result.output)
        self.assertIn('-dr, --dropout-repair', result.output)
        self.assertIn('-wf, --wow-flutter-compensation', result.output)
        self.assertIn('-ala, --auto-line-align', result.output)
        self.assertIn('-pls, --per-line-shift', result.output)
        self.assertIn('--show-quality', result.output)
        self.assertIn('--show-rejects', result.output)
        self.assertIn('--show-start-clock', result.output)
        self.assertIn('--show-clock-visuals', result.output)
        self.assertIn('--show-alignment-visuals', result.output)
        self.assertIn('--show-quality-meter', result.output)
        self.assertIn('--show-histogram-graph', result.output)
        self.assertIn('--show-eye-pattern', result.output)
        self.assertIn('VALUE[/COEFF]', result.output)
        self.assertNotIn('-lqcf, --line-quality-coeff', result.output)
        self.assertNotIn('-ifcf, --impulse-filter-coeff', result.output)
        self.assertNotIn('-bncf, --brightness-coeff', result.output)
        self.assertIn('-vtn, --vbi-tune', result.output)
        self.assertNotIn('-vtnl, --vbi-tune-live', result.output)

    def test_vbirepair_help_lists_repair_options(self):
        result = self.runner.invoke(teletext.cli.teletext.vbirepair, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-M, --mode', result.output)
        self.assertIn('-8, --eight-bit', result.output)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-if, --impulse-filter', result.output)
        self.assertIn('-td, --temporal-denoise', result.output)
        self.assertIn('-hsm, --head-switching-mask', result.output)
        self.assertIn('-agc, --auto-gain-contrast', result.output)
        self.assertIn('-ala, --auto-line-align', result.output)
        self.assertIn('-pls, --per-line-shift', result.output)
        self.assertIn('--show-clock-visuals', result.output)
        self.assertIn('--show-alignment-visuals', result.output)
        self.assertIn('--show-quality-meter', result.output)
        self.assertIn('--show-histogram-graph', result.output)
        self.assertIn('--show-eye-pattern', result.output)
        self.assertIn('-vtn, --vbi-tune', result.output)
        self.assertIn('-vtnl, --vbi-tune-live', result.output)
        self.assertIn('VALUE[/COEFF]', result.output)

    def test_record_help_lists_tuning_dialog_option(self):
        result = self.runner.invoke(teletext.cli.teletext.record, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-vtn, --vbi-tune', result.output)


class TestIgnoreLineHelpers(unittest.TestCase):

    def test_parse_ignore_lines_accepts_csv_and_repeated_options(self):
        result = teletext.cli.teletext.parse_ignore_lines(None, None, ('23,24', '25'))
        self.assertEqual(result, (23, 24, 25))

    def test_parse_used_lines_accepts_csv_and_repeated_options(self):
        result = teletext.cli.teletext.parse_used_lines(None, None, ('4,5', '6'))
        self.assertEqual(result, (4, 5, 6))

    def test_normalise_ignore_lines_rejects_out_of_range_values(self):
        with self.assertRaises(click.BadParameter):
            teletext.cli.teletext.normalise_ignore_lines((33,), Config())

    def test_normalise_used_lines_rejects_out_of_range_values(self):
        with self.assertRaises(click.BadParameter):
            teletext.cli.teletext.normalise_used_lines((33,), Config())

    def test_resolve_frame_line_selection_from_used_lines(self):
        config = Config()
        selected, ignored = teletext.cli.teletext.resolve_frame_line_selection(config, used_lines=(4, 5))

        self.assertEqual(selected, frozenset({4, 5}))
        self.assertIn(1, ignored)
        self.assertNotIn(4, ignored)
        self.assertNotIn(5, ignored)

    def test_resolve_frame_line_selection_combines_used_and_ignore_lines(self):
        config = Config()
        selected, ignored = teletext.cli.teletext.resolve_frame_line_selection(
            config,
            ignore_lines=(5,),
            used_lines=(4, 5, 6),
        )

        self.assertEqual(selected, frozenset({4, 6}))
        self.assertIn(5, ignored)
        self.assertNotIn(4, ignored)
        self.assertNotIn(6, ignored)

    def test_filter_ignored_chunks_skips_lines_within_each_frame(self):
        config = Config()
        chunks = list(enumerate(range(40)))
        filtered = list(teletext.cli.teletext.filter_ignored_chunks(chunks, config, {23, 24, 25}))
        numbers = [number for number, _ in filtered]

        self.assertNotIn(22, numbers)
        self.assertNotIn(23, numbers)
        self.assertNotIn(24, numbers)
        self.assertIn(21, numbers)
        self.assertIn(25, numbers)

    def test_filter_ignored_chunks_can_keep_only_used_lines(self):
        config = Config()
        chunks = list(enumerate(range(40)))
        _, ignored = teletext.cli.teletext.resolve_frame_line_selection(config, used_lines=(4, 5))
        filtered = teletext.cli.teletext.filter_ignored_chunks(chunks, config, ignored)
        numbers = [number for number, _ in filtered]

        self.assertEqual(numbers, [3, 4, 35, 36])


class TestUrxvtHelpers(unittest.TestCase):

    def test_build_urxvt_command_strips_flag_and_keeps_args(self):
        command = teletext.cli.teletext.build_urxvt_command([
            'teletext',
            'deconvolve',
            '-u',
            '-p',
            '100',
            'test.vbi',
        ])

        self.assertEqual(command[:10], [
            'urxvt',
            '-fg', 'white',
            '-bg', 'black',
            '-fn', 'teletext',
            '-fb', 'teletext',
            '-e',
        ])
        self.assertEqual(command[10:], ['teletext', 'deconvolve', '-p', '100', 'test.vbi'])


class TestPauseHelpers(unittest.TestCase):

    def test_pause_controller_wrap_iterable_preserves_items(self):
        controller = PauseController(enabled=False)
        controller.set_paused(True)
        controller.set_paused(False)

        self.assertEqual(list(controller.wrap_iterable([1, 2, 3])), [1, 2, 3])

    def test_parse_timer_value_accepts_hms_triplet(self):
        value = teletext.cli.teletext.parse_timer_value(None, None, ('1h', '2m', '3s'))
        self.assertEqual(value, 3723)

    def test_parse_timer_value_accepts_seconds_only(self):
        value = teletext.cli.teletext.parse_timer_value(None, None, ('20s',))
        self.assertEqual(value, 20)

    def test_parse_timer_value_accepts_minutes_and_seconds(self):
        value = teletext.cli.teletext.parse_timer_value(None, None, ('1m', '20s'))
        self.assertEqual(value, 80)

    def test_parse_timer_value_rejects_missing_suffix(self):
        with self.assertRaises(click.BadParameter):
            teletext.cli.teletext.parse_timer_value(None, None, ('1', '2m', '3s'))

    def test_timer_option_accepts_variable_value_count(self):
        @click.command()
        @teletext.cli.teletext.timerparam
        def cmd(timer):
            click.echo(str(timer))

        runner = CliRunner()

        result = runner.invoke(cmd, ['-tm', '20s'])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.strip(), '20')

        result = runner.invoke(cmd, ['-tm', '1m', '20s'])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.strip(), '80')


class TestVBICropHelpers(unittest.TestCase):

    def test_estimate_vbi_size_megabytes_matches_frame_size(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        expected = (frame_size * 2) / (1024 * 1024)

        actual = teletext.cli.teletext.estimate_vbi_size_megabytes(2, config)

        self.assertAlmostEqual(actual, expected)

    def test_save_edited_vbi_removes_multiple_cut_ranges(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.vbi')
            output_path = os.path.join(tmpdir, 'output.vbi')
            with open(input_path, 'wb') as input_file:
                input_file.write((b'\x60' * frame_size) * 5)

            teletext.cli.teletext.save_edited_vbi(
                input_path=input_path,
                output_path=output_path,
                config=config,
                controls=controls,
                cut_ranges=((1, 1), (3, 4)),
            )

            self.assertEqual(os.path.getsize(output_path), frame_size * 2)

    def test_save_edited_vbi_inserts_file_after_selected_frame(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.vbi')
            insert_path = os.path.join(tmpdir, 'insert.vbi')
            output_path = os.path.join(tmpdir, 'output.vbi')
            with open(input_path, 'wb') as input_file:
                input_file.write((b'\x60' * frame_size) * 2)
            with open(insert_path, 'wb') as insert_file:
                insert_file.write(b'\x70' * frame_size)

            teletext.cli.teletext.save_edited_vbi(
                input_path=input_path,
                output_path=output_path,
                config=config,
                controls=controls,
                insertions=({'after_frame': 0, 'path': insert_path, 'frame_count': 1},),
            )

            self.assertEqual(os.path.getsize(output_path), frame_size * 3)

    def test_config_with_vbi_capture_format_updates_field_count(self):
        config = Config(card='bt8x8')
        updated = teletext.cli.teletext.config_with_vbi_capture_format(config, {
            'sampling_rate': 35468950,
            'offset': 244,
            'samples_per_line': 2048,
            'sample_format': 0,
            'start': (7, 320),
            'count': (17, 17),
            'flags': 0,
        })

        self.assertEqual(updated.field_lines, 17)
        self.assertEqual(tuple(updated.field_range), tuple(range(17)))

    def test_config_with_vbi_capture_format_rejects_mismatched_counts(self):
        config = Config(card='bt8x8')
        with self.assertRaises(click.UsageError):
            teletext.cli.teletext.config_with_vbi_capture_format(config, {
                'sampling_rate': 35468950,
                'offset': 244,
                'samples_per_line': 2048,
                'sample_format': 0,
                'start': (7, 320),
                'count': (17, 16),
                'flags': 0,
            })

    def test_restore_vbi_capture_format_path_retries_busy_device(self):
        capture_format = {
            'sampling_rate': 35468950,
            'offset': 244,
            'samples_per_line': 2048,
            'sample_format': 0,
            'start': (7, 320),
            'count': (17, 17),
            'flags': 0,
        }

        with mock.patch(
            'teletext.cli.teletext.set_vbi_capture_format_path',
            side_effect=[
                click.UsageError('v4l2-ctl failed for /dev/vbi0: VIDIOC_S_FMT: failed: Device or resource busy'),
                capture_format,
            ],
        ) as set_format, mock.patch('teletext.cli.teletext.time.sleep') as sleep:
            restored = teletext.cli.teletext.restore_vbi_capture_format_path('/dev/vbi0', capture_format, retries=2, delay=0.01)

        self.assertTrue(restored)
        self.assertEqual(set_format.call_count, 2)
        sleep.assert_called_once_with(0.01)

    def test_vbireset_restores_bt878_defaults(self):
        current_format = {
            'sampling_rate': 35468950,
            'offset': 244,
            'samples_per_line': 2048,
            'sample_format': 0,
            'start': (8, 321),
            'count': (17, 17),
            'flags': 0,
        }
        applied_format = dict(current_format)
        applied_format['start'] = teletext.cli.teletext.BT8X8_DEFAULT_VBI_START
        applied_format['count'] = teletext.cli.teletext.BT8X8_DEFAULT_VBI_COUNT

        with tempfile.TemporaryDirectory() as tmpdir:
            device_path = os.path.join(tmpdir, 'vbi0')
            with open(device_path, 'wb'):
                pass

            with mock.patch('teletext.cli.teletext.os.name', 'posix'), \
                 mock.patch('teletext.cli.teletext.get_vbi_capture_format_path', return_value=current_format) as get_format, \
                 mock.patch('teletext.cli.teletext.set_vbi_capture_format_path', return_value=applied_format) as set_format:
                result = CliRunner().invoke(teletext.cli.teletext.vbireset, ['-d', device_path])

        self.assertEqual(result.exit_code, 0)
        self.assertIn('restored VBI start/count', result.output)
        get_format.assert_called_once_with(device_path)
        set_format.assert_called_once()

    def test_live_tuner_decoder_tuning_does_not_leak_line_selection_into_per_line_shift(self):
        line_count = 32
        total_slots = (
            teletext.gui.vbituner._line_decoder_override_offset(line_count)
            + teletext.gui.vbituner.LINE_OVERRIDE_DECODER_SLOT_COUNT
        )
        shared_values = [0.0] * total_slots
        line_offset = teletext.gui.vbituner._line_selection_offset()
        for line in range(1, line_count + 1):
            shared_values[line_offset + line - 1] = 1.0

        class _DummyProcess:
            def is_alive(self):
                return False

        handle = teletext.gui.vbituner.LiveTunerHandle(
            _DummyProcess(),
            shared_values,
            tape_formats=['vhs'],
            line_count=line_count,
        )

        self.assertEqual(handle.decoder_tuning()['per_line_shift'], {})


    def test_processed_frame_for_output_respects_line_control_overrides(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        frame = (b'\x60' * frame_size)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        baseline = teletext.cli.teletext._processed_frame_for_output(
            frame,
            config,
            controls,
        )
        overridden = teletext.cli.teletext._processed_frame_for_output(
            frame,
            config,
            controls,
            decoder_tuning=teletext.cli.teletext.current_decoder_tuning(
                config,
                'vhs',
                line_control_overrides={1: (60, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)},
            ),
        )

        self.assertEqual(len(baseline), frame_size)
        self.assertEqual(len(overridden), frame_size)
        self.assertNotEqual(baseline, overridden)

    def test_stabilize_repair_vbi_preserves_file_size(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.vbi')
            output_path = os.path.join(tmpdir, 'stabilized.vbi')
            with open(input_path, 'wb') as input_file:
                input_file.write((b'\x60' * frame_size) * 3)

            teletext.cli.teletext.stabilize_repair_vbi(
                input_path=input_path,
                output_path=output_path,
                config=config,
                tape_format='vhs',
                controls=controls,
            )

            self.assertEqual(os.path.getsize(output_path), frame_size * 3)

    def test_stabilize_repair_vbi_quick_preview_limits_frame_count(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.vbi')
            output_path = os.path.join(tmpdir, 'preview.vbi')
            with open(input_path, 'wb') as input_file:
                input_file.write((b'\x60' * frame_size) * 4)

            teletext.cli.teletext.stabilize_repair_vbi(
                input_path=input_path,
                output_path=output_path,
                config=config,
                tape_format='vhs',
                controls=controls,
                start_frame=1,
                frame_count=2,
            )

            self.assertEqual(os.path.getsize(output_path), frame_size * 2)

    def test_stabilize_repair_vbi_reference_line_mode_preserves_file_size(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.vbi')
            output_path = os.path.join(tmpdir, 'reference-stabilized.vbi')
            with open(input_path, 'wb') as input_file:
                input_file.write((b'\x60' * frame_size) * 3)

            teletext.cli.teletext.stabilize_repair_vbi(
                input_path=input_path,
                output_path=output_path,
                config=config,
                tape_format='vhs',
                controls=controls,
                lock_mode='reference',
                reference_line=1,
                target_right_edge=0,
            )

            self.assertEqual(os.path.getsize(output_path), frame_size * 3)

    def test_build_reference_stabilize_analysis_marks_gap_left_as_needs_repair(self):
        analysis = teletext.cli.teletext._build_reference_stabilize_analysis(
            1,
            3,
            {
                1: {'left': 100.0, 'right': 200.0},
                13: {'left': 105.0, 'right': 205.0},
                26: {'left': 120.0, 'right': 180.0},
            },
        )

        self.assertEqual(analysis['reference_left'], 100.0)
        self.assertEqual(analysis['reference_right'], 200.0)
        self.assertEqual(analysis['per_line'][1]['status'], 'ok')
        self.assertEqual(analysis['per_line'][13]['shift'], -5)
        self.assertEqual(analysis['per_line'][13]['status'], 'ok')
        self.assertEqual(analysis['per_line'][26]['raw_shift'], 20)
        self.assertEqual(analysis['per_line'][26]['shift'], -5)
        self.assertEqual(analysis['per_line'][26]['status'], 'needs-repair')
        self.assertGreater(analysis['per_line'][26]['gap_left'], 3.0)

    def test_stabilize_repair_vbi_reference_line_mode_returns_analysis(self):
        config = Config(card='bt8x8')
        frame_size = teletext.cli.teletext.frame_size_for_config(config)
        controls = (50, 50, 50, 50, 48.0, 3.0, 0.5, 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, 'input.vbi')
            output_path = os.path.join(tmpdir, 'reference-analysis.vbi')
            with open(input_path, 'wb') as input_file:
                input_file.write((b'\x60' * frame_size) * 2)

            analysis = teletext.cli.teletext.stabilize_repair_vbi(
                input_path=input_path,
                output_path=output_path,
                config=config,
                tape_format='vhs',
                controls=controls,
                lock_mode='reference',
                reference_line=1,
                tolerance=3,
            )

            self.assertIsInstance(analysis, dict)
            self.assertEqual(analysis['reference_line'], 1)
            self.assertEqual(analysis['tolerance'], 3.0)

    def test_build_reference_stabilize_analysis_smooths_outlier_shift(self):
        analysis = teletext.cli.teletext._build_reference_stabilize_analysis(
            11,
            3,
            {
                11: {'left': 100.0, 'right': 200.0},
                12: {'left': 160.0, 'right': 260.0},
                13: {'left': 110.0, 'right': 210.0},
            },
        )

        self.assertEqual(analysis['per_line'][11]['shift'], 0)
        self.assertEqual(analysis['per_line'][12]['raw_shift'], -60)
        self.assertEqual(analysis['per_line'][12]['status'], 'needs-repair')
        self.assertEqual(analysis['per_line'][12]['shift'], -5)
        self.assertEqual(analysis['per_line'][13]['shift'], -10)

    def test_build_reference_stabilize_analysis_median_mode_uses_stable_lines(self):
        analysis = teletext.cli.teletext._build_reference_stabilize_analysis(
            1,
            3,
            {
                1: {'left': 100.0, 'right': 200.0},
                2: {'left': 101.0, 'right': 201.0},
                3: {'left': 99.0, 'right': 199.0},
                12: {'left': 160.0, 'right': 260.0},
                26: {'left': 97.0, 'right': 197.0},
            },
            reference_mode='median',
        )

        self.assertEqual(analysis['reference_mode'], 'median')
        self.assertEqual(analysis['reference_lines'], [1, 2, 3, 26])
        self.assertAlmostEqual(analysis['reference_right'], 199.5)
        self.assertAlmostEqual(analysis['reference_width'], 100.0)
        self.assertNotIn(12, analysis['reference_lines'])


class TestCmdTraining(TestCommandTeletext):
    cmd = teletext.cli.training.training


class TestCmdGenerate(TestCommandTeletext):
    cmd = teletext.cli.training.generate


class TestCmdTrainingSquash(TestCommandTeletext):
    cmd = teletext.cli.training.training_squash


class TestCmdShowBin(TestCommandTeletext):
    cmd = teletext.cli.training.showbin


class TestCmdBuild(TestCommandTeletext):
    cmd = teletext.cli.training.build
