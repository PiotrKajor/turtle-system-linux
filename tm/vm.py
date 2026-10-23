"""The Turtle Machine: runs PCode on a memory of 32-bit cells, like the original.

Memory layout (cell numbers):
    0 ^turtle  1 ^keybuffer  2..11 ^file 1..10  12 ^memtop  13 ^hpperm  14 ^hptemp
    15.. subroutine frame pointers, ^fnbase, the turtle record and the globals;
    the stack of subroutine frames grows from memtop; the heap starts at HEAP_BASE.

A string or list lives at an address a: mem[a-1] is its capacity + 1, mem[a] its length and
mem[a+1..] its characters / elements; elements of nested lists point to their sub-lists.
Each PCode line is translated to a small Python function, which runs the line's instructions
on the stack `s` and the memory `m` and returns the next line (None: the following line).
"""

import time

from runtime import StopProgram, TurtleError, div_round, ext_round, wrap32
from tm.pcode import NOPS, OPS, disassemble_line

HEAP_BASE = 200000
HEAP_LIMIT = 1500000
MEMORY_SIZE = HEAP_LIMIT + 1024
REG_TURTLE, REG_KEYBUF, REG_MEMTOP, REG_HPPERM, REG_HPTEMP = 0, 1, 12, 13, 14


class MachineError(TurtleError):
    pass


def _w(expr):
    """Python source that wraps an integer expression to 32 bits."""
    return f"((({expr}) + 2147483648) & 4294967295) - 2147483648"


# Simple instructions compiled inline: name -> source using s (stack), m (memory), V (machine)
INLINE = {
    "DROP": "s.pop()",
    "DUPL": "s.append(s[-1])",
    "SWAP": "s[-1], s[-2] = s[-2], s[-1]",
    "ROTA": "s.append(s.pop(-3))",
    "INCR": "s[-1] = " + _w("s[-1] + 1"),
    "DECR": "s[-1] = " + _w("s[-1] - 1"),
    "NEG": "s[-1] = " + _w("-s[-1]"),
    "ABS": "s[-1] = " + _w("abs(s[-1])"),
    "NOT": "s[-1] = ~s[-1]",
    "AND": "b = s.pop(); s[-1] &= b",
    "OR": "b = s.pop(); s[-1] |= b",
    "XOR": "b = s.pop(); s[-1] ^= b",
    "ANDL": "b = s.pop(); s[-1] = b if s[-1] else s[-1]",
    "ORL": "b = s.pop(); s[-1] = s[-1] if s[-1] else b",
    "PLUS": "b = s.pop(); s[-1] = " + _w("s[-1] + b"),
    "SUBT": "b = s.pop(); s[-1] = " + _w("s[-1] - b"),
    "MULT": "b = s.pop(); s[-1] = " + _w("s[-1] * b"),
    "EQAL": "b = s.pop(); s[-1] = V.true if s[-1] == b else 0",
    "NOEQ": "b = s.pop(); s[-1] = V.true if s[-1] != b else 0",
    "LESS": "b = s.pop(); s[-1] = V.true if s[-1] < b else 0",
    "MORE": "b = s.pop(); s[-1] = V.true if s[-1] > b else 0",
    "LSEQ": "b = s.pop(); s[-1] = V.true if s[-1] <= b else 0",
    "MREQ": "b = s.pop(); s[-1] = V.true if s[-1] >= b else 0",
    "MAXI": "b = s.pop(); s[-1] = max(s[-1], b)",
    "MINI": "b = s.pop(); s[-1] = min(s[-1], b)",
    "MXIN": "s.append(2147483647)",
    "LPTR": "s[-1] = m[s[-1]]",
    "SPTR": "a = s.pop(); m[a] = s.pop()",
    "TEST": "V.test(s, m)",
    "HCLR": "V.hclr(s)",
    "HFIX": "m[13] = m[14]",
    "LDMT": "s.append(m[12])",
    "PLSR": "V.sregs.pop()",
    "PSRJ": "s.append(line)",
}

# Instructions with operands, compiled inline: name -> function(operands) -> source
INLINE_ARGS = {
    "LDIN": lambda a: f"s.append({a[0]})",
    "LDAG": lambda a: f"s.append({a[0]})",
    "LDVG": lambda a: f"s.append(m[{a[0]}])",
    "LDVV": lambda a: f"s.append(m[m[{a[0]}] + {a[1]}])",
    "LDVR": lambda a: f"s.append(m[m[m[{a[0]}] + {a[1]}]])",
    "LDAV": lambda a: f"s.append(m[{a[0]}] + {a[1]})",
    "STVG": lambda a: f"m[{a[0]}] = s.pop()",
    "STVV": lambda a: f"m[m[{a[0]}] + {a[1]}] = s.pop()",
    "STVR": lambda a: f"m[m[m[{a[0]}] + {a[1]}]] = s.pop()",
    "PICK": lambda a: f"s.append(s[-{a[0]}])",
    "TRUE": lambda a: f"V.true = {a[0]}",
    "PSSR": lambda a: f"V.sregs.append({a[0]})",
}


