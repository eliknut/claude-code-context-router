"""The cloud check is frozen at 2.0.1: same command in, same decision out.

Every round of work on the shell-write parser that touched a splitter shared with the
cloud check reopened a bare az or terraform call somewhere. So the cloud check's
behaviour is pinned to a vendored copy of the 2.0.1 guard (fixtures/guard_2_0_1.py),
and this test compares the two over a corpus of awkward commands (fixtures/
cloud_corpus.json: comments, heredocs, quotes, substitutions, wrappers). The root is
a path that does not exist and the cwd lies outside it, so no command has a write
target in any context and the shell-write check can never be what decides.

If this fails, the cloud check changed. That is a deliberate decision with its own
review, never a side effect: update the fixture only together with that decision.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import guard  # noqa: E402

_spec = importlib.util.spec_from_file_location("guard_2_0_1", HERE / "fixtures" / "guard_2_0_1.py")
frozen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frozen)

CORPUS = json.loads((HERE / "fixtures" / "cloud_corpus.json").read_text(encoding="utf-8"))

REG = {
    "solo": {"home": "clients/solo", "owns": [], "cloudctx": "solo (Azure, solo.example.com)"},
    "multi": {"home": "clients/multi", "owns": [],
              "cloudctx": "multi (Azure, a.example.com), multi-b (Azure, b.example.com)"},
    "local": {"home": "repos/local", "owns": [], "cloudctx": "none"},
}
ROOT = Path("/nonexistent-root-for-frozen-cloud-test")
MEMORY = Path("/nonexistent-memory-for-frozen-cloud-test")


def outcome(decision):
    return (decision.action, decision.reason, decision.updated_input)


class FrozenCloudCheckTests(unittest.TestCase):
    def test_the_corpus_is_big_enough_to_mean_something(self):
        self.assertGreaterEqual(len(CORPUS), 150)

    def test_every_command_gets_the_2_0_1_cloud_decision(self):
        mismatches = []
        for active in ("solo", "multi", "local", ""):
            for command in CORPUS:
                tool_input = {"command": command}
                old = outcome(frozen.decide("Bash", tool_input, active, REG, ROOT, MEMORY))
                new = outcome(guard.decide("Bash", tool_input, active, REG, ROOT, MEMORY,
                                           cwd="/nonexistent-cwd-for-frozen-cloud-test"))
                if old != new:
                    mismatches.append((active, command, old[0], new[0]))
        self.assertEqual(mismatches, [], f"{len(mismatches)} cloud decisions changed")


if __name__ == "__main__":
    unittest.main()
