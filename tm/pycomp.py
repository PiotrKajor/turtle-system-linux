"""Turtle Python to Turtle Machine PCode, following the original compiler's code patterns.

The program is parsed into a small syntax tree, its variables and subroutines are allocated in
the original's memory layout, and PCode is generated line by line.  The layout of memory:

    0 ^turtle   1 ^keybuffer   2..11 ^file 1..10   12 ^memtop   13 ^hpperm   14 ^hptemp
    15.. ^subr 1..n (frame pointer of each subroutine), then ^fnbase (if there are any)
    TURTLE (=6), turtx turty turtd turta turtt turtc, then the global variables.
"""

import json
from pathlib import Path

from colours import COLOURS
from tm.pcode import CODE, Label, Program

CODE_HCLR = CODE["HCLR"]
from tm.pylex import LexError, tokenize

STRING_SIZE = 64
LIST_SIZE = 1000
TURTLE_VARS = ("turtx", "turty", "turtd", "turta", "turtt", "turtc")

_BUILTINS = json.loads((Path(__file__).resolve().parent.parent / "data" / "builtins.json")
                       .read_text())["builtins"]
BUILTINS = {b["names"]["python"]: b for b in _BUILTINS if b["names"]["python"]}
COLOUR_VALUES = {name: value for name, value in COLOURS}


class CompileError(Exception):
    def __init__(self, msg, line):
        super().__init__(msg)
        self.line = line


# ======================================================================== syntax tree

class Node:
    def __init__(self, kind, line, **kw):
        self.kind = kind
        self.line = line
        self.__dict__.update(kw)

    def __repr__(self):
        return f"<{self.kind} {self.__dict__}>"


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    @property
    def tok(self):
        return self.toks[self.i]

    def peek(self, k=1):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind, text=None):
        t = self.tok
        return t.kind == kind and (text is None or t.text == text)

    def at_op(self, text):
        return self.tok.kind == "op" and self.tok.text == text

    def at_kw(self, word):
        return self.tok.kind == "name" and self.tok.text == word

    def expect_op(self, text):
        if not self.at_op(text):
            raise CompileError(f'"{text}" expected', self.tok.line)
        return self.next()

    def expect_name(self):
        if self.tok.kind != "name":
            raise CompileError("Identifier expected", self.tok.line)
        return self.next()

    # ------------------------------------------------ statements
    def program(self):
        body = []
        while not self.at("eof"):
            if self.at("newline"):
                self.next()
                continue
            body.append(self.statement())
        return body

    def block(self):
        self.expect_op(":")
        if not self.at("newline"):          # one-line body: if x: y
            return self.simple_line()
        self.next()
        if not self.at("indent"):
            raise CompileError("Indented block expected", self.tok.line)
        self.next()
        body = []
        while not self.at("dedent") and not self.at("eof"):
            if self.at("newline"):
                self.next()
                continue
            body.append(self.statement())
        if self.at("dedent"):
            self.next()
        return body

    def statement(self):
        t = self.tok
        if t.kind == "name":
            w = t.text
            if w == "def":
                return self.funcdef()
            if w == "if":
                return self.if_stmt()
            if w == "while":
                self.next()
                cond = self.expr()
                body = self.block()
                return Node("while", t.line, cond=cond, body=body)
            if w == "for":
                self.next()
                var = self.expect_name()
                if not self.at_kw("in"):
                    raise CompileError('"in" expected', self.tok.line)
                self.next()
                seq = self.expr()
                body = self.block()
                return Node("for", t.line, var=var.text, seq=seq, body=body)
        stmts = self.simple_line()
        return stmts[0] if len(stmts) == 1 else Node("multi", t.line, body=stmts)

    def simple_line(self):
        stmts = [self.simple_stmt()]
        while self.at_op(";"):
            self.next()
            if self.at("newline"):
                break
            stmts.append(self.simple_stmt())
        if self.at("newline"):
            self.next()
        elif not self.at("eof") and not self.at("dedent"):
            raise CompileError("End of line expected", self.tok.line)
        return stmts

    def simple_stmt(self):
        t = self.tok
        if t.kind == "name":
            w = t.text
            if w == "pass":
                self.next()
                return Node("pass", t.line)
            if w == "break":
                self.next()
                return Node("break", t.line)
            if w == "continue":
                self.next()
                return Node("continue", t.line)
            if w == "return":
                self.next()
                value = None if self.at("newline") or self.at_op(";") else self.expr()
                return Node("return", t.line, value=value)
            if w == "global":
                self.next()
                names = [self.expect_name().text]
                while self.at_op(","):
                    self.next()
                    names.append(self.expect_name().text)
                return Node("global", t.line, names=names)
        target = self.expr()
        hint = None
        if self.at_op(":"):
            self.next()
            hint = self.type_hint()
            if not self.at_op("="):
                return Node("declare", t.line, target=target, hint=hint)
        if self.at_op("="):
            self.next()
            value = self.expr()
            return Node("assign", t.line, target=target, value=value, hint=hint)
        if self.tok.kind == "op" and self.tok.text in ("+=", "-=", "*=", "/=", "//=", "%="):
            op = self.next().text[:-1]
            value = self.expr()
            return Node("augassign", t.line, target=target, op=op, value=value)
        return Node("expr", t.line, value=target)

    def type_hint(self):
        name = self.expect_name().text
        if self.at_op("["):
            self.next()
            inner = self.type_hint()
            self.expect_op("]")
            return ("list", inner)
        return name

    def funcdef(self):
        t = self.next()
        name = self.expect_name().text
        self.expect_op("(")
        params = []
        while not self.at_op(")"):
            p = self.expect_name().text
            hint = None
            if self.at_op(":"):
                self.next()
                hint = self.type_hint()
            params.append((p, hint))
            if not self.at_op(","):
                break
            self.next()
        self.expect_op(")")
        rtype = None
        if self.at_op("->"):
            self.next()
            rtype = self.type_hint()
        body = self.block()
        return Node("def", t.line, name=name, params=params, rtype=rtype, body=body)

    def if_stmt(self):
        t = self.next()
        cond = self.expr()
        body = self.block()
        orelse = []
        if self.at_kw("elif"):
            orelse = [self.if_stmt()]
            orelse[0].is_elif = True
        elif self.at_kw("else"):
            self.next()
            orelse = self.block()
        return Node("if", t.line, cond=cond, body=body, orelse=orelse)

    # ------------------------------------------------ expressions
    # Turtle Python uses Pascal-style precedence: "and" binds like *, "or" like +,
    # and a comparison takes whole sums (with or) on both sides.
    def expr(self):
        left = self.simple()
        ops = ("==", "!=", "<>", "<", ">", "<=", ">=")
        while self.tok.kind == "op" and self.tok.text in ops or self.at_kw("in") \
                or (self.at_kw("not") and self.peek().text == "in"):
            t = self.next()
            op = t.text
            if op == "not":
                self.next()
                op = "not in"
            left = Node("compare", t.line, op=op, left=left, right=self.simple())
        return left

    def simple(self):
        left = self.term()
        while (self.tok.kind == "op" and self.tok.text in ("+", "-", "|", "^")) \
                or self.at_kw("or"):
            t = self.next()
            if t.text == "or":
                left = Node("bool", t.line, op="or", left=left, right=self.term())
            else:
                left = Node("binop", t.line, op=t.text, left=left, right=self.term())
        return left

    def term(self):
        left = self.unary()
        while (self.tok.kind == "op" and self.tok.text in ("*", "/", "//", "%", "&", "<<", ">>")) \
                or self.at_kw("and"):
            t = self.next()
            if t.text == "and":
                left = Node("bool", t.line, op="and", left=left, right=self.unary())
            else:
                left = Node("binop", t.line, op=t.text, left=left, right=self.unary())
        return left

    def unary(self):
        if self.tok.kind == "op" and self.tok.text in ("-", "+", "~"):
            t = self.next()
            return Node("unary", t.line, op=t.text, operand=self.unary())
        if self.at_kw("not"):
            t = self.next()
            return Node("not", t.line, operand=self.unary())
        return self.power()

    def power(self):
        base = self.postfix()
        if self.at_op("**"):
            t = self.next()
            return Node("binop", t.line, op="**", left=base, right=self.unary())
        return base

    def postfix(self):
        node = self.atom()
        while True:
            if self.at_op("("):
                t = self.next()
                args, kwargs = self.arguments()
                node = Node("call", t.line, func=node, args=args, kwargs=kwargs)
            elif self.at_op("["):
                t = self.next()
                lo = hi = None
                is_slice = False
                if not self.at_op(":"):
                    lo = self.expr()
                if self.at_op(":"):
                    is_slice = True
                    self.next()
                    if not self.at_op("]"):
                        hi = self.expr()
                self.expect_op("]")
                if is_slice:
                    node = Node("slice", t.line, value=node, lo=lo, hi=hi)
                else:
                    node = Node("index", t.line, value=node, index=lo)
            elif self.at_op("."):
                t = self.next()
                name = self.expect_name().text
                node = Node("attr", t.line, value=node, name=name)
            else:
                return node

    def arguments(self):
        args, kwargs = [], {}
        while not self.at_op(")"):
            if self.tok.kind == "name" and self.peek().kind == "op" and self.peek().text == "=":
                key = self.next().text
                self.next()
                kwargs[key] = self.expr()
            else:
                args.append(self.expr())
            if not self.at_op(","):
                break
            self.next()
        self.expect_op(")")
        return args, kwargs

    def atom(self):
        t = self.tok
        if t.kind == "num":
            self.next()
            return Node("num", t.line, value=t.value)
        if t.kind == "str":
            self.next()
            return Node("str", t.line, value=t.value)
        if t.kind == "query":
            self.next()
            return Node("query", t.line, value=t.value)
        if t.kind == "code":
            self.next()
            return Node("num", t.line, value=t.value)
        if t.kind == "name":
            self.next()
            if t.text == "True":
                return Node("bool_const", t.line, value=True)
            if t.text == "False":
                return Node("bool_const", t.line, value=False)
            return Node("name", t.line, id=t.text)
        if self.at_op("("):
            self.next()
            e = self.expr()
            self.expect_op(")")
            return e
        if self.at_op("["):
            self.next()
            items = []
            while not self.at_op("]"):
                items.append(self.expr())
                if not self.at_op(","):
                    break
                self.next()
            self.expect_op("]")
            return Node("list", t.line, items=items)
        raise CompileError(f'"{t.text}" not expected here', t.line)


