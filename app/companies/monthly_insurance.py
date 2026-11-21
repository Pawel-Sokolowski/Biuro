"""Insurance titles from the selected month's imported Płatnik documents."""
def read_codes(conn, company_ids, month):
    from app.integrations.platnik.declarations import decoded_snapshot
    result = {}
    for row in conn.execute("""
        SELECT company_id,payload FROM company_checking_snapshots
        WHERE source='platnik_declarations' AND company_id=ANY(%s) AND period=%s
    """, (list(company_ids), month)):
        snapshot = decoded_snapshot(row['payload'])
        entries = [entry for entry in snapshot.get('declarations',[]) if entry.get('insurance_code')]
        codes = sorted({entry['insurance_code'] for entry in entries})
        result[row['company_id']] = {
            'insurance_codes':'; '.join(codes),
            'insurance_details':'\n'.join(dict.fromkeys(
                f"{entry.get('employee_name') or 'Płatnik'} — {entry['insurance_code']} — {entry['form']} "
                f"(NIP {entry.get('payer_nip') or 'brak'}, dokument {entry.get('source_id') or '—'})" for entry in entries))
                + '\nOdczyt: '+str(snapshot.get('imported_at') or 'brak')}
    return result
