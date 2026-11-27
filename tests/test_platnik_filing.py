"""Synthetic wizard state tests. CI does not send declarations."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, patch

from app.integrations.platnik import gui_filing as filing, gui_pin


GROUP_SEND = 'Kreator grupowego wysyłania zestawów do ZUS'
GROUP_RECEIPT = 'Kreator grupowego potwierdzania zestawów'
EXE = Path('p2.exe')
PID = 91


def window(title='', *, classname='TfKreatorWysylki', handle=1, enabled=True):
    control = Mock(handle=handle, element_info=SimpleNamespace(class_name=classname))
    control.window_text.return_value = title
    control.is_enabled.return_value = enabled
    control.process_id.return_value = PID
    return control


class FilingTests(TestCase):
    def test_selected_group_send_advances_once_and_uses_the_confirmed_signing_report(self):
        set_selection = window(f'{GROUP_SEND} - krok 2 / 10')
        signing = window(f'{GROUP_SEND} - krok 8 / 10')
        complete = window(f'{GROUP_SEND} - krok 10 / 10')
        plan, step = filing.identify(set_selection)
        session = filing.FilingSession(EXE, PID, 1, plan, step)
        button = Mock()
        with patch.object(filing, 'process_image', return_value=EXE), \
             patch.object(filing, 'desktop_windows',
                          side_effect=[[set_selection], [set_selection], [signing], [complete], []]), \
             patch.object(filing, 'dialog_button', return_value=button) as find, \
             patch.object(filing, 'press_button') as press, patch.object(filing, 'sleep'):
            self.assertEqual(session.advance(), {'closed': True, 'finished': True})
        self.assertIn(call(signing, 'Raport z podpisywania'), find.call_args_list)
        self.assertEqual(press.call_args_list, [
            call(set_selection, button), call(signing, button), call(complete, button),
        ])

    def test_group_receipts_register_after_download(self):
        selection = window(f'{GROUP_RECEIPT} - krok 2 / 6')
        download = window(f'{GROUP_RECEIPT} - krok 3 / 6')
        register = window(f'{GROUP_RECEIPT} - krok 4 / 6')
        complete = window(f'{GROUP_RECEIPT} - krok 6 / 6')
        plan, step = filing.identify(selection)
        session = filing.FilingSession(EXE, PID, 1, plan, step)
        button = Mock()
        with patch.object(filing, 'process_image', return_value=EXE), \
             patch.object(filing, 'desktop_windows',
                          side_effect=[[selection], [download], [register], [complete], []]), \
             patch.object(filing, 'dialog_button', return_value=button) as find, \
             patch.object(filing, 'press_button') as press, patch.object(filing, 'sleep'):
            self.assertTrue(session.advance()['finished'])
        self.assertIn(call(register, 'Raport z pobierania'), find.call_args_list)
        self.assertEqual(press.call_args_list, [call(selection, button), call(register, button), call(complete, button)])

    def test_pin_is_used_once_per_signing_step_and_never_for_other_prompts(self):
        plan = filing.WIZARDS[GROUP_SEND.casefold()]
        session = filing.FilingSession(EXE, PID, 1, plan, 7)
        pin_dialog = window(classname='TFrmDlgNPin', handle=2)
        error_dialog = window(classname='#32770', handle=3)
        with patch.object(filing, 'read_pin', return_value='test-only') as read, \
             patch.object(filing, 'submit_pin') as enter:
            self.assertIsNone(session._modal([pin_dialog]))
            self.assertIn('ponownie', session._modal([pin_dialog]))
            self.assertIn('komunikat', session._modal([error_dialog]))
            session.step = 8
            self.assertIn('komunikat', session._modal([pin_dialog]))
        read.assert_called_once_with()
        enter.assert_called_once_with(pin_dialog, 'test-only')

    def test_cannot_start_at_the_final_send_step(self):
        wizard = window(f'{GROUP_SEND} - krok 8 / 10')
        with patch('app.integrations.platnik.gui_agent.default_profile', return_value=Mock(is_file=lambda: False)), \
             patch.object(filing, 'discover_executable', return_value=EXE), \
             patch.object(filing, 'desktop_windows', return_value=[wizard]), \
             patch.object(filing, 'process_image', return_value=EXE), \
             self.assertRaisesRegex(ValueError, 'wybierz zestawy'):
            filing.start('send')

    def test_inline_pin_is_written_once_in_the_certificate_step(self):
        plan = filing.WIZARDS[GROUP_SEND.casefold()]
        session = filing.FilingSession(EXE, PID, 1, plan, 6)
        wizard = window(f'{GROUP_SEND} - krok 6 / 10')
        password = Mock()
        with patch.object(filing, 'pin_fields', return_value=[password]), \
             patch.object(filing, 'dialog_button', return_value=Mock()) as find, \
             patch.object(filing, 'read_pin', return_value='test-only') as read, \
             patch.object(filing, 'write_pin_field') as fill:
            self.assertIsNone(session._inline_pin(wizard))
            self.assertIn('Sprawdź certyfikat', session._inline_pin(wizard))
        find.assert_called_once_with(wizard, 'Wyświetl certyfikat')
        read.assert_called_once_with()
        fill.assert_called_once_with(password, 'test-only')

    def test_pin_uses_its_own_windows_credential(self):
        with patch.object(gui_pin.credential_store, 'save_named', return_value=True) as save, \
             patch.object(gui_pin.credential_store, 'delete_named') as delete, \
             patch.object(gui_pin.credential_store, 'read_named', side_effect=['test-only', None]) as read:
            gui_pin.save_pin('test-only')
            self.assertEqual(gui_pin.read_pin(), 'test-only')
            gui_pin.forget_pin()
        save.assert_called_once_with(gui_pin.PIN_TARGET, 'test-only', 'Płatnik certificate PIN')
        delete.assert_called_once_with(gui_pin.PIN_TARGET)
        self.assertEqual(read.call_args_list, [call(gui_pin.PIN_TARGET), call(gui_pin.PIN_TARGET)])
