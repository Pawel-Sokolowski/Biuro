from unittest import TestCase
from unittest.mock import Mock, patch
from app.integrations.enova.discovery import propose, discover


class EnovaDiscoveryTests(TestCase):
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
            result = discover({'server':r'192.168.1.16\SQLEMKA','username':'reader'},password='secret')
        self.assertEqual(result['matches'],{'1112223344':['ALFA']})
        company.cursor.return_value.execute.assert_any_call('SELECT DISTINCT DeklaracjeNIP FROM dbo.OddzialyFirmy')
        root.close.assert_called_once()
        company.close.assert_called_once()
        self.assertEqual(driver.connect.call_args.kwargs['attrs_before'],{101:1})
