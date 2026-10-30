"""Elevation folders are confined before probing and remain confined when sealed."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.chat_automation import validate_elevation_roots


@pytest.fixture
def folders(tmp_path, monkeypatch):
    from src import settings
    allowed = tmp_path / "allowed"
    child = allowed / "child"
    child.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(settings, "get_setting", lambda name, default=None: {
        "agent_elevation_enabled": True,
        "agent_elevation_roots": [str(allowed)],
    }.get(name, default))
    return allowed, child, outside


@pytest.mark.parametrize("kind", ["absolute", "traversal", "sibling_prefix", "symlink"])
def test_outside_folder_is_rejected_before_metadata_probes(folders, monkeypatch, kind):
    from src import tool_execution
    allowed, child, outside = folders
    path = outside
    if kind == "traversal":
        path = allowed / ".." / "outside"
    elif kind == "sibling_prefix":
        path = allowed.with_name("allowed-other")
        path.mkdir()
    elif kind == "symlink":
        path = allowed / "escape"
        path.symlink_to(outside, target_is_directory=True)

    def unexpected_probe(value):
        raise AssertionError("out-of-scope folder reached metadata probing")

    monkeypatch.setattr(tool_execution, "vet_workspace", unexpected_probe)
    with pytest.raises(HTTPException) as error:
        validate_elevation_roots("alice", [{"path": str(path), "access": "read"}])
    assert error.value.status_code == 403


def test_configured_root_and_descendant_keep_exact_access_and_identity(folders):
    from src.agent_runtime.resources import FilesystemRoot
    allowed, child, _ = folders
    choices = [{"path": str(allowed), "access": "read"},
               {"path": str(child), "access": "write"}]
    result = validate_elevation_roots("alice", choices)
    assert [(item["path"], item["access"]) for item in result] == [
        (str(allowed), "read"), (str(child), "write")]
    for item in result:
        sealed = FilesystemRoot.from_dict(item["identity"])
        assert sealed.path == item["path"] and sealed.owner == "alice"
        sealed.validate()


@pytest.mark.parametrize("path,access", [(None, "read"), ([], "read"), (42, "read"),
                                        ("", "read"), ("\x00", "read"), ("/tmp", [])])
def test_malformed_folder_permission_is_a_client_error(folders, path, access):
    with pytest.raises(HTTPException) as error:
        validate_elevation_roots("alice", [{"path": path, "access": access}])
    assert error.value.status_code == 422


def test_folder_replaced_by_outside_symlink_during_admission_is_rejected(folders, monkeypatch):
    from src import tool_execution
    _, child, outside = folders
    original = tool_execution.vet_workspace

    def replace_after_probe(value):
        assert original(value) == str(child)
        child.rmdir()
        child.symlink_to(outside, target_is_directory=True)
        return value

    monkeypatch.setattr(tool_execution, "vet_workspace", replace_after_probe)
    with pytest.raises(HTTPException) as error:
        validate_elevation_roots("alice", [{"path": str(child), "access": "write"}])
    assert error.value.status_code == 403
