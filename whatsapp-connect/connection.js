"use strict";

// Pure helpers for the WhatsApp connection lifecycle, extracted from server.js
// so the offline release gate can test them (server.js starts Express and
// Baileys on import, so it is only ever syntax-checked).

// What `state` should show after a socket close with the given Baileys
// DisconnectReason code. loggedOut wipes credentials and re-pairs via QR;
// every other close is a transient reconnect, which the console renders as
// "Connecting".
function nextStateOnClose(code, loggedOutCode) {
  return code === loggedOutCode ? "qr" : "connecting";
}

// Attempt `send`, re-trying while it fails with a retryable error — one that
// never touched the wire (sendAlert in server.js tags its pre-check failure
// this way). Covers the ~2s reconnect window so an escalation POSTed during
// a brief disconnect isn't dropped. Non-retryable failures reject immediately,
// so a genuine sendMessage failure can never double-deliver.
//
// maxWaitMs stays under the caller's HTTP timeout (processor/whatsapp_notifier.py
// uses 15s and retries on timeout): the server must always answer first, or a
// timed-out caller could re-POST while the first hold is still pending.
async function sendWithRetry(send, { intervalMs = 2000, maxWaitMs = 10000 } = {}) {
  const deadline = Date.now() + maxWaitMs;
  for (;;) {
    try {
      return await send();
    } catch (e) {
      if (!e || !e.retryable || Date.now() >= deadline) throw e;
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
  }
}

module.exports = { nextStateOnClose, sendWithRetry };
