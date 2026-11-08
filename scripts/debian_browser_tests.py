#!/usr/bin/env python3
"""Headless real-app desktop/phone checks over a private loopback bridge.

Chromium must already be installed. Login JSON can arrive on stdin, so the
operator can fetch it through SSH without putting credentials in arguments.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import json
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse


class Browser:
    def __init__(self, executable, profile, log):
        self.process = subprocess.Popen(["bash", "-c", 'exec "$1" --headless --no-first-run --no-default-browser-check --disable-extensions --remote-debugging-pipe --user-data-dir="$2" about:blank 3<&0 4>&1', "cdp", executable, str(profile)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, start_new_session=True)
        self.counter = 0
        self.buffer = b""
        self.session = None
        target = self.call("Target.createTarget", {"url": "about:blank"}, scoped=False)["targetId"]
        self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True}, scoped=False)["sessionId"]
        self.call("Page.enable")

    def call(self, method, params=None, scoped=True):
        self.counter += 1
        request = {"id": self.counter, "method": method, "params": params or {}}
        if scoped and self.session:
            request["sessionId"] = self.session
        self.process.stdin.write(json.dumps(request).encode() + b"\0")
        self.process.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if b"\0" not in self.buffer:
                if not select.select([self.process.stdout], [], [], 1)[0]:
                    continue
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("browser_closed")
                self.buffer += chunk
                continue
            raw, self.buffer = self.buffer.split(b"\0", 1)
            if not raw:
                continue
            response = json.loads(raw)
            if response.get("id") == self.counter:
                if "error" in response:
                    raise RuntimeError("browser_protocol_error")
                return response.get("result", {})
        raise TimeoutError("browser_protocol_deadline")

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in response:
            raise RuntimeError("page_evaluation_failed")
        return response.get("result", {}).get("value")

    def wait(self, expression):
        for _ in range(150):
            if self.evaluate(expression):
                return
            time.sleep(.2)
        raise TimeoutError("page_readiness_deadline")

    def close(self):
        try:
            self.call("Browser.close", scoped=False)
        except (OSError, RuntimeError, TimeoutError):
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7824")
    parser.add_argument("--login-file", default="-", help="Private JSON file, or - for stdin")
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--chromium", default=shutil.which("chromium"))
    parser.add_argument("--isolated-state", action="store_true", help="This test can materialize disposable empty chats")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    parsed = urllib.parse.urlsplit(args.origin)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        parser.error("origin must be a private loopback bridge")
    if not args.isolated_state:
        parser.error("browser mutation checks require a disposable state copy")
    if not args.chromium:
        parser.error("a preinstalled Chromium executable is required")
    if args.login_file == "-":
        raw = sys.stdin.buffer.read(16385)
    else:
        login_path = Path(args.login_file)
        info = login_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            parser.error("login file must be private and regular")
        with login_path.open("rb") as stream:
            raw = stream.read(16385)
    if len(raw) > 16384:
        parser.error("login JSON is too large")
    login = json.loads(raw)
    if not all(isinstance(login.get(k), str) and login[k] for k in ("username", "password")):
        parser.error("login JSON requires username/password")
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows = []
    with tempfile.TemporaryDirectory(prefix="odysseus-browser-") as temporary:
        log_path = args.output.with_suffix(".browser.log")
        with log_path.open("w") as log:
            browser = Browser(args.chromium, Path(temporary) / "profile", log)
            try:
                for width in (1440, 390):
                    browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": width < 500})
                    browser.call("Page.navigate", {"url": args.origin})
                    browser.wait("!!document.querySelector('#username') || !!document.querySelector('.model-picker-btn')")
                    if browser.evaluate("!!document.querySelector('#username')"):
                        status = browser.evaluate("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(" + json.dumps({k: login[k] for k in ("username", "password")}) + ")}).then(r=>r.status)")
                        if status != 200:
                            raise RuntimeError("login_http_" + str(status))
                        browser.call("Page.navigate", {"url": args.origin})
                    browser.wait("!!document.querySelector('.model-picker-logo svg') && !document.querySelector('#chat-approval-mode')?.disabled")
                    browser.evaluate("document.fonts.ready.then(() => true)")
                    actual = browser.evaluate("fetch('/api/version').then(r=>r.json()).then(v=>v.source_commit)")
                    if actual != args.expected_source:
                        raise RuntimeError("wrong_deployed_source")
                    def check(case_id, expression, label):
                        try:
                            evidence = browser.evaluate(expression)
                            status = "passed" if evidence else "failed"
                            rows.append({"id": case_id, "status": status, "profile": "browser", "variant": str(width), "evidence": {label: evidence}})
                        except (RuntimeError, TimeoutError):
                            rows.append({"id": case_id, "status": "failed", "profile": "browser", "variant": str(width), "reason": label + "_evaluation_failed"})
                    check("UI-02", "(() => {const input=Array.from(document.querySelectorAll('textarea')).find(e=>e.getClientRects().length&&!e.disabled);if(!input)return false;input.value='Synthetic composer text '.repeat(100);input.dispatchEvent(new Event('input',{bubbles:true}));return true})()", "long_text_entered")
                    time.sleep(.4)
                    check("UI-02", "(() => {const b=document.querySelector('.model-picker-btn'),r=b.getBoundingClientRect(),s=b.querySelector('.model-picker-logo svg'),l=document.querySelector('#model-picker-label');return !!s && getComputedStyle(l).fontSize==='12px' && getComputedStyle(s).width==='14px' && r.x>=0 && r.right<=innerWidth && r.y>=0 && r.bottom<=innerHeight && !l.textContent.includes('OpenAI ·') && !l.textContent.includes('Google Gemini ·') && l.title.includes(l.textContent.trim())})()", "model_control_visible_while_typing")
                    check("UI-01", "(async () => {const button=document.querySelector('#chat-approval-mode'),menu=document.querySelector('#chat-permissions-menu');for(let i=0;i<5;i++){let end=performance.now()+4000;while(button.disabled&&performance.now()<end)await new Promise(r=>setTimeout(r,20));button.click();while(menu.hidden&&performance.now()<end)await new Promise(r=>setTimeout(r,20));if(menu.hidden || !menu.textContent.includes('Auto-Approve') || menu.querySelectorAll('[data-approval-mode] svg').length!==3)return false;for(let frame=0;frame<3;frame++){await new Promise(requestAnimationFrame);if(menu.hidden || menu.getAttribute('role')!=='menu')return false;}button.click();await new Promise(requestAnimationFrame);if(!menu.hidden)return false;}return true})()", "permission_menu_stable_with_three_icons")
                    scroll = browser.evaluate("(() => {const e=Array.from(document.querySelectorAll('textarea')).find(e=>e.getClientRects().length&&!e.disabled),r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,top:e.scrollTop,font:getComputedStyle(e).fontSize}})()")
                    browser.call("Input.dispatchMouseEvent", {"type":"mouseWheel", "x":scroll["x"], "y":scroll["y"], "deltaX":0, "deltaY":120})
                    time.sleep(.3)
                    check("UI-04", "(() => {const e=Array.from(document.querySelectorAll('textarea')).find(e=>e.getClientRects().length&&!e.disabled);return e.scrollTop>" + str(scroll["top"]) + " && getComputedStyle(e).fontSize===" + json.dumps(scroll["font"]) + " && getComputedStyle(document.body).zoom==='1'})()", "real_composer_scroll_preserves_scale")
                    rect = browser.evaluate("(() => {const r=document.querySelector('.model-picker-wrap').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scale:1}})()")
                    png = browser.call("Page.captureScreenshot", {"format": "png", "clip": rect})["data"]
                    (args.output.parent / ("composer-" + str(width) + ".png")).write_bytes(base64.b64decode(png))
            finally:
                browser.close()
        log_path.chmod(0o600)
    report = {"schema": "odysseus-debian-supplement/v1", "expected_source": args.expected_source, "results": rows}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps({"counts": dict(Counter(r["status"] for r in rows)), "output": str(args.output)}))
    return int(any(r["status"] != "passed" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
