---
name: new-context
description: Use when the user mentions a folder, repo or project that is not in CONTEXTS.md, or asks to onboard, register or set up a context, or asks which folders are unregistered ("check contexts", "unregistered folders").
---

# new-context

Registers a context in the project root's `CONTEXTS.md` and creates its home, handoff and memory
index. Everything is idempotent and nothing outside the new context is touched.

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

Resolve the other paths, once, and reuse them:

```bash
python3 -c "import sys; sys.path.insert(0, '<root>/.claude/lib'); import rootpath; print(rootpath.root()); print(rootpath.memory_dir())"
```

## Drift mode

Run: `python3 "<root>/.claude/scripts/registry_check.py"`

Report the sections verbatim. For each unregistered folder, offer to register it. For a context
with an unfinished TODO field, ask for the missing value and fix the line.

## Register mode

1. Ask for, or infer and confirm, each field:
   - `kind`: what sort of context this is. The template registry uses client, platform and
     personal, but the set is the adopter's own.
   - `status`: the context's stage of life. The template registry uses active, dormant, stub or known,
     but the set is the adopter's own.
   - `aliases`: every other name the user calls this thing, comma separated, lowercase. Aliases
     may contain spaces and usually should: they exist to match how the user actually says the
     thing. Only the context's own name must be a single token with no spaces, since
     `set_context.sh` validates the name and rejects one that has a space. If the natural name has
     a space in it ("acme corp"), pick a single-token name ("acme-corp" or "acmecorp") and offer
     the spaced form as an alias instead.
   - `cloudctx`: the per-context cloud or account scoping, or `none`.
   - `home`: the folder, relative to the root. Default to the folder the user named.
2. Refuse if the name or any alias already resolves. Check the name, and then check each alias
   one by one:

   ```bash
   python3 -c "import sys; sys.path.insert(0, '<root>/.claude/lib'); import registry, rootpath; r=registry.load_registry(rootpath.root()/'CONTEXTS.md'); print(registry.resolve(r, '<term>'))"
   ```

   Anything but `[]` is a collision. Name the block it collides with and stop.
3. Only when `kit.json` configures an `iac.root`, offer to fill `iac_names` from the folders found
   under it. Run this command (where `<term>` is the new context's name) and show the list before writing:

   ```bash
   python3 -c "import sys; sys.path.insert(0, '<root>/.claude/lib'); import registry, rootpath; print(sorted(n for n in registry.env_dir_names(rootpath.root()) if '<term>' in n))"
   ```
4. Create the home folder if missing. Write `<home>/CLAUDE.md` from `templates/CLAUDE.md.tmpl`,
   filling `{{name}}` and `{{cloud}}`; leave any existing file alone. Write `<home>/HANDOFF.md`
   from `templates/HANDOFF.md.tmpl` if missing, filling `{{name}}`, `{{date}}` (today, `YYYY-MM-DD HH:MM`),
   `{{day}}` (today, `YYYY-MM-DD`), `{{session_id}}` from the SessionStart output, and
   `{{questions}}`, which is `Open questions for <owner>` when `.claude/kit.json` sets a
   string `owner`, and the bare `Open questions` otherwise. This must match what the
   save-trigger hook tells Claude to rewrite, or the trigger will name a section that does
   not exist in the file.
5. Create `<memory_dir>/MEMORY.md` first if it does not exist yet (this skill can run before
   `context-bootstrap` ever has, so nothing may have seeded it): a `# Memory index` heading and a
   `## Contexts` heading, nothing else. Create `<memory_dir>/contexts/<name>/INDEX.md` from
   `templates/INDEX.md.tmpl` if missing, filling `{{name}}`. Add
   `- [<name>](contexts/<name>/INDEX.md): context memory index` under the `## Contexts` heading of
   `<memory_dir>/MEMORY.md` only if no line for that context already exists under that heading.
6. Insert the block into `CONTEXTS.md` below the registry marker, in alphabetical position among
   the blocks that are already there. If a `## ignore` block is present, it stays last regardless
   of its name; insert before it:

   ```
   ## <name>
   kind: <kind>
   status: <status>
   aliases: <aliases>
   cloudctx: <cloud>
   home: <home>
   iac_names: <iac_names>
   rules:
     - <rule>
   ```

   Omit `rules` entirely when there are none. Omit `iac_names` value when there are none.
7. Re-run `registry_check.py` and confirm the new folder no longer appears as unregistered.
