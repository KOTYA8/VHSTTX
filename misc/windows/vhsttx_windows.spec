# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path.cwd()
DATAS = collect_data_files("teletext")
HIDDENIMPORTS = collect_submodules("teletext")
HIDDENIMPORTS.extend(
    [
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtQuickWidgets",
    ]
)


def build_analysis(script_path):
    return Analysis(
        [str(ROOT / script_path)],
        pathex=[str(ROOT)],
        binaries=[],
        datas=DATAS,
        hiddenimports=HIDDENIMPORTS,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
    )


launcher_analysis = build_analysis("teletext/gui/launcher_bootstrap.py")
launcher_pyz = PYZ(launcher_analysis.pure)
launcher_exe = EXE(
    launcher_pyz,
    launcher_analysis.scripts,
    launcher_analysis.binaries,
    launcher_analysis.datas,
    [],
    name="VHSTTX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

teletext_analysis = build_analysis("teletext/cli/teletext_bootstrap.py")
teletext_pyz = PYZ(teletext_analysis.pure)
teletext_exe = EXE(
    teletext_pyz,
    teletext_analysis.scripts,
    teletext_analysis.binaries,
    teletext_analysis.datas,
    [],
    name="teletext",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

viewer_analysis = build_analysis("teletext/gui/viewer_bootstrap.py")
viewer_pyz = PYZ(viewer_analysis.pure)
viewer_exe = EXE(
    viewer_pyz,
    viewer_analysis.scripts,
    viewer_analysis.binaries,
    viewer_analysis.datas,
    [],
    name="TTViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    launcher_exe,
    teletext_exe,
    viewer_exe,
    launcher_analysis.binaries + teletext_analysis.binaries + viewer_analysis.binaries,
    launcher_analysis.zipfiles + teletext_analysis.zipfiles + viewer_analysis.zipfiles,
    launcher_analysis.datas + teletext_analysis.datas + viewer_analysis.datas,
    strip=False,
    upx=False,
    name="VHSTTX-Windows",
)
