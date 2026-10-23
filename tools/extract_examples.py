#!/usr/bin/env python3
"""Extract the built-in example programs from the original TurtleSystem .exe.

The Delphi executable stores each example as a "compacted" token stream per language
(Pascal, BASIC, Python). This tool locates the per-language tables inside the exe, decodes
the streams and lays the programs out as source text, writing them to examples/<lang>/.

Compacted stream format (reverse engineered from TurtleSystem 15.95):
  byte 0       format version ('d' Pascal/BASIC, 'e' Python)
  varint       number of identifiers, then each identifier with bit 7 set on its last char
  tokens       0x21.. builtin command names (per-language table), then lexemes via a
               token->lexeme byte map; some lexemes carry a varint payload:
               identifier/constant/input-code index, or length + text for literals
  0x20         end of code
  varint       number of comments, then per comment:
               varint token position, newlines-before, flag, indent (0x9F = like code),
               varint length, text
  varint       n: values < 151 are chr(n + 32); larger are two bytes
               (b1 - 183) * 224 + (b2 - 32)

Usage: extract_examples.py path/to/TurtleSystem.exe [output_dir]
"""

import json
import re
import struct
import sys
from pathlib import Path

LANG_EXT = {"pascal": ".tgp", "basic": ".tgb", "python": ".tgy"}
LANG_INDEX = {"python": 0, "pascal": 1, "basic": 2}   # order of spellings in the lexeme setup calls


class PE:
    def __init__(self, path):
        self.d = d = Path(path).read_bytes()
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        nsec = struct.unpack_from("<H", d, pe + 6)[0]
        opt = struct.unpack_from("<H", d, pe + 20)[0]
        self.base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
        self.secs = []
        for i in range(nsec):
            o = pe + 24 + opt + 40 * i
            vs, va, rs, ro = struct.unpack_from("<IIII", d, o + 8)
            self.secs.append((d[o:o + 8].rstrip(b"\0").decode(), va, vs, ro, rs))

    def f2va(self, off):
        for _, va, _, ro, rs in self.secs:
            if ro <= off < ro + rs:
                return self.base + va + off - ro
        return None

    def va2f(self, v):
        for _, va, _, ro, rs in self.secs:
            if rs and self.base + va <= v < self.base + va + rs:
                return v - self.base - va + ro
        return None

    def section(self, name):
        for n, va, vs, ro, rs in self.secs:
            if n == name:
                return ro, ro + rs
        raise KeyError(name)

    def u32(self, off):
        return struct.unpack_from("<I", self.d, off)[0]

    def astr(self, va, maxlen=1 << 20):
        """Delphi AnsiString literal whose characters start at virtual address va."""
        if not va:
            return None
        f = self.va2f(va)
        if f is None or f < 8 or self.u32(f - 8) != 0xFFFFFFFF:
            return None
        n = self.u32(f - 4)
        return self.d[f:f + n].decode("latin1") if n <= maxlen else None

    def literal_refs(self, text, lo, hi):
        """DATA offsets of pointers to the AnsiString literal `text`."""
        pat = b"\xff\xff\xff\xff" + struct.pack("<I", len(text)) + text.encode("latin1") + b"\x00"
        refs = []
        for m in re.finditer(re.escape(pat), self.d):
            va = struct.pack("<I", self.f2va(m.start() + 8))
            j = self.d.find(va, lo, hi)
            while j != -1:
                refs.append(j)
                j = self.d.find(va, j + 1, hi)
        return refs


# ---------------------------------------------------------------- tables inside the exe

