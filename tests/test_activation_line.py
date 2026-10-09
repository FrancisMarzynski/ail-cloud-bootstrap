"""Owner tests: the README's activation line never runs code from an unverified checkout.

Seam: the first ```sh block of the README's Activation section, run with bash after its
PIN= and SRC= lines are replaced, against a local bare repository whose cloud/setup.sh only
writes a marker file. Catches: a documented line that checks out a branch, tag, short hash
or unknown hash and runs that code before the pin is verified. Misses: the real host's git
and network (the source here is a local path).
"""

import pathlib
import re
import subprocess

from tests.acceptance.support import REPO, CloudCase, clean_env

MARKER_SETUP = '#!/usr/bin/env bash\nprintf \'%s %s\\n\' "$1" "$2" > "$AIL_TEST_MARKER"\n'


def activation_block() -> str:
    readme = (REPO / "README.md").read_text()
    section = readme.split("## Activation", 1)[1]
    match = re.search(r"```sh\n(.*?)```", section, re.S)
    assert match, "README Activation section has no sh block"
    return match.group(1)


def git(cwd: pathlib.Path, *args: str) -> str:
    env = clean_env(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture", GIT_COMMITTER_NAME="fixture",
                    GIT_COMMITTER_EMAIL="fixture")
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


class ActivationLine(CloudCase):
    def setUp(self) -> None:
        super().setUp()
        work = self.tmp / "work"
        (work / "cloud").mkdir(parents=True)
        (work / "cloud/setup.sh").write_text(MARKER_SETUP)
        git(work, "init", "-q", "-b", "main")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "fixture")
        self.pin = git(work, "rev-parse", "HEAD")
        self.tree = git(work, "rev-parse", "HEAD^{tree}")
        git(work, "tag", "-a", "v1", "-m", "fixture tag")
        self.tag_object = git(work, "rev-parse", "v1")
        self.source = self.tmp / "source.git"
        git(self.tmp, "clone", "-q", "--bare", str(work), str(self.source))
        self.marker = self.scratch / "setup-ran"

    def run_line(self, pin: str) -> subprocess.CompletedProcess[str]:
        lines = activation_block().splitlines()
        self.assertEqual(sum(line.startswith("PIN=") for line in lines), 1)
        self.assertEqual(sum(line.startswith("SRC=") for line in lines), 1)
        script = "\n".join("PIN='" + pin + "'" if line.startswith("PIN=")
                           else "SRC='" + str(self.source) + "'" if line.startswith("SRC=") else line
                           for line in lines)
        self.marker.unlink(missing_ok=True)
        env = clean_env(HOME=str(self.home), TMPDIR=str(self.scratch), AIL_TEST_MARKER=str(self.marker))
        return subprocess.run(["bash", "-c", script], cwd=self.home, env=env, text=True, capture_output=True,
                              timeout=60)

    def test_unpinned_or_mismatched_pin_runs_nothing_from_the_checkout(self) -> None:
        cases = {"branch": "main", "tag": "v1", "short hash": self.pin[:12], "39 hex": self.pin[:39],
                 "41 hex": self.pin + "0", "uppercase": self.pin.upper(), "empty": "",
                 "option-shaped": "--upload-pack=touch", "ref suffix": self.pin + "^{}",
                 "unknown hash": "f" * 40, "tag object hash": self.tag_object, "tree hash": self.tree}
        for name, pin in cases.items():
            with self.subTest(case=name):
                result = self.run_line(pin)
                self.assertNotEqual(result.returncode, 0, f"pin {name!r} was accepted")
                self.assertFalse(self.marker.exists(), f"code from the checkout ran for pin {name!r}")

    def test_full_matching_pin_runs_setup_with_the_source_and_pin(self) -> None:
        result = self.run_line(self.pin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text(), f"{self.source} {self.pin}\n")
