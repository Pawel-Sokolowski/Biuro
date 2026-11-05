"""Compare unchanged mail-list rendering with a Git baseline, excluding SQL/paint.

QT_QPA_PLATFORM=offscreen python -m scripts.measure_mail_model [base-ref]
The actual page rendering method receives a fixed database result in both cases.
"""
import ast
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import MethodType, SimpleNamespace

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QPushButton, QTreeWidget


def main():
    app = QApplication.instance() or QApplication(['measure-mail-model'])
    from app.email import email_ui
    from app.email.mail_model import MailTreeView
    root = Path(__file__).resolve().parents[1]
    base = sys.argv[1] if len(sys.argv) > 1 else 'origin/main'
    source = subprocess.check_output(['git', 'show', f'{base}:app/email/email_ui.py'], cwd=root, text=True)
    tree = ast.parse(source)
    page_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'EmailPage')
    render = next(node for node in page_class.body if isinstance(node, ast.FunctionDef) and node.name == 'load_mail_list_from_db')
    namespace = dict(email_ui.__dict__)
    exec(compile(ast.Module(body=[render], type_ignores=[]), '<baseline-render>', 'exec'), namespace)
    baseline_render = namespace['load_mail_list_from_db']

    def harness(view, method):
        view.setColumnCount(3)
        page = SimpleNamespace(mail_list=view, unread_map={}, assign_map={}, completed_map=set(),
                               current_account={'id': 1}, employees=[], label_numbers={}, employee_filter=None,
                               completed_filter=False, _mail_rows=[], _mail_cursor=None, _mail_context=None,
                               _current_message_row_id=None, current_uid=None, older_messages=QPushButton(),
                               _mail_request=lambda _: (1, 'INBOX'), _clear_view=lambda: None,
                               _refresh_chips=lambda: None)
        page._style_mail_item = MethodType(email_ui.EmailPage._style_mail_item, page)
        page.render = MethodType(method, page)
        return page

    results = []
    for count in (500, 5000):
        rows = [{'id': i+1, 'uid': str(i+1), 'server_name': 'INBOX', 'temat': f'Subject {i}',
                 'nadawca': 'sender@example.com', 'data': '2026-11-04 09:00', 'przeczytana': 1,
                 'message_id': f'<{i}@example.com>'} for i in range(count)]
        payload = (rows, False, None, {'INBOX': 'inbox'}, {}, set())
        classic = harness(QTreeWidget(), baseline_render)
        model = harness(MailTreeView(email_ui.ROLE_ROW_ID), email_ui.EmailPage.load_mail_list_from_db)
        for page in (classic, model):
            page.render('INBOX', keep_view=True, _payload=payload)
        retained = dict(model.mail_list.model().items)
        reset, inserted, removed = (QSignalSpy(model.mail_list.model().modelReset),
                                    QSignalSpy(model.mail_list.model().rowsInserted), QSignalSpy(model.mail_list.model().rowsRemoved))
        def measure(page):
            samples = []
            for _ in range(10):
                # A real query returns new dictionaries, even for unchanged rows.
                snapshot = ([dict(row) for row in rows], *payload[1:])
                started = time.perf_counter()
                page.render('INBOX', keep_view=True, _payload=snapshot)
                samples.append((time.perf_counter()-started)*1000)
            return round(statistics.median(samples), 3)
        results.append({'rows': count, 'baseline_median_ms': measure(classic), 'current_median_ms': measure(model),
                        'retained_nodes': sum(model.mail_list.model().items[key] is node for key, node in retained.items()),
                        'resets': len(reset), 'insertions': len(inserted), 'removals': len(removed)})
        for page in (classic, model):
            page.mail_list.deleteLater()
            page.older_messages.deleteLater()
        app.processEvents()
    print(json.dumps({'base': subprocess.check_output(['git', 'rev-parse', base], cwd=root, text=True).strip(),
                      'samples': 10, 'scope': 'unchanged page render from fresh row dictionaries; excludes SQL and painting',
                      'results': results}, indent=2))


if __name__ == '__main__':
    main()