def lexeme_table(pe):
    """Lexeme ordinal -> (name, [python, pascal, basic] spellings), read from the setup code.

    The setup routine calls one helper per lexeme: `mov al, ordinal; mov ecx, @name` with three
    (flag, @spelling) pairs pushed before the call, Python's spelling pushed last."""
    d = pe.d
    lit = d.find(b"\xff\xff\xff\xff\x07\x00\x00\x00ltWhile\x00")
    ref = d.find(struct.pack("<I", pe.f2va(lit + 8)))      # `mov ecx, @'ltWhile'` operand
    call = d.find(b"\xe8", ref + 4)                          # the helper call after it
    helper = pe.f2va(call) + 5 + struct.unpack_from("<i", d, call + 1)[0]
    # scan backwards/forwards over the whole routine for calls to the helper
    start, end = ref - 0x1200, ref + 0x1200
    lexemes = {}
    i = start
    while i < end:
        if d[i] == 0xE8 and pe.f2va(i) + 5 + struct.unpack_from("<i", d, i + 1)[0] == helper:
            # walk back over: b0 <al>, b2 <dl>, b9 <ecx>, then 3x (68 <str> | 6a 00) + 6a <flag>
            j = i
            if d[j - 2] != 0xB0:
                i += 1
                continue
            ordinal = d[j - 1]
            j -= 2
            if d[j - 2] == 0xB2 or (d[j - 2] in (0x31, 0x33) and d[j - 1] == 0xD2):
                j -= 2
            if d[j - 5] != 0xB9:
                i += 1
                continue
            name = pe.astr(pe.u32(j - 4))
            j -= 5
            spell = []
            for _ in range(3):
                if d[j - 5] == 0x68:
                    spell.append(pe.astr(pe.u32(j - 4)))
                    j -= 5
                else:                     # push 0
                    spell.append(None)
                    j -= 2
                j -= 2                     # push flag
            lexemes[ordinal] = (name, spell)   # spell[0] = last pushed = Python
        i += 1
    return lexemes


def language_tables(pe):
    """Per language: builtin names (tokens 0x21..) and the token->lexeme map that follows them."""
    lo, hi = pe.section("DATA")
    tables = {}
    for lang, word in (("python", "forward"), ("pascal", "forward"), ("basic", "FORWARD")):
        for ref in pe.literal_refs(word, lo, hi):
            s = ref
            while pe.u32(s - 4) and pe.astr(pe.u32(s - 4), 40) is not None:
                s -= 4
            e = ref
            while pe.u32(e) and pe.astr(pe.u32(e), 40) is not None:
                e += 4
            names = [pe.astr(pe.u32(o)) for o in range(s, e, 4)]
            if len(names) < 100:
                continue
            raw = pe.d[e:e + 120]
            lmap = []
            for b in raw:
                if b == 0 or b > 117:
                    break
                lmap.append(b)
            is_python = "capitalize" in names
            is_basic = names[0].isupper()
            kind = "python" if is_python else ("basic" if is_basic else "pascal")
            if kind == lang:
                tables[lang] = {"names": names, "map": lmap, "end": e}
    return tables


def examples(pe, tables, lexemes):
    """Per language: menu groups and (name, title, compacted body) records in menu order."""
    lo, hi = pe.section("DATA")
    result = {}
    for lang, t in tables.items():
        o = t["end"]
        while not ((pe.astr(pe.u32(o + 4), 100) or "").startswith("Examples 1")):
            o += 1
        groups = []
        while (pe.astr(pe.u32(o + 4), 100) or "").startswith("Examples"):
            groups.append({"name": pe.astr(pe.u32(o)), "title": pe.astr(pe.u32(o + 4)),
                           "first": pe.u32(o + 8), "last": pe.u32(o + 12)})
            o += 16
        total = groups[-1]["last"]
        # find the record array: name pointer, optional flag, title, body (compacted, 'd'/'e')
        records = None
        for size in (12, 16):
            t_off = size - 8
            for s in range(lo, hi - size * total, 4):
                ok = 0
                for k in range(total):
                    r = s + size * k
                    body_ptr = pe.u32(r + t_off + 4)
                    body = pe.astr(body_ptr) if body_ptr else ""
                    if not (pe.astr(pe.u32(r), 60) and pe.astr(pe.u32(r + t_off), 200)
                            and body is not None and (body == "" or body[:1] in "de")):
                        break
                    ok += 1
                if ok == total:
                    recs = []
                    for k in range(total):
                        r = s + size * k
                        body = pe.astr(pe.u32(r + t_off + 4)) if pe.u32(r + t_off + 4) else None
                        recs.append((pe.astr(pe.u32(r)), pe.astr(pe.u32(r + t_off)), body))
                    # the bodies must decode as this language (Pascal programs start `program`)
                    sample = next(b for _, _, b in recs if b)
                    if (lang == "python") != (sample[0] == "e"):
                        continue
                    try:
                        first = decode(sample, lang, tables, lexemes)[0][0]
                    except (IndexError, KeyError):
                        continue
                    if lang == "python" or (first.lex == "ltProgram") == (lang == "pascal"):
                        records = recs
                        break
            if records:
                break
        result[lang] = {"groups": groups, "records": records}
    return result


