'use strict';
let memoryBusy = false;
let memoryEditor = null;

function selectedMemory() {
  return current?.memories?.find(project => project.project === $('memory-project').value);
}

async function memoryRequest(data) {
  memoryBusy = true;
  if (current) renderMemory(current);
  try {
    const result = await api('/api/memory', data);
    $('error').hidden = true;
    await refresh();
    return result;
  } catch (failure) {
    error(failure.message);
    throw failure;
  } finally {
    memoryBusy = false;
    if (current) renderMemory(current);
  }
}

function memoryButton(label, enabled, action) {
  const button = el('button', label);
  button.disabled = !enabled || memoryBusy;
  button.onclick = async () => {
    try { await action(); } catch (_) { /* memoryRequest displays the failure. */ }
  };
  return button;
}

function recordDescription(record) {
  return record.text + '\nApplies when: ' + (record.conditions || 'This project') +
    '\n' + record.evidence.length + ' checkpoint reference(s)' + (record.pinned ? ' · Pinned guidance' : '');
}

function checkpointDescription(notes) {
  return notes.map(note => note.text + '\nApplies when: ' + (note.conditions || 'This project') +
    '\nVerification: ' + (note.verified ? note.verification : 'Awaiting verification') +
    '\nTask or attempt: ' + note.incident_id).join('\n\n');
}

function prepareMemoryOperation(project, operation) {
  return memoryRequest({action: 'propose', project: project.project, operations: [operation],
    allow_pinned: $('memory-allow-pinned').checked, expected_revision: project.revision});
}

function renderMemory(state) {
  const projects = state.memories || [];
  const select = $('memory-project');
  const previous = select.value;
  select.replaceChildren();
  for (const project of projects) {
    const option = el('option', project.project);
    option.value = project.project;
    select.append(option);
  }
  select.value = projects.some(project => project.project === previous) ? previous : (projects[0]?.project || '');
  $('memory-count').textContent = state.memory_pending || 0;
  const project = selectedMemory();
  $('memory-controls').hidden = !project;
  $('memory-drafts').replaceChildren();
  $('memory-records').replaceChildren();
  $('memory-checkpoints').replaceChildren();
  if (!project) {
    $('memory-status').textContent = 'Opt a personal project into memory in your configuration to begin.';
    $('memory-editor').hidden = true;
    return;
  }
  $('memory-status').textContent = project.bundle.capacity_error ||
    `Revision ${project.revision} · ${project.records.length} guidance record(s) · ${project.observations} working checkpoint(s). Drafting runs locally.`;
  $('memory-save').disabled = memoryBusy;
  $('memory-dream').disabled = memoryBusy;
  $('memory-semantic').disabled = memoryBusy || !state.agent?.enabled;
  $('memory-rollback').disabled = memoryBusy;
  $('memory-editor').hidden = !memoryEditor || memoryEditor.project !== project.project;
  const revisions = $('memory-revision');
  const selectedRevision = revisions.value;
  revisions.replaceChildren();
  for (const revision of project.revisions) {
    const option = el('option', 'Revision ' + revision.revision);
    option.value = String(revision.revision);
    revisions.append(option);
  }
  revisions.value = project.revisions.some(row => String(row.revision) === selectedRevision)
    ? selectedRevision : String(project.revision);
  $('memory-rollback').disabled = memoryBusy || Number(revisions.value) === project.revision;
  for (const change of project.changes) {
    const card = el('article', undefined, 'card');
    const body = el('div');
    body.append(el('h3', 'Memory update · ' + change.status),
      el('p', `Based on revision ${change.scope.base_revision} · ${change.scope.method} draft`));
    const active = ['pending', 'approved'].includes(change.status);
    if (!change.integrity_valid || (active && (change.stale || change.expired))) {
      body.append(el('p', 'This draft needs a fresh review because its scope is stale, expired or inconsistent.', 'notice'));
    }
    const before = new Map(change.scope.before.map(record => [record.id, record]));
    for (const operation of change.scope.operations) {
      body.append(el('h3', operation.action), el('p', operation.reason));
      const old = before.get(operation.id);
      if (old) body.append(el('strong', 'Before'), el('pre', recordDescription(old)));
      if (operation.record) body.append(el('strong', 'After'), el('pre', recordDescription(operation.record)));
    }
    const evidence = el('details');
    evidence.append(el('summary', 'Checkpoint evidence and verification claims'));
    for (const checkpoint of change.evidence) {
      evidence.append(el('p', `${checkpoint.tool} · ${checkpoint.session} · ${checkpoint.agent || 'main agent'}`),
        el('pre', checkpointDescription(checkpoint.payload.notes)));
    }
    body.append(evidence);
    const integrity = el('details');
    integrity.append(el('summary', 'Full approval scope and audit'),
      el('pre', JSON.stringify({scope: change.scope, hash: change.scope_hash, audit: change.audit}, null, 2)));
    body.append(integrity, el('p', 'Approval and applying the new revision are separate actions.', 'notice'));
    const valid = change.integrity_valid && !change.expired && !change.stale;
    const controls = el('div', undefined, 'actions');
    for (const [action, label, enabled] of [
      ['approve', 'Approve memory update', change.status === 'pending' && valid],
      ['apply', 'Apply approved revision', change.status === 'approved' && valid],
      ['reject', 'Dismiss draft', ['pending', 'approved'].includes(change.status)],
      ['revoke', 'Withdraw draft', ['pending', 'approved'].includes(change.status)]
    ]) {
      controls.append(memoryButton(label, enabled, () => memoryRequest({action, project: project.project,
        id: change.id, scope_hash: change.scope_hash})));
    }
    body.append(controls);
    card.append(body);
    $('memory-drafts').append(card);
  }
  if (!project.changes.length) $('memory-drafts').append(el('p', 'No learning drafts yet.'));
  for (const record of project.records) {
    const card = el('article', undefined, 'card');
    const body = el('div');
    body.append(el('strong', record.kind), el('pre', recordDescription(record)));
    body.append(memoryButton('Edit guidance', true, async () => {
      memoryEditor = {project: project.project, revision: project.revision, record};
      $('memory-edit-text').value = record.text;
      $('memory-edit-conditions').value = record.conditions;
      $('memory-editor').hidden = false;
    }));
    body.append(memoryButton('Prepare archive', true, () => prepareMemoryOperation(project,
      {action: 'retire', id: record.id, reason: 'Archive guidance that is no longer applicable.'})));
    card.append(body);
    $('memory-records').append(card);
  }
  if (!project.records.length) $('memory-records').append(el('p', 'Approved experience will appear here.'));
  for (const checkpoint of project.checkpoints) {
    const card = el('article', undefined, 'card');
    const body = el('div');
    body.append(el('p', checkpoint.session + ' · ' + checkpoint.tool),
      el('pre', checkpointDescription(checkpoint.payload.notes)),
      memoryButton('Dismiss checkpoint', true, () => memoryRequest({action: 'dismiss', project: project.project,
        id: checkpoint.id, scope_hash: null})));
    card.append(body);
    $('memory-checkpoints').append(card);
  }
}

