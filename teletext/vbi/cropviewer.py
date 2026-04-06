import multiprocessing as mp
import time

from teletext.gui.vbituner import (
    DECODER_TUNING_SLOT_COUNT,
    DEFAULT_CONTROLS,
    LINE_OVERRIDE_DECODER_SLOT_COUNT,
    LINE_OVERRIDE_SIGNAL_SLOT_COUNT,
    SIGNAL_CONTROL_COUNT,
    _deserialise_line_control_override_slots,
    _deserialise_line_decoder_override_slots,
    _fix_capture_card_offset,
    _line_control_override_offset,
    _line_selection_offset,
)
from teletext.vbi.line import (
    ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
    AUTO_LINE_ALIGN_DEFAULT,
    CLOCK_LOCK_COEFF_DEFAULT,
    CLOCK_LOCK_DEFAULT,
    DROPOUT_REPAIR_COEFF_DEFAULT,
    DROPOUT_REPAIR_DEFAULT,
    Line,
    QUALITY_THRESHOLD_COEFF_DEFAULT,
    START_LOCK_COEFF_DEFAULT,
    START_LOCK_DEFAULT,
    WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
    WOW_FLUTTER_COMPENSATION_DEFAULT,
    normalise_per_line_shift_map,
)
from teletext.vbi.viewer import VBIViewer


DEFAULT_FRAME_RATE = 25.0


class CropPlaybackStateProxy:
    CURRENT_INDEX = 0
    PLAYING_INDEX = 1
    SPEED_TENTHS_INDEX = 4
    DIRECTION_INDEX = 5

    def __init__(self, shared_values, total_frames, frame_rate=DEFAULT_FRAME_RATE):
        self._shared_values = shared_values
        self._total_frames = max(int(total_frames), 1)
        self._frame_rate = float(frame_rate)
        self._last_tick = time.monotonic()

    def next_frame_index(self):
        now = time.monotonic()
        current = max(min(int(self._shared_values[self.CURRENT_INDEX]), self._total_frames - 1), 0)
        playing = bool(int(self._shared_values[self.PLAYING_INDEX]))
        speed = max(int(self._shared_values[self.SPEED_TENTHS_INDEX]), 1) / 10.0
        direction = -1 if int(self._shared_values[self.DIRECTION_INDEX]) < 0 else 1

        if playing:
            elapsed = now - self._last_tick
            steps = int(elapsed * self._frame_rate * speed)
            if steps > 0:
                if direction < 0:
                    current = max(current - steps, 0)
                else:
                    current = min(current + steps, self._total_frames - 1)
                self._shared_values[self.CURRENT_INDEX] = current
                self._last_tick += steps / (self._frame_rate * speed)
                if current <= 0 or current >= self._total_frames - 1:
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
        int(shared_values[8]),
        int(shared_values[9]),
        int(shared_values[10]),
        int(shared_values[11]),
        int(shared_values[12]),
        int(shared_values[13]),
        int(shared_values[14]),
        int(shared_values[15]),
        float(shared_values[16]),
        float(shared_values[17]),
        float(shared_values[18]),
        float(shared_values[19]),
        float(shared_values[20]),
        float(shared_values[21]),
        float(shared_values[22]),
        float(shared_values[23]),
    )


def _live_decoder_tuning(shared_values, tape_formats):
    if not tape_formats:
        return None
    format_index = min(max(int(shared_values[SIGNAL_CONTROL_COUNT]), 0), len(tape_formats) - 1)
    line_count = 32
    line_control_offset = _line_control_override_offset(line_count)
    line_decoder_offset = line_control_offset + LINE_OVERRIDE_SIGNAL_SLOT_COUNT
    return {
        'tape_format': tape_formats[format_index],
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
            line: float(shared_values[SIGNAL_CONTROL_COUNT + 25 + line])
            for line in range(1, 33)
            if abs(float(shared_values[SIGNAL_CONTROL_COUNT + 25 + line])) > 1e-9
        },
        'line_control_overrides': _deserialise_line_control_override_slots(
            shared_values[line_control_offset:line_control_offset + LINE_OVERRIDE_SIGNAL_SLOT_COUNT],
            line_count=line_count,
        ),
        'line_decoder_overrides': _deserialise_line_decoder_override_slots(
            shared_values[line_decoder_offset:line_decoder_offset + LINE_OVERRIDE_DECODER_SLOT_COUNT],
            line_count=line_count,
        ),
    }


