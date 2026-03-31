import os
import random
import sys
import webbrowser

import numpy as np
from PyQt5.QtCore import QSize, QObject, QUrl
from PyQt5.QtGui import QFont, QColor

from teletext.parser import Parser


class Palette(object):

    def __init__(self, context):
        self._context = context
        self._palette = [
            QColor(0, 0, 0),
            QColor(255, 0, 0),
            QColor(0, 255, 0),
            QColor(255, 255, 0),
            QColor(0, 0, 255),
            QColor(255, 0, 255),
            QColor(0, 255, 255),
            QColor(255, 255, 255),
        ]
        self._context.setContextProperty('ttpalette', self._palette)

    def __getitem__(self, item):
        return (self._palette[item].red(), self._palette[item].green(), self._palette[item].blue())

    def __setitem__(self, item, value):
        self._palette[item].setRed(value[0])
        self._palette[item].setGreen(value[1])
        self._palette[item].setBlue(value[2])
        self._context.setContextProperty('ttpalette', self._palette)


class ParserQML(Parser):

    def __init__(self, tt, row, cells, nextrow, root):
        self._row = row
        self._cells = cells
        self._nextrow = nextrow
        self._root = root
        super().__init__(tt)

    def _doubleheight_enabled(self):
        value = self._root.property('doubleheight')
        return True if value is None else bool(value)

    def _doublewidth_enabled(self):
        value = self._root.property('doublewidth')
        return True if value is None else bool(value)

    def _flash_enabled(self):
        value = self._root.property('flashenabled')
        return True if value is None else bool(value)

    def _local_codepage(self):
        value = self._root.property('localcodepage')
        return None if value in (None, '', 'default') else str(value)

    def _current_codepage(self):
        if self._root.property('forcecodepage'):
            return 1
        value = self._root.property('pagecodepage')
        return 0 if value is None else int(value)

    def setstate(self, **kwargs):
        if 'dh' in kwargs and not self._doubleheight_enabled():
            kwargs['dh'] = False
        if 'dw' in kwargs and not self._doublewidth_enabled():
            kwargs['dw'] = False
        if 'flash' in kwargs and not self._flash_enabled():
            kwargs['flash'] = False
        super().setstate(**kwargs)

    def emitcharacter(self, c):
        self._cells[self._cell].setProperty('c', c)
        for state, value in self._state.items():
            self._cells[self._cell].setProperty(state, value)
        self._dh |= self._state['dh']
        self._cell += 1

    def parse(self):
        self.localcodepage = self._local_codepage()
        self.codepage = self._current_codepage()
        self._cell = 0
        self._dh = False
        super().parse()
        self._row.setProperty('rowheight', 2 if self._dh else 1)
        if self._nextrow:
            self._nextrow.setProperty('rowrendered', not (self._row.property('rowrendered') and self._dh))