# ---------------------------------------------------------------- decoding

def varint(b, i):
    if b[i] < 183:
        return b[i] - 32, i + 1
    return (b[i] - 183) * 224 + (b[i + 1] - 32), i + 2


class Tok:
    __slots__ = ("kind", "text", "lex")

    def __init__(self, kind, text, lex=None):
        self.kind, self.text, self.lex = kind, text, lex

    def __repr__(self):
        return f"{self.kind}:{self.text}"


def decode(body, lang, tables, lexemes):
    t = tables[lang]
    names, lmap = t["names"], t["map"]
    li = LANG_INDEX[lang]
    b = body.encode("latin1")
    i = 1
    count, i = varint(b, i)
    ids = []
    for _ in range(count):
        j = i
        while b[j] < 0x80:
            j += 1
        ids.append((b[i:j] + bytes([b[j] & 0x7F])).decode("latin1"))
        i = j + 1
    toks = []
    while i < len(b):
        c = b[i]
        i += 1
        if c == 0x20:
            break
        if c < 0x21 + len(names):
            toks.append(Tok("builtin", names[c - 0x21]))
            continue
        ordinal = lmap[c - 0x21 - len(names)]
        lname, spell = lexemes[ordinal]
        if lname in ("ltId", "ltConstant", "ltKey", "ltQuery", "ltIdK"):
            v, i = varint(b, i)
            toks.append(Tok("id", ids[v - 1], lname))
        elif lname in ("ltDecinum", "ltHexnum", "ltString", "ltString3", "ltChar"):
            n, i = varint(b, i)
            toks.append(Tok("lit", b[i:i + n].decode("latin1"), lname))
            i += n
        elif lname in ("ltEol", "ltIndent", "ltDedent"):
            toks.append(Tok(lname, ""))
        else:
            text = spell[li]
            if lname in ("ltSmul", "ltMulS"):
                text = "*"
            kind = "op" if not text[0].isalpha() else "kw"
            if lname in ("ltTrue", "ltFalse", "ltNone"):
                kind = "val"
            toks.append(Tok(kind, text, lname))
    comments = []
    if i < len(b):
        n, i = varint(b, i)
        for _ in range(n):
            pos, i = varint(b, i)
            before = b[i] - 32
            indent = b[i + 2]
            i += 3
            length, i = varint(b, i)
            comments.append({"pos": pos, "before": before, "indent": indent,
                             "text": b[i:i + length].decode("latin1")})
            i += length
    return toks, comments


# ---------------------------------------------------------------- layout

WORDY = re.compile(r"[A-Za-z0-9_$%\"'?\\]")


def joined(prev, cur):
    """Narrow spacing: a space only where two word-like tokens meet, or after a keyword."""
    if prev is None:
        return cur.text
    if prev.kind == "kw" and cur.text not in (")", ":", ";", ",", ".", "]"):
        return " " + cur.text
    if cur.kind == "kw" and (WORDY.match(prev.text[-1]) or prev.text in (")", "]")):
        return " " + cur.text
    if WORDY.match(prev.text[-1]) and WORDY.match(cur.text[0]):
        return " " + cur.text
    return cur.text


class Line:
    def __init__(self, level):
        self.level = level
        self.toks = []
        self.text = ""
        self.comments = []   # (indent flag, text, trailing)

    def add(self, tok):
        self.text += joined(self.toks[-1] if self.toks else None, tok)
        self.toks.append(tok)