# ======================================================================== symbols

class Var:
    def __init__(self, name, vtype, scope, addr, sub=None):
        self.name = name
        self.type = vtype         # "int" "bool" "str" ("list", elem)
        self.scope = scope        # "global" "local" "param" "result" "turtle"
        self.addr = addr          # global: absolute index; local: offset in the frame
        self.sub = sub
        self.line = None


class Sub:
    def __init__(self, name, index, node):
        self.name = name
        self.index = index
        self.node = node
        self.params = []
        self.locals = {}
        self.globals = set()
        self.rtype = None
        self.is_function = False
        self.size = 0
        self.entry = Label()
        self.exit = Label()
        self.parent = None


RADIX = (2048, 1024, 64, 32)


def _dims(t):
    """Capacities of each dimension of a list type."""
    depth = 0
    while isinstance(t, tuple):
        depth += 1
        t = t[1]
    if t == "str":                     # a list of strings: 500 strings of 60 characters
        return [500, 60] if depth == 1 else [50, 50, 40]
    if depth == 1:
        return [LIST_SIZE]
    return [100] * depth


def liad_code(t):
    if t == "str":
        return STRING_SIZE
    dims = _dims(t)
    code, mult = 0, 1
    for k, d in enumerate(dims):
        code += d * mult
        if k < len(dims) - 1:
            mult *= RADIX[k]
    return code


def _array_cells(dims):
    if len(dims) == 1:
        return 2 + dims[0]
    return 2 + dims[0] + dims[0] * _array_cells(dims[1:])


def list_code(t):
    """Operand of the list instructions: 4 integers, 5 strings, +16 per extra dimension."""
    e = t[1]
    if isinstance(e, tuple):
        return 16 + list_code(e)
    return 5 if e == "str" else 4


def _type_size(t):
    if t == "str":
        return 2 + STRING_SIZE + 1
    if isinstance(t, tuple):
        dims = _dims(t)
        return 1 + _array_cells(dims)
    return 1


def _hint_type(h):
    if h is None:
        return None
    if isinstance(h, tuple):
        return ("list", _hint_type(h[1]) or "int")
    return {"int": "int", "bool": "bool", "str": "str", "list": ("list", "int")}.get(h, "int")


