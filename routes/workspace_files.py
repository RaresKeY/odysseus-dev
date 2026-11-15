"""Interactive workspace explorer; shared file-tool policy, bounded held-file reads."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import stat
import threading
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.auth_helpers import get_current_user, is_delegated_credential
from src.tool_security import owner_is_admin_or_single_user

TEXT_LIMIT = 1024 * 1024
DOWNLOAD_LIMIT = 16 * 1024 * 1024
ENTRY_LIMIT = 500
SCAN_LIMIT = 5000
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_SAVE_LOCK = threading.Lock()


def _authorize(request):
    # A scoped bearer cannot inherit its human owner's interactive file access.
    if is_delegated_credential(request) or not owner_is_admin_or_single_user(get_current_user(request)):
        raise HTTPException(403, "Workspace files require an interactive administrator")


def _resolve(workspace, path):
    from src.tool_execution import vet_workspace, _resolve_tool_path_in_workspace
    from src.workspace_paths import backend_workspace_path
    if not isinstance(workspace, str) or not workspace or len(workspace) > 4096:
        raise HTTPException(400, "Select a workspace first")
    if not isinstance(path, str) or len(path) > 4096 or any(ord(c) < 32 for c in path):
        raise HTTPException(400, "Invalid file path")
    root = vet_workspace(backend_workspace_path(workspace))
    if not root:
        raise HTTPException(404, "Workspace is unavailable")
    try:
        target = _resolve_tool_path_in_workspace(root, path or ".")
        # Keep confinement explicit at the HTTP boundary as well as in the
        # shared tool policy, before any pathname reaches filesystem I/O.
        root = os.path.abspath(root)
        target = os.path.abspath(target)
        if os.path.commonpath([root, target]) != root:
            raise ValueError("Outside workspace")
        parts = os.path.relpath(target, root).replace(os.sep, "/").split("/")
        if any(part.casefold() in {".git", ".hg", ".svn", ".codex", ".odysseus"}
               or part.casefold().startswith((".env.", ".env-", ".odysseus-edit-")) for part in parts):
            raise ValueError("Private configuration")
        # Canonical relative paths keep links, breadcrumbs and drafts identical.
        relative = os.path.relpath(target, root).replace(os.sep, "/")
        return root, target, "" if relative == "." else relative
    except (ValueError, OSError):
        raise HTTPException(404, "File is unavailable in this workspace") from None


def _component(value):
    # Each openat argument must be exactly one filename, never a new root or
    # a parent traversal.
    name = os.path.basename(value)
    if name != value or name in {"", ".", ".."} or "\\" in name or "\0" in name:
        raise HTTPException(404, "File is unavailable in this workspace")
    return name


@contextmanager
def _opened(root, target, *, directory=False):
    """Hold every ancestor, including ancestors above the selected root."""
    root = os.path.abspath(root)
    target = os.path.abspath(target)
    if os.path.commonpath([root, target]) != root:
        raise HTTPException(404, "File is unavailable in this workspace")
    if os.name != "posix" or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise HTTPException(501, "Workspace file access is unavailable on this platform")
    opened = []
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent = None
        # Opening root in one call only protects its final component: a
        # replaced ancestor could still redirect the complete workspace.
        fd = os.open(os.sep, flags | os.O_DIRECTORY)
        opened.append(fd)
        for part in root.split(os.sep):
            if not part:
                continue
            fd = os.open(_component(part), flags | os.O_DIRECTORY, dir_fd=fd)
            opened.append(fd)
        parts = os.path.relpath(target, root).split(os.sep)
        if parts == ["."]:
            expected = os.fstat(fd)
        else:
            for part in parts[:-1]:
                fd = os.open(_component(part), flags | os.O_DIRECTORY, dir_fd=fd)
                opened.append(fd)
            parent = fd
            name = _component(parts[-1])
            expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
            fd = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=parent)
            opened.append(fd)
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino, actual.st_ctime_ns, actual.st_size) != (expected.st_dev, expected.st_ino, expected.st_ctime_ns, expected.st_size):
            raise HTTPException(409, "File changed while opening; refresh and try again")
        valid = stat.S_ISDIR(actual.st_mode) if directory else stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1
        if not valid:
            raise HTTPException(404, "File is unavailable")
        yield fd, actual, parent
    except FileNotFoundError:
        raise HTTPException(404, "File no longer exists") from None
    except (PermissionError, OSError):
        raise HTTPException(404, "File is unavailable") from None
    finally:
        for handle in reversed(opened):
            os.close(handle)


def _read(fd, limit):
    chunks, size = [], 0
    while size <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > limit:
        raise HTTPException(413, "File is too large for this view")
    return b"".join(chunks)


def _text(data):
    if b"\0" in data:
        raise HTTPException(415, "Binary file — use Download to open it")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "This file is not UTF-8 text — use Download to open it") from None


class FileSave(BaseModel):
    workspace: str = Field(min_length=1, max_length=4096)
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=TEXT_LIMIT)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def setup_workspace_file_routes():
    router = APIRouter()

    @router.get("/files")
    def list_files(request: Request, workspace: str, path: str = ""):
        _authorize(request)
        root, target, relative = _resolve(workspace, path)
        entries, truncated = [], False
        with _opened(root, target, directory=True) as (fd, _, _parent):
            if os.scandir not in os.supports_fd:
                raise HTTPException(501, "Workspace file browsing is unavailable on this platform")
            with os.scandir(fd) as children:
                for index, child in enumerate(children):
                    if index >= SCAN_LIMIT or len(entries) >= ENTRY_LIMIT:
                        truncated = True
                        break
                    if child.is_symlink():
                        continue
                    try:
                        absolute = os.path.join(target, child.name)
                        _, checked, child_path = _resolve(root, absolute)
                        info = child.stat(follow_symlinks=False)
                        folder = stat.S_ISDIR(info.st_mode)
                        if checked != absolute or (not folder and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                            continue
                        entries.append({"name": child.name, "path": child_path,
                                        "directory": folder, "size": None if folder else info.st_size})
                    except (OSError, HTTPException):
                        continue
        entries.sort(key=lambda entry: (not entry["directory"], entry["name"].casefold()))
        return Response(json.dumps({"path": relative, "entries": entries, "truncated": truncated}), media_type="application/json", headers=_HEADERS)

    @router.get("/file")
    def read_file(request: Request, workspace: str, path: str):
        _authorize(request)
        root, target, relative = _resolve(workspace, path)
        with _opened(root, target) as (fd, info, _parent):
            if os.path.splitext(target)[1].casefold() in {".pdf", ".docx", ".xlsx", ".pptx", ".epub", ".zip"}:
                raise HTTPException(415, "Open this file in Documents or download it")
            data = _read(fd, TEXT_LIMIT)
        return Response(content=json.dumps({
            "path": relative, "name": os.path.basename(target), "content": _text(data),
            "revision": hashlib.sha256(data).hexdigest(), "size": len(data),
            "editable": os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"),
        }), media_type="application/json", headers=_HEADERS)

    @router.get("/file/download")
    def download_file(request: Request, workspace: str, path: str):
        _authorize(request)
        root, target, _ = _resolve(workspace, path)
        with _opened(root, target) as (fd, _, _parent):
            data = _read(fd, DOWNLOAD_LIMIT)
        return Response(data, media_type="application/octet-stream", headers={
            **_HEADERS, "Content-Disposition": "attachment; filename*=UTF-8''" + quote(os.path.basename(target), safe="")
        })

    @router.put("/file")
    def save_file(request: Request, body: FileSave):
        _authorize(request)
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            raise HTTPException(403, "Save files from this application")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Save files from this application")
        data = body.content.encode("utf-8")
        if len(data) > TEXT_LIMIT:
            raise HTTPException(413, "File is too large for editing")
        _text(data)
        root, target, relative = _resolve(body.workspace, body.path)
        with _SAVE_LOCK, _opened(root, target) as (fd, info, parent):
            if parent is None:
                raise HTTPException(501, "Editing workspace files is unavailable on this platform")
            old = _read(fd, TEXT_LIMIT)
            _text(old)
            if hashlib.sha256(old).hexdigest() != body.revision:
                raise HTTPException(409, "File changed on disk. Copy your edits, then reload before saving")
            # Replace within the pinned parent; a swapped ancestor/symlink cannot
            # redirect writes. Keep original permissions and clean temporary bytes.
            import secrets
            temporary = ".odysseus-edit-" + secrets.token_hex(16)
            created = False
            try:
                tempfd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
                created = True
                with os.fdopen(tempfd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode) & 0o777)
                    os.fsync(stream.fileno())
                from src.tool_execution import _resolve_tool_path_in_workspace
                try:
                    with _opened(root, os.path.dirname(target), directory=True) as (_current_fd, current_parent, _ancestor):
                        pass
                    pinned_parent = os.fstat(parent)
                    same_parent = (current_parent.st_dev, current_parent.st_ino) == (pinned_parent.st_dev, pinned_parent.st_ino)
                    if not same_parent or _resolve_tool_path_in_workspace(root, body.path) != target:
                        raise ValueError("Parent changed")
                except (ValueError, OSError, HTTPException):
                    raise HTTPException(409, "File location changed; refresh before saving") from None
                current = os.stat(_component(os.path.basename(target)), dir_fd=parent, follow_symlinks=False)
                after = os.fstat(fd)
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(current) != identity(info) or identity(after) != identity(info):
                    raise HTTPException(409, "File changed on disk; reload before saving")
                os.replace(temporary, _component(os.path.basename(target)), src_dir_fd=parent, dst_dir_fd=parent)
                created = False
            finally:
                if created:
                    os.unlink(temporary, dir_fd=parent)
        return {"path": relative, "revision": hashlib.sha256(data).hexdigest(), "size": len(data)}

    return router
