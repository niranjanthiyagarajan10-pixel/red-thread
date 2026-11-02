// A match: their profile, your chat, where the story breaks, and checks with sources.
// Everything from a chat or a model goes in as text, never as HTML.
import { api } from "/static/app.js";

const $ = (id) => document.getElementById(id);
const STAGES = { hook: "Hook", line: "Line", sinker: "Sinker" };
const KIND_WORDS = { link: "site", upi: "UPI ID", phone: "number", place: "place", line: "message" };
const PATTERNS = new Set(["wealth_story", "investment_pitch", "asks_money", "pay_to_withdraw", "small_payout", "install_app"]);

let token = null;
let state = null;
const busy = {};
const errors = {};

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat(Infinity)) if (kid) node.append(kid);
  return node;
}

function waiting(text) {
  return el("p", { class: "wait", role: "status" }, el("i"), el("span", { text }));
}

function fill(id, done, ...kids) {
  const node = $(id);
  node.classList.toggle("todo", !done);
  node.replaceChildren(...kids.flat().filter(Boolean));
}

function failure(key) {
  return errors[key] ? el("p", { class: "error", role: "alert", text: errors[key] }) : null;
}

async function shrink(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1800 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.86);
}

function asDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

async function run(key, work) {
  busy[key] = true;
  errors[key] = "";
  render();
  try {
    await work();
  } catch (error) {
    errors[key] = error.message;
  }
  busy[key] = false;
  render();
}

function addProfile(body) {
  return run("profile", async () => {
    state = (await api(`/api/thread/${token}/profile`, body)).state;
  });
}

function addChat(text) {
  return run("chat", async () => {
    state = (await api(`/api/thread/${token}/chat`, { text })).state;
  });
}

