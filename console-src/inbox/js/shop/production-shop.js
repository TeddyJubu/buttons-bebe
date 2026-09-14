import { createHelpdeskClient } from './helpdesk-client.js';
import { ACTIVATE_SEND_MESSAGE, SEND_ACCESS_ERROR } from '../send-access.js';

/** Real inbox responses only. Provider failures never become invented records. */
export function createHelpdeskShop(opts = {}) {
  const client = opts.client || createHelpdeskClient();
  async function read(tool, args = {}) {
    const result = await client.invoke(tool, args);
    if (!result?.ok) throw new Error(result?.message || 'Inbox unavailable.');
    if (['sample', 'fixture'].includes(result.source)) throw new Error('Preview data is not available in this inbox.');
    return result;
  }
  const shop = {
    id: 'shop', shop: '', client, observedHistory: true, operatorEmail: '',
    capabilities: Object.fromEntries(['draftReply','summarizeThread','searchMacros','applyMacro','escalateTicket','markPrivacyHandled','markUnsubscribed','markBugHandled','customerDetails','sendReply'].map(key => [key, false])),
    getCapabilities: async () => {
      // Only the inbox service knows the operator. Clearing first means a
      // failed refresh leaves "Assigned to me" empty instead of stale.
      shop.operatorEmail = '';
      const result = await read('helpdesk.capabilities');
      shop.operatorEmail = typeof result.operatorEmail === 'string' ? result.operatorEmail : '';
      return result.capabilities;
    },
    listTickets: async args => {
      const result = await read('helpdesk.list_tickets', args);
      shop.projection = result.projection;
      return result.tickets;
    },
    getTicket: async args => (await read('helpdesk.get_ticket', args)).ticket,
    getCustomer: async args => (await read('helpdesk.get_customer', args)).customer,
    getOrder: async args => (await read('helpdesk.get_order', args)).order,
    getReturns: async args => await read('helpdesk.get_returns', args),
    getOrderHistory: async args => (await read('helpdesk.list_past_orders', args)).orders,
    draftReply: async args => await read('helpdesk.draft_reply', args),
    summarizeThread: async args => await read('helpdesk.summarize_thread', args),
    searchMacros: async args => await read('helpdesk.search_macros', args),
    applyMacro: async args => await read('helpdesk.apply_macro', args),
    escalateTicket: async args => (await read('helpdesk.escalate_ticket', args)).ticket,
    writeGateStatus: async args => await read('helpdesk.write_gate_status', args),
    bridgeStatus: async args => await read('helpdesk.bridge_status', args),
    sendReply: async () => ({ok: false, error: SEND_ACCESS_ERROR, message: ACTIVATE_SEND_MESSAGE}),
  };
  return shop;
}
