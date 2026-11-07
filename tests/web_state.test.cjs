// State regressions for the actual browser script, without external packages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function node() {
  return {textContent: '', hidden: false, children: [], className: '', dataset: {},
    classList: {toggle() {}}, style: {setProperty() {}},
    append(...items) {this.children.push(...items);},
    replaceChildren(...items) {this.children = items;}, setAttribute() {}};
}
const elements = new Map();
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, node());
    return elements.get(id);
  }, querySelectorAll() {return [];}, createElement: node,
  documentElement: {style: {setProperty() {}}}};
const state = {appearance: {accent: '#8b5cf6', compact: false}, pending: 0, approved: 0,
  failures: 0, state: 'quiet', worker_error: false, proposals: [], adapters: [],
  notifications: {enabled: false, backend: 'browser'}, execution: 'preview_only'};
let online = true;
let notificationPosts=0;let receiptFailure=false;let nativeReply=null;let memoryActions=[];
const context = vm.createContext({document, window: {PCO_TOKEN: 'fixture'}, setInterval() {},
  fetch: async (path,options) => {
    if(path==='/api/memory'){const body=JSON.parse(options.body);memoryActions.push(body);if(body.action==='approve')state.memories[0].changes[0].status='approved';return {ok:true,json:async()=>({status:'approved'})};}
    if(path==='/api/action'&&nativeReply)return {ok:true,json:async()=>nativeReply};
    if(path==='/api/notification-open'){notificationPosts++;return {ok:!receiptFailure,json:async()=>receiptFailure?{error:'Notification link expired'}:{status:'review_opened'}};}
    if (!online) throw new Error('fixture offline');
    return {ok: true, json: async () => JSON.parse(JSON.stringify(state))};
  }});
let script = fs.readFileSync(path.join(__dirname, '../src/proactive_coding_orchestrator/web/app.js'), 'utf8');
script = script.replace('refresh();setInterval(refresh,3000);', '');
vm.runInContext(script, context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/proactive_coding_orchestrator/web/memory.js'),'utf8'),context);

(async () => {
  await vm.runInContext('refresh()', context);
  assert.equal(elements.get('summary').textContent, 'Nothing needs your attention');
  online = false;
  await vm.runInContext('refresh()', context);
  assert.equal(elements.get('summary').textContent, 'Disconnected from local companion');
  assert.equal(elements.get('error').hidden, false);
  online = true; // Identical server snapshot must still restore connection state.
  await vm.runInContext('refresh()', context);
  assert.equal(elements.get('summary').textContent, 'Nothing needs your attention');
  assert.equal(elements.get('error').hidden, true);
  console.log('PASS: unchanged state recovers after disconnect');

  vm.runInContext("selected='disabled-proposal';$('detail').hidden=false;render(current)", context);
  assert.equal(elements.get('detail').hidden, true);
  assert.equal(elements.get('detail').children.length, 0);
  console.log('PASS: removed/disabled proposal clears stale review controls');
  context.window.location={pathname:'/review',hash:'#notice='+'A'.repeat(32)};
  state.appearance.compact=true;
  context.fixtureState=JSON.parse(JSON.stringify(state));
  vm.runInContext('initialized=false;render(fixtureState)', context);
  assert.equal(elements.get('content').hidden,true);
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('content').hidden,false);
  assert.equal(elements.get('review').hidden,false);
  assert.equal(elements.get('expand').textContent,'Collapse');
  assert.match(elements.get('notification-result').textContent,/Nothing has been approved or run/);
  await vm.runInContext('refresh()',context);
  assert.equal(notificationPosts,1);
  console.log('PASS: notification deep link expands review and acknowledges once without executing');
  vm.runInContext("setTab('connections');$('content').hidden=true",context);
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('connections').hidden,false);
  assert.equal(elements.get('content').hidden,true);
  console.log('PASS: background refresh preserves navigation after notification open');
  receiptFailure=true;
  vm.runInContext('noticeAcknowledged=false;notificationRouteHandled=false',context);
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('summary').textContent,'Nothing needs your attention');
  assert.equal(elements.get('error').textContent,'Notification link expired');
  console.log('PASS: invalid notification receipt does not mark companion disconnected');
  nativeReply={status:'blocked',os_setting:'DisabledForApplication'};
  await elements.get('test-native').onclick();
  await vm.runInContext('refresh()',context);
  assert.match(elements.get('native-result').textContent,/Windows blocked/);
  assert.doesNotMatch(elements.get('native-result').textContent,/Submitted to the OS/);
  console.log('PASS: blocked native notices never appear as submitted and feedback survives refresh');
  const record={id:'record-one',kind:'lesson',text:'<script>fixture only</script>',conditions:'Windows',pinned:false,evidence:[]};
  state.memory_pending=1;
  state.memories=[{project:'personal',revision:1,records:[record],observations:0,checkpoints:[],
    revisions:[{revision:1},{revision:0}],bundle:{text:'Fixture memory'},
    changes:[{id:'change-one',status:'pending',scope_hash:'reviewed-hash',integrity_valid:true,expired:false,stale:false,
      audit:[],evidence:[],scope:{base_revision:1,method:'deterministic',before:[],after:[record],
      operations:[{action:'add',record,reason:'Verified fixture evidence'}]}}]}];
  await vm.runInContext('refresh()',context);
  function descendants(element){return [element,...element.children.flatMap(descendants)];}
  const draftNodes=descendants(elements.get('memory-drafts'));
  assert(draftNodes.some(element=>element.textContent.includes('<script>fixture only</script>')));
  console.log('PASS: memory review renders untrusted findings as literal text');
  const approveMemory=draftNodes.find(element=>element.textContent==='Approve memory update');
  await approveMemory.onclick();
  assert.equal(memoryActions.length,1);
  assert.equal(memoryActions[0].action,'approve');
  assert.equal(memoryActions[0].scope_hash,'reviewed-hash');
  console.log('PASS: memory approval posts its reviewed scope without applying automatically');
  const editMemory=descendants(elements.get('memory-records')).find(element=>element.textContent==='Edit guidance');
  await editMemory.onclick();
  elements.get('memory-edit-text').value='User is editing this guidance';
  vm.runInContext('renderMemory(current)',context);
  assert.equal(elements.get('memory-edit-text').value,'User is editing this guidance');
  console.log('PASS: memory polling preserves the active guidance editor');
  state.memories[0].changes[0].status='applied';
  state.memories[0].changes[0].stale=true;
  await vm.runInContext('refresh()',context);
  assert(!descendants(elements.get('memory-drafts')).some(element=>element.textContent.includes('needs a fresh review')));
  console.log('PASS: applied memory history is not mislabeled as awaiting fresh review');
})().catch(error => {console.error(error); process.exitCode = 1;});
