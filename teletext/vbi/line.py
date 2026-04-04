# * Copyright 2016 Alistair Buxton <a.j.buxton@gmail.com>
# *
# * License: This program is free software; you can redistribute it and/or
# * modify it under the terms of the GNU General Public License as published
# * by the Free Software Foundation; either version 3 of the License, or (at
# * your option) any later version. This program is distributed in the hope
# * that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# * warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# * GNU General Public License for more details.

import importlib
import math
import pathlib
import sys
import numpy as np
from scipy.ndimage import gaussian_filter1d as gauss
from scipy.signal import resample

from teletext.packet import Packet
from teletext.elements import Mrag, DesignationCode

from .config import Config


def normalise(a, start=None, end=None):
    mn = a[start:end].min()
    mx = a[start:end].max()
    r = (mx-mn)
    if r == 0:
        r = 1
    return np.clip((a.astype(np.float32) - mn) * (255.0/r), 0, 255)


SIGNAL_CONTROL_NEUTRAL = 50
BRIGHTNESS_COEFF_DEFAULT = 48.0
GAIN_COEFF_DEFAULT = 0.5
CONTRAST_COEFF_DEFAULT = 0.5
SHARPNESS_SIGMA = 1.0
SHARPNESS_COEFF_DEFAULT = 3.0
IMPULSE_FILTER_DEFAULT = 0
TEMPORAL_DENOISE_DEFAULT = 0
NOISE_REDUCTION_DEFAULT = 0
HUM_REMOVAL_DEFAULT = 0
AUTO_BLACK_LEVEL_DEFAULT = 0
HEAD_SWITCHING_MASK_DEFAULT = 0
LINE_STABILIZATION_DEFAULT = 0
AUTO_GAIN_CONTRAST_DEFAULT = 0
QUALITY_THRESHOLD_DEFAULT = 50
CLOCK_LOCK_DEFAULT = 50
START_LOCK_DEFAULT = 50
ADAPTIVE_THRESHOLD_DEFAULT = 0
DROPOUT_REPAIR_DEFAULT = 0
WOW_FLUTTER_COMPENSATION_DEFAULT = 0
AUTO_LINE_ALIGN_DEFAULT = 0
IMPULSE_FILTER_COEFF_DEFAULT = 1.0
TEMPORAL_DENOISE_COEFF_DEFAULT = 1.0
NOISE_REDUCTION_COEFF_DEFAULT = 1.0
HUM_REMOVAL_COEFF_DEFAULT = 1.0
AUTO_BLACK_LEVEL_COEFF_DEFAULT = 1.0
HEAD_SWITCHING_MASK_COEFF_DEFAULT = 1.0
LINE_STABILIZATION_COEFF_DEFAULT = 1.0
AUTO_GAIN_CONTRAST_COEFF_DEFAULT = 1.0
QUALITY_THRESHOLD_COEFF_DEFAULT = 1.0
CLOCK_LOCK_COEFF_DEFAULT = 1.0
START_LOCK_COEFF_DEFAULT = 1.0
ADAPTIVE_THRESHOLD_COEFF_DEFAULT = 1.0
DROPOUT_REPAIR_COEFF_DEFAULT = 1.0
WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT = 1.0


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


def signal_controls_active(
    brightness=SIGNAL_CONTROL_NEUTRAL,
    sharpness=SIGNAL_CONTROL_NEUTRAL,
    gain=SIGNAL_CONTROL_NEUTRAL,
    contrast=SIGNAL_CONTROL_NEUTRAL,
    impulse_filter=IMPULSE_FILTER_DEFAULT,
    temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
    noise_reduction=NOISE_REDUCTION_DEFAULT,
    hum_removal=HUM_REMOVAL_DEFAULT,
    auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
    head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
    line_stabilization=LINE_STABILIZATION_DEFAULT,
    auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
):
    return (
        any(value != SIGNAL_CONTROL_NEUTRAL for value in (brightness, sharpness, gain, contrast))
        or int(impulse_filter) > IMPULSE_FILTER_DEFAULT
        or int(temporal_denoise) > TEMPORAL_DENOISE_DEFAULT
        or int(noise_reduction) > NOISE_REDUCTION_DEFAULT
        or int(hum_removal) > HUM_REMOVAL_DEFAULT
        or int(auto_black_level) > AUTO_BLACK_LEVEL_DEFAULT
        or int(head_switching_mask) > HEAD_SWITCHING_MASK_DEFAULT
        or int(line_stabilization) > LINE_STABILIZATION_DEFAULT
        or int(auto_gain_contrast) > AUTO_GAIN_CONTRAST_DEFAULT
    )


def _normalise_signal_controls_tuple(controls):
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
        return _normalise_signal_controls_tuple(controls)
    if len(controls) == 14:
        controls = (
            controls[0], controls[1], controls[2], controls[3],
            controls[4], controls[5], controls[6], controls[7],
            0, 0,
            controls[8], controls[9], controls[10],
            1.0, 1.0,
            controls[11], controls[12], controls[13],
        )
        return _normalise_signal_controls_tuple(controls)
    if len(controls) == 11:
        controls = (
            controls[0], controls[1], controls[2], controls[3],
            controls[4], controls[5], controls[6], controls[7],
            0, 0,
            controls[8], controls[9], controls[10],
            1.0, 1.0, 1.0, 1.0, 1.0,
        )
        return _normalise_signal_controls_tuple(controls)
    if len(controls) == 8:
        controls = controls + (0, 0, 0, 0, 0, 1.0, 1.0, 1.0, 1.0, 1.0)
        return _normalise_signal_controls_tuple(controls)
    raise ValueError(f'Expected 8, 11, 14, 16, 18, or 24 signal control values, got {len(controls)}.')


def _control_factor(value, coeff):
    return 1.0 + ((float(value) - SIGNAL_CONTROL_NEUTRAL) / SIGNAL_CONTROL_NEUTRAL) * float(coeff)


def _zero_based_strength(value, coeff, maximum=1.0):
    return float(np.clip((float(value) / 100.0) * max(float(coeff), 0.0), 0.0, maximum))


def _neutral_factor(value, coeff, maximum=2.0):
    return float(np.clip(_control_factor(value, max(float(coeff), 0.0)), 0.0, maximum))


def samples_from_bytes(data, dtype):
    samples = np.frombuffer(data, dtype=dtype).astype(np.float32)
    samples /= 256 ** (np.dtype(dtype).itemsize - 1)
    return samples


def samples_to_bytes(samples, dtype):
    scale = 256 ** (np.dtype(dtype).itemsize - 1)
    clipped = np.clip(np.rint(samples * scale), np.iinfo(dtype).min, np.iinfo(dtype).max)
    return clipped.astype(dtype).tobytes()


