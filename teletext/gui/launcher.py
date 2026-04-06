from __future__ import annotations

import html
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import types
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import click

from teletext import (
    __display_version__,
    __github_latest_release_api__,
    __github_releases_url__,
    __github_url__,
    __version__,
)

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    QtCore = None
    QtGui = None
    QtWidgets = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


OPTIONAL_CLI_STUBS = {"tqdm", "zmq"}
LAUNCHER_SETTINGS_ORGANISATION = "VHSTTX"
LAUNCHER_SETTINGS_APPLICATION = "VHSTTXLauncher"
BASIC_OPTION_NAMES = {
    "mode",
    "output",
    "page",
    "pages",
    "subpage",
    "subpages",
    "start",
    "stop",
    "step",
    "limit",
    "pause",
    "card",
    "tape_format",
    "input",
    "timer",
    "progress",
    "paginate",
    "threads",
    "ignore_lines",
    "used_lines",
    "fix_capture_card",
    "vbi_tune",
    "vbi_tune_live",
}
PRIMARY_COMMANDS = {
    "squash",
    "record",
    "vbiview",
    "vbitool",
    "vbirepair",
    "t42tool",
    "deconvolve",
    "apps",
}
PRIMARY_COMMAND_ORDER = (
    "record",
    "vbiview",
    "vbitool",
    "vbirepair",
    "deconvolve",
    "t42tool",
    "squash",
    "apps",
)
LIST_MODE_ALL = "all"
LIST_MODE_FAVORITE = "favorite"
LIST_MODE_PRIMARY = "primary"
LIST_MODE_ADDITIONAL = "additional"
HIDDEN_COMMAND_PATHS = {
}
TTVIEWER_COMMAND_PATH = ("apps", "ttviewer")
EXTERNAL_LAUNCHER_LAYOUT_CANDIDATES = (
    os.environ.get("VHSTTX_LAUNCHER_FIELDS_PATH", "").strip(),
    r"C:\Users\igory\Downloads\networkfolder\launcher_fields.json",
)
_ANSI_PATTERN = re.compile(r"\x1b\[([0-9;]+)m")
_ANSI_COLOURS = {
    0: "#000000",
    1: "#ff3b30",
    2: "#40ff40",
    3: "#ffd60a",
    4: "#4c7dff",
    5: "#ff4df2",
    6: "#3df2ff",
    7: "#f5f5f5",
}
WINDOWS_HIDDEN_COMMAND_PATHS = {
    ("record",),
}
WINDOWS_HIDDEN_OPTION_NAMES = {
    "device",
    "fix_capture_card",
    "urxvt",
    "vbi_start",
    "vbi_count",
    "vbi_terminate_reset",
}


def _install_optional_cli_stubs():
    if "tqdm" not in sys.modules:
        tqdm_module = types.ModuleType("tqdm")

        def _tqdm(iterable=None, *args, **kwargs):
            return iterable if iterable is not None else []

        tqdm_module.tqdm = _tqdm
        sys.modules["tqdm"] = tqdm_module
    if "zmq" not in sys.modules:
        sys.modules["zmq"] = types.ModuleType("zmq")


def load_teletext_root_command():
    try:
        from teletext.cli.teletext import teletext
    except ModuleNotFoundError as exc:
        if exc.name not in OPTIONAL_CLI_STUBS:
            raise
        _install_optional_cli_stubs()
        from teletext.cli.teletext import teletext
    return teletext


def _is_missing_default(value):
    return value is None or repr(value) == "Sentinel.UNSET"


def normalise_default_value(value):
    if _is_missing_default(value):
        return None
    if isinstance(value, range):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        normalised = []
        for item in value:
            if isinstance(item, tuple):
                normalised.append(list(item))
            else:
                normalised.append(item)
        return normalised
    return value


def split_text_values(text):
    if text is None:
        return []
    text = str(text).strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        parts = []
    if not parts:
        if "," in text:
            return [item.strip() for item in text.split(",") if item.strip()]
        return [item for item in text.split() if item]
    if len(parts) == 1 and "," in text and " " not in text and "\t" not in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    return parts


def preview_command_text(tokens):
    if os.name == "nt":
        return subprocess.list2cmdline(tokens)
    return " ".join(shlex.quote(token) for token in tokens)


def format_command_label(name):
    text = str(name or "").strip()
    if not text:
        return ""
    lower_text = text.lower()
    explicit = {
        "apps": "Apps",
        "vbiview": "VBI View",
        "vbitool": "VBI Tool",
        "vbirepair": "VBI Repair",
        "vbicrop": "VBI Crop",
        "t42tool": "T42 Tool",
        "t42crop": "T42 Crop",
        "ttviewer": "Teletext Viewer",
        "servicedir": "Service Dir",
        "spellcheck-analyze": "Spellcheck Analyze",
    }
    if lower_text in explicit:
        return explicit[lower_text]
    if lower_text.startswith("vbi") and len(lower_text) > 3:
        return "VBI " + lower_text[3:].replace("-", " ").replace("_", " ").title()
    if lower_text.startswith("t42") and len(lower_text) > 3:
        return "T42 " + lower_text[3:].replace("-", " ").replace("_", " ").title()
    return " ".join(part[:1].upper() + part[1:] for part in text.replace("-", " ").replace("_", " ").split())


def _normalise_version_text(version_text):
    text = str(version_text or "").strip()
    if not text:
        return ()
    text = re.sub(r"^[Vv]", "", text)
    parts = re.split(r"[^0-9]+", text)
    return tuple(int(part) for part in parts if part != "")


def _display_version_text(version_text):
    text = str(version_text or "").strip()
    if not text:
        return "Unknown"
    if text[:1].lower() == "v":
        return f"V{text[1:]}"
    return f"V{text}"


def parse_latest_release_payload(payload):
    if not isinstance(payload, dict):
        return {}
    tag_name = str(payload.get("tag_name") or payload.get("name") or "").strip()
    html_url = str(payload.get("html_url") or __github_releases_url__).strip() or __github_releases_url__
    return {
        "tag_name": tag_name,
        "display_version": _display_version_text(tag_name),
        "version_tuple": _normalise_version_text(tag_name),
        "html_url": html_url,
        "name": str(payload.get("name") or "").strip(),
    }


def compare_versions(current_version, latest_version):
    current_tuple = _normalise_version_text(current_version)
    latest_tuple = _normalise_version_text(latest_version)
    max_len = max(len(current_tuple), len(latest_tuple), 1)
    current_tuple = current_tuple + (0,) * (max_len - len(current_tuple))
    latest_tuple = latest_tuple + (0,) * (max_len - len(latest_tuple))
    if latest_tuple > current_tuple:
        return 1
    if latest_tuple < current_tuple:
        return -1
    return 0


def favourite_path_key(path):
    return "/".join(path)


def is_windows_runtime(platform_name=None):
    if platform_name is None:
        return os.name == "nt"
    return str(platform_name).lower() in {"nt", "windows", "win32"}


def command_visible_for_platform(node, platform_name=None):
    if not node.path:
        return True
    if is_windows_runtime(platform_name) and tuple(node.path) in WINDOWS_HIDDEN_COMMAND_PATHS:
        return False
    return True


def filter_descriptors_for_platform(descriptors, command_path=(), platform_name=None):
    if not is_windows_runtime(platform_name):
        return list(descriptors)
    command_path = tuple(command_path or ())
    filtered = []
    for descriptor in descriptors:
        if descriptor.name in WINDOWS_HIDDEN_OPTION_NAMES:
            continue
        filtered.append(descriptor)
    return filtered


def is_frozen_runtime():
    return bool(getattr(sys, "frozen", False))


def bundled_executable_path(name):
    if not is_frozen_runtime():
        return None
    base_dir = pathlib.Path(sys.executable).resolve().parent
    candidates = []
    if os.name == "nt":
        candidates.extend([f"{name}.exe", name])
    else:
        candidates.append(name)
    for candidate in candidates:
        path = base_dir / candidate
        if path.exists():
            return str(path)
    return None


