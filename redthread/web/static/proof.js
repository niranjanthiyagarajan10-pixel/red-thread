// A Proof Call. The asker sees questions, live captions and cards. The guest sees only the consent and the phrase.
// Everything from a call or a model goes in as text, never as HTML.
import { api, buzz, el, remember } from "/static/app.js";
import { audioSender, openMic } from "/static/mic.js";

const token = location.pathname.split("/").pop();
const root = document.getElementById("call");
const RESULTS = {
  consistent: ["Fits what they wrote", "fits"],
  contradicts: ["Differs from what they wrote", "differs"],
  no_answer: ["No clear answer", "unclear"],
  unscored: ["Not scored this time", "unclear"],
};
const MAX_CAPTIONS = 6;

let state = null;
let seq = 0;
let receipt = null;
let partial = null;
let mic = null;
let sender = null;
let note = "";
const captions = [];
const cards = new Map();
const differs = [];
const alarms = [];
const asked = new Set();
const busy = new Set();

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const post = (action, body = {}) => api(`/api/proof/${token}/${action}`, body);
const other = () => (state && state.other) || "them";
const modelName = (by) => (by === "muse-wonder-1.0" ? "Muse Wonder" : "A second model");

function swap(id, ...kids) {
  const node = $(id);
  if (node) node.replaceChildren(...kids.flat(Infinity).filter(Boolean));
}

async function act(key, work) {
  if (busy.has(key)) return;
  busy.add(key);
  note = "";
  renderParts();
  try {
    await work();
  } catch (error) {
    note = error.message;
  }
  busy.delete(key);
  renderParts();
}

// The page.

function render() {
  if (!state) return;
  if (state.role === "guest") return renderGuest();
  const title = state.phase === "ended" ? "The call has ended." : `Does ${other()} know the story they wrote?`;
  root.replaceChildren(...[
    el("p", { class: "eyebrow" }, "Proof Call", state.simulated ? el("span", { class: "badge", text: "Simulated person, made for testing" }) : null),
    el("h1", { class: "headline", tabindex: "-1", text: title }),
    state.phase === "ended" ? el("div", { id: "c-receipt" }) : null,
    state.phase === "waiting" ? el("div", { id: "c-waiting" }) : null,
    state.phase === "live" ? el("div", { id: "c-alarms" }) : null,
    state.phase === "live" ? el("div", { id: "c-phrase" }) : null,
    el("div", { id: "c-guard" }),
    el("div", { id: "c-questions" }),
    state.phase === "live" ? el("div", { id: "c-live" }) : null,
    el("div", { id: "c-cards" }),
    state.phase === "live" ? el("div", { class: "actions" }, el("button", { class: "btn btn-ink", id: "end", text: "End the call", onclick: end })) : null,
    el("p", { class: "error", id: "c-note", role: "alert" }),
  ].filter(Boolean));
  renderParts();
}

function renderParts() {
  if (!state || state.role === "guest") return renderGuest();
  renderWaiting();
  renderAlarms();
  renderPhrase();
  renderGuard();
  renderQuestions();
  renderLive();
  renderCards();
  renderReceipt();
  swap("c-note", note);
}

function renderWaiting() {
  if (state.phase !== "waiting") return;
  const parts = [el("p", { class: "lede", text: "Ask small things only the person who wrote the chat would know. Their answers appear beside their own words, and you decide what it means." })];
  if (state.guest) {
    const link = `${location.origin}/proof/${state.guest}`;
    const field = el("input", { class: "copy", readonly: true, value: link, "aria-label": "Their link" });
    parts.push(el("section", { class: "step" },
      el("h2", { text: "1. Send them this link" }),
      el("p", { class: "sub", text: "They agree before anything is heard. Then talk on any call as usual, with this page open." }),
      el("div", { class: "ask-row" }, field,
        el("button", { class: "btn btn-line", text: "Copy", onclick: async () => {
          field.select();
          try { await navigator.clipboard.writeText(link); note = "Copied."; } catch (error) { note = "Select the link and copy it."; }
          swap("c-note", note);
        } }))));
  }
  const agreed = state.consented;
  parts.push(el("section", { class: "step" },
    el("h2", { text: state.guest ? "2. Start when you're ready" : "Start the call" }),
    el("p", { class: "consent", text: state.consent_text }),
    state.other_joined && state.guest ? el("p", { class: "tag fits", text: `${other()} has joined and agreed.` }) : null,
    agreed
      ? el("p", { class: "wait", role: "status" }, el("i"), el("span", { text: `Waiting for ${other()} to join and agree` }))
      : el("div", { class: "actions" }, el("button", { class: "btn btn-red", text: "I agree. Start the call", disabled: busy.has("consent"),
        onclick: () => act("consent", async () => { await post("consent"); state.consented = true; }) }))));
  swap("c-waiting", parts);
}

