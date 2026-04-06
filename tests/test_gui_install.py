import os
import sys
import tempfile
import unittest
from unittest import mock

from teletext.gui.install import (
    desktop_entry,
    install_desktop_integration,
    mime_xml,
    resolve_exec_command,
    uninstall_desktop_integration,
)


class TestGuiInstall(unittest.TestCase):
    def test_desktop_entry_uses_requested_exec_command(self):
        entry = desktop_entry(exec_command='ttviewer-test')

        self.assertIn('Exec=ttviewer-test %f', entry)
        self.assertIn('MimeType=application/x-teletext-t42;', entry)

    def test_resolve_exec_command_prefers_absolute_launcher(self):
        with mock.patch('teletext.gui.install.shutil.which', return_value='/home/kot/.local/bin/ttviewer'):
            self.assertEqual(resolve_exec_command('ttviewer'), '/home/kot/.local/bin/ttviewer')

    def test_resolve_exec_command_falls_back_to_sibling_script(self):
        sibling = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'ttviewer')
        with mock.patch('teletext.gui.install.shutil.which', return_value=None):
            with mock.patch('teletext.gui.install.os.path.exists', side_effect=lambda path: path == sibling):
                self.assertEqual(resolve_exec_command('ttviewer'), sibling)

    def test_mime_xml_registers_t42_extension(self):
        xml = mime_xml()

        self.assertIn('application/x-teletext-t42', xml)
        self.assertIn('*.t42', xml)

    def test_install_desktop_integration_writes_user_files(self):
        with tempfile.TemporaryDirectory() as data_home:
            with mock.patch('teletext.gui.install.shutil.which', return_value='/home/kot/.local/bin/ttviewer-test'):
                with mock.patch('teletext.gui.install._run_command'):
                    installed = install_desktop_integration(
                        data_home=data_home,
                        exec_command='ttviewer-test',
                        set_default=False,
                    )

            self.assertTrue(os.path.exists(installed['desktop']))
            self.assertTrue(os.path.exists(installed['mime']))
            self.assertTrue(os.path.exists(installed['icon']))

            with open(installed['desktop'], 'r', encoding='utf-8') as handle:
                desktop_contents = handle.read()
            with open(installed['mime'], 'r', encoding='utf-8') as handle:
                mime_contents = handle.read()

            self.assertIn('Exec=/home/kot/.local/bin/ttviewer-test %f', desktop_contents)
            self.assertIn('application/x-teletext-t42', mime_contents)

    def test_uninstall_desktop_integration_removes_user_files(self):
        with tempfile.TemporaryDirectory() as data_home:
            with mock.patch('teletext.gui.install.shutil.which', return_value='/home/kot/.local/bin/ttviewer-test'):
                with mock.patch('teletext.gui.install._run_command'):
                    installed = install_desktop_integration(
                        data_home=data_home,
                        exec_command='ttviewer-test',
                        set_default=False,
                    )
                    removed = uninstall_desktop_integration(data_home=data_home)

            self.assertEqual(set(removed), {'desktop', 'mime', 'icon'})
            self.assertFalse(os.path.exists(installed['desktop']))
            self.assertFalse(os.path.exists(installed['mime']))
            self.assertFalse(os.path.exists(installed['icon']))
