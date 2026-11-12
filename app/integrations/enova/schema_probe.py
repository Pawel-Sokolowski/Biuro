"""Metadata-only export for mapping installation-specific payroll/declaration tables."""
from app.integrations.enova.document_counts import sql_connection_string
from app.integrations.enova.odbc_support import load_odbc, resolve_driver, describe_sql_error
from app.integrations.enova.connection_settings import credentials


def inspect_schema(config, database, *, password=''):
    if not database:
        raise ValueError('Wpisz bazę Enova przy wybranej firmie.')
    odbc = load_odbc()
    config = {**config, 'driver': resolve_driver(config, odbc)}
    if not password:
        if not config.get('password_enc'):
            raise ValueError('Uzupełnij hasło SQL.')
        _, password = credentials(config)
    connection = None
    try:
        connection = odbc.connect(sql_connection_string(config, database, config['username'], password),
                                  timeout=15, attrs_before={101: 1})
        queries = {
            'columns': """SELECT s.name AS schema_name,t.name AS object_name,c.name AS column_name,
                ty.name AS data_type FROM sys.objects t
                JOIN sys.schemas s ON s.schema_id=t.schema_id
                JOIN sys.columns c ON c.object_id=t.object_id
                JOIN sys.types ty ON ty.user_type_id=c.user_type_id
                WHERE t.type IN ('U','V') AND t.is_ms_shipped=0 ORDER BY s.name,t.name,c.column_id""",
            'foreign_keys': """SELECT OBJECT_SCHEMA_NAME(f.parent_object_id) AS schema_name,
                OBJECT_NAME(f.parent_object_id) AS object_name,
                COL_NAME(f.parent_object_id,f.parent_column_id) AS column_name,
                OBJECT_SCHEMA_NAME(f.referenced_object_id) AS referenced_schema,
                OBJECT_NAME(f.referenced_object_id) AS referenced_object,
                COL_NAME(f.referenced_object_id,f.referenced_column_id) AS referenced_column
                FROM sys.foreign_key_columns f ORDER BY schema_name,object_name,column_name""",
        }
        result = {'database': database}
        for name, query in queries.items():
            cursor = connection.cursor()
            cursor.timeout = 30
            cursor.execute(query)
            names = [column[0] for column in cursor.description]
            rows = cursor.fetchmany(100001)
            if len(rows) > 100000:
                raise ValueError('Zbyt duży schemat do jednorazowego eksportu.')
            result[name] = [dict(zip(names, row)) for row in rows]
        if not result['columns']:
            raise ValueError('Brak widocznych metadanych. Sprawdź uprawnienie VIEW DEFINITION do bazy.')
        return result
    except Exception as exc:
        error = describe_sql_error(exc, secrets=(password, config.get('password_enc')))
        raise ValueError(error['hint'] + '\n' + error['details']) from None
    finally:
        if connection is not None:
            connection.close()
