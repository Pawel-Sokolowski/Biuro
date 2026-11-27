"""Read registers and retrieve receipts in the local Płatnik process on Windows."""
from contextlib import suppress
from ctypes import byref, create_unicode_buffer, sizeof
from ctypes import wintypes
from pathlib import Path
import re
from subprocess import Popen, list2cmdline
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
    from pywinauto.timings import wait_until

    executable = config['executable']
    backend = config['backend']

    def find_process():
        pids = {window.process_id() for window in Desktop(backend='win32').windows()}
        matching = {pid for pid in pids if process_image(pid) == executable}
        if len(matching) > 1:
            raise ReadError('AppUnavailable')
        return next(iter(matching), None)

    try:
        # Desktop lists windows in the agent's own interactive session. Compare
        # full image paths before connecting and let Application scope all locators.
        pid = find_process()
        app = Application(backend=backend)
        if pid is None and (launcher := config.get('launcher')):
            Popen([str(launcher)], cwd=launcher.parent)
            # The launcher may exit before or after the Płatnik process appears.
            pid = wait_until(12, 0.25, find_process, op=lambda value, _: value is not None)
        if pid is not None:
            app.connect(process=pid, timeout=5)
        else:
            app.start(list2cmdline([str(executable)]), work_dir=str(executable.parent),
                      timeout=12, wait_for_idle=False)
        if process_image(app.process) != executable:
            raise ReadError('AppUnavailable')
        window = app.window(**config['window'])
        try:
            window.wait('exists visible enabled', timeout=12)
        except Exception:
            group_wizard = app.window(title_re=GROUP_SEND_WIZARD, class_name='TfKreatorWysylki')
            if group_wizard.exists(timeout=0) and group_wizard.is_visible():
                raise ReadError('ScreenUnavailable', detail=(
                    'Dokończ kreator grupowego wysyłania zestawów do ZUS w Płatniku. '
                    'Potem uruchom koniec dnia ponownie.'
                )) from None
            raise
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


def register(window, config):
    try:
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
        from pywinauto.controls.common_controls import ListViewWrapper

        if not isinstance(wrapper, ListViewWrapper):
            raise ReadError('ProfileMismatch', detail=(
                'Odczyt Win32 wymaga kontrolki ListView. Dla TdxDBGrid sprawdź w UIA wzorce Grid i Table.'
            ))
        row_count = wrapper.item_count()
        if row_count > MAX_ROWS:
            raise ReadError('ScanLimit')
        columns = checked_columns(config['columns'], [column['text'] for column in wrapper.columns()])

        def cell_text(row, column):
            return wrapper.get_item(row, column).text()

        return wrapper, row_count, columns, cell_text

    # GridPattern indexes logical rows, including those outside the viewport.
    # Virtualized or custom controls without this contract require a different,
    # verified profile. Visible subsets must not be treated as the whole register.
    from pywinauto.uia_defines import NoPatternInterfaceError

    try:
        raw = wrapper.iface_grid
        headers = wrapper.iface_table.GetCurrentColumnHeaders()
    except NoPatternInterfaceError:
        raise ReadError('ProfileMismatch', detail=(
            'Wybrana kontrolka nie udostępnia wymaganych wzorców UIA Grid i Table. '
            'Jeśli w rejestrze jest tylko TdxDBGrid bez tych wzorców, użyj importu Płatnika przez SQL.'
        )) from None
    row_count = raw.CurrentRowCount
    if row_count > MAX_ROWS:
        raise ReadError('ScanLimit')
    if headers.Length != raw.CurrentColumnCount:
        raise ReadError('ProfileMismatch')
    columns = checked_columns(config['columns'], [headers.GetElement(index).CurrentName for index in range(headers.Length)])

    def cell_text(row, column):
        cell = uia_cell(wrapper, row, column)
        try:
            return cell.iface_value.CurrentValue
        except NoPatternInterfaceError:
            return cell.window_text()

    return wrapper, row_count, columns, cell_text


