using System.Text;
using System.Xml;
using NUnit.Framework;

namespace Biuro.Ezla.Tests;

[TestFixture]
public sealed class ReportReaderTests
{
    private static SourceReport Report(string status = "Wystawione", string extra = "", string prefix = "")
    {
        string xml = prefix + $$"""
        <raportEzla><daneRaportu><platnik><nip>1112223344</nip></platnik><liczbaDokumentowEzla>1</liczbaDokumentowEzla></daneRaportu>
        <dokumentyEzla><daneUbezpieczonego><identyfikator><typ>PESEL</typ><wartosc>00000000000</wartosc></identyfikator></daneUbezpieczonego>
        <daneDokumentu><seria>ZZ</seria><numer>123456</numer><status>{{status}}</status><czyAnulowane>Nie</czyAnulowane>
        <dataWystawienia>2025-12-01</dataWystawienia><okresZwolnienia><dataOd>2025-12-01</dataOd><dataDo>2025-12-10</dataDo></okresZwolnienia>
        {{extra}}</daneDokumentu></dokumentyEzla></raportEzla>
        """;
        return new("synthetic", "not-used-by-reader", "1112223344", Convert.ToBase64String(Encoding.UTF8.GetBytes(xml)));
    }

    [Test]
    public void OrdinaryCertificateRetainsOnlyItsOwnSourceAndStableIdentity()
    {
        var doc = ReportReader.Read(Report()).Single();
        Assert.That(doc.Review, Is.Empty);
        Assert.That(doc.Number, Is.EqualTo("ZZ123456"));
        Assert.That(doc.Source, Does.Contain("dokumentyEzla").And.Not.Contain("daneRaportu"));
        Assert.That(doc.ToString(), Is.EqualTo("e-ZLA"));
        Assert.That(ReportReader.Consolidate(new[] { doc, doc }).Count, Is.EqualTo(1));
    }

    [TestCase("Anulowane")]
    [TestCase("Skorygowane")]
    [TestCase("Wsteczne - do wyjaśnienia")]
    [TestCase("nowy nieznany status")]
    public void LaterStatusBlocksEarlierIssuedCopy(string status)
    {
        var earlier = ReportReader.Read(Report()).Single();
        var later = ReportReader.Read(Report(status)).Single();
        var decision = ReportReader.Consolidate(new[] { earlier, later }).Single();
        Assert.That(decision.Review, Is.Not.Empty);
    }

    [Test]
    public void HospitalStaySplitsIntoExactNonoverlappingSegmentsAndPreservesCodeB()
    {
        var doc = ReportReader.Read(Report(extra: "<kodChorobyB>B</kodChorobyB><okresWSzpitalu><dataOd>2025-12-03</dataOd><dataDo>2025-12-05</dataDo></okresWSzpitalu>")).Single();
        Assert.That(doc.Codes, Is.EqualTo("B"));
        var segments = AbsenceImporter.Segments(doc).ToArray();
        Assert.That(segments.Length, Is.EqualTo(3));
        Assert.That(segments[0].End, Is.EqualTo(new DateTime(2025,12,2)));
        Assert.That(segments[1], Is.EqualTo((new DateTime(2025,12,3),new DateTime(2025,12,5),true)));
        Assert.That(segments[2].Start, Is.EqualTo(new DateTime(2025,12,6)));
    }

    [Test]
    public void SameCertificateAcrossCompanyAndPartnerNipsIsOneDocumentAndAnnulmentBlocksIt()
    {
        var original = Report();
        SourceReport ForPartner(SourceReport report)
        {
            string xml = Encoding.UTF8.GetString(Convert.FromBase64String(report.Xml)).Replace("1112223344", "9999999999");
            return report with { PayerNip = "9999999999", Xml = Convert.ToBase64String(Encoding.UTF8.GetBytes(xml)) };
        }
        var company = ReportReader.Read(original).Single();
        var partner = ReportReader.Read(ForPartner(original)).Single();
        Assert.That(ReportReader.Consolidate(new[] { company, partner }).Count, Is.EqualTo(1));
        var annulled = ReportReader.Read(ForPartner(Report("Anulowane"))).Single();
        Assert.That(ReportReader.Consolidate(new[] { company, annulled }).Single().Review, Is.Not.Empty);
    }

