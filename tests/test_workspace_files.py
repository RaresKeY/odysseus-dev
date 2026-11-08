"""Real route and held-file checks for interactive workspace browsing."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import workspace_files as files


@pytest.fixture
def explorer(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "test.txt").write_bytes(b"sample text\r\nsecond line\r\n")
    (root / "folder").mkdir()
    (root / "folder" / "nested.md").write_text("# Nested\n")
    monkeypatch.setattr(files, "owner_is_admin_or_single_user", lambda owner: owner == "owner")
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = request.headers.get("x-test-user", "owner")
        request.state.api_token = request.headers.get("x-test-token") == "yes"
        return await call_next(request)

    app.include_router(files.setup_workspace_file_routes(), prefix="/api/workspace")
    with TestClient(app) as client:
        yield client, root


def params(root, path="test.txt"):
    return {"workspace": str(root), "path": path}


def test_list_and_canonical_read(explorer):
    client, root = explorer
    response = client.get("/api/workspace/files", params=params(root, ""))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [row["name"] for row in response.json()["entries"]] == ["folder", "test.txt"]
    for path in ("test.txt", "/workspace/test.txt", str(root / "test.txt"), "folder/../test.txt"):
        response = client.get("/api/workspace/file", params=params(root, path))
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "test.txt"
        assert data["content"] == "sample text\r\nsecond line\r\n"
        assert data["revision"] == hashlib.sha256((root / "test.txt").read_bytes()).hexdigest()


@pytest.mark.parametrize("route", ["/files", "/file", "/file/download"])
@pytest.mark.parametrize("headers", [{"x-test-user": "other"}, {"x-test-token": "yes"}])
def test_no_file_oracle_for_unauthorized(explorer, route, headers):
    client, root = explorer
    response = client.get("/api/workspace" + route, params=params(root, "../../missing"), headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", ".env", ".env.production", ".git/config", ".codex/session.json", "app.db", ".ssh/key"])
def test_path_denial(explorer, path):
    client, root = explorer
    target = root.parent / "outside.txt" if path.startswith("../") else root / path.lstrip("/")
    if str(target).startswith(str(root)):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private")
    response = client.get("/api/workspace/file", params=params(root, path))
    assert response.status_code == 404
    assert "private" not in response.text


def test_symlinks_hardlinks_and_special_files_hidden(explorer):
    client, root = explorer
    outside = root.parent / "outside.txt"
    outside.write_text("private")
    (root / "alias.txt").symlink_to(outside)
    os.link(outside, root / "hard.txt")
    os.mkfifo(root / "pipe")
    response = client.get("/api/workspace/files", params=params(root, ""))
    names = {row["name"] for row in response.json()["entries"]}
    assert names == {"folder", "test.txt"}
    for path in ("alias.txt", "hard.txt", "pipe"):
        assert client.get("/api/workspace/file", params=params(root, path)).status_code == 404


def test_binary_large_missing_and_download(explorer, monkeypatch):
    client, root = explorer
    (root / "image.bin").write_bytes(b"\0payload")
    response = client.get("/api/workspace/file", params=params(root, "image.bin"))
    assert response.status_code == 415
    response = client.get("/api/workspace/file/download", params=params(root, "image.bin"))
    assert response.content == b"\0payload"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(files, "TEXT_LIMIT", 5)
    assert client.get("/api/workspace/file", params=params(root)).status_code == 413
    assert client.get("/api/workspace/file", params=params(root, "missing.txt")).status_code == 404


def test_listing_is_bounded(explorer, monkeypatch):
    client, root = explorer
    monkeypatch.setattr(files, "ENTRY_LIMIT", 1)
    result = client.get("/api/workspace/files", params=params(root, "")).json()
    assert result["truncated"] is True
    assert len(result["entries"]) == 1


def test_save_revision_conflict_and_permissions(explorer):
    client, root = explorer
    target = root / "test.txt"
    target.chmod(0o640)
    snapshot = client.get("/api/workspace/file", params=params(root)).json()
    body = {**params(root), "content": "new text\r\n", "revision": snapshot["revision"]}
    response = client.put("/api/workspace/file", json=body)
    assert response.status_code == 200
    assert target.read_bytes() == b"new text\r\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert response.json()["revision"] == hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text("external change")
    response = client.put("/api/workspace/file", json={**body, "revision": response.json()["revision"]})
    assert response.status_code == 409
    assert target.read_text() == "external change"
    assert not list(root.glob(".odysseus-edit-*"))


@pytest.mark.parametrize("headers", [{"x-test-user": "other"}, {"x-test-token": "yes"}, {"origin": "https://example.org"}, {"sec-fetch-site": "cross-site"}])
def test_save_admission(explorer, headers):
    client, root = explorer
    response = client.put("/api/workspace/file", headers=headers, json={**params(root), "content": "changed", "revision": "0" * 64})
    assert response.status_code == 403
    assert (root / "test.txt").read_bytes() == b"sample text\r\nsecond line\r\n"


def test_replacement_while_opening_is_refused(explorer, monkeypatch):
    client, root = explorer
    original = os.open
    changed = False

    def raced(path, flags, *args, **kwargs):
        nonlocal changed
        if path == "test.txt" and not changed:
            changed = True
            (root / "test.txt").unlink()
            (root / "test.txt").write_text("replacement")
        return original(path, flags, *args, **kwargs)

    # Preserve the adapter's capability check after replacing os.open.
    monkeypatch.setattr(files.os, "supports_dir_fd", files.os.supports_dir_fd | {raced})
    monkeypatch.setattr(files.os, "open", raced)
    assert client.get("/api/workspace/file", params=params(root)).status_code == 409


def test_pinned_parent_move_is_refused(explorer, monkeypatch):
    client, root = explorer
    original = files._read
    snapshot = client.get("/api/workspace/file", params=params(root, "folder/nested.md")).json()

    def raced(fd, limit):
        data = original(fd, limit)
        (root / "folder").rename(root.parent / "moved")
        return data

    monkeypatch.setattr(files, "_read", raced)
    response = client.put("/api/workspace/file", json={**params(root, "folder/nested.md"), "content": "new", "revision": snapshot["revision"]})
    assert response.status_code == 409
    assert (root.parent / "moved/nested.md").read_text() == "# Nested\n"
    assert not list((root.parent / "moved").glob(".odysseus-edit-*"))


def test_no_create_or_binary_overwrite(explorer):
    client, root = explorer
    (root / "binary.txt").write_bytes(b"\0keep")
    for path in ("missing.txt", "binary.txt"):
        response = client.put("/api/workspace/file", json={**params(root, path), "content": "new", "revision": "0" * 64})
        assert response.status_code in {404, 415}
    assert (root / "binary.txt").read_bytes() == b"\0keep"


def test_structured_documents_use_native_viewer_even_if_ascii(explorer):
    client, root = explorer
    (root / "original.pdf").write_bytes(b"%PDF-1.4 ascii body")
    assert client.get("/api/workspace/file", params=params(root, "original.pdf")).status_code == 415
    assert client.get("/api/workspace/file/download", params=params(root, "original.pdf")).content == b"%PDF-1.4 ascii body"
