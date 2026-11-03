// The test lab results, as counted by code in redthread/lab.py.
import { el } from "/static/app.js";

const $ = (id) => document.getElementById(id);
const KINDS = {
  overcharge: "overcharge", total_off: "padded total", not_on_menu: "item not on the menu",
  payee: "personal UPI ID", service: "service charge", nothing: "nothing",
};
const words = (list) => list.map((kind) => KINDS[kind] || kind).join(", ");

function stat(num, of, cap) {
  return el("div", { class: "stat" }, el("div", { class: "num" }, el("em", { text: String(num) }), ` of ${of}`), el("div", { class: "cap", text: cap }));
}

function bills(part) {
  const sum = part.summary;
  $("bill-stats").replaceChildren(
    stat(sum.caught, sum.planted, "planted problems caught"),
    stat(sum.false_alarms, sum.fair, "fair bills wrongly flagged"),
    stat(sum.read_exact, sum.bills, "photos read to the rupee"));
  $("bill-cases").replaceChildren(...part.rows.map((row) => el("article", { class: `case${row.caught ? "" : " missed"}` },
    el("img", { src: `/static/lab-${row.id}.jpg`, alt: `Bill made for the test: ${row.title}`, loading: "lazy", width: "800", height: "800" }),
    el("h3", { text: row.title }),
    el("p", { class: "sub", text: row.plant }),
    el("p", { class: `tag ${row.caught ? (row.planted ? "differs" : "fits") : "unclear"}`, text: row.caught
      ? (row.planted ? `Caught: ${words(row.found)}` : "Left alone, as it should be")
      : `Missed. Expected ${words(row.expected)}, found ${words(row.found)}` }),
    el("p", { class: "small", text: row.headline }))));
}

function bluff(part) {
  const sum = part.summary;
  $("bluff-stats").replaceChildren(
    stat(sum.guessed, sum.tried, "questions guessed blind, so dropped"),
    stat(sum.chats - sum.unguarded, sum.chats, "chats guarded"));
  const guessed = part.rows.flatMap((row) => row.guessed.map((item) => ({ chat: row.chat, ...item })));
  $("bluff-cases").replaceChildren(el("div", {}, guessed.map((item) => el("div", { class: "guessed" },
    el("p", { class: "label", text: `From the chat with ${item.chat}` }),
    el("s", { text: item.question }),
    el("p", {}, el("span", { class: "label", text: "Wonder's guess " }), el("q", { text: item.spoken }))))));
}

async function load() {
  try {
    const results = await (await fetch("/static/lab.json")).json();
    if (results.bills) bills(results.bills);
    if (results.bluff) bluff(results.bluff);
    $("lab-note").textContent = `Last run ${results.ran || ""}. Every chat, bill and person here is invented. Models vary from run to run, so we run it again before every release: python -m redthread.lab all`;
  } catch (error) {
    $("lab-note").textContent = "The lab results could not be loaded.";
  }
}

load();
