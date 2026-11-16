"""Standard fields: ZUS Struktury danych osobowych 1.9, section 4.3.

Only identities and source periods; no PESEL, amounts or family member data.
"""
MONTHLY = {
    'DRA': {'table': 'ZUSDRA', 'period': 'I_2_2OKRESDEKLAR', 'report': 'I_2_1IDDEKLS'},
    **{code: {'table': 'ZUS' + code, 'period': 'I_1_2OKRROZL', 'report': 'I_1_1IDRAPS',
              'child': 'UBEZP_ZUS' + code, 'first': 'III_A_2_IMIEPIERW', 'last': 'III_A_1_NAZWISKO'}
       for code in ('RCA', 'RSA', 'RZA', 'RPA')},
    **{code: {'table': 'ZUS' + code, 'period': filled, 'kind': 'filled_date',
              'first': 'III_6_IMIEPIERW', 'last': 'III_5_NAZWISKO', 'employee': True}
       for code, filled in (('ZUA', 'XV_1_DATAWYPEL'), ('ZZA', 'XII_1_DATAWYPEL'),
                           ('ZWUA', 'VI_1_DATAWYPEL'), ('ZIUA', 'V_1_DATAWYPEL'), ('ZCNA', 'VI_1_DATAWYPEL'))},
}
ANNUAL = {
    'IWA': {'table': 'ZUSIWA', 'period': 'I_1_2OKRINFOR', 'report': 'I_1_1IDINFOR', 'kind': 'year'},
    'ZSWA': {'table': 'ZUSZSWA', 'period': 'I_1_2OKRESZGL', 'report': 'I_1_1IDZGLOSZ', 'kind': 'year',
             'child': 'UBEZP_ZUSZSWA', 'first': 'III_A_8_IMIEPIERW', 'last': 'III_A_7_NAZWISKO'},
}


def requirements(specs):
    result = {'DOKUMENT': {'ID_PLATNIK'}}
    if any(spec.get('child') for spec in specs.values()):
        result['DOKUMENT'].add('ID_DOK_NAD')
    for spec in specs.values():
        result[spec['table']] = {'ID_DOKUMENT', 'ID_PLATNIK', 'II_1_NIP', spec['period']}
        if spec.get('report'):
            result[spec['table']].add(spec['report'])
        if spec.get('employee'):
            result[spec['table']].update(('ID_UBEZPIECZONY', spec['first'], spec['last']))
        if spec.get('child'):
            result[spec['child']] = {'ID_DOKUMENT', 'ID_DOK_NAD', 'ID_PLATNIK', 'ID_UBEZPIECZONY',
                                     spec['first'], spec['last']}
    return result


def records_sql(specs, document_key, has_status, columns=None):
    """Identifiers come only from this shipped catalogue and a two-name key allowlist."""
    if document_key not in ('ID', 'ID_DOKUMENT'):
        raise ValueError('Nieprawidłowy klucz dokumentu Płatnika.')
    status = 'CONVERT(nvarchar(32),d.STATUSPT)' if has_status else 'CAST(NULL AS nvarchar(32))'
    def insurance(table, alias, prefix):
        names = [prefix+suffix for suffix in ('1KODTYTUB','2PRDOEM','3STNIEP')]
        available = (columns or {}).get(table,set())
        if names[0] not in available:
            return 'CAST(NULL AS nvarchar(6))'
        base = f'NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(6),{alias}.{names[0]}))),N\'\')'
        if set(names)<=available:
            suffix = ' + '.join(f'COALESCE(CONVERT(nvarchar(1),{alias}.{name}),N\'\')' for name in names[1:])
            return f'CASE WHEN LEN({base})=4 AND LEN({suffix})=2 THEN {base} + {suffix} ELSE {base} END'
        return base
    result = []
    for code, spec in specs.items():
        period = (f"CONVERT(nvarchar(10),a.{spec['period']},23)" if spec.get('kind') == 'filled_date'
                  else f"CONVERT(nvarchar(16),a.{spec['period']})")
        report = f"CONVERT(nvarchar(16),a.{spec['report']})" if spec.get('report') else 'CAST(NULL AS nvarchar(16))'
        employee_id = 'a.ID_UBEZPIECZONY' if spec.get('employee') else 'CAST(NULL AS int)'
        name = (f"LTRIM(RTRIM(COALESCE(a.{spec['first']},N'') + N' ' + COALESCE(a.{spec['last']},N'')))"
                if spec.get('employee') else 'CAST(NULL AS nvarchar(256))')
        code_expr = insurance(spec['table'],'a','XI_1_') if code=='DRA' else 'CAST(NULL AS nvarchar(6))'
        result.append(f"""SELECT N'ZUS {code}' AS form, a.ID_DOKUMENT AS source_id,
            a.ID_PLATNIK AS payer_id, a.II_1_NIP AS nip, {period} AS source_period,
            {report} AS report_id, {employee_id} AS employee_id, {name} AS employee_name,
            {status} AS raw_status,
            CASE WHEN d.{document_key} IS NOT NULL AND d.ID_PLATNIK=a.ID_PLATNIK THEN 1 ELSE 0 END AS valid_link,
            N'{spec.get('kind', 'month')}' AS period_kind, {code_expr} AS insurance_code
            FROM dbo.{spec['table']} a LEFT JOIN dbo.DOKUMENT d ON d.{document_key} = a.ID_DOKUMENT""")
        if spec.get('child'):
            child_code = insurance(spec['child'],'u','III_B_1_')
            child_status = 'CONVERT(nvarchar(32),child_d.STATUSPT)' if has_status else 'CAST(NULL AS nvarchar(32))'
            result.append(f"""SELECT N'ZUS {code}', u.ID_DOKUMENT, a.ID_PLATNIK, a.II_1_NIP,
                {period}, {report}, u.ID_UBEZPIECZONY,
                LTRIM(RTRIM(COALESCE(u.{spec['first']},N'') + N' ' + COALESCE(u.{spec['last']},N''))),
                {child_status}, CASE WHEN d.{document_key} IS NOT NULL AND d.ID_PLATNIK=a.ID_PLATNIK
                    AND u.ID_PLATNIK=a.ID_PLATNIK AND child_d.{document_key} IS NOT NULL
                    AND child_d.ID_PLATNIK=a.ID_PLATNIK AND child_d.ID_DOK_NAD=a.ID_DOKUMENT THEN 1 ELSE 0 END,
                N'{spec.get('kind', 'month')}', {child_code}
                FROM dbo.{spec['child']} u LEFT JOIN dbo.{spec['table']} a ON a.ID_DOKUMENT=u.ID_DOK_NAD
                LEFT JOIN dbo.DOKUMENT d ON d.{document_key} = a.ID_DOKUMENT
                LEFT JOIN dbo.DOKUMENT child_d ON child_d.{document_key} = u.ID_DOKUMENT""")
    return '\nUNION ALL\n'.join(result)
