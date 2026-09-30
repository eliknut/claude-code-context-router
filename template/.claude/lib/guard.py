"""Decide whether a tool call stays inside the active context.

All decisions live here as pure functions over plain data, so every case is testable
without a session marker, a registry file or a live filesystem. The hook that calls
this (hooks/context_guard.py) does all the IO.
"""
from __future__ import annotations

import collections
import os
import re
import shlex
from pathlib import Path

# action is one of: allow, deny, ask, update.
# reason is shown to Claude for deny and ask, and to the user for update.
# updated_input is the partial tool_input to merge, for update only.
Decision = collections.namedtuple("Decision", ("action", "reason", "updated_input"))
Decision.__new__.__defaults__ = ("", None)

ALLOW = Decision("allow")


def _split_outside_parens(value: str) -> list[str]:
    """Split on commas that are not inside parentheses.

    The registry writes a scope as "name (Azure, tenant)", so the detail carries commas
    of its own and a plain split lands in the middle of one.
    """
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def cloud_scopes(entry: dict) -> list[str]:
    """The cloudctx scope names for one registry block, in order, without duplicates.

    Accepts either spelling of the key: current registries use "cloudctx", older
    templates used "cloud", and the router says to treat them as the same field.

    "none" is the registry saying the context has no tenant, so it is dropped rather
    than read as a name. It is not always bare: a block may write
    "none (<why>)", and taking that at face value would hand the guard a scope called
    "none" for a context that must never touch a cloud at all.
    """
    line = (entry.get("cloudctx") or entry.get("cloud") or "").strip()
    names: list[str] = []
    for part in _split_outside_parens(line):
        token = re.split(r"[\s(]", part, maxsplit=1)[0]
        if not token or token == "none" or token in names:
            continue
        names.append(token)
    return names


def _owned_paths(entry: dict) -> list[str]:
    """Every path a registry block claims: its home, plus each owns entry."""
    paths = []
    home = entry.get("home", "")
    if home and home != "none":
        paths.append(home.strip("/"))
    paths.extend(str(p).strip("/") for p in entry.get("owns", []) if str(p).strip("/"))
    return paths


def owner_of(target: str, reg: dict, root: Path, memory_root: Path) -> str:
    """The context that owns `target`, or "" when no context does.

    Resolved through symlinks first, so a link inside one context cannot be used to
    write into another. The longest matching claim wins: "clients" and
    "clients/acme" are both homes, and a file under the latter belongs to the
    latter.
    """
    real = Path(os.path.realpath(target))

    contexts_dir = Path(os.path.realpath(str(memory_root))) / "contexts"
    try:
        rel = real.relative_to(contexts_dir)
    except ValueError:
        pass
    else:
        return rel.parts[0] if rel.parts else ""

    try:
        rel = real.relative_to(Path(os.path.realpath(str(root))))
    except ValueError:
        return ""
    relative = rel.as_posix()

    best = ""
    best_length = -1
    for name, entry in reg.items():
        for claimed in _owned_paths(entry):
            if relative == claimed or relative.startswith(claimed + "/"):
                if len(claimed) > best_length:
                    best, best_length = name, len(claimed)
    return best


CLOUD_COMMANDS = {"az", "aws", "terraform", "terragrunt"}
NO_GUARD_TOKEN = "#noctx"
# Anything here means the command is more than one plain invocation, so rewriting it
# by wrapping the whole string would change what it does. Deliberately conservative:
# a false "not simple" costs one permission prompt, a false "simple" corrupts a command.
_NOT_SIMPLE = ("$(", "`", "<<", ">", "<", "&", "(", ")", "\\")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A shell starts a fresh command after each of these, so the invocation that follows is
# not the segment's first token. Used to split a segment further, not to split a command.
_COMMAND_POSITION = re.compile(r"\$\(|`|\(|\)")
# Words that can precede the real command without being it. Not exhaustive, and it
# does not need to be: a word we fail to skip costs a missed hit, which is the same
# position we are in today with no guard at all.
_WRAPPERS = frozenset({"if", "while", "until", "then", "do", "else", "elif", "time",
                       "sudo", "env", "xargs", "nohup", "command", "exec", "!"})