def launcher_process_command(preview_tokens):
    is_ttviewer = bool(preview_tokens and preview_tokens[0] == "ttviewer")
    if is_frozen_runtime():
        if is_ttviewer:
            program = (
                bundled_executable_path("TTViewer")
                or bundled_executable_path("ttviewer")
                or "ttviewer"
            )
            return program, preview_tokens[1:]
        program = bundled_executable_path("teletext") or "teletext"
        return program, preview_tokens[1:]

    if is_ttviewer:
        return "ttviewer", preview_tokens[1:]

    cli_args = preview_tokens[1:]
    launcher_code = (
        "import shutil, sys; "
        "sys.argv[0] = shutil.which('teletext') or 'teletext'; "
        "from teletext.cli.teletext import teletext; "
        "teletext()"
    )
    return sys.executable, ["-u", "-c", launcher_code, *cli_args]


def list_mode_leaf_nodes(command_tree, mode, favorite_paths=(), platform_name=None):
    leaf_nodes = list(iter_leaf_nodes(command_tree))
    leaf_nodes = [node for node in leaf_nodes if command_visible_for_platform(node, platform_name)]
    favorite_paths = set(favorite_paths or ())

    if mode == LIST_MODE_FAVORITE:
        return [node for node in leaf_nodes if favourite_path_key(node.path) in favorite_paths]

    if mode == LIST_MODE_PRIMARY:
        nodes_by_root = {}
        for node in leaf_nodes:
            if node.path and not node.is_group:
                nodes_by_root.setdefault(node.path[0], []).append(node)
        ordered = []
        for command_name in PRIMARY_COMMAND_ORDER:
            ordered.extend(nodes_by_root.get(command_name, ()))
        return ordered

    if mode == LIST_MODE_ADDITIONAL:
        return [
            node for node in leaf_nodes
            if node.path
            and not node.is_group
            and node.path[0] not in PRIMARY_COMMANDS
            and node.path[-1] != "squash"
        ]

    return leaf_nodes


def is_basic_descriptor(descriptor):
    if descriptor.param_type == "argument":
        return True
    if descriptor.name in BASIC_OPTION_NAMES:
        return True
    if descriptor.name.startswith("show_"):
        return False
    if descriptor.value_kind == "redirect_output":
        return True
    return False


def order_param_descriptors(descriptors, saved_order=()):
    saved_order = [str(name) for name in (saved_order or ()) if str(name).strip()]
    descriptor_map = {descriptor.name: descriptor for descriptor in descriptors}
    ordered = []
    seen = set()

    for name in saved_order:
        descriptor = descriptor_map.get(name)
        if descriptor is not None and name not in seen:
            ordered.append(descriptor)
            seen.add(name)

    for descriptor in descriptors:
        if descriptor.name not in seen:
            ordered.append(descriptor)

    return ordered


def _launcher_config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if not base:
            base = str(pathlib.Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        if not base:
            base = str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "VHSTTX"


def launcher_field_layout_path():
    for candidate in EXTERNAL_LAUNCHER_LAYOUT_CANDIDATES:
        if not candidate:
            continue
        try:
            candidate_path = pathlib.Path(candidate)
        except Exception:
            continue
        if candidate_path.exists():
            return candidate_path
    return _launcher_config_dir() / "launcher_fields.json"


def _ansi_text_to_html(text, font_family=None):
    text = str(text or "")
    parts = []
    fg = 7
    bg = 0
    index = 0
    span_open = False

    def open_span():
        nonlocal span_open
        parts.append(
            f'<span style="color:{_ANSI_COLOURS.get(fg, _ANSI_COLOURS[7])};'
            f'background-color:{_ANSI_COLOURS.get(bg, _ANSI_COLOURS[0])};">'
        )
        span_open = True

    def close_span():
        nonlocal span_open
        if span_open:
            parts.append("</span>")
            span_open = False

    open_span()
    for match in _ANSI_PATTERN.finditer(text):
        if match.start() > index:
            parts.append(html.escape(text[index:match.start()]))
        codes = [int(code or 0) for code in match.group(1).split(";") if code != ""]
        if not codes:
            codes = [0]
        for code in codes:
            if code == 0:
                fg = 7
                bg = 0
            elif 30 <= code <= 37:
                fg = code - 30
            elif 40 <= code <= 47:
                bg = code - 40
        close_span()
        open_span()
        index = match.end()
    if index < len(text):
        parts.append(html.escape(text[index:]))
    close_span()
    family = html.escape(font_family or "monospace")
    return (
        '<html><body style="margin:0; background:#000000;">'
        f'<pre style="margin:0; padding:8px; white-space:pre; font-family:{family}; font-size:12pt;">'
        + "".join(parts)
        + "</pre></body></html>"
    )


@dataclass
class ParamDescriptor:
    name: str
    param_type: str
    display_name: str
    help_text: str
    required: bool
    multiple: bool
    nargs: int
    option_names: tuple[str, ...] = ()
    secondary_option_names: tuple[str, ...] = ()
    default: Any = None
    choices: tuple[str, ...] = ()
    value_kind: str = "text"
    path_kind: str | None = None
    metavar: str = ""
    hidden: bool = False

    @property
    def primary_option(self):
        return self.option_names[0] if self.option_names else ""

    @property
    def has_secondary_flag(self):
        return bool(self.secondary_option_names)

    @property
    def is_flag(self):
        return self.value_kind == "flag"

    @property
    def default_text(self):
        value = normalise_default_value(self.default)
        if self.name == "input" and value == "-":
            return ""
        if self.value_kind == "redirect_output":
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, (list, tuple)) and len(first) >= 2 and first[1] == "-":
                    return ""
            return ""
        if value is None or value == "":
            return ""
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return str(value)


@dataclass
class CommandNode:
    name: str
    path: tuple[str, ...]
    help_text: str
    is_group: bool
    command: click.Command | None = None
    children: list["CommandNode"] = field(default_factory=list)


def _command_help_text(command):
    text = getattr(command, "short_help", None) or getattr(command, "help", None) or ""
    return " ".join(str(text).split())


def infer_param_value_kind(param):
    if isinstance(param, click.Option) and param.is_flag:
        return "flag", None
    param_type = param.type
    if isinstance(param_type, click.Tuple):
        tuple_types = getattr(param_type, "types", ())
        if (
            len(tuple_types) == 2
            and isinstance(tuple_types[0], click.Choice)
            and isinstance(tuple_types[1], (click.File, click.Path))
        ):
            return "redirect_output", "file_write"
    if isinstance(param_type, click.Choice):
        return "choice", None
    if isinstance(param_type, click.File):
        mode = "read"
        if getattr(param_type, "mode", ""):
            if "w" in param_type.mode or "a" in param_type.mode or "x" in param_type.mode:
                mode = "write"
        return "path", f"file_{mode}"
    if isinstance(param_type, click.Path):
        if getattr(param_type, "dir_okay", False) and not getattr(param_type, "file_okay", True):
            return "path", "directory"
        if getattr(param_type, "file_okay", True) and not getattr(param_type, "dir_okay", False):
            if getattr(param_type, "exists", False):
                return "path", "file_read"
            return "path", "file_write"
        return "path", "path"
    type_name = type(param_type).__name__
    if type_name in {"IntParamType", "IntRange"}:
        return "int", None
    if type_name in {"FloatParamType", "FloatRange"}:
        return "float", None
    return "text", None


def describe_param(param):
    param_type = "option" if isinstance(param, click.Option) else "argument"
    value_kind, path_kind = infer_param_value_kind(param)
    if param_type == "option":
        display_name = ">" if value_kind == "redirect_output" else (param.opts[0] if param.opts else param.name)
        option_names = tuple(param.opts)
        secondary_option_names = tuple(getattr(param, "secondary_opts", ()))
        help_text = getattr(param, "help", "") or ""
    else:
        display_name = "INPUT" if param.name == "input" else param.human_readable_name.upper()
        option_names = ()
        secondary_option_names = ()
        help_text = ""
    try:
        default = param.get_default(None)
    except Exception:
        default = getattr(param, "default", None)
    if value_kind == "redirect_output":
        normalised = normalise_default_value(default)
        if isinstance(normalised, list) and normalised:
            cleaned = []
            for item in normalised:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    cleaned.append([item[0], "" if item[1] == "-" else item[1]])
                elif item == "-":
                    cleaned.append("")
                else:
                    cleaned.append(item)
            default = cleaned
        elif normalised == "-":
            default = ""
    metavar = ""
    if hasattr(param, "make_metavar"):
        try:
            metavar = param.make_metavar(None)
        except TypeError:
            try:
                metavar = param.make_metavar()
            except TypeError:
                metavar = ""
    return ParamDescriptor(
        name=param.name,
        param_type=param_type,
        display_name=display_name,
        help_text=help_text,
        required=bool(getattr(param, "required", False)),
        multiple=bool(getattr(param, "multiple", False)),
        nargs=int(getattr(param, "nargs", 1)),
        option_names=option_names,
        secondary_option_names=secondary_option_names,
        default=default,
        choices=tuple(
            str(choice)
            for choice in (
                getattr(getattr(param, "type", None), "choices", ())
                or getattr(getattr(getattr(param, "type", None), "types", [None])[0], "choices", ())
                or ()
            )
        ),
        value_kind=value_kind,
        path_kind=path_kind,
        metavar=metavar,
        hidden=bool(getattr(param, "hidden", False)),
    )


