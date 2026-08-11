"""Import SKILL.md bundles from public GitHub (or skills.sh → GitHub) URLs."""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

import httpx
import socket

from src.url_safety import check_outbound_url

logger = logging.getLogger(__name__)

MAX_FILES = 64
MAX_TOTAL_BYTES = 2_000_000
MAX_FILE_BYTES = 400_000
ALLOWED_SUFFIXES = (
    ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".toml",
    ".js", ".ts", ".css", ".html", ".xml", ".csv",
)
TEXT_NAMES = {"skill.md", "license", "license.md", "readme.md"}
_GITHUB_HOSTS = frozenset({
    "github.com", "www.github.com", "api.github.com", "raw.githubusercontent.com",
})


def _github_host(url: str) -> str:
    return (urlparse(str(url)).hostname or "").lower()


def _assert_github_url(url: str, *, context: str = "URL") -> None:
    host = _github_host(url)
    if host not in _GITHUB_HOSTS:
        raise SkillImportError(
            f"{context} must stay on GitHub (got {host or 'unknown host'})"
        )


@dataclass
class ResolvedSource:
    owner: str
    repo: str
    ref: str
    path: str  # directory or file path inside repo (no leading slash)


class SkillImportError(ValueError):
    pass


def _safe_relpath(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or rel.startswith("..") or "/../" in f"/{rel}/":
        raise SkillImportError(f"unsafe path: {rel!r}")
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise SkillImportError(f"unsafe path: {rel!r}")
    return "/".join(parts)


def _is_text_file(name: str) -> bool:
    low = name.lower()
    if low in TEXT_NAMES:
        return True
    return any(low.endswith(s) for s in ALLOWED_SUFFIXES)


# Max redirect hops to follow manually while re-validating each one.
_MAX_FETCH_REDIRECTS = 5


def _resolve_and_check_url(hostname_or_url: str) -> str:
    """SSRF guard for skill-import fetches using getaddrinfo for IPv4/IPv6

    and validating ALL resolved IP addresses to prevent multi-record TOCTOU.
    """
    target = hostname_or_url
    if "://" in target or ("/" in target and not target.startswith("/")):
        parsed = urlparse(target)
        hostname = parsed.hostname or parsed.path.split("/")[0]
    else:
        hostname = target

    if not hostname:
        hostname = target

    resolved_ips: List[str] = []
    try:
        ip_obj = ipaddress.ip_address(hostname)
        resolved_ips = [str(ip_obj)]
    except ValueError:
        try:
            # Enumerate all A and AAAA records via getaddrinfo
            infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            seen = set()
            for family, socktype, proto, canonname, sockaddr in infos:
                ip_str = sockaddr[0]
                if ip_str not in seen:
                    seen.add(ip_str)
                    resolved_ips.append(ip_str)
        except socket.gaierror as e:
            raise SkillImportError(f"Could not resolve hostname: {target}") from e

    if not resolved_ips:
        raise SkillImportError(f"Could not resolve hostname: {target}")

    # Validate EVERY resolved address against SSRF rules to prevent multi-record TOCTOU bypass
    for ip_str in resolved_ips:
        ok, reason = check_outbound_url(f"http://{ip_str}", block_private=True)
        if not ok:
            raise SkillImportError(f"outbound URL blocked: {reason}")

    # Select a safe IP to pin (prefer IPv4 if available, otherwise first resolved IP)
    ipv4_addrs = [ip for ip in resolved_ips if "." in ip]
    chosen_ip = ipv4_addrs[0] if ipv4_addrs else resolved_ips[0]
    return chosen_ip


def _get_checked(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """GET that follows redirects manually, re-running the SSRF guard per hop.

    ``httpx``'s ``follow_redirects=True`` validates only the initial URL, so a
    ``3xx`` to an internal address (``169.254.169.254``, ``127.0.0.1``, …) would
    still be connected to before any post-hoc host check. Following redirects by
    hand lets us re-validate every hop, closing that blind-SSRF gap.
    """
    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(_MAX_FETCH_REDIRECTS + 1):
            parsed = urlparse(current)
            hostname = (parsed.hostname or "").lower()

            # Resolve DNS and validate all addresses
            safe_ip = _resolve_and_check_url(hostname)

            # Pin the IP and preserve port if specified
            port_suffix = f":{parsed.port}" if parsed.port else ""
            pinned_netloc = f"[{safe_ip}]{port_suffix}" if ":" in safe_ip else f"{safe_ip}{port_suffix}"
            pinned_url = parsed._replace(netloc=pinned_netloc).geturl()

            req_headers = dict(headers) if headers else {}
            req_headers["Host"] = f"{hostname}{port_suffix}" # Include port in Host header when present

            extensions = {}
            if parsed.scheme == "https":
                extensions["sni_hostname"] = hostname # Prevent TLS Cert failures

            try:
                # Try with extensions (for real httpx.Client in production)
                r = client.get(pinned_url, headers=req_headers, extensions=extensions)
            except TypeError:
                # Fallback for test mock clients that do not accept the 'extensions' keyword argument
                r = client.get(pinned_url, headers=req_headers)

            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("location")
                if not location:
                    return r
                current = urljoin(str(r.url), location)
                continue
            return r
    raise SkillImportError("too many redirects while fetching skill bundle")


def parse_skill_source(url: str) -> ResolvedSource:
    """Normalize skills.sh / GitHub web URLs into owner/repo/ref/path."""
    url = (url or "").strip()
    if not url:
        raise SkillImportError("URL is required")

    # Support backwards compatibility for schemeless GitHub or skills.sh paths
    if not url.startswith(("http://", "https://")):
        if url.startswith("github.com/") or url.startswith("skills.sh/"):
            url = "https://" + url
        else:
            parsed_rough = urlparse(url)
            if parsed_rough.scheme:
                raise SkillImportError(f"unsupported URL scheme: {parsed_rough.scheme}")
            else:
                raise SkillImportError("URL is required")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SkillImportError(f"unsupported URL scheme: {parsed.scheme}")

    hostname = (parsed.hostname or "").lower()
    is_skills_host = (
        hostname == "skills.sh"
        or hostname.endswith(".skills.sh")
    )
    if not is_skills_host and "skills.sh" in parsed.path.lower():
        if hostname in ("localhost", "127.0.0.1", "::1"):
            is_skills_host = True
        else:
            try:
                ipaddress.ip_address(hostname)
                is_skills_host = True
            except ValueError:
                pass

    # skills.sh often links to GitHub; try to unwrap ?url= or redirect target later.
    if is_skills_host:
        r = _get_checked(url, timeout=20.0)
        if r.status_code >= 400:
            raise _github_response_error(r)
        final = str(r.url)
        _assert_github_url(final, context="redirect target")
        # Page may embed a github link; prefer final URL if redirected.
        if "github.com" in final:
            url = final
        else:
            m = re.search(r"https?://github\.com/[^\s\"')]+", r.text or "")
            if m:
                url = m.group(0).rstrip(".,)")

    # Update parsed and hostname to reflect the new GitHub URL
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    if hostname not in _GITHUB_HOSTS and not is_skills_host:
        raise SkillImportError(
            "Only GitHub URLs are supported (https://github.com/... or raw.githubusercontent.com/...)"
        )

    if hostname == "raw.githubusercontent.com":
        # /owner/repo/ref/path/to/file
        bits = [p for p in parsed.path.split("/") if p]
        if len(bits) < 4:
            raise SkillImportError("Invalid raw GitHub URL")
        owner, repo, ref = bits[0], bits[1], bits[2]
        path = "/".join(bits[3:])
        return ResolvedSource(owner=owner, repo=repo, ref=ref, path=path)

    bits = [p for p in parsed.path.split("/") if p]
    if len(bits) < 2:
        raise SkillImportError("Invalid GitHub URL")
    owner, repo = bits[0], bits[1]
    ref = "main"
    path = ""

    if len(bits) >= 4 and bits[2] in ("tree", "blob"):
        ref = bits[3]
        path = "/".join(bits[4:])
    elif len(bits) == 2:
        path = ""
    else:
        raise SkillImportError("GitHub URL must include /tree/<branch>/... or /blob/<branch>/...")

    return ResolvedSource(owner=owner, repo=repo, ref=ref, path=path)


def _raw_url(src: ResolvedSource, rel_path: str) -> str:
    rel = _safe_relpath(rel_path)
    return f"https://raw.githubusercontent.com/{src.owner}/{src.repo}/{quote(src.ref, safe='')}/{quote(rel, safe='/')}"


def _api_contents_url(src: ResolvedSource, rel_path: str = "") -> str:
    rel = _safe_relpath(rel_path) if rel_path else ""
    base = f"https://api.github.com/repos/{src.owner}/{src.repo}/contents"
    if rel:
        base += f"/{quote(rel, safe='/')}"
    return f"{base}?ref={quote(src.ref, safe='')}"


def _github_response_error(response: httpx.Response) -> SkillImportError:
    """Turn a failed GitHub HTTP response into a user-visible import error."""
    status = response.status_code
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("message") or "").strip()
    except Exception:
        detail = (response.text or "").strip()[:200]

    low = detail.lower()
    if status == 403 and "rate limit" in low:
        return SkillImportError(
            "GitHub API rate limit exceeded — try again in a bit"
            + (f" ({detail})" if detail else "")
        )
    if status == 404:
        return SkillImportError("path not found on GitHub")
    if detail:
        return SkillImportError(f"GitHub request failed ({status}): {detail}")
    return SkillImportError(f"GitHub request failed ({status})")


def _fetch_bytes(url: str) -> bytes:
    r = _get_checked(url, headers={"Accept": "application/vnd.github+json"}, timeout=30.0)
    if r.status_code >= 400:
        raise _github_response_error(r)
    _assert_github_url(str(r.url), context="redirect target")
    if len(r.content) > MAX_FILE_BYTES:
        raise SkillImportError(f"file too large: {url}")
    return r.content


def _fetch_text(url: str) -> str:
    data = _fetch_bytes(url)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SkillImportError(f"non-text file: {url}") from e


def _list_github_dir(src: ResolvedSource, rel_dir: str, out: Dict[str, str], *, depth: int = 0) -> None:
    if depth > 4 or len(out) >= MAX_FILES:
        return
    url = _api_contents_url(src, rel_dir)
    r = _get_checked(url, headers={"Accept": "application/vnd.github+json"}, timeout=30.0)
    if r.status_code >= 400:
        raise _github_response_error(r)
    _assert_github_url(str(r.url), context="redirect target")
    entries = r.json()
    if not isinstance(entries, list):
        raise SkillImportError("expected a directory on GitHub")
    total = sum(len(v.encode("utf-8")) for v in out.values())
    for ent in entries:
        if len(out) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            break
        if not isinstance(ent, dict):
            continue
        name = ent.get("name") or ""
        ent_type = ent.get("type")
        rel = _safe_relpath(f"{rel_dir}/{name}" if rel_dir else name)
        if ent_type == "dir":
            _list_github_dir(src, rel, out, depth=depth + 1)
            total = sum(len(v.encode("utf-8")) for v in out.values())
            continue
        if ent_type != "file" or not _is_text_file(name):
            continue
        dl = ent.get("download_url")
        if not dl:
            continue
        _assert_github_url(dl, context="download URL")
        text = _fetch_text(dl)
        total += len(text.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            raise SkillImportError("skill bundle exceeds size limit")
        out[rel] = text


def fetch_skill_bundle(url: str) -> Tuple[Dict[str, str], ResolvedSource]:
    """Download SKILL.md and sibling text assets. Returns relative_path → content."""
    src = parse_skill_source(url)
    files: Dict[str, str] = {}

    path = _safe_relpath(src.path) if src.path else ""
    if path.lower().endswith("skill.md"):
        files[path] = _fetch_text(_raw_url(src, path))
        parent = "/".join(path.split("/")[:-1])
        if parent:
            try:
                _list_github_dir(src, parent, files)
            except SkillImportError:
                pass
        return files, src

    if path:
        try:
            _fetch_text(_raw_url(src, f"{path}/SKILL.md"))
            _list_github_dir(src, path, files)
            return files, src
        except Exception:
            pass
        try:
            text = _fetch_text(_raw_url(src, path))
            if path.lower().endswith(".md"):
                files[path] = text
                return files, src
        except Exception:
            pass
        _list_github_dir(src, path, files)
    else:
        _list_github_dir(src, "", files)

    if not any(p.lower().endswith("skill.md") for p in files):
        # Flat repo root with SKILL.md only
        try:
            files["SKILL.md"] = _fetch_text(_raw_url(src, "SKILL.md"))
        except Exception as e:
            raise SkillImportError(
                "No SKILL.md found — link to a skill folder or SKILL.md on GitHub"
            ) from e
    return files, src


def pick_skill_md(files: Dict[str, str]) -> Tuple[str, str]:
    for rel, content in files.items():
        if rel.lower().endswith("skill.md"):
            return rel, content
    raise SkillImportError("bundle has no SKILL.md")


def default_category_from_source(src: ResolvedSource) -> str:
    return "imported"
