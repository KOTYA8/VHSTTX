import time
import sys

import numpy as np
from itertools import islice

from OpenGL.GLUT import *
from OpenGL.GL import *


class VBIViewer(object):

    def __init__(self, lines, config, name = "VBI Viewer", width=800, height=512, nlines=32, tint=True, show_grid=True, show_slices=False, pause=False, show_line_numbers=True, signal_controls=None, decoder_tuning=None, tape_format='vhs', line_selection=None, external_playback=False):
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

        self.line_attr = 'resampled'

        if nlines is None:
            self.nlines = 32
            self.frame_line_count = len(self.config.field_range) * 2
        else:
            self.nlines = nlines
            self.frame_line_count = nlines

        self.lines_src = lines
        self.lines = list(islice(self.lines_src, 0, self.nlines))
        self._apply_live_decoder_tuning(rebuild=True)
        self._apply_live_signal_controls(rebuild=True)

        glutInit(sys.argv)
        glutInitDisplayMode(GLUT_SINGLE | GLUT_RGB)
        glutInitWindowSize(width,height)
        glutCreateWindow(name)
        self.set_title()

        glutDisplayFunc(self.display)
        glutReshapeFunc(self.reshape)
        glutKeyboardFunc(self.keyboard)
        glutMouseFunc(self.mouse)

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
            return max(self.width - self.label_margin, 1)
        return max(self.width, 1)

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
            exit(0)
        self.set_title()

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

    def text_width(self, font, text):
        return sum(glutBitmapWidth(font, ord(char)) for char in text)

    def draw_text(self, x, y, text, font=GLUT_BITMAP_8_BY_13):
        glRasterPos2f(x, y)
        for char in text:
            glutBitmapCharacter(font, ord(char))

    def draw_line_numbers(self):
        if not self.show_line_numbers:
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
            if number is None:
                continue
            label = str(number)
            x = margin_right - self.text_width(font, label) - 6
            y = self.height - ((index + 0.75) * line_height)
            self.draw_text(x, y, label, font=font)

    def draw_lines(self):

        glEnable(GL_TEXTURE_2D)
        for n,l in enumerate(self.lines[::-1]):
            array = getattr(l, self.line_attr)
            if self.line_selection is not None and self.line_number(l) not in self.line_selection():
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
        )

        if self.lines:
            self.lines = [Line(line._original_bytes, line._number) for line in self.lines]

    def _apply_live_decoder_tuning(self, rebuild=False):
        if self.decoder_tuning is None:
            return

        from teletext.vbi.line import Line

        next_tuning = self.decoder_tuning()
        tuning = (
            next_tuning['tape_format'],
            int(next_tuning['extra_roll']),
            tuple(int(value) for value in next_tuning['line_start_range']),
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
        )
        if self.lines:
            self.lines = [Line(line._original_bytes, line._number) for line in self.lines]

    def display(self):
        glClear(GL_COLOR_BUFFER_BIT)

        self._apply_live_decoder_tuning()
        self._apply_live_signal_controls()

        self.set_plot_projection()

        self.draw_lines()

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
