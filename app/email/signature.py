from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any, Dict, Optional, Tuple

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn

DEFAULT_TEMPLATE: Dict[str, str] = {
    "stopka_szablon_pozdrowienie": "Pozdrawiam",
    "stopka_szablon_firma": "Biuro Rachunkowe EM-KA Sp. z o.o.",
    "stopka_szablon_adres": "ul. Chopina 35\n71-450 Szczecin",
    "stopka_szablon_telefon": "123456789",
    "stopka_szablon_email": "biuro@szczecin-ksiegowosc.pl",
    "stopka_szablon_zastrzezenie": (
        "Wiadomość, której częścią są również załączniki jest przeznaczona tylko dla jej "
        "adresata. Dostęp osób trzecich do tej wiadomości jest zabroniony. Jeśli nie jest "
        "Pan/i adresatem niniejszej wiadomości, informujemy, że jej rozpowszechnianie, "
        "kopiowanie, rozprowadzanie lub inne działanie o podobnym charakterze jest "
        "zabronione i może być nielegalne. Prosimy o poinformowanie nadawcy oraz usunięcie "
        "wiadomości bez otwierania załączników."
    ),
}
SEPARATOR = "----------------------"
DEFAULT_SENDER_KEY = "email_konto_wysylki_id"


def get_template() -> Dict[str, str]:
    template = {}
    for key, default in DEFAULT_TEMPLATE.items():
        value = get_setting(key, None)
        template[key] = default if value is None else value
    return template


def save_template(values: Dict[str, str]) -> None:
    for key in DEFAULT_TEMPLATE:
        if key in values:
            set_setting(key, values[key])


def build_default_signature(
    imie: str, nazwisko: str, template: Optional[Dict[str, str]] = None
) -> Tuple[str, str]:
    """Zwraca (tekst, html) domyślnej stopki wypełnionej imieniem i nazwiskiem pracownika."""
    t = template or get_template()
    name = f"{imie or ''} {nazwisko or ''}".strip()
    address = [l.strip() for l in t["stopka_szablon_adres"].splitlines() if l.strip()]

    blocks = [["--", t["stopka_szablon_pozdrowienie"], name]]
    company = [t["stopka_szablon_firma"], *address]
    if t["stopka_szablon_telefon"].strip():
        company.append(f"tel: {t['stopka_szablon_telefon'].strip()}")
    blocks.append([l for l in company if l])
    if t["stopka_szablon_email"].strip():
        blocks.append([t["stopka_szablon_email"].strip()])
    blocks[0] = [l for l in blocks[0] if l]

    lines = []
    for block in blocks:
        lines.extend(block)
        lines.append("")
    lines.append(SEPARATOR)
    disclaimer = t["stopka_szablon_zastrzezenie"].strip()
    if disclaimer:
        lines.extend([disclaimer, SEPARATOR])
    text = "\n".join(lines)
    return text, text_to_html(text)


def text_to_html(text: str) -> str:
    if not text:
        return ""
    paragraphs = []
    for chunk in text.replace("\r\n", "\n").split("\n\n"):
        paragraphs.append("<p style='margin:0 0 8px 0;'>" + escape(chunk).replace("\n", "<br>") + "</p>")
    return "".join(paragraphs)


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    text = unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_body_fragment(html: str) -> str:
    """Z pełnego dokumentu HTML zostawia zawartość <body>, żeby dało się ją wstawić do wiadomości."""
    match = re.search(r"<body[^>]*>(.*)</body>", html or "", re.S | re.I)
    return match.group(1).strip() if match else (html or "").strip()


def load_signature_file(path: str) -> Tuple[str, str]:
    """Wczytuje stopkę z pliku .html/.htm/.txt; zwraca (tekst, html)."""
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if path.lower().endswith((".html", ".htm")):
        fragment = extract_body_fragment(content)
        return html_to_text(fragment), fragment
    return content.strip(), text_to_html(content.strip())


def get_employee_signature(pracownik_id: int) -> Tuple[str, str]:
    """Stopka pracownika; gdy jej nie ma - domyślna wygenerowana z imienia i nazwiska."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT imie, nazwisko, stopka_tekst, stopka_html FROM pracownicy WHERE id = ?",
            (pracownik_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return "", ""
    text, html = row["stopka_tekst"] or "", row["stopka_html"] or ""
    if html.strip() or text.strip():
        return text or html_to_text(html), html or text_to_html(text)
    return build_default_signature(row["imie"], row["nazwisko"])


def get_saved_signature(pracownik_id: int) -> Tuple[str, str]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT stopka_tekst, stopka_html FROM pracownicy WHERE id = ?", (pracownik_id,)
        ).fetchone()
    finally:
        conn.close()
    return (row["stopka_tekst"] or "", row["stopka_html"] or "") if row else ("", "")


def save_employee_signature(pracownik_id: int, text: str, html: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE pracownicy SET stopka_tekst = ?, stopka_html = ? WHERE id = ?",
            (text or None, html or None, pracownik_id),
        )
        conn.commit()
    finally:
        conn.close()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def has_signature(body_text: str, signature_text: str) -> bool:
    sig = _norm(signature_text)
    return bool(sig) and sig in _norm(body_text)


QUOTE_MARKER = "<p><br></p>"


def merge_signature(
    html: str, body: str, signature_html: str, signature_text: str
) -> str:
    """Zwraca HTML z dopisaną stopką (przed cytatem odpowiedzi/przekazania) albo "" gdy nic nie trzeba zmieniać."""
    if not (signature_html or "").strip():
        return ""
    current_text = html_to_text(html) if html else (body or "")
    if has_signature(current_text, signature_text or html_to_text(signature_html)):
        return ""
    fragment = extract_body_fragment(signature_html)
    block = f"{QUOTE_MARKER}<div>{fragment}</div>"
    if html:
        if html.startswith(QUOTE_MARKER):
            return block + html[len(QUOTE_MARKER):]
        return html + block
    return text_to_html(body) + block if body else block


def default_sender_id() -> Optional[int]:
    value = get_setting(DEFAULT_SENDER_KEY, None)
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def set_default_sender_id(konto_id: Optional[int]) -> None:
    set_setting(DEFAULT_SENDER_KEY, str(konto_id) if konto_id else "")


def choose_sender(accounts: list, current: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Konto do wysyłki: domyślne biurowe (jeśli pracownik ma do niego dostęp), inaczej bieżące/pierwsze."""
    wanted = default_sender_id()
    for acc in accounts:
        if wanted and acc["id"] == wanted:
            return acc
    return current or (accounts[0] if accounts else None)
