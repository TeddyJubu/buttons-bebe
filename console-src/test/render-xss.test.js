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
const line = f => {
  const start = source.indexOf(f);
  const end = source.indexOf("\n", start);
  assert.ok(start > 0 && end > start, `stable line: ${f}`);
  return source.slice(start, end + 1);
};
const escSrc = line("const esc=");
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
const RAW = /(?<!&lt;)<(img|script)|" onmouseover="/;
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
