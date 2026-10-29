"""Shared fixtures for the locked acceptance tests. Seam: tests/acceptance/CONTRACT.md.

Everything here is hermetic: a temporary HOME, a fake HTTP endpoint on 127.0.0.1 and,
for setup, a local bare git repository. Nothing reads the real home or a real network.
"""

import http.server
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[2]
CLIENT = REPO / "bin/ail-memory"
STARTUP = REPO / "cloud/startup.sh"
SETUP = REPO / "cloud/setup.sh"
PARTS = ("bin/ail-memory", "hooks/session_index.py", "hooks/cloud_bundle.py")

# Obviously fake values only: this repository is public.
AUTH = "Bearer " + "fake-token-0000"
PAYLOAD_MARKER = "payload-must-not-leak-693"
REQUEST = {"version": 1, "sql": "select $1::jsonb", "params": [{"secret": PAYLOAD_MARKER}]}
INDEX_SQL = "select memory.session_index()"
INDEX = "## Active things\nproject/example: Zażółć gęślą jaźń\n"
SUCCESS = {"version": 1, "columns": ["session_index"], "rows": [[INDEX]]}
RULES = "## Cloud rules\nRule one: read the index first.\nRule two: stop on any memory failure.\n"
FETCHED_AT = "2026-10-09T08:15:30+00:00"
COMMIT = "a" * 40
SKILLS = {
    "skills/alpha/SKILL.md": "---\nname: alpha\n---\nAlpha skill body. Zażółć.\n",
    "skills/alpha/reference/notes.md": "Alpha notes.\n",
    "skills/beta/SKILL.md": "---\nname: beta\n---\nBeta skill body.\n",
}
BUNDLE_LIMIT = 33_554_432
BACKUP_ROOT = ".ail-cloud-bootstrap-backup"
ROOTS = {"claude": (".claude/skills",), "codex": (".codex/skills", ".agents/skills")}
PROVIDERS = ("claude", "codex")


def bundle(files: dict[str, str] | None = None, **overrides: Any) -> dict[str, Any]:
    """A valid v1 bundle; overrides replace top-level keys."""
    data: dict[str, Any] = {
        "version": 1, "commit": COMMIT, "fetched_at": FETCHED_AT, "stale": False,
        "stale_reason": None, "rules": RULES,
        "files": [{"path": p, "content": c} for p, c in (SKILLS if files is None else files).items()],
    }
    data.update(overrides)
    return data


def refusal(code: str, status_reason: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": "refused", "outcome": "not_executed"}
    if status_reason is not None:
        error["reason"] = status_reason
    return {"version": 1, "error": error}


def encode(data: Any) -> bytes:
    return data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()


class Endpoint:
    """Disposable fake memory endpoint: POST is the index route, GET .../bundle the bundle route.

    It records every request in arrival order and imitates transport only, never
    database guarantees.
    """

    def __init__(self, response: Any = SUCCESS, status: int = 200, mode: str = "normal",
                 location: str = "", bundle_response: Any = None, bundle_status: int = 200,
                 bundle_mode: str = "normal") -> None:
        self.response = response
        self.status = status
        self.mode = mode
        self.location = location
        self.bundle_response: Any = bundle() if bundle_response is None else bundle_response
        self.bundle_status = bundle_status
        self.bundle_mode = bundle_mode
        self.calls: list[dict[str, Any]] = []
        lock = threading.Lock()
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_any(self, method: str) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                with lock:
                    fixture.calls.append({"method": method, "body": body, "headers": self.headers,
                                          "path": self.path})
                is_bundle = method == "GET" and self.path.split("?")[0].endswith("/bundle")
                if is_bundle:
                    data, status, mode = fixture.bundle_response, fixture.bundle_status, fixture.bundle_mode
                else:
                    data, status, mode = fixture.response, fixture.status, fixture.mode
                if mode == "drop":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if mode == "delay":
                    time.sleep(2)
                raw = encode(data)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    if fixture.location:
                        self.send_header("Location", fixture.location)
                    if mode != "no_length":
                        extra = 17 if mode == "premature_eof" else 0
                        self.send_header("Content-Length", str(len(raw) + extra))
                    self.end_headers()
                    view = memoryview(raw)
                    for start in range(0, len(raw), 1 << 20):
                        self.wfile.write(view[start:start + (1 << 20)])
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self) -> None:
                self.handle_any("POST")

            def do_GET(self) -> None:
                self.handle_any("GET")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/memory"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def methods(self) -> list[tuple[str, str]]:
        return [(c["method"], c["path"]) for c in self.calls]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def dead_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def clean_env(**extra: str) -> dict[str, str]:
    """Inherited env minus anything that could reach a real endpoint, proxy or git config."""
    stripped = ("AIL_", "GIT_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
                "all_proxy", "NO_PROXY", "no_proxy", "CLAUDE", "CODEX", "PYTHON")
    env = {k: v for k, v in os.environ.items() if not k.startswith(stripped)}
    env.update(TZ="UTC", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1",
               PYTHONUTF8="1")
    env.update(extra)
    return env


def tree(root: pathlib.Path, exclude: tuple[str, ...] = ()) -> dict[str, tuple[str, bytes]]:
    """relative path -> (kind, content or link target) for every file and symlink under root."""
    state: dict[str, tuple[str, bytes]] = {}
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        here = pathlib.Path(dirpath)
        for name in files + [d for d in dirs if (here / d).is_symlink()]:
            path = here / name
            rel = str(path.relative_to(root))
            if any(rel == e or rel.startswith(e + "/") for e in exclude):
                continue
            if path.is_symlink():
                state[rel] = ("link", os.readlink(path).encode())
            else:
                state[rel] = ("file", path.read_bytes())
    return state


