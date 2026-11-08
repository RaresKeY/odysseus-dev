import {getWorkspace} from './workspace.js';
import {workspaceFilePath, fileLanguage} from './workspaceFileLinks.js';
import markdown from './markdown.js';

const FILE_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h5"/></svg>';
const FOLDER_ICON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 7h6l2-3h8a2 2 0 0 1 2 2v13H3z"/></svg>';
const ICONS = {
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 4M18 18a8 8 0 0 1-13-4"/>',
  copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M15 4H4v11"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4"/>',
  expand:'<path d="M9 3H3v6M15 3h6v6M21 15v6h-6M9 21H3v-6"/>',
};
const node = (tag, cls, text) => {const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;};
function button(label, action, icon) {
  const el=node('button','files-button',icon ? undefined : label);
  el.type='button';el.title=label;el.setAttribute('aria-label',label);
  if(icon)el.innerHTML='<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">'+ICONS[icon]+'</svg>';
  el.addEventListener('click',action);return el;
}
let panel, trigger, closeButton, status, tree, filter, breadcrumb, fileTitle, textarea, preview, editorArea;
let editButton, saveButton, cancelButton, copyButton, downloadButton, docButton, viewButton, wrapButton;
let workspaceTab, documentsTab, browserArea, treeStatus, returnFocus, separator;
let selection=null, directory='', entries=[], tab='workspace', loadSequence=0, treeSequence=0, docSequence=0;
let documentImporting=false;
let readController, treeController, sessionId='', width=560, full=false, previewing=false, imageUrl=null;
const drafts=new Map();
const narrow=window.matchMedia('(max-width: 900px)');
try {const saved=Number(localStorage.getItem('odysseus.files.width'));if(saved>=360&&saved<=1400)width=saved;} catch (_) {}
function root(){return getWorkspace();}
function key(workspace,path){return JSON.stringify([workspace,path]);}
function message(text,error=false){status.textContent=text;status.classList.toggle('files-error',error);}
async function request(url,options={}) {
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options});
  if(!response.ok){const data=await response.json().catch(()=>({}));const error=new Error(typeof data.detail==='string'?data.detail:'Could not load file ('+response.status+')');error.status=response.status;throw error;}
  return response;
}
function api(path='',extra={}) {return '/api/workspace/'+path+'?'+new URLSearchParams({workspace:root(),...extra});}
function releaseImage(){if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl=null;}}
function resetView() {
  ++loadSequence;readController?.abort();selection=null;previewing=false;releaseImage();
  textarea.value='';textarea.hidden=true;preview.hidden=true;preview.replaceChildren();
  fileTitle.textContent='Choose a file';message('Open a file from chat or browse your workspace.');
  syncActions();
}
function syncActions() {
  const draft=selection?.draft, available=!!draft, editing=available&&draft.editing;
  const busy=!!draft?.saving;
  editButton.hidden=!available||editing;editButton.disabled=!draft?.editable;
  saveButton.hidden=!editing;saveButton.disabled=!draft?.dirty||busy;
  saveButton.textContent=busy?'Saving…':'Save';
  cancelButton.hidden=!editing;cancelButton.disabled=busy;
  copyButton.disabled=!available;downloadButton.disabled=!selection;
  const originalDocument=!!selection&&/\.(pdf|docx)$/i.test(selection.path);
  docButton.disabled=(!available&&!originalDocument)||busy||documentImporting;
  docButton.textContent=originalDocument&&!available?'Open in Documents':'Edit a copy in Documents';
  docButton.setAttribute('aria-label',docButton.textContent);
  docButton.title='Create an editable document copy; workspace file stays separate';
  viewButton.hidden=!available||fileLanguage(selection?.path)!=='markdown';
  viewButton.setAttribute('aria-pressed',String(previewing));viewButton.textContent=previewing?'Source':'Preview';
  wrapButton.disabled=!available;wrapButton.setAttribute('aria-pressed',String(!textarea.classList.contains('files-nowrap')));
  textarea.readOnly=!editing;textarea.setAttribute('aria-label',editing?'Edit '+selection.path:'Contents of '+(selection?.path||'file'));
  fileTitle.textContent=selection ? selection.path+(draft?.dirty?' •':'') : 'Choose a file';
  if(available){textarea.hidden=previewing;preview.hidden=!previewing;}
}
function showDraft(draft,path,workspace) {
  selection={path,workspace,draft};previewing=false;releaseImage();
  preview.replaceChildren();textarea.value=draft.content;textarea.hidden=false;preview.hidden=true;
  message(draft.dirty?'Unsaved changes':draft.size+' bytes · '+(draft.content.split('\n').length)+' lines');
  syncActions();
}
function cleanDrafts() {
  if(drafts.size<24)return true;
  for(const [id,draft] of drafts)if(!draft.dirty&&!draft.saving)drafts.delete(id);
  if(drafts.size>=24){message('Save or discard an open file before opening more files.',true);return false;}
  return true;
}
async function openFile(raw,source) {
  const path=workspaceFilePath(raw,{inline:true})||workspaceFilePath(raw);
  if(!path)return;
  ensurePanel();returnFocus=source||document.activeElement;showPanel();
  setTab('workspace',false);
  const workspace=root();
  if(!workspace){message('Select a workspace in the composer to browse files.',true);return;}
  const ticket=++loadSequence;readController?.abort();readController=new AbortController();
  selection={path,workspace};textarea.hidden=true;preview.hidden=true;releaseImage();syncActions();
  message('Opening '+path+'…');
  try {
    const response=await request(api('file',{path}),{signal:readController.signal});
    const file=await response.json();
    if(ticket!==loadSequence||workspace!==root()||!panel.open)return;
    const id=key(workspace,file.path);let draft=drafts.get(id);
    if(!draft||(!draft.dirty&&!draft.saving)){if(!cleanDrafts())return;const newline=file.content.includes('\r\n')&&!file.content.replaceAll('\r\n','').includes('\n')?'crlf':'lf';const content=file.content.replaceAll('\r\n','\n');draft={...file,content,base:content,newline,dirty:false,editing:false,saving:false};drafts.set(id,draft);}
    showDraft(draft,file.path,workspace);
    directory=file.path.includes('/')?file.path.slice(0,file.path.lastIndexOf('/')):'';
    await loadDirectory(directory);
  } catch(error) {
    if(ticket!==loadSequence||error.name==='AbortError')return;
    message(error.message,true);syncActions();
    // Binary previews never interpret HTML/SVG or execute generated code.
    if([415,413].includes(error.status)&&/\.(png|jpe?g|gif|webp)$/i.test(path))loadImage(path,ticket);
  }
}
async function loadImage(path,ticket) {
  try {
    const response=await request(api('file/download',{path}));
    const bytes=new Uint8Array(await response.arrayBuffer());
    let type='';
    if(bytes[0]===137&&bytes[1]===80&&bytes[2]===78&&bytes[3]===71)type='image/png';
    else if(bytes[0]===255&&bytes[1]===216&&bytes[2]===255)type='image/jpeg';
    else if(String.fromCharCode(...bytes.slice(0,6)).match(/^GIF8[79]a$/))type='image/gif';
    else if(String.fromCharCode(...bytes.slice(0,4))==='RIFF'&&String.fromCharCode(...bytes.slice(8,12))==='WEBP')type='image/webp';
    if(!type||ticket!==loadSequence||!panel.open)return;
    imageUrl=URL.createObjectURL(new Blob([bytes],{type}));
    const image=node('img','files-image');image.src=imageUrl;image.alt=path;
    preview.replaceChildren(image);preview.hidden=false;message('Image preview');
  } catch(error){if(ticket===loadSequence)message(error.message,true);}
}
function drawBreadcrumb() {
  breadcrumb.replaceChildren();
  const workspace=root();
  const home=button(workspace.split(/[\\/]/).filter(Boolean).pop()||'Workspace',()=>loadDirectory(''));
  home.title=workspace;breadcrumb.append(home);
  let path='';
  for(const part of directory.split('/').filter(Boolean)){
    path+=(path?'/':'')+part;const location=path;
    breadcrumb.append(node('span','','/'),button(part,()=>loadDirectory(location)));
  }
}
function renderEntries() {
  tree.replaceChildren();
  const query=filter.value.trim().toLocaleLowerCase();
  const visible=entries.filter(entry=>entry.name.toLocaleLowerCase().includes(query));
  if(!visible.length){tree.append(node('p','files-empty',query?'No matching files.':'This folder is empty.'));return;}
  for(const entry of visible){
    const row=button(entry.name,()=>entry.directory?loadDirectory(entry.path):openFile(entry.path,row));
    row.className='files-entry';row.innerHTML=entry.directory?FOLDER_ICON:FILE_ICON;
    row.append(node('span','files-entry-name',entry.name));
    row.title=entry.path;row.dataset.path=entry.path;
    if(selection?.path===entry.path){row.classList.add('selected');row.setAttribute('aria-current','true');}
    if(entry.directory)row.append(node('span','files-entry-meta','›'));
    else row.append(node('small','files-entry-meta',entry.size<1024?entry.size+' B':Math.ceil(entry.size/1024)+' KB'));
    tree.append(row);
  }
}
async function loadDirectory(path='') {
  const ticket=++treeSequence, workspace=root();
  treeController?.abort();treeController=new AbortController();
  if(!workspace){tree.replaceChildren(node('p','files-empty','Select a workspace to browse files.'));return;}
  treeStatus.textContent='Loading files…';
  try{
    const data=await (await request(api('files',{path}),{signal:treeController.signal})).json();
    if(ticket!==treeSequence||workspace!==root()||!panel.open||tab!=='workspace')return;
    directory=data.path;entries=data.entries;drawBreadcrumb();renderEntries();
    treeStatus.textContent=data.truncated?'Showing a limited folder listing. Open a subfolder to narrow it.':'';
  }catch(error){if(ticket!==treeSequence||error.name==='AbortError')return;treeStatus.textContent=error.message;tree.replaceChildren();}
}
async function loadDocuments() {
  const ticket=++docSequence, ownedSession=sessionId;
  tree.replaceChildren();treeStatus.textContent='Loading documents…';breadcrumb.replaceChildren();
  breadcrumb.append(node('span','','Documents in this chat'));
  try {
    if(!ownedSession){treeStatus.textContent='This chat has no saved documents yet.';return;}
    const documents=await (await request('/api/documents/'+encodeURIComponent(ownedSession))).json();
    if(ticket!==docSequence||ownedSession!==sessionId||!panel.open||tab!=='documents')return;
    treeStatus.textContent=documents.length?'':'No documents in this chat. Files can be copied into Documents.';
    for(const doc of documents){
      const row=button(doc.title||'Untitled',()=>{closePanel(false);window.documentModule?.loadDocument(doc.id);});
      row.className='files-entry';row.innerHTML=FILE_ICON;row.append(node('span','files-entry-name',doc.title||'Untitled'),node('small','files-entry-meta',doc.language||'text'));tree.append(row);
    }
  }catch(error){if(ticket===docSequence)treeStatus.textContent=error.message;}
}
function setTab(value,load=true) {
  tab=value;browserArea.setAttribute('aria-labelledby','files-tab-'+value);++treeSequence;++docSequence;treeController?.abort();filter.hidden=value!=='workspace';
  workspaceTab.setAttribute('aria-selected',String(value==='workspace'));
  documentsTab.setAttribute('aria-selected',String(value==='documents'));
  workspaceTab.tabIndex=value==='workspace'?0:-1;documentsTab.tabIndex=value==='documents'?0:-1;
  browserArea.classList.toggle('files-documents',value==='documents');
  if(load){if(value==='workspace')loadDirectory(directory);else loadDocuments();}
}
function edited() {
  const draft=selection?.draft;if(!draft||!draft.editing)return;
  draft.content=textarea.value;draft.dirty=draft.content!==draft.base;
  message(draft.dirty?'Unsaved changes':'No changes');syncActions();
}
async function save() {
  const selected=selection,draft=selected?.draft;
  if(!draft?.dirty||draft.saving)return;
  const content=draft.content,revision=draft.revision;
  // HTML textareas normalize CRLF; preserve the file's original newline style.
  const crlf=draft.newline==='crlf';
  const encoded=crlf?content.replace(/\r?\n/g,'\r\n'):content;
  draft.saving=true;syncActions();
  try {
    const response=await request('/api/workspace/file',{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({workspace:selected.workspace,path:selected.path,content:encoded,revision})});
    const data=await response.json();
    draft.revision=data.revision;draft.base=content;draft.size=data.size;draft.dirty=draft.content!==content;
    if(selection?.draft===draft)message(draft.dirty?'Saved. Newer edits are still unsaved.':'Saved');
  }catch(error){if(selection?.draft===draft)message(error.message,true);}
  finally {draft.saving=false;if(selection?.draft===draft)syncActions();}
}
async function copyText() {
  if(!selection?.draft)return;
  try{await navigator.clipboard.writeText(selection.draft.content);message('Copied file contents');}
  catch(_){message('Clipboard unavailable. Select and copy the text instead.',true);}
}
async function download() {
  if(!selection)return;
  const selected=selection;
  try{
    const response=await request('/api/workspace/file/download?'+new URLSearchParams({workspace:selected.workspace,path:selected.path}));
    const url=URL.createObjectURL(await response.blob()),link=node('a');
    link.href=url;link.download=selected.path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){message(error.message,true);}
}
async function openDocumentCopy() {
  const selected=selection, ownedSession=sessionId;
  if(!selected||documentImporting)return;
  documentImporting=true;docButton.disabled=true;
  try {
    let response;
    if(!selected.draft&&/\.(pdf|docx)$/i.test(selected.path)){
      const extension=selected.path.split('.').pop().toLowerCase();
      const source=await request('/api/workspace/file/download?'+new URLSearchParams({workspace:selected.workspace,path:selected.path}));
      const body=new FormData();body.append('file',await source.blob(),selected.path.split('/').pop());
      if(ownedSession)body.append('session_id',ownedSession);
      response=await request('/api/documents/import-'+extension,{method:'POST',body});
    }else if(selected.draft){
      response=await request('/api/document',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({title:selected.path.split('/').pop(),content:selected.draft.content,
          language:fileLanguage(selected.path),session_id:ownedSession||null})});
    }else return;
    const doc=await response.json();
    if(selection!==selected||sessionId!==ownedSession||!panel.open){return;}
    closePanel(false);
    await window.documentModule?.loadDocument(doc.id||doc.doc_id);
  }catch(error){if(selection===selected)message(error.message,true);}
  finally {documentImporting=false;syncActions();}
}
function togglePreview() {
  if(!selection?.draft)return;
  previewing=!previewing;
  if(previewing){preview.innerHTML=markdown.mdToHtml(selection.draft.content);markdown.renderMath?.(preview);}
  syncActions();
}
function applyWidth() {
  if(!panel)return;
  const left=document.getElementById('chat-container')?.getBoundingClientRect().left||0;
  const maximum=Math.max(360,innerWidth-left-360);
  panel.style.setProperty('--files-width',(full?maximum:Math.max(360,Math.min(width,maximum)))+'px');
  separator?.setAttribute('aria-valuenow',String(Math.round(Math.max(360,Math.min(width,maximum)))));
  separator?.setAttribute('aria-valuemax',String(maximum));
}
function persistWidth(){try{localStorage.setItem('odysseus.files.width',String(Math.round(width)));}catch(_){}}
let panelRoot='';
function showPanel() {
  if(panel.open)return;
  const decision=document.getElementById('chat-decision-pane');if(decision?.open)document.getElementById('chat-decision-close')?.click();
  window.documentModule?.isPanelOpen?.()&&window.documentModule.closePanel();
  panelRoot=root();applyWidth();if(narrow.matches)panel.showModal();else panel.show();
  trigger.setAttribute('aria-expanded','true');closeButton.focus({preventScroll:true});
}
function closePanel(restore=true) {
  if(!panel?.open)return;
  ++loadSequence;++treeSequence;++docSequence;readController?.abort();treeController?.abort();
  panel.close();trigger.setAttribute('aria-expanded','false');releaseImage();
  if(restore)(returnFocus?.isConnected?returnFocus:trigger).focus({preventScroll:true});
}
function ensurePanel() {
  if(panel)return;
  panel=node('dialog','workspace-files-pane');panel.id='workspace-files-pane';panel.setAttribute('aria-labelledby','workspace-files-heading');
  separator=node('div','files-resizer');separator.tabIndex=0;separator.setAttribute('role','separator');
  separator.setAttribute('aria-label','Resize files panel');separator.setAttribute('aria-orientation','vertical');separator.setAttribute('aria-valuemin','360');
  const header=node('header','files-header');
  const heading=node('h2','','Files');heading.id='workspace-files-heading';heading.prepend(node('span','files-heading-icon'));heading.firstChild.innerHTML=FILE_ICON;
  const actions=node('div','files-header-actions');
  actions.append(button('Refresh files',()=>tab==='workspace'?loadDirectory(directory):loadDocuments(),'refresh'),
    button('Expand files panel',()=>{full=!full;applyWidth();},'expand'));
  closeButton=button('Close files',()=>closePanel(),'close');actions.append(closeButton);header.append(heading,actions);
  const tabs=node('div','files-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','File sources');
  workspaceTab=button('Workspace',()=>setTab('workspace'));documentsTab=button('Documents',()=>setTab('documents'));
  for(const [item,id] of [[workspaceTab,'workspace'],[documentsTab,'documents']]){
    item.setAttribute('role','tab');item.id='files-tab-'+id;item.setAttribute('aria-controls','files-browser');tabs.append(item);
    item.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setTab(item===workspaceTab?'documents':'workspace');(tab==='workspace'?workspaceTab:documentsTab).focus();}});
  }
  browserArea=node('section','files-browser');browserArea.id='files-browser';browserArea.setAttribute('role','tabpanel');
  breadcrumb=node('nav','files-breadcrumb');breadcrumb.setAttribute('aria-label','File path');
  filter=node('input','files-filter');filter.type='search';filter.placeholder='Filter this folder';filter.setAttribute('aria-label','Filter files in this folder');filter.addEventListener('input',renderEntries);
  tree=node('div','files-tree quiet-scroll');tree.setAttribute('aria-label','Files');
  treeStatus=node('p','files-tree-status');treeStatus.setAttribute('role','status');
  browserArea.append(breadcrumb,filter,tree,treeStatus);
  editorArea=node('section','files-editor');
  const titlebar=node('div','files-titlebar');fileTitle=node('h3','','Choose a file');titlebar.append(fileTitle);
  const toolbar=node('div','files-toolbar');toolbar.setAttribute('aria-label','File actions');
  editButton=button('Edit',()=>{selection.draft.editing=true;previewing=false;syncActions();textarea.focus();});
  saveButton=button('Save',save);saveButton.classList.add('files-save');
  cancelButton=button('Discard edits',()=>{const draft=selection.draft;if(draft.dirty&&!window.confirm('Discard unsaved changes to this file?'))return;draft.content=draft.base;draft.dirty=false;draft.editing=false;textarea.value=draft.content;message('Changes discarded');syncActions();});
  viewButton=button('Preview',togglePreview);
  wrapButton=button('Wrap',()=>{textarea.classList.toggle('files-nowrap');syncActions();});
  copyButton=button('Copy contents',copyText,'copy');downloadButton=button('Download file',download,'download');
  docButton=button('Edit a copy in Documents',openDocumentCopy);
  toolbar.append(editButton,saveButton,cancelButton,viewButton,wrapButton,copyButton,downloadButton);
  const more=node('details','files-more'),summary=node('summary','','More');more.append(summary,docButton);
  document.addEventListener('pointerdown',event=>{if(!more.contains(event.target))more.open=false;});
  more.addEventListener('keydown',event=>{if(event.key==='Escape'&&more.open){event.preventDefault();event.stopPropagation();more.open=false;summary.focus();}});toolbar.append(more);
  textarea=node('textarea','files-text quiet-scroll');textarea.spellcheck=false;textarea.readOnly=true;textarea.hidden=true;
  textarea.addEventListener('input',edited);
  textarea.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}});
  preview=node('div','files-preview quiet-scroll');preview.hidden=true;
  status=node('p','files-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  editorArea.append(titlebar,toolbar,textarea,preview,status);
  const body=node('div','files-body');body.append(editorArea,browserArea);
  panel.append(separator,header,tabs,body);
  const before=document.getElementById('chat-decision-pane');
  if(before)before.before(panel);else document.getElementById('chat-container')?.after(panel);
  panel.addEventListener('cancel',event=>{event.preventDefault();closePanel();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&panel.open&&!event.defaultPrevented&&![...document.querySelectorAll('dialog:modal')].some(item=>item!==panel)){event.preventDefault();closePanel();}});
  separator.addEventListener('pointerdown',event=>{
    if(event.button!==0||narrow.matches)return;
    event.preventDefault();separator.setPointerCapture(event.pointerId);full=false;
    const start=event.clientX,initial=panel.getBoundingClientRect().width;
    let pending=null,frame=0;
    const update=()=>{frame=0;if(pending!==null){width=Math.max(360,Math.min(innerWidth-360,initial+start-pending));applyWidth();}};
    const move=next=>{pending=next.clientX;if(!frame)frame=requestAnimationFrame(update);};
    const end=()=>{cancelAnimationFrame(frame);update();persistWidth();separator.removeEventListener('pointermove',move);separator.removeEventListener('pointerup',end);separator.removeEventListener('lostpointercapture',end);};
    separator.addEventListener('pointermove',move);separator.addEventListener('pointerup',end,{once:true});separator.addEventListener('lostpointercapture',end,{once:true});
  });
  separator.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();full=false;
    width=event.key==='Home'?360:event.key==='End'?Math.max(360,innerWidth-360):width+(event.key==='ArrowLeft'?1:-1)*(event.shiftKey?64:16);applyWidth();persistWidth();
  });
  separator.addEventListener('dblclick',()=>{width=560;full=false;persistWidth();applyWidth();});
  narrow.addEventListener('change',()=>{if(panel.open){const focus=document.activeElement;panel.close();if(narrow.matches)panel.showModal();else panel.show();if(panel.contains(focus))focus.focus();}applyWidth();});
  window.addEventListener('resize',applyWidth);
  window.addEventListener('beforeunload',event=>{if([...drafts.values()].some(draft=>draft.dirty)){event.preventDefault();event.returnValue='';}});
  setTab('workspace',false);resetView();
}
export function openWorkspaceFile(path,source){return openFile(path,source);}
export function initWorkspaceFiles() {
  const meta=document.querySelector('.chat-meta-overlay');if(!meta||document.getElementById('chat-files-toggle'))return;
  trigger=button('Files',()=>{ensurePanel();returnFocus=trigger;if(panel.open)closePanel();else {showPanel();setTab(tab);}});
  trigger.id='chat-files-toggle';trigger.className='decision-activity-trigger files-trigger';trigger.innerHTML=FILE_ICON+'<span>Files</span>';
  trigger.setAttribute('aria-controls','workspace-files-pane');trigger.setAttribute('aria-expanded','false');meta.append(trigger);
  const getSession=()=>window.sessionModule?.getCurrentSessionId?.()||'';
  sessionId=getSession();
  window.addEventListener('odysseus:session-changed',event=>{
    sessionId=event.detail?.sessionId||getSession();if(panel){resetView();directory='';if(panel.open)setTab(tab);}
  });
  document.addEventListener('overflow-state-change',()=>{
    if(panel&&panelRoot!==root()){panelRoot=root();resetView();directory='';if(panel.open)setTab(tab);}
  });
  window.addEventListener('odysseus:document-open',()=>closePanel(false));
  document.getElementById('chat-decision-toggle')?.addEventListener('click',()=>{if(panel?.open)closePanel(false);});
  document.addEventListener('click',event=>{
    if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const target=event.target.closest?.('[data-workspace-file]');
    if(target&&(document.getElementById('chat-history')?.contains(target)||preview?.contains(target))){
      let path=target.dataset.workspaceFile;
      if(preview?.contains(target)&&selection&&!path.startsWith('/')){const parent=selection.path.includes('/')?selection.path.slice(0,selection.path.lastIndexOf('/')):'';path=(parent?parent+'/':'')+path;}
      event.preventDefault();event.stopImmediatePropagation();openFile(path,target);
    }
  },true);
  // Only inspect newly changed message bodies; never rescan the full transcript
  // on each stream delta, and never touch executable fenced code or user text.
  const history=document.getElementById('chat-history');if(!history)return;
  const queued=new Set();let timer=0;
  function enhance(body) {
    if(!body.isConnected||body.closest('.msg-user'))return;
    for(const code of body.querySelectorAll('code:not(pre code)')){
      if(code.closest('a,button')||code.childElementCount)continue;
      const path=workspaceFilePath(code.textContent,{inline:true});if(!path)continue;
      const link=node('button','workspace-file-link');link.type='button';link.dataset.workspaceFile=path;
      link.title='Open '+path+' in Files';link.setAttribute('aria-label',link.title);code.replaceWith(link);link.append(code);
    }
    for(const link of body.querySelectorAll('a[href]:not([data-workspace-file])')){
      const path=workspaceFilePath(link.getAttribute('href'));if(!path)continue;
      link.dataset.workspaceFile=path;link.title='Open '+path+' in Files';
    }
  }
  function flush(){timer=0;for(const body of queued)enhance(body);queued.clear();}
  function queue(element){
    if(element.nodeType!==1)element=element.parentElement;if(!element)return;
    const body=element.closest('.msg-ai > .body,.agent-thread .body');
    if(body)queued.add(body);
    else element.querySelectorAll?.('.msg-ai > .body,.agent-thread .body').forEach(item=>queued.add(item));
    if(queued.size&&!timer)timer=setTimeout(flush,120);
  }
  history.querySelectorAll('.msg-ai > .body,.agent-thread .body').forEach(enhance);
  new MutationObserver(records=>{for(const record of records){queue(record.target);for(const child of record.addedNodes)queue(child);}}).observe(history,{childList:true,subtree:true,characterData:true});
}
