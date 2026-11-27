"""Per-user secrets in Windows Credential Manager."""
import ctypes
from ctypes import wintypes
import hashlib
import os


class Credential(ctypes.Structure):
    _fields_ = [('Flags',wintypes.DWORD),('Type',wintypes.DWORD),('TargetName',wintypes.LPWSTR),
                ('Comment',wintypes.LPWSTR),('LastWritten',wintypes.FILETIME),('CredentialBlobSize',wintypes.DWORD),
                ('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',wintypes.DWORD),
                ('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),
                ('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]


def target(server):
    return 'Biuro/ServerSession/' + hashlib.sha256(server.encode()).hexdigest()


def read(server):
    return read_named(target(server))


def read_named(name):
    if os.name != 'nt':
        return None
    api = ctypes.WinDLL('advapi32',use_last_error=True)
    pointer = ctypes.POINTER(Credential)()
    api.CredReadW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    if not api.CredReadW(name,1,0,ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
    finally:
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree(pointer)


def save(server, token):
    return save_named(target(server), token, 'Biuro session')


def save_named(name, secret, description):
    if os.name != 'nt':
        return False
    raw = secret.encode()
    buffer = (ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
    value = Credential(Type=1,TargetName=name,CredentialBlobSize=len(raw),CredentialBlob=buffer,
                       Persist=2,UserName=description)
    api = ctypes.WinDLL('advapi32',use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential),wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    if not api.CredWriteW(ctypes.byref(value),0):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def delete(server):
    delete_named(target(server))


def delete_named(name):
    if os.name == 'nt':
        api = ctypes.WinDLL('advapi32',use_last_error=True)
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD]
        api.CredDeleteW(name,1,0)