def render_lines(lines, indent_unit, comment_wrap=lambda s: s):
    """Turn Line objects into text, placing own-line comments at the next code line's level."""
    out = []
    for n, line in enumerate(lines):
        own = [c for c in line.comments if not c[2]]
        trailing = [c for c in line.comments if c[2]]
        if own:
            nxt = next((l.level for l in lines[n:] if l.toks), line.level)
            for flag, text, _ in own:
                pad = indent_unit * nxt if flag == 0x9F else " " * max(0, flag - 32)
                out.append(pad + comment_wrap(text))
            if not line.toks and not trailing:
                continue
        if not line.toks and not trailing:
            out.append("")
            continue
        text = indent_unit * line.level + line.text
        for flag, ctext, _ in trailing:
            gap = 2 if flag == 0x9F else max(1, flag - 32)
            text += " " * gap + comment_wrap(ctext)
        out.append(text.rstrip())
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def strip_hints(toks):
    """Remove Python type hints (`name: type` in def parameters and annotations, `-> type`)."""
    out = []
    i = 0
    depth = 0
    in_def = False
    while i < len(toks):
        t = toks[i]
        if t.lex == "ltDef":
            in_def, depth = True, 0
        if t.text in ("(", "["):
            depth += 1
        elif t.text in (")", "]"):
            depth -= 1
        if t.lex == "ltHint":
            # skip `-> type` up to the colon ending the def line
            i += 1
            while i < len(toks) and toks[i].lex != "ltColon":
                i += 1
            continue
        if t.lex == "ltColon" and out and out[-1].kind == "id":
            starts_line = len(out) < 2 or out[-2].lex in ("ltEol", "ltIndent", "ltDedent")
            in_params = in_def and depth == 1
            if in_params or starts_line:
                # skip the type expression: until , ) = Eol at this depth
                j = i + 1
                d = 0
                while j < len(toks):
                    x = toks[j]
                    if x.text in ("(", "["):
                        d += 1
                    elif x.text in (")", "]"):
                        if d == 0:
                            break
                        d -= 1
                    elif d == 0 and (x.lex in ("ltComma", "ltAsgn", "ltEol")):
                        break
                    j += 1
                if starts_line and j < len(toks) and toks[j].lex == "ltEol":
                    # bare declaration `x: int` - keep it, Python accepts annotations
                    out.append(t)
                    i += 1
                    continue
                i = j
                continue
        if t.lex == "ltEol":
            in_def = False
        out.append(t)
        i += 1
    return out


def layout_eol_language(toks, comments, lang, hints=False):
    """Python and BASIC: lines come from Eol tokens; comments sit before token `pos`."""
    # comment positions count tokens without Python's indent/dedent markers
    counted = [k for k, t in enumerate(toks) if t.kind not in ("ltIndent", "ltDedent")]
    at = {}
    for c in comments:
        if c["pos"] < len(counted):
            at.setdefault(counted[c["pos"]], []).append(c)
        else:
            at.setdefault(len(toks), []).append(c)
    lines = [Line(0)]
    level = 0
    removed = set()
    if lang == "python" and not hints:
        kept = strip_hints(toks)
        kept_ids = {id(t) for t in kept}
        removed = {k for k, t in enumerate(toks) if id(t) not in kept_ids}
    for k, t in enumerate(toks + [None]):
        for c in at.get(k, ()):
            trailing = bool(lines[-1].toks)
            lines[-1].comments.append((c["indent"], c["text"], trailing))
        if t is None:
            break
        if k in removed:
            continue
        if t.kind == "ltEol":
            lines.append(Line(level))
        elif t.kind == "ltIndent":
            level += 1
            lines[-1].level = level
        elif t.kind == "ltDedent":
            level = max(0, level - 1)
            lines[-1].level = level
        else:
            lines[-1].add(t)
    if lang == "basic":
        _basic_indent(lines)
    return render_lines(lines, "    " if lang == "python" else "  ")


def _basic_indent(lines):
    level = 0
    in_fn = False
    for line in lines:
        words = [t.text for t in line.toks]
        if not words:
            line.level = level
            continue
        first = words[0]
        if first in ("ENDPROC", "NEXT", "ENDWHILE", "UNTIL", "ENDIF"):
            level = max(0, level - 1)
        if first == "ELSE":
            line.level = max(0, level - 1)
        else:
            line.level = level
        if first == "=" and in_fn:
            line.level = level
            level = max(0, level - 1)
            in_fn = False
        if first in ("DEF",):
            level += 1
            in_fn = len(words) > 1 and words[1].startswith("FN")
        elif first in ("FOR", "WHILE", "REPEAT"):
            level += 1
        elif first == "IF" and words[-1] == "THEN":
            level += 1


