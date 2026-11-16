"""Generate a reviewed, static operation catalogue and explicit boundary decorators."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = {}
READ = ('get_','list_','count_','find_','search_','format_','is_','can_','has_','choose_','invoice_payment_total',
        'monthly_','payments_for_','bank_import_summary','opis_','wybierz_','inferred_','company_declarations')
ACTORS = {'actor_id','current_user_id','user_id','uzytkownik_id'}
SKIP_DAO = {'dao_conn','dao_calendar_sync','dao_active_timers'}
DENY = {'login_employee','ensure_company_columns_exist','create_task_in_transaction','contracts_by_month',
        'set_google_event_id','set_caldav_uid','get_event_by_google_id','get_event_by_caldav_uid',
        'list_caldav_events','list_events_without_caldav','list_unsynced_events','remove_local_duplicates'}
# These helpers only format/filter snapshots already authorized by the server.
LOCAL_RENDERERS = {
    'app.db.dao_dashboard.format_duration',
    'app.db.dao_dashboard.format_event_time',
    'app.integrations.enova.accounting.documents',
    'app.integrations.enova.accounting.active_types',
    'app.integrations.enova.accounting.declaration_entries',
    'app.integrations.enova.accounting.declarations',
    'app.companies.checking_obligations.profile',
    'app.companies.checking_obligations.report_applies',
}
MANUAL = {
 'app.db.dao_conn':'database_context',
 'app.db.schema':'schema_is_current',
 'app.db.reset_data':'count_company_data delete_company_data',
 'app.db.dao_active_timers':'checkpoint recoverable claim',
 'app.security.authorization':'get_permissions is_administrator can allowed_tabs can_manage_invoices assigned_company_ids',
 'app.employees.authority_profiles':'load save',
 'app.companies.checking_obligations':'load profile save report_applies',
 'app.companies.holiday_eligibility':'list_candidates read_conditions save_conditions',
 'app.companies.registry_enrichment':'enrich_all_companies',
 'app.companies.company_lookup':'lookup_company_by_nip',
 'app.companies.ceidg_api':'get_ceidg_firma_by_nip',
 'app.companies.gus_api':'get_gus_firma_by_nip',
 'app.companies.krs_api':'get_krs_firma_by_nip',
 'app.notifications':'list_user_notifications',
 'app.preferences.department':'default_department',
 'app.preferences.mail_sync':'load_settings save_settings',
 'app.reports.reports_logic':'get_all_companies build_daily_report get_months_range get_imported_seconds get_hours_for_company_month get_extra_services_value get_extra_services_by_department build_summary_report build_report build_yearly_report compare_companies build_profit_report',
 'app.reports.import_dao':'import_work_file import_invoices_file list_employee_rates set_employee_rates delete_imported build_profitability_report',
 'app.reports.document_counts_xlsx':'import_document_counts_file',
 'app.chat.chat_model':'get_conversations get_messages add_message add_message_with_attachments get_attachments get_attachment_data edit_message get_new_messages get_latest_message_ids count_messages_after get_unread_counts mark_conversation_read create_conversation get_conversation get_participant_ids is_participant can_manage_conversation get_active_employees update_conversation delete_conversation ensure_direct_conversations get_direct_conversations get_chat_notification_messages',
 'app.services.services':'pobierz_uslugi_globalne pobierz_uslugi_firmy',
 'app.services.calculator':'oblicz_miesieczny_koszt',
 'app.services.pricing':'wybierz_cennik',
 'app.services.settings':'is_admin get_global_roles get_email_settings',
 'app.services.worker_settings':'load_settings save_settings',
 'app.services.worker_monitor':'snapshot',
 'app.services.checking_refresh':'enqueue latest',
 'app.services.email_reminders':'load_settings save_settings',
 'app.services.manual_reminders':'prepare send',
 'app.email.email_actions':'send_email_smtp send_batch_emails choose_copy_folder store_sent_copy add_filter mark_read mark_unread delete_email',
 'app.email.bulk_mail':'send_bulk_batch',
 'app.email.email_connection':'check_imap check_smtp',
 'app.email.email_rules':'import_thunderbird_filters apply_rules_to_folder',
 'app.email.signature':'get_template save_template get_employee_signature save_employee_phone get_saved_signature save_employee_signature default_sender_id set_default_sender_id',
 'app.email.email_sync':'list_folder_infos list_folders fetch_message_from_imap prefetch_messages prefetch_recent prefetch_all_messages fetch_headers backfill_refs sync_folder_and_prefetch_all append_message move_message delete_message sync_account mark_unread_remote archive_message sync_folder needs_initial_sync sync_and_prefetch mark_read_remote list_folders_from_imap_and_store fetch_headers_from_imap_and_store fetch_message_from_imap_and_store delete_email_remote move_email_and_get_sender move_emails create_folder rename_folder sync_folder_flags sync_requested_accounts sync_automatic_accounts',
 'app.email.email_storage':'mail_read_snapshot get_mailbox_generation get_account_info get_email_accounts_for_employee get_all_email_accounts get_email_filters_for_company add_email_filter delete_email_filter_for_company get_known_sender_addresses ensure_contact_email_filters get_folder_id get_folders_for_account account_has_headers get_cached_messages get_unread_counts get_visible_unread_counts get_new_unread_messages find_special_folder get_message_by_uid load_raw_message get_message_row_id update_message_read_flag mark_deleted search_messages get_cached_messages_by_folder_name get_assignment_map set_employee_assignment get_completed_map set_messages_completed count_completed_messages list_completed_rows count_assigned_by_employee count_unread_assigned_by_employee list_assigned_rows get_mail_page refresh_email_list resolve_assigned_message resolve_notification_message mail_notification_snapshot',
 'app.invoices.ksef_credentials':'import_pkcs12 import_cert_and_key clear_certificate get_certificate_info',
 'app.invoices.ksef_submission':'refresh_invoice_submission send_invoice_xml generate_invoice_xml send_invoice_document download_submitted_invoice process_invoice_batch',
 'app.invoices.ksef_sync':'assign_downloaded_invoice save_ksef_credentials is_configured sync_issued_invoices sync_month',
 'app.invoices.invoice_delivery':'delivery_plan queue_deliveries deliver_if_queued',
 'app.invoices.saldeo_correction':'confirm_saldeo_correction',
 'app.integrations.enova.accounting':'list_accounting documents active_types declaration_entries declarations',
 'app.integrations.enova.counting_rules':'load_rules effective_divisor save_rules',
 'app.integrations.enova.connection_settings':'load_connection save_connection check_connection',
 'app.integrations.enova.discovery':'discover',
 'app.integrations.enova.document_counts':'run_import',
 'app.integrations.enova.checking':'run_checking_import import_document_year',
 'app.integrations.ezla.settings':'load_settings save_settings',
 'app.integrations.ezla.sync':'run_sync load_reports',
 'app.integrations.ezla.preview':'preview_report preview_reports',
 'app.integrations.platnik.holidays':'load save check_connection import_year read_year',
 'app.integrations.platnik.holiday_tracking':'read_tracking_year save_field',
 'app.integrations.platnik.declarations':'import_months import_year read_year',
 'app.integrations.saldeo_web.settings':'load save',
 'app.integrations.saldeo_web.history':'list_history notification_batches',
 'app.kalendarz.caldav_sync':'get_config save_config is_configured test_connection list_calendars push_event delete_remote sync',
 'app.kalendarz.google_sync':'get_access save_access calendars_for has_calendar_access mark_event_pending save_client_credentials is_account_connected is_connected disconnect push_event delete_remote list_account_calendars test_connection sync',
 'app.utils.importers':'import_tax_offices_from_excel import_companies_from_excel_file_payloads import_companies_from_excel_file save_imported_payload import_companies_fast_from_excel_file import_cennik_from_excel import_ceny_za_dokument_from_excel preview_company_import prepare_company_import',
 'app.utils.import_assignments':'import_role_assignments_from_file',
 'app.utils.saldeo_contractors_import':'import_saldeo_contractors import_saldeo_company_names',
 'app.utils.saldeo_exporter':'export_saldeo_xlsx apply_default_main_pricing validate_saldeo_invoices',
 'app.utils.saldeo_status':'rescan_saldeo_emails apply_saldeo_emails',
 'app.utils.saldeo_push':'mark_documents_complete',
 'app.utils.vat_check':'check_company_vat',
 'app.utils.ceidg_suspension':'check_company_ceidg',
 'app.utils.backup_status':'get_backup_status save_backup_dates_to_companies stored_backup_dates',
 'app.utils.backup_mail':'check_backup_from_latest_mail refresh_backup_log_from_mail',
 'app.utils.system_mailbox':'get_system_account_id get_system_account save_system_account',
 'app.documents.contract_pack':'load_context office_context',
 'app.documents.power_of_attorney':'office_address office_email',
 'app.utils.crypto':'get_employee_info get_employee_assigned_companies get_employee_role is_admin',
 'app.remote.screen_data':'activity_rows invoice_entities invoice_filter_entities reportable_employees search_company_buyers reminder_preview_jobs company_identities check_ezla_service mail_view_snapshot reminder_history catalogue_count release_preparation google_client_metadata',
 'app.integrations.enova.schema_probe':'inspect_schema',
}
CLASSES = {'app.companies.company_logic':'CompanyLogic','app.monthly_view.monthly_view_logic':'MonthlyViewLogic',
           'app.invoices.faktury_logic':'FakturyLogic','app.admin.global_settings_logic':'GlobalSettingsLogic'}


def capability(module,name):
    read = name.startswith(READ) or name in {'load','profile','read_year','load_settings','snapshot','database_context','schema_is_current','load_connection','load_rules','effective_divisor'}
    if module=='app.security.authorization' or module=='app.utils.crypto' or module=='app.notifications' or module.startswith('app.preferences.'):
        return 'authenticated'
    if module.endswith('dao_app_settings'):
        return 'authenticated' if name.startswith(('get_','list_')) else 'admin'
    if module.endswith('dao_global_settings') or module.endswith('GlobalSettingsLogic'):
        if name in ('get_setting','get_user_permissions','get_user_role','list_employees','get_roles','get_role_tabs','get_global_pricing','get_pricing_kadry','get_extra_services','get_companies','get_all_companies','get_company'):
            return 'authenticated'
        return 'admin'
    if module.endswith('dao_employees'):
        if name=='get_email_assignments':
            return 'permission:can_manage_users'
        return 'authenticated' if name.startswith(('get_','list_')) else 'permission:can_manage_users'
    if module.endswith('dao_audit') or module in ('app.utils.system_mailbox','app.services.settings'):
        return 'admin'
    if module.endswith('dao_companies') or module.endswith('CompanyLogic'):
        return 'authenticated' if read or name in ('calculate_kadry_values','calculate_kadry_price') else 'permission:can_edit_companies'
    if module.endswith(('dao_timers','dao_active_timers')):
        return 'authenticated'
    if module=='app.db.reset_data':
        return 'admin'
    if module.endswith('MonthlyViewLogic'):
        return 'tab:Widok miesięczny|Klienci|Raporty'
    if module.startswith(('app.invoices.','app.db.dao_invoices','app.db.dao_payments')):
        if module=='app.invoices.ksef_credentials' or name.startswith('save_ksef'):
            return 'admin'
        return 'tab:Faktury'
    if module=='app.remote.screen_data':
        if name in ('reminder_history','reminder_preview_jobs','check_ezla_service','catalogue_count','release_preparation','google_client_metadata'):
            return 'admin'
        if name=='mail_view_snapshot':
            return 'tab:E-mail'
        if name.startswith('invoice_') or name=='search_company_buyers':
            return 'tab:Faktury'
        return 'authenticated' 
    if module.startswith('app.chat.'):
        return 'tab:Czat'
    if module.startswith(('app.email.','app.db.dao_email')):
        if name=='ensure_contact_email_filters':
            return 'permission:can_edit_companies'
        if module=='app.email.email_connection' or name=='save_template':
            return 'admin'
        return 'tab:E-mail'
    if module.startswith(('app.kalendarz.','app.db.dao_calendar')):
        return 'admin' if name.startswith(('save_config','save_access','save_client')) or name in ('disconnect','list_account_calendars','list_calendars','test_connection') else 'tab:Kalendarz'
    if module.startswith('app.reports.'):
        return 'admin' if name.startswith(('import_','set_employee_rates','delete_imported')) else 'tab:Raporty|Widok miesięczny'
    if module=='app.employees.authority_profiles':
        return 'permission:can_manage_users' if name=='save' else 'tab:Klienci|Widok miesięczny|Ustawienia'
    if module=='app.companies.checking_obligations':
        return 'permission:can_edit_companies' if name=='save' else 'authenticated'
    if module=='app.companies.holiday_eligibility':
        return 'tab:E-mail'
    if module.startswith(('app.integrations.enova.accounting','app.integrations.ezla.preview')) or module in ('app.integrations.ezla.sync','app.integrations.platnik.holiday_tracking','app.integrations.platnik.declarations'):
        return 'tab:Sprawdzanie|Klienci'
    if module.startswith('app.integrations.'):
        return 'authenticated' if read and name not in ('load_settings','load_connection') else 'admin'
    if module=='app.utils.backup_status' and name in ('get_backup_status','stored_backup_dates'):
        return 'authenticated'
    if module.startswith(('app.services.worker','app.services.email_reminders','app.utils.backup')):
        return 'admin'
    if module=='app.services.manual_reminders':
        return 'tab:Faktury|Widok miesięczny'
    if module.startswith(('app.utils.import','app.utils.saldeo_contractors','app.companies.registry_enrichment')):
        return 'admin'
    if module=='app.utils.saldeo_exporter':
        return 'tab:Faktury'
    if module.startswith('app.db.dao_cenniki') and not read:
        return 'admin'
    if module.startswith(('app.documents.','app.db.dao_cenniki','app.db.dao_kontrahenci','app.services.')):
        return 'authenticated'
    if module.startswith('app.db.dao_tasks'):
        return 'tab:Zadania|Widok miesięczny'
    return 'authenticated'


def spec_for(module,name,node,cls=None):
    args=[a.arg for a in node.args.posonlyargs+node.args.args+node.args.kwonlyargs if a.arg!='self']
    result={'module':module,'arguments':args,'capability':capability(module+'.'+cls if cls else module,name)}
    if cls:
        result.update({'class':cls,'method':name})
        if name=='__new__' and cls in ('CompanyLogic','GlobalSettingsLogic'):
            result['capability']='authenticated'
    else:
        result['function']=name
    actors=set(args)&ACTORS
    # These parameters are the actor in workflows, and a target only in user administration.
    target_admin = module in ('app.db.dao_employees','app.db.dao_global_settings','app.admin.global_settings_logic') and name not in ('get_user_permissions','get_user_role')
    if 'pracownik_id' in args and not target_admin and 'actor_id' not in args:
        actors.add('pracownik_id')
    if module in ('app.security.authorization','app.utils.crypto'):
        actors.update(set(args)&{'pracownik_id','employee_id','user_id'})
    if module=='app.db.dao_tasks' and 'created_by' in args:
        actors.add('created_by')
    if cls=='MonthlyViewLogic' and name=='__new__':
        actors.add('pracownik_id')
    if actors:
        result['actors']=sorted(actors)
    if module in ('app.db.dao_employees','app.email.email_storage') and name in ('get_email_accounts_for_employee','get_email_assignments','get_email_filters_for_employee'):
        result['self_only']=[k for k in ('pracownik_id','employee_id') if k in args]
    if module=='app.db.dao_active_timers' and 'employee' in args:
        actors.add('employee')
        result['actors']=sorted(actors)
    for param,label in (('callbacks',{'progress','progress_cb','on_progress','on_headers'}),('cancellation',{'stop','stop_event','cancel_event'})):
        if set(args)&label:
            result[param]=sorted(set(args)&label)
    files={}
    for arg in args:
        if arg in ('file_path','filepath','csv_path','xlsx_path','path','source_path','path_str','xml_path') and (name.startswith(('import_','preview_','prepare_company')) or name in ('add_document_attachment','send_invoice_xml')):
            files[arg]='input'
        elif arg=='paths' and name=='preview_reports':
            files[arg]='inputs'
        elif arg in ('target_path','output_path','destination') or arg=='path' and name.startswith('export_'):
            files[arg]='output'
        elif arg=='attachments' and module=='app.email.email_actions':
            files[arg]='inputs'
    if files:
        result['files']=files
    return result


for path in (ROOT/'app').rglob('*.py'):
    module=str(path.relative_to(ROOT)).removesuffix('.py').replace('/','.')
    if module.startswith('app.remote.') and module!='app.remote.screen_data':
        continue
    source=path.read_text(encoding='utf-8-sig')
    source=re.sub(r'^from app\.remote\.boundary import [^\n]+\n','',source,flags=re.M)
    source=re.sub(r'^@office_(?:endpoint|remote_class)\([^\n]+\)\n','',source,flags=re.M)
    tree=ast.parse(source)
    wanted=set(MANUAL.get(module,'').split())
    dao=module.startswith('app.db.dao_') and module.rsplit('.',1)[1] not in SKIP_DAO
    edits=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and (node.name in wanted or dao and not node.name.startswith('_') and node.name not in DENY):
            arguments={a.arg for a in node.args.args+node.args.kwonlyargs}
            optional_names={a.arg for a in node.args.args[-len(node.args.defaults):]} if node.args.defaults else set()
            optional_names.update(a.arg for a,default in zip(node.args.kwonlyargs,node.args.kw_defaults) if default is not None)
            if (arguments & {'conn','connection','cur','cursor'})-optional_names or node.name=='mail_read_snapshot':
                continue
            name=module+'.'+node.name
            if name in LOCAL_RENDERERS:
                continue
            OPERATIONS[name]=spec_for(module,node.name,node)
            line=min([node.lineno]+[d.lineno for d in node.decorator_list])-1
            edits.append((line,'@office_endpoint('+repr(name)+')\n'))
        elif isinstance(node,ast.ClassDef) and node.name==CLASSES.get(module):
            class_name=module+'.'+node.name
            edits.append((min([node.lineno]+[d.lineno for d in node.decorator_list])-1,'@office_remote_class('+repr(class_name)+')\n'))
            initializer=next((m for m in node.body if isinstance(m,ast.FunctionDef) and m.name=='__init__'),None)
            if initializer is None:
                initializer=ast.parse('def create(): pass').body[0]
            OPERATIONS[class_name+'.__new__']=spec_for(module,'__new__',initializer,node.name)
            for method in node.body:
                if not isinstance(method,ast.FunctionDef) or method.name.startswith('__') or method.name.startswith(('open_','more_option_')) or method.name in ('_get_conn','ensure_schema_minimum','ensure_role_columns','_finish_timer','_auto_check_suspension'):
                    continue
                OPERATIONS[class_name+'.'+method.name]=spec_for(module,method.name,method,node.name)
    if edits:
        lines=source.splitlines(keepends=True)
        for line,text in sorted(edits,reverse=True):
            lines.insert(line,text)
        header=0
        for node in tree.body:
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str) or isinstance(node,ast.ImportFrom) and node.module=='__future__':
                header=max(header,node.end_lineno)
            else:
                break
        imports=[]
        if any('office_endpoint(' in e[1] for e in edits):imports.append('endpoint as office_endpoint')
        if any('office_remote_class(' in e[1] for e in edits):imports.append('remote_class as office_remote_class')
        lines.insert(header,'from app.remote.boundary import '+', '.join(imports)+'\n')
        path.write_text(''.join(lines))

import pprint
text='"""Explicit service API catalogue. Unlisted operations are rejected before import/invocation."""\nOPERATIONS = '+pprint.pformat(dict(sorted(OPERATIONS.items())),width=120,sort_dicts=True)+'\n'
(ROOT/'app/remote/operations.py').write_text(text)
print('Registered business operations:',len(OPERATIONS))
