#!/usr/bin/env node
/* =====================================================================
   AIShorts · The Daily Wire — Pinocchio & Gandalf (daily strip writer)
   ---------------------------------------------------------------------
   1. Reads today's AI headlines (Google News RSS, same feed as the wire).
   2. Picks the best headline for a strip (one that matches a claim theme
      and hasn't been used in the last 30 strips).
   3. Writes the three lines:
        - with ANTHROPIC_API_KEY set: Claude drafts fresh lines in the
          locked 3-panel pattern, validated before use;
        - otherwise (or on any failure): a theme script from the style
          guide, rotated by date so the same lines don't repeat.
   4. Adds the strip to daily-wire.json (newest first, last 120 kept).

   It never overwrites a strip already present for today, so a strip you
   publish by hand (with your own image) always wins. Set FORCE=1 to
   replace today's auto strip.
--------------------------------------------------------------------- */
import fs from 'node:fs';

const FILE = 'daily-wire.json';
const FEED = 'https://news.google.com/rss/search?q=artificial+intelligence&hl=en-IN&gl=IN&ceid=IN:en';
const KEEP = 120;
const FORCE = process.env.FORCE === '1' || process.env.FORCE === 'true';
const API_KEY = process.env.ANTHROPIC_API_KEY || '';
const MODEL = process.env.DAILY_WIRE_MODEL || 'claude-haiku-5-5';

/* ---------- the date in IST (the wire's home edition) ---------- */
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());

/* ---------- theme scripts (from the style guide, plus a few variants) ---------- */
/* order matters: the first matching theme wins, most specific first */
const THEMES = [
  { key: 'agi', title: 'AGI Achieved (Again)', re: /\bagi\b|superintelligen|general intelligence|sentien|conscious|human[- ]level/i,
    scripts: [
      ['We have achieved AGI.', 'It can do everything. I am AGI.', 'You shall not pass that title without proof.'],
      ['AGI arrives next week!', 'It already sent me a calendar invite.', 'You shall not pass that date without a definition.'] ] },
  { key: 'security', title: 'The Bug Slayer', re: /security|cyber|hack|vulnerab|flaw|breach|malware|attack|bug|exploit|privacy|data leak/i,
    scripts: [
      ['This AI agent found 948 bugs and fixed them all.', 'And then it wrote the entire Linux kernel from scratch.', 'You shall not pass that claim.'],
      ['This AI makes every system unhackable!', 'It even changed my password to “password”. Twice.', 'You shall not pass that promise without an audit.'] ] },
  { key: 'jobs', title: 'The Job Forecast', re: /\bjobs?\b|layoff|workforce|hiring|employ|replace (workers|humans)|white[- ]collar|careers?/i,
    scripts: [
      ['This AI will replace all jobs next year!', 'Including mine. I already quit.', 'You shall not pass that prediction.'],
      ['AI will do every job by Friday!', 'I’ve already trained it to do my homework. And my chores.', 'You shall not pass that forecast without data.'] ] },
  { key: 'benchmark', title: 'Top of Every Chart', re: /benchmark|leaderboard|state[- ]of[- ]the[- ]art|\bsota\b|outperform|beats? (gpt|gemini|claude|human)|tops?\b.*\b(test|exam|ranking)/i,
    scripts: [
      ['This new AI model scores 100% on every benchmark!', 'I also beat every human expert and never hallucinate.', 'You shall not pass that claim without a source.'],
      ['It topped every leaderboard this morning!', 'Including the ones it wrote itself.', 'You shall not pass that ranking without the test set.'] ] },
  { key: 'valuation', title: 'The Trillion-Dollar Tuesday', re: /valuation|valued at|\bbillion\b|\btrillion\b|funding|raises?\b|\bipo\b|investment|invest|market cap|stake|acqui|deal/i,
    scripts: [
      ['This AI company is worth a trillion dollars!', 'And I invented their product last Tuesday.', 'You shall not pass that valuation without revenue.'],
      ['They just raised more money than the moon costs!', 'The pitch deck was one slide. It said “AI”.', 'You shall not pass that round without a business model.'] ] },
  { key: 'accuracy', title: 'Never Wrong (Mostly)', re: /hallucinat|accura|mistake|error|wrong|misinformation|fact[- ]check|reliab|trust/i,
    scripts: [
      ['This AI never makes mistakes.', 'Except when it does. But those don’t count.', 'You shall not pass that logic.'],
      ['It’s 100% accurate!', 'I checked. By asking it.', 'You shall not pass that test without a second opinion.'] ] },
  { key: 'rules', title: 'Rules? What Rules?', re: /regulat|\blaws?\b|\brules?\b|policy|\bban\b|govern|compliance|court|lawsuit|copyright|\bact\b|parliament|minister/i,
    scripts: [
      ['This AI follows every rule in every country!', 'It even wrote some of the laws. Retroactively.', 'You shall not pass that claim without a regulator.'],
      ['New rules? This AI was compliant yesterday!', 'It read all 400 pages in a second and agreed with itself.', 'You shall not pass that compliance without a lawyer.'] ] },
  { key: 'speed', title: '100x Before Breakfast', re: /faster|speed|latency|cheaper|cost|price|spend|budget|efficien|chip|gpu|compute|data cent|power|energy/i,
    scripts: [
      ['This AI is 100x faster than last month.', 'And it costs nothing. Trust me.', 'You shall not pass that number without a benchmark.'],
      ['These new chips make AI free forever!', 'The power bill just sends itself a thank-you note.', 'You shall not pass that cost without an invoice.'] ] },
  { key: 'launch', title: 'The Launch to End All Launches', re: /launch|unveil|releases?|announce|introduc|rolls? out|new model|update|feature|search|assistant|agent/i,
    scripts: [
      ['This new AI feature changes everything!', 'It also changes itself. Every hour. Without telling anyone.', 'You shall not pass that launch without release notes.'],
      ['Every app now has AI inside!', 'Even my toaster. It writes poems about bread.', 'You shall not pass that feature without a use case.'] ] },
];
const GENERIC = { key: 'generic', title: 'Big Claim Energy',
  scripts: [
    ['This AI news changes the world!', 'I read the headline. That’s the same as the article.', 'You shall not pass that take without reading the story.'],
    ['AI just solved this completely!', 'Experts agree. I am the experts.', 'You shall not pass that claim without a source.'] ] };

