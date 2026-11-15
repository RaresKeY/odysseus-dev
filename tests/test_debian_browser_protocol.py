"""Browser qualification must retain replies across intercepted requests."""
import io
import json
from types import SimpleNamespace

import pytest
from scripts import debian_browser_tests as driver
from tests.helpers.cli_loader import load_script


@pytest.mark.parametrize("url,method", [
    ("https://external.invalid/asset.js", "Fetch.failRequest"),
    ("http://127.0.0.1:7826/static/app.js", "Fetch.continueRequest"),
    ("http://169.254.169.254/metadata", "Fetch.failRequest"),
])
def test_intercepted_request_does_not_lose_the_outstanding_reply(monkeypatch, url, method):
    browser = driver.Browser.__new__(driver.Browser)
    browser.counter, browser.session = 0, "tab"
    browser.process = SimpleNamespace(stdin=io.BytesIO(), stdout=object())
    events = [
        {"method": "Fetch.requestPaused", "sessionId": "tab", "params": {
            "requestId": "resource", "request": {"url": url}}},
        {"id": 1, "result": {"received": True}},
    ]
    browser.buffer = b"\0".join(json.dumps(event).encode() for event in events) + b"\0"
    assert browser.call("Page.enable") == {"received": True}
    writes = [json.loads(raw) for raw in browser.process.stdin.getvalue().split(b"\0") if raw]
    assert writes[1]["method"] == method
    assert writes[1]["params"]["requestId"] == "resource"
    assert writes[1]["sessionId"] == "tab"
    if method == "Fetch.failRequest":
        assert writes[1]["params"]["errorReason"] == "BlockedByClient"


def test_wrong_source_file_check_keeps_original_failure_and_writes_report(monkeypatch, tmp_path):
    monkeypatch.setitem(__import__("sys").modules, "debian_browser_tests", driver)
    script = load_script("workspace_files_browser_tests.py")
    class FakeBrowser:
        def __init__(self, *_): pass
        def call(self, method, *_):
            return {"data": ""} if method == "Page.captureScreenshot" else {}
        def wait(self, *_): pass
        def evaluate(self, expression):
            return "wrong-source" if "/api/version" in expression else {}
        def close(self): pass

    monkeypatch.setattr(script, "Browser", FakeBrowser)
    output = tmp_path / "evidence"
    monkeypatch.setattr(__import__("sys"), "argv", ["workspace_files_browser_tests.py",
        "--isolated-state", "--expected-source", "a"*40, "--chromium", "/unused/chromium",
        "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        script.main()
    assert error.value.code == 1
    report = json.loads((output / "checks.json").read_text())
    assert report["passed"] is False
    assert report["checks"][-1]["evidence"] == "RuntimeError: wrong_fixture_source"
