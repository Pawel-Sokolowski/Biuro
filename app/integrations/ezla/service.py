"""Official ZUS PobierzRaporty SOAP transport; no interactive PUE login."""
import base64
import binascii
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import PurePosixPath
import re
from lxml import etree
import requests
from app.integrations.ezla.report import parse_report, MAX_BYTES

ENDPOINT = ('https://pue.zus.pl:8500/ws/zus.channel.platnikRaportyZla:wsdlPlatnikRaportyZla/'
            'zus_channel_platnikRaportyZla_wsdlPlatnikRaportyZla_Port')
# Mandatory SOAP 1.1 action from the ZUS instruction effective 2026-02-13.
SOAP_ACTION = 'zus_channel_platnikRaportyZla_wsdlPlatnikRaportyZla_Binder_pobierzRaporty'
WSDL_URL = ENDPOINT + '?wsdl'
SOAP_NS = 'http://schemas.xmlsoap.org/soap/envelope/'
ZUS_NS = 'http://zus/zus.channel.platnikRaportyZla:wsdlPlatnikRaportyZla'
# Published transport credentials from the ZUS producer instruction; not payer credentials.
TRANSPORT_LOGIN = 'b2b_platnik_raporty_zla'
MAX_RESPONSE = 64 * 1024 * 1024
MAX_ARCHIVE = 20 * 1024 * 1024


class ServiceError(ValueError):
    def __init__(self, code, *, http_status=None):
        known = {'100', '101', '200', 'network', 'http', 'format', 'archive', 'payer', 'limit', 'read_failed', 'soap_client', 'soap_server', 'soap', 'tls', 'timeout'}
        http_code = re.fullmatch(r'http_([1-5][0-9]{2})', str(code))
        self.http_status = int(http_code[1]) if http_code else (http_status if type(http_status) is int and 100 <= http_status <= 599 else None)
        self.code = str(code) if str(code) in known or http_code else 'unknown'
        super().__init__({'100': 'ZUS: data pobierania jest starsza niż 30 dni.',
            '101': 'ZUS: nieprawidłowy login usługi, hasło lub NIP.',
            '200': 'ZUS: usługa chwilowo niedostępna.',
            'network': 'Nie udało się połączyć z usługą e-ZLA ZUS (HTTPS, port 8500).',
            'http': 'Usługa e-ZLA ZUS zwróciła błąd HTTP.',
            'soap_client': 'ZUS odrzucił żądanie SOAP. Wymaga sprawdzenia zgodności wywołania usługi.',
            'soap_server': 'ZUS zwrócił wewnętrzny błąd SOAP. Spróbuj ponownie później.',
            'soap': 'ZUS zwrócił błąd protokołu SOAP.',
            'tls': 'Nie udało się zweryfikować połączenia TLS z ZUS. Sprawdź datę serwera i zaufane certyfikaty; nie wyłączaj weryfikacji TLS.',
            'timeout': 'Przekroczono czas oczekiwania na usługę e-ZLA ZUS (HTTPS, port 8500).',
            'format': 'Nieprawidłowa odpowiedź usługi e-ZLA ZUS.',
            'archive': 'Nie udało się odczytać ZIP. Sprawdź osobne hasło szyfrowania raportów.',
            'payer': 'NIP pobranego raportu nie odpowiada firmie.',
            'read_failed': 'Nie udało się odczytać raportu. Sprawdź ustawienia, format XML i wspólny klucz.',
            'limit': 'Raporty ZUS przekraczają dopuszczalny rozmiar.'}.get(self.code, 'ZUS: nieobsługiwany kod odpowiedzi usługi.'))
        message = self.args[0]
        if http_code:
            message = {
                401: 'Odrzucono uwierzytelnienie HTTP usługi ZUS. To warstwa transportowa, nie weryfikacja hasła ZIP ani konta Biuro.',
                403: 'Dostęp do usługi ZUS odrzucony przez serwer lub bramę sieciową.',
                404: 'Nie znaleziono adresu usługi e-ZLA ZUS.',
                407: 'Serwer proxy wymaga uwierzytelnienia.',
                429: 'Usługa ZUS ograniczyła liczbę żądań. Odczekaj przed następną próbą.',
                500: 'Usługa ZUS lub brama zwróciła błąd serwera.',
                502: 'Brama sieciowa nie otrzymała poprawnej odpowiedzi od usługi ZUS.',
                503: 'Usługa ZUS jest chwilowo niedostępna.',
                504: 'Brama sieciowa przekroczyła czas oczekiwania na ZUS.',
            }.get(self.http_status, 'Usługa e-ZLA ZUS zwróciła błąd HTTP lub przekierowanie, którego nie wykonano.')
        if self.http_status:
            message = f'HTTP {self.http_status}: ' + message
        self.args = (message,)


@dataclass
class Download:
    generated_at: str
    xml: bytes


def _xml(data):
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, recover=False, remove_comments=True, remove_pis=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        raise ServiceError('format') from None
    if root.getroottree().docinfo.doctype or any(not isinstance(node.tag, str) for node in root.iter()):
        raise ServiceError('format')
    return root


def _children(node, name):
    return [child for child in node if etree.QName(child).localname == name]


def _one(node, name):
    values = _children(node, name)
    if len(values) != 1:
        raise ServiceError('format')
    return values[0]


def _value(node, name):
    value = _one(node, name)
    if len(value):
        raise ServiceError('format')
    return (value.text or '').strip()


