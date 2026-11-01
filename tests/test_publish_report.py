"""Exercise report persistence against a local bare Git remote, without network."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("publish_report", Path(__file__).resolve().parents[1] / "scripts" / "publish_report.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublishReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.remote = self.base / "remote.git"
        self.work = self.base / "work"
        self.work.mkdir()
        self.git("init", "--bare", str(self.remote), cwd=self.base)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.invalid")
        (self.work / "data").mkdir()
        (self.work / "internship.md").write_text("Previous report\n")
        (self.work / "data" / "last-run.json").write_text('{"status":"complete"}\n')
        (self.work / "README.md").write_text("Original documentation\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "origin", "main")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.work, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()

    def publish(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return publisher.publish("main", self.work)

    def test_stale_report_is_persisted_without_committing_unrelated_staged_edits(self):
        (self.work / "internship.md").write_text("STALE: collection failed\nPrevious report\n")
        (self.work / "data" / "last-run.json").write_text('{"status":"failed"}\n')
        (self.work / "README.md").write_text("User documentation edit\n")
        self.git("add", "README.md")
        self.assertTrue(self.publish())
        self.assertEqual(self.git("--git-dir=" + str(self.remote), "show", "main:internship.md"), "STALE: collection failed\nPrevious report")
        self.assertEqual(self.git("--git-dir=" + str(self.remote), "show", "main:README.md"), "Original documentation")
        self.assertIn("README.md", self.git("diff", "--cached", "--name-only"))

    def test_unchanged_output_does_not_create_a_commit(self):
        previous = self.git("rev-parse", "HEAD")
        self.assertFalse(self.publish())
        self.assertEqual(self.git("rev-parse", "HEAD"), previous)

    def test_concurrent_remote_changes_are_not_overwritten(self):
        other = self.base / "other"
        self.git("clone", "--branch", "main", str(self.remote), str(other), cwd=self.base)
        self.git("config", "user.name", "Other User", cwd=other)
        self.git("config", "user.email", "other@example.invalid", cwd=other)
        (other / "README.md").write_text("Remote documentation edit\n")
        self.git("add", "README.md", cwd=other)
        self.git("commit", "-m", "Other change", cwd=other)
        self.git("push", "origin", "main", cwd=other)
        remote_head = self.git("rev-parse", "HEAD", cwd=other)
        (self.work / "internship.md").write_text("Updated report\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.git("--git-dir=" + str(self.remote), "rev-parse", "refs/heads/main"), remote_head)


if __name__ == "__main__":
    unittest.main()
