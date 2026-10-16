"""The claude wrapper in a real `zsh -f`, with a stub claude that records its argv and
the CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD value, against real git worktrees."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "claude-router.zsh"
sys.path.insert(0, str(HERE))
import gitfixture  # noqa: E402

ZSH = shutil.which("zsh")
DROPPED = ("claude-router: dropped --dangerously-skip-permissions "
           "(router rules need permission prompts)")
STUB = """#!/bin/sh
{
  printf 'ENV=%s\\n' "${CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD-unset}"
  for a in "$@"; do printf 'ARG=%s\\n' "$a"; done
} > "$STUB_LOG"
"""
RUN = 'source "$1" && cd "$2" || exit 9; shift 2; claude "$@"'


@unittest.skipUnless(ZSH and gitfixture.HAVE_GIT, "zsh or git not installed")
class ClaudeRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "root"
        self.main = gitfixture.make_repo(self.root / "repos" / "demo")
        self.wt = gitfixture.add_worktree(self.main, self.tmp / "my workspaces" / "demo" / "router test",
                                          "dev/router-test")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stub = self.bin / "claude"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.tmp / "stub.log"
        self.router = self.router_flags(self.root)
        # The shipped script has no root override: every test sources a copy installed
        # at <root>/.claude/scripts, so the root is derived exactly as in real use.
        self.script = self.install_script(self.root)

    @staticmethod
    def router_flags(root):
        return ["--add-dir", str(root), "--settings", str(root / ".claude" / "orca-settings.json")]

    def env(self, path=None, extra_env=None):
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
        git_dir = os.path.dirname(shutil.which("git"))
        env.update(PATH=path or f"{self.bin}:{git_dir}:/usr/bin:/bin", HOME=str(self.tmp),
                   STUB_LOG=str(self.log),
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        env.update(extra_env or {})
        return env

    def run_claude(self, cwd, *args, path=None, extra_env=None, script=None, run=RUN):
        proc = subprocess.run([ZSH, "-f", "-c", run, "zsh", str(script or self.script), str(cwd), *args],
                              env=self.env(path, extra_env),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        envval = lines[0].split("=", 1)[1]
        argv = [line.split("=", 1)[1] for line in lines[1:]]
        return argv, envval, proc.stderr

    def test_worktree_router_mode_adds_the_router_flags(self):
        argv, envval, err = self.run_claude(self.wt)
        self.assertEqual(argv, self.router)
        self.assertEqual(envval, "1")
        self.assertEqual(err, "")

    def test_a_subfolder_of_the_worktree_is_router_mode_too(self):
        sub = self.wt / "src" / "deep"
        sub.mkdir(parents=True)
        argv, envval, _ = self.run_claude(sub)
        self.assertEqual(argv, self.router)
        self.assertEqual(envval, "1")

    def test_the_main_checkout_runs_plain_claude(self):
        argv, envval, _ = self.run_claude(self.main, "-p", "hi")
        self.assertEqual(argv, ["-p", "hi"])
        self.assertEqual(envval, "unset")

    def test_outside_git_runs_plain_claude(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        argv, envval, _ = self.run_claude(plain)
        self.assertEqual(argv, [])
        self.assertEqual(envval, "unset")

    def test_a_worktree_of_a_repo_outside_the_root_runs_plain_claude(self):
        other = gitfixture.make_repo(self.tmp / "elsewhere" / "repo")
        wt = gitfixture.add_worktree(other, self.tmp / "my workspaces" / "other" / "rt", "dev/o")
        argv, envval, _ = self.run_claude(wt, "--resume", "abc")
        self.assertEqual(argv, ["--resume", "abc"])
        self.assertEqual(envval, "unset")

    def test_without_git_on_path_runs_plain_claude(self):
        argv, envval, _ = self.run_claude(self.wt, "-p", "hi", path=str(self.bin))
        self.assertEqual(argv, ["-p", "hi"])
        self.assertEqual(envval, "unset")

    def test_dangerously_skip_permissions_is_dropped_with_one_stderr_line(self):
        argv, envval, err = self.run_claude(self.wt, "--dangerously-skip-permissions", "-p", "hi")
        self.assertEqual(argv, self.router + ["-p", "hi"])
        self.assertEqual(err.splitlines(), [DROPPED])

    def test_dangerously_skip_permissions_is_kept_outside_router_mode(self):
        argv, _, err = self.run_claude(self.main, "--dangerously-skip-permissions")
        self.assertEqual(argv, ["--dangerously-skip-permissions"])
        self.assertEqual(err, "")

    def test_a_prompt_with_spaces_stays_one_argument(self):
        argv, _, _ = self.run_claude(self.wt, "-p", "which context is active?")
        self.assertEqual(argv, self.router + ["-p", "which context is active?"])

    def test_resume_is_passed_through(self):
        argv, _, _ = self.run_claude(self.wt, "--resume", "abc-123")
        self.assertEqual(argv, self.router + ["--resume", "abc-123"])

    def assert_refused(self, flag, *args):
        proc = subprocess.run([ZSH, "-f", "-c", RUN, "zsh", str(self.script), str(self.wt), *args],
                              env=self.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr.splitlines(), [
            f"claude-router: refused {flag} in a linked worktree (it would skip the router hooks);"
            " run 'command claude ...' to do it deliberately"])
        self.assertFalse(self.log.exists())

    def test_settings_space_form_is_refused(self):
        self.assert_refused("--settings", "-p", "hi", "--settings", "mine.json")

    def test_settings_equals_form_is_refused(self):
        self.assert_refused("--settings=mine.json", "--settings=mine.json")

    def test_bare_is_refused(self):
        self.assert_refused("--bare", "--bare")

    def test_safe_mode_is_refused(self):
        self.assert_refused("--safe-mode", "--safe-mode")

    def test_a_user_settings_flag_passes_through_outside_a_worktree(self):
        argv, envval, err = self.run_claude(self.main, "--settings", "x")
        self.assertEqual(argv, ["--settings", "x"])
        self.assertEqual(envval, "unset")
        self.assertEqual(err, "")

    def test_an_inherited_git_dir_does_not_turn_a_plain_folder_into_router_mode(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        argv, envval, _ = self.run_claude(plain, extra_env={"GIT_DIR": str(self.main / ".git")})
        self.assertEqual(argv, [])
        self.assertEqual(envval, "unset")

    def test_an_inherited_out_of_root_git_dir_does_not_hide_router_mode(self):
        other = gitfixture.make_repo(self.tmp / "elsewhere" / "repo")
        argv, envval, _ = self.run_claude(self.wt, extra_env={"GIT_DIR": str(other / ".git")})
        self.assertEqual(argv, self.router)
        self.assertEqual(envval, "1")

    def test_a_worktree_inside_the_root_runs_plain_claude(self):
        wt = gitfixture.add_worktree(self.main, self.root / "wt" / "inside", "dev/inside")
        argv, envval, _ = self.run_claude(wt, "-p", "hi")
        self.assertEqual(argv, ["-p", "hi"])
        self.assertEqual(envval, "unset")

    def test_stdout_is_restored_after_a_redirected_pipeline_outside_git(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        out = self.tmp / "out.md"
        proc = subprocess.run([ZSH, "-f", "-c",
                               'source "$1"; cd "$2"; echo hi | claude -p q > "$3"; echo marker',
                               "zsh", str(self.script), str(plain), str(out)],
                              env=self.env(), capture_output=True, text=True, timeout=30)
        self.assertIn("marker", proc.stdout)
        self.assertNotIn("marker", out.read_text(encoding="utf-8") if out.exists() else "")

    def test_allow_dangerously_skip_permissions_is_dropped(self):
        argv, _, err = self.run_claude(self.wt, "--allow-dangerously-skip-permissions", "-p", "hi")
        self.assertEqual(argv, self.router + ["-p", "hi"])
        self.assertEqual(err.splitlines(), [
            "claude-router: dropped --allow-dangerously-skip-permissions (router rules need permission prompts)"])

    def test_permission_mode_bypass_pair_is_dropped_on_one_line(self):
        argv, _, err = self.run_claude(self.wt, "--dangerously-skip-permissions",
                                       "--permission-mode", "bypassPermissions", "-p", "hi")
        self.assertEqual(argv, self.router + ["-p", "hi"])
        self.assertEqual(err.splitlines(), [
            "claude-router: dropped --dangerously-skip-permissions --permission-mode bypassPermissions "
            "(router rules need permission prompts)"])

    def test_permission_mode_bypass_equals_form_is_dropped(self):
        argv, _, err = self.run_claude(self.wt, "--permission-mode=bypassPermissions")
        self.assertEqual(argv, self.router)
        self.assertEqual(err.splitlines(), [
            "claude-router: dropped --permission-mode=bypassPermissions (router rules need permission prompts)"])

    def test_other_permission_modes_pass_through(self):
        argv, _, err = self.run_claude(self.wt, "--permission-mode", "plan", "--permission-mode=acceptEdits")
        self.assertEqual(argv, self.router + ["--permission-mode", "plan", "--permission-mode=acceptEdits"])
        self.assertEqual(err, "")

    # Root derivation: the root is three folders up from the sourced script,
    # <root>/.claude/scripts/claude-router.zsh.

    def install_script(self, root):
        dest = root / ".claude" / "scripts" / "claude-router.zsh"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, dest)
        return dest

    def test_the_root_is_derived_from_the_script_location(self):
        script = self.install_script(self.root)
        argv, envval, _ = self.run_claude(self.wt, "-p", "hi", script=script)
        self.assertEqual(argv, self.router + ["-p", "hi"])
        self.assertEqual(envval, "1")

    def test_a_script_sourced_through_a_symlink_uses_the_real_location(self):
        script = self.install_script(self.root)
        link = self.tmp / "dotfiles" / "claude-router.zsh"
        link.parent.mkdir()
        link.symlink_to(script)
        argv, _, _ = self.run_claude(self.wt, script=link)
        self.assertEqual(argv, self.router)

    def test_a_derived_root_that_does_not_hold_the_repo_runs_plain_claude(self):
        other_root = self.tmp / "other-root"
        script = self.install_script(other_root)
        argv, envval, _ = self.run_claude(self.wt, "-p", "hi", script=script)
        self.assertEqual(argv, ["-p", "hi"])
        self.assertEqual(envval, "unset")

    def test_the_shipped_script_has_no_root_override(self):
        self.assertNotIn("CLAUDE_ROUTER_ROOT", SCRIPT.read_text(encoding="utf-8").replace(
            "_CLAUDE_ROUTER_ROOT", ""))

    def test_an_earlier_alias_does_not_survive_sourcing(self):
        run = 'alias claude="claude --dangerously-skip-permissions"; ' + RUN
        argv, envval, err = self.run_claude(self.wt, "-p", "hi", run=run)
        self.assertEqual(argv, self.router + ["-p", "hi"])
        self.assertEqual(envval, "1")
        self.assertEqual(err, "")

    def test_a_ceiling_at_the_worktree_top_does_not_hide_router_mode(self):
        sub = self.wt / "src"
        sub.mkdir()
        argv, envval, _ = self.run_claude(sub, extra_env={"GIT_CEILING_DIRECTORIES": str(self.wt)})
        self.assertEqual(argv, self.router)
        self.assertEqual(envval, "1")


if __name__ == "__main__":
    unittest.main()
