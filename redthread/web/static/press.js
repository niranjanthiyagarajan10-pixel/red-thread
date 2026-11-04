// One Press: a personal link for the phone's shortcut, a way to try it here, the presses so far, and the case after a scam.
import { api, el, remember } from "./app.js";
import { openMic } from "./mic.js";
import { base64, wav } from "./note.js";

const $ = (id) => document.getElementById(id);
const KEY = "press-key";
const MAX_NOTE_SECONDS = 60;
const levels = { danger: "Stop", caution: "Careful", calm: "Calm" };
const note = { mic: null, chunks: [], timer: null };
let mine = "";
let clock = null;

async function key() {
  const kept = remember(KEY);
  if (kept) return kept;
  const made = (await api("/api/press", {})).key;
  return remember(KEY, made);
}

function banner(id, reply) {
  const box = $(id);
  box.querySelector(".title").textContent = reply.title;
  box.querySelector(".body").textContent = reply.body;
  box.className = `banner-demo ${reply.level || "calm"}`;
  box.hidden = false;
}

async function copyText(value, button) {
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = "Copied";
  } catch (error) {
    button.textContent = "Select and copy";
  }
}

// The presses so far.

function row(item) {
  const when = String(item.at || "").slice(11, 16);
  const took = item.seconds ? `read in ${item.seconds} s` : "";
  const late = item.why === "slow" ? "answered late" : "";
  return el("li", { class: item.level },
    el("p", { class: "meta", text: [when, levels[item.level] || "", took, late].filter(Boolean).join(" · ") }),
    el("p", { class: "title", text: item.title }),
    el("p", { class: "body", text: item.body }));
}

async function refresh() {
  try {
    const data = await api(`/api/press/${mine}`);
    $("presses").replaceChildren(...data.presses.map(row));
    $("empty").hidden = data.presses.length > 0;
    $("forget").hidden = data.presses.length === 0;
  } catch (error) {
    // The list is a nicety. The next refresh tries again.
  }
  loadCase();
}

// The case after a scam press. Every fact on it comes from the server's code.

function showTimer(timer) {
  clearInterval(clock);
  const line = $("timer");
  if (!timer) {
    line.textContent = "If any money left your account, call within the first hour. That is when it is easiest to freeze.";
    return;
  }
  const end = Date.now() + timer.minutes_left * 60000;
  const tick = () => {
    const left = Math.max(0, Math.round((end - Date.now()) / 60000));
    line.textContent = left > 0
      ? `${left} minutes left in the first hour. Money is easiest to freeze now.`
      : "The first hour has passed. Call 1930 anyway. It can still help.";
  };
  tick();
  clock = setInterval(tick, 15000);
}

function sheetRow(field) {
  const missing = !field.value && !field.yours;
  const extra = field.problems.filter((problem) => problem !== "missing").join(" ");
  const note = field.yours ? field.meaning : missing ? "Not known yet. Say it above, or fill it on the form." : field.meaning;
  const copy = !field.value ? null : el("button", { class: "btn btn-line btn-small", type: "button", text: "Copy",
    onclick: (event) => copyText(field.value, event.currentTarget) });
  return el("li", { class: missing ? "missing" : "" },
    el("p", { class: "label", text: field.label }),
    field.value ? el("p", { class: "value", text: field.value }) : null,
    note ? el("p", { class: "small", text: note }) : null,
    extra ? el("p", { class: "small warn", text: extra }) : null,
    copy);
}

function showCase(view) {
  $("case").hidden = !view;
  if (!view) return;
  $("h-case").textContent = view.title;
  $("script").textContent = view.script;
  showTimer(view.timer);
  $("paid-yes").classList.toggle("chosen", view.paid === true);
  $("paid-no").classList.toggle("chosen", view.paid === false);
  $("checked").hidden = view.checked.length === 0;
  $("checked-lines").replaceChildren(...view.checked.map((line) => el("li", {},
    el("p", { text: line.text }),
    el("a", { class: "inline small", href: line.url, target: "_blank", rel: "noopener", text: `Source: ${line.source}` }))));
  $("heard").textContent = view.heard ? `We have: "${view.heard}"` : "";
  $("form-name").textContent = view.form;
  $("sheet").replaceChildren(...view.sheet.map(sheetRow));
}

async function loadCase() {
  try {
    showCase((await api(`/api/press/${mine}/case`)).case);
  } catch (error) {
    // The next refresh tries again.
  }
}

