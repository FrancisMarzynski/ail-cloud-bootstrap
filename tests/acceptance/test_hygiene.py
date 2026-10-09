"""Scenarios 9 and 10: public-repo hygiene over every file, and the README's activation notes.

The forbidden patterns are assembled at runtime from fragments so that their literals never
appear in a tracked file (this one included). Each pattern has a positive control, so a
scanner that silently matches nothing fails too.
"""

import ipaddress
import math
import re
import secrets
import string
import subprocess
import unittest
from collections import Counter

from tests.acceptance.support import REPO, clean_env


def j(*parts: str) -> str:
    return "".join(parts)


def dotted(*labels: str) -> str:
    return ".".join(labels)


ALLOWED_HOSTS = {"127.0.0.1", j("local", "host"), ".".join(["0"] * 4), dotted("github", "com"),
                 dotted("raw", "githubusercontent", "com")}
# Reserved names (RFC 2606 / 6761) cannot point at a real service.
RESERVED_SUFFIXES = (".invalid", ".test", ".example", ".localhost", dotted("example", "com"),
                     dotted("example", "org"), dotted("example", "net"))
TLDS = "com|net|org|io|co|dev|ai|cloud|xyz|tech|pl|eu|us|uk|de"

TOKEN_PREFIX = re.compile(r"\b(?:" + "|".join(j("g", "h", c, "_") for c in "pousr") + "|" + j("git", "hub_pat_")
                          + "|" + j("s", "k-") + "|" + j("sb", "_secret_") + "|" + j("xo", "x[abposr]-")
                          + r")[A-Za-z0-9_-]{16,}")
BEARER = re.compile(j("[Bb]e", "arer") + r"\s+[A-Za-z0-9._~+/=-]{20,}")
JWT = re.compile(j("e", "yJ") + r"[A-Za-z0-9_-]{8,}\." + j("e", "yJ"))
LONG_HEX = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{41,}(?![0-9A-Fa-f])")
# "/" is left out so ordinary paths are never candidates; most secrets still have a long run.
BASE64_RUN = re.compile(r"[A-Za-z0-9+_=-]{32,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
URL_HOST = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*" + ":" + r"//(?:[^@/\s'\"<>]*@)?(\[[^\]]*\]|[^/\s:'\"<>)\]},;?#]+)")
BARE_HOST = re.compile(r"(?<![\w@./-])(?:[A-Za-z0-9-]+\.)+(?:" + TLDS + r")\b(?![\w-])", re.I)
HOME_PATH = re.compile(r"(?<![\w.])/(?:" + j("Us", "ers") + "|" + j("ho", "me") + r")/[A-Za-z0-9._-]+|"
                       + "/" + j("private", "/var/") + "|" + "/" + j("var", "/folders/") + "|"
                       + r"[A-Za-z]:\\\\?" + j("Us", "ers"))
LOCAL_ONLY = re.compile("|".join([j("ail", "-ps", "ql"), r"\b" + j("ps", "ql") + r"\b", j("key", "chain"),
                                  j("find-generic-", "pass", "word"), j("secret", "-tool"), j("PG", "PASS", "WORD"),
                                  j("lib", "secret")]), re.I)


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def secret_like(run: str) -> bool:
    return (any(c.isdigit() for c in run) and any(c.isupper() for c in run) and any(c.islower() for c in run)
            and entropy(run) >= 4.3)


def host_allowed(host: str) -> bool:
    host = host.lower().strip("[]").rstrip(".")
    if not host or host[0] in "{$<%" or "{" in host:
        return True  # a template placeholder, not a hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in ALLOWED_HOSTS or host.endswith(RESERVED_SUFFIXES)
    return address.is_loopback or address.is_unspecified


def findings(text: str) -> list[str]:
    found: list[str] = []
    for label, pattern in (("credential prefix", TOKEN_PREFIX), ("bearer value", BEARER), ("jwt", JWT),
                           ("long hex", LONG_HEX), ("email", EMAIL), ("home path", HOME_PATH),
                           ("local-only tool", LOCAL_ONLY)):
        found += [f"{label}: {m.group(0)[:24]}" for m in pattern.finditer(text)]
    found += [f"base64-like secret: {m.group(0)[:12]}" for m in BASE64_RUN.finditer(text) if secret_like(m.group(0))]
    found += [f"hostname: {m.group(1)}" for m in URL_HOST.finditer(text) if not host_allowed(m.group(1))]
    found += [f"hostname: {m.group(0)}" for m in BARE_HOST.finditer(text) if not host_allowed(m.group(0))]
    return found


def scanned_files() -> list[str]:
    """Every tracked file plus every new file not yet committed (ignored files excluded)."""
    out = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=REPO, check=True,
                         capture_output=True, env=clean_env()).stdout.decode()
    return sorted({p for p in out.split("\0") if p and (REPO / p).is_file()})


