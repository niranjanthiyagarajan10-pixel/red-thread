// A match: their profile, your chat, where the story breaks, and checks with sources.
// Everything from a chat or a model goes in as text, never as HTML.
import { api, el, remember } from "/static/app.js";

const $ = (id) => document.getElementById(id);
const STAGES = { hook: "Hook", line: "Line", sinker: "Sinker" };
const KIND_WORDS = { link: "site", upi: "UPI ID", phone: "number", place: "place", line: "message" };
const PATTERNS = new Set(["wealth_story", "investment_pitch", "asks_money", "pay_to_withdraw", "small_payout", "install_app"]);

let token = null;
let state = null;
const busy = {};
const errors = {};

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

function openCall() {
  return run("call", async () => {
    const reply = await api(`/api/thread/${token}/call`, {});
    state.call = reply.token;
    remember("call-from", { call: reply.token, match: token });
    location.href = `/proof/${reply.token}`;
  });
}

function renderCall() {
  const title = el("h2", { text: "Test on a call" });
  if (!state.reading) {
    return fill("m-call", false, title, el("p", { class: "sub", text: "Add your chat first. The questions come from it." }));
  }
  if (busy.call) return fill("m-call", false, title, waiting("Writing questions only the writer could answer, then trying to guess them blind"));
  const name = state.chat ? state.chat.other : state.name;
  const sub = state.example
    ? `A voice call with a simulated ${name}. It might be the person who wrote the chat, or a stand-in who never saw it. You find out at the end.`
    : `A short voice call. You ask small things from your chat that only the person who wrote it would know. ${name} agrees first, and sees nothing you see.`;
  const open = state.call ? el("a", { class: "link", href: `/proof/${state.call}`, text: "Open the call",
    onclick: () => remember("call-from", { call: state.call, match: token }) }) : null;
  return fill("m-call", Boolean(state.call), title, el("p", { class: "sub", text: sub }),
    el("div", { class: "adders" },
      el("button", { class: "btn btn-red", text: state.call ? "Start a new call" : "Set up a Proof Call", onclick: openCall }), open),
    failure("call"));
}

const money = (amount) => `₹${Number(amount).toLocaleString("en-IN")}`;
let billImage = null;

function readPlan() {
  return run("plan", async () => {
    state = (await api(`/api/thread/${token}/plan`, {})).state;
  });
}

async function share(text) {
  try {
    if (navigator.share) {
      await navigator.share({ text });
      return;
    }
    await navigator.clipboard.writeText(text);
    errors.plan = "Copied. Paste it to a friend.";
  } catch (error) {
    errors.plan = "Select the note and copy it.";
  }
  render();
}

function renderDate() {
  const title = el("h2", { text: "The date" });
  if (!state.reading) return fill("m-date", false, title, el("p", { class: "sub", text: "Add your chat first. We read how the meeting was set up." }));
  if (busy.plan) return fill("m-date", false, title, waiting("Reading how the date was set up"));
  const plan = state.plan;
  if (!plan || plan.fallback) {
    return fill("m-date", false, title,
      el("p", { class: "sub", text: plan ? plan.headline : "Before you go, see where and how they set up the meeting." }),
      el("div", { class: "adders" }, el("button", { class: "btn btn-ink", text: "Read the date plan", onclick: readPlan })), failure("plan"));
  }
  const checked = new Set(state.checks.map((item) => `${item.kind}:${item.label}`));
  const venue = plan.venue ? el("div", { class: "venue" },
    el("p", {}, el("span", { class: "label", text: "Where " }), el("b", { text: plan.venue })),
    checked.has(`place:${plan.venue}`) ? null : el("button", { class: "chip", text: `Check ${plan.venue}`, disabled: busy.check,
      onclick: () => check("place", plan.venue) })) : null;
  const wires = plan.tripwires.length ? el("ul", { class: "findings" }, plan.tripwires.map((item) => el("li", {},
    el("div", {}, el("span", { class: "what", text: item.label }), item.when ? el("span", { class: "when", text: item.when }) : null),
    el("blockquote", { class: "said", text: item.quote })))) : null;
  const note = el("div", { class: "friend" },
    el("p", { class: "label", text: "A note for a friend" }),
    el("p", { class: "note-text", text: plan.note }),
    el("button", { class: "btn btn-line btn-small", text: "Send to a friend", onclick: () => share(plan.note) }));
  return fill("m-date", true, title,
    el("p", { class: `verdict${plan.tripwires.length ? " warn" : ""}`, text: plan.headline }),
    venue, wires,
    el("ul", { class: "advice" }, plan.advice.map((line) => el("li", { text: line }))),
    note, busy.check ? waiting("Searching the web. This can take up to a minute.") : null, failure("plan"));
}

