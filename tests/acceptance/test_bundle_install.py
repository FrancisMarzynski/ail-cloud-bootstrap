"""Scenarios 5 and 6: hostile or invalid bundles write nothing; installs are atomic with backups.

Seam: `bash cloud/startup.sh <claude|codex> -- <marker>` against a fake endpoint whose
bundle route serves the case under test; HOME is compared file by file before and after.
Limits and path rules: tests/acceptance/CONTRACT.md.
"""

import json
from typing import Any

from tests.acceptance.support import (
    BACKUP_ROOT,
    BUNDLE_LIMIT,
    PROVIDERS,
    ROOTS,
    CloudCase,
    bundle,
    tree,
)

ALPHA = "skills/alpha/SKILL.md"


def entries(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"path": p, "content": c} for p, c in pairs]


def with_files(files: list[Any]) -> dict[str, Any]:
    data = bundle()
    data["files"] = files
    return data


def raw_bundle(files_json: str) -> bytes:
    """A bundle whose files array is given as raw JSON text (for values Python cannot build)."""
    head = json.dumps(bundle())
    return head.replace('"files": [', '"files": [' + files_json + ", ", 1).encode()


# Each case is a whole bundle response body that must be refused.
HOSTILE: dict[str, Any] = {
    "absolute path": with_files(entries((ALPHA, "ok"), ("/skills/evil/SKILL.md", "x"))),
    "dot-dot segment": with_files(entries((ALPHA, "ok"), ("skills/alpha/../../evil/SKILL.md", "x"))),
    "dot-dot escaping skills": with_files(entries((ALPHA, "ok"), ("skills/../evil.md", "x"))),
    "dot segment": with_files(entries((ALPHA, "ok"), ("skills/./alpha/x.md", "x"))),
    "empty segment": with_files(entries((ALPHA, "ok"), ("skills//alpha/x.md", "x"))),
    "backslash": with_files(entries((ALPHA, "ok"), ("skills\\alpha\\x.md", "x"))),
    "outside skills tree": with_files(entries((ALPHA, "ok"), ("rules/AGENTS.md", "x"))),
    "prefix lookalike": with_files(entries((ALPHA, "ok"), ("skillsx/alpha/SKILL.md", "x"))),
    "skills root itself": with_files(entries((ALPHA, "ok"), ("skills", "x"))),
    "trailing slash": with_files(entries((ALPHA, "ok"), ("skills/alpha/", "x"))),
    "empty path": with_files(entries((ALPHA, "ok"), ("", "x"))),
    "duplicate path": with_files(entries((ALPHA, "one"), (ALPHA, "two"))),
    "file and directory clash": with_files(entries((ALPHA, "ok"), ("skills/alpha/SKILL.md/x", "x"))),
    "symlink-shaped entry (type)": with_files(
        [{"path": ALPHA, "content": "ok"}, {"path": "skills/alpha/link", "content": "../../..", "type": "symlink"}]),
    "symlink-shaped entry (mode)": with_files(
        [{"path": ALPHA, "content": "ok"}, {"path": "skills/alpha/link", "content": "/etc", "mode": "120000"}]),
    "entry missing content": with_files([{"path": ALPHA}]),
    "entry not an object": with_files(["skills/alpha/SKILL.md"]),
    "path not a string": with_files([{"path": ["skills", "alpha", "SKILL.md"], "content": "x"}]),
    "binary content object": with_files([{"path": ALPHA, "content": {"base64": "AAEC"}}]),
    "content not text (number)": with_files([{"path": ALPHA, "content": 7}]),
    "content not text (null)": with_files([{"path": ALPHA, "content": None}]),
    "NUL byte in content": with_files(entries((ALPHA, "before\u0000after"))),
    "NUL byte in path": with_files(entries((ALPHA, "ok"), ("skills/alpha/a\u0000b.md", "x"))),
    "lone surrogate (not UTF-8)": raw_bundle('{"path": "skills/alpha/bad.md", "content": "x\\ud800y"}'),
    "malformed JSON": b'{"version": 1, "files": [',
    "not an object": b"[]",
    "empty body": b"",
    "unknown version 2": bundle(version=2),
    "version as string": bundle(version="1"),
    "version missing": {k: v for k, v in bundle().items() if k != "version"},
    "files missing": {k: v for k, v in bundle().items() if k != "files"},
    "rules missing": {k: v for k, v in bundle().items() if k != "rules"},
    "extra top-level key": bundle(extra="x"),
    "files not a list": {**bundle(), "files": {"skills/alpha/SKILL.md": "x"}},
    "rules not text": bundle(rules=["x"]),
    "stale not boolean": bundle(stale="false"),
    "error body with 200": {"version": 1, "error": {"code": "x", "message": "x", "outcome": "not_executed"}},
    "non-finite number": b'{"version": 1, "commit": "x", "fetched_at": NaN, "stale": false, '
                         b'"stale_reason": null, "rules": "r", "files": []}',
}


