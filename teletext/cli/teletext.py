import itertools
import math
import multiprocessing
import os
import pathlib
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time

import sys
from collections import defaultdict, deque

import click
from tqdm import tqdm

from teletext.charset import g0
from teletext.cli.clihelpers import packetreader, packetwriter, paginated, \
    progressparams, filterparams, carduser, chunkreader
from teletext.cli.livepause import PauseController
from teletext.file import FileChunker, LenWrapper
from teletext.mp import itermap
from teletext.packet import Packet, np
from teletext.stats import StatsList, MagHistogram, RowHistogram, Rejects, ErrorHistogram
from teletext.subpage import Subpage
from teletext import pipeline
from teletext.cli.training import training
from teletext.cli.vbi import vbi
from teletext.capturefix import CaptureCardFixer, normalise_fix_capture_card
from teletext.vbi.config import Config
from teletext.vbi.line import (
    ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
    ADAPTIVE_THRESHOLD_DEFAULT,
    AUTO_LINE_ALIGN_DEFAULT,
    AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
    AUTO_GAIN_CONTRAST_DEFAULT,
    AUTO_BLACK_LEVEL_COEFF_DEFAULT,
    AUTO_BLACK_LEVEL_DEFAULT,
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
    HUM_REMOVAL_DEFAULT,
    IMPULSE_FILTER_COEFF_DEFAULT,
    IMPULSE_FILTER_DEFAULT,
    LINE_STABILIZATION_COEFF_DEFAULT,
    LINE_STABILIZATION_DEFAULT,
    NOISE_REDUCTION_COEFF_DEFAULT,
    NOISE_REDUCTION_DEFAULT,
    QUALITY_THRESHOLD_DEFAULT,
    QUALITY_THRESHOLD_COEFF_DEFAULT,
    START_LOCK_DEFAULT,
    START_LOCK_COEFF_DEFAULT,
    SHARPNESS_COEFF_DEFAULT,
    TEMPORAL_DENOISE_COEFF_DEFAULT,
    TEMPORAL_DENOISE_DEFAULT,
    WOW_FLUTTER_COMPENSATION_DEFAULT,
    WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
    normalise_per_line_shift_map,
)


if os.name == 'nt' and platform.release() == '10' and platform.version() >= '10.0.14393':
    # Fix ANSI color in Windows 10 version 10.0.14393 (Windows Anniversary Update)
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)


BT8X8_DEFAULT_VBI_START = (7, 320)
BT8X8_DEFAULT_VBI_COUNT = (16, 16)


def parse_frame_lines(ctx, param, value):
    lines = []
    for group in value:
        for item in group.split(','):
            item = item.strip()
            if not item:
                raise click.BadParameter('Expected a comma-separated list such as 23,24,25.', ctx=ctx, param=param)
            try:
                lines.append(int(item, 10))
            except ValueError:
                raise click.BadParameter(f'{item!r} is not a valid line number.', ctx=ctx, param=param)
    return tuple(lines)


def parse_ignore_lines(ctx, param, value):
    return parse_frame_lines(ctx, param, value)


def parse_used_lines(ctx, param, value):
    return parse_frame_lines(ctx, param, value)


def vbiformatparams(f):
    options = [
        click.option('-vt', '--vbi-terminate-reset', 'vbi_terminate_reset', is_flag=True,
                     help='On exit, restore BT878 raw VBI start/count to 7/320 and 16/16 on live /dev/vbi* devices.'),
        click.option('-vc', '--vbi-count', type=(click.IntRange(1), click.IntRange(1)), default=None,
                     help='Raw VBI line count for field 1 and field 2, for example --vbi-count 17 17.'),
        click.option('-vs', '--vbi-start', type=(click.IntRange(1), click.IntRange(1)), default=None,
                     help='Raw VBI start line for field 1 and field 2, for example -vs 7 320.'),
    ]
    for decorator in reversed(options):
        f = decorator(f)
    return f


def parse_per_line_shift_options(ctx, param, value):
    shifts = {}
    for group in value:
        for item in str(group).split(','):
            item = item.strip()
            if not item:
                raise click.BadParameter('Expected LINE:SHIFT such as 6:+3 or 12:-2.5.', ctx=ctx, param=param)
            if ':' not in item:
                raise click.BadParameter(f'{item!r} is not a valid LINE:SHIFT value.', ctx=ctx, param=param)
            line_text, shift_text = item.split(':', 1)
            try:
                line = int(line_text, 10)
                shift = float(shift_text)
            except ValueError as exc:
                raise click.BadParameter(f'{item!r} is not a valid LINE:SHIFT value.', ctx=ctx, param=param) from exc
            if line < 1 or line > 32:
                raise click.BadParameter(f'Line number must be between 1 and 32, got {line}.', ctx=ctx, param=param)
            if abs(shift) <= 1e-9:
                shifts.pop(line, None)
            else:
                shifts[line] = shift
    return shifts


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


def parse_signal_value_coeff(raw_value, default_coeff, *, ctx=None, param=None):
    if isinstance(raw_value, (tuple, list)) and len(raw_value) == 2:
        value_text = raw_value[0]
        coeff_text = raw_value[1]
    else:
        text = str(raw_value).strip()
        if not text:
            raise click.BadParameter('Expected VALUE or VALUE/COEFF.', ctx=ctx, param=param)
        if '/' in text:
            value_text, coeff_text = text.split('/', 1)
        else:
            value_text, coeff_text = text, None

    try:
        value = int(str(value_text).strip(), 10)
    except (TypeError, ValueError) as exc:
        raise click.BadParameter(f'Invalid value {value_text!r}. Expected 0..100 or 0..100/COEFF.', ctx=ctx, param=param) from exc
    if value < 0 or value > 100:
        raise click.BadParameter(f'Value must be between 0 and 100, got {value}.', ctx=ctx, param=param)

    if coeff_text in (None, ''):
        coeff = float(default_coeff)
    else:
        try:
            coeff = float(str(coeff_text).strip())
        except (TypeError, ValueError) as exc:
            raise click.BadParameter(f'Invalid coefficient {coeff_text!r}.', ctx=ctx, param=param) from exc
        if coeff < 0:
            raise click.BadParameter(f'Coefficient must be zero or greater, got {coeff}.', ctx=ctx, param=param)

    return value, coeff


def signal_value_coeff_callback(default_coeff):
    def callback(ctx, param, value):
        return parse_signal_value_coeff(value, default_coeff, ctx=ctx, param=param)
    return callback


def resolve_signal_value_coeff(spec, default_value, default_coeff):
    if spec is None:
        return int(default_value), float(default_coeff)
    if isinstance(spec, str):
        return parse_signal_value_coeff(spec, default_coeff)
    if isinstance(spec, (tuple, list)) and len(spec) == 2:
        return parse_signal_value_coeff(spec, default_coeff)
    value = int(spec)
    if value < 0 or value > 100:
        raise ValueError(f'Value must be between 0 and 100, got {value}.')
    return value, float(default_coeff)


