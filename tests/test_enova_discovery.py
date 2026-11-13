from unittest import TestCase
from unittest.mock import Mock, patch
from app.integrations.enova.discovery import propose, discover, mapping_review
from app.integrations.enova.odbc_support import SqlOperationError


class EnovaDiscoveryTests(TestCase):
    def test_unrecognized_metadata_and_owner_nips_remain_visible_for_manual_mapping(self):
        root, no_schema, no_nip, unknown_company = Mock(), Mock(), Mock(), Mock()
        root.cursor.return_value.fetchmany.return_value = [('NoSchema',), ('NoNip',), ('UnknownCompany',)]
        no_schema.cursor.return_value.fetchone.return_value = [None]
        no_nip.cursor.return_value.fetchone.return_value = [20]
        no_nip.cursor.return_value.fetchmany.return_value = [(None,)]
        unknown_company.cursor.return_value.fetchone.return_value = [20]
        unknown_company.cursor.return_value.fetchmany.return_value = [('9999999999',)]
        driver = Mock()
        driver.connect.side_effect = [root, no_schema, no_nip, unknown_company]
        with patch('app.integrations.enova.discovery.load_odbc', return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver', return_value='ODBC Driver 18 for SQL Server'):
            result = discover({'server': 'ENOVA,49848', 'username': 'reader'}, password='secret')
        review = mapping_review([{'id': 1, 'nip': '1112223344'}], {}, result)
        self.assertEqual([row['database'] for row in review], ['NoSchema', 'NoNip', 'UnknownCompany'])
        self.assertTrue(all(row['reason'] and row['company_id'] is None for row in review))
        self.assertIn('struktur', review[0]['reason'])
        self.assertIn('NIP', review[1]['reason'])
        self.assertIn('Brak firmy', review[2]['reason'])
        self.assertEqual(review[2]['nips'], ['9999999999'])

    def test_review_explains_duplicate_nips_and_keeps_existing_manual_links(self):
        companies = [{'id': 1, 'nip': '1112223344'}, {'id': 2, 'nip': '2223334455'},
                     {'id': 3, 'nip': '2223334455'}]
        result = {'errors': [], 'matches': {'1112223344': ['A', 'COPY'], '2223334455': ['DUP']},
                  'databases': [{'database': name, 'nips': [nip], 'reason': ''}
                                for name, nip in [('A', '1112223344'), ('COPY', '1112223344'), ('DUP', '2223334455')]]}
        review = mapping_review(companies, {1: 'a'}, result)
        self.assertEqual(review[0]['company_id'], 1)
        self.assertIn('Kilka baz', review[1]['reason'])
        self.assertIn('powielony', review[2]['reason'])

    def test_122_databases_use_full_query_timeout_and_report_each_completed_read(self):
        root = Mock()
        root.cursor.return_value.fetchmany.return_value = [(f'Client{i:03d}',) for i in range(122)]
        connections = []
        for i in range(122):
            connection = Mock()
            cursor = Mock(spec_set=['execute','fetchone','fetchmany'])
            cursor.fetchone.return_value = [20]
            cursor.fetchmany.return_value = [(str(1112223344+i),)]
            connection.cursor.return_value = cursor
            connections.append(connection)
        driver = Mock()
        driver.connect.side_effect = [root,*connections]
        progress = Mock()
        with patch('app.integrations.enova.discovery.load_odbc',return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver',return_value='ODBC Driver 18 for SQL Server'):
            result = discover({'server':r'ENOVA\SQLEXPRESS','database':'Biuro_rachunkowe','username':'reader'},password='secret',progress=progress)
        self.assertEqual(result['visible'],122)
        self.assertEqual(result['scanned'],122)
        self.assertEqual(len(result['matches']),122)
        self.assertEqual(result['errors'],[])
        self.assertEqual(driver.connect.call_count,123)
        self.assertNotIn('HAS_DBACCESS',root.cursor.return_value.execute.call_args.args[0])
        for connection in [root,*connections]:
            self.assertEqual(connection.timeout,120)
            connection.close.assert_called_once()
        progress.assert_any_call(0,0,'Biuro_rachunkowe')
        self.assertEqual(progress.call_args.args,(122,122,'Client121'))
        self.assertTrue(all(call.kwargs['timeout']==15 for call in driver.connect.call_args_list))

    def test_only_unique_normalized_nips_fill_empty_cells(self):
        companies = [{'id':1,'nip':'PL 111-222-33-44'},{'id':2,'nip':'2223334455'},{'id':3,'nip':'3334445566'}]
        result = {'errors':[], 'matches':{'1112223344':['ALFA'],'2223334455':['BETA','BETA_BACKUP'],'3334445566':['NEW']}}
        proposals,conflicts = propose(companies,{1:'',2:'',3:'MANUAL'},result)
        self.assertEqual(proposals,{1:'ALFA'})
        self.assertIn('BETA_BACKUP',conflicts[0])
        self.assertNotIn(3,proposals)

    def test_duplicate_company_nips_and_partial_scans_never_autofill(self):
        companies = [{'id':1,'nip':'1112223344'},{'id':2,'nip':'1112223344'}]
        result = {'errors':[], 'matches':{'1112223344':['ALFA']}}
        self.assertEqual(propose(companies,{},result)[0],{})
        result['errors']=['Inaccessible copy']
        self.assertEqual(propose(companies[:1],{},result)[0],{})

    def test_scanner_uses_owner_identity_not_contractors_and_closes_connections(self):
        root, company = Mock(), Mock()
        root.cursor.return_value.fetchmany.return_value = [('ALFA',)]
        company.cursor.return_value.fetchone.return_value = [20]
        company.cursor.return_value.fetchmany.return_value = [('PL111-222-33-44',)]
        driver = Mock()
        driver.connect.side_effect = [root,company]
        with patch('app.integrations.enova.discovery.load_odbc',return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver',return_value='ODBC Driver 18 for SQL Server'):
            result = discover({'server':r'ENOVA\SQLEXPRESS','database':'Biuro_rachunkowe','username':'reader'},password='secret')
        self.assertEqual(result['matches'],{'1112223344':['ALFA']})
        company.cursor.return_value.execute.assert_any_call('SELECT DISTINCT DeklaracjeNIP FROM dbo.OddzialyFirmy')
        root.close.assert_called_once()
        company.close.assert_called_once()
        self.assertEqual(driver.connect.call_args.kwargs['attrs_before'],{101:1})
        self.assertIn('DATABASE={Biuro_rachunkowe}',driver.connect.call_args_list[0].args[0])
        self.assertIn('DATABASE={ALFA}',driver.connect.call_args_list[1].args[0])

    def test_selected_main_database_failure_is_reported_without_falling_back_to_master(self):
        driver = Mock()
        driver.connect.side_effect = Exception('42000','Cannot open database (4060)')
        with patch('app.integrations.enova.discovery.load_odbc',return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver',return_value='ODBC Driver 18 for SQL Server'), \
             self.assertRaises(SqlOperationError) as caught:
            discover({'server':r'ENOVA\SQLEXPRESS','database':'Biuro_rachunkowe','username':'reader'},password='secret')
        self.assertEqual(caught.exception.diagnostic['database'],'Biuro_rachunkowe')
        self.assertEqual(driver.connect.call_count,1)
        self.assertIn('DATABASE={Biuro_rachunkowe}',driver.connect.call_args.args[0])

    def test_master_connection_error_keeps_actual_state_and_redacts_password(self):
        driver = Mock()
        driver.connect.side_effect = Exception('08001','Instance unavailable; PWD={private;pass}; private;pass')
        with patch('app.integrations.enova.discovery.load_odbc',return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver',return_value='ODBC Driver 18 for SQL Server'), \
             self.assertRaises(SqlOperationError) as caught:
            discover({'server':r'ENOVA\SQLEXPRESS','username':'sa'},password='private;pass')
        error = caught.exception
        self.assertEqual(error.diagnostic['sqlstate'],'08001')
        self.assertEqual(error.diagnostic['database'],'master')
        self.assertIn('Instance unavailable',error.diagnostic['details'])
        self.assertNotIn('private;pass',str(error.diagnostic))

    def test_master_query_permission_error_is_distinct_from_login_and_closes_connection(self):
        root = Mock()
        root.cursor.return_value.execute.side_effect = Exception('42000','SELECT permission denied (229)')
        driver = Mock()
        driver.connect.return_value = root
        with patch('app.integrations.enova.discovery.load_odbc',return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver',return_value='ODBC Driver 17 for SQL Server'), \
             self.assertRaises(SqlOperationError) as caught:
            discover({'server':r'ENOVA\SQLEXPRESS','username':'reader'},password='secret')
        self.assertEqual(caught.exception.diagnostic['sqlstate'],'42000')
        self.assertIn('229',caught.exception.diagnostic['details'])
        root.close.assert_called_once()

    def test_partial_company_scan_keeps_redacted_diagnostics_and_blocks_automatic_assignment(self):
        root,company = Mock(),Mock()
        root.cursor.return_value.fetchmany.return_value = [('ALFA',)]
        company.cursor.return_value.execute.side_effect = Exception('42S22','Invalid column; PWD=secret;')
        driver = Mock()
        driver.connect.side_effect = [root,company]
        with patch('app.integrations.enova.discovery.load_odbc',return_value=driver), \
             patch('app.integrations.enova.discovery.resolve_driver',return_value='ODBC Driver 18 for SQL Server'):
            result = discover({'server':r'ENOVA\SQLEXPRESS','username':'reader'},password='secret')
        self.assertIn('42S22',result['errors'][0])
        self.assertIn('Invalid column',result['errors'][0])
        self.assertNotIn('secret',result['errors'][0])
        self.assertEqual(propose([{'id':1,'nip':'1112223344'}],{},result)[0],{})
        root.close.assert_called_once()
        company.close.assert_called_once()
