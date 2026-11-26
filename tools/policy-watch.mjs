#!/usr/bin/env node
/* =====================================================================
   AIShorts · AI Policy Watch builder
   ---------------------------------------------------------------------
   Writes the data behind policy-watch.html and the front-page policy bell:

     policy-watch.json            what readers see: newest AI policy
                                  changes, EU rules adopted in the last
                                  12 months, US vs China per quarter and
                                  the transparency comparison
     ops/policy-changelog.json    internal log: one entry per policy
                                  change the first time this desk saw it

   The data comes from Digital Policy Alert (digitalpolicyalert.org).
   DPA is reached through Claude's Digital Policy Alert connector, so a
   scheduled Claude task fetches the raw answers, saves them as JSON in
   a scratch folder and runs this script. Every input is optional: a
   section with no input keeps what policy-watch.json already holds.

   Inputs (raw DPA tool answers, response_format "json"):
     --alerts FILE        dpa_search_events, AI thread, sorted -publication_date
     --eu FILE [FILE…]    dpa_search_events, EU supranational binding, sorted -date
                          (several pages may be passed)
     --counts FILE        dpa_count_data by event_date_year, event_date_month,
                          implementer for USA + CHN since 2023-01-01
     --transparency FILE  dpa_get_ai_governance_concern "transparency",
                          parts requirements + international_frameworks
     --africa FILE [FILE…]  dpa_search_events for ZAF and/or KEN, NGA, EGY, GHA, ETH
                          since a recent date (show_keys event_id, title, date,
                          action_type, policy_area, economic_activities, threads,
                          implementers). Joined with the hand-curated editorial
                          layer in tools/africa-radar-notes.json.
     --africa-areas FILE  dpa_count_data count_by policy_area, ZAF, last 12 months
     --now YYYY-MM-DD     pretend today is this date (tests)

   No dependencies: Node 18+ only.
   ===================================================================== */

import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUT = path.join(ROOT, 'policy-watch.json');
const LOG = path.join(ROOT, 'ops', 'policy-changelog.json');
const LOG_CAP = 3000;

/* ---------- args ---------- */
const args = {};
{
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    if (!a[i].startsWith('--')) continue;
    const k = a[i].slice(2), vals = [];
    while (a[i + 1] && !a[i + 1].startsWith('--')) vals.push(a[++i]);
    args[k] = vals;
  }
}
const now = args.now && args.now[0] ? new Date(args.now[0] + 'T12:00:00Z') : new Date();
const iso = (d) => d.toISOString().slice(0, 10);

const readJSON = (f, fallback) => {
  try {
    let j = JSON.parse(fs.readFileSync(f, 'utf8'));
    if (j && typeof j.result === 'string') j = JSON.parse(j.result);   // connector wrapper
    else if (j && j.result && !j.results && typeof j.result === 'object') j = j.result;
    return j;
  } catch (e) { return fallback; }
};
const asList = (v) => Array.isArray(v) ? v : (v == null ? [] : [v]);
const jur = (im) => {
  const l = asList(im).map(String);
  return l.length > 3 ? l.slice(0, 3).join(', ') + ' +' + (l.length - 3) : l.join(', ');
};
const url = (id) => 'https://digitalpolicyalert.org/event/' + id;

const prev = readJSON(OUT, {}) || {};
const out = {
  updated_at: now.toISOString(),
  source: 'Digital Policy Alert (digitalpolicyalert.org)',
  alerts: prev.alerts || [],
  eu: prev.eu || null,
  uscn: prev.uscn || null,
  transparency: prev.transparency || null,
  africa: prev.africa || null
};