def signalcontrolparams(f):
    options = [
        click.option('-bn', '--brightness', type=str, callback=signal_value_coeff_callback(BRIGHTNESS_COEFF_DEFAULT), default='50', show_default=True, metavar='VALUE[/COEFF]',
                     help='Software brightness control for VBI samples. Use VALUE or VALUE/COEFF. 50 = unchanged.'),
        click.option('-sp', '--sharpness', type=str, callback=signal_value_coeff_callback(SHARPNESS_COEFF_DEFAULT), default='50', show_default=True, metavar='VALUE[/COEFF]',
                     help='Software sharpness control for VBI samples. Use VALUE or VALUE/COEFF. 50 = unchanged.'),
        click.option('-gn', '--gain', type=str, callback=signal_value_coeff_callback(GAIN_COEFF_DEFAULT), default='50', show_default=True, metavar='VALUE[/COEFF]',
                     help='Software gain control for VBI samples. Use VALUE or VALUE/COEFF. 50 = unchanged.'),
        click.option('-ct', '--contrast', type=str, callback=signal_value_coeff_callback(CONTRAST_COEFF_DEFAULT), default='50', show_default=True, metavar='VALUE[/COEFF]',
                     help='Software contrast control for VBI samples. Use VALUE or VALUE/COEFF. 50 = unchanged.'),
        click.option('-if', '--impulse-filter', type=str, callback=signal_value_coeff_callback(IMPULSE_FILTER_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Reduce short impulse spikes in VBI lines. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-td', '--temporal-denoise', type=str, callback=signal_value_coeff_callback(TEMPORAL_DENOISE_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Blend the same VBI line across neighbouring frames. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-nr', '--noise-reduction', type=str, callback=signal_value_coeff_callback(NOISE_REDUCTION_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Reduce fine high-frequency VBI noise. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-hm', '--hum-removal', type=str, callback=signal_value_coeff_callback(HUM_REMOVAL_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Reduce slow low-frequency hum or ripple in VBI lines. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-abl', '--auto-black-level', type=str, callback=signal_value_coeff_callback(AUTO_BLACK_LEVEL_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Automatically pull the line background back toward black. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-hsm', '--head-switching-mask', type=str, callback=signal_value_coeff_callback(HEAD_SWITCHING_MASK_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Fade noisy trailing head-switching tail toward a stable baseline. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-lls', '--line-to-line-stabilization', type=str, callback=signal_value_coeff_callback(LINE_STABILIZATION_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Stabilize adjacent VBI lines toward a consistent level and range. Use VALUE or VALUE/COEFF. 0 = off.'),
        click.option('-agc', '--auto-gain-contrast', type=str, callback=signal_value_coeff_callback(AUTO_GAIN_CONTRAST_COEFF_DEFAULT), default='0', show_default=True, metavar='VALUE[/COEFF]',
                     help='Automatically normalize VBI gain and contrast toward a target range. Use VALUE or VALUE/COEFF. 0 = off.'),
    ]
    for decorator in reversed(options):
        f = decorator(f)
    return f


def linequalityparam(f):
    options = [
        click.option(
            '-lq', '--line-quality',
            type=int,
            default=QUALITY_THRESHOLD_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Teletext line detection threshold. Lower = more permissive, higher = stricter.',
        ),
        click.option(
            '-cl', '--clock-lock',
            type=int,
            default=CLOCK_LOCK_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Strength of clock run-in/framing lock. 50 = normal.',
        ),
        click.option(
            '-sl', '--start-lock',
            type=int,
            default=START_LOCK_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Bias rough start detection toward the configured start range. 50 = normal.',
        ),
        click.option(
            '-at', '--adaptive-threshold',
            type=int,
            default=ADAPTIVE_THRESHOLD_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Adaptive per-line bit normalization. 0 = off.',
        ),
        click.option(
            '-dr', '--dropout-repair',
            type=int,
            default=DROPOUT_REPAIR_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Repair short bit-level dropouts after line chopping. 0 = off.',
        ),
        click.option(
            '-wf', '--wow-flutter-compensation',
            type=int,
            default=WOW_FLUTTER_COMPENSATION_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Stabilize slow horizontal wow/flutter drift between repeated logical lines. 0 = off.',
        ),
        click.option(
            '-ala', '--auto-line-align',
            type=int,
            default=AUTO_LINE_ALIGN_DEFAULT,
            show_default=True,
            metavar='0-100',
            help='Auto-align teletext starts so lines sit in the same column. 0 = off.',
        ),
        click.option(
            '-pls', '--per-line-shift',
            multiple=True,
            callback=parse_per_line_shift_options,
            metavar='LINE:SHIFT',
            help='Apply a manual horizontal shift to a 1-based logical VBI line, e.g. 6:+3 or 12:-2.',
        ),
        click.option(
            '-sq', '--show-quality/--no-show-quality',
            default=False,
            show_default=True,
            help='Show per-line quality overlay in VBI viewers and tune preview.',
        ),
        click.option(
            '-sr', '--show-rejects/--no-show-rejects',
            default=False,
            show_default=True,
            help='Show reject reasons for non-teletext lines in VBI viewers and tune preview.',
        ),
        click.option(
            '-ssc', '--show-start-clock/--no-show-start-clock',
            default=False,
            show_default=True,
            help='Show start and clock/framing markers in VBI viewers and tune preview.',
        ),
        click.option(
            '-scv', '--show-clock-visuals/--no-show-clock-visuals',
            default=False,
            show_default=True,
            help='Show a highlighted clock run-in window and clock center guide in VBI viewers and tune preview.',
        ),
        click.option(
            '-sav', '--show-alignment-visuals/--no-show-alignment-visuals',
            default=False,
            show_default=True,
            help='Show alignment target, pre-align start and final start guides in VBI viewers and tune preview.',
        ),
        click.option(
            '-sqm', '--show-quality-meter/--no-show-quality-meter',
            default=False,
            show_default=True,
            help='Show overall quality meter in VBI viewers and tune preview.',
        ),
        click.option(
            '-shg', '--show-histogram-graph/--no-show-histogram-graph',
            default=False,
            show_default=True,
            help='Show histogram and black-level graph in VBI viewers and tune preview.',
        ),
        click.option(
            '-sep', '--show-eye-pattern/--no-show-eye-pattern',
            default=False,
            show_default=True,
            help='Show eye-pattern and clock preview in VBI viewers and tune preview.',
        ),
    ]
    for decorator in reversed(options):
        f = decorator(f)
    return f


def previewtuneparams(f):
    options = [
        click.option('-vtn', '--vbi-tune', is_flag=True, help='Open the VBI tuning window before starting processing.'),
        click.option('-vtnl', '--vbi-tune-live', is_flag=True, help='Open the VBI tuning window and apply changes live while deconvolving in the terminal.'),
    ]
    for decorator in reversed(options):
        f = decorator(f)
    return f


def fixcaptureparams(f):
    return click.option(
        '-fcc', '--fix-capture-card',
        type=(click.IntRange(1), click.IntRange(1)),
        default=None,
        help='Run ffmpeg on /dev/video0 for N seconds every M minutes to keep capture card levels stable.',
    )(f)


def parse_timer_value(ctx, param, value):
    if value is None:
        return None
    if isinstance(value, str):
        value = (value,)
    value = tuple(value)
    if not 1 <= len(value) <= 3:
        raise click.BadParameter('Expected one to three values such as 20s, 1m 20s, or 1h 2m 3s.', ctx=ctx, param=param)

    multipliers = {'h': 3600, 'm': 60, 's': 1}
    total_seconds = 0
    seen_suffixes = set()
    for raw_value in value:
        raw_value = raw_value.strip()
        if len(raw_value) < 2:
            raise click.BadParameter(f'Invalid timer value: {raw_value!r}.', ctx=ctx, param=param)
        suffix = raw_value[-1].lower()
        if suffix not in multipliers:
            raise click.BadParameter(f'Expected value ending with h, m, or s, got {raw_value!r}.', ctx=ctx, param=param)
        if suffix in seen_suffixes:
            raise click.BadParameter(f'Duplicate timer unit {suffix!r} in {raw_value!r}.', ctx=ctx, param=param)
        seen_suffixes.add(suffix)
        number = raw_value[:-1]
        if not number.isdigit():
            raise click.BadParameter(f'Invalid timer value: {raw_value!r}.', ctx=ctx, param=param)
        total_seconds += int(number) * multipliers[suffix]

    if total_seconds <= 0:
        raise click.BadParameter('Timer must be greater than zero.', ctx=ctx, param=param)
    return total_seconds


class TimerOption(click.Option):
    _duration_pattern = re.compile(r'^\d+[hmsHMS]$')

    def add_to_parser(self, parser, ctx):
        option_parser = None
        previous_parser_process = None

        def parser_process(value, state):
            values = [value]
            while state.rargs and len(values) < 3:
                candidate = state.rargs[0]
                if not self._duration_pattern.match(candidate):
                    break
                values.append(state.rargs.pop(0))
            previous_parser_process(tuple(values), state)

        retval = super().add_to_parser(parser, ctx)

        for name in self.opts:
            option_parser = parser._short_opt.get(name) or parser._long_opt.get(name)
            if option_parser is not None:
                previous_parser_process = option_parser.process
                option_parser.process = parser_process
                break

        return retval


def timerparam(f):
    return click.option(
        '-tm', '--timer',
        cls=TimerOption,
        type=click.UNPROCESSED,
        callback=parse_timer_value,
        default=None,
        metavar='TIME',
        help='Stop after the given active processing time, for example -tm 20s, -tm 1m 20s, or -tm 1h 2m 3s.',
    )(f)


URXVT_ENV_GUARD = 'TELETEXT_URXVT_ACTIVE'


def build_urxvt_command(argv, executable=None):
    if not argv:
        raise click.UsageError('Could not determine the current teletext command line.')

    stripped_args = []
    skipped = False
    for arg in argv[1:]:
        if not skipped and arg in ('-u', '--urxvt'):
            skipped = True
            continue
        stripped_args.append(arg)

    if executable is None:
        program = argv[0]
        if str(program).lower().endswith('.py'):
            executable = [sys.executable, program]
        else:
            executable = [program]
    elif isinstance(executable, str):
        executable = [executable]

    return [
        'urxvt',
        '-fg', 'white',
        '-bg', 'black',
        '-fn', 'teletext',
        '-fb', 'teletext',
        '-e',
        *executable,
        *stripped_args,
    ]


def launch_urxvt_command(argv):
    urxvt_path = shutil.which('urxvt')
    if urxvt_path is None:
        raise click.UsageError('urxvt is not installed or not available in PATH.')

    command = build_urxvt_command(argv)
    command[0] = urxvt_path
    env = os.environ.copy()
    env[URXVT_ENV_GUARD] = '1'
    subprocess.Popen(command, cwd=os.getcwd(), env=env)


def urxvt_callback(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return value
    if os.environ.get(URXVT_ENV_GUARD) == '1':
        return False
    launch_urxvt_command(sys.argv)
    ctx.exit()


def urxvtparam(f):
    return click.option(
        '-u', '--urxvt',
        is_flag=True,
        is_eager=True,
        expose_value=False,
        callback=urxvt_callback,
        help='Run this deconvolve command inside urxvt with the teletext font preset.',
    )(f)


def useful_frame_lines(config):
    return len(config.field_range) * 2


def normalise_frame_lines(lines, config, param_hint):
    lines = frozenset(lines)
    max_line = useful_frame_lines(config)
    invalid = sorted(line for line in lines if line < 1 or line > max_line)
    if invalid:
        invalid_str = ', '.join(str(line) for line in invalid)
        raise click.BadParameter(
            f'Line numbers must be between 1 and {max_line}. Invalid value(s): {invalid_str}.',
            param_hint=param_hint
        )
    return lines


def normalise_ignore_lines(ignore_lines, config):
    return normalise_frame_lines(ignore_lines, config, '--ignore-line')


def normalise_used_lines(used_lines, config):
    return normalise_frame_lines(used_lines, config, '--used-line')


def resolve_frame_line_selection(config, ignore_lines=(), used_lines=()):
    max_line = useful_frame_lines(config)
    all_lines = frozenset(range(1, max_line + 1))
    ignore_lines = normalise_ignore_lines(ignore_lines, config)
    used_lines = normalise_used_lines(used_lines, config)
    selected_lines = used_lines if used_lines else all_lines
    selected_lines = selected_lines - ignore_lines
    ignored_lines = all_lines - selected_lines
    return selected_lines, ignored_lines


def ignored_frame_line_byte_ranges(config, ignore_lines):
    field_range = list(config.field_range)
    lines_per_field = len(field_range)
    ranges = []

    for line in sorted(ignore_lines):
        field, line_in_field = divmod(line - 1, lines_per_field)
        raw_line = (field * config.field_lines) + field_range[line_in_field]
        start = raw_line * config.line_bytes
        ranges.append((start, start + config.line_bytes))

    return ranges


def blank_ignored_frame_lines(chunk, ignored_ranges, preserve_tail=0):
    if not ignored_ranges:
        return chunk

    original_chunk = chunk
    chunk = bytearray(chunk)
    for start, end in ignored_ranges:
        chunk[start:end] = b'\x00' * (end - start)

    if preserve_tail:
        chunk[-preserve_tail:] = original_chunk[-preserve_tail:]

    return bytes(chunk)


def filter_ignored_chunks(chunks, config, ignore_lines):
    if not ignore_lines:
        return chunks

    lines_per_frame = useful_frame_lines(config)
    filtered = (
        (number, chunk)
        for number, chunk in chunks
        if ((number % lines_per_frame) + 1) not in ignore_lines
    )

    if hasattr(chunks, '__len__'):
        full_frames, remainder = divmod(len(chunks), lines_per_frame)
        kept = full_frames * (lines_per_frame - len(ignore_lines))
        kept += sum(1 for line in range(1, remainder + 1) if line not in ignore_lines)
        return LenWrapper(filtered, kept)

    return filtered


def format_timer_seconds(total_seconds):
    total_seconds = max(int(total_seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}h {minutes:02d}m {seconds:02d}s'


def wrap_live_iterable(iterable, pause_controller=None, timer_seconds=None, label='teletext'):
    deadline = None if timer_seconds is None else (time.monotonic() + float(timer_seconds))
    timer_notice_sent = False

    for item in iterable:
        if pause_controller is not None:
            paused_for = pause_controller.wait_if_paused()
            if deadline is not None:
                deadline += paused_for
        if deadline is not None and time.monotonic() >= deadline:
            if not timer_notice_sent:
                sys.stderr.write(f'[{label}] Timer elapsed after {format_timer_seconds(timer_seconds)}. Stopping.\n')
                sys.stderr.flush()
                timer_notice_sent = True
            break
        yield item


def _extract_preview_lines_from_frame(frame, config):
    field_range = list(config.field_range)
    lines_per_field = len(field_range)
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


def make_record_preview_provider(device, config):
    frame_size = config.line_bytes * config.field_lines * 2

    def reset_if_possible():
        try:
            device.seek(0)
            return True
        except (AttributeError, OSError):
            return False

    def provider():
        frame = device.read(frame_size)
        if len(frame) < frame_size:
            if not reset_if_possible():
                return []
            frame = device.read(frame_size)
            if len(frame) < frame_size:
                return []
        return _extract_preview_lines_from_frame(frame, config)

    return provider


def make_vbi_file_preview_provider(input_path, config, n_lines=None, frame_index=0):
    frame_size = config.line_bytes * config.field_lines * 2
    frame_index = max(int(frame_index), 0)

    def provider():
        with open(input_path, 'rb') as handle:
            handle.seek(frame_index * frame_size)
            frame = handle.read(frame_size)
        if len(frame) < frame_size:
            return []
        return _extract_preview_lines_from_frame(frame, config)

    return provider


def make_line_preview_provider(chunker, config, n_lines=None):
    preview_lines = n_lines or useful_frame_lines(config)

    def line_iterator():
        if n_lines is not None:
            return iter(chunker(config.line_bytes, n_lines, range(n_lines)))
        return iter(chunker(config.line_bytes, config.field_lines, config.field_range))

    iterator = line_iterator()

    def provider():
        nonlocal iterator
        lines = list(itertools.islice(iterator, preview_lines))
        if len(lines) < preview_lines:
            iterator = line_iterator()
            if not lines:
                lines = list(itertools.islice(iterator, preview_lines))
        return lines

    return provider


def chunker_input_path(chunker):
    path = getattr(chunker, '_teletext_input_path', None)
    if not path:
        return None
    return os.path.abspath(path)


def chunker_input_handle(chunker):
    return getattr(chunker, '_teletext_input_handle', None)


def open_tuning_dialog(
    title,
    brightness,
    sharpness,
    gain,
    contrast,
    brightness_coeff,
    sharpness_coeff,
    gain_coeff,
    contrast_coeff,
    impulse_filter,
    temporal_denoise,
    noise_reduction,
    hum_removal,
    auto_black_level,
    head_switching_mask,
    line_to_line_stabilization,
    auto_gain_contrast,
    impulse_filter_coeff,
    temporal_denoise_coeff,
    noise_reduction_coeff,
    hum_removal_coeff,
    auto_black_level_coeff,
    head_switching_mask_coeff,
    line_to_line_stabilization_coeff,
    auto_gain_contrast_coeff,
    preview_provider=None,
    config=None,
    tape_format='vhs',
    live=False,
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=None,
    fix_capture_card=None,
    analysis_source=None,
    visible_sections=None,
    timer_seconds=None,
    show_timer=False,
):
    try:
        from teletext.gui.vbituner import run_tuning_dialog
    except ModuleNotFoundError as e:
        if e.name == 'PyQt5':
            raise click.UsageError(f'{e.msg}. PyQt5 is not installed. VBI tuning window is not available.')
        raise

    result = run_tuning_dialog(
        title,
        controls=(
            brightness,
            sharpness,
            gain,
            contrast,
            brightness_coeff,
            sharpness_coeff,
            gain_coeff,
            contrast_coeff,
            impulse_filter,
            temporal_denoise,
            noise_reduction,
            hum_removal,
            auto_black_level,
            head_switching_mask,
            line_to_line_stabilization,
            auto_gain_contrast,
            impulse_filter_coeff,
            temporal_denoise_coeff,
            noise_reduction_coeff,
            hum_removal_coeff,
            auto_black_level_coeff,
            head_switching_mask_coeff,
            line_to_line_stabilization_coeff,
            auto_gain_contrast_coeff,
        ),
        preview_provider=preview_provider,
        config=config,
        tape_format=tape_format,
        live=live,
        decoder_tuning=decoder_tuning,
        tape_formats=tape_formats,
        line_selection=line_selection,
        line_count=useful_frame_lines(config) if line_count is None and config is not None else (line_count or 32),
        fix_capture_card=fix_capture_card,
        analysis_source=analysis_source,
        visible_sections=visible_sections,
        timer_seconds=timer_seconds,
        show_timer=show_timer,
    )
    return result


def open_live_tuner(
    title,
    brightness,
    sharpness,
    gain,
    contrast,
    brightness_coeff,
    sharpness_coeff,
    gain_coeff,
    contrast_coeff,
    impulse_filter,
    temporal_denoise,
    noise_reduction,
    hum_removal,
    auto_black_level,
    head_switching_mask,
    line_to_line_stabilization,
    auto_gain_contrast,
    impulse_filter_coeff,
    temporal_denoise_coeff,
    noise_reduction_coeff,
    hum_removal_coeff,
    auto_black_level_coeff,
    head_switching_mask_coeff,
    line_to_line_stabilization_coeff,
    auto_gain_contrast_coeff,
    config=None,
    tape_format='vhs',
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=32,
    fix_capture_card=None,
    analysis_source=None,
    visible_sections=None,
):
    try:
        from teletext.gui.vbituner import launch_live_tuner
    except ModuleNotFoundError as e:
        if e.name == 'PyQt5':
            raise click.UsageError(f'{e.msg}. PyQt5 is not installed. VBI tuning window is not available.')
        raise

    return launch_live_tuner(
        title,
        controls=(
            brightness,
            sharpness,
            gain,
            contrast,
            brightness_coeff,
            sharpness_coeff,
            gain_coeff,
            contrast_coeff,
            impulse_filter,
            temporal_denoise,
            noise_reduction,
            hum_removal,
            auto_black_level,
            head_switching_mask,
            line_to_line_stabilization,
            auto_gain_contrast,
            impulse_filter_coeff,
            temporal_denoise_coeff,
            noise_reduction_coeff,
            hum_removal_coeff,
            auto_black_level_coeff,
            head_switching_mask_coeff,
            line_to_line_stabilization_coeff,
            auto_gain_contrast_coeff,
        ),
        config=config,
        tape_format=tape_format,
        decoder_tuning=decoder_tuning,
        tape_formats=tape_formats,
        line_selection=line_selection,
        line_count=line_count,
        fix_capture_card=fix_capture_card,
        analysis_source=analysis_source,
        visible_sections=visible_sections,
    )


def current_decoder_tuning(
    config,
    tape_format,
    quality_threshold=QUALITY_THRESHOLD_DEFAULT,
    quality_threshold_coeff=QUALITY_THRESHOLD_COEFF_DEFAULT,
    clock_lock=CLOCK_LOCK_DEFAULT,
    clock_lock_coeff=CLOCK_LOCK_COEFF_DEFAULT,
    start_lock=START_LOCK_DEFAULT,
    start_lock_coeff=START_LOCK_COEFF_DEFAULT,
    adaptive_threshold=ADAPTIVE_THRESHOLD_DEFAULT,
    adaptive_threshold_coeff=ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
    dropout_repair=DROPOUT_REPAIR_DEFAULT,
    dropout_repair_coeff=DROPOUT_REPAIR_COEFF_DEFAULT,
    wow_flutter_compensation=WOW_FLUTTER_COMPENSATION_DEFAULT,
    wow_flutter_compensation_coeff=WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
    auto_line_align=AUTO_LINE_ALIGN_DEFAULT,
    per_line_shift=None,
    line_control_overrides=None,
    line_decoder_overrides=None,
    show_quality=False,
    show_rejects=False,
    show_start_clock=False,
    show_clock_visuals=False,
    show_alignment_visuals=False,
    show_quality_meter=False,
    show_histogram_graph=False,
    show_eye_pattern=False,
):
    quality_threshold, quality_threshold_coeff = resolve_signal_value_coeff(
        quality_threshold,
        QUALITY_THRESHOLD_DEFAULT,
        quality_threshold_coeff,
    )
    clock_lock, clock_lock_coeff = resolve_signal_value_coeff(
        clock_lock,
        CLOCK_LOCK_DEFAULT,
        clock_lock_coeff,
    )
    start_lock, start_lock_coeff = resolve_signal_value_coeff(
        start_lock,
        START_LOCK_DEFAULT,
        start_lock_coeff,
    )
    adaptive_threshold, adaptive_threshold_coeff = resolve_signal_value_coeff(
        adaptive_threshold,
        ADAPTIVE_THRESHOLD_DEFAULT,
        adaptive_threshold_coeff,
    )
    dropout_repair, dropout_repair_coeff = resolve_signal_value_coeff(
        dropout_repair,
        DROPOUT_REPAIR_DEFAULT,
        dropout_repair_coeff,
    )
    wow_flutter_compensation, wow_flutter_compensation_coeff = resolve_signal_value_coeff(
        wow_flutter_compensation,
        WOW_FLUTTER_COMPENSATION_DEFAULT,
        wow_flutter_compensation_coeff,
    )
    return {
        'tape_format': tape_format,
        'extra_roll': int(config.extra_roll),
        'line_start_range': tuple(int(value) for value in config.line_start_range),
        'quality_threshold': int(quality_threshold),
        'quality_threshold_coeff': float(quality_threshold_coeff),
        'clock_lock': int(clock_lock),
        'clock_lock_coeff': float(clock_lock_coeff),
        'start_lock': int(start_lock),
        'start_lock_coeff': float(start_lock_coeff),
        'adaptive_threshold': int(adaptive_threshold),
        'adaptive_threshold_coeff': float(adaptive_threshold_coeff),
        'dropout_repair': int(dropout_repair),
        'dropout_repair_coeff': float(dropout_repair_coeff),
        'wow_flutter_compensation': int(wow_flutter_compensation),
        'wow_flutter_compensation_coeff': float(wow_flutter_compensation_coeff),
        'auto_line_align': int(auto_line_align),
        'per_line_shift': normalise_per_line_shift_map(per_line_shift, maximum_line=max(useful_frame_lines(config), 32)),
        'line_control_overrides': {
            int(line): tuple(values)
            for line, values in dict(line_control_overrides or {}).items()
        },
        'line_decoder_overrides': {
            int(line): dict(values)
            for line, values in dict(line_decoder_overrides or {}).items()
        },
        'show_quality': bool(show_quality),
        'show_rejects': bool(show_rejects),
        'show_start_clock': bool(show_start_clock),
        'show_clock_visuals': bool(show_clock_visuals),
        'show_alignment_visuals': bool(show_alignment_visuals),
        'show_quality_meter': bool(show_quality_meter),
        'show_histogram_graph': bool(show_histogram_graph),
        'show_eye_pattern': bool(show_eye_pattern),
    }


def apply_decoder_tuning(
    config,
    tape_format,
    quality_threshold,
    quality_threshold_coeff,
    clock_lock,
    clock_lock_coeff,
    start_lock,
    start_lock_coeff,
    adaptive_threshold,
    adaptive_threshold_coeff,
    dropout_repair,
    dropout_repair_coeff,
    wow_flutter_compensation,
    wow_flutter_compensation_coeff,
    auto_line_align,
    per_line_shift,
    line_control_overrides,
    line_decoder_overrides,
    show_quality,
    show_rejects,
    show_start_clock,
    show_clock_visuals,
    show_alignment_visuals,
    show_quality_meter,
    show_histogram_graph,
    show_eye_pattern,
    decoder_tuning,
):
    if decoder_tuning is None:
        return (
            config,
            tape_format,
            quality_threshold,
            quality_threshold_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
        )
    updated_config = config.retuned(
        extra_roll=int(decoder_tuning['extra_roll']),
        line_start_range=tuple(int(value) for value in decoder_tuning['line_start_range']),
    )
    return (
        updated_config,
        decoder_tuning['tape_format'],
        int(decoder_tuning['quality_threshold']),
        float(decoder_tuning['quality_threshold_coeff']),
        int(decoder_tuning.get('clock_lock', clock_lock)),
        float(decoder_tuning.get('clock_lock_coeff', clock_lock_coeff)),
        int(decoder_tuning.get('start_lock', start_lock)),
        float(decoder_tuning.get('start_lock_coeff', start_lock_coeff)),
        int(decoder_tuning.get('adaptive_threshold', adaptive_threshold)),
        float(decoder_tuning.get('adaptive_threshold_coeff', adaptive_threshold_coeff)),
        int(decoder_tuning.get('dropout_repair', dropout_repair)),
        float(decoder_tuning.get('dropout_repair_coeff', dropout_repair_coeff)),
        int(decoder_tuning.get('wow_flutter_compensation', wow_flutter_compensation)),
        float(decoder_tuning.get('wow_flutter_compensation_coeff', wow_flutter_compensation_coeff)),
        int(decoder_tuning.get('auto_line_align', auto_line_align)),
        normalise_per_line_shift_map(decoder_tuning.get('per_line_shift', per_line_shift), maximum_line=max(useful_frame_lines(updated_config), 32)),
        {
            int(line): tuple(values)
            for line, values in dict(decoder_tuning.get('line_control_overrides', line_control_overrides) or {}).items()
        },
        {
            int(line): dict(values)
            for line, values in dict(decoder_tuning.get('line_decoder_overrides', line_decoder_overrides) or {}).items()
        },
        bool(decoder_tuning.get('show_quality', show_quality)),
        bool(decoder_tuning.get('show_rejects', show_rejects)),
        bool(decoder_tuning.get('show_start_clock', show_start_clock)),
        bool(decoder_tuning.get('show_clock_visuals', show_clock_visuals)),
        bool(decoder_tuning.get('show_alignment_visuals', show_alignment_visuals)),
        bool(decoder_tuning.get('show_quality_meter', show_quality_meter)),
        bool(decoder_tuning.get('show_histogram_graph', show_histogram_graph)),
        bool(decoder_tuning.get('show_eye_pattern', show_eye_pattern)),
    )


def current_line_selection(config, ignore_lines=(), used_lines=()):
    selected_lines, _ = resolve_frame_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
    return frozenset(sorted(selected_lines))


def current_fix_capture_card(fix_capture_card):
    if fix_capture_card is None:
        return normalise_fix_capture_card(None)
    return normalise_fix_capture_card({
        'enabled': True,
        'seconds': int(fix_capture_card[0]),
        'interval_minutes': int(fix_capture_card[1]),
    })


def current_signal_controls(
    brightness,
    sharpness,
    gain,
    contrast,
    impulse_filter,
    temporal_denoise,
    noise_reduction,
    hum_removal,
    auto_black_level,
    head_switching_mask,
    line_to_line_stabilization,
    auto_gain_contrast,
):
    brightness, brightness_coeff = resolve_signal_value_coeff(brightness, 50, BRIGHTNESS_COEFF_DEFAULT)
    sharpness, sharpness_coeff = resolve_signal_value_coeff(sharpness, 50, SHARPNESS_COEFF_DEFAULT)
    gain, gain_coeff = resolve_signal_value_coeff(gain, 50, GAIN_COEFF_DEFAULT)
    contrast, contrast_coeff = resolve_signal_value_coeff(contrast, 50, CONTRAST_COEFF_DEFAULT)
    impulse_filter, impulse_filter_coeff = resolve_signal_value_coeff(impulse_filter, IMPULSE_FILTER_DEFAULT, IMPULSE_FILTER_COEFF_DEFAULT)
    temporal_denoise, temporal_denoise_coeff = resolve_signal_value_coeff(temporal_denoise, TEMPORAL_DENOISE_DEFAULT, TEMPORAL_DENOISE_COEFF_DEFAULT)
    noise_reduction, noise_reduction_coeff = resolve_signal_value_coeff(noise_reduction, NOISE_REDUCTION_DEFAULT, NOISE_REDUCTION_COEFF_DEFAULT)
    hum_removal, hum_removal_coeff = resolve_signal_value_coeff(hum_removal, HUM_REMOVAL_DEFAULT, HUM_REMOVAL_COEFF_DEFAULT)
    auto_black_level, auto_black_level_coeff = resolve_signal_value_coeff(auto_black_level, AUTO_BLACK_LEVEL_DEFAULT, AUTO_BLACK_LEVEL_COEFF_DEFAULT)
    head_switching_mask, head_switching_mask_coeff = resolve_signal_value_coeff(head_switching_mask, HEAD_SWITCHING_MASK_DEFAULT, HEAD_SWITCHING_MASK_COEFF_DEFAULT)
    line_to_line_stabilization, line_to_line_stabilization_coeff = resolve_signal_value_coeff(line_to_line_stabilization, LINE_STABILIZATION_DEFAULT, LINE_STABILIZATION_COEFF_DEFAULT)
    auto_gain_contrast, auto_gain_contrast_coeff = resolve_signal_value_coeff(auto_gain_contrast, AUTO_GAIN_CONTRAST_DEFAULT, AUTO_GAIN_CONTRAST_COEFF_DEFAULT)
    return (
        int(brightness),
        int(sharpness),
        int(gain),
        int(contrast),
        float(brightness_coeff),
        float(sharpness_coeff),
        float(gain_coeff),
        float(contrast_coeff),
        int(impulse_filter),
        int(temporal_denoise),
        int(noise_reduction),
        int(hum_removal),
        int(auto_black_level),
        int(head_switching_mask),
        int(line_to_line_stabilization),
        int(auto_gain_contrast),
        float(impulse_filter_coeff),
        float(temporal_denoise_coeff),
        float(noise_reduction_coeff),
        float(hum_removal_coeff),
        float(auto_black_level_coeff),
        float(head_switching_mask_coeff),
        float(line_to_line_stabilization_coeff),
        float(auto_gain_contrast_coeff),
    )


def normalise_signal_controls_tuple(controls):
    controls = tuple(controls)
    if len(controls) >= 24:
        return controls[:24]
    if len(controls) == 18:
        return controls + (0, 0, 0, 1.0, 1.0, 1.0)
    if len(controls) == 16:
        controls = (
            controls[0], controls[1], controls[2], controls[3],
            controls[4], controls[5], controls[6], controls[7],
            controls[8], 0,
            controls[9], controls[10], controls[11],
            controls[12], 1.0,
            controls[13], controls[14], controls[15],
        )
        return normalise_signal_controls_tuple(controls)
    if len(controls) == 14:
        controls = (
            controls[0], controls[1], controls[2], controls[3],
            controls[4], controls[5], controls[6], controls[7],
            0, 0,
            controls[8], controls[9], controls[10],
            1.0, 1.0,
            controls[11], controls[12], controls[13],
        )
        return normalise_signal_controls_tuple(controls)
    if len(controls) == 11:
        controls = (
            controls[0], controls[1], controls[2], controls[3],
            controls[4], controls[5], controls[6], controls[7],
            0, 0,
            controls[8], controls[9], controls[10],
            1.0, 1.0, 1.0, 1.0, 1.0,
        )
        return normalise_signal_controls_tuple(controls)
    if len(controls) == 8:
        controls = controls + (0, 0, 0, 0, 0, 1.0, 1.0, 1.0, 1.0, 1.0)
        return normalise_signal_controls_tuple(controls)
    raise ValueError(f'Expected 8, 11, 14, 16, 18, or 24 signal control values, got {len(controls)}.')


def normalise_line_control_overrides(overrides, line_count=32):
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
            values = normalise_signal_controls_tuple(raw_values)
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


def normalise_line_decoder_overrides(overrides, line_count=32):
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


def frame_size_for_config(config):
    return config.line_bytes * config.field_lines * 2


def _ioc(direction, ioc_type, number, size):
    IOC_NRSHIFT = 0
    IOC_TYPESHIFT = 8
    IOC_SIZESHIFT = 16
    IOC_DIRSHIFT = 30
    return (
        (direction << IOC_DIRSHIFT)
        | (ioc_type << IOC_TYPESHIFT)
        | (number << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


def _iowr(ioc_type, number, size):
    IOC_READ = 2
    IOC_WRITE = 1
    return _ioc(IOC_READ | IOC_WRITE, ioc_type, number, size)


def _v4l2_vbi_structs():
    import ctypes

    class V4L2VBIFormat(ctypes.Structure):
        _fields_ = [
            ('sampling_rate', ctypes.c_uint32),
            ('offset', ctypes.c_uint32),
            ('samples_per_line', ctypes.c_uint32),
            ('sample_format', ctypes.c_uint32),
            ('start', ctypes.c_int32 * 2),
            ('count', ctypes.c_uint32 * 2),
            ('flags', ctypes.c_uint32),
            ('reserved', ctypes.c_uint32 * 2),
        ]

    class V4L2FormatUnion(ctypes.Union):
        _fields_ = [
            ('vbi', V4L2VBIFormat),
            ('raw_data', ctypes.c_uint8 * 200),
        ]

    class V4L2Format(ctypes.Structure):
        _fields_ = [
            ('type', ctypes.c_uint32),
            ('fmt', V4L2FormatUnion),
        ]

    return V4L2Format


def _parse_v4l2_ctl_vbi_format(text):
    patterns = {
        'sampling_rate': r'Sampling Rate\s*:\s*(\d+)',
        'offset': r'Offset\s*:\s*(\d+)',
        'samples_per_line': r'Samples per Line\s*:\s*(\d+)',
        'start0': r'Start 1st Field\s*:\s*(-?\d+)',
        'count0': r'Count 1st Field\s*:\s*(\d+)',
        'start1': r'Start 2nd Field\s*:\s*(-?\d+)',
        'count1': r'Count 2nd Field\s*:\s*(\d+)',
    }
    values = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            raise click.UsageError(f'Could not parse VBI format field {key!r} from v4l2-ctl output.')
        values[key] = int(match.group(1))

    return {
        'sampling_rate': values['sampling_rate'],
        'offset': values['offset'],
        'samples_per_line': values['samples_per_line'],
        'sample_format': 0,
        'start': (values['start0'], values['start1']),
        'count': (values['count0'], values['count1']),
        'flags': 0,
    }


def _run_v4l2_ctl(device_path, *args):
    v4l2_ctl = shutil.which('v4l2-ctl')
    if v4l2_ctl is None:
        raise click.UsageError('v4l2-ctl is required for --vbi-start/--vbi-count when ioctl access is unavailable.')

    command = [v4l2_ctl, '-d', device_path, *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or '').strip()
        raise click.UsageError(f'v4l2-ctl failed for {device_path}: {stderr}')
    return result.stdout


def get_vbi_capture_format_path(device_path):
    output = _run_v4l2_ctl(device_path, '--get-fmt-vbi')
    return _parse_v4l2_ctl_vbi_format(output)


def get_vbi_capture_format(device):
    import fcntl
    import ctypes

    V4L2Format = _v4l2_vbi_structs()
    V4L2_BUF_TYPE_VBI_CAPTURE = 4
    VIDIOC_G_FMT = _iowr(ord('V'), 4, ctypes.sizeof(V4L2Format))

    fmt = V4L2Format()
    fmt.type = V4L2_BUF_TYPE_VBI_CAPTURE
    try:
        fcntl.ioctl(device.fileno(), VIDIOC_G_FMT, fmt)
    except OSError:
        device_path = getattr(device, 'name', None)
        if not device_path:
            raise
        output = _run_v4l2_ctl(device_path, '--get-fmt-vbi')
        return _parse_v4l2_ctl_vbi_format(output)

    return {
        'sampling_rate': int(fmt.fmt.vbi.sampling_rate),
        'offset': int(fmt.fmt.vbi.offset),
        'samples_per_line': int(fmt.fmt.vbi.samples_per_line),
        'sample_format': int(fmt.fmt.vbi.sample_format),
        'start': (int(fmt.fmt.vbi.start[0]), int(fmt.fmt.vbi.start[1])),
        'count': (int(fmt.fmt.vbi.count[0]), int(fmt.fmt.vbi.count[1])),
        'flags': int(fmt.fmt.vbi.flags),
    }


def set_vbi_capture_format_path(device_path, capture_format):
    _run_v4l2_ctl(
        device_path,
        f'--set-fmt-vbi=samplingrate={int(capture_format["sampling_rate"])},'
        f'offset={int(capture_format["offset"])},'
        f'samplesperline={int(capture_format["samples_per_line"])},'
        f'start0={int(capture_format["start"][0])},count0={int(capture_format["count"][0])},'
        f'start1={int(capture_format["start"][1])},count1={int(capture_format["count"][1])}',
    )
    output = _run_v4l2_ctl(device_path, '--get-fmt-vbi')
    return _parse_v4l2_ctl_vbi_format(output)


def bt8x8_default_vbi_capture_format(reference_format):
    capture_format = dict(reference_format)
    capture_format['start'] = BT8X8_DEFAULT_VBI_START
    capture_format['count'] = BT8X8_DEFAULT_VBI_COUNT
    return capture_format


def set_vbi_capture_format(device, start, count):
    import fcntl
    import ctypes

    V4L2Format = _v4l2_vbi_structs()
    V4L2_BUF_TYPE_VBI_CAPTURE = 4
    VIDIOC_G_FMT = _iowr(ord('V'), 4, ctypes.sizeof(V4L2Format))
    VIDIOC_S_FMT = _iowr(ord('V'), 5, ctypes.sizeof(V4L2Format))

    fmt = V4L2Format()
    fmt.type = V4L2_BUF_TYPE_VBI_CAPTURE
    try:
        fcntl.ioctl(device.fileno(), VIDIOC_G_FMT, fmt)
        fmt.fmt.vbi.start[0] = int(start[0])
        fmt.fmt.vbi.start[1] = int(start[1])
        fmt.fmt.vbi.count[0] = int(count[0])
        fmt.fmt.vbi.count[1] = int(count[1])
        fcntl.ioctl(device.fileno(), VIDIOC_S_FMT, fmt)
    except OSError:
        device_path = getattr(device, 'name', None)
        if not device_path:
            raise
        current_format = get_vbi_capture_format(device)
        current_format = dict(current_format)
        current_format['start'] = (int(start[0]), int(start[1]))
        current_format['count'] = (int(count[0]), int(count[1]))
        return set_vbi_capture_format_path(device_path, current_format)
    return get_vbi_capture_format(device)


def _close_handle_quietly(handle):
    if handle is None:
        return
    close = getattr(handle, 'close', None)
    if close is None:
        return
    try:
        if not getattr(handle, 'closed', False):
            close()
    except OSError:
        pass


def restore_vbi_capture_format_path(device_path, capture_format, retries=10, delay=0.1):
    if not device_path or capture_format is None:
        return False

    last_error = None
    attempts = max(int(retries), 1)
    for attempt in range(attempts):
        try:
            set_vbi_capture_format_path(device_path, capture_format)
            return True
        except click.UsageError as exc:
            last_error = exc
            busy = 'Device or resource busy' in str(exc)
            if busy and (attempt + 1) < attempts:
                time.sleep(delay)
                continue
            if busy:
                return False
            raise

    if last_error is not None:
        raise last_error
    return False


def config_with_vbi_capture_format(config, capture_format):
    count0, count1 = capture_format['count']
    if count0 != count1:
        raise click.UsageError(
            f'VHSTTX currently requires the same VBI line count for both fields. '
            f'Driver returned {count0} and {count1}.'
        )

    return Config(
        card=config.card,
        line_length=int(capture_format['samples_per_line']),
        sample_rate=float(capture_format['sampling_rate']),
        sample_rate_adjust=0,
        line_start_range=config.line_start_range,
        extra_roll=getattr(config, 'extra_roll', 0),
        dtype=config.dtype,
        field_lines=int(count0),
        field_range=range(int(count0)),
    )


def apply_vbi_record_format(device, config, vbi_start=None, vbi_count=None):
    if vbi_start is None and vbi_count is None:
        return config, None
    if vbi_start is None or vbi_count is None:
        raise click.UsageError('Use both -vs/--vbi-start and --vbi-count together.')
    if os.name == 'nt':
        raise click.UsageError('Raw VBI format changes are only supported on Linux.')

    try:
        original_format = get_vbi_capture_format(device)
    except OSError as exc:
        raise click.UsageError(f'Could not read current VBI format from {device.name}: {exc}') from exc

    try:
        applied_format = set_vbi_capture_format(device, vbi_start, vbi_count)
    except OSError as exc:
        raise click.UsageError(f'Could not apply VBI format to {device.name}: {exc}') from exc

    requested_start = tuple(int(value) for value in vbi_start)
    requested_count = tuple(int(value) for value in vbi_count)
    if applied_format['start'] != requested_start or applied_format['count'] != requested_count:
        raise click.UsageError(
            f'Device {device.name} did not accept the requested VBI format. '
            f'Requested start/count {requested_start}/{requested_count}, '
            f'got {applied_format["start"]}/{applied_format["count"]}.'
        )

    return config_with_vbi_capture_format(config, applied_format), original_format


def apply_vbi_runtime_format(input_handle, input_path, config, vbi_start=None, vbi_count=None):
    if vbi_start is None and vbi_count is None:
        return config, None
    if vbi_start is None or vbi_count is None:
        raise click.UsageError('Use both -vs/--vbi-start and -vc/--vbi-count together.')

    requested_format = {
        'sampling_rate': int(config.sample_rate),
        'offset': 0,
        'samples_per_line': int(config.line_length),
        'sample_format': 0,
        'start': tuple(int(value) for value in vbi_start),
        'count': tuple(int(value) for value in vbi_count),
        'flags': 0,
    }

    is_vbi_device = bool(input_path) and os.path.abspath(input_path).startswith('/dev/vbi')
    if is_vbi_device:
        if input_handle is None:
            raise click.UsageError('Could not access the VBI device handle for applying --vbi-start/--vbi-count.')
        return apply_vbi_record_format(input_handle, config, vbi_start=vbi_start, vbi_count=vbi_count)

    return config_with_vbi_capture_format(config, requested_format), None


def estimate_vbi_size_megabytes(frame_count, config):
    return (max(int(frame_count), 0) * frame_size_for_config(config)) / (1024 * 1024)


def count_complete_frames(input_path, config):
    frame_size = frame_size_for_config(config)
    file_size = os.path.getsize(input_path)
    return file_size // frame_size


def _processed_frame_for_output(frame, config, controls, line_selection=None, temporal_state=None, decoder_tuning=None):
    from teletext.vbi.line import process_frame_bytes

    controls = normalise_signal_controls_tuple(controls)
    decoder_tuning = dict(decoder_tuning or {})
    preserve_tail = 4 if config.card == 'bt8x8' else 0
    all_lines = frozenset(range(1, useful_frame_lines(config) + 1))
    selected_lines = current_line_selection(config) if line_selection is None else frozenset(int(line) for line in line_selection)
    ignored_lines = all_lines - selected_lines
    ignored_ranges = ignored_frame_line_byte_ranges(config, ignored_lines)

    frame = process_frame_bytes(
        frame,
        config,
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
        preserve_tail=preserve_tail,
        temporal_state=temporal_state,
        line_control_overrides=decoder_tuning.get('line_control_overrides', {}),
    )
    if ignored_ranges:
        frame = blank_ignored_frame_lines(frame, ignored_ranges, preserve_tail=preserve_tail)
    return frame


def save_cropped_vbi(
    input_path,
    output_path,
    config,
    start_frame,
    end_frame,
    controls,
    line_selection=None,
    decoder_tuning=None,
):
    frame_size = frame_size_for_config(config)
    start_frame = max(int(start_frame), 0)
    end_frame = max(int(end_frame), start_frame)
    temporal_state = {}

    with open(input_path, 'rb') as source, open(output_path, 'wb') as output:
        source.seek(start_frame * frame_size)
        for _ in range(start_frame, end_frame + 1):
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection, temporal_state=temporal_state, decoder_tuning=decoder_tuning))


def delete_cropped_vbi(
    input_path,
    output_path,
    config,
    start_frame,
    end_frame,
    controls,
    line_selection=None,
    decoder_tuning=None,
):
    frame_size = frame_size_for_config(config)
    start_frame = max(int(start_frame), 0)
    end_frame = max(int(end_frame), start_frame)
    temporal_state = {}

    with open(input_path, 'rb') as source, open(output_path, 'wb') as output:
        frame_index = 0
        while True:
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            if start_frame <= frame_index <= end_frame:
                frame_index += 1
                continue
            output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection, temporal_state=temporal_state, decoder_tuning=decoder_tuning))
            frame_index += 1


def save_edited_vbi(
    input_path,
    output_path,
    config,
    controls,
    line_selection=None,
    decoder_tuning=None,
    cut_ranges=(),
    insertions=(),
):
    frame_size = frame_size_for_config(config)
    cut_ranges = tuple(sorted(cut_ranges))
    insertions = tuple(sorted(insertions, key=lambda item: (int(item['after_frame']), item['path'])))
    cut_index = 0
    insertion_index = 0
    temporal_state = {}

    def write_insertions(output, after_frame):
        nonlocal insertion_index
        while insertion_index < len(insertions) and int(insertions[insertion_index]['after_frame']) == after_frame:
            insertion = insertions[insertion_index]
            with open(insertion['path'], 'rb') as insert_file:
                while True:
                    frame = insert_file.read(frame_size)
                    if len(frame) < frame_size:
                        break
                    output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection, temporal_state=temporal_state, decoder_tuning=decoder_tuning))
            insertion_index += 1

    with open(input_path, 'rb') as source, open(output_path, 'wb') as output:
        frame_index = 0
        while True:
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            while cut_index < len(cut_ranges) and frame_index > cut_ranges[cut_index][1]:
                cut_index += 1
            if cut_index < len(cut_ranges):
                cut_start, cut_end = cut_ranges[cut_index]
                if cut_start <= frame_index <= cut_end:
                    write_insertions(output, frame_index)
                    frame_index += 1
                    continue
            output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection, temporal_state=temporal_state, decoder_tuning=decoder_tuning))
            write_insertions(output, frame_index)
            frame_index += 1

        while insertion_index < len(insertions):
            insertion = insertions[insertion_index]
            with open(insertion['path'], 'rb') as insert_file:
                while True:
                    frame = insert_file.read(frame_size)
                    if len(frame) < frame_size:
                        break
                    output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection, temporal_state=temporal_state, decoder_tuning=decoder_tuning))
            insertion_index += 1


