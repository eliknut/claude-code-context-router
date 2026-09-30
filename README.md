# Context router kit

One folder, many contexts. Claude Code resolves which context you are in before it does anything,
loads that context's notes and handoff, keeps its writes and cloud commands inside that context,
and writes the handoff back when you stop.

A context is anything you switch between: a client, a shared codebase, an internal tool, a
personal project. Each one is a block in `CONTEXTS.md` with a name, aliases, a home folder, an
optional cloud scope and its own standing rules.

Standard library Python (3.9 or newer) and bash. No dependencies.

## How it works

```
 session starts in <root>
        |
        v
 SessionStart hook ----> prints registry digest (names + aliases) and recent handoffs
        |
 first message: "northwind deploy"
        |
        v
 CLAUDE.md router ----> resolve: one match? activate. none or several? ask.
        |
        v
 activate: read block in CONTEXTS.md -> <home>/CLAUDE.md -> <home>/HANDOFF.md
           -> memory/contexts/<ctx>/INDEX.md -> set_context.sh writes the marker
        |
        v
 every tool call ----> PreToolUse context_guard.py (reads the marker)
        |                 Write/Edit into another context's folder ... deny
        |                 bare az/aws/terraform/terragrunt ........... rewrite to
        |                                                  cloudctx exec <scope> -- <cmd>
        |                 Agent/Task prompt .......................... context block prepended
        v
 "done for today" ---> UserPromptSubmit hook prints SAVE TRIGGER, Claude rewrites HANDOFF.md
        |
 session ends -------> SessionEnd hook logs a line under Recent in the handoff
```

## Layout

```
install.sh            installer (dry run by default)
VERSION
template/
  CLAUDE.md           the router: resolve, activate, scope, where writes go, handoff
  CONTEXTS.md         registry: example contexts, an ignore block, an example IaC layout table
  kit.json.example    optional config
  statusline.sh       optional status line showing the active context
  .claude/
    settings.json     hook wiring (SessionStart, UserPromptSubmit, SessionEnd, PreToolUse)
    hooks/            session_start.py, user_prompt_submit.py, session_end.py,
                      context_guard.py, save-phrases.txt
    lib/              rootpath.py, registry.py, handoff.py, guard.py
    scripts/          set_context.sh, registry_check.py
    skills/           context-bootstrap, new-context
    tests/            unittest suite for all of the above
```

Nothing hardcodes a path. `rootpath.py` derives the root from `CLAUDE_PROJECT_DIR` or from its
own location, and the memory folder from the root, the same way Claude Code names it.

## The guard

`.claude/hooks/context_guard.py` runs before every `Write`, `Edit`, `NotebookEdit`, `Bash`,
`Agent` and `Task` call. All decisions are pure functions in `.claude/lib/guard.py`.

- **Cross-context writes.** A write whose target (resolved through symlinks) sits in another
  context's `home` or `owns` folders, or in another context's memory folder, is denied. Files
  that no context owns (`CLAUDE.md`, `CONTEXTS.md`, `.claude/`) are allowed.
- **Cloud CLI scoping.** A bare `az`, `aws`, `terraform` or `terragrunt` call that is one plain
  command is rewritten to `cloudctx exec <scope> -- <command>`. Anything it cannot rewrite
  safely is denied with the scoped form to use instead: pipes, substitutions, redirects, a
  leading `VAR=value`, `sudo`, wrappers like `time` or `env`, a context with several scopes, or
  no active context. For a context with `cloudctx: none` it asks. `#noctx` as a bare token
  opts one command out.
- **Subagent briefing.** A delegated subagent starts blank, so the active context's block (home,
  scope, standing rules, the no-cross-writes rule) is prepended to its prompt. Forks are skipped.

`cloudctx` is the per-context credential wrapper: any command that runs one command with one
context's credentials exported and nothing else leaking in. If yours has another name, change
the rewrite in `guard.py` and section 3 of `CLAUDE.md`.

What it does not catch: it is a safety net for the shapes an agent usually emits, not a sandbox.
A write made through Bash (`cat >`, `mv`, `cp`, a python heredoc) is not checked. A cloud CLI
reached inside a quoted string (`bash -c '...'`, `su -c '...'`) or through a script file is not
seen. Every error inside the guard fails open: the hook logs one line to stderr and allows the
call, because a broken guard must never stop work. The rules in `CLAUDE.md` bind either way.

