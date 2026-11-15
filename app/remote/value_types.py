"""Application data records explicitly permitted by the JSON protocol."""
TYPES = frozenset({
    'app.remote.prepared.ImportPreparation',
    'app.integrations.enova.document_counts.MonthlyCount',
    'app.integrations.ezla.service.Download',
    'app.integrations.ezla.report.Certificate', 'app.integrations.ezla.report.Report',
    'app.reports.document_counts_xlsx.CountCell', 'app.reports.import_parse.InvoiceLineIdentity',
    'app.utils.table_records.TableRecord', 'app.email.bulk_mail.BulkMailPlan',
    'app.db.dao_active_timers.ActiveTimer', 'app.email.notification_types.MailNotificationSnapshot',
})
