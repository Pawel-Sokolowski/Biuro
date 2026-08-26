import requests
from app.config import CEIDG_BASE_URL, CEIDG_API_KEY

def search_ceidg_by_nip(nip: str):
    nip = nip.replace("-", "").replace(" ", "")

    headers = {
        "Authorization": f"Bearer {CEIDG_API_KEY}"
    }

    url_search = f"{CEIDG_BASE_URL}/firmy"
    params = {
        "wlasciciel[nip]": nip,
        "limit": 1,
        "status[]": [
            "AKTYWNY",
            "WYKRESLONY",
            "ZAWIESZONY",
            "OCZEKUJE_NA_ROZPOCZECIE_DZIALALNOSCI",
            "WYLACZNIE_W_FORMIE_SPOLKI"
        ]
    }

    r = requests.get(url_search, params=params, headers=headers, timeout=10)
    print("CEIDG SEARCH STATUS:", r.status_code)
    print("CEIDG SEARCH JSON:", r.text)

    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "response": r.text}

    data = r.json()
    firmy = data.get("firmy", [])
    if not firmy:
        return {"error": "Brak wyników", "response": data}

    match = firmy[0]
    firm_id = match.get("id")

    url_firma = f"{CEIDG_BASE_URL}/firma/{firm_id}"
    r2 = requests.get(url_firma, headers=headers, timeout=10)

    if r2.status_code != 200:
        return {"error": f"HTTP {r2.status_code}", "response": r2.text}

    f = r2.json()

    adr = f.get("adresDzialalnosci", {})
    wl = f.get("wlasciciel", {})
    pkd_list = [p.get("kod") for p in f.get("pkd", []) if p.get("kod")]

    return {
        "nazwa": f.get("nazwa"),
        "nip": wl.get("nip"),
        "regon": wl.get("regon"),
        "miasto": adr.get("miasto"),
        "kod_pocztowy": adr.get("kod"),
        "ulica": adr.get("ulica"),
        "budynek": adr.get("budynek"),
        "lokal": adr.get("lokal"),
        "pkd": pkd_list,
        "status": f.get("status"),
        "data_rozpoczecia": f.get("dataRozpoczecia"),
        "raw": f
    }