def _repair_line_mappings(config, n_lines=None):
    if n_lines is None:
        field_range = list(config.field_range)
        frame_line_count = len(field_range) * 2
        mappings = []
        for logical_index in range(frame_line_count):
            field, line_in_field = divmod(logical_index, len(field_range))
            raw_line = (field * config.field_lines) + field_range[line_in_field]
            mappings.append((logical_index + 1, raw_line))
        return tuple(mappings)
    frame_line_count = max(int(n_lines), 0)
    return tuple((logical_index + 1, logical_index) for logical_index in range(frame_line_count))


def _shift_line_bytes(data, config, shift_samples, preserve_tail=0):
    from teletext.vbi.line import samples_from_bytes, samples_to_bytes

    shift = int(round(float(shift_samples)))
    payload = data[:-preserve_tail] if preserve_tail else data
    if shift == 0 or not payload:
        return data

    samples = samples_from_bytes(payload, config.dtype)
    if samples.size == 0:
        return data

    if abs(shift) >= samples.size:
        fill_value = float(samples[0] if shift > 0 else samples[-1])
        shifted = np.full_like(samples, fill_value)
    elif shift > 0:
        shifted = np.empty_like(samples)
        shifted[:shift] = samples[0]
        shifted[shift:] = samples[:-shift]
    else:
        shift = abs(shift)
        shifted = np.empty_like(samples)
        shifted[-shift:] = samples[-1]
        shifted[:-shift] = samples[shift:]

    result = samples_to_bytes(shifted, config.dtype)
    if preserve_tail:
        result += data[-preserve_tail:]
    return result


def _stabilization_decoder_tuning(config, tape_format, decoder_tuning=None):
    tuning = dict(decoder_tuning or current_decoder_tuning(config, tape_format))
    tuning['auto_line_align'] = AUTO_LINE_ALIGN_DEFAULT
    tuning['per_line_shift'] = {}
    cleaned_line_decoder_overrides = {}
    for line, values in dict(tuning.get('line_decoder_overrides', {})).items():
        cleaned = dict(values)
        cleaned.pop('auto_line_align', None)
        if cleaned:
            cleaned_line_decoder_overrides[int(line)] = cleaned
    tuning['line_decoder_overrides'] = cleaned_line_decoder_overrides
    return tuning


def _default_stabilize_target_center(config):
    expected_start = float((config.start_slice.start + config.start_slice.stop) / 2.0)
    expected_width = float(max(int(config.line_trim) - int(config.start_slice.start), 1))
    return (expected_start + (expected_width / 2.0)) * float(config.bit_width / 8.0)


def _default_stabilize_target_right_edge(config):
    expected_width = float(max(int(config.line_trim) - int(config.start_slice.start), 1))
    return _default_stabilize_target_center(config) + ((expected_width * float(config.bit_width / 8.0)) / 2.0)


def _line_kgi_window(line):
    samples = np.asarray(line.resampled, dtype=np.float32)
    if samples.size == 0:
        return None

    expected_width = max(int(line.config.line_trim) - int(line.config.start_slice.start), 1)
    default_start = int(line.config.start_slice.start)
    guessed_start = int(line.start) if line.start is not None else default_start
    start = max(guessed_start - 16, 0)
    stop = min(samples.size, max(guessed_start + expected_width + 64, start + 64))
    if stop <= start:
        return None

    segment = samples[start:stop]
    baseline_end = max(min(int(line.config.start_slice.start), samples.size), 1)
    baseline_slice = samples[:baseline_end]
    baseline = float(np.median(baseline_slice)) if baseline_slice.size else float(np.median(segment))
    envelope = np.abs(segment - baseline)
    if envelope.size >= 5:
        kernel = np.asarray((1.0, 2.0, 3.0, 2.0, 1.0), dtype=np.float32)
        kernel /= float(np.sum(kernel))
        envelope = np.convolve(envelope, kernel, mode='same')

    peak = float(np.max(envelope))
    fallback_left = float(max(min(guessed_start, samples.size - 1), 0))
    fallback_right = float(min(samples.size - 1, guessed_start + expected_width))
    if peak <= 1e-6:
        return fallback_left, fallback_right

    high_threshold = max(peak * 0.28, 3.0)
    strong = np.flatnonzero(envelope >= high_threshold)
    if strong.size == 0:
        return fallback_left, fallback_right

    split_points = np.where(np.diff(strong) > 6)[0]
    cluster_starts = np.concatenate(([0], split_points + 1))
    cluster_stops = np.concatenate((split_points + 1, [strong.size]))
    expected_right = guessed_start + expected_width
    best_cluster = None
    best_score = None
    for cluster_start, cluster_stop in zip(cluster_starts, cluster_stops):
        cluster = strong[int(cluster_start):int(cluster_stop)]
        if cluster.size == 0:
            continue
        local_left = int(cluster[0])
        local_right = int(cluster[-1])
        local_width = max(local_right - local_left, 1)
        local_centre = (local_left + local_right) / 2.0
        distance_penalty = abs((start + local_centre) - expected_right)
        score = (local_width * 2.0) - distance_penalty
        if best_score is None or score > best_score:
            best_score = score
            best_cluster = (local_left, local_right)

    if best_cluster is None:
        return fallback_left, fallback_right

    low_threshold = max(peak * 0.12, 1.5)
    low_active = envelope >= low_threshold
    local_left, local_right = best_cluster

    gap_run = 0
    expanded_left = int(local_left)
    index = int(local_left) - 1
    while index >= 0:
        if low_active[index]:
            expanded_left = index
            gap_run = 0
        else:
            gap_run += 1
            if gap_run >= 6:
                break
        index -= 1

    gap_run = 0
    expanded_right = int(local_right)
    index = int(local_right) + 1
    while index < low_active.size:
        if low_active[index]:
            expanded_right = index
            gap_run = 0
        else:
            gap_run += 1
            if gap_run >= 6:
                break
        index += 1

    left = float(start + int(expanded_left))
    right = float(start + int(expanded_right))
    if (right - left) < (expected_width * 0.35):
        return fallback_left, fallback_right
    return left, right


def _line_kgi_center(line):
    window = _line_kgi_window(line)
    if window is None:
        return None
    return (float(window[0]) + float(window[1])) / 2.0


def _line_kgi_end(line):
    window = _line_kgi_window(line)
    if window is None:
        return None
    return float(window[1])


def _line_kgi_default_left(line):
    samples = np.asarray(line.resampled, dtype=np.float32)
    if samples.size == 0:
        return None
    guessed_start = int(line.start) if line.start is not None else int(line.config.start_slice.start)
    return float(max(min(guessed_start, samples.size - 1), 0))


def _line_kgi_default_right(line):
    samples = np.asarray(line.resampled, dtype=np.float32)
    if samples.size == 0:
        return None
    expected_width = max(int(line.config.line_trim) - int(line.config.start_slice.start), 1)
    guessed_start = int(line.start) if line.start is not None else int(line.config.start_slice.start)
    return float(min(samples.size - 1, guessed_start + expected_width))


def _line_kgi_bounds(line):
    window = _line_kgi_window(line)
    if window is not None:
        return float(window[0]), float(window[1])
    left = _line_kgi_default_left(line)
    right = _line_kgi_default_right(line)
    if left is None or right is None:
        return None
    return float(left), float(right)


def _resolve_reference_stabilize_bounds(reference_mode, reference_line, cleaned_bounds, *, fallback_bounds=None):
    reference_mode = str(reference_mode or 'line').strip().lower()
    if reference_mode not in ('line', 'median'):
        reference_mode = 'line'

    reference_line = max(int(reference_line), 1)
    if reference_mode == 'line':
        reference = cleaned_bounds.get(reference_line)
        if reference is None:
            if fallback_bounds is None:
                raise ValueError('Reference line bounds are unavailable.')
            fallback_left, fallback_right = fallback_bounds
            reference = {
                'left': float(fallback_left),
                'right': float(fallback_right),
                'width': max(float(fallback_right) - float(fallback_left), 0.0),
            }
        return {
            'mode': 'line',
            'display_line': reference_line,
            'source_lines': [reference_line],
            'left': float(reference['left']),
            'right': float(reference['right']),
            'width': max(float(reference['width']), 0.0),
        }

    if not cleaned_bounds:
        if fallback_bounds is None:
            raise ValueError('Reference bounds are unavailable.')
        fallback_left, fallback_right = fallback_bounds
        return {
            'mode': 'median',
            'display_line': reference_line,
            'source_lines': [],
            'left': float(fallback_left),
            'right': float(fallback_right),
            'width': max(float(fallback_right) - float(fallback_left), 0.0),
        }

    widths = np.asarray([float(bounds['width']) for bounds in cleaned_bounds.values()], dtype=np.float32)
    rights = np.asarray([float(bounds['right']) for bounds in cleaned_bounds.values()], dtype=np.float32)
    median_width = float(np.median(widths))
    median_right = float(np.median(rights))
    width_mad = float(np.median(np.abs(widths - median_width))) if widths.size else 0.0
    right_mad = float(np.median(np.abs(rights - median_right))) if rights.size else 0.0
    width_tolerance = max(width_mad * 2.5, 12.0)
    right_tolerance = max(right_mad * 2.5, 12.0)

    source_lines = [
        int(logical_line)
        for logical_line, bounds in cleaned_bounds.items()
        if abs(float(bounds['width']) - median_width) <= width_tolerance
        and abs(float(bounds['right']) - median_right) <= right_tolerance
    ]
    if len(source_lines) < 3:
        scored_lines = sorted(
            cleaned_bounds,
            key=lambda logical_line: (
                abs(float(cleaned_bounds[logical_line]['right']) - median_right)
                + abs(float(cleaned_bounds[logical_line]['width']) - median_width)
            ),
        )
        keep_count = max(1, min(len(scored_lines), 8))
        source_lines = [int(line) for line in scored_lines[:keep_count]]

    reference_right = float(np.median(np.asarray(
        [float(cleaned_bounds[line]['right']) for line in source_lines],
        dtype=np.float32,
    )))
    reference_width = float(np.median(np.asarray(
        [float(cleaned_bounds[line]['width']) for line in source_lines],
        dtype=np.float32,
    )))
    reference_left = reference_right - reference_width
    display_line = min(
        source_lines,
        key=lambda logical_line: (
            abs(float(cleaned_bounds[logical_line]['right']) - reference_right)
            + abs(float(cleaned_bounds[logical_line]['width']) - reference_width)
        ),
    )
    return {
        'mode': 'median',
        'display_line': int(display_line),
        'source_lines': sorted(int(line) for line in source_lines),
        'left': reference_left,
        'right': reference_right,
        'width': max(reference_width, 0.0),
    }


def _build_reference_stabilize_analysis(reference_line, tolerance, per_line_bounds, *, fallback_bounds=None, reference_mode='line'):
    cleaned_bounds = {}
    for logical_line, bounds in dict(per_line_bounds or {}).items():
        if bounds is None:
            continue
        if isinstance(bounds, dict):
            left = bounds.get('left')
            right = bounds.get('right')
        else:
            left, right = bounds
        if left is None or right is None:
            continue
        left = float(left)
        right = float(right)
        if right < left:
            left, right = right, left
        cleaned_bounds[int(logical_line)] = {
            'left': left,
            'right': right,
            'width': max(right - left, 0.0),
        }

    tolerance = max(float(tolerance), 0.0)
    reference = _resolve_reference_stabilize_bounds(
        reference_mode,
        reference_line,
        cleaned_bounds,
        fallback_bounds=fallback_bounds,
    )
    reference_mode = str(reference.get('mode', 'line'))
    reference_line = max(int(reference.get('display_line', reference_line)), 1)
    reference_left = float(reference['left'])
    reference_right = float(reference['right'])
    reference_width = max(float(reference['width']), 0.0)
    reference_lines = [int(line) for line in reference.get('source_lines', [])]

    per_line = {}
    for logical_line, bounds in sorted(cleaned_bounds.items()):
        left = float(bounds['left'])
        right = float(bounds['right'])
        width = max(float(bounds['width']), 0.0)
        raw_shift = int(round(reference_right - right))
        shifted_left = left + float(raw_shift)
        shifted_right = right + float(raw_shift)
        gap_left = max(0.0, reference_width - width)
        overflow_left = max(0.0, width - reference_width)
        status = 'ok' if gap_left <= tolerance else 'needs-repair'
        per_line[int(logical_line)] = {
            'left': left,
            'right': right,
            'width': width,
            'raw_shift': raw_shift,
            'shift': raw_shift,
            'shifted_left': shifted_left,
            'shifted_right': shifted_right,
            'gap_left': gap_left,
            'overflow_left': overflow_left,
            'status': status,
        }

    sorted_lines = sorted(per_line)
    for logical_line in sorted_lines:
        if logical_line == reference_line:
            continue
        entry = per_line[logical_line]
        prev_entry = per_line.get(logical_line - 1)
        next_entry = per_line.get(logical_line + 1)
        if prev_entry is None or next_entry is None:
            continue
        neighbour_median = int(round(float(np.median(np.asarray((prev_entry['raw_shift'], next_entry['raw_shift']), dtype=np.float32)))))
        if abs(int(entry['raw_shift']) - neighbour_median) > max(int(round(tolerance * 3.0)), 8):
            entry['status'] = 'needs-repair'

    for logical_line in sorted_lines:
        entry = per_line[logical_line]
        if logical_line == reference_line:
            continue
        raw_shift = int(entry['raw_shift'])
        if entry['status'] != 'needs-repair':
            prev_entry = per_line.get(logical_line - 1)
            next_entry = per_line.get(logical_line + 1)
            if prev_entry is None or next_entry is None:
                continue
            prev_shift = int(prev_entry['raw_shift'])
            next_shift = int(next_entry['raw_shift'])
            neighbour_delta = abs(prev_shift - next_shift)
            if neighbour_delta > max(int(round(tolerance * 4.0)), 20):
                continue
            smoothed_shift = int(round(float(np.median(np.asarray((prev_shift, raw_shift, next_shift), dtype=np.float32)))))
            if abs(raw_shift - smoothed_shift) > max(int(round(tolerance * 2.0)), 6):
                entry['shift'] = smoothed_shift
                entry['shifted_left'] = float(entry['left']) + float(smoothed_shift)
                entry['shifted_right'] = float(entry['right']) + float(smoothed_shift)
            continue

        prev_anchor_line = None
        next_anchor_line = None
        for neighbour in range(logical_line - 1, min(sorted_lines) - 1, -1):
            neighbour_entry = per_line.get(neighbour)
            if neighbour_entry is not None and neighbour_entry['status'] == 'ok':
                prev_anchor_line = neighbour
                break
        for neighbour in range(logical_line + 1, max(sorted_lines) + 1):
            neighbour_entry = per_line.get(neighbour)
            if neighbour_entry is not None and neighbour_entry['status'] == 'ok':
                next_anchor_line = neighbour
                break

        interpolated_shift = raw_shift
        if prev_anchor_line is not None and next_anchor_line is not None and next_anchor_line != prev_anchor_line:
            prev_shift = int(per_line[prev_anchor_line]['shift'])
            next_shift = int(per_line[next_anchor_line]['shift'])
            ratio = float(logical_line - prev_anchor_line) / float(next_anchor_line - prev_anchor_line)
            interpolated_shift = int(round(prev_shift + ((next_shift - prev_shift) * ratio)))
        elif prev_anchor_line is not None:
            interpolated_shift = int(per_line[prev_anchor_line]['shift'])
        elif next_anchor_line is not None:
            interpolated_shift = int(per_line[next_anchor_line]['shift'])

        entry['shift'] = interpolated_shift
        entry['shifted_left'] = float(entry['left']) + float(interpolated_shift)
        entry['shifted_right'] = float(entry['right']) + float(interpolated_shift)

    return {
        'reference_mode': reference_mode,
        'reference_line': reference_line,
        'reference_lines': reference_lines,
        'tolerance': tolerance,
        'reference_left': reference_left,
        'reference_right': reference_right,
        'reference_width': reference_width,
        'per_line': per_line,
    }


def _analyse_stabilize_vbi_reference(
    input_path,
    config,
    tape_format,
    controls,
    selected_lines,
    decoder_tuning,
    start_frame,
    end_frame,
    *,
    n_lines=None,
    reference_mode='line',
    reference_line=1,
    tolerance=3,
    progress_callback=None,
):
    frame_size = frame_size_for_config(config)
    expected_width = float(max(int(config.line_trim) - int(config.start_slice.start), 1))
    raw_sample_scale = float(config.bit_width / 8.0)
    temporal_state = {}
    mappings = _repair_line_mappings(config, n_lines=n_lines)
    selected_lines = frozenset(int(line) for line in selected_lines)
    reference_line = max(int(reference_line), 1)
    analysed_bounds = defaultdict(lambda: {'left': [], 'right': []})
    total = max(int(end_frame - start_frame), 1)

    _configure_repair_lines(config, tape_format, controls, decoder_tuning)

    with open(input_path, 'rb') as source:
        source.seek(max(int(start_frame), 0) * frame_size)
        for frame_index in range(int(start_frame), int(end_frame)):
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            processed = _processed_frame_for_output(
                frame,
                config,
                controls,
                line_selection=selected_lines,
                temporal_state=temporal_state,
                decoder_tuning=decoder_tuning,
            )
            lines = _extract_frame_lines_for_repair(
                processed,
                config,
                frame_index,
                n_lines=n_lines,
            )
            line_map = {
                int(logical_line): line
                for (logical_line, _raw_line), line in zip(mappings, lines)
            }

            for logical_line, line in line_map.items():
                if int(logical_line) not in selected_lines:
                    continue
                bounds = _line_kgi_bounds(line)
                if bounds is None:
                    continue
                resampled_left, resampled_right = bounds
                analysed_bounds[int(logical_line)]['left'].append(float(resampled_left) * raw_sample_scale)
                analysed_bounds[int(logical_line)]['right'].append(float(resampled_right) * raw_sample_scale)

            if callable(progress_callback):
                progress_callback((frame_index - int(start_frame)) + 1, total)

    per_line_bounds = {}
    for logical_line in selected_lines:
        bounds = analysed_bounds.get(int(logical_line))
        if not bounds or not bounds['right']:
            continue
        right_values = bounds['right'] or []
        left_values = bounds['left'] or []
        line_right = float(np.median(np.asarray(right_values, dtype=np.float32)))
        if left_values:
            line_left = float(np.median(np.asarray(left_values, dtype=np.float32)))
        else:
            line_left = line_right - (expected_width * raw_sample_scale)
        per_line_bounds[int(logical_line)] = {
            'left': line_left,
            'right': line_right,
        }

    fallback_right = _default_stabilize_target_right_edge(config)
    fallback_left = fallback_right - (expected_width * raw_sample_scale)
    return _build_reference_stabilize_analysis(
        reference_line,
        tolerance,
        per_line_bounds,
        fallback_bounds=(fallback_left, fallback_right),
        reference_mode=reference_mode,
    )


