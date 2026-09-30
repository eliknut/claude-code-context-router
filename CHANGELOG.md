# Changelog

## 2.1.0

- The guard's write check now covers `Bash` too (the shell-write check). A command that writes
  into another context's home, owned folders or memory folder is denied: output redirects
  (`>`, `>>`, `2>`, `&>`, heredocs), `tee`, `cp`, `mv`, `install`, `rsync`, `ln`, `sed -i`,
  `perl -i`, `rm`, `touch`, `mkdir`, `truncate`, `chmod`, `dd of=`, `curl -o`, `wget`, `tar -x`,
  `unzip`, `sort -o`, `patch`, git write subcommands (`git -C DIR commit` and the like, `git mv`,
  `git clone DEST`), `find -delete` and `find -exec`, and `bash -c '...'` and `$(...)` strings,
  including inside double quotes. Wrapper options (`sudo -u root`, `nice -n 10`, `env -u`,
  `timeout 5`) do not hide the command. Relative paths resolve against the hook input's `cwd` and
  any `cd`, `pushd` or `popd` in the command; a `cd` inside a subshell does not leak out of it.
  The deny wins over a cloud rewrite, and `#noctx` does not bypass it. A fault in this parser
  skips only the shell-write check, never the cloud check. Not caught: `python -c`, `eval`,
  `xargs`, scripts, shell functions and paths in variables.
- Command splitting now knows comments and backslash escapes, so an apostrophe in a comment
  (`# don't`) or an escaped quote no longer hides the lines after it, and `>|` is read as a
  redirect rather than a pipe. A command that spans several lines is never rewritten by the
  cloud check as a whole; it is denied with the scoped form instead.
- `guard.decide()` takes a `cwd` keyword; the hook passes the input's `cwd` field.

## 2.0.1

- The repository is now `claude-code-context-router` (was `context-router-kit`).
- New `.claude/kit.json` key `cloud_wrapper`: the name of your credential wrapper (default
  `cloudctx`), or `false` to turn the guard's cloud check off. The installer never replaces an
  existing `kit.json`, not even with `--force`.
- IaC folders count as owned by their context: with `iac.root` set in `kit.json`, a context owns
  its `<iac root>/<repo>/environments/<stage>/<iac_name>*` folders, and the longest match wins.
- Example `CONTEXTS.md` fixed: `platform` no longer has the whole infrastructure folder as its
  home, which made northwind's own environment folder look like platform's.
- `install.sh --help` exits 0. Upgrade notes moved out of the installer output into this file.
- README rewritten for new users: quickstart, example session, what the guard does not catch.

## 2.0.0

- Adds the `PreToolUse` guard (`.claude/hooks/context_guard.py`, `.claude/lib/guard.py`) with
  three checks: a write into another context's home or memory is denied; a bare `az`, `aws`,
  `terraform` or `terragrunt` call is rewritten to `cloudctx exec <scope> -- <command>` or denied
  when it cannot be rewritten safely; a delegated subagent gets the active context's block.
- The registry key for the cloud scope is now `cloudctx`. The older `cloud` key is still read.
- 2.0.0 follow-up: quoted parentheses (such as a JMESPath `--query "[?contains(name,'web')]"`) no
  longer hide a cloud call, and a path spelled in another case no longer slips past the write
  check on a case-insensitive filesystem.

## 1.1.1

- The installer says so when an upgraded root still has the `@CONTEXTS.md` import in its
  `CLAUDE.md`.

## 1.1.0

- `CLAUDE.md` no longer imports the whole registry. The SessionStart hook prints a digest (every
  context name with its aliases) and the rest of a block is read at activation. On a root with
  about fifty contexts this cut the fixed cost per session by roughly 40 percent.
- Activation gains a step: read the context's own block in `CONTEXTS.md`.
- Handoff detail at session start is capped at the six newest; older ones are named only.

## 1.0.0

- First release: `CLAUDE.md` router, `CONTEXTS.md` registry, SessionStart, UserPromptSubmit and
  SessionEnd hooks, `context-bootstrap` and `new-context` skills, installer with dry run.

## Upgrading

Run the installer again (see "Updating" in the README). Kit files are replaced; your
`CLAUDE.md` is not, so check it by hand for the items below.

### From 1.x to 2.x

- The installer adds the guard files and the `PreToolUse` hook entry. Nothing else to do for them.
- Compare sections 3 ("Scoped commands") and 4 ("Where writes go") of your `CLAUDE.md` with
  `template/CLAUDE.md`, which describe the guard, the `#noctx` escape and what a deny means.
- Rename `cloud:` to `cloudctx:` in your blocks when convenient. Both work.
- If you do not use cloudctx, set `"cloud_wrapper": false` in `.claude/kit.json` (2.0.1 and later).

### From 1.0.0 to 1.1.x

- Remove the line `@CONTEXTS.md` from your `CLAUDE.md`. It imports the whole registry into every
  session, which the digest now replaces.
- Add a first activation step: read the context's own block in `CONTEXTS.md`. Without it the
  cloud scope and the standing rules are never loaded.
