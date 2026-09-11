import contextlib, importlib.util, io, json, os, shutil, sys, tempfile, unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import rootpath  # noqa: E402


def load_isolated(root: Path):
    """Import a copy of rootpath.py that lives under `root`/.claude/lib, as its own module."""
    lib = root / ".claude" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    shutil.copy(LIB / "rootpath.py", lib / "rootpath.py")
    spec = importlib.util.spec_from_file_location(f"rp_{root.name}", lib / "rootpath.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RootPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        if self.saved is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self.saved

    def test_root_prefers_the_env_var(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.tmp)
        self.assertEqual(rootpath.root(), self.tmp.resolve())

    def test_root_falls_back_to_the_file_location(self):
        mod = load_isolated(self.tmp)
        self.assertEqual(mod.root(), self.tmp.resolve())

    def test_memory_dir_slugs_the_root_path(self):
        # Contains an underscore AND a dot, deliberately: replacing only "/" (the old,
        # wrong rule) would leave both untouched and yield "-home-test.user-my_work",
        # which disagrees with Claude Code's actual rule of replacing every
        # non-alphanumeric character. This root would pass under the old rule and fail
        # under this one, which is the point.
        self.assertEqual(
            rootpath.memory_dir(Path("/home/test.user/my_work")),
            Path.home() / ".claude" / "projects" / "-home-test-user-my-work" / "memory",
        )

    def test_config_is_empty_when_absent(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rootpath.config(self.tmp)
        self.assertEqual(result, {})
        self.assertEqual(err.getvalue(), "")

    def test_config_reads_the_keys(self):
        (self.tmp / ".claude").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".claude" / "kit.json").write_text(json.dumps({"owner": "Ada"}), encoding="utf-8")
        self.assertEqual(rootpath.config(self.tmp)["owner"], "Ada")

    def test_config_survives_malformed_json(self):
        (self.tmp / ".claude").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".claude" / "kit.json").write_text("{ not json", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rootpath.config(self.tmp)
        self.assertEqual(result, {})
        self.assertIn("kit.json ignored", err.getvalue())

    def test_ctx_state_returns_the_marker_directory(self):
        self.assertEqual(rootpath.ctx_state(),
                         Path.home() / ".claude" / "ctx-state")


if __name__ == "__main__":
    unittest.main()
