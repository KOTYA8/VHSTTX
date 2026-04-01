import itertools
import multiprocessing
import os
import pathlib
import platform
import shutil
import subprocess
import threading

import sys
from collections import defaultdict

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


if os.name == 'nt' and platform.release() == '10' and platform.version() >= '10.0.14393':
    # Fix ANSI color in Windows 10 version 10.0.14393 (Windows Anniversary Update)
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)


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


def signalcontrolparams(f):
    options = [
        click.option('-bn', '--brightness', type=click.IntRange(0, 100), default=50, show_default=True,
                     help='Software brightness control for VBI samples. 50 = unchanged.'),
        click.option('-sp', '--sharpness', type=click.IntRange(0, 100), default=50, show_default=True,
                     help='Software sharpness control for VBI samples. 50 = unchanged.'),
        click.option('-gn', '--gain', type=click.IntRange(0, 100), default=50, show_default=True,
                     help='Software gain control for VBI samples. 50 = unchanged.'),
        click.option('-ct', '--contrast', type=click.IntRange(0, 100), default=50, show_default=True,
                     help='Software contrast control for VBI samples. 50 = unchanged.'),
        click.option('-bncf', '--brightness-coeff', type=click.FloatRange(0.0), default=48.0, show_default=True,
                     help='Brightness response coefficient.'),
        click.option('-spcf', '--sharpness-coeff', type=click.FloatRange(0.0), default=3.0, show_default=True,
                     help='Sharpness response coefficient.'),
        click.option('-gncf', '--gain-coeff', type=click.FloatRange(0.0), default=0.5, show_default=True,
                     help='Gain response coefficient.'),
        click.option('-ctcf', '--contrast-coeff', type=click.FloatRange(0.0), default=0.5, show_default=True,
                     help='Contrast response coefficient.'),
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
    preview_provider=None,
    config=None,
    tape_format='vhs',
    live=False,
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=None,
    fix_capture_card=None,
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
    decoder_tuning=None,
    tape_formats=None,
    line_selection=None,
    line_count=32,
    fix_capture_card=None,
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
        ),
        decoder_tuning=decoder_tuning,
        tape_formats=tape_formats,
        line_selection=line_selection,
        line_count=line_count,
        fix_capture_card=fix_capture_card,
    )


def current_decoder_tuning(config, tape_format):
    return {
        'tape_format': tape_format,
        'extra_roll': int(config.extra_roll),
        'line_start_range': tuple(int(value) for value in config.line_start_range),
    }


def apply_decoder_tuning(config, tape_format, decoder_tuning):
    if decoder_tuning is None:
        return config, tape_format
    updated_config = config.retuned(
        extra_roll=int(decoder_tuning['extra_roll']),
        line_start_range=tuple(int(value) for value in decoder_tuning['line_start_range']),
    )
    return updated_config, decoder_tuning['tape_format']


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
    brightness_coeff,
    sharpness_coeff,
    gain_coeff,
    contrast_coeff,
):
    return (
        int(brightness),
        int(sharpness),
        int(gain),
        int(contrast),
        float(brightness_coeff),
        float(sharpness_coeff),
        float(gain_coeff),
        float(contrast_coeff),
    )


def frame_size_for_config(config):
    return config.line_bytes * config.field_lines * 2


def estimate_vbi_size_megabytes(frame_count, config):
    return (max(int(frame_count), 0) * frame_size_for_config(config)) / (1024 * 1024)


def count_complete_frames(input_path, config):
    frame_size = frame_size_for_config(config)
    file_size = os.path.getsize(input_path)
    return file_size // frame_size


def _processed_frame_for_output(frame, config, controls, line_selection=None):
    from teletext.vbi.line import process_frame_bytes

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
        preserve_tail=preserve_tail,
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
):
    frame_size = frame_size_for_config(config)
    start_frame = max(int(start_frame), 0)
    end_frame = max(int(end_frame), start_frame)

    with open(input_path, 'rb') as source, open(output_path, 'wb') as output:
        source.seek(start_frame * frame_size)
        for _ in range(start_frame, end_frame + 1):
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection))


