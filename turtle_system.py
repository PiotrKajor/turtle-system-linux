#!/usr/bin/env python3
"""Turtle System for Linux: a native re-implementation of the Turtle System (Python, Pascal and
BASIC modes), with semantics and drawing measured against the original."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QEvent, QRect, QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFontMetrics, QFont, QFontDatabase, QIcon, QKeySequence, QPainter,
                           QPen, QPixmap, QPolygonF, QSyntaxHighlighter, QTextCharFormat, QTextDocument,
                           QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QGridLayout,
                               QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget)

import formatting
from wraptabs import WrapTabWidget
import inspector
from tm import info as tm_info
from tm import pcode as pcode_mod
from tm.vm import TurtleVM
import themes

THEME = themes.THEMES["classic"]     # the active colour set (see MainWindow.apply_theme)
SYSTEM_LOOK = {}                    # the desktop's own style and palette, for the System theme

import lang_basic
import lang_pascal
import lang_python
from runtime import ALT, CTRL, KEY_CODES, QUERY_CODES, SHIFT, Machine

APP_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = APP_DIR / "examples"
REGISTRY = json.loads((APP_DIR / "data" / "builtins.json").read_text(encoding="utf-8"))

# the original's skeleton programs (from the executable)
LANGUAGES = {
    "python": {"module": lang_python, "label": "PYTHON", "name": "Python", "indent": "    ",
               "filter": "Turtle Python (*.tgy *.py)",
               "skeleton": '# Python skeleton program\nprint("Hello world!")\nvar1=100\n'
                           "colour(green)\nblot(var1)\n"},
    "pascal": {"module": lang_pascal, "label": "PASCAL", "name": "Pascal", "indent": "  ",
               "filter": "Turtle Pascal (*.tgp *.pas)",
               "skeleton": "{Pascal skeleton program}\n\nPROGRAM skeleton;\nVAR var1: integer;\n"
                           "BEGIN\n  writeln('Hello world!');\n  var1:=100;\n  colour(green);\n"
                           "  blot(var1)\nEND.\n"},
    "basic": {"module": lang_basic, "label": "BASIC", "name": "BASIC", "indent": "  ",
              "filter": "Turtle BASIC (*.tgb *.bas)",
              "skeleton": 'REM BASIC skeleton program\n\nPRINT("Hello world!")\nvar1%=100\n'
                          "COLOUR(GREEN)\nBLOT(var1%)\nEND\n"},
}
EXTENSION_LANGUAGE = {ext: lang for lang, cfg in LANGUAGES.items()
                      for ext in cfg["module"].EXTENSIONS}
ALL_FILTER = ("Turtle programs (*.tgy *.py *.tgp *.pas *.tgb *.bas);;"
              + ";;".join(cfg["filter"] for cfg in LANGUAGES.values()) + ";;All files (*)")

# Qt keys that don't share their number with the Windows virtual-key code
QT_TO_VK = {Qt.Key.Key_Backspace: 8, Qt.Key.Key_Tab: 9, Qt.Key.Key_Return: 13,
            Qt.Key.Key_Enter: 13, Qt.Key.Key_Shift: 16, Qt.Key.Key_Control: 17,
            Qt.Key.Key_Alt: 18, Qt.Key.Key_Pause: 19, Qt.Key.Key_CapsLock: 20,
            Qt.Key.Key_Escape: 27, Qt.Key.Key_Space: 32, Qt.Key.Key_PageUp: 33,
            Qt.Key.Key_PageDown: 34, Qt.Key.Key_End: 35, Qt.Key.Key_Home: 36,
            Qt.Key.Key_Left: 37, Qt.Key.Key_Up: 38, Qt.Key.Key_Right: 39, Qt.Key.Key_Down: 40,
            Qt.Key.Key_Insert: 45, Qt.Key.Key_Delete: 46, Qt.Key.Key_Meta: 91}
QT_TO_VK.update({getattr(Qt.Key, f"Key_F{i}"): 111 + i for i in range(1, 13)})

CURSORS = [Qt.CursorShape.BlankCursor, Qt.CursorShape.ArrowCursor, Qt.CursorShape.CrossCursor,
           Qt.CursorShape.IBeamCursor, Qt.CursorShape.SizeAllCursor, Qt.CursorShape.SizeBDiagCursor,
           Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeFDiagCursor,
           Qt.CursorShape.SizeHorCursor, Qt.CursorShape.UpArrowCursor, Qt.CursorShape.WaitCursor,
           Qt.CursorShape.ForbiddenCursor, Qt.CursorShape.PointingHandCursor,
           Qt.CursorShape.BusyCursor, Qt.CursorShape.WhatsThisCursor,
           Qt.CursorShape.SplitVCursor, Qt.CursorShape.SplitHCursor]


def modifiers(event):
    mods = event.modifiers()
    return ((SHIFT if mods & Qt.KeyboardModifier.ShiftModifier else 0)
            | (ALT if mods & Qt.KeyboardModifier.AltModifier else 0)
            | (CTRL if mods & Qt.KeyboardModifier.ControlModifier else 0))


def colour_swatch(rgb, size=14):
    pix = QPixmap(size, size)
    pix.fill(QColor.fromRgb(rgb & 0xFFFFFF))
    return pix


# ---------------------------------------------------------------- editor

class SourceHighlighter(QSyntaxHighlighter):
    """Syntax colouring for Turtle Python, Pascal and BASIC, in the original's colours."""

    KEYWORDS = {
        "python": set("and as assert break class continue def del elif else except finally for "
                      "from global if import in is lambda nonlocal not or pass raise return try "
                      "while with yield True False None".split()),
        "pascal": set("program var const begin end procedure function if then else while do for "
                      "to downto repeat until div mod and or not xor array of true false integer "
                      "boolean char string result".split()),
        "basic": set("IF THEN ELSE ENDIF FOR TO STEP NEXT WHILE ENDWHILE REPEAT UNTIL DEF PROC FN "
                     "ENDPROC LOCAL PRIVATE RETURN DIM CONST END AND OR EOR NOT DIV MOD TRUE FALSE "
                     "STRINGSIZE".split()),
    }
    IN_COMMENT, IN_TRIPLE, IN_PAREN_COMMENT = 1, 2, 3

    def __init__(self, doc, language):
        super().__init__(doc)
        self.language = language
        self.apply_theme()

    def apply_theme(self):
        self.keyword = QTextCharFormat()
        self.keyword.setForeground(QColor(THEME["keyword"]))
        self.keyword.setFontWeight(QFont.Weight.Bold)
        self.comment = QTextCharFormat()
        self.comment.setForeground(QColor(THEME["comment"]))
        self.comment.setFontItalic(True)
        self.string = QTextCharFormat()
        self.string.setForeground(QColor(THEME["string"]))
        self.query = QTextCharFormat()
        self.query.setForeground(QColor(THEME["query"]))
        self.number = QTextCharFormat()
        self.number.setForeground(QColor(THEME["number"]))
        self.rehighlight()

    def set_language(self, language):
        self.language = language
        self.rehighlight()

    def _close(self, text, i, closer, state):
        end = text.find(closer, i)
        if end < 0:
            self.setFormat(i, len(text) - i, self.comment if state != self.IN_TRIPLE
                           else self.string)
            self.setCurrentBlockState(state)
            return None
        self.setFormat(i, end + len(closer) - i, self.comment if state != self.IN_TRIPLE
                       else self.string)
        return end + len(closer)

    def highlightBlock(self, text):
        lang = self.language
        keywords = self.KEYWORDS[lang]
        quote = {"python": "'\"", "pascal": "'", "basic": '"'}[lang]
        i, n = 0, len(text)
        self.setCurrentBlockState(0)
        prev = self.previousBlockState()
        if prev == self.IN_COMMENT:
            i = self._close(text, 0, "}", self.IN_COMMENT)
        elif prev == self.IN_PAREN_COMMENT:
            i = self._close(text, 0, "*)", self.IN_PAREN_COMMENT)
        elif prev == self.IN_TRIPLE:
            i = self._close(text, 0, '"""', self.IN_TRIPLE)
        if i is None:
            return
        while i < n:
            c = text[i]
            if lang == "python" and c == "#" or lang == "pascal" and text.startswith("//", i):
                self.setFormat(i, n - i, self.comment)
                return
            if lang == "pascal" and c == "{":
                i = self._close(text, i, "}", self.IN_COMMENT)
                if i is None:
                    return
                continue
            if lang == "pascal" and text.startswith("(*", i):
                i = self._close(text, i, "*)", self.IN_PAREN_COMMENT)
                if i is None:
                    return
                continue
            if lang == "basic" and text.startswith("REM", i) and (i + 3 == n or not text[i + 3].isalnum()) \
                    and (i == 0 or not text[i - 1].isalnum()):
                self.setFormat(i, n - i, self.comment)
                return
            if c in quote:
                if lang == "python" and text.startswith('"""', i):
                    i = self._close(text, i + 3, '"""', self.IN_TRIPLE)
                    if i is None:
                        self.setFormat(0, n, self.string)
                        return
                    continue
                end = i + 1
                while end < n and text[end] != c:
                    end += 2 if (text[end] == "\\" and lang == "python") else 1
                end = min(end + 1, n)
                self.setFormat(i, end - i, self.string)
                i = end
            elif c.isalpha() or c == "_" or (c in "?\\" and i + 1 < n and text[i + 1].isalnum()):
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] in "_%$#"):
                    j += 1
                word = text[i:j]
                if c in "?\\":
                    self.setFormat(i, j - i, self.query)
                elif (word.lower() if lang == "pascal" else word) in keywords:
                    self.setFormat(i, j - i, self.keyword)
                i = j
            elif c.isdigit() or (c in "$&" and i + 1 < n and text[i + 1].isalnum()):
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] in "._"):
                    j += 1
                self.setFormat(i, j - i, self.number)
                i = j
            else:
                i += 1


class LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.gutter = LineNumbers(self)
        self.error_line = None
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_lines)
        self.language = "python"
        self.highlighter = SourceHighlighter(self.document(), self.language)
        self._update_gutter_width()

    def set_language(self, language):
        self.language = language
        self.highlighter.set_language(language)

    @property
    def indent_unit(self):
        return LANGUAGES[self.language]["indent"]

    def _opens_block(self, line):
        code = line.strip()
        if self.language == "python":
            return code.endswith(":") and not code.startswith("#")
        if self.language == "pascal":
            low = code.lower()
            return low.endswith(("begin", "then", "do", "repeat", "else", "var", "const"))
        return code.startswith(("FOR ", "WHILE ", "REPEAT", "DEF ", "ELSE")) or code.endswith("THEN")

    def set_font_size(self, size):
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(size)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._update_gutter_width()

    def gutter_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(THEME["gutter_bg"]))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                number = block.blockNumber() + 1
                p.setPen(QColor(THEME["error_text"]) if number == self.error_line
                         else QColor(THEME["gutter_fg"]))
                p.drawText(0, top, self.gutter.width() - 6, height, Qt.AlignmentFlag.AlignRight,
                           str(number))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()

    def mark_error(self, line):
        self.error_line = line
        if line:
            block = self.document().findBlockByNumber(line - 1)
            cursor = QTextCursor(block)
            self.setTextCursor(cursor)
            self.centerCursor()
        self._highlight_lines()
        self.gutter.update()

    def _highlight_lines(self):
        selections = []
        current = QTextEdit.ExtraSelection()
        current.format.setBackground(QColor(THEME["current_line"]))
        current.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        current.cursor = self.textCursor()
        current.cursor.clearSelection()
        selections.append(current)
        if self.error_line:
            err = QTextEdit.ExtraSelection()
            err.format.setBackground(QColor(THEME["error_line"]))
            err.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            err.cursor = QTextCursor(self.document().findBlockByNumber(self.error_line - 1))
            selections.append(err)
        self.setExtraSelections(selections)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Tab and not event.modifiers():
            self.insertPlainText(self.indent_unit)
            return
        if key == Qt.Key.Key_Backtab:
            self._dedent()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            line = self.textCursor().block().text()[:self.textCursor().positionInBlock()]
            indent = line[:len(line) - len(line.lstrip())]
            if self._opens_block(line):
                indent += self.indent_unit
            super().keyPressEvent(event)
            self.insertPlainText(indent)
            return
        if key == Qt.Key.Key_Backspace and not self.textCursor().hasSelection():
            cursor = self.textCursor()
            before = cursor.block().text()[:cursor.positionInBlock()]
            if before and not before.strip():
                unit = len(self.indent_unit)
                remove = (len(before) - 1) % unit + 1
                for _ in range(remove):
                    cursor.deletePreviousChar()
                return
        super().keyPressEvent(event)

    def _dedent(self):
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        remove = min(len(self.indent_unit), len(text) - len(text.lstrip(" ")))
        if remove:
            c = QTextCursor(block)
            c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, remove)
            c.removeSelectedText()

    def toggle_comment(self):
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart()).blockNumber()
        last = doc.findBlock(cursor.selectionEnd()).blockNumber()
        blocks = [doc.findBlockByNumber(i) for i in range(first, last + 1)]
        mark = {"python": "#", "pascal": "//", "basic": "REM"}[self.language]
        uncomment = all(b.text().lstrip().startswith(mark) for b in blocks if b.text().strip())
        cursor.beginEditBlock()
        for b in blocks:
            text = b.text()
            if not text.strip():
                continue
            pos = len(text) - len(text.lstrip())
            c = QTextCursor(b)
            c.movePosition(QTextCursor.MoveOperation.Right, n=pos)
            if uncomment:
                width = len(mark) + (1 if text[pos + len(mark):pos + len(mark) + 1] == " " else 0)
                c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor,
                               width)
                c.removeSelectedText()
            else:
                c.insertText(mark + " ")
        cursor.endEditBlock()


# ---------------------------------------------------------------- canvas

class CanvasView(QWidget):
    """Shows the machine's canvas scaled to fit, with coordinate rulers like the original."""

    def __init__(self, machine):
        super().__init__()
        self.m = machine
        self.frame = machine.frame()
        self.show_turtle = False
        self.target = QRectF()
        self.zoom = 1.0                 # 1 = the whole canvas fits the area
        self.center = (0.5, 0.5)
        self._pan = None                # (start point, visible centre) while dragging
        self.hand = False               # hand tool: the left button pans instead of clicking
        self.program_cursor = None      # the cursor the running program asked for
        self.setMinimumSize(200, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self._make_buttons()

    def _make_buttons(self):
        """Zoom and hand-tool buttons over the canvas, for touchpads and mice without a wheel."""
        self.buttons = []
        for text, tip, slot in [("\u2212", "Zoom out (Ctrl+-)", lambda: self.zoom_step(-1)),
                                ("+", "Zoom in (Ctrl++)", lambda: self.zoom_step(1)),
                                ("Fit", "Show the whole canvas (Ctrl+0)", lambda: self.zoom_step(0)),
                                ("\u270b", "Hand tool: drag the zoomed canvas with the left button\n"
                                 "(clicks are not passed to the program while it is on)", None)]:
            b = QToolButton(self)
            b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.ArrowCursor)
            if slot:
                b.clicked.connect(slot)
            self.buttons.append(b)
        self.hand_button = self.buttons[-1]
        self.hand_button.setCheckable(True)
        self.hand_button.toggled.connect(self.set_hand)
        self._sync_buttons()

    def _sync_buttons(self):
        zoomed = self.zoom > 1
        self.buttons[0].setEnabled(zoomed)
        self.buttons[2].setEnabled(zoomed)
        self.hand_button.setEnabled(zoomed)
        if not zoomed and self.hand:
            self.hand_button.setChecked(False)
        self._apply_cursor()

    def resizeEvent(self, event):
        a = self._area()
        x, y = a.right() - 4, a.top() + 4
        height = max(b.sizeHint().height() for b in self.buttons)
        for b in reversed(self.buttons):
            b.resize(b.sizeHint().width(), height)
            x -= b.width()
            b.move(int(x), int(y))
            x -= 2

    def set_hand(self, on):
        self.hand = on
        self._apply_cursor()

    def set_program_cursor(self, shape):
        self.program_cursor = shape
        self._apply_cursor()

    def _apply_cursor(self):
        if self._pan:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.hand and self.zoom > 1:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.program_cursor is not None:
            self.setCursor(self.program_cursor)
        else:
            self.unsetCursor()

    def zoom_step(self, step):
        """step > 0 zooms in, < 0 out, 0 fits the whole canvas; keeps the visible centre."""
        if step and self.zoom > 1:
            self.center = self._visible_center()
        z = self.set_zoom(1.0 if step == 0 else self.zoom * (1.25 if step > 0 else 0.8))
        self.window().statusBar().showMessage(f"Canvas zoom {z * 100:.0f}%", 2000)
        return z

    def refresh(self):
        """Take a new frame if the program has drawn and screen updating is on."""
        m = self.m
        if m.dirty and m.updating:
            self.frame = m.frame()
            self.bounds = (m.left, m.top, m.width, m.height)
            self.update()
            return True
        return False

    def _area(self):
        fm = self.fontMetrics()
        margin_left = fm.horizontalAdvance("-00000") + 6
        margin_top = fm.height() + 4
        return QRectF(margin_left, margin_top, max(1, self.width() - margin_left - 6),
                      max(1, self.height() - margin_top - 6))

    def _layout(self):
        """The image fills the area (keeping its proportions) times the zoom factor; when
        it is larger than the area, `center` (fractions of the image) is in the middle."""
        a = self._area()
        fw, fh = self.frame.width(), self.frame.height()
        scale = min(a.width() / fw, a.height() / fh) * self.zoom
        tw, th = fw * scale, fh * scale
        cx, cy = self.center
        left = a.left() if tw <= a.width() else \
            min(a.left(), max(a.right() - tw, a.left() + a.width() / 2 - cx * tw))
        top = a.top() if th <= a.height() else \
            min(a.top(), max(a.bottom() - th, a.top() + a.height() / 2 - cy * th))
        self.target = QRectF(left, top, tw, th)

    def set_zoom(self, zoom, anchor=None):
        """Zoom relative to fitting the area; anchor (widget point) stays under the cursor."""
        self._layout()
        t = self.target
        if anchor is not None and t.contains(anchor):
            self.center = ((anchor.x() - t.left()) / t.width(), (anchor.y() - t.top()) / t.height())
        elif zoom <= 1:
            self.center = (0.5, 0.5)
        self.zoom = max(1.0, min(32.0, zoom))
        self._sync_buttons()
        self.update()
        return self.zoom

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta and event.pixelDelta().isNull():
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * (1.25 if delta > 0 else 0.8), event.position())
            self.window().statusBar().showMessage(f"Canvas zoom {self.zoom * 100:.0f}%", 2000)
            return
        if self.zoom <= 1:
            return
        cx, cy = self._visible_center()
        a = self._area()
        t = self.target
        pixels = event.pixelDelta()
        if not pixels.isNull():         # touchpad: two-finger scrolling moves both ways
            self.center = (min(1, max(0, cx - pixels.x() / t.width())),
                           min(1, max(0, cy - pixels.y() / t.height())))
            self.update()
            return
        step = -delta / 120 * 0.1
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier or event.angleDelta().x():
            cx += step * a.width() / t.width()
        else:
            cy += step * a.height() / t.height()
        self.center = (min(1, max(0, cx)), min(1, max(0, cy)))
        self.update()

    def event(self, event):
        if (event.type() == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
            self.set_zoom(self.zoom * (1 + event.value()), event.position())
            self.window().statusBar().showMessage(f"Canvas zoom {self.zoom * 100:.0f}%", 2000)
            return True
        return super().event(event)

    def _visible_center(self):
        """The image point (fractions) shown in the middle of the area."""
        self._layout()
        t, a = self.target, self._area()
        return (a.center().x() - t.left()) / t.width(), (a.center().y() - t.top()) / t.height()

    def paintEvent(self, event):
        self._layout()
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window())
        t = self.target
        a = self._area()
        smooth = t.width() < self.frame.width()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        p.save()
        p.setClipRect(a.adjusted(-1, -1, 1, 1))
        p.drawImage(t, self.frame)
        p.setPen(QColor(THEME["canvas_border"]))
        p.drawRect(t.adjusted(-1, -1, 0, 0))
        if self.show_turtle:
            self._draw_turtle(p)
        p.restore()

        # rulers for the visible part of the canvas
        v = t.intersected(a)
        left, top, width, height = getattr(self, "bounds", (0, 0, 1000, 1000))
        fx0 = (v.left() - t.left()) / t.width()
        fx1 = (v.right() - t.left()) / t.width()
        fy0 = (v.top() - t.top()) / t.height()
        fy1 = (v.bottom() - t.top()) / t.height()
        p.setPen(QColor(THEME["ruler"]))
        fm = self.fontMetrics()
        for i in range(5):
            frac = i / 4
            gx = fx0 + frac * (fx1 - fx0)
            gy = fy0 + frac * (fy1 - fy0)
            vx = round(left + gx * width - (1 if gx >= 1 else 0))
            vy = round(top + gy * height - (1 if gy >= 1 else 0))
            x = v.left() + frac * v.width()
            y = v.top() + frac * v.height()
            label = str(vx)
            w = fm.horizontalAdvance(label)
            p.drawText(int(min(max(x - w / 2, v.left()), v.right() - w)), int(v.top() - 4), label)
            label = str(vy)
            p.drawText(int(v.left() - fm.horizontalAdvance(label) - 5),
                       int(min(max(y + fm.ascent() / 2, v.top() + fm.ascent()), v.bottom())), label)

    def _draw_turtle(self, p):
        m = self.m
        tx, ty, td, ta = m.turtle[0], m.turtle[1], m.turtle[2], m.turtle[3]
        px, py = m.to_pixel(tx, ty)
        sx = self.target.width() / self.frame.width()
        sy = self.target.height() / self.frame.height()
        x = self.target.left() + (px + 0.5) * sx
        y = self.target.top() + (py + 0.5) * sy
        if not self.target.adjusted(-20, -20, 20, 20).contains(x, y):
            return
        size = 12
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(x, y)
        p.rotate(td * 360 / ta)
        p.setPen(QPen(QColor("#202020"), 1.5))
        p.setBrush(QColor(0, 160, 0, 110))
        p.drawPolygon(QPolygonF([QRectF(-size * 0.6, size * 0.5, 0, 0).topLeft(),
                                 QRectF(0, -size, 0, 0).topLeft(),
                                 QRectF(size * 0.6, size * 0.5, 0, 0).topLeft()]))

    def _virtual(self, pos):
        t = self.target
        if t.width() <= 0:
            return -1, -1
        px = (pos.x() - t.left()) * self.frame.width() / t.width()
        py = (pos.y() - t.top()) * self.frame.height() / t.height()
        return self.m.to_virtual(px, py)

    def mouseMoveEvent(self, event):
        if self._pan:
            start, (cx, cy) = self._pan
            d = event.position() - start
            t = self.target
            self.center = (min(1, max(0, cx - d.x() / t.width())),
                           min(1, max(0, cy - d.y() / t.height())))
            self.update()
        x, y = self._virtual(event.position())
        self.m.mouse_event(x, y, "move")
        self.window().show_mouse(x, y, self.target.contains(event.position()))

    def _button(self, event):
        return {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2,
                Qt.MouseButton.MiddleButton: 4}.get(event.button(), 0)

    def mousePressEvent(self, event):
        self.setFocus()
        # a zoomed canvas pans with a middle-button drag, or a left-button drag with the
        # hand tool or when no program is running (a running program gets the clicks too,
        # except with the hand tool)
        button = event.button()
        left = button == Qt.MouseButton.LeftButton
        if self.zoom > 1 and (button == Qt.MouseButton.MiddleButton or
                              left and (self.hand or not self.m.running)):
            self._pan = (event.position(), self._visible_center())
            self._apply_cursor()
        if left and self.hand and self.zoom > 1:
            return
        x, y = self._virtual(event.position())
        self.m.mouse_event(x, y, "down", self._button(event), modifiers(event))

    def mouseReleaseEvent(self, event):
        if self._pan and not event.buttons() & (Qt.MouseButton.MiddleButton |
                                                Qt.MouseButton.LeftButton):
            self._pan = None
            self._apply_cursor()
        if event.button() == Qt.MouseButton.LeftButton and self.hand and self.zoom > 1:
            return
        x, y = self._virtual(event.position())
        self.m.mouse_event(x, y, "up", self._button(event), modifiers(event))

    def _vk(self, event):
        key = event.key()
        if key in QT_TO_VK:
            return QT_TO_VK[key]
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return int(key)
        return 0

    def keyPressEvent(self, event):
        vk = self._vk(event)
        if vk:
            self.m.key_event(vk, modifiers(event), True, event.text())

    def keyReleaseEvent(self, event):
        vk = self._vk(event)
        if vk and not event.isAutoRepeat():
            self.m.key_event(vk, modifiers(event), False)

    def focusNextPrevChild(self, forward):
        return False  # let Tab reach the program


