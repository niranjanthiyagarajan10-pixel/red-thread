// Anyone with the link sees counts and dates. Never what the person said.
import { api } from "/static/app.js";
import { renderTicket } from "/static/ticket.js";

const token = location.pathname.split("/").pop();
const title = document.getElementById("card-title");
const text = document.getElementById("card-text");
const ticket = document.getElementById("ticket");

try {
  const card = await api(`/api/card/${encodeURIComponent(token)}`);
  if (card.tied) {
    title.textContent = `Knot tied ${card.date}.`;
    text.textContent = "This person answered three questions about their own life, live and out loud, after saying three random words.";
  } else {
    title.textContent = "Not tied yet.";
    text.textContent = "This person started a First Knot but hasn't finished it.";
  }
  renderTicket(ticket, card);
  ticket.hidden = false;
} catch (error) {
  title.textContent = "We can't find this card.";
  text.textContent = "The link may be wrong, or the card has expired.";
}
