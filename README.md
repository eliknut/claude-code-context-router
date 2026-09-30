# claude-code-context-router

If you use Claude Code for several clients, codebases and side projects from one machine, things
bleed. A session started for one client edits another client's files, runs `az` against whatever
tenant happens to be logged in, and forgets on Monday what you were halfway through on Friday.
This kit fixes that with one project root folder and one rule: every session belongs to exactly one
context. Claude resolves the context from your first message, loads only that context's notes and
handoff, keeps writes and cloud commands inside it, and writes a handoff when you stop.

Plain files and hooks: a `CLAUDE.md` router, a `CONTEXTS.md` registry, stdlib Python and bash.

## Quickstart

```
gh repo clone eliknut/claude-code-context-router   # or: git clone https://github.com/eliknut/claude-code-context-router
cd claude-code-context-router
./install.sh --root ~/work            # dry run: prints the plan, writes nothing
./install.sh --root ~/work --apply    # writes it
cd ~/work && claude                   # hooks load at startup
> set up my contexts                  # runs the context-bootstrap skill on your folders
> northwind: check the deploy         # reply starts with [northwind]
```

`~/work` is any existing folder that holds your work. It must not be your home directory.

## How it works

```
 session starts in <root>
        |
 SessionStart hook ----> prints registry digest (names + aliases) and recent handoffs
        |
 first message: "northwind: check the deploy"
        |
 CLAUDE.md router ----> resolve: one match? activate. none or several? ask.
        |
 activate: read the block in CONTEXTS.md -> <home>/CLAUDE.md -> <home>/HANDOFF.md
           -> memory/contexts/<ctx>/INDEX.md -> set_context.sh writes the marker
        |
 every tool call ----> PreToolUse context_guard.py (reads the marker)
        |                 Write/Edit into another context ........ deny
        |                 bare az/aws/terraform/terragrunt ....... rewrite to
        |                                              cloudctx exec <scope> -- <cmd>
        |                 Agent/Task prompt ...................... context block prepended
        |
 "done for today" ---> UserPromptSubmit hook prints SAVE TRIGGER, Claude rewrites HANDOFF.md
        |
 session ends -------> SessionEnd hook logs a line under Recent in the handoff
```

The marker is a one-line file under `~/.claude/ctx-state/<session_id>` holding the context name.
The hooks and the optional status line read it.

## Example session

```
$ cd ~/work && claude

> northwind: check the deploy

[northwind]
In flight: moving the app service plan to the new region.
Next: run the plan for prod and compare with last week's.
Blocked: nothing.
Recommended: start with the plan for prod.

  Bash  az webapp list -g nw-prod
        Scoped to cloudctx 'northwind' (CLAUDE.md section 3).
        ran: cloudctx exec northwind -- az webapp list -g nw-prod

> also fix the typo in globex's runbook

  Edit  clients/globex/docs/runbook.md
        denied: clients/globex/docs/runbook.md belongs to context 'globex', but
        'northwind' is the active context. Switch context first, or write inside 'northwind'.

[northwind] That file belongs to globex. Want me to switch to globex for it?

> no, done for today

[northwind] Handoff written to clients/northwind/HANDOFF.md: the prod plan is next, nothing blocked.
```

## Make CONTEXTS.md yours

`CONTEXTS.md` ships with four example contexts (`northwind`, `globex`, `platform`, `sideproject`).
The `context-bootstrap` skill replaces them with yours. To do it by hand, delete the examples and
write one block per context below the `<!-- registry -->` line:

```
## northwind                                  the name: one token, no spaces
kind: client                                  any category you like
status: active                                active, dormant, stub, ... your choice
aliases: nw, northwind traders                what you actually say; comma separated
cloudctx: northwind (Azure, prod tenant)      scope name(s) for the wrapper, or none
home: clients/northwind                       folder relative to the root, or none
iac_names: northwind                          folder names under environments/<stage>/
owns:                                         extra folders this context owns
  - repos/northwind-app
rules:                                        standing rules Claude must follow here
  - read-only by default; changes need explicit approval
```

- **Aliases resolve case-insensitively** against the block name and every alias. One match
  activates it. No match or several: Claude asks one short question. A name it does not know
  starts the `new-context` skill after you confirm.
- **Several scopes** (`cloudctx: globex (AWS, prod), globex-dev (AWS, dev)`) make Claude confirm
  which one before the first cloud call. The text in parentheses is a note for you.
