"""UserPromptSubmit hook: when the prompt contains a stop phrase, tell Claude to update the handoff first."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import rootpath  # noqa: E402

PHRASES = Path(__file__).with_name("save-phrases.txt")
_owner_value = rootpath.config().get("owner", "")
OWNER = _owner_value.strip() if isinstance(_owner_value, str) else ""
QUESTIONS = f"Open questions for {OWNER}" if OWNER else "Open questions"
MESSAGE = (
    "SAVE TRIGGER: the user is stopping. Before replying, rewrite the active context's HANDOFF.md "
    f"(Now, {QUESTIONS}, Repo state; add one line at the top of Recent) and confirm its path "
    "in the reply. If no context is active, say so instead of writing anything."
)


def load_phrases() -> list[str]:
    if not PHRASES.exists():
        return []
    try:
        return [l.strip().lower() for l in PHRASES.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    except OSError:
        return []


def matches(prompt: str, phrases: list[str]) -> bool:
    p = prompt.lower()
    return any(re.search(r"(?<!\w)" + re.escape(ph) + r"(?!\w)", p) for ph in phrases)


def harness_notification(prompt: str) -> bool:
    """True for text the harness injected, not something the user typed."""
    return prompt.lstrip().startswith("<system-reminder>") or "[SYSTEM NOTIFICATION" in prompt


def main() -> int:
    try:
        data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        return 0
    prompt = data.get("prompt", "")
    if harness_notification(prompt):
        return 0
    if matches(prompt, load_phrases()):
        print(MESSAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
