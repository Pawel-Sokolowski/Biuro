"""Resolve report payer against authorized companies and existing Enova mapping."""
from app.remote.boundary import endpoint as office_endpoint
import re
from app.db.company_labels import company_label_sql
from app.db.dao_conn import get_conn
from app.integrations.enova.accounting import require_access
from app.security.authorization import assigned_company_ids
from app.integrations.ezla.report import read_report, MAX_DOCUMENTS
from app.companies.identities import is_civil, payer_nips
from app.db.dao_companies import get_partners


@office_endpoint('app.integrations.ezla.preview.preview_report')
def preview_report(actor_id, path):
    require_access(actor_id)
    report = read_report(path)
    ids = list(assigned_company_ids(actor_id))
    with get_conn() as conn:
        companies = conn.execute(f"SELECT f.id, {company_label_sql('f')} AS nazwa, "
                                 "f.nip, f.forma_dzialalnosci FROM firmy f WHERE f.id=ANY(%s)", (ids,)).fetchall()
    matches = []
    for row in companies:
        try:
            identities = payer_nips(row, get_partners(row['id']) if is_civil(row) else ())
        except ValueError:
            continue
        if report.nip in identities:
            matches.append(row)
    if len(matches) != 1:
        raise ValueError('NIP raportu nie wskazuje jednej dostępnej firmy w Biuro. Sprawdź kartotekę i uprawnienia.')
    from app.integrations.enova.connection_settings import load_connection
    company_nip = re.sub(r'[\s-]', '', str(matches[0]['nip'] or '')).removeprefix('PL')
    sources = [source for source in load_connection()['sources']
               if re.sub(r'[\s-]', '', str(source.get('nip') or '')).removeprefix('PL') == company_nip]
    database = sources[0].get('database', '') if len(sources) == 1 else ''
    return {'report': report, 'company': dict(matches[0]), 'database': database}


@office_endpoint('app.integrations.ezla.preview.preview_reports')
def preview_reports(actor_id, paths):
    if not paths or len(paths) > 20:
        raise ValueError('Wybierz od 1 do 20 raportów XML jednocześnie.')
    require_access(actor_id)
    results, errors, count = [], [], 0
    for index, path in enumerate(paths, 1):
        try:
            result = preview_report(actor_id, path)
        except (ValueError, PermissionError) as error:
            errors.append(f'Raport {index}: {error}')
            continue
        except Exception:
            errors.append(f'Raport {index}: nie udało się odczytać pliku lub ustawień.')
            continue
        count += len(result['report'].certificates)
        if count > MAX_DOCUMENTS:
            raise ValueError('Wybrane raporty zawierają zbyt wiele dokumentów. Wczytaj mniejszą partię.')
        results.append(result)
    return {'reports': results, 'errors': errors}
