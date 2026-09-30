import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import guard  # noqa: E402

ACME = "acme (Azure, acme.example.com), acme-labs (Azure, acme-labs.example.com)"


class CloudScopeTests(unittest.TestCase):
    def test_one_scope_drops_the_parenthesised_detail(self):
        self.assertEqual(guard.cloud_scopes({"cloudctx": "globex (Azure, globex.example.com)"}),
                         ["globex"])

    def test_two_scopes_split_on_the_comma_outside_the_parentheses(self):
        self.assertEqual(guard.cloud_scopes({"cloudctx": ACME}), ["acme", "acme-labs"])

    def test_a_naive_comma_split_would_have_given_four(self):
        """The regression this parser exists for: most real-world blocks break a naive split."""
        self.assertEqual(len(ACME.split(",")), 4)
        self.assertEqual(len(guard.cloud_scopes({"cloudctx": ACME})), 2)

    def test_none_yields_no_scopes(self):
        self.assertEqual(guard.cloud_scopes({"cloudctx": "none"}), [])

    def test_a_missing_field_yields_no_scopes(self):
        self.assertEqual(guard.cloud_scopes({}), [])

    def test_none_with_a_parenthesised_note_still_yields_no_scopes(self):
        """Annotated shape: "none" is never a scope name."""
        entry = {"cloudctx": "none (activate the specific customer before touching its tenant)"}
        self.assertEqual(guard.cloud_scopes(entry), [])

    def test_trailing_prose_after_the_parentheses_is_ignored(self):
        entry = {"cloudctx": "internal (Azure, a shared tenant) when a tenant is needed"}
        self.assertEqual(guard.cloud_scopes(entry), ["internal"])

    def test_the_cloud_spelling_is_accepted_too(self):
        self.assertEqual(guard.cloud_scopes({"cloud": "northwind (prod)"}), ["northwind"])


REG = {
    "portfolio": {"home": "clients", "owns": []},
    "acme": {"home": "clients/acme", "owns": []},
    "initech": {"home": "clients/initech", "owns": []},
    "globex": {"home": "clients/globex", "owns": []},
    "platform": {"home": "repos/my-org",
                 "owns": ["repos/my-org/modules", "repos/ops/shared-pipeline"]},
    "my-workspace": {"home": "repos/my-workspace", "owns": []},
    "example-tool": {"home": "repos/status-board", "owns": []},
    "stub": {"home": "none", "owns": []},
}