def _live_line_selection(shared_values, line_count):
    line_offset = _line_selection_offset()
    return frozenset(
        line for line in range(1, int(line_count) + 1)
        if int(shared_values[line_offset + line - 1]) != 0
    )


def _initialise_line(
    config,
    tape_format,
    controls=DEFAULT_CONTROLS,
    quality_threshold=50,
    quality_threshold_coeff=1.0,
    clock_lock=CLOCK_LOCK_DEFAULT,
    clock_lock_coeff=CLOCK_LOCK_COEFF_DEFAULT,
    start_lock=START_LOCK_DEFAULT,
    start_lock_coeff=START_LOCK_COEFF_DEFAULT,
    adaptive_threshold=0,
    adaptive_threshold_coeff=ADAPTIVE_THRESHOLD_COEFF_DEFAULT,
    dropout_repair=DROPOUT_REPAIR_DEFAULT,
    dropout_repair_coeff=DROPOUT_REPAIR_COEFF_DEFAULT,
    wow_flutter_compensation=WOW_FLUTTER_COMPENSATION_DEFAULT,
    wow_flutter_compensation_coeff=WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT,
    auto_line_align=AUTO_LINE_ALIGN_DEFAULT,
    per_line_shift=None,
    line_control_overrides=None,
    line_decoder_overrides=None,
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
        quality_threshold=int(quality_threshold),
        quality_threshold_coeff=float(quality_threshold_coeff),
        clock_lock=int(clock_lock),
        clock_lock_coeff=float(clock_lock_coeff),
        start_lock=int(start_lock),
        start_lock_coeff=float(start_lock_coeff),
        adaptive_threshold=int(adaptive_threshold),
        adaptive_threshold_coeff=float(adaptive_threshold_coeff),
        dropout_repair=int(dropout_repair),
        dropout_repair_coeff=float(dropout_repair_coeff),
        wow_flutter_compensation=int(wow_flutter_compensation),
        wow_flutter_compensation_coeff=float(wow_flutter_compensation_coeff),
        auto_line_align=int(auto_line_align),
        per_line_shift=per_line_shift,
        line_control_overrides=line_control_overrides,
        line_decoder_overrides=line_decoder_overrides,
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
    window_name='VBI Crop',
):
    initial_controls = tuple(fixed_controls or DEFAULT_CONTROLS)
    if signal_controls is not None:
        initial_controls = _live_signal_controls(signal_controls)
    effective_tape_format = tape_format
    effective_config = config
    effective_quality_threshold = 50
    effective_quality_threshold_coeff = 1.0
    effective_clock_lock = CLOCK_LOCK_DEFAULT
    effective_clock_lock_coeff = CLOCK_LOCK_COEFF_DEFAULT
    effective_start_lock = START_LOCK_DEFAULT
    effective_start_lock_coeff = START_LOCK_COEFF_DEFAULT
    effective_adaptive_threshold = 0
    effective_adaptive_threshold_coeff = ADAPTIVE_THRESHOLD_COEFF_DEFAULT
    effective_dropout_repair = DROPOUT_REPAIR_DEFAULT
    effective_dropout_repair_coeff = DROPOUT_REPAIR_COEFF_DEFAULT
    effective_wow_flutter_compensation = WOW_FLUTTER_COMPENSATION_DEFAULT
    effective_wow_flutter_compensation_coeff = WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT
    effective_auto_line_align = AUTO_LINE_ALIGN_DEFAULT
    effective_per_line_shift = {}
    effective_line_control_overrides = {}
    effective_line_decoder_overrides = {}
    if fixed_decoder_tuning is not None:
        effective_tape_format = fixed_decoder_tuning['tape_format']
        effective_config = config.retuned(
            extra_roll=int(fixed_decoder_tuning['extra_roll']),
            line_start_range=tuple(int(value) for value in fixed_decoder_tuning['line_start_range']),
        )
        effective_quality_threshold = int(fixed_decoder_tuning.get('quality_threshold', 50))
        effective_quality_threshold_coeff = float(fixed_decoder_tuning.get('quality_threshold_coeff', 1.0))
        effective_clock_lock = int(fixed_decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))
        effective_clock_lock_coeff = float(fixed_decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT))
        effective_start_lock = int(fixed_decoder_tuning.get('start_lock', START_LOCK_DEFAULT))
        effective_start_lock_coeff = float(fixed_decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT))
        effective_adaptive_threshold = int(fixed_decoder_tuning.get('adaptive_threshold', 0))
        effective_adaptive_threshold_coeff = float(fixed_decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT))
        effective_dropout_repair = int(fixed_decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
        effective_dropout_repair_coeff = float(fixed_decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT))
        effective_wow_flutter_compensation = int(fixed_decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
        effective_wow_flutter_compensation_coeff = float(fixed_decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT))
        effective_auto_line_align = int(fixed_decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))
        effective_per_line_shift = normalise_per_line_shift_map(
            fixed_decoder_tuning.get('per_line_shift', {}),
            maximum_line=max(int(n_lines or 32), 32),
        )
        effective_line_control_overrides = dict(fixed_decoder_tuning.get('line_control_overrides', {}))
        effective_line_decoder_overrides = dict(fixed_decoder_tuning.get('line_decoder_overrides', {}))
    if decoder_tuning is not None:
        initial_decoder_tuning = _live_decoder_tuning(decoder_tuning, tape_formats)
        if initial_decoder_tuning is not None:
            effective_tape_format = initial_decoder_tuning['tape_format']
            effective_config = config.retuned(
                extra_roll=int(initial_decoder_tuning['extra_roll']),
                line_start_range=tuple(int(value) for value in initial_decoder_tuning['line_start_range']),
            )
            effective_quality_threshold = int(initial_decoder_tuning.get('quality_threshold', 50))
            effective_quality_threshold_coeff = float(initial_decoder_tuning.get('quality_threshold_coeff', 1.0))
            effective_clock_lock = int(initial_decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT))
            effective_clock_lock_coeff = float(initial_decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT))
            effective_start_lock = int(initial_decoder_tuning.get('start_lock', START_LOCK_DEFAULT))
            effective_start_lock_coeff = float(initial_decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT))
            effective_adaptive_threshold = int(initial_decoder_tuning.get('adaptive_threshold', 0))
            effective_adaptive_threshold_coeff = float(initial_decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT))
            effective_dropout_repair = int(initial_decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT))
            effective_dropout_repair_coeff = float(initial_decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT))
            effective_wow_flutter_compensation = int(initial_decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT))
            effective_wow_flutter_compensation_coeff = float(initial_decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT))
            effective_auto_line_align = int(initial_decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT))
            effective_per_line_shift = normalise_per_line_shift_map(
                initial_decoder_tuning.get('per_line_shift', {}),
                maximum_line=max(int(n_lines or 32), 32),
            )
            effective_line_control_overrides = dict(initial_decoder_tuning.get('line_control_overrides', {}))
            effective_line_decoder_overrides = dict(initial_decoder_tuning.get('line_decoder_overrides', {}))
    _initialise_line(
        effective_config,
        effective_tape_format,
        controls=initial_controls,
        quality_threshold=effective_quality_threshold,
        quality_threshold_coeff=effective_quality_threshold_coeff,
        clock_lock=effective_clock_lock,
        clock_lock_coeff=effective_clock_lock_coeff,
        start_lock=effective_start_lock,
        start_lock_coeff=effective_start_lock_coeff,
        adaptive_threshold=effective_adaptive_threshold,
        adaptive_threshold_coeff=effective_adaptive_threshold_coeff,
        dropout_repair=effective_dropout_repair,
        dropout_repair_coeff=effective_dropout_repair_coeff,
        wow_flutter_compensation=effective_wow_flutter_compensation,
        wow_flutter_compensation_coeff=effective_wow_flutter_compensation_coeff,
        auto_line_align=effective_auto_line_align,
        per_line_shift=effective_per_line_shift,
        line_control_overrides=effective_line_control_overrides,
        line_decoder_overrides=effective_line_decoder_overrides,
    )

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
        decoder_tuning_cb = fixed_decoder_tuning
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
        name=window_name,
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
    window_name='VBI Crop',
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
            window_name,
        ),
    )
    process.daemon = True
    process.start()
    return process