- **Ownership** is `home` plus `owns`. The longest matching path wins, so `clients` and
  `clients/acme` can both be homes.
- **IaC folders.** With `"iac": {"root": "repos/infra"}` in `.claude/kit.json`, each context also
  owns its own `repos/infra/<repo>/environments/<stage>/<iac_name>*` folders. Give the shared-code
  context only the shared parts (the module repo, `environments/_base`), not the whole
  infrastructure folder.
- **The `ignore` block** at the end lists folders that are deliberately not contexts
  (`paths: scratch, archive`), so the drift check stays quiet about them.

Check the registry at any time with `python3 .claude/scripts/registry_check.py`.

## If you do not use cloud CLIs or cloudctx

[cloudctx](https://github.com/eliknut/cloudctx) gives each terminal window its own Azure or AWS CLI
identity, so a command scoped to one client cannot reach another. The guard rewrites bare cloud
calls to go through it. If you do not want that, set `cloud_wrapper` in `.claude/kit.json`:

```json
{ "cloud_wrapper": false }
```

`false` turns the cloud check off; everything else in the guard keeps working. Then delete section
3 ("Scoped commands") of your `CLAUDE.md`, and set `cloudctx: none` in every block (or leave the
line out). If you use a different wrapper, give its name instead, for example
`{ "cloud_wrapper": "awsctx" }`. It must accept `<name> exec <scope> -- <command>`. Missing means
`cloudctx`.

## The guard: what it catches and what it does not

`.claude/hooks/context_guard.py` runs before every `Write`, `Edit`, `NotebookEdit`, `Bash`, `Agent`
and `Task` call. It does three checks:

- **Write check.** A `Write`, `Edit` or `NotebookEdit` into another context's home, owned folders
  or memory folder is denied. Paths are resolved through symlinks and compared case-insensitively
  on macOS and Windows. Files no context owns (`CLAUDE.md`, `CONTEXTS.md`, `.claude/`) are allowed.
- **Shell-write check.** The same rule for a `Bash` command. The guard reads the paths the command
  writes and denies it when one is in another context: output redirects (`>`, `>>`, `2>`, `&>`,
  including `cat > file <<EOF`), `tee`, the destination of `cp`, `mv`, `install`, `rsync` and `ln`
  (and the sources `mv` removes), `sed -i` and `perl -i`, `rm`, `touch`, `mkdir`, `truncate`,
  `chmod`, `dd of=`, `curl -o`/`-O`, `wget`, `tar -x -C` and `tar -c -f`, `unzip`, `sort -o`,
  `patch`, git subcommands that write (`commit`, `checkout`, `reset`, `pull`, `mv`, `clone` and
  others; the repo or `-C DIR` counts as the target), `find -delete` and `find -exec`, and the
  same inside `bash -c '...'` and `$(...)`, quoted or not. Relative paths resolve against the
  session's working directory and any `cd`, `pushd` or `popd` earlier in the command (a `cd`
  inside `( ... )` ends with the subshell); `~`, `$HOME` and `$PWD` are expanded. Reads (`cat`,
  `grep`, `diff`, `<`, and read-only git such as `git -C DIR status`, `log` or `diff`) are not
  flagged. There is no `#noctx` for writes, this deny wins over a cloud rewrite, and a fault in
  this parser skips only this check, never the cloud check.
- **Cloud check.** A bare `az`, `aws`, `terraform` or `terragrunt` call that is one plain command
  is rewritten to `cloudctx exec <scope> -- <command>`. Anything it cannot rewrite safely is denied
  with the scoped form to use instead: pipes, `&&`, substitutions, redirects, a leading
  `VAR=value`, `sudo`, wrappers like `time` or `env`, a context with several scopes, or no active
  context. For a context with `cloudctx: none` it asks. Put `#noctx` in a command as its own word
  to run it unscoped on purpose.
- **Subagent check.** A delegated subagent starts blank, so the active context's block (home,
  scope, standing rules, the no-cross-writes rule) is prepended to its prompt. Forks are skipped.

What it does **not** catch, so the rules in `CLAUDE.md` still matter:

- Bash writes the shell text does not show: `python -c`, `node -e`, `eval`, `xargs`, a script
  file, a shell function or alias, or a path held in a variable other than `$HOME` or `$PWD`
  (such paths are skipped).
- A cloud CLI inside a quoted string (`bash -c '...'`, `su -c '...'`), inside a script file, or
  behind an alias or shell function.