def analyse_stabilize_repair_vbi(
    input_path,
    config,
    tape_format,
    controls,
    line_selection=None,
    decoder_tuning=None,
    n_lines=None,
    start_frame=0,
    frame_count=None,
    lock_mode='reference',
    target_center=None,
    target_right_edge=None,
    reference_mode='line',
    reference_line=1,
    tolerance=3,
    progress_callback=None,
):
    controls = normalise_signal_controls_tuple(controls)
    selected_lines = current_line_selection(config) if line_selection is None else frozenset(int(line) for line in line_selection)
    if not selected_lines:
        raise ValueError('Select at least one line before stabilizing VBI.')
    decoder_tuning = _stabilization_decoder_tuning(config, tape_format, decoder_tuning)
    total_frames = count_complete_frames(input_path, config)
    start_frame = max(int(start_frame), 0)
    if start_frame >= total_frames:
        raise ValueError('Start frame is outside the available VBI file range.')
    if frame_count is None:
        end_frame = total_frames
    else:
        end_frame = min(start_frame + max(int(frame_count), 1), total_frames)
    lock_mode = str(lock_mode or 'reference')
    if lock_mode != 'reference':
        return None
    return _analyse_stabilize_vbi_reference(
        input_path,
        config,
        tape_format,
        controls,
        selected_lines,
        decoder_tuning,
        start_frame,
        end_frame,
        n_lines=n_lines,
        reference_mode=reference_mode,
        reference_line=reference_line,
        tolerance=tolerance,
        progress_callback=progress_callback,
    )


def stabilize_repair_vbi(
    input_path,
    output_path,
    config,
    tape_format,
    controls,
    line_selection=None,
    decoder_tuning=None,
    global_shift=0,
    n_lines=None,
    start_frame=0,
    frame_count=None,
    lock_mode='target',
    target_center=None,
    target_right_edge=None,
    reference_mode='line',
    reference_line=1,
    tolerance=3,
    progress_callback=None,
):
    if os.path.abspath(str(input_path)) == os.path.abspath(str(output_path)):
        raise ValueError('Choose a different output file for stabilized VBI.')
    controls = normalise_signal_controls_tuple(controls)
    selected_lines = current_line_selection(config) if line_selection is None else frozenset(int(line) for line in line_selection)
    if not selected_lines:
        raise ValueError('Select at least one line before stabilizing VBI.')
    decoder_tuning = _stabilization_decoder_tuning(config, tape_format, decoder_tuning)
    frame_size = frame_size_for_config(config)
    total_frames = count_complete_frames(input_path, config)
    start_frame = max(int(start_frame), 0)
    if start_frame >= total_frames:
        raise ValueError('Start frame is outside the available VBI file range.')
    if frame_count is None:
        end_frame = total_frames
    else:
        end_frame = min(start_frame + max(int(frame_count), 1), total_frames)
    preserve_tail = 4 if config.card == 'bt8x8' else 0
    temporal_state = {}
    mappings = _repair_line_mappings(config, n_lines=n_lines)
    raw_sample_scale = float(config.bit_width / 8.0)
    expected_width = float(max(int(config.line_trim) - int(config.start_slice.start), 1))
    reference_line = max(int(reference_line), 1)
    lock_mode = str(lock_mode or 'target')
    analyse_total = max(int(end_frame - start_frame), 1)
    analysis = None
    analysed_per_line_shift = {}

    if lock_mode == 'reference':
        if callable(progress_callback):
            progress_callback(0, max(analyse_total, 1) * 2)
        def report_analyse(current, total):
            if callable(progress_callback):
                progress_callback(int(current), max(int(total), 1) * 2)

        analysis = _analyse_stabilize_vbi_reference(
            input_path,
            config,
            tape_format,
            controls,
            selected_lines,
            decoder_tuning,
            start_frame,
            end_frame,
            n_lines=n_lines,
            reference_mode=reference_mode,
            reference_line=reference_line,
            tolerance=tolerance,
            progress_callback=report_analyse,
        )
        analysed_per_line_shift = {
            int(logical_line): int(values.get('shift', 0))
            for logical_line, values in dict(analysis.get('per_line', {})).items()
        }
    else:
        target_centre_value = float(target_center) if target_center is not None else _default_stabilize_target_center(config)
        target_raw_centre = target_centre_value + float(global_shift)
        if target_right_edge is not None and float(target_right_edge) > 0.0:
            target_raw_right = float(target_right_edge)
        else:
            target_raw_right = target_raw_centre + ((expected_width * raw_sample_scale) / 2.0)

    _configure_repair_lines(config, tape_format, controls, decoder_tuning)

    with open(input_path, 'rb') as source, open(output_path, 'wb') as output:
        source.seek(start_frame * frame_size)
        for frame_index in range(start_frame, end_frame):
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            processed = bytearray(_processed_frame_for_output(
                frame,
                config,
                controls,
                line_selection=selected_lines,
                temporal_state=temporal_state,
                decoder_tuning=decoder_tuning,
            ))
            lines = _extract_frame_lines_for_repair(
                bytes(processed),
                config,
                frame_index,
                n_lines=n_lines,
            )
            if lock_mode != 'reference':
                frame_target_raw_centre = target_raw_centre
                frame_target_raw_right = target_raw_right
            if callable(progress_callback) and lock_mode != 'reference' and frame_index == start_frame:
                progress_callback(0, max(end_frame - start_frame, 1))
            for (logical_line, raw_line), line in zip(mappings, lines):
                if logical_line not in selected_lines:
                    continue
                current_resampled_right = _line_kgi_end(line)
                if current_resampled_right is None:
                    current_resampled_right = _line_kgi_default_right(line)
                if current_resampled_right is None:
                    continue
                if lock_mode == 'reference':
                    shift = int(analysed_per_line_shift.get(int(logical_line), 0))
                else:
                    current_resampled_centre = _line_kgi_center(line)
                    if current_resampled_centre is None:
                        current_resampled_centre = float(line.start) + (expected_width / 2.0)
                    current_raw_centre = float(current_resampled_centre) * raw_sample_scale
                    current_raw_right = float(current_resampled_right) * raw_sample_scale
                    shift = int(round(frame_target_raw_centre - current_raw_centre))
                    shifted_raw_right = current_raw_right + float(shift)
                    if shifted_raw_right > frame_target_raw_right:
                        shift -= int(math.ceil(shifted_raw_right - frame_target_raw_right))
                if shift == 0:
                    continue
                start = int(raw_line) * int(config.line_bytes)
                end = start + int(config.line_bytes)
                line_tail = preserve_tail if preserve_tail and int(raw_line) == ((int(config.field_lines) * 2) - 1) else 0
                processed[start:end] = _shift_line_bytes(processed[start:end], config, shift, preserve_tail=line_tail)
            output.write(bytes(processed))
            if callable(progress_callback):
                current = (frame_index - start_frame) + 1
                if lock_mode == 'reference':
                    progress_callback(analyse_total + current, analyse_total * 2)
                else:
                    progress_callback(current, max(end_frame - start_frame, 1))
    return analysis


def _frame_line_number(frame_index, logical_index, frame_line_count):
    return (int(frame_index) * int(frame_line_count)) + int(logical_index)


def _extract_frame_lines_for_repair(frame, config, frame_index, n_lines=None):
    from teletext.vbi.line import Line

    if n_lines is None:
        field_range = list(config.field_range)
        frame_line_count = len(field_range) * 2
        lines = []
        for logical_index in range(frame_line_count):
            field, line_in_field = divmod(logical_index, len(field_range))
            raw_line = (field * config.field_lines) + field_range[line_in_field]
            start = raw_line * config.line_bytes
            end = start + config.line_bytes
            if end > len(frame):
                break
            lines.append(Line(frame[start:end], _frame_line_number(frame_index, logical_index, frame_line_count)))
        return lines

    frame_line_count = int(n_lines)
    lines = []
    for logical_index in range(frame_line_count):
        start = logical_index * config.line_bytes
        end = start + config.line_bytes
        if end > len(frame):
            break
        lines.append(Line(frame[start:end], _frame_line_number(frame_index, logical_index, frame_line_count)))
    return lines


def _configure_repair_lines(config, tape_format, controls, decoder_tuning):
    from teletext.vbi.line import Line

    controls = normalise_signal_controls_tuple(controls)
    decoder_tuning = decoder_tuning or current_decoder_tuning(config, tape_format)
    Line.configure(
        config,
        force_cpu=True,
        tape_format=tape_format,
        brightness=int(controls[0]),
        sharpness=int(controls[1]),
        gain=int(controls[2]),
        contrast=int(controls[3]),
        brightness_coeff=float(controls[4]),
        sharpness_coeff=float(controls[5]),
        gain_coeff=float(controls[6]),
        contrast_coeff=float(controls[7]),
        impulse_filter=int(controls[8]),
        temporal_denoise=int(controls[9]),
        noise_reduction=int(controls[10]),
        hum_removal=int(controls[11]),
        auto_black_level=int(controls[12]),
        head_switching_mask=int(controls[13]),
        line_stabilization=int(controls[14]),
        auto_gain_contrast=int(controls[15]),
        impulse_filter_coeff=float(controls[16]),
        temporal_denoise_coeff=float(controls[17]),
        noise_reduction_coeff=float(controls[18]),
        hum_removal_coeff=float(controls[19]),
        auto_black_level_coeff=float(controls[20]),
        head_switching_mask_coeff=float(controls[21]),
        line_stabilization_coeff=float(controls[22]),
        auto_gain_contrast_coeff=float(controls[23]),
        quality_threshold=int(decoder_tuning.get('quality_threshold', QUALITY_THRESHOLD_DEFAULT)),
        quality_threshold_coeff=float(decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)),
        clock_lock=int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)),
        clock_lock_coeff=float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)),
        start_lock=int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT)),
        start_lock_coeff=float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)),
        adaptive_threshold=int(decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT)),
        adaptive_threshold_coeff=float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)),
        dropout_repair=int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)),
        dropout_repair_coeff=float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)),
        wow_flutter_compensation=int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)),
        wow_flutter_compensation_coeff=float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)),
        auto_line_align=int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
        per_line_shift=normalise_per_line_shift_map(
            decoder_tuning.get('per_line_shift', {}),
            maximum_line=max(int(useful_frame_lines(config)), 32),
        ),
        line_control_overrides=normalise_line_control_overrides(
            decoder_tuning.get('line_control_overrides', {}),
            line_count=max(int(useful_frame_lines(config)), 32),
        ),
        line_decoder_overrides=normalise_line_decoder_overrides(
            decoder_tuning.get('line_decoder_overrides', {}),
            line_count=max(int(useful_frame_lines(config)), 32),
        ),
    )


def _parse_repair_page_value(page_text):
    text = (str(page_text).strip() or '100').upper()
    if len(text) > 3:
        raise ValueError('Page must be a 3-digit hexadecimal value like 100 or 1AF.')
    try:
        value = int(text, 16)
    except ValueError as exc:
        raise ValueError(f'Invalid page {page_text!r}. Use a hexadecimal value like 100 or 1AF.') from exc
    if value < 0x100 or value > 0x8FF:
        raise ValueError(f'Page must be between 100 and 8FF, got {text}.')
    return value, text


def _parse_repair_subpage_value(subpage_text):
    text = str(subpage_text or '').strip().upper().replace('_', '')
    if not text:
        return None, ''
    if len(text) > 4:
        raise ValueError('Subpage must be a 4-digit hexadecimal value like 0001 or 002A.')
    try:
        value = int(text, 16)
    except ValueError as exc:
        raise ValueError(f'Invalid subpage {subpage_text!r}. Use a hexadecimal value like 0001 or 002A.') from exc
    if value < 0x0000 or value > 0x3F7F:
        raise ValueError(f'Subpage must be between 0000 and 3F7F, got {text}.')
    return value, f'{value:04X}'


def _render_repair_subpage_text(subpage, subpage_count=1, buffered_frames=0):
    lines = [
        (
            f"Page P{subpage.mrag.magazine}{subpage.header.page:02X} "
            f"Subpage {subpage.header.subpage:04X} | "
            f"Avg confidence {subpage.average_confidence:.1f} | "
            f"Buffered frames {int(buffered_frames)} | "
            f"Subpages {int(subpage_count)}"
        ),
        subpage.packet(0).to_ansi(colour=True),
    ]
    for row in range(1, 25):
        if subpage.has_packet(row):
            lines.append(subpage.packet(row).to_ansi(colour=True))
        else:
            lines.append(' ' * 40)
    return '\n'.join(lines)


def _repair_packet_confidence(packet, fallback_quality=0):
    return float(getattr(packet, '_line_confidence', fallback_quality))


def _repair_current_header_summary(packets):
    seen = []
    for packet in packets:
        if int(packet.mrag.row) != 0:
            continue
        label = f"P{int(packet.mrag.magazine)}{int(packet.header.page):02X}/{int(packet.header.subpage):04X}"
        if label not in seen:
            seen.append(label)
    if not seen:
        return 'Current page/subpage: --'
    return 'Current page/subpage: ' + ', '.join(seen[:6])


def _repair_recent_header_summary(packets, frame_history=(), fallback='Current page/subpage: --'):
    summary = _repair_current_header_summary(packets)
    if summary != 'Current page/subpage: --':
        return summary
    for _, frame_packets in reversed(tuple(frame_history)):
        summary = _repair_current_header_summary(frame_packets)
        if summary != 'Current page/subpage: --':
            return summary
    return str(fallback or 'Current page/subpage: --')


def _repair_requested_page_summary(page_text, subpage_text=''):
    try:
        _, page_hex = _parse_repair_page_value(page_text)
    except ValueError:
        page_hex = str(page_text).strip().upper() or '100'
    try:
        _, subpage_hex = _parse_repair_subpage_value(subpage_text)
    except ValueError:
        subpage_hex = str(subpage_text).strip().upper()
    if subpage_hex:
        return f'Watching page/subpage: P{page_hex}/{subpage_hex}'
    return f'Watching page/subpage: P{page_hex}'


def _repair_header_entries(packets, frame_history=(), limit=12):
    seen = []

    def add_packets(source_packets):
        for packet in source_packets:
            if int(packet.mrag.row) != 0:
                continue
            entry = (
                f"{int(packet.mrag.magazine)}{int(packet.header.page):02X}",
                f"{int(packet.header.subpage):04X}",
            )
            if entry not in seen:
                seen.append(entry)
                if len(seen) >= max(int(limit), 1):
                    return True
        return False

    if add_packets(packets):
        return tuple(seen[:max(int(limit), 1)])
    for _, frame_packets in reversed(tuple(frame_history)):
        if add_packets(frame_packets):
            break
    return tuple(seen[:max(int(limit), 1)])


def _repair_recent_page_suggestions(packets, frame_history=(), limit=12):
    pages = []
    for page_text, _ in _repair_header_entries(packets, frame_history, limit=max(int(limit) * 2, 1)):
        if page_text not in pages:
            pages.append(page_text)
        if len(pages) >= max(int(limit), 1):
            break
    return tuple(pages[:max(int(limit), 1)])


def _repair_recent_subpage_suggestions(packets, page_value, frame_history=(), limit=16):
    page_value = int(page_value)
    subpages = []
    for page_text, subpage_text in _repair_header_entries(packets, frame_history, limit=max(int(limit) * 3, 1)):
        try:
            packet_page, _ = _parse_repair_page_value(page_text)
        except ValueError:
            continue
        if int(packet_page) != page_value:
            continue
        if subpage_text not in subpages:
            subpages.append(subpage_text)
        if len(subpages) >= max(int(limit), 1):
            break
    return tuple(subpages[:max(int(limit), 1)])


def _repair_page_suggestions(packets, frame_history=(), limit=12):
    counts = {}
    order = []

    def add_packet(packet):
        if int(packet.mrag.row) != 0:
            return
        label = f"{int(packet.mrag.magazine)}{int(packet.header.page):02X}"
        counts[label] = counts.get(label, 0) + 1
        if label not in order:
            order.append(label)

    for packet in packets:
        add_packet(packet)
    for _, frame_packets in reversed(tuple(frame_history)):
        for packet in frame_packets:
            add_packet(packet)

    ranked = sorted(
        counts.items(),
        key=lambda item: (-int(item[1]), order.index(item[0])),
    )
    return tuple(label for label, _ in ranked[:max(int(limit), 1)])


def _repair_subpage_suggestions(packets, page_value, frame_history=(), limit=16):
    counts = {}
    order = []

    def add_packet(packet):
        if int(packet.mrag.row) != 0:
            return
        packet_page = (int(packet.mrag.magazine) << 8) | int(packet.header.page)
        if packet_page != int(page_value):
            return
        label = f'{int(packet.header.subpage):04X}'
        counts[label] = counts.get(label, 0) + 1
        if label not in order:
            order.append(label)

    for packet in packets:
        add_packet(packet)
    for _, frame_packets in reversed(tuple(frame_history)):
        for packet in frame_packets:
            add_packet(packet)

    ranked = sorted(
        counts.items(),
        key=lambda item: (-int(item[1]), order.index(item[0])),
    )
    return tuple(label for label, _ in ranked[:max(int(limit), 1)])


def _vbicrop_line_looks_erased(line):
    if line.is_teletext:
        return False

    quality = int(line.diagnostic_quality)
    reason = str(line.reject_reason or '').lower()
    signal_max = getattr(line, '_signal_max', None)
    noisefloor = getattr(line, '_noisefloor', None)

    if quality <= 12:
        return True
    if reason.startswith('signal max is') or reason.startswith('noise is'):
        return True
    if signal_max is not None and noisefloor is not None:
        try:
            if float(signal_max) <= (float(noisefloor) + 8.0):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _vbicrop_line_noise_value(line):
    noisefloor = getattr(line, '_noisefloor', None)
    if noisefloor is None:
        return 0.0
    try:
        return float(noisefloor)
    except (TypeError, ValueError):
        return 0.0


def _longest_true_run(values):
    longest = 0
    current = 0
    for value in values:
        if bool(value):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def _vbicrop_frame_error_metrics(frame_index, lines, selected_lines=None):
    if selected_lines:
        selected = [
            (logical_line, line)
            for logical_line, line in enumerate(lines, start=1)
            if logical_line in selected_lines
        ]
    else:
        selected = list(enumerate(lines, start=1))

    if not selected:
        return {
            'frame_index': int(frame_index),
            'line_count': 0,
            'teletext_count': 0,
            'mean_quality': 0.0,
            'erased_count': 0,
            'noisy_count': 0,
            'severe_noise_count': 0,
            'extreme_noise_count': 0,
            'longest_erased_run': 0,
            'first_teletext_line': None,
            'last_teletext_line': None,
        }

    qualities = []
    teletext_count = 0
    erased_count = 0
    noisy_count = 0
    severe_noise_count = 0
    extreme_noise_count = 0
    erased_mask = []
    teletext_positions = []
    for logical_line, line in selected:
        qualities.append(int(line.diagnostic_quality))
        if line.is_teletext:
            teletext_count += 1
            teletext_positions.append(int(logical_line))
        erased = _vbicrop_line_looks_erased(line)
        erased_mask.append(erased)
        if erased:
            erased_count += 1
        noise_value = _vbicrop_line_noise_value(line)
        if noise_value >= 150.0:
            noisy_count += 1
        if noise_value >= 170.0:
            severe_noise_count += 1
        if noise_value >= 190.0:
            extreme_noise_count += 1

    return {
        'frame_index': int(frame_index),
        'line_count': len(selected),
        'teletext_count': int(teletext_count),
        'mean_quality': float(np.mean(qualities)) if qualities else 0.0,
        'erased_count': int(erased_count),
        'noisy_count': int(noisy_count),
        'severe_noise_count': int(severe_noise_count),
        'extreme_noise_count': int(extreme_noise_count),
        'longest_erased_run': _longest_true_run(erased_mask),
        'first_teletext_line': teletext_positions[0] if teletext_positions else None,
        'last_teletext_line': teletext_positions[-1] if teletext_positions else None,
    }


def _merge_frame_ranges(frame_indexes, gap=1):
    if not frame_indexes:
        return ()
    gap = max(int(gap), 0)
    ordered = sorted({int(frame_index) for frame_index in frame_indexes})
    start = ordered[0]
    end = ordered[0]
    ranges = []
    for frame_index in ordered[1:]:
        if frame_index <= (end + gap + 1):
            end = frame_index
            continue
        ranges.append((start, end))
        start = frame_index
        end = frame_index
    ranges.append((start, end))
    return tuple(ranges)


def _count_frame_ranges(frame_ranges):
    return sum((int(end) - int(start)) + 1 for start, end in frame_ranges)


def infer_vbicrop_error_ranges(frame_metrics):
    metrics = tuple(frame_metrics)
    if not metrics:
        return ()

    line_counts = np.array([max(int(item['line_count']), 0) for item in metrics], dtype=np.float32)
    teletext_counts = np.array([max(int(item['teletext_count']), 0) for item in metrics], dtype=np.float32)
    mean_qualities = np.array([max(float(item['mean_quality']), 0.0) for item in metrics], dtype=np.float32)

    if float(np.max(line_counts)) <= 0.0:
        return ()

    baseline_teletext = float(np.median(teletext_counts))
    baseline_quality = float(np.median(mean_qualities))
    if baseline_teletext < 1.0 and baseline_quality < 18.0:
        return ()

    first_positions = np.array(
        [int(item['first_teletext_line']) for item in metrics if item.get('first_teletext_line') is not None],
        dtype=np.float32,
    )
    last_positions = np.array(
        [int(item['last_teletext_line']) for item in metrics if item.get('last_teletext_line') is not None],
        dtype=np.float32,
    )
    baseline_first = float(np.median(first_positions)) if first_positions.size else None
    baseline_last = float(np.median(last_positions)) if last_positions.size else None

    flagged_frames = []
    for item in metrics:
        inspected = max(int(item['line_count']), 1)
        teletext_drop = baseline_teletext - float(item['teletext_count'])
        erased_count = int(item['erased_count'])
        noisy_count = int(item.get('noisy_count', 0))
        severe_noise_count = int(item.get('severe_noise_count', 0))
        extreme_noise_count = int(item.get('extreme_noise_count', 0))
        erased_run = int(item.get('longest_erased_run', 0))

        extreme_noise = (
            extreme_noise_count >= max(2, int(np.ceil(inspected * 0.30)))
        )
        severe_noise = (
            extreme_noise
            or
            severe_noise_count >= max(3, int(np.ceil(inspected * 0.45)))
            or noisy_count >= max(4, int(np.ceil(inspected * 0.55)))
        )
        severe_erase = (
            erased_run >= max(3, int(np.ceil(inspected * 0.15)))
            or erased_count >= max(4, int(np.ceil(inspected * 0.25)))
        )
        strong_loss = teletext_drop >= max(3.0, float(np.ceil(max(baseline_teletext, 1.0) * 0.35)))
        full_loss = int(item.get('teletext_count', 0)) <= 2 and baseline_teletext >= 3.0

        vertical_shift = False
        first_line = item.get('first_teletext_line')
        last_line = item.get('last_teletext_line')
        boundary_shift = 0
        if (
            baseline_first is not None
            and baseline_last is not None
            and first_line is not None
            and last_line is not None
        ):
            first_shift = int(first_line) - int(round(baseline_first))
            last_shift = int(last_line) - int(round(baseline_last))
            same_direction = (
                (first_shift > 0 and last_shift > 0)
                or (first_shift < 0 and last_shift < 0)
            )
            boundary_shift = max(abs(first_shift), abs(last_shift))
            vertical_shift = (
                same_direction
                and boundary_shift >= 2
                and abs(abs(first_shift) - abs(last_shift)) <= 1
                and int(item['teletext_count']) >= max(1, int(round(baseline_teletext * 0.5)))
            )

        if (
            severe_noise
            or full_loss
            or (severe_erase and strong_loss)
            or (vertical_shift and (erased_count >= 2 or severe_noise or strong_loss))
        ):
            flagged_frames.append(int(item['frame_index']))

    return _merge_frame_ranges(flagged_frames, gap=1)


