"""Deterministic regressions for skill-import DNS validation and pinning."""

import ipaddress

import httpcore
import httpx

from services.memory import skill_importer


PUBLIC_A = ipaddress.ip_address("93.184.216.34")
PUBLIC_B = ipaddress.ip_address("1.1.1.1")


def test_validation_snapshot_is_the_only_connect_destination(monkeypatch):
    answers = iter([
        [str(PUBLIC_A)],
        ["127.0.0.1"],
    ])
    resolver_calls = []

    def _flipping_resolver(host):
        resolver_calls.append(host)
        return next(answers)

    monkeypatch.setattr(skill_importer, "_default_resolver", _flipping_resolver)
    pinned_ips = skill_importer._check_fetch_url("https://rebind.example/skill")

    connected = []

    class _RecordingBackend:
        def connect_tcp(self, host, port, timeout, local_address, socket_options):
            connected.append((host, port))
            return object()

    backend = skill_importer._PinnedBackend(pinned_ips)
    backend._real = _RecordingBackend()
    backend.connect_tcp("rebind.example", 443, timeout=1.0)

    assert resolver_calls == ["rebind.example"]
    assert pinned_ips == [PUBLIC_A]
    assert connected == [(str(PUBLIC_A), 443)]


def test_pinned_backend_falls_back_only_within_validated_snapshot():
    attempts = []

    class _FallbackBackend:
        def connect_tcp(self, host, port, timeout, local_address, socket_options):
            attempts.append((host, timeout))
            if host == str(PUBLIC_A):
                raise httpcore.ConnectError("first address unavailable")
            return "connected"

    backend = skill_importer._PinnedBackend([PUBLIC_A, PUBLIC_B])
    backend._real = _FallbackBackend()

    assert backend.connect_tcp("rebind.example", 443, timeout=1.0) == "connected"
    assert [host for host, _ in attempts] == [str(PUBLIC_A), str(PUBLIC_B)]
    assert all(timeout is not None and 0 <= timeout <= 1.0 for _, timeout in attempts)


def test_transport_preserves_request_authority_and_response_url():
    recorded = []

    class _CoreResponse:
        status = 200
        headers = [(b"content-type", b"text/plain")]
        stream = [b"ok"]
        extensions = {}

        def close(self):
            return None

    class _RecordingPool:
        def handle_request(self, request):
            recorded.append(request)
            return _CoreResponse()

        def close(self):
            return None

    transport = skill_importer._PinnedTransport([PUBLIC_A])
    transport._pool.close()
    transport._pool = _RecordingPool()

    url = "https://github.com:444/octocat/repo?q=1"
    with httpx.Client(transport=transport) as client:
        response = client.get(url)

    core_request = recorded[0]
    assert core_request.url.host == b"github.com"
    assert core_request.url.port == 444
    assert core_request.url.target == b"/octocat/repo?q=1"
    assert (b"host", b"github.com:444") in [
        (name.lower(), value) for name, value in core_request.headers
    ]
    assert str(response.url) == url


def test_get_checked_uses_fresh_transport_per_redirect_hop(monkeypatch):
    first = "https://github.com/owner/repo"
    second = "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"
    snapshots = {
        first: [PUBLIC_A],
        second: [PUBLIC_B],
    }
    clients = []
    requested = []

    monkeypatch.setattr(
        skill_importer,
        "_resolve_and_check_url",
        lambda url: snapshots[url],
    )

    class _Client:
        def __init__(self, *, transport, follow_redirects, timeout):
            assert follow_redirects is False
            clients.append((transport._pinned_ips, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            requested.append((url, headers))
            request = httpx.Request("GET", url)
            if url == first:
                return httpx.Response(
                    302,
                    headers={"location": second},
                    request=request,
                )
            return httpx.Response(200, content=b"ok", request=request)

    monkeypatch.setattr(skill_importer.httpx, "Client", _Client)

    response = skill_importer._get_checked(first, headers={"Accept": "text/plain"})

    assert [ips for ips, _ in clients] == [[PUBLIC_A], [PUBLIC_B]]
    assert requested == [
        (first, {"Accept": "text/plain"}),
        (second, {"Accept": "text/plain"}),
    ]
    assert str(response.url) == second
