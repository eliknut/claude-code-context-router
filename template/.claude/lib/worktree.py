"""Map a path inside a linked git worktree to the same path in its main checkout.

No git process: the guard calls this on every write, so it reads the two files git
writes for a linked worktree. The worktree has a `.git` FILE holding `gitdir: <path>`
(absolute, or relative to the worktree when worktree.useRelativePaths is on). That
folder holds `commondir` (normally `../..`, relative to it), naming the main repo's
`.git` folder; the main checkout is its parent. A `.git` DIRECTORY means a main
checkout. A `.git` file without `commondir` is a submodule and counts as no worktree.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path


def _resolve_from(base: Path, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    return Path(os.path.realpath(str(candidate)))


def _same(a: Path, b: Path) -> bool:
    """True when both name the same existing file or folder. A missing path is no match."""
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return False


def _read_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


@functools.lru_cache(maxsize=256)
def _locate(directory: str) -> tuple[str, str] | None:
    """(worktree top, main checkout) for the linked worktree holding `directory`.

    `directory` is already a real path. Cached per folder: a hook process is short
    lived, and the guard asks about the same few folders many times per command.
    """
    start = Path(directory)
    for folder in (start, *start.parents):
        dotgit = str(folder / ".git")
        # os.path, not Path: on 3.9 Path.is_dir() raises on an unsearchable folder,
        # and the guard must never fail on this lookup. Cannot tell means None.
        if os.path.isdir(dotgit):
            return None
        if not os.path.isfile(dotgit):
            continue
        dotgit = Path(dotgit)
        line = _read_line(dotgit)
        if not line.startswith("gitdir:"):
            return None
        gitdir = _resolve_from(folder, line[len("gitdir:"):].strip())
        common_raw = _read_line(gitdir / "commondir")
        if not common_raw:
            return None
        common = _resolve_from(gitdir, common_raw)
        if common.name != ".git":
            return None
        # git keeps a linked worktree's admin folder at <common>/worktrees/<name> and
        # writes <gitdir>/gitdir there pointing back at <top>/.git. Both must hold: a
        # .git file that fails either is a forgery (or a pruned or moved worktree) and
        # counts as no worktree. An admin folder outside the main repo cannot claim it.
        # Compared with samefile, so a case variant on a case-insensitive filesystem
        # still matches.
        if not _same(gitdir.parent, common / "worktrees"):
            return None
        back_raw = _read_line(gitdir / "gitdir")
        if not back_raw or not _same(_resolve_from(gitdir, back_raw), dotgit):
            return None
        return str(folder), str(common.parent)
    return None


def _start(path: str | os.PathLike) -> str:
    """The real folder to start the walk from: the path itself, or its parent when it
    is a file or does not exist yet."""
    real = os.path.realpath(str(path))
    return real if os.path.isdir(real) else os.path.dirname(real)


def linked(path: str | os.PathLike) -> tuple[Path, Path] | None:
    """(worktree top, main checkout) real paths when `path` is inside a linked
    worktree, else None. Never raises: an unreadable folder counts as None."""
    try:
        hit = _locate(_start(path))
    except OSError:
        return None
    return (Path(hit[0]), Path(hit[1])) if hit else None


def main_checkout(path: str | os.PathLike) -> Path | None:
    """The main checkout's real path when `path` is inside a linked worktree, else None."""
    hit = linked(path)
    return hit[1] if hit else None


def to_main_path(path: str | os.PathLike) -> Path:
    """`path` moved from its linked worktree into the main checkout; the real path
    unchanged when it is not in a linked worktree."""
    real = Path(os.path.realpath(str(path)))
    hit = linked(real)
    if hit is None:
        return real
    top, main = hit
    return Path(main) / real.relative_to(top)