def summarise_vbicrop_error_zones(frame_metrics, ranges, frame_rate):
    metrics = tuple(frame_metrics)
    ranges = tuple(ranges)
    if not metrics or not ranges:
        return ()

    metric_by_frame = {int(item['frame_index']): item for item in metrics}
    teletext_counts = np.array([max(int(item['teletext_count']), 0) for item in metrics], dtype=np.float32)
    first_positions = np.array(
        [int(item['first_teletext_line']) for item in metrics if item.get('first_teletext_line') is not None],
        dtype=np.float32,
    )
    last_positions = np.array(
        [int(item['last_teletext_line']) for item in metrics if item.get('last_teletext_line') is not None],
        dtype=np.float32,
    )
    baseline_teletext = float(np.median(teletext_counts)) if teletext_counts.size else 0.0
    baseline_first = float(np.median(first_positions)) if first_positions.size else None
    baseline_last = float(np.median(last_positions)) if last_positions.size else None
    frame_rate = max(float(frame_rate), 0.001)

    zones = []
    for start, end in ranges:
        zone_metrics = [
            metric_by_frame[frame_index]
            for frame_index in range(int(start), int(end) + 1)
            if frame_index in metric_by_frame
        ]
        if not zone_metrics:
            continue

        max_noisy = max(int(item.get('noisy_count', 0)) for item in zone_metrics)
        max_severe_noise = max(int(item.get('severe_noise_count', 0)) for item in zone_metrics)
        max_extreme_noise = max(int(item.get('extreme_noise_count', 0)) for item in zone_metrics)
        max_erased = max(int(item.get('erased_count', 0)) for item in zone_metrics)
        max_erased_run = max(int(item.get('longest_erased_run', 0)) for item in zone_metrics)
        min_teletext = min(int(item.get('teletext_count', 0)) for item in zone_metrics)
        max_line_count = max(int(item.get('line_count', 0)) for item in zone_metrics)
        min_first = min(
            (int(item['first_teletext_line']) for item in zone_metrics if item.get('first_teletext_line') is not None),
            default=None,
        )
        max_last = max(
            (int(item['last_teletext_line']) for item in zone_metrics if item.get('last_teletext_line') is not None),
            default=None,
        )

        reasons = []
        score = 0
        kind_scores = {
            'noise': 0,
            'erased-lines': 0,
            'vertical-shift': 0,
            'teletext-loss': 0,
        }
        near_total_erase = (
            max_erased >= max(12, int(np.ceil(max(max_line_count, 1) * 0.70)))
            or max_erased_run >= max(10, int(np.ceil(max(max_line_count, 1) * 0.45)))
        )
        full_loss = (min_teletext <= 2 and baseline_teletext >= 3)
        if max_extreme_noise > 0:
            reasons.append(f'190+ noise on up to {max_extreme_noise} lines')
            noise_score = 12 if max_extreme_noise >= max(4, int(np.ceil(max(max_line_count, 1) * 0.18))) else 7
            score += noise_score
            kind_scores['noise'] += noise_score
        elif max_severe_noise > 0:
            reasons.append(f'170+ noise on up to {max_severe_noise} lines')
            noise_score = 5 if max_severe_noise >= 8 else 3
            score += noise_score
            kind_scores['noise'] += noise_score
        elif max_noisy > 0:
            reasons.append(f'150+ noise on up to {max_noisy} lines')
            noise_score = 3 if max_noisy >= 6 else 1
            score += noise_score
            kind_scores['noise'] += noise_score
        if max_erased_run > 0 or max_erased > 0:
            reasons.append(f'erased lines {max_erased} (run {max_erased_run})')
            erase_score = 0
            if near_total_erase:
                erase_score = 8
            elif max_erased_run >= 6 or max_erased >= 8:
                erase_score = 4
            elif max_erased_run >= 3 or max_erased >= 4:
                erase_score = 2
            elif max_erased >= 2:
                erase_score = 1
            score += erase_score
            kind_scores['erased-lines'] += erase_score
        teletext_drop = max(0, int(round(baseline_teletext)) - min_teletext)
        if full_loss:
            reasons.append(f'teletext loss {teletext_drop} lines')
            loss_score = 12
            score += loss_score
            kind_scores['teletext-loss'] += loss_score
        elif teletext_drop >= 3:
            reasons.append(f'teletext loss {teletext_drop} lines')
            loss_score = 0
            if teletext_drop >= max(6, int(round(baseline_teletext * 0.6))):
                loss_score = 5
            elif teletext_drop >= max(3, int(round(baseline_teletext * 0.35))):
                loss_score = 3
            score += loss_score
            kind_scores['teletext-loss'] += loss_score
        shift_distance = 0
        if (
            baseline_first is not None
            and baseline_last is not None
            and min_first is not None
            and max_last is not None
        ):
            start_shift = int(min_first) - int(round(baseline_first))
            end_shift = int(max_last) - int(round(baseline_last))
            if (
                abs(start_shift) >= 2
                and abs(end_shift) >= 2
                and ((start_shift > 0 and end_shift > 0) or (start_shift < 0 and end_shift < 0))
            ):
                shift_distance = max(abs(start_shift), abs(end_shift))
                direction = 'down' if start_shift > 0 else 'up'
                reasons.append(f'line block shifted {direction} by {shift_distance}')
                shift_score = 4 if shift_distance >= 5 else 2
                score += shift_score
                kind_scores['vertical-shift'] += shift_score

        critical = (
            full_loss
            or max_extreme_noise >= max(4, int(np.ceil(max(max_line_count, 1) * 0.18)))
            or (near_total_erase and teletext_drop >= max(5, int(round(max(baseline_teletext, 1.0) * 0.5))))
            or score >= 16
        )

        if critical:
            level = 'critical'
        elif score >= 5:
            level = 'bad'
        else:
            level = 'warning'

        active_kinds = [kind for kind, kind_score in kind_scores.items() if kind_score > 0]
        if len(active_kinds) > 1:
            kind = 'mixed'
        elif active_kinds:
            kind = active_kinds[0]
        else:
            kind = 'signal'

        duration_frames = (int(end) - int(start)) + 1
        duration_seconds = duration_frames / frame_rate
        zones.append({
            'start_frame': int(start),
            'end_frame': int(end),
            'duration_frames': duration_frames,
            'duration_seconds': duration_seconds,
            'level': level,
            'severity_score': int(score),
            'kind': kind,
            'teletext_loss_count': int(teletext_drop),
            'shift_distance': int(shift_distance),
            'has_noise': bool(max_noisy > 0 or max_severe_noise > 0 or max_extreme_noise > 0),
            'max_noisy_count': max_noisy,
            'max_severe_noise_count': max_severe_noise,
            'max_extreme_noise_count': max_extreme_noise,
            'max_erased_count': max_erased,
            'max_erased_run': max_erased_run,
            'min_teletext_count': min_teletext,
            'reason': '; '.join(reasons) if reasons else 'severe signal disruption',
        })

    return tuple(zones)


def scan_vbicrop_error_ranges(
    input_path,
    config,
    tape_format,
    controls,
    decoder_tuning,
    *,
    line_selection=None,
    n_lines=None,
    progress_callback=None,
):
    frame_size = frame_size_for_config(config)
    total_frames = count_complete_frames(input_path, config)
    if total_frames <= 0:
        return {
            'ranges': (),
            'total_frames': 0,
            'flagged_frames': 0,
            'summary': 'Errors: no frames',
        }

    controls = normalise_signal_controls_tuple(controls)
    selected_lines = frozenset(int(line) for line in (line_selection or ()))
    temporal_state = {}
    frame_metrics = []

    _configure_repair_lines(config, tape_format, controls, decoder_tuning)

    with open(input_path, 'rb') as handle:
        for frame_index in range(total_frames):
            frame = handle.read(frame_size)
            if len(frame) < frame_size:
                break
            processed = _processed_frame_for_output(
                frame,
                config,
                controls,
                line_selection=selected_lines if selected_lines else None,
                temporal_state=temporal_state,
                decoder_tuning=decoder_tuning,
            )
            lines = _extract_frame_lines_for_repair(
                processed,
                config,
                frame_index,
                n_lines=n_lines,
            )
            frame_metrics.append(_vbicrop_frame_error_metrics(frame_index, lines, selected_lines=selected_lines))
            if callable(progress_callback) and ((frame_index % 8) == 0 or frame_index == (total_frames - 1)):
                progress_callback(frame_index + 1, total_frames)

    ranges = infer_vbicrop_error_ranges(frame_metrics)
    zones = summarise_vbicrop_error_zones(frame_metrics, ranges, frame_rate=25.0)
    flagged_frames = _count_frame_ranges(ranges)
    level_counts = defaultdict(int)
    for zone in zones:
        level_counts[str(zone.get('level') or 'warning')] += 1
    if ranges:
        parts = []
        if level_counts.get('critical'):
            parts.append(f"{level_counts['critical']} critical")
        if level_counts.get('bad'):
            parts.append(f"{level_counts['bad']} bad")
        if level_counts.get('warning'):
            parts.append(f"{level_counts['warning']} warning")
        summary = f"Errors: {', '.join(parts)} / {flagged_frames} frames"
    else:
        summary = 'Errors: none detected'
    return {
        'ranges': ranges,
        'zones': zones,
        'total_frames': total_frames,
        'flagged_frames': flagged_frames,
        'summary': summary,
    }


class VBIRepairDiagnostics:
    def __init__(
        self,
        input_path,
        state_provider,
        *,
        mode='deconvolve',
        eight_bit=False,
        n_lines=None,
        page_history_frames=15,
        input_path_provider=None,
        frame_index_offset_provider=None,
    ):
        self._input_path = input_path
        self._state_provider = state_provider
        self._mode = str(mode)
        self._eight_bit = bool(eight_bit)
        self._n_lines = n_lines
        self._page_history_frames = max(int(page_history_frames), 1)
        self._input_path_provider = input_path_provider if callable(input_path_provider) else None
        self._frame_index_offset_provider = frame_index_offset_provider if callable(frame_index_offset_provider) else None
        self._frame_history = deque(maxlen=self._page_history_frames)
        self._last_signature = None
        self._last_frame_index = None
        self._last_frame_results = ()
        self._last_frame_packets = ()
        self._last_processed_frame = None
        self._temporal_state = {}
        self._handle = None
        self._opened_input_path = None
        self._frame_size = None
        self._last_page_render_key = None
        self._last_page_render_text = None

    def close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        self._opened_input_path = None

    def __call__(self, frame_index, view_mode, row, page, subpage='', hide_noisy=False):
        return self.describe(frame_index, view_mode=view_mode, row=row, page=page, subpage=subpage, hide_noisy=hide_noisy)

    def _current_input_path(self):
        if self._input_path_provider is not None:
            current_path = self._input_path_provider()
            if current_path:
                return str(current_path)
        return str(self._input_path)

    def _current_frame_index_offset(self):
        if self._frame_index_offset_provider is None:
            return 0
        try:
            return max(int(self._frame_index_offset_provider()), 0)
        except Exception:
            return 0

    def _open(self):
        current_input_path = self._current_input_path()
        if (
            self._handle is None
            or self._opened_input_path is None
            or os.path.abspath(str(self._opened_input_path)) != os.path.abspath(str(current_input_path))
        ):
            self.close()
            self._handle = open(current_input_path, 'rb')
            self._opened_input_path = current_input_path
        return self._handle

    def _runtime(self):
        runtime = dict(self._state_provider())
        runtime['controls'] = normalise_signal_controls_tuple(runtime['controls'])
        runtime['selected_lines'] = frozenset(int(line) for line in runtime.get('selected_lines', ()))
        runtime['decoder_tuning'] = dict(runtime['decoder_tuning'])
        return runtime

    def _signature_for_runtime(self, runtime):
        config = runtime['config']
        decoder_tuning = runtime['decoder_tuning']
        return (
            int(config.line_bytes),
            int(config.field_lines),
            tuple(int(value) for value in config.field_range),
            runtime['tape_format'],
            tuple(runtime['controls']),
            tuple(sorted(runtime['selected_lines'])),
            decoder_tuning['tape_format'],
            int(decoder_tuning['extra_roll']),
            tuple(int(value) for value in decoder_tuning['line_start_range']),
            int(decoder_tuning['quality_threshold']),
            float(decoder_tuning.get('quality_threshold_coeff', QUALITY_THRESHOLD_COEFF_DEFAULT)),
            int(decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)),
            float(decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)),
            int(decoder_tuning.get('start_lock', START_LOCK_DEFAULT)),
            float(decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)),
            int(decoder_tuning.get('adaptive_threshold', ADAPTIVE_THRESHOLD_DEFAULT)),
            float(decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)),
            int(decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)),
            float(decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)),
            int(decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)),
            float(decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)),
            int(decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
            tuple(sorted(normalise_per_line_shift_map(decoder_tuning.get('per_line_shift', {}), maximum_line=32).items())),
            tuple(sorted(
                (int(line), tuple(normalise_signal_controls_tuple(values)))
                for line, values in dict(decoder_tuning.get('line_control_overrides', {})).items()
            )),
            tuple(sorted(
                (int(line), tuple(sorted(dict(values).items())))
                for line, values in dict(decoder_tuning.get('line_decoder_overrides', {})).items()
            )),
            self._mode,
            self._eight_bit,
            self._n_lines,
            os.path.abspath(self._current_input_path()),
            int(self._current_frame_index_offset()),
        )

    def _read_frame(self, frame_index, config):
        frame_index = max(int(frame_index), 0)
        frame_index -= self._current_frame_index_offset()
        if frame_index < 0:
            return None
        if self._frame_size is None:
            self._frame_size = frame_size_for_config(config)
        handle = self._open()
        handle.seek(frame_index * self._frame_size)
        frame = handle.read(self._frame_size)
        if len(frame) < self._frame_size:
            return None
        return frame

    def _reset_runtime_cache(self, signature=None):
        self._frame_history.clear()
        self._last_signature = signature
        self._last_frame_index = None
        self._last_frame_results = ()
        self._last_frame_packets = ()
        self._last_processed_frame = None
        self._temporal_state = {}
        self._last_page_render_key = None
        self._last_page_render_text = None

    def _page_history_start(self, frame_index):
        return max(int(frame_index) - self._page_history_frames + 1, 0)

    def _history_covers_page_window(self, frame_index):
        frame_index = max(int(frame_index), 0)
        if not self._frame_history:
            return False
        history_indexes = tuple(int(index) for index, _ in self._frame_history)
        if not history_indexes or history_indexes[-1] != frame_index:
            return False
        expected_start = self._page_history_start(frame_index)
        if history_indexes[0] > expected_start:
            return False
        return history_indexes == tuple(range(history_indexes[0], frame_index + 1))

    def _decode_current_frame(self, frame_index, runtime):
        frame_index = max(int(frame_index), 0)
        frame = self._read_frame(frame_index, runtime['config'])
        if frame is None:
            return (), ()

        if self._last_processed_frame is None or frame_index != (self._last_processed_frame + 1):
            self._temporal_state = {}
        self._last_processed_frame = frame_index

        processed = _processed_frame_for_output(
            frame,
            runtime['config'],
            runtime['controls'],
            line_selection=runtime['selected_lines'],
            temporal_state=self._temporal_state,
            decoder_tuning=runtime['decoder_tuning'],
        )
        _configure_repair_lines(
            runtime['config'],
            runtime['tape_format'],
            runtime['controls'],
            runtime['decoder_tuning'],
        )
        lines = _extract_frame_lines_for_repair(
            processed,
            runtime['config'],
            frame_index,
            n_lines=self._n_lines,
        )
        selected_lines = frozenset(runtime['selected_lines'])
        decode_method = 'deconvolve' if self._mode == 'deconvolve' else 'slice'
        results = []
        packets = []
        for logical_line, line in enumerate(lines, start=1):
            if logical_line not in selected_lines:
                results.append({
                    'logical_line': logical_line,
                    'status': 'disabled',
                    'reason': 'disabled by line selection',
                    'quality': 0,
                })
                continue
            decoded = getattr(line, decode_method)(mags=range(9), rows=range(32), eight_bit=self._eight_bit)
            entry = {
                'logical_line': logical_line,
                'quality': int(line.diagnostic_quality),
                'reason': str(line.reject_reason or ''),
            }
            if isinstance(decoded, Packet):
                entry['status'] = 'packet'
                entry['packet'] = decoded
                entry['reason'] = ''
                entry['start'] = int(line.start) if line.start is not None else None
                packets.append(decoded)
            else:
                entry['status'] = str(decoded)
            results.append(entry)
        return tuple(results), tuple(packets)

    def _ensure_history(self, frame_index, runtime, require_history=False, progress_callback=None):
        signature = self._signature_for_runtime(runtime)
        if signature != self._last_signature:
            self._reset_runtime_cache(signature)

        frame_index = max(int(frame_index), 0)
        if self._last_frame_index == frame_index and (
            (not require_history) or self._history_covers_page_window(frame_index)
        ):
            return

        if not require_history:
            self._frame_history.clear()
            self._last_processed_frame = None
            self._temporal_state = {}
            if callable(progress_callback):
                progress_callback(0, 1)
            results, packets = self._decode_current_frame(frame_index, runtime)
            self._frame_history.append((frame_index, packets))
            self._last_frame_results = results
            self._last_frame_packets = packets
            self._last_frame_index = frame_index
            if callable(progress_callback):
                progress_callback(1, 1)
            return

        if self._history_covers_page_window(frame_index):
            return

        can_extend_history = (
            self._last_frame_index is not None
            and frame_index == (self._last_frame_index + 1)
            and self._history_covers_page_window(self._last_frame_index)
        )

        if not can_extend_history:
            self._frame_history.clear()
            self._last_processed_frame = None
            self._temporal_state = {}
            start = self._page_history_start(frame_index)
        else:
            start = self._last_frame_index + 1

        total = max((frame_index - start) + 1, 1)
        if callable(progress_callback):
            progress_callback(0, total)

        for current_index in range(start, frame_index + 1):
            results, packets = self._decode_current_frame(current_index, runtime)
            self._frame_history.append((current_index, packets))
            if current_index == frame_index:
                self._last_frame_results = results
                self._last_frame_packets = packets
            if callable(progress_callback):
                progress_callback((current_index - start) + 1, total)

        self._last_frame_index = frame_index

    def _render_packet_results(self, frame_index, results, packets, row=None, hide_noisy=False, quality_threshold=0, current_headers=None):
        lines = [
            f'Frame {int(frame_index)} | decoded packets {len(packets)} | mode {self._mode}',
        ]
        if current_headers:
            lines.append(current_headers)
        if row is not None:
            lines[0] += f' | filtered row {int(row)}'
        visible = 0
        for item in results:
            logical_line = int(item['logical_line'])
            if item['status'] == 'packet':
                packet = item['packet']
                if row is not None and int(packet.mrag.row) != int(row):
                    continue
                confidence = _repair_packet_confidence(packet, item['quality'])
                if hide_noisy and confidence < float(quality_threshold):
                    continue
                visible += 1
                lines.append(
                    f"L{logical_line:02d} "
                    f"M{packet.mrag.magazine} R{packet.mrag.row:02d} "
                    f"Q{confidence:05.1f} "
                    f"{packet.to_ansi(colour=True)}"
                )
            elif row is None and not hide_noisy:
                visible += 1
                reason = item.get('reason', '')
                suffix = f' ({reason})' if reason else ''
                lines.append(
                    f"L{logical_line:02d} {item['status']} "
                    f"Q{int(item['quality']):02d}{suffix}"
                )
        if visible == 0:
            lines.append('No matching decoded packets for this frame yet.')
        return '\n'.join(lines)

    def _render_page_results(self, frame_index, page_text, subpage_text='', hide_noisy=False, quality_threshold=0, current_headers=None):
        page_value, page_hex, subpage_value, subpage_hex, subpages = self._collect_page_subpages(
            page_text,
            subpage_text,
            hide_noisy=hide_noisy,
            quality_threshold=quality_threshold,
        )
        cache_key = (
            int(frame_index),
            page_hex,
            subpage_hex,
            bool(hide_noisy),
            int(quality_threshold),
            tuple(int(index) for index, _ in self._frame_history),
        )
        if cache_key == self._last_page_render_key and self._last_page_render_text is not None:
            return self._last_page_render_text

        if not subpages:
            text = (
                f'Page P{page_hex} not assembled yet.\n'
                f'Buffered frames: {len(self._frame_history)}\n'
                f'Current frame: {int(frame_index)}'
            )
            if current_headers:
                text = current_headers + '\n' + text
            self._last_page_render_key = cache_key
            self._last_page_render_text = text
            return text
        text = _render_repair_subpage_text(
            subpages[0],
            subpage_count=len(subpages),
            buffered_frames=len(self._frame_history),
        )
        if current_headers:
            text = current_headers + '\n' + text
        self._last_page_render_key = cache_key
        self._last_page_render_text = text
        return text

    def _collect_page_subpages(self, page_text, subpage_text='', hide_noisy=False, quality_threshold=0):
        page_value, page_hex = _parse_repair_page_value(page_text)
        subpage_value, subpage_hex = _parse_repair_subpage_value(subpage_text)
        packet_stream = (
            packet
            for _, frame_packets in self._frame_history
            for packet in frame_packets
            if (not hide_noisy) or (_repair_packet_confidence(packet, quality_threshold) >= float(quality_threshold))
        )
        packet_lists = list(pipeline.paginate(packet_stream, pages={page_value}, subpages=range(0x3f80), drop_empty=False))
        subpages = [
            subpage for subpage in (Subpage.from_packets(packet_list, ignore_empty=False) for packet_list in packet_lists)
            if ((int(subpage.mrag.magazine) << 8) | int(subpage.header.page)) == page_value
        ]
        if subpage_value is not None:
            subpages = [subpage for subpage in subpages if int(subpage.header.subpage) == int(subpage_value)]
        subpages.sort(key=lambda subpage: (subpage.average_confidence, subpage.header.subpage), reverse=True)
        return page_value, page_hex, subpage_value, subpage_hex, subpages

    def export_page_t42(self, frame_index, page_text, subpage_text, output_path, hide_noisy=False):
        runtime = self._runtime()
        self._ensure_history(
            frame_index,
            runtime,
            require_history=True,
        )
        quality_threshold = int(runtime['decoder_tuning'].get('quality_threshold', QUALITY_THRESHOLD_DEFAULT))
        page_value, page_hex, subpage_value, subpage_hex, subpages = self._collect_page_subpages(
            page_text,
            subpage_text,
            hide_noisy=hide_noisy,
            quality_threshold=quality_threshold,
        )
        if not subpages:
            message = (
                f'Page P{page_hex}'
                + (f' Subpage {subpage_hex}' if subpage_value is not None else '')
                + ' is not assembled yet.'
            )
            raise ValueError(message)

        with open(output_path, 'wb') as handle:
            for packet in subpages[0].packets:
                handle.write(packet.to_bytes())
        chosen_subpage_hex = subpage_hex or f'{int(subpages[0].header.subpage):04X}'
        return {
            'page': int(page_value),
            'page_hex': str(page_hex),
            'subpage': int(chosen_subpage_hex, 16),
            'subpage_hex': chosen_subpage_hex,
            'packet_count': sum(1 for _ in subpages[0].packets),
        }

    def describe_payload(self, frame_index, view_mode='packets', row=0, page='100', subpage='', hide_noisy=False, progress_callback=None):
        runtime = self._runtime()
        mode = str(view_mode)
        self._ensure_history(
            frame_index,
            runtime,
            require_history=(mode == 'page'),
            progress_callback=progress_callback,
        )
        quality_threshold = int(runtime['decoder_tuning'].get('quality_threshold', QUALITY_THRESHOLD_DEFAULT))
        current_headers = _repair_recent_header_summary(self._last_frame_packets, self._frame_history)
        current_header_entries = _repair_header_entries(self._last_frame_packets, self._frame_history)
        summary = current_headers
        subpage_suggestions = ()
        page_auto_suggestions = ()
        subpage_auto_suggestions = ()
        if mode == 'page':
            try:
                page_value, _ = _parse_repair_page_value(page)
            except ValueError:
                page_value = None
            if page_value is not None:
                subpage_suggestions = _repair_subpage_suggestions(self._last_frame_packets, page_value, self._frame_history)
                subpage_auto_suggestions = _repair_recent_subpage_suggestions(
                    self._last_frame_packets,
                    page_value,
                    self._frame_history,
                )
            summary = _repair_requested_page_summary(page, subpage)
            if current_headers != 'Current page/subpage: --':
                summary = summary + '\n' + current_headers
            page_auto_suggestions = _repair_recent_page_suggestions(
                self._last_frame_packets,
                self._frame_history,
            )
            text = self._render_page_results(
                frame_index,
                page,
                subpage,
                hide_noisy=hide_noisy,
                quality_threshold=quality_threshold,
                current_headers=None,
            )
        elif mode == 'row':
            text = self._render_packet_results(
                frame_index,
                self._last_frame_results,
                self._last_frame_packets,
                row=int(row),
                hide_noisy=hide_noisy,
                quality_threshold=quality_threshold,
                current_headers=current_headers,
            )
        else:
            text = self._render_packet_results(
                frame_index,
                self._last_frame_results,
                self._last_frame_packets,
                row=None,
                hide_noisy=hide_noisy,
                quality_threshold=quality_threshold,
                current_headers=current_headers,
            )
        return {
            'text': text,
            'summary': summary,
            'quality_threshold': quality_threshold,
            'frame_index': int(frame_index),
            'mode': mode,
            'page_suggestions': _repair_page_suggestions(self._last_frame_packets, self._frame_history),
            'page_auto_suggestions': page_auto_suggestions,
            'subpage_suggestions': subpage_suggestions,
            'subpage_auto_suggestions': subpage_auto_suggestions,
            'current_page_entries': current_header_entries,
        }

    def describe(self, frame_index, view_mode='packets', row=0, page='100', subpage='', hide_noisy=False):
        return self.describe_payload(frame_index, view_mode=view_mode, row=row, page=page, subpage=subpage, hide_noisy=hide_noisy)['text']


def start_live_fix_capture_card(live_tuner, initial_settings):
    fixer = CaptureCardFixer()
    fixer.update(initial_settings)
    stop_event = threading.Event()

    def poll():
        last_settings = normalise_fix_capture_card(initial_settings)
        while not stop_event.is_set():
            settings = normalise_fix_capture_card(live_tuner.fix_capture_card())
            if settings != last_settings:
                fixer.update(settings)
                last_settings = settings
            stop_event.wait(0.5)

    thread = threading.Thread(target=poll, name='capture-card-fix-live', daemon=True)
    thread.start()
    return fixer, stop_event, thread