def apply_signal_controls(
    samples,
    brightness=SIGNAL_CONTROL_NEUTRAL,
    sharpness=SIGNAL_CONTROL_NEUTRAL,
    gain=SIGNAL_CONTROL_NEUTRAL,
    contrast=SIGNAL_CONTROL_NEUTRAL,
    brightness_coeff=BRIGHTNESS_COEFF_DEFAULT,
    sharpness_coeff=SHARPNESS_COEFF_DEFAULT,
    gain_coeff=GAIN_COEFF_DEFAULT,
    contrast_coeff=CONTRAST_COEFF_DEFAULT,
    impulse_filter=IMPULSE_FILTER_DEFAULT,
    temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
    noise_reduction=NOISE_REDUCTION_DEFAULT,
    hum_removal=HUM_REMOVAL_DEFAULT,
    auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
    head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
    line_stabilization=LINE_STABILIZATION_DEFAULT,
    auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
    impulse_filter_coeff=IMPULSE_FILTER_COEFF_DEFAULT,
    temporal_denoise_coeff=TEMPORAL_DENOISE_COEFF_DEFAULT,
    noise_reduction_coeff=NOISE_REDUCTION_COEFF_DEFAULT,
    hum_removal_coeff=HUM_REMOVAL_COEFF_DEFAULT,
    auto_black_level_coeff=AUTO_BLACK_LEVEL_COEFF_DEFAULT,
    head_switching_mask_coeff=HEAD_SWITCHING_MASK_COEFF_DEFAULT,
    line_stabilization_coeff=LINE_STABILIZATION_COEFF_DEFAULT,
    auto_gain_contrast_coeff=AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
    config=None,
    temporal_state=None,
    temporal_key=None,
):
    adjusted = np.asarray(samples, dtype=np.float32).copy()
    if adjusted.size == 0 or not signal_controls_active(
        brightness,
        sharpness,
        gain,
        contrast,
        impulse_filter=impulse_filter,
        temporal_denoise=temporal_denoise,
        noise_reduction=noise_reduction,
        hum_removal=hum_removal,
        auto_black_level=auto_black_level,
        head_switching_mask=head_switching_mask,
        line_stabilization=line_stabilization,
        auto_gain_contrast=auto_gain_contrast,
    ):
        return adjusted

    gain_factor = _control_factor(gain, gain_coeff)
    if gain_factor != 1.0:
        adjusted *= gain_factor

    contrast_factor = _control_factor(contrast, contrast_coeff)
    if contrast_factor != 1.0:
        centre = float(np.mean(adjusted))
        adjusted = centre + ((adjusted - centre) * contrast_factor)

    brightness_offset = ((float(brightness) - SIGNAL_CONTROL_NEUTRAL) / SIGNAL_CONTROL_NEUTRAL) * float(brightness_coeff)
    if brightness_offset:
        adjusted += brightness_offset

    if sharpness != SIGNAL_CONTROL_NEUTRAL:
        sharpen_blurred = gauss(adjusted, SHARPNESS_SIGMA)
        if sharpness < SIGNAL_CONTROL_NEUTRAL:
            soft_blur_sigma = SHARPNESS_SIGMA * max(float(sharpness_coeff) / SHARPNESS_COEFF_DEFAULT, 0.05)
            softened = gauss(adjusted, soft_blur_sigma)
            blend = (SIGNAL_CONTROL_NEUTRAL - float(sharpness)) / SIGNAL_CONTROL_NEUTRAL
            adjusted = (adjusted * (1.0 - blend)) + (softened * blend)
        else:
            amount = ((float(sharpness) - SIGNAL_CONTROL_NEUTRAL) / SIGNAL_CONTROL_NEUTRAL) * float(sharpness_coeff)
            adjusted = adjusted + ((adjusted - sharpen_blurred) * amount)

    if impulse_filter > IMPULSE_FILTER_DEFAULT and adjusted.size >= 3:
        strength = float(impulse_filter) / 100.0
        coeff = max(float(impulse_filter_coeff), 0.0)
        blend = min(strength * max(coeff, 0.0), 1.0)
        if blend > 0.0:
            previous = np.concatenate((adjusted[:1], adjusted[:-1]))
            following = np.concatenate((adjusted[1:], adjusted[-1:]))
            median3 = np.median(np.stack((previous, adjusted, following), axis=0), axis=0)
            deviation = np.abs(adjusted - median3)
            threshold = np.clip(20.0 - (strength * 14.0 * max(coeff, 0.05)), 2.0, 20.0)
            mask = np.clip((deviation - threshold) / max(threshold, 1.0), 0.0, 1.0)
            adjusted = adjusted + ((median3 - adjusted) * mask * blend)

    if (
        temporal_denoise > TEMPORAL_DENOISE_DEFAULT
        and temporal_state is not None
        and temporal_key is not None
        and adjusted.size > 0
    ):
        strength = float(temporal_denoise) / 100.0
        coeff = max(float(temporal_denoise_coeff), 0.0)
        blend = float(np.clip(strength * 0.65 * max(coeff, 0.05), 0.0, 0.9))
        history_key = (int(temporal_key), int(adjusted.size))
        history = temporal_state.get(history_key)
        if history is None or history.shape != adjusted.shape:
            temporal_state[history_key] = adjusted.copy()
        elif blend > 0.0:
            adjusted = ((adjusted * (1.0 - blend)) + (history * blend)).astype(np.float32, copy=False)
            store_blend = min(blend + 0.1, 0.95)
            temporal_state[history_key] = ((history * store_blend) + (adjusted * (1.0 - store_blend))).astype(np.float32, copy=False)

    if noise_reduction > NOISE_REDUCTION_DEFAULT:
        strength = float(noise_reduction) / 100.0
        coeff = max(float(noise_reduction_coeff), 0.0)
        sigma = 0.2 + (strength * 2.2 * max(coeff, 0.05))
        smoothed = gauss(adjusted, sigma)
        blend = min(strength * max(coeff, 0.0), 1.0)
        adjusted = (adjusted * (1.0 - blend)) + (smoothed * blend)

    if hum_removal > HUM_REMOVAL_DEFAULT:
        strength = float(hum_removal) / 100.0
        coeff = max(float(hum_removal_coeff), 0.0)
        sigma = 12.0 + (strength * 64.0 * max(coeff, 0.05))
        baseline = gauss(adjusted, sigma)
        blend = min(strength * max(coeff, 0.0), 1.0)
        adjusted = adjusted - ((baseline - float(np.mean(baseline))) * blend)

    if auto_black_level > AUTO_BLACK_LEVEL_DEFAULT:
        strength = float(auto_black_level) / 100.0
        coeff = max(float(auto_black_level_coeff), 0.0)
        if config is not None and getattr(config, 'start_slice', None) is not None and config.start_slice.start > 0:
            background = adjusted[:config.start_slice.start]
        elif config is not None and getattr(config, 'line_trim', None) is not None and config.line_trim < adjusted.size:
            background = adjusted[config.line_trim:]
        else:
            background = adjusted[:max(adjusted.size // 8, 1)]
        if background.size:
            reference = float(np.percentile(background, 20))
            adjusted -= reference * min(strength * max(coeff, 0.0), 1.0)

    if head_switching_mask > HEAD_SWITCHING_MASK_DEFAULT and adjusted.size >= 16:
        strength = float(head_switching_mask) / 100.0
        coeff = max(float(head_switching_mask_coeff), 0.0)
        blend = float(np.clip(strength * 0.85 * max(coeff, 0.05), 0.0, 1.0))
        if blend > 0.0:
            width = int(np.clip(round(adjusted.size * (0.015 + (strength * 0.12 * max(coeff, 0.05)))), 6, max(adjusted.size // 4, 6)))
            tail_start = max(adjusted.size - width, 0)
            anchor_start = max(tail_start - width, 0)
            anchor = adjusted[anchor_start:tail_start]
            target = float(np.mean(anchor)) if anchor.size else float(adjusted[tail_start - 1])
            ramp = np.linspace(0.0, 1.0, adjusted.size - tail_start, dtype=np.float32)
            adjusted[tail_start:] = adjusted[tail_start:] + ((target - adjusted[tail_start:]) * ramp * blend)

    if line_stabilization > LINE_STABILIZATION_DEFAULT and adjusted.size > 0 and temporal_state is not None:
        strength = float(line_stabilization) / 100.0
        coeff = max(float(line_stabilization_coeff), 0.0)
        blend = float(np.clip(strength * 0.7 * max(coeff, 0.05), 0.0, 0.9))
        history_key = ('_line_stabilization', int(adjusted.size))
        meta_key = ('_line_stabilization_meta', int(adjusted.size))
        previous = temporal_state.get(history_key)
        last_key = temporal_state.get(meta_key)
        current_key = None if temporal_key is None else int(temporal_key)
        if (
            previous is not None
            and previous.shape == adjusted.shape
            and blend > 0.0
            and (
                current_key is None
                or last_key is None
                or (0 <= (current_key - int(last_key)) <= 2)
            )
        ):
            current_mean = float(np.mean(adjusted))
            previous_mean = float(np.mean(previous))
            current_std = float(np.std(adjusted))
            previous_std = float(np.std(previous))
            if current_std > 1e-3 and previous_std > 1e-3:
                stabilised = (((adjusted - current_mean) * (previous_std / current_std)) + previous_mean).astype(np.float32, copy=False)
            else:
                stabilised = (adjusted + (previous_mean - current_mean)).astype(np.float32, copy=False)
            adjusted = ((adjusted * (1.0 - blend)) + (stabilised * blend)).astype(np.float32, copy=False)
            temporal_state[history_key] = ((previous * 0.35) + (adjusted * 0.65)).astype(np.float32, copy=False)
        else:
            temporal_state[history_key] = adjusted.copy()
        if current_key is not None:
            temporal_state[meta_key] = current_key

    if auto_gain_contrast > AUTO_GAIN_CONTRAST_DEFAULT and adjusted.size >= 8:
        strength = float(auto_gain_contrast) / 100.0
        coeff = max(float(auto_gain_contrast_coeff), 0.0)
        blend = float(np.clip(strength * max(coeff, 0.05), 0.0, 1.0))
        if blend > 0.0:
            low = float(np.percentile(adjusted, 10))
            high = float(np.percentile(adjusted, 90))
            dynamic = high - low
            if dynamic > 1.0:
                target_low = 18.0
                target_high = 236.0
                scale = (target_high - target_low) / dynamic
                auto_adjusted = ((adjusted - low) * scale) + target_low
                auto_adjusted = np.clip(auto_adjusted, 0, 255)
                adjusted = ((adjusted * (1.0 - blend)) + (auto_adjusted * blend)).astype(np.float32, copy=False)

    return np.clip(adjusted, 0, 255).astype(np.float32, copy=False)


def process_line_bytes(
    data,
    config,
    brightness=SIGNAL_CONTROL_NEUTRAL,
    sharpness=SIGNAL_CONTROL_NEUTRAL,
    gain=SIGNAL_CONTROL_NEUTRAL,
    contrast=SIGNAL_CONTROL_NEUTRAL,
    brightness_coeff=BRIGHTNESS_COEFF_DEFAULT,
    sharpness_coeff=SHARPNESS_COEFF_DEFAULT,
    gain_coeff=GAIN_COEFF_DEFAULT,
    contrast_coeff=CONTRAST_COEFF_DEFAULT,
    impulse_filter=IMPULSE_FILTER_DEFAULT,
    temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
    noise_reduction=NOISE_REDUCTION_DEFAULT,
    hum_removal=HUM_REMOVAL_DEFAULT,
    auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
    head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
    line_stabilization=LINE_STABILIZATION_DEFAULT,
    auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
    impulse_filter_coeff=IMPULSE_FILTER_COEFF_DEFAULT,
    temporal_denoise_coeff=TEMPORAL_DENOISE_COEFF_DEFAULT,
    noise_reduction_coeff=NOISE_REDUCTION_COEFF_DEFAULT,
    hum_removal_coeff=HUM_REMOVAL_COEFF_DEFAULT,
    auto_black_level_coeff=AUTO_BLACK_LEVEL_COEFF_DEFAULT,
    head_switching_mask_coeff=HEAD_SWITCHING_MASK_COEFF_DEFAULT,
    line_stabilization_coeff=LINE_STABILIZATION_COEFF_DEFAULT,
    auto_gain_contrast_coeff=AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
    preserve_tail=0,
    temporal_state=None,
    temporal_key=None,
):
    if not signal_controls_active(
        brightness,
        sharpness,
        gain,
        contrast,
        impulse_filter=impulse_filter,
        temporal_denoise=temporal_denoise,
        noise_reduction=noise_reduction,
        hum_removal=hum_removal,
        auto_black_level=auto_black_level,
        head_switching_mask=head_switching_mask,
        line_stabilization=line_stabilization,
        auto_gain_contrast=auto_gain_contrast,
    ):
        return data

    payload = data[:-preserve_tail] if preserve_tail else data
    if not payload:
        return data

    adjusted = apply_signal_controls(
        samples_from_bytes(payload, config.dtype),
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
        line_stabilization=line_stabilization,
        auto_gain_contrast=auto_gain_contrast,
        impulse_filter_coeff=impulse_filter_coeff,
        temporal_denoise_coeff=temporal_denoise_coeff,
        noise_reduction_coeff=noise_reduction_coeff,
        hum_removal_coeff=hum_removal_coeff,
        auto_black_level_coeff=auto_black_level_coeff,
        head_switching_mask_coeff=head_switching_mask_coeff,
        line_stabilization_coeff=line_stabilization_coeff,
        auto_gain_contrast_coeff=auto_gain_contrast_coeff,
        config=config,
        temporal_state=temporal_state,
        temporal_key=temporal_key,
    )
    result = samples_to_bytes(adjusted, config.dtype)
    if preserve_tail:
        result += data[-preserve_tail:]
    return result


def process_frame_bytes(
    data,
    config,
    brightness=SIGNAL_CONTROL_NEUTRAL,
    sharpness=SIGNAL_CONTROL_NEUTRAL,
    gain=SIGNAL_CONTROL_NEUTRAL,
    contrast=SIGNAL_CONTROL_NEUTRAL,
    brightness_coeff=BRIGHTNESS_COEFF_DEFAULT,
    sharpness_coeff=SHARPNESS_COEFF_DEFAULT,
    gain_coeff=GAIN_COEFF_DEFAULT,
    contrast_coeff=CONTRAST_COEFF_DEFAULT,
    impulse_filter=IMPULSE_FILTER_DEFAULT,
    temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
    noise_reduction=NOISE_REDUCTION_DEFAULT,
    hum_removal=HUM_REMOVAL_DEFAULT,
    auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
    head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
    line_stabilization=LINE_STABILIZATION_DEFAULT,
    auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
    impulse_filter_coeff=IMPULSE_FILTER_COEFF_DEFAULT,
    temporal_denoise_coeff=TEMPORAL_DENOISE_COEFF_DEFAULT,
    noise_reduction_coeff=NOISE_REDUCTION_COEFF_DEFAULT,
    hum_removal_coeff=HUM_REMOVAL_COEFF_DEFAULT,
    auto_black_level_coeff=AUTO_BLACK_LEVEL_COEFF_DEFAULT,
    head_switching_mask_coeff=HEAD_SWITCHING_MASK_COEFF_DEFAULT,
    line_stabilization_coeff=LINE_STABILIZATION_COEFF_DEFAULT,
    auto_gain_contrast_coeff=AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
    preserve_tail=0,
    temporal_state=None,
    line_control_overrides=None,
):
    base_controls_active = signal_controls_active(
        brightness,
        sharpness,
        gain,
        contrast,
        impulse_filter=impulse_filter,
        temporal_denoise=temporal_denoise,
        noise_reduction=noise_reduction,
        hum_removal=hum_removal,
        auto_black_level=auto_black_level,
        head_switching_mask=head_switching_mask,
        line_stabilization=line_stabilization,
        auto_gain_contrast=auto_gain_contrast,
    )
    normalised_line_control_overrides = {
        int(line): _normalise_signal_controls_tuple(values)
        for line, values in dict(line_control_overrides or {}).items()
    }
    if (
        not base_controls_active
        and not any(
            signal_controls_active(
                override[0],
                override[1],
                override[2],
                override[3],
                impulse_filter=override[8],
                temporal_denoise=override[9],
                noise_reduction=override[10],
                hum_removal=override[11],
                auto_black_level=override[12],
                head_switching_mask=override[13],
                line_stabilization=override[14],
                auto_gain_contrast=override[15],
            )
            for override in normalised_line_control_overrides.values()
        )
    ):
        return data

    frame_lines = config.field_lines * 2
    line_bytes = config.line_bytes
    output = bytearray()
    base_controls = (
        brightness, sharpness, gain, contrast,
        brightness_coeff, sharpness_coeff, gain_coeff, contrast_coeff,
        impulse_filter, temporal_denoise, noise_reduction, hum_removal,
        auto_black_level, head_switching_mask, line_stabilization, auto_gain_contrast,
        impulse_filter_coeff, temporal_denoise_coeff, noise_reduction_coeff, hum_removal_coeff,
        auto_black_level_coeff, head_switching_mask_coeff, line_stabilization_coeff, auto_gain_contrast_coeff,
    )

    for line_number in range(frame_lines):
        start = line_number * line_bytes
        end = start + line_bytes
        if end > len(data):
            output.extend(data[start:])
            break
        line_tail = preserve_tail if preserve_tail and line_number == (frame_lines - 1) else 0
        line_controls = normalised_line_control_overrides.get(line_number + 1, base_controls)
        output.extend(process_line_bytes(
            data[start:end],
            config,
            brightness=line_controls[0],
            sharpness=line_controls[1],
            gain=line_controls[2],
            contrast=line_controls[3],
            brightness_coeff=line_controls[4],
            sharpness_coeff=line_controls[5],
            gain_coeff=line_controls[6],
            contrast_coeff=line_controls[7],
            impulse_filter=line_controls[8],
            temporal_denoise=line_controls[9],
            noise_reduction=line_controls[10],
            hum_removal=line_controls[11],
            auto_black_level=line_controls[12],
            head_switching_mask=line_controls[13],
            line_stabilization=line_controls[14],
            auto_gain_contrast=line_controls[15],
            impulse_filter_coeff=line_controls[16],
            temporal_denoise_coeff=line_controls[17],
            noise_reduction_coeff=line_controls[18],
            hum_removal_coeff=line_controls[19],
            auto_black_level_coeff=line_controls[20],
            head_switching_mask_coeff=line_controls[21],
            line_stabilization_coeff=line_controls[22],
            auto_gain_contrast_coeff=line_controls[23],
            preserve_tail=line_tail,
            temporal_state=temporal_state,
            temporal_key=line_number,
        ))

    if len(output) < len(data):
        output.extend(data[len(output):])

    return bytes(output)


# Line: Handles a single line of raw VBI samples.

class Line(object):
    """Container for a single line of raw samples."""

    config: Config

    configured = False
    brightness = SIGNAL_CONTROL_NEUTRAL
    sharpness = SIGNAL_CONTROL_NEUTRAL
    gain = SIGNAL_CONTROL_NEUTRAL
    contrast = SIGNAL_CONTROL_NEUTRAL
    brightness_coeff = BRIGHTNESS_COEFF_DEFAULT
    sharpness_coeff = SHARPNESS_COEFF_DEFAULT
    gain_coeff = GAIN_COEFF_DEFAULT
    contrast_coeff = CONTRAST_COEFF_DEFAULT
    impulse_filter = IMPULSE_FILTER_DEFAULT
    temporal_denoise = TEMPORAL_DENOISE_DEFAULT
    noise_reduction = NOISE_REDUCTION_DEFAULT
    hum_removal = HUM_REMOVAL_DEFAULT
    auto_black_level = AUTO_BLACK_LEVEL_DEFAULT
    head_switching_mask = HEAD_SWITCHING_MASK_DEFAULT
    line_stabilization = LINE_STABILIZATION_DEFAULT
    auto_gain_contrast = AUTO_GAIN_CONTRAST_DEFAULT
    quality_threshold = QUALITY_THRESHOLD_DEFAULT
    clock_lock = CLOCK_LOCK_DEFAULT
    start_lock = START_LOCK_DEFAULT
    adaptive_threshold = ADAPTIVE_THRESHOLD_DEFAULT
    dropout_repair = DROPOUT_REPAIR_DEFAULT
    wow_flutter_compensation = WOW_FLUTTER_COMPENSATION_DEFAULT
    auto_line_align = AUTO_LINE_ALIGN_DEFAULT
    per_line_shift_map = {}
    line_control_overrides = {}
    line_decoder_overrides = {}
    impulse_filter_coeff = IMPULSE_FILTER_COEFF_DEFAULT
    temporal_denoise_coeff = TEMPORAL_DENOISE_COEFF_DEFAULT
    noise_reduction_coeff = NOISE_REDUCTION_COEFF_DEFAULT
    hum_removal_coeff = HUM_REMOVAL_COEFF_DEFAULT
    auto_black_level_coeff = AUTO_BLACK_LEVEL_COEFF_DEFAULT
    head_switching_mask_coeff = HEAD_SWITCHING_MASK_COEFF_DEFAULT
    line_stabilization_coeff = LINE_STABILIZATION_COEFF_DEFAULT
    auto_gain_contrast_coeff = AUTO_GAIN_CONTRAST_COEFF_DEFAULT
    quality_threshold_coeff = QUALITY_THRESHOLD_COEFF_DEFAULT
    temporal_line_count = 32
    _temporal_state = {}

    @classmethod
    def configure_patterns(cls, method, tape_format):
        try:
            module = importlib.import_module(".pattern" + method.lower(), __package__)
            Pattern = getattr(module, "Pattern" + method)
            datadir = pathlib.Path(__file__).parent / 'data' / tape_format
            cls.h = Pattern(datadir / 'hamming.dat')
            cls.p = Pattern(datadir / 'parity.dat')
            cls.f = Pattern(datadir / 'full.dat')
            return True
        except Exception as e:
            sys.stderr.write(str(e) + '\n')
            sys.stderr.write((method if method else 'CPU') + ' init failed.\n')
            return False

    @classmethod
    def configure(
        cls,
        config,
        force_cpu=False,
        prefer_opencl=False,
        tape_format='vhs',
        brightness=SIGNAL_CONTROL_NEUTRAL,
        sharpness=SIGNAL_CONTROL_NEUTRAL,
        gain=SIGNAL_CONTROL_NEUTRAL,
        contrast=SIGNAL_CONTROL_NEUTRAL,
        brightness_coeff=BRIGHTNESS_COEFF_DEFAULT,
        sharpness_coeff=SHARPNESS_COEFF_DEFAULT,
        gain_coeff=GAIN_COEFF_DEFAULT,
        contrast_coeff=CONTRAST_COEFF_DEFAULT,
        impulse_filter=IMPULSE_FILTER_DEFAULT,
        temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
        noise_reduction=NOISE_REDUCTION_DEFAULT,
        hum_removal=HUM_REMOVAL_DEFAULT,
        auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
        head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
        line_stabilization=LINE_STABILIZATION_DEFAULT,
        auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
        impulse_filter_coeff=IMPULSE_FILTER_COEFF_DEFAULT,
        temporal_denoise_coeff=TEMPORAL_DENOISE_COEFF_DEFAULT,
        noise_reduction_coeff=NOISE_REDUCTION_COEFF_DEFAULT,
        hum_removal_coeff=HUM_REMOVAL_COEFF_DEFAULT,
        auto_black_level_coeff=AUTO_BLACK_LEVEL_COEFF_DEFAULT,
        head_switching_mask_coeff=HEAD_SWITCHING_MASK_COEFF_DEFAULT,
        line_stabilization_coeff=LINE_STABILIZATION_COEFF_DEFAULT,
        auto_gain_contrast_coeff=AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
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
    ):
        cls.config = config
        cls.set_signal_controls(
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
            line_stabilization=line_stabilization,
            auto_gain_contrast=auto_gain_contrast,
            impulse_filter_coeff=impulse_filter_coeff,
            temporal_denoise_coeff=temporal_denoise_coeff,
            noise_reduction_coeff=noise_reduction_coeff,
            hum_removal_coeff=hum_removal_coeff,
            auto_black_level_coeff=auto_black_level_coeff,
            head_switching_mask_coeff=head_switching_mask_coeff,
            line_stabilization_coeff=line_stabilization_coeff,
            auto_gain_contrast_coeff=auto_gain_contrast_coeff,
        )
        cls.quality_threshold = int(quality_threshold)
        cls.quality_threshold_coeff = float(quality_threshold_coeff)
        cls.clock_lock = int(clock_lock)
        cls.clock_lock_coeff = float(clock_lock_coeff)
        cls.start_lock = int(start_lock)
        cls.start_lock_coeff = float(start_lock_coeff)
        cls.adaptive_threshold = int(adaptive_threshold)
        cls.adaptive_threshold_coeff = float(adaptive_threshold_coeff)
        cls.dropout_repair = int(dropout_repair)
        cls.dropout_repair_coeff = float(dropout_repair_coeff)
        cls.wow_flutter_compensation = int(wow_flutter_compensation)
        cls.wow_flutter_compensation_coeff = float(wow_flutter_compensation_coeff)
        cls.temporal_line_count = max(len(getattr(config, 'field_range', ())) * 2, 1)
        cls.auto_line_align = int(auto_line_align)
        cls.per_line_shift_map = normalise_per_line_shift_map(
            per_line_shift,
            maximum_line=max(cls.temporal_line_count, 32),
        )
        cls.line_control_overrides = {
            int(line): tuple(values)
            for line, values in dict(line_control_overrides or {}).items()
        }
        cls.line_decoder_overrides = {
            int(line): dict(values)
            for line, values in dict(line_decoder_overrides or {}).items()
        }
        cls.reset_temporal_state()
        if force_cpu:
            methods = ['']
        elif prefer_opencl:
            methods = ['OpenCL', 'CUDA', '']
        else:
            methods = ['CUDA', 'OpenCL', '']
        if any(cls.configure_patterns(method, tape_format) for method in methods):
            cls.configured = True
        else:
            raise Exception('Could not initialize any deconvolution method.')

    @classmethod
    def set_signal_controls(
        cls,
        brightness=SIGNAL_CONTROL_NEUTRAL,
        sharpness=SIGNAL_CONTROL_NEUTRAL,
        gain=SIGNAL_CONTROL_NEUTRAL,
        contrast=SIGNAL_CONTROL_NEUTRAL,
        brightness_coeff=BRIGHTNESS_COEFF_DEFAULT,
        sharpness_coeff=SHARPNESS_COEFF_DEFAULT,
        gain_coeff=GAIN_COEFF_DEFAULT,
        contrast_coeff=CONTRAST_COEFF_DEFAULT,
        impulse_filter=IMPULSE_FILTER_DEFAULT,
        temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
        noise_reduction=NOISE_REDUCTION_DEFAULT,
        hum_removal=HUM_REMOVAL_DEFAULT,
        auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
        head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
        line_stabilization=LINE_STABILIZATION_DEFAULT,
        auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
        impulse_filter_coeff=IMPULSE_FILTER_COEFF_DEFAULT,
        temporal_denoise_coeff=TEMPORAL_DENOISE_COEFF_DEFAULT,
        noise_reduction_coeff=NOISE_REDUCTION_COEFF_DEFAULT,
        hum_removal_coeff=HUM_REMOVAL_COEFF_DEFAULT,
        auto_black_level_coeff=AUTO_BLACK_LEVEL_COEFF_DEFAULT,
        head_switching_mask_coeff=HEAD_SWITCHING_MASK_COEFF_DEFAULT,
        line_stabilization_coeff=LINE_STABILIZATION_COEFF_DEFAULT,
        auto_gain_contrast_coeff=AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
    ):
        cls.brightness = brightness
        cls.sharpness = sharpness
        cls.gain = gain
        cls.contrast = contrast
        cls.brightness_coeff = brightness_coeff
        cls.sharpness_coeff = sharpness_coeff
        cls.gain_coeff = gain_coeff
        cls.contrast_coeff = contrast_coeff
        cls.impulse_filter = impulse_filter
        cls.temporal_denoise = temporal_denoise
        cls.noise_reduction = noise_reduction
        cls.hum_removal = hum_removal
        cls.auto_black_level = auto_black_level
        cls.head_switching_mask = head_switching_mask
        cls.line_stabilization = line_stabilization
        cls.auto_gain_contrast = auto_gain_contrast
        cls.impulse_filter_coeff = impulse_filter_coeff
        cls.temporal_denoise_coeff = temporal_denoise_coeff
        cls.noise_reduction_coeff = noise_reduction_coeff
        cls.hum_removal_coeff = hum_removal_coeff
        cls.auto_black_level_coeff = auto_black_level_coeff
        cls.head_switching_mask_coeff = head_switching_mask_coeff
        cls.line_stabilization_coeff = line_stabilization_coeff
        cls.auto_gain_contrast_coeff = auto_gain_contrast_coeff
        cls.reset_temporal_state()

    @classmethod
    def reset_temporal_state(cls):
        cls._temporal_state = {}

    @classmethod
    def temporal_key(cls, number):
        if number is None:
            return None
        line_count = max(int(getattr(cls, 'temporal_line_count', 0)), 1)
        return int(number) % line_count

    def __init__(self, data, number=None):
        if not self.configured:
            self.configure(Config())

        self._number = number
        logical_line = self.temporal_key(number)
        user_line = None if logical_line is None else (int(logical_line) + 1)
        self.config = type(self).config

        signal_values = {
            'brightness': self.brightness,
            'sharpness': self.sharpness,
            'gain': self.gain,
            'contrast': self.contrast,
            'brightness_coeff': self.brightness_coeff,
            'sharpness_coeff': self.sharpness_coeff,
            'gain_coeff': self.gain_coeff,
            'contrast_coeff': self.contrast_coeff,
            'impulse_filter': self.impulse_filter,
            'temporal_denoise': self.temporal_denoise,
            'noise_reduction': self.noise_reduction,
            'hum_removal': self.hum_removal,
            'auto_black_level': self.auto_black_level,
            'head_switching_mask': self.head_switching_mask,
            'line_stabilization': self.line_stabilization,
            'auto_gain_contrast': self.auto_gain_contrast,
            'impulse_filter_coeff': self.impulse_filter_coeff,
            'temporal_denoise_coeff': self.temporal_denoise_coeff,
            'noise_reduction_coeff': self.noise_reduction_coeff,
            'hum_removal_coeff': self.hum_removal_coeff,
            'auto_black_level_coeff': self.auto_black_level_coeff,
            'head_switching_mask_coeff': self.head_switching_mask_coeff,
            'line_stabilization_coeff': self.line_stabilization_coeff,
            'auto_gain_contrast_coeff': self.auto_gain_contrast_coeff,
        }
        decoder_values = {
            'quality_threshold': self.quality_threshold,
            'quality_threshold_coeff': self.quality_threshold_coeff,
            'clock_lock': self.clock_lock,
            'clock_lock_coeff': self.clock_lock_coeff,
            'start_lock': self.start_lock,
            'start_lock_coeff': self.start_lock_coeff,
            'adaptive_threshold': self.adaptive_threshold,
            'adaptive_threshold_coeff': self.adaptive_threshold_coeff,
            'dropout_repair': self.dropout_repair,
            'dropout_repair_coeff': self.dropout_repair_coeff,
            'wow_flutter_compensation': self.wow_flutter_compensation,
            'wow_flutter_compensation_coeff': self.wow_flutter_compensation_coeff,
            'auto_line_align': self.auto_line_align,
        }
        if user_line is not None:
            override_values = getattr(type(self), 'line_control_overrides', {}).get(user_line)
            if override_values is not None:
                signal_keys = (
                    'brightness', 'sharpness', 'gain', 'contrast',
                    'brightness_coeff', 'sharpness_coeff', 'gain_coeff', 'contrast_coeff',
                    'impulse_filter', 'temporal_denoise', 'noise_reduction', 'hum_removal',
                    'auto_black_level', 'head_switching_mask', 'line_stabilization', 'auto_gain_contrast',
                    'impulse_filter_coeff', 'temporal_denoise_coeff', 'noise_reduction_coeff',
                    'hum_removal_coeff', 'auto_black_level_coeff', 'head_switching_mask_coeff',
                    'line_stabilization_coeff', 'auto_gain_contrast_coeff',
                )
                signal_values.update({
                    key: value for key, value in zip(signal_keys, tuple(override_values))
                })
            decoder_override = getattr(type(self), 'line_decoder_overrides', {}).get(user_line, {})
            if decoder_override:
                decoder_values.update({
                    key: value for key, value in decoder_override.items()
                    if key in decoder_values
                })
                line_start_range = decoder_override.get('line_start_range')
                extra_roll = decoder_override.get('extra_roll')
                if line_start_range is not None or extra_roll is not None:
                    self.config = self.config.retuned(
                        extra_roll=int(extra_roll) if extra_roll is not None else int(self.config.extra_roll),
                        line_start_range=tuple(int(value) for value in line_start_range) if line_start_range is not None else tuple(int(value) for value in self.config.line_start_range),
                    )

        for key, value in signal_values.items():
            setattr(self, key, value)
        for key, value in decoder_values.items():
            setattr(self, key, value)

        self._original = samples_from_bytes(data, self.config.dtype)
        self._original = apply_signal_controls(
            self._original,
            brightness=signal_values['brightness'],
            sharpness=signal_values['sharpness'],
            gain=signal_values['gain'],
            contrast=signal_values['contrast'],
            brightness_coeff=signal_values['brightness_coeff'],
            sharpness_coeff=signal_values['sharpness_coeff'],
            gain_coeff=signal_values['gain_coeff'],
            contrast_coeff=signal_values['contrast_coeff'],
            impulse_filter=signal_values['impulse_filter'],
            temporal_denoise=signal_values['temporal_denoise'],
            noise_reduction=signal_values['noise_reduction'],
            hum_removal=signal_values['hum_removal'],
            auto_black_level=signal_values['auto_black_level'],
            head_switching_mask=signal_values['head_switching_mask'],
            line_stabilization=signal_values['line_stabilization'],
            auto_gain_contrast=signal_values['auto_gain_contrast'],
            impulse_filter_coeff=signal_values['impulse_filter_coeff'],
            temporal_denoise_coeff=signal_values['temporal_denoise_coeff'],
            noise_reduction_coeff=signal_values['noise_reduction_coeff'],
            hum_removal_coeff=signal_values['hum_removal_coeff'],
            auto_black_level_coeff=signal_values['auto_black_level_coeff'],
            head_switching_mask_coeff=signal_values['head_switching_mask_coeff'],
            line_stabilization_coeff=signal_values['line_stabilization_coeff'],
            auto_gain_contrast_coeff=signal_values['auto_gain_contrast_coeff'],
            config=self.config,
            temporal_state=self._temporal_state,
            temporal_key=logical_line,
        )
        self._original_bytes = data

        resample_tmp = np.pad(self._original, (0, self.config.resample_pad), 'constant', constant_values=(0,0))

        self._resampled = np.pad(resample(resample_tmp, self.config.resample_tgt)[:self.config.resample_size], (0, 64), 'edge')

        self.reset()

    def reset(self):
        """Reset line to original unknown state."""
        self.roll = 0

        self._noisefloor = None
        self._max = None
        self._fft = None
        self._fft_harmonics = None
        self._gstart = None
        self._signal_max = None
        self._is_teletext = None
        self._start = None
        self._clock_locked_start = None
        self._pre_alignment_start = None
        self._post_alignment_start = None
        self._auto_align_target = None
        self._reason = None
        self._diagnostic_quality = None

    @property
    def resampled(self):
        """The resampled line. 8 samples = 1 bit."""
        return self._resampled[:]

    @property
    def original(self):
        """The raw, untouched line."""
        return self._original[:]

    @property
    def rolled(self):
        if self.start is not None:
            return np.roll(self._resampled, 90-(self.start+self.roll))
        else:
            return self._resampled[:]

    @property
    def gradient(self):
        return (np.gradient(gauss(self.rolled, 12))[20:300]>0)*255

    def fchop(self, start, stop):
        """Chop the samples associated with each bit."""
        # This should use self.start not self._start so that self._start
        # is calculated if it hasn't been already.
        r = (self.start + self.roll)
        # sys.stderr.write(f'{r}, {start}, {stop}, {d.shape}\n')
        return self._resampled[r + (start * 8):r + (stop * 8)]

    def chop(self, start, stop):
        """Average the samples associated with each bit."""
        return np.mean(self.fchop(start, stop).reshape(-1, 8), 1)

    @property
    def chopped(self):
        """The whole chopped teletext line, for vbi viewer."""
        return self.chop(0, 360)

    @property
    def noisefloor(self):
        if self._noisefloor is None:
            if self.config.start_slice.start == 0:
                self._noisefloor = np.max(gauss(self._resampled[self.config.line_trim:-4], self.config.gauss))
            else:
                self._noisefloor = np.max(gauss(self._resampled[:self.config.start_slice.start], self.config.gauss))
        return self._noisefloor

    @property
    def fft(self):
        """The FFT of the original line."""
        if self._fft is None:
            # This test only looks at the bins for the harmonics.
            # It could be made smarter by looking at all bins.
            self._fft = normalise(gauss(np.abs(np.fft.fft(np.diff(self._resampled[:3200], n=1))[:256]), 4))
        return self._fft

    def find_start(self):
        self._clock_locked_start = None
        self._pre_alignment_start = None
        self._post_alignment_start = None
        self._auto_align_target = None
        quality_factor = 1.0 + (
            ((float(self.quality_threshold) - QUALITY_THRESHOLD_DEFAULT) / QUALITY_THRESHOLD_DEFAULT)
            * (0.8 * float(self.quality_threshold_coeff))
        )
        signal_threshold = float(np.clip(64.0 * quality_factor, 16.0, 160.0))
        noise_threshold = float(np.clip(80.0 / max(quality_factor, 0.2), 32.0, 160.0))
        delta_threshold = float(np.clip(16.0 * quality_factor, 4.0, 64.0))
        fft_threshold = float(np.clip(1000.0 * quality_factor, 300.0, 2500.0))

        # First try to detect by comparing pre-start noise floor to post-start levels.
        # Store self._gstart so that self.start can re-use it.
        self._gstart = gauss(self._resampled[self.config.start_slice], self.config.gauss)
        smax = float(np.max(self._gstart))
        self._signal_max = smax
        if smax < signal_threshold:
            self._is_teletext = False
            self._reason = f'Signal max is {smax}'
        elif self.noisefloor > noise_threshold:
            self._is_teletext = False
            self._reason = f'Noise is {self.noisefloor}'
        elif smax < (self.noisefloor + delta_threshold):
            # There is no interesting signal in the start_slice.
            self._is_teletext = False
            self._reason = f'Noise is higher than signal {smax} {self.noisefloor}'
        else:
            # There is some kind of signal in the line. Check if
            # it is teletext by looking for harmonics of teletext
            # symbol rate.
            fftchop = np.add.reduceat(self.fft, self.config.fftbins)
            harmonic_sum = float(np.sum(fftchop[1:-1:2]))
            self._fft_harmonics = harmonic_sum
            self._is_teletext = harmonic_sum > fft_threshold
            if not self._is_teletext:
                self._reason = f'FFT is {harmonic_sum:.1f}'
        if not self._is_teletext:
            return

        rough_start = self.rough_start_from_gradient()
        self._start = rough_start
        # Now find the extra roll needed to lock in the clock run-in and framing code.
        confidence = []

        for roll in range(max(-30, 8-self._start), 20):
            self.roll = roll
            # 15:20 is the last bit of CRI and first 4 bits of FC - 01110.
            # This is the most distinctive part of the CRI/FC to look for.
            c = self.chop(15, 21)
            confidence.append((c[1] + c[2] + c[3] - c[0] - c[4] - c[5], roll))
            #confidence.append((np.sum(self.chop(15, 20) * self.config.crifc[15:20]), roll))

        first_roll = max(confidence)[1]
        self.roll = 0

        # Use the observed CRIFC to lock to the framing code
        confidence = []
        for roll in range(-4, 4):
            self.roll = roll
            x = np.gradient(self.fchop(8, 24))
            c = np.sum(np.square(x - self.config.observed_crifc_gradient))
            confidence.append((c, roll))

        second_roll = min(confidence)[1]
        self.roll = 0

        lock_factor = _neutral_factor(
            self.clock_lock,
            getattr(self, 'clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT),
        )
        total_roll = first_roll + second_roll
        self._start = rough_start + int(round(total_roll * lock_factor)) + self.config.extra_roll
        self._clock_locked_start = int(self._start)
        self.apply_wow_flutter_compensation()
        self._pre_alignment_start = int(self._start) if self._start is not None else None
        self.apply_auto_line_align()
        self._post_alignment_start = int(self._start) if self._start is not None else None
        self.apply_per_line_shift()

    def rough_start_from_gradient(self):
        edge_gradient = np.gradient(np.maximum.accumulate(self._gstart))
        rough_start = int(np.argmax(edge_gradient)) + self.config.start_slice.start

        start_lock_coeff = max(float(getattr(self, 'start_lock_coeff', START_LOCK_COEFF_DEFAULT)), 0.0)
        if self.start_lock < START_LOCK_DEFAULT:
            loosen = (float(START_LOCK_DEFAULT - self.start_lock) / START_LOCK_DEFAULT) * start_lock_coeff
            direct_gradient = np.maximum(np.gradient(self._gstart), 0.0)
            direct_start = int(np.argmax(direct_gradient)) + self.config.start_slice.start
            unlocked_target = int(round((direct_start + self.config.start_slice.start) / 2.0))
            blend = min(loosen * 0.75, 0.85)
            rough_start = int(round((rough_start * (1.0 - blend)) + (unlocked_target * blend)))
        elif self.start_lock > START_LOCK_DEFAULT:
            tighten = (float(self.start_lock - START_LOCK_DEFAULT) / START_LOCK_DEFAULT) * start_lock_coeff
            expected_start = int(round((self.config.start_slice.start + self.config.start_slice.stop) / 2.0))
            blend = min(tighten * 0.65, 0.75)
            rough_start = int(round((rough_start * (1.0 - blend)) + (expected_start * blend)))

        return rough_start

    def adaptive_normalise(self, chopped):
        bits = np.asarray(chopped, dtype=np.float32)
        normalised = normalise(bits)
        if self.adaptive_threshold <= ADAPTIVE_THRESHOLD_DEFAULT or bits.size < 8:
            return normalised

        strength = _zero_based_strength(
            self.adaptive_threshold,
            getattr(self, 'adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT),
            maximum=1.0,
        )
        baseline_sigma = 4.0 + ((1.0 - strength) * 8.0)
        baseline = gauss(bits, baseline_sigma)
        centred = bits - baseline + float(np.mean(baseline))
        adaptive = normalise(centred)
        return ((normalised * (1.0 - strength)) + (adaptive * strength)).astype(np.float32, copy=False)

    def repair_dropouts(self, chopped):
        bits = np.asarray(chopped, dtype=np.float32)
        if self.dropout_repair <= DROPOUT_REPAIR_DEFAULT or bits.size < 5:
            return bits

        strength = _zero_based_strength(
            self.dropout_repair,
            getattr(self, 'dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT),
            maximum=1.0,
        )
        padded = np.pad(bits, (2, 2), mode='edge')
        windows = np.stack([padded[index:index + bits.size] for index in range(5)], axis=0)
        median5 = np.median(windows, axis=0)
        deviation = np.abs(bits - median5)
        threshold = np.clip(48.0 - (strength * 32.0), 8.0, 48.0)
        mask = np.clip((deviation - threshold) / max(threshold, 1.0), 0.0, 1.0)
        blend = 0.35 + (strength * 0.55)
        repaired = bits + ((median5 - bits) * mask * blend)
        return repaired.astype(np.float32, copy=False)

    def apply_wow_flutter_compensation(self):
        if self.wow_flutter_compensation <= WOW_FLUTTER_COMPENSATION_DEFAULT or self._start is None:
            return
        key = self.temporal_key(self._number)
        if key is None:
            return

        history_key = ('_wow_flutter_start', int(key))
        previous_start = self._temporal_state.get(history_key)
        current_start = float(self._start)
        if previous_start is None:
            self._temporal_state[history_key] = current_start
            return

        strength = _zero_based_strength(
            self.wow_flutter_compensation,
            getattr(self, 'wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT),
            maximum=1.0,
        )
        drift_limit = np.clip(10.0 - (strength * 6.0), 2.0, 10.0)
        corrected_start = float(previous_start) + float(np.clip(current_start - float(previous_start), -drift_limit, drift_limit))
        blend = np.clip(0.18 + (strength * 0.62), 0.0, 0.85)
        self._start = int(round((current_start * (1.0 - blend)) + (corrected_start * blend)))
        self._temporal_state[history_key] = (float(previous_start) * 0.4) + (float(self._start) * 0.6)

    def apply_auto_line_align(self):
        if self.auto_line_align <= AUTO_LINE_ALIGN_DEFAULT or self._start is None:
            self._auto_align_target = None
            return

        history_key = '_auto_line_align_recent'
        history = list(self._temporal_state.get(history_key, ()))
        strength = float(np.clip(float(self.auto_line_align) / 100.0, 0.0, 1.0))
        current_start = float(self._start)
        self._auto_align_target = None

        if len(history) >= 2:
            median_start = float(np.median(np.asarray(history, dtype=np.float32)))
            self._auto_align_target = median_start
            max_shift = float(np.clip(2.0 + (strength * 18.0), 2.0, 20.0))
            blended_shift = float(np.clip(median_start - current_start, -max_shift, max_shift))
            blend = float(np.clip(0.12 + (strength * 0.78), 0.0, 0.9))
            self._start = int(round(current_start + (blended_shift * blend)))

        history.append(float(self._start))
        max_history = max(int(getattr(self, 'temporal_line_count', 0)), 8)
        if len(history) > max_history:
            history = history[-max_history:]
        self._temporal_state[history_key] = history

    def apply_per_line_shift(self):
        if self._start is None:
            return
        logical_line = self.temporal_key(self._number)
        if logical_line is None:
            return
        shift = float(getattr(self, 'per_line_shift_map', {}).get(logical_line + 1, 0.0))
        if abs(shift) <= 1e-9:
            return
        if self._resampled.size:
            integer_shift = int(np.trunc(shift))
            fractional_shift = float(shift - integer_shift)
            if abs(fractional_shift) > 1e-9 and self._resampled.size > 1:
                sample_positions = np.arange(self._resampled.size, dtype=np.float32) + fractional_shift
                self._resampled = np.interp(
                    sample_positions,
                    np.arange(self._resampled.size, dtype=np.float32),
                    self._resampled.astype(np.float32, copy=False),
                    left=float(self._resampled[0]),
                    right=float(self._resampled[-1]),
                ).astype(self._resampled.dtype, copy=False)
            self._start = int(round(np.clip(float(self._start) + integer_shift, 0, self._resampled.size - 1)))
        else:
            self._start = int(round(float(self._start) + int(np.trunc(shift))))

    @property
    def is_teletext(self):
        """Determine whether the VBI data in this line contains a teletext signal."""
        if self._is_teletext is None:
            self.find_start()
        return self._is_teletext

    @property
    def reject_reason(self):
        if self._is_teletext is None:
            self.find_start()
        return self._reason

    @property
    def diagnostic_quality(self):
        if self._diagnostic_quality is None:
            if self._is_teletext is None:
                self.find_start()

            signal_max = float(self._signal_max or 0.0)
            noise = float(self.noisefloor) if self._noisefloor is not None else float(self.noisefloor)
            margin = max(signal_max - noise, 0.0)
            margin_score = min((margin / 128.0) * 60.0, 60.0)

            fft_score = 0.0
            if self._fft_harmonics is not None:
                fft_score = min((float(self._fft_harmonics) / 2200.0) * 40.0, 40.0)
            elif self._is_teletext:
                fftchop = np.add.reduceat(self.fft, self.config.fftbins)
                harmonic_sum = float(np.sum(fftchop[1:-1:2]))
                self._fft_harmonics = harmonic_sum
                fft_score = min((harmonic_sum / 2200.0) * 40.0, 40.0)

            quality = margin_score + fft_score
            if not self._is_teletext:
                quality *= 0.65
            self._diagnostic_quality = int(np.clip(round(quality), 0, 100))

        return self._diagnostic_quality

    @property
    def start(self):
        """Find the offset in samples where teletext data begins in the line."""
        if self.is_teletext:
            return self._start
        else:
            return None

    def deconvolve(self, mags=range(9), rows=range(32), eight_bit=False):
        """Recover original teletext packet by pattern recognition."""
        if not self.is_teletext:
            return 'rejected'

        bytes_array = np.zeros((42,), dtype=np.uint8)

        # Note: 368 (46*8) not 360 (45*8), because pattern matchers need an
        # extra byte on either side of the input byte(s) we want to match for.
        # The framing code serves this purpose at the beginning as we never
        # need to match it. We need just an extra byte at the end.
        bits_array = self.adaptive_normalise(self.chop(0, 368))
        bits_array = self.repair_dropouts(bits_array)

        # First match just the mrag and dc for the line.
        bytes_array[:3] = self.h.match(bits_array[16:56])
        m = Mrag(bytes_array[:2])
        d = DesignationCode((1, ), bytes_array[2:3])
        if m.magazine in mags and m.row in rows:
            if m.row == 0:
                bytes_array[3:10] = self.h.match(bits_array[40:112])
                bytes_array[10:] = self.p.match(bits_array[96:368])
            elif m.row < 26:
                if eight_bit:
                    bytes_array[2:] = self.f.match(bits_array[32:368])
                else:
                    bytes_array[2:] = self.p.match(bits_array[32:368])
            elif m.row == 27:
                if d.dc < 4:
                    bytes_array[3:40] = self.h.match(bits_array[40:352])
                    bytes_array[40:] = self.f.match(bits_array[336:368])
                else:
                    bytes_array[3:] = self.f.match(bits_array[40:368]) # TODO: proper codings
            elif m.row < 30:
                bytes_array[3:] = self.f.match(bits_array[40:368]) # TODO: proper codings
            elif m.row == 30 and m.magazine == 8: # BDSP
                bytes_array[3:9] = self.h.match(bits_array[40:104]) # initial page
                if d.dc in [2, 3]:
                    bytes_array[9:22] = self.h.match(bits_array[88:208]) # 8-bit data
                else:
                    bytes_array[9:22] = self.f.match(bits_array[88:208])  # 8-bit data
                bytes_array[22:] = self.p.match(bits_array[192:368]) # status display
            else:
                bytes_array[3:] = self.f.match(bits_array[40:368]) # TODO: proper codings
            packet = Packet(bytes_array, number=self._number, original=self._original_bytes)
            packet._line_confidence = float(self.diagnostic_quality)
            return packet
        else:
            return 'filtered'

    def slice(self, mags=range(9), rows=range(32), eight_bit=False):
        """Recover original teletext packet by threshold and differential."""
        if not self.is_teletext:
            return 'rejected'

        # Note: 23 (last bit of FC), not 24 (first bit of MRAG) because
        # taking the difference reduces array length by 1. We cut the
        # extra bit off when taking the threshold.
        bits_array = self.adaptive_normalise(self.chop(23, 360))
        diff = np.diff(bits_array, n=1)
        ones = (diff > 48)
        zeros = (diff > -48)
        result = ((bits_array[1:] > 127) | ones) & zeros

        packet = Packet(np.packbits(result.reshape(-1,8)[:,::-1]), number=self._number, original=self._original_bytes)
        packet._line_confidence = float(self.diagnostic_quality)

        m = packet.mrag
        if m.magazine in mags and m.row in rows:
            return packet
        else:
            return 'filtered'


def quality_meter_stats(lines):
    analysed = 0
    teletext = 0
    qualities = []
    for line in lines:
        analysed += 1
        if line.is_teletext:
            teletext += 1
            qualities.append(int(line.diagnostic_quality))
    rejects = max(analysed - teletext, 0)
    average_quality = float(np.mean(qualities)) if qualities else 0.0
    return {
        'analysed_lines': int(analysed),
        'teletext_lines': int(teletext),
        'rejects': int(rejects),
        'average_quality': average_quality,
        'best_quality': int(max(qualities)) if qualities else 0,
        'worst_quality': int(min(qualities)) if qualities else 0,
    }


def histogram_black_level_stats(lines, config=None, bins=64):
    samples = []
    black_levels = []
    for line in lines:
        resampled = np.asarray(line.resampled, dtype=np.float32)
        if resampled.size == 0:
            continue
        samples.append(np.clip(resampled, 0, 255))
        if config is not None and getattr(config, 'start_slice', None) is not None and config.start_slice.start > 0:
            background = resampled[:config.start_slice.start]
        else:
            background = resampled[:max(min(resampled.size // 8, 64), 1)]
        if background.size:
            black_levels.append(float(np.mean(background)))

    if not samples:
        return {
            'histogram': np.zeros((int(bins),), dtype=np.float32),
            'black_level': 0.0,
            'peak_bin': 0,
        }

    merged = np.concatenate(samples)
    histogram, _ = np.histogram(merged, bins=int(bins), range=(0.0, 255.0))
    peak_bin = int(np.argmax(histogram)) if histogram.size else 0
    return {
        'histogram': histogram.astype(np.float32, copy=False),
        'black_level': float(np.mean(black_levels)) if black_levels else float(np.percentile(merged, 12)),
        'peak_bin': peak_bin,
    }


def eye_pattern_clock_stats(lines, width=192):
    width = max(int(width), 32)
    segments = []
    for line in lines:
        if not line.is_teletext or line.start is None:
            continue
        start = int(line.start)
        segment = np.asarray(line.resampled[start:start + width], dtype=np.float32)
        if segment.size != width:
            continue
        minimum = float(np.min(segment))
        maximum = float(np.max(segment))
        dynamic = maximum - minimum
        if dynamic <= 1e-6:
            continue
        normalised = ((segment - minimum) * (255.0 / dynamic)).astype(np.float32, copy=False)
        segments.append(normalised)

    if not segments:
        return None

    stack = np.stack(segments, axis=0)
    return {
        'average': np.mean(stack, axis=0).astype(np.float32, copy=False),
        'low': np.percentile(stack, 20, axis=0).astype(np.float32, copy=False),
        'high': np.percentile(stack, 80, axis=0).astype(np.float32, copy=False),
        'segment_count': int(stack.shape[0]),
        'samples_per_bit': 8,
    }

def process_lines(
    chunks,
    mode,
    config,
    force_cpu=False,
    prefer_opencl=False,
    mags=range(9),
    rows=range(32),
    tape_format='vhs',
    eight_bit=False,
    brightness=SIGNAL_CONTROL_NEUTRAL,
    sharpness=SIGNAL_CONTROL_NEUTRAL,
    gain=SIGNAL_CONTROL_NEUTRAL,
    contrast=SIGNAL_CONTROL_NEUTRAL,
    brightness_coeff=BRIGHTNESS_COEFF_DEFAULT,
    sharpness_coeff=SHARPNESS_COEFF_DEFAULT,
    gain_coeff=GAIN_COEFF_DEFAULT,
    contrast_coeff=CONTRAST_COEFF_DEFAULT,
    impulse_filter=IMPULSE_FILTER_DEFAULT,
    temporal_denoise=TEMPORAL_DENOISE_DEFAULT,
    noise_reduction=NOISE_REDUCTION_DEFAULT,
    hum_removal=HUM_REMOVAL_DEFAULT,
    auto_black_level=AUTO_BLACK_LEVEL_DEFAULT,
    head_switching_mask=HEAD_SWITCHING_MASK_DEFAULT,
    line_stabilization=LINE_STABILIZATION_DEFAULT,
    auto_gain_contrast=AUTO_GAIN_CONTRAST_DEFAULT,
    impulse_filter_coeff=IMPULSE_FILTER_COEFF_DEFAULT,
    temporal_denoise_coeff=TEMPORAL_DENOISE_COEFF_DEFAULT,
    noise_reduction_coeff=NOISE_REDUCTION_COEFF_DEFAULT,
    hum_removal_coeff=HUM_REMOVAL_COEFF_DEFAULT,
    auto_black_level_coeff=AUTO_BLACK_LEVEL_COEFF_DEFAULT,
    head_switching_mask_coeff=HEAD_SWITCHING_MASK_COEFF_DEFAULT,
    line_stabilization_coeff=LINE_STABILIZATION_COEFF_DEFAULT,
    auto_gain_contrast_coeff=AUTO_GAIN_CONTRAST_COEFF_DEFAULT,
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
    signal_controls=None,
    decoder_tuning=None,
    line_selection=None,
):
    if mode == 'slice':
        force_cpu = True
    Line.configure(
        config,
        force_cpu,
        prefer_opencl,
        tape_format,
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
        line_stabilization=line_stabilization,
        auto_gain_contrast=auto_gain_contrast,
        impulse_filter_coeff=impulse_filter_coeff,
        temporal_denoise_coeff=temporal_denoise_coeff,
        noise_reduction_coeff=noise_reduction_coeff,
        hum_removal_coeff=hum_removal_coeff,
        auto_black_level_coeff=auto_black_level_coeff,
        head_switching_mask_coeff=head_switching_mask_coeff,
        line_stabilization_coeff=line_stabilization_coeff,
        auto_gain_contrast_coeff=auto_gain_contrast_coeff,
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
    current_controls = (
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
        line_stabilization,
        auto_gain_contrast,
        impulse_filter_coeff,
        temporal_denoise_coeff,
        noise_reduction_coeff,
        hum_removal_coeff,
        auto_black_level_coeff,
        head_switching_mask_coeff,
        line_stabilization_coeff,
        auto_gain_contrast_coeff,
    )
    lines_per_frame = len(config.field_range) * 2
    current_decoder_tuning = (
        tape_format,
        int(config.extra_roll),
        tuple(int(value) for value in config.line_start_range),
        int(quality_threshold),
        float(quality_threshold_coeff),
        int(clock_lock),
        float(clock_lock_coeff),
        int(start_lock),
        float(start_lock_coeff),
        int(adaptive_threshold),
        float(adaptive_threshold_coeff),
        int(dropout_repair),
        float(dropout_repair_coeff),
        int(wow_flutter_compensation),
        float(wow_flutter_compensation_coeff),
        int(auto_line_align),
        serialise_per_line_shift_map(per_line_shift, maximum_line=max(lines_per_frame, 32)),
        tuple(sorted((int(line), tuple(values)) for line, values in dict(line_control_overrides or {}).items())),
        tuple(sorted((int(line), tuple(sorted(dict(values).items()))) for line, values in dict(line_decoder_overrides or {}).items())),
    )
    for number, chunk in chunks:
        try:
            if line_selection is not None:
                selected_lines = line_selection()
                if ((number % lines_per_frame) + 1) not in selected_lines:
                    continue
            if decoder_tuning is not None:
                next_decoder_tuning = decoder_tuning()
                next_tuning = (
                    next_decoder_tuning['tape_format'],
                    int(next_decoder_tuning['extra_roll']),
                    tuple(int(value) for value in next_decoder_tuning['line_start_range']),
                    int(next_decoder_tuning['quality_threshold']),
                    float(next_decoder_tuning['quality_threshold_coeff']),
                    int(next_decoder_tuning.get('clock_lock', CLOCK_LOCK_DEFAULT)),
                    float(next_decoder_tuning.get('clock_lock_coeff', CLOCK_LOCK_COEFF_DEFAULT)),
                    int(next_decoder_tuning.get('start_lock', START_LOCK_DEFAULT)),
                    float(next_decoder_tuning.get('start_lock_coeff', START_LOCK_COEFF_DEFAULT)),
                    int(next_decoder_tuning['adaptive_threshold']),
                    float(next_decoder_tuning.get('adaptive_threshold_coeff', ADAPTIVE_THRESHOLD_COEFF_DEFAULT)),
                    int(next_decoder_tuning.get('dropout_repair', DROPOUT_REPAIR_DEFAULT)),
                    float(next_decoder_tuning.get('dropout_repair_coeff', DROPOUT_REPAIR_COEFF_DEFAULT)),
                    int(next_decoder_tuning.get('wow_flutter_compensation', WOW_FLUTTER_COMPENSATION_DEFAULT)),
                    float(next_decoder_tuning.get('wow_flutter_compensation_coeff', WOW_FLUTTER_COMPENSATION_COEFF_DEFAULT)),
                    int(next_decoder_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
                    serialise_per_line_shift_map(
                        next_decoder_tuning.get('per_line_shift', {}),
                        maximum_line=max(lines_per_frame, 32),
                    ),
                    tuple(sorted(
                        (int(line), tuple(values))
                        for line, values in dict(next_decoder_tuning.get('line_control_overrides', {})).items()
                    )),
                    tuple(sorted(
                        (int(line), tuple(sorted(dict(values).items())))
                        for line, values in dict(next_decoder_tuning.get('line_decoder_overrides', {})).items()
                    )),
                )
                if next_tuning != current_decoder_tuning:
                    current_decoder_tuning = next_tuning
                    config = config.retuned(
                        extra_roll=current_decoder_tuning[1],
                        line_start_range=current_decoder_tuning[2],
                    )
                    Line.configure(
                        config,
                        force_cpu,
                        prefer_opencl,
                        current_decoder_tuning[0],
                        brightness=current_controls[0],
                        sharpness=current_controls[1],
                        gain=current_controls[2],
                        contrast=current_controls[3],
                        brightness_coeff=current_controls[4],
                        sharpness_coeff=current_controls[5],
                        gain_coeff=current_controls[6],
                        contrast_coeff=current_controls[7],
                        impulse_filter=current_controls[8],
                        temporal_denoise=current_controls[9],
                        noise_reduction=current_controls[10],
                        hum_removal=current_controls[11],
                        auto_black_level=current_controls[12],
                        head_switching_mask=current_controls[13],
                        line_stabilization=current_controls[14],
                        auto_gain_contrast=current_controls[15],
                        impulse_filter_coeff=current_controls[16],
                        temporal_denoise_coeff=current_controls[17],
                        noise_reduction_coeff=current_controls[18],
                        hum_removal_coeff=current_controls[19],
                        auto_black_level_coeff=current_controls[20],
                        head_switching_mask_coeff=current_controls[21],
                        line_stabilization_coeff=current_controls[22],
                        auto_gain_contrast_coeff=current_controls[23],
                        quality_threshold=current_decoder_tuning[3],
                        quality_threshold_coeff=current_decoder_tuning[4],
                        clock_lock=current_decoder_tuning[5],
                        clock_lock_coeff=current_decoder_tuning[6],
                        start_lock=current_decoder_tuning[7],
                        start_lock_coeff=current_decoder_tuning[8],
                        adaptive_threshold=current_decoder_tuning[9],
                        adaptive_threshold_coeff=current_decoder_tuning[10],
                        dropout_repair=current_decoder_tuning[11],
                        dropout_repair_coeff=current_decoder_tuning[12],
                        wow_flutter_compensation=current_decoder_tuning[13],
                        wow_flutter_compensation_coeff=current_decoder_tuning[14],
                        auto_line_align=current_decoder_tuning[15],
                        per_line_shift=dict(current_decoder_tuning[16]),
                        line_control_overrides=dict(current_decoder_tuning[17]),
                        line_decoder_overrides={
                            int(line): dict(items)
                            for line, items in current_decoder_tuning[18]
                        },
                    )
                    lines_per_frame = len(config.field_range) * 2
            if signal_controls is not None:
                next_controls = tuple(signal_controls())
                if next_controls != current_controls:
                    current_controls = next_controls
                    Line.set_signal_controls(
                        brightness=current_controls[0],
                        sharpness=current_controls[1],
                        gain=current_controls[2],
                        contrast=current_controls[3],
                        brightness_coeff=current_controls[4],
                        sharpness_coeff=current_controls[5],
                        gain_coeff=current_controls[6],
                        contrast_coeff=current_controls[7],
                        impulse_filter=current_controls[8],
                        temporal_denoise=current_controls[9],
                        noise_reduction=current_controls[10],
                        hum_removal=current_controls[11],
                        auto_black_level=current_controls[12],
                        head_switching_mask=current_controls[13],
                        line_stabilization=current_controls[14],
                        auto_gain_contrast=current_controls[15],
                        impulse_filter_coeff=current_controls[16],
                        temporal_denoise_coeff=current_controls[17],
                        noise_reduction_coeff=current_controls[18],
                        hum_removal_coeff=current_controls[19],
                        auto_black_level_coeff=current_controls[20],
                        head_switching_mask_coeff=current_controls[21],
                        line_stabilization_coeff=current_controls[22],
                        auto_gain_contrast_coeff=current_controls[23],
                    )
            yield getattr(Line(chunk, number), mode)(mags, rows, eight_bit)
        except Exception:
            sys.stderr.write(str(number) + '\n')
            raise
