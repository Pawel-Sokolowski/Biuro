"""Session-owned file capabilities; client paths never become server paths."""
from pathlib import Path
import secrets
import shutil
import threading
import time

MAX_FILE = 32*1024*1024
_lock = threading.RLock()


class FileStore:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.items = {}
        # Retire orphaned upload files after a server restart as well.
        for path in self.root.iterdir():
            if path.is_file() and not path.is_symlink() and time.time()-path.stat().st_mtime>12*3600:
                path.unlink(missing_ok=True)
            elif (path.is_dir() and not path.is_symlink() and len(path.name) == 32
                    and all(c.isascii() and (c.isalnum() or c in '_-') for c in path.name)
                    and time.time()-path.stat().st_mtime>12*3600):
                shutil.rmtree(path)

    def put(self, owner, name, data=None):
        if data is not None and len(data)>MAX_FILE:
            raise ValueError('Plik przekracza 32 MB.')
        name = Path(str(name).replace('\\','/')).name
        if (not name or name in ('.', '..') or name.endswith(('.', ' ')) or len(name) > 180
                or any(ord(c) < 32 or c in '<>:"|?*' for c in name)):
            raise ValueError('Nieprawidłowa nazwa pliku.')
        suffix = Path(name).suffix.lower()
        if len(suffix)>12 or not all(c.isalnum() or c=='.' for c in suffix):
            raise ValueError('Nieprawidłowa nazwa pliku.')
        token = secrets.token_urlsafe(24)
        path = self.root/token/name
        with _lock:
            self.expire()
            if sum(item[0]==owner for item in self.items.values())>=100 or len(self.items)>=1000:
                raise ValueError('Zbyt wiele plików w sesji.')
            size = len(data) if data is not None else 0
            total = sum(item[1].stat().st_size for item in self.items.values() if item[1].is_file())
            own = sum(item[1].stat().st_size for item in self.items.values() if item[0]==owner and item[1].is_file())
            if total+size>1024*1024*1024 or own+size>128*1024*1024:
                raise ValueError('Zbyt wiele plików w sesji.')
            path.parent.mkdir(mode=0o700)
            if data is not None:
                try:
                    path.write_bytes(data)
                    path.chmod(0o600)
                except OSError:
                    shutil.rmtree(path.parent, ignore_errors=True)
                    raise
            self.items[token] = (owner,path,time.monotonic())
        return {'file':token,'name':name}

    def path(self, owner, value):
        if not isinstance(value,dict) or set(value)-{'file','name'} or not isinstance(value.get('file'),str):
            raise ValueError('Wybierz plik na swoim komputerze.')
        with _lock:
            self.expire()
            item = self.items.get(value['file'])
            if not item or item[0]!=owner:
                raise PermissionError('Brak dostępu do pliku.')
            return str(item[1])

    def reference(self,owner,path):
        with _lock:
            for token,item in self.items.items():
                if item[0]==owner and str(item[1])==str(path):
                    return {'file':token,'name':item[1].name}
        raise PermissionError('Plik nie należy do tej sesji.')

    def get(self, owner, value):
        with _lock:
            path = Path(self.path(owner,value))
            if not path.is_file() or path.stat().st_size>MAX_FILE:
                raise ValueError('Plik nie istnieje lub jest zbyt duży.')
            return path.read_bytes()

    def release(self, owner, references):
        if (not isinstance(references, list) or len(references) > 100
                or any(not isinstance(ref, dict) or not isinstance(ref.get('file'), str) for ref in references)):
            raise ValueError('Nieprawidłowa lista plików.')
        with _lock:
            for reference in references:
                token = reference['file']
                item = self.items.get(token)
                if item and item[0] == owner:
                    self._remove(token, item[1])

    def release_path(self, owner, path):
        with _lock:
            references = [{'file': token} for token, item in self.items.items()
                          if item[0] == owner and str(item[1]) == str(path)]
            self.release(owner, references)

    def expire(self):
        now = time.monotonic()
        for token,item in list(self.items.items()):
            if now-item[2]>12*3600:
                self._remove(token, item[1])

    def forget(self, owner):
        with _lock:
            for token,item in list(self.items.items()):
                if item[0]==owner:
                    self._remove(token, item[1])

    def _remove(self, token, path):
        if path.parent == self.root/token and path.parent.is_dir():
            shutil.rmtree(path.parent)
        del self.items[token]
