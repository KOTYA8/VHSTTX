import argparse
import os
import shutil
import subprocess
import sys


DESKTOP_FILENAME = 'ttviewer.desktop'
MIME_FILENAME = 'x-teletext-t42.xml'
ICON_FILENAME = 'teletext.png'


def _resource_path(filename):
    return os.path.join(os.path.dirname(__file__), filename)


def desktop_entry(exec_command='ttviewer'):
    return (
        '[Desktop Entry]\n'
        'Version=1.0\n'
        'Type=Application\n'
        'Name=Teletext Viewer\n'
        'Comment=Open teletext .t42 captures\n'
        f'Exec={exec_command} %f\n'
        'Icon=teletext\n'
        'Terminal=false\n'
        'Categories=AudioVideo;Utility;Viewer;\n'
        'MimeType=application/x-teletext-t42;\n'
        'StartupNotify=true\n'
    )


def mime_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
        '  <mime-type type="application/x-teletext-t42">\n'
        '    <comment>Teletext capture</comment>\n'
        '    <glob pattern="*.t42"/>\n'
        '  </mime-type>\n'
        '</mime-info>\n'
    )


def _write_text(path, content):
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)


def resolve_exec_command(exec_command):
    if os.path.sep in exec_command or (os.path.altsep and os.path.altsep in exec_command):
        return exec_command

    resolved = shutil.which(exec_command)
    if resolved:
        return resolved

    sibling = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), exec_command)
    if os.path.exists(sibling):
        return sibling

    return exec_command


def _run_command(command, quiet=False):
    if not shutil.which(command[0]):
        return
    kwargs = {'check': False}
    if quiet:
        kwargs['stdout'] = subprocess.DEVNULL
        kwargs['stderr'] = subprocess.DEVNULL
    subprocess.run(command, **kwargs)


def install_desktop_integration(data_home=None, exec_command='ttviewer', set_default=True):
    if data_home is None:
        data_home = os.environ.get('XDG_DATA_HOME', os.path.join(os.path.expanduser('~'), '.local', 'share'))
    resolved_exec_command = resolve_exec_command(exec_command)

    applications_dir = os.path.join(data_home, 'applications')
    mime_packages_dir = os.path.join(data_home, 'mime', 'packages')
    icon_dir = os.path.join(data_home, 'icons', 'hicolor', '512x512', 'apps')

    for path in (applications_dir, mime_packages_dir, icon_dir):
        os.makedirs(path, exist_ok=True)

    desktop_path = os.path.join(applications_dir, DESKTOP_FILENAME)
    mime_path = os.path.join(mime_packages_dir, MIME_FILENAME)
    icon_path = os.path.join(icon_dir, ICON_FILENAME)

    _write_text(desktop_path, desktop_entry(exec_command=resolved_exec_command))
    _write_text(mime_path, mime_xml())
    shutil.copyfile(_resource_path(ICON_FILENAME), icon_path)

    _run_command(['update-mime-database', os.path.join(data_home, 'mime')])
    _run_command(['update-desktop-database', applications_dir])
    _run_command(['gtk-update-icon-cache', '-f', '-t', os.path.join(data_home, 'icons', 'hicolor')], quiet=True)
    if set_default:
        _run_command(['xdg-mime', 'default', DESKTOP_FILENAME, 'application/x-teletext-t42'])

    return {
        'desktop': desktop_path,
        'mime': mime_path,
        'icon': icon_path,
        'exec': resolved_exec_command,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install Teletext Viewer desktop integration for .t42 files.')
    parser.add_argument('--data-home', help='Override XDG data directory (default: ~/.local/share).')
    parser.add_argument('--exec', dest='exec_command', default='ttviewer', help='Command used in the desktop launcher.')
    parser.add_argument('--no-default', action='store_true', help='Do not register ttviewer.desktop as the default handler.')
    args = parser.parse_args(argv)

    installed = install_desktop_integration(
        data_home=args.data_home,
        exec_command=args.exec_command,
        set_default=not args.no_default,
    )

    print('Installed Teletext Viewer desktop integration.')
    for key, path in installed.items():
        print(f'{key}: {path}')
    return 0


if __name__ == '__main__':  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
