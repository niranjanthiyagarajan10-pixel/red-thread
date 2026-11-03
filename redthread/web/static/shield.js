// Scam check: one chat for any scam, a listen mode for a live call, and the way back after money has gone.
// It talks to the same engine the WhatsApp contact used. Everything from a person or a model goes in as text.
import { api, buzz, el, remember } from "/static/app.js";
import { audioSender, openMic } from "/static/mic.js";

const $ = (id) => document.getElementById(id);
const MAX_NOTE_SECONDS = 60;
const STEPS = [
  ["helpline_ack", "Called 1930"], ["filed", "Complaint filed"], ["held", "Money on hold"],
  ["eligible", "Ready to restore"], ["requested", "Refund asked for"], ["closed", "Money back"],
];
const STARTERS = [
  { label: "Someone from CBI or police is on a call with me", text: "Someone says they are from the CBI and I am on a video call with them. They say I am under digital arrest." },
  { label: "Is this message real?", text: "Is this real? Congratulations, your profit is Rs 4.2 lakh. To withdraw it, pay 10% tax first to tax.desk@ybl" },
  { label: "I lost money", text: "I lost Rs 38,000 to a fake trading app yesterday. The UTR is 533827652689." },
  { label: "Listen to a call on speaker", listen: true },
];

let session = remember("shield-session");
if (!session) session = remember("shield-session", (crypto.randomUUID && crypto.randomUUID()) || String(Math.random()).slice(2));
let busy = false;
let simulated = [];
let simulatedAt = 0;

// The conversation.

function you(text, extra) {
  $("convo").append(el("li", { class: "turn you" }, el("span", { class: "label", text: "You" }), el("p", { class: "said", text: text || extra })));
}

function linkify(line) {
  const match = line.match(/(\/proof\/[A-Za-z0-9_-]{10,64})/);
  if (!match) return el("p", { text: line });
  const [before] = line.split(match[1]);
  return el("p", {}, before, el("a", { href: match[1], text: `${location.origin}${match[1]}` }));
}

function sheetView(sheet) {
  const rows = sheet.fields.map((field) => {
    const problems = field.problems.filter((problem) => problem !== "missing");
    return el("div", { class: `field-row${field.problems.length ? " problem" : ""}` },
    el("div", { class: "label", text: field.label }),
    el("div", { class: "value", text: field.value || (field.person_only ? "You fill this in" : "Missing") }),
    field.value ? el("button", { class: "chip", type: "button", text: "Copy", onclick: (event) => copy(field.value, event.target) }) : null,
    problems.length ? el("p", { class: "fix", text: problems.join(" ") }) : null,
    field.meaning ? el("p", { class: "meaning", text: field.meaning }) : null);
  });
  return el("section", { class: "sheet" },
    el("h3", { text: sheet.ready ? "Ready to copy, field by field" : "Your form so far, field by field" }),
    el("p", { class: "sub", text: sheet.ready
      ? "Copy each value into the portal in this order. You submit it yourself."
      : "Fields marked in red need fixing before you file. Answer the question above, and we update the form." }),
    sheet.changes.length ? el("p", { class: "small", text: `Changed: ${sheet.changes.join(", ")}` }) : null,
    rows);
}

async function copy(value, button) {
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = "Copied";
  } catch (error) {
    button.textContent = "Select and copy";
  }
}

function answer(reply) {
  const out = reply.answer;
  const turn = el("li", { class: "turn bot" },
    el("h2", { class: "reply-head", text: out.headline }),
    out.proof.length ? el("ul", { class: "reply-proof" }, out.proof.map((line) => el("li", { text: line }))) : null,
    out.next_step ? el("p", { class: "reply-next", text: out.next_step }) : null,
    reply.other_reports ? el("p", { class: "tag differs", text: "Someone else reported the same details." }) : null,
    (reply.also || []).map(linkify),
    out.buttons.length ? el("div", { class: "chips" }, out.buttons.map((button) => el("button", {
      class: "chip", type: "button", text: button.label, onclick: () => send({ button: button.id }, button.label),
    }))) : null,
    reply.sheet ? sheetView(reply.sheet) : null);
  $("convo").append(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });
  if (reply.case) showCase(reply.case);
}

