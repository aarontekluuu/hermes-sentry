# Onchain Monitoring Reference for Sentry

## RPC Endpoints (Free, Public)

| Chain | RPC | Explorer | Chain ID |
|-------|-----|----------|----------|
| Base | https://mainnet.base.org | https://basescan.org | 8453 |
| Ethereum | https://eth.llamarpc.com | https://etherscan.io | 1 |
| Arbitrum | https://arb1.arbitrum.io/rpc | https://arbiscan.io | 42161 |

## JSON-RPC Methods Used

### Get Contract Bytecode
```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "eth_getCode",
  "params": ["{address}", "latest"]
}
```
Hash the result to detect bytecode changes (direct contract upgrades).

### Read Storage Slot (Proxy Detection)
```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "eth_getStorageAt",
  "params": ["{address}", "{slot}", "latest"]
}
```

#### ERC-1967 Proxy Slots
| Slot | Purpose | Hex |
|------|---------|-----|
| Implementation | Logic contract address | `0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc` |
| Admin | Proxy admin address | `0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103` |
| Beacon | Beacon contract address | `0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50` |

### Get ETH Balance
```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "eth_getBalance",
  "params": ["{address}", "latest"]
}
```
Result is hex wei. Divide by 1e18 for ETH.

### Get ERC-20 Balance (e.g., USDC)
```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "eth_call",
  "params": [{
    "to": "{token_address}",
    "data": "0x70a08231000000000000000000000000{address_no_0x_padded_32bytes}"
  }, "latest"]
}
```

#### Common Token Addresses
| Token | Base | Ethereum |
|-------|------|----------|
| USDC | 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 | 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 |
| WETH | 0x4200000000000000000000000000000000000006 | 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 |

### Get Recent Transactions (via logs)
```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "eth_getLogs",
  "params": [{
    "address": "{address}",
    "fromBlock": "0x...",
    "toBlock": "latest"
  }]
}
```

## Upgrade Detection Logic

1. **Direct upgrade**: `eth_getCode` hash changes → bytecode was replaced (rare, non-proxy)
2. **Proxy upgrade**: ERC-1967 implementation slot changes → new logic contract deployed
3. **Beacon upgrade**: Beacon slot points to new beacon → all proxies using that beacon upgraded

### Detection Flow
```
1. Read implementation slot → store address
2. On next poll, read again → compare
3. If different:
   a. Fetch new implementation code
   b. Try to get verified source from explorer API
   c. Alert as 🔴 CRITICAL
```

## Explorer APIs (for verified source)

### Basescan
```
GET https://api.basescan.org/api?module=contract&action=getsourcecode&address={address}&apikey={key}
```

### Etherscan
```
GET https://api.etherscan.io/api?module=contract&action=getsourcecode&address={address}&apikey={key}
```

Free tier: 5 calls/sec, no key needed for basic queries.
