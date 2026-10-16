"""install.sh and worktree mode, run as a real process against a temp root and a temp
HOME. Kit tree only: install.sh is not part of an install, so this skips there."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[3] / "install.sh"


@unittest.skipUnless(INSTALL.is_file(), "not in the kit tree (no install.sh)")
class InstallWorktreeModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "my_root.d"
        self.root.mkdir()

    def install(self, *args):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run(["bash", str(INSTALL), "--root", str(self.root), *args],
                              env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_the_dry_run_lists_the_worktree_files_and_writes_nothing(self):
        out = self.install()
        for rel in (".claude/scripts/claude-router.zsh", ".claude/lib/worktree.py",
                    ".claude/orca-settings.json.tmpl", ".claude/orca-settings.json"):
            self.assertRegex(out, rf"NEW\s+{re.escape(rel)}\n")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_apply_renders_absolute_paths_and_the_memory_slug(self):
        self.install("--apply")
        data = json.loads((self.root / ".claude" / "orca-settings.json").read_text(encoding="utf-8"))
        slug = re.sub(r"[^a-zA-Z0-9]", "-", str(self.root))
        self.assertEqual(data["autoMemoryDirectory"],
                         str(self.home / ".claude" / "projects" / slug / "memory"))
        command = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(command, f"python3 {self.root}/.claude/hooks/context_guard.py")

    def test_apply_prints_the_source_line_and_leaves_zshrc_alone(self):
        out = self.install("--apply")
        self.assertIn(f"  source {self.root}/.claude/scripts/claude-router.zsh\n", out)
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".claude").exists())

    def test_a_changed_generated_file_is_backed_up_and_replaced(self):
        self.install("--apply")
        generated = self.root / ".claude" / "orca-settings.json"
        fresh = generated.read_text(encoding="utf-8")
        generated.write_text("{}\n", encoding="utf-8")
        out = self.install("--apply")
        self.assertRegex(out, r"REPLACE\s+\.claude/orca-settings\.json\n")
        self.assertEqual(generated.read_text(encoding="utf-8"), fresh)
        backups = list((self.root / ".claude" / ".kit-backup").glob("*/.claude/orca-settings.json"))
        self.assertEqual([b.read_text(encoding="utf-8") for b in backups], ["{}\n"])

    def test_a_second_install_reports_the_generated_file_as_same(self):
        self.install("--apply")
        out = self.install()
        self.assertRegex(out, r"same\s+\.claude/orca-settings\.json\n")


if __name__ == "__main__":
    unittest.main()
