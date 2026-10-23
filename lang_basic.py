"""Turtle BASIC front end: a compiler from Turtle BASIC to Python code objects.

Turtle BASIC follows BBC BASIC: variables typed by suffix (% integer, $ string), TRUE = -1 and
FALSE = 0 with comparisons giving -1/0, bitwise AND/OR/EOR/NOT, DIM arrays indexed from 0 to
the given bound, CONST, STRINGSIZE, DEF PROC ... ENDPROC and DEF FN ... =result with LOCAL,
PRIVATE and RETURN (by-reference) parameters, multi-line and single-line IF, FOR/NEXT with STEP,
WHILE/ENDWHILE and REPEAT/UNTIL. Generated code carries the BASIC line numbers.
"""

import ast

import lang_common as C
from colours import COLOURS
from runtime import (INPUT_CODES, QUERY_CODES, STRING_SIZE, StopProgram, _TurtleView,
                     base_environment, div_round, wrap32)

NAME = "BASIC"
EXTENSIONS = (".tgb", ".bas")
KEYWORDS = {"IF", "THEN", "ELSE", "ENDIF", "FOR", "TO", "STEP", "NEXT", "WHILE", "ENDWHILE",
            "REPEAT", "UNTIL", "DEF", "ENDPROC", "LOCAL", "PRIVATE", "RETURN", "DIM", "CONST",
            "END", "AND", "OR", "EOR", "NOT", "DIV", "MOD", "TRUE", "FALSE", "STRINGSIZE"}
TURTLE_VARS = {"turtx%": 0, "turty%": 1, "turtd%": 2, "turta%": 3, "turtt%": 4, "turtc%": 5}
BUILTINS = {k.upper(): v for k, v in C.builtin_table("basic").items()}
BUILTINS["KEYSTATUS"] = BUILTINS["STATUS"]

INT, STR, VOID = "int", "str", "void"


def var_type(ident):
    return STR if ident.endswith("$") else INT


def py_ident(ident):
    return ident.replace("%", "_i").replace("$", "_s").replace("#", "_h")


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
            toks.append(Tok("nl", None, line))
            line += 1
            i += 1
        elif c in " \t\r\f":
            i += 1
        elif c == ":":
            toks.append(Tok("nl", None, line))
            i += 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j < n and src[j] in "%$#":
                j += 1
            word = src[i:j]
            if word == "REM":
                k = src.find("\n", i)
                i = n if k < 0 else k
                continue
            if word.upper() in KEYWORDS and word.isupper():
                toks.append(Tok("kw", word, line))
            elif word.startswith(("PROC", "FN")) and len(word) > 2 and word not in BUILTINS:
                toks.append(Tok("proc", word, line))
            else:
                toks.append(Tok("id", word, line))
            i = j
        elif c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            toks.append(Tok("num", int(src[i:j]), line))
            i = j
        elif c == "&":
            j = i + 1
            while j < n and src[j] in "0123456789abcdefABCDEF":
                j += 1
            if j == i + 1:
                raise C.CompileError("Hexadecimal number expected after &", line)
            toks.append(Tok("num", int(src[i + 1:j], 16), line))
            i = j
        elif c == '"':
            j = i + 1
            text = ""
            while True:
                if j >= n or src[j] == "\n":
                    raise C.CompileError("String not closed", line)
                if src[j] == '"':
                    if j + 1 < n and src[j + 1] == '"':
                        text += '"'
                        j += 2
                        continue
                    break
                text += src[j]
                j += 1
            toks.append(Tok("str", text, line))
            i = j + 1
        elif c in "?\\" and i + 1 < n and (src[i + 1].isalnum() or src[i + 1] == "_"):
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(Tok("query" if c == "?" else "code", src[i + 1:j].lower(), line))
            i = j
        else:
            two = src[i:i + 2]
            if two in ("<=", ">=", "<>"):
                toks.append(Tok("op", two, line))
                i += 2
            elif c in "+-*/=<>(),;":
                toks.append(Tok("op", c, line))
                i += 1
            else:
                raise C.CompileError(f'Unexpected character "{c}"', line)
    toks.append(Tok("nl", None, line))
    toks.append(Tok("eof", None, line))
    return toks


# ---------------------------------------------------------------- the compiler

