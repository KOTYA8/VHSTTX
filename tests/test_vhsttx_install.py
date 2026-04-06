import os
import sys
import tempfile
import unittest
from unittest import mock

from teletext.gui.vhsttx_install import (
    desktop_entry,
    install_desktop_integration,
    resolve_exec_command,
    uninstall_desktop_integration,
)


class TestVHSTTXInstall(unittest.TestCase):
    def test_desktop_entry_uses_requested_exec_command(self):
        entry = desktop_entry(exec_command='vhsttx-test')
        self.assertIn('Exec=vhsttx-test', entry)
        self.assertIn('Name=VHSTTX', entry)

    def test_resolve_exec_command_prefers_absolute_launcher(self):
        with mock.patch('teletext.gui.vhsttx_install.shutil.which', return_value='/home/kot/.local/bin/vhsttx'):
            self.assertEqual(resolve_exec_command('vhsttx'), '/home/kot/.local/bin/vhsttx')

    def test_install_desktop_integration_writes_user_files(self):
        with tempfile.TemporaryDirectory() as data_home:
            with mock.patch('teletext.gui.vhsttx_install.shutil.which', return_value='/home/kot/.local/bin/vhsttx'):
                with mock.patch('teletext.gui.vhsttx_install._run_command'):
                    installed = install_desktop_integration(
                        data_home=data_home,
                        exec_command='vhsttx',
                    )

            self.assertTrue(os.path.exists(installed['desktop']))
            self.assertTrue(os.path.exists(installed['icon']))

            with open(installed['desktop'], 'r', encoding='utf-8') as handle:
                desktop_contents = handle.read()

            self.assertIn('Exec=/home/kot/.local/bin/vhsttx', desktop_contents)

    def test_uninstall_desktop_integration_removes_user_files(self):
        with tempfile.TemporaryDirectory() as data_home:
            with mock.patch('teletext.gui.vhsttx_install.shutil.which', return_value='/home/kot/.local/bin/vhsttx'):
                with mock.patch('teletext.gui.vhsttx_install._run_command'):
                    installed = install_desktop_integration(
                        data_home=data_home,
                        exec_command='vhsttx',
                    )
                    removed = uninstall_desktop_integration(data_home=data_home)

            self.assertEqual(set(removed), {'desktop', 'icon'})
            self.assertFalse(os.path.exists(installed['desktop']))
            self.assertFalse(os.path.exists(installed['icon']))


if __name__ == '__main__':
    unittest.main()
