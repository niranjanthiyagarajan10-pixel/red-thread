// First Knot: interests, three live words, three questions about your own life, then the knot.
import { api, buzz, remember } from "/static/app.js";
import { audioSender, openMic } from "/static/mic.js";
import { renderTicket } from "/static/ticket.js";

const $ = (id) => document.getElementById(id);
const screens = ["interests", "start", "say", "question", "wait", "tied", "loose", "reask"];
const STEPS = 6;

let token = null;
let questions = [];
let poller = null;
let mic = null;
let sender = null;
let typing = false;
let current = null;
let showAfter = 0;
let lastInterests = [];

function show(name, focusId) {
  for (const screen of screens) {
    const element = $(`screen-${screen}`);
    const on = screen === name;
    element.hidden = !on;
    element.classList.toggle("screen-enter", on);
  }
  const target = focusId && $(focusId);
  if (target) target.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

let drawn = [0, {}];

function drawProgress(done, marks = {}) {
  drawn = [done, marks];
  const svg = $("progress");
  const width = Math.max(120, Math.round(svg.getBoundingClientRect().width) || 600);
  svg.setAttribute("viewBox", `0 0 ${width} 22`);
  for (const line of svg.querySelectorAll("line")) line.setAttribute("x2", String(width - 8));
  svg.querySelectorAll("circle").forEach((circle) => circle.remove());
  for (let index = 0; index < STEPS; index += 1) {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", String(8 + ((width - 16) / (STEPS - 1)) * index));
    circle.setAttribute("cy", "11");
    circle.setAttribute("r", "6");
    if (index < done) circle.classList.add(marks[index] || "done");
    if (index === done) circle.classList.add("now");
    svg.appendChild(circle);
  }
  $("run").style.setProperty("stroke-dashoffset", String(1 - Math.min(1, done / (STEPS - 1))));
}

const marks = {};

function setLevel(element, level) {
  element.style.setProperty("--level", String(1 + Math.min(1.4, level * 9)));
}

function stopListening() {
  if (sender) sender.stop();
  if (mic) mic.stop();
  sender = null;
  mic = null;
}

async function listen(sessionToken, levelElement) {
  try {
    mic = await openMic((pcm) => sender && sender.push(pcm), (level) => setLevel(levelElement, level));
    sender = audioSender(`/api/knot/${sessionToken}/audio`, () => voiceDropped());
    return true;
  } catch (error) {
    return false;
  }
}

function voiceDropped() {
  stopListening();
  typeMode(true);
}

function typeMode(on) {
  typing = on;
  for (const id of ["phrase-typed", "answer-typed", "reask-typed"]) $(id).hidden = !on;
  $("phrase-send").hidden = !on;
  for (const id of ["phrase-skip", "type-instead", "reask-type"]) $(id).hidden = on;
  for (const id of ["mic", "mic-2", "mic-3"]) {
    $(id).classList.toggle("off", on);
  }
  for (const id of ["mic-text", "mic-2-text", "mic-3-text"]) {
    $(id).textContent = on ? "Typing" : "Listening";
  }
}

function poll(sessionToken, handle) {
  let after = 0;
  let alive = true;
  (async () => {
    while (alive) {
      try {
        const data = await api(`/api/knot/${sessionToken}/events?after=${after}&wait=20`);
        after = data.next;
        for (const event of data.events) handle(event);
      } catch (error) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    }
  })();
  return { stop() { alive = false; } };
}

function caption(element, text, partial) {
  const finals = element.querySelector(".final") || element.appendChild(document.createElement("span"));
  finals.className = "final";
  const loose = element.querySelector(".partial") || element.appendChild(document.createElement("span"));
  loose.className = "partial";
  if (partial) {
    loose.textContent = (finals.textContent ? " " : "") + text;
  } else {
    finals.textContent = `${finals.textContent} ${text}`.trim();
    loose.textContent = "";
  }
}

function clearCaption(element) {
  element.textContent = "";
}

function renderStep(event) {
  if (event.step === "phrase") {
    const phrase = $("phrase");
    phrase.classList.remove("heard");
    phrase.textContent = "";
    for (const word of event.phrase.split(" ")) {
      const span = document.createElement("span");
      span.textContent = word;
      phrase.appendChild(span);
    }
    clearCaption($("phrase-caption"));
    drawProgress(1, marks);
    show("say", "h-say");
    return;
  }
  const index = questions.findIndex((item) => item.id === event.step);
  current = event.step;
  $("question-eyebrow").textContent = `${event.interest} · ${index + 1} of ${questions.length}`;
  $("ask").textContent = event.question;
  clearCaption($("answer-caption"));
  $("answer-typed").querySelector("textarea").value = "";
  $("done").disabled = false;
  drawProgress(2 + index, marks);
  show("question", "ask");
}

function handle(event) {
  if (event.type === "step") {
    const delay = Math.max(0, showAfter - Date.now());
    setTimeout(() => renderStep(event), delay);
  } else if (event.type === "partial" || event.type === "caption") {
    const target = current === null ? $("phrase-caption") : $("answer-caption");
    caption(target, event.text, event.type === "partial");
  } else if (event.type === "phrase_ok") {
    $("phrase").classList.add("heard");
    $("mic-text").textContent = "Heard you. You're live.";
    buzz(30);
    showAfter = Date.now() + 1100;
  } else if (event.type === "judged") {
    const index = questions.findIndex((item) => item.id === event.question);
    marks[2 + index] = event.label === "personal" ? "done" : "soft";
  } else if (event.type === "captions_off") {
    voiceDropped();
  } else if (event.type === "outcome") {
    finish(event);
  }
}

async function start(interests) {
  lastInterests = interests;
  $("wait-text").textContent = "Writing your questions";
  show("wait");
  try {
    const data = await api("/api/knot", { interests });
    token = data.token;
    questions = data.state.questions;
    current = null;
    drawProgress(1, marks);
    show("start", "h-start");
  } catch (error) {
    show("interests", "h-interests");
    $("interests-note").textContent = error.message;
  }
}

async function begin(typed) {
  $("begin").disabled = true;
  let listening = false;
  if (!typed) {
    $("begin").textContent = "Allow the mic";
    listening = await listen(token, $("mic"));
  }
  typeMode(!listening);
  poller = poll(token, handle);
  try {
    await api(`/api/knot/${token}/begin`, {});
  } catch (error) {
    $("mic-note").textContent = error.message;
  }
  $("begin").disabled = false;
  $("begin").textContent = "Show the words";
}

async function sendTyped(area, sessionToken = token) {
  const text = area.value.trim();
  if (!text) return;
  area.value = "";
  await api(`/api/knot/${sessionToken}/say`, { text });
}

async function done() {
  $("done").disabled = true;
  if (typing) await sendTyped($("answer-typed").querySelector("textarea"));
  const last = questions.findIndex((item) => item.id === current) === questions.length - 1;
  if (last) {
    $("wait-text").textContent = "Checking your answers";
    showAfter = 0;
    setTimeout(() => {
      if (!$("screen-question").hidden) show("wait");
    }, 150);
  }
  await api(`/api/knot/${token}/next`, {});
}

async function finish(outcome) {
  stopListening();
  if (poller) poller.stop();
  drawProgress(outcome.tied ? 6 : 5, marks);
  if (!outcome.tied) {
    $("loose-text").textContent = outcome.reason;
    show("loose", "h-loose");
    return;
  }
  const words = outcome.checked_by === "code" ? " We checked this one by code alone." : "";
  $("tied-text").textContent = `${outcome.personal} of ${outcome.asked} answers came from your own life, and we heard your three words live.${words}`;
  remember("redthread.knot", { token, card: outcome.card });
  $("open-card").href = `/card/${outcome.card}`;
  $("copy").dataset.link = `${location.origin}/card/${outcome.card}`;
  try {
    renderTicket($("ticket"), await api(`/api/card/${outcome.card}`));
  } catch (error) {
    $("ticket").hidden = true;
  }
  show("tied", "h-tied");
  requestAnimationFrame(() => $("knot").classList.add("tied"));
  buzz([20, 60, 20]);
}

async function reask() {
  $("try-reask").disabled = true;
  try {
    const data = await api(`/api/knot/${token}/reask`, {});
    const later = data.token;
    $("reask-ask").textContent = data.state.question;
    $("reask-result").textContent = "";
    $("reask-result").className = "result";
    clearCaption($("reask-caption"));
    show("reask", "reask-ask");
    const listening = await listen(later, $("mic-3"));
    typeMode(!listening);
    const watcher = poll(later, (event) => {
      if (event.type === "partial" || event.type === "caption") {
        caption($("reask-caption"), event.text, event.type === "partial");
      } else if (event.type === "captions_off") {
        voiceDropped();
      } else if (event.type === "rechecked") {
        stopListening();
        watcher.stop();
        const words = {
          consistent: ["That fits your intro.", "fits"],
          contradicts: ["That's different from your intro.", "differs"],
          no_answer: ["We couldn't hear a clear answer.", "differs"],
        }[event.label] || ["We couldn't check that one right now.", "fits"];
        $("reask-result").textContent = words[0];
        $("reask-result").className = `result ${words[1]}`;
      }
    });
    await api(`/api/knot/${later}/begin`, {});
    $("reask-done").onclick = async () => {
      $("reask-done").disabled = true;
      if (typing) await sendTyped($("reask-typed").querySelector("textarea"), later);
      await api(`/api/knot/${later}/next`, {});
    };
  } catch (error) {
    $("try-reask").textContent = error.message;
  }
}

function wire() {
  const inputs = [...document.querySelectorAll("input[name=interest]")];
  for (const chip of document.querySelectorAll(".chip")) {
    chip.addEventListener("click", () => {
      const empty = inputs.find((input) => !input.value.trim());
      if (empty) empty.value = chip.textContent;
      else inputs[inputs.length - 1].value = chip.textContent;
    });
  }
  $("interests-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const interests = inputs.map((input) => input.value.trim()).filter(Boolean);
    if (!interests.length) {
      $("interests-note").textContent = "Add at least one thing you love.";
      inputs[0].focus();
      return;
    }
    start(interests);
  });
  for (const input of inputs) input.addEventListener("input", () => { $("interests-note").textContent = "Nothing here is shown to anyone."; });
  $("begin").addEventListener("click", () => begin(false));
  $("begin-typed").addEventListener("click", () => begin(true));
  $("phrase-skip").addEventListener("click", () => typeMode(true));
  $("phrase-send").addEventListener("click", () => sendTyped($("phrase-typed").querySelector("textarea")));
  $("done").addEventListener("click", done);
  $("type-instead").addEventListener("click", () => typeMode(true));
  $("reask-type").addEventListener("click", () => typeMode(true));
  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("copy").dataset.link);
      $("copy").textContent = "Copied";
    } catch (error) {
      $("copy").textContent = $("copy").dataset.link;
    }
  });
  $("try-reask").addEventListener("click", reask);
  $("again").addEventListener("click", () => {
    for (const key of Object.keys(marks)) delete marks[key];
    inputs.forEach((input, index) => { input.value = lastInterests[index] || ""; });
    drawProgress(0);
    show("interests", "h-interests");
  });
  drawProgress(0);
  window.addEventListener("resize", () => drawProgress(...drawn));
}

wire();