# Wrappers that hand the command a fresh environment. cloudctx scopes by exporting
# variables, so a rewrite through one of these produces a command that runs unscoped
# while reporting itself as scoped. Asking is the only honest answer.
#
# Only sudo, because only sudo is reachable: this set is consulted after cloud_hits()
# has already found a hit, and a hit needs segment_command() to see through the wrapper
# word, which it only does for the words in _WRAPPERS. su and doas are not in there and
# never will be: adding them would catch "su az ..." but not "su -c 'az ...'", the form
# people actually type, whose payload sits inside a quoted string this parser does not
# open. Naming them here made the set look closed when it was half-open, so su -c is
# deliberately out of scope and stated as such rather than pretended away.
_ENV_RESETTING = frozenset({"sudo"})


def split_segments(command: str) -> list[str]:
    """Split a command on ; && || | and newlines, ignoring separators inside quotes."""
    # A backslash-newline is a line continuation, not a separator. Splitting on the raw
    # newline instead cuts a single command in half and hides its command name.
    command = command.replace("\\\n", " ")
    segments: list[str] = []
    current = ""
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            current += ch
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            current += ch
            i += 1
            continue
        if command[i:i + 2] in ("&&", "||"):
            segments.append(current)
            current = ""
            i += 2
            continue
        if ch in ";|\n":
            segments.append(current)
            current = ""
            i += 1
            continue
        current += ch
        i += 1
    segments.append(current)
    return [s.strip() for s in segments if s.strip()]


def segment_command(segment: str) -> str:
    """The command one segment runs, with leading env assignments stripped.

    Tokenised with shlex rather than str.split, because an assignment whose value
    contains a space ("FOO='/tmp/my dir' az ...") otherwise splits mid-value and the
    real command is never seen. When shlex cannot parse the segment we do not guess:
    we scan every token for a cloud CLI name, because a missed cloud call is a silent
    unscoped command, while a missed ordinary command costs nothing.

    An earlier version fell back to the first token, on the grounds that the shell
    would refuse a segment shlex cannot parse. That holds for an unterminated quote
    and not for a trailing backslash, which bash reads as a line continuation and runs.
    Scanning for a cloud name specifically, rather than calling every unparseable
    segment a hit, keeps the failure closed where it matters: `echo "unterminated`
    must not prompt.
    """
    try:
        tokens = shlex.split(segment)
    except ValueError:
        for token in segment.split():
            name = os.path.basename(token.strip("\"'"))
            if name in CLOUD_COMMANDS:
                return name
        return ""
    for token in tokens:
        if _ASSIGNMENT.match(token):
            continue
        name = os.path.basename(token.strip("\"'"))
        if name in _WRAPPERS:
            continue
        return name
    return ""


def _parses(text: str) -> bool:
    """Whether shlex can tokenise this, that is, whether its quoting is self-consistent."""
    try:
        shlex.split(text)
    except ValueError:
        return False
    return True


def cloud_hits(command: str) -> list[str]:
    """Segments that invoke a cloud CLI without going through cloudctx.

    Segments are split again on command substitution and grouping, because a shell
    starts a fresh command after $( , a backtick or a parenthesis, and the invocation
    there is not the segment's first token.
    """
    hits = []
    for segment in split_segments(command):
        # No explicit cloudctx skip: "cloudctx" is not in CLOUD_COMMANDS, so a segment whose
        # command is cloudctx already fails the membership test below. A separate skip would
        # be a line no test could ever kill.
        if not _parses(segment):
            # The segment's own quoting is broken, so we cannot tell which token is the
            # command. segment_command falls back to scanning every token for a cloud name,
            # which is the conservative answer when the shell itself may still run it.
            if segment_command(segment) in CLOUD_COMMANDS:
                hits.append(segment)
            continue
        for part in _COMMAND_POSITION.split(segment):
            if not part.strip():
                continue
            if not _parses(part):
                # This part is unparseable only because the split above cut through a
                # quoted string that was intact a moment ago. Handing it to
                # segment_command would run the all-token fallback over ordinary prose,
                # so `echo "deploying terraform (prod)"` would read as a cloud call.
                continue
            if segment_command(part) in CLOUD_COMMANDS:
                # The whole segment, not the part: the hit list feeds the reason text.
                # Break so one segment contributes at most one hit.
                hits.append(segment)
                break
    return hits


def is_simple(command: str) -> bool:
    """True when wrapping the whole command in cloudctx exec is safe."""
    if len(split_segments(command)) != 1:
        return False
    if any(marker in command for marker in _NOT_SIMPLE):
        return False
    # Sound only because a backslash is rejected above: with no escapes in play, an odd
    # quote count means a quote this parser never closed, so a separator may have been
    # swallowed and the single-segment result cannot be trusted.
    return command.count('"') % 2 == 0 and command.count("'") % 2 == 0


