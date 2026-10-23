"""Turtle Pascal to Turtle Machine PCode, following the original compiler's code patterns.

Memory layout as for Turtle Python (see tm.pycomp).  Pascal specifics: TRUE is -1, a string
variable is a pointer cell followed by its capacity + 1, its length and its characters; an
array is a pointer cell, its length and its elements (pointers to the rows / strings, which
follow, for arrays of arrays or strings); characters are their codes.
"""

import json
from pathlib import Path

from colours import COLOURS
from lang_pascal import tokenize as pascal_tokenize
from tm.pcode import CODE, OPS, Label, Program

STRING_SIZE = 64
TURTLE_VARS = ("turtx", "turty", "turtd", "turta", "turtt", "turtc")
COLOUR_VALUES = {name: value for name, value in COLOURS}
CODE_HCLR = CODE["HCLR"]

_BUILTINS = json.loads((Path(__file__).resolve().parent.parent / "data" / "builtins.json")
                       .read_text())["builtins"]
BUILTINS = {b["names"]["pascal"].lower(): b for b in _BUILTINS if b["names"]["pascal"]}


class CompileError(Exception):
    def __init__(self, msg, line):
        super().__init__(msg)
        self.line = line


class Node:
    def __init__(self, kind, line, **kw):
        self.kind = kind
        self.line = line
        self.__dict__.update(kw)

    def __repr__(self):
        return f"<{self.kind} {self.__dict__}>"


# types: ("int",) ("bool",) ("char",) ("str", size) ("array", lo, hi, elem)
INT, BOOL, CHAR = ("int",), ("bool",), ("char",)


def STR(size=STRING_SIZE):
    return ("str", size)


def is_str(t):
    return t[0] == "str"


def type_size(t):
    k = t[0]
    if k == "str":
        return 3 + t[1]
    if k == "array":
        n = t[2] - t[1] + 1
        return 2 + n + n * (element_body(t[3]))
    return 1


def element_body(t):
    """Cells an array element needs beyond its slot (strings / rows are stored after)."""
    if t[0] == "str":
        return 2 + t[1]
    if t[0] == "array":
        n = t[2] - t[1] + 1
        return 1 + n + n * element_body(t[3])
    return 0


class Var:
    def __init__(self, name, vtype, scope, addr, sub=None, byref=False):
        self.name = name
        self.type = vtype
        self.scope = scope            # global local param result turtle const
        self.addr = addr
        self.sub = sub
        self.byref = byref
        self.line = None
        self.value = None


class Sub:
    def __init__(self, name, index, line):
        self.name = name
        self.index = index
        self.line = line
        self.params = []
        self.locals = {}
        self.consts = {}
        self.subs = {}
        self.rtype = None
        self.size = 0
        self.entry = Label()
        self.exit = Label()
        self.parent = None
        self.body = []
        self.children = []
        self.end_line = line

    @property
    def is_function(self):
        return self.rtype is not None


class TrackingProgram(Program):
    """Remembers whether the code since the last subroutine call put strings on the heap."""
    HEAP_OPS = {"LSTR", "CTOS", "HSTR", "COPY", "DELS", "INSS", "CASE", "REPL", "SPAD", "TRIM",
                "QTOS", "HEXS", "RDLN", "READ", "FRLN", "ITOS", "FRDS", "FNXT", "FFND", "FDIR"}

    def __init__(self):
        super().__init__()
        self.heap = False               # this statement put strings on the heap
        self.line_heap = False          # ... so did the current line (not counting HSTR)
        self.pending_break = False      # a label points at the next line, not yet begun

    def _break(self):
        if self.pending_break:
            self.pending_break = False
            if self.lines[-1]:
                self.lines.append([])
                self.line_heap = False

    def newline(self):
        self._break()
        if self.lines[-1]:
            self.line_heap = False
        super().newline()

    def emit(self, op, *args):
        if op != "HCLR":
            self._break()
        if op in self.HEAP_OPS:
            self.heap = True
            if op != "HSTR":
                self.line_heap = True
        super().emit(op, *args)

    def emit_str(self, text):
        self._break()
        self.heap = True
        self.line_heap = True
        super().emit_str(text)

    def emit_jump(self, op, label):
        self._break()
        super().emit_jump(op, label)

    def place(self, label):
        """The label names the next line; the line break waits for the next instruction, so
        a closing HCLR can still go at the end of the current line."""
        if self.pending_break:              # several labels for the same coming line
            label.line = len(self.lines) + 1
        elif self.lines[-1]:
            label.line = len(self.lines) + 1
            self.pending_break = True
        else:
            label.line = len(self.lines)
        for li, pos in label.uses:
            self.lines[li][pos] = label.line
        label.uses.clear()

    def mark_source(self, src_line):
        self._break()
        super().mark_source(src_line)

    def finish(self):
        self.pending_break = False
        super().finish()


