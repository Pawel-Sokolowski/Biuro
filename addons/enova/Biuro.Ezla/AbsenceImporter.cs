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
            var employees = session.GetKadry().Pracownicy.WgPESEL[doc.Identifier].Cast<Pracownik>().ToArray();
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
                    n.Zwolnienie.LeczenieSzpitalne == s.Hospital && n.Zwolnienie.Przyczyna == Cause(doc)));
                lines.Add(new(employee.Kod, doc.Number, identical ? "Już zaimportowano.".Translate()
                    : "Konflikt z istniejącą nieobecnością — sprawdź ręcznie.".Translate(), 0, !identical));
                continue;
            }
            // Also detect the same certificate moved to another period in an earlier import.
            // The employee's keyed subtable bounds this read; never scan all employees/absences.
            if (employee.Nieobecnosci.Cast<Nieobecnosc>().Any(n => n.Zwolnienie.Numer == doc.Number))
            { lines.Add(new(employee.Kod, doc.Number, "Ten numer ZLA istnieje z innym okresem.".Translate(), 0, true)); continue; }
            if (!preview)
                foreach (var segment in segments)
                {
                    var absence = session.AddRow(new NieobecnośćPracownika(employee));
                    absence.Definicja = definition;
                    absence.Okres = new FromTo(ToDate(segment.Start), ToDate(segment.End));
                    absence.Zwolnienie.Numer = doc.Number;
                    absence.Zwolnienie.KodChoroby = doc.Codes;
                    absence.Zwolnienie.Przyczyna = Cause(doc);
                    absence.Zwolnienie.LeczenieSzpitalne = segment.Hospital;
                    absence.Zwolnienie.ZwolnienieWystawione = ToDate(doc.Issued);
                    // Delivery to the employer is not the moment this add-on happens to run.
                    // Leave ZwolnienieDostarczone at the platform default for explicit review.
                    absence.ZLA.Wersja = WersjaZLA.PUEService;
                    absence.ZLA.Data = ToDate(doc.Issued);
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
    private static Date ToDate(DateTime value) => new(value.Year, value.Month, value.Day);
    private static PrzyczynaZwolnienia Cause(EzlaDocument doc) => doc.Codes.Contains('B')
        ? PrzyczynaZwolnienia.NiezdolnośćDoPracyWOkresieCiąży : doc.Codes.Contains('C')
        ? PrzyczynaZwolnienia.NadużycieAlkoholu : doc.Codes.Contains('D')
        ? PrzyczynaZwolnienia.ZwolnienieSpowodowaneGruźlicą : PrzyczynaZwolnienia.ZwolnienieLekarskie;
}
