#!/usr/bin/env python3
"""
Hermes Sentry — Repo & Contract Monitoring Agent
All external calls go through `requests` (no shell interpolation of user input).
State is persisted with atomic writes and file locking.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("ERROR: requests library required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────────────────────

SENTRY_DIR = Path.home() / ".hermes" / "sentry"
WATCHLIST_PATH = SENTRY_DIR / "watchlist.json"
STATE_PATH = SENTRY_DIR / "state.json"
LOG_PATH = SENTRY_DIR / "sentry.log"
LOCK_PATH = SENTRY_DIR / ".sentry.lock"

GITHUB_API = "https://api.github.com"

CHAINS: dict[str, dict[str, str]] = {
    "base": {
        "rpc": "https://mainnet.base.org",
        "explorer": "https://basescan.org",
        "name": "Base",
    },
    "ethereum": {
        "rpc": "https://eth.llamarpc.com",
        "explorer": "https://etherscan.io",
        "name": "Ethereum",
    },
    "arbitrum": {
        "rpc": "https://arb1.arbitrum.io/rpc",
        "explorer": "https://arbiscan.io",
        "name": "Arbitrum",
    },
}

# ERC-1967 implementation slot
ERC1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# GitHub bot authors to filter
BOT_AUTHORS = {
    "dependabot[bot]",
    "renovate[bot]",
    "github-actions[bot]",
    "codecov[bot]",
    "mergify[bot]",
    "greenkeeper[bot]",
}

# File patterns for severity classification
CRITICAL_PATTERNS = [r"\.sol$", r"security", r"auth", r"\.rs$"]
NOISE_PATTERNS = [
    r"\.md$", r"\.txt$", r"\.mdx$",
    r"package-lock\.json$", r"yarn\.lock$", r"bun\.lockb$",
    r"\.prettierrc", r"\.eslintrc", r"\.config\.(js|ts|mjs)$",
    r"CHANGELOG", r"LICENSE",
]
TEST_PATTERNS = [r"\.test\.", r"\.spec\.", r"__tests__"]

# Limits
MAX_DIFF_LINES_PER_FILE = 500
MAX_DIFF_LINES_TOTAL = 2000
MAX_COMMITS_PER_POLL = 25
REQUEST_TIMEOUT = 15

# ─── Logging ──────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    SENTRY_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sentry")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(LOG_PATH)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
    return logger

log = setup_logging()

# ─── Validation ───────────────────────────────────────────────────────────────

REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
GITHUB_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)/?")
OWNER_REPO_RE = re.compile(r"^([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)$")

def validate_repo_name(name: str) -> str:
    """Validate a GitHub owner or repo name."""
    if not name or len(name) > 100:
        raise ValueError(f"Invalid repo name: too long or empty")
    if not REPO_NAME_RE.match(name):
        raise ValueError(f"Invalid repo name: '{name}' contains disallowed characters")
    return name

def validate_address(addr: str) -> str:
    """Validate an Ethereum address."""
    if not ADDRESS_RE.match(addr):
        raise ValueError(f"Invalid address format: '{addr}' (expected 0x + 40 hex chars)")
    return addr.lower()

def validate_chain(chain: str) -> str:
    """Validate chain name against known chains."""
    if chain not in CHAINS:
        raise ValueError(f"Unknown chain: '{chain}'. Supported: {', '.join(CHAINS.keys())}")
    return chain

def validate_branches(branches: list[str]) -> list[str]:
    """Validate branch names."""
    branch_re = re.compile(r"^[a-zA-Z0-9._/-]+$")
    for b in branches:
        if not branch_re.match(b) or len(b) > 100:
            raise ValueError(f"Invalid branch name: '{b}'")
    return branches

# ─── Atomic File I/O ──────────────────────────────────────────────────────────

def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: write to temp file, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON with fallback default."""
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Failed to load {path}: {e}")
        return default if default is not None else {}

class FileLock:
    """Simple file-based lock to prevent concurrent state modifications."""
    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise RuntimeError("Another sentry process is running. Aborting to prevent state corruption.")
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)

# ─── Watchlist Management ─────────────────────────────────────────────────────

def load_watchlist() -> dict:
    return load_json(WATCHLIST_PATH, {"repos": [], "contracts": [], "wallets": []})

def save_watchlist(wl: dict) -> None:
    atomic_write_json(WATCHLIST_PATH, wl)

def load_state() -> dict:
    return load_json(STATE_PATH, {"last_poll": None, "poll_count": 0, "alerts_24h": [], "errors": []})

def save_state(state: dict) -> None:
    atomic_write_json(STATE_PATH, state)

# ─── GitHub Client ────────────────────────────────────────────────────────────

