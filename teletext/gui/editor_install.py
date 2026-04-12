import argparse
import os
import shutil

from teletext.gui.install import _run_command, _write_text, resolve_exec_command


DESKTOP_FILENAME = 'tteditor.desktop'
ICON_FILENAME = 'tteditor.png'
ICON_NAME = 'tteditor'


def _resource_path(filename):
    return os.path.join(os.path.dirname(__file__), filename)


def desktop_entry(exec_command='tteditor'):
    return (
        '[Desktop Entry]\n'
        'Version=1.0\n'
        'Type=Application\n'
        'Name=TeleText Editor\n'
        'Comment=Launch the TeleText Editor\n'
        f'Exec={exec_command} %f\n'
        f'Icon={ICON_NAME}\n'
        'Terminal=false\n'
        'Categories=AudioVideo;Utility;Graphics;\n'
        'StartupNotify=true\n'
    )


def install_desktop_integration(data_home=None, exec_command='tteditor'):
    if data_home is None:
        data_home = os.environ.get('XDG_DATA_HOME', os.path.join(os.path.expanduser('~'), '.local', 'share'))
    resolved_exec_command = resolve_exec_command(exec_command)

    applications_dir = os.path.join(data_home, 'applications')
    icon_dir = os.path.join(data_home, 'icons', 'hicolor', '512x512', 'apps')

    for path in (applications_dir, icon_dir):
        os.makedirs(path, exist_ok=True)

    desktop_path = os.path.join(applications_dir, DESKTOP_FILENAME)
    icon_path = os.path.join(icon_dir, ICON_FILENAME)

    _write_text(desktop_path, desktop_entry(exec_command=resolved_exec_command))
    shutil.copyfile(_resource_path(ICON_FILENAME), icon_path)

    _run_command(['update-desktop-database', applications_dir])
    _run_command(['gtk-update-icon-cache', '-f', '-t', os.path.join(data_home, 'icons', 'hicolor')], quiet=True)

    return {
        'desktop': desktop_path,
        'icon': icon_path,
        'exec': resolved_exec_command,
    }


def uninstall_desktop_integration(data_home=None):
    if data_home is None:
        data_home = os.environ.get('XDG_DATA_HOME', os.path.join(os.path.expanduser('~'), '.local', 'share'))

    applications_dir = os.path.join(data_home, 'applications')
    icon_dir = os.path.join(data_home, 'icons', 'hicolor', '512x512', 'apps')

    targets = {
        'desktop': os.path.join(applications_dir, DESKTOP_FILENAME),
        'icon': os.path.join(icon_dir, ICON_FILENAME),
    }
    removed = {}
    for key, path in targets.items():
        if os.path.exists(path):
            os.remove(path)
            removed[key] = path

    _run_command(['update-desktop-database', applications_dir])
    _run_command(['gtk-update-icon-cache', '-f', '-t', os.path.join(data_home, 'icons', 'hicolor')], quiet=True)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install TeleText Editor desktop integration.')
    parser.add_argument('--data-home', help='Override XDG data directory (default: ~/.local/share).')
    parser.add_argument('--exec', dest='exec_command', default='tteditor', help='Command used in the desktop launcher.')
    parser.add_argument('--uninstall', action='store_true', help='Remove TeleText Editor desktop integration.')
    args = parser.parse_args(argv)

    if args.uninstall:
        removed = uninstall_desktop_integration(data_home=args.data_home)
        print('Removed TeleText Editor desktop integration.')
        for key, path in removed.items():
            print(f'{key}: {path}')
        return 0

    installed = install_desktop_integration(
        data_home=args.data_home,
        exec_command=args.exec_command,
    )
    print('Installed TeleText Editor desktop integration.')
    for key, path in installed.items():
        print(f'{key}: {path}')
    return 0


def uninstall_main(argv=None):
    parser = argparse.ArgumentParser(description='Remove TeleText Editor desktop integration.')
    parser.add_argument('--data-home', help='Override XDG data directory (default: ~/.local/share).')
    args = parser.parse_args(argv)

    removed = uninstall_desktop_integration(data_home=args.data_home)
    print('Removed TeleText Editor desktop integration.')
    for key, path in removed.items():
        print(f'{key}: {path}')
    return 0


if __name__ == '__main__':  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
