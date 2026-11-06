// Native Deno/CDP client: no browser automation packages or dependency downloads.
const base = 'http://127.0.0.1:9223';
const checks = [];
const errors = [];
const failedRequests = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function assert(condition, name, evidence = {}) {
  checks.push({ name, pass: !!condition, ...evidence });
  if (!condition) throw new Error(`${name}: ${JSON.stringify(evidence)}`);
}

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  let sequence = 0;
  const waiting = new Map();
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = waiting.get(message.id);
      if (!entry) return;
      waiting.delete(message.id);
      clearTimeout(entry.timeout);
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
      else entry.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
    else if (message.method === 'Network.loadingFailed') failedRequests.push(message.params);
  });
  return {
    ws,
    call(method, params = {}) {
      const id = ++sequence;
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => { waiting.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 20000);
        waiting.set(id, { resolve, reject, timeout });
        ws.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

let page;
let browser;
let gpu;
const output = { checks, errors, failedRequests };
try {
  const version = await (await fetch(`${base}/json/version`)).json();
  browser = await connect(version.webSocketDebuggerUrl);
  gpu = await browser.call('SystemInfo.getInfo');
  output.gpu = gpu.gpu;
  const renderer = gpu.gpu.auxAttributes?.glRenderer || JSON.stringify(gpu.gpu.devices);
  assert(!/swiftshader|llvmpipe|softpipe|software rasterizer/i.test(renderer), 'Hardware GPU renderer', { renderer });
  const created = await (await fetch(`${base}/json/new?about:blank`, { method: 'PUT' })).json();
  page = await connect(created.webSocketDebuggerUrl);
  await page.call('Page.enable');
  await page.call('Page.bringToFront');
  await page.call('Runtime.enable');
  await page.call('Network.enable');
  await page.call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1040, deviceScaleFactor: 1, mobile: false });
  await page.call('Page.navigate', { url: 'http://127.0.0.1:8785/docs/icon-suite/' });
  const evaluate = async (expression) => {
    const result = await page.call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  let ready = false;
  for (let i = 0; i < 100; i++) {
    ready = await evaluate('Boolean(window.iconLab?.ready)');
    if (ready) break;
    await sleep(100);
  }
  assert(ready, 'Gallery initializes');
  const initial = await evaluate(`({ cells: document.querySelectorAll('.icon-cell').length,
    count: iconLab.DATA.count, symbols: document.querySelectorAll('symbol').length,
    missing: [...document.querySelectorAll('.icon-cell use')].filter(el => !document.querySelector(el.getAttribute('href'))).length })`);
  assert(initial.cells === 802 && initial.count === 802 && initial.symbols === 802 && initial.missing === 0,
    'All 802 glyphs mounted with resolved symbols', initial);
  const metadataSafety = await evaluate(`(() => {
    const icon=iconLab.DATA.icons.find(x=>x.id==='sailboat'), saved=structuredClone(icon);
    const upstream=iconLab.DATA.upstream, revision=iconLab.DATA.upstream_sha;
    const label='" onload="window.unexpectedMetadataScript=1';
    try {
      icon.svg='javascript:window.unexpectedMetadataScript=1'; icon.label=label;
      icon.uses=[{path:'static/" onclick="unexpected.js',line:'1" onclick="unexpected'}];
      iconLab.DATA.upstream='javascript:window.unexpectedMetadataScript=1';
      iconLab.DATA.upstream_sha='revision/" onclick="unexpected';
      iconLab.selectIcon('sailboat');
      const svg=new DOMParser().parseFromString(document.getElementById('svg-code').value,'image/svg+xml').documentElement;
      const download=new URL(document.getElementById('download-svg').href);
      const source=new URL(document.querySelector('#selected-uses a').href);
      return {label:svg.getAttribute('aria-label')===label, noHandler:!svg.hasAttribute('onload'),
        download:download.protocol==='http:' && download.pathname.endsWith('/static/icons/ui/sailboat.svg'),
        source:source.origin==='https://github.com' && source.pathname.startsWith('/odysseus-dev/odysseus/blob/') && source.hash==='#L1',
        script:!window.unexpectedMetadataScript};
    } finally {
      Object.assign(icon,saved);iconLab.DATA.upstream=upstream;iconLab.DATA.upstream_sha=revision;
      iconLab.selectIcon('sailboat');
    }
  })()`);
  assert(Object.values(metadataSafety).every(Boolean), 'Catalog metadata cannot inject SVG attributes or link protocols', metadataSafety);
  await evaluate(`document.getElementById('search').value='mask';document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}))`);
  const search = await evaluate(`({count:document.querySelectorAll('.icon-cell').length, mask:!!document.querySelector('.icon-cell[data-id="mask"]')})`);
  assert(search.count > 5 && search.count < 60 && search.mask, 'Search filters to relevant masks', search);
  await evaluate(`document.querySelector('.icon-cell[data-id="mask"]').click()`);
  assert(await evaluate(`document.getElementById('selected-name').textContent==='mask' && document.getElementById('svg-code').value.includes('currentColor')`), 'Selection updates editable source and inspector');
  await evaluate(`document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('coverage-select').value='expansion';document.getElementById('coverage-select').dispatchEvent(new Event('change',{bubbles:true}))`);
  assert(await evaluate(`document.querySelectorAll('.icon-cell').length===51`), 'Expanded UI filter exposes 51 proposals');
  await evaluate(`document.getElementById('coverage-select').value='all';document.getElementById('coverage-select').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[data-family="navigation"]').click()`);
  assert(await evaluate(`document.querySelectorAll('.icon-cell').length===26`), 'Family navigation filters catalog');
  await evaluate(`document.querySelector('[data-family="all"]').click();document.getElementById('size').value='14';document.getElementById('size').dispatchEvent(new Event('input',{bubbles:true}))`);
  assert(await evaluate(`getComputedStyle(document.querySelector('.icon-cell svg')).width==='14px'`), '14 px size trial uses actual glyph dimensions');
  await evaluate(`document.getElementById('size').value='28';document.getElementById('size').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('theme-select').value='light';document.getElementById('theme-select').dispatchEvent(new Event('change',{bubbles:true}))`);
  assert(await evaluate(`getComputedStyle(document.body).color==='rgb(38, 49, 58)'`), 'Theme selector applies exact upstream light foreground');
  await evaluate(`document.getElementById('accent-color').value='#8844cc';document.getElementById('accent-color').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('role-select').value='accent';document.getElementById('role-select').dispatchEvent(new Event('change',{bubbles:true}))`);
  assert(await evaluate(`getComputedStyle(document.querySelector('.icon-cell svg')).color==='rgb(136, 68, 204)'`), 'Every SVG inherits a custom parent accent');
  await evaluate(`document.getElementById('reset-theme').click();document.getElementById('theme-select').value='dark';document.getElementById('theme-select').dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('role-select').value='foreground';document.getElementById('role-select').dispatchEvent(new Event('change',{bubbles:true}));iconLab.selectIcon('sailboat')`);
  await sleep(100);
  const saveScreenshot = async (name) => {
    const result = await page.call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await Deno.writeFile(`checks/${name}.png`, Uint8Array.from(atob(result.data), (c) => c.charCodeAt(0)));
  };
  await saveScreenshot('gallery-desktop');
  await evaluate(`document.querySelector('[data-view="themes"]').focus()`);
  await page.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', text: '\r', unmodifiedText: '\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await page.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  const keyboardResult = await evaluate(`({view:iconLab.state.view,hidden:document.getElementById('themes-view').hidden,focus:document.activeElement?.outerHTML.slice(0,300),documentFocus:document.hasFocus()})`);
  assert(keyboardResult.view === 'themes' && !keyboardResult.hidden, 'Keyboard activates theme trials', keyboardResult);
  output.themeRoles = await evaluate(`(() => {
    const rgb = hex => 'rgb('+[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(', ')+')';
    return [...document.querySelectorAll('.trial-row')].map(row => {
      const colors=iconLab.CONTRACT.themes[row.dataset.theme], adv=colors.advanced||{}, cells={};
      for (const cell of row.querySelectorAll('[data-role]')) {
        const el=cell.firstElementChild,s=getComputedStyle(el),svg=el.querySelector('svg');
        cells[cell.dataset.role]={color:s.color,opacity:s.opacity,background:s.backgroundColor,svgColor:getComputedStyle(svg).color};
      }
      const expected={foreground:rgb(colors.fg),'rail-idle':rgb(colors.red),'rail-active':rgb(colors.red),toolbar:rgb(colors.fg),send:'rgb(255, 255, 255)','send-mic':rgb(colors.fg),brand:rgb(adv.brandColor||colors.red),danger:rgb(colors.red)};
      return {theme:row.dataset.theme,cells,expected,pass:Object.entries(expected).every(([role,color])=>cells[role].color===color && cells[role].svgColor===color)
        && cells['rail-idle'].opacity==='0.5' && cells['rail-active'].opacity==='1'
        && cells.send.background===rgb(adv.sendBtnBg||colors.red)};
    });
  })()`);
  assert(output.themeRoles.length === 19 && output.themeRoles.every((row) => row.pass), 'All 19 themes obey actual component color and advanced override rules', { failures: output.themeRoles.filter((row) => !row.pass) });
  await page.call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 2050, deviceScaleFactor: 1, mobile: false });
  await sleep(100);
  await saveScreenshot('theme-trials');
  await evaluate(`iconLab.switchView('references');document.querySelectorAll('.reference-card img').forEach(img=>img.loading='eager')`);
  let references;
  for (let i = 0; i < 100; i++) {
    references = await evaluate(`({count:document.querySelectorAll('.reference-card img').length, ready:[...document.querySelectorAll('.reference-card img')].filter(img=>img.complete&&img.naturalWidth>0).length})`);
    if (references.ready === 7) break;
    await sleep(100);
  }
  assert(references.count === 7 && references.ready === 7, 'Seven saved imagegen references load', references);
  await evaluate(`iconLab.switchView('coverage')`);
  assert(await evaluate(`document.querySelectorAll('#coverage-table tr').length===17`), 'Coverage includes every icon family');
  await page.call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate(`iconLab.switchView('catalog')`);
  await sleep(100);
  const narrow = await evaluate(`({viewport:innerWidth, document:document.documentElement.scrollWidth, families:getComputedStyle(document.querySelector('.families')).display, inspector:getComputedStyle(document.querySelector('.inspector')).display, toolbarBottom:document.querySelector('.theme-toolbar').getBoundingClientRect().bottom, searchTop:document.querySelector('.search-wrap').getBoundingClientRect().top})`);
  assert(narrow.document <= 390 && narrow.families === 'none' && narrow.inspector === 'none', 'Narrow catalog fits viewport', narrow);
  assert(narrow.toolbarBottom <= narrow.searchTop, 'Wrapped theme controls do not overlap search', narrow);
  await saveScreenshot('gallery-narrow');
  assert(errors.length === 0, 'No browser JavaScript exceptions', { errors });
  assert(failedRequests.length === 0, 'No failed resource loads', { failedRequests });
  output.pass = true;
} catch (error) {
  output.pass = false;
  output.failure = String(error.stack || error);
} finally {
  await Deno.writeTextFile('checks/browser.json', JSON.stringify(output, null, 2) + '\n');
  page?.ws.close();
  browser?.ws.close();
}
console.log(JSON.stringify({ pass: output.pass, checks: checks.length, failure: output.failure, renderer: gpu?.gpu?.auxAttributes?.glRenderer }, null, 2));
if (!output.pass) Deno.exit(1);