function renderAlarms() {
  swap("c-alarms", alarms.map((alarm) => el("section", { class: "alarm", role: "alert" },
    el("h2", { text: alarm.headline }),
    el("q", { text: alarm.said }),
    el("p", { text: alarm.next_step }))));
}

function renderPhrase() {
  if (state.phase !== "live" || !state.phrase) return;
  const words = state.phrase.split(" ");
  swap("c-phrase", el("section", { class: "step" },
    el("h2", { text: state.phrase_heard ? "They said it live." : `First, ask ${other()} to say these three words` }),
    el("p", { class: `phrase small-phrase${state.phrase_heard ? " heard" : ""}`, "aria-label": state.phrase }, words.map((word) => el("span", { text: word }))),
    el("p", { class: "sub", text: state.phrase_heard
      ? "They were only shown these words when the call began, so this voice is not a recording."
      : "They see these words only now, so a recording made earlier can't hold them." })));
}

function renderGuard() {
  if (state.phase === "ended") return swap("c-guard");
  if (state.guarding) {
    return swap("c-guard", el("section", { class: "guard" },
      el("p", { class: "wait", role: "status" }, el("i"), el("span", { text: "Muse Wonder is trying to guess these questions without the chat" }))));
  }
  const guard = state.guard;
  if (!guard) return swap("c-guard");
  const name = modelName(guard.by);
  const count = guard.guessed.length;
  const headline = count
    ? `${name} guessed ${count} of ${guard.tried} without ever seeing the chat. ${count === 1 ? "That question is" : "Those are"} gone.`
    : `${name} tried all ${guard.tried} without the chat, and missed every one.`;
  swap("c-guard", el("section", { class: "guard" },
    el("h2", { text: headline }),
    el("p", { class: "sub", text: "It played a scammer who was given only a name, a job and a city. A question that can be guessed proves nothing." }),
    guard.guessed.map((item) => el("div", { class: "guessed" },
      el("s", { text: item.question }),
      el("p", {}, el("span", { class: "label", text: "Its guess " }), el("q", { text: item.spoken }))))));
}

function renderQuestions() {
  if (state.phase === "ended") return swap("c-questions");
  const live = state.phase === "live";
  const list = el("ol", { class: "questions" }, state.questions.map((item) => {
    const card = cards.get(item.id);
    const status = card ? RESULTS[card.label] || RESULTS.unscored : null;
    const waiting = asked.has(item.id) && !card;
    return el("li", { class: card ? `done ${status[1]}` : "" },
      el("p", { class: "q", text: item.text }),
      el("p", { class: "from" }, el("span", { class: "label", text: "They wrote " }), el("q", { text: item.quote })),
      live && !card ? el("button", { class: "btn btn-line btn-small", text: waiting ? (state.simulated ? "Answering" : "Listening for the answer") : "Ask",
        disabled: busy.has(`ask${item.id}`) || (waiting && state.simulated), onclick: () => ask(item) }) : null,
      status ? el("p", { class: `tag ${status[1]}`, text: status[0] }) : null);
  }));
  swap("c-questions", el("section", { class: "step" },
    el("h2", { text: live ? "Ask these, in your own words" : "The questions" }),
    el("p", { class: "sub", text: live
      ? (state.simulated ? "Tap Ask, and the simulated person answers out loud on the call." : "Say a question aloud and we notice, or tap Ask first.")
      : "Written from their own words. You'll see each answer beside what they wrote." }),
    list));
}

function ask(item) {
  return act(`ask${item.id}`, async () => {
    await post("ask", { question: item.id });
    asked.add(item.id);
    renderQuestions();
    if (state.simulated) await post("play", { question: item.id });
  });
}