function check(kind, value) {
  return run("check", async () => {
    const reply = await api(`/api/thread/${token}/check`, { kind, value });
    state.checks.push(reply.check);
  }).then(() => {
    const results = document.querySelectorAll("#m-checks .check");
    if (results.length) results[results.length - 1].scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function renderProfile() {
  const title = el("h2", { text: "Their profile" });
  const profile = state.profile;
  if (busy.profile) return fill("m-profile", false, title, waiting("Reading their profile"));
  if (profile && profile.lines.length) {
    const claims = profile.claims.length
      ? el("ul", { class: "claims", "aria-label": "What the profile claims" },
        profile.claims.map((claim) => el("li", {}, el("b", { text: claim.topic }), claim.quote)))
      : null;
    return fill("m-profile", true, title,
      el("p", { class: "sub", text: "What their profile says about them. We hold the chat to it." }),
      el("div", { class: "profile-text", text: profile.lines.join("\n") }), claims);
  }
  const text = el("textarea", { rows: "6", placeholder: "Paste the text from their profile", "aria-label": "Profile text" });
  const pasteBox = el("div", { class: "drop", hidden: true }, text,
    el("div", { class: "adders" }, el("button", { class: "btn btn-ink", text: "Read their profile",
      onclick: () => text.value.trim() && addProfile({ text: text.value }) })));
  const files = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", multiple: true, "aria-label": "Add profile screenshots" });
  files.addEventListener("change", async () => {
    const chosen = [...files.files].slice(0, 4);
    if (!chosen.length) return;
    const images = await Promise.all(chosen.map(shrink));
    addProfile({ images });
  });
  const sub = profile && profile.fallback
    ? "We couldn't read that screenshot. Try a clearer one, or paste the text."
    : "Screenshots of their dating profile, or its text. Up to four.";
  return fill("m-profile", false, title, el("p", { class: "sub", text: sub }),
    el("div", { class: "adders" },
      el("span", { class: "btn btn-red file" }, "Add screenshots", files),
      el("button", { class: "link", text: "Paste the text instead", onclick: () => { pasteBox.hidden = false; text.focus(); } })),
    pasteBox, failure("profile"));
}

function renderChat() {
  const title = el("h2", { text: "Your chat" });
  if (busy.chat) return fill("m-chat", false, title, waiting("Reading the whole chat"));
  const reading = state.reading;
  if (!reading) {
    const text = el("textarea", { rows: "8", placeholder: "Rohan: Hey! Your profile made me laugh\nMe: Ha, someone had to say it", "aria-label": "Your chat" });
    const file = el("input", { type: "file", accept: ".txt,text/plain", "aria-label": "Add a WhatsApp export" });
    file.addEventListener("change", async () => {
      if (file.files[0]) addChat(await file.files[0].text());
    });
    return fill("m-chat", false, title,
      el("p", { class: "sub", text: "Paste it, or add a WhatsApp export. The whole chat is read at once." }),
      el("div", { class: "drop" }, text),
      el("div", { class: "adders" },
        el("button", { class: "btn btn-red", text: "Read the whole chat", onclick: () => text.value.trim() && addChat(text.value) }),
        el("span", { class: "link file" }, "Add a WhatsApp export", file)),
      failure("chat"));
  }
  const chat = state.chat;
  const span = chat.first && chat.last ? `, ${chat.first} to ${chat.last}` : "";
  const order = ["hook", "line", "sinker"];
  const findings = [...reading.findings].sort((a, b) => order.indexOf(a.stage) - order.indexOf(b.stage));
  const list = findings.length ? el("ul", { class: "findings" }, findings.map((found) => el("li", {},
    el("div", {}, el("span", { class: "stage", text: STAGES[found.stage] }), el("span", { class: "what", text: found.label }),
      found.when ? el("span", { class: "when", text: found.when }) : null),
    el("blockquote", { class: "said", text: found.quote }),
    PATTERNS.has(found.signal) ? el("button", { class: "link ask", text: "Is this a known scam?", disabled: busy.check,
      onclick: () => check("line", found.quote) }) : null))) : null;
  const changed = reading.contradictions.map((item) => el("div", { class: "break" },
    el("div", {}, el("div", { class: "label", text: `They said${item.first_when ? `, ${item.first_when}` : ""}` }), el("q", { text: item.first })),
    el("div", {}, el("div", { class: "label", text: `Then${item.second_when ? `, ${item.second_when}` : ""}` }), el("q", { text: item.second }))));
  return fill("m-chat", true, title,
    el("p", { class: `verdict${reading.level === "nothing_found" ? "" : " warn"}`, text: reading.headline }),
    el("p", { class: "sub", text: `${chat.turns} messages with ${chat.other}${span}. This is about the messages, not the person.` }),
    reading.fallback ? el("p", { class: "small", text: "Read by fixed rules only this time." }) : null,
    list, changed.length ? el("h3", { class: "subhead", text: "Their own story changed" }) : null, changed, failure("chat"));
}

function renderBreaks() {
  const title = el("h2", { text: "Where the story breaks" });
  if (!(state.profile && state.profile.lines.length && state.reading)) {
    return fill("m-breaks", false, title, el("p", { class: "sub", text: "Add their profile and the chat, and we'll compare them." }));
  }
  if (busy.profile || busy.chat) return fill("m-breaks", false, title, waiting("Comparing"));
  if (!state.breaks.length) {
    return fill("m-breaks", true, title, el("p", { class: "sub", text: "Their profile and the chat agree so far." }));
  }
  return fill("m-breaks", true, title,
    el("p", { class: "sub", text: "Things their profile says, and things they wrote, that can't both be true." }),
    state.breaks.map((item) => el("div", { class: "break" },
      el("div", {}, el("div", { class: "label", text: "On their profile" }), el("q", { text: item.profile_quote })),
      el("div", {}, el("div", { class: "label", text: `In the chat${item.when ? `, ${item.when}` : ""}` }), el("q", { text: item.chat_quote })))));
}

function renderChecks() {
  const title = el("h2", { text: "Check before you trust" });
  const done = new Set(state.checks.map((item) => `${item.kind}:${item.label}`));
  const offered = state.items.filter((item) => !done.has(`${item.kind}:${item.label}`));
  const chips = offered.length ? el("div", { class: "items" }, offered.map((item) => el("button", {
    class: "chip", text: `Check ${item.label}`, disabled: busy.check, onclick: () => check(item.kind, item.value),
  }))) : null;
  const place = el("input", { placeholder: "A place, app or company they named", "aria-label": "Something to check" });
  const ask = el("div", { class: "ask-row" }, place, el("button", { class: "btn btn-line", text: "Check", disabled: busy.check,
    onclick: () => place.value.trim() && check("place", place.value) }));
  const results = state.checks.map((item) => el("section", { class: "check" },
    el("p", { class: "what", text: `${KIND_WORDS[item.kind]}: ${item.label}` }),
    el("h3", { text: item.headline }),
    item.lines.length ? el("ul", {}, item.lines.map((line) => el("li", {}, line.text,
      el("span", { class: "src" }, line.sources.map((source, index) => [index ? " · " : "",
        el("a", { href: source.url, target: "_blank", rel: "noopener noreferrer", text: source.host })]))))) : null,
    item.note ? el("p", { class: "note", text: item.note }) : null));
  return fill("m-checks", state.checks.length > 0, title,
    el("p", { class: "sub", text: "Links, payment IDs and numbers they sent. We search the web and earlier reports, and show only what has a source." }),
    chips, ask, busy.check ? waiting("Searching the web. This can take up to a minute.") : null, results, failure("check"));
}

function render() {
  if (!state) return;
  $("who").textContent = state.name;
  $("example-badge").hidden = !state.example;
  renderProfile();
  renderChat();
  renderBreaks();
  renderChecks();
}

function show(view) {
  $("start").hidden = view !== "start";
  $("view").hidden = view !== "view";
  window.scrollTo({ top: 0 });
}

async function start(name, example = false) {
  const reply = await api("/api/thread", { name, example });
  token = reply.token;
  state = reply.state;
  history.replaceState(null, "", `/match#${token}`);
  show("view");
  render();
}

async function tryExample() {
  $("example").disabled = true;
  try {
    const example = await api("/api/example");
    await start(example.name, true);
    busy.profile = true;
    render();
    const image = await asDataUrl(await (await fetch(example.profile_image)).blob());
    await addProfile({ images: [image] });
    await addChat(example.chat);
  } catch (error) {
    $("start-note").textContent = error.message;
  }
  $("example").disabled = false;
}

async function resume() {
  const saved = location.hash.slice(1);
  if (!/^[A-Za-z0-9_-]{10,64}$/.test(saved)) return;
  try {
    state = await api(`/api/thread/${saved}`);
    token = saved;
    show("view");
    render();
  } catch (error) {
    history.replaceState(null, "", "/match");
  }
}

$("start-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = $("name").value.trim();
  if (!name) {
    $("start-note").textContent = "Add their first name, as it shows in the chat.";
    $("name").focus();
    return;
  }
  start(name).catch((error) => { $("start-note").textContent = error.message; });
});
$("example").addEventListener("click", tryExample);
$("forget").addEventListener("click", async () => {
  if (token) await api(`/api/thread/${token}/forget`, {}).catch(() => null);
  token = null;
  state = null;
  history.replaceState(null, "", "/match");
  show("start");
});
resume();
