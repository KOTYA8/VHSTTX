import unittest
import tempfile
import os

import click
from click.testing import CliRunner

import teletext.cli.teletext
import teletext.cli.training
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


class TestCmdVBICrop(TestCommandTeletext):
    cmd = teletext.cli.teletext.vbicrop


class TestCmdT42Crop(TestCommandTeletext):
    cmd = teletext.cli.teletext.t42crop


class TestCmdDeconvolve(TestCommandTeletext):
    cmd = teletext.cli.teletext.deconvolve


class TestSignalControlOptions(TestCommandTeletext):

    def test_record_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.record, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-bncf, --brightness-coeff', result.output)
        self.assertIn('-spcf, --sharpness-coeff', result.output)
        self.assertIn('-gncf, --gain-coeff', result.output)
        self.assertIn('-ctcf, --contrast-coeff', result.output)

    def test_deconvolve_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.deconvolve, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-u, --urxvt', result.output)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-bncf, --brightness-coeff', result.output)
        self.assertIn('-spcf, --sharpness-coeff', result.output)
        self.assertIn('-gncf, --gain-coeff', result.output)
        self.assertIn('-ctcf, --contrast-coeff', result.output)
        self.assertIn('-vtn, --vbi-tune', result.output)
        self.assertIn('-vtnl, --vbi-tune-live', result.output)
        self.assertIn('while deconvolving in the terminal', result.output)

    def test_vbiview_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.vbiview, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-bncf, --brightness-coeff', result.output)
        self.assertIn('-spcf, --sharpness-coeff', result.output)
        self.assertIn('-gncf, --gain-coeff', result.output)
        self.assertIn('-ctcf, --contrast-coeff', result.output)
        self.assertIn('-vtnl, --vbi-tune-live', result.output)

    def test_vbicrop_help_lists_signal_controls(self):
        result = self.runner.invoke(teletext.cli.teletext.vbicrop, ['--help'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('-il, --ignore-line', result.output)
        self.assertIn('-ul, --used-line', result.output)
        self.assertIn('-fcc, --fix-capture-card', result.output)
        self.assertIn('-bn, --brightness', result.output)
        self.assertIn('-sp, --sharpness', result.output)
        self.assertIn('-gn, --gain', result.output)
        self.assertIn('-ct, --contrast', result.output)
        self.assertIn('-vtn, --vbi-tune', result.output)
        self.assertIn('-vtnl, --vbi-tune-live', result.output)

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