class Compiler:
    def __init__(self, src):
        self.src = src
        try:
            toks, self.comments = tokenize(src)
        except LexError as e:
            raise CompileError(str(e), e.line) from None
        self.toks = toks
        self.tree = Parser(toks).program()
        self.p = Program()
        self.subs = {}
        self.sub_list = []
        self.globals = {}
        self.consts = {}
        self.cur = None           # current Sub while compiling a body
        self.pending_hclr = False
        self.loops = []           # (continue label, break label, drops)

    # ------------------------------------------------ declarations
    def declare(self):
        """Allocate memory; types depend on each other (parameters on the arguments of the
        calls, results on the returned values), so the allocation is repeated with the
        types found by the previous round."""
        self.prev = {}
        for _ in range(3):
            self._declare_once()
            prev = {}
            for name, v in self.globals.items():
                prev[(None, name)] = v.type
            for sub in self.sub_list:
                for name, v in sub.locals.items():
                    prev[(sub.name, name)] = v.type
                prev[(sub.name, "<result>")] = sub.rtype
            if prev == self.prev:
                break
            self.prev = prev

    def _declare_once(self):
        self.subs = {}
        self.globals = {}
        self.consts = {}
        subs = []

        def collect_subs(body, parent):
            for st in body:
                if st.kind == "def":
                    sub = Sub(st.name, 0, st)
                    sub.parent = parent
                    subs.append(sub)
                    collect_subs(st.body, sub)

        collect_subs(self.tree, None)
        for k, sub in enumerate(subs, 1):
            sub.index = k
            self.subs[sub.name] = sub
        self.sub_list = subs
        n = len(subs)
        self.turtle_base = 15 + n + (1 if n else 0)
        self.fnbase = 15 + n
        self.next_global = self.turtle_base + 7
        # constants: top-level assignments of an all-capitals name to a literal
        for st in self.tree:
            if (st.kind == "assign" and st.target.kind == "name" and st.target.id.isupper()
                    and st.value.kind in ("num", "str", "bool_const", "unary", "name", "binop")):
                v = self.const_value(st.value)
                if v is not None:
                    self.consts[st.target.id] = v
        # functions: result types from hints or return statements
        for sub in subs:
            sub.is_function = self._has_return_value(sub.node.body)
            sub.rtype = _hint_type(sub.node.rtype)
        # globals and locals in textual order, subroutine bodies where they stand
        self._scan(self.tree, None)
        self.p.memtop = self.next_global - 1

    def const_value(self, node):
        if node.kind == "num":
            return node.value
        if node.kind == "bool_const":
            return 1 if node.value else 0
        if node.kind == "str":
            return node.value
        if node.kind == "unary" and node.op == "-" and node.operand.kind == "num":
            return -node.operand.value
        if node.kind == "name" and node.id in COLOUR_VALUES:
            return COLOUR_VALUES[node.id]
        if node.kind == "name" and node.id in self.consts:
            return self.consts[node.id]
        if node.kind == "binop":            # constant expressions are folded
            a = self.const_value(node.left)
            b = self.const_value(node.right)
            if not isinstance(a, int) or not isinstance(b, int):
                return None
            op = node.op
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "//" and b:
                return a // b
            if op == "%" and b:
                return a % b
            if op == "/" and b:
                return (2 * a + b) // (2 * b)
            return None
        return None

    def _has_return_value(self, body):
        for st in body:
            if st.kind == "return" and st.value is not None:
                return True
            for attr in ("body", "orelse"):
                if attr in st.__dict__ and st.kind != "def":
                    if self._has_return_value(getattr(st, attr)):
                        return True
        return False

    def new_global(self, name, vtype, line):
        v = Var(name, vtype, "global", self.next_global)
        v.line = line
        self.next_global += _type_size(vtype)
        self.globals[name] = v
        return v

    def _scan(self, body, sub):
        """Allocate variables in textual order; sub is the enclosing subroutine or None."""
        for st in body:
            if st.kind == "def":
                self._scan_sub(self.subs[st.name])
                continue
            self._scan_stmt(st, sub)

    def _target_name(self, target):
        return target.id if target.kind == "name" else None

    def _scan_stmt(self, st, sub):
        k = st.kind
        if k in ("assign", "declare"):
            name = self._target_name(st.target)
            if name and name not in TURTLE_VARS and name not in self.consts:
                self._bind(name, st, sub)
        elif k == "augassign":
            pass
        elif k == "for":
            if st.var not in TURTLE_VARS:
                self._bind_var(st.var, self._for_type(st, sub), st.line, sub)
            self._scan(st.body, sub)
        elif k in ("if", "while"):
            self._scan(st.body, sub)
            self._scan(getattr(st, "orelse", []), sub)
        elif k == "multi":
            self._scan(st.body, sub)
        elif k == "global" and sub is not None:
            sub.globals.update(st.names)
            for name in st.names:          # a global is allocated where it is first named
                if name not in self.globals and name not in TURTLE_VARS:
                    self.new_global(name, self._global_type(name), st.line)

    def _empty_list_type(self, name, sub):
        """An empty list's element type comes from what is appended to it."""
        for node in self._walk(self.tree):
            if (node.kind == "call" and node.func.kind == "attr" and node.func.name == "append"
                    and node.func.value.kind == "name" and node.func.value.id == name
                    and node.args):
                arg = node.args[0]
                t = self.type_of(arg, sub)
                if arg.kind == "name":
                    owner = self._owner_sub(node)
                    v = self.lookup(arg.id, owner) if owner else self.lookup(arg.id, sub)
                    if v is None:
                        t = self._name_type_anywhere(arg.id)
                return ("list", t)
        return ("list", "int")

    def _owner_sub(self, target):
        for sub in self.sub_list:
            for n in self._walk(sub.node.body):
                if n is target:
                    return sub
        return None

    def _name_type_anywhere(self, name):
        for node in self._walk_ordered(self.tree):
            if node.kind == "assign" and node.target.kind == "name" and node.target.id == name:
                return _hint_type(node.hint) or self.type_of(node.value, None)
        return "int"

    def _global_type(self, name):
        """Type of a global from its first assignment anywhere in the program."""
        for node in self._walk_ordered(self.tree):
            if node.kind == "assign" and node.target.kind == "name" and node.target.id == name:
                hint = _hint_type(node.hint)
                return hint or self.type_of(node.value, None)
        return "int"

    def _walk_ordered(self, body):
        for st in body:
            yield st
            for attr in ("body", "orelse"):
                if attr in st.__dict__:
                    yield from self._walk_ordered(getattr(st, attr))

    def _for_type(self, st, sub):
        seq = st.seq
        if seq.kind == "call" and seq.func.kind == "name" and seq.func.id == "range":
            return "int"
        t = self.type_of(seq, sub)
        if isinstance(t, tuple):
            return t[1]
        if t == "str":
            return "str"
        return "int"

    def _bind(self, name, st, sub):
        hint = _hint_type(getattr(st, "hint", None))
        vtype = hint or (self.type_of(st.value, sub) if st.kind == "assign" else "int")
        if not hint and st.kind == "assign" and st.value.kind == "list" and not st.value.items:
            vtype = self._empty_list_type(name, sub)
        self._bind_var(name, vtype, st.line, sub)

    def _bind_var(self, name, vtype, line, sub):
        if sub is None or name in sub.globals:
            if name not in self.globals:
                self.new_global(name, vtype, line)
            return
        if name in sub.locals:
            return
        v = Var(name, vtype, "local", sub.size + 1, sub)
        v.line = line
        sub.size += _type_size(vtype)
        sub.locals[name] = v

    def _scan_sub(self, sub):
        node = sub.node
        sub.size = 0
        # globals declared anywhere in the body
        def find_globals(body):
            for st in body:
                if st.kind == "global":
                    sub.globals.update(st.names)
                elif st.kind != "def":
                    for attr in ("body", "orelse"):
                        if attr in st.__dict__:
                            find_globals(getattr(st, attr))
        find_globals(node.body)
        for pname, hint in node.params:
            vtype = _hint_type(hint) or self._param_type(sub, pname)
            v = Var(pname, vtype, "param", sub.size + 1, sub)
            v.line = node.line
            sub.size += 1 if isinstance(vtype, tuple) else _type_size(vtype)
            sub.locals[pname] = v
            sub.params.append(v)
        self._scan(node.body, sub)
        if sub.is_function:           # the result comes first in the frame
            rtype = sub.rtype or self._return_type(node.body, sub) or "int"
            sub.rtype = rtype
            shift = _type_size(rtype)
            for v in sub.locals.values():
                v.addr += shift
            sub.size += shift
            sub.result = Var("result", rtype, "result", 1, sub)

    def _return_type(self, body, sub):
        for st in body:
            if st.kind == "return" and st.value is not None:
                return self.type_of(st.value, sub)
            if st.kind != "def":
                for attr in ("body", "orelse"):
                    if attr in st.__dict__:
                        t = self._return_type(getattr(st, attr), sub)
                        if t:
                            return t
        return None

    def _param_type(self, sub, pname):
        """Infer a parameter's type from the arguments of the calls to the subroutine."""
        index = [p for p, _ in sub.node.params].index(pname)
        for node in self._walk(self.tree):
            if (node.kind == "call" and node.func.kind == "name" and node.func.id == sub.name
                    and index < len(node.args)):
                t = self.type_of(node.args[index], self._owner_sub(node), guess=True)
                if t:
                    return t
        return "int"

    def _walk(self, body):
        stack = list(body)
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
                continue
            if not isinstance(n, Node):
                continue
            yield n
            for v in n.__dict__.values():
                if isinstance(v, (Node, list)):
                    stack.append(v)

    # ------------------------------------------------ types
    def lookup(self, name, sub=None):
        sub = self.cur if sub is None else sub
        while sub is not None:
            if name in sub.globals:
                break
            if name in sub.locals:
                return sub.locals[name]
            sub = sub.parent
        if name in self.globals:
            return self.globals[name]
        if name in TURTLE_VARS:
            v = Var(name, "int", "turtle", self.turtle_base + 1 + TURTLE_VARS.index(name))
            return v
        return None

    def type_of(self, node, sub, guess=False):
        k = node.kind
        if k in ("num", "query"):
            return "int"
        if k == "bool_const":
            return "bool"
        if k == "str":
            return "str"
        if k == "name":
            if node.id in self.consts:
                v = self.consts[node.id]
                return "str" if isinstance(v, str) else "int"
            v = self.lookup(node.id, sub)
            if v:
                return v.type
            s = sub
            while s is not None:
                t = self.prev.get((s.name, node.id))
                if t:
                    return t
                s = s.parent
            return self.prev.get((None, node.id), "int")
        if k == "list":
            return ("list", self.type_of(node.items[0], sub) if node.items else "int")
        if k == "binop" and node.op == "*" and node.left.kind == "list":
            return self.type_of(node.left, sub)
        if k == "binop":
            lt = self.type_of(node.left, sub)
            if node.op == "*" and isinstance(lt, tuple):
                return lt
            if node.op == "+" and (lt == "str" or isinstance(lt, tuple)):
                return lt
            return "int"
        if k in ("compare", "not"):
            return "bool"
        if k == "bool":
            return self.type_of(node.left, sub)
        if k == "unary":
            return "int"
        if k == "index":
            t = self.type_of(node.value, sub)
            if t == "str":
                return "str"
            if isinstance(t, tuple):
                return t[1]
            return "int"
        if k == "slice":
            return self.type_of(node.value, sub)
        if k == "call":
            f = node.func
            if f.kind == "name":
                if f.id in self.subs:
                    s = self.subs[f.id]
                    if s.rtype:
                        return s.rtype
                    if self.prev.get((s.name, "<result>")):
                        return self.prev[(s.name, "<result>")]
                    if s is not sub:
                        t = self._return_type(s.node.body, s)
                        if t:
                            return t
                    return "int"
                b = BUILTINS.get(f.id)
                if b:
                    return {"string": "str", "boolean": "bool", "char": "str",
                            "list": ("list", "int")}.get(b["returns"], "int")
                return "int"
            if f.kind == "attr":
                if f.name in ("upper", "lower", "capitalize", "swapcase", "title", "strip",
                              "replace"):
                    return "str"
                if f.name == "copy":
                    return self.type_of(f.value, sub)
                return "int"
        return "int"

    # ------------------------------------------------ code generation helpers
    @property
    def frame(self):
        return 14 + self.cur.index

    def load_var(self, v):
        if v.scope in ("global", "turtle"):
            self.p.emit("LDVG", v.addr)
        else:
            self.p.emit("LDVV", 14 + v.sub.index, v.addr)

    def store_var(self, v):
        if v.scope in ("global", "turtle"):
            self.p.emit("STVG", v.addr)
        else:
            self.p.emit("STVV", 14 + v.sub.index, v.addr)

    def load_addr(self, v):
        if v.scope in ("global", "turtle"):
            self.p.emit("LDAG", v.addr)
        else:
            self.p.emit("LDAV", 14 + v.sub.index, v.addr)

    # ------------------------------------------------ program
    def compile(self):
        self.declare()
        p = self.p
        T = self.turtle_base
        G = self.next_global - (T + 7)
        p.emit("LDIN", T)
        p.emit("DUPL")
        p.emit("DUPL")
        p.emit("LDIN", 0)
        p.emit("SPTR")
        p.emit("LDIN", 6)
        p.emit("SWAP")
        p.emit("SPTR")
        p.emit("INCR")
        p.emit("LDIN", 6 + G)
        p.emit("ZPTR")
        p.emit("LDIN", self.next_global - 1)
        p.emit("STMT")
        p.newline()
        p.emit("TRUE", 1)
        p.emit("HOME")
        p.emit("LDIN", 2)
        p.emit("THIK")
        p.emit("LDIN", 360)
        p.emit("ANGL")
        p.emit("LDIN", 32)
        p.emit("BUFR")
        p.emit("LDIN", 1)
        p.emit("SPTR")
        p.emit("HFIX")
        p.emit("LDIN", 0)
        p.emit("DUPL")
        p.emit("LDIN", 1000)
        p.emit("DUPL")
        p.emit("DUPL")
        p.emit("DUPL")
        p.emit("RESO")
        p.emit("CANV")
        for v in sorted(self.globals.values(), key=lambda v: v.addr):
            if v.type == "str" or isinstance(v.type, tuple):
                p.newline()
                p.emit("LDAG", v.addr + 2)
                p.emit("DUPL")
                p.emit("STVG", v.addr)
                p.emit("LIAD", liad_code(v.type))
        main = Label()
        if self.sub_list:
            p.newline()
            p.emit_jump("JUMP", main)
            for sub in self.sub_list:
                self.compile_sub(sub)
        p.place(main)
        self.body(self.tree)
        p.newline()
        p.emit("HALT")
        p.finish()
        return p

    def compile_sub(self, sub):
        p = self.p
        outer = self.cur
        self.cur = sub
        outer_loops, self.loops = self.loops, []
        p.place(sub.entry)
        p.emit("PSSR", sub.index)
        f = 14 + sub.index
        pending = False
        if sub.size:
            p.newline()
            p.emit("MEMC", f, sub.size)
            p.newline()
            p.emit("LDAV", f, 1)
            p.emit("LDIN", sub.size)
            p.emit("ZPTR")
            if sub.is_function and sub.rtype == "str":
                p.emit("LDAV", f, 3)
                p.emit("STVV", f, 1)
                p.emit("LDIN", STRING_SIZE + 1)
                p.emit("STVV", f, 2)
            for v in reversed(sub.params):
                if v.type != "str":
                    p.emit("STVV", f, v.addr)
                else:
                    p.emit("LDAV", f, v.addr + 1)
                    p.emit("DUPL")
                    p.emit("DUPL")
                    p.emit("INCR")
                    p.emit("STVV", f, v.addr)
                    p.emit("LDIN", STRING_SIZE)
                    p.emit("SWAP")
                    p.emit("SPTR")
                    p.emit("INCR")
                    p.emit("CSTR")
            # a string function releases the heap once its first statement is done
            pending = sub.is_function and sub.rtype == "str"
            for v in sorted(sub.locals.values(), key=lambda v: v.addr):
                if v.scope == "local" and v.type == "str":
                    p.newline()
                    p.emit("LDAV", f, v.addr + 2)
                    p.emit("STVV", f, v.addr)
                    p.emit("LDIN", STRING_SIZE + 1)
                    p.emit("STVV", f, v.addr + 1)
                elif v.scope == "local" and isinstance(v.type, tuple):
                    p.newline()
                    p.emit("LDAV", f, v.addr + 2)
                    p.emit("DUPL")
                    p.emit("STVV", f, v.addr)
                    p.emit("LIAD", liad_code(v.type))
        for st in sub.node.body:
            if st.kind == "def":
                continue
        body = [st for st in sub.node.body if st.kind != "def"]
        if sub.size and pending and body:
            # the heap used to copy string parameters is released after the first statement
            first = body[0]
            if first.kind in ("assign", "augassign", "expr", "global", "pass"):
                if first.kind not in ("global", "pass"):
                    self.statement(first)
                line = p.lines[-1]
                if not line or line[-1] != CODE_HCLR:
                    p.emit("HCLR")
                body = body[1:]
            else:                         # released with the first condition instead
                self.pending_hclr = True
        self.body(body)
        p.place(sub.exit)
        if sub.is_function:
            p.emit("LDVG", f)
            p.emit("STVG", self.fnbase)
        if sub.size:
            p.emit("MEMR", f)
        p.emit("PLSR")
        p.emit("RETN")
        self.cur = outer
        self.loops = outer_loops
        self.pending_hclr = False
        for st in sub.node.body:
            if st.kind == "def":
                pass

    # ------------------------------------------------ statements
    def body(self, stmts):
        for st in stmts:
            self.statement(st)

    def statement(self, st):
        p = self.p
        k = st.kind
        if k == "def":
            return
        p.newline()
        p.mark_source(st.line)
        getattr(self, "st_" + k)(st)

    def st_multi(self, st):
        for s in st.body:
            self.statement(s)

    def st_pass(self, st):
        pass

    def st_global(self, st):
        pass

    def st_declare(self, st):
        pass

    def st_expr(self, st):
        t = self.expr(st.value, statement=True)
        if t not in (None, "none"):           # a discarded function result
            self.p.newline()
            self.p.emit("DROP")

    def st_assign(self, st):
        target = st.target
        if target.kind == "name" and target.id in self.consts and self.cur is None:
            return
        if target.kind == "name":
            v = self.lookup(target.id)
            if v is None:
                raise CompileError(f'"{target.id}" not recognised', st.line)
            if v.type == "str":
                self.load_var(v)
                self.str_expr(st.value)
                self.p.emit("SWAP")
                self.p.emit("CSTR")
                if self.uses_strings(st.value):
                    self.p.emit("HCLR")
                return
            if isinstance(v.type, tuple):
                self.list_assign(v, st.value)
                return
            self.expr(st.value)
            self.store_var(v)
            if self.uses_strings(st.value):
                self.p.emit("HCLR")
            return
        if target.kind == "index":
            self.index_assign(target, st.value)
            return
        raise CompileError("Cannot assign to this", st.line)

    def st_augassign(self, st):
        target = st.target
        if target.kind == "name":
            v = self.lookup(target.id)
            if v is not None and v.type not in ("str",) and not isinstance(v.type, tuple):
                self.expr(st.value)
                self.load_var(v)
                self.p.emit("SWAP")
                self.p.emit(self._BIN[st.op])
                self.store_var(v)
                return
        if target.kind == "index" and self.type_of(target.value, self.cur) != "str":
            p = self.p
            self.element_address(target)
            self.expr(st.value)
            p.emit("PICK", 2)
            p.emit("LPTR")
            p.emit("SWAP")
            p.emit(self._BIN[st.op])
            p.emit("SWAP")
            p.emit("SPTR")
            return
        node = Node("binop", st.line, op=st.op, left=st.target, right=st.value)
        self.st_assign(Node("assign", st.line, target=st.target, value=node, hint=None))

    def st_if(self, st, outer_end=None):
        """if/elif/else; the original threads the end jumps of an elif chain, so an earlier
        branch jumps to the end jump of the following one."""
        p = self.p
        orelse = Label()
        self.condition(st.cond)
        p.emit_jump("IFNO", orelse)
        self.body(st.body)
        if st.orelse:
            end = Label()
            p.newline()
            if outer_end is not None:
                p.place(outer_end)
            p.emit_jump("JUMP", end)
            p.place(orelse)
            if len(st.orelse) == 1 and getattr(st.orelse[0], "is_elif", False):
                p.newline()
                self.st_if(st.orelse[0], end)
            else:
                self.body(st.orelse)
            p.place(end)
        else:
            p.place(orelse)
            if outer_end is not None:
                p.place(outer_end)

    def st_while(self, st):
        p = self.p
        top = Label()
        end = Label()
        p.place(top)
        self.condition(st.cond)
        p.emit_jump("IFNO", end)
        self.loops.append((top, end, 0))
        self.body(st.body)
        self.loops.pop()
        p.newline()
        p.emit_jump("JUMP", top)
        p.place(end)

    def st_for(self, st):
        p = self.p
        seq = st.seq
        v = self.lookup(st.var)
        if seq.kind == "call" and seq.func.kind == "name" and seq.func.id == "range":
            args = seq.args
            if len(args) == 1:
                self.expr(args[0])
                p.emit("LDIN", 0)
                p.emit("SWAP")
                p.emit("LDIN", 1)
                p.emit("ROTA")
            else:
                self.expr(args[0])
                p.newline()
                self.expr(args[1])
                if len(args) > 2:
                    p.newline()
                    self.expr(args[2])
                else:
                    p.emit("LDIN", 1)
                p.emit("ROTA")
            top = Label()
            drop3 = Label()
            drop2 = Label()
            neg = Label()
            body = Label()
            cont = Label()
            p.place(top)
            p.emit("DUPL")
            self.store_var(v)
            p.emit("PICK", 3)
            p.emit("PICK", 3)
            p.emit("DUPL")
            p.emit_jump("IFNO", drop3)
            p.newline()
            p.emit("LDIN", 0)
            p.emit("MORE")
            p.emit_jump("IFNO", neg)
            p.emit("LESS")
            p.emit_jump("IFNO", drop2)
            p.newline()
            p.emit_jump("JUMP", body)
            p.place(neg)
            p.emit("MORE")
            p.emit_jump("IFNO", drop2)
            p.place(body)
            self.loops.append((cont, drop2, 2))
            self.body(st.body)
            self.loops.pop()
            p.place(cont)
            p.emit("DUPL")
            self.load_var(v)
            p.emit("PLUS")
            p.emit_jump("JUMP", top)
            p.place(drop3)
            p.emit("DROP")
            p.emit("DROP")
            p.emit("DROP")
            p.place(drop2)
            p.emit("DROP")
            p.emit("DROP")
            return
        # for element in list / string
        t = self.type_of(seq, self.cur)
        self.expr(seq)
        if t == "str" and seq.kind == "name":
            p.emit("HSTR")
        p.emit("DUPL")
        p.emit("LPTR")
        p.emit("LDIN", 0)
        top = Label()
        end = Label()
        p.place(top)
        p.emit("INCR")
        p.emit("DUPL")
        p.emit("PICK", 3)
        p.emit("LSEQ")
        p.emit_jump("IFNO", end)
        p.newline()
        p.emit("DUPL")
        p.emit("PICK", 4)
        p.emit("PLUS")
        p.emit("LPTR")
        if t == "str":
            p.emit("LDIN", 1)
            if v.scope == "global":
                p.emit("STVG", v.addr + 2)
                p.emit("STVG", v.addr + 3)
            else:
                f = 14 + v.sub.index
                p.emit("STVV", f, v.addr + 2)
                p.emit("STVV", f, v.addr + 3)
            p.emit("HCLR")
        else:
            self.store_var(v)
        self.loops.append((top, end, 3))
        self.body(st.body)
        self.loops.pop()
        p.newline()
        p.emit_jump("JUMP", top)
        p.place(end)
        p.emit("DROP")
        p.emit("DROP")
        p.emit("DROP")

    def st_break(self, st):
        if not self.loops:
            raise CompileError('"break" outside a loop', st.line)
        _, end, drops = self.loops[-1]
        self.p.emit_jump("JUMP", end)

    def st_continue(self, st):
        if not self.loops:
            raise CompileError('"continue" outside a loop', st.line)
        top, _, _ = self.loops[-1]
        self.p.emit_jump("JUMP", top)

    def st_return(self, st):
        sub = self.cur
        if sub is None:
            raise CompileError('"return" outside a subroutine', st.line)
        if st.value is not None:
            self.expr(st.value)
            self.p.emit("STVV", 14 + sub.index, 1)
        for _, _, drops in self.loops:
            for _ in range(drops):
                self.p.emit("DROP")
        if st is not self._last_statement(sub.node.body):
            self.p.emit_jump("JUMP", sub.exit)

    def _last_statement(self, body):
        body = [b for b in body if b.kind != "def"]
        if not body:
            return None
        st = body[-1]
        if st.kind == "if":
            return self._last_statement(st.orelse or st.body)
        if st.kind in ("while", "for"):
            return self._last_statement(st.body)
        if st.kind == "multi":
            return st.body[-1]
        return st

    # ------------------------------------------------ conditions and expressions
    def uses_strings(self, node):
        """Whether evaluating the expression puts a temporary string on the heap."""
        for n in self._walk([node]):
            if n.kind in ("str", "slice"):
                return True
            if n.kind == "binop" and n.op == "+" and self.type_of(n, self.cur) == "str":
                return True
            if n.kind == "call" and self.type_of(n, self.cur) == "str":
                return True
            if n.kind == "index" and self.type_of(n.value, self.cur) == "str":
                return True
        return False

    def condition(self, node):
        self.expr(node)
        if self.uses_strings(node) or self.pending_hclr:
            self.p.emit("HCLR")
        self.pending_hclr = False

    def str_expr(self, node):
        t = self.expr(node)
        if t == "int":
            pass
        return t

    def expr(self, node, statement=False):
        if node.kind == "call":
            return self.ex_call(node, statement)
        return getattr(self, "ex_" + node.kind)(node)

    def ex_num(self, node):
        self.p.emit("LDIN", node.value)
        return "int"

    def ex_query(self, node):
        self.p.emit("LDIN", node.value)
        self.p.emit("STAT")
        return "int"

    def ex_bool_const(self, node):
        self.p.emit("LDIN", 1 if node.value else 0)
        return "bool"

    def ex_str(self, node):
        self.p.emit_str(node.value)
        return "str"

    def ex_name(self, node):
        name = node.id
        if name in self.consts:
            v = self.consts[name]
            if isinstance(v, str):
                self.p.emit_str(v)
                return "str"
            self.p.emit("LDIN", v)
            return "int"
        v = self.lookup(name)
        if v is None:
            if name in COLOUR_VALUES:
                self.p.emit("LDIN", COLOUR_VALUES[name])
                return "int"
            raise CompileError(f'"{name}" not recognised', node.line)
        self.load_var(v)
        return v.type

    def ex_list(self, node):
        p = self.p
        t = self.type_of(node, self.cur)
        p.emit("LIHP", len(node.items) + (60 * 2048 if t[1] == "str" else 0))
        p.emit("DUPL")
        self.list_items(node.items, t[1])
        return t

    def ex_unary(self, node):
        if node.op == "-":
            self.expr(node.operand)
            self.p.emit("NEG")
        elif node.op == "~":
            self.expr(node.operand)
            self.p.emit("NOT")
        else:
            self.expr(node.operand)
        return "int"

    def ex_not(self, node):
        self.expr(node.operand)
        self.p.emit("LDIN", 0)
        self.p.emit("EQAL")
        return "bool"

    def ex_bool(self, node):
        self.expr(node.left)
        self.expr(node.right)
        self.p.emit("ANDL" if node.op == "and" else "ORL")
        return "bool"

    _CMP = {"==": "EQAL", "!=": "NOEQ", "<>": "NOEQ", "<": "LESS", ">": "MORE", "<=": "LSEQ",
            ">=": "MREQ"}
    _SCMP = {"==": "SEQL", "!=": "SNEQ", "<>": "SNEQ", "<": "SLES", ">": "SMOR", "<=": "SLEQ",
             ">=": "SMEQ"}

    def ex_compare(self, node):
        if node.op in ("in", "not in"):
            self.expr(node.left)
            self.p.newline()
            rt = self.expr(node.right)
            if isinstance(rt, tuple):
                self.p.emit("SWAP")
                self.p.emit("LIDX", list_code(rt))
                self.p.emit("LDIN", -1)
                self.p.emit("NOEQ")
            else:
                self.p.emit("POSS")
                self.p.emit("SIGN")
            if node.op == "not in":
                self.p.emit("LDIN", 0)
                self.p.emit("EQAL")
            return "bool"
        lt = self.expr(node.left)
        rt = self.expr(node.right)
        if lt == "str" or rt == "str":
            self.p.emit(self._SCMP[node.op])
        else:
            self.p.emit(self._CMP[node.op])
        return "bool"

    _BIN = {"+": "PLUS", "-": "SUBT", "*": "MULT", "/": "DIVR", "//": "DIVF", "%": "MODF",
            "&": "AND", "|": "OR", "^": "XOR"}

    def ex_binop(self, node):
        p = self.p
        lt = self.type_of(node.left, self.cur)
        if node.op == "*" and isinstance(lt, tuple):
            return self.list_mult(node)
        self.expr(node.left)
        rt = self.expr(node.right)
        if node.op == "+" and (lt == "str" or rt == "str"):
            p.emit("SCAT")
            return "str"
        if node.op == "<<":
            p.emit("SHFT")
            return "int"
        if node.op == ">>":
            p.emit("NEG")
            p.emit("SHFT")
            return "int"
        if node.op == "**":
            p.emit("POWR")
            return "int"
        p.emit(self._BIN[node.op])
        return "int"

    def string_index(self, base, index, pick=2):
        """Push base and a non-negative index (negative counts from the end)."""
        p = self.p
        self.expr(index)
        after = Label()
        p.emit("DUPL")
        p.emit("LDIN", 0)
        p.emit("LESS")
        p.emit_jump("IFNO", after)
        p.emit("PICK", pick)
        p.emit("LPTR")
        p.emit("PLUS")
        p.place(after)

    def ex_index(self, node):
        p = self.p
        t = self.type_of(node.value, self.cur)
        self.expr(node.value)
        if t == "str":
            self.string_index(node.value, node.index)
            p.emit("SWAP")
            p.emit("TEST")
            p.emit("PLUS")
            p.emit("INCR")
            p.emit("LPTR")
            p.emit("CTOS")
            return "str"
        self.expr(node.index)
        p.emit("SWAP")
        p.emit("TEST")
        p.emit("PLUS")
        p.emit("INCR")
        p.emit("LPTR")
        return t[1] if isinstance(t, tuple) else "int"

    def ex_slice(self, node):
        p = self.p
        self.expr(node.value)
        if node.lo is None:
            p.emit("LDIN", 0)
        else:
            self.string_index(node.value, node.lo, 2)
        if node.hi is None:
            p.newline()
            p.emit("PICK", 2)
            p.emit("LPTR")
            p.place(Label())
        else:
            self.string_index(node.value, node.hi, 3)
        p.emit("SWAP")
        p.emit("DUPL")
        p.emit("INCR")
        p.emit("ROTA")
        p.emit("ROTA")
        p.emit("SUBT")
        p.emit("COPY")
        return "str"

    def element_address(self, target):
        """Push the address of a list element."""
        p = self.p
        v = target.value
        if v.kind == "name":
            var = self.lookup(v.id)
            self.load_addr(var)
            p.emit("LPTR")
        elif v.kind == "index":
            self.element_address(v)
            p.emit("LPTR")
        else:
            self.expr(v)
        self.expr(target.index)
        p.emit("SWAP")
        p.emit("TEST")
        p.emit("PLUS")
        p.emit("INCR")

    def index_assign(self, target, value):
        p = self.p
        self.element_address(target)
        t = self.type_of(target, self.cur)
        if t == "str":
            p.emit("LPTR")
            self.expr(value)
            p.emit("SWAP")
            p.emit("CSTR")
            if self.uses_strings(value):
                p.emit("HCLR")
            return
        self.expr(value)
        p.emit("SWAP")
        p.emit("SPTR")
        if self.uses_strings(value):
            p.emit("HCLR")

    def list_mult(self, node):
        p = self.p
        # [x]*n : a one-element heap list replicated n times
        raise CompileError("list multiplication only in assignments", node.line)

    def list_items(self, items, elem, fresh_line=True):
        """Store literal items into the list whose base address is on the stack (twice);
        nested lists fill the sub-lists their elements already point to."""
        p = self.p
        if isinstance(elem, tuple) and items:
            p.newline()
            p.emit("DUPL")
            for k, item in enumerate(items):
                p.emit("INCR")
                p.newline()
                p.emit("DUPL")
                p.emit("LPTR")
                p.emit("DUPL")
                self.list_items(item.items if item.kind == "list" else [], elem[1], False)
            p.emit("PICK", 2)
            p.emit("SUBT")
            p.emit("SWAP")
            p.emit("SPTR")
            return
        if fresh_line:
            p.newline()
            if items:
                p.emit("DUPL")
        for k, item in enumerate(items):
            if k:
                p.newline()
            p.emit("INCR")
            self.expr(item)
            p.emit("PICK", 2)
            if elem == "str":
                p.emit("LPTR")
                p.emit("CSTR")
            else:
                p.emit("SPTR")
        p.newline()
        if items:
            p.emit("PICK", 2)
            p.emit("SUBT")
        else:
            p.emit("LDIN", 0)
        p.emit("SWAP")
        p.emit("SPTR")

    def list_assign(self, v, value):
        p = self.p
        elem = v.type[1]
        if value.kind == "binop" and value.op == "*" and value.left.kind == "list":
            self.load_addr(v)
            p.emit("LPTR")
            p.emit("DUPL")
            self.list_items(value.left.items, elem)
            self.expr(value.right)
            p.emit("LMUL", list_code(v.type))
            if any(self.uses_strings(i) for i in value.left.items):
                p.emit("HCLR")
            return
        if value.kind == "list":
            self.load_addr(v)
            p.emit("LPTR")
            p.emit("DUPL")
            self.list_items(value.items, elem)
            p.emit("DROP")
            if any(self.uses_strings(i) for i in value.items):
                p.emit("HCLR")
            return
        self.load_addr(v)
        p.emit("LPTR")
        self.expr(value)
        p.emit("LCPY", list_code(v.type))

    # ------------------------------------------------ calls
    def ex_attr(self, node):
        raise CompileError(f'"{node.name}" not recognised', node.line)

    def ex_call(self, node, statement=False):
        f = node.func
        p = self.p
        if f.kind == "attr":
            return self.method_call(node)
        if f.kind != "name":
            raise CompileError("Call of a non-subroutine", node.line)
        name = f.id
        if name in self.subs:
            sub = self.subs[name]
            p.newline()
            for a, prm in zip(node.args, sub.params):
                self.expr(a)
            if len(node.args) != len(sub.params):
                raise CompileError(f"{name} takes {len(sub.params)} parameters", node.line)
            if statement and any(self.uses_strings(a) for a in node.args):
                p.emit("HCLR")
            p.emit_jump("SUBR", sub.entry)
            p.newline()
            if sub.is_function:
                p.emit("LDVV", self.fnbase, 1)
                if sub.rtype == "str":
                    p.emit("HSTR")
                return sub.rtype
            return "none"
        handler = getattr(self, "bi_" + name, None)
        if handler:
            return handler(node)
        b = BUILTINS.get(name)
        if b is None:
            raise CompileError(f'"{name}" not recognised', node.line)
        for a in node.args:
            self.expr(a)
        p.emit(_opname(b["opcode"]))
        r = b["returns"]
        if r == "procedure":
            if any(self.uses_strings(a) for a in node.args):
                p.emit("HCLR")
            return "none"
        return {"string": "str", "boolean": "bool", "char": "str"}.get(r, "int")

    def method_call(self, node):
        f = node.func
        p = self.p
        t = self.type_of(f.value, self.cur)
        m = f.name
        if isinstance(t, tuple):
            ops = {"append": "LAPP", "del": "LDEL", "extend": "LEXT", "remove": "LREM",
                   "insert": "LINS", "reverse": "LREV"}
            if m in ops:
                var = self.lookup(f.value.id) if f.value.kind == "name" else None
                if var:
                    self.load_addr(var)
                    p.emit("LPTR")
                else:
                    self.expr(f.value)
                for a in node.args:
                    self.expr(a)
                p.emit(ops[m], list_code(t))
                if m == "extend":
                    p.emit("HCLR")
                return "none"
            if m == "index":
                self.expr(f.value)
                for a in node.args:
                    self.expr(a)
                p.emit("LIDX", list_code(t))
                return "int"
            if m == "pop":
                self.expr(f.value)
                for a in node.args:
                    self.expr(a)
                p.emit("LPOP", list_code(t))
                return "int"
            if m == "copy":
                self.expr(f.value)
                return t
        cases = {"lower": 1, "upper": 2, "capitalize": 3, "title": 4, "swapcase": 5}
        if m in cases:
            self.expr(f.value)
            p.emit("LDIN", cases[m])
            p.emit("CASE")
            return "str"
        if m in ("find", "index"):
            self.expr(f.value)
            self.expr(node.args[0])
            p.emit("SWAP")
            p.emit("POSS")
            p.emit("DECR")
            return "int"
        if m == "strip":
            self.expr(f.value)
            p.emit("TRIM")
            return "str"
        if m == "replace":
            self.expr(f.value)
            for a in node.args:
                self.expr(a)
            if len(node.args) < 3:
                p.emit("LDIN", 0)
            p.emit("REPL")
            return "str"
        raise CompileError(f'"{m}" not recognised', node.line)

    # builtins compiled to special sequences
    def bi_print(self, node):
        p = self.p
        args = node.args
        end = node.kwargs.get("end")
        sep = node.kwargs.get("sep")
        for a in args:
            self.to_string(a)
        n = len(args)
        for k in range(n):
            left = n - k              # strings still on the stack
            if left == 2:
                p.emit("SWAP")
            elif left == 3:
                p.emit("ROTA")
            elif left > 3:
                p.emit("LDIN", left)
                p.emit("ROLL")
            if k:
                pass
            p.emit("WRIT")
            if k < n - 1:
                if sep is None:
                    p.emit_str(" ")
                    p.emit("WRIT")
                elif not (sep.kind == "str" and sep.value == ""):
                    self.expr(sep)
                    p.emit("WRIT")
        if end is None:
            p.emit_str("\r\n")
            p.emit("WRIT")
        elif not (end.kind == "str" and end.value == ""):
            self.expr(end)
            p.emit("WRIT")
        p.emit("HCLR")
        return "none"

    def to_string(self, node):
        t = self.expr(node)
        if isinstance(t, tuple):
            self.p.emit("LPRT", list_code(t))
        elif t in ("int", "bool"):
            self.p.emit("ITOS")
        return "str"

    def bi_str(self, node):
        t = self.expr(node.args[0])
        if t != "str":
            self.p.emit("ITOS")
        return "str"

    def bi_input(self, node):
        if node.args:
            self.expr(node.args[0])
            self.p.emit("WRIT")
        self.p.emit("RDLN")
        return "str"

    def bi_sqrt(self, node):
        self.expr(node.args[0])
        self.expr(node.args[1])
        self.p.emit("LDIN", 2)
        self.p.emit("SWAP")
        self.p.emit("ROOT")
        return "int"

    def _file_test(self, node, op, code, cmp, limit):
        self.expr(node.args[0])
        self.p.emit("LDIN", code)
        self.p.emit(op)
        self.p.emit("LDIN", limit)
        self.p.emit(cmp)
        return "bool"

    def bi_isfile(self, node):
        return self._file_test(node, "FILE", 0, "MORE", 127)

    def bi_fremove(self, node):
        return self._file_test(node, "FILE", 1, "LESS", 128)

    def bi_isdir(self, node):
        return self._file_test(node, "DIRY", 0, "MORE", 127)

    def bi_rmdir(self, node):
        return self._file_test(node, "DIRY", 1, "LESS", 128)

    def bi_mkdir(self, node):
        return self._file_test(node, "DIRY", 2, "MORE", 127)

    def _fmov(self, node, mode):
        self.expr(node.args[0])
        self.expr(node.args[1])
        self.p.emit("LDIN", mode)
        self.p.emit("FMOV")
        return "bool"

    def bi_frename(self, node):
        return self._fmov(node, 1)

    def bi_fmove(self, node):
        return self._fmov(node, 2)

    def bi_fcopy(self, node):
        return self._fmov(node, 3)

    def _var_param_string(self, node, op):
        """findfirst / finddir: (pattern, handle variable) -> string, updating the handle."""
        p = self.p
        self.expr(node.args[0])
        h = node.args[1]
        v = self.lookup(h.id) if h.kind == "name" else None
        if v is None:
            raise CompileError("Variable expected", node.line)
        self.load_addr(v)
        p.emit("DUPL")
        p.emit("LPTR")
        p.emit("ROTA")
        p.emit(op)
        p.emit("SWAP")
        p.emit("ROTA")
        p.emit("SPTR")
        return "str"

    def bi_findfirst(self, node):
        return self._var_param_string(node, "FFND")

    def bi_finddir(self, node):
        return self._var_param_string(node, "FDIR")

    def bi_address(self, node):
        a = node.args[0]
        v = self.lookup(a.id) if a.kind == "name" else None
        if v is None:
            raise CompileError("Variable expected", node.line)
        self.load_addr(v)
        return "int"

    def bi_len(self, node):
        self.expr(node.args[0])
        self.p.emit("SLEN")
        return "int"

    def bi_ord(self, node):
        self.expr(node.args[0])
        self.p.emit("CTST")
        self.p.emit("INCR")
        self.p.emit("LPTR")
        return "int"

    def bi_chr(self, node):
        self.expr(node.args[0])
        self.p.emit("CTOS")
        return "str"

    def bi_randcol(self, node):
        self.expr(node.args[0])
        self.p.emit("RAND")
        self.p.emit("INCR")
        self.p.emit("RGB")
        self.p.emit("COLR")
        return "none"

    def bi_penup(self, node):
        self.p.emit("LDIN", 0)
        self.p.emit("PEN")
        return "none"

    def bi_pendown(self, node):
        self.p.emit("LDIN", -1)
        self.p.emit("PEN")
        return "none"

    def bi_noupdate(self, node):
        self.p.emit("LDIN", 0)
        self.p.emit("UDAT")
        return "none"

    def bi_update(self, node):
        self.p.emit("LDIN", -1)
        self.p.emit("UDAT")
        return "none"

    def bi_randint(self, node):
        self.expr(node.args[0])
        self.expr(node.args[1])
        self.p.emit("PICK", 2)
        self.p.emit("SUBT")
        self.p.emit("INCR")
        self.p.emit("RAND")
        self.p.emit("PLUS")
        return "int"

    def bi_randrange(self, node):
        self.expr(node.args[0])
        self.p.emit("RAND")
        return "int"

    def bi_reset(self, node):
        self.expr(node.args[0])
        self.p.emit("ICLR")
        return "none"

    def bi_intdef(self, node):
        self.expr(node.args[0])
        self.expr(node.args[1])
        self.p.emit("LDIN", 3)
        self.p.emit("SVDF")
        return "int"

    def bi_int(self, node):
        self.expr(node.args[0])
        self.p.emit("LDIN", 3)
        self.p.emit("SVAL")
        return "int"


def _opname(code):
    from tm.pcode import OPS
    return OPS[code][0]


def compile_python(src):
    c = Compiler(src)
    return c.compile(), c
