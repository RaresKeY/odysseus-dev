"""Speech requests keep a bounded budget for cold starts and CPU inference."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("configured_timeout", [None, "0.13"])
def test_stt_request_budget_with_real_app_middleware(tmp_path, configured_timeout):
    environment = os.environ.copy()
    environment.pop("REQUEST_HARD_TIMEOUT", None)
    if configured_timeout is not None:
        environment["REQUEST_HARD_TIMEOUT"] = configured_timeout
    environment.update({
        "AUTH_ENABLED": "false",
        "CHROMADB_CONNECT_TIMEOUT": "0.01",
        "CHROMADB_HOST": "127.0.0.1",
        "CHROMADB_PORT": "9",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
        "ODYSSEUS_DATA_DIR": str(tmp_path),
        "ODYSSEUS_DISABLE_MCP": "1",
        "ODYSSEUS_INPROCESS_TASKS": "0",
        "ODYSSEUS_INPROCESS_POLLERS": "0",
        "OPENAI_API_KEY": "",
        "PYTHONPATH": os.pathsep.join(filter(None, (str(ROOT), environment.get("PYTHONPATH", "")))),
        "PYTHON_DOTENV_DISABLED": "1",
    })
    probe = textwrap.dedent(
        """
        import asyncio
        import json
        import time
        from fastapi.testclient import TestClient
        import app as app_module

        configured = [app_module.REQUEST_HARD_TIMEOUT, app_module._STT_REQUEST_TIMEOUT]
        app_module.REQUEST_HARD_TIMEOUT = 0.05
        app_module._STT_REQUEST_TIMEOUT = 0.5
        service = app_module.stt_service
        type(service).available = property(lambda self: True)

        def transcribe(audio):
            time.sleep(0.12)
            return "Recognized speech."

        def stats():
            time.sleep(0.12)
            return {"available": True}

        async def lookalike():
            await asyncio.sleep(0.12)
            return {"reached": True}

        service.transcribe = transcribe
        service.get_stats = stats
        app_module.app.add_api_route(
            "/api/stt/transcribe-extra", lookalike, methods=["POST"],
        )
        results = {"configured": configured}
        for root_path in ("", "/odysseus"):
            client = TestClient(app_module.app, root_path=root_path)
            response = client.post(
                root_path + "/api/stt/transcribe",
                files={"file": ("speech.webm", b"fixture audio", "audio/webm")},
            )
            results[root_path or "unmounted"] = {
                "status": response.status_code,
                "body": response.json(),
            }
            client.close()

        client = TestClient(app_module.app)
        results["stats"] = client.get("/api/stt/stats").status_code
        results["lookalike"] = client.post("/api/stt/transcribe-extra").status_code
        app_module._STT_REQUEST_TIMEOUT = 0.05
        results["bounded"] = client.post(
            "/api/stt/transcribe",
            files={"file": ("speech.webm", b"fixture audio", "audio/webm")},
        ).status_code
        client.close()
        print("RESULT=" + json.dumps(results))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stderr
    result_line = next(
        (line for line in result.stdout.splitlines() if line.startswith("RESULT=")),
        None,
    )
    assert result_line is not None, result.stdout
    payload = json.loads(result_line.removeprefix("RESULT="))
    expected = [45.0, 420.0] if configured_timeout is None else [0.13, 0.13]
    assert payload["configured"] == expected
    for key in ("unmounted", "/odysseus"):
        assert payload[key] == {
            "status": 200,
            "body": {"text": "Recognized speech."},
        }
    assert payload["stats"] == 504
    assert payload["lookalike"] == 504
    assert payload["bounded"] == 504