def delete_cropped_vbi(
    input_path,
    output_path,
    config,
    start_frame,
    end_frame,
    controls,
    line_selection=None,
):
    frame_size = frame_size_for_config(config)
    start_frame = max(int(start_frame), 0)
    end_frame = max(int(end_frame), start_frame)

    with open(input_path, 'rb') as source, open(output_path, 'wb') as output:
        frame_index = 0
        while True:
            frame = source.read(frame_size)
            if len(frame) < frame_size:
                break
            if start_frame <= frame_index <= end_frame:
                frame_index += 1
                continue
            output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection))
            frame_index += 1


def save_edited_vbi(
    input_path,
    output_path,
    config,
    controls,
    line_selection=None,
    cut_ranges=(),
    insertions=(),
):
    frame_size = frame_size_for_config(config)
    cut_ranges = tuple(sorted(cut_ranges))
    insertions = tuple(sorted(insertions, key=lambda item: (int(item['after_frame']), item['path'])))
    cut_index = 0
    insertion_index = 0

    def write_insertions(output, after_frame):
        nonlocal insertion_index
        while insertion_index < len(insertions) and int(insertions[insertion_index]['after_frame']) == after_frame:
            insertion = insertions[insertion_index]
            with open(insertion['path'], 'rb') as insert_file:
                while True:
                    frame = insert_file.read(frame_size)
                    if len(frame) < frame_size:
                        break
                    output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection))
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
            output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection))
            write_insertions(output, frame_index)
            frame_index += 1

        while insertion_index < len(insertions):
            insertion = insertions[insertion_index]
            with open(insertion['path'], 'rb') as insert_file:
                while True:
                    frame = insert_file.read(frame_size)
                    if len(frame) < frame_size:
                        break
                    output.write(_processed_frame_for_output(frame, config, controls, line_selection=line_selection))
            insertion_index += 1


