"""Explicit, paced BCC campaigns. An uncertain SMTP result is never retried."""
from dataclasses import dataclass
from email.utils import getaddresses
import re

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal

from app.db.dao_companies import list_mailing_companies
from app.email.email_actions import send_email_smtp
from app.email.email_storage import get_email_accounts_for_employee
from app.security.authorization import allowed_tabs
from app.utils.background import CommandRunner


def client_addresses(value):
    raw = str(value or '').strip()
    if not raw or '\r' in raw or '\n' in raw:
        raise ValueError('Brak poprawnego kontaktowego adresu e-mail firmy.')
    addresses = []
    for _name, address in getaddresses([raw.replace(';', ',')]):
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', address) or len(address) > 254:
            raise ValueError('Niepoprawny kontaktowy adres e-mail firmy.')
        if address.casefold() not in {item.casefold() for item in addresses}:
            addresses.append(address)
    if not addresses:
        raise ValueError('Brak kontaktowego adresu e-mail firmy.')
    return tuple(addresses)


def filter_companies(rows, *, query='', activity=None, settlement=None, tax=None, active_only=True):
    needle = query.strip().casefold()
    def match(row):
        if active_only and str(row.get('status') or 'Aktywne').strip().casefold() not in ('aktywne', 'aktywna', 'aktywny'):
            return False
        for field, expected in (('forma_dzialalnosci', activity), ('typ_rozliczenia', settlement), ('forma_opodatkowania', tax)):
            if expected is not None and str(row.get(field) or '').strip() != expected:
                return False
        return needle in ' '.join(str(row.get(field) or '') for field in ('nazwa', 'nazwa_skrocona', 'nip', 'adres_email')).casefold()
    return [row for row in rows if match(row)]


from dataclasses import dataclass, field

@dataclass(frozen=True)
class BulkMailPlan:
    actor_id: int
    account_id: int
    sender: str
    subject: str
    body: str
    companies: tuple
    batch_size: int = 25
    pause_seconds: int = 60
    attachments: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not self.subject.strip() or '\r' in self.subject or '\n' in self.subject or not self.body.strip():
            raise ValueError('Uzupełnij temat i treść wiadomości.')

        if not 1 <= self.batch_size <= 50 or not 10 <= self.pause_seconds <= 3600:
            raise ValueError('Partia: 1–50 adresów; przerwa: 10–3600 sekund.')

        if len(client_addresses(self.sender)) != 1:
            raise ValueError('Niepoprawny adres nadawcy.')

        for _company_id, addresses in self.companies:
            if not addresses or client_addresses(','.join(addresses)) != addresses:
                raise ValueError('Niepoprawna lista odbiorców.')

        if not self.recipients:
            raise ValueError('Zaznacz co najmniej jedną firmę z poprawnym adresem e-mail.')

    @property
    def recipients(self):
        found = {}

        for _company_id, addresses in self.companies:
            for address in addresses:
                if address.casefold() != self.sender.casefold():
                    found.setdefault(address.casefold(), address)

        return tuple(found.values())


def send_bulk_batch(plan, recipients):
    try:
        if 'E-mail' not in allowed_tabs(plan.actor_id):
            raise PermissionError('Utracono dostęp do modułu E-mail.')

        accounts = get_email_accounts_for_employee(plan.actor_id)

        account = next(
            (a for a in accounts if a['id'] == plan.account_id),
            None
        )

        if not account or str(account.get('email') or '').casefold() != plan.sender.casefold():
            raise PermissionError('Konto nadawcze zmieniło się lub nie jest już dostępne.')

        companies = {
            row['id']: row
            for row in list_mailing_companies(plan.actor_id)
        }

        for company_id, confirmed in plan.companies:
            current = companies.get(company_id)

            if current is None or client_addresses(current.get('adres_email')) != confirmed:
                raise PermissionError(
                    'Dane odbiorców zmieniły się. Zatrzymano wysyłkę; sprawdź listę firm.'
                )

    except Exception as exc:
        return {
            'ok': False,
            'blocked': True,
            'info': str(exc)
        }

    ok, info = send_email_smtp(
        plan.account_id,
        [plan.sender],
        plan.subject,
        plan.body,
        bcc=list(recipients),
        save_copy=True,
        attachments=list(plan.attachments)
    )

    return {
        'ok': ok,
        'blocked': False,
        'info': str(info).splitlines()[0] if info else ''
    }


class BulkMailSender(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = CommandRunner(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._next)
        self.running = False
        self._busy = False
        self._stopped = False

    def start(self, plan):
        if self.running:
            raise RuntimeError('Wysyłka już trwa.')
        self.plan = plan
        self._index = 0
        self._stopped = False
        self.report = []
        self.running = True
        self._next()

    def cancel(self):
        if not self.running:
            return
        self._stopped = True
        self._timer.stop()
        if not self._busy:
            self._finish()

    def _next(self):
        if not self.running or self._busy:
            return
        if self._stopped or self._index >= len(self.plan.recipients):
            self._finish()
            return
        batch = self.plan.recipients[self._index:self._index + self.plan.batch_size]
        self._busy = True
        self.progress.emit({'done': self._index, 'total': len(self.plan.recipients), 'waiting': False})
        def done(result):
            self._busy = False
            status = 'sent' if result['ok'] else 'blocked' if result['blocked'] else 'uncertain'
            self.report.append({'recipients': batch, 'status': status, 'info': result['info']})
            if status != 'blocked':
                self._index += len(batch)
            if not result['ok'] or self._stopped or self._index >= len(self.plan.recipients):
                self._finish()
                return
            self.progress.emit({'done': self._index, 'total': len(self.plan.recipients), 'waiting': True})
            self._timer.start(self.plan.pause_seconds * 1000)
        def failed(exc):
            done({'ok': False, 'blocked': False, 'info': str(exc)})
        try:
            self._jobs.submit(lambda: send_bulk_batch(self.plan, batch), done, failed)
        except Exception as exc:
            done({'ok': False, 'blocked': True, 'info': str(exc)})

    def _finish(self):
        if not self.running:
            return
        self._timer.stop()
        self.running = False
        self.finished.emit({'entries': list(self.report), 'remaining': len(self.plan.recipients) - self._index,
                            'stopped': self._stopped, 'total': len(self.plan.recipients)})
