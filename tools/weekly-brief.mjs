#!/usr/bin/env node
/* =====================================================================
   AIShorts — AI Briefs: the weekly brief agent
   =====================================================================
   Once a week this agent reads the week's editions, chooses the top story
   for each weekday (Monday to Friday), lays the issue out in AIShorts
   branding and publishes it as a PDF anyone can download.

   What it does, in order
     1. CURATE   pool every dispatch in editions/ for the week, bucket them
                 by the day the story was published, score them and pick
                 one lead per weekday (plus up to three "also on the wire").
     2. WRITE    when the brief model is configured (BRIEF_ENDPOINT,
                 BRIEF_MODEL, BRIEF_API_KEY — the same three the daily
                 edition uses) each lead gets a brief in our own words with
                 "why it matters" and "what to watch". Without it the page
                 carries the publisher's own description, credited.
     3. DESIGN   build a 1080 x 1350 page per story plus a cover.
     4. RENDER   print the pages to PDF with headless Chrome and take a
                 cover image.
     5. PUBLISH  write briefs/<week>.pdf|html|png|json and briefs/index.json
                 (the site reads these), then, when SUPABASE_URL and
                 SUPABASE_KEY are set, mirror the PDF to Supabase Storage
                 and record the issue in the weekly_briefs table.

   Usage
     node tools/weekly-brief.mjs                 the week that just ended
     node tools/weekly-brief.mjs --week=2026-11-09   the week containing that day
     node tools/weekly-brief.mjs --offline       no network at all (no backfill,
                                                 no brief model, no Supabase)
     node tools/weekly-brief.mjs --no-render     data and HTML only
     node tools/weekly-brief.mjs --sql           print the Supabase setup SQL

   No dependencies — plain Node 22 and the Chrome already on the CI runner.
   ===================================================================== */

import fs from "node:fs";
import path from "node:path";
import {execFileSync} from "node:child_process";
import {fileURLToPath, pathToFileURL} from "node:url";

const ROOT   = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT    = path.join(ROOT, "briefs");
const SITE   = "https://aishorts.biz";
const KEEP   = 60;                       // issues kept in the repo
const BUCKET = "weekly-briefs";          // Supabase Storage bucket
const TABLE  = "weekly_briefs";          // Supabase table
const DAYS   = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];

/* Run once in the Supabase SQL editor (safe to re-run). */
export const SUPABASE_SQL = `
-- AIShorts — AI Briefs (weekly). Safe to re-run.
create table if not exists public.${TABLE} (
  week        text primary key,            -- ISO week, e.g. 2026-W46
  title       text not null,
  starts      date not null,
  ends        date not null,
  pages       int  not null default 6,
  stories     jsonb not null,              -- the five curated stories
  pdf_url     text,
  cover_url   text,
  downloads   bigint not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
alter table public.${TABLE} enable row level security;
drop policy if exists "${TABLE} are public" on public.${TABLE};
create policy "${TABLE} are public" on public.${TABLE} for select using (true);

-- Readers may add one to the download count and nothing else.
create or replace function public.brief_downloaded(p_week text)
returns bigint language sql security definer set search_path = public as $$
  update public.${TABLE} set downloads = downloads + 1 where week = p_week returning downloads;
$$;
revoke all on function public.brief_downloaded(text) from public;
grant execute on function public.brief_downloaded(text) to anon, authenticated;

-- Public bucket that holds the PDFs and cover images.
insert into storage.buckets (id, name, public) values ('${BUCKET}', '${BUCKET}', true)
on conflict (id) do update set public = true;
`.trim();

/* ------------------------------------------------------------- small helpers */
const args = Object.fromEntries(process.argv.slice(2).map(a => {
  const m = a.match(/^--([^=]+)(?:=(.*))?$/); return m ? [m[1], m[2] === undefined ? true : m[2]] : [a, true];
}));
const iso   = d => d.toISOString().slice(0, 10);
const utc   = s => new Date(s + "T00:00:00Z");
const addD  = (d, n) => new Date(d.getTime() + n * 864e5);
const ENT   = {amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " "};
const decode = s => String(s || "")
  .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
  .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
  .replace(/&(amp|lt|gt|quot|apos|nbsp);/g, (_, n) => ENT[n]);
const tidy  = s => decode(decode(String(s || "").replace(/<[^>]*>/g, " "))).replace(/\s+/g, " ").trim();
const esc   = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const norm  = s => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const readJ = f => { try{ return JSON.parse(fs.readFileSync(f, "utf8")); }catch(e){ return null; } };

/* ISO-8601 week of a date: {year, week} */
export function isoWeek(d){
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dow = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - dow);
  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return {year: t.getUTCFullYear(), week: Math.ceil(((t - y0) / 864e5 + 1) / 7)};
}

/* The Monday of the week to publish. With --week, the week containing that
   day. Otherwise the most recent week whose Friday has already begun. */
export function pickMonday(now, weekArg){
  const d = weekArg ? utc(String(weekArg)) : utc(iso(now));
  if(Number.isNaN(d.getTime())) throw new Error(`--week=${weekArg} is not a date (use YYYY-MM-DD)`);
  const dow = d.getUTCDay() || 7;                       // Mon=1 … Sun=7
  const monday = addD(d, 1 - dow);
  return (!weekArg && dow < 5) ? addD(monday, -7) : monday;
}

function rangeLabel(a, b){
  const m = d => MONTHS[d.getUTCMonth()];
  if(a.getUTCFullYear() !== b.getUTCFullYear())
    return `${a.getUTCDate()} ${m(a)} ${a.getUTCFullYear()} – ${b.getUTCDate()} ${m(b)} ${b.getUTCFullYear()}`;
  if(a.getUTCMonth() !== b.getUTCMonth())
    return `${a.getUTCDate()} ${m(a)} – ${b.getUTCDate()} ${m(b)} ${b.getUTCFullYear()}`;
  return `${a.getUTCDate()}–${b.getUTCDate()} ${m(a)} ${a.getUTCFullYear()}`;
}