def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def github_get(path: str, params: Optional[dict] = None) -> Any:
    """Safe GitHub API GET — no string interpolation of user input into URLs."""
    url = f"{GITHUB_API}{path}"
    try:
        resp = requests.get(url, headers=github_headers(), params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            log.warning(f"GitHub 403 (rate limit remaining: {remaining}): {path}")
            return None
        if resp.status_code == 404:
            log.warning(f"GitHub 404: {path}")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"GitHub request failed: {path} — {e}")
        return None

def github_rate_limit() -> dict:
    """Check GitHub rate limit status."""
    data = github_get("/rate_limit")
    if data and "rate" in data:
        return data["rate"]
    return {"remaining": -1, "limit": -1, "reset": 0}

# ─── Onchain Client ──────────────────────────────────────────────────────────

def rpc_call(rpc_url: str, method: str, params: list) -> Any:
    """Make a JSON-RPC call. No user input in the URL — only validated chain RPCs."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        resp = requests.post(rpc_url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.error(f"RPC error ({method}): {data['error']}")
            return None
        return data.get("result")
    except requests.RequestException as e:
        log.error(f"RPC request failed ({rpc_url}, {method}): {e}")
        return None

def get_code_hash(rpc_url: str, address: str) -> Optional[str]:
    """Get SHA-256 hash of contract bytecode."""
    code = rpc_call(rpc_url, "eth_getCode", [address, "latest"])
    if not code or code == "0x":
        return None
    return hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()

def get_impl_address(rpc_url: str, address: str) -> Optional[str]:
    """Read ERC-1967 implementation slot."""
    result = rpc_call(rpc_url, "eth_getStorageAt", [address, ERC1967_IMPL_SLOT, "latest"])
    if not result or result == "0x" + "0" * 64:
        return None
    # Extract address from 32-byte slot (last 20 bytes)
    return "0x" + result[-40:]

def get_eth_balance(rpc_url: str, address: str) -> Optional[float]:
    """Get ETH balance in ether."""
    result = rpc_call(rpc_url, "eth_getBalance", [address, "latest"])
    if result is None:
        return None
    return int(result, 16) / 1e18

def get_block_number(rpc_url: str) -> Optional[int]:
    """Get latest block number."""
    result = rpc_call(rpc_url, "eth_blockNumber", [])
    if result is None:
        return None
    return int(result, 16)

# ─── Severity Classification ─────────────────────────────────────────────────

def classify_file(filename: str) -> str:
    """Classify a single file change into a severity bucket."""
    for pattern in CRITICAL_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return "warning"  # Individual files are warning; aggregate can escalate
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return "noise"
    for pattern in TEST_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return "info"
    return "info"

def classify_commit(commit: dict) -> str:
    """Classify a commit's overall severity."""
    files = commit.get("files", [])
    if not files:
        return "info"

    severities = [classify_file(f.get("filename", "")) for f in files]

    if "warning" in severities:
        # If most files are warning-level, escalate
        warning_ratio = severities.count("warning") / len(severities)
        if warning_ratio > 0.5:
            return "warning"
        return "warning"

    if all(s == "noise" for s in severities):
        return "noise"

    return "info"

def is_bot_commit(commit: dict) -> bool:
    """Check if commit is from a known bot."""
    author = commit.get("author")
    if author and author.get("login") in BOT_AUTHORS:
        return True
    commit_data = commit.get("commit", {})
    author_name = commit_data.get("author", {}).get("name", "")
    return author_name in BOT_AUTHORS

# ─── GitHub Polling ───────────────────────────────────────────────────────────

