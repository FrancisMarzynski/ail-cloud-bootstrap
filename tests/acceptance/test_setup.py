"""Scenarios 1 and 2: setup without credentials from a pinned commit; the pin is enforced.

Seam: `bash cloud/setup.sh <source> <pin>` with a local bare repository (a commit of this
worktree) standing in for this public repo, an empty temporary HOME, proxies pointed at a
closed port, and a fake memory endpoint that must never be called during setup.
"""

import json
import pathlib
import subprocess
import tomllib
from typing import Any

from tests.acceptance.support import (
    BACKUP_ROOT,
    INDEX,
    REPO,
    SETUP,
    CloudCase,
    clean_env,
    context_of,
    dead_url,
    snapshot_repo,
    tree,
)

CHECKOUT = "ail-cloud-bootstrap"
CHECKOUT_GIT = CHECKOUT + "/.git"
CLAUDE_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other-pre-tool-hook"}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": "echo other-session-hook", "timeout": 60}]}],
    },
}
CODEX_CONFIG = """model = "fixture-model"
approval_policy = "on-request"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "echo other-stop-hook"

[[hooks.SessionStart]]

[[hooks.SessionStart.hooks]]
type = "command"
command = "echo other-session-hook"
timeout = 70
"""


def git(cwd: pathlib.Path, *args: str) -> str:
    env = clean_env(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture", GIT_COMMITTER_NAME="fixture",
                    GIT_COMMITTER_EMAIL="fixture", GIT_AUTHOR_DATE="2026-10-09T00:00:00Z",
                    GIT_COMMITTER_DATE="2026-10-09T00:00:00Z")
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def commands(groups: list[dict[str, Any]]) -> list[str]:
    return [h.get("command", "") for g in groups for h in g.get("hooks", [])]