def request_body(nip, login, password, since, today):
    if not re.fullmatch(r'[0-9]{10}', nip) or not login or not password:
        raise ValueError('Uzupełnij NIP, login i hasło usługi e-ZLA.')
    if not today - timedelta(days=30) <= since <= today:
        raise ValueError('Data pobierania e-ZLA musi mieścić się w ostatnich 30 dniach.')
    envelope = etree.Element(etree.QName(SOAP_NS, 'Envelope'), nsmap={'soapenv': SOAP_NS, 'zus': ZUS_NS})
    etree.SubElement(envelope, etree.QName(SOAP_NS, 'Header'))
    body = etree.SubElement(envelope, etree.QName(SOAP_NS, 'Body'))
    operation = etree.SubElement(body, etree.QName(ZUS_NS, 'PobierzRaporty'))
    for key, value in (('login', login), ('haslo', password), ('nip', nip), ('dataOd', since.isoformat())):
        etree.SubElement(operation, key).text = value
    return etree.tostring(envelope, encoding='utf-8', xml_declaration=True)


def decode_archive(data, password, nip):
    import pyzipper
    if len(data) > MAX_ARCHIVE:
        raise ServiceError('limit')
    try:
        with pyzipper.AESZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            candidates = [entry for entry in entries if not entry.is_dir()
                          and PurePosixPath(entry.filename).name.casefold() == 'raport.xml']
            if len(entries) > 20001 or len(candidates) != 1 or candidates[0].file_size > MAX_BYTES:
                raise ServiceError('format')
            with archive.open(candidates[0], pwd=password.encode('utf-8')) as stream:
                xml = stream.read(MAX_BYTES + 1)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError('archive') from None
    if len(xml) > MAX_BYTES:
        raise ServiceError('limit')
    parsed = parse_report(xml)
    if parsed.nip != nip:
        raise ServiceError('payer')
    return xml


def parse_response(data, zip_password, nip):
    root = _xml(data)
    if root.tag != '{' + SOAP_NS + '}Envelope':
        raise ServiceError('format')
    body = _one(root, 'Body')
    if _children(body, 'Fault'):
        fault = _one(body, 'Fault')
        values = _children(fault, 'faultcode')
        code = (values[0].text or '').strip().split(':')[-1] if len(values)==1 else ''
        raise ServiceError({'Client':'soap_client','Server':'soap_server'}.get(code,'soap'))
    response = _one(body, 'PobierzRaportyResponse')
    code = _value(response, 'kod')
    if code != '0':
        raise ServiceError(code)
    containers = _children(response, 'raporty')
    if len(containers) > 1:
        raise ServiceError('format')
    reports = _children(containers[0], 'raport') if containers else []
    if len(reports) > 100:
        raise ServiceError('limit')
    results, expanded = [], 0
    for report in reports:
        try:
            generated = date.fromisoformat(_value(report, 'dataWygenerowania')).isoformat()
            content = re.sub(r'\s', '', _value(report, 'zawartosc'))
            if len(content) > MAX_ARCHIVE * 4 // 3 + 4:
                raise ServiceError('limit')
            archive = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as error:
            if isinstance(error, ServiceError):
                raise
            raise ServiceError('format') from None
        xml = decode_archive(archive, zip_password, nip)
        expanded += len(xml)
        if expanded > MAX_RESPONSE:
            raise ServiceError('limit')
        results.append(Download(generated, xml))
    return results


def fetch_reports(nip, login, password, zip_password, since, *, today=None):
    today = today or date.today()
    payload = request_body(nip, login, password, since, today)
    if not zip_password:
        raise ValueError('Uzupełnij osobne hasło szyfrowania ZIP z ustawień PUE/eZUS.')
    try:
        with requests.post(ENDPOINT, data=payload, auth=(TRANSPORT_LOGIN, TRANSPORT_LOGIN),
                headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': '"' + SOAP_ACTION + '"'},
                timeout=(15, 90), allow_redirects=False, stream=True) as response:
            status = response.status_code
            if status != 200:
                # SOAP errors often arrive with HTTP 500. Read a bounded body to
                # classify them, never expose/log fault strings, HTML or headers.
                error_chunks, error_size = [], 0
                for chunk in response.iter_content(64 * 1024):
                    error_size += len(chunk)
                    if error_size > 1024 * 1024:
                        raise ServiceError(f'http_{status}')
                    error_chunks.append(chunk)
                data = b''.join(error_chunks)
                try:
                    root = _xml(data)
                    if root.tag != '{' + SOAP_NS + '}Envelope':
                        raise ServiceError('format')
                    body = _one(root, 'Body')
                    if _children(body, 'Fault'):
                        # Reuse the safe SOAP Fault classifier; it never decodes archives.
                        parse_response(data, zip_password, nip)
                    code = _value(_one(body, 'PobierzRaportyResponse'), 'kod')
                    if code in ('100','101','200'):
                        raise ServiceError(code)
                except ServiceError as error:
                    if error.code in ('100','101','200','soap_client','soap_server','soap'):
                        raise ServiceError(error.code,http_status=status) from None
                raise ServiceError(f'http_{status}')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise ServiceError('limit')
                chunks.append(chunk)
    except requests.exceptions.SSLError:
        raise ServiceError('tls') from None
    except requests.Timeout:
        raise ServiceError('timeout') from None
    except requests.RequestException:
        raise ServiceError('network') from None
    return parse_response(b''.join(chunks), zip_password, nip)