def poll_repo(repo_entry: dict) -> list[dict]:
    """Poll a single repo for new commits. Returns list of alert dicts."""
    owner = repo_entry["owner"]
    repo = repo_entry["repo"]
    last_sha = repo_entry.get("last_checked_sha")
    last_at = repo_entry.get("last_checked_at")

    alerts = []

    # Fetch recent commits
    params: dict[str, Any] = {"per_page": MAX_COMMITS_PER_POLL}
    if last_at:
        params["since"] = last_at

    commits = github_get(f"/repos/{owner}/{repo}/commits", params=params)
    if commits is None:
        return [{"severity": "warning", "type": "error", "source": f"{owner}/{repo}",
                 "message": f"Failed to fetch commits for {owner}/{repo}. GitHub may be down or rate-limited."}]

    if not commits:
        return []

    # Filter out already-seen commits and bots
    new_commits = []
    for c in commits:
        if c["sha"] == last_sha:
            break
        if is_bot_commit(c):
            continue
        new_commits.append(c)

    if not new_commits:
        # Update timestamp even if no new commits
        repo_entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        return []

    # Fetch details for each new commit (with diff)
    detailed_commits = []
    total_diff_lines = 0
    for c in new_commits[:MAX_COMMITS_PER_POLL]:
        detail = github_get(f"/repos/{owner}/{repo}/commits/{c['sha']}")
        if detail:
            # Truncate large diffs
            for f in detail.get("files", []):
                patch = f.get("patch", "")
                lines = patch.split("\n")
                if len(lines) > MAX_DIFF_LINES_PER_FILE:
                    f["patch"] = "\n".join(lines[:MAX_DIFF_LINES_PER_FILE]) + f"\n... truncated ({len(lines)} lines total)"
                total_diff_lines += min(len(lines), MAX_DIFF_LINES_PER_FILE)
                if total_diff_lines > MAX_DIFF_LINES_TOTAL:
                    f["patch"] = "(diff omitted — total diff too large)"
            detailed_commits.append(detail)

    if not detailed_commits:
        return []

    # Batch if 3+ commits
    if len(detailed_commits) >= 3:
        # Group into a single batch alert
        all_files = []
        total_additions = 0
        total_deletions = 0
        authors = set()
        max_severity = "noise"

        for dc in detailed_commits:
            for f in dc.get("files", []):
                all_files.append(f.get("filename", ""))
                total_additions += f.get("additions", 0)
                total_deletions += f.get("deletions", 0)
            sev = classify_commit(dc)
            if sev == "warning":
                max_severity = "warning"
            elif sev == "info" and max_severity == "noise":
                max_severity = "info"
            author = dc.get("commit", {}).get("author", {}).get("name", "unknown")
            authors.add(author)

        alerts.append({
            "severity": max_severity,
            "type": "commit_batch",
            "source": f"{owner}/{repo}",
            "count": len(detailed_commits),
            "authors": list(authors),
            "total_files": len(set(all_files)),
            "additions": total_additions,
            "deletions": total_deletions,
            "first_time": detailed_commits[-1].get("commit", {}).get("author", {}).get("date"),
            "last_time": detailed_commits[0].get("commit", {}).get("author", {}).get("date"),
            "commits": [
                {
                    "sha": dc["sha"][:8],
                    "message": dc.get("commit", {}).get("message", "").split("\n")[0][:120],
                    "author": dc.get("commit", {}).get("author", {}).get("name", "unknown"),
                    "files": [f.get("filename", "") for f in dc.get("files", [])],
                    "severity": classify_commit(dc),
                }
                for dc in detailed_commits
            ],
            "url": f"https://github.com/{owner}/{repo}/commits",
        })
    else:
        # Individual alerts
        for dc in detailed_commits:
            sev = classify_commit(dc)
            files = dc.get("files", [])
            msg = dc.get("commit", {}).get("message", "").split("\n")[0][:200]
            author = dc.get("commit", {}).get("author", {}).get("name", "unknown")
            total_add = sum(f.get("additions", 0) for f in files)
            total_del = sum(f.get("deletions", 0) for f in files)

            alerts.append({
                "severity": sev,
                "type": "commit",
                "source": f"{owner}/{repo}",
                "sha": dc["sha"][:8],
                "message": msg,
                "author": author,
                "file_count": len(files),
                "additions": total_add,
                "deletions": total_del,
                "files": [f.get("filename", "") for f in files[:10]],
                "patches": {
                    f.get("filename", ""): f.get("patch", "")
                    for f in files[:5]
                    if f.get("patch")
                },
                "url": dc.get("html_url", ""),
            })

    # Update baseline
    repo_entry["last_checked_sha"] = new_commits[0]["sha"]
    repo_entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()

    return alerts

# ─── Onchain Polling ──────────────────────────────────────────────────────────

