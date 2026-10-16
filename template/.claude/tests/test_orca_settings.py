"""orca-settings.json.tmpl is settings.json's four hooks with a root placeholder, plus the
memory folder, and orca_settings.py renders it with absolute paths for one root. If
settings.json gains or changes a hook, this fails until both agree. In an installed root
the generated orca-settings.json is checked against a fresh render too."""
import copy
import json
import os
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "scripts"))
sys.path.insert(0, str(CLAUDE / "lib"))
import orca_settings  # noqa: E402
import rootpath  # noqa: E402

TEMPLATE = (CLAUDE / "orca-settings.json.tmpl").read_text(encoding="utf-8")


def load(name):
    return json.loads((CLAUDE / name).read_text(encoding="utf-8"))


def absolute(command, root):
    return command.replace('"$CLAUDE_PROJECT_DIR/', str(root) + "/").replace('.py"', ".py")


class OrcaSettingsTemplateTests(unittest.TestCase):
    def setUp(self):
        self.project = load("settings.json")

    def render(self, root, home="/home/alex"):
        with mock.patch.object(Path, "home", return_value=Path(home)):
            return json.loads(orca_settings.render(TEMPLATE, Path(root)))

    def test_only_hooks_and_the_memory_folder(self):
        self.assertEqual(set(json.loads(TEMPLATE)), {"hooks", "autoMemoryDirectory"})

    def test_the_hooks_mirror_settings_json_with_absolute_paths(self):
        rendered = self.render("/srv/work")
        expected = copy.deepcopy(self.project["hooks"])
        for groups in expected.values():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = absolute(hook["command"], "/srv/work")
        self.assertEqual(rendered["hooks"], expected)

    def test_the_memory_folder_uses_the_slug_of_the_root(self):
        rendered = self.render("/srv/my_work.d", home="/home/alex")
        self.assertEqual(rendered["autoMemoryDirectory"],
                         "/home/alex/.claude/projects/-srv-my-work-d/memory")

    def test_the_memory_folder_matches_rootpath(self):
        rendered = self.render("/srv/work")
        with mock.patch.object(Path, "home", return_value=Path("/home/alex")):
            self.assertEqual(rendered["autoMemoryDirectory"],
                             str(rootpath.memory_dir(Path("/srv/work"))))

    def test_a_root_with_a_space_is_quoted_for_the_shell(self):
        rendered = self.render("/srv/my work")
        command = rendered["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertEqual(shlex.split(command),
                         ["python3", "/srv/my work/.claude/hooks/session_start.py"])

    def test_no_placeholder_survives(self):
        self.assertNotIn("@@", orca_settings.render(TEMPLATE, Path("/srv/work")))


class InstalledOrcaSettingsTests(unittest.TestCase):
    """Only in an installed root: the kit's own template tree has no generated file."""

    def setUp(self):
        self.path = CLAUDE / "orca-settings.json"
        if not self.path.is_file():
            self.skipTest("no generated orca-settings.json here (kit tree, not an install)")

    def test_the_generated_file_is_a_fresh_render_for_this_root(self):
        saved = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        try:
            root = rootpath.root()
        finally:
            if saved is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = saved
        self.assertEqual(self.path.read_text(encoding="utf-8"),
                         orca_settings.render(TEMPLATE, root))

    def test_every_hook_script_exists(self):
        for groups in json.loads(self.path.read_text(encoding="utf-8"))["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    program, script = shlex.split(hook["command"])
                    self.assertEqual(program, "python3")
                    self.assertTrue(Path(script).is_file(), script)


if __name__ == "__main__":
    unittest.main()
