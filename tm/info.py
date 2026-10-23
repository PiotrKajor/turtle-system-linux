"""Compilation for the Turtle Machine and a language-neutral description of the result, used
by the Vars/Subs, PCode and Memory tabs (in the original's terms and layout)."""

from tm import pcode

LANG_NAMES = {"python": "Python", "pascal": "Pascal", "basic": "BASIC"}
RESERVED = ["^turtle", "^keybuf"] + [f"^file {k}" for k in range(1, 11)] + \
    ["^memtop", "^hpperm", "^hptemp"]
TURTLE_FIELDS = ("turtx", "turty", "turtd", "turta", "turtt", "turtc")


def compile_source(language, source):
    """(Program, compiler) for the source; raises the compiler's CompileError."""
    if language == "python":
        from tm.pycomp import compile_python
        return compile_python(source)
    if language == "pascal":
        from tm.pascomp import compile_pascal
        return compile_pascal(source)
    from tm.bascomp import compile_basic
    return compile_basic(source)


# ---------------------------------------------------------------- types

def _norm(t):
    """Normalise both compilers' type notations to ("int"|"bool"|"char"|"str"|"array", ...)."""
    if isinstance(t, str):                       # Python compiler
        return {"int": ("int",), "bool": ("bool",), "str": ("str", 64)}.get(t, ("int",))
    if isinstance(t, tuple) and t and t[0] == "list":
        return ("list", _norm(t[1]))
    return t


def type_name(t):
    t = _norm(t)
    k = t[0]
    if k in ("array", "list"):
        return type_name(t[-1])
    return {"int": "Integer", "bool": "Boolean", "char": "Char", "str": "String"}.get(k, "Integer")


def array_text(t):
    t = _norm(t)
    parts = []
    while t[0] in ("array", "list"):
        if t[0] == "array":
            parts.append(f"{t[1]}..{t[2]}" if t[2] >= t[1] else "open")
        else:
            parts.append("list")
        t = t[-1]
    return ",".join(parts) if parts else "-"


# ---------------------------------------------------------------- description

class ProgramInfo:
    def __init__(self, language, source, program, comp):
        self.language = language
        self.program = program
        self.comp = comp
        self.nlines_src = source.rstrip("\n").count("\n") + 1
        self.turtle_base = comp.turtle_base
        self.fnbase = comp.fnbase
        subs = getattr(comp, "sub_list", None) or getattr(comp, "subs_all", [])
        self.subs = sorted(subs, key=lambda s: s.index)
        self.nsubs = len(self.subs)
        self.has_fnbase = self.turtle_base > 15 + self.nsubs
        main_start = 2
        for line in range(1, len(program.lines) + 1):
            if program.lines[line - 1] and pcode.OPS.get(program.lines[line - 1][0], ("",))[0] == "JUMP" \
                    and line > 2:
                main_start = program.lines[line - 1][1]
                break
        self.main_scope = (main_start if self.subs else 1, len(program.lines))
        self.sub_rows = self._sub_rows()
        self.var_rows = self._var_rows()

    # -- subroutines
    def _sub_lines(self, sub):
        start = getattr(sub, "line", None) or getattr(getattr(sub, "node", None), "line", 0)
        end = getattr(sub, "end_line", None)
        if end is None and getattr(sub, "node", None) is not None:
            end = start
            stack = list(sub.node.body)
            while stack:
                n = stack.pop()
                if hasattr(n, "line"):
                    end = max(end, n.line)
                for v in n.__dict__.values():
                    if isinstance(v, list):
                        stack.extend(x for x in v if hasattr(x, "__dict__"))
                    elif hasattr(v, "__dict__") and hasattr(v, "kind"):
                        stack.append(v)
        return start, end

    def _sub_rows(self):
        rows = [{"index": 0, "type": "PROGRAM", "name": "", "params": "0:",
                 "mem": self.program.memtop, "lines": (1, self.nlines_src),
                 "pcode": self.main_scope, "entry": self.main_scope[0],
                 "exit": self.main_scope[1]}]
        for sub in self.subs:
            is_fn = bool(getattr(sub, "is_function", False))
            exit_line = sub.exit.line or 0
            rows.append({"index": sub.index, "type": "Function" if is_fn else "Procedure",
                         "name": sub.name, "params": f"{len(sub.params)}:", "mem": sub.size,
                         "lines": self._sub_lines(sub), "pcode": (sub.entry.line, exit_line),
                         "entry": sub.entry.line, "exit": exit_line, "sub": sub})
        return rows

    # -- variables
    def _var_rows(self):
        rows = []
        lines = (1, self.nlines_src)
        for k, name in enumerate(TURTLE_FIELDS):
            rows.append({"name": name, "context": "(0) GLOBAL", "index": self.turtle_base + 1 + k,
                         "array": "-", "type": "Integer", "ref": "global", "lines": lines,
                         "pcode": self.main_scope, "sub": None, "vtype": ("int",)})
        shared = set()
        for sub in self.subs:
            shared |= set(getattr(sub, "globals", ()))
        for name, v in sorted(self.comp.globals.items(), key=lambda kv: kv[1].addr):
            rows.append({"name": name, "context": "(0) GLOBAL", "index": v.addr,
                         "array": array_text(v.type), "type": type_name(v.type),
                         "ref": "shared" if name in shared else "global", "lines": lines,
                         "pcode": self.main_scope, "sub": None, "vtype": _norm(v.type)})
        for row in self.sub_rows[1:]:
            sub = row["sub"]
            for name, v in sorted(sub.locals.items(), key=lambda kv: kv[1].addr):
                ref = "value"
                if getattr(v, "byref", False):
                    ref = "reference"
                elif v.scope == "local":
                    ref = "local"
                rows.append({"name": name, "context": f"({sub.index}) {sub.name}",
                             "index": v.addr, "array": array_text(v.type),
                             "type": type_name(v.type), "ref": ref, "lines": row["lines"],
                             "pcode": row["pcode"], "sub": sub, "vtype": _norm(v.type)})
        return rows

    # -- memory cell labels
    def static_labels(self):
        """{address: label} for the fixed cells and the globals."""
        labels = {k: name for k, name in enumerate(RESERVED)}
        for sub in self.subs:
            labels[14 + sub.index] = f"^subr {sub.index}"
        if self.has_fnbase:
            labels[self.fnbase] = "^fnbase"
        labels[self.turtle_base] = "TURTLE"
        for k, name in enumerate(TURTLE_FIELDS):
            labels[self.turtle_base + 1 + k] = name
        for name, v in self.comp.globals.items():
            self._label_var(labels, name, v.addr, _norm(v.type))
        return labels

    def frame_labels(self, mem):
        """Labels for the frames of the subroutines active now (frame pointer set)."""
        labels = {}
        for sub in self.subs:
            base = mem[14 + sub.index]
            if base <= 0:
                continue
            if getattr(sub, "is_function", False):
                labels[base + 1] = "result"
            for name, v in sub.locals.items():
                t = _norm(v.type)
                if getattr(v, "byref", False):
                    labels[base + v.addr] = "^" + name
                else:
                    self._label_var(labels, name, base + v.addr, t)
        return labels

    @staticmethod
    def _label_var(labels, name, addr, t):
        k = t[0]
        if k == "str":
            labels[addr] = f"^{name}[]"
            labels[addr + 2] = f"{name}[]"
            for i in range(t[1]):
                labels[addr + 3 + i] = f"{name}[{i}]"
        elif k in ("array", "list"):
            labels[addr] = f"^{name}[]"
        else:
            labels[addr] = name
