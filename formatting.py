"""Edit-menu source tools: auto-format, remove comments, remove Python type hints."""

import io
import re
import tokenize


# ---------------------------------------------------------------- comment scanning

def _split_code_comment(line, lang, in_block):
    """(code part, comment part, still inside a Pascal block comment) for one line."""
    code, comment = [], []
    i, n = 0, len(line)
    quote = {"python": "'\"", "pascal": "'", "basic": '"'}[lang]
    while i < n:
        if in_block:
            closer = in_block
            j = line.find(closer, i)
            if j < 0:
                comment.append(line[i:])
                return "".join(code), "".join(comment), in_block
            comment.append(line[i:j + len(closer)])
            i = j + len(closer)
            in_block = None
            continue
        c = line[i]
        if c in quote:
            j = i + 1
            while j < n and line[j] != c:
                j += 2 if (lang == "python" and line[j] == "\\") else 1
            code.append(line[i:j + 1])
            i = j + 1
        elif lang == "python" and c == "#" or lang == "pascal" and line.startswith("//", i):
            comment.append(line[i:])
            break
        elif lang == "basic" and line.startswith("REM", i) and (i == 0 or not line[i - 1].isalnum()) \
                and (i + 3 >= n or not line[i + 3].isalnum()):
            comment.append(line[i:])
            break
        elif lang == "pascal" and c == "{":
            in_block = "}"
            continue
        elif lang == "pascal" and line.startswith("(*", i):
            in_block = "*)"
            comment.append("(*")
            i += 2
            continue
        else:
            code.append(c)
            i += 1
    return "".join(code), "".join(comment), in_block


def remove_comments(lang, text):
    out = []
    in_block = None
    for line in text.split("\n"):
        code, comment, in_block = _split_code_comment(line, lang, in_block)
        if comment and not code.strip():
            continue                      # a comment-only line disappears
        out.append(code.rstrip() if comment else line)
    return "\n".join(out)


# ---------------------------------------------------------------- auto-format

def _python_format(text):
    """Re-indent Python blocks to 4 spaces per level and strip trailing spaces."""
    lines = text.split("\n")
    protected = re.sub(r"([?\\])([A-Za-z_])", lambda m: ("_Q" if m.group(1) == "?" else "_K")
                       + m.group(2), text)
    levels = {}
    level = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(protected).readline):
            if tok.type == tokenize.INDENT:
                level += 1
            elif tok.type == tokenize.DEDENT:
                level -= 1
            elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                                  tokenize.ENDMARKER):
                levels.setdefault(tok.start[0], level)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return "\n".join(l.rstrip() for l in lines)
    out = []
    pending_comments = []
    for n, line in enumerate(lines, 1):
        stripped = line.strip()
        if n in levels:
            indent = "    " * levels[n]
            for c in pending_comments:          # comments take the next code line's indent
                out.append(indent + c if c else "")
            pending_comments = []
            out.append(indent + stripped)
        elif stripped.startswith("#") or not stripped:
            pending_comments.append(stripped)
        else:                                   # continuation lines: keep as they are
            out.extend(pending_comments)
            pending_comments = []
            out.append(line.rstrip())
    out.extend(pending_comments)
    return "\n".join(out)


def _pascal_format(text):
    out = []
    level = 0
    single = 0          # extra indent for one statement after then/do/else
    in_decl = False
    in_block = None
    for line in text.split("\n"):
        code, comment, in_block_after = _split_code_comment(line, "pascal", in_block)
        stripped = line.strip()
        words = re.findall(r"[A-Za-z_]\w*", code.lower())
        first = words[0] if words else ""
        if in_block:                       # inside a multi-line comment: keep the line
            out.append(line.rstrip())
            in_block = in_block_after
            continue
        in_block = in_block_after
        if not stripped:
            out.append("")
            continue
        if first in ("procedure", "function", "begin", "var", "const", "program") and in_decl:
            level -= 1
            in_decl = False
        if first in ("end", "until"):
            level = max(0, level - 1)
            single = 0
        indent = level + (single if first not in ("begin", "else", "end", "until") else 0)
        if first == "else" and single:
            single = 0
        out.append("  " * max(0, indent) + stripped)
        opened = words.count("begin") + words.count("repeat")
        closed = words.count("end") + words.count("until") - (1 if first in ("end", "until") else 0)
        level += opened - closed
        if words and words[-1] in ("then", "do", "else") and "begin" not in words[-1:]:
            single = 1
        elif first not in ("begin",) and code.rstrip().endswith(";"):
            single = 0
        if first in ("var", "const") and len(words) == 1:
            level += 1
            in_decl = True
    return "\n".join(out)