# ---------------------------------------------------------------- console

class Console(QWidget):
    def __init__(self, machine):
        super().__init__()
        self.m = machine
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setMaximumBlockCount(5000)
        self.entry = QLineEdit(placeholderText="Input for the program (Enter to send)")
        self.entry.returnPressed.connect(self._send)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.text)
        layout.addWidget(self.entry)

    def _send(self):
        if self.m.running and self.m.waiting_input:
            self.m.submit_line(self.entry.text())
            self.entry.clear()

    def set_font(self, font):
        self.text.setFont(font)
        self.entry.setFont(font)


def append_text(widget, text, colour=None):
    cursor = widget.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    fmt = QTextCharFormat()
    if colour:
        fmt.setForeground(QColor(colour))
    cursor.insertText(text, fmt)
    widget.setTextCursor(cursor)
    widget.ensureCursorVisible()


def set_background(widget, rgb):
    """A program-chosen background (with readable text), or None for the theme's own."""
    if rgb is None:
        widget.setStyleSheet("")
        return
    rgb &= 0xFFFFFF
    widget.setStyleSheet(f"QPlainTextEdit {{ background: #{rgb:06x}; "
                         f"color: {themes.contrasting_text(rgb)}; }}")


# ---------------------------------------------------------------- main window

class FindReplaceDialog(QDialog):
    """Find and replace in the current program."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Find and replace")
        self.find = QLineEdit()
        self.replace = QLineEdit()
        self.case = QCheckBox("Match case")
        self.words = QCheckBox("Whole words")
        self.status = QLabel()
        grid = QGridLayout(self)
        grid.addWidget(QLabel("Find:"), 0, 0)
        grid.addWidget(self.find, 0, 1, 1, 3)
        grid.addWidget(QLabel("Replace with:"), 1, 0)
        grid.addWidget(self.replace, 1, 1, 1, 3)
        grid.addWidget(self.case, 2, 1)
        grid.addWidget(self.words, 2, 2)
        for col, (text, slot) in enumerate((("Find next", lambda: self.find_next(False)),
                                            ("Find previous", lambda: self.find_next(True)),
                                            ("Replace", self.replace_one),
                                            ("Replace all", self.replace_all))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            grid.addWidget(button, 3, col)
        grid.addWidget(self.status, 4, 0, 1, 4)
        self.find.returnPressed.connect(lambda: self.find_next(False))

    def _flags(self, backward=False):
        flags = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.words.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        return flags

    def find_next(self, backward=False):
        editor = self.main.editor
        text = self.find.text()
        if not text:
            return False
        if editor.find(text, self._flags(backward)):
            self.status.setText("")
            return True
        cursor = editor.textCursor()          # wrap around
        cursor.movePosition(QTextCursor.MoveOperation.End if backward
                            else QTextCursor.MoveOperation.Start)
        editor.setTextCursor(cursor)
        found = editor.find(text, self._flags(backward))
        self.status.setText("" if found else f'"{text}" not found')
        return found

    def _matches(self, selected):
        text = self.find.text()
        return selected == text if self.case.isChecked() else selected.lower() == text.lower()

    def replace_one(self):
        editor = self.main.editor
        cursor = editor.textCursor()
        if cursor.hasSelection() and self._matches(cursor.selectedText()):
            cursor.insertText(self.replace.text())
        self.find_next(False)

    def replace_all(self):
        editor = self.main.editor
        text = self.find.text()
        if not text:
            return
        cursor = editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        editor.setTextCursor(cursor)
        count = 0
        while editor.find(text, self._flags()):
            editor.textCursor().insertText(self.replace.text())
            count += 1
        cursor.endEditBlock()
        self.status.setText(f"{count} replacement{'s' if count != 1 else ''}")


MODE_PAGES = {
    "simple": ["canvas", "output", "help1", "help2"],
    "normal": ["canvas", "output", "help1", "help2", "usage"],
    "expert": ["canvas", "output", "help1", "help2", "usage", "comments", "vars"],
    "machine": ["canvas", "output", "help1", "help2", "usage", "comments", "syntax", "vars",
                "pcode", "memory"],
}
MODE_LEVELS = {"simple": {1}, "normal": {1, 2}, "expert": {1, 2, 3}, "machine": {1, 2, 3}}
SHORT_LABELS = {"canvas": "Canvas", "help1": "Help1", "help2": "Help2"}

HELP1 = {
    "python": """<h3>Turtle Python</h3>
<p>Python with integer arithmetic: <code>/</code> divides rounding to the nearest integer,
<code>//</code> rounds down, <code>%</code> is the remainder; integers are 32-bit.</p>
<p><b>Structures:</b> <code>if/elif/else</code>, <code>while</code>, <code>for i in range(...)</code>,
<code>def name(params):</code> with <code>return</code>, <code>global</code>, lists with
<code>.append .insert .del .remove .index .reverse .copy .extend</code>, strings with
<code>.upper .lower .find .replace ...</code></p>
<p><b>Turtle:</b> starts at (500,500) facing up (direction 0), angles clockwise in 360 units,
thickness 2, black. Variables <code>turtx turty turtd turta turtt turtc</code> can be read and
assigned.</p>
<p><b>Input:</b> <code>?key ?click ?mousex ?mousey ?lmouse ?kshift ?keybuffer</code> give values;
<code>\\escape \\a \\key \\lmouse ...</code> are codes for <code>detect reset status</code>.</p>""",
    "pascal": """<h3>Turtle Pascal</h3>
<pre>program Name;
const N=10;
var i: integer; s: string; a: array[1..N] of boolean;
procedure p(x: integer; var y: integer);
begin ... end;
function f(c: char): integer;
begin result:=ord(c) end;
begin
  for i:=1 to N do begin ... end;
  while cond do ...;  repeat ... until cond;
  if cond then ... else ...
end.</pre>
<p>Types: integer, boolean, char, string[n] (default 64), arrays. Operators:
<code>+ - * / div mod</code>, <code>and or xor not</code> (bitwise on integers),
<code>= &lt;&gt; &lt; &gt; &lt;= &gt;=</code>. Strings are indexed from 1.
<code>inc(x) dec(x)</code>; <code>writeln readln</code>.</p>""",
    "basic": """<h3>Turtle BASIC</h3>
<pre>CONST N%=10
DIM a%(N%)
PROCdraw(100)
x%=FNdouble(4)
END

DEF PROCdraw(size%)
  LOCAL i%
  FOR i%=1 TO 4 STEP 1
    FORWARD(size%)
    RIGHT(90)
  NEXT
ENDPROC

