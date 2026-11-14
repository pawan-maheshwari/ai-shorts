#!/usr/bin/env node
/* =====================================================================
   AIShorts · Local Desk builder
   ---------------------------------------------------------------------
   Writes the two data files behind the front-page Local Desk:

     events.json   upcoming AI events worldwide, each with coordinates so
                   the page can sort them by distance from the reader
     courses.json  AI courses from leading universities, ranked

   Events come from two places:
     tools/ai-events.json   hand-checked events (always win on a clash)
     confs.tech             the open, community-maintained conference list
                            (github.com/tech-conferences/conference-data)

   Courses come from tools/ai-courses.json. No provider offers a free API
   for learner counts, ratings or fees, so those figures are read off the
   provider's page by a person and dated; this script only ranks them,
   retires expired offers and past start dates, and warns when a figure
   is getting old.

   No API keys, no dependencies: Node 18+ only.

   Usage:
     node tools/local-desk.mjs              live run
     node tools/local-desk.mjs --offline    skip confs.tech and geocoding
     node tools/local-desk.mjs --now 2026-12-01
   ===================================================================== */

import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const P = f => path.join(ROOT, f);
const args = process.argv.slice(2);
const arg = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const OFFLINE = args.includes('--offline');
const NOW = arg('--now') ? new Date(arg('--now')) : new Date();
const TODAY = NOW.toISOString().slice(0, 10);
const HORIZON = new Date(+NOW + 400 * 864e5).toISOString().slice(0, 10);
const STALE_DAYS = 45;

const readJSON = (f, d) => { try { return JSON.parse(fs.readFileSync(P(f), 'utf8')); } catch (e) { return d; } };
const writeJSON = (f, o) => fs.writeFileSync(P(f), JSON.stringify(o, null, 1) + '\n');
const isDate = s => /^\d{4}-\d{2}-\d{2}$/.test(s || '');
const safeUrl = u => /^https?:\/\//i.test(u || '') ? u : null;

async function getJSON(url, ms = 15000) {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), ms);
  try {
    const r = await fetch(url, { signal: c.signal, headers: { 'User-Agent': 'aishorts-local-desk' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } finally { clearTimeout(t); }
}

/* ---------------------------------------------------------------------
   EVENTS
   --------------------------------------------------------------------- */
const AI_NAME = /\b(a\.?i\.?|artificial intelligence|machine learning|ml|mlops|llms?|gen ?ai|generative|agentic|agents?|deep learning|neural|data science)\b/i;
// Paid-listing "conference mills" that fill open lists with look-alike events.
const BLOCK_HOSTS = /(averconferences|vividglobalsummits|scientificmeditech|novelticsconferences|conferenceseries|waset\.org|researchfora|iser\.co|allconferencealert)/i;
const MILL_NAME = /^(\d+(st|nd|rd|th) )?(international conference on|annual global summit|world congress on|global summit on)\b/i;
const COUNTRY = { 'U.K.': ['United Kingdom', 'GB'], 'U.S.A.': ['United States', 'US'], 'Germany': ['Germany', 'DE'], 'India': ['India', 'IN'],
  'Switzerland': ['Switzerland', 'CH'], 'Nepal': ['Nepal', 'NP'], 'United Arab Emirates': ['United Arab Emirates', 'AE'], 'Spain': ['Spain', 'ES'],
  'Netherlands': ['Netherlands', 'NL'], 'Brazil': ['Brazil', 'BR'], 'Singapore': ['Singapore', 'SG'], 'Italy': ['Italy', 'IT'], 'Serbia': ['Serbia', 'RS'],
  'France': ['France', 'FR'], 'South Africa': ['South Africa', 'ZA'], 'Australia': ['Australia', 'AU'], 'Japan': ['Japan', 'JP'], 'Canada': ['Canada', 'CA'],
  'Kenya': ['Kenya', 'KE'], 'Nigeria': ['Nigeria', 'NG'], 'Poland': ['Poland', 'PL'], 'Portugal': ['Portugal', 'PT'], 'Ireland': ['Ireland', 'IE'] };

const geo = readJSON('tools/geo-cities.json', { cities: {} });
let geoDirty = false;
async function locate(city, country) {
  const key = (city + '|' + country).toLowerCase();
  if (geo.cities[key]) return geo.cities[key];
  if (OFFLINE) return null;
  try {
    const name = city.split(',')[0].trim();
    const j = await getJSON('https://geocoding-api.open-meteo.com/v1/search?count=5&language=en&format=json&name=' + encodeURIComponent(name));
    const want = (COUNTRY[country] || [country])[0].toLowerCase();
    const hit = (j.results || []).find(r => (r.country || '').toLowerCase() === want) || null;
    if (!hit) return null;
    const ll = [Math.round(hit.latitude * 100) / 100, Math.round(hit.longitude * 100) / 100];
    geo.cities[key] = ll; geoDirty = true;
    return ll;
  } catch (e) { return null; }
}

const norm = s => String(s || '').toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]+/g, ' ').trim();
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };

