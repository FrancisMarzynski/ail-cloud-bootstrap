"""Cloud session start, step 2: fetch the skills and rules bundle, install it atomically.

Usage: python3 hooks/cloud_bundle.py <claude|codex>, with step 1's SessionStart hook
object (the memory index) on stdin. On success it prints one SessionStart hook object
whose context is the index, the cloud rules and, for a stale bundle, a notice.

Fail closed: any transport failure, endpoint refusal, malformed, unknown, oversize or
hostile bundle exits non-zero with one `ail-cloud:` reason line on stderr. Reasons are
fixed words and entry numbers only, never server text, so nothing can leak through them.
The whole bundle is validated before anything is written. Replaced skill directories
are moved to a timestamped backup, never deleted; a failure part-way through moves every
change back.

Choices the contract leaves open (also in the README):
- a file directly under skills/ (not inside a skill directory) refuses the bundle;
- an empty files list or empty rules refuses the bundle (a source that lost its skills or
  rules must not silently uninstall nothing and continue without rules);
- paths that differ only by case or Unicode normalisation count as duplicates, because
  common filesystems would collapse them into one file;
- a Claude session fills only ~/.claude/skills, never the Codex directories;
- a skill root (or a directory above it inside HOME) that is a symlink refuses the install,
  so a bundle is never written through a link to somewhere else.
"""

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import time
import unicodedata
from typing import Any, NoReturn

BUNDLE_LIMIT = 33_554_432  # 32 MiB: 8x the endpoint's 4 MiB source limit (JSON escaping can expand text 6x)
INDEX_INPUT_LIMIT = 4 * 1048576
KEYS = {"version", "commit", "fetched_at", "stale", "stale_reason", "rules", "files"}
ROOTS = {"claude": (".claude/skills",), "codex": (".codex/skills", ".agents/skills")}
BACKUP_ROOT = ".ail-cloud-bootstrap-backup"
STALE_REASONS = ("unreachable", "auth", "rate_limited", "malformed")
ENDPOINT_CODES = ("authentication", "bundle_unavailable", "configuration", "database_unavailable")
COMMIT = re.compile(r"[0-9a-f]{40}")


class Refused(Exception):
    """The bundle cannot be installed; the message is a fixed, safe reason."""


def refuse(reason: str) -> NoReturn:
    raise Refused(reason)


# --- input -------------------------------------------------------------------

def index_context(raw: bytes) -> str:
    if len(raw) > INDEX_INPUT_LIMIT:
        refuse("memory index input too large")
    try:
        data = json.loads(raw.decode("utf-8"))
        context = data["hookSpecificOutput"]["additionalContext"]
    except (ValueError, TypeError, KeyError):
        refuse("no confirmed memory index on input")
    if not isinstance(context, str) or not context.strip():
        refuse("no confirmed memory index on input")
    return context


# --- transport -----------------------------------------------------------------

