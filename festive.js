/* AIShorts festive themes — one global festival per month.
 *
 * Each theme shows from 3 days before the festival to 2 days after it
 * (reader's local date), then the page falls back to the normal broadsheet
 * look on its own. Subtle by design: an accent colour on the nameplate rules,
 * a hairline band above the nameplate and a slim greeting ribbon under the
 * masthead that the reader can dismiss.
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
    if (q && byId(q)) return { f: byId(q), year: new Date().getFullYear() };
    return active(ymd(new Date()));
  }

  var CSS =
    'html[data-festival] .stockbar{border-bottom-color:var(--fest)}' +
    'html[data-festival] .nameplate{border-bottom-color:var(--fest)}' +
    'html[data-festival] .nameplate::before{content:"";position:absolute;left:0;right:0;top:0;height:3px;' +
      'background:linear-gradient(90deg,var(--fest) 0 40%,var(--fest-2) 40% 60%,var(--fest) 60%)}' +
    'html[data-festival] .chip.active{border-bottom-color:var(--fest)}' +
    '.fest-ribbon{flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:6px 10px 6px 14px;' +
      'border-bottom:1px solid var(--rule);background:var(--paper-2);' +
      'background:color-mix(in srgb,var(--fest) 8%,var(--paper))}' +
    '.fest-ribbon svg{flex:0 0 auto;width:26px;height:26px;transform-origin:50% 90%;animation:festSway 4s ease-in-out infinite}' +
    '.fest-ribbon .fr-txt{flex:1 1 auto;min-width:0;line-height:1.2}' +
    '.fest-ribbon .fr-g{display:block;font-family:Newsreader,Georgia,serif;font-style:italic;font-weight:600;font-size:15px;color:var(--fest)}' +
    'html[data-theme="night"] .fest-ribbon .fr-g{color:var(--fest-2)}' +
    '.fest-ribbon .fr-s{display:block;font-family:"Libre Franklin",sans-serif;font-size:10.5px;color:var(--meta)}' +
    '.fest-ribbon .fr-x{flex:0 0 auto;appearance:none;border:0;background:none;color:var(--meta);font-size:18px;line-height:1;' +
      'width:32px;height:32px;cursor:pointer;border-radius:50%}' +
    '.fest-ribbon .fr-x:hover{color:var(--ink);background:var(--paper)}' +
    '.fest-ribbon .fr-x:focus-visible{outline:2px solid var(--fest);outline-offset:1px}' +
    '@keyframes festSway{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(4deg)}}' +
    '@media (prefers-reduced-motion:reduce){.fest-ribbon svg{animation:none}}';

  function apply(hit) {
    var f = hit.f, root = document.documentElement;
    root.setAttribute('data-festival', f.id);
    root.style.setProperty('--fest', f.c1);
    root.style.setProperty('--fest-2', f.c2);
    var st = document.createElement('style');
    st.id = 'festive-css';
    st.textContent = CSS;
    document.head.appendChild(st);

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
