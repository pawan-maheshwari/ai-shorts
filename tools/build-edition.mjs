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

   Each dispatch also gets an AIShorts brief — a short summary in our own
   words plus "why it matters" and "what to watch" — written by a model on
   GitHub Models using the workflow's own GITHUB_TOKEN (no API key, free
   within rate limits). Briefs are optional: with no token, a rate limit or
   a bad answer, the dispatch simply keeps the publisher's own line.

   No dependencies — plain Node 22, so CI needs no install step.
   ===================================================================== */

import fs from "node:fs";
import path from "node:path";
import https from "node:https";
import crypto from "node:crypto";
import {pathToFileURL} from "node:url";

const SITE     = "https://aishorts.biz";
const DOMAIN   = "aishorts.biz";
const MAX      = 12;                 // front-page dispatches from the main wire
const MIN_SEC  = 3;                  // every section tab carries at least this many
const KEEP     = 120;                // editions to retain on disk
const FRESH_D  = 7;                  // discard anything older than this
const RSS = "https://news.google.com/rss/search?q="
          + encodeURIComponent("artificial intelligence when:5d")
          + "&hl=en-US&gl=US&ceid=US:en";

const gnews = q => "https://news.google.com/rss/search?q="
          + encodeURIComponent(q + " when:5d") + "&hl=en-US&gl=US&ceid=US:en";

/* One targeted query per section tab on the front page. The main wire is
   sorted into sections by keyword, which on most days leaves some tabs
   empty; any section short of MIN_SEC is topped up from its own query.
   Keys must match CATS in index.html. */
const SECTIONS = {
  Models:         "AI model launch OR release (OpenAI OR Anthropic OR Gemini OR Llama OR Mistral)",
  Business:       "AI startup funding OR acquisition OR earnings OR IPO",
  Policy:         "AI regulation OR policy OR law OR \"AI Act\"",
  Research:       "AI research study OR paper OR benchmark OR breakthrough",
  Safety:         "AI safety OR \"AI security\" OR deepfake OR alignment",
  Robotics:       "AI robotics OR humanoid robot OR \"self-driving\"",
  Infrastructure: "AI data center OR chips OR GPU OR compute"
};

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

function toEdition(raw, day, {seen = new Set(), limit = MAX, category = ""} = {}){
  const items = [];
  const cutoff = Date.now() - FRESH_D * 86400000;

  for(const r of raw){
    if(items.length >= limit) break;
    const key = r.title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if(!key || seen.has(key)) continue;
    const when = new Date(r.pub);
    if(!isNaN(when) && when.getTime() < cutoff) continue;
    seen.add(key);

    let summary = clipWords(dedupeSummary(r.title, r.desc), 42);
    if(summary.length < 36) summary = `Open the full report at ${r.source} for the complete story.`;

    items.push({
      id: idFor(r.link || r.url, r.title, day),   // feed link: stable across re-runs
      headline: r.title,
      summary,
      source: r.source,
      url: r.url || r.link,
      category: category || guessCat(r.title + " " + r.desc),
      region: "Global",
      date: iso(when),
      note: "via Google News"
    });
  }
  return items;
}

/* ------------------------------------------------- publisher + material */
const UA_BOT = "Mozilla/5.0 (compatible; AIShortsBot/1.0; +https://aishorts.biz)";
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function fetchText(url, {method = "GET", headers = {}, body, timeout = 9000, max = 600000} = {}){
  const r = await fetch(url, {method, body, redirect: "follow",
    headers: {"User-Agent": UA_BOT, "Accept-Language": "en-US,en;q=0.8", ...headers},
    signal: AbortSignal.timeout(timeout)});
  const text = (await r.text()).slice(0, max);
  return {ok: r.ok, status: r.status, url: r.url, text};
}

/* Google News article links no longer redirect; the publisher's address
   comes back from a small signed lookup. Any failure returns "" and the
   caller keeps the Google link, which still opens the story. */
