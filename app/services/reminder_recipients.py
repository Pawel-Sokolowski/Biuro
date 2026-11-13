"""Recipient selection shared by manual reminders and the background worker."""
from app.invoices.invoice_delivery import parse_recipients


def select_recipient(*, company_email=None, billing_email=None, invoice_email=None,
                     contractor_email=None, contact_emails=(), payments=False):
    # Current billing instructions take precedence over the invoice's old snapshot.
    preferred = (billing_email, invoice_email, company_email, contractor_email) if payments else ()
    for value in preferred:
        if value and str(value).strip():
            return {'email': str(value).strip(), 'recipient_choices': []}
    contacts = {}
    for value in contact_emails or ():
        try:
            for address in parse_recipients(value):
                contacts.setdefault(address.casefold(), address)
        except ValueError:
            continue
    choices = list(contacts.values())
    if not choices and company_email and str(company_email).strip():
        return {'email':str(company_email).strip(),'recipient_choices':[]}
    # Never expand an automatic mailing to everyone in a company's address book.
    return {'email': choices[0] if len(choices) == 1 else '',
            'recipient_choices': choices if len(choices) > 1 else []}
