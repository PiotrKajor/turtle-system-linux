"""Turtle Python front end: compiles a program to a Python code object for the Turtle Machine.

Turtle Python is Python with integer-only Turtle semantics. The source is rewritten so that
?name / \\name input codes become calls and constants, `/` becomes Turtle's rounded integer
division, + - * ** << wrap to 32 bits, the turtle variables (turtx ... turtc) read and write the
current turtle, and `list.del(i)` works.
"""

import ast
import copy
import re
import sys

from colours import COLOURS
from runtime import (INPUT_CODES, PROGRAM_FILE, QUERY_CODES, _TurtleView, base_environment,
                     div_round, wrap32)

NAME = "Python"
EXTENSIONS = (".tgy", ".py")
TURTLE_VARS = {"turtx": 0, "turty": 1, "turtd": 2, "turta": 3, "turtt": 4, "turtc": 5}
WRAPPED_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Pow, ast.LShift)


def _rewrite_codes(src):
    """Replace ?name and \\name outside strings and comments."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == "#":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(src[i:j])
            i = j
        elif c in "'\"":
            q = src[i:i + 3] if src[i:i + 3] in ("'''", '"""') else c
            j = i + len(q)
            while j < n:
                if src[j] == "\\":
                    j += 2
                elif src.startswith(q, j):
                    j += len(q)
                    break
                elif len(q) == 1 and src[j] == "\n":
                    break
                else:
                    j += 1
            out.append(src[i:j])
            i = j
        elif c in "?\\" and i + 1 < n and (src[i + 1].isalnum() or src[i + 1] == "_"):
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            name = src[i + 1:j].lower()
            line = src.count("\n", 0, i) + 1
            if c == "?":
                if name not in QUERY_CODES:
                    raise SyntaxError(f'"?{name}" not recognised', (PROGRAM_FILE, line, 0, ""))
                out.append(f"_query_({QUERY_CODES[name]})")
            else:
                if name not in INPUT_CODES:
                    raise SyntaxError(f'"\\{name}" not recognised', (PROGRAM_FILE, line, 0, ""))
                out.append(f"({INPUT_CODES[name]})")
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _call(name, *args, node):
    return ast.copy_location(ast.Call(func=ast.Name(name, ast.Load()), args=list(args),
                                      keywords=[]), node)


class _Transformer(ast.NodeTransformer):
    def visit_Call(self, node):
        # address(var): turtle properties get fixed addresses, other variables a frame cell
        if (isinstance(node.func, ast.Name) and node.func.id == "address" and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)):
            var = node.args[0].id
            if var in TURTLE_VARS:
                return _call("_address_turtle_", ast.Constant(TURTLE_VARS[var]), node=node)
            frame_vars = _call("_frame_vars_", node=node)
            return _call("_address_var_", frame_vars, ast.Constant(var), node=node)
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if node.id in TURTLE_VARS:
            return ast.copy_location(ast.Subscript(value=ast.Name("_turtle_", ast.Load()),
                                                   slice=ast.Constant(TURTLE_VARS[node.id]),
                                                   ctx=node.ctx), node)
        return node

    def _strip_declaration(self, node):
        node.names = [n for n in node.names if n not in TURTLE_VARS]
        return node if node.names else ast.copy_location(ast.Pass(), node)

    visit_Global = visit_Nonlocal = _strip_declaration

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Div):
            return _call("_div_", node.left, node.right, node=node)
        if isinstance(node.op, WRAPPED_OPS):
            return _call("_w_", node, node=node)
        return node

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.USub):
            return _call("_w_", node, node=node)
        return node

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        if not isinstance(node.op, (ast.Div,) + WRAPPED_OPS):
            return node
        load = _as_load(node.target)
        value = ast.copy_location(ast.BinOp(left=load, op=node.op, right=node.value), node)
        value = self.visit_BinOp(value)
        return ast.copy_location(ast.Assign(targets=[node.target], value=value), node)


def _as_load(target):
    clone = copy.deepcopy(target)
    for n in ast.walk(clone):
        if hasattr(n, "ctx"):
            n.ctx = ast.Load()
    return clone


def _global_defaults(tree):
    """Turtle globals are static and start at zero: give every name declared `global` an
    initial value of the type the program assigns to it (0, '' or [])."""
    declared = {n for node in ast.walk(tree) if isinstance(node, ast.Global) for n in node.names}
    defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in declared:
                    v = node.value
                    if isinstance(v, (ast.List, ast.ListComp)) or (
                            isinstance(v, ast.BinOp) and isinstance(v.left, ast.List)):
                        defaults.setdefault(target.id, ast.List(elts=[], ctx=ast.Load()))
                    elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                        defaults.setdefault(target.id, ast.Constant(""))
    inits = []
    for name in sorted(declared - set(TURTLE_VARS)):
        target = ast.Name(name, ast.Store(), lineno=1, col_offset=0)
        inits.append(ast.Assign(targets=[target], value=defaults.get(name, ast.Constant(0)),
                                lineno=1, col_offset=0))
    tree.body[:0] = inits
    return tree


def _builtin_arities():
    import json
    from pathlib import Path
    reg = json.loads((Path(__file__).resolve().parent / "data" / "builtins.json")
                     .read_text(encoding="utf-8"))["builtins"]
    out = {}
    for row in reg:
        name = row["names"]["python"]
        sig = row["signature"]
        if not name or name.startswith(".") or "[" in sig or sig.startswith("s1"):
            continue
        out[name] = (len(sig), row["params"])
    out["input"] = ((0, 1), "prompt")
    for lenient in ("max", "min", "str", "int", "len", "chr", "ord", "abs", "print"):
        out.pop(lenient, None)
    return out