def poll_contract(entry: dict) -> list[dict]:
    """Poll a single contract for changes."""
    chain = entry["chain"]
    rpc_url = CHAINS[chain]["rpc"]
    address = entry["address"]
    label = entry.get("label", address[:10])
    explorer = CHAINS[chain]["explorer"]
    alerts = []

    watch_types = entry.get("watch_type", ["upgrades", "balance"])

    if "upgrades" in watch_types:
        # Check implementation slot (proxy upgrade)
        new_impl = get_impl_address(rpc_url, address)
        old_impl = entry.get("last_impl_address")

        if old_impl is not None and new_impl is not None and new_impl != old_impl:
            alerts.append({
                "severity": "critical",
                "type": "contract_upgrade",
                "source": label,
                "chain": chain,
                "address": address,
                "old_impl": old_impl,
                "new_impl": new_impl,
                "explorer_url": f"{explorer}/address/{address}",
            })

        if new_impl is not None:
            entry["last_impl_address"] = new_impl

        # Check bytecode hash (direct upgrade — rare)
        new_hash = get_code_hash(rpc_url, address)
        old_hash = entry.get("last_code_hash")

        if old_hash is not None and new_hash is not None and new_hash != old_hash:
            alerts.append({
                "severity": "critical",
                "type": "bytecode_change",
                "source": label,
                "chain": chain,
                "address": address,
                "old_hash": old_hash[:16] + "...",
                "new_hash": new_hash[:16] + "...",
                "explorer_url": f"{explorer}/address/{address}",
            })

        if new_hash is not None:
            entry["last_code_hash"] = new_hash

    if "balance" in watch_types:
        new_balance = get_eth_balance(rpc_url, address)
        old_balance = entry.get("last_balance")

        if old_balance is not None and new_balance is not None:
            delta = new_balance - old_balance
            threshold = entry.get("threshold_eth", 1.0)
            if abs(delta) >= threshold:
                alerts.append({
                    "severity": "warning" if abs(delta) < threshold * 10 else "critical",
                    "type": "balance_change",
                    "source": label,
                    "chain": chain,
                    "address": address,
                    "old_balance": f"{old_balance:.6f}",
                    "new_balance": f"{new_balance:.6f}",
                    "delta": f"{delta:+.6f}",
                    "explorer_url": f"{explorer}/address/{address}",
                })

        if new_balance is not None:
            entry["last_balance"] = new_balance

    entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return alerts

def poll_wallet(entry: dict) -> list[dict]:
    """Poll a wallet for balance changes."""
    chain = entry["chain"]
    rpc_url = CHAINS[chain]["rpc"]
    address = entry["address"]
    label = entry.get("label", address[:10])
    explorer = CHAINS[chain]["explorer"]
    threshold = entry.get("threshold_eth", 1.0)

    new_balance = get_eth_balance(rpc_url, address)
    old_balance = entry.get("last_balance")
    alerts = []

    if old_balance is not None and new_balance is not None:
        delta = new_balance - old_balance
        if abs(delta) >= threshold:
            sev = "critical" if abs(delta) >= threshold * 10 else "warning"
            alerts.append({
                "severity": sev,
                "type": "wallet_movement",
                "source": label,
                "chain": chain,
                "address": address,
                "old_balance": f"{old_balance:.6f}",
                "new_balance": f"{new_balance:.6f}",
                "delta": f"{delta:+.6f}",
                "threshold": f"{threshold:.6f}",
                "explorer_url": f"{explorer}/address/{address}",
            })

    if new_balance is not None:
        entry["last_balance"] = new_balance

    entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return alerts

# ─── Commands ─────────────────────────────────────────────────────────────────

def parse_repo_input(text: str) -> tuple[str, str]:
    """Parse 'owner/repo', GitHub URL, or other formats into (owner, repo)."""
    text = text.strip().rstrip("/")

    # Try GitHub URL first
    m = GITHUB_URL_RE.match(text)
    if m:
        return validate_repo_name(m.group(1)), validate_repo_name(m.group(2))

    # Try owner/repo
    m = OWNER_REPO_RE.match(text)
    if m:
        return validate_repo_name(m.group(1)), validate_repo_name(m.group(2))

    raise ValueError(f"Can't parse repo: '{text}'. Use 'owner/repo' or a GitHub URL.")


SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🟢", "noise": "⚪"}


