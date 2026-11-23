const query = document.querySelector('#query');
const results = document.querySelector('#catalog-results');
const status = document.querySelector('#catalog-status');
let request;

async function search() {
  request?.abort();
  request = new AbortController();
  const signal = request.signal;
  const term = query.value.trim();
  const catalog = new URL('https://huggingface.co/models');
  catalog.searchParams.set('library', 'gguf');
  catalog.searchParams.set('search', term);
  document.querySelector('#catalog-all').href = catalog.href;
  if (/^[\w.-]+\/[\w.-]+$/.test(term)) {
    results.replaceChildren(repositoryCard(term));
    status.textContent = 'Choose a GGUF file from this repository.';
    return;
  }
  const url = new URL('https://huggingface.co/api/models');
  url.search = new URLSearchParams({filter: 'gguf', search: term, sort: 'downloads', direction: '-1', limit: '50'});
  results.replaceChildren();
  status.textContent = 'Searching public GGUF models…';
  try {
    const response = await fetch(url, {signal});
    if (!response.ok) throw new Error();
    const models = await response.json();
    if (signal.aborted) return;
    const ids = models.map(model => model.id);
    if (/^[\w.-]+\/[\w.-]+$/.test(term) && !ids.includes(term)) ids.unshift(term);
    for (const id of ids) results.append(repositoryCard(id));
    status.textContent = ids.length ? `Showing ${ids.length} matching repositories by downloads. Select one to view its files.` : 'No matching public GGUF repositories.';
  } catch {
    if (!signal.aborted) status.textContent = 'Search is unavailable. Open the full catalog below to continue.';
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function bytes(n) {
  if (n == null) return 'size unknown';
  return n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(2) + ' GiB' : (n / 1024 ** 2).toFixed(1) + ' MiB';
}

function repositoryCard(repo) {
  const card = element('div', 'model-card', '');
  const title = element('strong', 'model-name', repo);
  const choose = element('button', 'panel-btn', 'Choose file to download');
  card.append(title, choose);
  choose.addEventListener('click', async () => {
    choose.disabled = true;
    try {
      const response = await fetch('/api/downloads/files?' + new URLSearchParams({repo}));
      if (!response.ok) throw new Error();
      const files = await response.json();
      if (!files.length) { choose.textContent = 'No GGUF files'; return; }
      const select = element('select', 'panel-input', '');
      select.setAttribute('aria-label', 'GGUF file for ' + repo);
      for (const file of files) select.add(new Option(file.name + ' · ' + bytes(file.size), file.name));
      const button = element('button', 'panel-btn', 'Download');
      const feedback = element('p', 'hint', '');
      button.addEventListener('click', async () => {
        button.disabled = true;
        feedback.textContent = 'Starting download…';
        try {
          const res = await fetch('/api/downloads', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({repo, file:select.value})});
          if (!res.ok) throw new Error((await res.json()).error || 'Could not start download.');
          renderProgress(await res.json());
          document.querySelector('#download-progress').scrollIntoView({block:'nearest'});
          feedback.textContent = '';
        } catch (error) { feedback.textContent = error.message; }
        finally { button.disabled = false; }
      });
      choose.replaceWith(select, button, feedback);
    } catch { choose.disabled = false; choose.textContent = 'Retry loading files'; }
  });
  return card;
}

function renderProgress(job) {
  const panel = document.querySelector('#download-progress');
  panel.classList.toggle('hidden', job.state === 'idle');
  if (job.state === 'idle') return;
  const pct = job.size ? Math.floor(100 * job.received / job.size) : 0;
  document.querySelector('#download-title').textContent = job.file;
  document.querySelector('#download-pct').textContent = pct + '%';
  document.querySelector('#download-bar').style.width = pct + '%';
  document.querySelector('#download-detail').textContent = job.error || (job.state === 'complete'
    ? 'Downloaded to the local model library.' : bytes(job.received) + ' of ' + bytes(job.size));
}

async function poll() {
  try {
    const response = await fetch('/api/downloads');
    if (response.ok) renderProgress(await response.json());
  } finally { window.setTimeout(poll, 2000); }
}
poll();

document.querySelector('#catalog-search').addEventListener('submit', event => {
  event.preventDefault();
  search();
});

search();
