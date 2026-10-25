import sessions from './sessions.js';
import './decision-activity.js';

const $ = id => document.getElementById(id);
let policy, configuration, endpoints = [], decisionEndpoints = [], sequence = 0;
let settingsWrite = Promise.resolve(true);
let navigationGeneration = 0;
let settingsChatId = null, settingsNavigation = 0;
let activity = null, activitySessionId = null;
let saving = false, activationChat = null, activationNavigation = null;
const modeLabels = {always_ask:'Always Ask',auto_approve:'Auto-Approve',yolo:'YOLO'};
function notice(text='') { const el=$('chat-automation-notice'); el.textContent=text; el.hidden=!text; }
function permissionFeedback(text='') { $('chat-permissions-feedback').textContent=text; notice(text); }
function closePermissions(restore=false) {
  $('chat-permissions-menu').hidden=true;
  $('chat-permissions-control').append($('chat-permissions-menu'));
  $('chat-approval-mode').setAttribute('aria-expanded','false');
  if(restore)$('chat-approval-mode').focus();
}
function positionPermissions() {
  const menu=$('chat-permissions-menu');if(menu.hidden)return;
  const trigger=$('chat-permissions-control').getBoundingClientRect();
  const left=Math.max(12,Math.min(trigger.left,window.innerWidth-menu.offsetWidth-12));
  menu.style.left=`${left}px`;menu.style.right='auto';menu.style.bottom='auto';
  menu.style.maxHeight=`${Math.max(80,Math.min(window.innerHeight*.65,trigger.top-20))}px`;
  menu.style.top=`${Math.max(10,trigger.top-menu.offsetHeight-10)}px`;
}
function openPermissions() {
  if(saving)return;
  // The native toolbar intentionally clips collapsed tools. Like More tools,
  // portal this menu above that container while preserving its trigger owner.
  document.body.append($('chat-permissions-menu'));
  $('chat-permissions-menu').hidden=false;
  positionPermissions();
  $('chat-approval-mode').setAttribute('aria-expanded','true');
  ($('chat-permissions-menu').querySelector('[aria-checked="true"]') || $('chat-permissions-menu').querySelector('button')).focus();
}
function controlsBusy(busy) {
  saving=busy;
  $('chat-approval-mode').disabled=busy || !configuration;
  $('chat-tool-routing').disabled=busy;
  $('chat-automation-default').disabled=busy;
  $('chat-permissions-menu').setAttribute('aria-busy',String(busy));
  $('chat-permissions-menu').querySelectorAll('button').forEach(b=>b.disabled=busy);
}
export async function whenAutomationSettingsReady() {
  const ready = await settingsWrite;
  if (!ready) settingsWrite = Promise.resolve(true);
  return ready;
}
function writeSettings(operation) {
  const pending = settingsWrite.then(operation);
  settingsWrite = pending.then(()=>true,()=>false);
  return pending;
}
const sid = () => sessions.getCurrentSessionId();
async function api(path, method='GET', body) {
  const response = await fetch(path,{method,credentials:'same-origin',headers:body ? {'Content-Type':'application/json'} : {},body:body ? JSON.stringify(body) : undefined});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Settings could not be saved');
  return value;
}
function message(text) { $('chat-automation-feedback').textContent = text; }
async function currentChat(seed) {
  const existingId=sid(), navigation=navigationGeneration;
  if (!existingId) {
    if (!sessions.hasPendingChat()) {
      let model;
      if(seed){
        const item=(window.modelsModule?.getCachedItems?.() || []).find(item=>item.endpoint_id===seed.endpoint_id && (item.models || []).concat(item.models_extra || []).includes(seed.model));
        if(!item || item.offline)throw new Error('The selected Auto model is unavailable. Refresh the model picker.');
        model={endpoint_url:item.url,model:seed.model,endpoint_id:seed.endpoint_id};
      }else model=await api('/api/default-chat');
      if(navigationGeneration!==navigation || sid()) throw new Error('The chat changed. Choose the setting again.');
      if (!model.endpoint_url || !model.model) throw new Error('Choose a model first');
      sessions.createDirectChat(model.endpoint_url,model.model,model.endpoint_id);
    }
    let materializedId=null;
    const capture=event=>{if(event.detail?.materialized)materializedId=event.detail.sessionId;};
    window.addEventListener('odysseus:session-changed',capture);
    let materialized=false;
    try{materialized=await sessions.materializePendingSession();}finally{window.removeEventListener('odysseus:session-changed',capture);}
    if(!materialized || !materializedId || sid()!==materializedId)throw new Error('The chat changed. Choose the setting again.');
    return materializedId;
  }
  return existingId;
}
function render() {
  const mode=policy?.approval_mode || configuration?.last_explicit_mode || 'always_ask';
  $('chat-permissions-label').textContent=modeLabels[mode] || 'Permissions';
  $('chat-permissions-short').textContent={always_ask:'Ask',auto_approve:'Auto',yolo:'YOLO'}[mode] || '…';
  $('chat-approval-mode').title=`Permissions for this chat: ${modeLabels[mode] || 'Loading'}`;
  $('chat-approval-mode').value=mode;
  $('chat-approval-mode').setAttribute('aria-label',`Change permissions: ${modeLabels[mode] || 'Permissions'}`);
  $('chat-permissions-control').classList.toggle('mode-yolo',mode==='yolo');
  $('chat-permissions-mark').setAttribute('d',mode==='yolo'?'m8 8 8 8M16 8l-8 8':'m9 12 2 2 4-4');
  $('chat-permissions-menu').querySelectorAll('[data-approval-mode]').forEach(b=>b.setAttribute('aria-checked',String(b.dataset.approvalMode===mode)));
  controlsBusy(saving);
  window.dispatchEvent(new CustomEvent('odysseus:automation-policy',{detail:policy}));
  $('chat-tool-routing').value = policy?.tool_routing || 'existing';
  const roots = policy?.effective_roots || [];
  $('chat-automation-state').textContent = policy?.restriction || (roots.length ? `${roots.length} extra folder(s) allowed` : 'Workspace only');
  $('chat-automation-state').title = 'Actual execution requires a working sandbox. YOLO skips discretionary approval within the current permissions.';
  $('chat-resume-input').hidden=!(policy?.task?.pending_question?.kind==='clarification' && policy.task.pending_question.input_card_id);
}
async function refresh() {
  const ticket = ++sequence, id = sid();
  try {
    const config = await api('/api/chat-settings');
    const chat = id ? await api(`/api/session/${encodeURIComponent(id)}/automation`) : null;
    if (ticket !== sequence || id !== sid()) return;
    configuration = config; policy = chat; render();
    if(activitySessionId!==id){activity?.destroy();activity=null;activitySessionId=id;if(id)activity=window.DecisionActivity?.mount($('chat-decision-activity'),id);}
  } catch(e) { if(ticket===sequence){notice(e.message);$('chat-approval-mode').disabled=true;} }
}
function changeChat(changes, context) {
  const requestedId=sid(), requestedNavigation=navigationGeneration;
  return writeSettings(async()=>{
    controlsBusy(true);
    $('chat-automation-state').textContent='Saving chat settings…';
    try {
      if(requestedNavigation!==navigationGeneration || sid()!==requestedId) throw new Error('The chat changed. Choose the setting again in the current chat.');
      const id = requestedId || await currentChat(changes.auto_models?.[0]), ticket=++sequence;
      const previous=policy?.session_id===id ? policy : await api(`/api/session/${encodeURIComponent(id)}/automation`);
      if(context && (context.navigation!==requestedNavigation || context.session_id!==requestedId || (requestedId && context.policy_revision!==previous.policy_revision)))throw new Error('The chat settings changed. Reopen Auto models before saving.');
      const saved=await api(`/api/session/${encodeURIComponent(id)}/automation`,'PUT',{policy_revision:previous.policy_revision,changes});
      if(id===sid() && ticket===sequence){++sequence;policy=saved;render();}else await refresh();
      return saved;
    } finally {controlsBusy(false);}
  });
}
function routeFields(key, title, value={}) {
  const row = document.createElement('div'); row.className='chat-automation-route'; row.dataset.route=key;
  const heading=document.createElement('strong'); heading.textContent=title; row.append(heading);
  function label(text,element){const l=document.createElement('label');l.append(document.createTextNode(text),element);row.append(l);}
  const endpoint=document.createElement('select');endpoint.dataset.field='endpoint_id';
  endpoint.append(new Option('Choose endpoint',''));
  const choices=key==='decision'?decisionEndpoints:endpoints;
  choices.forEach(e=>endpoint.append(new Option(e.name,e.id)));endpoint.value=value.endpoint_id||'';label('Endpoint',endpoint);
  const model=document.createElement('input');model.dataset.field='model';model.value=value.model||'';model.autocomplete='off';model.setAttribute('list',`chat-models-${key}`);label('Model',model);
  const list=document.createElement('datalist');list.id=`chat-models-${key}`;row.append(list);
  const options=()=>{list.replaceChildren();(choices.find(e=>e.id===endpoint.value)?.models||[]).forEach(m=>list.append(new Option(m)));};endpoint.addEventListener('change',options);options();
  return row;
}
function readRoute(key) {
  const row=document.querySelector(`[data-route="${key}"]`), result={};
  row.querySelectorAll('[data-field]').forEach(e=>result[e.dataset.field]=e.value);
  if('reasoning_effort' in result && !result.reasoning_effort) result.reasoning_effort=null;
  if(result.supported_efforts?.trim()) result.supported_efforts=result.supported_efforts.split(',').map(v=>v.trim());
  else delete result.supported_efforts;
  return result;
}
async function openSettings() {
  try {
    await refresh(); const choices=await api('/api/chat-settings/models');endpoints=choices.endpoints;decisionEndpoints=choices.decision_endpoints||choices.endpoints;
    $('chat-elevation-ceiling').checked=!!configuration.elevation_ceiling;
    $('chat-elevation-explanation').textContent=configuration.elevation_available ? 'Allows a separate chat grant for listed folders. Mode or ceiling changes revoke existing grants. Process controls remain required.' : 'The deployment currently disallows extra folders. The ceiling cannot override that restriction.';
    $('chat-elevation-folders').replaceChildren();
    (configuration.available_folders||[]).forEach(path=>{const label=document.createElement('label'),check=document.createElement('input'),access=document.createElement('select');check.type='checkbox';check.value=path;check.checked=(policy?.effective_roots||[]).some(r=>r.path===path);access.append(new Option('Read only','read'),new Option('Read and write','write'));access.value=(policy?.effective_roots||[]).find(r=>r.path===path)?.access||'read';label.append(check,document.createTextNode(path),access);$('chat-elevation-folders').append(label);});
    $('chat-elevation-save').disabled=!configuration.elevation_available;
    $('chat-decision-route').replaceChildren(routeFields('decision','Review and approach decisions',configuration.decision_provider));
    $('chat-decision-protocol').value=configuration.decision_provider?.protocol||'openai_json';
    for(const name of ['followup','preparation','completion-advice']) $( `chat-${name}-enabled`).checked=!!policy?.[name.replaceAll('-','_')+'_enabled'];
    settingsChatId=sid();settingsNavigation=navigationGeneration;
    message('');$('chat-automation-dialog').showModal();
  } catch(e){notice(e.message);}
}
async function initialize(){
  if(!$('chat-approval-mode'))return;
  window.ChatAutomation={
    getPolicy:()=>policy,
    selectionContext:()=>({session_id:sid(),navigation:navigationGeneration,policy_revision:policy?.policy_revision}),
    saveAutoModels:(models,context)=>changeChat({auto_models:models,route_mode:'auto'},context),
    enableAuto:()=>changeChat({route_mode:'auto'}),
  };
  $('chat-tool-routing').addEventListener('change',async e=>{
    try {await changeChat({tool_routing:e.target.value});message('Tool routing saved.');}
    catch(error){message(error.message);render();}
  });
  $('chat-approval-mode').addEventListener('click',()=>{
    if($('chat-permissions-menu').hidden)openPermissions();else closePermissions(true);
  });
  $('chat-approval-mode').addEventListener('keydown',e=>{
    if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();openPermissions();}
  });
  $('chat-permissions-menu').addEventListener('keydown',e=>{
    if(e.key==='Escape'){e.preventDefault();closePermissions(true);return;}
    if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key))return;
    e.preventDefault();const buttons=[...e.currentTarget.querySelectorAll('button:not(:disabled)')];
    if(!buttons.length)return;
    const i=buttons.indexOf(document.activeElement);
    buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length].focus();
  });
  const insidePermissions=target=>$('chat-permissions-control').contains(target) || $('chat-permissions-menu').contains(target);
  document.addEventListener('pointerdown',e=>{if(!insidePermissions(e.target))closePermissions();});
  window.addEventListener('resize',positionPermissions);
  window.visualViewport?.addEventListener('resize',positionPermissions);
  window.visualViewport?.addEventListener('scroll',positionPermissions);
  for(const id of ['chat-permissions-control','chat-permissions-menu'])$(id).addEventListener('focusout',e=>{if(e.relatedTarget && !insidePermissions(e.relatedTarget))closePermissions();});
  $('chat-permissions-menu').querySelectorAll('[data-approval-mode]').forEach(button=>button.addEventListener('click',async()=>{
    const mode=button.dataset.approvalMode;
    if(saving)return;
    permissionFeedback('');
    if(mode==='auto_approve' && $('chat-approval-mode').value!=='auto_approve'){
      activationChat=sid();activationNavigation=navigationGeneration;
      closePermissions();$('chat-auto-approve-feedback').textContent='';$('chat-auto-approve-dialog').showModal();return;
    }
    try{await changeChat({approval_mode:mode});closePermissions(true);}
    catch(error){permissionFeedback(error.message);render();}
  }));
  $('chat-auto-approve-cancel').addEventListener('click',()=>{$('chat-auto-approve-dialog').close();$('chat-approval-mode').focus();});
  $('chat-auto-approve-dialog').addEventListener('cancel',event=>{if(saving){event.preventDefault();return;}$('chat-approval-mode').focus();});
  $('chat-auto-approve-enable').addEventListener('click',async()=>{
    const button=$('chat-auto-approve-enable');button.disabled=true;$('chat-auto-approve-cancel').disabled=true;
    try{
      if(activationChat!==sid() || activationNavigation!==navigationGeneration)throw new Error('The chat changed. Choose Auto-Approve again.');
      await changeChat({approval_mode:'auto_approve'});
      $('chat-auto-approve-dialog').close();$('chat-approval-mode').focus();
    }catch(error){$('chat-auto-approve-feedback').textContent=error.message;render();}
    finally{button.disabled=false;$('chat-auto-approve-cancel').disabled=false;}
  });
  $('chat-auto-models-settings').addEventListener('click',event=>{
    event.stopPropagation();
    $('chat-automation-dialog').close();document.getElementById('model-picker-btn').click();window.dispatchEvent(new Event('odysseus:edit-auto-models'));
  });
  $('chat-automation-settings').addEventListener('click',openSettings);
  $('chat-resume-input').addEventListener('click',async()=>{
    await refresh();const pending=policy?.task?.pending_question,id=sid(),ticket=sequence;
    if(pending?.kind!=='clarification' || !pending.input_card_id)return;
    const renderer=await import('./chatRenderer.js?v=20260914metricssummary1');
    if(id===sid() && ticket===sequence)renderer.renderAskUserCard(pending,{focus:true,scroll:true});
  });
  $('chat-automation-default').addEventListener('click',async()=>{try{await changeChat({approval_mode:$('chat-approval-mode').value});await refresh();message('New chats will use this approval mode.');}catch(e){$('chat-automation-state').textContent=e.message;await refresh();}});
  $('chat-automation-close').addEventListener('click',()=>$('chat-automation-dialog').close());
  const browserPath=async()=>{if(settingsChatId!==sid() || settingsNavigation!==navigationGeneration)throw new Error('The chat changed. Open settings again.');return `/api/session/${encodeURIComponent(await currentChat())}/browser`;};
  function browserScope(){const effects=[];if($('chat-browser-clicks').checked)effects.push('click');if($('chat-browser-fill').checked)effects.push('fill');if($('chat-browser-submit').checked)effects.push('submit');const label=$('chat-browser-field-label').value;return {url:$('chat-browser-url').value,effects,fields:label?[{label,value:$('chat-browser-field-value').value}]:[],checks:$('chat-browser-check').value?[{kind:'text_contains',value:$('chat-browser-check').value}]:[]};}
  async function viewBrowser(){const result=await api((await browserPath())+'/view');$('chat-browser-screenshot').src=result.screenshot.url;$('chat-browser-screenshot').hidden=false;$('chat-browser-status').textContent='You have control. This is a current browser image; automation’s previous targets have been invalidated.';const obs=result.observation;$('chat-browser-targets').replaceChildren();(obs.elements||[]).filter(n=>!n.disabled).forEach(node=>{const button=document.createElement('button');button.type='button';button.textContent=(node.editable?'Fill: ':'Click: ')+(node.label||node.role||'Unnamed target');button.addEventListener('click',async()=>{try{await api((await browserPath())+'/action','POST',{action:node.editable?'fill':'click',page:'t1',observation:obs.observation,ref:node.ref,...(node.editable?{value_id:node.label}:{})});await viewBrowser();}catch(e){$('chat-browser-status').textContent=e.message;}});$('chat-browser-targets').append(button);});}
  for(const [id,handler] of [['chat-browser-open',async()=>{await api(await browserPath(),'POST');$('chat-browser-status').textContent='Browser opened. Choose a page and allowed actions before using it.';}],['chat-browser-close',async()=>{const result=await api(await browserPath(),'DELETE');$('chat-browser-status').textContent=result.status==='closed'?'Browser closed.':'Browser cleanup is incomplete.';$('chat-browser-screenshot').hidden=true;$('chat-browser-targets').replaceChildren();}],['chat-browser-scope',async()=>{await api((await browserPath())+'/scope','POST',browserScope());$('chat-browser-status').textContent='Allowed for your next message for two minutes. Only the selected site and actions are permitted.';}],['chat-browser-navigate',async()=>{await api((await browserPath())+'/scope','POST',browserScope());await api((await browserPath())+'/action','POST',{action:'navigate',page:'t1',url:$('chat-browser-url').value});await viewBrowser();}],['chat-browser-view',viewBrowser]]) $(id).addEventListener('click',async()=>{try{await handler();}catch(e){$('chat-browser-status').textContent=e.message;}});
  $('chat-elevation-save').addEventListener('click',async()=>{try{if(settingsChatId!==sid() || settingsNavigation!==navigationGeneration)throw new Error('The chat changed. Open settings again.');const roots=[...$('chat-elevation-folders').querySelectorAll('label')].filter(l=>l.querySelector('input').checked).map(l=>({path:l.querySelector('input').value,access:l.querySelector('select').value}));await changeChat({elevation_roots:roots});$('chat-elevation-status').textContent='Folder grant saved for this chat for one hour.';}catch(e){$('chat-elevation-status').textContent=e.message;}});
  $('chat-automation-form').addEventListener('submit',async e=>{
    e.preventDefault(); const button=e.submitter; if(button)button.disabled=true;
    try {
      if(settingsChatId!==sid() || settingsNavigation!==navigationGeneration) throw new Error('The chat changed. Close settings and open them again.');
      const chatChanges={};
      for(const name of ['followup','preparation','completion-advice']) chatChanges[name.replaceAll('-','_')+'_enabled']=$(`chat-${name}-enabled`).checked;
      const changes={elevation_ceiling:$('chat-elevation-ceiling').checked};
      const decision=readRoute('decision');
      if(decision.endpoint_id||decision.model) changes.decision_provider={...decision,protocol:$('chat-decision-protocol').value,timeout_seconds:30,max_calls:32};
      const revision=configuration.revision;
      await changeChat(chatChanges);
      configuration=await writeSettings(()=>api('/api/chat-settings','PUT',{revision,changes}));
      await refresh(); message('Settings saved. New messages use the committed choices.');
    } catch(error) { message(error.message); } finally {if(button)button.disabled=false;}
  });
  window.addEventListener('odysseus:session-changed',()=>{++navigationGeneration;policy=null;closePermissions();notice();refresh();});
  window.addEventListener('odysseus:automation-changed',refresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  await refresh();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initialize);else initialize();
