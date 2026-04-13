from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

import numpy as np


PAL_VITC_LINE_RATE = 15625.0
PAL_VITC_BITS = 90
PAL_VITC_SYNC_POSITIONS = tuple(range(0, 82, 10))
PAL_VITC_REQUIRED_SYNC_MATCHES = len(PAL_VITC_SYNC_POSITIONS)
PAL_VITC_SEARCH_WIDTH = 41
PAL_VITC_SEARCH_STARTS = 81
PAL_VITC_START_MARGIN = (0.30, 0.65)
PAL_VITC_ABSOLUTE_LINES = (19, 21, 332, 334)


@dataclass(frozen=True)
class VITCDecode:
    line_number: int | None
    timecode: str
    hours: int
    minutes: int
    seconds: int
    frames: int
    field_mark: int
    color_frame: int
    drop_frame: int
    bgf0: int
    bgf1: int
    bgf2: int
    user_bits: tuple[int, ...]
    user_bits_hex: str
    crc_ok: bool
    sync_matches: int
    start_offset: float
    bit_width: float
    inverted: bool
    threshold: float


def _bit_indices(line_size):
    return np.arange(line_size, dtype=float)


def _vitc_search_bounds(config):
    nominal_width = config.sample_rate / (PAL_VITC_LINE_RATE * 115.0)
    spare = max(float(config.line_length) - (nominal_width * PAL_VITC_BITS), 0.0)
    start_min = spare * PAL_VITC_START_MARGIN[0]
    start_max = spare * PAL_VITC_START_MARGIN[1]
    if start_max <= start_min:
        start_max = start_min + max(nominal_width * 2.0, 8.0)
    return nominal_width, start_min, start_max


def _sample_candidate_bits(samples, sample_positions, start_offset, bit_width, inverted):
    centers = start_offset + ((np.arange(PAL_VITC_BITS, dtype=float) + 0.5) * bit_width)
    if centers[-1] >= samples.size:
        return None

    values = np.interp(centers, sample_positions, samples)
    low = float(np.percentile(values, 20))
    high = float(np.percentile(values, 80))
    threshold = (low + high) / 2.0
    bits = (values > threshold).astype(np.uint8)
    if inverted:
        bits = 1 - bits
    return bits, threshold


def _sync_matches(bits):
    return sum(int(bits[position] == 1 and bits[position + 1] == 0) for position in PAL_VITC_SYNC_POSITIONS)


def _bcd_from_bits(bits, positions):
    return sum((int(bits[position]) << shift) for shift, position in enumerate(positions))


def _crc_remainder(bits):
    data = [int(bit) for bit in bits]
    polynomial = [1, 0, 0, 0, 0, 0, 0, 0, 1]
    for bit_index in range(len(data) - 8):
        if data[bit_index]:
            for polynomial_index in range(9):
                data[bit_index + polynomial_index] ^= polynomial[polynomial_index]
    return tuple(data[-8:])


def _decode_from_bits(bits, line_number, start_offset, bit_width, inverted, threshold):
    frames_units = _bcd_from_bits(bits, (2, 3, 4, 5))
    frames_tens = _bcd_from_bits(bits, (12, 13))
    seconds_units = _bcd_from_bits(bits, (22, 23, 24, 25))
    seconds_tens = _bcd_from_bits(bits, (32, 33, 34))
    minutes_units = _bcd_from_bits(bits, (42, 43, 44, 45))
    minutes_tens = _bcd_from_bits(bits, (52, 53, 54))
    hours_units = _bcd_from_bits(bits, (62, 63, 64, 65))
    hours_tens = _bcd_from_bits(bits, (72, 73))

    if frames_units >= 10 or frames_tens >= 3:
        return None
    if seconds_units >= 10 or seconds_tens >= 6:
        return None
    if minutes_units >= 10 or minutes_tens >= 6:
        return None
    if hours_units >= 10 or hours_tens >= 3:
        return None

    frames = (frames_tens * 10) + frames_units
    seconds = (seconds_tens * 10) + seconds_units
    minutes = (minutes_tens * 10) + minutes_units
    hours = (hours_tens * 10) + hours_units

    user_bit_groups = tuple(
        _bcd_from_bits(bits, range(start, start + 4))
        for start in (6, 16, 26, 36, 46, 56, 66, 76)
    )
    crc_ok = not any(_crc_remainder(bits))
    sync_matches = _sync_matches(bits)

    return VITCDecode(
        line_number=line_number,
        timecode=f'{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}',
        hours=hours,
        minutes=minutes,
        seconds=seconds,
        frames=frames,
        field_mark=int(bits[75]),
        color_frame=int(bits[15]),
        drop_frame=int(bits[14]),
        bgf0=int(bits[35]),
        bgf1=int(bits[74]),
        bgf2=int(bits[55]),
        user_bits=user_bit_groups,
        user_bits_hex=''.join(f'{nibble:X}' for nibble in user_bit_groups),
        crc_ok=crc_ok,
        sync_matches=sync_matches,
        start_offset=float(start_offset),
        bit_width=float(bit_width),
        inverted=bool(inverted),
        threshold=float(threshold),
    )