class Routine:
    def __init__(self, name, is_fn, params):
        self.name = name
        self.is_fn = is_fn
        self.params = params          # [(ident, byref, is_array)]
        self.locals = {}              # ident -> python name
        self.line = self.end = None
        self.privates = {}            # ident -> global python name
        self.globals_assigned = set()


class Compiler:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.pos = 0
        self.routine = None
        self.routines = {}
        self.arrays = {}              # global array ident -> dims
        self.consts = {name.upper(): value for name, value in COLOURS}
        self.consts.update({"TRUE": -1, "FALSE": 0})
        self.globals = {}             # ident -> python name
        self.private_inits = {}       # python name -> initial value
        self.temp = 0
        self.pre = []
        self.post = []
        self.string_size = STRING_SIZE
        self.scan_definitions()

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
        found = {"nl": "end of line", "eof": "end of program"}.get(self.tok.kind, self.tok.value)
        raise C.CompileError(f'"{what or value or kind}" expected but "{found}" found',
                             self.tok.line)

    def end_of_statement(self):
        if not (self.at("nl") or self.at("eof") or self.at("kw", "ELSE")):
            raise C.CompileError(f'End of statement expected but "{self.tok.value}" found',
                                 self.tok.line)

    def skip_newlines(self):
        while self.accept("nl"):
            pass

    def new_temp(self):
        self.temp += 1
        return f"_t{self.temp}"

    # -- pre-scan of DEF PROC / DEF FN headers, so calls may precede definitions
    def scan_definitions(self):
        toks = self.toks
        for k, t in enumerate(toks):
            if t.kind == "kw" and t.value == "DEF" and toks[k + 1].kind == "proc":
                name_ = toks[k + 1].value
                params = []
                j = k + 2
                if toks[j].kind == "op" and toks[j].value == "(":
                    j += 1
                    while not (toks[j].kind == "op" and toks[j].value == ")"):
                        byref = toks[j].kind == "kw" and toks[j].value == "RETURN"
                        if byref:
                            j += 1
                        if toks[j].kind != "id":
                            raise C.CompileError("Parameter name expected", toks[j].line)
                        ident = toks[j].value
                        j += 1
                        is_array = (toks[j].kind == "op" and toks[j].value == "("
                                    and toks[j + 1].kind == "op" and toks[j + 1].value == ")")
                        if is_array:
                            j += 2
                        params.append((ident, byref, is_array))
                        if toks[j].kind == "op" and toks[j].value == ",":
                            j += 1
                    j += 1
                if name_ in self.routines:
                    raise C.CompileError(f'"{name_}" defined twice', t.line)
                self.routines[name_] = Routine(name_, name_.startswith("FN"), params)

    # -- variables
    def py_var(self, ident, store=False):
        """Python name for a scalar or array variable in the current scope."""
        r = self.routine
        if r:
            if ident in r.locals:
                return r.locals[ident], "local"
            if ident in r.privates:
                if store:
                    r.globals_assigned.add(r.privates[ident])
                return r.privates[ident], "global"
        py = self.globals.setdefault(ident, "g_" + py_ident(ident))
        if store and r:
            r.globals_assigned.add(py)
        return py, "global"

    def is_byref(self, ident):
        r = self.routine
        return bool(r) and any(p == ident and byref and not arr for p, byref, arr in r.params)

    def load_scalar(self, ident, line):
        low = ident.lower()
        if low in TURTLE_VARS:
            return C.subscript(C.name("_turtle_", line), C.const(TURTLE_VARS[low], line), line)
        py, _ = self.py_var(ident)
        node = C.name(py, line)
        if self.is_byref(ident):
            node = C.subscript(node, C.const(0, line), line)
        return node

    def store_scalar(self, ident, value, line):
        low = ident.lower()
        if low in TURTLE_VARS:
            target = C.subscript(C.name("_turtle_", line), C.const(TURTLE_VARS[low], line),
                                 line, True)
        elif self.is_byref(ident):
            py, _ = self.py_var(ident)
            target = C.subscript(C.name(py, line), C.const(0, line), line, True)
        else:
            py, _ = self.py_var(ident, store=True)
            target = C.name(py, line, True)
        if var_type(ident) == STR:
            value = C.call("_truncate", [value, C.const(self.string_size, line)], line)
        return C.assign(target, value, line)

    def array_ref(self, ident, line):
        """(load node of the list, python name)."""
        py, _ = self.py_var(ident)
        return C.name(py, line), py

    def element(self, ident, indices, line, store=False):
        node, _ = self.array_ref(ident, line)
        for k, idx in enumerate(indices):
            idx = C.call("_aindex", [idx, node], line)
            node = C.subscript(node, idx, line, store=store and k == len(indices) - 1)
        return node

    # -- program
    def program(self):
        defs = []
        main = []
        self.skip_newlines()
        while not self.at("eof"):
            if self.at("kw", "DEF"):
                defs.append(self.definition())
            else:
                main += self.statement()
            self.skip_newlines()
        inits = [C.assign(C.name(py, 1, True),
                          C.const("" if ident.endswith("$") else 0, 1), 1)
                 for ident, py in sorted(self.globals.items())]
        inits += [C.assign(C.name(py, 1, True), C.const(value, 1), 1)
                  for py, value in sorted(self.private_inits.items())]
        self.info = {"subroutines": [], "variables": []}
        code = C.finish(inits + defs + (main or [C.loc(ast.Pass(), 1)]), self.info)
        self.collect_info()
        return code

    def collect_info(self):
        first_line = {}
        for t in self.toks:
            if t.kind == "id":
                first_line.setdefault(t.value, t.line)
        info = self.info
        info["subroutines"].append({"index": 0, "type": "PROGRAM", "name": "", "params": "",
                                    "line": 1, "end": self.toks[-1].line, "py": None})
        for k, r in enumerate(self.routines.values(), 1):
            info["subroutines"].append({
                "index": k, "type": "FUNCTION" if r.is_fn else "PROCEDURE", "name": r.name,
                "params": ", ".join(("RETURN " if b else "") + p + ("()" if a else "")
                                    for p, b, a in r.params),
                "line": r.line, "end": r.end, "py": py_ident(r.name)})
        for ident, py in sorted(self.globals.items()):
            info["variables"].append({"name": ident, "context": "GLOBAL",
                                      "type": C.type_text(var_type(ident)), "ref": "global", "line": first_line.get(ident), "py": py,
                                      "function": None, "byref": False, "value": None})
        for ident, value in sorted(self.consts.items()):
            if ident in first_line:
                info["variables"].append({"name": ident, "context": "GLOBAL",
                                          "type": C.type_text(var_type(ident)), "ref": "constant",
                                          "line": first_line[ident], "py": None,
                                          "function": None, "byref": False, "value": value})
        for r in self.routines.values():
            params = {p: (b, a) for p, b, a in r.params}
            for ident, py in r.locals.items():
                b, a = params.get(ident, (False, False))
                ref = ("RETURN param" if b else "value param") if ident in params else "local"
                info["variables"].append({"name": ident, "context": r.name, "type":
                                          C.type_text(var_type(ident)) + ("()" if a else ""),
                                          "ref": ref,
                                          "line": r.line, "py": py, "function": py_ident(r.name),
                                          "byref": b and not a, "value": None})
            for ident, py in r.privates.items():
                info["variables"].append({"name": ident, "context": r.name,
                                          "type": C.type_text(var_type(ident)), "ref": "private",
                                          "line": r.line, "py": py, "function": None,
                                          "byref": False, "value": None})

    def definition(self):
        line = self.next().line
        name_ = self.expect("proc", what="PROC or FN name").value
        r = self.routines[name_]
        # skip the parameter list (already scanned)
        if self.accept("op", "("):
            depth = 1
            while depth:
                t = self.next()
                if t.kind == "op" and t.value == "(":
                    depth += 1
                elif t.kind == "op" and t.value == ")":
                    depth -= 1
        self.routine = r
        r.line = line
        args = []
        for ident, byref, is_array in r.params:
            py = "p_" + py_ident(ident)
            r.locals[ident] = py
            args.append(ast.arg(arg=py))
        body = []
        ended = False
        while True:
            self.skip_newlines()
            if self.at("eof"):
                raise C.CompileError(f'"{name_}" has no ' + ("=result" if r.is_fn else "ENDPROC"),
                                     line)
            if r.is_fn and self.at("op", "="):
                ln = self.next().line
                value, _ = self.expression()
                body.append(C.loc(ast.Return(value=value), ln))
                ended = True
                break
            if not r.is_fn and self.at("kw", "ENDPROC"):
                self.next()
                ended = True
                break
            body += self.statement()
        assert ended
        r.end = self.toks[self.pos - 1].line
        decls = []
        if r.globals_assigned:
            decls.append(C.loc(ast.Global(names=sorted(r.globals_assigned)), line))
        self.routine = None
        fn = ast.FunctionDef(name=py_ident(name_), args=ast.arguments(posonlyargs=[], args=args,
                                                            kwonlyargs=[], kw_defaults=[],
                                                            defaults=[]),
                             body=decls + (body or [ast.Pass()]), decorator_list=[],
                             returns=None, type_params=[])
        return C.loc(fn, line)

    # -- statements
    def block(self, *enders):
        """Statements up to one of the ender keywords (not consumed)."""
        out = []
        while True:
            self.skip_newlines()
            if self.at("eof"):
                raise C.CompileError(f'"{enders[0]}" expected but end of program found',
                                     self.tok.line)
            if self.tok.kind == "kw" and self.tok.value in enders:
                return out
            out += self.statement()

    def statement(self):
        self.pre, self.post = [], []
        t = self.tok
        line = t.line
        if t.kind == "kw":
            v = t.value
            if v == "IF":
                return self.if_statement()
            if v == "FOR":
                return self.for_statement()
            if v == "WHILE":
                self.next()
                cond = self.condition()
                pre = self.pre
                body = self.block("ENDWHILE")
                self.next()
                if pre:
                    stop = C.loc(ast.If(test=C.loc(ast.UnaryOp(op=ast.Not(), operand=cond), line),
                                        body=[C.loc(ast.Break(), line)], orelse=[]), line)
                    return [C.loc(ast.While(test=C.const(True, line), body=pre + [stop] + body,
                                            orelse=[]), line)]
                return [C.loc(ast.While(test=cond, body=body or [C.loc(ast.Pass(), line)],
                                        orelse=[]), line)]
            if v == "REPEAT":
                self.next()
                body = self.block("UNTIL")
                self.next()
                cond = self.condition()
                stop = C.loc(ast.If(test=cond, body=[C.loc(ast.Break(), line)], orelse=[]), line)
                return [C.loc(ast.While(test=C.const(True, line), body=body + self.pre + [stop],
                                        orelse=[]), line)]
            if v == "END":
                self.next()
                return [C.loc(ast.Raise(exc=C.name("_StopProgram", line), cause=None), line)]
            if v in ("LOCAL", "PRIVATE"):
                return self.local_statement(v == "PRIVATE")
            if v == "DIM":
                return self.dim_statement()
            if v == "CONST":
                return self.const_statement()
            if v == "STRINGSIZE":
                self.next()
                size = self.expect("num", what="number").value
                if not 1 <= size <= 255:
                    raise C.CompileError("The STRINGSIZE value must be between 1 and 255", line)
                self.string_size = size
                return []
            raise C.CompileError(f'"{v}" not expected here', line)
        if t.kind == "proc":
            return self.pre + [C.expr_stmt(self.call_routine(), line)] + self.post
        if t.kind == "op" and t.value == "=" and self.routine and self.routine.is_fn:
            self.next()
            value, _ = self.expression()
            return self.pre + [C.loc(ast.Return(value=value), line)]
        if t.kind == "id":
            stmt = self.id_statement()
            return self.pre + stmt + self.post
        raise C.CompileError(f'Statement expected but "{t.value}" found', line)

    def condition(self):
        node, _ = self.expression()
        return C.compare(node, ast.NotEq(), C.const(0, self.tok.line), self.tok.line)

    def if_statement(self):
        line = self.next().line
        cond = self.condition()
        pre = self.pre
        self.expect("kw", "THEN")
        if self.at("nl"):
            body = self.block("ELSE", "ENDIF")
            orelse = []
            if self.accept("kw", "ELSE"):
                orelse = self.block("ENDIF")
            self.expect("kw", "ENDIF")
            return pre + [C.loc(ast.If(test=cond, body=body or [C.loc(ast.Pass(), line)],
                                       orelse=orelse), line)]
        body = self.statement()
        orelse = []
        if self.accept("kw", "ELSE"):
            orelse = self.statement()
        return pre + [C.loc(ast.If(test=cond, body=body or [C.loc(ast.Pass(), line)],
                                   orelse=orelse), line)]

    def for_statement(self):
        line = self.next().line
        ident = self.expect("id", what="variable").value
        if var_type(ident) != INT:
            raise C.CompileError("FOR loop variable must be an integer variable", line)
        self.expect("op", "=")
        start, _ = self.expression()
        self.expect("kw", "TO")
        stop, _ = self.expression()
        step = C.const(1, line)
        if self.accept("kw", "STEP"):
            step, _ = self.expression()
        pre = self.pre
        body = self.block("NEXT")
        self.next()
        if self.at("id"):
            self.next()
        tmp = self.new_temp()
        stop_tmp, step_tmp = tmp + "s", tmp + "d"
        # limits are evaluated once; the variable steps until it passes the limit
        setup = [C.assign(C.name(stop_tmp, line, True), stop, line),
                 C.assign(C.name(step_tmp, line, True), step, line),
                 self.store_scalar(ident, start, line)]
        cont = C.loc(ast.IfExp(test=C.compare(C.name(step_tmp, line), ast.GtE(), C.const(0, line),
                                              line),
                               body=C.compare(self.load_scalar(ident, line), ast.LtE(),
                                              C.name(stop_tmp, line), line),
                               orelse=C.compare(self.load_scalar(ident, line), ast.GtE(),
                                                C.name(stop_tmp, line), line)), line)
        incr = self.store_scalar(ident, C.call("_w_", [C.binop(self.load_scalar(ident, line),
                                                                 ast.Add(),
                                                                 C.name(step_tmp, line), line)],
                                               line), line)
        loop = C.loc(ast.While(test=cont, body=(body or []) + [incr], orelse=[]), line)
        return pre + setup + [loop]

    def local_statement(self, private):
        line = self.next().line
        r = self.routine
        if r is None:
            raise C.CompileError(("PRIVATE" if private else "LOCAL")
                                 + " is only allowed in a procedure or function", line)
        out = []
        while True:
            ident = self.expect("id", what="variable").value
            is_array = False
            if self.accept("op", "("):
                self.expect("op", ")")
                is_array = True
            if private:
                py = f"v_{py_ident(r.name)}_{py_ident(ident)}"
                r.privates[ident] = py
                self.private_inits[py] = "" if ident.endswith("$") else 0
            else:
                py = "l_" + py_ident(ident)
                r.locals[ident] = py
                if not is_array:
                    out.append(C.assign(C.name(py, line, True),
                                        C.const("" if ident.endswith("$") else 0, line), line))
            if not self.accept("op", ","):
                break
        return out

    def dim_statement(self):
        line = self.next().line
        out = []
        while True:
            ident = self.expect("id", what="array name").value
            self.expect("op", "(")
            dims = [self.expression()[0]]
            while self.accept("op", ","):
                dims.append(self.expression()[0])
            self.expect("op", ")")
            default = C.const("" if ident.endswith("$") else 0, line)
            py, scope = self.py_var(ident, store=True)
            out.append(C.assign(C.name(py, line, True),
                                C.call("_dim", [C.loc(ast.List(elts=dims, ctx=ast.Load()), line),
                                                default], line), line))
            if not self.accept("op", ","):
                break
        return out

    def const_statement(self):
        line = self.next().line
        out = []
        while True:
            ident = self.expect("id", what="constant name").value
            self.expect("op", "=")
            value, _ = self.expression()
            folded = const_eval(value)
            if folded is not None:
                self.consts[ident] = folded
            else:
                out.append(self.store_scalar(ident, value, line))
            if not self.accept("op", ","):
                break
        return out

    def id_statement(self):
        t = self.tok
        line = t.line
        ident = t.value
        if ident in ("INC", "DEC") and self.toks[self.pos + 1].value == "(":
            return self.inc_dec()
        if ident.upper() in BUILTINS and ident.isupper() and not self.toks[self.pos + 1].value == "=":
            node, _ = self.builtin_call(statement=True)
            self.end_of_statement()
            return [C.expr_stmt(node, line)]
        self.next()
        if self.accept("op", "("):
            indices = [self.expression()[0]]
            while self.accept("op", ","):
                indices.append(self.expression()[0])
            self.expect("op", ")")
            self.expect("op", "=")
            value, _ = self.expression()
            if var_type(ident) == STR:
                value = C.call("_truncate", [value, C.const(self.string_size, line)], line)
            self.end_of_statement()
            return [C.assign(self.element(ident, indices, line, store=True), value, line)]
        self.expect("op", "=")
        value, _ = self.expression()
        self.end_of_statement()
        return [self.store_scalar(ident, value, line)]

    def inc_dec(self):
        t = self.next()
        line = t.line
        self.expect("op", "(")
        ident = self.expect("id", what="variable").value
        indices = None
        if self.accept("op", "("):
            indices = [self.expression()[0]]
            while self.accept("op", ","):
                indices.append(self.expression()[0])
            self.expect("op", ")")
        self.expect("op", ")")
        op = ast.Add() if t.value == "INC" else ast.Sub()
        if indices is None:
            value = C.call("_w_", [C.binop(self.load_scalar(ident, line), op, C.const(1, line),
                                           line)], line)
            return [self.store_scalar(ident, value, line)]
        load = self.element(ident, indices, line)
        value = C.call("_w_", [C.binop(load, op, C.const(1, line), line)], line)
        return [C.assign(self.element(ident, indices, line, store=True), value, line)]

    # -- calls
    def call_routine(self):
        t = self.next()
        line = t.line
        r = self.routines.get(t.value)
        if r is None:
            raise C.CompileError(f'"{t.value}" not defined', line)
        args = []
        if self.accept("op", "("):
            if not self.at("op", ")"):
                while True:
                    k = len(args)
                    param = r.params[k] if k < len(r.params) else None
                    if param and param[2]:
                        ident = self.expect("id", what="array").value
                        node = self.array_ref(ident, line)[0]
                        if self.accept("op", "("):          # a%(), a% or a row such as b%(i)
                            if not self.at("op", ")"):
                                indices = [self.expression()[0]]
                                while self.accept("op", ","):
                                    indices.append(self.expression()[0])
                                node = self.element(ident, indices, line)
                            self.expect("op", ")")
                        args.append(node)
                    elif param and param[1]:
                        args.append(self.ref_argument(line))
                    else:
                        args.append(self.expression()[0])
                    if not self.accept("op", ","):
                        break
            self.expect("op", ")")
        if len(args) != len(r.params):
            raise C.CompileError(f"{t.value} takes {len(r.params)} parameter"
                                 f"{'s' if len(r.params) != 1 else ''}", line)
        return C.call(py_ident(t.value), args, line)

    def ref_argument(self, line):
        ident = self.expect("id", what="variable").value
        low = ident.lower()
        if low in TURTLE_VARS:
            return C.call("_tref_", [C.const(TURTLE_VARS[low], line)], line)
        if self.is_byref(ident) and not self.at("op", "("):
            return C.name(self.py_var(ident)[0], line)
        tmp = self.new_temp()
        if self.accept("op", "("):
            indices = [self.expression()[0]]
            while self.accept("op", ","):
                indices.append(self.expression()[0])
            self.expect("op", ")")
            load = self.element(ident, indices, line)
            self.pre.append(C.assign(C.name(tmp, line, True),
                                     C.loc(ast.List(elts=[load], ctx=ast.Load()), line), line))
            self.post.append(C.assign(self.element(ident, indices, line, store=True),
                                      C.subscript(C.name(tmp, line), C.const(0, line), line),
                                      line))
            return C.name(tmp, line)
        self.pre.append(C.assign(C.name(tmp, line, True),
                                 C.loc(ast.List(elts=[self.load_scalar(ident, line)],
                                                ctx=ast.Load()), line), line))
        self.post.append(self.store_scalar(ident, C.subscript(C.name(tmp, line),
                                                              C.const(0, line), line), line))
        return C.name(tmp, line)

    def builtin_call(self, statement=False):
        t = self.next()
        line = t.line
        key = t.value.upper()
        info = BUILTINS[key]
        args = []
        if self.accept("op", "("):
            if not self.at("op", ")"):
                while True:
                    args.append(self.expression()[0])
                    if not self.accept("op", ","):
                        break
            self.expect("op", ")")
        nargs = info["nargs"]
        if nargs is not None and len(args) != nargs and not (key == "REPLACE$" and len(args) == 3):
            raise C.CompileError(f"{key} takes {nargs} parameter{'s' if nargs != 1 else ''}"
                                 + (f": {info['params']}" if info["params"] else ""), line)
        if info["returns"] == "void" and not statement:
            raise C.CompileError(f'"{key}" does not return a value', line)
        if key in ("FINDFIRST$", "FINDDIR$"):
            args = args[:1]
        rtype = STR if info["returns"] in ("str", "char") else (VOID if info["returns"] == "void"
                                                                 else INT)
        node = C.call("b_" + py_ident(key), args, line)
        if info["returns"] == "bool":
            node = C.call("_bool", [node], line)       # BASIC truth values are -1 / 0
        return node, rtype

    # -- expressions
    def expression(self):
        line = self.tok.line
        node, t = self.and_or()
        return node, t

    def and_or(self):
        line = self.tok.line
        node, t = self.comparison()
        while self.tok.kind == "kw" and self.tok.value in ("AND", "OR", "EOR"):
            op = {"AND": ast.BitAnd(), "OR": ast.BitOr(), "EOR": ast.BitXor()}[self.next().value]
            right, _ = self.comparison()
            node = C.binop(node, op, right, line)
            t = INT
        return node, t

    def comparison(self):
        line = self.tok.line
        node, t = self.additive()
        ops = {"=": ast.Eq(), "<>": ast.NotEq(), "<": ast.Lt(), ">": ast.Gt(), "<=": ast.LtE(),
               ">=": ast.GtE()}
        while self.tok.kind == "op" and self.tok.value in ops:
            op = ops[self.next().value]
            right, rt = self.additive()
            if (t == STR) != (rt == STR):
                raise C.CompileError("Type error: incompatible types in comparison", line)
            node = C.call("_bool", [C.compare(node, op, right, line)], line)
            t = INT
        return node, t

    def additive(self):
        line = self.tok.line
        node, t = self.term()
        while self.tok.kind == "op" and self.tok.value in ("+", "-"):
            op = self.next().value
            right, rt = self.term()
            if op == "+" and t == STR and rt == STR:
                node = C.binop(node, ast.Add(), right, line)
                continue
            if t != INT or rt != INT:
                raise C.CompileError(f'Type error: Integer expected for "{op}"', line)
            node = C.call("_w_", [C.binop(node, ast.Add() if op == "+" else ast.Sub(), right,
                                          line)], line)
        return node, t

    def term(self):
        line = self.tok.line
        node, t = self.unary()
        while (self.tok.kind == "op" and self.tok.value in ("*", "/")) or (
                self.tok.kind == "kw" and self.tok.value in ("DIV", "MOD")):
            op = self.next().value
            right, rt = self.unary()
            if t != INT or rt != INT:
                raise C.CompileError(f'Type error: Integer expected for "{op}"', line)
            if op == "*":
                node = C.call("_w_", [C.binop(node, ast.Mult(), right, line)], line)
            else:
                node = C.call({"/": "_div_", "DIV": "_pdiv", "MOD": "_pmod"}[op], [node, right],
                              line)
        return node, t

    def unary(self):
        line = self.tok.line
        if self.accept("op", "-"):
            node, t = self.unary()
            return C.call("_w_", [C.loc(ast.UnaryOp(op=ast.USub(), operand=node), line)], line), t
        if self.accept("op", "+"):
            return self.unary()
        if self.accept("kw", "NOT"):
            node, t = self.unary()
            return C.loc(ast.UnaryOp(op=ast.Invert(), operand=node), line), INT
        return self.factor()

    def factor(self):
        t = self.tok
        line = t.line
        if t.kind == "num":
            self.next()
            return C.const(wrap32(t.value), line), INT
        if t.kind == "str":
            self.next()
            return C.const(t.value, line), STR
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
        if t.kind == "kw" and t.value in ("TRUE", "FALSE"):
            self.next()
            return C.const(-1 if t.value == "TRUE" else 0, line), INT
        if t.kind == "proc":
            r = self.routines.get(t.value)
            if r is None:
                raise C.CompileError(f'"{t.value}" not defined', line)
            if not r.is_fn:
                raise C.CompileError(f'"{t.value}" does not return a value', line)
            return self.call_routine(), var_type(t.value)
        if t.kind != "id":
            raise C.CompileError(f'Expression expected but "{t.value}" found', line)
        ident = t.value
        if ident in self.consts:
            self.next()
            return C.const(self.consts[ident], line), var_type(ident)
        if ident.upper() in BUILTINS and ident.isupper():
            if ident == "MAXINT" and not self.toks[self.pos + 1].value == "(":
                self.next()
                return C.const(2 ** 31 - 1, line), INT
            return self.builtin_call()
        self.next()
        if self.accept("op", "("):
            indices = [self.expression()[0]]
            while self.accept("op", ","):
                indices.append(self.expression()[0])
            self.expect("op", ")")
            return self.element(ident, indices, line), var_type(ident)
        return self.load_scalar(ident, line), var_type(ident)


