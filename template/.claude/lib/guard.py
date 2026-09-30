"""Decide whether a tool call stays inside the active context.

All decisions live here as pure functions over plain data, so every case is testable
without a session marker, a registry file or a live filesystem. The hook that calls
this (hooks/context_guard.py) does all the IO.
"""
from __future__ import annotations

import collections
import functools
import os
import re
import shlex
import sys
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


@functools.lru_cache(maxsize=None)
def _case_insensitive(path: str) -> bool:
    """Whether the filesystem holding `path` ignores case, as APFS and NTFS do by default.

    realpath() does not normalise case, so on such a filesystem "Clients/Globex" and
    "clients/globex" are the same directory under two spellings, and a plain string
    comparison lets a write into another context's home through by spelling it
    differently. Detected by asking whether the path with its case flipped names the
    same directory. A path with no letters, or one that does not exist, cannot be
    probed; on darwin and win32 that falls back to True, the default there, because a
    wrong True costs a spurious deny while a wrong False reopens the bypass.
    """
    flipped = path.swapcase()
    if flipped != path and os.path.exists(path):
        try:
            return os.path.exists(flipped) and os.path.samefile(path, flipped)
        except OSError:
            pass
    return sys.platform in ("darwin", "win32")


def _fold(text: str, fold: bool) -> str:
    return text.casefold() if fold else text


def _iac_claim(relative_parts: list[str], iac_root: str, entry: dict, fold: bool) -> str:
    """The IaC folder of this context that contains the path, or "".

    A context's IaC folders are <iac root>/<repo>/environments/<stage>/<iac_name>*,
    the same folders the router resolves with find at activation. Stages starting
    with "_" (such as _base) are shared, so they are never claimed by one context.
    Matched case-insensitively, like find -iname.
    """
    base = [_fold(p, fold) for p in iac_root.strip("/").split("/") if p]
    if not base or relative_parts[:len(base)] != base:
        return ""
    rest = relative_parts[len(base):]
    if len(rest) < 4 or rest[1] != "environments" or rest[2].startswith("_"):
        return ""
    folder = rest[3].casefold()
    for name in entry.get("iac_names", []):
        if name and folder.startswith(name.casefold()):
            return "/".join(base + rest[:4])
    return ""


def owner_of(target: str, reg: dict, root: Path, memory_root: Path, iac_root: str = "") -> str:
    """The context that owns `target`, or "" when no context does.

    Resolved through symlinks first, so a link inside one context cannot be used to
    write into another. The longest matching claim wins: "clients" and
    "clients/acme" are both homes, and a file under the latter belongs to the
    latter. When `iac_root` is set, a context's IaC folders count as claims too, so
    a context's own environment folder inside a shared infrastructure repo belongs
    to that context and not to whichever context owns the repo.
    """
    real = Path(os.path.realpath(target))

    contexts_dir = Path(os.path.realpath(str(memory_root))) / "contexts"
    fold = _case_insensitive(str(contexts_dir.parent))
    # Compared part by part, casefolded when the filesystem ignores case, so that
    # "Contexts/Globex" cannot pass for somewhere other than contexts/globex.
    real_parts = [_fold(p, fold) for p in real.parts]
    base = [_fold(p, fold) for p in contexts_dir.parts]
    if real_parts[:len(base)] == base:
        if len(real.parts) == len(base):
            return ""
        name = real.parts[len(base)]
        if fold:
            # Map the spelling on the path back to the registry's own spelling, so the
            # active context's own memory folder still compares equal to its name.
            for known in reg:
                if known.casefold() == name.casefold():
                    return known
        return name

    root_real = Path(os.path.realpath(str(root)))
    fold = _case_insensitive(str(root_real))
    base = [_fold(p, fold) for p in root_real.parts]
    real_parts = [_fold(p, fold) for p in real.parts]
    if real_parts[:len(base)] != base:
        return ""
    relative_parts = real_parts[len(base):]
    relative = "/".join(relative_parts)

    best = ""
    best_length = -1
    for name, entry in reg.items():
        claims = [_fold(c, fold) for c in _owned_paths(entry)]
        if iac_root:
            iac = _iac_claim(relative_parts, iac_root, entry, fold)
            if iac:
                claims.append(iac)
        for claimed in claims:
            if relative == claimed or relative.startswith(claimed + "/"):
                if len(claimed) > best_length:
                    best, best_length = name, len(claimed)
    return best


