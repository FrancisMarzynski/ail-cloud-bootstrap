"""Owner tests: cloud/setup.sh switches the checkout atomically when the pin moves.

Seam: `bash cloud/setup.sh <bare repo> <pin>` in a temporary HOME. Faults are injected
into setup's Python apply step through a `sitecustomize` module on PYTHONPATH, which
patches the standard library only when AIL_TEST_FAULT names a fault. Catches: a copy that
fails part-way (as across filesystems, on a full disk) or a failing final rename leaving
no checkout where the registered hooks point, and a landing directory left behind or
reused from a crashed run. Misses: a kill between the backup move and the final rename.
"""

import os
import subprocess

from tests.acceptance.support import REPO, CloudCase, clean_env
from tests.acceptance.test_setup import Source

SITECUSTOMIZE = '''
import errno, os, pathlib, shutil

fault = os.environ.get("AIL_TEST_FAULT")
home = pathlib.Path(os.environ["HOME"])
prefix = ".ail-cloud-bootstrap-incoming-"

if fault == "copy":
    def broken_move(src, dst, *args, **kwargs):
        # A cross-filesystem move is a copy: leave part of it behind, then fail.
        pathlib.Path(dst).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(dst) / "partial").write_text("half")
        raise OSError(errno.ENOSPC, "No space left on device")
    shutil.move = broken_move

if fault == "rename":
    real_rename = os.rename
    def broken_rename(src, dst, *args, **kwargs):
        source = pathlib.Path(src)
        from_landing = source.name.startswith(prefix) or source.parent.name.startswith(prefix)
        if pathlib.Path(dst) == home / "ail-cloud-bootstrap" and from_landing:
            raise OSError(errno.EIO, "Input/output error")
        return real_rename(src, dst, *args, **kwargs)
    os.rename = broken_rename
'''


class CheckoutSwitch(CloudCase):
    def setUp(self) -> None:
        super().setUp()
        self.source = Source(self.tmp)
        self.inject = self.tmp / "inject"
        self.inject.mkdir()
        (self.inject / "sitecustomize.py").write_text(SITECUSTOMIZE)

    def setup(self, pin: str, fault: str = "") -> subprocess.CompletedProcess[str]:
        env = clean_env(HOME=str(self.home), TMPDIR=str(self.scratch), PYTHONPATH=str(self.inject),
                        AIL_TEST_FAULT=fault)
        return subprocess.run(["bash", "cloud/setup.sh", str(self.source.path), pin], cwd=REPO,
                              env=env, text=True, capture_output=True, timeout=120)

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.home / "ail-cloud-bootstrap"), "rev-parse", "HEAD"],
                              capture_output=True, text=True, env=clean_env(), check=True).stdout.strip()

    def landings(self) -> list[str]:
        return sorted(p for p in os.listdir(self.home) if p.startswith(".ail-cloud-bootstrap-incoming-"))

    def test_failed_copy_keeps_the_old_checkout_and_leaves_no_landing_directory(self) -> None:
        first = self.setup(self.source.pin)
        self.assertEqual(first.returncode, 0, first.stderr)
        result = self.setup(self.source.second, fault="copy")
        self.assertNotEqual(result.returncode, 0, "a failed copy must fail setup")
        self.assertTrue((self.home / "ail-cloud-bootstrap/cloud/startup.sh").is_file(),
                        "the registered hooks point at a checkout that is gone")
        self.assertEqual(self.head(), self.source.pin)
        self.assertEqual(self.landings(), [])

    def test_failed_final_rename_restores_the_old_checkout(self) -> None:
        first = self.setup(self.source.pin)
        self.assertEqual(first.returncode, 0, first.stderr)
        result = self.setup(self.source.second, fault="rename")
        self.assertNotEqual(result.returncode, 0, "a failed rename must fail setup")
        self.assertTrue((self.home / "ail-cloud-bootstrap/cloud/startup.sh").is_file())
        self.assertEqual(self.head(), self.source.pin)
        self.assertEqual(self.landings(), [])

    def test_a_landing_directory_left_by_a_crashed_run_is_never_reused_or_removed(self) -> None:
        stale = self.home / f".ail-cloud-bootstrap-incoming-{os.getpid()}"
        stale.mkdir()
        (stale / "left-over").write_text("from a crashed run\n")
        for pin in (self.source.pin, self.source.second):
            result = self.setup(pin)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.head(), pin)
        self.assertFalse((self.home / "ail-cloud-bootstrap/ail-cloud-bootstrap").exists(), "checkout was nested")
        self.assertEqual(self.landings(), [stale.name])
        self.assertEqual((stale / "left-over").read_text(), "from a crashed run\n")
