import json
import multiprocessing as mp
import os
import shlex
import sys
import time

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
    _ANALYSIS_THREAD_HOLD = {}


if IMPORT_ERROR is None:
    from teletext.vbi.line import (
        ADAPTIVE_THRESHOLD_DEFAULT,
        ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
        AUTO_LINE_ALIGN_DEFAULT,
        AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
        AUTO_GAIN_CONTRAST_DEFAULT,
        AUTO_BLACK_LEVEL_DEFAULT,
        AUTO_BLACK_LEVEL_COEFF_DEFAULT,
        BRIGHTNESS_COEFF_DEFAULT,
        CLOCK_LOCK_DEFAULT,
        CLOCK_LOCK_COEFF_DEFAULT,
        CONTRAST_COEFF_DEFAULT,
        DROPOUT_REPAIR_DEFAULT,
        DROPOUT_REPAIR_COEFF_DEFAULT,
        GAIN_COEFF_DEFAULT,
        HEAD_SWITCHING_MASK_COEFF_DEFAULT,
        HEAD_SWITCHING_MASK_DEFAULT,
        HUM_REMOVAL_COEFF_DEFAULT,
        IMPULSE_FILTER_COEFF_DEFAULT,
        IMPULSE_FILTER_DEFAULT,
        LINE_STABILIZATION_COEFF_DEFAULT,
        LINE_STABILIZATION_DEFAULT,
        Line,
        HUM_REMOVAL_DEFAULT,
        NOISE_REDUCTION_DEFAULT,
        NOISE_REDUCTION_COEFF_DEFAULT,
        normalise_per_line_shift_map,
        serialise_per_line_shift_map,
        eye_pattern_clock_stats,
        histogram_black_level_stats,
        QUALITY_THRESHOLD_DEFAULT,
        QUALITY_THRESHOLD_COEFF_DEFAULT,
        quality_meter_stats,
        START_LOCK_DEFAULT,
        START_LOCK_COEFF_DEFAULT,
        SHARPNESS_COEFF_DEFAULT,
        TEMPORAL_DENOISE_COEFF_DEFAULT,
        TEMPORAL_DENOISE_DEFAULT,
        WOW_FLUTTER_COMPENSATION_DEFAULT,
        WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
    )
else:
    BRIGHTNESS_COEFF_DEFAULT = 48.0
    CLOCK_LOCK_DEFAULT = 50
    CLOCK_LOCK_COEFF_DEFAULT = 1.0
    START_LOCK_DEFAULT = 50
    START_LOCK_COEFF_DEFAULT = 1.0
    AUTO_LINE_ALIGN_DEFAULT = 0
    SHARPNESS_COEFF_DEFAULT = 3.0
    WOW_FLUTTER_COMPENSATION_DEFAULT = 0
    WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT = 1.0
    GAIN_COEFF_DEFAULT = 0.5
    CONTRAST_COEFF_DEFAULT = 0.5
    ADAPTIVE_THRESHOLD_DEFAULT = 0
    ADAPTIVE_THRESHOLD_COEFF_DEFAULT = 1.0
    DROPOUT_REPAIR_DEFAULT = 0
    DROPOUT_REPAIR_COEFF_DEFAULT = 1.0
    IMPULSE_FILTER_DEFAULT = 0
    TEMPORAL_DENOISE_DEFAULT = 0
    NOISE_REDUCTION_DEFAULT = 0
    HUM_REMOVAL_DEFAULT = 0
    AUTO_BLACK_LEVEL_DEFAULT = 0
    HEAD_SWITCHING_MASK_DEFAULT = 0
    LINE_STABILIZATION_DEFAULT = 0
    AUTO_GAIN_CONTRAST_DEFAULT = 0
    QUALITY_THRESHOLD_DEFAULT = 50
    IMPULSE_FILTER_COEFF_DEFAULT = 1.0
    TEMPORAL_DENOISE_COEFF_DEFAULT = 1.0
    NOISE_REDUCTION_COEFF_DEFAULT = 1.0
    HUM_REMOVAL_COEFF_DEFAULT = 1.0
    AUTO_BLACK_LEVEL_COEFF_DEFAULT = 1.0
    HEAD_SWITCHING_MASK_COEFF_DEFAULT = 1.0
    LINE_STABILIZATION_COEFF_DEFAULT = 1.0
    AUTO_GAIN_CONTRAST_COEFF_DEFAULT = 1.0
    QUALITY_THRESHOLD_COEFF_DEFAULT = 1.0

    def normalise_per_line_shift_map(per_line_shift, maximum_line=32):
        if not per_line_shift:
            return {}
        if isinstance(per_line_shift, dict):
            items = per_line_shift.items()
        else:
            items = per_line_shift
        maximum_line = max(int(maximum_line), 1)
        normalised = {}
        for raw_line, raw_shift in items:
            try:
                line = int(raw_line)
                shift = float(raw_shift)
            except (TypeError, ValueError):
                continue
            if 1 <= line <= maximum_line and abs(shift) > 1e-9:
                normalised[line] = shift
        return normalised

    def serialise_per_line_shift_map(per_line_shift, maximum_line=32):
        return tuple(sorted(normalise_per_line_shift_map(per_line_shift, maximum_line=maximum_line).items()))


DEFAULT_CONTROLS = (
    50,
    50,
    50,
    50,
    BRIGHTNESS_COEFF_DEFAULT if IMPORT_ERROR is None else 48.0,
    SHARPNESS_COEFF_DEFAULT if IMPORT_ERROR is None else 3.0,
    GAIN_COEFF_DEFAULT if IMPORT_ERROR is None else 0.5,
    CONTRAST_COEFF_DEFAULT if IMPORT_ERROR is None else 0.5,
    IMPULSE_FILTER_DEFAULT if IMPORT_ERROR is None else 0,
    TEMPORAL_DENOISE_DEFAULT if IMPORT_ERROR is None else 0,
    NOISE_REDUCTION_DEFAULT if IMPORT_ERROR is None else 0,
    HUM_REMOVAL_DEFAULT if IMPORT_ERROR is None else 0,
    AUTO_BLACK_LEVEL_DEFAULT if IMPORT_ERROR is None else 0,
    HEAD_SWITCHING_MASK_DEFAULT if IMPORT_ERROR is None else 0,
    LINE_STABILIZATION_DEFAULT if IMPORT_ERROR is None else 0,
    AUTO_GAIN_CONTRAST_DEFAULT if IMPORT_ERROR is None else 0,
    IMPULSE_FILTER_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    TEMPORAL_DENOISE_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    NOISE_REDUCTION_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    HUM_REMOVAL_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    AUTO_BLACK_LEVEL_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    HEAD_SWITCHING_MASK_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    LINE_STABILIZATION_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
    AUTO_GAIN_CONTRAST_COEFF_DEFAULT if IMPORT_ERROR is None else 1.0,
)
DEFAULT_LINE_COUNT = 32
PRESET_FILE_FILTER = 'VBI Tune Presets (*.vtnargs *.txt);;All Files (*)'
LOCAL_PRESET_FILE = 'vbi-tune-presets.json'
ANALYSIS_HISTORY_FILE = 'vbi-analysis-history.json'
DEFAULT_ANALYSIS_QUICK_FRAMES = 500
DEFAULT_ANALYSIS_EVALUATION_LINES = 960
ANALYSIS_MODE_QUICK = 'quick'
ANALYSIS_MODE_FULL = 'full'
ANALYSIS_KIND_AUTO_TUNE = 'auto_tune'
ANALYSIS_KIND_CLOCK_START = 'clock_start'
ANALYSIS_EVALUATION_LINE_LIMITS = {
    ANALYSIS_KIND_AUTO_TUNE: 256,
    ANALYSIS_KIND_CLOCK_START: 384,
}
ANALYSIS_KIND_TITLES = {
    ANALYSIS_KIND_AUTO_TUNE: 'Auto Tune',
    ANALYSIS_KIND_CLOCK_START: 'Clock / Start Auto-Lock',
}
SIGNAL_CONTROL_COUNT = len(DEFAULT_CONTROLS)
PER_LINE_SHIFT_SLOT_COUNT = DEFAULT_LINE_COUNT
DECODER_TUNING_BASE_SLOT_COUNT = 25
DECODER_TUNING_SLOT_COUNT = DECODER_TUNING_BASE_SLOT_COUNT + PER_LINE_SHIFT_SLOT_COUNT
LINE_OVERRIDE_SIGNAL_SLOT_COUNT = DEFAULT_LINE_COUNT * SIGNAL_CONTROL_COUNT
PER_LINE_DECODER_OVERRIDE_FIELDS = (
    'extra_roll',
    'line_start_range_start',
    'line_start_range_end',
    'quality_threshold',
    'quality_threshold_coeff',
    'clock_lock',
    'clock_lock_coeff',
    'start_lock',
    'start_lock_coeff',
    'adaptive_threshold',
    'adaptive_threshold_coeff',
    'dropout_repair',
    'dropout_repair_coeff',
    'wow_flutter_compensation',
    'wow_flutter_compensation_coeff',
    'auto_line_align',
)
PER_LINE_DECODER_OVERRIDE_SLOT_COUNT = len(PER_LINE_DECODER_OVERRIDE_FIELDS)
LINE_OVERRIDE_DECODER_SLOT_COUNT = DEFAULT_LINE_COUNT * PER_LINE_DECODER_OVERRIDE_SLOT_COUNT
EDIT_TARGET_ALL = 'all'
EDIT_TARGET_SINGLE = 'single'


def _line_selection_offset():
    return SIGNAL_CONTROL_COUNT + DECODER_TUNING_SLOT_COUNT


def _per_line_shift_offset(line):
    return SIGNAL_CONTROL_COUNT + DECODER_TUNING_BASE_SLOT_COUNT + (int(line) - 1)


def _fix_capture_card_offset(line_count):
    return _line_selection_offset() + int(line_count)


def _line_control_override_offset(line_count):
    return _fix_capture_card_offset(line_count) + 3


def _line_decoder_override_offset(line_count):
    return _line_control_override_offset(line_count) + LINE_OVERRIDE_SIGNAL_SLOT_COUNT


CONTROL_INDEX = {
    'brightness': 0,
    'sharpness': 1,
    'gain': 2,
    'contrast': 3,
    'brightness_coeff': 4,
    'sharpness_coeff': 5,
    'gain_coeff': 6,
    'contrast_coeff': 7,
    'impulse_filter': 8,
    'temporal_denoise': 9,
    'noise_reduction': 10,
    'hum_removal': 11,
    'auto_black_level': 12,
    'head_switching_mask': 13,
    'line_stabilization': 14,
    'auto_gain_contrast': 15,
    'impulse_filter_coeff': 16,
    'temporal_denoise_coeff': 17,
    'noise_reduction_coeff': 18,
    'hum_removal_coeff': 19,
    'auto_black_level_coeff': 20,
    'head_switching_mask_coeff': 21,
    'line_stabilization_coeff': 22,
    'auto_gain_contrast_coeff': 23,
}
FUNCTION_MENU_SECTIONS = (
    'Signal Controls',
    'Signal Cleanup',
    'Decoder Tuning',
    'Diagnostics',
)
SECTION_DISPLAY_METADATA = {
    'Signal Controls': {
        'title': 'Signal Controls (VBI)',
        'tooltip': 'Raw VBI signal controls. These affect the captured signal itself.',
    },
    'Signal Cleanup': {
        'title': 'Signal Cleanup (VBI)',
        'tooltip': 'Raw VBI cleanup filters applied before decoding.',
    },
    'Decoder Tuning': {
        'title': 'Decoder Tuning (Deconvolve)',
        'tooltip': 'Decoder-only settings used when turning VBI into teletext packets and pages.',
    },
    'Diagnostics': {
        'title': 'Diagnostics (Viewer)',
        'tooltip': 'Preview and debug overlays for viewer and repair tools.',
    },
    'Line Selection': {
        'title': 'Line Selection (VBI)',
        'tooltip': 'Select which raw VBI lines are used or ignored.',
    },
    'Fix Capture Card': {
        'title': 'Fix Capture Card (Record)',
        'tooltip': 'Capture-card keepalive helper for recording workflows.',
    },
    'Record Timer': {
        'title': 'Record Timer (Record)',
        'tooltip': 'Recording-only timer controls.',
    },
    'Tools': {
        'title': 'Tools (Analysis)',
        'tooltip': 'Analysis helpers that inspect a VBI file and suggest settings.',
    },
    'Args / Preset': {
        'title': 'Args / Preset (Shared)',
        'tooltip': 'Shared command-line arguments and preset storage.',
    },
}
FUNCTION_METADATA = {
    'brightness': {'title': 'Brightness', 'section': 'Signal Controls'},
    'sharpness': {'title': 'Sharpness', 'section': 'Signal Controls'},
    'gain': {'title': 'Gain', 'section': 'Signal Controls'},
    'contrast': {'title': 'Contrast', 'section': 'Signal Controls'},
    'impulse_filter': {'title': 'Impulse Filter', 'section': 'Signal Cleanup'},
    'temporal_denoise': {'title': 'Temporal Denoise', 'section': 'Signal Cleanup'},
    'noise_reduction': {'title': 'Noise Reduction', 'section': 'Signal Cleanup'},
    'hum_removal': {'title': 'Hum Removal', 'section': 'Signal Cleanup'},
    'auto_black_level': {'title': 'Auto Black Level', 'section': 'Signal Cleanup'},
    'head_switching_mask': {'title': 'Head Switching Mask', 'section': 'Signal Cleanup'},
    'line_stabilization': {'title': 'Line-to-Line Stabilization', 'section': 'Signal Cleanup'},
    'auto_gain_contrast': {'title': 'Auto Gain / Contrast', 'section': 'Signal Cleanup'},
    'tape_format': {'title': 'Template', 'section': 'Decoder Tuning'},
    'extra_roll': {'title': 'Extra Roll', 'section': 'Decoder Tuning'},
    'line_start_range': {'title': 'Line Start Range', 'section': 'Decoder Tuning'},
    'quality_threshold': {'title': 'Line Quality', 'section': 'Decoder Tuning'},
    'clock_lock': {'title': 'Clock Lock', 'section': 'Decoder Tuning'},
    'start_lock': {'title': 'Start Lock', 'section': 'Decoder Tuning'},
    'adaptive_threshold': {'title': 'Adaptive Threshold', 'section': 'Decoder Tuning'},
    'dropout_repair': {'title': 'Dropout Repair', 'section': 'Decoder Tuning'},
    'wow_flutter_compensation': {'title': 'Wow/Flutter Compensation', 'section': 'Decoder Tuning'},
    'auto_line_align': {'title': 'Auto Line Align', 'section': 'Decoder Tuning'},
    'per_line_shift': {'title': 'Per-Line Shift', 'section': 'Decoder Tuning'},
    'show_quality': {'title': 'Show Quality', 'section': 'Diagnostics'},
    'show_rejects': {'title': 'Show Rejects', 'section': 'Diagnostics'},
    'show_start_clock': {'title': 'Show Start/Clock', 'section': 'Diagnostics'},
    'show_clock_visuals': {'title': 'Show Clock Visuals', 'section': 'Diagnostics'},
    'show_alignment_visuals': {'title': 'Show Alignment Visuals', 'section': 'Diagnostics'},
    'show_quality_meter': {'title': 'Quality Meter', 'section': 'Diagnostics'},
    'show_histogram_graph': {'title': 'Histogram / Black Level Graph', 'section': 'Diagnostics'},
    'show_eye_pattern': {'title': 'Eye Pattern / Clock Preview', 'section': 'Diagnostics'},
}
CONTROL_KEYS = tuple(key for key, _ in sorted(CONTROL_INDEX.items(), key=lambda item: item[1]))
PER_LINE_DECODER_OVERRIDE_KEYS = (
    'extra_roll',
    'line_start_range',
    'quality_threshold',
    'quality_threshold_coeff',
    'clock_lock',
    'clock_lock_coeff',
    'start_lock',
    'start_lock_coeff',
    'adaptive_threshold',
    'adaptive_threshold_coeff',
    'dropout_repair',
    'dropout_repair_coeff',
    'wow_flutter_compensation',
    'wow_flutter_compensation_coeff',
    'auto_line_align',
)
GLOBAL_DECODER_ONLY_KEYS = (
    'tape_format',
    'per_line_shift',
    'show_quality',
    'show_rejects',
    'show_start_clock',
    'show_clock_visuals',
    'show_alignment_visuals',
    'show_quality_meter',
    'show_histogram_graph',
    'show_eye_pattern',
)
PRESET_FILE_VERSION = 2
ARGS_APPLY_SCOPE_ALL = 'all_lines'
ARGS_APPLY_SCOPE_SELECTED = 'selected_line'


def normalise_disabled_functions(disabled_functions):
    if disabled_functions is None:
        return ()
    disabled = {str(value).strip() for value in disabled_functions}
    return tuple(key for key in FUNCTION_METADATA if key in disabled)


def _section_display_title(title):
    return SECTION_DISPLAY_METADATA.get(title, {}).get('title', title)


def _section_tooltip(title):
    return SECTION_DISPLAY_METADATA.get(title, {}).get('tooltip', '')


def _args_text_mentions_line_selection(text):
    try:
        tokens = shlex.split(str(text or ''))
    except ValueError:
        return False
    return any(token in ('-il', '--ignore-line', '-ul', '--used-line') for token in tokens)


def _resolve_args_line_selection(parsed_line_selection, text, scope, selected_line, line_count=DEFAULT_LINE_COUNT):
    scope = str(scope or ARGS_APPLY_SCOPE_ALL)
    if scope == ARGS_APPLY_SCOPE_SELECTED:
        try:
            line = int(selected_line)
        except (TypeError, ValueError):
            line = 1
        line = min(max(line, 1), max(int(line_count), 1))
        return frozenset((line,))
    if _args_text_mentions_line_selection(text):
        return _normalise_line_selection(parsed_line_selection, line_count=line_count)
    return frozenset(range(1, max(int(line_count), 1) + 1))


def _normalise_edit_target_line(value, line_count=DEFAULT_LINE_COUNT):
    if value in (None, '', False):
        return None
    try:
        line = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= line <= max(int(line_count), 1):
        return line
    return None


def _control_values_to_dict(values):
    values = _normalise_signal_controls_tuple(values)
    return {
        key: values[index]
        for key, index in sorted(CONTROL_INDEX.items(), key=lambda item: item[1])
    }


def _control_dict_to_values(mapping, defaults=DEFAULT_CONTROLS):
    values = list(_normalise_signal_controls_tuple(defaults))
    if mapping is None:
        return tuple(values)
    for key, index in CONTROL_INDEX.items():
        if key not in mapping:
            continue
        if key.endswith('_coeff'):
            values[index] = float(mapping[key])
        else:
            values[index] = int(mapping[key])
    return tuple(values)


def normalise_line_control_overrides(overrides, line_count=DEFAULT_LINE_COUNT):
    if not overrides:
        return {}
    if isinstance(overrides, dict):
        items = overrides.items()
    else:
        items = overrides
    line_count = max(int(line_count), 1)
    cleaned = {}
    for raw_line, raw_values in items:
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            continue
        if line < 1 or line > line_count:
            continue
        try:
            values = _normalise_signal_controls_tuple(raw_values)
        except (TypeError, ValueError):
            continue
        cleaned[line] = tuple(values)
    return dict(sorted(cleaned.items()))


def _normalise_line_decoder_override_entry(override):
    if not isinstance(override, dict):
        return {}
    cleaned = {}
    for key in PER_LINE_DECODER_OVERRIDE_KEYS:
        if key not in override:
            continue
        value = override[key]
        if key == 'line_start_range':
            try:
                cleaned[key] = (int(value[0]), int(value[1]))
            except (TypeError, ValueError, IndexError):
                continue
        elif key.endswith('_coeff'):
            try:
                cleaned[key] = float(value)
            except (TypeError, ValueError):
                continue
        else:
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                continue
    return cleaned


def normalise_line_decoder_overrides(overrides, line_count=DEFAULT_LINE_COUNT):
    if not overrides:
        return {}
    if isinstance(overrides, dict):
        items = overrides.items()
    else:
        items = overrides
    line_count = max(int(line_count), 1)
    cleaned = {}
    for raw_line, raw_override in items:
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            continue
        if line < 1 or line > line_count:
            continue
        entry = _normalise_line_decoder_override_entry(raw_override)
        if entry:
            cleaned[line] = entry
    return dict(sorted(cleaned.items()))


def _effective_control_values_for_line(base_values, line_control_overrides, line):
    values = _normalise_signal_controls_tuple(base_values)
    if line is None:
        return values
    override = normalise_line_control_overrides(line_control_overrides).get(int(line))
    return tuple(override) if override is not None else values


def _effective_decoder_tuning_for_line(base_decoder_tuning, line_decoder_overrides, line):
    decoder_tuning = _normalise_decoder_tuning(base_decoder_tuning)
    if decoder_tuning is None:
        return None
    if line is None:
        return decoder_tuning
    override = normalise_line_decoder_overrides(line_decoder_overrides).get(int(line), {})
    if not override:
        return decoder_tuning
    merged = dict(decoder_tuning)
    merged.update(override)
    return _normalise_decoder_tuning(merged)


def _serialise_line_control_override_slots(overrides, line_count=DEFAULT_LINE_COUNT):
    overrides = normalise_line_control_overrides(overrides, line_count=line_count)
    slots = [float('nan')] * LINE_OVERRIDE_SIGNAL_SLOT_COUNT
    for line, values in overrides.items():
        base = (int(line) - 1) * SIGNAL_CONTROL_COUNT
        for index, value in enumerate(values):
            slots[base + index] = float(value)
    return slots


def _deserialise_line_control_override_slots(values, line_count=DEFAULT_LINE_COUNT):
    line_count = min(max(int(line_count), 1), DEFAULT_LINE_COUNT)
    cleaned = {}
    for line in range(1, line_count + 1):
        base = (line - 1) * SIGNAL_CONTROL_COUNT
        chunk = values[base:base + SIGNAL_CONTROL_COUNT]
        if len(chunk) < SIGNAL_CONTROL_COUNT or all(np.isnan(value) for value in chunk):
            continue
        cleaned[line] = _control_dict_to_values(
            {
                key: chunk[index]
                for key, index in sorted(CONTROL_INDEX.items(), key=lambda item: item[1])
            }
        )
    return cleaned


def _serialise_line_decoder_override_slots(overrides, line_count=DEFAULT_LINE_COUNT):
    overrides = normalise_line_decoder_overrides(overrides, line_count=line_count)
    slots = [float('nan')] * LINE_OVERRIDE_DECODER_SLOT_COUNT
    for line, override in overrides.items():
        base = (int(line) - 1) * PER_LINE_DECODER_OVERRIDE_SLOT_COUNT
        for index, key in enumerate(PER_LINE_DECODER_OVERRIDE_FIELDS):
            if key not in (
                'line_start_range_start',
                'line_start_range_end',
            ):
                source_key = key
                if source_key in override:
                    slots[base + index] = float(override[source_key])
                continue
            line_start_range = override.get('line_start_range')
            if line_start_range is None:
                continue
            if key.endswith('_start'):
                slots[base + index] = float(line_start_range[0])
            else:
                slots[base + index] = float(line_start_range[1])
    return slots


def _deserialise_line_decoder_override_slots(values, line_count=DEFAULT_LINE_COUNT):
    line_count = min(max(int(line_count), 1), DEFAULT_LINE_COUNT)
    cleaned = {}
    for line in range(1, line_count + 1):
        base = (line - 1) * PER_LINE_DECODER_OVERRIDE_SLOT_COUNT
        chunk = values[base:base + PER_LINE_DECODER_OVERRIDE_SLOT_COUNT]
        if len(chunk) < PER_LINE_DECODER_OVERRIDE_SLOT_COUNT or all(np.isnan(value) for value in chunk):
            continue
        entry = {}
        range_start = chunk[PER_LINE_DECODER_OVERRIDE_FIELDS.index('line_start_range_start')]
        range_end = chunk[PER_LINE_DECODER_OVERRIDE_FIELDS.index('line_start_range_end')]
        if not np.isnan(range_start) and not np.isnan(range_end):
            entry['line_start_range'] = (int(range_start), int(range_end))
        for index, key in enumerate(PER_LINE_DECODER_OVERRIDE_FIELDS):
            if key in ('line_start_range_start', 'line_start_range_end'):
                continue
            value = chunk[index]
            if np.isnan(value):
                continue
            if key.endswith('_coeff'):
                entry[key] = float(value)
            else:
                entry[key] = int(value)
        entry = _normalise_line_decoder_override_entry(entry)
        if entry:
            cleaned[line] = entry
    return cleaned


def _normalise_preset_payload(payload):
    if isinstance(payload, dict):
        text = str(payload.get('args', '')).strip()
        disabled_functions = normalise_disabled_functions(payload.get('disabled_functions', ()))
        line_control_overrides = normalise_line_control_overrides(payload.get('line_control_overrides', {}))
        line_decoder_overrides = normalise_line_decoder_overrides(payload.get('line_decoder_overrides', {}))
        edit_target_line = _normalise_edit_target_line(payload.get('edit_target_line'))
    else:
        text = str(payload).strip()
        disabled_functions = ()
        line_control_overrides = {}
        line_decoder_overrides = {}
        edit_target_line = None
    if not text:
        raise ValueError('Preset is empty.')
    return {
        'args': text,
        'disabled_functions': disabled_functions,
        'line_control_overrides': line_control_overrides,
        'line_decoder_overrides': line_decoder_overrides,
        'edit_target_line': edit_target_line,
    }


def _normalise_signal_controls_tuple(values):
    values = tuple(values)
    if len(values) >= 24:
        return values[:24]
    if len(values) == 18:
        return values + (
            HEAD_SWITCHING_MASK_DEFAULT,
            LINE_STABILIZATION_DEFAULT,
            AUTO_GAIN_CONTRAST_DEFAULT,
            HEAD_SWITCHING_MASK_COEFF_DEFAULT,
            LINE_STABILIZATION_COEFF_DEFAULT,
            AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
        )
    if len(values) == 16:
        values = (
            values[0], values[1], values[2], values[3],
            values[4], values[5], values[6], values[7],
            values[8],
            TEMPORAL_DENOISE_DEFAULT,
            values[9], values[10], values[11],
            values[12],
            TEMPORAL_DENOISE_COEFF_DEFAULT,
            values[13], values[14], values[15],
        )
        return _normalise_signal_controls_tuple(values)
    if len(values) == 14:
        values = (
            values[0], values[1], values[2], values[3],
            values[4], values[5], values[6], values[7],
            IMPULSE_FILTER_DEFAULT,
            TEMPORAL_DENOISE_DEFAULT,
            values[8], values[9], values[10],
            IMPULSE_FILTER_COEFF_DEFAULT,
            TEMPORAL_DENOISE_COEFF_DEFAULT,
            values[11], values[12], values[13],
        )
        return _normalise_signal_controls_tuple(values)
    if len(values) == 11:
        values = (
            values[0], values[1], values[2], values[3],
            values[4], values[5], values[6], values[7],
            IMPULSE_FILTER_DEFAULT,
            TEMPORAL_DENOISE_DEFAULT,
            values[8], values[9], values[10],
            IMPULSE_FILTER_COEFF_DEFAULT,
            TEMPORAL_DENOISE_COEFF_DEFAULT,
            NOISE_REDUCTION_COEFF_DEFAULT,
            HUM_REMOVAL_COEFF_DEFAULT,
            AUTO_BLACK_LEVEL_COEFF_DEFAULT,
        )
        return _normalise_signal_controls_tuple(values)
    if len(values) == 8:
        values = values + (
            IMPULSE_FILTER_DEFAULT,
            TEMPORAL_DENOISE_DEFAULT,
            NOISE_REDUCTION_DEFAULT,
            HUM_REMOVAL_DEFAULT,
            AUTO_BLACK_LEVEL_DEFAULT,
            IMPULSE_FILTER_COEFF_DEFAULT,
            TEMPORAL_DENOISE_COEFF_DEFAULT,
            NOISE_REDUCTION_COEFF_DEFAULT,
            HUM_REMOVAL_COEFF_DEFAULT,
            AUTO_BLACK_LEVEL_COEFF_DEFAULT,
        )
        return _normalise_signal_controls_tuple(values)
    raise ValueError(f'Expected 8, 11, 14, 16, 18, or 24 signal control values, got {len(values)}.')


