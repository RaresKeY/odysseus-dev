import pytest
import ipaddress
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse

# UNCOMMENT AND ADJUST THESE IMPORTS TO MATCH YOUR FILE PATHS:
from services.memory.skill_importer import (
    parse_skill_source, 
    check_outbound_url, 
    _get_checked, 
    SkillImportError,
    ResolvedSource
)

## 1. Tests for Hostname Dispatch & Substring Spoofing

def test_parse_skill_source_blocks_spoofed_domains():
    """Ensure substring attacks like skills.sh.attacker.com or evilskills.sh are rejected."""
    spoofed_urls = [
        "https://skills.sh.attacker.com/owner/repo",
        "https://evilskills.sh/owner/repo",
        "https://notskills.sh/owner/repo"
    ]
    for url in spoofed_urls:
        with pytest.raises(SkillImportError):
            parse_skill_source(url)


def test_parse_skill_source_allows_valid_subdomains():
    """Ensure true subdomains like api.skills.sh and the main domain work."""
    # Note: This will attempt a network call unless mocked, 
    # but we can verify the dispatch logic triggers correctly.
    with patch("services.memory.skill_importer._get_checked") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://github.com/test-owner/test-repo"
        mock_get.return_value = mock_response

        source = parse_skill_source("https://api.skills.sh/my-skill")
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


## 2. Tests for SSRF Guard, CGNAT, & Local Exceptions

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


def test_check_outbound_url_allows_local_exception():
    """Ensure operator-configured allowed_dist overrides the private IP block."""
    def mock_resolver(host):
        return ["127.0.0.1"]

    # Without exception, it fails
    ok, reason = check_outbound_url("http://127.0.0.1:7860/v1/models", block_private=True, resolver=mock_resolver)
    assert not ok

    # With matching allowed_dist, it succeeds
    ok, reason = check_outbound_url(
        "http://127.0.0.1:7860/v1/models", 
        block_private=True, 
        resolver=mock_resolver, 
        allowed_dist="127.0.0.1:7860"
    )
    assert ok
    assert reason == "ok"


def test_check_outbound_url_allows_public_ip():
    """Ensure public routable IPs pass successfully."""
    def mock_resolver(host):
        return ["93.184.216.34"]

    ok, reason = check_outbound_url("http://example.com", block_private=True, resolver=mock_resolver)
    assert ok
    assert reason == "ok"
    