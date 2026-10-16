import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(HERE))
import gitfixture  # noqa: E402
import worktree  # noqa: E402


@unittest.skipUnless(gitfixture.HAVE_GIT, "git not installed")
class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.main = gitfixture.make_repo(self.tmp / "root" / "repos" / "demo")
        self.wt = gitfixture.add_worktree(self.main, self.tmp / "workspaces" / "demo" / "rt", "dev/rt")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_a_path_under_an_unsearchable_folder_is_none_without_raising(self):
        locked = self.tmp / "locked"
        (locked / "inner").mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        self.assertIsNone(worktree.main_checkout(locked / "inner" / "x.md"))

    def test_a_folder_in_a_worktree_maps_to_the_main_checkout(self):
        self.assertEqual(worktree.main_checkout(self.wt), self.main)

    def test_a_file_that_does_not_exist_yet_still_maps(self):
        target = self.wt / "docs" / "new.md"
        self.assertEqual(worktree.main_checkout(target), self.main)
        self.assertEqual(worktree.to_main_path(target), self.main / "docs" / "new.md")

    def test_the_worktree_top_maps_to_the_main_checkout_itself(self):
        self.assertEqual(worktree.to_main_path(self.wt), self.main)

    def test_the_main_checkout_is_not_a_worktree(self):
        self.assertIsNone(worktree.main_checkout(self.main / "x.md"))
        self.assertEqual(worktree.to_main_path(self.main / "x.md"), self.main / "x.md")

    def test_a_folder_outside_git_is_not_a_worktree(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(worktree.main_checkout(plain / "x.md"))
        self.assertEqual(worktree.to_main_path(plain / "x.md"), plain / "x.md")

    def test_a_worktree_whose_main_checkout_is_outside_the_root_still_maps(self):
        """The helper knows nothing about the root; the caller decides what counts."""
        other = gitfixture.make_repo(self.tmp / "elsewhere" / "repo")
        wt = gitfixture.add_worktree(other, self.tmp / "workspaces" / "other" / "rt", "dev/o")
        self.assertEqual(worktree.main_checkout(wt / "a.txt"), other)

    def test_a_worktree_path_with_spaces_maps_to_the_main_checkout(self):
        wt = gitfixture.add_worktree(self.main, self.tmp / "my workspaces" / "demo" / "router test",
                                     "dev/spaces")
        self.assertEqual(worktree.to_main_path(wt / "my notes.md"), self.main / "my notes.md")

    def test_a_relative_gitdir_is_resolved_against_the_worktree(self):
        wt = gitfixture.add_worktree(self.main, self.tmp / "workspaces" / "demo" / "rel", "dev/rel",
                                     relative=True)
        self.assertFalse((wt / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip()
                         .startswith("/"), "fixture did not write a relative gitdir")
        self.assertEqual(worktree.main_checkout(wt / "x"), self.main)

    def test_a_git_file_without_commondir_is_not_a_worktree(self):
        """A submodule has a .git file too, but its gitdir holds no commondir."""
        sub = self.tmp / "super" / "sub"
        sub.mkdir(parents=True)
        moddir = self.tmp / "super" / ".git" / "modules" / "sub"
        moddir.mkdir(parents=True)
        (sub / ".git").write_text(f"gitdir: {moddir}\n", encoding="utf-8")
        self.assertIsNone(worktree.main_checkout(sub / "x"))

    def test_a_symlink_into_a_worktree_maps_through_the_link(self):
        link = self.tmp / "link"
        os.symlink(self.wt, link)
        self.assertEqual(worktree.to_main_path(link / "x.md"), self.main / "x.md")


if __name__ == "__main__":
    unittest.main()
