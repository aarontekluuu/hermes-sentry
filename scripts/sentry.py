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
    "optimism": {
        "rpc": "https://mainnet.optimism.io",
        "explorer": "https://optimistic.etherscan.io",
        "name": "Optimism",
    },
    "polygon": {
        "rpc": "https://polygon-rpc.com",
        "explorer": "https://polygonscan.com",
        "name": "Polygon",
    },
}

# ERC-1967 implementation slot
ERC1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# ─── ERC-20 Token Registry ───────────────────────────────────────────────────

# Common ERC-20 tokens by chain: address → (symbol, decimals)
ERC20_TOKENS: dict[str, dict[str, tuple[str, int]]] = {
    "ethereum": {
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
        "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
        "0x514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
        "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": ("UNI", 18),
        "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": ("AAVE", 18),
    },
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
        "0x4200000000000000000000000000000000000006": ("WETH", 18),
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": ("DAI", 18),
        "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": ("cbETH", 18),
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": ("USDbC", 6),
    },
    "arbitrum": {
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", 6),
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": ("WETH", 18),
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": ("WBTC", 8),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18),
    },
    "optimism": {
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": ("USDC", 6),
        "0x4200000000000000000000000000000000000006": ("WETH", 18),
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": ("USDT", 6),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18),
    },
    "polygon": {
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", 6),
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": ("WETH", 18),
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": ("USDT", 6),
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": ("DAI", 18),
    },
}

# Well-known event signatures
EVENT_SIGNATURES: dict[str, str] = {
    "Transfer": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "Approval": "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
    "OwnershipTransferred": "0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0",
    "Upgraded": "0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b",
    "AdminChanged": "0x7e644d79422f17c01e4894b5f4f588d331ebfa28653d42ae832dc59e38c9798f",
    "Paused": "0x62e78cea01bee320cd4e420270b5ea74000d11b0c9f74754ebdbfc544b05a258",
    "Unpaused": "0x5db9ee0a495bf2e6ff9c91a7834c1ba4fdd244a5e8aa4e537bd38aeae4b073aa",
    "RoleGranted": "0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d",
    "RoleRevoked": "0xf6391f5c32d9c69d2a47ea670b442974b53935d1edc7fd64eb21e047a839171b",
}

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
    if not name or len(name) > 100:
        raise ValueError(f"Invalid repo name: too long or empty")
    if not REPO_NAME_RE.match(name):
        raise ValueError(f"Invalid repo name: '{name}' contains disallowed characters")
    return name

def validate_address(addr: str) -> str:
    if not ADDRESS_RE.match(addr):
        raise ValueError(f"Invalid address format: '{addr}' (expected 0x + 40 hex chars)")
    return addr.lower()

def validate_chain(chain: str) -> str:
    if chain not in CHAINS:
        raise ValueError(f"Unknown chain: '{chain}'. Supported: {', '.join(CHAINS.keys())}")
    return chain

def validate_branches(branches: list[str]) -> list[str]:
    branch_re = re.compile(r"^[a-zA-Z0-9._/-]+$")
    for b in branches:
        if not branch_re.match(b) or len(b) > 100:
            raise ValueError(f"Invalid branch name: '{b}'")
    return branches

# ─── Atomic File I/O ──────────────────────────────────────────────────────────

def atomic_write_json(path: Path, data: Any) -> None:
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
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Failed to load {path}: {e}")
        return default if default is not None else {}

class FileLock:
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
    data = github_get("/rate_limit")
    if data and "rate" in data:
        return data["rate"]
    return {"remaining": -1, "limit": -1, "reset": 0}

# ─── Onchain Client ──────────────────────────────────────────────────────────

def rpc_call(rpc_url: str, method: str, params: list) -> Any:
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
    code = rpc_call(rpc_url, "eth_getCode", [address, "latest"])
    if not code or code == "0x":
        return None
    return hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()

def get_impl_address(rpc_url: str, address: str) -> Optional[str]:
    result = rpc_call(rpc_url, "eth_getStorageAt", [address, ERC1967_IMPL_SLOT, "latest"])
    if not result or result == "0x" + "0" * 64:
        return None
    return "0x" + result[-40:]

def get_eth_balance(rpc_url: str, address: str) -> Optional[float]:
    result = rpc_call(rpc_url, "eth_getBalance", [address, "latest"])
    if result is None:
        return None
    return int(result, 16) / 1e18

def get_block_number(rpc_url: str) -> Optional[int]:
    result = rpc_call(rpc_url, "eth_blockNumber", [])
    if result is None:
        return None
    return int(result, 16)

# ─── ERC-20 Token Functions ──────────────────────────────────────────────────

def get_erc20_balance(rpc_url: str, token_address: str, wallet_address: str) -> Optional[int]:
    """Get ERC-20 token balance (raw, not adjusted for decimals).
    Uses balanceOf(address) — function selector 0x70a08231
    """
    # balanceOf(address) selector = 0x70a08231
    # Pad wallet address to 32 bytes
    padded_addr = wallet_address[2:].lower().zfill(64)
    call_data = "0x70a08231" + padded_addr

    result = rpc_call(rpc_url, "eth_call", [
        {"to": token_address, "data": call_data},
        "latest"
    ])
    if result is None or result == "0x":
        return None
    try:
        return int(result, 16)
    except (ValueError, TypeError):
        return None

def format_token_balance(raw_balance: int, decimals: int) -> str:
    """Format raw token balance with proper decimal places."""
    if decimals == 0:
        return str(raw_balance)
    divisor = 10 ** decimals
    whole = raw_balance // divisor
    frac = raw_balance % divisor
    frac_str = str(frac).zfill(decimals)
    # Show up to 4 decimal places for readability
    display_decimals = min(decimals, 4)
    return f"{whole}.{frac_str[:display_decimals]}"