def layout_pascal(toks, comments):
    """Pascal has no Eol tokens: lay out statements, placing comments before token `pos`."""
    at = {}
    for c in comments:
        at.setdefault(min(c["pos"], len(toks)), []).append(c)
    lines = [Line(0)]
    stack = []          # 'block' (begin/repeat/var/const) or 'single' (one statement after then/do/else)
    paren = 0
    pending_nl = False

    def depth():
        return len(stack)

    def newline():
        nonlocal pending_nl
        pending_nl = True

    def start_line():
        nonlocal pending_nl
        if lines[-1].toks or lines[-1].comments:
            lines.append(Line(depth()))
        else:
            lines[-1].level = depth()
        pending_nl = False

    def pop_singles():
        while stack and stack[-1] == "single":
            stack.pop()

    for k, t in enumerate(toks + [None]):
        for c in at.get(k, ()):
            if c["before"] == 0 and lines[-1].toks:
                lines[-1].comments.append((c["indent"], c["text"], True))
            else:
                if lines[-1].toks or lines[-1].comments:
                    lines.append(Line(depth()))
                for _ in range(max(0, c["before"] - 1)):
                    if lines[-1].toks or lines[-1].comments:
                        lines.append(Line(depth()))
                    lines.append(Line(depth()))
                lines[-1].comments.append((c["indent"], c["text"], False))
                lines.append(Line(depth()))
                pending_nl = False
        if t is None:
            break
        lx = t.lex
        if lx == "ltEnd" or lx == "ltUntil":
            pop_singles()
            if stack:
                stack.pop()
            start_line()
        elif lx in ("ltBegin", "ltRepeat"):
            if stack and stack[-1] == "decl":
                stack.pop()
            if stack and stack[-1] == "single":
                stack.pop()          # `then begin` - begin at the statement's level
            start_line()
        elif lx == "ltElse":
            pop_singles()
            start_line()
        elif lx in ("ltProcedure", "ltFunction"):
            while stack and stack[-1] == "decl":
                stack.pop()
            start_line()
        elif lx in ("ltVar", "ltConst") and paren == 0:
            while stack and stack[-1] == "decl":
                stack.pop()
            start_line()
        elif pending_nl:
            start_line()
        lines[-1].add(t)
        if lx in ("ltLbkt", "ltLsqb"):
            paren += 1
        elif lx in ("ltRbkt", "ltRsqb"):
            paren -= 1
        elif lx in ("ltBegin", "ltRepeat"):
            stack.append("block")
            newline()
        elif lx in ("ltVar", "ltConst") and paren == 0:
            stack.append("decl")
            newline()
        elif lx in ("ltThen", "ltDo") or lx == "ltElse":
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            if nxt is not None and nxt.lex == "ltIf" and lx == "ltElse":
                continue
            if nxt is not None and nxt.lex != "ltBegin":
                stack.append("single")
            newline()
        elif lx == "ltSemicolon" and paren == 0:
            pop_singles()
            newline()
    return render_lines(lines, "  ", lambda s: "{" + s + "}")


# ---------------------------------------------------------------- main

def main():
    exe = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "examples"
    pe = PE(exe)
    lexemes = lexeme_table(pe)
    tables = language_tables(pe)
    found = examples(pe, tables, lexemes)
    index = {}
    for lang, info in found.items():
        folder = out_dir / lang
        folder.mkdir(parents=True, exist_ok=True)
        entries = []
        for n, (name, title, body) in enumerate(info["records"], 1):
            if not body:
                continue
            toks, comments = decode(body, lang, tables, lexemes)
            if lang == "pascal":
                text = layout_pascal(toks, comments)
            else:
                text = layout_eol_language(toks, comments, lang)
            path = folder / f"{n:03d}_{name}{LANG_EXT[lang]}"
            path.write_text(text, encoding="utf-8")
            if lang == "python":            # also with the functional type hints kept
                hinted = out_dir / "python_hints"
                hinted.mkdir(parents=True, exist_ok=True)
                (hinted / path.name).write_text(
                    layout_eol_language(toks, comments, lang, hints=True), encoding="utf-8")
            entries.append({"n": n, "name": name, "title": title, "file": path.name})
        index[lang] = {"groups": info["groups"], "examples": entries}
        print(f"{lang}: {len(entries)} examples")
    (out_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
