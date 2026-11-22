"""Read the owner's registration intervals from the Płatnik insurance register."""
from datetime import date, datetime
from itertools import batched
import json
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from app.db.checking_storage import save_snapshots
from app.db.dao_conn import get_conn
from app.integrations.enova.document_counts import normalize_nip
from app.integrations.platnik.holidays import connect, sql_operation
from app.utils.crypto import encrypt_password

SOURCE = 'platnik_insurance'
ERROR_MESSAGES = {
    'InsuranceOwnerAmbiguous': 'Kilka kartotek właściciela w rejestrze ubezpieczeń Płatnika.',
    'InsurancePeriodInvalid': 'Nieprawidłowy okres ubezpieczenia właściciela w Płatniku.',
    'InsuranceRegistryReadFailed': 'Odczyt rejestru ubezpieczeń Płatnika nie powiódł się.',
    'InsuranceRegistryUnavailable': 'Brak wymaganych tabel lub kolumn rejestru ubezpieczeń Płatnika.',
    'InsuranceOwnerLinkStale': 'Zatwierdzona kartoteka właściciela wymaga ponownej weryfikacji.',
}
REQUIRED = {
    'PLATN_IDENT': {'ID_PLATNIK', 'NIP', 'PESEL'},
    'UBEZPIECZONY': {'ID', 'ID_PLATNIK'},
    'UBEZP_IDENT': {'ID_UBEZPIECZONY', 'NIP', 'PESEL'},
    'UBEZP_PODLEGANIE': {'ID_UBEZPIECZONY', 'KODTYTUB', 'PRDOEM', 'STNIEP', 'DATAOD', 'DATADO'},
}


def payer_scope(*, include_pesel=False):
    pesel = ", NULLIF(LTRIM(RTRIM(p.PESEL)),N'') AS pesel" if include_pesel else ''
    return f"""
WITH Parameters AS (
    SELECT CAST(? AS xml) AS Payers
), SelectedNips AS (
    SELECT refs.nip.value('(text())[1]', 'nvarchar(10)') AS nip
    FROM Parameters p CROSS APPLY p.Payers.nodes('/p/n') AS refs(nip)
), PayerIdentities AS (
    SELECT p.ID_PLATNIK, s.nip{pesel}
    FROM dbo.PLATN_IDENT p
    CROSS APPLY (SELECT REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(p.NIP),
        N'-',N''),N' ',N''),NCHAR(9),N''),NCHAR(10),N''),NCHAR(13),N''),NCHAR(160),N'') AS nip) cleaned
    JOIN SelectedNips s ON s.nip=CASE WHEN LEFT(cleaned.nip,2)=N'PL'
        THEN SUBSTRING(cleaned.nip,3,32) ELSE cleaned.nip END
)
"""


OWNER_SCOPE = payer_scope(include_pesel=True) + """,
Owners AS (
    SELECT DISTINCT p.nip, p.ID_PLATNIK, u.ID AS person_id
    FROM PayerIdentities p
    JOIN dbo.UBEZPIECZONY u ON u.ID_PLATNIK=p.ID_PLATNIK
    JOIN dbo.UBEZP_IDENT i ON i.ID_UBEZPIECZONY=u.ID
    WHERE (LEN(p.pesel)=11 AND p.pesel NOT LIKE N'%[^0-9]%' AND LTRIM(RTRIM(i.PESEL))=p.pesel)
       OR ((p.pesel IS NULL OR LEN(p.pesel)<>11 OR p.pesel LIKE N'%[^0-9]%')
           AND REPLACE(REPLACE(UPPER(LTRIM(RTRIM(i.NIP))),N'-',N''),N' ',N'') IN (p.nip,N'PL'+p.nip))
)
"""
PERIOD_FILTER = "r.KODTYTUB IS NOT NULL AND LEFT(LTRIM(RTRIM(r.KODTYTUB)),2)=N'05'"
QUERY = OWNER_SCOPE + f"""
SELECT DISTINCT o.nip, o.ID_PLATNIK, o.person_id, r.KODTYTUB, r.PRDOEM, r.STNIEP, r.DATAOD, r.DATADO
FROM Owners o JOIN dbo.UBEZP_PODLEGANIE r ON r.ID_UBEZPIECZONY=o.person_id
WHERE {PERIOD_FILTER}
"""
OWNER_CANDIDATES = OWNER_SCOPE + f"""
SELECT o.nip, o.ID_PLATNIK, o.person_id, MIN(r.DATAOD), MAX(r.DATAOD), COUNT_BIG(*)
FROM Owners o JOIN dbo.UBEZP_PODLEGANIE r ON r.ID_UBEZPIECZONY=o.person_id
WHERE {PERIOD_FILTER}
GROUP BY o.nip, o.ID_PLATNIK, o.person_id
ORDER BY o.nip, o.ID_PLATNIK, o.person_id
"""


