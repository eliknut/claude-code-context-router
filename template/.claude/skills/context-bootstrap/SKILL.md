---
name: context-bootstrap
description: Use on the first run after installing the context router kit, whenever CONTEXTS.md still contains only the shipped example contexts, or when the user asks to set up contexts for their folders ("set up my contexts", "bootstrap the router", "make my folder smarter").
---

# context-bootstrap

The machinery is installed, but the registry still only describes the three shipped example
contexts (or nothing, if those were already deleted), not the adopter's real work. This turns the
folders that are actually on this machine into contexts, without erasing anything the adopter, or a
previous run of this skill, or `new-context`, already curated below the marker.

Two rules for the whole flow. Do not guess what a folder is: the inventory is cheap, the judgement
belongs to the user. And do not interview folder by folder: an agent that asks about each folder in
turn will exhaust the person before the registry even exists, and they will abandon the setup half
done. Present everything at once, in one table, and ask for corrections in a single message.

## 1. Confirm the kit is installed

Resolve `<root>`, the project root, first, once. Run this and keep the absolute path it prints;
substitute it for `<root>` in every command in this skill:

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

If it prints `no context router installed above <dir>`, stop and say the kit is not installed in or
above the working directory, rather than guessing.

Warning: never use `$CLAUDE_PROJECT_DIR` in a command you run yourself. Claude Code sets that
variable for hook subprocesses only, and it is empty in an ordinary tool call, so a command using it
silently resolves to `/.claude/...` and fails.

```bash
python3 -c "import sys; sys.path.insert(0, '<root>/.claude/lib'); import rootpath; print('root:', rootpath.root()); print('memory:', rootpath.memory_dir())"
```

If that fails, the kit is not installed in this root. Say so, name the root (`<root>`
if the command never got far enough to print one), and stop. Do not install it from inside this
skill.

## 2. Inventory the root

```bash
cd "<root>" || exit 1
for d in */; do
  name="${d%/}"
  if [ -d "$name/.git" ]; then
    remote="$(git -C "$name" remote get-url origin 2>/dev/null || echo 'no remote')"
    last="$(git -C "$name" log -1 --format=%ad --date=short 2>/dev/null || echo '-')"
    printf '%s\trepo\t%s\t%s\n' "$name" "$remote" "$last"
  else
    kids="$(find "$name" -mindepth 1 -maxdepth 1 -type d -not -name '.*' | wc -l | tr -d ' ')"
    printf '%s\tfolder\t%s child folders\t-\n' "$name" "$kids"
  fi
done
```

For any folder reporting more than three child folders, list one level down as well. A folder full
of sibling projects is a parent, not a context, and each child is a candidate.

## 3. Propose, then confirm

First, parse what is already below the marker in `CONTEXTS.md` and list every existing block by
name, `kind` and `home`. Flag, by name, any block that matches one of the three shipped examples
(`northwind`, `platform`, `sideproject`): those are illustration, not curated work, and this is
where you say so rather than assuming either that they are real or that they are junk. Every other
existing block is the adopter's own until they say otherwise; it is not yours to remove or rewrite
on your own judgement.

Then present one table from the inventory: folder, what it looks like (repo with a remote, repo
with no remote, parent of projects, plain folder), and a proposed `kind`. Leave out any folder that
is already the `home` of an existing, non-example block: it already has a context, so this run is
not here to redo it.

Then ask, in a single message:

- which proposals are wrong, and what they should be
- which folders to leave out of the registry entirely
- for each new context, any other name the person calls it, which becomes `aliases`
- whether any new context needs per-context cloud or account scoping, which becomes `cloud`
- which of the flagged shipped-example blocks, if any, to remove
- whether any existing block needs a change today, and what

A context's name is a single token: no spaces (`set_context.sh` writes it into a session marker's
filename and refuses one that has a space). Aliases never reach that script and may contain
spaces; they usually should, since aliases exist to match how the person actually says the thing.
A folder name is usually already a single token; when it is not (a name with a space, or one that
reads better split into words), propose a single-token block name and offer the natural, spaced
form as an alias instead of using it as the name itself.

Suggested starting kinds, which the adopter is free to replace: `client` or `customer` for work
done in someone else's environment, `platform` for shared code, `tool` for something they
maintain, `personal` for their own projects.

## 4. Write the registry

This step is additive, never a wholesale replace. Someone may already have curated blocks here by
hand or with `new-context`, and this skill firing again later, for example on "make my folder
smarter" once they get round to the folders they skipped the first time, must never be how that
work gets lost.

`CONTEXTS.md` already exists at the root in the common case: the installer seeds it with an intro
paragraph, the key schema, the `<!-- registry -->` marker and the three shipped example contexts.
Open it and keep everything from the top of the file through the marker line exactly as it is: the
adopter may already have edited that intro (added a note, tweaked the schema description), and
this step is not a chance to revert it back to whatever the installer originally seeded. Below the
marker:

- Leave every existing block that is not a shipped example, and that the person did not ask to
  remove in step 3, exactly as it is: same fields, same values, same position. Never delete one
  without an explicit "yes, remove this" naming that block; a context can legitimately have no
  folder yet, so an inventory that never mentioned it is not grounds to remove it.
- Remove a shipped example block (`northwind`, `platform`, `sideproject`) only when the person
  confirmed removing it in step 3, not on its name alone.
- Update an existing block's fields only when the person explicitly asked for that change in step
  3. Do not rewrite a block just because the file is already open.
- Add one new block, in alphabetical position among the blocks that remain, for every folder
  confirmed today that has no existing block yet.