/* ---------- alerts ---------- */
let fresh = [];
if (args.alerts) {
  const j = readJSON(args.alerts[0], null);
  if (!j || !Array.isArray(j.results)) { console.error('alerts: no results array'); process.exitCode = 2; }
  else {
    out.alerts = j.results.slice(0, 60).map((r) => ({
      event_id: String(r.event_id),
      title: String(r.title || ''),
      jurisdictions: jur(r.implementers),
      action_type: String(r.action_type || ''),
      date: r.date || '',
      publication_date: r.publication_date || r.date || '',
      url: url(r.event_id),
      /* catalogued late: DPA published it more than 60 days after the event itself */
      late: !!(r.date && r.publication_date && (Date.parse(r.publication_date) - Date.parse(r.date)) > 60 * 864e5)
    }));
  }
}

/* ---------- EU rules, last 12 months ---------- */
if (args.eu) {
  const rows = [];
  for (const f of args.eu) { const j = readJSON(f, null); if (j && Array.isArray(j.results)) rows.push(...j.results); }
  if (rows.length) {
    const start = iso(new Date(now.getTime() - 365 * 864e5));
    const keep = { adoption: 'Adopted', implementation: 'In force', 'in force with grace period': 'In force' };
    const groups = new Map();
    const seenIds = new Set();
    for (const r of rows) {
      if (!r || seenIds.has(r.event_id)) continue;
      seenIds.add(r.event_id);
      if (!(r.action_type in keep) || !r.date || r.date < start || r.date > iso(now)) continue;
      const base = String(r.title).replace(/\s+(including|including measures|with)\s.*$/i, '').trim();
      const key = r.date + '|' + keep[r.action_type] + '|' + base;
      if (!groups.has(key)) groups.set(key, { date: r.date, step: keep[r.action_type], title: base, provisions: 0, event_ids: [] });
      const g = groups.get(key); g.provisions++; g.event_ids.push(String(r.event_id));
    }
    const items = [...groups.values()].sort((a, b) => a.date < b.date ? 1 : -1);
    out.eu = { window_start: start, window_end: iso(now), items,
      adopted: items.filter((i) => i.step === 'Adopted').length,
      in_force: items.filter((i) => i.step === 'In force').length };
  }
}

/* ---------- US vs China per quarter ---------- */
if (args.counts) {
  const j = readJSON(args.counts[0], null);
  if (j && Array.isArray(j.results)) {
    const m = new Map();
    for (const r of j.results) {
      const q = r.event_date_year + ' Q' + Math.ceil(Number(r.event_date_month) / 3);
      if (!m.has(q)) m.set(q, { q, us: 0, cn: 0 });
      if (r.implementer_iso === 'USA') m.get(q).us += Number(r.value);
      if (r.implementer_iso === 'CHN') m.get(q).cn += Number(r.value);
    }
    const quarters = [...m.values()].sort((a, b) => a.q < b.q ? -1 : 1);
    const cur = now.getUTCFullYear() + ' Q' + Math.ceil((now.getUTCMonth() + 1) / 3);
    quarters.forEach((x) => { if (x.q === cur) x.partial = true; });
    out.uscn = { since: '2023-01-01', quarters,
      us_total: quarters.reduce((s, x) => s + x.us, 0),
      cn_total: quarters.reduce((s, x) => s + x.cn, 0) };
  }
}

/* ---------- transparency ---------- */
if (args.transparency) {
  const j = readJSON(args.transparency[0], null);
  if (j && j.concern) {
    const fw = (j.international_frameworks && j.international_frameworks.rows) || [];
    out.transparency = {
      domestic_provisions: j.concern.domestic_provisions,
      international_provisions: j.concern.international_provisions,
      jurisdictions: j.concern.jurisdictions,
      policies: j.concern.interventions,
      requirements: (j.requirements || []).map((r) => ({ title: r.title, provisions: r.provisions, laws: r.documents_count, jurisdictions: r.jurisdictions_count }))
        .sort((a, b) => b.provisions - a.provisions),
      frameworks: fw.map((r) => ({ name: String(r[0]), provisions: Number(r[1]) }))
    };
  }
}