class Source:
    """A local bare repository holding this worktree at commit `pin`, plus a later commit, a tag and a branch."""

    def __init__(self, tmp: pathlib.Path) -> None:
        work = tmp / "source-work"
        snapshot_repo(work)
        git(work, "init", "-q", "-b", "main")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "fixture")
        self.pin = git(work, "rev-parse", "HEAD")
        self.tree = git(work, "rev-parse", "HEAD^{tree}")
        git(work, "tag", "-a", "v1", "-m", "fixture tag")
        self.tag_object = git(work, "rev-parse", "v1")
        (work / "fixture-second-commit.txt").write_text("second\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "second")
        self.second = git(work, "rev-parse", "HEAD")
        git(work, "branch", "feature", self.pin)
        self.path = tmp / "source.git"
        git(tmp, "clone", "-q", "--bare", str(work), str(self.path))


class SetupCase(CloudCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertTrue(SETUP.is_file(), "missing cloud/setup.sh")
        self.source = Source(self.tmp)
        self.memory = self.endpoint()

    def setup_env(self) -> dict[str, str]:
        env = self.env(self.memory)
        closed = dead_url()
        env.update(HTTP_PROXY=closed, HTTPS_PROXY=closed, ALL_PROXY=closed, http_proxy=closed,
                   https_proxy=closed, all_proxy=closed, NO_PROXY="", no_proxy="")
        return env

    def setup(self, pin: str, source: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(SETUP), source or str(self.source.path), pin], cwd=self.home,
                              env=self.setup_env(), text=True, capture_output=True, timeout=180)

    def ok(self, pin: str) -> None:
        result = self.setup(pin)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def registered(self) -> dict[str, str]:
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        config = tomllib.loads((self.home / ".codex/config.toml").read_text())
        found: dict[str, str] = {}
        for provider, groups in (("claude", settings["hooks"]["SessionStart"]),
                                 ("codex", config["hooks"]["SessionStart"])):
            ours = [c for c in commands(groups) if "cloud/startup.sh" in c]
            self.assertEqual(len(ours), 1, f"{provider}: expected exactly one startup registration")
            self.assertIn(provider, ours[0])
            self.assertNotIn("|| true", ours[0], "the startup hook must not swallow failure")
            self.assertNotIn(str(REPO), ours[0], "the hook must run the pinned checkout, not the source")
            found[provider] = ours[0]
        return found


class SetupWithoutCredentials(SetupCase):
    def test_empty_home_setup_installs_client_and_hooks_with_no_request_and_is_idempotent(self) -> None:
        # Catches setup that needs credentials or the endpoint, installs a different commit than the pin,
        # wires hooks that do not run, or changes anything on a second run.
        # Misses: whether the provider's setup phase really lacks credentials (genuine session).
        self.ok(self.source.pin)
        first = tree(self.home, exclude=(CHECKOUT_GIT,))
        self.assertEqual(git(self.home / CHECKOUT, "rev-parse", "HEAD"), self.source.pin)
        client = self.home / ".local/bin/ail-memory"
        self.assertTrue(client.exists(), "client not installed on the user's bin path")
        self.registered()
        self.ok(self.source.pin)
        self.assertEqual(tree(self.home, exclude=(CHECKOUT_GIT,)), first, "a second setup run changed HOME")
        self.assertEqual(self.memory.calls, [], "setup contacted the memory endpoint")

    def test_registered_hooks_and_installed_client_work_end_to_end(self) -> None:
        # Catches registrations that point at a missing path, a client link that does not run,
        # and an installed startup that skips the index or the bundle. Misses: a real host's hook runner.
        self.ok(self.source.pin)
        client = self.home / ".local/bin/ail-memory"
        request = json.dumps({"version": 1, "sql": "select memory.session_index()", "params": []})
        result = subprocess.run([str(client)], input=request, text=True, capture_output=True,
                                env=self.env(self.memory), cwd=self.home, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["rows"], [[INDEX]])
        for provider, command in self.registered().items():
            with self.subTest(provider=provider):
                endpoint = self.endpoint()
                result = subprocess.run(["bash", "-c", command], input="{}", text=True, capture_output=True,
                                        env=self.env(endpoint, provider), cwd=self.home, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(INDEX, context_of(result.stdout))
                self.assertEqual([m for m, _ in endpoint.methods()], ["POST", "GET"])
                self.installed(provider)

    def test_existing_settings_are_preserved_and_backed_up_before_change(self) -> None:
        # Catches setup that drops unrelated settings or hooks, duplicates its own hook, or overwrites a
        # settings file without a timestamped backup. Misses: settings files that are not valid JSON/TOML.
        settings = self.home / ".claude/settings.json"
        config = self.home / ".codex/config.toml"
        settings.parent.mkdir(parents=True)
        config.parent.mkdir(parents=True)
        settings.write_text(json.dumps(CLAUDE_SETTINGS, indent=2))
        config.write_text(CODEX_CONFIG)
        original = {settings: settings.read_bytes(), config: config.read_bytes()}
        self.ok(self.source.pin)
        self.ok(self.source.pin)
        new_settings = json.loads(settings.read_text())
        new_config = tomllib.loads(config.read_text())
        self.assertEqual(new_settings["model"], "opus")
        self.assertEqual(new_settings["permissions"], CLAUDE_SETTINGS["permissions"])
        self.assertEqual(new_settings["hooks"]["PreToolUse"], CLAUDE_SETTINGS["hooks"]["PreToolUse"])
        self.assertEqual(commands(new_settings["hooks"]["SessionStart"]).count("echo other-session-hook"), 1)
        self.assertEqual(new_config["model"], "fixture-model")
        self.assertEqual(commands(new_config["hooks"]["Stop"]), ["echo other-stop-hook"])
        self.assertEqual(commands(new_config["hooks"]["SessionStart"]).count("echo other-session-hook"), 1)
        self.registered()
        backups = self.home / BACKUP_ROOT
        self.assertTrue(backups.is_dir(), "changed settings were not backed up")
        for path, content in original.items():
            rel = path.relative_to(self.home)
            saved = [d / rel for d in backups.iterdir() if (d / rel).is_file()]
            self.assertTrue(saved, f"no backup of {rel}")
            self.assertEqual(saved[0].read_bytes(), content)
            self.assertRegex(saved[0].relative_to(backups).parts[0], r"^\d{8}-\d{6}")
        self.assertEqual(self.memory.calls, [])

    def test_moving_the_pin_installs_the_new_commit(self) -> None:
        # Catches setup that keeps an old checkout when the pin changes. Misses: rollback ordering in docs.
        self.ok(self.source.pin)
        self.ok(self.source.second)
        self.assertEqual(git(self.home / CHECKOUT, "rev-parse", "HEAD"), self.source.second)
        self.registered()


class PinEnforced(SetupCase):
    def refused(self, pin: str) -> None:
        result = self.setup(pin)
        self.assertNotEqual(result.returncode, 0, f"pin {pin!r} was accepted")
        self.assertIn("pin", (result.stdout + result.stderr).lower(), "the refusal must name the pin")

    def bad_pins(self) -> dict[str, str]:
        source = self.source
        return {
            "short sha 7": source.pin[:7],
            "short sha 12": source.pin[:12],
            "39 hex": source.pin[:39],
            "41 hex": source.pin + "0",
            "branch": "main",
            "other branch": "feature",
            "tag": "v1",
            "HEAD": "HEAD",
            "empty": "",
            "option-shaped": "--upload-pack=touch",
            "sha with ref suffix": source.pin + "^{}",
            "uppercase sha": source.pin.upper(),
            "tag object sha": source.tag_object,
            "tree sha": source.tree,
            "unknown sha": "f" * 40,
        }

    def test_unpinned_or_mismatched_reference_is_refused_and_installs_nothing(self) -> None:
        # Catches accepting a moving reference (branch, tag, short hash) or a hash that is not the checked-out
        # commit (tag object, tree, unknown), and any partial install on refusal.
        # Misses: a compromised account publishing a malicious commit at a new pin (manual review).
        for name, pin in self.bad_pins().items():
            with self.subTest(case=name):
                before = tree(self.home)
                self.refused(pin)
                self.assertEqual(tree(self.home), before, "a refused pin changed HOME")
        self.assertEqual(self.memory.calls, [])

    def test_refused_pin_keeps_a_previous_good_install_intact(self) -> None:
        # Catches a refused re-run that half-replaces a working install. Misses: disk-full failures.
        self.ok(self.source.pin)
        before = tree(self.home, exclude=(CHECKOUT_GIT,))
        for name, pin in self.bad_pins().items():
            with self.subTest(case=name):
                self.refused(pin)
                self.assertEqual(tree(self.home, exclude=(CHECKOUT_GIT,)), before)
                self.assertEqual(git(self.home / CHECKOUT, "rev-parse", "HEAD"), self.source.pin)

    def test_missing_arguments_are_refused(self) -> None:
        # Catches a silent default to an unpinned branch when no pin is given. Misses: nothing else.
        for argv in ([], [str(self.source.path)]):
            with self.subTest(argc=len(argv)):
                result = subprocess.run(["bash", str(SETUP), *argv], cwd=self.home, env=self.setup_env(),
                                        text=True, capture_output=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(tree(self.home), {})
