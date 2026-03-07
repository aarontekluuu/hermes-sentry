# Hermes Sentry 🛡️

> Your AI agent's eyes on GitHub and the blockchain. Real-time monitoring with significance scoring, ERC-20 token tracking, multi-chain support, and intelligent digests.

Built as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill for the AgentSkills hackathon.

---

## Why Sentry?

You can't watch everything. Your agent can.

**Sentry** monitors GitHub repos and onchain contracts simultaneously, then delivers plain-English alerts with **significance scores** so you know what actually matters. It's the difference between drowning in notifications and getting a single message that says: *"Your treasury lost 50 ETH. Score: 10/10. Look now."*

### What Makes It Different

- 🧠 **Significance Scoring (1-10)** — Every alert rated by importance. A security fix in `auth.rs` scores higher than a README typo.
- 💰 **ERC-20 Token Tracking** — USDC, WETH, DAI, WBTC, LINK, UNI, AAVE — not just ETH balance.
- 🌐 **Multi-Chain in One Command** — `watch-multi` tracks the same address across Base, Ethereum, Arbitrum, Optimism, Polygon.
- 📜 **Event Log Monitoring** — Watch for Transfer, OwnershipTransferred, Upgraded, Paused events on any contract.
- 📊 **Intelligent Digests** — Daily summaries with trend analysis, anomaly detection, and contributor activity.
- 💡 **Commit Summaries** — Auto-generated plain-English descriptions of what code changes actually do.
- 🔴 **Contract Upgrade Detection** — ERC-1967 proxy changes flagged as significance 10/10 immediately.

---

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

Or install from GitHub:
```bash
mkdir -p ~/.hermes/skills/devops/sentry
git clone https://github.com/aarontekluuu/hermes-sentry.git ~/.hermes/skills/devops/sentry/
python3 ~/.hermes/skills/devops/sentry/scripts/sentry.py init
```

### Usage

```bash
SENTRY="python3 ~/.hermes/skills/devops/sentry/scripts/sentry.py"

# Initialize
$SENTRY init

# ━━━ GitHub Monitoring ━━━
$SENTRY watch NousResearch/hermes-agent
$SENTRY watch https://github.com/bitcoin/bitcoin

# ━━━ Contract Monitoring (with token & event tracking) ━━━
$SENTRY watch 0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad --label "Uniswap Router" --chain base
$SENTRY add-contract --address 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2 \
  --chain ethereum --label "Aave V3 Pool" \
  --watch upgrades,balance,tokens,events \
  --events Transfer,Upgraded

# ━━━ Wallet Monitoring (with ERC-20 tokens) ━━━
$SENTRY add-wallet --address 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 \
  --chain ethereum --label "vitalik.eth" --tokens

# ━━━ Multi-Chain (one address, many chains) ━━━
$SENTRY watch-multi --address 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 \
  --chains base,ethereum,arbitrum --label "vitalik.eth"

# ━━━ Operations ━━━
$SENTRY poll          # Check all targets for changes
$SENTRY digest        # 24h summary with trends
$SENTRY health        # System status
$SENTRY list          # Show all targets
$SENTRY remove --id "NousResearch/hermes-agent"
```

---

## Real Output Examples

### Multi-Chain Watch
```
$ sentry watch-multi --address 0xd8dA...96045 --chains base,ethereum --label "vitalik.eth"

{
  "ok": true,
  "message": "Multi-chain watch for 0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
  "results": [
    {
      "chain": "base",
      "type": "contract",
      "balance": "0.070943",
      "tokens": {
        "USDC": "14.5785",
        "WETH": "0.0628",
        "DAI": "3.7343",
        "USDbC": "4.3907"
      }
    },
    {
      "chain": "ethereum",
      "type": "contract",
      "balance": "32.131220",
      "tokens": {
        "USDC": "75547.2642",
        "USDT": "270.1903",
        "WBTC": "0.0010",
        "LINK": "1.7777",
        "AAVE": "0.0101"
      }
    }
  ]
}
```

### Poll with Significance Scores
```
🔔 Sentry: 3 alerts from 6 targets

🟡 NousResearch/hermes-agent — 25 new commits
━━━━━━━━━━━━━━━━━━━━━
Significance: ██████░░░░ 6/10
🔑 Top change: Code changes with tests across 5 directories (15 files, +33085/-260)

  🟢 ce28f847 [3/10] — fix: update OpenRouter model names for yc-bench config
  🟡 388dd478 [6/10] — feat: add z.ai/GLM, Kimi/Moonshot, MiniMax as first-class providers
  🟢 fdebca45 [4/10] — fix: implement Nous credential refresh on 401 error
  🟡 94053d75 [5/10] — fix: custom endpoint no longer leaks OPENROUTER_API_KEY
  ... +21 more

Files: 112 changed (+33085 -260)
Authors: Teknium, Robin Fernandes, 0xbyt4, teknium1

🟡 📈 DAO Treasury — USDC
━━━━━━━━━━━━━━━━━━━━━
Significance: ███████░░░ 7/10
Chain: ethereum
Address: 0x28849d2b...5642

75547.26 → 52103.84 USDC
Change: -31.0%

https://etherscan.io/address/0x28849d2b...5642
```

