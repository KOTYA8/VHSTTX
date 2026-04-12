from . import charset

_unicode13 = False


class Parser(object):

    "Abstract base class for parsers"

    def __init__(self, tt, localcodepage=None, codepage=0):
        self.tt = tt
        self._state = {}
        self.codepage = codepage
        self.localcodepage = localcodepage
        self.parse()

    def reset(self):
        self._state['fg'] = 7
        self._state['bg'] = 0
        self._state['dw'] = False
        self._state['dh'] = False
        self._state['mosaic'] = False
        self._state['solid'] = True
        self._state['flash'] = False
        self._state['conceal'] = False
        self._state['boxed'] = False
        self._state['rendered'] = True

        self._heldmosaic = ' '
        self._heldsolid = True
        self._held = False
        self._esc = False
        #self._codepage = 0 # not implemented

    def setstate(self, **kwargs):
        any = False
        for state, value in kwargs.items():
            if value != self._state[state]:
                self._state[state] = value
                any = True
                if state in ['dw', 'dh']:
                    self._heldmosaic = ' '
                getattr(self, state+'Changed', lambda: None)()
        if any:
            getattr(self, 'stateChanged', lambda: None)()

    def ttchar(self, c):
        if self._state['mosaic'] and c not in range(0x40, 0x60):
            if _unicode13:
                return charset.g1[c]
            else:
                return chr(int(c)+0xee00) if self._state['solid'] else chr(int(c)+0xede0)
        else:
            if not self.localcodepage:
                return charset.g0["default"][c]
            else:
                if not self._esc and self.codepage:
                    return charset.g0[self.localcodepage][c]
                else:
                    return charset.g0["default"][c]

    def _emitcharacter(self, c):
        getattr(self, 'emitcharacter', lambda x: None)(c)
        if self._state['dw']:
            self._state['rendered'] = not self._state['rendered']
        else:
            self._state['rendered'] = True

    def emitcode(self, code=None):
        if self._held:
            tmp = self._state['solid']
            self._state['solid'] = self._heldsolid
            self._emitcharacter(self._heldmosaic)
            self._state['solid'] = tmp
        else:
            self._emitcharacter(' ')

    def setat(self, code=None, **kwargs):
        self.setstate(**kwargs)
        self.emitcode(code)

    def setafter(self, code=None, **kwargs):
        self.emitcode(code)
        self.setstate(**kwargs)

    def parsebyte(self, b, prev):
        h, l = int(b&0xf0), int(b&0x0f)
        if h == 0x0:
            if l < 8:
                self.setafter(code=b, fg=l, mosaic=False, conceal=False)
                self._heldmosaic = ' '
            elif l == 0x8: # flashing
                self.setafter(code=b, flash=True)
            elif l == 0x9: # steady
                self.setat(code=b, flash=False)
            elif l == 0xa:
                if prev == 0xa: # end box - set at because we're triggering on the second one
                    self.setat(code=b, boxed=False)
                else:
                    self.emitcode(b)
            elif l == 0xb:
                if prev == 0xb: # start box - set at because we're triggering on the second one
                    self.setat(code=b, boxed=True)
                else:
                    self.emitcode(b)
            else: # sizes
                dh, dw = bool(l&1), bool(l&2)
                if dh or dw:
                    self.setafter(code=b, dh=dh, dw=dw)
                else:
                    self.setat(code=b, dh=dh, dw=dw)

        elif h == 0x10:
            if l < 8:
                self.setafter(code=b, fg=l, mosaic=True, conceal=False)
            elif l == 0x8: # conceal
                self.setat(code=b, conceal=True)
            elif l == 0x9: # contiguous mosaic
                self.setat(code=b, solid=True)
            elif l == 0xa: # separated mosaic
                self.setat(code=b, solid=False)
            elif l == 0xb: # esc/switch
                self.emitcode(b)
                self._esc = not self._esc
            elif l == 0xc: # black background
                self.setat(code=b, bg = 0)
            elif l == 0xd: # new background
                self.setat(code=b, bg = self._state['fg'])
            elif l == 0xe: # hold mosaic
                self._held = True
                self.emitcode(b)
            elif l == 0xf: # release mosaic
                self.emitcode(b)
                self._held = False
        else:
            c = self.ttchar(b)
            if self._state['mosaic'] and (b & 0x20):
                self._heldmosaic = c
                self._heldsolid = self._state['solid']
            self._emitcharacter(c)

    def parse(self):
        self.reset()
        prev = None
        for c in self.tt&0x7f:
            self.parsebyte(c, prev)
            prev = c
