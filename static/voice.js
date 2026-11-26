/* Dedicated llama.cpp-only voice call with a locally drawn, animated face. */
(() => {
  const canvas = document.getElementById("voice-avatar");
  const ctx = canvas.getContext("2d");
  const statusEl = document.getElementById("voice-status");
  const captionEl = document.getElementById("voice-caption");
  const modelEl = document.getElementById("voice-model-select");
  const startBtn = document.getElementById("voice-start");
  const micBtn = document.getElementById("voice-mic");
  const hangupBtn = document.getElementById("voice-hangup");

  let stream = null;
  let audioCtx = null;
  let micAnalyser = null;
  let vadRaf = null;
  let recording = false;
  let recorder = null;
  let chunks = [];
  let speaking = false;
  let activeSource = null;
  let activeController = null;
  let mouth = 0.1;
  let blink = 0;
  let nextBlink = Date.now() + 2500;
  let emotion = "neutral";
  let history = [];
  let speechRms = 0;

  const setStatus = (text) => { statusEl.textContent = text; };
  const setCaption = (text) => { captionEl.textContent = text; };

  function emotionFromText(text, explicit) {
    if (explicit) return explicit;
    const value = text.toLowerCase();
    if (/\b(haha|hilarious|great|wonderful|happy|glad|excited)\b/.test(value)) return "happy";
    if (/\b(sorry|unfortunately|concerned|worried|sad)\b/.test(value)) return "concerned";
    if (/\b(wow|really|incredible|surprising)\b/.test(value)) return "surprised";
    if (/\b(think|consider|maybe|perhaps|let me see)\b/.test(value)) return "thoughtful";
    return "neutral";
  }

  function stripEmotion(text) {
    const match = text.match(/^\s*\[emotion:\s*([a-z]+)\]/i);
    if (!match) return { text: text.trim(), emotion: null };
    return { text: text.replace(match[0], "").trim(), emotion: match[1].toLowerCase() };
  }

  function rmsFrom(analyser) {
    if (!analyser) return 0;
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const sample of data) {
      const value = (sample - 128) / 128;
      sum += value * value;
    }
    return Math.sqrt(sum / data.length);
  }

  function drawFace() {
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#101725";
    ctx.fillRect(0, 0, w, h);

    const cx = w / 2;
    const cy = h / 2;
    const headR = 150;
    const bob = Math.sin(Date.now() / 900) * 3;

    ctx.fillStyle = "#d9b8a3";
    ctx.beginPath();
    ctx.ellipse(cx, cy + bob, headR * 0.78, headR, 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#4b3a33";
    ctx.beginPath();
    ctx.ellipse(cx, cy - 105 + bob, headR * 0.8, 58, 0, Math.PI, Math.PI * 2);
    ctx.fill();

    const eyeY = cy - 28 + bob;
    const eyeOffset = 52;
    if (Date.now() > nextBlink) { blink = 1; nextBlink = Date.now() + 2200 + Math.random() * 2500; }
    blink = Math.max(0, blink - 0.08);

    ctx.fillStyle = "#fff";
    for (const dx of [-eyeOffset, eyeOffset]) {
      ctx.beginPath();
      ctx.ellipse(cx + dx, eyeY, 24, 20 * (1 - blink * 0.9), 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = emotion === "surprised" ? "#31527a" : "#3a2b26";
      ctx.beginPath();
      ctx.arc(cx + dx + (emotion === "thoughtful" ? 5 : 0), eyeY, 9 * (1 - blink), 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#fff";
    }

    ctx.strokeStyle = "#4b3a33";
    ctx.lineWidth = 6;
    ctx.lineCap = "round";
    for (const dx of [-eyeOffset, eyeOffset]) {
      ctx.beginPath();
      const slant = emotion === "concerned" ? (dx < 0 ? -10 : 10) : emotion === "happy" ? 4 : 0;
      ctx.moveTo(cx + dx - 22, eyeY - 36 + slant);
      ctx.lineTo(cx + dx + 22, eyeY - 36 - slant);
      ctx.stroke();
    }

    const targetMouth = speaking ? Math.min(0.95, 0.12 + speechRms * 7) : 0.08;
    mouth += (targetMouth - mouth) * 0.35;
    ctx.fillStyle = "#7c3b3b";
    ctx.beginPath();
    ctx.ellipse(cx, cy + 76 + bob, 38, 8 + mouth * 34, 0, 0, Math.PI * 2);
    ctx.fill();

    if (emotion === "happy" || emotion === "surprised") {
      ctx.fillStyle = "rgba(216, 112, 112, 0.25)";
      for (const dx of [-88, 88]) {
        ctx.beginPath();
        ctx.ellipse(cx + dx, cy + 34 + bob, 22, 12, 0, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    requestAnimationFrame(drawFace);
  }

  async function loadModel() {
    try {
      const data = await fetch("/api/models").then((r) => r.json());
      const usable = (data.models || []).filter((m) =>
        ["llamacpp", "opencode"].includes(m.backend) && m.state === "loaded");
      modelEl.replaceChildren();
      for (const model of usable) {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = `${model.name || model.id} (${model.backend === "opencode" ? "online" : "local llama.cpp"})`;
        modelEl.appendChild(option);
      }
      if (!usable.length) {
        const option = document.createElement("option");
        option.textContent = "No loaded model — load one from Models first.";
        modelEl.appendChild(option);
      }
    } catch (err) {
      modelEl.textContent = "Could not read model list.";
    }
  }

  async function transcribe(blob) {
    const form = new FormData();
    form.append("audio", blob, "utterance.webm");
    const data = await fetch("/api/voice/transcribe", { method: "POST", body: form }).then((r) => r.json());
    if (!data.text) throw new Error(data.error || "No transcript returned.");
    return data.text;
  }

  async function ask(message) {
    const controller = new AbortController();
    activeController = controller;
    const response = await fetch("/api/voice/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, model: modelEl.value }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Voice model request failed (${response.status}).`);
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
          answer += event.content || event.delta?.content || "";
          setCaption(answer);
        } catch (err) {
          if (err instanceof SyntaxError) continue;
          throw err;
        }
      }
    }
    activeController = null;
    return answer;
  }

  async function speak(text) {
    const blob = await fetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(async (response) => {
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "Local speech synthesis failed.");
      }
      return response.blob();
    });
    const buffer = await audioCtx.decodeAudioData(await blob.arrayBuffer());
    const source = audioCtx.createBufferSource();
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    source.buffer = buffer;
    source.connect(analyser);
    analyser.connect(audioCtx.destination);
    activeSource = source;
    speaking = true;
    source.onended = () => {
      speaking = false;
      activeSource = null;
      speechRms = 0;
    };
    source.start();
    const meter = () => {
      if (!speaking) return;
      speechRms = rmsFrom(analyser);
      requestAnimationFrame(meter);
    };
    meter();
  }

  async function processUtterance(blob) {
    setStatus("Transcribing locally…");
    try {
      const userText = await transcribe(blob);
      if (!userText.trim()) { setStatus("Listening…"); return; }
      setCaption(`You: ${userText}`);
      setStatus("Thinking…");
      const raw = await ask(userText);
      const clean = stripEmotion(raw);
      emotion = emotionFromText(clean.text, clean.emotion);
      setCaption(clean.text || "(no reply)");
      history.push({ role: "user", content: userText }, { role: "assistant", content: clean.text });
      history = history.slice(-8);
      if (clean.text) {
        setStatus("Speaking…");
        await speak(clean.text);
      }
      setStatus("Listening…");
    } catch (err) {
      setStatus(`Error: ${err.message}`);
    }
  }

  function startRecording(auto) {
    if (recording || !stream) return;
    try {
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        recording = false;
        micBtn.textContent = auto ? "Press to talk" : "Press to talk";
        const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
        if (blob.size) processUtterance(blob);
      };
      recorder.start();
      recording = true;
      micBtn.textContent = "Stop and send";
      setStatus("Recording…");
    } catch (err) {
      setStatus(`Microphone error: ${err.message}`);
    }
  }

  function stopRecording() {
    if (recorder && recording) recorder.stop();
  }

  function vadLoop() {
    if (!stream) return;
    const rms = rmsFrom(micAnalyser);
    if (speaking && rms > 0.12) {
      if (activeSource) { try { activeSource.stop(); } catch (_) {} }
      if (activeController) activeController.abort();
      speaking = false;
      setStatus("Interrupted — listening…");
    }
    if (!speaking && !recording && rms > 0.045) startRecording(true);
    else if (recording && rms < 0.02) stopRecording();
    vadRaf = requestAnimationFrame(vadLoop);
  }

  async function startCall() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(stream);
      micAnalyser = audioCtx.createAnalyser();
      micAnalyser.fftSize = 512;
      source.connect(micAnalyser);
      startBtn.disabled = true;
      micBtn.disabled = false;
      hangupBtn.disabled = false;
      setStatus("Listening… speak naturally.");
      vadLoop();
    } catch (err) {
      setStatus(`Microphone unavailable: ${err.message}`);
    }
  }

  function hangup() {
    if (vadRaf) cancelAnimationFrame(vadRaf);
    if (activeSource) { try { activeSource.stop(); } catch (_) {} }
    if (activeController) activeController.abort();
    if (recorder && recording) recorder.stop();
    if (stream) stream.getTracks().forEach((track) => track.stop());
    if (audioCtx) audioCtx.close();
    stream = null; audioCtx = null; micAnalyser = null;
    startBtn.disabled = false;
    micBtn.disabled = true;
    hangupBtn.disabled = true;
    setStatus("Call ended.");
  }

  startBtn.addEventListener("click", startCall);
  hangupBtn.addEventListener("click", hangup);
  micBtn.addEventListener("click", () => {
    if (recording) stopRecording();
    else startRecording(false);
  });

  loadModel();
  drawFace();
})();