CLOUD_COMMANDS = {"az", "aws", "terraform", "terragrunt"}
DEFAULT_WRAPPER = "cloudctx"


def cloud_wrapper(cfg: dict | None) -> str:
    """The credential wrapper named by kit.json's cloud_wrapper, or "" when disabled.

    Missing or null means the default, cloudctx. false turns cloud scoping off
    entirely. Any other non-empty string is the wrapper's command name, which must
    accept `<wrapper> exec <scope> -- <command>`. Anything else falls back to the
    default, because a malformed config must not silently switch scoping off.
    """
    value = (cfg or {}).get("cloud_wrapper", DEFAULT_WRAPPER)
    if value is False:
        return ""
    if isinstance(value, str) and value.strip() and not any(c.isspace() for c in value.strip()):
        return value.strip()
    return DEFAULT_WRAPPER
NO_GUARD_TOKEN = "#noctx"
# Anything here means the command is more than one plain invocation, so rewriting it
# by wrapping the whole string would change what it does. Deliberately conservative:
# a false "not simple" costs one permission prompt, a false "simple" corrupts a command.
_NOT_SIMPLE = ("$(", "`", "<<", ">", "<", "&", "(", ")", "\\")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Markers from _NOT_SIMPLE that only mean something to the shell outside quotes. Inside
# quotes they are literal text, as in the JMESPath `--query "[?contains(name,'web')]"`,
# so is_simple() looks for them in the unquoted text only. $( , a backtick and a
# backslash stay in _NOT_SIMPLE's checks on the whole string: they are live inside
# double quotes, and rejecting them inside single quotes too is merely conservative.
_UNQUOTED_ONLY = ("<<", ">", "<", "&", "(", ")")
# Words that can precede the real command without being it. Not exhaustive: a word
# that is not skipped costs a missed hit, the same outcome as having no guard.
_WRAPPERS = frozenset({"if", "while", "until", "then", "do", "else", "elif", "time",
                       "sudo", "env", "xargs", "nohup", "command", "exec", "!"})
# Wrappers that hand the command a fresh environment. The credential wrapper scopes by
# exporting variables, so a rewrite through one of these produces a command that runs
# unscoped while reporting itself as scoped. Only sudo is listed, because only words in
# _WRAPPERS are seen through at all. `su -c '...'` and `doas` are out of scope: their
# payload sits inside a quoted string this parser does not open.
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

    Falling back to the first token is not enough: bash refuses an unterminated
    quote, but reads a trailing backslash as a line continuation and runs it.
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


def _command_position_parts(segment: str) -> list[str]:
    """Split a segment wherever the shell starts a fresh command: $( , a backtick, ( and ).

    Quote-aware. A parenthesis inside quotes is literal, and splitting on it cut
    `az vm list --query "[?contains(name,'web')]"` into pieces that no longer parsed, so
    every piece was skipped and the call ran unscoped. Command substitution is still
    live inside double quotes, so $( and a backtick there open a fresh, unquoted
    context, and the quote state from before it is restored when it closes.
    """
    parts: list[str] = []
    current = ""
    quote = ""
    # One frame per open ( , $( or backtick: the frame's kind and the quote state
    # it interrupted.
    stack: list[tuple[str, str]] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if quote == "'":
            current += ch
            if ch == "'":
                quote = ""
            i += 1
            continue
        if ch == "\\":
            current += segment[i:i + 2]
            i += 2
            continue
        if quote == '"' and ch == '"':
            current += ch
            quote = ""
            i += 1
            continue
        if segment.startswith("$(", i):
            parts.append(current)
            current = ""
            stack.append(("(", quote))
            quote = ""
            i += 2
            continue
        if ch == "`":
            parts.append(current)
            current = ""
            if stack and stack[-1][0] == "`":
                quote = stack.pop()[1]
            else:
                stack.append(("`", quote))
                quote = ""
            i += 1
            continue
        if quote == '"':
            current += ch
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            current += ch
            i += 1
            continue
        if ch == "(":
            parts.append(current)
            current = ""
            stack.append(("(", ""))
            i += 1
            continue
        if ch == ")":
            parts.append(current)
            current = ""
            if stack and stack[-1][0] == "(":
                quote = stack.pop()[1]
            i += 1
            continue
        current += ch
        i += 1
    parts.append(current)
    return parts


