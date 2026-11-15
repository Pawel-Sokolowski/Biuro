using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Xml;
using System.Xml.Linq;
using Soneta.Langs;
using Soneta.Tools;

namespace Biuro.Ezla;

// Deliberately not a record: generated ToString must not disclose PESEL or medical XML.
public sealed class EzlaDocument
{
    public required string Identity { get; init; }
    public required string Identifier { get; init; }
    public required string Number { get; init; }
    public required string Status { get; init; }
    public required DateTime Start { get; init; }
    public required DateTime End { get; init; }
    public required DateTime Issued { get; init; }
    public required string Codes { get; init; }
    public DateTime? HospitalStart { get; init; }
    public DateTime? HospitalEnd { get; init; }
    public required string Source { get; init; }
    public required string Fingerprint { get; init; }
    public required string Review { get; init; }
    public override string ToString() => "e-ZLA";
}

public static class ReportReader
{
    public static IReadOnlyList<EzlaDocument> Read(SourceReport report)
    {
        var bytes = Convert.FromBase64String(report.Xml);
        if (bytes.Length == 0 || bytes.Length > 10 * 1024 * 1024) throw Invalid();
        using var stream = new MemoryStream(bytes);
        using var reader = XmlReader.Create(stream, new XmlReaderSettings {
            DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, MaxCharactersInDocument = 10 * 1024 * 1024 });
        var root = XDocument.Load(reader).Root;
        if (root?.Name != "raportEzla") throw Invalid();
        var header = Node(root, "daneRaportu");
        if (Text(Node(header, "platnik"), "nip").Replace("-", "").Replace(" ", "") != report.PayerNip) throw Invalid();
        var rows = root.Elements("dokumentyEzla").ToArray();
        if (!int.TryParse(Text(header, "liczbaDokumentowEzla"), out int count) || count != rows.Length || count > 20000) throw Invalid();
        var correctionRows = root.Elements("dokumentyEzlaK").Count();
        string declaredCorrections = Text(header, "liczbaDokumentowEzlaK", false);
        if (declaredCorrections.Length > 0 && (!int.TryParse(declaredCorrections, out int corrections) || corrections != correctionRows))
            throw new InvalidDataException("Raport wykazuje e-ZLA/K bez zgodnych szczegółów. Pobierz pełny raport z Biuro.".Translate());
        var result = new List<EzlaDocument>();
        foreach (var row in root.Elements().Where(e => e.Name == "dokumentyEzla" || e.Name == "dokumentyEzlaK"))
        {
            var person = Node(row, "daneUbezpieczonego");
            var identifier = Node(person, "identyfikator");
            string kind = Text(identifier, "typ"), id = Text(identifier, "wartosc");
            var doc = Node(row, "daneDokumentu");
            string series = Text(doc, "seria"), number = Text(doc, "numer");
            var review = new List<string>();
            string[] known = ["seria", "numer", "status", "czyAnulowane", "dataWystawienia", "dataZus",
                "okresZwolnienia", "okresWSzpitalu", "kodChorobyA", "kodChorobyB", "kodChorobyC",
                "kodChorobyD", "kodChorobyE", "kodPokrewienstwa", "dataUrodzeniaOsoby",
                "miejsceUbezpieczenia", "wskazaniaLekarskie", "dataZwolnieniaDo"];
            if (doc.Elements().Any(e => (!known.Contains(e.Name.LocalName) || e.Name.NamespaceName.Length > 0) && !string.IsNullOrWhiteSpace(e.Value)))
                review.Add("Dodatkowe nieobsługiwane dane dokumentu — sprawdź przed naliczeniem.".Translate());
            bool correction = row.Name == "dokumentyEzlaK";
            string status = correction ? "Skrócenie e-ZLA/K" : Text(doc, "status");
            if (correction || status != "Wystawione") review.Add("Korekta, anulowanie lub status wymagający obsługi w enova.".Translate());
            if (kind != "PESEL" || id.Length != 11 || id.Any(c => c is < '0' or > '9')) review.Add("Brak jednoznacznego PESEL.".Translate());
            if (series.Length == 0 || number.Length == 0 || (series + number).Length > 9) review.Add("Nieobsługiwana seria lub długość numeru ZLA.".Translate());
            string cancelled = Text(doc, "czyAnulowane", false).ToLowerInvariant();
            if (cancelled is not ("" or "nie" or "false" or "0")) review.Add("Oznaczenie anulowania lub nieznana wartość.".Translate());
            string care = Text(doc, "kodPokrewienstwa", false);
            if (care is not ("" or "0")) review.Add("Zwolnienie na opiekę wymaga odrębnej definicji nieobecności.".Translate());
            string insurance = Text(doc, "miejsceUbezpieczenia", false);
            if (insurance is not ("" or "1" or "ZUS")) review.Add("Sprawdź miejsce ubezpieczenia.".Translate());
            DateTime start = DateTime.MinValue, end = DateTime.MinValue, issued = DateTime.MinValue;
            if (!correction)
            {
                var period = Node(doc, "okresZwolnienia");
                start = Date(Text(period, "dataOd")); end = Date(Text(period, "dataDo")); issued = Date(Text(doc, "dataWystawienia"));
                if (end < start) review.Add("Nieprawidłowy okres zwolnienia.".Translate());
            }
            string codes = "";
            foreach (char code in "ABCDE")
            {
                string flag = Text(doc, "kodChoroby" + code, false);
                if (flag is "" or "0" or "false" or "Nie") continue;
                if (flag == code.ToString() || flag is "1" or "true" or "Tak") codes += code;
                else review.Add("Nieznany kod literowy choroby.".Translate());
            }
            // Conflicting causes must be reviewed, never guessed from enum precedence.
            if (codes.Count(c => "BCD".Contains(c)) > 1) review.Add("Wiele przyczyn zwolnienia — sprawdź rozliczenie.".Translate());
            DateTime? hospitalStart = null, hospitalEnd = null;
            var hospital = doc.Element("okresWSzpitalu");
            if (hospital != null)
            {
                string hs = Text(hospital, "dataOd", false), he = Text(hospital, "dataDo", false);
                if (hs.Length > 0 || he.Length > 0)
                {
                    hospitalStart = Date(hs); hospitalEnd = Date(he);
                    if (hospitalStart < start || hospitalEnd > end || hospitalEnd < hospitalStart)
                        review.Add("Nieprawidłowy okres pobytu w szpitalu.".Translate());
                }
            }
            string identity = Hash(string.Join("|", kind, id, series, number));
            string fingerprint = Hash(string.Join("|", identity, status, start.ToString("O"), end.ToString("O"), issued.ToString("O"), codes, hospitalStart?.ToString("O"), hospitalEnd?.ToString("O"), care, cancelled));
            string source = new XElement("biuroEzla", new XAttribute("id", identity), new XAttribute("fingerprint", fingerprint), new XElement(row)).ToString(SaveOptions.DisableFormatting);
            result.Add(new EzlaDocument { Identity = identity, Identifier = id, Number = series + number,
                Status = status, Start = start, End = end, Issued = issued, Codes = codes,
                HospitalStart = hospitalStart, HospitalEnd = hospitalEnd, Source = source,
                Fingerprint = fingerprint, Review = string.Join(" ", review.Distinct()) });
        }
        return result;
    }

