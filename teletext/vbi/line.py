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


def signal_controls_active(brightness=SIGNAL_CONTROL_NEUTRAL, sharpness=SIGNAL_CONTROL_NEUTRAL, gain=SIGNAL_CONTROL_NEUTRAL, contrast=SIGNAL_CONTROL_NEUTRAL):
    return any(value != SIGNAL_CONTROL_NEUTRAL for value in (brightness, sharpness, gain, contrast))


def _control_factor(value, coeff):
    return 1.0 + ((float(value) - SIGNAL_CONTROL_NEUTRAL) / SIGNAL_CONTROL_NEUTRAL) * float(coeff)


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
):
    adjusted = np.asarray(samples, dtype=np.float32).copy()
    if adjusted.size == 0 or not signal_controls_active(brightness, sharpness, gain, contrast):
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
    preserve_tail=0,
):
    if not signal_controls_active(brightness, sharpness, gain, contrast):
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
    preserve_tail=0,
):
    if not signal_controls_active(brightness, sharpness, gain, contrast):
        return data

    frame_lines = config.field_lines * 2
    line_bytes = config.line_bytes
    output = bytearray()

    for line_number in range(frame_lines):
        start = line_number * line_bytes
        end = start + line_bytes
        if end > len(data):
            output.extend(data[start:])
            break
        line_tail = preserve_tail if preserve_tail and line_number == (frame_lines - 1) else 0
        output.extend(process_line_bytes(
            data[start:end],
            config,
            brightness=brightness,
            sharpness=sharpness,
            gain=gain,
            contrast=contrast,
            brightness_coeff=brightness_coeff,
            sharpness_coeff=sharpness_coeff,
            gain_coeff=gain_coeff,
            contrast_coeff=contrast_coeff,
            preserve_tail=line_tail,
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
        )
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
    ):
        cls.brightness = brightness
        cls.sharpness = sharpness
        cls.gain = gain
        cls.contrast = contrast
        cls.brightness_coeff = brightness_coeff
        cls.sharpness_coeff = sharpness_coeff
        cls.gain_coeff = gain_coeff
        cls.contrast_coeff = contrast_coeff

    def __init__(self, data, number=None):
        if not self.configured:
            self.configure(Config())

        self._number = number
        self._original = samples_from_bytes(data, self.config.dtype)
        self._original = apply_signal_controls(
            self._original,
            brightness=self.brightness,
            sharpness=self.sharpness,
            gain=self.gain,
            contrast=self.contrast,
            brightness_coeff=self.brightness_coeff,
            sharpness_coeff=self.sharpness_coeff,
            gain_coeff=self.gain_coeff,
            contrast_coeff=self.contrast_coeff,
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
        self._gstart = None
        self._is_teletext = None
        self._start = None
        self._reason = None

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
        # First try to detect by comparing pre-start noise floor to post-start levels.
        # Store self._gstart so that self.start can re-use it.
        self._gstart = gauss(self._resampled[self.config.start_slice], self.config.gauss)
        smax = np.max(self._gstart)
        if smax < 64:
            self._is_teletext = False
            self._reason = f'Signal max is {smax}'
        elif self.noisefloor > 80:
            self._is_teletext = False
            self._reason = f'Noise is {self.noisefloor}'
        elif smax < (self.noisefloor + 16):
            # There is no interesting signal in the start_slice.
            self._is_teletext = False
            self._reason = f'Noise is higher than signal {smax} {self.noisefloor}'
        else:
            # There is some kind of signal in the line. Check if
            # it is teletext by looking for harmonics of teletext
            # symbol rate.
            fftchop = np.add.reduceat(self.fft, self.config.fftbins)
            self._is_teletext = np.sum(fftchop[1:-1:2]) > 1000
        if not self._is_teletext:
            return

        # Find the steepest part of the line within start_slice.
        # This gives a rough location of the start.
        self._start = np.argmax(np.gradient(np.maximum.accumulate(self._gstart))) + self.config.start_slice.start
        # Now find the extra roll needed to lock in the clock run-in and framing code.
        confidence = []

        for roll in range(max(-30, 8-self._start), 20):
            self.roll = roll
            # 15:20 is the last bit of CRI and first 4 bits of FC - 01110.
            # This is the most distinctive part of the CRI/FC to look for.
            c = self.chop(15, 21)
            confidence.append((c[1] + c[2] + c[3] - c[0] - c[4] - c[5], roll))
            #confidence.append((np.sum(self.chop(15, 20) * self.config.crifc[15:20]), roll))

        self._start += max(confidence)[1]
        self.roll = 0

        # Use the observed CRIFC to lock to the framing code
        confidence = []
        for roll in range(-4, 4):
            self.roll = roll
            x = np.gradient(self.fchop(8, 24))
            c = np.sum(np.square(x - self.config.observed_crifc_gradient))
            confidence.append((c, roll))

        self._start += min(confidence)[1]
        self.roll = 0

        self._start += self.config.extra_roll

    @property
    def is_teletext(self):
        """Determine whether the VBI data in this line contains a teletext signal."""
        if self._is_teletext is None:
            self.find_start()
        return self._is_teletext

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
        bits_array = normalise(self.chop(0, 368))

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
            return Packet(bytes_array, number=self._number, original=self._original_bytes)
        else:
            return 'filtered'

    def slice(self, mags=range(9), rows=range(32), eight_bit=False):
        """Recover original teletext packet by threshold and differential."""
        if not self.is_teletext:
            return 'rejected'

        # Note: 23 (last bit of FC), not 24 (first bit of MRAG) because
        # taking the difference reduces array length by 1. We cut the
        # extra bit off when taking the threshold.
        bits_array = normalise(self.chop(23, 360))
        diff = np.diff(bits_array, n=1)
        ones = (diff > 48)
        zeros = (diff > -48)
        result = ((bits_array[1:] > 127) | ones) & zeros

        packet = Packet(np.packbits(result.reshape(-1,8)[:,::-1]), number=self._number, original=self._original_bytes)

        m = packet.mrag
        if m.magazine in mags and m.row in rows:
            return packet
        else:
            return 'filtered'

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
    )
    current_decoder_tuning = (
        tape_format,
        int(config.extra_roll),
        tuple(int(value) for value in config.line_start_range),
    )
    lines_per_frame = len(config.field_range) * 2
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
                    )
            yield getattr(Line(chunk, number), mode)(mags, rows, eight_bit)
        except Exception:
            sys.stderr.write(str(number) + '\n')
            raise
