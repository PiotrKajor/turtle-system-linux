"""Turtle Machine: canvas, turtle state, input state and the built-in library.

Semantics were measured against the original TurtleSystem 15.95 (run as an oracle) and from
its executable: integer turtle coordinates moved by Round(n*sin) in 80-bit arithmetic with
Delphi's banker's rounding, GDI-exact drawing (see gdi.py), 32-bit integer arithmetic, the
original input codes (\\key = -9, ?click = 128 + button + modifiers, ...), and a "Turtle Files"
directory for file processing. Language front ends (lang_*.py) compile programs to Python code
objects that call the implementations collected by Machine.library().
"""

import builtins
import ctypes
import math
from decimal import ROUND_HALF_UP, Decimal
import os
import queue
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QLocale, QObject, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, Qt

import gdi
from colours import COLOURS

PROGRAM_FILE = "<program>"
L = np.longdouble
PI = L("3.14159265358979323846264338327950288")

# display() font codes 0-15; +16 bold, +32 italic, +64 underline, +128 strikeout
FONTS = ["Arial", "Arial Black", "Arial Narrow", "Bookman Old Style", "Comic Sans MS",
         "Courier New", "Georgia", "Lucida Bright", "Lucida Calligraphy", "Lucida Handwriting",
         "Lucida Sans", "Lucida Sans Typewriter", "Old English Text MT", "Symbol",
         "Times New Roman", "Verdana"]

# fonts missing on Linux: a similar family, never a decorative substitute
FONT_FALLBACKS = {"Arial": "Liberation Sans", "Arial Black": "Liberation Sans",
                  "Arial Narrow": "Liberation Sans Narrow", "Bookman Old Style": "Liberation Serif",
                  "Comic Sans MS": "Liberation Sans", "Courier New": "Liberation Mono",
                  "Georgia": "Liberation Serif", "Lucida Bright": "Liberation Serif",
                  "Lucida Calligraphy": "Liberation Serif", "Lucida Handwriting": "Liberation Serif",
                  "Lucida Sans": "Liberation Sans", "Lucida Sans Typewriter": "Liberation Mono",
                  "Old English Text MT": "Liberation Serif", "Symbol": "Symbol",
                  "Times New Roman": "Liberation Serif", "Verdana": "DejaVu Sans"}
_FAMILY_CACHE = {}


def display_family(index):
    """The family to draw display() font `index` with: the Windows font if installed."""
    name = FONTS[index]
    if name not in _FAMILY_CACHE:
        from PySide6.QtGui import QFontDatabase
        installed = set(QFontDatabase.families())
        _FAMILY_CACHE[name] = name if name in installed else FONT_FALLBACKS.get(name, "Sans Serif")
    return _FAMILY_CACHE[name]


# input codes, as in the original's code table: \name is the code, ?name the current value
QUERY_CODES = {"lmouse": -1, "rmouse": -2, "mmouse": -3, "click": -4, "clickx": -5,
               "clicky": -6, "mousex": -7, "mousey": -8, "key": -9, "kshift": -10,
               "mousekey": -11, "keybuffer": 0}
KEY_CODES = {"backspace": 8, "tab": 9, "return": 13, "shift": 16, "ctrl": 17, "alt": 18,
             "capslock": 20, "escape": 27, "space": 32, "pgup": 33, "pgdn": 34, "end": 35,
             "home": 36, "left": 37, "up": 38, "right": 39, "down": 40, "insert": 45,
             "delete": 46, "lwin": 91, "rwin": 92, "enter": 13, "all": 256}
KEY_CODES.update({chr(c).lower(): c for c in range(65, 91)})
KEY_CODES.update({str(d): 48 + d for d in range(10)})
KEY_CODES.update({f"f{i}": 111 + i for i in range(1, 13)})
INPUT_CODES = {**KEY_CODES, **QUERY_CODES}
BUTTON_CODES = {1: -1, 2: -2, 4: -3}

# modifier bits in ?click / ?kshift / status values
SHIFT, ALT, CTRL = 8, 16, 32

BACKGROUND = 0xFEFFFFFF      # never-painted canvas (GDI paints with a different alpha byte)
INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1
STRING_SIZE = 64             # the original's default maximum string length ("VarsStringSize")


class StopProgram(BaseException):
    """Raised to end a program; BaseException so a user's `except Exception` can't catch it."""


class TurtleError(Exception):
    """A run-time error reported with the original's wording."""


def wrap32(v):
    """Result of + - * etc.: 32-bit two's complement wrap-around for integers, as the Turtle
    Machine's arithmetic, and the default string size limit for strings."""
    t = type(v)
    if t is int:
        if not INT_MIN <= v <= INT_MAX:
            return ((v - INT_MIN) & 0xFFFFFFFF) + INT_MIN
    elif t is str and len(v) > STRING_SIZE:
        return v[:STRING_SIZE]
    return v