    [Test]
    public void CareAndOutOfRangeHospitalDatesRequireReview()
    {
        Assert.That(ReportReader.Read(Report(extra: "<kodPokrewienstwa>1</kodPokrewienstwa>")).Single().Review, Is.Not.Empty);
        Assert.That(ReportReader.Read(Report(extra: "<okresWSzpitalu><dataOd>2025-11-30</dataOd><dataDo>2025-12-02</dataDo></okresWSzpitalu>")).Single().Review, Is.Not.Empty);
    }

    [Test]
    public void DtdAndDifferentPayerAreRejected()
    {
        Assert.Throws<XmlException>(() => ReportReader.Read(Report(prefix: "<!DOCTYPE raportEzla [<!ENTITY secret SYSTEM 'file:///etc/passwd'>]>")));
        Assert.Throws<InvalidDataException>(() => ReportReader.Read(Report() with { PayerNip = "9999999999" }));
    }

    [Test]
    public void UiFormsAreActuallyEmbeddedInTheAddon()
    {
        var resources = typeof(Biuro.Ezla.UI.EzlaWorker).Assembly.GetManifestResourceNames();
        Assert.That(resources.Any(n => n.EndsWith("EzlaParameters.Ogolne.pageform.xml")), Is.True);
        Assert.That(resources.Any(n => n.EndsWith("ScheduleParameters.Ogolne.pageform.xml")), Is.True);
    }

    [Test]
    public void DatabaseImportIdentityIsStableAcrossHostsAndDistinctPerEmployeeAndSegment()
    {
        var employee = new Guid("a57a1da6-bc8b-41d7-89f5-f210d59e08c0");
        var start = new DateTime(2025,12,1);
        var end = new DateTime(2025,12,10);
        var id = AbsenceImporter.ImportGuid(employee, "document", start, end);
        Assert.That(id, Is.EqualTo(AbsenceImporter.ImportGuid(employee, "document", start, end)));
        Assert.That(id, Is.Not.EqualTo(AbsenceImporter.ImportGuid(employee, "other", start, end)));
        Assert.That(id, Is.Not.EqualTo(AbsenceImporter.ImportGuid(employee, "document", start, end.AddDays(1))));
        Assert.That(id, Is.Not.EqualTo(AbsenceImporter.ImportGuid(Guid.Empty, "document", start, end)));
    }

    [Test]
    public void SelectedMonthsExcludeUnrelatedCertificatesButKeepBlockingStatusChanges()
    {
        var original = Report();
        string xml = Encoding.UTF8.GetString(Convert.FromBase64String(original.Xml))
            .Replace("2025-12", "2026-01").Replace("123456", "654321");
        var unrelated = original with { Xml = Convert.ToBase64String(Encoding.UTF8.GetBytes(xml)) };
        var all = ReportReader.Read(original).Concat(ReportReader.Read(unrelated));
        var from = new DateTime(2025,12,1);
        var to = new DateTime(2025,12,31);
        Assert.That(ReportReader.SelectMonths(all, from, to).Select(d => d.Number), Is.EqualTo(new[] { "ZZ123456" }));
        var annulled = ReportReader.Read(Report("Anulowane"));
        Assert.That(ReportReader.SelectMonths(all.Concat(annulled), from, to).Single().Review, Is.Not.Empty);
    }

    [Test]
    public void UnknownMedicalFieldsAreNeverSilentlyDiscarded()
    {
        var document = ReportReader.Read(Report(extra: "<wypadekPrzyPracy>Tak</wypadekPrzyPracy>")).Single();
        Assert.That(document.Review, Is.Not.Empty);
    }

    [TestCase("http://biuro.example")]
    [TestCase("https://user:password@biuro.example")]
    [TestCase("https://biuro.example/api")]
    public void InvalidTransportAddressIsRejectedBeforeSendingCredentials(string address)
        => Assert.Throws<ArgumentException>(() => new BiuroConnection(address));
}
