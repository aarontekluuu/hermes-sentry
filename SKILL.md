---
name: sentry
description: Monitor GitHub repos and onchain smart contracts for changes. Get plain-English alerts when commits land, contracts upgrade, tokens move, or treasuries shift. Supports ERC-20 token tracking, multi-chain monitoring, event log watching, significance scoring, and intelligent daily digests with trend analysis.
version: 0.3.0
author: aaronteklu
license: MIT
metadata:
  hermes:
    tags: [monitoring, github, blockchain, security, contracts, alerts, crypto, base, ethereum, defi, treasury, erc20, tokens]
    category: devops
    related_skills: []
---

# Hermes Sentry

Real-time monitoring agent for GitHub repositories, onchain smart contracts, and crypto wallets.

## When to Use

- "Watch this repo for changes"
- "Monitor this contract for upgrades"
- "Track this wallet's balance"
- "Track USDC and WETH balances for this address"
- "Watch this address on Base and Ethereum"
- "What changed in [repo] today?"
- "Set up alerts for [contract address]"
- "Watch for Transfer events on this contract"
- Any request involving repo monitoring, contract watching, token tracking, or onchain alerts

## Setup

### Requirements
- Python 3.11+ (ships with Hermes)
- `requests` library: `pip install requests`
- **GitHub token** (recommended): `export GITHUB_TOKEN=$(gh auth token)` (60 req/hr without, 5000 with)

### Running Commands

**Always use this pattern** when executing sentry commands via the terminal tool:
```bash
export GITHUB_TOKEN=$(gh auth token 2>/dev/null) && python3 scripts/sentry.py <command> [args]
```

If `gh` is not available, the script still works without a token (reduced rate limits).

### First-Time Init
```bash
export GITHUB_TOKEN=$(gh auth token 2>/dev/null) && python3 scripts/sentry.py init
```
Creates `~/.hermes/sentry/` with `watchlist.json`, `state.json`, and `sentry.log`.

## Important: Never Interpolate User Input Into Shell Commands

All monitoring is done through the Python scripts in `scripts/`. Do NOT construct curl commands with user-supplied repo names or addresses. Always use the CLI interface:

```bash
# ✅ CORRECT — input is validated by the script
python3 scripts/sentry.py add-repo --owner uniswap --repo v4-core

# ❌ WRONG — command injection risk
curl https://api.github.com/repos/{user_input}/commits
```

## Procedures

### 1. Add a Watch Target

#### Smart Watch (auto-detects type)
```bash
# GitHub repo (URL or owner/repo)
python3 scripts/sentry.py watch NousResearch/hermes-agent
python3 scripts/sentry.py watch https://github.com/bitcoin/bitcoin

# Onchain contract (auto-enables upgrades + balance + token tracking)
python3 scripts/sentry.py watch 0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad --label "Uniswap Router" --chain base
```

#### GitHub Repo
```bash
python3 scripts/sentry.py add-repo \
  --owner <github_owner> \
  --repo <repo_name> \
  --branches main \
  --severity all
```

#### Onchain Contract (with ERC-20 & event monitoring)
```bash
python3 scripts/sentry.py add-contract \
  --address <0x_address> \
  --chain base \
  --label "Uniswap Router" \
  --watch upgrades,balance,tokens,events \
  --events Transfer,OwnershipTransferred
```

Watch types:
- `upgrades` — ERC-1967 proxy upgrades + bytecode changes
- `balance` — ETH balance movements
- `tokens` — ERC-20 token balance tracking (USDC, WETH, DAI, WBTC, etc.)
- `events` — Smart contract event log monitoring

#### Wallet (with token tracking)
```bash
python3 scripts/sentry.py add-wallet \
  --address <0x_address> \
  --chain base \
  --label "Treasury" \
  --threshold 1.0 \
  --tokens
```

#### Multi-Chain Watch (same address across chains)
```bash
python3 scripts/sentry.py watch-multi \
  --address <0x_address> \
  --chains base,ethereum,arbitrum \
  --label "Treasury" \
  --watch upgrades,balance,tokens
```

### 2. List Watch Targets
```bash
python3 scripts/sentry.py list
```

### 3. Remove a Watch Target
```bash
python3 scripts/sentry.py remove --id <target_id>
```

### 4. Run a Poll (Cron Task)
```bash
python3 scripts/sentry.py poll
```
Checks all targets, outputs formatted alerts sorted by significance. Includes:
- Significance scores (1-10) for every alert
- AI-generated commit summaries
- ERC-20 token balance changes
- Event log detections

### 5. Generate Daily Digest
```bash
python3 scripts/sentry.py digest
```
Outputs an insightful summary including:
- Trend analysis (most active sources, avg significance)
- Anomaly detection (unusual patterns, critical events)
- Contributor activity
- Alerts grouped by severity with significance scores

### 6. Health Check
```bash
python3 scripts/sentry.py health
```
Reports: targets watched, last poll, RPC status, rate limits, supported chains & tokens.

## Supported Chains

| Chain | Native Token | Tracked ERC-20s |
|-------|-------------|-----------------|
| Ethereum | ETH | USDC, USDT, WETH, DAI, WBTC, LINK, UNI, AAVE |
| Base | ETH | USDC, WETH, DAI, cbETH, USDbC |
| Arbitrum | ETH | USDC, USDT, WETH, WBTC, DAI |
| Optimism | ETH | USDC, WETH, USDT, DAI |
| Polygon | MATIC | USDC, WETH, USDT, DAI |

