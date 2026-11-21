"""Validate administrator-approved links against the live, read-only Płatnik catalogue."""
from collections import defaultdict
from hashlib import sha256
import json
import logging
import uuid

from app.companies.identities import is_civil, payer_nips
from app.db.dao_conn import get_conn, get_coordination_conn
from app.integrations.enova.document_counts import normalize_nip
from app.integrations.platnik.holidays import KEY as CONNECTION_SETTING, connect, load, sql_operation
from app.integrations.platnik.insurance import OWNER_CANDIDATES, payer_scope, registry_available, scope_xml
from app.remote.boundary import endpoint as office_endpoint

ERROR_MESSAGES = {
    'PlatnikPayerMissing': 'Brak kartoteki płatnika dla NIP w Płatniku.',
    'PlatnikPayerAmbiguous': 'Kilka kartotek płatnika ma ten sam NIP. Zatwierdź powiązanie w ustawieniach Płatnika.',
    'PlatnikLinkStale': 'Zatwierdzone powiązanie płatnika wymaga ponownej weryfikacji.',
}
PAYER_CANDIDATES = payer_scope() + """
SELECT DISTINCT nip, ID_PLATNIK FROM PayerIdentities WHERE ID_PLATNIK>0 ORDER BY nip, ID_PLATNIK
"""


def _require_admin(actor_id):
    from app.security.authorization import is_administrator
    if not is_administrator(actor_id):
        raise PermissionError('Tylko administrator zatwierdza powiązania Płatnika.')


def _source_settings(config):
    return tuple(str(config.get(name) or '').strip().casefold() for name in ('server', 'database'))


def source_key(config, cursor):
    row = cursor.execute("""SELECT DB_NAME(), CONVERT(varchar(36), service_broker_guid)
        FROM sys.databases WHERE database_id=DB_ID()""").fetchone()
    server, database = _source_settings(config)
    if not row or str(row[0]).casefold() != database or not row[1]:
        raise ValueError('Nie można zweryfikować tożsamości bazy Płatnika.')
    return sha256(json.dumps([server, database, str(row[1]).lower()]).encode()).hexdigest()


def catalogue(config, nips, *, include_owners=False):
    payers, owners = defaultdict(list), defaultdict(list)
    with sql_operation(config, 'Odczyt kartotek Płatnika'):
        connection = connect(config)
        try:
            cursor = connection.cursor()
            source = source_key(config, cursor)
            scope = scope_xml(nips)
            cursor.execute(PAYER_CANDIDATES, scope)
            rows = cursor.fetchmany(100001)
            if len(rows) > 100000:
                raise ValueError('Katalog płatników przekracza 100000 kartotek.')
            for nip, payer_id in rows:
                payers[nip].append(payer_id)
            available = include_owners and registry_available(cursor)
            if available:
                cursor.execute(OWNER_CANDIDATES, scope)
                rows = cursor.fetchmany(100001)
                if len(rows) > 100000:
                    raise ValueError('Katalog właścicieli przekracza 100000 kartotek.')
                for nip, payer_id, person_id, first, last, count in rows:
                    owners[nip, payer_id].append({
                        'person_id': person_id, 'first': str(first)[:10] if first else '',
                        'last': str(last)[:10] if last else '', 'count': count,
                    })
            return source, payers, owners, available
        finally:
            connection.close()


def _company_scopes(conn):
    rows = conn.execute('SELECT id,nip,nazwa,forma_dzialalnosci FROM firmy ORDER BY lower(nazwa),id').fetchall()
    civil_ids = [row['id'] for row in rows if is_civil(row)]
    partners = defaultdict(list)
    if civil_ids:
        for row in conn.execute('SELECT firma_id,nip FROM udzialowcy WHERE firma_id=ANY(%s)', (civil_ids,)):
            partners[row['firma_id']].append(row)
    memberships = defaultdict(set)
    scopes = []
    for row in rows:
        try:
            nips = payer_nips(row, partners[row['id']])
        except ValueError:
            nips = []
        scopes.append((row, nips))
        for nip in nips:
            memberships[nip].add(row['id'])
    return scopes, memberships


