"""Real HTTP/TLS checks for selected-account context metadata probes."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import ssl
import subprocess
import threading
from types import SimpleNamespace

import httpx
import pytest

from src import model_context as context
from src import tls_overrides

MODEL = "served-fixture"


@contextmanager
def provider_server(tls=None):
    state = SimpleNamespace(requests=[], windows={"first": 8192, "second": 32768, "rotated": 16384},
                            allocation=4096, loaded=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            account = self.headers.get("Authorization", "").removeprefix("Bearer ")
            state.requests.append((self.path, account))
            if self.path.startswith("/redirect/"):
                self.send_response(302)
                self.send_header("Location", "/credential-trap")
                self.end_headers()
                return
            if account not in state.windows:
                self.send_response(401)
                self.end_headers()
                return
            if self.path.endswith("/api/ps"):
                payload = {"models": [{"name": MODEL + ":latest", "context_length": state.allocation}]
                           if state.loaded else []}
            elif self.path.endswith("/v1/models"):
                payload = {"data": [{"id": MODEL, "context_length": state.windows[account]}]}
            else:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = ("https" if tls else "http") + f"://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    caches = (context._context_cache, context._catalog_ctx_cache, context._ollama_runtime_endpoints)
    for cache in caches:
        cache.clear()
    # Exercise the socket directly, independent of a developer's proxy settings.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield
    for cache in caches:
        cache.clear()


def test_http_accounts_rotation_expiry_and_tenant_path(monkeypatch):
    with provider_server() as server:
        url = server.base + "/tenant/v1/chat/completions"

        def window(account):
            return context.get_context_length(url, MODEL, headers={"Authorization": "Bearer " + account},
                                              endpoint_kind="proxy")

        assert window("first") == 8192
        assert window("second") == 32768
        assert window("first") == 8192
        assert len(server.requests) == 2
        assert window("rotated") == 16384
        assert context.get_context_length_known(url, MODEL, headers={}, endpoint_kind="proxy") == (
            context.DEFAULT_CONTEXT, False)
        assert all(path == "/tenant/v1/models" for path, _ in server.requests)
        # Expire both cache layers without depending on wall-clock scheduling.
        monkeypatch.setattr(context, "_CACHE_TTL_SECONDS", -1)
        context._context_cache.clear()
        context._catalog_ctx_cache.clear()
        assert window("first") == 8192
        server.windows["first"] = 24576
        assert window("first") == 24576


def test_http_redirect_and_origin_change_do_not_forward_credentials():
    with provider_server() as server, provider_server() as other:
        headers = {"Authorization": "Bearer first"}
        url = server.base + "/redirect/v1/chat/completions"
        assert context.get_context_length_known(url, MODEL, headers=headers, endpoint_kind="proxy") == (
            context.DEFAULT_CONTEXT, False)
        assert server.requests == [("/redirect/v1/models", "first")]
        assert context._probe_get(url, other.base + "/tenant/v1/models", headers) is None
        assert other.requests == []


def test_http_ollama_uses_live_allocation_and_detects_unload():
    with provider_server() as server:
        url = server.base + "/tenant/v1/chat/completions"
        headers = {"Authorization": "Bearer first"}
        assert context.get_context_length(url, MODEL, headers=headers, endpoint_kind="api") == 4096
        server.allocation = 8192
        assert context.get_context_length(url, MODEL, headers=headers, endpoint_kind="api") == 8192
        server.loaded = False
        assert context.get_context_length_known(url, MODEL, headers=headers, endpoint_kind="api") == (
            context.DEFAULT_CONTEXT, False)
        assert server.requests == [("/tenant/api/ps", "first")] * 3


def test_private_ca_is_used_only_for_provider_probe(monkeypatch, tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is required for the real TLS negative control")
    cert, key = tmp_path / "provider.pem", tmp_path / "provider.key"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True, timeout=20)
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert, key)
    with provider_server(server_tls) as server:
        url = server.base + "/tenant/v1/chat/completions"
        target = server.base + "/tenant/v1/models"
        headers = {"Authorization": "Bearer first"}
        monkeypatch.setattr(tls_overrides, "_SHARED_SSL_CONTEXT", None)
        with pytest.raises(httpx.ConnectError):
            context._probe_get(url, target, headers)
        assert server.requests == []
        monkeypatch.setattr(tls_overrides, "_extra_bundle_path", str(cert))
        monkeypatch.setattr(tls_overrides, "_SHARED_SSL_CONTEXT", tls_overrides._build_ssl_context())
        assert context.get_context_length(url, MODEL, headers=headers, endpoint_kind="proxy") == 8192
        # Extending provider trust must not alter arbitrary URL-fetch defaults.
        with pytest.raises(httpx.ConnectError):
            httpx.get(target, headers=headers, timeout=5)
        assert server.requests == [("/tenant/v1/models", "first")]
