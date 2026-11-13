"""Personal assignment inbox, with local seen state and permission-checked links."""
import logging
from PyQt6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMenu, QPushButton
from app.db.dao_conn import get_conn
from app.db.dao_tasks import list_tasks
from app.email.email_storage import _SAME_MESSAGE
from app.security.authorization import allowed_tabs

logger = logging.getLogger(__name__)
POLL_INTERVAL_MS = 15000


def list_user_notifications(actor_id):
    """Only current assignments accessible to this employee; no historic backfill."""
    tabs = set(allowed_tabs(actor_id))
    notifications = []
    if 'E-mail' in tabs:
        conn = get_conn()
        try:
            rows = conn.execute(f'''
                SELECT DISTINCT ON (a.id)
                    a.id AS assignment_id, we.id, we.konto_id, we.uid, we.message_id,
                    we.temat, we.data, fe.server_name
                FROM email_message_assignments a
                JOIN wiadomosci_email w2 ON w2.id = a.message_id
                JOIN wiadomosci_email we ON we.konto_id = w2.konto_id AND {_SAME_MESSAGE}
                JOIN foldery_email fe ON fe.id = we.folder_id
                JOIN konta_email ke ON ke.id = we.konto_id
                JOIN pracownik_konto_email pke ON pke.konto_id = ke.id AND pke.pracownik_id = %s
                WHERE a.employee_id = %s AND COALESCE(we.usunieta, 0) = 0
                  AND COALESCE(ke.aktywne, 1) = 1 AND COALESCE(ke.systemowe, 0) = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM wiadomosci_email done
                      WHERE done.konto_id = we.konto_id AND COALESCE(done.email_done, 0) = 1
                        AND (done.id = we.id OR (NULLIF(we.message_id, '') IS NOT NULL AND done.message_id = we.message_id))
                  )
                ORDER BY a.id DESC, we.id DESC LIMIT 100
            ''', (actor_id, actor_id)).fetchall()
            for row in rows:
                notifications.append({
                    'key': f"email:{row['assignment_id']}", 'kind': 'email',
                    'title': 'Przypisany e-mail: '+str(row['temat'] or '(bez tematu)'),
                    'target': {'kind':'email', 'account_id':row['konto_id'], 'row_id':row['id'],
                               'message_id':row['message_id'], 'folder':row['server_name'], 'uid':row['uid']},
                })
        finally:
            conn.close()
    if 'Zadania' in tabs:
        tasks = list_tasks(pracownik_id=actor_id, viewer_id=actor_id)
        tasks = sorted(tasks, key=lambda task: (str(task.get('updated_at') or ''), task['id']), reverse=True)
        for task in tasks:
            if task.get('status') not in ('Nowe', 'W toku'):
                continue
            notifications.append({
                'key': f"task:{task['id']}:{task.get('updated_at') or task.get('created_at') or ''}",
                'kind':'task', 'title':'Przypisane zadanie: '+str(task['tytul']),
                'target':{'kind':'task', 'task_id':task['id']},
            })
            if sum(entry['kind'] == 'task' for entry in notifications) >= 100:
                break
    if 'Widok miesięczny' in tabs or 'Sprawdzanie' in tabs:
        from app.integrations.saldeo_web.history import notification_batches
        notifications.extend(notification_batches(actor_id))
    return notifications


class _NotificationSignals(QObject):
    finished = pyqtSignal(object, str)


class _NotificationWorker(QRunnable):
    def __init__(self, actor_id):
        super().__init__()
        self.actor_id = actor_id
        self.signals = _NotificationSignals()

    def run(self):
        try:
            self.signals.finished.emit(list_user_notifications(self.actor_id), '')
        except Exception as exc:
            logger.exception('Nie można pobrać powiadomień')
            self.signals.finished.emit([], str(exc))


class NotificationCenter(QPushButton):
    def __init__(self, actor_id, navigate, parent=None):
        super().__init__('Powiadomienia', parent)
        self.actor_id, self.navigate = actor_id, navigate
        self.settings = QSettings('Biuro', 'OfficeManager')
        self.settings_key = f'users/{actor_id}/notifications/seen'
        stored = self.settings.value(self.settings_key, [], type=list)
        self._seen = list(dict.fromkeys(str(key) for key in stored))
        self.entries = []
        self._worker = None
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._menu.aboutToShow.connect(self._menu_opening)
        self._rebuild_menu()
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        if self._worker is not None:
            return
        worker = _NotificationWorker(self.actor_id)
        self._worker = worker
        worker.signals.finished.connect(self._loaded)
        QThreadPool.globalInstance().start(worker)

    def _loaded(self, entries, error):
        self._worker = None
        if error:
            self.setToolTip('Nie można odświeżyć powiadomień. Ponowna próba co 15 sekund.')
            return
        self.entries = entries
        self.setToolTip('Przypisane e-maile i otwarte zadania · odświeżanie co 15 sekund')
        self._rebuild_menu()

    def _menu_opening(self):
        self._rebuild_menu()
        self.refresh()

    def _rebuild_menu(self):
        self._menu.clear()
        seen = set(self._seen)
        unread = sum(entry['key'] not in seen for entry in self.entries)
        self.setText(f'Powiadomienia ({unread})' if unread else 'Powiadomienia')
        entries = sorted(self.entries, key=lambda entry: entry['key'] in seen)
        for entry in entries[:40]:
            text = str(entry['title']).replace('\n', ' ').replace('\r', ' ')
            if len(text) > 110:
                text = text[:107]+'…'
            prefix = '• ' if entry['key'] not in seen else '✓ '
            action = self._menu.addAction((prefix+text).replace('&','&&'))
            action.setToolTip(entry['title'])
            action.triggered.connect(lambda checked=False, item=entry: self._open(item))
        if not entries:
            self._menu.addAction('Brak przypisanych e-maili i otwartych zadań.').setEnabled(False)
        if len(entries) > 40:
            self._menu.addAction(f'Pozostałe: {len(entries)-40} · pełne listy w E-mail i Zadania').setEnabled(False)
        self._menu.addSeparator()
        mark_all = self._menu.addAction('Oznacz wszystkie powiadomienia jako przeczytane')
        mark_all.setEnabled(bool(unread))
        mark_all.triggered.connect(self.mark_all_seen)

    def _store_seen(self, keys):
        self._seen = list(dict.fromkeys(self._seen + list(keys)))[-2000:]
        self.settings.setValue(self.settings_key, self._seen)
        self.settings.sync()
        self._rebuild_menu()

    def _open(self, entry):
        if entry['kind'] == 'saldeo':
            from PyQt6.QtWidgets import QDialog, QVBoxLayout
            from app.admin.settings.saldeo_web_settings import SaldeoHistory
            window = QDialog(self)
            window.setWindowTitle('Historia powiadomień Saldeo')
            window.resize(1100, 650)
            layout = QVBoxLayout(window)
            layout.addWidget(SaldeoHistory(self.actor_id, window))
            window.exec()
            self._store_seen([entry['key']])
            return
        if self.navigate(entry['target']):
            self._store_seen([entry['key']])
            self.refresh()

    def mark_all_seen(self):
        # Dismiss notifications only; this does not mark emails as read or finish tasks.
        self._store_seen([entry['key'] for entry in self.entries])
