import json
import multiprocessing as mp
import os
import shlex
import sys

import numpy as np

from teletext.capturefix import DEFAULT_FIX_CAPTURE_CARD, normalise_fix_capture_card

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    QtCore = None
    QtGui = None
    QtWidgets = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None
    _APP = None


if IMPORT_ERROR is None:
    from teletext.vbi.line import (
        BRIGHTNESS_COEFF_DEFAULT,
        CONTRAST_COEFF_DEFAULT,
        GAIN_COEFF_DEFAULT,
        Line,
        SHARPNESS_COEFF_DEFAULT,
    )


DEFAULT_CONTROLS = (
    50,
    50,
    50,
    50,
    BRIGHTNESS_COEFF_DEFAULT if IMPORT_ERROR is None else 48.0,
    SHARPNESS_COEFF_DEFAULT if IMPORT_ERROR is None else 3.0,
    GAIN_COEFF_DEFAULT if IMPORT_ERROR is None else 0.5,
    CONTRAST_COEFF_DEFAULT if IMPORT_ERROR is None else 0.5,
)
DEFAULT_LINE_COUNT = 32
PRESET_FILE_FILTER = 'VBI Tune Presets (*.vtnargs *.txt);;All Files (*)'
LOCAL_PRESET_FILE = 'vbi-tune-presets.json'


def _normalise_decoder_tuning(decoder_tuning):
    if decoder_tuning is None:
        return None
    return {
        'tape_format': decoder_tuning.get('tape_format', 'vhs'),
        'extra_roll': int(decoder_tuning.get('extra_roll', 0)),
        'line_start_range': tuple(decoder_tuning.get('line_start_range', (0, 0))),
    }


def _normalise_line_selection(line_selection, line_count=DEFAULT_LINE_COUNT):
    if line_selection is None:
        return frozenset(range(1, line_count + 1))
    return frozenset(
        line for line in (int(value) for value in line_selection)
        if 1 <= line <= line_count
    )


def format_fix_capture_card(fix_capture_card):
    settings = normalise_fix_capture_card(fix_capture_card)
    if not settings['enabled']:
        return ''
    return f"-fcc {int(settings['seconds'])} {int(settings['interval_minutes'])}"


def format_decoder_tuning(decoder_tuning):
    if decoder_tuning is None:
        return ''
    line_start_range = tuple(int(value) for value in decoder_tuning['line_start_range'])
    return (
        f"-f {decoder_tuning['tape_format']} --extra-roll {int(decoder_tuning['extra_roll'])} "
        f"--line-start-range {line_start_range[0]} {line_start_range[1]}"
    )


def format_line_selection(line_selection, line_count=DEFAULT_LINE_COUNT):
    selected = _normalise_line_selection(line_selection, line_count=line_count)
    all_lines = frozenset(range(1, line_count + 1))
    if selected == all_lines:
        return ''
    ignored = tuple(sorted(all_lines - selected))
    used = tuple(sorted(selected))
    if used and len(used) <= len(ignored):
        return '-ul ' + ','.join(str(line) for line in used)
    return '-il ' + ','.join(str(line) for line in ignored)


def format_signal_controls(
    brightness,
    sharpness,
    gain,
    contrast,
    brightness_coeff=DEFAULT_CONTROLS[4],
    sharpness_coeff=DEFAULT_CONTROLS[5],
    gain_coeff=DEFAULT_CONTROLS[6],
    contrast_coeff=DEFAULT_CONTROLS[7],
):
    return (
        f'-bn {int(brightness)} -sp {int(sharpness)} -gn {int(gain)} -ct {int(contrast)} '
        f'-bncf {float(brightness_coeff):g} -spcf {float(sharpness_coeff):g} '
        f'-gncf {float(gain_coeff):g} -ctcf {float(contrast_coeff):g}'
    )


def format_tuning_args(controls, decoder_tuning=None, line_selection=None, line_count=DEFAULT_LINE_COUNT, fix_capture_card=None):
    signal_part = format_signal_controls(*controls)
    decoder_part = format_decoder_tuning(decoder_tuning)
    line_part = format_line_selection(line_selection, line_count=line_count)
    fix_part = format_fix_capture_card(fix_capture_card)
    parts = [signal_part]
    if decoder_part:
        parts.append(decoder_part)
    if line_part:
        parts.append(line_part)
    if fix_part:
        parts.append(fix_part)
    return ' '.join(parts)


