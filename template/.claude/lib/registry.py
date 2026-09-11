"""Parse and check CONTEXTS.md, the context registry for a project root."""
from __future__ import annotations

import re
from pathlib import Path

import rootpath

REGISTRY_MARKER = "<!-- registry -->"
LIST_KEYS = ("aliases", "iac_names", "owns", "paths")
MULTILINE_KEYS = ("rules", "owns", "paths")
BLOCK_RE = re.compile(r"^## (?P<name>[^\n]+)\n(?P<body>.*?)(?=^## |\Z)", re.M | re.S)


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def load_registry(path: Path) -> dict[str, dict]:
    text = Path(path).read_text(encoding="utf-8")
    _, _, body = text.partition(REGISTRY_MARKER)
    reg: dict[str, dict] = {}
    for m in BLOCK_RE.finditer(body):
        name = m.group("name").strip()
        entry: dict = {"name": name, "aliases": [], "iac_names": [], "owns": [], "rules": [], "paths": []}
        current = None
        for raw in m.group("body").splitlines():
            if not raw.strip():
                continue
            if raw.startswith("  - ") and current in MULTILINE_KEYS:
                entry[current].append(raw[4:].strip())
                continue
            key, sep, value = raw.partition(":")
            if not sep:
                continue
            key, value = key.strip(), value.strip()
            current = key
            if key in LIST_KEYS:
                entry[key] = _split_list(value)
            elif key == "rules":
                entry[key] = [value] if value else []
            else:
                entry[key] = value
        reg[name] = entry
    return reg


def contexts(reg: dict[str, dict]) -> dict[str, dict]:
    return {k: v for k, v in reg.items() if k != "ignore"}


def resolve(reg: dict[str, dict], term: str) -> list[str]:
    t = term.strip().lower()
    hits = []
    for name, entry in contexts(reg).items():
        names = [name.lower()] + [a.lower() for a in entry.get("aliases", [])]
        if t in names:
            hits.append(name)
    return hits


def scanned_parents(root: Path, cfg: dict | None = None) -> list[str]:
    """Top level folders scanned for unregistered work.

    From kit.json when it names them, otherwise every visible top level folder.
    Naming them is what stops a root with loose folders (docs, notes, archives)
    from reporting all of them as unregistered work every single run.
    """
    cfg = rootpath.config(root) if cfg is None else cfg
    named = cfg.get("scanned_parents")
    if isinstance(named, list) and named:
        return [str(n).strip("/") for n in named if str(n).strip("/")]
    return sorted(p.name for p in Path(root).iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def _covered_paths(reg: dict[str, dict]) -> list[str]:
    """Home and owns paths from every context. Subject to the scanned-parent refusal in
    `_is_covered`: naming a bare scanned parent here covers only itself, not its children."""
    paths = []
    for entry in contexts(reg).values():
        home = entry.get("home", "")
        if home and home != "none":
            paths.append(home.rstrip("/"))
        paths.extend(p.rstrip("/") for p in entry.get("owns", []))
    return paths


def _ignored_paths(reg: dict[str, dict]) -> list[str]:
    """Paths from the `ignore` block. Always cover themselves and everything under them.

    Unlike `home` and `owns`, an ignore entry is the person explicitly saying a whole path,
    scanned parent or not, is out of scope. There is no "new folder under it" to protect
    against finding, because nothing under it is ever meant to become a context.
    """
    return [p.rstrip("/") for p in reg.get("ignore", {}).get("paths", [])]


def _is_covered(rel: str, covered: list[str], parents: list[str], ignored: list[str] = ()) -> bool:
    for c in ignored:
        if rel == c or rel.startswith(c + "/") or c.startswith(rel + "/"):
            return True
    for c in covered:
        if rel == c:
            return True
        # A bare scanned parent as a home or owns entry covers only itself.
        # Treating it as a prefix would hide every new folder under it, which
        # is exactly what this check exists to find.
        if c in parents:
            continue
        if rel.startswith(c + "/") or c.startswith(rel + "/"):
            return True
    return False


def unregistered_folders(reg: dict[str, dict], root: Path, cfg: dict | None = None) -> list[str]:
    parents = scanned_parents(root, cfg)
    covered = _covered_paths(reg)
    ignored = _ignored_paths(reg)
    out = []
    for parent in parents:
        pdir = Path(root) / parent
        if not pdir.is_dir():
            continue
        for child in sorted(pdir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            rel = f"{parent}/{child.name}"
            if not _is_covered(rel, covered, parents, ignored):
                out.append(rel)
    return out


def missing_homes(reg: dict[str, dict], root: Path) -> list[str]:
    out = []
    for name, entry in contexts(reg).items():
        home = entry.get("home", "")
        if home and home != "none" and not (Path(root) / home).is_dir():
            out.append(name)
    return out


def env_dir_names(root: Path, cfg: dict | None = None) -> set[str]:
    """Per-environment folder names under the configured infrastructure root.

    Shaped as <iac root>/<repo>/environments/<stage>/<name>. Returns an empty
    set when no iac root is configured, which is the neutral default.
    """
    cfg = rootpath.config(root) if cfg is None else cfg
    iac_root = (cfg.get("iac") or {}).get("root")
    names: set[str] = set()
    if not iac_root:
        return names
    base = Path(root) / iac_root
    if not base.is_dir():
        return names
    for repo in base.iterdir():
        envs = repo / "environments"
        if not envs.is_dir():
            continue
        for stage in envs.iterdir():
            if not stage.is_dir() or stage.name.startswith("_") or stage.name.startswith("."):
                continue
            for entry in stage.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    names.add(entry.name)
    return names


def iac_names_without_block(reg: dict[str, dict], root: Path, cfg: dict | None = None) -> set[str]:
    known = {n for e in contexts(reg).values() for n in e.get("iac_names", [])}
    return env_dir_names(root, cfg) - known


def todo_placeholders(reg: dict[str, dict]) -> list[str]:
    """Contexts with an unfinished field, marked TODO anywhere in the block.

    Deliberately key-agnostic: it works whatever the registry calls its cloud
    or account field. Checks plain string fields and list-of-string fields
    (such as rules) alike, since a TODO can sit inside either.
    """
    def _has_todo(v) -> bool:
        if isinstance(v, str):
            return "TODO" in v
        if isinstance(v, list):
            return any(isinstance(i, str) and "TODO" in i for i in v)
        return False

    return sorted(n for n, e in contexts(reg).items()
                  if any(_has_todo(v) for v in e.values()))