## Supported Event Signatures

| Event | Use Case |
|-------|----------|
| Transfer | Token movements, NFT transfers |
| Approval | Token spending approvals |
| OwnershipTransferred | Contract ownership changes |
| Upgraded | Proxy implementation upgrades |
| AdminChanged | Proxy admin changes |
| Paused / Unpaused | Contract pause events |
| RoleGranted / RoleRevoked | Access control changes |

## Cron Setup

```bash
# Poll every 5 minutes
hermes cron add --name "sentry-poll" --every 5m \
  --task "Run sentry poll: python3 scripts/sentry.py poll — then format and send any alerts above NOISE severity to the user."

# Daily digest at 9am
hermes cron add --name "sentry-digest" --cron "0 9 * * *" \
  --task "Run sentry digest: python3 scripts/sentry.py digest — format as daily digest and send to user."

# Health check every 6 hours
hermes cron add --name "sentry-health" --every 6h \
  --task "Run sentry health: python3 scripts/sentry.py health — alert if any sources are degraded."
```

## Severity Levels

| Level | Meaning | Examples |
|-------|---------|----------|
| 🔴 **CRITICAL** | Immediate attention | Contract upgrade, proxy impl change, large treasury drain, 50%+ token balance shift |
| 🟡 **WARNING** | Worth reviewing | Security-related commits, new release, unusual wallet activity, ownership transfer, token movements |
| 🟢 **INFO** | Routine | Normal commits, small balance changes, test updates |
| ⚪ **NOISE** | Filtered by default | Bot commits, dep bumps, formatting, CI config, lockfile changes |

## Significance Scores (1-10)

Every alert includes a significance score to help prioritize:

| Score | Priority | Action |
|-------|----------|--------|
| 8-10 | 🔥 Critical | Read immediately |
| 5-7 | ⚡ Important | Review soon |
| 3-4 | 📋 Normal | Check when free |
| 1-2 | 💤 Low | Skip unless relevant |

Scores are computed from: file types changed, commit message keywords, change volume, directory spread, and pattern matching.

## Alert Templates

### Commit Alert (with summary & significance)
```
{severity_emoji} {owner}/{repo}
━━━━━━━━━━━━━━━━━━━━━
{commit_message}
💡 {ai_generated_summary}

Significance: {sig_bar} {score}/10
Files: {file_count} changed (+{insertions} -{deletions})
Author: {author} • {short_sha}
{url}
```

### Token Balance Alert
```
{severity_emoji} 💰 {label} — {token_symbol}
━━━━━━━━━━━━━━━━━━━━━
Significance: {sig_bar} {score}/10
Chain: {chain}
Address: {address}

{old_balance} → {new_balance} {symbol}
Change: {change_pct}

{explorer_url}
```

### Event Log Alert
```
{severity_emoji} 📜 {label} — {event_name} events
━━━━━━━━━━━━━━━━━━━━━
Significance: {sig_bar} {score}/10
Chain: {chain}
Address: {address}

{count} events detected:
  • {value} {symbol} from {from}... → {to}...

{explorer_url}
```

### Daily Digest (with trends)
```
📊 SENTRY DAILY DIGEST — {date}
━━━━━━━━━━━━━━━━━━━━━
Watching: {repos} repos, {contracts} contracts, {wallets} wallets
Total alerts (24h): {total}

📈 TRENDS & PATTERNS
  🏆 Most active: {source} ({count} alerts)
  📊 Avg significance: {avg}/10
  👥 Active contributors: {authors}

  ⚠️ ANOMALIES:
    • {anomaly_description}

{severity_grouped_alerts}
```

## Example Watchlist Configs

See `examples/` directory for ready-to-use configurations:
- **`defi-monitoring.json`** — Track Uniswap, Aave, and major DeFi protocols
- **`dao-treasury.json`** — Monitor DAO treasuries, multisigs, and governance repos
- **`competitor-tracking.json`** — Watch competitor repos for feature releases and security patches

## Security Notes

- All user input is validated in Python before use (address format, repo name regex, chain enum)
- No shell interpolation of user-supplied values — all external calls use `requests` library
- Watchlist file permissions are set to 600 (owner-only read/write)
- GitHub tokens are read from env vars, never stored in watchlist
- Public RPCs can be unreliable — for high-value monitoring, use private RPCs
- State file uses atomic writes (write to temp, then rename) to prevent corruption
- ERC-20 balanceOf calls use validated contract addresses from a curated registry

## Pitfalls

- **Rate limits**: GitHub without token = 60 req/hr. Set `GITHUB_TOKEN` for production.
- **Public RPCs**: May throttle or return stale data. Sentry logs RPC errors and reports degraded health.
- **Proxy contracts**: Sentry checks ERC-1967 implementation slot automatically. Non-proxy contracts are monitored via bytecode hash.
- **Cold start**: First poll after adding a target only baselines — no alerts fire. Changes trigger on subsequent polls.
- **Token dust**: Balances under 0.01 are ignored to avoid noise from dust amounts.
- **Event log range**: Event queries scan the last 5000 blocks by default. Adjust for chains with fast block times.

## Verification

1. Add a repo you control: `sentry.py add-repo --owner you --repo test`
2. Push a commit to that repo
3. Run `sentry.py poll`
4. Confirm alert with significance score appears in stdout
5. Test multi-chain: `sentry.py watch-multi --address 0x... --chains base,ethereum`
6. Run `sentry.py digest` and verify trends section
