---
description: Fix a bug with minimal reading and a minimal diff
argument-hint: <symptom, page, and any error text>
---
Fix: $ARGUMENTS

1. Find the code from the symptom: grep for the visible text, element id, or error
   message with `tools/claude/peek.sh FILE -g 'pattern'` (clipped output). Use
   `docs/CODEMAP.md` only to find which file/line block to look at.
2. Read only the surrounding block (`peek.sh FILE START END`, ≤120 lines).
3. State the root cause in one sentence, then make the smallest `Edit` that fixes it.
   No refactors, no reformatting, no "while I'm here" changes.
4. Verify: `python3 tools/claude/check_html.py FILE` (HTML) or `node --check FILE` (JS)
   or `python3 -m py_compile FILE` (Python).
5. Commit with message `fix: <one line>` and reply in ≤3 lines.