ARITIES = _builtin_arities()


def _check_program(tree, known):
    """Compile-time checks like the original's: argument counts of native commands and
    identifiers that are never defined."""
    import builtins as _b
    defined = set(known) | set(dir(_b)) | set(TURTLE_VARS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.alias):
            defined.add((node.asname or node.name).split(".")[0])
    user_functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.id not in defined and not node.id.startswith("_"):
            raise SyntaxError(f'"{node.id}" not recognised',
                              (PROGRAM_FILE, node.lineno, node.col_offset + 1, ""))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ARITIES and node.func.id not in user_functions
                and not any(isinstance(a, ast.Starred) for a in node.args) and not node.keywords):
            expected, params = ARITIES[node.func.id]
            ok = len(node.args) in expected if isinstance(expected, tuple) \
                else len(node.args) == expected
            if not ok:
                n = expected[-1] if isinstance(expected, tuple) else expected
                where = '"," expected; ' if len(node.args) < n else '")" expected; '
                raise SyntaxError(f"{where}{node.func.id} takes {n} parameter"
                                  f"{'s' if n != 1 else ''}" + (f": {params}" if params else ""),
                                  (PROGRAM_FILE, node.lineno, node.col_offset + 1, ""))


def compile_program(src, info=None):
    """Compile Turtle Python source; raises SyntaxError with the original line numbers.
    `info`, if given, receives variables/subroutines and the translated Python code."""
    src = _rewrite_codes(src)
    # Turtle Python allows `mylist.del(i)`; `del` is a keyword in standard Python
    src = re.sub(r"\.del\s*\(", ".__delitem__(", src)
    tree = ast.parse(src, PROGRAM_FILE)
    _check_program(tree, _known_names())
    tree = _global_defaults(tree)
    if info is not None:
        info.update(_analyse(tree))
    tree = ast.fix_missing_locations(_Transformer().visit(tree))
    code = compile(tree, PROGRAM_FILE, "exec")
    if info is not None:
        info["python"] = ast.unparse(tree)
    return code


def _assigned_names(body):
    """Names stored in a list of statements, not descending into nested functions."""
    names = {}
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.setdefault(node.id, getattr(node, "lineno", 1))
        stack.extend(ast.iter_child_nodes(node))
    return names


def _type_of(value):
    if isinstance(value, ast.Constant):
        return {bool: "Boolean", int: "Integer", str: "String"}.get(type(value.value), "-")
    if isinstance(value, (ast.List, ast.ListComp)):
        return "List"
    return "-"


def _analyse(tree):
    """Variables and subroutines of a Turtle Python program, for the inspector tabs."""
    types = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    types.setdefault(t.id, _type_of(node.value))
    end = max((getattr(n, "end_lineno", 0) or 0) for n in ast.walk(tree))
    variables, subs = [], [{"index": 0, "type": "PROGRAM", "name": "", "params": "", "line": 1,
                            "end": end, "py": None}]
    for name, line in sorted(_assigned_names(tree.body).items(), key=lambda kv: kv[1]):
        if name not in TURTLE_VARS:
            variables.append({"name": name, "context": "GLOBAL", "type": types.get(name, "-"),
                              "ref": "global", "line": line, "py": name, "function": None,
                              "byref": False, "value": None})
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            params = [a.arg for a in node.args.args]
            subs.append({"index": len(subs), "type": "FUNCTION", "name": node.name,
                         "params": ", ".join(params), "line": node.lineno,
                         "end": node.end_lineno, "py": node.name})
            declared = {n for g in ast.walk(node) if isinstance(g, (ast.Global, ast.Nonlocal))
                        for n in g.names}
            for p in params:
                variables.append({"name": p, "context": node.name, "type": "-",
                                  "ref": "value param", "line": node.lineno, "py": p,
                                  "function": node.name, "byref": False, "value": None})
            for name, line in sorted(_assigned_names(node.body).items(), key=lambda kv: kv[1]):
                if name not in declared and name not in params and name not in TURTLE_VARS:
                    variables.append({"name": name, "context": node.name,
                                      "type": types.get(name, "-"), "ref": "local", "line": line,
                                      "py": name, "function": node.name, "byref": False,
                                      "value": None})
    return {"variables": variables, "subroutines": subs}


_KNOWN = None


def _known_names():
    """Names a program may use without defining them (library, colours, helpers)."""
    global _KNOWN
    if _KNOWN is None:
        from runtime import Machine
        _KNOWN = set(environment(Machine())) | {"address"}
    return _KNOWN


def turtle_str(value=""):
    """Turtle Python booleans are integers: str(True) is '1'."""
    if type(value) is bool:
        return "1" if value else "0"
    return str(value)


def environment(machine):
    env = base_environment()
    env.update(machine.library())
    env.update({name: value for name, value in COLOURS})
    env.update({"_turtle_": _TurtleView(machine), "_query_": env.pop("query"),
                "_div_": div_round, "_w_": wrap32})
    # string methods are also available as functions, e.g. lower(s)
    for name in ("lower", "upper", "capitalize", "swapcase", "title", "strip"):
        env[name] = getattr(str, name)
    env["find"] = lambda s, sub: str(s).find(sub)
    env["color"] = env["colour"]
    env["str"] = turtle_str
    env["_frame_vars_"] = lambda: sys._getframe(1).f_locals
    env["replace"] = lambda s, f, r, n=-1: str(s).replace(f, r, n)
    return env