class TurtleMemoryView:
    """The current turtle (turtx ... turtc) as seen by the drawing library: cells in memory."""

    def __init__(self, vm):
        self.vm = vm

    def __getitem__(self, i):
        m = self.vm.mem
        return m[m[0] + 1 + i]

    def __setitem__(self, i, v):
        m = self.vm.mem
        m[m[0] + 1 + i] = wrap32(int(v))

    def __len__(self):
        return 6

    def __iter__(self):
        return (self[i] for i in range(6))


class TurtleVM:
    def __init__(self, machine, program, autodel=True):
        self.machine = machine
        self.program = program
        self.lib = machine.library()
        self.mem = [0] * MEMORY_SIZE
        self.stack = []
        self.mstack = []           # memory control stack (MEMC / MEMR)
        self.rstack = []           # return stack (SUBR / RETN)
        self.sregs = []            # subroutine register stack (PSSR / PLSR)
        self.true = -1
        self.autodel = autodel
        self.hclr_pending = False
        self.line = 1
        self.max_memtop = 0
        self.max_heap = 0
        self.watch = None
        self.trace = False
        self.halt_line = None
        self.started = False
        self.funcs = [None] + [self._compile_line(n, line)
                               for n, line in enumerate(program.lines, 1)]
        m = self.mem
        for i in range(1, 251):
            m[i] = 0
        m[REG_HPPERM] = m[REG_HPTEMP] = HEAP_BASE - 1

    # ------------------------------------------------ translation of PCode lines
    def _compile_line(self, n, line):
        code = [f"def L(s, m, V, line={n}):"]
        ops = disassemble_line(line)
        for name, vals in ops:
            if name in INLINE:
                code.append("    " + INLINE[name])
            elif name in INLINE_ARGS:
                code.append("    " + INLINE_ARGS[name](vals))
            elif name == "LSTR":
                text = "".join(chr(v) for v in vals[1:])
                code.append(f"    V.lstr(s, m, {text!r})")
            elif name == "JUMP":
                code.append(f"    return {vals[0]}")
            elif name == "IFNO":
                code.append(f"    if not s.pop(): return {vals[0]}")
            elif name == "SUBR":
                code.append(f"    V.subr(line); return {vals[0]}")
            elif name == "RETN":
                code.append("    return V.rstack.pop() + 1")
            elif name == "HALT":
                code.append("    return 0")
            elif name == "PLRJ":
                code.append("    return V.plrj(s)")
            else:
                method = getattr(self, "op_" + name, None)
                if method is None:
                    code.append(f"    V.unknown({name!r})")
                elif vals:
                    code.append(f"    V.op_{name}(s, m, {', '.join(str(v) for v in vals)})")
                else:
                    code.append(f"    V.op_{name}(s, m)")
        env = {}
        exec(compile("\n".join(code), f"<pcode line {n}>", "exec"), env)
        return env["L"]

    def unknown(self, name):
        raise MachineError(f"PCode command {name} not supported")

    # ------------------------------------------------ running
    def start(self):
        """Run in the machine's program thread; errors carry their source line."""
        machine = self.machine

        def runner():
            machine.turtle = TurtleMemoryView(self)
            try:
                self.run()
            except BaseException as e:  # noqa: BLE001
                if not isinstance(e, StopProgram):
                    e.turtle_line = self.program.source_line(self.line)
                raise
        machine.start(runner, {})

    def run(self, check_every=200):
        s, m, funcs = self.stack, self.mem, self.funcs
        machine = self.machine
        line = 1
        count = 0
        while line:
            self.line = line
            if self.hclr_pending and not s:
                self.hclr_pending = False
                m[REG_HPTEMP] = m[REG_HPPERM]
            nxt = funcs[line](s, m, self)
            if line == 2 and machine.dark_canvas and not self.started:
                self.started = True              # after the prologue: white pen
                m[m[REG_TURTLE] + 6] = 0xFFFFFF
            line = line + 1 if nxt is None else nxt
            count += 1
            if count >= check_every:
                count = 0
                machine.check()
            if line == self.halt_line:
                raise StopProgram
        self.line = 0

    def subr(self, line):
        if len(self.rstack) >= 1000:
            raise MachineError("Return stack overflow (too deep recursion)")
        self.rstack.append(line)

    def plrj(self, s):
        target = s.pop()
        self.rstack.pop()
        return target + 1

    # ------------------------------------------------ memory and heap
    def test(self, s, m):
        """TEST: the index below the list address must be within its length."""
        index = s[-2]
        if index < 0 or index >= m[s[-1]]:
            raise MachineError("Array index out of range")

    def hclr(self, s):
        if not self.autodel:
            return
        if s:
            self.hclr_pending = True
        else:
            self.mem[REG_HPTEMP] = self.mem[REG_HPPERM]

    def read_str(self, addr, release=False):
        m = self.mem
        if addr == 0:
            raise MachineError("String pointer unassigned")
        n = m[addr]
        text = "".join(chr(c) for c in m[addr + 1:addr + 1 + n] if 0 <= c < 256)
        if release and self.autodel and addr > m[REG_HPPERM]:
            m[REG_HPTEMP] = addr - 1
        return text

    def write_str(self, text):
        """Put a string on the heap; returns its address."""
        m = self.mem
        top = m[REG_HPTEMP]
        if top + len(text) + 1 >= HEAP_LIMIT:
            raise MachineError("Heap overflow")
        addr = top + 1
        m[addr] = len(text)
        m[addr + 1:addr + 1 + len(text)] = [ord(c) & 0xFF for c in text]
        m[REG_HPTEMP] = top + 1 + len(text)
        if m[REG_HPTEMP] > self.max_heap:
            self.max_heap = m[REG_HPTEMP]
        return addr

    def lstr(self, s, m, text):
        s.append(self.write_str(text))

    def pop_str(self, s, release=True):
        return self.read_str(s.pop(), release)

    def push_str(self, s, text):
        s.append(self.write_str(text))

    def copy_str(self, src, dst):
        m = self.mem
        n = min(m[src], m[dst - 1] - 1)
        m[dst] = n
        if n > 0:
            m[dst + 1:dst + 1 + n] = m[src + 1:src + 1 + n]

    # ------------------------------------------------ stack
    def op_ROLL(self, s, m):
        n = s.pop()
        if n > 0:
            s.append(s.pop(-n))
        elif n < 0:
            s.insert(len(s) + n, s.pop())

    def op_SIGN(self, s, m):
        s[-1] = (s[-1] > 0) - (s[-1] < 0)

    def op_SHFT(self, s, m):
        b = s.pop()
        a = s[-1] & 0xFFFFFFFF
        s[-1] = wrap32(a << b if b >= 0 else a >> -b)

    def op_DIVR(self, s, m):
        b = s.pop()
        s[-1] = div_round(s[-1], b)

    def op_DIV(self, s, m):
        b = s.pop()
        if b == 0:
            raise ZeroDivisionError
        a = s[-1]
        q = abs(a) // abs(b)
        s[-1] = wrap32(q if (a >= 0) == (b >= 0) else -q)

    def op_MOD(self, s, m):
        b = s.pop()
        if b == 0:
            raise ZeroDivisionError
        a = s[-1]
        r = abs(a) % abs(b)
        s[-1] = r if a >= 0 else -r

    def op_DIVF(self, s, m):
        b = s.pop()
        if b == 0:
            raise ZeroDivisionError
        s[-1] = wrap32(s[-1] // b)

    def op_MODF(self, s, m):
        b = s.pop()
        if b == 0:
            raise ZeroDivisionError
        s[-1] = s[-1] - (s[-1] // b) * b

    def op_RAND(self, s, m):
        s[-1] = self.lib["randrange"](abs(s[-1]))

    def op_SEED(self, s, m):
        s[-1] = self.lib["randseed"](s[-1])

    def _call(self, s, name, n, result=True):
        args = s[len(s) - n:]
        del s[len(s) - n:]
        r = self.lib[name](*args)
        if result:
            s.append(wrap32(int(r)))

    # arithmetic functions of the library, operands in source order
    def op_DIVM(self, s, m): self._call(s, "divmult", 3)
    def op_LERP(self, s, m): self._call(s, "lerp", 4)
    def op_HYP(self, s, m): self._call(s, "hypot", 3)
    def op_ROOT(self, s, m): self._call(s, "root", 3)
    def op_POWR(self, s, m): self._call(s, "power", 5)
    def op_LOG(self, s, m): self._call(s, "log10", 3)
    def op_ALOG(self, s, m): self._call(s, "antilog", 3)
    def op_LN(self, s, m): self._call(s, "log", 3)
    def op_EXP(self, s, m): self._call(s, "exp", 3)
    def op_SIN(self, s, m): self._call(s, "sin", 3)
    def op_COS(self, s, m): self._call(s, "cos", 3)
    def op_TAN(self, s, m): self._call(s, "tan", 3)
    def op_ASIN(self, s, m): self._call(s, "asin", 3)
    def op_ACOS(self, s, m): self._call(s, "acos", 3)
    def op_ATAN(self, s, m): self._call(s, "atan", 3)
    def op_PI(self, s, m): self._call(s, "pi", 1)

    # ------------------------------------------------ strings
    def _scmp(self, s, op):
        b = self.read_str(s.pop(), True)
        a = self.read_str(s.pop(), False)
        s.append(self.true if op(a, b) else 0)

    def op_SEQL(self, s, m): self._scmp(s, lambda a, b: a == b)
    def op_SNEQ(self, s, m): self._scmp(s, lambda a, b: a != b)
    def op_SLES(self, s, m): self._scmp(s, lambda a, b: a < b)
    def op_SMOR(self, s, m): self._scmp(s, lambda a, b: a > b)
    def op_SLEQ(self, s, m): self._scmp(s, lambda a, b: a <= b)
    def op_SMEQ(self, s, m): self._scmp(s, lambda a, b: a >= b)

    def op_SMAX(self, s, m):
        b = self.read_str(s.pop(), True)
        a = self.read_str(s.pop(), False)
        self.push_str(s, max(a, b))

    def op_SMIN(self, s, m):
        b = self.read_str(s.pop(), True)
        a = self.read_str(s.pop(), False)
        self.push_str(s, min(a, b))

    def op_CASE(self, s, m):
        mode = s.pop()
        text = self.read_str(s.pop(), True)
        if mode == 1:
            text = text.lower()
        elif mode == 2:
            text = text.upper()
        elif mode == 3:
            text = text.lower()
            text = text[:1].upper() + text[1:]
        elif mode == 4:
            out, new = [], True
            for c in text.lower():
                out.append(c.upper() if new else c)
                new = c == " "
            text = "".join(out)
        elif mode == 5:
            text = text.swapcase()
        self.push_str(s, text)

    def op_COPY(self, s, m):
        n = s.pop()
        start = s.pop()
        text = self.read_str(s.pop(), True)
        if start < 1:
            n += start - 1
            start = 1
        self.push_str(s, text[start - 1:start - 1 + max(0, n)])

    def op_DELS(self, s, m):
        n = s.pop()
        start = s.pop()
        text = self.read_str(s.pop(), True)
        if 1 <= start <= len(text) and n > 0:
            text = text[:start - 1] + text[start - 1 + n:]
        self.push_str(s, text)

    def op_INSS(self, s, m):
        index = s.pop()
        text = self.read_str(s.pop(), True)
        sub = self.read_str(s.pop(), False)
        index = max(1, min(index, len(text) + 1))
        self.push_str(s, text[:index - 1] + sub + text[index - 1:])

    def op_POSS(self, s, m):
        text = self.read_str(s.pop(), True)
        sub = self.read_str(s.pop(), False)
        s.append(text.find(sub) + 1 if sub else 0)

    def op_REPL(self, s, m):
        n = s.pop()
        new = self.read_str(s.pop(), True)
        old = self.read_str(s.pop(), False)
        text = self.read_str(s.pop(), False)
        if old:
            text = text.replace(old, new) if n < 1 else text.replace(old, new, n)
        self.push_str(s, text)

    def op_SCAT(self, s, m):
        b = self.read_str(s.pop(), True)
        a = self.read_str(s.pop(), False)
        self.push_str(s, a + b)

    def op_SLEN(self, s, m):
        if s[-1] == 0:
            raise MachineError("String pointer unassigned")
        s[-1] = m[s[-1]]

    def op_SMUL(self, s, m):
        n = s.pop()
        text = self.read_str(s.pop(), False)
        self.push_str(s, text * max(0, n))

    def op_SPAD(self, s, m):
        w = s.pop()
        pad = self.read_str(s.pop(), True)
        text = self.read_str(s.pop(), False)
        self.push_str(s, self.lib["pad"](text, pad, w))

    def op_TRIM(self, s, m):
        self.push_str(s, self.read_str(s.pop(), True).strip(" \t\r\n"))

    def op_CTST(self, s, m):
        if m[s[-1]] != 1:
            raise MachineError("String is not a single character")

    def op_ERNF(self, s, m):
        if s[-1] < 0:
            raise MachineError("Item not found")

    def op_CTOS(self, s, m):
        self.push_str(s, chr(s.pop() & 0xFF))

    def op_SASC(self, s, m):
        text = self.read_str(s.pop(), True)
        s.append(ord(text[0]) if text else 0)

    def op_ITOS(self, s, m):
        self.push_str(s, str(s.pop()))

    def op_HEXS(self, s, m):
        digits = s.pop()
        self.push_str(s, self.lib["hex"](s.pop(), digits))

    def op_SVAL(self, s, m):
        s.pop()
        text = self.read_str(s.pop(), True)
        s.append(self.lib["intdef"](text, 0))

    def op_SVDF(self, s, m):
        s.pop()
        default = s.pop()
        text = self.read_str(s.pop(), True)
        s.append(self.lib["intdef"](text, default))

    def op_QTOS(self, s, m):
        places = s.pop()
        b = s.pop()
        a = s.pop()
        self.push_str(s, self.lib["qstr"](a, b, places))

    def op_QVAL(self, s, m):
        default = s.pop()
        n = s.pop()
        text = self.read_str(s.pop(), True)
        s.append(self.lib["qint"](text, n, default))

    def op_HSTR(self, s, m):
        self.push_str(s, self.read_str(s.pop(), True))

    def op_CSTR(self, s, m):
        dst = s.pop()
        src = s.pop()
        self.copy_str(src, dst)

    def op_ZPTR(self, s, m):
        n = s.pop()
        a = s.pop()
        if n > 0:
            m[a:a + n] = [0] * n

    def op_CPTR(self, s, m):
        n = s.pop()
        dst = s.pop()
        src = s.pop()
        if n > 0:
            m[dst:dst + n] = m[src:src + n]

    def op_STMT(self, s, m):
        m[REG_MEMTOP] = s.pop()
        if m[REG_MEMTOP] > self.max_memtop:
            self.max_memtop = m[REG_MEMTOP]
        if self.max_memtop >= HEAP_BASE:
            raise MachineError("Memory stack has collided with the heap")

    def op_MEMC(self, s, m, reg, size):
        if len(self.mstack) >= 1000:
            raise MachineError("Memory control stack overflow")
        self.mstack.append(m[reg])
        m[reg] = m[REG_MEMTOP]
        m[REG_MEMTOP] += size
        if m[REG_MEMTOP] > self.max_memtop:
            self.max_memtop = m[REG_MEMTOP]
            if self.max_memtop >= HEAP_BASE:
                raise MachineError("Memory stack has collided with the heap")

    def op_MEMR(self, s, m, reg):
        m[REG_MEMTOP] = m[reg]
        m[reg] = self.mstack.pop()

    def op_HRST(self, s, m):
        m[REG_HPPERM] = m[REG_HPTEMP] = HEAP_BASE
        m[1] = 0

    # ------------------------------------------------ lists
    def _elem_copy(self, src_val, dst_addr, code):
        """Store an element value (int, string address or list address) into a slot."""
        m = self.mem
        if code == 4:
            m[dst_addr] = src_val
        elif code == 5:
            self.copy_str(src_val, m[dst_addr])
        else:
            self.list_copy(src_val, m[dst_addr], code - 16)

    def list_copy(self, src, dst, code):
        m = self.mem
        n = min(m[src], m[dst - 1] - 1)
        m[dst] = n
        for k in range(1, n + 1):
            if code == 4:
                m[dst + k] = m[src + k]
            elif code == 5:
                self.copy_str(m[src + k], m[dst + k])
            else:
                self.list_copy(m[src + k], m[dst + k], code - 16)

    def _elem_equal(self, a, b, code):
        m = self.mem
        if code == 4:
            return a == b
        if code == 5:
            return self.read_str(a) == self.read_str(b)
        if m[a] != m[b]:
            return False
        return all(self._elem_equal(m[a + k], m[b + k], code - 16) for k in range(1, m[a] + 1))

    def _shift(self, lst, frm, to, code):
        """Copy element slot frm to slot to (1-based)."""
        m = self.mem
        if code == 4:
            m[lst + to] = m[lst + frm]
        elif code == 5:
            self.copy_str(m[lst + frm], m[lst + to])
        else:
            self.list_copy(m[lst + frm], m[lst + to], code - 16)

    def op_LAPP(self, s, m, code):
        value = s.pop()
        lst = s.pop()
        cap = m[lst - 1] - 1
        n = m[lst]
        if n < cap:
            m[lst] = n + 1
            self._elem_copy(value, lst + n + 1, code)

    def op_LCPY(self, s, m, code):
        src = s.pop()
        dst = s.pop()
        if src != dst:
            self.list_copy(src, dst, code)

    def op_LDEL(self, s, m, code):
        index = s.pop()
        lst = s.pop()
        n = m[lst]
        if index < 0 or index >= n:
            raise MachineError("Invalid list index")
        m[lst] = n - 1
        for k in range(index + 1, n):
            self._shift(lst, k + 1, k, code)

    def op_LEXT(self, s, m, code):
        other = s.pop()
        lst = s.pop()
        cap = m[lst - 1] - 1
        n = m[lst]
        total = min(n + m[other], cap)
        m[lst] = max(n, total)
        for k in range(1, total - n + 1):
            self._elem_copy(m[other + k], lst + n + k, code)

    def op_LIDX(self, s, m, code):
        value = s.pop()
        lst = s.pop()
        n = m[lst]
        for k in range(1, n + 1):
            if self._elem_equal(m[lst + k], value, code):
                s.append(k - 1)
                return
        s.append(-1)

    def op_LINS(self, s, m, code):
        value = s.pop()
        index = s.pop()
        lst = s.pop()
        n = m[lst]
        cap = m[lst - 1]
        if index > n or index >= cap or index < 0:
            return
        m[lst] = min(n + 1, cap - 1)
        for k in range(min(n, cap - 2), index, -1):
            self._shift(lst, k, k + 1, code)
        self._elem_copy(value, lst + index + 1, code)

    def op_LMUL(self, s, m, code):
        times = s.pop()
        lst = s.pop()
        n = m[lst]
        if n <= 0:
            return
        total = min(n * times, m[lst - 1] - 1)
        for k in range(n, total):
            self._shift(lst, k % n + 1, k + 1, code)
        m[lst] = total

    def op_LPOP(self, s, m, code):
        index = s.pop()
        lst = s.pop()
        n = m[lst]
        if index < 0 or index >= n:
            raise MachineError("Invalid list index")
        value = m[lst + index + 1]
        if code == 5:
            value = self.write_str(self.read_str(value))
        m[lst] = n - 1
        for k in range(index + 1, n):
            self._shift(lst, k + 1, k, code)
        s.append(value)

    def op_LREM(self, s, m, code):
        value = s.pop()
        lst = s.pop()
        n = m[lst]
        for k in range(1, n + 1):
            if self._elem_equal(m[lst + k], value, code):
                m[lst] = n - 1
                for j in range(k, n):
                    self._shift(lst, j + 1, j, code)
                return
        raise MachineError("Item not found in list")

    def op_LREV(self, s, m, code):
        lst = s.pop()
        n = m[lst]
        if code == 4:
            m[lst + 1:lst + 1 + n] = m[lst + 1:lst + 1 + n][::-1]
        else:
            items = [self._snapshot(m[lst + k], code) for k in range(1, n + 1)]
            for k, item in enumerate(reversed(items), 1):
                self._restore(item, m[lst + k], code)

    def _snapshot(self, addr, code):
        m = self.mem
        if code == 5:
            return self.read_str(addr)
        return [m[addr]] + [self._snapshot(m[addr + k], code - 16) if code >= 16 else m[addr + k]
                            for k in range(1, m[addr] + 1)]

    def _restore(self, value, addr, code):
        m = self.mem
        if code == 5:
            m[addr] = len(value)
            m[addr + 1:addr + 1 + len(value)] = [ord(c) for c in value]
            return
        m[addr] = value[0]
        for k, v in enumerate(value[1:], 1):
            if code >= 16:
                self._restore(v, m[addr + k], code - 16)
            else:
                m[addr + k] = v

    def list_text(self, lst, code):
        m = self.mem
        parts = []
        for k in range(1, m[lst] + 1):
            v = m[lst + k]
            if code == 4:
                parts.append(str(v))
            elif code == 5:
                parts.append("'" + self.read_str(v) + "'")
            else:
                parts.append(self.list_text(v, code - 16))
        return "[" + ", ".join(parts) + "]"

    def op_LPRT(self, s, m, code):
        self.push_str(s, self.list_text(s.pop(), code))

    def _init_array(self, pos, dims, level):
        """LIAD / LIHP: lay out an array (and its sub-arrays) from cell pos + 1 on."""
        m = self.mem
        d = dims[level - 1]
        pos += 1
        m[pos] = d + 1
        pos += 1
        m[pos] = 0
        start = pos
        pos += d
        if level == len(dims):
            m[start + 1:start + 1 + d] = [0] * d
        else:
            for k in range(1, d + 1):
                m[start + k] = pos + 2
                pos = self._init_array(pos, dims, level + 1)
        return pos

    @staticmethod
    def _dims(code):
        radix = (2048, 1024, 64, 32)
        dims = []
        k = 0
        while code > 0:
            dims.append(code % radix[k])
            code //= radix[k]
            k += 1
        return dims

    def op_LIAD(self, s, m, code):
        addr = s.pop()
        self._init_array(addr - 2, self._dims(code), 1)

    def op_LIHP(self, s, m, code):
        s.append(m[REG_HPTEMP] + 2)
        m[REG_HPTEMP] = self._init_array(m[REG_HPTEMP], self._dims(code), 1)
        if m[REG_HPTEMP] > self.max_heap:
            self.max_heap = m[REG_HPTEMP]

    # ------------------------------------------------ files
    def op_CHDR(self, s, m):
        self.lib["chdir"](self.pop_str(s))

    def op_FILE(self, s, m):
        code = s.pop()
        name = self.pop_str(s)
        s.append(self.lib["checkfile"](name, code))

    def op_DIRY(self, s, m):
        code = s.pop()
        name = self.pop_str(s)
        s.append(self.lib["checkdir"](name, code))

    def op_OPEN(self, s, m):
        mode = s.pop()
        name = self.pop_str(s)
        s.append(self.lib["fopen"](name, mode))

    def op_CLOS(self, s, m):
        self.lib["fclose"](s.pop())

    def op_FBEG(self, s, m):
        self.lib["frestart"](s.pop())

    def op_EOF(self, s, m):
        s[-1] = self.true if self.lib["eof"](s[-1]) else 0

    def op_EOLN(self, s, m):
        s[-1] = self.true if self.lib["eoln"](s[-1]) else 0

    def op_FRDS(self, s, m):
        n = s.pop()
        self.push_str(s, self.lib["fread"](s.pop(), n))

    def op_FRLN(self, s, m):
        self.push_str(s, self.lib["freadline"](s.pop()))

    def op_FWRS(self, s, m):
        text = self.pop_str(s)
        self.lib["fwrite"](s.pop(), text)

    def op_FWLN(self, s, m):
        text = self.pop_str(s)
        self.lib["fwriteline"](s.pop(), text)

    def op_FFND(self, s, m):
        pattern = self.pop_str(s)
        handle = s.pop()
        s.append(handle)
        self.push_str(s, self.lib["findfirst"](pattern, handle))

    def op_FDIR(self, s, m):
        pattern = self.pop_str(s)
        handle = s.pop()
        s.append(handle)
        self.push_str(s, self.lib["finddir"](pattern, handle))

    def op_FNXT(self, s, m):
        self.push_str(s, self.lib["findnext"](s.pop()))

    def op_FMOV(self, s, m):
        mode = s.pop()
        new = self.read_str(s.pop(), True)
        old = self.read_str(s.pop(), False)
        fn = {1: "fmove", 2: "fmove", 3: "fcopy"}[mode]
        s.append(self.true if self.lib[fn](old, new) else 0)

    # ------------------------------------------------ machine monitoring
    def op_TRAC(self, s, m):
        self.trace = bool(s.pop())

    def op_MEMW(self, s, m):
        self.watch = s.pop()

    def op_DUMP(self, s, m):
        self.machine.bridge.dump.emit() if hasattr(self.machine.bridge, "dump") else None

    def op_PCOH(self, s, m):
        self.halt_line = s.pop()

    def op_POKE(self, s, m):
        value = s.pop()
        m[s.pop()] = value

    def op_TRY(self, s, m, n):
        pass

    def op_XCPT(self, s, m):
        pass

    # ------------------------------------------------ canvas and turtle
    def _lib(self, s, name, n):
        self._call(s, name, n, result=False)

    def op_CANV(self, s, m): self._lib(s, "canvas", 4)
    def op_RESO(self, s, m): self._lib(s, "resolution", 2)

    def op_UDAT(self, s, m):
        self.lib["update" if s.pop() else "noupdate"]()

    def op_HOME(self, s, m): self.lib["home"]()
    def op_SETX(self, s, m): self._lib(s, "setx", 1)
    def op_SETY(self, s, m): self._lib(s, "sety", 1)
    def op_SETD(self, s, m): self._lib(s, "direction", 1)
    def op_ANGL(self, s, m):
        n = s.pop()
        base = m[REG_TURTLE]
        if m[base + 4] != 0:                 # keep the direction pointing the same way
            if n == 0:
                raise ZeroDivisionError
            m[base + 3] = ext_round(m[base + 3] * n / m[base + 4] + n) % n
        m[base + 4] = n
    def op_THIK(self, s, m): self._lib(s, "thickness", 1)

    def op_PEN(self, s, m):
        self.lib["pendown" if s.pop() else "penup"]()

    def op_COLR(self, s, m): self._lib(s, "colour", 1)
    def op_TOXY(self, s, m): self._lib(s, "setxy", 2)
    def op_MVXY(self, s, m): self._lib(s, "movexy", 2)
    def op_DRXY(self, s, m): self._lib(s, "drawxy", 2)
    def op_FWRD(self, s, m): self._lib(s, "forward", 1)
    def op_BACK(self, s, m): self._lib(s, "back", 1)
    def op_LEFT(self, s, m): self._lib(s, "left", 1)
    def op_RGHT(self, s, m): self._lib(s, "right", 1)
    def op_TURN(self, s, m): self._lib(s, "turnxy", 2)
    def op_BLNK(self, s, m): self._lib(s, "blank", 1)
    def op_RCOL(self, s, m): self._lib(s, "recolour", 3)
    def op_FILL(self, s, m): self._lib(s, "fill", 4)
    def op_PIXC(self, s, m): self._call(s, "pixcol", 2)
    def op_PIXS(self, s, m): self._lib(s, "pixset", 3)
    def op_RGB(self, s, m): self._call(s, "rgb", 1)
    def op_MIXC(self, s, m): self._call(s, "mixcols", 4)
    def op_RMBR(self, s, m): self.lib["remember"]()
    def op_FRGT(self, s, m): self._lib(s, "forget", 1)
    def op_POLY(self, s, m): self._lib(s, "polyline", 1)
    def op_PFIL(self, s, m): self._lib(s, "polygon", 1)
    def op_CIRC(self, s, m): self._lib(s, "circle", 1)
    def op_BLOT(self, s, m): self._lib(s, "blot", 1)
    def op_ELPS(self, s, m): self._lib(s, "ellipse", 2)
    def op_EBLT(self, s, m): self._lib(s, "ellblot", 2)
    def op_BOX(self, s, m): self._lib(s, "box", 4)

    # ------------------------------------------------ input and output
    def op_STAT(self, s, m):
        code = s[-1]
        s[-1] = self.lib["status"](code) if -11 <= code <= 255 else 0

    def op_ICLR(self, s, m):
        self.lib["reset"](s.pop())

    def op_BUFR(self, s, m):
        n = s.pop()
        if n > 0:
            self.lib["keybuffer"](n)
            self._layout_keybuffer(n)

    def _layout_keybuffer(self, n):
        """The key buffer's cells at the start of the heap: end, read and write pointers."""
        m = self.mem
        base = m[REG_HPTEMP] + 1
        m[REG_KEYBUF] = base
        m[base] = base + n + 2
        m[base + 1] = base + 3
        m[base + 2] = base + 3
        m[REG_HPTEMP] = base + n + 2
        s = self.stack
        s.append(base)

    def op_READ(self, s, m):
        self.push_str(s, self.lib["read"](s.pop()))

    def op_RDLN(self, s, m):
        self.push_str(s, self.lib["readline"]())

    def op_TDET(self, s, m):
        ms = s.pop()
        s[-1] = self.lib["detect"](s[-1], ms)

    def op_CURS(self, s, m): self._lib(s, "cursor", 1)

    def op_KECH(self, s, m):
        self.lib["keyecho"](bool(s.pop()))

    def op_OUTP(self, s, m):
        front = s.pop()
        col = s.pop()
        clear = s.pop()
        self.lib["output"](clear, col, front)

    def op_CONS(self, s, m):
        col = s.pop()
        clear = s.pop()
        self.lib["console"](clear, col)

    def op_DISP(self, s, m):
        size = s.pop()
        font = s.pop()
        self.lib["display"](self.pop_str(s), font, size)

    def op_WRIT(self, s, m):
        text = self.pop_str(s)
        self.machine.check()
        bridge = self.machine.bridge
        bridge.write.emit(text.replace("\r\n", "\n"), "console")
        bridge.write.emit(text.replace("\r\n", "\n"), "output")

    def op_NEWL(self, s, m):
        bridge = self.machine.bridge
        bridge.write.emit("\n", "console")
        bridge.write.emit("\n", "output")

    def op_TIME(self, s, m):
        s.append(self.lib["time"]())

    def op_TSET(self, s, m):
        self.lib["timeset"](s.pop())

    def op_WAIT(self, s, m):
        self.lib["pause"](s.pop())

    def op_ADDR(self, s, m):
        pass
