"""Link existing IMAP folders to clients by unambiguous names or tax IDs."""
from collections import defaultdict
from email.utils import getaddresses
import re
import unicodedata

from app.db.dao_conn import get_conn
from app.email.email_helpers import decode_mailbox_utf7, folder_role


def normalized(value):
    value = unicodedata.normalize('NFKD',str(value or '').casefold().replace('ł','l'))
    return ''.join(c for c in value if c.isalnum())


def link_account(konto_id, company_ids=None):
    """No folder creation, fuzzy matching, or replacement of manual rules."""
    with get_conn() as conn:
        account = conn.execute('SELECT pracownik_id FROM konta_email WHERE id=%s '
                               'AND COALESCE(aktywne,1)=1 AND COALESCE(systemowe,0)=0',(konto_id,)).fetchone()
        if not account:
            return 0
        companies = [dict(r) for r in conn.execute('SELECT id,nazwa,nazwa_skrocona,nip,adres_email FROM firmy')]
        contacts = list(conn.execute('SELECT firma_id,email FROM company_contacts'))
        partners = list(conn.execute('SELECT firma_id,email,imie_pierwsze,nazwisko FROM udzialowcy'))
        folders = list(conn.execute('SELECT server_name,display_name,delimiter,attributes FROM foldery_email WHERE konto_id=%s',(konto_id,)))
        aliases, targets, addresses = defaultdict(set),defaultdict(set),defaultdict(set)
        for company in companies:
            for label in (company['nazwa'],company['nazwa_skrocona'],company['nip']):
                if normalized(label):
                    aliases[normalized(label)].add(company['id'])
            addresses[company['id']].add(company['adres_email'] or '')
        for person in partners:
            label = normalized(' '.join(filter(None,(person['imie_pierwsze'],person['nazwisko']))))
            if label:
                aliases[label].add(person['firma_id'])
        for person in (*contacts,*partners):
            addresses[person['firma_id']].add(person['email'] or '')
        owners = defaultdict(set)
        for company_id, values in addresses.items():
            for _,address in getaddresses([v.replace(';',',') for v in values]):
                owners[address.casefold()].add(company_id)
        for folder in folders:
            if folder_role(folder['server_name'],folder['attributes'],folder['delimiter']) or '\\noselect' in (folder['attributes'] or '').casefold():
                continue
            decoded = decode_mailbox_utf7(folder['server_name'])
            leaf = decoded.split(folder['delimiter'])[-1] if folder['delimiter'] else decoded
            matches = set()
            for name in (decoded,leaf,folder['display_name']):
                matches.update(aliases.get(normalized(name),()))
            if len(matches)==1:
                targets[next(iter(matches))].add(folder['server_name'])
        conn.execute('SELECT pg_advisory_xact_lock(748224,%s)',(konto_id,))
        count = 0
        for company_id, names in targets.items():
            if len(names)!=1 or (company_ids is not None and company_id not in company_ids):
                continue
            target = next(iter(names))
            for _,address in getaddresses([v.replace(';',',') for v in addresses[company_id]]):
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',address):
                    continue
                if len(owners[address.casefold()])!=1:
                    continue
                # A manual address rule always wins, including a disabled rule.
                exists = conn.execute('SELECT 1 FROM reguly_filtrow WHERE konto_id=%s AND '
                    '(lower(trim(COALESCE(od,\'\')))=lower(%s) OR lower(trim(COALESCE("do",\'\')))=lower(%s))',
                    (konto_id,address,address)).fetchone()
                if exists:
                    continue
                conn.execute('INSERT INTO reguly_filtrow(pracownik_id,konto_id,firma_id,od,folder_docelowy,aktywna,usun) '
                             'VALUES (%s,%s,%s,%s,%s,1,0)',(account[0],konto_id,company_id,address.lower(),target))
                count += 1
        return count
