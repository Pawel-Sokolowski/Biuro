"""Restore stored secrets on the server without forwarding them to a new origin."""
from pathlib import Path
import secrets

from app.remote.secrets import PRESENT, restore


def _same(value, old, keys):
    return all(str(value.get(key, '')).strip() == str(old.get(key, '')).strip() for key in keys)


def prepare(operation, arguments, files, owner):
    if operation.startswith('app.integrations.enova.'):
        from app.integrations.enova.connection_settings import load_connection
        previous = load_connection()
        field = 'config'
        bundled = Path(__file__).resolve().parents[2] / 'docs' / 'enova'
    elif operation.startswith('app.integrations.platnik.'):
        from app.integrations.platnik.holidays import load
        previous = load()
        field = 'value' if 'value' in arguments else 'config'
        bundled = Path(__file__).resolve().parents[2] / 'docs' / 'platnik'
    else:
        previous = None
    if previous is not None and isinstance(arguments.get(field),dict):
        incoming = dict(arguments[field])
        if not _same(incoming,previous,('server','username')) and not arguments.get('password'):
            raise ValueError('Przy zmianie serwera lub loginu wpisz ponownie hasło SQL.')
        incoming = restore(incoming,previous)
        if not _same(incoming,previous,('server','username')):
            incoming['password_enc'] = ''
        elif not arguments.get('password'):
            incoming['password_enc'] = previous.get('password_enc','')
        incoming['_directory'] = bundled
        _queries(incoming,previous,files,owner,bundled)
        sources = {str(item.get('database')):item for item in previous.get('sources',[])}
        for source in incoming.get('sources',[]):
            old = sources.get(str(source.get('database')), {})
            _queries(source,old,files,owner,bundled)
            # Defaults are server metadata, not instructions supplied by a desktop.
            source.pop('_query_defaults',None)
        arguments[field] = incoming
    if operation=='app.integrations.saldeo_web.settings.save':
        from app.integrations.saldeo_web.settings import load
        old = load()
        config = arguments.get('config') or {}
        if config.get('executable_path','')!=old.get('executable_path',''):
            raise ValueError('Ścieżkę przeglądarki zmień lokalnie na serwerze.')
        from urllib.parse import urlsplit
        import os
        allowed = {'saldeo.brainshare.pl'} | {host.strip().lower() for host in os.getenv('BIO_SALDEO_ALLOWED_HOSTS','').split(',') if host.strip()}
        if any(urlsplit(str(config.get(key,''))).hostname not in allowed for key in ('login_url','history_url')):
            raise ValueError('Adres Saldeo nie należy do zaufanych serwerów skonfigurowanych lokalnie.')
    if operation.startswith('app.kalendarz.caldav_sync.'):
        from app.kalendarz.caldav_sync import get_config
        old = get_config()
        cfg = arguments.get('cfg')
        if cfg is not None:
            if not _same(cfg,old,('url','login')):
                raise ValueError('Zapisz nowe połączenie CalDAV przed odczytem kalendarzy.')
            arguments['cfg'] = old
        if arguments.get('password')==PRESENT or (operation.endswith('.save_config') and not arguments.get('password')):
            if not _same(arguments,old,('url','login')):
                raise ValueError('Przy zmianie adresu lub loginu wpisz ponownie hasło CalDAV.')
            arguments['password'] = old['haslo']


def _queries(value,old,files,owner,bundled):
    for key,item in list(value.items()):
        if not key.endswith('_query') or not item:
            continue
        if isinstance(item,dict):
            source = Path(files.path(owner,item))
            if source.suffix.lower()!='.sql' or not source.is_file():
                raise ValueError('Wybierz plik zapytania SQL.')
            destination = files.root.parent / 'sql-overrides' / (secrets.token_hex(16)+'.sql')
            destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o600)
            value[key] = str(destination)
        else:
            path = Path(str(item))
            allowed = str(item)==str(old.get(key,''))
            try:
                path.resolve().relative_to(bundled.resolve())
                allowed = path.suffix.lower()=='.sql' and path.is_file()
            except ValueError:
                pass
            if not allowed:
                raise ValueError('Wybierz lokalny plik SQL do przesłania albo użyj zapytania dołączonego do workera.')
