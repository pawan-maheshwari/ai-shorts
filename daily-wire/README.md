# The Daily Wire — Pinocchio & Gandalf

Side panel on aishorts.biz (left-edge tab, Menu → Today, or `aishorts.biz/#daily-wire`).
Same two characters every day. Only the conversation changes.

## Characters (lock these)

Reference art: `cast/character-sheet.jpg` — attach it to every image-generation request.
Cut-outs used in the panel's "Meet the cast": `cast/pinocchio.png`, `cast/gandalf.png`.

| Character | Visual lock | Role & voice |
|---|---|---|
| **Pinocchio** | Wooden puppet boy, messy short brown hair, round dark eyes, cream short-sleeved button shirt, brown belted trousers rolled at the calf, brown shoes, visible wooden joints at elbows and knees. Long-nose version for panel 2. | The AI. Eager, confident, slightly smug. Always has an answer. Nose grows when he lies or exaggerates. |
| **Gandalf** | Old wizard, tall grey pointed hat with a soft bent tip, plain grey robe with a sash, very long white beard, thick white eyebrows, gnarled brown wooden staff. Expressions: neutral, thoughtful, worried, stern (stern for the punchline). | The observer. Dry, precise, delivers the short punchline. |

## Image prompt (change only the dialogue)

> 3-panel comic strip, clean hand-drawn cartoon style with soft colour, white background. Use the two characters exactly as in the attached character sheet — same faces, clothes and colours. Panel 1: the wooden puppet boy (normal nose) says "[dialogue 1]". Panel 2: the same puppet boy, nose now long and growing, says "[dialogue 2]". Panel 3: the grey wizard with his staff, stern expression, says "[dialogue 3]". Clear speech bubbles, readable text, consistent characters, no other people.

## Publishing a strip (daily)

1. Pick one headline from the AIShorts wire.
2. Adapt a script below (or write a new one in the same 3-panel pattern: claim → bigger claim → check).
3. Generate the strip image and save it here as `daily-wire/YYYY-MM-DD.png` (or `.jpg` / `.webp`).
4. Add an entry at the top of `strips` in `/daily-wire.json`:

```json
{
  "date": "2026-11-06",
  "title": "Short title for the archive",
  "headline": "Original headline, as on the wire",
  "source": "Publication name",
  "url": "https://link-to-the-original-story",
  "image": "daily-wire/2026-11-06.png",
  "alt": "",
  "panels": [
    { "speaker": "Pinocchio", "line": "Dialogue 1" },
    { "speaker": "Pinocchio", "line": "Dialogue 2 (nose grows)" },
    { "speaker": "Gandalf",   "line": "Dialogue 3 (punchline)" }
  ]
}
```

- `image` can be left `""` — the panel then shows a text edition of the three lines.
- `alt` can be left `""` — it is built from the three lines automatically.
- The panel always links to `url`, so every strip points back to its source.
- The newest date is shown as "Today's strip"; older ones go to the archive (last 30 listed).

## Ready scripts

1. **Benchmark** — This new AI model scores 100% on every benchmark! / I also beat every human expert and never hallucinate. / You shall not pass that claim without a source.
2. **Jobs** — This AI will replace all jobs next year! / Including mine. I already quit. / You shall not pass that prediction.
3. **Valuation** — This AI company is worth a trillion dollars! / And I invented their product last Tuesday. / You shall not pass that valuation without revenue.
4. **Perfect accuracy** — This AI never makes mistakes. / Except when it does. But those don't count. / You shall not pass that logic.
5. **Security agent** — This AI agent found 948 bugs and fixed them all. / And then it wrote the entire Linux kernel from scratch. / You shall not pass that claim.
6. **AGI claim** — We have achieved AGI. / It can do everything. I am AGI. / You shall not pass that title without proof.
7. **Speed claim** — This AI is 100x faster than last month. / And it costs nothing. Trust me. / You shall not pass that number without a benchmark.

## Automatic daily strip

`.github/workflows/daily-wire.yml` runs every day at 02:30 UTC (8:00 AM IST), after the morning headlines. It runs `tools/daily-wire.mjs`, which:

1. reads the AI wire feed and picks a fresh headline that fits a claim theme (benchmark, jobs, valuation, accuracy, security, AGI, speed/cost, rules, launch);
2. writes the three lines — from the scripts above, or, if the repo has an `ANTHROPIC_API_KEY` secret, fresh headline-specific lines drafted by Claude (validated, with a script fallback);
3. adds the strip to `daily-wire.json` (auto strips carry `"auto": true`; the last 120 are kept).

A strip you add by hand for a date is never overwritten. To regenerate today's auto strip, run the workflow manually (Actions → The Daily Wire → Run workflow) with **force** ticked.
