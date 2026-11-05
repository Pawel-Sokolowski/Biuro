import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.db.dao_address_book import delete_contact, find_contact, import_contacts, list_contacts, save_contact
from app.email.address_book import CONTACT_FIELDS, ENGLISH_HEADERS, contact_mailbox, normalize_contact, read_contacts_csv, split_recipients
from tests.base import DbTestCase


class CsvContactTests(unittest.TestCase):
    def read(self, rows, encoding='utf-8-sig', delimiter=',', headers=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'contacts.csv'
            with path.open('w', encoding=encoding, newline='') as stream:
                writer = csv.writer(stream, delimiter=delimiter)
                writer.writerow(headers or [label for _field, label in CONTACT_FIELDS])
                writer.writerows(rows)
            return read_contacts_csv(path)

    def row(self, **values):
        return [values.get(field, '') for field, _label in CONTACT_FIELDS]

    def test_polish_csv_keeps_every_column_quotes_notes_and_leading_zeros(self):
        values = {field: 'wartość ' + field for field, _ in CONTACT_FIELDS}
        values.update(email='jan@example.com', additional_email='other@example.com', first_name='Łukasz',
                       last_name='Żółć', display_name='Firma, "Oddział"', phone_work='012345', notes='Pierwsza linia,\nDruga linia')
        result = self.read([self.row(**values)])
        self.assertEqual(len(result['contacts']), 1)
        self.assertEqual(result['contacts'][0], values)
        self.assertEqual(result['issues'], [])

    def test_utf16_semicolon_and_polish_windows_encoding_are_supported(self):
        for encoding in ('utf-16', 'cp1250'):
            with self.subTest(encoding=encoding):
                result = self.read([self.row(email='a@example.com', display_name='Łukasz Żółć')], encoding=encoding, delimiter=';')
                self.assertEqual(result['contacts'][0]['display_name'], 'Łukasz Żółć')

    def test_duplicates_fill_empty_fields_and_invalid_records_are_reported(self):
        result = self.read([self.row(email='a@example.com', display_name='Original'),
                            self.row(email='A@example.com', display_name='Replacement', phone_mobile='0123'),
                            self.row(email='not-an-address')])
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual(len(result['issues']), 1)
        self.assertEqual(result['contacts'][0]['display_name'], 'Original')
        self.assertEqual(result['contacts'][0]['phone_mobile'], '0123')

    def test_english_headers_and_secondary_only_contact(self):
        result = self.read([self.row(additional_email='secondary@example.com', nickname='Nick')], headers=ENGLISH_HEADERS)
        self.assertEqual(result['contacts'][0]['email'], 'secondary@example.com')
        self.assertEqual(result['contacts'][0]['additional_email'], '')

    def test_unknown_columns_are_preserved_and_bad_row_width_is_not_guessed(self):
        result = self.read([['a@example.com', 'Ważna informacja'], ['b@example.com']],
                           headers=['Podstawowy e-mail', 'Dodatkowe pole'])
        self.assertEqual(result['contacts'][0]['extra_fields'], {'Dodatkowe pole': 'Ważna informacja'})
        self.assertEqual(len(result['issues']), 1)
        with self.assertRaises(ValueError):
            self.read([['Jan']], headers=['Imię'])

    def test_named_recipients_with_commas_and_semicolons_stay_whole(self):
        contact = {'display_name': 'Kowalski, Jan; księgowy', 'email': 'jan@example.com'}
        text = contact_mailbox(contact) + '; next@example.com'
        self.assertEqual(split_recipients(text), ['jan@example.com', 'next@example.com'])


class SharedContactTests(DbTestCase):
    def test_contact_is_shared_between_employees_and_keeps_all_csv_details(self):
        other = self.make_employee('contact-worker@example.com')
        values = {'email': 'global@example.com', 'display_name': 'Globalny', 'notes': 'Notatka\nDruga linia',
                  'phone_work': '00123', 'home_city': 'Łódź', 'birth_month': '05', 'extra_fields': {'Własne': 'Pole'}}
        saved = save_contact(1, values)
        found = find_contact(other, 'GLOBAL@example.com')
        self.assertEqual(found['id'], saved['id'])
        for key, value in values.items():
            self.assertEqual(found[key], value)
        found['nickname'] = 'Wspólny'
        updated = save_contact(other, found, found['id'], found['version'])
        self.assertEqual(find_contact(1, values['email'])['nickname'], 'Wspólny')
        self.assertGreater(updated['version'], found['version'])

    def test_primary_email_is_unique_case_insensitively(self):
        save_contact(1, {'email': 'A@example.com'})
        with self.assertRaises(ValueError):
            save_contact(1, {'email': 'a@EXAMPLE.com'})

    def test_search_uses_names_aliases_company_and_both_addresses(self):
        contact = save_contact(1, {'email': 'main@example.com', 'additional_email': 'extra@example.com',
                                  'first_name': 'Jan', 'last_name': 'Kowalski', 'nickname': 'Janko', 'company': 'Organizacja'})
        for query in ('Jan Kow', 'janko', 'EXTRA@', 'Organizacja', 'main@'):
            with self.subTest(query=query):
                self.assertEqual(list_contacts(1, query)[0]['id'], contact['id'])
        self.assertEqual(list_contacts(1, '%'), [])

    def test_csv_reimport_is_idempotent_and_preserves_manual_changes(self):
        original = save_contact(1, {'email': 'a@example.com', 'display_name': 'Ręczna nazwa', 'notes': 'Ręczna notatka'})
        incoming = normalize_contact({'email': 'A@example.com', 'display_name': 'CSV nazwa', 'notes': 'CSV notatka', 'phone_home': '0123'})
        counts = import_contacts(1, [incoming, {'email': 'new@example.com'}])
        self.assertEqual(counts, {'created': 1, 'updated': 1, 'unchanged': 0})
        updated = find_contact(1, 'a@example.com')
        self.assertEqual(updated['display_name'], original['display_name'])
        self.assertEqual(updated['notes'], original['notes'])
        self.assertEqual(updated['phone_home'], '0123')
        self.assertEqual(import_contacts(1, [incoming])['unchanged'], 1)

    def test_stale_edits_and_deletes_cannot_overwrite_another_worker(self):
        original = save_contact(1, {'email': 'a@example.com', 'display_name': 'Initial'})
        updated = save_contact(1, {**original, 'display_name': 'New'}, original['id'], original['version'])
        with self.assertRaises(ValueError):
            save_contact(1, {**original, 'display_name': 'Stale'}, original['id'], original['version'])
        with self.assertRaises(ValueError):
            delete_contact(1, original['id'], original['version'])
        self.assertEqual(find_contact(1, original['email'])['display_name'], 'New')
        delete_contact(1, updated['id'], updated['version'])
        self.assertIsNone(find_contact(1, original['email']))

    def test_import_rolls_back_all_rows_if_any_contact_is_invalid(self):
        with self.assertRaises(ValueError):
            import_contacts(1, [{'email': 'valid@example.com'}, {'email': 'invalid'}])
        self.assertEqual(list_contacts(1), [])

    def test_inactive_or_unauthorized_employee_cannot_read_or_write(self):
        from app.db.dao_conn import get_conn
        other = self.make_employee('inactive@example.com')
        with get_conn() as conn:
            conn.execute('UPDATE pracownicy SET aktywny = 0 WHERE id = %s', (other,))
        for actor in (None, other, 999999):
            with self.subTest(actor=actor), self.assertRaises(PermissionError):
                list_contacts(actor)
        with mock.patch('app.db.dao_address_book.allowed_tabs', return_value=[]), self.assertRaises(PermissionError):
            save_contact(1, {'email': 'blocked@example.com'})

    def test_bootstrap_and_alembic_upgrade_preserve_contacts(self):
        from app.db.schema import init_db, schema_is_current, upgrade_database
        saved = save_contact(1, {'email': 'a@example.com'})
        init_db()
        upgrade_database()
        self.assertTrue(schema_is_current())
        self.assertEqual(find_contact(1, 'a@example.com')['id'], saved['id'])
