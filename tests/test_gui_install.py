import os
import tempfile
import unittest
from unittest import mock

from teletext.gui.install import desktop_entry, install_desktop_integration, mime_xml


class TestGuiInstall(unittest.TestCase):
    def test_desktop_entry_uses_requested_exec_command(self):
        entry = desktop_entry(exec_command='ttviewer-test')

        self.assertIn('Exec=ttviewer-test %f', entry)
        self.assertIn('MimeType=application/x-teletext-t42;', entry)

    def test_mime_xml_registers_t42_extension(self):
        xml = mime_xml()

        self.assertIn('application/x-teletext-t42', xml)
        self.assertIn('*.t42', xml)

    def test_install_desktop_integration_writes_user_files(self):
        with tempfile.TemporaryDirectory() as data_home:
            with mock.patch('teletext.gui.install.shutil.which', return_value=None):
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

            self.assertIn('Exec=ttviewer-test %f', desktop_contents)
            self.assertIn('application/x-teletext-t42', mime_contents)
