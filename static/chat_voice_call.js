/* Voice call launched from the chat composer's existing phone button.
   The avatar floats above the chat window and can be dragged anywhere. */
(() => {
  const button = document.getElementById("conversation-btn");
  if (!button) return;

  let widget = null;
  let stream = null;
  let audioCtx = null;
  let micAnalyser = null;
  let recorder = null;
  let chunks = [];
  let recording = false;
  let busy = false;
  let cooldownUntil = 0;
  let recordTimer = null;
  let speaking = false;
  let source = null;
  let controller = null;
  let askTimeout = null;
  let vadRaf = null;
  let emotion = "neutral";
  let mouth = 0.08;
  let blink = 0;
  let history = [];
  let speechRms = 0;

  // A person pauses between words and sentences. Never stop on the first quiet
  // frame: require a sustained pause after speech has actually started.
  const VAD_SPEECH_RMS = 0.035;
  const VAD_SILENCE_RMS = 0.018;
  const VAD_SILENCE_MS = 900;
  const VAD_MIN_SPEECH_MS = 450;
  const VAD_MAX_UTTERANCE_MS = 15000;
  let speechStartedAt = 0;
  let silenceStartedAt = 0;
  let onsetFrames = 0;
  let bargeInSince = 0;
  let recognition = null;
  let liveTranscript = "";
  let pendingSpeech = "";
  // Text that was actually spoken by the incremental queue. The end-of-turn path
  // uses this to speak anything the streaming path dropped.
  let spokenText = "";
  let speechChain = Promise.resolve();
  let lastSpokenText = "";

  function voiceLog(event, fields = {}) {
    const payload = {
      event,
      conversation_id: window.ChatApp?.getConversationId?.() || "",
      model: document.getElementById("model-select")?.value || "",
      ...fields,
    };
    console.debug("[voice]", event, payload);
    try {
      fetch("/api/voice/log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(() => { /* diagnostics must never break the call */ });
    } catch (_) { /* nothing to do */ }
  }

  function queueSpeak(text) {
    const clean = cleanText(text);
    if (!clean || clean === lastSpokenText) {
      voiceLog("skipped_duplicate", { answer_chars: clean.length, preview: clean });
      return;
    }
    lastSpokenText = clean;
    speechChain = speechChain.then(async () => {
      setStatus("Speaking…");
      try {
        const didSpeak = await speak(clean);
        if (didSpeak) {
          // Only count text whose audio actually completed. Chunks dropped by a
          // retry or a barge-in must be spoken again at the end of the turn.
          spokenText += clean + " ";
          voiceLog("spoken", { spoken_chars: clean.length, preview: clean });
        } else {
          voiceLog("skipped_busy", { answer_chars: clean.length, preview: clean });
        }
      } catch (error) {
        setStatus(`Speech failed: ${error.message || error}`);
        voiceLog("speech_error", { answer_chars: clean.length,
                                  preview: `${error.message || error} | ${clean}` });
      }
      // Small pause between sentences so consecutive clips never sound overlapped.
      await new Promise((resolve) => setTimeout(resolve, 180));
    });
  }

  function discardQueuedSpeech() {
    if (spokenText.trim()) {
      voiceLog("discarded_speech", { spoken_chars: spokenText.trim().length,
                                     preview: spokenText });
    }
    if (source) { try { source.stop(); } catch (_) { /* already stopped */ } }
    speechChain = Promise.resolve();
    spokenText = "";
    pendingSpeech = "";
    window.__voiceSpeakerBusy = false;
  }
  let blinkUntil = 0;
  let nextBlink = Date.now() + 2000;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function buildWidget() {
    const wrap = el("div", "voice-float");
    wrap.style.cssText = "position:fixed;right:24px;bottom:96px;width:300px;z-index:900;" +
      "background:var(--panel,#141b29);border:1px solid var(--border,#334);border-radius:14px;" +
      "box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden;";
    const head = el("div", "voice-float-head");
    head.style.cssText = "display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:move;" +
      "border-bottom:1px solid var(--border,#334);";
    head.appendChild(el("strong", "", "Voice call"));
    const close = el("button", "icon-btn", "×");
    close.type = "button";
    close.setAttribute("aria-label", "End voice call");
    close.title = "End voice call";
    close.style.cssText = "margin-left:auto;width:22px;height:22px;padding:0;line-height:1;";
    close.addEventListener("click", closeCall);
    head.appendChild(close);

    const globe = el("div", "voice-globe");
    globe.innerHTML = `<svg viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" stroke-width="2"/>
      <ellipse cx="50" cy="50" rx="18" ry="42" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <ellipse cx="50" cy="50" rx="34" ry="42" fill="none" stroke="currentColor" stroke-width="1" opacity="0.65"/>
      <ellipse cx="50" cy="50" rx="42" ry="16" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <ellipse cx="50" cy="50" rx="42" ry="31" fill="none" stroke="currentColor" stroke-width="1" opacity="0.65"/>
    </svg>`;
    globe.style.cssText = "display:flex;align-items:center;justify-content:center;height:240px;" +
      "color:#8fd9cf;background:radial-gradient(circle at 50% 45%,#1b3440,#101725 70%);";
    globe.querySelector("svg").style.cssText = "width:170px;height:170px;transform-origin:center;" +
      "animation:voice-globe-spin 12s linear infinite;";
    const status = el("p", "hint", "Connecting…");
    status.style.cssText = "margin:8px 10px 4px;min-height:1.2em;";
    const caption = el("p", "voice-float-caption", "");
    caption.style.cssText = ("margin:0 10px 14px;min-height:2.6em;max-height:7em;"
      + "overflow-y:auto;overflow-wrap:anywhere;font-size:13px;line-height:1.4;");

    wrap.append(head, globe, status, caption);
    document.body.appendChild(wrap);

    let dragging = false;
    let offsetX = 0;
    let offsetY = 0;
    head.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button")) return;
      dragging = true;
      const box = wrap.getBoundingClientRect();
      offsetX = event.clientX - box.left;
      offsetY = event.clientY - box.top;
      head.setPointerCapture(event.pointerId);
    });
    head.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      wrap.style.left = `${event.clientX - offsetX}px`;
      wrap.style.top = `${event.clientY - offsetY}px`;
      wrap.style.right = "auto";
      wrap.style.bottom = "auto";
    });
    head.addEventListener("pointerup", (event) => {
      dragging = false;
      if (head.hasPointerCapture?.(event.pointerId)) head.releasePointerCapture(event.pointerId);
    });
    head.addEventListener("pointercancel", (event) => {
      dragging = false;
      if (head.hasPointerCapture?.(event.pointerId)) head.releasePointerCapture(event.pointerId);
    });

    return { wrap, globe, status, caption };
  }

  function setStatus(text) { if (widget) widget.status.textContent = text; }
  function setCaption(text) { if (widget) widget.caption.textContent = text; }

  // Keep the floating call caption small: only the tail of a long reply is shown
  // and thinking/tool markup is removed before it reaches the screen.
  function shortCaption(text) {
    let value = String(text || "")
      .replace(/<think>[\s\S]*?<\/think>/gi, "")
      .replace(/<\/?think\s*>/gi, "")
      .replace(/🔧\s*\*Calling[^*]*\*/g, "")
      .trim();
    if (value.length > 240) value = "…" + value.slice(-240);
    return value;
  }

  function rms() {
    if (!micAnalyser) return 0;
    const data = new Uint8Array(micAnalyser.fftSize);
    micAnalyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const sample of data) { const v = (sample - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / data.length);
  }

  let globeTimer = null;

  function animateGlobe() {
    if (!widget || !widget.globe) return;
    const svg = widget.globe.querySelector("svg");
    if (!svg) return;
    if (speaking) {
      svg.style.animationDuration = "4s";
      widget.globe.style.filter = "drop-shadow(0 0 14px rgba(143,217,207,0.65))";
    } else if (busy) {
      svg.style.animationDuration = "7s";
      widget.globe.style.filter = "drop-shadow(0 0 8px rgba(143,217,207,0.35))";
    } else {
      svg.style.animationDuration = "16s";
      widget.globe.style.filter = "none";
    }
    if (globeTimer) clearTimeout(globeTimer);
    globeTimer = setTimeout(animateGlobe, 400);
  }

  // Diagnostic hook used by the Playwright voice-call checks.
  window.__voiceCallDebug = () => ({
    globe: !!widget?.globe,
    recording,
    speaking,
    level: micAnalyser ? rms() : null,
    model: document.getElementById("model-select")?.value || "",
  });
  // Test seam: the panel has no buttons; checks submit a completed utterance here.
  window.__voiceCallTest = {
    send: (text) => processUtterance(new Blob([text]), text),
  };

  function cleanText(raw) {
    const match = String(raw || "").match(/^\s*\[emotion:\s*([a-z]+)\]/i);
    if (match) {
      emotion = match[1].toLowerCase();
      return String(raw).replace(match[0], "").trim();
    }
    const value = String(raw || "").toLowerCase();
    emotion = /great|happy|glad|haha/.test(value) ? "happy"
      : /sorry|unfortunately|worried/.test(value) ? "concerned"
      : /wow|surprising|incredible/.test(value) ? "surprised" : "neutral";
    return String(raw || "").trim();
  }

  function readError(data, fallback) {
    return data && data.error ? String(data.error) : fallback;
  }

  function startLiveTranscription() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    liveTranscript = "";
    if (!SpeechRecognition || !navigator.mediaDevices) return;
    try {
      recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = navigator.language || "en-US";
      recognition.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const text = event.results[i][0]?.transcript || "";
          if (event.results[i].isFinal) liveTranscript += `${text} `;
          else interim += text;
        }
        const shown = `${liveTranscript}${interim}`.trim();
        if (shown) setCaption(`You: ${shown}`);
      };
      recognition.onerror = () => { /* server transcription remains the fallback */ };
      recognition.onend = () => {
        if (recording && recognition) {
          try { recognition.start(); } catch (_) { /* browser may block restart */ }
        }
      };
      recognition.start();
    } catch (_) {
      recognition = null;
    }
  }

  function stopLiveTranscription() {
    if (!recognition) return "";
    const finalText = liveTranscript.trim();
    const current = recognition;
    recognition = null;
    current.onend = null;
    try { current.stop(); } catch (_) { /* already stopped */ }
    return finalText;
  }

  async function transcribe(blob) {
    const form = new FormData();
    form.append("audio", blob, "utterance.webm");
    const transcribeController = new AbortController();
    const timer = setTimeout(() => transcribeController.abort(new Error("Transcription timed out.")), 45000);
    try {
      const response = await fetch("/api/voice/transcribe", {
        method: "POST", body: form, signal: transcribeController.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readError(data, `Transcription failed (${response.status}).`));
      if (!data.text) throw new Error("No speech detected — try speaking a little closer.");
      return data.text;
    } finally {
      clearTimeout(timer);
    }
  }

  async function ask(message, modelOverride, onSentence) {
    pendingSpeech = "";
    controller = new AbortController();
    const model = modelOverride || document.getElementById("model-select")?.value || "";
    // Hosted turns can run tool rounds (live lookups) before they answer, so a
    // short budget used to abort a healthy reply and trigger a pointless retry.
    const budget = 90000;
    const timeout = setTimeout(() => controller.abort(new Error("No response from the model.")), budget);
    askTimeout = timeout;
    const conversationId = window.ChatApp?.getConversationId?.() || "";
    // Reuse the real chat transcript as context, like an advanced assistant,
    // instead of only remembering turns spoken during this call.
    let chatHistory = history;
    try {
      if (conversationId) {
        const saved = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`)
          .then((response) => response.json());
        const messages = (saved.messages || []).filter((item) =>
          ["user", "assistant"].includes(item.role) && item.content).slice(-8);
        if (messages.length) {
          chatHistory = messages.map((item) => ({ role: item.role, content: item.content }));
        }
      }
    } catch (_) { /* in-memory history remains the fallback */ }
    const response = await fetch("/api/voice/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message, history: chatHistory, model, conversation_id: conversationId,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readError(data, `Model request failed (${response.status}).`));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((item) => item.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") continue;
        try {
          const event = JSON.parse(payload);
          if (event.error) throw new Error(event.error);
          const choice = (event.choices || [])[0] || {};
          const rawDelta = event.content || (choice.delta?.content || "") || "";
          const delta = rawDelta.replace(/<think>[\s\S]*?<\/think>/gi, "")
                                .replace(/<\/?think\s*>/gi, "")
                                .replace(/🔧\s*\*Calling[^*]*\*/g, "");
          answer += delta;
          setCaption(shortCaption(answer));
          // Speak each complete sentence as soon as it arrives instead of waiting
          // for the whole reply to finish streaming.
          if (onSentence && delta) {
            pendingSpeech += delta;
            const match = pendingSpeech.match(/[.!?](?:\s|$)/);
            if (match) {
              const sentence = pendingSpeech.slice(0, match.index + 1).trim();
              pendingSpeech = pendingSpeech.slice(match.index + 1);
              if (sentence) onSentence(sentence);
            }
          }
        } catch (err) {
          if (!(err instanceof SyntaxError)) throw err;
        }
      }
    }
    clearTimeout(timeout);
    askTimeout = null;
    controller = null;
    return answer;
  }

  async function speak(text) {
    if (window.__voiceSpeakerBusy) return false;
    window.__voiceSpeakerBusy = true;
    let finished = false;
    let resolvePlayback;
    const playbackDone = new Promise((resolve) => { resolvePlayback = resolve; });
    try {
      const response = await fetch("/api/voice/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(readError(data, `Speech failed (${response.status}).`));
      }
      const buffer = await audioCtx.decodeAudioData(await response.arrayBuffer());
      const player = audioCtx.createBufferSource();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      player.buffer = buffer;
      player.connect(analyser);
      analyser.connect(audioCtx.destination);
      source = player;
      // Keep the mic from treating the speaker's own audio as a barge-in. Without
      // this, echo trips the VAD about 600ms into playback and only the first few
      // words of the reply are ever heard.
      cooldownUntil = Date.now() + Math.min(120000, (buffer.duration || 30) * 1000) + 800;
      const finish = () => {
        if (finished) return;
        finished = true;
        if (source === player) { speaking = false; source = null; }
        speechRms = 0;
        window.__voiceSpeakerBusy = false;
        cooldownUntil = Date.now() + 800;
        setStatus("Ready — start speaking");
        resolvePlayback(true);
      };
      player.onended = finish;
      speaking = true;
      player.start();
      const safety = setTimeout(finish,
        Math.min(120000, (buffer.duration || 30) * 1000 + 2000));
      const meter = () => {
        if (source !== player || !speaking) return;
        speechRms = rmsFrom(analyser);
        requestAnimationFrame(meter);
      };
      meter();
      // Wait for the audio to actually finish. Resolving at player.start() let
      // the next queued sentence hit the busy guard and be silently dropped.
      await playbackDone;
      clearTimeout(safety);
      return true;
    } catch (error) {
      if (!finished) {
        finished = true;
        window.__voiceSpeakerBusy = false;
        resolvePlayback(false);
      }
      throw error;
    }
  }

  function rmsFrom(analyser) {
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const sample of data) { const v = (sample - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / data.length);
  }

  function modelLabel(modelId) {
    const options = [...(document.getElementById("model-select")?.options || [])];
    const option = options.find((item) => item.value === modelId);
    if (option) return option.textContent.replace(/\s*\((?:online|local llama\.cpp engine)\)\s*$/, "");
    return String(modelId || "the model").split("/").pop();
  }

  async function processUtterance(blob, liveText) {
    if (busy) return;
    busy = true;
    lastSpokenText = "";
    const turnStarted = performance.now();
    setStatus("Transcribing…");
    try {
      // The browser Web Speech API mishears accented/mixed-language speech and its
      // language is tied to navigator.language. The local Whisper model handles
      // that better, so it is primary; the live transcript is only a fallback.
      let sttSource = "server";
      let userText = "";
      try {
        userText = await transcribe(blob);
      } catch (error) {
        sttSource = "browser-fallback";
        userText = (liveText || "").trim();
        voiceLog("stt_fallback", { preview: String(error.message || error) });
      }
      if (!userText.trim()) {
        setCaption("Didn't catch that — please try again.");
        setStatus("Ready — start speaking");
        return;
      }
      setCaption(shortCaption(`You: ${userText}`));
      setStatus("Thinking…");
      voiceLog("turn_start", { answer_chars: userText.length, preview: userText,
                               stt_source: sttSource });
      let raw = "";
      discardQueuedSpeech();
      const selected = document.getElementById("model-select")?.value || "";
      // Stay on the model the user chose. Give it up to two attempts with a
      // longer timeout rather than silently switching to a different model.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          if (attempt) {
            setStatus(`Retrying with ${modelLabel(selected)}…`);
            discardQueuedSpeech(); // stop and forget speech from the stalled attempt
            voiceLog("retry", { elapsed_ms: Math.round(performance.now() - turnStarted),
                                preview: userText });
          }
          raw = await ask(userText, selected, queueSpeak);
          if (cleanText(raw)) break;
        } catch (err) {
          voiceLog("ask_error", { elapsed_ms: Math.round(performance.now() - turnStarted),
                                  preview: String(err.message || err) });
          raw = "";
        }
      }
      const text = cleanText(raw);
      voiceLog("answer_ready", { elapsed_ms: Math.round(performance.now() - turnStarted),
                                 answer_chars: text.length, preview: text });
      setCaption(shortCaption(text) || "(no reply)");
      // The server persisted both turns in the current chat conversation; show them.
      try {
        await window.ChatApp?.refreshConversations?.();
        const cid = window.ChatApp?.getConversationId?.();
        if (cid) await window.ChatApp?.loadConversation?.(cid);
      } catch (_) { /* voice call still works if the chat view is unavailable */ }
      history.push({ role: "user", content: userText }, { role: "assistant", content: text });
      history = history.slice(-8);
      if (text) {
        if (pendingSpeech.trim()) queueSpeak(pendingSpeech);
        // Wait for everything the streaming path queued, then speak any part of
        // the final answer that was not actually spoken. Chunks can be lost when
        // a retry resets the queue, so this is what guarantees the full reply.
        await speechChain;
        const full = cleanText(text);
        const said = spokenText.trim();
        let remainder = full.trim();
        if (said && remainder.toLowerCase().startsWith(said.toLowerCase())) {
          remainder = remainder.slice(said.length).trim();
        }
        const left = remainder.length;
        voiceLog("coverage", { elapsed_ms: Math.round(performance.now() - turnStarted),
                               answer_chars: full.length, spoken_chars: said.length,
                               left_chars: left, preview: remainder || full });
        if (remainder) {
          setStatus("Speaking…");
          const didSpeak = await speak(remainder);
          voiceLog(didSpeak ? "remainder_spoken" : "remainder_skipped", {
            left_chars: left, preview: remainder,
            elapsed_ms: Math.round(performance.now() - turnStarted),
          });
        }
      }
      voiceLog("turn_done", { elapsed_ms: Math.round(performance.now() - turnStarted) });
      setStatus("Ready — start speaking");
    } catch (err) {
      if (askTimeout) clearTimeout(askTimeout);
      askTimeout = null;
      const message = String(err.message || "");
      voiceLog("turn_error", { elapsed_ms: Math.round(performance.now() - turnStarted),
                               preview: message });
      if (/speech|transcri|no transcript/i.test(message)) {
        setStatus("Ready — start speaking");
        setCaption("I didn't catch that — please say it once more.");
      } else {
        setStatus(`${message} — listening again.`);
        setCaption("No reply that time. Try repeating yourself.");
      }
    } finally {
      busy = false;
      // Give speaker echo time to fade before the mic can start a new turn.
      cooldownUntil = Date.now() + 500;
    }
  }

  function startRecording() {
    if (recording || !stream) return;
    speechStartedAt = Date.now();
    silenceStartedAt = 0;
    onsetFrames = 0;
    try {
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        recording = false;
        const spoken = stopLiveTranscription();
        const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
        if (blob.size) processUtterance(blob, spoken);
      };
      recorder.start();
      recording = true;
      startLiveTranscription();
      recordTimer = setTimeout(stopRecording, VAD_MAX_UTTERANCE_MS);
      setStatus("Listening…");
    } catch (err) {
      setStatus(`Microphone error: ${err.message}`);
    }
  }

  function stopRecording() {
    if (recordTimer) { clearTimeout(recordTimer); recordTimer = null; }
    if (recorder && recording) {
      cooldownUntil = Date.now() + 200;
      recorder.stop();
    }
  }

  function vadLoop() {
    if (!stream || !widget) return;
    const now = Date.now();
    const level = rms();
    if (busy || now < cooldownUntil) { vadRaf = requestAnimationFrame(vadLoop); return; }
    if (speaking) {
      bargeInSince = level > 0.32 ? (bargeInSince || now) : 0;
      if (bargeInSince && now - bargeInSince >= 1000) {
        if (source) { try { source.stop(); } catch (_) {} }
        if (controller) controller.abort();
        speaking = false;
        bargeInSince = 0;
        setStatus("Interrupted — listening…");
      }
    } else {
      bargeInSince = 0;
    }
    if (!speaking && !recording) {
      onsetFrames = level > VAD_SPEECH_RMS ? onsetFrames + 1 : 0;
      if (onsetFrames >= 2) startRecording();
    } else if (recording) {
      if (level >= VAD_SILENCE_RMS) {
        silenceStartedAt = 0;
      } else if (!silenceStartedAt) {
        silenceStartedAt = now;
      }
      const pausedFor = silenceStartedAt ? now - silenceStartedAt : 0;
      const spokeFor = now - speechStartedAt;
      if ((pausedFor >= VAD_SILENCE_MS && spokeFor >= VAD_MIN_SPEECH_MS)
          || spokeFor >= VAD_MAX_UTTERANCE_MS) {
        stopRecording();
      }
    }
    vadRaf = requestAnimationFrame(vadLoop);
  }

  async function openCall() {
    if (widget) return;
    try {
      // Ensure voice turns have a chat conversation to be saved into.
      window.ChatApp?.ensureConversationId?.();
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const input = audioCtx.createMediaStreamSource(stream);
      micAnalyser = audioCtx.createAnalyser();
      micAnalyser.fftSize = 512;
      input.connect(micAnalyser);
      widget = buildWidget();
      window.ChatApp?.setVoiceCallActive?.(true);
      button.setAttribute("aria-pressed", "true");
      setStatus("Ready — start speaking");
      vadLoop();
      animateGlobe();
    } catch (err) {
      if (!widget) widget = buildWidget();
      setStatus(`Microphone unavailable: ${err.message}`);
    }
  }

  function closeCall() {
    if (vadRaf) cancelAnimationFrame(vadRaf);
    if (recordTimer) { clearTimeout(recordTimer); recordTimer = null; }
    if (source) { try { source.stop(); } catch (_) {} }
    if (controller) controller.abort();
    if (recorder && recording) recorder.stop();
    if (stream) stream.getTracks().forEach((track) => track.stop());
    if (audioCtx) audioCtx.close();
    if (widget) widget.wrap.remove();
    widget = null; stream = null; audioCtx = null; micAnalyser = null;
    window.ChatApp?.setVoiceCallActive?.(false);
    button.setAttribute("aria-pressed", "false");
  }

  // Capture phase overrides the older hands-free handler in app.js.
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (widget) closeCall();
    else openCall();
  }, true);
})();
