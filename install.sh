#!/bin/bash
# install.sh: install the context router kit into a project root.
#
# Usage:
#   ./install.sh --root <path>                     show the plan, write nothing
#   ./install.sh --root <path> --apply             write it
#   ./install.sh --root <path> --apply --statusline also install the status line
#   --force                                        overwrite a file you own
#
# Files under .claude/{hooks,lib,scripts,skills,tests} belong to the kit and are replaced
# on every install, with a backup. CLAUDE.md, CONTEXTS.md, .claude/kit.json and
# save-phrases.txt belong to you and are only ever seeded when absent, unless --force
# says otherwise. --force also governs an existing statusLine entry in your home
# settings.json and an existing $HOME/.claude/statusline.sh.
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$KIT/template"
VERSION="$(cat "$KIT/VERSION")"
ROOT=""
APPLY=0
STATUSLINE=0
FORCE=0

die() { echo "refused: $*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage:
  ./install.sh --root <path>                     show the plan, write nothing
  ./install.sh --root <path> --apply             write it
  ./install.sh --root <path> --apply --statusline also install the status line
  --force                                        overwrite a file you own: CLAUDE.md,
                                                  CONTEXTS.md, .claude/kit.json,
                                                  .claude/hooks/save-phrases.txt,
                                                  $HOME/.claude/statusline.sh, and an
                                                  existing statusLine entry in
                                                  $HOME/.claude/settings.json
USAGE
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --root) [ $# -ge 2 ] || usage; ROOT="$2"; shift 2;;
    --apply) APPLY=1; shift;;
    --statusline) STATUSLINE=1; shift;;
    --force) FORCE=1; shift;;
    -h|--help) usage;;
    *) echo "unknown argument: $1" >&2; usage;;
  esac
done

[ -n "$ROOT" ] || usage
[ -d "$ROOT" ] || die "root does not exist or is not a directory: $ROOT"
ROOT="$(cd "$ROOT" && pwd)"
[ "$ROOT" != "$HOME" ] || die "root must not be your home directory"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || die "python3 3.9 or newer is required"

# One merge implementation, used twice: once in memory during pre-flight (to
# refuse before anything is copied) and once for real, later, to write the
# result. Because it is the same function both times, a shape the merge
# cannot handle can never slip past the pre-flight check by accident: whatever
# makes the real merge raise is exactly what makes the check raise too.
MERGE_PY="$(mktemp "${TMPDIR:-/tmp}/kit-merge-hooks.XXXXXX")"
trap 'rm -f "$MERGE_PY"' EXIT
cat > "$MERGE_PY" <<'PY'
import json, shutil, sys
from pathlib import Path


class BadShape(Exception):
    pass


def script(entry):
    """The hook script filename an entry runs, however its path is written."""
    for hook in entry.get("hooks", []):
        command = hook.get("command", "")
        if "/.claude/hooks/" in command:
            return command.rsplit("/.claude/hooks/", 1)[1].strip('"').strip()
    return ""


def merge_hooks(current, kit_hooks):
    """Fold kit_hooks into current["hooks"], in place. Returns [(event, verb), ...].

    Raises BadShape with a specific message for the two shapes we can name
    precisely (hooks not an object, an event not a list); raises whatever the
    underlying data does for anything else (for example, a hook entry that is
    not itself an object). There is only one merge: a pre-flight check that
    runs this exact function can never drift from what the real merge does.
    """
    if "hooks" not in current:
        current["hooks"] = {}
    hooks = current["hooks"]
    if not isinstance(hooks, dict):
        kind = "null" if hooks is None else type(hooks).__name__
        raise BadShape(f"'hooks' must be an object, found {kind}")
    results = []
    for event, entries in kit_hooks.items():
        ours = {script(e) for e in entries}
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            raise BadShape(f"'hooks.{event}' must be a list, found {type(existing).__name__}")
        kept = [e for e in existing if script(e) not in ours]
        replaced = len(existing) - len(kept)
        hooks[event] = kept + entries
        results.append((event, "replaced" if replaced else "added"))
    return results


def load_kit_hooks(template_path):
    return json.loads(Path(template_path).read_text(encoding="utf-8"))["hooks"]


