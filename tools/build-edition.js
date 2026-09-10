#!/usr/bin/env node
/* =====================================================================
   AIShorts — edition + Atom feed builder
   =====================================================================
   Runs once a day in CI and writes the edition as data, so that:
     - readers get a page that is already assembled (no CORS proxies in
       their browser, and everyone sees the same edition)
     - editions/ becomes the back-catalogue for the archive
     - feed.xml exists at all, which it cannot when the edition is only
       ever built inside a reader's browser

   Outputs
     editions/YYYY-MM-DD.json   the day's dispatches
     editions/latest.json       a copy; the front page reads this
     editions/index.json        every edition on file, newest first
     feed.xml                   Atom 1.0

   No dependencies — plain Node, so CI needs no install step.
   ===================================================================== */

import fs from "node:fs";
import path from "node:path";
import https from "node:https";
import crypto from "node:crypto";

const SITE     = "https://aishorts.biz";
const DOMAIN   = "aishorts.biz";
const MAX      = 12;                 // dispatches per edition
const KEEP     = 120;                // editions to retain on disk
const FRESH_D  = 7;                  // discard anything older than this
const RSS = "https://news.google.com/rss/search?q="
          + encodeURIComponent("artificial intelligence when:5d")
          + "&hl=en-US&gl=US&ceid=US:en";

/* ------------------------------------------------------------- fetching */
function get(url, {hops = 0, method = "GET", timeout = 12000} = {}){
  return new Promise((resolve, reject) => {
    const req = https.request(url, {
      method,
      headers: {"User-Agent": "Mozilla/5.0 (compatible; AIShortsBot/1.0; +https://aishorts.biz)"}
    }, r => {
      const loc = r.headers.location;
      if([301,302,303,307,308].includes(r.statusCode) && loc && hops < 5){
        r.resume();
        return resolve(get(new URL(loc, url).href, {hops: hops + 1, method, timeout}));
      }
      let d = "";
      r.setEncoding("utf8");
      r.on("data", c => d += c);
      r.on("end", () => resolve({status: r.statusCode, body: d, url}));
    });
    req.setTimeout(timeout, () => { req.destroy(new Error("timeout")); });
    req.on("error", reject);
    req.end();
  });
}

/* Google News links are redirectors. Resolve to the publisher where we
   can; where we can't, keep the Google link — a working redirect beats a
   dead entry. */
async function resolvePublisher(link){
  if(!/news\.google\.com/.test(link)) return link;
  try{
    const r = await get(link, {timeout: 9000});
    const final = r.url;
    if(final && !/news\.google\.com/.test(final)) return final;
    const m = String(r.body).match(/<a[^>]+href="(https?:\/\/(?!news\.google)[^"]+)"/i);
    if(m) return m[1].replace(/&amp;/g, "&");
  }catch(e){ /* fall through */ }
  return link;
}

/* -------------------------------------------------------------- parsing */
const ENT = {"&amp;":"&","&lt;":"<","&gt;":">","&quot;":'"',"&#39;":"'","&apos;":"'","&nbsp;":" "};
const decode = s => String(s || "")
  .replace(/&(amp|lt|gt|quot|#39|apos|nbsp);/g, m => ENT[m])
  .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n));
/* Feed descriptions arrive with markup that is sometimes literal and
   sometimes entity-encoded, so strip, decode, then strip again. */
const strip = s => decode(String(s || "").replace(/<[^>]*>/g, " "))
  .replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
const tag = (block, name) => {
  const m = block.match(new RegExp(`<${name}[^>]*>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</${name}>`, "i"));
  return m ? m[1] : "";
};

/* A feed's <description> is frequently the <title> verbatim, or the title
   followed by the real blurb. Compare on letters and digits only, then drop
   the repeated opening; anything left too short becomes "". Mirrors
   dedupeSummary() in index.html — keep the two in step. */
