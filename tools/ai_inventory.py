#!/usr/bin/env python3
"""Render docs/governance/AI_INVENTORY.md from use_cases.json (CI fails if the committed file drifts)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai.governance import UseCase, assess, render_markdown  # noqa: E402


def main():
    cases = [UseCase(**c) for c in json.loads((ROOT / "docs/governance/use_cases.json").read_text())]
    (ROOT / "docs/governance/AI_INVENTORY.md").write_text(render_markdown([assess(c) for c in cases], cases))


if __name__ == "__main__":
    main()
