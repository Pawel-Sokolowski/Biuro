"""Comparison and follow-up rules; missing source reads are never zero-filled."""
from datetime import date, timedelta
from app.integrations.enova.accounting import decoded, documents, declaration_entries
from app.integrations.enova.checking import history_months

HR_LABELS = {'contract_end': 'Koniec umowy', 'medical_exam': 'Badania lekarskie', 'ohs_training': 'Szkolenie BHP'}


def _count(report, department, kind=None):
    counts = report.get('documents', {}).get(department)
    if counts is None:
        return None
    return sum(counts.values()) if kind is None else counts.get(kind, 0)


def comparison(row, month, department, kind=None):
    counts = documents(row, department)
    full = bool(decoded(row.get('checking')))
    current = None if counts is None or (kind is not None and kind not in counts and not full) else (
        sum(counts.values()) if kind is None else counts.get(kind, 0))
    history = {report['month']: report for report in row.get('history', []) if report.get('month')}
    previous = history_months(month, 1)[0]
    prior = _count(history.get(previous, {}), department, kind)
    delta = current - prior if current is not None and prior is not None else None
    return {'current': current, 'previous': prior, 'delta': delta,
            'percent': delta / prior * 100 if delta is not None and prior else None,
            'new': current is not None and current > 0 and prior == 0}


def previous_active_types(row, month):
    previous = history_months(month, 1)[0]
    return {kind for report in row.get('history', []) if report.get('month') == previous
            for kind, count in (report.get('documents', {}).get('accounting') or {}).items() if count > 0}


def declaration_issue(entry):
    status = entry.get('status')
    if status == 'not_applicable':
        return None
    if status == 'accepted' and entry.get('upo'):
        return None
    return {'sent': 'Oczekuje na UPO', 'accepted': 'Brak potwierdzenia UPO',
            'rejected': 'Odrzucona', 'failed': 'Błąd przetwarzania', 'cancelled': 'Anulowana', 'not_sent': 'Niewysłana', 'prepared': 'Przygotowana, niewysłana',
            'processing': 'Przetwarzana', 'unknown': 'Odczytano dokument; status wysyłki/UPO nieustalony'}.get(status, 'Nie sprawdzono')


def hr_status(event, today=None, days=30):
    today = today or date.today()
    if event.get('status') != 'active':
        return None
    if not event.get('due_date'):
        return 'Bezterminowa' if event['event_type'] == 'contract_end' else 'Brak terminu'
    due = date.fromisoformat(event['due_date'])
    if due < today:
        return 'Po terminie'
    if due == today:
        return 'Dzisiaj'
    if due <= today + timedelta(days=days):
        return 'Zbliża się termin'
    return 'Późniejszy termin'


def needs_attention(row, department, today, days=30, month=None):
    counts = documents(row, department)
    full = bool(decoded(row.get('checking')))
    if department == 'accounting' and month and counts is not None and any(
            (kind in counts or full) and counts.get(kind, 0) == 0 for kind in previous_active_types(row, month)):
        return True
    if any(declaration_issue(entry) for entry in declaration_entries(row, department)):
        return True
    return department == 'payroll' and any(hr_status(event, today, days) in
        ('Po terminie', 'Dzisiaj', 'Zbliża się termin', 'Brak terminu')
        for event in decoded(row.get('hr')).get('events') or [])
