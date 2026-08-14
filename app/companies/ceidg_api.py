# app/integrations/ceidg.py
import logging
import time
from typing import Optional, Dict, Any, List

import requests

# Upewnij się, że importujesz z jednego, spójnego pliku config
from app.config import CEIDG_BASE_URL, CEIDG_API_KEY

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

# Priorytet statusów: niższy index = wyższy priorytet
STATUS_PRIORITY = ["AKTYWNY", "ZAWIESZONY", "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI",
                   "WYLACZNIE_W_FORMIE_SPOLKI", "WYKRESLONY"]


def _make_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "BiuroApp/1.0"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_json(url: str, params: Dict[str, Any], headers: Dict[str, str],
                  max_retries: int = 2, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Wykonuje GET i zwraca parsowany JSON lub None. Loguje status i fragment body."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("CEIDG request error (attempt %d): %s", attempt + 1, e)
            if attempt < max_retries:
                time.sleep(1 + attempt)
                continue
            return None

        logger.debug("CEIDG URL: %s", resp.url)
        logger.info("CEIDG Status: %s", resp.status_code)
        logger.debug("CEIDG Body (snippet): %s", resp.text[:1000])

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                logger.error("CEIDG: invalid JSON response")
                return None

        if resp.status_code == 401:
            logger.warning("CEIDG: Unauthorized (401). Check API key and environment (test/prod).")
            return None

        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
            # retryable server or rate-limit errors
            wait = 1 + attempt
            logger.info("CEIDG: retrying after %ds (status %d)", wait, resp.status_code)
            time.sleep(wait)
            continue

        # inne kody błędów — nie retryujemy
        logger.error("CEIDG: unexpected status %d", resp.status_code)
        return None

    return None


def _status_score(status: Optional[str]) -> int:
    if not status:
        return len(STATUS_PRIORITY)
    try:
        return STATUS_PRIORITY.index(status)
    except ValueError:
        return len(STATUS_PRIORITY)


def _choose_best_item(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Wybiera najlepszy wpis z listy: preferuj statusy wg priorytetu, potem nowszą datęRozpoczecia."""
    if not items:
        return None

    def key_fn(it: Dict[str, Any]):
        status = it.get("status")
        score = _status_score(status)
        date = it.get("dataRozpoczecia") or ""
        # chcemy, żeby nowsze daty były wyżej — odwracamy porządek przez prefiks '-'
        return (score, "-" + date)

    sorted_items = sorted(items, key=key_fn)
    return sorted_items[0]


def _fetch_firma_details(firma_link: str, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pobiera szczegóły firmy z podanego linku (endpoint /firma/{id})."""
    if not firma_link:
        return None
    headers = _make_headers(api_key)
    data = _request_json(firma_link, params={}, headers=headers)
    if isinstance(data, dict):
        return data
    return None


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Mapuje różne nazwy pól do ujednoliconego słownika wynikowego."""
    adres = item.get("adresDzialalnosci") or item.get("adres") or {}
    wlasciciel = item.get("wlasciciel") or {}
    kontakt = item.get("kontakt") or {}

    pkd_raw = item.get("pkd") or item.get("kody_pkd") or item.get("kodyPKD") or []
    pkd_list = []
    for p in pkd_raw:
        if isinstance(p, dict):
            code = p.get("kod") or p.get("code") or p.get("pkd")
            if code:
                pkd_list.append(code)
        elif isinstance(p, str):
            pkd_list.append(p)

    return {
        "id": item.get("id"),
        "nip": wlasciciel.get("nip") or item.get("nip"),
        "nazwa": item.get("nazwa") or item.get("nazwaFirmy"),
        "nazwa_skrocona": item.get("nazwaSkrocona") or item.get("nazwaSkrócona") or item.get("nazwa_skrocona"),
        "regon": wlasciciel.get("regon") or item.get("regon"),
        "adres_ulica": adres.get("ulica") or adres.get("Ulica") or adres.get("street"),
        "adres_budynek": adres.get("budynek") or adres.get("building"),
        "adres_lokal": adres.get("lokal") or adres.get("local"),
        "miasto": adres.get("miasto") or adres.get("city"),
        "kod_pocztowy": adres.get("kod") or adres.get("kodPocztowy") or adres.get("postalCode"),
        "wojewodztwo": adres.get("wojewodztwo"),
        "telefon": kontakt.get("telefon") or kontakt.get("phone") or item.get("telefon"),
        "email": kontakt.get("email") or kontakt.get("mail") or item.get("email"),
        "pkd": pkd_list,
        "status": item.get("status"),
        "data_rozpoczecia": item.get("dataRozpoczecia") or item.get("dataRozpoczecia"),
        "link": item.get("link"),
        "raw": item
    }


def search_ceidg_by_nip(nip: str,
                        api_key: Optional[str] = None,
                        base_url: Optional[str] = None,
                        limit: int = 25,
                        max_retries: int = 2) -> Optional[Dict[str, Any]]:
    """
    Szuka firmy w CEIDG po NIP i zwraca ujednolicony słownik z najlepszym wpisem.
    - api_key: JWT token (jeśli None, użyje CEIDG_API_KEY z config)
    - base_url: endpoint /firmy (np. test/prod)
    - limit: ile wpisów pobrać, żeby mieć wybór (domyślnie 25)
    """
    api_key = api_key or CEIDG_API_KEY
    url = base_url or CEIDG_BASE_URL
    nip_clean = str(nip).replace("-", "").replace(" ", "")
    params = {"nip": nip_clean, "limit": limit}
    headers = _make_headers(api_key)

    data = _request_json(url, params=params, headers=headers, max_retries=max_retries)
    if not data:
        return None

    # różne środowiska zwracają listę pod kluczem "firmy" lub "data" lub bez opakowania
    items = None
    if isinstance(data, dict):
        items = data.get("firmy") or data.get("data") or data.get("items") or data.get("results")
    elif isinstance(data, list):
        items = data

    if not items:
        logger.info("CEIDG: brak wyników dla NIP %s", nip_clean)
        return None

    best = _choose_best_item(items)
    if not best:
        return None

    # jeśli w podstawowym wpisie brakuje pól kontaktowych lub pkd, spróbuj pobrać szczegóły
    details = _fetch_firma_details(best.get("link") or f"{url.rstrip('/')}/firma/{best.get('id')}", api_key) or {}
    # scal: szczegóły mają wyższy priorytet
    merged = {**best, **details}
    normalized = _normalize_item(merged)
    return normalized


def search_ceidg_by_name(nazwa: str,
                         api_key: Optional[str] = None,
                         base_url: Optional[str] = None,
                         page_size: int = 10,
                         max_retries: int = 2) -> List[Dict[str, Any]]:
    """
    Szuka firm w CEIDG po nazwie. Zwraca listę ujednoliconych słowników.
    """
    api_key = api_key or CEIDG_API_KEY
    url = base_url or CEIDG_BASE_URL
    params = {"nazwa": nazwa, "limit": page_size}
    headers = _make_headers(api_key)

    data = _request_json(url, params=params, headers=headers, max_retries=max_retries)
    if not data:
        return []

    items = None
    if isinstance(data, dict):
        items = data.get("firmy") or data.get("data") or data.get("items") or data.get("results")
    elif isinstance(data, list):
        items = data

    if not items:
        return []

    results = []
    for it in items:
        # opcjonalnie pobierz szczegóły jeśli potrzebujesz pełnych danych (można odkomentować)
        # details = _fetch_firma_details(it.get("link"), api_key) or {}
        # merged = {**it, **details}
        normalized = _normalize_item(it)
        results.append(normalized)

    return results
