"""Compare this system's PCode with the original's (.tmc files saved by TurtleSystem.exe).

usage: pcode_diff.py ORACLE_DIR [LANG] [NAME_PREFIX] [-v]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tm.pcode import Program, line_text  # noqa: E402

EXT = {"python": ".tgy", "pascal": ".tgp", "basic": ".tgb"}


def compile_file(lang, src):
    if lang == "python":
        from tm.pycomp import compile_python
        return compile_python(src)[0]
    if lang == "pascal":
        from tm.pascomp import compile_pascal
        return compile_pascal(src)[0]
    if lang == "basic":
        from tm.bascomp import compile_basic
        return compile_basic(src)[0]
    raise SystemExit("no compiler for " + lang)


def main():
    oracle = Path(sys.argv[1])
    lang = sys.argv[2] if len(sys.argv) > 2 else "python"
    prefix = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else ""
    verbose = "-v" in sys.argv
    ok = bad = 0
    for tmc in sorted((oracle / lang).glob(prefix + "*.tmc")):
        src = tmc.with_suffix(EXT[lang]).read_text(encoding="latin-1")
        theirs = Program.from_tmc(tmc.read_bytes())
        try:
            mine = compile_file(lang, src)
        except Exception as e:  # noqa: BLE001
            print(f"{tmc.stem}: EXCEPTION {type(e).__name__}: {e} (line {getattr(e, 'line', '?')})")
            bad += 1
            continue
        if mine.lines == theirs.lines and mine.memtop == theirs.memtop:
            ok += 1
            if verbose:
                print(f"{tmc.stem}: ok")
            continue
        bad += 1
        from tm.pcode import CODE, disassemble_line

        def masked(line):
            return [(nm, () if nm in ("JUMP", "IFNO", "SUBR") else tuple(v))
                    for nm, v in disassemble_line(line)]
        first = None
        for n, (a, b) in enumerate(zip(mine.lines, theirs.lines), 1):
            if masked(a) != masked(b):
                first = n
                break
        for n, (a, b) in enumerate(zip(mine.lines, theirs.lines), 1):
            if first is not None and n != first:
                continue
            if a != b:
                print(f"{tmc.stem}: line {n}\n   mine:   {line_text(a)}\n   theirs: {line_text(b)}")
                if "-c" in sys.argv:       # context: where the programs' line structure diverges
                    for k in range(max(1, n - 3), n + 12):
                        ma = line_text(mine.lines[k - 1]) if k <= len(mine.lines) else ""
                        th = line_text(theirs.lines[k - 1]) if k <= len(theirs.lines) else ""
                        flag = " " if ma == th else "*"
                        print(f"  {flag}{k:4} M {ma}\n  {flag}     T {th}")
                break
        else:
            print(f"{tmc.stem}: lines {len(mine.lines)} vs {len(theirs.lines)}, memtop {mine.memtop} vs {theirs.memtop}")
    print(f"{ok} identical, {bad} different")


main()
