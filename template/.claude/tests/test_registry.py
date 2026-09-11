import json, subprocess, sys, tempfile, unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(LIB))
import registry  # noqa: E402

SAMPLE = """# Contexts

## Layout notes
Not a context block: this sits above the marker.

<!-- registry -->

## northwind
kind: customer
status: active
aliases: nw, northwind traders
cloud: northwind (prod)
home: clients/northwind
iac_names: northwind
rules:
  - read-only by default
  - confirm the environment before any write

## fabrikam
kind: customer
status: active
aliases: fab, fabrikam two
cloud: fabrikam (prod), fabrikam-two (test)
home: clients/fabrikam
iac_names: fabrikam, fabrikam-two

## stubco
kind: customer
status: stub
aliases:
cloud: stubco (tenant TODO)
home: clients/stubco
iac_names:

## platform
kind: platform
status: active
aliases: shared, modules
cloud: none
home: repos/infra
owns:
  - repos/infra/modules

## ignore
paths: repos/scratch
"""

CFG = {"scanned_parents": ["clients", "repos", "personal"], "iac": {"root": "repos/infra"}}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reg_path = self.root / "CONTEXTS.md"
        self.reg_path.write_text(SAMPLE, encoding="utf-8")
        for folder in [
            "clients/northwind", "clients/fabrikam", "clients/stubco", "clients/newcorp",
            "repos/infra/modules",
            "repos/infra/env-a/environments/prod/fabrikam/zone-a",
            "repos/infra/env-b/environments/prod/northwind",
            "repos/infra/env-b/environments/prod/unlisted",
            "repos/scratch", "personal/game",
        ]:
            (self.root / folder).mkdir(parents=True)
        self.reg = registry.load_registry(self.reg_path)

    def test_parses_blocks_and_lists(self):
        nw = self.reg["northwind"]
        self.assertEqual(nw["kind"], "customer")
        self.assertEqual(nw["aliases"], ["nw", "northwind traders"])
        self.assertEqual(nw["iac_names"], ["northwind"])
        self.assertEqual(nw["rules"], ["read-only by default",
                                       "confirm the environment before any write"])
        self.assertEqual(self.reg["fabrikam"]["iac_names"], ["fabrikam", "fabrikam-two"])
        self.assertEqual(self.reg["stubco"]["aliases"], [])
        self.assertEqual(self.reg["platform"]["owns"], ["repos/infra/modules"])
        self.assertNotIn("Layout notes", self.reg)
        self.assertEqual(self.reg["ignore"]["paths"], ["repos/scratch"])

    def test_resolve_by_name_alias_and_case(self):
        self.assertEqual(registry.resolve(self.reg, "NW"), ["northwind"])
        self.assertEqual(registry.resolve(self.reg, "fabrikam two"), ["fabrikam"])
        self.assertEqual(registry.resolve(self.reg, "Platform"), ["platform"])
        self.assertEqual(registry.resolve(self.reg, "nothing"), [])

    def test_unregistered_folders_respects_home_owns_prefix_and_ignore(self):
        self.assertEqual(
            registry.unregistered_folders(self.reg, self.root, CFG),
            ["clients/newcorp", "personal/game"],
        )

    def test_scanned_parent_home_does_not_cover_new_children(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "CONTEXTS.md").write_text(
            "# Contexts\n\n<!-- registry -->\n\n"
            "## portfolio\nkind: platform\nstatus: active\ncloud: none\nhome: clients\n\n"
            "## northwind\nkind: customer\nstatus: active\ncloud: northwind\nhome: clients/northwind\n",
            encoding="utf-8")
        for folder in ["clients/northwind", "clients/brandnew"]:
            (root / folder).mkdir(parents=True)
        reg = registry.load_registry(root / "CONTEXTS.md")
        self.assertEqual(registry.unregistered_folders(reg, root, {"scanned_parents": ["clients"]}),
                         ["clients/brandnew"])

    def test_ignored_scanned_parent_hides_children_but_home_as_bare_parent_does_not(self):
        """An `ignore` entry naming a whole scanned parent must suppress every child under it,
        unconditionally, because the person has explicitly said the whole path is out of scope.

        A `home` naming a bare scanned parent must keep behaving as it already does: it covers
        only itself, not new children under it, which is exactly what lets this check notice a
        new client folder appearing next to an existing one. This is the half that regresses
        first if the ignore fix is ever generalised to `home` and `owns` as well, so it is
        pinned in the same test as the ignore behaviour it must not disturb.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "CONTEXTS.md").write_text(
            "# Contexts\n\n<!-- registry -->\n\n"
            "## portfolio\nkind: platform\nstatus: active\ncloud: none\nhome: clients\n\n"
            "## ignore\npaths: personal\n",
            encoding="utf-8")
        for folder in ["clients/brandnew", "personal/sideproject", "personal/other"]:
            (root / folder).mkdir(parents=True)
        reg = registry.load_registry(root / "CONTEXTS.md")
        cfg = {"scanned_parents": ["clients", "personal"]}
        # personal/sideproject and personal/other: suppressed, "personal" is ignored outright.
        # clients/brandnew: still reported, "clients" is only a bare home, not an ignore entry.
        self.assertEqual(registry.unregistered_folders(reg, root, cfg), ["clients/brandnew"])

    def test_missing_homes(self):
        (self.root / "clients/stubco").rmdir()
        self.assertEqual(registry.missing_homes(self.reg, self.root), ["stubco"])

    def test_iac_names(self):
        self.assertEqual(registry.env_dir_names(self.root, CFG),
                         {"fabrikam", "northwind", "unlisted"})
        self.assertEqual(registry.iac_names_without_block(self.reg, self.root, CFG), {"unlisted"})

    def test_todo_placeholders(self):
        self.assertEqual(registry.todo_placeholders(self.reg), ["stubco"])

    def test_todo_placeholders_flags_todo_inside_rules(self):
        sample = """# Contexts