### Daily Digest with Trends
```
📊 SENTRY DAILY DIGEST — 2026-03-07
━━━━━━━━━━━━━━━━━━━━━
Watching: 3 repos, 3 contracts, 2 wallets
Total alerts (24h): 8

📈 TRENDS & PATTERNS
  🏆 Most active: NousResearch/hermes-agent (3 alerts)
  📊 Avg significance: 5.8/10
  🚨 Critical alerts: 1
  👥 Active contributors: teknium1, Robin Fernandes, Ava Chow
  📝 Total files changed: 303

  ⚠️ ANOMALIES:
    • 💸 Large balance movements: 1 critical treasury/wallet change
    • ⚡ Unusually high activity: 4 high-significance events

🔴 CRITICAL (1)
  • [10/10] Aave V3 Pool: contract_upgrade

🟡 WARNING (4)
  • [6/10] NousResearch/hermes-agent: commit_batch
  • [7/10] DAO Treasury: token_balance_change
  • [5/10] torvalds/linux: commit_batch
  • [6/10] vitalik.eth: event_logs

🟢 INFO (3)
  • [4/10] bitcoin/bitcoin: commit_batch
  • [3/10] Uniswap Router: balance_change
  • [2/10] vitalik.eth (Base): balance_change
```

### Health Check
```json
{
  "status": "healthy",
  "poll_count": 8,
  "targets": { "repos": 3, "contracts": 3, "wallets": 2 },
  "github_rate_limit": { "remaining": 4937, "limit": 5000 },
  "rpc_status": {
    "base": { "ok": true, "block": 43035205 },
    "ethereum": { "ok": true, "block": 22012841 }
  },
  "supported_chains": ["base", "ethereum", "arbitrum", "optimism", "polygon"],
  "supported_tokens": {
    "ethereum": ["USDC", "USDT", "WETH", "DAI", "WBTC", "LINK", "UNI", "AAVE"],
    "base": ["USDC", "WETH", "DAI", "cbETH", "USDbC"],
    "arbitrum": ["USDC", "USDT", "WETH", "WBTC", "DAI"]
  },
  "alerts_24h": 8
}
```

---

## Features

| Feature | Description |
|---------|-------------|
| **GitHub Monitoring** | Track commits with auto-classification, bot filtering, and significance scoring |
| **Commit Summaries** | Auto-generated plain-English summaries of what code changes actually do |
| **Significance Scores** | 1-10 rating on every alert based on file types, keywords, change volume |
| **ERC-20 Token Tracking** | Monitor USDC, WETH, DAI, WBTC, LINK, UNI, AAVE balances |
| **Multi-Chain** | Watch same address across Base, Ethereum, Arbitrum, Optimism, Polygon |
| **Event Log Monitoring** | Transfer, Approval, OwnershipTransferred, Upgraded, Paused events |
| **Contract Upgrades** | ERC-1967 proxy upgrade detection with significance 10/10 |
| **Intelligent Digests** | Trends, anomaly detection, contributor analysis in daily summaries |
| **Severity Filtering** | Critical 🔴 → Warning 🟡 → Info 🟢 → Noise ⚪ (auto-classified) |
| **Bot Filtering** | Dependabot, Renovate, and CI bots filtered automatically |
| **Atomic State** | File locking + atomic writes prevent corruption |

---

## Supported Chains & Tokens

| Chain | Tracked Tokens |
|-------|---------------|
| **Ethereum** | USDC, USDT, WETH, DAI, WBTC, LINK, UNI, AAVE |
| **Base** | USDC, WETH, DAI, cbETH, USDbC |
| **Arbitrum** | USDC, USDT, WETH, WBTC, DAI |
| **Optimism** | USDC, WETH, USDT, DAI |
| **Polygon** | USDC, WETH, USDT, DAI |

## Watched Event Signatures

| Event | Use Case |
|-------|----------|
| `Transfer` | Token/NFT movements |
| `Approval` | Spending approvals |
| `OwnershipTransferred` | Ownership changes |
| `Upgraded` | Proxy upgrades |
| `AdminChanged` | Admin modifications |
| `Paused` / `Unpaused` | Emergency stops |
| `RoleGranted` / `RoleRevoked` | Access control |

---

## Example Configs

See [`examples/`](examples/) for ready-to-use setups:

| Config | Use Case |
|--------|----------|
| [`defi-monitoring.json`](examples/defi-monitoring.json) | Track Uniswap, Aave, and major DeFi protocols |
| [`dao-treasury.json`](examples/dao-treasury.json) | Monitor DAO treasuries, multisigs, governance repos |
| [`competitor-tracking.json`](examples/competitor-tracking.json) | Watch competitor repos for features and security patches |

---

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

---

## Architecture

```
~/.hermes/sentry/
├── watchlist.json    # Repos, contracts, wallets + token state
├── state.json        # Poll history, 24h alert buffer, trend data
└── sentry.log        # Execution logs

~/.hermes/skills/devops/sentry/
├── SKILL.md          # Hermes skill definition (agentskills.io spec)
├── README.md
├── scripts/
│   └── sentry.py     # Core monitoring engine (~1600 lines)
├── references/
│   ├── github-api.md
│   └── onchain-monitoring.md
├── examples/
│   ├── defi-monitoring.json
│   ├── dao-treasury.json
│   └── competitor-tracking.json
└── templates/
```

## Security

- All user input validated in Python (regex for repos, addresses, chain enum)
- No shell interpolation — all HTTP via `requests` library
- Watchlist permissions set to `600` (owner-only)
- Atomic file writes (temp + rename) prevent state corruption
- File locking prevents concurrent poll races
- GitHub tokens read from env vars, never persisted
- ERC-20 calls use a curated token registry — no arbitrary contract calls
- Event log queries are scoped to known signatures

## License

MIT