DEF FNdouble(n%)
=n%*2</pre>
<p>Variables end in <code>%</code> (integer) or <code>$</code> (string). TRUE=-1, FALSE=0.
IF ... THEN ... ELSE ... ENDIF (or on one line), WHILE ... ENDWHILE, REPEAT ... UNTIL.
<code>RETURN</code> parameters are passed by reference; LOCAL and PRIVATE variables.</p>""",
}


class DetachedWindow(QWidget):
    """A separate top-level window for the canvas or the tabs."""

    def __init__(self, title, main):
        super().__init__(None, Qt.WindowType.Window)
        self.main = main
        self.setWindowTitle(title)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(2, 2, 2, 2)
        self.content = None

    def put(self, widget):
        self.content = widget
        self.box.addWidget(widget)
        widget.show()

    def take(self):
        widget, self.content = self.content, None
        if widget is not None:
            self.box.removeWidget(widget)
        return widget

    def closeEvent(self, event):
        # closing a detached window re-integrates its content
        event.ignore()
        if self.content is self.main.canvas_split:
            self.main.canvas_actions["integrated"].setChecked(True)
        elif self.content is self.main.right:
            self.main.set_wide_editor(False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("turtle-linux", "TurtleSystem")
        self.machine = Machine()
        self.font_size = int(self.settings.value("fontSize", 12))
        self.docs = QTabWidget()
        self.docs.setTabPosition(QTabWidget.TabPosition.South)
        self.docs.setTabsClosable(True)
        self.docs.setMovable(True)
        self.docs.tabCloseRequested.connect(self.close_program)
        self.docs.currentChanged.connect(self._doc_changed)

        self.title_bar = QLabel()
        self.title_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.language = self.settings.value("language", "python")
        if self.language not in LANGUAGES:
            self.language = "python"
        lang = QLabel(f" {LANGUAGES[self.language]['label']} ")
        self.language_label = lang

        header = QHBoxLayout()
        header.setSpacing(0)
        header.addWidget(lang)
        header.addWidget(self.title_bar, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addLayout(header)
        left_layout.addWidget(self.docs)

        self.run_button = QPushButton("RUN")
        self.run_button.setStyleSheet("font-weight: bold; padding: 2px 14px;")
        self.run_button.clicked.connect(self.toggle_run)
        self.status_labels = {name: QLabel() for name in ("x", "y", "d", "t", "c", "mouse")}
        self.attr_widget = QWidget()
        attrs = QHBoxLayout(self.attr_widget)
        attrs.setContentsMargins(0, 0, 0, 0)
        for name in ("x", "y", "d", "t"):
            attrs.addSpacing(10)
            attrs.addWidget(self.status_labels[name])
        attrs.addSpacing(10)
        attrs.addWidget(QLabel("<b>Colour</b>"))
        attrs.addWidget(self.status_labels["c"])
        attrs.addStretch(1)
        attrs.addWidget(self.status_labels["mouse"])
        status = QHBoxLayout()
        status.addWidget(self.run_button)
        status.addWidget(self.attr_widget, 1)

        self.canvas = CanvasView(self.machine)
        self.console = Console(self.machine)
        canvas_split = QSplitter(Qt.Orientation.Vertical)
        canvas_split.addWidget(self.canvas)
        canvas_split.addWidget(self.console)
        canvas_split.setStretchFactor(0, 4)
        canvas_split.setStretchFactor(1, 1)
        self.canvas_split = canvas_split

        self.output = QPlainTextEdit(readOnly=True)
        self.help_table = self._build_help()
        help_tab = QWidget()
        help_layout = QVBoxLayout(help_tab)
        help_top = QHBoxLayout()
        help_filter = QLineEdit(placeholderText="Filter commands…")
        help_filter.textChanged.connect(self._filter_help)
        self.help_filter = help_filter
        help_top.addWidget(help_filter, 1)
        self.help_levels = {}
        for level, text in ((1, "Simple"), (2, "Intermediate"), (3, "Advanced")):
            box = QCheckBox(text, checked=True)
            box.toggled.connect(lambda _=False: self._filter_help(self.help_filter.text()))
            self.help_levels[level] = box
            help_top.addWidget(box)
        help_layout.addLayout(help_top)
        help_layout.addWidget(self.help_table)
        self.help1 = QTextBrowser()

        self.program_info = {}
        self.usage_tab = inspector.UsageTab()
        self.comments_tab = inspector.CommentsTab()
        self.syntax_tab = inspector.SyntaxTab()
        self.vars_tab = inspector.VarsSubsTab()
        self.pcode_tab = inspector.PCodeTab(lambda: self.program_info)
        self.memory_tab = inspector.MemoryTab(self.machine, lambda: self.program_info)
        # name -> (widget, label); the order is the original's
        self.pages = {"canvas": (canvas_split, "Canvas && Console"),
                      "output": (self.output, "Output"),
                      "help1": (self.help1, "QuickHelp 1"),
                      "help2": (help_tab, "QuickHelp 2"),
                      "usage": (self.usage_tab, "Usage"),
                      "comments": (self.comments_tab, "Comments"),
                      "syntax": (self.syntax_tab, "Syntax"),
                      "vars": (self.vars_tab, "Vars/Subs"),
                      "pcode": (self.pcode_tab, "PCode"),
                      "memory": (self.memory_tab, "Memory")}
        self.hidden_tabs = set(json.loads(self.settings.value("hiddenTabs", "[]")))
        self.mode = self.settings.value("mode", "normal")
        self.canvas_mode = self.settings.value("canvasMode", "integrated")
        self.wide_editor = self.settings.value("wideEditor", "false") == "true"

        self.tabs = WrapTabWidget()          # tab buttons wrap onto more rows, never scroll
        top = self.settings.value("tabsTop", "false") == "true"
        self.tabs.setTabPosition(QTabWidget.TabPosition.North if top
                                 else QTabWidget.TabPosition.South)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.right = QWidget()
        right_layout = QVBoxLayout(self.right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.addLayout(status)
        right_layout.addWidget(self.tabs)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(left)
        self.split.addWidget(self.right)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 1)
        self.setCentralWidget(self.split)
        self.canvas_window = DetachedWindow("Turtle Canvas", self)
        self.tabs_window = DetachedWindow("Turtle Tabs", self)
        self.analysis_timer = QTimer(self, singleShot=True, interval=600)
        self.analysis_timer.timeout.connect(self._update_source_tabs)

        self._apply_font_size()
        self._build_menus()

        bridge = self.machine.bridge
        bridge.write.connect(self._on_write)
        bridge.clear.connect(self._on_clear)
        bridge.want_input.connect(self._on_want_input)
        bridge.cursor.connect(self._on_cursor)
        bridge.finished.connect(self._on_finished)

        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1400, 900)
        for key, splitter in (("split", self.split), ("canvasSplit", canvas_split)):
            state = self.settings.value(key)
            if state:
                splitter.restoreState(state)

        self._new_doc(self.language)
        self.set_language(self.language)
        self.apply_theme(self.settings.value("theme", "system"))
        self.apply_layout()
        self.set_mode(self.mode)
        self.set_show_attributes(self.settings.value("showAttributes", "true") == "true")
        last = self.settings.value("lastFile")
        if last and Path(last).is_file():
            self.load(Path(last))
        else:
            self.new_program(confirm=False)

    # ------------------------------------------------ documents (one tab per program)

    @property
    def editor(self):
        return self.docs.currentWidget()

    @property
    def path(self):
        return self.editor.path

    @path.setter
    def path(self, value):
        self.editor.path = value
        self._label_doc(self.editor)

    def _new_doc(self, language, text=None, path=None):
        editor = CodeEditor()
        editor.path = path
        editor.doc_language = language
        editor.stored_copy = None
        editor.title = None
        editor.example = None
        editor.set_font_size(self.font_size)
        editor.set_language(language)
        editor.document().modificationChanged.connect(self._update_title)
        editor.textChanged.connect(lambda e=editor: e.error_line and e.mark_error(None))
        editor.textChanged.connect(lambda: self.analysis_timer.start())
        editor.setPlainText(LANGUAGES[language]["skeleton"] if text is None else text)
        editor.document().setModified(False)
        if hasattr(self, "theme_actions"):
            editor.setStyleSheet(f"QPlainTextEdit {{ background: {THEME['editor_bg']}; "
                                 f"color: {THEME['editor_fg']}; }}")
        index = self.docs.addTab(editor, "")
        self.docs.setCurrentIndex(index)
        self._label_doc(editor)
        return editor

    def _label_doc(self, editor):
        index = self.docs.indexOf(editor)
        if index < 0:
            return
        ext = {"python": "py", "pascal": "pas", "basic": "bas"}[editor.doc_language]
        name = editor.path.stem if editor.path else (editor.title or "untitled")
        star = "*" if editor.document().isModified() else ""
        self.docs.setTabText(index, f"{index + 1} {ext} {star}{name}")
        self.docs.setTabToolTip(index, str(editor.path) if editor.path else name)

    def _pristine(self):
        """The current program is an untouched skeleton or empty: reuse its tab."""
        e = self.editor
        return (e is not None and e.path is None and not e.document().isModified()
                and e.toPlainText() in ("", LANGUAGES[e.doc_language]["skeleton"]))

    def _doc_changed(self, _index):
        e = self.editor
        if e is None:
            return
        if e.doc_language != self.language:
            self.set_language(e.doc_language)
        for i in range(self.docs.count()):
            self._label_doc(self.docs.widget(i))
        self.program_info = {}
        self._update_title()
        self.analysis_timer.start()

    def close_program(self, index=None):
        index = self.docs.currentIndex() if index is None or index is False else index
        editor = self.docs.widget(index)
        if editor is None:
            return
        self.docs.setCurrentIndex(index)
        if not self._confirm_discard():
            return
        self.docs.removeTab(index)
        editor.deleteLater()
        if self.docs.count() == 0:
            self._new_doc(self.language)
        self._doc_changed(self.docs.currentIndex())

    def close_all(self):
        """Close all programs and reset displays, without prompts."""
        if self.machine.running:
            self.machine.stop()
        while self.docs.count():
            w = self.docs.widget(0)
            self.docs.removeTab(0)
            w.deleteLater()
        self._new_doc(self.language)
        self.reset_all()

    # ------------------------------------------------ setup

    def _build_menus(self):
        bar = self.menuBar()

        def add(menu, text, slot, shortcut=None, checkable=False, checked=False):
            action = QAction(text, self, checkable=checkable)
            if checkable:
                action.setChecked(checked)
                action.toggled.connect(slot)
            else:
                action.triggered.connect(slot)
            if shortcut:
                action.setShortcuts(shortcut if isinstance(shortcut, list) else [shortcut])
            menu.addAction(action)
            return action

        m = bar.addMenu("&File")
        add(m, "&New program", lambda: self.new_program(skeleton=False), QKeySequence("Ctrl+N"))
        add(m, "S&keleton program", lambda: self.new_program(skeleton=True),
            QKeySequence("Ctrl+K"))
        add(m, "&Open program…", self.open_dialog, QKeySequence("Ctrl+O"))
        add(m, "&Save program", self.save, QKeySequence("Ctrl+S"))
        add(m, "Save &As…", self.save_as, QKeySequence("Ctrl+Alt+S"))
        add(m, "&Close program", lambda: self.close_program(), QKeySequence("Ctrl+W"))
        m.addSeparator()
        add(m, "Copy canvas &graphic to clipboard", self.copy_canvas, QKeySequence("Ctrl+G"))
        add(m, "Save canvas graphic to &file…", self.save_canvas)
        m.addSeparator()
        add(m, "Print program…", lambda: self.print_text(self.editor.toPlainText()))
        add(m, "Print text in Output tab…", lambda: self.print_text(self.output.toPlainText()))
        add(m, "Print text in Console…", lambda: self.print_text(self.console.text.toPlainText()))
        add(m, "Show CSV/TSV saving buttons within data tables", inspector.show_csv_buttons,
            checkable=True)
        m.addSeparator()
        add(m, "Close all programs and reset displays (no prompts)", self.close_all)
        add(m, "E&xit Turtle System", self.close, QKeySequence.StandardKey.Quit)

        m = bar.addMenu("&Edit")
        add(m, "&Undo", lambda: self.editor.undo(), QKeySequence("Ctrl+Z"))
        add(m, "&Redo", lambda: self.editor.redo(), QKeySequence("Ctrl+Y"))
        m.addSeparator()
        add(m, "Cu&t", lambda: self.editor.cut(), QKeySequence("Ctrl+X"))
        add(m, "&Copy", lambda: self.editor.copy(), QKeySequence("Ctrl+C"))
        add(m, "&Paste", lambda: self.editor.paste(), QKeySequence("Ctrl+V"))
        m.addSeparator()
        add(m, "Select &All", lambda: self.editor.selectAll(), QKeySequence("Ctrl+A"))
        add(m, "&Find and replace…", self.find_replace, QKeySequence("Ctrl+F"))
        m.addSeparator()
        add(m, "Auto-format program", self.auto_format)
        add(m, "Remove any comments", self.remove_comments)
        self.remove_hints_action = add(m, "Remove any type hints", self.remove_hints)
        add(m, "Toggle c&omment on selected lines", lambda: self.editor.toggle_comment(),
            QKeySequence("Ctrl+/"))
        m.addSeparator()
        add(m, "Store copy of program", self.store_copy)
        self.restore_action = add(m, "Restore previous version", self.restore_copy)

        def radio(menu, items, current, slot):
            group = QActionGroup(self)
            actions = {}
            for key, text, shortcut in items:
                action = add(menu, text, lambda on, k=key: on and slot(k),
                             QKeySequence(shortcut) if shortcut else None, checkable=True,
                             checked=(key == current))
                group.addAction(action)
                actions[key] = action
            return actions

        m = bar.addMenu("&View")
        self.canvas_actions = radio(m, [
            ("integrated", "Integrated canvas", "Ctrl+I"),
            ("detached", "Detached resizable canvas", "Ctrl+D"),
            ("middle", "(Middle of screen, integrated canvas)", "Ctrl+M"),
            ("large", "(Large detached canvas - up to 1000x1000 pixels)", "Ctrl+L")],
            self.canvas_mode, self.set_canvas_mode)
        m.addSeparator()
        radio(m, [(False, "Narrow editor, integrated tabs", "Ctrl+Alt+N"),
                  (True, "Wide editor, tabs in separate window", "Ctrl+Alt+W")],
              self.wide_editor, self.set_wide_editor)
        add(m, "Place Tabs at top rather than bottom", self.set_tabs_top, checkable=True,
            checked=self.tabs.tabPosition() == QTabWidget.TabPosition.North)
        add(m, "Display Turtle attributes above Canvas", self.set_show_attributes,
            checkable=True, checked=self.settings.value("showAttributes", "true") == "true")
        m.addSeparator()
        add(m, "Large default font for program", self.set_large_font, QKeySequence("Ctrl+Alt+L"),
            checkable=True, checked=self.settings.value("largeFont", "false") == "true")
        add(m, "Zoom &In (program text, or Canvas under the mouse)", lambda: self.zoom(1),
            [QKeySequence("Ctrl+="), QKeySequence("Ctrl++"), QKeySequence("Ctrl+Shift++"),
             QKeySequence("Ctrl+Shift+=")])
        add(m, "Zoom &Out", lambda: self.zoom(-1), [QKeySequence("Ctrl+-"),
                                                    QKeySequence("Ctrl+Shift+_")])
        add(m, "Normal size (zoom 100%)", lambda: self.zoom(0), QKeySequence("Ctrl+0"))
        m.addSeparator()
        self.mode_actions = radio(m, [("simple", "Simple Mode", None), ("normal", "Normal Mode", None),
                                      ("expert", "Expert Mode", None),
                                      ("machine", "Machine Mode", None)],
                                  self.mode, self.set_mode)

        self.tabs_menu = bar.addMenu("&Tabs")
        add(self.tabs_menu, "HIDE TEXT OUTPUT TAB", lambda: self.hide_group(["output"]))
        self.tabs_menu.addSeparator()
        add(self.tabs_menu, "HIDE QUICK HELP TABS", lambda: self.hide_group(["help1", "help2"]))
        self.tab_actions = {}
        for key, text in (("help1", "Quick Help 1"), ("help2", "Quick Help 2"), (None, None),
                          ("hide-source", "HIDE SOURCE CODE TABS"), ("usage", "Usage of Commands"),
                          ("comments", "Comments"), (None, None),
                          ("hide-compiled", "HIDE COMPILED CODE TABS"),
                          ("syntax", "Syntax Tables"), ("vars", "Variables & Subroutines"),
                          ("pcode", "PCode and Trace"), ("memory", "Memory")):
            if key is None:
                self.tabs_menu.addSeparator()
            elif key == "hide-source":
                add(self.tabs_menu, text, lambda: self.hide_group(["usage", "comments"]))
            elif key == "hide-compiled":
                add(self.tabs_menu, text,
                    lambda: self.hide_group(["syntax", "vars", "pcode", "memory"]))
            else:
                self.tab_actions[key] = add(self.tabs_menu, text,
                                            lambda on, k=key: self.set_tab_shown(k, on),
                                            checkable=True, checked=key not in self.hidden_tabs)

        self.compile_menu = c = bar.addMenu("&Compile")
        add(c, "Compile to Turtle Machine PCode", self.compile_only, QKeySequence("Ctrl+F9"))
        c.addSeparator()
        add(c, "Save Turtle Machine PCode file (JSON)…", lambda: self.save_pcode("json"))
        self.autosave_json = add(c, "Auto-save PCode file on compiling (JSON)",
                                 lambda on: None, checkable=True)
        add(c, "Save Turtle Machine PCode file (binary)…", lambda: self.save_pcode("tmc"))
        self.autosave_tmc = add(c, "Auto-save PCode file on compiling (binary)",
                                lambda on: None, checkable=True)
        c.addSeparator()
        for text in ("Automatically set up default key buffer (length 32)",
                     "Treat Turtle attributes as global variables",
                     "Initialize local variables (to zero/false)",
                     "Allow quick string parameter copying with CSTR",
                     "Allow quick list creation with LIAD", None,
                     "Turtle machine using separate Return Stack",
                     "Turtle machine using separate Memory Control Stack",
                     "Turtle machine using Subroutine Register Stack"):
            if text is None:
                c.addSeparator()
                continue
            a = add(c, text, lambda on: None, checkable=True, checked=True)
            a.setEnabled(False)              # the compilers implement the default setup

        m = bar.addMenu("&Run")
        add(m, "&Run program", self.run_program, [QKeySequence("Ctrl+R"), QKeySequence("F9")])
        add(m, "&Halt program", self.halt, QKeySequence("Ctrl+H"))
        self.pause_action = add(m, "&Pause program", self.toggle_pause, QKeySequence("Ctrl+P"),
                                checkable=True)
        add(m, "Reset Canvas, Console, and Output", self.reset_all, QKeySequence("Ctrl+Alt+R"))
        m.addSeparator()
        add(m, "Load and Run PCode file (ignoring source code)…", self.load_run_pcode)
        m.addSeparator()
        self.show_canvas_on_run = add(m, "Show Canvas on RUN", lambda on: self.settings.setValue(
            "showCanvasOnRun", "true" if on else "false"), checkable=True,
            checked=self.settings.value("showCanvasOnRun", "true") == "true")
        self.show_output_on_text = add(m, "Show Output tab when text output is generated",
                                       lambda on: self.settings.setValue(
                                           "showOutputOnText", "true" if on else "false"),
                                       checkable=True, checked=self.settings.value(
                                           "showOutputOnText", "false") == "true")
        m.addSeparator()
        self.trace_action = add(m, "Trace on run", self.set_trace, QKeySequence("Ctrl+T"),
                                checkable=True)
        m.addSeparator()
        self.autodel_action = add(m, "Auto-delete temporary Heap strings (by activating HCLR)",
                                  lambda on: None, checkable=True, checked=True)
        add(m, "Prevent memory stack collision with Heap", lambda on: None, checkable=True,
            checked=True).setEnabled(False)
        add(m, "Range-check all references to array elements", lambda on: None, checkable=True,
            checked=True).setEnabled(False)

        m = bar.addMenu("&Options")
        size = int(self.settings.value("canvasSize", 1000))
        self.machine.default_size = size
        radio(m, [(500, "Start programs with 500x500 Canvas and Resolution", None),
                  (1000, "Start programs with 1000x1000 Canvas and Resolution", None),
                  (2000, "Start programs with 2000x2000 Canvas and Resolution", None)],
              size, self.set_canvas_size)
        m.addSeparator()
        self.machine.dark_canvas = self.settings.value("darkCanvas", "true") == "true"
        self.machine.reset()
        add(m, "Start programs with black Canvas and white pen", self.set_dark_canvas,
            checkable=True, checked=self.machine.dark_canvas)
        m.addSeparator()
        self.auto_compile = add(m, "Auto-Compile on loading", lambda on: self.settings.setValue(
            "autoCompile", "true" if on else "false"), checkable=True,
            checked=self.settings.value("autoCompile", "false") == "true")
        self.auto_run = add(m, "Auto-Run on loading", lambda on: self.settings.setValue(
            "autoRun", "true" if on else "false"), checkable=True,
            checked=self.settings.value("autoRun", "false") == "true")
        self.auto_format_load = add(m, "Auto-Format on loading", lambda on: self.settings.setValue(
            "autoFormat", "true" if on else "false"), checkable=True,
            checked=self.settings.value("autoFormat", "false") == "true")
        m.addSeparator()
        add(m, "Specify base directory for file operations…", self.choose_files_dir)
        add(m, "Show &Turtle on Canvas", self._set_show_turtle, checkable=True,
            checked=self.settings.value("showTurtle", "false") == "true")

        m = bar.addMenu("T&heme")
        self.theme_actions = radio(m, [(key, themes.NAMES[key], None) for key in themes.ORDER],
                                   self.settings.value("theme", "system"), self.apply_theme)

        self.language_menu = bar.addMenu("&Language")
        group = QActionGroup(self)
        self.language_actions = {}
        for lang, cfg in LANGUAGES.items():
            action = add(self.language_menu, cfg["name"], lambda on, l=lang: on and
                         self.change_language(l), checkable=True, checked=(lang == self.language))
            group.addAction(action)
            self.language_actions[lang] = action

        self.examples_menu = bar.addMenu("E&xamples")
        files_dir = self.settings.value("filesDir")
        if files_dir:
            self.machine.files_dir = Path(files_dir)

        m = bar.addMenu("&Help")
        add(m, "&QuickHelp", lambda: self.show_page("help2"), QKeySequence("F1"))
        add(m, "&About", self.about)

    # ------------------------------------------------ view: layout, tabs and modes

    def show_page(self, key):
        widget = self.pages[key][0]
        if key == "canvas" and self.canvas_mode in ("detached", "large"):
            self.canvas_window.raise_()
            return
        index = self.tabs.indexOf(widget)
        if index < 0:
            if key in self.hidden_tabs:
                self.hidden_tabs.discard(key)
                if key in self.tab_actions:
                    self.tab_actions[key].setChecked(True)
            self.rebuild_tabs()
            index = self.tabs.indexOf(widget)
        if index >= 0:
            self.tabs.setCurrentIndex(index)
        if self.wide_editor:
            self.tabs_window.raise_()

    def rebuild_tabs(self):
        current = self.tabs.currentWidget()
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for key in MODE_PAGES[self.mode]:
            if key in self.hidden_tabs:
                continue
            if key == "canvas" and self.canvas_mode in ("detached", "large"):
                continue
            widget, label = self.pages[key]
            if self.mode == "machine":
                label = SHORT_LABELS.get(key, label)
            self.tabs.addTab(widget, label)
        self.tabs.blockSignals(False)
        index = self.tabs.indexOf(current) if current is not None else -1
        self.tabs.setCurrentIndex(max(index, 0))

    def apply_layout(self):
        """Place the canvas and the tabs according to the View settings."""
        detached = self.canvas_mode in ("detached", "large")
        if detached and self.canvas_window.content is None:
            index = self.tabs.indexOf(self.canvas_split)
            if index >= 0:
                self.tabs.removeTab(index)
            self.canvas_window.put(self.canvas_split)
            if self.canvas_mode == "large":
                margin = self.canvas.fontMetrics().height() * 4
                self.canvas_window.resize(1000 + margin, 1000 + margin + 160)
            elif not self.canvas_window.isVisible():
                self.canvas_window.resize(700, 760)
            self.canvas_window.show()
        elif not detached and self.canvas_window.content is not None:
            self.canvas_window.take()
            self.canvas_window.hide()
        if self.canvas_mode == "large":
            margin = self.canvas.fontMetrics().height() * 4
            self.canvas_window.resize(1000 + margin, 1000 + margin + 160)
        if self.wide_editor and self.tabs_window.content is None:
            self.tabs_window.put(self.right)
            self.tabs_window.resize(max(600, self.width() // 2), self.height())
            self.tabs_window.show()
        elif not self.wide_editor and self.tabs_window.content is not None:
            self.tabs_window.take()
            self.tabs_window.hide()
            self.split.addWidget(self.right)
            self.right.show()
        self.rebuild_tabs()
        if self.canvas_mode == "middle":
            screen = self.screen().availableGeometry()
            frame = self.frameGeometry()
            frame.moveCenter(screen.center())
            self.move(frame.topLeft())

    def apply_theme(self, key):
        global THEME
        if key not in themes.NAMES:
            key = "system"
        self.settings.setValue("theme", key)
        app = QApplication.instance()
        if key == "system":
            app.setStyle(SYSTEM_LOOK.get("style", "Fusion"))
            app.setPalette(SYSTEM_LOOK.get("palette", app.palette()))
            THEME = themes.resolve(key, app.palette())
        else:
            THEME = themes.resolve(key, app.palette())
            app.setStyle("Fusion")
            app.setPalette(themes.palette(THEME))
        # a theme may bring its own interface font (Green terminal: monospace)
        if "font" not in SYSTEM_LOOK:
            SYSTEM_LOOK["font"] = app.font()
        ui_font = QFont(SYSTEM_LOOK["font"])
        if THEME.get("ui_font"):
            ui_font.setFamily(THEME["ui_font"])
        app.setFont(ui_font)
        fm = QFontMetrics(ui_font)
        for name, sample in (("x", "X -00000"), ("y", "Y -00000"), ("d", "Direction 000/000"),
                             ("t", "Thickness -000")):
            self.status_labels[name].setMinimumWidth(int(fm.horizontalAdvance(sample) * 1.15))
        self.language_label.setStyleSheet(
            f"background: {THEME['lang_bg']}; color: {THEME['lang_fg']}; font-weight: bold; "
            "font-style: italic; padding: 2px;")
        self.title_bar.setStyleSheet(f"background: {THEME['title_bg']}; color: {THEME['title_fg']};"
                                     " font-weight: bold; padding: 2px;")
        for i in range(self.docs.count()):
            e = self.docs.widget(i)
            e.setStyleSheet(f"QPlainTextEdit {{ background: {THEME['editor_bg']}; "
                            f"color: {THEME['editor_fg']}; }}")
            e.highlighter.apply_theme()
            e._highlight_lines()
            e.gutter.update()
        if self.machine.running:
            self.run_button.setStyleSheet(f"font-weight: bold; padding: 2px 14px; "
                                          f"color: {THEME['error_text']};")
        for widget in (self.console.text, self.output):
            if "background" not in widget.styleSheet():
                widget.setStyleSheet("")
        self.canvas.update()
        if hasattr(self, "theme_actions"):
            self.theme_actions[key].setChecked(True)

    def set_canvas_mode(self, mode):
        self.canvas_mode = mode
        self.settings.setValue("canvasMode", mode)
        self.apply_layout()

    def set_wide_editor(self, wide):
        self.wide_editor = bool(wide)
        self.settings.setValue("wideEditor", "true" if self.wide_editor else "false")
        if not wide and hasattr(self, "tabs_window"):
            for action in self.findChildren(QAction):
                if action.text() == "Narrow editor, integrated tabs":
                    action.setChecked(True)
        self.apply_layout()

    def set_tabs_top(self, top):
        self.tabs.setTabPosition(QTabWidget.TabPosition.North if top
                                 else QTabWidget.TabPosition.South)
        self.settings.setValue("tabsTop", "true" if top else "false")

    def set_show_attributes(self, on):
        self.attr_widget.setVisible(on)
        self.settings.setValue("showAttributes", "true" if on else "false")

    def set_large_font(self, on):
        self.settings.setValue("largeFont", "true" if on else "false")
        self.font_size = 16 if on else 12
        for i in range(self.docs.count()):
            self.docs.widget(i).set_font_size(self.font_size)
        self._apply_font_size()
        self.settings.setValue("fontSize", self.font_size)

    def set_mode(self, mode):
        self.mode = mode if mode in MODE_PAGES else "normal"
        self.settings.setValue("mode", self.mode)
        machine = self.mode == "machine"
        self.tabs_menu.menuAction().setVisible(machine)
        self.compile_menu.menuAction().setVisible(machine)
        levels = MODE_LEVELS[self.mode]
        for level, box in self.help_levels.items():
            box.blockSignals(True)
            box.setChecked(level in levels)
            box.blockSignals(False)
        self._filter_help(self.help_filter.text())
        self.rebuild_tabs()
        self._update_source_tabs()

    def hide_group(self, keys):
        for key in keys:
            self.hidden_tabs.add(key)
            if key in self.tab_actions:
                self.tab_actions[key].blockSignals(True)
                self.tab_actions[key].setChecked(False)
                self.tab_actions[key].blockSignals(False)
        self.settings.setValue("hiddenTabs", json.dumps(sorted(self.hidden_tabs)))
        self.rebuild_tabs()

    def set_tab_shown(self, key, shown):
        (self.hidden_tabs.discard if shown else self.hidden_tabs.add)(key)
        self.settings.setValue("hiddenTabs", json.dumps(sorted(self.hidden_tabs)))
        self.rebuild_tabs()

    def _tab_changed(self, _index):
        widget = self.tabs.currentWidget()
        if widget is self.memory_tab:
            self.memory_tab.refresh()
        elif widget in (self.vars_tab, self.pcode_tab) and not self.program_info:
            self.compile_only()

    def _update_source_tabs(self):
        if self.mode in ("simple",):
            return
        analysis = inspector.analyse_source(self.language, self.editor.toPlainText(),
                                            REGISTRY["builtins"])
        self.usage_tab.update_from(analysis)
        self.comments_tab.update_from(analysis)
        self.syntax_tab.update_from(analysis)

    def _update_compiled_tabs(self):
        self.vars_tab.update_from(self.program_info)
        self.pcode_tab.update_from(self.program_info)
        self.syntax_tab.set_program(self.program_info.get("tm"))
        self.memory_tab.refresh()

    def set_canvas_size(self, size):
        self.settings.setValue("canvasSize", size)
        self.machine.default_size = size
        if not self.machine.running:
            self.reset_all()

    def choose_files_dir(self):
        name = QFileDialog.getExistingDirectory(self, "Base directory for file operations",
                                                str(self.machine.files_dir))
        if name:
            self.machine.files_dir = Path(name)
            self.settings.setValue("filesDir", name)

    def _build_help(self):
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Command", "Parameters", "Description"])
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setWordWrap(True)
        table.cellDoubleClicked.connect(self._insert_command)
        return table

    def _help_rows(self):
        """QuickHelp entries for the current language: builtins, input codes, colours."""
        lang = self.language
        rows = []
        for entry in REGISTRY["builtins"]:
            name = entry["names"][lang]
            if not name:
                continue
            rows.append((entry["category"], name, entry["params"], entry["description"],
                         entry["level"]))
        rows.sort(key=lambda r: (r[0], r[1].lower().lstrip(".")))
        self.help_row_levels = [r[4] for r in rows]
        rows = [r[:4] for r in rows]
        suffix = "%" if lang == "basic" else ""
        turtle = [("turtx", "x-coordinate"), ("turty", "y-coordinate"), ("turtd", "direction"),
                  ("turta", "angles (degrees in a circle)"), ("turtt", "thickness (negative: pen up)"),
                  ("turtc", "colour")]
        extra = [("Turtle variables", v + suffix, "", f"Turtle's {d} (read or assign)")
                 for v, d in turtle]
        extra += [("Input codes", f"?{q}", "", f"Value of input code \\{q} = {code}")
                  for q, code in QUERY_CODES.items()]
        extra += [("Input codes", f"\\{k}", "", f"Key code {code}")
                  for k, code in KEY_CODES.items() if len(k) > 1 and not k[0].isdigit()]
        self.help_row_levels += [None] * len(extra)
        return rows + extra

    def _fill_help(self):
        rows = self._help_rows()
        self.help_rows = rows
        table = self.help_table
        table.setRowCount(len(rows))
        for r, (category, name, params, desc) in enumerate(rows):
            for col, text in enumerate((name, params, desc)):
                item = QTableWidgetItem(text)
                item.setToolTip(category)
                table.setItem(r, col, item)
        self._filter_help(self.help_filter.text())

    def _filter_help(self, text):
        text = text.lower()
        levels = {lv for lv, box in getattr(self, "help_levels", {}).items() if box.isChecked()}
        for r, entry in enumerate(getattr(self, "help_rows", [])):
            level = self.help_row_levels[r] if hasattr(self, "help_row_levels") else 1
            self.help_table.setRowHidden(r, text not in " ".join(entry).lower()
                                         or (level is not None and level not in levels))

    def _insert_command(self, row, _col):
        _, name, params, _ = self.help_rows[row]
        if name.startswith(("?", "\\")) or name.startswith("turt"):
            self.editor.insertPlainText(name)
        elif params:
            self.editor.insertPlainText(f"{name.lstrip('.')}({params})")
        else:
            self.editor.insertPlainText(name.lstrip("."))
        self.editor.setFocus()

    def _fill_examples(self):
        menu = self.examples_menu
        menu.clear()
        try:
            index = json.loads((EXAMPLES_DIR / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            menu.addAction("(no examples - run tools/extract_examples.py)").setEnabled(False)
            return
        self.examples_index = index
        info = index.get(self.language, {"groups": [], "examples": []})
        by_number = {e["n"]: e for e in info["examples"]}
        for group in info["groups"]:
            sub = menu.addMenu(group["title"])
            for n in range(group["first"], group["last"] + 1):
                e = by_number.get(n)
                if e:
                    action = sub.addAction(e["title"])
                    action.triggered.connect(lambda _=False, e=e: self.open_numbered_example(
                        self.language, e["n"]))
        menu.addSeparator()
        for key, text, default in (
                ("exampleComments", "Include comments within example programs", "true"),
                ("exampleHints", "Include functional type hints within example programs",
                 "false"),
                ("exampleSwitch", "Load corresponding example on language switch", "true")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(self.settings.value(key, default) == "true")
            action.toggled.connect(lambda on, k=key: self.settings.setValue(
                k, "true" if on else "false"))
            if key == "exampleHints":
                action.setEnabled(self.language == "python")

    def open_numbered_example(self, language, number):
        """Open example `number` of `language`, honouring the Examples menu options."""
        info = self.examples_index.get(language, {"examples": []})
        entry = next((e for e in info["examples"] if e["n"] == number), None)
        if entry is None:
            return False
        folder = language
        if language == "python" and self.settings.value("exampleHints", "false") == "true":
            folder = "python_hints"
        path = EXAMPLES_DIR / folder / entry["file"]
        self.load(path, example=True, title=entry["title"])
        e = self.editor
        if self.settings.value("exampleComments", "true") != "true":
            e.setPlainText(formatting.remove_comments(language, e.toPlainText()))
            e.document().setModified(False)
        e.example = number
        self._update_title()
        return True

    def change_language(self, language):
        if language == self.language:
            return
        if self.machine.running:
            self.machine.stop()
        e = self.editor
        number = getattr(e, "example", None)
        if (number and not e.document().isModified()
                and self.settings.value("exampleSwitch", "true") == "true"):
            e.setPlainText("")               # make the tab reusable for the new example
            e.path, e.title, e.example = None, None, None
            e.document().setModified(False)
            self.set_language(language)
            if not self.open_numbered_example(language, number):
                e.setPlainText(LANGUAGES[language]["skeleton"])
                e.document().setModified(False)
            return
        self.set_language(language)
        if self._pristine():
            self.editor.doc_language = language
            self.editor.set_language(language)
            self.editor.setPlainText(LANGUAGES[language]["skeleton"])
            self.editor.document().setModified(False)
        self._label_doc(self.editor)
        self._update_title()

    def set_language(self, language):
        self.language = language
        self.settings.setValue("language", language)
        self.language_label.setText(f" {LANGUAGES[language]['label']} ")
        if self.editor is not None and self._pristine():
            self.editor.doc_language = language
        if self.editor is not None and self.editor.doc_language == language:
            self.editor.set_language(language)
        self.help1.setHtml(HELP1[language])
        self.language_actions[language].setChecked(True)
        self._fill_examples()
        self._fill_help()

    def _apply_font_size(self):
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(max(8, self.font_size - 1))
        self.console.set_font(mono)
        self.output.setFont(mono)

    # ------------------------------------------------ files

    def _update_title(self, *_):
        e = self.editor
        if e is None:
            return
        name = e.path.name if e.path else (e.title or "untitled")
        star = "*" if e.document().isModified() else ""
        self.setWindowTitle(f"{star}{name} — Turtle System for Linux")
        self.title_bar.setText(e.title or Path(name).stem.replace("_", " "))
        self._label_doc(e)

    def _confirm_discard(self):
        if not self.editor.document().isModified():
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save changes to the current program?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def new_program(self, confirm=True, skeleton=True):
        text = None if skeleton else ""
        if self._pristine():
            self.editor.setPlainText(LANGUAGES[self.language]["skeleton"] if skeleton else "")
            self.editor.document().setModified(False)
        else:
            self._new_doc(self.language, text)
        self._update_title()

    def open_dialog(self):
        start = self.settings.value("lastDir", str(Path.home()))
        name, _ = QFileDialog.getOpenFileName(self, "Open program", start, ALL_FILTER)
        if name:
            self.load(Path(name))

    def open_example(self, path, title=None):
        self.load(path, example=True, title=title)

    def load(self, path, example=False, title=None):
        for i in range(self.docs.count()):       # already open: switch to it
            w = self.docs.widget(i)
            if not example and w.path and w.path.resolve() == path.resolve():
                self.docs.setCurrentIndex(i)
                return
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp1252")
        except OSError as e:
            QMessageBox.warning(self, "Open", f"Cannot open {path}:\n{e}")
            return
        language = EXTENSION_LANGUAGE.get(path.suffix.lower()) or self.language
        text = text.replace("\r\n", "\n")
        if self._pristine():
            e = self.editor
            e.doc_language = language
            e.set_language(language)
            e.setPlainText(text)
        else:
            e = self._new_doc(language, text)
        e.path = None if example else path
        e.title = (title or path.stem.split("_", 1)[-1].replace("_", " ")) if example else None
        e.stored_copy = None
        e.example = None
        if language != self.language:
            self.set_language(language)
        if not example:
            self.settings.setValue("lastDir", str(path.parent))
            self.settings.setValue("lastFile", str(path))
        e.document().setModified(False)
        self._update_title()
        if self.auto_format_load.isChecked():
            self.auto_format()
        if self.auto_compile.isChecked():
            QTimer.singleShot(0, self.compile_only)
        if self.auto_run.isChecked():
            QTimer.singleShot(0, self.run_program)

    def save(self):
        if self.path is None:
            return self.save_as()
        try:
            self.path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Save", f"Cannot save {self.path}:\n{e}")
            return False
        self.editor.document().setModified(False)
        self.settings.setValue("lastFile", str(self.path))
        self._update_title()
        return True

    def save_as(self):
        start = self.settings.value("lastDir", str(Path.home()))
        cfg = LANGUAGES[self.editor.doc_language]
        name, _ = QFileDialog.getSaveFileName(self, "Save program", start,
                                              cfg["filter"] + ";;All files (*)")
        if not name:
            return False
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(cfg["module"].EXTENSIONS[0])
        self.path = path
        self.settings.setValue("lastDir", str(path.parent))
        ok = self.save()
        self._update_title()
        return ok

    def copy_canvas(self):
        QApplication.clipboard().setImage(self.machine.frame())
        self.statusBar().showMessage("Canvas copied to the clipboard", 3000)

    # ------------------------------------------------ editing tools

    def find_replace(self):
        if not hasattr(self, "find_dialog"):
            self.find_dialog = FindReplaceDialog(self)
        cursor = self.editor.textCursor()
        if cursor.hasSelection() and "\u2029" not in cursor.selectedText():
            self.find_dialog.find.setText(cursor.selectedText())
        self.find_dialog.show()
        self.find_dialog.raise_()
        self.find_dialog.find.setFocus()
        self.find_dialog.find.selectAll()

    def _replace_text(self, text):
        """Replace the whole program as one undoable edit."""
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()

    def auto_format(self):
        self._replace_text(formatting.auto_format(self.editor.doc_language,
                                                  self.editor.toPlainText()))

    def remove_comments(self):
        self._replace_text(formatting.remove_comments(self.editor.doc_language,
                                                      self.editor.toPlainText()))

    def remove_hints(self):
        if self.editor.doc_language != "python":
            self.statusBar().showMessage("Type hints exist only in Turtle Python", 3000)
            return
        self._replace_text(formatting.remove_type_hints(self.editor.toPlainText()))

    def store_copy(self):
        self.editor.stored_copy = self.editor.toPlainText()
        self.statusBar().showMessage("Copy of the program stored", 3000)

    def restore_copy(self):
        if self.editor.stored_copy is None:
            self.statusBar().showMessage("No stored copy of this program", 3000)
            return
        self._replace_text(self.editor.stored_copy)

    def save_canvas(self):
        start = self.settings.value("lastDir", str(Path.home()))
        name, _ = QFileDialog.getSaveFileName(self, "Save canvas", start, "PNG image (*.png)")
        if name:
            image = self.machine.frame()
            if not name.lower().endswith(".png"):
                name += ".png"
            image.save(name)

    # ------------------------------------------------ running

    def toggle_run(self):
        if self.machine.running:
            self.halt()
        else:
            self.run()

    def _compile_machine(self, source, info):
        """Compile to Turtle Machine PCode; None if this program needs the fallback engine."""
        try:
            program, comp = tm_info.compile_source(self.language, source)
        except Exception as e:  # noqa: BLE001 - the checking compiler accepted the program
            print(f"PCode compiler: {type(e).__name__}: {e}", file=sys.stderr)
            info.pop("tm", None)
            return None
        info["tm"] = tm_info.ProgramInfo(self.language, source, program, comp)
        return program

    def run(self):
        source = self.editor.toPlainText()
        self.editor.mark_error(None)
        module = LANGUAGES[self.language]["module"]
        info = {}
        try:
            code = module.compile_program(source, info)
        except SyntaxError as e:
            self._report_error(e.lineno, f"ERROR: {e.msg}")
            return
        program = self._compile_machine(source, info)
        self.program_info = info
        self._update_compiled_tabs()
        self.console.text.clear()
        self.output.clear()
        set_background(self.console.text, None)
        set_background(self.output, None)
        self.canvas.set_program_cursor(None)
        if program is not None:            # run the PCode on the Turtle Machine
            vm = TurtleVM(self.machine, program, autodel=self.autodel_action.isChecked())
            if self.pcode_tab.trace_on_run.isChecked():
                self.pcode_tab.trace_display.setChecked(True)
            info["vm"] = vm
            vm.start()
        else:
            self.machine.start(code, module.environment(self.machine))
        self.editor.setReadOnly(True)
        self.run_button.setText("HALT")
        self.run_button.setStyleSheet(f"font-weight: bold; padding: 2px 14px; "
                                      f"color: {THEME['error_text']};")
        if self.show_canvas_on_run.isChecked():
            self.show_page("canvas")
        self.canvas.setFocus()

    def set_dark_canvas(self, on):
        self.settings.setValue("darkCanvas", "true" if on else "false")
        self.machine.dark_canvas = on
        if not self.machine.running:
            self.reset_all()

    def set_trace(self, on):
        self.pcode_tab.trace_display.setChecked(on)
        self.pcode_tab.trace_on_run.setChecked(on)

    def save_pcode(self, kind, quiet_path=None):
        info = self.program_info.get("tm") if self.program_info else None
        if info is None:
            if not self.compile_only():
                return
            info = self.program_info.get("tm")
            if info is None:
                return
        if quiet_path is None:
            base = Path(self.path).with_suffix("." + ("tmj" if kind == "json" else "tmc")) \
                if self.path else Path.home() / ("program." + ("tmj" if kind == "json" else "tmc"))
            name, _ = QFileDialog.getSaveFileName(
                self, "Save Turtle Machine PCode", str(base),
                "PCode JSON (*.tmj *.json)" if kind == "json" else "PCode (*.tmc)")
            if not name:
                return
        else:
            name = quiet_path
        program = info.program
        if kind == "json":
            data = {"language": self.language, "lines": len(program.lines),
                    "memtop": program.memtop,
                    "pcode": [{"line": n, "code": line,
                               "assembler": pcode_mod.line_text(line)}
                              for n, line in enumerate(program.lines, 1)]}
            Path(name).write_text(json.dumps(data, indent=1))
        else:
            Path(name).write_bytes(program.to_tmc())
        self.statusBar().showMessage(f"PCode saved to {name}", 3000)

    def load_run_pcode(self):
        name, _ = QFileDialog.getOpenFileName(self, "Load Turtle Machine PCode",
                                              str(Path.home()), "PCode (*.tmc)")
        if not name or self.machine.running:
            return
        program = pcode_mod.Program.from_tmc(Path(name).read_bytes())
        self.program_info = {"pcode_only": True}
        self.pcode_tab.info = type("I", (), {"program": program})()
        self.pcode_tab.refresh()
        self.console.text.clear()
        self.output.clear()
        vm = TurtleVM(self.machine, program)
        self.program_info["vm"] = vm
        vm.start()
        self.run_button.setText("HALT")

    def print_text(self, text):
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter()
        if QPrintDialog(printer, self).exec() == QDialog.DialogCode.Accepted:
            doc = QTextDocument()
            doc.setDefaultFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            doc.setPlainText(text)
            doc.print_(printer)

    def run_program(self):
        if not self.machine.running:
            self.run()

    def halt(self):
        self.machine.paused.clear()
        self.pause_action.setChecked(False)
        self.machine.stop()

    def toggle_pause(self, on):
        if on and self.machine.running:
            self.machine.paused.set()
            self.run_button.setText("PAUSED")
        else:
            self.machine.paused.clear()
            self.pause_action.setChecked(False)
            if self.machine.running:
                self.run_button.setText("HALT")

    def reset_all(self):
        if not self.machine.running:
            self.machine.reset()
            self.canvas.frame = self.machine.frame()
            self.canvas.update()
        self.console.text.clear()
        self.output.clear()

    def compile_only(self):
        """Compile without running, filling the compiled-code tabs. Returns success."""
        module = LANGUAGES[self.language]["module"]
        info = {}
        self.editor.mark_error(None)
        try:
            module.compile_program(self.editor.toPlainText(), info)
        except SyntaxError as e:
            self._report_error(e.lineno, f"ERROR: {e.msg}")
            return False
        self._compile_machine(self.editor.toPlainText(), info)
        self.program_info = info
        if info.get("tm") is not None and self.path:
            if self.autosave_tmc.isChecked():
                self.save_pcode("tmc", str(Path(self.path).with_suffix(".tmc")))
            if self.autosave_json.isChecked():
                self.save_pcode("json", str(Path(self.path).with_suffix(".tmj")))
        self._update_compiled_tabs()
        self.statusBar().showMessage("Compiled successfully", 3000)
        return True

    def clear_canvas(self):
        if not self.machine.running:
            self.machine.reset()

    def _on_finished(self, error):
        self.memory_tab.refresh()
        self.editor.setReadOnly(False)
        self.run_button.setText("RUN")
        self.run_button.setStyleSheet("font-weight: bold; padding: 2px 14px;")
        self.console.entry.setStyleSheet("")
        if error:
            self._report_error(*error)

    def _report_error(self, line, message):
        where = f" in line {line}" if line else ""
        message = message[len("ERROR: "):] if message.startswith("ERROR: ") else message
        append_text(self.console.text, f"\nERROR{where}: {message}\n", THEME["error_text"])
        self.show_page("canvas")
        self.editor.mark_error(line)
        self.editor.setFocus()

    def _clear_error(self):
        if self.editor is not None and self.editor.error_line:
            self.editor.mark_error(None)

    def _on_write(self, text, target):
        widget = self.output if target == "output" else self.console.text
        append_text(widget, text)
        if target == "output" and self.show_output_on_text.isChecked():
            self.show_page("output")

    def _on_clear(self, target, rgb, to_front):
        widget = self.output if target.startswith("output") else self.console.text
        if not target.endswith("colour"):
            widget.clear()
        if rgb >= 0:
            set_background(widget, rgb)
        if to_front:
            self.show_page("output")

    def _on_want_input(self, _prompt):
        self.show_page("canvas")
        self.console.entry.setStyleSheet(f"QLineEdit {{ border: 2px solid {THEME['highlight']}; }}")
        self.console.entry.setFocus()

    def _on_cursor(self, code):
        self.canvas.set_program_cursor(CURSORS[code] if 0 <= code < len(CURSORS)
                                       else Qt.CursorShape.ArrowCursor)

    def _tick(self):
        self.canvas.refresh()
        if not self.machine.waiting_input and self.console.entry.styleSheet():
            self.console.entry.setStyleSheet("")
        t = self.machine.turtle
        try:
            x, y, d, a, th, c = (round(t[0]), round(t[1]), round(t[2]), t[3], t[4], int(t[5]))
        except (IndexError, TypeError, ValueError):
            return
        labels = self.status_labels
        labels["x"].setText(f"<b>X</b> {x}")
        labels["y"].setText(f"<b>Y</b> {y}")
        labels["d"].setText(f"<b>Direction</b> {d}/{a}")
        labels["t"].setText(f"<b>Thickness</b> {th}")
        if getattr(self, "_last_colour", None) != c:
            self._last_colour = c
            labels["c"].setPixmap(colour_swatch(c, self.fontMetrics().height()))
        if self.canvas.show_turtle and self.machine.running:
            self.canvas.update()

    def show_mouse(self, x, y, inside):
        self.status_labels["mouse"].setText(f"<span style='color:{THEME['ruler']}'>{x}, {y}</span>"
                                            if inside else "")

    # ------------------------------------------------ misc

    def _canvas_targeted(self):
        c = self.canvas
        if not c.isVisible():
            return False
        if c.hasFocus():
            return True
        from PySide6.QtGui import QCursor
        return c.rect().contains(c.mapFromGlobal(QCursor.pos()))

    def zoom(self, step):
        if self._canvas_targeted():
            self.canvas.zoom_step(step)
            return
        self.font_size = 12 if step == 0 else max(6, min(40, self.font_size + step))
        for i in range(self.docs.count()):
            self.docs.widget(i).set_font_size(self.font_size)
        self._apply_font_size()
        self.settings.setValue("fontSize", self.font_size)

    def _set_show_turtle(self, on):
        self.canvas.show_turtle = on
        self.canvas.update()
        self.settings.setValue("showTurtle", "true" if on else "false")

    def about(self):
        QMessageBox.about(
            self, "Turtle System for Linux",
            "<h3>Turtle System for Linux</h3>"
            "<p>Native re-implementation of the Turtle System (Peter Millican, Oxford) for "
            "Linux: Turtle Python, Turtle Pascal and Turtle BASIC, with drawing and integer "
            "semantics matched against the original.</p>"
            "<p>Ctrl+R runs the program, Ctrl+H halts it. View - Machine Mode shows the "
            "compiler tabs (Syntax, Vars/Subs, PCode, Memory).</p>")

    def closeEvent(self, event):
        for i in range(self.docs.count()):
            if self.docs.widget(i).document().isModified():
                self.docs.setCurrentIndex(i)
                if not self._confirm_discard():
                    event.ignore()
                    return
        self.machine.stop()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        self.settings.setValue("canvasSplit", self.canvas_split.saveState())
        event.accept()
        QApplication.quit()       # also closes detached canvas / tabs windows


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Turtle System")
    SYSTEM_LOOK.update(style=app.style().name(), palette=app.palette())
    app.setDesktopFileName("turtle-system")
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128, 256, 512):   # small sizes: no text (logo E)
        path = APP_DIR / "icons" / f"turtle-system-{size}.png"
        if path.exists():
            icon.addFile(str(path), QSize(size, size))
    app.setWindowIcon(icon)
    window = MainWindow()
    window.canvas.show_turtle = window.settings.value("showTurtle", "false") == "true"
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        window.load(Path(sys.argv[1]).resolve())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