def _unquoted_text(command: str) -> str:
    """The command with the contents of every quoted string removed, quotes included."""
    out = ""
    quote = ""
    for ch in command:
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            continue
        out += ch
    return out


def cloud_hits(command: str) -> list[str]:
    """Segments that invoke a cloud CLI directly, not through the credential wrapper.

    Segments are split again on command substitution and grouping, because a shell
    starts a fresh command after $( , a backtick or a parenthesis, and the invocation
    there is not the segment's first token.
    """
    hits = []
    for segment in split_segments(command):
        # No explicit wrapper skip: the wrapper is not in CLOUD_COMMANDS, so a segment
        # whose command is the wrapper already fails the membership test below.
        if not _parses(segment):
            # The segment's own quoting is broken, so we cannot tell which token is the
            # command. segment_command falls back to scanning every token for a cloud name,
            # which is the conservative answer when the shell itself may still run it.
            if segment_command(segment) in CLOUD_COMMANDS:
                hits.append(segment)
            continue
        if segment_command(segment) in CLOUD_COMMANDS:
            # The segment as a whole already runs a cloud CLI. Recorded before splitting,
            # because a split can only lose this hit, never find a better one.
            hits.append(segment)
            continue
        for part in _command_position_parts(segment):
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
    """True when wrapping the whole command in the credential wrapper is safe."""
    if len(split_segments(command)) != 1:
        return False
    unquoted = _unquoted_text(command)
    for marker in _NOT_SIMPLE:
        haystack = unquoted if marker in _UNQUOTED_ONLY else command
        if marker in haystack:
            return False
    # Sound only because a backslash is rejected above: with no escapes in play, an odd
    # quote count means a quote this parser never closed, so a separator may have been
    # swallowed and the single-segment result cannot be trusted.
    return command.count('"') % 2 == 0 and command.count("'") % 2 == 0