def get_all_token_balances(rpc_url: str, address: str, chain: str) -> dict[str, str]:
    """Get balances for all known ERC-20 tokens on a chain."""
    tokens = ERC20_TOKENS.get(chain, {})
    balances = {}
    for token_addr, (symbol, decimals) in tokens.items():
        raw = get_erc20_balance(rpc_url, token_addr, address)
        if raw is not None and raw > 0:
            balances[symbol] = format_token_balance(raw, decimals)
    return balances

# ─── Event Log Functions ─────────────────────────────────────────────────────

def get_event_logs(rpc_url: str, address: str, topics: list[str],
                   from_block: str = "latest", to_block: str = "latest",
                   block_range: int = 1000) -> list[dict]:
    """Fetch event logs for a contract address with given topic filters.
    Uses eth_getLogs. from_block defaults to (latest - block_range).
    """
    # Get current block for range calculation
    current_block = get_block_number(rpc_url)
    if current_block is None:
        return []

    if from_block == "latest":
        from_block_num = max(0, current_block - block_range)
        from_block = hex(from_block_num)
    if to_block == "latest":
        to_block = hex(current_block)

    filter_params = {
        "address": address,
        "fromBlock": from_block,
        "toBlock": to_block,
        "topics": [topics] if isinstance(topics, str) else topics,
    }

    result = rpc_call(rpc_url, "eth_getLogs", [filter_params])
    if result is None:
        return []
    return result if isinstance(result, list) else []

def decode_transfer_log(log_entry: dict, chain: str) -> Optional[dict]:
    """Decode a Transfer event log into readable format."""
    topics = log_entry.get("topics", [])
    if len(topics) < 3:
        return None

    from_addr = "0x" + topics[1][-40:]
    to_addr = "0x" + topics[2][-40:]
    raw_value = int(log_entry.get("data", "0x0"), 16) if log_entry.get("data") else 0

    contract_addr = log_entry.get("address", "").lower()
    tokens = ERC20_TOKENS.get(chain, {})
    token_info = tokens.get(contract_addr)

    if token_info:
        symbol, decimals = token_info
        value = format_token_balance(raw_value, decimals)
    else:
        symbol = contract_addr[:10] + "..."
        value = str(raw_value)

    return {
        "event": "Transfer",
        "from": from_addr,
        "to": to_addr,
        "value": value,
        "symbol": symbol,
        "contract": contract_addr,
        "tx_hash": log_entry.get("transactionHash", ""),
        "block": int(log_entry.get("blockNumber", "0x0"), 16) if log_entry.get("blockNumber") else 0,
    }

# ─── Significance Scoring ────────────────────────────────────────────────────

def score_commit_significance(commit: dict) -> int:
    """Score a commit's significance from 1-10 based on multiple signals."""
    score = 3  # baseline

    files = commit.get("files", [])
    file_count = len(files)
    additions = sum(f.get("additions", 0) for f in files)
    deletions = sum(f.get("deletions", 0) for f in files)
    total_changes = additions + deletions
    message = commit.get("commit", {}).get("message", "").lower()

    # Size signals
    if total_changes > 1000:
        score += 2
    elif total_changes > 200:
        score += 1
    elif total_changes < 10:
        score -= 1

    # File type signals
    has_critical = any(
        re.search(p, f.get("filename", ""), re.IGNORECASE)
        for f in files for p in CRITICAL_PATTERNS
    )
    if has_critical:
        score += 2

    # Message signals
    critical_keywords = ["security", "vulnerability", "cve", "exploit", "breaking", "critical", "urgent", "hotfix"]
    important_keywords = ["feat", "feature", "major", "release", "deploy", "migration", "upgrade"]
    noise_keywords = ["typo", "readme", "comment", "formatting", "lint", "bump", "chore", "docs"]

    if any(kw in message for kw in critical_keywords):
        score += 3
    elif any(kw in message for kw in important_keywords):
        score += 1
    if any(kw in message for kw in noise_keywords):
        score -= 1

    # Multi-file changes in different directories = more significant
    dirs = set(os.path.dirname(f.get("filename", "")) for f in files)
    if len(dirs) > 5:
        score += 1

    # Merge commits are slightly less significant
    if message.startswith("merge"):
        score -= 1

    return max(1, min(10, score))

def generate_commit_summary(commit: dict) -> str:
    """Generate a 1-2 sentence plain-English summary of what a commit does.
    Uses heuristics on files changed and commit message (no LLM needed).
    """
    message = commit.get("commit", {}).get("message", "").split("\n")[0]
    files = commit.get("files", [])
    additions = sum(f.get("additions", 0) for f in files)
    deletions = sum(f.get("deletions", 0) for f in files)
    filenames = [f.get("filename", "") for f in files]

    # Detect patterns
    dirs = set()
    extensions = set()
    for fn in filenames:
        d = os.path.dirname(fn)
        if d:
            dirs.add(d.split("/")[0])
        ext = os.path.splitext(fn)[1]
        if ext:
            extensions.add(ext)

    # Build summary based on patterns
    parts = []

    # What kind of change
    if any(fn.endswith(".sol") for fn in filenames):
        parts.append("Smart contract changes")
    elif any(fn.endswith((".rs", ".go")) for fn in filenames):
        parts.append("Core implementation changes")
    elif any("test" in fn.lower() for fn in filenames):
        if all("test" in fn.lower() for fn in filenames):
            parts.append("Test-only changes")
        else:
            parts.append("Code changes with tests")
    elif any(fn.endswith((".md", ".txt", ".mdx")) for fn in filenames):
        if all(fn.endswith((".md", ".txt", ".mdx")) for fn in filenames):
            parts.append("Documentation updates")

    if not parts:
        if additions > deletions * 3:
            parts.append("Adds new code")
        elif deletions > additions * 3:
            parts.append("Removes/cleans up code")
        else:
            parts.append("Modifies existing code")

    # Scope
    if len(dirs) == 1:
        parts.append(f"in {list(dirs)[0]}/")
    elif len(dirs) > 3:
        parts.append(f"across {len(dirs)} directories")

    # Scale
    if len(files) == 1:
        parts.append(f"({filenames[0].split('/')[-1]})")
    else:
        parts.append(f"({len(files)} files, +{additions}/-{deletions})")

    return " ".join(parts)