def _starts_with_assignment(command: str) -> bool:
    """True when the command's first token sets an environment variable.

    cloudctx exec runs its command as an argv with no shell between, so wrapping
    "FOO=1 az ..." produces an argv whose first element is "FOO=1" and execution fails
    with FileNotFoundError. Asking is right: the rewrite could not have worked.
    """
    tokens = command.split()
    return bool(tokens) and bool(_ASSIGNMENT.match(tokens[0]))


def _first_token(command: str) -> str:
    """The command's own first word: no assignment stripping, no wrapper skipping.

    segment_command() looks through wrappers on purpose, so that `sudo az ...` is
    DETECTED as a cloud call. The rewrite must not look through them: wrapping
    `time az ...` or `env FOO=1 az ...` in cloudctx exec changes what actually runs.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return os.path.basename(tokens[0].strip("\"'")) if tokens else ""


def _decide_bash(tool_input: dict, active: str, reg: dict) -> Decision:
    """Decide a Bash call: rewrite it, refuse it, or stay out of the way.

    Only two answers carry weight here. A cloud call we can scope safely is rewritten; a
    cloud call we cannot scope is denied. There is deliberately no "ask" for anything that
    would touch a live tenant, because an ask is not reliably a pause: in a permission mode
    that auto-approves, it is indistinguishable from allow, and the command runs against
    whatever tenant is ambient. Observed directly: a hook returning ask
    for `az --version` produced no prompt at all.

    The remedy for every deny below is the same and is named in its reason: write the
    scoped form, `cloudctx exec <name> -- <command>`. That is what CLAUDE.md section 3
    requires anyway, so the deny costs a reformulation, never the work itself. `#noctx`
    remains the deliberate exit.

    The single surviving ask is a context with no cloud scope at all. There is no tenant to
    get wrong there, many contexts are in that state, and a local `terraform fmt` or
    `az --version` is legitimate work that a deny would dead-end.
    """
    command = tool_input.get("command", "")
    if NO_GUARD_TOKEN in command.split():
        return ALLOW
    if not cloud_hits(command):
        return ALLOW
    if not active:
        return Decision("deny",
                        "No context is active, so there is no account to scope this to. CLAUDE.md "
                        "section 3: no active context, no cloud command. Resolve a context first.")
    scopes = cloud_scopes(reg.get(active, {}))
    if not scopes:
        return Decision("ask",
                        f"Context '{active}' has no cloud scope (cloudctx: none) but this command "
                        f"calls a cloud CLI. Allow it only if it is local and needs no credentials, "
                        f"such as terraform fmt or az --version.")
    if len(scopes) > 1:
        return Decision("deny",
                        f"Context '{active}' has several cloud scopes ({', '.join(scopes)}). "
                        f"CLAUDE.md section 3: confirm the tenant first, then run "
                        f"cloudctx exec <name> -- <command>.")
    scope = scopes[0]
    if _starts_with_assignment(command):
        return Decision("deny",
                        f"This sets an environment variable before the cloud call, and cloudctx "
                        f"exec runs its command without a shell, so the rewrite would try to run a "
                        f"program named after the assignment. Use "
                        f"cloudctx exec {scope} -- env VAR=value <command>, which does work.")
    if any(token in _ENV_RESETTING for token in command.split()):
        return Decision("deny",
                        f"This runs the cloud CLI through sudo or su, which resets the "
                        f"environment. cloudctx scopes by exporting variables, so the rewrite "
                        f"would report itself as scoped to '{scope}' while actually running "
                        f"unscoped. Run it as cloudctx exec {scope} -- <command> without sudo, "
                        f"or scope it by hand.")
    # After the two branches above, so each keeps its more specific message, and before
    # is_simple(), which asks about shape rather than about what the first word is. The
    # spec only ever rewrites a segment whose own first token is the cloud CLI: everything
    # else reaches the CLI through another program, and cloudctx exec runs its argv with
    # no shell, so the wrapper either dies (exec, command, !) or, for env, re-sets the very
    # variable cloudctx exported and runs unscoped under a message claiming a scope.
    if _first_token(command) not in CLOUD_COMMANDS:
        return Decision("deny",
                        f"This reaches a cloud CLI through another command, so wrapping the "
                        f"whole line in cloudctx exec would change what runs: cloudctx executes "
                        f"its argv directly, with no shell. Put the wrapper inside instead, as "
                        f"cloudctx exec {scope} -- <wrapper> <cloud command>.")
    if not is_simple(command):
        return Decision("deny",
                        f"This calls a cloud CLI outside cloudctx, and it is not a single plain "
                        f"command, so it was not rewritten automatically. Run each cloud call as "
                        f"cloudctx exec {scope} -- <command>.")
    return Decision("update",
                    f"Scoped to cloudctx '{scope}' (CLAUDE.md section 3).",
                    {"command": f"cloudctx exec {scope} -- {command}"})


# Tool name to the field in tool_input holding the path it writes.
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}


def _decide_write(tool_name: str, tool_input: dict, active: str, reg: dict,
                  root: Path, memory_root: Path) -> Decision:
    """Deny a write that lands in a context other than the active one.

    The guard's only hard block, and deny rather than ask because the detection is path
    arithmetic with no parsing: resolve the target, find the longest home or owns claim
    that contains it, compare. There is nothing here that can misread a command, so there
    is no false-positive risk to hedge against.
    """
    if not active:
        # Without an active context there is no "other" to compare against.
        return ALLOW
    target = tool_input.get(WRITE_TOOLS[tool_name], "")
    if not target:
        return ALLOW
    owner = owner_of(target, reg, root, memory_root)
    if not owner or owner == active:
        return ALLOW
    return Decision("deny",
                    f"{target} belongs to context '{owner}', but '{active}' is the active context. "
                    f"CLAUDE.md section 4: never write into another context's home, handoff or "
                    f"memory. Switch context first, or write inside '{active}'.")


# Both names reach this hook: Task is the older spelling of the delegation tool.
AGENT_TOOLS = {"Agent", "Task"}


def context_header(active: str, entry: dict) -> str:
    """The block a delegated subagent needs, since it inherits none of this session."""
    home = entry.get("home", "") or "none"
    scopes = cloud_scopes(entry)
    lines = [f"[context: {active}]  home: {home}  cloud: {', '.join(scopes) if scopes else 'none'}"]
    rules = [r for r in entry.get("rules", []) if r]
    if rules:
        lines.append("Standing rules for this context:")
        lines.extend(f"  - {rule}" for rule in rules)
    # The prohibition, not a fence, because the prohibition is the actual rule: D1 denies
    # a write into ANOTHER context's territory and allows everything owned by no context
    # (.claude, CLAUDE.md, CONTEXTS.md), plus this context's own memory folder, which sits
    # outside the root entirely. "Write only inside <home>" was narrower than both D1 and
    # the work the router hands out, and it contradicted standing rules that tell a context
    # to change workspace files. The paths are still home union owns, not home alone: that
    # is the set owner_of() lets a write into, and a context may own paths outside its
    # home. _owned_paths drops a home of "none" already, so a context with neither home
    # nor owns still gets no line here.
    writable = _owned_paths(entry)
    if writable:
        lines.append(f"Never write into another context's home, handoff or memory folder. "
                     f"This context's own are: {', '.join(writable)}, plus its own memory folder.")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _header_prefix(active: str, entry: dict) -> str:
    """The first line of the header this guard would build for `active`.

    The idempotency check matches on this rather than on a bare "[context:" prefix.
    A literal marker cannot tell our own header from a different context's, from a
    stale one left by an earlier context, or from a prompt that merely opens with a
    context tag, and this router asks every reply to start with one.
    """
    return context_header(active, entry).split("\n", maxsplit=1)[0]


def _decide_agent(tool_input: dict, active: str, reg: dict) -> Decision:
    if not active:
        return ALLOW
    if tool_input.get("subagent_type") == "fork":
        # A fork inherits the parent conversation, so the header would only be noise.
        return ALLOW
    entry = reg.get(active, {})
    prompt = tool_input.get("prompt", "")
    # lstrip, so a prompt whose header is preceded by whitespace is still recognised
    # as already carrying one and does not collect a second copy.
    if prompt.lstrip().startswith(_header_prefix(active, entry)):
        return ALLOW
    return Decision("update",
                    f"Prepended the '{active}' context block to the subagent prompt.",
                    {"prompt": context_header(active, entry) + prompt})


def decide(tool_name: str, tool_input: dict, active: str, reg: dict,
           root: Path, memory_root: Path) -> Decision:
    """The guard's whole decision, as plain data in and plain data out."""
    if tool_name in WRITE_TOOLS:
        return _decide_write(tool_name, tool_input, active, reg, root, memory_root)
    if tool_name == "Bash":
        return _decide_bash(tool_input, active, reg)
    if tool_name in AGENT_TOOLS:
        return _decide_agent(tool_input, active, reg)
    return ALLOW
