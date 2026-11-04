// One Press setup: a personal link for the phone's shortcut, a way to try it here, and the presses so far.
import { api, el, remember } from "./app.js";

const KEY = "press-key";
const levels = { danger: "Stop", caution: "Careful", calm: "Calm" };

async function key() {
  const kept = remember(KEY);
  if (kept) return kept;
  const made = (await api("/api/press", {})).key;
  return remember(KEY, made);
}

function show(reply) {
  document.getElementById("b-title").textContent = reply.title;
  document.getElementById("b-body").textContent = reply.body;
  const banner = document.getElementById("banner");
  banner.className = `banner-demo ${reply.level}`;
  banner.hidden = false;
}

function row(item) {
  const when = String(item.at || "").slice(11, 16);
  const took = item.seconds ? `read in ${item.seconds} s` : "";
  const late = item.why === "slow" ? "answered late" : "";
  return el("li", { class: item.level },
    el("p", { class: "meta", text: [when, levels[item.level] || "", took, late].filter(Boolean).join(" · ") }),
    el("p", { class: "title", text: item.title }),
    el("p", { class: "body", text: item.body }));
}

async function refresh(mine) {
  try {
    const data = await api(`/api/press/${mine}`);
    document.getElementById("presses").replaceChildren(...data.presses.map(row));
    document.getElementById("empty").hidden = data.presses.length > 0;
  } catch (error) {
    // The list is a nicety. The next refresh tries again.
  }
}

async function send(mine, file) {
  const response = await fetch(`/api/press/${mine}`, { method: "POST", headers: { Accept: "application/json" }, body: file });
  show(response.ok ? await response.json() : { title: "Try again in a minute.", body: "Too many presses at once.", level: "calm" });
  refresh(mine);
}

async function start() {
  const mine = await key();
  const link = `${location.origin}/api/press/${mine}`;
  document.getElementById("link").value = link;
  document.getElementById("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(link);
      document.getElementById("copied").textContent = "Copied.";
    } catch (error) {
      document.getElementById("link").select();
      document.getElementById("copied").textContent = "Select the link and copy it.";
    }
  });
  document.getElementById("shot").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) send(mine, file);
    event.target.value = "";
  });
  refresh(mine);
  setInterval(() => refresh(mine), 10000);
}

start().catch(() => {
  document.getElementById("link").value = "Could not make a link. Reload the page.";
});
