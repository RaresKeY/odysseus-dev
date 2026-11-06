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
  const saveScreenshot = async (name) => {
    const result = await page.call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await Deno.writeFile(`checks/${name}.png`, Uint8Array.from(atob(result.data), (c) => c.charCodeAt(0)));
  };
  const comparison = await evaluate(`({view:iconLab.state.view,hash:location.hash,
    count:document.querySelectorAll('.comparison-card').length,expected:iconLab.COMPARISON.paired_icons,
    columns:getComputedStyle(document.getElementById('comparison-grid')).gridTemplateColumns.split(' ').length,
    oldSize:getComputedStyle(document.querySelector('.old-slot .original-icon')).width,
    newSize:getComputedStyle(document.querySelector('.new-slot svg')).width,
    inspector:getComputedStyle(document.querySelector('.inspector')).display})`);
  assert(comparison.view==='compare' && comparison.hash==='#compare' && comparison.count===comparison.expected
    && comparison.columns>=4 && comparison.oldSize==='56px' && comparison.newSize==='56px' && comparison.inspector==='none',
    'Large paired comparison grid is the default view', comparison);
  await saveScreenshot('comparison-desktop');
  const variants = await evaluate(`(() => {
    const select=document.querySelector('.original-variants'), card=select.closest('.comparison-card');
    const old=card.querySelector('.old-slot .comparison-preview').dataset.original;
    const fresh=card.querySelector('.new-slot use').getAttribute('href');
    select.value='1';select.dispatchEvent(new Event('change',{bubbles:true}));
    return {changed:old!==card.querySelector('.old-slot .comparison-preview').dataset.original,
      unchanged:fresh===card.querySelector('.new-slot use').getAttribute('href'),source:card.querySelector('.comparison-footer a').href.startsWith('https://github.com/odysseus-dev/odysseus/blob/')};
  })()`);
  assert(Object.values(variants).every(Boolean), 'Old SVG variants change independently of the new glyph', variants);
  const pickTarget = await evaluate(`(() => {
    const card=document.querySelector('.original-variants').closest('.comparison-card');
    window.reviewTestId=card.dataset.id;
    card.scrollIntoView({block:'nearest'});
    const slot=card.querySelector('.old-slot'), rect=slot.getBoundingClientRect();
    return {x:rect.x+8,y:rect.y+8,color:getComputedStyle(slot).color,
      original:card.querySelector('.old-slot .comparison-preview').dataset.original};
  })()`);
  await page.call('Input.dispatchMouseEvent', {type:'mousePressed',x:pickTarget.x,y:pickTarget.y,button:'left',clickCount:1});
  await page.call('Input.dispatchMouseEvent', {type:'mouseReleased',x:pickTarget.x,y:pickTarget.y,button:'left',clickCount:1});
  const oldWinner = await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    const old=card.querySelector('.old-slot'), fresh=card.querySelector('.new-slot');
    return {winner:card.dataset.winner,locked:old.disabled&&fresh.disabled&&card.querySelector('select').disabled,
      losers:card.querySelectorAll('.is-loser').length,oldOpacity:getComputedStyle(old).opacity,
      oldFilter:getComputedStyle(old).filter,oldColor:getComputedStyle(old).color,
      newOpacity:getComputedStyle(fresh).opacity,newFilter:getComputedStyle(fresh).filter,
      cardOpacity:getComputedStyle(card).opacity,unlock:!card.querySelector('.unlock-choice').hidden,
      count:document.getElementById('winner-count').textContent};
  })()`);
  assert(oldWinner.winner==='old' && oldWinner.locked && oldWinner.losers===1 && oldWinner.oldOpacity==='1'
    && oldWinner.oldFilter==='none' && oldWinner.oldColor===pickTarget.color && oldWinner.newOpacity==='0.32'
    && oldWinner.newFilter==='grayscale(1)' && oldWinner.cardOpacity==='1' && oldWinner.unlock && oldWinner.count==='1 picked',
    'Clicking the whole old side locks the pick and grays only the unchosen new side', oldWinner);
  assert(await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    card.querySelector('.new-slot').click();
    return card.dataset.winner==='old' && iconLab.choiceExport().choices[0].old.original===card.querySelector('.old-slot .comparison-preview').dataset.original;
  })()`), 'Locked choices cannot be overwritten and retain the exact reviewed old variant');
  await saveScreenshot('choices-old-winner');
  const exported = await evaluate(`iconLab.choiceExport()`);
  assert(exported.choices.length===1 && exported.choices[0].winner==='old'
    && exported.choices[0].old.original===pickTarget.original && exported.choices[0].old.source_url.startsWith('https://github.com/'),
    'Pick export includes the winner, pinned source variant and provenance');
  await page.call('Page.reload', {ignoreCache:true});
  await sleep(100);
  for (let i=0;i<100;i++) {
    if (await evaluate('Boolean(window.iconLab?.ready)')) break;
    await sleep(50);
  }
  const restored = await evaluate(`(() => {
    const id=iconLab.choiceExport().choices[0].id;
    window.reviewTestId=id;
    const card=document.querySelector('.comparison-card[data-id="'+id+'"]');
    return {winner:card.dataset.winner,variant:card.querySelector('.old-slot .comparison-preview').dataset.original,
      select:card.querySelector('select').disabled,count:document.getElementById('winner-count').textContent};
  })()`);
  assert(restored.winner==='old' && restored.variant===pickTarget.original && restored.select && restored.count==='1 picked',
    'Winner, lock and selected old variant survive a real page reload', restored);
  const unlocked = await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    card.querySelector('.unlock-choice').click();
    return {winner:card.dataset.winner,losers:card.querySelectorAll('.is-loser').length,
      enabled:[...card.querySelectorAll('.winner-option,select')].every(el=>!el.disabled),
      focus:document.activeElement===card.querySelector('.old-slot'),count:iconLab.choiceExport().choices.length};
  })()`);
  assert(unlocked.winner==='' && unlocked.losers===0 && unlocked.enabled && unlocked.focus && unlocked.count===0,
    'Unlock reopens both sides, restores color and returns keyboard focus', unlocked);
  await evaluate(`document.querySelector('.comparison-card[data-id="'+reviewTestId+'"] .new-slot').focus()`);
  await page.call('Input.dispatchKeyEvent', {type:'keyDown',key:'Enter',code:'Enter',text:'\r',windowsVirtualKeyCode:13});
  await page.call('Input.dispatchKeyEvent', {type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  assert(await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    return card.dataset.winner==='new' && card.querySelector('.old-slot').classList.contains('is-loser')
      && getComputedStyle(card.querySelector('.new-slot')).opacity==='1'
      && getComputedStyle(card.querySelector('.new-slot')).filter==='none'
      && document.activeElement===card.querySelector('.unlock-choice');
  })()`), 'Keyboard can choose new while only old grays out and Unlock remains reachable');
  await saveScreenshot('choices-desktop');
  await evaluate(`document.getElementById('search').value=reviewTestId;document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('role-select').value='accent';document.getElementById('role-select').dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('theme-select').value='light';document.getElementById('theme-select').dispatchEvent(new Event('change',{bubbles:true}))`);
  assert(await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    return card.dataset.winner==='new' && card.querySelector('.old-slot').classList.contains('is-loser')
      && getComputedStyle(card.querySelector('.new-slot')).color===getComputedStyle(card.querySelector('.comparison-pair')).color
      && getComputedStyle(card.querySelector('.new-slot')).filter==='none';
  })()`), 'Picks and winner ink survive filtering, role changes and theme changes');
  await saveScreenshot('choices-light');
  await page.call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert(await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    return document.documentElement.scrollWidth<=390 && card.querySelectorAll('.winner-option').length===2
      && card.querySelector('.unlock-choice').getBoundingClientRect().width>0
      && getComputedStyle(card.querySelector('.new-slot')).opacity==='1';
  })()`), 'Narrow layout keeps both winner sides and Unlock visible');
  await saveScreenshot('choices-narrow');
  await page.call('Emulation.setDeviceMetricsOverride', {width:1600,height:1040,deviceScaleFactor:1,mobile:false});
  const storageFailure = await evaluate(`(() => {
    const card=document.querySelector('.comparison-card[data-id="'+reviewTestId+'"]');
    window.originalSetItem=Storage.prototype.setItem;
    Storage.prototype.setItem=()=>{throw new DOMException('Storage blocked','QuotaExceededError')};
    card.querySelector('.unlock-choice').click();card.querySelector('.old-slot').click();
    const result=card.dataset.winner==='old' && document.getElementById('choice-save-status').classList.contains('save-failed')
      && !document.getElementById('export-choices').disabled && iconLab.choiceExport().choices.length===1;
    Storage.prototype.setItem=window.originalSetItem;
    card.querySelector('.unlock-choice').click();
    return result;
  })()`);
  assert(storageFailure, 'Blocked storage leaves picks usable and exportable with visible save feedback');
  await evaluate(`document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('role-select').value='foreground';document.getElementById('role-select').dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('theme-select').value='dark';document.getElementById('theme-select').dispatchEvent(new Event('change',{bubbles:true}))`);
  await evaluate(`document.getElementById('inspect-toggle').click()`);
  assert(await evaluate(`getComputedStyle(document.querySelector('.inspector')).display!=='none'`), 'Comparison inspector can be opened');
  await evaluate(`document.getElementById('inspect-toggle').click();document.querySelectorAll('.compare-select')[1].focus()`);
  await page.call('Input.dispatchKeyEvent', {type:'keyDown',key:'Enter',code:'Enter',text:'\r',windowsVirtualKeyCode:13});
  await page.call('Input.dispatchKeyEvent', {type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  assert(await evaluate(`document.querySelectorAll('.comparison-card')[1].dataset.id===document.getElementById('selected-name').textContent`), 'Keyboard selects a comparison pair');
  await evaluate(`document.getElementById('comparison-scope').value='all';document.getElementById('comparison-scope').dispatchEvent(new Event('change',{bubbles:true}))`);
  assert(await evaluate(`document.querySelectorAll('.comparison-card').length===802 && document.querySelectorAll('.comparison-missing').length===802-iconLab.COMPARISON.paired_icons`), 'All 802 new icons have a comparison slot without invented originals');
  await evaluate(`document.getElementById('comparison-scope').value='unpaired';document.getElementById('comparison-scope').dispatchEvent(new Event('change',{bubbles:true}))`);
  assert(await evaluate(`document.querySelectorAll('.comparison-card').length===802-iconLab.COMPARISON.paired_icons && document.querySelectorAll('.old-slot .original-icon,.old-slot .original-text').length===0`), 'Unmatched filter shows explicit empty counterparts');
  assert(await evaluate(`(() => {
    const card=document.querySelector('.comparison-card');
    card.querySelector('.old-slot').click();
    const unavailable=card.dataset.winner==='' && card.querySelector('.old-slot').disabled;
    card.querySelector('.new-slot').click();
    const chosen=card.dataset.winner==='new' && card.querySelectorAll('.is-loser').length===0;
    card.querySelector('.unlock-choice').click();
    return unavailable && chosen && iconLab.choiceExport().choices.length===0;
  })()`), 'Entries without originals cannot choose an invented old winner');
  await evaluate(`document.getElementById('comparison-scope').value='paired';document.getElementById('comparison-scope').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[data-family="actions"]').click()`);
  assert(await evaluate(`iconLab.state.view==='compare' && [...document.querySelectorAll('.comparison-card')].every(card=>iconLab.DATA.icons.find(x=>x.id===card.dataset.id).category==='actions')`), 'Family filters preserve comparison mode');
  await evaluate(`document.querySelector('[data-family="all"]').click();document.getElementById('search').value='undo';document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}))`);
  assert(await evaluate(`!!document.querySelector('.comparison-card[data-id="undo"] .original-icon') && document.querySelectorAll('.comparison-card').length<20`), 'Comparison search finds actual undo originals');
  await evaluate(`document.getElementById('comparison-size').value='88';document.getElementById('comparison-size').dispatchEvent(new Event('input',{bubbles:true}))`);
  assert(await evaluate(`getComputedStyle(document.querySelector('.comparison-card[data-id="undo"] .old-slot svg')).width==='88px' && getComputedStyle(document.querySelector('.comparison-card[data-id="undo"] .new-slot svg')).width==='88px'`), 'Comparison size applies equally to old and new geometry');
  await evaluate(`document.getElementById('comparison-size').value='56';document.getElementById('comparison-size').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('same-ink').click()`);
  assert(await evaluate(`iconLab.state.sameInk===false && document.querySelectorAll('.comparison-card').length===iconLab.COMPARISON.paired_icons`), 'Original paint can be restored');
  await evaluate(`document.getElementById('same-ink').click();document.getElementById('theme-select').value='light';document.getElementById('theme-select').dispatchEvent(new Event('change',{bubbles:true}));iconLab.selectIcon('sailboat')`);
  const comparisonInk = await evaluate(`({old:getComputedStyle(document.querySelector('.old-slot svg')).color,new:getComputedStyle(document.querySelector('.new-slot svg')).color,unsafe:document.querySelectorAll('.old-slot script,.old-slot image,.old-slot foreignObject,.old-slot [href],.old-slot [onload]').length})`);
  assert(comparisonInk.old==='rgb(38, 49, 58)' && comparisonInk.new===comparisonInk.old && comparisonInk.unsafe===0, 'Paired icons inherit the light theme with inert original SVGs', comparisonInk);
  await saveScreenshot('comparison-light');
  await page.call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate(`document.getElementById('theme-select').value='dark';document.getElementById('theme-select').dispatchEvent(new Event('change',{bubbles:true}))`);
  const narrowComparison = await evaluate(`({width:document.documentElement.scrollWidth,columns:getComputedStyle(document.getElementById('comparison-grid')).gridTemplateColumns.split(' ').length,old:getComputedStyle(document.querySelector('.old-slot svg')).width,new:getComputedStyle(document.querySelector('.new-slot svg')).width})`);
  assert(narrowComparison.width<=390 && narrowComparison.columns===1 && narrowComparison.old===narrowComparison.new, 'Narrow comparison keeps both columns without horizontal overflow', narrowComparison);
  await saveScreenshot('comparison-narrow');
  await page.call('Emulation.setDeviceMetricsOverride', {width:1600,height:1040,deviceScaleFactor:1,mobile:false});
  await evaluate(`iconLab.switchView('catalog')`);
  const initial = await evaluate(`({ cells: document.querySelectorAll('.icon-cell').length,
    count: iconLab.DATA.count, symbols: document.querySelectorAll('symbol').length,
    missing: [...document.querySelectorAll('.icon-cell use')].filter(el => !document.querySelector(el.getAttribute('href'))).length })`);
  assert(initial.cells === 802 && initial.count === 802 && initial.symbols === 802 && initial.missing === 0,
    'All 802 glyphs mounted with resolved symbols', initial);
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
