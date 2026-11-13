"""Download generated ZUS reports atomically per company; retain exact XML encrypted."""
import base64
from datetime import date, datetime, timedelta
import hashlib
import json
import threading
from zoneinfo import ZoneInfo
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import set_setting
from app.integrations.ezla.settings import load_settings, credentials
from app.integrations.ezla.service import fetch_reports, ServiceError
from app.integrations.ezla.report import parse_report
from app.integrations.enova.document_counts import normalize_nip
from app.utils.crypto import encrypt_password, decrypt_password
from app.utils.months import month_bounds
from app.services.worker_activity import record


def since_date(previous, today):
    try:
        prior = date.fromisoformat(previous.get('last_success', '')[:10]) - timedelta(days=1)
    except ValueError:
        prior = today - timedelta(days=30)
    return max(today - timedelta(days=30), min(today, prior))


def _metadata(report):
    months, review = set(), bool(report.issues)
    for item in report.certificates:
        if item.issues:
            review = True
            continue
        period, end = item.start[:7], item.end[:7]
        for _ in range(36):
            if period > end:
                break
            months.add(period)
            period = month_bounds(period)[1][:7]
        else:
            review = True
    if not report.certificates:
        months.add(report.generated_at[:7])
    return sorted(months), review


def _write(conn, key, value):
    conn.execute('INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s) '
                 'ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc', (key, json.dumps(value)))


def wait_between(seconds, stop):
    return stop.wait(seconds)


def progress(stage, **details):
    activity_stage = {'Pobieranie i odczyt ZIP': 'downloading', 'Zapisywanie raportów': 'saving',
                      'Przerwa między firmami': 'pause', 'Przerwano': 'cancelled'}.get(stage)
    if activity_stage:
        record('ezla', activity_stage, company_id=details.get('company_id'))
    set_setting('ezla_progress', json.dumps({'stage': stage, **details,
                'checked_at': datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()}))


def run_sync(*, actor_id=None, today=None, stop=None, company_ids=None):
    if actor_id is not None:
        from app.security.authorization import is_administrator
        if not is_administrator(actor_id):
            raise PermissionError('Tylko administrator może uruchomić pobieranie e-ZLA.')
    with get_conn() as leader:
        if not leader.execute('SELECT pg_try_advisory_xact_lock(748212)').fetchone()[0]:
            return {'mode': 'busy', 'companies': 0, 'failed': 0}
        return _run_sync(today=today, stop=stop, company_ids=company_ids)


