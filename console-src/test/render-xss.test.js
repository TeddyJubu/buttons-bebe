const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(new URL("../index.html", `file://${__filename}`), "utf8");
const slice = (from, to) => {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  assert.ok(start > 0 && end > start, `stable boundary: ${from} -> ${to}`);
  return source.slice(start, end);
};
// ponytail: slices render fns out of index.html like ops-health.test.js; no DOM needed.
const line = f => {
  const start = source.indexOf(f);
  const end = source.indexOf("\n", start);
  assert.ok(start > 0 && end > start, `stable line: ${f}`);
  return source.slice(start, end + 1);
};
const fn = f => slice(f, "\n}\n").split("\n}\n")[0] + "\n}\n";
// ponytail: slices render fns out of index.html like ops-health.test.js; no DOM needed.
const escSrc = line("const esc=");
// ponytail: the legacy ticket row is gone — the tickets tab is an inbox
// iframe, so XSS pinning moves to the bridge reply text (error strings are
// the only console-controlled content reaching the iframe).
const bridgeSrc = slice("function bbSendErrorText(code){", "\nfunction bbSendTicket(");
const bridgeCtx = {};
vm.runInNewContext(`${bridgeSrc}\nthis.err=bbSendErrorText;`, bridgeCtx);
const notifSrc = slice("function waNum(jid){", "\nasync function loadNotifications");
const cardSrc = slice("function fmtWhen(iso){", "\nfunction noticesView(){");
const notifCtx = { IC: { check: "✓" }, Date };
vm.runInNewContext(
  `${escSrc}\n${line("const ago=")}\nlet notificationData=null,notificationError="",notificationMsg="",notificationBusy=false,waData=null,waMsg="",waMode=null,waNumber=null,waPoll=null;\n${notifSrc}\nthis.view=notificationsView;this.set=d=>{notificationData=d;};`,
  notifCtx,
);
const cardCtx = {};
vm.runInNewContext(
  `${escSrc}\nlet noticeDeletingId=null,noticeRemoveId=null;\n${cardSrc}\nthis.card=noticeCard;`,
  cardCtx,
);

const hostile = '<img src=x onerror=alert(1)>';
// ponytail: legacy row() is gone with the feed; keep the hostile fixture for
// the notification/notice tests below, and pin the bridge error strings.
const ticket = {
  message_id: '1" onmouseover="alert(1)',
  customer_email: `${hostile}@example.com`,
  ticket_subject: `</div>${hostile}`,
  message_text: `hello ${hostile}`,
  draft_text: `hi </textarea><script>alert(1)</script>`,
  reason: `" onmouseover="alert(1)`,
  job_status: hostile,
  priority: hostile,
  processed_at: new Date().toISOString(),
};

// ponytail: escaped text still contains "onerror=" literally (harmless); only a RAW "<tag"
// breakout matters. `&lt;img` contains "<img", so use a lookbehind for the "&lt;" prefix.
const RAW = /(?<!&lt;)<(img|script)|" onmouseover="/;
test("bridge error strings carry no markup", () => {
  for (const code of ["draft_changed_refresh_ticket", "recipient_unavailable", "previous_delivery_unresolved", "source_message_not_in_console", "delivery_unconfirmed", "no_such_code"]) {
    const s = bridgeCtx.err(code);
    assert.equal(typeof s, "string");
    assert.doesNotMatch(s, RAW);
  }
});

test("notification rows escape hostile title/customer/subject/detail", () => {
  notifCtx.set({ notifications: [{ title: hostile, customer: hostile, subject: hostile, detail: hostile, kind: "failed", severity: "error", read: false }], unread_count: 1 });
  const html = notifCtx.view();
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, RAW);
});

test("noticeCard escapes hostile id/text/author", () => {
  const html = cardCtx.card({ id: '1" onmouseover="alert(1)', text: hostile, active: true, created_by: "<script>alert(1)</script>", created_at: new Date().toISOString(), expires_at: null });
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, RAW);
});
