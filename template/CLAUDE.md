# Context router

This folder is the single project root. Every session belongs to exactly one context: contexts are
defined in `CONTEXTS.md`, and the router says how to pick one and what to do once it is
picked.

The registry is NOT imported here. The SessionStart hook prints a digest of it, every context
name with its aliases, which is everything resolution needs. Once a context is resolved, read
its one block from `CONTEXTS.md` for the cloud scope, home, IaC names and rules (section 2
step 1 covers this). Search `CONTEXTS.md` directly for a folder path, or for any term the
digest does not list. The IaC layout table at the top of `CONTEXTS.md`, if you keep one,
is not in the digest either: read it before any shared infrastructure work (section 6). If the digest is missing from the session, read `CONTEXTS.md` in full
before resolving anything.

## 0. First run

If the registry still only describes the four shipped example contexts (`northwind`,
`globex`, `platform`, `sideproject`), or nothing at all, the router is installed but not yet set up for your
work. Use the `context-bootstrap` skill and do nothing else first.

## 1. Resolve the context first

- On the first message, match what the user says against the registry: block name, any alias, or a
  folder path they mention. Case-insensitive.
- Exactly one match: activate it (section 2). No match or several: ask one short question. Do not
  do context-bound work before a context is active. Questions that need no context can be answered
  without one.
- A name that looks like a context but is not in the registry: ask the user to confirm, then use
  the `new-context` skill. Never invent a context.
- A later message naming a different context is a switch: update the current handoff (section 5),
  confirm the switch, activate the new context and rewrite the marker.

## 2. Activation, in this order

0. Resolve `<root>`, the project root, once, before running any command below that needs it. Run
   this and keep the absolute path it prints; substitute it for `<root>` in every command below:

   ```bash
   python3 - <<'EOF'
   from pathlib import Path
   here = Path.cwd()
   for candidate in [here, *here.parents]:
       if (candidate / ".claude" / "lib" / "rootpath.py").is_file():
           print(candidate)
           break
   else:
       print("no context router installed above", here)
   EOF
   ```

   If it prints `no context router installed above <dir>`, stop and say the kit is not installed in
   or above the working directory, rather than guessing.

   Warning: never use `$CLAUDE_PROJECT_DIR` in a command you run yourself. Claude Code sets that
   variable for hook subprocesses only, and it is empty in an ordinary tool call, so a command using
   it silently resolves to `/.claude/...` and fails.
1. Read the context's own block in `CONTEXTS.md`. The SessionStart digest carries names and
   aliases only, which is enough to resolve but not to act: the block is where the cloud scope,
   the home, the IaC names and the standing rules live, and those rules bind everything you do
   in this context. Read the one block, not the file.
2. Read `<home>/CLAUDE.md` if the context has a home and the file exists. Not every context has one
   yet: `new-context` seeds it for the one context it registers, but `context-bootstrap` does not
   write it for every folder it finds.
3. Read `<home>/HANDOFF.md` if it exists.
4. Read the context's memory index if it exists: `<memory_dir>/contexts/<ctx>/INDEX.md`. Resolve
   `<memory_dir>` with
   `python3 -c "import sys; sys.path.insert(0, '<root>/.claude/lib'); import rootpath; print(rootpath.memory_dir())"`
   Open individual memory files only when needed.
5. If the context has `iac_names`, resolve its IaC folders with
   `find <iac root> -maxdepth 4 -type d -path '*/environments/*' -iname '<iac_name>*'` for each
   name, where `<iac root>` is the folder the IaC layout table in `CONTEXTS.md` describes.
6. Write the marker so the status line and the session-end hook know the context. The session id is
   the `session_id:` line the SessionStart hook printed:
   `bash "<root>/.claude/scripts/set_context.sh" <ctx> <session_id>`
   The guard reads this marker too: without it, writes are not checked and every cloud CLI call
   is denied.
   On resume, compact or fork, if the SessionStart hook reports the context as unknown to the
   hooks, rewrite the marker now and do not re-ask which context this is.
7. Start the first reply with the tag `[<ctx>]` followed by at most three lines from the handoff:
   what is in flight, the next step, what is blocked. If there is no handoff, say so in one line.

Every reply in the session starts with `[<ctx>]`.

<!-- optional: cloud -->
## 3. Scoped commands