def format_alert_human(alert: dict) -> str:
    """Format a single alert as a clean, readable Telegram message."""
    sev = alert.get("severity", "info")
    emoji = SEVERITY_EMOJI.get(sev, "⚪")
    alert_type = alert.get("type", "")
    source = alert.get("source", "unknown")

    if alert_type == "commit":
        files = alert.get("files", [])
        file_list = "\n".join(f"  • {f}" for f in files[:5])
        if len(files) > 5:
            file_list += f"\n  ... +{len(files) - 5} more"
        return (
            f"{emoji} {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{alert.get('message', '')}\n\n"
            f"Files: {alert.get('file_count', 0)} changed "
            f"(+{alert.get('additions', 0)} -{alert.get('deletions', 0)})\n"
            f"{file_list}\n\n"
            f"By {alert.get('author', '?')} • {alert.get('sha', '?')}\n"
            f"{alert.get('url', '')}"
        )

    elif alert_type == "commit_batch":
        commits_preview = "\n".join(
            f"  {SEVERITY_EMOJI.get(c.get('severity', 'info'), '⚪')} {c.get('sha', '?')} — {c.get('message', '')}"
            for c in alert.get("commits", [])[:6]
        )
        remaining = len(alert.get("commits", [])) - 6
        if remaining > 0:
            commits_preview += f"\n  ... +{remaining} more"
        return (
            f"{emoji} {source} — {alert.get('count', 0)} new commits\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{commits_preview}\n\n"
            f"Files: {alert.get('total_files', 0)} changed "
            f"(+{alert.get('additions', 0)} -{alert.get('deletions', 0)})\n"
            f"Authors: {', '.join(alert.get('authors', []))}\n"
            f"{alert.get('url', '')}"
        )

    elif alert_type == "contract_upgrade":
        return (
            f"🔴 CONTRACT UPGRADE: {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Chain: {alert.get('chain', '?')}\n"
            f"Address: {alert.get('address', '?')}\n\n"
            f"Old impl → {alert.get('old_impl', '?')}\n"
            f"New impl → {alert.get('new_impl', '?')}\n\n"
            f"⚠️ Review before interacting.\n"
            f"{alert.get('explorer_url', '')}"
        )

    elif alert_type == "bytecode_change":
        return (
            f"🔴 BYTECODE CHANGED: {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Chain: {alert.get('chain', '?')}\n"
            f"Address: {alert.get('address', '?')}\n\n"
            f"Old hash: {alert.get('old_hash', '?')}\n"
            f"New hash: {alert.get('new_hash', '?')}\n\n"
            f"{alert.get('explorer_url', '')}"
        )

    elif alert_type in ("balance_change", "wallet_movement"):
        delta = alert.get("delta", "0")
        direction = "📈" if not delta.startswith("-") else "📉"
        return (
            f"{emoji} {direction} {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Chain: {alert.get('chain', '?')}\n"
            f"Address: {alert.get('address', '?')}\n\n"
            f"{alert.get('old_balance', '?')} → {alert.get('new_balance', '?')} ETH\n"
            f"Change: {delta} ETH\n\n"
            f"{alert.get('explorer_url', '')}"
        )

    elif alert_type == "error":
        return f"⚠️ SENTRY ERROR: {source}\n{alert.get('message', '')}"

    else:
        return f"{emoji} {source}: {json.dumps(alert, indent=2)}"


def format_poll_human(output: dict) -> str:
    """Format full poll output as readable text."""
    alerts = output.get("alerts", [])
    errors = output.get("errors", [])
    noise = output.get("noise_count", 0)
    targets = output.get("targets_checked", 0)

    if not alerts and not errors:
        return f"✅ Sentry: {targets} targets checked — all clear." + (f" ({noise} noise filtered)" if noise else "")

    parts = []
    for alert in alerts:
        parts.append(format_alert_human(alert))

    if errors:
        parts.append("⚠️ Errors:\n" + "\n".join(f"  • {e}" for e in errors))

    header = f"🔔 Sentry: {len(alerts)} alert{'s' if len(alerts) != 1 else ''} from {targets} targets"
    if noise:
        header += f" ({noise} noise filtered)"

    return header + "\n\n" + "\n\n".join(parts)


def format_digest_human(digest: dict) -> str:
    """Format daily digest as readable text."""
    date = digest.get("date", "?")
    total = digest.get("total_alerts", 0)
    repos = digest.get("repos_watched", 0)
    contracts = digest.get("contracts_watched", 0)
    wallets = digest.get("wallets_watched", 0)

    parts = [
        f"📊 SENTRY DAILY DIGEST — {date}",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"Watching: {repos} repos, {contracts} contracts, {wallets} wallets",
        f"Total alerts (24h): {total}",
    ]

    for sev in ["critical", "warning", "info"]:
        items = digest.get("by_severity", {}).get(sev, [])
        if items:
            emoji = SEVERITY_EMOJI[sev]
            parts.append(f"\n{emoji} {sev.upper()} ({len(items)})")
            for a in items[:5]:
                parts.append(f"  • {a.get('source', '?')}: {a.get('type', '?')}")
            if len(items) > 5:
                parts.append(f"  ... +{len(items) - 5} more")

    if not total:
        parts.append("\n✅ All quiet — no changes detected.")

    errors = digest.get("errors", [])
    if errors:
        parts.append(f"\n⚠️ Errors: {len(errors)}")

    return "\n".join(parts)


