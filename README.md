# Hermes Sentry 🛡️

Real-time monitoring agent for GitHub repositories and onchain smart contracts. Built as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill.

Sentry watches your repos and contracts, then delivers plain-English alerts when commits land, contracts upgrade, or treasury balances shift.

## Features

- **GitHub Repo Monitoring** — Track commits across any public/private repo with severity classification
- **Smart Contract Watching** — Detect proxy upgrades (ERC-1967), bytecode changes, and balance movements
- **Wallet Tracking** — Monitor ETH balances with configurable thresholds
- **Severity Filtering** — Critical 🔴 → Warning 🟡 → Info 🟢 → Noise ⚪ (auto-classified)
- **Bot Filtering** — Dependabot, Renovate, and other bot commits filtered automatically
- **Daily Digests** — Summarized 24h activity reports
- **Health Checks** — GitHub rate limits, RPC status, staleness detection
- **Multi-Chain** — Base, Ethereum, Arbitrum supported out of the box

## Quick Start

### Requirements
- Python 3.11+
- `requests` library (`pip install requests`)
- GitHub token recommended (`export GITHUB_TOKEN=$(gh auth token)`)

### Install as Hermes Skill

```bash
mkdir -p ~/.hermes/skills/devops/sentry
cp -r * ~/.hermes/skills/devops/sentry/
python3 ~/.hermes/skills/devops/sentry/scripts/sentry.py init
```

### Usage

```bash
SENTRY="python3 ~/.hermes/skills/devops/sentry/scripts/sentry.py"

# Initialize
$SENTRY init

# Watch a GitHub repo
$SENTRY watch NousResearch/hermes-agent
$SENTRY watch https://github.com/bitcoin/bitcoin

# Watch an onchain contract (Base by default)
$SENTRY watch 0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad --label "Uniswap Router"

# Watch a wallet
$SENTRY add-wallet --address 0x... --chain ethereum --label "Treasury" --threshold 5.0

# Poll for changes
$SENTRY poll

# Daily digest
$SENTRY digest

# Health check
$SENTRY health

# List all targets
$SENTRY list

# Remove a target
$SENTRY remove --id "NousResearch/hermes-agent"
```

## Example Output

### Poll
```
🔔 Sentry: 3 alerts from 4 targets

🟡 NousResearch/hermes-agent — 25 new commits
━━━━━━━━━━━━━━━━━━━━━
  🟢 ce28f847 — fix: update OpenRouter model names for yc-bench config
  🟡 388dd478 — feat: add z.ai/GLM, Kimi/Moonshot, MiniMax as first-class providers
  ... +19 more

Files: 112 changed (+33085 -260)
Authors: Teknium, Robin Fernandes, 0xbyt4, teknium1

🟢 bitcoin/bitcoin — 10 new commits
━━━━━━━━━━━━━━━━━━━━━
  🟢 c7a3ea24 — Merge bitcoin/bitcoin#34692: Bump dbcache to 1 GiB
  🟢 8b70ed69 — Merge bitcoin/bitcoin#34521: validation: fix UB in LoadChainTip
  ... +8 more

Files: 43 changed (+838 -255)
```

### Health Check
```json
{
  "status": "healthy",
  "poll_count": 8,
  "targets": { "repos": 3, "contracts": 1, "wallets": 0 },
  "github_rate_limit": { "remaining": 4940, "limit": 5000 },
  "rpc_status": { "base": { "ok": true, "block": 43034264 } },
  "alerts_24h": 3
}
```

### Daily Digest
```
📊 SENTRY DAILY DIGEST — 2026-03-07
━━━━━━━━━━━━━━━━━━━━━
Watching: 3 repos, 1 contracts, 0 wallets
Total alerts (24h): 3

🟡 WARNING (2)
  • NousResearch/hermes-agent: commit_batch
  • torvalds/linux: commit_batch

🟢 INFO (1)
  • bitcoin/bitcoin: commit_batch
```

## Severity Classification

| Level | Emoji | Triggers |
|-------|-------|----------|
| Critical | 🔴 | Contract upgrades, proxy impl changes, large balance drains |
| Warning | 🟡 | Security-related file changes (`.sol`, `auth`, `.rs`), unusual activity |
| Info | 🟢 | Normal commits, test updates, small changes |
| Noise | ⚪ | Bot commits, lockfiles, markdown, CI config (filtered by default) |

## Cron Setup (via Hermes)

```bash
# Poll every 5 minutes
hermes cron add --name "sentry-poll" --every 5m \
  --task "Run sentry poll and send alerts above NOISE severity."

# Daily digest at 9am
hermes cron add --name "sentry-digest" --cron "0 9 * * *" \
  --task "Run sentry digest and send formatted summary."

# Health check every 6 hours
hermes cron add --name "sentry-health" --every 6h \
  --task "Run sentry health and alert if degraded."
```

## Architecture

```
~/.hermes/sentry/
├── watchlist.json    # Repos, contracts, wallets being monitored
├── state.json        # Poll history, 24h alert buffer
└── sentry.log        # Execution logs

~/.hermes/skills/devops/sentry/
├── SKILL.md          # Hermes skill definition
├── scripts/
│   └── sentry.py     # Core monitoring engine
├── references/
│   ├── github-api.md
│   └── onchain-monitoring.md
└── templates/
```

## Security

- All user input validated in Python before use (regex for repos, addresses, chains)
- No shell interpolation — all HTTP via `requests` library
- Watchlist permissions set to `600` (owner-only)
- Atomic file writes (temp + rename) prevent state corruption
- File locking prevents concurrent poll races
- GitHub tokens read from env vars, never persisted to disk

## Supported Chains

| Chain | RPC | Explorer |
|-------|-----|----------|
| Base | mainnet.base.org | basescan.org |
| Ethereum | eth.llamarpc.com | etherscan.io |
| Arbitrum | arb1.arbitrum.io/rpc | arbiscan.io |

## License

MIT