def const_eval(node):
    try:
        code = compile(ast.fix_missing_locations(ast.Expression(body=node)), "<const>", "eval")
        return eval(code, {"__builtins__": {}, "_w_": wrap32, "_div_": div_round,
                           "_pdiv": C.pascal_div, "_pmod": C.pascal_mod,
                           "_bool": lambda b: -1 if b else 0})
    except Exception:  # noqa: BLE001 - anything non-constant
        return None


def compile_program(src, info=None):
    compiler = Compiler(src)
    code = compiler.program()
    if info is not None:
        info.update(compiler.info)
    return code


def _dim(bounds, default):
    if not bounds:
        return default
    return [_dim(bounds[1:], default) for _ in range(int(bounds[0]) + 1)]


def _aindex(i, lst):
    if not 0 <= i < len(lst):
        raise C.TurtleError(f"Array index {i} out of range 0..{len(lst) - 1}")
    return i


def environment(machine):
    lib = machine.library()
    strings = C.string_functions()
    env = base_environment()

    def output(*args, end="\n"):
        machine.check()
        text = "".join(str(a) for a in args) + end
        machine.bridge.write.emit(text, "console")
        machine.bridge.write.emit(text, "output")

    def mode_open(mode):
        return lambda filename: lib["fopen"](filename, mode)

    special = {
        "PRINT": output, "PRINTON": lambda *a: output(*a, end=""),
        "ABS": abs, "ACS": lib["acos"], "ASN": lib["asin"], "ATN": lib["atan"], "LN": lib["log"],
        "SGN": lib["sign"], "SQR": lib["sqrt"], "RND": lambda n: 1 + machine.rng.random(n) if n >= 1 else 0, "RNDSEED": lib["randseed"], "RNDCOL": lib["randcol"],
        "CHR$": chr, "ASC": lambda s: ord(s[0]) if s else 0, "STR$": str,
        "LEN": len, "LEFT$": lambda s, n: s[:max(n, 0)], "RIGHT$": lambda s, n: s[len(s) - n:]
        if n > 0 else "", "MID$": strings["copy"], "INSTR": lambda s, sub: s.find(sub) + 1,
        "INSERT$": lambda s, i, sub: strings["insert"](sub, s, i), "DEL$": strings["delete"],
        "REPLACE$": strings["replace"], "LCASE$": strings["lower"], "UCASE$": strings["upper"],
        "TCASE$": strings["title"], "TRIM$": strings["trim"], "VAL": C.to_int,
        "VALDEF": C.to_int, "HEX$": lib["hex"], "PAD$": lib["pad"], "QSTR$": lib["qstr"],
        "QVAL": lib["qint"], "BOOLINT": lib["boolint"],
        "OPENIN": mode_open(1), "OPENOUT": mode_open(3), "OPENUP": mode_open(2),
        "CLOSE#": lib["fclose"], "EOF#": lib["eof"], "EOLN#": lib["eoln"],
        "FREAD#": lib["fread"], "FREADLN#": lib["freadline"], "PRINT#": lib["fwrite"],
        "PRINTLN#": lib["fwriteline"], "GET$": lib["read"], "GETLINE$": lib["readline"],
        "INPUT$": lib["input"], "COPYFILE": lib["fcopy"], "MOVEFILE": lib["fmove"],
        "RENAMEFILE": lib["fmove"], "DELETEFILE": lib["fremove"], "FILEEXISTS": lib["isfile"],
        "DIREXISTS": lib["isdir"], "RESTARTFILE": lib["frestart"], "MAXINT": lib["maxint"],
        "COLOR": lib["colour"], "KEYSTATUS": lib["status"],
    }
    for key, info in BUILTINS.items():
        impl = special.get(key)
        if impl is None and info["python"]:
            impl = lib.get(info["python"])
        if impl is None:
            impl = lib.get(key.lower())
        if impl is not None:
            env["b_" + key.replace("$", "_s").replace("#", "_h")] = impl
    env.update({"_turtle_": _TurtleView(machine), "_query_": lib["query"], "_div_": div_round,
                "_w_": wrap32, "_pdiv": C.pascal_div, "_pmod": C.pascal_mod,
                "_truncate": C.truncate, "_bool": lambda b: -1 if b else 0, "_dim": _dim,
                "_aindex": _aindex, "_tref_": lib["_tref_"], "_StopProgram": StopProgram})
    return env