def start_live_fix_capture_card(live_tuner, initial_settings):
    fixer = CaptureCardFixer()
    fixer.update(initial_settings)
    stop_event = threading.Event()

    def poll():
        last_settings = None
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
@packetwriter
@paginated(always=True)
@packetreader()
def squash(packets, min_duplicates, threshold, pages, subpages, ignore_empty):

    """Reduce errors in t42 stream by using frequency analysis."""

    packets = (p for p in packets if not p.is_padding())
    for sp in pipeline.subpage_squash(
            pipeline.paginate(packets, pages=pages, subpages=subpages),
            min_duplicates=min_duplicates, ignore_empty=ignore_empty,
            threshold=threshold
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


@teletext.command()
@click.argument('output', type=click.File('wb'), default='-')
@click.option('-d', '--device', type=click.File('rb'), default='/dev/vbi0', help='Capture device.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@fixcaptureparams
@click.option('-vtn', '--vbi-tune', is_flag=True, help='Open the VBI tuning window before starting capture.')
@carduser()
def record(output, device, ignore_lines, used_lines, brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, fix_capture_card, vbi_tune, config):

    """Record VBI samples from a capture device."""

    import struct
    import sys
    from teletext.vbi.line import process_frame_bytes

    if output.name.startswith('/dev/vbi'):
        raise click.UsageError(f'Refusing to write output to VBI device. Did you mean -d?')

    _, ignore_lines = resolve_frame_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
    ignored_ranges = ignored_frame_line_byte_ranges(config, ignore_lines)
    preserve_tail = 4 if config.card == 'bt8x8' else 0
    selected_lines = current_line_selection(config, ignore_lines=ignore_lines)
    fix_capture_card_settings = current_fix_capture_card(fix_capture_card)

    if vbi_tune:
        result = open_tuning_dialog(
            'VBI Tune - Record',
            brightness,
            sharpness,
            gain,
            contrast,
            brightness_coeff,
            sharpness_coeff,
            gain_coeff,
            contrast_coeff,
            config=config,
            line_selection=selected_lines,
            fix_capture_card=fix_capture_card_settings,
        )
        if result is None:
            return
        values, _, selected_lines, fix_capture_card_settings = result
        brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff = values
        ignore_lines = frozenset(range(1, useful_frame_lines(config) + 1)) - frozenset(selected_lines)
        ignored_ranges = ignored_frame_line_byte_ranges(config, ignore_lines)
        try:
            device.seek(0)
        except (AttributeError, OSError):
            pass

    chunks = FileChunker(device, config.line_bytes*config.field_lines*2)
    pause_controller = PauseController('record')
    bar = tqdm(pause_controller.wrap_iterable(chunks), unit=' Frames')
    fixer = CaptureCardFixer()
    fixer.update(fix_capture_card_settings)

    prev_seq = None
    dropped = 0

    try:
        for n, chunk in bar:
            chunk = process_frame_bytes(
                chunk,
                config,
                brightness=brightness,
                sharpness=sharpness,
                gain=gain,
                contrast=contrast,
                brightness_coeff=brightness_coeff,
                sharpness_coeff=sharpness_coeff,
                gain_coeff=gain_coeff,
                contrast_coeff=contrast_coeff,
                preserve_tail=preserve_tail,
            )
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
        pause_controller.close()
        fixer.close()


@teletext.command()
@click.option('-p', '--pause', is_flag=True, help='Start the viewer paused.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@click.option('-n', '--n-lines', type=int, default=None, help='Number of lines to display. Overrides card config.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@fixcaptureparams
@click.option('-vtnl', '--vbi-tune-live', is_flag=True, help='Open the VBI tuning window with live preview instead of the OpenGL viewer.')
@carduser(extended=True)
@chunkreader()
def vbiview(chunker, config, pause, tape_format, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, fix_capture_card, vbi_tune_live):

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
        fix_capture_card_settings = current_fix_capture_card(fix_capture_card)
        fixer = None
        fixer_stop = None
        fixer_thread = None
        if vbi_tune_live:
            live_tuner = open_live_tuner(
                'VBI Tune Live',
                brightness, sharpness, gain, contrast,
                brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff,
                decoder_tuning=current_decoder_tuning(config, tape_format),
                tape_formats=Config.tape_formats,
                line_selection=selected_lines,
                line_count=useful_frame_lines(config),
                fix_capture_card=fix_capture_card_settings,
            )
            signal_controls = live_tuner.values
            decoder_tuning = live_tuner.decoder_tuning
            line_selection = live_tuner.line_selection
            fixer, fixer_stop, fixer_thread = start_live_fix_capture_card(live_tuner, fix_capture_card_settings)
        else:
            decoder_tuning = None
            fixer = CaptureCardFixer()
            fixer.update(fix_capture_card_settings)

        try:
            VBIViewer(lines, config, pause=pause, nlines=n_lines, signal_controls=signal_controls, decoder_tuning=decoder_tuning, tape_format=tape_format, line_selection=line_selection)
        finally:
            if fixer_stop is not None:
                fixer_stop.set()
            if fixer_thread is not None:
                fixer_thread.join(timeout=1)
            if fixer is not None:
                fixer.close()
            if live_tuner is not None:
                live_tuner.close()


@teletext.command()
@click.argument('input_path', type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option('-p', '--pause/--play', default=True, help='Start playback paused.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@click.option('-n', '--n-lines', type=int, default=None, help='Number of lines to display. Overrides card config.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@fixcaptureparams
@previewtuneparams
@carduser(extended=True)
def vbicrop(input_path, config, pause, tape_format, n_lines, ignore_lines, used_lines, brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, fix_capture_card, vbi_tune, vbi_tune_live):

    """Open the VBI crop editor for a recorded .vbi file."""

    if vbi_tune and vbi_tune_live:
        raise click.UsageError("Use either -vtn or -vtnl, not both.")

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
        brightness_coeff,
        sharpness_coeff,
        gain_coeff,
        contrast_coeff,
    )
    fix_capture_card_settings = current_fix_capture_card(fix_capture_card)
    state = {
        'config': config,
        'tape_format': tape_format,
        'controls': controls,
        'selected_lines': frozenset(selected_lines),
        'fix_capture_card': fix_capture_card_settings,
    }

    if vbi_tune:
        result = open_tuning_dialog(
            'VBI Tune - Crop',
            *state['controls'],
            decoder_tuning=current_decoder_tuning(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            fix_capture_card=state['fix_capture_card'],
        )
        if result is None:
            return
        state['controls'], decoder_tuning, state['selected_lines'], state['fix_capture_card'] = result
        state['config'], state['tape_format'] = apply_decoder_tuning(state['config'], state['tape_format'], decoder_tuning)

    crop_state = create_crop_state(total_frames=total_frames, current_frame=0, playing=not pause)

    live_tuner = None
    fixer = None
    fixer_stop = None
    fixer_thread = None
    viewer_process = None
    if vbi_tune_live:
        live_tuner = open_live_tuner(
            'VBI Tune Live - Crop',
            *state['controls'],
            decoder_tuning=current_decoder_tuning(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            fix_capture_card=state['fix_capture_card'],
        )
        fixer, fixer_stop, fixer_thread = start_live_fix_capture_card(live_tuner, state['fix_capture_card'])
    else:
        fixer = CaptureCardFixer()
        fixer.update(state['fix_capture_card'])

    def sync_state_from_live_tuner():
        if live_tuner is None:
            return
        state['controls'] = live_tuner.values()
        decoder_tuning = live_tuner.decoder_tuning()
        if decoder_tuning is not None:
            state['config'], state['tape_format'] = apply_decoder_tuning(state['config'], state['tape_format'], decoder_tuning)
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
            fixed_decoder_tuning=current_decoder_tuning(state['config'], state['tape_format']),
            fixed_line_selection=state['selected_lines'],
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
        live_tuner = open_live_tuner(
            'VBI Tune Live - Crop',
            *state['controls'],
            decoder_tuning=current_decoder_tuning(state['config'], state['tape_format']),
            tape_formats=Config.tape_formats,
            line_selection=state['selected_lines'],
            line_count=useful_frame_lines(state['config']),
            fix_capture_card=state['fix_capture_card'],
        )
        fixer, fixer_stop, fixer_thread = start_live_fix_capture_card(live_tuner, state['fix_capture_card'])
        restart_viewer()

    def save_callback(output_path, cut_ranges, insertions):
        current_controls_value = state['controls'] if live_tuner is None else live_tuner.values()
        current_line_selection_value = state['selected_lines'] if live_tuner is None else live_tuner.line_selection()
        save_edited_vbi(
            input_path=input_path,
            output_path=output_path,
            config=state['config'],
            controls=current_controls_value,
            line_selection=current_line_selection_value,
            cut_ranges=cut_ranges,
            insertions=insertions,
        )

    try:
        run_crop_window(
            state=crop_state,
            total_frames=total_frames,
            frame_rate=DEFAULT_FRAME_RATE,
            save_callback=save_callback,
            live_tune_callback=open_live_tune_callback,
            viewer_process=lambda: viewer_process,
            frame_size_bytes=frame_size_for_config(state['config']),
        )
    finally:
        if viewer_process is not None and viewer_process.is_alive():
            viewer_process.terminate()
            viewer_process.join(timeout=1)
        stop_fix_capture_card_runtime()
        if live_tuner is not None:
            live_tuner.close()


@teletext.command()
@click.option('-M', '--mode', type=click.Choice(['deconvolve', 'slice']), default='deconvolve', help='Deconvolution mode.')
@click.option('-8', '--eight-bit', is_flag=True, help='Treat rows 1-25 as 8-bit data without parity check.')
@click.option('-f', '--tape-format', type=click.Choice(Config.tape_formats), default='vhs', help='Source VCR format.')
@click.option('-C', '--force-cpu', is_flag=True, help='Disable GPU even if it is available.')
@click.option('-O', '--prefer-opencl', is_flag=True, default=False, help='Use OpenCL even if CUDA is available.')
@click.option('-t', '--threads', type=int, default=multiprocessing.cpu_count(), help='Number of threads.')
@click.option('-k', '--keep-empty', is_flag=True, help='Insert empty packets in the output when line could not be deconvolved.')
@click.option('-il', '--ignore-line', 'ignore_lines', multiple=True, callback=parse_ignore_lines,
              help='Ignore 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 23,24,25.')
@click.option('-ul', '--used-line', 'used_lines', multiple=True, callback=parse_used_lines,
              help='Use only 1-based VBI lines within each frame. Accepts comma-separated values, e.g. 4,5.')
@signalcontrolparams
@fixcaptureparams
@previewtuneparams
@urxvtparam
@carduser(extended=True)
@packetwriter
@chunkreader()
@filterparams()
@paginated()
@progressparams(progress=True, mag_hist=True)
@click.option('--rejects/--no-rejects', default=True, help='Display percentage of lines rejected.')
def deconvolve(chunker, mags, rows, pages, subpages, paginate, config, mode, eight_bit, force_cpu, prefer_opencl, threads, keep_empty, ignore_lines, used_lines, brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff, fix_capture_card, vbi_tune, vbi_tune_live, progress, mag_hist, row_hist, err_hist, rejects, tape_format):

    """Deconvolve raw VBI samples into Teletext packets."""

    if keep_empty and paginate:
        raise click.UsageError("Can't keep empty packets when paginating.")
    if vbi_tune and vbi_tune_live:
        raise click.UsageError("Use either -vtn or -vtnl, not both.")

    from teletext.vbi.line import process_lines

    if force_cpu:
        sys.stderr.write('GPU disabled by user request.\n')

    fix_capture_card_settings = current_fix_capture_card(fix_capture_card)
    pause_controller = PauseController('deconvolve')
    _, ignore_lines = resolve_frame_line_selection(config, ignore_lines=ignore_lines, used_lines=used_lines)
    selected_lines = current_line_selection(config, ignore_lines=ignore_lines)
    chunks = chunker(config.line_bytes, config.field_lines, config.field_range)
    if not vbi_tune_live:
        chunks = filter_ignored_chunks(chunks, config, ignore_lines)
    chunks = pause_controller.wrap_iterable(chunks)

    if progress:
        chunks = tqdm(chunks, unit='L', dynamic_ncols=True)
        if any((mag_hist, row_hist, rejects)):
            chunks.postfix = StatsList()

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
            decoder_tuning=current_decoder_tuning(config, tape_format),
            tape_formats=Config.tape_formats,
            line_selection=selected_lines,
            line_count=useful_frame_lines(config),
            fix_capture_card=fix_capture_card_settings,
        )
        if result is None:
            return
        values, decoder_tuning, selected_lines, fix_capture_card_settings = result
        brightness, sharpness, gain, contrast, brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff = values
        config, tape_format = apply_decoder_tuning(config, tape_format, decoder_tuning)
        ignore_lines = frozenset(range(1, useful_frame_lines(config) + 1)) - frozenset(selected_lines)
        chunks = chunker(config.line_bytes, config.field_lines, config.field_range)
        chunks = filter_ignored_chunks(chunks, config, ignore_lines)
        chunks = pause_controller.wrap_iterable(chunks)
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
            decoder_tuning=current_decoder_tuning(config, tape_format),
            tape_formats=Config.tape_formats,
            line_selection=selected_lines,
            line_count=useful_frame_lines(config),
            fix_capture_card=fix_capture_card_settings,
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
                          brightness_coeff=brightness_coeff,
                          sharpness_coeff=sharpness_coeff,
                          gain_coeff=gain_coeff,
                          contrast_coeff=contrast_coeff,
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

        if paginate:
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
