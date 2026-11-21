/* AIShorts festive themes — one global festival per month.
 *
 * Each theme shows from 3 days before the festival to 2 days after it
 * (reader's local date), then the page falls back to the normal broadsheet
 * look on its own. The nameplate becomes a themed plate (colours, motifs and
 * light CSS animation) after the "AIShorts Seasonal Design Themes" reference
 * sheet, and a slim greeting ribbon the reader can dismiss sits under it.
 *
 * Preview any theme: aishorts.biz/?festival=diwali   (?festival=off to disable)
 *
 * YEARLY UPKEEP: festivals that follow a lunar calendar have exact dates in
 * MOVABLE below. Add the next year's dates before it starts (dates loaded
 * through 2028). Fixed-date festivals and Thanksgiving are computed.
 */
(function () {
  'use strict';
  var BEFORE = 3, AFTER = 2;

  var MOVABLE = {
    lunar:     { 2027: '2027-02-06', 2028: '2028-01-26' },
    holi:      { 2027: '2027-03-22', 2028: '2028-03-11' },
    vesak:     { 2027: '2027-05-20', 2028: '2028-05-08' },
    rakhi:     { 2027: '2027-08-17', 2028: '2028-08-05' },
    midautumn: { 2027: '2027-09-15', 2028: '2028-10-03' },
    diwali:    { 2027: '2027-10-29', 2028: '2028-10-17' }
  };
  var ZODIAC = { 2027: 'Goat', 2028: 'Monkey' };

  // month slot → festival. fixed: [month, day]; nth: Thanksgiving rule.
  var FESTIVALS = [
    { id: 'newyear',      month: 1,  fixed: [1, 1],   c1: '#1b2a5c', c2: '#c9a227', greet: function (y) { return 'Happy New Year ' + y; },             sub: 'Here’s to the year ahead in AI' },
    { id: 'lunar',        month: 2,                   c1: '#b3121f', c2: '#d4a017', greet: function (y) { return 'Happy Lunar New Year' + (ZODIAC[y] ? ' — Year of the ' + ZODIAC[y] : ''); }, sub: 'Spring Festival · Tết · Seollal' },
    { id: 'holi',         month: 3,                   c1: '#d4007a', c2: '#ff8c1a', greet: function () { return 'Happy Holi'; },                          sub: 'The festival of colours' },
    { id: 'songkran',     month: 4,  fixed: [4, 13],  c1: '#0a6fa8', c2: '#2a9d8f', greet: function () { return 'Happy Songkran & Vaisakhi'; },           sub: 'The spring new year' },
    { id: 'vesak',        month: 5,                   c1: '#c28a00', c2: '#e8b4b8', greet: function () { return 'Happy Vesak'; },                         sub: 'Buddha Day · Buddha Purnima' },
    { id: 'midsummer',    month: 6,  fixed: [6, 21],  c1: '#d9631e', c2: '#f2b134', greet: function () { return 'Happy Midsummer'; },                     sub: 'And International Day of Yoga' },
    { id: 'tanabata',     month: 7,  fixed: [7, 7],   c1: '#2f2a7a', c2: '#e3b505', greet: function () { return 'Happy Tanabata'; },                      sub: 'The star festival' },
    { id: 'rakhi',        month: 8,                   c1: '#e05a00', c2: '#c1121f', greet: function () { return 'Happy Raksha Bandhan'; },                sub: 'The bond of protection' },
    { id: 'midautumn',    month: 9,                   c1: '#9a5b13', c2: '#e6b450', greet: function () { return 'Happy Mid-Autumn Festival'; },           sub: 'Mooncakes and lanterns' },
    { id: 'diwali',       month: 10,                  c1: '#d9480f', c2: '#f5a400', greet: function () { return 'Happy Diwali'; },                        sub: 'The festival of lights' },
    { id: 'thanksgiving', month: 11, nth: [11, 4, 4], c1: '#8a3b12', c2: '#c98b1b', greet: function () { return 'Happy Thanksgiving'; },                  sub: 'A season of gratitude' },
    { id: 'christmas',    month: 12, fixed: [12, 25], c1: '#1d6b3a', c2: '#c8102e', greet: function () { return 'Merry Christmas'; },                     sub: 'Season’s greetings from AIShorts' }
  ];

  // Small inline motifs (24×24), drawn in the festival colours.
  var MOTIF = {
    newyear:      '<path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3.5 3.5M15.5 15.5L19 19M19 5l-3.5 3.5M8.5 15.5L5 19" stroke="var(--fest-2)" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="12" r="2.2" fill="var(--fest)"/>',
    lunar:        '<path d="M12 2v2" stroke="var(--fest-2)" stroke-width="1.6"/><rect x="9" y="4" width="6" height="2" rx="1" fill="var(--fest-2)"/><ellipse cx="12" cy="12.5" rx="7" ry="6.5" fill="var(--fest)"/><path d="M12 6v13M7.5 8.5c-1.5 2.5-1.5 5.5 0 8M16.5 8.5c1.5 2.5 1.5 5.5 0 8" stroke="var(--fest-2)" stroke-width="1"/><rect x="9" y="19" width="6" height="2" rx="1" fill="var(--fest-2)"/>',
    holi:         '<circle cx="8" cy="9" r="5" fill="var(--fest)" opacity=".85"/><circle cx="15.5" cy="8" r="4" fill="var(--fest-2)" opacity=".85"/><circle cx="13" cy="15.5" r="5" fill="#2bb673" opacity=".75"/><circle cx="6" cy="17" r="2.2" fill="#3a86ff" opacity=".8"/>',
    songkran:     '<path d="M12 3c3 4.5 6 7.6 6 11a6 6 0 0 1-12 0c0-3.4 3-6.5 6-11z" fill="var(--fest)"/><path d="M9 14.5a3 3 0 0 0 3 3" stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round"/>',
    vesak:        '<path d="M12 5c2 3 2 7 0 11-2-4-2-8 0-11z" fill="var(--fest-2)"/><path d="M12 16c-1-4-4-7-8-7 0 4 3 7 8 7zM12 16c1-4 4-7 8-7 0 4-3 7-8 7z" fill="var(--fest)" opacity=".9"/><path d="M5 19h14" stroke="var(--fest)" stroke-width="1.6" stroke-linecap="round"/>',
    midsummer:    '<circle cx="12" cy="12" r="5" fill="var(--fest-2)"/><g stroke="var(--fest)" stroke-width="1.8" stroke-linecap="round"><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/></g>',
    tanabata:     '<path d="M12 3l2.1 5.4 5.7.4-4.4 3.6 1.4 5.6L12 15l-4.8 3 1.4-5.6L4.2 8.8l5.7-.4z" fill="var(--fest-2)"/><path d="M4 20c4-2 12-2 16 0" stroke="var(--fest)" stroke-width="1.6" fill="none" stroke-linecap="round"/>',
    rakhi:        '<path d="M2 12h20" stroke="var(--fest-2)" stroke-width="1.8"/><circle cx="12" cy="12" r="5.5" fill="var(--fest)"/><circle cx="12" cy="12" r="2.4" fill="var(--fest-2)"/><g fill="var(--fest-2)"><circle cx="12" cy="5.2" r="1"/><circle cx="12" cy="18.8" r="1"/><circle cx="5.2" cy="12" r="1"/><circle cx="18.8" cy="12" r="1"/></g>',
    midautumn:    '<circle cx="12" cy="11" r="8" fill="var(--fest-2)"/><circle cx="9" cy="9" r="1.4" fill="var(--fest)" opacity=".35"/><circle cx="14.5" cy="13" r="2" fill="var(--fest)" opacity=".3"/><path d="M6 21h12" stroke="var(--fest)" stroke-width="1.6" stroke-linecap="round"/>',
    diwali:       '<path d="M12 4c1.6 2.2 2.2 3.9 0 6.2C9.8 7.9 10.4 6.2 12 4z" fill="var(--fest-2)"/><path d="M4 14h16c-.6 3.6-3.8 6-8 6s-7.4-2.4-8-6z" fill="var(--fest)"/><path d="M8 14c0-1.6 1.8-3 4-3s4 1.4 4 3" fill="var(--fest)" opacity=".7"/>',
    thanksgiving: '<path d="M12 21V8" stroke="var(--fest)" stroke-width="1.6" stroke-linecap="round"/><g fill="var(--fest-2)"><ellipse cx="12" cy="5" rx="1.8" ry="2.6"/><ellipse cx="9.4" cy="9" rx="1.6" ry="2.4" transform="rotate(-30 9.4 9)"/><ellipse cx="14.6" cy="9" rx="1.6" ry="2.4" transform="rotate(30 14.6 9)"/><ellipse cx="9.6" cy="13.6" rx="1.6" ry="2.4" transform="rotate(-30 9.6 13.6)"/><ellipse cx="14.4" cy="13.6" rx="1.6" ry="2.4" transform="rotate(30 14.4 13.6)"/></g>',
    christmas:    '<path d="M12 3l7 11H5z" fill="var(--fest)"/><path d="M12 8l8 10H4z" fill="var(--fest)"/><rect x="10.8" y="18" width="2.4" height="3" fill="#7a4a1d"/><circle cx="12" cy="3" r="1.6" fill="var(--fest-2)"/><circle cx="9" cy="14" r=".9" fill="var(--fest-2)"/><circle cx="14.6" cy="12" r=".9" fill="var(--fest-2)"/>'
  };

  function ymd(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  function addDays(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function parse(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function nthWeekday(y, month, weekday, n) { // weekday 0=Sun; Thanksgiving = 4th Thursday
    var d = new Date(y, month - 1, 1);
    var shift = (weekday - d.getDay() + 7) % 7;
    return new Date(y, month - 1, 1 + shift + (n - 1) * 7);
  }
  function dateFor(f, y) {
    if (f.fixed) return new Date(y, f.fixed[0] - 1, f.fixed[1]);
    if (f.nth) return nthWeekday(y, f.nth[0], f.nth[1], f.nth[2]);
    var s = MOVABLE[f.id] && MOVABLE[f.id][y];
    return s ? parse(s) : null;
  }
  function active(today) {
    var y = today.getFullYear();
    for (var i = 0; i < FESTIVALS.length; i++) {
      for (var yy = y - 1; yy <= y + 1; yy++) {
        var d = dateFor(FESTIVALS[i], yy);
        if (d && today >= addDays(d, -BEFORE) && today <= addDays(d, AFTER)) return { f: FESTIVALS[i], year: yy };
      }
    }
    return null;
  }
  function byId(id) { for (var i = 0; i < FESTIVALS.length; i++) if (FESTIVALS[i].id === id) return FESTIVALS[i]; return null; }

  function pick() {
    var q = null;
    try { q = new URLSearchParams(location.search).get('festival'); } catch (e) {}
    if (q === 'off') return null;
    if (q && byId(q)) {
      var t0 = ymd(new Date()), y0 = t0.getFullYear(), yy = y0;
      for (var k = y0; k <= y0 + 1; k++) { var dk = dateFor(byId(q), k); if (dk && addDays(dk, AFTER) >= t0) { yy = k; break; } }
      return { f: byId(q), year: yy };
    }
    return active(ymd(new Date()));
  }

  // ---------------------------------------------------------------------------
  // Masthead plates — one per festival, after Pawan's "AIShorts Seasonal Design
  // Themes" reference sheet. Only the nameplate is restyled; the rest of the
  // page keeps the broadsheet look. Everything is CSS/SVG, no images, and all
  // motion is transform/opacity only and switches off under reduced motion.
  // ---------------------------------------------------------------------------
  var GOLD = 'linear-gradient(180deg,#fff3c4 0%,#f5d36b 38%,#c8961e 70%,#f7dc84 100%)';
  var THEMES = {
    christmas:    { bg: 'linear-gradient(112deg,#b3121f 0 12%,#f7f1e3 12% 88%,#1d5e33 88%)', wm: '#141414', meta: '#5b4a36', deco: ['holly', 'snow', 'cap'] },
    newyear:      { bg: 'radial-gradient(ellipse at 50% 45%,#1a2a63 0,#0a1233 72%)', dark: 1, wmAi: GOLD, wm: '#ffffff', meta: '#e6c86a', deco: ['fireworks', 'twinkle'] },
    lunar:        { bg: 'radial-gradient(ellipse at 50% 50%,#c8171d 0,#8f0c12 85%)', dark: 1, wm: GOLD, shadow: '#5a0408', meta: '#f6d27a', deco: ['clouds', 'lanterns'] },
    holi:         { bg: '#ffffff', wm: '#0d0d0d', meta: '#3a3a3a', deco: ['powder'] },
    songkran:     { bg: 'linear-gradient(180deg,#f1fafe 0,#d6effa 55%,#a9dcf0 100%)', wm: '#0b5d73', meta: '#2f6f7e', deco: ['waves', 'leaves'] },
    vesak:        { bg: 'radial-gradient(ellipse at 50% 35%,#fffaf0 0,#f1e4c3 90%)', wmAi: '#0f5e5a', wm: GOLD, shadow: 'rgba(90,60,10,.25)', meta: '#7a6232', deco: ['lattice', 'glow', 'lotus'] },
    midsummer:    { bg: 'radial-gradient(circle at 50% 130%,#fffdf0 0,#fff1bf 38%,#ffd98a 70%,#f7b267 100%)', wmAi: 'linear-gradient(180deg,#f0b429 0,#c98a0b 55%,#8a5a00 100%)', shadow: 'rgba(120,60,0,.25)', wm: '#1d5fa8', meta: '#6b3500', deco: ['rays', 'birds'] },
    tanabata:     { bg: 'linear-gradient(180deg,#081238 0,#15287a 100%)', dark: 1, wm: 'linear-gradient(180deg,#ffffff 0,#dfe6ff 55%,#a9b8ff 100%)', meta: '#cdd6ff', deco: ['stars', 'streamer'] },
    rakhi:        { bg: 'radial-gradient(ellipse at 50% 40%,#f79a1c 0,#d9480f 90%)', dark: 1, wm: 'linear-gradient(180deg,#fff7e0 0,#ffd27a 60%,#f5a623 100%)', shadow: '#7a2a00', meta: '#fff1d6', deco: ['marigolds', 'rakhis', 'diyas'] },
    midautumn:    { bg: 'linear-gradient(180deg,#fdf7e6 0,#f3e3bd 100%)', wm: '#5a3d1e', meta: '#8a6a3a', deco: ['moon', 'goldlanterns', 'mist'] },
    diwali:       { bg: 'radial-gradient(ellipse at 50% 45%,#d9530f 0,#9a3412 55%,#4a1504 100%)', dark: 1, wm: GOLD, shadow: '#3a0e00', meta: '#ffe1a8', deco: ['rangoli', 'bokeh', 'diyas'] },
    thanksgiving: { bg: '#f4e7c9', frame: '#8a5a2b', wm: '#8a2a14', meta: '#6b4a2a', deco: ['wheat', 'fall'] }
  };

  function rnd(a, b) { return a + Math.random() * (b - a); }
  function fx(cls, style, html) { return '<span class="fx ' + cls + '" style="' + (style || '') + '">' + (html || '') + '</span>'; }

  var SVG = {
    holly: '<svg viewBox="0 0 60 40"><path d="M4 30C10 22 18 26 22 18c3 7 12 4 14 12-8-2-12 6-20 2-4 4-8 0-12-2z" fill="#1d6b3a"/><path d="M26 16c6-8 14-4 18-12 2 8 12 6 12 14-8-2-11 6-19 1-4 3-9-1-11-3z" fill="#2a7d46"/><circle cx="25" cy="21" r="3.4" fill="#c8102e"/><circle cx="30" cy="24" r="3" fill="#d62828"/><circle cx="29" cy="18" r="2.8" fill="#b3121f"/></svg>',
    lantern: function (c) { return '<svg viewBox="0 0 20 44"><path d="M10 0v8" stroke="#e3b341" stroke-width="1"/><rect x="5.5" y="7" width="9" height="2.4" rx="1" fill="#e3b341"/><ellipse cx="10" cy="18.5" rx="9" ry="9.5" fill="' + c + '"/><path d="M10 9v19M5.2 11c-2 4.5-2 10.5 0 15M14.8 11c2 4.5 2 10.5 0 15" stroke="#f6d27a" stroke-width=".7" fill="none"/><rect x="5.5" y="27.6" width="9" height="2.4" rx="1" fill="#e3b341"/><path d="M8 30l-1 9M10 30v11M12 30l1 9" stroke="#e3b341" stroke-width=".8"/></svg>'; },
    firework: function (c) { var s = '<svg viewBox="-30 -30 60 60">'; for (var i = 0; i < 16; i++) { var a = i / 16 * Math.PI * 2, r1 = 7, r2 = i % 2 ? 22 : 28; s += '<line x1="' + (Math.cos(a) * r1).toFixed(1) + '" y1="' + (Math.sin(a) * r1).toFixed(1) + '" x2="' + (Math.cos(a) * r2).toFixed(1) + '" y2="' + (Math.sin(a) * r2).toFixed(1) + '" stroke="' + c + '" stroke-width="1.3" stroke-linecap="round"/><circle cx="' + (Math.cos(a) * (r2 + 2)).toFixed(1) + '" cy="' + (Math.sin(a) * (r2 + 2)).toFixed(1) + '" r="1.1" fill="#fff"/>'; } return s + '</svg>'; },
    leaf: '<svg viewBox="0 0 30 16"><path d="M1 8C8 0 22 0 29 8 22 16 8 16 1 8z" fill="#5cb85c"/><path d="M2 8h26" stroke="#3d8b3d" stroke-width=".8"/></svg>',
    lotus: '<svg viewBox="0 0 60 34"><path d="M30 4c5 7 5 17 0 26-5-9-5-19 0-26z" fill="#f2b6b9"/><path d="M30 30c-3-9-11-16-22-17 1 9 10 16 22 17zM30 30c3-9 11-16 22-17-1 9-10 16-22 17z" fill="#e8949a"/><path d="M30 30C24 24 12 22 2 25c6 5 17 7 28 5zM30 30c6-6 18-8 28-5-6 5-17 7-28 5z" fill="#d4a017" opacity=".85"/></svg>',
    bird: '<svg viewBox="0 0 20 8"><path d="M1 6c3-4 6-4 9 0 3-4 6-4 9 0" stroke="#5a3a1a" stroke-width="1.3" fill="none" stroke-linecap="round"/></svg>',
    marigold: '<svg viewBox="0 0 40 40">' + (function () { var s = ''; for (var i = 0; i < 12; i++) { var a = i / 12 * Math.PI * 2; s += '<circle cx="' + (20 + Math.cos(a) * 11).toFixed(1) + '" cy="' + (20 + Math.sin(a) * 11).toFixed(1) + '" r="7" fill="' + (i % 2 ? '#f59e0b' : '#f97316') + '"/>'; } return s; })() + '<circle cx="20" cy="20" r="8" fill="#fbbf24"/><circle cx="20" cy="20" r="3.5" fill="#c2410c"/></svg>',
    rakhi: '<svg viewBox="0 0 30 60"><path d="M15 0v22" stroke="#b91c1c" stroke-width="1.4"/><circle cx="15" cy="32" r="11" fill="#e3b341"/><circle cx="15" cy="32" r="7" fill="#fbbf24" stroke="#b45309" stroke-width=".8"/><circle cx="15" cy="32" r="3.2" fill="#b91c1c"/><g fill="#38bdf8"><circle cx="15" cy="22.6" r="1.3"/><circle cx="15" cy="41.4" r="1.3"/><circle cx="5.6" cy="32" r="1.3"/><circle cx="24.4" cy="32" r="1.3"/></g><path d="M13 43l-2 14M15 43v16M17 43l2 14" stroke="#b91c1c" stroke-width="1.2"/></svg>',
    diya: '<svg viewBox="0 0 40 30"><path d="M2 16h36c-2 8-9 12-18 12S4 24 2 16z" fill="#b45309"/><path d="M6 16h28c-2 5-7 8-14 8S8 21 6 16z" fill="#d97706"/><g class="flame"><path d="M20 2c4 5 4 9 0 13-4-4-4-8 0-13z" fill="#fbbf24"/><path d="M20 7c2 3 2 5 0 7-2-2-2-4 0-7z" fill="#fff7cc"/></g></svg>',
    rangoli: '<svg viewBox="0 0 80 80">' + (function () { var s = ''; for (var r = 0; r < 3; r++) for (var i = 0; i < 12; i++) { var a = i / 12 * Math.PI * 2 + r * .26, d = 14 + r * 13; s += '<ellipse cx="' + (40 + Math.cos(a) * d).toFixed(1) + '" cy="' + (40 + Math.sin(a) * d).toFixed(1) + '" rx="' + (5 - r) + '" ry="' + (2.6 - r * .5) + '" transform="rotate(' + (a * 180 / Math.PI).toFixed(0) + ' ' + (40 + Math.cos(a) * d).toFixed(1) + ' ' + (40 + Math.sin(a) * d).toFixed(1) + ')" fill="' + ['#fbbf24', '#f97316', '#fde68a'][r] + '"/>'; } return s; })() + '<circle cx="40" cy="40" r="7" fill="#fbbf24"/></svg>',
    wheat: '<svg viewBox="0 0 30 80"><path d="M15 80V16" stroke="#a16207" stroke-width="1.4"/>' + (function () { var s = ''; for (var i = 0; i < 7; i++) { var y = 14 + i * 7; s += '<ellipse cx="11" cy="' + y + '" rx="3" ry="5.5" transform="rotate(-28 11 ' + y + ')" fill="#d4a017"/><ellipse cx="19" cy="' + y + '" rx="3" ry="5.5" transform="rotate(28 19 ' + y + ')" fill="#c8961e"/>'; } return s; })() + '<ellipse cx="15" cy="8" rx="3" ry="6" fill="#d4a017"/></svg>',
    maple: function (c) { return '<svg viewBox="0 0 20 20"><path d="M10 1l2 5 4-2-1 5 4 1-4 3 1 3-4-1-2 4-2-4-4 1 1-3-4-3 4-1-1-5 4 2z" fill="' + c + '"/></svg>'; },
    waves: '<svg viewBox="0 0 400 30" preserveAspectRatio="none"><path d="M0 15 Q25 3 50 15 T100 15 T150 15 T200 15 T250 15 T300 15 T350 15 T400 15 V30 H0z" fill="rgba(255,255,255,.65)"/></svg>',
    waves2: '<svg viewBox="0 0 400 30" preserveAspectRatio="none"><path d="M0 18 Q25 8 50 18 T100 18 T150 18 T200 18 T250 18 T300 18 T350 18 T400 18 V30 H0z" fill="rgba(10,111,168,.22)"/></svg>',
    streamer: '<svg viewBox="0 0 400 60" preserveAspectRatio="none"><defs><linearGradient id="fstg" x1="0" x2="1"><stop offset="0" stop-color="#8ec5ff" stop-opacity="0"/><stop offset=".5" stop-color="#c8d4ff" stop-opacity=".9"/><stop offset="1" stop-color="#8ec5ff" stop-opacity="0"/></linearGradient></defs><path class="st" d="M0 44 C80 10 140 60 210 30 S330 6 400 26" stroke="url(#fstg)" stroke-width="2" fill="none"/><path class="st st2" d="M0 50 C90 24 150 66 220 38 S340 16 400 34" stroke="url(#fstg)" stroke-width="1.2" fill="none"/></svg>'
  };

  function decorate(id, t, once, reduce) {
    var h = '', d = t.deco, has = function (k) { return d.indexOf(k) >= 0; }, i;
    if (has('holly')) h += fx('corner', 'left:-6px;top:-4px;width:64px', SVG.holly) + fx('corner', 'right:-6px;bottom:-6px;width:64px;transform:rotate(180deg)', SVG.holly);
    if (has('snow') && !reduce) for (i = 0; i < 20; i++) h += fx('flake', 'left:' + rnd(0, 100).toFixed(1) + '%;--s:' + rnd(2, 4.5).toFixed(1) + 'px;--t:' + rnd(6, 11).toFixed(1) + 's;--d:-' + rnd(0, 11).toFixed(1) + 's;--x:' + Math.round(rnd(-18, 18)) + 'px');
    if (has('fireworks')) [['9%', '20%', 56, '#f5d36b', 0], ['84%', '12%', 64, '#ffffff', 1.1], ['72%', '62%', 34, '#f5d36b', 2], ['22%', '66%', 30, '#9fb6ff', 1.6]].forEach(function (f) {
      h += fx('fw', 'left:' + f[0] + ';top:' + f[1] + ';width:' + f[2] + 'px;height:' + f[2] + 'px;--d:' + f[4] + 's', SVG.firework(f[3]));
    });
    if (has('twinkle') || has('stars')) for (i = 0; i < (has('stars') ? 34 : 16); i++) h += fx('star', 'left:' + rnd(0, 100).toFixed(1) + '%;top:' + rnd(0, 100).toFixed(1) + '%;--s:' + rnd(1, 2.6).toFixed(1) + 'px;--d:-' + rnd(0, 3).toFixed(1) + 's');
    if (has('clouds')) h += fx('clouds', '');
    if (has('lanterns')) [['3%', 22, '#d62828', 0], ['12%', 16, '#e63946', .7], ['84%', 16, '#e63946', .4], ['92%', 22, '#d62828', 1.1]].forEach(function (l) {
      h += fx('hang', 'left:' + l[0] + ';width:' + l[1] + 'px;--d:-' + l[3] + 's', SVG.lantern(l[2]));
    });
    if (has('powder')) [['-6%', '#ff2e93', 0], ['14%', '#ff8c1a', .12], ['32%', '#ffd60a', .24], ['50%', '#2bb673', .36], ['68%', '#3a86ff', .48], ['86%', '#9b5de5', .6]].forEach(function (p) {
      h += fx('powder', 'left:' + p[0] + ';--b:' + p[1] + ';--d:' + p[2] + 's');
    });
    if (has('powder')) for (i = 0; i < 26; i++) h += fx('dot', 'left:' + rnd(0, 100).toFixed(1) + '%;top:' + rnd(5, 95).toFixed(1) + '%;--s:' + rnd(2, 5).toFixed(1) + 'px;--b:' + ['#ff2e93', '#ff8c1a', '#ffd60a', '#2bb673', '#3a86ff', '#9b5de5'][i % 6] + ';--d:' + rnd(.3, 1.1).toFixed(2) + 's');
    if (has('waves')) h += fx('wave w1', '', SVG.waves + SVG.waves) + fx('wave w2', '', SVG.waves2 + SVG.waves2);
    if (has('leaves')) h += fx('corner sway', 'left:-2px;top:2px;width:34px;--d:0s;transform:rotate(25deg)', SVG.leaf) + fx('corner sway', 'left:10px;top:14px;width:24px;--d:-1s;transform:rotate(-15deg)', SVG.leaf) + fx('corner sway', 'right:-2px;top:4px;width:34px;--d:-2s;transform:scaleX(-1) rotate(25deg)', SVG.leaf);
    if (has('lattice')) h += fx('lattice', '');
    if (has('glow')) h += fx('glow', '');
    if (has('lotus')) h += fx('corner', 'left:1%;bottom:-3px;width:52px', SVG.lotus) + fx('corner', 'right:1%;bottom:-3px;width:52px', SVG.lotus);
    if (has('rays')) h += fx('rays', '') + fx('sun', '');
    if (has('birds')) [['18%', '18%', 0], ['26%', '10%', -3], ['76%', '16%', -6]].forEach(function (b) { h += fx('bird', 'left:' + b[0] + ';top:' + b[1] + ';--d:' + b[2] + 's', SVG.bird); });
    if (has('streamer')) h += fx('streamer', '', SVG.streamer) + (reduce ? '' : fx('shoot', ''));
    if (has('marigolds')) h += fx('corner', 'left:-10px;bottom:-12px;width:38px', SVG.marigold) + fx('corner', 'left:18px;bottom:-16px;width:28px', SVG.marigold) + fx('corner', 'right:-10px;bottom:-12px;width:38px', SVG.marigold) + fx('corner', 'right:18px;bottom:-16px;width:28px', SVG.marigold);
    if (has('rakhis')) h += fx('hang', 'left:5%;width:16px;--d:0s', SVG.rakhi) + fx('hang', 'right:5%;width:16px;--d:-1.4s', SVG.rakhi);
    if (has('rangoli')) h += fx('corner spin', 'left:-34px;top:-34px;width:78px', SVG.rangoli) + fx('corner spin', 'right:-34px;bottom:-34px;width:78px', SVG.rangoli);
    if (has('bokeh')) for (i = 0; i < 14; i++) h += fx('bokeh', 'left:' + rnd(0, 100).toFixed(1) + '%;--s:' + rnd(4, 10).toFixed(1) + 'px;--t:' + rnd(7, 12).toFixed(1) + 's;--d:-' + rnd(0, 12).toFixed(1) + 's');
    if (has('diyas')) h += fx('diya', 'left:20%;width:28px', SVG.diya) + fx('diya', 'right:20%;width:28px;--d:-.6s', SVG.diya);
    if (has('moon')) h += fx('moon', '');
    if (has('goldlanterns')) h += fx('hang', 'left:4%;width:16px;--d:0s', SVG.lantern('#f2c14e')) + fx('hang', 'left:12%;width:12px;--d:-.9s', SVG.lantern('#e9a23b'));
    if (has('mist')) h += fx('mist', '');
    if (has('wheat')) h += fx('corner sway', 'left:4px;bottom:-6px;width:20px;--d:0s', SVG.wheat) + fx('corner sway', 'left:18px;bottom:-12px;width:16px;--d:-1.2s', SVG.wheat) + fx('corner sway', 'right:4px;bottom:-6px;width:20px;--d:-.6s', SVG.wheat) + fx('corner sway', 'right:18px;bottom:-12px;width:16px;--d:-1.8s', SVG.wheat);
    if (has('fall') && !reduce) for (i = 0; i < 6; i++) h += fx('maple', 'left:' + rnd(5, 95).toFixed(1) + '%;--t:' + rnd(8, 12).toFixed(1) + 's;--d:-' + rnd(0, 12).toFixed(1) + 's;--x:' + Math.round(rnd(-30, 30)) + 'px', SVG.maple(['#c2410c', '#b45309', '#a16207'][i % 3]));
    return h;
  }

  var CSS =
    /* plate */
    'html[data-festival] .nameplate{isolation:isolate;background:var(--np-bg);border-bottom-color:transparent}' +
    'html[data-festival] .stockbar{border-bottom-color:var(--fest)}' +
    'html[data-festival] .chip.active{border-bottom-color:var(--fest)}' +
    'html[data-festival] .wordmark{color:var(--np-wm);text-shadow:0 1px 0 var(--np-shadow,transparent)}' +
    'html[data-fest-grad] .wordmark .wm-rest,html[data-fest-grad-ai] .wordmark .wm-ai{background:var(--np-grad);-webkit-background-clip:text;background-clip:text;color:transparent;text-shadow:none;filter:drop-shadow(0 1px 0 var(--np-shadow,rgba(0,0,0,.3)))}' +
    'html[data-fest-grad-ai] .wordmark .wm-ai{background:var(--np-grad-ai);-webkit-background-clip:text;background-clip:text;color:transparent;text-shadow:none}' +
    'html[data-fest-grad] .wordmark .wm-ai:not([data-own]){background:var(--np-grad);-webkit-background-clip:text;background-clip:text;color:transparent;text-shadow:none;filter:drop-shadow(0 1px 0 var(--np-shadow,rgba(0,0,0,.3)))}' +
    'html[data-festival] .nameplate .slogan,html[data-festival] .nameplate .dateline-side,html[data-festival] .nameplate .publisher,html[data-festival] .nameplate .publisher a{color:var(--np-meta)}' +
    'html[data-festival] .nameplate .publisher a{border-bottom-color:currentColor}' +
    'html[data-festival] .nameplate .ear .eng-btn{background:rgba(255,255,255,.92);color:#121212;border-color:#121212}' +
    'html[data-fest-dark] .nameplate .ear .eng-btn{border-color:transparent;box-shadow:0 1px 4px rgba(0,0,0,.35)}' +
    'html[data-festival] .nameplate .ear .eng-btn:hover{background:#121212;color:#fff}' +
    'html[data-festival="thanksgiving"] .nameplate{box-shadow:inset 0 0 0 4px #f4e7c9,inset 0 0 0 5px var(--np-frame),inset 0 0 0 7px #f4e7c9,inset 0 0 0 8px var(--np-frame)}' +
    '.fest-fx{position:absolute;inset:0;overflow:hidden;pointer-events:none;z-index:-1}' +
    '.fest-fx .fx{position:absolute;display:block;pointer-events:none}.fest-fx svg{display:block;width:100%;height:auto;overflow:visible}' +
    /* snow, stars */
    '.fx.flake{top:-6px;width:var(--s);height:var(--s);border-radius:50%;background:#fff;box-shadow:0 0 1px rgba(0,0,0,.35);animation:fsnow var(--t) linear infinite;animation-delay:var(--d)}' +
    '@keyframes fsnow{0%{transform:translate(0,0);opacity:0}10%{opacity:1}90%{opacity:.9}100%{transform:translate(var(--x),130px);opacity:0}}' +
    '.fx.star{width:var(--s);height:var(--s);border-radius:50%;background:#fff;box-shadow:0 0 4px #fff;animation:ftw 3s ease-in-out infinite;animation-delay:var(--d)}' +
    '@keyframes ftw{0%,100%{opacity:.25}50%{opacity:1}}' +
    /* fireworks */
    '.fx.fw{transform:translate(-50%,-50%) scale(.2);opacity:0;animation:ffw 3.4s ease-out infinite;animation-delay:var(--d)}' +
    '@keyframes ffw{0%{transform:translate(-50%,-50%) scale(.15);opacity:0}8%{opacity:1}45%{transform:translate(-50%,-50%) scale(1);opacity:.95}70%,100%{transform:translate(-50%,-50%) scale(1.08);opacity:0}}' +
    /* hanging things */
    '.fx.hang{top:0;transform-origin:50% 0;animation:fsway 4.5s ease-in-out infinite;animation-delay:var(--d)}' +
    '.fx.sway{transform-origin:50% 100%;animation:fsway2 5s ease-in-out infinite;animation-delay:var(--d)}' +
    '@keyframes fsway{0%,100%{transform:rotate(-5deg)}50%{transform:rotate(5deg)}}' +
    '@keyframes fsway2{0%,100%{rotate:-3deg}50%{rotate:3deg}}' +
    '.fx.clouds{inset:0;opacity:.22;background:radial-gradient(circle at 8% 110%,#f6d27a 0 18px,transparent 19px),radial-gradient(circle at 18% 118%,#f6d27a 0 22px,transparent 23px),radial-gradient(circle at 82% 115%,#f6d27a 0 20px,transparent 21px),radial-gradient(circle at 93% 108%,#f6d27a 0 16px,transparent 17px),radial-gradient(circle,#f6d27a 1px,transparent 1.5px) 0 0/14px 14px}' +
    /* holi */
    '.fx.powder{top:12%;width:34%;height:76%;border-radius:50%;background:radial-gradient(ellipse at center,var(--b) 0,var(--b) 30%,color-mix(in srgb,var(--b) 55%,transparent) 52%,transparent 70%);filter:blur(4px);opacity:0;transform:scale(.3);animation:fpow .9s cubic-bezier(.2,1.3,.4,1) forwards,fbreathe 7s ease-in-out 1s infinite;animation-delay:var(--d),calc(var(--d) + 1s)}' +
    '@keyframes fpow{to{opacity:.9;transform:scale(1)}}@keyframes fbreathe{0%,100%{transform:scale(1)}50%{transform:scale(1.06) translateY(-2px)}}' +
    '.fx.dot{width:var(--s);height:var(--s);border-radius:50%;background:var(--b);opacity:0;animation:fdot .5s ease-out forwards;animation-delay:var(--d)}@keyframes fdot{from{opacity:0;transform:scale(0)}to{opacity:.8;transform:scale(1)}}' +
    /* songkran */
    '.fx.wave{left:0;bottom:-2px;width:200%;height:16px;display:flex;animation:fwave 9s linear infinite}.fx.wave svg{width:50%;height:100%}' +
    '.fx.wave.w2{height:12px;animation-duration:14s;animation-direction:reverse}@keyframes fwave{to{transform:translateX(-50%)}}' +
    /* vesak */
    '.fx.lattice{inset:0;opacity:.13;background:linear-gradient(45deg,transparent 46%,#b8860b 47% 53%,transparent 54%) 0 0/16px 16px,linear-gradient(-45deg,transparent 46%,#0f5e5a 47% 53%,transparent 54%) 0 0/16px 16px}' +
    '.fx.glow{left:20%;right:20%;top:5%;bottom:5%;border-radius:50%;background:radial-gradient(ellipse,rgba(255,214,120,.55),transparent 70%);animation:fglow 4s ease-in-out infinite}@keyframes fglow{0%,100%{opacity:.55}50%{opacity:1}}' +
    /* midsummer */
    '.fx.rays{left:50%;bottom:-160px;width:320px;height:320px;margin-left:-160px;border-radius:50%;background:repeating-conic-gradient(rgba(255,255,255,.32) 0 5deg,transparent 5deg 15deg);animation:fspin 80s linear infinite}' +
    '.fx.sun{left:50%;bottom:-40px;width:120px;height:70px;margin-left:-60px;border-radius:50% 50% 0 0;background:radial-gradient(ellipse at 50% 100%,#fffbe6 0,rgba(255,240,180,.7) 45%,transparent 72%)}' +
    '@keyframes fspin{to{transform:rotate(360deg)}}.fx.spin{animation:fspin 60s linear infinite}' +
    '.fx.bird{width:14px;animation:fbird 14s linear infinite;animation-delay:var(--d)}@keyframes fbird{0%{transform:translate(0,0)}50%{transform:translate(26px,-4px)}100%{transform:translate(0,0)}}' +
    /* tanabata */
    '.fx.streamer{left:0;right:0;top:0;bottom:0}.fx.streamer svg{width:100%;height:100%}' +
    '.fx.streamer .st{stroke-dasharray:60 340;animation:fdash 7s linear infinite}.fx.streamer .st2{animation-duration:10s}@keyframes fdash{to{stroke-dashoffset:-400}}' +
    '.fx.shoot{top:12%;left:-10%;width:70px;height:1.5px;background:linear-gradient(90deg,transparent,#fff);opacity:0;transform:rotate(12deg);animation:fshoot 7s ease-in 2s infinite}' +
    '@keyframes fshoot{0%{opacity:0;transform:translate(0,0) rotate(12deg)}3%{opacity:1}14%{opacity:0;transform:translate(420px,90px) rotate(12deg)}100%{opacity:0}}' +
    /* rakhi / diwali */
    '.fx.diya{bottom:-2px;animation:none}.fx.diya .flame{transform-origin:20px 15px;animation:fflick 1.2s ease-in-out infinite;animation-delay:var(--d,0s)}' +
    '.fx.diya::after{content:"";position:absolute;left:50%;top:-6px;width:30px;height:30px;margin-left:-15px;border-radius:50%;background:radial-gradient(circle,rgba(255,200,80,.55),transparent 70%);animation:fglow 1.8s ease-in-out infinite}' +
    '@keyframes fflick{0%,100%{transform:scale(1,1)}30%{transform:scale(.9,1.12)}60%{transform:scale(1.06,.94)}}' +
    '.fx.bokeh{bottom:-12px;width:var(--s);height:var(--s);border-radius:50%;background:rgba(255,206,110,.75);filter:blur(1.5px);animation:fbokeh var(--t) linear infinite;animation-delay:var(--d)}' +
    '@keyframes fbokeh{0%{transform:translateY(0);opacity:0}15%{opacity:.85}100%{transform:translateY(-140px);opacity:0}}' +
    /* mid-autumn */
    '.fx.moon{right:19%;top:50%;width:62px;height:62px;margin-top:-31px;border-radius:50%;background:radial-gradient(circle at 40% 38%,#fffbe8 0,#f7e3a1 55%,#e9c96a 100%);box-shadow:0 0 18px 6px rgba(247,227,161,.7);animation:fmoon 5s ease-in-out infinite}' +
    '@keyframes fmoon{0%,100%{box-shadow:0 0 14px 4px rgba(247,227,161,.55)}50%{box-shadow:0 0 26px 10px rgba(247,227,161,.85)}}' +
    '.fx.mist{left:0;right:0;bottom:-8px;height:22px;background:radial-gradient(ellipse at 20% 100%,#fff 0,transparent 60%),radial-gradient(ellipse at 60% 110%,#fff 0,transparent 55%),radial-gradient(ellipse at 90% 100%,#fffaf0 0,transparent 55%);opacity:.9}' +
    /* thanksgiving */
    '.fx.maple{top:-12px;width:11px;animation:fleaf var(--t) linear infinite;animation-delay:var(--d)}' +
    '@keyframes fleaf{0%{transform:translate(0,0) rotate(0);opacity:0}10%{opacity:.9}100%{transform:translate(var(--x),130px) rotate(260deg);opacity:0}}' +
    '@media (min-width:900px){.fest-fx .fx.corner,.fest-fx .fx.hang,.fest-fx .fx.fw,.fest-fx .fx.diya,.fest-fx .fx.bird{scale:1.7}.fest-fx .fx.moon{scale:1.5}.fest-cap{scale:1.35}}' +
    /* santa cap */
    'html[data-festival="christmas"] .wordmark{position:relative}' +
    '.fest-cap{position:absolute;left:-9px;top:-13px;width:26px;height:22px;transform:rotate(-22deg);pointer-events:none;transform-origin:60% 100%;animation:fcap .9s cubic-bezier(.2,1.6,.4,1) .3s both}' +
    '@keyframes fcap{0%{opacity:0;transform:translateY(-14px) rotate(-40deg)}100%{opacity:1;transform:rotate(-22deg)}}' +
    /* greeting ribbon */
    '.fest-ribbon{flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:6px 10px 6px 14px;border-bottom:1px solid var(--rule);background:var(--paper-2);background:color-mix(in srgb,var(--fest) 8%,var(--paper))}' +
    '.fest-ribbon svg{flex:0 0 auto;width:26px;height:26px;transform-origin:50% 90%;animation:fsway 4s ease-in-out infinite}' +
    '.fest-ribbon .fr-txt{flex:1 1 auto;min-width:0;line-height:1.2}' +
    '.fest-ribbon .fr-g{display:block;font-family:Newsreader,Georgia,serif;font-style:italic;font-weight:600;font-size:15px;color:var(--fest)}' +
    'html[data-theme="night"] .fest-ribbon .fr-g{color:var(--fest-2)}' +
    '.fest-ribbon .fr-s{display:block;font-family:"Libre Franklin",sans-serif;font-size:10.5px;color:var(--meta)}' +
    '.fest-ribbon .fr-x{flex:0 0 auto;appearance:none;border:0;background:none;color:var(--meta);font-size:18px;line-height:1;width:32px;height:32px;cursor:pointer;border-radius:50%}' +
    '.fest-ribbon .fr-x:hover{color:var(--ink);background:var(--paper)}.fest-ribbon .fr-x:focus-visible{outline:2px solid var(--fest);outline-offset:1px}' +
    '@media (prefers-reduced-motion:reduce){.fest-fx .fx,.fest-fx .fx *,.fest-cap,.fest-ribbon svg{animation:none!important}' +
      '.fx.fw{opacity:.85;transform:translate(-50%,-50%) scale(1)}.fx.powder{opacity:.9;transform:none}.fx.dot{opacity:.8}.fx.star{opacity:.7}}';

  var CAP = '<svg viewBox="0 0 26 22" aria-hidden="true"><path d="M3 17C5 8 11 2 19 3c2 .3 3 2 4 5l-3-1c-4 0-8 4-10 10z" fill="#d62828"/>' +
    '<path d="M19 3c2 .3 3 2 4 5" stroke="#a4161a" stroke-width="1" fill="none"/>' +
    '<rect x="1" y="15.5" width="15" height="5" rx="2.5" fill="#fff" stroke="#ddd" stroke-width=".6"/>' +
    '<circle cx="23" cy="9" r="2.6" fill="#fff" stroke="#ddd" stroke-width=".6"/></svg>';

  function isGrad(v) { return /gradient\(/.test(v || ''); }

  function plate(f) {
    var t = THEMES[f.id], root = document.documentElement;
    var np = document.querySelector('.nameplate'); if (!t || !np) return;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    root.style.setProperty('--np-bg', t.bg);
    root.style.setProperty('--np-meta', t.meta);
    if (t.shadow) root.style.setProperty('--np-shadow', t.shadow);
    if (t.frame) root.style.setProperty('--np-frame', t.frame);
    if (t.dark) root.setAttribute('data-fest-dark', '');
    // Wordmark: split "AI" / "Shorts" so each half can take its own colour.
    var wm = document.querySelector('.wordmark');
    if (wm && wm.textContent.trim() === 'AIShorts') wm.innerHTML = '<span class="wm-ai">AI</span><span class="wm-rest">Shorts</span>';
    if (isGrad(t.wm)) { root.setAttribute('data-fest-grad', ''); root.style.setProperty('--np-grad', t.wm); root.style.setProperty('--np-wm', 'transparent'); }
    else root.style.setProperty('--np-wm', t.wm);
    if (t.wmAi && wm) {
      var ai = wm.querySelector('.wm-ai');
      if (ai) {
        ai.setAttribute('data-own', '');
        if (isGrad(t.wmAi)) { root.setAttribute('data-fest-grad-ai', ''); root.style.setProperty('--np-grad-ai', t.wmAi); }
        else ai.style.color = t.wmAi;
      }
    }
    var layer = document.createElement('div');
    layer.className = 'fest-fx'; layer.setAttribute('aria-hidden', 'true');
    layer.innerHTML = decorate(f.id, t, true, reduce);
    np.insertBefore(layer, np.firstChild);
    if (t.deco.indexOf('cap') >= 0 && wm) { var c = document.createElement('span'); c.className = 'fest-cap'; c.setAttribute('aria-hidden', 'true'); c.innerHTML = CAP; wm.appendChild(c); }
  }

  function apply(hit) {
    var f = hit.f, root = document.documentElement;
    root.setAttribute('data-festival', f.id);
    root.style.setProperty('--fest', f.c1);
    root.style.setProperty('--fest-2', f.c2);
    var st = document.createElement('style');
    st.id = 'festive-css';
    st.textContent = CSS;
    document.head.appendChild(st);
    try { plate(f); } catch (e) { /* decoration only — never break the page */ }

    var key = 'aishorts_fest_hide_' + f.id + '_' + hit.year, hidden = false;
    try { hidden = localStorage.getItem(key) === '1'; } catch (e) {}
    var mast = document.querySelector('header.masthead');
    if (hidden || !mast) return;

    var greet = f.greet(hit.year);
    var rib = document.createElement('div');
    rib.className = 'fest-ribbon';
    rib.setAttribute('role', 'note');
    rib.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none">' + MOTIF[f.id] + '</svg>' +
      '<div class="fr-txt"><span class="fr-g"></span><span class="fr-s"></span></div>' +
      '<button class="fr-x" type="button" aria-label="Hide greeting">&times;</button>';
    rib.querySelector('.fr-g').textContent = greet;
    rib.querySelector('.fr-s').textContent = f.sub;
    rib.querySelector('.fr-x').addEventListener('click', function () {
      rib.parentNode && rib.parentNode.removeChild(rib);
      try { localStorage.setItem(key, '1'); } catch (e) {}
    });
    mast.parentNode.insertBefore(rib, mast.nextSibling);
  }

  // Exposed for testing only.
  window.AISHORTS_FESTIVE = { active: active, dateFor: dateFor, list: FESTIVALS };

  var hit = pick();
  if (!hit) return;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(hit); });
  else apply(hit);
})();
