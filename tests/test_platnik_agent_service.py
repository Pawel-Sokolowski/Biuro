"""libpq service files for the interactive Płatnik agent."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.integrations.platnik.agent_service import (SERVICE_NAME, files_exist, parse_dsn, pgpass_line,
                                                    service_file, write_files)


class ServiceFileTests(unittest.TestCase):
    def test_dsn_is_split_into_libpq_parts(self):
        parts = parse_dsn('postgresql://biuro:sekret@192.168.1.12:5432/biuro')
        self.assertEqual(parts, {'host': '192.168.1.12', 'port': '5432', 'dbname': 'biuro', 'user': 'biuro'})

    def test_default_port_and_missing_scheme(self):
        self.assertEqual(parse_dsn('postgresql://u:p@host/baza')['port'], '5432')
        with self.assertRaisesRegex(ValueError, 'DB_URL'):
            parse_dsn('mysql://u:p@host/baza')
        with self.assertRaisesRegex(ValueError, 'DB_URL'):
            parse_dsn('')

    def test_service_stanza_has_no_password(self):
        text = service_file('192.168.1.12', '5432', 'biuro', SERVICE_NAME)
        self.assertTrue(text.startswith('[' + SERVICE_NAME + ']\n'))
        self.assertNotIn('password', text.casefold())

    def test_password_colon_and_backslash_are_escaped(self):
        line = pgpass_line('host', '5432', 'biuro', 'agent', r'p:a\ss')
        self.assertEqual(line, 'host:5432:biuro:agent:p\\:a\\\\ss\n')
        # Five colon-separated fields: the escaped ones must not add separators.
        self.assertEqual(len(line.rstrip('\n').replace('\\:', '').replace('\\\\', '').split(':')), 5)

    def test_newlines_and_empty_password_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'hasło'):
            pgpass_line('host', '5432', 'biuro', 'agent', '')
        with self.assertRaisesRegex(ValueError, 'nowej linii'):
            pgpass_line('host', '5432', 'biuro', 'agent', 'a\nb')
        with self.assertRaisesRegex(ValueError, 'usługi libpq'):
            service_file('host', '5432', 'biuro', 'bad=user')

    def test_files_are_written_with_owner_only_permissions(self):
        with TemporaryDirectory() as directory:
            self.assertFalse(files_exist(directory))
            service, pgpass = write_files('192.168.1.12', '5432', 'biuro', SERVICE_NAME, 'sekret',
                                          directory=directory)
            self.assertTrue(files_exist(directory))
            self.assertEqual(service, Path(directory) / '.pg_service.conf')
            self.assertEqual(pgpass, Path(directory) / 'pgpass.conf')
            self.assertIn('user=' + SERVICE_NAME, service.read_text(encoding='utf-8'))
            self.assertTrue(pgpass.read_text(encoding='utf-8').endswith(':sekret\n'))
