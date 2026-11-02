// The public card. Only counts and dates, never what anyone said.
export function renderTicket(element, card) {
  element.textContent = "";
  const list = document.createElement("dl");
  const rows = [
    ["Knot tied", card.date],
    ["Live voice", card.live_phrase ? "Yes, three random words" : "No"],
    ["From their own life", `${card.personal} of ${card.asked} answers`],
    ["Interests", card.interests.join(", ")],
  ];
  if (card.rechecks.length) {
    const fits = card.rechecks.filter((item) => item.label === "consistent").length;
    rows.push(["Later re-checks", `${fits} of ${card.rechecks.length} fit their intro`]);
  }
  for (const [name, value] of rows) {
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  }
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = "No answers, words, voice, face or ID are on this card. It shows someone answered live, about their own life. It does not prove who they are.";
  element.append(list, note);
}