def looks_like_vitc_line(samples):
    if samples.size < 128:
        return False
    smoothed = np.convolve(samples.astype(np.float32), np.ones(5, dtype=np.float32) / 5.0, mode='same')
    dynamic_range = float(np.percentile(smoothed, 95) - np.percentile(smoothed, 5))
    if dynamic_range < 20.0:
        return False
    threshold = float((np.percentile(smoothed, 20) + np.percentile(smoothed, 80)) / 2.0)
    binary = smoothed > threshold
    edge_count = int(np.count_nonzero(np.diff(binary.astype(np.int8))))
    return 18 <= edge_count <= 48


def decode_vitc_line(samples, config, line_number=None, previous=None):
    samples = np.asarray(samples, dtype=np.float32)
    if previous is None and not looks_like_vitc_line(samples):
        return None

    sample_positions = _bit_indices(samples.size)
    nominal_width, start_min, start_max = _vitc_search_bounds(config)
    candidates = []

    if previous is not None:
        candidates.append((
            np.linspace(max(start_min, previous.start_offset - 2.0), min(start_max, previous.start_offset + 2.0), 17),
            np.linspace(previous.bit_width * 0.9925, previous.bit_width * 1.0075, 9),
            (previous.inverted,),
        ))

    candidates.append((
        np.linspace(start_min, start_max, PAL_VITC_SEARCH_STARTS),
        np.linspace(nominal_width * 0.985, nominal_width * 1.015, PAL_VITC_SEARCH_WIDTH),
        (False, True),
    ))

    best = None
    best_score = None
    seen = set()

    for start_offsets, bit_widths, invert_options in candidates:
        for inverted in invert_options:
            for start_offset in start_offsets:
                for bit_width in bit_widths:
                    key = (round(float(start_offset), 4), round(float(bit_width), 4), bool(inverted))
                    if key in seen:
                        continue
                    seen.add(key)
                    sampled = _sample_candidate_bits(samples, sample_positions, float(start_offset), float(bit_width), bool(inverted))
                    if sampled is None:
                        continue
                    bits, threshold = sampled
                    decoded = _decode_from_bits(bits, line_number, start_offset, bit_width, inverted, threshold)
                    if decoded is None:
                        continue
                    score = (decoded.sync_matches * 10) + (100 if decoded.crc_ok else 0)
                    if best is None or score > best_score:
                        best = decoded
                        best_score = score
                        if decoded.crc_ok and decoded.sync_matches == PAL_VITC_REQUIRED_SYNC_MATCHES:
                            return decoded

    return best


def decode_vitc_lines(lines, config, previous_results=None):
    decoded = []
    previous_lookup = {}
    if previous_results is not None:
        previous_lookup = {
            int(result.line_number): result
            for result in previous_results
            if result.line_number is not None
        }

    for line_number, samples in lines:
        previous = previous_lookup.get(int(line_number))
        result = decode_vitc_line(samples, config, line_number=int(line_number), previous=previous)
        if result is not None:
            decoded.append(result)
    return tuple(decoded)


def summarise_vitc_lines(results: Iterable[VITCDecode]):
    results = tuple(results)
    if not results:
        return {
            'summary': 'No VITC decoded',
            'timecode': None,
            'lines': (),
            'results': (),
        }

    grouped = Counter(result.timecode for result in results)
    timecode, _ = grouped.most_common(1)[0]
    matching = tuple(result for result in results if result.timecode == timecode)
    return {
        'summary': f'{timecode} on lines {", ".join(str(result.line_number) for result in matching)}',
        'timecode': timecode,
        'lines': tuple(result.line_number for result in matching),
        'results': results,
    }


def preferred_vitc_lines(vbi_start=(7, 320), vbi_count=(16, 16)):
    start0, start1 = (int(vbi_start[0]), int(vbi_start[1]))
    count0, count1 = (int(vbi_count[0]), int(vbi_count[1]))
    preferred = []

    first_field = (19, 21)
    second_field = (332, 334)

    for absolute_line in first_field:
        logical = absolute_line - start0 + 1
        if 1 <= logical <= count0:
            preferred.append(logical)

    for absolute_line in second_field:
        logical = count0 + (absolute_line - start1 + 1)
        if (count0 + 1) <= logical <= (count0 + count1):
            preferred.append(logical)

    return tuple(preferred)