class Decoder(object):

    def __init__(self, widget, font_family='teletext2'):

        self.widget = widget
        self._font_family = font_family

        self._fonts = [
            [
                [self.make_font(100), self.make_font(50)],
                [self.make_font(200), self.make_font(100)]
            ],
            [
                [self.make_font(120), self.make_font(60)],
                [self.make_font(240), self.make_font(120)]
            ]
        ]

        self.widget.rootContext().setContextProperty('ttfonts', self._fonts)
        self._palette = Palette(self.widget.rootContext())

        qml_file = os.path.join(os.path.dirname(__file__), 'decoder.qml')
        self.widget.setSource(QUrl.fromLocalFile(qml_file))

        self._root = self.widget.rootObject()
        self._rows = [self._root.findChild(QObject, 'rows').itemAt(x) for x in range(25)]
        self._cells = [[r.findChild(QObject, 'cols').itemAt(x) for x in range(40)] for r in self._rows]
        self._data = np.zeros((25, 40), dtype=np.uint8)
        self._parsers = [
            ParserQML(
                self._data[x],
                self._rows[x],
                self._cells[x],
                self._rows[x+1] if x < 24 else None,
                self._root,
            )
            for x in range(25)
        ]

        self.zoom = 2

    def __setitem__(self, item, value):
        self._data[item] = value
        if isinstance(item, tuple):
            item = item[0]
        if isinstance(item, int):
            self._parsers[item].parse()
        else:
            for p in self._parsers[item]:
                p.parse()

    def __getitem__(self, item):
        return self._data[item]

    def randomize(self):
        self[1:] = np.random.randint(0, 256, size=(24, 40), dtype=np.uint8)

    def make_font(self, stretch):
        font = QFont(self._font_family)
        font.setStyleStrategy(QFont.NoSubpixelAntialias)
        font.setHintingPreference(QFont.PreferNoHinting)
        font.setStretch(stretch)
        return font

    @property
    def palette(self):
        return self._palette

    @property
    def zoom(self):
        return self.widget.rootObject().property('zoom')

    @zoom.setter
    def zoom(self, zoom):
        if 0 < zoom < 5:
            self._fonts[0][0][0].setPixelSize(zoom * 10)
            self._fonts[0][0][1].setPixelSize(zoom * 20)
            self._fonts[0][1][0].setPixelSize(zoom * 10)
            self._fonts[0][1][1].setPixelSize(zoom * 20)
            self._fonts[1][0][0].setPixelSize(zoom * 10)
            self._fonts[1][0][1].setPixelSize(zoom * 20)
            self._fonts[1][1][0].setPixelSize(zoom * 10)
            self._fonts[1][1][1].setPixelSize(zoom * 20)
            self.widget.rootContext().setContextProperty('ttfonts', self._fonts)
            self.widget.rootObject().setProperty('zoom', zoom)
            self.widget.setFixedSize(self.size())

    @property
    def reveal(self):
        return self.widget.rootObject().property('reveal')

    @reveal.setter
    def reveal(self, reveal):
        self.widget.rootObject().setProperty('reveal', reveal)

    @property
    def showallsymbols(self):
        return self.widget.rootObject().property('showallsymbols')

    @showallsymbols.setter
    def showallsymbols(self, enabled):
        self.widget.rootObject().setProperty('showallsymbols', bool(enabled))

    @property
    def crteffect(self):
        return self.widget.rootObject().property('crteffect')

    @crteffect.setter
    def crteffect(self, crteffect):
        self.widget.rootObject().setProperty('crteffect', crteffect)

    @property
    def doublewidth(self):
        return self._root.property('doublewidth')

    @doublewidth.setter
    def doublewidth(self, enabled):
        self._root.setProperty('doublewidth', bool(enabled))
        for parser in self._parsers:
            parser.parse()

    @property
    def horizontalscale(self):
        return self._root.property('horizontalScale')

    @horizontalscale.setter
    def horizontalscale(self, value):
        self._root.setProperty('horizontalScale', float(value))
        self.widget.setFixedSize(self.size())

    @property
    def doubleheight(self):
        return self._root.property('doubleheight')

    @doubleheight.setter
    def doubleheight(self, enabled):
        self._root.setProperty('doubleheight', bool(enabled))
        for parser in self._parsers:
            parser.parse()

    @property
    def flashenabled(self):
        return self._root.property('flashenabled')

    @flashenabled.setter
    def flashenabled(self, enabled):
        self._root.setProperty('flashenabled', bool(enabled))
        for parser in self._parsers:
            parser.parse()

    @property
    def highlighttext(self):
        return self._root.property('highlighttext')

    @highlighttext.setter
    def highlighttext(self, enabled):
        self._root.setProperty('highlighttext', bool(enabled))

    @property
    def language(self):
        value = self._root.property('localcodepage')
        return 'default' if value in (None, '') else str(value)

    @language.setter
    def language(self, language):
        language = 'default' if language in (None, '', 'default') else str(language)
        if language == 'default':
            self._root.setProperty('localcodepage', '')
            self._root.setProperty('forcecodepage', False)
        else:
            self._root.setProperty('localcodepage', language)
            self._root.setProperty('forcecodepage', True)
        for parser in self._parsers:
            parser.parse()

    @property
    def pagecodepage(self):
        value = self._root.property('pagecodepage')
        return 0 if value is None else int(value)

    @pagecodepage.setter
    def pagecodepage(self, codepage):
        self._root.setProperty('pagecodepage', int(codepage))
        if not self._root.property('forcecodepage'):
            for parser in self._parsers:
                parser.parse()

    def size(self):
        sf = self.widget.rootObject().size()
        return QSize(int(sf.width()), int(sf.height()))

    @property
    def fullscreenmode(self):
        return self._root.property('fullscreenmode')

    @fullscreenmode.setter
    def fullscreenmode(self, enabled):
        self._root.setProperty('fullscreenmode', bool(enabled))
        self.widget.setFixedSize(self.size())

    @property
    def fullscreenstretch(self):
        return self._root.property('fullscreenstretch')

    @fullscreenstretch.setter
    def fullscreenstretch(self, enabled):
        self._root.setProperty('fullscreenstretch', bool(enabled))
        self.widget.setFixedSize(self.size())

    def set_viewport_size(self, width, height):
        self._root.setProperty('viewportwidth', int(width))
        self._root.setProperty('viewportheight', int(height))
        self.widget.setFixedSize(self.size())

    def setEffect(self, e):
        self._effect = bool(e)
        self.widget.rootContext().setContextProperty('tteffect', self._effect)