def _starts_with_assignment(command: str) -> bool:
    """True when the command's first token sets an environment variable.

    cloudctx exec runs its command as an argv with no shell between, so wrapping
    "FOO=1 az ..." produces an argv whose first element is "FOO=1" and execution fails
    with FileNotFoundError. Denying is right: the rewrite could not have worked.
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


def _decide_bash(tool_input: dict, active: str, reg: dict,
                 wrapper: str = DEFAULT_WRAPPER) -> Decision:
    """Decide a Bash call: rewrite it, refuse it, or stay out of the way.

    A cloud call that can be scoped safely is rewritten; one that cannot is denied.
    There is no "ask" for anything that would touch a live tenant, because in a
    permission mode that auto-approves, an ask is indistinguishable from allow and the
    command runs against whatever tenant is ambient.

    The remedy for every deny below is named in its reason: write the scoped form,
    `<wrapper> exec <name> -- <command>`, which CLAUDE.md section 3 requires anyway.
    `#noctx` remains the deliberate exit.

    The one ask is a context with no cloud scope at all: there is no tenant to get
    wrong, and a local `terraform fmt` or `az --version` is legitimate work.

    With cloud scoping disabled (`wrapper` empty) this check allows everything.
    """
    command = tool_input.get("command", "")
    if not wrapper:
        return ALLOW
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
                        f"Context '{active}' has no cloud scope (cloudctx: none), but this command "
                        f"calls a cloud CLI. Allow it only if it is local and needs no credentials, "
                        f"such as terraform fmt or az --version.")
    if len(scopes) > 1:
        return Decision("deny",
                        f"Context '{active}' has several cloud scopes ({', '.join(scopes)}). "
                        f"CLAUDE.md section 3: confirm the tenant first, then run "
                        f"{wrapper} exec <name> -- <command>.")
    scope = scopes[0]
    if _starts_with_assignment(command):
        return Decision("deny",
                        f"This sets an environment variable before the cloud call, and {wrapper} "
                        f"exec runs its command without a shell, so the rewrite would try to run a "
                        f"program named after the assignment. Use "
                        f"{wrapper} exec {scope} -- env VAR=value <command>, which does work.")
    if any(token in _ENV_RESETTING for token in command.split()):
        return Decision("deny",
                        f"This runs the cloud CLI through sudo, which resets the "
                        f"environment. {wrapper} scopes by exporting variables, so the rewrite "
                        f"would report itself as scoped to '{scope}' while actually running "
                        f"unscoped. Run it as {wrapper} exec {scope} -- <command> without sudo, "
                        f"or scope it by hand.")
    # After the two branches above, so each keeps its more specific message, and before
    # is_simple(), which asks about shape rather than about what the first word is. Only a
    # command whose own first token is the cloud CLI is rewritten: anything else reaches
    # the CLI through another program, and the wrapper runs its argv with no shell, so the
    # outer program either dies (exec, command, !) or, for env, re-sets the very variable
    # the wrapper exported and runs unscoped under a message claiming a scope.
    if _first_token(command) not in CLOUD_COMMANDS:
        return Decision("deny",
                        f"This reaches a cloud CLI through another command, so wrapping the "
                        f"whole line in {wrapper} exec would change what runs: {wrapper} executes "
                        f"its argv directly, with no shell. Put the other command inside instead, "
                        f"as {wrapper} exec {scope} -- <command> <cloud command>.")
    if not is_simple(command):
        return Decision("deny",
                        f"This calls a cloud CLI outside {wrapper}, and it is not a single plain "
                        f"command, so it was not rewritten automatically. Run each cloud call as "
                        f"{wrapper} exec {scope} -- <command>.")
    return Decision("update",
                    f"Scoped to {wrapper} '{scope}' (CLAUDE.md section 3).",
                    {"command": f"{wrapper} exec {scope} -- {command}"})


# Tool name to the field in tool_input holding the path it writes.
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}


def _decide_write(tool_name: str, tool_input: dict, active: str, reg: dict,
                  root: Path, memory_root: Path, iac_root: str = "") -> Decision:
    """Deny a write that lands in a context other than the active one.

    Deny rather than ask because the detection is path arithmetic with no parsing:
    resolve the target, find the longest home, owns or IaC claim that contains it,
    compare. Nothing here can misread a command.
    """
    if not active:
        # Without an active context there is no "other" to compare against.
        return ALLOW
    target = tool_input.get(WRITE_TOOLS[tool_name], "")
    if not target:
        return ALLOW
    owner = owner_of(target, reg, root, memory_root, iac_root)
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
    # Stated as a prohibition, not a fence, because that is the rule the write check
    # enforces: a write into ANOTHER context is denied, while files no context owns
    # (.claude, CLAUDE.md, CONTEXTS.md) and this context's own memory folder are allowed.
    # The paths listed are home plus owns, since a context may own paths outside its
    # home. A context with neither gets no line here.
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
           root: Path, memory_root: Path, cfg: dict | None = None) -> Decision:
    """The guard's whole decision, as plain data in and plain data out.

    `cfg` is the parsed .claude/kit.json: its cloud_wrapper names the credential
    wrapper (or disables the cloud check), and its iac.root lets IaC folders count
    as owned by their context.
    """
    cfg = cfg or {}
    if tool_name in WRITE_TOOLS:
        iac = cfg.get("iac")
        iac_root = str(iac.get("root") or "") if isinstance(iac, dict) else ""
        return _decide_write(tool_name, tool_input, active, reg, root, memory_root, iac_root)
    if tool_name == "Bash":
        return _decide_bash(tool_input, active, reg, cloud_wrapper(cfg))
    if tool_name in AGENT_TOOLS:
        return _decide_agent(tool_input, active, reg)
    return ALLOW
