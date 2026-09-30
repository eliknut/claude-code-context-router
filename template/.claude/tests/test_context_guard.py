"""Tests for hooks/context_guard.py, the IO layer around guard.decide().

The hook itself holds no decision logic, so what is tested here is the three things
that can still go wrong in a thin layer: the shape of what it prints, the merge that
turns an update decision into a complete tool_input, and its refusal to raise. The
script tests run the real file as a subprocess, because the fail-open wrapper lives in
main() and only a process can show that a raised exception still exits 0.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

CLAUDE = Path(__file__).resolve().parents[1]
HOOK = CLAUDE / "hooks" / "context_guard.py"
sys.path.insert(0, str(CLAUDE / "hooks"))
sys.path.insert(0, str(CLAUDE / "lib"))
import context_guard  # noqa: E402
import guard  # noqa: E402
import rootpath  # noqa: E402


def emitted(decision, tool_input):
    """What emit() prints, parsed. None when it printed nothing at all."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        context_guard.emit(decision, tool_input)
    text = buf.getvalue()
    return None if text == "" else json.loads(text)


class EmitTests(unittest.TestCase):
    def test_allow_emits_nothing_at_all(self):
        """Not an empty object, not a newline: nothing. Claude Code reads any stdout as
        an opinion, so an allow that prints is an allow that costs a parse on every call."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            context_guard.emit(guard.ALLOW, {"file_path": "/tmp/x.md"})
        self.assertEqual(buf.getvalue(), "")

    def test_deny_emits_the_permission_decision_and_its_reason(self):
        out = emitted(guard.Decision("deny", "globex is not acme"), {"file_path": "/x"})
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"],
                         "globex is not acme")

    def test_ask_is_emitted_the_same_way_as_deny(self):
        out = emitted(guard.Decision("ask", "confirm the tenant"), {"command": "az login"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"],
                         "confirm the tenant")

    def test_update_merges_into_the_original_tool_input(self):
        """The whole point of the merge. The decision rewrites `command` only, so
        `description` and `timeout` have to survive it: replacing tool_input instead of
        merging into a copy drops both, and the Bash call runs without them."""
        tool_input = {"command": "az account show",
                      "description": "Show the signed in account",
                      "timeout": 120}
        decision = guard.Decision("update", "Scoped to cloudctx 'globex'.",
                                  {"command": "cloudctx exec globex -- az account show"})
        updated = emitted(decision, tool_input)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["command"], "cloudctx exec globex -- az account show")
        self.assertEqual(updated["description"], "Show the signed in account")
        self.assertEqual(updated["timeout"], 120)

    def test_update_on_an_agent_call_keeps_subagent_type_and_model(self):
        """The subagent check has the same risk: the decision rewrites `prompt`, and losing
        subagent_type would silently send the work to a different agent."""
        tool_input = {"prompt": "do the thing", "subagent_type": "general-purpose",
                      "model": "opus", "description": "the thing"}
        decision = guard.Decision("update", "Prepended the 'globex' context block.",
                                  {"prompt": "[context: globex]\n---\ndo the thing"})
        updated = emitted(decision, tool_input)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["subagent_type"], "general-purpose")
        self.assertEqual(updated["model"], "opus")
        self.assertEqual(updated["description"], "the thing")
        self.assertTrue(updated["prompt"].startswith("[context: globex]"))

    def test_update_does_not_mutate_the_dict_it_was_given(self):
        """Merging into `tool_input` itself rather than into a copy would pass every
        assertion above while quietly editing the caller's data."""
        tool_input = {"command": "az account show", "description": "d"}
        decision = guard.Decision("update", "r", {"command": "cloudctx exec globex -- az account show"})
        emitted(decision, tool_input)
        self.assertEqual(tool_input, {"command": "az account show", "description": "d"})

    def test_update_carries_a_system_message_and_no_permission_decision(self):
        """An update deliberately omits permissionDecision, and that is load-bearing.

        The docs are explicit: "If you omit permissionDecision, the modified input still
        applies and flows through the normal permission evaluation"
        (code.claude.com/docs/en/agent-sdk/hooks). Adding "allow" here would suppress the
        confirmation prompt for the rewritten command; omitting it lets the user's own
        permission rules decide, evaluated against the rewritten input rather than what
        Claude proposed. Do not "fix" this by adding the field.
        """
        out = emitted(guard.Decision("update", "Scoped to cloudctx 'globex'.", {"command": "x"}),
                      {"command": "az account show"})
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        self.assertEqual(out["systemMessage"], "Scoped to cloudctx 'globex'.")

    def test_update_with_no_reason_omits_the_system_message(self):
        out = emitted(guard.Decision("update", "", {"command": "x"}), {"command": "y"})
        self.assertNotIn("systemMessage", out)
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"], {"command": "x"})


class ActiveContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.object(context_guard.rootpath, "ctx_state", return_value=self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_empty_session_id_means_no_context(self):
        self.assertEqual(context_guard.active_context(""), "")

    def test_a_missing_marker_means_no_context(self):
        """Reading it without the is_file() guard raises FileNotFoundError instead."""
        self.assertEqual(context_guard.active_context("never-marked"), "")

    def test_a_marker_is_read_and_stripped(self):
        """Without this, active_context could return "" unconditionally and every other
        test in this class would still pass."""
        (self.tmp / "sess-1").write_text("acme\n", encoding="utf-8")
        self.assertEqual(context_guard.active_context("sess-1"), "acme")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "running as root, chmod 000 does not deny a read")
    def test_an_unreadable_marker_means_no_context_rather_than_skipping_the_call(self):
        """Skipping leaves a bare cloud call allowed; no active context denies it. The
        difference is invisible for writes and inverted for Bash, so it must not depend on
        which detector happens to be routed."""
        marker = rootpath.ctx_state() / "unreadable-probe"
        marker.write_text("globex", encoding="utf-8")
        self.addCleanup(marker.unlink, missing_ok=True)
        self.addCleanup(os.chmod, marker, 0o600)
        os.chmod(marker, 0o000)
        self.assertEqual(context_guard.active_context("unreadable-probe"), "")


REGISTRY = """# Contexts

<!-- registry -->
## acme
kind: customer
cloudctx: acme (Azure, acme.example.com)
home: clients/acme

## globex
kind: customer
cloudctx: globex (Azure, globex.example.com)
home: clients/globex
"""