<!-- registry -->

## newcorp
kind: customer
status: active
cloud: newcorp (prod)
home: clients/newcorp
rules:
  - confirm tenant TODO before any write
"""
        path = self.root / "RULES-CONTEXTS.md"
        path.write_text(sample, encoding="utf-8")
        reg = registry.load_registry(path)
        # No string field contains "TODO"; only the rules list entry does.
        self.assertTrue(all("TODO" not in v for v in reg["newcorp"].values()
                             if isinstance(v, str)))
        self.assertEqual(registry.todo_placeholders(reg), ["newcorp"])

    def test_scanned_parents_from_config(self):
        self.assertEqual(registry.scanned_parents(self.root, {"scanned_parents": ["clients"]}),
                         ["clients"])

    def test_scanned_parents_auto_detects_visible_folders(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for folder in ["alpha", "beta", ".hidden"]:
            (root / folder).mkdir()
        (root / "loose.md").write_text("x", encoding="utf-8")
        self.assertEqual(registry.scanned_parents(root, {}), ["alpha", "beta"])

    def test_env_dir_names_is_empty_without_an_iac_root(self):
        self.assertEqual(registry.env_dir_names(self.root, {}), set())
        self.assertEqual(registry.iac_names_without_block(self.reg, self.root, {}), set())

    def test_registry_check_omits_iac_sections_without_config(self):
        out = subprocess.run([sys.executable, str(SCRIPTS / "registry_check.py"),
                              "--root", str(self.root)],
                             capture_output=True, text=True)
        self.assertNotIn("IaC environment names", out.stdout)
        self.assertIn("Folders with no registry block", out.stdout)

    def test_registry_check_includes_iac_sections_with_config(self):
        (self.root / ".claude").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "kit.json").write_text(json.dumps(CFG), encoding="utf-8")
        out = subprocess.run([sys.executable, str(SCRIPTS / "registry_check.py"),
                              "--root", str(self.root)],
                             capture_output=True, text=True)
        self.assertIn("IaC environment names with no registry block", out.stdout)
        self.assertIn("unlisted", out.stdout)

    def test_registry_check_reports_a_missing_registry_instead_of_a_traceback(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        # No CONTEXTS.md written at all: someone deleted it, or it was never seeded.
        out = subprocess.run([sys.executable, str(SCRIPTS / "registry_check.py"),
                              "--root", str(root)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn(str(root / "CONTEXTS.md"), out.stdout)
        self.assertNotIn("Traceback", out.stdout)
        self.assertNotIn("Traceback", out.stderr)


if __name__ == "__main__":
    unittest.main()