def parse_signal_controls_args(text, defaults=DEFAULT_CONTROLS):
    values = {
        'brightness': int(defaults[0]),
        'sharpness': int(defaults[1]),
        'gain': int(defaults[2]),
        'contrast': int(defaults[3]),
        'brightness_coeff': float(defaults[4]),
        'sharpness_coeff': float(defaults[5]),
        'gain_coeff': float(defaults[6]),
        'contrast_coeff': float(defaults[7]),
    }
    option_map = {
        '-bn': 'brightness',
        '--brightness': 'brightness',
        '-sp': 'sharpness',
        '--sharpness': 'sharpness',
        '-gn': 'gain',
        '--gain': 'gain',
        '-ct': 'contrast',
        '--contrast': 'contrast',
        '-bncf': 'brightness_coeff',
        '--brightness-coeff': 'brightness_coeff',
        '-spcf': 'sharpness_coeff',
        '--sharpness-coeff': 'sharpness_coeff',
        '-gncf': 'gain_coeff',
        '--gain-coeff': 'gain_coeff',
        '-ctcf': 'contrast_coeff',
        '--contrast-coeff': 'contrast_coeff',
    }
    tokens = shlex.split(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in option_map:
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            target = option_map[token]
            raw_value = tokens[index + 1]
            if target.endswith('_coeff'):
                try:
                    value = float(raw_value)
                except ValueError as exc:
                    raise ValueError(f'Invalid float for {token}: {raw_value!r}.') from exc
                if value < 0:
                    raise ValueError(f'Value for {token} must be zero or greater.')
            else:
                try:
                    value = int(raw_value)
                except ValueError as exc:
                    raise ValueError(f'Invalid integer for {token}: {raw_value!r}.') from exc
                if value < 0 or value > 100:
                    raise ValueError(f'Value for {token} must be between 0 and 100.')
            values[target] = value
            index += 2
            continue
        index += 1
    return (
        values['brightness'],
        values['sharpness'],
        values['gain'],
        values['contrast'],
        values['brightness_coeff'],
        values['sharpness_coeff'],
        values['gain_coeff'],
        values['contrast_coeff'],
    )


def parse_decoder_tuning_args(text, defaults=None, tape_formats=None):
    defaults = _normalise_decoder_tuning(defaults)
    if defaults is None:
        return None
    values = {
        'tape_format': defaults['tape_format'],
        'extra_roll': int(defaults['extra_roll']),
        'line_start_range': tuple(int(value) for value in defaults['line_start_range']),
    }
    tokens = shlex.split(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ('-f', '--tape-format'):
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            value = tokens[index + 1]
            if tape_formats and value not in tape_formats:
                raise ValueError(f'Invalid tape format for {token}: {value!r}.')
            values['tape_format'] = value
            index += 2
            continue
        if token == '--extra-roll':
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            try:
                values['extra_roll'] = int(tokens[index + 1])
            except ValueError as exc:
                raise ValueError(f'Invalid integer for {token}: {tokens[index + 1]!r}.') from exc
            index += 2
            continue
        if token == '--line-start-range':
            if index + 2 >= len(tokens):
                raise ValueError(f'Missing values for {token}.')
            try:
                start = int(tokens[index + 1])
                end = int(tokens[index + 2])
            except ValueError as exc:
                raise ValueError(f'Invalid integers for {token}.') from exc
            if start > end:
                raise ValueError(f'Values for {token} must be in ascending order.')
            values['line_start_range'] = (start, end)
            index += 3
            continue
        index += 1
    return values


def parse_line_selection_args(text, defaults=None, line_count=DEFAULT_LINE_COUNT):
    selected = set(_normalise_line_selection(defaults, line_count=line_count))
    tokens = shlex.split(text)
    index = 0
    all_lines = frozenset(range(1, line_count + 1))

    def parse_line_list(raw_value, token):
        values = []
        for item in raw_value.split(','):
            item = item.strip()
            if not item:
                raise ValueError(f'Invalid line list for {token}.')
            try:
                value = int(item)
            except ValueError as exc:
                raise ValueError(f'Invalid line number for {token}: {item!r}.') from exc
            if value < 1 or value > line_count:
                raise ValueError(f'Line numbers for {token} must be between 1 and {line_count}.')
            values.append(value)
        return frozenset(values)

    while index < len(tokens):
        token = tokens[index]
        if token in ('-il', '--ignore-line', '-ul', '--used-line'):
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            lines = parse_line_list(tokens[index + 1], token)
            if token in ('-ul', '--used-line'):
                selected = set(lines)
            else:
                selected -= set(lines)
            index += 2
            continue
        index += 1

    return frozenset(sorted(selected & all_lines))


def parse_fix_capture_card_args(text, defaults=None):
    settings = normalise_fix_capture_card(defaults)
    tokens = shlex.split(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ('-fcc', '--fix-capture-card'):
            if index + 2 >= len(tokens):
                raise ValueError(f'Missing values for {token}.')
            try:
                seconds = int(tokens[index + 1])
                interval_minutes = int(tokens[index + 2])
            except ValueError as exc:
                raise ValueError(f'Invalid integers for {token}.') from exc
            if seconds < 1 or interval_minutes < 1:
                raise ValueError(f'Values for {token} must be greater than zero.')
            settings.update({
                'enabled': True,
                'seconds': seconds,
                'interval_minutes': interval_minutes,
            })
            index += 3
            continue
        index += 1
    return settings


def parse_tuning_args(text, defaults=DEFAULT_CONTROLS, decoder_defaults=None, tape_formats=None, line_defaults=None, line_count=DEFAULT_LINE_COUNT, fix_capture_card_defaults=None):
    return (
        parse_signal_controls_args(text, defaults=defaults),
        parse_decoder_tuning_args(text, defaults=decoder_defaults, tape_formats=tape_formats),
        parse_line_selection_args(text, defaults=line_defaults, line_count=line_count),
        parse_fix_capture_card_args(text, defaults=fix_capture_card_defaults),
    )


def save_preset_text(path, text):
    content = text.strip()
    if not content:
        raise ValueError('Preset is empty.')
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)
        handle.write('\n')


def load_preset_text(path):
    with open(path, 'r', encoding='utf-8') as handle:
        lines = [
            line.strip()
            for line in handle
            if line.strip() and not line.lstrip().startswith('#')
        ]
    if not lines:
        raise ValueError('Preset file is empty.')
    return ' '.join(lines)


def default_local_preset_store_path(base_dir=None):
    root = os.path.expanduser(base_dir or '~')
    return os.path.join(root, '.vhsttx', LOCAL_PRESET_FILE)


def load_local_presets(path=None):
    path = path or default_local_preset_store_path()
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('Local preset store is invalid.')
    presets = {}
    for name, value in data.items():
        preset_name = str(name).strip()
        preset_value = str(value).strip()
        if preset_name and preset_value:
            presets[preset_name] = preset_value
    return dict(sorted(presets.items(), key=lambda item: item[0].lower()))


def save_local_presets(presets, path=None):
    path = path or default_local_preset_store_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    cleaned = {}
    for name, value in presets.items():
        preset_name = str(name).strip()
        preset_value = str(value).strip()
        if not preset_name:
            raise ValueError('Preset name is empty.')
        if not preset_value:
            raise ValueError(f'Preset {preset_name!r} is empty.')
        cleaned[preset_name] = preset_value
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(dict(sorted(cleaned.items(), key=lambda item: item[0].lower())), handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def save_local_preset(name, text, path=None):
    preset_name = str(name).strip()
    preset_text = str(text).strip()
    if not preset_name:
        raise ValueError('Preset name is empty.')
    if not preset_text:
        raise ValueError('Preset is empty.')
    presets = load_local_presets(path)
    presets[preset_name] = preset_text
    save_local_presets(presets, path)
    return preset_name


def delete_local_preset(name, path=None):
    preset_name = str(name).strip()
    if not preset_name:
        raise ValueError('Preset name is empty.')
    presets = load_local_presets(path)
    if preset_name not in presets:
        raise ValueError(f'Preset {preset_name!r} does not exist.')
    del presets[preset_name]
    save_local_presets(presets, path)


def _ensure_app():
    global _APP
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1] or ['teletext-vbituner'])
    _APP = app
    return app


def _scaled_pixmap(image, size):
    pixmap = QtGui.QPixmap.fromImage(image)
    return pixmap.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)


