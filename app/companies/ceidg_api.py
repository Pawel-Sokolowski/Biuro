# --- INTEGRACJA CEIDG API ---
import requests

from app.companies.importers import _get_cell, parse_num

CEIDG_API_KEY = "TWÓJ_KLUCZ_API"
CEIDG_BASE_URL = "https://api-prod.ceidg.gov.pl/api/v2"
def search_ceidg_by_nip(nip):
    """Szukaj firmy w CEIDG po NIP – API v2"""
    try:
        nip_clean = str(nip).replace('-', '').replace(' ', '')
        url = f"{CEIDG_BASE_URL}/entities"
        params = {"nip": nip_clean, "pageSize": 1}
        headers = {"X-API-Key": CEIDG_API_KEY, "Accept": "application/json"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        if not data.get("items"):
            return None
        item = data["items"][0]
        return {
            "nip": item.get("nip"),
            "nazwa": item.get("name"),
            "regon": item.get("regon"),
            "adres": item.get("address", {}).get("street"),
            "miasto": item.get("address", {}).get("city"),
            "kod_pocztowy": item.get("address", {}).get("postalCode"),
            "kody_pkd": [pkd.get("code") for pkd in item.get("pkdCodes", [])]
        }
    except Exception:
        return None


def search_ceidg_by_name(nazwa, page_size=10):
    """Szukaj firm w CEIDG po nazwie – API v2"""
    try:
        url = f"{CEIDG_BASE_URL}/entities"
        params = {"name": nazwa, "pageSize": page_size}
        headers = {"X-API-Key": CEIDG_API_KEY, "Accept": "application/json"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        companies = []
        for item in data.get("items", []):
            companies.append({
                "nip": item.get("nip"),
                "nazwa": item.get("name"),
                "regon": item.get("regon"),
                "adres": item.get("address", {}).get("street"),
                "miasto": item.get("address", {}).get("city"),
                "kod_pocztowy": item.get("address", {}).get("postalCode"),
                "kody_pkd": [pkd.get("code") for pkd in item.get("pkdCodes", [])]
            })
        return companies
    except Exception:
        return []

