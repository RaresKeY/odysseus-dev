"""Fork user journeys through the running app, without external model calls."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

import httpx
import pytest

from tests.smoke.stub_provider import MODEL_PRIMARY


def new_chat(client, endpoint):
    response = client.post("/api/session", data={
        "name": "fork-user-flow-smoke", "endpoint_id": endpoint, "model": MODEL_PRIMARY,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_new_chat_default_and_saved_permission_survive_fresh_login(client, fresh_client, stub_endpoint):
    first = new_chat(client, stub_endpoint)
    second = None
    try:
        policy = client.get(f"/api/session/{first}/automation")
        assert policy.status_code == 200, policy.text
        assert policy.json()["approval_mode"] == "auto_approve"
        revision = policy.json()["policy_revision"]
        saved = client.put(f"/api/session/{first}/automation", json={
            "policy_revision": revision, "changes": {"approval_mode": "always_ask"},
        })
        assert saved.status_code == 200, saved.text
        stale = client.put(f"/api/session/{first}/automation", json={
            "policy_revision": revision, "changes": {"approval_mode": "yolo"},
        })
        assert stale.status_code == 409
        assert fresh_client.get(f"/api/session/{first}/automation").json()["approval_mode"] == "always_ask"
        second = new_chat(fresh_client, stub_endpoint)
        assert fresh_client.get(f"/api/session/{second}/automation").json()["approval_mode"] == "auto_approve"
        assert client.get(f"/api/session/{first}/automation").json()["approval_mode"] == "always_ask"
    finally:
        for session in (first, second):
            if session:
                assert client.delete(f"/api/session/{session}").status_code == 200


@pytest.fixture
def workspace():
    data = os.environ.get("ODYSSEUS_DATA_DIR")
    if not data:
        pytest.skip("requires the disposable release-smoke instance")
    # The smoke launcher owns this state. Seed only an independently named
    # directory; the HTTP explorer deliberately has no create/delete endpoint.
    root = Path(data) / "agent_workspace"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fork-flow-", dir=root) as temporary:
        root = Path(temporary)
        (root / "sample.txt").write_bytes("Before\r\nCafé 🌊\r\n".encode())
        yield root


def test_workspace_open_edit_download_and_stale_save_preserve_bytes(client, workspace):
    params = {"workspace": str(workspace), "path": "sample.txt"}
    listing = client.get("/api/workspace/files", params={"workspace": str(workspace)})
    assert listing.status_code == 200, listing.text
    assert any(entry["name"] == "sample.txt" for entry in listing.json()["entries"])
    loaded = client.get("/api/workspace/file", params=params)
    assert loaded.status_code == 200, loaded.text
    edited = "After\r\nCafé 🌊\r\n"
    saved = client.put("/api/workspace/file", json={
        **params, "content": edited, "revision": loaded.json()["revision"],
    })
    assert saved.status_code == 200, saved.text
    downloaded = client.get("/api/workspace/file/download", params=params)
    assert downloaded.status_code == 200
    assert downloaded.content == edited.encode()
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert saved.json()["revision"] == hashlib.sha256(downloaded.content).hexdigest()
    stale = client.put("/api/workspace/file", json={
        **params, "content": "stale draft", "revision": loaded.json()["revision"],
    })
    assert stale.status_code == 409
    assert client.get("/api/workspace/file/download", params=params).content == edited.encode()


def test_workspace_sensitive_paths_and_unauthenticated_requests_are_denied(client, base_url, workspace):
    (workspace / ".env.production").write_text("synthetic private fixture")
    for path in ("../app.db", ".env.production"):
        response = client.get("/api/workspace/file", params={"workspace": str(workspace), "path": path})
        assert response.status_code in (403, 404)
        assert "synthetic private fixture" not in response.text
    with httpx.Client(base_url=base_url, follow_redirects=False) as anonymous:
        response = anonymous.get("/api/workspace/file", params={
            "workspace": str(workspace), "path": "sample.txt",
        })
        assert response.status_code in (401, 403)


def test_chat_and_file_controls_are_shipped_with_the_app(client):
    response = client.get("/")
    assert response.status_code == 200
    for control in ('id="chat-panel-toggle"', 'id="chat-panel-tabs"'):
        assert control in response.text
    assert "./js/workspaceFiles.js" in client.get("/static/app.js").text
