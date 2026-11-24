// Full-page model manager. Self-contained: lists models from /api/models grouped by
// backend, with Load / Unload / Delete, a summary strip, search filter, and load options.
(() => {
  const el = (id) => document.getElementById(id);
  const groupsEl = el("model-groups");
  const localToolsEl = el("local-model-tools");
  const toolbarEl = el("model-toolbar");
  const viewTabs = [...document.querySelectorAll(".model-view-tab")];
  const summaryEl = el("model-summary");
  const searchEl = el("model-search");
  const warningsOnlyEl = el("model-warnings-only");
  const maxContextBtn = el("model-max-context-btn");
  const toastEl = el("toast");
  const localUploadForm = el("local-model-upload-form");
  const localUploadInput = el("local-model-file");
  const localUploadButton = el("local-model-upload-btn");
  const localUploadStatus = el("local-model-upload-status");
  const localUploadProgress = el("local-model-upload-progress");

  function displayName(m) {
    if (m.name && m.name !== m.id) return m.name;
    const raw = String(m.id || "");
    const base = raw.split("/").pop() || raw;
    return base.replace(/\.gguf$/i, "");
  }

  function formatBytes(n) {
    if (n == null) return "";
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function copyableField(text, label, title, copyText = text) {
    const wrap = document.createElement("span");
    wrap.className = "model-copyable-field";
    const span = document.createElement("span");
    span.className = "model-copyable-text";
    span.textContent = text;
    if (title) span.title = title;
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "model-copy-btn";
    copyBtn.textContent = "copy";
    copyBtn.setAttribute("aria-label", `Copy ${label}`);
    copyBtn.title = `Copy ${label}`;
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(copyText);
        copyBtn.textContent = "copied";
        setTimeout(() => { copyBtn.textContent = "copy"; }, 1200);
      } catch {
        copyBtn.textContent = "failed";
        setTimeout(() => { copyBtn.textContent = "copy"; }, 1200);
      }
    });
    wrap.append(span, copyBtn);
    return wrap;
  }

  if (localUploadForm) {
    localUploadForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const file = localUploadInput.files[0];
      if (!file) return;
      localUploadButton.disabled = true;
      localUploadStatus.textContent = `Uploading ${file.name}…`;
      localUploadProgress.classList.remove("hidden");
      localUploadProgress.firstElementChild.style.width = "0%";
      const form = new FormData();
      form.append("model_file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/models/upload-local");
      xhr.setRequestHeader("X-CSRF-Token", document.querySelector('meta[name="csrf-token"]')?.content || "");
      xhr.upload.addEventListener("progress", (progress) => {
        if (!progress.lengthComputable) return;
        const percent = Math.round(progress.loaded / progress.total * 100);
        localUploadProgress.firstElementChild.style.width = `${percent}%`;
        localUploadStatus.textContent = `Uploading ${file.name}… ${percent}%`;
      });
      xhr.addEventListener("load", () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch { /* handled as an HTTP error */ }
        if (xhr.status < 200 || xhr.status >= 300) {
          localUploadStatus.textContent = data.error || `Upload failed (HTTP ${xhr.status}).`;
        } else {
          localUploadProgress.firstElementChild.style.width = "100%";
          localUploadStatus.textContent = `${data.model_id} uploaded and registered (${formatBytes(data.size_bytes)}). Refresh the model list to load it.`;
          localUploadInput.value = "";
          fetchModels();
        }
        localUploadButton.disabled = false;
      });
      xhr.addEventListener("error", () => {
        localUploadStatus.textContent = "Upload failed because the connection was interrupted.";
        localUploadButton.disabled = false;
      });
      xhr.addEventListener("abort", () => {
        localUploadStatus.textContent = "Upload was cancelled.";
        localUploadButton.disabled = false;
      });
      xhr.send(form);
    });
  }

  const GROUPS = [
    { key: "llamacpp", label: "llama.cpp engine (local GGUF library)" },
    { key: "opencode", label: "opencode.ai (remote)" },
  ];
  const LABEL = Object.fromEntries(GROUPS.map((g) => [g.key, g.label]));
  const NON_CHAT = ["embeddings", "transcription", "tts", "image", "video"];
  // Both llama.cpp services are started on demand: a stopped service means the next
  // Load is a cold start, so make that state unmistakable on the card.
  const ON_DEMAND_BACKENDS = new Set(["llamacpp"]);

  let models = [];
  let activeView = "local";
  let fetchingModels = false;
  let busy = false;
  let isEditingContext = false;
  let busyModelId = null;   // which card is mid-load/unload/delete, so it can show that visibly
  let busyLabel = "";       // "Loading" / "Unloading" / "Deleting", shown on the card + toast

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s ?? "";
    return d.innerHTML;
  }
  function fmtSize(gb) {
    if (typeof gb !== "number" || gb <= 0) return null;
    return gb < 1 ? `${Math.round(gb * 1000)} MB` : `${gb.toFixed(1)} GB`;
  }
  let toastTimer;
  function toast(msg, isErr, persist) {
    toastEl.textContent = msg;
    toastEl.style.display = "block";
    toastEl.style.borderColor = isErr ? "var(--danger)" : "var(--border)";
    clearTimeout(toastTimer);
    // persist=true skips the auto-hide — used for long-running service starts so the page
    // doesn't look idle/hung for the rest of the wait. A cold llama.cpp start can take minutes;
    // a disappearing toast made a successful load look like a failure.
    if (!persist) toastTimer = setTimeout(() => { toastEl.style.display = "none"; }, 4000);
  }

  async function fetchModels() {
    if (fetchingModels) return;
    fetchingModels = true;
    try {
      const res = await fetch("/api/models");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "failed to list models");
      models = data.models || [];
      render();
    } catch (err) {
      groupsEl.innerHTML = `<p class="hint">Could not load models: ${esc(err.message)}</p>`;
    } finally {
      fetchingModels = false;
    }
  }

  function render() {
    renderSummary();
    renderGroups();
  }

  function renderSummary() {
    const loaded = models.filter((m) => m.backend !== "opencode" && m.state === "loaded");
    const onDisk = models.filter((m) => m.backend === "llamacpp");
    const chip = (val, label) =>
      `<div class="model-sum-chip"><div class="model-sum-val">${val}</div><div class="model-sum-label">${esc(label)}</div></div>`;
    let loadedHtml = "";
    if (loaded.length) {
      loadedHtml = `<div class="model-loaded-now"><span class="hint">Loaded now:</span> ` +
        loaded.map((m) => `<span class="model-loaded-pill" title="${esc(m.id)}">${esc(displayName(m))} <span class="hint">· ${esc(LABEL[m.backend] || m.backend)}</span></span>`).join(" ") +
        `</div>`;
    }
    summaryEl.innerHTML =
      `<div class="model-sum-chips">` +
      chip(onDisk.length, "on disk") +
      chip(loaded.length, "loaded") +
      chip(fmtSize(onDisk.reduce((s, m) => s + (m.size_gb || 0), 0)) || "—", "total size") +
      chip(models.filter(m => m.resource_warning || m.load_status?.state === "error").length, "with warnings") +
      `</div>` + loadedHtml;
  }

  function card(m) {
    const loaded = m.state === "loaded";
    const stopped = !loaded && ON_DEMAND_BACKENDS.has(m.backend);
    const isBusy = m.id === busyModelId || m.download_state === "downloading";
    const size = fmtSize(m.size_gb);
    const backendLabel = loaded ? LABEL[m.backend] || m.backend : "";
    const metaParts = [backendLabel, m.publisher, m.quantization, m.type, size].filter(Boolean);
    const meta = metaParts.map(esc).join(" · ");
    const c = document.createElement("div");
    c.className = "model-card2" + (loaded ? " loaded" : "") + (stopped ? " stopped" : "") + (isBusy ? " busy" : "");
    c.dataset.name = (m.id || "").toLowerCase();
    const badge = isBusy
      ? `<span class="badge loading">${esc(busyLabel)}…</span>`
      : loaded
        ? `<span class="badge loaded">${m.backend === "opencode" ? "online · remote" : "running"}</span>`
        : stopped
          ? `<span class="badge stopped">stopped · not running</span>`
          : `<span class="badge not-loaded">not loaded</span>`;
    const stoppedNote = stopped
      ? `<div class="model-stopped-note">${esc(LABEL[m.backend] || m.backend)} service is stopped — this model is not running. Load to start it.</div>`
      : "";
    c.innerHTML =
      `<div class="model-card2-main">
        <div class="model-card2-name" title="${esc(m.id)}">${esc(displayName(m))}</div>
        <div class="model-card2-meta">${meta || "&nbsp;"}</div>
        ${stoppedNote}
      </div>
      ${badge}
      <div class="model-card2-actions"></div>`;
    const contextTokens = Number(m.context_window_tokens);
    const metaEl = c.querySelector(".model-card2-meta");
    const loadedContext = Number(m.loaded_context_length);
    if (loaded && Number.isSafeInteger(loadedContext) && loadedContext > 0) {
      const loadedContextLabel = document.createElement("div");
      loadedContextLabel.className = "model-loaded-context-row";
      loadedContextLabel.textContent = `loaded ctx-size: ${loadedContext.toLocaleString()} tokens`;
      loadedContextLabel.title = "Context window currently active in the loaded model runtime.";
      c.querySelector(".model-card2-main").appendChild(loadedContextLabel);
    }
    if (Number.isSafeInteger(contextTokens) && contextTokens > 0) {
      const contextLabel = document.createElement("span");
      contextLabel.className = "model-context-label";
      const isModelMax = m.context_window_source === "model_max";
      const labelPrefix = isModelMax ? "Max context" : "Context";
      contextLabel.textContent = `${labelPrefix}: ${new Intl.NumberFormat(undefined).format(contextTokens)} tokens`;
      const sourceText = {
        model_max: "Maximum context reported by the model backend.",
        loaded_runtime: "Current loaded runtime context; the model may support a larger window.",
        configured: "Context length configured for this model or runtime.",
        backend_default: "llama-web's default context for this backend; the model's actual maximum is not reported.",
      }[m.context_window_source] || "Context length reported by the backend.";
      contextLabel.title = sourceText;
      metaEl.appendChild(contextLabel);
      metaEl.append(document.createTextNode(" · "));
    }
    // The saved value is reused on every later Load, so an unloaded model keeps its window.
    const contextEditable = m.context_editable === true || m.backend === "llamacpp";
    if (contextEditable && m.type && !NON_CHAT.includes(m.type) && m.type !== "embedding") {
      const activeContext = Number(m.configured_context_length || m.loaded_context_length || m.context_window_tokens);
      const loadedContext = Number(m.loaded_context_length);
      const maxContext = Number(m.safe_context_length || m.max_context_length || m.context_window_tokens);
      if (Number.isSafeInteger(activeContext) && activeContext > 0) {
        const ctxField = document.createElement("span");
        ctxField.className = "model-copyable-field";
        const fieldLabel = document.createElement("span");
        fieldLabel.className = "model-field-label";
        fieldLabel.textContent = "model loading ctx-size:";
        const ctxInput = document.createElement("input");
        ctxInput.type = "number";
        ctxInput.className = "model-context-input";
        ctxInput.min = 512;
        if (Number.isSafeInteger(maxContext) && maxContext > 0) ctxInput.max = String(maxContext);
        ctxInput.value = String(activeContext);
        ctxInput.disabled = isBusy;
        ctxInput.title = Number.isSafeInteger(maxContext) && maxContext > 0
          ? `Served context window in tokens (model maximum ${maxContext.toLocaleString()}). Saving reloads a loaded model.`
          : "Served context window in tokens. Saving reloads a loaded model.";
        if (Number.isSafeInteger(loadedContext) && loadedContext > 0 && loadedContext !== activeContext) {
          ctxInput.title += ` Currently loaded at ${loadedContext.toLocaleString()} tokens; the saved value applies on the next Load.`;
        }
        ctxInput.setAttribute("aria-label", `Context size in tokens for ${m.id}`);
        ctxInput.addEventListener("focus", () => { isEditingContext = true; });
        ctxInput.addEventListener("blur", () => { isEditingContext = false; });
        ctxInput.addEventListener("change", () => { isEditingContext = false; updateContextLength(m, ctxInput); });
        ctxField.append(fieldLabel, ctxInput);
        metaEl.appendChild(ctxField);
      }
    }
    metaEl.appendChild(copyableField(displayName(m), "model id", `Full model id: ${m.id}`, m.id));
    const actions = c.querySelector(".model-card2-actions");
    if (m.resource_warning) {
      const warning = m.resource_warning;
      const box = document.createElement("div");
      box.className = "model-load-warning";
      const label = document.createElement("div");
      label.className = "model-load-warning-label";
      label.textContent = `⚠ ${warning.label}`;
      const message = document.createElement("div");
      message.textContent = warning.summary;
      box.append(label, message);
      const note = document.createElement("details"); note.className = "model-resource-warning";
      const summary = document.createElement("summary"); summary.textContent = `Evidence & review (${(warning.evidence || []).length})`;
      const scope = document.createElement("p"); scope.className = "hint";
      scope.textContent = `${warning.scope} Reviewed ${warning.reviewed_at}.`;
      const evidence = document.createElement("ul");
      for (const item of warning.evidence || []) {
        const entry = document.createElement("li");
        const source = document.createElement("strong");
        source.textContent = `${new Date(item.observed_at).toLocaleString()} · ${item.source}`;
        const excerpt = document.createElement("p"); excerpt.textContent = item.excerpt;
        entry.append(source, excerpt); evidence.appendChild(entry);
      }
      note.append(summary, scope, evidence); box.appendChild(note);
      actions.appendChild(box);
    }
    if (m.load_status?.state === "error") {
      const box = document.createElement("div");
      box.className = "model-load-warning model-load-error";
      const label = document.createElement("div");
      label.className = "model-load-warning-label";
      label.textContent = "⚠ Latest load failed";
      const message = document.createElement("div");
      message.textContent = m.load_status.error || "The model could not be loaded.";
      const copyBtn = document.createElement("button");
      copyBtn.className = "panel-btn";
      copyBtn.type = "button";
      copyBtn.textContent = "Copy error";
      copyBtn.style.marginTop = "4px";
      copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(m.load_status.error || "The model could not be loaded.").then(() => {
          copyBtn.textContent = "Copied!";
          setTimeout(() => { copyBtn.textContent = "Copy error"; }, 1500);
        }).catch(() => {
          copyBtn.textContent = "Copy failed";
        });
      });
      box.append(label, message, copyBtn);
      actions.appendChild(box);
    }
    if (m.backend === "opencode") {
      const note = document.createElement("span");
      note.className = "hint";
      note.textContent = "Hosted by opencode.ai — select it in Chat to use it. No local load or delete is needed.";
      actions.appendChild(note);
      return c;
    }
    if (loaded) {
      actions.appendChild(btn("Unload", "", () => unloadModel(m.id), isBusy));
    } else {
      actions.appendChild(btn(stopped ? "Start & load" : "Load", "primary", () => loadModel(m.id), isBusy));
    }
    if (m.deletable !== false) {
      actions.appendChild(btn("Delete", "danger", () => deleteModel(m.id), isBusy));
    }
    if (m.backend === "llamacpp" && m.type && !NON_CHAT.includes(m.type) && m.type !== "embedding") {
      const thinkingLabel = document.createElement("label");
      thinkingLabel.className = "model-thinking-toggle";
      const thinkingInput = document.createElement("input");
      thinkingInput.type = "checkbox";
      thinkingInput.checked = m.enable_thinking !== false;
      thinkingInput.disabled = isBusy || window.llamaWebModelTests?.isRunning();
      thinkingInput.setAttribute("aria-label", `Enable thinking for ${m.id}`);
      thinkingInput.addEventListener("change", () => setModelThinking(m, thinkingInput));
      thinkingLabel.append(thinkingInput, document.createTextNode(" Enable thinking"));
      actions.appendChild(thinkingLabel);
    }
    if (!unavailableGuard(m) && m.testable !== false && !NON_CHAT.includes(m.type) && m.type !== "embedding") {
      actions.appendChild(btn("Test Model", "", () => {
        window.location.assign(`/model-tests?backend=${encodeURIComponent(m.backend)}&model=${encodeURIComponent(m.id)}&action=test-model`);
      }, isBusy));
      actions.appendChild(btn("Reset repair", "", () => {
        window.location.assign(`/model-tests?backend=${encodeURIComponent(m.backend)}&model=${encodeURIComponent(m.id)}&action=reset`);
      }, isBusy));
    }
    return c;
  }

  function unavailableGuard(m) {
    return m.loadable === false || m.management_hint;
  }

  async function setModelThinking(model, input) {
    input.disabled = true;
    try {
      const res = await fetch("/api/models/thinking", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend: model.backend, model: model.id, enabled: input.checked }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "could not update model thinking");
      model.enable_thinking = data.enable_thinking;
      toast(`${data.enable_thinking ? "Enabled" : "Disabled"} thinking for ${model.id}`);
    } catch (err) {
      input.checked = model.enable_thinking !== false;
      toast(`Could not update thinking for ${model.id}: ${err.message}`, true);
    } finally {
      input.disabled = false;
    }
  }

  async function updateContextLength(model, input) {
    const value = parseInt(input.value, 10);
    const max = Number(model.max_context_length);
    if (!Number.isSafeInteger(value) || value < 512) {
      input.value = String(model.configured_context_length || model.loaded_context_length || model.context_window_tokens);
      toast("Context size must be at least 512 tokens.", true);
      return;
    }
    if (Number.isSafeInteger(max) && max > 0 && value > max) {
      input.value = String(model.configured_context_length || model.loaded_context_length || model.context_window_tokens);
      toast(`${model.id} supports at most ${max.toLocaleString()} context tokens.`, true);
      return;
    }
    input.disabled = true;
    try {
      const res = await fetch("/api/models/context", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend: model.backend, model: model.id, context_length: value }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "could not update context length");
      model.configured_context_length = data.context_length;
      if (data.reloaded) model.loaded_context_length = data.context_length;
      model.context_window_tokens = data.context_length;
      model.context_window_source = "configured";
      toast(`${data.reloaded ? "Reloaded" : "Saved"} ${model.id} at ${value.toLocaleString()} context tokens`);
      if (data.reloaded) fetchModels();
    } catch (err) {
      input.value = String(model.configured_context_length || model.loaded_context_length || model.context_window_tokens);
      toast(`Could not update context size for ${model.id}: ${err.message}`, true);
    } finally {
      input.disabled = false;
    }
  }

  function btn(label, cls, onClick, disabled) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "model-mini-btn" + (cls ? " " + cls : "");
    b.textContent = label;
    if (disabled || window.llamaWebModelTests?.isRunning()) b.disabled = true;
    b.addEventListener("click", onClick);
    return b;
  }

  function renderGroups() {
    const q = (searchEl.value || "").trim().toLowerCase();
    groupsEl.innerHTML = "";
    const ordered = [...GROUPS, ...[...new Set(models.map((m) => m.backend))]
      .filter((k) => !LABEL[k]).map((k) => ({ key: k, label: k || "Other" }))];
    const activeModels = models.filter((m) => activeView === "online"
      ? m.backend === "opencode" : m.backend !== "opencode");
    const matching = activeModels.filter((m) =>
      (!q || (m.id || "").toLowerCase().includes(q)) &&
      (!warningsOnlyEl?.checked || m.resource_warning || m.load_status?.state === "error"));
    const isLoadedLocal = (m) => activeView === "local" && m.state === "loaded";
    const remaining = matching.filter((m) => !isLoadedLocal(m));
    const sections = [
      ...(activeView === "local" ? [{ label: "Loaded models", list: matching.filter(isLoadedLocal) }] : []),
      ...ordered.map((g) => ({ label: g.label, list: remaining.filter((m) => m.backend === g.key) })),
    ];
    for (const { label, list } of sections) {
      if (!list.length) continue;
      const section = document.createElement("div");
      section.className = "console-card span-all model-group";
      section.innerHTML = `<h3>${esc(label)} <span class="hint">· ${list.length}</span></h3>`;
      const grid = document.createElement("div");
      grid.className = "model-card2-grid";
      list.forEach((m) => grid.appendChild(card(m)));
      section.appendChild(grid);
      groupsEl.appendChild(section);
    }
    if (!matching.length) {
      groupsEl.innerHTML = warningsOnlyEl?.checked
        ? `<p class="hint">No models with recorded resource warnings match this filter.</p>` : q
        ? `<p class="hint">No models match “${esc(q)}”.</p>`
        : (activeView === "online"
          ? `<p class="hint">No online models are available. Configure an opencode.ai API key in <a href="/settings">Settings</a> first.</p>`
          : `<p class="hint">No llama.cpp models are available. Upload a GGUF above or get one from the <a href="/downloads">Downloads page</a>.</p>`);
    }
  }

  warningsOnlyEl?.addEventListener("change", renderGroups);
  viewTabs.forEach((tab) => tab.addEventListener("click", () => {
    activeView = tab.dataset.modelView;
    viewTabs.forEach((item) => {
      const selected = item === tab;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-selected", String(selected));
    });
    localToolsEl.hidden = activeView !== "local";
    toolbarEl.hidden = activeView === "online";
    document.querySelector(".model-resource-hint").hidden = activeView === "online";
    renderGroups();
  }));

  maxContextBtn?.addEventListener("click", async () => {
    if (!confirm("Save every chat model's maximum reported context as its configured ctx-size? Loaded models apply it on their next Load.")) return;
    maxContextBtn.disabled = true;
    try {
      const res = await fetch("/api/models/context/max", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "could not update model contexts");
      const count = (data.updated || []).length;
      const skipped = (data.skipped || []).length;
      toast(`Saved max context for ${count} model${count === 1 ? "" : "s"}` + (skipped ? `; ${skipped} had no reported maximum.` : "."));
      fetchModels();
    } catch (err) {
      toast(`Could not set max contexts: ${err.message}`, true);
    } finally {
      maxContextBtn.disabled = false;
    }
  });

  // A slow cold start looked exactly like a failed load when the old code showed one static
  // toast that auto-hid after 4s and
  // never touched the card again until the whole request finished — nothing on the page visibly
  // changed for the rest of the wait, so a perfectly successful load looked hung/broken. Now the
  // toast persists and ticks up every second, and the specific card shows a "Loading…" badge with
  // its buttons disabled, for the entire duration of the operation.
  function startBusyOp(id, label, baseMsg) {
    busy = true;
    busyModelId = id;
    busyLabel = label;
    renderGroups();
    const t0 = Date.now();
    toast(`${baseMsg} (0s elapsed)`, false, true);
    const timer = setInterval(() => {
      toast(`${baseMsg} (${Math.round((Date.now() - t0) / 1000)}s elapsed)`, false, true);
    }, 1000);
    return () => clearInterval(timer);
  }
  function endBusyOp(stopTicker) {
    stopTicker();
    busy = false;
    busyModelId = null;
    busyLabel = "";
  }

  async function loadModel(modelKey) {
    if (busy || window.llamaWebModelTests?.isRunning()) return;
    const target = models.find((m) => m.id === modelKey)?.backend;
    const supportTypes = new Set(["embeddings", "embedding", "transcription", "tts"]);
    const other = models.filter(
      (m) => m.state === "loaded" && m.id !== modelKey
        && m.externally_managed !== true
        && !supportTypes.has(m.type)
    );
    const serviceIsDown = ON_DEMAND_BACKENDS.has(target)
      && !models.some((m) => m.backend === target && m.state === "loaded");

    const lines = [`Load ${modelKey}?`];
    if (other.length) {
      lines.push(`The currently loaded chat model will be unloaded: ${other.map((m) => m.id).join(", ")}.`);
    }
    if (serviceIsDown) {
      lines.push(`${LABEL[target] || target}'s service is stopped, so this will start it from cold — that can take a few minutes for a large GGUF.`);
    }
    if (lines.length > 1 && !confirm(lines.join("\n\n"))) return;

    const stopTicker = startBusyOp(modelKey, "Loading", `Loading ${modelKey}… (cold starts can take a few minutes)`);
    try {
      const res = await fetch("/api/models/load", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelKey,
          identifier: el("opt-identifier").value.trim() || undefined,
          context_length: el("opt-context").value ? Number(el("opt-context").value) : undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "load failed");
      toast(data.unloaded?.length ? `Loaded ${modelKey} (unloaded ${data.unloaded.join(", ")})` : `Loaded ${modelKey}`);
    } catch (err) {
      toast(`Failed to load ${modelKey}: ${err.message}`, true);
    }
    endBusyOp(stopTicker);
    fetchModels();
  }

  async function unloadModel(id) {
    if (busy || window.llamaWebModelTests?.isRunning()) return;
    const target = models.find((m) => m.id === id)?.backend;
    const lines = [`Unload ${id}?`];
    if (ON_DEMAND_BACKENDS.has(target)) {
      lines.push(`This stops ${LABEL[target] || target}'s service entirely. Loading a model here again will be a cold start.`);
    }
    if (!confirm(lines.join("\n\n"))) return;

    const stopTicker = startBusyOp(id, "Unloading", `Unloading ${id}…`);
    const controller = new AbortController();
    const requestTimeout = setTimeout(() => controller.abort(), 75000);
    try {
      const res = await fetch("/api/models/unload", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: id }), signal: controller.signal,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "unload failed");
      toast(`Stopped ${id} — its llama.cpp service is no longer running.`);
    } catch (err) {
      const detail = err.name === "AbortError"
        ? "request timed out; the backend may still be finishing an active request"
        : err.message;
      toast(`Failed to unload ${id}: ${detail}`, true);
    } finally {
      clearTimeout(requestTimeout);
    }
    endBusyOp(stopTicker);
    fetchModels();
  }

  async function deleteModel(id) {
    if (busy || window.llamaWebModelTests?.isRunning()) return;
    const m = models.find((mm) => mm.id === id);
    const lines = [`Permanently delete "${id}" from disk? Frees storage (and memory if loaded), but you'd need to re-download it.`];
    if (m?.state === "loaded" && ON_DEMAND_BACKENDS.has(m.backend)) {
      lines.push(`It's currently loaded, so this also stops ${LABEL[m.backend] || m.backend}'s service first.`);
    }
    if (!confirm(lines.join("\n\n"))) return;
    const stopTicker = startBusyOp(id, "Deleting", `Deleting ${id}…`);
    try {
      const res = await fetch("/api/models/delete", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identifier: id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "delete failed");
      toast(`Deleted ${id}`);
    } catch (err) { toast(`Failed to delete ${id}: ${err.message}`, true); }
    endBusyOp(stopTicker);
    fetchModels();
  }

  el("refresh").addEventListener("click", fetchModels);
  searchEl.addEventListener("input", renderGroups);
  fetchModels();
  setInterval(() => {
    if (document.visibilityState === "visible" && !isEditingContext && !busy) fetchModels();
  }, 5000);
})();
