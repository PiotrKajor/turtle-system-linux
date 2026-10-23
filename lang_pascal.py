"""Turtle Pascal front end: a compiler from Turtle Pascal to Python code objects.

Covers the language of the original's Pascal mode: program/const/var declarations, integer,
boolean, char, string[n] and multi-dimensional array types, nested procedures and functions
(with `result`, value and `var` parameters), if/while/for/repeat, integer arithmetic with
div/mod/`/`, bitwise and/or/xor/not on integers, 1-based strings, turtle variables, input codes
(?key, \\escape) and the Pascal names of the built-in library. Generated code carries the
Pascal line numbers, so run-time errors point at the right line.
"""

import ast

import lang_common as C
from colours import COLOURS
from runtime import (INPUT_CODES, QUERY_CODES, STRING_SIZE, _TurtleView, base_environment,
                     div_round, wrap32)

NAME = "Pascal"
EXTENSIONS = (".tgp", ".pas")
KEYWORDS = {"program", "var", "const", "begin", "end", "procedure", "function", "if", "then",
            "else", "while", "do", "for", "to", "downto", "repeat", "until", "div", "mod", "and",
            "or", "not", "xor", "array", "of", "true", "false"}
TURTLE_VARS = {"turtx": 0, "turty": 1, "turtd": 2, "turta": 3, "turtt": 4, "turtc": 5}
BUILTINS = C.builtin_table("pascal")

INT, BOOL, CHAR, VOID = ("int",), ("bool",), ("char",), ("void",)


def STR(size=STRING_SIZE):
    return ("str", size)


def is_str(t):
    return t[0] in ("str", "char")


# ---------------------------------------------------------------- lexer

class Tok:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind, value, line):
        self.kind, self.value, self.line = kind, value, line

    def __repr__(self):
        return f"{self.kind}:{self.value!r}@{self.line}"


def tokenize(src):
    toks = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r\f":
            i += 1
        elif c == "{":
            j = src.find("}", i)
            if j < 0:
                raise C.CompileError("Comment not closed with }", line)
            line += src.count("\n", i, j)
            i = j + 1
        elif src.startswith("(*", i):
            j = src.find("*)", i + 2)
            if j < 0:
                raise C.CompileError("Comment not closed with *)", line)
            line += src.count("\n", i, j)
            i = j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            low = word.lower()
            toks.append(Tok("kw" if low in KEYWORDS else "id", low, line))
            i = j
        elif c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            toks.append(Tok("num", int(src[i:j]), line))
            i = j
        elif c == "$":
            j = i + 1
            while j < n and src[j] in "0123456789abcdefABCDEF":
                j += 1
            if j == i + 1:
                raise C.CompileError("Hexadecimal number expected after $", line)
            toks.append(Tok("num", int(src[i + 1:j], 16), line))
            i = j
        elif c in "'#":
            text = ""
            while i < n and src[i] in "'#":
                if src[i] == "'":
                    j = i + 1
                    while True:
                        if j >= n or src[j] == "\n":
                            raise C.CompileError("String not closed", line)
                        if src[j] == "'":
                            if j + 1 < n and src[j + 1] == "'":
                                text += "'"
                                j += 2
                                continue
                            break
                        text += src[j]
                        j += 1
                    i = j + 1
                else:
                    j = i + 1
                    while j < n and src[j].isdigit():
                        j += 1
                    if j == i + 1:
                        raise C.CompileError("Character code expected after #", line)
                    text += chr(int(src[i + 1:j]))
                    i = j
            toks.append(Tok("str", text, line))
        elif c in "?\\" and i + 1 < n and (src[i + 1].isalnum() or src[i + 1] == "_"):
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(Tok("query" if c == "?" else "code", src[i + 1:j].lower(), line))
            i = j
        else:
            two = src[i:i + 2]
            if two in (":=", "<=", ">=", "<>", ".."):
                toks.append(Tok("op", two, line))
                i += 2
            elif c in "+-*/=<>()[],;:.":
                toks.append(Tok("op", c, line))
                i += 1
            else:
                raise C.CompileError(f'Unexpected character "{c}"', line)
    toks.append(Tok("eof", None, line))
    return toks


# ---------------------------------------------------------------- symbols

class Sym:
    __slots__ = ("kind", "type", "py", "depth", "byref", "params", "value")

    def __init__(self, kind, type_=None, py=None, depth=0, byref=False, params=None, value=None):
        self.kind, self.type, self.py, self.depth = kind, type_, py, depth
        self.byref, self.params, self.value = byref, params, value