def _run_sync(*, today=None, stop=None, company_ids=None):
    config = load_settings()
    if company_ids is not None:
        if not company_ids or any(type(fid) is not int or fid not in config['companies'] for fid in company_ids):
            raise ValueError('Wybrane firmy muszą być skonfigurowane w usłudze e-ZLA workera.')
        config['companies'] = sorted(set(company_ids))
    if not config['enabled'] and company_ids is None:
        return {'mode': 'disabled', 'companies': 0, 'failed': 0}
    today = today or datetime.now(ZoneInfo('Europe/Warsaw')).date()
    progress('Odczyt ustawień usługi')
    login, password, zip_password = credentials(config)
    with get_conn() as conn:
        companies = conn.execute('SELECT id,nip,forma_dzialalnosci FROM firmy WHERE id=ANY(%s) ORDER BY id', (config['companies'],)).fetchall()
    from app.db.dao_companies import get_partners
    from app.companies.identities import payer_nips
    results = []
    stop = stop or threading.Event()
    for index, company in enumerate(companies):
        if stop.is_set():
            break
        fid, previous = company['id'], {}
        stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
        try:
            with get_conn() as conn:
                if not conn.execute('SELECT pg_try_advisory_xact_lock(748211,%s)', (fid,)).fetchone()[0]:
                    results.append({'company_id': fid, 'state': 'busy'})
                    record('ezla', 'busy', company_id=fid)
                    continue
                old = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (f'ezla_sync_{fid}',)).fetchone()
                previous = json.loads(old[0]) if old else {}
                identities = payer_nips(company, get_partners(fid))
                payer_states = dict(previous.get('payers') or {})
                added, report_count, failures = 0, 0, []
                for nip in identities:
                    if stop.is_set():
                        break
                    prior = payer_states.get(nip, previous if nip == normalize_nip(company['nip']) else {})
                    try:
                        with conn.transaction():
                            progress('Pobieranie i odczyt ZIP', company_id=fid, position=index + 1, total=len(companies))
                            reports = fetch_reports(nip, login, password, zip_password, since_date(prior, today), today=today)
                            progress('Zapisywanie raportów', company_id=fid, position=index + 1, total=len(companies))
                            payer_added = 0
                            for report in reports:
                                parsed = parse_report(report.xml)
                                if parsed.nip != nip:
                                    raise ServiceError('payer')
                                key = f'ezla_report_{fid}_' + hashlib.sha256(report.xml).hexdigest()
                                if conn.execute('SELECT 1 FROM app_settings WHERE klucz=%s', (key,)).fetchone():
                                    continue
                                months, review = _metadata(parsed)
                                value = {'nip': nip, 'generated_at': report.generated_at, 'fetched_at': stamp,
                                         'months': months, 'needs_review': review,
                                         'xml_enc': encrypt_password(base64.b64encode(report.xml).decode('ascii'))}
                                _write(conn, key, value)
                                payer_added += 1
                        added += payer_added
                        report_count += len(reports)
                        payer_states[nip] = {'state': 'ok', 'last_success': stamp, 'checked_at': stamp}
                    except Exception as error:
                        code = error.code if isinstance(error, ServiceError) else 'read_failed'
                        status = error.http_status if isinstance(error, ServiceError) else None
                        payer_states[nip] = {**prior, 'state': 'failed', 'checked_at': stamp, 'error_code': code, 'http_status': status}
                        failures.append((code, status))
                state = 'cancelled' if stop.is_set() else 'failed' if failures else 'ok'
                value = {**previous, 'state': state, 'checked_at': stamp, 'payers': payer_states,
                         'report_count': report_count, 'new_reports': added}
                if failures:
                    value.update(error_code=failures[0][0], http_status=failures[0][1])
                elif not stop.is_set():
                    value['last_success'] = stamp
                    value.pop('error_code', None)
                    value.pop('http_status', None)
                _write(conn, f'ezla_sync_{fid}', value)
                results.append({'company_id': fid, 'state': state, 'new_reports': added})
            record('ezla', 'failed' if failures else 'downloaded', company_id=fid, count=report_count)
        except Exception as error:
            # Keep the cursor and previous reports after any transport/decryption/validation failure.
            code = error.code if isinstance(error, ServiceError) else 'read_failed'
            set_setting(f'ezla_sync_{fid}', json.dumps({**previous, 'state': 'failed',
                        'checked_at': stamp, 'error_code': code,
                        'http_status': error.http_status if isinstance(error,ServiceError) else None}))
            results.append({'company_id': fid, 'state': 'failed', 'error_code': code})
            record('ezla', 'failed', company_id=fid, error_type=type(error).__name__)
        finally:
            if index + 1 < len(companies) and not stop.is_set():
                delay = config['error_pause_seconds'] if results and results[-1]['state'] == 'failed' else config['pause_seconds']
                progress('Przerwa między firmami', company_id=fid, position=index + 1, total=len(companies),
                         resume_at=(datetime.now(ZoneInfo('Europe/Warsaw')) + timedelta(seconds=delay)).isoformat())
                wait_between(delay, stop)
    progress('Przerwano' if stop.is_set() else 'Zakończono', processed=len(results), total=len(companies))
    return {'mode': 'cancelled' if stop.is_set() else 'completed',
            'companies': len(results), 'failed': sum(row['state'] == 'failed' for row in results), 'results': results}


def load_reports(actor_id, month):
    from app.integrations.enova.accounting import require_access
    from app.security.authorization import assigned_company_ids
    from app.integrations.enova.connection_settings import load_connection
    require_access(actor_id)
    month_bounds(month)
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        # app_settings contains plain text too. Materialize the authorized report
        # subset before JSON casts; a JOIN/WHERE alone does not fix evaluation order.
        rows = conn.execute('''WITH selected_reports AS MATERIALIZED (
            SELECT f.id,f.nazwa,f.nip,f.forma_dzialalnosci,a.klucz AS report_key,a.wartosc
            FROM firmy f JOIN app_settings a
              ON a.klucz='ezla_report_' || f.id::text || '_' || right(a.klucz,64)
            WHERE f.id=ANY(%s)
        ) SELECT * FROM selected_reports
          WHERE (wartosc::jsonb->'months') ? %s
             OR wartosc::jsonb->>'needs_review'='true'
          ORDER BY report_key''', (ids, month)).fetchall()
    sources = load_connection()['sources']
    reports, errors = [], []
    for row in rows:
        try:
            metadata = json.loads(row['wartosc'])
            xml = base64.b64decode(decrypt_password(metadata['xml_enc']), validate=True)
            parsed = parse_report(xml)
            from app.companies.identities import payer_nips
            from app.db.dao_companies import get_partners
            if parsed.nip not in payer_nips(row, get_partners(row['id'])):
                raise ServiceError('payer')
            databases = [source['database'] for source in sources if normalize_nip(source['nip']) == normalize_nip(row['nip'])]
            reports.append({'report': parsed, 'company': {'id': row['id'], 'nazwa': row['nazwa'], 'nip': row['nip']},
                            'database': databases[0] if len(databases) == 1 else ''})
        except Exception:
            errors.append(f"Firma {row['id']}: nie udało się odczytać zapisanego raportu. Sprawdź wspólny klucz i NIP firmy.")
    return {'reports': reports, 'errors': list(dict.fromkeys(errors))}
