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
STATIC_PDFS = {'01_terms': TEMPLATES / '01_terms.pdf'}
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
)


def company_context(company, partners=()):
    street = str(company.get('ulica') or '').strip()
    building = str(company.get('adres_budynek') or '').strip()
    apartment = str(company.get('adres_lokal') or '').strip()
    # Some legacy cards contain the building number inside the street field.
    if building and not street.endswith(building):
        street += ' ' + building
    if apartment:
        street += '/' + apartment
    locality = ' '.join(str(company.get(k) or '').strip() for k in ('kod_pocztowy', 'miasto')).strip()
    partner_data = []
    for partner in partners[:4]:
        p = dict(partner)
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
        'nip': company.get('nip') or '', 'krs': company.get('krs') or '', 'pesel': company.get('pesel') or '',
        'document_limit': '' if company.get('limit_dokumentow') is None else str(company['limit_dokumentow']),
        'extra_document_price': '' if company.get('cena_ponad_limit') is None else str(company['cena_ponad_limit']),
        'tax_office': company.get('urzad_skarbowy') or '',
        'representatives': [], 'partners': partner_data,
    }


def load_context(company_id, actor_id):
    from app.security.authorization import assigned_company_ids
    from app.db.dao_companies import get_company, get_partners
    if not actor_id or company_id not in assigned_company_ids(actor_id):
        raise PermissionError('Brak dostępu do firmy.')
    company = get_company(company_id)
    if not company:
        raise ValueError('Firma nie istnieje.')
    return company_context(company, get_partners(company_id))


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
    if key not in {key for key, _, _ in DOCUMENTS}:
        raise ValueError('Nieznany dokument.')
    document = Document(TEMPLATES / (key + '.docx'))
    if key in ('00_framework', '03_processing'):
        table = document.tables[0]
        for row, col, field in ((0, 1, 'name'), (1, 1, 'address'), (2, 1, 'phone'), (2, 3, 'email'),
                                (3, 1, 'nip'), (4, 1, 'krs'), (5, 1, 'pesel')):
            _cell(table.cell(row, col), data.get(field))
        for index, representative in enumerate(data.get('representatives', ())[:2]):
            for row, col, field in ((7, 1, 'imie_pierwsze'), (7, 3, 'nazwisko'), (8, 1, 'pesel'), (8, 3, 'nr_telefonu')):
                _cell(table.cell(row + index * 2, col), representative.get(field))
        for paragraph in document.paragraphs:
            if paragraph.text.startswith('Zawarta dnia'):
                _replace(paragraph, '_____________________' if key == '00_framework' else '09.06.2026', data['agreement_date'])
            if key == '03_processing' and 'Umową główną' in paragraph.text:
                _replace(paragraph, '09.02.2026', data.get('main_agreement_date') or data['agreement_date'])
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
    elif key == '04_checklist':
        for index, field in enumerate(('document_limit', 'extra_document_price', 'insured_limit', 'first_period')):
            _cell(document.tables[0].cell(index, 1), data.get(field))
        _cell(document.tables[1].cell(19, 1), data.get('tax_office'))
        for i, partner in enumerate(data.get('partners', ())[:4]):
            table = document.tables[i + 2]
            _cell(table.cell(0, 1), ' '.join(str(partner.get(k) or '') for k in ('imie_pierwsze', 'nazwisko')).strip())
            for row, field in ((1, 'adres_zamieszkania'), (2, 'nip'), (3, 'pesel')):
                _cell(table.cell(row, 2), partner.get(field))
    elif key == '02_prices':
        # The only client-specific price is the empty basic-fee value cell.
        if data.get('monthly_fee'):
            _cell(document.tables[0].cell(1, 1), data['monthly_fee'])
    elif key == '05_pep':
        for paragraph in document.paragraphs:
            if paragraph.text.startswith('Ja, niżej podpisany'):
                _replace(paragraph, '______________', data.get('pep_person') or '______________')
    elif key == '09_authorization':
        paragraphs = document.paragraphs
        _replace(paragraphs[1], '________________', data['agreement_date'])
        _replace(paragraphs[2], '___________________', data['name'])
        _replace(paragraphs[3], '___________________', data['address'])
        if data.get('authorized_person'):
            _replace(paragraphs[8], '_' * 82, data['authorized_person'])
    elif key == '10_termination':
        _cell(document.tables[0].cell(0, 1), data['name'])
        _cell(document.tables[0].cell(1, 1), data['address'])
        for paragraph in document.paragraphs:
            if paragraph.text.startswith('Umowy ramowej'):
                _replace(paragraph, '______________________', data.get('main_agreement_date') or data['agreement_date'])
            elif paragraph.text.startswith('Rozwiązanie umowy nastąpi') and data.get('termination_date'):
                _replace(paragraph, '_________________________', data['termination_date'])
            elif paragraph.text.startswith('Ostatni miesiąc') and data.get('last_period'):
                _replace(paragraph, '____________________________', data['last_period'])
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


def generate_pack(company_id, actor_id, overrides, selected, destination, *, extra_pdfs=(), duplex=False, docx_only=False):
    data = load_context(company_id, actor_id)  # Recheck access at execution time.
    data.update(overrides)
    known = {key for key, _, _ in DOCUMENTS}
    if not selected or not set(selected) <= known:
        raise ValueError('Wybierz dokumenty do wygenerowania.')
    if not str(data.get('name') or '').strip() or not str(data.get('address') or '').strip():
        raise ValueError('Uzupełnij pełną nazwę firmy i adres.')
    from datetime import datetime
    datetime.strptime(data.get('agreement_date', ''), '%d.%m.%Y')
    destination = Path(destination)
    if destination.resolve() in {Path(p).resolve() for p in extra_pdfs}:
        raise ValueError('Plik wynikowy musi być inny niż dołączone PDF-y.')
    # Staging on the destination filesystem permits atomic replacement.
    with tempfile.TemporaryDirectory(prefix='.biuro-komplet-', dir=destination.parent) as temp:
        staging = Path(temp)
        documents = []
        for key, _, _ in DOCUMENTS:
            if key in selected:
                path = staging / (key + '.docx')
                fill_template(key, data, path)
                documents.append(path)
        result = staging / ('komplet.zip' if docx_only else 'komplet.pdf')
        if docx_only:
            with ZipFile(result, 'w', ZIP_DEFLATED) as archive:
                for path in documents:
                    archive.write(path, path.name)
                for index, path in enumerate(extra_pdfs):
                    archive.write(path, f'zalacznik_{index + 1}_{Path(path).name}')
        else:
            dynamic = [path for path in documents if path.stem not in STATIC_PDFS]
            converted = convert_to_pdf(dynamic, staging / 'pdf') if dynamic else []
            by_key = {path.stem: path for path in converted}
            by_key.update(STATIC_PDFS)
            pdfs = [by_key[path.stem] for path in documents]
            merge_pdfs([*pdfs, *extra_pdfs], result, duplex=duplex)
        os.replace(result, destination)
    return str(destination)