/* ------------------------------------------------------------------- curate */
const TIER1 = /^(reuters|bloomberg|financial times|ft|the new york times|the wall street journal|wsj|ap news|associated press|bbc|bbc news|cnbc|the guardian|the washington post|the economist|nikkei asia|the economic times|the hindu|mint|axios|semafor|npr|politico|the times of india|business standard)$/i;
const TIER2 = /^(the verge|techcrunch|wired|ars technica|mit technology review|venturebeat|the information|business insider|fortune|forbes|nature|science|ieee spectrum|engadget|zdnet|the register|moneycontrol|ndtv|the indian express|hindustan times|cnn|cbs news|nbc news|abc news|sky news|al jazeera|time|the atlantic|quartz|rest of world|404 media|platformer|bloomberg law|euronews|dw|france 24|south china morning post|the straits times|daily maverick|news24|techcabal)$/i;
const PRIMARY = /^(openai|anthropic|blog\.google|google|google deepmind|deepmind|microsoft|the official microsoft blog|meta|about\.fb\.com|nvidia newsroom|nvidia blog|mistral ai|apple|apple newsroom|amazon|about amazon|european commission|the white house|gov\.uk|pib|nist|oecd|united nations|stanford hai)$/i;
const WEAK   = /(motley fool|jd supra|pr newswire|globenewswire|business wire|prweb|einpresswire|accesswire|newsfile|yahoo finance|seeking alpha|investorplace|zacks|benzinga|marketbeat|tipranks|stock ?titan|openpr|medium|substack)/i;
const CAMPUS = /(university|college|institute|school|campus|chronicle|daily bruin|\bnews office\b|\bedu\b)/i;
const ENTITY = /\b(openai|anthropic|google|gemini|deepmind|microsoft|meta|nvidia|apple|amazon|xai|mistral|deepseek|spacex|tesla|tsmc|samsung|softbank|alibaba|baidu|tencent|huawei|eu|european|white house|congress|senate|parliament|india|china|u\.?s\.?|uk|japan|un|g7|g20|supreme court|ftc|sec|chatgpt|claude|copilot|llama|gpt-?\d\w*)\b/i;
const EVENT  = /\b(launch(?:es|ed)?|releas(?:es|ed)|unveil(?:s|ed)?|introduc(?:es|ed|ing)|announc(?:es|ed)|acquir(?:es|ed)|acquisition|merger|rais(?:es|ed)|funding|ipo|\$?\d[\d.,]*\s?(?:billion|bn|trillion)|billion|summit|law|act|bill|ban(?:s|ned)?|regulat\w+|ruling|court|sues?|lawsuit|settle(?:s|ment)|steps down|appoint(?:s|ed)|breakthrough|record|first[- ]ever|deal|partnership|invest(?:s|ment|ing)?|approv(?:es|ed)|sign(?:s|ed)|recall|outage|breach|strike|layoffs?|export controls?|tariffs?|treaty|standard)\b/gi;
const SOFT   = /\b(opinion|commentary|column|editorial|op-ed|letters?:|letter to|review:|how to|webinar|podcast|q&a|quiz|explainer|what is|what are|guide|tips|ways to|reasons|things to|stocks? to buy|which .*stock|better buy|vs\.|should you|top \d+|best \d+|\d+ (?:best|top)|sponsored|press release|horoscope|minor|fellowship|scholarship|exploring|reflections)\b/i;
const BYLINE = /^[A-Z][A-Z .'’-]{3,24}:\s/;             // "STALLARD: …", "COMMENTARY: …"
const PRIVATE = /\b(affair|relationship with|dating|divorce|sex(?:ual)?|scandal|mistress|girlfriend|boyfriend)\b/i;

const realSummary = s => { s = tidy(s); return (s.length >= 40 && !/^Open the full report at /.test(s)) ? s : ""; };

export function score(it){
  const h = it.headline, src = String(it.source || "").trim();
  let s = 0;
  if(TIER1.test(src)) s += 12; else if(PRIMARY.test(src)) s += 9; else if(TIER2.test(src)) s += 8;
  if(WEAK.test(src)) s -= 15;
  if(CAMPUS.test(src)) s -= 6;
  if(ENTITY.test(h)) s += 6;
  const ev = new Set((h.match(EVENT) || []).map(x => x.toLowerCase())).size;
  if(ev) s += 7 + (ev > 1 ? 3 : 0);
  if(SOFT.test(h)) s -= 25;
  if(BYLINE.test(h)) s -= 20;
  if(PRIVATE.test(h)) s -= 18;
  if(/\?$/.test(h.trim())) s -= 6;                       // question headlines are rarely the news
  s += Math.max(0, 6 - it.pos) * 0.5;                    // how high the daily edition ran it
  s += Math.min(3, it.seen - 1) * 2;                     // stayed on the wire for several days
  if(realSummary(it.summary)) s += 3;
  if(it.briefBy) s += 1;
  return s;
}

/* Every dispatch on file for the week, de-duplicated, keyed by the UTC day
   the story itself was published. */
export function loadPool(monday, dir = path.join(ROOT, "editions")){
  const seen = new Map();
  for(let i = 0; i <= 6; i++){
    const day = iso(addD(monday, i));
    const ed = readJ(path.join(dir, `${day}.json`));
    if(!ed || !Array.isArray(ed.items)) continue;
    ed.items.forEach((x, pos) => {
      if(!x || !x.headline || !x.url || !x.date) return;
      const headline = tidy(x.headline);
      const key = norm(headline);
      const prev = seen.get(key);
      if(prev){ prev.seen++; prev.pos = Math.min(prev.pos, pos); if(x.briefBy && !prev.briefBy) Object.assign(prev, {summary: x.summary, why: x.why, watch: x.watch, briefBy: x.briefBy}); return; }
      seen.set(key, {id: x.id, headline, summary: x.summary || "", why: x.why || "", watch: x.watch || "", briefBy: x.briefBy || "",
        source: tidy(x.source), url: x.url, category: x.category || "AI", date: x.date, day: String(x.date).slice(0, 10), pos, seen: 1});
    });
  }
  return [...seen.values()];
}

/* Editions only reach back a few days. If a weekday has nothing on file,
   ask Google News for that date. Best effort; network failures are fine. */
async function backfill(day){
  const next = iso(addD(utc(day), 1)), prev = iso(addD(utc(day), -1));
  const url = "https://news.google.com/rss/search?q=" + encodeURIComponent(`artificial intelligence after:${prev} before:${next}`) + "&hl=en-US&gl=US&ceid=US:en";
  try{
    const r = await fetch(url, {headers: {"User-Agent": "Mozilla/5.0 (compatible; AIShortsBot/1.0; +https://aishorts.biz)"}, signal: AbortSignal.timeout(15000)});
    if(!r.ok) return [];
    const xml = await r.text(), out = [];
    xml.split(/<item[\s>]/).slice(1).forEach((b, pos) => {
      const g = n => { const m = b.match(new RegExp(`<${n}[^>]*>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</${n}>`, "i")); return m ? m[1] : ""; };
      const source = tidy(g("source")), date = new Date(g("pubDate"));
      let headline = tidy(g("title"));
      if(source && headline.endsWith(" - " + source)) headline = headline.slice(0, -(source.length + 3));
      if(!headline || Number.isNaN(date.getTime()) || iso(date) !== day) return;
      out.push({id: "backfill:" + day + ":" + pos, headline, summary: "", why: "", watch: "", briefBy: "", source,
        url: tidy(g("link")), category: "AI", date: date.toISOString(), day, pos, seen: 1});
    });
    return out;
  }catch(e){ console.warn(`Backfill ${day}: ${e.message}`); return []; }
}

const lead = h => { const m = String(h).match(ENTITY); return m ? m[0].toLowerCase() : ""; };

/* One lead per weekday. A category or company already used this week costs
   a little, so the five pages do not all tell the same kind of story. */
export function curate(pool, monday){
  const picks = [], used = new Set(), cats = {}, leads = {};
  const days = DAYS.map((name, i) => ({name, date: iso(addD(monday, i))}));
  for(const d of days){
    const cand = pool.filter(x => x.day === d.date && !used.has(norm(x.headline)))
      .map(x => ({x, s: score(x) - 4 * (cats[x.category] || 0) - 5 * (leads[lead(x.headline)] || 0)}))
      .sort((a, b) => b.s - a.s || a.x.pos - b.x.pos);
    if(!cand.length){ picks.push({...d, story: null, also: []}); continue; }
    const top = cand[0].x;
    used.add(norm(top.headline));
    cats[top.category] = (cats[top.category] || 0) + 1;
    if(lead(top.headline)) leads[lead(top.headline)] = (leads[lead(top.headline)] || 0) + 1;
    const L = lead(top.headline), stem = norm(top.headline).split(" ").filter(w => w.length > 4);
    const twin = x => (L && lead(x.headline) === L) || stem.filter(w => norm(x.headline).includes(w)).length >= 3;
    const also = cand.slice(1).filter(c => score(c.x) >= 4 && !twin(c.x)).slice(0, 4).map(c => c.x);
    const all = pool.filter(x => x.day === d.date), byCat = {};
    all.forEach(x => byCat[x.category] = (byCat[x.category] || 0) + 1);
    const stats = {count: all.length, sources: new Set(all.map(x => x.source)).size,
      cats: Object.entries(byCat).sort((a, b) => b[1] - a[1]).slice(0, 4)};
    picks.push({...d, story: top, also, stats, score: cand[0].s});
  }
  /* A day with nothing on file takes the strongest unused story of the week,
     shown under its own date, so the issue still has five pages. */
  for(const p of picks.filter(p => !p.story)){
    const spare = pool.filter(x => !used.has(norm(x.headline)) && x.day >= days[0].date && x.day <= days[4].date)
      .map(x => ({x, s: score(x)})).sort((a, b) => b.s - a.s)[0];
    if(spare && spare.s >= 8){ p.story = spare.x; p.moved = true; used.add(norm(spare.x.headline)); }
  }
  return picks;
}

const sentence = s => { s = tidy(s); if(!s) return ""; if(s.length > 330) s = s.slice(0, 330).replace(/\s+\S*$/, "") + "…"; return /[.!?…”"']$/.test(s) ? s : s + "."; };

/* ------------------------------------------------------------------- design */
const ROUNDEL = `<svg class="rd" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="31" fill="#14110f"/><text x="32" y="41" font-family="Newsreader,Georgia,serif" font-size="29" font-weight="700" fill="#fff" text-anchor="middle">AI</text><path d="M13 47H28l3-6 3.5 12 3-6H51" fill="none" stroke="#E8B04B" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const PULSE = `<svg class="pulse" viewBox="0 0 900 120" aria-hidden="true"><path d="M0 70H330l34-52 44 96 36-70 22 26H900" fill="none" stroke="#E8B04B" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const CSS = `
@font-face{font-family:"Newsreader";font-style:normal;font-weight:200 800;font-display:block;src:url(../assets/fonts/newsreader-latin-opsz-normal.woff2) format("woff2");unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;}
@font-face{font-family:"Newsreader";font-style:normal;font-weight:200 800;font-display:block;src:url(../assets/fonts/newsreader-latin-ext-opsz-normal.woff2) format("woff2");unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF;}
@font-face{font-family:"Newsreader";font-style:italic;font-weight:200 800;font-display:block;src:url(../assets/fonts/newsreader-latin-opsz-italic.woff2) format("woff2");}
@font-face{font-family:"Libre Franklin";font-weight:500;font-display:block;src:url(../assets/fonts/libre-franklin-latin-500-normal.woff2) format("woff2");}
@font-face{font-family:"Libre Franklin";font-weight:600;font-display:block;src:url(../assets/fonts/libre-franklin-latin-600-normal.woff2) format("woff2");}
@font-face{font-family:"Libre Franklin";font-weight:700;font-display:block;src:url(../assets/fonts/libre-franklin-latin-700-normal.woff2) format("woff2");}
@font-face{font-family:"Libre Franklin";font-weight:800;font-display:block;src:url(../assets/fonts/libre-franklin-latin-800-normal.woff2) format("woff2");}
:root{--paper:#fdfcf9;--paper-2:#f4f2ec;--ink:#14110f;--body:#2e2a26;--meta:#6b645c;--rule:#ddd8cc;--link:#1c4a5e;--live:#a8172a;--accent:#8a6a2f;--gold:#E8B04B;--desk:#e4e0d6;
  --serif:"Newsreader",Georgia,"Times New Roman",serif;--sans:"Libre Franklin",system-ui,sans-serif;}
@page{size:1080px 1350px;margin:0;}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{background:var(--desk);font-family:var(--serif);color:var(--ink);-webkit-print-color-adjust:exact;print-color-adjust:exact;text-rendering:optimizeLegibility;}
a{color:inherit;text-decoration:none;}
.pg{position:relative;width:1080px;height:1350px;overflow:hidden;background:var(--paper);break-after:page;page-break-after:always;display:flex;flex-direction:column;}
.pg:last-of-type{break-after:auto;page-break-after:auto;}
.rd{display:block;}
.wm{font-family:var(--serif);font-weight:700;letter-spacing:-.5px;line-height:1;}
.caps{font-family:var(--sans);font-weight:800;text-transform:uppercase;}

/* ---- cover ---- */
.cover{padding:84px 92px 70px;background:
  radial-gradient(900px 720px at 104% -4%,rgba(232,176,75,.62),transparent 62%),
  radial-gradient(860px 800px at -6% 104%,rgba(168,23,42,.30),transparent 60%),
  radial-gradient(760px 640px at 106% 104%,rgba(28,74,94,.36),transparent 62%),
  radial-gradient(560px 460px at -4% -4%,rgba(232,176,75,.24),transparent 62%),#fbf6ea;}
.cover::before{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,rgba(20,17,15,.045) 0 1px,transparent 1px 216px);}
.cover>*{position:relative;}
.c-top{display:flex;justify-content:space-between;align-items:flex-start;}
.c-issue{font-size:22px;letter-spacing:5px;color:var(--ink);line-height:1.7;}
.c-issue b{display:block;color:var(--live);font-weight:800;}
.c-brand{text-align:right;}
.c-brand .row{display:flex;align-items:center;gap:16px;justify-content:flex-end;}
.c-brand .rd{width:74px;height:74px;}
.c-brand .wm{font-size:70px;}
.c-brand .sl{font-style:italic;font-size:23px;color:var(--meta);margin-top:8px;}
.c-title{margin-top:96px;}
.c-ai{font-family:var(--serif);font-weight:700;font-size:400px;line-height:.78;letter-spacing:-6px;height:300px;
  background:repeating-linear-gradient(-58deg,var(--ink) 0 6px,transparent 6px 13px);-webkit-background-clip:text;background-clip:text;color:transparent;-webkit-text-stroke:4px var(--ink);}
.pulse{display:block;width:720px;height:96px;margin:6px 0 0 -4px;}
.c-word{font-family:var(--sans);font-weight:800;font-size:168px;letter-spacing:10px;line-height:1;margin:2px 0 0 -8px;}
.c-sub{font-size:44px;line-height:1.24;font-weight:500;color:var(--link);max-width:800px;margin-top:38px;}
.c-list{margin-top:auto;border-top:3px solid var(--ink);padding-top:18px;}
.c-list .lab{font-size:19px;letter-spacing:5px;color:var(--accent);margin-bottom:10px;}
.c-row{display:flex;gap:22px;align-items:baseline;padding:7px 0;border-bottom:1px solid rgba(20,17,15,.16);}
.c-row .d{flex:0 0 74px;font-size:19px;letter-spacing:3px;color:var(--live);}
.c-row .h{flex:1;min-width:0;font-size:27px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.c-foot{display:flex;justify-content:space-between;align-items:center;margin-top:26px;font-family:var(--sans);font-size:20px;font-weight:600;color:var(--body);}
.c-foot .go{font-weight:800;font-size:34px;letter-spacing:10px;color:var(--live);}

/* ---- story pages ---- */
.story{padding:60px 84px 54px;}
.story::after{content:attr(data-n);position:absolute;right:26px;bottom:60px;font-family:var(--serif);font-weight:700;font-size:640px;line-height:.74;color:rgba(138,106,47,.10);letter-spacing:-20px;pointer-events:none;}
.story>*{position:relative;z-index:1;}
.s-top{display:flex;justify-content:space-between;align-items:center;padding-bottom:20px;border-bottom:4px solid var(--ink);box-shadow:0 7px 0 -5px var(--ink);}
.s-top .row{display:flex;align-items:center;gap:12px;}
.s-top .rd{width:46px;height:46px;}
.s-top .wm{font-size:44px;}
.s-top .iss{font-size:17px;letter-spacing:3.5px;color:var(--meta);text-align:right;line-height:1.6;}
.s-top .iss b{color:var(--live);}
.s-day{display:flex;align-items:center;gap:22px;margin-top:44px;}
.s-day .tag{background:var(--live);color:#fff;font-size:26px;letter-spacing:6px;padding:13px 20px 12px 24px;}
.s-day .dt{font-style:italic;font-size:34px;color:var(--body);}
.s-day .of{margin-left:auto;font-size:17px;letter-spacing:3.5px;color:var(--accent);}
.s-kick{margin-top:46px;font-size:21px;letter-spacing:5px;color:var(--link);display:flex;align-items:center;gap:13px;}
.s-kick::before{content:"";width:15px;height:15px;background:var(--link);}
.s-h{margin:18px 0 0;font-weight:700;letter-spacing:-.8px;line-height:1.07;}
.s-h.x0{font-size:104px;} .s-h.x1{font-size:88px;} .s-h.x2{font-size:76px;} .s-h.x3{font-size:65px;} .s-h.x4{font-size:56px;}
.s-sum{margin:30px 0 0;font-size:35px;line-height:1.4;color:var(--body);display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:6;overflow:hidden;}
.s-sum.dense{font-size:31px;}
.s-by{margin-top:10px;font-family:var(--sans);font-size:17px;font-weight:600;color:var(--meta);}
.s-pts{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:28px;}
.s-pts.one{grid-template-columns:1fr;}
.s-pt{background:rgba(244,242,236,.92);border-left:6px solid var(--gold);padding:18px 22px 20px;}
.s-pt .k{font-size:16px;letter-spacing:3.5px;color:var(--accent);}
.s-pt .v{font-size:27px;line-height:1.3;margin-top:8px;font-weight:500;}
.s-src{display:flex;align-items:center;gap:24px;margin-top:34px;}
.s-src .who{font-family:var(--sans);font-size:21px;color:var(--meta);font-weight:600;}
.s-src .who b{color:var(--ink);font-weight:800;}
.s-src .btn{font-family:var(--sans);font-weight:800;font-size:19px;letter-spacing:2px;text-transform:uppercase;color:#fff;background:var(--ink);padding:14px 22px;}
.s-also{margin-top:auto;border-top:3px solid var(--ink);padding-top:16px;}
.s-also .lab{font-size:17px;letter-spacing:4.5px;color:var(--accent);margin-bottom:6px;}
.s-also a{display:block;padding:11px 0;border-bottom:1px solid var(--rule);}
.s-also .h{font-size:27px;line-height:1.22;font-weight:600;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;}
.s-also .s{font-family:var(--sans);font-size:15px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:var(--meta);margin-top:4px;}
.s-none{margin-top:auto;}
.nb{white-space:nowrap;}
.s-src{margin-bottom:34px;}
.s-gl{margin-top:auto;margin-bottom:26px;background:var(--ink);color:#fff;padding:22px 26px 24px;}
.s-gl .lab{font-size:16px;letter-spacing:4px;color:var(--gold);}
.g-row{display:flex;gap:30px;align-items:flex-end;margin-top:12px;}
.g b{display:block;font-family:var(--serif);font-size:78px;line-height:.9;font-weight:700;}
.g span{font-family:var(--sans);font-size:15px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:rgba(255,255,255,.7);}
.g.wide{flex:1;display:grid;gap:7px;padding-left:28px;border-left:1px solid rgba(255,255,255,.25);}
.g.wide i{position:relative;display:flex;justify-content:space-between;font-style:normal;font-family:var(--sans);font-size:17px;font-weight:700;padding:4px 10px;}
.g.wide em{position:absolute;left:0;top:0;bottom:0;background:rgba(232,176,75,.35);}
.g.wide u,.g.wide s{position:relative;text-decoration:none;}
.s-gl + .s-also{margin-top:0;}
.s-foot{display:flex;justify-content:space-between;align-items:center;margin-top:24px;font-family:var(--sans);font-size:18px;font-weight:700;color:var(--meta);}
.s-foot .dots{display:flex;gap:9px;}
.s-foot .dots i{width:11px;height:11px;border-radius:50%;background:var(--rule);}
.s-foot .dots i.on{background:var(--live);}
.s-foot b{color:var(--ink);}

/* ---- on screen (briefs/<week>.html is also the "read online" view) ---- */
.bar{display:none;}
@media screen{
  body:not(.shot){padding:0 0 40px;}
  body:not(.shot) .bar{display:flex;position:sticky;top:0;z-index:5;gap:14px;align-items:center;justify-content:space-between;padding:10px 16px;background:var(--ink);color:#fff;font-family:var(--sans);font-size:14px;font-weight:700;}
  body:not(.shot) .bar a.dl{background:var(--gold);color:var(--ink);padding:9px 14px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;font-size:12px;white-space:nowrap;}
  body:not(.shot) .pg{margin:22px auto 0;box-shadow:0 10px 40px rgba(20,17,15,.22);zoom:var(--z,1);}
}
@media print{.bar{display:none!important;}}
`;

function coverPage(issue){
  const rows = issue.stories.map(s => `<div class="c-row"><span class="d caps">${esc(s.dayName.slice(0, 3))}</span><span class="h">${esc(s.headline)}</span></div>`).join("");
  return `<section class="pg cover">
  <div class="c-top">
    <div class="c-issue caps"><b>Week ${issue.number}</b>${esc(issue.range)}</div>
    <div class="c-brand"><div class="row">${ROUNDEL}<div class="wm">AIShorts</div></div><div class="sl">&ldquo;All the AI That&rsquo;s Fit to Print&rdquo;</div></div>
  </div>
  <div class="c-title"><div class="c-ai">AI</div>${PULSE}<div class="c-word">BRIEFS</div></div>
  <div class="c-sub">The week in AI, in five stories. One top story for every day, Monday to Friday.</div>
  <div class="c-list"><div class="lab caps">In this issue</div>${rows}</div>
  <div class="c-foot"><span>aishorts.biz &nbsp;&middot;&nbsp; A planetAI LLP product</span><span class="go">&rsaquo;&rsaquo;&rsaquo;&rsaquo;&rsaquo;</span></div>
</section>`;
}

function storyPage(issue, s, i){
  const n = issue.stories.length, len = s.headline.length;
  const size = len <= 58 ? "x1" : len <= 88 ? "x2" : len <= 122 ? "x3" : "x4";
  const pts = [s.why && ["Why it matters", s.why], s.watch && ["What to watch", s.watch]].filter(Boolean);
  const roomy = !s.summary && !pts.length;
  const also = s.also.slice(0, pts.length ? 2 : s.summary ? 3 : 3);
  const st = s.stats && s.stats.count >= 3 && !pts.length ? s.stats : null;
  const glLab = st && st.week ? "This week on the AIShorts wire" : `${s.dayName} on the AIShorts wire`;
  const glance = st ? `<div class="s-gl"><div class="lab caps">${esc(glLab)}</div><div class="g-row">
    <div class="g"><b>${st.count}</b><span>AI stories filed</span></div><div class="g"><b>${st.sources}</b><span>publishers</span></div>
    <div class="g wide">${st.cats.map(c => `<i><em style="width:${Math.round(100 * c[1] / st.cats[0][1])}%"></em><u>${esc(c[0])}</u><s>${c[1]}</s></i>`).join("")}</div></div></div>` : "";
  const dots = Array.from({length: n}, (_, k) => `<i${k === i ? ' class="on"' : ""}></i>`).join("");
  return `<section class="pg story" data-n="${i + 1}">
  <div class="s-top"><div class="row">${ROUNDEL}<div class="wm">AIShorts</div></div><div class="iss caps"><b>AI Briefs</b> &middot; Week ${issue.number}<br>${esc(issue.range)}</div></div>
  <div class="s-day"><span class="tag caps">${esc(s.dayName)}</span><span class="dt">${esc(s.dateLabel)}</span><span class="of caps">Story ${i + 1} of ${n}</span></div>
  <div class="s-kick caps">${esc(s.category)}${s.moved ? " &middot; also this week" : ""}</div>
  <h1 class="s-h ${roomy && len <= 70 ? {x1: "x0", x2: "x1"}[size] : size}">${esc(s.headline).replace(/(\S+-\S+)/g, '<span class="nb">$1</span>')}</h1>
  ${s.summary ? `<p class="s-sum${s.summary.length > 250 ? " dense" : ""}">${esc(s.summary)}</p><div class="s-by">${s.briefBy === "ai" ? "Brief written by AIShorts AI from the publisher&rsquo;s report" : `As described by ${esc(s.source)}`}</div>` : ""}
  ${pts.length ? `<div class="s-pts${pts.length === 1 ? " one" : ""}">${pts.map(p => `<div class="s-pt"><div class="k caps">${p[0]}</div><div class="v">${esc(p[1])}</div></div>`).join("")}</div>` : ""}
  <div class="s-src"><a class="btn" href="${esc(s.url)}">Read the full story &rarr;</a><span class="who">Reported by <b>${esc(s.source)}</b></span></div>
  ${glance}
  ${also.length ? `<div class="s-also"><div class="lab caps">Also on the wire &middot; ${esc(s.dayName)}</div>${also.map(a => `<a href="${esc(a.url)}"><div class="h">${esc(a.headline)}</div><div class="s">${esc(a.source)}</div></a>`).join("")}</div>` : `<div class="s-none"></div>`}
  <div class="s-foot"><span><b>aishorts.biz</b> &nbsp;&middot;&nbsp; All the AI that&rsquo;s fit to print</span><span class="dots">${dots}</span><span>${i + 2} / ${n + 1}</span></div>
</section>`;
}

export function buildHtml(issue){
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(issue.title)} — AIShorts</title>
<meta name="description" content="${esc(`AI Briefs from AIShorts: the top AI story for each day, ${issue.range}.`)}">
<link rel="canonical" href="${SITE}/briefs/${issue.week}.html">
<meta property="og:title" content="${esc(issue.title)}"><meta property="og:image" content="${SITE}/briefs/${issue.week}.png">
<style>${CSS}</style></head>
<body>
<div class="bar"><span>AIShorts &middot; AI Briefs &middot; Week ${issue.number}</span><a class="dl" href="${issue.week}.pdf" download="AIShorts-AI-Briefs-${issue.week}.pdf">Download PDF</a></div>
${coverPage(issue)}
${issue.stories.map((s, i) => storyPage(issue, s, i)).join("\n")}
<script>(function(){
/* fit: drop "also" items, then the stats panel, until every page fits */
document.querySelectorAll(".story").forEach(function(pg){
  var over=function(){var f=pg.querySelector(".s-foot");return f&&f.getBoundingClientRect().bottom>pg.getBoundingClientRect().bottom-40;};
  var a=pg.querySelectorAll(".s-also a"),i=a.length-1;
  while(over()&&i>0){a[i--].remove();}
  if(over()){var g=pg.querySelector(".s-gl");if(g)g.remove();}
  while(over()&&i>=0){a[i--].remove();}
  var box=pg.querySelector(".s-also");if(box&&!box.querySelector("a"))box.remove();
  if(over()){var s=pg.querySelector(".s-sum");if(s)s.style.webkitLineClamp=3;}
});
var b=document.body;if(location.hash==="#shot"){b.className="shot";return;}function z(){document.documentElement.style.setProperty("--z",Math.min(1,(innerWidth-24)/1080));}z();addEventListener("resize",z);})();</script>
</body></html>
`;
}

/* ------------------------------------------------------------------- render */
function chrome(){
  const names = [process.env.CHROME_BIN, "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"].filter(Boolean);
  for(const n of names){ try{ execFileSync(n, ["--version"], {stdio: "ignore"}); return n; }catch(e){ /* next */ } }
  throw new Error("No Chrome found. Set CHROME_BIN or install google-chrome.");
}
function render(htmlFile, pdfFile, pngFile){
  const bin = chrome(), url = pathToFileURL(htmlFile).href;
  const base = ["--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--disable-dev-shm-usage", "--virtual-time-budget=6000"];
  execFileSync(bin, [...base, "--no-pdf-header-footer", `--print-to-pdf=${pdfFile}`, url], {stdio: "pipe", timeout: 120000});
  execFileSync(bin, [...base, "--window-size=1080,1350", "--force-device-scale-factor=0.5", `--screenshot=${pngFile}`, url + "#shot"], {stdio: "pipe", timeout: 120000});
  const pdf = fs.readFileSync(pdfFile);
  if(pdf.length < 20000 || pdf.subarray(0, 5).toString() !== "%PDF-") throw new Error("Chrome did not produce a usable PDF.");
  return pdf.length;
}

/* ----------------------------------------------------------------- Supabase */
/* What kind of key this is. Only a publishable (anon) key may be written
   into briefs/index.json, where every reader can see it. */
export function keyKind(k){
  k = String(k || "");
  if(!k) return "none";
  if(k.startsWith("sb_secret_")) return "secret";
  if(k.startsWith("sb_publishable_")) return "public";
  try{
    const role = JSON.parse(Buffer.from(k.split(".")[1], "base64url").toString()).role;
    return role === "anon" ? "public" : role === "service_role" ? "secret" : "unknown";
  }catch(e){ return "unknown"; }
}

async function toSupabase(issue, files){
  const base = String(process.env.SUPABASE_URL || "").replace(/\/+$/, "").replace(/\/rest\/v1$/, "");
  const key = process.env.SUPABASE_KEY || "";
  if(!base || !key){ console.log("Supabase: SUPABASE_URL / SUPABASE_KEY not set, skipping (the PDF is still published from the site)."); return null; }
  if(keyKind(key) === "public"){ console.warn("Supabase: SUPABASE_KEY is a publishable key and cannot upload. Use the secret key (sb_secret_…) or service_role key. Skipping."); return null; }
  const auth = {Authorization: `Bearer ${key}`, apikey: key};
  const call = async (url, init) => {
    const r = await fetch(base + url, {...init, headers: {...auth, ...(init.headers || {})}, signal: AbortSignal.timeout(60000)});
    return {ok: r.ok, status: r.status, text: r.ok ? "" : (await r.text()).slice(0, 240)};
  };
  try{
    await call("/storage/v1/bucket", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id: BUCKET, name: BUCKET, public: true})});
    const urls = {};
    for(const [name, file, type] of files){
      const r = await call(`/storage/v1/object/${BUCKET}/${name}`, {method: "POST", headers: {"Content-Type": type, "x-upsert": "true", "cache-control": "3600"}, body: fs.readFileSync(file)});
      if(!r.ok){ console.warn(`Supabase: upload of ${name} failed (HTTP ${r.status}) ${r.text}`); return null; }
      urls[name] = `${base}/storage/v1/object/public/${BUCKET}/${name}`;
    }
    const row = {week: issue.week, title: issue.title, starts: issue.start, ends: issue.end, pages: issue.pages,
      stories: issue.stories.map(s => ({day: s.dayName, date: s.date, category: s.category, headline: s.headline, source: s.source, url: s.url})),
      pdf_url: urls[`${issue.week}.pdf`], cover_url: urls[`${issue.week}.png`], updated_at: new Date().toISOString()};
    const r = await call(`/rest/v1/${TABLE}?on_conflict=week`, {method: "POST", headers: {"Content-Type": "application/json", Prefer: "resolution=merge-duplicates,return=minimal"}, body: JSON.stringify(row)});
    if(!r.ok){ console.warn(`Supabase: files uploaded, but the ${TABLE} row was refused (HTTP ${r.status}) ${r.text}\n  Run the setup SQL once: node tools/weekly-brief.mjs --sql`); return {pdf: row.pdf_url, cover: row.cover_url, row: false}; }
    console.log(`Supabase: uploaded ${files.length} files and recorded ${issue.week}.`);
    return {pdf: row.pdf_url, cover: row.cover_url, row: true};
  }catch(e){ console.warn(`Supabase: ${e.message}. Skipping; the PDF is still published from the site.`); return null; }
}

/* --------------------------------------------------------------------- main */
async function main(){
  if(args.sql){ console.log(SUPABASE_SQL); return; }
  const now = args.now ? new Date(args.now) : new Date();
  const monday = pickMonday(now, args.week === true ? "" : args.week);
  const friday = addD(monday, 4);
  const {year, week: number} = isoWeek(monday);
  const week = `${year}-W${String(number).padStart(2, "0")}`;
  console.log(`AI Briefs ${week}: ${iso(monday)} to ${iso(friday)}`);

  /* 1. curate */
  let pool = loadPool(monday);
  if(!args.offline){
    for(let i = 0; i < 5; i++){
      const day = iso(addD(monday, i));
      if(!pool.some(x => x.day === day)){ const more = await backfill(day); console.log(`  ${day}: nothing on file, Google News gave ${more.length}`); pool = pool.concat(more); }
    }
  }
  const picks = curate(pool, monday).filter(p => p.story);
  const wk = pool.filter(x => x.day >= iso(monday) && x.day <= iso(friday)), wc = {};
  wk.forEach(x => wc[x.category] = (wc[x.category] || 0) + 1);
  const weekStats = {count: wk.length, sources: new Set(wk.map(x => x.source)).size, week: true,
    cats: Object.entries(wc).sort((a, b) => b[1] - a[1]).slice(0, 4)};
  for(const p of picks) if(!p.stats || p.stats.count < 3) p.stats = weekStats;
  if(picks.length < 3) throw new Error(`Only ${picks.length} of 5 days have a usable story on file for ${week}. Nothing published.`);

  /* 2. write */
  const leads = picks.map(p => p.story);
  if(!args.offline && process.env.BRIEF_API_KEY && process.env.BRIEF_ENDPOINT && process.env.BRIEF_MODEL){
    try{
      const {gather, writeBriefs} = await import("./build-edition.mjs");
      const todo = leads.filter(x => !x.briefBy);
      const {material, descs} = await gather(todo);
      for(const x of todo) if(!realSummary(x.summary) && descs.get(x.id)) x.summary = descs.get(x.id);
      const st = await writeBriefs(todo, material, process.env.BRIEF_API_KEY);
      console.log(`  briefs: ${st.written} written, ${st.reused} already had one`);
    }catch(e){ console.warn(`  briefs: ${e.message}; using the publishers' descriptions`); }
  }

  const stories = picks.map(p => {
    const x = p.story, d = utc(x.day);
    return {dayName: p.name, date: x.day, dateLabel: `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
      moved: !!p.moved, category: x.category, headline: x.headline,
      summary: x.briefBy === "ai" ? sentence(x.summary) : sentence(realSummary(x.summary)),
      why: x.briefBy === "ai" ? tidy(x.why) : "", watch: x.briefBy === "ai" ? tidy(x.watch) : "", briefBy: x.briefBy || "publisher",
      source: x.source, url: x.url,
      stats: p.stats, also: p.also.map(a => ({headline: a.headline, source: a.source, url: a.url}))};
  });
  stories.forEach(s => console.log(`  ${s.dayName.padEnd(9)} [${s.category}] ${s.headline}  (${s.source})`));

  const issue = {week, number, year, title: `AI Briefs — Week ${number}, ${rangeLabel(monday, friday)}`, range: rangeLabel(monday, friday),
    start: iso(monday), end: iso(friday), generated: now.toISOString(), pages: stories.length + 1, stories,
    method: "One lead per weekday, chosen from the AIShorts daily editions by publisher standing, the kind of event and how prominently the story ran. Each story links to the publisher's own report."};

  /* 3. design */
  fs.mkdirSync(OUT, {recursive: true});
  const f = ext => path.join(OUT, `${week}.${ext}`);
  fs.writeFileSync(f("html"), buildHtml(issue));

  /* 4. render */
  if(args["no-render"]){ fs.writeFileSync(f("json"), JSON.stringify(issue, null, 1) + "\n"); console.log("  --no-render: wrote HTML and JSON only"); return; }
  issue.bytes = render(f("html"), f("pdf"), f("png"));
  issue.pdf = `briefs/${week}.pdf`; issue.html = `briefs/${week}.html`; issue.cover = `briefs/${week}.png`;
  console.log(`  rendered ${issue.pages} pages, ${(issue.bytes / 1048576).toFixed(2)} MB`);

  /* 5. publish */
  if(!args.offline){
    const up = await toSupabase(issue, [[`${week}.pdf`, f("pdf"), "application/pdf"], [`${week}.png`, f("png"), "image/png"]]);
    if(up) issue.mirror = {pdf: up.pdf, cover: up.cover};
  }
  fs.writeFileSync(f("json"), JSON.stringify(issue, null, 1) + "\n");

  const index = readJ(path.join(OUT, "index.json")) || {issues: []};
  const entry = {week, number, title: issue.title, range: issue.range, start: issue.start, end: issue.end, pages: issue.pages, bytes: issue.bytes,
    pdf: issue.pdf, html: issue.html, cover: issue.cover, ...(issue.mirror ? {mirror: issue.mirror.pdf} : {}),
    stories: stories.map(s => ({day: s.dayName, date: s.date, category: s.category, headline: s.headline, source: s.source}))};
  const issues = [entry, ...(index.issues || []).filter(x => x.week !== week)].sort((a, b) => b.week.localeCompare(a.week));
  for(const old of issues.slice(KEEP)) for(const ext of ["pdf", "html", "png", "json"]) fs.rmSync(path.join(OUT, `${old.week}.${ext}`), {force: true});
  const out = {updated: now.toISOString(), issues: issues.slice(0, KEEP)};
  /* Lets the site count downloads. Only ever a publishable key. */
  const pub = process.env.SUPABASE_PUBLISHABLE_KEY || "", sbUrl = String(process.env.SUPABASE_URL || "").replace(/\/+$/, "");
  if(pub && sbUrl && keyKind(pub) === "public") out.supabase = {url: sbUrl, key: pub};
  else if(index.supabase && !pub) out.supabase = index.supabase;
  if(pub && keyKind(pub) !== "public") console.warn("SUPABASE_PUBLISHABLE_KEY does not look like a publishable key; it was NOT written to briefs/index.json.");
  fs.writeFileSync(path.join(OUT, "index.json"), JSON.stringify(out, null, 1) + "\n");
  console.log(`  published briefs/${week}.pdf`);
}

if(process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href){
  main().catch(e => { console.error(e.message || e); process.exit(1); });
}
