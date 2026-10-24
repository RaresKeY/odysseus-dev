#!/usr/bin/env python3
"""Deterministic no-network/no-inference private-pipe qualification candidate.

Run only in the prescribed secretless review runner, with browser sandbox and
normal containment enabled. The fixed fixture transport fulfills admitted
HTTPS requests from local bytes before DNS/network. It cannot be used as an
application producer and does not install a deployment manifest.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import browser_pipe as browser

FIXTURE_URL = 'https://odysseus-fixture.invalid/start'
HTML = b'''<!doctype html><meta charset="utf-8"><title>Owned fixture</title>
<label>Name<input></label><button onclick="document.querySelector('output').textContent='saved'">Save</button>
<button onclick="history.replaceState(null,'',location.href)">Same URL</button>
<output>pending</output><script>window.__jev={action(){throw Error('page-world trap')}};</script>'''


class FixtureReader:
    def __init__(self, stream, requests):
        self.stream, self.requests, self.buffer = stream, requests, b''

    async def read(self, size):
        chunk = await self.stream.read(size)
        self.buffer += chunk
        if len(self.buffer) > browser.MAX_FRAME:
            raise ValueError('fixture_protocol_oversized')
        while b'\0' in self.buffer:
            item, self.buffer = self.buffer.split(b'\0', 1)
            if item:
                event = json.loads(item)
                if event.get('method') == 'Fetch.requestPaused':
                    params = event['params']
                    self.requests[params['requestId']] = params['request']['url']
        return chunk


class FixtureWriter:
    def __init__(self, stream, requests):
        self.stream, self.requests = stream, requests

    def write(self, raw):
        request = json.loads(raw[:-1])
        if request['method'] == 'Fetch.continueRequest':
            identifier = request['params']['requestId']
            if self.requests.get(identifier) != FIXTURE_URL:
                raise ValueError('fixture_network_not_permitted')
            request['method'] = 'Fetch.fulfillRequest'
            request['params'] = {'requestId': identifier, 'responseCode': 200,
                'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html; charset=utf-8'}, {'name': 'Cache-Control', 'value': 'no-store'}],
                'body': base64.b64encode(HTML).decode()}
        self.stream.write(json.dumps(request, separators=(',', ':')).encode() + b'\0')

    async def drain(self):
        await self.stream.drain()


async def fixture_channel(grant, command):
    from src import containment
    factory = getattr(containment, 'open_channel', None)
    if factory is None:
        raise browser.BrowserPipeError('browser_channel_runtime_unavailable')
    channel = await factory(grant, command)
    requests = {}
    # Wrap the channel, not a global transport/producer allowlist.
    class Channel:
        identity = channel.identity
        stdout = FixtureReader(channel.stdout, requests)
        stdin = FixtureWriter(channel.stdin, requests)
        async def close(self): return await channel.close()
    return Channel()


async def main():
    report = {'schema': 'odysseus-browser-pipe-qualification/v1', 'inference_calls': 0,
              'network': 'none; fixed local HTTPS response interception', 'sandbox_disabled': False,
              'runtime_qualified': False, 'checks': {}}
    record, stage = None, 'prerequisites'
    try:
        executable = Path('/usr/lib/chromium/chromium')
        with executable.open('rb') as stream:
            binary = hashlib.file_digest(stream, 'sha256').hexdigest()
        observer = hashlib.sha256(browser.OBSERVER.read_bytes()).hexdigest()
        manifest = browser.ProducerManifest(str(executable), binary, observer, platform.machine(), browser.digest(['qualification-candidate', binary, observer]))
        with tempfile.TemporaryDirectory(prefix='ody-pipe-qualification-') as directory:
            stage = 'contained_chromium_startup'
            record = await browser.enrol('qualification', 'local-fixture', manifest, directory, channel_factory=fixture_channel)
            grant = browser.BrowserTaskGrant('qualification', 'local-fixture', 'qualification',
                frozenset({'https://odysseus-fixture.invalid'}), frozenset({'click', 'fill'}),
                {'Name': 'fixture-only'}, time.time() + 60, frozenset({FIXTURE_URL}))
            stage = 'local_fixture_navigation'
            await record.human_control({'action': 'navigate', 'page': 't1', 'url': FIXTURE_URL}, grant=grant)
            deadline = time.monotonic() + 8
            while True:
                observation = await record.observe()
                if observation['title'] == 'Owned fixture' and observation['ready'] == 'complete': break
                if time.monotonic() >= deadline: raise browser.BrowserPipeError('fixture_document_unavailable')
                await asyncio.sleep(.05)
            report['checks']['isolated_world_observation'] = True
            stage = 'captured_fill'
            field = next(item for item in observation['elements'] if item['label'] == 'Name')
            await record.human_control({'action': 'fill', 'page': 't1', 'observation': observation['observation'], 'ref': field['ref'], 'value_id': 'Name'}, grant=grant)
            report['checks']['captured_fill'] = True
            observation = await record.observe()
            stage = 'captured_click'
            button = next(item for item in observation['elements'] if item['label'] == 'Save')
            await record.human_control({'action': 'click', 'page': 't1', 'observation': observation['observation'], 'ref': button['ref']}, grant=grant)
            observation = await record.observe()
            if 'saved' not in observation['text']: raise browser.BrowserPipeError('fixture_click_unproved')
            report['checks']['captured_click'] = True
            stage = 'same_url_invalidation'
            old = record.current
            button = next(item for item in observation['elements'] if item['label'] == 'Same URL')
            await record.human_control({'action': 'click', 'page': 't1', 'observation': observation['observation'], 'ref': button['ref']}, grant=grant)
            stale = await record.cdp.call('Runtime.callFunctionOn', {'objectId': old.object_id,
                'functionDeclaration': browser._CALL_ACTION, 'arguments': [{'value': {'action': 'click', 'nonce': old.nonce, 'ref': button['ref']}}],
                'returnByValue': True, 'awaitPromise': False}, session=record.cdp_session)
            if stale.get('result', {}).get('value', {}).get('error') != 'stale_observation': raise browser.BrowserPipeError('fixture_same_url_guard_failed')
            report['checks']['same_url_native_guard'] = True
            stage = 'actual_screenshot'
            image = (await record.screenshot())['screenshot']
            report['checks']['actual_png'] = {'sha256': image['sha256'], 'bytes': len(image['data']), 'width': image['width'], 'height': image['height'], 'atomic_document_capture': False}
            report.update(runtime_qualified=True, producer_namespace=browser.NAMESPACE,
                binary_sha256=binary, observer_sha256=observer, architecture=manifest.architecture)
            report['candidate_manifest'] = {'schema': 'odysseus-browser-pipe/v1', 'executable': manifest.executable,
                'binary_sha256': binary, 'observer_sha256': observer, 'architecture': manifest.architecture,
                'sandbox_qualified': True, 'qualification_id': browser.digest(report)}
    except Exception as error:
        report.update(failed_stage=stage, failure_kind=getattr(error, 'code', type(error).__name__))
    finally:
        if record is not None:
            outcome = await browser.close('qualification', 'local-fixture')
            report['cleanup_verified'] = bool(outcome and outcome.dead)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report['runtime_qualified'] and report.get('cleanup_verified') else 1


if __name__ == '__main__':
    try:
        with asyncio.Runner() as runner:
            raise SystemExit(runner.run(asyncio.wait_for(main(), 45)))
    except TimeoutError:
        print(json.dumps({'schema': 'odysseus-browser-pipe-qualification/v1', 'runtime_qualified': False, 'failure_kind': 'qualification_timeout'}))
        raise SystemExit(1)
