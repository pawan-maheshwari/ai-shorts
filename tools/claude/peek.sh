#!/usr/bin/env bash
# Token-safe viewer for huge single-file HTML/JS. Every output line is clipped,
# so inline base64 images / minified blobs never flood the context window.
#
#   tools/claude/peek.sh FILE START [END]      print lines START..END (default START+60)
#   tools/claude/peek.sh FILE -g REGEX [CTX]   grep -n with CTX lines of context (default 0)
#   tools/claude/peek.sh FILE -f NAME          jump to function/const NAME and show 60 lines
#   tools/claude/peek.sh FILE -id ID           jump to element id="ID" and show 60 lines
# Env: WIDTH (default 240) = max chars per printed line.
set -euo pipefail
f="${1:?file}"; shift
W="${WIDTH:-240}"
clip() { awk -v w="$W" '{ if (length($0) > w) print substr($0,1,w) " …[+" length($0)-w " chars]"; else print }'; }
show() { local s=$1 e=$2; awk -v s="$s" -v e="$e" 'NR>=s && NR<=e { printf "%6d  %s\n", NR, $0 } NR>e{exit}' "$f" | clip; }
case "${1:-}" in
  -g)  grep -nE ${3:+-C "$3"} -- "$2" "$f" | clip | head -n 200 ;;
  -f)  n=$(grep -nE "(function[[:space:]]+$2[[:space:]]*\(|(const|let|var)[[:space:]]+$2[[:space:]]*=|def[[:space:]]+$2\b|class[[:space:]]+$2\b)" "$f" | head -1 | cut -d: -f1)
       [ -n "$n" ] || { echo "not found: $2" >&2; exit 1; }; show "$n" $((n+60)) ;;
  -id) n=$(grep -nE "id=[\"']$2[\"']" "$f" | head -1 | cut -d: -f1)
       [ -n "$n" ] || { echo "not found: id=$2" >&2; exit 1; }; show "$n" $((n+60)) ;;
  *)   s="${1:?start}"; e="${2:-$((s+60))}"; show "$s" "$e" ;;
esac