/* ---------- helpers ---------- */
async function get(url) {
  const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0 (aishorts-daily-wire)' } });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  return r.text();
}
const unesc = s => String(s || '')
  .replace(/<!\[CDATA\[|\]\]>/g, '')
  .replace(/&amp;/g, '&').replace(/&#39;|&apos;/g, "'").replace(/&quot;/g, '"')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();

function parseItems(xml, limit = 25) {
  const out = [];
  for (const c of String(xml).split(/<item[\s>]/).slice(1)) {
    const t = unesc((c.match(/<title>([\s\S]*?)<\/title>/) || [])[1]);
    const link = unesc((c.match(/<link>([\s\S]*?)<\/link>/) || [])[1]);
    const src = unesc((c.match(/<source[^>]*>([\s\S]*?)<\/source>/) || [])[1]);
    if (!t) continue;
    // Google News titles end with " - Publication"
    let headline = t, source = src;
    const m = t.match(/^(.*)\s+[-–—]\s+([^-–—]+)$/);
    if (m) { headline = m[1].trim(); source = source || m[2].trim(); }
    out.push({ headline, source, url: /^https?:\/\//.test(link) ? link : '' });
    if (out.length >= limit) break;
  }
  return out;
}

const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim();
const dayNum = d => Math.floor(Date.parse(d + 'T00:00:00Z') / 864e5);

function themeOf(headline) {
  return THEMES.find(t => t.re.test(headline)) || null;
}

function pick(items, recent) {
  const used = new Set(recent.map(s => norm(s.headline)));
  const lastTheme = recent[0] && recent[0].theme;
  const fresh = items.filter(i => i.headline.length > 20 && !used.has(norm(i.headline)));
  if (!fresh.length) return null;
  // prefer: matches a theme, and not the same theme as yesterday
  return fresh.find(i => { const t = themeOf(i.headline); return t && t.key !== lastTheme; })
      || fresh.find(i => themeOf(i.headline))
      || fresh[0];
}

function fromTemplate(item, theme) {
  const t = theme || GENERIC;
  const script = t.scripts[dayNum(today) % t.scripts.length];
  return { title: t.title, theme: t.key, lines: script, by: 'template' };
}

/* ---------- optional: Claude drafts fresh lines ---------- */
async function fromClaude(item, theme) {
  if (!API_KEY) return null;
  const example = (theme || GENERIC).scripts[0];
  const prompt = `You write "The Daily Wire", a 3-panel satirical comic on AIShorts.biz about over-hyped AI claims.
Characters (fixed):
- Pinocchio: a wooden puppet boy, "the AI". Eager, confident, slightly smug. Panel 1: he makes an over-the-top claim inspired by the headline. Panel 2: he exaggerates further (his nose grows).
- Gandalf: an old grey wizard, the observer. Panel 3: one dry, precise punchline that begins "You shall not pass".

Today's headline (from ${item.source || 'the wire'}): "${item.headline}"

Rules:
- Each line at most 12 words. Plain, family-friendly, clever.
- Lampoon the HYPE around the topic, not real people. Never put invented quotes, numbers or claims in the mouth of a real company, product or person — speak about "this AI", "this model", "this company" instead.
- No brand names, no names of real people.
- Panel 3 must start with "You shall not pass".

Example of the pattern: ${JSON.stringify(example)}

Reply with JSON only: {"title":"2-5 word strip title","p1":"...","p2":"...","p3":"..."}`;

  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'x-api-key': API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({ model: MODEL, max_tokens: 300, messages: [{ role: 'user', content: prompt }] }),
    });
    if (!r.ok) throw new Error(`API ${r.status}: ${(await r.text()).slice(0, 200)}`);
    const d = await r.json();
    const text = (d.content || []).map(c => c.text || '').join('');
    const j = JSON.parse((text.match(/\{[\s\S]*\}/) || ['{}'])[0]);
    const lines = [j.p1, j.p2, j.p3].map(x => String(x || '').trim());
    const words = s => s.split(/\s+/).filter(Boolean).length;
    const ok = lines.every(l => l && words(l) <= 16 && l.length <= 110)
      && /^you shall not pass/i.test(lines[2])
      && j.title && String(j.title).length <= 48;
    if (!ok) throw new Error('draft failed validation: ' + JSON.stringify(j));
    return { title: String(j.title).trim(), theme: (theme || GENERIC).key, lines, by: 'claude' };
  } catch (e) {
    console.warn('Claude draft skipped — using template.', e.message);
    return null;
  }
}

