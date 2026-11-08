#!/usr/bin/env python3
"""Real file-panel checks against explicitly disposable Debian application state."""
import argparse
import base64
import json
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urlsplit

from debian_browser_tests import Browser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:7826")
    parser.add_argument("--workspace", default="/app/data/agent_workspace")
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--isolated-state", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--login-file", type=Path)
    parser.add_argument("--chromium", default=shutil.which("chromium"))
    args = parser.parse_args()
    parsed = urlsplit(args.origin)
    if not args.isolated_state or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.scheme != "http" or parsed.path not in {"", "/"}:
        parser.error("requires an explicit disposable-state loopback fixture")
    if not args.chromium:
        parser.error("preinstalled Chromium required")
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []

    def literal(value):
        return json.dumps(value)

    with tempfile.TemporaryDirectory(prefix="odysseus-files-browser-") as temporary:
        with (args.output / "browser.log").open("w") as log:
            browser = Browser(args.chromium, Path(temporary) / "profile", log)
            try:
                browser.call("Page.navigate", {"url": args.origin})
                if args.login_file:
                    browser.wait("!!document.querySelector('#username') || !!document.getElementById('chat-files-toggle')")
                    login=json.loads(args.login_file.read_text())
                    if args.login_file.stat().st_mode & 0o077:
                        raise RuntimeError("private_login_file_required")
                    response=browser.evaluate("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify("+literal({key:login[key] for key in ('username','password')})+")}).then(r=>r.status)")
                    if response!=200: raise RuntimeError("fixture_login_failed")
                    browser.call("Page.navigate", {"url": args.origin})
                browser.wait("!!document.getElementById('chat-files-toggle')")
                actual = browser.evaluate("fetch('/api/version').then(r=>r.json()).then(v=>v.source_commit)")
                if actual != args.expected_source:
                    raise RuntimeError("wrong_fixture_source")
                browser.evaluate("document.fonts.ready.then(()=>true)")
                browser.evaluate("import('/static/js/workspace.js').then(m=>m.setWorkspace("+literal(args.workspace)+"))")

                def check(name, expression):
                    value = browser.evaluate(expression)
                    rows.append({"id": name, "passed": bool(value), "evidence": value})
                    if not value:
                        raise AssertionError(name)

                def screen(name):
                    raw = browser.call("Page.captureScreenshot", {"format": "png"})["data"]
                    (args.output / (name + ".png")).write_bytes(base64.b64decode(raw))

                def open_file(name):
                    browser.evaluate("import('/static/js/workspaceFiles.js').then(m=>m.openWorkspaceFile("+literal(name)+"))")
                    browser.wait("!!document.querySelector('.files-text:not([hidden])')")

                for width in (1440, 390, 320):
                    browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": width < 500})
                    browser.evaluate(r"""(async()=>{
                      const markdown=await import('/static/js/markdown.js');
                      const box=document.createElement('div');box.className='msg msg-ai';box.id='files-synthetic-message';
                      const body=document.createElement('div');body.className='body';
                      body.innerHTML=markdown.default.mdToHtml('Created `test.txt` with sample text. Also [test.txt](test.txt).');
                      box.append(body);document.getElementById('chat-history').append(box);
                    })()""")
                    browser.wait("document.querySelectorAll('#files-synthetic-message [data-workspace-file]').length===2")
                    check("inline-and-markdown-links-"+str(width), "document.querySelectorAll('#files-synthetic-message button.workspace-file-link').length===1")
                    browser.evaluate("document.querySelector('#files-synthetic-message button').click()")
                    browser.wait("document.querySelector('.files-text:not([hidden])')?.value.includes('sample')")
                    check("layout-"+str(width), """(()=>{
                      const p=document.getElementById('workspace-files-pane'),r=p.getBoundingClientRect();
                      const e=document.querySelector('.files-text').getBoundingClientRect();
                      const tree=document.querySelector('.files-browser').getBoundingClientRect();
                      return {ok:r.x>=-1&&Math.abs(r.right-innerWidth)<2&&e.width>180&&tree.width>120,
                        paneWidth:r.width,editorWidth:e.width,treeWidth:tree.width,modal:p.matches(':modal')};
                    })().ok""")
                    browser.wait("document.querySelectorAll('.files-entry').length>=3")
                    check("tree-"+str(width), "document.querySelectorAll('.files-entry').length>=3")
                    screen("file-"+str(width))
                    browser.evaluate("document.querySelector('[aria-label=\"Close files\"]').click()")
                    check("close-focus-"+str(width), "document.activeElement?.matches('#files-synthetic-message button')")
                    check("trigger-bounds-"+str(width), "(()=>{const r=document.getElementById('chat-panel-toggle').getBoundingClientRect();return r.x>=-1&&r.right<=innerWidth+1&&r.width>=32;})()")
                    browser.evaluate("document.getElementById('files-synthetic-message').remove()")

                browser.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
                open_file("test.txt")
                browser.evaluate(r"""(()=>{
                  document.querySelector('[aria-label="Edit"]').click();
                  const e=document.querySelector('.files-text');e.value='sample edited\nsecond line\n';e.dispatchEvent(new Event('input'));
                })()""")
                open_file("nested/readme.md")
                open_file("test.txt")
                check("dirty-draft-retained", r"document.querySelector('.files-text').value==='sample edited\nsecond line\n'")
                browser.evaluate("document.querySelector('[aria-label=\"Save\"]').click()")
                browser.wait("document.querySelector('.files-status').textContent==='Saved'")
                check("saved-bytes-and-crlf", "fetch('/api/workspace/file?'+new URLSearchParams({workspace:"+literal(args.workspace)+r",path:'test.txt'})).then(r=>r.json()).then(d=>d.content==='sample edited\r\nsecond line\r\n')")
                browser.evaluate("document.querySelector('.files-text').value='unsaved draft';document.querySelector('.files-text').dispatchEvent(new Event('input'))")
                check("external-edit", r"""(async()=>{
                  const q=new URLSearchParams({workspace:"""+literal(args.workspace)+r""",path:'test.txt'});
                  const d=await fetch('/api/workspace/file?'+q).then(r=>r.json());
                  return (await fetch('/api/workspace/file',{method:'PUT',headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({workspace:"""+literal(args.workspace)+r""",path:'test.txt',revision:d.revision,content:'sample external\r\n'})})).ok;
                })()""")
                browser.evaluate("document.querySelector('[aria-label=\"Save\"]').click()")
                browser.wait("document.querySelector('.files-status').textContent.includes('changed on disk')")
                check("conflict-preserves-draft", "document.querySelector('.files-text').value==='unsaved draft'")
                browser.evaluate("window.__filesConfirm=window.confirm;window.confirm=()=>true;document.querySelector('[aria-label=\"Discard edits\"]').click();window.confirm=window.__filesConfirm")
                open_file("nested/readme.md")
                browser.evaluate("document.querySelector('[aria-label=\"Preview\"]').click()")
                check("markdown-preview", "!!document.querySelector('.files-preview h1') && !window.filesXss")
                screen("markdown-preview")
                browser.evaluate("document.getElementById('chat-files-toggle').getAttribute('aria-expanded')==='false' && document.getElementById('chat-files-toggle').click()")
                browser.wait("document.getElementById('workspace-files-pane').open")
                check("keyboard-resize", """(()=>{
                  const e=document.querySelector('.files-resizer'),before=e.getAttribute('aria-valuenow');
                  e.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));
                  return Number(e.getAttribute('aria-valuenow'))>Number(before);
                })()""")
                screen("resized")
                browser.evaluate("document.querySelector('[aria-label=\"Close files\"]').click()")
                browser.evaluate("import('/static/js/workspaceFiles.js').then(m=>m.openWorkspaceFile('missing.txt'))")
                browser.wait("document.querySelector('.files-status').textContent.includes('no longer exists')")
                check("missing-clears-preview", "document.querySelector('.files-text').hidden")
                browser.evaluate("import('/static/js/workspaceFiles.js').then(m=>m.openWorkspaceFile('preview.png'))")
                browser.wait("document.querySelector('.files-image')?.naturalWidth>0")
                check("raster-preview", "document.querySelector('.files-image').naturalWidth===1")
                browser.evaluate("import('/static/js/workspaceFiles.js').then(m=>m.openWorkspaceFile('original.pdf'))")
                browser.wait("document.querySelector('.files-status').textContent.includes('Documents')")
                browser.evaluate("document.querySelector('.files-more').open=true;document.querySelector('[aria-label=\"Open in Documents\"]').click()")
                browser.wait("!!window.documentModule.getCurrentDocId() && !document.getElementById('workspace-files-pane').open")
                check("original-pdf-native-editor", "!!window.documentModule.getCurrentDocId()")
                open_file("test.txt")
                browser.evaluate("document.querySelector('.files-more').open=true;document.querySelector('[aria-label=\"Edit a copy in Documents\"]').click()")
                browser.wait("!!window.documentModule.getCurrentDocId() && !!document.getElementById('doc-editor-pane') && !document.getElementById('workspace-files-pane').open")
                check("existing-document-editor", "!!window.documentModule.getCurrentDocId()")
                screen("native-documents-editor")
            except Exception as error:
                rows.append({"id": "browser-error", "passed": False, "evidence": type(error).__name__ + ": " + str(error), "state": browser.evaluate("({status:document.querySelector('.files-status')?.textContent,filePane:document.querySelector('#workspace-files-pane')?.open,doc:window.documentModule?.getCurrentDocId(),nativePane:!!document.getElementById('doc-editor-pane')})")})
                screen("failed-state")
            finally:
                browser.close()
    result = {"source": args.expected_source, "passed": all(row["passed"] for row in rows), "checks": rows}
    (args.output / "checks.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"passed": result["passed"], "checks": len(rows), "evidence": str(args.output)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