## Instructions for Claude

If you are reading this because someone asked for a smarter way of working with their folder,
do these in order, and do not go past the confirmation step until they answer.

1. Ask which folder should become the project root, unless they have already said. It must not
   be their home directory. Everything below happens inside it.
2. Work from a temporary directory, not the folder that is about to become the project root:
   `cd $(mktemp -d)`.
3. Download the latest release archive of this repository (replace `<owner>` with the account
   that hosts it):
   `gh release download --repo <owner>/context-router-kit --pattern '*.tar.gz'`
   Without `gh`, take the archive link from the repository's latest release page.
4. Check it: `shasum -a 256 context-router-kit-*.tar.gz` and compare with the sha256 published
   on that release. If it does not match, stop and tell them.
5. Unpack it: `tar -xzf context-router-kit-*.tar.gz`
6. Show them what would change, writing nothing:
   `./context-router-kit-<version>/install.sh --root <their root>`
7. Wait for a yes. Do not apply on your own judgement.
8. On a yes: add `--apply` to the same command. Add `--statusline` too if they want the status
   line; it is the only thing the installer writes outside the root.
9. Tell them to restart Claude Code in that folder, because hooks load at startup.
10. In the new session, run the `context-bootstrap` skill. It inventories their folders, asks
    what each one is, and writes their registry.

Do not install into a folder they did not name. Do not skip the confirmation step.

## Install

The installer refuses to run under a `python3` older than 3.9, and refuses a root that is your
home directory: it is meant for one folder among several, not the whole of it.

```
./install.sh --root /path/to/your/root          # show the plan, write nothing
./install.sh --root /path/to/your/root --apply  # write it
```

`--root` alone is a dry run: it prints what would change and writes nothing. Nothing is written
until you add `--apply`. Add `--statusline` (with `--apply`) to also install a status line that
shows the active context, the only thing the installer writes outside the root. Add `--force` to
overwrite a file you already own, such as an existing `CLAUDE.md` or an existing status line
entry; without it, a file you own is only ever seeded when absent, never replaced.

Exit code 0 means it ran clean, 1 means it refused (for example, the root does not exist, or is
your home directory), 2 means a usage error (a missing or unknown argument).

Then start Claude Code in that root, because hooks load at startup, and ask it to run the
`context-bootstrap` skill. It inventories your folders, asks what each one is, and writes your
registry. Without that step the machinery is installed but has nothing to route.

## What the installer owns

Replaced on every install, with the previous copy saved under `.claude/.kit-backup/<timestamp>/`,
and a ready-to-paste restore command printed alongside it: `.claude/hooks/*.py`, `.claude/lib`,
`.claude/scripts`, `.claude/skills`, `.claude/tests`, and the four hook entries in
`.claude/settings.json`.

Seeded when absent and never overwritten: `CLAUDE.md`, `CONTEXTS.md`, `.claude/kit.json`,
`.claude/hooks/save-phrases.txt` (it lives inside `.claude/hooks`, but it is yours, not the kit's),
and everything else in `.claude/settings.json`, permissions included.

## Configuration

`.claude/kit.json`, all three keys optional:

```json
{ "owner": "your name", "scanned_parents": ["clients", "repos"], "iac": { "root": "repos/infra" } }
```

`owner` puts your name in the save trigger. `scanned_parents` limits the drift check to the folders
worth scanning. `iac` enables the infrastructure checks, and only makes sense if you have a
`<root>/<repo>/environments/<stage>/<name>` layout.

## Upgrading from 1.x

2.0.0 adds the guard (`hooks/context_guard.py`, `lib/guard.py`, their tests) and the
`PreToolUse` hook entry; the installer adds all of them. The registry key for cloud scope is now
`cloudctx`; the guard still reads the older `cloud` key. Your `CLAUDE.md` is never overwritten,
so compare its sections 3 and 4 with `template/CLAUDE.md`; the installer prints a note when yours
does not mention the guard.

## Your own conventions

Section 8 of `template/CLAUDE.md` is an empty slot for rules that hold in every context: commit
and PR conventions, ticket references, writing style, how you want answers shaped. Append further
sections after it as needed.

## Tests

```
cd .claude/tests && python3 -m unittest discover -p 'test_*.py'
```