function addPhoto(kind, image) {
  return run(kind, async () => {
    if (kind === "bill") billImage = image;
    state = (await api(`/api/thread/${token}/${kind}`, { image })).state;
  });
}

function photoAdder(kind, label) {
  const files = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": label });
  files.addEventListener("change", async () => {
    if (files.files[0]) addPhoto(kind, await shrink(files.files[0]));
  });
  return el("span", { class: `btn ${kind === "bill" ? "btn-red" : "btn-line"} file` }, label, files);
}

async function exampleImage(kind) {
  const example = await api("/api/example");
  return shrink(await (await fetch(example[`${kind}_image`])).blob());
}

function ringed(qr) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${qr.w} ${qr.h}`);
  svg.setAttribute("class", "bill-photo");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "The bill, with its payment QR ringed");
  const image = document.createElementNS(ns, "image");
  image.setAttribute("href", billImage);
  image.setAttribute("width", qr.w);
  image.setAttribute("height", qr.h);
  const pad = qr.w / 60;
  const ring = document.createElementNS(ns, "rect");
  ring.setAttribute("x", qr.x1 - pad);
  ring.setAttribute("y", qr.y1 - pad);
  ring.setAttribute("width", qr.x2 - qr.x1 + pad * 2);
  ring.setAttribute("height", qr.y2 - qr.y1 + pad * 2);
  ring.setAttribute("class", "ring");
  ring.setAttribute("stroke-width", qr.w / 150);
  svg.append(image, ring);
  return el("figure", { class: "bill-figure" }, svg, el("figcaption", { text: "SAM 3.1 found the payment QR. Check whose name it pays before you scan." }));
}

function renderTable() {
  const title = el("h2", { text: "At the table" });
  const table = state.table || {};
  const menu = table.menu;
  const read = table.bill;
  const result = table.check;
  if (busy.menu || busy.bill) return fill("m-table", false, title, waiting(busy.bill ? "Reading the bill and finding its QR" : "Reading the menu"));
  const parts = [el("p", { class: "sub", text: "A photo of the menu when you sit down, and of the bill at the end. We compare them line by line." })];
  const adders = el("div", { class: "adders" });
  if (!menu || menu.fallback) adders.append(state.example ? el("button", { class: "btn btn-line", text: "Use the example menu",
    onclick: async () => addPhoto("menu", await exampleImage("menu")) }) : photoAdder("menu", "Photo of the menu"));
  if (!read || read.fallback) adders.append(state.example ? el("button", { class: "btn btn-red", text: "Use the example bill",
    onclick: async () => addPhoto("bill", await exampleImage("bill")) }) : photoAdder("bill", "Photo of the bill"));
  if (menu && !menu.fallback) parts.push(el("p", { class: "small", text: `Menu read: ${menu.items.length} items${menu.venue ? ` from ${menu.venue}` : ""}.` }));
  if (menu && menu.fallback) parts.push(el("p", { class: "small", text: "We couldn't read that menu. Try a flatter, closer photo." }));
  if (adders.children.length) parts.push(adders);
  if (result) {
    const warn = result.level !== "fair" && result.level !== "no_menu";
    parts.push(el("p", { class: `verdict${warn ? " warn" : ""}`, text: result.headline }));
    if (result.level === "overcharged") {
      parts.push(el("p", { class: "sub", text: `By the menu, the food and drinks come to ${money(result.menu_total)}.` }));
    }
    if (read && read.lines.length) {
      const over = new Set(result.findings.filter((item) => item.kind === "overcharge").map((item) => item.name));
      parts.push(el("table", { class: "lines" },
        el("thead", {}, el("tr", {}, el("th", { text: "Item" }), el("th", { text: "Qty" }), el("th", { text: "Bill" }), el("th", { text: "Menu" }))),
        el("tbody", {}, read.lines.map((line) => el("tr", { class: over.has(line.name) ? "over" : "" },
          el("td", { text: line.name }), el("td", { text: String(line.quantity) }), el("td", { text: money(line.amount) }),
          el("td", { text: line.menu_price ? money(line.menu_price * line.quantity) : "" }))))));
    }
    if (table.qr && billImage) parts.push(ringed(table.qr));
    const notes = result.findings.filter((item) => item.kind !== "overcharge");
    if (notes.length) parts.push(el("ul", { class: "advice table-notes" }, notes.map((item) => el("li", { class: item.kind, text: item.text }))));
  }
  parts.push(failure("menu"), failure("bill"));
  return fill("m-table", Boolean(result && result.level !== "unread"), title, parts);
}

function render() {
  if (!state) return;
  $("who").textContent = state.name;
  $("example-badge").hidden = !state.example;
  renderProfile();
  renderChat();
  renderBreaks();
  renderChecks();
  renderCall();
  renderDate();
  renderTable();
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
