"""Explicit company/owner identities; an owner's tax ID never replaces the company NIP."""
from app.utils.nip import normalize_nip


# Formy działalności, w których występują wspólnicy. JDG i „osoba fizyczna”
# mają właściciela, nie wspólników, więc sekcja Wspólnicy jest dla nich zbędna.
PARTNER_FORMS = ('spółka cywilna', 'spolka cywilna', 'spółka jawna', 'spolka jawna',
                 'spółka komandytowa', 'spolka komandytowa', 'spółka komandytowo-akcyjna',
                 'spolka komandytowo-akcyjna', 'spółka partnerska', 'spolka partnerska',
                 'spółka z o.o.', 'spółka z ograniczoną odpowiedzialnością', 'spółka akcyjna',
                 's.c.', 's.c', 'sc', 's.j.', 'sp.k.', 's.k.a.', 'sp. z o.o.', 's.a.')
CIVIL_FORMS = ('spółka cywilna', 'spolka cywilna', 's.c.', 's.c', 'sc')


def has_partners(company):
    """Czy firma może mieć wspólników (spółki), czy tylko właściciela (JDG)."""
    value = ' '.join(str(company.get('forma_dzialalnosci') or '').strip().casefold().split())
    if not value:
        # Brak formy nie może ukryć już wpisanych wspólników.
        return True
    return any(form in value for form in PARTNER_FORMS)


def is_civil(company):
    value = ' '.join(str(company.get('forma_dzialalnosci') or '').strip().casefold().split())
    return value in CIVIL_FORMS


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
        people = list(partners)
        if people:
            addresses = []
            for owner in people:
                # Missing partner contacts must not silently reduce a two-owner mailing to one person.
                addresses.extend(parse_recipients(owner.get('email')))
            return parse_recipients(','.join(addresses))
    value = company.get('email_faktury') if billing else None
    return parse_recipients(value or company.get('adres_email') or company.get('email'))