class Compiler:
    builtins = BUILTINS
    LANG_CODE = 1           # the language code that val / valdef pass to the machine

    @staticmethod
    def handler_name(name):
        return "bi_" + name

    def __init__(self, src):
        self.src = src
        try:
            self.toks = pascal_tokenize(src)
        except Exception as e:  # noqa: BLE001
            raise CompileError(str(e), getattr(e, "line", 0)) from None
        self.i = 0
        self.p = TrackingProgram()
        self.globals = {}
        self.consts = {}
        self.subs_all = []
        self.top_subs = {}
        self.cur = None
        self.pending_hclr = False
        self.loops = []
        nsubs = sum(1 for t in self.toks if t.kind == "kw" and t.value in ("procedure", "function"))
        nfuncs = sum(1 for t in self.toks if t.kind == "kw" and t.value == "function")
        self.nsubs = nsubs
        self.turtle_base = 15 + nsubs + (1 if nfuncs else 0)
        self.fnbase = 15 + nsubs
        self.next_global = self.turtle_base + 7
        self.global_order = []

    # ------------------------------------------------ token helpers
    @property
    def tok(self):
        return self.toks[self.i]

    def peek(self, k=1):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind, value=None):
        t = self.tok
        return t.kind == kind and (value is None or t.value == value)

    def accept(self, kind, value=None):
        if self.at(kind, value):
            return self.next()
        return None

    def expect(self, kind, value=None):
        if not self.at(kind, value):
            what = value if value is not None else kind
            raise CompileError(f'"{what}" expected', self.tok.line)
        return self.next()

    def ident(self):
        t = self.tok
        if t.kind != "id":
            raise CompileError("Identifier expected", t.line)
        self.next()
        return t.value

    # ------------------------------------------------ declarations
    def program(self):
        self.expect("kw", "program")
        self.ident()
        self.expect("op", ";")
        self.declarations(None)
        self.expect("kw", "begin")
        self.main = self.statements_until("end")
        self.expect("kw", "end")

    def declarations(self, sub):
        while True:
            if self.accept("kw", "const"):
                while self.at("id"):
                    t = self.tok
                    name = self.ident()
                    self.expect("op", "=")
                    value = self.const_expr()
                    self.expect("op", ";")
                    (sub.consts if sub else self.consts)[name] = value
            elif self.accept("kw", "var"):
                while self.at("id"):
                    line = self.tok.line
                    names = [self.ident()]
                    while self.accept("op", ","):
                        names.append(self.ident())
                    self.expect("op", ":")
                    t = self.type_spec()
                    self.expect("op", ";")
                    for name in names:
                        self.declare_var(name, t, sub, line)
            elif self.at("kw", "procedure") or self.at("kw", "function"):
                self.routine(sub)
            else:
                return

    def declare_var(self, name, t, sub, line):
        if sub is None:
            v = Var(name, t, "global", self.next_global)
            self.next_global += type_size(t)
            self.globals[name] = v
            self.global_order.append(v)
        else:
            v = Var(name, t, "local", sub.size + 1, sub)
            sub.size += type_size(t)
            sub.locals[name] = v
        v.line = line
        return v

    def const_expr(self):
        node = self.expression()
        v = self.fold(node)
        if v is None:
            raise CompileError("Constant expected", node.line)
        return v

    def fold(self, node):
        k = node.kind
        if k == "num":
            return node.value
        if k == "str":
            return node.value
        if k == "bool_const":
            return -1 if node.value else 0
        if k == "name":
            c = self.find_const(node.id)
            return c
        if k == "unary" and node.op == "-":
            v = self.fold(node.operand)
            return -v if isinstance(v, int) else None
        if k == "binop":
            a, b = self.fold(node.left), self.fold(node.right)
            if not isinstance(a, int) or not isinstance(b, int):
                return None
            return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                    "div": lambda: int(a / b) if b else None,
                    "mod": lambda: a - int(a / b) * b if b else None}.get(node.op, lambda: None)()
        return None

    def find_const(self, name):
        sub = self.cur_decl
        while sub is not None:
            if name in sub.consts:
                return sub.consts[name]
            sub = sub.parent
        if name in self.consts:
            return self.consts[name]
        if name in COLOUR_VALUES:
            return COLOUR_VALUES[name]
        return None

    cur_decl = None

    def type_spec(self):
        if self.accept("kw", "array"):
            if self.accept("kw", "of"):           # open array parameter: indexed from 0
                return ("array", 0, -1, self.type_spec())
            self.expect("op", "[")
            ranges = [self.subrange()]
            while self.accept("op", ","):
                ranges.append(self.subrange())
            self.expect("op", "]")
            self.expect("kw", "of")
            elem = self.type_spec()
            for lo, hi in reversed(ranges):
                elem = ("array", lo, hi, elem)
            return elem
        name = self.ident()
        if name == "integer":
            return INT
        if name == "boolean":
            return BOOL
        if name == "char":
            return CHAR
        if name == "string":
            if self.accept("op", "["):
                n = self.const_expr()
                self.expect("op", "]")
                return STR(n)
            return STR()
        raise CompileError(f'"{name}" is not a type', self.tok.line)

    def subrange(self):
        lo = self.const_expr()
        self.expect("op", "..")
        hi = self.const_expr()
        return int(lo) if not isinstance(lo, str) else ord(lo), \
            int(hi) if not isinstance(hi, str) else ord(hi)

    def routine(self, parent):
        is_func = self.next().value == "function"
        line = self.tok.line
        name = self.ident()
        sub = Sub(name, len(self.subs_all) + 1, line)
        sub.parent = parent
        self.subs_all.append(sub)
        (parent.subs if parent else self.top_subs)[name] = sub
        if parent:
            parent.children.append(sub)
        outer = self.cur_decl
        self.cur_decl = sub
        params = []
        if self.accept("op", "("):
            while not self.at("op", ")"):
                byref = bool(self.accept("kw", "var"))
                names = [self.ident()]
                while self.accept("op", ","):
                    names.append(self.ident())
                self.expect("op", ":")
                t = self.type_spec()
                for n in names:
                    params.append((n, t, byref))
                if not self.accept("op", ";"):
                    break
            self.expect("op", ")")
        if is_func:
            self.expect("op", ":")
            sub.rtype = self.type_spec()
            sub.size = type_size(sub.rtype)
            sub.result = Var("result", sub.rtype, "result", 1, sub)
        for n, t, byref in params:
            v = Var(n, t, "param", sub.size + 1, sub, byref)
            sub.size += 1 if byref else type_size(t)
            sub.locals[n] = v
            sub.params.append(v)
        self.expect("op", ";")
        self.declarations(sub)
        self.expect("kw", "begin")
        sub.body = self.statements_until("end")
        sub.end_line = self.tok.line
        self.expect("kw", "end")
        self.expect("op", ";")
        self.cur_decl = outer

    # ------------------------------------------------ statements (parsed into nodes)
    def statements_until(self, *enders):
        body = []
        while True:
            if self.at("kw") and self.tok.value in enders:
                break
            if self.at("op", ";"):
                self.next()
                continue
            body.append(self.parse_statement())
            if not self.accept("op", ";"):
                break
        return body

    def parse_statement(self):
        t = self.tok
        line = t.line
        if t.kind == "kw":
            w = t.value
            if w == "begin":
                self.next()
                body = self.statements_until("end")
                self.expect("kw", "end")
                return Node("block", line, body=body)
            if w == "if":
                self.next()
                cond = self.expression()
                self.expect("kw", "then")
                then = self.parse_statement() if not self.at("kw", "else") else Node("empty", line)
                orelse = None
                if self.accept("kw", "else"):
                    orelse = self.parse_statement()
                return Node("if", line, cond=cond, then=then, orelse=orelse)
            if w == "while":
                self.next()
                cond = self.expression()
                self.expect("kw", "do")
                return Node("while", line, cond=cond, body=self.parse_statement())
            if w == "repeat":
                self.next()
                body = self.statements_until("until")
                self.expect("kw", "until")
                return Node("repeat", line, body=body, cond=self.expression())
            if w == "for":
                self.next()
                var = self.ident()
                self.expect("op", ":=")
                start = self.expression()
                down = bool(self.accept("kw", "downto"))
                if not down:
                    self.expect("kw", "to")
                stop = self.expression()
                self.expect("kw", "do")
                return Node("for", line, var=var, start=start, stop=stop, down=down,
                            body=self.parse_statement())
        if t.kind == "id":
            target = self.designator()
            if self.accept("op", ":="):
                return Node("assign", line, target=target, value=self.expression())
            return Node("call_stmt", line, call=target)
        if t.kind == "kw" and t.value in ("end", "until", "else"):
            return Node("empty", line)
        raise CompileError(f'"{t.value}" not expected here', line)

    def designator(self):
        t = self.tok
        name = self.ident()
        node = Node("name", t.line, id=name)
        while True:
            if self.at("op", "["):
                self.next()
                indices = [self.expression()]
                while self.accept("op", ","):
                    indices.append(self.expression())
                self.expect("op", "]")
                for ix in indices:
                    node = Node("index", t.line, value=node, index=ix)
            elif self.at("op", "(") and node.kind == "name":
                self.next()
                args = []
                while not self.at("op", ")"):
                    args.append(self.expression())
                    if not self.accept("op", ","):
                        break
                self.expect("op", ")")
                node = Node("call", t.line, name=name, args=args)
            elif self.at("op", ".") and self.peek().kind == "id" and self.peek().value == "length":
                self.next()
                self.next()
                node = Node("length", t.line, value=node)
            else:
                return node

    # ------------------------------------------------ expressions
    REL = ("=", "<>", "<", ">", "<=", ">=")

    def expression(self):
        left = self.simple()
        while self.at("op") and self.tok.value in self.REL:
            t = self.next()
            left = Node("compare", t.line, op=t.value, left=left, right=self.simple())
        return left

    def simple(self):
        left = self.term()
        while (self.at("op") and self.tok.value in ("+", "-")) or \
                (self.at("kw") and self.tok.value in ("or", "xor")):
            t = self.next()
            left = Node("binop", t.line, op=t.value, left=left, right=self.term())
        return left

    def term(self):
        left = self.factor()
        while (self.at("op") and self.tok.value in ("*", "/")) or \
                (self.at("kw") and self.tok.value in ("div", "mod", "and")):
            t = self.next()
            left = Node("binop", t.line, op=t.value, left=left, right=self.factor())
        return left

    def factor(self):
        t = self.tok
        if t.kind == "op" and t.value in ("-", "+"):
            self.next()
            return Node("unary", t.line, op=t.value, operand=self.factor())
        if t.kind == "kw" and t.value == "not":
            self.next()
            return Node("not", t.line, operand=self.factor())
        if t.kind == "num":
            self.next()
            return Node("num", t.line, value=t.value)
        if t.kind == "str":
            self.next()
            return Node("str", t.line, value=t.value)
        if t.kind == "query":
            self.next()
            from runtime import QUERY_CODES
            return Node("query", t.line, value=QUERY_CODES[t.value])
        if t.kind == "code":
            self.next()
            from runtime import INPUT_CODES
            return Node("num", t.line, value=INPUT_CODES[t.value])
        if t.kind == "kw" and t.value in ("true", "false"):
            self.next()
            return Node("bool_const", t.line, value=t.value == "true")
        if t.kind == "op" and t.value == "(":
            self.next()
            e = self.expression()
            self.expect("op", ")")
            return e
        if t.kind == "id":
            return self.designator()
        raise CompileError(f'"{t.value}" not expected here', t.line)

    # ------------------------------------------------ symbols
    def lookup(self, name):
        sub = self.cur
        while sub is not None:
            if name in sub.locals:
                return sub.locals[name]
            sub = sub.parent
        if name in self.globals:
            return self.globals[name]
        if name in TURTLE_VARS:
            return Var(name, INT, "turtle", self.turtle_base + 1 + TURTLE_VARS.index(name))
        return None

    def lookup_sub(self, name):
        sub = self.cur
        while sub is not None:
            if name in sub.subs:
                return sub.subs[name]
            if sub.name == name:
                return sub
            sub = sub.parent
        return self.top_subs.get(name)

    def lookup_const(self, name):
        sub = self.cur
        while sub is not None:
            if name in sub.consts:
                return sub.consts[name]
            sub = sub.parent
        if name in self.consts:
            return self.consts[name]
        if name in COLOUR_VALUES:
            return COLOUR_VALUES[name]
        return None

    def type_of(self, node):
        k = node.kind
        if k in ("num", "query"):
            return INT
        if k == "bool_const":
            return BOOL
        if k == "str":
            return CHAR if len(node.value) == 1 else STR()
        if k == "name":
            v = self.lookup(node.id)
            if v:
                return v.type
            if self.cur and self.cur.is_function and node.id in ("result", self.cur.name):
                return self.cur.rtype
            c = self.lookup_const(node.id)
            if c is not None:
                return (CHAR if len(c) == 1 else STR()) if isinstance(c, str) else INT
            sub = self.lookup_sub(node.id)
            if sub and sub.rtype:
                return sub.rtype
            b = self.builtins.get(node.id)
            if b:
                return self.builtin_type(b, node.id)
            return INT
        if k == "index":
            t = self.type_of(node.value)
            if t[0] == "str":
                return CHAR
            if t[0] == "array":
                return t[3]
            return INT
        if k == "length":
            return INT
        if k == "compare":
            return BOOL
        if k == "not":
            return self.type_of(node.operand)
        if k == "unary":
            return INT
        if k == "binop":
            lt = self.type_of(node.left)
            rt = self.type_of(node.right)
            if node.op == "+" and (lt[0] in ("str", "char") and rt[0] in ("str", "char")):
                return STR()
            if node.op in ("and", "or", "xor") and lt == BOOL:
                return BOOL
            return INT
        if k == "call":
            sub = self.lookup_sub(node.name)
            if sub:
                return sub.rtype or INT
            b = self.builtins.get(node.name)
            if b:
                return self.builtin_type(b, node.name)
            return INT
        return INT

    @staticmethod
    def builtin_type(b, name):
        r = b["returns"]
        if r == "string":
            return STR()
        if r == "char":
            return CHAR
        if r == "boolean":
            return BOOL
        return INT

    # ------------------------------------------------ code generation
    def compile(self):
        self.program()
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
        p.emit("TRUE", -1)
        for op, arg in (("HOME", None), ("LDIN", 2), ("THIK", None), ("LDIN", 360),
                        ("ANGL", None), ("LDIN", 32), ("BUFR", None), ("LDIN", 1),
                        ("SPTR", None), ("HFIX", None), ("LDIN", 0), ("DUPL", None),
                        ("LDIN", 1000), ("DUPL", None), ("DUPL", None), ("DUPL", None),
                        ("RESO", None), ("CANV", None)):
            if arg is None:
                p.emit(op)
            else:
                p.emit(op, arg)
        for v in self.global_order:
            self.init_storage(v, global_=True)
        p.memtop = self.next_global - 1
        main = Label()
        if self.subs_all:
            p.newline()
            p.emit_jump("JUMP", main)
            # nested routines are declared, and so compiled, before their parent's body
            def post_order(subs):
                for sub in subs:
                    yield from post_order(sub.children)
                    yield sub
            for sub in post_order([s for s in self.subs_all if s.parent is None]):
                self.compile_sub(sub)
        p.place(main)
        self.cur = None
        self.body(self.main)
        p.newline()
        p.emit("HALT")
        p.finish()
        return p

    def init_storage(self, v, global_=False, f=None):
        """Set up the pointers of a string or array variable (one line per pointer)."""
        t = v.type
        if t[0] not in ("str", "array"):
            return
        p = self.p
        if global_:
            def lda(a):
                p.emit("LDAG", a)

            def stv(a):
                p.emit("STVG", a)
        else:
            def lda(a):
                p.emit("LDAV", f, a)

            def stv(a):
                p.emit("STVV", f, a)
        self._init_at(v.addr, t, lda, stv)

    def _init_at(self, ptr, t, lda, stv):
        """ptr: the pointer cell; storage follows it."""
        p = self.p
        if t[0] == "str":
            p.newline()
            lda(ptr + 2)
            stv(ptr)
            p.emit("LDIN", t[1] + 1)
            stv(ptr + 1)
            return ptr + 3 + t[1]
        n = t[2] - t[1] + 1
        p.newline()
        lda(ptr + 1)
        stv(ptr)
        p.emit("LDIN", n)
        stv(ptr + 1)
        elem = t[3]
        nxt = ptr + 2 + n
        if elem[0] in ("str", "array"):
            for k in range(n):
                slot = ptr + 2 + k
                if elem[0] == "str":
                    p.newline()
                    lda(nxt + 1)
                    stv(slot)
                    p.emit("LDIN", elem[1] + 1)
                    stv(nxt)
                    nxt += 2 + elem[1]
                else:
                    nxt = self._init_at_row(slot, nxt, elem, lda, stv)
        return nxt

    def _init_at_row(self, slot, addr, t, lda, stv):
        """A row of an array of arrays: its length cell is at addr, pointed to by slot."""
        p = self.p
        n = t[2] - t[1] + 1
        p.newline()
        lda(addr)
        stv(slot)
        p.emit("LDIN", n)
        stv(addr)
        nxt = addr + 1 + n
        elem = t[3]
        if elem[0] in ("str", "array"):
            for k in range(n):
                s = addr + 1 + k
                if elem[0] == "str":
                    p.newline()
                    lda(nxt + 1)
                    stv(s)
                    p.emit("LDIN", elem[1] + 1)
                    stv(nxt)
                    nxt += 2 + elem[1]
                else:
                    nxt = self._init_at_row(s, nxt, elem, lda, stv)
        return nxt

    def compile_sub(self, sub):
        p = self.p
        outer = self.cur
        self.cur = sub
        outer_loops, self.loops = self.loops, []
        p.place(sub.entry)
        p.mark_source(sub.line)
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
            if sub.is_function and is_str(sub.rtype):
                p.emit("LDAV", f, 3)
                p.emit("STVV", f, 1)
                p.emit("LDIN", sub.rtype[1] + 1)
                p.emit("STVV", f, 2)
            for v in reversed(sub.params):
                if v.byref or not is_str(v.type):
                    p.emit("STVV", f, v.addr)
                else:
                    p.emit("LDAV", f, v.addr + 1)
                    p.emit("DUPL")
                    p.emit("DUPL")
                    p.emit("INCR")
                    p.emit("STVV", f, v.addr)
                    p.emit("LDIN", v.type[1])
                    p.emit("SWAP")
                    p.emit("SPTR")
                    p.emit("INCR")
                    p.emit("CSTR")
            for v in sub.locals.values():
                if v.scope == "local":
                    self.init_storage(v, f=f)
            pending = sub.is_function and is_str(sub.rtype)
        body = list(sub.body)
        if pending and body:
            first = body[0]
            if first.kind in ("assign", "call_stmt", "fn_result"):
                self.statement(first)
                line = p.lines[-1]
                if not line or line[-1] != CODE_HCLR:
                    p.emit("HCLR")
                body = body[1:]
            else:
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

    def frame_of(self, v):
        return 14 + v.sub.index

    def load_var(self, v):
        if v.scope in ("global", "turtle"):
            self.p.emit("LDVG", v.addr)
        elif v.byref and not is_str(v.type) and v.type[0] != "array":
            self.p.emit("LDVR", self.frame_of(v), v.addr)
        else:
            self.p.emit("LDVV", self.frame_of(v), v.addr)

    def store_var(self, v):
        if v.scope in ("global", "turtle"):
            self.p.emit("STVG", v.addr)
        elif v.byref:
            self.p.emit("STVR", self.frame_of(v), v.addr)
        else:
            self.p.emit("STVV", self.frame_of(v), v.addr)

    def load_addr(self, v):
        if v.scope in ("global", "turtle"):
            self.p.emit("LDAG", v.addr)
        elif v.byref:
            self.p.emit("LDVV", self.frame_of(v), v.addr)
        else:
            self.p.emit("LDAV", self.frame_of(v), v.addr)

    # ------------------------------------------------ statements
    def body(self, stmts):
        """A statement sequence: each statement releases the heap strings it used."""
        outer = self.tail_calls
        for st in stmts:
            self.p.heap = False
            self.tail_calls = outer + self._tails(st)
            self.statement(st)
            if self.p.heap and st.kind not in ("block", "empty"):
                self.p.emit("HCLR")
            self.p.heap = False
        self.tail_calls = outer

    def statement(self, st):
        if st.kind == "empty":
            return
        if st.kind == "block":
            self.body(st.body)
            return
        self.p.newline()
        self.p.mark_source(st.line)
        getattr(self, "st_" + st.kind)(st)

    def st_assign(self, st):
        p = self.p
        target = st.target
        if target.kind == "name":
            if target.id == "result" or (self.cur and target.id == self.cur.name and
                                         self.cur.is_function):
                sub = self.cur
                v = sub.result
                if is_str(v.type):
                    p.emit("LDVV", 14 + sub.index, 1)
                    self.expr_as_str(st.value)
                    p.emit("SWAP")
                    p.emit("CSTR")
                    self._release()
                else:
                    self.expr(st.value)
                    p.emit("STVV", 14 + sub.index, 1)
                    self._release()
                return
            v = self.lookup(target.id)
            if v is None:
                raise CompileError(f'"{target.id}" not recognised', st.line)
            if is_str(v.type):
                self.load_var(v)
                self.expr_as_str(st.value)
                p.emit("SWAP")
                p.emit("CSTR")
                self._release()
                return
            self.expr(st.value)
            self.store_var(v)
            self._release()
            return
        if target.kind == "index":
            t = self.type_of(target)
            if is_str(t):
                self.element_address(target)
                p.emit("LPTR")
                self.expr_as_str(st.value)
                p.emit("SWAP")
                p.emit("CSTR")
                self._release()
                return
            self.element_address(target)
            self.expr(st.value)
            p.emit("SWAP")
            p.emit("SPTR")
            self._release()
            return
        raise CompileError("Cannot assign to this", st.line)

    def _release(self):
        if self.p.heap:
            self.p.emit("HCLR")
            self.p.heap = False

    tail_calls = ()
    current_call_stmt = None

    def _tails(self, st):
        """Call statements that end a statement of a sequence (where its HCLR goes)."""
        if st.kind == "call_stmt":
            return (st,)
        if st.kind == "if":
            return self._tails(st.orelse if st.orelse is not None else st.then)
        return ()

    def st_call_stmt(self, st):
        self.current_call_stmt = st
        call = st.call
        if call.kind == "name":
            call = Node("call", call.line, name=call.id, args=[])
        if call.kind != "call":
            raise CompileError("Statement expected", st.line)
        t = self.call(call, statement=True)

    def st_if(self, st):
        p = self.p
        orelse = Label()
        self.condition(st.cond)
        p.emit_jump("IFNO", orelse)
        self.statement(st.then)
        if st.orelse is not None:
            end = Label()
            p.newline()
            if p.heap and st.orelse.kind == "block":
                p.emit("HCLR")
                p.heap = False
            p.emit_jump("JUMP", end)
            p.place(orelse)
            self.statement(st.orelse)
            p.place(end)
        else:
            p.place(orelse)

    def st_while(self, st):
        p = self.p
        top = Label()
        end = Label()
        p.place(top)
        self.condition(st.cond)
        p.emit_jump("IFNO", end)
        self.loops.append((top, end, 0))
        self.statement(st.body)
        self.loops.pop()
        p.newline()
        p.emit_jump("JUMP", top)
        p.place(end)

    def st_repeat(self, st):
        p = self.p
        top = Label()
        end = Label()
        p.place(top)
        self.loops.append((top, end, 0))
        self.body(st.body)
        self.loops.pop()
        p.newline()
        self.condition(st.cond)
        p.emit_jump("IFNO", top)
        p.place(end)

    def st_for(self, st):
        p = self.p
        v = self.lookup(st.var)
        if v is None:
            raise CompileError(f'"{st.var}" not recognised', st.line)
        self.expr(st.start)
        self.expr(st.stop)
        p.emit("SWAP")
        top = Label()
        end = Label()
        cont = Label()
        p.place(top)
        p.emit("DUPL")
        self.store_var(v)
        p.emit("PICK", 2)
        p.emit("MREQ" if st.down else "LSEQ")
        if self.pending_hclr:
            p.emit("HCLR")
            self.pending_hclr = False
        p.emit_jump("IFNO", end)
        self.loops.append((cont, end, 1))
        self.statement(st.body)
        self.loops.pop()
        p.place(cont)
        self.load_var(v)
        p.emit("DECR" if st.down else "INCR")
        p.emit_jump("JUMP", top)
        p.place(end)
        p.emit("DROP")

    # ------------------------------------------------ expressions
    def uses_strings(self, node):
        """Whether evaluating the expression puts a temporary string on the heap."""
        stack = [node]
        while stack:
            n = stack.pop()
            k = n.kind
            if k == "str":
                return True
            if k == "binop" and n.op == "+" and is_str(self.type_of(n)):
                return True
            if k == "call" and self.type_of(n)[0] in ("str",):
                return True
            for v in n.__dict__.values():
                if isinstance(v, Node):
                    stack.append(v)
                elif isinstance(v, list):
                    stack.extend(x for x in v if isinstance(x, Node))
        return False

    def condition(self, node):
        self.expr(node)
        if self.p.heap or self.pending_hclr:
            self.p.emit("HCLR")
        self.pending_hclr = False
        self.p.heap = False

    def expr_as_str(self, node):
        """Evaluate as a string: characters become one-character strings."""
        t = self.expr(node)
        if t == CHAR:
            self.p.emit("CTOS")
        return STR()

    def expr(self, node):
        return getattr(self, "ex_" + node.kind)(node)

    def ex_num(self, node):
        self.p.emit("LDIN", node.value)
        return INT

    def ex_query(self, node):
        self.p.emit("LDIN", node.value)
        self.p.emit("STAT")
        return INT

    def ex_bool_const(self, node):
        self.p.emit("LDIN", -1 if node.value else 0)
        return BOOL

    def ex_str(self, node):
        if len(node.value) == 1:
            self.p.emit("LDIN", ord(node.value))
            return CHAR
        self.p.emit_str(node.value)
        return STR()

    def ex_name(self, node):
        name = node.id
        v = self.lookup(name)
        if v is not None:
            if v.type[0] == "array":
                self.load_var(v)
            else:
                self.load_var(v)
            return v.type
        c = self.lookup_const(name)
        if c is not None:
            if isinstance(c, str):
                return self.ex_str(Node("str", node.line, value=c))
            self.p.emit("LDIN", c)
            return INT
        if self.lookup_sub(name) or name in self.builtins:
            return self.call(Node("call", node.line, name=name, args=[]))
        if name == "result" and self.cur and self.cur.is_function:
            self.p.emit("LDVV", 14 + self.cur.index, 1)
            return self.cur.rtype
        raise CompileError(f'"{name}" not recognised', node.line)

    def ex_unary(self, node):
        self.expr(node.operand)
        if node.op == "-":
            self.p.emit("NEG")
        return INT

    def ex_not(self, node):
        t = self.expr(node.operand)
        self.p.emit("NOT")
        return t

    _CMP = {"=": "EQAL", "<>": "NOEQ", "<": "LESS", ">": "MORE", "<=": "LSEQ", ">=": "MREQ"}
    _SCMP = {"=": "SEQL", "<>": "SNEQ", "<": "SLES", ">": "SMOR", "<=": "SLEQ", ">=": "SMEQ"}

    def ex_compare(self, node):
        lt = self.type_of(node.left)
        rt = self.type_of(node.right)
        if is_str(lt) or is_str(rt):
            self.expr_as_str(node.left)
            self.expr_as_str(node.right)
            self.p.emit(self._SCMP[node.op])
        else:
            self.expr(node.left)
            self.expr(node.right)
            self.p.emit(self._CMP[node.op])
        return BOOL

    _BIN = {"+": "PLUS", "-": "SUBT", "*": "MULT", "/": "DIVR", "div": "DIV", "mod": "MOD",
            "and": "AND", "or": "OR", "xor": "XOR"}

    def ex_binop(self, node):
        p = self.p
        lt = self.type_of(node.left)
        rt = self.type_of(node.right)
        if node.op == "+" and (is_str(lt) or is_str(rt) or (lt == CHAR and rt == CHAR)):
            self.expr_as_str(node.left)
            self.expr_as_str(node.right)
            p.emit("SCAT")
            return STR()
        self.expr(node.left)
        self.expr(node.right)
        p.emit(self._BIN[node.op])
        return BOOL if node.op in ("and", "or", "xor") and lt == BOOL else INT

    def element_address(self, target):
        """Push the address of an array element or string character."""
        p = self.p
        base = target.value
        bt = self.type_of(base)
        if base.kind == "name" and self.lookup(base.id) is None:
            c = self.lookup_const(base.id)        # a constant string: LSTR, index - 1
            if isinstance(c, str):
                p.emit_str(c)
                self.expr(target.index)
                p.emit("DECR")
                p.emit("SWAP")
                p.emit("TEST")
                p.emit("PLUS")
                p.emit("INCR")
                return
        if base.kind == "name":
            v = self.lookup(base.id)
            if v is None:
                raise CompileError(f'"{base.id}" not recognised', target.line)
            self.load_var(v)
        elif base.kind == "index":
            self.element_address(base)
            p.emit("LPTR")
        else:
            self.expr(base)
        self.expr(target.index)
        lo = bt[1] if bt[0] == "array" else 1
        if lo:
            p.emit("LDIN", lo)
            p.emit("SUBT")
        p.emit("SWAP")
        p.emit("TEST")
        p.emit("PLUS")
        p.emit("INCR")

    def ex_index(self, node):
        self.element_address(node)
        self.p.emit("LPTR")
        bt = self.type_of(node.value)
        if bt[0] == "str":
            return CHAR
        return bt[3]

    def ex_length(self, node):
        t = self.expr(node.value)
        self.p.emit("LPTR" if t[0] == "array" else "SLEN")
        return INT

    def ex_call(self, node):
        return self.call(node)

    # ------------------------------------------------ calls
    def call(self, node, statement=False):
        p = self.p
        name = node.name
        sub = self.lookup_sub(name)
        if sub is not None:
            if not statement:
                p.newline()
            if len(node.args) != len(sub.params):
                raise CompileError(f"{name} takes {len(sub.params)} parameters", node.line)
            for a, prm in zip(node.args, sub.params):
                if prm.byref:
                    self.var_argument(a)
                elif is_str(prm.type):
                    self.expr_as_str(a)
                else:
                    self.expr(a)
            if statement and p.heap and self.current_call_stmt in self.tail_calls:
                p.emit("HCLR")
                p.heap = False
            p.emit_jump("SUBR", sub.entry)
            p.newline()
            if sub.is_function:
                p.emit("LDVV", self.fnbase, 1)
                if is_str(sub.rtype):
                    p.emit("HSTR")
                if statement:
                    p.newline()
                    p.emit("DROP")
                return sub.rtype
            return None
        handler = getattr(self, self.handler_name(name), None)
        if handler:
            r = handler(node)
        else:
            b = self.builtins.get(name)
            if b is None:
                raise CompileError(f'"{name}" not recognised', node.line)
            for a, kind in zip(node.args, b["signature"]):
                if kind == "s":
                    self.expr_as_str(a)
                else:
                    self.expr(a)
            p.emit(OPS[b["opcode"]][0])
            r = self.builtin_type(b, name) if b["returns"] != "procedure" else None
        if r is not None and statement:
            p.newline()
            p.emit("DROP")
        return r

    def var_argument(self, a):
        if a.kind == "name":
            v = self.lookup(a.id)
            if v is None:
                raise CompileError(f'"{a.id}" not recognised', a.line)
            if is_str(v.type) or v.type[0] == "array":
                self.load_var(v)
            else:
                self.load_addr(v)
        elif a.kind == "index":
            self.element_address(a)
            if self.type_of(a)[0] == "array":       # a row of an array: its address
                self.p.emit("LPTR")
        else:
            raise CompileError("Variable expected", a.line)

    def _simple(self, node, *ops):
        for a in node.args:
            self.expr(a)
        for op in ops:
            if isinstance(op, tuple):
                self.p.emit(*op)
            else:
                self.p.emit(op)

    def bi_write(self, node):
        for a in node.args:
            t = self.expr(a)
            if t == CHAR:
                self.p.emit("CTOS")
            elif not is_str(t):
                self.p.emit("ITOS")
            self.p.emit("WRIT")
        return None

    def bi_writeln(self, node):
        self.bi_write(node)
        self.p.emit("NEWL")
        return None

    def bi_str(self, node):
        self.expr(node.args[0])
        self.p.emit("ITOS")
        return STR()

    def bi_sqrt(self, node):
        self._simple(node, ("LDIN", 2), "SWAP", "ROOT")
        return INT

    def bi_valdef(self, node):
        self.expr_as_str(node.args[0])
        self.expr(node.args[1])
        self.p.emit("LDIN", 1)
        self.p.emit("SVDF")
        return INT

    def bi_val(self, node):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", 0)
        self.p.emit("LDIN", 1)
        self.p.emit("SVDF")
        return INT

    def _var_param_string(self, node, op):
        p = self.p
        self.expr_as_str(node.args[0])
        self.var_argument(node.args[1])
        p.emit("DUPL")
        p.emit("LPTR")
        p.emit("ROTA")
        p.emit(op)
        p.emit("SWAP")
        p.emit("ROTA")
        p.emit("SPTR")
        return STR()

    def bi_findfirst(self, node):
        return self._var_param_string(node, "FFND")

    def bi_finddir(self, node):
        return self._var_param_string(node, "FDIR")

    def bi_length(self, node):
        t = self.expr(node.args[0])
        self.p.emit("LPTR" if t[0] == "array" else "SLEN")
        return INT

    def bi_color(self, node):
        self._simple(node, "COLR")
        return None

    def bi_newturtle(self, node):
        self._simple(node, ("LDIN", 0), "SPTR")
        return None

    def bi_oldturtle(self, node):
        self.p.emit("LDIN", self.turtle_base)
        self.p.emit("LDIN", 0)
        self.p.emit("SPTR")
        return None

    def _file_test(self, node, op, code, cmp, limit):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", code)
        self.p.emit(op)
        self.p.emit("LDIN", limit)
        self.p.emit(cmp)
        return BOOL

    def bi_fileexists(self, node):
        return self._file_test(node, "FILE", 0, "MORE", 127)

    def bi_deletefile(self, node):
        return self._file_test(node, "FILE", 1, "LESS", 128)

    def bi_direxists(self, node):
        return self._file_test(node, "DIRY", 0, "MORE", 127)

    def bi_rmdir(self, node):
        return self._file_test(node, "DIRY", 1, "LESS", 128)

    def bi_mkdir(self, node):
        return self._file_test(node, "DIRY", 2, "MORE", 127)

    def _fmov(self, node, mode):
        self.expr_as_str(node.args[0])
        self.expr_as_str(node.args[1])
        self.p.emit("LDIN", mode)
        self.p.emit("FMOV")
        return BOOL

    def bi_renamefile(self, node):
        return self._fmov(node, 1)

    def bi_movefile(self, node):
        return self._fmov(node, 2)

    def bi_copyfile(self, node):
        return self._fmov(node, 3)

    def bi_chr(self, node):
        self.expr(node.args[0])
        return CHAR

    def bi_ord(self, node):
        t = self.expr(node.args[0])
        if is_str(t):
            self.p.emit("CTST")
            self.p.emit("INCR")
            self.p.emit("LPTR")
        return INT

    def bi_randcol(self, node):
        self._simple(node, "RAND", "INCR", "RGB", "COLR")
        return None

    def bi_penup(self, node):
        self.p.emit("LDIN", 0)
        self.p.emit("PEN")
        return None

    def bi_pendown(self, node):
        self.p.emit("LDIN", -1)
        self.p.emit("PEN")
        return None

    def bi_noupdate(self, node):
        self.p.emit("LDIN", 0)
        self.p.emit("UDAT")
        return None

    def bi_update(self, node):
        self.p.emit("LDIN", -1)
        self.p.emit("UDAT")
        return None

    def bi_random(self, node):
        self._simple(node, "RAND")
        return INT

    def bi_inc(self, node):
        return self._incdec(node, "INCR")

    def bi_dec(self, node):
        return self._incdec(node, "DECR")

    def _incdec(self, node, op):
        p = self.p
        self.var_argument(node.args[0])
        p.emit("DUPL")
        p.emit("LPTR")
        p.emit(op)
        p.emit("SWAP")
        p.emit("SPTR")
        return None

    def bi_lowercase(self, node):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", 1)
        self.p.emit("CASE")
        return STR()

    def bi_uppercase(self, node):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", 2)
        self.p.emit("CASE")
        return STR()

    def bi_initcap(self, node):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", 3)
        self.p.emit("CASE")
        return STR()

    def bi_titlecase(self, node):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", 4)
        self.p.emit("CASE")
        return STR()

    def bi_swapcase(self, node):
        self.expr_as_str(node.args[0])
        self.p.emit("LDIN", 5)
        self.p.emit("CASE")
        return STR()

    def bi_exit(self, node):
        if self.cur is None:
            self.p.emit("HALT")
        else:
            self.p.emit_jump("JUMP", self.cur.exit)
        return None


def compile_pascal(src):
    c = Compiler(src)
    return c.compile(), c