def main():
    mode = sys.argv[1]
    settings_path = Path(sys.argv[2])
    template_path = Path(sys.argv[3])
    apply_flag = sys.argv[4] == "1"
    backup_dir = Path(sys.argv[5])
    kit_hooks = load_kit_hooks(template_path)

    if mode == "check":
        # In-memory only: never writes, never touches settings_path on disk.
        current = json.loads(settings_path.read_text(encoding="utf-8"))
        try:
            merge_hooks(current, kit_hooks)
        except BadShape as e:
            print(str(e))
        except Exception as e:
            print(f"its structure cannot be merged ({type(e).__name__}: {e})")
        return

    if mode == "merge":
        current = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
        before = json.dumps(current, sort_keys=True)
        results = merge_hooks(current, kit_hooks)
        for event, verb in results:
            print(f"  {verb:<8} {event}")
        if json.dumps(current, sort_keys=True) == before:
            print("  same     settings.json hook entries")
            print("RESULT unchanged")
        else:
            if apply_flag:
                if settings_path.is_file():
                    # Root-relative, like every other pre-image under $BACKUP: this
                    # mirrors $ROOT/.claude/settings.json, not $ROOT/settings.json,
                    # so a single "cp -R $BACKUP/. $ROOT/" restores it to the right
                    # place instead of leaving it orphaned at $BACKUP's own top level.
                    (backup_dir / ".claude").mkdir(parents=True, exist_ok=True)
                    shutil.copy(settings_path, backup_dir / ".claude" / "settings.json")
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
            print("RESULT changed")
        return

    raise SystemExit(f"unknown mode: {mode}")


main()
PY

SETTINGS="$ROOT/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$SETTINGS" 2>/dev/null; then
    die "$SETTINGS is not valid JSON; fix or move it first"
  fi
fi

# Unique per run: same-second back-to-back applies must not collide and silently
# clobber an earlier run's backup. Timestamp plus pid, then an incrementing
# suffix in the unlikely case that name is already taken.
backup_stamp="$(date +%Y%m%d-%H%M%S)-$$"
BACKUP="$ROOT/.claude/.kit-backup/$backup_stamp"
backup_suffix=1
while [ -e "$BACKUP" ]; do
  BACKUP="$ROOT/.claude/.kit-backup/$backup_stamp-$backup_suffix"
  backup_suffix=$((backup_suffix+1))
done
# $BACKUP mirrors $ROOT: every pre-image under it (kit files, adopter files,
# settings.json) sits at the same relative path it has under $ROOT, so
# "cp -R $BACKUP/. $ROOT/" is a correct, complete restore with no nesting.
# The two --statusline pre-images (statusline.sh, the statusLine settings key)
# belong to $HOME, not $ROOT, so they get their own mirror of $HOME instead of
# being folded into $BACKUP, where they would either collide with a real
# same-named root path or need excluding from the root restore by hand.
HOME_BACKUP="$HOME/.claude/.kit-backup/$(basename "$BACKUP")"
changes=0

if [ -f "$SETTINGS" ]; then
  # Run the actual merge in memory, against a throwaway copy, before anything is
  # copied: whatever shape the real merge cannot handle refuses right here.
  shape_err="$(python3 "$MERGE_PY" check "$SETTINGS" "$TEMPLATE/.claude/settings.json" "$APPLY" "$BACKUP")"
  [ -z "$shape_err" ] || die "$SETTINGS: $shape_err; fix or move it first"
fi

KIT_OWNED=(
  .claude/hooks/session_start.py
  .claude/hooks/session_end.py
  .claude/hooks/user_prompt_submit.py
  .claude/lib/rootpath.py
  .claude/lib/registry.py
  .claude/lib/handoff.py
  .claude/scripts/set_context.sh
  .claude/scripts/registry_check.py
  .claude/skills/new-context/SKILL.md
  .claude/skills/new-context/templates/CLAUDE.md.tmpl
  .claude/skills/new-context/templates/HANDOFF.md.tmpl
  .claude/skills/new-context/templates/INDEX.md.tmpl
  .claude/skills/context-bootstrap/SKILL.md
  .claude/tests/test_rootpath.py
  .claude/tests/test_registry.py
  .claude/tests/test_handoff.py
)