def _saved_links(conn, company_ids):
    return {(row['company_id'], row['payer_nip']): dict(row) for row in conn.execute("""
        SELECT company_id,payer_nip,source_key,payer_id,person_id,revision
        FROM platnik_approved_links WHERE company_id=ANY(%s)
    """, (sorted(set(company_ids)),))}


def import_links(config, mapped, *, annual=False):
    with get_conn() as conn:
        saved = _saved_links(conn, mapped.values())
    overrides = {nip: saved[(company_id, nip)] for nip, company_id in mapped.items() if (company_id, nip) in saved}
    errors = {company_id: 'PlatnikLinkStale' for company_id, nip in saved if mapped.get(nip) != company_id}
    if not mapped:
        return {}, {}, None
    custom = config.get('annual_declarations_query' if annual else 'declarations_query')
    checked = overrides if custom else mapped
    if not config.get('server'):
        errors.update({mapped[nip]: 'PlatnikLinkStale' for nip in overrides})
        return {}, errors, None
    if not checked:
        return {}, errors, None
    try:
        source, payers, owners, _ = catalogue(config, checked, include_owners=not annual)
    except (ValueError, RuntimeError):
        if annual:
            raise
        logging.getLogger(__name__).warning('Ponawiam odczyt kartotek Płatnika w zakresie płatników.')
        source, payers, owners, _ = catalogue(config, checked)
    links = {}
    for nip in checked:
        ids = payers.get(nip, ())
        company_id = mapped[nip]
        if company_id in errors:
            continue
        if approval := overrides.get(nip):
            if approval['source_key'] == source and approval['payer_id'] in ids:
                links[nip] = approval
            else:
                errors[company_id] = 'PlatnikLinkStale'
        elif len(ids) == 1:
            links[nip] = {'payer_id': ids[0], 'person_id': None}
        else:
            errors[company_id] = 'PlatnikPayerAmbiguous' if ids else 'PlatnikPayerMissing'
        if nip in links and not annual:
            link = links[nip]
            candidates = {row['person_id'] for row in owners.get((nip, link['payer_id']), ())}
            selected = link['person_id']
            if selected in candidates:
                links[nip] = {**link, 'contribution_owner_id': selected}
            elif selected is None and len(candidates) == 1:
                links[nip] = {**link, 'contribution_owner_id': candidates.pop()}
    return links, errors, source


def company_declaration_error(result, company_id):
    return ERROR_MESSAGES.get(result.get('declaration_link_errors', {}).get(company_id))


@office_endpoint('app.integrations.platnik.links.review')
def review(actor_id):
    _require_admin(actor_id)
    with get_conn() as conn:
        scopes, memberships = _company_scopes(conn)
        saved = _saved_links(conn, [row['id'] for row, _ in scopes])
    source, payers, owners, available = catalogue(load(), memberships, include_owners=True)
    saved_nips = defaultdict(set)
    for company_id, nip in saved:
        saved_nips[company_id].add(nip)
    rows = []
    for company, nips in scopes:
        company_id = company['id']
        for nip in sorted(set(nips) | saved_nips[company_id]) or ['']:
            approval = saved.get((company_id, nip))
            ids = payers.get(nip, ())
            current = approval and approval['source_key'] == source and approval['payer_id'] in ids
            choices = [{'payer_id': payer_id, 'owners': owners.get((nip, payer_id), [])} for payer_id in ids]
            can_approve = nip in nips and memberships[nip] == {company_id} and bool(ids)
            selected = approval['payer_id'] if current else (ids[0] if len(ids) == 1 else None)
            people = {owner['person_id'] for owner in owners.get((nip, selected), ())}
            if not nip or nip not in nips:
                status = 'Sprawdź NIP w kartotece Biuro'
            elif len(memberships[nip]) > 1:
                status = 'NIP należy do kilku firm w Biuro'
            elif approval and (not current or (approval['person_id'] is not None and approval['person_id'] not in people)):
                status = 'Wymaga ponownego zatwierdzenia'
            elif not ids:
                status = 'Brak kartoteki w Płatniku'
            elif selected is None:
                status = 'Wybierz kartotekę płatnika'
            elif len(people) > 1 and not (current and approval['person_id']):
                status = 'Wybierz kartotekę właściciela'
            else:
                status = 'Zatwierdzono' if current else 'Dopasowano po NIP'
            rows.append({'company_id': company_id, 'company': company['nazwa'], 'nip': nip,
                         'status': status, 'can_approve': can_approve, 'payers': choices,
                         'approval': approval, 'selected': selected})
    return {'source': source, 'rows': rows, 'registry_available': available}


