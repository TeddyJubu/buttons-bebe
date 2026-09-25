"""Embed the shared theme in pages so existing single-file console CD works."""
from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parents[1]
css=(ROOT/'console-src/support-theme.css').read_text()
block='<style id="support-theme">\n'+css+'</style>'
font='<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">'
for path in ['console-src/index.html','console-src/login.html']:
 p=ROOT/path; s=p.read_text()
 s=re.sub(r'<style id="support-theme">.*?</style>\n?', '', s, flags=re.S)
 # Branded pages embed their own Jost fonts and must keep the brand layer last.
 brand_marker='<style id="buttonsbebe-brand">'
 if brand_marker not in s and 'fonts.googleapis.com/css2?family=IBM+Plex' not in s:
  s=s.replace('</head>',font+'\n</head>')
 anchor=brand_marker if brand_marker in s else '</head>'
 updated=s.replace(anchor,block+'\n'+anchor)
 if '--check' in sys.argv:
  if updated!=p.read_text(): raise SystemExit(f'{path}: run python3 tools/build_support_theme.py')
 else: p.write_text(updated)