def cmd_watch(args: argparse.Namespace) -> None:
    """Smart add — accepts GitHub URLs, owner/repo, or contract addresses."""
    target = args.target

    # Check if it's an Ethereum address
    if ADDRESS_RE.match(target):
        chain = args.chain or "base"
        validate_chain(chain)
        # Reuse add-contract logic
        args.address = target
        args.chain = chain
        args.label = args.label
        args.watch = "upgrades,balance"
        args.threshold = args.threshold or 1.0
        cmd_add_contract(args)
        return

    # Otherwise treat as repo
    try:
        owner, repo = parse_repo_input(target)
        args.owner = owner
        args.repo = repo
        args.branches = "main"
        args.severity = "all"
        cmd_add_repo(args)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def cmd_init(_args: argparse.Namespace) -> None:
    """Initialize sentry data directory."""
    SENTRY_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCHLIST_PATH.exists():
        save_watchlist({"repos": [], "contracts": [], "wallets": []})
    if not STATE_PATH.exists():
        save_state({"last_poll": None, "poll_count": 0, "alerts_24h": [], "errors": []})
    os.chmod(SENTRY_DIR, 0o700)
    print(json.dumps({"ok": True, "message": f"Sentry initialized at {SENTRY_DIR}"}))

def cmd_add_repo(args: argparse.Namespace) -> None:
    """Add a GitHub repo to the watch list."""
    owner = validate_repo_name(args.owner)
    repo = validate_repo_name(args.repo)
    branches = validate_branches(args.branches.split(",")) if args.branches else ["main"]

    # Verify repo exists and get baseline
    repo_data = github_get(f"/repos/{owner}/{repo}")
    if not repo_data:
        print(json.dumps({"ok": False, "error": f"Repository {owner}/{repo} not found or inaccessible"}))
        sys.exit(1)

    commits = github_get(f"/repos/{owner}/{repo}/commits", {"per_page": 1})
    baseline_sha = commits[0]["sha"] if commits else None

    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        target_id = f"{owner}/{repo}"

        # Check for duplicates
        if any(r["id"] == target_id for r in wl["repos"]):
            print(json.dumps({"ok": False, "error": f"Already watching {target_id}"}))
            sys.exit(1)

        wl["repos"].append({
            "id": target_id,
            "owner": owner,
            "repo": repo,
            "branches": branches,
            "severity_filter": args.severity or "all",
            "last_checked_sha": baseline_sha,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        })
        save_watchlist(wl)

    print(json.dumps({"ok": True, "message": f"Now watching {target_id}", "baseline_sha": baseline_sha[:8] if baseline_sha else None}))

def cmd_add_contract(args: argparse.Namespace) -> None:
    """Add a contract to the watch list."""
    address = validate_address(args.address)
    chain = validate_chain(args.chain)
    watch_types = args.watch.split(",") if args.watch else ["upgrades", "balance"]

    rpc_url = CHAINS[chain]["rpc"]

    # Baseline
    code_hash = get_code_hash(rpc_url, address)
    impl_addr = get_impl_address(rpc_url, address)
    balance = get_eth_balance(rpc_url, address)

    if code_hash is None:
        print(json.dumps({"ok": False, "error": f"No contract found at {address} on {chain}"}))
        sys.exit(1)

    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        target_id = args.label or f"{address[:10]}...{address[-4:]}-{chain}"

        if any(c["id"] == target_id for c in wl["contracts"]):
            print(json.dumps({"ok": False, "error": f"Already watching {target_id}"}))
            sys.exit(1)

        wl["contracts"].append({
            "id": target_id,
            "label": args.label or target_id,
            "address": address,
            "chain": chain,
            "watch_type": watch_types,
            "last_code_hash": code_hash,
            "last_impl_address": impl_addr,
            "last_balance": balance,
            "threshold_eth": args.threshold or 1.0,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        })
        save_watchlist(wl)

    is_proxy = impl_addr is not None
    print(json.dumps({
        "ok": True,
        "message": f"Now watching {target_id}",
        "is_proxy": is_proxy,
        "impl_address": impl_addr,
        "code_hash": code_hash[:16] + "..." if code_hash else None,
        "balance_eth": f"{balance:.6f}" if balance else None,
    }))

def cmd_add_wallet(args: argparse.Namespace) -> None:
    """Add a wallet to the watch list."""
    address = validate_address(args.address)
    chain = validate_chain(args.chain)

    rpc_url = CHAINS[chain]["rpc"]
    balance = get_eth_balance(rpc_url, address)

    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        target_id = args.label or f"{address[:10]}...{address[-4:]}-{chain}"

        if any(w["id"] == target_id for w in wl["wallets"]):
            print(json.dumps({"ok": False, "error": f"Already watching {target_id}"}))
            sys.exit(1)

        wl["wallets"].append({
            "id": target_id,
            "label": args.label or target_id,
            "address": address,
            "chain": chain,
            "threshold_eth": args.threshold or 1.0,
            "last_balance": balance,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        })
        save_watchlist(wl)

    print(json.dumps({
        "ok": True,
        "message": f"Now watching wallet {target_id}",
        "balance_eth": f"{balance:.6f}" if balance else None,
    }))

