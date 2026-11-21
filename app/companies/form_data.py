"""The data for the company card and its individual secondary tabs."""

from app.remote.boundary import endpoint as office_endpoint

OPTION_FIELDS = ('forma_dzialalnosci', 'forma_opodatkowania', 'sposob_zaplaty', 'status_podatkowy')
CASH_DEFAULT_KEYS = ('eksport_kasowa', 'faktury_metoda_kasowa', 'domyslnie_metoda_kasowa')


def _field_options():
    from app.db.dao_companies import get_company_field_options
    from app.db.dao_conn import get_conn
    from app.remote.state import client

    if client() is not None:
        return {field: get_company_field_options(field) for field in OPTION_FIELDS}
    columns = [
        f"array_agg(DISTINCT {field} ORDER BY {field}) FILTER (WHERE TRIM({field}) <> '') AS {field}"
        for field in OPTION_FIELDS
    ]
    with get_conn() as conn:
        row = conn.execute('SELECT ' + ', '.join(columns) + ' FROM firmy').fetchone()
    return {field: row[field] or [] for field in OPTION_FIELDS}


def _mail_accounts(actor_id):
    from app.email.email_storage import get_all_email_accounts, get_email_accounts_for_employee
    from app.remote.state import client
    from app.security.authorization import is_administrator

    if client() is None and actor_id and not is_administrator(actor_id):
        accounts = get_email_accounts_for_employee(actor_id)
    else:
        accounts = get_all_email_accounts()
    return [{'id': account['id'], 'email': account['email']} for account in accounts]


def _mail_choices(account_id):
    from app.email.email_storage import get_known_sender_addresses
    from app.email.email_sync import selectable_folder_names

    return {'senders': get_known_sender_addresses(account_id) if account_id is not None else [],
            'folders': selectable_folder_names(account_id) if account_id is not None else []}


def read_section(company_id, actor_id, section, *, year=None, account_id=None):
    from app.db import dao_companies as dao

    if section == 'basic':
        from app.db.dao_app_settings import get_setting_bool
        company = dao.get_company(company_id) if company_id else None
        if company_id and not company:
            raise ValueError('Firma już nie istnieje.')
        return {
            'company': company,
            'options': _field_options(),
            'tax_offices': dao.get_tax_offices(),
            'employees': dao.get_employees_list(),
            'authorizations': dao.get_pelnomocnictwa(company_id) if company_id else {},
            'cash_default': get_setting_bool(CASH_DEFAULT_KEYS),
            'payroll_prices': dao.get_pricing_kadry(),
            'accounting_prices': dao.get_ksiegowosc_options(),
        }
    if section == 'people':
        return {'contacts': dao.get_contacts(company_id) if company_id else [],
                'partners': dao.get_partners(company_id) if company_id else []}
    if section == 'mail':
        from app.email.email_storage import get_email_filters_for_company
        accounts = _mail_accounts(actor_id)
        allowed = {account['id'] for account in accounts}
        return {
            'accounts': accounts,
            'filters': [row for row in get_email_filters_for_company(company_id)
                        if row.get('konto_id') in allowed] if company_id else [],
            'choices': _mail_choices(accounts[0]['id'] if accounts else None),
        }
    if section == 'mail_choices':
        if account_id not in {account['id'] for account in _mail_accounts(actor_id)}:
            raise PermissionError('Brak dostępu do konta pocztowego.')
        return _mail_choices(account_id)
    if section == 'filings':
        from app.companies.checking_obligations import load
        from app.db.dao_year_end import get_company_year_declarations
        if type(year) is not int or not 2000 <= year <= 2100:
            raise ValueError('Nieprawidłowy rok rozliczenia.')
        return {'monthly': load(company_id), 'year': year,
                'annual': get_company_year_declarations(company_id, year) if company_id else []}
    if section == 'notes':
        from app.db.dao_notes import get_notes_for_company
        return get_notes_for_company(company_id) if company_id else []
    raise ValueError('Nieznana zakładka karty firmy.')


@office_endpoint('app.companies.form_data.company_form_snapshot')
def company_form_snapshot(company_id, actor_id, section, *, year=None, account_id=None):
    return read_section(company_id, actor_id, section, year=year, account_id=account_id)