class PublicRepoHygiene(unittest.TestCase):
    def test_scanner_detects_each_forbidden_shape(self) -> None:
        # Catches a scanner that matches nothing (a pattern typo would make the scan vacuous).
        # Misses: shapes no pattern describes (reviewers still read every diff).
        alphabet = string.ascii_letters + string.digits
        rand = "".join(secrets.choice(alphabet) for _ in range(36)) + "aZ9"
        controls = {
            "credential prefix": j("g", "hp_") + rand,
            "bearer value": j("Bea", "rer ") + rand,
            "jwt": j("ey", "J") + "hbGciOiJIUzI1." + j("ey", "J") + "zdWIiOiIx",
            "long hex": secrets.token_hex(32),
            "base64-like secret": rand + "Qx7",
            "email": j("someone", "@", dotted("mail", "example-corp", "com")),
            "hostname": j("https", "://", dotted("memory", "real-service", "co"), "/functions"),
            "home path": "/" + j("Us", "ers") + "/someone/project",
            "local-only tool": j("ail-", "ps", "ql"),
        }
        for label, sample in controls.items():
            with self.subTest(control=label):
                self.assertTrue(any(f.startswith(label) for f in findings(sample)), f"{label} not detected")
        for allowed in ("http://127.0.0.1:8080/memory", j("https", "://", dotted("github", "com"), "/owner/repo.git"),
                        "a" * 40, "Bearer fake-token-0000", "bash cloud/startup.sh claude",
                        "http://memory.invalid/memory", "uses: actions/checkout@v5", "@AGENTS.md"):
            with self.subTest(allowed=allowed):
                self.assertEqual(findings(allowed), [])

    def test_no_credentials_hosts_emails_home_paths_or_local_tools_in_any_file(self) -> None:
        # Catches a committed token, real endpoint hostname, email address, machine path, or a reference
        # to the local database wrapper or the OS credential store. Misses: personal prose that has none
        # of these shapes, and anything in git history that is no longer in a file.
        problems: list[str] = []
        files = scanned_files()
        self.assertIn("tests/acceptance/test_hygiene.py", files)
        for rel in files:
            raw = (REPO / rel).read_bytes()
            if b"\0" in raw:
                problems.append(f"{rel}: binary file in a text-only repository")
                continue
            for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                problems += [f"{rel}:{line_number}: {f}" for f in findings(line)]
        self.assertEqual(problems, [])


class Readme(unittest.TestCase):
    def test_readme_carries_activation_rollback_and_codex_status(self) -> None:
        # Catches a README without the pinned installer line, how to move the pin, rollback, or the
        # statement that Codex is unproven. Misses: whether the instructions are correct on a real host.
        text = (REPO / "README.md").read_text().lower()
        for term in ("cloud/setup.sh", "pin", "rollback", "codex", "unproven"):
            self.assertIn(term, text, f"README must mention {term}")
        self.assertRegex(text, r"codex[^\n]*unproven|unproven[^\n]*codex")
