"""Scenarios 3, 4, 8 and 10: session start order, blocking, stale notice and fail-closed parts.

Seam: `bash cloud/startup.sh <claude|codex> -- <marker command>` in a temporary HOME
against a fake endpoint. The marker is the harmless follow-on work: it must run only
after a confirmed index and a confirmed bundle install. Every startup test runs for
both adapters (Claude, and Codex, which is tested here but unproven on a real host).
"""

import json
import subprocess
import sys
import time
from typing import Any

from tests.acceptance.support import (
    AUTH,
    FETCHED_AT,
    INDEX,
    INDEX_SQL,
    PARTS,
    PROVIDERS,
    REPO,
    RULES,
    STARTUP,
    SUCCESS,
    CloudCase,
    bundle,
    dead_url,
    refusal,
    snapshot_repo,
    tree,
)


class StartupOrder(CloudCase):
    def test_index_then_bundle_then_skills_and_rules_for_each_adapter(self) -> None:
        # Catches a wrong order (bundle before index), a wrong bundle route, missing Authorization on the
        # bundle request, skills not landing in the provider's directories, rules not reaching the agent,
        # and truncation of a long index. Misses: whether a managed host really runs the hook.
        long_index = INDEX + "x" * 12000
        for provider in PROVIDERS:
            for text in (INDEX, long_index):
                with self.subTest(provider=provider, length=len(text)):
                    endpoint = self.endpoint({"version": 1, "columns": ["session_index"], "rows": [[text]]})
                    context = self.started(*self.startup(endpoint, provider))
                    self.assertIn(text, context, "the index must reach the agent whole")
                    self.assertEqual(endpoint.methods(), [("POST", "/memory"), ("GET", "/memory/bundle")])
                    request = json.loads(endpoint.calls[0]["body"])
                    self.assertEqual(request["sql"].rstrip("; "), INDEX_SQL)
                    self.assertEqual(request["params"], [])
                    for call in endpoint.calls:
                        self.assertEqual(call["headers"].get("Authorization"), AUTH)
                    self.installed(provider)
                    self.assertNotIn("stale", context.lower(), "a fresh bundle must not carry a stale notice")

    def test_claude_without_runtime_authorization_relies_on_the_proxy_for_both_requests(self) -> None:
        # Catches a fetcher that refuses or invents a credential when the Claude proxy injects it.
        # Misses: real proxy injection on the bundle route (genuine-session evidence).
        endpoint = self.endpoint()
        self.started(*self.startup(endpoint, "claude", AIL_MEMORY_AUTHORIZATION=None))
        self.assertEqual(len(endpoint.calls), 2)
        for call in endpoint.calls:
            self.assertIsNone(call["headers"].get("Authorization"))

    def test_codex_without_authorization_sends_nothing_and_blocks(self) -> None:
        # Catches an unauthenticated request outside the Claude proxy path. Misses: other providers.
        endpoint = self.endpoint()
        self.blocked(*self.startup(endpoint, "codex", AIL_MEMORY_AUTHORIZATION=None))
        self.assertEqual(endpoint.calls, [])


