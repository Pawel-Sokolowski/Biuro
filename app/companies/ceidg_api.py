import os

import requests

from app.utils.env_config import load_environment

load_environment()

CEIDG_API_KEY = os.getenv("CEIDG_API_KEY")
CEIDG_BASE_URL = os.getenv("CEIDG_BASE_URL")


def get_ceidg_firma_by_nip(nip: str):
    url = f"{CEIDG_BASE_URL}/firma"
    params = {"nip": nip}
    headers = {"Authorization": f"Bearer {CEIDG_API_KEY}"}

    r = requests.get(url, params=params, headers=headers, timeout=10)
    print("CEIDG FIRMA STATUS:", r.status_code)
    print("CEIDG FIRMA JSON:", r.text)

    if r.status_code != 200:
        return {"error": True}

    data = r.json()
    firma_list = data.get("firma", [])
    if not firma_list:
        return {"error": True}

    # wybór wpisu aktywnego
    f = None
    for firma in firma_list:
        if firma.get("status") == "AKTYWNY":
            f = firma
            break

    # jeśli brak aktywnego → wybierz najnowszy po dacie rozpoczęcia
    if f is None:
        f = sorted(
            firma_list,
            key=lambda x: x.get("dataRozpoczecia", "1900-01-01"),
            reverse=True
        )[0]

    dz = f.get("adresDzialalnosci") or {}
    ko = f.get("adresKorespondencyjny") or {}

    # jeśli CEIDG nie ma adresu działalności → kopiuj korespondencyjny
    if not dz.get("miasto"):
        dz = {
            "miasto": ko.get("miasto", ""),
            "kod": ko.get("kod", ""),
            "ulica": ko.get("ulica", ""),
            "budynek": ko.get("budynek", ""),
            "lokal": ko.get("lokal", ""),
            "wojewodztwo": ko.get("wojewodztwo", ""),
            "powiat": ko.get("powiat", ""),
            "gmina": ko.get("gmina", ""),
            "kraj": ko.get("kraj", "")
        }

    pkd_list = f.get("pkd", []) or []
    pkd_codes = [p.get("kod") for p in pkd_list if p.get("kod")]

    return {
        "id": f.get("id"),
        "nazwa": f.get("nazwa"),
        "nip": f.get("wlasciciel", {}).get("nip"),
        "regon": f.get("wlasciciel", {}).get("regon"),
        "pkd": pkd_codes,
        "data_rozpoczecia": f.get("dataRozpoczecia"),

        "dz_miasto": dz.get("miasto", ""),
        "dz_kod": dz.get("kod", ""),
        "dz_ulica": dz.get("ulica", ""),
        "dz_budynek": dz.get("budynek", ""),
        "dz_lokal": dz.get("lokal", ""),
        "dz_wojewodztwo": dz.get("wojewodztwo", ""),
        "dz_powiat": dz.get("powiat", ""),
        "dz_gmina": dz.get("gmina", ""),
        "dz_kraj": dz.get("kraj", ""),

        "koresp_miasto": ko.get("miasto", ""),
        "koresp_kod": ko.get("kod", ""),
        "koresp_ulica": ko.get("ulica", ""),
        "koresp_budynek": ko.get("budynek", ""),
        "koresp_lokal": ko.get("lokal", ""),
        "koresp_wojewodztwo": ko.get("wojewodztwo", ""),
        "koresp_powiat": ko.get("powiat", ""),
        "koresp_gmina": ko.get("gmina", ""),
        "koresp_kraj": ko.get("kraj", ""),

        "telefon": f.get("telefon", ""),
        "email": f.get("email", ""),
        "www": f.get("www", ""),
        "adres_doreczen_elektronicznych": f.get("adresDoreczenElektronicznych", "")
    }
