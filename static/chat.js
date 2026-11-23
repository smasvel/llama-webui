(() => {
  const sidebarEl = document.getElementById("sidebar");
  const sidebarOverlayEl = document.getElementById("sidebar-overlay");
  const menuToggleBtn = document.getElementById("menu-toggle");
  const multilineToggle = document.getElementById("multiline-toggle");
  const composerHintEl = document.getElementById("composer-hint");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  let multilineMode = localStorage.getItem("multilineMode") === "1";

  function openSidebar() {
    sidebarEl.classList.add("open");
    sidebarOverlayEl.classList.add("open");
  }

  function closeSidebar() {
    sidebarEl.classList.remove("open");
    sidebarOverlayEl.classList.remove("open");
  }

  function toggleSidebar() {
    sidebarEl.classList.contains("open") ? closeSidebar() : openSidebar();
  }

  menuToggleBtn.addEventListener("click", toggleSidebar);
  sidebarOverlayEl.addEventListener("click", closeSidebar);

  function updateComposerHint() {
    composerHintEl.textContent = multilineMode
      ? "Enter for new line · Ctrl+Enter (or ⌘+Enter) to send"
      : "Enter to send · Shift+Enter for new line";
  }

  function updateMultilineBtn() {
    multilineToggle.classList.toggle("active", multilineMode);
    multilineToggle.setAttribute("aria-pressed", multilineMode ? "true" : "false");
  }

  updateMultilineBtn();
  updateComposerHint();

  multilineToggle.addEventListener("click", () => {
    multilineMode = !multilineMode;
    localStorage.setItem("multilineMode", multilineMode ? "1" : "0");
    updateMultilineBtn();
    updateComposerHint();
  });

  const insertCodeBtn = document.getElementById("insert-code-btn");
  const codeModalOverlay = document.getElementById("code-modal-overlay");
  const codeModalTextarea = document.getElementById("code-modal-textarea");
  const codeModalLang = document.getElementById("code-modal-lang");
  const codeModalClose = document.getElementById("code-modal-close");
  const codeModalCancel = document.getElementById("code-modal-cancel");
  const codeModalInsert = document.getElementById("code-modal-insert");

  function openCodeModal() {
    codeModalOverlay.classList.remove("hidden");
    codeModalTextarea.value = "";
    codeModalLang.value = "";
    codeModalTextarea.focus();
  }

  function closeCodeModal() {
    codeModalOverlay.classList.add("hidden");
  }

  insertCodeBtn.addEventListener("click", openCodeModal);
  codeModalClose.addEventListener("click", closeCodeModal);
  codeModalCancel.addEventListener("click", closeCodeModal);
  codeModalOverlay.addEventListener("click", (e) => {
    if (e.target === codeModalOverlay) closeCodeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !codeModalOverlay.classList.contains("hidden")) closeCodeModal();
  });

  codeModalInsert.addEventListener("click", () => {
    const code = codeModalTextarea.value;
    if (!code.trim()) {
      closeCodeModal();
      return;
    }
    const fence = "```" + codeModalLang.value + "\n" + code.replace(/\n?$/, "\n") + "```\n";
    const start = chatInput.selectionStart ?? chatInput.value.length;
    const end = chatInput.selectionEnd ?? chatInput.value.length;
    const before = chatInput.value.slice(0, start);
    const after = chatInput.value.slice(end);
    const needsLeadingNewline = before.length > 0 && !before.endsWith("\n");
    chatInput.value = before + (needsLeadingNewline ? "\n" : "") + fence + after;
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 500) + "px";
    closeCodeModal();
    chatInput.focus();
    const caret = before.length + (needsLeadingNewline ? 1 : 0) + fence.length;
    chatInput.setSelectionRange(caret, caret);
  });

  chatInput.addEventListener("input", () => {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 500) + "px";
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const shouldSend = multilineMode ? e.ctrlKey || e.metaKey : !e.shiftKey;
    if (shouldSend) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
    // otherwise let Enter insert a newline as normal
  });

  const messages = document.getElementById("messages");
  messages.scrollTop = messages.scrollHeight;
  const copy = document.getElementById("copy-chat");
  copy.addEventListener("click", async () => {
    const entries = Array.from(messages.querySelectorAll(".msg"), el =>
      (el.classList.contains("user") ? "You: " : "Assistant: ") + el.textContent);
    try {
      await navigator.clipboard.writeText(entries.join("\n\n"));
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Copy unavailable";
    }
    setTimeout(() => { copy.textContent = "Copy chat"; }, 1200);
  });
  chatForm.addEventListener("submit", event => {
    if (chatForm.dataset.pending) { event.preventDefault(); return; }
    chatForm.dataset.pending = "1";
    document.getElementById("send-btn").disabled = true;
    composerHintEl.textContent = "Generating…";
  });
})();
