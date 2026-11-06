"""Parse debug_submit_fail_*.html snapshots and produce a CSV summary.

Usage: python tools/triage_debug_submits.py
Writes: tools/debug_submit_triage.csv
"""
import csv
import os
import re
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(__file__))
OUT_CSV = os.path.join(ROOT, 'tools', 'debug_submit_triage.csv')

def find_submit_candidates(soup):
    candidates = []
    # standard submit buttons
    for b in soup.select("button[type='submit'], button, input[type='submit']"):
        text = (b.get_text() or b.get('value') or '').strip()
        candidates.append(('button', text, str(b)[:200]))
    # clickable div/span with role=button
    for el in soup.select("[role='button'], .submit-btn, .apply-btn, [data-role='submit']"):
        text = (el.get_text() or el.get('value') or '').strip()
        candidates.append(('clickable', text, str(el)[:200]))
    # elements that look like disabled via class
    for el in soup.find_all(class_=re.compile(r"disable|disabled")):
        text = (el.get_text() or el.get('value') or '').strip()
        candidates.append(('disabled_candidate', text, str(el)[:200]))
    return candidates

def detect_overlays(soup):
    overlays = []
    for sel in ['.naukri-drawer', '.drawer-overlay', '.overlay', '.modal-backdrop', '.cdk-overlay-pane']:
        if soup.select_one(sel):
            overlays.append(sel)
    return overlays

def detect_iframes(soup):
    iframes = []
    for i, iframe in enumerate(soup.find_all('iframe')):
        src = iframe.get('src') or ''
        iframes.append(src or f'inline-{i}')
    return iframes

def detect_contenteditable(soup):
    elems = []
    for el in soup.find_all(attrs={"contenteditable": True}):
        elems.append((el.name, (el.get_text() or '')[:100]))
    return elems

def detect_skeletons(soup):
    # heuristic: presence of 'skeleton' class or loader ids
    if soup.select_one('.skeleton') or soup.select_one('.loader') or soup.find(text=re.compile(r"loading|skeleton", re.I)):
        return True
    return False

def summarize_file(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        txt = f.read()
    soup = BeautifulSoup(txt, 'html.parser')
    candidates = find_submit_candidates(soup)
    overlays = detect_overlays(soup)
    iframes = detect_iframes(soup)
    contenteditable = detect_contenteditable(soup)
    skeleton = detect_skeletons(soup)
    return {
        'file': os.path.basename(path),
        'has_submit_candidates': bool(candidates),
        'num_candidates': len(candidates),
        'top_candidates': ' | '.join([f"{t}:{text}" for t, text, _ in candidates[:3]]),
        'overlays': ';'.join(overlays),
        'iframes': ';'.join(iframes),
        'contenteditable': ';'.join([f"{n}:{t}" for n, t in contenteditable][:3]),
        'skeleton_loader': skeleton,
    }

def main():
    import glob
    html_files = sorted(glob.glob(os.path.join(ROOT, 'debug_submit_fail_*.html')))
    rows = []
    for p in html_files:
        print('Parsing', os.path.basename(p))
        rows.append(summarize_file(p))
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ['file'])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print('Wrote', OUT_CSV)

if __name__ == '__main__':
    main()