class IndexFailureStopsEverything(CloudCase):
    def test_bad_or_absent_index_blocks_and_the_bundle_is_never_requested(self) -> None:
        # Catches a startup that continues, or still fetches/installs the bundle, after the index fails.
        # Misses: index contents the server might send that are well-formed but wrong.
        cases: list[tuple[Any, int, str]] = [
            (b"", 200, "normal"), (b"not json", 200, "normal"),
            ({"version": 1, "columns": ["session_index"], "rows": []}, 200, "normal"),
            ({"version": 1, "columns": ["session_index"], "rows": [[""]]}, 200, "normal"),
            ({"version": 1, "columns": ["session_index"], "rows": [[" \n"]]}, 200, "normal"),
            ({"version": 1, "columns": ["session_index"], "rows": [[None]]}, 200, "normal"),
            ({"version": 1, "columns": ["session_index"], "rows": [[{}]]}, 200, "normal"),
            ({"version": 1, "columns": ["x", "y"], "rows": [[INDEX, INDEX]]}, 200, "normal"),
            ({"version": 1, "columns": ["session_index"], "rows": [[INDEX], [INDEX]]}, 200, "normal"),
            (refusal("authentication"), 401, "normal"),
            (refusal("outage"), 503, "normal"),
            (SUCCESS, 200, "delay"),
            (SUCCESS, 200, "drop"),
        ]
        for provider in PROVIDERS:
            for response, status, mode in cases:
                with self.subTest(provider=provider, status=status, mode=mode, response=str(response)[:60]):
                    endpoint = self.endpoint(response, status=status, mode=mode)
                    start = time.monotonic()
                    self.blocked(*self.startup(endpoint, provider, AIL_MEMORY_TIMEOUT_SECONDS="1"))
                    self.assertLess(time.monotonic() - start, 10, "startup must be bounded by the timeout")
                    self.assertEqual([m for m, _ in endpoint.methods()], ["POST"],
                                     "after an index failure nothing else may be requested")
                    self.assertEqual(tree(self.home), {}, "a blocked startup installed something")

    def test_unreachable_endpoint_blocks(self) -> None:
        # Catches treating a connection failure as success. Misses: proxy failures.
        endpoint = self.endpoint()
        closed = type("Closed", (), {"url": dead_url() + "/memory"})()
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                self.blocked(*self.startup(closed, provider, AIL_MEMORY_TIMEOUT_SECONDS="1"))
        self.assertEqual(endpoint.calls, [])


class BundleFailureBlocks(CloudCase):
    def test_endpoint_bundle_refusals_block_with_the_bundle_as_reason(self) -> None:
        # Catches continuing without the skills and rules when the bundle route refuses, times out,
        # redirects or reports that no good bundle was ever stored. Misses: real proxy errors.
        cases: list[tuple[Any, int, str]] = [
            (refusal("authentication"), 401, "normal"),
            (refusal("bundle_unavailable", "unreachable"), 503, "normal"),
            (refusal("bundle_unavailable", "auth"), 503, "normal"),
            (refusal("configuration"), 503, "normal"),
            (refusal("database_unavailable"), 503, "normal"),
            (bundle(), 200, "delay"),
            (bundle(), 200, "drop"),
            (bundle(), 200, "premature_eof"),
        ]
        for provider in PROVIDERS:
            for response, status, mode in cases:
                with self.subTest(provider=provider, status=status, mode=mode):
                    self.seed_skills()
                    before = tree(self.home)
                    endpoint = self.endpoint(bundle_response=response, bundle_status=status, bundle_mode=mode)
                    start = time.monotonic()
                    self.blocked(*self.startup(endpoint, provider, AIL_MEMORY_TIMEOUT_SECONDS="1"),
                                 bundle_step=True)
                    self.assertLess(time.monotonic() - start, 10)
                    self.assertEqual(endpoint.methods(), [("POST", "/memory"), ("GET", "/memory/bundle")],
                                     "the bundle must be requested exactly once, with no retry")
                    self.assertEqual(tree(self.home), before, "a refused bundle changed HOME")

    def test_bundle_redirect_is_refused_and_never_followed(self) -> None:
        # Catches following a redirect from the bundle route (forwarding the credential elsewhere).
        # Misses: redirects on the index route (covered by the client contract tests).
        target = self.endpoint()
        for provider in PROVIDERS:
            for status in (301, 302, 307, 308):
                with self.subTest(provider=provider, status=status):
                    source = self.endpoint(bundle_response={}, bundle_status=status)
                    source.location = target.url + "/bundle"
                    self.blocked(*self.startup(source, provider), bundle_step=True)
                    self.assertEqual(tree(self.home), {})
        self.assertEqual(target.calls, [])