def scope_xml(nips):
    root = ElementTree.Element('p')
    for nip in sorted(nips):
        ElementTree.SubElement(root, 'n').text = normalize_nip(nip)
    return ElementTree.tostring(root, encoding='unicode', short_empty_elements=False)


def registry_available(cursor):
    cursor.execute("""SELECT t.name,c.name FROM sys.tables t
        JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.columns c ON c.object_id=t.object_id
        WHERE s.name='dbo' AND t.name IN ('PLATN_IDENT','UBEZPIECZONY','UBEZP_IDENT','UBEZP_PODLEGANIE')""")
    columns = {}
    for table, name in cursor.fetchall():
        columns.setdefault(table.upper(), set()).add(name.upper())
    return all(fields <= columns.get(table, set()) for table, fields in REQUIRED.items())


def fetch_periods(config, nips, *, expected_source=None):
    with sql_operation(config, 'Odczyt tytułów ubezpieczenia właścicieli'):
        connection = connect(config)
        try:
            cursor = connection.cursor()
            if expected_source is not None:
                from app.integrations.platnik.links import source_key
                if source_key(config, cursor) != expected_source:
                    raise ValueError('Źródło Płatnika zmieniło się podczas odczytu ubezpieczeń.')
            if not registry_available(cursor):
                return None
            cursor.execute(QUERY, scope_xml(nips))
            rows = cursor.fetchmany(100001)
            if len(rows) > 100000:
                raise ValueError('Odczyt tytułów ubezpieczenia przekracza 100000 pozycji.')
            return rows
        finally:
            connection.close()


def _date(value):
    if value in (None, ''):
        return None
    return date.fromisoformat(str(value)[:10])


def normalize_periods(rows, mapped, links=None):
    from app.companies.holiday_eligibility import insurance_code

    periods = {company_id: [] for company_id in mapped.values()}
    owners, errors = {}, {}
    links = links or {}
    for raw_nip, payer_id, person_id, base, pension, disability, begin, stop in rows:
        nip = normalize_nip(raw_nip)
        company_id = mapped.get(nip)
        if company_id is None or company_id in errors:
            continue
        if (link := links.get(nip)) and (payer_id != link['payer_id'] or
                                        (link['person_id'] is not None and person_id != link['person_id'])):
            continue
        if owners.setdefault(nip, (payer_id, person_id)) != (payer_id, person_id):
            errors[company_id] = 'InsuranceOwnerAmbiguous'
            continue
        suffix = ''.join(str(value).strip() for value in (pension, disability) if value is not None)
        try:
            code = insurance_code(base)
            start, end = _date(begin), _date(stop)
        except ValueError:
            errors[company_id] = 'InsurancePeriodInvalid'
            continue
        if len(code) == 4 and len(suffix) == 2 and suffix.isascii() and suffix.isdigit():
            code += suffix
        if end and end.year == 9999:
            end = None
        if not code or not start or start.year < 1900 or (end and end < start):
            errors[company_id] = 'InsurancePeriodInvalid'
            continue
        if end != start:
            periods[company_id].append({'nip': nip, 'code': code, 'from': start.isoformat(),
                                        'until': end.isoformat() if end else None})
    for nip, link in links.items():
        company_id = mapped.get(nip)
        if company_id is not None and link['person_id'] is not None and nip not in owners:
            errors.setdefault(company_id, 'InsuranceOwnerLinkStale')
    for company_id in errors:
        periods.pop(company_id)
    return periods, errors


def refresh(config, mapped, links=None, *, expected_source=None):
    rows = fetch_periods(config, mapped, **({'expected_source': expected_source} if expected_source is not None else {}))
    if rows is None:
        return 'unavailable', dict.fromkeys(mapped.values(), 'InsuranceRegistryUnavailable')
    periods, errors = normalize_periods(rows, mapped, links)
    stamp = datetime.now(ZoneInfo('Europe/Warsaw')).isoformat()
    with get_conn() as conn:
        for batch in batched(sorted(periods.items()), 100):
            save_snapshots(conn, SOURCE, [
                (company_id, '', encrypt_password(json.dumps({'imported_at': stamp, 'periods': values})))
                for company_id, values in batch
            ])
    mode = ('partial' if periods else 'failed') if errors else 'applied'
    return mode, errors


def company_registry_error(result, company_id):
    code = result.get('insurance_registry_errors', {}).get(company_id)
    return ERROR_MESSAGES.get(code)
