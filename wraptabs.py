"""A tab widget whose tabs wrap onto several rows instead of scrolling with arrows.

It behaves like the original's multi-line Delphi page control: tabs are laid out left to right,
wrapping onto further rows when they do not fit; when there is more than one row every row is
stretched to the full width, and the row holding the selected tab sits next to the page.
It offers the part of QTabWidget's interface the main window uses (addTab, removeTab,
indexOf, count, currentWidget, setCurrentIndex, currentChanged, tab position)."""

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QSizePolicy, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

PAD_X = 10          # horizontal padding inside a tab
RAISE = 2           # how far the selected tab stands out


class TabRows(QWidget):
    clicked = Signal(int)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.labels = []
        self.current = -1
        self.south = True
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.hover = -1

    def tab_height(self):
        return self.fontMetrics().height() + 6

    def _rows(self, width):
        """Rows of tab indices, wrapped by natural width."""
        fm = self.fontMetrics()
        rows, row, used = [], [], 0
        for i, text in enumerate(self.labels):
            w = fm.horizontalAdvance(text.replace("&&", "&")) + 2 * PAD_X
            if row and used + w > width - 2 * RAISE:
                rows.append(row)
                row, used = [], 0
            row.append(i)
            used += w
        if row:
            rows.append(row)
        # balance: spread the tabs evenly over as few rows as fit (10 tabs -> 5 + 5)
        n = len(rows)
        count = len(self.labels)
        widths = [fm.horizontalAdvance(t) + 2 * PAD_X for t in self.labels]
        while 1 < n < count:
            per = -(-count // n)
            balanced = [list(range(k, min(k + per, count))) for k in range(0, count, per)]
            if all(sum(widths[i] for i in r) <= width - 2 * RAISE for r in balanced):
                return balanced
            n += 1
        return rows

    def _geometry(self):
        """{index: QRect} with the selected tab's row next to the page."""
        width = self.width()
        fm = self.fontMetrics()
        rows = self._rows(width)
        if len(rows) > 1:
            sel = next((r for r in rows if self.current in r), None)
            if sel is not None:          # rotate so the selected row is first (next to the page)
                k = rows.index(sel)
                rows = rows[k:] + rows[:k]
        h = self.tab_height()
        rects = {}
        for r, row in enumerate(rows):
            naturals = [fm.horizontalAdvance(self.labels[i]) + 2 * PAD_X for i in row]
            if len(rows) > 1:            # justify: stretch the row to the full width
                avail = width - 2 * RAISE
                total = sum(naturals)
                extra = avail - total
                widths = []
                acc = 0
                for j, n in enumerate(naturals):
                    share = extra * (j + 1) // len(naturals) - extra * j // len(naturals)
                    widths.append(n + share)
            else:
                widths = naturals
            x = RAISE
            y = RAISE + r * h if self.south else self._height_for(len(rows)) - RAISE - (r + 1) * h
            for i, w in zip(row, widths):
                rects[i] = QRect(x, y, w, h)
                x += w
        return rects, len(rows)

    def _height_for(self, nrows):
        return nrows * self.tab_height() + 2 * RAISE

    def sizeHint(self):
        return QSize(200, self.heightForWidth(max(self.width(), 200)))

    def minimumSizeHint(self):
        return QSize(50, self._height_for(1))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._height_for(max(1, len(self._rows(width))))

    def resizeEvent(self, event):
        h = self.heightForWidth(self.width())
        if h != self.height():
            self.setFixedHeight(h)
        super().resizeEvent(event)

    def relayout(self):
        self.setFixedHeight(self.heightForWidth(max(self.width(), 1)))
        self.update()

    def index_at(self, pos):
        rects, _ = self._geometry()
        for i, r in rects.items():
            if r.contains(pos):
                return i
        return -1

    def mousePressEvent(self, event):
        i = self.index_at(event.position().toPoint())
        if i >= 0:
            self.clicked.emit(i)

    def mouseMoveEvent(self, event):
        i = self.index_at(event.position().toPoint())
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, event):
        self.hover = -1
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        pal = self.palette()
        rects, nrows = self._geometry()
        light = pal.color(QPalette.ColorRole.Light)
        dark = pal.color(QPalette.ColorRole.Dark)
        shadow = pal.color(QPalette.ColorRole.Shadow)
        for i, r in rects.items():
            selected = i == self.current
            r = QRect(r)
            if selected:                  # the selected tab is larger and joins the page
                r.adjust(-RAISE, -RAISE if self.south else 0, RAISE, 0 if self.south else RAISE)
            else:
                r.adjust(0, 0 if self.south else 0, -1, 0)
            bg = pal.color(QPalette.ColorRole.Window if selected else QPalette.ColorRole.Button)
            if i == self.hover and not selected:
                bg = pal.color(QPalette.ColorRole.Midlight)
            p.fillRect(r, bg)
            # 3D edges in the Windows classic style
            if self.south:
                p.setPen(light)
                p.drawLine(r.left(), r.top() + (0 if selected else 1), r.left(), r.bottom() - 1)
                p.setPen(shadow)
                p.drawLine(r.left() + 1, r.bottom(), r.right() - 1, r.bottom())
                p.drawLine(r.right(), r.top() + (0 if selected else 1), r.right(), r.bottom() - 1)
                p.setPen(dark)
                p.drawLine(r.right() - 1, r.top() + 1, r.right() - 1, r.bottom() - 1)
                if not selected and self._row_of(rects, i) == 0:
                    p.setPen(light)
                    p.drawLine(r.left(), r.top(), r.right() + 1, r.top())
            else:
                p.setPen(light)
                p.drawLine(r.left(), r.top() + 1, r.left(), r.bottom())
                p.drawLine(r.left() + 1, r.top(), r.right() - 1, r.top())
                p.setPen(shadow)
                p.drawLine(r.right(), r.top() + 1, r.right(), r.bottom())
                p.setPen(dark)
                p.drawLine(r.right() - 1, r.top() + 1, r.right() - 1, r.bottom())
            p.setPen(pal.color(QPalette.ColorRole.WindowText if selected
                               else QPalette.ColorRole.ButtonText))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.labels[i].replace("&&", "&"))
        p.end()

    def _row_of(self, rects, i):
        return (rects[i].top() - RAISE) // self.tab_height() if self.south else 0


