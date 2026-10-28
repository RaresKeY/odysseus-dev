"""Interactive, owner-scoped controls for committed chat automation settings."""
from fastapi import APIRouter, HTTPException, Request
from src.auth_helpers import require_user, effective_user, is_delegated_credential
from src.agent_runtime.authority import require_user_approval_request
from src.chat_automation import AutomationStore


def interactive_owner(request):
    require_user(request)
    require_user_approval_request(request)
    if is_delegated_credential(request):
        raise HTTPException(403, "Open chat settings in your browser")
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise HTTPException(403, "Open settings from this application")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Settings origin differs from this application")
    from src.owner_identity import effective_storage_owner
    owner = effective_storage_owner(effective_user(request))
    if owner is None:
        raise HTTPException(401, "Sign in before changing chat settings")
    return owner


def setup_chat_automation_routes(session_manager):
    router = APIRouter(tags=["chat settings"])

    def with_review_availability(result, owner, *, configuration=None):
        from src.approval_fallback import auto_approve_availability
        config = configuration if configuration is not None else AutomationStore().configuration(owner)
        return {**result, "auto_approve_availability": auto_approve_availability(
            config.get("decision_provider"), owner,
            session_id=result.get("session_id"), policy=result)}

    from src.decision_advice import SafetyAdviceService
    from src.tool_approvals import tool_approval_store
    safety = SafetyAdviceService(tool_approval_store)

    @router.post("/api/session/{session_id}/tool-approvals/{approval_id}/advice")
    async def safety_advice(request: Request, session_id: str, approval_id: str):
        owner = interactive_owner(request)
        # Session admission precedes inference and proposal lookup. The service
        # separately binds the sealed operation, owner, current policy and run.
        AutomationStore().policy(owner, session_id)
        return await safety.advise(approval_id, owner=owner, session_id=session_id)

    @router.get("/api/chat-settings/models")
    async def model_choices(request: Request):
        owner = interactive_owner(request)
        from core.database import SessionLocal, ModelEndpoint
        from src.auth_helpers import owner_filter
        from src.owner_identity import DEFAULT_LOCAL_OWNER
        from src.endpoint_resolver import _endpoint_enabled_models
        db = SessionLocal()
        try:
            rows = owner_filter(db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled.is_(True)),
                ModelEndpoint, owner or DEFAULT_LOCAL_OWNER).all()
            from src.model_admission import endpoint_supports_chat, endpoint_supports_decisions, endpoint_model_type
            def choice(row):
                return {"id": row.id, "name": row.name, "models": _endpoint_enabled_models(row),
                        "model_type": endpoint_model_type(row)}
            return {"endpoints": [choice(r) for r in rows if endpoint_supports_chat(r)],
                    "decision_endpoints": [choice(r) for r in rows if endpoint_supports_decisions(r)]}
        finally:
            db.close()

    @router.get("/api/chat-settings")
    async def configuration(request: Request):
        owner = interactive_owner(request)
        result = AutomationStore().configuration(owner)
        from src.settings import get_setting
        result["elevation_available"] = bool(get_setting("agent_elevation_enabled", False))
        result["available_folders"] = get_setting("agent_elevation_roots", []) if result["elevation_available"] else []
        return with_review_availability(result, owner, configuration=result)

    @router.put("/api/chat-settings")
    async def save_configuration(request: Request, body: dict):
        owner = interactive_owner(request)
        if set(body) != {"revision", "changes"} or not isinstance(body["changes"], dict):
            raise HTTPException(422, "Provide a settings revision and changes")
        # Validate registration/owner/model admission before committing mappings.
        from src.decisions.config import resolve_decision_endpoint
        from src.decisions.contracts import DecisionError
        from src.endpoint_resolver import resolve_endpoint_by_id
        from src.chat_automation import validate_configuration
        validate_configuration(body["changes"])
        try:
            from src.model_admission import require_owner_model
            if "decision_provider" in body["changes"]:
                resolve_decision_endpoint(body["changes"]["decision_provider"], owner)
            for mapping in body["changes"].get("approaches", {}).values():
                require_owner_model(owner, mapping["model"])
                if not resolve_endpoint_by_id(mapping["endpoint_id"], mapping["model"], owner=owner, require_exact_model=True):
                    raise HTTPException(422, "An approach model is unavailable")
        except (DecisionError, ValueError):
            raise HTTPException(422, "The decision model is unavailable") from None
        result = AutomationStore().update_configuration(owner, body["changes"], body["revision"])
        return with_review_availability(result, owner, configuration=result)

    @router.get("/api/session/{session_id}/automation")
    async def chat_settings(request: Request, session_id: str):
        owner = interactive_owner(request)
        store = AutomationStore()
        result = store.policy(owner, session_id)
        result['task'] = store.task_state(owner, session_id)
        return with_review_availability(result, owner)

    @router.put("/api/session/{session_id}/automation")
    async def save_chat_settings(request: Request, session_id: str, body: dict):
        owner = interactive_owner(request)
        if set(body) != {"policy_revision", "changes"} or not isinstance(body["changes"], dict):
            raise HTTPException(422, "Provide a chat revision and changes")
        result = AutomationStore().update_chat(owner, session_id, body["changes"], body["policy_revision"])
        return with_review_availability(result, owner)

    return router
