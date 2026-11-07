'use strict';
let current=null,selected=null,busy=false,requesting=false,initialized=false,disconnected=false;
const $=id=>document.getElementById(id);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const names={pending:'Awaiting review',approved:'Approved for preview',completed:'Preview completed',revoked:'Revoked',rejected:'Dismissed',failed:'Failed',interrupted:'Interrupted',expired:'Expired',running:'Preview in progress'};
async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',headers:{'X-PCO-Token':window.PCO_TOKEN,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'Local request failed');return value;}
function error(message){$('error').textContent=message;$('error').hidden=false;}
let noticeAcknowledged=false,notificationRouteHandled=false,nativeFeedback='';
function openReview(){ $('content').hidden=false;$('expand').textContent='Collapse';$('expand').setAttribute('aria-expanded','true');setTab('review'); }
async function acknowledgeNotice(key){await api('/api/notification-open',{notification_id:key});$('notification-result').textContent='Notification review opened. Nothing has been approved or run.';}
async function openNotificationRoute(){
 if(window.location?.pathname!=='/review'||notificationRouteHandled)return;
 notificationRouteHandled=true;openReview();
 const key=/^#notice=([A-Za-z0-9_-]{32})$/.exec(window.location.hash)?.[1];
 if(key&&!noticeAcknowledged){noticeAcknowledged=true;try{await acknowledgeNotice(key);}catch(e){error(e.message);}}
}
function setTab(tab){for(const id of ['review','activity','connections','agent','memory'])$(id).hidden=id!==tab;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
$('expand').onclick=()=>{const hidden=!$('content').hidden;$('content').hidden=hidden;$('expand').textContent=hidden?'Review':'Collapse';$('expand').setAttribute('aria-expanded',String(!hidden));};
function review(p){selected=p.id;$('detail').hidden=false;const box=$('detail');box.replaceChildren(el('span',p.project+' · '+p.session,'badge'),el('h2',p.title),el('p',p.explanation));
box.append(el('p',p.expired?'Expired · a new proposal is required':names[p.status]||p.status,'notice'));
const steps=el('ol');for(const step of (p.scope?.steps||[]))steps.append(el('li',step.action.replaceAll('_',' ')+' · '+step.scope));box.append(steps);
box.append(el('p',`Project: ${p.project} · Session: ${p.session}`));
box.append(el('p',`Effect: simulation only · No tool access · Expires: ${p.scope?.expires_at?new Date(p.scope.expires_at).toLocaleString():'Unavailable'}`));
if(p.memory_context){box.append(el('h2','Project experience'),el('pre',p.memory_context.text));}
const advanced=el('details');advanced.append(el('summary','Approval integrity details'),el('pre',JSON.stringify({scope_hash:p.scope_hash,integrity_valid:p.integrity_valid},null,2)));box.append(advanced);
if(p.output)box.append(el('pre',p.output));
box.append(el('p','Approval grants this exact preview only. Run preview is a separate action.','notice'));
const actions=el('div',undefined,'actions');
for(const [action,label,enabled] of [['approve','Approve preview',p.status==='pending'&&!p.expired&&p.integrity_valid],['preview','Run preview',p.can_execute],['reject','Dismiss',p.status==='pending'],['revoke','Revoke',['pending','approved'].includes(p.status)]]){const b=el('button',label,action==='approve'?'primary':'');b.disabled=!enabled||busy;b.onclick=async()=>{busy=true;review(p);try{const result=await api('/api/action',{id:p.id,action,scope_hash:p.scope_hash});$('error').hidden=true;if(result.output)box.append(el('pre',result.output));await refresh();}catch(e){error(e.message);}finally{busy=false;const latest=current?.proposals.find(q=>q.id===p.id);if(latest)review(latest);}};actions.append(b);}box.append(actions);
box.append(el('h2','Audit trail'));box.append(el('pre',p.audit.map(a=>a.transition+' · '+a.at).join('\n')||'No recorded transitions'));
}
function card(p){const c=el('article',undefined,'card'),copy=el('div');copy.append(el('span',p.project+' · '+(names[p.status]||p.status),'badge'),el('h3',p.title),el('p',p.explanation));const b=el('button','Review');b.onclick=()=>{setTab('review');review(p);$('detail').scrollIntoView({behavior:'smooth',block:'nearest'});};c.append(copy,b);return c;}
function render(s){current=s;if(!initialized){initialized=true;$('content').hidden=s.appearance.compact;$('expand').textContent=s.appearance.compact?'Review':'Collapse';$('expand').setAttribute('aria-expanded',String(!s.appearance.compact));}$('strip').dataset.state=s.state;document.documentElement.style.setProperty('--accent',s.appearance.accent);$('count').textContent=s.pending;$('summary').textContent=s.failures?`${s.failures} workflow(s) need attention`:(s.pending?`${s.pending} workflow(s) await your decision`:(s.approved?`${s.approved} approved preview(s) ready`:'Nothing needs your attention'));
if(!s.failures&&(s.memory_pending||s.agent_pending)){$('summary').textContent=`Review needed · ${s.pending} workflow(s) · ${s.agent_pending||0} text task(s) · ${s.memory_pending||0} experience update(s)`;}
$('health').textContent=s.worker_error?'Worker needs attention · check local settings':(s.agent?.enabled?'Local worker active · live text requires separate approval':'Local worker active · preview only');
$('queue').replaceChildren();const pending=s.proposals.filter(p=>['pending','approved'].includes(p.status)&&!p.expired);pending.forEach(p=>$('queue').append(card(p)));if(!pending.length)$('queue').append(el('div','All clear. Suggestions from opted-in projects will appear here.','empty'));
$('history').replaceChildren();s.proposals.filter(p=>!pending.includes(p)).forEach(p=>$('history').append(card(p)));if(!$('history').children.length)$('history').append(el('div','No past workflows yet.','empty'));
$('adapters').replaceChildren();for(const a of s.adapters){const n=el('div',undefined,'card');n.append(el('strong',a.tool==='codex'?'Codex':'Claude Code'),el('p',a.project+' · '+(a.observation==='recent_event'?'Event recently received':a.observation==='stale_event'?'Last event is stale':'No events received')+(a.last_event?' · '+new Date(a.last_event).toLocaleString():'')));$('adapters').append(n);}if(!s.adapters.length)$('adapters').append(el('p','No projects opted in. Add a personal workspace to your configuration.'));
$('native-result').textContent=s.notification_link?.opened_at?'Review opened · '+new Date(s.notification_link.opened_at).toLocaleString():(nativeFeedback||(s.notification_link?'No review open recorded for the latest notification link.':''));if(typeof renderMemory==='function')renderMemory(s);renderAgent(s);$('test-native').disabled=!s.notifications.enabled||s.notifications.backend!=='native';$('settings').textContent=JSON.stringify({notifications:s.notifications,appearance:s.appearance,execution:s.execution},null,2);$('permission').disabled=!s.notifications.enabled||s.notifications.backend!=='browser';
if(selected){const p=s.proposals.find(p=>p.id===selected);if(p&&!busy)review(p);else if(!p){selected=null;$('detail').hidden=true;$('detail').replaceChildren();}}if(s.worker_error)error('The local worker encountered an error. Check project settings and model availability.');}
async function refresh(){if(requesting)return;requesting=true;try{const s=await api('/api/state');if(disconnected||JSON.stringify(current)!==JSON.stringify(s)){if(disconnected)$('error').hidden=true;disconnected=false;render(s);}await openNotificationRoute();if(s.notifications.enabled&&s.notifications.backend==='browser'&&'Notification'in window&&Notification.permission==='granted'&&s.pending){const n=await api('/api/action',{id:null,action:'notify',scope_hash:null});if(n.count){const notice=new Notification('Agentic Butler · review needed',{body:`${n.count} workflow proposal(s) need your decision. Nothing runs automatically.`,tag:'pco-review'});notice.onclick=async()=>{window.focus();openReview();notice.close();try{await acknowledgeNotice(n.notification_id);await refresh();}catch(e){error(e.message);}};}}}catch(e){disconnected=true;$('summary').textContent='Disconnected from local companion';$('health').textContent='Local connection unavailable';error(e.message);}finally{requesting=false;}}
$('permission').onclick=async()=>{if(!('Notification'in window)){error('This browser does not support system notifications.');return;}await Notification.requestPermission();await refresh();};
refresh();setInterval(refresh,3000);

function renderAgent(s){
 const enabled=!!s.agent?.enabled;
 $('agent-prepare').disabled=!enabled;
 const project=$('agent-project'),previous=project.value;
 project.replaceChildren();for(const p of s.projects||[]){if(p.tools.includes('codex')){const o=el('option',p.name);o.value=p.name;project.append(o);}}if(previous)project.value=previous;
 const jobs=$('agent-jobs');jobs.replaceChildren();
 if(!enabled){jobs.append(el('p','Live text tasks are disabled. Set agent.enabled in your local configuration and restart to opt in.'));return;}
 for(const job of s.agent_jobs||[]){const box=el('article',undefined,'card'),body=el('div');
 body.append(el('h3',job.scope.project+' · '+job.status),el('p',job.scope.data_disclosure),el('pre',job.scope.prompt),el('p',`No tools · ${job.scope.timeout_seconds}s timeout · ${job.scope.output_limit_bytes} byte output limit · Expires ${job.scope.expires_at}`));
 const detail=el('details');detail.append(el('summary','Full approval scope'),el('pre',JSON.stringify({scope:job.scope,scope_hash:job.scope_hash,integrity_valid:job.integrity_valid},null,2)));body.append(detail);
 if(job.scope.memory_context)body.append(el('p','Includes project memory revision '+job.scope.memory_context.revision),el('pre',job.scope.memory_context.text));
 if(job.output)body.append(el('pre',job.output));
 const controls=el('div',undefined,'actions');for(const [action,label,allowed] of [['approve','Approve this text task',job.status==='pending'&&!job.expired&&job.integrity_valid],['run','Run approved text task',job.status==='approved'&&!job.expired&&job.integrity_valid],['revoke','Revoke',['pending','approved'].includes(job.status)],['cancel','Cancel running task',job.status==='running']]){const b=el('button',label);b.disabled=!allowed;b.onclick=async()=>{b.disabled=true;try{await api('/api/agent',{id:job.id,action,scope_hash:job.scope_hash});await refresh();}catch(e){error(e.message);b.disabled=false;}};controls.append(b);}if(job.status==='completed'&&job.scope.memory_draft_input){const importDraft=el('button','Prepare memory review');importDraft.onclick=async()=>{try{await api('/api/memory',{action:'model-import',project:job.scope.project,agent_id:job.id});await refresh();setTab('memory');}catch(e){error(e.message);}};controls.append(importDraft);}
 body.append(controls,el('pre',job.audit.map(a=>a.status+' · '+a.at).join('\n')));box.append(body);jobs.append(box);}
}
$('agent-prepare').onclick=async()=>{try{await api('/api/agent',{action:'prepare',project:$('agent-project').value,prompt:$('agent-prompt').value});$('agent-prompt').value='';await refresh();}catch(e){error(e.message);}};
$('test-native').onclick=async()=>{try{const result=await api('/api/action',{id:null,action:'test-native',scope_hash:null});nativeFeedback=result.status==='blocked'?'Windows blocked notification submission: '+result.os_setting+'. No system settings were changed.':'Submitted to the OS; display is not confirmed. Click the notification to open the review queue.';$('native-result').textContent=nativeFeedback;}catch(e){error(e.message);}};
