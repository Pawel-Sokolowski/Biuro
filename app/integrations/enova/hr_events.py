"""Verified-query adapter for active contracts, medical exams and OHS dates."""
from datetime import date, datetime, timedelta
from app.integrations.enova.document_counts import fetch_counts, normalize_nip
from app.utils.months import month_bounds

TYPES = {'contract_end', 'medical_exam', 'ohs_training'}


def event_months(day):
    current = day.strftime('%Y-%m')
    last = (day + timedelta(days=91)).strftime('%Y-%m')
    result = [current]
    while result[-1] < last:
        result.append(month_bounds(result[-1])[1][:7])
    return result


def validate_events(rows, source):
    events, seen = [], set()
    for row in rows:
        if normalize_nip(row['nip']) != normalize_nip(source['nip']):
            raise ValueError('NIP terminu kadrowego nie odpowiada firmie.')
        kind = row['event_type']
        identity = str(row['event_id'] or '').strip()
        employee_id = str(row['employee_id'] or '').strip()
        employee_name = str(row['employee_name'] or '').strip()
        status = row['status']
        if kind not in TYPES or status not in ('active', 'completed', 'cancelled') or not identity or not employee_id or not employee_name:
            raise ValueError('Nieprawidłowy rodzaj, stan lub identyfikator terminu kadrowego.')
        if (kind, identity) in seen:
            raise ValueError('Powtórzony termin kadrowy.')
        seen.add((kind, identity))
        due = row['due_date']
        if due not in (None, ''):
            try:
                if isinstance(due, datetime):
                    due = due.date()
                due = due.isoformat() if isinstance(due, date) else date.fromisoformat(str(due)).isoformat()
            except ValueError:
                raise ValueError('Nieprawidłowa data terminu kadrowego.') from None
        else:
            due = None
        events.append({'event_id': identity, 'employee_id': employee_id, 'employee_name': employee_name,
                       'event_type': kind, 'due_date': due, 'status': status,
                       'document_number': str(row.get('document_number') or '').strip()})
    return events


def fetch_events(config, source, day):
    query = {**source, 'count_query': source['hr_events_query']}
    return validate_events(fetch_counts(config, query, event_months(day), result_kind='hr_events'), source)
