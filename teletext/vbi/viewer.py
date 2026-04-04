import time
import sys

import numpy as np
from itertools import islice

from teletext.vbi.line import (
    AUTO_LINE_ALIGN_DEFAULT,
    eye_pattern_clock_stats,
    histogram_black_level_stats,
    normalise_per_line_shift_map,
    quality_meter_stats,
)

from OpenGL.GLUT import *
from OpenGL.GL import *


class VBIViewer(object):

    def __init__(self, lines, config, name = "VBI Viewer", width=800, height=512, nlines=None, tint=True, show_grid=True, show_slices=False, pause=False, show_line_numbers=True, signal_controls=None, decoder_tuning=None, tape_format='vhs', line_selection=None, external_playback=False):
        self.config = config
        self.show_grid = show_grid
        self.tint = tint
        self.pause = pause
        self.single_step = False
        self.name = name
        self.show_line_numbers = show_line_numbers
        self.label_margin = 56
        self.width = width
        self.height = height
        self.signal_controls = signal_controls
        self.decoder_tuning = decoder_tuning
        self.tape_format = tape_format
        self.line_selection = line_selection
        self.external_playback = external_playback
        self._current_signal_controls = None
        self._current_decoder_tuning = None
        self._show_quality = False
        self._show_rejects = False
        self._show_start_clock = False
        self._show_clock_visuals = False
        self._show_alignment_visuals = False
        self._show_quality_meter = False
        self._show_histogram_graph = False
        self._show_eye_pattern = False
        self._closing = False

        self.line_attr = 'resampled'

        if nlines is None:
            self.frame_line_count = len(self.config.field_range) * 2
            self.nlines = self.frame_line_count
        else:
            self.nlines = nlines
            self.frame_line_count = nlines

        self.lines_src = lines
        self.lines = list(islice(self.lines_src, 0, self.nlines))
        self._apply_live_decoder_tuning(rebuild=True)
        self._apply_live_signal_controls(rebuild=True)

        glutInit(sys.argv)
        try:
            glutSetOption(GLUT_ACTION_ON_WINDOW_CLOSE, GLUT_ACTION_GLUTMAINLOOP_RETURNS)
        except Exception:
            pass
        glutInitDisplayMode(GLUT_SINGLE | GLUT_RGB)
        glutInitWindowSize(width,height)
        self.window = glutCreateWindow(name)
        self.set_title()

        glutDisplayFunc(self.display)
        glutReshapeFunc(self.reshape)
        glutKeyboardFunc(self.keyboard)
        glutMouseFunc(self.mouse)
        try:
            glutCloseFunc(self.close_viewer)
        except Exception:
            pass

        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, glGenTextures(1))
        glPixelStorei(GL_UNPACK_ALIGNMENT,1)

        glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexEnvf(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glClearColor(0.0, 0.0, 0.0, 1.0)

        self.set_plot_projection()

        glutMainLoop()

    def reshape(self, width, height):
        self.width = width
        self.height = height
        glViewport(0, 0, width, height)

    @property
    def plot_width(self):
        if self.show_line_numbers:
            return max(self.width - self.overlay_margin, 1)
        return max(self.width, 1)

    @property
    def overlay_margin(self):
        if not self.show_line_numbers:
            return 0
        if self._show_quality:
            return max(self.label_margin, 86)
        return self.label_margin

    def set_plot_projection(self):
        glViewport(0, 0, self.plot_width, self.height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.config.resample_size, 0, self.nlines, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    def set_overlay_projection(self):
        glViewport(0, 0, self.width, self.height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.width, 0, self.height, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    def keyboard(self, key, x, y):
        if key == b'g':
            self.show_grid ^= True
        elif key == b'c':
            self.tint ^= True
        elif key == b'p' and not self.external_playback:
            self.pause ^= True
        elif key == b'n' and not self.external_playback:
            self.single_step = True
        elif key == b'r':
            self.dumpline(x, y, teletext=False)
        elif key == b't':
            self.dumpline(x, y, teletext=True)
        elif key == b'R':
            self.dumpall(teletext=False)
        elif key == b'T':
            self.dumpall(teletext=True)
        elif key == b'1':
            self.line_attr = 'resampled'
        elif key == b'2':
            self.line_attr = 'fft'
        elif key == b'3':
            self.line_attr = 'rolled'
        elif key == b'4':
            self.line_attr = 'gradient'
        elif key == b'l':
            self.show_line_numbers ^= True
        elif key == b'q':
            self.close_viewer()
        self.set_title()

    def close_viewer(self, *args):
        if self._closing:
            return
        self._closing = True
        try:
            glutLeaveMainLoop()
        except Exception:
            try:
                glutDestroyWindow(self.window)
            except Exception:
                pass

    def mouse(self, button, state, x, y):
        if state == GLUT_DOWN:
            l = self.lines[self.nlines * y//self.height]
            if button == 3:
                l.roll += 1
            elif button == 4:
                l.roll -= 1
            if l.is_teletext:
                print(l.deconvolve().debug.decode('utf8')[:-1], 'er:', l.roll, l._reason)
            else:
                print(l._reason)
            a = np.frombuffer(l._original_bytes, dtype=np.uint8)
            d = np.diff(a.astype(np.int16))
            md = np.mean(np.abs(d))
            steps = np.floor(np.linspace(0, 2048 - 5, num=11)).astype(np.uint32)[[1, 5, 9]]
            s = np.sort(a)
            print(md, s[steps])
            sys.stdout.flush()

    def dumpline(self, x, y, teletext):
        if teletext:
            print('Writing to teletext.vbi')
            fn = 'teletext.vbi'
        else:
            print('Writing to reject.vbi')
            fn = 'reject.vbi'
        l = self.lines[self.nlines * y // self.height]
        with open(fn, 'ab') as f:
            f.write(l._original_bytes)

    def dumpall(self, teletext):
        if teletext:
            print('Writing all to teletext.vbi')
            fn = 'teletext.vbi'
        else:
            print('Writing all to reject.vbi')
            fn = 'reject.vbi'
        with open(fn, 'ab') as f:
            for l in self.lines:
                f.write(l._original_bytes)

    def set_title(self):
        glutSetWindowTitle(f'{self.name} - {self.line_attr}{" (paused)" if self.pause else ""}')

    def draw_slice(self, slice, r, g, b, a=1.0):
        glColor4f(r, g, b, a)
        glBegin(GL_LINES)
        glVertex2f(slice.start, 0)
        glVertex2f(slice.start, self.nlines)
        glVertex2f(slice.stop, 0)
        glVertex2f(slice.stop, self.nlines)
        glEnd()

    def draw_h_grid(self, r, g, b, a=1.0):
        glColor4f(r, g, b, a)
        glBegin(GL_LINES)
        for x in range(self.nlines):
            glVertex2f(0, x)
            glVertex2f(self.config.resample_size, x)
        glEnd()

    def draw_bits(self, r, g, b, a=1.0):
        glColor4f(r, g, b, a)
        glBegin(GL_LINES)
        for x in range(0, 368,8):
            glVertex2f((x*8)+90, 0)
            glVertex2f((x*8)+90, self.nlines)
        glEnd()

    def draw_freq_bins(self, n, r, g, b, a=1.0):
        glColor4f(r, g, b, a)
        glBegin(GL_LINES)
        for x in self.config.fftbins:
            glVertex2f(self.config.resample_size*x/256, 0)
            glVertex2f(self.config.resample_size*x/256, self.nlines)
        glEnd()

    def line_number(self, line):
        if line._number is None:
            return None
        return (line._number % self.frame_line_count) + 1

    def selected_lines(self):
        if self.line_selection is None:
            return None
        if callable(self.line_selection):
            return self.line_selection()
        return self.line_selection

    def line_enabled(self, line, selected_lines=None):
        if selected_lines is None:
            selected_lines = self.selected_lines()
        if selected_lines is None:
            return True
        return self.line_number(line) in selected_lines

    def text_width(self, font, text):
        return sum(glutBitmapWidth(font, ord(char)) for char in text)

    def draw_text(self, x, y, text, font=GLUT_BITMAP_8_BY_13):
        glRasterPos2f(x, y)
        for char in text:
            glutBitmapCharacter(font, ord(char))

    def draw_line_numbers(self):
        if not self.show_line_numbers and not self._show_quality:
            return

        font = GLUT_BITMAP_8_BY_13
        margin_left = self.plot_width
        margin_right = self.width
        line_height = self.height / self.nlines

        glColor4f(0.08, 0.03, 0.03, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(margin_left, 0)
        glVertex2f(margin_left, self.height)
        glVertex2f(margin_right, self.height)
        glVertex2f(margin_right, 0)
        glEnd()

        glColor4f(1.0, 1.0, 1.0, 0.35)
        glBegin(GL_LINES)
        glVertex2f(margin_left, 0)
        glVertex2f(margin_left, self.height)
        glEnd()

        glColor4f(0.95, 0.95, 0.95, 0.85)
        for index, line in enumerate(self.lines):
            number = self.line_number(line)
            if number is None and not self._show_quality:
                continue
            label = ''
            if number is not None and self.show_line_numbers:
                label = str(number)
            if self._show_quality:
                quality_label = f'Q{line.diagnostic_quality:02d}'
                label = f'{label} {quality_label}'.strip()
            if not label:
                continue
            x = margin_right - self.text_width(font, label) - 6
            y = self.height - ((index + 0.75) * line_height)
            self.draw_text(x, y, label, font=font)

    def draw_reject_reasons(self):
        if not self._show_rejects:
            return

        font = GLUT_BITMAP_8_BY_13
        line_height = self.height / self.nlines
        selected_lines = self.selected_lines()

        for index, line in enumerate(self.lines):
            if not self.line_enabled(line, selected_lines=selected_lines):
                continue
            if line.is_teletext:
                continue
            reason = (line.reject_reason or 'Rejected').strip()
            if not reason:
                continue
            label = reason[:30]
            x = 6
            y = self.height - ((index + 0.75) * line_height)
            glColor4f(1.0, 0.72, 0.72, 0.9)
            self.draw_text(x, y, label, font=font)

    def visible_lines(self):
        selected_lines = self.selected_lines()
        return [line for line in self.lines if self.line_enabled(line, selected_lines=selected_lines)]

    def draw_quality_meter(self):
        if not self._show_quality_meter:
            return
        stats = quality_meter_stats(self.visible_lines())
        box_width = 214
        box_height = 34
        x0 = 8
        y0 = self.height - box_height - 8
        meter_width = box_width - 16
        average = max(min(float(stats['average_quality']), 100.0), 0.0)

        glColor4f(0.05, 0.05, 0.06, 0.86)
        glBegin(GL_QUADS)
        glVertex2f(x0, y0)
        glVertex2f(x0, y0 + box_height)
        glVertex2f(x0 + box_width, y0 + box_height)
        glVertex2f(x0 + box_width, y0)
        glEnd()

        glColor4f(0.12, 0.12, 0.13, 1.0)
        glBegin(GL_QUADS)
        glVertex2f(x0 + 8, y0 + 8)
        glVertex2f(x0 + 8, y0 + 18)
        glVertex2f(x0 + 8 + meter_width, y0 + 18)
        glVertex2f(x0 + 8 + meter_width, y0 + 8)
        glEnd()

        fill_width = meter_width * (average / 100.0)
        if fill_width > 0:
            glColor4f(0.25, 0.78, 0.42, 0.95)
            glBegin(GL_QUADS)
            glVertex2f(x0 + 8, y0 + 8)
            glVertex2f(x0 + 8, y0 + 18)
            glVertex2f(x0 + 8 + fill_width, y0 + 18)
            glVertex2f(x0 + 8 + fill_width, y0 + 8)
            glEnd()

        glColor4f(0.94, 0.94, 0.95, 0.95)
        self.draw_text(x0 + 8, y0 + 28, f"Q {average:04.1f} TT {stats['teletext_lines']}/{stats['analysed_lines']} R {stats['rejects']}")

    def draw_histogram_graph(self):
        if not self._show_histogram_graph:
            return
        stats = histogram_black_level_stats(self.visible_lines(), config=self.config, bins=48)
        histogram = stats['histogram']
        if not histogram.size:
            return
        box_width = min(220, max(self.plot_width - 16, 0))
        box_height = 78
        x0 = 8
        y0 = 8
        plot_left = x0 + 6
        plot_bottom = y0 + 8
        plot_width = box_width - 12
        plot_height = box_height - 20

        glColor4f(0.05, 0.05, 0.06, 0.82)
        glBegin(GL_QUADS)
        glVertex2f(x0, y0)
        glVertex2f(x0, y0 + box_height)
        glVertex2f(x0 + box_width, y0 + box_height)
        glVertex2f(x0 + box_width, y0)
        glEnd()

        maximum = float(np.max(histogram))
        if maximum > 0 and plot_width > 2:
            glColor4f(0.40, 0.80, 0.98, 0.95)
            glBegin(GL_LINE_STRIP)
            for index, value in enumerate(histogram):
                x = plot_left + (plot_width * (index / max(len(histogram) - 1, 1)))
                y = plot_bottom + (plot_height * (float(value) / maximum))
                glVertex2f(x, y)
            glEnd()

        black_x = plot_left + (plot_width * (float(stats['black_level']) / 255.0))
        glColor4f(1.0, 0.82, 0.25, 0.95)
        glBegin(GL_LINES)
        glVertex2f(black_x, plot_bottom)
        glVertex2f(black_x, plot_bottom + plot_height)
        glEnd()

        glColor4f(0.95, 0.95, 0.96, 0.92)
        self.draw_text(x0 + 8, y0 + box_height - 8, f"Hist BL {stats['black_level']:.1f}")

    def draw_eye_pattern(self):
        if not self._show_eye_pattern:
            return
        stats = eye_pattern_clock_stats(self.visible_lines(), width=24 * 8)
        box_width = min(240, max(self.plot_width - 16, 0))
        box_height = 78
        x0 = max(self.plot_width - box_width - 8, 8)
        y0 = 8
        plot_left = x0 + 6
        plot_bottom = y0 + 8
        plot_width = box_width - 12
        plot_height = box_height - 20

        glColor4f(0.05, 0.05, 0.06, 0.82)
        glBegin(GL_QUADS)
        glVertex2f(x0, y0)
        glVertex2f(x0, y0 + box_height)
        glVertex2f(x0 + box_width, y0 + box_height)
        glVertex2f(x0 + box_width, y0)
        glEnd()

        if stats is None:
            glColor4f(0.82, 0.84, 0.88, 0.92)
            self.draw_text(x0 + 8, y0 + box_height - 8, 'Eye No teletext')
            return

        average = stats['average']
        low = stats['low']
        high = stats['high']
        samples_per_bit = max(int(stats.get('samples_per_bit', 8)), 1)

        glColor4f(0.25, 0.25, 0.28, 0.85)
        glBegin(GL_LINES)
        for bit in range(0, len(average), samples_per_bit):
            x = plot_left + (plot_width * (bit / max(len(average) - 1, 1)))
            glVertex2f(x, plot_bottom)
            glVertex2f(x, plot_bottom + plot_height)
        glEnd()

        glColor4f(0.35, 0.68, 1.0, 0.35)
        glBegin(GL_LINES)
        for index in range(len(average)):
            x = plot_left + (plot_width * (index / max(len(average) - 1, 1)))
            y0_line = plot_bottom + (plot_height * (float(low[index]) / 255.0))
            y1_line = plot_bottom + (plot_height * (float(high[index]) / 255.0))
            glVertex2f(x, y0_line)
            glVertex2f(x, y1_line)
        glEnd()

        glColor4f(1.0, 0.42, 0.42, 0.95)
        glBegin(GL_LINE_STRIP)
        for index, value in enumerate(average):
            x = plot_left + (plot_width * (index / max(len(average) - 1, 1)))
            y = plot_bottom + (plot_height * (float(value) / 255.0))
            glVertex2f(x, y)
        glEnd()

        glColor4f(0.95, 0.95, 0.96, 0.92)
        self.draw_text(x0 + 8, y0 + box_height - 8, f"Eye {stats['segment_count']} lines")

    def marker_positions(self, line):
        if not line.is_teletext or line.start is None:
            return None
        if self.line_attr == 'resampled':
            start = int(line.start)
            return start, start + (8 * 8), start + (24 * 8)
        if self.line_attr == 'rolled':
            start = int(90 - line.roll)
            return start, start + (8 * 8), start + (24 * 8)
        return None

    def alignment_positions(self, line):
        if not line.is_teletext or line.start is None:
            return None
        return {
            'current': float(line.start),
            'clock_locked': getattr(line, '_clock_locked_start', None),
            'pre_alignment': getattr(line, '_pre_alignment_start', None),
            'post_alignment': getattr(line, '_post_alignment_start', None),
            'target': getattr(line, '_auto_align_target', None),
        }

    def draw_start_clock_markers(self):
        if not self._show_start_clock:
            return

        selected_lines = self.selected_lines()
        glBegin(GL_LINES)
        for index, line in enumerate(self.lines):
            if not self.line_enabled(line, selected_lines=selected_lines):
                continue
            positions = self.marker_positions(line)
            if positions is None:
                continue
            y0 = self.nlines - index - 1
            y1 = y0 + 1

            start_x, clock_start_x, clock_end_x = positions
            glColor4f(1.0, 0.9, 0.2, 0.85)
            glVertex2f(start_x, y0)
            glVertex2f(start_x, y1)
            glColor4f(0.2, 0.9, 1.0, 0.75)
            glVertex2f(clock_start_x, y0)
            glVertex2f(clock_start_x, y1)
            glVertex2f(clock_end_x, y0)
            glVertex2f(clock_end_x, y1)
        glEnd()

    def draw_clock_visuals(self):
        if not self._show_clock_visuals:
            return

        selected_lines = self.selected_lines()
        glColor4f(0.08, 0.55, 0.95, 0.22)
        glBegin(GL_QUADS)
        for index, line in enumerate(self.lines):
            if not self.line_enabled(line, selected_lines=selected_lines):
                continue
            positions = self.marker_positions(line)
            if positions is None:
                continue
            _, clock_start_x, clock_end_x = positions
            y0 = self.nlines - index - 1
            y1 = y0 + 1
            glVertex2f(clock_start_x, y0)
            glVertex2f(clock_start_x, y1)
            glVertex2f(clock_end_x, y1)
            glVertex2f(clock_end_x, y0)
        glEnd()

        glBegin(GL_LINES)
        for index, line in enumerate(self.lines):
            if not self.line_enabled(line, selected_lines=selected_lines):
                continue
            positions = self.marker_positions(line)
            if positions is None:
                continue
            start_x, clock_start_x, clock_end_x = positions
            y0 = self.nlines - index - 1
            y1 = y0 + 1
            clock_mid_x = (clock_start_x + clock_end_x) / 2.0

            glColor4f(1.0, 0.88, 0.22, 0.8)
            glVertex2f(start_x, y0)
            glVertex2f(start_x, y1)

            glColor4f(0.15, 0.95, 1.0, 0.9)
            glVertex2f(clock_mid_x, y0)
            glVertex2f(clock_mid_x, y1)
        glEnd()

    def draw_alignment_visuals(self):
        if not self._show_alignment_visuals:
            return

        selected_lines = self.selected_lines()
        glBegin(GL_QUADS)
        for index, line in enumerate(self.lines):
            if not self.line_enabled(line, selected_lines=selected_lines):
                continue
            positions = self.alignment_positions(line)
            if positions is None:
                continue
            current = positions.get('current')
            target = positions.get('target')
            if current is None or target is None:
                continue
            x0 = min(float(current), float(target))
            x1 = max(float(current), float(target))
            if abs(x1 - x0) < 1e-6:
                continue
            y0 = self.nlines - index - 1
            y1 = y0 + 1
            glColor4f(0.9, 0.2, 0.8, 0.18)
            glVertex2f(x0, y0)
            glVertex2f(x0, y1)
            glVertex2f(x1, y1)
            glVertex2f(x1, y0)
        glEnd()

        glBegin(GL_LINES)
        for index, line in enumerate(self.lines):
            if not self.line_enabled(line, selected_lines=selected_lines):
                continue
            positions = self.alignment_positions(line)
            if positions is None:
                continue
            y0 = self.nlines - index - 1
            y1 = y0 + 1
            pre_alignment = positions.get('pre_alignment')
            target = positions.get('target')
            current = positions.get('current')

            if pre_alignment is not None:
                glColor4f(0.82, 0.82, 0.86, 0.55)
                glVertex2f(float(pre_alignment), y0)
                glVertex2f(float(pre_alignment), y1)
            if target is not None:
                glColor4f(0.95, 0.15, 0.85, 0.95)
                glVertex2f(float(target), y0)
                glVertex2f(float(target), y1)
            if current is not None:
                glColor4f(0.3, 1.0, 0.45, 0.92)
                glVertex2f(float(current), y0)
                glVertex2f(float(current), y1)
        glEnd()

    def draw_lines(self):
        selected_lines = self.selected_lines()

        glEnable(GL_TEXTURE_2D)
        for n,l in enumerate(self.lines[::-1]):
            array = getattr(l, self.line_attr)
            if not self.line_enabled(l, selected_lines=selected_lines):
                array = np.zeros_like(array)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, array.size, 1, 0, GL_LUMINANCE, GL_UNSIGNED_BYTE, np.clip(array, 0, 255).astype(np.uint8).tostring())
            if self.tint:
                if l.is_teletext:
                    glColor4f(0.5, 1.0, 0.7, 1.0)
                else:
                    glColor4f(1.0, 0.5, 0.5, 1.0)
            else:
                glColor4f(1.0, 1.0, 1.0, 1.0)

            glBegin(GL_QUADS)

            glTexCoord2f(0, 1)
            glVertex2f(0, n)

            glTexCoord2f(0, 0)
            glVertex2f(0, (n+1))

            glTexCoord2f(1, 0)
            glVertex2f(self.config.resample_size, (n+1))

            glTexCoord2f(1, 1)
            glVertex2f(self.config.resample_size, n)

            glEnd()

        glDisable(GL_TEXTURE_2D)

    def _apply_live_signal_controls(self, rebuild=False):
        if self.signal_controls is None:
            return

        from teletext.vbi.line import Line

        raw_controls = tuple(self.signal_controls())
        controls = (
            int(raw_controls[0]),
            int(raw_controls[1]),
            int(raw_controls[2]),
            int(raw_controls[3]),
            float(raw_controls[4]),
            float(raw_controls[5]),
            float(raw_controls[6]),
            float(raw_controls[7]),
            int(raw_controls[8]),
            int(raw_controls[9]),
            int(raw_controls[10]),
            int(raw_controls[11]),
            int(raw_controls[12]),
            int(raw_controls[13]),
            int(raw_controls[14]),
            int(raw_controls[15]),
            float(raw_controls[16]),
            float(raw_controls[17]),
            float(raw_controls[18]),
            float(raw_controls[19]),
            float(raw_controls[20]),
            float(raw_controls[21]),
            float(raw_controls[22]),
            float(raw_controls[23]),
        )
        if not rebuild and controls == self._current_signal_controls:
            return

        self._current_signal_controls = controls
        Line.set_signal_controls(
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
        )

        if self.lines:
            self.lines = [Line(line._original_bytes, line._number) for line in self.lines]

    def _apply_live_decoder_tuning(self, rebuild=False):
        if self.decoder_tuning is None:
            return

        from teletext.vbi.line import Line

        next_tuning = self.decoder_tuning() if callable(self.decoder_tuning) else self.decoder_tuning
        tuning = (
            next_tuning['tape_format'],
            int(next_tuning['extra_roll']),
            tuple(int(value) for value in next_tuning['line_start_range']),
            int(next_tuning['quality_threshold']),
            float(next_tuning.get('quality_threshold_coeff', 1.0)),
            int(next_tuning.get('clock_lock', 50)),
            float(next_tuning.get('clock_lock_coeff', 1.0)),
            int(next_tuning.get('start_lock', 50)),
            float(next_tuning.get('start_lock_coeff', 1.0)),
            int(next_tuning.get('adaptive_threshold', 0)),
            float(next_tuning.get('adaptive_threshold_coeff', 1.0)),
            int(next_tuning.get('dropout_repair', 0)),
            float(next_tuning.get('dropout_repair_coeff', 1.0)),
            int(next_tuning.get('wow_flutter_compensation', 0)),
            float(next_tuning.get('wow_flutter_compensation_coeff', 1.0)),
            int(next_tuning.get('auto_line_align', AUTO_LINE_ALIGN_DEFAULT)),
            tuple(
                sorted(
                    normalise_per_line_shift_map(
                        next_tuning.get('per_line_shift', {}),
                        maximum_line=max(int(self.frame_line_count), 32),
                    ).items()
                )
            ),
            tuple(
                sorted(
                    (int(line), tuple(values))
                    for line, values in dict(next_tuning.get('line_control_overrides', {})).items()
                )
            ),
            tuple(
                sorted(
                    (int(line), tuple(sorted(dict(values).items())))
                    for line, values in dict(next_tuning.get('line_decoder_overrides', {})).items()
                )
            ),
            bool(next_tuning.get('show_quality', False)),
            bool(next_tuning.get('show_rejects', False)),
            bool(next_tuning.get('show_start_clock', False)),
            bool(next_tuning.get('show_clock_visuals', False)),
            bool(next_tuning.get('show_alignment_visuals', False)),
            bool(next_tuning.get('show_quality_meter', False)),
            bool(next_tuning.get('show_histogram_graph', False)),
            bool(next_tuning.get('show_eye_pattern', False)),
        )
        if not rebuild and tuning == self._current_decoder_tuning:
            return

        self._current_decoder_tuning = tuning
        self.tape_format = tuning[0]
        self.config = self.config.retuned(extra_roll=tuning[1], line_start_range=tuning[2])
        Line.configure(
            self.config,
            force_cpu=True,
            tape_format=self.tape_format,
            brightness=self._current_signal_controls[0] if self._current_signal_controls is not None else 50,
            sharpness=self._current_signal_controls[1] if self._current_signal_controls is not None else 50,
            gain=self._current_signal_controls[2] if self._current_signal_controls is not None else 50,
            contrast=self._current_signal_controls[3] if self._current_signal_controls is not None else 50,
            brightness_coeff=self._current_signal_controls[4] if self._current_signal_controls is not None else 48.0,
            sharpness_coeff=self._current_signal_controls[5] if self._current_signal_controls is not None else 3.0,
            gain_coeff=self._current_signal_controls[6] if self._current_signal_controls is not None else 0.5,
            contrast_coeff=self._current_signal_controls[7] if self._current_signal_controls is not None else 0.5,
            impulse_filter=self._current_signal_controls[8] if self._current_signal_controls is not None else 0,
            temporal_denoise=self._current_signal_controls[9] if self._current_signal_controls is not None else 0,
            noise_reduction=self._current_signal_controls[10] if self._current_signal_controls is not None else 0,
            hum_removal=self._current_signal_controls[11] if self._current_signal_controls is not None else 0,
            auto_black_level=self._current_signal_controls[12] if self._current_signal_controls is not None else 0,
            head_switching_mask=self._current_signal_controls[13] if self._current_signal_controls is not None else 0,
            line_stabilization=self._current_signal_controls[14] if self._current_signal_controls is not None else 0,
            auto_gain_contrast=self._current_signal_controls[15] if self._current_signal_controls is not None else 0,
            impulse_filter_coeff=self._current_signal_controls[16] if self._current_signal_controls is not None else 1.0,
            temporal_denoise_coeff=self._current_signal_controls[17] if self._current_signal_controls is not None else 1.0,
            noise_reduction_coeff=self._current_signal_controls[18] if self._current_signal_controls is not None else 1.0,
            hum_removal_coeff=self._current_signal_controls[19] if self._current_signal_controls is not None else 1.0,
            auto_black_level_coeff=self._current_signal_controls[20] if self._current_signal_controls is not None else 1.0,
            head_switching_mask_coeff=self._current_signal_controls[21] if self._current_signal_controls is not None else 1.0,
            line_stabilization_coeff=self._current_signal_controls[22] if self._current_signal_controls is not None else 1.0,
            auto_gain_contrast_coeff=self._current_signal_controls[23] if self._current_signal_controls is not None else 1.0,
            quality_threshold=tuning[3],
            quality_threshold_coeff=tuning[4],
            clock_lock=tuning[5],
            clock_lock_coeff=tuning[6],
            start_lock=tuning[7],
            start_lock_coeff=tuning[8],
            adaptive_threshold=tuning[9],
            adaptive_threshold_coeff=tuning[10],
            dropout_repair=tuning[11],
            dropout_repair_coeff=tuning[12],
            wow_flutter_compensation=tuning[13],
            wow_flutter_compensation_coeff=tuning[14],
            auto_line_align=tuning[15],
            per_line_shift=dict(tuning[16]),
            line_control_overrides=dict(tuning[17]),
            line_decoder_overrides={
                int(line): dict(items)
                for line, items in tuning[18]
            },
        )
        self._show_quality = tuning[19]
        self._show_rejects = tuning[20]
        self._show_start_clock = tuning[21]
        self._show_clock_visuals = tuning[22]
        self._show_alignment_visuals = tuning[23]
        self._show_quality_meter = tuning[24]
        self._show_histogram_graph = tuning[25]
        self._show_eye_pattern = tuning[26]
        if self.lines:
            self.lines = [Line(line._original_bytes, line._number) for line in self.lines]

    def display(self):
        glClear(GL_COLOR_BUFFER_BIT)

        self._apply_live_decoder_tuning()
        self._apply_live_signal_controls()

        self.set_plot_projection()

        self.draw_lines()

        if self._show_start_clock:
            self.draw_start_clock_markers()
        if self._show_clock_visuals:
            self.draw_clock_visuals()
        if self._show_alignment_visuals:
            self.draw_alignment_visuals()

        if self.height / self.nlines > 3:
            self.draw_h_grid(0, 0, 0, 0.25)

        if self.show_grid:
            if self.line_attr == 'fft':
                self.draw_freq_bins(256, 1, 1, 1, 0.5)
            elif self.line_attr == 'rolled' and self.width / 42 > 5:
                self.draw_bits(1, 1, 1, 0.5)
            elif self.line_attr == 'resampled':
                self.draw_slice(self.config.start_slice, 0, 1, 0, 0.5)

        self.set_overlay_projection()
        self.draw_line_numbers()
        self.draw_reject_reasons()
        self.draw_quality_meter()
        self.draw_histogram_graph()
        self.draw_eye_pattern()

        glutSwapBuffers()
        glutPostRedisplay()

        if self.pause and not self.single_step and not self.external_playback:
            time.sleep(0.1)
        else:
            next_lines = list(islice(self.lines_src, 0, self.nlines))

            if len(next_lines) > 0:
                self.lines = next_lines
                self._apply_live_signal_controls(rebuild=True)
            self.single_step = False