def score_batch_significance(commits: list[dict]) -> int:
    """Score significance of a batch of commits."""
    if not commits:
        return 1
    scores = []
    for c in commits:
        scores.append(score_commit_significance(c))
    # Batch significance = max individual score + bonus for volume
    max_score = max(scores)
    volume_bonus = 1 if len(commits) > 10 else 0
    return min(10, max_score + volume_bonus)

# ─── Severity Classification ─────────────────────────────────────────────────

def classify_file(filename: str) -> str:
    for pattern in CRITICAL_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return "warning"
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return "noise"
    for pattern in TEST_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return "info"
    return "info"

def classify_commit(commit: dict) -> str:
    files = commit.get("files", [])
    if not files:
        return "info"
    severities = [classify_file(f.get("filename", "")) for f in files]
    if "warning" in severities:
        return "warning"
    if all(s == "noise" for s in severities):
        return "noise"
    return "info"

def is_bot_commit(commit: dict) -> bool:
    author = commit.get("author")
    if author and author.get("login") in BOT_AUTHORS:
        return True
    commit_data = commit.get("commit", {})
    author_name = commit_data.get("author", {}).get("name", "")
    return author_name in BOT_AUTHORS

# ─── GitHub Polling ───────────────────────────────────────────────────────────

def poll_repo(repo_entry: dict) -> list[dict]:
    owner = repo_entry["owner"]
    repo = repo_entry["repo"]
    last_sha = repo_entry.get("last_checked_sha")
    last_at = repo_entry.get("last_checked_at")

    alerts = []
    params: dict[str, Any] = {"per_page": MAX_COMMITS_PER_POLL}
    if last_at:
        params["since"] = last_at

    commits = github_get(f"/repos/{owner}/{repo}/commits", params=params)
    if commits is None:
        return [{"severity": "warning", "type": "error", "source": f"{owner}/{repo}",
                 "message": f"Failed to fetch commits for {owner}/{repo}. GitHub may be down or rate-limited."}]

    if not commits:
        return []

    new_commits = []
    for c in commits:
        if c["sha"] == last_sha:
            break
        if is_bot_commit(c):
            continue
        new_commits.append(c)

    if not new_commits:
        repo_entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        return []

    # Fetch details
    detailed_commits = []
    total_diff_lines = 0
    for c in new_commits[:MAX_COMMITS_PER_POLL]:
        detail = github_get(f"/repos/{owner}/{repo}/commits/{c['sha']}")
        if detail:
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

    if len(detailed_commits) >= 3:
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

        significance = score_batch_significance(detailed_commits)

        alerts.append({
            "severity": max_severity,
            "type": "commit_batch",
            "source": f"{owner}/{repo}",
            "count": len(detailed_commits),
            "authors": list(authors),
            "total_files": len(set(all_files)),
            "additions": total_additions,
            "deletions": total_deletions,
            "significance": significance,
            "first_time": detailed_commits[-1].get("commit", {}).get("author", {}).get("date"),
            "last_time": detailed_commits[0].get("commit", {}).get("author", {}).get("date"),
            "commits": [
                {
                    "sha": dc["sha"][:8],
                    "message": dc.get("commit", {}).get("message", "").split("\n")[0][:120],
                    "author": dc.get("commit", {}).get("author", {}).get("name", "unknown"),
                    "files": [f.get("filename", "") for f in dc.get("files", [])],
                    "severity": classify_commit(dc),
                    "significance": score_commit_significance(dc),
                    "summary": generate_commit_summary(dc),
                }
                for dc in detailed_commits
            ],
            "url": f"https://github.com/{owner}/{repo}/commits",
        })
    else:
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
                "significance": score_commit_significance(dc),
                "summary": generate_commit_summary(dc),
                "files": [f.get("filename", "") for f in files[:10]],
                "patches": {
                    f.get("filename", ""): f.get("patch", "")
                    for f in files[:5]
                    if f.get("patch")
                },
                "url": dc.get("html_url", ""),
            })

    repo_entry["last_checked_sha"] = new_commits[0]["sha"]
    repo_entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return alerts

# ─── Onchain Polling ──────────────────────────────────────────────────────────