function nowTime() {
  const now = new Date();
  return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

async function sayPaid(paid, extra = {}) {
  $("paid-error").textContent = "";
  try {
    showCase((await api(`/api/press/${mine}/paid`, { paid, ...extra })).case);
    $("paid-form").hidden = true;
  } catch (error) {
    $("paid-error").textContent = error.message;
  }
}

async function tell(body, status) {
  $("note-error").textContent = "";
  $("heard").textContent = status;
  try {
    const data = await api(`/api/press/${mine}/story`, body);
    if (data.error) $("note-error").textContent = data.error;
    if (data.case) showCase(data.case);
    else $("heard").textContent = data.heard ? `We have: "${data.heard}"` : "";
  } catch (error) {
    $("note-error").textContent = error.message;
    $("heard").textContent = "";
  }
}

async function toggleNote() {
  const button = $("note");
  if (note.mic) {
    clearTimeout(note.timer);
    note.mic.stop();
    note.mic = null;
    button.classList.remove("on");
    button.lastChild.textContent = "Tap and talk";
    if (note.chunks.length) tell({ audio: base64(wav(note.chunks)) }, "Listening to your note...");
    note.chunks = [];
    return;
  }
  try {
    note.chunks = [];
    note.mic = await openMic((pcm) => note.chunks.push(pcm));
    note.timer = setTimeout(toggleNote, MAX_NOTE_SECONDS * 1000);
    button.classList.add("on");
    button.lastChild.textContent = "Recording. Tap to stop";
  } catch (error) {
    $("note-error").textContent = "We couldn't use the mic. Type it instead.";
  }
}

// Trying it from this page: the same check, the same family alert text and the same second notice.

async function later() {
  try {
    const response = await fetch(`/api/press/${mine}/later?wait=40`);
    if (response.status !== 200) return;
    const [title, ...rest] = (await response.text()).split("\n");
    banner("banner-2", { title, body: rest.join(" "), level: "calm" });
    loadCase();
  } catch (error) {
    // The second notice is extra. The first one already said what to do.
  }
}

async function send(file) {
  $("banner-2").hidden = true;
  $("alert-preview").hidden = true;
  const response = await fetch(`/api/press/${mine}`, { method: "POST", headers: { Accept: "application/json" }, body: file });
  if (!response.ok) {
    banner("banner", { title: "Try again in a minute.", body: "Too many presses at once.", level: "calm" });
    return;
  }
  const reply = await response.json();
  banner("banner", reply);
  if (reply.alert) {
    $("alert-text").textContent = reply.alert;
    $("alert-preview").hidden = false;
  }
  refresh();
  if (reply.claim) later();
}

async function start() {
  mine = await key();
  const link = `${location.origin}/api/press/${mine}?for=shortcut`;
  $("link").value = link;
  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(link);
      $("copied").textContent = "Copied.";
    } catch (error) {
      $("link").select();
      $("copied").textContent = "Select the link and copy it.";
    }
  });
  $("shot").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) send(file);
    event.target.value = "";
  });
  $("copy-script").addEventListener("click", (event) => copyText($("script").textContent, event.currentTarget));
  $("paid-no").addEventListener("click", () => sayPaid(false));
  $("paid-yes").addEventListener("click", () => {
    $("paid-form").hidden = false;
    if (!$("paid-at").value) $("paid-at").value = nowTime();
    $("paid-amount").focus();
  });
  $("paid-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const at = $("paid-at").value;
    if (!at) {
      $("paid-error").textContent = "Enter the time you paid.";
      return;
    }
    sayPaid(true, { at, amount: $("paid-amount").value });
  });
  $("paid-at").addEventListener("input", () => { $("paid-error").textContent = ""; });
  $("note").addEventListener("click", toggleNote);
  $("story-send").addEventListener("click", () => {
    const text = $("story-text").value.trim();
    if (!text) {
      $("note-error").textContent = "Type what happened first.";
      return;
    }
    $("story-text").value = "";
    tell({ text }, "Adding it to your case...");
  });
  $("story-text").addEventListener("input", () => { $("note-error").textContent = ""; });
  $("forget").addEventListener("click", async () => {
    if (!window.confirm("Delete every press, your story and your case? This cannot be undone.")) return;
    const response = await fetch(`/api/press/${mine}`, { method: "DELETE" });
    $("forgot").textContent = response.ok ? "Deleted. Nothing from your presses is kept." : "Couldn't delete. Try again.";
    refresh();
  });
  refresh();
  setInterval(refresh, 10000);
}

start().catch(() => {
  $("link").value = "Could not make a link. Reload the page.";
});