def cmd_list(_args: argparse.Namespace) -> None:
    """List all watch targets."""
    wl = load_watchlist()
    summary = {
        "repos": [{"id": r["id"], "last_checked": r.get("last_checked_at")} for r in wl["repos"]],
        "contracts": [{"id": c["id"], "chain": c["chain"], "address": c["address"], "last_checked": c.get("last_checked_at")} for c in wl["contracts"]],
        "wallets": [{"id": w["id"], "chain": w["chain"], "address": w["address"], "last_checked": w.get("last_checked_at")} for w in wl["wallets"]],
        "total": len(wl["repos"]) + len(wl["contracts"]) + len(wl["wallets"]),
    }
    print(json.dumps(summary, indent=2))

def cmd_remove(args: argparse.Namespace) -> None:
    """Remove a watch target by ID."""
    target_id = args.id
    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        found = False
        for category in ["repos", "contracts", "wallets"]:
            before = len(wl[category])
            wl[category] = [item for item in wl[category] if item["id"] != target_id]
            if len(wl[category]) < before:
                found = True
                break
        if not found:
            print(json.dumps({"ok": False, "error": f"Target not found: {target_id}"}))
            sys.exit(1)
        save_watchlist(wl)
    print(json.dumps({"ok": True, "message": f"Removed {target_id}"}))

def cmd_poll(_args: argparse.Namespace) -> None:
    """Poll all watch targets for changes."""
    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        state = load_state()
        all_alerts: list[dict] = []
        errors: list[str] = []

        # Poll repos
        for repo_entry in wl["repos"]:
            try:
                alerts = poll_repo(repo_entry)
                all_alerts.extend(alerts)
            except Exception as e:
                error_msg = f"Error polling {repo_entry['id']}: {e}"
                log.error(error_msg)
                errors.append(error_msg)

        # Poll contracts
        for contract_entry in wl["contracts"]:
            try:
                alerts = poll_contract(contract_entry)
                all_alerts.extend(alerts)
            except Exception as e:
                error_msg = f"Error polling {contract_entry['id']}: {e}"
                log.error(error_msg)
                errors.append(error_msg)

        # Poll wallets
        for wallet_entry in wl["wallets"]:
            try:
                alerts = poll_wallet(wallet_entry)
                all_alerts.extend(alerts)
            except Exception as e:
                error_msg = f"Error polling {wallet_entry['id']}: {e}"
                log.error(error_msg)
                errors.append(error_msg)

        # Update state
        state["last_poll"] = datetime.now(timezone.utc).isoformat()
        state["poll_count"] = state.get("poll_count", 0) + 1
        state["errors"] = errors

        # Append to 24h alert history
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        state["alerts_24h"] = [a for a in state.get("alerts_24h", []) if a.get("time", "") > cutoff]
        for alert in all_alerts:
            alert["time"] = datetime.now(timezone.utc).isoformat()
            state["alerts_24h"].append(alert)

        save_watchlist(wl)
        save_state(state)

    # Output alerts
    output = {
        "alerts": [a for a in all_alerts if a.get("severity") != "noise"],
        "noise_count": sum(1 for a in all_alerts if a.get("severity") == "noise"),
        "errors": errors,
        "targets_checked": len(wl["repos"]) + len(wl["contracts"]) + len(wl["wallets"]),
    }

    if getattr(_args, "json", False):
        print(json.dumps(output, indent=2))
    else:
        print(format_poll_human(output))

def cmd_digest(_args: argparse.Namespace) -> None:
    """Generate a daily digest from the last 24h of alerts."""
    state = load_state()
    wl = load_watchlist()
    alerts_24h = state.get("alerts_24h", [])

    digest = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "repos_watched": len(wl["repos"]),
        "contracts_watched": len(wl["contracts"]),
        "wallets_watched": len(wl["wallets"]),
        "total_alerts": len(alerts_24h),
        "by_severity": {
            "critical": [a for a in alerts_24h if a.get("severity") == "critical"],
            "warning": [a for a in alerts_24h if a.get("severity") == "warning"],
            "info": [a for a in alerts_24h if a.get("severity") == "info"],
        },
        "by_source": {},
        "errors": state.get("errors", []),
    }

    for alert in alerts_24h:
        source = alert.get("source", "unknown")
        if source not in digest["by_source"]:
            digest["by_source"][source] = []
        digest["by_source"][source].append(alert)

    if getattr(_args, "json", False):
        print(json.dumps(digest, indent=2))
    else:
        print(format_digest_human(digest))

