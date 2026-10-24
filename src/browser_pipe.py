"""Owned Chromium pipe producer with captured isolated-world operations.

Only trusted server enrolment launches a browser. Model values never select an
executable, a CDP method, a target, a realm, or code. A lost response after an
effect was sent is indeterminate and revokes the channel; it is never retried.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import stat
import struct
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4

from src.agent_runtime.resources import ResourceIdentityError
from src.process_lifecycle import ProcessIdentity

NAMESPACE = 'native:odysseus-cdp-pipe'
VERSION = '1'
MAX_FRAME = 4_000_000
MAX_SCREENSHOT = 2_000_000
OBSERVER = Path(__file__).with_name('browser_pipe_observer.js')
REGISTRY: dict[tuple[str, str], 'PipeBrowserRecord'] = {}
_BINARY_RECEIPTS = {}
PAGE_ACTIONS = frozenset({'open', 'navigate', 'snapshot', 'read', 'click', 'fill', 'scroll', 'screenshot', 'wait', 'close', 'automate'})
_ALLOWED_CDP = frozenset({'Browser.getVersion', 'Browser.setDownloadBehavior', 'Target.createTarget', 'Target.setAutoAttach', 'Target.closeTarget', 'Page.enable', 'Page.setLifecycleEventsEnabled', 'Page.getFrameTree', 'Page.createIsolatedWorld', 'Page.navigate', 'Page.captureScreenshot', 'Runtime.enable', 'Runtime.runIfWaitingForDebugger', 'Runtime.evaluate', 'Runtime.callFunctionOn', 'Runtime.releaseObject', 'Fetch.enable', 'Fetch.continueRequest', 'Fetch.failRequest', 'Page.handleJavaScriptDialog', 'Browser.close'})
_CALL_SNAPSHOT = 'function(nonce) { return this.snapshot(nonce); }'
_CALL_ACTION = 'function(args) { return this.action(args); }'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def origin(url):
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('Invalid browser URL')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Only admitted HTTPS origins are supported')
    host = parsed.hostname.lower()
    if host == 'localhost' or host.endswith('.localhost'):
        raise ValueError('Local browser navigation is unavailable')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('Private browser address is unavailable')
    authority = '[' + host + ']' if ':' in host else host
    return 'https://' + authority + (':' + str(parsed.port) if parsed.port not in (None, 443) else '')


class BrowserPipeError(ResourceIdentityError):
    def __init__(self, code, *, indeterminate=False):
        self.code = code
        self.indeterminate = bool(indeterminate)
        super().__init__(code)

    def result(self):
        return {'error': self.code, 'failure_kind': self.code, 'exit_code': 1,
                'executed': None if self.indeterminate else False,
                'outcome': 'indeterminate' if self.indeterminate else 'refused', 'retryable': False}


@dataclass(frozen=True)
class ProducerManifest:
    executable: str
    binary_sha256: str
    observer_sha256: str
    architecture: str
    qualification_id: str

    @classmethod
    def load(cls, path):
        path = Path(path)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o022 or info.st_uid != 0 or info.st_size > 8192):
            raise BrowserPipeError('browser_manifest_untrusted')
        raw = json.loads(path.read_text(), object_pairs_hook=_pairs)
        if set(raw) != {'schema', 'executable', 'binary_sha256', 'observer_sha256', 'architecture', 'qualification_id', 'sandbox_qualified'} or raw['schema'] != 'odysseus-browser-pipe/v1' or raw['sandbox_qualified'] is not True:
            raise BrowserPipeError('browser_runtime_unqualified')
        manifest = cls(**{k: raw[k] for k in cls.__dataclass_fields__})
        manifest.validate()
        return manifest

    def validate(self):
        path = Path(self.executable)
        if self.executable != '/usr/lib/chromium/chromium' or platform.system() != 'Linux' or platform.machine() != self.architecture:
            raise BrowserPipeError('browser_runtime_unsupported')
        info = path.lstat()
        parents = (path.parent, *path.parent.parents)
        if any(parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022 for parent in parents):
            raise BrowserPipeError('browser_producer_changed')
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        binary_digest = _BINARY_RECEIPTS.get(signature)
        if binary_digest is None:
            with path.open('rb') as executable:
                binary_digest = hashlib.file_digest(executable, 'sha256').hexdigest()
            _BINARY_RECEIPTS[signature] = binary_digest
        if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or binary_digest != self.binary_sha256
                or hashlib.sha256(OBSERVER.read_bytes()).hexdigest() != self.observer_sha256
                or not re.fullmatch(r'[a-f0-9]{64}', self.qualification_id)):
            raise BrowserPipeError('browser_producer_changed')


class PipeCDP:
    """One channel reader continuously invalidates state before resolving calls."""
    def __init__(self, channel, on_event: Callable[[dict], None], on_loss: Callable[[], None]):
        self.channel, self.on_event, self.on_loss = channel, on_event, on_loss
        self.pending = {}
        self.sequence = 0
        self.closed = False
        self.termination = None
        self.write_lock = asyncio.Lock()
        self.reader = asyncio.create_task(self._read())

    def _end(self):
        self.closed = True
        self.on_loss()
        if self.termination is None:
            self.termination = asyncio.create_task(self.channel.close())

    async def _read(self):
        buffer = b''
        try:
            while True:
                chunk = await self.channel.stdout.read(65536)
                if not chunk:
                    raise BrowserPipeError('browser_pipe_closed')
                buffer += chunk
                if len(buffer) > MAX_FRAME:
                    raise BrowserPipeError('browser_protocol_oversized')
                while b'\0' in buffer:
                    raw, buffer = buffer.split(b'\0', 1)
                    if not raw:
                        continue
                    message = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                    if not isinstance(message, dict):
                        raise ValueError('protocol')
                    if 'method' in message:
                        self.on_event(message)
                    elif message.get('id') in self.pending:
                        future, session = self.pending.pop(message['id'])
                        if message.get('sessionId') != session:
                            future.set_exception(BrowserPipeError('browser_response_binding_changed'))
                            raise ValueError('response session')
                        if not future.done():
                            future.set_result(message)
        except (Exception, asyncio.CancelledError):
            self._end()
            for future, _session in self.pending.values():
                if not future.done():
                    future.set_exception(BrowserPipeError('browser_pipe_closed'))
            self.pending.clear()

    async def call(self, method, params=None, *, session=None, effect=False, timeout=10, before_send=None):
        if self.closed or method not in _ALLOWED_CDP or len(self.pending) >= 32:
            raise BrowserPipeError('browser_channel_unavailable')
        sent = False
        self.sequence += 1
        identifier = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = (future, session)
        message = {'id': identifier, 'method': method, 'params': params or {}}
        if session:
            message['sessionId'] = session
        try:
            async with asyncio.timeout(timeout):
                async with self.write_lock:
                    if self.closed:
                        raise BrowserPipeError('browser_pipe_closed')
                    if before_send is not None:
                        before_send()
                    self.channel.stdin.write(json.dumps(message, separators=(',', ':'), allow_nan=False).encode() + b'\0')
                    sent = True
                    await self.channel.stdin.drain()
                result = await future
            if 'error' in result:
                raise BrowserPipeError('browser_protocol_error', indeterminate=effect and sent)
            return result.get('result', {})
        except (Exception, asyncio.CancelledError) as error:
            self.pending.pop(identifier, None)
            if not future.done():
                future.cancel()
            if effect and sent:
                self._end()
                raise BrowserPipeError('browser_action_indeterminate', indeterminate=True) from None
            if isinstance(error, asyncio.CancelledError):
                raise
            raise BrowserPipeError(getattr(error, 'code', 'browser_protocol_error')) from None

    async def close(self):
        self._end()
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        return await self.termination


@dataclass(frozen=True)
class BrowserTaskGrant:
    owner: str
    thread_id: str
    run_id: str
    allowed_origins: frozenset[str]
    allowed_effects: frozenset[str] = frozenset()
    prepared_values: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    expires_at: float = 0
    # This value is constructed by server admission, never accepted from tools.
    navigation_urls: frozenset[str] = frozenset()

    def __post_init__(self):
        if not self.owner or not self.thread_id or not self.run_id or not self.allowed_origins or self.expires_at <= 0:
            raise ValueError('Invalid browser task grant')
        if any(origin(item) != item for item in self.allowed_origins) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 2000 for k, v in self.prepared_values.items()):
            raise ValueError('Invalid browser scope')
        if not self.allowed_effects <= {'click', 'fill', 'submit', 'send', 'publish', 'purchase', 'delete', 'account_change'}:
            raise ValueError('Invalid browser effects')
        if any(not self.permits_navigation(url) for url in self.navigation_urls):
            raise ValueError('Invalid browser navigation scope')
        # Frozen capability includes immutable contents, not a mutable mapping
        # that could change after approval/classification.
        object.__setattr__(self, 'prepared_values', MappingProxyType(dict(self.prepared_values)))
        object.__setattr__(self, 'allowed_origins', frozenset(self.allowed_origins))
        object.__setattr__(self, 'allowed_effects', frozenset(self.allowed_effects))
        object.__setattr__(self, 'navigation_urls', frozenset(self.navigation_urls))

    def check(self, owner, thread_id):
        if (owner, thread_id) != (self.owner, self.thread_id) or time.time() >= self.expires_at:
            raise BrowserPipeError('browser_task_stale')

    def permits_navigation(self, url):
        try:
            return origin(url) in self.allowed_origins
        except ValueError:
            return False


@dataclass(frozen=True)
class ObservedDocument:
    target: str
    frame: str
    loader: str
    context_unique_id: str
    object_id: str
    epoch: int
    nonce: str
    packet: dict


class PipeBrowserRecord:
    def __init__(self, owner, thread_id, manifest, channel, grant, *, clock=time.monotonic):
        self.owner, self.thread_id, self.manifest, self.channel, self.containment_grant = owner, thread_id, manifest, channel, grant
        self.process = channel.identity
        self.channel_nonce = uuid4().hex
        self.key = 'ody-' + digest([owner, thread_id, self.channel_nonce])[:24]
        self.lock = asyncio.Lock()
        self.target = self.cdp_session = self.frame = self.loader = ''
        self.attachments = {}
        self.creating_target = False
        self.started = False
        self.contexts = {}
        self.epoch = 0
        self.current = None
        self.world = None
        self.pending_loader = None
        self.session = None
        self.pages = ()
        self.live = True
        self.clock = clock
        self.changed = asyncio.Event()
        self.task_grant = None
        self.network_grant = None
        self.artifact_writer = None
        self.observer_source = OBSERVER.read_text()
        if hashlib.sha256(self.observer_source.encode()).hexdigest() != manifest.observer_sha256:
            raise BrowserPipeError('browser_producer_changed')
        self.control_tasks = set()
        self.cdp = PipeCDP(channel, self._event, self.invalidate)

    def invalidate(self):
        self.live = False
        self.task_grant = self.network_grant = None
        self.epoch += 1
        self.current = None
        self.pages = ()
        self.changed.set()

    def invalidate_document(self):
        self.epoch += 1
        self.current = None
        self.changed.set()
        self._resources()

    def _control(self, method, params, session=None):
        async def send():
            try:
                await self.cdp.call(method, params, session=session)
            except Exception:
                self.invalidate()
        task = asyncio.create_task(send())
        self.control_tasks.add(task)
        task.add_done_callback(self.control_tasks.discard)

    def _event(self, message):
        method, params = message.get('method'), message.get('params', {})
        relevant = bool(self.cdp_session) and message.get('sessionId') == self.cdp_session
        if method == 'Target.attachedToTarget':
            target, session = params.get('targetInfo', {}).get('targetId'), params.get('sessionId')
            if not isinstance(target, str) or not re.fullmatch(r'[A-F0-9]{32}', target) or not isinstance(session, str) or not re.fullmatch(r'[A-F0-9]{32}', session):
                self.invalidate()
                return
            if target == self.target or self.creating_target:
                self.attachments[target] = (session, params.get('waitingForDebugger') is True)
                self.changed.set()
            else:
                # Other tabs/popups/workers never resume. A user-facing page
                # cannot turn a click into an unobserved producer/session.
                self._control('Target.closeTarget', {'targetId': target})
                if self.started and params.get('waitingForDebugger') is not True:
                    self.cdp._end()
        elif method == 'Target.detachedFromTarget' and params.get('sessionId') == self.cdp_session:
            self.invalidate()
        elif method in {'Target.targetDestroyed', 'Target.targetCrashed'} and params.get('targetId') == self.target:
            self.invalidate()
        elif method == 'Inspector.detached' and relevant:
            self.invalidate()
        elif relevant and method == 'Page.frameNavigated' and not params.get('frame', {}).get('parentId'):
            frame = params.get('frame', {})
            self.frame, self.loader = str(frame.get('id') or ''), str(frame.get('loaderId') or '')
            self.contexts.clear()
            self.world = None
            self.invalidate_document()
        elif relevant and method == 'Page.navigatedWithinDocument' and params.get('frameId') == self.frame:
            self.invalidate_document()
        elif relevant and method in {'Runtime.executionContextDestroyed', 'Runtime.executionContextsCleared', 'Page.frameDetached'}:
            self.contexts.clear()
            self.world = None
            self.invalidate_document()
        elif relevant and method == 'Runtime.executionContextCreated':
            context = params.get('context', {})
            auxiliary = context.get('auxData') or {}
            if auxiliary.get('frameId') == self.frame and context.get('name') == 'odysseus-observed-v1' and context.get('uniqueId'):
                self.contexts[context['id']] = context['uniqueId']
                self.changed.set()
        elif relevant and method == 'Page.lifecycleEvent' and params.get('frameId') == self.frame and params.get('name') in {'init', 'backForwardCacheRestore', 'prerenderActivation'}:
            self.contexts.clear()
            self.invalidate_document()
        elif relevant and method == 'Fetch.requestPaused':
            request = params.get('request') or {}
            network = self.task_grant or self.network_grant
            allowed = self.live and network is not None and time.time() < network.expires_at and network.permits_navigation(request.get('url', ''))
            if request.get('method') not in {'GET', 'HEAD'} and (network is None or 'submit' not in network.allowed_effects):
                allowed = False
            self._control('Fetch.continueRequest' if allowed else 'Fetch.failRequest',
                          {'requestId': params.get('requestId'), **({} if allowed else {'errorReason': 'BlockedByClient'})}, message.get('sessionId'))
        elif relevant and method == 'Page.javascriptDialogOpening':
            self._control('Page.handleJavaScriptDialog', {'accept': False}, message.get('sessionId'))

    def validate_config(self):
        self.manifest.validate()
        if not self.live or not self.process.owned() or self.cdp.closed:
            raise BrowserPipeError('browser_session_stale')

    def _resources(self):
        if self.session is None or not self.target or not self.live:
            return
        from src.agent_runtime.resources import PipeBrowserPageResource
        context = self.current.context_unique_id if self.current else ''
        self.pages = (PipeBrowserPageResource(self.session, self.target, self.loader or '', 't1', '', 'document', context, self.epoch),) if self.loader and context else (
            PipeBrowserPageResource(self.session, self.target, '', 't1', '', 'page', '', self.epoch),)

    async def start(self):
        self.validate_config()
        version = await self.cdp.call('Browser.getVersion')
        if not isinstance(version.get('product'), str) or not re.fullmatch(r'(?:Chrome|HeadlessChrome)/[0-9.]+', version['product']):
            raise BrowserPipeError('browser_protocol_unsupported')
        await self.cdp.call('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True})
        self.creating_target = True
        target = await self.cdp.call('Target.createTarget', {'url': 'about:blank'})
        self.target = target.get('targetId', '')
        self.creating_target = False
        if not re.fullmatch(r'[A-F0-9]{32}', self.target):
            raise BrowserPipeError('browser_target_unavailable')
        deadline = self.clock() + 2
        while self.target not in self.attachments:
            self.changed.clear()
            if self.clock() >= deadline or not self.live:
                raise BrowserPipeError('browser_target_unavailable')
            await asyncio.wait_for(self.changed.wait(), max(.001, deadline - self.clock()))
        self.cdp_session, paused = self.attachments.pop(self.target)
        if not paused:
            raise BrowserPipeError('browser_target_not_paused')
        for other in self.attachments:
            self._control('Target.closeTarget', {'targetId': other})
        self.attachments.clear()
        if not re.fullmatch(r'[A-F0-9]{32}', self.cdp_session):
            raise BrowserPipeError('browser_target_unavailable')
        for method, params in [('Page.enable', {}), ('Runtime.enable', {}), ('Page.setLifecycleEventsEnabled', {'enabled': True}), ('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})]:
            await self.cdp.call(method, params, session=self.cdp_session)
        await self.cdp.call('Browser.setDownloadBehavior', {'behavior': 'deny'})
        await self.cdp.call('Runtime.runIfWaitingForDebugger', session=self.cdp_session)
        frame = (await self.cdp.call('Page.getFrameTree', session=self.cdp_session)).get('frameTree', {}).get('frame', {})
        self.frame, self.loader = str(frame.get('id') or ''), str(frame.get('loaderId') or '')
        if not self.frame or not self.loader:
            raise BrowserPipeError('browser_document_unavailable')
        from src.agent_runtime.resources import BrowserSessionResource, PipeBrowserSessionObservation
        payload = dict(producer_namespace=NAMESPACE, producer_version=VERSION, platform=self.manifest.architecture,
                       binary_sha256=self.manifest.binary_sha256, configuration_digest=digest(asdict(self.manifest)),
                       session_key=self.key, process=self.process, channel_nonce=self.channel_nonce)
        observation = PipeBrowserSessionObservation(**payload, session_incarnation=digest({**payload, 'process': self.process.to_record()}))
        self.session = BrowserSessionResource(self.owner, self.thread_id, observation)
        self._resources()
        self.started = True

    async def _world(self, before_send=None):
        self.validate_config()
        deadline = self.clock() + 2
        while self.pending_loader is not None and self.loader != self.pending_loader:
            self.changed.clear()
            if before_send is not None:
                before_send()
            if self.clock() >= deadline or not self.live:
                raise BrowserPipeError('browser_navigation_document_unavailable')
            await asyncio.wait_for(self.changed.wait(), max(.001, deadline - self.clock()))
        self.pending_loader = None
        start = self.epoch
        if self.world is not None and self.world[0] in self.contexts.values():
            return *self.world, start
        world = await self.cdp.call('Page.createIsolatedWorld', {'frameId': self.frame, 'worldName': 'odysseus-observed-v1', 'grantUniveralAccess': False}, session=self.cdp_session, before_send=before_send)
        identifier = world.get('executionContextId')
        deadline = self.clock() + 2
        while identifier not in self.contexts:
            self.changed.clear()
            if self.clock() >= deadline or self.epoch != start or not self.live:
                raise BrowserPipeError('browser_context_unavailable')
            await asyncio.wait_for(self.changed.wait(), max(.001, deadline - self.clock()))
        unique = self.contexts[identifier]
        result = await self.cdp.call('Runtime.evaluate', {'expression': self.observer_source, 'uniqueContextId': unique, 'returnByValue': False, 'awaitPromise': False}, session=self.cdp_session, before_send=before_send)
        remote = result.get('result', {})
        if result.get('exceptionDetails') or not remote.get('objectId') or self.epoch != start:
            raise BrowserPipeError('browser_context_unavailable')
        self.world = (unique, remote['objectId'])
        return *self.world, start

    async def observe(self, *, before_send=None):
        async with self.lock:
            unique, object_id, epoch = await self._world(before_send)
            nonce = uuid4().hex
            result = await self.cdp.call('Runtime.callFunctionOn', {'objectId': object_id, 'functionDeclaration': _CALL_SNAPSHOT,
                'arguments': [{'value': nonce}], 'returnByValue': True, 'awaitPromise': False}, session=self.cdp_session, before_send=before_send)
            packet = result.get('result', {}).get('value')
            if result.get('exceptionDetails') or not isinstance(packet, dict) or packet.get('error') or self.epoch != epoch or not self.live:
                raise BrowserPipeError('browser_observation_stale')
            if set(packet) != {'nonce', 'url', 'title', 'text', 'elements', 'total_elements', 'truncated', 'ready'} or packet['nonce'] != nonce or not isinstance(packet['elements'], list) or len(packet['elements']) > 180:
                raise BrowserPipeError('browser_observation_invalid')
            if len(json.dumps(packet).encode()) > 100_000:
                raise BrowserPipeError('browser_observation_oversized')
            self.current = ObservedDocument(self.target, self.frame, self.loader, unique, object_id, epoch, nonce, packet)
            self._resources()
            return self.public_observation()

    def public_observation(self):
        if self.current is None:
            raise BrowserPipeError('browser_observation_required')
        packet = self.current.packet
        elements = []
        for node in packet['elements'][:40]:
            elements.append({k: node[k] for k in ('ref', 'label', 'role', 'editable', 'disabled') if k in node})
        # Destinations and form metadata remain in the host's action table.
        parsed = urlsplit(str(packet['url']))
        public_url = (parsed.scheme + '://' + str(parsed.hostname) + parsed.path[:512]) if parsed.scheme == 'https' and parsed.hostname else ''
        result = {'observation': self.current.nonce, 'page': 't1', 'url': public_url, 'title': str(packet['title'])[:160], 'text': str(packet['text'])[:2000], 'elements': elements,
                  'truncated': packet['truncated'] or len(packet['elements']) > len(elements), 'ready': packet['ready']}
        while len(json.dumps(result, ensure_ascii=False).encode()) > 16000 and result['elements']:
            result['elements'].pop(); result['truncated'] = True
        return result

    def validate_page(self, resource):
        self.validate_config()
        if resource.session != self.session or resource.target_id != self.target:
            raise BrowserPipeError('browser_target_stale')
        if resource.scope == 'document' and (self.current is None or resource.loader_id != self.loader or resource.context_unique_id != self.current.context_unique_id or resource.epoch != self.epoch):
            raise BrowserPipeError('browser_document_stale')

    def _check_observation(self, nonce):
        self.validate_config()
        current = self.current
        if current is None or nonce != current.nonce or current.epoch != self.epoch or current.loader != self.loader or current.target != self.target or current.context_unique_id not in self.contexts.values():
            raise BrowserPipeError('stale_observation')
        return current

    def permitted_node(self, action, ref, grant):
        node = next((n for n in self.current.packet['elements'] if n.get('ref') == ref), None) if self.current else None
        if node is None:
            raise BrowserPipeError('invalid_target')
        if node.get('disabled'):
            raise BrowserPipeError('target_unavailable')
        if action == 'fill':
            if not node.get('editable') or 'fill' not in grant.allowed_effects:
                raise BrowserPipeError('browser_effect_denied')
        elif node.get('destination'):
            if not grant.permits_navigation(node['destination']):
                raise BrowserPipeError('browser_origin_denied')
        elif 'click' not in grant.allowed_effects:
            # Unknown JS handlers are not proved safe by an English label.
            raise BrowserPipeError('browser_effect_denied')
        if node.get('submits_form') and ('submit' not in grant.allowed_effects or not grant.permits_navigation(node.get('form_action', ''))):
            raise BrowserPipeError('browser_effect_denied')
        return node

    def _dispatch_guard(self, grant, *, human=False, nonce=None):
        grant.check(self.owner, self.thread_id)
        self.validate_config()
        if not human:
            from src.chat_automation import current_turn
            turn = current_turn()
            if (self.task_grant is not grant or turn is None
                    or ((turn.owner or '').casefold(), turn.session_id, turn.run_id) != (self.owner, self.thread_id, grant.run_id)
                    or not turn.current()):
                raise BrowserPipeError('browser_policy_stale')
        if nonce is not None:
            self._check_observation(nonce)

    async def execute_bound(self, bound, args, grant=None, *, _human=False):
        action = args.get('action')
        if action not in PAGE_ACTIONS - {'automate'} or set(args) - {'action', 'page', 'observation', 'ref', 'value_id', 'amount', 'url', 'timeout_ms'}:
            raise BrowserPipeError('invalid_arguments')
        grant = grant or self.task_grant
        if not isinstance(grant, BrowserTaskGrant):
            raise BrowserPipeError('browser_task_authority_required')
        grant.check(self.owner, self.thread_id)
        if bound.owner != self.owner or bound.thread_id != self.thread_id or bound.session != self.session:
            raise BrowserPipeError('browser_application_binding_changed')
        bound.validate()
        guard = lambda: (bound.validate(), self._dispatch_guard(grant, human=_human))
        if action in {'snapshot', 'read'}:
            return {'output': json.dumps(await self.observe(before_send=guard)), 'exit_code': 0, 'executed': True}
        if action == 'screenshot':
            if self.artifact_writer is None:
                raise BrowserPipeError('browser_screenshot_store_unavailable')
            result = await self.screenshot(before_send=guard)
            artifact = await self.artifact_writer(result.pop('screenshot'))
            return {**result, 'screenshot': artifact}
        async with self.lock:
            grant.check(self.owner, self.thread_id)
            self.validate_config()
            if action in {'open', 'navigate'}:
                url = args.get('url')
                if not isinstance(url, str) or (url not in grant.navigation_urls and not any(n.get('destination') == url for n in (self.current.packet['elements'] if self.current else []))) or not grant.permits_navigation(url):
                    raise BrowserPipeError('browser_navigation_not_observed')
                result = await self.cdp.call('Page.navigate', {'url': url}, session=self.cdp_session, effect=True, before_send=guard)
                self.invalidate_document()
                if result.get('errorText'):
                    raise BrowserPipeError('browser_navigation_failed')
                expected = result.get('loaderId')
                if expected is not None and (not isinstance(expected, str) or not expected or len(expected) > 128):
                    self.invalidate()
                    raise BrowserPipeError('browser_action_indeterminate', indeterminate=True)
                self.pending_loader = expected
                return {'output': 'Navigation dispatched. Obtain a fresh observation.', 'exit_code': 0, 'executed': True}
            if action == 'close':
                await self.cdp.call('Target.closeTarget', {'targetId': self.target}, effect=True, before_send=guard)
                self.invalidate()
                return {'output': 'Browser page closed.', 'exit_code': 0, 'executed': True}
            if action == 'wait':
                milliseconds = args.get('timeout_ms', 250)
                if type(milliseconds) is not int or not 0 <= milliseconds <= 1000:
                    raise BrowserPipeError('invalid_arguments')
                guard()
                await asyncio.sleep(milliseconds / 1000)
                guard()
                return {'output': 'Wait completed.', 'exit_code': 0, 'executed': True}
            observed = self._check_observation(args.get('observation'))
            command = {'action': action, 'nonce': observed.nonce}
            if action in {'click', 'fill'}:
                self.permitted_node(action, args.get('ref'), grant)
                command['ref'] = args['ref']
                if action == 'fill':
                    key = args.get('value_id')
                    node = self.permitted_node(action, args.get('ref'), grant)
                    if key not in grant.prepared_values or node.get('label') != key:
                        raise BrowserPipeError('browser_prepared_value_missing')
                    command['text'] = grant.prepared_values[key]
            elif action == 'scroll':
                amount = args.get('amount', 600)
                if type(amount) is not int or not -800 <= amount <= 800:
                    raise BrowserPipeError('invalid_arguments')
                command['amount'] = amount
            # Host guard is defense in depth; the effect's atomic guard is the
            # captured object in the isolated world, never the active context.
            self._check_observation(observed.nonce)
            action_guard = lambda: (bound.validate(), self._dispatch_guard(grant, human=_human, nonce=observed.nonce))
            result = await self.cdp.call('Runtime.callFunctionOn', {'objectId': observed.object_id, 'functionDeclaration': _CALL_ACTION,
                'arguments': [{'value': command}], 'returnByValue': True, 'awaitPromise': False}, session=self.cdp_session, effect=True, before_send=action_guard)
            self.current = None
            self._resources()
            response = result.get('result', {}).get('value')
            if result.get('exceptionDetails'):
                self.invalidate()
                raise BrowserPipeError('browser_action_indeterminate', indeterminate=True)
            if not isinstance(response, dict) or response.get('executed') is not True:
                raise BrowserPipeError(response.get('error', 'browser_action_refused') if isinstance(response, dict) else 'browser_action_refused')
            return {'output': 'Observed action executed. Obtain a fresh observation.', 'exit_code': 0, 'executed': True, 'actor': 'automation'}

    async def screenshot(self, *, before_send=None):
        async with self.lock:
            self.validate_config()
            epoch, loader, target = self.epoch, self.loader, self.target
            result = await self.cdp.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False}, session=self.cdp_session, before_send=before_send)
            if self.epoch != epoch or self.loader != loader or self.target != target:
                raise BrowserPipeError('browser_screenshot_stale')
            raw = base64.b64decode(result.get('data', ''), validate=True)
            if len(raw) > MAX_SCREENSHOT or len(raw) < 24 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                raise BrowserPipeError('browser_screenshot_invalid')
            width, height = struct.unpack('>II', raw[16:24])
            if not 0 < width <= 8192 or not 0 < height <= 8192:
                raise BrowserPipeError('browser_screenshot_invalid')
            return {'output': 'Captured current browser pixels.', 'exit_code': 0, 'executed': True,
                    'screenshot': {'data': raw, 'mime_type': 'image/png', 'sha256': hashlib.sha256(raw).hexdigest(), 'width': width, 'height': height,
                                   'target_consistency_checked': True, 'atomic_document_capture': False}}

    async def human_action(self, callback):
        async with self.lock:
            self.validate_config()
            self.invalidate_document()
            return await callback(self)

    async def human_control(self, args, *, grant):
        """Trusted owner route, never model callable and never raw CDP/JS.

        The human chooses the current opaque target; a fresh prepared value
        comes from the owner route's explicit input. Revocation precedes any
        await, so an in-flight classifier cannot inherit human authority.
        """
        if (not isinstance(grant, BrowserTaskGrant) or not isinstance(args, dict)
                or args.get('action') not in {'snapshot', 'read', 'click', 'fill', 'scroll', 'navigate', 'wait'}
                or args.get('page') != 't1'):
            raise BrowserPipeError('invalid_arguments')
        grant.check(self.owner, self.thread_id)
        self.task_grant = None
        self.network_grant = grant
        from src.agent_runtime.authority import ExactOperation
        from src.browser_identity import BoundBrowserOperation
        if args['action'] in {'snapshot', 'read'}:
            self.invalidate_document()
        if len(self.pages) != 1:
            raise BrowserPipeError('browser_target_stale')
        operation = ExactOperation.normalize('private_browser', json.dumps(args))
        bound = BoundBrowserOperation(operation, 'human-' + uuid4().hex, self.owner, self.thread_id, self.session, self.pages[0])
        try:
            result = await self.execute_bound(bound, args, grant, _human=True)
            return {**result, 'actor': 'human', 'automation_revoked': True}
        finally:
            if args['action'] not in {'snapshot', 'read'}:
                self.invalidate_document()

    async def close(self):
        self.invalidate()
        for task in tuple(self.control_tasks):
            task.cancel()
        await asyncio.gather(*self.control_tasks, return_exceptions=True)
        return await self.cdp.close()


def get(owner, thread_id):
    return REGISTRY.get((owner, thread_id))


def supports(owner, thread_id, action):
    record = get(owner, thread_id)
    return record is not None and record.live and action in PAGE_ACTIONS


async def enrol(owner, thread_id, manifest, profile_root, *, channel_factory=None):
    """Trusted interactive-owner seam; never called from model discovery."""
    from src.browser_identity import _REGISTRY as legacy_registry
    if not owner or not thread_id or (owner, thread_id) in REGISTRY or (owner, thread_id) in legacy_registry or not isinstance(manifest, ProducerManifest):
        raise BrowserPipeError('browser_enrolment_denied')
    manifest.validate()
    root = Path(profile_root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise BrowserPipeError('browser_profile_untrusted')
    directory = root / ('pipe-' + uuid4().hex)
    directory.mkdir(mode=0o700)
    for name in ('profile', 'home', 'tmp', 'config'):
        (directory / name).mkdir(mode=0o700)
    from src import containment
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(directory / 'home'), 'XDG_CONFIG_HOME': str(directory / 'config'), 'TMPDIR': str(directory / 'tmp')}
    spec = containment.agent_spec(str(directory), environment, 300)
    acquired = containment.acquire(spec, owner='browser:' + owner + ':' + thread_id)
    factory = channel_factory or getattr(containment, 'open_channel', None)
    if factory is None:
        containment.release(acquired)
        raise BrowserPipeError('browser_channel_runtime_unavailable')
    command = ['/bin/sh', '-c', 'exec 3<&0 4>&1; exec "$@"', 'ody-browser', manifest.executable,
               '--remote-debugging-pipe', '--headless=new', '--no-first-run', '--no-default-browser-check',
               '--disable-background-networking', '--disable-component-update', '--disable-sync', '--mute-audio',
               '--user-data-dir=' + str(directory / 'profile'), 'about:blank']
    try:
        channel = await factory(acquired, command)
    except BaseException:
        containment.release(acquired)
        raise
    record = PipeBrowserRecord(owner, thread_id, manifest, channel, acquired)
    try:
        await record.start()
        REGISTRY[(owner, thread_id)] = record
        return record
    except BaseException:
        await record.close()
        raise


async def close(owner, thread_id):
    record = REGISTRY.pop((owner, thread_id), None)
    return await record.close() if record else None
