import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import handoff  # noqa: E402

SAMPLE = """# northwind handoff
updated: 2026-09-09 17:40  |  session: "pick up northwind", id abc-123

## Now
- **SQL patch-compliance workbook**
  status: deploys in lab
  next:   validate against ARG
- **AKS upgrades**
  status: waiting on window
  next:   propose window

## Open questions
- second subscription too?

## Repo state
- none

## Recent
- 2026-09-09  status page built
- 2026-09-02  kube audit written
"""


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.nw = self.root / "customers" / "northwind" / "HANDOFF.md"
        self.nw.parent.mkdir(parents=True)
        self.nw.write_text(SAMPLE)
        old = self.root / "customers" / "fabrikam" / "HANDOFF.md"
        old.parent.mkdir(parents=True)
        old.write_text("# fabrikam handoff\nupdated: 2026-01-01 09:00\n\n## Now\n- **certs**\n  status: done\n")
        stamp = (dt.datetime.now() - dt.timedelta(days=40)).timestamp()
        os.utime(old, (stamp, stamp))
        noise = self.root / "personal" / "sidegarden" / "node_modules" / "x" / "HANDOFF.md"
        noise.parent.mkdir(parents=True)
        noise.write_text("# noise handoff\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_recent_parses_context_updated_and_workstreams(self):
        found = handoff.find_recent_handoffs(self.root, days=14)
        self.assertEqual([h["context"] for h in found], ["northwind"])
        self.assertEqual(found[0]["updated"], "2026-09-09 17:40")
        self.assertEqual(found[0]["workstreams"], ["SQL patch-compliance workbook", "AKS upgrades"])
        self.assertEqual(found[0]["path"], self.nw)

    def test_find_recent_includes_old_when_window_is_wide(self):
        found = handoff.find_recent_handoffs(self.root, days=365)
        self.assertEqual({h["context"] for h in found}, {"northwind", "fabrikam"})

    def test_find_recent_with_homes_ignores_copies_outside_them(self):
        """A backup mirror of a handoff, inside the root, is not a second context.

        Snapshot tooling copies every HANDOFF.md into a folder under the root, so the
        same context is found twice: once at its home and once in the mirror. Only the
        copy sitting exactly at a registered home is the context's handoff.
        """
        backup = self.root / "repos" / "workspace" / "private" / "handoffs" / "customers" / "northwind" / "HANDOFF.md"
        backup.parent.mkdir(parents=True)
        backup.write_text(SAMPLE)
        found = handoff.find_recent_handoffs(self.root, days=14, homes=["customers/northwind"])
        self.assertEqual([h["path"] for h in found], [self.nw])

    def test_append_recent_inserts_at_top_and_caps(self):
        for i in range(12):
            handoff.append_recent(self.nw, f"- 2026-09-1{i % 10}  event {i}")
        text = self.nw.read_text()
        recent = text.split("## Recent\n", 1)[1].strip().splitlines()
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0], "- 2026-09-11  event 11")
        self.assertNotIn("kube audit written", text)
        self.assertIn("## Open questions\n- second subscription too?", text)

    def test_append_recent_keeps_non_bullet_lines(self):
        p = self.root / "customers" / "keep" / "HANDOFF.md"
        p.parent.mkdir(parents=True)
        p.write_text(
            "# keep handoff\nupdated: 2026-09-09 10:00\n\n## Recent\n"
            "- 2026-09-01  old thing\n"
            "  more detail\n"
            "\n"
            "Note from the owner: keep this\n"
        )
        handoff.append_recent(p, "- 2026-09-09  new thing")
        body = p.read_text().split("## Recent\n", 1)[1]
        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "- 2026-09-09  new thing")
        self.assertEqual(lines[1], "- 2026-09-01  old thing")
        self.assertIn("  more detail", lines)
        self.assertIn("Note from the owner: keep this", lines)

    def test_append_recent_creates_section_when_missing(self):
        p = self.root / "customers" / "new" / "HANDOFF.md"
        p.parent.mkdir(parents=True)
        p.write_text("# new handoff\nupdated: 2026-09-09 10:00\n\n## Now\n- **x**\n  status: y\n")
        handoff.append_recent(p, "- 2026-09-09 10:05  session ended (other), id zzz")
        self.assertTrue(p.read_text().rstrip().endswith("## Recent\n- 2026-09-09 10:05  session ended (other), id zzz"))


if __name__ == "__main__":
    unittest.main()
