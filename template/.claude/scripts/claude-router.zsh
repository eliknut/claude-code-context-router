# claude-router.zsh, sourced from ~/.zshrc (worktree mode).
# Defines `claude`. In a linked git worktree outside the router root, of a repo under the
# root (for example an Orca worktree at ~/orca/workspaces/<repo>/<name>), it runs claude
# with the router: the root's CLAUDE.md, its four hooks, its memory. Everywhere else it is
# plain `command claude`.
# The root is where this file lives (<root>/.claude/scripts/claude-router.zsh), captured
# once at source time. zsh only: source it, do not eval or copy it.
typeset -g _CLAUDE_ROUTER_ROOT=${${(%):-%x}:A:h:h:h}

# An alias named claude would make a `claude()` definition fail and then run in its
# place, bypass flags kept; drop it and use the keyword form.
unalias claude 2>/dev/null

function claude {
  emulate -L zsh
  local root=$_CLAUDE_ROUTER_ROOT
  if [[ -z $root ]]; then
    command claude "$@"
    return
  fi
  root=${root:A}
  local raw
  raw=$(unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_CEILING_DIRECTORIES; command git -c core.fsmonitor=false rev-parse --path-format=absolute --show-toplevel --git-common-dir 2>/dev/null)
  local -a out
  out=("${(@f)raw}")
  if (( ${#out} != 2 )) || [[ -z ${out[1]} || -z ${out[2]} ]]; then
    command claude "$@"
    return
  fi
  local top=${out[1]:A} common=${out[2]:A}
  local main=${common:h}
  # Not a linked worktree (top is the main checkout), a submodule (common dir is not
  # a .git folder), or a repo outside the root: leave claude alone.
  if [[ ${common:t} != .git || $top == $main || $main != $root/* || $top == $root || $top == $root/* ]]; then
    command claude "$@"
    return
  fi
  local -a args drop
  local a i=1
  while (( i <= $# )); do
    a=${@[i]}
    if [[ $a == --settings || $a == --settings=* || $a == --bare || $a == --safe-mode ]]; then
      print -u2 -r -- "claude-router: refused $a in a linked worktree (it would skip the router hooks); run 'command claude ...' to do it deliberately"
      return 2
    elif [[ $a == --dangerously-skip-permissions || $a == --allow-dangerously-skip-permissions \
          || $a == --permission-mode=bypassPermissions ]]; then
      drop+=("$a")
    elif [[ $a == --permission-mode && ${@[i+1]-} == bypassPermissions ]]; then
      drop+=("--permission-mode bypassPermissions")
      (( i++ ))
    else
      args+=("$a")
    fi
    (( i++ ))
  done
  if (( ${#drop} )); then
    print -u2 -r -- "claude-router: dropped ${(j: :)drop} (router rules need permission prompts)"
  fi
  CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1 command claude \
    --add-dir "$root" \
    --settings "$root/.claude/orca-settings.json" \
    "${args[@]}"
}