def cmd_health(_args: argparse.Namespace) -> None:
    """Health check — report status of all monitoring sources."""
    wl = load_watchlist()
    state = load_state()
    now = datetime.now(timezone.utc)

    # Check GitHub rate limit
    rate = github_rate_limit()

    # Check RPC health
    rpc_status = {}
    chains_in_use = set()
    for c in wl["contracts"]:
        chains_in_use.add(c["chain"])
    for w in wl["wallets"]:
        chains_in_use.add(w["chain"])

    for chain in chains_in_use:
        rpc_url = CHAINS[chain]["rpc"]
        block = get_block_number(rpc_url)
        rpc_status[chain] = {"ok": block is not None, "block": block}

    # Check staleness
    last_poll = state.get("last_poll")
    stale = False
    if last_poll:
        last_poll_dt = datetime.fromisoformat(last_poll.replace("Z", "+00:00"))
        minutes_since = (now - last_poll_dt).total_seconds() / 60
        stale = minutes_since > 15  # Stale if >15 min since last poll

    health = {
        "status": "degraded" if stale or state.get("errors") else "healthy",
        "last_poll": last_poll,
        "stale": stale,
        "poll_count": state.get("poll_count", 0),
        "targets": {
            "repos": len(wl["repos"]),
            "contracts": len(wl["contracts"]),
            "wallets": len(wl["wallets"]),
        },
        "github_rate_limit": {
            "remaining": rate.get("remaining"),
            "limit": rate.get("limit"),
            "resets_at": datetime.fromtimestamp(rate.get("reset", 0), tz=timezone.utc).isoformat() if rate.get("reset") else None,
        },
        "rpc_status": rpc_status,
        "recent_errors": state.get("errors", [])[:5],
        "alerts_24h": len(state.get("alerts_24h", [])),
    }
    print(json.dumps(health, indent=2))

# ─── CLI Parser ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="sentry", description="Hermes Sentry — Repo & Contract Monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize sentry data directory")

    # watch (smart add)
    w = sub.add_parser("watch", help="Add a repo (URL or owner/repo) or contract (0x address)")
    w.add_argument("target", help="GitHub URL, owner/repo, or 0x contract address")
    w.add_argument("--chain", default="base", help="Chain for contract addresses (default: base)")
    w.add_argument("--label", help="Human-readable label")
    w.add_argument("--threshold", type=float, default=1.0, help="Balance threshold in ETH")

    # add-repo
    ar = sub.add_parser("add-repo", help="Add a GitHub repo to watch")
    ar.add_argument("--owner", required=True, help="GitHub owner/org")
    ar.add_argument("--repo", required=True, help="Repository name")
    ar.add_argument("--branches", default="main", help="Comma-separated branches (default: main)")
    ar.add_argument("--severity", default="all", help="Severity filter: all, warning, critical")

    # add-contract
    ac = sub.add_parser("add-contract", help="Add an onchain contract to watch")
    ac.add_argument("--address", required=True, help="Contract address (0x...)")
    ac.add_argument("--chain", required=True, help="Chain: base, ethereum, arbitrum")
    ac.add_argument("--label", help="Human-readable label")
    ac.add_argument("--watch", default="upgrades,balance", help="What to watch: upgrades,balance")
    ac.add_argument("--threshold", type=float, default=1.0, help="Balance change threshold in ETH")

    # add-wallet
    aw = sub.add_parser("add-wallet", help="Add a wallet to watch")
    aw.add_argument("--address", required=True, help="Wallet address (0x...)")
    aw.add_argument("--chain", required=True, help="Chain: base, ethereum, arbitrum")
    aw.add_argument("--label", help="Human-readable label")
    aw.add_argument("--threshold", type=float, default=1.0, help="Balance change threshold in ETH")

    # list
    sub.add_parser("list", help="List all watch targets")

    # remove
    rm = sub.add_parser("remove", help="Remove a watch target")
    rm.add_argument("--id", required=True, help="Target ID to remove")

    # poll
    p = sub.add_parser("poll", help="Poll all targets for changes")
    p.add_argument("--json", action="store_true", help="Output raw JSON instead of readable text")

    # digest
    d = sub.add_parser("digest", help="Generate 24h digest")
    d.add_argument("--json", action="store_true", help="Output raw JSON instead of readable text")

    # health
    sub.add_parser("health", help="Health check")

    args = parser.parse_args()
    commands = {
        "init": cmd_init,
        "watch": cmd_watch,
        "add-repo": cmd_add_repo,
        "add-contract": cmd_add_contract,
        "add-wallet": cmd_add_wallet,
        "list": cmd_list,
        "remove": cmd_remove,
        "poll": cmd_poll,
        "digest": cmd_digest,
        "health": cmd_health,
    }
    commands[args.command](args)

if __name__ == "__main__":
    main()
