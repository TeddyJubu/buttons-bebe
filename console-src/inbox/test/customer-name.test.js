import test from "node:test";
import assert from "node:assert/strict";
import { isMailboxEmail, isMailboxName, listCustomerName, messageSpeaker } from "../js/shop/clerk-ticket.js";
import { createMailbox } from "../js/mailbox.js";
import { createComposerTissue } from "../js/tissues/composer.js";

// Issue #35: a ticket with no observed name derives a Gorgias-style display
// name from the email local part (e***a@gmail.com → Estya-style), everywhere
// the customer is named: list row, thread header, composer To line.
function observedTicket(state = {}) {
  return {
    id: "gorgias:9",
    projectionSource: true,
    subject: "Re: Order",
    fromEmail: "e***a@gmail.com",
    customerName: "e***a@gmail.com",
    messages: [],
    ...state,
  };
}

test("listCustomerName derives a display name from the email local part when none is observed", () => {
  const cases = [
    ["estywa.s@gmail.com", "Estywa S"],
    ["e***a@gmail.com", "E***a"],
    ["estya@outlook.com", "Estya"],
    ["jane.doe.smith@mail.com", "Jane Doe Smith"],
    ["mika_123@yahoo.com", "Mika 123"],
  ];
  for (const [email, expected] of cases) {
    const ticket = observedTicket({ customerName: email, fromEmail: email });
    assert.equal(listCustomerName(ticket), expected, email);
  }
});

test("an observed name always wins over email derivation", () => {
  const ticket = observedTicket({ customerName: "Esty Real", fromEmail: "estywa.s@gmail.com" });
  assert.equal(listCustomerName(ticket), "Esty Real");
});

test("mailbox and shop identities never derive personas", () => {
  const mailboxTicket = observedTicket({ customerName: "helpdesk-support@agentmail.to", fromEmail: "helpdesk-support@agentmail.to" });
  assert.equal(listCustomerName(mailboxTicket), "Customer");
  const shopTicket = observedTicket({ customerName: "demo shop support", fromEmail: "helpdesk-support@agentmail.to" });
  assert.equal(listCustomerName(shopTicket), "Customer");
  const noEmail = observedTicket({ customerName: "", fromEmail: "" });
  assert.equal(listCustomerName(noEmail), "Customer");
});

test("message speakers fall back to derived names, not raw addresses", () => {
  const ticket = observedTicket({ messages: [] });
  const speaker = messageSpeaker(ticket, { from: "customer", body: "hi" });
  assert.equal(speaker.name, "E***a");
  assert.equal(speaker.email, "e***a@gmail.com");
});

test("composer To line prints the address once when the derived name equals it", () => {
  const mailbox = createMailbox();
  const composer = createComposerTissue({ mailbox });
  composer.update({
    ticket: observedTicket({ customerName: "e***a@gmail.com" }),
    capabilities: { sendReply: false },
  });
  const html = composer.render();
  const toLine = html.match(/<div class="composer-to">[\s\S]*?<\/div>/)?.[0] || "";
  const address = "e***a@gmail.com";
  const occurrences = toLine.split(address).length - 1;
  assert.equal(occurrences, 1, `To line must print the address exactly once, got ${occurrences}: ${toLine}`);
  assert.match(toLine, /E\*\*\*a/);
});

test("composer To line keeps name and address distinct when both are real", () => {
  const mailbox = createMailbox();
  const composer = createComposerTissue({ mailbox });
  composer.update({
    ticket: observedTicket({ customerName: "Esty Real", fromEmail: "estywa.s@gmail.com" }),
    capabilities: { sendReply: false },
  });
  const html = composer.render();
  const toLine = html.match(/<div class="composer-to">[\s\S]*?<\/div>/)?.[0] || "";
  assert.match(toLine, /Esty Real/);
  assert.match(toLine, /estywa\.s@gmail\.com/);
});
