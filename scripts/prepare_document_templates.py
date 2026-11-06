"""One-time conversion of the reviewed source DOCX fields to named Jinja tags.

Run after reviewing a replacement source document's structure; never on export.
This keeps geometry knowledge in template preparation, outside production filling.
"""
from pathlib import Path
from docx import Document
from app.documents.contract_pack import TEMPLATES, _cell, _replace


def prepare():
    for path in TEMPLATES.glob('*.docx'):
        doc = Document(path)
        if any('{{' in t.text for t in doc.element.iter() if t.tag.endswith('}t') and t.text):
            continue
        key = path.stem
        if key in ('00_framework', '03_processing'):
            table = doc.tables[0]
            for row, col, name in ((0,1,'name'),(1,1,'address'),(2,1,'phone'),(2,3,'email'),(3,1,'nip'),(4,1,'krs'),(5,1,'pesel')):
                _cell(table.cell(row,col), '{{ '+name+' }}')
            for i in range(2):
                for row,col,name in ((7,1,'imie_pierwsze'),(7,3,'nazwisko'),(8,1,'pesel'),(8,3,'nr_telefonu')):
                    _cell(table.cell(row+i*2,col), '{{ representative_'+str(i+1)+'.'+name+' }}')
            original = table.cell(6,0).text
            _cell(table.cell(6,0), '{% if representative_roles %}zwanym dalej Zleceniodawcą, reprezentowanym przez:\n{{ representative_roles }}{% else %}'+original+'{% endif %}')
            for p in doc.paragraphs:
                if p.text.startswith('Zawarta dnia'):
                    _replace(p, '_____________________' if key=='00_framework' else '09.06.2026', '{{ agreement_date }}')
                if key=='03_processing' and 'Umową główną' in p.text:
                    _replace(p,'09.02.2026','{{ main_agreement_date or agreement_date }}')
        elif key=='04_checklist':
            for i,name in enumerate(('document_limit','extra_document_price','insured_limit','first_period')):
                _cell(doc.tables[0].cell(i,1),'{{ '+name+' }}')
            _cell(doc.tables[1].cell(19,1),'{{ tax_office }}')
            for i in range(4):
                table=doc.tables[i+2]
                _cell(table.cell(0,1),'{{ partner_'+str(i+1)+'.name }}')
                for row,col,name in ((1,2,'adres_zamieszkania'),(2,2,'nip'),(3,2,'pesel')):
                    _cell(table.cell(row,col),'{{ partner_'+str(i+1)+'.'+name+' }}')
        elif key=='02_prices':
            _cell(doc.tables[0].cell(1,1),'{{ monthly_fee }}')
        elif key=='05_pep':
            for p in doc.paragraphs:
                if p.text.startswith('Ja, niżej podpisany'):_replace(p,'______________','{{ pep_person or "______________" }}')
        elif key=='09_authorization':
            for i,old,new in ((1,'________________','agreement_date'),(2,'___________________','name'),(3,'___________________','address')):
                _replace(doc.paragraphs[i],old,'{{ '+new+' }}')
            _replace(doc.paragraphs[8],'_'*82,'{{ authorized_person or "'+'_'*82+'" }}')
        elif key=='10_termination':
            _cell(doc.tables[0].cell(0,1),'{{ name }}');_cell(doc.tables[0].cell(1,1),'{{ address }}')
            for p in doc.paragraphs:
                if p.text.startswith('Umowy ramowej'):_replace(p,'______________________','{{ main_agreement_date or agreement_date }}')
                elif p.text.startswith('Rozwiązanie umowy nastąpi'):_replace(p,'_________________________','{{ termination_date or "_________________________" }}')
                elif p.text.startswith('Ostatni miesiąc'):_replace(p,'____________________________','{{ last_period or "____________________________" }}')
        else:
            continue
        doc.save(path)

if __name__=='__main__':prepare()