function cleanCurated(e) {
  if (!e || !e.title || !isDate(e.start) || !safeUrl(e.url)) return null;
  const o = { title: String(e.title), type: e.type || 'Event', start: e.start, end: isDate(e.end) ? e.end : e.start, url: e.url, source: e.source || '' };
  if (e.online) o.online = true;
  else {
    if (!e.city || typeof e.lat !== 'number' || typeof e.lon !== 'number') return null;
    Object.assign(o, { city: e.city, country: e.country || '', cc: e.cc || '', lat: e.lat, lon: e.lon });
    if (e.venue) o.venue = e.venue;
    if (e.hybrid) o.hybrid = true;
  }
  return o;
}

async function confsTech() {
  if (OFFLINE) return [];
  const years = [...new Set([TODAY.slice(0, 4), HORIZON.slice(0, 4)])];
  const out = [];
  for (const y of years) for (const topic of ['data', 'general']) {
    let list;
    try { list = await getJSON(`https://raw.githubusercontent.com/tech-conferences/conference-data/main/conferences/${y}/${topic}.json`); }
    catch (e) { console.warn(`confs.tech ${y}/${topic}: ${e.message}`); continue; }
    for (const c of Array.isArray(list) ? list : []) {
      if (!c || !c.name || !isDate(c.startDate) || !safeUrl(c.url)) continue;
      if (!AI_NAME.test(c.name) || BLOCK_HOSTS.test(c.url) || MILL_NAME.test(c.name.trim())) continue;
      const o = { title: String(c.name).trim(), type: 'Conference', start: c.startDate, end: isDate(c.endDate) ? c.endDate : c.startDate, url: c.url, source: 'confs.tech' };
      if (c.city && c.country) {
        const ll = await locate(c.city, c.country);
        if (!ll) { if (c.online) o.online = true; else continue; }
        else {
          const [country, cc] = COUNTRY[c.country] || [c.country, ''];
          const CITY_ALIAS = { Bangalore: 'Bengaluru', Bombay: 'Mumbai', Gurgaon: 'Gurugram' };
          const name = c.city.split(',')[0].trim();
          Object.assign(o, { city: CITY_ALIAS[name] || name, country, cc, lat: ll[0], lon: ll[1] });
          if (c.online) o.hybrid = true;
        }
      } else if (c.online) o.online = true;
      else continue;
      out.push(o);
    }
  }
  return out;
}