def describe_command(command):
    return [describe_param(param) for param in getattr(command, "params", ()) if not getattr(param, "hidden", False)]


def build_command_tree(command, path=()):
    if path in HIDDEN_COMMAND_PATHS:
        return None
    is_group = isinstance(command, click.Group)
    node = CommandNode(
        name=command.name or (path[-1] if path else "teletext"),
        path=path,
        help_text=_command_help_text(command),
        is_group=is_group,
        command=command,
    )
    if is_group:
        for child_name, child_command in command.commands.items():
            if getattr(child_command, "hidden", False):
                continue
            child_node = build_command_tree(child_command, path + (child_name,))
            if child_node is not None:
                node.children.append(child_node)
        if not path and (command.name or "").strip().lower() == "teletext":
            apps_group = CommandNode(
                name="apps",
                path=("apps",),
                help_text="GUI applications.",
                is_group=True,
            )
            apps_group.children.append(
                CommandNode(
                    name="ttviewer",
                    path=TTVIEWER_COMMAND_PATH,
                    help_text="Launch the Teletext Viewer GUI.",
                    is_group=False,
                    command=click.Command("ttviewer"),
                )
            )
            node.children.append(
                apps_group
            )
    return node


def iter_leaf_nodes(node):
    if not node.is_group:
        yield node
        return
    for child in node.children:
        yield from iter_leaf_nodes(child)


