"""Jednorazowe odtworzenie zaznaczeń deklaracji z Płatnika i Enovy.

Na VM workera, w głównym katalogu repozytorium Biuro, uruchom:
    python -m scripts.odtworz_zaznaczenia_deklaracji --year 2025
    python -m scripts.odtworz_zaznaczenia_deklaracji --year 2025 --apply

Pierwsze polecenie robi podgląd. Podaj właściwy rok rozliczeń. Skrypt używa
konfiguracji worker.env z konta workera. Ścieżkę można podać przez --worker-env.
Można zacząć od jednej firmy, podając --company-id z jej identyfikatorem w Biuro.
"""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


def read_platnik(companies, company_ids, months, year):
    from app.db.dao_conn import advisory_lock
    from app.integrations.platnik import declarations

    mapped = declarations.payer_mapping(companies, company_ids)
    monthly = {company_id: {month: set() for month in months} for company_id in set(mapped.values())}
    yearly = defaultdict(set)
    if not mapped:
        return monthly, yearly
    config = declarations.load()
    with advisory_lock(748220) as acquired:
        if not acquired:
            raise ValueError('Trwa inny odczyt Płatnika. Ponów po jego zakończeniu.')
        # Read each month in bulk for the selected companies, within the adapter row limit.
        for month in months:
            rows = declarations.fetch(config, [month], selected_nips=mapped)
            for nip, entries in declarations.normalize(rows, [month], mapped).items():
                monthly[mapped[nip]][month].update(entry['form'] for entry in entries[month])
        period = str(year)
        rows = declarations.fetch(config, [period], annual=True, selected_nips=mapped)
        for nip, entries in declarations.normalize(rows, [period], mapped, annual=True).items():
            yearly[mapped[nip]].update(entry['form'] for entry in entries[period])
    return monthly, yearly


def monthly_selection(previous, monthly, declarations, jpk, stamp):
    from app.companies.checking_obligations import merge_source_scope, profile, source_scope
    from app.utils.months import month_bounds

    periods = previous.get('periods', {})
    selected = dict(periods)
    imported = dict(previous.get('imported', {}))
    next_period = month_bounds(next(reversed(monthly)))[1][:7]
    # Restore the prior recurring profile after the last rebuilt month if its
    # starting month is being replaced. Existing later changes take precedence.
    while periods.get(next_period, {}).get('source_month'):
        next_period = month_bounds(next_period)[1][:7]
    recurring = [month for month, value in periods.items()
                 if month <= next_period and not value.get('source_month')]
    if recurring and max(recurring) in monthly and next_period not in periods:
        selected[next_period] = periods[max(recurring)]

    for month, forms in monthly.items():
        prior = profile(previous, month)
        value = {}
        merge_source_scope(value, source_scope({'form': form} for form in forms), 'platnik', stamp)
        merge_source_scope(value, source_scope(declarations[month]), 'enova', stamp)
        status = jpk[month]
        if status.get('form_variant') or status['status'] not in ('unknown', 'not_applicable'):
            merge_source_scope(value, source_scope([{'form': 'JPK_VAT', **status}]), 'enova-jpk', stamp)
        imported[month] = value
        selected[month] = {
            'source_month': True,
            'vat_period': prior['vat_period'],
            'forms': {form: 'quarterly' for form in ('PIT', 'CIT')
                      if prior['forms'].get(form) == 'quarterly' and form in value['forms']},
        }
        if 'document_types' in prior:
            selected[month]['document_types'] = prior['document_types']
    return {**previous, 'periods': selected, 'imported': imported}


def update_company(company_id, year, monthly, yearly, declarations, jpk, source, *, apply):
    from app.accounting.report_types import form_key
    from app.companies.annual_declarations import DECLARATION_DETAILS
    from app.db.checking_storage import read_snapshot, save_snapshot
    from app.db.dao_conn import get_conn
    from app.db.dao_year_end import company_declarations, write_company_year_declarations
    from app.integrations.enova.document_counts import normalize_nip
    from app.integrations.enova.query_defaults import bundled_directory
    from app.integrations.platnik.form_catalog import ANNUAL

    stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
    with get_conn() as conn:
        if apply:
            conn.execute('SELECT pg_advisory_xact_lock(748221,%s)', (company_id,))
            conn.execute('SELECT pg_advisory_xact_lock(748204)')
        company = conn.execute('SELECT * FROM firmy WHERE id=%s', (company_id,)).fetchone()
        if not company or normalize_nip(company['nip']) != normalize_nip(source['nip']):
            raise ValueError('Firma zmieniła się podczas odczytu.')
        previous = read_snapshot(conn, 'checking_obligations', company_id, lock=apply) or {}
        value = monthly_selection(previous, monthly, declarations, jpk, stamp)
        annual = conn.execute('SELECT company_json FROM year_end_snapshots WHERE rok=%s AND firma_id=%s',
                              (year, company_id)).fetchone()
        snapshot = json.loads(annual[0]) if annual else dict(company)
        current = snapshot['declaration_codes'] if 'declaration_codes' in snapshot else company_declarations(snapshot)
        names = {form_key(code): code for code in DECLARATION_DETAILS}
        detected = {form_key(form) for form in yearly}
        detected.update(form_key(entry['form']) for entries in declarations.values() for entry in entries
                        if entry['status'] != 'cancelled')
        detected &= names.keys()
        covered = ANNUAL.keys() | detected
        if Path(source['declarations_query']).resolve() == bundled_directory() / 'declarations.sql':
            covered.add('CIT8')
        filed = conn.execute('SELECT deklaracja FROM zakonczenie_roku WHERE rok=%s AND firma_id=%s AND owner_key=0',
                             (year, company_id)).fetchall()
        codes = list(dict.fromkeys([code for code in current if form_key(code) not in covered]
                                   + sorted(names[code] for code in detected) + [row[0] for row in filed]))
        if apply:
            save_snapshot(conn, 'checking_obligations', company_id, '', value)
            write_company_year_declarations(conn, company, year, codes, None)
            conn.execute("INSERT INTO audit_log(tabela,rekord_id,akcja,szczegoly) "
                         "VALUES ('firmy',%s,'odtworzenie_deklaracji',%s)",
                         (company_id, json.dumps({'year': year, 'previous_monthly': previous,
                                                  'previous_annual': current})))
    return {month: list(value['imported'][month]['forms']) for month in monthly}, codes


