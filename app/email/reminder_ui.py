"""Shared preview/send controller for manual reminder buttons."""
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QDialog, QMessageBox, QInputDialog
from app.utils.background import TaskRunner,CommandRunner
from app.email.compose_dialog import ComposeDialog
from app.email.email_storage import get_email_accounts_for_employee
from app.services.manual_reminders import prepare,send


class ManualReminderController(QObject):
    def __init__(self,owner,actor_id,button):
        super().__init__(owner)
        self.owner,self.actor_id,self.button = owner,actor_id,button
        self.reader,self.writer = TaskRunner(self),CommandRunner(self)
        self.busy = False

    def open(self,kind,target_id,month=None):
        if self.busy:
            return
        self.busy = True
        self.button.setEnabled(False)
        def load():
            plan = prepare(kind,target_id,self.actor_id,month)
            accounts = get_email_accounts_for_employee(self.actor_id)
            if not accounts:
                raise ValueError('Przypisz pracownikowi konto pocztowe w ustawieniach, aby wysyłać przypomnienia.')
            return plan,accounts
        self.reader.submit(load,self.review,self.failed)

    def review(self,result):
        plan,accounts = result
        recipients = plan['recipients']
        if not recipients and plan.get('recipient_choices'):
            address, accepted = QInputDialog.getItem(self.owner,'Odbiorca przypomnienia',
                'Wybierz kontakt firmy '+plan['context']['firma']+':',plan['recipient_choices'],0,False)
            if not accepted:
                self.finish()
                return
            recipients = [address]
        dialog = ComposeDialog(self.owner,to=', '.join(recipients),
                               subject=plan['subject'],body=plan['body'],
                               title='Przypomnienie — sprawdź i wyślij' if recipients else 'Przypomnienie — uzupełnij odbiorcę i wyślij',
                               pracownik_id=self.actor_id,accounts=accounts,
                               account_id=accounts[0]['id'],from_addr=accounts[0]['email'])
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            self.finish()
            return
        message = dialog.values()
        try:
            self.writer.submit(lambda: send(plan,self.actor_id,message),self.sent,self.failed)
        except Exception as error:
            self.failed(error)

    def finish(self):
        self.busy = False
        self.button.setEnabled(True)

    def sent(self,result):
        self.finish()
        if result['ok']:
            text = 'Wysłano przypomnienie.'
            if result.get('info') not in (None,'','OK'):
                text += '\n'+result['info']
            QMessageBox.information(self.owner,'Przypomnienie',text)
        else:
            QMessageBox.warning(self.owner,'Przypomnienie','Nie potwierdzono wysyłki. Sprawdź folder Wysłane przed ponowieniem.\n'+str(result.get('info') or 'Brak potwierdzenia SMTP.').splitlines()[0])

    def failed(self,error):
        self.finish()
        message = str(error) if isinstance(error,(ValueError,PermissionError)) else 'Nie udało się przygotować lub potwierdzić przypomnienia. Sprawdź połączenie i folder Wysłane.'
        QMessageBox.warning(self.owner,'Przypomnienie',message)
