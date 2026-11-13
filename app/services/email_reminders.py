"""Scheduled reminders. No SMTP retry after an ambiguous delivery result."""
from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
import logging
from string import Formatter
from zoneinfo import ZoneInfo

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn

logger = logging.getLogger(__name__)
SETTINGS_KEY = 'automatic_email_reminders'
FIELDS = {'firma', 'miesiac', 'termin', 'numer', 'pozostalo'}
DEFAULTS = {
    'templates': [
        {'name': 'Brak dokumentów', 'subject': 'Przypomnienie o dokumentach za {miesiac}',
         'body': 'Dzień dobry,\n\nProsimy o dostarczenie do biura kompletu dokumentów firmy {firma} za {miesiac}. Termin: {termin}.\n\nDziękujemy.'},
        {'name': 'Termin płatności', 'subject': 'Przypomnienie o płatności faktury {numer}',
         'body': 'Dzień dobry,\n\nPrzypominamy o płatności faktury {numer} dla {firma}. Termin płatności: {termin}. Pozostało do zapłaty: {pozostalo} PLN.\n\nDziękujemy.'},
    ],
    'documents': {'enabled': False, 'template': 'Brak dokumentów', 'day': 10,
                  'offsets': [1], 'repeat': 3, 'time': '09:00', 'weekdays': [0, 1, 2, 3, 4], 'holidays': False},
    'payments': {'enabled': False, 'template': 'Termin płatności', 'offsets': [0, 2, 3],
                 'repeat': 3, 'time': '09:00', 'weekdays': [0, 1, 2, 3, 4], 'holidays': False},
}


def load_settings():
    result = deepcopy(DEFAULTS)
    raw = get_setting(SETTINGS_KEY)
    if raw:
        saved = json.loads(raw)
        result['templates'] = saved.get('templates', result['templates'])
        for kind in ('documents', 'payments'):
            result[kind].update(saved.get(kind, {}))
    validate_settings(result)
    return result


def validate_settings(settings):
    names = set()
    for template in settings['templates']:
        name = template['name'].strip()
        if not name or name in names:
            raise ValueError('Nazwy szablonów muszą być niepuste i unikalne.')
        names.add(name)
        for key in ('subject', 'body'):
            if not template[key].strip():
                raise ValueError('Uzupełnij temat i treść szablonu.')
            for _, field, spec, conversion in Formatter().parse(template[key]):
                if field is not None and (field not in FIELDS or spec or conversion):
                    raise ValueError('Dozwolone pola: ' + ', '.join(sorted(FIELDS)))
        if '\n' in template['subject'] or '\r' in template['subject']:
            raise ValueError('Temat musi mieścić się w jednym wierszu.')
    for kind in ('documents', 'payments'):
        rule = settings[kind]
        if rule['template'] not in names:
            raise ValueError('Wybierz istniejący szablon dla każdej reguły.')
        time.fromisoformat(rule['time'])
        if not rule['weekdays'] or any(type(d) is not int or d not in range(7) for d in rule['weekdays']):
            raise ValueError('Wybierz co najmniej jeden dzień tygodnia.')
        if not rule['offsets'] or any(type(d) is not int or not 0 <= d <= 365 for d in rule['offsets']):
            raise ValueError('Odstępy muszą być liczbami dni od 0 do 365.')
        if type(rule['repeat']) is not int or not 0 <= rule['repeat'] <= 365:
            raise ValueError('Powtarzanie musi wynosić od 0 do 365 dni.')
    if not 1 <= settings['documents']['day'] <= 31:
        raise ValueError('Dzień terminu dokumentów musi wynosić od 1 do 31.')


def save_settings(settings):
    validate_settings(settings)
    set_setting(SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))