if QtWidgets is not None:
    class PathInputWidget(QtWidgets.QWidget):
        changed = QtCore.pyqtSignal()

        def __init__(self, path_kind, initial_text="", parent=None):
            super().__init__(parent)
            self._path_kind = path_kind or "path"
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            self.line_edit = QtWidgets.QLineEdit(str(initial_text or ""))
            self.browse_button = QtWidgets.QPushButton("...")
            self.browse_button.setFixedWidth(34)

            layout.addWidget(self.line_edit, 1)
            layout.addWidget(self.browse_button)

            self.line_edit.textChanged.connect(self._emit_changed)
            self.browse_button.clicked.connect(self._browse)

        def _emit_changed(self, *_args):
            self.changed.emit()

        def _browse(self, *_args):
            current = self.line_edit.text().strip() or os.getcwd()
            if self._path_kind == "directory":
                path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Directory", current)
            elif self._path_kind == "file_write":
                path = QtWidgets.QFileDialog.getSaveFileName(self, "Choose File", current)[0]
            else:
                path = QtWidgets.QFileDialog.getOpenFileName(self, "Choose File", current)[0]
            if path:
                self.line_edit.setText(path)

        def text(self):
            return self.line_edit.text()

        def setText(self, value):
            self.line_edit.setText(str(value or ""))


    class RedirectOutputWidget(QtWidgets.QWidget):
        changed = QtCore.pyqtSignal()

        def __init__(self, parent=None):
            super().__init__(parent)
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            self.path_widget = PathInputWidget("file_write")
            layout.addWidget(QtWidgets.QLabel("File"))
            layout.addWidget(self.path_widget, 1)

            self.path_widget.changed.connect(self._emit_changed)

        def _emit_changed(self, *_args):
            self.changed.emit()

        def setValue(self, path):
            text = str(path or "").strip()
            if text == "-":
                text = ""
            self.path_widget.setText(text)

        def currentPath(self):
            text = self.path_widget.text().strip()
            return "" if text == "-" else text


    class FieldEditorDialog(QtWidgets.QDialog):
        def __init__(self, descriptors, hidden_names, label_overrides, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Field Editor")
            self.resize(480, 560)
            layout = QtWidgets.QVBoxLayout(self)
            self._descriptors = list(descriptors)
            self._initial_hidden_names = set(hidden_names or ())
            self._initial_label_overrides = dict(label_overrides or {})

            label = QtWidgets.QLabel("Choose which fields stay visible for this command and rename them if needed.")
            label.setWordWrap(True)
            layout.addWidget(label)
            path_label = QtWidgets.QLabel(str(launcher_field_layout_path()))
            path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            path_label.setStyleSheet("color: #666;")
            layout.addWidget(path_label)

            tools_row = QtWidgets.QHBoxLayout()
            self.show_all_button = QtWidgets.QPushButton("Show All")
            self.hide_all_button = QtWidgets.QPushButton("Hide All")
            self.move_up_button = QtWidgets.QPushButton("Up")
            self.move_down_button = QtWidgets.QPushButton("Down")
            self.default_button = QtWidgets.QPushButton("Default")
            self.show_all_button.clicked.connect(self._show_all)
            self.hide_all_button.clicked.connect(self._hide_all)
            self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
            self.move_down_button.clicked.connect(lambda: self._move_selected(1))
            self.default_button.clicked.connect(self._reset_defaults)
            tools_row.addWidget(self.show_all_button)
            tools_row.addWidget(self.hide_all_button)
            tools_row.addStretch(1)
            tools_row.addWidget(self.move_up_button)
            tools_row.addWidget(self.move_down_button)
            tools_row.addWidget(self.default_button)
            layout.addLayout(tools_row)

            self.list_widget = QtWidgets.QListWidget()
            self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.list_widget.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
            self.list_widget.setDefaultDropAction(QtCore.Qt.MoveAction)
            layout.addWidget(self.list_widget, 1)
            self._items = {}

            self._populate_items(self._descriptors, self._initial_hidden_names, self._initial_label_overrides)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _populate_items(self, descriptors, hidden_names, label_overrides):
            self.list_widget.clear()
            self._items = {}
            hidden_names = set(hidden_names or ())
            label_overrides = dict(label_overrides or {})
            for descriptor in descriptors:
                item = QtWidgets.QListWidgetItem(label_overrides.get(descriptor.name, descriptor.display_name))
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEditable)
                item.setData(QtCore.Qt.UserRole, descriptor.name)
                item.setData(QtCore.Qt.UserRole + 1, descriptor.display_name)
                if descriptor.required and descriptor.param_type == "argument":
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
                    item.setToolTip("Required argument")
                else:
                    item.setToolTip(descriptor.help_text or descriptor.display_name)
                item.setCheckState(
                    QtCore.Qt.Unchecked if descriptor.name in hidden_names else QtCore.Qt.Checked
                )
                self.list_widget.addItem(item)
                self._items[descriptor.name] = item

        def _iter_items(self):
            for row in range(self.list_widget.count()):
                yield self.list_widget.item(row)

        def _show_all(self):
            for item in self._iter_items():
                if item.flags() & QtCore.Qt.ItemIsEnabled:
                    item.setCheckState(QtCore.Qt.Checked)

        def _hide_all(self):
            for item in self._iter_items():
                if item.flags() & QtCore.Qt.ItemIsEnabled:
                    item.setCheckState(QtCore.Qt.Unchecked)

        def _move_selected(self, delta):
            current_row = self.list_widget.currentRow()
            if current_row < 0:
                return
            new_row = current_row + int(delta)
            if new_row < 0 or new_row >= self.list_widget.count():
                return
            item = self.list_widget.takeItem(current_row)
            self.list_widget.insertItem(new_row, item)
            self.list_widget.setCurrentRow(new_row)

        def _reset_defaults(self):
            self._populate_items(self._descriptors, set(), {})
            if self.list_widget.count():
                self.list_widget.setCurrentRow(0)

        def hidden_names(self):
            hidden = []
            for item in self._iter_items():
                name = item.data(QtCore.Qt.UserRole)
                if item.checkState() != QtCore.Qt.Checked:
                    hidden.append(name)
            return hidden

        def label_overrides(self):
            overrides = {}
            for item in self._iter_items():
                name = item.data(QtCore.Qt.UserRole)
                original = item.data(QtCore.Qt.UserRole + 1)
                current = item.text().strip()
                if current and current != original:
                    overrides[name] = current
            return overrides

        def field_order(self):
            return [item.data(QtCore.Qt.UserRole) for item in self._iter_items()]


    class ParamEditorWidget(QtWidgets.QWidget):
        changed = QtCore.pyqtSignal()

        def _emit_changed(self, *_args):
            if self.descriptor.value_kind == "redirect_output" and self.include_checkbox is not None:
                values = self._current_values() if self.value_widget is not None else []
                if values and not self.include_checkbox.isChecked():
                    self.include_checkbox.setChecked(True)
                    return
            self.changed.emit()

        def __init__(self, descriptor, parent=None):
            super().__init__(parent)
            self.descriptor = descriptor
            self.value_widget = None
            self.include_checkbox = None
            self.flag_mode = None

            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 2, 0, 2)
            layout.setSpacing(8)

            if descriptor.param_type == "option" and not descriptor.required and not descriptor.is_flag and descriptor.value_kind != "redirect_output":
                self.include_checkbox = QtWidgets.QCheckBox()
                self.include_checkbox.setChecked(False)
                self.include_checkbox.toggled.connect(self._sync_enabled_state)
                self.include_checkbox.toggled.connect(self._emit_changed)
                layout.addWidget(self.include_checkbox)
            else:
                spacer = QtWidgets.QLabel("")
                spacer.setFixedWidth(18)
                layout.addWidget(spacer)

            self.label = QtWidgets.QLabel(descriptor.display_name)
            self.label.setMinimumWidth(190)
            self.label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            tooltip_parts = []
            if descriptor.help_text:
                tooltip_parts.append(descriptor.help_text)
            if descriptor.default_text:
                tooltip_parts.append(f"Default: {descriptor.default_text}")
            if descriptor.metavar:
                tooltip_parts.append(f"Value: {descriptor.metavar}")
            if tooltip_parts:
                tooltip = "\n".join(tooltip_parts)
                self.setToolTip(tooltip)
                self.label.setToolTip(tooltip)
            layout.addWidget(self.label)

            if descriptor.is_flag:
                self.flag_mode = QtWidgets.QComboBox()
                self.flag_mode.addItem("Default")
                self.flag_mode.addItem("On")
                if descriptor.has_secondary_flag:
                    self.flag_mode.addItem("Off")
                self.flag_mode.currentIndexChanged.connect(self._emit_changed)
                layout.addWidget(self.flag_mode, 1)
            else:
                self.value_widget = self._build_value_widget(descriptor)
                layout.addWidget(self.value_widget, 1)

            layout.addStretch(0)
            self.reset()

        def _build_value_widget(self, descriptor):
            if descriptor.multiple or descriptor.nargs != 1:
                if descriptor.value_kind == "redirect_output":
                    widget = RedirectOutputWidget()
                    widget.changed.connect(self._emit_changed)
                    return widget
                widget = QtWidgets.QLineEdit()
                widget.setPlaceholderText("Enter values separated by spaces or commas")
                widget.textChanged.connect(self._emit_changed)
                return widget
            if descriptor.value_kind == "choice" and descriptor.choices:
                widget = QtWidgets.QComboBox()
                widget.addItems(list(descriptor.choices))
                widget.currentIndexChanged.connect(self._emit_changed)
                return widget
            if descriptor.value_kind == "int":
                widget = QtWidgets.QSpinBox()
                widget.setRange(-999999999, 999999999)
                widget.setAccelerated(True)
                widget.valueChanged.connect(self._emit_changed)
                return widget
            if descriptor.value_kind == "float":
                widget = QtWidgets.QDoubleSpinBox()
                widget.setDecimals(4)
                widget.setSingleStep(0.1)
                widget.setRange(-999999999.0, 999999999.0)
                widget.valueChanged.connect(self._emit_changed)
                return widget
            if descriptor.value_kind == "path":
                widget = PathInputWidget(descriptor.path_kind, "")
                widget.changed.connect(self._emit_changed)
                return widget
            widget = QtWidgets.QLineEdit()
            widget.textChanged.connect(self._emit_changed)
            return widget

        def _widget_is_enabled(self):
            if self.include_checkbox is None:
                return True
            return self.include_checkbox.isChecked()

        def _sync_enabled_state(self):
            if self.value_widget is not None:
                self.value_widget.setEnabled(self._widget_is_enabled())

        def reset(self):
            descriptor = self.descriptor
            default = normalise_default_value(descriptor.default)
            if self.include_checkbox is not None:
                self.include_checkbox.setChecked(False)
            if descriptor.is_flag:
                self.flag_mode.setCurrentIndex(0)
            elif descriptor.value_kind == "choice" and descriptor.choices and descriptor.nargs == 1 and not descriptor.multiple:
                if default in descriptor.choices:
                    self.value_widget.setCurrentText(str(default))
                else:
                    self.value_widget.setCurrentIndex(0 if descriptor.choices else -1)
            elif descriptor.value_kind == "int" and descriptor.nargs == 1 and not descriptor.multiple:
                self.value_widget.setValue(int(default if default is not None else 0))
            elif descriptor.value_kind == "float" and descriptor.nargs == 1 and not descriptor.multiple:
                self.value_widget.setValue(float(default if default is not None else 0.0))
            elif descriptor.value_kind == "path" and descriptor.nargs == 1 and not descriptor.multiple:
                if descriptor.name == "input" and default == "-":
                    self.value_widget.setText("")
                else:
                    self.value_widget.setText(default if default is not None else "")
            elif descriptor.value_kind == "redirect_output":
                if isinstance(default, list) and default:
                    first = default[0]
                    if isinstance(first, list) and len(first) >= 2:
                        self.value_widget.setValue("" if first[1] == "-" else first[1])
                    elif isinstance(first, tuple) and len(first) >= 2:
                        self.value_widget.setValue("" if first[1] == "-" else first[1])
                    elif isinstance(first, str):
                        self.value_widget.setValue("" if first == "-" else first)
                    else:
                        self.value_widget.setValue("")
                elif default == "-":
                    self.value_widget.setValue("")
                else:
                    self.value_widget.setValue("")
            else:
                self.value_widget.setText(descriptor.default_text)
            self._sync_enabled_state()
            self._emit_changed()

        def _current_values(self):
            descriptor = self.descriptor
            if descriptor.value_kind == "choice" and descriptor.choices and descriptor.nargs == 1 and not descriptor.multiple:
                value = self.value_widget.currentText().strip()
                return [value] if value else []
            if descriptor.value_kind == "int" and descriptor.nargs == 1 and not descriptor.multiple:
                return [str(self.value_widget.value())]
            if descriptor.value_kind == "float" and descriptor.nargs == 1 and not descriptor.multiple:
                text = ("%.4f" % self.value_widget.value()).rstrip("0").rstrip(".")
                return [text or "0"]
            if descriptor.value_kind == "path" and descriptor.nargs == 1 and not descriptor.multiple:
                value = self.value_widget.text().strip()
                return [value] if value else []
            if descriptor.value_kind == "redirect_output":
                path = self.value_widget.currentPath()
                return [path] if path else []
            text = self.value_widget.text().strip()
            if not text:
                return []
            if descriptor.multiple or descriptor.nargs != 1:
                return split_text_values(text)
            return [text]

        def current_tokens(self):
            descriptor = self.descriptor
            if descriptor.is_flag:
                mode = self.flag_mode.currentIndex()
                if mode == 1:
                    return [descriptor.primary_option]
                if mode == 2 and descriptor.secondary_option_names:
                    return [descriptor.secondary_option_names[0]]
                return []
            if descriptor.param_type == "option" and not descriptor.required and not self._widget_is_enabled():
                return []
            values = self._current_values()
            if descriptor.param_type == "argument":
                return values
            if descriptor.multiple:
                if descriptor.value_kind == "redirect_output":
                    return []
                if descriptor.nargs == 1:
                    tokens = []
                    for value in values:
                        tokens.extend([descriptor.primary_option, value])
                    return tokens
                if len(values) % descriptor.nargs != 0:
                    raise ValueError(f"{descriptor.display_name} expects groups of {descriptor.nargs} values.")
                tokens = []
                for index in range(0, len(values), descriptor.nargs):
                    tokens.append(descriptor.primary_option)
                    tokens.extend(values[index:index + descriptor.nargs])
                return tokens
            if descriptor.nargs != 1:
                if len(values) != descriptor.nargs:
                    raise ValueError(f"{descriptor.display_name} expects {descriptor.nargs} values.")
                return [descriptor.primary_option, *values]
            if not values:
                return []
            return [descriptor.primary_option, values[0]]

        def validate(self):
            descriptor = self.descriptor
            try:
                tokens = self.current_tokens()
            except ValueError as exc:
                return False, str(exc)
            if descriptor.value_kind == "redirect_output" and self._widget_is_enabled():
                values = self._current_values()
                if not values:
                    return True, ""
            if descriptor.param_type == "argument" and descriptor.required and not tokens:
                return False, f"{descriptor.display_name} is required."
            if descriptor.param_type == "option" and descriptor.required and not tokens:
                return False, f"{descriptor.display_name} is required."
            return True, ""


    class UpdateCheckWorker(QtCore.QObject):
        finished = QtCore.pyqtSignal(dict)
        failed = QtCore.pyqtSignal(str)

        def run(self):
            request = urllib.request.Request(
                __github_latest_release_api__,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"VHSTTX/{__version__}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.URLError as exc:
                self.failed.emit(str(exc))
                return
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.finished.emit(parse_latest_release_payload(payload))


    class UpdateDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._update_thread = None
            self._update_worker = None
            self._latest_release_url = __github_releases_url__

            self.setWindowTitle("VHSTTX Updates")
            self.resize(540, 260)
            self.setModal(False)

            layout = QtWidgets.QVBoxLayout(self)

            info_group = QtWidgets.QGroupBox("Version Information")
            info_layout = QtWidgets.QGridLayout(info_group)
            info_layout.addWidget(QtWidgets.QLabel("Current Version"), 0, 0)
            self.current_version_value = QtWidgets.QLabel(__display_version__)
            self.current_version_value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            info_layout.addWidget(self.current_version_value, 0, 1)
            info_layout.addWidget(QtWidgets.QLabel("Latest Release"), 1, 0)
            self.latest_version_value = QtWidgets.QLabel("Not checked")
            self.latest_version_value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            info_layout.addWidget(self.latest_version_value, 1, 1)
            info_layout.addWidget(QtWidgets.QLabel("Status"), 2, 0)
            self.update_status_value = QtWidgets.QLabel("Ready")
            self.update_status_value.setWordWrap(True)
            info_layout.addWidget(self.update_status_value, 2, 1)
            layout.addWidget(info_group)

            buttons_row = QtWidgets.QHBoxLayout()
            self.open_github_button = QtWidgets.QPushButton("Open GitHub")
            self.open_github_button.clicked.connect(self._open_github)
            self.check_updates_button = QtWidgets.QPushButton("Check Updates")
            self.check_updates_button.clicked.connect(self._check_updates)
            close_button = QtWidgets.QPushButton("Close")
            close_button.clicked.connect(self.close)
            buttons_row.addWidget(self.open_github_button)
            buttons_row.addWidget(self.check_updates_button)
            buttons_row.addStretch(1)
            buttons_row.addWidget(close_button)
            layout.addLayout(buttons_row)

        def _open_github(self, *_args):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._latest_release_url or __github_url__))

        def _check_updates(self, *_args):
            if self._update_thread is not None:
                return
            self.update_status_value.setText("Checking GitHub releases...")
            self.check_updates_button.setEnabled(False)
            self._update_thread = QtCore.QThread(self)
            self._update_worker = UpdateCheckWorker()
            self._update_worker.moveToThread(self._update_thread)
            self._update_thread.started.connect(self._update_worker.run)
            self._update_worker.finished.connect(self._update_check_finished)
            self._update_worker.failed.connect(self._update_check_failed)
            self._update_worker.finished.connect(self._update_thread.quit)
            self._update_worker.failed.connect(self._update_thread.quit)
            self._update_thread.finished.connect(self._cleanup_update_thread)
            self._update_thread.start()

        def _update_check_finished(self, release_info):
            latest_display = release_info.get("display_version") or "Unknown"
            latest_tag = release_info.get("tag_name") or ""
            self.latest_version_value.setText(latest_display)
            self._latest_release_url = release_info.get("html_url") or __github_releases_url__
            comparison = compare_versions(__version__, latest_tag)
            if comparison < 0:
                self.update_status_value.setText("Installed version is newer than the latest GitHub release.")
            elif comparison > 0:
                self.update_status_value.setText(f"Update available: {latest_display}")
            else:
                self.update_status_value.setText("You already have the latest release.")

        def _update_check_failed(self, message):
            self.update_status_value.setText(f"Update check failed: {message}")

        def _cleanup_update_thread(self):
            self.check_updates_button.setEnabled(True)
            if self._update_worker is not None:
                self._update_worker.deleteLater()
            if self._update_thread is not None:
                self._update_thread.deleteLater()
            self._update_worker = None
            self._update_thread = None

        def closeEvent(self, event):
            if self._update_thread is not None:
                self._update_thread.quit()
                self._update_thread.wait(2000)
            super().closeEvent(event)


    class VHSTTXLauncherWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.root_command = load_teletext_root_command()
            self.command_tree = build_command_tree(self.root_command)
            self.current_node = None
            self.param_editors = []
            self._process = None
            self._log_lines = []
            self._current_log_line = ""
            self._favorite_paths = self._load_favorite_paths()
            self._monitor_font_family = self._load_monitor_font_family()
            self._updates_dialog = None

            self.setWindowTitle("VHSTTX Launcher")
            self.resize(1320, 880)
            self.setMinimumSize(1040, 720)
            icon_path = pathlib.Path(__file__).with_name("vhsttxgui.png")
            if icon_path.exists():
                self.setWindowIcon(QtGui.QIcon(str(icon_path)))

            self._build_ui()
            self._populate_tree()
            self._select_first_command()

        def _build_ui(self):
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            root_layout = QtWidgets.QVBoxLayout(central)
            root_layout.setContentsMargins(10, 10, 10, 10)
            root_layout.setSpacing(8)

            working_row = QtWidgets.QHBoxLayout()
            working_row.setSpacing(8)
            working_row.addWidget(QtWidgets.QLabel("Working Directory"))
            self.cwd_edit = QtWidgets.QLineEdit(os.getcwd())
            self.cwd_edit.textChanged.connect(self._update_command_preview)
            cwd_button = QtWidgets.QPushButton("Browse...")
            cwd_button.clicked.connect(self._choose_working_directory)
            working_row.addWidget(self.cwd_edit, 1)
            working_row.addWidget(cwd_button)
            root_layout.addLayout(working_row)

            splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            root_layout.addWidget(splitter, 1)

            left_widget = QtWidgets.QWidget()
            left_layout = QtWidgets.QVBoxLayout(left_widget)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(6)

            left_layout.addWidget(QtWidgets.QLabel("Command List"))
            self.list_mode_tabs = QtWidgets.QTabBar()
            self.list_mode_tabs.addTab("ALL")
            self.list_mode_tabs.setTabData(0, LIST_MODE_ALL)
            self.list_mode_tabs.addTab("Favorite")
            self.list_mode_tabs.setTabData(1, LIST_MODE_FAVORITE)
            self.list_mode_tabs.addTab("Main")
            self.list_mode_tabs.setTabData(2, LIST_MODE_PRIMARY)
            self.list_mode_tabs.addTab("Additional")
            self.list_mode_tabs.setTabData(3, LIST_MODE_ADDITIONAL)
            self.list_mode_tabs.setCurrentIndex(2)
            self.list_mode_tabs.currentChanged.connect(self._list_mode_changed)
            left_layout.addWidget(self.list_mode_tabs)

            self.command_tree_widget = QtWidgets.QTreeWidget()
            self.command_tree_widget.setHeaderHidden(True)
            self.command_tree_widget.itemSelectionChanged.connect(self._command_selection_changed)
            left_layout.addWidget(self.command_tree_widget, 1)
            splitter.addWidget(left_widget)

            right_widget = QtWidgets.QWidget()
            right_layout = QtWidgets.QVBoxLayout(right_widget)
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(8)
            splitter.addWidget(right_widget)
            splitter.setStretchFactor(1, 1)

            title_row = QtWidgets.QHBoxLayout()
            self.command_title = QtWidgets.QLabel("Choose a command")
            title_font = self.command_title.font()
            title_font.setPointSize(title_font.pointSize() + 3)
            title_font.setBold(True)
            self.command_title.setFont(title_font)
            title_row.addWidget(self.command_title, 1)
            self.favorite_button = QtWidgets.QPushButton("Add Favorite")
            self.favorite_button.setCheckable(True)
            self.favorite_button.setEnabled(False)
            self.favorite_button.toggled.connect(self._favorite_toggled)
            title_row.addWidget(self.favorite_button)
            self.field_editor_button = QtWidgets.QPushButton("Field Editor")
            self.field_editor_button.setEnabled(False)
            self.field_editor_button.clicked.connect(self._open_field_editor)
            title_row.addWidget(self.field_editor_button)
            self.updates_button = QtWidgets.QPushButton("Updates")
            self.updates_button.clicked.connect(self._open_updates_dialog)
            title_row.addWidget(self.updates_button)
            right_layout.addLayout(title_row)

            self.command_help = QtWidgets.QLabel("Select a leaf command on the left to configure its options.")
            self.command_help.setWordWrap(True)
            right_layout.addWidget(self.command_help)

            self.command_path_label = QtWidgets.QLabel("")
            self.command_path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.command_path_label.setStyleSheet("color: #666;")
            right_layout.addWidget(self.command_path_label)

            body_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
            right_layout.addWidget(body_splitter, 1)

            params_container = QtWidgets.QWidget()
            params_layout = QtWidgets.QVBoxLayout(params_container)
            params_layout.setContentsMargins(0, 0, 0, 0)
            params_layout.setSpacing(10)
            body_splitter.addWidget(params_container)

            self.scroll_area = QtWidgets.QScrollArea()
            self.scroll_area.setWidgetResizable(True)
            params_layout.addWidget(self.scroll_area, 1)

            self.param_container = QtWidgets.QWidget()
            self.param_layout = QtWidgets.QVBoxLayout(self.param_container)
            self.param_layout.setContentsMargins(6, 6, 6, 6)
            self.param_layout.setSpacing(4)
            self.scroll_area.setWidget(self.param_container)

            extras_group = QtWidgets.QGroupBox("Additional Args")
            extras_layout = QtWidgets.QVBoxLayout(extras_group)
            self.extra_args_edit = QtWidgets.QLineEdit()
            self.extra_args_edit.setPlaceholderText("Optional raw arguments for advanced cases")
            self.extra_args_edit.textChanged.connect(self._update_command_preview)
            extras_layout.addWidget(self.extra_args_edit)
            params_layout.addWidget(extras_group)

            preview_group = QtWidgets.QGroupBox("Command Preview")
            preview_layout = QtWidgets.QVBoxLayout(preview_group)
            self.command_preview_edit = QtWidgets.QPlainTextEdit()
            self.command_preview_edit.setReadOnly(True)
            self.command_preview_edit.setMaximumBlockCount(32)
            self.command_preview_edit.setFixedHeight(82)
            self.command_preview_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            preview_layout.addWidget(self.command_preview_edit)
            params_layout.addWidget(preview_group)

            controls_row = QtWidgets.QHBoxLayout()
            self.run_button = QtWidgets.QPushButton("Run")
            self.stop_button = QtWidgets.QPushButton("Stop")
            self.stop_button.setEnabled(False)
            self.reset_button = QtWidgets.QPushButton("Reset Options")
            self.copy_button = QtWidgets.QPushButton("Copy Command")
            self.clear_log_button = QtWidgets.QPushButton("Clear Log")
            self.run_button.clicked.connect(self._run_command)
            self.stop_button.clicked.connect(self._stop_process)
            self.reset_button.clicked.connect(self._reset_current_command)
            self.copy_button.clicked.connect(self._copy_command_preview)
            self.clear_log_button.clicked.connect(self._clear_output_log)
            controls_row.addWidget(self.run_button)
            controls_row.addWidget(self.stop_button)
            controls_row.addStretch(1)
            controls_row.addWidget(self.reset_button)
            controls_row.addWidget(self.copy_button)
            controls_row.addWidget(self.clear_log_button)
            params_layout.addLayout(controls_row)

            log_group = QtWidgets.QGroupBox("Monitor")
            log_group.setStyleSheet(
                "QGroupBox {"
                " border: 1px solid #2f5f2f;"
                " border-radius: 4px;"
                " margin-top: 10px;"
                " color: #8fd18f;"
                "}"
                "QGroupBox::title {"
                " subcontrol-origin: margin;"
                " left: 8px;"
                " padding: 0 4px;"
                "}"
            )
            log_layout = QtWidgets.QVBoxLayout(log_group)
            self.output_log = QtWidgets.QTextEdit()
            self.output_log.setReadOnly(True)
            self.output_log.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
            log_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            if self._monitor_font_family:
                log_font.setFamily(self._monitor_font_family)
            self.output_log.setFont(log_font)
            self.output_log.setStyleSheet(
                "QTextEdit { background: #000000; color: #40ff40; border: none; }"
            )
            log_layout.addWidget(self.output_log)
            body_splitter.addWidget(log_group)
            body_splitter.setSizes([560, 260])

            self.statusBar().showMessage("Ready")
            self._clear_output_log()

        def _open_updates_dialog(self, *_args):
            if self._updates_dialog is None:
                self._updates_dialog = UpdateDialog(self)
            self._updates_dialog.show()
            self._updates_dialog.raise_()
            self._updates_dialog.activateWindow()

        def _choose_working_directory(self, *_args):
            current = self.cwd_edit.text().strip() or os.getcwd()
            chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Working Directory", current)
            if chosen:
                self.cwd_edit.setText(chosen)

        def _load_monitor_font_family(self):
            font_path = pathlib.Path(__file__).with_name("teletext2.ttf")
            if font_path.exists():
                font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
                if font_id != -1:
                    families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        return families[0]
            return None

        def _settings(self):
            return QtCore.QSettings(LAUNCHER_SETTINGS_ORGANISATION, LAUNCHER_SETTINGS_APPLICATION)

        def _load_favorite_paths(self):
            value = self._settings().value("favorites/commands", [], type=list)
            return {str(item) for item in (value or []) if str(item).strip()}

        def _save_favorite_paths(self):
            self._settings().setValue("favorites/commands", sorted(self._favorite_paths))
            self._settings().sync()

        def _load_hidden_layout_map(self):
            path = launcher_field_layout_path()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except json.JSONDecodeError:
                return {}
            if not isinstance(data, dict):
                return {}
            result = {}
            for key, value in data.items():
                if isinstance(value, list):
                    result[str(key)] = {
                        "hidden": [str(item) for item in value if str(item).strip()],
                        "labels": {},
                        "order": [],
                    }
                elif isinstance(value, dict):
                    hidden = value.get("hidden", [])
                    labels = value.get("labels", {})
                    order = value.get("order", [])
                    if not isinstance(hidden, list):
                        hidden = []
                    if not isinstance(labels, dict):
                        labels = {}
                    if not isinstance(order, list):
                        order = []
                    result[str(key)] = {
                        "hidden": [str(item) for item in hidden if str(item).strip()],
                        "labels": {
                            str(label_key): str(label_value)
                            for label_key, label_value in labels.items()
                            if str(label_key).strip() and str(label_value).strip()
                        },
                        "order": [str(item) for item in order if str(item).strip()],
                    }
            return result

        def _load_hidden_param_names(self, path):
            data = self._load_hidden_layout_map()
            return set(data.get(favourite_path_key(path), {}).get("hidden", []))

        def _load_label_overrides(self, path):
            data = self._load_hidden_layout_map()
            return dict(data.get(favourite_path_key(path), {}).get("labels", {}))

        def _load_param_order(self, path):
            data = self._load_hidden_layout_map()
            return list(data.get(favourite_path_key(path), {}).get("order", []))

        def _save_field_layout(self, path, hidden_names, label_overrides, order_names):
            layout_path = launcher_field_layout_path()
            layout_path.parent.mkdir(parents=True, exist_ok=True)
            data = self._load_hidden_layout_map()
            data[favourite_path_key(path)] = {
                "hidden": sorted(set(hidden_names)),
                "labels": {
                    str(key): str(value)
                    for key, value in dict(label_overrides or {}).items()
                    if str(key).strip() and str(value).strip()
                },
                "order": [
                    str(name) for name in (order_names or ())
                    if str(name).strip()
                ],
            }
            layout_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

        def _current_list_mode(self):
            index = self.list_mode_tabs.currentIndex()
            mode = self.list_mode_tabs.tabData(index)
            return mode if mode else LIST_MODE_PRIMARY

        def _matches_list_mode(self, node, mode):
            if not command_visible_for_platform(node, os.name):
                return False
            key = favourite_path_key(node.path)
            if mode == LIST_MODE_FAVORITE:
                return key in self._favorite_paths
            if mode == LIST_MODE_PRIMARY:
                return any(node.path == candidate.path for candidate in list_mode_leaf_nodes(self.command_tree, mode, self._favorite_paths, os.name))
            if mode == LIST_MODE_ADDITIONAL:
                return any(node.path == candidate.path for candidate in list_mode_leaf_nodes(self.command_tree, mode, self._favorite_paths, os.name))
            return True

        def _populate_tree(self):
            self.command_tree_widget.clear()
            mode = self._current_list_mode()
            if mode == LIST_MODE_FAVORITE:
                for node in list_mode_leaf_nodes(self.command_tree, mode, self._favorite_paths, os.name):
                    self.command_tree_widget.addTopLevelItem(self._build_tree_item(node))
            elif mode in {LIST_MODE_PRIMARY, LIST_MODE_ADDITIONAL}:
                app_nodes = []
                for node in list_mode_leaf_nodes(self.command_tree, mode, self._favorite_paths, os.name):
                    if node.path[:1] == ("apps",):
                        app_nodes.append(node)
                    else:
                        self.command_tree_widget.addTopLevelItem(self._build_tree_item(node))
                if app_nodes:
                    apps_group = QtWidgets.QTreeWidgetItem([format_command_label("apps")])
                    apps_group.setForeground(0, QtGui.QBrush(QtGui.QColor("#305080")))
                    apps_group.setData(0, QtCore.Qt.UserRole, CommandNode(
                        name="apps",
                        path=("apps",),
                        help_text="GUI applications.",
                        is_group=True,
                    ))
                    for node in app_nodes:
                        apps_group.addChild(self._build_tree_item(node))
                    self.command_tree_widget.addTopLevelItem(apps_group)
            else:
                for child in self.command_tree.children:
                    filtered = self._build_filtered_tree_item(child, mode)
                    if filtered is not None:
                        self.command_tree_widget.addTopLevelItem(filtered)
            self.command_tree_widget.expandAll()

        def _build_filtered_tree_item(self, node, mode):
            if not node.is_group:
                return self._build_tree_item(node) if self._matches_list_mode(node, mode) else None
            item = QtWidgets.QTreeWidgetItem([format_command_label(node.name)])
            item.setData(0, QtCore.Qt.UserRole, node)
            if node.help_text:
                item.setToolTip(0, node.help_text)
            item.setForeground(0, QtGui.QBrush(QtGui.QColor("#305080")))
            for child in node.children:
                filtered_child = self._build_filtered_tree_item(child, mode)
                if filtered_child is not None:
                    item.addChild(filtered_child)
            if item.childCount() == 0:
                return None
            return item

        def _list_mode_changed(self, *_args):
            selected_path = self.current_node.path if self.current_node is not None else None
            self._populate_tree()
            if selected_path:
                self._restore_selection(selected_path)
            if not self.command_tree_widget.selectedItems():
                self._select_first_command()

        def _build_tree_item(self, node):
            item = QtWidgets.QTreeWidgetItem([format_command_label(node.name)])
            item.setData(0, QtCore.Qt.UserRole, node)
            if node.help_text:
                item.setToolTip(0, node.help_text)
            if node.is_group:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#305080")))
            for child in node.children:
                item.addChild(self._build_tree_item(child))
            return item

        def _select_first_command(self):
            root = self.command_tree_widget.invisibleRootItem()
            first = self._find_first_leaf_item(root)
            if first is not None:
                self.command_tree_widget.setCurrentItem(first)

        def _find_first_leaf_item(self, item):
            for index in range(item.childCount()):
                child = item.child(index)
                node = child.data(0, QtCore.Qt.UserRole)
                if node is not None and not node.is_group:
                    return child
                nested = self._find_first_leaf_item(child)
                if nested is not None:
                    return nested
            return None

        def _command_selection_changed(self):
            items = self.command_tree_widget.selectedItems()
            if not items:
                return
            self.current_node = items[0].data(0, QtCore.Qt.UserRole)
            self._rebuild_param_panel()

        def _clear_param_layout(self):
            while self.param_layout.count():
                item = self.param_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                elif item.layout() is not None:
                    item.layout().deleteLater()

        def _rebuild_param_panel(self):
            self._clear_param_layout()
            self.param_editors = []
            self.extra_args_edit.clear()
            node = self.current_node
            if node is None:
                self.command_title.setText("Choose a command")
                self.command_help.setText("")
                self.command_path_label.setText("")
                self.favorite_button.setEnabled(False)
                self.field_editor_button.setEnabled(False)
                self.run_button.setEnabled(False)
                self._update_command_preview()
                return

            if node.path == TTVIEWER_COMMAND_PATH:
                self.command_title.setText("Teletext Viewer")
                self.command_path_label.setText("CLI path: ttviewer")
            else:
                self.command_title.setText(" ".join(node.path) if node.path else "teletext")
                self.command_path_label.setText(f"CLI path: teletext {' '.join(node.path)}".strip())
            self.command_help.setText(node.help_text or "No help text for this command.")
            self.favorite_button.setEnabled(not node.is_group)
            self.field_editor_button.setEnabled(not node.is_group)
            key = favourite_path_key(node.path)
            self.favorite_button.blockSignals(True)
            self.favorite_button.setChecked(key in self._favorite_paths)
            self.favorite_button.setText("Remove Favorite" if key in self._favorite_paths else "Add Favorite")
            self.favorite_button.blockSignals(False)

            if node.is_group:
                placeholder = QtWidgets.QLabel("Choose a concrete subcommand from the tree.")
                placeholder.setWordWrap(True)
                self.param_layout.addWidget(placeholder)
                self.param_layout.addStretch(1)
                self.run_button.setEnabled(False)
                self._update_command_preview()
                return

            output_arguments = []
            arguments = []
            options = []
            hidden_names = self._load_hidden_param_names(node.path)
            label_overrides = self._load_label_overrides(node.path)
            param_order = self._load_param_order(node.path)
            descriptors = filter_descriptors_for_platform(
                order_param_descriptors(describe_command(node.command), param_order),
                node.path,
                os.name,
            )
            for descriptor in descriptors:
                if descriptor.name in hidden_names:
                    continue
                if descriptor.name in label_overrides:
                    descriptor.display_name = label_overrides[descriptor.name]
                editor = ParamEditorWidget(descriptor)
                editor.changed.connect(self._update_command_preview)
                self.param_editors.append(editor)
                if descriptor.value_kind == "redirect_output":
                    output_arguments.append(editor)
                elif descriptor.param_type == "argument":
                    arguments.append(editor)
                else:
                    options.append(editor)

            if output_arguments or arguments:
                group = QtWidgets.QGroupBox("Arguments")
                layout = QtWidgets.QVBoxLayout(group)
                for editor in arguments:
                    layout.addWidget(editor)
                for editor in output_arguments:
                    layout.addWidget(editor)
                self.param_layout.addWidget(group)

            if options:
                group = QtWidgets.QGroupBox("Options")
                layout = QtWidgets.QVBoxLayout(group)
                for editor in options:
                    layout.addWidget(editor)
                self.param_layout.addWidget(group)

            self.param_layout.addStretch(1)
            self.run_button.setEnabled(True)
            self._update_command_preview()

        def _collect_command_tokens(self, strict=False):
            if self.current_node is None or self.current_node.is_group:
                return [], [], None
            if self.current_node.path == TTVIEWER_COMMAND_PATH:
                tokens = ["ttviewer"]
            else:
                tokens = ["teletext", *self.current_node.path]
            errors = []
            redirect_output = None
            for editor in self.param_editors:
                valid, message = editor.validate()
                if not valid:
                    errors.append(message)
                    continue
                if editor.descriptor.value_kind == "redirect_output":
                    values = editor._current_values()
                    redirect_output = values[0] if values else None
                    continue
                try:
                    tokens.extend(editor.current_tokens())
                except ValueError as exc:
                    errors.append(str(exc))
            extra_args = self.extra_args_edit.text().strip()
            if extra_args:
                tokens.extend(split_text_values(extra_args))
            if strict and errors:
                raise ValueError("\n".join(errors))
            return tokens, errors, redirect_output

        def _update_command_preview(self, *_args):
            tokens, errors, redirect_output = self._collect_command_tokens(strict=False)
            preview = preview_command_text(tokens) if tokens else ""
            if redirect_output:
                preview = f"{preview} > {shlex.quote(redirect_output)}"
            if errors:
                preview = (preview + "\n\nInvalid:\n- " + "\n- ".join(errors)).strip()
            self.command_preview_edit.setPlainText(preview)

        def _favorite_toggled(self, checked):
            if self.current_node is None or self.current_node.is_group:
                return
            key = favourite_path_key(self.current_node.path)
            if checked:
                self._favorite_paths.add(key)
            else:
                self._favorite_paths.discard(key)
            self.favorite_button.setText("Remove Favorite" if checked else "Add Favorite")
            self._save_favorite_paths()
            selected_path = self.current_node.path
            self._populate_tree()
            self._restore_selection(selected_path)

        def _open_field_editor(self, *_args):
            if self.current_node is None or self.current_node.is_group:
                return
            descriptors = order_param_descriptors(
                describe_command(self.current_node.command),
                self._load_param_order(self.current_node.path),
            )
            hidden_names = self._load_hidden_param_names(self.current_node.path)
            label_overrides = self._load_label_overrides(self.current_node.path)
            dialog = FieldEditorDialog(descriptors, hidden_names, label_overrides, parent=self)
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return
            self._save_field_layout(
                self.current_node.path,
                dialog.hidden_names(),
                dialog.label_overrides(),
                dialog.field_order(),
            )
            self._rebuild_param_panel()

        def _restore_selection(self, path):
            item = self._find_item_by_path(self.command_tree_widget.invisibleRootItem(), path)
            if item is not None:
                self.command_tree_widget.setCurrentItem(item)

        def _find_item_by_path(self, item, path):
            for index in range(item.childCount()):
                child = item.child(index)
                node = child.data(0, QtCore.Qt.UserRole)
                if node is not None and node.path == path:
                    return child
                nested = self._find_item_by_path(child, path)
                if nested is not None:
                    return nested
            return None

        def _copy_command_preview(self, *_args):
            text = self.command_preview_edit.toPlainText().strip()
            if not text:
                return
            QtWidgets.QApplication.clipboard().setText(text)
            self.statusBar().showMessage("Command copied to clipboard.", 2500)

        def _reset_current_command(self, *_args):
            for editor in self.param_editors:
                editor.reset()
            self.extra_args_edit.clear()
            self._update_command_preview()

        def _clear_output_log(self, *_args):
            self._log_lines = []
            self._current_log_line = ""
            self.output_log.setHtml(_ansi_text_to_html("", font_family=self._monitor_font_family))

        def _ensure_process(self):
            if self._process is None:
                self._process = QtCore.QProcess(self)
                self._process.readyReadStandardOutput.connect(self._append_process_output)
                self._process.readyReadStandardError.connect(self._append_process_error_output)
                self._process.finished.connect(self._process_finished)
                self._process.errorOccurred.connect(self._process_error)
            return self._process

        def _append_process_output(self):
            if self._process is None:
                return
            data = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if not data:
                return
            self._append_log_chunk(data)

        def _append_process_error_output(self):
            if self._process is None:
                return
            data = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
            if not data:
                return
            self._append_log_chunk(data)

        def _append_log_chunk(self, data):
            for char in data:
                if char == "\r":
                    self._current_log_line = ""
                elif char == "\n":
                    self._log_lines.append(self._current_log_line)
                    self._current_log_line = ""
                else:
                    self._current_log_line += char
            if len(self._log_lines) > 4000:
                self._log_lines = self._log_lines[-4000:]
            rendered_lines = list(self._log_lines)
            if self._current_log_line or not rendered_lines:
                rendered_lines.append(self._current_log_line)
            self.output_log.setHtml(
                _ansi_text_to_html("\n".join(rendered_lines), font_family=self._monitor_font_family)
            )
            cursor = self.output_log.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            self.output_log.setTextCursor(cursor)
            self.output_log.ensureCursorVisible()

        def _run_command(self, *_args):
            try:
                preview_tokens, _, redirect_output = self._collect_command_tokens(strict=True)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "Invalid Command", str(exc))
                return
            if not preview_tokens:
                return
            cwd = self.cwd_edit.text().strip() or os.getcwd()
            if not os.path.isdir(cwd):
                QtWidgets.QMessageBox.warning(self, "Invalid Working Directory", f"Directory does not exist:\n{cwd}")
                return
            process = self._ensure_process()
            if process.state() != QtCore.QProcess.NotRunning:
                QtWidgets.QMessageBox.information(self, "Process Running", "Stop the current process before starting a new one.")
                return
            program, args = launcher_process_command(preview_tokens)

            self._log_lines = []
            self._current_log_line = ""
            preview_line = preview_command_text(preview_tokens)
            if redirect_output:
                preview_line = f"{preview_line} > {shlex.quote(redirect_output)}"
            self._append_log_chunk(f"$ {preview_line}\n")
            process.setWorkingDirectory(cwd)
            process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)
            process.setStandardOutputFile(redirect_output or "")
            process.start(program, args)
            if not process.waitForStarted(3000):
                QtWidgets.QMessageBox.warning(self, "Launch Failed", "Unable to start the selected command.")
                return
            self.run_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.statusBar().showMessage("Process started.")

        def _stop_process(self, *_args):
            if self._process is None or self._process.state() == QtCore.QProcess.NotRunning:
                return
            self._process.terminate()
            QtCore.QTimer.singleShot(2000, self._kill_process_if_needed)
            self.statusBar().showMessage("Stopping process...")

        def _kill_process_if_needed(self):
            if self._process is not None and self._process.state() != QtCore.QProcess.NotRunning:
                self._process.kill()

        def _process_finished(self, exit_code, exit_status):
            status_text = "finished" if exit_status == QtCore.QProcess.NormalExit else "crashed"
            self._append_log_chunk(f"\n[process {status_text}, exit code {exit_code}]\n")
            self.run_button.setEnabled(self.current_node is not None and not self.current_node.is_group)
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage(f"Process {status_text}.", 3000)

        def _process_error(self, error):
            self._append_log_chunk(f"\n[process error: {error}]\n")
            self.statusBar().showMessage("Process error.", 3000)

        def closeEvent(self, event):
            if self._process is not None and self._process.state() != QtCore.QProcess.NotRunning:
                self._process.terminate()
                if not self._process.waitForFinished(1500):
                    self._process.kill()
                    self._process.waitForFinished(1500)
                self._process.close()
            if self._process is not None:
                self._process.deleteLater()
            if self._updates_dialog is not None:
                self._updates_dialog.close()
            super().closeEvent(event)


def main():
    if QtWidgets is None:
        raise SystemExit(f"PyQt5 is not installed. Launcher unavailable: {IMPORT_ERROR}")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = VHSTTXLauncherWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
