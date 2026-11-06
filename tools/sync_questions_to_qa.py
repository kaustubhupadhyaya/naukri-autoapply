"""Sync unique questions from chatbot_questions.csv into qa_dictionary.json.

Creates backups of the original CSV and QA JSON. Prefills known answers from config.json
for keys like notice_period, current_ctc, expected_ctc where available.
"""
import csv
import json
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CSV_PATH = os.path.join(ROOT, 'chatbot_questions.csv')
QA_PATH = os.path.join(ROOT, 'qa_dictionary.json')
CONFIG_PATH = os.path.join(ROOT, 'config.json')

def load_questions(csv_path):
    seen = []
    if not os.path.exists(csv_path):
        return seen
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        for row in reader:
            if len(row) >= 2:
                q = ' '.join(row[1].strip().split())
                seen.append(q)
    return seen

def load_qa(qa_path):
    if not os.path.exists(qa_path):
        return {}
    with open(qa_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_qa(qa, qa_path):
    tmp = qa_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(qa, f, ensure_ascii=False, indent=2)
    os.replace(tmp, qa_path)

def main():
    questions = load_questions(CSV_PATH)
    unique = []
    lower_seen = set()
    for q in questions:
        k = q.strip().lower()
        if k not in lower_seen:
            lower_seen.add(k)
            unique.append(q)

    # Backup CSV
    if os.path.exists(CSV_PATH):
        backup = CSV_PATH + '.' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.bak'
        os.replace(CSV_PATH, backup)
        print('Backed up CSV to', backup)

    # Load existing QA
    qa = load_qa(QA_PATH)

    # Prefill answers from config if present
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            config = {}

    def prefill_answer(q):
        lk = q.strip().lower()
        if 'notice period' in lk:
            return config.get('personal_info', {}).get('notice_period', '')
        if 'current ctc' in lk or 'current salary' in lk:
            return config.get('personal_info', {}).get('current_ctc', '')
        if 'expected ctc' in lk or 'expected salary' in lk:
            return config.get('personal_info', {}).get('expected_ctc', '')
        return ''

    # Seed QA entries
    changed = False
    for q in unique:
        if q not in qa:
            qa[q] = prefill_answer(q)
            changed = True

    if changed:
        # Backup QA if exists
        if os.path.exists(QA_PATH):
            backup = QA_PATH + '.' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.bak'
            os.replace(QA_PATH, backup)
            print('Backed up QA to', backup)
        save_qa(qa, QA_PATH)
        print('Wrote QA with', len(qa), 'entries to', QA_PATH)
    else:
        print('No new questions to add.')

if __name__ == '__main__':
    main()
