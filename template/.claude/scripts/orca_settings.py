"""Render orca-settings.json (worktree mode) from orca-settings.json.tmpl for one root.

Usage: python3 orca_settings.py <template> <root>    prints the JSON on stdout

The settings file a worktree-mode session loads with --settings cannot use
$CLAUDE_PROJECT_DIR, which names the worktree there, so every hook path is absolute.
@@ROOT@@ in a hook command becomes the root (the script path shell-quoted when it needs
it) and @@MEMORY@@ becomes the root's memory folder, by the same slug rule as
rootpath.memory_dir, so the session reads and writes the root's memory.
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import rootpath  # noqa: E402


def render(template: str, root: Path) -> str:
    data = json.loads(template)
    for groups in data.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                program, script = hook["command"].split(" ", 1)
                hook["command"] = f"{program} {shlex.quote(script.replace('@@ROOT@@', str(root)))}"
    if data.get("autoMemoryDirectory") == "@@MEMORY@@":
        data["autoMemoryDirectory"] = str(rootpath.memory_dir(root))
    return json.dumps(data, indent=2) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    template = Path(argv[0]).read_text(encoding="utf-8")
    sys.stdout.write(render(template, Path(argv[1]).resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
