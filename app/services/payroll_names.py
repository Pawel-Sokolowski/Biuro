"""Nazwy usług kadrowych używane przy tworzeniu pozycji faktur."""
import re


def payroll_invoice_service_name(contract_type: str, pricing_key: str, description: str) -> str:
    """Nazwij UoP/UZ według zakresu wybranego cennika, bez cen i kluczy.

    Opis cennika określa zakres usługi; klucz pomaga starszym wpisom bez opisu.
    Pozostałe rodzaje umów zachowują dotychczasowe nazewnictwo.
    """
    description = str(description or "").strip()
    contract = {"uop": "UoP", "uz": "UZ"}.get(contract_type)
    if contract is None:
        return description or "Usługa kadrowo-płacowa - Umowa o dzieło"

    service = "Usługa kadrowo-płacowa"
    for value in (description, pricing_key):
        text = str(value or "").casefold().replace("_", " ")
        if re.search(r"\bkadr\w*", text):
            break
        if re.search(r"\b(?:p[łl]ac\w*|payroll)\b", text):
            service = "Usługa płacowa"
            break
    return f"{service} – {contract}"
