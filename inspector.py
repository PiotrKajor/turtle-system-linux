"""Expert / Machine mode tabs, laid out like the original's: Usage, Comments, Syntax,
Vars/Subs, PCode and Memory.

Programs are compiled to Turtle Machine PCode (tm.*) and run on the Turtle Machine (tm.vm),
so the PCode tab lists the program's real PCode and the Memory tab shows the machine's memory
stack and heap cell by cell.
"""

import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QRadioButton, QSplitter, QTableView, QToolButton, QVBoxLayout,
                               QWidget, QApplication)

from tm.pcode import OPS
from tm.vm import HEAP_BASE

TURTLE_ATTRS = ("turtx", "turty", "turtd", "turta", "turtt", "turtc")

PY_KEYWORDS = set("and as assert break class continue def del elif else except finally for from "
                  "global if import in is lambda nonlocal not or pass raise return try while "
                  "with yield True False None".split())
PAS_KEYWORDS = set("program var const begin end procedure function if then else while do for to "
                   "downto repeat until div mod and or not xor array of true false".split())
BAS_KEYWORDS = set("IF THEN ELSE ENDIF FOR TO STEP NEXT WHILE ENDWHILE REPEAT UNTIL DEF ENDPROC "
                   "LOCAL PRIVATE RETURN DIM CONST END AND OR EOR NOT DIV MOD TRUE FALSE "
                   "STRINGSIZE".split())

TOKEN_RE = {
    "python": re.compile(r"""(?P<comment>\#.*)|(?P<string>'[^'\n]*'|"[^"\n]*")|"""
                         r"""(?P<code>[?\\][A-Za-z_]\w*)|(?P<number>\d+)|(?P<word>[A-Za-z_]\w*)|"""
                         r"""(?P<op>//=?|\*\*|->|[<>=!]=|[-+*/%]=|[^\s\w])"""),
    "pascal": re.compile(r"""(?P<comment>\{[^}]*\}?|\(\*.*?(\*\)|$)|//.*)|(?P<string>'([^']|'')*'?)|"""
                         r"""(?P<code>[?\\][A-Za-z_]\w*)|(?P<number>\$[0-9A-Fa-f]+|\d+)|"""
                         r"""(?P<word>[A-Za-z_]\w*)|(?P<op>:=|<=|>=|<>|\.\.|[^\s\w])"""),
    "basic": re.compile(r"""(?P<comment>\bREM\b.*)|(?P<string>"([^"]|"")*"?)|"""
                        r"""(?P<code>[?\\][A-Za-z_]\w*)|(?P<number>&[0-9A-Fa-f]+|\d+)|"""
                        r"""(?P<word>[A-Za-z_]\w*[%$#]?)|(?P<op><=|>=|<>|[^\s\w])"""),
}


