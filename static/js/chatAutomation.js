import sessions from './sessions.js';

const $ = id => document.getElementById(id);
const categories = [['established_procedure','Established procedure'],['bounded_domain','Bounded domain'],['approach_discovery','Approach discovery']];
let policy, configuration, endpoints = [], sequence = 0;
const sid = () => sessions.getCurrentSessionId();
async function api(path, method='GET', body) {
  const response = await fetch(path,{method,credentials:'same-origin',headers:body ? {'Content-Type':'application/json'} : {},body:body ? JSON.stringify(body) : undefined});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Settings could not be saved');
  return value;
}
function message(text) { $('chat-automation-feedback').textContent = text; }
async function currentChat() {
  if (!sid()) {
    if (!sessions.hasPendingChat()) {
      const model = await api('/api/default-chat');
      if (!model.endpoint_url || !model.model) throw new Error('Choose a model first');
      sessions.createDirectChat(model.endpoint_url,model.model,model.endpoint_id);
    }
    await sessions.materializePendingSession();
  }
  if (!sid()) throw new Error('Choose a model first');
  return sid();
}
function render() {
  $('chat-approval-mode').value = policy?.approval_mode || configuration?.last_explicit_mode || 'always_ask';
  $('chat-route-mode').value = policy?.route_mode || 'manual';
  const roots = policy?.effective_roots || [];
  $('chat-automation-state').textContent = roots.length ? `${roots.length} extra folder(s) requested · sandbox checked at execution` : 'Workspace scope requested · sandbox checked at execution';
  $('chat-automation-state').title = 'Actual execution requires a working sandbox. YOLO skips discretionary approval within the current permissions.';
}
async function refresh() {
  const ticket = ++sequence, id = sid();
  try {
    const config = await api('/api/chat-settings');
    const chat = id ? await api(`/api/session/${encodeURIComponent(id)}/automation`) : null;
    if (ticket !== sequence || id !== sid()) return;
    configuration = config; policy = chat; render();
  } catch(e) { if(ticket===sequence) $('chat-automation-state').textContent=e.message; }
}
async function changeChat(changes) {
  const id = await currentChat();
  if (!policy || policy.session_id !== id) policy = await api(`/api/session/${encodeURIComponent(id)}/automation`);
  policy = await api(`/api/session/${encodeURIComponent(id)}/automation`,'PUT',{policy_revision:policy.policy_revision,changes});
  render();
}
function routeFields(key, title, value={}) {
  const row = document.createElement('div'); row.className='chat-automation-route'; row.dataset.route=key;
  const heading=document.createElement('strong'); heading.textContent=title; row.append(heading);
  function label(text,element){const l=document.createElement('label');l.append(document.createTextNode(text),element);row.append(l);}
  const endpoint=document.createElement('select');endpoint.dataset.field='endpoint_id';
  endpoint.append(new Option('Choose endpoint',''));
  endpoints.forEach(e=>endpoint.append(new Option(e.name,e.id)));endpoint.value=value.endpoint_id||'';label('Endpoint',endpoint);
  const model=document.createElement('input');model.dataset.field='model';model.value=value.model||'';model.autocomplete='off';model.setAttribute('list',`chat-models-${key}`);label('Model',model);
  const list=document.createElement('datalist');list.id=`chat-models-${key}`;row.append(list);
  const options=()=>{list.replaceChildren();(endpoints.find(e=>e.id===endpoint.value)?.models||[]).forEach(m=>list.append(new Option(m)));};endpoint.addEventListener('change',options);options();
  if(key!=='decision') {const effort=document.createElement('select');effort.dataset.field='reasoning_effort';[['','Default'],...['none','minimal','low','medium','high','xhigh','max'].map(v=>[v,v])].forEach(([v,t])=>effort.append(new Option(t,v)));effort.value=value.reasoning_effort||'';label('Effort',effort);}
  return row;
}
function readRoute(key) {
  const row=document.querySelector(`[data-route="${key}"]`), result={};
  row.querySelectorAll('[data-field]').forEach(e=>result[e.dataset.field]=e.value);
  if('reasoning_effort' in result && !result.reasoning_effort) result.reasoning_effort=null;
  return result;
}
async function openSettings() {
  try {
    await refresh(); endpoints=(await api('/api/chat-settings/models')).endpoints;
    $('chat-elevation-ceiling').checked=!!configuration.elevation_ceiling;
    $('chat-elevation-explanation').textContent=configuration.elevation_available ? 'Allows a separate chat grant for listed folders. Mode or ceiling changes revoke existing grants. Process controls remain required.' : 'The deployment currently disallows extra folders. The ceiling cannot override that restriction.';
    $('chat-elevation-folders').replaceChildren();
    (configuration.available_folders||[]).forEach(path=>{const label=document.createElement('label'),check=document.createElement('input'),access=document.createElement('select');check.type='checkbox';check.value=path;check.checked=(policy?.effective_roots||[]).some(r=>r.path===path);access.append(new Option('Read only','read'),new Option('Read and write','write'));access.value=(policy?.effective_roots||[]).find(r=>r.path===path)?.access||'read';label.append(check,document.createTextNode(path),access);$('chat-elevation-folders').append(label);});
    $('chat-elevation-save').disabled=!configuration.elevation_available;
    $('chat-decision-route').replaceChildren(routeFields('decision','Review and approach decisions',configuration.decision_provider));
    $('chat-decision-protocol').value=configuration.decision_provider?.protocol||'openai_json';
    $('chat-approach-routes').replaceChildren(...categories.map(([key,title])=>routeFields(key,title,configuration.approaches?.[key])));
    message('');$('chat-automation-dialog').showModal();
  } catch(e){$('chat-automation-state').textContent=e.message;}
}
async function initialize(){
  if(!$('chat-approval-mode'))return;
  for(const [id,key] of [['chat-approval-mode','approval_mode'],['chat-route-mode','route_mode']]) $(id).addEventListener('change',async e=>{const value=e.target.value;e.target.disabled=true;try{await changeChat({[key]:value});}catch(error){$('chat-automation-state').textContent=error.message;await refresh();}finally{e.target.disabled=false;}});
  $('chat-automation-settings').addEventListener('click',openSettings);
  $('chat-automation-close').addEventListener('click',()=>$('chat-automation-dialog').close());
  const browserPath=async()=>`/api/session/${encodeURIComponent(await currentChat())}/browser`;
  function browserScope(){const effects=[];if($('chat-browser-clicks').checked)effects.push('click');if($('chat-browser-fill').checked)effects.push('fill');if($('chat-browser-submit').checked)effects.push('submit');const label=$('chat-browser-field-label').value;return {url:$('chat-browser-url').value,effects,fields:label?[{label,value:$('chat-browser-field-value').value}]:[]};}
  async function viewBrowser(){const result=await api((await browserPath())+'/view');$('chat-browser-screenshot').src=result.screenshot.url;$('chat-browser-screenshot').hidden=false;$('chat-browser-status').textContent='You have control. This is a current browser image; automation’s previous targets have been invalidated.';const obs=result.observation;$('chat-browser-targets').replaceChildren();(obs.elements||[]).filter(n=>!n.disabled).forEach(node=>{const button=document.createElement('button');button.type='button';button.textContent=(node.editable?'Fill: ':'Click: ')+(node.label||node.role||'Unnamed target');button.addEventListener('click',async()=>{try{await api((await browserPath())+'/action','POST',{action:node.editable?'fill':'click',page:'t1',observation:obs.observation,ref:node.ref,...(node.editable?{value_id:node.label}:{})});await viewBrowser();}catch(e){$('chat-browser-status').textContent=e.message;}});$('chat-browser-targets').append(button);});}
  for(const [id,handler] of [['chat-browser-open',async()=>{await api(await browserPath(),'POST');$('chat-browser-status').textContent='Browser opened. Choose a page and allowed actions before using it.';}],['chat-browser-close',async()=>{const result=await api(await browserPath(),'DELETE');$('chat-browser-status').textContent=result.status==='closed'?'Browser closed.':'Browser cleanup is incomplete.';$('chat-browser-screenshot').hidden=true;$('chat-browser-targets').replaceChildren();}],['chat-browser-scope',async()=>{await api((await browserPath())+'/scope','POST',browserScope());$('chat-browser-status').textContent='Allowed for your next message for two minutes. Only the selected site and actions are permitted.';}],['chat-browser-navigate',async()=>{await api((await browserPath())+'/scope','POST',browserScope());await api((await browserPath())+'/action','POST',{action:'navigate',page:'t1',url:$('chat-browser-url').value});await viewBrowser();}],['chat-browser-view',viewBrowser]]) $(id).addEventListener('click',async()=>{try{await handler();}catch(e){$('chat-browser-status').textContent=e.message;}});
  $('chat-elevation-save').addEventListener('click',async()=>{try{const roots=[...$('chat-elevation-folders').querySelectorAll('label')].filter(l=>l.querySelector('input').checked).map(l=>({path:l.querySelector('input').value,access:l.querySelector('select').value}));await changeChat({elevation_roots:roots});$('chat-elevation-status').textContent='Folder grant saved for this chat for one hour.';}catch(e){$('chat-elevation-status').textContent=e.message;}});
  $('chat-automation-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const changes={elevation_ceiling:$('chat-elevation-ceiling').checked};const decision=readRoute('decision');if(decision.endpoint_id||decision.model)changes.decision_provider={...decision,protocol:$('chat-decision-protocol').value,timeout_seconds:30,max_calls:32};const mappings=Object.fromEntries(categories.map(([key])=>[key,readRoute(key)]));if(Object.values(mappings).some(m=>m.endpoint_id||m.model))changes.approaches=mappings;configuration=await api('/api/chat-settings','PUT',{revision:configuration.revision,changes});await refresh();message('Settings saved. New messages use the committed selection.');}catch(error){message(error.message);}finally{button.disabled=false;}});
  window.addEventListener('odysseus:session-changed',refresh);
  window.addEventListener('odysseus:automation-changed',refresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  await refresh();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initialize);else initialize();