if IMPORT_ERROR is None:
    class VBITuningDialog(QtWidgets.QDialog):
        def __init__(
            self,
            title,
            controls,
            preview_provider=None,
            config=None,
            tape_format='vhs',
            live=False,
            decoder_tuning=None,
            tape_formats=None,
            line_selection=None,
            line_count=DEFAULT_LINE_COUNT,
            fix_capture_card=None,
            parent=None,
        ):
            super().__init__(parent)
            self.setWindowTitle(title)
            self._preview_provider = preview_provider
            self._config = config
            self._tape_format = tape_format
            self._live = live
            self._decoder_tuning_enabled = decoder_tuning is not None and tape_formats
            self._tape_formats = list(tape_formats or [])
            self._last_image = None
            self._external_change_callback = None
            self._updating_args_text = False
            self._show_preview = self._preview_provider is not None and self._config is not None
            self._decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._default_decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._line_count = int(line_count)
            self._default_line_selection = _normalise_line_selection(line_selection, line_count=self._line_count)
            self._default_fix_capture_card = normalise_fix_capture_card(fix_capture_card)
            self._last_preset_path = None
            self._local_preset_store_path = default_local_preset_store_path()
            self._local_presets = {}

            if self._show_preview:
                self.resize(960, 620)
            else:
                self.resize(520, 250)
                self.setMinimumWidth(460)
                self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            self._preview_label = None
            if self._show_preview:
                self._preview_label = QtWidgets.QLabel('Waiting for preview...')
                self._preview_label.setMinimumSize(720, 320)
                self._preview_label.setAlignment(QtCore.Qt.AlignCenter)
                self._preview_label.setFrameShape(QtWidgets.QFrame.StyledPanel)
                root.addWidget(self._preview_label, 1)

            controls_group = QtWidgets.QGroupBox('Signal Controls')
            form = QtWidgets.QGridLayout(controls_group)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
            root.addWidget(controls_group)

            form.addWidget(QtWidgets.QLabel('Value'), 0, 2)
            form.addWidget(QtWidgets.QLabel('Coeff'), 0, 3)

            self._sliders = {}
            self._spin_boxes = {}
            self._coeff_boxes = {}
            for row, (name, key, value, coeff) in enumerate((
                ('Brightness', 'brightness', controls[0], controls[4]),
                ('Sharpness', 'sharpness', controls[1], controls[5]),
                ('Gain', 'gain', controls[2], controls[6]),
                ('Contrast', 'contrast', controls[3], controls[7]),
            ), start=1):
                label = QtWidgets.QLabel(name)
                slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                slider.setRange(0, 100)
                slider.setValue(int(value))
                slider.setTickInterval(10)
                slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                spin_box = QtWidgets.QSpinBox()
                spin_box.setRange(0, 100)
                spin_box.setValue(int(value))
                spin_box.setSingleStep(1)
                spin_box.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
                spin_box.setAccelerated(True)
                spin_box.setMinimumWidth(68)
                slider.valueChanged.connect(spin_box.setValue)
                spin_box.valueChanged.connect(slider.setValue)
                slider.valueChanged.connect(self._controls_changed)
                coeff_box = QtWidgets.QDoubleSpinBox()
                coeff_box.setRange(0.0, 999.0)
                coeff_box.setDecimals(2)
                coeff_box.setValue(float(coeff))
                coeff_box.setSingleStep(0.1)
                coeff_box.setAccelerated(True)
                coeff_box.setMinimumWidth(84)
                coeff_box.valueChanged.connect(self._controls_changed)
                form.addWidget(label, row, 0)
                form.addWidget(slider, row, 1)
                form.addWidget(spin_box, row, 2)
                form.addWidget(coeff_box, row, 3)
                self._sliders[key] = slider
                self._spin_boxes[key] = spin_box
                self._coeff_boxes[key] = coeff_box

            if self._decoder_tuning_enabled:
                decoder_group = QtWidgets.QGroupBox('Decoder Tuning')
                decoder_form = QtWidgets.QGridLayout(decoder_group)
                decoder_form.setHorizontalSpacing(10)
                decoder_form.setVerticalSpacing(8)
                root.addWidget(decoder_group)

                decoder_form.addWidget(QtWidgets.QLabel('Template'), 0, 0)
                self._tape_format_box = QtWidgets.QComboBox()
                self._tape_format_box.addItems(self._tape_formats)
                current_index = max(self._tape_formats.index(self._decoder_tuning['tape_format']), 0) if self._decoder_tuning['tape_format'] in self._tape_formats else 0
                self._tape_format_box.setCurrentIndex(current_index)
                self._tape_format_box.currentIndexChanged.connect(self._controls_changed)
                decoder_form.addWidget(self._tape_format_box, 0, 1, 1, 3)

                decoder_form.addWidget(QtWidgets.QLabel('Extra Roll'), 1, 0)
                self._extra_roll_box = QtWidgets.QSpinBox()
                self._extra_roll_box.setRange(-64, 64)
                self._extra_roll_box.setValue(int(self._decoder_tuning['extra_roll']))
                self._extra_roll_box.setAccelerated(True)
                self._extra_roll_box.valueChanged.connect(self._controls_changed)
                decoder_form.addWidget(self._extra_roll_box, 1, 1)

                decoder_form.addWidget(QtWidgets.QLabel('Line Start Range'), 2, 0)
                self._line_start_start_box = QtWidgets.QSpinBox()
                self._line_start_start_box.setRange(0, 4096)
                self._line_start_start_box.setValue(int(self._decoder_tuning['line_start_range'][0]))
                self._line_start_start_box.setAccelerated(True)
                self._line_start_start_box.valueChanged.connect(self._line_start_range_changed)
                decoder_form.addWidget(self._line_start_start_box, 2, 1)

                self._line_start_end_box = QtWidgets.QSpinBox()
                self._line_start_end_box.setRange(0, 4096)
                self._line_start_end_box.setValue(int(self._decoder_tuning['line_start_range'][1]))
                self._line_start_end_box.setAccelerated(True)
                self._line_start_end_box.valueChanged.connect(self._line_start_range_changed)
                decoder_form.addWidget(self._line_start_end_box, 2, 2)

            line_group = QtWidgets.QGroupBox('Line Selection')
            line_form = QtWidgets.QGridLayout(line_group)
            line_form.setHorizontalSpacing(8)
            line_form.setVerticalSpacing(6)
            root.addWidget(line_group)

            self._line_checkboxes = {}
            for line in range(1, self._line_count + 1):
                checkbox = QtWidgets.QCheckBox(str(line))
                checkbox.setChecked(line in self._default_line_selection)
                checkbox.toggled.connect(self._controls_changed)
                row = (line - 1) // 8
                column = (line - 1) % 8
                line_form.addWidget(checkbox, row, column)
                self._line_checkboxes[line] = checkbox

            line_buttons = QtWidgets.QHBoxLayout()
            line_form.addLayout(line_buttons, (self._line_count // 8) + 1, 0, 1, 8)
            all_on_button = QtWidgets.QPushButton('All On')
            all_on_button.clicked.connect(self._enable_all_lines)
            line_buttons.addWidget(all_on_button)
            all_off_button = QtWidgets.QPushButton('All Off')
            all_off_button.clicked.connect(self._disable_all_lines)
            line_buttons.addWidget(all_off_button)
            line_buttons.addStretch(1)

            fix_group = QtWidgets.QGroupBox('Fix Capture Card')
            fix_form = QtWidgets.QGridLayout(fix_group)
            fix_form.setHorizontalSpacing(10)
            fix_form.setVerticalSpacing(8)
            root.addWidget(fix_group)

            self._fix_capture_card_enabled = QtWidgets.QCheckBox('Enable')
            self._fix_capture_card_enabled.setChecked(bool(self._default_fix_capture_card['enabled']))
            self._fix_capture_card_enabled.toggled.connect(self._controls_changed)
            fix_form.addWidget(self._fix_capture_card_enabled, 0, 0, 1, 2)

            fix_form.addWidget(QtWidgets.QLabel('Seconds'), 1, 0)
            self._fix_capture_card_seconds = QtWidgets.QSpinBox()
            self._fix_capture_card_seconds.setRange(1, 3600)
            self._fix_capture_card_seconds.setValue(int(self._default_fix_capture_card['seconds']))
            self._fix_capture_card_seconds.setAccelerated(True)
            self._fix_capture_card_seconds.valueChanged.connect(self._controls_changed)
            fix_form.addWidget(self._fix_capture_card_seconds, 1, 1)

            fix_form.addWidget(QtWidgets.QLabel('Interval Minutes'), 1, 2)
            self._fix_capture_card_interval = QtWidgets.QSpinBox()
            self._fix_capture_card_interval.setRange(1, 1440)
            self._fix_capture_card_interval.setValue(int(self._default_fix_capture_card['interval_minutes']))
            self._fix_capture_card_interval.setAccelerated(True)
            self._fix_capture_card_interval.valueChanged.connect(self._controls_changed)
            fix_form.addWidget(self._fix_capture_card_interval, 1, 3)

            args_row = QtWidgets.QHBoxLayout()
            args_row.addWidget(QtWidgets.QLabel('Args'))
            self._args_input = QtWidgets.QLineEdit()
            self._args_input.setPlaceholderText(format_tuning_args(
                DEFAULT_CONTROLS,
                self._default_decoder_tuning,
                self._default_line_selection,
                line_count=self._line_count,
                fix_capture_card=self._default_fix_capture_card,
            ))
            self._args_input.returnPressed.connect(self._apply_args_text)
            args_row.addWidget(self._args_input, 1)
            apply_args_button = QtWidgets.QPushButton('Apply Args')
            apply_args_button.clicked.connect(self._apply_args_text)
            args_row.addWidget(apply_args_button)
            root.addLayout(args_row)

            local_presets_row = QtWidgets.QHBoxLayout()
            local_presets_row.addWidget(QtWidgets.QLabel('Local Preset'))
            self._local_preset_box = QtWidgets.QComboBox()
            self._local_preset_box.setEditable(False)
            self._local_preset_box.setMinimumContentsLength(18)
            self._local_preset_box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            local_presets_row.addWidget(self._local_preset_box, 1)
            load_local_button = QtWidgets.QPushButton('Use Local')
            load_local_button.clicked.connect(self._load_local_preset)
            local_presets_row.addWidget(load_local_button)
            save_local_button = QtWidgets.QPushButton('Save Local')
            save_local_button.clicked.connect(self._save_local_preset)
            local_presets_row.addWidget(save_local_button)
            delete_local_button = QtWidgets.QPushButton('Delete Local')
            delete_local_button.clicked.connect(self._delete_local_preset)
            local_presets_row.addWidget(delete_local_button)
            root.addLayout(local_presets_row)

            if live:
                info_text = 'Live changes apply immediately to new lines.'
            elif self._show_preview:
                info_text = 'Preview updates while you tune. Click Start to continue.'
            else:
                info_text = 'Adjust values and click Start to continue.'
            self._info_label = QtWidgets.QLabel(info_text)
            root.addWidget(self._info_label)

            buttons = QtWidgets.QHBoxLayout()
            root.addLayout(buttons)
            buttons.addStretch(1)

            reset_button = QtWidgets.QPushButton('Reset')
            reset_button.clicked.connect(self._reset_controls)
            buttons.addWidget(reset_button)

            copy_button = QtWidgets.QPushButton('Copy Args')
            copy_button.clicked.connect(self._copy_args)
            buttons.addWidget(copy_button)

            paste_button = QtWidgets.QPushButton('Paste Args')
            paste_button.clicked.connect(self._paste_args)
            buttons.addWidget(paste_button)

            load_preset_button = QtWidgets.QPushButton('Load Preset')
            load_preset_button.clicked.connect(self._load_preset)
            buttons.addWidget(load_preset_button)

            save_preset_button = QtWidgets.QPushButton('Save Preset')
            save_preset_button.clicked.connect(self._save_preset)
            buttons.addWidget(save_preset_button)

            if live:
                close_button = QtWidgets.QPushButton('Close')
                close_button.clicked.connect(self.close)
                buttons.addWidget(close_button)
            else:
                cancel_button = QtWidgets.QPushButton('Cancel')
                cancel_button.clicked.connect(self.reject)
                buttons.addWidget(cancel_button)

                start_button = QtWidgets.QPushButton('Start')
                start_button.clicked.connect(self.accept)
                start_button.setDefault(True)
                buttons.addWidget(start_button)

            self._preview_timer = None
            if self._show_preview:
                self._preview_timer = QtCore.QTimer(self)
                self._preview_timer.setInterval(120)
                self._preview_timer.timeout.connect(self.refresh_preview)
                self._preview_timer.start()
                self.refresh_preview()

            self._refresh_local_presets()
            self._controls_changed()

        @property
        def values(self):
            return (
                self._sliders['brightness'].value(),
                self._sliders['sharpness'].value(),
                self._sliders['gain'].value(),
                self._sliders['contrast'].value(),
                self._coeff_boxes['brightness'].value(),
                self._coeff_boxes['sharpness'].value(),
                self._coeff_boxes['gain'].value(),
                self._coeff_boxes['contrast'].value(),
            )

        @property
        def decoder_tuning_values(self):
            if not self._decoder_tuning_enabled:
                return None
            return {
                'tape_format': self._tape_format_box.currentText(),
                'extra_roll': self._extra_roll_box.value(),
                'line_start_range': (
                    self._line_start_start_box.value(),
                    self._line_start_end_box.value(),
                ),
            }

        @property
        def line_selection_values(self):
            return frozenset(
                line for line, checkbox in self._line_checkboxes.items()
                if checkbox.isChecked()
            )

        @property
        def fix_capture_card_values(self):
            return {
                'enabled': self._fix_capture_card_enabled.isChecked(),
                'seconds': self._fix_capture_card_seconds.value(),
                'interval_minutes': self._fix_capture_card_interval.value(),
                'device': self._default_fix_capture_card['device'],
            }

        def set_change_callback(self, callback):
            self._external_change_callback = callback

        def _enable_all_lines(self):
            self._set_line_selection_values(range(1, self._line_count + 1))

        def _disable_all_lines(self):
            self._set_line_selection_values(())

        def _line_start_range_changed(self):
            start = self._line_start_start_box.value()
            end = self._line_start_end_box.value()
            if start > end:
                sender = self.sender()
                if sender is self._line_start_start_box:
                    self._line_start_end_box.setValue(start)
                else:
                    self._line_start_start_box.setValue(end)
                    return
            self._controls_changed()

        def _controls_changed(self):
            self._set_args_text(format_tuning_args(
                self.values,
                self.decoder_tuning_values,
                self.line_selection_values,
                line_count=self._line_count,
                fix_capture_card=self.fix_capture_card_values,
            ))
            if self._external_change_callback is not None:
                self._external_change_callback(
                    self.values,
                    self.decoder_tuning_values,
                    self.line_selection_values,
                    self.fix_capture_card_values,
                )
            if self._show_preview and self.isVisible():
                self.refresh_preview()

        def _reset_controls(self):
            for slider in self._sliders.values():
                slider.setValue(50)
            self._coeff_boxes['brightness'].setValue(DEFAULT_CONTROLS[4])
            self._coeff_boxes['sharpness'].setValue(DEFAULT_CONTROLS[5])
            self._coeff_boxes['gain'].setValue(DEFAULT_CONTROLS[6])
            self._coeff_boxes['contrast'].setValue(DEFAULT_CONTROLS[7])
            if self._decoder_tuning_enabled and self._default_decoder_tuning is not None:
                tape_format = self._default_decoder_tuning['tape_format']
                if tape_format in self._tape_formats:
                    self._tape_format_box.setCurrentIndex(self._tape_formats.index(tape_format))
                self._extra_roll_box.setValue(int(self._default_decoder_tuning['extra_roll']))
                self._line_start_start_box.setValue(int(self._default_decoder_tuning['line_start_range'][0]))
                self._line_start_end_box.setValue(int(self._default_decoder_tuning['line_start_range'][1]))
            self._set_line_selection_values(self._default_line_selection)
            self._set_fix_capture_card_values(self._default_fix_capture_card)

        def _copy_args(self):
            QtWidgets.QApplication.clipboard().setText(self._args_input.text())

        def _refresh_local_presets(self, selected_name=None):
            current_name = selected_name or self._local_preset_box.currentText()
            try:
                self._local_presets = load_local_presets(self._local_preset_store_path)
            except (OSError, ValueError) as exc:
                self._local_presets = {}
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
            self._local_preset_box.blockSignals(True)
            self._local_preset_box.clear()
            self._local_preset_box.addItem('')
            for name in self._local_presets:
                self._local_preset_box.addItem(name)
            if current_name and current_name in self._local_presets:
                self._local_preset_box.setCurrentText(current_name)
            else:
                self._local_preset_box.setCurrentIndex(0)
            self._local_preset_box.blockSignals(False)

        def _selected_local_preset_name(self):
            name = self._local_preset_box.currentText().strip()
            if not name:
                raise ValueError('Select a local preset first.')
            return name

        def _load_local_preset(self):
            try:
                name = self._selected_local_preset_name()
                text = self._local_presets[name]
            except (KeyError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._args_input.setText(text)
            self._apply_args_text()

        def _save_local_preset(self):
            suggested = self._local_preset_box.currentText().strip()
            name, ok = QtWidgets.QInputDialog.getText(
                self,
                'Save Local Preset',
                'Preset name',
                text=suggested,
            )
            if not ok:
                return
            try:
                saved_name = save_local_preset(name, self._args_input.text(), path=self._local_preset_store_path)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._refresh_local_presets(selected_name=saved_name)

        def _delete_local_preset(self):
            try:
                name = self._selected_local_preset_name()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            answer = QtWidgets.QMessageBox.question(
                self,
                'Delete Local Preset',
                f'Delete local preset "{name}"?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
            try:
                delete_local_preset(name, path=self._local_preset_store_path)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._refresh_local_presets()

        def _default_preset_path(self):
            if self._last_preset_path:
                return self._last_preset_path
            return os.path.join(os.getcwd(), 'vbi-tune.vtnargs')

        def _load_preset(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                'Load VBI Tune Preset',
                self._default_preset_path(),
                PRESET_FILE_FILTER,
            )
            if not path:
                return
            try:
                text = load_preset_text(path)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._last_preset_path = path
            self._args_input.setText(text)
            self._apply_args_text()

        def _save_preset(self):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                'Save VBI Tune Preset',
                self._default_preset_path(),
                PRESET_FILE_FILTER,
            )
            if not path:
                return
            if not os.path.splitext(path)[1]:
                path += '.vtnargs'
            try:
                save_preset_text(path, self._args_input.text())
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._last_preset_path = path

        def _paste_args(self):
            self._apply_parsed_tuning(
                *parse_tuning_args(
                    QtWidgets.QApplication.clipboard().text(),
                    defaults=self.values,
                    decoder_defaults=self.decoder_tuning_values,
                    tape_formats=self._tape_formats,
                    line_defaults=self.line_selection_values,
                    line_count=self._line_count,
                    fix_capture_card_defaults=self.fix_capture_card_values,
                )
            )

        def _apply_args_text(self):
            try:
                values, decoder_tuning, line_selection, fix_capture_card = parse_tuning_args(
                    self._args_input.text(),
                    defaults=self.values,
                    decoder_defaults=self.decoder_tuning_values,
                    tape_formats=self._tape_formats,
                    line_defaults=self.line_selection_values,
                    line_count=self._line_count,
                    fix_capture_card_defaults=self.fix_capture_card_values,
                )
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._apply_parsed_tuning(values, decoder_tuning, line_selection, fix_capture_card)

        def _set_args_text(self, text):
            self._updating_args_text = True
            self._args_input.setText(text)
            self._updating_args_text = False

        def _set_control_values(self, values):
            for key, value in zip(('brightness', 'sharpness', 'gain', 'contrast'), values[:4]):
                self._sliders[key].setValue(int(value))
            for key, value in zip(('brightness', 'sharpness', 'gain', 'contrast'), values[4:]):
                self._coeff_boxes[key].setValue(float(value))

        def _set_decoder_tuning_values(self, decoder_tuning):
            if not self._decoder_tuning_enabled or decoder_tuning is None:
                return
            if decoder_tuning['tape_format'] in self._tape_formats:
                self._tape_format_box.setCurrentIndex(self._tape_formats.index(decoder_tuning['tape_format']))
            self._extra_roll_box.setValue(int(decoder_tuning['extra_roll']))
            self._line_start_start_box.setValue(int(decoder_tuning['line_start_range'][0]))
            self._line_start_end_box.setValue(int(decoder_tuning['line_start_range'][1]))

        def _set_line_selection_values(self, line_selection):
            selected = _normalise_line_selection(line_selection, line_count=self._line_count)
            for line, checkbox in self._line_checkboxes.items():
                checkbox.setChecked(line in selected)

        def _set_fix_capture_card_values(self, fix_capture_card):
            settings = normalise_fix_capture_card(fix_capture_card)
            self._fix_capture_card_enabled.setChecked(bool(settings['enabled']))
            self._fix_capture_card_seconds.setValue(int(settings['seconds']))
            self._fix_capture_card_interval.setValue(int(settings['interval_minutes']))

        def _apply_parsed_tuning(self, values, decoder_tuning, line_selection, fix_capture_card):
            self._set_control_values(values)
            self._set_decoder_tuning_values(decoder_tuning)
            self._set_line_selection_values(line_selection)
            self._set_fix_capture_card_values(fix_capture_card)

        def _preview_settings(self):
            preview_config = self._config
            preview_tape_format = self._tape_format
            if self._decoder_tuning_enabled:
                decoder_tuning = self.decoder_tuning_values
                preview_config = self._config.retuned(
                    extra_roll=decoder_tuning['extra_roll'],
                    line_start_range=decoder_tuning['line_start_range'],
                )
                preview_tape_format = decoder_tuning['tape_format']
            return preview_config, preview_tape_format

        def _render_preview_image(self, preview_lines):
            if not preview_lines:
                return None

            preview_config, preview_tape_format = self._preview_settings()
            Line.configure(
                preview_config,
                force_cpu=True,
                tape_format=preview_tape_format,
                brightness=self.values[0],
                sharpness=self.values[1],
                gain=self.values[2],
                contrast=self.values[3],
                brightness_coeff=self.values[4],
                sharpness_coeff=self.values[5],
                gain_coeff=self.values[6],
                contrast_coeff=self.values[7],
            )
            width = preview_config.resample_size
            line_height = 6
            image = np.zeros((len(preview_lines) * line_height, width, 3), dtype=np.uint8)

            for row, (number, raw_bytes) in enumerate(preview_lines):
                line = Line(raw_bytes, number)
                base = np.clip(line.resampled[:width], 0, 255).astype(np.uint8)
                if line.is_teletext:
                    rgb = np.stack((base // 3, base, (base * 3) // 4), axis=1)
                else:
                    rgb = np.stack((base, base // 3, base // 3), axis=1)
                start = row * line_height
                image[start:start + line_height, :, :] = rgb[np.newaxis, :, :]

            return QtGui.QImage(
                image.data,
                image.shape[1],
                image.shape[0],
                image.strides[0],
                QtGui.QImage.Format_RGB888,
            ).copy()

        def refresh_preview(self):
            if self._preview_provider is None:
                return
            preview_lines = self._preview_provider()
            if not preview_lines:
                return
            image = self._render_preview_image(preview_lines)
            if image is None:
                return
            self._last_image = image
            if self._preview_label is not None:
                self._preview_label.setPixmap(_scaled_pixmap(image, self._preview_label.size()))

        def resizeEvent(self, event):  # pragma: no cover - GUI path
            super().resizeEvent(event)
            if self._last_image is not None and self._preview_label is not None:
                self._preview_label.setPixmap(_scaled_pixmap(self._last_image, self._preview_label.size()))


def run_tuning_dialog(
    title,
    controls=DEFAULT_CONTROLS,
    preview_provider=None,
    config=None,
    tape_format='vhs',
    live=False,
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=DEFAULT_LINE_COUNT,
    fix_capture_card=None,
):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()
    dialog = VBITuningDialog(
        title,
        controls=controls,
        preview_provider=preview_provider,
        config=config,
        tape_format=tape_format,
        live=live,
        decoder_tuning=decoder_tuning,
        tape_formats=tape_formats,
        line_selection=line_selection,
        line_count=line_count,
        fix_capture_card=fix_capture_card,
    )
    if live:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec_()
        return dialog.values, dialog.decoder_tuning_values, dialog.line_selection_values, dialog.fix_capture_card_values
    if dialog.exec_() == QtWidgets.QDialog.Accepted:
        return dialog.values, dialog.decoder_tuning_values, dialog.line_selection_values, dialog.fix_capture_card_values
    return None


def _live_tuner_entry(shared_values, title, tape_formats, line_count):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()

    decoder_tuning = {
        'tape_format': tape_formats[int(shared_values[8])],
        'extra_roll': int(shared_values[9]),
        'line_start_range': (int(shared_values[10]), int(shared_values[11])),
    } if tape_formats else None
    line_selection = frozenset(
        line for line in range(1, line_count + 1)
        if int(shared_values[11 + line]) != 0
    )
    fix_offset = 12 + line_count
    fix_capture_card = {
        'enabled': bool(int(shared_values[fix_offset])),
        'seconds': int(shared_values[fix_offset + 1]),
        'interval_minutes': int(shared_values[fix_offset + 2]),
        'device': DEFAULT_FIX_CAPTURE_CARD['device'],
    }

    def update_values(values, next_decoder_tuning, next_line_selection, next_fix_capture_card):
        for index, value in enumerate(values):
            shared_values[index] = float(value)
        if tape_formats and next_decoder_tuning is not None:
            shared_values[8] = float(tape_formats.index(next_decoder_tuning['tape_format']))
            shared_values[9] = float(next_decoder_tuning['extra_roll'])
            shared_values[10] = float(next_decoder_tuning['line_start_range'][0])
            shared_values[11] = float(next_decoder_tuning['line_start_range'][1])
        for line in range(1, line_count + 1):
            shared_values[11 + line] = 1.0 if line in next_line_selection else 0.0
        fix_settings = normalise_fix_capture_card(next_fix_capture_card)
        shared_values[fix_offset] = 1.0 if fix_settings['enabled'] else 0.0
        shared_values[fix_offset + 1] = float(fix_settings['seconds'])
        shared_values[fix_offset + 2] = float(fix_settings['interval_minutes'])

    dialog = VBITuningDialog(
        title,
        controls=tuple(float(value) for value in shared_values[:8]),
        live=True,
        decoder_tuning=decoder_tuning,
        tape_formats=tape_formats,
        line_selection=line_selection,
        line_count=line_count,
        fix_capture_card=fix_capture_card,
    )
    dialog.set_change_callback(update_values)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    dialog.exec_()


class LiveTunerHandle:
    def __init__(self, process, shared_values, tape_formats=None, line_count=DEFAULT_LINE_COUNT):
        self._process = process
        self._shared_values = shared_values
        self._tape_formats = list(tape_formats or [])
        self._line_count = int(line_count)

    def values(self):
        return (
            int(self._shared_values[0]),
            int(self._shared_values[1]),
            int(self._shared_values[2]),
            int(self._shared_values[3]),
            float(self._shared_values[4]),
            float(self._shared_values[5]),
            float(self._shared_values[6]),
            float(self._shared_values[7]),
        )

    def decoder_tuning(self):
        if not self._tape_formats:
            return None
        format_index = min(max(int(self._shared_values[8]), 0), len(self._tape_formats) - 1)
        return {
            'tape_format': self._tape_formats[format_index],
            'extra_roll': int(self._shared_values[9]),
            'line_start_range': (
                int(self._shared_values[10]),
                int(self._shared_values[11]),
            ),
        }

    def line_selection(self):
        return frozenset(
            line for line in range(1, self._line_count + 1)
            if int(self._shared_values[11 + line]) != 0
        )

    def fix_capture_card(self):
        fix_offset = 12 + self._line_count
        return {
            'enabled': bool(int(self._shared_values[fix_offset])),
            'seconds': int(self._shared_values[fix_offset + 1]),
            'interval_minutes': int(self._shared_values[fix_offset + 2]),
            'device': DEFAULT_FIX_CAPTURE_CARD['device'],
        }

    def is_alive(self):
        return self._process.is_alive()

    def apply(self, values=None, decoder_tuning=None, line_selection=None, fix_capture_card=None):
        if values is not None:
            for index, value in enumerate(values):
                self._shared_values[index] = float(value)
        if self._tape_formats and decoder_tuning is not None:
            tape_format = decoder_tuning['tape_format']
            if tape_format in self._tape_formats:
                self._shared_values[8] = float(self._tape_formats.index(tape_format))
            self._shared_values[9] = float(decoder_tuning['extra_roll'])
            self._shared_values[10] = float(decoder_tuning['line_start_range'][0])
            self._shared_values[11] = float(decoder_tuning['line_start_range'][1])
        if line_selection is not None:
            selected = _normalise_line_selection(line_selection, line_count=self._line_count)
            for line in range(1, self._line_count + 1):
                self._shared_values[11 + line] = 1.0 if line in selected else 0.0
        if fix_capture_card is not None:
            settings = normalise_fix_capture_card(fix_capture_card)
            fix_offset = 12 + self._line_count
            self._shared_values[fix_offset] = 1.0 if settings['enabled'] else 0.0
            self._shared_values[fix_offset + 1] = float(settings['seconds'])
            self._shared_values[fix_offset + 2] = float(settings['interval_minutes'])

    def close(self):
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1)


def launch_live_tuner(
    title,
    controls=DEFAULT_CONTROLS,
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=DEFAULT_LINE_COUNT,
    fix_capture_card=None,
):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    ctx = mp.get_context('spawn')
    tape_formats = list(tape_formats or [])
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
    line_selection = _normalise_line_selection(line_selection, line_count=line_count)
    fix_capture_card = normalise_fix_capture_card(fix_capture_card)
    shared_seed = [float(value) for value in controls]
    if tape_formats and decoder_tuning is not None:
        shared_seed.extend((
            float(tape_formats.index(decoder_tuning['tape_format']) if decoder_tuning['tape_format'] in tape_formats else 0),
            float(decoder_tuning['extra_roll']),
            float(decoder_tuning['line_start_range'][0]),
            float(decoder_tuning['line_start_range'][1]),
        ))
    else:
        shared_seed.extend((0.0, 0.0, 0.0, 0.0))
    shared_seed.extend(1.0 if line in line_selection else 0.0 for line in range(1, line_count + 1))
    shared_seed.extend((
        1.0 if fix_capture_card['enabled'] else 0.0,
        float(fix_capture_card['seconds']),
        float(fix_capture_card['interval_minutes']),
    ))
    shared_values = ctx.Array('d', shared_seed, lock=False)
    process = ctx.Process(target=_live_tuner_entry, args=(shared_values, title, tape_formats, line_count))
    process.daemon = True
    process.start()
    return LiveTunerHandle(process, shared_values, tape_formats=tape_formats, line_count=line_count)
