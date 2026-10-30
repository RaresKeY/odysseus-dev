import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { chromium } from 'playwright';

const [baseRoot, headRoot, output] = process.argv.slice(2);
assert(baseRoot && headRoot && output, 'Usage: review_jev_ui.mjs BASE HEAD OUTPUT');
await fs.mkdir(output, { recursive: true });
const report = { screenshots: [], layouts: [], checks: [], pageErrors: [] };
const check = (name, value) => { assert(value, name); report.checks.push(name); };
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.json': 'application/json' };
async function serve(root) {
  root = path.resolve(root);
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const file = path.resolve(root, '.' + pathname);
      if (!file.startsWith(root + path.sep)) { response.writeHead(403).end(); return; }
      const body = await fs.readFile(file);
      response.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      response.end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { origin: `http://127.0.0.1:${server.address().port}`, close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }) };
}
const browser = await chromium.launch({ headless: true });
const servers = [];
try {
  const parser = await browser.newPage();
  async function shell(root, theme, viewport) {
    const source = await fs.readFile(path.join(root, 'static/index.html'), 'utf8');
    return parser.evaluate(({ source, theme, width }) => {
      const doc = new DOMParser().parseFromString(source, 'text/html');
      doc.querySelectorAll('script, link[rel="modulepreload"], link[rel="manifest"]').forEach(el => el.remove());
      doc.querySelectorAll('[autofocus]').forEach(el => el.removeAttribute('autofocus'));
      doc.documentElement.className = theme === 'light' ? 'light' : '';
      // Mirror the shipped shell's completed startup state; app.js is intentionally inert here.
      const toolbar = doc.querySelector('.chat-input-bottom');
      if (!toolbar) throw new Error('Missing native composer toolbar');
      toolbar.style.visibility = '';
      if (width <= 768) {
        doc.documentElement.classList.add('ody-sidebar-off');
        doc.documentElement.classList.remove('ody-sidebar-mini', 'ody-mobile-startup-sidebar-hidden');
        doc.getElementById('sidebar')?.classList.add('hidden');
        const rail = doc.getElementById('icon-rail');
        rail?.classList.add('rail-hidden');
        rail?.classList.remove('mobile-mini');
        doc.body.classList.add('sidebar-collapsed', 'hamburger-only', 'hamburger-left');
        doc.body.classList.remove('hamburger-right');
        doc.getElementById('sidebar-backdrop')?.classList.remove('visible');
        for (const id of ['hamburger-btn', 'sidebar-toggle-btn']) {
          doc.getElementById(id)?.setAttribute('aria-expanded', 'false');
        }
      }
      doc.getElementById('app-loader')?.remove();
      doc.getElementById('welcome-screen')?.remove();
      doc.getElementById('chat-container')?.classList.remove('welcome-active');
      return '<!doctype html>' + doc.documentElement.outerHTML;
    }, { source, theme, width: viewport.width });
  }
  async function settle(page) {
    await page.evaluate(async () => {
      const missing = [...document.querySelectorAll('link[rel="stylesheet"]')]
        .filter(link => !link.sheet).map(link => link.getAttribute('href'));
      if (missing.length) throw new Error('Stylesheets not loaded: ' + missing.join(', '));
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
  }
  async function pageFor(root, server, size, theme, label) {
    const context = await browser.newContext({ viewport: size, deviceScaleFactor: 1, colorScheme: theme, hasTouch: size.width < 540, isMobile: false, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => report.pageErrors.push({ label, message: error.message }));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Page.setFontFamilies', { fontFamilies: { standard: 'Times New Roman' } });
    const html = await shell(root, theme, size);
    await page.route('**/*', route => new URL(route.request().url()).origin === server.origin ? route.continue() : route.abort());
    await page.route('**/static/index.html', route => route.fulfill({ contentType: 'text/html', body: html }));
    await page.goto(server.origin + '/static/index.html', { waitUntil: 'networkidle' });
    await settle(page);
    await page.locator('.chat-input-bottom').waitFor({ state: 'visible' });
    const controlsVisible = await page.evaluate(() => {
      return ['mode-agent-btn', 'mode-chat-btn'].every(id => {
        const element = document.getElementById(id);
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
        return style.visibility === 'visible' && rect.width > 0 && rect.height > 0
          && x >= 0 && x < innerWidth && y >= 0 && y < innerHeight
          && element.contains(document.elementFromPoint(x, y));
      });
    });
    check(label + ': native composer controls visible and unobscured', controlsVisible);
    return { page, context };
  }
  async function screenshot(page, name) {
    await settle(page);
    await page.screenshot({ path: path.join(output, name + '.png'), fullPage: false, animations: 'disabled' });
    report.screenshots.push(name + '.png');
  }
  const sizes = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };
  let headServer;
  for (const [version, root] of [['base', baseRoot], ['head', headRoot]]) {
    const server = await serve(root); servers.push(server);
    if (version === 'head') headServer = server;
    for (const [sizeName, size] of Object.entries(sizes)) for (const theme of ['dark', 'light']) {
      const label = `${version}-${sizeName}-${theme}`;
      const { page, context } = await pageFor(root, server, size, theme, label);
      await page.evaluate(() => {
        document.getElementById('model-picker-label').textContent = 'Review model';
        const list = document.getElementById('model-picker-list');
        list.replaceChildren(); list.classList.remove('is-empty');
        for (let i = 1; i <= 12; i++) {
          const row = document.createElement('button'); row.type = 'button'; row.className = 'model-switch-item';
          row.textContent = `Registered model ${i} · Local review fixture`;
          list.append(row);
        }
      });
      await screenshot(page, label + '-composer');
      await page.evaluate(() => {
        const menu = document.getElementById('model-picker-menu');
        for (let node = menu; node && node !== document.documentElement; node = node.parentElement) {
          node.classList.remove('hidden'); node.removeAttribute('hidden');
        }
      });
      await screenshot(page, label + '-picker');
      const layout = await page.evaluate(() => {
        const result = {};
        for (const selector of ['#model-picker-menu', '#model-picker-list', '.mode-toggle-btn.active', '.mode-toggle-btn:not(.active)']) {
          const element = document.querySelector(selector); if (!element) continue;
          const rect = element.getBoundingClientRect(), css = getComputedStyle(element);
          result[selector] = { x: rect.x, y: rect.y, width: rect.width, height: rect.height, clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, overflowY: css.overflowY, display: css.display, flex: css.flex, minWidth: css.minWidth, minHeight: css.minHeight, maxHeight: css.maxHeight };
        }
        return result;
      });
      report.layouts.push({ label, viewport: size, elements: layout });
      await context.close();
    }
  }

  for (const [sizeName, size] of Object.entries(sizes)) {
    const label = `head-${sizeName}-controller`;
    const { page, context } = await pageFor(headRoot, headServer, size, 'dark', label);
    const ready = { available: true, reason: '', source: 'conversation_model' };
    const state = { revision: 0, policy_revision: 0, decision_provider: null, approval_mode: 'always_ask', writes: [] };
    const policy = () => ({ session_id: 'chat', policy_revision: state.policy_revision, approval_mode: state.approval_mode, route_mode: 'manual', tool_routing: 'existing', effective_roots: [], auto_approve_availability: ready });
    const config = () => ({ revision: state.revision, decision_provider: state.decision_provider, elevation_ceiling: false, elevation_available: false, available_folders: [], auto_approve_availability: ready });
    await page.route('**/static/js/sessions.js', route => route.fulfill({ contentType: 'text/javascript', body: 'export default { getCurrentSessionId: () => "chat", hasPendingChat: () => false };' }));
    await page.route('**/static/js/decision-activity.js', route => route.fulfill({ contentType: 'text/javascript', body: 'export {};' }));
    await page.route('**/api/**', async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      const body = request.postDataJSON(), method = request.method();
      let value;
      if (method !== 'GET') state.writes.push({ pathname, method, body });
      if (pathname === '/api/chat-settings') {
        if (method === 'PUT') {
          assert.equal(body.revision, state.revision);
          if (Object.hasOwn(body.changes, 'decision_provider')) state.decision_provider = body.changes.decision_provider;
          state.revision++;
        }
        value = config();
      } else if (pathname === '/api/session/chat/automation') {
        if (method === 'PUT') {
          assert.equal(body.policy_revision, state.policy_revision);
          if (Object.hasOwn(body.changes, 'approval_mode')) state.approval_mode = body.changes.approval_mode;
          state.policy_revision++;
        }
        value = policy();
      } else if (pathname === '/api/chat-settings/models') {
        const endpoints = [{ id: 'jev-endpoint', name: 'Jev review fixture', models: ['jev-model'] }];
        value = { endpoints, decision_endpoints: endpoints };
      } else if (pathname === '/api/session/chat/approval-context') {
        value = { revision: 0, private: false, reason: 'Review fixture context.' };
      } else {
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Unexpected fixture request: ' + pathname }) });
        return;
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });
    await page.evaluate(async () => { await import('/static/js/chatAutomation.js'); });
    await page.waitForFunction(() => window.ChatAutomation?.getPolicy()?.session_id === 'chat');
    const openSettings = async () => {
      await page.locator('#chat-automation-settings').dispatchEvent('click');
      await page.waitForFunction(() => document.getElementById('chat-automation-dialog').open);
    };
    const endpoint = page.locator('[data-route="decision"] [data-field="endpoint_id"]');
    const model = page.locator('[data-route="decision"] [data-field="model"]');
    await openSettings();
    check(label + ': persisted null opens settings', await page.locator('#chat-automation-dialog').isVisible());
    check(label + ': null route fields are empty', await endpoint.inputValue() === '' && await model.inputValue() === '');
    check(label + ': opening settings performs no writes', state.writes.length === 0);
    await screenshot(page, label + '-null-provider');
    await page.locator('#chat-automation-close').click();
    state.decision_provider = { endpoint_id: 'jev-endpoint', model: 'jev-model', protocol: 'systemone', timeout_seconds: 30, max_calls: 32 };
    state.revision++;
    await openSettings();
    check(label + ': configured route is displayed', await endpoint.inputValue() === 'jev-endpoint' && await model.inputValue() === 'jev-model');
    await endpoint.selectOption(''); await model.fill('');
    await page.locator('#chat-automation-form button[type="submit"]').click();
    await page.waitForFunction(() => document.getElementById('chat-automation-feedback').textContent.startsWith('Settings saved.'));
    const saved = state.writes.find(item => item.pathname === '/api/chat-settings');
    check(label + ': clear submits explicit null', saved?.body?.changes?.decision_provider === null);
    check(label + ': cleared provider persists', state.decision_provider === null);
    check(label + ': saving provider preserves permissions', state.approval_mode === 'always_ask');
    await page.locator('#chat-automation-close').click();
    await openSettings();
    check(label + ': settings reopen after clearing', await endpoint.inputValue() === '' && await model.inputValue() === '');
    await page.locator('#chat-automation-close').click();
    await page.locator('#chat-approval-mode').click();
    await page.locator('#chat-permissions-menu [data-approval-mode="auto_approve"]').click();
    check(label + ': Auto-Approve requires confirmation', state.approval_mode === 'always_ask' && await page.locator('#chat-auto-approve-dialog').isVisible());
    await screenshot(page, label + '-auto-approve-confirmation');
    await page.locator('#chat-auto-approve-enable').click();
    await page.waitForFunction(() => window.ChatAutomation.getPolicy().approval_mode === 'auto_approve');
    check(label + ': confirmation saves Auto-Approve', state.approval_mode === 'auto_approve');
    check(label + ': no controller exception', report.pageErrors.filter(error => error.label === label).length === 0);
    await context.close();
  }
  check('No uncaught page exceptions', report.pageErrors.length === 0);
} catch (error) {
  report.failure = error.stack;
  process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(output, 'browser-review.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
  for (const server of servers) await server.close();
}
console.log(JSON.stringify({ checks: report.checks.length, screenshots: report.screenshots.length, failure: report.failure || null }, null, 2));