def probe_register(window, config):
    """Verify that the first row can be read without returning any cell values."""
    _, row_count, columns, read = register(window, config)
    if not row_count:
        return f'Nagłówki zgodne ({len(columns)}). Otwórz rejestr z danymi, aby sprawdzić odczyt komórek.'
    try:
        values = [read(0, index) for index in columns.values()]
    except ReadError:
        raise
    except Exception:
        raise ReadError('ReadFailed', detail='Znaleziono tabelę, ale nie udało się odczytać pierwszego wiersza.') from None
    if not any(label(value) for value in values):
        raise ReadError('ProfileMismatch', detail='Pierwszy wiersz jest pusty. Wybierz rejestr zawierający dane.')
    return f'Odczytano pierwszy wiersz i sprawdzono nagłówki ({len(columns)} kolumn).'


def uia_cell(grid, row, column):
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    return UIAWrapper(UIAElementInfo(grid.iface_grid.GetItem(row, column)))


def grid_rows(window, config, *, nip):
    _, row_count, columns, cell_text = register(window, config)
    yield from payer_rows(cell_text, columns, row_count, nip)


def select_sent_set(window, view, set_id, receipt):
    grid, row_count, columns, read = register(window, view)

    def matches_set(index):
        return (read(index, columns['set_id']).strip() == set_id
                and ('receipt' not in columns or read(index, columns['receipt']).strip() == receipt))

    matches = [index for index in range(row_count) if matches_set(index)]
    if len(matches) != 1:
        raise ReadError('ProfileMismatch')
    index = matches[0]
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')

    if view['backend'] == 'win32':
        item = grid.get_item(index)
        if grid.get_selected_count() > int(item.is_selected()):
            for candidate in range(row_count):
                if candidate != index:
                    previous = grid.get_item(candidate)
                    if previous.is_selected():
                        previous.deselect()
        item.select()
        selected_count = grid.get_selected_count()
    else:
        from pywinauto.uia_defines import NoPatternInterfaceError
        from pywinauto.uia_element_info import UIAElementInfo

        item = uia_cell(grid, index, columns['set_id'])
        while item is not None and item != grid:
            try:
                container = item.iface_selection_item.CurrentSelectionContainer
                if container and UIAElementInfo(container) == grid.element_info:
                    break
            except NoPatternInterfaceError:
                pass
            item = item.parent()
        if item is None or item == grid:
            raise ReadError('ProfileMismatch')
        item.select()
        selected_count = grid.iface_selection.GetCurrentSelection().Length

    if selected_count != 1 or not item.is_selected() or not matches_set(index):
        raise ReadError('ProfileMismatch')


def menu_label(text):
    return label(text.split('\t', 1)[0].replace('&', '').rstrip('.…'))


def post_menu(window, heading, action):
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    menu = window.menu()
    for title in (heading, action):
        matches = [item for item in menu.items() if menu_label(item.text()) == label(title)]
        if len(matches) != 1 or not matches[0].is_enabled():
            raise ReadError('ProfileMismatch')
        item = matches[0]
        menu = item.sub_menu()
    window.post_command(item.item_id())


SET_WINDOW = r'Zestaw dokumentów nr ([0-9]+)$'
WIZARD = r'Kreator potwierdzania zestawów\s*[-–—]\s*krok\s*{step}\s*/\s*5$'
GROUP_SEND_WIZARD = r'Kreator grupowego wysyłania zestawów do ZUS\s*[-–—]\s*krok\s*\d+\s*/\s*\d+$'


def advance_wizard(app, step, button, *, message=None):
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    dialog = app.window(title_re=WIZARD.format(step=step))
    dialog.wait('exists visible enabled', timeout=15)
    wrapper = dialog.wrapper_object()
    uia_dialog = None

    def accessible_dialog():
        nonlocal uia_dialog
        if uia_dialog is None:
            from pywinauto import Desktop

            uia_dialog = Desktop(backend='uia').window(handle=wrapper.handle).wrapper_object()
        return uia_dialog

    if message:
        shown = any(message in label(control.window_text())
                    for control in wrapper.descendants(class_name='Static') if control.is_visible())
        if not shown:
            from pywinauto.uia_defines import NoPatternInterfaceError

            accessible = accessible_dialog()
            shown = any(message in label(control.window_text()) for control in
                        accessible.descendants(control_type='Text') if control.is_visible())
            if not shown:
                for memo in accessible.descendants(control_type='Edit', class_name='TMemo'):
                    if memo.is_visible():
                        with suppress(NoPatternInterfaceError):
                            if message in label(memo.iface_value.CurrentValue):
                                shown = True
                                break
            if not shown:
                raise ReadError('ReadFailed')
    controls = [control for control in wrapper.descendants(class_name='Button')
                if menu_label(control.window_text()) == label(button)
                and control.is_visible() and control.is_enabled()]
    if not controls:
        # Płatnik also uses Delphi TCorelButton controls whose names are exposed via UIA.
        controls = [control for control in accessible_dialog().descendants(control_type='Button')
                    if control.element_info.class_name == 'TCorelButton'
                    and menu_label(control.window_text()) == label(button)
                    and control.is_visible() and control.is_enabled()]
        if len(controls) != 1:
            raise ReadError('ProfileMismatch')
        controls[0].invoke()
        return
    if len(controls) != 1:
        raise ReadError('ProfileMismatch')
    from pywinauto.win32defines import BN_CLICKED, WM_COMMAND
    control = controls[0]
    wrapper.post_message(WM_COMMAND, control.control_id() | (BN_CLICKED << 16), control.handle)