# destination|source under template/
ADOPTER_OWNED=(
  "CLAUDE.md|CLAUDE.md"
  "CONTEXTS.md|CONTEXTS.md"
  ".claude/kit.json|kit.json.example"
  ".claude/hooks/save-phrases.txt|.claude/hooks/save-phrases.txt"
)

line() { printf '  %-8s %s\n' "$1" "$2"; }
place() { mkdir -p "$(dirname "$2")"; rm -f "$2"; cp "$1" "$2"; }
keep() { mkdir -p "$(dirname "$BACKUP/$1")"; cp "$2" "$BACKUP/$1"; }

echo "context router kit $VERSION"
echo "root: $ROOT"
if [ "$APPLY" = 1 ]; then echo "mode: apply"; else echo "mode: dry run, nothing will be written"; fi

echo
echo "kit files (replaced on every install, previous copy backed up):"
for rel in "${KIT_OWNED[@]}"; do
  src="$TEMPLATE/$rel"; dest="$ROOT/$rel"
  [ -f "$src" ] || die "missing from the kit: $rel"
  if [ ! -f "$dest" ]; then
    line NEW "$rel"; changes=$((changes+1))
    [ "$APPLY" = 1 ] && place "$src" "$dest"
  elif cmp -s "$src" "$dest"; then
    line same "$rel"
  else
    line REPLACE "$rel"; changes=$((changes+1))
    if [ "$APPLY" = 1 ]; then keep "$rel" "$dest"; place "$src" "$dest"; fi
  fi
done
[ "$APPLY" = 1 ] && chmod +x "$ROOT/.claude/scripts/set_context.sh"

echo
echo "your files (seeded when absent, never overwritten):"
contexts_seeded=0
for pair in "${ADOPTER_OWNED[@]}"; do
  rel="${pair%%|*}"; srcrel="${pair##*|}"
  src="$TEMPLATE/$srcrel"; dest="$ROOT/$rel"
  if [ -f "$dest" ] && [ "$FORCE" = 0 ]; then
    line SKIP "$rel (yours)"
  elif [ -f "$dest" ]; then
    line REPLACE "$rel (--force)"; changes=$((changes+1))
    if [ "$APPLY" = 1 ]; then keep "$rel" "$dest"; place "$src" "$dest"; fi
    [ "$rel" = "CONTEXTS.md" ] && contexts_seeded=1
  else
    line NEW "$rel"; changes=$((changes+1))
    [ "$APPLY" = 1 ] && place "$src" "$dest"
    [ "$rel" = "CONTEXTS.md" ] && contexts_seeded=1
  fi
done

# An upgrade-only nag, not a change. From 1.1.0 the SessionStart hook prints a registry
# digest and CLAUDE.md no longer imports the whole registry. CLAUDE.md is adopter-owned and
# is never overwritten, so someone upgrading from 1.0.0 keeps their "@CONTEXTS.md" line,
# gets the new hook, and sees none of the saving. Nothing breaks either way, which is exactly
# why it needs saying out loud: a silent no-op is the kind of thing nobody ever notices.
if [ -f "$ROOT/CLAUDE.md" ] && grep -q '^@CONTEXTS\.md[[:space:]]*$' "$ROOT/CLAUDE.md"; then
  echo
  echo "note: your CLAUDE.md still has the line \"@CONTEXTS.md\", which imports the whole"
  echo "      registry into every session. Since 1.1.0 the SessionStart hook prints a digest"
  echo "      of it instead, so that import is now redundant and costs tokens every session."
  echo "      CLAUDE.md is yours and is never overwritten, so remove that line by hand, and"
  echo "      add a first activation step: read the context's own block in CONTEXTS.md."
fi

echo
echo "hook entries in .claude/settings.json:"
set +e
merge="$(python3 "$MERGE_PY" merge "$SETTINGS" "$TEMPLATE/.claude/settings.json" "$APPLY" "$BACKUP")"
merge_status=$?
set -e
if [ "$merge_status" -ne 0 ]; then
  echo "$merge" >&2
  die "the settings merge failed unexpectedly (python exit $merge_status); $SETTINGS was not touched by this step, but files listed above may already have been written; fix $SETTINGS and re-run"
fi
echo "$merge" | grep -v '^RESULT'
echo "$merge" | grep -q '^RESULT changed' && changes=$((changes+1)) || true

