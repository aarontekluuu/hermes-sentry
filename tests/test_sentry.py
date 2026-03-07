"""Comprehensive tests for Hermes Sentry — scripts/sentry.py"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Add scripts dir to path so we can import sentry
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

# Patch SENTRY_DIR before import so module-level setup_logging doesn't touch ~/.hermes
_test_dir = tempfile.mkdtemp()
_test_sentry_dir = Path(_test_dir) / "sentry"
_test_sentry_dir.mkdir(parents=True, exist_ok=True)

with patch.dict("os.environ", {}, clear=False):
    import sentry as sentry_mod

# Override module-level paths for all tests
sentry_mod.SENTRY_DIR = _test_sentry_dir
sentry_mod.WATCHLIST_PATH = _test_sentry_dir / "watchlist.json"
sentry_mod.STATE_PATH = _test_sentry_dir / "state.json"
sentry_mod.LOG_PATH = _test_sentry_dir / "sentry.log"
sentry_mod.LOCK_PATH = _test_sentry_dir / ".sentry.lock"


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    """Reset watchlist and state before each test."""
    sentry_mod.save_watchlist({"repos": [], "contracts": [], "wallets": []})
    sentry_mod.save_state({"last_poll": None, "poll_count": 0, "alerts_24h": [], "errors": []})
    yield


@pytest.fixture
def mock_github():
    """Mock GitHub API calls."""
    with patch.object(sentry_mod, "github_get") as mock:
        yield mock


@pytest.fixture
def mock_rpc():
    """Mock RPC calls."""
    with patch.object(sentry_mod, "rpc_call") as mock:
        yield mock


def make_ns(**kwargs):
    """Create an argparse.Namespace with defaults."""
    defaults = {
        "command": "init",
        "json": False,
        "owner": None,
        "repo": None,
        "branches": "main",
        "severity": "all",
        "address": None,
        "chain": "base",
        "label": None,
        "watch": "upgrades,balance,tokens",
        "threshold": 1.0,
        "tokens": False,
        "events": "Transfer",
        "target": None,
        "chains": None,
        "id": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ─── Validation Tests ─────────────────────────────────────────────────────

class TestValidation:
    def test_validate_repo_name_valid(self):
        assert sentry_mod.validate_repo_name("my-repo") == "my-repo"
        assert sentry_mod.validate_repo_name("repo.name") == "repo.name"
        assert sentry_mod.validate_repo_name("repo_name") == "repo_name"

    def test_validate_repo_name_invalid(self):
        with pytest.raises(ValueError):
            sentry_mod.validate_repo_name("")
        with pytest.raises(ValueError):
            sentry_mod.validate_repo_name("repo name with spaces")
        with pytest.raises(ValueError):
            sentry_mod.validate_repo_name("a" * 101)

    def test_validate_address_valid(self):
        addr = "0x" + "a" * 40
        assert sentry_mod.validate_address(addr) == addr.lower()

    def test_validate_address_invalid(self):
        with pytest.raises(ValueError):
            sentry_mod.validate_address("not-an-address")
        with pytest.raises(ValueError):
            sentry_mod.validate_address("0xshort")
        with pytest.raises(ValueError):
            sentry_mod.validate_address("0x" + "g" * 40)

    def test_validate_chain_valid(self):
        for chain in ["base", "ethereum", "arbitrum", "optimism", "polygon"]:
            assert sentry_mod.validate_chain(chain) == chain

    def test_validate_chain_invalid(self):
        with pytest.raises(ValueError):
            sentry_mod.validate_chain("solana")

    def test_validate_branches(self):
        assert sentry_mod.validate_branches(["main", "develop"]) == ["main", "develop"]
        with pytest.raises(ValueError):
            sentry_mod.validate_branches(["invalid branch!"])

    def test_parse_repo_input_url(self):
        owner, repo = sentry_mod.parse_repo_input("https://github.com/bitcoin/bitcoin")
        assert owner == "bitcoin"
        assert repo == "bitcoin"

    def test_parse_repo_input_owner_repo(self):
        owner, repo = sentry_mod.parse_repo_input("NousResearch/hermes-agent")
        assert owner == "NousResearch"
        assert repo == "hermes-agent"

    def test_parse_repo_input_invalid(self):
        with pytest.raises(ValueError):
            sentry_mod.parse_repo_input("just-a-name")


# ─── Utility Function Tests ──────────────────────────────────────────────

class TestUtilities:
    def test_format_eth_balance_none(self):
        assert sentry_mod.format_eth_balance(None) is None

    def test_format_eth_balance_value(self):
        assert sentry_mod.format_eth_balance(1.5) == "1.500000"
        assert sentry_mod.format_eth_balance(0.0) == "0.000000"

    def test_format_token_balance(self):
        assert sentry_mod.format_token_balance(1000000, 6) == "1.0000"
        assert sentry_mod.format_token_balance(0, 18) == "0.0000"
        assert sentry_mod.format_token_balance(42, 0) == "42"

    def test_atomic_write_json(self):
        path = sentry_mod.SENTRY_DIR / "test_atomic.json"
        data = {"key": "value"}
        sentry_mod.atomic_write_json(path, data)
        loaded = sentry_mod.load_json(path)
        assert loaded == data
        path.unlink()

    def test_load_json_missing_file(self):
        result = sentry_mod.load_json(Path("/nonexistent/path.json"), {"default": True})
        assert result == {"default": True}

    def test_load_json_corrupt_file(self):
        path = sentry_mod.SENTRY_DIR / "corrupt.json"
        path.write_text("not json{{{")
        result = sentry_mod.load_json(path, {"fallback": True})
        assert result == {"fallback": True}
        path.unlink()


# ─── GitHub Client Tests ─────────────────────────────────────────────────

class TestGitHubClient:
    def test_github_headers_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = sentry_mod.github_headers()
            assert "Authorization" not in headers
            assert headers["Accept"] == "application/vnd.github+json"

    def test_github_headers_with_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            headers = sentry_mod.github_headers()
            assert headers["Authorization"] == "Bearer test-token"

    @patch("sentry.requests.get")
    def test_github_get_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 1}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        result = sentry_mod.github_get("/repos/test/test")
        assert result == {"id": 1}

    @patch("sentry.requests.get")
    def test_github_get_404(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        result = sentry_mod.github_get("/repos/nonexistent/repo")
        assert result is None

    @patch("sentry.requests.get")
    def test_github_get_403_rate_limit(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"X-RateLimit-Remaining": "0"}
        mock_get.return_value = mock_resp
        result = sentry_mod.github_get("/repos/test/test")
        assert result is None


# ─── Onchain Client Tests ────────────────────────────────────────────────

class TestOnchainClient:
    @patch("sentry.requests.post")
    def test_rpc_call_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        result = sentry_mod.rpc_call("https://rpc.test", "eth_blockNumber", [])
        assert result == "0x1"

    @patch("sentry.requests.post")
    def test_rpc_call_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"message": "fail"}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        result = sentry_mod.rpc_call("https://rpc.test", "eth_getBalance", ["0x0"])
        assert result is None

    def test_get_eth_balance(self, mock_rpc):
        mock_rpc.return_value = hex(int(1.5 * 1e18))
        result = sentry_mod.get_eth_balance("https://rpc.test", "0x" + "a" * 40)
        assert abs(result - 1.5) < 0.001

    def test_get_eth_balance_none(self, mock_rpc):
        mock_rpc.return_value = None
        result = sentry_mod.get_eth_balance("https://rpc.test", "0x" + "a" * 40)
        assert result is None

    def test_get_block_number(self, mock_rpc):
        mock_rpc.return_value = "0x1000"
        result = sentry_mod.get_block_number("https://rpc.test")
        assert result == 4096

    def test_get_code_hash(self, mock_rpc):
        mock_rpc.return_value = "0xabcdef"
        result = sentry_mod.get_code_hash("https://rpc.test", "0x" + "a" * 40)
        assert result is not None
        assert len(result) == 64  # sha256 hex

    def test_get_code_hash_no_code(self, mock_rpc):
        mock_rpc.return_value = "0x"
        result = sentry_mod.get_code_hash("https://rpc.test", "0x" + "a" * 40)
        assert result is None

    def test_get_impl_address(self, mock_rpc):
        impl = "0x" + "b" * 40
        mock_rpc.return_value = "0x" + "0" * 24 + "b" * 40
        result = sentry_mod.get_impl_address("https://rpc.test", "0x" + "a" * 40)
        assert result == impl

    def test_get_impl_address_none(self, mock_rpc):
        mock_rpc.return_value = "0x" + "0" * 64
        result = sentry_mod.get_impl_address("https://rpc.test", "0x" + "a" * 40)
        assert result is None

    def test_get_erc20_balance(self, mock_rpc):
        mock_rpc.return_value = hex(1000000)  # 1 USDC (6 decimals)
        result = sentry_mod.get_erc20_balance(
            "https://rpc.test", "0x" + "a" * 40, "0x" + "b" * 40
        )
        assert result == 1000000

    def test_get_erc20_balance_none(self, mock_rpc):
        mock_rpc.return_value = None
        result = sentry_mod.get_erc20_balance(
            "https://rpc.test", "0x" + "a" * 40, "0x" + "b" * 40
        )
        assert result is None


# ─── ERC-20 Token Functions ──────────────────────────────────────────────

class TestERC20:
    def test_get_all_token_balances(self, mock_rpc):
        # Return non-zero for first call, zero for rest
        mock_rpc.side_effect = [hex(5000000)] + [hex(0)] * 20
        result = sentry_mod.get_all_token_balances("https://rpc.test", "0x" + "a" * 40, "base")
        assert isinstance(result, dict)

    def test_decode_transfer_log(self):
        log_entry = {
            "topics": [
                sentry_mod.EVENT_SIGNATURES["Transfer"],
                "0x" + "0" * 24 + "a" * 40,
                "0x" + "0" * 24 + "b" * 40,
            ],
            "data": hex(1000000),
            "address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
            "transactionHash": "0x" + "c" * 64,
            "blockNumber": "0x100",
        }
        result = sentry_mod.decode_transfer_log(log_entry, "base")
        assert result is not None
        assert result["event"] == "Transfer"
        assert result["symbol"] == "USDC"

    def test_decode_transfer_log_insufficient_topics(self):
        log_entry = {"topics": ["0xone"], "data": "0x0"}
        result = sentry_mod.decode_transfer_log(log_entry, "base")
        assert result is None


# ─── Significance Scoring ────────────────────────────────────────────────

class TestSignificance:
    def test_score_commit_baseline(self):
        commit = {"commit": {"message": "update something"}, "files": []}
        score = sentry_mod.score_commit_significance(commit)
        assert 1 <= score <= 10

    def test_score_commit_security_keyword(self):
        commit = {
            "commit": {"message": "fix critical security vulnerability"},
            "files": [{"filename": "auth.sol", "additions": 50, "deletions": 10}],
        }
        score = sentry_mod.score_commit_significance(commit)
        assert score >= 7

    def test_score_commit_noise(self):
        commit = {
            "commit": {"message": "fix typo in readme"},
            "files": [{"filename": "README.md", "additions": 1, "deletions": 1}],
        }
        score = sentry_mod.score_commit_significance(commit)
        assert score <= 4

    def test_score_batch_significance(self):
        commits = [
            {"commit": {"message": "security fix"}, "files": [{"filename": "auth.rs", "additions": 100, "deletions": 50}]},
            {"commit": {"message": "docs update"}, "files": [{"filename": "README.md", "additions": 1, "deletions": 1}]},
        ]
        score = sentry_mod.score_batch_significance(commits)
        assert score >= 5

    def test_score_batch_empty(self):
        assert sentry_mod.score_batch_significance([]) == 1

    def test_generate_commit_summary_sol(self):
        commit = {
            "commit": {"message": "update vault"},
            "files": [{"filename": "contracts/Vault.sol", "additions": 50, "deletions": 10}],
        }
        summary = sentry_mod.generate_commit_summary(commit)
        assert "Smart contract" in summary

    def test_generate_commit_summary_docs(self):
        commit = {
            "commit": {"message": "update docs"},
            "files": [{"filename": "docs/guide.md", "additions": 20, "deletions": 5}],
        }
        summary = sentry_mod.generate_commit_summary(commit)
        assert "Documentation" in summary


# ─── Severity Classification ─────────────────────────────────────────────

class TestClassification:
    def test_classify_critical_file(self):
        assert sentry_mod.classify_file("contracts/Token.sol") == "warning"
        assert sentry_mod.classify_file("src/auth/login.rs") == "warning"

    def test_classify_noise_file(self):
        assert sentry_mod.classify_file("README.md") == "noise"
        assert sentry_mod.classify_file("package-lock.json") == "noise"

    def test_classify_info_file(self):
        assert sentry_mod.classify_file("src/main.py") == "info"

    def test_classify_test_file(self):
        assert sentry_mod.classify_file("tests/test_main.spec.ts") == "info"

    def test_is_bot_commit(self):
        assert sentry_mod.is_bot_commit({"author": {"login": "dependabot[bot]"}, "commit": {"author": {"name": ""}}})
        assert not sentry_mod.is_bot_commit({"author": {"login": "developer"}, "commit": {"author": {"name": "Developer"}}})

    def test_classify_commit_warning(self):
        commit = {"files": [{"filename": "contracts/Vault.sol"}]}
        assert sentry_mod.classify_commit(commit) == "warning"

    def test_classify_commit_noise(self):
        commit = {"files": [{"filename": "README.md"}, {"filename": "CHANGELOG.md"}]}
        assert sentry_mod.classify_commit(commit) == "noise"

    def test_classify_commit_no_files(self):
        assert sentry_mod.classify_commit({"files": []}) == "info"


# ─── Command Tests ───────────────────────────────────────────────────────

class TestCmdInit:
    def test_init(self, capsys):
        sentry_mod.cmd_init(make_ns())
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True


class TestCmdAddRepo:
    def test_add_repo(self, mock_github, capsys):
        mock_github.side_effect = [
            {"id": 1, "full_name": "test/repo"},  # repo lookup
            [{"sha": "abc123"}],  # commits
        ]
        sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert "test/repo" in output["message"]

        wl = sentry_mod.load_watchlist()
        assert len(wl["repos"]) == 1
        assert wl["repos"][0]["id"] == "test/repo"

    def test_add_repo_not_found(self, mock_github, capsys):
        mock_github.return_value = None
        with pytest.raises(SystemExit):
            sentry_mod.cmd_add_repo(make_ns(owner="ghost", repo="missing"))

    def test_add_repo_duplicate(self, mock_github, capsys):
        mock_github.side_effect = [
            {"id": 1}, [{"sha": "abc"}],
            {"id": 1}, [{"sha": "abc"}],
        ]
        sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))
        capsys.readouterr()
        with pytest.raises(SystemExit):
            sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))


class TestCmdAddContract:
    def test_add_contract(self, mock_rpc, capsys):
        address = "0x" + "a" * 40
        mock_rpc.side_effect = [
            "0xdeadbeef",  # eth_getCode
            "0x" + "0" * 64,  # eth_getStorageAt (no proxy)
            hex(int(2.5 * 1e18)),  # eth_getBalance
            # get_all_token_balances calls (one per token on base)
        ] + [hex(0)] * 20
        sentry_mod.cmd_add_contract(make_ns(address=address, chain="base"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

        wl = sentry_mod.load_watchlist()
        assert len(wl["contracts"]) == 1

    @patch.object(sentry_mod, "get_code_hash", return_value=None)
    @patch.object(sentry_mod, "get_impl_address", return_value=None)
    @patch.object(sentry_mod, "get_eth_balance", return_value=0.0)
    @patch.object(sentry_mod, "get_all_token_balances", return_value={})
    def test_add_contract_no_code(self, mock_tokens, mock_bal, mock_impl, mock_code, mock_rpc, capsys):
        address = "0x" + "a" * 40
        with pytest.raises(SystemExit):
            sentry_mod.cmd_add_contract(make_ns(address=address, chain="base"))


class TestCmdAddWallet:
    def test_add_wallet(self, mock_rpc, capsys):
        address = "0x" + "a" * 40
        mock_rpc.return_value = hex(int(1e18))  # 1 ETH
        sentry_mod.cmd_add_wallet(make_ns(address=address, chain="base"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

        wl = sentry_mod.load_watchlist()
        assert len(wl["wallets"]) == 1

    def test_add_wallet_with_tokens(self, mock_rpc, capsys):
        address = "0x" + "a" * 40
        mock_rpc.side_effect = [hex(int(1e18))] + [hex(0)] * 20
        sentry_mod.cmd_add_wallet(make_ns(address=address, chain="base", tokens=True))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True


class TestCmdWatch:
    def test_watch_repo(self, mock_github, capsys):
        mock_github.side_effect = [
            {"id": 1, "full_name": "test/repo"},
            [{"sha": "abc123"}],
        ]
        sentry_mod.cmd_watch(make_ns(target="test/repo", chain="base"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

    def test_watch_github_url(self, mock_github, capsys):
        mock_github.side_effect = [
            {"id": 1},
            [{"sha": "abc123"}],
        ]
        sentry_mod.cmd_watch(make_ns(target="https://github.com/test/repo", chain="base"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

    @patch.object(sentry_mod, "get_code_hash", return_value="deadbeef" * 8)
    @patch.object(sentry_mod, "get_impl_address", return_value=None)
    @patch.object(sentry_mod, "get_eth_balance", return_value=1.0)
    @patch.object(sentry_mod, "get_all_token_balances", return_value={})
    def test_watch_contract(self, mock_tokens, mock_bal, mock_impl, mock_code, capsys):
        address = "0x" + "a" * 40
        sentry_mod.cmd_watch(make_ns(target=address, chain="base"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True


class TestCmdWatchMulti:
    @patch.object(sentry_mod, "get_code_hash")
    @patch.object(sentry_mod, "get_impl_address", return_value=None)
    @patch.object(sentry_mod, "get_eth_balance", return_value=1.0)
    @patch.object(sentry_mod, "get_all_token_balances", return_value={})
    @patch.object(sentry_mod, "get_block_number", return_value=1000)
    def test_watch_multi_contracts(self, mock_block, mock_tokens, mock_bal, mock_impl, mock_code, capsys):
        mock_code.return_value = "deadbeef" * 8
        address = "0x" + "a" * 40
        sentry_mod.cmd_watch_multi(make_ns(
            address=address, chains="base,ethereum", watch="upgrades,balance"
        ))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert len(output["results"]) == 2

    @patch.object(sentry_mod, "get_code_hash", return_value=None)
    @patch.object(sentry_mod, "get_eth_balance", return_value=0.5)
    @patch.object(sentry_mod, "get_all_token_balances", return_value={})
    def test_watch_multi_wallet_fallback(self, mock_tokens, mock_bal, mock_code, capsys):
        address = "0x" + "a" * 40
        sentry_mod.cmd_watch_multi(make_ns(
            address=address, chains="base", watch="balance"
        ))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert output["results"][0]["type"] == "wallet"


class TestCmdList:
    def test_list_empty(self, capsys):
        sentry_mod.cmd_list(make_ns())
        output = json.loads(capsys.readouterr().out)
        assert output["total"] == 0
        assert output["repos"] == []

    def test_list_with_entries(self, mock_github, capsys):
        mock_github.side_effect = [{"id": 1}, [{"sha": "abc"}]]
        sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))
        capsys.readouterr()

        sentry_mod.cmd_list(make_ns())
        output = json.loads(capsys.readouterr().out)
        assert output["total"] == 1
        assert len(output["repos"]) == 1


class TestCmdRemove:
    def test_remove_existing(self, mock_github, capsys):
        mock_github.side_effect = [{"id": 1}, [{"sha": "abc"}]]
        sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))
        capsys.readouterr()

        sentry_mod.cmd_remove(make_ns(id="test/repo"))
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

        wl = sentry_mod.load_watchlist()
        assert len(wl["repos"]) == 0

    def test_remove_nonexistent(self, capsys):
        with pytest.raises(SystemExit):
            sentry_mod.cmd_remove(make_ns(id="ghost/repo"))


class TestCmdPoll:
    def test_poll_empty(self, capsys):
        sentry_mod.cmd_poll(make_ns())
        out = capsys.readouterr().out
        assert "all clear" in out

    def test_poll_repo_no_new_commits(self, mock_github, capsys):
        # Add a repo first
        mock_github.side_effect = [{"id": 1}, [{"sha": "abc123"}]]
        sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))
        capsys.readouterr()

        # Poll — no new commits
        mock_github.side_effect = [[]]
        sentry_mod.cmd_poll(make_ns())
        out = capsys.readouterr().out
        assert "all clear" in out

    def test_poll_repo_with_commit(self, mock_github, capsys):
        # Setup repo
        mock_github.side_effect = [{"id": 1}, [{"sha": "old123"}]]
        sentry_mod.cmd_add_repo(make_ns(owner="test", repo="repo"))
        capsys.readouterr()

        # Poll returns new commit
        new_commit = {
            "sha": "new456",
            "author": {"login": "dev"},
            "commit": {"message": "feat: add feature", "author": {"name": "dev", "date": "2026-01-01T00:00:00Z"}},
        }
        detail = {
            **new_commit,
            "html_url": "https://github.com/test/repo/commit/new456",
            "files": [{"filename": "src/main.py", "additions": 10, "deletions": 2, "patch": "+code"}],
        }
        mock_github.side_effect = [
            [new_commit],  # commits list
            detail,  # commit detail
        ]
        sentry_mod.cmd_poll(make_ns())
        out = capsys.readouterr().out
        assert "alert" in out.lower() or "test/repo" in out

    def test_poll_json_output(self, capsys):
        sentry_mod.cmd_poll(make_ns(json=True))
        output = json.loads(capsys.readouterr().out)
        assert "alerts" in output
        assert "targets_checked" in output


class TestCmdDigest:
    def test_digest_empty(self, capsys):
        sentry_mod.cmd_digest(make_ns())
        out = capsys.readouterr().out
        assert "DIGEST" in out
        assert "All quiet" in out

    def test_digest_with_alerts(self, capsys):
        state = sentry_mod.load_state()
        state["alerts_24h"] = [
            {
                "severity": "warning",
                "type": "commit",
                "source": "test/repo",
                "significance": 6,
                "time": "2026-03-07T00:00:00+00:00",
                "author": "dev",
                "file_count": 3,
            },
        ]
        sentry_mod.save_state(state)

        sentry_mod.cmd_digest(make_ns())
        out = capsys.readouterr().out
        assert "DIGEST" in out
        assert "test/repo" in out
        assert "Total alerts (24h): 1" in out

    def test_digest_json(self, capsys):
        sentry_mod.cmd_digest(make_ns(json=True))
        output = json.loads(capsys.readouterr().out)
        assert "total_alerts" in output
        assert "trends" in output


class TestCmdHealth:
    @patch.object(sentry_mod, "github_rate_limit", return_value={"remaining": 4999, "limit": 5000, "reset": 0})
    def test_health(self, mock_rate, capsys):
        sentry_mod.cmd_health(make_ns())
        output = json.loads(capsys.readouterr().out)
        assert output["status"] in ("healthy", "degraded")
        assert "targets" in output
        assert "supported_chains" in output


# ─── Polling Logic Tests ─────────────────────────────────────────────────

class TestPollRepo:
    def test_poll_repo_github_error(self, mock_github):
        entry = {"owner": "test", "repo": "repo", "id": "test/repo"}
        mock_github.return_value = None
        alerts = sentry_mod.poll_repo(entry)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "error"

    def test_poll_repo_no_commits(self, mock_github):
        entry = {"owner": "test", "repo": "repo", "id": "test/repo"}
        mock_github.return_value = []
        alerts = sentry_mod.poll_repo(entry)
        assert alerts == []


class TestPollContract:
    def test_poll_contract_upgrade(self):
        entry = {
            "chain": "base",
            "address": "0x" + "a" * 40,
            "label": "Test",
            "watch_type": ["upgrades"],
            "last_impl_address": "0x" + "1" * 40,
            "last_code_hash": "old_hash",
        }
        with patch.object(sentry_mod, "get_impl_address", return_value="0x" + "2" * 40), \
             patch.object(sentry_mod, "get_code_hash", return_value="old_hash"):
            alerts = sentry_mod.poll_contract(entry)
            assert any(a["type"] == "contract_upgrade" for a in alerts)
            assert any(a["significance"] == 10 for a in alerts)

    def test_poll_contract_balance_change(self):
        entry = {
            "chain": "base",
            "address": "0x" + "a" * 40,
            "label": "Test",
            "watch_type": ["balance"],
            "last_balance": 10.0,
            "threshold_eth": 1.0,
        }
        with patch.object(sentry_mod, "get_eth_balance", return_value=5.0):
            alerts = sentry_mod.poll_contract(entry)
            assert any(a["type"] == "balance_change" for a in alerts)

    def test_poll_contract_no_change(self):
        entry = {
            "chain": "base",
            "address": "0x" + "a" * 40,
            "label": "Test",
            "watch_type": ["balance"],
            "last_balance": 10.0,
            "threshold_eth": 1.0,
        }
        with patch.object(sentry_mod, "get_eth_balance", return_value=10.0):
            alerts = sentry_mod.poll_contract(entry)
            assert len(alerts) == 0


class TestPollWallet:
    def test_poll_wallet_movement(self):
        entry = {
            "chain": "base",
            "address": "0x" + "a" * 40,
            "label": "Treasury",
            "threshold_eth": 1.0,
            "last_balance": 100.0,
        }
        with patch.object(sentry_mod, "get_eth_balance", return_value=90.0):
            alerts = sentry_mod.poll_wallet(entry)
            assert len(alerts) == 1
            assert alerts[0]["type"] == "wallet_movement"

    def test_poll_wallet_below_threshold(self):
        entry = {
            "chain": "base",
            "address": "0x" + "a" * 40,
            "label": "Treasury",
            "threshold_eth": 1.0,
            "last_balance": 100.0,
        }
        with patch.object(sentry_mod, "get_eth_balance", return_value=99.5):
            alerts = sentry_mod.poll_wallet(entry)
            assert len(alerts) == 0

    def test_poll_wallet_token_tracking(self):
        entry = {
            "chain": "base",
            "address": "0x" + "a" * 40,
            "label": "Treasury",
            "threshold_eth": 1.0,
            "last_balance": 1.0,
            "track_tokens": True,
            "last_token_balances": {"USDC": "1000.0000"},
            "token_threshold_pct": 5.0,
        }
        with patch.object(sentry_mod, "get_eth_balance", return_value=1.0), \
             patch.object(sentry_mod, "get_all_token_balances", return_value={"USDC": "800.0000"}):
            alerts = sentry_mod.poll_wallet(entry)
            assert any(a["type"] == "token_balance_change" for a in alerts)


# ─── Formatting Tests ────────────────────────────────────────────────────

class TestFormatting:
    def test_format_alert_commit(self):
        alert = {
            "severity": "info",
            "type": "commit",
            "source": "test/repo",
            "message": "add feature",
            "sha": "abc12345",
            "author": "dev",
            "file_count": 2,
            "additions": 10,
            "deletions": 2,
            "significance": 5,
            "summary": "Adds new code",
            "files": ["src/main.py"],
            "url": "https://github.com/test/repo/commit/abc",
        }
        result = sentry_mod.format_alert_human(alert)
        assert "test/repo" in result
        assert "5/10" in result

    def test_format_alert_contract_upgrade(self):
        alert = {
            "severity": "critical",
            "type": "contract_upgrade",
            "source": "Uniswap",
            "chain": "base",
            "address": "0x" + "a" * 40,
            "old_impl": "0x111",
            "new_impl": "0x222",
            "significance": 10,
            "explorer_url": "https://basescan.org/address/0x",
        }
        result = sentry_mod.format_alert_human(alert)
        assert "CONTRACT UPGRADE" in result
        assert "10/10" in result

    def test_format_alert_token_balance(self):
        alert = {
            "severity": "warning",
            "type": "token_balance_change",
            "source": "Treasury",
            "chain": "base",
            "address": "0x" + "a" * 40,
            "token": "USDC",
            "old_balance": "1000.0000",
            "new_balance": "800.0000",
            "change_pct": "-20.0%",
            "significance": 7,
            "explorer_url": "https://basescan.org/address/0x",
        }
        result = sentry_mod.format_alert_human(alert)
        assert "USDC" in result
        assert "7/10" in result

    def test_format_alert_event_logs(self):
        alert = {
            "severity": "warning",
            "type": "event_logs",
            "source": "Contract",
            "chain": "base",
            "address": "0x" + "a" * 40,
            "event_name": "Transfer",
            "count": 3,
            "recent_events": [
                {"event": "Transfer", "value": "100", "symbol": "USDC",
                 "from": "0x" + "1" * 40, "to": "0x" + "2" * 40},
            ],
            "significance": 6,
            "explorer_url": "https://basescan.org",
        }
        result = sentry_mod.format_alert_human(alert)
        assert "Transfer" in result
        assert "6/10" in result

    def test_format_poll_human_no_alerts(self):
        output = {"alerts": [], "errors": [], "noise_count": 0, "targets_checked": 3}
        result = sentry_mod.format_poll_human(output)
        assert "all clear" in result

    def test_format_poll_human_with_alerts(self):
        output = {
            "alerts": [{"severity": "info", "type": "commit", "source": "t/r",
                        "significance": 3, "message": "hi", "sha": "abc",
                        "author": "dev", "file_count": 1, "additions": 1,
                        "deletions": 0, "files": [], "url": "", "summary": ""}],
            "errors": [],
            "noise_count": 2,
            "targets_checked": 5,
        }
        result = sentry_mod.format_poll_human(output)
        assert "1 alert" in result
        assert "2 noise filtered" in result

    def test_format_digest_human(self):
        digest = {
            "date": "2026-03-07",
            "repos_watched": 2,
            "contracts_watched": 1,
            "wallets_watched": 1,
            "total_alerts": 0,
            "trends": {},
            "by_severity": {"critical": [], "warning": [], "info": []},
            "errors": [],
        }
        result = sentry_mod.format_digest_human(digest)
        assert "DIGEST" in result
        assert "2026-03-07" in result


# ─── Event Log Tests ─────────────────────────────────────────────────────

class TestEventLogs:
    def test_get_event_logs(self, mock_rpc):
        mock_rpc.side_effect = [
            "0x1000",  # eth_blockNumber
            [{"topics": ["0xabc"], "data": "0x1"}],  # eth_getLogs
        ]
        result = sentry_mod.get_event_logs(
            "https://rpc.test", "0x" + "a" * 40,
            [sentry_mod.EVENT_SIGNATURES["Transfer"]]
        )
        assert isinstance(result, list)

    def test_get_event_logs_rpc_failure(self, mock_rpc):
        mock_rpc.return_value = None
        result = sentry_mod.get_event_logs(
            "https://rpc.test", "0x" + "a" * 40,
            [sentry_mod.EVENT_SIGNATURES["Transfer"]]
        )
        assert result == []