async function decodeGoogleNews(link){
  const m = String(link).match(/\/articles\/([^?/#]+)/);
  if(!m) return "";
  const id = m[1];
  let sg = "", ts = "";
  for(const u of [`https://news.google.com/articles/${id}`, `https://news.google.com/rss/articles/${id}`]){
    try{
      const page = await fetchText(u);
      sg = (page.text.match(/data-n-a-sg="([^"]+)"/) || [])[1] || "";
      ts = (page.text.match(/data-n-a-ts="([^"]+)"/) || [])[1] || "";
      if(sg && ts) break;
    }catch(e){ /* try the next form */ }
  }
  if(!sg || !ts) return "";
  const inner = JSON.stringify(["garturlreq",
    [["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],
    id, Number(ts), sg]);
  const r = await fetchText("https://news.google.com/_/DotsSplashUi/data/batchexecute", {
    method: "POST",
    headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    body: "f.req=" + encodeURIComponent(JSON.stringify([[["Fbv4je", inner, null, "generic"]]]))
  });
  try{
    const outer = JSON.parse(r.text.split("\n\n")[1]);
    const url = JSON.parse(outer[0][2])[1];
    if(/^https?:\/\//.test(url) && !/news\.google\.com/.test(url)) return url;
  }catch(e){ /* fall through to a plain scan */ }
  const scan = r.text.match(/garturlres\\?",\\?"(https?:[^"\\]+)/);
  return scan ? scan[1] : "";
}

const metaTag = (html, key) => {
  const a = html.match(new RegExp(`<meta[^>]+(?:property|name)=["']${key}["'][^>]*content=["']([^"']+)["']`, "i"))
         || html.match(new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["']${key}["']`, "i"));
  return a ? strip(a[1]) : "";
};

/* What the brief may draw on: the publisher's own description and the
   opening paragraphs. Read once, handed to the model, never stored. */
export function extractArticle(html){
  const h = String(html || "");
  const desc = metaTag(h, "og:description") || metaTag(h, "description") || metaTag(h, "twitter:description");
  let image = metaTag(h, "og:image") || metaTag(h, "twitter:image");
  if(!/^https:\/\//.test(image)) image = "";
  const body = h.replace(/<(script|style|noscript|nav|header|footer|aside|form|figure)[\s\S]*?<\/\1>/gi, " ");
  const paras = [];
  let n = 0;
  for(const m of body.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/gi)){
    const t = strip(m[1]);
    if(t.length < 80 || /cookie|subscribe|newsletter|all rights reserved|sign up|javascript/i.test(t)) continue;
    paras.push(t); n += t.length;
    if(n > 1800) break;
  }
  return {desc, image, text: paras.join(" ").slice(0, 1800)};
}

async function pool(list, size, fn){
  let i = 0;
  await Promise.all(Array.from({length: Math.min(size, list.length)}, async () => {
    while(i < list.length){ const it = list[i++]; try{ await fn(it); }catch(e){ /* per-item failure is fine */ } }
  }));
}

/* Resolve each dispatch to its publisher and gather the brief's material.
   Material lives in a side map so it can never leak into the edition. */
async function gather(items){
  const material = new Map();
  await pool(items, 3, async it => {
    if(/news\.google\.com/.test(it.url)){
      const u = await decodeGoogleNews(it.url);
      if(u) it.url = u;
      await sleep(250);
    }
    if(/news\.google\.com/.test(it.url)) return;
    const page = await fetchText(it.url, {timeout: 8000});
    if(!page.ok) return;
    const a = extractArticle(page.text);
    if(a.image && !it.image) it.image = a.image;
    const text = [a.desc, a.text].filter(Boolean).join(" ").trim();
    if(text.length >= 120) material.set(it.id, text);
  });
  return material;
}

/* ---------------------------------------------------------- AI briefs */
const BRIEF_ENDPOINT = process.env.BRIEF_ENDPOINT || "https://models.github.ai/inference/chat/completions";
const BRIEF_MODEL    = process.env.BRIEF_MODEL    || "openai/gpt-4o-mini";
const BRIEF_BATCH    = 3;                 // dispatches per request
const BRIEF_GAP_MS   = Number(process.env.BRIEF_GAP_MS || 4500);   // free tier allows ~15 requests a minute

const BRIEF_SYSTEM = `You write story briefs for AIShorts, a daily artificial-intelligence news wire.
For each story you are given a headline, the publisher and, when available, source material from the publisher's page.

Rules:
- Use ONLY facts stated in the headline and source material. Never add names, numbers, dates, quotes or claims from memory.
- Write in your own words. Do not copy sentences from the material. No quotation marks.
- Neutral, plain English. No hype, no opinion on whether something is good or bad, no first person.
- "brief": 40 to 55 words on what happened. If a story has no source material, write 18 to 28 words that restate only what the headline establishes.
- "why": one sentence of at most 18 words on who is affected or what changes. Reasoned from the facts given, not new facts.
- "watch": one sentence of at most 18 words on the next step the material itself points to. Use "" if it points to none.

Reply with JSON only: {"briefs":[{"id":"<id>","brief":"...","why":"...","watch":"..."}]}`;

const words = s => String(s || "").trim().split(/\s+/).filter(Boolean);
const numbersIn = s => (String(s || "").match(/\d[\d,.]*\d|\d/g) || []).map(n => n.replace(/[,.]+$/, "").replace(/,/g, ""));

/* Keep a brief only if it is the right shape and every figure in it also
   appears in what the model was shown — a cheap guard against invention. */
export function checkBrief(b, it, material){
  if(!b || typeof b.brief !== "string") return null;
  const clean = s => String(s || "").replace(/\s+/g, " ").replace(/^["'\u201c]+|["'\u201d]+$/g, "").trim();
  const brief = clean(b.brief), why = clean(b.why), watch = clean(b.watch);
  const n = words(brief).length;
  if(n < 12 || n > 75) return null;
  if(/https?:\/\//i.test(brief + why + watch)) return null;
  const shown = numbersIn(`${it.headline} ${it.source} ${material || ""}`);
  for(const x of numbersIn(`${brief} ${why} ${watch}`)) if(!shown.includes(x)) return null;
  return {
    brief,
    why:   words(why).length   >= 4 && words(why).length   <= 28 ? why   : "",
    watch: words(watch).length >= 4 && words(watch).length <= 28 ? watch : ""
  };
}

async function writeBriefs(items, material, token){
  const stats = {model: BRIEF_MODEL, attempted: 0, written: 0, fromSource: 0, reused: 0};
  const todo = items.filter(it => !it.briefBy);
  stats.reused = items.length - todo.length;
  if(!token || !todo.length) return stats;

  for(let i = 0; i < todo.length; i += BRIEF_BATCH){
    const batch = todo.slice(i, i + BRIEF_BATCH);
    stats.attempted += batch.length;
    const user = batch.map(it => {
      const m = material.get(it.id);
      return `ID: ${it.id}\nHEADLINE: ${it.headline}\nPUBLISHER: ${it.source}\nSOURCE MATERIAL: ${m ? m : "(none — headline only)"}`;
    }).join("\n\n---\n\n");

    let res;
    try{
      res = await fetch(BRIEF_ENDPOINT, {
        method: "POST",
        headers: {"Authorization": `Bearer ${token}`, "Content-Type": "application/json",
                  "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        body: JSON.stringify({model: BRIEF_MODEL, temperature: 0.2, max_tokens: 900,
          response_format: {type: "json_object"},
          messages: [{role: "system", content: BRIEF_SYSTEM}, {role: "user", content: user}]}),
        signal: AbortSignal.timeout(45000)
      });
    }catch(e){ console.warn(`Briefs: request failed (${e.message}).`); continue; }

    if(res.status === 429 || res.status === 401 || res.status === 403 || res.status === 404){
      const why = (await res.text()).slice(0, 200).replace(/\s+/g, " ");
      console.warn(`Briefs: stopping, HTTP ${res.status} — ${why}`);
      stats.stopped = res.status;
      break;                               // out of quota or not permitted: no point retrying
    }
    if(!res.ok){ console.warn(`Briefs: HTTP ${res.status}, skipping this batch.`); continue; }

    try{
      const data = await res.json();
      const txt = String(data.choices?.[0]?.message?.content || "").replace(/^```(?:json)?|```$/g, "").trim();
      const out = JSON.parse(txt).briefs || [];
      for(const it of batch){
        const ok = checkBrief(out.find(o => o && o.id === it.id), it, material.get(it.id));
        if(!ok) continue;
        it.summary = ok.brief;
        if(ok.why)   it.why = ok.why;
        if(ok.watch) it.watch = ok.watch;
        it.briefBy = "ai";
        it.briefFrom = material.has(it.id) ? "source" : "headline";
        it.note = "Brief by AIShorts AI \u00b7 via Google News";
        stats.written++;
        if(material.has(it.id)) stats.fromSource++;
      }
    }catch(e){ console.warn(`Briefs: could not read the answer (${e.message}).`); }

    if(i + BRIEF_BATCH < todo.length) await sleep(BRIEF_GAP_MS);
  }
  return stats;
}

/* A re-run on the same day keeps the briefs already written, so manual
   runs don't spend the daily allowance twice. */
function reuseBriefs(items, day){
  try{
    const prev = JSON.parse(fs.readFileSync(`editions/${day}.json`, "utf8"));
    const by = new Map((prev.items || []).filter(p => p.briefBy === "ai").map(p => [p.id, p]));
    for(const it of items){
      const p = by.get(it.id);
      if(!p) continue;
      for(const k of ["summary", "why", "watch", "briefBy", "briefFrom", "note", "url", "image"]) if(p[k]) it[k] = p[k];
    }
  }catch(e){ /* no earlier edition today */ }
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
    <rights type="text">${it.briefBy === "ai"
      ? `Headline belongs to ${xesc(it.source)}. Brief written by AIShorts with AI assistance. Linked, not republished.`
      : `Headline and summary belong to ${xesc(it.source)}. Linked, not republished.`}</rights>
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

  const fixture = args.has("--fixture");
  const seen = new Set();

  let raw;
  if(fixture){
    raw = parseItems(fs.readFileSync("tools/fixture-rss.xml", "utf8"));
  }else{
    const r = await get(RSS);
    raw = parseItems(r.body);
  }

  const items = toEdition(raw, day, {seen});
  if(items.length < 3){
    console.error(`Only ${items.length} usable dispatches — keeping the previous edition.`);
    process.exit(0);                      // leave yesterday's files in place
  }

  /* Top up any section tab the main wire left thin. A failed section query
     is logged and skipped — it must never cost us the whole edition. */
  for(const [cat, q] of Object.entries(SECTIONS)){
    const need = MIN_SEC - items.filter(it => it.category === cat).length;
    if(need <= 0) continue;
    try{
      let more;
      if(fixture){
        const f = `tools/fixture-rss-${cat.toLowerCase()}.xml`;
        more = fs.existsSync(f) ? parseItems(fs.readFileSync(f, "utf8")) : [];
      }else{
        more = parseItems((await get(gnews(q))).body).slice(0, need * 4);
      }
      const added = toEdition(more, day, {seen, limit: need, category: cat});
      items.push(...added);
      if(added.length < need) console.warn(`Section ${cat}: only ${added.length} of ${need} top-up dispatches found.`);
    }catch(e){
      console.warn(`Section ${cat}: top-up failed (${e.message}).`);
    }
  }

  /* Briefs. Everything here is best-effort: a failure leaves the dispatch
     with the line it already has. */
  let briefs = {model: BRIEF_MODEL, attempted: 0, written: 0, fromSource: 0, reused: 0};
  try{
    reuseBriefs(items, day);
    const token = process.env.GITHUB_TOKEN || "";
    const fresh = items.filter(it => !it.briefBy);
    const material = (fixture || !fresh.length) ? new Map() : await gather(fresh);
    if(fixture && fs.existsSync("tools/fixture-material.json")){
      const fm = JSON.parse(fs.readFileSync("tools/fixture-material.json", "utf8"));
      for(const it of fresh) if(fm[it.headline]) material.set(it.id, fm[it.headline]);
    }
    briefs = await writeBriefs(items, material, token);
    briefs.publisherLinks = items.filter(it => !/news\.google\.com/.test(it.url)).length;
  }catch(e){
    console.warn(`Briefs: skipped (${e.message}).`);
  }

  const tally = Object.keys(SECTIONS)
    .map(c => `${c} ${items.filter(it => it.category === c).length}`).join(", ");

  const edition = {
    edition: day,
    updated: new Date().toISOString(),
    source: "Google News · artificial intelligence",
    count: items.length,
    briefs,
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
  console.log(`Sections: ${tally}`);
  console.log(`Briefs: ${briefs.written} written (${briefs.fromSource} from source text), ${briefs.reused} reused, `
            + `${briefs.publisherLinks ?? 0} publisher links, model ${briefs.model}.`);
}

if(process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href){
  main().catch(e => { console.error(e); process.exit(1); });
}
