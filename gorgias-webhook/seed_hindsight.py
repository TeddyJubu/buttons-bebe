#!/usr/bin/env python3
"""Seed Hindsight with existing KB content (policies, intents, FAQ).

Run once after deploying Hindsight. This populates the 'world' memory
with the static KB knowledge so recall can return it immediately.
"""
import os
import sys
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from hindsight_integration import retain_kb_content, is_available

KB_ROOT = os.path.join(SCRIPT_DIR, "kb")


def extract_text(filepath):
    """Extract body text from a KB markdown file (skip front-matter)."""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    # Skip YAML front-matter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]
    # Skip DRAFT banners
    lines = text.split("\n")
    body_lines = [l for l in lines if not l.startswith("> ⚠️")]
    return "\n".join(body_lines).strip()


def main():
    if not is_available():
        print("Hindsight is not available. Start it first: docker start hindsight")
        sys.exit(1)

    categories = ["policies", "intents", "faq", "tickets"]
    total = 0
    errors = 0

    for cat in categories:
        pattern = os.path.join(KB_ROOT, cat, "*.md")
        files = sorted(glob.glob(pattern))
        # Skip README.md
        files = [f for f in files if os.path.basename(f) != "README.md"]
        print(f"\n{cat}/: {len(files)} files")

        for fpath in files:
            fname = os.path.basename(fpath)
            title = fname.replace(".md", "").replace("-", " ").title()
            text = extract_text(fpath)
            if not text or len(text) < 20:
                continue

            ok = retain_kb_content(title=title, text=text, category=cat, tags=cat)
            if ok:
                total += 1
                print(f"  OK: {fname[:50]}")
            else:
                errors += 1
                print(f"  FAIL: {fname[:50]}")

    print(f"\nDone: {total} KB entries seeded, {errors} errors")


if __name__ == "__main__":
    main()