"""Server records: register, list, sweep the dead, and stop the living."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from interfacile import procs


class ProcsCase(unittest.TestCase):
    def setUp(self):
        # Records live under XDG_CONFIG_HOME — point it at a temp dir so a test
        # can never see, or kill, a server the developer is actually running.
        self.home = tempfile.mkdtemp(prefix="ifc-test-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.home
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old


class TestRecords(ProcsCase):
    def test_a_registered_server_is_listed_and_found_by_repo(self):
        procs.register(8790, "http://x.localhost:8790/", ["/tmp/repo-a"])
        recs = procs.servers()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["port"], 8790)
        self.assertEqual(recs[0]["pid"], os.getpid())
        self.assertEqual(len(procs.serving("/tmp/repo-a")), 1)
        self.assertEqual(procs.serving("/tmp/repo-b"), [])

    def test_unregister_removes_it(self):
        procs.register(8790, "u", ["/tmp/repo-a"])
        procs.unregister()
        self.assertEqual(procs.servers(), [])

    def test_a_record_whose_process_is_gone_is_swept_not_reported(self):
        """kill -9 leaves the file behind. It's a lie, so it goes."""
        dead = self._dead_pid()
        procs.register(8790, "u", ["/tmp/repo-a"], pid=dead)
        path = os.path.join(procs.servers_dir(), "%d.json" % dead)
        self.assertTrue(os.path.exists(path))

        self.assertEqual(procs.servers(), [])          # not reported
        self.assertFalse(os.path.exists(path))         # and cleaned up

    def test_a_corrupt_record_is_swept_too(self):
        os.makedirs(procs.servers_dir(), exist_ok=True)
        path = os.path.join(procs.servers_dir(), "123.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(procs.servers(), [])
        self.assertFalse(os.path.exists(path))

    def _dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid


class TestStop(ProcsCase):
    def test_stop_actually_ends_the_process(self):
        p = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(30)"])
        self.addCleanup(p.kill)
        procs.register(8790, "http://x/", ["/tmp/repo-a"], pid=p.pid)

        stopped = procs.stop(procs.serving("/tmp/repo-a"))
        self.assertEqual([r["pid"] for r in stopped], [p.pid])

        for _ in range(50):                    # SIGTERM isn't instant
            if p.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(p.poll(), "process should be gone")
        self.assertEqual(procs.servers(), [])  # record swept with it


if __name__ == "__main__":
    unittest.main()
