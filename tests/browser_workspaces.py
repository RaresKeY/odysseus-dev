"""Exercise saved workspace choices in the actual app without model requests.

Run python3 -I tests/browser_workspaces.py in the same secretless Chromium
container described by browser_chat_controls.py. Only temporary folders and
browser preferences are changed. REVIEW_BASELINE=1 captures the old picker.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).parent))
from browser_chat_controls import Chrome, OUT

BASELINE = os.environ.get('REVIEW_BASELINE') == '1'
ALPHA = '/state/home/Research project'
BETA = '/state/home/A long workspace name for checking narrow composer layouts'
MISSING = '/state/home/removed-workspace'


def wait(browser, expression):
    for _ in range(100):
        if browser.js(expression):
            return
        time.sleep(.1)
    browser.capture('failure.png')
    raise AssertionError((expression, browser.js("({active:document.activeElement?.outerHTML,workspace:window.workspaceReview?.getWorkspace(),feedback:document.getElementById('workspace-feedback')?.textContent})")))


def load(browser):
    browser.js("window.workspaceNavigationMarker='leaving'")
    browser.call('Page.navigate', {'url': 'http://127.0.0.1:7000/'})
    wait(browser, "!window.workspaceNavigationMarker && document.readyState==='complete' && Boolean(window.chatModule)")
    browser.js("(async()=>{window.workspaceReview=await import('/static/js/workspace.js');return true})()")
    time.sleep(.5)
    browser.js("document.getElementById('mode-agent-btn').click()")
    wait(browser, "document.getElementById('mode-agent-btn').classList.contains('active')")


def choose(browser, path):
    browser.js("workspaceReview.openWorkspaceBrowser()")
    browser.js("document.getElementById('workspace-cur-path').value=" + json.dumps(path))
    browser.js("document.getElementById('workspace-cur-path').dispatchEvent(new Event('input'));document.getElementById('workspace-use').click()")
    wait(browser, "workspaceReview.getWorkspace()===" + json.dumps(path))
    wait(browser, "document.getElementById('workspace-modal').style.display==='none'")


def main():
    for path in (ALPHA, BETA):
        Path(path).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(AUTH_ENABLED='true', LOCALHOST_BYPASS='false', HOME='/state/home',
        ODYSSEUS_DATA_DIR='/app/data', DATABASE_URL='sqlite:////app/data/app.db',
        ODYSSEUS_DISABLE_MCP='1', ODYSSEUS_STARTUP_WARMUPS='0', ODYSSEUS_MODEL_KEEPALIVE='0',
        ODYSSEUS_INPROCESS_TASKS='0', ODYSSEUS_INPROCESS_POLLERS='0', ODYSSEUS_TOOL_INDEX_PREWARM='0',
        ODYSSEUS_TASK_AUTOMATION_ENABLED='0', ODYSSEUS_REQUIRE_TOOL_INDEX_READY='0',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', CHROMADB_HOST='127.0.0.1', CHROMADB_PORT='8100')
    log = (OUT / 'app.log').open('wb')
    app = subprocess.Popen([sys.executable, '-I', '-m', 'uvicorn', 'app:app', '--app-dir', '/app',
        '--host', '127.0.0.1', '--port', '7000', '--no-access-log'], env=env, stdout=log, stderr=log)
    browser = None
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen('http://127.0.0.1:7000/api/version', timeout=1)
                break
            except Exception:
                time.sleep(.25)
        browser = Chrome()
        browser.call('Page.navigate', {'url': 'http://127.0.0.1:7000/login'})
        wait(browser, "document.readyState==='complete'")
        auth = browser.js("""(async()=>{
          const options={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'workspace-review',password:'workspace-browser-review-only'})};
          await fetch('/api/auth/setup',options);
          return (await fetch('/api/auth/login',options)).ok;
        })()""")
        assert auth, 'Temporary browser account login failed'
        results = []
        for width, height in [(1440, 1000), (390, 844)]:
            browser.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': width < 900})
            browser.call('Emulation.setTouchEmulationEnabled', {'enabled': width < 900})
            load(browser)
            if BASELINE:
                browser.js('workspaceReview.openWorkspaceBrowser()')
                time.sleep(1.2)
                browser.capture(f'picker-before-{width}.png')
                results.append({'width': width, 'baseline': True})
                browser.js('workspaceReview.closeWorkspaceBrowser()')
                continue

            # Existing single-folder preferences migrate without losing the choice.
            browser.js("localStorage.setItem('odysseus-workspace'," + json.dumps(ALPHA) + ");localStorage.removeItem('odysseus-saved-workspaces')")
            load(browser)
            assert browser.js("workspaceReview.getSavedWorkspaces().includes(" + json.dumps(ALPHA) + ")")
            choose(browser, BETA)
            chip = browser.js("""(()=>{const chip=document.getElementById('chat-workspace-bar');const x=document.getElementById('workspace-clear-btn');return {visible:!chip.hidden,name:document.getElementById('workspace-indicator-name').textContent,inside:chip.closest('.chat-input-bar')!==null,closeWidth:x.getBoundingClientRect().width,closeHeight:x.getBoundingClientRect().height,overflow:document.documentElement.scrollWidth>innerWidth}})()""")
            assert chip['visible'] and chip['inside'] and not chip['overflow'], chip
            assert chip['name'] == Path(BETA).name, chip
            assert chip['closeWidth'] == chip['closeHeight'] == (44 if width < 900 else 32), chip
            browser.capture(f'workspace-chip-{width}.png')

            # Picker shows selected and saved folders; it never becomes a tool tab.
            browser.js('workspaceReview.openWorkspaceBrowser()')
            time.sleep(1.2)
            assert browser.js("!document.querySelector('#workspace-modal .minimize-btn, #workspace-modal .modal-minimize-btn')")
            assert browser.js("document.querySelector('#workspace-selected .workspace-choice').title===" + json.dumps(BETA))
            assert browser.js("document.querySelector('#workspace-saved-list .workspace-choice').title===" + json.dumps(ALPHA))
            assert browser.js("document.getElementById('workspace-modal').getBoundingClientRect().right<=innerWidth")
            picker_targets = browser.js("[...document.querySelectorAll('#workspace-modal .workspace-remove')].map(x=>{const r=x.getBoundingClientRect();return {width:r.width,height:r.height,left:r.left,right:r.right}})")
            assert len(picker_targets) == 2 and all(0 <= r['left'] < r['right'] <= width and r['width'] == r['height'] == (44 if width < 900 else 32) for r in picker_targets), picker_targets
            browser.capture(f'workspace-picker-{width}.png')

            # Keyboard selection uses the same server vetting as typed paths.
            browser.js("document.querySelector('#workspace-saved-list .workspace-choice').focus()")
            browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13, 'text': '\r'})
            browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
            wait(browser, 'workspaceReview.getWorkspace()===' + json.dumps(ALPHA))
            load(browser)
            assert browser.js('workspaceReview.getWorkspace()===' + json.dumps(ALPHA))
            assert browser.js('workspaceReview.getSavedWorkspaces().includes(' + json.dumps(BETA) + ')')

            # A removed folder fails visibly and preserves the selected workspace.
            browser.js("localStorage.setItem('odysseus-saved-workspaces',JSON.stringify([" + json.dumps(MISSING) + "," + json.dumps(ALPHA) + "," + json.dumps(BETA) + "]))")
            browser.js('workspaceReview.openWorkspaceBrowser()')
            browser.js("document.querySelector('#workspace-saved-list .workspace-choice').click()")
            wait(browser, "document.getElementById('workspace-feedback').textContent.includes('unavailable')")
            assert browser.js('workspaceReview.getWorkspace()===' + json.dumps(ALPHA))
            browser.capture(f'workspace-unavailable-{width}.png')
            browser.js("document.querySelector('#workspace-saved-list .workspace-remove').click()")
            assert browser.js('!workspaceReview.getSavedWorkspaces().includes(' + json.dumps(MISSING) + ')')
            browser.js('workspaceReview.closeWorkspaceBrowser()')

            # Leave hides the chip, keeps the saved folder, and survives reload.
            browser.js("document.getElementById('workspace-clear-btn').click()")
            assert browser.js("workspaceReview.getWorkspace()==='' && document.getElementById('chat-workspace-bar').hidden")
            load(browser)
            assert browser.js("workspaceReview.getWorkspace()==='' && document.getElementById('chat-workspace-bar').hidden")
            assert browser.js('workspaceReview.getSavedWorkspaces().includes(' + json.dumps(ALPHA) + ')')

            # Corrupt/oversized saved state is bounded; paths remain inert text.
            browser.js("localStorage.setItem('odysseus-saved-workspaces','{}')")
            assert browser.js('workspaceReview.getSavedWorkspaces().length===0')
            browser.js("localStorage.setItem('odysseus-saved-workspaces',JSON.stringify([null,3,'',...Array.from({length:25},(_,i)=>'/folder-'+i),'/folder-1']))")
            assert browser.js('workspaceReview.getSavedWorkspaces().length===20')
            browser.js("localStorage.setItem('odysseus-saved-workspaces',JSON.stringify(['/folder-<img src=x onerror=alert(1)>']))")
            browser.js('workspaceReview.openWorkspaceBrowser()')
            assert browser.js("!document.querySelector('#workspace-saved-list img')")
            browser.js('workspaceReview.closeWorkspaceBrowser()')

            # A delayed default or cancelled selection cannot reverse Leave.
            race = browser.js("""(async()=>{
              const actual=window.fetch;let answer;
              localStorage.removeItem('odysseus-workspace');
              window.fetch=(url,options)=>String(url).endsWith('/workspace/default')
                ?new Promise(resolve=>answer=resolve):actual(url,options);
              const fresh=await import('/static/js/workspace.js?workspace-race-review');
              const pending=fresh.loadDefaultWorkspace();fresh.clearWorkspace();
              answer(new Response(JSON.stringify({ok:true,path:'/state/home/Research project'})));
              await pending;window.fetch=actual;
              return fresh.getWorkspace()==='';
            })()""")
            assert race
            browser.js("localStorage.setItem('odysseus-saved-workspaces','[]')")
            choose(browser, ALPHA)
            cancelled = browser.js("""(async()=>{
              await workspaceReview.openWorkspaceBrowser();
              const actual=window.fetch;let answer;
              window.fetch=(url,options)=>String(url).includes('/workspace/vet?')
                ?new Promise(resolve=>answer=resolve):actual(url,options);
              const pending=workspaceReview.vetAndSetWorkspace('/state/home/another-folder');
              workspaceReview.closeWorkspaceBrowser();
              answer(new Response(JSON.stringify({ok:true,path:'/state/home/another-folder'})));
              const result=await pending;window.fetch=actual;
              return !result.ok && workspaceReview.getWorkspace()==='/state/home/Research project';
            })()""")
            assert cancelled
            browser.js("document.getElementById('mode-chat-btn').click()")
            assert browser.js("document.getElementById('chat-workspace-bar').hidden")
            browser.js("document.getElementById('mode-agent-btn').click()")
            assert browser.js("!document.getElementById('chat-workspace-bar').hidden")
            results.append({'width': width, 'chip': chip, 'savedSelectionReload': True, 'keyboardSelection': True,
                'staleFolderRejected': True, 'leavePersists': True, 'defaultRace': True, 'cancelledSelection': True, 'modeVisibility': True,
                'boundedStorage': True, 'inertPathText': True, 'noMinimizedPicker': True})
        (OUT / 'workspace-results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2), flush=True)
    except Exception:
        if browser:
            print(browser.js("({url:location.href,ready:document.readyState,binding:typeof window.workspaceReview})"), flush=True)
            print(json.dumps(browser.events[-15:]), flush=True)
            browser.capture('failure.png')
        raise
    finally:
        if browser:
            browser.process.terminate()
            browser.process.wait(timeout=8)
        app.terminate()
        app.wait(timeout=15)


if __name__ == '__main__':
    main()