class HostileBundle(CloudCase):
    def test_hostile_or_invalid_bundle_writes_nothing_and_blocks(self) -> None:
        # Catches any per-entry install before whole-bundle validation, path escapes, link creation,
        # duplicate overwrites, binary/NUL acceptance and lenient parsing of an unknown shape.
        # Misses: filesystem quirks of real cloud hosts (case-insensitive duplicates are not tested).
        for provider in PROVIDERS:
            for name, response in HOSTILE.items():
                with self.subTest(provider=provider, case=name):
                    self.seed_skills()
                    outside = self.tmp / "evil"
                    before = tree(self.home)
                    endpoint = self.endpoint(bundle_response=response)
                    self.blocked(*self.startup(endpoint, provider), bundle_step=True)
                    self.assertEqual(tree(self.home), before, "a refused bundle changed HOME")
                    self.assertFalse(outside.exists(), "a refused bundle wrote outside HOME")
                    self.assertFalse((self.home / "evil").exists())

    def test_oversize_bundle_is_refused_whole_never_truncated(self) -> None:
        # Catches a fetcher that reads only a prefix and installs it, or has no response limit at all.
        # Misses: a limit set lower than the documented one (see the accept test below).
        content = "a" * (BUNDLE_LIMIT + 1)
        body = json.dumps(bundle({ALPHA: content})).encode()
        self.assertGreater(len(body), BUNDLE_LIMIT)
        for mode in ("normal", "no_length"):
            with self.subTest(mode=mode):
                self.seed_skills()
                before = tree(self.home)
                endpoint = self.endpoint(bundle_response=body, bundle_mode=mode)
                self.blocked(*self.startup(endpoint, "claude", timeout=60), bundle_step=True)
                self.assertEqual(tree(self.home), before)

    def test_a_bundle_the_endpoint_could_serve_is_never_refused_for_size(self) -> None:
        # Catches a client limit below the documented 32 MiB: a 5 MiB text file of control characters
        # (valid UTF-8 text) JSON-escapes to about 30 MiB. Misses: the exact boundary byte.
        content = "\u0001" * (5 * 1024 * 1024)
        body = json.dumps(bundle({ALPHA: content}), ensure_ascii=False).encode()
        self.assertGreater(len(body), 30 * 1024 * 1024)
        self.assertLess(len(body), BUNDLE_LIMIT)
        endpoint = self.endpoint(bundle_response=body)
        self.started(*self.startup(endpoint, "claude", timeout=60))
        self.installed("claude", {ALPHA: content})


class AtomicInstall(CloudCase):
    def backups(self) -> list[str]:
        root = self.home / BACKUP_ROOT
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def test_replaced_skill_is_moved_to_a_timestamped_backup_and_unrelated_skills_stay(self) -> None:
        # Catches deleting replaced files, merging old files into the new skill, touching skills the
        # bundle does not name, and a backup without a timestamp. Misses: backup retention policy.
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                self.seed_skills()
                endpoint = self.endpoint()
                self.started(*self.startup(endpoint, provider))
                self.installed(provider)
                stamps = self.backups()
                self.assertTrue(stamps, "replaced skill directories were not backed up")
                for stamp in stamps:
                    self.assertRegex(stamp, r"^\d{8}-\d{6}")
                for root in ROOTS[provider]:
                    live = self.home / root
                    self.assertFalse((live / "alpha/extra.md").exists(), "old files leaked into the new skill")
                    self.assertEqual((live / "unrelated/SKILL.md").read_text(), "keep me\n")
                    saved = [self.home / BACKUP_ROOT / s / root / "alpha" for s in stamps]
                    saved = [d for d in saved if (d / "SKILL.md").is_file()]
                    self.assertEqual(len(saved), 1, f"expected one backup of {root}/alpha")
                    self.assertEqual((saved[0] / "SKILL.md").read_text(), "old alpha\n")
                    self.assertEqual((saved[0] / "extra.md").read_text(), "old extra\n")

    def test_installing_the_same_bundle_again_changes_nothing(self) -> None:
        # Catches a startup that re-backs-up or rewrites identical skills every session.
        # Misses: concurrent sessions installing at once.
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                endpoint = self.endpoint()
                self.started(*self.startup(endpoint, provider))
                first = tree(self.home)
                self.started(*self.startup(endpoint, provider))
                self.assertEqual(tree(self.home), first)

    def test_existing_symlinked_skill_is_replaced_not_written_through(self) -> None:
        # Catches writing bundle files through a pre-existing link into a directory outside the root.
        # Misses: a link planted between validation and the final move (a race).
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                outside = self.tmp / f"outside-{provider}"
                outside.mkdir()
                (outside / "SKILL.md").write_text("outside\n")
                for root in ROOTS[provider]:
                    (self.home / root).mkdir(parents=True, exist_ok=True)
                    (self.home / root / "alpha").symlink_to(outside, target_is_directory=True)
                self.started(*self.startup(self.endpoint(), provider))
                self.assertEqual(tree(outside), {"SKILL.md": ("file", b"outside\n")})
                for root in ROOTS[provider]:
                    self.assertFalse((self.home / root / "alpha").is_symlink())
                self.installed(provider)

    def test_failure_mid_install_leaves_every_root_as_it_was(self) -> None:
        # Catches a partial install when one skill directory (or a whole Codex root) cannot be
        # replaced after another already was, whichever order the installer uses.
        # Misses: power loss or a kill between two renames.
        files = {"skills/aaa/SKILL.md": "new aaa\n", "skills/zzz/SKILL.md": "new zzz\n"}
        cases = [(provider, f"{root}/{name}") for provider in PROVIDERS for root in ROOTS[provider]
                 for name in ("aaa", "zzz")]
        cases += [("codex", root) for root in ROOTS["codex"]]
        for number, (provider, stuck) in enumerate(cases):
            with self.subTest(provider=provider, stuck=stuck):
                self.home = self.tmp / f"home-{number}"
                for root in ROOTS[provider]:
                    for name in ("aaa", "zzz"):
                        (self.home / root / name).mkdir(parents=True)
                        (self.home / root / name / "SKILL.md").write_text(f"old {name}\n")
                self.readonly(self.home / stuck)
                before = tree(self.home)
                endpoint = self.endpoint(bundle_response=bundle(files))
                self.blocked(*self.startup(endpoint, provider), bundle_step=True)
                after = tree(self.home, exclude=(BACKUP_ROOT,))
                self.assertEqual(after, before, "a failed install left a partial skill or staging files")
