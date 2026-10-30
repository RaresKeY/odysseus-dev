"""Bound chat turns retain the ordinary workspace without widening it."""
from types import SimpleNamespace
import pytest
from src.agent_runtime.authority import create_request_authority, ExactOperation, RequestAuthority
from src.agent_runtime.resource_binding import resolve_filesystem_operation
from src.tool_policy import ToolPolicy


@pytest.fixture
def bound(monkeypatch):
    import src.chat_automation as chat
    monkeypatch.setattr(chat, "current_turn", lambda: SimpleNamespace(owner="alice", session_id="chat"))
    monkeypatch.setattr(chat, "effective_elevation_roots", lambda: [])


def test_bound_turn_keeps_default_file_root(bound, tmp_path, monkeypatch):
    from src import tool_execution
    monkeypatch.setattr(tool_execution, "default_agent_workspace", lambda: str(tmp_path))
    grant = create_request_authority("Create a file, write test, then delete it", owner="alice", session_id="chat", policy=ToolPolicy())
    assert [root.path for root in grant.resource_roots] == [str(tmp_path)]
    operation = ExactOperation.normalize("write_file", '{"path":"test_file.txt","content":"test"}')
    result = resolve_filesystem_operation(operation, roots=grant.resource_roots)
    assert result.bindings[0].resource.path == str(tmp_path / "test_file.txt")
    with pytest.raises(ValueError):
        resolve_filesystem_operation(ExactOperation.normalize("write_file", '{"path":"../escape.txt","content":"test"}'), roots=grant.resource_roots)


def test_selected_workspace_does_not_gain_default(bound, tmp_path, monkeypatch):
    from src import tool_execution
    def forbidden():
        raise AssertionError("An explicit workspace must not gain the default root")
    monkeypatch.setattr(tool_execution, "default_agent_workspace", forbidden)
    grant = create_request_authority("Write a file", owner="alice", session_id="chat", workspace=str(tmp_path), policy=ToolPolicy())
    assert [root.path for root in grant.resource_roots] == [str(tmp_path)]


def test_unavailable_default_fails_closed(bound, monkeypatch):
    from src import tool_execution
    def unavailable():
        raise RuntimeError("redirected default")
    monkeypatch.setattr(tool_execution, "default_agent_workspace", unavailable)
    grant = create_request_authority("Write a file", owner="alice", session_id="chat", policy=ToolPolicy())
    assert grant.resource_roots == ()
    assert RequestAuthority("restored", "alice", "chat", "", resource_roots=()).resource_roots == ()


def test_default_endpoint_uses_built_in_workspace(tmp_path, monkeypatch):
    from src import tool_execution
    from routes import workspace_routes as routes
    monkeypatch.delenv("ODYSSEUS_WORKSPACE_DEFAULT", raising=False)
    monkeypatch.setattr(routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(routes, "owner_is_admin_or_single_user", lambda owner: True)
    monkeypatch.setattr(tool_execution, "default_agent_workspace", lambda: str(tmp_path))
    endpoint = next(r.endpoint for r in routes.setup_workspace_routes().routes if r.path.endswith("/default"))
    assert endpoint(object()) == {"ok": True, "path": str(tmp_path)}
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_DEFAULT", "/")
    assert endpoint(object()) == {"ok": False, "path": None}
    monkeypatch.setattr(routes, "owner_is_admin_or_single_user", lambda owner: False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        endpoint(object())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_bound_default_native_write_read_delete(bound, tmp_path, monkeypatch):
    from src import tool_execution
    from src.agent_tools.filesystem_tools import WriteFileTool, ReadFileTool, ApplyPatchTool
    from src.agent_runtime.resource_binding import bind_resource_operation
    monkeypatch.setattr(tool_execution, "default_agent_workspace", lambda: str(tmp_path))
    grant = create_request_authority("Create a file, write test, then delete it", owner="alice", session_id="chat", policy=ToolPolicy())
    for tool, content in [
        (WriteFileTool(), '{"path":"test_file.txt","content":"test"}'),
        (ReadFileTool(), '{"path":"test_file.txt"}'),
        (ApplyPatchTool(), '{"patch_text":"*** Begin Patch\\n*** Delete File: test_file.txt\\n*** End Patch"}'),
    ]:
        name = {WriteFileTool: "write_file", ReadFileTool: "read_file", ApplyPatchTool: "apply_patch"}[type(tool)]
        operation = resolve_filesystem_operation(ExactOperation.normalize(name, content), roots=grant.resource_roots)
        with bind_resource_operation(operation):
            result = await tool.execute(operation.execution_input, {})
        assert result.get("exit_code", 0) == 0, result
        if name == "write_file":
            assert (tmp_path / "test_file.txt").read_text() == "test"
    assert not (tmp_path / "test_file.txt").exists()