def decimal_point():
    return QLocale.system().decimalPoint() or "."


def div_round(a, b):
    """Turtle `/`: integer division rounded half up (7/2 = 4, -7/2 = -3)."""
    if type(a) is int and type(b) is int:
        if b == 0:
            raise ZeroDivisionError
        return wrap32((2 * a + b) // (2 * b))
    return math.floor(a / b + 0.5)


def ratio_round_even(n, d):
    """n / d rounded half to even, exactly (as Delphi's Round on the extended quotient)."""
    if d < 0:
        n, d = -n, -d
    q, r = divmod(n, d)
    if 2 * r > d or (2 * r == d and q % 2):
        q += 1
    return q


def ext_round(v):
    """Round an extended-precision value half up, to a 32-bit integer."""
    if not np.isfinite(v):
        raise TurtleError("Arithmetic error (result not defined)")
    return wrap32(int(np.floor(v + L(0.5))))


class DelphiRandom:
    """Delphi's Random / RandSeed generator, which the original uses."""

    def __init__(self, seed=None):
        if seed is None:
            seed = int(time.time() * 1000) ^ os.getpid()
        self.seed = seed & 0xFFFFFFFF

    def random(self, n):
        """0 <= result < n (0 when n <= 0)."""
        self.seed = (self.seed * 0x08088405 + 1) & 0xFFFFFFFF
        return (self.seed * n) >> 32 if n > 0 else 0


def half_up(v):
    return int(math.floor(v + 0.5))


def round_even(v):
    """Delphi Round on an extended value: half to even."""
    return int(np.rint(v))


# ---------------------------------------------------------------- GUI bridge

class Bridge(QObject):
    """Signals from the program thread to the GUI (delivered queued, on the GUI thread)."""
    write = Signal(str, str)            # text, target: "console" | "output"
    clear = Signal(str, int, bool)      # target, background rgb (-1 = unchanged), bring to front
    want_input = Signal(str)            # prompt
    cursor = Signal(int)
    finished = Signal(object)           # None, or (line, message) on error


class _TurtleView:
    """What a program sees as turtx/turty/turtd/turta/turtt/turtc."""

    def __init__(self, machine):
        self.m = machine

    def __getitem__(self, i):
        return self.m.turtle[i]

    def __setitem__(self, i, v):
        if i == 3:
            self.m.set_angles(v)
        else:
            self.m.turtle[i] = int(v)


class TurtleRef:
    """A live reference to turtle property k, used as a VAR parameter box."""

    __slots__ = ("m", "k")

    def __init__(self, machine, k):
        self.m, self.k = machine, k

    def __getitem__(self, _):
        return self.m.turtle[self.k]

    def __setitem__(self, _, v):
        if self.k == 3:
            self.m.set_angles(v)
        else:
            self.m.turtle[self.k] = int(v)


class _CallableInt(int):
    """A constant that also works when called, e.g. both `maxint` and `maxint()`."""

    def __call__(self):
        return int(self)


# ---------------------------------------------------------------- the machine

class Machine:
    def __init__(self, files_dir=None):
        self.bridge = Bridge()
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.lines = queue.Queue()
        self.thread = None
        self.paused = threading.Event()
        self.default_size = 1000
        self.files_dir = Path(files_dir) if files_dir else Path.home() / "Turtle Files"
        self.reset()

    # ------------------------------------------------ state

    dark_canvas = False          # option: programs start on a black canvas with a white pen

    def background(self):
        """The never-painted canvas colour (alpha byte FE, see BACKGROUND)."""
        return 0xFE000000 if self.dark_canvas else BACKGROUND

    def reset(self):
        with self.lock:
            size = self.default_size
            self.resx = self.resy = size
            self.pixels = np.full((size, size), self.background(), dtype=np.uint32)
            self.left = self.top = 0
            self.width = self.height = size
            self.default_turtle = [size // 2, size // 2, 0, 360, 2,
                                   0xFFFFFF if self.dark_canvas else 0x000000]
            self.turtle = self.default_turtle
            self.points = []
            self.updating = True
            self.dirty = True
            self.t0 = time.monotonic()
            self.values = {}
            self.events = {}
            self.keybuf = []
            self.keybuf_size = 32
            self.keyecho = True
            self.rng = DelphiRandom()
            self.waiting_input = False
            self.files = {}
            self.cwd = self.files_dir
            self._trig = {}
            self.memory = []          # address() targets, for peek/poke

    def check(self):
        if self.stop_flag.is_set():
            raise StopProgram
        while self.paused.is_set():
            time.sleep(0.02)
            if self.stop_flag.is_set():
                raise StopProgram

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def frame(self):
        """An opaque copy of the canvas for display (QImage RGB32)."""
        with self.lock:
            arr = self.pixels | np.uint32(0xFF000000)
            self.dirty = False
        h, w = arr.shape
        img = QImage(arr.data, w, h, 4 * w, QImage.Format.Format_RGB32)
        return img.copy()

    # ------------------------------------------------ coordinates

    def px(self, x):
        if self.width == self.resx and not self.left:
            return int(x)
        return ratio_round_even((int(x) - self.left) * self.resx, self.width)

    def py(self, y):
        if self.height == self.resy and not self.top:
            return int(y)
        return ratio_round_even((int(y) - self.top) * self.resy, self.height)

    def to_pixel(self, x, y):
        return self.px(x), self.py(y)

    def to_virtual(self, px, py):
        return (half_up(self.left + px * self.width / self.resx),
                half_up(self.top + py * self.height / self.resy))

    def _sincos(self, d):
        a = self.turtle[3]
        key = (d, a)
        sc = self._trig.get(key)
        if sc is None:
            rad = L(2) * PI * L(d) / L(a)
            sc = self._trig[key] = (np.sin(rad), np.cos(rad))
        return sc

    # ------------------------------------------------ drawing primitives

    def _pen(self, width=None, colour=None):
        t = abs(self.turtle[4]) if width is None else width
        return gdi.Pen(t, self.turtle[5] if colour is None else colour)

    def _painted(self):
        self.dirty = True

    def move_to(self, x, y, draw):
        self.check()
        t = self.turtle
        if draw and t[4] >= 0:
            with self.lock:
                p1, p2 = self.to_pixel(t[0], t[1]), self.to_pixel(x, y)
                pen = self._pen()
                gdi.line_to(self.pixels, pen, *p1, *p2)
                gdi.line_to(self.pixels, pen, *p2, *p1)
                self._painted()
        t[0], t[1] = int(x), int(y)
        self.points.append((t[0], t[1]))
        if len(self.points) > 20000:
            del self.points[:10000]

    def forward(self, n):
        sin, cos = self._sincos(self.turtle[2])
        n = L(int(n))
        self.move_to(self.turtle[0] + round_even(n * sin), self.turtle[1] + round_even(-n * cos),
                     True)

    def set_angles(self, n):
        n = int(n)
        if n <= 0:
            raise TurtleError("Angles must be positive")
        t = self.turtle
        t[2] = round_even(L(t[2]) * L(n) / L(t[3]))
        t[3] = n

    def ellipse(self, rx, ry, filled):
        self.check()
        x, y = self.turtle[0], self.turtle[1]
        with self.lock:
            box = (self.px(x - rx), self.py(y - ry), self.px(x + rx), self.py(y + ry))
            if filled:
                gdi.ellipse(self.pixels, None, self.turtle[5] & 0xFFFFFF, *box)
            else:
                gdi.ellipse(self.pixels, self._pen(), None, *box)
            self._painted()

    def polygon_points(self, n):
        n = int(n)
        return [self.to_pixel(x, y) for x, y in (self.points[-n:] if n > 0 else [])]

    # ------------------------------------------------ input events (from the GUI thread)

    def _set(self, code, value, event=True):
        self.values[code] = value
        if event and value > 0:
            self.events[code] = self.events.get(code, 0) + 1

    def key_event(self, vk, mods, down, text=""):
        status = 128 + mods
        if down:
            self._set(-9, vk)
            self._set(vk, status)
            self._set(-10, status)
            self._set(-11, vk)
            if text and text.isprintable() and len(self.keybuf) < self.keybuf_size:
                self.keybuf.append(text)
                self._set(0, len(self.keybuf))
                if self.keyecho:
                    self.bridge.write.emit(text, "console")
        else:
            self._set(-9, -vk)
            self._set(vk, -status)
            self._set(-10, -status)

    def mouse_event(self, x, y, kind, button=0, mods=0):
        """kind: 'move' | 'down' | 'up'; button: 1 left, 2 right, 4 middle."""
        self._set(-7, x, False)
        self._set(-8, y, False)
        if kind == "move" or button not in BUTTON_CODES:
            return
        value = 128 + button + mods
        if kind == "down":
            self._set(-5, x, False)
            self._set(-6, y, False)
            self._set(-4, value)
            self._set(BUTTON_CODES[button], value)
            self._set(-11, button)
        else:
            self._set(-4, -value)
            self._set(BUTTON_CODES[button], -value)

    def submit_line(self, line):
        self.lines.put(line)

    # ------------------------------------------------ running

    def start(self, code, env):
        self.reset()
        self.env = env
        self.stop_flag.clear()
        while not self.lines.empty():
            self.lines.get_nowait()
        threading.stack_size(256 * 1024 * 1024)
        self.thread = threading.Thread(target=self._run, args=(code, env), daemon=True,
                                       name="turtle-program")
        self.thread.start()
        threading.stack_size(0)

    def stop(self):
        """Halt the running program, interrupting even a tight loop."""
        if not self.running:
            return
        self.stop_flag.set()
        self.paused.clear()
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.thread.ident),
                                                   ctypes.py_object(StopProgram))

    def _run(self, code, env):
        sys.setrecursionlimit(100000)
        result = None
        try:
            if callable(code):          # a Turtle Machine run (PCode)
                code()
            else:
                exec(code, env)
        except StopProgram:
            pass
        except BaseException as e:  # noqa: BLE001 - every program error goes to the user
            line = getattr(e, "turtle_line", None)
            tb = e.__traceback__
            while tb is not None:
                if tb.tb_frame.f_code.co_filename == PROGRAM_FILE and line is None:
                    line = tb.tb_lineno
                tb = tb.tb_next
            result = (line, error_message(e))
        finally:
            for f in self.files.values():
                f.close()
            self.files.clear()
            with self.lock:
                self.updating = True
                self.dirty = True
            self.bridge.finished.emit(result)

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, 0.01))

    # ------------------------------------------------ the built-in library

    def library(self):
        """Implementations keyed by their Turtle Python names (front ends add aliases)."""
        m = self
        T = lambda: m.turtle  # noqa: E731
        lib = {}

        def builtin(fn):
            lib[fn.__name__.rstrip("_")] = fn
            return fn

        # -- turtle: relative movement
        @builtin
        def forward(n):
            m.forward(n)

        @builtin
        def back(n):
            m.forward(-int(n))

        @builtin
        def left(a):
            T()[2] = (T()[2] - int(a)) % T()[3]

        @builtin
        def right(a):
            T()[2] = (T()[2] + int(a)) % T()[3]

        @builtin
        def drawxy(dx, dy):
            m.move_to(T()[0] + int(dx), T()[1] + int(dy), True)

        @builtin
        def movexy(dx, dy):
            m.move_to(T()[0] + int(dx), T()[1] + int(dy), False)

        # -- turtle: absolute movement
        @builtin
        def home():
            m.move_to(m.left + m.width // 2, m.top + m.height // 2, False)
            T()[2] = 0

        @builtin
        def setx(x):
            m.move_to(x, T()[1], False)

        @builtin
        def sety(y):
            m.move_to(T()[0], y, False)

        @builtin
        def setxy(x, y):
            m.move_to(x, y, False)

        @builtin
        def direction(d):
            T()[2] = int(d)

        @builtin
        def angles(n):
            m.set_angles(n)

        @builtin
        def turnxy(dx, dy):
            if dx or dy:
                ang = np.arctan2(L(int(dx)), L(-int(dy))) * L(T()[3]) / (L(2) * PI)
                T()[2] = round_even(ang) % T()[3]

        # -- turtle: drawing shapes
        @builtin
        def circle(r):
            m.ellipse(int(r), int(r), False)

        @builtin
        def blot(r):
            m.ellipse(int(r), int(r), True)

        @builtin
        def ellipse(rx, ry):
            m.ellipse(int(rx), int(ry), False)

        @builtin
        def ellblot(rx, ry):
            m.ellipse(int(rx), int(ry), True)

        @builtin
        def box(w, h, col, border):
            m.check()
            x, y, w, h = T()[0], T()[1], int(w), int(h)
            pts = [m.to_pixel(*p) for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y))]
            col = int(col) & 0xFFFFFF
            pen = m._pen() if border else gdi.Pen(1, col)
            with m.lock:
                gdi.polygon(m.pixels, pen, col, pts)
                m._painted()

        @builtin
        def polyline(n):
            m.check()
            pts = m.polygon_points(n)
            with m.lock:
                gdi.polyline(m.pixels, m._pen(), pts)
                m._painted()

        @builtin
        def polygon(n):
            m.check()
            pts = m.polygon_points(n)
            if len(pts) >= 2:
                with m.lock:
                    colour = T()[5] & 0xFFFFFF
                    gdi.polygon(m.pixels, gdi.Pen(1, colour), colour, pts)
                    m._painted()

        @builtin
        def remember():
            m.points.append((T()[0], T()[1]))

        @builtin
        def forget(n):
            if int(n) > 0:
                del m.points[-int(n):]

        # -- other turtle commands
        @builtin
        def penup():
            T()[4] = -abs(T()[4])

        @builtin
        def pendown():
            T()[4] = abs(T()[4])

        @builtin
        def thickness(t):
            T()[4] = int(t)

        @builtin
        def colour(c):
            T()[5] = int(c)

        @builtin
        def randcol(n):
            n = max(1, min(int(n), len(COLOURS)))
            T()[5] = COLOURS[m.rng.random(n)][1]

        @builtin
        def newturtle(arr):
            if len(arr) < 6:
                raise TurtleError("newturtle needs an integer array of (at least) 6 elements")
            m.turtle = arr

        @builtin
        def oldturtle():
            m.turtle = m.default_turtle

        @builtin
        def rgb(i):
            i = int(i)
            return COLOURS[i - 1][1] if 1 <= i <= len(COLOURS) else i

        @builtin
        def mixcols(c1, c2, p1, p2):
            total = p1 + p2
            return sum(half_up((((c1 >> s) & 255) * p1 + ((c2 >> s) & 255) * p2) / total) << s
                       for s in (16, 8, 0))

        # -- canvas operations
        @builtin
        def blank(col):
            m.check()
            with m.lock:
                m.pixels[:] = 0xFF000000 | (int(col) & 0xFFFFFF)
                m._painted()

        @builtin
        def canvas(left_, top_, width_, height_):
            if int(width_) == 0 or int(height_) == 0:
                raise TurtleError("Canvas width and height must be non-zero")
            with m.lock:
                m.left, m.top, m.width, m.height = int(left_), int(top_), int(width_), int(height_)
                m._painted()

        @builtin
        def resolution(rx, ry):
            rx, ry = int(rx), int(ry)
            if not (1 <= rx <= 10000 and 1 <= ry <= 10000):
                raise TurtleError("Resolution must be between 1 and 10000")
            with m.lock:
                m.pixels = np.full((ry, rx), m.background(), dtype=np.uint32)
                m.resx, m.resy = rx, ry
                m._painted()

        @builtin
        def pixset(x, y, c):
            if m.stop_flag.is_set():
                raise StopProgram
            px, py = m.px(x), m.py(y)
            if 0 <= px < m.resx and 0 <= py < m.resy:
                m.pixels[py, px] = 0xFF000000 | (c & 0xFFFFFF)
                m.dirty = True

        @builtin
        def pixcol(x, y):
            px, py = m.px(x), m.py(y)
            if 0 <= px < m.resx and 0 <= py < m.resy:
                return int(m.pixels[py, px]) & 0xFFFFFF
            return -1

        @builtin
        def fill(x, y, fillcol, edgecol):
            m.check()
            px, py = m.to_pixel(x, y)
            with m.lock:
                gdi.flood_fill(m.pixels, px, py, int(fillcol),
                               border=0xFF000000 | (int(edgecol) & 0xFFFFFF))
                m._painted()

        @builtin
        def recolour(x, y, newcol):
            m.check()
            px, py = m.to_pixel(x, y)
            with m.lock:
                if 0 <= px < m.resx and 0 <= py < m.resy:
                    seed = 0xFF000000 | (int(m.pixels[py, px]) & 0xFFFFFF)
                    gdi.flood_fill(m.pixels, px, py, int(newcol), surface=seed)
                    m._painted()

        @builtin
        def display(text, font, size):
            m.check()
            font, size = int(font), int(size)
            f = QFont(display_family(font & 15))
            f.setBold(bool(font & 16))
            f.setItalic(bool(font & 32))
            f.setUnderline(bool(font & 64))
            f.setStrikeOut(bool(font & 128))
            f.setPixelSize(max(1, half_up(size * 96 / 72)))
            px, py = m.to_pixel(T()[0], T()[1])
            with m.lock:
                h, w = m.pixels.shape
                img = QImage(m.pixels.data, w, h, 4 * w, QImage.Format.Format_RGB32)
                p = QPainter(img)
                p.setFont(f)
                p.setPen(QColor.fromRgb(T()[5] & 0xFFFFFF))
                p.drawText(QPointF(px, py + QFontMetricsF(f).ascent()), str(text))
                p.end()
                m._painted()

        @builtin
        def noupdate():
            with m.lock:
                m.updating = False

        @builtin
        def update():
            with m.lock:
                m.updating = True
                m.dirty = True

        @builtin
        def pause(ms):
            with m.lock:
                m.dirty = True
            m.wait(max(0, int(ms)) / 1000)

        # -- arithmetic: computed in 80-bit extended precision like the original, then
        #    rounded half up (sin(30,1,1) = 1, sin(210,1,1) = 0, divmult(-1,2,1) = 0)
        def unit():
            return L(2) * PI / L(T()[3])

        def q(a, b):
            if b == 0:
                raise ZeroDivisionError
            return L(int(a)) / L(int(b))

        @builtin
        def sign(a):
            return (a > 0) - (a < 0)

        @builtin
        def sqrt(a, b):
            if a < 0:
                raise TurtleError("Square root of a negative number")
            return ext_round(np.sqrt(L(int(a))) * L(int(b)))

        @builtin
        def hypot(a, b, c):
            return ext_round(np.hypot(L(int(a)), L(int(b))) * L(int(c)))

        @builtin
        def divmult(a, b, c):
            return ext_round(q(a, b) * L(int(c)))

        @builtin
        def power(a, b, c, d, e):
            return ext_round(np.power(q(a, b), q(c, d)) * L(int(e)))

        @builtin
        def root(a, b, c):
            return ext_round(np.power(L(int(a)), L(1) / L(int(b))) * L(int(c)))

        @builtin
        def lerp(a, b, t, scale):
            return ext_round(L(int(a)) + L(int(b) - int(a)) * q(t, scale))

        @builtin
        def pi(a):
            return ext_round(PI * L(int(a)))

        @builtin
        def sin(a, b, c):
            return ext_round(np.sin(q(a, b) * unit()) * L(int(c)))

        @builtin
        def cos(a, b, c):
            return ext_round(np.cos(q(a, b) * unit()) * L(int(c)))

        @builtin
        def tan(a, b, c):
            return ext_round(np.tan(q(a, b) * unit()) * L(int(c)))

        @builtin
        def asin(a, b, c):
            return ext_round(np.arcsin(q(a, b)) / unit() * L(int(c)))

        @builtin
        def acos(a, b, c):
            return ext_round(np.arccos(q(a, b)) / unit() * L(int(c)))

        @builtin
        def atan(a, b, c):
            angle = np.arctan(q(a, b)) if b else (PI / L(2) if a >= 0 else -PI / L(2))
            return ext_round(angle / unit() * L(int(c)))

        @builtin
        def log(a, b, c):
            return ext_round(np.log(q(a, b)) * L(int(c)))

        @builtin
        def log10(a, b, c):
            return ext_round(np.log10(q(a, b)) * L(int(c)))

        @builtin
        def exp(a, b, c):
            return ext_round(np.exp(q(a, b)) * L(int(c)))

        @builtin
        def antilog(a, b, c):
            return ext_round(np.power(L(10), q(a, b)) * L(int(c)))

        # -- random numbers: Delphi's generator, so randseed gives the original's sequences
        @builtin
        def randint(a, b):
            a, b = int(a), int(b)
            if b < a:
                a, b = b, a
            return a + m.rng.random(b - a + 1)

        @builtin
        def randrange(n):
            return m.rng.random(int(n))

        @builtin
        def randseed(seed):
            m.rng.seed = int(seed) & 0xFFFFFFFF
            return int(seed)

        lib["maxint"] = _CallableInt(INT_MAX)
        lib["abs"] = lambda a: wrap32(abs(a))
        lib["max"] = max
        lib["min"] = min

        # -- strings & conversion
        @builtin
        def intdef(s, default):
            try:
                return int(str(s).strip())
            except ValueError:
                return default

        @builtin
        def qstr(a, b, places):
            if b == 0:
                raise ZeroDivisionError
            value = (Decimal(int(a)) / Decimal(int(b))).quantize(
                Decimal(1).scaleb(-max(0, int(places))), rounding=ROUND_HALF_UP)
            return f"{value:f}".replace(".", decimal_point())

        @builtin
        def qint(s, n, default):
            text, point = str(s).strip(), decimal_point()
            if point != "." and "." in text:
                return default
            try:
                return half_up(float(text.replace(point, ".")) * n)
            except ValueError:
                return default

        @builtin
        def hex_(i, mindigits):
            return format(int(i) & 0xFFFFFFFF, "X").zfill(int(mindigits))

        @builtin
        def pad(s, padding, maxw):
            s, padding, maxw = str(s), str(padding) or " ", int(maxw)
            if maxw >= 0:
                while len(s) < maxw:
                    s = padding + s
                return s[-maxw:] if len(s) > maxw else s
            while len(s) < -maxw:
                s = s + padding
            return s[:-maxw]

        @builtin
        def boolint(b):
            return -1 if b else 0

        # -- input, output and timing
        @builtin
        def print_(*args, sep=" ", end="\n"):
            m.check()
            text = sep.join(("1" if a else "0") if type(a) is bool else str(a)
                            for a in args) + end
            m.bridge.write.emit(text, "console")
            m.bridge.write.emit(text, "output")

        @builtin
        def readline():
            m.waiting_input = True
            m.bridge.want_input.emit("")
            try:
                while True:
                    m.check()
                    try:
                        line = m.lines.get(timeout=0.05)
                        break
                    except queue.Empty:
                        pass
            finally:
                m.waiting_input = False
            m.bridge.write.emit(line + "\n", "console")
            return line

        @builtin
        def input_(prompt=""):
            if prompt:
                m.bridge.write.emit(str(prompt), "console")
            return readline()

        @builtin
        def read(n):
            n = int(n)
            out = "".join(m.keybuf[:n])
            del m.keybuf[:n]
            m.values[0] = len(m.keybuf)
            return out

        @builtin
        def keybuffer(size):
            m.keybuf_size = max(0, int(size))
            m.keybuf.clear()
            m.values[0] = -1

        @builtin
        def keyecho(on):
            m.keyecho = bool(on)

        @builtin
        def reset(code):
            code = int(code)
            if code == 256:
                m.values.clear()
                m.keybuf.clear()
            else:
                m.values[code] = -1
                if code == 0:
                    m.keybuf.clear()

        @builtin
        def status(code):
            return m.values.get(int(code), -1)

        @builtin
        def detect(code, ms):
            code, ms = int(code), int(ms)
            seen = m.events.get(code, 0)
            end = time.monotonic() + ms / 1000
            while True:
                if m.events.get(code, 0) != seen:
                    return m.values.get(code, -1)
                if ms and time.monotonic() >= end:
                    return 0
                m.wait(0.005)

        @builtin
        def time_():
            return int((time.monotonic() - m.t0) * 1000)

        @builtin
        def timeset(ms):
            m.t0 = time.monotonic() - int(ms) / 1000

        @builtin
        def console(clear, col):
            m.bridge.clear.emit("console" if clear else "console-colour", int(col), False)

        @builtin
        def output(clear, col, tofront):
            m.bridge.clear.emit("output" if clear else "output-colour", int(col), bool(tofront))

        @builtin
        def cursor(code):
            m.bridge.cursor.emit(int(code))

        @builtin
        def halt():
            raise StopProgram

        lib["query"] = lambda code: m.values.get(code, -1)
        lib.update(self._file_library())

        # -- memory access: address() of a variable, peek/poke through it
        def address_of(target):
            m.memory.append(target)
            return len(m.memory)

        def resolve(addr):
            addr = int(addr)
            if not 1 <= addr <= len(m.memory):
                raise TurtleError(f"Invalid memory address {addr}")
            return m.memory[addr - 1]

        def peek(addr):
            kind, where, key = resolve(addr)
            return m.turtle[key] if kind == "turtle" else where[key]

        def poke(addr, value):
            kind, where, key = resolve(addr)
            if kind == "turtle":
                TurtleRef(m, key)[0] = value
            else:
                where[key] = value

        lib["_address_turtle_"] = lambda k: address_of(("turtle", None, k))
        lib["_address_var_"] = lambda frame_vars, name: address_of(("var", frame_vars, name))
        lib["_tref_"] = lambda k: TurtleRef(m, k)
        lib["peek"] = peek
        lib["poke"] = poke

        def ignore(*_):
            return 0

        for name in ("trace", "watch", "dump", "heapreset", "pcodehalt"):
            lib[name] = ignore
        return lib

    # ------------------------------------------------ files ("Turtle Files" directory)

    def _path(self, name):
        """A path inside the Turtle Files directory; Windows-style \\ separators allowed."""
        self.files_dir.mkdir(parents=True, exist_ok=True)
        p = (self.cwd / str(name).replace("\\", "/")).resolve()
        base = self.files_dir.resolve()
        if p != base and base not in p.parents:
            raise TurtleError(f'"{name}" is outside the Turtle Files directory')
        return p

    def _file_library(self):
        m = self
        lib = {}

        def warn(text):
            m.bridge.write.emit(text + "\n", "console")

        def fopen(name, mode):
            m.files_dir.mkdir(parents=True, exist_ok=True)
            p = m._path(name)
            mode = int(mode)
            try:
                if mode == 1:
                    f = open(p, "r", encoding="utf-8", errors="replace", newline="")
                elif mode == 2:
                    if not p.exists():
                        warn(f'File "{name}" does not exist, and so cannot be opened for appending')
                        return 0
                    f = open(p, "a", encoding="utf-8", newline="")
                elif mode == 4:
                    f = open(p, "w", encoding="utf-8", newline="")
                elif mode == 3:
                    if p.exists():
                        warn(f'File "{name}" already exists, and so cannot be opened as a new '
                             f'file for writing')
                        return 0
                    f = open(p, "w", encoding="utf-8", newline="")
                else:
                    raise TurtleError("File mode must be 1 (read), 2 (append), 3 (write) or 4 (rewrite)")
            except OSError:
                return 0
            handle = 1
            while handle in m.files:          # lowest free handle, as the original
                handle += 1
            m.files[handle] = f
            return handle

        def handle_file(h):
            f = m.files.get(int(h))
            if f is None:
                raise TurtleError(f"File handle {h} was not open")
            return f

        def fclose(h):
            if int(h) == 0:                   # closefile(0) closes every open file
                for f in m.files.values():
                    f.close()
                m.files.clear()
                return
            handle_file(h).close()
            del m.files[int(h)]

        def fwrite(h, s):
            handle_file(h).write(str(s))

        def fwriteline(h, s):
            handle_file(h).write(str(s) + "\r\n")

        def freadline(h):
            line = handle_file(h).readline()
            return line.rstrip("\r\n")

        def fread(h, maxlen):
            return handle_file(h).read(int(maxlen))

        def eof(h):
            f = handle_file(h)
            pos = f.tell()
            more = f.read(1)
            f.seek(pos)
            return more == ""

        def eoln(h):
            f = handle_file(h)
            pos = f.tell()
            nxt = f.read(1)
            f.seek(pos)
            return nxt in ("", "\r", "\n")

        def frestart(h):
            handle_file(h).seek(0)

        def fcopy(old, new):
            try:
                m._path(new).write_bytes(m._path(old).read_bytes())
                return True
            except OSError:
                return False

        def fmove(old, new):
            try:
                m._path(old).rename(m._path(new))
                return True
            except OSError:
                return False

        def fremove(name):
            try:
                m._path(name).unlink()
                return True
            except OSError:
                return False

        def isfile(name):
            return m._path(name).is_file()

        def isdir(name):
            return m._path(name).is_dir()

        def mkdir(name):
            try:
                m._path(name).mkdir()
                return True
            except OSError:
                return False

        def rmdir(name):
            try:
                m._path(name).rmdir()
                return True
            except OSError:
                return False

        def chdir(name):
            p = m._path(name)
            if p.is_dir():
                m.cwd = p

        def _check(name, code, is_dir):
            """checkfile / checkdir: code bits 0-1 = action (0 enquire, 1 delete, 2 create,
            3 recreate), 4 = notify if it exists, 16 = notify if not; the result adds
            64 if it existed on entry and 128 if it exists on exit."""
            import shutil
            p = m._path(name)
            code = int(code)
            exists = p.is_dir if is_dir else p.is_file
            on_entry = exists()
            action = code & 3
            try:
                if action in (1, 3) and on_entry:
                    if is_dir:
                        shutil.rmtree(p) if action == 3 else p.rmdir()
                    else:
                        p.unlink()
                if action in (2, 3) and not exists():
                    p.mkdir() if is_dir else p.touch()
            except OSError:
                pass
            on_exit = exists()
            what = "Directory" if is_dir else "File"
            if (code & 4 and on_exit) or (code & 16 and not on_exit):
                warn(f'{what} "{name}" ' + ("exists" if on_exit else "does not exist"))
            return (code & 63) | (64 if on_entry else 0) | (128 if on_exit else 0)

        def checkfile(name, code):
            return _check(name, code, False)

        def checkdir(name, code):
            return _check(name, code, True)

        def _finder(pattern, dirs):
            matches = sorted(q.name for q in m.cwd.glob(str(pattern)) if q.is_dir() == dirs)
            m._found = matches[1:]
            return matches[0] if matches else ""

        def findfirst(pattern, handle=0):
            return _finder(pattern, False)

        def finddir(pattern, handle=0):
            return _finder(pattern, True)

        def findnext(handle=0):
            found = getattr(m, "_found", [])
            return found.pop(0) if found else ""

        for fn in (fopen, fclose, fwrite, fwriteline, freadline, fread, eof, eoln, frestart,
                   fcopy, fmove, fremove, isfile, isdir, mkdir, rmdir, chdir, checkfile,
                   checkdir, findfirst, finddir, findnext):
            lib[fn.__name__] = fn
        lib["frename"] = fmove
        return lib


def error_message(e):
    """Run-time error text in the original's style."""
    if isinstance(e, ZeroDivisionError):
        return "Division by zero"
    if isinstance(e, RecursionError):
        return "Stack overflow - probable cause is unterminated recursion"
    if isinstance(e, TurtleError):
        return str(e)
    if isinstance(e, IndexError):
        return "Index out of range"
    if isinstance(e, NameError):
        return f"{e}".replace("name ", "Identifier ").replace("is not defined", "not recognised")
    return f"{type(e).__name__}: {e}"


def base_environment():
    return {"__name__": "__main__", "__builtins__": builtins}