def _normalise_decoder_tuning(decoder_tuning):
    if decoder_tuning is None:
        return None
    return {
        'tape_format': decoder_tuning.get('tape_format', 'vhs'),
        'extra_roll': int(decoder_tuning.get('extra_roll', 0)),
        'line_start_range': tuple(decoder_tuning.get('line_start_range', (0, 0))),
        'quality_threshold': int(decoder_tuning.get('quality_threshold', QUALITY_THRESHOLD_DEFAULT)),
        'quality_threshold_coeff': float(decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)),
        'clock_lock': int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)),
        'clock_lock_coeff': float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)),
        'start_lock': int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT)),
        'start_lock_coeff': float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)),
        'adaptive_threshold': int(decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT)),
        'adaptive_threshold_coeff': float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)),
        'dropout_repair': int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)),
        'dropout_repair_coeff': float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)),
        'wow_flutter_compensation': int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)),
        'wow_flutter_compensation_coeff': float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)),
        'auto_line_align': int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
        'per_line_shift': normalise_per_line_shift_map(
            decoder_tuning.get('per_line_shift', {}),
            maximum_line=DEFAULT_LINE_COUNT,
        ),
        'show_quality': bool(decoder_tuning.get('show_quality', False)),
        'show_rejects': bool(decoder_tuning.get('show_rejects', False)),
        'show_start_clock': bool(decoder_tuning.get('show_start_clock', False)),
        'show_clock_visuals': bool(decoder_tuning.get('show_clock_visuals', False)),
        'show_alignment_visuals': bool(decoder_tuning.get('show_alignment_visuals', False)),
        'show_quality_meter': bool(decoder_tuning.get('show_quality_meter', False)),
        'show_histogram_graph': bool(decoder_tuning.get('show_histogram_graph', False)),
        'show_eye_pattern': bool(decoder_tuning.get('show_eye_pattern', False)),
        'line_control_overrides': normalise_line_control_overrides(
            decoder_tuning.get('line_control_overrides', {}),
            line_count=DEFAULT_LINE_COUNT,
        ),
        'line_decoder_overrides': normalise_line_decoder_overrides(
            decoder_tuning.get('line_decoder_overrides', {}),
            line_count=DEFAULT_LINE_COUNT,
        ),
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


def _format_value_coeff_arg(flag, value, coeff, default_coeff):
    if float(coeff) == float(default_coeff):
        return f'{flag} {int(value)}'
    return f'{flag} {int(value)}/{float(coeff):g}'


def _float_matches_default(value, default):
    return abs(float(value) - float(default)) < 1e-9


def _append_value_coeff_part(parts, flag, value, coeff, default_value, default_coeff):
    if int(value) == int(default_value) and _float_matches_default(coeff, default_coeff):
        return
    parts.append(_format_value_coeff_arg(flag, value, coeff, default_coeff))


def _default_decoder_tuning_for_formatting(decoder_tuning, defaults=None):
    defaults = _normalise_decoder_tuning(defaults)
    if defaults is not None:
        return defaults
    if decoder_tuning is None:
        return None
    line_start_range = tuple(int(value) for value in decoder_tuning['line_start_range'])
    return {
        'tape_format': 'vhs',
        'extra_roll': 0,
        'line_start_range': line_start_range,
        'quality_threshold': QUALITY_THRESHOLD_DEFAULT,
        'quality_threshold_coeff': QUALITY_THRESHOLD_COEFF_DEFAULT,
        'clock_lock': CLOCK_LOCK_DEFAULT,
        'clock_lock_coeff': CLOCK_LOCK_COEFF_DEFAULT,
        'start_lock': START_LOCK_DEFAULT,
        'start_lock_coeff': START_LOCK_COEFF_DEFAULT,
        'adaptive_threshold': ADAPTIVE_THRESHOLD_DEFAULT,
        'adaptive_threshold_coeff': ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
        'dropout_repair': DROPOUT_REPAIR_DEFAULT,
        'dropout_repair_coeff': DROPOUT_REPAIR_COEFF_DEFAULT,
        'wow_flutter_compensation': WOW_FLUTTER_COMPENSATION_DEFAULT,
        'wow_flutter_compensation_coeff': WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
        'auto_line_align': AUTO_LINE_ALIGN_DEFAULT,
        'per_line_shift': {},
        'show_quality': False,
        'show_rejects': False,
        'show_start_clock': False,
        'show_clock_visuals': False,
        'show_alignment_visuals': False,
        'show_quality_meter': False,
        'show_histogram_graph': False,
        'show_eye_pattern': False,
        'line_control_overrides': {},
        'line_decoder_overrides': {},
    }


def format_decoder_tuning(decoder_tuning, defaults=None):
    if decoder_tuning is None:
        return ''
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
    defaults = _default_decoder_tuning_for_formatting(decoder_tuning, defaults)
    line_start_range = tuple(int(value) for value in decoder_tuning['line_start_range'])
    default_line_start_range = tuple(int(value) for value in defaults['line_start_range'])
    parts = []
    if str(decoder_tuning['tape_format']) != str(defaults['tape_format']):
        parts.append(f"-f {decoder_tuning['tape_format']}")
    if int(decoder_tuning['extra_roll']) != int(defaults['extra_roll']):
        parts.append(f"-er {int(decoder_tuning['extra_roll'])}")
    if line_start_range != default_line_start_range:
        parts.append(f"-lsr {line_start_range[0]} {line_start_range[1]}")
    if int(decoder_tuning['quality_threshold']) != int(defaults['quality_threshold']):
        parts.append(f"-lq {int(decoder_tuning['quality_threshold'])}")
    if int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)) != int(defaults.get('clock_lock', CLOCK_LOCK_DEFAULT)):
        parts.append(f"-cl {int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))}")
    if int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT)) != int(defaults.get('start_lock', START_LOCK_DEFAULT)):
        parts.append(f"-sl {int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT))}")
    if int(decoder_tuning['adaptive_threshold']) != int(defaults['adaptive_threshold']):
        parts.append(f"-at {int(decoder_tuning['adaptive_threshold'])}")
    if int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)) != int(defaults.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)):
        parts.append(f"-dr {int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))}")
    if int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)) != int(defaults.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)):
        parts.append(f"-wf {int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))}")
    if int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)) != int(defaults.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)):
        parts.append(f"-ala {int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))}")

    def format_shift_value(shift):
        shift = float(shift)
        if abs(shift - round(shift)) <= 1e-9:
            return f'{int(round(shift)):+d}'
        return f'{shift:+.2f}'.rstrip('0').rstrip('.')

    for line, shift in serialise_per_line_shift_map(
        decoder_tuning.get('per_line_shift', {}),
        maximum_line=DEFAULT_LINE_COUNT,
    ):
        parts.append(f'-pls {int(line)}:{format_shift_value(shift)}')
    if decoder_tuning.get('show_quality', False) and not defaults.get('show_quality', False):
        parts.append('--show-quality')
    if decoder_tuning.get('show_rejects', False) and not defaults.get('show_rejects', False):
        parts.append('--show-rejects')
    if decoder_tuning.get('show_start_clock', False) and not defaults.get('show_start_clock', False):
        parts.append('--show-start-clock')
    if decoder_tuning.get('show_clock_visuals', False) and not defaults.get('show_clock_visuals', False):
        parts.append('--show-clock-visuals')
    if decoder_tuning.get('show_alignment_visuals', False) and not defaults.get('show_alignment_visuals', False):
        parts.append('--show-alignment-visuals')
    if decoder_tuning.get('show_quality_meter', False) and not defaults.get('show_quality_meter', False):
        parts.append('--show-quality-meter')
    if decoder_tuning.get('show_histogram_graph', False) and not defaults.get('show_histogram_graph', False):
        parts.append('--show-histogram-graph')
    if decoder_tuning.get('show_eye_pattern', False) and not defaults.get('show_eye_pattern', False):
        parts.append('--show-eye-pattern')
    return ' '.join(parts)


def _frame_size_for_analysis(config):
    return int(config.line_bytes) * int(config.field_lines) * 2


def _count_complete_analysis_frames(input_path, config):
    frame_size = _frame_size_for_analysis(config)
    if frame_size <= 0:
        return 0
    return os.path.getsize(input_path) // frame_size


def _extract_analysis_preview_lines(frame, config, n_lines=None):
    field_range = list(config.field_range)
    lines_per_field = len(field_range)
    if n_lines is None:
        preview_lines = []
        for line in range(lines_per_field * 2):
            field, line_in_field = divmod(line, lines_per_field)
            raw_line = (field * config.field_lines) + field_range[line_in_field]
            start = raw_line * config.line_bytes
            end = start + config.line_bytes
            if end > len(frame):
                break
            preview_lines.append((line, frame[start:end]))
        return preview_lines

    preview_lines = []
    for line in range(max(int(n_lines), 0)):
        start = line * config.line_bytes
        end = start + config.line_bytes
        if end > len(frame):
            break
        preview_lines.append((line, frame[start:end]))
    return preview_lines


def _analysis_frame_indices(total_frames, mode=ANALYSIS_MODE_QUICK, quick_frames=DEFAULT_ANALYSIS_QUICK_FRAMES):
    total_frames = max(int(total_frames), 0)
    if total_frames <= 0:
        return ()
    if str(mode) == ANALYSIS_MODE_FULL:
        return tuple(range(total_frames))
    quick_frames = max(int(quick_frames), 1)
    if quick_frames >= total_frames:
        return tuple(range(total_frames))
    return tuple(sorted({int(value) for value in np.linspace(0, total_frames - 1, num=quick_frames)}))


def _sample_analysis_preview_lines(preview_lines, limit=DEFAULT_ANALYSIS_EVALUATION_LINES):
    preview_lines = tuple(preview_lines)
    limit = max(int(limit), 1)
    if len(preview_lines) <= limit:
        return preview_lines
    indices = np.linspace(0, len(preview_lines) - 1, num=limit)
    return tuple(preview_lines[int(index)] for index in indices)


def _analysis_evaluation_line_limit(analysis_kind):
    return int(ANALYSIS_EVALUATION_LINE_LIMITS.get(analysis_kind, DEFAULT_ANALYSIS_EVALUATION_LINES))


def _hold_analysis_runner(thread, worker):
    if IMPORT_ERROR is not None or thread is None:
        return
    key = int(id(thread))
    _ANALYSIS_THREAD_HOLD[key] = (thread, worker)

    def _cleanup():
        _ANALYSIS_THREAD_HOLD.pop(key, None)

    thread.finished.connect(_cleanup)


def _analysis_eta_text(seconds):
    if seconds is None:
        return '--:--'
    seconds = max(int(round(float(seconds))), 0)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
    return f'{minutes:02d}:{seconds:02d}'


def _split_timer_hms(total_seconds):
    total_seconds = max(int(round(float(total_seconds or 0))), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return hours, minutes, seconds


def _emit_analysis_progress(progress_callback, *, phase, current, total, percent, started_at=None):
    if progress_callback is None:
        return
    eta_seconds = None
    if started_at is not None and current and total and current < total:
        elapsed = max(time.monotonic() - float(started_at), 1e-6)
        eta_seconds = (elapsed / float(current)) * float(total - current)
    progress_callback({
        'phase': str(phase),
        'current': int(current),
        'total': int(total),
        'percent': int(max(min(round(float(percent)), 100), 0)),
        'eta_seconds': eta_seconds,
        'eta_text': _analysis_eta_text(eta_seconds),
    })


def analyse_vbi_file(
    analysis_kind,
    input_path,
    config,
    tape_format,
    controls,
    *,
    decoder_tuning=None,
    line_selection=None,
    mode=ANALYSIS_MODE_QUICK,
    quick_frames=DEFAULT_ANALYSIS_QUICK_FRAMES,
    n_lines=None,
    progress_callback=None,
):
    if not input_path:
        raise ValueError('Analysis needs a recorded .vbi file.')
    if not os.path.exists(input_path):
        raise ValueError(f'Input file not found: {input_path}')

    total_frames = _count_complete_analysis_frames(input_path, config)
    if total_frames <= 0:
        raise ValueError('Input file does not contain any complete VBI frames.')

    frame_indices = _analysis_frame_indices(total_frames, mode=mode, quick_frames=quick_frames)
    if not frame_indices:
        raise ValueError('No frames selected for analysis.')

    frame_size = _frame_size_for_analysis(config)
    preview_lines = []
    started_at = time.monotonic()

    with open(input_path, 'rb') as handle:
        for index, frame_index in enumerate(frame_indices, start=1):
            handle.seek(int(frame_index) * frame_size)
            frame = handle.read(frame_size)
            if len(frame) < frame_size:
                continue
            preview_lines.extend(_extract_analysis_preview_lines(frame, config, n_lines=n_lines))
            _emit_analysis_progress(
                progress_callback,
                phase='Scanning frames',
                current=index,
                total=len(frame_indices),
                percent=(index / max(len(frame_indices), 1)) * 80.0,
                started_at=started_at,
            )

    if not preview_lines:
        raise ValueError('Analysis preview is empty. No usable VBI frames were found.')

    evaluation_preview_lines = _sample_analysis_preview_lines(
        preview_lines,
        _analysis_evaluation_line_limit(analysis_kind),
    )

    _emit_analysis_progress(
        progress_callback,
        phase='Evaluating candidates',
        current=1,
        total=3,
        percent=86,
    )

    def report_evaluation_progress(current, total, phase='Evaluating candidates'):
        total = max(int(total), 1)
        current = max(min(int(current), total), 0)
        percent = 86.0 + (13.0 * (float(current) / float(total)))
        _emit_analysis_progress(
            progress_callback,
            phase=phase,
            current=current,
            total=total,
            percent=percent,
            started_at=started_at,
        )

    controls = _normalise_signal_controls_tuple(controls)
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
    line_selection = _normalise_line_selection(line_selection, line_count=DEFAULT_LINE_COUNT) if line_selection is not None else None
    result_controls = tuple(controls)
    result_decoder_tuning = decoder_tuning

    if analysis_kind == ANALYSIS_KIND_AUTO_TUNE:
        result_controls, result_decoder_tuning, stats = auto_tune_preview(
            evaluation_preview_lines,
            config,
            tape_format,
            controls,
            decoder_tuning=decoder_tuning,
            line_selection=line_selection,
            progress_callback=report_evaluation_progress,
        )
        summary = (
            f"Auto Tune analysed {stats['teletext_lines']}/{stats['analysed_lines']} teletext lines "
            f"across {len(frame_indices)}/{total_frames} frames"
            f" (sampled {len(evaluation_preview_lines)}/{len(preview_lines)} lines)."
        )
    elif analysis_kind == ANALYSIS_KIND_CLOCK_START:
        if decoder_tuning is None:
            raise ValueError('Clock / Start Auto-Lock needs decoder tuning.')
        result_decoder_tuning, stats = auto_lock_preview(
            evaluation_preview_lines,
            config,
            tape_format,
            controls,
            decoder_tuning=decoder_tuning,
            line_selection=line_selection,
            progress_callback=report_evaluation_progress,
        )
        summary = (
            "Clock / Start Auto-Lock suggests "
            f"range {result_decoder_tuning['line_start_range'][0]}-{result_decoder_tuning['line_start_range'][1]}, "
            f"clock {result_decoder_tuning['clock_lock']}, start {result_decoder_tuning['start_lock']} "
            f"from {stats['teletext_lines']}/{stats['analysed_lines']} teletext lines "
            f"across {len(frame_indices)}/{total_frames} frames"
            f" (sampled {len(evaluation_preview_lines)}/{len(preview_lines)} lines)."
        )
    else:
        raise ValueError(f'Unknown analysis kind: {analysis_kind}')

    _emit_analysis_progress(
        progress_callback,
        phase='Finalizing suggestion',
        current=1,
        total=1,
        percent=100,
    )

    return {
        'kind': str(analysis_kind),
        'title': ANALYSIS_KIND_TITLES.get(str(analysis_kind), 'VBI Analysis'),
        'mode': str(mode),
        'input_path': os.path.abspath(input_path),
        'total_frames': int(total_frames),
        'frames_analysed': int(len(frame_indices)),
        'controls': tuple(result_controls),
        'decoder_tuning': result_decoder_tuning,
        'line_selection': line_selection,
        'stats': dict(stats),
        'summary': summary,
    }


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
    impulse_filter=DEFAULT_CONTROLS[8],
    temporal_denoise=DEFAULT_CONTROLS[9],
    noise_reduction=DEFAULT_CONTROLS[10],
    hum_removal=DEFAULT_CONTROLS[11],
    auto_black_level=DEFAULT_CONTROLS[12],
    head_switching_mask=DEFAULT_CONTROLS[13],
    line_stabilization=DEFAULT_CONTROLS[14],
    auto_gain_contrast=DEFAULT_CONTROLS[15],
    impulse_filter_coeff=DEFAULT_CONTROLS[16],
    temporal_denoise_coeff=DEFAULT_CONTROLS[17],
    noise_reduction_coeff=DEFAULT_CONTROLS[18],
    hum_removal_coeff=DEFAULT_CONTROLS[19],
    auto_black_level_coeff=DEFAULT_CONTROLS[20],
    head_switching_mask_coeff=DEFAULT_CONTROLS[21],
    line_stabilization_coeff=DEFAULT_CONTROLS[22],
    auto_gain_contrast_coeff=DEFAULT_CONTROLS[23],
    defaults=DEFAULT_CONTROLS,
):
    defaults = _normalise_signal_controls_tuple(defaults)
    parts = []
    _append_value_coeff_part(parts, '-bn', brightness, brightness_coeff, defaults[0], defaults[4])
    _append_value_coeff_part(parts, '-sp', sharpness, sharpness_coeff, defaults[1], defaults[5])
    _append_value_coeff_part(parts, '-gn', gain, gain_coeff, defaults[2], defaults[6])
    _append_value_coeff_part(parts, '-ct', contrast, contrast_coeff, defaults[3], defaults[7])
    _append_value_coeff_part(parts, '-if', impulse_filter, impulse_filter_coeff, defaults[8], defaults[16])
    _append_value_coeff_part(parts, '-td', temporal_denoise, temporal_denoise_coeff, defaults[9], defaults[17])
    _append_value_coeff_part(parts, '-nr', noise_reduction, noise_reduction_coeff, defaults[10], defaults[18])
    _append_value_coeff_part(parts, '-hm', hum_removal, hum_removal_coeff, defaults[11], defaults[19])
    _append_value_coeff_part(parts, '-abl', auto_black_level, auto_black_level_coeff, defaults[12], defaults[20])
    _append_value_coeff_part(parts, '-hsm', head_switching_mask, head_switching_mask_coeff, defaults[13], defaults[21])
    _append_value_coeff_part(parts, '-lls', line_stabilization, line_stabilization_coeff, defaults[14], defaults[22])
    _append_value_coeff_part(parts, '-agc', auto_gain_contrast, auto_gain_contrast_coeff, defaults[15], defaults[23])
    return ' '.join(parts)


def format_tuning_args(
    controls,
    decoder_tuning=None,
    line_selection=None,
    line_count=DEFAULT_LINE_COUNT,
    fix_capture_card=None,
    *,
    control_defaults=DEFAULT_CONTROLS,
    decoder_defaults=None,
):
    signal_part = format_signal_controls(*controls, defaults=control_defaults)
    decoder_part = format_decoder_tuning(decoder_tuning, defaults=decoder_defaults)
    line_part = format_line_selection(line_selection, line_count=line_count)
    fix_part = format_fix_capture_card(fix_capture_card)
    parts = []
    if signal_part:
        parts.append(signal_part)
    if decoder_part:
        parts.append(decoder_part)
    if line_part:
        parts.append(line_part)
    if fix_part:
        parts.append(fix_part)
    return ' '.join(parts)


def parse_signal_controls_args(text, defaults=DEFAULT_CONTROLS):
    defaults = _normalise_signal_controls_tuple(defaults)
    values = {
        'brightness': int(defaults[0]),
        'sharpness': int(defaults[1]),
        'gain': int(defaults[2]),
        'contrast': int(defaults[3]),
        'brightness_coeff': float(defaults[4]),
        'sharpness_coeff': float(defaults[5]),
        'gain_coeff': float(defaults[6]),
        'contrast_coeff': float(defaults[7]),
        'impulse_filter': int(defaults[8]),
        'temporal_denoise': int(defaults[9]),
        'noise_reduction': int(defaults[10]),
        'hum_removal': int(defaults[11]),
        'auto_black_level': int(defaults[12]),
        'head_switching_mask': int(defaults[13]),
        'line_stabilization': int(defaults[14]),
        'auto_gain_contrast': int(defaults[15]),
        'impulse_filter_coeff': float(defaults[16]),
        'temporal_denoise_coeff': float(defaults[17]),
        'noise_reduction_coeff': float(defaults[18]),
        'hum_removal_coeff': float(defaults[19]),
        'auto_black_level_coeff': float(defaults[20]),
        'head_switching_mask_coeff': float(defaults[21]),
        'line_stabilization_coeff': float(defaults[22]),
        'auto_gain_contrast_coeff': float(defaults[23]),
    }
    option_map = {
        '-bn': ('brightness', 'brightness_coeff', float(defaults[4])),
        '--brightness': ('brightness', 'brightness_coeff', float(defaults[4])),
        '-sp': ('sharpness', 'sharpness_coeff', float(defaults[5])),
        '--sharpness': ('sharpness', 'sharpness_coeff', float(defaults[5])),
        '-gn': ('gain', 'gain_coeff', float(defaults[6])),
        '--gain': ('gain', 'gain_coeff', float(defaults[6])),
        '-ct': ('contrast', 'contrast_coeff', float(defaults[7])),
        '--contrast': ('contrast', 'contrast_coeff', float(defaults[7])),
        '-if': ('impulse_filter', 'impulse_filter_coeff', float(defaults[16])),
        '--impulse-filter': ('impulse_filter', 'impulse_filter_coeff', float(defaults[16])),
        '-td': ('temporal_denoise', 'temporal_denoise_coeff', float(defaults[17])),
        '--temporal-denoise': ('temporal_denoise', 'temporal_denoise_coeff', float(defaults[17])),
        '-nr': ('noise_reduction', 'noise_reduction_coeff', float(defaults[18])),
        '--noise-reduction': ('noise_reduction', 'noise_reduction_coeff', float(defaults[18])),
        '-hm': ('hum_removal', 'hum_removal_coeff', float(defaults[19])),
        '--hum-removal': ('hum_removal', 'hum_removal_coeff', float(defaults[19])),
        '-abl': ('auto_black_level', 'auto_black_level_coeff', float(defaults[20])),
        '--auto-black-level': ('auto_black_level', 'auto_black_level_coeff', float(defaults[20])),
        '-hsm': ('head_switching_mask', 'head_switching_mask_coeff', float(defaults[21])),
        '--head-switching-mask': ('head_switching_mask', 'head_switching_mask_coeff', float(defaults[21])),
        '-lls': ('line_stabilization', 'line_stabilization_coeff', float(defaults[22])),
        '--line-to-line-stabilization': ('line_stabilization', 'line_stabilization_coeff', float(defaults[22])),
        '-agc': ('auto_gain_contrast', 'auto_gain_contrast_coeff', float(defaults[23])),
        '--auto-gain-contrast': ('auto_gain_contrast', 'auto_gain_contrast_coeff', float(defaults[23])),
    }
    coeff_option_map = {
        '-ifcf': 'impulse_filter_coeff',
        '--impulse-filter-coeff': 'impulse_filter_coeff',
        '-tdcf': 'temporal_denoise_coeff',
        '--temporal-denoise-coeff': 'temporal_denoise_coeff',
        '-nrcf': 'noise_reduction_coeff',
        '--noise-reduction-coeff': 'noise_reduction_coeff',
        '-hmcf': 'hum_removal_coeff',
        '--hum-removal-coeff': 'hum_removal_coeff',
        '-ablcf': 'auto_black_level_coeff',
        '--auto-black-level-coeff': 'auto_black_level_coeff',
        '-hsmcf': 'head_switching_mask_coeff',
        '--head-switching-mask-coeff': 'head_switching_mask_coeff',
        '-llscf': 'line_stabilization_coeff',
        '--line-to-line-stabilization-coeff': 'line_stabilization_coeff',
        '-agccf': 'auto_gain_contrast_coeff',
        '--auto-gain-contrast-coeff': 'auto_gain_contrast_coeff',
    }
    tokens = shlex.split(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in option_map:
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            target, coeff_target, default_coeff = option_map[token]
            raw_value = tokens[index + 1]
            if '/' in raw_value:
                value_text, coeff_text = raw_value.split('/', 1)
            else:
                value_text, coeff_text = raw_value, None
            try:
                value = int(value_text)
            except ValueError as exc:
                raise ValueError(f'Invalid integer for {token}: {raw_value!r}.') from exc
            if value < 0 or value > 100:
                raise ValueError(f'Value for {token} must be between 0 and 100.')
            values[target] = value
            if coeff_text not in (None, ''):
                try:
                    coeff = float(coeff_text)
                except ValueError as exc:
                    raise ValueError(f'Invalid float coefficient for {token}: {raw_value!r}.') from exc
                if coeff < 0:
                    raise ValueError(f'Value for {token} must be zero or greater.')
                values[coeff_target] = coeff
            else:
                values[coeff_target] = float(default_coeff)
            index += 2
            continue
        if token in coeff_option_map:
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            target = coeff_option_map[token]
            raw_value = tokens[index + 1]
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ValueError(f'Invalid float for {token}: {raw_value!r}.') from exc
            if value < 0:
                raise ValueError(f'Value for {token} must be zero or greater.')
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
        values['impulse_filter'],
        values['temporal_denoise'],
        values['noise_reduction'],
        values['hum_removal'],
        values['auto_black_level'],
        values['head_switching_mask'],
        values['line_stabilization'],
        values['auto_gain_contrast'],
        values['impulse_filter_coeff'],
        values['temporal_denoise_coeff'],
        values['noise_reduction_coeff'],
        values['hum_removal_coeff'],
        values['auto_black_level_coeff'],
        values['head_switching_mask_coeff'],
        values['line_stabilization_coeff'],
        values['auto_gain_contrast_coeff'],
    )