def download_confirmation(window, view, set_id, receipt):
    from pywinauto import Application
    from pywinauto.win32defines import WM_CLOSE

    if not re.fullmatch(r'[0-9]+', set_id):
        raise ReadError('ProfileMismatch')
    opened_set = started_wizard = False
    try:
        main = window.wrapper_object()
        app = Application(backend='win32').connect(process=main.process_id(), timeout=5)
        # Use native menus even when the grids are read via UIA. A posted
        # command lets the agent continue when Płatnik opens a modal wizard.
        root = app.window(handle=main.handle)
        details = app.window(title_re=SET_WINDOW)
        wizard = app.window(title_re=WIZARD.format(step='[1-5]'))
        if details.exists(timeout=0) or wizard.exists(timeout=0):
            raise ReadError('ScreenUnavailable')
        select_sent_set(window, view, set_id, receipt)
        post_menu(root.wrapper_object(), 'Program', 'Otwórz')
        opened_set = True
        details.wait('exists visible enabled', timeout=5)
        opened = details.wrapper_object()
        title = re.fullmatch(SET_WINDOW, opened.window_text())
        if not title or int(title[1]) != int(set_id):
            raise ReadError('ProfileMismatch')
        post_menu(opened, 'Narzędzia', 'Pobierz i rejestruj potwierdzenie')
        started_wizard = True
        advance_wizard(app, 1, 'Dalej >')
        advance_wizard(app, 3, 'Dalej >', message='podpis złożony na potwierdzeniu został zweryfikowany poprawnie')
        advance_wizard(app, 5, 'Zakończ', message='potwierdzenie zostało zarejestrowane')
        wizard.wait_not('exists', timeout=5)
        started_wizard = False
        opened.post_message(WM_CLOSE)
        details.wait_not('exists', timeout=5)
        opened_set = False
    except ReadError:
        raise
    except Exception:
        raise ReadError('ReadFailed') from None
    finally:
        # Close only windows opened by this job. Leave unexpected prompts
        # for the operator so the agent never confirms an unrelated dialog.
        if opened_set and interactive_desktop():
            with suppress(Exception):
                if started_wizard and wizard.exists(timeout=0):
                    wizard.wrapper_object().post_message(WM_CLOSE)
                    wizard.wait_not('exists', timeout=3)
                if details.exists(timeout=0):
                    details.wrapper_object().post_message(WM_CLOSE)


def uia_grid_metadata(info):
    kind = info.control_type
    if kind not in ('DataGrid', 'Table', 'List') and not (
            kind == 'Pane' and 'grid' in (info.class_name or '').casefold()):
        return {}

    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    patterns = []
    for name in ('Grid', 'Table', 'Selection', 'LegacyIAccessible'):
        try:
            get_elem_interface(info.element, name)
        except NoPatternInterfaceError:
            continue
        patterns.append(name)
    return {'uia_patterns': patterns}


def describe(window):
    """Report locator metadata only, without titles, cell values or screenshots."""
    controls = []
    for item in window.wrapper_object().descendants(depth=4):
        info = item.element_info
        kind = getattr(info, 'control_type', None)
        class_name = info.class_name
        if kind not in ('TabItem', 'DataGrid', 'Table', 'List', 'Pane') and class_name != 'SysListView32':
            continue
        control = {'control_type': kind, 'class_name': class_name,
                   'auto_id': getattr(info, 'automation_id', None),
                   'control_id': getattr(info, 'control_id', None)}
        if kind is not None:
            control.update(uia_grid_metadata(info))
        controls.append(control)
    return controls
