'use strict';

function renderCommands(state) {
  const enabled = !!state.commands?.enabled;
  const selection = $('command-name');
  const previous = selection.value;
  selection.replaceChildren();
  for (const entry of state.commands?.allowlist || []) {
    const option = el('option', entry.project + ' · ' + entry.name);
    option.value = entry.name;
    selection.append(option);
  }
  if ([...(state.commands?.allowlist || [])].some(entry => entry.name === previous)) {
    selection.value = previous;
  }
  $('command-prepare').disabled = !enabled || !selection.children.length;
  const list = $('command-jobs');
  list.replaceChildren();
  if (!enabled) {
    list.append(el('p', 'Local commands are disabled. Register your project test command in the configuration and opt in to prepare it for review.'));
    return;
  }
  for (const job of state.command_jobs || []) {
    const card = el('article', undefined, 'card');
    const body = el('div');
    body.append(el('h3', job.scope.project + ' · ' + job.scope.name + ' · ' + job.status),
      el('p', job.scope.effect),
      el('pre', JSON.stringify(job.scope.argv, null, 2)),
      el('p', 'Working directory: ' + job.scope.workspace),
      el('p', job.scope.timeout_seconds + 's timeout · ' + job.scope.output_limit_bytes +
        ' byte output limit · Expires ' + job.scope.expires_at));
    const details = el('details');
    details.append(el('summary', 'Full approval scope'), el('pre', JSON.stringify({
      scope: job.scope, scope_hash: job.scope_hash, integrity_valid: job.integrity_valid
    }, null, 2)));
    body.append(details);
    if (job.output) body.append(el('pre', job.output));
    if (job.exit_code !== null) body.append(el('p', 'Exit code: ' + job.exit_code));
    const controls = el('div', undefined, 'actions');
    const valid = !job.expired && job.integrity_valid;
    for (const [action, label, allowed] of [
      ['approve', 'Approve this command', job.status === 'pending' && valid],
      ['run', 'Run approved command', job.status === 'approved' && valid],
      ['revoke', 'Revoke', ['pending', 'approved'].includes(job.status)],
      ['cancel', 'Cancel running command', job.status === 'running']
    ]) {
      const button = el('button', label, action === 'approve' ? 'primary' : '');
      button.disabled = !allowed;
      button.onclick = async () => {
        button.disabled = true;
        try {
          const request = api('/api/commands', {id: job.id, action, scope_hash: job.scope_hash});
          if (action === 'run') {
            body.append(el('p', 'Command starting. Refreshing the task status…', 'notice'));
          }
          await request;
          await refresh();
        } catch (failure) {
          error(failure.message);
          await refresh();
        }
      };
      controls.append(button);
    }
    body.append(controls, el('pre', job.audit.map(entry => entry.status + ' · ' + entry.at).join('\n')));
    card.append(body);
    list.append(card);
  }
}

async function prepareCommand(name) {
  const button = $('command-prepare');
  button.disabled = true;
  try {
    await api('/api/commands', {action: 'prepare', name});
    await refresh();
    setTab('commands');
  } catch (failure) {
    error(failure.message);
  } finally {
    button.disabled = !current?.commands?.enabled;
  }
}

$('command-prepare').onclick = () => prepareCommand($('command-name').value);
