"""Report registry drift: unregistered folders, missing homes, unfinished fields.

Usage: python3 registry_check.py [--root <project root>]
The root defaults to the derived one, so the script works from any directory.
Exit 1 when anything is reported, 0 when clean, 2 when CONTEXTS.md itself is missing (a usage
problem, not a finding, matching the neutrality gate's convention for the same exit code).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import registry  # noqa: E402
import rootpath  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="project root (defaults to the derived root)")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else rootpath.root()
    registry_path = root / "CONTEXTS.md"
    if not registry_path.is_file():
        print(f"No registry at {registry_path}. Run the installer, then the context-bootstrap "
              "skill, before checking for drift.")
        return 2
    cfg = rootpath.config(root)
    reg = registry.load_registry(registry_path)

    sections: dict[str, list] = {
        "Folders with no registry block": registry.unregistered_folders(reg, root, cfg),
        "Registry blocks whose home folder does not exist": registry.missing_homes(reg, root),
    }
    if (cfg.get("iac") or {}).get("root"):
        sections["IaC environment names with no registry block"] = sorted(
            registry.iac_names_without_block(reg, root, cfg))
    sections["Contexts with an unfinished TODO field"] = registry.todo_placeholders(reg)

    problems = 0
    print(f"Registry: {len(registry.contexts(reg))} contexts, root {root}")
    for title, items in sections.items():
        print(f"\n{title}: {len(items)}")
        for item in items:
            print(f"  - {item}")
        problems += len(items)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
