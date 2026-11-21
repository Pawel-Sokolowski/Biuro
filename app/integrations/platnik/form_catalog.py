"""Standard fields from ZUS Struktury danych osobowych, section 4.3.

Only identities and source periods; no PESEL, amounts or family member data.
"""
MONTHLY = {
    'DRA': {'table': 'ZUSDRA', 'period': 'I_2_2OKRESDEKLAR', 'report': 'I_2_1IDDEKLS',
            'header_insurance': 'XI_1_'},
    **{code: {'table': 'ZUS' + code, 'period': 'I_1_2OKRROZL', 'report': 'I_1_1IDRAPS',
              'aliases': ({'report': ('I_1_11DRAPS',)} if code == 'RPA' else
                          {'period': ('I_1_2OKKROZL',)}),
              'child': 'UBEZP_ZUS' + code, 'first': 'III_A_2_IMIEPIERW', 'last': 'III_A_1_NAZWISKO',
              'insurance_prefix': 'III_A_5_' if code == 'RPA' else 'III_B_1_'}
       for code in ('RCA', 'RSA', 'RZA', 'RPA')},
    **{code: {'table': 'ZUS' + code, 'period': filled, 'kind': 'filled_date',
              'first': 'III_6_IMIEPIERW', 'last': 'III_5_NAZWISKO', 'employee': True,
              'header_insurance': insurance}
       for code, filled, insurance in (
           ('ZUA', 'XV_1_DATAWYPEL', 'V_1_'), ('ZZA', 'XII_1_DATAWYPEL', 'V_1_'),
           ('ZWUA', 'VI_1_DATAWYPEL', 'IV_1_'), ('ZIUA', 'V_1_DATAWYPEL', None), ('ZCNA', 'VI_1_DATAWYPEL', None),
       )},
}
ADDITIONAL_MONTHLY = {
    **{code: {'table': 'ZUS' + code, 'period': filled, 'kind': 'filled_date'}
       for code, filled in (('ZAA', 'IX_1_DATAWYPEL'), ('ZBA', 'IX_1_DATAWYPEL'), ('ZFA', 'XI_3_DATAWYPEL'),
                            ('ZPA', 'IX_3_DATAWYPEL'), ('ZWPA', 'IV_1_DATAWYPEL'))},
    'ZIPA': {'table': 'ZUSZIPA', 'period': 'IV_1_DATAWYPEL', 'kind': 'filled_date', 'current_nip': 'III_1_NIP'},
    'ZCZA': {**MONTHLY['ZCNA'], 'table': 'ZUSZCZA', 'period': 'VIII_1_DATAWYPEL'},
    'RIA': {'table': 'ZUSRIA', 'period': 'XII_1_DATAWYPEL', 'kind': 'filled_date',
            'first': 'III_2_IMIEPIERW', 'last': 'III_1_NAZWISKO', 'employee': True},
    'OSW': {'table': 'ZUSOSW', 'period': 'III_1_DATAWYPEL', 'kind': 'filled_date',
            'nip': 'I_1_NIP', 'aliases': {'nip': ('II_1_NIP',)}},
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
        result[spec['table']] = {'ID_DOKUMENT', 'ID_PLATNIK', spec.get('nip', 'II_1_NIP'), spec['period']}
        if spec.get('report'):
            result[spec['table']].add(spec['report'])
        if spec.get('current_nip'):
            result[spec['table']].add(spec['current_nip'])
        if spec.get('employee'):
            result[spec['table']].update(('ID_UBEZPIECZONY', spec['first'], spec['last']))
        if spec.get('child'):
            result[spec['child']] = {'ID_DOKUMENT', 'ID_DOK_NAD', 'ID_PLATNIK', 'ID_UBEZPIECZONY',
                                     spec['first'], spec['last']}
    return result


def resolve_fields(specs, columns):
    resolved = {}
    for code, spec in specs.items():
        available = columns.get(spec['table'], set())
        resolved[code] = {
            **spec,
            **{field: next((name for name in (spec[field], *aliases) if name in available), spec[field])
               for field, aliases in spec.get('aliases', {}).items()},
        }
    return resolved


def records_sql(specs, document_key, has_status, columns=None):
    """Identifiers come only from this shipped catalogue and a two-name key allowlist."""
    if document_key not in ('ID', 'ID_DOKUMENT'):
        raise ValueError('Nieprawidłowy klucz dokumentu Płatnika.')
    status = 'CONVERT(nvarchar(32),d.STATUSPT)' if has_status else 'CAST(NULL AS nvarchar(32))'
    payer_identity_visible = {'ID_PLATNIK', 'NIP'} <= (columns or {}).get('PLATN_IDENT', set())
    def insurance(table, alias, prefix):
        if not prefix:
            return 'CAST(NULL AS nvarchar(6))'
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
        nip = f"a.{spec.get('nip', 'II_1_NIP')}"
        if spec.get('current_nip'):
            nip = f"COALESCE(NULLIF(LTRIM(RTRIM(a.{spec['current_nip']})),N''),{nip})"
        period = (f"CONVERT(nvarchar(10),a.{spec['period']},23)" if spec.get('kind') == 'filled_date'
                  else f"CONVERT(nvarchar(16),a.{spec['period']})")
        report = f"CONVERT(nvarchar(16),a.{spec['report']})" if spec.get('report') else 'CAST(NULL AS nvarchar(16))'
        employee_id = 'a.ID_UBEZPIECZONY' if spec.get('employee') else 'CAST(NULL AS int)'
        name = (f"LTRIM(RTRIM(COALESCE(a.{spec['first']},N'') + N' ' + COALESCE(a.{spec['last']},N'')))"
                if spec.get('employee') else 'CAST(NULL AS nvarchar(256))')
        code_expr = insurance(spec['table'], 'a', spec.get('header_insurance'))
        result.append(f"""SELECT N'ZUS {code}' AS form, a.ID_DOKUMENT AS source_id,
            a.ID_PLATNIK AS payer_id, {nip} AS nip, {period} AS source_period,
            {report} AS report_id, {employee_id} AS employee_id, {name} AS employee_name,
            {status} AS raw_status,
            CASE WHEN d.{document_key} IS NOT NULL AND d.ID_PLATNIK=a.ID_PLATNIK THEN 1 ELSE 0 END AS valid_link,
            N'{spec.get('kind', 'month')}' AS period_kind, {code_expr} AS insurance_code
            FROM dbo.{spec['table']} a LEFT JOIN dbo.DOKUMENT d ON d.{document_key} = a.ID_DOKUMENT""")
        if spec.get('child'):
            child_code = insurance(spec['child'], 'u', spec.get('insurance_prefix', 'III_B_1_'))
            child_status = 'CONVERT(nvarchar(32),child_d.STATUSPT)' if has_status else 'CAST(NULL AS nvarchar(32))'
            child_nip, orphan_join = nip, ''
            if payer_identity_visible:
                child_nip = f'COALESCE({nip}, orphan.NIP)'
                orphan_join = """
                LEFT JOIN (SELECT DISTINCT ID_PLATNIK,NIP FROM dbo.PLATN_IDENT
                           WHERE NULLIF(LTRIM(RTRIM(NIP)),N'') IS NOT NULL) orphan
                    ON orphan.ID_PLATNIK=u.ID_PLATNIK AND a.ID_DOKUMENT IS NULL"""
            result.append(f"""SELECT N'ZUS {code}', u.ID_DOKUMENT, COALESCE(a.ID_PLATNIK,u.ID_PLATNIK), {child_nip},
                {period}, {report}, u.ID_UBEZPIECZONY,
                LTRIM(RTRIM(COALESCE(u.{spec['first']},N'') + N' ' + COALESCE(u.{spec['last']},N''))),
                {child_status}, CASE WHEN d.{document_key} IS NOT NULL AND d.ID_PLATNIK=a.ID_PLATNIK
                    AND u.ID_PLATNIK=a.ID_PLATNIK AND child_d.{document_key} IS NOT NULL
                    AND child_d.ID_PLATNIK=a.ID_PLATNIK AND child_d.ID_DOK_NAD=a.ID_DOKUMENT THEN 1 ELSE 0 END,
                N'{spec.get('kind', 'month')}', {child_code}
                FROM dbo.{spec['child']} u LEFT JOIN dbo.{spec['table']} a ON a.ID_DOKUMENT=u.ID_DOK_NAD
                LEFT JOIN dbo.DOKUMENT d ON d.{document_key} = a.ID_DOKUMENT
                LEFT JOIN dbo.DOKUMENT child_d ON child_d.{document_key} = u.ID_DOKUMENT{orphan_join}""")
    return '\nUNION ALL\n'.join(result)
