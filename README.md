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
# Clone anywhere — the skill uses relative paths
git clone https://github.com/aarontekluuu/hermes-sentry.git
cd hermes-sentry
python3 scripts/sentry.py init
```

Or copy into your Hermes skills directory:
```bash
cp -r hermes-sentry ~/.hermes/skills/devops/sentry
cd ~/.hermes/skills/devops/sentry
python3 scripts/sentry.py init
```

### Usage

```bash
# Run from the skill's root directory
SENTRY="python3 scripts/sentry.py"

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
🔔 Sentry: 1 alert from 6 targets

🟢 NousResearch/hermes-agent — 16 new commits
━━━━━━━━━━━━━━━━━━━━━
Significance: ███████░░░ 8/10
🔑 Top change: Modifies existing code in hermes_cli/ (2 files, +552/-431)

  🟢 caab1cf4 [3/10] — fix: update setup/config UI for local browser mode
  🟢 55c70f35 [3/10] — fix: strip MarkdownV2 escapes from Telegram plaintext fallback
  🟢 d29249b8 [5/10] — feat: local browser backend — zero-cost headless Chromium via agent-browser
  🟢 f668e9fc [5/10] — feat: platform-conditional skill loading + Apple/macOS skills
  ⚪ 74fe1e22 [2/10] — chore: remove TODO.md — all items tracked as issues
  🟢 34893675 [3/10] — fix: simplify timezone migration to use os.getenv directly
  ... +10 more

Files: 38 changed (+4736 -2401)
Authors: teknium1, Teknium
https://github.com/NousResearch/hermes-agent/commits
```

### Daily Digest with Trends
```
📊 SENTRY DAILY DIGEST — 2026-03-07
━━━━━━━━━━━━━━━━━━━━━
Watching: 3 repos, 3 contracts, 0 wallets
Total alerts (24h): 6

📈 TRENDS & PATTERNS
  🏆 Most active: NousResearch/hermes-agent (2 alerts)
  📊 Avg significance: 7.3/10
  🚨 Critical alerts: 2
  👥 Active contributors: Christian Loehle, Dave Airlie, teknium1, furszy, Robin Fernandes
  📝 Total files changed: 341

  ⚠️ ANOMALIES:
    • 💸 Large balance movements: 2 critical treasury/wallet changes

🔴 CRITICAL (2)
  • [7/10] vitalik.eth (Base): token_balance_change
  • [7/10] vitalik.eth (Ethereum): token_balance_change

🟡 WARNING (2)
  • [8/10] NousResearch/hermes-agent: commit_batch
  • [?/10] torvalds/linux: commit_batch

🟢 INFO (2)
  • [?/10] bitcoin/bitcoin: commit_batch
  • [8/10] NousResearch/hermes-agent: commit_batch
```

### Health Check
```json
{
  "status": "healthy",
  "poll_count": 11,
  "targets": { "repos": 3, "contracts": 3, "wallets": 0 },
  "github_rate_limit": { "remaining": 5000, "limit": 5000 },
  "rpc_status": {
    "ethereum": { "ok": true, "block": 24606277 },
    "base": { "ok": true, "block": 43052838 }
  },
  "supported_chains": ["base", "ethereum", "arbitrum", "optimism", "polygon"],
  "supported_tokens": {
    "ethereum": ["USDC", "USDT", "WETH", "DAI", "WBTC", "LINK", "UNI", "AAVE"],
    "base": ["USDC", "WETH", "DAI", "cbETH", "USDbC"],
    "arbitrum": ["USDC", "USDT", "WETH", "WBTC", "DAI"]
  },
  "alerts_24h": 6
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

hermes-sentry/              # Install anywhere — uses relative paths
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
