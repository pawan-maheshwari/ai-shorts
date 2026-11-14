#!/usr/bin/env python3
"""Cheap sanity check for single-file HTML pages, without reading them into context.

  python3 tools/claude/check_html.py index.html [more.html ...]

Checks: inline <script> blocks parse (node --check, if node is installed),
<script>/<style> open/close counts match, duplicate element ids.
Prints only problems plus a one-line summary per file. Exit 1 on any error.
"""
import os, re, shutil, subprocess, sys, tempfile
from collections import Counter

SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)


def check(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    errs, warns = [], []
    src = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    for tag in ("script", "style"):
        pos, opened = 0, 0
        while True:
            o = re.compile(rf"<{tag}\b", re.I).search(src, pos)
            if not o:
                break
            opened += 1
            c = re.compile(rf"</{tag}\s*>", re.I).search(src, o.end())
            if not c:
                errs.append(f"<{tag}> at L{src.count(chr(10), 0, o.start()) + 1} is never closed")
                break
            pos = c.end()
    ids = Counter(re.findall(r"\bid=[\"']([^\"']+)[\"']", src))
    dups = [k for k, v in ids.items() if v > 1 and "${" not in k]
    if dups:
        warns.append(f"duplicate ids ({len(dups)}): " + ", ".join(dups[:15]))
    node = shutil.which("node")
    n_js = 0
    if node:
        for m in SCRIPT.finditer(src):
            attrs, body = m.group(1), m.group(2)
            if "src=" in attrs or re.search(r"type=[\"'](?!text/javascript|module)", attrs) or not body.strip():
                continue
            n_js += 1
            line = src.count("\n", 0, m.start(2)) + 1
            ext = ".mjs" if "module" in attrs else ".js"
            with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False) as t:
                t.write(body)
            r = subprocess.run([node, "--check", t.name], capture_output=True, text=True)
            os.unlink(t.name)
            if r.returncode:
                msg = next((l for l in r.stderr.splitlines() if "Error" in l), r.stderr.strip()[:200])
                where = re.search(r":(\d+)", r.stderr)
                at = line + int(where.group(1)) - 1 if where else line
                errs.append(f"JS syntax error in <script> starting L{line} (≈L{at}): {msg[:200]}")
    for w in warns:
        print(f"  warn  {path}: {w}")
    for e in errs:
        print(f"  ERROR {path}: {e}")
    print(f"{'FAIL' if errs else 'ok  '} {path}: {n_js} inline scripts checked{'' if node else ' (node not found, JS skipped)'}")
    return not errs


if __name__ == "__main__":
    files = sys.argv[1:] or ["index.html"]
    sys.exit(0 if all([check(f) for f in files]) else 1)
