"""A fixed catalogue of business operations, with bounded session-owned jobs."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
import logging
import secrets
import threading
import time

from app.remote import auth
from app.remote.codec import encode,decode
from app.remote.files import FileStore
from app.remote.policy import check,bind,resource_access,public_result
from app.remote.secrets import safe_error
from app.remote.state import service_scope
from contextvars import ContextVar
_operation_owner = ContextVar("office_operation_owner")

logger = logging.getLogger(__name__)


class OfficeService:
    def __init__(self,directory):
        self.files = FileStore(Path(directory)/'api-files')
        self.jobs,self.objects,self.preparations = {},{},{}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=4,thread_name_prefix='office-api')
        with service_scope():
            auth.initialize()

    def identity(self,token):
        with service_scope():
            return auth.authenticate(token)

    def close(self):
        for job in self.jobs.values():
            job['stop'].set()
        self.executor.shutdown(wait=True,cancel_futures=True)
        for item in self.preparations.values():
            item['value'].close()
        self.preparations.clear()

    def forget(self,token):
        owner = auth.digest(token)
        with service_scope():
            auth.logout(token)
        with self.lock:
            for job in self.jobs.values():
                if job['owner']==owner:
                    job['stop'].set()
            for key,value in list(self.objects.items()):
                if value['owner']==owner:
                    del self.objects[key]
            for key,item in list(self.preparations.items()):
                if item['owner']==owner:
                    item['value'].close()
                    del self.preparations[key]
        self.files.forget(owner)

    def submit(self,token,payload):
        from app.remote.operations import OPERATIONS
        actor = self.identity(token)
        operation = payload.get('operation')
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise PermissionError('Nieznana operacja.')
        with service_scope(actor):
            check(spec['capability'],actor)
        values = decode(payload['arguments'])
        if not isinstance(values,dict):
            raise ValueError('Nieprawidłowe argumenty.')
        owner = auth.digest(token)
        with self.lock:
            self._expire()
            if len(self.jobs)>=1000:
                raise ValueError('Zbyt wiele oczekujących wyników. Poczekaj na zakończenie odczytów.')
            if sum(j['state']=='running' for j in self.jobs.values())>=32 or sum(j['owner']==owner and j['state']=='running' for j in self.jobs.values())>=4:
                raise ValueError('Trwają już inne operacje. Poczekaj na ich zakończenie.')
            key = secrets.token_urlsafe(24)
            job = {'owner':owner,'state':'running','events':[],'stop':threading.Event(),'created':time.monotonic()}
            self.jobs[key] = job
        future = self.executor.submit(self._execute,job,token,actor,operation,spec,values,payload.get('object'))
        try:
            future.result(timeout=.2)
        except TimeoutError:
            pass
        if job['state']=='done':
            with self.lock:
                self.jobs.pop(key,None)
            return {'completed':True,'result':job['result'],'events':list(job['events'])}
        if job['state']=='failed':
            with self.lock:
                self.jobs.pop(key,None)
            if job.get('permission'):
                raise PermissionError(job['message'])
            raise ValueError(job['message'])
        return {'job':key}

    def _expire(self):
        now = time.monotonic()
        for key,job in list(self.jobs.items()):
            if job['state']!='running' and now-job['created']>3600:
                del self.jobs[key]
        for key,item in list(self.preparations.items()):
            if now-item['created']>3600:
                item['value'].close()
                del self.preparations[key]
        for key,value in list(self.objects.items()):
            if now-value['created']>14*86400:
                del self.objects[key]

    def _execute(self,job,token,actor,operation,spec,values,object_id):
        try:
            _operation_owner.set(job['owner'])
            with service_scope(actor):
                if self.identity(token)!=actor:
                    raise PermissionError('Sesja wygasła.')
                check(spec['capability'],actor)
                for name,mode in spec.get('files',{}).items():
                    if values.get(name):
                        values[name] = ([self.files.path(job['owner'],item) for item in values[name]] if mode=='inputs'
                                        else self.files.path(job['owner'],values[name]))
                for name,value in list(values.items()):
                    if isinstance(value,dict) and value=={'callback':name}:
                        if name not in spec.get('callbacks',[]):
                            raise ValueError('Nieprawidłowy parametr postępu.')
                        def emit(*args,_name=name,**kwargs):
                            with self.lock:
                                if len(job['events'])<1000:
                                    job['events'].append({'name':_name,'args':encode(public_result(operation,args,actor)),
                                                          'kwargs':encode(public_result(operation,kwargs,actor))})
                        values[name] = emit
                    elif isinstance(value,dict) and value=={'cancel':True}:
                        if name not in spec.get('cancellation',[]):
                            raise ValueError('Nieprawidłowy parametr przerwania.')
                        values[name] = job['stop']
                from app.email.bulk_mail import BulkMailPlan
                from dataclasses import replace
                if isinstance(values.get('plan'),BulkMailPlan):
                    plan = values['plan']
                    values['plan'] = replace(plan,attachments=tuple(self.files.path(job['owner'],item) for item in plan.attachments))
                module = import_module(spec['module'])
                if 'class' in spec:
                    cls = getattr(module,spec['class'])
                    cls = getattr(cls,'__office_original__',cls)
                    if spec['method']=='__new__':
                        initializer = cls.__init__
                        def construct(**kwargs):
                            return cls(**kwargs)
                        import inspect
                        signature = inspect.Signature() if initializer is object.__init__ else inspect.signature(initializer)
                        arguments = {k:v for k,v in signature.parameters.items() if k!='self'}
                        construct.__signature__ = signature.replace(parameters=arguments.values())
                        bound = bind(construct,values,spec,actor)
                        with self.lock:
                            if sum(item['owner']==job['owner'] for item in self.objects.values())>=100:
                                raise ValueError('Zbyt wiele otwartych widoków.')
                        # Schema upgrades are explicit server commands, never remote desktop actions.
                        value = object.__new__(cls) if spec['class']=='GlobalSettingsLogic' else construct(**bound.arguments)
                        key = secrets.token_urlsafe(24)
                        with self.lock:
                            self.objects[key] = {'owner':job['owner'],'class':spec['class'],'value':value,
                                                 'lock':threading.RLock(),'created':time.monotonic()}
                        result = {'object':key,'state':dict(value.__dict__)}
                    else:
                        with self.lock:
                            item = self.objects.get(object_id)
                        if not item or item['owner']!=job['owner'] or item['class']!=spec['class']:
                            raise PermissionError('Widok jest niedostępny. Otwórz go ponownie.')
                        with item['lock']:
                            function = getattr(item['value'],spec['method'])
                            bound = bind(function,values,spec,actor)
                            value = self._invoke(function,bound,spec,operation,actor)
                            result = {'result':value,'state':dict(item['value'].__dict__)}
                else:
                    function = getattr(module,spec['function'])
                    function = getattr(function,'__office_original__',function)
                    bound = bind(function,values,spec,actor)
                    result = self._invoke(function,bound,spec,operation,actor)
                # Only paths explicitly reserved as operation outputs become downloadable.
                output_paths = {}
                for name,mode in spec.get('files',{}).items():
                    if mode=='output' and values.get(name):
                        output_paths[str(values[name])] = self.files.reference(job['owner'],values[name])
                if operation=='app.invoices.ksef_submission.download_submitted_invoice':
                    from app.config import get_data_dir
                    generated = Path(result).resolve()
                    generated.relative_to((get_data_dir()/'ksef').resolve())
                    if generated.suffix.lower()!='.xml':
                        raise PermissionError('Nieprawidłowy dokument KSeF.')
                    output_paths[str(result)] = self.files.put(job['owner'],generated.name,generated.read_bytes())
                def transport_files(value):
                    if isinstance(value,(str,Path)) and str(value) in output_paths:
                        return {'office_file':output_paths[str(value)]}
                    if isinstance(value,dict):
                        return {k:transport_files(v) for k,v in value.items()}
                    if isinstance(value,tuple) and hasattr(value,'keys'):
                        return value
                    if isinstance(value,(list,tuple)):
                        return type(value)(transport_files(v) for v in value)
                    return value
                result = encode(public_result(operation,transport_files(result),actor))
                with self.lock:
                    job.update(state='done',result=result)
        except Exception as error:
            if not isinstance(error,(ValueError,PermissionError)):
                logger.exception('office_operation_failed operation=%s employee_id=%s',operation,actor)
            with self.lock:
                job.update(state='failed',message=safe_error(error),permission=isinstance(error,PermissionError))

    def _invoke(self,function,bound,spec,operation,actor):
        from app.remote.secrets import restore,secret_name,PRESENT
        resource_access(operation,bound.arguments,actor)
        from app.remote.configuration import prepare
        prepare(operation,bound.arguments,self.files,_operation_owner.get())
        name = spec.get('function',spec.get('method'))
        from app.remote.prepared import ImportPreparation
        preparation = bound.arguments.get('prepared')
        if operation.endswith('.release_preparation') or isinstance(preparation,ImportPreparation):
            reference = bound.arguments.get('reference') if operation.endswith('.release_preparation') else preparation.reference
            with self.lock:
                item = self.preparations.get(reference)
                if not item or item['owner']!=_operation_owner.get():
                    raise PermissionError('Plan importu wygasł. Sprawdź plik ponownie.')
                if operation.endswith('.release_preparation'):
                    item['value'].close()
                    del self.preparations[reference]
                    return None
                bound.arguments['prepared'] = item['value']
                for field in ('filepath','file_path'):
                    if field in bound.arguments:
                        import hashlib
                        incoming = Path(bound.arguments[field]).read_bytes()
                        accepted = Path(item['value'].path).read_bytes()
                        if hashlib.sha256(incoming).digest()!=hashlib.sha256(accepted).digest():
                            raise ValueError('Plik zmienił się po sprawdzeniu. Wybierz go ponownie i sprawdź import.')
                        bound.arguments[field] = item['value'].path
        setting_key = bound.arguments.get('key',bound.arguments.get('klucz'))
        if name in ('set_setting','delete_setting') and setting_key:
            from app.security.authorization import is_administrator
            if not is_administrator(actor):
                raise PermissionError('Tylko administrator zmienia ustawienia biura.')
            protected = {'enova_connection','platnik_connection','ezla_service','saldeo_web',
                         'caldav_url','caldav_login','caldav_kalendarz','google_client_id'}
            if setting_key in protected or secret_name(setting_key):
                raise PermissionError('Użyj formularza konfiguracji usługi. Surowa zmiana danych dostępu jest zablokowana.')
            if name=='set_setting':
                from app.db.dao_app_settings import get_setting
                for field in ('value','wartosc'):
                    if field in bound.arguments:
                        bound.arguments[field] = restore(bound.arguments[field],get_setting(setting_key))
        result = function(*bound.args,**bound.kwargs)
        if operation=='app.utils.importers.prepare_company_import':
            plan,prepared = result
            with self.lock:
                if sum(item['owner']==_operation_owner.get() for item in self.preparations.values())>=8:
                    prepared.close()
                    raise ValueError('Zbyt wiele otwartych planów importu.')
                reference = secrets.token_urlsafe(24)
                self.preparations[reference] = {'owner':_operation_owner.get(),'value':prepared,'created':time.monotonic()}
            result = plan,ImportPreparation(reference)
        if name in ('get_setting','get_setting_first'):
            keys = bound.arguments.get('keys',[]) if setting_key is None else [setting_key]
            if any(secret_name(key) for key in keys):
                result = PRESENT if result else ''
        return result

    def poll(self,token,payload):
        self.identity(token)
        owner = auth.digest(token)
        with self.lock:
            job = self.jobs.get(payload.get('job'))
            if not job or job['owner']!=owner:
                raise PermissionError('Brak dostępu do operacji.')
            seen = payload.get('seen',0)
            if type(seen) is not int or seen<0:
                raise ValueError('Nieprawidłowy odczyt postępu.')
            if payload.get('cancel'):
                job['stop'].set()
            result = {key:value for key,value in job.items() if key in ('state','result','message','permission')}
            result.update(events=job['events'][seen:],seen=len(job['events']))
            return result