function dedupeSummary(title, desc){
  const d = String(desc == null ? "" : desc).trim();
  if(!d) return "";
  const flat = s => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  const nt = flat(title), nd = flat(d);
  if(!nt) return d;
  if(nd === nt) return "";
  if(nd.indexOf(nt) !== 0) return d;

  const words = d.split(/\s+/);
  let acc = "", cut = 0;
  for(let i = 0; i < words.length; i++){
    acc += flat(words[i]); cut = i + 1;
    if(acc.length >= nt.length) break;
  }
  const rest = words.slice(cut).join(" ").replace(/^[\s\-\u2013\u2014:.,;)"']+/, "").trim();
  return rest.split(/\s+/).filter(Boolean).length < 6 ? "" : rest;
}

function guessCat(t){
  t = String(t || "").toLowerCase();
  if(/(robot|humanoid|autonom|self.?driv|drone|embodied)/.test(t)) return "Robotics";
  if(/(data ?cent(er|re)|gpu|chip|nvidia|compute|cluster|silicon|\bfab\b|energy|power)/.test(t)) return "Infrastructure";
  if(/(regulat|policy|\blaw\b|\bban\b|government|eu ai act|congress|court|lawsuit|export|antitrust|white house)/.test(t)) return "Policy";
  if(/(safety|alignment|jailbreak|\brisk\b|misuse|guardrail|security|hack|breach|worm|deepfake|scam)/.test(t)) return "Safety";
  if(/(research|paper|study|benchmark|arxiv|scientist|universit|dataset|breakthrough)/.test(t)) return "Research";
  if(/(model|\bgpt\b|claude|gemini|llama|\bllm\b|release|launch|fine.?tun|open.?source|open weights|reasoning)/.test(t)) return "Models";
  return "Business";
}

const clipWords = (s, n) => {
  const w = String(s || "").split(/\s+/);
  return w.length <= n ? String(s || "") : w.slice(0, n).join(" ") + "\u2026";
};

function parseItems(xml){
  const out = [];
  for(const chunk of String(xml).split(/<item[\s>]/).slice(1)){
    let title = strip(tag(chunk, "title"));
    if(!title) continue;
    let source = strip(tag(chunk, "source"));
    const m = title.match(/\s[-\u2013]\s([^-\u2013]{2,40})$/);
    if(m && !source){ source = m[1].trim(); }
    if(m) title = title.slice(0, m.index).trim();
    out.push({
      title,
      link: decode(tag(chunk, "link")).trim(),
      desc: strip(tag(chunk, "description")),
      pub:  strip(tag(chunk, "pubDate")),
      source: source || "Google News"
    });
  }
  return out;
}

/* ---------------------------------------------------------------- build */
const iso = d => (isNaN(d) ? new Date() : d).toISOString();
const idFor = (url, headline, day) =>
  `tag:${DOMAIN},${day}:story/` +
  crypto.createHash("sha1").update(String(url || headline)).digest("hex").slice(0, 16);

function toEdition(raw, day){
  const seen = new Set();
  const items = [];
  const cutoff = Date.now() - FRESH_D * 86400000;

  for(const r of raw){
    if(items.length >= MAX) break;
    const key = r.title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if(!key || seen.has(key)) continue;
    const when = new Date(r.pub);
    if(!isNaN(when) && when.getTime() < cutoff) continue;
    seen.add(key);

    let summary = clipWords(dedupeSummary(r.title, r.desc), 42);
    if(summary.length < 36) summary = `Open the full report at ${r.source} for the complete story.`;

    items.push({
      id: idFor(r.url || r.link, r.title, day),
      headline: r.title,
      summary,
      source: r.source,
      url: r.url || r.link,
      category: guessCat(r.title + " " + r.desc),
      region: "Global",
      date: iso(when),
      note: "via Google News"
    });
  }
  return items;
}

/* ----------------------------------------------------------------- atom */
const xesc = s => String(s || "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&apos;");

function atom(edition){
  const updated = edition.updated;
  const e = edition.items.map(it => `  <entry>
    <title type="text">${xesc(it.headline)}</title>
    <id>${xesc(it.id)}</id>
    <link rel="alternate" type="text/html" href="${xesc(it.url)}"/>
    <updated>${xesc(it.date)}</updated>
    <published>${xesc(it.date)}</published>
    <author><name>${xesc(it.source)}</name></author>
    <category term="${xesc(it.category)}"/>
    <summary type="text">${xesc(it.summary)}</summary>
    <rights type="text">Headline and summary belong to ${xesc(it.source)}. Linked, not republished.</rights>
  </entry>`).join("\n");

  return `<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="text">AIShorts</title>
  <subtitle type="text">All the AI That's Fit to Print — the day's artificial-intelligence wire.</subtitle>
  <id>tag:${DOMAIN},2026:feed</id>
  <link rel="self" type="application/atom+xml" href="${SITE}/feed.xml"/>
  <link rel="alternate" type="text/html" href="${SITE}/"/>
  <updated>${xesc(updated)}</updated>
  <icon>${SITE}/assets/icon-192.png</icon>
  <logo>${SITE}/assets/icon-512.png</logo>
  <author><name>AIShorts</name><uri>${SITE}/</uri></author>
  <rights type="text">© ${new Date().getUTCFullYear()} planetAI LLP. Entries link to their original publishers.</rights>
  <generator uri="${SITE}/" version="1.0">AIShorts edition builder</generator>
${e}
</feed>
`;
}

/* ------------------------------------------------------------------ run */
const args = new Set(process.argv.slice(2));

async function main(){
  const day = new Date().toISOString().slice(0, 10);

  let raw;
  if(args.has("--fixture")){
    raw = parseItems(fs.readFileSync("tools/fixture-rss.xml", "utf8"));
  }else{
    const r = await get(RSS);
    raw = parseItems(r.body);
    for(const it of raw.slice(0, MAX * 2)) it.url = await resolvePublisher(it.link);
  }

  const items = toEdition(raw, day);
  if(items.length < 3){
    console.error(`Only ${items.length} usable dispatches — keeping the previous edition.`);
    process.exit(0);                      // leave yesterday's files in place
  }

  const edition = {
    edition: day,
    updated: new Date().toISOString(),
    source: "Google News · artificial intelligence",
    count: items.length,
    items
  };

  fs.mkdirSync("editions", {recursive: true});
  fs.writeFileSync(`editions/${day}.json`, JSON.stringify(edition, null, 2));
  fs.writeFileSync("editions/latest.json", JSON.stringify(edition, null, 2));

  const all = fs.readdirSync("editions")
    .filter(f => /^\d{4}-\d{2}-\d{2}\.json$/.test(f))
    .map(f => f.slice(0, 10))
    .sort().reverse();

  for(const old of all.slice(KEEP)) fs.unlinkSync(path.join("editions", old + ".json"));

  fs.writeFileSync("editions/index.json", JSON.stringify({
    updated: edition.updated,
    editions: all.slice(0, KEEP)
  }, null, 2));

  fs.writeFileSync("feed.xml", atom(edition));

  console.log(`Edition ${day}: ${items.length} dispatches, ${all.length} on file.`);
}

main().catch(e => { console.error(e); process.exit(1); });
