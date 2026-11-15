using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using Soneta.Langs;

namespace Biuro.Ezla;

/// <summary>Owns one short-lived Biuro session; never acquires a database key.</summary>
public sealed class BiuroConnection : IDisposable
{
    private readonly HttpClient http;
    private string? token;
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
    public BiuroConnection(string address)
    {
        if (!Uri.TryCreate(address, UriKind.Absolute, out var uri) || uri.Scheme != "https" ||
            !string.IsNullOrEmpty(uri.UserInfo) || uri.AbsolutePath != "/" ||
            !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
            throw new ArgumentException("Podaj adres HTTPS serwera Biuro bez ścieżki.".Translate());
        // Never disable certificate validation; never forward credentials through redirects.
        http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false })
        { BaseAddress = uri, Timeout = TimeSpan.FromSeconds(90), MaxResponseContentBufferSize = 24 * 1024 * 1024 };
    }

    public void Login(string email, string password)
    {
        var result = Post<LoginResponse>("api/v1/login", new { email, password, remember = false });
        if (string.IsNullOrEmpty(result.Token)) throw new InvalidDataException("Brak sesji Biuro.".Translate());
        token = result.Token;
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }

    public IReadOnlyList<SourceReport> Download(string companyNip, IEnumerable<string> months)
    {
        var reports = new Dictionary<string, SourceReport>(StringComparer.Ordinal);
        long bytes = 0;
        foreach (var month in months.Distinct())
        {
            string after = "";
            var cursors = new HashSet<string>(StringComparer.Ordinal);
            do
            {
                var page = Post<ReportPage>("api/v1/enova/ezla", new { company_nip = companyNip, month, after });
                if (page.Schema != 1 || page.CompanyNip != companyNip || page.Month != month || page.Reports == null)
                    throw new InvalidDataException("Niezgodna odpowiedź serwera e-ZLA.".Translate());
                foreach (var report in page.Reports)
                {
                    byte[] xml = Convert.FromBase64String(report.Xml);
                    if (xml.Length > 10 * 1024 * 1024 || !string.Equals(Convert.ToHexString(SHA256.HashData(xml)), report.Sha256, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("Niezgodna suma kontrolna e-ZLA.".Translate());
                    if (reports.TryGetValue(report.Id, out var previous))
                    {
                        if (previous.Sha256 != report.Sha256 || previous.PayerNip != report.PayerNip)
                            throw new InvalidDataException("Zmieniono raport podczas pobierania.".Translate());
                        continue;
                    }
                    bytes += xml.Length;
                    if (bytes > 128 * 1024 * 1024 || reports.Count >= 1000)
                        throw new InvalidDataException("Zbyt dużo raportów. Zawęź okres importu.".Translate());
                    reports.Add(report.Id, report);
                }
                after = page.Next ?? "";
                if (after.Length > 0 && !cursors.Add(after))
                    throw new InvalidDataException("Powtórzona strona raportów.".Translate());
            } while (after.Length > 0);
        }
        return reports.Values.ToArray();
    }

    private T Post<T>(string path, object value)
    {
        using var response = http.PostAsJsonAsync(path, value).GetAwaiter().GetResult();
        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden)
            throw new UnauthorizedAccessException("Sesja Biuro wygasła lub brakuje uprawnień do eksportu e-ZLA.".Translate());
        if (!response.IsSuccessStatusCode)
            throw new HttpRequestException("Serwer Biuro odrzucił żądanie (HTTP {0}).".TranslateFormat((int)response.StatusCode));
        return response.Content.ReadFromJsonAsync<T>(Json).GetAwaiter().GetResult()
            ?? throw new InvalidDataException("Pusta odpowiedź Biuro.".Translate());
    }

    public void Dispose()
    {
        try { if (token != null) Post<JsonElement>("api/v1/logout", new { }); }
        catch (HttpRequestException) { /* Server expiry/revocation still applies. */ }
        catch (TaskCanceledException) { }
        catch (UnauthorizedAccessException) { }
        finally { token = null; http.Dispose(); }
    }

    private sealed record LoginResponse(string Token);
    private sealed record ReportPage(int Schema, [property: JsonPropertyName("company_nip")] string CompanyNip,
        string Month, SourceReport[] Reports, string? Next);
}

public sealed record SourceReport(string Id, string Sha256,
    [property: JsonPropertyName("payer_nip")] string PayerNip, string Xml);
