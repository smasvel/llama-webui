(() => {

  const BACKEND_ORDER = ["llamacpp", "opencode"];
  const BACKEND_LABELS = {
    llamacpp: "llama.cpp engine",
    opencode: "opencode.ai (remote)",
  };
  const settingsForm = document.getElementById("settings-form");
  const restartEngineBtn = document.getElementById("restart-engine");
  const restartHearthBtn = document.getElementById("restart-llama-web");
  const restartResults = document.getElementById("restart-results");
  const restartStatusEl = document.getElementById("restart-status");
  const engineStatusEl = document.getElementById("engine-status");

  async function refreshEngineStatus() {
    if (!engineStatusEl) return;
    try {
      const res = await fetch("/api/models");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "status unavailable");
      const loaded = (data.models || []).filter((m) => m.backend === "llamacpp" && m.state === "loaded");
      engineStatusEl.textContent = loaded.length
        ? `llama.cpp engine: serving ${loaded.map((m) => m.id).join(", ")}`
        : "llama.cpp engine: no model loaded";
    } catch (err) {
      engineStatusEl.textContent = `llama.cpp engine status unavailable: ${err.message}`;
    }
  }
  refreshEngineStatus();
  setInterval(refreshEngineStatus, 10000);

  async function pollRestartJob(jobId) {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/admin/restart-status/${jobId}`);
        const data = await res.json();
        if (restartStatusEl) {
          restartStatusEl.textContent = `${data.status}: ${data.progress}/${data.total} — ${data.detail || ""}`;
        }
        if (data.status === "done" || data.status === "error") {
          clearInterval(interval);
          restartEngineBtn.disabled = false;
          restartHearthBtn.disabled = false;
        }
      } catch (e) {
        clearInterval(interval);
        restartEngineBtn.disabled = false;
        restartHearthBtn.disabled = false;
      }
    }, 1000);
  }

  async function adminAction(url, label) {
    if (!window.confirm(`${label}? This may interrupt active requests.`)) return;
    restartResults.textContent = `${label}…`;
    restartEngineBtn.disabled = true;
    restartHearthBtn.disabled = true;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
        },
        body: "{}",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || data.error || "restart failed");
      restartResults.textContent = data.message || "Restart requested.";
      if (data.job_id) {
        pollRestartJob(data.job_id);
      } else {
        restartEngineBtn.disabled = false;
        restartHearthBtn.disabled = false;
      }
      setTimeout(refreshEngineStatus, 4000);
    } catch (error) {
      restartResults.textContent = `Could not complete ${label}: ${error.message}`;
      restartEngineBtn.disabled = false;
      restartHearthBtn.disabled = false;
    }
  }
  restartEngineBtn?.addEventListener("click", () => adminAction("/api/admin/restart-engine", "Restart llama.cpp engine"));
  restartHearthBtn?.addEventListener("click", () => adminAction("/api/admin/restart-llama-web", "Restart llama-web"));
  const settingsOpencodeUrlEl = document.getElementById("settings-opencode-url");
  const settingsOpencodePlanEl = document.getElementById("settings-opencode-plan");
  const settingsOpencodeKeyEl = document.getElementById("settings-opencode-key");


  const settingsDefaultModelEl = document.getElementById("settings-default-model");
  const settingsWebSearchEnabledEl = document.getElementById("settings-web-search-enabled");
  const settingsWebSearchModeEl = document.getElementById("settings-web-search-mode");
  const settingsWebSearchMaxResultsEl = document.getElementById("settings-web-search-max-results");
  const settingsWebSearchMaxPagesEl = document.getElementById("settings-web-search-max-pages");
  const settingsWebSearchDirectUrlsEl = document.getElementById("settings-web-search-direct-urls");
  const settingsWebSearchBrowserEnabledEl = document.getElementById("settings-web-search-browser-enabled");
  const settingsCaddyInfoEl = document.getElementById("settings-caddy-info");
  const settingsSaveBtn = document.getElementById("settings-save-btn");
  const settingsTestOpencodeBtn = document.getElementById("settings-test-opencode");
  const opencodePlanDefaults = {
    payg: "https://opencode.ai/inference/openai",
    go: "https://opencode.ai/inference/go/openai",
  };
  function opencodeOverrideToSend() {
    // A prefilled plan-derived default is not an override — sending it back would
    // pin that single product endpoint forever (observed: a Go subscriber's plan
    // select was silently neutralized by the auto-filled URL). Only a genuinely
    // custom (advanced) base URL is sent.
    const value = settingsOpencodeUrlEl.value.trim();
    const known = Object.values(opencodePlanDefaults);
    return value && !known.includes(value) ? value : "";
  }
  const settingsTestOpencodeResultEl = document.getElementById("settings-test-opencode-result");
  const opencodeClearKeyBtn = document.getElementById("opencode-clear-key");
  const settingsTestCaddyResultEl = document.getElementById("settings-test-caddy-result");
  const settingsTailscaleDetectedEl = document.getElementById("settings-tailscale-detected");
  const settingsTailscaleEnabledEl = document.getElementById("settings-tailscale-enabled");
  const settingsTailscaleIpOverrideEl = document.getElementById("settings-tailscale-ip-override");
  const settingsTestTailscaleResultEl = document.getElementById("settings-test-tailscale-result");
  const settingsChatDefaultMaxTokensEl = document.getElementById("settings-chat-default-max-tokens");
  const settingsChatMaxMaxTokensEl = document.getElementById("settings-chat-max-max-tokens");
  const settingsChatMaxMessagesEl = document.getElementById("settings-chat-max-messages");
  const settingsChatMaxMessageCharsEl = document.getElementById("settings-chat-max-message-chars");
  const settingsChatMaxToolRoundsEl = document.getElementById("settings-chat-max-tool-rounds");
  const settingsOpencodeCompactionKeepTokensEl = document.getElementById("settings-opencode-compaction-keep-tokens");
  const settingsOpencodeCompactionBufferEl = document.getElementById("settings-opencode-compaction-buffer");
  const settingsOpencodeCompactionAutoEl = document.getElementById("settings-opencode-compaction-auto");
  const settingsOpencodeAdvertiseLoadedContextEl = document.getElementById("settings-opencode-advertise-loaded-context");
  const settingsOpencodeContextFallbackEl = document.getElementById("settings-opencode-context-fallback");

  // An empty field means "clear the override, fall back to the built-in default" — send null
  // rather than Number("") === 0, which the server would reject as out of range.
  const numOrNull = (el) => (el.value.trim() === "" ? null : Number(el.value));

  let models = [];
  let savedDefaultModel = ""; // "backend/model_id", from Settings' lan_default_model ("" = auto)

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function addSystemMessage(text, isError) {
    // No chat pane on this page — surface feedback the same lightweight toast way the
    // Users page does.
    const el = document.createElement("div");
    el.className = `users-toast ${isError ? "test-result-fail" : "test-result-ok"}`;
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // Populates the "Default LAN model" dropdown from the live model list. Re-applies
  // savedDefaultModel after rebuilding the options so a model-list refresh doesn't reset
  // the admin's saved choice back to "Auto".
  function renderDefaultModelSelect() {
    const chatModels = models.filter((m) =>
      m.loadable !== false && m.lan_compatible !== false
      && !["embeddings", "transcription", "tts"].includes(m.type));
    settingsDefaultModelEl.innerHTML = '<option value="">Auto (recommended)</option>';
    const backends = [...BACKEND_ORDER, ...chatModels.map((m) => m.backend)]
      .filter((backend, index, all) => backend && all.indexOf(backend) === index);
    for (const backend of backends) {
      const backendModels = chatModels.filter((m) => m.backend === backend);
      if (backendModels.length === 0) continue;
      const group = document.createElement("optgroup");
      group.label = BACKEND_LABELS[backend] || backend;
      for (const m of backendModels) {
        const opt = document.createElement("option");
        opt.value = `${m.backend}/${m.id}`;
        // `quantization` is normalized by each client to the actual compute path when
        // known (NPU, GPU, CPU). This prevents an "-NPU" marketing/model name from being
        // mistaken for NPU execution when Lemonade reports a llamacpp/GPU recipe.
        const quantization = String(m.quantization || m.compatibility_type || "").toLowerCase();
        const compute = quantization.includes("npu") ? "NPU"
          : quantization.includes("gpu") || quantization.includes("vulkan") ? "GPU" : "GGUF";
        const maxContext = Number(m.max_context_length || m.context_window_tokens || 0);
        const contextLabel = maxContext > 0
          ? ` · ${maxContext >= 1_000_000 ? (maxContext / 1_000_000).toFixed(1) + "M" : Math.round(maxContext / 1024) + "K"} ctx`
          : "";
        opt.textContent = `${m.name || m.id} — ${compute}${contextLabel}${m.state === "loaded" ? " · loaded" : ""}`;
        group.appendChild(opt);
      }
      settingsDefaultModelEl.appendChild(group);
    }
    // Keep the saved choice selected even if it's not in the current list (e.g. a backend
    // that's temporarily unreachable) - falls back to "Auto" display only if truly absent
    // from the dropdown's own options, matching a native <select>'s own behavior.
    settingsDefaultModelEl.value = savedDefaultModel;
  }

  async function fetchModelsForDefault() {
    try {
      const res = await fetch("/api/models");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "failed to list models");
      models = data.models || [];
      renderDefaultModelSelect();
    } catch {
      // Non-fatal for this page - the dropdown just stays at whatever it already had
      // (or "Auto" only, on first load), same fail-soft contract as the rest of this app.
    }
  }

  function applySettings(data) {
    settingsOpencodePlanEl.value = data.opencode_api_plan === "payg" ? "payg" : "go";
    settingsOpencodeUrlEl.value = data.opencode_api_base_url || opencodePlanDefaults[settingsOpencodePlanEl.value];
    settingsOpencodeKeyEl.value = data.opencode_api_key || "";


    savedDefaultModel = data.lan_default_model || "";
    renderDefaultModelSelect();
    settingsWebSearchEnabledEl.checked = data.web_search_enabled !== false;
    settingsWebSearchModeEl.value = data.web_search_mode || "automatic";
    settingsWebSearchMaxResultsEl.value = data.web_search_max_results ?? 6;
    settingsWebSearchMaxPagesEl.value = data.web_search_max_pages ?? 100;
    settingsWebSearchDirectUrlsEl.checked = data.web_search_direct_urls !== false;
    settingsWebSearchBrowserEnabledEl.checked = data.web_search_browser_enabled !== false;
    updateWebSearchControls();

    settingsChatDefaultMaxTokensEl.value = data.chat_default_max_tokens ?? 32768;
    settingsChatMaxMaxTokensEl.value = data.chat_max_max_tokens ?? 32768;
    settingsChatMaxMessagesEl.value = data.chat_max_messages ?? 500;
    settingsChatMaxMessageCharsEl.value = data.chat_max_message_chars ?? 2000000;
    settingsChatMaxToolRoundsEl.value = data.chat_max_tool_rounds ?? 100;
    settingsOpencodeCompactionKeepTokensEl.value = data.opencode_compaction_keep_tokens ?? "";
    settingsOpencodeCompactionBufferEl.value = data.opencode_compaction_buffer ?? "";
    settingsOpencodeCompactionAutoEl.checked = data.opencode_compaction_auto === true;
    settingsOpencodeAdvertiseLoadedContextEl.checked = data.opencode_advertise_loaded_context ?? true;
    settingsOpencodeContextFallbackEl.value = data.opencode_context_fallback ?? "";


    settingsTailscaleEnabledEl.checked = data.tailscale_enabled !== false;
    settingsTailscaleIpOverrideEl.value = data.tailscale_ip_override || "";
    settingsTailscaleDetectedEl.textContent = data.tailscale_detected_ip
      ? `Auto-detected Tailscale IP: ${data.tailscale_detected_ip}`
      : "No Tailscale IP auto-detected on this machine (tailscale not installed, logged out, or unreachable) — set a manual override below if you know it.";

    if (!data.caddy) {
      settingsCaddyInfoEl.textContent = "Caddyfile not found or not readable — Caddy may not be set up on this machine.";
    } else {
      const lines = [];
      if (data.caddy.bearer_token) lines.push(`Bearer token: ${data.caddy.bearer_token}`);
      settingsCaddyInfoEl.innerHTML = lines.length
        ? lines.map((l) => escapeHtml(l)).join("<br>")
        : "Caddyfile found; LAN access proxies to the llama-web app.";
    }
  }

  function updateWebSearchControls() {
    const enabled = settingsWebSearchEnabledEl.checked;
    settingsWebSearchModeEl.disabled = !enabled;
    settingsWebSearchMaxResultsEl.disabled = !enabled;
    settingsWebSearchBrowserEnabledEl.disabled = !enabled;
    const browserEnabled = enabled && settingsWebSearchBrowserEnabledEl.checked;
    settingsWebSearchMaxPagesEl.disabled = !browserEnabled;
    settingsWebSearchDirectUrlsEl.disabled = !browserEnabled;
  }

  settingsWebSearchEnabledEl.addEventListener("change", updateWebSearchControls);
  settingsWebSearchBrowserEnabledEl.addEventListener("change", updateWebSearchControls);

  async function fetchSettings() {
    try {
      const res = await fetch("/api/settings");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "failed to load settings");
      applySettings(data);
    } catch (err) {
      addSystemMessage(`Failed to load settings: ${err.message}`, true);
    }
  }

  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    settingsSaveBtn.disabled = true;
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          opencode_api_plan: settingsOpencodePlanEl.value,
          opencode_api_base_url: opencodeOverrideToSend(),
          // only send a non-empty key; sending the empty field must not erase
          // a stored credential (explicit removal uses opencode_api_clear)
          ...(settingsOpencodeKeyEl.value.trim() ? {opencode_api_key: settingsOpencodeKeyEl.value.trim()} : {}),

          lan_default_model: settingsDefaultModelEl.value,
          chat_default_max_tokens: numOrNull(settingsChatDefaultMaxTokensEl),
          chat_max_max_tokens: numOrNull(settingsChatMaxMaxTokensEl),
          chat_max_messages: numOrNull(settingsChatMaxMessagesEl),
          chat_max_message_chars: numOrNull(settingsChatMaxMessageCharsEl),
          chat_max_tool_rounds: numOrNull(settingsChatMaxToolRoundsEl),
          opencode_compaction_keep_tokens: numOrNull(settingsOpencodeCompactionKeepTokensEl),
          opencode_compaction_buffer: numOrNull(settingsOpencodeCompactionBufferEl),
          opencode_compaction_auto: settingsOpencodeCompactionAutoEl.checked,
          opencode_advertise_loaded_context: settingsOpencodeAdvertiseLoadedContextEl.checked,
          opencode_context_fallback: numOrNull(settingsOpencodeContextFallbackEl),
          web_search_enabled: settingsWebSearchEnabledEl.checked,
          web_search_mode: settingsWebSearchModeEl.value,
          web_search_max_results: Number(settingsWebSearchMaxResultsEl.value),
          web_search_max_pages: Number(settingsWebSearchMaxPagesEl.value),
          web_search_direct_urls: settingsWebSearchDirectUrlsEl.checked,
          web_search_browser_enabled: settingsWebSearchBrowserEnabledEl.checked,
          tailscale_enabled: settingsTailscaleEnabledEl.checked,
          tailscale_ip_override: settingsTailscaleIpOverrideEl.value.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "failed to save settings");
      applySettings(data);
      addSystemMessage("Settings saved.");
      await fetchModelsForDefault();
    } catch (err) {
      addSystemMessage(`Failed to save settings: ${err.message}`, true);
    } finally {
      settingsSaveBtn.disabled = false;
    }
  });

  async function runTest(btn, resultEl, target, extra) {
    btn.disabled = true;
    resultEl.textContent = "Testing…";
    resultEl.className = "hint";
    try {
      const res = await fetch("/api/settings/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target, ...extra }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "test failed");
      resultEl.textContent = data.message;
      resultEl.className = `hint ${data.ok ? "test-result-ok" : "test-result-fail"}`;
    } catch (err) {
      resultEl.textContent = err.message;
      resultEl.className = "hint test-result-fail";
    } finally {
      btn.disabled = false;
    }
  }


  const settingsTestLlamacppBtn = document.getElementById("settings-test-llamacpp");
  const settingsTestLlamacppResultEl = document.getElementById("settings-test-llamacpp-result");
  settingsTestLlamacppBtn.addEventListener("click", () =>
    runTest(settingsTestLlamacppBtn, settingsTestLlamacppResultEl, "llamacpp", {})
  );
  const settingsTestCaddyLlamacppBtn = document.getElementById("settings-test-caddy-llamacpp");
  const settingsTestCaddyLlamacppResultEl = document.getElementById("settings-test-caddy-llamacpp-result");
  const settingsTestTailscaleLlamacppBtn = document.getElementById("settings-test-tailscale-llamacpp");
  const settingsTestTailscaleLlamacppResultEl = document.getElementById("settings-test-tailscale-llamacpp-result");
  settingsTestCaddyLlamacppBtn.addEventListener("click", () =>
    runTest(settingsTestCaddyLlamacppBtn, settingsTestCaddyLlamacppResultEl, "caddy_llamacpp", {})
  );
  settingsTestTailscaleLlamacppBtn.addEventListener("click", () =>
    runTest(settingsTestTailscaleLlamacppBtn, settingsTestTailscaleLlamacppResultEl, "tailscale_llamacpp", {})
  );
  settingsTestOpencodeBtn.addEventListener("click", async () => {
    // opencode.ai is configured by the key alone, so testing is configuring:
    // persist what the admin typed (the backend stores it encrypted) and then
    // verify against the live model list. Without this, a successful test left
    // the key unsaved and every picker silently stayed Console-empty.
    const btn = settingsTestOpencodeBtn;
    btn.disabled = true;
    settingsTestOpencodeResultEl.textContent = "Saving plan and key…";
    settingsTestOpencodeResultEl.className = "hint";
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          opencode_api_base_url: settingsOpencodeUrlEl.value.trim(),
          opencode_api_plan: settingsOpencodePlanEl.value,
          opencode_api_key: settingsOpencodeKeyEl.value.trim(),
        }),
      });
      if (!res.ok) {
        const fail = await res.json().catch(() => ({}));
        throw new Error(fail.error || `Saving failed (${res.status})`);
      }
    } catch (err) {
      settingsTestOpencodeResultEl.textContent = err.message;
      settingsTestOpencodeResultEl.className = "hint test-result-fail";
      btn.disabled = false;
      return;
    }
    try {
      await runTest(btn, settingsTestOpencodeResultEl, "opencode", {
        base_url: opencodeOverrideToSend(),
        plan: settingsOpencodePlanEl.value,
        api_key: settingsOpencodeKeyEl.value.trim(),
      });
    } finally {
      btn.disabled = false;
    }
  });
  opencodeClearKeyBtn.addEventListener("click", async () => {
    opencodeClearKeyBtn.disabled = true;
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ opencode_api_clear: true }),
      });
      if (!res.ok) {
        const fail = await res.json().catch(() => ({}));
        throw new Error(fail.error || `Removal failed (${res.status})`);
      }
      settingsOpencodeKeyEl.value = "";
      settingsTestOpencodeResultEl.textContent = "Stored opencode.ai key removed; opencode.ai models disappear immediately.";
      settingsTestOpencodeResultEl.className = "hint";
    } catch (err) {
      settingsTestOpencodeResultEl.textContent = err.message;
      settingsTestOpencodeResultEl.className = "hint test-result-fail";
    } finally {
      opencodeClearKeyBtn.disabled = false;
    }
  });

  fetchSettings();
  fetchModelsForDefault();
})();