@click.group(invoke_without_command=True, no_args_is_help=True)
@click.option('-u', '--unicode', is_flag=True, help='Use experimental Unicode 13.0 Terminal graphics.')
@click.version_option()
@click.help_option()
@click.option('--help-all', is_flag=True, help='Show help for all subcommands.')
@click.pass_context
def teletext(ctx, unicode, help_all):
    """Teletext stream processing toolkit."""
    if help_all:
        print(teletext.get_help(ctx))

        def help_recurse(group, ctx):
            for scmd in group.list_commands(ctx):
                cmd = group.get_command(ctx, scmd)
                nctx = click.Context(cmd, ctx, scmd)
                if isinstance(cmd, click.Group):
                    help_recurse(cmd, nctx)
                else:
                    click.echo()
                    click.echo(cmd.get_help(nctx))

        help_recurse(teletext, ctx)

    if unicode:
        from teletext import parser
        parser._unicode13 = True


teletext.add_command(training)
teletext.add_command(vbi)

try:
    from teletext.cli.celp import celp
    teletext.add_command(celp)
except ImportError:
    pass


@teletext.command()
@packetwriter
@paginated()
@click.option('--pagecount', 'n', type=int, default=0, help='Stop after n pages. 0 = no limit. Implies -P.')
@click.option('-k', '--keep-empty', is_flag=True, help='Keep empty packets in the output.')
@packetreader()
def filter(packets, pages, subpages, paginate, n, keep_empty):

    """Demultiplex and display t42 packet streams."""

    if n:
        paginate = True

    if not keep_empty:
        packets = (p for p in packets if not p.is_padding())

    if paginate:
        for pn, pl in enumerate(pipeline.paginate(packets, pages=pages, subpages=subpages), start=1):
            yield from pl
            if pn == n:
                return
    else:
        yield from packets


@teletext.command()
@packetwriter
@paginated()
@click.argument('regex', type=str)
@click.option('-v', is_flag=True, help='Invert matches.')
@click.option('-i', is_flag=True, help='Ignore case.')
@click.option('--pagecount', 'n', type=int, default=0, help='Stop after n pages. 0 = no limit. Implies -P.')
@click.option('-k', '--keep-empty', is_flag=True, help='Keep empty packets in the output.')
@packetreader()
def grep(packets, pages, subpages, paginate, regex, v, i, n, keep_empty):

    """Filter packets with a regular expression."""

    import re

    pattern = re.compile(regex.encode('ascii'), re.IGNORECASE if i else 0)

    if n:
        paginate = True

    if not keep_empty:
        packets = (p for p in packets if not p.is_padding())

    if paginate:
        for pn, pl in enumerate(pipeline.paginate(packets, pages=pages, subpages=subpages), start=1):
            for p in pl:
                if bool(v) != bool(re.search(pattern, p.to_bytes_no_parity())):
                    yield from pl
                    if pn == n:
                        return
    else:
        for p in packets:
            if bool(v) != bool(re.search(pattern, p.to_bytes_no_parity())):
                yield p


@teletext.command(name='list')
@click.option('-c', '--count', is_flag=True, help='Show counts of each entry.')
@click.option('-s', '--subpages', is_flag=True, help='Also list subpages.')
@paginated(always=True, filtered=False)
@packetreader()
@progressparams(progress=True, mag_hist=True)
def _list(packets, count, subpages):

    """List pages present in a t42 stream."""

    import textwrap

    packets = (p for p in packets if not p.is_padding())

    seen = {}
    try:
        for pl in pipeline.paginate(packets):
            s = Subpage.from_packets(pl)
            identifier = f'{s.mrag.magazine}{s.header.page:02x}'
            if subpages:
                identifier += f':{s.header.subpage:04x}'
            if identifier in seen:
                seen[identifier]+=1
            else:
                seen[identifier]=1
    except KeyboardInterrupt:
        print('\n')
    finally:
        if count:
            maxdigits = len(str(max(seen.values())))
            formatstr="{page}/{count:0" + str(maxdigits) +"}"
        else:
            formatstr="{page}"
        seen = list(map(lambda e: formatstr.format(page = e[0], count = e[1]), seen.items()))
        print('\n'.join(textwrap.wrap(' '.join(sorted(seen)))))


@teletext.command()
@click.argument('pattern')
@paginated(always=True)
@packetreader()
def split(packets, pattern, pages, subpages):

    """Split a t42 stream in to multiple files."""

    packets = (p for p in packets if not p.is_padding())
    counts = defaultdict(int)

    for pl in pipeline.paginate(packets, pages=pages, subpages=subpages):
        subpage = Subpage.from_packets(pl)
        m = subpage.mrag.magazine
        p = subpage.header.page
        s = subpage.header.subpage
        c = counts[(m,p,s)]
        counts[(m,p,s)] += 1
        f = pathlib.Path(pattern.format(m=m, p=f'{p:02x}', s=f'{s:04x}', c=f'{c:04d}'))
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open('ab') as ff:
            ff.write(b''.join(p.bytes for p in pl))


@teletext.command()
@click.argument('a', type=click.File('rb'))
@click.argument('b', type=click.File('rb'))
@filterparams()
def diff(a, b, mags, rows):
    """Show side by side difference of two t42 streams."""
    for chunka, chunkb in zip(FileChunker(a, 42), FileChunker(b, 42)):
        pa = Packet(chunka[1], chunka[0])
        pb = Packet(chunkb[1], chunkb[0])
        if (pa.mrag.row in rows and pa.mrag.magazine in mags) or (pb.mrag.row in rows and pa.mrag.magazine in mags):
            if any(pa[:] != pb[:]):
                print(pa.to_ansi(), pb.to_ansi())


@teletext.command()
@packetwriter
@packetreader()
def finders(packets):

    """Apply finders to fix up common packets."""

    for p in packets:
        if p.type == 'header':
            p.header.apply_finders()
        yield p


@teletext.command()
@packetreader(filtered=False)
@click.option('-l', '--lines', type=int, default=32, help='Number of recorded lines per frame.')
@click.option('-f', '--frames', type=int, default=250, help='Number of frames to squash.')
def scan(packets, lines, frames):

    """Filter a t42 stream down to headers and bsdp, with squashing."""

    from teletext.pipeline import packet_squash, bsdp_squash_format1, bsdp_squash_format2
    bars = '_:|I'

    while True:
        actives = np.zeros((lines,), dtype=np.uint32)
        headers = [[], [], [], [], [], [], [], [], []]
        service = [[], []]
        start = None
        try:
            for i in range(frames):
                for n, p in enumerate(itertools.islice(packets, lines)):
                    if start is None:
                        start = p._number
                    if not p.is_padding():
                        if p.type == 'header':
                            p.header.apply_finders()
                        actives[n] += 1
                        if p.mrag.row == 0:
                            headers[p.mrag.magazine].append(p)
                        elif p.mrag.row == 30 and p.mrag.magazine == 8:
                            if p.broadcast.dc in [0, 1]:
                                service[0].append(p)
                            elif p.broadcast.dc in [2, 3]:
                                service[1].append(p)

        except StopIteration:
            pass
        if start is None:
            return
        active_group = 1*(actives>0) + 1*(actives>(frames/2)) + 1*(actives==frames)
        print(f'{start:8d}', '['+''.join(bars[a] for a in active_group)+']', end=' ')
        for h in headers:
            if h:
                print(packet_squash(h).header.displayable.to_ansi(), end=' ')
                break
        for s in service:
            if s:
                print(packet_squash(s).broadcast.displayable.to_ansi(), end=' ')
                break
        if service[0]:
            print(bsdp_squash_format1(service[0]), end=' ')
        if service[1]:
            print(bsdp_squash_format2(service[1]), end=' ')
        print()


@teletext.command()
@click.option('-d', '--min-duplicates', type=int, default=3, help='Only squash and output subpages with at least N duplicates.')
@click.option('-t', '--threshold', type=int, default=-1, help='Max difference for squashing.')
@click.option('-i', '--ignore-empty', is_flag=True, default=False, help='Ignore the emptiest duplicate packets instead of the earliest.')
@click.option('-md', '--mode', 'squash_mode', type=click.Choice(['v1', 'v3', 'auto']), default='v3', show_default=True,
              help='Squash grouping mode. v1 groups by page similarity, v3 groups by subpage code, auto chooses per page.')
@packetwriter
@paginated(always=True)
@packetreader()
def squash(packets, min_duplicates, threshold, squash_mode, pages, subpages, ignore_empty):

    """Reduce errors in t42 stream by using frequency analysis."""

    packets = (p for p in packets if not p.is_padding())
    for sp in pipeline.subpage_squash(
            pipeline.paginate(packets, pages=pages, subpages=subpages),
            min_duplicates=min_duplicates, ignore_empty=ignore_empty,
            threshold=threshold,
            squash_mode=squash_mode,
    ):
        yield from sp.packets


@teletext.command()
@click.option('-l', '--language', default='en_GB', help='Language. Default: en_GB')
@click.option('--localcodepage', type=click.Choice(g0.keys()), default=None, help='Select teletext local codepage. Default: infer from language if possible.')
@click.option('--mode', type=click.Choice(['teletext', 'legacy']), default='teletext', help='Spellcheck mode. Default: teletext.')
@click.option('-b', '--both', is_flag=True, help='Show packet before and after corrections.')
@click.option('-t', '--threads', type=int, default=multiprocessing.cpu_count(), help='Number of threads.')
@packetwriter
@paginated(always=True)
@packetreader()
def spellcheck(packets, language, localcodepage, mode, both, threads, pages, subpages):

    """Spell check a t42 stream."""

    try:
        from teletext.spellcheck import spellcheck_page_packets, spellcheck_packets
        if mode == 'legacy':
            if both:
                packets, orig_packets = itertools.tee(packets, 2)
                packets = itermap(spellcheck_packets, packets, threads, language=language)
                try:
                    while True:
                        yield next(orig_packets)
                        yield next(packets)
                except StopIteration:
                    pass
            else:
                yield from itermap(spellcheck_packets, packets, threads, language=language)
        else:
            packet_lists = list(pipeline.paginate(packets, pages=pages, subpages=subpages))
            corrected_packet_lists = list(spellcheck_page_packets(
                packet_lists,
                language=language,
                localcodepage=localcodepage,
            ))
            if both:
                for orig_packet_list, corrected_packet_list in zip(packet_lists, corrected_packet_lists):
                    yield from orig_packet_list
                    yield from corrected_packet_list
            else:
                for corrected_packet_list in corrected_packet_lists:
                    yield from corrected_packet_list
    except ModuleNotFoundError as e:
        if e.name == 'enchant':
            raise click.UsageError(f'{e.msg}. PyEnchant is not installed. Spelling checker is not available.')
        else:
            raise e


def _format_page_key(page_key):
    magazine, page, subpage = (int(value) for value in page_key)
    return f'P{magazine}{page:02X}:{subpage:04X}'


@teletext.command(name='spellcheck-analyze')
@click.option('--localcodepage', type=click.Choice(g0.keys()), default=None, help='Select teletext local codepage for decoding national characters.')
@click.option('--min-word-length', type=int, default=3, show_default=True, help='Ignore shorter word tokens.')
@click.option('--max-differences', type=int, default=3, show_default=True, help='Ignore slot variants with more than N differing characters.')
@click.option('--top-slots', type=int, default=20, show_default=True, help='Show the top N conflicting word slots.')
@click.option('--top-pairs', type=int, default=20, show_default=True, help='Show the top N character confusion pairs.')
@click.option('--top-words', type=int, default=20, show_default=True, help='Show the top N word-variant pairs.')
@paginated(always=True)
@packetreader(progress=False)
def spellcheck_analyze(packets, localcodepage, min_word_length, max_differences, top_slots, top_pairs, top_words, pages, subpages):

    """Analyze noisy word variants in a t42 stream."""

    from teletext.spellcheck import analyze_page_packets

    packet_lists = list(pipeline.paginate(
        (packet for packet in packets if not packet.is_padding()),
        pages=pages,
        subpages=subpages,
    ))
    analysis = analyze_page_packets(
        packet_lists,
        localcodepage=localcodepage,
        min_word_length=min_word_length,
        max_differences=max_differences,
    )

    click.echo(f"Pages: {analysis['page_count']}")
    click.echo(f"Tokens: {analysis['token_count']}")
    click.echo(f"Word slots: {analysis['slot_count']}")
    click.echo(f"Conflicting slots: {analysis['variant_slot_count']}")

    click.echo()
    click.echo('Top Character Pairs')
    for (left, right), count in analysis['char_pairs'].most_common(top_pairs):
        click.echo(f'{count:4d} {left}/{right}')

    click.echo()
    click.echo('Top Word Variants')
    for (leader, variant), count in analysis['variant_words'].most_common(top_words):
        click.echo(f'{count:4d} {leader} -> {variant}')

    click.echo()
    click.echo('Top Variant Slots')
    for report in analysis['variant_reports'][:top_slots]:
        differences = ', '.join(f'{left}->{right}' for left, right in report.differences)
        click.echo(
            f"{_format_page_key(report.page_key)} row={report.row:02d} cols={report.start}-{report.end} "
            f"total={report.total} {report.leader}:{report.leader_count} vs {report.variant}:{report.variant_count} "
            f"diffs={differences}"
        )


@teletext.command()
@click.option('-r', '--replace_headers', 'replace_headers', is_flag=True, default=False, help='Replace headers with a live clock.')
@click.option('-t', '--title', 'title', type=str, default="Teletext ", help='Replace header title field with this string.')
@packetwriter
@paginated(always=True, filtered=False)
@packetreader()
def service(packets, replace_headers, title):

    """Build a service carousel from a t42 stream."""

    from teletext.service import Service
    return Service.from_packets((p for p in packets if  not p.is_padding()), replace_headers, title)


@teletext.command()
@click.option('-r', '--replace_headers', 'replace_headers', is_flag=True, default=False, help='Replace headers with a live clock.')
@click.option('-t', '--title', 'title', type=str, default=None, help='Replace header title field with this string.')
@packetwriter
@click.argument('directory', type=click.Path(exists=True, readable=True, file_okay=False, dir_okay=True))
def servicedir(directory, replace_headers, title):
    """Build a service from a directory of t42 files."""

    from teletext.servicedir import ServiceDir
    with ServiceDir(directory, replace_headers, title) as s:
        yield from s


@teletext.command()
@click.option('-i', '--initial_page', 'initial_page', type=str, default='100', help='Initial page.')
@packetreader(loop=True, dup_stdin=True)
def interactive(packets, initial_page):

    """Interactive teletext emulator."""

    from teletext import interactive
    interactive.main(packets, int(initial_page, 16))


@teletext.command()
@packetreader(loop=True)
@click.option('-p', '--port', type=str, default=None)
def serial(packets, port):

    """Write escaped packets to serial inserter."""

    import serial.tools.list_ports
    import time

    if port is None:
        for comport in serial.tools.list_ports.comports():
            if comport.vid == 0x2e8a and (comport.pid == 0x000a or comport.pid == 0x0009):
                port = comport.device

    if port is None:
        raise click.UsageError('No serial inserter found. Specify the path with -p')

    port = serial.Serial(port, timeout=3, rtscts=True)

    for p in packets:
        buf = p.bytes
        buf = buf.replace(b'\xfe', b'\xfe\x00')
        buf = buf.replace(b'\xff', b'\xfe\x01')
        buf = b'\xff' + buf
        port.write(buf)


@teletext.command()
@click.option('-e', '--editor', type=str, default='https://zxnet.co.uk/teletext/editor/#',
              show_default=True, help='Teletext editor URL.')
@paginated(always=True)
@packetreader()
def urls(packets, editor, pages, subpages):

    """Paginate a t42 stream and print edit.tf URLs."""

    packets = (p for p in packets if  not p.is_padding())
    subpages = (Subpage.from_packets(pl) for pl in pipeline.paginate(packets, pages=pages, subpages=subpages))

    for s in subpages:
        print(f'{editor}{s.url}')

@teletext.command()
@click.argument('outdir', type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True), required=True)
@click.option('-f', '--font', type=click.File('rb'), help='PCF font for rendering.')
@paginated(always=True)
@packetreader()
def images(packets, outdir, font, pages, subpages):

    """Generate images for the input stream."""

    try:
        from teletext.image import subpage_to_image, load_glyphs
    except ModuleNotFoundError as e:
        if e.name == 'PIL':
            raise click.UsageError(
                f'{e.msg}. PIL is not installed. Image generation is not available.')
        else:
            raise e

    from teletext.service import Service

    glyphs = load_glyphs(font)

    packets = (p for p in packets if  not p.is_padding())
    svc = Service.from_packets(p for p in packets if not p.is_padding())

    subpages = tqdm(list(svc.all_subpages), unit="subpage")
    for s in subpages:
        image = subpage_to_image(s, glyphs)
        filename = f'P{s.mrag.magazine}{s.header.page:02x}-{s.header.subpage:04x}.png'
        subpages.set_description(filename, refresh=False)
        if image._flash_used:
            opts = {
                'save_all': True,
                'append_images': [subpage_to_image(s, glyphs, flash_off=True)],
                'duration': 500,
                'loop': 0,
                'disposal': 2,
            }
        else:
            opts = {}
        image.save(pathlib.Path(outdir) / filename, **opts)
        if image._missing_glyphs:
            missing = ', '.join(f'{repr(c)} {hex(ord(c))}' for c in image._missing_glyphs)
            print(f'{filename} missing characters: {missing}')


@teletext.command()
@click.argument('outdir', type=click.Path(exists=True, file_okay=False, dir_okay=True, writable=True), required=True)
@click.option('-t', '--template', type=click.File('r'), default=None, help='HTML template.')
@click.option('--localcodepage', type=click.Choice(g0.keys()), default=None, help='Select codepage for Local Code of Practice')
@paginated(always=True, filtered=False)
@packetreader()
def html(packets, outdir, template, localcodepage):

    """Generate HTML files from the input stream."""

    from teletext.service import Service

    if template is not None:
        template = template.read()

    svc = Service.from_packets(p for p in packets if not p.is_padding())
    svc.to_html(outdir, template, localcodepage)


@teletext.command(name='vbireset')
@click.option('-d', '--device', type=click.Path(exists=True, dir_okay=False, readable=True, path_type=str), default='/dev/vbi0', show_default=True, help='VBI capture device to reset.')
def vbireset(device):

    """Restore the default BT878 raw VBI format (start 7/320, count 16/16)."""

    if os.name == 'nt':
        raise click.UsageError('VBI format reset is only supported on Linux.')

    current_format = get_vbi_capture_format_path(device)
    reset_format = dict(current_format)
    reset_format['start'] = BT8X8_DEFAULT_VBI_START
    reset_format['count'] = BT8X8_DEFAULT_VBI_COUNT
    applied_format = set_vbi_capture_format_path(device, reset_format)

    if applied_format['start'] != BT8X8_DEFAULT_VBI_START or applied_format['count'] != BT8X8_DEFAULT_VBI_COUNT:
        raise click.UsageError(
            f'Device {device} did not accept the default BT878 VBI format. '
            f'Got start/count {applied_format["start"]}/{applied_format["count"]}.'
        )

    click.echo(
        f'{device}: restored VBI start/count to '
        f'{BT8X8_DEFAULT_VBI_START[0]}/{BT8X8_DEFAULT_VBI_START[1]} '
        f'and {BT8X8_DEFAULT_VBI_COUNT[0]}/{BT8X8_DEFAULT_VBI_COUNT[1]}.'
    )

