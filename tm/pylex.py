"""Lexical analysis of Turtle Python, shared by the PCode compiler and the Syntax tab."""

import re

from runtime import INPUT_CODES, QUERY_CODES


class Tok:
    __slots__ = ("kind", "text", "line", "col", "value", "nl", "spaces")

    def __init__(self, kind, text, line, col, value=None):
        self.kind = kind          # name op num str code query newline indent dedent eof
        self.text = text
        self.line = line
        self.col = col
        self.value = value
        self.nl = 0               # newlines before the token
        self.spaces = 0           # spaces before the token

    def __repr__(self):
        return f"Tok({self.kind},{self.text!r},{self.line})"


class LexError(Exception):
    def __init__(self, msg, line):
        super().__init__(msg)
        self.line = line


_OPS = sorted("""( ) [ ] { } , : ; . = == != <> < > <= >= + - * / // % ** ~ & | ^ << >>
+= -= *= /= //= %= -> @""".split(), key=len, reverse=True)

_ESCAPES = {"n": "\r\n", "t": "\t", "\\": "\\", "'": "'", '"': '"', "r": "\r", "0": "\0"}


def tokenize(src):
    toks = []
    comments = []           # (line, text)
    indents = [0]
    lines = src.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    depth = 0               # bracket nesting: newlines inside brackets are ignored
    pending_nl = 0
    for lineno, line in enumerate(lines, 1):
        i = 0
        n = len(line)
        # indentation (only at the start of a logical line)
        while i < n and line[i] in " \t":
            i += 1
        if depth == 0:
            if i >= n or line[i] == "#":
                if i < n:
                    comments.append((lineno, line[i:]))
                pending_nl += 1
                continue
            width = len(line[:i].expandtabs(4))
            if width > indents[-1]:
                indents.append(width)
                t = Tok("indent", "", lineno, i)
                toks.append(t)
            else:
                while width < indents[-1]:
                    indents.pop()
                    toks.append(Tok("dedent", "", lineno, i))
                if width != indents[-1]:
                    raise LexError("Inconsistent indentation", lineno)
        first = True
        spaces = i if depth == 0 else 0
        while i < n:
            c = line[i]
            if c in " \t":
                i += 1
                spaces += 1
                continue
            if c == "#":
                comments.append((lineno, line[i:]))
                break
            start = i
            if c.isalpha() or c == "_":
                while i < n and (line[i].isalnum() or line[i] == "_"):
                    i += 1
                t = Tok("name", line[start:i], lineno, start)
            elif c.isdigit():
                if line.startswith(("0x", "0X"), i):
                    i += 2
                    while i < n and line[i] in "0123456789abcdefABCDEF":
                        i += 1
                    t = Tok("num", line[start:i], lineno, start, int(line[start + 2:i], 16))
                else:
                    while i < n and line[i].isdigit():
                        i += 1
                    t = Tok("num", line[start:i], lineno, start, int(line[start:i]))
            elif c in "'\"":
                q = c
                i += 1
                chars = []
                while i < n and line[i] != q:
                    if line[i] == "\\" and i + 1 < n:
                        e = line[i + 1]
                        chars.append(_ESCAPES.get(e, "\\" + e))
                        i += 2
                    else:
                        chars.append(line[i])
                        i += 1
                if i >= n:
                    raise LexError("String not terminated", lineno)
                i += 1
                t = Tok("str", line[start:i], lineno, start, "".join(chars))
            elif c in "?\\" and i + 1 < n and (line[i + 1].isalnum() or line[i + 1] == "_"):
                i += 1
                while i < n and (line[i].isalnum() or line[i] == "_"):
                    i += 1
                name = line[start + 1:i].lower()
                table = QUERY_CODES if c == "?" else INPUT_CODES
                if name not in table:
                    raise LexError(f'"{line[start:i]}" not recognised', lineno)
                t = Tok("query" if c == "?" else "code", line[start:i], lineno, start, table[name])
            else:
                for op in _OPS:
                    if line.startswith(op, i):
                        break
                else:
                    raise LexError(f'Unexpected character "{c}"', lineno)
                i += len(op)
                t = Tok("op", op, lineno, start)
                if op in "([{":
                    depth += 1
                elif op in ")]}":
                    depth = max(0, depth - 1)
            t.nl = pending_nl + (1 if first and toks else 0)
            t.spaces = spaces
            pending_nl = 0
            first = False
            spaces = 0
            toks.append(t)
        if depth == 0 and not first:
            toks.append(Tok("newline", "", lineno, n))
    for _ in indents[1:]:
        toks.append(Tok("dedent", "", len(lines), 0))
    toks.append(Tok("eof", "", len(lines), 0))
    return toks, comments
