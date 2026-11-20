"""Small private-office HTTPS host. Only the fixed /api/v1 routes are accepted."""
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import logging
import ssl
import socket
import threading

from app.remote import auth
from app.remote.secrets import safe_error
from app.remote.service import OfficeService
from app.remote.state import service_scope

MAX_BODY = 48*1024*1024
logger = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Biuro'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self,*args):
        # Request headers and session tokens must never reach access logs.
        pass

    def _answer(self,status,value):
        data = json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if status>=400:
            self.close_connection = True
            self.send_header('Connection','close')
        self.end_headers()
        self.wfile.write(data)

    def handle_one_request(self):
        # Idle keep-alive clients must release bounded connection threads quickly.
        self.connection.settimeout(5)
        super().handle_one_request()

    def do_GET(self):
        self._answer(405,{'message':'Użyj aplikacji Biuro.'})

    def do_POST(self):
        large_body = False
        try:
            self.connection.settimeout(20)
            public_routes = {'/api/v1/login','/api/v1/reset','/api/v1/redeem'}
            private_routes = {'/api/v1/session','/api/v1/logout','/api/v1/operations','/api/v1/jobs','/api/v1/files','/api/v1/download','/api/v1/enova/ezla'}
            if self.path not in public_routes|private_routes:
                self._answer(404,{'message':'Nieznana operacja.'})
                return
            # Authenticate before allocating or parsing an upload body.
            if self.path in private_routes:
                header = self.headers.get('Authorization','')
                if not header.startswith('Bearer '):
                    self._answer(401,{'message':'Zaloguj się do serwera biura.'})
                    return
                try:
                    self.server.office_service.identity(header[7:])
                except PermissionError:
                    self._answer(401,{'message':'Sesja wygasła. Zaloguj się ponownie.'})
                    return
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';',1)[0]!='application/json':
                raise ValueError('Nieprawidłowy format żądania.')
            size = int(self.headers.get('Content-Length','0'))
            if len(self.headers.get_all('Content-Length',[]))!=1:
                raise ValueError('Nieprawidłowy rozmiar żądania.')
            limit = 16*1024 if self.path in public_routes else MAX_BODY
            if not 0<size<=limit:
                raise ValueError('Nieprawidłowy rozmiar żądania.')
            if size>1024*1024:
                large_body = self.server.large_bodies.acquire(blocking=False)
                if not large_body:
                    self._answer(429,{'message':'Trwają inne przesyłania plików. Spróbuj ponownie.'})
                    return
            raw = self.rfile.read(size)
            if len(raw)!=size:
                raise ValueError('Niepełne żądanie.')
            value = json.loads(raw)
            if not isinstance(value,dict):
                raise ValueError('Nieprawidłowe żądanie.')
            service = self.server.office_service
            if self.path in ('/api/v1/reset','/api/v1/redeem'):
                email = value.get('email')
                if not isinstance(email,str) or not 1<=len(email)<=320:
                    raise ValueError('Podaj adres e-mail pracownika.')
                with service_scope():
                    from app.db.dao_conn import get_conn
                    with get_conn() as conn:
                        permitted = auth._attempt(conn,'reset-ip:'+self.client_address[0],20)
                    if not permitted:
                        raise ValueError('Zbyt wiele prób. Spróbuj ponownie później.')
                    if self.path.endswith('/reset'):
                        from app.utils.password_reset import reset_password,GENERIC_MESSAGE
                        ok,message = reset_password(email)
                        if not ok:
                            # SMTP error details and account configuration are server-local.
                            logger.warning('office_password_reset_not_sent')
                        result = {'message':GENERIC_MESSAGE}
                    else:
                        from app.utils.password_reset import redeem_password_reset
                        code,password = value.get('code'),value.get('password')
                        if not isinstance(code,str) or not 1<=len(code)<=128 or not isinstance(password,str):
                            raise ValueError('Podaj kod i nowe hasło.')
                        redeem_password_reset(email,code,password)
                        result = {'message':'Hasło zmienione. Zaloguj się nowym hasłem.'}
                self._answer(200,result)
                return
            if self.path=='/api/v1/login':
                try:
                    with service_scope():
                        result = auth.login(value.get('email'),value.get('password'),self.client_address[0],value.get('remember') is True)
                except PermissionError as error:
                    self._answer(401,{'message':str(error)})
                    return
                result['ui_authorization'] = auth.ui_authorization(result['employee_id'])
                result['ui_features'] = ['mail_sidebar_snapshot','calendar_view_snapshot','checking_run_now','invoice_selection']
                self._answer(200,result)
                return
            header = self.headers.get('Authorization','')
            if not header.startswith('Bearer '):
                self._answer(401,{'message':'Zaloguj się do serwera biura.'})
                return
            token = header[7:]
            try:
                actor = service.identity(token)
            except PermissionError:
                self._answer(401,{'message':'Sesja wygasła. Zaloguj się ponownie.'})
                return
            if self.path=='/api/v1/session':
                from app.version import __version__
                result = {'employee_id':actor,'version':__version__,'ui_authorization':auth.ui_authorization(actor),
                          'ui_features':['mail_sidebar_snapshot','calendar_view_snapshot','checking_run_now','invoice_selection']}
            elif self.path=='/api/v1/logout':
                service.forget(token)
                result = {'ok':True}
            elif self.path=='/api/v1/enova/ezla':
                from app.integrations.ezla.enova_export import export_reports
                with service_scope():
                    result = export_reports(actor,value)
            elif self.path=='/api/v1/operations':
                result = service.submit(token,value)
            elif self.path=='/api/v1/jobs':
                result = service.poll(token,value)
            elif self.path=='/api/v1/files':
                data = base64.b64decode(value['data'],validate=True) if value.get('data') is not None else None
                result = service.files.put(auth.digest(token),value.get('name','plik'),data)
            elif self.path=='/api/v1/download':
                result = {'data':base64.b64encode(service.files.get(auth.digest(token),value)).decode('ascii')}
            else:
                self._answer(404,{'message':'Nieznana operacja.'})
                return
            self._answer(200,result)
        except (ValueError,KeyError,TypeError) as error:
            self._answer(400,{'message':safe_error(error)})
        except PermissionError as error:
            self._answer(403,{'message':safe_error(error)})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            self.close_connection = True
        except Exception:
            logger.exception('office_http_request_failed')
            self._answer(500,{'message':'Błąd serwera biura. Sprawdź logi serwera.'})
        finally:
            if large_body:
                self.server.large_bodies.release()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self,address,service):
        self.office_service = service
        self.slots = threading.BoundedSemaphore(64)
        self.large_bodies = threading.BoundedSemaphore(4)
        super().__init__(address,Handler)

    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except Exception:
            self.slots.release()
            raise

    def get_request(self):
        connection,address = super().get_request()
        connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        connection.settimeout(10)
        return self.tls_context.wrap_socket(connection,server_side=True,do_handshake_on_connect=False),address

    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self.slots.release()