def parse_decoder_tuning_args(text, defaults=None, tape_formats=None):
    defaults = _normalise_decoder_tuning(defaults)
    if defaults is None:
        return None
    values = {
        'tape_format': defaults['tape_format'],
        'extra_roll': int(defaults['extra_roll']),
        'line_start_range': tuple(int(value) for value in defaults['line_start_range']),
        'quality_threshold': int(defaults['quality_threshold']),
        'quality_threshold_coeff': float(defaults.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)),
        'clock_lock': int(defaults.get('clock_lock', CLOCK_LOCK_DEFAULT)),
        'clock_lock_coeff': float(defaults.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)),
        'start_lock': int(defaults.get('start_lock', START_LOCK_DEFAULT)),
        'start_lock_coeff': float(defaults.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)),
        'adaptive_threshold': int(defaults['adaptive_threshold']),
        'adaptive_threshold_coeff': float(defaults.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)),
        'dropout_repair': int(defaults.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)),
        'dropout_repair_coeff': float(defaults.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)),
        'wow_flutter_compensation': int(defaults.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)),
        'wow_flutter_compensation_coeff': float(defaults.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)),
        'auto_line_align': int(defaults.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
        'per_line_shift': normalise_per_line_shift_map(
            defaults.get('per_line_shift', {}),
            maximum_line=DEFAULT_LINE_COUNT,
        ),
        'show_quality': bool(defaults.get('show_quality', False)),
        'show_rejects': bool(defaults.get('show_rejects', False)),
        'show_start_clock': bool(defaults.get('show_start_clock', False)),
        'show_clock_visuals': bool(defaults.get('show_clock_visuals', False)),
        'show_alignment_visuals': bool(defaults.get('show_alignment_visuals', False)),
        'show_quality_meter': bool(defaults.get('show_quality_meter', False)),
        'show_histogram_graph': bool(defaults.get('show_histogram_graph', False)),
        'show_eye_pattern': bool(defaults.get('show_eye_pattern', False)),
    }
    tokens = shlex.split(text)
    decoder_value_coeff_option_map = {
        '-lq': ('quality_threshold', 'quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT),
        '--line-quality': ('quality_threshold', 'quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT),
        '-cl': ('clock_lock', 'clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT),
        '--clock-lock': ('clock_lock', 'clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT),
        '-sl': ('start_lock', 'start_lock_coeff', START_LOCK_COEFF_DEFAULT),
        '--start-lock': ('start_lock', 'start_lock_coeff', START_LOCK_COEFF_DEFAULT),
        '-at': ('adaptive_threshold', 'adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT),
        '--adaptive-threshold': ('adaptive_threshold', 'adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT),
        '-dr': ('dropout_repair', 'dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT),
        '--dropout-repair': ('dropout_repair', 'dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT),
        '-wf': ('wow_flutter_compensation', 'wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT),
        '--wow-flutter-compensation': ('wow_flutter_compensation', 'wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT),
    }
    legacy_decoder_coeff_option_map = {
        '-lqcf': 'quality_threshold_coeff',
        '--line-quality-coeff': 'quality_threshold_coeff',
        '-clcf': 'clock_lock_coeff',
        '--clock-lock-coeff': 'clock_lock_coeff',
        '-slcf': 'start_lock_coeff',
        '--start-lock-coeff': 'start_lock_coeff',
        '-atcf': 'adaptive_threshold_coeff',
        '--adaptive-threshold-coeff': 'adaptive_threshold_coeff',
        '-drcf': 'dropout_repair_coeff',
        '--dropout-repair-coeff': 'dropout_repair_coeff',
        '-wfcf': 'wow_flutter_compensation_coeff',
        '--wow-flutter-compensation-coeff': 'wow_flutter_compensation_coeff',
    }
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
        if token in ('-er', '--extra-roll'):
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            try:
                values['extra_roll'] = int(tokens[index + 1])
            except ValueError as exc:
                raise ValueError(f'Invalid integer for {token}: {tokens[index + 1]!r}.') from exc
            index += 2
            continue
        if token in ('-lsr', '--line-start-range'):
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
        if token in ('-ala', '--auto-line-align'):
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            try:
                value = int(tokens[index + 1], 10)
            except ValueError as exc:
                raise ValueError(f'Invalid integer for {token}: {tokens[index + 1]!r}.') from exc
            if value < 0 or value > 100:
                raise ValueError(f'Value for {token} must be between 0 and 100.')
            values['auto_line_align'] = value
            index += 2
            continue
        if token in ('-pls', '--per-line-shift'):
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            for raw_group in str(tokens[index + 1]).split(','):
                item = raw_group.strip()
                if not item or ':' not in item:
                    raise ValueError(f'Invalid LINE:SHIFT for {token}: {tokens[index + 1]!r}.')
                line_text, shift_text = item.split(':', 1)
                try:
                    line = int(line_text, 10)
                    shift = float(shift_text)
                except ValueError as exc:
                    raise ValueError(f'Invalid LINE:SHIFT for {token}: {item!r}.') from exc
                if line < 1 or line > DEFAULT_LINE_COUNT:
                    raise ValueError(f'Line for {token} must be between 1 and {DEFAULT_LINE_COUNT}.')
                if abs(shift) <= 1e-9:
                    values['per_line_shift'].pop(line, None)
                else:
                    values['per_line_shift'][line] = shift
            index += 2
            continue
        if token in decoder_value_coeff_option_map:
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            value_key, coeff_key, default_coeff = decoder_value_coeff_option_map[token]
            raw_value = tokens[index + 1]
            if '/' in raw_value:
                value_text, coeff_text = raw_value.split('/', 1)
            else:
                value_text, coeff_text = raw_value, None
            try:
                value = int(value_text, 10)
            except ValueError as exc:
                raise ValueError(f'Invalid integer for {token}: {raw_value!r}.') from exc
            if value < 0 or value > 100:
                raise ValueError(f'Value for {token} must be between 0 and 100.')
            if coeff_text in (None, ''):
                coeff = float(default_coeff)
            else:
                try:
                    coeff = float(coeff_text)
                except ValueError as exc:
                    raise ValueError(f'Invalid coefficient for {token}: {coeff_text!r}.') from exc
                if coeff < 0:
                    raise ValueError(f'Coefficient for {token} must be zero or greater.')
            values[value_key] = value
            values[coeff_key] = coeff
            index += 2
            continue
        if token in legacy_decoder_coeff_option_map:
            if index + 1 >= len(tokens):
                raise ValueError(f'Missing value for {token}.')
            try:
                coeff = float(tokens[index + 1])
            except ValueError as exc:
                raise ValueError(f'Invalid float for {token}: {tokens[index + 1]!r}.') from exc
            if coeff < 0:
                raise ValueError(f'Value for {token} must be zero or greater.')
            values[legacy_decoder_coeff_option_map[token]] = coeff
            index += 2
            continue
        if token in ('-sq', '--show-quality'):
            values['show_quality'] = True
            index += 1
            continue
        if token == '--no-show-quality':
            values['show_quality'] = False
            index += 1
            continue
        if token in ('-sr', '--show-rejects'):
            values['show_rejects'] = True
            index += 1
            continue
        if token == '--no-show-rejects':
            values['show_rejects'] = False
            index += 1
            continue
        if token in ('-ssc', '--show-start-clock'):
            values['show_start_clock'] = True
            index += 1
            continue
        if token == '--no-show-start-clock':
            values['show_start_clock'] = False
            index += 1
            continue
        if token in ('-scv', '--show-clock-visuals'):
            values['show_clock_visuals'] = True
            index += 1
            continue
        if token == '--no-show-clock-visuals':
            values['show_clock_visuals'] = False
            index += 1
            continue
        if token in ('-sav', '--show-alignment-visuals'):
            values['show_alignment_visuals'] = True
            index += 1
            continue
        if token == '--no-show-alignment-visuals':
            values['show_alignment_visuals'] = False
            index += 1
            continue
        if token in ('-sqm', '--show-quality-meter'):
            values['show_quality_meter'] = True
            index += 1
            continue
        if token == '--no-show-quality-meter':
            values['show_quality_meter'] = False
            index += 1
            continue
        if token in ('-shg', '--show-histogram-graph'):
            values['show_histogram_graph'] = True
            index += 1
            continue
        if token == '--no-show-histogram-graph':
            values['show_histogram_graph'] = False
            index += 1
            continue
        if token in ('-sep', '--show-eye-pattern'):
            values['show_eye_pattern'] = True
            index += 1
            continue
        if token == '--no-show-eye-pattern':
            values['show_eye_pattern'] = False
            index += 1
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


def _controls_with_updates(controls, **updates):
    controls = list(_normalise_signal_controls_tuple(controls))
    for key, value in updates.items():
        if key not in CONTROL_INDEX:
            continue
        controls[CONTROL_INDEX[key]] = value
    return tuple(controls)


def _decoder_tuning_with_updates(decoder_tuning, **updates):
    if decoder_tuning is None:
        return None
    updated = dict(_normalise_decoder_tuning(decoder_tuning))
    updated.update(updates)
    if 'line_start_range' in updated:
        updated['line_start_range'] = tuple(int(value) for value in updated['line_start_range'])
    if 'extra_roll' in updated:
        updated['extra_roll'] = int(updated['extra_roll'])
    if 'quality_threshold' in updated:
        updated['quality_threshold'] = int(updated['quality_threshold'])
    if 'quality_threshold_coeff' in updated:
        updated['quality_threshold_coeff'] = float(updated['quality_threshold_coeff'])
    if 'clock_lock' in updated:
        updated['clock_lock'] = int(updated['clock_lock'])
    if 'clock_lock_coeff' in updated:
        updated['clock_lock_coeff'] = float(updated['clock_lock_coeff'])
    if 'start_lock' in updated:
        updated['start_lock'] = int(updated['start_lock'])
    if 'start_lock_coeff' in updated:
        updated['start_lock_coeff'] = float(updated['start_lock_coeff'])
    if 'adaptive_threshold' in updated:
        updated['adaptive_threshold'] = int(updated['adaptive_threshold'])
    if 'adaptive_threshold_coeff' in updated:
        updated['adaptive_threshold_coeff'] = float(updated['adaptive_threshold_coeff'])
    if 'dropout_repair' in updated:
        updated['dropout_repair'] = int(updated['dropout_repair'])
    if 'dropout_repair_coeff' in updated:
        updated['dropout_repair_coeff'] = float(updated['dropout_repair_coeff'])
    if 'wow_flutter_compensation' in updated:
        updated['wow_flutter_compensation'] = int(updated['wow_flutter_compensation'])
    if 'wow_flutter_compensation_coeff' in updated:
        updated['wow_flutter_compensation_coeff'] = float(updated['wow_flutter_compensation_coeff'])
    if 'auto_line_align' in updated:
        updated['auto_line_align'] = int(updated['auto_line_align'])
    if 'per_line_shift' in updated:
        updated['per_line_shift'] = normalise_per_line_shift_map(updated['per_line_shift'], maximum_line=DEFAULT_LINE_COUNT)
    if 'show_quality' in updated:
        updated['show_quality'] = bool(updated['show_quality'])
    if 'show_rejects' in updated:
        updated['show_rejects'] = bool(updated['show_rejects'])
    if 'show_start_clock' in updated:
        updated['show_start_clock'] = bool(updated['show_start_clock'])
    if 'show_clock_visuals' in updated:
        updated['show_clock_visuals'] = bool(updated['show_clock_visuals'])
    if 'show_alignment_visuals' in updated:
        updated['show_alignment_visuals'] = bool(updated['show_alignment_visuals'])
    if 'show_quality_meter' in updated:
        updated['show_quality_meter'] = bool(updated['show_quality_meter'])
    if 'show_histogram_graph' in updated:
        updated['show_histogram_graph'] = bool(updated['show_histogram_graph'])
    if 'show_eye_pattern' in updated:
        updated['show_eye_pattern'] = bool(updated['show_eye_pattern'])
    return updated


def _evaluate_preview_candidate(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None):
    controls = _normalise_signal_controls_tuple(controls)
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)

    preview_config = config
    preview_tape_format = tape_format
    quality_threshold = QUALITY_THRESHOLD_DEFAULT
    quality_threshold_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
    clock_lock = CLOCK_LOCK_DEFAULT
    clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
    start_lock = START_LOCK_DEFAULT
    start_lock_coeff = START_LOCK_COEFF_DEFAULT
    adaptive_threshold = ADAPTIVE_THRESHOLD_DEFAULT
    adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
    dropout_repair = DROPOUT_REPAIR_DEFAULT
    dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
    wow_flutter_compensation = WOW_FLUTTER_COMPENSATION_DEFAULT
    wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT

    if decoder_tuning is not None:
        preview_config = config.retuned(
            extra_roll=int(decoder_tuning['extra_roll']),
            line_start_range=tuple(int(value) for value in decoder_tuning['line_start_range']),
        )
        preview_tape_format = decoder_tuning['tape_format']
        quality_threshold = int(decoder_tuning['quality_threshold'])
        quality_threshold_coeff = float(decoder_tuning['quality_threshold_coeff'])
        clock_lock = int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))
        start_lock = int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT))
        adaptive_threshold = int(decoder_tuning['adaptive_threshold'])
        dropout_repair = int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
        wow_flutter_compensation = int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
        line_control_overrides = decoder_tuning.get('line_control_overrides', {})
        line_decoder_overrides = decoder_tuning.get('line_decoder_overrides', {})
    else:
        line_control_overrides = {}
        line_decoder_overrides = {}

    selected_lines = None if line_selection is None else _normalise_line_selection(line_selection)

    Line.configure(
        preview_config,
        force_cpu=True,
        tape_format=preview_tape_format,
        brightness=controls[0],
        sharpness=controls[1],
        gain=controls[2],
        contrast=controls[3],
        brightness_coeff=controls[4],
        sharpness_coeff=controls[5],
        gain_coeff=controls[6],
        contrast_coeff=controls[7],
        impulse_filter=controls[8],
        temporal_denoise=controls[9],
        noise_reduction=controls[10],
        hum_removal=controls[11],
        auto_black_level=controls[12],
        head_switching_mask=controls[13],
        line_stabilization=controls[14],
        auto_gain_contrast=controls[15],
        impulse_filter_coeff=controls[16],
        temporal_denoise_coeff=controls[17],
        noise_reduction_coeff=controls[18],
        hum_removal_coeff=controls[19],
        auto_black_level_coeff=controls[20],
        head_switching_mask_coeff=controls[21],
        line_stabilization_coeff=controls[22],
        auto_gain_contrast_coeff=controls[23],
        quality_threshold=quality_threshold,
        quality_threshold_coeff=quality_threshold_coeff,
        clock_lock=clock_lock,
        start_lock=start_lock,
        adaptive_threshold=adaptive_threshold,
        dropout_repair=dropout_repair,
        wow_flutter_compensation=wow_flutter_compensation,
        line_control_overrides=line_control_overrides,
        line_decoder_overrides=line_decoder_overrides,
    )

    analysed_lines = 0
    teletext_lines = 0
    score = 0.0

    for number, raw_bytes in preview_lines:
        logical_line = (int(number) + 1) if number is not None else None
        if selected_lines is not None and logical_line is not None and logical_line not in selected_lines:
            continue
        analysed_lines += 1
        line = Line(raw_bytes, number)
        is_teletext = bool(line.is_teletext)
        signal_max = float(np.max(line._gstart)) if getattr(line, '_gstart', None) is not None else 0.0
        noise = float(line.noisefloor)
        margin = signal_max - noise
        if is_teletext:
            teletext_lines += 1
            score += 1000.0 + (margin * 2.0)
        else:
            score += margin - (noise * 0.25)

    return score, teletext_lines, analysed_lines


def auto_tune_preview(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None, progress_callback=None):
    controls = _normalise_signal_controls_tuple(controls)
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
    preview_lines = tuple(preview_lines)

    best_controls = tuple(controls)
    best_decoder = decoder_tuning
    best_score, best_teletext, analysed = _evaluate_preview_candidate(
        preview_lines,
        config,
        tape_format,
        best_controls,
        decoder_tuning=best_decoder,
        line_selection=line_selection,
    )

    control_candidates = {
        'impulse_filter': (0, 20, 40, 60, 80),
        'temporal_denoise': (0, 20, 40, 60),
        'noise_reduction': (0, 20, 40, 60),
        'hum_removal': (0, 20, 40, 60),
        'auto_black_level': (0, 20, 40, 60, 80),
    }
    decoder_candidates = {
        'clock_lock': (20, 40, 50, 60, 80),
        'start_lock': (20, 40, 50, 60, 80),
        'adaptive_threshold': (0, 20, 40, 60, 80),
        'quality_threshold': (35, 45, 50, 60, 70),
    }
    total_steps = 1
    for key, candidates in control_candidates.items():
        current_value = int(best_controls[CONTROL_INDEX[key]])
        total_steps += len(list(dict.fromkeys((current_value,) + tuple(int(value) for value in candidates))))
    if best_decoder is not None:
        for key, candidates in decoder_candidates.items():
            current_value = int(best_decoder[key])
            total_steps += len(list(dict.fromkeys((current_value,) + tuple(int(value) for value in candidates))))
    step = 1
    if callable(progress_callback):
        progress_callback(step, total_steps, 'Evaluating Auto Tune')

    for key, candidates in control_candidates.items():
        current_value = int(best_controls[CONTROL_INDEX[key]])
        values = list(dict.fromkeys((current_value,) + tuple(int(value) for value in candidates)))
        for candidate in values:
            step += 1
            if callable(progress_callback):
                progress_callback(step, total_steps, f'Evaluating Auto Tune ({key})')
            trial_controls = _controls_with_updates(best_controls, **{key: int(candidate)})
            trial_score, trial_teletext, _ = _evaluate_preview_candidate(
                preview_lines,
                config,
                tape_format,
                trial_controls,
                decoder_tuning=best_decoder,
                line_selection=line_selection,
            )
            if (trial_score, trial_teletext) > (best_score, best_teletext):
                best_controls = trial_controls
                best_score = trial_score
                best_teletext = trial_teletext

    if best_decoder is not None:
        for key, candidates in decoder_candidates.items():
            current_value = int(best_decoder[key])
            values = list(dict.fromkeys((current_value,) + tuple(int(value) for value in candidates)))
            for candidate in values:
                step += 1
                if callable(progress_callback):
                    progress_callback(step, total_steps, f'Evaluating Auto Tune ({key})')
                trial_decoder = _decoder_tuning_with_updates(best_decoder, **{key: int(candidate)})
                trial_score, trial_teletext, _ = _evaluate_preview_candidate(
                    preview_lines,
                    config,
                    tape_format,
                    best_controls,
                    decoder_tuning=trial_decoder,
                    line_selection=line_selection,
                )
                if (trial_score, trial_teletext) > (best_score, best_teletext):
                    best_decoder = trial_decoder
                    best_score = trial_score
                    best_teletext = trial_teletext

    return best_controls, best_decoder, {
        'score': float(best_score),
        'teletext_lines': int(best_teletext),
        'analysed_lines': int(analysed),
    }


def _build_preview_line_objects(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None):
    controls = _normalise_signal_controls_tuple(controls)
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)

    preview_config = config
    preview_tape_format = tape_format
    quality_threshold = QUALITY_THRESHOLD_DEFAULT
    quality_threshold_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
    clock_lock = CLOCK_LOCK_DEFAULT
    clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
    start_lock = START_LOCK_DEFAULT
    start_lock_coeff = START_LOCK_COEFF_DEFAULT
    adaptive_threshold = ADAPTIVE_THRESHOLD_DEFAULT
    adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
    dropout_repair = DROPOUT_REPAIR_DEFAULT
    dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
    wow_flutter_compensation = WOW_FLUTTER_COMPENSATION_DEFAULT
    wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT
    auto_line_align = AUTO_LINE_ALIGN_DEFAULT
    per_line_shift = {}
    line_control_overrides = {}
    line_decoder_overrides = {}

    if decoder_tuning is not None:
        preview_config = config.retuned(
            extra_roll=int(decoder_tuning['extra_roll']),
            line_start_range=tuple(int(value) for value in decoder_tuning['line_start_range']),
        )
        preview_tape_format = decoder_tuning['tape_format']
        quality_threshold = int(decoder_tuning['quality_threshold'])
        quality_threshold_coeff = float(decoder_tuning['quality_threshold_coeff'])
        clock_lock = int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))
        clock_lock_coeff = float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT))
        start_lock = int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT))
        start_lock_coeff = float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT))
        adaptive_threshold = int(decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT))
        adaptive_threshold_coeff = float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT))
        dropout_repair = int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
        dropout_repair_coeff = float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT))
        wow_flutter_compensation = int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
        wow_flutter_compensation_coeff = float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT))
        auto_line_align = int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))
        per_line_shift = normalise_per_line_shift_map(
            decoder_tuning.get('per_line_shift', {}),
            maximum_line=DEFAULT_LINE_COUNT,
        )
        line_control_overrides = decoder_tuning.get('line_control_overrides', {})
        line_decoder_overrides = decoder_tuning.get('line_decoder_overrides', {})

    selected_lines = None if line_selection is None else _normalise_line_selection(line_selection)
    Line.configure(
        preview_config,
        force_cpu=True,
        tape_format=preview_tape_format,
        brightness=controls[0],
        sharpness=controls[1],
        gain=controls[2],
        contrast=controls[3],
        brightness_coeff=controls[4],
        sharpness_coeff=controls[5],
        gain_coeff=controls[6],
        contrast_coeff=controls[7],
        impulse_filter=controls[8],
        temporal_denoise=controls[9],
        noise_reduction=controls[10],
        hum_removal=controls[11],
        auto_black_level=controls[12],
        head_switching_mask=controls[13],
        line_stabilization=controls[14],
        auto_gain_contrast=controls[15],
        impulse_filter_coeff=controls[16],
        temporal_denoise_coeff=controls[17],
        noise_reduction_coeff=controls[18],
        hum_removal_coeff=controls[19],
        auto_black_level_coeff=controls[20],
        head_switching_mask_coeff=controls[21],
        line_stabilization_coeff=controls[22],
        auto_gain_contrast_coeff=controls[23],
        quality_threshold=quality_threshold,
        quality_threshold_coeff=quality_threshold_coeff,
        clock_lock=clock_lock,
        clock_lock_coeff=clock_lock_coeff,
        start_lock=start_lock,
        start_lock_coeff=start_lock_coeff,
        adaptive_threshold=adaptive_threshold,
        adaptive_threshold_coeff=adaptive_threshold_coeff,
        dropout_repair=dropout_repair,
        dropout_repair_coeff=dropout_repair_coeff,
        wow_flutter_compensation=wow_flutter_compensation,
        wow_flutter_compensation_coeff=wow_flutter_compensation_coeff,
        auto_line_align=auto_line_align,
        per_line_shift=per_line_shift,
        line_control_overrides=line_control_overrides,
        line_decoder_overrides=line_decoder_overrides,
    )

    rendered_lines = []
    analysed_lines = 0
    teletext_lines = 0
    for number, raw_bytes in tuple(preview_lines):
        logical_line = (int(number) + 1) if number is not None else None
        if selected_lines is not None and logical_line is not None and logical_line not in selected_lines:
            continue
        analysed_lines += 1
        line = Line(raw_bytes, number)
        rendered_lines.append(line)
        if line.is_teletext:
            teletext_lines += 1

    return rendered_lines, preview_config, {
        'analysed_lines': int(analysed_lines),
        'teletext_lines': int(teletext_lines),
    }


def suggest_head_switch_crop(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None, progress_callback=None):
    if callable(progress_callback):
        progress_callback(1, 3, 'Evaluating Head-switch Crop')
    analysis_controls = _controls_with_updates(_normalise_signal_controls_tuple(controls), head_switching_mask=0)
    rendered_lines, _, stats = _build_preview_line_objects(
        preview_lines,
        config,
        tape_format,
        analysis_controls,
        decoder_tuning=decoder_tuning,
        line_selection=line_selection,
    )

    severities = []
    if callable(progress_callback):
        progress_callback(2, 3, 'Measuring Head-switch tail noise')
    for line in rendered_lines:
        if not line.is_teletext:
            continue
        samples = np.asarray(line.resampled, dtype=np.float32)
        if samples.size < 48:
            continue
        width = max(int(round(samples.size * 0.12)), 24)
        tail = samples[-width:]
        anchor = samples[-(width * 2):-width]
        if anchor.size != width:
            continue
        tail_energy = float(np.mean(np.abs(np.diff(tail))))
        anchor_energy = float(np.mean(np.abs(np.diff(anchor)))) + 1e-3
        tail_shift = abs(float(np.mean(tail)) - float(np.mean(anchor)))
        ratio = tail_energy / anchor_energy
        severity = max(ratio - 1.05, 0.0) * 58.0
        severity += min(tail_shift * 0.75, 42.0)
        severities.append(severity)

    suggested = int(np.clip(np.percentile(severities, 75) if severities else 0.0, 0.0, 100.0))
    if callable(progress_callback):
        progress_callback(3, 3, 'Finalizing Head-switch Crop suggestion')
    return suggested, {
        **stats,
        'tail_samples': int(len(severities)),
    }


def auto_lock_preview(preview_lines, config, tape_format, controls, decoder_tuning=None, line_selection=None, progress_callback=None):
    decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
    if decoder_tuning is None:
        raise ValueError('Clock / Start Auto-Lock needs decoder tuning.')

    if callable(progress_callback):
        progress_callback(1, 38, 'Evaluating Clock / Start Auto-Lock')
    rendered_lines, _, stats = _build_preview_line_objects(
        preview_lines,
        config,
        tape_format,
        controls,
        decoder_tuning=decoder_tuning,
        line_selection=line_selection,
    )
    starts = [int(line.start) for line in rendered_lines if line.is_teletext and line.start is not None]
    if not starts:
        raise ValueError('No teletext starts found in preview.')

    median_start = int(round(float(np.median(starts))))
    spread = float(np.std(starts)) if len(starts) > 1 else 0.0
    half_width = int(np.clip(round(14.0 + (spread * 2.5)), 10, 56))
    suggested_range = (max(median_start - half_width, 0), max(median_start + half_width, 0))
    best_decoder = _decoder_tuning_with_updates(decoder_tuning, line_start_range=suggested_range)

    best_score, best_teletext, analysed = _evaluate_preview_candidate(
        preview_lines,
        config,
        tape_format,
        controls,
        decoder_tuning=best_decoder,
        line_selection=line_selection,
    )
    step = 2
    if callable(progress_callback):
        progress_callback(step, 38, 'Evaluating Clock / Start Auto-Lock')
    for clock_candidate in (20, 35, 50, 65, 80, 95):
        for start_candidate in (20, 35, 50, 65, 80, 95):
            step += 1
            if callable(progress_callback):
                progress_callback(step, 38, 'Evaluating Clock / Start Auto-Lock')
            trial_decoder = _decoder_tuning_with_updates(
                best_decoder,
                clock_lock=int(clock_candidate),
                start_lock=int(start_candidate),
            )
            trial_score, trial_teletext, _ = _evaluate_preview_candidate(
                preview_lines,
                config,
                tape_format,
                controls,
                decoder_tuning=trial_decoder,
                line_selection=line_selection,
            )
            if (trial_score, trial_teletext) > (best_score, best_teletext):
                best_decoder = trial_decoder
                best_score = trial_score
                best_teletext = trial_teletext

    return best_decoder, {
        **stats,
        'score': float(best_score),
        'median_start': int(median_start),
        'start_spread': float(spread),
        'analysed_lines': int(analysed),
        'teletext_lines': int(best_teletext),
    }


def save_preset_text(
    path,
    text,
    disabled_functions=None,
    line_control_overrides=None,
    line_decoder_overrides=None,
    edit_target_line=None,
):
    payload = _normalise_preset_payload({
        'args': text,
        'disabled_functions': disabled_functions,
        'line_control_overrides': line_control_overrides,
        'line_decoder_overrides': line_decoder_overrides,
        'edit_target_line': edit_target_line,
    })
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        if (
            disabled_functions is None
            and not payload['line_control_overrides']
            and not payload['line_decoder_overrides']
            and payload['edit_target_line'] is None
        ):
            handle.write(payload['args'])
            handle.write('\n')
        else:
            json.dump({
                'version': PRESET_FILE_VERSION,
                'args': payload['args'],
                'disabled_functions': list(payload['disabled_functions']),
                'line_control_overrides': {
                    str(line): list(values)
                    for line, values in payload['line_control_overrides'].items()
                },
                'line_decoder_overrides': {
                    str(line): dict(values)
                    for line, values in payload['line_decoder_overrides'].items()
                },
                'edit_target_line': payload['edit_target_line'],
            }, handle, indent=2, ensure_ascii=False)
            handle.write('\n')


