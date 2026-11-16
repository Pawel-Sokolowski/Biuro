"""Insurance titles from the selected month's imported Płatnik documents."""
def read_codes(conn, company_ids, month):
    from app.integrations.platnik.declarations import decoded_snapshot
    keys = {f'platnik_declarations_{fid}_{month}':fid for fid in company_ids}
    result = {}
    for row in conn.execute('SELECT klucz,wartosc FROM app_settings WHERE klucz=ANY(%s)',(list(keys),)):
        snapshot = decoded_snapshot(row['wartosc'])
        entries = [entry for entry in snapshot.get('declarations',[]) if entry.get('insurance_code')]
        codes = sorted({entry['insurance_code'] for entry in entries})
        result[keys[row['klucz']]] = {
            'insurance_codes':'; '.join(codes),
            'insurance_details':'\n'.join(dict.fromkeys(
                f"{entry.get('employee_name') or 'Płatnik'} — {entry['insurance_code']} — {entry['form']} "
                f"(NIP {entry.get('payer_nip') or 'brak'}, dokument {entry.get('source_id') or '—'})" for entry in entries))
                + '\nOdczyt: '+str(snapshot.get('imported_at') or 'brak')}
    return result