def _basic_format(text):
    out = []
    level = 0
    in_fn = False
    for line in text.split("\n"):
        stripped = line.strip()
        code, _, _ = _split_code_comment(stripped, "basic", None)
        words = code.split()
        first = words[0] if words else ""
        if not stripped:
            out.append("")
            continue
        if first in ("ENDPROC", "NEXT", "ENDWHILE", "UNTIL", "ENDIF") or first.startswith("NEXT"):
            level = max(0, level - 1)
        indent = level - 1 if first == "ELSE" else level
        out.append("  " * max(0, indent) + stripped)
        if code.startswith("=") and in_fn:
            level = max(0, level - 1)
            in_fn = False
        if first == "DEF":
            level += 1
            in_fn = len(words) > 1 and words[1].startswith("FN")
        elif first in ("FOR", "WHILE", "REPEAT"):
            level += 1
        elif first == "IF" and code.rstrip().endswith("THEN"):
            level += 1
    return "\n".join(out)


def auto_format(lang, text):
    formatted = {"python": _python_format, "pascal": _pascal_format,
                 "basic": _basic_format}[lang](text)
    return re.sub(r"\n{3,}", "\n\n", formatted.rstrip()) + "\n"


# ---------------------------------------------------------------- Python type hints

def remove_type_hints(text):
    """Remove `name: type` in def parameters and annotated assignments, and `-> type`."""
    # same-length placeholders keep token columns valid for the original text
    protected = re.sub(r"([?\\])([A-Za-z_])", lambda m: ("Q" if m.group(1) == "?" else "K")
                       + m.group(2), text)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(protected).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    lines = text.split("\n")
    cuts = []                       # (line, start col, end line, end col)
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.type == tokenize.NAME and t.string == "def":
            depth = 0
            j = i + 1
            while j < n:
                tj = toks[j]
                if tj.string in "([{" and tj.type == tokenize.OP:
                    depth += 1
                elif tj.string in ")]}" and tj.type == tokenize.OP:
                    depth -= 1
                    if depth == 0:
                        # `-> type` before the colon
                        if j + 1 < n and toks[j + 1].string == "->":
                            k = j + 2
                            while k < n and toks[k].string != ":":
                                k += 1
                            cuts.append((toks[j + 1].start, toks[k - 1].end))
                            j = k
                        break
                elif tj.string == ":" and depth == 1 and toks[j - 1].type == tokenize.NAME:
                    k = j + 1
                    d = 0
                    while k < n:
                        tk = toks[k]
                        if tk.string in "([{":
                            d += 1
                        elif tk.string in ")]}":
                            if d == 0:
                                break
                            d -= 1
                        elif d == 0 and tk.string in (",", "="):
                            break
                        k += 1
                    cuts.append((tj.start, toks[k - 1].end))
                    j = k
                    continue
                j += 1
            i = j
            continue
        # annotated assignment at the start of a statement: name: type = value
        if (t.type == tokenize.NAME and i + 1 < n and toks[i + 1].string == ":"
                and (i == 0 or toks[i - 1].type in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                                                    tokenize.DEDENT))):
            k = i + 2
            while k < n and toks[k].string != "=" and toks[k].type not in (tokenize.NEWLINE,
                                                                            tokenize.NL):
                k += 1
            if k < n and toks[k].string == "=":
                cuts.append((toks[i + 1].start, toks[k - 1].end))
        i += 1
    for (sl, sc), (el, ec) in sorted(cuts, reverse=True):
        if sl == el:
            line = lines[sl - 1]
            lines[sl - 1] = line[:sc].rstrip() + line[ec:]
    return "\n".join(lines)
