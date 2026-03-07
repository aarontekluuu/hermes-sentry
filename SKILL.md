---
name: sentry
description: Monitor GitHub repos and onchain smart contracts for changes. Get plain-English alerts when commits land, contracts upgrade, or treasuries move. Persistent watch list with severity filtering, cron-powered polling, and daily digests.
version: 0.2.0
author: aaronteklu
license: MIT
metadata:
  hermes:
    tags: [monitoring, github, blockchain, security, contracts, alerts, crypto, base, ethereum]
    category: devops
    related_skills: []
---

# Hermes Sentry

Real-time monitoring agent for GitHub repositories and onchain smart contracts.

## When to Use

- "Watch this repo for changes"
- "Monitor this contract for upgrades"
- "Track this wallet's balance"
- "What changed in [repo] today?"
- "Set up alerts for [contract address]"
- Any request involving repo monitoring, contract watching, or onchain alerts

## Setup

### Requirements
- Python 3.11+ (ships with Hermes)
- `requests` library: `pip install requests`
- **GitHub token** (recommended): `export GITHUB_TOKEN=ghp_...` (60 req/hr without, 5000 with)

### First-Time Init
```bash
python3 ~/.hermes/skills/devops/sentry/scripts/sentry.py init
```
Creates `~/.hermes/sentry/` with `watchlist.json`, `state.json`, and `sentry.log`.

## Important: Never Interpolate User Input Into Shell Commands

All monitoring is done through the Python scripts in `scripts/`. Do NOT construct curl commands with user-supplied repo names or addresses. Always use the CLI interface:

```bash
# ✅ CORRECT — input is validated by the script
python3 {SKILL_DIR}/scripts/sentry.py add-repo --owner uniswap --repo v4-core

# ❌ WRONG — command injection risk
curl https://api.github.com/repos/{user_input}/commits
```

## Procedures

### 1. Add a Watch Target

#### GitHub Repo
```bash
python3 {SKILL_DIR}/scripts/sentry.py add-repo \
  --owner <github_owner> \
  --repo <repo_name> \
  --branches main \
  --severity all
```
Validates the repo exists and baselines the latest commit SHA.

#### Onchain Contract
```bash
python3 {SKILL_DIR}/scripts/sentry.py add-contract \
  --address <0x_address> \
  --chain base \
  --label "Uniswap Router" \
  --watch upgrades,balance
```
Validates address format, fetches current bytecode hash and implementation slot, stores baseline.

#### Wallet
```bash
python3 {SKILL_DIR}/scripts/sentry.py add-wallet \
  --address <0x_address> \
  --chain base \
  --label "Treasury" \
  --threshold 1.0
```

### 2. List Watch Targets
```bash
python3 {SKILL_DIR}/scripts/sentry.py list
```

### 3. Remove a Watch Target
```bash
python3 {SKILL_DIR}/scripts/sentry.py remove --id <target_id>
```

### 4. Run a Poll (Cron Task)
```bash
python3 {SKILL_DIR}/scripts/sentry.py poll
```
Checks all targets, outputs JSON array of alerts to stdout. Empty array = no changes.

The agent should read the alerts and format them using the templates below before sending to the user.

### 5. Generate Daily Digest
```bash
python3 {SKILL_DIR}/scripts/sentry.py digest
```
Outputs a structured summary of all changes in the last 24 hours.

### 6. Health Check
```bash
python3 {SKILL_DIR}/scripts/sentry.py health
```
Reports: targets watched, last successful poll, any degraded sources, rate limit remaining.

## Cron Setup

```bash
# Poll every 5 minutes
hermes cron add --name "sentry-poll" --every 5m \
  --task "Run sentry poll: python3 {SKILL_DIR}/scripts/sentry.py poll — then format and send any alerts above NOISE severity to the user."

# Daily digest at 9am
hermes cron add --name "sentry-digest" --cron "0 9 * * *" \
  --task "Run sentry digest: python3 {SKILL_DIR}/scripts/sentry.py digest — format as daily digest and send to user."

# Health check every 6 hours
hermes cron add --name "sentry-health" --every 6h \
  --task "Run sentry health: python3 {SKILL_DIR}/scripts/sentry.py health — alert if any sources are degraded."
```

## Severity Levels

| Level | Meaning | Examples |
|-------|---------|----------|
| 🔴 **CRITICAL** | Immediate attention | Contract upgrade, proxy impl change, large treasury drain, security advisory |
| 🟡 **WARNING** | Worth reviewing | Security-related commits, new release, unusual wallet activity, ownership transfer |
| 🟢 **INFO** | Routine | Normal commits, small balance changes, test updates |
| ⚪ **NOISE** | Filtered by default | Bot commits, dep bumps, formatting, CI config, lockfile changes |

## Alert Templates

### Commit Alert
```
{severity_emoji} SENTRY: {owner}/{repo}
━━━━━━━━━━━━━━━━━━━━━
{plain_english_summary}

Files: {file_count} changed (+{insertions} -{deletions})
Author: {author}
Commit: {short_sha}
Link: {url}
```

### Batch Commit Alert (3+ commits grouped)
```
🟢 SENTRY: {owner}/{repo} — {count} new commits
━━━━━━━━━━━━━━━━━━━━━
{grouped_summary}

Top change: {most_significant_commit_summary}
Authors: {unique_authors}
Period: {first_commit_time} → {last_commit_time}
```

### Contract Upgrade Alert
```
🔴 SENTRY: Contract Upgrade Detected
━━━━━━━━━━━━━━━━━━━━━
{label} on {chain}
Address: {address}

Old implementation: {old_impl}
New implementation: {new_impl}

⚠️ Review before interacting.
Explorer: {explorer_url}
```

### Treasury Movement Alert
```
🟡 SENTRY: Treasury Movement
━━━━━━━━━━━━━━━━━━━━━
{label} on {chain}
Address: {address}

{old_balance} → {new_balance} ETH ({delta_direction} {abs_delta})
Threshold: {threshold} ETH

Explorer: {explorer_url}
```

### Daily Digest
```
📊 SENTRY: Daily Digest — {date}
━━━━━━━━━━━━━━━━━━━━━

📁 Repos ({count} watched, {changes} with changes)
{per_repo_summary}

⛓️ Contracts ({count} watched)
{contract_summary_or_no_changes}

💰 Wallets ({count} watched)
{wallet_summary_or_no_changes}

🏥 Health: {status}
```

## Security Notes

- All user input is validated in Python before use (address format, repo name regex, chain enum)
- No shell interpolation of user-supplied values — all external calls use `requests` library
- Watchlist file permissions are set to 600 (owner-only read/write)
- GitHub tokens are read from env vars, never stored in watchlist
- Public RPCs can be unreliable or compromised — for high-value monitoring, use private RPCs
- State file uses atomic writes (write to temp, then rename) to prevent corruption

## Pitfalls

- **Rate limits**: GitHub without token = 60 req/hr. Set `GITHUB_TOKEN` for production.
- **Public RPCs**: May throttle or return stale data. Sentry logs RPC errors and reports degraded health.
- **Proxy contracts**: Sentry checks ERC-1967 implementation slot automatically for any contract. Non-proxy contracts are monitored via bytecode hash.
- **Cold start**: First poll after adding a target only baselines — no alerts fire. Changes trigger on subsequent polls.
- **Large diffs**: Diffs are truncated to 500 lines per file, 2000 lines total per commit in the poll output.

## Verification

1. Add a repo you control: `sentry.py add-repo --owner you --repo test`
2. Push a commit to that repo
3. Run `sentry.py poll`
4. Confirm alert JSON appears in stdout
