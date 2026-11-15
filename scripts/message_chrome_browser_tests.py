#!/usr/bin/env python3
"""Check sidebar targets and message footers on a disposable deployed app.

Uses the existing Chromium CDP driver; no provider calls or build are needed.
"""
import argparse
import base64
import json
from pathlib import Path
import shutil
import stat
import tempfile
import time
from urllib.parse import urlsplit

from debian_browser_tests import Browser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7826")
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--login-file", type=Path, required=True)
    parser.add_argument("--isolated-state", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--suite", choices=("all", "sidebar", "footer"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium", default=shutil.which("chromium"))
    args = parser.parse_args()
    url = urlsplit(args.origin)
    if (not args.isolated_state or url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost"}
            or url.username or url.password or url.path not in {"", "/"}
            or url.query or url.fragment):
        parser.error("requires an explicit disposable-state loopback fixture")
    info = args.login_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        parser.error("login file must be private and regular")
    if not args.chromium:
        parser.error("preinstalled Chromium required")
    login = json.loads(args.login_file.read_text())
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows = []
    with tempfile.TemporaryDirectory(prefix="odysseus-message-chrome-") as temporary:
        with (args.output / "browser.log").open("w") as log:
            browser = Browser(args.chromium, Path(temporary) / "profile", log)
            try:
                browser.call("Page.navigate", {"url": args.origin})
                browser.wait("!!document.querySelector('#username') || !!document.querySelector('#tools-section .section-collapse-btn')")
                status = browser.evaluate("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify("
                                          + json.dumps({k: login[k] for k in ("username", "password")})
                                          + ")}).then(r=>r.status)")
                assert status == 200, "fixture login failed"
                browser.call("Page.navigate", {"url": args.origin})
                browser.wait("!!document.querySelector('#tools-section .section-collapse-btn')")
                actual = browser.evaluate("fetch('/api/version').then(r=>r.json()).then(v=>v.source_commit)")
                assert actual == args.expected_source, "wrong source"
                browser.evaluate("document.fonts.ready.then(()=>true)")

                def check(name, expression):
                    evidence = browser.evaluate(expression)
                    passed = bool(evidence.get("ok")) if isinstance(evidence, dict) else bool(evidence)
                    rows.append({"id": name, "passed": passed, "width": width, "evidence": evidence})
                    if not args.baseline:
                        assert passed, name

                for width in (1440, 390, 320):
                    browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 1000,
                                 "deviceScaleFactor": 1, "mobile": width < 500})
                    browser.call("Emulation.setTouchEmulationEnabled", {"enabled": width < 500})
                    if args.suite in ("all", "sidebar"):
                        browser.evaluate("document.getElementById('sidebar').classList.remove('hidden'); document.body.classList.remove('sidebar-collapsed'); document.getElementById('tools-section').classList.remove('collapsed');document.getElementById('sessions-section').classList.remove('collapsed');document.getElementById('sessions-section').style.setProperty('display','block','important')")
                        time.sleep(.35)
                        check("sidebar-targets", """(() => {
                          const selectors=['#session-sort-btn','#email-compose-btn','#library-new-doc-btn','#notes-new-note-btn','#tools-section .section-collapse-btn','#user-bar-settings'];
                          const targets=selectors.map(s=>{const b=document.querySelector(s),r=b.getBoundingClientRect(),i=b.querySelector('svg').getBoundingClientRect();return {selector:s,width:r.width,height:r.height,icon:i.width}});
                          const min=innerWidth<500?44:32;
                          return {ok:targets.every(r=>r.width>=min&&r.height>=min&&r.icon>=18),targets};
                        })()""")
                        point = browser.evaluate("""(() => {const b=document.querySelector('#tools-section .section-collapse-btn');b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return {x:r.left+3,y:r.top+r.height/2}})()""")
                        for kind in ("mousePressed", "mouseReleased"):
                            browser.call("Input.dispatchMouseEvent", {"type": kind, **point, "button": "left", "clickCount": 1})
                        time.sleep(.7)
                        check("sidebar-padding-click", "document.getElementById('tools-section').classList.contains('collapsed')")
                        browser.evaluate("document.querySelector('#tools-section .section-collapse-btn').focus()")
                        for kind in ("keyDown", "keyUp"):
                            browser.call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, **({"text":"\r"} if kind == "keyDown" else {})})
                        time.sleep(.7)
                        check("sidebar-keyboard-toggle", "!document.getElementById('tools-section').classList.contains('collapsed')")
                        if width == 1440:
                            png = browser.call("Page.captureScreenshot", {"format": "png"})["data"]
                            (args.output / "sidebar-desktop.png").write_bytes(base64.b64decode(png))

                    if args.suite in ("all", "footer"):
                        browser.evaluate("""(async () => {
                          document.getElementById('sidebar').classList.toggle('hidden',innerWidth<500);document.body.classList.toggle('sidebar-collapsed',innerWidth<500);
                          const url=[...document.scripts].find(s=>s.src.includes('/chatRenderer.js')).src;
                          window.probeRenderer=await import(url);
                          document.querySelector('#chat-history').replaceChildren();
                          const u=probeRenderer.addMessage('user','Hi, please show the result.','', {});
                          u.id='probe-user';
                          const a=probeRenderer.addMessage('assistant','Hello! Here is the result.','gemma-4-26b-a4b-it',
                            {thinking:'This synthetic explanation belongs inside the plate.',tokens_per_second:4.49,response_time:1,input_tokens:100,output_tokens:20,context_percent:1,context_length:128000,model:'gemma-4-26b-a4b-it'});
                          a.id='probe-ai';
                          document.querySelector('#chat-history').scrollTop=0;
                        })()""")
                        time.sleep(.3)
                        check("footer-below-both-plates", """(() => {
                          const boxes=['probe-user','probe-ai'].map(id=>{const m=document.getElementById(id),p=m.querySelector(':scope > .msg-plate'),f=m.querySelector(':scope > .msg-footer');if(!p||!f)return {id,ok:false};const a=p.getBoundingClientRect(),b=f.getBoundingClientRect();return {id,ok:!p.contains(f)&&b.top>=a.bottom&&a.left>=0&&a.right<=Math.min(innerWidth,visualViewport.width)+1,plateBottom:a.bottom,footerTop:b.top,right:a.right,viewport:visualViewport.width}});
                          return {ok:boxes.every(b=>b.ok),boxes};
                        })()""")
                        check("metrics-and-context-once", "document.querySelectorAll('#probe-ai > .msg-footer .response-metrics').length===1 && document.querySelectorAll('#probe-ai > .msg-footer .ctx-ring').length===1")
                        check("copy-answer-without-reasoning", "probeRenderer.copyMessageText(document.querySelector('#probe-ai'))==='Hello! Here is the result.'")
                        check("thinking-inside-plate", "!!document.querySelector('#probe-ai > .msg-plate .thinking-section')")
                        browser.evaluate("document.querySelector('#probe-ai .response-metrics').click()")
                        check("metrics-popup", "!!document.querySelector('.ctx-popup') && document.querySelector('.ctx-popup').textContent.includes('4.49 tok/s')")
                        browser.evaluate("document.querySelectorAll('.ctx-popup').forEach(p=>p._dismiss?p._dismiss():p.remove());document.querySelector('#probe-ai .ctx-ring').click()")
                        check("context-popup", "!!document.querySelector('.ctx-detail-popup')")
                        browser.evaluate("document.querySelectorAll('.ctx-detail-popup').forEach(p=>p._dismiss?p._dismiss():p.remove());document.querySelector('#probe-user .msg-more-btn').click()")
                        check("user-menu-edit", "!![...document.querySelectorAll('.msg-overflow-item')].find(e=>e.textContent.includes('Edit'))")
                        browser.evaluate("document.querySelectorAll('.msg-overflow-menu').forEach(p=>p._dismiss?p._dismiss():p.remove());document.querySelector('#probe-ai .msg-more-btn').click()")
                        check("assistant-menu", """(() => {const e=document.querySelector('.msg-overflow-menu'),r=e?.getBoundingClientRect();return !!r&&r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight})()""")
                        browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
                        check("user-inline-edit-cancel", """(async()=>{const m=document.querySelector('#probe-user');await window.chatModule.editUserMessage(m);const inside=!!m.querySelector('.msg-plate .edit-textarea');m.querySelector('.edit-cancel-btn').click();return inside&&m.querySelector('.body').textContent.includes('please show')&&!m.querySelector('textarea')})()""")
                        check("assistant-inline-edit-cancel", """(async()=>{const m=document.querySelector('#probe-ai');await window.chatModule.editAIMessage(m);const inside=!!m.querySelector('.msg-plate .msg-edit-textarea');m.querySelector('.msg-edit-cancel').click();return inside&&!m.querySelector('textarea')&&getComputedStyle(m.querySelector('.body')).display!=='none'})()""")
                        check("expanded-thinking-keeps-footer-below", """(async()=>{const m=document.querySelector('#probe-ai');m.querySelector('.thinking-header').click();await new Promise(r=>setTimeout(r,350));return m.querySelector('.msg-footer').getBoundingClientRect().top>=m.querySelector('.msg-plate').getBoundingClientRect().bottom})()""")
                        check("footer-replacement-keeps-one-plate", """(() => {const m=document.querySelector('#probe-ai'),body=m.querySelector('.body');m.querySelector('.msg-footer').remove();m.append(probeRenderer.createMsgFooter(m));probeRenderer.displayMetrics(m,{tokens_per_second:4.49,response_time:1,context_percent:1,input_tokens:100,context_length:128000,model:'gemma-4-26b-a4b-it'});return m.querySelectorAll('.msg-plate').length===1&&m.querySelector('.body')===body&&m.querySelectorAll('.msg-footer').length===1})()""")
                        check("image-footer-outside-plate", """(()=>{const m=probeRenderer.buildImageBubble('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jMZkAAAAASUVORK5CYII=','Synthetic image','image', '1x1','standard');document.querySelector('#chat-history').append(m);const ok=!!m.querySelector(':scope > .msg-plate .body')&&!!m.querySelector(':scope > .msg-footer');m.remove();return ok})()""")
                        check("thread-footer-stays-unwrapped", """(()=>{const m=document.createElement('div');m.className='agent-thread';m.append(probeRenderer.createMsgFooter(m));return !m.querySelector('.msg-plate')&&m.querySelectorAll('.msg-footer').length===1})()""")
                        png = browser.call("Page.captureScreenshot", {"format": "png"})["data"]
                        (args.output / ("messages-" + str(width) + ".png")).write_bytes(base64.b64decode(png))
            except Exception as error:
                rows.append({"id": "runtime", "passed": False, "reason": str(error)[:200]})
            finally:
                browser.close()
    (args.output / "results.json").write_text(json.dumps({"source":args.expected_source,"baseline":args.baseline,"results":rows},indent=2)+"\n")
    counts = {"passed":sum(r["passed"] for r in rows),"failed":sum(not r["passed"] for r in rows)}
    print(json.dumps(counts))
    return int(not args.baseline and bool(counts["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