def analyse_source(language, source, registry):
    """Lexemes, comments and command usage of a program (no compilation needed)."""
    keywords = {"python": PY_KEYWORDS, "pascal": PAS_KEYWORDS, "basic": BAS_KEYWORDS}[language]
    commands = {}
    for entry in registry:
        name = entry["names"][language]
        if name:
            commands[name.lstrip(".").lower() if language != "basic" else name] = entry
    lexemes, comments, usage = [], [], {}
    pending_nl = 0
    indents = [0]
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip(" \t")
        indent_cols = len(line) - len(stripped)
        first = True
        prev_end = 0
        line_lexemes = 0
        for m in TOKEN_RE[language].finditer(line):
            kind = m.lastgroup
            text = m.group(kind)
            spaces = m.start() - prev_end
            prev_end = m.end()
            if kind == "comment":
                comments.append({"line": lineno, "lexeme": len(lexemes),
                                 "newlines": pending_nl if first else 0, "spaces": spaces,
                                 "after": 1, "text": text})
                continue
            if first and language == "python":
                if indent_cols > indents[-1]:
                    indents.append(indent_cols)
                    lexemes.append({"line": lineno, "text": "", "type": "=>", "nl": 0,
                                    "spaces": 0, "first": False})
                while indent_cols < indents[-1]:
                    indents.pop()
                    lexemes.append({"line": lineno, "text": "", "type": "<=", "nl": 0,
                                    "spaces": 0, "first": False})
            if kind == "word":
                key = text if language == "basic" else text.lower()
                if key in keywords:
                    ltype = key.lower()
                    if text in ("True", "False", "TRUE", "FALSE") or key in ("true", "false"):
                        ltype = key.lower()
                elif language == "python" and key in ("int", "str", "bool", "list"):
                    ltype = key
                elif key in commands:
                    ltype = "identifier"
                    usage.setdefault(key, [commands[key], 0, lineno])[1] += 1
                else:
                    ltype = "identifier"
            elif kind == "number":
                ltype = "hexnum" if text[0] in "$&" else "decnum"
            elif kind == "string":
                ltype = "str_val"
            elif kind == "code":
                ltype = "inputcode" if text[0] == "\\" else "query"
            else:
                ltype = text
            lexemes.append({"line": lineno, "text": text if kind != "op" else text, "type": ltype,
                            "nl": pending_nl + (1 if first and lexemes else 0) if first else 0,
                            "spaces": spaces, "first": first,
                            "indent": len(indents) if language == "python" else None})
            first = False
            line_lexemes += 1
        if line_lexemes:
            if language in ("python", "basic"):
                lexemes.append({"line": lineno, "text": "", "type": "Eol", "nl": 0, "spaces": 0,
                                "first": False})
            pending_nl = 0
        else:
            pending_nl += 1
    return {"lexemes": lexemes, "comments": comments, "usage": usage,
            "tokens": [(x["line"], x["type"], x["text"]) for x in lexemes]}


# ---------------------------------------------------------------- shared look

def _small_font():
    f = QFont()
    f.setPointSizeF(max(7.0, f.pointSizeF() * 0.9))
    return f


