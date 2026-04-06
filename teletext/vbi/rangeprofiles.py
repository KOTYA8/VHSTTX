from teletext.vbi.line import normalise_per_line_shift_map


DEFAULT_LINE_COUNT = 32


def normalise_signal_controls_tuple(controls):
    controls = tuple(controls or ())
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


def normalise_line_selection(line_selection, line_count=DEFAULT_LINE_COUNT):
    if line_selection is None:
        return None
    line_count = max(int(line_count), 1)
    selected = {
        int(line)
        for line in line_selection
        if 1 <= int(line) <= line_count
    }
    return frozenset(sorted(selected))


def normalise_decoder_tuning(decoder_tuning, line_count=DEFAULT_LINE_COUNT):
    if decoder_tuning is None:
        return None
    tuning = dict(decoder_tuning)
    if 'line_start_range' in tuning:
        start, end = tuple(tuning.get('line_start_range', (0, 0)))[:2]
        start = int(start)
        end = int(end)
        if start > end:
            start, end = end, start
        tuning['line_start_range'] = (start, end)
    if 'per_line_shift' in tuning:
        tuning['per_line_shift'] = normalise_per_line_shift_map(
            tuning.get('per_line_shift', {}),
            maximum_line=line_count,
        )
    if 'line_control_overrides' in tuning:
        cleaned = {}
        for raw_line, raw_values in dict(tuning.get('line_control_overrides', {})).items():
            try:
                line = int(raw_line)
            except (TypeError, ValueError):
                continue
            if 1 <= line <= line_count:
                try:
                    cleaned[line] = tuple(normalise_signal_controls_tuple(raw_values))
                except (TypeError, ValueError):
                    continue
        tuning['line_control_overrides'] = dict(sorted(cleaned.items()))
    if 'line_decoder_overrides' in tuning:
        cleaned = {}
        for raw_line, raw_values in dict(tuning.get('line_decoder_overrides', {})).items():
            try:
                line = int(raw_line)
            except (TypeError, ValueError):
                continue
            if 1 <= line <= line_count and isinstance(raw_values, dict):
                cleaned[line] = dict(raw_values)
        tuning['line_decoder_overrides'] = dict(sorted(cleaned.items()))
    return tuning


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze(item) for item in value))
    return value


def normalise_tuning_ranges(ranges, total_frames=None, line_count=DEFAULT_LINE_COUNT):
    maximum_frame = None if total_frames is None else max(int(total_frames) - 1, 0)
    cleaned = []
    for index, entry in enumerate(tuple(ranges or ())):
        if not isinstance(entry, dict):
            continue
        try:
            start_frame = int(entry.get('start_frame', 0))
            end_frame = int(entry.get('end_frame', start_frame))
        except (TypeError, ValueError):
            continue
        if maximum_frame is not None:
            start_frame = max(min(start_frame, maximum_frame), 0)
            end_frame = max(min(end_frame, maximum_frame), 0)
        else:
            start_frame = max(start_frame, 0)
            end_frame = max(end_frame, 0)
        if start_frame > end_frame:
            start_frame, end_frame = end_frame, start_frame
        try:
            controls = tuple(normalise_signal_controls_tuple(entry.get('controls')))
        except (TypeError, ValueError):
            continue
        cleaned.append({
            'start_frame': start_frame,
            'end_frame': end_frame,
            'controls': controls,
            'line_selection': normalise_line_selection(entry.get('line_selection'), line_count=line_count),
            'decoder_tuning': normalise_decoder_tuning(entry.get('decoder_tuning'), line_count=line_count),
            'label': str(entry.get('label') or '').strip(),
            'order': int(entry.get('order', index)),
        })
    cleaned.sort(key=lambda item: (int(item['start_frame']), int(item['end_frame']), int(item.get('order', 0))))
    return tuple(cleaned)


def tuning_ranges_signature(ranges, total_frames=None, line_count=DEFAULT_LINE_COUNT):
    return _freeze(normalise_tuning_ranges(ranges, total_frames=total_frames, line_count=line_count))


def resolve_tuning_range(frame_index, base_controls, base_line_selection=None, base_decoder_tuning=None, tuning_ranges=(), line_count=DEFAULT_LINE_COUNT):
    frame_index = max(int(frame_index), 0)
    controls = tuple(normalise_signal_controls_tuple(base_controls))
    line_selection = normalise_line_selection(base_line_selection, line_count=line_count)
    decoder_tuning = normalise_decoder_tuning(base_decoder_tuning, line_count=line_count)
    active_key = None
    active_index = None
    for index, entry in enumerate(normalise_tuning_ranges(tuning_ranges, line_count=line_count)):
        if int(entry['start_frame']) <= frame_index <= int(entry['end_frame']):
            controls = tuple(entry['controls'])
            line_selection = entry['line_selection']
            decoder_tuning = entry['decoder_tuning']
            active_key = (
                int(entry['start_frame']),
                int(entry['end_frame']),
                int(entry.get('order', index)),
            )
            active_index = index
    return controls, line_selection, decoder_tuning, active_key, active_index


def format_tuning_range_label(entry, index=None):
    if not isinstance(entry, dict):
        return ''
    prefix = f'#{int(index) + 1} ' if index is not None else ''
    label = str(entry.get('label') or '').strip()
    if label:
        prefix += label + ' '
    return (
        f"{prefix}{int(entry.get('start_frame', 0))}..{int(entry.get('end_frame', 0))}"
    ).strip()