/* ---------- Africa Radar (South Africa + region) ---------- */
if (args.africa) {
  const notesFile = readJSON(path.join(ROOT, 'tools', 'africa-radar-notes.json'), { notes: {}, questions: {} });
  const N = notesFile.notes || {};
  const AFRICA = ['South Africa', 'Kenya', 'Egypt', 'Nigeria', 'Ghana', 'Ethiopia', 'Rwanda', 'Morocco'];
  const seenIds = new Set(), items = [];
  for (const f of args.africa) {
    const j = readJSON(f, null);
    for (const r of (j && j.results) || []) {
      const id = String(r.event_id);
      if (seenIds.has(id)) continue; seenIds.add(id);
      const im = asList(r.implementers).map(String);
      const n = N[id] || {};
      const hay = (r.title + ' ' + asList(r.economic_activities).join(' ') + ' ' + (r.policy_area || '')).toLowerCase();
      const th = asList(r.threads).join(' ').toLowerCase();
      const lenses = n.lenses || [
        /payment|bank|crypto|virtual asset|credit|lend|financ|insur/.test(hay) && 'fin',
        /telecom|infrastructure provider|licen[cs]e|icasa|numbering|data cent|network/.test(hay) && 'telco',
        /e-commerce|consumer|retail|advert|promotion|platform|loyalty/.test(hay) && 'retail'
      ].filter(Boolean);
      const topics = n.topics || [
        /artificial intelligence/.test(th) || /\bai\b|artificial intelligence/.test(hay) ? 'ai' : '',
        /data governance|privacy|personal data/.test(hay + ' ' + th) ? 'data' : '',
        /competition/.test(hay + ' ' + th) ? 'comp' : '',
        /payment|crypto|virtual asset/.test(hay + ' ' + th) ? 'pay' : ''
      ].filter(Boolean);
      const local = im.filter((c) => AFRICA.includes(c));
      const country = n.country || (im.length > 3 ? 'Global' : (local[0] || im[0] || ''));
      items.push({ event_id: id, title: n.short_title || String(r.title || ''), date: r.date || '', step: String(r.action_type || ''),
        country, lenses: lenses.length ? lenses : ['retail', 'fin', 'telco'], topics,
        impact: n.impact || 'watch', why: n.why || '', curated: !!n.impact, url: url(id) });
    }
  }
  items.sort((a, b) => a.date < b.date ? 1 : -1);
  let areas = (prev.africa && prev.africa.areas) || [], total = (prev.africa && prev.africa.total_12m) || null;
  if (args['africa-areas']) {
    const c = readJSON(args['africa-areas'][0], null);
    if (c && Array.isArray(c.results)) {
      areas = c.results.map((r) => ({ area: r.policy_area_name, events: Number(r.value) })).sort((a, b) => b.events - a.events);
      total = c.total || (c.basis && c.basis.total) || total;
    }
  }
  out.africa = { as_of: iso(now), total_12m: total, areas, questions: notesFile.questions || {}, items };
} else if (prev.africa) out.africa = prev.africa;

/* ---------- internal changelog ---------- */
const log = readJSON(LOG, null) || { note: 'Internal log of AI policy changes first seen by the AIShorts Policy Desk. Newest first.', entries: [] };
const known = new Set(log.entries.map((e) => e.event_id));
fresh = out.alerts.filter((a) => !known.has(a.event_id));
if (fresh.length) {
  const stamp = now.toISOString();
  log.entries = fresh.map((a) => ({ logged_at: stamp, event_id: a.event_id, publication_date: a.publication_date,
    date: a.date, jurisdictions: a.jurisdictions, action_type: a.action_type, title: a.title, url: a.url, late: a.late }))
    .concat(log.entries).slice(0, LOG_CAP);
  log.updated_at = stamp;
}
out.new_since_last_run = fresh.map((a) => a.event_id);

fs.mkdirSync(path.dirname(LOG), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(out, null, 1) + '\n');
fs.writeFileSync(LOG, JSON.stringify(log, null, 1) + '\n');
console.log(`policy-watch.json: ${out.alerts.length} alerts, ${out.eu ? out.eu.items.length : 0} EU items; changelog +${fresh.length} (total ${log.entries.length})`);