async function send(body, shown) {
  if (busy) return;
  busy = true;
  $("send").disabled = true;
  you(body.text, shown);
  const wait = el("li", { class: "turn bot" }, el("p", { class: "wait", role: "status" }, el("i"), el("span", { text: "Reading" })));
  $("convo").append(wait);
  try {
    const reply = await api("/api/message", { session, ...body });
    wait.remove();
    answer(reply);
  } catch (error) {
    wait.replaceChildren(el("p", { class: "error", text: error.message }));
  }
  busy = false;
  $("send").disabled = false;
}

// The case, as forwarded messages move it along.

function showCase(found) {
  const reached = STEPS.findIndex(([state]) => state === found.state);
  const done = found.state === "partly_credited" ? 4 : reached;
  $("case").hidden = false;
  $("case").replaceChildren(
    el("h2", { class: "subhead", id: "h-case", text: "Your case" }),
    el("ol", { class: "steps" }, STEPS.map(([, label], index) => el("li", { class: index <= done ? "done" : "", text: label }))),
    el("dl", { class: "case-facts" },
      found.ack_id ? [el("dt", { text: "Acknowledgement" }), el("dd", { text: found.ack_id })] : null,
      found.expected ? [el("dt", { text: "Lost" }), el("dd", { text: `₹${Number(found.expected).toLocaleString("en-IN")}` })] : null,
      found.held ? [el("dt", { text: "On hold" }), el("dd", { text: `₹${Number(found.held).toLocaleString("en-IN")}` })] : null,
      found.request_id ? [el("dt", { text: "Refund request" }), el("dd", { text: found.request_id })] : null));
  showSimulated();
}

async function showSimulated() {
  if (!simulated.length) {
    try {
      simulated = (await api("/api/world")).events;
    } catch (error) {
      return;
    }
  }
  const next = simulated[simulatedAt];
  $("sim").hidden = false;
  $("sim").replaceChildren(
    el("h2", { class: "subhead", id: "h-sim", text: "Try the weeks after, simulated" }),
    el("p", { class: "sub", text: "A real case moves by SMS from the helpline, the portal and the bank. Forward each one here. These are invented, for testing." }),
    next
      ? el("button", { class: "chip", type: "button", text: `Day ${next.day}: ${next.text}`, onclick: () => {
        simulatedAt += 1;
        send({ text: next.text }, `Simulated message, day ${next.day}: ${next.text}`);
      } })
      : el("p", { class: "small", text: "That's the whole simulated case." }));
}

// Listen mode: the call on speaker, this page on a second device.

const listen = { token: null, mic: null, sender: null, seq: 0, captions: [], partial: "", alarms: [], said: "", phase: "ready" };

function renderListen() {
  const box = $("listen");
  box.hidden = false;
  const parts = [el("h2", { class: "title", id: "h-listen", text: "Listen to a call" })];
  if (listen.phase === "ready") {
    parts.push(
      el("p", { class: "sub", text: "Put the call on speaker, and keep this page open on another phone or a laptop nearby. We show what's said, and warn the moment it follows a scam script. Nothing is recorded or kept." }),
      el("div", { class: "actions" }, el("button", { class: "btn btn-red", type: "button", text: "Start listening", onclick: startListening }),
        el("button", { class: "link", type: "button", text: "Not now", onclick: () => { box.hidden = true; } })));
  } else {
    parts.push(...listen.alarms.map((alarm) => el("section", { class: "alarm", role: "alert" },
      el("h2", { text: alarm.headline }), alarm.proof.map((line) => el("p", { text: line })), el("p", { class: "go", text: alarm.next_step }))));
    const lines = listen.captions.slice(-6).map((text) => el("p", { class: "line", text }));
    if (listen.partial) lines.push(el("p", { class: "line partial", text: listen.partial }));
    parts.push(el("div", { class: "captions" }, lines.length ? lines : el("p", { class: "line quiet", text: listen.phase === "live" ? "Listening. Words appear here." : "Nothing was heard." })));
    if (listen.phase === "live") {
      parts.push(el("div", { class: "adders" }, el("span", { class: `mic${listen.mic ? "" : " off"}`, id: "listen-mic" }, el("i"), listen.mic ? "Listening" : "The mic is off"),
        el("button", { class: "btn btn-ink", type: "button", text: "Stop", onclick: stopListening })));
    } else {
      parts.push(el("div", { class: "actions" },
        listen.said ? el("button", { class: "btn btn-red", type: "button", text: "Check what was said", onclick: () => send({ text: `On a call they said: ${listen.said}` }, "Check what was said on the call") }) : null,
        el("button", { class: "link", type: "button", text: "Listen again", onclick: () => { Object.assign(listen, { token: null, seq: 0, captions: [], partial: "", alarms: [], said: "", phase: "ready" }); renderListen(); } })));
    }
  }
  if (listen.error) parts.push(el("p", { class: "error", role: "alert", text: listen.error }));
  box.replaceChildren(...parts);
}

