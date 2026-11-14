#!/usr/bin/env python3
"""Build docs/CODEMAP.md: a line-numbered index of every source file so Claude
can jump straight to the right lines (Read offset/limit) instead of reading
multi-megabyte HTML files whole.

Usage (from repo root):  python3 tools/claude/codemap.py
Re-run after any structural change (new section, new function, new file).
Stdlib only. Safe to run in CI.
"""
import os, re, subprocess, sys

ROOT = os.getcwd()
OUT = os.path.join(ROOT, "docs", "CODEMAP.md")
SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".venv", "results", "media", "img", "assets-bac"}
BACKUP = re.compile(r"(_bac|bac_|indexbac|index_?\d{4,}|index\d{4}|\d{1,2}sep|_15aug|v1\.0)", re.I)
SRC_EXT = {".html", ".js", ".mjs", ".py", ".css", ".gs", ".sql", ".yml", ".yaml", ".ipynb"}
MAX_ITEMS = 120  # per file, keeps the map small

PATTERNS = {
    ".html": [
        ("section", re.compile(r"<(section|main|nav|header|footer|dialog|template)\b[^>]*\bid=[\"']([\w-]+)")),
        ("div#", re.compile(r"<div\b[^>]*\bid=[\"']((?:page|view|tab|screen|panel|modal|module|step|app)[\w-]*)")),
        ("h2", re.compile(r"<h[12]\b[^>]*>([^<]{3,60})<")),
        ("script", re.compile(r"<script\b(?![^>]*\bsrc=)")),
        ("style", re.compile(r"<style\b")),
        ("fn", re.compile(r"^\s{0,6}(?:async\s+)?function\s+([\w$]+)\s*\(")),
        ("const-fn", re.compile(r"^\s{0,6}(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>")),
        ("data", re.compile(r"^\s{0,6}(?:const|let|var)\s+([A-Z][A-Z0-9_]{2,})\s*=")),
    ],
    ".js": [
        ("fn", re.compile(r"^\s{0,4}(?:export\s+)?(?:async\s+)?function\s+([\w$]+)\s*\(")),
        ("const-fn", re.compile(r"^\s{0,4}(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>")),
        ("data", re.compile(r"^\s{0,4}(?:export\s+)?(?:const|let|var)\s+([A-Z][A-Z0-9_]{2,})\s*=")),
        ("class", re.compile(r"^\s{0,4}(?:export\s+)?class\s+(\w+)")),
    ],
    ".py": [
        ("def", re.compile(r"^(?:async\s+)?def\s+(\w+)")),
        ("class", re.compile(r"^class\s+(\w+)")),
        ("page", re.compile(r"^\s*(?:st\.)?(?:title|header|subheader)\(\s*[\"']([^\"']{3,50})")),
    ],
    ".sql": [("table", re.compile(r"create\s+(?:table|view|function|policy)\s+(?:if not exists\s+)?([\w.\"]+)", re.I))],
    ".gs": [("fn", re.compile(r"^function\s+(\w+)"))],
}
PATTERNS[".mjs"] = PATTERNS[".js"]


def tracked_files():
    try:
        out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
        return [f for f in out.splitlines() if f]
    except Exception:
        res = []
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            res += [os.path.relpath(os.path.join(d, f), ROOT) for f in files]
        return res


def human(n):
    return f"{n/1048576:.1f} MB" if n > 1048576 else f"{n//1024} KB" if n > 1024 else f"{n} B"


def scan(path, ext):
    items, long_lines, n = [], 0, 0
    pats = PATTERNS.get(ext, [])
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if len(line) > 2000:
                long_lines += 1
                head = line[:300]
            else:
                head = line
            for kind, rx in pats:
                m = rx.search(head)
                if m:
                    label = m.group(m.lastindex) if m.lastindex else ""
                    items.append((n, kind, label.strip()))
                    break
    return n, long_lines, items


def main():
    files = [f for f in tracked_files() if os.path.splitext(f)[1].lower() in SRC_EXT
             and not any(p in SKIP_DIRS for p in f.split("/")[:-1])]
    live = [f for f in files if not BACKUP.search(os.path.basename(f))]
    backups = sorted(set(files) - set(live))
    live.sort(key=lambda f: -os.path.getsize(os.path.join(ROOT, f)))

    out = ["# CODEMAP (auto-generated — do not edit by hand)",
           "",
           "Regenerate: `python3 tools/claude/codemap.py`. Line numbers are 1-based; use them with",
           "`Read(file, offset=LINE-5, limit=80)` or `sed -n 'A,Bp' file | cut -c1-400`.",
           "Files with *long lines* contain inline base64/minified blobs — never print those lines raw.",
           "",
           "## File inventory (live files, largest first)",
           "",
           "| File | Size | Lines | Long lines |", "|---|---|---|---|"]
    details = []
    for f in live:
        p = os.path.join(ROOT, f)
        ext = os.path.splitext(f)[1].lower()
        try:
            lines, longl, items = scan(p, ext)
        except Exception:
            continue
        out.append(f"| `{f}` | {human(os.path.getsize(p))} | {lines} | {longl or ''} |")
        if items and ext not in {".yml", ".yaml", ".css", ".ipynb"}:
            fnk = ("fn", "const-fn", "def")
            struct = [i for i in items if i[1] not in fnk]
            fns = [i for i in items if i[1] in fnk]
            body = [f"### `{f}`"]
            if len(struct) > MAX_ITEMS:
                body.append(f"_({len(struct)} structural anchors, first {MAX_ITEMS} shown — grep for the rest)_")
            body += [f"- L{n} {k} `{lbl}`" if lbl else f"- L{n} {k}" for n, k, lbl in struct[:MAX_ITEMS]]
            if fns:
                body.append(f"- functions ({len(fns)}): " + ", ".join(f"{lbl}@{n}" for n, k, lbl in fns))
            details.append("\n".join(body))
    if backups:
        out += ["", "## Backup / snapshot files — IGNORE (never read or edit)", "",
                ", ".join(f"`{b}`" for b in backups)]
    out += ["", "## Anchors per file", ""] + details
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {OUT}: {len(live)} live files, {len(backups)} backups ignored")


if __name__ == "__main__":
    sys.exit(main())
