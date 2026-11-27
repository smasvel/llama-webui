const query = document.querySelector('#query');
const results = document.querySelector('#catalog-results');
const status = document.querySelector('#catalog-status');
const toolOnlyEl = document.querySelector('#filter-tool-calling');
const excludeUnknownEl = document.querySelector('#filter-exclude-unknown-tools');
const sizeFilterEls = [...document.querySelectorAll('[data-parameter-band]')];
let request;
let foundModels = [];

function toolSupport(model) {
  const tags = (model.tags || []).map(tag => String(tag).toLowerCase());
  if (tags.includes('function-calling') || tags.includes('tool-calling')) return true;
  return null; // Hugging Face does not explicitly report "no tool calling".
}

function parameterCount(model) {
  const text = [model.id, (model.tags || []).join(' ')].join(' ');
  const match = text.match(/(?:^|[^\d])(\d+(?:\.\d+)?)\s*b(?:illion)?\b/i);
  return match ? Number(match[1]) : null;
}

function sizeBand(count) {
  if (count == null) return null;
  return count <= 4 ? 'small' : count <= 13 ? 'medium' : 'large';
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

function formatCount(n) {
  if (n == null) return null;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString();
}

function metaParts(model) {
  const parts = [{ text: 'llama.cpp (GGUF)' }, { text: bytes(model.usedStorage || model.safetensors?.total), key: 'size' }];
  if (toolSupport(model)) parts.unshift({ text: 'supports tool calling' });
  if (model.pipeline_tag) parts.push({ text: model.pipeline_tag });
  const updated = formatDate(model.lastModified);
  if (updated) parts.push({ text: `updated ${updated}` });
  if (model.likes != null) parts.push({ text: `★ ${formatCount(model.likes)}` });
  if (model.downloads != null) parts.push({ text: `${formatCount(model.downloads)} downloads` });
  parts.push({ text: 'Hugging Face' }, { text: 'GGUF' });
  return parts;
}

function modelCard(model) {
  const card = element('div', 'model-card');
  const support = toolSupport(model);
  const badges = element('div', 'model-card-badges');
  badges.append(element('span', 'badge', `tool calling: ${support ? 'yes' : 'unknown'}`));
  const top = element('div', 'model-card-top');
  const info = element('div');
  const meta = element('div', 'model-meta');
  let sizeEl = null;
  metaParts(model).forEach((part, index) => {
    if (index) meta.append(document.createTextNode(' · '));
    const span = element('span', part.key === 'size' ? 'model-size-value' : '', part.text);
    meta.append(span);
    if (part.key === 'size') sizeEl = span;
  });
  info.append(element('div', 'model-name', model.id), meta);
  top.append(info, badges);
  const actions = element('div', 'model-card-actions');
  const source = element('a', 'panel-btn secondary', 'Model source');
  source.href = `https://huggingface.co/${model.id}`;
  source.target = '_blank';
  source.rel = 'noopener noreferrer';
  const download = element('button', 'panel-btn', 'Download');
  actions.append(source, download);
  card.append(top, actions);
  download.addEventListener('click', async () => {
    download.disabled = true;
    download.textContent = 'Loading files…';
    try {
      const res = await fetch('/api/downloads/files?' + new URLSearchParams({ repo: model.id }));
      if (!res.ok) throw new Error();
      const files = await res.json();
      if (!files.length) { download.textContent = 'No GGUF files'; return; }
      const select = element('select', 'panel-input', '');
      select.setAttribute('aria-label', 'GGUF file for ' + model.id);
      for (const file of files) select.add(new Option(file.name + ' · ' + bytes(file.size), file.name));
      const start = element('button', 'panel-btn', 'Download');
      const feedback = element('p', 'hint', '');
      start.addEventListener('click', async () => {
        start.disabled = true;
        feedback.textContent = 'Starting download…';
        try {
          const response = await fetch('/api/downloads', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo: model.id, file: select.value }),
          });
          if (!response.ok) throw new Error((await response.json()).error || 'Could not start download.');
          renderProgress(await response.json());
          document.querySelector('#download-progress').scrollIntoView({ block: 'nearest' });
          feedback.textContent = '';
        } catch (error) {
          feedback.textContent = error.message;
          start.disabled = false;
        }
      });
      download.replaceWith(select, start, feedback);
    } catch {
      download.disabled = false;
      download.textContent = 'Retry loading files';
    }
  });
  card.dataset.repo = model.id;
  card.dataset.sizeEl = sizeEl ? 'yes' : '';
  return card;
}

async function enrichSizes(cards) {
  await Promise.allSettled(cards.slice(0, 12).map(async card => {
    const span = card.querySelector('.model-size-value');
    if (!span) return;
    try {
      const res = await fetch('/api/downloads/files?' + new URLSearchParams({ repo: card.dataset.repo }));
      if (!res.ok) return;
      const files = await res.json();
      const largest = files.filter(f => String(f.name).toLowerCase().endsWith('.gguf'))
        .reduce((max, f) => Math.max(max, Number(f.size) || 0), 0);
      if (largest > 0) {
        span.textContent = bytes(largest);
        span.title = 'Largest GGUF file in this repository';
      }
    } catch { /* keep the catalog size */ }
  }));
}

function render() {
  results.replaceChildren();
  let models = foundModels;
  if (toolOnlyEl?.checked) models = models.filter(model => toolSupport(model) === true);
  if (excludeUnknownEl?.checked) models = models.filter(model => toolSupport(model) !== null);
  const bands = new Set(sizeFilterEls.filter(el => el.checked).map(el => el.dataset.parameterBand));
  if (bands.size) models = models.filter(model => bands.has(sizeBand(parameterCount(model))));
  const cards = models.map(modelCard);
  for (const card of cards) results.append(card);
  if (cards.length) enrichSizes(cards);
  status.textContent = models.length
    ? `Showing ${models.length} llama.cpp GGUF models. Select a file to download.`
    : (foundModels.length ? 'No models match the selected filters.' : 'No matching public GGUF repositories.');
}

async function search() {
  request?.abort();
  request = new AbortController();
  const signal = request.signal;
  const term = query.value.trim();
  // Default view mirrors:
  // https://huggingface.co/models?library=gguf&apps=llama.cpp&sort=trending&other=function-calling
  document.querySelector('#catalog-all').href =
    'https://huggingface.co/models?library=gguf&apps=llama.cpp&sort=trending&other=function-calling';
  const url = new URL('https://huggingface.co/api/models');
  const params = term
    ? { filter: 'gguf', search: term, sort: 'trendingScore', direction: '-1', limit: '50', full: 'true' }
    : { filter: 'gguf', apps: 'llama.cpp', other: 'function-calling',
        sort: 'trendingScore', direction: '-1', limit: '50', full: 'true' };
  url.search = new URLSearchParams(params);
  status.textContent = 'Searching public GGUF models…';
  try {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error();
    foundModels = (await response.json()).filter(model => model?.id);
    if (signal.aborted) return;
    render();
  } catch {
    if (!signal.aborted) status.textContent = 'Search is unavailable. Open the full catalog below to continue.';
  }
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
for (const el of [toolOnlyEl, excludeUnknownEl, ...sizeFilterEls]) {
  el?.addEventListener('change', render);
}
search();
