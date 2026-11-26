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
  let vadRaf = null;
  let emotion = "neutral";
  let mouth = 0.08;
  let history = [];
  let speechRms = 0;
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
    close.style.marginLeft = "auto";
    close.addEventListener("click", closeCall);
    head.appendChild(close);

    const canvas = el("canvas");
    canvas.width = 280;
    canvas.height = 220;
    canvas.style.cssText = "display:block;width:100%;background:#101725;";
    const status = el("p", "hint", "Connecting…");
    status.style.cssText = "margin:8px 10px 4px;min-height:1.2em;";
    const caption = el("p", "voice-float-caption", "");
    caption.style.cssText = "margin:0 10px 8px;min-height:2.6em;font-size:13px;line-height:1.4;";
    const talk = el("button", "panel-btn", "Hold to talk");
    talk.type = "button";
    talk.style.cssText = "margin:0 10px 10px;width:calc(100% - 20px);";
    talk.addEventListener("click", () => recording ? stopRecording() : startRecording());

    wrap.append(head, canvas, status, caption, talk);
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
    head.addEventListener("pointerup", () => { dragging = false; });

    return { wrap, canvas, status, caption, talk };
  }

  function setStatus(text) { if (widget) widget.status.textContent = text; }
  function setCaption(text) { if (widget) widget.caption.textContent = text; }

  function rms() {
    if (!micAnalyser) return 0;
    const data = new Uint8Array(micAnalyser.fftSize);
    micAnalyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const sample of data) { const v = (sample - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / data.length);
  }

  function draw() {
    if (!widget) return;
    const ctx = widget.canvas.getContext("2d");
    const w = widget.canvas.width;
    const h = widget.canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#101725";
    ctx.fillRect(0, 0, w, h);
    const cx = w / 2;
    const cy = h / 2 + 8;
    const target = speaking ? Math.min(0.95, 0.1 + speechRms * 7) : 0.08;
    mouth += (target - mouth) * 0.3;
    ctx.fillStyle = "#d9b8a3";
    ctx.beginPath();
    ctx.ellipse(cx, cy, 86, 104, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#4b3a33";
    ctx.beginPath();
    ctx.ellipse(cx, cy - 72, 88, 38, 0, Math.PI, Math.PI * 2);
    ctx.fill();
    const blinking = Date.now() < blinkUntil;
    if (Date.now() > nextBlink) { blinkUntil = Date.now() + 130; nextBlink = Date.now() + 2000 + Math.random() * 2200; }
    for (const dx of [-34, 34]) {
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.ellipse(cx + dx, cy - 18, 15, blinking ? 1.5 : 12, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#3a2b26";
      ctx.beginPath();
      ctx.arc(cx + dx, cy - 18, 6, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.strokeStyle = "#4b3a33";
    ctx.lineWidth = 4;
    ctx.beginPath();
    const slant = emotion === "concerned" ? 7 : emotion === "happy" ? -3 : 0;
    ctx.moveTo(cx - 50, cy - 42 + slant); ctx.lineTo(cx - 18, cy - 42 - slant);
    ctx.moveTo(cx + 18, cy - 42 - slant); ctx.lineTo(cx + 50, cy - 42 + slant);
    ctx.stroke();
    ctx.fillStyle = "#7c3b3b";
    ctx.beginPath();
    ctx.ellipse(cx, cy + 48, 24, 5 + mouth * 22, 0, 0, Math.PI * 2);
    ctx.fill();
    requestAnimationFrame(draw);
  }

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

  async function transcribe(blob) {
    const form = new FormData();
    form.append("audio", blob, "utterance.webm");
    const response = await fetch("/api/voice/transcribe", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(readError(data, `Transcription failed (${response.status}).`));
    if (!data.text) throw new Error("No transcript returned.");
    return data.text;
  }

  async function ask(message) {
    controller = new AbortController();
    const model = document.getElementById("model-select")?.value || "";
    const response = await fetch("/api/voice/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, model }),
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
          answer += event.content || (choice.delta?.content || "") || "";
          setCaption(answer);
        } catch (err) {
          if (!(err instanceof SyntaxError)) throw err;
        }
      }
    }
    controller = null;
    return answer;
  }

  async function speak(text) {
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
    source = audioCtx.createBufferSource();
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    source.buffer = buffer;
    source.connect(analyser);
    analyser.connect(audioCtx.destination);
    source.onended = () => { speaking = false; source = null; speechRms = 0; };
    source.start();
    speaking = true;
    const meter = () => {
      if (!speaking) return;
      speechRms = rmsFrom(analyser);
      requestAnimationFrame(meter);
    };
    meter();
  }

  function rmsFrom(analyser) {
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const sample of data) { const v = (sample - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / data.length);
  }

  async function processUtterance(blob) {
    if (busy) return;
    busy = true;
    setStatus("Transcribing…");
    try {
      const userText = await transcribe(blob);
      setCaption(`You: ${userText}`);
      setStatus("Thinking…");
      const raw = await ask(userText);
      const text = cleanText(raw);
      setCaption(text || "(no reply)");
      history.push({ role: "user", content: userText }, { role: "assistant", content: text });
      history = history.slice(-8);
      if (text) {
        setStatus("Speaking…");
        await speak(text);
      }
      setStatus("Listening…");
    } catch (err) {
      setStatus(`Error: ${err.message}`);
    } finally {
      busy = false;
      cooldownUntil = Date.now() + 3000;
    }
  }

  function startRecording() {
    if (recording || !stream) return;
    try {
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        recording = false;
        if (widget) widget.talk.textContent = "Hold to talk";
        const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
        if (blob.size) processUtterance(blob);
      };
      recorder.start();
      recording = true;
      recordTimer = setTimeout(stopRecording, 20000);
      if (widget) widget.talk.textContent = "Stop and send";
      setStatus("Recording…");
    } catch (err) {
      setStatus(`Microphone error: ${err.message}`);
    }
  }

  function stopRecording() {
    if (recordTimer) { clearTimeout(recordTimer); recordTimer = null; }
    if (recorder && recording) {
      cooldownUntil = Date.now() + 3000;
      recorder.stop();
    }
  }

  function vadLoop() {
    if (!stream || !widget) return;
    const level = rms();
    if (busy || Date.now() < cooldownUntil) { vadRaf = requestAnimationFrame(vadLoop); return; }
    if (speaking && level > 0.12) {
      if (source) { try { source.stop(); } catch (_) {} }
      if (controller) controller.abort();
      speaking = false;
      setStatus("Interrupted — listening…");
    }
    if (!speaking && !recording && level > 0.045) startRecording();
    else if (recording && level < 0.02) stopRecording();
    vadRaf = requestAnimationFrame(vadLoop);
  }

  async function openCall() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const input = audioCtx.createMediaStreamSource(stream);
      micAnalyser = audioCtx.createAnalyser();
      micAnalyser.fftSize = 512;
      input.connect(micAnalyser);
      widget = buildWidget();
      setStatus("Listening…");
      vadLoop();
      draw();
      button.setAttribute("aria-pressed", "true");
    } catch (err) {
      widget = buildWidget();
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
