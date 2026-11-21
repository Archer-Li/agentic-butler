'use strict';

async function ciAction(data, destination = 'ci') {
  try {
    await api('/api/ci', data);
    await refresh();
    setTab(destination);
  } catch (failure) {
    error(failure.message);
  }
}

function renderCI(state) {
  const enabled = !!state.ci?.enabled;
  $('ci-open').disabled = !enabled;
  const proposals = $('ci-proposal');
  const previous = proposals.value;
  proposals.replaceChildren();
  for (const p of state.proposals || []) {
    if (p.scope?.kind !== 'ci_failed') continue;
    const option = el('option', p.project + ' · ' + p.title + ' · ' + p.id);
    option.value = p.id;
    proposals.append(option);
  }
  if ([...(state.proposals || [])].some(p => p.id === previous)) proposals.value = previous;
  const list = $('ci-cases');
  list.replaceChildren();
  if (!enabled) {
    list.append(el('p', 'Enable ci.enabled for selected project evidence and agent.enabled for separately approved analysis.'));
    return;
  }
  const labels = {
    needs_analysis: 'Ready for diagnosis', changed: 'Selected files changed · refresh diagnosis',
    diagnosed: 'Diagnosis available', plan_ready: 'Modification plan available',
    needs_verification: 'Fix reported · verification needed', verifying: 'Verification in progress',
    verified: 'Selected verification passed', evidence_unavailable: 'Selected evidence unavailable'
  };
  for (const investigation of state.ci_cases || []) {
    const card = el('article', undefined, 'card'), body = el('div');
    const compact = ['needs_verification', 'verifying', 'verified'].includes(investigation.state);
    body.append(el('h3', investigation.project + ' · ' + labels[investigation.state]));
    const staleNotice = el('p', 'Earlier analysis describes older evidence. Review a fresh task before using its recommendations.', 'notice');
    if (investigation.stale && !compact) body.append(staleNotice);
    const evidence = el('details');
    evidence.open = !compact;
    evidence.append(el('summary', compact ? 'Earlier evidence and recommendations' : 'Selected evidence and recommendations'));
    if (investigation.stale && compact) evidence.append(staleNotice);
    if (investigation.material) {
      evidence.append(el('h3', 'Failure log'), el('pre', investigation.material.log));
      for (const file of investigation.material.files) {
        evidence.append(el('h3', file.path), el('pre', file.text.split('\n').map((line, index) => (index + 1) + ' ' + line).join('\n')));
      }
    }
    if (investigation.diagnosis) evidence.append(el('h3', 'Diagnosis'), el('pre', JSON.stringify(investigation.diagnosis, null, 2)));
    if (investigation.plan) evidence.append(el('h3', 'Modification plan'), el('pre', JSON.stringify(investigation.plan, null, 2)));
    evidence.append(el('pre', investigation.audit.map(entry => entry.action + ' · ' + entry.at).join('\n')));
    body.append(evidence);
    const actions = el('div', undefined, 'actions');
    const canAnalyze = !investigation.evidence_error && !investigation.user_fixed && investigation.state !== 'verified' && state.agent?.enabled;
    for (const [action, label, allowed] of [
      ['diagnose', 'Prepare diagnosis for review', canAnalyze],
      ['plan', 'Prepare modification plan for review', canAnalyze && investigation.diagnosis && !investigation.stale],
      ['fixed', 'I have made the fix', !investigation.user_fixed && investigation.state !== 'verified'],
      ['resume', 'Investigate again', !!investigation.user_fixed || investigation.state === 'verified']
    ]) {
      if (compact && ['diagnose', 'plan', 'fixed'].includes(action)) continue;
      const button = el('button', label);
      button.disabled = !allowed;
      button.onclick = () => ciAction({action, id: investigation.id}, ['diagnose', 'plan'].includes(action) ? 'agent' : 'ci');
      actions.append(button);
    }
    for (const [id, stage] of [[investigation.diagnosis_job, 'diagnosis'], [investigation.plan_job, 'plan']]) {
      if (!id || compact) continue;
      const job = (state.agent_jobs || []).find(item => item.id === id);
      const button = el('button', 'Import completed ' + stage);
      button.disabled = job?.status !== 'completed' || investigation.stale && stage === 'plan';
      button.onclick = () => ciAction({action: 'import', id: investigation.id, agent_id: id});
      actions.append(button);
    }
    if (investigation.state !== 'verified') {
      for (const command of state.commands?.enabled ? state.commands.allowlist : []) {
        if (command.project !== investigation.project) continue;
        const button = el('button', 'Review verification: ' + command.name);
        button.disabled = investigation.evidence_error;
        button.onclick = () => ciAction({action: 'verify', id: investigation.id, name: command.name}, 'commands');
        actions.append(button);
      }
    }
    body.append(actions);
    if (investigation.state === 'verified') body.append(el('p', 'The selected command passed for the selected evidence snapshot. Full remote CI status is separate.'));
    card.append(body);
    list.append(card);
  }
}

$('ci-open').onclick = () => {
  const files = $('ci-files').value.split('\n').map(path => path.trim()).filter(Boolean);
  ciAction({action: 'open', proposal_id: $('ci-proposal').value, log: $('ci-log').value, files,
    command_id: $('ci-command-id').value.trim() || null});
};