class RowsModel(QAbstractTableModel):
    """A plain read-only table of strings (and optional per-cell background colours)."""

    def __init__(self, headers, align=None):
        super().__init__()
        self.headers = headers
        self.rows = []
        self.colours = {}
        self.align = align or {}

    def set_rows(self, rows, colours=None):
        self.beginResetModel()
        self.rows = [[("" if v is None else str(v)) for v in r] for r in rows]
        self.colours = colours or {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r, c = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            row = self.rows[r]
            return row[c] if c < len(row) else ""
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self.align.get(c, Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.BackgroundRole:
            col = self.colours.get((r, c)) or self.colours.get((r, None))
            return QBrush(col) if col is not None else None
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None


def _grid(model, stretch_last=True, widths=None):
    """A Delphi-style string grid."""
    view = QTableView()
    view.setModel(model)
    view.verticalHeader().hide()
    view.setFont(_small_font())
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setShowGrid(True)
    view.verticalHeader().setDefaultSectionSize(view.fontMetrics().height() + 4)
    view.verticalHeader().setMinimumSectionSize(4)
    header = view.horizontalHeader()
    header.setFont(_small_font())
    header.setHighlightSections(False)
    for c in range(model.columnCount()):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        if widths and c < len(widths):
            view.setColumnWidth(c, widths[c])
    if stretch_last:
        header.setStretchLastSection(True)
    return view


def _csv_button(view, owner):
    """The small disk button of "Show CSV/TSV saving buttons within data tables"."""
    button = QToolButton()
    button.setText("\U0001F4BE")
    button.setToolTip("Save table data in CSV or TSV format")
    button.setAutoRaise(True)

    def save():
        name, flt = QFileDialog.getSaveFileName(owner, "Save Table Data in CSV or TSV Format", "",
                                                "CSV files (*.csv);;TSV files (*.tsv)")
        if not name:
            return
        sep = "\t" if name.lower().endswith(".tsv") or "TSV" in flt else ","
        model = view.model()
        lines = [sep.join(model.headerData(c, Qt.Orientation.Horizontal) or ""
                          for c in range(model.columnCount()))]
        for r in range(model.rowCount()):
            lines.append(sep.join(str(model.data(model.index(r, c)) or "")
                                  for c in range(model.columnCount())))
        with open(name, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    button.clicked.connect(save)
    button.hide()
    CSV_BUTTONS.append(button)
    return button


CSV_BUTTONS = []


def show_csv_buttons(on):
    for b in CSV_BUTTONS:
        b.setVisible(on)


def _with_csv(view, owner):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(view)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(_csv_button(view, owner))
    lay.addLayout(row)
    return w


def _scope(a, b):
    return f"{a} to {b}" if a or b else ""


# ---------------------------------------------------------------- Usage / Comments / Syntax

class UsageTab(QWidget):
    def __init__(self):
        super().__init__()
        self.model = RowsModel(["Command", "Uses", "First line", "Group", "Effect"],
                               {3: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                4: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter})
        view = _grid(self.model, widths=[110, 50, 70, 190])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(_with_csv(view, self))

    def update_from(self, analysis):
        rows = [(name, count, line, entry["category"], entry["description"])
                for name, (entry, count, line) in sorted(analysis["usage"].items())]
        self.model.set_rows(rows)


class CommentsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.model = RowsModel(["Line", "Lexeme", "Newlines", "Spaces", "After", "CommentText"],
                               {5: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter})
        view = _grid(self.model, widths=[40, 55, 65, 55, 45])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(_with_csv(view, self))

    def update_from(self, analysis):
        self.model.set_rows([(c["line"], c["lexeme"], c["newlines"], c["spaces"], c["after"],
                              c["text"]) for c in analysis["comments"]])


class SyntaxTab(QWidget):
    def __init__(self):
        super().__init__()
        self.model = RowsModel(["Lexeme", "Line", "String", "Type", "Subroutine", "NL&&Spaces",
                                "Indent", "Parse", "PCref"],
                               {2: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter})
        view = _grid(self.model, widths=[50, 40, 90, 80, 80, 75, 50, 50])
        self.analysis = None
        self.info = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(_with_csv(view, self))

    def update_from(self, analysis):
        self.analysis = analysis
        self._fill()

    def set_program(self, info):
        self.info = info
        self._fill()

    def _fill(self):
        if self.analysis is None:
            return
        subs = []
        pcref = {}
        if self.info is not None:
            for row in self.info.sub_rows[1:]:
                subs.append((row["lines"][0], row["lines"][1], row["name"]))
            for pline, sline in self.info.program.source.items():
                pcref.setdefault(sline, pline)
        rows = []
        for k, x in enumerate(self.analysis["lexemes"], 1):
            sub = ""
            indent = ""
            ref = ""
            if x.get("first"):
                sub = "GLOBAL"
                for a, b, name in subs:
                    if a <= x["line"] <= b:
                        sub = name
                if x.get("indent"):
                    indent = x["indent"]
                if x["line"] in pcref:
                    ref = f"{pcref[x['line']]}.0"
            rows.append((k, x["line"], x["text"], x["type"], sub,
                         f"{x['nl']}   {x['spaces']}", indent, "", ref))
        self.model.set_rows(rows)
        self.model.headers[5] = "NL&Spaces"


# ---------------------------------------------------------------- Vars / Subs

VAR_HEADERS = ["Identifier", "Context", "Index", "Array", "Type", "Reference", "Line Scope",
               "PCode Scope"]


def var_table_rows(info, show_arrays=True):
    if info is None:
        return []
    return [(v["name"], v["context"], v["index"], v["array"] if show_arrays else "-", v["type"],
             v["ref"], _scope(*v["lines"]), _scope(*v["pcode"])) for v in info.var_rows]


class VarsSubsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.info = None
        self.vars = RowsModel(VAR_HEADERS)
        self.subs = RowsModel(["Index", "Type", "Identifier", "Params", "Mem", "Line Scope",
                               "Pcode Scope", "Entry", "Exit"])
        self.show_array = QCheckBox("Show array size", checked=True)
        self.show_string = QCheckBox("Show string size")
        self.show_array.toggled.connect(lambda _=False: self.update_from(None))
        self.show_string.toggled.connect(lambda _=False: self.update_from(None))
        top = QHBoxLayout()
        title = QLabel("<b>Variables</b><small>(including Turtle attributes)</small>")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.show_array)
        top.addWidget(self.show_string)
        vars_box = QWidget()
        vl = QVBoxLayout(vars_box)
        vl.setContentsMargins(2, 2, 2, 2)
        vl.addLayout(top)
        vl.addWidget(_with_csv(_grid(self.vars, widths=[80, 95, 50, 50, 60, 70, 75]), self))
        subs_box = QWidget()
        sl = QVBoxLayout(subs_box)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.addWidget(QLabel("<b>Subroutines</b>"))
        sl.addWidget(_with_csv(_grid(self.subs, widths=[40, 80, 90, 60, 45, 75, 75, 45]), self))
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(vars_box)
        split.addWidget(subs_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    def update_from(self, program_info):
        if program_info is not None:
            self.info = program_info.get("tm") if isinstance(program_info, dict) else None
        info = self.info
        rows = var_table_rows(info, self.show_array.isChecked())
        if info is not None and self.show_string.isChecked():
            rows = [r[:4] + ((r[4] + "[64]") if r[4] == "String" else r[4],) + r[5:]
                    for r in rows]
        self.vars.set_rows(rows)
        if info is None:
            self.subs.set_rows([])
            return
        self.subs.set_rows([(s["index"], s["type"], s["name"], s["params"], s["mem"],
                             _scope(*s["lines"]), _scope(*s["pcode"]), s["entry"], s["exit"])
                            for s in info.sub_rows])


# ---------------------------------------------------------------- PCode

class PCodeTab(QWidget):
    COLUMNS = 10

    def __init__(self, get_info=None):
        super().__init__()
        self.get_info = get_info
        self.info = None
        self.model = RowsModel(["PCode"] + [f".{k}" for k in range(1, self.COLUMNS + 1)])
        self.view = _grid(self.model, stretch_last=False,
                          widths=[46] + [46] * self.COLUMNS)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSizeF(_small_font().pointSizeF())
        self.view.setFont(mono)
        self.dec_mc = QRadioButton('"Machine Code"')
        self.dec_asm = QRadioButton('"Assembler" code', checked=True)
        self.hex_mc = QRadioButton('"Machine Code"')
        self.hex_asm = QRadioButton('"Assembler" code')
        group = QButtonGroup(self)
        for b in (self.dec_mc, self.dec_asm, self.hex_mc, self.hex_asm):
            group.addButton(b)
            b.toggled.connect(lambda on: on and self.refresh())
        self.trace_display = QCheckBox("Trace Display")
        self.trace_on_run = QCheckBox("Trace on Run")
        grid = QGridLayout()
        grid.addWidget(QLabel("Display:"), 0, 0, 2, 1)
        grid.addWidget(QLabel("decimal:"), 0, 1)
        grid.addWidget(QLabel("hexadecimal"), 1, 1)
        grid.addWidget(self.dec_mc, 0, 2)
        grid.addWidget(self.dec_asm, 0, 3)
        grid.addWidget(self.hex_mc, 1, 2)
        grid.addWidget(self.hex_asm, 1, 3)
        grid.addWidget(self.trace_display, 0, 4)
        grid.addWidget(self.trace_on_run, 1, 4)
        grid.setColumnStretch(5, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(grid)
        lay.addWidget(_with_csv(self.view, self))
        self.row_of_line = {}
        self.timer = QTimer(self, interval=250)
        self.timer.timeout.connect(self._trace)
        self.timer.start()

    def update_from(self, program_info):
        self.info = program_info.get("tm") if isinstance(program_info, dict) else None
        self.refresh()

    def _cells(self, line):
        """The cells of one PCode line, as the chosen display shows them."""
        hexa = self.hex_mc.isChecked() or self.hex_asm.isChecked()
        asm = self.dec_asm.isChecked() or self.hex_asm.isChecked()

        def num(v):
            if hexa:
                return ("-" if v < 0 else "") + format(abs(v), "X")
            return str(v)
        cells = []
        i = 0
        while i < len(line):
            code = line[i]
            name, n = OPS.get(code, (f"?{code}", 0))
            cells.append(name if asm else num(code))
            i += 1
            if n == -1:
                length = line[i]
                cells.append(num(length))
                for c in line[i + 1:i + 1 + length]:
                    if asm:
                        cells.append(chr(c) if 32 <= c < 127 else f"#{c}")
                    else:
                        cells.append(num(c))
                i += 1 + length
            else:
                for v in line[i:i + n]:
                    cells.append(num(v))
                i += n
        return cells

    def refresh(self):
        info = self.info
        if info is None:
            self.model.set_rows([])
            return
        rows = []
        self.row_of_line = {}
        for n, line in enumerate(info.program.lines, 1):
            cells = self._cells(line)
            self.row_of_line[n] = len(rows)
            for k in range(0, max(1, len(cells)), self.COLUMNS):
                rows.append([str(n) if k == 0 else "..."] + cells[k:k + self.COLUMNS])
        self.model.set_rows(rows)

    def _trace(self):
        if not self.trace_display.isChecked() or not self.isVisible() or self.get_info is None:
            return
        vm = (self.get_info() or {}).get("vm")
        if vm is None or not vm.line:
            return
        row = self.row_of_line.get(vm.line)
        if row is not None:
            self.view.selectRow(row)
            self.view.scrollTo(self.model.index(row, 0))


# ---------------------------------------------------------------- Memory

RED = QColor(0xE0, 0x40, 0x40)


def _blend(a, b, t):
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def memory_colours():
    """Cell colours from the current theme (on the classic theme: the original's greys):
    free cells on the base colour, used cells shaded toward the text colour, the
    subroutine pointers tinted green, and the group separators."""
    pal = QApplication.palette()
    base = pal.color(QPalette.ColorRole.Base)
    text = pal.color(QPalette.ColorRole.Text)
    return {"free": base, "used": _blend(base, text, 0.13),
            "subr": _blend(base, QColor(0x22, 0xCC, 0x44), 0.40),
            "text": text, "stack_sep": _blend(base, text, 0.95),
            "heap_sep": _blend(base, text, 0.30)}


def _char(v):
    if 0 <= v < 32:
        return " "
    if 32 <= v < 256:
        return chr(v)
    return ""


class MemoryModel(QAbstractTableModel):
    """Memory cells, ten to a row group of four rows: names, values, characters, separator."""

    def __init__(self, title, base, separator):
        super().__init__()
        self.title = title
        self.base = base
        self.separator = separator           # "stack_sep" or "heap_sep"
        self.colours = memory_colours()
        self.values = []
        self.labels = {}
        self.special = {}            # address -> text for the character row (subroutine names)
        self.used_to = base - 1
        self.mark = None             # address marked in red on the separator (heap top)

    def set_memory(self, values, labels, special, used_to, mark=None):
        self.beginResetModel()
        self.colours = memory_colours()
        self.values = values
        self.labels = labels
        self.special = special
        self.used_to = used_to
        self.mark = mark
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return 4 * ((len(self.values) + 9) // 10)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 11

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.title if section == 0 else str(section - 1)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r, c = index.row(), index.column()
        group, kind = divmod(r, 4)
        addr = self.base + group * 10 + (c - 1)
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                if kind == 0:
                    a = self.base + group * 10
                    return f"{a:02d}" if self.base == 0 else str(a)
                return ""
            k = addr - self.base
            if k >= len(self.values) or kind == 3:
                return ""
            v = self.values[k]
            if kind == 0:
                return self.labels.get(addr, "")
            if kind == 1:
                return str(v)
            return self.special.get(addr, _char(v))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        col = self.colours
        if role == Qt.ItemDataRole.ForegroundRole:
            return QBrush(col["text"])
        if role == Qt.ItemDataRole.BackgroundRole:
            if kind == 3:
                if c == 0:
                    return None
                if self.mark is not None and addr == self.mark:
                    return QBrush(RED)
                return QBrush(col[self.separator])
            if c == 0:
                return None
            if addr in self.special:
                return QBrush(col["subr"])
            if addr <= self.used_to and addr in self.labels:
                return QBrush(col["used"])
            if addr <= self.used_to and self.base == 0 and addr <= 14:
                return QBrush(col["used"])
            return QBrush(col["free"])
        return None


class MemoryTab(QWidget):
    def __init__(self, machine, get_info):
        super().__init__()
        self.machine = machine
        self.get_info = get_info
        self.show_stack = QCheckBox("Memory Stack", checked=True)
        self.show_heap = QCheckBox("Memory Heap", checked=True)
        self.show_vars = QCheckBox("Variables Table", checked=True)
        button = QPushButton("Show Current State")
        button.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(QLabel("Display:"))
        for w in (self.show_stack, self.show_heap, self.show_vars):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(button)
        self.stack_model = MemoryModel("MStack", 0, "stack_sep")
        self.heap_model = MemoryModel("MHeap", HEAP_BASE, "heap_sep")
        self.stack_view = self._memory_view(self.stack_model)
        self.heap_view = self._memory_view(self.heap_model)
        self.vars_model = RowsModel(VAR_HEADERS)
        self.vars_view = _grid(self.vars_model, widths=[80, 95, 50, 50, 60, 70, 75])
        self.stack_box = _with_csv(self.stack_view, self)
        self.heap_box = _with_csv(self.heap_view, self)
        self.vars_box = _with_csv(self.vars_view, self)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.stack_box)
        split.addWidget(self.heap_box)
        split.addWidget(self.vars_box)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(split)
        self.show_stack.toggled.connect(self.stack_box.setVisible)
        self.show_heap.toggled.connect(self.heap_box.setVisible)
        self.show_vars.toggled.connect(self.vars_box.setVisible)
        self.timer = QTimer(self, interval=500)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _memory_view(self, model):
        view = _grid(model, stretch_last=False, widths=[52] + [46] * 10)
        view.setShowGrid(True)
        h = view.fontMetrics().height() + 3
        view.verticalHeader().setDefaultSectionSize(h)
        view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        view.setRowHeight(3, 4)
        model.modelReset.connect(lambda: self._thin_separators(view, model))
        return view

    @staticmethod
    def _thin_separators(view, model):
        for r in range(3, model.rowCount(), 4):
            view.setRowHeight(r, 4)

    def _tick(self):
        if self.isVisible() and self.machine.running:
            self.refresh()

    def refresh(self):
        info_dict = self.get_info() or {}
        info = info_dict.get("tm")
        vm = info_dict.get("vm")
        self.vars_model.set_rows(var_table_rows(info))
        if info is None or vm is None:
            self.stack_model.set_memory([], {}, {}, -1)
            self.heap_model.set_memory([], {}, {}, -1)
            return
        mem = vm.mem
        memtop = max(mem[12], info.program.memtop, vm.max_memtop)
        top = (memtop // 10 + 2) * 10
        labels = info.static_labels()
        labels.update(info.frame_labels(mem))
        special = {}
        for sub in info.subs:
            special[14 + sub.index] = sub.name
        self.stack_model.set_memory(mem[:top], labels, special, memtop)
        hp = max(mem[14], vm.max_heap, HEAP_BASE + 2)
        heap_top = ((hp - HEAP_BASE) // 10 + 2) * 10
        self.heap_model.set_memory(mem[HEAP_BASE:HEAP_BASE + heap_top], {}, {}, -1,
                                   mark=mem[14] if mem[14] >= HEAP_BASE else None)