def load_client() -> Any:
    """The memory client module: the bundle request follows exactly its transport rules."""
    path = pathlib.Path(__file__).resolve().parents[1] / "bin/ail-memory"
    sys.dont_write_bytecode = True  # never leave bytecode in the pinned checkout
    try:
        loader = importlib.machinery.SourceFileLoader("ail_memory_client", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            refuse("memory client unavailable")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        if not (callable(module.prepare) and callable(module.exchange)):
            refuse("memory client unavailable")
    except (OSError, SyntaxError, ImportError, AttributeError):
        refuse("memory client unavailable")
    return module


def refusal_detail(status: int, raw: bytes) -> str:
    """HTTP status plus the endpoint's error code and reason, only when they are known words."""
    detail = f"HTTP {status}"
    try:
        error = json.loads(raw.decode("utf-8"))["error"]
        code, reason = error.get("code"), error.get("reason")
    except (ValueError, TypeError, KeyError, AttributeError):
        return detail
    if code in ENDPOINT_CODES:
        detail += f", {code}"
        if reason in STALE_REASONS:
            detail += f" ({reason})"
    return detail


def fetch(client: Any) -> bytes:
    try:
        request, timeout = client.prepare("GET", "/bundle", headers={"Accept": "application/json"})
    except (ValueError, TypeError, UnicodeError):
        refuse("memory endpoint configuration invalid")
    try:
        status, raw = client.exchange(request, timeout, BUNDLE_LIMIT)
    except client.TRANSPORT_ERRORS:
        refuse("bundle request failed, timed out, was cut short or is over the 32 MiB limit")
    if len(raw) > BUNDLE_LIMIT:
        refuse("bundle is over the 32 MiB limit")
    if status != 200:
        refuse(f"endpoint did not serve a bundle ({refusal_detail(status, raw)})")
    return raw


# --- validation ----------------------------------------------------------------

def reject_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite number")


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def text(value: Any) -> bool:
    """A JSON string that is valid UTF-8 text without NUL."""
    if not isinstance(value, str) or "\0" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_bundle(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=no_duplicate_keys)
    except (ValueError, RecursionError):
        refuse("bundle is not valid JSON")
    if not isinstance(data, dict) or set(data) != KEYS:
        refuse("bundle does not have the version 1 shape")
    if type(data["version"]) is not int or data["version"] != 1:
        refuse("unknown bundle version")
    if not isinstance(data["commit"], str) or not COMMIT.fullmatch(data["commit"]):
        refuse("bundle commit is not a full commit hash")
    fetched_at = data["fetched_at"]
    if (not isinstance(fetched_at, str) or not fetched_at or len(fetched_at) > 64
            or not fetched_at.isprintable()):
        refuse("bundle fetch time is not valid")
    if type(data["stale"]) is not bool:
        refuse("bundle stale flag is not a boolean")
    if data["stale_reason"] is not None and not isinstance(data["stale_reason"], str):
        refuse("bundle stale reason is not text")
    if not text(data["rules"]) or not data["rules"].strip():
        refuse("bundle rules are missing or not text")
    if not isinstance(data["files"], list) or not data["files"]:
        refuse("bundle has no skill files")
    return data


def skill_path(path: Any, number: int) -> tuple[str, str]:
    """(skill, path inside the skill) for a safe `skills/<skill>/<rest>` path, else refuse."""
    if not isinstance(path, str) or not text(path) or not path:
        refuse(f"file {number}: path is not text")
    if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        refuse(f"file {number}: path has a backslash or control character")
    segments = path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        refuse(f"file {number}: path has an empty, '.' or '..' segment")
    if segments[0] != "skills" or len(segments) < 3:
        refuse(f"file {number}: path is not inside a skill directory under skills/")
    return segments[1], "/".join(segments[2:])


def fold(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def skills_of(files: list[Any]) -> dict[str, dict[str, bytes]]:
    """skill name -> {path inside the skill: content bytes}, after validating every entry."""
    skills: dict[str, dict[str, bytes]] = {}
    seen: set[str] = set()
    parents: set[str] = set()
    for number, entry in enumerate(files, 1):
        if not isinstance(entry, dict) or set(entry) != {"path", "content"}:
            refuse(f"file {number}: entry is not exactly a path and a text content")
        skill, rest = skill_path(entry["path"], number)
        if not text(entry["content"]):
            refuse(f"file {number}: content is not UTF-8 text without NUL")
        key = fold(entry["path"])
        if key in seen:
            refuse(f"file {number}: duplicate path")
        seen.add(key)
        segments = key.split("/")
        parents.update("/".join(segments[:i]) for i in range(2, len(segments)))
        skills.setdefault(skill, {})[rest] = entry["content"].encode("utf-8")
    if seen & parents:
        refuse("a path is both a file and a directory")
    folded = [fold(name) for name in skills]
    if len(set(folded)) != len(folded):
        refuse("two skill directories differ only by case")
    return skills


# --- install ---------------------------------------------------------------------

def check_root(home: pathlib.Path, root: pathlib.Path) -> None:
    """Every existing directory from HOME down to the skill root is real, never a symlink."""
    current = home
    for part in root.relative_to(home).parts:
        current = current / part
        if current.is_symlink():
            refuse("a skill root is a symlink; refusing to write through it")
        if current.exists() and not current.is_dir():
            refuse("a skill root is not a directory")


def identical(target: pathlib.Path, files: dict[str, bytes]) -> bool:
    """True when target is a real directory holding exactly these files (so nothing changes)."""
    if target.is_symlink() or not target.is_dir():
        return False
    found: set[str] = set()
    for dirpath, dirs, names in os.walk(target):
        here = pathlib.Path(dirpath)
        if any((here / d).is_symlink() for d in dirs):
            return False
        for name in names:
            path = here / name
            rel = path.relative_to(target).as_posix()
            if path.is_symlink() or not path.is_file() or files.get(rel) != path.read_bytes():
                return False
            found.add(rel)
    return found == set(files)


def new_stamp(backups: pathlib.Path) -> pathlib.Path:
    backups.mkdir(mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    for suffix in [""] + [f"-{n}" for n in range(2, 100)]:
        try:
            (backups / (stamp + suffix)).mkdir()
            return backups / (stamp + suffix)
        except FileExistsError:
            continue
    refuse("no free backup directory name")


def prune(top: pathlib.Path) -> None:
    """Remove empty directories under and including top; anything with content stays."""
    for dirpath, _dirs, _names in os.walk(top, topdown=False):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


def missing_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """The directories that must be created for root to exist, outermost first."""
    missing: list[pathlib.Path] = []
    while not root.exists():
        missing.insert(0, root)
        root = root.parent
    return missing


def install(home: pathlib.Path, provider: str, skills: dict[str, dict[str, bytes]]) -> None:
    roots = [home / r for r in ROOTS[provider]]
    try:
        for root in roots:
            check_root(home, root)
        plan = [(root, name) for root in roots for name in sorted(skills)
                if not identical(root / name, skills[name])]
        if not plan:
            return
        stamp = new_stamp(home / BACKUP_ROOT)
    except OSError:
        prune_backups(home)
        refuse("cannot read the skill roots or create the backup directory")
    staging = stamp / ".staging"
    undo: list[tuple[pathlib.Path, pathlib.Path]] = []  # (moved to, moved from), newest last
    created: list[pathlib.Path] = []
    try:
        for number, (_root, name) in enumerate(plan):
            for rest, content in skills[name].items():
                path = staging / str(number) / rest
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "xb") as handle:
                    handle.write(content)
        for number, (root, name) in enumerate(plan):
            for directory in missing_dirs(root):
                directory.mkdir()
                created.append(directory)
            target = root / name
            if os.path.lexists(target):
                saved = stamp / root.relative_to(home) / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                os.rename(target, saved)
                undo.append((saved, target))
            os.rename(staging / str(number), target)
            undo.append((target, staging / str(number)))
    except OSError:
        restored = rollback(undo, created)
        shutil.rmtree(staging, ignore_errors=True)
        prune(stamp)
        prune_backups(home)
        refuse("install failed; previous skills restored" if restored
               else "install failed and could not be fully undone; previous skills are in the backup directory")
    shutil.rmtree(staging)
    prune(stamp)
    prune_backups(home)


def rollback(undo: list[tuple[pathlib.Path, pathlib.Path]], created: list[pathlib.Path]) -> bool:
    restored = True
    for moved_to, moved_from in reversed(undo):
        try:
            os.rename(moved_to, moved_from)
        except OSError:
            restored = False
    for directory in reversed(created):
        try:
            directory.rmdir()
        except OSError:
            restored = False
    return restored


def prune_backups(home: pathlib.Path) -> None:
    try:
        (home / BACKUP_ROOT).rmdir()
    except OSError:
        pass


# --- output ------------------------------------------------------------------------

def session_context(index: str, bundle: dict[str, Any]) -> str:
    parts = [index, f"Cloud rules (bundle {bundle['commit'][:12]}):", bundle["rules"]]
    if bundle["stale"]:
        reason = bundle["stale_reason"] if bundle["stale_reason"] in STALE_REASONS else "unspecified"
        parts.append(f"Notice: the skills and rules bundle is stale (reason: {reason}). "
                     f"It is the last good copy, fetched at {bundle['fetched_at']}.")
    return "\n\n".join(parts)


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 2 or argv[1] not in ROOTS:
            refuse("expected claude or codex")
        home_value = os.environ.get("HOME", "")
        if not os.path.isabs(home_value):
            refuse("HOME is not an absolute path")
        index = index_context(sys.stdin.buffer.read(INDEX_INPUT_LIMIT + 1))
        bundle = parse_bundle(fetch(load_client()))
        install(pathlib.Path(home_value), argv[1], skills_of(bundle["files"]))
    except Refused as refused:
        print(f"ail-cloud: bundle refused: {refused}", file=sys.stderr)
        return 3
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                     "additionalContext": session_context(index, bundle)}}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
