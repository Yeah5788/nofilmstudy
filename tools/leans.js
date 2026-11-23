// Runs the app's own pick engine headless and saves the picks it makes into data.json "leans",
// so the Lean Tracker keeps growing without anyone opening the app.
const fs = require("fs"), path = require("path");
const { chromium } = require("playwright");
const ROOT = path.resolve(__dirname, "..");
const DATA = path.join(ROOT, "data.json");

(async () => {
  const data = JSON.parse(fs.readFileSync(DATA, "utf8"));
  const app = fs.readFileSync(path.join(__dirname, "app.html"), "utf8");
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.on("pageerror", e => console.log("pageerror:", String(e).slice(0, 200)));
  await page.addInitScript(d => {
    const W = (window.__W = {});
    const cbs = [];
    const snap = c => ({ docs: Object.entries(d[c] || {}).map(([id, v]) => ({ id, data: () => v, exists: true })) });
    const coll = c => { const q = { orderBy: () => q, limit: () => q, where: () => q,
      onSnapshot: fn => { const f = () => fn(snap(c)); cbs.push(f); setTimeout(f, 0); return () => {}; } }; return q; };
    const doc = p => { const a = p.split("/"), c = a.slice(0, -1).join("/"), id = a[a.length - 1];
      return { onSnapshot: fn => { setTimeout(() => { const v = d[c] && d[c][id]; fn({ exists: !!v, data: () => v }); }, 0); return () => {}; },
        set: v => { if (c !== "leans") return Promise.reject({ code: "permission-denied" });
          W[id] = v; (d[c] = d[c] || {})[id] = v; return Promise.resolve(); },
        update: () => Promise.reject({ code: "permission-denied" }), delete: () => Promise.reject({ code: "permission-denied" }) }; };
    const db = { collection: coll, doc };
    window.claude = { use: n => Promise.resolve(n === "db" ? db : null) };
    window.PUBLIC_SITE = true;
  }, data);
  await page.route("https://nofilm.local/", r => r.fulfill({ contentType: "text/html", body: "<!doctype html><meta charset=utf-8>" + app }));
  await page.route(/^https?:\/\/(?!nofilm\.local).*/, r => r.request().url().includes("fonts.") || r.request().url().includes("cdnjs") ? r.continue() : r.abort());
  await page.goto("https://nofilm.local/", { waitUntil: "load", timeout: 60000 });
  // the engine snapshots picks ~4s after data settles; give it time to finish every game
  let last = -1;
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(3000);
    const n = await page.evaluate(() => Object.keys(window.__W).length);
    if (n === last && i > 3) break;
    last = n;
  }
  const W = await page.evaluate(() => window.__W);
  await browser.close();
  const ids = Object.keys(W);
  data.leans = data.leans || {};
  for (const id of ids) data.leans[id] = W[id];
  const r = (data.meta = data.meta || {}).refresh || {};
  r.note = (r.note || "") + ` Picks engine saved ${ids.length} new/changed leans.`;
  data.meta.refresh = r;
  fs.writeFileSync(DATA, JSON.stringify(data));
  console.log("leans written:", ids.length);
})().catch(e => { console.error("leans.js failed (data.json left as is):", e); process.exit(0); });