$('memory-project').onchange = () => {
  memoryEditor = null;
  if (current) renderMemory(current);
};
$('memory-save').onclick = async () => {
  const project = selectedMemory();
  if (!project) return;
  const delivery = window.crypto.randomUUID();
  try {
    await memoryRequest({action: 'record', project: project.project,
      session: $('memory-session').value, delivery_id: delivery, final: true,
      notes: [{kind: 'lesson', text: $('memory-note').value, conditions: $('memory-conditions').value,
        verified: $('memory-verified').checked, verification: $('memory-verification').value,
        incident_id: $('memory-incident').value}]});
    $('memory-note').value = '';
  } catch (_) { /* The form remains available for correction. */ }
};
$('memory-dream').onclick = async () => {
  const project = selectedMemory();
  if (!project) return;
  try { await memoryRequest({action: 'dream', project: project.project}); } catch (_) {}
};
$('memory-rollback').onclick = async () => {
  const project = selectedMemory();
  if (!project) return;
  try {
    await memoryRequest({action: 'rollback', project: project.project,
      revision: Number($('memory-revision').value), allow_pinned: $('memory-allow-pinned').checked});
  } catch (_) {}
};
$('memory-edit-cancel').onclick = () => {
  memoryEditor = null;
  $('memory-editor').hidden = true;
};
$('memory-edit-save').onclick = async () => {
  if (!memoryEditor) return;
  const editor = memoryEditor;
  try {
    await prepareMemoryOperation({project: editor.project, revision: editor.revision}, {
      action: 'revise', id: editor.record.id,
      record: {...editor.record, text: $('memory-edit-text').value, conditions: $('memory-edit-conditions').value},
      reason: 'Revise guidance after reviewing its applicability.'});
    memoryEditor = null;
    $('memory-editor').hidden = true;
  } catch (_) {}
};
if (current) renderMemory(current);

$('memory-semantic').onclick = async () => {
  const project = selectedMemory();
  if (!project) return;
  try {
    await memoryRequest({action: 'model-prepare', project: project.project});
    setTab('agent');
  } catch (_) {}
};

$('memory-revision').onchange = () => {
  const project = selectedMemory();
  $('memory-rollback').disabled = memoryBusy || !project || Number($('memory-revision').value) === project.revision;
};
