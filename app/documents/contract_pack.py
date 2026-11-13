"""Fill the supplied office templates and produce a single printable PDF.

Word/LibreOffice perform pagination and PDF export; python-docx preserves the
templates rather than rebuilding their layout in HTML. No document is sent.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from pypdf import PdfReader, PdfWriter

TEMPLATES = Path(__file__).with_name('templates')
STATIC_PDFS = {}
DOCUMENTS = (
    ('00_framework', 'Umowa ramowa', True),
    ('01_terms', 'Ogólne warunki świadczenia usług', True),
    ('02_prices', 'Cennik', True),
    ('03_processing', 'Umowa powierzenia danych', True),
    ('04_checklist', 'Oświadczenia i lista kontrolna', True),
    ('05_pep', 'Oświadczenie PEP', True),
    ('06_ksef', 'Uzgodnienie obsługi KSeF', True),
    ('07_gdpr', 'Klauzula RODO', True),
    ('09_authorization', 'Upoważnienie do odbioru dokumentów', True),
    ('10_termination', 'Wypowiedzenie umowy', False),
    ('11_upl', 'UPL-1(6) — formularz do druku', False),
    ('12_pel', 'PEL — formularz do druku (3 strony)', False),
    ('13_pel_z', 'PEL-Z — załącznik z przedstawicielami', False),
)


def company_context(company, partners=(), contacts=()):
    from app.documents.power_of_attorney import split_street, principal_kind_for_company
    structured_street = split_street(company.get('ulica'))
    street = str(company.get('ulica') or '').strip()
    building = str(company.get('adres_budynek') or '').strip()
    apartment = str(company.get('adres_lokal') or '').strip()
    # Some legacy cards contain the building number inside the street field.
    if building and not street.endswith(building):
        street += ' ' + building
    if apartment:
        street += '/' + apartment
    locality = ' '.join(str(company.get(k) or '').strip() for k in ('kod_pocztowy', 'miasto')).strip()
    from app.companies.identities import is_civil
    partner_data = []
    for partner in partners[:4]:
        p = dict(partner)
        if is_civil(company) and not p.get('stanowisko'):
            p['stanowisko'] = 'Wspólnik'
        address = ' '.join(str(p.get(k) or '').strip() for k in ('ulica', 'nr_domu'))
        if p.get('nr_mieszkania'):
            address += '/' + str(p['nr_mieszkania'])
        p['adres_zamieszkania'] = ', '.join(v for v in (address.strip(),
            ' '.join(str(p.get(k) or '').strip() for k in ('kod_pocztowy', 'miejscowosc')).strip()) if v)
        partner_data.append(p)
    return {
        'name': company.get('nazwa') or '',
        'address': ', '.join(p for p in (street.strip(), locality) if p),
        'phone': company.get('nr_telefonu') or '', 'email': company.get('adres_email') or '',
        'nip': company.get('nip') or '', 'krs': company.get('krs') or '', 'pesel': company.get('pesel') or (partner_data[0].get('pesel') if len(partner_data) == 1 and principal_kind_for_company(company) == 'person' else '') or '',
        'document_limit': '' if company.get('limit_dokumentow') is None else str(company['limit_dokumentow']),
        'extra_document_price': '' if company.get('cena_ponad_limit') is None else str(company['cena_ponad_limit']),
        'tax_office': company.get('urzad_skarbowy') or '',
        'short_name': company.get('nazwa_skrocona') or '', 'regon': company.get('regon') or '',
        'principal_kind': principal_kind_for_company(company),
        'street': structured_street['street'] if not building or structured_street['building'] == building else company.get('ulica') or '',
        'building': building or structured_street['building'], 'apartment': apartment or structured_street['apartment'],
        'postcode': company.get('kod_pocztowy') or '', 'city': company.get('miasto') or '',
        'country': 'PL' if str(company.get('kraj') or 'PL').casefold() in ('pl', 'polska', 'poland') else company['kraj'],
        'region': company.get('wojewodztwo') or '', 'county': company.get('powiat') or '', 'municipality': company.get('gmina') or '',
        'civil_partnership': is_civil(company),
        'principal_birth_date': partner_data[0].get('data_urodzenia', '') if len(partner_data) == 1 and principal_kind_for_company(company) == 'person' else '',
        'principal_first_name': partner_data[0].get('imie_pierwsze', '') if len(partner_data) == 1 and principal_kind_for_company(company) == 'person' else '',
        'principal_surname': partner_data[0].get('nazwisko', '') if len(partner_data) == 1 and principal_kind_for_company(company) == 'person' else '',
        'representatives': [], 'partners': partner_data,
        'representative_candidates': [*partner_data, *[
            dict(imie_pierwsze=c.get('imie') or '', nazwisko=c.get('nazwisko') or '',
                 telefon=c.get('telefon') or '', stanowisko=c.get('rola') or '') for c in contacts]],
    }


def office_context():
    from app.db.dao_app_settings import get_setting
    from app.documents.power_of_attorney import office_email
    keys = {'office_name': 'nazwa', 'office_nip': 'nip', 'office_city': 'miasto', 'office_street': 'adres',
            'office_phone': 'telefon', 'office_representative': 'reprezentant'}
    result = {name: str(get_setting('faktury_sprzedawca_' + key) or '') for name, key in keys.items()}
    result['office_postal'] = ' '.join(str(get_setting('faktury_sprzedawca_' + key) or '') for key in ('kod', 'miasto')).strip()
    result['office_address'] = ', '.join(v for v in (result['office_street'], result['office_postal']) if v)
    result['office_email'] = office_email()
    return result


def load_context(company_id, actor_id):
    from app.security.authorization import assigned_company_ids
    from app.db.dao_companies import get_company, get_partners, get_contacts
    if not actor_id or company_id not in assigned_company_ids(actor_id):
        raise PermissionError('Brak dostępu do firmy.')
    company = get_company(company_id)
    if not company:
        raise ValueError('Firma nie istnieje.')
    return {**company_context(company, get_partners(company_id), get_contacts(company_id)), **office_context()}


def _cell(cell, text):
    """Write only the designated value cell, retaining its paragraph properties."""
    paragraph = cell.paragraphs[0]
    for child in list(paragraph._p):
        if child.tag != qn('w:pPr'):
            paragraph._p.remove(child)
    run = paragraph.add_run(str(text or ''))
    run.font.size = Pt(10)  # Matches the supplied contract tables.


def _replace(paragraph, old, new):
    """Replace a literal across split runs without discarding shapes or styling."""
    nodes = paragraph._p.xpath('.//w:t')
    while True:
        text = ''.join(n.text or '' for n in nodes)
        start = text.find(old)
        if start < 0:
            return
        end, offset = start + len(old), 0
        inserted = False
        for node in nodes:
            value = node.text or ''
            left, right = max(start - offset, 0), min(end - offset, len(value))
            if right > left:
                replacement = str(new) if not inserted else ''
                node.text = value[:left] + replacement + value[right:]
                node.set(qn('xml:space'), 'preserve')
                inserted = True
            offset += len(value)
        if old in str(new):
            return


def _check(element, selected):
    # Word's content-control checkbox stores both a state and its printed glyph.
    for control in element.iter(qn('w:sdt')):
        checked = control.find('.//' + qn('w14:checked'))
        if checked is None:
            continue
        checked.set(qn('w14:val'), '1' if selected else '0')
        content = control.find(qn('w:sdtContent'))
        if content is not None:
            for node in content.iter(qn('w:t')):
                if node.text in ('☐', '☒', '☑'):
                    node.text = '☒' if selected else '☐'


def fill_template(key, data, destination):
    if key not in {key for key, _, _ in DOCUMENTS if key not in ('11_upl', '12_pel', '13_pel_z')}:
        raise ValueError('Nieznany dokument.')
    from io import BytesIO
    from docxtpl import DocxTemplate
    context = {name: '' for name in ('name', 'address', 'phone', 'email', 'nip', 'krs', 'pesel', 'monthly_fee',
        'agreement_date', 'main_agreement_date', 'document_limit', 'extra_document_price', 'insured_limit',
        'first_period', 'tax_office', 'pep_person', 'authorized_person', 'termination_date', 'last_period')}
    context.update({key: '' for key in ('office_name','office_nip','office_city','office_street','office_phone','office_representative','office_postal','office_address','office_email')})
    context.update(data)
    representatives = list(data.get('representatives') or ())
    for index in range(2):
        context[f'representative_{index+1}'] = representatives[index] if index < len(representatives) else {}
    roles = []
    for person in representatives[:2]:
        name = ' '.join(str(person.get(k) or '').strip() for k in ('imie_pierwsze','nazwisko')).strip()
        if name and person.get('stanowisko'):
            roles.append(name + ' — ' + person['stanowisko'])
    context['representative_roles'] = '; '.join(roles)
    partners = list(data.get('partners') or ())
    for index in range(4):
        partner = dict(partners[index]) if index < len(partners) else {}
        partner['name'] = ' '.join(str(partner.get(k) or '') for k in ('imie_pierwsze','nazwisko')).strip()
        context[f'partner_{index+1}'] = partner
    template = DocxTemplate(TEMPLATES / (key + '.docx'))
    template.render(context, autoescape=True)
    rendered = BytesIO()
    template.save(rendered)
    rendered.seek(0)
    document = Document(rendered)
    if key == '00_framework':
        accounting = data.get('accounting', '')
        choices = {'Usług księgowych': bool(accounting),
                   'Ewidencji do celów ryczałtu': accounting == 'ryczalt',
                   'Podatkowej Księgi': accounting == 'kpir', 'Ksiąg Rachunkowych': accounting == 'ksiegi',
                   'Obsługi kadrowo-płacowej': bool(data.get('payroll')),
                   'Usług płacowych': data.get('payroll') == 'place',
                   'Pełnych usług kadrowo-płacowych': data.get('payroll') == 'kadry',
                   'Wystawiania faktur': bool(data.get('invoicing')), 'Generowania przelewów': bool(data.get('transfers'))}
        for paragraph in document.paragraphs:
            for prefix, selected in choices.items():
                if paragraph.text.strip().startswith(prefix):
                    _check(paragraph._p, selected)
    document.save(destination)


WORD_EXPORT = r'''
$ErrorActionPreference = 'Stop'
$jobs = Get-Content -LiteralPath $args[0] -Raw -Encoding UTF8 | ConvertFrom-Json
$word = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.UpdateLinksAtOpen = $false
    foreach ($job in $jobs) {
        $doc = $null
        try {
            $doc = $word.Documents.Open([string]$job.source, $false, $true, $false)
            $doc.ExportAsFixedFormat([string]$job.target, 17)
        } finally {
            if ($null -ne $doc) { $doc.Close(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) }
        }
    }
} finally {
    if ($null -ne $word) { $word.Quit(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
}
'''


def convert_to_pdf(paths, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    office = shutil.which('libreoffice') or shutil.which('soffice')
    if not office and os.name == 'nt':
        for env in ('PROGRAMFILES', 'PROGRAMFILES(X86)'):
            candidate = Path(os.environ.get(env, '')) / 'LibreOffice/program/soffice.exe'
            if candidate.is_file():
                office = str(candidate)
                break
    if os.name == 'nt':
        # Keep paths in JSON, never interpolate company data into executable code.
        script = output_dir / 'word-export.ps1'
        manifest = output_dir / 'word-jobs.json'
        script.write_text(WORD_EXPORT, encoding='utf-8-sig')
        manifest.write_text(json.dumps([{'source': str(Path(p).resolve()),
            'target': str((output_dir / (Path(p).stem + '.pdf')).resolve())} for p in paths]), encoding='utf-8')
        try:
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'RemoteSigned',
                            '-File', str(script), str(manifest)], check=True, capture_output=True, timeout=240,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            if not office:
                raise RuntimeError('Nie udało się utworzyć PDF w programie Word. Zainstaluj Word lub LibreOffice albo zapisz komplet DOCX.') from None
            # Word might have exported only a prefix before failing.
            for path in output_dir.glob('*.pdf'):
                path.unlink()
        else:
            return _verify_pdfs(paths, output_dir)
    if not office:
        raise RuntimeError('Do zachowania układu dokumentów potrzebny jest Word (Windows) lub LibreOffice. Możesz też zapisać komplet DOCX.')
    profile = (output_dir / 'lo-profile').resolve().as_uri()
    try:
        subprocess.run([office, '-env:UserInstallation=' + profile, '--headless', '--convert-to',
                        'pdf:writer_pdf_Export', '--outdir', str(output_dir), *map(str, paths)],
                       check=True, capture_output=True, timeout=240)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Konwersja dokumentów w LibreOffice nie powiodła się. Spróbuj zapisać komplet DOCX.') from None
    return _verify_pdfs(paths, output_dir)


def _verify_pdfs(paths, output_dir):
    result = [output_dir / (Path(p).stem + '.pdf') for p in paths]
    for path in result:
        if not path.is_file() or not PdfReader(path).pages:
            raise RuntimeError('Nie powstał poprawny PDF dokumentu: ' + path.stem)
    return result


def merge_pdfs(paths, destination, duplex=False):
    writer = PdfWriter()
    try:
        for index, path in enumerate(paths):
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError('Załączony PDF jest zabezpieczony hasłem.')
            pages = list(reader.pages)
            if not pages:
                raise ValueError('Załączony PDF nie zawiera stron.')
            for page in pages:
                writer.add_page(page)
            if duplex and index < len(paths) - 1 and len(pages) % 2:
                writer.add_blank_page(width=float(pages[-1].mediabox.width), height=float(pages[-1].mediabox.height))
        with open(destination, 'wb') as stream:
            writer.write(stream)
    finally:
        writer.close()

def generate_document(
    company_id,
    actor_id,
    overrides,
    document_key,
    destination,
    *,
    form_renderer=None,
):
    return generate_pack(
        company_id=company_id,
        actor_id=actor_id,
        overrides=overrides,
        selected=[document_key],
        destination=destination,
        extra_pdfs=(),
        duplex=False,
        docx_only=False,
        form_renderer=form_renderer,
    )

def generate_framework_agreement(
    company_id,
    actor_id,
    overrides,
    destination,
    *,
    form_renderer=None,
):
    return generate_document(
        company_id,
        actor_id,
        overrides,
        '00_framework',
        destination,
        form_renderer=form_renderer,
    )


def generate_upl(
    company_id,
    actor_id,
    overrides,
    destination,
    *,
    form_renderer=None,
):
    return generate_document(
        company_id,
        actor_id,
        overrides,
        '11_upl',
        destination,
        form_renderer=form_renderer,
    )


def generate_pel(
    company_id,
    actor_id,
    overrides,
    destination,
    *,
    form_renderer=None,
):
    return generate_document(
        company_id,
        actor_id,
        overrides,
        '12_pel',
        destination,
        form_renderer=form_renderer,
    )


def generate_pel_z(
    company_id,
    actor_id,
    overrides,
    destination,
    *,
    form_renderer=None,
):
    return generate_document(
        company_id,
        actor_id,
        overrides,
        '13_pel_z',
        destination,
        form_renderer=form_renderer,
    )

def generate_pack(company_id, actor_id, overrides, selected, destination, *, extra_pdfs=(), duplex=False, docx_only=False, form_renderer=None):
    data = load_context(company_id, actor_id)  # Recheck access at execution time.
    data.update(overrides)
    known = {key for key, _, _ in DOCUMENTS}
    if not selected or not set(selected) <= known:
        raise ValueError('Wybierz dokumenty do wygenerowania.')
    if not str(data.get('name') or '').strip() or not str(data.get('address') or '').strip():
        raise ValueError('Uzupełnij pełną nazwę firmy i adres.')
    from datetime import datetime
    datetime.strptime(data.get('agreement_date', ''), '%d.%m.%Y')
    form_keys = [key for key, _, _ in DOCUMENTS if key in selected and key in ('11_upl', '12_pel', '13_pel_z')]
    if form_keys:
        from app.documents.power_of_attorney import reviewed_data, resolve_employee, split_street, formatted_address
        candidates = data.get('attorneys') or [data.get('attorney') or {}]
        employee_ids = [row.get('employee_id') for row in candidates]
        if not employee_ids or len(candidates) > 100 or len(set(employee_ids)) != len(employee_ids):
            raise ValueError('Zaznacz różnych pracowników (maks. 100).')
        resolved_attorneys = [resolve_employee(dict(data, attorney=person), actor_id) for person in candidates]
        data['attorney'] = resolved_attorneys[0]
        if not data.get('building'):
            data.update(split_street(data.get('street')))
        if not data.get('postcode') or not data.get('city'):
            raise ValueError('Uzupełnij kod pocztowy i miejscowość klienta w danych do pełnomocnictwa.')
        data['address'] = formatted_address(data)
        for key in form_keys:
            if key == '11_upl':
                for person in resolved_attorneys:
                    reviewed_data(dict(data, attorney=person), 'UPL-PAPER')
        data['pel_z_attached'] = '13_pel_z' in form_keys
        if form_renderer is None:
            raise ValueError('Generowanie PDF pełnomocnictw wymaga mechanizmu wydruku aplikacji.')
    destination = Path(destination)
    if destination.resolve() in {Path(p).resolve() for p in extra_pdfs}:
        raise ValueError('Plik wynikowy musi być inny niż dołączone PDF-y.')
    # Staging on the destination filesystem permits atomic replacement.
    with tempfile.TemporaryDirectory(prefix='.biuro-komplet-', dir=destination.parent) as temp:
        staging = Path(temp)
        documents = []
        for key, _, _ in DOCUMENTS:
            if key in selected and key not in form_keys:
                path = staging / (key + '.docx')
                fill_template(key, data, path)
                documents.append(path)
        result = staging / ('komplet.zip' if docx_only else 'komplet.pdf')
        form_pdfs = []
        for person in resolved_attorneys if form_keys else []:
            person_data = dict(data, attorney=person)
            for key in form_keys:
                pdf = staging / (key + '_pracownik_' + str(person['employee_id']) + '.pdf')
                if key == '11_upl':
                    from app.documents.upl_paper import write_pdf
                    write_pdf(person_data, pdf, form_renderer)
                elif key == '13_pel_z':
                    from app.documents.pel_z import write_pdf
                    write_pdf(person_data, pdf, form_renderer)
                else:
                    from app.documents.pel_paper import write_pdf
                    write_pdf(person_data, pdf, form_renderer)
                if not pdf.is_file() or not PdfReader(pdf).pages:
                    raise RuntimeError('Nie powstał poprawny PDF pełnomocnictwa.')
                form_pdfs.append(pdf)
        if docx_only:
            with ZipFile(result, 'w', ZIP_DEFLATED) as archive:
                for path in documents:
                    archive.write(path, path.name)
                for pdf in form_pdfs:
                    archive.write(pdf, pdf.name)
                for index, path in enumerate(extra_pdfs):
                    archive.write(path, f'zalacznik_{index + 1}_{Path(path).name}')
        else:
            dynamic = [path for path in documents if path.stem not in STATIC_PDFS]
            converted = convert_to_pdf(dynamic, staging / 'pdf') if dynamic else []
            by_key = {path.stem: path for path in converted}
            by_key.update(STATIC_PDFS)
            pdfs = [by_key[path.stem] for path in documents]
            pdfs.extend(form_pdfs)
            merge_pdfs([*pdfs, *extra_pdfs], result, duplex=duplex)
        os.replace(result, destination)
    return str(destination)