/* ---------- main ---------- */
const data = fs.existsSync(FILE) ? JSON.parse(fs.readFileSync(FILE, 'utf8')) : {};
data.series = data.series || 'The Daily Wire — Pinocchio & Gandalf';
data.strips = Array.isArray(data.strips) ? data.strips : [];

const existing = data.strips.find(s => s.date === today);
if (existing && !(FORCE && existing.auto)) {
  console.log(`Strip for ${today} already present (${existing.auto ? 'auto' : 'hand-made'}) — leaving it.`);
  process.exit(0);
}

const items = parseItems(process.env.FEED_FILE ? fs.readFileSync(process.env.FEED_FILE, 'utf8') : await get(FEED));
if (!items.length) { console.log('No headlines in the feed — nothing to do.'); process.exit(0); }

const recent = data.strips.filter(s => s.date !== today).sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 30);
const item = pick(items, recent);
if (!item) { console.log('Every headline was already used recently — skipping today.'); process.exit(0); }

const theme = themeOf(item.headline);
const draft = (await fromClaude(item, theme)) || fromTemplate(item, theme);

const strip = {
  date: today,
  title: draft.title,
  headline: item.headline,
  source: item.source || '',
  url: item.url || ('https://news.google.com/search?q=' + encodeURIComponent(item.headline)),
  image: '',
  alt: '',
  theme: draft.theme,
  auto: true,
  panels: [
    { speaker: 'Pinocchio', line: draft.lines[0] },
    { speaker: 'Pinocchio', line: draft.lines[1] },
    { speaker: 'Gandalf', line: draft.lines[2] },
  ],
};

data.strips = [strip, ...data.strips.filter(s => s.date !== today)]
  .sort((a, b) => (a.date < b.date ? 1 : -1))
  .slice(0, KEEP);
fs.writeFileSync(FILE, JSON.stringify(data, null, 2) + '\n');
console.log(`Daily Wire ${today} [${draft.by}/${draft.theme}]: "${strip.title}" ← ${item.headline} (${item.source})`);