def polish_holiday(day):
    """Polish statutory holidays, including Christmas Eve from 2025."""
    y = day.year
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    easter = date(y, n // 31, n % 31 + 1)
    fixed = {(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 25), (12, 26)}
    if y >= 2025:
        fixed.add((12, 24))
    return (day.month, day.day) in fixed or day in {easter, easter + timedelta(days=1),
                                                                  easter + timedelta(days=49), easter + timedelta(days=60)}


def scheduled_slot(due, rule, now):
    """Latest due slot, shifted forward over excluded days; one catch-up, no backlog burst."""
    today = now.date()
    if (today.weekday() not in rule['weekdays']
            or (not rule['holidays'] and polish_holiday(today))
            or now.time().replace(tzinfo=None) < time.fromisoformat(rule['time'])):
        return None
    offsets = sorted(set(rule['offsets']))
    age = (today - due).days
    slots = [due + timedelta(days=n) for n in offsets if n <= age]
    repeat = rule['repeat']
    if repeat and age > offsets[-1]:
        last = offsets[-1] + ((age - offsets[-1]) // repeat) * repeat
        # A previous slot may have moved beyond the nominal latest slot.
        for n in (last - repeat, last):
            if n > offsets[-1]:
                slots.append(due + timedelta(days=n))
    eligible = []
    for slot in slots:
        while slot.weekday() not in rule['weekdays'] or (not rule['holidays'] and polish_holiday(slot)):
            slot += timedelta(days=1)
        if datetime.combine(slot, time.fromisoformat(rule['time']), tzinfo=now.tzinfo) <= now:
            eligible.append(slot)
    return max(eligible) if eligible else None


def _candidates(conn, kind, now, rule, target_id=None):
    if kind == 'documents':
        first = now.date().replace(day=1)
        month = (first - timedelta(days=1)).strftime('%Y-%m')
        due = first.replace(day=min(rule['day'], calendar.monthrange(first.year, first.month)[1]))
        rows = conn.execute("""SELECT f.id, f.nazwa AS firma, f.adres_email AS email
            FROM firmy f LEFT JOIN statusy_miesieczne s ON s.firma_id = f.id AND s.miesiac = %s
            WHERE COALESCE(s.dokumenty_dostarczone, 0) = 0 AND f.status = 'Aktywne'
              AND f.ksiegowa_id IS NOT NULL
              AND (%s::integer IS NULL OR f.id = %s)
              AND (NULLIF(f.data_rozpoczecia_obslugi, '') IS NULL OR f.data_rozpoczecia_obslugi <= %s)
              AND (NULLIF(f.data_zakonczenia_obslugi, '') IS NULL OR f.data_zakonczenia_obslugi >= %s)""",
            (month, target_id, target_id, (first - timedelta(days=1)).isoformat(), first.isoformat())).fetchall()
        for row in rows:
            yield {'target': f"{row['id']}:{month}", 'email': row['email'], 'due': due,
                   'firma': row['firma'], 'miesiac': month, 'numer': '', 'pozostalo': ''}
    else:
        rows = conn.execute("""SELECT i.id, i.numer, i.miesiac, i.termin_platnosci,
            COALESCE(NULLIF(i.nabywca_pelna, ''), f.nazwa, k.nazwa, '') AS firma,
            COALESCE(NULLIF(i.nabywca_email, ''), NULLIF(f.email_faktury, ''), f.adres_email, k.email) AS email,
            CAST(i.kwota_brutto AS NUMERIC(14,2)) - COALESCE(p.paid, 0) AS balance
            FROM faktury i LEFT JOIN firmy f ON f.id = i.firma_id
            LEFT JOIN kontrahenci k ON k.id = i.kontrahent_id
            LEFT JOIN (SELECT faktura_id, SUM(kwota) AS paid FROM faktury_platnosci GROUP BY faktura_id) p
                ON p.faktura_id = i.id
            WHERE i.status = 'wystawiona' AND i.cancelled_at IS NULL
              AND (%s::integer IS NULL OR i.id = %s)
              AND i.rodzaj IN ('faktura', 'zaliczkowa')
              AND CAST(i.kwota_brutto AS NUMERIC(14,2)) - COALESCE(p.paid, 0) > 0
              AND NOT EXISTS (SELECT 1 FROM faktury_kosz t WHERE t.faktura_id = i.id AND t.restored_at IS NULL)""",
            (target_id, target_id)).fetchall()
        for row in rows:
            try:
                due = date.fromisoformat(str(row['termin_platnosci']))
            except (ValueError, TypeError):
                continue
            yield {'target': str(row['id']), 'email': row['email'], 'due': due,
                   'firma': row['firma'], 'miesiac': row['miesiac'], 'numer': row['numer'] or '',
                   'pozostalo': f"{Decimal(str(row['balance'])):.2f}"}


def run_reminders(now=None):
    """Run from GUI timer or an unattended process. Never send when rules are disabled."""
    from app.email.email_actions import send_email_smtp
    from app.utils.system_mailbox import get_system_account
    now = (now or datetime.now(ZoneInfo('Europe/Warsaw'))).astimezone(ZoneInfo('Europe/Warsaw'))
    settings = load_settings()
    if not any(settings[k]['enabled'] for k in ('documents', 'payments')):
        return 0
    account = get_system_account()
    if not account or not account.get('aktywne'):
        logger.warning('Przypomnienia: skonfiguruj konto systemowe w ustawieniach.')
        return 0
    conn = get_conn()
    sent = 0
    try:
        # Unique claims span workstations and are committed before SMTP.
        # Uncertain attempts never retry automatically.
        for kind in ('documents', 'payments'):
            rule = settings[kind]
            if not rule['enabled']:
                continue
            template = next(t for t in settings['templates'] if t['name'] == rule['template'])
            for candidate in _candidates(conn, kind, now, rule):
                # Recheck status/balance after earlier SMTP operations in this batch.
                candidate = next(_candidates(conn, kind, now, rule, int(candidate['target'].split(':')[0])), None)
                if candidate is None:
                    continue
                slot = scheduled_slot(candidate['due'], rule, now)
                recipient = (candidate['email'] or '').strip()
                if slot is None or not recipient:
                    continue
                from app.invoices.invoice_delivery import parse_recipients
                try:
                    recipients = parse_recipients(recipient)
                except ValueError:
                    logger.warning('Przypomnienia: nieprawidłowy adres dla %s %s', kind, candidate['target'])
                    continue
                values = {key: str(candidate.get(key, '')) for key in FIELDS}
                values['termin'] = candidate['due'].isoformat()
                subject, body = (template[key].format(**values) for key in ('subject', 'body'))
                from app.services.manual_reminders import sent_manually_today,lock_day
                lock_day(conn,kind,candidate['target'],now.date())
                if sent_manually_today(conn,kind,candidate['target'],now.date()):
                    conn.commit()
                    continue
                claim = conn.execute("""INSERT INTO email_reminder_log
                    (kind, target, scheduled_date, recipient, subject, body)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
                    (kind, candidate['target'], slot, recipient, subject, body)).fetchone()
                conn.commit()
                if claim is None:
                    continue
                try:
                    ok, error = send_email_smtp(account['id'], recipients, subject, body)
                except Exception as exc:
                    ok, error = False, str(exc)
                conn.execute("""UPDATE email_reminder_log SET status = %s, error = %s,
                    sent_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = %s""",
                    ('sent' if ok else 'failed', None if ok else error, ok, claim[0]))
                conn.commit()
                sent += int(ok)
        return sent
    finally:
        conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_reminders()
