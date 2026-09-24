import { readFileSync } from 'node:fs';
import { createInboxOrgan } from '../js/inbox.js';

// Synthetic observed-history data: never loads a provider or customer snapshot.
export async function layoutFixture() {
  const tickets = Array.from({ length: 12 }, (_, i) => ({
    id: `gorgias:layout-${i}`, projectionSource: true, status: 'open',
    customerName: `Review customer ${i + 1}`, fromEmail: 'review@example.test',
    subject: 'Help with a size exchange', updatedAt: '2026-09-24T10:00:00Z',
    orderId: 'test-order', statusEvents: [],
    messages: Array.from({ length: 8 }, (_, j) => ({
      id: `message-${j}`, fromName: 'Review customer', fromEmail: 'review@example.test',
      body: 'Please help me understand the exchange options for my order. '.repeat(4),
      at: '2026-09-24T10:00:00Z', channel: 'email',
    })),
    readonlyDraft: '[SENSITIVE — REVIEW BEFORE SENDING] ' + 'Please review the available exchange options and confirm the requested size before proceeding. '.repeat(22),
    draftReason: 'A long synthetic draft must not hide the customer conversation.',
    draftSourceMessageId: 'message-7',
  }));
  const shop = {
    observedHistory: true,
    projection: { ticketCount: 100, generatedAt: '2026-09-24T10:00:00Z' },
    getCapabilities: async () => ({ draftReply: false, customerDetails: false }),
    listTickets: async () => tickets,
    getTicket: async ({ ticketId }) => tickets.find(t => t.id === ticketId) || tickets[0],
  };
  const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  const { html: snapshot } = await createInboxOrgan({ shop, viewId: 'all', storage }).ready();
  // snapshot() renders tissues directly; mount() wraps them in these two slots.
  const html = snapshot
    .replace('<div class="pane-inner thread-inner">', '<div data-slot="thread"><div class="pane-inner thread-inner">')
    .replace('<section class="composer"', '</div><div data-slot="composer"><section class="composer"')
    .replace('</section></section>', '</section></div></section>');
  const source = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const themes = source.match(/<style id="(?:support-theme|buttonsbebe-brand)">[\s\S]*?<\/style>/g).join('\n');
  const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>${themes}</head><body data-support-page="inbox" data-embedded="1"><div id="inbox-root">${html}</div></body></html>`;
}
