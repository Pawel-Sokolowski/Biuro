"""HTTPS-only desktop transport with a revocable user session, never DB credentials."""
from pathlib import Path
import threading
import logging
import time
from urllib.parse import urlsplit

import requests
from app.remote.codec import encode,decode


def normalize_server(value):
    value = str(value).strip().rstrip('/')
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Podaj adres HTTPS serwera biura, bez loginu i hasła.')
    return value


class OfficeClient:
    def __init__(self,server,*,verify=True):
        self.server = normalize_server(server)
        if verify is False:
            raise ValueError('Weryfikacja certyfikatu serwera jest wymagana.')
        self.verify = verify
        self.token = None
        self.employee_id = None
        self._local = threading.local()
        self._sessions = set()
        self._sessions_lock = threading.Lock()

    def _request(self,path,payload=None,*,authenticated=True):
        started = time.perf_counter()
        try:
            return self._perform_request(path,payload,authenticated=authenticated)
        finally:
            duration = time.perf_counter()-started
            if duration >= .25:
                # Log operation identifiers only, never arguments, URLs or tokens.
                operation = '-'
                if path=='operations' and isinstance(payload,dict):
                    from app.remote.operations import OPERATIONS
                    candidate = payload.get('operation')
                    if candidate in OPERATIONS:
                        operation = candidate
                logging.getLogger(__name__).info(
                    'office_request_slow route=%s operation=%s duration=%.3f main_thread=%s',
                    path if path in ('operations','jobs','login','session','files','download','logout','reset','redeem') else '-',
                    operation,duration,threading.current_thread() is threading.main_thread())

    def _perform_request(self,path,payload=None,*,authenticated=True):
        session = getattr(self._local,'session',None)
        if session is None:
            session = self._local.session = requests.Session()
            import ssl
            context = ssl.create_default_context(cafile=self.verify if isinstance(self.verify,str) else None)
            class OfficeTLS(requests.adapters.HTTPAdapter):
                def build_connection_pool_key_attributes(adapter,request,verify,cert=None):
                    host,options = super().build_connection_pool_key_attributes(request,verify,cert)
                    options['ssl_context'] = context
                    return host,options
            session.mount('https://',OfficeTLS())
            with self._sessions_lock:
                self._sessions.add(session)
        headers = {'Accept':'application/json'}
        if authenticated:
            if not self.token:
                raise PermissionError('Zaloguj się do serwera biura.')
            headers['Authorization'] = 'Bearer ' + self.token
        try:
            response = session.post(self.server+'/api/v1/'+path,json=payload or {},headers=headers,
                                    verify=self.verify,timeout=(10,35),allow_redirects=False)
        except requests.exceptions.SSLError:
            raise ValueError('Nie można potwierdzić certyfikatu serwera biura. Sprawdź adres i zaufany certyfikat HTTPS.') from None
        except requests.RequestException:
            raise ValueError('Brak połączenia z serwerem biura.') from None
        if 300<=response.status_code<400:
            raise ValueError('Serwer nie może przekierowywać logowania ani sesji.')
        try:
            value = response.json()
        except ValueError:
            raise ValueError('Nieprawidłowa odpowiedź serwera biura.') from None
        if response.status_code==401:
            self.token = None
            raise PermissionError(value.get('message','Sesja wygasła. Zaloguj się ponownie.'))
        if response.status_code==403:
            raise PermissionError(value.get('message','Brak uprawnień.'))
        if response.status_code>=400:
            raise ValueError(value.get('message','Operacja na serwerze nie powiodła się.'))
        return value

    def close(self):
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            session.close()

    def login(self,email,password,remember=False):
        result = self._request('login',{'email':email,'password':password,'remember':bool(remember)},authenticated=False)
        self.token,self.employee_id = result['token'],result['employee_id']
        return self.employee_id

    def resume(self,token):
        self.token = token
        result = self._request('session')
        self.employee_id = result['employee_id']
        return self.employee_id

    def logout(self):
        try:
            if self.token:
                self._request('logout')
        finally:
            self.token,self.employee_id = None,None

    def _file(self,path,mode):
        path = Path(path)
        data = path.read_bytes() if mode=='input' else None
        import base64
        return self._request('files',{'name':path.name,'data':base64.b64encode(data).decode() if data is not None else None})

    def call(self,operation,args=(),kwargs=None,*,object_id=None):
        from app.remote.operations import OPERATIONS
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise PermissionError('Ta operacja nie jest udostępniona przez serwer.')
        # Names come from the shipped manifest; bind without importing server code.
        values = dict(zip(spec['arguments'],args))
        if len(args)>len(spec['arguments']) or set(values)&set(kwargs or {}):
            raise ValueError('Nieprawidłowe argumenty operacji.')
        values.update(kwargs or {})
        callbacks,stops,outputs = {},[],[]
        for key,value in list(values.items()):
            if callable(value):
                callbacks[key] = value
                values[key] = {'callback':key}
            elif isinstance(value,threading.Event):
                stops.append(value)
                values[key] = {'cancel':True}
        for key,mode in spec.get('files',{}).items():
            if values.get(key):
                if mode=='inputs':
                    values[key] = [self._file(v,'input') for v in values[key]]
                else:
                    local_path = values[key]
                    values[key] = self._file(local_path,mode)
                    if mode=='output':
                        outputs.append((Path(local_path),values[key]))
        from app.email.bulk_mail import BulkMailPlan
        from dataclasses import replace
        if isinstance(values.get('plan'),BulkMailPlan):
            values['plan'] = replace(values['plan'],attachments=tuple(self._file(path,'input') for path in values['plan'].attachments))
        def query_upload(value):
            if isinstance(value,dict):
                defaults = value.get('_query_defaults') or {}
                return {key:(item if key=='_query_defaults' or defaults.get(key)==item else
                             self._file(item,'input') if key.endswith('_query') and isinstance(item,str)
                             and Path(item).is_file() else query_upload(item)) for key,item in value.items()}
            if isinstance(value,list):
                return [query_upload(item) for item in value]
            return value
        if operation.startswith(('app.integrations.enova.','app.integrations.platnik.')):
            for key in ('config','value'):
                if key in values:
                    values[key] = query_upload(values[key])
        result = self._request('operations',{'operation':operation,'arguments':encode(values),'object':object_id})
        job,seen = result.get('job'),0
        if result.get('completed'):
            value = decode(result['result'])
            for event in result.get('events',[]):
                if event['name'] in callbacks:
                    callbacks[event['name']](*decode(event['args']),**decode(event['kwargs']))
        while job is not None:
            result = self._request('jobs',{'job':job,'seen':seen,'cancel':any(stop.is_set() for stop in stops)})
            for event in result.get('events',[]):
                callback = callbacks.get(event['name'])
                if callback:
                    callback(*decode(event['args']),**decode(event['kwargs']))
            seen = result['seen']
            if result['state']=='done':
                value = decode(result['result'])
                break
            if result['state']=='failed':
                if result.get('permission'):
                    raise PermissionError(result['message'])
                raise ValueError(result['message'])
            time.sleep(.15)
        def materialize(item):
            if isinstance(item,dict) and set(item)=={'office_file'}:
                file = item['office_file']
                existing = next((path for path,ref in outputs if ref['file']==file['file']),None)
                if existing is not None:
                    return str(existing)
                import tempfile
                folder = Path(tempfile.mkdtemp(prefix='biuro-document-'))
                path = folder / Path(file['name'].replace('\\','/')).name
                outputs.append((path,file))
                return str(path)
            if isinstance(item,dict):
                return {k:materialize(v) for k,v in item.items()}
            if isinstance(item,tuple) and hasattr(item,'keys'):
                return item
            if isinstance(item,(list,tuple)):
                return type(item)(materialize(v) for v in item)
            return item
        value = materialize(value)
        for path,file in outputs:
            import base64,os,tempfile
            data = base64.b64decode(self._request('download',file)['data'],validate=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(data)
                os.replace(temporary,path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return value
