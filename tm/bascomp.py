"""Turtle BASIC to Turtle Machine PCode.

The original generates nearly the same code for BASIC as for Pascal, so this compiler reuses
the Pascal code generator (tm.pascomp) with a BASIC parser: variables are typed by their suffix
(% integer, $ string) and allocated where they first appear, DIM arrays are indexed from 0,
DEF PROC / DEF FN subroutines follow END, with LOCAL variables and RETURN (by reference)
parameters, and a function's result is given by a final "=expression" line.
"""

from colours import COLOURS
from lang_basic import tokenize as basic_tokenize
from runtime import INPUT_CODES, QUERY_CODES
from tm import pascomp as P
from tm.pascomp import INT, STR, CompileError, Node, Sub, Var, is_str

BUILTINS = {b["names"]["basic"]: b for b in P._BUILTINS if b["names"]["basic"]}
BUILTINS["KEYSTATUS"] = BUILTINS["STATUS"]
COLOUR_CONSTS = {name.upper(): value for name, value in COLOURS}




def var_type(name, size=P.STRING_SIZE):
    return STR(size) if name.endswith("$") else INT


class BasicProgram(P.TrackingProgram):
    HEAP_OPS = P.TrackingProgram.HEAP_OPS | {"SCAT"}


class Compiler(P.Compiler):
    builtins = BUILTINS
    LANG_CODE = 2

    string_size = P.STRING_SIZE

    def var_type(self, name):
        return var_type(name, self.string_size)

    @staticmethod
    def handler_name(name):
        return "bi_" + name.lower().replace("$", "_s").replace("#", "_h")

    def __init__(self, src):
        try:
            self.toks = basic_tokenize(src)
        except Exception as e:  # noqa: BLE001
            raise CompileError(str(e), getattr(e, "line", 0)) from None
        self.src = src
        self.i = 0
        self.p = BasicProgram()
        self.globals = {}
        self.consts = dict(COLOUR_CONSTS)
        self.consts.update({"TRUE": -1, "FALSE": 0})
        self.subs_all = []
        self.top_subs = {}
        self.cur = None
        self.cur_decl = None
        self.pending_hclr = False
        self.loops = []
        names = [self.toks[k + 1].value for k, t in enumerate(self.toks[:-1])
                 if t.kind == "kw" and t.value == "DEF" and self.toks[k + 1].kind == "proc"]
        self.nsubs = len(names)
        nfuncs = sum(1 for n in names if n.startswith("FN"))
        self.turtle_base = 15 + self.nsubs + (1 if nfuncs else 0)
        self.fnbase = 15 + self.nsubs
        self.next_global = self.turtle_base + 7
        self.global_order = []
        # subroutines are known before their definitions (calls precede DEF)
        for k, name in enumerate(names, 1):
            sub = Sub(name, k, 0)
            if name.startswith("FN"):
                sub.rtype = self.var_type(name)
            self.top_subs[name] = sub
            self.subs_all.append(sub)

    # ------------------------------------------------ helpers
    def at_nl(self):
        return self.at("nl") or self.at("eof")

    def skip_nl(self):
        while self.accept("nl"):
            pass

    def ident(self):
        t = self.tok
        if t.kind != "id":
            raise CompileError("Identifier expected", t.line)
        self.next()
        return t.value

    def note_var(self, name, line, array_type=None):
        """A variable is allocated where it first appears (unless local or constant)."""
        if name in self.consts or name in self.builtins:
            return
        sub = self.cur_decl
        if sub is not None and name in sub.locals:
            return
        if name.lower() in P.TURTLE_VARS or name.rstrip("%").lower() in P.TURTLE_VARS:
            return
        if name not in self.globals:
            self.declare_var(name, array_type or self.var_type(name), None, line)

    def lookup(self, name):
        sub = self.cur
        if sub is not None and name in sub.locals:
            return sub.locals[name]
        if name in self.globals:
            return self.globals[name]
        base = name.rstrip("%").lower()
        if base in P.TURTLE_VARS:
            return Var(name, INT, "turtle", self.turtle_base + 1 + P.TURTLE_VARS.index(base))
        return None

    def lookup_const(self, name):
        return self.consts.get(name)

    def lookup_sub(self, name):
        return self.top_subs.get(name)

    def type_of(self, node):
        if node.kind == "str":
            return STR()
        if node.kind == "name" and node.id in self.consts:
            return STR() if isinstance(self.consts[node.id], str) else INT
        return super().type_of(node)

    # ------------------------------------------------ parsing
    def program(self):
        self.read_vars = []
        self.main = []
        self.skip_nl()
        while not self.at("eof"):
            if self.at("kw", "DEF"):
                self.definition()
            elif self.at("kw", "END") and self.cur_decl is None and not self.main_done():
                self.next()
                self.main_ended = True
            else:
                st = self.parse_statement()
                if st is not None:
                    self.main.append(st)
            self.skip_nl()
        for name, line in self.read_vars:       # read but never assigned
            if name not in self.globals and not any(name in sb.locals for sb in self.subs_all):
                self.note_var(name, line)

    main_ended = False

    def main_done(self):
        return self.main_ended

    def block(self, *enders):
        body = []
        self.skip_nl()
        while not (self.at("kw") and self.tok.value in enders) and not self.at("eof"):
            if self.at("op", "=") and "=" in enders:
                break
            st = self.parse_statement()
            if st is not None:
                body.append(st)
            self.skip_nl()
        return body

    def definition(self):
        line = self.next().line
        name = self.next().value
        sub = self.top_subs[name]
        sub.line = line
        self.cur_decl = sub
        sub.size = P.type_size(sub.rtype) if sub.rtype else 0
        if sub.rtype:
            sub.result = Var("result", sub.rtype, "result", 1, sub)
        if self.accept("op", "("):
            while not self.at("op", ")"):
                byref = bool(self.accept("kw", "RETURN"))
                pname = self.ident()
                t = self.var_type(pname)
                if self.at("op", "(") and self.peek().kind == "op" and self.peek().value == ")":
                    self.next()
                    self.next()
                    t = ("array", 0, -1, t)
                v = Var(pname, t, "param", sub.size + 1, sub, byref)
                sub.size += 1 if (byref or t[0] == "array") else P.type_size(t)
                sub.locals[pname] = v
                sub.params.append(v)
                if not self.accept("op", ","):
                    break
            self.expect("op", ")")
        if sub.rtype:
            sub.body = self.block("=")
            t = self.expect("op", "=")
            sub.body.append(Node("fn_result", t.line, value=self.expression()))
        else:
            sub.body = self.block("ENDPROC")
            self.expect("kw", "ENDPROC")
        sub.end_line = self.tok.line
        self.cur_decl = None

    def parse_statement(self):
        t = self.tok
        line = t.line
        if t.kind == "kw":
            w = t.value
            if w == "IF":
                return self.if_statement()
            if w == "FOR":
                self.next()
                var = self.ident()
                self.note_var(var, line)
                self.expect("op", "=")
                start = self.expression()
                self.expect("kw", "TO")
                stop = self.expression()
                step = None
                if self.accept("kw", "STEP"):
                    step = self.const_expr()
                body = self.block("NEXT")
                self.expect("kw", "NEXT")
                if self.at("id"):
                    self.next()
                return Node("for", line, var=var, start=start, stop=stop, step=step,
                            down=step is not None and step < 0,
                            body=Node("block", line, body=body))
            if w == "WHILE":
                self.next()
                cond = self.expression()
                body = self.block("ENDWHILE")
                self.expect("kw", "ENDWHILE")
                return Node("while", line, cond=cond, body=Node("block", line, body=body))
            if w == "REPEAT":
                self.next()
                body = self.block("UNTIL")
                self.expect("kw", "UNTIL")
                return Node("repeat", line, body=body, cond=self.expression())
            if w in ("LOCAL", "PRIVATE"):
                self.next()
                sub = self.cur_decl
                while True:
                    name = self.ident()
                    t2 = self.var_type(name)
                    if sub is not None:
                        v = Var(name, t2, "local", sub.size + 1, sub)
                        sub.size += P.type_size(t2)
                        sub.locals[name] = v
                    if not self.accept("op", ","):
                        break
                return None
            if w == "DIM":
                self.next()
                while True:
                    name = self.ident()
                    self.expect("op", "(")
                    bounds = [self.const_expr()]
                    while self.accept("op", ","):
                        bounds.append(self.const_expr())
                    self.expect("op", ")")
                    t2 = self.var_type(name)
                    for b in reversed(bounds):
                        t2 = ("array", 0, b, t2)
                    sub = self.cur_decl
                    if sub is not None:
                        v = Var(name, t2, "local", sub.size + 1, sub)
                        sub.size += P.type_size(t2)
                        sub.locals[name] = v
                    else:
                        self.note_var(name, line, t2)
                    if not self.accept("op", ","):
                        break
                return None
            if w == "CONST":
                self.next()
                name = self.ident()
                self.expect("op", "=")
                self.consts[name] = self.const_expr()
                return None
            if w == "END":
                self.next()
                return Node("halt", line)
            if w == "STRINGSIZE":
                self.next()
                self.string_size = self.const_expr()
                return None
        if t.kind == "op" and t.value == "=":
            return None
        if t.kind == "proc":
            return Node("call_stmt", line, call=self.proc_call())
        if t.kind == "id":
            name = t.value
            if name in self.builtins or name in ("INC", "DEC"):
                return Node("call_stmt", line, call=self.builtin_call())
            target = self.designator(store=True)
            self.expect("op", "=")
            return Node("assign", line, target=target, value=self.expression())
        raise CompileError(f'"{t.value}" not expected here', line)

    def if_statement(self):
        line = self.next().line
        cond = self.expression()
        self.expect("kw", "THEN")
        if self.at_nl():
            then = self.block("ELSE", "ENDIF")
            orelse = None
            if self.accept("kw", "ELSE"):
                if self.at("kw", "IF"):
                    orelse = Node("block", line, body=[self.if_statement()])
                    return Node("if", line, cond=cond, then=Node("block", line, body=then),
                                orelse=orelse)
                orelse = Node("block", line, body=self.block("ENDIF"))
            self.expect("kw", "ENDIF")
            return Node("if", line, cond=cond, then=Node("block", line, body=then),
                        orelse=orelse)
        then = self.parse_statement()
        orelse = None
        if self.accept("kw", "ELSE"):
            orelse = self.parse_statement()
        return Node("if", line, cond=cond, then=then or Node("empty", line), orelse=orelse)

    def designator(self, store=False):
        t = self.tok
        name = self.ident()
        if store:                     # variables are allocated where first assigned
            self.note_var(name, t.line)
        else:
            self.read_vars.append((name, t.line))
        node = Node("name", t.line, id=name)
        if self.at("op", "("):
            self.next()
            indices = [self.expression()]
            while self.accept("op", ","):
                indices.append(self.expression())
            self.expect("op", ")")
            for ix in indices:
                node = Node("index", t.line, value=node, index=ix)
        return node

    def args(self):
        args = []
        if self.accept("op", "("):
            while not self.at("op", ")"):
                args.append(self.expression())
                if not self.accept("op", ","):
                    break
            self.expect("op", ")")
        return args

    def proc_call(self):
        t = self.next()
        if t.value not in self.top_subs:
            raise CompileError(f'"{t.value}" not defined', t.line)
        return Node("call", t.line, name=t.value, args=self.args())

    def builtin_call(self):
        t = self.next()
        args = self.args()
        b = self.builtins.get(t.value)
        if b and "I" in b["signature"]:          # INC(x%) etc. assign their variable
            for a, kind in zip(args, b["signature"]):
                if kind == "I" and a.kind == "name":
                    self.note_var(a.id, t.line)
        return Node("call", t.line, name=t.value, args=args)

    # expressions as in Pascal: comparisons loosest, then + - OR EOR, then * / DIV MOD AND
    def expression(self):
        left = self.additive()
        while self.at("op") and self.tok.value in self.REL:
            t = self.next()
            left = Node("compare", t.line, op=t.value, left=left, right=self.additive())
        return left

    def additive(self):
        left = self.term()
        while (self.at("op") and self.tok.value in ("+", "-")) or \
                (self.at("kw") and self.tok.value in ("OR", "EOR")):
            t = self.next()
            op = {"OR": "or", "EOR": "xor"}.get(t.value, t.value)
            left = Node("binop", t.line, op=op, left=left, right=self.term())
        return left

    def term(self):
        left = self.factor()
        while (self.at("op") and self.tok.value in ("*", "/")) or \
                (self.at("kw") and self.tok.value in ("DIV", "MOD", "AND")):
            t = self.next()
            op = t.value.lower() if t.kind == "kw" else t.value
            left = Node("binop", t.line, op=op, left=left, right=self.factor())
        return left

    def factor(self):
        t = self.tok
        if t.kind == "op" and t.value in ("-", "+"):
            self.next()
            return Node("unary", t.line, op=t.value, operand=self.factor())
        if t.kind == "kw" and t.value == "NOT":
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
            if t.value not in QUERY_CODES:
                raise CompileError(f'"?{t.value}" not recognised', t.line)
            return Node("query", t.line, value=QUERY_CODES[t.value])
        if t.kind == "code":
            self.next()
            if t.value not in INPUT_CODES:
                raise CompileError(f'"\\{t.value}" not recognised', t.line)
            return Node("num", t.line, value=INPUT_CODES[t.value])
        if t.kind == "kw" and t.value in ("TRUE", "FALSE"):
            self.next()
            return Node("bool_const", t.line, value=t.value == "TRUE")
        if t.kind == "op" and t.value == "(":
            self.next()
            e = self.expression()
            self.expect("op", ")")
            return e
        if t.kind == "proc":
            return self.proc_call()
        if t.kind == "id":
            if t.value in self.consts:
                self.next()
                return Node("name", t.line, id=t.value)
            if t.value in self.builtins:
                return self.builtin_call()
            return self.designator()
        raise CompileError(f'"{t.value}" not expected here', t.line)

    def const_expr(self):
        node = self.expression()
        v = self.fold(node)
        if v is None:
            raise CompileError("Constant expected", node.line)
        return v

    def find_const(self, name):
        return self.consts.get(name)

    # ------------------------------------------------ BASIC-specific code
    def ex_str(self, node):
        self.p.emit_str(node.value)
        return STR()

    def ex_bool_const(self, node):
        self.p.emit("LDIN", -1 if node.value else 0)
        return INT

    def st_halt(self, st):
        self.p.emit("HALT")

    def st_fn_result(self, st):
        sub = self.cur
        p = self.p
        if is_str(sub.rtype):
            p.emit("LDVV", 14 + sub.index, 1)
            self.expr_as_str(st.value)
            p.emit("SWAP")
            p.emit("CSTR")
        else:
            self.expr(st.value)
            p.emit("STVV", 14 + sub.index, 1)
        self._release()

    def st_for(self, st):
        if st.step is None or st.step in (1, -1):
            return super().st_for(st)
        p = self.p
        v = self.lookup(st.var)
        self.expr(st.start)
        self.expr(st.stop)
        p.emit("SWAP")
        top = P.Label()
        end = P.Label()
        p.place(top)
        p.emit("DUPL")
        self.store_var(v)
        p.emit("PICK", 2)
        p.emit("MREQ" if st.step < 0 else "LSEQ")
        p.emit_jump("IFNO", end)
        self.statement(st.body)
        p.newline()
        self.load_var(v)
        p.emit("LDIN", abs(st.step))
        p.emit("SUBT" if st.step < 0 else "PLUS")
        p.emit_jump("JUMP", top)
        p.place(end)
        p.emit("DROP")

    # builtins whose BASIC form differs from the machine's operand order
    def bi_openin(self, node):
        self._simple(node, ("LDIN", 1), "OPEN")
        return INT

    def bi_openup(self, node):
        self._simple(node, ("LDIN", 2), "OPEN")
        return INT

    def bi_openout(self, node):
        self._simple(node, ("LDIN", 3), "OPEN")
        return INT

    def bi_print(self, node):
        for a in node.args:
            self.expr_as_str(a)
            self.p.emit("WRIT")
        self.p.emit("NEWL")
        return None

    def bi_printon(self, node):
        for a in node.args:
            self.expr_as_str(a)
            self.p.emit("WRIT")
        return None

    def bi_insert_s(self, node):
        self._simple(node, "ROTA", "ROTA", "INSS")
        return STR()

    def bi_instr(self, node):
        self._simple(node, "SWAP", "POSS")
        return INT

    def bi_mid_s(self, node):
        if len(node.args) == 2:
            self._simple(node, "MXIN", "COPY")
        else:
            self._simple(node, "COPY")
        return STR()

    def bi_left_s(self, node):
        self._simple(node, ("LDIN", 1), "SWAP", "COPY")
        return STR()

    def bi_right_s(self, node):
        self._simple(node, ("PICK", 2), "SLEN", "SWAP", "SUBT", "INCR", "MXIN", "COPY")
        return STR()

    def bi_del_s(self, node):
        self._simple(node, "DELS")
        return STR()

    def bi_str_s(self, node):
        self._simple(node, "ITOS")
        return STR()

    def bi_chr_s(self, node):
        self._simple(node, "CTOS")
        return STR()

    def bi_asc(self, node):
        self._simple(node, "SASC")
        return INT

    def bi_len(self, node):
        self._simple(node, "SLEN")
        return INT

    def bi_lcase_s(self, node):
        self._simple(node, ("LDIN", 1), "CASE")
        return STR()

    def bi_ucase_s(self, node):
        self._simple(node, ("LDIN", 2), "CASE")
        return STR()

    def bi_tcase_s(self, node):
        self._simple(node, ("LDIN", 4), "CASE")
        return STR()

    def bi_rnd(self, node):
        self._simple(node, "RAND", "INCR")
        return INT

    def bi_rndcol(self, node):
        self._simple(node, "RAND", "INCR", "RGB", "COLR")
        return None

    def bi_sqr(self, node):
        self._simple(node, ("LDIN", 2), "SWAP", "ROOT")
        return INT

    def bi_val(self, node):
        self._simple(node, ("LDIN", 0), ("LDIN", 2), "SVDF")
        return INT

    def bi_valdef(self, node):
        self._simple(node, ("LDIN", 2), "SVDF")
        return INT

    def bi_get_s(self, node):
        self._simple(node, "READ")
        return STR()

    def bi_getline_s(self, node):
        self.p.emit("RDLN")
        return STR()

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

    def bi_inc(self, node):
        return self._incdec(node, "INCR")

    def bi_dec(self, node):
        return self._incdec(node, "DECR")

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

    def bi_finddir_s(self, node):
        return self._var_param_string(node, "FDIR")

    def bi_findfirst_s(self, node):
        return self._var_param_string(node, "FFND")


def compile_basic(src):
    c = Compiler(src)
    return c.compile(), c
