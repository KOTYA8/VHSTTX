import multiprocessing as mp
import time

from teletext.gui.vbituner import DEFAULT_CONTROLS
from teletext.vbi.line import Line
from teletext.vbi.viewer import VBIViewer


DEFAULT_FRAME_RATE = 25.0


class CropPlaybackStateProxy:
    CURRENT_INDEX = 0
    PLAYING_INDEX = 1

    def __init__(self, shared_values, total_frames, frame_rate=DEFAULT_FRAME_RATE):
        self._shared_values = shared_values
        self._total_frames = max(int(total_frames), 1)
        self._frame_rate = float(frame_rate)
        self._last_tick = time.monotonic()

    def next_frame_index(self):
        now = time.monotonic()
        current = max(min(int(self._shared_values[self.CURRENT_INDEX]), self._total_frames - 1), 0)
        playing = bool(int(self._shared_values[self.PLAYING_INDEX]))

        if playing:
            elapsed = now - self._last_tick
            steps = int(elapsed * self._frame_rate)
            if steps > 0:
                current = min(current + steps, self._total_frames - 1)
                self._shared_values[self.CURRENT_INDEX] = current
                self._last_tick += steps / self._frame_rate
                if current >= self._total_frames - 1:
                    self._shared_values[self.PLAYING_INDEX] = 0
        else:
            self._last_tick = now

        return current


def _line_number(frame_index, logical_index, frame_line_count):
    return (frame_index * frame_line_count) + logical_index


def _extract_frame_lines(frame, config, frame_index, n_lines=None):
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
            lines.append(Line(frame[start:end], _line_number(frame_index, logical_index, frame_line_count)))
        return lines

    frame_line_count = int(n_lines)
    lines = []
    for logical_index in range(frame_line_count):
        start = logical_index * config.line_bytes
        end = start + config.line_bytes
        if end > len(frame):
            break
        lines.append(Line(frame[start:end], _line_number(frame_index, logical_index, frame_line_count)))
    return lines


def _live_signal_controls(shared_values):
    return (
        int(shared_values[0]),
        int(shared_values[1]),
        int(shared_values[2]),
        int(shared_values[3]),
        float(shared_values[4]),
        float(shared_values[5]),
        float(shared_values[6]),
        float(shared_values[7]),
    )


def _live_decoder_tuning(shared_values, tape_formats):
    if not tape_formats:
        return None
    format_index = min(max(int(shared_values[8]), 0), len(tape_formats) - 1)
    return {
        'tape_format': tape_formats[format_index],
        'extra_roll': int(shared_values[9]),
        'line_start_range': (
            int(shared_values[10]),
            int(shared_values[11]),
        ),
    }


def _live_line_selection(shared_values, line_count):
    return frozenset(
        line for line in range(1, int(line_count) + 1)
        if int(shared_values[11 + line]) != 0
    )


def _initialise_line(
    config,
    tape_format,
    controls=DEFAULT_CONTROLS,
):
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
    )


def _crop_line_source(
    input_path,
    config,
    crop_shared_values,
    total_frames,
    frame_rate,
    n_lines=None,
):
    frame_size = config.line_bytes * config.field_lines * 2
    state = CropPlaybackStateProxy(crop_shared_values, total_frames, frame_rate=frame_rate)
    cached_frame_index = None
    cached_lines = None

    with open(input_path, 'rb') as input_file:
        while True:
            frame_index = state.next_frame_index()
            if frame_index != cached_frame_index or cached_lines is None:
                input_file.seek(frame_index * frame_size)
                frame = input_file.read(frame_size)
                cached_lines = _extract_frame_lines(frame, config, frame_index, n_lines=n_lines)
                cached_frame_index = frame_index
            for line in cached_lines:
                yield line


def _run_crop_viewer(
    input_path,
    config,
    crop_shared_values,
    total_frames,
    frame_rate,
    pause,
    tape_format,
    n_lines,
    signal_controls,
    decoder_tuning,
    tape_formats,
    line_selection,
    fixed_controls,
    fixed_decoder_tuning,
    fixed_line_selection,
):
    initial_controls = tuple(fixed_controls or DEFAULT_CONTROLS)
    if signal_controls is not None:
        initial_controls = _live_signal_controls(signal_controls)
    effective_tape_format = tape_format
    effective_config = config
    if fixed_decoder_tuning is not None:
        effective_tape_format = fixed_decoder_tuning['tape_format']
        effective_config = config.retuned(
            extra_roll=int(fixed_decoder_tuning['extra_roll']),
            line_start_range=tuple(int(value) for value in fixed_decoder_tuning['line_start_range']),
        )
    if decoder_tuning is not None:
        initial_decoder_tuning = _live_decoder_tuning(decoder_tuning, tape_formats)
        if initial_decoder_tuning is not None:
            effective_tape_format = initial_decoder_tuning['tape_format']
            effective_config = config.retuned(
                extra_roll=int(initial_decoder_tuning['extra_roll']),
                line_start_range=tuple(int(value) for value in initial_decoder_tuning['line_start_range']),
            )
    _initialise_line(effective_config, effective_tape_format, controls=initial_controls)

    lines = _crop_line_source(
        input_path=input_path,
        config=config,
        crop_shared_values=crop_shared_values,
        total_frames=total_frames,
        frame_rate=frame_rate,
        n_lines=n_lines,
    )

    if signal_controls is None:
        signal_controls_cb = None
    else:
        signal_controls_cb = lambda: _live_signal_controls(signal_controls)

    if decoder_tuning is None:
        decoder_tuning_cb = None
    else:
        decoder_tuning_cb = lambda: _live_decoder_tuning(decoder_tuning, tape_formats)

    if line_selection is None:
        if fixed_line_selection is None:
            line_selection_cb = None
        else:
            selected = frozenset(int(line) for line in fixed_line_selection)
            line_selection_cb = lambda selected=selected: selected
    else:
        line_count = len(config.field_range) * 2
        line_selection_cb = lambda: _live_line_selection(line_selection, line_count)

    VBIViewer(
        lines,
        effective_config,
        name='VBI Crop',
        pause=False,
        nlines=n_lines,
        signal_controls=signal_controls_cb,
        decoder_tuning=decoder_tuning_cb,
        tape_format=effective_tape_format,
        line_selection=line_selection_cb,
        external_playback=True,
    )


def launch_crop_viewer(
    input_path,
    config,
    crop_state,
    total_frames,
    frame_rate=DEFAULT_FRAME_RATE,
    pause=False,
    tape_format='vhs',
    n_lines=None,
    live_tuner=None,
    fixed_controls=None,
    fixed_decoder_tuning=None,
    fixed_line_selection=None,
):
    ctx = mp.get_context('spawn')
    process = ctx.Process(
        target=_run_crop_viewer,
        args=(
            input_path,
            config,
            crop_state._shared_values,
            total_frames,
            frame_rate,
            pause,
            tape_format,
            n_lines,
            None if live_tuner is None else live_tuner._shared_values,
            None if live_tuner is None else live_tuner._shared_values,
            None if live_tuner is None else list(live_tuner._tape_formats),
            None if live_tuner is None else live_tuner._shared_values,
            fixed_controls,
            fixed_decoder_tuning,
            fixed_line_selection,
        ),
    )
    process.daemon = True
    process.start()
    return process