class Routine:
    """The procedure/function being compiled."""

    def __init__(self, sym, depth, result_py=None):
        self.sym = sym
        self.depth = depth
        self.result_py = result_py
        self.outer = {}          # python name -> depth, for global/nonlocal declarations
        self.sym_name = ""


def default_value(t, line):
    kind = t[0]
    if kind == "int":
        return C.const(0, line)
    if kind == "bool":
        return C.const(False, line)
    if kind == "char":
        return C.const("\x00", line)
    if kind == "str":
        return C.const("", line)
    if kind == "array":
        dims = ast.List(elts=[ast.Tuple(elts=[C.const(lo, line), C.const(hi, line)],
                                        ctx=ast.Load()) for lo, hi in t[1]], ctx=ast.Load())
        return C.call("_newarray", [C.loc(dims, line), default_value(t[2], line)], line)
    raise C.CompileError("Unsupported type", line)


# ---------------------------------------------------------------- the compiler

class Compiler:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.pos = 0
        self.scopes = [{}]
        self.routine = None
        self.temp = 0
        self.pre = []
        self.post = []
        self.param_kind = None
        self.info = {"variables": [], "subroutines": [
            {"index": 0, "type": "PROGRAM", "name": "", "params": "", "line": 1, "end": None,
             "py": None}]}
        g = self.scopes[0]
        for cname, value in COLOURS:
            g[cname] = Sym("const", INT, value=value)
        g["true"] = Sym("const", BOOL, value=True)
        g["false"] = Sym("const", BOOL, value=False)
        for tname, idx in TURTLE_VARS.items():
            g[tname] = Sym("turtle", INT, value=idx)

    # -- token helpers
    @property
    def tok(self):
        return self.toks[self.pos]

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def at(self, kind, value=None):
        t = self.tok
        return t.kind == kind and (value is None or t.value == value)

    def accept(self, kind, value=None):
        if self.at(kind, value):
            return self.next()
        return None

    def expect(self, kind, value=None, what=None):
        if self.at(kind, value):
            return self.next()
        found = self.tok.value if self.tok.kind != "eof" else "end of program"
        raise C.CompileError(f'"{what or value or kind}" expected but "{found}" found',
                             self.tok.line)

    def ident(self):
        t = self.tok
        if t.kind != "id":
            raise C.CompileError(f'Identifier expected but "{t.value}" found', t.line)
        self.pos += 1
        return t.value

    # -- symbols
    def lookup(self, ident, line):
        for scope in reversed(self.scopes):
            if ident in scope:
                return scope[ident]
        if ident in BUILTINS:
            return Sym("builtin", value=BUILTINS[ident])
        raise C.CompileError(f'"{ident}" not recognised', line)

    def declare(self, ident, sym, line):
        scope = self.scopes[-1]
        if ident in scope and scope[ident].kind not in ("const",) or ident in KEYWORDS:
            raise C.CompileError(f'"{ident}" already declared', line)
        scope[ident] = sym
        if sym.kind in ("var", "const"):
            if sym.kind == "const":
                ref = "constant"
            elif self.param_kind:
                ref = self.param_kind
            else:
                ref = "global" if sym.depth == 0 else "local"
            self.info["variables"].append({
                "name": ident, "context": self.routine.sym_name if self.routine else "GLOBAL",
                "type": C.type_text(sym.type), "ref": ref, "line": line, "py": sym.py,
                "function": self.routine.sym.py if self.routine else None,
                "byref": sym.byref, "value": sym.value if sym.kind == "const" else None})
        return sym

    def depth(self):
        return len(self.scopes) - 1

    def note_store(self, sym):
        """Record an assignment to a variable of an enclosing scope."""
        if self.routine and sym.kind == "var" and not sym.byref and sym.depth < self.routine.depth:
            self.routine.outer[sym.py] = sym.depth

    def new_temp(self):
        self.temp += 1
        return f"_t{self.temp}"

    # -- program
    def program(self):
        line = self.tok.line
        if self.accept("kw", "program"):
            self.ident()
            self.expect("op", ";")
        body = self.declarations()
        self.expect("kw", "begin")
        body += self.statements_until("end")
        self.expect("kw", "end")
        self.expect("op", ".")
        if not self.at("eof"):
            raise C.CompileError("Text after end of program", self.tok.line)
        self.info["subroutines"][0].update(name="", end=self.toks[self.pos - 1].line)
        return C.finish(body or [C.loc(ast.Pass(), line)], self.info)

    def declarations(self):
        body = []
        while True:
            if self.accept("kw", "const"):
                while self.at("id"):
                    body += self.const_decl()
            elif self.accept("kw", "var"):
                while self.at("id"):
                    body += self.var_decl()
            elif self.at("kw", "procedure") or self.at("kw", "function"):
                body.append(self.routine_decl())
            else:
                return body

    def const_decl(self):
        line = self.tok.line
        ident = self.ident()
        self.expect("op", "=")
        node, t = self.expression()
        self.expect("op", ";")
        value = const_eval(node)
        if value is not None:
            self.declare(ident, Sym("const", t, value=value), line)
            return []
        sym = self.declare(ident, Sym("var", t, f"{ident}_{self.depth()}", self.depth()), line)
        return [C.assign(C.name(sym.py, line, True), node, line)]

    def var_decl(self):
        line = self.tok.line
        names = [self.ident()]
        while self.accept("op", ","):
            names.append(self.ident())
        self.expect("op", ":")
        t = self.type_spec()
        self.expect("op", ";")
        out = []
        for ident in names:
            sym = self.declare(ident, Sym("var", t, f"{ident}_{self.depth()}", self.depth()), line)
            out.append(C.assign(C.name(sym.py, line, True), default_value(t, line), line))
        return out

    def const_int(self):
        line = self.tok.line
        node, t = self.simple_expression()
        value = const_eval(node)
        if t != INT or not isinstance(value, int):
            raise C.CompileError("Constant integer expected", line)
        return value

    def type_spec(self):
        line = self.tok.line
        if self.accept("kw", "array"):
            if self.accept("kw", "of"):
                return ("array", None, self.type_spec())       # open array parameter
            self.expect("op", "[")
            dims = []
            while True:
                lo = self.const_int()
                self.expect("op", "..")
                hi = self.const_int()
                if hi < lo:
                    raise C.CompileError("Array upper bound below lower bound", line)
                dims.append((lo, hi))
                if not self.accept("op", ","):
                    break
            self.expect("op", "]")
            self.expect("kw", "of")
            elem = self.type_spec()
            if elem[0] == "array" and elem[1] is not None:
                return ("array", dims + elem[1], elem[2])
            return ("array", dims, elem)
        ident = self.ident()
        if ident == "integer":
            return INT
        if ident == "boolean":
            return BOOL
        if ident == "char":
            return CHAR
        if ident == "string":
            if self.accept("op", "["):
                size = self.const_int()
                self.expect("op", "]")
                if not 1 <= size <= 255:
                    raise C.CompileError("String size must be between 1 and 255", line)
                return STR(size)
            return STR()
        raise C.CompileError(f'Type "{ident}" not recognised', line)

    def routine_decl(self):
        line = self.tok.line
        is_func = self.next().value == "function"
        ident = self.ident()
        depth = self.depth()
        params = []
        if self.accept("op", "("):
            while True:
                byref = bool(self.accept("kw", "var"))
                names = [self.ident()]
                while self.accept("op", ","):
                    names.append(self.ident())
                self.expect("op", ":")
                t = self.type_spec()
                params += [(p, t, byref) for p in names]
                if not self.accept("op", ";"):
                    break
            self.expect("op", ")")
        ret = VOID
        if is_func:
            self.expect("op", ":")
            ret = self.type_spec()
        self.expect("op", ";")
        sym = self.declare(ident, Sym("func" if is_func else "proc", ret, f"p_{ident}_{depth}",
                                      depth, params=params), line)
        outer_routine = self.routine
        self.scopes.append({})
        inner = depth + 1
        result_py = f"result_{inner}" if is_func else None
        self.routine = Routine(sym, inner, result_py)
        self.routine.sym_name = ident
        sub = {"index": len(self.info["subroutines"]), "type": "FUNCTION" if is_func else "PROCEDURE",
               "name": ident, "params": ", ".join(("var " if b else "") + f"{p}: {C.type_text(t)}"
                                                 for p, t, b in params),
               "returns": C.type_text(ret) if is_func else "", "line": line, "end": None,
               "py": sym.py}
        self.info["subroutines"].append(sub)
        args = []
        prologue = []
        for p, t, byref in params:
            self.param_kind = "VAR param" if byref else "value param"
            psym = self.declare(p, Sym("var", t, f"{p}_{inner}", inner, byref=byref and t[0] != "array"),
                                line)
            self.param_kind = None
            args.append(ast.arg(arg=psym.py))
            if t[0] == "array" and not byref:
                prologue.append(C.assign(C.name(psym.py, line, True),
                                         C.call("_deepcopy", [C.name(psym.py, line)], line), line))
        if is_func:
            self.param_kind = "result"
            self.declare("result", Sym("var", ret, result_py, inner), line)
            self.param_kind = None
            prologue.append(C.assign(C.name(result_py, line, True), default_value(ret, line), line))
        body = prologue + self.declarations()
        self.expect("kw", "begin")
        body += self.statements_until("end")
        self.expect("kw", "end")
        self.expect("op", ";")
        sub["end"] = self.toks[self.pos - 1].line
        if is_func:
            body.append(C.loc(ast.Return(value=C.name(result_py, line)), line))
        decls = []
        globals_ = sorted(n for n, d in self.routine.outer.items() if d == 0)
        nonlocals = sorted(n for n, d in self.routine.outer.items() if d > 0)
        if globals_:
            decls.append(C.loc(ast.Global(names=globals_), line))
        if nonlocals:
            decls.append(C.loc(ast.Nonlocal(names=nonlocals), line))
        # nested routines' outer references propagate to this routine when they reach past it
        self.scopes.pop()
        finished = self.routine
        self.routine = outer_routine
        if outer_routine:
            for n, d in finished.outer.items():
                if d < outer_routine.depth:
                    outer_routine.outer[n] = d
        fn = ast.FunctionDef(name=sym.py, args=ast.arguments(posonlyargs=[], args=args,
                                                             kwonlyargs=[], kw_defaults=[],
                                                             defaults=[]),
                             body=decls + (body or [ast.Pass()]), decorator_list=[],
                             returns=None, type_params=[])
        return C.loc(fn, line)

    # -- statements
    def statements_until(self, *enders):
        out = []
        while True:
            if self.tok.kind == "kw" and self.tok.value in enders:
                return out
            out += self.statement()
            if self.tok.kind == "kw" and self.tok.value in enders:
                return out
            if not self.accept("op", ";"):
                raise C.CompileError(f'";" expected but "{self.tok.value}" found', self.tok.line)

    def statement(self):
        t = self.tok
        line = t.line
        self.pre, self.post = [], []
        if t.kind == "kw":
            if t.value == "begin":
                self.next()
                body = self.statements_until("end")
                self.expect("kw", "end")
                return body
            if t.value == "if":
                return self.if_statement()
            if t.value == "while":
                self.next()
                cond = self.condition()
                pre, post = self.pre, self.post
                self.expect("kw", "do")
                body = self.statement_block()
                if pre:
                    stop = C.loc(ast.If(test=C.loc(ast.UnaryOp(op=ast.Not(), operand=cond), line),
                                        body=[C.loc(ast.Break(), line)], orelse=[]), line)
                    return [C.loc(ast.While(test=C.const(True, line),
                                            body=pre + [stop] + post + body, orelse=[]), line)]
                return [C.loc(ast.While(test=cond, body=body, orelse=[]), line)]
            if t.value == "repeat":
                self.next()
                body = self.statements_until("until")
                self.expect("kw", "until")
                cond = self.condition()
                stop = C.loc(ast.If(test=cond, body=[C.loc(ast.Break(), line)], orelse=[]), line)
                return [C.loc(ast.While(test=C.const(True, line), body=body + self.pre + [stop],
                                        orelse=[]), line)]
            if t.value == "for":
                return self.for_statement()
            if t.value in ("end", "until", "else"):
                return []
            raise C.CompileError(f'Statement expected but "{t.value}" found', line)
        if t.kind == "op" and t.value == ";":
            return []
        if t.kind != "id":
            raise C.CompileError(f'Statement expected but "{t.value}" found', line)
        stmt = self.simple_statement()
        return self.pre + stmt + self.post

    def statement_block(self):
        body = self.statement()
        return body or [C.loc(ast.Pass(), self.tok.line)]

    def condition(self):
        line = self.tok.line
        node, t = self.expression()
        if t != BOOL:
            raise C.CompileError("Boolean expression expected", line)
        return node

    def if_statement(self):
        line = self.next().line
        cond = self.condition()
        pre = self.pre
        self.expect("kw", "then")
        body = self.statement_block()
        orelse = []
        if self.accept("kw", "else"):
            orelse = self.statement_block()
        return pre + [C.loc(ast.If(test=cond, body=body, orelse=orelse), line)]

    def for_statement(self):
        line = self.next().line
        ident = self.ident()
        sym = self.lookup(ident, line)
        if sym.kind not in ("var", "turtle") or sym.type != INT:
            raise C.CompileError("FOR loop variable must be an integer variable", line)
        self.expect("op", ":=")
        start, ts = self.expression()
        down = bool(self.accept("kw", "downto"))
        if not down:
            self.expect("kw", "to")
        stop, te = self.expression()
        pre = self.pre
        if ts != INT or te != INT:
            raise C.CompileError("FOR loop limits must be integers", line)
        self.expect("kw", "do")
        body = self.statement_block()
        if sym.kind == "var" and not sym.byref:
            self.note_store(sym)
            rng = C.call("range", [start, C.binop(stop, ast.Sub() if down else ast.Add(),
                                                  C.const(1, line), line)]
                         + ([C.const(-1, line)] if down else []), line)
            return pre + [C.loc(ast.For(target=C.name(sym.py, line, True), iter=rng, body=body,
                                        orelse=[]), line)]
        # loop over a var parameter or turtle variable: an explicit counter
        tmp = self.new_temp()
        target = self.store_target(sym, line)
        rng = C.call("range", [start, C.binop(stop, ast.Sub() if down else ast.Add(),
                                              C.const(1, line), line)]
                     + ([C.const(-1, line)] if down else []), line)
        return pre + [C.loc(ast.For(target=C.name(tmp, line, True), iter=rng,
                                    body=[C.assign(target, C.name(tmp, line), line)] + body,
                                    orelse=[]), line)]

    def store_target(self, sym, line):
        if sym.kind == "turtle":
            return C.subscript(C.name("_turtle_", line), C.const(sym.value, line), line, True)
        if sym.byref:
            return C.subscript(C.name(sym.py, line), C.const(0, line), line, True)
        self.note_store(sym)
        return C.name(sym.py, line, True)

    def load_var(self, sym, line):
        if sym.kind == "turtle":
            return C.subscript(C.name("_turtle_", line), C.const(sym.value, line), line)
        if sym.byref:
            return C.subscript(C.name(sym.py, line), C.const(0, line), line)
        return C.name(sym.py, line)

    def coerce(self, node, src_t, dst_t, line):
        """Value conversion on assignment."""
        if dst_t[0] == "str":
            if not is_str(src_t):
                raise C.CompileError("Type error: String expected", line)
            if dst_t[1] < 255 or src_t[0] == "str":
                return C.call("_truncate", [node, C.const(dst_t[1], line)], line)
            return node
        if dst_t == CHAR:
            if not is_str(src_t):
                raise C.CompileError("Type error: Character expected", line)
            return node
        if dst_t == INT and src_t not in (INT,):
            raise C.CompileError("Type error: Integer expected", line)
        if dst_t == BOOL and src_t != BOOL:
            raise C.CompileError("Type error: Boolean expected", line)
        return node

    def simple_statement(self):
        line = self.tok.line
        ident = self.ident()
        if ident in ("inc", "dec") and self.at("op", "("):
            return self.inc_dec(ident, line)
        sym = self.lookup(ident, line)
        routine = self.routine
        if routine and routine.sym.kind == "func" and sym is routine.sym and self.at("op", ":="):
            sym = self.scopes[-1]["result"]
        if self.at("op", ":=") or self.at("op", "["):
            return self.assignment(sym, ident, line)
        if sym.kind in ("proc", "func", "builtin"):
            node, _ = self.call(sym, ident, line, statement=True)
            return [C.expr_stmt(node, line)]
        raise C.CompileError(f'"{ident}" cannot be used as a statement', line)

    def designator(self, sym, ident, line):
        """A variable reference with optional indices: (load node, store(value) -> stmt, type)."""
        if sym.kind not in ("var", "turtle"):
            raise C.CompileError(f'"{ident}" is not a variable', line)
        indices = []
        while self.accept("op", "["):
            indices.append(self.expression())
            while self.accept("op", ","):
                indices.append(self.expression())
            self.expect("op", "]")
        if not indices:
            return (self.load_var(sym, line),
                    lambda v: C.assign(self.store_target(sym, line), v, line), sym.type)
        t = sym.type
        base_load = self.load_var(sym, line)
        chain = []          # index nodes into the nested lists
        k = 0
        while k < len(indices) and t[0] == "array":
            dims = t[1]
            if dims is None:                      # open array parameter: 0-based, one dimension
                chain.append(C.call("_oindex", [indices[k][0],
                                                self.chain_load(base_load, chain, line)], line))
                k += 1
                t = t[2]
                continue
            used = 0
            for lo, hi in dims:
                if k >= len(indices):
                    break
                chain.append(C.call("_index", [indices[k][0], C.const(lo, line),
                                               C.const(hi, line)], line))
                k += 1
                used += 1
            if used < len(dims):
                t = ("array", dims[used:], t[2])
                break
            t = t[2]
        elem_load = self.chain_load(base_load, chain, line)

        def store_elem(v):
            if not chain:
                return C.assign(self.store_target(sym, line), v, line)
            target = self.load_var(sym, line)
            for idx in chain[:-1]:
                target = C.subscript(target, idx, line)
            return C.assign(C.subscript(target, chain[-1], line, True), v, line)

        if k == len(indices):
            return elem_load, store_elem, t
        if k == len(indices) - 1 and t[0] == "str":
            char_index = indices[k][0]
            return (C.call("_getchar", [elem_load, char_index], line),
                    lambda v: store_elem(C.call("_setchar", [self.chain_load(
                        self.load_var(sym, line), chain, line), char_index, v], line)), CHAR)
        raise C.CompileError(f'Too many indices for "{ident}"', line)

    def chain_load(self, base, chain, line):
        node = base
        for idx in chain:
            node = C.subscript(node, idx, line)
        return node

    def assignment(self, sym, ident, line):
        if sym.kind not in ("var", "turtle"):
            raise C.CompileError(f'Cannot assign to "{ident}"', line)
        load, store, t = self.designator(sym, ident, line)
        self.expect("op", ":=")
        value, vt = self.expression()
        return [store(self.coerce(value, vt, t, line))]

    def inc_dec(self, ident, line):
        self.expect("op", "(")
        target_line = self.tok.line
        name_ = self.ident()
        load, store, t = self.designator(self.lookup(name_, target_line), name_, target_line)
        if t != INT:
            raise C.CompileError(f"{ident} needs an integer variable", line)
        amount = C.const(1, line)
        if self.accept("op", ","):
            amount, _ = self.expression()
        self.expect("op", ")")
        op = ast.Add() if ident == "inc" else ast.Sub()
        return [store(C.call("_w_", [C.binop(load, op, amount, line)], line))]

    # -- calls
    def call(self, sym, ident, line, statement=False):
        params = sym.params if sym.kind != "builtin" else None
        args = []
        if self.accept("op", "("):
            if not self.at("op", ")"):
                while True:
                    byref = bool(params and len(args) < len(params) and params[len(args)][2]
                                 and params[len(args)][1][0] != "array")
                    args.append(self.var_argument(line) if byref else self.expression())
                    if not self.accept("op", ","):
                        break
            self.expect("op", ")")
        if sym.kind == "builtin":
            return self.builtin_call(sym.value, ident, args, line, statement)
        if len(args) != len(params):
            raise C.CompileError(f"{ident} takes {len(params)} parameter"
                                 f"{'s' if len(params) != 1 else ''}", line)
        nodes = []
        for (pname, ptype, byref), (node, t) in zip(params, args):
            if byref and ptype[0] != "array":
                nodes.append(node)
            elif ptype[0] == "array":
                nodes.append(node)
            else:
                nodes.append(self.coerce(node, t, ptype, line))
        if sym.kind == "proc" and not statement:
            raise C.CompileError(f'Procedure "{ident}" does not return a value', line)
        return C.call(sym.py, nodes, line), sym.type

    def var_argument(self, line):
        """An argument for a VAR parameter: pass a box, writing it back after the call."""
        t = self.tok
        if t.kind != "id":
            raise C.CompileError("Variable needed for VAR parameter", t.line)
        ident = self.ident()
        sym = self.lookup(ident, t.line)
        if sym.kind == "var" and sym.byref and not self.at("op", "["):
            return C.name(sym.py, line), sym.type          # pass the caller's own box on
        if sym.kind == "turtle":
            return C.call("_tref_", [C.const(sym.value, line)], line), INT
        load, store, vt = self.designator(sym, ident, t.line)
        tmp = self.new_temp()
        self.pre.append(C.assign(C.name(tmp, line, True),
                                 C.loc(ast.List(elts=[load], ctx=ast.Load()), line), line))
        self.post.append(store(C.subscript(C.name(tmp, line), C.const(0, line), line)))
        return C.name(tmp, line), vt

    def builtin_call(self, info, ident, args, line, statement):
        nargs = info["nargs"]
        if nargs is not None and len(args) != nargs and not (ident == "replace" and len(args) == 3):
            raise C.CompileError(f"{ident} takes {nargs} parameter{'s' if nargs != 1 else ''}"
                                 + (f": {info['params']}" if info["params"] else ""), line)
        if info["returns"] == "void" and not statement:
            raise C.CompileError(f'Procedure "{ident}" does not return a value', line)
        nodes = [node for node, t in args]
        if ident in ("findfirst", "finddir"):
            nodes = nodes[:1]
        rtype = {"int": INT, "bool": BOOL, "char": CHAR, "str": STR(255), "void": VOID,
                 "list": VOID}[info["returns"]]
        return C.call(f"b_{ident}", nodes, line), rtype

    # -- expressions
    def expression(self):
        line = self.tok.line
        left, lt = self.simple_expression()
        ops = {"=": ast.Eq(), "<>": ast.NotEq(), "<": ast.Lt(), ">": ast.Gt(), "<=": ast.LtE(),
               ">=": ast.GtE()}
        if self.tok.kind == "op" and self.tok.value in ops:
            op = self.next().value
            right, rt = self.simple_expression()
            if is_str(lt) != is_str(rt) or (lt == BOOL) != (rt == BOOL):
                raise C.CompileError("Type error: incompatible types in comparison", line)
            return C.compare(left, ops[op], right, line), BOOL
        return left, lt

    def simple_expression(self):
        line = self.tok.line
        if self.accept("op", "-"):
            node, t = self.term()
            if t != INT:
                raise C.CompileError("Type error: Integer expected", line)
            node = C.call("_w_", [C.loc(ast.UnaryOp(op=ast.USub(), operand=node), line)], line)
        else:
            self.accept("op", "+")
            node, t = self.term()
        while self.tok.kind in ("op", "kw") and self.tok.value in ("+", "-", "or", "xor"):
            op = self.next().value
            right, rt = self.term()
            node, t = self.arith(op, node, t, right, rt, line)
        return node, t

    def term(self):
        line = self.tok.line
        node, t = self.factor()
        while self.tok.kind in ("op", "kw") and self.tok.value in ("*", "/", "div", "mod", "and"):
            op = self.next().value
            right, rt = self.factor()
            node, t = self.arith(op, node, t, right, rt, line)
        return node, t

    def arith(self, op, left, lt, right, rt, line):
        if op in ("and", "or", "xor"):
            if lt == BOOL and rt == BOOL:
                if op == "xor":
                    return C.compare(left, ast.NotEq(), right, line), BOOL
                return C.loc(ast.BoolOp(op=ast.And() if op == "and" else ast.Or(),
                                        values=[left, right]), line), BOOL
            if lt == INT and rt == INT:
                pyop = {"and": ast.BitAnd(), "or": ast.BitOr(), "xor": ast.BitXor()}[op]
                return C.binop(left, pyop, right, line), INT
            raise C.CompileError(f'Type error: "{op}" needs two Booleans or two Integers', line)
        if op == "+" and is_str(lt) and is_str(rt):
            return C.binop(left, ast.Add(), right, line), STR(255)
        if lt != INT or rt != INT:
            raise C.CompileError(f'Type error: Integer expected for "{op}"', line)
        if op == "/":
            return C.call("_div_", [left, right], line), INT
        if op == "div":
            return C.call("_pdiv", [left, right], line), INT
        if op == "mod":
            return C.call("_pmod", [left, right], line), INT
        pyop = {"+": ast.Add(), "-": ast.Sub(), "*": ast.Mult()}[op]
        return C.call("_w_", [C.binop(left, pyop, right, line)], line), INT

    def factor(self):
        t = self.tok
        line = t.line
        if t.kind == "num":
            self.next()
            return C.const(wrap32(t.value), line), INT
        if t.kind == "str":
            self.next()
            return C.const(t.value, line), (CHAR if len(t.value) == 1 else STR(255))
        if t.kind == "query":
            self.next()
            if t.value not in QUERY_CODES:
                raise C.CompileError(f'"?{t.value}" not recognised', line)
            return C.call("_query_", [C.const(QUERY_CODES[t.value], line)], line), INT
        if t.kind == "code":
            self.next()
            if t.value not in INPUT_CODES:
                raise C.CompileError(f'"\\{t.value}" not recognised', line)
            return C.const(INPUT_CODES[t.value], line), INT
        if self.accept("op", "("):
            node, nt = self.expression()
            self.expect("op", ")")
            return node, nt
        if self.accept("kw", "not"):
            node, nt = self.factor()
            if nt == BOOL:
                return C.loc(ast.UnaryOp(op=ast.Not(), operand=node), line), BOOL
            if nt == INT:
                return C.loc(ast.UnaryOp(op=ast.Invert(), operand=node), line), INT
            raise C.CompileError('Type error: "not" needs a Boolean or Integer', line)
        if self.accept("op", "-"):
            node, nt = self.factor()
            return C.call("_w_", [C.loc(ast.UnaryOp(op=ast.USub(), operand=node), line)],
                          line), nt
        if t.kind == "kw" and t.value in ("true", "false"):
            self.next()
            return C.const(t.value == "true", line), BOOL
        if t.kind != "id":
            raise C.CompileError(f'Expression expected but "{t.value}" found', line)
        ident = self.ident()
        sym = self.lookup(ident, line)
        if sym.kind == "const":
            if self.at("op", "[") and isinstance(sym.value, str):
                self.expect("op", "[")
                index, _ = self.expression()
                self.expect("op", "]")
                return C.call("_getchar", [C.const(sym.value, line), index], line), CHAR
            return C.const(sym.value, line), sym.type
        if sym.kind in ("var", "turtle"):
            load, _, vt = self.designator(sym, ident, line)
            if self.at("op", ".") and self.toks[self.pos + 1].value == "length":
                self.pos += 2
                return C.call("len", [load], line), INT
            return load, vt
        if sym.kind in ("func", "proc", "builtin"):
            if sym.kind == "builtin" and ident == "maxint" and not self.at("op", "("):
                return C.const(2 ** 31 - 1, line), INT
            return self.call(sym, ident, line)
        raise C.CompileError(f'"{ident}" cannot be used in an expression', line)


