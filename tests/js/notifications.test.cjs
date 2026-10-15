const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('hidden tabs do not poll; returning to the tab refreshes notifications', async () => {
  const events = {}, intervals = [];
  let requests = 0;
  const document = {
    hidden: true,
    body: {dataset: {notificationFeed: '/notifications/feed/', userId: 'test', notificationsSince: '2026-10-07T00:00:00Z'}, append() {}},
    addEventListener(name, callback) { events[name] = callback; },
    createElement() { return {setAttribute() {}}; },
    querySelectorAll() { return []; },
    getElementById() { return null; },
  };
  vm.runInNewContext(fs.readFileSync('static/js/notifications.js', 'utf8'), {
    document,
    navigator: {userAgent: 'Test', platform: 'Test'},
    window: {addEventListener() {}, matchMedia() { return {matches: false}; }},
    location: {href: 'https://test.localhost/'}, URL,
    localStorage: {getItem() { return null; }},
    setInterval(callback) { intervals.push(callback); },
    fetch: async () => {
      requests++;
      return {ok: true, json: async () => ({cursor: '2026-10-07T00:00:15Z', unread: 0, toasts: false, items: []})};
    },
  });
  events.DOMContentLoaded();
  await intervals[0]();
  assert.equal(requests, 0);
  document.hidden = false;
  events.visibilitychange();
  await new Promise(setImmediate);
  assert.equal(requests, 1);
  await intervals[0]();
  assert.equal(requests, 2);
  document.hidden = true;
  await intervals[0]();
  assert.equal(requests, 2);
});