def load_preset_payload(path):
    with open(path, 'r', encoding='utf-8') as handle:
        content = handle.read()
    stripped = content.lstrip()
    if not stripped:
        raise ValueError('Preset file is empty.')
    if stripped.startswith('{'):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Preset file is invalid: {exc.msg}.') from exc
        return _normalise_preset_payload(payload)
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    if not lines:
        raise ValueError('Preset file is empty.')
    return _normalise_preset_payload(' '.join(lines))


def load_preset_text(path):
    return load_preset_payload(path)['args']


def default_local_preset_store_path(base_dir=None):
    root = os.path.expanduser(base_dir or '~')
    return os.path.join(root, '.vhsttx', LOCAL_PRESET_FILE)


def default_analysis_history_store_path(base_dir=None):
    root = os.path.expanduser(base_dir or '~')
    return os.path.join(root, '.vhsttx', ANALYSIS_HISTORY_FILE)


def _analysis_history_key(analysis_kind, input_path):
    return f'{str(analysis_kind).strip()}|{os.path.abspath(str(input_path))}'


def _normalise_analysis_result_payload(result):
    if not isinstance(result, dict):
        raise ValueError('Analysis result is invalid.')
    input_path = str(result.get('input_path', '')).strip()
    if not input_path:
        raise ValueError('Analysis result is missing input_path.')
    line_selection = result.get('line_selection')
    if line_selection is not None:
        line_selection = tuple(sorted(_normalise_line_selection(line_selection)))
    return {
        'kind': str(result.get('kind', '')).strip(),
        'title': str(result.get('title', 'VBI Analysis')).strip() or 'VBI Analysis',
        'mode': str(result.get('mode', ANALYSIS_MODE_QUICK)).strip() or ANALYSIS_MODE_QUICK,
        'input_path': os.path.abspath(input_path),
        'total_frames': max(int(result.get('total_frames', 0)), 0),
        'frames_analysed': max(int(result.get('frames_analysed', 0)), 0),
        'controls': tuple(_normalise_signal_controls_tuple(result.get('controls', DEFAULT_CONTROLS))),
        'decoder_tuning': _normalise_decoder_tuning(result.get('decoder_tuning')),
        'line_selection': line_selection,
        'stats': dict(result.get('stats', {})),
        'summary': str(result.get('summary', '')).strip(),
    }


def _normalise_analysis_history_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError('Analysis history entry is invalid.')
    analysis_kind = str(entry.get('analysis_kind', '')).strip()
    input_path = str(entry.get('input_path', '')).strip()
    if not analysis_kind or not input_path:
        raise ValueError('Analysis history entry is missing analysis_kind or input_path.')
    result = _normalise_analysis_result_payload(entry.get('result', {}))
    return {
        'analysis_kind': analysis_kind,
        'input_path': os.path.abspath(input_path),
        'mode': str(entry.get('mode', ANALYSIS_MODE_QUICK)).strip() or ANALYSIS_MODE_QUICK,
        'quick_frames': max(int(entry.get('quick_frames', DEFAULT_ANALYSIS_QUICK_FRAMES)), 1),
        'result': result,
    }


def load_analysis_history_entries(path=None):
    path = path or default_analysis_history_store_path()
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('Analysis history store is invalid.')
    entries = {}
    for key, value in data.items():
        try:
            entry = _normalise_analysis_history_entry(value)
        except ValueError:
            continue
        entries[str(key)] = entry
    return entries