    public static IReadOnlyList<EzlaDocument> Consolidate(IEnumerable<EzlaDocument> documents)
    {
        var result = new List<EzlaDocument>();
        foreach (var group in documents.GroupBy(d => d.Identity))
        {
            // Include annulments/corrections from every report before considering any insertion.
            var blocked = group.FirstOrDefault(d => d.Review.Length > 0);
            if (blocked != null) { result.Add(blocked); continue; }
            if (group.Select(d => d.Fingerprint).Distinct().Count() != 1)
                throw new InvalidDataException("Sprzeczne wersje e-ZLA. Sprawdź raporty w Biuro przed importem.".Translate());
            result.Add(group.First());
        }
        return result;
    }
    private static XElement Node(XElement parent, string name) => parent.Elements(name).SingleOrDefault() ?? throw Invalid();
    private static string Text(XElement parent, string name, bool required = true)
    {
        var items = parent.Elements(name).ToArray();
        if (items.Length > 1 || (required && items.Length != 1) || items.Any(x => x.HasElements)) throw Invalid();
        string value = items.FirstOrDefault()?.Value.Trim() ?? "";
        if (value.Length > 256 || (required && value.Length == 0)) throw Invalid();
        return value;
    }
    private static DateTime Date(string value) => DateTime.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture,
        DateTimeStyles.None, out var date) ? date : throw new InvalidDataException("Nieprawidłowa data e-ZLA.".Translate());
    private static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
    private static InvalidDataException Invalid() => new("Nieprawidłowa struktura raportu e-ZLA.".Translate());
}
