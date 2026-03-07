# GitHub API Reference for Sentry

## Authentication
```
Authorization: Bearer {GITHUB_TOKEN}
```

## Endpoints Used

### List Commits (since timestamp)
```
GET /repos/{owner}/{repo}/commits?since={ISO8601}&per_page=20
```
Returns: array of commit objects with `sha`, `commit.message`, `commit.author`, `html_url`

### Get Single Commit (with diff)
```
GET /repos/{owner}/{repo}/commits/{sha}
```
Returns: commit object with `files` array. Each file has:
- `filename` — path
- `status` — added/modified/removed/renamed
- `additions` / `deletions` — line counts
- `patch` — the actual diff (may be large)

### List Releases
```
GET /repos/{owner}/{repo}/releases?per_page=5
```
Returns: release objects with `tag_name`, `name`, `body`, `published_at`

### Get Rate Limit
```
GET /rate_limit
```
Check remaining requests before polling.

## Bot Authors to Filter
- `dependabot[bot]`
- `renovate[bot]`
- `github-actions[bot]`
- `codecov[bot]`
- `mergify[bot]`

## File Classification Patterns

### Critical / Warning
- `*.sol` — smart contracts
- `*.rs` — core Rust code
- `**/security/**`, `**/auth/**` — security-related
- `Dockerfile`, `docker-compose*` — infra changes
- `.github/workflows/**` — CI/CD changes

### Info
- `*.test.*`, `*.spec.*`, `**/__tests__/**` — tests
- `*.ts`, `*.js`, `*.py` — general source

### Noise
- `*.md`, `*.txt`, `*.mdx` — docs
- `package-lock.json`, `yarn.lock`, `bun.lockb` — lockfiles
- `.prettierrc`, `.eslintrc`, `*.config.js` — config/formatting
- `CHANGELOG*`, `LICENSE*` — metadata