async function buildEvents() {
  const curatedFile = readJSON('tools/ai-events.json', { events: [] });
  const curated = (curatedFile.events || []).map(cleanCurated).filter(Boolean);
  const auto = await confsTech();
  const seen = new Set(), all = [];
  const keys = e => [norm(e.title) + '|' + e.start, host(e.url) + '|' + e.start];
  for (const e of [...curated, ...auto]) {           // curated first, so it wins
    const k = keys(e);
    if (k.some(x => seen.has(x))) continue;
    k.forEach(x => seen.add(x));
    if (e.end < TODAY || e.start > HORIZON) continue;
    all.push(e);
  }
  all.sort((a, b) => a.start.localeCompare(b.start) || a.title.localeCompare(b.title));
  const prev = readJSON('events.json', null);
  const same = prev && JSON.stringify(prev.events) === JSON.stringify(all);
  writeJSON('events.json', {
    updated: same ? prev.updated : NOW.toISOString(),
    checked: curatedFile.checked || null,
    sources: ['Organisers’ own pages', 'Global AI Community', 'AIM Media House', 'confs.tech open conference list'],
    events: all,
  });
  if (geoDirty) writeJSON('tools/geo-cities.json', geo);
  console.log(`events.json: ${all.length} upcoming (${curated.length} curated, ${auto.length} from confs.tech before de-duplication)`);
}

/* ---------------------------------------------------------------------
   COURSES
   --------------------------------------------------------------------- */
const PRIOR_REVIEWS = 2000, PRIOR_RATING = 4.5;
function score(c) {
  if (typeof c.rating !== 'number' || typeof c.learners !== 'number' || !c.reviews) return null;
  const adj = (c.reviews * c.rating + PRIOR_REVIEWS * PRIOR_RATING) / (c.reviews + PRIOR_REVIEWS);   // review-count-adjusted rating
  const quality = Math.max(0, Math.min(1, (adj - 4) / 1));                                              // 4.0 → 0, 5.0 → 1
  const reach = Math.max(0, Math.min(1, (Math.log10(c.learners) - 4) / 2));                             // 10k → 0, 1M → 1
  return { score: Math.round((0.7 * quality + 0.3 * reach) * 1000) / 10, adjRating: Math.round(adj * 100) / 100 };
}

function buildCourses() {
  const src = readJSON('tools/ai-courses.json', null);
  if (!src || !Array.isArray(src.courses)) { console.warn('tools/ai-courses.json missing: courses.json left as is'); return; }
  const out = [];
  for (const c of src.courses) {
    if (!c || !c.id || !c.title || !safeUrl(c.url)) continue;
    const o = { id: c.id, title: c.title, university: c.university, partner: c.partner || null, platform: c.platform, level: c.level || null,
      duration: c.duration || null, learners: c.learners ?? null, learnersNote: c.learnersNote || null, rating: c.rating ?? null, reviews: c.reviews ?? null,
      start: c.start || { type: 'dated', dates: [] }, fee: c.fee ?? null, feeSort: c.feeSort ?? null,
      discounts: (c.discounts || []).filter(d => d && d.text && (!d.until || d.until >= TODAY)),
      url: c.url, verified: isDate(c.verified) ? c.verified : null };
    const s = score(o);
    if (s) Object.assign(o, s);
    const age = o.verified ? Math.round((Date.parse(TODAY) - Date.parse(o.verified)) / 864e5) : 999;
    if (age > STALE_DAYS) { o.stale = true; console.warn(`STALE (${age} days): ${o.title}. Re-check ${o.url}`); }
    out.push(o);
  }
  const ranked = out.filter(c => c.score != null).sort((a, b) => b.score - a.score || b.learners - a.learners);
  ranked.forEach((c, i) => { c.rank = i + 1; });
  const rest = out.filter(c => c.score == null);
  const courses = [...ranked, ...rest];
  const prev = readJSON('courses.json', null);
  const same = prev && JSON.stringify(prev.courses) === JSON.stringify(courses);
  const verified = courses.map(c => c.verified).filter(Boolean).sort();
  writeJSON('courses.json', { updated: same ? prev.updated : NOW.toISOString(), figuresChecked: verified[0] || null, method: src.method || '', courses });
  console.log(`courses.json: ${ranked.length} ranked, ${rest.length} listed without a rank`);
}

await buildEvents();
buildCourses();