@teletext.command()
@click.argument('output', type=click.File('wb'), default='-')
@click.option('-d', '--device', type=click.File('rb'), default='/dev/vbi0', help='Capture device.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@vbiformatparams
@fixcaptureparams
@timerparam
@click.option('-vtn', '--vbi-tune', is_flag=True, help='Open the VBI tuning window before starting capture.')
@carduser()
def record(output, device, ignore_lines, used_lines, vbi_start, vbi_count, vbi_terminate_reset, fix_capture_card, timer, vbi_tune, config):

    """Record VBI samples from a capture device."""

    import struct
    import sys

    if output.name.startswith('/dev/vbi'):
        raise click.UsageError(f'Refusing to write output to VBI device. Did you mean -d?')

    original_vbi_format = None
    pause_controller = None
    fixer = None

    try:
        config, original_vbi_format = apply_vbi_record_format(device, config, vbi_start=vbi_start, vbi_count=vbi_count)
        _, ignore_lines = resolve_frame_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
        ignored_ranges = ignored_frame_line_byte_ranges(config, ignore_lines)
        preserve_tail = 4 if config.card == 'bt8x8' else 0
        selected_lines = current_line_selection(config, ignore_lines=ignore_lines)
        fix_capture_card_settings = current_fix_capture_card(fix_capture_card)
        neutral_controls = current_signal_controls(
            '50',
            '50',
            '50',
            '50',
            '0',
            '0',
            '0',
            '0',
            '0',
            '0',
            '0',
            '0',
        )

        if vbi_tune:
            result = open_tuning_dialog(
                'VBI Tune - Record',
                *neutral_controls,
                config=config,
                line_selection=selected_lines,
                fix_capture_card=fix_capture_card_settings,
                visible_sections=('Line Selection', 'Fix Capture Card', 'Record Timer'),
                timer_seconds=timer,
                show_timer=True,
            )
            if result is None:
                return
            _, _, selected_lines, fix_capture_card_settings, timer = result
            ignore_lines = frozenset(range(1, useful_frame_lines(config) + 1)) - frozenset(selected_lines)
            ignored_ranges = ignored_frame_line_byte_ranges(config, ignore_lines)
            try:
                device.seek(0)
            except (AttributeError, OSError):
                pass

        chunks = FileChunker(device, config.line_bytes*config.field_lines*2)
        pause_controller = PauseController('record')
        bar = tqdm(wrap_live_iterable(chunks, pause_controller=pause_controller, timer_seconds=timer, label='record'), unit=' Frames')
        fixer = CaptureCardFixer()
        fixer.update(fix_capture_card_settings)

        prev_seq = None
        dropped = 0

        for n, chunk in bar:
            if ignored_ranges:
                chunk = blank_ignored_frame_lines(chunk, ignored_ranges, preserve_tail=preserve_tail)
            output.write(chunk)
            if config.card == 'bt8x8':
                seq, = struct.unpack('<I', chunk[-4:])
                if prev_seq is not None and seq != (prev_seq + 1):
                   dropped += 1
                   sys.stderr.write('Frame drop? %d\n' % dropped)
                prev_seq = seq

    except KeyboardInterrupt:
        pass
    finally:
        if pause_controller is not None:
            pause_controller.close()
        if fixer is not None:
            fixer.close()
        device_path = getattr(device, 'name', None)
        restore_format = original_vbi_format
        _close_handle_quietly(device)
        if vbi_terminate_reset and device_path:
            try:
                restore_format = bt8x8_default_vbi_capture_format(get_vbi_capture_format_path(device_path))
            except click.UsageError as exc:
                click.echo(f'Warning: {exc}', err=True)
                restore_format = None
        if restore_format is not None:
            try:
                restore_vbi_capture_format_path(device_path, restore_format)
            except click.UsageError as exc:
                click.echo(f'Warning: {exc}', err=True)
            except OSError:
                pass


@teletext.command()
@click.option('-p', '--pause', is_flag=True, help='Start the viewer paused.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@linequalityparam
@click.option('-n', '--n-lines', type=int, default=None, help='Number of lines to display. Overrides card config.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@vbiformatparams
@signalcontrolparams
@click.option('-vtnl', '--vbi-tune-live', is_flag=True, help='Open the VBI tuning window with live preview instead of the OpenGL viewer.')
@carduser(extended=True)
@chunkreader()
def vbiview(chunker, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, vbi_start, vbi_count, vbi_terminate_reset, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune_live):

    """Display raw VBI samples with OpenGL."""

    try:
        from teletext.vbi.viewer import VBIViewer
    except ModuleNotFoundError as e:
        if e.name.startswith('OpenGL'):
            raise click.UsageError(f'{e.msg}. PyOpenGL is not installed. VBI viewer is not available.')
        else:
            raise e
    else:
        from teletext.vbi.line import Line
        input_path = chunker_input_path(chunker)
        input_handle = chunker_input_handle(chunker)
        original_vbi_format = None
        config, original_vbi_format = apply_vbi_runtime_format(
            input_handle,
            input_path,
            config,
            vbi_start=vbi_start,
            vbi_count=vbi_count,
        )
        controls = current_signal_controls(
            brightness,
            sharpness,
            gain,
            contrast,
            impulse_filter,
            temporal_denoise,
            noise_reduction,
            hum_removal,
            auto_black_level,
            head_switching_mask,
            line_to_line_stabilization,
            auto_gain_contrast,
        )
        brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, impulse_filter_coeff, temporal_denoise_coeff, noise_reduction_coeff, hum_removal_coeff, auto_black_level_coeff, head_switching_mask_coeff, line_to_line_stabilization_coeff, auto_gain_contrast_coeff = controls
        line_quality_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
        clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
        start_lock_coeff = START_LOCK_COEFF_DEFAULT
        adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
        dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
        wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT
        line_control_overrides = {}
        line_decoder_overrides = {}

        def build_decoder_tuning_state(current_config=config, current_tape_format=tape_format):
            return current_decoder_tuning(
                current_config,
                current_tape_format,
                line_quality,
                line_quality_coeff,
                clock_lock,
                clock_lock_coeff,
                start_lock,
                start_lock_coeff,
                adaptive_threshold,
                adaptive_threshold_coeff,
                dropout_repair,
                dropout_repair_coeff,
                wow_flutter_compensation,
                wow_flutter_compensation_coeff,
                auto_line_align,
                per_line_shift,
                line_control_overrides,
                line_decoder_overrides,
                show_quality,
                show_rejects,
                show_start_clock,
                show_clock_visuals,
                show_alignment_visuals,
                show_quality_meter,
                show_histogram_graph,
                show_eye_pattern,
            )

        decoder_state = build_decoder_tuning_state()
        line_quality = decoder_state['quality_threshold']
        line_quality_coeff = decoder_state['quality_threshold_coeff']
        clock_lock = decoder_state['clock_lock']
        clock_lock_coeff = decoder_state['clock_lock_coeff']
        start_lock = decoder_state['start_lock']
        start_lock_coeff = decoder_state['start_lock_coeff']
        adaptive_threshold = decoder_state['adaptive_threshold']
        adaptive_threshold_coeff = decoder_state['adaptive_threshold_coeff']
        dropout_repair = decoder_state['dropout_repair']
        dropout_repair_coeff = decoder_state['dropout_repair_coeff']
        wow_flutter_compensation = decoder_state['wow_flutter_compensation']
        wow_flutter_compensation_coeff = decoder_state['wow_flutter_compensation_coeff']
        auto_line_align = decoder_state['auto_line_align']
        per_line_shift = decoder_state['per_line_shift']
        line_control_overrides = decoder_state.get('line_control_overrides', {})
        line_decoder_overrides = decoder_state.get('line_decoder_overrides', {})
        show_quality = decoder_state['show_quality']
        show_rejects = decoder_state['show_rejects']
        show_start_clock = decoder_state['show_start_clock']
        show_clock_visuals = decoder_state['show_clock_visuals']
        show_alignment_visuals = decoder_state['show_alignment_visuals']
        show_quality_meter = decoder_state['show_quality_meter']
        show_histogram_graph = decoder_state['show_histogram_graph']
        show_eye_pattern = decoder_state['show_eye_pattern']

        Line.configure(
            config,
            force_cpu=True,
            tape_format=tape_format,
            brightness=brightness,
            sharpness=sharpness,
            gain=gain,
            contrast=contrast,
            brightness_coeff=brightness_coeff,
            sharpness_coeff=sharpness_coeff,
            gain_coeff=gain_coeff,
            contrast_coeff=contrast_coeff,
            impulse_filter=impulse_filter,
            temporal_denoise=temporal_denoise,
            noise_reduction=noise_reduction,
            hum_removal=hum_removal,
            auto_black_level=auto_black_level,
            head_switching_mask=head_switching_mask,
            line_stabilization=line_to_line_stabilization,
            auto_gain_contrast=auto_gain_contrast,
            impulse_filter_coeff=impulse_filter_coeff,
            temporal_denoise_coeff=temporal_denoise_coeff,
            noise_reduction_coeff=noise_reduction_coeff,
            hum_removal_coeff=hum_removal_coeff,
            auto_black_level_coeff=auto_black_level_coeff,
            head_switching_mask_coeff=head_switching_mask_coeff,
            line_stabilization_coeff=line_to_line_stabilization_coeff,
            auto_gain_contrast_coeff=auto_gain_contrast_coeff,
            quality_threshold=line_quality,
            quality_threshold_coeff=line_quality_coeff,
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

        if n_lines is not None:
            chunks = chunker(config.line_bytes, n_lines, range(n_lines))
        else:
            chunks = chunker(config.line_bytes, config.field_lines, config.field_range)

        lines = (Line(chunk, number) for number, chunk in chunks)
        live_tuner = None
        signal_controls = None
        selected_lines = current_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
        line_selection = lambda: selected_lines
        analysis_source = None
        if input_path is not None:
            analysis_source = {
                'input_path': input_path,
                'n_lines': n_lines,
            }
        if vbi_tune_live:
            live_tuner = open_live_tuner(
                'VBI Tune Live',
                brightness, sharpness, gain, contrast,
                brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff,
                impulse_filter,
                temporal_denoise,
                noise_reduction, hum_removal, auto_black_level,
                head_switching_mask, line_to_line_stabilization, auto_gain_contrast,
                impulse_filter_coeff,
                temporal_denoise_coeff,
                noise_reduction_coeff, hum_removal_coeff, auto_black_level_coeff,
                head_switching_mask_coeff, line_to_line_stabilization_coeff, auto_gain_contrast_coeff,
                config=config,
                tape_format=tape_format,
                decoder_tuning=build_decoder_tuning_state(),
                tape_formats=Config.tape_formats,
                line_selection=selected_lines,
                line_count=useful_frame_lines(config),
                analysis_source=analysis_source,
                visible_sections=('Signal Controls', 'Signal Cleanup', 'Decoder Tuning', 'Diagnostics', 'Line Selection', 'Tools', 'Args / Preset'),
            )
            signal_controls = live_tuner.values
            decoder_tuning = live_tuner.decoder_tuning
            line_selection = live_tuner.line_selection
        else:
            decoder_tuning = build_decoder_tuning_state()

        try:
            VBIViewer(lines, config, pause=pause, nlines=n_lines, signal_controls=signal_controls, decoder_tuning=decoder_tuning, tape_format=tape_format, line_selection=line_selection)
        finally:
            if live_tuner is not None:
                live_tuner.close()
            restore_format = original_vbi_format
            _close_handle_quietly(input_handle)
            if vbi_terminate_reset and input_path and os.path.abspath(input_path).startswith('/dev/vbi'):
                try:
                    restore_format = bt8x8_default_vbi_capture_format(get_vbi_capture_format_path(input_path))
                except click.UsageError as exc:
                    click.echo(f'Warning: {exc}', err=True)
                    restore_format = None
            if restore_format is not None:
                try:
                    restore_vbi_capture_format_path(input_path, restore_format)
                except click.UsageError as exc:
                    click.echo(f'Warning: {exc}', err=True)
                except OSError:
                    pass


def _launch_vbi_tool_editor(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune):
    return _vbitool_impl(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune)


@teletext.command(name='vbitool')
@click.argument('input_path', type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option('-p', '--pause/--play', default=True, help='Start playback paused.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@linequalityparam
@click.option('-n', '--n-lines', type=int, default=None, help='Number of lines to display. Overrides card config.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@click.option('-vtn', '--vbi-tune', is_flag=True, help='Open the VBI tuning window before starting the crop editor.')
@carduser(extended=True)
def vbitool(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune):

    """Open the VBI tool editor for a recorded .vbi file."""

    return _launch_vbi_tool_editor(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune)


@teletext.command(name='vbicrop', hidden=True)
@click.argument('input_path', type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option('-p', '--pause/--play', default=True, help='Start playback paused.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@linequalityparam
@click.option('-n', '--n-lines', type=int, default=None, help='Number of lines to display. Overrides card config.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@click.option('-vtn', '--vbi-tune', is_flag=True, help='Open the VBI tuning window before starting the crop editor.')
@carduser(extended=True)
def vbicrop(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune):

    """Backward-compatible alias for vbitool."""

    return _launch_vbi_tool_editor(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune)


def _vbitool_impl(input_path, config, pause, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, vbi_tune):

    """Open the VBI tool editor for a recorded .vbi file."""

    try:
        from teletext.gui.vbicrop import create_crop_state, run_crop_window, DEFAULT_FRAME_RATE
    except ModuleNotFoundError as e:
        if e.name == 'PyQt5':
            raise click.UsageError(f'{e.msg}. PyQt5 is not installed. VBI crop window is not available.')
        raise

    try:
        from teletext.vbi.cropviewer import launch_crop_viewer
    except ModuleNotFoundError as e:
        if e.name.startswith('OpenGL'):
            raise click.UsageError(f'{e.msg}. PyOpenGL is not installed. VBI crop viewer is not available.')
        raise

    total_frames = count_complete_frames(input_path, config)
    if total_frames <= 0:
        raise click.UsageError('Input file does not contain any complete VBI frames.')

    selected_lines = current_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
    controls = current_signal_controls(
        brightness,
        sharpness,
        gain,
        contrast,
        impulse_filter,
        temporal_denoise,
        noise_reduction,
        hum_removal,
        auto_black_level,
        head_switching_mask,
        line_to_line_stabilization,
        auto_gain_contrast,
    )
    line_quality_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
    clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
    start_lock_coeff = START_LOCK_COEFF_DEFAULT
    adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
    dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
    wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT
    line_control_overrides = {}
    line_decoder_overrides = {}
    state = {
        'config': config,
        'tape_format': tape_format,
        'controls': controls,
        'selected_lines': frozenset(selected_lines),
    }

    def build_decoder_tuning_state(current_config=None, current_tape_format=None):
        return current_decoder_tuning(
            current_config or state['config'],
            current_tape_format or state['tape_format'],
            line_quality,
            line_quality_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
        )

    decoder_state = build_decoder_tuning_state()
    line_quality = decoder_state['quality_threshold']
    line_quality_coeff = decoder_state['quality_threshold_coeff']
    clock_lock = decoder_state['clock_lock']
    clock_lock_coeff = decoder_state['clock_lock_coeff']
    start_lock = decoder_state['start_lock']
    start_lock_coeff = decoder_state['start_lock_coeff']
    adaptive_threshold = decoder_state['adaptive_threshold']
    adaptive_threshold_coeff = decoder_state['adaptive_threshold_coeff']
    dropout_repair = decoder_state['dropout_repair']
    dropout_repair_coeff = decoder_state['dropout_repair_coeff']
    wow_flutter_compensation = decoder_state['wow_flutter_compensation']
    wow_flutter_compensation_coeff = decoder_state['wow_flutter_compensation_coeff']
    auto_line_align = decoder_state['auto_line_align']
    per_line_shift = decoder_state['per_line_shift']
    line_control_overrides = decoder_state.get('line_control_overrides', {})
    line_decoder_overrides = decoder_state.get('line_decoder_overrides', {})
    show_quality = decoder_state['show_quality']
    show_rejects = decoder_state['show_rejects']
    show_start_clock = decoder_state['show_start_clock']
    show_clock_visuals = decoder_state['show_clock_visuals']
    show_alignment_visuals = decoder_state['show_alignment_visuals']
    show_quality_meter = decoder_state['show_quality_meter']
    show_histogram_graph = decoder_state['show_histogram_graph']
    show_eye_pattern = decoder_state['show_eye_pattern']

    if vbi_tune:
        result = open_tuning_dialog(
            'VBI Tune - Tool',
            *state['controls'],
            decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            visible_sections=('Signal Controls', 'Signal Cleanup', 'Decoder Tuning', 'Diagnostics', 'Line Selection', 'Args / Preset'),
        )
        if result is None:
            return
        state['controls'], decoder_tuning, state['selected_lines'], _ = result
        state['config'], state['tape_format'], line_quality, line_quality_coeff, clock_lock, clock_lock_coeff, start_lock, start_lock_coeff, adaptive_threshold, adaptive_threshold_coeff, dropout_repair, dropout_repair_coeff, wow_flutter_compensation, wow_flutter_compensation_coeff, auto_line_align, per_line_shift, line_control_overrides, line_decoder_overrides, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern = apply_decoder_tuning(
            state['config'],
            state['tape_format'],
            line_quality,
            line_quality_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
            decoder_tuning,
        )

    crop_state = create_crop_state(total_frames=total_frames, current_frame=0, playing=not pause)

    live_tuner = None
    viewer_process = None

    def restart_viewer():
        nonlocal viewer_process
        if viewer_process is not None and viewer_process.is_alive():
            viewer_process.terminate()
            viewer_process.join(timeout=1)
        viewer_process = launch_crop_viewer(
            input_path=input_path,
            config=state['config'],
            crop_state=crop_state,
            total_frames=total_frames,
            frame_rate=DEFAULT_FRAME_RATE,
            pause=pause,
            tape_format=state['tape_format'],
            n_lines=n_lines,
            live_tuner=live_tuner,
            fixed_controls=state['controls'],
            fixed_decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            fixed_line_selection=state['selected_lines'],
            window_name='VBI Tool',
        )

    restart_viewer()

    def save_callback(output_path, cut_ranges, insertions):
        current_controls_value = state['controls'] if live_tuner is None else live_tuner.values()
        current_line_selection_value = state['selected_lines'] if live_tuner is None else live_tuner.line_selection()
        current_decoder_tuning_value = build_decoder_tuning_state(state['config'], state['tape_format']) if live_tuner is None else (live_tuner.decoder_tuning() or build_decoder_tuning_state(state['config'], state['tape_format']))
        save_edited_vbi(
            input_path=input_path,
            output_path=output_path,
            config=state['config'],
            controls=current_controls_value,
            line_selection=current_line_selection_value,
            decoder_tuning=current_decoder_tuning_value,
            cut_ranges=cut_ranges,
            insertions=insertions,
        )

    def error_scan_callback(progress_callback=None):
        return scan_vbicrop_error_ranges(
            input_path=input_path,
            config=state['config'],
            tape_format=state['tape_format'],
            controls=state['controls'],
            decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            line_selection=state['selected_lines'],
            n_lines=n_lines,
            progress_callback=progress_callback,
        )

    try:
        run_crop_window(
            state=crop_state,
            total_frames=total_frames,
            frame_rate=DEFAULT_FRAME_RATE,
            save_callback=save_callback,
            viewer_process=lambda: viewer_process,
            frame_size_bytes=frame_size_for_config(state['config']),
            error_scan_callback=error_scan_callback,
        )
    finally:
        if viewer_process is not None and viewer_process.is_alive():
            viewer_process.terminate()
            viewer_process.join(timeout=1)
        if live_tuner is not None:
            live_tuner.close()


@teletext.command()
@click.option('-p', '--pause/--play', default=True, help='Start playback paused.')
@click.option('-M', '--mode', type=click.Choice(['deconvolve', 'slice']), default='deconvolve', help='Diagnostics decode mode.')
@click.option('-8', '--eight-bit', is_flag=True, help='Treat rows 1-25 as 8-bit data without parity check.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@linequalityparam
@click.option('-n', '--n-lines', type=int, default=None, help='Number of lines to display. Overrides card config.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@fixcaptureparams
@previewtuneparams
@carduser(extended=True)
@click.argument('input_path', type=click.Path(exists=True, dir_okay=False, readable=True))
def vbirepair(input_path, config, pause, mode, eight_bit, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, fix_capture_card, vbi_tune, vbi_tune_live):

    """Open the VBI repair tool for a recorded .vbi file."""

    if vbi_tune and vbi_tune_live:
        raise click.UsageError("Use either -vtn or -vtnl, not both.")

    try:
        from teletext.gui.vbicrop import create_crop_state, DEFAULT_FRAME_RATE
        from teletext.gui.vbirepair import run_repair_window
    except ModuleNotFoundError as e:
        if e.name == 'PyQt5':
            raise click.UsageError(f'{e.msg}. PyQt5 is not installed. VBI repair window is not available.')
        raise

    try:
        from teletext.vbi.cropviewer import launch_crop_viewer
    except ModuleNotFoundError as e:
        if e.name.startswith('OpenGL'):
            raise click.UsageError(f'{e.msg}. PyOpenGL is not installed. VBI repair viewer is not available.')
        raise

    total_frames = count_complete_frames(input_path, config)
    if total_frames <= 0:
        raise click.UsageError('Input file does not contain any complete VBI frames.')

    selected_lines = current_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
    controls = current_signal_controls(
        brightness,
        sharpness,
        gain,
        contrast,
        impulse_filter,
        temporal_denoise,
        noise_reduction,
        hum_removal,
        auto_black_level,
        head_switching_mask,
        line_to_line_stabilization,
        auto_gain_contrast,
    )
    line_quality_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
    clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
    start_lock_coeff = START_LOCK_COEFF_DEFAULT
    adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
    dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
    wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT
    fix_capture_card_settings = current_fix_capture_card(fix_capture_card)
    state = {
        'config': config,
        'tape_format': tape_format,
        'controls': controls,
        'selected_lines': frozenset(selected_lines),
        'fix_capture_card': fix_capture_card_settings,
    }
    line_control_overrides = {}
    line_decoder_overrides = {}
    repair_live_defaults_applied = False
    analysis_source = {
        'input_path': os.path.abspath(input_path),
        'n_lines': n_lines,
    }

    def ensure_repair_live_defaults():
        nonlocal show_quality, show_start_clock, repair_live_defaults_applied
        if repair_live_defaults_applied:
            return
        show_quality = True
        show_start_clock = True
        repair_live_defaults_applied = True

    def build_decoder_tuning_state(current_config=None, current_tape_format=None):
        return current_decoder_tuning(
            current_config or state['config'],
            current_tape_format or state['tape_format'],
            line_quality,
            line_quality_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
        )

    decoder_state = build_decoder_tuning_state()
    line_quality = decoder_state['quality_threshold']
    line_quality_coeff = decoder_state['quality_threshold_coeff']
    clock_lock = decoder_state['clock_lock']
    clock_lock_coeff = decoder_state['clock_lock_coeff']
    start_lock = decoder_state['start_lock']
    start_lock_coeff = decoder_state['start_lock_coeff']
    adaptive_threshold = decoder_state['adaptive_threshold']
    adaptive_threshold_coeff = decoder_state['adaptive_threshold_coeff']
    dropout_repair = decoder_state['dropout_repair']
    dropout_repair_coeff = decoder_state['dropout_repair_coeff']
    wow_flutter_compensation = decoder_state['wow_flutter_compensation']
    wow_flutter_compensation_coeff = decoder_state['wow_flutter_compensation_coeff']
    auto_line_align = decoder_state['auto_line_align']
    per_line_shift = decoder_state['per_line_shift']
    line_control_overrides = decoder_state.get('line_control_overrides', {})
    line_decoder_overrides = decoder_state.get('line_decoder_overrides', {})
    show_quality = decoder_state['show_quality']
    show_rejects = decoder_state['show_rejects']
    show_start_clock = decoder_state['show_start_clock']
    show_clock_visuals = decoder_state['show_clock_visuals']
    show_alignment_visuals = decoder_state['show_alignment_visuals']
    show_quality_meter = decoder_state['show_quality_meter']
    show_histogram_graph = decoder_state['show_histogram_graph']
    show_eye_pattern = decoder_state['show_eye_pattern']

    if vbi_tune:
        result = open_tuning_dialog(
            'VBI Tune - Repair',
            *state['controls'],
            preview_provider=make_vbi_file_preview_provider(input_path, state['config'], n_lines=n_lines),
            config=state['config'],
            tape_format=state['tape_format'],
            decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            fix_capture_card=state['fix_capture_card'],
            analysis_source=analysis_source,
        )
        if result is None:
            return
        state['controls'], decoder_tuning, state['selected_lines'], state['fix_capture_card'] = result
        state['config'], state['tape_format'], line_quality, line_quality_coeff, clock_lock, clock_lock_coeff, start_lock, start_lock_coeff, adaptive_threshold, adaptive_threshold_coeff, dropout_repair, dropout_repair_coeff, wow_flutter_compensation, wow_flutter_compensation_coeff, auto_line_align, per_line_shift, line_control_overrides, line_decoder_overrides, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern = apply_decoder_tuning(
            state['config'],
            state['tape_format'],
            line_quality,
            line_quality_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
            decoder_tuning,
        )
        repair_live_defaults_applied = True

    repair_state = create_crop_state(total_frames=total_frames, current_frame=0, playing=not pause)

    live_tuner = None
    fixer = None
    fixer_stop = None
    fixer_thread = None
    viewer_process = None
    stabilize_preview_path = None
    stabilize_preview_total_frames = None
    stabilize_preview_start_frame = 0

    def current_viewer_input_path():
        return stabilize_preview_path or input_path

    def current_viewer_total_frames():
        return int(stabilize_preview_total_frames or total_frames)

    def current_viewer_frame_offset():
        if stabilize_preview_path:
            return int(stabilize_preview_start_frame)
        return 0

    if vbi_tune_live:
        ensure_repair_live_defaults()
        live_tuner = open_live_tuner(
            'VBI Tune Live - Repair',
            *state['controls'],
            config=state['config'],
            tape_format=state['tape_format'],
            decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            fix_capture_card=state['fix_capture_card'],
            analysis_source=analysis_source,
            visible_sections=('Signal Controls', 'Signal Cleanup', 'Decoder Tuning', 'Diagnostics', 'Line Selection', 'Tools', 'Args / Preset'),
        )
        fixer, fixer_stop, fixer_thread = start_live_fix_capture_card(live_tuner, state['fix_capture_card'])
    else:
        fixer = CaptureCardFixer()
        fixer.update(state['fix_capture_card'])

    def sync_state_from_live_tuner():
        nonlocal line_quality, line_quality_coeff, clock_lock, clock_lock_coeff, start_lock, start_lock_coeff
        nonlocal adaptive_threshold, adaptive_threshold_coeff, dropout_repair, dropout_repair_coeff
        nonlocal wow_flutter_compensation, wow_flutter_compensation_coeff, auto_line_align, per_line_shift
        nonlocal line_control_overrides, line_decoder_overrides
        nonlocal show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern
        if live_tuner is None:
            return
        state['controls'] = live_tuner.values()
        decoder_tuning = live_tuner.decoder_tuning()
        if decoder_tuning is not None:
            state['config'], state['tape_format'], line_quality, line_quality_coeff, clock_lock, clock_lock_coeff, start_lock, start_lock_coeff, adaptive_threshold, adaptive_threshold_coeff, dropout_repair, dropout_repair_coeff, wow_flutter_compensation, wow_flutter_compensation_coeff, auto_line_align, per_line_shift, line_control_overrides, line_decoder_overrides, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern = apply_decoder_tuning(
                state['config'],
                state['tape_format'],
                line_quality,
                line_quality_coeff,
                clock_lock,
                clock_lock_coeff,
                start_lock,
                start_lock_coeff,
                adaptive_threshold,
                adaptive_threshold_coeff,
                dropout_repair,
                dropout_repair_coeff,
                wow_flutter_compensation,
                wow_flutter_compensation_coeff,
                auto_line_align,
                per_line_shift,
                line_control_overrides,
                line_decoder_overrides,
                show_quality,
                show_rejects,
                show_start_clock,
                show_clock_visuals,
                show_alignment_visuals,
                show_quality_meter,
                show_histogram_graph,
                show_eye_pattern,
                decoder_tuning,
            )
        state['selected_lines'] = live_tuner.line_selection()
        state['fix_capture_card'] = live_tuner.fix_capture_card()

    def stop_fix_capture_card_runtime():
        nonlocal fixer, fixer_stop, fixer_thread
        if fixer_stop is not None:
            fixer_stop.set()
            fixer_stop = None
        if fixer_thread is not None:
            fixer_thread.join(timeout=1)
            fixer_thread = None
        if fixer is not None:
            fixer.close()
            fixer = None

    def restart_viewer():
        nonlocal viewer_process
        if viewer_process is not None and viewer_process.is_alive():
            viewer_process.terminate()
            viewer_process.join(timeout=1)
        viewer_process = launch_crop_viewer(
            input_path=current_viewer_input_path(),
            config=state['config'],
            crop_state=repair_state,
            total_frames=current_viewer_total_frames(),
            frame_rate=DEFAULT_FRAME_RATE,
            pause=pause,
            tape_format=state['tape_format'],
            n_lines=n_lines,
            live_tuner=live_tuner,
            fixed_controls=state['controls'],
            fixed_decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            fixed_line_selection=state['selected_lines'],
            window_name='VBI Repair Preview' if stabilize_preview_path else 'VBI Repair',
        )

    restart_viewer()

    def open_live_tune_callback():
        nonlocal live_tuner, fixer, fixer_stop, fixer_thread
        if live_tuner is not None and live_tuner.is_alive():
            return
        if live_tuner is not None:
            sync_state_from_live_tuner()
            live_tuner.close()
            live_tuner = None
        stop_fix_capture_card_runtime()
        ensure_repair_live_defaults()
        live_tuner = open_live_tuner(
            'VBI Tune Live - Repair',
            *state['controls'],
            config=state['config'],
            tape_format=state['tape_format'],
            decoder_tuning=build_decoder_tuning_state(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            fix_capture_card=state['fix_capture_card'],
            analysis_source=analysis_source,
            visible_sections=('Signal Controls', 'Signal Cleanup', 'Decoder Tuning', 'Diagnostics', 'Line Selection', 'Tools', 'Args / Preset'),
        )
        fixer, fixer_stop, fixer_thread = start_live_fix_capture_card(live_tuner, state['fix_capture_card'])
        restart_viewer()

    def repair_runtime_state():
        sync_state_from_live_tuner()
        return {
            'config': state['config'],
            'tape_format': state['tape_format'],
            'controls': state['controls'] if live_tuner is None else live_tuner.values(),
            'selected_lines': state['selected_lines'] if live_tuner is None else live_tuner.line_selection(),
            'decoder_tuning': build_decoder_tuning_state(state['config'], state['tape_format']) if live_tuner is None else (live_tuner.decoder_tuning() or build_decoder_tuning_state(state['config'], state['tape_format'])),
        }

    diagnostics = VBIRepairDiagnostics(
        input_path,
        state_provider=repair_runtime_state,
        mode=mode,
        eight_bit=eight_bit,
        n_lines=n_lines,
        page_history_frames=15,
    )

    def save_callback(output_path):
        current_controls_value = state['controls'] if live_tuner is None else live_tuner.values()
        current_line_selection_value = state['selected_lines'] if live_tuner is None else live_tuner.line_selection()
        current_decoder_tuning_value = build_decoder_tuning_state(state['config'], state['tape_format']) if live_tuner is None else (live_tuner.decoder_tuning() or build_decoder_tuning_state(state['config'], state['tape_format']))
        save_cropped_vbi(
            input_path=input_path,
            output_path=output_path,
            config=state['config'],
            start_frame=0,
            end_frame=total_frames - 1,
            controls=current_controls_value,
            line_selection=current_line_selection_value,
            decoder_tuning=current_decoder_tuning_value,
        )

    def stabilize_callback(output_path, global_shift, *, lock_mode, target_center, target_right_edge, reference_mode='median', reference_line=1, tolerance=3, quick_preview=False, preview_frames=300, start_frame=0, progress_callback=None):
        current_controls_value = state['controls'] if live_tuner is None else live_tuner.values()
        current_line_selection_value = state['selected_lines'] if live_tuner is None else live_tuner.line_selection()
        current_decoder_tuning_value = build_decoder_tuning_state(state['config'], state['tape_format']) if live_tuner is None else (live_tuner.decoder_tuning() or build_decoder_tuning_state(state['config'], state['tape_format']))
        return stabilize_repair_vbi(
            input_path=input_path,
            output_path=output_path,
            config=state['config'],
            tape_format=state['tape_format'],
            controls=current_controls_value,
            line_selection=current_line_selection_value,
            decoder_tuning=current_decoder_tuning_value,
            global_shift=global_shift,
            n_lines=n_lines,
            start_frame=start_frame if quick_preview else 0,
            frame_count=preview_frames if quick_preview else None,
            lock_mode=lock_mode,
            target_center=target_center,
            target_right_edge=target_right_edge,
            reference_mode=reference_mode,
            reference_line=reference_line,
            tolerance=tolerance,
            progress_callback=progress_callback,
        )

    def stabilize_analysis_callback(*, global_shift, lock_mode, target_center, target_right_edge, reference_mode='median', reference_line=1, tolerance=3, quick_preview=False, preview_frames=300, start_frame=0, progress_callback=None):
        current_controls_value = state['controls'] if live_tuner is None else live_tuner.values()
        current_line_selection_value = state['selected_lines'] if live_tuner is None else live_tuner.line_selection()
        current_decoder_tuning_value = build_decoder_tuning_state(state['config'], state['tape_format']) if live_tuner is None else (live_tuner.decoder_tuning() or build_decoder_tuning_state(state['config'], state['tape_format']))
        return analyse_stabilize_repair_vbi(
            input_path=input_path,
            config=state['config'],
            tape_format=state['tape_format'],
            controls=current_controls_value,
            line_selection=current_line_selection_value,
            decoder_tuning=current_decoder_tuning_value,
            n_lines=n_lines,
            start_frame=start_frame,
            frame_count=1,
            lock_mode=lock_mode,
            target_center=target_center,
            target_right_edge=target_right_edge,
            reference_mode=reference_mode,
            reference_line=reference_line,
            tolerance=tolerance,
            progress_callback=progress_callback,
        )

    def clear_stabilize_preview():
        nonlocal stabilize_preview_path, stabilize_preview_total_frames, stabilize_preview_start_frame
        previous_path = stabilize_preview_path
        stabilize_preview_path = None
        stabilize_preview_total_frames = None
        stabilize_preview_start_frame = 0
        restart_viewer()
        if previous_path and os.path.exists(previous_path):
            try:
                os.remove(previous_path)
            except OSError:
                pass

    def stabilize_preview_callback(*, global_shift, lock_mode, target_center, target_right_edge, reference_mode='median', reference_line=1, tolerance=3, quick_preview=False, preview_frames=300, start_frame=0):
        nonlocal stabilize_preview_path, stabilize_preview_total_frames, stabilize_preview_start_frame
        current_controls_value = state['controls'] if live_tuner is None else live_tuner.values()
        current_line_selection_value = state['selected_lines'] if live_tuner is None else live_tuner.line_selection()
        current_decoder_tuning_value = build_decoder_tuning_state(state['config'], state['tape_format']) if live_tuner is None else (live_tuner.decoder_tuning() or build_decoder_tuning_state(state['config'], state['tape_format']))
        preview_count = 1
        fd, preview_path = tempfile.mkstemp(prefix='vhsttx-repair-preview-', suffix='.vbi')
        os.close(fd)
        try:
            analysis = stabilize_repair_vbi(
                input_path=input_path,
                output_path=preview_path,
                config=state['config'],
                tape_format=state['tape_format'],
                controls=current_controls_value,
                line_selection=current_line_selection_value,
                decoder_tuning=current_decoder_tuning_value,
                global_shift=global_shift,
                n_lines=n_lines,
                start_frame=start_frame,
                frame_count=preview_count,
                lock_mode=lock_mode,
                target_center=target_center,
                target_right_edge=target_right_edge,
                reference_mode=reference_mode,
                reference_line=reference_line,
                tolerance=tolerance,
                progress_callback=None,
            )
        except Exception:
            try:
                os.remove(preview_path)
            except OSError:
                pass
            raise
        previous_path = stabilize_preview_path
        stabilize_preview_path = preview_path
        stabilize_preview_total_frames = max(count_complete_frames(preview_path, state['config']), 1)
        stabilize_preview_start_frame = max(int(start_frame), 0)
        restart_viewer()
        if previous_path and os.path.exists(previous_path):
            try:
                os.remove(previous_path)
            except OSError:
                pass
        return analysis

    def save_page_callback(frame_index, page_text, subpage_text, include_noise, output_path):
        return diagnostics.export_page_t42(
            frame_index,
            page_text,
            subpage_text,
            output_path,
            hide_noisy=not bool(include_noise),
        )

    try:
        run_repair_window(
            state=repair_state,
            total_frames=total_frames,
            frame_rate=DEFAULT_FRAME_RATE,
            save_callback=save_callback,
            stabilize_callback=stabilize_callback,
            stabilize_analysis_callback=stabilize_analysis_callback,
            stabilize_default_path=os.path.join(
                os.path.dirname(os.path.abspath(input_path)),
                f"{os.path.splitext(os.path.basename(input_path))[0]}-stabilized.vbi",
            ),
            stabilize_line_count=useful_frame_lines(state['config']),
            stabilize_preview_callback=stabilize_preview_callback,
            clear_stabilize_preview_callback=clear_stabilize_preview,
            save_page_callback=save_page_callback,
            live_tune_callback=open_live_tune_callback,
            viewer_process=lambda: viewer_process,
            diagnostics_callback=diagnostics,
        )
    finally:
        diagnostics.close()
        if viewer_process is not None and viewer_process.is_alive():
            viewer_process.terminate()
            viewer_process.join(timeout=1)
        if stabilize_preview_path and os.path.exists(stabilize_preview_path):
            try:
                os.remove(stabilize_preview_path)
            except OSError:
                pass
        stop_fix_capture_card_runtime()
        if live_tuner is not None:
            live_tuner.close()


def _launch_t42_tool_editor(input_path):
    try:
        from teletext.gui.t42crop import (
            load_t42_entries,
            run_t42_tool_window,
            write_t42_entries,
        )
    except ModuleNotFoundError as e:
        if e.name == 'PyQt5':
            raise click.UsageError(f'{e.msg}. PyQt5 is not installed. T42 tool window is not available.')
        raise

    entries = ()
    if input_path:
        entries = load_t42_entries(input_path)

    def save_callback(output_path, final_entries):
        write_t42_entries(final_entries, output_path)

    run_t42_tool_window(
        input_path=input_path,
        entries=entries,
        save_callback=save_callback,
    )


@teletext.command(name='t42tool')
@click.argument('input_path', required=False, type=click.Path(exists=True, dir_okay=False, readable=True))
def t42tool(input_path):

    """Open the T42 tool editor for a .t42 file or an empty project."""

    _launch_t42_tool_editor(input_path)


@teletext.command(name='t42crop', hidden=True)
@click.argument('input_path', required=False, type=click.Path(exists=True, dir_okay=False, readable=True))
def t42crop(input_path):

    """Backward-compatible alias for t42tool."""

    _launch_t42_tool_editor(input_path)


@teletext.command()
@click.option('-M', '--mode', type=click.Choice(['deconvolve', 'slice']), default='deconvolve', help='Deconvolution mode.')
@click.option('-8', '--eight-bit', is_flag=True, help='Treat rows 1-25 as 8-bit data without parity check.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@linequalityparam
@click.option('-C', '--force-cpu', is_flag=True, help='Disable GPU even if it is available.')
@click.option('-O', '--prefer-opencl', is_flag=True, default=False, help='Use OpenCL even if CUDA is available.')
@click.option('-t', '--threads', type=int, default=multiprocessing.cpu_count(), help='Number of threads.')
@click.option('-k', '--keep-empty', is_flag=True, help='Insert empty packets in the output when line could not be deconvolved.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@vbiformatparams
@signalcontrolparams
@fixcaptureparams
@timerparam
@previewtuneparams
@urxvtparam
@carduser(extended=True)
@packetwriter
@chunkreader()
@filterparams()
@paginated()
@progressparams(progress=True, mag_hist=True)
@click.option('--rejects/--no-rejects', default=True, help='Display percentage of lines rejected.')
@click.option('--duplicate-consensus/--no-duplicate-consensus', default=False, help='Group duplicate decoded subpages and rebuild them by consensus after deconvolution.')
@click.option('--per-line-confidence/--no-per-line-confidence', default=False, help='Use decoded per-line confidence to weight duplicate/page rebuild decisions.')
@click.option('--best-of-n-page-rebuild', type=int, default=0, show_default=True, help='When rebuilding duplicate pages, only use the best N duplicates by confidence. 0 = use all duplicates.')
def deconvolve(chunker, mags, rows, pages, subpages, paginate, config, mode, eight_bit, tape_format, line_quality, clock_lock, start_lock, adaptive_threshold, dropout_repair, wow_flutter_compensation, auto_line_align, per_line_shift, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern, force_cpu, prefer_opencl, threads, keep_empty, ignore_lines, used_lines, vbi_start, vbi_count, vbi_terminate_reset, brightness, sharpness, gain, contrast, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, fix_capture_card, timer, vbi_tune, vbi_tune_live, progress, mag_hist, row_hist, err_hist, rejects, duplicate_consensus, per_line_confidence, best_of_n_page_rebuild):

    """Deconvolve raw VBI samples into Teletext packets."""

    if keep_empty and paginate:
        raise click.UsageError("Can't keep empty packets when paginating.")
    if keep_empty and (duplicate_consensus or best_of_n_page_rebuild > 0):
        raise click.UsageError("Can't keep empty packets when rebuilding duplicate pages.")
    if vbi_tune and vbi_tune_live:
        raise click.UsageError("Use either -vtn or -vtnl, not both.")

    from teletext.vbi.line import process_lines

    if force_cpu:
        sys.stderr.write('GPU disabled by user request.\n')

    input_path = chunker_input_path(chunker)
    input_handle = chunker_input_handle(chunker)
    original_vbi_format = None
    config, original_vbi_format = apply_vbi_runtime_format(
        input_handle,
        input_path,
        config,
        vbi_start=vbi_start,
        vbi_count=vbi_count,
    )
    fix_capture_card_settings = current_fix_capture_card(fix_capture_card)
    controls = current_signal_controls(
        brightness,
        sharpness,
        gain,
        contrast,
        impulse_filter,
        temporal_denoise,
        noise_reduction,
        hum_removal,
        auto_black_level,
        head_switching_mask,
        line_to_line_stabilization,
        auto_gain_contrast,
    )
    brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, impulse_filter_coeff, temporal_denoise_coeff, noise_reduction_coeff, hum_removal_coeff, auto_black_level_coeff, head_switching_mask_coeff, line_to_line_stabilization_coeff, auto_gain_contrast_coeff = controls
    line_quality_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
    clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
    start_lock_coeff = START_LOCK_COEFF_DEFAULT
    adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
    dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
    wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT
    line_control_overrides = {}
    line_decoder_overrides = {}
    decoder_state = current_decoder_tuning(
        config,
        tape_format,
        line_quality,
        line_quality_coeff,
        clock_lock,
        clock_lock_coeff,
        start_lock,
        start_lock_coeff,
        adaptive_threshold,
        adaptive_threshold_coeff,
        dropout_repair,
        dropout_repair_coeff,
        wow_flutter_compensation,
        wow_flutter_compensation_coeff,
        auto_line_align,
        per_line_shift,
        line_control_overrides,
        line_decoder_overrides,
        show_quality,
        show_rejects,
        show_start_clock,
        show_clock_visuals,
        show_alignment_visuals,
        show_quality_meter,
        show_histogram_graph,
        show_eye_pattern,
    )
    line_quality = decoder_state['quality_threshold']
    line_quality_coeff = decoder_state['quality_threshold_coeff']
    clock_lock = decoder_state['clock_lock']
    clock_lock_coeff = decoder_state['clock_lock_coeff']
    start_lock = decoder_state['start_lock']
    start_lock_coeff = decoder_state['start_lock_coeff']
    adaptive_threshold = decoder_state['adaptive_threshold']
    adaptive_threshold_coeff = decoder_state['adaptive_threshold_coeff']
    dropout_repair = decoder_state['dropout_repair']
    dropout_repair_coeff = decoder_state['dropout_repair_coeff']
    wow_flutter_compensation = decoder_state['wow_flutter_compensation']
    wow_flutter_compensation_coeff = decoder_state['wow_flutter_compensation_coeff']
    auto_line_align = decoder_state['auto_line_align']
    per_line_shift = decoder_state['per_line_shift']
    line_control_overrides = decoder_state.get('line_control_overrides', {})
    line_decoder_overrides = decoder_state.get('line_decoder_overrides', {})
    show_quality = decoder_state['show_quality']
    show_rejects = decoder_state['show_rejects']
    show_start_clock = decoder_state['show_start_clock']
    show_clock_visuals = decoder_state['show_clock_visuals']
    show_alignment_visuals = decoder_state['show_alignment_visuals']
    show_quality_meter = decoder_state['show_quality_meter']
    show_histogram_graph = decoder_state['show_histogram_graph']
    show_eye_pattern = decoder_state['show_eye_pattern']
    pause_controller = PauseController('deconvolve')
    _, ignore_lines = resolve_frame_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
    selected_lines = current_line_selection(config, ignore_lines=ignore_lines)
    analysis_source = None
    if input_path is not None:
        analysis_source = {
            'input_path': input_path,
            'n_lines': None,
        }
    chunks = chunker(config.line_bytes, config.field_lines, config.field_range)
    if not vbi_tune_live:
        chunks = filter_ignored_chunks(chunks, config, ignore_lines)
    chunks = wrap_live_iterable(chunks, pause_controller=pause_controller, timer_seconds=timer, label='deconvolve')

    if progress:
        chunks = tqdm(chunks, unit='L', dynamic_ncols=True)
        if any((mag_hist, row_hist, rejects)):
            chunks.postfix = StatsList()

    def build_decoder_tuning_state(current_config=config, current_tape_format=tape_format):
        return current_decoder_tuning(
            current_config,
            current_tape_format,
            line_quality,
            line_quality_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
        )

    if vbi_tune:
        result = open_tuning_dialog(
            'VBI Tune - Deconvolve',
            brightness,
            sharpness,
            gain,
            contrast,
            brightness_coeff,
            sharpness_coeff,
            gain_coeff,
            contrast_coeff,
            impulse_filter,
            temporal_denoise,
            noise_reduction,
            hum_removal,
            auto_black_level,
            head_switching_mask,
            line_to_line_stabilization,
            auto_gain_contrast,
            impulse_filter_coeff,
            temporal_denoise_coeff,
            noise_reduction_coeff,
            hum_removal_coeff,
            auto_black_level_coeff,
            head_switching_mask_coeff,
            line_to_line_stabilization_coeff,
            auto_gain_contrast_coeff,
            decoder_tuning=build_decoder_tuning_state(),
            tape_formats=Config.tape_formats,
            line_selection=selected_lines,
            line_count=useful_frame_lines(config),
            fix_capture_card=fix_capture_card_settings,
            analysis_source=analysis_source,
            visible_sections=('Signal Controls', 'Signal Cleanup', 'Decoder Tuning', 'Line Selection', 'Tools', 'Args / Preset'),
        )
        if result is None:
            return
        values, decoder_tuning, selected_lines, fix_capture_card_settings = result
        brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, impulse_filter, temporal_denoise, noise_reduction, hum_removal, auto_black_level, head_switching_mask, line_to_line_stabilization, auto_gain_contrast, impulse_filter_coeff, temporal_denoise_coeff, noise_reduction_coeff, hum_removal_coeff, auto_black_level_coeff, head_switching_mask_coeff, line_to_line_stabilization_coeff, auto_gain_contrast_coeff = values
        config, tape_format, line_quality, line_quality_coeff, clock_lock, clock_lock_coeff, start_lock, start_lock_coeff, adaptive_threshold, adaptive_threshold_coeff, dropout_repair, dropout_repair_coeff, wow_flutter_compensation, wow_flutter_compensation_coeff, auto_line_align, per_line_shift, line_control_overrides, line_decoder_overrides, show_quality, show_rejects, show_start_clock, show_clock_visuals, show_alignment_visuals, show_quality_meter, show_histogram_graph, show_eye_pattern = apply_decoder_tuning(
            config,
            tape_format,
            line_quality,
            line_quality_coeff,
            clock_lock,
            clock_lock_coeff,
            start_lock,
            start_lock_coeff,
            adaptive_threshold,
            adaptive_threshold_coeff,
            dropout_repair,
            dropout_repair_coeff,
            wow_flutter_compensation,
            wow_flutter_compensation_coeff,
            auto_line_align,
            per_line_shift,
            line_control_overrides,
            line_decoder_overrides,
            show_quality,
            show_rejects,
            show_start_clock,
            show_clock_visuals,
            show_alignment_visuals,
            show_quality_meter,
            show_histogram_graph,
            show_eye_pattern,
            decoder_tuning,
        )
        ignore_lines = frozenset(range(1, useful_frame_lines(config) + 1)) - frozenset(selected_lines)
        chunks = chunker(config.line_bytes, config.field_lines, config.field_range)
        chunks = filter_ignored_chunks(chunks, config, ignore_lines)
        chunks = wrap_live_iterable(chunks, pause_controller=pause_controller, timer_seconds=timer, label='deconvolve')
        if progress:
            chunks = tqdm(chunks, unit='L', dynamic_ncols=True)
            if any((mag_hist, row_hist, rejects)):
                chunks.postfix = StatsList()

    live_tuner = None
    signal_controls = None
    fixer = None
    fixer_stop = None
    fixer_thread = None
    if vbi_tune_live:
        if threads != 1:
            sys.stderr.write('Live VBI tuning forces --threads 1.\n')
            threads = 1
        live_tuner = open_live_tuner(
            'VBI Tune Live - Deconvolve',
            brightness, sharpness, gain, contrast,
            brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff,
            impulse_filter,
            temporal_denoise,
            noise_reduction, hum_removal, auto_black_level,
            head_switching_mask, line_to_line_stabilization, auto_gain_contrast,
            impulse_filter_coeff,
            temporal_denoise_coeff,
            noise_reduction_coeff, hum_removal_coeff, auto_black_level_coeff,
            head_switching_mask_coeff, line_to_line_stabilization_coeff, auto_gain_contrast_coeff,
            config=config,
            tape_format=tape_format,
            decoder_tuning=build_decoder_tuning_state(),
            tape_formats=Config.tape_formats,
            line_selection=selected_lines,
            line_count=useful_frame_lines(config),
            fix_capture_card=fix_capture_card_settings,
            analysis_source=analysis_source,
            visible_sections=('Signal Controls', 'Signal Cleanup', 'Decoder Tuning', 'Line Selection', 'Tools', 'Args / Preset'),
        )
        signal_controls = live_tuner.values
        decoder_tuning = live_tuner.decoder_tuning
        line_selection = live_tuner.line_selection
        fixer, fixer_stop, fixer_thread = start_live_fix_capture_card(live_tuner, fix_capture_card_settings)
    else:
        decoder_tuning = None
        line_selection = None
        fixer = CaptureCardFixer()
        fixer.update(fix_capture_card_settings)

    try:
        packets = itermap(process_lines, chunks, threads,
                          mode=mode, config=config,
                          force_cpu=force_cpu, prefer_opencl=prefer_opencl,
                          mags=mags, rows=rows,
                          tape_format=tape_format,
                          brightness=brightness, sharpness=sharpness,
                          gain=gain, contrast=contrast,
                          impulse_filter=impulse_filter,
                          temporal_denoise=temporal_denoise,
                          noise_reduction=noise_reduction,
                          hum_removal=hum_removal,
                          auto_black_level=auto_black_level,
                          head_switching_mask=head_switching_mask,
                          line_stabilization=line_to_line_stabilization,
                          auto_gain_contrast=auto_gain_contrast,
                          impulse_filter_coeff=impulse_filter_coeff,
                          temporal_denoise_coeff=temporal_denoise_coeff,
                          noise_reduction_coeff=noise_reduction_coeff,
                          hum_removal_coeff=hum_removal_coeff,
                          auto_black_level_coeff=auto_black_level_coeff,
                          head_switching_mask_coeff=head_switching_mask_coeff,
                          line_stabilization_coeff=line_to_line_stabilization_coeff,
                          auto_gain_contrast_coeff=auto_gain_contrast_coeff,
                          brightness_coeff=brightness_coeff,
                          sharpness_coeff=sharpness_coeff,
                          gain_coeff=gain_coeff,
                          contrast_coeff=contrast_coeff,
                          quality_threshold=line_quality,
                          quality_threshold_coeff=line_quality_coeff,
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
                          signal_controls=signal_controls,
                          decoder_tuning=decoder_tuning,
                          line_selection=line_selection,
                          eight_bit=eight_bit)

        if progress and rejects:
            packets = Rejects(packets)
            chunks.postfix.append(packets)

        if keep_empty:
            packets = (p if isinstance(p, Packet) else Packet() for p in packets)
        else:
            packets = (p for p in packets if isinstance(p, Packet))

        if progress and mag_hist:
            packets = MagHistogram(packets)
            chunks.postfix.append(packets)
        if progress and row_hist:
            packets = RowHistogram(packets)
            chunks.postfix.append(packets)
        if progress and err_hist:
            packets = ErrorHistogram(packets)
            chunks.postfix.append(packets)

        if duplicate_consensus or best_of_n_page_rebuild > 0:
            min_duplicates = 2 if duplicate_consensus else 1
            rebuilt = pipeline.subpage_squash(
                pipeline.paginate((p for p in packets if not p.is_padding()), pages=pages, subpages=subpages, drop_empty=True),
                threshold=-1,
                min_duplicates=min_duplicates,
                ignore_empty=False,
                best_of_n=best_of_n_page_rebuild if best_of_n_page_rebuild > 0 else None,
                use_confidence=per_line_confidence,
            )
            for subpage in rebuilt:
                yield from subpage.packets
        elif paginate:
            for p in pipeline.paginate(packets, pages=pages, subpages=subpages):
                yield from p
        else:
            yield from packets
    finally:
        pause_controller.close()
        if fixer_stop is not None:
            fixer_stop.set()
        if fixer_thread is not None:
            fixer_thread.join(timeout=1)
        if fixer is not None:
            fixer.close()
        if live_tuner is not None:
            live_tuner.close()
        restore_format = original_vbi_format
        _close_handle_quietly(input_handle)
        if vbi_terminate_reset and input_path and os.path.abspath(input_path).startswith('/dev/vbi'):
            try:
                restore_format = bt8x8_default_vbi_capture_format(get_vbi_capture_format_path(input_path))
            except click.UsageError as exc:
                click.echo(f'Warning: {exc}', err=True)
                restore_format = None
        if restore_format is not None:
            try:
                restore_vbi_capture_format_path(input_path, restore_format)
            except click.UsageError as exc:
                click.echo(f'Warning: {exc}', err=True)
            except OSError:
                pass