class ScriptTests(unittest.TestCase):
    """The real file, run as a process, against a temp root and a temp HOME.

    HOME is overridden because the marker directory is ~/.claude/ctx-state, so this is
    what keeps the tests off the live markers.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "root"
        self.home = self.tmp / "home"
        (self.root).mkdir()
        (self.home / ".claude" / "ctx-state").mkdir(parents=True)
        (self.root / "CONTEXTS.md").write_text(REGISTRY, encoding="utf-8")
        (self.home / ".claude" / "ctx-state" / "sess").write_text("acme", encoding="utf-8")

    def run_hook(self, payload):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        proc = subprocess.run([sys.executable, str(HOOK)],
                              input=payload, env=env, capture_output=True, text=True, timeout=30)
        return proc

    def test_a_null_command_fails_open_silently(self):
        """guard.decide() raises on {"command": None}, and main() must catch it.
        Exit 0 and nothing on stdout, so the call is neither blocked nor rewritten."""
        proc = self.run_hook(json.dumps({"session_id": "sess", "tool_name": "Bash",
                                         "tool_input": {"command": None}}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("context_guard skipped:", proc.stderr)
        # Names the actual failure, so this cannot pass because the temp registry was
        # unreadable or the module failed to import.
        self.assertIn("NoneType", proc.stderr)

    def test_a_sound_command_reaches_the_end_without_the_skip_line(self):
        """The control for the test above: same env, same script, nothing on stderr."""
        proc = self.run_hook(json.dumps({"session_id": "sess", "tool_name": "Bash",
                                         "tool_input": {"command": "echo hello"}}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_malformed_stdin_exits_zero_and_says_nothing(self):
        proc = self.run_hook("this is not json")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_a_write_into_another_context_is_denied_end_to_end(self):
        proc = self.run_hook(json.dumps({
            "session_id": "sess", "tool_name": "Write",
            "tool_input": {"file_path": str(self.root / "clients" / "globex" / "x.md")}}))
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("globex", reason)
        self.assertIn("acme", reason)

    def test_a_write_inside_the_active_context_emits_nothing_end_to_end(self):
        proc = self.run_hook(json.dumps({
            "session_id": "sess", "tool_name": "Write",
            "tool_input": {"file_path": str(self.root / "clients" / "acme" / "x.md")}}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_a_write_into_another_contexts_memory_is_denied_end_to_end(self):
        """The write check's memory clause, through the real hook and the real rootpath.memory_dir().

        The clause is unit tested against a fabricated memory root, so nothing else checks
        that memory_dir()'s slug agrees with the path the guard is handed. If the two ever
        drift, no memory path is ever recognised as memory: the check allows instead of
        denying, silently, with no error, and another context's memory stops being protected. Hence the real
        function here rather than a path spelled out by hand, computed under the same HOME
        and CLAUDE_PROJECT_DIR the hook process gets.
        """
        with mock.patch.dict(os.environ, {"HOME": str(self.home),
                                          "CLAUDE_PROJECT_DIR": str(self.root)}):
            memory = rootpath.memory_dir()
        target = memory / "contexts" / "globex" / "tenant-notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = self.run_hook(json.dumps({
            "session_id": "sess", "tool_name": "Write",
            "tool_input": {"file_path": str(target)}}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, "")
        self.assertNotEqual(proc.stdout, "",
                            "the hook said nothing, so it allowed a write into another "
                            "context's memory")
        out = json.loads(proc.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("globex", reason)
        self.assertIn("acme", reason)

    def test_a_bare_cloud_call_is_rewritten_and_keeps_its_other_fields_end_to_end(self):
        """The merge again, through the real entry point rather than through emit()."""
        proc = self.run_hook(json.dumps({
            "session_id": "sess", "tool_name": "Bash",
            "tool_input": {"command": "az account show",
                           "description": "Show the signed in account",
                           "timeout": 120}}))
        self.assertEqual(proc.returncode, 0)
        updated = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["command"], "cloudctx exec acme -- az account show")
        self.assertEqual(updated["description"], "Show the signed in account")
        self.assertEqual(updated["timeout"], 120)


    def write_kit_json(self, data):
        (self.root / ".claude").mkdir(exist_ok=True)
        (self.root / ".claude" / "kit.json").write_text(json.dumps(data), encoding="utf-8")

    def bash(self, command):
        return self.run_hook(json.dumps({"session_id": "sess", "tool_name": "Bash",
                                         "tool_input": {"command": command}}))

    def test_kit_json_names_a_custom_wrapper_end_to_end(self):
        self.write_kit_json({"cloud_wrapper": "credwrap"})
        proc = self.bash("az account show")
        self.assertEqual(proc.stderr, "")
        updated = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["command"], "credwrap exec acme -- az account show")

    def test_kit_json_false_turns_the_cloud_check_off_end_to_end(self):
        self.write_kit_json({"cloud_wrapper": False})
        proc = self.bash("az account show")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_kit_json_without_the_key_keeps_cloudctx_end_to_end(self):
        self.write_kit_json({"owner": "someone"})
        updated = json.loads(self.bash("az account show").stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["command"], "cloudctx exec acme -- az account show")

if __name__ == "__main__":
    unittest.main()
