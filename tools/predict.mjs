#!/usr/bin/env node
/* =====================================================================
   AIShorts · Top AI Prediction engine
   ---------------------------------------------------------------------
   Reads a full day (24h) of AI coverage from around the world, groups it
   into storylines, scores each storyline on the signals that tend to carry
   a story into the next day's front pages, and publishes the three most
   likely ones to predictions.json. Every run also grades the previous
   day's calls against what actually led the wire, so the site carries an
   honest, running hit rate.

   No API keys, no dependencies: Node 18+ only.

   Signals (all transparent, all shown on the page):
     reach      distinct outlets covering the storyline
     breadth    distinct world regions covering it
     momentum   coverage in the last 6h vs the 18h before (accelerating?)
     authority  weight of the outlets (wires and majors count more)
     community  Hacker News points on matching stories
     next-step  language that implies a next chapter ("set to", "will
                unveil", "hearing", "vote", "tomorrow"...)
     arc        storyline was already running on previous days
     calendar   a scheduled event in tools/ai-calendar.json for that date

   Usage:
     node tools/predict.mjs                      live run
     node tools/predict.mjs --fixture f.json     offline run on saved items
     node tools/predict.mjs --now 2026-10-31T18:00:00Z
     node tools/predict.mjs --dump items.json    also save gathered items
   ===================================================================== */

import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const OUT = path.join(ROOT, 'predictions.json');
const HIST = path.join(ROOT, 'predictions-history.json');
const CAL = path.join(ROOT, 'tools', 'ai-calendar.json');

const args = process.argv.slice(2);
const arg = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const NOW = arg('--now') ? new Date(arg('--now')) : new Date();
const H = 3600e3;
const WINDOW_START = new Date(NOW - 24 * H);
const IST = 5.5 * H;
const istDate = d => new Date(+d + IST).toISOString().slice(0, 10);
const TODAY = istDate(NOW);
const FOR_DATE = istDate(+NOW + 24 * H);

/* ---------------------------------------------------------------------
   1. SOURCES — Google News editions across the world, topical searches,
      specialist AI desks and the Hacker News community.
   --------------------------------------------------------------------- */
const gn = (q, hl, gl) =>
  `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=${hl}&gl=${gl}&ceid=${gl}:${hl.split('-')[0]}`;

const EDITIONS = [
  ['North America', 'en-US', 'US'], ['North America', 'en-CA', 'CA'],
  ['Europe', 'en-GB', 'GB'], ['Europe', 'en-IE', 'IE'],
  ['South Asia', 'en-IN', 'IN'],
  ['Asia-Pacific', 'en-SG', 'SG'], ['Asia-Pacific', 'en-AU', 'AU'],
  ['Africa', 'en-ZA', 'ZA'], ['Africa', 'en-NG', 'NG'], ['Africa', 'en-KE', 'KE'],
  ['Middle East', 'en-AE', 'AE'],
];
const TOPICS = [
  'OpenAI OR Anthropic OR "Google DeepMind" OR xAI OR Mistral OR DeepSeek',
  'Nvidia AI chips OR "AI chip" OR "export controls"',
  '"AI regulation" OR "AI Act" OR "AI policy" OR "AI bill"',
  '"AI startup" funding OR valuation OR IPO',
  'AI lawsuit OR copyright OR "AI safety"',
  '"AI model" launch OR release OR unveils',
  '"data center" AI power OR energy',
];
const DESKS = [
  ['TechCrunch', 'https://techcrunch.com/category/artificial-intelligence/feed/'],
  ['The Verge', 'https://www.theverge.com/rss/ai-artificial-intelligence/index.xml'],
  ['VentureBeat', 'https://venturebeat.com/category/ai/feed/'],
  ['MIT Technology Review', 'https://www.technologyreview.com/topic/artificial-intelligence/feed'],
  ['Ars Technica', 'https://arstechnica.com/ai/feed/'],
  ['Wired', 'https://www.wired.com/feed/tag/ai/latest/rss'],
  ['The Guardian', 'https://www.theguardian.com/technology/artificialintelligenceai/rss'],
];