def rebuild(year, company_ids=None, *, apply=False):
    from app.db.dao_conn import advisory_lock, get_conn
    from app.integrations.enova.connection_settings import load_connection
    from app.integrations.enova.document_counts import normalize_nip
    from app.remote.secrets import safe_error
    from app.services.declaration_year import fetch_enova

    today = datetime.now(ZoneInfo('Europe/Warsaw'))
    if not 2000 <= year <= today.year:
        raise ValueError('Wybierz rok rozliczeń od 2000 do bieżącego roku.')
    months = [f'{year}-{number:02d}' for number in range(1, (today.month if year == today.year else 12) + 1)]
    company_ids = None if company_ids is None else set(company_ids)
    with advisory_lock(748229) as acquired:
        if not acquired:
            raise ValueError('Trwa już odtwarzanie zaznaczeń deklaracji.')
        with get_conn() as conn:
            companies = conn.execute('SELECT id,nip,forma_dzialalnosci FROM firmy ORDER BY id').fetchall()
        if not companies:
            raise ValueError('Brak firm do sprawdzenia.')
        monthly, yearly = read_platnik(companies, company_ids, months, year)
        config = load_connection()
        sources = defaultdict(list)
        for source in config['sources']:
            sources[normalize_nip(source['nip'])].append(source)
        completed = skipped = 0
        print(f'Rok {year}, okres {months[0]} – {months[-1]}. ' + ('Zapis.' if apply else 'Podgląd.'), flush=True)
        with advisory_lock(748210) as enova_lock:
            if not enova_lock:
                raise ValueError('Trwa inny odczyt Enovy. Ponów po jego zakończeniu.')
            for company in companies:
                company_id = company['id']
                if company_ids is not None and company_id not in company_ids:
                    continue
                try:
                    matches = sources[normalize_nip(company['nip'])]
                    if company_id not in monthly or len(matches) != 1 or not matches[0].get('jpk_query'):
                        raise ValueError('Brak pełnego powiązania z Płatnikiem, Enovą lub odczytem JPK.')
                    source = matches[0]
                    declarations, jpk = fetch_enova(config, source, months)
                    forms, codes = update_company(company_id, year, monthly[company_id], yearly[company_id],
                                                  declarations, jpk, source, apply=apply)
                    completed += 1
                    count = sum(map(len, forms.values()))
                    print(f'Firma {company_id} | miesięczne zaznaczenia: {count} | roczne: {", ".join(codes) or "brak"}',
                          flush=True)
                    if not apply:
                        print('  ' + ' | '.join(f'{month}: {", ".join(items) or "brak"}' for month, items in forms.items()),
                              flush=True)
                except Exception as error:
                    skipped += 1
                    print(f'Firma {company_id} | pominięta. {safe_error(error)}', file=sys.stderr, flush=True)
    print(f'Firmy przetworzone: {completed}. Firmy pominięte: {skipped}.')
    return int(bool(skipped))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--year', required=True, type=int, help='rok rozliczeń')
    parser.add_argument('--company-id', action='append', type=int, help='ogranicz odczyt do wybranej firmy Biuro')
    parser.add_argument('--worker-env', type=Path, default=Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Biuro' / 'worker.env',
                        help='ścieżka do konfiguracji workera')
    parser.add_argument('--apply', action='store_true', help='zapisz zaznaczenia po poprawnym odczycie źródeł')
    options = parser.parse_args(argv)

    from dotenv import dotenv_values
    from cryptography.fernet import Fernet
    from app.remote.secrets import safe_error

    try:
        if not options.worker_env.is_file():
            raise ValueError('Nie znaleziono worker.env. Podaj jego ścieżkę przez --worker-env.')
        config = dotenv_values(options.worker_env, interpolate=False)
        database, key = config.get('DB_URL'), config.get('BIO_ENCRYPTION_KEY')
        if not database or not key:
            raise ValueError('worker.env musi zawierać DB_URL i BIO_ENCRYPTION_KEY.')
        Fernet(key.encode())
        for flag in ('BIO_CLIENT_MODE', 'BIO_DEV_DB', 'BIO_WORKER_MODE'):
            os.environ.pop(flag, None)
        from app.config import load_environment
        load_environment()
        os.environ.update(DB_URL=database, BIO_ENCRYPTION_KEY=key)
        return rebuild(options.year, options.company_id, apply=options.apply)
    except Exception as error:
        print(safe_error(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
