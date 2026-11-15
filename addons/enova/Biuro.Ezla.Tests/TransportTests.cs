using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using NUnit.Framework;

namespace Biuro.Ezla.Tests;

[TestFixture]
public sealed class TransportTests
{
    [Test]
    public void LoginPagedExportAndLogoutUseLengthDelimitedJsonAndTheRevocableSession()
    {
        using var server = new OfficeProtocol();
        using (var connection = new BiuroConnection("https://biuro.example.invalid", server))
        {
            connection.Login("worker@example.invalid", "synthetic-password");
            var reports = connection.Download("1112223344", new[] { "2025-12" });
            Assert.That(reports.Count, Is.EqualTo(2));
            Assert.That(reports[0].PayerNip, Is.EqualTo("9999999999"));
            Assert.That(reports[1].PayerNip, Is.EqualTo("8888888888"));
        }
        Assert.That(server.Routes, Is.EqualTo(new[] { "/api/v1/login", "/api/v1/enova/ezla", "/api/v1/enova/ezla", "/api/v1/logout" }));
    }

    private sealed class OfficeProtocol : HttpMessageHandler
    {
        private const string Token = "synthetic-revocable-session-for-ci-only";
        public List<string> Routes { get; } = new();
        private string cursor = "";
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Assert.That(request.RequestUri!.Scheme, Is.EqualTo("https"));
            Assert.That(request.Method, Is.EqualTo(HttpMethod.Post));
            Assert.That(request.Headers.TransferEncodingChunked, Is.Not.True);
            Assert.That(request.Content, Is.Not.Null);
            Assert.That(request.Content!.Headers.ContentType!.MediaType, Is.EqualTo("application/json"));
            string body = await request.Content.ReadAsStringAsync(cancellationToken);
            Assert.That(request.Content.Headers.ContentLength, Is.EqualTo(Encoding.UTF8.GetByteCount(body)));
            using var json = JsonDocument.Parse(body);
            var root = json.RootElement;
            string path = request.RequestUri.AbsolutePath;
            Routes.Add(path);
            object response;
            if (path == "/api/v1/login")
            {
                Assert.That(request.Headers.Authorization, Is.Null);
                Assert.That(root.GetProperty("email").GetString(), Is.EqualTo("worker@example.invalid"));
                Assert.That(root.GetProperty("password").GetString(), Is.EqualTo("synthetic-password"));
                Assert.That(root.GetProperty("remember").GetBoolean(), Is.False);
                response = new { token = Token, employee_id = 7, expires_in = 43200 };
            }
            else
            {
                Assert.That(request.Headers.Authorization!.Scheme, Is.EqualTo("Bearer"));
                Assert.That(request.Headers.Authorization.Parameter, Is.EqualTo(Token));
                if (path == "/api/v1/logout") response = new { ok = true };
                else
                {
                    Assert.That(path, Is.EqualTo("/api/v1/enova/ezla"));
                    Assert.That(root.GetProperty("company_nip").GetString(), Is.EqualTo("1112223344"));
                    Assert.That(root.GetProperty("month").GetString(), Is.EqualTo("2025-12"));
                    Assert.That(root.GetProperty("after").GetString(), Is.EqualTo(cursor));
                    bool first = cursor.Length == 0;
                    byte[] xml = Encoding.UTF8.GetBytes(first ? "<synthetic-first/>" : "<synthetic-second/>");
                    string checksum = Convert.ToHexString(SHA256.HashData(xml)).ToLowerInvariant();
                    string key = "ezla_report_1_" + checksum;
                    cursor = first ? key : "";
                    response = new { schema = 1, company_nip = "1112223344", month = "2025-12", next = cursor,
                        reports = new[] { new { id = key, sha256 = checksum, payer_nip = first ? "9999999999" : "8888888888", xml = Convert.ToBase64String(xml) } } };
                }
            }
            return new HttpResponseMessage(HttpStatusCode.OK)
            { Content = new StringContent(JsonSerializer.Serialize(response), Encoding.UTF8, "application/json") };
        }
    }
}
