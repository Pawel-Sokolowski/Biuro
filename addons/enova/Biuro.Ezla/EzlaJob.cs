using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Soneta.Business;
using Soneta.Core;
using Soneta.Langs;

namespace Biuro.Ezla;

/// <summary>Entry point shared by UI and the enova task scheduler. No UI dependencies.</summary>
public static class EzlaJob
{
    public static IReadOnlyList<ImportLine> Run(Session owner)
    {
        // HZ credentials are local to the service identity, never serialized into the enova database.
        string address = Required("BIURO_EZLA_URL");
        string email = Required("BIURO_EZLA_EMAIL");
        string password = Required("BIURO_EZLA_PASSWORD");
        string definition = Environment.GetEnvironmentVariable("BIURO_EZLA_DEFINITION") ?? "Zwolnienie chorobowe";
        return Run(owner, address, email, password, DateTime.Today.AddMonths(-2), DateTime.Today, definition, false);
    }

    public static IReadOnlyList<ImportLine> Run(Session owner, string address, string email, string password,
        DateTime from, DateTime to, string definition, bool preview)
    {
        if (from > to || to > from.AddMonths(12)) throw new ArgumentException("Wybierz okres do 12 miesięcy.".Translate());
        // Own a clean session: never save unrelated edits from the caller's screen/config session.
        using var session = owner.Login.CreateSession(false, false, "Biuro e-ZLA");
        string nip = new string(session.GetCore().Config.Firma.Pieczątka.NIP.Where(char.IsDigit).ToArray());
        if (nip.Length != 10) throw new InvalidOperationException("Bieżąca firma enova musi mieć NIP.".Translate());
        // Single HZ service instance per company; the OS mutex also serializes manual jobs on that host.
        using var mutex = new Mutex(false, "BiuroEzla_" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(nip))));
        bool acquired;
        try { acquired = mutex.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
        if (!acquired) throw new InvalidOperationException("Trwa już import e-ZLA tej firmy.".Translate());
        try
        {
            using var connection = new BiuroConnection(address);
            connection.Login(email, password);
            var months = new List<string>();
            for (DateTime month = new(from.Year, from.Month, 1); month <= to; month = month.AddMonths(1))
                months.Add(month.ToString("yyyy-MM", CultureInfo.InvariantCulture));
            var reports = connection.Download(nip, months);
            // Read all pages/statuses before any database modification.
            var documents = reports.SelectMany(ReportReader.Read).ToArray();
            if (preview) return AbsenceImporter.Import(session, documents, definition, true);
            IReadOnlyList<ImportLine> lines;
            using (var transaction = session.Logout(true))
            {
                lines = AbsenceImporter.Import(session, documents, definition, false);
                transaction.Commit();
            }
            session.Save(); // Save conflicts propagate: never report success after a failed write.
            var log = new Log("Biuro e-ZLA", true);
            log.WriteLine("Import e-ZLA: raporty {0}, dokumenty {1}, dodane odcinki nieobecności {2}.".TranslateFormat(
                reports.Count, lines.Count, lines.Sum(l => l.Added)));
            return lines;
        }
        finally { mutex.ReleaseMutex(); }
    }
    private static string Required(string name) => Environment.GetEnvironmentVariable(name)
        ?? throw new InvalidOperationException("Brak lokalnego ustawienia usługi: {0}.".TranslateFormat(name));
}
