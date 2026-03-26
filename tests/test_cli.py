import unittest

import click
from click.testing import CliRunner

import teletext.cli.teletext
import teletext.cli.training
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


class TestCmdDeconvolve(TestCommandTeletext):
    cmd = teletext.cli.teletext.deconvolve


class TestIgnoreLineHelpers(unittest.TestCase):

    def test_parse_ignore_lines_accepts_csv_and_repeated_options(self):
        result = teletext.cli.teletext.parse_ignore_lines(None, None, ('23,24', '25'))
        self.assertEqual(result, (23, 24, 25))

    def test_normalise_ignore_lines_rejects_out_of_range_values(self):
        with self.assertRaises(click.BadParameter):
            teletext.cli.teletext.normalise_ignore_lines((33,), Config())

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
