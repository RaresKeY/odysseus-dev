"""Human-only enrolment, scope and pixels for the narrow private producer."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from routes.chat_automation_routes import interactive_owner
from src.chat_automation import AutomationStore

_PIXELS = {}


def _owner(request, session_id):
    owner = interactive_owner(request)
    AutomationStore().policy(owner, session_id)
    if not owner:
        raise HTTPException(409, "Sign in to use a private browser")
    return owner.strip().casefold()


def _record(owner, session_id):
    from src.browser_pipe import get
    record = get(owner, session_id)
    if record is None or not record.live:
        raise HTTPException(409, "Open the private browser first")
    return record


def _grant(owner, sid, body):
    from src.browser_pipe import BrowserTaskGrant, origin
    if (not isinstance(body, dict) or set(body) - {"url", "effects", "fields"}
            or not isinstance(body.get("url"), str) or not isinstance(body.get("effects", []), list)
            or not isinstance(body.get("fields", []), list) or len(body.get("fields", [])) > 12):
        raise HTTPException(422, "Choose a page, allowed actions and optional named fields")
    try:
        fields = {}
        for item in body.get("fields", []):
            if (not isinstance(item, dict) or set(item) != {"label", "value"}
                    or not isinstance(item["label"], str) or not item["label"] or item["label"] in fields
                    or not isinstance(item["value"], str)):
                raise ValueError()
            fields[item["label"]] = item["value"]
        return BrowserTaskGrant(owner, sid, 'human-' + uuid.uuid4().hex,
            frozenset({origin(body["url"])}), frozenset(body.get("effects", [])), fields,
            time.time() + 120, frozenset({body["url"]}))
    except (ValueError, TypeError):
        raise HTTPException(422, "The page or allowed actions are invalid") from None


async def _pixels(owner, sid, image):
    # No provider/model content is used as a filename or artifact identity.
    now = time.time()
    for key, item in list(_PIXELS.items()):
        if item["expires"] <= now:
            _PIXELS.pop(key)
    owned = [(key, item) for key, item in _PIXELS.items() if (item["owner"], item["sid"]) == (owner, sid)]
    while len(owned) >= 5:
        key, _ = owned.pop(0)
        _PIXELS.pop(key, None)
    data = image["data"]
    if not isinstance(data, bytes) or len(data) > 2_000_000 or hashlib.sha256(data).hexdigest() != image["sha256"]:
        raise HTTPException(502, "The browser image was invalid")
    identifier = uuid.uuid4().hex
    _PIXELS[identifier] = {"owner": owner, "sid": sid, "data": data, "expires": now + 120}
    return {"url": f"/api/session/{sid}/browser/image/{identifier}",
            **{k: image[k] for k in ("sha256", "width", "height")},
            "actor": "browser", "kind": "actual_screenshot", "atomic_document_capture": False}


def setup_browser_control_routes():
    router = APIRouter(tags=["private browser"])

    @router.post("/api/session/{session_id}/browser")
    async def open_browser(request: Request, session_id: str):
        owner = _owner(request, session_id)
        from src.browser_pipe import ProducerManifest, enrol, BrowserPipeError
        from src.constants import DATA_DIR
        manifest_path = os.environ.get("ODYSSEUS_BROWSER_PRODUCER_MANIFEST", "/etc/odysseus/browser-pipe-producer.json")
        try:
            manifest = ProducerManifest.load(manifest_path)
            record = await enrol(owner, session_id, manifest, Path(DATA_DIR) / 'browser-pipe-profiles')
            record.artifact_writer = lambda image: _pixels(owner, session_id, image)
            return {"status": "open", "actor": "human", "producer": "odysseus-cdp-pipe"}
        except (OSError, ValueError, BrowserPipeError):
            raise HTTPException(503, "The private browser is unavailable: a qualified sandbox runtime is required") from None

    @router.delete("/api/session/{session_id}/browser")
    async def close_browser(request: Request, session_id: str):
        owner = _owner(request, session_id)
        from src.browser_pipe import close
        outcome = await close(owner, session_id)
        for key, image in list(_PIXELS.items()):
            if (image["owner"], image["sid"]) == (owner, session_id):
                _PIXELS.pop(key, None)
        return {"status": "closed" if outcome is None or outcome.dead else "cleanup_incomplete",
                "actor": "human", "teardown": outcome.to_dict() if outcome else None}

    @router.post("/api/session/{session_id}/browser/scope")
    async def scope(request: Request, session_id: str, body: dict):
        owner = _owner(request, session_id)
        record = _record(owner, session_id)
        grant = _grant(owner, session_id, body)
        policy = AutomationStore().policy(owner, session_id)
        record.pending_task_grant = (grant, policy["policy_revision"], policy["authority_epoch"])
        record.human_scope = grant
        return {"status": "ready_for_next_message", "expires_in_seconds": 120,
                "origins": sorted(grant.allowed_origins), "effects": sorted(grant.allowed_effects), "actor": "human"}

    @router.get("/api/session/{session_id}/browser/view")
    async def view(request: Request, session_id: str):
        owner = _owner(request, session_id)
        record = _record(owner, session_id)
        # Taking control revokes automation before new observations are shown.
        record.task_grant = None
        record.invalidate_document()
        observed = await record.observe()
        captured = await record.screenshot()
        artifact = await _pixels(owner, session_id, captured["screenshot"])
        return {"status": "human_control", "actor": "human", "observation": observed, "screenshot": artifact}

    @router.post("/api/session/{session_id}/browser/action")
    async def action(request: Request, session_id: str, body: dict):
        owner = _owner(request, session_id)
        record = _record(owner, session_id)
        grant = getattr(record, 'human_scope', None)
        if grant is None:
            raise HTTPException(409, "Set this browser’s page and allowed actions first")
        try:
            result = await record.human_control(body, grant=grant)
            return {**result, "actor": "human"}
        except ValueError:
            raise HTTPException(409, "The browser target or permission changed; view the page again") from None

    @router.get("/api/session/{session_id}/browser/image/{identifier}")
    async def image(request: Request, session_id: str, identifier: str):
        owner = _owner(request, session_id)
        item = _PIXELS.get(identifier)
        if not item or (item["owner"], item["sid"]) != (owner, session_id) or item["expires"] <= time.time():
            raise HTTPException(404, "Browser image expired")
        return Response(item["data"], media_type='image/png', headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    return router