def poll_contract(entry: dict) -> list[dict]:
    chain = entry["chain"]
    rpc_url = CHAINS[chain]["rpc"]
    address = entry["address"]
    label = entry.get("label", address[:10])
    explorer = CHAINS[chain]["explorer"]
    alerts = []

    watch_types = entry.get("watch_type", ["upgrades", "balance"])

    if "upgrades" in watch_types:
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
                "significance": 10,
                "explorer_url": f"{explorer}/address/{address}",
            })

        if new_impl is not None:
            entry["last_impl_address"] = new_impl

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
                "significance": 10,
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
                sig = 8 if abs(delta) >= threshold * 10 else 5
                alerts.append({
                    "severity": "warning" if abs(delta) < threshold * 10 else "critical",
                    "type": "balance_change",
                    "source": label,
                    "chain": chain,
                    "address": address,
                    "old_balance": f"{old_balance:.6f}",
                    "new_balance": f"{new_balance:.6f}",
                    "delta": f"{delta:+.6f}",
                    "significance": sig,
                    "explorer_url": f"{explorer}/address/{address}",
                })

        if new_balance is not None:
            entry["last_balance"] = new_balance

    # ERC-20 token balance monitoring
    if "tokens" in watch_types:
        new_token_balances = get_all_token_balances(rpc_url, address, chain)
        old_token_balances = entry.get("last_token_balances", {})

        for symbol, new_bal_str in new_token_balances.items():
            old_bal_str = old_token_balances.get(symbol)
            if old_bal_str is not None:
                try:
                    new_val = float(new_bal_str.replace(",", ""))
                    old_val = float(old_bal_str.replace(",", ""))
                    delta = new_val - old_val
                    # Skip dust amounts (both old and new < 0.01)
                    if abs(old_val) < 0.01 and abs(new_val) < 0.01:
                        continue
                    pct_change = abs(delta / old_val * 100) if old_val > 0 else (100 if new_val > 0.01 else 0)
                    token_threshold = entry.get("token_threshold_pct", 5.0)
                    if pct_change >= token_threshold:
                        sig = 7 if pct_change > 20 else 4
                        alerts.append({
                            "severity": "warning" if pct_change < 50 else "critical",
                            "type": "token_balance_change",
                            "source": label,
                            "chain": chain,
                            "address": address,
                            "token": symbol,
                            "old_balance": old_bal_str,
                            "new_balance": new_bal_str,
                            "change_pct": f"{pct_change:+.1f}%",
                            "significance": sig,
                            "explorer_url": f"{explorer}/address/{address}",
                        })
                except (ValueError, ZeroDivisionError):
                    pass

        entry["last_token_balances"] = new_token_balances

    # Event log monitoring
    if "events" in watch_types:
        watched_events = entry.get("watched_events", ["Transfer"])
        last_event_block = entry.get("last_event_block", 0)

        for event_name in watched_events:
            topic = EVENT_SIGNATURES.get(event_name)
            if not topic:
                continue

            current_block = get_block_number(rpc_url)
            if current_block is None:
                continue

            from_block = max(last_event_block + 1, current_block - 5000)
            logs = get_event_logs(rpc_url, address, [topic],
                                 from_block=hex(from_block),
                                 to_block=hex(current_block))

            if logs:
                decoded_logs = []
                if event_name == "Transfer":
                    for log_entry in logs[:10]:  # Cap at 10
                        decoded = decode_transfer_log(log_entry, chain)
                        if decoded:
                            decoded_logs.append(decoded)

                if decoded_logs:
                    sig = 6 if len(decoded_logs) < 5 else 8
                    alerts.append({
                        "severity": "warning",
                        "type": "event_logs",
                        "source": label,
                        "chain": chain,
                        "address": address,
                        "event_name": event_name,
                        "count": len(logs),
                        "recent_events": decoded_logs[:5],
                        "significance": sig,
                        "explorer_url": f"{explorer}/address/{address}",
                    })
                elif logs:
                    alerts.append({
                        "severity": "info",
                        "type": "event_logs",
                        "source": label,
                        "chain": chain,
                        "address": address,
                        "event_name": event_name,
                        "count": len(logs),
                        "significance": 3,
                        "explorer_url": f"{explorer}/address/{address}",
                    })

                entry["last_event_block"] = current_block

    entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return alerts

def poll_wallet(entry: dict) -> list[dict]:
    chain = entry["chain"]
    rpc_url = CHAINS[chain]["rpc"]
    address = entry["address"]
    label = entry.get("label", address[:10])
    explorer = CHAINS[chain]["explorer"]
    threshold = entry.get("threshold_eth", 1.0)

    alerts = []

    # ETH balance
    new_balance = get_eth_balance(rpc_url, address)
    old_balance = entry.get("last_balance")

    if old_balance is not None and new_balance is not None:
        delta = new_balance - old_balance
        if abs(delta) >= threshold:
            sev = "critical" if abs(delta) >= threshold * 10 else "warning"
            sig = 8 if sev == "critical" else 5
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
                "significance": sig,
                "explorer_url": f"{explorer}/address/{address}",
            })

    if new_balance is not None:
        entry["last_balance"] = new_balance

    # ERC-20 token tracking for wallets
    if entry.get("track_tokens", False):
        new_token_balances = get_all_token_balances(rpc_url, address, chain)
        old_token_balances = entry.get("last_token_balances", {})

        for symbol, new_bal_str in new_token_balances.items():
            old_bal_str = old_token_balances.get(symbol)
            if old_bal_str is not None:
                try:
                    new_val = float(new_bal_str)
                    old_val = float(old_bal_str)
                    delta = new_val - old_val
                    if abs(old_val) < 0.01 and abs(new_val) < 0.01:
                        continue
                    pct_change = abs(delta / old_val * 100) if old_val > 0 else (100 if new_val > 0.01 else 0)
                    if pct_change >= entry.get("token_threshold_pct", 5.0):
                        alerts.append({
                            "severity": "warning",
                            "type": "token_balance_change",
                            "source": label,
                            "chain": chain,
                            "address": address,
                            "token": symbol,
                            "old_balance": old_bal_str,
                            "new_balance": new_bal_str,
                            "change_pct": f"{pct_change:+.1f}%",
                            "significance": 5,
                            "explorer_url": f"{explorer}/address/{address}",
                        })
                except (ValueError, ZeroDivisionError):
                    pass

        entry["last_token_balances"] = new_token_balances

    entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return alerts

# ─── Formatting ───────────────────────────────────────────────────────────────

SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🟢", "noise": "⚪"}

SIG_BAR = {
    1: "░░░░░░░░░░",
    2: "█░░░░░░░░░",
    3: "██░░░░░░░░",
    4: "███░░░░░░░",
    5: "████░░░░░░",
    6: "█████░░░░░",
    7: "██████░░░░",
    8: "███████░░░",
    9: "████████░░",
    10: "██████████",
}

