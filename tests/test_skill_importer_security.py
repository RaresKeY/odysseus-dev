from unittest.mock import MagicMock, patch

import pytest

from services.memory.skill_importer import (
    ResolvedSource,
    SkillImportError,
    check_outbound_url,
    parse_skill_source,
)

## 1. Tests for Hostname Dispatch & Substring Spoofing

@pytest.mark.parametrize(
    "url",
    [
        "https://skills.sh.attacker.com/owner/repo",
        "https://evilskills.sh/owner/repo",
        "https://notskills.sh/owner/repo",
        "https://api.skills.sh/owner/repo",
        "https://1.1.1.1/skills.sh/owner/repo",
        "http://localhost/skills.sh/owner/repo",
    ],
)
def test_parse_skill_source_rejects_unsupported_host_before_fetch(url):
    """Unsupported authorities must never reach the network unwrap path."""
    with patch("services.memory.skill_importer._get_checked") as mock_get:
        with pytest.raises(SkillImportError):
            parse_skill_source(url)
    mock_get.assert_not_called()


def test_parse_skill_source_allows_exact_skills_host():
    """The documented skills.sh host may unwrap to an exact GitHub host."""
    with patch("services.memory.skill_importer._get_checked") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://github.com/test-owner/test-repo"
        mock_get.return_value = mock_response

        source = parse_skill_source("https://skills.sh/my-skill")
        assert source.owner == "test-owner"
        assert source.repo == "test-repo"


def test_parse_skill_source_valid_github():
    """Ensure standard GitHub URLs parse into the correct ResolvedSource fields."""
    source = parse_skill_source("https://github.com/octocat/Hello-World/tree/main/docs")
    assert isinstance(source, ResolvedSource)
    assert source.owner == "octocat"
    assert source.repo == "Hello-World"
    assert source.ref == "main"
    assert source.path == "docs"


## 2. Tests for SSRF Guard & CGNAT

def test_check_outbound_url_blocks_cgnat():
    """Ensure Carrier-Grade NAT (RFC 6598) block 100.64.0.0/10 is blocked."""
    def mock_resolver(host):
        return ["100.64.5.10"]

    ok, reason = check_outbound_url("http://example.com", block_private=True, resolver=mock_resolver)
    assert not ok
    assert "private/shared/loopback" in reason  # Updated to match your codebase's error string

def test_check_outbound_url_blocks_loopback():
    """Ensure loopback IPs (127.0.0.1) are blocked by default."""
    def mock_resolver(host):
        return ["127.0.0.1"]

    ok, reason = check_outbound_url("http://localhost", block_private=True, resolver=mock_resolver)
    assert not ok


def test_check_outbound_url_blocks_metadata():
    """Ensure cloud metadata endpoints (169.254.169.254) are blocked."""
    def mock_resolver(host):
        return ["169.254.169.254"]

    ok, reason = check_outbound_url("http://metadata.google.internal", block_private=True, resolver=mock_resolver)
    assert not ok


def test_check_outbound_url_allows_public_ip():
    """Ensure public routable IPs pass successfully."""
    def mock_resolver(host):
        return ["93.184.216.34"]

    ok, reason = check_outbound_url("http://example.com", block_private=True, resolver=mock_resolver)
    assert ok
    assert reason == "ok"