- No active context, no command that touches a live environment.
- Every az, aws, terraform or terragrunt call runs as `cloudctx exec <scope> -- <command>`, where
  `<scope>` is the name in the context's `cloudctx` line and `cloudctx` is your per-context
  credential wrapper (any tool that runs one command with one context's credentials exported).
  Never bare, never a separate "switch context" call followed by a command.
- Contexts with several scopes: confirm which one before the first call.
- Read-only by default. Anything that mutates needs the user's explicit approval in this session,
  in writing.
- A `PreToolUse` guard (`.claude/hooks/context_guard.py`) rewrites a bare cloud CLI call into
  `cloudctx exec <scope> -- <command>` when the command is a single plain invocation. It denies
  the call instead when it is not (pipes, substitutions, redirects, a leading `VAR=value`, `sudo`,
  a wrapper such as `time` or `env`), when the context has several scopes, or when no context is
  active; the fix is always to write the scoped form yourself. For a context whose `cloudctx` is
  `none` it asks, since a local `terraform fmt` needs no credentials. To run one unscoped on
  purpose, put the token `#noctx` in the command, on its own and not inside quotes. The guard is
  a safety net for the shapes an agent emits, not a sandbox: it does not see every way a shell
  can reach a command, so these rules still bind whether or not it catches a given call.
<!-- /optional: cloud -->

## 4. Where writes go

- Memory: with active context X, a new fact goes to the context's memory folder with frontmatter
  (`name`, `description`, `metadata.type`) and one line added to that folder's `INDEX.md`. Only
  facts about you, or about the project as a whole, go to the memory root's `MEMORY.md`.
- Handoff: `<home>/HANDOFF.md` of the active context only.
- Working files: inside the active context's home or owned folders. Never loose at the root.
- Personal notes never go into a git repository other people use. Write them to the context home
  instead. If a note must sit inside a repo tree, name it `<name>.local.md` or put it under
  `.notes/`, and cover both patterns in your own global gitignore so they never show up in a diff.
  Before creating any file inside a repo, check `git -C <repo> remote -v`: a remote that is not
  yours means the file is a team change and needs approval.
- Never write into another context's home, handoff or memory folder. The guard denies this for
  `Write`, `Edit` and `NotebookEdit`, including through a symlink and inside the memory tree. It
  does not see a write made through Bash (`cat >`, `mv`, a python heredoc), so this rule binds you
  whether or not the guard is watching.
- Delegated subagents start blank. The guard prepends the active context's block (scope, home,
  standing rules) to every `Agent` or `Task` prompt, except a fork, which inherits the session.

## 5. Handoff

Rewrite `<home>/HANDOFF.md` when the user signals stopping (the save hook prints `SAVE TRIGGER`),
when a piece of work reaches a natural end, or before a context switch. `Now`, the open questions
section and `Repo state` are rewritten; `Recent` gets one new line at the top and is capped at ten.
For `Repo state`, run `git status --short --branch` and `git worktree list` in every git repo under
the context's home and owned folders, and list only repos that are dirty or not on their main
branch. Confirm the path in the reply. Keep the file under about 60 lines.

Template:

```
# <ctx> handoff
updated: <YYYY-MM-DD HH:MM>  |  session: <first prompt or name>, id <session_id>

## Now
- **<workstream>**
  status:  <one line, what is true right now>
  next:    <the single next action>
  blocked: <what and since when, or omit the line>
  refs:    <ticket keys, PR URLs, paths>

## Open questions
- <decision waiting on you>

## Repo state
- <repo>: <branch>, <n> uncommitted files, worktrees: <list>

## Recent
- <YYYY-MM-DD>  <one line>
```

The `Open questions` heading becomes `Open questions for <owner>` when `.claude/kit.json` sets a
string `owner`; otherwise leave it bare, matching what the save hook and the context templates
produce.

<!-- optional: shared code -->
## 6. Shared code is everyone's

Some paths are consumed by more than one context: a module registry, a shared pipeline, a base
configuration. List them under `owns` in whichever context represents that shared code (the shipped
example calls it `platform`), and, if you keep infrastructure as code, describe them in the IaC
layout table at the top of `CONTEXTS.md`. Read that table before any shared infrastructure work.
If a change would land in one of them while a different context is active: stop, name every
context affected (every `environments/*/<iac_name>` under that repo, or every consumer of a module
registry), and offer to switch to the shared-code context. In a shared-code session, name the
affected consumers before editing.
<!-- /optional: shared code -->

## 7. Wrong directory

If the working directory is not the project root, say so in the first line of the first reply and
recommend restarting from the root. Still use absolute paths so nothing lands in the wrong place.

<!-- optional: conventions -->
## 8. Conventions that apply everywhere

Put the rules that hold across every context here: commit and PR conventions, how tickets are
referenced, writing style, anything that should never appear in customer-facing text. This block is
yours to fill in.
<!-- /optional: conventions -->