async function startListening() {
  listen.error = "";
  try {
    listen.token = (await api("/api/listen", {})).token;
    listen.sender = audioSender(`/api/listen/${listen.token}/audio`);
    listen.mic = await openMic((pcm) => listen.sender.push(pcm), (level) => {
      const dot = document.querySelector("#listen-mic i");
      if (dot) dot.style.setProperty("--level", String(1 + Math.min(1.2, level * 6)));
    }).catch(() => {
      listen.error = "We couldn't use the mic. Check the browser's permission.";
      return null;
    });
    await api(`/api/listen/${listen.token}/start`, {});
    listen.phase = "live";
    renderListen();
    follow(listen.token);
  } catch (error) {
    listen.error = error.message;
    renderListen();
  }
}

async function follow(token) {
  while (listen.token === token && listen.phase === "live") {
    try {
      const reply = await api(`/api/listen/${token}/events?after=${listen.seq}&wait=20`);
      listen.seq = reply.next;
      for (const event of reply.events) {
        if (event.type === "partial") listen.partial = event.text;
        if (event.type === "caption") { listen.captions.push(event.text); listen.partial = ""; }
        if (event.type === "alarm") { listen.alarms.push(event); buzz([80, 60, 80]); }
        if (event.type === "captions_off") listen.error = "Live captions are off right now. The alarms need them, so tell us what they said in the chat below.";
      }
      if (listen.phase === "live") renderListen();
    } catch (error) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
}

async function stopListening() {
  if (listen.mic) listen.mic.stop();
  if (listen.sender) listen.sender.stop();
  listen.mic = null;
  listen.phase = "ended";
  try {
    listen.said = (await api(`/api/listen/${listen.token}/stop`, {})).said;
  } catch (error) {
    listen.said = "";
  }
  renderListen();
}

// A voice note, recorded as the WAV the engine reads.

const note = { mic: null, chunks: [], started: 0, timer: null };

function wav(chunks) {
  const size = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const view = new DataView(new ArrayBuffer(44 + size));
  const text = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + size, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, size, true);
  let offset = 44;
  for (const chunk of chunks) {
    new Uint8Array(view.buffer, offset, chunk.byteLength).set(new Uint8Array(chunk));
    offset += chunk.byteLength;
  }
  return new Uint8Array(view.buffer);
}

function base64(bytes) {
  let out = "";
  for (let index = 0; index < bytes.length; index += 0x8000) out += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(out);
}

async function toggleNote() {
  const button = $("note");
  $("note-error").textContent = "";
  if (note.mic) {
    clearTimeout(note.timer);
    note.mic.stop();
    note.mic = null;
    button.classList.add("off");
    button.lastChild.textContent = "Record a voice note";
    if (note.chunks.length) send({ audio: base64(wav(note.chunks)) }, "A voice note");
    note.chunks = [];
    return;
  }
  try {
    note.chunks = [];
    note.mic = await openMic((pcm) => note.chunks.push(pcm));
    note.timer = setTimeout(toggleNote, MAX_NOTE_SECONDS * 1000);
    button.classList.remove("off");
    button.lastChild.textContent = "Recording. Tap to send";
  } catch (error) {
    $("note-error").textContent = "We couldn't use the mic. Type it instead.";
  }
}

// Files and the form.

function readAs(file, how) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    if (how === "text") reader.readAsText(file);
    else reader.readAsDataURL(file);
  });
}

$("shot").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const url = await readAs(file, "data");
  send({ image: url.split(",")[1], image_mime: file.type || "image/jpeg" }, `A screenshot: ${file.name}`);
  event.target.value = "";
});
$("export").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  send({ chat_export: await readAs(file, "text") }, `A chat export: ${file.name}`);
  event.target.value = "";
});
$("note").addEventListener("click", toggleNote);
$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  $("text").value = "";
  send({ text });
});
$("starters").replaceChildren(...STARTERS.map((starter) => el("button", {
  class: "chip", type: "button", text: starter.label,
  onclick: () => (starter.listen ? (renderListen(), $("listen").scrollIntoView({ behavior: "smooth" })) : send({ text: starter.text })),
})));