function renderLive() {
  if (state.phase !== "live") return;
  const lines = captions.slice(-MAX_CAPTIONS).map((line) => el("p", { class: `line ${line.who}` },
    el("b", { text: line.who === "you" ? "You" : other() }), line.text));
  if (partial) lines.push(el("p", { class: `line ${partial.who} partial` }, el("b", { text: partial.who === "you" ? "You" : other() }), partial.text));
  const extras = state.simulated ? el("div", { class: "adders" },
    el("button", { class: "chip", text: "Ask about their work", disabled: busy.has("job"), onclick: () => act("job", () => post("play", { line: "job" })) }),
    el("button", { class: "chip", text: "Have them bring up money", disabled: busy.has("money"), onclick: () => act("money", () => post("play", { line: "money" })) })) : null;
  swap("c-live", el("section", { class: "step" },
    el("h2", { text: "On the call" }),
    el("div", { class: "captions", "aria-live": "polite" }, lines.length ? lines : el("p", { class: "line quiet", text: "Captions appear here as you talk." })),
    el("div", { class: "adders" },
      el("button", { class: `mic${mic ? "" : " off"}`, id: "mic", onclick: toggleMic }, el("i"), mic ? "Your mic is on" : "Use my mic, so questions you say are noticed")),
    extras));
}

function renderCards() {
  const shown = [...cards.values()].map((card) => {
    const status = RESULTS[card.label] || RESULTS.unscored;
    return el("div", { class: `break result-card ${status[1]}` },
      el("div", {}, el("div", { class: "label", text: "They wrote" }), el("q", { text: card.quote })),
      el("div", {}, el("div", { class: "label", text: "On the call" }), el("q", { text: card.said || "Nothing yet" })),
      el("p", { class: `tag ${status[1]}`, text: `${card.text} ${status[0]}.` }));
  });
  const lines = differs.map((item) => el("div", { class: "break result-card differs" },
    el("div", {}, el("div", { class: "label", text: `They wrote, about ${item.topic}` }), el("q", { text: item.quote })),
    el("div", {}, el("div", { class: "label", text: "On the call" }), el("q", { text: item.said })),
    el("p", { class: "tag differs", text: "This can't be true at the same time as what they wrote." })));
  if (!shown.length && !lines.length) return swap("c-cards");
  swap("c-cards", el("section", { class: "step" }, el("h2", { text: "Old words beside new words" }), shown, lines));
}

function renderReceipt() {
  if (state.phase !== "ended") return;
  if (!receipt) return swap("c-receipt", el("p", { class: "wait", role: "status" }, el("i"), el("span", { text: "Adding it up" })));
  const answer = receipt.answer;
  const reveal = receipt.person ? el("section", { class: "reveal-who" },
    el("p", { class: "eyebrow", text: "Who answered" }),
    el("h2", { text: receipt.person === "writer"
      ? "This was the person who wrote the chat."
      : "This was a stand-in who never saw the chat." }),
    el("p", { class: "sub", text: "A simulated call picks one at random, so you can see both." })) : null;
  swap("c-receipt",
    el("p", { class: "verdict", text: answer.headline }),
    answer.proof.length ? el("ul", { class: "receipt" }, answer.proof.map((line) => el("li", { text: line }))) : null,
    el("p", { class: "lede", text: answer.next_step }),
    reveal,
    el("div", { class: "actions" }, backLink() ? el("a", { class: "btn btn-red", href: backLink(), text: "Back to the match" }) : null,
      state.simulated ? el("button", { class: "link", text: "Try another simulated call", onclick: again }) : null));
}

async function again() {
  const reply = await api("/api/proof/demo", {});
  const from = remember("call-from");
  if (from && from.call === token) remember("call-from", { call: reply.token, match: from.match });
  location.href = `/proof/${reply.token}`;
}

function backLink() {
  const from = remember("call-from");
  return from && from.call === token && from.match ? `/match#${from.match}` : "";
}

// The guest only ever sees the consent, the phrase and the end.