def save_analysis_history_entries(entries, path=None):
    path = path or default_analysis_history_store_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    cleaned = {}
    for key, value in dict(entries).items():
        entry = _normalise_analysis_history_entry(value)
        cleaned[str(key)] = {
            'analysis_kind': entry['analysis_kind'],
            'input_path': entry['input_path'],
            'mode': entry['mode'],
            'quick_frames': int(entry['quick_frames']),
            'result': {
                'kind': entry['result']['kind'],
                'title': entry['result']['title'],
                'mode': entry['result']['mode'],
                'input_path': entry['result']['input_path'],
                'total_frames': int(entry['result']['total_frames']),
                'frames_analysed': int(entry['result']['frames_analysed']),
                'controls': list(entry['result']['controls']),
                'decoder_tuning': entry['result']['decoder_tuning'],
                'line_selection': list(entry['result']['line_selection']) if entry['result']['line_selection'] is not None else None,
                'stats': dict(entry['result']['stats']),
                'summary': entry['result']['summary'],
            },
        }
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(dict(sorted(cleaned.items(), key=lambda item: item[0].lower())), handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def load_analysis_history_entry(analysis_kind, input_path, path=None):
    key = _analysis_history_key(analysis_kind, input_path)
    return load_analysis_history_entries(path).get(key)


def save_analysis_history_entry(analysis_kind, input_path, mode, quick_frames, result, path=None):
    key = _analysis_history_key(analysis_kind, input_path)
    entries = load_analysis_history_entries(path)
    entries[key] = {
        'analysis_kind': str(analysis_kind).strip(),
        'input_path': os.path.abspath(str(input_path)),
        'mode': str(mode).strip() or ANALYSIS_MODE_QUICK,
        'quick_frames': max(int(quick_frames), 1),
        'result': _normalise_analysis_result_payload(result),
    }
    save_analysis_history_entries(entries, path)
    return entries[key]


def load_local_preset_entries(path=None):
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
        if not preset_name:
            continue
        payload = _normalise_preset_payload(value)
        presets[preset_name] = payload
    return dict(sorted(presets.items(), key=lambda item: item[0].lower()))


def load_local_presets(path=None):
    return {
        name: payload['args']
        for name, payload in load_local_preset_entries(path).items()
    }


def save_local_presets(presets, path=None):
    path = path or default_local_preset_store_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    cleaned = {}
    for name, value in presets.items():
        preset_name = str(name).strip()
        if not preset_name:
            raise ValueError('Preset name is empty.')
        payload = _normalise_preset_payload(value)
        cleaned[preset_name] = {
            'version': PRESET_FILE_VERSION,
            'args': payload['args'],
            'disabled_functions': list(payload['disabled_functions']),
            'line_control_overrides': {
                str(line): list(values)
                for line, values in payload['line_control_overrides'].items()
            },
            'line_decoder_overrides': {
                str(line): dict(values)
                for line, values in payload['line_decoder_overrides'].items()
            },
            'edit_target_line': payload['edit_target_line'],
        }
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(dict(sorted(cleaned.items(), key=lambda item: item[0].lower())), handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def save_local_preset(
    name,
    text,
    path=None,
    disabled_functions=None,
    line_control_overrides=None,
    line_decoder_overrides=None,
    edit_target_line=None,
):
    preset_name = str(name).strip()
    payload = _normalise_preset_payload({
        'args': text,
        'disabled_functions': disabled_functions,
        'line_control_overrides': line_control_overrides,
        'line_decoder_overrides': line_decoder_overrides,
        'edit_target_line': edit_target_line,
    })
    if not preset_name:
        raise ValueError('Preset name is empty.')
    presets = load_local_preset_entries(path)
    presets[preset_name] = payload
    save_local_presets(presets, path)
    return preset_name


def delete_local_preset(name, path=None):
    preset_name = str(name).strip()
    if not preset_name:
        raise ValueError('Preset name is empty.')
    presets = load_local_preset_entries(path)
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


def _run_dialog_window(dialog):
    loop = QtCore.QEventLoop()
    dialog.finished.connect(loop.quit)
    dialog.setModal(False)
    dialog.setWindowModality(QtCore.Qt.NonModal)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    loop.exec_()
    return dialog.result()


def _standard_window_flags():
    return (
        QtCore.Qt.Window
        | QtCore.Qt.CustomizeWindowHint
        | QtCore.Qt.WindowSystemMenuHint
        | QtCore.Qt.WindowTitleHint
        | QtCore.Qt.WindowCloseButtonHint
        | QtCore.Qt.WindowMinimizeButtonHint
        | QtCore.Qt.WindowMaximizeButtonHint
        | QtCore.Qt.WindowMinMaxButtonsHint
    ) & ~QtCore.Qt.WindowContextHelpButtonHint


if IMPORT_ERROR is None:
    class _VBIAnalysisWorker(QtCore.QObject):
        progress = QtCore.pyqtSignal(object)
        finished = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)

        def __init__(
            self,
            analysis_kind,
            input_path,
            config,
            tape_format,
            controls,
            decoder_tuning,
            line_selection,
            *,
            mode=ANALYSIS_MODE_QUICK,
            quick_frames=DEFAULT_ANALYSIS_QUICK_FRAMES,
            n_lines=None,
        ):
            super().__init__()
            self._analysis_kind = analysis_kind
            self._input_path = input_path
            self._config = config
            self._tape_format = tape_format
            self._controls = tuple(controls)
            self._decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._line_selection = line_selection
            self._mode = mode
            self._quick_frames = int(quick_frames)
            self._n_lines = n_lines

        @QtCore.pyqtSlot()
        def run(self):
            try:
                result = analyse_vbi_file(
                    self._analysis_kind,
                    self._input_path,
                    self._config,
                    self._tape_format,
                    self._controls,
                    decoder_tuning=self._decoder_tuning,
                    line_selection=self._line_selection,
                    mode=self._mode,
                    quick_frames=self._quick_frames,
                    n_lines=self._n_lines,
                    progress_callback=self.progress.emit,
                )
            except Exception as exc:  # pragma: no cover - GUI worker path
                self.failed.emit(str(exc))
                return
            self.finished.emit(result)


    class VBIAnalysisDialog(QtWidgets.QDialog):
        def __init__(
            self,
            analysis_kind,
            *,
            input_path,
            config,
            tape_format,
            controls,
            decoder_tuning=None,
            line_selection=None,
            n_lines=None,
            parent=None,
        ):
            super().__init__(parent)
            self._analysis_kind = str(analysis_kind)
            self._input_path = os.path.abspath(str(input_path))
            self._config = config
            self._tape_format = tape_format
            self._controls = _normalise_signal_controls_tuple(controls)
            self._decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._line_selection = _normalise_line_selection(line_selection, line_count=DEFAULT_LINE_COUNT) if line_selection is not None else None
            self._n_lines = n_lines
            self._total_frames = _count_complete_analysis_frames(self._input_path, self._config)
            self._analysis_result = None
            self._analysis_thread = None
            self._analysis_worker = None
            self._analysis_error_message = None
            self._analysis_history_path = default_analysis_history_store_path()

            self.setWindowTitle(f"{ANALYSIS_KIND_TITLES.get(self._analysis_kind, 'VBI Analysis')} - Analysis")
            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)
            self.resize(760, 440)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)

            options_layout = QtWidgets.QGridLayout()
            options_layout.setHorizontalSpacing(10)
            options_layout.setVerticalSpacing(8)
            root.addLayout(options_layout)

            options_layout.addWidget(QtWidgets.QLabel('Mode'), 0, 0)
            self._mode_box = QtWidgets.QComboBox()
            self._mode_box.addItem('Quick', ANALYSIS_MODE_QUICK)
            self._mode_box.addItem('Full', ANALYSIS_MODE_FULL)
            self._mode_box.currentIndexChanged.connect(self._mode_changed)
            options_layout.addWidget(self._mode_box, 0, 1)

            options_layout.addWidget(QtWidgets.QLabel('Frames'), 0, 2)
            self._quick_frames_box = QtWidgets.QSpinBox()
            self._quick_frames_box.setRange(1, max(self._total_frames, 1))
            self._quick_frames_box.setValue(min(max(DEFAULT_ANALYSIS_QUICK_FRAMES, 1), max(self._total_frames, 1)))
            self._quick_frames_box.setAccelerated(True)
            options_layout.addWidget(self._quick_frames_box, 0, 3)

            self._file_label = QtWidgets.QLabel(self._input_path)
            self._file_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            options_layout.addWidget(self._file_label, 1, 0, 1, 4)

            self._frames_label = QtWidgets.QLabel(f'Frames in file: {self._total_frames}')
            options_layout.addWidget(self._frames_label, 2, 0, 1, 4)

            self._progress_bar = QtWidgets.QProgressBar()
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
            root.addWidget(self._progress_bar)

            self._status_label = QtWidgets.QLabel('Ready.')
            root.addWidget(self._status_label)

            self._results_box = QtWidgets.QPlainTextEdit()
            self._results_box.setReadOnly(True)
            self._results_box.setPlaceholderText('Suggested settings will appear here after analysis.')
            root.addWidget(self._results_box, 1)

            buttons = QtWidgets.QHBoxLayout()
            root.addLayout(buttons)
            buttons.addStretch(1)

            self._start_button = QtWidgets.QPushButton('Start Analysis')
            self._start_button.clicked.connect(self._start_analysis)
            buttons.addWidget(self._start_button)

            self._apply_button = QtWidgets.QPushButton('Apply')
            self._apply_button.setEnabled(False)
            self._apply_button.clicked.connect(self.accept)
            buttons.addWidget(self._apply_button)

            cancel_button = QtWidgets.QPushButton('Cancel')
            cancel_button.clicked.connect(self.reject)
            buttons.addWidget(cancel_button)

            self._mode_changed()
            self._restore_saved_analysis()

        @property
        def analysis_result(self):
            return self._analysis_result

        def _mode_changed(self):
            is_quick = self._mode_box.currentData() == ANALYSIS_MODE_QUICK
            self._quick_frames_box.setEnabled(bool(is_quick))

        def _restore_saved_analysis(self):
            try:
                entry = load_analysis_history_entry(
                    self._analysis_kind,
                    self._input_path,
                    path=self._analysis_history_path,
                )
            except (OSError, ValueError):
                entry = None
            if not entry:
                return
            mode = entry.get('mode', ANALYSIS_MODE_QUICK)
            index = self._mode_box.findData(mode)
            if index >= 0:
                self._mode_box.setCurrentIndex(index)
            self._quick_frames_box.setValue(min(max(int(entry.get('quick_frames', DEFAULT_ANALYSIS_QUICK_FRAMES)), 1), max(self._total_frames, 1)))
            result = entry.get('result')
            if not result:
                return
            self._analysis_result = dict(result)
            self._results_box.setPlainText(self._format_result_text(self._analysis_result))
            self._progress_bar.setValue(100)
            self._status_label.setText('Loaded previous analysis. Review it or run a new analysis.')
            self._apply_button.setEnabled(True)

        def _save_analysis_history(self):
            if self._analysis_result is None:
                return
            try:
                save_analysis_history_entry(
                    self._analysis_kind,
                    self._input_path,
                    self._mode_box.currentData(),
                    self._quick_frames_box.value(),
                    self._analysis_result,
                    path=self._analysis_history_path,
                )
            except (OSError, ValueError):
                return

        def _set_running(self, running):
            running = bool(running)
            self._mode_box.setEnabled(not running)
            self._quick_frames_box.setEnabled(not running and self._mode_box.currentData() == ANALYSIS_MODE_QUICK)
            self._start_button.setEnabled(not running)
            self._apply_button.setEnabled((not running) and self._analysis_result is not None)

        def _format_result_text(self, result):
            current_args = format_tuning_args(
                self._controls,
                self._decoder_tuning,
                self._line_selection,
                line_count=DEFAULT_LINE_COUNT,
                fix_capture_card=None,
                control_defaults=DEFAULT_CONTROLS,
                decoder_defaults=self._decoder_tuning,
            )
            suggested_args = format_tuning_args(
                result['controls'],
                result['decoder_tuning'],
                self._line_selection,
                line_count=DEFAULT_LINE_COUNT,
                fix_capture_card=None,
                control_defaults=DEFAULT_CONTROLS,
                decoder_defaults=self._decoder_tuning,
            )
            lines = [
                f"{result['title']}",
                '',
                f"Mode: {'Full' if result['mode'] == ANALYSIS_MODE_FULL else 'Quick'}",
                f"Frames analysed: {result['frames_analysed']}/{result['total_frames']}",
                result['summary'],
                '',
                'Current Args:',
                current_args,
                '',
                'Suggested Args:',
                suggested_args,
            ]
            stats = dict(result.get('stats', {}))
            if stats:
                lines.extend((
                    '',
                    'Stats:',
                ))
                for key in sorted(stats):
                    lines.append(f"- {key.replace('_', ' ').title()}: {stats[key]}")
            return '\n'.join(lines)

        def _start_analysis(self):
            if self._analysis_thread is not None:
                return
            self._analysis_result = None
            self._analysis_error_message = None
            self._apply_button.setEnabled(False)
            self._results_box.clear()
            self._progress_bar.setValue(0)
            self._status_label.setText('Preparing analysis...')
            self._set_running(True)

            mode = self._mode_box.currentData()
            quick_frames = self._quick_frames_box.value()
            self._analysis_thread = QtCore.QThread()
            self._analysis_worker = _VBIAnalysisWorker(
                self._analysis_kind,
                self._input_path,
                self._config,
                self._tape_format,
                self._controls,
                self._decoder_tuning,
                self._line_selection,
                mode=mode,
                quick_frames=quick_frames,
                n_lines=self._n_lines,
            )
            self._analysis_worker.moveToThread(self._analysis_thread)
            self._analysis_thread.started.connect(self._analysis_worker.run)
            self._analysis_worker.progress.connect(self._analysis_progress)
            self._analysis_worker.finished.connect(self._analysis_finished)
            self._analysis_worker.failed.connect(self._analysis_failed)
            self._analysis_worker.finished.connect(self._analysis_thread.quit)
            self._analysis_worker.failed.connect(self._analysis_thread.quit)
            self._analysis_thread.finished.connect(self._analysis_thread_finished)
            self._analysis_thread.finished.connect(self._analysis_worker.deleteLater)
            self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)
            _hold_analysis_runner(self._analysis_thread, self._analysis_worker)
            self._analysis_thread.start()

        def _abandon_running_analysis(self):
            if self._analysis_worker is None or self._analysis_thread is None:
                return
            for signal, slot in (
                (self._analysis_worker.progress, self._analysis_progress),
                (self._analysis_worker.finished, self._analysis_finished),
                (self._analysis_worker.failed, self._analysis_failed),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._analysis_thread.requestInterruption()
            self._analysis_thread = None
            self._analysis_worker = None
            self._analysis_result = None
            self._analysis_error_message = None

        def _analysis_progress(self, payload):
            payload = dict(payload or {})
            percent = int(payload.get('percent', 0))
            phase = str(payload.get('phase', 'Working'))
            current = int(payload.get('current', 0))
            total = int(payload.get('total', 0))
            eta_text = str(payload.get('eta_text', '--:--'))
            self._progress_bar.setValue(percent)
            if total > 0:
                self._status_label.setText(f'{phase}: {current}/{total} ({percent}%) ETA {eta_text}')
            else:
                self._status_label.setText(f'{phase}: {percent}%')

        def _analysis_finished(self, result):
            self._analysis_result = dict(result or {})
            self._results_box.setPlainText(self._format_result_text(self._analysis_result))
            self._progress_bar.setValue(100)
            self._status_label.setText('Analysis complete. Review the suggestion and click Apply if it looks good.')
            self._apply_button.setEnabled(True)
            self._save_analysis_history()

        def _analysis_failed(self, message):
            self._analysis_result = None
            self._analysis_error_message = str(message)
            self._progress_bar.setValue(0)
            self._status_label.setText('Analysis failed.')

        def _analysis_thread_finished(self):
            error_message = self._analysis_error_message
            self._analysis_thread = None
            self._analysis_worker = None
            self._analysis_error_message = None
            self._set_running(False)
            if error_message:
                QtWidgets.QMessageBox.warning(self, 'VBI Analysis', error_message)

        def accept(self):  # pragma: no cover - GUI path
            self._save_analysis_history()
            super().accept()

        def reject(self):  # pragma: no cover - GUI path
            self._save_analysis_history()
            if self._analysis_thread is not None:
                self._abandon_running_analysis()
            super().reject()

        def closeEvent(self, event):  # pragma: no cover - GUI path
            self._save_analysis_history()
            if self._analysis_thread is not None:
                self._abandon_running_analysis()
            super().closeEvent(event)


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
            analysis_source=None,
            visible_sections=None,
            timer_seconds=None,
            show_timer=False,
            parent=None,
        ):
            super().__init__(parent)
            controls = _normalise_signal_controls_tuple(controls)
            self.setWindowTitle(title)
            self._preview_provider = preview_provider
            self._config = config
            self._tape_format = tape_format
            self._live = live
            self._decoder_tuning_enabled = bool(decoder_tuning is not None and tape_formats)
            self._tape_formats = list(tape_formats or [])
            self._last_image = None
            self._external_change_callback = None
            self._updating_args_text = False
            self._show_preview = self._preview_provider is not None and self._config is not None
            self._analysis_source = dict(analysis_source) if analysis_source is not None else None
            self._decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._default_decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._line_count = int(line_count)
            self._global_controls = _normalise_signal_controls_tuple(controls)
            self._default_global_controls = tuple(self._global_controls)
            self._line_control_overrides = normalise_line_control_overrides(
                (self._decoder_tuning or {}).get('line_control_overrides', {}),
                line_count=self._line_count,
            )
            self._default_line_control_overrides = normalise_line_control_overrides(
                self._line_control_overrides,
                line_count=self._line_count,
            )
            self._line_decoder_overrides = normalise_line_decoder_overrides(
                (self._decoder_tuning or {}).get('line_decoder_overrides', {}),
                line_count=self._line_count,
            )
            self._default_line_decoder_overrides = normalise_line_decoder_overrides(
                self._line_decoder_overrides,
                line_count=self._line_count,
            )
            self._global_decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
            self._per_line_shift_map = normalise_per_line_shift_map(
                (self._global_decoder_tuning or {}).get('per_line_shift', {}),
                maximum_line=self._line_count,
            )
            self._default_line_selection = _normalise_line_selection(line_selection, line_count=self._line_count)
            self._default_fix_capture_card = normalise_fix_capture_card(fix_capture_card)
            self._show_timer = bool(show_timer and not live)
            self._default_timer_seconds = max(int(round(float(timer_seconds or 0))), 0)
            self._edit_target_line = None
            self._last_preset_path = None
            self._local_preset_store_path = default_local_preset_store_path()
            self._local_presets = {}
            self._section_buttons = {}
            self._section_widgets = {}
            self._section_headers = {}
            self._disabled_functions = set()
            self._function_rows = {}
            self._function_actions = {}
            self._ctrl_reset_targets = {}
            self._function_menu_button = None
            self._visible_sections = set(visible_sections) if visible_sections is not None else None
            self._history_limit = 200
            self._history_undo_stack = []
            self._history_redo_stack = []
            self._history_navigation_in_progress = False
            self._bulk_change_depth = 0
            self._undo_button = None
            self._redo_button = None
            self._history_commit_timer = QtCore.QTimer(self)
            self._history_commit_timer.setSingleShot(True)
            self._history_commit_timer.setInterval(180)
            self._history_commit_timer.timeout.connect(self._commit_history_snapshot)

            self.setWindowFlags(_standard_window_flags())
            self.setModal(False)
            self.setWindowModality(QtCore.Qt.NonModal)

            if self._show_preview:
                self.resize(960, 620)
            else:
                self.resize(520, 250)
                self.setMinimumWidth(460)

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(10)
            self._root_layout = root

            self._preview_label = None
            if self._show_preview:
                self._preview_label = QtWidgets.QLabel('Waiting for preview...')
                self._preview_label.setMinimumSize(720, 320)
                self._preview_label.setAlignment(QtCore.Qt.AlignCenter)
                self._preview_label.setFrameShape(QtWidgets.QFrame.StyledPanel)
                root.addWidget(self._preview_label, 1)

            target_widget = QtWidgets.QWidget()
            target_layout = QtWidgets.QHBoxLayout(target_widget)
            target_layout.setContentsMargins(0, 0, 0, 0)
            target_layout.setSpacing(8)
            target_layout.addWidget(QtWidgets.QLabel('Edit Target'))
            self._edit_target_mode_box = QtWidgets.QComboBox()
            self._edit_target_mode_box.addItem('All Lines', EDIT_TARGET_ALL)
            self._edit_target_mode_box.addItem('Selected Line', EDIT_TARGET_SINGLE)
            self._edit_target_mode_box.currentIndexChanged.connect(self._edit_target_changed)
            target_layout.addWidget(self._edit_target_mode_box)
            target_layout.addWidget(QtWidgets.QLabel('Line'))
            self._edit_target_line_box = QtWidgets.QSpinBox()
            self._edit_target_line_box.setRange(1, self._line_count)
            self._edit_target_line_box.valueChanged.connect(self._edit_target_changed)
            target_layout.addWidget(self._edit_target_line_box)
            self._edit_target_status = QtWidgets.QLabel('Editing all lines')
            target_layout.addWidget(self._edit_target_status, 1)
            root.addWidget(target_widget)

            controls_group = QtWidgets.QGroupBox()
            form = QtWidgets.QGridLayout(controls_group)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
            self._add_section('Signal Controls', controls_group, reset_callback=self._reset_signal_controls)

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
                self._register_function_row(
                    key,
                    label,
                    (slider, spin_box, coeff_box),
                    lambda control_key=key: self._reset_signal_control(control_key),
                )

            cleanup_group = QtWidgets.QGroupBox()
            cleanup_form = QtWidgets.QGridLayout(cleanup_group)
            cleanup_form.setHorizontalSpacing(10)
            cleanup_form.setVerticalSpacing(8)
            self._add_section('Signal Cleanup', cleanup_group, expanded=False, reset_callback=self._reset_signal_cleanup)

            cleanup_form.addWidget(QtWidgets.QLabel('Value'), 0, 2)
            cleanup_form.addWidget(QtWidgets.QLabel('Coeff'), 0, 3)
            self._cleanup_sliders = {}
            self._cleanup_spin_boxes = {}
            self._cleanup_coeff_boxes = {}
            for row, (name, key, value, coeff) in enumerate((
                ('Impulse Filter', 'impulse_filter', controls[8], controls[16]),
                ('Temporal Denoise', 'temporal_denoise', controls[9], controls[17]),
                ('Noise Reduction', 'noise_reduction', controls[10], controls[18]),
                ('Hum Removal', 'hum_removal', controls[11], controls[19]),
                ('Auto Black Level', 'auto_black_level', controls[12], controls[20]),
                ('Head Switching Mask', 'head_switching_mask', controls[13], controls[21]),
                ('Line-to-Line Stabilization', 'line_stabilization', controls[14], controls[22]),
                ('Auto Gain / Contrast', 'auto_gain_contrast', controls[15], controls[23]),
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
                cleanup_form.addWidget(label, row, 0)
                cleanup_form.addWidget(slider, row, 1)
                cleanup_form.addWidget(spin_box, row, 2)
                cleanup_form.addWidget(coeff_box, row, 3)
                self._cleanup_sliders[key] = slider
                self._cleanup_spin_boxes[key] = spin_box
                self._cleanup_coeff_boxes[key] = coeff_box
                self._register_function_row(
                    key,
                    label,
                    (slider, spin_box, coeff_box),
                    lambda control_key=key: self._reset_cleanup_control(control_key),
                )

            if self._decoder_tuning_enabled:
                decoder_group = QtWidgets.QGroupBox()
                decoder_form = QtWidgets.QGridLayout(decoder_group)
                decoder_form.setHorizontalSpacing(10)
                decoder_form.setVerticalSpacing(8)
                self._add_section('Decoder Tuning', decoder_group, expanded=False, reset_callback=self._reset_decoder_tuning)

                template_label = QtWidgets.QLabel('Template')
                decoder_form.addWidget(template_label, 0, 0)
                self._tape_format_box = QtWidgets.QComboBox()
                self._tape_format_box.addItems(self._tape_formats)
                current_index = max(self._tape_formats.index(self._decoder_tuning['tape_format']), 0) if self._decoder_tuning['tape_format'] in self._tape_formats else 0
                self._tape_format_box.setCurrentIndex(current_index)
                self._tape_format_box.currentIndexChanged.connect(self._controls_changed)
                decoder_form.addWidget(self._tape_format_box, 0, 1, 1, 3)
                self._register_function_row(
                    'tape_format',
                    template_label,
                    (self._tape_format_box,),
                    self._reset_tape_format,
                )

                extra_roll_label = QtWidgets.QLabel('Extra Roll')
                decoder_form.addWidget(extra_roll_label, 1, 0)
                self._extra_roll_box = QtWidgets.QSpinBox()
                self._extra_roll_box.setRange(-64, 64)
                self._extra_roll_box.setValue(int(self._decoder_tuning['extra_roll']))
                self._extra_roll_box.setAccelerated(True)
                self._extra_roll_box.valueChanged.connect(self._controls_changed)
                decoder_form.addWidget(self._extra_roll_box, 1, 1)
                self._register_function_row(
                    'extra_roll',
                    extra_roll_label,
                    (self._extra_roll_box,),
                    self._reset_extra_roll,
                )

                line_start_label = QtWidgets.QLabel('Line Start Range')
                decoder_form.addWidget(line_start_label, 2, 0)
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
                self._register_function_row(
                    'line_start_range',
                    line_start_label,
                    (self._line_start_start_box, self._line_start_end_box),
                    self._reset_line_start_range,
                )

                quality_label = QtWidgets.QLabel('Line Quality')
                decoder_form.addWidget(quality_label, 3, 0)
                self._quality_threshold_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._quality_threshold_slider.setRange(0, 100)
                self._quality_threshold_slider.setValue(int(self._decoder_tuning['quality_threshold']))
                self._quality_threshold_slider.setTickInterval(10)
                self._quality_threshold_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._quality_threshold_box = QtWidgets.QSpinBox()
                self._quality_threshold_box.setRange(0, 100)
                self._quality_threshold_box.setValue(int(self._decoder_tuning['quality_threshold']))
                self._quality_threshold_box.setAccelerated(True)
                self._quality_threshold_coeff_box = QtWidgets.QDoubleSpinBox()
                self._quality_threshold_coeff_box.setRange(0.0, 999.0)
                self._quality_threshold_coeff_box.setDecimals(2)
                self._quality_threshold_coeff_box.setSingleStep(0.1)
                self._quality_threshold_coeff_box.setAccelerated(True)
                self._quality_threshold_coeff_box.setMinimumWidth(84)
                self._quality_threshold_coeff_box.setValue(float(self._decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)))
                self._quality_threshold_slider.valueChanged.connect(self._quality_threshold_box.setValue)
                self._quality_threshold_box.valueChanged.connect(self._quality_threshold_slider.setValue)
                self._quality_threshold_slider.valueChanged.connect(self._controls_changed)
                self._quality_threshold_coeff_box.hide()
                decoder_form.addWidget(self._quality_threshold_slider, 3, 1)
                decoder_form.addWidget(self._quality_threshold_box, 3, 2)
                self._register_function_row(
                    'quality_threshold',
                    quality_label,
                    (self._quality_threshold_slider, self._quality_threshold_box),
                    self._reset_quality_threshold,
                )

                clock_lock_label = QtWidgets.QLabel('Clock Lock')
                decoder_form.addWidget(clock_lock_label, 4, 0)
                self._clock_lock_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._clock_lock_slider.setRange(0, 100)
                self._clock_lock_slider.setValue(int(self._decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)))
                self._clock_lock_slider.setTickInterval(10)
                self._clock_lock_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._clock_lock_box = QtWidgets.QSpinBox()
                self._clock_lock_box.setRange(0, 100)
                self._clock_lock_box.setValue(int(self._decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)))
                self._clock_lock_box.setAccelerated(True)
                self._clock_lock_coeff_box = QtWidgets.QDoubleSpinBox()
                self._clock_lock_coeff_box.setRange(0.0, 999.0)
                self._clock_lock_coeff_box.setDecimals(2)
                self._clock_lock_coeff_box.setSingleStep(0.1)
                self._clock_lock_coeff_box.setAccelerated(True)
                self._clock_lock_coeff_box.setMinimumWidth(84)
                self._clock_lock_coeff_box.setValue(float(self._decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)))
                self._clock_lock_slider.valueChanged.connect(self._clock_lock_box.setValue)
                self._clock_lock_box.valueChanged.connect(self._clock_lock_slider.setValue)
                self._clock_lock_slider.valueChanged.connect(self._controls_changed)
                self._clock_lock_coeff_box.hide()
                decoder_form.addWidget(self._clock_lock_slider, 4, 1)
                decoder_form.addWidget(self._clock_lock_box, 4, 2)
                self._register_function_row(
                    'clock_lock',
                    clock_lock_label,
                    (self._clock_lock_slider, self._clock_lock_box),
                    self._reset_clock_lock,
                )

                start_lock_label = QtWidgets.QLabel('Start Lock')
                decoder_form.addWidget(start_lock_label, 5, 0)
                self._start_lock_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._start_lock_slider.setRange(0, 100)
                self._start_lock_slider.setValue(int(self._decoder_tuning.get('start_lock', START_LOCK_DEFAULT)))
                self._start_lock_slider.setTickInterval(10)
                self._start_lock_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._start_lock_box = QtWidgets.QSpinBox()
                self._start_lock_box.setRange(0, 100)
                self._start_lock_box.setValue(int(self._decoder_tuning.get('start_lock', START_LOCK_DEFAULT)))
                self._start_lock_box.setAccelerated(True)
                self._start_lock_coeff_box = QtWidgets.QDoubleSpinBox()
                self._start_lock_coeff_box.setRange(0.0, 999.0)
                self._start_lock_coeff_box.setDecimals(2)
                self._start_lock_coeff_box.setSingleStep(0.1)
                self._start_lock_coeff_box.setAccelerated(True)
                self._start_lock_coeff_box.setMinimumWidth(84)
                self._start_lock_coeff_box.setValue(float(self._decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)))
                self._start_lock_slider.valueChanged.connect(self._start_lock_box.setValue)
                self._start_lock_box.valueChanged.connect(self._start_lock_slider.setValue)
                self._start_lock_slider.valueChanged.connect(self._controls_changed)
                self._start_lock_coeff_box.hide()
                decoder_form.addWidget(self._start_lock_slider, 5, 1)
                decoder_form.addWidget(self._start_lock_box, 5, 2)
                self._register_function_row(
                    'start_lock',
                    start_lock_label,
                    (self._start_lock_slider, self._start_lock_box),
                    self._reset_start_lock,
                )

                adaptive_threshold_label = QtWidgets.QLabel('Adaptive Threshold')
                decoder_form.addWidget(adaptive_threshold_label, 6, 0)
                self._adaptive_threshold_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._adaptive_threshold_slider.setRange(0, 100)
                self._adaptive_threshold_slider.setValue(int(self._decoder_tuning['adaptive_threshold']))
                self._adaptive_threshold_slider.setTickInterval(10)
                self._adaptive_threshold_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._adaptive_threshold_box = QtWidgets.QSpinBox()
                self._adaptive_threshold_box.setRange(0, 100)
                self._adaptive_threshold_box.setValue(int(self._decoder_tuning['adaptive_threshold']))
                self._adaptive_threshold_box.setAccelerated(True)
                self._adaptive_threshold_coeff_box = QtWidgets.QDoubleSpinBox()
                self._adaptive_threshold_coeff_box.setRange(0.0, 999.0)
                self._adaptive_threshold_coeff_box.setDecimals(2)
                self._adaptive_threshold_coeff_box.setSingleStep(0.1)
                self._adaptive_threshold_coeff_box.setAccelerated(True)
                self._adaptive_threshold_coeff_box.setMinimumWidth(84)
                self._adaptive_threshold_coeff_box.setValue(float(self._decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)))
                self._adaptive_threshold_slider.valueChanged.connect(self._adaptive_threshold_box.setValue)
                self._adaptive_threshold_box.valueChanged.connect(self._adaptive_threshold_slider.setValue)
                self._adaptive_threshold_slider.valueChanged.connect(self._controls_changed)
                self._adaptive_threshold_coeff_box.hide()
                decoder_form.addWidget(self._adaptive_threshold_slider, 6, 1)
                decoder_form.addWidget(self._adaptive_threshold_box, 6, 2)
                self._register_function_row(
                    'adaptive_threshold',
                    adaptive_threshold_label,
                    (self._adaptive_threshold_slider, self._adaptive_threshold_box),
                    self._reset_adaptive_threshold,
                )

                dropout_repair_label = QtWidgets.QLabel('Dropout Repair')
                decoder_form.addWidget(dropout_repair_label, 7, 0)
                self._dropout_repair_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._dropout_repair_slider.setRange(0, 100)
                self._dropout_repair_slider.setValue(int(self._decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)))
                self._dropout_repair_slider.setTickInterval(10)
                self._dropout_repair_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._dropout_repair_box = QtWidgets.QSpinBox()
                self._dropout_repair_box.setRange(0, 100)
                self._dropout_repair_box.setValue(int(self._decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)))
                self._dropout_repair_box.setAccelerated(True)
                self._dropout_repair_coeff_box = QtWidgets.QDoubleSpinBox()
                self._dropout_repair_coeff_box.setRange(0.0, 999.0)
                self._dropout_repair_coeff_box.setDecimals(2)
                self._dropout_repair_coeff_box.setSingleStep(0.1)
                self._dropout_repair_coeff_box.setAccelerated(True)
                self._dropout_repair_coeff_box.setMinimumWidth(84)
                self._dropout_repair_coeff_box.setValue(float(self._decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)))
                self._dropout_repair_slider.valueChanged.connect(self._dropout_repair_box.setValue)
                self._dropout_repair_box.valueChanged.connect(self._dropout_repair_slider.setValue)
                self._dropout_repair_slider.valueChanged.connect(self._controls_changed)
                self._dropout_repair_coeff_box.hide()
                decoder_form.addWidget(self._dropout_repair_slider, 7, 1)
                decoder_form.addWidget(self._dropout_repair_box, 7, 2)
                self._register_function_row(
                    'dropout_repair',
                    dropout_repair_label,
                    (self._dropout_repair_slider, self._dropout_repair_box),
                    self._reset_dropout_repair,
                )

                wow_flutter_label = QtWidgets.QLabel('Wow/Flutter Compensation')
                decoder_form.addWidget(wow_flutter_label, 8, 0)
                self._wow_flutter_compensation_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._wow_flutter_compensation_slider.setRange(0, 100)
                self._wow_flutter_compensation_slider.setValue(int(self._decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)))
                self._wow_flutter_compensation_slider.setTickInterval(10)
                self._wow_flutter_compensation_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._wow_flutter_compensation_box = QtWidgets.QSpinBox()
                self._wow_flutter_compensation_box.setRange(0, 100)
                self._wow_flutter_compensation_box.setValue(int(self._decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)))
                self._wow_flutter_compensation_box.setAccelerated(True)
                self._wow_flutter_compensation_coeff_box = QtWidgets.QDoubleSpinBox()
                self._wow_flutter_compensation_coeff_box.setRange(0.0, 999.0)
                self._wow_flutter_compensation_coeff_box.setDecimals(2)
                self._wow_flutter_compensation_coeff_box.setSingleStep(0.1)
                self._wow_flutter_compensation_coeff_box.setAccelerated(True)
                self._wow_flutter_compensation_coeff_box.setMinimumWidth(84)
                self._wow_flutter_compensation_coeff_box.setValue(float(self._decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)))
                self._wow_flutter_compensation_slider.valueChanged.connect(self._wow_flutter_compensation_box.setValue)
                self._wow_flutter_compensation_box.valueChanged.connect(self._wow_flutter_compensation_slider.setValue)
                self._wow_flutter_compensation_slider.valueChanged.connect(self._controls_changed)
                self._wow_flutter_compensation_coeff_box.hide()
                decoder_form.addWidget(self._wow_flutter_compensation_slider, 8, 1)
                decoder_form.addWidget(self._wow_flutter_compensation_box, 8, 2)
                self._register_function_row(
                    'wow_flutter_compensation',
                    wow_flutter_label,
                    (self._wow_flutter_compensation_slider, self._wow_flutter_compensation_box),
                    self._reset_wow_flutter_compensation,
                )

                auto_line_align_label = QtWidgets.QLabel('Auto Line Align')
                decoder_form.addWidget(auto_line_align_label, 9, 0)
                self._auto_line_align_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._auto_line_align_slider.setRange(0, 100)
                self._auto_line_align_slider.setValue(int(self._decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)))
                self._auto_line_align_slider.setTickInterval(10)
                self._auto_line_align_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
                self._auto_line_align_box = QtWidgets.QSpinBox()
                self._auto_line_align_box.setRange(0, 100)
                self._auto_line_align_box.setValue(int(self._decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)))
                self._auto_line_align_box.setAccelerated(True)
                self._auto_line_align_slider.valueChanged.connect(self._auto_line_align_box.setValue)
                self._auto_line_align_box.valueChanged.connect(self._auto_line_align_slider.setValue)
                self._auto_line_align_slider.valueChanged.connect(self._controls_changed)
                decoder_form.addWidget(self._auto_line_align_slider, 9, 1)
                decoder_form.addWidget(self._auto_line_align_box, 9, 2)
                self._register_function_row(
                    'auto_line_align',
                    auto_line_align_label,
                    (self._auto_line_align_slider, self._auto_line_align_box),
                    self._reset_auto_line_align,
                )

                per_line_shift_label = QtWidgets.QLabel('Per-Line Shift')
                decoder_form.addWidget(per_line_shift_label, 10, 0)
                self._per_line_shift_editor = QtWidgets.QWidget()
                per_line_shift_layout = QtWidgets.QHBoxLayout(self._per_line_shift_editor)
                per_line_shift_layout.setContentsMargins(0, 0, 0, 0)
                per_line_shift_layout.setSpacing(6)
                per_line_shift_layout.addWidget(QtWidgets.QLabel('Line'))
                self._per_line_shift_line_box = QtWidgets.QSpinBox()
                self._per_line_shift_line_box.setRange(1, self._line_count)
                self._per_line_shift_line_box.setValue(1)
                self._per_line_shift_line_box.setAccelerated(True)
                self._per_line_shift_line_box.valueChanged.connect(self._per_line_shift_line_changed)
                per_line_shift_layout.addWidget(self._per_line_shift_line_box)
                per_line_shift_layout.addWidget(QtWidgets.QLabel('Shift'))
                self._per_line_shift_value_box = QtWidgets.QDoubleSpinBox()
                self._per_line_shift_value_box.setRange(-256.0, 256.0)
                self._per_line_shift_value_box.setDecimals(2)
                self._per_line_shift_value_box.setSingleStep(0.1)
                self._per_line_shift_value_box.setValue(0.0)
                self._per_line_shift_value_box.setAccelerated(True)
                self._per_line_shift_value_box.valueChanged.connect(self._per_line_shift_value_changed)
                per_line_shift_layout.addWidget(self._per_line_shift_value_box)
                per_line_shift_layout.addStretch(1)
                decoder_form.addWidget(self._per_line_shift_editor, 10, 1, 1, 3)
                self._per_line_shift_summary = QtWidgets.QLabel('none')
                self._per_line_shift_summary.setStyleSheet('color: #666;')
                decoder_form.addWidget(self._per_line_shift_summary, 11, 1, 1, 3)
                self._register_function_row(
                    'per_line_shift',
                    per_line_shift_label,
                    (
                        self._per_line_shift_editor,
                        self._per_line_shift_line_box,
                        self._per_line_shift_value_box,
                        self._per_line_shift_summary,
                    ),
                    self._reset_per_line_shift,
                )
                self._refresh_per_line_shift_editor()
                self._refresh_per_line_shift_summary()

                diagnostics_group = QtWidgets.QGroupBox()
                diagnostics_form = QtWidgets.QGridLayout(diagnostics_group)
                diagnostics_form.setHorizontalSpacing(10)
                diagnostics_form.setVerticalSpacing(8)
                self._add_section('Diagnostics', diagnostics_group, expanded=False, reset_callback=self._reset_diagnostics)

                show_quality_label = QtWidgets.QLabel('Show Quality')
                diagnostics_form.addWidget(show_quality_label, 0, 0)
                self._show_quality_box = QtWidgets.QCheckBox()
                self._show_quality_box.setChecked(bool(self._decoder_tuning.get('show_quality', False)))
                self._show_quality_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_quality_box, 0, 1)
                self._register_function_row(
                    'show_quality',
                    show_quality_label,
                    (self._show_quality_box,),
                    self._reset_show_quality,
                )

                show_rejects_label = QtWidgets.QLabel('Show Rejects')
                diagnostics_form.addWidget(show_rejects_label, 1, 0)
                self._show_rejects_box = QtWidgets.QCheckBox()
                self._show_rejects_box.setChecked(bool(self._decoder_tuning.get('show_rejects', False)))
                self._show_rejects_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_rejects_box, 1, 1)
                self._register_function_row(
                    'show_rejects',
                    show_rejects_label,
                    (self._show_rejects_box,),
                    self._reset_show_rejects,
                )

                show_start_clock_label = QtWidgets.QLabel('Show Start/Clock')
                diagnostics_form.addWidget(show_start_clock_label, 2, 0)
                self._show_start_clock_box = QtWidgets.QCheckBox()
                self._show_start_clock_box.setChecked(bool(self._decoder_tuning.get('show_start_clock', False)))
                self._show_start_clock_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_start_clock_box, 2, 1)
                self._register_function_row(
                    'show_start_clock',
                    show_start_clock_label,
                    (self._show_start_clock_box,),
                    self._reset_show_start_clock,
                )

                show_clock_visuals_label = QtWidgets.QLabel('Show Clock Visuals')
                diagnostics_form.addWidget(show_clock_visuals_label, 3, 0)
                self._show_clock_visuals_box = QtWidgets.QCheckBox()
                self._show_clock_visuals_box.setChecked(bool(self._decoder_tuning.get('show_clock_visuals', False)))
                self._show_clock_visuals_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_clock_visuals_box, 3, 1)
                self._register_function_row(
                    'show_clock_visuals',
                    show_clock_visuals_label,
                    (self._show_clock_visuals_box,),
                    self._reset_show_clock_visuals,
                )

                show_alignment_visuals_label = QtWidgets.QLabel('Show Alignment Visuals')
                diagnostics_form.addWidget(show_alignment_visuals_label, 4, 0)
                self._show_alignment_visuals_box = QtWidgets.QCheckBox()
                self._show_alignment_visuals_box.setChecked(bool(self._decoder_tuning.get('show_alignment_visuals', False)))
                self._show_alignment_visuals_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_alignment_visuals_box, 4, 1)
                self._register_function_row(
                    'show_alignment_visuals',
                    show_alignment_visuals_label,
                    (self._show_alignment_visuals_box,),
                    self._reset_show_alignment_visuals,
                )

                show_quality_meter_label = QtWidgets.QLabel('Quality Meter')
                diagnostics_form.addWidget(show_quality_meter_label, 5, 0)
                self._show_quality_meter_box = QtWidgets.QCheckBox()
                self._show_quality_meter_box.setChecked(bool(self._decoder_tuning.get('show_quality_meter', False)))
                self._show_quality_meter_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_quality_meter_box, 5, 1)
                self._register_function_row(
                    'show_quality_meter',
                    show_quality_meter_label,
                    (self._show_quality_meter_box,),
                    self._reset_show_quality_meter,
                )

                show_histogram_graph_label = QtWidgets.QLabel('Histogram / Black Level Graph')
                diagnostics_form.addWidget(show_histogram_graph_label, 6, 0)
                self._show_histogram_graph_box = QtWidgets.QCheckBox()
                self._show_histogram_graph_box.setChecked(bool(self._decoder_tuning.get('show_histogram_graph', False)))
                self._show_histogram_graph_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_histogram_graph_box, 6, 1)
                self._register_function_row(
                    'show_histogram_graph',
                    show_histogram_graph_label,
                    (self._show_histogram_graph_box,),
                    self._reset_show_histogram_graph,
                )

                show_eye_pattern_label = QtWidgets.QLabel('Eye Pattern / Clock Preview')
                diagnostics_form.addWidget(show_eye_pattern_label, 7, 0)
                self._show_eye_pattern_box = QtWidgets.QCheckBox()
                self._show_eye_pattern_box.setChecked(bool(self._decoder_tuning.get('show_eye_pattern', False)))
                self._show_eye_pattern_box.toggled.connect(self._controls_changed)
                diagnostics_form.addWidget(self._show_eye_pattern_box, 7, 1)
                self._register_function_row(
                    'show_eye_pattern',
                    show_eye_pattern_label,
                    (self._show_eye_pattern_box,),
                    self._reset_show_eye_pattern,
                )

            line_group = QtWidgets.QGroupBox()
            line_form = QtWidgets.QGridLayout(line_group)
            line_form.setHorizontalSpacing(8)
            line_form.setVerticalSpacing(6)
            self._add_section('Line Selection', line_group, expanded=False, reset_callback=self._reset_line_selection)

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

            fix_group = QtWidgets.QGroupBox()
            fix_form = QtWidgets.QGridLayout(fix_group)
            fix_form.setHorizontalSpacing(10)
            fix_form.setVerticalSpacing(8)
            self._add_section('Fix Capture Card', fix_group, expanded=False, reset_callback=self._reset_fix_capture_card)

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

            if self._show_timer:
                timer_group = QtWidgets.QGroupBox()
                timer_form = QtWidgets.QGridLayout(timer_group)
                timer_form.setHorizontalSpacing(10)
                timer_form.setVerticalSpacing(8)
                self._add_section('Record Timer', timer_group, expanded=False, reset_callback=self._reset_record_timer)

                timer_form.addWidget(QtWidgets.QLabel('Hours'), 0, 0)
                self._timer_hours_box = QtWidgets.QSpinBox()
                self._timer_hours_box.setRange(0, 99)
                self._timer_hours_box.setAccelerated(True)
                timer_form.addWidget(self._timer_hours_box, 0, 1)

                timer_form.addWidget(QtWidgets.QLabel('Minutes'), 0, 2)
                self._timer_minutes_box = QtWidgets.QSpinBox()
                self._timer_minutes_box.setRange(0, 59)
                self._timer_minutes_box.setAccelerated(True)
                timer_form.addWidget(self._timer_minutes_box, 0, 3)

                timer_form.addWidget(QtWidgets.QLabel('Seconds'), 0, 4)
                self._timer_seconds_box = QtWidgets.QSpinBox()
                self._timer_seconds_box.setRange(0, 59)
                self._timer_seconds_box.setAccelerated(True)
                timer_form.addWidget(self._timer_seconds_box, 0, 5)

                timer_hint = QtWidgets.QLabel('00:00:00 = no timer, recording runs until you stop it.')
                timer_hint.setStyleSheet('color: #666;')
                timer_form.addWidget(timer_hint, 1, 0, 1, 6)

                timer_hours, timer_minutes, timer_seconds_value = _split_timer_hms(self._default_timer_seconds)
                self._timer_hours_box.setValue(timer_hours)
                self._timer_minutes_box.setValue(timer_minutes)
                self._timer_seconds_box.setValue(timer_seconds_value)
            else:
                self._timer_hours_box = None
                self._timer_minutes_box = None
                self._timer_seconds_box = None

            tools_group = QtWidgets.QGroupBox()
            tools_layout = QtWidgets.QVBoxLayout(tools_group)
            tools_layout.setContentsMargins(0, 0, 0, 0)
            tools_layout.setSpacing(8)
            self._add_section('Tools', tools_group, expanded=False)

            auto_tune_button = QtWidgets.QPushButton('Auto Tune')
            auto_tune_button.setEnabled(self._analysis_available())
            auto_tune_button.setToolTip(
                'Analyse a recorded .vbi file and suggest signal/decoder tuning.'
                if self._analysis_available()
                else 'Auto Tune needs a recorded .vbi file.'
            )
            auto_tune_button.clicked.connect(lambda: self._run_analysis_dialog(ANALYSIS_KIND_AUTO_TUNE))
            tools_layout.addWidget(auto_tune_button)

            auto_lock_button = QtWidgets.QPushButton('Clock / Start Auto-Lock')
            auto_lock_button.setEnabled(self._analysis_available() and self._decoder_tuning_enabled)
            auto_lock_button.setToolTip(
                'Analyse the recorded .vbi file and suggest Clock Lock, Start Lock, and Line Start Range values.'
                if self._analysis_available()
                else 'Clock / Start Auto-Lock needs a recorded .vbi file.'
            )
            auto_lock_button.clicked.connect(lambda: self._run_analysis_dialog(ANALYSIS_KIND_CLOCK_START))
            tools_layout.addWidget(auto_lock_button)

            args_group = QtWidgets.QGroupBox()
            args_layout = QtWidgets.QVBoxLayout(args_group)
            args_layout.setContentsMargins(0, 0, 0, 0)
            args_layout.setSpacing(8)
            self._add_section('Args / Preset', args_group, expanded=False)

            args_row = QtWidgets.QHBoxLayout()
            args_row.addWidget(QtWidgets.QLabel('Args'))
            self._args_input = QtWidgets.QLineEdit()
            self._args_input.setPlaceholderText(format_tuning_args(
                DEFAULT_CONTROLS,
                self._default_decoder_tuning,
                self._default_line_selection,
                line_count=self._line_count,
                fix_capture_card=self._default_fix_capture_card,
                control_defaults=DEFAULT_CONTROLS,
                decoder_defaults=self._default_decoder_tuning,
            ))
            self._args_input.returnPressed.connect(self._apply_args_text)
            args_row.addWidget(self._args_input, 1)
            apply_args_button = QtWidgets.QPushButton('Apply Args')
            apply_args_button.clicked.connect(self._apply_args_text)
            args_row.addWidget(apply_args_button)
            clear_args_button = QtWidgets.QPushButton('Clear Args')
            clear_args_button.clicked.connect(self._clear_args)
            args_row.addWidget(clear_args_button)
            args_layout.addLayout(args_row)

            args_buttons_row = QtWidgets.QHBoxLayout()
            copy_button = QtWidgets.QPushButton('Copy Args')
            copy_button.clicked.connect(self._copy_args)
            args_buttons_row.addWidget(copy_button)

            paste_button = QtWidgets.QPushButton('Paste Args')
            paste_button.clicked.connect(self._paste_args)
            args_buttons_row.addWidget(paste_button)
            args_buttons_row.addStretch(1)
            args_layout.addLayout(args_buttons_row)

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
            args_layout.addLayout(local_presets_row)

            file_presets_row = QtWidgets.QHBoxLayout()
            load_preset_button = QtWidgets.QPushButton('Load Preset')
            load_preset_button.clicked.connect(self._load_preset)
            file_presets_row.addWidget(load_preset_button)

            save_preset_button = QtWidgets.QPushButton('Save Preset')
            save_preset_button.clicked.connect(self._save_preset)
            file_presets_row.addWidget(save_preset_button)
            file_presets_row.addStretch(1)
            args_layout.addLayout(file_presets_row)

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

            self._undo_button = QtWidgets.QPushButton('Undo')
            self._undo_button.clicked.connect(self._undo_history)
            buttons.addWidget(self._undo_button)

            self._redo_button = QtWidgets.QPushButton('Redo')
            self._redo_button.clicked.connect(self._redo_history)
            buttons.addWidget(self._redo_button)

            reset_button = QtWidgets.QPushButton('Reset')
            reset_button.clicked.connect(self._reset_controls)
            buttons.addWidget(reset_button)

            self._function_menu_button = QtWidgets.QToolButton()
            self._function_menu_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self._function_menu_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            self._function_menu_button.setToolTip('Unchecked items are hidden from the tuner UI and use defaults. Settings also support Enable All and Disable All. Presets remember this state.')
            buttons.addWidget(self._function_menu_button)

            if live:
                close_button = QtWidgets.QPushButton('Close')
                close_button.clicked.connect(self.close)
                buttons.addWidget(close_button)
            else:
                close_button = QtWidgets.QPushButton('Close')
                close_button.clicked.connect(self.reject)
                buttons.addWidget(close_button)

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

            self._function_menu_button.setMenu(self._build_function_menu())
            self._apply_visible_sections()
            self._refresh_local_presets()
            self._load_target_state_into_editor()
            self._controls_changed()
            self._history_commit_timer.stop()
            self._history_undo_stack = [self._capture_history_snapshot()]
            self._history_redo_stack = []
            self._update_history_buttons()

            self._undo_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence.Undo, self)
            self._undo_shortcut.activated.connect(self._undo_history)
            self._redo_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence.Redo, self)
            self._redo_shortcut.activated.connect(self._redo_history)
            self._redo_shortcut_alt = QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+Shift+Z'), self)
            self._redo_shortcut_alt.activated.connect(self._redo_history)

        def _is_function_disabled(self, key):
            return key in self._disabled_functions

        def _capture_history_snapshot(self):
            return {
                'controls': _normalise_signal_controls_tuple(self._global_controls),
                'decoder_tuning': _normalise_decoder_tuning(self._global_decoder_tuning),
                'line_control_overrides': normalise_line_control_overrides(
                    self._line_control_overrides,
                    line_count=self._line_count,
                ),
                'line_decoder_overrides': normalise_line_decoder_overrides(
                    self._line_decoder_overrides,
                    line_count=self._line_count,
                ),
                'line_selection': tuple(sorted(self.line_selection_values)),
                'fix_capture_card': normalise_fix_capture_card(self.fix_capture_card_values),
                'timer_seconds': self.timer_seconds_value,
                'edit_target_mode': self._edit_target_mode_box.currentData() if hasattr(self, '_edit_target_mode_box') else EDIT_TARGET_ALL,
                'edit_target_line': self._edit_target_line_box.value() if hasattr(self, '_edit_target_line_box') else 1,
            }

        def _update_history_buttons(self):
            if self._undo_button is not None:
                self._undo_button.setEnabled(len(self._history_undo_stack) > 1)
            if self._redo_button is not None:
                self._redo_button.setEnabled(bool(self._history_redo_stack))

        def _schedule_history_capture(self):
            if self._history_navigation_in_progress or self._bulk_change_depth > 0:
                return
            self._history_commit_timer.start()

        def _commit_history_snapshot(self):
            if self._history_navigation_in_progress or self._bulk_change_depth > 0:
                return
            snapshot = self._capture_history_snapshot()
            if self._history_undo_stack and snapshot == self._history_undo_stack[-1]:
                self._update_history_buttons()
                return
            self._history_undo_stack.append(snapshot)
            if len(self._history_undo_stack) > self._history_limit:
                self._history_undo_stack = self._history_undo_stack[-self._history_limit:]
            self._history_redo_stack.clear()
            self._update_history_buttons()

        def _begin_bulk_change(self):
            self._bulk_change_depth += 1
            if self._history_commit_timer.isActive():
                self._history_commit_timer.stop()

        def _end_bulk_change(self, record_history=True):
            self._bulk_change_depth = max(self._bulk_change_depth - 1, 0)
            if self._bulk_change_depth == 0:
                self._handle_controls_changed(skip_history=not record_history)

        def _set_timer_seconds_value(self, timer_seconds):
            if not self._show_timer or self._timer_hours_box is None:
                return
            hours, minutes, seconds = _split_timer_hms(timer_seconds)
            self._timer_hours_box.setValue(hours)
            self._timer_minutes_box.setValue(minutes)
            self._timer_seconds_box.setValue(seconds)

        def _restore_history_snapshot(self, snapshot):
            self._history_navigation_in_progress = True
            self._begin_bulk_change()
            try:
                self._apply_parsed_tuning(
                    snapshot['controls'],
                    snapshot.get('decoder_tuning'),
                    snapshot.get('line_selection'),
                    snapshot.get('fix_capture_card'),
                    line_control_overrides=snapshot.get('line_control_overrides'),
                    line_decoder_overrides=snapshot.get('line_decoder_overrides'),
                    edit_target_mode=snapshot.get('edit_target_mode', EDIT_TARGET_ALL),
                    edit_target_line=snapshot.get('edit_target_line', 1),
                )
                self._set_timer_seconds_value(snapshot.get('timer_seconds'))
            finally:
                self._end_bulk_change(record_history=False)
                self._history_navigation_in_progress = False
            self._update_history_buttons()

        def _undo_history(self):
            if self._history_commit_timer.isActive():
                self._history_commit_timer.stop()
                self._commit_history_snapshot()
            if len(self._history_undo_stack) <= 1:
                self._update_history_buttons()
                return
            current = self._history_undo_stack.pop()
            self._history_redo_stack.append(current)
            self._restore_history_snapshot(self._history_undo_stack[-1])

        def _redo_history(self):
            if self._history_commit_timer.isActive():
                self._history_commit_timer.stop()
                self._commit_history_snapshot()
            if not self._history_redo_stack:
                self._update_history_buttons()
                return
            snapshot = self._history_redo_stack.pop()
            self._history_undo_stack.append(snapshot)
            self._restore_history_snapshot(snapshot)

        @property
        def raw_values(self):
            return (
                self._sliders['brightness'].value(),
                self._sliders['sharpness'].value(),
                self._sliders['gain'].value(),
                self._sliders['contrast'].value(),
                self._coeff_boxes['brightness'].value(),
                self._coeff_boxes['sharpness'].value(),
                self._coeff_boxes['gain'].value(),
                self._coeff_boxes['contrast'].value(),
                self._cleanup_sliders['impulse_filter'].value(),
                self._cleanup_sliders['temporal_denoise'].value(),
                self._cleanup_sliders['noise_reduction'].value(),
                self._cleanup_sliders['hum_removal'].value(),
                self._cleanup_sliders['auto_black_level'].value(),
                self._cleanup_sliders['head_switching_mask'].value(),
                self._cleanup_sliders['line_stabilization'].value(),
                self._cleanup_sliders['auto_gain_contrast'].value(),
                self._cleanup_coeff_boxes['impulse_filter'].value(),
                self._cleanup_coeff_boxes['temporal_denoise'].value(),
                self._cleanup_coeff_boxes['noise_reduction'].value(),
                self._cleanup_coeff_boxes['hum_removal'].value(),
                self._cleanup_coeff_boxes['auto_black_level'].value(),
                self._cleanup_coeff_boxes['head_switching_mask'].value(),
                self._cleanup_coeff_boxes['line_stabilization'].value(),
                self._cleanup_coeff_boxes['auto_gain_contrast'].value(),
            )

        @property
        def values(self):
            values = list(_normalise_signal_controls_tuple(self._global_controls))
            for key in CONTROL_KEYS:
                if self._is_function_disabled(key):
                    value_index = CONTROL_INDEX[key]
                    coeff_index = CONTROL_INDEX[f'{key}_coeff']
                    values[value_index] = DEFAULT_CONTROLS[value_index]
                    values[coeff_index] = DEFAULT_CONTROLS[coeff_index]
            return tuple(values)

        @property
        def raw_decoder_tuning_values(self):
            if not self._decoder_tuning_enabled:
                return None
            return {
                'tape_format': self._tape_format_box.currentText(),
                'extra_roll': self._extra_roll_box.value(),
                'line_start_range': (
                    self._line_start_start_box.value(),
                    self._line_start_end_box.value(),
                ),
                'quality_threshold': self._quality_threshold_slider.value(),
                'quality_threshold_coeff': self._quality_threshold_coeff_box.value(),
                'clock_lock': self._clock_lock_slider.value(),
                'clock_lock_coeff': self._clock_lock_coeff_box.value(),
                'start_lock': self._start_lock_slider.value(),
                'start_lock_coeff': self._start_lock_coeff_box.value(),
                'adaptive_threshold': self._adaptive_threshold_slider.value(),
                'adaptive_threshold_coeff': self._adaptive_threshold_coeff_box.value(),
                'dropout_repair': self._dropout_repair_slider.value(),
                'dropout_repair_coeff': self._dropout_repair_coeff_box.value(),
                'wow_flutter_compensation': self._wow_flutter_compensation_slider.value(),
                'wow_flutter_compensation_coeff': self._wow_flutter_compensation_coeff_box.value(),
                'auto_line_align': self._auto_line_align_slider.value(),
                'per_line_shift': normalise_per_line_shift_map(self._per_line_shift_map, maximum_line=self._line_count),
                'show_quality': self._show_quality_box.isChecked(),
                'show_rejects': self._show_rejects_box.isChecked(),
                'show_start_clock': self._show_start_clock_box.isChecked(),
                'show_clock_visuals': self._show_clock_visuals_box.isChecked(),
                'show_alignment_visuals': self._show_alignment_visuals_box.isChecked(),
                'show_quality_meter': self._show_quality_meter_box.isChecked(),
                'show_histogram_graph': self._show_histogram_graph_box.isChecked(),
                'show_eye_pattern': self._show_eye_pattern_box.isChecked(),
            }

        @property
        def decoder_tuning_values(self):
            if not self._decoder_tuning_enabled:
                return None
            values = _normalise_decoder_tuning(self._global_decoder_tuning)
            defaults = self._default_decoder_tuning or _normalise_decoder_tuning({})
            if self._is_function_disabled('tape_format'):
                values['tape_format'] = defaults['tape_format']
            if self._is_function_disabled('extra_roll'):
                values['extra_roll'] = int(defaults['extra_roll'])
            if self._is_function_disabled('line_start_range'):
                values['line_start_range'] = tuple(defaults['line_start_range'])
            if self._is_function_disabled('quality_threshold'):
                values['quality_threshold'] = int(defaults['quality_threshold'])
                values['quality_threshold_coeff'] = float(defaults.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT))
            if self._is_function_disabled('clock_lock'):
                values['clock_lock'] = int(defaults.get('clock_lock', CLOCK_LOCK_DEFAULT))
                values['clock_lock_coeff'] = float(defaults.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT))
            if self._is_function_disabled('start_lock'):
                values['start_lock'] = int(defaults.get('start_lock', START_LOCK_DEFAULT))
                values['start_lock_coeff'] = float(defaults.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT))
            if self._is_function_disabled('adaptive_threshold'):
                values['adaptive_threshold'] = int(defaults.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT))
                values['adaptive_threshold_coeff'] = float(defaults.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT))
            if self._is_function_disabled('dropout_repair'):
                values['dropout_repair'] = int(defaults.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
                values['dropout_repair_coeff'] = float(defaults.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT))
            if self._is_function_disabled('wow_flutter_compensation'):
                values['wow_flutter_compensation'] = int(defaults.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
                values['wow_flutter_compensation_coeff'] = float(defaults.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT))
            if self._is_function_disabled('auto_line_align'):
                values['auto_line_align'] = int(defaults.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))
            if self._is_function_disabled('per_line_shift'):
                values['per_line_shift'] = normalise_per_line_shift_map(
                    defaults.get('per_line_shift', {}),
                    maximum_line=self._line_count,
                )
            if self._is_function_disabled('show_quality'):
                values['show_quality'] = bool(defaults.get('show_quality', False))
            if self._is_function_disabled('show_rejects'):
                values['show_rejects'] = bool(defaults.get('show_rejects', False))
            if self._is_function_disabled('show_start_clock'):
                values['show_start_clock'] = bool(defaults.get('show_start_clock', False))
            if self._is_function_disabled('show_clock_visuals'):
                values['show_clock_visuals'] = bool(defaults.get('show_clock_visuals', False))
            if self._is_function_disabled('show_alignment_visuals'):
                values['show_alignment_visuals'] = bool(defaults.get('show_alignment_visuals', False))
            if self._is_function_disabled('show_quality_meter'):
                values['show_quality_meter'] = bool(defaults.get('show_quality_meter', False))
            if self._is_function_disabled('show_histogram_graph'):
                values['show_histogram_graph'] = bool(defaults.get('show_histogram_graph', False))
            if self._is_function_disabled('show_eye_pattern'):
                values['show_eye_pattern'] = bool(defaults.get('show_eye_pattern', False))
            values['line_control_overrides'] = normalise_line_control_overrides(
                self._line_control_overrides,
                line_count=self._line_count,
            )
            values['line_decoder_overrides'] = normalise_line_decoder_overrides(
                self._line_decoder_overrides,
                line_count=self._line_count,
            )
            return values

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

        @property
        def timer_seconds_value(self):
            if not self._show_timer or self._timer_hours_box is None:
                return None
            total_seconds = (
                (int(self._timer_hours_box.value()) * 3600)
                + (int(self._timer_minutes_box.value()) * 60)
                + int(self._timer_seconds_box.value())
            )
            return int(total_seconds) if total_seconds > 0 else None

        def _register_function_row(self, key, label, widgets, reset_callback):
            widgets = tuple(widgets)
            self._function_rows[key] = {
                'label': label,
                'widgets': widgets,
                'reset': reset_callback,
                'title': FUNCTION_METADATA[key]['title'],
                'section': FUNCTION_METADATA[key]['section'],
            }
            for widget in (label,) + widgets:
                widget.installEventFilter(self)
                self._ctrl_reset_targets[widget] = key
                existing_tooltip = widget.toolTip().strip()
                reset_tooltip = 'Ctrl + Left Click resets this function.'
                widget.setToolTip(f'{existing_tooltip}\n{reset_tooltip}'.strip())
            self._set_function_row_enabled(key, True)

        def _build_function_menu(self):
            menu = QtWidgets.QMenu(self)
            enable_all_action = menu.addAction('Enable All Functions')
            enable_all_action.triggered.connect(self._enable_all_functions)
            disable_all_action = menu.addAction('Disable All Functions')
            disable_all_action.triggered.connect(self._disable_all_functions)
            menu.addSeparator()
            for section in FUNCTION_MENU_SECTIONS:
                if self._visible_sections is not None and section not in self._visible_sections:
                    continue
                section_menu = menu.addMenu(section)
                for key, metadata in FUNCTION_METADATA.items():
                    if metadata['section'] != section or key not in self._function_rows:
                        continue
                    action = section_menu.addAction(metadata['title'])
                    action.setCheckable(True)
                    action.setChecked(key not in self._disabled_functions)
                    action.toggled.connect(lambda checked, function_key=key: self._set_function_enabled(function_key, checked))
                    self._function_actions[key] = action
            self._update_function_menu_button()
            return menu

        def _update_function_menu_button(self):
            if self._function_menu_button is None:
                return
            disabled_count = len(self._disabled_functions)
            if disabled_count:
                self._function_menu_button.setText(f'Settings ({disabled_count} hidden)')
            else:
                self._function_menu_button.setText('Settings')

        def _set_function_row_enabled(self, key, enabled):
            row = self._function_rows.get(key)
            if row is None:
                return
            row['label'].setVisible(bool(enabled))
            for widget in row['widgets']:
                widget.setEnabled(bool(enabled))
                widget.setVisible(bool(enabled))
            label = row['label']
            font = QtGui.QFont(label.font())
            font.setStrikeOut(not enabled)
            label.setFont(font)
            label.setStyleSheet('color: #808080;' if not enabled else '')

        def _set_function_enabled(self, key, enabled):
            action = self._function_actions.get(key)
            if enabled:
                self._disabled_functions.discard(key)
            else:
                self._disabled_functions.add(key)
            if action is not None and action.isChecked() != bool(enabled):
                action.blockSignals(True)
                action.setChecked(bool(enabled))
                action.blockSignals(False)
            self._set_function_row_enabled(key, enabled)
            self._update_function_menu_button()
            self._handle_controls_changed(skip_history=True)

        def _apply_disabled_functions(self, disabled_functions):
            disabled = set(normalise_disabled_functions(disabled_functions))
            self._disabled_functions = disabled
            for key in self._function_rows:
                enabled = key not in self._disabled_functions
                action = self._function_actions.get(key)
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(enabled)
                    action.blockSignals(False)
                self._set_function_row_enabled(key, enabled)
            self._update_function_menu_button()
            self._handle_controls_changed(skip_history=True)

        def _enable_all_functions(self):
            self._begin_bulk_change()
            try:
                self._apply_disabled_functions(())
            finally:
                self._end_bulk_change(record_history=True)

        def _disable_all_functions(self):
            self._begin_bulk_change()
            try:
                self._apply_disabled_functions(tuple(self._function_rows))
            finally:
                self._end_bulk_change(record_history=True)

        def _preset_payload(self):
            return {
                'args': format_tuning_args(
                    self.values,
                    self.decoder_tuning_values,
                    self.line_selection_values,
                    line_count=self._line_count,
                    fix_capture_card=self.fix_capture_card_values,
                    control_defaults=DEFAULT_CONTROLS,
                    decoder_defaults=self._default_decoder_tuning,
                ),
                'disabled_functions': normalise_disabled_functions(self._disabled_functions),
                'line_control_overrides': normalise_line_control_overrides(
                    self._line_control_overrides,
                    line_count=self._line_count,
                ),
                'line_decoder_overrides': normalise_line_decoder_overrides(
                    self._line_decoder_overrides,
                    line_count=self._line_count,
                ),
                'edit_target_line': self._current_edit_target_line(),
            }

        def set_change_callback(self, callback):
            self._external_change_callback = callback

        def _add_section(self, title, widget, expanded=True, reset_callback=None):
            header_widget = QtWidgets.QWidget()
            header = QtWidgets.QHBoxLayout(header_widget)
            header.setContentsMargins(0, 0, 0, 0)
            button = QtWidgets.QToolButton()
            button.setText(_section_display_title(title))
            button.setCheckable(True)
            button.setChecked(bool(expanded))
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            button.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
            button.setStyleSheet('QToolButton { font-weight: bold; }')
            tooltip = _section_tooltip(title)
            if tooltip:
                button.setToolTip(tooltip)
                header_widget.setToolTip(tooltip)
                widget.setToolTip(tooltip)
            button.toggled.connect(lambda checked, name=title: self._set_section_visible(name, checked))
            header.addWidget(button)
            header.addStretch(1)
            if reset_callback is not None:
                reset_button = QtWidgets.QPushButton('Reset')
                reset_button.clicked.connect(reset_callback)
                header.addWidget(reset_button)
            self._root_layout.addWidget(header_widget)
            self._root_layout.addWidget(widget)
            self._section_buttons[title] = button
            self._section_widgets[title] = widget
            self._section_headers[title] = header_widget
            widget.setVisible(bool(expanded))

        def _apply_visible_sections(self):
            if self._visible_sections is None:
                return
            for title, widget in self._section_widgets.items():
                visible = title in self._visible_sections
                self._section_headers[title].setVisible(bool(visible))
                widget.setVisible(bool(visible and self._section_buttons[title].isChecked()))
            if not self._show_preview:
                self.adjustSize()
            else:
                QtCore.QTimer.singleShot(0, self.adjustSize)

        def _set_section_visible(self, title, visible):
            button = self._section_buttons[title]
            widget = self._section_widgets[title]
            button.setArrowType(QtCore.Qt.DownArrow if visible else QtCore.Qt.RightArrow)
            widget.setVisible(bool(visible))
            if not self._show_preview:
                self.adjustSize()
            else:
                QtCore.QTimer.singleShot(0, self.adjustSize)

        def _current_edit_target_line(self):
            if not hasattr(self, '_edit_target_mode_box'):
                return None
            if self._edit_target_mode_box.currentData() != EDIT_TARGET_SINGLE:
                return None
            return int(self._edit_target_line_box.value())

        def _update_target_sensitive_widgets(self):
            target_line = self._current_edit_target_line()
            selected_line_mode = target_line is not None
            if hasattr(self, '_per_line_shift_line_box'):
                if selected_line_mode:
                    self._per_line_shift_line_box.blockSignals(True)
                    self._per_line_shift_line_box.setValue(target_line)
                    self._per_line_shift_line_box.blockSignals(False)
                self._per_line_shift_line_box.setEnabled(not selected_line_mode)
            if hasattr(self, '_tape_format_box'):
                self._tape_format_box.setEnabled(not selected_line_mode)
            if hasattr(self, '_edit_target_status'):
                if selected_line_mode:
                    self._edit_target_status.setText(f'Editing line {target_line} overrides')
                else:
                    override_count = len(self._line_control_overrides) + len(self._line_decoder_overrides)
                    suffix = f' ({override_count} override blocks)' if override_count else ''
                    self._edit_target_status.setText(f'Editing all lines{suffix}')

        def _refresh_line_checkbox_styles(self):
            target_line = self._current_edit_target_line()
            override_lines = set(self._line_control_overrides) | set(self._line_decoder_overrides)
            for line, checkbox in getattr(self, '_line_checkboxes', {}).items():
                style = ''
                if line == target_line:
                    style = 'QCheckBox { background: #46360f; border: 1px solid #d8b94a; padding: 2px; border-radius: 3px; }'
                elif line in override_lines:
                    style = 'QCheckBox { background: #1e2d4a; border: 1px solid #5d85d6; padding: 2px; border-radius: 3px; }'
                checkbox.setStyleSheet(style)

        def _load_target_state_into_editor(self):
            target_line = self._current_edit_target_line()
            values = _effective_control_values_for_line(self._global_controls, self._line_control_overrides, target_line)
            decoder_tuning = _effective_decoder_tuning_for_line(
                self._global_decoder_tuning,
                self._line_decoder_overrides,
                target_line,
            )
            self._begin_bulk_change()
            try:
                self._set_control_values(values)
                self._set_decoder_tuning_values(decoder_tuning)
            finally:
                self._end_bulk_change(record_history=False)
            self._update_target_sensitive_widgets()
            self._refresh_line_checkbox_styles()

        def _sync_editor_state_to_model(self):
            target_line = self._current_edit_target_line()
            editor_controls = _normalise_signal_controls_tuple(self.raw_values)
            if target_line is None:
                self._global_controls = editor_controls
                self._line_control_overrides = {
                    int(line): tuple(values)
                    for line, values in self._line_control_overrides.items()
                    if tuple(values) != tuple(self._global_controls)
                }
            else:
                if tuple(editor_controls) == tuple(self._global_controls):
                    self._line_control_overrides.pop(int(target_line), None)
                else:
                    self._line_control_overrides[int(target_line)] = tuple(editor_controls)

            if self._decoder_tuning_enabled:
                editor_decoder = _normalise_decoder_tuning(self.raw_decoder_tuning_values)
                if target_line is None:
                    self._global_decoder_tuning = dict(editor_decoder)
                    self._global_decoder_tuning['line_control_overrides'] = normalise_line_control_overrides(
                        self._line_control_overrides,
                        line_count=self._line_count,
                    )
                    self._global_decoder_tuning['line_decoder_overrides'] = normalise_line_decoder_overrides(
                        self._line_decoder_overrides,
                        line_count=self._line_count,
                    )
                else:
                    if self._global_decoder_tuning is None:
                        self._global_decoder_tuning = _normalise_decoder_tuning(editor_decoder)
                    override = {
                        key: editor_decoder[key]
                        for key in PER_LINE_DECODER_OVERRIDE_KEYS
                    }
                    global_reference = {
                        key: self._global_decoder_tuning[key]
                        for key in PER_LINE_DECODER_OVERRIDE_KEYS
                    }
                    if override == global_reference:
                        self._line_decoder_overrides.pop(int(target_line), None)
                    else:
                        self._line_decoder_overrides[int(target_line)] = dict(override)
                    self._global_decoder_tuning['line_control_overrides'] = normalise_line_control_overrides(
                        self._line_control_overrides,
                        line_count=self._line_count,
                    )
                    self._global_decoder_tuning['line_decoder_overrides'] = normalise_line_decoder_overrides(
                        self._line_decoder_overrides,
                        line_count=self._line_count,
                    )

            self._refresh_line_checkbox_styles()
            self._update_target_sensitive_widgets()

        def _edit_target_changed(self):
            if self._bulk_change_depth > 0:
                self._update_target_sensitive_widgets()
                self._refresh_line_checkbox_styles()
                return
            self._sync_editor_state_to_model()
            self._load_target_state_into_editor()
            self._handle_controls_changed()

        def _enable_all_lines(self):
            self._begin_bulk_change()
            try:
                self._set_line_selection_values(range(1, self._line_count + 1))
            finally:
                self._end_bulk_change(record_history=True)

        def _disable_all_lines(self):
            self._begin_bulk_change()
            try:
                self._set_line_selection_values(())
            finally:
                self._end_bulk_change(record_history=True)

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

        def _refresh_per_line_shift_editor(self):
            if not hasattr(self, '_per_line_shift_line_box'):
                return
            selected_line = int(self._per_line_shift_line_box.value())
            shift = float(self._per_line_shift_map.get(selected_line, 0.0))
            self._per_line_shift_value_box.blockSignals(True)
            self._per_line_shift_value_box.setValue(shift)
            self._per_line_shift_value_box.blockSignals(False)

        def _refresh_per_line_shift_summary(self):
            if not hasattr(self, '_per_line_shift_summary'):
                return
            items = serialise_per_line_shift_map(self._per_line_shift_map, maximum_line=self._line_count)
            if not items:
                self._per_line_shift_summary.setText('none')
                return
            preview = ', '.join(
                f"{line}:{(f'{shift:+.2f}'.rstrip('0').rstrip('.') if abs(float(shift) - round(float(shift))) > 1e-9 else f'{int(round(float(shift))):+d}')}"
                for line, shift in items[:6]
            )
            if len(items) > 6:
                preview += f' ... ({len(items)})'
            self._per_line_shift_summary.setText(preview)

        def _per_line_shift_line_changed(self):
            self._refresh_per_line_shift_editor()

        def _per_line_shift_value_changed(self):
            line = int(self._per_line_shift_line_box.value())
            shift = float(self._per_line_shift_value_box.value())
            if abs(shift) <= 1e-9:
                self._per_line_shift_map.pop(line, None)
            else:
                self._per_line_shift_map[line] = shift
            self._refresh_per_line_shift_summary()
            self._controls_changed()

        def _reset_signal_control(self, key):
            value_index, coeff_index = {
                'brightness': (0, 4),
                'sharpness': (1, 5),
                'gain': (2, 6),
                'contrast': (3, 7),
            }[key]
            self._sliders[key].setValue(int(DEFAULT_CONTROLS[value_index]))
            self._coeff_boxes[key].setValue(float(DEFAULT_CONTROLS[coeff_index]))

        def _reset_cleanup_control(self, key):
            value_index, coeff_index = {
                'impulse_filter': (8, 16),
                'temporal_denoise': (9, 17),
                'noise_reduction': (10, 18),
                'hum_removal': (11, 19),
                'auto_black_level': (12, 20),
                'head_switching_mask': (13, 21),
                'line_stabilization': (14, 22),
                'auto_gain_contrast': (15, 23),
            }[key]
            self._cleanup_sliders[key].setValue(int(DEFAULT_CONTROLS[value_index]))
            self._cleanup_coeff_boxes[key].setValue(float(DEFAULT_CONTROLS[coeff_index]))

        def _reset_tape_format(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            tape_format = self._default_decoder_tuning['tape_format']
            if tape_format in self._tape_formats:
                self._tape_format_box.setCurrentIndex(self._tape_formats.index(tape_format))

        def _reset_extra_roll(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._extra_roll_box.setValue(int(self._default_decoder_tuning['extra_roll']))

        def _reset_line_start_range(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._line_start_start_box.setValue(int(self._default_decoder_tuning['line_start_range'][0]))
            self._line_start_end_box.setValue(int(self._default_decoder_tuning['line_start_range'][1]))

        def _reset_quality_threshold(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._quality_threshold_slider.setValue(int(self._default_decoder_tuning['quality_threshold']))
            self._quality_threshold_coeff_box.setValue(float(self._default_decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)))

        def _reset_clock_lock(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._clock_lock_slider.setValue(int(self._default_decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)))
            self._clock_lock_coeff_box.setValue(float(self._default_decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)))

        def _reset_start_lock(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._start_lock_slider.setValue(int(self._default_decoder_tuning.get('start_lock', START_LOCK_DEFAULT)))
            self._start_lock_coeff_box.setValue(float(self._default_decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)))

        def _reset_adaptive_threshold(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._adaptive_threshold_slider.setValue(int(self._default_decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT)))
            self._adaptive_threshold_coeff_box.setValue(float(self._default_decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)))

        def _reset_dropout_repair(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._dropout_repair_slider.setValue(int(self._default_decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)))
            self._dropout_repair_coeff_box.setValue(float(self._default_decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)))

        def _reset_wow_flutter_compensation(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._wow_flutter_compensation_slider.setValue(int(self._default_decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)))
            self._wow_flutter_compensation_coeff_box.setValue(float(self._default_decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)))

        def _reset_auto_line_align(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._auto_line_align_slider.setValue(int(self._default_decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)))

        def _reset_per_line_shift(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._per_line_shift_map = normalise_per_line_shift_map(
                self._default_decoder_tuning.get('per_line_shift', {}),
                maximum_line=self._line_count,
            )
            self._refresh_per_line_shift_editor()
            self._refresh_per_line_shift_summary()
            self._controls_changed()

        def _reset_show_quality(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_quality_box.setChecked(bool(self._default_decoder_tuning.get('show_quality', False)))

        def _reset_show_rejects(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_rejects_box.setChecked(bool(self._default_decoder_tuning.get('show_rejects', False)))

        def _reset_show_start_clock(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_start_clock_box.setChecked(bool(self._default_decoder_tuning.get('show_start_clock', False)))

        def _reset_show_clock_visuals(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_clock_visuals_box.setChecked(bool(self._default_decoder_tuning.get('show_clock_visuals', False)))

        def _reset_show_alignment_visuals(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_alignment_visuals_box.setChecked(bool(self._default_decoder_tuning.get('show_alignment_visuals', False)))

        def _reset_show_quality_meter(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_quality_meter_box.setChecked(bool(self._default_decoder_tuning.get('show_quality_meter', False)))

        def _reset_show_histogram_graph(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_histogram_graph_box.setChecked(bool(self._default_decoder_tuning.get('show_histogram_graph', False)))

        def _reset_show_eye_pattern(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._show_eye_pattern_box.setChecked(bool(self._default_decoder_tuning.get('show_eye_pattern', False)))

        def _handle_controls_changed(self, skip_history=False):
            self._sync_editor_state_to_model()
            self._set_args_text(format_tuning_args(
                self.values,
                self.decoder_tuning_values,
                self.line_selection_values,
                line_count=self._line_count,
                fix_capture_card=self.fix_capture_card_values,
                control_defaults=DEFAULT_CONTROLS,
                decoder_defaults=self._default_decoder_tuning,
            ))
            if not skip_history:
                self._schedule_history_capture()
            if self._external_change_callback is not None:
                self._external_change_callback(
                    self.values,
                    self.decoder_tuning_values,
                    self.line_selection_values,
                    self.fix_capture_card_values,
                )
            if self._show_preview and self.isVisible():
                self.refresh_preview()

        def _controls_changed(self):
            if self._bulk_change_depth > 0:
                return
            self._handle_controls_changed()

        def eventFilter(self, watched, event):  # pragma: no cover - GUI path
            if (
                watched in self._ctrl_reset_targets
                and event.type() == QtCore.QEvent.MouseButtonPress
                and event.button() == QtCore.Qt.LeftButton
                and bool(event.modifiers() & QtCore.Qt.ControlModifier)
            ):
                function_key = self._ctrl_reset_targets[watched]
                row = self._function_rows.get(function_key)
                if row is not None and row['reset'] is not None:
                    self._begin_bulk_change()
                    try:
                        row['reset']()
                    finally:
                        self._end_bulk_change(record_history=True)
                    return True
            return super().eventFilter(watched, event)

        def _reset_signal_controls(self):
            target_line = self._current_edit_target_line()
            source = self._global_controls if target_line is not None else self._default_global_controls
            self._begin_bulk_change()
            try:
                for key in ('brightness', 'sharpness', 'gain', 'contrast'):
                    self._sliders[key].setValue(int(source[CONTROL_INDEX[key]]))
                    self._coeff_boxes[key].setValue(float(source[CONTROL_INDEX[f'{key}_coeff']]))
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_signal_cleanup(self):
            target_line = self._current_edit_target_line()
            source = self._global_controls if target_line is not None else self._default_global_controls
            self._begin_bulk_change()
            try:
                for key in (
                    'impulse_filter',
                    'temporal_denoise',
                    'noise_reduction',
                    'hum_removal',
                    'auto_black_level',
                    'head_switching_mask',
                    'line_stabilization',
                    'auto_gain_contrast',
                ):
                    self._cleanup_sliders[key].setValue(int(source[CONTROL_INDEX[key]]))
                    self._cleanup_coeff_boxes[key].setValue(float(source[CONTROL_INDEX[f'{key}_coeff']]))
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_decoder_tuning(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            target_line = self._current_edit_target_line()
            source = self._global_decoder_tuning if target_line is not None else self._default_decoder_tuning
            self._begin_bulk_change()
            try:
                tape_format = source['tape_format']
                if target_line is None and tape_format in self._tape_formats:
                    self._tape_format_box.setCurrentIndex(self._tape_formats.index(tape_format))
                self._extra_roll_box.setValue(int(source['extra_roll']))
                self._line_start_start_box.setValue(int(source['line_start_range'][0]))
                self._line_start_end_box.setValue(int(source['line_start_range'][1]))
                self._quality_threshold_slider.setValue(int(source['quality_threshold']))
                self._quality_threshold_coeff_box.setValue(float(source.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)))
                self._clock_lock_slider.setValue(int(source.get('clock_lock', CLOCK_LOCK_DEFAULT)))
                self._clock_lock_coeff_box.setValue(float(source.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)))
                self._start_lock_slider.setValue(int(source.get('start_lock', START_LOCK_DEFAULT)))
                self._start_lock_coeff_box.setValue(float(source.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)))
                self._adaptive_threshold_slider.setValue(int(source['adaptive_threshold']))
                self._adaptive_threshold_coeff_box.setValue(float(source.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)))
                self._dropout_repair_slider.setValue(int(source.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)))
                self._dropout_repair_coeff_box.setValue(float(source.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)))
                self._wow_flutter_compensation_slider.setValue(int(source.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)))
                self._wow_flutter_compensation_coeff_box.setValue(float(source.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)))
                self._auto_line_align_slider.setValue(int(source.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)))
                self._per_line_shift_map = normalise_per_line_shift_map(
                    source.get('per_line_shift', {}),
                    maximum_line=self._line_count,
                )
                self._refresh_per_line_shift_editor()
                self._refresh_per_line_shift_summary()
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_diagnostics(self):
            if not self._decoder_tuning_enabled or self._default_decoder_tuning is None:
                return
            self._begin_bulk_change()
            try:
                self._show_quality_box.setChecked(bool(self._default_decoder_tuning.get('show_quality', False)))
                self._show_rejects_box.setChecked(bool(self._default_decoder_tuning.get('show_rejects', False)))
                self._show_start_clock_box.setChecked(bool(self._default_decoder_tuning.get('show_start_clock', False)))
                self._show_clock_visuals_box.setChecked(bool(self._default_decoder_tuning.get('show_clock_visuals', False)))
                self._show_alignment_visuals_box.setChecked(bool(self._default_decoder_tuning.get('show_alignment_visuals', False)))
                self._show_quality_meter_box.setChecked(bool(self._default_decoder_tuning.get('show_quality_meter', False)))
                self._show_histogram_graph_box.setChecked(bool(self._default_decoder_tuning.get('show_histogram_graph', False)))
                self._show_eye_pattern_box.setChecked(bool(self._default_decoder_tuning.get('show_eye_pattern', False)))
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_line_selection(self):
            self._begin_bulk_change()
            try:
                self._set_line_selection_values(self._default_line_selection)
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_fix_capture_card(self):
            self._begin_bulk_change()
            try:
                self._set_fix_capture_card_values(self._default_fix_capture_card)
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_record_timer(self):
            if not self._show_timer or self._timer_hours_box is None:
                return
            self._begin_bulk_change()
            try:
                hours, minutes, seconds = _split_timer_hms(self._default_timer_seconds)
                self._timer_hours_box.setValue(hours)
                self._timer_minutes_box.setValue(minutes)
                self._timer_seconds_box.setValue(seconds)
            finally:
                self._end_bulk_change(record_history=True)

        def _reset_controls(self):
            self._begin_bulk_change()
            try:
                self._reset_signal_controls()
                self._reset_signal_cleanup()
                self._reset_decoder_tuning()
                self._reset_diagnostics()
                self._reset_line_selection()
                self._reset_fix_capture_card()
                self._reset_record_timer()
            finally:
                self._end_bulk_change(record_history=True)

        def _copy_args(self):
            QtWidgets.QApplication.clipboard().setText(self._args_input.text())

        def _refresh_local_presets(self, selected_name=None):
            current_name = selected_name or self._local_preset_box.currentText()
            try:
                self._local_presets = load_local_preset_entries(self._local_preset_store_path)
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
                payload = self._local_presets[name]
            except (KeyError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._apply_preset_payload(payload)

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
                payload = self._preset_payload()
                saved_name = save_local_preset(
                    name,
                    payload['args'],
                    path=self._local_preset_store_path,
                    disabled_functions=payload['disabled_functions'],
                    line_control_overrides=payload['line_control_overrides'],
                    line_decoder_overrides=payload['line_decoder_overrides'],
                    edit_target_line=payload['edit_target_line'],
                )
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
                payload = load_preset_payload(path)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._last_preset_path = path
            self._apply_preset_payload(payload)

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
                payload = self._preset_payload()
                save_preset_text(
                    path,
                    payload['args'],
                    disabled_functions=payload['disabled_functions'],
                    line_control_overrides=payload['line_control_overrides'],
                    line_decoder_overrides=payload['line_decoder_overrides'],
                    edit_target_line=payload['edit_target_line'],
                )
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._last_preset_path = path

        def _analysis_available(self):
            return (
                self._analysis_source is not None
                and self._config is not None
                and bool(self._analysis_source.get('input_path'))
            )

        def _run_analysis_dialog(self, analysis_kind):
            if not self._analysis_available():
                QtWidgets.QMessageBox.information(self, 'VBI Tune', 'This helper needs a recorded .vbi file.')
                return
            if analysis_kind == ANALYSIS_KIND_CLOCK_START and not self._decoder_tuning_enabled:
                QtWidgets.QMessageBox.information(self, 'VBI Tune', 'Clock / Start Auto-Lock needs decoder tuning.')
                return

            if self._preview_timer is not None:
                self._preview_timer.stop()
            try:
                dialog = VBIAnalysisDialog(
                    analysis_kind,
                    input_path=self._analysis_source['input_path'],
                    config=self._config,
                    tape_format=self.decoder_tuning_values['tape_format'] if self._decoder_tuning_enabled else self._tape_format,
                    controls=self.values,
                    decoder_tuning=self.decoder_tuning_values,
                    line_selection=self.line_selection_values,
                    n_lines=self._analysis_source.get('n_lines'),
                    parent=self,
                )
                if _run_dialog_window(dialog) != QtWidgets.QDialog.Accepted:
                    return
                result = dialog.analysis_result
                if result is None:
                    return
                self._begin_bulk_change()
                try:
                    if analysis_kind == ANALYSIS_KIND_AUTO_TUNE:
                        for key in ('impulse_filter', 'temporal_denoise', 'noise_reduction', 'hum_removal', 'auto_black_level', 'clock_lock', 'start_lock', 'adaptive_threshold', 'quality_threshold'):
                            self._set_function_enabled(key, True)
                    elif analysis_kind == ANALYSIS_KIND_CLOCK_START:
                        self._set_function_enabled('line_start_range', True)
                        self._set_function_enabled('clock_lock', True)
                        self._set_function_enabled('start_lock', True)
                    self._apply_parsed_tuning(
                        result['controls'],
                        result['decoder_tuning'],
                        self.line_selection_values,
                        self.fix_capture_card_values,
                    )
                finally:
                    self._end_bulk_change(record_history=True)
                self._info_label.setText(result['summary'])
            finally:
                if self._preview_timer is not None:
                    self._preview_timer.start()
                if self._show_preview:
                    self.refresh_preview()

        def _paste_args(self):
            self._apply_preset_payload({
                'args': QtWidgets.QApplication.clipboard().text(),
                'disabled_functions': self._disabled_functions,
            })

        def _clear_args(self):
            self._begin_bulk_change()
            try:
                self._args_input.clear()
                self._reset_controls()
            finally:
                self._end_bulk_change(record_history=True)

        def _apply_args_text(self):
            text = self._args_input.text()
            try:
                values, decoder_tuning, line_selection, fix_capture_card = parse_tuning_args(
                    text,
                    defaults=self.raw_values,
                    decoder_defaults=self.raw_decoder_tuning_values,
                    tape_formats=self._tape_formats,
                    line_defaults=self.line_selection_values,
                    line_count=self._line_count,
                    fix_capture_card_defaults=self.fix_capture_card_values,
                )
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._apply_parsed_tuning(values, decoder_tuning, line_selection, fix_capture_card)

        def _apply_preset_payload(self, payload):
            payload = _normalise_preset_payload(payload)
            self._args_input.setText(payload['args'])
            try:
                values, decoder_tuning, line_selection, fix_capture_card = parse_tuning_args(
                    payload['args'],
                    defaults=self.raw_values,
                    decoder_defaults=self.raw_decoder_tuning_values,
                    tape_formats=self._tape_formats,
                    line_defaults=self.line_selection_values,
                    line_count=self._line_count,
                    fix_capture_card_defaults=self.fix_capture_card_values,
                )
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, 'VBI Tune', str(exc))
                return
            self._begin_bulk_change()
            try:
                self._apply_parsed_tuning(
                    values,
                    decoder_tuning,
                    line_selection,
                    fix_capture_card,
                    line_control_overrides=payload['line_control_overrides'],
                    line_decoder_overrides=payload['line_decoder_overrides'],
                    edit_target_mode=EDIT_TARGET_SINGLE if payload['edit_target_line'] is not None else EDIT_TARGET_ALL,
                    edit_target_line=payload['edit_target_line'] or self._edit_target_line_box.value(),
                )
                self._apply_disabled_functions(payload['disabled_functions'])
            finally:
                self._end_bulk_change(record_history=True)

        def _set_args_text(self, text):
            self._updating_args_text = True
            self._args_input.setText(text)
            self._updating_args_text = False

        def _set_control_values(self, values):
            values = _normalise_signal_controls_tuple(values)
            for key, value in zip(('brightness', 'sharpness', 'gain', 'contrast'), values[:4]):
                self._sliders[key].setValue(int(value))
            for key, value in zip(('brightness', 'sharpness', 'gain', 'contrast'), values[4:8]):
                self._coeff_boxes[key].setValue(float(value))
            self._cleanup_sliders['impulse_filter'].setValue(int(values[8]))
            self._cleanup_sliders['temporal_denoise'].setValue(int(values[9]))
            self._cleanup_sliders['noise_reduction'].setValue(int(values[10]))
            self._cleanup_sliders['hum_removal'].setValue(int(values[11]))
            self._cleanup_sliders['auto_black_level'].setValue(int(values[12]))
            self._cleanup_sliders['head_switching_mask'].setValue(int(values[13]))
            self._cleanup_sliders['line_stabilization'].setValue(int(values[14]))
            self._cleanup_sliders['auto_gain_contrast'].setValue(int(values[15]))
            self._cleanup_coeff_boxes['impulse_filter'].setValue(float(values[16]))
            self._cleanup_coeff_boxes['temporal_denoise'].setValue(float(values[17]))
            self._cleanup_coeff_boxes['noise_reduction'].setValue(float(values[18]))
            self._cleanup_coeff_boxes['hum_removal'].setValue(float(values[19]))
            self._cleanup_coeff_boxes['auto_black_level'].setValue(float(values[20]))
            self._cleanup_coeff_boxes['head_switching_mask'].setValue(float(values[21]))
            self._cleanup_coeff_boxes['line_stabilization'].setValue(float(values[22]))
            self._cleanup_coeff_boxes['auto_gain_contrast'].setValue(float(values[23]))

        def _set_decoder_tuning_values(self, decoder_tuning):
            if not self._decoder_tuning_enabled or decoder_tuning is None:
                return
            if decoder_tuning['tape_format'] in self._tape_formats:
                self._tape_format_box.setCurrentIndex(self._tape_formats.index(decoder_tuning['tape_format']))
            self._extra_roll_box.setValue(int(decoder_tuning['extra_roll']))
            self._line_start_start_box.setValue(int(decoder_tuning['line_start_range'][0]))
            self._line_start_end_box.setValue(int(decoder_tuning['line_start_range'][1]))
            self._quality_threshold_slider.setValue(int(decoder_tuning['quality_threshold']))
            self._quality_threshold_coeff_box.setValue(float(decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)))
            self._clock_lock_slider.setValue(int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)))
            self._clock_lock_coeff_box.setValue(float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)))
            self._start_lock_slider.setValue(int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT)))
            self._start_lock_coeff_box.setValue(float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)))
            self._adaptive_threshold_slider.setValue(int(decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT)))
            self._adaptive_threshold_coeff_box.setValue(float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)))
            self._dropout_repair_slider.setValue(int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)))
            self._dropout_repair_coeff_box.setValue(float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)))
            self._wow_flutter_compensation_slider.setValue(int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)))
            self._wow_flutter_compensation_coeff_box.setValue(float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)))
            self._auto_line_align_slider.setValue(int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)))
            self._per_line_shift_map = normalise_per_line_shift_map(
                decoder_tuning.get('per_line_shift', {}),
                maximum_line=self._line_count,
            )
            self._refresh_per_line_shift_editor()
            self._refresh_per_line_shift_summary()
            self._show_quality_box.setChecked(bool(decoder_tuning.get('show_quality', False)))
            self._show_rejects_box.setChecked(bool(decoder_tuning.get('show_rejects', False)))
            self._show_start_clock_box.setChecked(bool(decoder_tuning.get('show_start_clock', False)))
            self._show_clock_visuals_box.setChecked(bool(decoder_tuning.get('show_clock_visuals', False)))
            self._show_alignment_visuals_box.setChecked(bool(decoder_tuning.get('show_alignment_visuals', False)))
            self._show_quality_meter_box.setChecked(bool(decoder_tuning.get('show_quality_meter', False)))
            self._show_histogram_graph_box.setChecked(bool(decoder_tuning.get('show_histogram_graph', False)))
            self._show_eye_pattern_box.setChecked(bool(decoder_tuning.get('show_eye_pattern', False)))

        def _set_line_selection_values(self, line_selection):
            selected = _normalise_line_selection(line_selection, line_count=self._line_count)
            for line, checkbox in self._line_checkboxes.items():
                checkbox.setChecked(line in selected)

        def _set_fix_capture_card_values(self, fix_capture_card):
            settings = normalise_fix_capture_card(fix_capture_card)
            self._fix_capture_card_enabled.setChecked(bool(settings['enabled']))
            self._fix_capture_card_seconds.setValue(int(settings['seconds']))
            self._fix_capture_card_interval.setValue(int(settings['interval_minutes']))

        def _apply_parsed_tuning(
            self,
            values,
            decoder_tuning,
            line_selection,
            fix_capture_card,
            *,
            line_control_overrides=None,
            line_decoder_overrides=None,
            edit_target_mode=EDIT_TARGET_ALL,
            edit_target_line=1,
        ):
            self._begin_bulk_change()
            try:
                self._global_controls = _normalise_signal_controls_tuple(values)
                self._line_control_overrides = normalise_line_control_overrides(
                    line_control_overrides if line_control_overrides is not None else (decoder_tuning or {}).get('line_control_overrides', {}),
                    line_count=self._line_count,
                )
                self._global_decoder_tuning = _normalise_decoder_tuning(decoder_tuning)
                self._line_decoder_overrides = normalise_line_decoder_overrides(
                    line_decoder_overrides if line_decoder_overrides is not None else (decoder_tuning or {}).get('line_decoder_overrides', {}),
                    line_count=self._line_count,
                )
                if self._global_decoder_tuning is not None:
                    self._global_decoder_tuning['line_control_overrides'] = normalise_line_control_overrides(
                        self._line_control_overrides,
                        line_count=self._line_count,
                    )
                    self._global_decoder_tuning['line_decoder_overrides'] = normalise_line_decoder_overrides(
                        self._line_decoder_overrides,
                        line_count=self._line_count,
                    )
                mode_index = self._edit_target_mode_box.findData(edit_target_mode)
                if mode_index < 0:
                    mode_index = self._edit_target_mode_box.findData(EDIT_TARGET_ALL)
                self._edit_target_mode_box.setCurrentIndex(mode_index)
                self._edit_target_line_box.setValue(_normalise_edit_target_line(edit_target_line, line_count=self._line_count) or 1)
                self._load_target_state_into_editor()
                self._set_line_selection_values(line_selection)
                self._set_fix_capture_card_values(fix_capture_card)
            finally:
                self._end_bulk_change(record_history=True)

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

        def _preview_marker_positions(self, line):
            if not line.is_teletext or line.start is None:
                return None
            return (
                int(line.start),
                int(line.start + (8 * 8)),
                int(line.start + (24 * 8)),
            )

        def _preview_alignment_positions(self, line):
            if not line.is_teletext or line.start is None:
                return None
            return {
                'current': float(line.start),
                'pre_alignment': getattr(line, '_pre_alignment_start', None),
                'target': getattr(line, '_auto_align_target', None),
            }

        def _draw_panel_background(self, painter, rect, title):
            painter.fillRect(rect, QtGui.QColor(12, 12, 14))
            painter.setPen(QtGui.QPen(QtGui.QColor(90, 90, 96), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            painter.setPen(QtGui.QColor(222, 232, 244))
            painter.drawText(rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft, title)

        def _draw_quality_meter_panel(self, painter, rect, rendered_lines):
            self._draw_panel_background(painter, rect, 'Quality Meter')
            stats = quality_meter_stats(rendered_lines)
            meter_rect = QtCore.QRectF(rect.left() + 10, rect.top() + 24, rect.width() - 20, 14)
            painter.fillRect(meter_rect, QtGui.QColor(32, 32, 36))
            painter.setPen(QtGui.QPen(QtGui.QColor(88, 88, 92), 1))
            painter.drawRect(meter_rect)
            fill_width = meter_rect.width() * (max(min(stats['average_quality'], 100.0), 0.0) / 100.0)
            if fill_width > 0:
                fill_rect = QtCore.QRectF(meter_rect.left() + 1, meter_rect.top() + 1, max(fill_width - 2, 0), meter_rect.height() - 2)
                painter.fillRect(fill_rect, QtGui.QColor(80, 210, 120))
            painter.setPen(QtGui.QColor(230, 236, 242))
            painter.drawText(
                rect.adjusted(10, 42, -10, -8),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
                f"Avg {stats['average_quality']:.1f}   TT {stats['teletext_lines']}/{stats['analysed_lines']}   Reject {stats['rejects']}",
            )

        def _draw_histogram_panel(self, painter, rect, rendered_lines, preview_config):
            self._draw_panel_background(painter, rect, 'Histogram / Black Level')
            stats = histogram_black_level_stats(rendered_lines, config=preview_config, bins=64)
            histogram = stats['histogram']
            plot = rect.adjusted(8, 22, -8, -18)
            painter.fillRect(plot, QtGui.QColor(20, 20, 24))
            painter.setPen(QtGui.QPen(QtGui.QColor(44, 44, 52), 1))
            painter.drawRect(plot.adjusted(0, 0, -1, -1))
            maximum = float(np.max(histogram)) if histogram.size else 0.0
            if maximum > 0.0 and plot.width() > 2:
                points = []
                for index, value in enumerate(histogram):
                    x = plot.left() + ((plot.width() - 1) * (index / max(len(histogram) - 1, 1)))
                    y = plot.bottom() - ((plot.height() - 1) * (float(value) / maximum))
                    points.append(QtCore.QPointF(x, y))
                painter.setPen(QtGui.QPen(QtGui.QColor(110, 210, 255), 1.3))
                painter.drawPolyline(QtGui.QPolygonF(points))
            black_x = plot.left() + ((plot.width() - 1) * (stats['black_level'] / 255.0))
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 205, 90), 1))
            painter.drawLine(QtCore.QPointF(black_x, plot.top()), QtCore.QPointF(black_x, plot.bottom()))
            painter.setPen(QtGui.QColor(228, 232, 240))
            painter.drawText(rect.adjusted(10, 4, -10, -4), QtCore.Qt.AlignTop | QtCore.Qt.AlignRight, f"Black {stats['black_level']:.1f}")

        def _draw_eye_pattern_panel(self, painter, rect, rendered_lines):
            self._draw_panel_background(painter, rect, 'Eye Pattern / Clock Preview')
            stats = eye_pattern_clock_stats(rendered_lines, width=24 * 8)
            plot = rect.adjusted(8, 22, -8, -18)
            painter.fillRect(plot, QtGui.QColor(20, 20, 24))
            painter.setPen(QtGui.QPen(QtGui.QColor(44, 44, 52), 1))
            painter.drawRect(plot.adjusted(0, 0, -1, -1))
            if stats is None:
                painter.setPen(QtGui.QColor(190, 196, 204))
                painter.drawText(plot, QtCore.Qt.AlignCenter, 'No teletext lines')
                return
            average = stats['average']
            low = stats['low']
            high = stats['high']
            samples_per_bit = max(int(stats.get('samples_per_bit', 8)), 1)
            for bit in range(0, len(average), samples_per_bit):
                x = plot.left() + ((plot.width() - 1) * (bit / max(len(average) - 1, 1)))
                painter.setPen(QtGui.QPen(QtGui.QColor(56, 56, 62), 1))
                painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
            for index in range(len(average)):
                x = plot.left() + ((plot.width() - 1) * (index / max(len(average) - 1, 1)))
                y_low = plot.bottom() - ((plot.height() - 1) * (float(low[index]) / 255.0))
                y_high = plot.bottom() - ((plot.height() - 1) * (float(high[index]) / 255.0))
                painter.setPen(QtGui.QPen(QtGui.QColor(80, 180, 255, 90), 1))
                painter.drawLine(QtCore.QPointF(x, y_low), QtCore.QPointF(x, y_high))
            points = []
            for index, value in enumerate(average):
                x = plot.left() + ((plot.width() - 1) * (index / max(len(average) - 1, 1)))
                y = plot.bottom() - ((plot.height() - 1) * (float(value) / 255.0))
                points.append(QtCore.QPointF(x, y))
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 120, 120), 1.4))
            painter.drawPolyline(QtGui.QPolygonF(points))
            painter.setPen(QtGui.QColor(228, 232, 240))
            painter.drawText(rect.adjusted(10, 4, -10, -4), QtCore.Qt.AlignTop | QtCore.Qt.AlignRight, f"{stats['segment_count']} lines")

        def _render_preview_image(self, preview_lines):
            if not preview_lines:
                return None

            preview_config, preview_tape_format = self._preview_settings()
            decoder_tuning = self.decoder_tuning_values or {}
            show_quality = bool(decoder_tuning.get('show_quality', False))
            show_rejects = bool(decoder_tuning.get('show_rejects', False))
            show_start_clock = bool(decoder_tuning.get('show_start_clock', False))
            show_clock_visuals = bool(decoder_tuning.get('show_clock_visuals', False))
            show_alignment_visuals = bool(decoder_tuning.get('show_alignment_visuals', False))
            show_quality_meter = bool(decoder_tuning.get('show_quality_meter', False))
            show_histogram_graph = bool(decoder_tuning.get('show_histogram_graph', False))
            show_eye_pattern = bool(decoder_tuning.get('show_eye_pattern', False))
            show_diagnostics = show_quality or show_rejects or show_start_clock or show_clock_visuals or show_alignment_visuals
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
                impulse_filter=self.values[8],
                temporal_denoise=self.values[9],
                noise_reduction=self.values[10],
                hum_removal=self.values[11],
                auto_black_level=self.values[12],
                head_switching_mask=self.values[13],
                line_stabilization=self.values[14],
                auto_gain_contrast=self.values[15],
                impulse_filter_coeff=self.values[16],
                temporal_denoise_coeff=self.values[17],
                noise_reduction_coeff=self.values[18],
                hum_removal_coeff=self.values[19],
                auto_black_level_coeff=self.values[20],
                head_switching_mask_coeff=self.values[21],
                line_stabilization_coeff=self.values[22],
                auto_gain_contrast_coeff=self.values[23],
                quality_threshold=self.decoder_tuning_values['quality_threshold'] if self.decoder_tuning_values is not None else QUALITY_THRESHOLD_DEFAULT,
                quality_threshold_coeff=self.decoder_tuning_values.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT) if self.decoder_tuning_values is not None else QUALITY_THRESHOLD_COEFF_DEFAULT,
                clock_lock=self.decoder_tuning_values['clock_lock'] if self.decoder_tuning_values is not None else CLOCK_LOCK_DEFAULT,
                clock_lock_coeff=self.decoder_tuning_values.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT) if self.decoder_tuning_values is not None else CLOCK_LOCK_COEFF_DEFAULT,
                start_lock=self.decoder_tuning_values['start_lock'] if self.decoder_tuning_values is not None else START_LOCK_DEFAULT,
                start_lock_coeff=self.decoder_tuning_values.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT) if self.decoder_tuning_values is not None else START_LOCK_COEFF_DEFAULT,
                adaptive_threshold=self.decoder_tuning_values['adaptive_threshold'] if self.decoder_tuning_values is not None else ADAPTIVE_THRESHOLD_DEFAULT,
                adaptive_threshold_coeff=self.decoder_tuning_values.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT) if self.decoder_tuning_values is not None else ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
                dropout_repair=self.decoder_tuning_values['dropout_repair'] if self.decoder_tuning_values is not None else DROPOUT_REPAIR_DEFAULT,
                dropout_repair_coeff=self.decoder_tuning_values.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT) if self.decoder_tuning_values is not None else DROPOUT_REPAIR_COEFF_DEFAULT,
                wow_flutter_compensation=self.decoder_tuning_values['wow_flutter_compensation'] if self.decoder_tuning_values is not None else WOW_FLUTTER_COMPENSATION_DEFAULT,
                wow_flutter_compensation_coeff=self.decoder_tuning_values.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT) if self.decoder_tuning_values is not None else WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
                auto_line_align=self.decoder_tuning_values['auto_line_align'] if self.decoder_tuning_values is not None else AUTO_LINE_ALIGN_DEFAULT,
                per_line_shift=self.decoder_tuning_values.get('per_line_shift', {}) if self.decoder_tuning_values is not None else {},
                line_control_overrides=self.decoder_tuning_values.get('line_control_overrides', {}) if self.decoder_tuning_values is not None else {},
                line_decoder_overrides=self.decoder_tuning_values.get('line_decoder_overrides', {}) if self.decoder_tuning_values is not None else {},
            )
            width = preview_config.resample_size
            line_height = 10 if show_diagnostics else 6
            image = np.zeros((len(preview_lines) * line_height, width, 3), dtype=np.uint8)
            rendered_lines = []

            for row, (number, raw_bytes) in enumerate(preview_lines):
                line = Line(raw_bytes, number)
                rendered_lines.append(line)
                base = np.clip(line.resampled[:width], 0, 255).astype(np.uint8)
                if line.is_teletext:
                    rgb = np.stack((base // 3, base, (base * 3) // 4), axis=1)
                else:
                    rgb = np.stack((base, base // 3, base // 3), axis=1)
                start = row * line_height
                image[start:start + line_height, :, :] = rgb[np.newaxis, :, :]
            qimage = QtGui.QImage(
                image.data,
                image.shape[1],
                image.shape[0],
                image.strides[0],
                QtGui.QImage.Format_RGB888,
            ).copy()
            if not show_diagnostics:
                return qimage

            painter = QtGui.QPainter(qimage)
            font = painter.font()
            font.setPixelSize(max(line_height - 2, 7))
            painter.setFont(font)
            target_line = self._current_edit_target_line()
            for row, line in enumerate(rendered_lines):
                top = row * line_height
                bottom = top + line_height - 1
                baseline = top + max(line_height - 2, 7)
                logical_line = line.temporal_key(line._number)
                if target_line is not None and logical_line is not None and (int(logical_line) + 1) == int(target_line):
                    painter.fillRect(0, top, qimage.width(), line_height, QtGui.QColor(255, 220, 90, 32))
                    painter.setPen(QtGui.QPen(QtGui.QColor(255, 220, 90, 180), 1))
                    painter.drawRect(0, top, qimage.width() - 1, line_height - 1)
                if show_start_clock:
                    positions = self._preview_marker_positions(line)
                    if positions is not None:
                        start_x, clock_start_x, clock_end_x = positions
                        painter.setPen(QtGui.QPen(QtGui.QColor(255, 230, 80, 220), 1))
                        painter.drawLine(start_x, top, start_x, bottom)
                        painter.setPen(QtGui.QPen(QtGui.QColor(80, 220, 255, 210), 1))
                        painter.drawLine(clock_start_x, top, clock_start_x, bottom)
                        painter.drawLine(clock_end_x, top, clock_end_x, bottom)
                if show_clock_visuals:
                    positions = self._preview_marker_positions(line)
                    if positions is not None:
                        _, clock_start_x, clock_end_x = positions
                        painter.fillRect(
                            QtCore.QRectF(clock_start_x, top, max(clock_end_x - clock_start_x, 1), line_height),
                            QtGui.QColor(45, 150, 255, 52),
                        )
                        painter.setPen(QtGui.QPen(QtGui.QColor(60, 235, 255, 230), 1))
                        clock_mid_x = int(round((clock_start_x + clock_end_x) / 2.0))
                        painter.drawLine(clock_mid_x, top, clock_mid_x, bottom)
                if show_alignment_visuals:
                    alignment = self._preview_alignment_positions(line)
                    if alignment is not None:
                        current = alignment.get('current')
                        target = alignment.get('target')
                        pre_alignment = alignment.get('pre_alignment')
                        if current is not None and target is not None and abs(float(current) - float(target)) > 1e-6:
                            painter.fillRect(
                                QtCore.QRectF(min(current, target), top, abs(current - target), line_height),
                                QtGui.QColor(235, 40, 210, 44),
                            )
                        if pre_alignment is not None:
                            painter.setPen(QtGui.QPen(QtGui.QColor(208, 208, 214, 150), 1))
                            painter.drawLine(int(round(pre_alignment)), top, int(round(pre_alignment)), bottom)
                        if target is not None:
                            painter.setPen(QtGui.QPen(QtGui.QColor(255, 90, 235, 235), 1))
                            painter.drawLine(int(round(target)), top, int(round(target)), bottom)
                        if current is not None:
                            painter.setPen(QtGui.QPen(QtGui.QColor(90, 255, 120, 235), 1))
                            painter.drawLine(int(round(current)), top, int(round(current)), bottom)
                if show_quality:
                    painter.setPen(QtGui.QColor(235, 245, 255))
                    painter.drawText(4, baseline, f'Q{line.diagnostic_quality:02d}')
                if show_rejects and not line.is_teletext:
                    painter.setPen(QtGui.QColor(255, 180, 180))
                    painter.drawText(46, baseline, (line.reject_reason or 'Rejected')[:26])
            painter.end()
            extra_panels = []
            if show_quality_meter:
                extra_panels.append(('quality_meter', 60))
            if show_histogram_graph:
                extra_panels.append(('histogram_graph', 84))
            if show_eye_pattern:
                extra_panels.append(('eye_pattern', 84))
            if not extra_panels:
                return qimage

            total_height = qimage.height() + sum(height for _, height in extra_panels)
            canvas = QtGui.QImage(qimage.width(), total_height, QtGui.QImage.Format_RGB888)
            canvas.fill(QtGui.QColor(8, 8, 10))
            painter = QtGui.QPainter(canvas)
            painter.drawImage(0, 0, qimage)
            offset_y = qimage.height()
            for kind, height in extra_panels:
                rect = QtCore.QRect(0, offset_y, canvas.width(), height)
                if kind == 'quality_meter':
                    self._draw_quality_meter_panel(painter, rect, rendered_lines)
                elif kind == 'histogram_graph':
                    self._draw_histogram_panel(painter, rect, rendered_lines, preview_config)
                elif kind == 'eye_pattern':
                    self._draw_eye_pattern_panel(painter, rect, rendered_lines)
                offset_y += height
            painter.end()
            return canvas

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
    analysis_source=None,
    visible_sections=None,
    timer_seconds=None,
    show_timer=False,
):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    controls = _normalise_signal_controls_tuple(controls)
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
        analysis_source=analysis_source,
        visible_sections=visible_sections,
        timer_seconds=timer_seconds,
        show_timer=show_timer,
    )
    if live:
        _run_dialog_window(dialog)
        result = (dialog.values, dialog.decoder_tuning_values, dialog.line_selection_values, dialog.fix_capture_card_values)
        if show_timer:
            return result + (dialog.timer_seconds_value,)
        return result
    if _run_dialog_window(dialog) == QtWidgets.QDialog.Accepted:
        result = (dialog.values, dialog.decoder_tuning_values, dialog.line_selection_values, dialog.fix_capture_card_values)
        if show_timer:
            return result + (dialog.timer_seconds_value,)
        return result
    return None


def _live_tuner_entry(shared_values, title, tape_formats, line_count, config=None, tape_format='vhs', analysis_source=None, visible_sections=None):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    _ensure_app()

    line_offset = _line_selection_offset()
    fix_offset = _fix_capture_card_offset(line_count)
    line_control_offset = _line_control_override_offset(line_count)
    line_decoder_offset = _line_decoder_override_offset(line_count)
    decoder_tuning = {
        'tape_format': tape_formats[int(shared_values[SIGNAL_CONTROL_COUNT])],
        'extra_roll': int(shared_values[SIGNAL_CONTROL_COUNT + 1]),
        'line_start_range': (
            int(shared_values[SIGNAL_CONTROL_COUNT + 2]),
            int(shared_values[SIGNAL_CONTROL_COUNT + 3]),
        ),
        'quality_threshold': int(shared_values[SIGNAL_CONTROL_COUNT + 4]),
        'quality_threshold_coeff': float(shared_values[SIGNAL_CONTROL_COUNT + 5]),
        'clock_lock': int(shared_values[SIGNAL_CONTROL_COUNT + 6]),
        'clock_lock_coeff': float(shared_values[SIGNAL_CONTROL_COUNT + 7]),
        'start_lock': int(shared_values[SIGNAL_CONTROL_COUNT + 8]),
        'start_lock_coeff': float(shared_values[SIGNAL_CONTROL_COUNT + 9]),
        'adaptive_threshold': int(shared_values[SIGNAL_CONTROL_COUNT + 10]),
        'adaptive_threshold_coeff': float(shared_values[SIGNAL_CONTROL_COUNT + 11]),
        'dropout_repair': int(shared_values[SIGNAL_CONTROL_COUNT + 12]),
        'dropout_repair_coeff': float(shared_values[SIGNAL_CONTROL_COUNT + 13]),
        'wow_flutter_compensation': int(shared_values[SIGNAL_CONTROL_COUNT + 14]),
        'wow_flutter_compensation_coeff': float(shared_values[SIGNAL_CONTROL_COUNT + 15]),
        'auto_line_align': int(shared_values[SIGNAL_CONTROL_COUNT + 16]),
            'show_quality': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 17])),
            'show_rejects': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 18])),
            'show_start_clock': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 19])),
            'show_clock_visuals': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 20])),
        'show_alignment_visuals': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 21])),
        'show_quality_meter': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 22])),
        'show_histogram_graph': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 23])),
        'show_eye_pattern': bool(int(shared_values[SIGNAL_CONTROL_COUNT + 24])),
        'per_line_shift': {
            line: float(shared_values[_per_line_shift_offset(line)])
            for line in range(1, min(int(line_count), PER_LINE_SHIFT_SLOT_COUNT) + 1)
            if abs(float(shared_values[_per_line_shift_offset(line)])) > 1e-9
        },
        'line_control_overrides': _deserialise_line_control_override_slots(
            shared_values[line_control_offset:line_control_offset + LINE_OVERRIDE_SIGNAL_SLOT_COUNT],
            line_count=line_count,
        ),
        'line_decoder_overrides': _deserialise_line_decoder_override_slots(
            shared_values[line_decoder_offset:line_decoder_offset + LINE_OVERRIDE_DECODER_SLOT_COUNT],
            line_count=line_count,
        ),
    } if tape_formats else None
    line_selection = frozenset(
        line for line in range(1, line_count + 1)
        if int(shared_values[line_offset + line - 1]) != 0
    )
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
            shared_values[SIGNAL_CONTROL_COUNT] = float(tape_formats.index(next_decoder_tuning['tape_format']))
            shared_values[SIGNAL_CONTROL_COUNT + 1] = float(next_decoder_tuning['extra_roll'])
            shared_values[SIGNAL_CONTROL_COUNT + 2] = float(next_decoder_tuning['line_start_range'][0])
            shared_values[SIGNAL_CONTROL_COUNT + 3] = float(next_decoder_tuning['line_start_range'][1])
            shared_values[SIGNAL_CONTROL_COUNT + 4] = float(next_decoder_tuning['quality_threshold'])
            shared_values[SIGNAL_CONTROL_COUNT + 5] = float(next_decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 6] = float(next_decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 7] = float(next_decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 8] = float(next_decoder_tuning.get('start_lock', START_LOCK_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 9] = float(next_decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 10] = float(next_decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 11] = float(next_decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 12] = float(next_decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 13] = float(next_decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 14] = float(next_decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 15] = float(next_decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 16] = float(next_decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))
            shared_values[SIGNAL_CONTROL_COUNT + 17] = 1.0 if next_decoder_tuning.get('show_quality', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 18] = 1.0 if next_decoder_tuning.get('show_rejects', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 19] = 1.0 if next_decoder_tuning.get('show_start_clock', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 20] = 1.0 if next_decoder_tuning.get('show_clock_visuals', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 21] = 1.0 if next_decoder_tuning.get('show_alignment_visuals', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 22] = 1.0 if next_decoder_tuning.get('show_quality_meter', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 23] = 1.0 if next_decoder_tuning.get('show_histogram_graph', False) else 0.0
            shared_values[SIGNAL_CONTROL_COUNT + 24] = 1.0 if next_decoder_tuning.get('show_eye_pattern', False) else 0.0
            per_line_shift = normalise_per_line_shift_map(next_decoder_tuning.get('per_line_shift', {}), maximum_line=line_count)
            for line in range(1, PER_LINE_SHIFT_SLOT_COUNT + 1):
                shared_values[_per_line_shift_offset(line)] = float(per_line_shift.get(line, 0))
            line_control_slots = _serialise_line_control_override_slots(
                next_decoder_tuning.get('line_control_overrides', {}),
                line_count=line_count,
            )
            for index, value in enumerate(line_control_slots):
                shared_values[line_control_offset + index] = float(value)
            line_decoder_slots = _serialise_line_decoder_override_slots(
                next_decoder_tuning.get('line_decoder_overrides', {}),
                line_count=line_count,
            )
            for index, value in enumerate(line_decoder_slots):
                shared_values[line_decoder_offset + index] = float(value)
        for line in range(1, line_count + 1):
            shared_values[line_offset + line - 1] = 1.0 if line in next_line_selection else 0.0
        fix_settings = normalise_fix_capture_card(next_fix_capture_card)
        shared_values[fix_offset] = 1.0 if fix_settings['enabled'] else 0.0
        shared_values[fix_offset + 1] = float(fix_settings['seconds'])
        shared_values[fix_offset + 2] = float(fix_settings['interval_minutes'])

    dialog = VBITuningDialog(
        title,
        controls=tuple(float(value) for value in shared_values[:SIGNAL_CONTROL_COUNT]),
        config=config,
        tape_format=tape_format,
        live=True,
        decoder_tuning=decoder_tuning,
        tape_formats=tape_formats,
        line_selection=line_selection,
        line_count=line_count,
        fix_capture_card=fix_capture_card,
        analysis_source=analysis_source,
        visible_sections=visible_sections,
    )
    dialog.set_change_callback(update_values)
    _run_dialog_window(dialog)


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
            int(self._shared_values[8]),
            int(self._shared_values[9]),
            int(self._shared_values[10]),
            int(self._shared_values[11]),
            int(self._shared_values[12]),
            int(self._shared_values[13]),
            int(self._shared_values[14]),
            int(self._shared_values[15]),
            float(self._shared_values[16]),
            float(self._shared_values[17]),
            float(self._shared_values[18]),
            float(self._shared_values[19]),
            float(self._shared_values[20]),
            float(self._shared_values[21]),
            float(self._shared_values[22]),
            float(self._shared_values[23]),
        )

    def decoder_tuning(self):
        if not self._tape_formats:
            return None
        fix_offset = _fix_capture_card_offset(self._line_count)
        line_control_offset = _line_control_override_offset(self._line_count)
        line_decoder_offset = _line_decoder_override_offset(self._line_count)
        format_index = min(max(int(self._shared_values[SIGNAL_CONTROL_COUNT]), 0), len(self._tape_formats) - 1)
        return {
            'tape_format': self._tape_formats[format_index],
            'extra_roll': int(self._shared_values[SIGNAL_CONTROL_COUNT + 1]),
            'line_start_range': (
                int(self._shared_values[SIGNAL_CONTROL_COUNT + 2]),
                int(self._shared_values[SIGNAL_CONTROL_COUNT + 3]),
            ),
            'quality_threshold': int(self._shared_values[SIGNAL_CONTROL_COUNT + 4]),
            'quality_threshold_coeff': float(self._shared_values[SIGNAL_CONTROL_COUNT + 5]),
            'clock_lock': int(self._shared_values[SIGNAL_CONTROL_COUNT + 6]),
            'clock_lock_coeff': float(self._shared_values[SIGNAL_CONTROL_COUNT + 7]),
            'start_lock': int(self._shared_values[SIGNAL_CONTROL_COUNT + 8]),
            'start_lock_coeff': float(self._shared_values[SIGNAL_CONTROL_COUNT + 9]),
            'adaptive_threshold': int(self._shared_values[SIGNAL_CONTROL_COUNT + 10]),
            'adaptive_threshold_coeff': float(self._shared_values[SIGNAL_CONTROL_COUNT + 11]),
            'dropout_repair': int(self._shared_values[SIGNAL_CONTROL_COUNT + 12]),
            'dropout_repair_coeff': float(self._shared_values[SIGNAL_CONTROL_COUNT + 13]),
            'wow_flutter_compensation': int(self._shared_values[SIGNAL_CONTROL_COUNT + 14]),
            'wow_flutter_compensation_coeff': float(self._shared_values[SIGNAL_CONTROL_COUNT + 15]),
            'auto_line_align': int(self._shared_values[SIGNAL_CONTROL_COUNT + 16]),
            'show_quality': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 17])),
            'show_rejects': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 18])),
            'show_start_clock': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 19])),
            'show_clock_visuals': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 20])),
            'show_alignment_visuals': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 21])),
            'show_quality_meter': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 22])),
            'show_histogram_graph': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 23])),
            'show_eye_pattern': bool(int(self._shared_values[SIGNAL_CONTROL_COUNT + 24])),
            'per_line_shift': {
                line: float(self._shared_values[_per_line_shift_offset(line)])
                for line in range(1, min(self._line_count, PER_LINE_SHIFT_SLOT_COUNT) + 1)
                if abs(float(self._shared_values[_per_line_shift_offset(line)])) > 1e-9
            },
            'line_control_overrides': _deserialise_line_control_override_slots(
                self._shared_values[line_control_offset:line_control_offset + LINE_OVERRIDE_SIGNAL_SLOT_COUNT],
                line_count=self._line_count,
            ),
            'line_decoder_overrides': _deserialise_line_decoder_override_slots(
                self._shared_values[line_decoder_offset:line_decoder_offset + LINE_OVERRIDE_DECODER_SLOT_COUNT],
                line_count=self._line_count,
            ),
        }

    def line_selection(self):
        line_offset = _line_selection_offset()
        return frozenset(
            line for line in range(1, self._line_count + 1)
            if int(self._shared_values[line_offset + line - 1]) != 0
        )

    def fix_capture_card(self):
        fix_offset = _fix_capture_card_offset(self._line_count)
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
            for index, value in enumerate(_normalise_signal_controls_tuple(values)):
                self._shared_values[index] = float(value)
        if self._tape_formats and decoder_tuning is not None:
            tape_format = decoder_tuning['tape_format']
            if tape_format in self._tape_formats:
                self._shared_values[SIGNAL_CONTROL_COUNT] = float(self._tape_formats.index(tape_format))
            self._shared_values[SIGNAL_CONTROL_COUNT + 1] = float(decoder_tuning['extra_roll'])
            self._shared_values[SIGNAL_CONTROL_COUNT + 2] = float(decoder_tuning['line_start_range'][0])
            self._shared_values[SIGNAL_CONTROL_COUNT + 3] = float(decoder_tuning['line_start_range'][1])
            self._shared_values[SIGNAL_CONTROL_COUNT + 4] = float(decoder_tuning.get('quality_threshold', QUALITY_THRESHOLD_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 5] = float(decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 6] = float(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 7] = float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 8] = float(decoder_tuning.get('start_lock', START_LOCK_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 9] = float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 10] = float(decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 11] = float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 12] = float(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 13] = float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 14] = float(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 15] = float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 16] = float(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))
            self._shared_values[SIGNAL_CONTROL_COUNT + 17] = 1.0 if decoder_tuning.get('show_quality', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 18] = 1.0 if decoder_tuning.get('show_rejects', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 19] = 1.0 if decoder_tuning.get('show_start_clock', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 20] = 1.0 if decoder_tuning.get('show_clock_visuals', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 21] = 1.0 if decoder_tuning.get('show_alignment_visuals', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 22] = 1.0 if decoder_tuning.get('show_quality_meter', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 23] = 1.0 if decoder_tuning.get('show_histogram_graph', False) else 0.0
            self._shared_values[SIGNAL_CONTROL_COUNT + 24] = 1.0 if decoder_tuning.get('show_eye_pattern', False) else 0.0
            per_line_shift = normalise_per_line_shift_map(decoder_tuning.get('per_line_shift', {}), maximum_line=self._line_count)
            for line in range(1, PER_LINE_SHIFT_SLOT_COUNT + 1):
                self._shared_values[_per_line_shift_offset(line)] = float(per_line_shift.get(line, 0))
            line_control_offset = _line_control_override_offset(self._line_count)
            line_decoder_offset = _line_decoder_override_offset(self._line_count)
            line_control_slots = _serialise_line_control_override_slots(
                decoder_tuning.get('line_control_overrides', {}),
                line_count=self._line_count,
            )
            for index, value in enumerate(line_control_slots):
                self._shared_values[line_control_offset + index] = float(value)
            line_decoder_slots = _serialise_line_decoder_override_slots(
                decoder_tuning.get('line_decoder_overrides', {}),
                line_count=self._line_count,
            )
            for index, value in enumerate(line_decoder_slots):
                self._shared_values[line_decoder_offset + index] = float(value)
        if line_selection is not None:
            selected = _normalise_line_selection(line_selection, line_count=self._line_count)
            line_offset = _line_selection_offset()
            for line in range(1, self._line_count + 1):
                self._shared_values[line_offset + line - 1] = 1.0 if line in selected else 0.0
        if fix_capture_card is not None:
            settings = normalise_fix_capture_card(fix_capture_card)
            fix_offset = _fix_capture_card_offset(self._line_count)
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
    config=None,
    tape_format='vhs',
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=DEFAULT_LINE_COUNT,
    fix_capture_card=None,
    analysis_source=None,
    visible_sections=None,
):
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR

    ctx = mp.get_context('spawn')
    controls = _normalise_signal_controls_tuple(controls)
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
            float(decoder_tuning['quality_threshold']),
            float(decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)),
            float(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)),
            float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)),
            float(decoder_tuning.get('start_lock', START_LOCK_DEFAULT)),
            float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)),
            float(decoder_tuning['adaptive_threshold']),
            float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)),
            float(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)),
            float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)),
            float(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)),
            float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)),
            float(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
            1.0 if decoder_tuning.get('show_quality', False) else 0.0,
            1.0 if decoder_tuning.get('show_rejects', False) else 0.0,
            1.0 if decoder_tuning.get('show_start_clock', False) else 0.0,
            1.0 if decoder_tuning.get('show_clock_visuals', False) else 0.0,
            1.0 if decoder_tuning.get('show_alignment_visuals', False) else 0.0,
            1.0 if decoder_tuning.get('show_quality_meter', False) else 0.0,
            1.0 if decoder_tuning.get('show_histogram_graph', False) else 0.0,
            1.0 if decoder_tuning.get('show_eye_pattern', False) else 0.0,
        ))
        per_line_shift = normalise_per_line_shift_map(decoder_tuning.get('per_line_shift', {}), maximum_line=line_count)
        shared_seed.extend(
            float(per_line_shift.get(line, 0))
            for line in range(1, PER_LINE_SHIFT_SLOT_COUNT + 1)
        )
    else:
        shared_seed.extend((
            0.0,
            0.0,
            0.0,
            0.0,
            float(QUALITY_THRESHOLD_DEFAULT),
            float(QUALITY_THRESHOLD_COEFF_DEFAULT),
            float(CLOCK_LOCK_DEFAULT),
            float(CLOCK_LOCK_COEFF_DEFAULT),
            float(START_LOCK_DEFAULT),
            float(START_LOCK_COEFF_DEFAULT),
            float(ADAPTIVE_THRESHOLD_DEFAULT),
            float(ADAPTIVE_THRESHOLD_COEFF_DEFAULT),
            float(DROPOUT_REPAIR_DEFAULT),
            float(DROPOUT_REPAIR_COEFF_DEFAULT),
            float(WOW_FLUTTER_COMPENSATION_DEFAULT),
            float(WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT),
            float(AUTO_LINE_ALIGN_DEFAULT),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ))
        shared_seed.extend(0.0 for _ in range(PER_LINE_SHIFT_SLOT_COUNT))
    shared_seed.extend(1.0 if line in line_selection else 0.0 for line in range(1, line_count + 1))
    shared_seed.extend((
        1.0 if fix_capture_card['enabled'] else 0.0,
        float(fix_capture_card['seconds']),
        float(fix_capture_card['interval_minutes']),
    ))
    shared_seed.extend(_serialise_line_control_override_slots(
        decoder_tuning.get('line_control_overrides', {}) if decoder_tuning is not None else {},
        line_count=line_count,
    ))
    shared_seed.extend(_serialise_line_decoder_override_slots(
        decoder_tuning.get('line_decoder_overrides', {}) if decoder_tuning is not None else {},
        line_count=line_count,
    ))
    shared_values = ctx.Array('d', shared_seed, lock=False)
    process = ctx.Process(target=_live_tuner_entry, args=(shared_values, title, tape_formats, line_count, config, tape_format, analysis_source, visible_sections))
    process.daemon = True
    process.start()
    return LiveTunerHandle(process, shared_values, tape_formats=tape_formats, line_count=line_count)