def format_alert_human(alert: dict) -> str:
    sev = alert.get("severity", "info")
    emoji = SEVERITY_EMOJI.get(sev, "⚪")
    alert_type = alert.get("type", "")
    source = alert.get("source", "unknown")
    sig = alert.get("significance", 5)
    sig_bar = SIG_BAR.get(sig, SIG_BAR[5])

    if alert_type == "commit":
        files = alert.get("files", [])
        file_list = "\n".join(f"  • {f}" for f in files[:5])
        if len(files) > 5:
            file_list += f"\n  ... +{len(files) - 5} more"
        summary = alert.get("summary", "")
        summary_line = f"\n💡 {summary}" if summary else ""
        return (
            f"{emoji} {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{alert.get('message', '')}{summary_line}\n\n"
            f"Significance: {sig_bar} {sig}/10\n"
            f"Files: {alert.get('file_count', 0)} changed "
            f"(+{alert.get('additions', 0)} -{alert.get('deletions', 0)})\n"
            f"{file_list}\n\n"
            f"By {alert.get('author', '?')} • {alert.get('sha', '?')}\n"
            f"{alert.get('url', '')}"
        )

    elif alert_type == "commit_batch":
        commits_preview = "\n".join(
            f"  {SEVERITY_EMOJI.get(c.get('severity', 'info'), '⚪')} {c.get('sha', '?')} [{c.get('significance', '?')}/10] — {c.get('message', '')}"
            for c in alert.get("commits", [])[:6]
        )
        remaining = len(alert.get("commits", [])) - 6
        if remaining > 0:
            commits_preview += f"\n  ... +{remaining} more"

        # Add top summary for most significant commit
        top_commit = max(alert.get("commits", [{}]), key=lambda c: c.get("significance", 0))
        top_summary = f"\n🔑 Top change: {top_commit.get('summary', 'N/A')}" if top_commit.get("summary") else ""

        return (
            f"{emoji} {source} — {alert.get('count', 0)} new commits\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Significance: {sig_bar} {sig}/10{top_summary}\n\n"
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
            f"Significance: {sig_bar} {sig}/10\n"
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
            f"Significance: {sig_bar} {sig}/10\n"
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
            f"Significance: {sig_bar} {sig}/10\n"
            f"Chain: {alert.get('chain', '?')}\n"
            f"Address: {alert.get('address', '?')}\n\n"
            f"{alert.get('old_balance', '?')} → {alert.get('new_balance', '?')} ETH\n"
            f"Change: {delta} ETH\n\n"
            f"{alert.get('explorer_url', '')}"
        )

    elif alert_type == "token_balance_change":
        token = alert.get("token", "?")
        return (
            f"{emoji} 💰 {source} — {token}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Significance: {sig_bar} {sig}/10\n"
            f"Chain: {alert.get('chain', '?')}\n"
            f"Address: {alert.get('address', '?')}\n\n"
            f"{alert.get('old_balance', '?')} → {alert.get('new_balance', '?')} {token}\n"
            f"Change: {alert.get('change_pct', '?')}\n\n"
            f"{alert.get('explorer_url', '')}"
        )

    elif alert_type == "event_logs":
        event_name = alert.get("event_name", "?")
        count = alert.get("count", 0)
        events_preview = ""
        for ev in alert.get("recent_events", [])[:3]:
            if ev.get("event") == "Transfer":
                events_preview += f"  • {ev.get('value', '?')} {ev.get('symbol', '?')} from {ev['from'][:10]}... → {ev['to'][:10]}...\n"
            else:
                events_preview += f"  • {json.dumps(ev)}\n"
        return (
            f"{emoji} 📜 {source} — {event_name} events\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Significance: {sig_bar} {sig}/10\n"
            f"Chain: {alert.get('chain', '?')}\n"
            f"Address: {alert.get('address', '?')}\n\n"
            f"{count} events detected:\n{events_preview}\n"
            f"{alert.get('explorer_url', '')}"
        )

    elif alert_type == "error":
        return f"⚠️ SENTRY ERROR: {source}\n{alert.get('message', '')}"

    else:
        return f"{emoji} {source}: {json.dumps(alert, indent=2)}"


def format_poll_human(output: dict) -> str:
    alerts = output.get("alerts", [])
    errors = output.get("errors", [])
    noise = output.get("noise_count", 0)
    targets = output.get("targets_checked", 0)

    if not alerts and not errors:
        return f"✅ Sentry: {targets} targets checked — all clear." + (f" ({noise} noise filtered)" if noise else "")

    parts = []
    # Sort alerts by significance (highest first)
    sorted_alerts = sorted(alerts, key=lambda a: a.get("significance", 0), reverse=True)
    for alert in sorted_alerts:
        parts.append(format_alert_human(alert))

    if errors:
        parts.append("⚠️ Errors:\n" + "\n".join(f"  • {e}" for e in errors))

    header = f"🔔 Sentry: {len(alerts)} alert{'s' if len(alerts) != 1 else ''} from {targets} targets"
    if noise:
        header += f" ({noise} noise filtered)"

    return header + "\n\n" + "\n\n".join(parts)