def snapshot_repo(dest: pathlib.Path) -> None:
    """Copy this worktree's files (tracked and untracked, not ignored) to dest."""
    out = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=REPO, check=True,
                         capture_output=True, env=clean_env()).stdout.decode()
    for rel in sorted({p for p in out.split("\0") if p}):
        src = REPO / rel
        if not (src.exists() or src.is_symlink()):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target, follow_symlinks=False)


def context_of(stdout: str) -> str:
    output = json.loads(stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert isinstance(context, str)
    return context


class CloudCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.scratch = self.tmp / "scratch"
        self.scratch.mkdir()

    def endpoint(self, *args: Any, **kwargs: Any) -> Endpoint:
        endpoint = Endpoint(*args, **kwargs)
        self.addCleanup(endpoint.close)
        return endpoint

    def env(self, endpoint: Any, provider: str | None = "codex", **extra: str | None) -> dict[str, str]:
        env = clean_env(HOME=str(self.home), TMPDIR=str(self.scratch))
        env.update(AIL_MEMORY_URL=endpoint.url, AIL_MEMORY_AUTHORIZATION=AUTH,
                   AIL_MEMORY_ALLOW_LOOPBACK="1", AIL_MEMORY_TIMEOUT_SECONDS="3", NO_PROXY="127.0.0.1",
                   no_proxy="127.0.0.1")
        env.update({"CLAUDE_CODE_REMOTE": "true"} if provider == "claude" else {"CODEX_CLOUD": "1"})
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def readonly(self, path: pathlib.Path) -> None:
        """Make a directory read-only for one test and always restore it for cleanup."""
        path.chmod(0o555)
        self.addCleanup(path.chmod, 0o755)

    # --- client --------------------------------------------------------------
    def client(self, endpoint: Any, request: Any = REQUEST, raw: str | None = None,
               **extra: str | None) -> subprocess.CompletedProcess[str]:
        self.assertTrue(CLIENT.is_file(), "missing bin/ail-memory public client")
        self.assertTrue(os.access(CLIENT, os.X_OK), "bin/ail-memory must be executable")
        return subprocess.run([str(CLIENT)], input=raw if raw is not None else json.dumps(request),
                              text=True, capture_output=True, cwd=self.home,
                              env=self.env(endpoint, **extra), timeout=8)

    def failure(self, result: subprocess.CompletedProcess[str], outcome: str) -> dict[str, Any]:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "", "failure must not look like a confirmed result")
        self.assertLessEqual(len(result.stderr.encode()), 4096)
        error = json.loads(result.stderr)
        self.assertEqual(error["version"], 1)
        self.assertEqual(error["error"]["outcome"], outcome)
        self.assertTrue(error["error"]["code"])
        self.assertTrue(error["error"]["message"])
        for value in (AUTH, PAYLOAD_MARKER, REQUEST["sql"], "Traceback", "/memory", "127.0.0.1"):
            self.assertNotIn(value, result.stderr)
        return error

    # --- startup -------------------------------------------------------------
    def startup(self, endpoint: Any, provider: str = "codex", repo: pathlib.Path = REPO,
                timeout: float = 30, **extra: str | None) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
        adapter = repo / "cloud/startup.sh"
        self.assertTrue(adapter.is_file(), "missing cloud/startup.sh")
        marker = self.scratch / "continued"
        marker.unlink(missing_ok=True)
        result = subprocess.run(["bash", str(adapter), provider, "--", sys.executable, "-c",
                                 "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('continued')",
                                 str(marker)],
                                input="{}", text=True, capture_output=True, cwd=self.home,
                                env=self.env(endpoint, provider, **extra), timeout=timeout)
        return result, marker

    def started(self, result: subprocess.CompletedProcess[str], marker: pathlib.Path) -> str:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(marker.exists(), "confirmed startup did not run the continuation")
        context = context_of(result.stdout)
        self.assertIn(RULES, context, "cloud rules must reach the agent whole")
        return context

    def blocked(self, result: subprocess.CompletedProcess[str], marker: pathlib.Path,
                bundle_step: bool = False) -> None:
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertFalse(marker.exists(), "a failed startup ran the follow-on continuation")
        self.assertRegex(result.stderr.lower(), r"block|stop")
        self.assertLessEqual(len(result.stderr.encode()), 4096)
        for secret in (AUTH, PAYLOAD_MARKER, "127.0.0.1", "/memory"):
            self.assertNotIn(secret, result.stderr)
        if bundle_step:
            self.assertIn("bundle", result.stderr.lower(), "a bundle block must name the bundle as the reason")

    def installed(self, provider: str, files: dict[str, str] | None = None) -> None:
        for root in ROOTS[provider]:
            for path, content in (SKILLS if files is None else files).items():
                target = self.home / root / path.removeprefix("skills/")
                self.assertTrue(target.is_file(), f"{target.relative_to(self.home)} not installed")
                self.assertEqual(target.read_bytes(), content.encode(), f"{target} content differs")

    def seed_skills(self) -> None:
        """An unrelated skill and an older copy of a bundled skill in every root."""
        for roots in ROOTS.values():
            for root in roots:
                (self.home / root / "unrelated").mkdir(parents=True, exist_ok=True)
                (self.home / root / "unrelated/SKILL.md").write_text("keep me\n")
                (self.home / root / "alpha").mkdir(parents=True, exist_ok=True)
                (self.home / root / "alpha/SKILL.md").write_text("old alpha\n")
                (self.home / root / "alpha/extra.md").write_text("old extra\n")
