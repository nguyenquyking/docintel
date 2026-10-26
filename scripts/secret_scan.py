#!/usr/bin/env python3
"""Block secrets and internal identifiers before they reach a public repository.

Runs as the pre-commit hook (staged content), the pre-push hook (every object in
history plus commit messages) and in CI. Standard library only, Python >= 3.9,
so it works in a fresh clone before any dependency is installed.

    python3 scripts/secret_scan.py --staged      # what is about to be committed
    python3 scripts/secret_scan.py --tracked     # every tracked file (default)
    python3 scripts/secret_scan.py --history     # every blob and message ever committed
    python3 scripts/secret_scan.py PATH ...      # explicit files

Two layers:
  1. Built-in rules: provider tokens, private keys, cloud workspace hosts,
     credential-looking assignments, forbidden file names (.env, *.pem, *.db).
  2. A local, git-ignored denylist (.secret-denylist): one literal or
     ``re:<regex>`` per line — company names, internal hosts, endpoint names,
     e-mails. It lets the scanner block identifiers without the repository
     ever containing them.

A line can opt out with the marker ``secret-scan: allow`` — use it only for
documentation that names a rule, never for a real value.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DENYLIST_FILE = os.path.join(ROOT, ".secret-denylist")
ALLOW_MARKER = "secret-scan: allow"
MAX_BYTES = 2_000_000

RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("databricks-pat", re.compile(r"\bdapi[0-9a-f]{32}(?:-\d+)?\b")),
    ("databricks-oauth-secret", re.compile(r"\bdose[0-9a-f]{32}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("cloud-workspace-host", re.compile(
        r"\b[a-z0-9][a-z0-9-]*\.(?:cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)\b", re.I)),
    ("private-ip", re.compile(
        r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b")),
]

# key = "value" where the value looks like a real credential (long, high entropy).
ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|client[_-]?secret|access[_-]?key)\w*"
    r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-/+=.]{16,})")
PLACEHOLDER = re.compile(r"(?i)(change[-_]?me|your[-_]|example|placeholder|dummy|fake|test|xxxx|<|\$\{|\*\*\*)")

FORBIDDEN_NAMES = [
    (re.compile(r"(^|/)\.env(\.[^/]*)?$"), "dotenv file"),
    (re.compile(r"\.(pem|key|p12|pfx)$"), "key material"),
    (re.compile(r"(^|/)\.databrickscfg$"), "databricks config"),
    (re.compile(r"(^|/)\.secret-denylist$"), "local denylist"),
    (re.compile(r"\.(db|sqlite|sqlite3)(-journal|-wal|-shm)?$"), "database file"),
    (re.compile(r"(^|/)data/"), "runtime data directory"),
    (re.compile(r"(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$"), "ssh key"),
]
ALLOWED_NAMES = re.compile(r"(^|/)\.env\.example$")


@dataclass
class Finding:
    where: str
    line: int
    rule: str
    excerpt: str

    def __str__(self) -> str:
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"  {loc}  [{self.rule}]  {self.excerpt}"


def redact(value: str) -> str:
    value = value.strip()
    return value[:4] + "…" + f"({len(value)} chars)" if len(value) > 8 else "…"


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def load_denylist(path: str = DENYLIST_FILE) -> List[Tuple[str, "re.Pattern[str]"]]:
    rules: List[Tuple[str, "re.Pattern[str]"]] = []
    if not os.path.exists(path):
        return rules
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("re:"):
                rules.append((f"denylist#{n}", re.compile(line[3:], re.I)))
            else:
                rules.append((f"denylist#{n}", re.compile(re.escape(line), re.I)))
    return rules


def scan_text(where: str, text: str, denylist) -> Iterator[Finding]:
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for rule, pattern in RULES:
            for m in pattern.finditer(line):
                yield Finding(where, n, rule, redact(m.group(0)))
        for m in ASSIGNMENT.finditer(line):
            value = m.group(1)
            if PLACEHOLDER.search(value) or entropy(value) < 3.5:
                continue
            if re.fullmatch(r"[a-z_.]+", value):  # identifiers such as settings.llm_api_key
                continue
            yield Finding(where, n, "credential-assignment", redact(value))
        for rule, pattern in denylist:
            for m in pattern.finditer(line):
                yield Finding(where, n, rule, redact(m.group(0)))


def check_name(path: str) -> Optional[Finding]:
    if ALLOWED_NAMES.search(path):
        return None
    for pattern, why in FORBIDDEN_NAMES:
        if pattern.search(path):
            return Finding(path, 0, "forbidden-file", why)
    return None


def decode(blob: bytes) -> Optional[str]:
    if b"\x00" in blob[:8192]:
        return None  # binary: only the file name is checked
    return blob[:MAX_BYTES].decode("utf-8", errors="replace")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def staged() -> Iterator[Tuple[str, bytes]]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\x00")
    for name in filter(None, names):
        path = name.decode()
        yield path, git("show", f":{path}")


def tracked() -> Iterator[Tuple[str, bytes]]:
    for name in filter(None, git("ls-files", "-z").split(b"\x00")):
        path = name.decode()
        full = os.path.join(ROOT, path)
        if os.path.isfile(full):
            with open(full, "rb") as fh:
                yield path, fh.read(MAX_BYTES)


def history() -> Iterator[Tuple[str, bytes]]:
    seen = set()
    for line in git("rev-list", "--all", "--objects").decode().splitlines():
        sha, _, path = line.partition(" ")
        if not path or sha in seen:
            continue
        if git("cat-file", "-t", sha).strip() != b"blob":
            continue
        seen.add(sha)
        yield f"{path}@{sha[:8]}", git("cat-file", "-p", sha)
    for sha in git("rev-list", "--all").decode().split():
        yield f"commit {sha[:8]} message", git("log", "-1", "--format=%B", sha)


def files(paths: Iterable[str]) -> Iterator[Tuple[str, bytes]]:
    for path in paths:
        with open(path, "rb") as fh:
            yield os.path.relpath(path, ROOT), fh.read(MAX_BYTES)


def run(items: Iterable[Tuple[str, bytes]], denylist) -> List[Finding]:
    found: List[Finding] = []
    for where, blob in items:
        name_hit = check_name(where.split("@")[0]) if not where.startswith("commit ") else None
        if name_hit:
            found.append(name_hit)
        text = decode(blob)
        if text is not None:
            found.extend(scan_text(where, text, denylist))
    return found


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan the index (pre-commit)")
    mode.add_argument("--tracked", action="store_true", help="scan tracked files (default)")
    mode.add_argument("--history", action="store_true", help="scan all history and messages (pre-push)")
    ap.add_argument("paths", nargs="*", help="explicit files to scan")
    args = ap.parse_args(argv)

    denylist = load_denylist()
    if args.paths:
        items = files(args.paths)
    elif args.staged:
        items = staged()
    elif args.history:
        items = history()
    else:
        items = tracked()
    findings = run(items, denylist)
    scope = "paths" if args.paths else "staged" if args.staged else "history" if args.history else "tracked"
    if findings:
        print(f"secret-scan: BLOCKED — {len(findings)} finding(s) in {scope} content:", file=sys.stderr)
        for f in findings:
            print(f, file=sys.stderr)
        print("Move the value to .env (git-ignored) and reference it through settings.", file=sys.stderr)
        return 1
    extra = f", {len(denylist)} local denylist rule(s)" if denylist else ", no local denylist"
    print(f"secret-scan: clean ({scope}{extra})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
