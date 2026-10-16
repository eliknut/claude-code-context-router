"""session_start.py run as a real process, with a temp HOME and a temp root, against
real git worktrees. Nothing here reads or writes the real ~/.claude."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "session_start.py"
sys.path.insert(0, str(HERE))
import gitfixture  # noqa: E402

REGISTRY = """# Contexts

<!-- registry -->

## acme
kind: tool
status: active
aliases: acme tool
cloudctx: none
home: repos/acme

## globex
kind: platform
status: active
aliases: modules
cloudctx: none
home: repos/shared
"""


@unittest.skipUnless(gitfixture.HAVE_GIT, "git not installed")
class SessionStartWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "root"
        self.home = self.tmp / "home"
        self.root.mkdir()
        self.home.mkdir()
        (self.root / "CONTEXTS.md").write_text(REGISTRY, encoding="utf-8")
        self.main = gitfixture.make_repo(self.root / "repos" / "acme")
        self.wt = gitfixture.add_worktree(self.main, self.tmp / "my workspaces" / "acme" / "rt",
                                          "dev/rt")
        self.marker = self.home / ".claude" / "ctx-state" / "s1"

    def run_hook(self, cwd, source="startup", sid="s1"):
        env = dict(os.environ, HOME=str(self.home), CLAUDE_PROJECT_DIR=str(self.root))
        payload = json.dumps({"session_id": sid, "source": source, "cwd": str(cwd)})
        proc = subprocess.run([sys.executable, str(HOOK)], input=payload, env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_a_worktree_of_a_context_home_picks_that_context(self):
        out = self.run_hook(self.wt)
        self.assertIn("worktree of repos/acme -> context acme", out)
        self.assertIn("Active context (from marker): acme", out)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "acme")

    def test_a_cwd_in_a_subfolder_of_the_worktree_picks_the_context(self):
        sub = self.wt / "internal" / "deep"
        sub.mkdir(parents=True)
        out = self.run_hook(sub)
        self.assertIn("worktree of repos/acme -> context acme", out)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "acme")

    def test_an_unowned_repo_prints_the_no_owner_line_and_writes_no_marker(self):
        orphan = gitfixture.make_repo(self.root / "repos" / "orphan")
        wt = gitfixture.add_worktree(orphan, self.tmp / "my workspaces" / "orphan" / "rt", "dev/o")
        out = self.run_hook(wt)
        self.assertIn(f"worktree of {orphan}: no context owns it", out)
        self.assertFalse(self.marker.exists())
        self.assertIn("Active context: none.", out)

    def test_an_existing_marker_is_kept(self):
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text("globex", encoding="utf-8")
        out = self.run_hook(self.wt, source="resume")
        self.assertIn("worktree of repos/acme -> context acme", out)
        self.assertIn("Active context (from marker): globex", out)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "globex")

    def test_resume_without_a_marker_prints_the_lines_and_writes_no_marker(self):
        out = self.run_hook(self.wt, source="resume")
        self.assertIn("worktree of repos/acme -> context acme", out)
        self.assertIn("Active context: unknown to the hooks.", out)
        self.assertFalse(self.marker.exists())

    def test_fork_without_a_marker_writes_no_marker(self):
        out = self.run_hook(self.wt, source="fork")
        self.assertIn("worktree of repos/acme -> context acme", out)
        self.assertIn("Active context: unknown to the hooks.", out)
        self.assertFalse(self.marker.exists())

    def test_compact_without_a_marker_writes_no_marker(self):
        self.run_hook(self.wt, source="compact")
        self.assertFalse(self.marker.exists())

    def test_clear_writes_the_marker(self):
        out = self.run_hook(self.wt, source="clear")
        self.assertIn("Active context (from marker): acme", out)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "acme")

    def test_an_existing_marker_is_kept_for_every_source(self):
        self.marker.parent.mkdir(parents=True)
        for source in ("startup", "clear", "resume", "compact", "fork"):
            self.marker.write_text("globex", encoding="utf-8")
            out = self.run_hook(self.wt, source=source)
            self.assertIn("Active context (from marker): globex", out, source)
            self.assertEqual(self.marker.read_text(encoding="utf-8"), "globex", source)

    def test_the_main_checkout_prints_no_worktree_line(self):
        out = self.run_hook(self.main)
        self.assertNotIn("worktree of", out)
        self.assertFalse(self.marker.exists())

    def test_an_unsafe_session_id_writes_no_marker(self):
        out = self.run_hook(self.wt, sid="../evil")
        self.assertIn("worktree of repos/acme -> context acme", out)
        self.assertFalse((self.home / ".claude" / "evil").exists())
        self.assertFalse((self.home / ".claude" / "ctx-state").exists()
                         and any((self.home / ".claude" / "ctx-state").iterdir()))

    def test_a_worktree_inside_the_root_is_not_used(self):
        wt = gitfixture.add_worktree(self.main, self.root / "inside-wt", "dev/in")
        out = self.run_hook(wt)
        self.assertNotIn("worktree of", out)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