def format_digest_human(digest: dict) -> str:
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

    # Trends & Patterns
    trends = digest.get("trends", {})
    if trends:
        parts.append(f"\n📈 TRENDS & PATTERNS")
        if trends.get("most_active_source"):
            parts.append(f"  🏆 Most active: {trends['most_active_source']} ({trends.get('most_active_count', 0)} alerts)")
        if trends.get("avg_significance") is not None:
            parts.append(f"  📊 Avg significance: {trends['avg_significance']:.1f}/10")
        if trends.get("critical_count", 0) > 0:
            parts.append(f"  🚨 Critical alerts: {trends['critical_count']}")
        if trends.get("unique_authors"):
            parts.append(f"  👥 Active contributors: {', '.join(list(trends['unique_authors'])[:5])}")
        if trends.get("total_file_changes", 0) > 0:
            parts.append(f"  📝 Total files changed: {trends['total_file_changes']}")
        if trends.get("anomalies"):
            parts.append(f"\n  ⚠️ ANOMALIES:")
            for a in trends["anomalies"]:
                parts.append(f"    • {a}")

    for sev in ["critical", "warning", "info"]:
        items = digest.get("by_severity", {}).get(sev, [])
        if items:
            emoji = SEVERITY_EMOJI[sev]
            parts.append(f"\n{emoji} {sev.upper()} ({len(items)})")
            for a in items[:5]:
                sig = a.get("significance", "?")
                parts.append(f"  • [{sig}/10] {a.get('source', '?')}: {a.get('type', '?')}")
            if len(items) > 5:
                parts.append(f"  ... +{len(items) - 5} more")

    if not total:
        parts.append("\n✅ All quiet — no changes detected.")

    errors = digest.get("errors", [])
    if errors:
        parts.append(f"\n⚠️ Errors: {len(errors)}")

    return "\n".join(parts)


# ─── Commands ─────────────────────────────────────────────────────────────────

def parse_repo_input(text: str) -> tuple[str, str]:
    text = text.strip().rstrip("/")
    m = GITHUB_URL_RE.match(text)
    if m:
        return validate_repo_name(m.group(1)), validate_repo_name(m.group(2))
    m = OWNER_REPO_RE.match(text)
    if m:
        return validate_repo_name(m.group(1)), validate_repo_name(m.group(2))
    raise ValueError(f"Can't parse repo: '{text}'. Use 'owner/repo' or a GitHub URL.")


def cmd_watch(args: argparse.Namespace) -> None:
    """Smart add — accepts GitHub URLs, owner/repo, or contract addresses."""
    target = args.target

    # Check if it's an Ethereum address
    if ADDRESS_RE.match(target):
        chain = args.chain or "base"
        validate_chain(chain)
        args.address = target
        args.chain = chain
        args.label = args.label
        args.watch = "upgrades,balance,tokens"
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
    SENTRY_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCHLIST_PATH.exists():
        save_watchlist({"repos": [], "contracts": [], "wallets": []})
    if not STATE_PATH.exists():
        save_state({"last_poll": None, "poll_count": 0, "alerts_24h": [], "errors": []})
    os.chmod(SENTRY_DIR, 0o700)
    print(json.dumps({"ok": True, "message": f"Sentry initialized at {SENTRY_DIR}"}))

def cmd_add_repo(args: argparse.Namespace) -> None:
    owner = validate_repo_name(args.owner)
    repo = validate_repo_name(args.repo)
    branches = validate_branches(args.branches.split(",")) if args.branches else ["main"]

    repo_data = github_get(f"/repos/{owner}/{repo}")
    if not repo_data:
        print(json.dumps({"ok": False, "error": f"Repository {owner}/{repo} not found or inaccessible"}))
        sys.exit(1)

    commits = github_get(f"/repos/{owner}/{repo}/commits", {"per_page": 1})
    baseline_sha = commits[0]["sha"] if commits else None

    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        target_id = f"{owner}/{repo}"

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
    address = validate_address(args.address)
    chain = validate_chain(args.chain)
    watch_types = args.watch.split(",") if args.watch else ["upgrades", "balance", "tokens"]

    rpc_url = CHAINS[chain]["rpc"]

    code_hash = get_code_hash(rpc_url, address)
    impl_addr = get_impl_address(rpc_url, address)
    balance = get_eth_balance(rpc_url, address)
    token_balances = get_all_token_balances(rpc_url, address, chain) if "tokens" in watch_types else {}

    if code_hash is None:
        print(json.dumps({"ok": False, "error": f"No contract found at {address} on {chain}"}))
        sys.exit(1)

    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        target_id = args.label or f"{address[:10]}...{address[-4:]}-{chain}"

        if any(c["id"] == target_id for c in wl["contracts"]):
            print(json.dumps({"ok": False, "error": f"Already watching {target_id}"}))
            sys.exit(1)

        entry = {
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
        }

        if "tokens" in watch_types:
            entry["last_token_balances"] = token_balances
            entry["token_threshold_pct"] = 5.0

        if "events" in watch_types:
            entry["watched_events"] = getattr(args, "events", "Transfer").split(",")
            entry["last_event_block"] = get_block_number(rpc_url) or 0

        wl["contracts"].append(entry)
        save_watchlist(wl)

    is_proxy = impl_addr is not None
    result = {
        "ok": True,
        "message": f"Now watching {target_id}",
        "is_proxy": is_proxy,
        "impl_address": impl_addr,
        "code_hash": code_hash[:16] + "..." if code_hash else None,
        "balance_eth": f"{balance:.6f}" if balance else None,
    }
    if token_balances:
        result["token_balances"] = token_balances
    print(json.dumps(result))