class WrapTabWidget(QWidget):
    currentChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.stack = QStackedWidget()
        self.bar = TabRows(self)
        self.bar.clicked.connect(self.setCurrentIndex)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)
        self._position = QTabWidget.TabPosition.South
        self._place_bar()

    def _place_bar(self):
        self.box.removeWidget(self.stack)
        self.box.removeWidget(self.bar)
        self.bar.south = self._position != QTabWidget.TabPosition.North
        if self.bar.south:
            self.box.addWidget(self.stack, 1)
            self.box.addWidget(self.bar)
        else:
            self.box.addWidget(self.bar)
            self.box.addWidget(self.stack, 1)
        self.bar.relayout()

    def setTabPosition(self, position):
        self._position = position
        self._place_bar()

    def tabPosition(self):
        return self._position

    def addTab(self, widget, label):
        index = self.stack.addWidget(widget)
        self.bar.labels.append(label)
        if self.stack.count() == 1:
            self.setCurrentIndex(0)
        self.bar.relayout()
        return index

    def removeTab(self, index):
        if not 0 <= index < self.stack.count():
            return
        was_current = index == self.stack.currentIndex()
        self.stack.removeWidget(self.stack.widget(index))
        del self.bar.labels[index]
        self.bar.current = self.stack.currentIndex()
        if was_current and self.stack.count():
            self.setCurrentIndex(min(index, self.stack.count() - 1))
        self.bar.relayout()

    def indexOf(self, widget):
        return self.stack.indexOf(widget)

    def count(self):
        return self.stack.count()

    def widget(self, index):
        return self.stack.widget(index)

    def tabText(self, index):
        return self.bar.labels[index] if 0 <= index < len(self.bar.labels) else ""

    def currentIndex(self):
        return self.stack.currentIndex()

    def currentWidget(self):
        return self.stack.currentWidget()

    def setCurrentIndex(self, index):
        if not 0 <= index < self.stack.count():
            return
        changed = index != self.stack.currentIndex() or self.bar.current != index
        self.stack.setCurrentIndex(index)
        self.bar.current = index
        self.bar.update()
        if changed and not self.signalsBlocked():
            self.currentChanged.emit(index)

    def usesScrollButtons(self):
        return False