function get(url, hops = 0) {
  return new Promise(res => {
    const req = https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0 (aishorts-predictor)', Accept: '*/*' }, timeout: 20000 }, r => {
      if ([301, 302, 303, 307, 308].includes(r.statusCode) && r.headers.location && hops < 4) {
        r.resume(); return res(get(new URL(r.headers.location, url).href, hops + 1));
      }
      if (r.statusCode >= 400) { r.resume(); return res(''); }
      let d = ''; r.setEncoding('utf8'); r.on('data', c => d += c); r.on('end', () => res(d));
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => res(''));
  });
}

const ENT = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&apos;': "'", '&nbsp;': ' ' };
const decode = s => String(s || '')
  .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
  .replace(/&(amp|lt|gt|quot|#39|apos|nbsp);/g, m => ENT[m])
  .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n))
  .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCharCode(parseInt(n, 16)))
  .replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
const tag = (block, t) => { const m = block.match(new RegExp(`<${t}[^>]*>([\\s\\S]*?)</${t}>`, 'i')); return m ? decode(m[1]) : ''; };

function parseFeed(xml, { feed, region, source }) {
  const out = [];
  const blocks = String(xml).split(/<item[\s>]|<entry[\s>]/i).slice(1);
  for (const b of blocks) {
    let title = tag(b, 'title');
    if (!title) continue;
    let link = tag(b, 'link');
    if (!link) { const m = b.match(/<link[^>]*href="([^"]+)"/i); link = m ? m[1] : ''; }
    const when = tag(b, 'pubDate') || tag(b, 'published') || tag(b, 'updated') || tag(b, 'dc:date');
    let src = tag(b, 'source') || source || feed;
    if (src && title.endsWith(' - ' + src)) title = title.slice(0, -(src.length + 3));
    out.push({ title, url: link, source: src, region, feed, pub: when ? new Date(when).toISOString() : null });
  }
  return out;
}

async function gather() {
  const jobs = [];
  for (const [region, hl, gl] of EDITIONS)
    jobs.push(get(gn('artificial intelligence when:1d', hl, gl)).then(x => parseFeed(x, { feed: `Google News ${gl}`, region })));
  for (const q of TOPICS)
    jobs.push(get(gn(q + ' when:1d', 'en-US', 'US')).then(x => parseFeed(x, { feed: 'Google News topics', region: 'North America' })));
  for (const [name, url] of DESKS)
    jobs.push(get(url).then(x => parseFeed(x, { feed: name, region: 'Global desks', source: name })));
  const since = Math.floor(WINDOW_START / 1000);
  for (const q of ['AI', 'LLM', 'OpenAI', 'Anthropic', 'GPT']) {
    jobs.push(get(`https://hn.algolia.com/api/v1/search?query=${q}&tags=story&hitsPerPage=60&numericFilters=created_at_i>${since}`)
      .then(x => { try { return JSON.parse(x).hits.map(h => ({ title: h.title, url: h.url || `https://news.ycombinator.com/item?id=${h.objectID}`, source: 'Hacker News', region: 'Community', feed: 'Hacker News', pub: h.created_at, points: h.points || 0 })); } catch { return []; } }));
  }
  const all = (await Promise.all(jobs)).flat();
  return all;
}

/* ---------------------------------------------------------------------
   2. LANGUAGE — tokens, entities and themes
   --------------------------------------------------------------------- */
const STOP = new Set(('a an the and or but of to in on at for from by with as is are was were be been being it its this that these those ' +
  'into over after before about amid than then new news says say said will would could should can may might just more most how why what when ' +
  'who whom which where your you our their they them his her he she we us not no yes ai artificial intelligence generative genai report reports ' +
  'today latest update week year years day days first big make makes made using use uses used get gets now here there out up down via vs ' +
  'analysis opinion exclusive explainer tech technology company companies firm firms world global top amid also like one two three billion million per inc ltd plc').split(' '));

const tokens = t => {
  const s = new Set();
  for (let w of t.toLowerCase().replace(/[’']/g, '').split(/[^a-z0-9.+$]+/)) {
    w = w.replace(/^[.]+|[.]+$/g, '');
    if (w.length < 3 || STOP.has(w) || /^\d+$/.test(w)) continue;
    if (w.length > 4 && w.endsWith('s') && !/(ss|is|us|ics)$/.test(w)) w = w.slice(0, -1);
    s.add(w);
  }
  return s;
};

const ENTITIES = [
  ['OpenAI', /\bopenai|chatgpt|gpt-?\d|sam altman|sora\b/i], ['Anthropic', /\banthropic|claude\b/i],
  ['Google', /\bgoogle|gemini|deepmind|alphabet|sundar pichai\b/i], ['Microsoft', /\bmicrosoft|copilot|satya nadella\b/i],
  ['Meta', /\bmeta\b|llama|zuckerberg/i], ['Nvidia', /\bnvidia|jensen huang|blackwell|rubin gpu/i],
  ['Apple', /\bapple\b|apple intelligence|siri\b/i], ['Amazon', /\bamazon|aws\b|bedrock|alexa\b/i],
  ['xAI', /\bxai\b|grok\b|elon musk/i], ['Tesla', /\btesla|optimus\b|robotaxi/i],
  ['Mistral', /\bmistral\b/i], ['DeepSeek', /\bdeepseek\b/i], ['Alibaba', /\balibaba|qwen\b/i],
  ['Baidu', /\bbaidu|ernie\b/i], ['Tencent', /\btencent\b/i], ['ByteDance', /\bbytedance|tiktok\b/i],
  ['Samsung', /\bsamsung\b/i], ['TSMC', /\btsmc\b/i], ['AMD', /\bamd\b/i], ['Intel', /\bintel\b/i],
  ['Broadcom', /\bbroadcom\b/i], ['Arm', /\barm holdings\b/i], ['SoftBank', /\bsoftbank|masayoshi son/i],
  ['Oracle', /\boracle\b/i], ['IBM', /\bibm\b/i], ['Salesforce', /\bsalesforce\b/i],
  ['Perplexity', /\bperplexity\b/i], ['Hugging Face', /\bhugging ?face\b/i], ['Midjourney', /\bmidjourney\b/i],
  ['Infosys / TCS / Wipro', /\binfosys|\btcs\b|tata consultancy|wipro|hcltech/i], ['Reliance', /\breliance|jio\b/i],
  ['EU', /\beuropean union|\beu\b|brussels|european commission/i], ['US Government', /\bwhite house|congress|senate|trump|federal trade|\bftc\b|commerce department/i],
  ['China', /\bchina|chinese|beijing\b/i], ['India', /\bindia|indian|modi|meity\b/i], ['UK', /\bu\.?k\.?\b|britain|british|starmer/i],
];
const THEMES = [
  ['Model launch', /\blaunch|unveil|releas|rolls? out|debut|introduc|announc|new model|upgrade|preview\b/i,
    e => `Hands-on reviews, benchmark claims and rival responses to ${e} are likely to lead tomorrow.`],
  ['Regulation & policy', /\bregulat|\blaw\b|\bbill\b|\bact\b|policy|ban\b|govern|legislat|minister|executive order|compliance|rules\b/i,
    e => `Expect reactions from industry, lawmakers and civil society as the ${e} policy story moves to its next step.`],
  ['Chips & compute', /\bchip|gpu|semiconductor|compute|export control|foundry|wafer|data cent|datacent/i,
    e => `Supply, pricing and export angles on ${e} should keep this in the headlines.`],
  ['Funding & markets', /\bfund|rais|valuation|ipo\b|invest|shares?\b|stock|market cap|market value|\bvalue\b|earning|revenue|sales\b|profit|deal\b|acqui|merger|buy\b|plunge|slump|soar|surge|rally|\$\d/i,
    e => `Market reaction, analyst notes and deal details around ${e} are the likely next chapter.`],
  ['Courts & copyright', /\blawsuit|\bsue|court|judge|copyright|ruling|settle|antitrust|probe|investigat/i,
    e => `Filings, responses or a ruling involving ${e} could land and drive fresh coverage.`],
  ['Safety & risk', /\bsafety|risk|deepfake|misinformation|scam|harm|bias|hallucinat|jailbreak|security|breach|leak/i,
    e => `Expect follow-ups and expert commentary on the ${e} safety question.`],
  ['Jobs & society', /\bjobs?\b|layoff|workforce|employ|worker|hiring|education|student|school|skills/i,
    e => `Expect workplace and society angles on ${e} to keep spreading across regions.`],
  ['Energy & infrastructure', /\benergy|power|nuclear|electric|grid|water|infrastructure/i,
    e => `Infrastructure and energy follow-ups tied to ${e} are likely.`],
  ['Agents & products', /\bagent|assistant|copilot|chatbot|app\b|feature|search\b|browser/i,
    e => `Product adoption stories and comparisons around ${e} are likely to continue.`],
];
const FORWARD = /\b(set to|to unveil|to launch|to announce|will (launch|unveil|announce|release|vote|rule|testify|report)|expected to|plans? to|poised to|gears up|ahead of|upcoming|tomorrow|next week|this week|later today|deadline|hearing|vote\b|to testify|earnings (call|report)|keynote|summit|conference|event on)/i;

const AUTH1 = /\b(reuters|bloomberg|financial times|\bft\b|wall street journal|\bwsj\b|new york times|nytimes|bbc|associated press|\bap news|cnbc|the guardian|economist|washington post|nikkei|axios|the information)\b/i;
const AUTH2 = /\b(techcrunch|the verge|wired|ars technica|venturebeat|mit technology review|engadget|zdnet|cnet|forbes|business insider|fortune|semafor|politico|the register|economic times|mint|hindustan times|times of india|the hindu|south china morning post|straits times|al jazeera|cnn|nbc|abc news|sky news|le monde|handelsblatt|fast company|platformer|ieee spectrum|nature|science)\b/i;
const tier = s => AUTH1.test(s) ? 1 : AUTH2.test(s) ? 0.6 : s === 'Hacker News' ? 0.3 : 0.25;

const jac = (a, b) => { let i = 0; for (const x of a) if (b.has(x)) i++; return i / (a.size + b.size - i || 1); };
const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));

/* ---------------------------------------------------------------------
   3. STORYLINES — greedy clustering on words + shared entities
   --------------------------------------------------------------------- */
function enrich(items) {
  const seen = new Set();
  const kept = [];
  for (const it of items) {
    if (!it.title) continue;
    const t = new Date(it.pub || NOW);
    if (isNaN(t) || t < WINDOW_START - 6 * H || t > +NOW + H) continue;
    const key = it.title.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    if (seen.has(key + '|' + it.source)) continue;
    seen.add(key + '|' + it.source);
    it.t = +t;
    it.tok = tokens(it.title);
    it.ents = ENTITIES.filter(([, re]) => re.test(it.title)).map(([n]) => n);
    { let bt = 'General AI', bn = 0; for (const [n, re] of THEMES) { const k = (it.title.match(new RegExp(re.source, 'gi')) || []).length; if (k > bn) { bn = k; bt = n; } } it.theme = bt; }
    it.fwd = FORWARD.test(it.title);
    if (it.tok.size >= 2) kept.push(it);
  }
  return kept;
}

function cluster(items) {
  const cl = [];
  for (const it of items.sort((a, b) => a.t - b.t)) {
    let best = null, bs = 0;
    for (const c of cl) {
      let s = 0;
      for (const m of c.items) { const j = jac(it.tok, m.tok); if (j > s) s = j; }
      const shared = it.ents.some(e => c.ents.has(e));
      const need = shared ? 0.2 : 0.34;
      if (s >= need && s > bs) { bs = s; best = c; }
    }
    if (!best) { best = { items: [], ents: new Set() }; cl.push(best); }
    best.items.push(it);
    it.ents.forEach(e => best.ents.add(e));
  }
  return cl;
}

function topTokens(items, n = 8) {
  const f = new Map();
  for (const it of items) for (const w of it.tok) f.set(w, (f.get(w) || 0) + 1);
  return [...f.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([w]) => w);
}
const mode = arr => { const f = new Map(); arr.forEach(x => f.set(x, (f.get(x) || 0) + 1)); return [...f.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]; };

/* ---------------------------------------------------------------------
   4. SCORING
   --------------------------------------------------------------------- */
function loadJSON(p, fb) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return fb; } }

function score(c, history, calendar) {
  const its = c.items;
  const sources = new Set(its.map(i => i.source));
  const regions = new Set(its.filter(i => i.region !== 'Community').map(i => i.region));
  const recentCut = +NOW - 6 * H;
  const recent = its.filter(i => i.t >= recentCut).length, older = its.length - recent;
  const vel = (recent + 1) / (older / 3 + 1);
  const auth = [...sources].reduce((s, x) => s + tier(x), 0);
  const hn = its.reduce((s, i) => s + (i.points || 0), 0);
  const fwd = its.filter(i => i.fwd).length;
  const sig = new Set(topTokens(its));
  const entity = mode(its.flatMap(i => i.ents)) || 'AI industry';
  const theme = mode(its.map(i => i.theme).filter(t => t !== 'General AI')) || 'General AI';

  const arcDays = (history.days || []).slice(-3).filter(d =>
    (d.clusters || []).some(h => jac(sig, new Set(h.sig)) >= 0.22 || (h.entity === entity && h.theme === theme && entity !== 'AI industry'))).length;
  const cal = (calendar.events || []).find(ev => ev.date === FOR_DATE &&
    (ev.keywords || []).some(k => its.some(i => i.title.toLowerCase().includes(String(k).toLowerCase()))));

  const f = {
    reach: clamp(Math.log2(1 + sources.size) / Math.log2(31)),
    breadth: clamp(regions.size / 6),
    momentum: clamp((Math.log2(vel) + 1) / 3),
    authority: clamp(auth / 5),
    community: clamp(Math.log10(1 + hn) / 3),
    nextStep: clamp(fwd / 3),
    arc: clamp(arcDays / 3),
  };
  let s = 0.24 * f.reach + 0.14 * f.breadth + 0.14 * f.momentum + 0.16 * f.authority +
          0.07 * f.community + 0.15 * f.nextStep + 0.10 * f.arc;
  if (cal) s += 0.25;
  if (sources.size < 2 && !cal) s *= 0.35;           // one-outlet stories rarely lead
  const probability = clamp(1 / (1 + Math.exp(-(s * 7.5 - 3.1))), 0.06, 0.92);

  // representative headline: most authoritative, then most central
  const rep = [...its].sort((a, b) => (tier(b.source) - tier(a.source)) ||
    (its.reduce((x, m) => x + jac(b.tok, m.tok), 0) - its.reduce((x, m) => x + jac(a.tok, m.tok), 0)))[0];

  const ev = []; const used = new Set();
  for (const i of [...its].sort((a, b) => tier(b.source) - tier(a.source) || b.t - a.t)) {
    if (used.has(i.source)) continue; used.add(i.source);
    ev.push({ title: i.title, source: i.source, url: i.url, region: i.region });
    if (ev.length >= 4) break;
  }
  const outlookFn = (THEMES.find(([n]) => n === theme) || [, , e => `Follow-up coverage of the ${e} story is likely to continue.`])[2];

  return {
    score: +s.toFixed(4), probability: +probability.toFixed(2),
    confidence: probability >= 0.6 ? 'High' : probability >= 0.38 ? 'Medium' : 'Low',
    entity, theme, headline: rep.title.replace(/\s+[|–—-]\s+[^|–—-]{2,40}$/, '').trim(),
    outlook: (cal ? `Scheduled: ${cal.title}. ` : '') + outlookFn(entity),
    signals: {
      outlets: sources.size, regions: [...regions], stories: its.length,
      momentum: +vel.toFixed(2), recent6h: recent, authority: +auth.toFixed(1),
      hnPoints: hn, nextStepCues: fwd, arcDays, calendar: cal ? cal.title : null,
    },
    factors: Object.fromEntries(Object.entries(f).map(([k, v]) => [k, +v.toFixed(2)])),
    sig: [...sig], evidence: ev,
  };
}

/* ---------------------------------------------------------------------
   5. SCORECARD — grade yesterday's calls against today's leading stories
   --------------------------------------------------------------------- */
function grade(prev, ranked) {
  if (!prev || prev.forDate !== TODAY || !Array.isArray(prev.predictions)) return null;
  const leaders = [...ranked].sort((a, b) => b.signals.outlets - a.signals.outlets).slice(0, 10);
  const calls = prev.predictions.map(p => {
    const ps = new Set(p.sig || []);
    const m = leaders.find(c => jac(ps, new Set(c.sig)) >= 0.2 ||
      (p.entity !== 'AI industry' && c.entity === p.entity && c.theme === p.theme));
    return { rank: p.rank, headline: p.headline, probability: p.probability, hit: !!m, matched: m ? m.headline : null };
  });
  return { date: TODAY, calls, hits: calls.filter(c => c.hit).length };
}

/* ---------------------------------------------------------------------
   6. RUN
   --------------------------------------------------------------------- */
(async () => {
  const raw = arg('--fixture') ? loadJSON(arg('--fixture'), []) : await gather();
  if (arg('--dump')) fs.writeFileSync(arg('--dump'), JSON.stringify(raw, null, 1));
  const items = enrich(raw);
  console.log(`gathered ${raw.length} · kept ${items.length} in 24h window`);
  if (items.length < 25) { console.error('Too little coverage gathered — keeping previous predictions.'); process.exit(0); }

  const history = loadJSON(HIST, { days: [] });
  const calendar = loadJSON(CAL, { events: [] });
  const prev = loadJSON(OUT, null);

  const ranked = cluster(items).map(c => score(c, history, calendar)).sort((a, b) => b.score - a.score);

  // pick three distinct storylines (no two on the same entity + theme)
  const picks = [];
  for (const c of ranked) {
    if (picks.some(p => (p.entity === c.entity && p.theme === c.theme && c.entity !== 'AI industry') || jac(new Set(p.sig), new Set(c.sig)) >= 0.3)) continue;
    picks.push(c); if (picks.length === 3) break;
  }

  const graded = grade(prev, ranked);
  const track = prev?.track || { days: 0, calls: 0, hits: 0, log: [] };
  if (graded && !track.log.some(l => l.date === graded.date)) {
    track.days++; track.calls += graded.calls.length; track.hits += graded.hits;
    track.log = [...track.log, { date: graded.date, hits: graded.hits, calls: graded.calls.length }].slice(-30);
  }

  const sources = new Set(items.map(i => i.source));
  const regions = new Set(items.map(i => i.region));
  const out = {
    generated: NOW.toISOString(),
    forDate: FOR_DATE,
    window: { from: WINDOW_START.toISOString(), to: NOW.toISOString() },
    scanned: { stories: items.length, outlets: sources.size, regions: [...regions], storylines: ranked.length },
    definition: 'Probability that the storyline is among the ten most-covered AI stories on the forecast date.',
    predictions: picks.map((p, i) => ({ rank: i + 1, ...p })),
    scorecard: graded || prev?.scorecard || null,
    track: { ...track, hitRate: track.calls ? +(track.hits / track.calls).toFixed(2) : null },
    method: 'AIShorts Top AI Prediction v1 — storyline clustering + reach, breadth, momentum, authority, community, next-step, arc and calendar signals.',
  };
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2));

  history.days = [...(history.days || []).filter(d => d.date !== TODAY), {
    date: TODAY,
    clusters: ranked.slice(0, 25).map(c => ({ sig: c.sig, entity: c.entity, theme: c.theme, outlets: c.signals.outlets })),
  }].slice(-14);
  fs.writeFileSync(HIST, JSON.stringify(history));

  console.log(`For ${FOR_DATE}:`);
  out.predictions.forEach(p => console.log(` ${p.rank}. [${Math.round(p.probability * 100)}%] ${p.entity} · ${p.theme} — ${p.headline}`));
  if (graded) console.log(`Graded ${TODAY}: ${graded.hits}/${graded.calls.length} hits`);
})().catch(e => { console.error(e); process.exit(1); });
