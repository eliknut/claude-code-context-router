"""Real git repos and linked worktrees for tests.

No mocks: the code under test reads the files git itself writes, so the tests make
git write them. Global and system git config are switched off so a hook, signing key
or template on this machine cannot change what gets written.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

HAVE_GIT = shutil.which("git") is not None

_ENV = dict(os.environ,
            GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com")


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=_ENV,
                          check=True, capture_output=True, text=True)
    return proc.stdout


def make_repo(path: Path) -> Path:
    """A main checkout with one empty commit, so a worktree can branch from it."""
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=path)
    return path


def add_worktree(main: Path, where: Path, branch: str, relative: bool = False) -> Path:
    """A linked worktree of `main` at `where` on a new `branch`, as worktree tools
    such as Orca make them."""
    where.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", "-q"]
    if relative:
        args.append("--relative-paths")
    git(*args, "-b", branch, str(where), cwd=main)
    return where