if [ "$STATUSLINE" = 1 ]; then
  echo
  echo "status line (the only thing written outside the root):"
  sl="$HOME/.claude/statusline.sh"
  if [ -f "$sl" ] && [ "$FORCE" = 0 ]; then
    line SKIP "\$HOME/.claude/statusline.sh (yours)"
  elif [ -f "$sl" ]; then
    line REPLACE "\$HOME/.claude/statusline.sh (--force)"; changes=$((changes+1))
    if [ "$APPLY" = 1 ]; then
      mkdir -p "$HOME_BACKUP/.claude"; cp "$sl" "$HOME_BACKUP/.claude/statusline.sh"
      cp "$TEMPLATE/statusline.sh" "$sl"; chmod +x "$sl"
    fi
  else
    line NEW "\$HOME/.claude/statusline.sh"; changes=$((changes+1))
    if [ "$APPLY" = 1 ]; then
      mkdir -p "$HOME/.claude"
      cp "$TEMPLATE/statusline.sh" "$sl"; chmod +x "$sl"
    fi
  fi
  sl_settings_out="$(python3 - "$HOME/.claude/settings.json" "$APPLY" "$FORCE" "$HOME_BACKUP" <<'PY'
import json, sys
from pathlib import Path
dest, apply_, force, backup = Path(sys.argv[1]), sys.argv[2] == "1", sys.argv[3] == "1", Path(sys.argv[4])
data = json.loads(dest.read_text(encoding="utf-8")) if dest.is_file() else {}
entry = {"type": "command", "command": 'bash "$HOME/.claude/statusline.sh"', "padding": 0}
if "statusLine" in data and not force:
    print("  SKIP     a status line is already configured; add this yourself if you want ours:")
    print(f"           {json.dumps({'statusLine': entry})}")
elif "statusLine" in data:
    print("  REPLACE  statusLine in $HOME/.claude/settings.json (--force)")
    if apply_:
        # $HOME_BACKUP mirrors $HOME, the same way $BACKUP mirrors $ROOT.
        home_claude = backup / ".claude"
        home_claude.mkdir(parents=True, exist_ok=True)
        (home_claude / "settings.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        data["statusLine"] = entry
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
else:
    print("  NEW      statusLine in $HOME/.claude/settings.json")
    if apply_:
        data["statusLine"] = entry
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
)"
  echo "$sl_settings_out"
  # The bash-side statusline.sh NEW/REPLACE lines above already count toward
  # $changes; this python step is the one that used to report a real write
  # ("NEW statusLine in ...") while never touching the counter, so the summary
  # could still say "no changes" underneath it.
  case "$sl_settings_out" in
    *"  NEW      statusLine"*|*"  REPLACE  statusLine"*) changes=$((changes+1));;
  esac
fi

if [ "$APPLY" = 1 ]; then
  printf '%s\n' "$VERSION" > "$ROOT/.claude/.kit-version"
fi

echo
if [ "$APPLY" = 1 ]; then
  # Printed here, from the exact variables that made the backup, rather than
  # left to the spec, the plan or a person's memory: a restore instruction
  # that is generated cannot drift from the layout it describes.
  if [ -d "$BACKUP" ]; then
    echo "backups: $BACKUP"
    echo "restore: cp -R \"$BACKUP/.\" \"$ROOT/\""
  fi
  if [ -d "$HOME_BACKUP" ]; then
    echo "home backups: $HOME_BACKUP"
    echo "restore (home): cp -R \"$HOME_BACKUP/.\" \"$HOME/\""
  fi
  if [ "$changes" = 0 ]; then echo "no changes"; else echo "$changes change(s) applied"; fi
  echo
  echo "next:"
  echo "  1. start Claude Code in $ROOT so the hooks load"
  if [ "$contexts_seeded" = 1 ]; then
    echo "  2. ask it to run the context-bootstrap skill; it inventories your folders and writes your registry"
  else
    echo "  2. CONTEXTS.md already exists (yours, left alone); ask it to run the new-context skill for anything not yet registered"
  fi
  echo "  3. then: python3 \"$ROOT/.claude/scripts/registry_check.py\""
else
  if [ "$changes" = 0 ]; then echo "nothing to do"; else echo "$changes change(s) would be applied; re-run with --apply"; fi
fi
