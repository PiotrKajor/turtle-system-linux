"""Shared pieces of the Pascal and BASIC front ends: the builtin registry, Python AST
construction helpers, and the string/number helpers their generated code calls."""

import ast
import json
from pathlib import Path

from runtime import INT_MAX, INT_MIN, PROGRAM_FILE, STRING_SIZE, TurtleError, wrap32

REGISTRY = json.loads((Path(__file__).resolve().parent / "data" / "builtins.json")
                      .read_text(encoding="utf-8"))["builtins"]

RETURN_TYPES = {"string": "str", "integer": "int", "boolean": "bool", "char": "char",
                "list": "list", "procedure": "void"}


def builtin_table(lang):
    """name (lower case) -> (python implementation name, return type, signature, params)."""
    table = {}
    colour_alias = {"python": "color", "pascal": "color", "basic": "COLOR"}[lang]
    for row in REGISTRY:
        name = row["names"][lang]
        if not name:
            continue
        py = (row["names"]["python"] or "").lstrip(".") or None
        sig = row["signature"]
        nargs = None if "[" in sig or sig.startswith("s1") else len(sig)
        table[name.lower()] = {"name": name, "python": py, "returns": RETURN_TYPES[row["returns"]],
                               "signature": sig, "nargs": nargs, "params": row["params"]}
    table[colour_alias.lower()] = dict(table["colour"], name=colour_alias)
    return table


class CompileError(SyntaxError):
    def __init__(self, message, line):
        super().__init__(message, (PROGRAM_FILE, line, 0, ""))


# ---------------------------------------------------------------- AST helpers

def loc(node, line):
    node.lineno = node.end_lineno = line
    node.col_offset = node.end_col_offset = 0
    return node


def name(ident, line, store=False):
    return loc(ast.Name(ident, ast.Store() if store else ast.Load()), line)


def const(value, line):
    return loc(ast.Constant(value), line)


def call(func, args, line):
    f = name(func, line) if isinstance(func, str) else func
    return loc(ast.Call(func=f, args=list(args), keywords=[]), line)


def subscript(value, index, line, store=False):
    return loc(ast.Subscript(value=value, slice=index, ctx=ast.Store() if store else ast.Load()),
               line)


def binop(left, op, right, line):
    return loc(ast.BinOp(left=left, op=op, right=right), line)


def compare(left, op, right, line):
    return loc(ast.Compare(left=left, ops=[op], comparators=[right]), line)


def assign(target, value, line):
    return loc(ast.Assign(targets=[target], value=value), line)


def expr_stmt(value, line):
    return loc(ast.Expr(value=value), line)


def finish(body, info=None):
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, PROGRAM_FILE, "exec")
    if info is not None:
        info["python"] = ast.unparse(module)
    return code


def type_text(t):
    """Readable name of a compiler type tuple / string."""
    if isinstance(t, str):
        return {"int": "Integer", "str": "String", "void": "-"}.get(t, t)
    kind = t[0]
    if kind == "str":
        return f"String[{t[1]}]"
    if kind == "array":
        dims = ",".join(f"{lo}..{hi}" for lo, hi in t[1]) if t[1] else ""
        return f"Array[{dims}] of {type_text(t[2])}"
    return {"int": "Integer", "bool": "Boolean", "char": "Char", "void": "-"}.get(kind, kind)


# ---------------------------------------------------------------- runtime helpers

def pascal_div(a, b):
    if b == 0:
        raise ZeroDivisionError
    q = abs(a) // abs(b)
    return wrap32(q if (a >= 0) == (b > 0) else -q)


def pascal_mod(a, b):
    if b == 0:
        raise ZeroDivisionError
    r = abs(a) % abs(b)
    return r if a >= 0 else -r


def truncate(s, size):
    return s[:size] if len(s) > size else s


def set_char(s, index, c, size=STRING_SIZE):
    """s[index] := c for a 1-based string index."""
    if not 1 <= index <= len(s):
        raise TurtleError(f"String index {index} out of range")
    return s[:index - 1] + c + s[index:]


def get_char(s, index):
    if not 1 <= index <= len(s):
        raise TurtleError(f"String index {index} out of range")
    return s[index - 1]


def check_index(i, lo, hi):
    if not lo <= i <= hi:
        raise TurtleError(f"Array index {i} out of range {lo}..{hi}")
    return i - lo


def open_index(i, lst):
    if not 0 <= i < len(lst):
        raise TurtleError(f"Array index {i} out of range 0..{len(lst) - 1}")
    return i


def shl(n, s):
    return wrap32((n << s) & 0xFFFFFFFF)


def shr(n, s):
    return wrap32((n & 0xFFFFFFFF) >> s)


def to_int(text, default=0):
    try:
        return int(str(text).strip())
    except ValueError:
        return default


def new_array(dims, default):
    if not dims:
        return default
    lo, hi = dims[0]
    return [new_array(dims[1:], default) for _ in range(hi - lo + 1)]


def string_functions():
    """Functions shared by Pascal and BASIC (1-based positions)."""
    return {
        "copy": lambda s, start, n: s[max(start, 1) - 1:max(start, 1) - 1 + max(n, 0)],
        "delete": lambda s, start, n: s[:max(start, 1) - 1] + s[max(start, 1) - 1 + max(n, 0):],
        "insert": lambda sub, s, index: s[:max(index, 1) - 1] + sub + s[max(index, 1) - 1:],
        "pos": lambda sub, s: s.find(sub) + 1,
        "upper": str.upper, "lower": str.lower, "title": str.title, "swapcase": str.swapcase,
        "initcap": lambda s: s[:1].upper() + s[1:].lower(),
        "trim": str.strip,
        "replace": lambda s, f, r, n=-1: s.replace(f, r, n if n > 0 else -1),
    }


__all__ = ["INT_MAX", "INT_MIN", "STRING_SIZE"]
