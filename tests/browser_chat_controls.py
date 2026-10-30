"""Check the production UI in a secretless container with Chromium installed.

Mount the project at /app and writable empty data, home and /evidence paths.
Run python3 -I tests/browser_chat_controls.py. API fixtures never submit a turn
or contact a model. REVIEW_BASELINE=1 records comparison data without asserting
that the baseline already implements the fixes.
"""
import base64
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import urllib.request

OUT = Path('/evidence')
OUT.mkdir(exist_ok=True)
BASELINE = os.environ.get('REVIEW_BASELINE') == '1'


class Chrome:
    def __init__(self):
        incoming, self.write = os.pipe()
        self.read, outgoing = os.pipe()
        def descriptors():
            os.dup2(incoming, 3)
            os.dup2(outgoing, 4)
        self.log = (OUT / 'chromium.log').open('wb')
        self.process = subprocess.Popen([
            '/usr/bin/chromium', '--headless=new', '--no-sandbox',
            '--remote-debugging-pipe', '--disable-background-networking',
            '--disable-component-update', '--no-first-run', '--no-default-browser-check',
            '--user-data-dir=/tmp/chrome-review', 'about:blank'],
            pass_fds=tuple({incoming, outgoing, 3, 4}), preexec_fn=descriptors,
            stdout=self.log, stderr=self.log)
        os.close(incoming)
        os.close(outgoing)
        self.events = []
        self.sequence = 0
        self.buffer = b''
        self.session = None
        target = self.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        self.session = self.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        self.call('Page.enable')
        self.call('Page.bringToFront')

    def call(self, method, params=None):
        self.sequence += 1
        message = {'id': self.sequence, 'method': method, 'params': params or {}}
        if self.session:
            message['sessionId'] = self.session
        os.write(self.write, json.dumps(message).encode() + b'\0')
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if b'\0' not in self.buffer:
                if not select.select([self.read], [], [], 1)[0]:
                    continue
                data = os.read(self.read, 4_000_000)
                if not data:
                    raise RuntimeError('Chromium pipe closed')
                self.buffer += data
            while b'\0' in self.buffer:
                packet, self.buffer = self.buffer.split(b'\0', 1)
                result = json.loads(packet)
                if result.get('method', '').startswith('Page.frame'):
                    self.events.append(result)
                if result.get('id') == self.sequence:
                    if 'error' in result:
                        raise RuntimeError(result['error'])
                    return result.get('result', {})
        raise TimeoutError(method)

    def js(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(result['exceptionDetails'])
        return result.get('result', {}).get('value')

    def capture(self, name):
        result = self.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
        (OUT / name).write_bytes(base64.b64decode(result['data']))


FIXTURE = r"""(async () => {
    const realFetch = window.fetch.bind(window);
    window.fixtureContext = {session_id:'ui-fixture',model:'gpt-5',endpoint_url:'https://api.openai.com/v1',used_tokens:83,
      context_length:100000,context_percent:.1,messages:4,context_messages:4,auto_compact_threshold:85,
      temperature_control:{enabled:false,reason:'This provider and model do not receive the temperature setting.'}};
    window.fixtureAttempts = [{attempt_id:'one',run_id:'run-one',purpose:'approval',status:'completed',usage_provenance:'native',
      usage:{input_tokens:140,output_tokens:12},questions:{approval:{choice:'allow',confidence:.98,probabilities:{allow:.98,deny:.02}}},
      provider:{name:'Jev',model:'jev-1.14.0',protocol:'systemone',distribution_source:'native'},
      approval:{decision_source:'jev',risk_level:'low',user_authorization:'explicit',disposition:'allow',policy:'chat permissions',assessed:true}}];
    window.fetch = async (url, options) => {
      const path = typeof url === 'string' ? url : url.url;
      let data;
      if (path.endsWith('/ui-fixture/decisions/live')) data={items:window.fixtureAttempts};
      else if(path.endsWith('/ui-fixture/decision-handoffs')) data={items:[{id:'receipt-one',run_id:'run-one',stop_reason:'completed',completion_verified:true,action_ids:['write','delete'],check_ids:['read']}]};
      else if(path.endsWith('/ui-fixture/automation')) data={task:{previous_status:'completed'}};
      else if(path.endsWith('/ui-fixture/context')) data=window.fixtureContext;
      else return realFetch(url, options);
      return new Response(JSON.stringify(data), {status:200,headers:{'Content-Type':'application/json'}});
    };
    const mount = window.DecisionActivity.mount;
    window.DecisionActivity = {mount(...args) { window.reviewActivity = mount(...args); return window.reviewActivity; }};
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId('ui-fixture');
    const renderer = window.chatModule;
    renderer.addMessage('user','Create a file, write “test”, then delete it.');
    renderer.addMessage('assistant','Created the file, checked its contents, and deleted it.','gemini-2.5-flash');
    const el=id=>document.getElementById(id);
    await new Promise(resolve=>setTimeout(resolve,500));
    if(!window.reviewActivity) window.reviewActivity = window.DecisionActivity.mount(el('chat-decision-activity'),'ui-fixture',{
      pane:el('chat-decision-pane'),trigger:el('chat-decision-toggle'),closeButton:el('chat-decision-close'),refreshButton:el('chat-decision-refresh')});
    return {app:!!window.chatModule,activity:!!window.DecisionActivity};
})()"""


def main():
    env = dict(os.environ)
    env.update(AUTH_ENABLED='false', LOCALHOST_BYPASS='false', HOME='/state/home',
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
                with urllib.request.urlopen('http://127.0.0.1:7000/api/health', timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                if app.poll() is not None:
                    raise RuntimeError('application startup failed')
                time.sleep(.25)
        browser = Chrome()
        results = []
        for width, height in [(1440, 1000), (390, 844)]:
            browser.call('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':1,'mobile':width<900})
            browser.call('Emulation.setTouchEmulationEnabled', {'enabled':width<900, 'maxTouchPoints':1})
            browser.call('Page.navigate', {'url':'http://127.0.0.1:7000/'})
            for _ in range(120):
                if browser.js('Boolean(window.chatModule && window.DecisionActivity)'):
                    break
                time.sleep(.25)
            result = browser.js(FIXTURE)
            browser.js("document.getElementById('chat-decision-toggle').click()")
            time.sleep(.8)
            browser.capture(f'decisions-{width}.png')
            result.update(width=width, height=height)
            result['hasRows'] = browser.js("document.querySelectorAll('.decision-activity ol > li').length")
            result['stableUpdates'] = browser.js("""(async()=>{const row=document.querySelector('.decision-activity ol > li');const details=row.querySelector('details');details.open=true;window.keptRow=row;window.keptReceipt=document.querySelector('.decision-activity ul > li');await window.reviewActivity.refresh();return row===document.querySelector('.decision-activity ol > li') && details.open && window.keptReceipt===document.querySelector('.decision-activity ul > li');})()""")
            browser.js("document.getElementById('chat-decision-close').click()")
            result['actionRects'] = browser.js("[...document.querySelectorAll('.msg-user .msg-actions button')].map(b=>({width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height,label:b.title}))")
            result['fullUI'] = browser.js("Boolean(document.querySelector('#chat-history') && document.querySelector('#message') && window.chatModule)")
            result['defaultWorkspace'] = browser.js("fetch('/api/workspace/default').then(r=>r.json())")
            browser.js("window.refreshChatContextHeader('review')")
            time.sleep(.5)
            browser.js("document.getElementById('chat-context-pill').click()")
            time.sleep(.3)
            browser.capture(f'context-actions-{width}.png')
            result['temperatureDisabled'] = browser.js("Boolean(document.querySelector('.generation-control-unavailable input[disabled]'))")
            result['fitsViewport'] = browser.js("document.documentElement.scrollWidth <= window.innerWidth")
            result['decisionsStyled'] = browser.js("getComputedStyle(document.querySelector('.decision-activity ol')).listStyleType === 'none'")
            if not BASELINE:
                assert result['decisionsStyled'], result
            if not BASELINE:
                assert result['app'] and result['fullUI'] and result['hasRows'] == 1 and result['stableUpdates'], result
                assert result['defaultWorkspace']['ok'] and result['temperatureDisabled'] and result['fitsViewport'], result
                assert len(result['actionRects']) == 3 and len({(x['width'], x['height']) for x in result['actionRects']}) == 1, result
                assert result['actionRects'][0]['width'] == (44 if width < 900 else 28), result
            browser.js("window.fixtureContext.temperature_control={enabled:true,reason:''};window.fixtureContext.model='gemini-2.5-flash';window.refreshChatContextHeader('review-model-change')")
            result['temperatureReenabled'] = browser.js("!document.querySelector('.generation-control-unavailable') && !(document.querySelector('input[aria-label=Temperature]') || document.querySelector('.chat-context-threshold-slider')).disabled")
            assert result['temperatureReenabled'], result
            results.append(result)
        (OUT / 'browser-results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2), flush=True)
    finally:
        if browser:
            browser.process.terminate()
            browser.process.wait(timeout=8)
        app.terminate()
        app.wait(timeout=15)


if __name__ == '__main__':
    main()