function renderGuest() {
  if (!state) return;
  const parts = [el("p", { class: "eyebrow", text: "Proof Call" })];
  if (state.phase === "waiting") {
    parts.push(el("h1", { class: "headline", text: "You're invited to a short call." }),
      el("p", { class: "consent", text: state.consent_text }),
      state.consented
        ? el("p", { class: "wait", role: "status" }, el("i"), el("span", { text: "Waiting for the other person to start" }))
        : el("div", { class: "actions" },
          el("button", { class: "btn btn-red", text: "I agree. Join the call", disabled: busy.has("consent"), onclick: () => act("consent", async () => {
            let micError = "";
            await startMic().catch((error) => { micError = error.message; });
            await post("consent");
            state.consented = true;
            if (micError) throw new Error(micError);
          }) }),
          el("a", { class: "link", href: "/", text: "No thanks" })));
  } else if (state.phase === "live") {
    parts.push(el("h1", { class: "headline", text: "Say these three words first." }),
      el("p", { class: "phrase", "aria-label": state.phrase || "" }, (state.phrase || "").split(" ").map((word) => el("span", { text: word }))),
      el("p", { class: "lede", text: "Then talk as normal. You can leave at any time." }),
      el("div", { class: "adders" }, el("span", { class: `mic${mic ? "" : " off"}`, id: "mic" }, el("i"), mic ? "Your mic is on" : "Your mic is off"),
        mic ? null : el("button", { class: "link", text: "Turn it on", onclick: () => act("mic", startMic) })),
      el("div", { class: "actions" }, el("button", { class: "btn btn-line", text: "Leave the call", onclick: end })));
  } else {
    parts.push(el("h1", { class: "headline", text: "The call has ended." }), el("p", { class: "lede", text: "Nothing was recorded. Thank you for joining." }));
  }
  parts.push(el("p", { class: "error", role: "alert", text: note }));
  root.replaceChildren(...parts.filter(Boolean));
}

// The mic. Chunks go only while the call is live, so nothing is heard before both people agree.

async function startMic() {
  if (mic) return;
  sender = audioSender(`/api/proof/${token}/audio`);
  try {
    mic = await openMic((pcm) => {
      if (state.phase === "live") sender.push(pcm);
    }, (level) => {
      const dot = document.querySelector("#mic i");
      if (dot) dot.style.setProperty("--level", String(1 + Math.min(1.2, level * 6)));
    });
  } catch (error) {
    sender.stop();
    sender = null;
    throw new Error("We couldn't use the mic. Check the browser's permission.");
  }
}

function stopMic() {
  if (mic) mic.stop();
  if (sender) sender.stop();
  mic = null;
  sender = null;
}

async function toggleMic() {
  if (mic) {
    stopMic();
    renderLive();
    return;
  }
  await act("mic", startMic);
}

async function end() {
  await act("end", () => post("finish"));
}

// Events, held open by the server so cards arrive the moment they exist.

function handle(event) {
  switch (event.type) {
    case "joined": state.other_joined = true; break;
    case "live":
      state.phase = "live";
      state.phrase = event.phrase;
      render();
      return;
    case "partial": partial = { who: event.who, text: event.text }; return renderLive();
    case "caption":
      captions.push({ who: event.who, text: event.text });
      partial = null;
      return renderLive();
    case "phrase_ok":
      state.phrase_heard = true;
      buzz(30);
      return renderPhrase();
    case "asked": asked.add(event.question); return renderQuestions();
    case "answer":
      cards.set(event.question, event);
      buzz(20);
      renderQuestions();
      return renderCards();
    case "differs": differs.push(event); return renderCards();
    case "alarm":
      alarms.push(event);
      buzz([80, 60, 80]);
      renderAlarms();
      $("c-alarms").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    case "captions_off": note = "Live captions stopped. Cards already shown still stand."; break;
    case "guarded":
      state.guard = event.guard;
      state.questions = event.questions;
      state.guarding = false;
      renderGuard();
      return renderQuestions();
    case "unguarded": state.guarding = false; return renderGuard();
    case "receipt":
      receipt = event;
      state.phase = "ended";
      stopMic();
      render();
      return;
    case "ended":
      state.phase = "ended";
      stopMic();
      render();
      return;
    default: return;
  }
  renderParts();
}

async function listen() {
  for (;;) {
    try {
      const reply = await api(`/api/proof/${token}/events?after=${seq}&wait=20`);
      seq = reply.next;
      reply.events.forEach(handle);
      if (state.phase === "ended" && (receipt || state.role === "guest")) return;
    } catch (error) {
      await sleep(1500);
    }
  }
}

async function start() {
  try {
    state = await api(`/api/proof/${token}/state`);
  } catch (error) {
    root.replaceChildren(el("p", { class: "eyebrow", text: "Proof Call" }),
      el("h1", { class: "headline", text: "This call has ended or expired." }),
      el("p", { class: "lede", text: "Calls are forgotten within the hour." }));
    return;
  }
  if (backLink()) {
    $("back").href = backLink();
    $("back").hidden = false;
  }
  render();
  listen();
}

start();
