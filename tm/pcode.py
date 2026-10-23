"""Turtle Machine PCode: the opcode table, a program container and a line-based assembler.

PCode is organised in numbered lines (from 1); jumps and subroutine calls name a line.
The binary .tmc layout written by the original is: number of lines, 1, memory top after the
globals, then for every line its length followed by its integers.
"""

import json
import struct
from pathlib import Path

_TABLE = json.loads((Path(__file__).resolve().parent.parent / "data" / "opcodes.json").read_text())
OPS = {int(k): (v[0], v[1]) for k, v in _TABLE.items()}       # code -> (mnemonic, operands)
CODE = {name: code for code, (name, _) in OPS.items()}        # mnemonic -> code
NOPS = {code: n for code, (_, n) in OPS.items()}


class Label:
    """A forward reference to a PCode line, patched when the line becomes known."""
    __slots__ = ("line", "uses")

    def __init__(self):
        self.line = None
        self.uses = []


class Program:
    def __init__(self):
        self.lines = [[]]            # lines[0] is PCode line 1
        self.memtop = 0
        self.source = {}             # PCode line -> source line where a statement starts

    # ------------------------------------------------ building
    @property
    def line_no(self):
        """Number of the line now being written."""
        return len(self.lines)

    def newline(self):
        """Start a new line unless the current one is still empty."""
        if self.lines[-1]:
            self.lines.append([])

    def emit(self, op, *args):
        self.lines[-1].append(CODE[op])
        self.lines[-1].extend(args)

    def emit_str(self, text):
        self.lines[-1].append(CODE["LSTR"])
        self.lines[-1].append(len(text))
        self.lines[-1].extend(ord(c) for c in text)

    def emit_jump(self, op, label):
        """JUMP / IFNO / SUBR to a line that may not be known yet."""
        self.lines[-1].append(CODE[op])
        if isinstance(label, int):
            self.lines[-1].append(label)
        elif label.line is not None:
            self.lines[-1].append(label.line)
        else:
            self.lines[-1].append(0)
            label.uses.append((len(self.lines) - 1, len(self.lines[-1]) - 1))

    def place(self, label):
        """Make the label refer to the next line to be written (starting one if needed)."""
        self.newline()
        label.line = self.line_no
        for li, pos in label.uses:
            self.lines[li][pos] = label.line
        label.uses.clear()

    def mark_source(self, src_line):
        """Record that the statement being compiled starts on the current PCode line."""
        self.source.setdefault(self.line_no, src_line)

    def source_line(self, pcode_line):
        best = None
        for k, v in self.source.items():
            if k <= pcode_line and (best is None or k > best[0]):
                best = (k, v)
        return best[1] if best else None

    def finish(self):
        if not self.lines[-1]:
            self.lines.pop()

    # ------------------------------------------------ files
    def to_tmc(self):
        ints = [len(self.lines), 1, self.memtop]
        for line in self.lines:
            ints.append(len(line))
            ints.extend(line)
        return struct.pack("<%di" % len(ints), *[(v + 2**31) % 2**32 - 2**31 for v in ints])

    @classmethod
    def from_tmc(cls, data):
        ints = struct.unpack("<%di" % (len(data) // 4), data)
        p = cls()
        p.lines = []
        p.memtop = ints[2]
        i = 3
        for _ in range(ints[0]):
            n = ints[i]
            p.lines.append(list(ints[i + 1:i + 1 + n]))
            i += 1 + n
        return p


def disassemble_line(line):
    """[(mnemonic or None, [values])] for one line; LSTR carries its string."""
    out = []
    i = 0
    while i < len(line):
        code = line[i]
        name, n = OPS.get(code, ("?%d" % code, 0))
        i += 1
        if n == -1:
            length = line[i]
            out.append((name, [length] + line[i + 1:i + 1 + length]))
            i += 1 + length
        else:
            out.append((name, line[i:i + n]))
            i += n
    return out


def line_text(line):
    parts = []
    for name, vals in disassemble_line(line):
        if name == "LSTR":
            parts.append("LSTR " + repr("".join(chr(v) for v in vals[1:])))
        else:
            parts.append(" ".join([name] + [str(v) for v in vals]))
    return " ; ".join(parts)