- Anything run outside Claude Code's tools.
- Its own errors: every failure inside the guard fails open (one line to stderr, call allowed),
  because a broken guard must never stop work.

It is a safety net for the shapes an agent usually emits, not a sandbox.

## Customising

Your `CLAUDE.md` is yours after install. Section 8 is an empty slot for rules that hold in every
context. Add what you care about, for example:

```
## 8. Conventions that apply everywhere
- Commit messages in the imperative, under 72 characters; reference the ticket as PROJ-123.
- Answer first in one line, then at most three bullets. Detail only when I ask.
- Never write client names into shared repositories.
```

Add more numbered sections after it as needed. Per-context rules go in the block's `rules` or in
`<home>/CLAUDE.md`. Save phrases ("done for today", ...) live in `.claude/hooks/save-phrases.txt`,
one per line, in any language. `.claude/kit.json` also takes `owner` (your name in the handoff's
"Open questions for ..." heading) and `scanned_parents` (the folders the drift check scans).

## File layout

```
install.sh            installer (dry run by default)
VERSION, CHANGELOG.md
template/
  CLAUDE.md           the router: resolve, activate, scope, where writes go, handoff
  CONTEXTS.md         registry: example contexts, an ignore block, an example IaC layout table
  kit.json.example    seeded as .claude/kit.json
  statusline.sh       optional status line showing the active context
  .claude/
    settings.json     hook wiring (SessionStart, UserPromptSubmit, SessionEnd, PreToolUse)
    hooks/            session_start.py, user_prompt_submit.py, session_end.py,
                      context_guard.py, save-phrases.txt
    lib/              rootpath.py, registry.py, handoff.py, guard.py
    scripts/          set_context.sh, registry_check.py
    skills/           context-bootstrap, new-context
    tests/            test suite for all of the above
```

Per context, at run time: `<home>/CLAUDE.md`, `<home>/HANDOFF.md`, and a memory folder at
`~/.claude/projects/<root slug>/memory/contexts/<ctx>/`. Nothing hardcodes a path: `rootpath.py`
derives the root from `CLAUDE_PROJECT_DIR` or from its own location.

## Updating

Pull the repo (or check out a newer tag) and run the installer again, dry run first:

```
git pull && ./install.sh --root ~/work && ./install.sh --root ~/work --apply
```

- **Replaced** on every install, previous copy saved under `.claude/.kit-backup/<timestamp>/`
  with a restore command printed: the kit's own files in `.claude/hooks/*.py`, `.claude/lib`,
  `.claude/scripts`, `.claude/tests`, the two kit skills in `.claude/skills`, and the four kit
  hook entries in `.claude/settings.json`.
- **Seeded when absent, never replaced**: `CLAUDE.md`, `CONTEXTS.md`,
  `.claude/hooks/save-phrases.txt`. `--force` replaces these three, with a backup.
- **Never replaced, not even with `--force`**: `.claude/kit.json`.
- **Never touched**: your handoffs, your memory folders, your own skills and hooks, and every
  other key in `.claude/settings.json`, permissions included.

Since `CLAUDE.md` is never replaced, compare it with `template/CLAUDE.md` after an upgrade.
`CHANGELOG.md` says what changed in each version.

Installer exit codes: 0 ran clean, 1 refused (root missing, root is your home directory, python
too old, unmergeable settings.json), 2 usage error.

## Installing through Claude

If you asked Claude Code to set this up for you, Claude should do this, in order, and not go past
step 4 without your yes:

1. Ask which folder becomes the project root, unless you already said. Never the home directory.
2. Clone the release into a temporary directory, not into the root:
   `git clone --depth 1 --branch v2.1.0 https://github.com/eliknut/claude-code-context-router "$(mktemp -d)/kit"`
3. Show the plan, writing nothing: `<clone>/install.sh --root <root>`
4. Wait for a yes. On a yes, run it again with `--apply` (plus `--statusline` if you want the
   status line, the only thing written outside the root).
5. Tell you to restart Claude Code in the root, then say "set up my contexts".

## Requirements

- Claude Code with hooks (`SessionStart`, `UserPromptSubmit`, `SessionEnd`, `PreToolUse`).
- Python 3.9 or newer as `python3`, and bash. No packages.
- macOS or Linux.
- Optional: [cloudctx](https://github.com/eliknut/cloudctx), or any wrapper with the same
  `exec <scope> -- <command>` shape.

Run the tests with `python3 -m unittest discover -s .claude/tests -p 'test_*.py'` from your root.
