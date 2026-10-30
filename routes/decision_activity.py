"""Interactive owner-only observation routes; every GET is inference-free."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from routes.chat_automation_routes import interactive_owner
from src.decisions.contracts import encode
from src.decisions.observability import default_observability


def setup_decision_activity_routes(session_manager=None, *, service=None):
    def store():
        return service if service is not None else default_observability()

    async def expire_captures():
        while True:
            try:
                await asyncio.to_thread(store().purge_expired)
            except Exception:
                # Access still checks expiry if a periodic cleanup was delayed.
                pass
            await asyncio.sleep(60)

    @asynccontextmanager
    async def retention_lifespan(_app):
        retention_task = asyncio.create_task(expire_captures())
        try:
            yield
        finally:
            retention_task.cancel()
            try:
                await retention_task
            except asyncio.CancelledError:
                pass

    router = APIRouter(tags=["decision activity"], lifespan=retention_lifespan)

    @router.get("/api/session/{session_id}/decisions")
    async def history(request: Request, session_id: str, before: str | None = None,
                      limit: int = Query(50, ge=1, le=100)):
        owner = interactive_owner(request)
        return await asyncio.to_thread(store().history, owner, session_id, before=before, limit=limit)

    @router.get("/api/session/{session_id}/decisions/live")
    async def poll(request: Request, session_id: str):
        # Bounded polling also sees settlements of already-published reserved
        # attempts. There is no scheduler/event_bus subscription or inference.
        owner = interactive_owner(request)
        result = await asyncio.to_thread(store().history, owner, session_id, limit=50)
        return Response(encode(result), media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    @router.get("/api/session/{session_id}/decisions/{run_id}/export")
    async def export(request: Request, session_id: str, run_id: str):
        owner = interactive_owner(request)
        result = await asyncio.to_thread(store().history, owner, session_id, run_id=run_id, limit=100)
        return Response(encode(result), media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="decision-metadata.json"', "Cache-Control": "no-store"})

    @router.put("/api/session/{session_id}/decision-capture")
    async def consent(request: Request, session_id: str, body: dict):
        owner = interactive_owner(request)
        return await asyncio.to_thread(store().consent, owner, session_id, body)

    @router.delete("/api/session/{session_id}/decision-capture")
    async def remove_all(request: Request, session_id: str):
        owner = interactive_owner(request)
        return await asyncio.to_thread(store().delete_capture, owner, session_id)

    @router.get("/api/session/{session_id}/decisions/{run_id}/capture")
    async def capture(request: Request, session_id: str, run_id: str):
        owner = interactive_owner(request)
        result = await asyncio.to_thread(store().capture, owner, session_id, run_id)
        return Response(encode(result), media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="redacted-decision-capture.json"', "Cache-Control": "no-store"})

    @router.delete("/api/session/{session_id}/decisions/{run_id}/capture")
    async def remove_run(request: Request, session_id: str, run_id: str):
        owner = interactive_owner(request)
        return await asyncio.to_thread(store().delete_capture, owner, session_id, run_id)

    @router.get("/api/session/{session_id}/decision-handoffs")
    async def handoffs(request: Request, session_id: str):
        owner = interactive_owner(request)
        return {"items": await asyncio.to_thread(store().handoffs, owner, session_id)}

    return router
