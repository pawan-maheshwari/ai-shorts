---
description: Build a feature with the lean, low-token workflow for this repo
argument-hint: <what to build>
---
Build this feature: $ARGUMENTS

Work lean — every file read costs tokens:
1. Locate, don't read. Open `docs/CODEMAP.md` only at the section for the file you need
   (`grep -n` it first), then use `tools/claude/peek.sh` or `Read` with offset/limit
   (≤150 lines at a time). Never read a file over 100 KB whole. Never open backup files.
2. Plan in ≤5 bullets: files to touch, anchors (line numbers), what changes. No essays.
   If the request is ambiguous in a way that changes the build, ask one question first.
3. Prefer a new small module/file (or a self-contained `<section>` + `<script>` block)
   over threading changes through existing giant blocks. Reuse existing CSS variables,
   components and helpers you found in step 1 instead of inventing new ones.
4. Edit with small, unique `Edit` anchors. Never rewrite or re-`Write` a large file.
5. Verify cheaply: `node --check` for JS files, a quick parse/grep for the new ids,
   and for HTML pages run `python3 tools/claude/check_html.py <file>` if present.
6. If you added/renamed sections, functions or files: `python3 tools/claude/codemap.py`.
7. Commit on a branch with a one-line message. Report back in ≤5 lines: what changed,
   where (file:line), how to see it.