class TreeFixture:
    """A temp project root plus REG. Not a TestCase, so its cases run once, under
    whichever class inherits it, and not a second time under its own name."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Registered as a cleanup, not done in tearDown: cleanups run after tearDown and in
        # reverse order, so a test that registers a cwd restore later gets it back before the
        # tree it points into is removed. tearDown would delete the tree first and leave the
        # process sitting in a directory that no longer exists.
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.memory = Path(self.tmp.name) / "memory"
        for rel in ("clients/acme", "clients/initech", "clients/globex",
                    "repos/my-org/modules", "repos/ops/shared-pipeline",
                    "repos/status-board",
                    "repos/my-workspace/private/handoffs/clients/acme"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        (self.memory / "contexts" / "globex").mkdir(parents=True, exist_ok=True)

    def owner(self, rel: str) -> str:
        return guard.owner_of(str(self.root / rel), REG, self.root, self.memory)


class OwnerTests(TreeFixture, unittest.TestCase):
    def test_a_file_in_a_context_home_is_owned_by_it(self):
        self.assertEqual(self.owner("clients/acme/docs/x.md"), "acme")

    def test_the_longest_matching_home_wins_over_a_shorter_one(self):
        self.assertEqual(self.owner("clients/acme/HANDOFF.md"), "acme")

    def test_a_file_directly_in_the_shorter_home_belongs_to_that_context(self):
        self.assertEqual(self.owner("clients/HANDOFF.md"), "portfolio")

    def test_an_owns_entry_counts_as_ownership(self):
        self.assertEqual(self.owner("repos/my-org/modules/vnet/main.tf"), "platform")

    def test_an_owns_entry_outside_the_home_counts_as_ownership(self):
        """The case the nested owns entry above cannot prove: two contexts (umbrella,
        example-tool) own a path outside their home, so dropping owns handling must fail here."""
        self.assertEqual(self.owner("repos/ops/shared-pipeline/deploy.yml"), "platform")

    def test_a_backup_mirror_belongs_to_the_context_whose_home_contains_it(self):
        self.assertEqual(
            self.owner("repos/my-workspace/private/handoffs/clients/acme/HANDOFF.md"),
            "my-workspace")

    def test_a_file_in_no_home_is_owned_by_nobody(self):
        self.assertEqual(self.owner("CONTEXTS.md"), "")

    def test_a_home_of_none_never_owns_anything(self):
        self.assertEqual(self.owner("none/x.md"), "")

    def test_a_path_outside_the_root_is_owned_by_nobody(self):
        self.assertEqual(guard.owner_of("/etc/hosts", REG, self.root, self.memory), "")

    def test_a_memory_file_is_owned_by_the_context_it_sits_under(self):
        target = str(self.memory / "contexts" / "globex" / "tenant.md")
        self.assertEqual(guard.owner_of(target, REG, self.root, self.memory), "globex")

    def test_a_symlink_cannot_be_used_to_leave_a_context(self):
        link = self.root / "clients" / "initech" / "shortcut"
        os.symlink(self.root / "clients" / "globex", link)
        self.assertEqual(self.owner("clients/initech/shortcut/notes.md"), "globex")


class WriteDecisionTests(TreeFixture, unittest.TestCase):
    """Same tree and REG as OwnerTests, one level up: the decision, not the lookup."""

    def decide(self, tool_name, tool_input, active):
        return guard.decide(tool_name, tool_input, active, REG, self.root, self.memory)

    def test_a_write_inside_the_active_context_is_allowed(self):
        d = self.decide("Write", {"file_path": str(self.root / "clients/acme/x.md")}, "acme")
        self.assertEqual(d.action, "allow")

    def test_a_write_into_another_context_is_denied(self):
        d = self.decide("Write", {"file_path": str(self.root / "clients/globex/x.md")}, "acme")
        self.assertEqual(d.action, "deny")
        self.assertIn("globex", d.reason)
        self.assertIn("acme", d.reason)

    def test_the_deny_reason_names_an_owner_whose_name_is_absent_from_the_path(self):
        """example-tool lives at repos/status-board in this fixture, so its name cannot
        reach the reason by being echoed back out of the target path."""
        d = self.decide("Write", {"file_path": str(self.root / "repos/status-board/x.md")}, "acme")
        self.assertEqual(d.action, "deny")
        self.assertIn("example-tool", d.reason)

    def test_an_edit_into_another_context_is_denied(self):
        d = self.decide("Edit", {"file_path": str(self.root / "clients/globex/x.md")}, "acme")
        self.assertEqual(d.action, "deny")

    def test_a_notebook_edit_uses_its_own_path_field(self):
        d = self.decide("NotebookEdit", {"notebook_path": str(self.root / "clients/globex/x.ipynb")}, "acme")
        self.assertEqual(d.action, "deny")

    def test_a_write_into_another_contexts_memory_is_denied(self):
        target = str(self.memory / "contexts" / "globex" / "tenant.md")
        d = self.decide("Write", {"file_path": target}, "acme")
        self.assertEqual(d.action, "deny")

    def test_a_write_owned_by_nobody_is_allowed(self):
        d = self.decide("Write", {"file_path": str(self.root / "CONTEXTS.md")}, "acme")
        self.assertEqual(d.action, "allow")

    def test_no_active_context_means_no_opinion_on_writes(self):
        d = self.decide("Write", {"file_path": str(self.root / "clients/globex/x.md")}, "")
        self.assertEqual(d.action, "allow")

    def test_a_missing_path_field_is_allowed(self):
        self.assertEqual(self.decide("Write", {}, "acme").action, "allow")

    def test_a_missing_path_field_is_allowed_even_when_the_cwd_is_inside_a_context(self):
        """The case the test above cannot prove: realpath("") is the cwd, so without the
        empty-target guard a path-less Write from inside another context would deny."""
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.root / "clients" / "globex")
        self.assertEqual(self.decide("Write", {}, "acme").action, "allow")

    def test_an_unmatched_tool_is_allowed(self):
        d = self.decide("Read", {"file_path": str(self.root / "clients/globex/x.md")}, "acme")
        self.assertEqual(d.action, "allow")


class ShellTests(unittest.TestCase):
    def test_a_plain_command_is_one_segment(self):
        self.assertEqual(guard.split_segments("az group list"), ["az group list"])

    def test_operators_split_segments(self):
        self.assertEqual(guard.split_segments("terraform init && terraform plan | tee out; echo done"),
                         ["terraform init", "terraform plan", "tee out", "echo done"])

    def test_a_separator_inside_quotes_does_not_split(self):
        self.assertEqual(guard.split_segments("az group list --query \"a;b\""),
                         ["az group list --query \"a;b\""])

    def test_leading_env_assignments_are_stripped(self):
        self.assertEqual(guard.segment_command("AZURE_CONFIG_DIR=/tmp FOO=1 az account show"), "az")

    def test_a_path_prefixed_command_is_reduced_to_its_basename(self):
        self.assertEqual(guard.segment_command("/opt/homebrew/bin/terraform plan"), "terraform")

    def test_a_bare_cloud_call_is_a_hit(self):
        self.assertEqual(guard.cloud_hits("az group list"), ["az group list"])

    def test_a_scoped_cloud_call_is_not_a_hit(self):
        self.assertEqual(guard.cloud_hits("cloudctx exec globex -- az group list"), [])

    def test_a_cloud_name_as_an_argument_is_not_a_hit(self):
        self.assertEqual(guard.cloud_hits("echo az && grep -r aws ."), [])

    def test_every_cloud_segment_in_a_chain_is_a_hit(self):
        self.assertEqual(len(guard.cloud_hits("terraform init && terraform apply")), 2)

    def test_a_scoped_and_an_unscoped_call_in_one_chain_hits_only_the_unscoped_one(self):
        """The scoped-call test above cannot fail: that segment's command is cloudctx, which
        never reaches the CLOUD_COMMANDS test at all. This one proves hits are decided per
        segment, so a chain that scopes one call and forgets the next is caught."""
        self.assertEqual(guard.cloud_hits("cloudctx exec globex -- az group list && az vm list"),
                         ["az vm list"])

    def test_every_cloud_command_is_detected(self):
        """aws and terragrunt were unexercised, so dropping either from CLOUD_COMMANDS was
        unkillable. Two tenants here are AWS accounts, so that gap had teeth."""
        for command in ("az group list", "aws s3 ls", "terraform plan", "terragrunt apply"):
            with self.subTest(command=command):
                self.assertEqual(guard.cloud_hits(command), [command])

    def test_a_quoted_command_name_is_still_a_hit(self):
        self.assertEqual(guard.cloud_hits('"az" group list'), ['"az" group list'])

    def test_a_path_that_merely_contains_a_cloud_name_is_not_a_hit(self):
        """Rules out matching cloud names as bare words anywhere: this workspace's IaC repos
        are named terraform-*, and prompting on every ls of one would get the guard disabled."""
        self.assertEqual(guard.cloud_hits("ls repos/my-org/terraform-modules"), [])
        self.assertEqual(guard.cloud_hits("cat terraform.tfvars"), [])

    def test_a_cloud_name_inside_quoted_prose_is_not_a_hit(self):
        """Found live, on the first command after the matcher was widened.

        cloud_hits splits each segment again on parens to catch command substitution, and
        that split cuts through quoted strings. The fragment left behind has an unbalanced
        quote, which sent segment_command down its all-token fallback, which scans every
        token for a cloud name. So ordinary prose mentioning a cloud CLI inside a quoted
        string that also contained a paren read as a cloud call. With no active context
        that is a hard deny, which blocked the very command that sets the marker.
        """
        for command in ('echo "--- a bare az in THIS context (cloudctx: none) ---"',
                        'echo "running terraform plan (prod)"'):
            with self.subTest(command=command):
                self.assertEqual(guard.cloud_hits(command), [])

    def test_a_genuinely_unparseable_segment_still_scans_every_token(self):
        """The fallback above is only skipped for parts we broke ourselves. A segment whose
        own quoting is already broken keeps the conservative all-token scan."""
        command = "FOO='a b' az group list --query \"x"
        self.assertEqual(guard.cloud_hits(command), [command])

    def test_a_malformed_non_cloud_command_is_not_a_hit(self):
        """The fallback fails closed only for cloud names. Treating every unparseable
        segment as a hit would prompt on ordinary broken commands."""
        self.assertEqual(guard.cloud_hits('echo "unterminated'), [])
        # Pins the all-token scan: the old fallback took the first non-assignment token,
        # which here is "b", so this unscoped az call was invisible.
        command = 'FOO=\'a b\' az group list --query "x'
        self.assertEqual(guard.cloud_hits(command), [command])

    def test_a_backtick_substitution_is_not_simple(self):
        """Reaches a _NOT_SIMPLE marker that segment count cannot. The chain and heredoc cases
        are both decided by segment count before any marker is consulted."""
        self.assertFalse(guard.is_simple("az vm show --name `cat name.txt`"))

    def test_a_backgrounded_command_is_not_simple(self):
        """Same again, for a single trailing & which does not split a segment."""
        self.assertFalse(guard.is_simple("az group list &"))

    def test_a_single_plain_command_is_simple(self):
        self.assertTrue(guard.is_simple("az group list -o table"))

    def test_a_chain_is_not_simple(self):
        self.assertFalse(guard.is_simple("az group list && az vm list"))

    def test_a_semicolon_chain_is_not_simple(self):
        """Kills is_simple's segment-count guard. The && and heredoc cases cannot: their
        separators are also _NOT_SIMPLE markers, so removing the guard still returns False."""
        self.assertFalse(guard.is_simple("az group list; az vm list"))

    def test_a_piped_command_is_not_simple(self):
        """Same, for the pipe, which is the common real shape (az ... | jq) and whose
        separator is likewise absent from _NOT_SIMPLE."""
        self.assertFalse(guard.is_simple("az group list | head"))

    def test_command_substitution_is_not_simple(self):
        self.assertFalse(guard.is_simple("az vm show --name $(cat name.txt)"))

    def test_a_redirection_is_not_simple(self):
        self.assertFalse(guard.is_simple("az group list > out.json"))

    def test_a_heredoc_is_not_simple(self):
        self.assertFalse(guard.is_simple("az deployment create <<EOF\nx\nEOF"))

    def test_an_escaped_quote_cannot_hide_a_second_command(self):
        """The parser has no backslash awareness, so an escaped quote used to close its
        quote state early and swallow a real separator. Rewriting that string would have
        run the second command outside cloudctx."""
        self.assertFalse(guard.is_simple('az foo --query "a\\"b" ; az vm delete --yes'))

    def test_an_even_parity_escape_cannot_hide_a_second_command(self):
        """The backslash marker's own test. The escaped-quote case above has odd quote
        parity, so the parity check rejects it alone and would still pass with the marker
        removed. This input has even parity and one parser segment, but bash runs it as two
        commands, so only the marker stops it being rewritten."""
        self.assertFalse(guard.is_simple('az a \\" ; az vm delete --yes \\"'))

    def test_an_unterminated_quote_is_not_simple(self):
        self.assertFalse(guard.is_simple('az foo --query "a ; az vm delete'))

    def test_a_realistic_quoted_query_is_still_simple(self):
        """Guards the fix against over-rejecting: this shape is common, and if it stopped
        being rewritable every real cloud call would become a prompt."""
        self.assertTrue(guard.is_simple('az vm list --query "[?name==\'x\']" -o json'))


CLOUD_REG = {
    "globex": {"home": "clients/globex", "owns": [], "cloudctx": "globex (Azure, globex.example.com)"},
    "acme": {"home": "clients/acme", "owns": [], "cloudctx": ACME},
    "my-workspace": {"home": "repos/my-workspace", "owns": [], "cloudctx": "none"},
}


class BashDecisionTests(unittest.TestCase):
    def decide(self, command, active):
        return guard.decide("Bash", {"command": command}, active, CLOUD_REG,
                            Path("/root"), Path("/memory"))

    def test_a_command_with_no_cloud_call_is_allowed(self):
        self.assertEqual(self.decide("ls -la", "globex").action, "allow")

    def test_an_already_scoped_command_is_allowed(self):
        self.assertEqual(self.decide("cloudctx exec globex -- az group list", "globex").action, "allow")

    def test_a_simple_bare_command_is_rewritten(self):
        d = self.decide("az group list -o table", "globex")
        self.assertEqual(d.action, "update")
        self.assertEqual(d.updated_input["command"], "cloudctx exec globex -- az group list -o table")
        self.assertIn("globex", d.reason)

    def test_a_leading_env_assignment_is_denied_rather_than_rewritten(self):
        """is_simple says True for this, so only the assignment branch stops it. Rewriting
        it would produce an argv whose first element is the assignment, which cannot exec."""
        d = self.decide("AZURE_CONFIG_DIR=/tmp az account show", "globex")
        self.assertEqual(d.action, "deny")
        self.assertIn("env ", d.reason)

    def test_a_quoted_assignment_value_with_a_space_is_still_a_hit(self):
        """The bypass fix round 1 missed. A value with a space is the only reason to quote
        one, and a plain split lands mid-value so the real command is never seen."""
        for command in ("AZURE_CONFIG_DIR='/tmp/my dir' az account show",
                        'AWS_PROFILE="prod profile" aws s3 ls s3://bucket'):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, "globex").action, "deny")

    def test_a_line_continuation_does_not_hide_the_command(self):
        """A backslash-newline is whitespace to the shell, not a separator. Splitting on the
        raw newline cut the command in half, shlex then failed on the trailing backslash,
        and the fallback landed mid-value: the call ran unscoped with no prompt."""
        command = "AZURE_CONFIG_DIR='/tmp/my dir' az account show \\\n  --output json"
        self.assertEqual(self.decide(command, "globex").action, "deny")
        # Pins the continuation join itself: without it this is two segments, and the
        # all-token fallback below would be the only thing left catching the command.
        self.assertEqual(len(guard.split_segments(command)), 1)

    def test_a_cloud_call_in_a_command_position_is_still_a_hit(self):
        """segment_command reads the first token as the command, but a shell starts a new
        command after $( , a backtick, a paren, and after keywords like if and do. Each of
        these ran completely unscoped before this was closed."""
        for command in ("RESULT=$(az account show --query id -o tsv)",
                        "echo `az account show`",
                        "if az account show; then echo ok; fi",
                        "for x in a b; do az group list; done",
                        "sudo az account show"):
            with self.subTest(command=command):
                self.assertNotEqual(self.decide(command, "globex").action, "allow")

    def test_a_cloud_call_through_sudo_is_denied_rather_than_claiming_to_be_scoped(self):
        """cloudctx scopes by exporting environment variables and sudo resets the
        environment, so rewriting this would run az unscoped while reporting it as scoped
        to globex. A false assurance is worse than no guard."""
        d = self.decide("sudo az account show", "globex")
        self.assertEqual(d.action, "deny")
        self.assertIn("unscoped", d.reason)

    def test_a_cloud_call_reached_through_a_wrapper_is_denied_instead_of_being_wrapped(self):
        """The spec only rewrites a single simple segment whose OWN first token is the
        cloud CLI. segment_command() looks through wrapper words on purpose, so that
        `sudo az ...` is DETECTED, and the rewrite must not inherit that: cloudctx exec
        runs its argv with no shell, so `exec`, `command` and `!` die, and every one of
        these produces a line that does something other than what was typed."""
        for command in ("time az group list",
                        "command az group list",
                        "exec az group list",
                        "! az group list",
                        "nohup az group list",
                        "xargs az group show",
                        "env FOO=1 az group list"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, "globex").action, "deny")

    def test_env_before_the_cloud_call_is_never_rewritten_into_a_false_scope(self):
        """The one wrapper that is not merely broken. cloudctx scopes by exporting
        AZURE_CONFIG_DIR; env re-sets it after cloudctx has, so the rewritten line runs
        against /tmp/x while the hook reports "Scoped to cloudctx 'globex'". A rewrite that
        claims a scope it does not achieve is the outcome the spec rules out."""
        d = self.decide("env AZURE_CONFIG_DIR=/tmp/x az account show", "globex")
        self.assertEqual(d.action, "deny")
        self.assertNotIn("Scoped to", d.reason)

    def test_the_wrapper_gate_still_rewrites_a_plain_call_at_an_absolute_path(self):
        """The gate reads the first token only, so it has to compare on the basename or
        an absolute path stops being rewritten and every such call turns into a prompt."""
        d = self.decide("/opt/homebrew/bin/az group list", "globex")
        self.assertEqual(d.action, "update")
        self.assertEqual(d.updated_input["command"],
                         "cloudctx exec globex -- /opt/homebrew/bin/az group list")

    def test_a_chained_command_is_denied_instead_of_rewritten(self):
        d = self.decide("terraform init && terraform plan", "globex")
        self.assertEqual(d.action, "deny")
        self.assertIn("globex", d.reason)

    def test_a_multi_scope_context_is_denied_and_never_picks_one(self):
        d = self.decide("az group list", "acme")
        self.assertEqual(d.action, "deny")
        self.assertIn("acme-labs", d.reason)

    def test_a_context_with_no_cloud_scope_asks(self):
        d = self.decide("terraform fmt", "my-workspace")
        self.assertEqual(d.action, "ask")

    def test_no_active_context_denies_a_cloud_call(self):
        d = self.decide("az group list", "")
        self.assertEqual(d.action, "deny")
        self.assertIn("section 3", d.reason)

    def test_the_escape_hatch_allows_the_command_unchanged(self):
        self.assertEqual(self.decide("az --version  #noctx", "globex").action, "allow")

    def test_the_escape_hatch_works_with_no_active_context(self):
        self.assertEqual(self.decide("az --version  #noctx", "").action, "allow")

    def test_the_escape_hatch_does_not_fire_from_inside_a_quoted_value(self):
        """#noctx was matched as a substring, so a tag literally named #noctx disabled the
        guard for that command."""
        d = self.decide('az tag create --name "#noctx" --value x', "globex")
        self.assertNotEqual(d.action, "allow")


RULES_REG = {
    "globex": {"home": "clients/globex", "owns": [],
              "cloudctx": "globex (Azure, globex.example.com)",
              "rules": ["read-only by default", "never touch prod without approval"]},
    "stub": {"home": "none", "owns": [], "cloudctx": "none", "rules": []},
}


class AgentDecisionTests(unittest.TestCase):
    def decide(self, tool_input, active):
        return guard.decide("Agent", tool_input, active, RULES_REG, Path("/root"), Path("/memory"))

    def test_the_header_carries_the_context_home_cloud_and_rules(self):
        d = self.decide({"prompt": "Do the thing", "subagent_type": "general-purpose"}, "globex")
        self.assertEqual(d.action, "update")
        prompt = d.updated_input["prompt"]
        self.assertTrue(prompt.startswith("[context: globex]"))
        self.assertIn("clients/globex", prompt)
        self.assertIn("cloud: globex", prompt)
        self.assertIn("Never write into another context's home, handoff or memory folder",
                      prompt)
        self.assertIn("clients/globex, plus its own memory folder", prompt)
        self.assertIn("read-only by default", prompt)
        self.assertIn("never touch prod without approval", prompt)
        self.assertTrue(prompt.rstrip().endswith("Do the thing"))

    def test_the_task_tool_name_is_handled_too(self):
        d = guard.decide("Task", {"prompt": "Do the thing"}, "globex", RULES_REG,
                         Path("/root"), Path("/memory"))
        self.assertEqual(d.action, "update")

    def test_a_fork_is_left_alone_because_it_inherits_the_conversation(self):
        d = self.decide({"prompt": "Do the thing", "subagent_type": "fork"}, "globex")
        self.assertEqual(d.action, "allow")

    def test_no_active_context_injects_nothing(self):
        d = self.decide({"prompt": "Do the thing"}, "")
        self.assertEqual(d.action, "allow")

    def test_injection_is_idempotent(self):
        first = self.decide({"prompt": "Do the thing"}, "globex").updated_input["prompt"]
        second = self.decide({"prompt": first}, "globex")
        self.assertEqual(second.action, "allow")

    def test_injection_is_idempotent_even_with_leading_whitespace(self):
        """The check lstrips before looking for the marker, so a prompt whose header is
        preceded by a newline or indentation is still recognised and does not collect a
        second copy."""
        first = self.decide({"prompt": "Do the thing"}, "globex").updated_input["prompt"]
        self.assertEqual(self.decide({"prompt": "\n  " + first}, "globex").action, "allow")

    def test_a_stale_or_coincidental_context_tag_does_not_suppress_injection(self):
        """The marker used to be the bare string "[context:", so any prompt starting with
        it silently skipped injection: another context's header, a stale one from before a
        switch, or just a prompt opening with a context tag, which this router asks every
        reply to do."""
        stale = self.decide({"prompt": "Do the thing"}, "globex").updated_input["prompt"]
        for prompt, active in ((stale, "stub"),
                               ("[context: stub] as discussed\nDo it", "globex"),
                               ("[context: globex] pick up the vnet cleanup", "globex")):
            with self.subTest(active=active):
                d = self.decide({"prompt": prompt}, active)
                self.assertEqual(d.action, "update")
                self.assertTrue(d.updated_input["prompt"].startswith(f"[context: {active}]"))

    def test_the_write_line_names_owned_paths_outside_the_home(self):
        """umbrella and example-tool both own a path outside their home in this fixture, and the
        guard permits writes there, so a header naming only the home understates the set.

        The line states the prohibition D1 actually enforces and lists the owned paths as
        information: phrasing it as "write only inside <paths>" was narrower than D1, which
        also allows the workspace files no context owns and this context's own memory."""
        entry = {"home": "clients/umbrella", "owns": ["repos/test-harness"],
                 "cloudctx": "umbrella (Azure, x)", "rules": []}
        header = guard.context_header("umbrella", entry)
        self.assertIn("Never write into another context's home, handoff or memory folder",
                      header)
        self.assertIn("clients/umbrella, repos/test-harness", header)

    def test_a_context_with_no_home_omits_the_write_line(self):
        d = self.decide({"prompt": "Do the thing"}, "stub")
        self.assertNotIn("Never write into another context", d.updated_input["prompt"])

    def test_the_original_prompt_survives_verbatim(self):
        d = self.decide({"prompt": "line one\nline two"}, "globex")
        self.assertIn("line one\nline two", d.updated_input["prompt"])


class QuotedParenthesisTests(unittest.TestCase):
    """Regression: a parenthesis inside a quoted argument was split on, every piece then
    failed to parse and was skipped, and the cloud call ran unscoped."""

    def decide(self, command):
        return guard.decide("Bash", {"command": command}, "globex", CLOUD_REG,
                            Path("/root"), Path("/memory"))

    def test_a_jmespath_function_in_a_quoted_query_is_rewritten_not_allowed(self):
        for command in ("az vm list --query \"[?contains(name,'web')]\"",
                        'aws ec2 describe-instances --query "length(Reservations)"',
                        "aws ec2 describe-instances --query 'sort_by(Reservations, &LaunchTime)'",
                        'terraform output -json "lookup(x)"',
                        'terragrunt run-all plan --terragrunt-include-dir "(a)"'):
            with self.subTest(command=command):
                d = self.decide(command)
                self.assertEqual(d.action, "update")
                self.assertEqual(d.updated_input["command"], f"cloudctx exec globex -- {command}")

    def test_a_genuine_subshell_or_substitution_is_still_caught(self):
        for command in ("(az account show)", "$(az account show)", "echo $(az account show)",
                        "echo `az account show`",
                        'echo "$(az account show --query "length(x)")"',
                        'cd x && (terraform plan)'):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command).action, "deny")

    def test_a_cloud_name_inside_literal_quoted_text_is_not_a_hit(self):
        for command in ('echo "deploying terraform (prod)"', "echo '$(az account show)'",
                        'git commit -m "fix (az) query"'):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command).action, "allow")

    def test_a_quoted_parenthesis_does_not_hide_an_unquoted_redirection(self):
        self.assertEqual(self.decide('az vm list --query "length(x)" > out.json').action, "deny")


