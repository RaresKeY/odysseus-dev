from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from core.models import ChatMessage, Session
from routes.history import history_routes as history
from routes import session_routes
import src.model_context as context


@pytest.fixture
def window_api(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "window.db"), connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(history, "SessionLocal", factory)
    monkeypatch.setattr(session_routes, "SessionLocal", factory)
    monkeypatch.setattr(session_routes, "_auth_disabled", lambda: False)
    session = Session("chat", "Test", "https://seed.invalid/v1", "seed", owner="alice", endpoint_id="seed-endpoint",
                      headers={"Authorization": "Bearer stale"})
    session.history = [ChatMessage("user", "Hello"), ChatMessage("assistant", "Reply", {
        "model": "actual", "endpoint_id": "actual-endpoint", "run_id": "turn"})]
    with factory() as db:
        db.add(database.Session(id="chat", name="Test", endpoint_url=session.endpoint_url, model="seed", owner="alice"))
        db.flush()
        db.add(database.ChatAutomationState(session_id="chat", owner_key="user:alice", route_mode="auto"))
        db.add(database.ChatAutomationSubmission(submission_id="turn", owner_key="user:alice", session_id="chat",
            policy_revision=0, authority_epoch=0, intent_digest="fixture", created_at=1, status="completed",
            descriptor={"route_mode": "auto", "model": "planned", "endpoint_id": "planned-endpoint", "reasoning_effort": "default"}))
        db.commit()
    calls = []
    registrations = {name + "-endpoint": name for name in ("seed", "planned", "actual")}
    def resolve(endpoint_id, model, *, owner, require_exact_model):
        calls.append((endpoint_id, model, owner))
        assert owner == "alice" and require_exact_model
        if registrations.get(endpoint_id) != model:
            return None
        return ("https://" + model + ".invalid/v1", model, {"Authorization": "Bearer current-" + model})
    monkeypatch.setattr("src.owner_identity.effective_storage_owner", lambda owner: owner)
    monkeypatch.setattr("src.model_admission.require_owner_model", lambda owner, model: None)
    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint_by_id", resolve)
    probes = []
    def window(url, model, *, headers):
        assert url == "https://" + model + ".invalid/v1"
        assert headers == {"Authorization": "Bearer current-" + model}
        probes.append(model)
        return {"seed": 8192, "planned": 16384, "actual": 32768}[model]
    monkeypatch.setattr(context, "get_context_length", window)
    monkeypatch.setattr("src.model_profiles.supports_user_thinking_toggle", lambda model: model == "actual")
    def temperature(url, model, headers, **kwargs):
        assert headers == {"Authorization": "Bearer current-" + model}
        return {"enabled": model == "actual", "reason": "fixture"}
    monkeypatch.setattr("src.llm_core.temperature_control_status", temperature)
    manager = SimpleNamespace(get_session=lambda sid: session)
    app = FastAPI()
    @app.middleware("http")
    async def identify(request, call_next):
        request.state.current_user = request.headers.get("X-Fixture-User")
        return await call_next(request)
    app.include_router(history.setup_history_routes(manager))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, factory=factory, calls=calls, probes=probes, registrations=registrations)
    engine.dispose()


def read(api, owner="alice"):
    return api.client.get("/api/session/chat/context", headers={"X-Fixture-User": owner})


def test_auto_uses_actual_response_for_window_identity_and_controls(window_api):
    data = read(window_api).json()
    assert (data["model"], data["endpoint_id"], data["context_length"]) == ("actual", "actual-endpoint", 32768)
    assert data["model_source"] == "auto_actual"
    assert not data["thinking_supported"] and data["temperature_control"]["enabled"]
    assert window_api.calls == [("actual-endpoint", "actual", "alice")]
    assert window_api.session.model == "seed" and window_api.session.endpoint_id == "seed-endpoint"
    assert window_api.session.headers == {"Authorization": "Bearer stale"}
    view, _ = history._context_window_view(window_api.session, "alice")
    assert context.get_session_context_length(view, owner="alice") == data["context_length"]


