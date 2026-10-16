#!/usr/bin/env python3
"""Small AST mutation tester: do the tests notice when logic is broken?

Mutates comparison/arithmetic/boolean operators and numeric constants one at a time in each target module,
runs that module's tests, and reports mutants that SURVIVE (tests still pass = a gap). Sampled and seeded for speed.
Usage: python tools/mutation_smoke.py [--per-module 30]
"""
import ast
import copy
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    "records/audit.py": ["records/tests/test_review_audit.py", "records/tests/test_rot_review_units.py", "tests/property/test_properties.py", "-k", "audit or chain"],
    "records/rot.py": ["records/tests"],
    "records/review.py": ["records/tests/test_review_audit.py", "records/tests/test_rot_review_units.py", "tests/bdd"],
    "insurance/actuarial/mortality.py": ["insurance/tests/test_actuarial.py", "tests/property/test_properties.py", "-k", "life or premium or actuarial or table"],
    "insurance/customers/resolve.py": ["insurance/tests/test_customers_portfolio.py", "tests/property/test_properties.py", "-k", "jaro or scoring or pipeline"],
}
CMP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In}
BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
BOOL = {ast.And: ast.Or, ast.Or: ast.And}


def sites(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and type(n.ops[0]) in CMP: out.append(("cmp", n))
        elif isinstance(n, ast.BinOp) and type(n.op) in BIN: out.append(("bin", n))
        elif isinstance(n, ast.BoolOp): out.append(("bool", n))
        elif isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool) and n.value not in (0, 1): out.append(("num", n))
    return out


def mutate(tree, idx):
    t = copy.deepcopy(tree)
    kind, node = sites(t)[idx]
    if kind == "cmp": node.ops[0] = CMP[type(node.ops[0])]()
    elif kind == "bin": node.op = BIN[type(node.op)]()
    elif kind == "bool": node.op = BOOL[type(node.op)]()
    else: node.value = node.value + 1
    return ast.unparse(ast.fix_missing_locations(t)), f"{kind}@{getattr(node, 'lineno', '?')}"


def run_tests(args):
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:           # a mutant that hangs (e.g. infinite loop) is detected
        return False
    return r.returncode == 0


def main():
    per = int(sys.argv[sys.argv.index("--per-module") + 1]) if "--per-module" in sys.argv else 30
    rnd = random.Random(7)
    total_k = total_n = 0
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
    for rel, tests in TARGETS.items():
        if only not in rel:
            continue
        path = ROOT / rel
        original = path.read_text()
        tree = ast.parse(original)
        if not run_tests(tests):
            print(f"{rel}: baseline tests FAIL, skipping"); continue
        idxs = list(range(len(sites(tree)))); rnd.shuffle(idxs); idxs = idxs[:per]
        killed, survived = 0, []
        try:
            for i in idxs:
                src, desc = mutate(tree, i)
                path.write_text(src)
                if run_tests(tests): survived.append(desc)
                else: killed += 1
        finally:
            path.write_text(original)
        total_k += killed; total_n += len(idxs)
        print(f"{rel}: killed {killed}/{len(idxs)}  survivors: {survived}")
    print(f"MUTATION SCORE: {total_k}/{total_n} = {total_k / max(total_n, 1):.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