def cmd_add_wallet(args: argparse.Namespace) -> None:
    address = validate_address(args.address)
    chain = validate_chain(args.chain)

    rpc_url = CHAINS[chain]["rpc"]
    balance = get_eth_balance(rpc_url, address)
    track_tokens = getattr(args, "tokens", False)
    token_balances = get_all_token_balances(rpc_url, address, chain) if track_tokens else {}

    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        target_id = args.label or f"{address[:10]}...{address[-4:]}-{chain}"

        if any(w["id"] == target_id for w in wl["wallets"]):
            print(json.dumps({"ok": False, "error": f"Already watching {target_id}"}))
            sys.exit(1)

        entry = {
            "id": target_id,
            "label": args.label or target_id,
            "address": address,
            "chain": chain,
            "threshold_eth": args.threshold or 1.0,
            "last_balance": balance,
            "track_tokens": track_tokens,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if track_tokens:
            entry["last_token_balances"] = token_balances
            entry["token_threshold_pct"] = 5.0

        wl["wallets"].append(entry)
        save_watchlist(wl)

    result = {
        "ok": True,
        "message": f"Now watching wallet {target_id}",
        "balance_eth": f"{balance:.6f}" if balance else None,
    }
    if token_balances:
        result["token_balances"] = token_balances
    print(json.dumps(result))


def cmd_watch_multi(args: argparse.Namespace) -> None:
    """Watch the same address across multiple chains."""
    address = validate_address(args.address)
    chains = [validate_chain(c.strip()) for c in args.chains.split(",")]
    label_base = args.label or f"{address[:10]}...{address[-4:]}"
    watch_types = args.watch.split(",") if args.watch else ["upgrades", "balance", "tokens"]

    results = []
    for chain in chains:
        rpc_url = CHAINS[chain]["rpc"]
        code_hash = get_code_hash(rpc_url, address)

        if code_hash is None:
            # Might be a wallet, not a contract
            balance = get_eth_balance(rpc_url, address)
            if balance is not None:
                # Add as wallet instead
                with FileLock(LOCK_PATH):
                    wl = load_watchlist()
                    target_id = f"{label_base}-{chain}"
                    if not any(w["id"] == target_id for w in wl["wallets"]):
                        entry = {
                            "id": target_id,
                            "label": f"{label_base} ({CHAINS[chain]['name']})",
                            "address": address,
                            "chain": chain,
                            "threshold_eth": args.threshold or 1.0,
                            "last_balance": balance,
                            "track_tokens": "tokens" in watch_types,
                            "last_checked_at": datetime.now(timezone.utc).isoformat(),
                        }
                        if "tokens" in watch_types:
                            entry["last_token_balances"] = get_all_token_balances(rpc_url, address, chain)
                            entry["token_threshold_pct"] = 5.0
                        wl["wallets"].append(entry)
                        save_watchlist(wl)
                        results.append({"chain": chain, "type": "wallet", "balance": f"{balance:.6f}"})
                    else:
                        results.append({"chain": chain, "type": "wallet", "status": "already watching"})
            else:
                results.append({"chain": chain, "error": "No contract or balance found"})
            continue

        impl_addr = get_impl_address(rpc_url, address)
        balance = get_eth_balance(rpc_url, address)
        token_balances = get_all_token_balances(rpc_url, address, chain) if "tokens" in watch_types else {}

        with FileLock(LOCK_PATH):
            wl = load_watchlist()
            target_id = f"{label_base}-{chain}"
            if not any(c["id"] == target_id for c in wl["contracts"]):
                entry = {
                    "id": target_id,
                    "label": f"{label_base} ({CHAINS[chain]['name']})",
                    "address": address,
                    "chain": chain,
                    "watch_type": watch_types,
                    "last_code_hash": code_hash,
                    "last_impl_address": impl_addr,
                    "last_balance": balance,
                    "threshold_eth": args.threshold or 1.0,
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                }
                if "tokens" in watch_types:
                    entry["last_token_balances"] = token_balances
                    entry["token_threshold_pct"] = 5.0
                if "events" in watch_types:
                    entry["watched_events"] = getattr(args, "events", "Transfer").split(",")
                    entry["last_event_block"] = get_block_number(rpc_url) or 0
                wl["contracts"].append(entry)
                save_watchlist(wl)
                results.append({
                    "chain": chain, "type": "contract",
                    "is_proxy": impl_addr is not None,
                    "balance": f"{balance:.6f}" if balance else None,
                    "tokens": token_balances if token_balances else None,
                })
            else:
                results.append({"chain": chain, "type": "contract", "status": "already watching"})

    print(json.dumps({"ok": True, "message": f"Multi-chain watch for {address}", "results": results}, indent=2))


def cmd_list(_args: argparse.Namespace) -> None:
    wl = load_watchlist()
    summary = {
        "repos": [{"id": r["id"], "last_checked": r.get("last_checked_at")} for r in wl["repos"]],
        "contracts": [
            {
                "id": c["id"], "chain": c["chain"], "address": c["address"],
                "watch_types": c.get("watch_type", []),
                "token_balances": c.get("last_token_balances", {}),
                "last_checked": c.get("last_checked_at"),
            }
            for c in wl["contracts"]
        ],
        "wallets": [
            {
                "id": w["id"], "chain": w["chain"], "address": w["address"],
                "track_tokens": w.get("track_tokens", False),
                "token_balances": w.get("last_token_balances", {}),
                "last_checked": w.get("last_checked_at"),
            }
            for w in wl["wallets"]
        ],
        "total": len(wl["repos"]) + len(wl["contracts"]) + len(wl["wallets"]),
    }
    print(json.dumps(summary, indent=2))

def cmd_remove(args: argparse.Namespace) -> None:
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
    with FileLock(LOCK_PATH):
        wl = load_watchlist()
        state = load_state()
        all_alerts: list[dict] = []
        errors: list[str] = []

        for repo_entry in wl["repos"]:
            try:
                alerts = poll_repo(repo_entry)
                all_alerts.extend(alerts)
            except Exception as e:
                error_msg = f"Error polling {repo_entry['id']}: {e}"
                log.error(error_msg)
                errors.append(error_msg)

        for contract_entry in wl["contracts"]:
            try:
                alerts = poll_contract(contract_entry)
                all_alerts.extend(alerts)
            except Exception as e:
                error_msg = f"Error polling {contract_entry['id']}: {e}"
                log.error(error_msg)
                errors.append(error_msg)

        for wallet_entry in wl["wallets"]:
            try:
                alerts = poll_wallet(wallet_entry)
                all_alerts.extend(alerts)
            except Exception as e:
                error_msg = f"Error polling {wallet_entry['id']}: {e}"
                log.error(error_msg)
                errors.append(error_msg)

        state["last_poll"] = datetime.now(timezone.utc).isoformat()
        state["poll_count"] = state.get("poll_count", 0) + 1
        state["errors"] = errors

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        state["alerts_24h"] = [a for a in state.get("alerts_24h", []) if a.get("time", "") > cutoff]
        for alert in all_alerts:
            alert["time"] = datetime.now(timezone.utc).isoformat()
            state["alerts_24h"].append(alert)

        save_watchlist(wl)
        save_state(state)

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
    state = load_state()
    wl = load_watchlist()
    alerts_24h = state.get("alerts_24h", [])

    # Compute trends & patterns
    trends = {}
    if alerts_24h:
        # Most active source
        source_counts: dict[str, int] = {}
        unique_authors: set[str] = set()
        total_file_changes = 0
        significances = []
        anomalies = []

        for a in alerts_24h:
            source = a.get("source", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

            sig = a.get("significance")
            if sig is not None:
                significances.append(sig)

            # Collect authors from commits
            if a.get("type") == "commit_batch":
                for author in a.get("authors", []):
                    unique_authors.add(author)
                total_file_changes += a.get("total_files", 0)
            elif a.get("type") == "commit":
                unique_authors.add(a.get("author", "unknown"))
                total_file_changes += a.get("file_count", 0)

        if source_counts:
            most_active = max(source_counts, key=source_counts.get)
            trends["most_active_source"] = most_active
            trends["most_active_count"] = source_counts[most_active]

        if significances:
            trends["avg_significance"] = sum(significances) / len(significances)

        trends["critical_count"] = sum(1 for a in alerts_24h if a.get("severity") == "critical")
        trends["unique_authors"] = list(unique_authors)[:10]
        trends["total_file_changes"] = total_file_changes

        # Detect anomalies
        if any(a.get("type") == "contract_upgrade" for a in alerts_24h):
            anomalies.append("🔴 Contract upgrade detected — review immediately")
        if any(a.get("type") == "bytecode_change" for a in alerts_24h):
            anomalies.append("🔴 Bytecode change detected — potential security event")

        high_sig = [a for a in alerts_24h if a.get("significance", 0) >= 8]
        if len(high_sig) > 3:
            anomalies.append(f"⚡ Unusually high activity: {len(high_sig)} high-significance events")

        critical_balance = [a for a in alerts_24h if a.get("type") in ("balance_change", "wallet_movement", "token_balance_change") and a.get("severity") == "critical"]
        if critical_balance:
            anomalies.append(f"💸 Large balance movements: {len(critical_balance)} critical treasury/wallet changes")

        trends["anomalies"] = anomalies

    digest = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "repos_watched": len(wl["repos"]),
        "contracts_watched": len(wl["contracts"]),
        "wallets_watched": len(wl["wallets"]),
        "total_alerts": len(alerts_24h),
        "trends": trends,
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
    wl = load_watchlist()
    state = load_state()
    now = datetime.now(timezone.utc)

    rate = github_rate_limit()

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

    last_poll = state.get("last_poll")
    stale = False
    if last_poll:
        last_poll_dt = datetime.fromisoformat(last_poll.replace("Z", "+00:00"))
        minutes_since = (now - last_poll_dt).total_seconds() / 60
        stale = minutes_since > 15

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
        "supported_chains": list(CHAINS.keys()),
        "supported_tokens": {
            chain: [info[0] for info in tokens.values()]
            for chain, tokens in ERC20_TOKENS.items()
        },
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

    # watch-multi (same address, multiple chains)
    wm = sub.add_parser("watch-multi", help="Watch same address across multiple chains")
    wm.add_argument("--address", required=True, help="0x address to watch")
    wm.add_argument("--chains", required=True, help="Comma-separated chains (e.g., base,ethereum,arbitrum)")
    wm.add_argument("--label", help="Human-readable label")
    wm.add_argument("--watch", default="upgrades,balance,tokens", help="What to watch")
    wm.add_argument("--threshold", type=float, default=1.0, help="Balance threshold in ETH")
    wm.add_argument("--events", default="Transfer", help="Comma-separated event names to watch")

    # add-repo
    ar = sub.add_parser("add-repo", help="Add a GitHub repo to watch")
    ar.add_argument("--owner", required=True, help="GitHub owner/org")
    ar.add_argument("--repo", required=True, help="Repository name")
    ar.add_argument("--branches", default="main", help="Comma-separated branches (default: main)")
    ar.add_argument("--severity", default="all", help="Severity filter: all, warning, critical")

    # add-contract
    ac = sub.add_parser("add-contract", help="Add an onchain contract to watch")
    ac.add_argument("--address", required=True, help="Contract address (0x...)")
    ac.add_argument("--chain", required=True, help="Chain: base, ethereum, arbitrum, optimism, polygon")
    ac.add_argument("--label", help="Human-readable label")
    ac.add_argument("--watch", default="upgrades,balance,tokens", help="What to watch: upgrades,balance,tokens,events")
    ac.add_argument("--threshold", type=float, default=1.0, help="Balance change threshold in ETH")
    ac.add_argument("--events", default="Transfer", help="Comma-separated event names to watch (with events watch type)")

    # add-wallet
    aw = sub.add_parser("add-wallet", help="Add a wallet to watch")
    aw.add_argument("--address", required=True, help="Wallet address (0x...)")
    aw.add_argument("--chain", required=True, help="Chain: base, ethereum, arbitrum, optimism, polygon")
    aw.add_argument("--label", help="Human-readable label")
    aw.add_argument("--threshold", type=float, default=1.0, help="Balance change threshold in ETH")
    aw.add_argument("--tokens", action="store_true", help="Also track ERC-20 token balances")

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
        "watch-multi": cmd_watch_multi,
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
