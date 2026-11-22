"""Read a profiled register from the local Płatnik process on Windows."""
from ctypes import byref, create_unicode_buffer, sizeof
from ctypes import wintypes
from pathlib import Path
from subprocess import list2cmdline
import sys

from app.integrations.platnik.gui_evidence import MAX_ROWS, ReadError, label
from app.utils.nip import normalize_nip


def interactive_desktop():
    if sys.platform != 'win32':
        return False
    from ctypes import WinDLL
    import win32ts

    try:
        state = win32ts.WTSQuerySessionInformation(
            win32ts.WTS_CURRENT_SERVER_HANDLE, win32ts.WTS_CURRENT_SESSION, win32ts.WTSConnectState,
        )
        if state != win32ts.WTSActive:
            return False
    except OSError:
        return False

    user = WinDLL('user32', use_last_error=True)
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, wintypes.INT, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPDWORD,
    ]
    handle = user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not handle:
        return False
    try:
        name = create_unicode_buffer(256)
        size = wintypes.DWORD()
        success = user.GetUserObjectInformationW(handle, 2, name, sizeof(name), byref(size))  # UOI_NAME
        return bool(success and name.value.casefold() == 'default')
    finally:
        user.CloseDesktop(handle)


def process_image(pid):
    from ctypes import WinDLL

    kernel = WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, wintypes.LPDWORD,
    ]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        name = create_unicode_buffer(32768)
        size = wintypes.DWORD(len(name))
        if kernel.QueryFullProcessImageNameW(handle, 0, name, byref(size)):
            return Path(name.value).resolve()
        return None
    finally:
        kernel.CloseHandle(handle)


def main_window(config):
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    from pywinauto import Application, Desktop

    executable = config['executable']
    backend = config['backend']
    try:
        # Desktop lists windows in the agent's own interactive session. Compare
        # full image paths before connecting and let Application scope all locators.
        pids = {window.process_id() for window in Desktop(backend='win32').windows()}
        pids = {pid for pid in pids if process_image(pid) == executable}
        if len(pids) > 1:
            raise ReadError('AppUnavailable')
        app = Application(backend=backend)
        if pids:
            app.connect(process=pids.pop(), timeout=5)
        else:
            app.start(list2cmdline([str(executable)]), work_dir=str(executable.parent),
                      timeout=12, wait_for_idle=False)
        if process_image(app.process) != executable:
            raise ReadError('AppUnavailable')
        window = app.window(**config['window'])
        window.wait('exists visible enabled', timeout=12)
        return window
    except ReadError:
        raise
    except Exception:
        raise ReadError('AppUnavailable') from None


def checked_columns(profile, headers):
    result = {}
    for name, field in profile.items():
        index = field['index']
        if index >= len(headers) or label(headers[index]) != label(field['header']):
            raise ReadError('ProfileMismatch')
        result[name] = index
    return result


def payer_rows(read_cell, columns, row_count, nip):
    for index in range(row_count):
        raw_nip = read_cell(index, columns['nip'])
        try:
            row_nip = normalize_nip(raw_nip)
        except ValueError:
            continue
        if row_nip == nip:
            yield {'nip': raw_nip, **{name: read_cell(index, column)
                                     for name, column in columns.items() if name != 'nip'}}


def grid_rows(window, config, *, nip):
    try:
        # A locally chosen tab can navigate to a read-only register. The
        # profile has no generic button invocation, keys, coordinates or scripts.
        for automation_id in config['tabs']:
            tab = window.child_window(auto_id=automation_id, control_type='TabItem')
            tab.wait('exists visible enabled', timeout=4)
            tab.select()
        grid = window.child_window(**config['grid'])
        grid.wait('exists visible enabled', timeout=5)
        wrapper = grid.wrapper_object()
    except Exception:
        raise ReadError('ScreenUnavailable') from None
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    if config['backend'] == 'win32':
        row_count = wrapper.item_count()
        if row_count > MAX_ROWS:
            raise ReadError('ScanLimit')
        columns = checked_columns(config['columns'], [column['text'] for column in wrapper.columns()])
        yield from payer_rows(lambda row, column: wrapper.get_item(row, column).text(),
                              columns, row_count, nip)
        return

    # GridPattern indexes logical rows, including those outside the viewport.
    # Virtualized or custom controls without this contract require a different,
    # verified profile. Visible subsets must not be treated as the whole register.
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_defines import NoPatternInterfaceError
    from pywinauto.uia_element_info import UIAElementInfo

    try:
        raw = wrapper.iface_grid
        headers = wrapper.iface_table.GetCurrentColumnHeaders()
    except NoPatternInterfaceError:
        raise ReadError('ProfileMismatch') from None
    row_count = raw.CurrentRowCount
    if row_count > MAX_ROWS:
        raise ReadError('ScanLimit')
    if headers.Length != raw.CurrentColumnCount:
        raise ReadError('ProfileMismatch')
    columns = checked_columns(config['columns'], [headers.GetElement(index).CurrentName for index in range(headers.Length)])

    def cell_text(row, column):
        cell = UIAWrapper(UIAElementInfo(raw.GetItem(row, column)))
        try:
            return cell.iface_value.CurrentValue
        except NoPatternInterfaceError:
            return cell.window_text()

    yield from payer_rows(cell_text, columns, row_count, nip)


def describe(window):
    """Report locator metadata only, without titles, cell values or screenshots."""
    controls = []
    for item in window.wrapper_object().descendants(depth=4):
        info = item.element_info
        kind = getattr(info, 'control_type', None)
        class_name = info.class_name
        if kind not in ('TabItem', 'DataGrid', 'Table', 'List', 'Pane') and class_name != 'SysListView32':
            continue
        controls.append({'control_type': kind, 'class_name': class_name,
                         'auto_id': getattr(info, 'automation_id', None),
                         'control_id': getattr(info, 'control_id', None)})
    return controls
