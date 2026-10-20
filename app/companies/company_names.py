"""Helpers for consistent company display names."""
import re


_QUOTES = str.maketrans("", "", "\"'„”«»“”")


def clean_company_name(value):
    name = str(value or "").strip()
    if re.match(r"^[\"'„“«]?\s*firma\b", name, re.IGNORECASE) and re.search(
        r"\bsp\.?\s*z\s*o\.?\s*o\.?\b", name, re.IGNORECASE
    ):
        return name.translate(_QUOTES).strip()
    return name


def sole_proprietor_short_name(first_name, surname):
    first_name = str(first_name or "").strip()
    surname = str(surname or "").strip()
    if not first_name or not surname:
        return ""
    return f"{surname} {first_name}"
