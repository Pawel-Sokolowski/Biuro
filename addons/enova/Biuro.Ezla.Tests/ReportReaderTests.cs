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

    [TestCase("http://biuro.example")]
    [TestCase("https://user:password@biuro.example")]
    [TestCase("https://biuro.example/api")]
    public void InvalidTransportAddressIsRejectedBeforeSendingCredentials(string address)
        => Assert.Throws<ArgumentException>(() => new BiuroConnection(address));
}