@office_endpoint('app.integrations.platnik.links.save')
def save(actor_id, company_id, payer_nip, payer_id, person_id, source, expected_revision):
    _require_admin(actor_id)
    if type(company_id) is not int or company_id <= 0:
        raise ValueError('Wybierz firmę w Biuro.')
    nip = normalize_nip(payer_nip)
    if ((payer_id is not None and (type(payer_id) is not int or payer_id <= 0))
            or (person_id is not None and (type(person_id) is not int or person_id <= 0 or payer_id is None))):
        raise ValueError('Wybierz kartotekę z listy Płatnika.')
    with get_coordination_conn() as lock:
        if not lock.execute('SELECT pg_try_advisory_xact_lock(748220)').fetchone()[0]:
            raise ValueError('Trwa odczyt Płatnika. Ponów po jego zakończeniu.')
        if payer_id is not None:
            config = load()
            live, payers, owners, _ = catalogue(config, [nip], include_owners=person_id is not None)
            if live != source or payer_id not in payers.get(nip, ()):
                raise ValueError('Baza lub kartoteka płatnika zmieniła się. Odśwież powiązania.')
            if person_id is not None and person_id not in {row['person_id'] for row in owners.get((nip, payer_id), ())}:
                raise ValueError('Kartoteka właściciela zmieniła się. Odśwież powiązania.')
        with get_conn() as conn:
            if payer_id is not None:
                configured = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s FOR SHARE',
                                          (CONNECTION_SETTING,)).fetchone()
                if not configured or _source_settings(json.loads(configured[0])) != _source_settings(config):
                    raise ValueError('Połączenie Płatnika zmieniło się. Odśwież powiązania.')
                _, memberships = _company_scopes(conn)
                if memberships.get(nip) != {company_id}:
                    raise ValueError('NIP musi jednoznacznie należeć do wybranej firmy lub jej wspólnika.')
            old = _saved_links(conn, [company_id]).get((company_id, nip))
            if (old['revision'] if old else None) != expected_revision:
                raise ValueError('Powiązanie zostało zmienione. Odśwież listę.')
            if payer_id is None:
                if not old:
                    return
                conn.execute('DELETE FROM platnik_approved_links WHERE company_id=%s AND payer_nip=%s', (company_id, nip))
            else:
                conn.execute("""
                    INSERT INTO platnik_approved_links
                        (company_id,payer_nip,source_key,payer_id,person_id,revision,approved_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(company_id,payer_nip) DO UPDATE SET source_key=EXCLUDED.source_key,
                        payer_id=EXCLUDED.payer_id,person_id=EXCLUDED.person_id,revision=EXCLUDED.revision,
                        approved_by=EXCLUDED.approved_by,approved_at=now()
                """, (company_id, nip, source, payer_id, person_id, uuid.uuid4().hex, actor_id))
            previous = {key: old[key] for key in ('payer_id', 'person_id', 'source_key')} if old else None
            details = {'nip': nip, 'previous': previous, 'payer_id': payer_id, 'person_id': person_id}
            conn.execute("""INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly)
                VALUES ('platnik_approved_links',%s,%s,%s,%s)""",
                (company_id, 'approve' if payer_id is not None else 'revoke', actor_id, json.dumps(details)))