class StaleAndNeverFetched(CloudCase):
    def test_stale_bundle_continues_with_a_notice_and_the_last_good_fetch_time(self) -> None:
        # Catches blocking on a stale bundle, hiding the notice, or dropping the fetch time.
        # Misses: how prominently a real agent shows the notice.
        for provider in PROVIDERS:
            for reason in ("unreachable", "auth", "rate_limited", "malformed"):
                with self.subTest(provider=provider, reason=reason):
                    endpoint = self.endpoint(bundle_response=bundle(stale=True, stale_reason=reason))
                    context = self.started(*self.startup(endpoint, provider))
                    self.assertIn("stale", context.lower())
                    self.assertIn(FETCHED_AT, context)
                    self.installed(provider)

    def test_never_fetched_bundle_blocks_and_installs_nothing(self) -> None:
        # Catches continuing, or installing anything, when the endpoint has no good bundle at all.
        # Misses: what the endpoint does to decide that (endpoint repo).
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                endpoint = self.endpoint(bundle_response=refusal("bundle_unavailable", "unreachable"),
                                         bundle_status=503)
                self.blocked(*self.startup(endpoint, provider), bundle_step=True)
                self.assertEqual(tree(self.home), {})


class MissingOrBrokenParts(CloudCase):
    def test_missing_crashing_or_silent_part_blocks_and_follow_on_never_runs(self) -> None:
        # Catches startup that tolerates a missing client, index hook or bundle fetcher, a crash in
        # one, or one that exits 0 without confirming anything. Misses: a part that lies with a
        # well-formed but false confirmation.
        for part in PARTS:
            self.assertTrue((REPO / part).exists(), f"missing {part}")
        for part in PARTS:
            for mode in ("missing", "crashing", "silent"):
                copy = self.tmp / f"copy-{part.replace('/', '-')}-{mode}"
                snapshot_repo(copy)
                target = copy / part
                target.unlink(missing_ok=True)
                python = part.endswith(".py")
                if mode == "crashing":
                    target.write_text("raise SystemExit('unexpected child crash')\n" if python
                                      else "#!/bin/sh\necho 'unexpected child crash' >&2\nexit 77\n")
                elif mode == "silent":
                    target.write_text("pass\n" if python else "#!/bin/sh\nexit 0\n")
                if mode != "missing":
                    target.chmod(0o755)
                for provider in PROVIDERS:
                    with self.subTest(part=part, mode=mode, provider=provider):
                        endpoint = self.endpoint()
                        self.blocked(*self.startup(endpoint, provider, repo=copy))
                        self.assertEqual(tree(self.home), {}, "a broken part left something installed")

    def test_startup_rejects_an_unknown_provider_or_malformed_continuation(self) -> None:
        # Catches an adapter that guesses the provider or runs an arbitrary continuation form.
        # Misses: provider detection from the environment alone.
        self.assertTrue(STARTUP.is_file(), "missing cloud/startup.sh")
        endpoint = self.endpoint()
        marker = self.scratch / "continued"
        writer = [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('x')",
                  str(marker)]
        for argv in (["other", "--", *writer], [], ["claude", *writer]):
            with self.subTest(argv=argv[:2]):
                result = subprocess.run(["bash", str(STARTUP), *argv], input="{}", text=True,
                                        capture_output=True, cwd=self.home, env=self.env(endpoint, "claude"),
                                        timeout=30)
                self.blocked(result, marker)
        self.assertEqual(endpoint.calls, [])


class Recovery(CloudCase):
    def test_runtime_failure_blocks_follow_on_until_an_explicit_successful_index_check(self) -> None:
        # Catches a block that persists after recovery, or a recovery that skips the index check.
        # Misses: an agent's own decision to resume (genuine-session evidence).
        endpoint = self.endpoint()
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                self.started(*self.startup(endpoint, provider))
                endpoint.response = {"version": 1, "error": {"code": "outage", "message": "unavailable",
                                                            "outcome": "not_executed"}}
                endpoint.status = 503
                self.blocked(*self.startup(endpoint, provider))
                endpoint.response, endpoint.status = SUCCESS, 200
                self.started(*self.startup(endpoint, provider))

    def test_rules_text_reaches_both_adapters_unchanged(self) -> None:
        # Catches rewriting, escaping or truncating the cloud rules. Misses: how the agent uses them.
        rules = RULES + "Unicode: Zażółć gęślą jaźń 🐈\n" + "r" * 20000 + "\n"
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                endpoint = self.endpoint(bundle_response=bundle(rules=rules))
                result, marker = self.startup(endpoint, provider)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(marker.exists())
                self.assertIn(rules, json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