def create_server(host,port,certificate,private_key,directory):
    if not certificate or not private_key:
        raise ValueError('Skonfiguruj certyfikat HTTPS i jego klucz prywatny na serwerze biura.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate,private_key)
    service = OfficeService(directory)
    try:
        server = Server((host,int(port)),service)
    except Exception:
        service.close()
        raise
    server.tls_context = context
    return server


def from_environment():
    import os
    from app.config import get_data_dir
    return create_server(os.getenv('BIO_API_BIND','0.0.0.0'),int(os.getenv('BIO_API_PORT','8443')),
                         os.getenv('BIO_API_CERT',''),os.getenv('BIO_API_TLS_KEY',''),get_data_dir())


def start_configured():
    import os
    if os.getenv('BIO_API_ENABLED')!='1':
        return None
    server = from_environment()
    threading.Thread(target=server.serve_forever,name='office-https',daemon=True).start()
    return server


def main():
    from app.utils.env_config import ensure_encryption_key
    from app.db.schema import schema_is_current
    ensure_encryption_key()
    if not schema_is_current():
        raise ValueError('Najpierw zaktualizuj bazę na serwerze biura.')
    from app.security.bootstrap import needs_initial_admin
    if needs_initial_admin():
        raise ValueError('Utwórz pierwszego administratora lokalnie na serwerze (Biuro.exe --direct).')
    from app.diagnostics import configure_logging
    configure_logging()
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PyQt6.QtWidgets import QApplication
    qt_application = QApplication.instance() or QApplication(['biuro-api'])
    server = from_environment()
    logger.info('office_https_started address=%s port=%s',*server.server_address)
    try:
        server.serve_forever()
    finally:
        server.office_service.close()
        server.server_close()
        qt_application.processEvents()