class CaseInsensitivePathTests(TreeFixture, unittest.TestCase):
    """Regression: on a case-insensitive filesystem (APFS, NTFS) realpath keeps the case
    it was given, so Clients/Globex slipped past a comparison against clients/globex. The
    detector is patched so the case-insensitive branch runs on any filesystem."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(guard, "_case_insensitive", lambda path: True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def decide(self, target, active="acme"):
        return guard.decide("Write", {"file_path": str(target)}, active, REG, self.root, self.memory)

    def test_another_contexts_home_in_any_case_is_denied(self):
        for rel in ("Clients/Globex/x.md", "CLIENTS/GLOBEX/x.md", "clients/Globex/x.md"):
            with self.subTest(rel=rel):
                d = self.decide(self.root / rel)
                self.assertEqual(d.action, "deny")
                self.assertIn("'globex'", d.reason)

    def test_the_active_contexts_own_home_in_another_case_is_allowed(self):
        self.assertEqual(self.decide(self.root / "Clients/Acme/x.md").action, "allow")

    def test_another_contexts_memory_in_any_case_is_denied(self):
        self.assertEqual(self.decide(self.memory / "Contexts" / "Globex" / "x.md").action, "deny")

    def test_the_active_contexts_own_memory_in_another_case_is_allowed(self):
        self.assertEqual(self.decide(self.memory / "contexts" / "ACME" / "x.md").action, "allow")

    def test_a_case_sensitive_filesystem_keeps_the_exact_comparison(self):
        with mock.patch.object(guard, "_case_insensitive", lambda path: False):
            self.assertEqual(self.owner("Clients/Globex/x.md"), "")



class CaseDetectorTests(TreeFixture, unittest.TestCase):
    def test_the_detector_agrees_with_a_direct_probe(self):
        real = os.path.realpath(str(self.root))
        flipped = real.swapcase()
        expected = os.path.exists(flipped) and os.path.samefile(real, flipped)
        self.assertEqual(guard._case_insensitive.__wrapped__(real), expected)


if __name__ == "__main__":
    unittest.main()