def const_eval(node):
    """The value of an expression built only from constants, else None."""
    try:
        code = compile(ast.fix_missing_locations(ast.Expression(body=node)), "<const>", "eval")
        return eval(code, {"__builtins__": {}, "_w_": wrap32, "_div_": div_round,
                           "_pdiv": C.pascal_div, "_pmod": C.pascal_mod})
    except Exception:  # noqa: BLE001 - anything non-constant
        return None


def compile_program(src, info=None):
    compiler = Compiler(src)
    code = compiler.program()
    if info is not None:
        info.update(compiler.info)
    return code


def environment(machine):
    import copy

    lib = machine.library()
    strings = C.string_functions()
    env = base_environment()
    special = {
        "arccos": lib["acos"], "arcsin": lib["asin"], "arctan": lib["atan"], "ln": lib["log"],
        "hexstr": lib["hex"], "random": lib["randrange"], "length": len, "str": str,
        "chr": chr, "ord": ord, "copy": strings["copy"], "delete": strings["delete"],
        "insert": strings["insert"], "pos": strings["pos"], "uppercase": strings["upper"],
        "lowercase": strings["lower"], "titlecase": strings["title"],
        "swapcase": strings["swapcase"], "initcap": strings["initcap"], "trim": strings["trim"],
        "replace": strings["replace"], "val": C.to_int, "valdef": C.to_int, "boolint": lib["boolint"],
        "shl": C.shl, "shr": C.shr, "openfile": lib["fopen"], "closefile": lib["fclose"],
        "fwriteln": lib["fwriteline"], "freadln": lib["freadline"], "restartfile": lib["frestart"],
        "copyfile": lib["fcopy"], "movefile": lib["fmove"], "renamefile": lib["fmove"],
        "deletefile": lib["fremove"], "fileexists": lib["isfile"], "direxists": lib["isdir"],
        "qval": lib["qint"], "readln": lib["readline"], "maxint": lib["maxint"],
        "color": lib["colour"],
    }

    def write(*args):
        machine.check()
        text = "".join(str(a) for a in args)
        machine.bridge.write.emit(text, "console")
        machine.bridge.write.emit(text, "output")

    def writeln(*args):
        write(*args, "\n")

    special["write"] = write
    special["writeln"] = writeln
    for ident, info in BUILTINS.items():
        impl = special.get(ident)
        if impl is None and info["python"]:
            impl = lib.get(info["python"])
        if impl is None:
            impl = lib.get(ident)
        if impl is not None:
            env[f"b_{ident}"] = impl
    env.update({"_turtle_": _TurtleView(machine), "_query_": lib["query"], "_div_": div_round,
                "_w_": wrap32, "_pdiv": C.pascal_div, "_pmod": C.pascal_mod,
                "_truncate": C.truncate, "_setchar": C.set_char, "_getchar": C.get_char,
                "_tref_": lib["_tref_"], "_index": C.check_index, "_oindex": C.open_index,
                "_newarray": C.new_array, "_deepcopy": copy.deepcopy})
    return env
