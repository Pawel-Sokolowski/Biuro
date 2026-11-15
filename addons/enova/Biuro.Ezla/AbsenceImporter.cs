using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Soneta.Business;
using Soneta.Kadry;
using Soneta.Kalend;
using Soneta.Langs;
using Soneta.Tools;
using Soneta.Types;

namespace Biuro.Ezla;

public sealed record ImportLine(string EmployeeCode, string CertificateNumber, string Decision, int Added, bool NeedsReview = false);

/// <summary>Caller owns the session and transaction. This class neither commits nor saves.</summary>
public static class AbsenceImporter
{
    public static IReadOnlyList<ImportLine> Import(Session session, IEnumerable<EzlaDocument> input,
        string definitionName, bool preview)
    {
        var kalend = session.GetKalend();
        var definition = kalend.DefNieobecnosci.WgNazwy[definitionName]
            ?? throw new InvalidOperationException("Nie znaleziono wskazanej definicji nieobecności.".Translate());
        if (definition.Blokada || definition.Przyczyna != PrzyczynaNieobecnosci.ZwolnienieChorobowe)
            throw new InvalidOperationException("Wskaż aktywną definicję zwolnienia chorobowego.".Translate());
        var lines = new List<ImportLine>();
        foreach (var doc in ReportReader.Consolidate(input))
        {
            if (doc.Review.Length > 0) { lines.Add(new("", doc.Number, doc.Review, 0, true)); continue; }
            var employees = session.GetKadry().Pracownicy.WgPESEL[doc.Identifier].Cast<Pracownik>().Take(2).ToArray();
            if (employees.Length != 1) { lines.Add(new("", doc.Number, "Brak jednego pracownika o zgodnym PESEL.".Translate(), 0, true)); continue; }
            var employee = employees[0];
            Date start = ToDate(doc.Start), end = ToDate(doc.End);
            var overlapping = employee.Nieobecnosci.GetIntersectedRows(new FromTo(start, end)).Cast<Nieobecnosc>().ToArray();
            var segments = Segments(doc).ToArray();
            if (overlapping.Length > 0)
            {
                bool identical = overlapping.Length == segments.Length && segments.All(s => overlapping.Any(n =>
                    n.Okres == new FromTo(ToDate(s.Start), ToDate(s.End)) && n.Definicja == definition &&
                    n.Zwolnienie.Numer == doc.Number && n.Zwolnienie.KodChoroby == doc.Codes &&
                    n.Zwolnienie.ZwolnienieWystawione == ToDate(doc.Issued) &&
                    n.Zwolnienie.LeczenieSzpitalne == s.Hospital && n.Zwolnienie.Przyczyna == CauseForCodes(doc.Codes)));
                lines.Add(new(employee.Kod, doc.Number, identical ? "Już zaimportowano.".Translate()
                    : "Konflikt z istniejącą nieobecnością — sprawdź ręcznie.".Translate(), 0, !identical));
                continue;
            }
            // Also detect the same certificate moved to another period in an earlier import.
            // The employee's keyed subtable bounds this read; never scan all employees/absences.
            if (employee.Nieobecnosci[new FieldCondition.Equal("Zwolnienie.Numer", doc.Number)].Any())
            { lines.Add(new(employee.Kod, doc.Number, "Ten numer ZLA istnieje z innym okresem.".Translate(), 0, true)); continue; }
            if (!preview)
                foreach (var segment in segments)
                {
                    var absence = session.AddRow(new NieobecnośćPracownika(employee));
                    // GuidedRow uses a unique database GUID. Two hosts importing the same
                    // certificate/segment must collide at Save(), never create two absences.
                    absence.Guid = ImportGuid(employee.Guid, doc.Identity, segment.Start, segment.End);
                    absence.Definicja = definition;
                    absence.Okres = new FromTo(ToDate(segment.Start), ToDate(segment.End));
                    absence.Zwolnienie.Numer = doc.Number;
                    absence.Zwolnienie.Przyczyna = CauseForCodes(doc.Codes);
                    // The native setter also sets continuation for code A and
                    // applies the B/C/D/E cause rules in the installed version.
                    absence.Zwolnienie.KodChoroby = doc.Codes;
                    absence.Zwolnienie.LeczenieSzpitalne = segment.Hospital;
                    absence.Zwolnienie.ZwolnienieWystawione = ToDate(doc.Issued);
                    // Match Soneta 2512's electronic PUE import convention:
                    // delivery = issue date; ZLA.Data = report generation date.
                    absence.Zwolnienie.ZwolnienieDostarczone = ToDate(doc.Issued);
                    absence.ZLA.Wersja = WersjaZLA.PUEService;
                    absence.ZLA.Data = ToDate(doc.Generated);
                    absence.ZLA.Zrodlo = doc.Source;
                }
            lines.Add(new(employee.Kod, doc.Number, preview ? "Gotowe do importu.".Translate() : "Dodano nieobecność.".Translate(), preview ? 0 : segments.Length));
        }
        return lines;
    }

    public static IEnumerable<(DateTime Start, DateTime End, bool Hospital)> Segments(EzlaDocument doc)
    {
        if (doc.HospitalStart is not DateTime hs || doc.HospitalEnd is not DateTime he)
        { yield return (doc.Start, doc.End, false); yield break; }
        if (doc.Start < hs) yield return (doc.Start, hs.AddDays(-1), false);
        yield return (hs, he, true);
        if (he < doc.End) yield return (he.AddDays(1), doc.End, false);
    }
    public static Guid ImportGuid(Guid employee, string document, DateTime start, DateTime end)
    {
        string name = string.Join("|", "Biuro.eZLA.v1", employee.ToString("D"), document,
            start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), end.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        byte[] hash = SHA256.HashData(Encoding.UTF8.GetBytes(name));
        hash[6] = (byte)((hash[6] & 0x0f) | 0x80); // UUID v8, RFC 9562 variant.
        hash[8] = (byte)((hash[8] & 0x3f) | 0x80);
        return new Guid(hash.AsSpan(0, 16), bigEndian: true);
    }
    private static Date ToDate(DateTime value) => new(value.Year, value.Month, value.Day);
    public static PrzyczynaZwolnienia CauseForCodes(string codes) => codes.Contains('B')
        ? PrzyczynaZwolnienia.NiezdolnośćDoPracyWOkresieCiąży : codes.Contains('C')
        ? PrzyczynaZwolnienia.NadużycieAlkoholu : codes.Contains('D')
        ? PrzyczynaZwolnienia.ZwolnienieSpowodowaneGruźlicą : codes.Contains('E')
        ? PrzyczynaZwolnienia.ChorobaZakaźna : PrzyczynaZwolnienia.ZwolnienieLekarskie;
}