- Keep, or start if none exists yet, a final block:
  ```
  ## ignore
  paths: <folder>, <folder>
  ```
  Add today's declined folders to its list without dropping any entry already there, whole
  categories included: an ignore entry covers itself and everything beneath it, so an `archive` or
  `scratch` folder full of children can be named once and none of its children will ever be
  reported as unregistered.

The key schema, so this step never depends on reading another file to know it (this is what
`registry.py` actually parses, so treat it as the authority if the seeded text and this note ever
disagree): `kind` (a category, the adopter's own set), `status` (its stage of life, the adopter's
own set), `aliases` (comma separated, case-insensitive, blank if none), `cloud` (per-context cloud
or account scoping, or `none`), `home` (folder relative to the root, or `none`), `iac_names` (comma
separated names under an infrastructure layout, blank unless one applies), `owns` (extra folders,
one per line, only when there are any), `rules` (standing rules, one per line, only when there are
any). Write each new block in this field order: `kind`, `status`, `aliases`, `cloud`, `home`,
`iac_names`, `owns` (omit the key entirely when there are none), `rules` (omit the key entirely
when there are none). Write `iac_names:` with no value unless the adopter has an infrastructure
layout that uses it.

If `CONTEXTS.md` is missing entirely (nothing has seeded it yet, or it was deleted), write it from
scratch instead: a `# Contexts` heading, one paragraph stating that this file is the registry, that
`CLAUDE.md` says how it is used, and the key schema above, then the `<!-- registry -->` marker on
its own line, then one block per confirmed context in alphabetical order, then the `ignore` block.
Everything is new in this case, so there is nothing to preserve and no guard above applies.

Then offer to write `.claude/kit.json`. Read whatever is already there first (it may hold keys this
skill does not know about) and merge into it: set or change only the keys today's run actually has
something to say about, and leave every other key, this run's three included when it has nothing
new for them, exactly as it was. Never write a fresh object over an existing file.

- `owner`: their name, so the save trigger says "Open questions for <name>". Skip for a neutral
  handoff section.
- `scanned_parents`: the parent folders worth scanning for new work. Naming them stops loose
  folders like `docs` or `archive` being reported as unregistered on every run.
- `iac`: only when they have a `<root>/<repo>/environments/<stage>/<name>` layout, an object with
  one key, `root`, naming the folder (relative to this root) that holds those `<repo>` folders, for
  example `{"root": "repos/platform"}`.

## 5. Seed the handoffs and the memory index

For every confirmed context that has a `home`, write `<home>/HANDOFF.md` from
`.claude/skills/new-context/templates/HANDOFF.md.tmpl`, filling all five placeholders it contains:
`{{name}}`, `{{date}}` (now, as `YYYY-MM-DD HH:MM`), `{{day}}` (today, as `YYYY-MM-DD`),
`{{session_id}}` (from the SessionStart output at the top of this session), and `{{questions}}`
(`Open questions for <owner>` when `.claude/kit.json` sets a string `owner`, otherwise the bare
`Open questions`). Never overwrite an existing handoff.

Create the memory store if it does not exist yet: `<memory_dir>/MEMORY.md`, starting with a
`# Memory index` heading and a `## Contexts` heading. Under that heading, add one line per
confirmed context, `- [<name>](contexts/<name>/INDEX.md): context memory index`, skipping any
context that already has a line there. For every confirmed context, create
`<memory_dir>/contexts/<name>/INDEX.md` from `.claude/skills/new-context/templates/INDEX.md.tmpl`
if it does not already exist, filling its one placeholder, `{{name}}`.

This step does not touch `.claude/skills/new-context/templates/CLAUDE.md.tmpl` at all. Unlike `new-context`, which seeds a
home `CLAUDE.md` for the single context it registers, bootstrap does not write one for every folder
it discovers here: a dozen near-identical files carrying a `cloud` value nobody has decided on yet
is clutter dressed as configuration, not a context that is ready to use. See the hand-over step for
how the adopter fills this in later, per context, as needed.

## 6. Validate before claiming success

Run all three and show the output:

```bash
python3 "<root>/.claude/scripts/registry_check.py"
cd "<root>/.claude/tests" && python3 -m unittest discover -p 'test_*.py'
echo '{"session_id":"bootstrap-check","source":"startup"}' | python3 "<root>/.claude/hooks/session_start.py"
```

Expected: no unregistered folders and no missing homes, the tests pass, and the SessionStart
output lists the handoffs just seeded. This cannot pass while step 4 has not run yet: a
still-seeded registry covers nothing but the three examples, so `registry_check.py` reports every
top level folder as unregistered and exits 1; if `CONTEXTS.md` is missing entirely it exits 2 with
a one-line message naming the path it looked for, not a traceback. If it still reports unregistered
folders after step 4, either register them or add them to the `ignore` block, and say which you
did.

## 7. Hand over

Tell them, in this order:

1. Restart Claude Code, because the hooks load at startup and are not active in this session.
2. Three habits: name the context in your first message ("northwind deploy" style, using their own
   context names), say a stop phrase when you finish so the handoff gets written, and, if they
   installed with `--statusline`, glance at the status line to see which context is active.
3. Where to change things: `CLAUDE.md` is the router and it is theirs to edit,
   `.claude/hooks/save-phrases.txt` holds the stop phrases, and `new-context` registers the next
   context. Unlike this skill, `new-context` also seeds a home `CLAUDE.md` for the one context it
   registers: add one for any of today's contexts by hand, from the same template, once that
   context actually needs its own notes.
