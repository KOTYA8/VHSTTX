import sys
import types
import unittest

import numpy as np


if 'PyQt5' not in sys.modules:
    pyqt5_module = types.ModuleType('PyQt5')
    qtcore_module = types.ModuleType('PyQt5.QtCore')
    qtgui_module = types.ModuleType('PyQt5.QtGui')

    class QSize(object):
        def __init__(self, width=0, height=0):
            self.width = width
            self.height = height

    class QObject(object):
        pass

    class QUrl(object):
        @staticmethod
        def fromLocalFile(path):
            return path

    class QFont(object):
        NoSubpixelAntialias = 0
        PreferNoHinting = 0

        def __init__(self, family=''):
            self.family = family

        def setStyleStrategy(self, strategy):
            self.strategy = strategy

        def setHintingPreference(self, preference):
            self.preference = preference

        def setStretch(self, stretch):
            self.stretch = stretch

        def setPixelSize(self, size):
            self.pixel_size = size

    class QColor(object):
        def __init__(self, red=0, green=0, blue=0):
            self._red = red
            self._green = green
            self._blue = blue

        def red(self):
            return self._red

        def green(self):
            return self._green

        def blue(self):
            return self._blue

        def setRed(self, value):
            self._red = value

        def setGreen(self, value):
            self._green = value

        def setBlue(self, value):
            self._blue = value

    qtcore_module.QSize = QSize
    qtcore_module.QObject = QObject
    qtcore_module.QUrl = QUrl
    qtgui_module.QFont = QFont
    qtgui_module.QColor = QColor
    pyqt5_module.QtCore = qtcore_module
    pyqt5_module.QtGui = qtgui_module

    sys.modules['PyQt5'] = pyqt5_module
    sys.modules['PyQt5.QtCore'] = qtcore_module
    sys.modules['PyQt5.QtGui'] = qtgui_module


from teletext.gui.decoder import ParserQML


class FakeObject(object):
    def __init__(self, **properties):
        self._properties = dict(properties)

    def setProperty(self, name, value):
        self._properties[name] = value

    def property(self, name):
        return self._properties.get(name)


class TestParserQMLAttributes(unittest.TestCase):
    def make_parser(self, data, **root_properties):
        row = FakeObject(rowheight=1, rowrendered=True)
        nextrow = FakeObject(rowrendered=True)
        root_defaults = {
            'doubleheight': True,
            'doublewidth': True,
            'flashenabled': True,
        }
        root_defaults.update(root_properties)
        root = FakeObject(**root_defaults)
        cells = [FakeObject(dh=False, dw=False, flash=False, rendered=True) for _ in range(40)]
        parser = ParserQML(data, row, cells, nextrow, root)
        return parser, row, nextrow, cells

    def test_double_height_enabled_hides_following_row(self):
        parser, row, nextrow, cells = self.make_parser(
            np.array([0x0D] + [ord('A')] * 39, dtype=np.uint8),
            doubleheight=True,
        )

        parser.parse()

        self.assertEqual(row.property('rowheight'), 2)
        self.assertFalse(nextrow.property('rowrendered'))
        self.assertTrue(any(cell.property('dh') for cell in cells))

    def test_double_height_disabled_keeps_rows_visible(self):
        parser, row, nextrow, cells = self.make_parser(
            np.array([0x0D] + [ord('A')] * 39, dtype=np.uint8),
            doubleheight=False,
        )

        parser.parse()

        self.assertEqual(row.property('rowheight'), 1)
        self.assertTrue(nextrow.property('rowrendered'))
        self.assertFalse(any(cell.property('dh') for cell in cells))

    def test_double_width_disabled_keeps_all_cells_rendered(self):
        parser, row, nextrow, cells = self.make_parser(
            np.array([0x0E, ord('A'), ord('B'), ord('C')] + [0x20] * 36, dtype=np.uint8),
            doublewidth=False,
        )

        parser.parse()

        self.assertTrue(all(not cell.property('dw') for cell in cells))
        self.assertTrue(all(cell.property('rendered') for cell in cells if cell.property('c')))
        self.assertEqual(row.property('rowheight'), 1)
        self.assertTrue(nextrow.property('rowrendered'))

    def test_flash_disabled_turns_flashing_text_steady(self):
        parser, row, nextrow, cells = self.make_parser(
            np.array([0x08, ord('A')] + [0x20] * 38, dtype=np.uint8),
            flashenabled=False,
        )

        parser.parse()

        self.assertFalse(any(cell.property('flash') for cell in cells))
