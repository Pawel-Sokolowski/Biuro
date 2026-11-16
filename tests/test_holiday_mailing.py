"""Eligibility, individual civil partners and last-moment mailing rechecks (CI only)."""
import json
from unittest import TestCase
from unittest.mock import patch

from tests.base import DbTestCase
from app.companies import holiday_eligibility as rules
from app.db.dao_app_settings import set_setting
from app.utils.crypto import encrypt_password
from app.email.bulk_mail import BulkMailPlan,send_bulk_batch

MONTH = '2026-12'
NIP = '1112223344'


def confirmed(nip=NIP, **changes):
    return {**dict.fromkeys(rules.CONDITIONS,True), 'payer_nip':nip,'month':MONTH,
            'insurance_code':'05 10 00','insured_count':1,'verified_month':'2026-11','evidence_hash':rules.evidence_hash([]), **changes}


class EligibilityRulesTests(TestCase):
    def decide(self,profile=None,rows=(),month=MONTH):
        return rules.assess(profile if profile is not None else confirmed(),list(rows),rules.context(month),NIP,now_month='2026-11')

    def test_insurance_code_is_only_one_of_the_required_conditions(self):
        for code in ('0510','0512','0570','0572','0590','0592'):
            self.assertEqual(self.decide(confirmed(insurance_code=code+'00'))[0],'eligible')
            self.assertEqual(self.decide({'payer_nip':NIP,'month':MONTH,'insurance_code':code})[0],'review')
        for name in rules.CONDITIONS:
            self.assertNotEqual(self.decide(confirmed(**{name:None}))[0],'eligible')
            self.assertNotEqual(self.decide(confirmed(**{name:False}))[0],'eligible')

    def test_health_only_krs_employee_and_relief_titles_are_excluded(self):
        for code in ('05 40 00','054300','011000','051400','057400','059400'):
            self.assertEqual(self.decide(confirmed(insurance_code=code))[0],'ineligible')
        self.assertEqual(self.decide(confirmed(insured_count=10))[0],'eligible')
        self.assertEqual(self.decide(confirmed(insured_count=11))[0],'ineligible')
        self.assertEqual(self.decide(confirmed(insured_count=None))[0],'review')

    def test_active_rws_overrides_manual_unused_and_inactive_is_not_refusal(self):
        row = {'status_zrodlowy':'A','source_id':1,'miesiac':'12'}
        self.assertEqual(self.decide(rows=[row])[0],'used')
        inactive = {**row,'status_zrodlowy':'N'}
        self.assertEqual(self.decide(profile={},rows=[inactive])[0],'review')
        self.assertEqual(self.decide(confirmed(evidence_hash=rules.evidence_hash([inactive])),[inactive])[0],'eligible')
        self.assertEqual(self.decide(confirmed(),[inactive])[0],'review')
        self.assertEqual(self.decide(confirmed(evidence_hash=rules.evidence_hash([{**row,'status_zrodlowy':'?'}])),[{**row,'status_zrodlowy':'?'}])[0],'review')

    def test_monthly_facts_and_income_years_follow_application_not_benefit_year(self):
        period = rules.context('2027-01')
        self.assertEqual(period['application_month'],'2026-12')
        self.assertEqual(period['reference_month'],'2026-11')
        self.assertEqual(period['income_years'],[2024,2025])
        self.assertEqual(self.decide(confirmed(month='2027-01'),month='2027-01')[0],'review')
        self.assertEqual(self.decide(month='2026-11')[0],'ineligible')
        self.assertEqual(self.decide(confirmed(payer_nip='9999999999'))[0],'review')
        self.assertEqual(self.decide(confirmed(month='2026-10'))[0],'review')
        self.assertEqual(self.decide(confirmed(verified_month='2026-10'))[0],'review')


class HolidayMailingDataTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.clock = patch.object(rules,'current_month',return_value='2026-11')
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.company = self.make_company('JDG test',nip=NIP,adres_email='client@example.com',forma_dzialalnosci='JDG')

    def save(self,company=None,person='company',revision=0,**changes):
        values = {**dict.fromkeys(rules.CONDITIONS,True),'insurance_code':'051000','insured_count':1,**changes}
        return rules.save_conditions(1,company or self.company,MONTH,person,values,revision)

    def candidates(self):
        return rules.list_candidates(1,MONTH)['companies']

    def test_no_rws_does_not_mean_entitled_until_verified_and_changes_are_audited(self):
        company = next(c for c in self.candidates() if c['id']==self.company)
        self.assertEqual(company['holiday_addresses']['eligible'],'')
        self.assertEqual(company['holiday_addresses']['review'],'client@example.com')
        self.save()
        company = next(c for c in self.candidates() if c['id']==self.company)
        self.assertEqual(company['holiday_addresses']['eligible'],'client@example.com')
        saved = rules.read_conditions(1,self.company,MONTH)['people'][0]
        self.assertEqual(saved['values']['actor_id'],1)
        self.assertEqual(saved['values']['insured_count'],1)
        with self.assertRaises(rules.EligibilityConflict):
            self.save()
        self.save(revision=1,insured_count=11)
        self.assertEqual(next(c for c in self.candidates() if c['id']==self.company)['holiday_addresses']['eligible'],'')

    def test_civil_partners_are_individual_and_only_the_unused_owner_gets_mail(self):
        from app.db.dao_companies import save_partner,get_partners
        company = self.make_company('SC test',nip='5555555555',forma_dzialalnosci='Spółka cywilna')
        for name,nip,email in [('Pierwszy','9999999999','first@example.com'),('Drugi','8888888888','second@example.com')]:
            save_partner(company,{'imie_pierwsze':name,'nazwisko':'Test','nip':nip,'email':email})
        for person in get_partners(company):
            self.save(company,str(person['id']))
        raw = [{'nip':'9999999999','source_id':1,'status_zrodlowy':'A','rok':2026,'miesiac':'10'}]
        set_setting('platnik_holidays_2026',json.dumps({'rows_enc':encrypt_password(json.dumps(raw))}))
        row = next(c for c in self.candidates() if c['id']==company)
        self.assertEqual(row['holiday_addresses']['eligible'],'second@example.com')
        self.assertEqual(row['holiday_people'][0]['status'],'used')
        self.assertEqual(row['holiday_people'][1]['status'],'eligible')
        plan = BulkMailPlan(1,10,'office@example.com','Wakacje ZUS','Treść',
                            ((company,('second@example.com',)),),holiday_month=MONTH)
        with patch('app.email.bulk_mail.get_email_accounts_for_employee',return_value=[{'id':10,'email':plan.sender}]), \
             patch('app.email.bulk_mail.send_email_smtp',return_value=(True,'OK')) as smtp:
            self.assertTrue(send_bulk_batch(plan,plan.recipients)['ok'])
        self.assertEqual(smtp.call_args.kwargs['bcc'],['second@example.com'])

    def test_permissions_and_owner_identity_cannot_be_selected_from_another_company(self):
        with patch.object(rules,'allowed_tabs',return_value=[]):
            with self.assertRaises(PermissionError):
                rules.list_candidates(1,MONTH)
        with patch.object(rules,'assigned_company_ids',return_value=set()):
            with self.assertRaises(PermissionError):
                rules.read_conditions(1,self.company,MONTH)
        with self.assertRaises(ValueError):
            self.save(person='other-person')

    def test_new_rws_blocks_the_next_campaign_batch_without_smtp(self):
        self.save()
        plan = BulkMailPlan(1,10,'office@example.com','Wakacje ZUS','Treść',
                            ((self.company,('client@example.com',)),),holiday_month=MONTH)
        raw = [{'nip':NIP,'source_id':1,'status_zrodlowy':'A','rok':2026,'miesiac':'12'}]
        set_setting('platnik_holidays_2026',json.dumps({'rows_enc':encrypt_password(json.dumps(raw))}))
        with patch('app.email.bulk_mail.get_email_accounts_for_employee',return_value=[{'id':10,'email':plan.sender}]), \
             patch('app.email.bulk_mail.send_email_smtp') as smtp:
            result = send_bulk_batch(plan,plan.recipients)
        self.assertTrue(result['blocked'])
        smtp.assert_not_called()
