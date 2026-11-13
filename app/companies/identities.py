"""Explicit company/owner identities; an owner's tax ID never replaces the company NIP."""
from app.integrations.enova.document_counts import normalize_nip


def is_civil(company):
    value = ' '.join(str(company.get('forma_dzialalnosci') or '').strip().casefold().split())
    return value in ('spółka cywilna', 'spolka cywilna', 's.c.', 's.c', 'sc')


def payer_nips(company, partners=()):
    result = [normalize_nip(company['nip'])]
    if is_civil(company):
        for owner in partners:
            if str(owner.get('nip') or '').strip():
                nip = normalize_nip(owner['nip'])
                if nip not in result:
                    result.append(nip)
    return result


def company_recipients(company, partners=(), *, billing=False):
    from app.invoices.invoice_delivery import parse_recipients
    if is_civil(company):
        people = [p for p in partners if p.get('imie_pierwsze') or p.get('nazwisko')]
        if people:
            addresses = []
            for owner in people:
                # Missing partner contacts must not silently reduce a two-owner mailing to one person.
                addresses.extend(parse_recipients(owner.get('email')))
            return parse_recipients(','.join(addresses))
    value = company.get('email_faktury') if billing else None
    return parse_recipients(value or company.get('adres_email') or company.get('email'))
