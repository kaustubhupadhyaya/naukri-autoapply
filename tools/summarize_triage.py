"""Summarize debug_submit_triage.csv and print frequency counts and recommendations."""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
CSV_PATH = os.path.join(ROOT, 'tools', 'debug_submit_triage.csv')

def main():
    stats = {
        'total': 0,
        'has_submit_candidates': 0,
        'overlays': 0,
        'iframes': 0,
        'contenteditable': 0,
        'skeleton_loader': 0,
    }
    rows = []
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            stats['total'] += 1
            if r.get('has_submit_candidates', '').lower() == 'true':
                stats['has_submit_candidates'] += 1
            if r.get('overlays'):
                stats['overlays'] += 1
            if r.get('iframes'):
                stats['iframes'] += 1
            if r.get('contenteditable'):
                stats['contenteditable'] += 1
            if r.get('skeleton_loader', '').lower() == 'true':
                stats['skeleton_loader'] += 1
            rows.append(r)

    print('Triage summary:')
    print(f"Total snapshots: {stats['total']}")
    print(f"Has submit-like candidates: {stats['has_submit_candidates']} ({stats['has_submit_candidates']/stats['total']:.0%})")
    print(f"Overlays present: {stats['overlays']} ({stats['overlays']/stats['total']:.0%})")
    print(f"Iframes present: {stats['iframes']} ({stats['iframes']/stats['total']:.0%})")
    print(f"Contenteditable fields: {stats['contenteditable']} ({stats['contenteditable']/stats['total']:.0%})")
    print(f"Skeleton/loaders: {stats['skeleton_loader']} ({stats['skeleton_loader']/stats['total']:.0%})")

    # Top examples per issue
    def find_examples(field):
        ex = [r['file'] for r in rows if r.get(field)]
        return ex[:3]

    print('\nSample files with overlays:')
    for f in find_examples('overlays'):
        print(' -', f)
    print('\nSample files with iframes:')
    for f in find_examples('iframes'):
        print(' -', f)
    print('\nSample files with contenteditable:')
    for f in find_examples('contenteditable'):
        print(' -', f)

if __name__ == '__main__':
    main()