@pytest.mark.parametrize("status", ["bound", "running", "awaiting_input", "awaiting_approval", "accepted"])
def test_active_auto_uses_its_binding_instead_of_previous_response(window_api, status):
    with window_api.factory() as db:
        db.get(database.ChatAutomationSubmission, "turn").status = status
        db.commit()
    data = read(window_api).json()
    assert (data["model"], data["context_length"], data["model_source"]) == ("planned", 16384, "auto_selected")


def test_pending_auto_does_not_report_manual_seed_or_old_reply(window_api):
    with window_api.factory() as db:
        row = db.get(database.ChatAutomationSubmission, "turn")
        row.status, row.descriptor = "preparing", {}
        db.commit()
    data = read(window_api).json()
    assert data["model"] == "" and data["context_length"] == 0 and data["endpoint_url"] is None
    assert data["model_source"] == "auto_pending" and not data["temperature_control"]["enabled"]
    assert not window_api.calls and not window_api.probes


def test_manual_selection_immediately_replaces_old_auto_window(window_api):
    assert read(window_api).json()["model"] == "actual"
    with window_api.factory() as db:
        db.get(database.ChatAutomationState, "chat").route_mode = "manual"
        db.commit()
    data = read(window_api).json()
    assert (data["model"], data["context_length"], data["model_source"]) == ("seed", 8192, "manual")
    assert not data["thinking_supported"] and not data["temperature_control"]["enabled"]


def test_unavailable_actual_endpoint_never_uses_stored_or_other_credentials(window_api):
    window_api.registrations.pop("actual-endpoint")
    data = read(window_api).json()
    assert data["model"] == "actual" and data["context_length"] == 0
    assert data["endpoint_url"] is None and not data["temperature_control"]["enabled"]
    assert not window_api.probes


def test_another_owner_cannot_read_or_resolve_context_route(window_api):
    assert read(window_api, "bob").status_code == 404
    assert not window_api.calls and not window_api.probes


def test_past_turn_metadata_does_not_override_latest_resolved_auto_turn(window_api):
    window_api.session.history[-1].metadata["run_id"] = "older-turn"
    data = read(window_api).json()
    assert data["model"] == "planned" and data["model_source"] == "auto_selected"


def test_forked_auto_history_retains_actual_model_without_submission_rows(window_api):
    with window_api.factory() as db:
        db.delete(db.get(database.ChatAutomationSubmission, "turn"))
        db.commit()
    assert read(window_api).json()["model"] == "actual"


def test_manual_thinking_control_uses_selected_model_capability(window_api):
    with window_api.factory() as db:
        db.get(database.ChatAutomationState, "chat").route_mode = "manual"
        db.commit()
    window_api.session.model, window_api.session.endpoint_id = "actual", "actual-endpoint"
    data = read(window_api).json()
    assert data["model"] == "actual" and data["thinking_supported"]


def test_failed_window_probe_keeps_identity_without_falling_back_to_seed(window_api, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("fixture metadata failure")
    monkeypatch.setattr(context, "get_context_length", fail)
    response = read(window_api)
    assert response.status_code == 200
    assert response.json()["model"] == "actual" and response.json()["context_length"] == 0
    view, _ = history._context_window_view(window_api.session, "alice")
    assert context.get_session_context_length(view, owner="alice") == context.DEFAULT_CONTEXT


def stream_metadata(api, **changes):
    metadata = api.session.history[-1].metadata
    metadata.update(run_id="independent-stream-id", requested_model="planned", requested_endpoint_id="planned-endpoint",
                    timestamp="2026-11-07T10:13:18.235526Z")
    metadata.update(changes)


def test_persisted_stream_ids_match_by_selected_route_and_completion_time(window_api):
    stream_metadata(window_api)
    data = read(window_api).json()
    assert data["model"] == "actual" and data["model_source"] == "auto_actual"


def test_independent_run_id_from_another_endpoint_does_not_override_binding(window_api):
    stream_metadata(window_api, requested_endpoint_id="other-endpoint")
    data = read(window_api).json()
    assert data["model"] == "planned" and data["model_source"] == "auto_selected"


def test_previous_reply_on_same_route_does_not_override_new_turn(window_api):
    stream_metadata(window_api, timestamp="1970-01-01T00:00:00Z")
    data = read(window_api).json()
    assert data["model"] == "planned" and data["model_source"] == "auto_selected"
