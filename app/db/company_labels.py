"""SQL expressions for company names shown in employee views."""

from app.companies.identities import CIVIL_FORMS


def short_company_name_sql(alias):
    return f"COALESCE(NULLIF(TRIM({alias}.nazwa_skrocona), ''), TRIM({alias}.nazwa))"


def company_label_sql(alias, name=None):
    """Use company IDs to detect name collisions and include only named civil partners."""
    name = name or short_company_name_sql(alias)
    other_name = short_company_name_sql('other_company')
    civil_forms = ', '.join(f"'{form}'" for form in CIVIL_FORMS)
    partner_name = "TRIM(partner.imie_pierwsze) || ' ' || TRIM(partner.nazwisko)"
    return f"""({name} || COALESCE(
        CASE WHEN regexp_replace(lower(trim({alias}.forma_dzialalnosci)), '[[:space:]]+', ' ', 'g') IN ({civil_forms})
              AND EXISTS (
                  SELECT 1 FROM firmy other_company
                  WHERE other_company.id <> {alias}.id AND lower({other_name}) = lower({name})
              )
             THEN (
                 SELECT ' — ' || string_agg(DISTINCT {partner_name}, ', ' ORDER BY {partner_name})
                 FROM udzialowcy partner
                 WHERE partner.firma_id = {alias}.id
                   AND NULLIF(TRIM(partner.imie_pierwsze), '') IS NOT NULL
                   AND NULLIF(TRIM(partner.nazwisko), '') IS NOT NULL
             ) END, ''))"""
