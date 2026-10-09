"""List source literals and report missing or unused English translations."""

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_literals(root):
    """Extract immediate literals, including deferred labels and both plurals."""
    result = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            count = {"_": 1, "N_": 1, "_n": 2}.get(node.func.id, 0)
            for arg in node.args[:count]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    result.setdefault(arg.value, []).append(
                        f"{path.relative_to(root)}:{node.lineno}"
                    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "luna_iptv")
    parser.add_argument("--catalog", type=Path, default=ROOT / "luna_iptv/locale/en.json")
    parser.add_argument("--list", action="store_true", help="List extracted source strings")
    args = parser.parse_args(argv)
    sources = source_literals(args.root)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if args.list:
        for source, locations in sorted(sources.items()):
            print(f"{source!r}: {', '.join(locations)}")
    missing = sources.keys() - catalog.keys()
    unused = catalog.keys() - sources.keys()
    for label, entries in (("Missing", missing), ("Unused", unused)):
        for source in sorted(entries):
            print(f"{label}: {source!r}")
    print(f"{len(sources)} source strings; {len(missing)} missing; {len(unused)} unused")
    return int(bool(missing))


if __name__ == "__main__":
    raise SystemExit(main())
