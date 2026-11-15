using Biuro.Ezla;
using Biuro.Ezla.UI;
using Soneta.Business;
using Soneta.Kadry;
using Soneta.Langs;
using Soneta.Tools;
using Soneta.Types;

[assembly: Worker(typeof(EzlaWorker), typeof(Pracownicy))]
namespace Biuro.Ezla.UI;

public sealed class EzlaParameters : ContextBase
{
    public EzlaParameters(Context context) : base(context) { }
    [Caption("Serwer Biuro (HTTPS)")] public string Server { get; set; } = "";
    [Caption("E-mail w Biuro")] public string Email { get; set; } = "";
    [Caption("Hasło w Biuro")] public string Password { get; set; } = "";
    [Caption("Od miesiąca")] public Date From { get; set; } = Date.Today.AddMonths(-2);
    [Caption("Do miesiąca")] public Date To { get; set; } = Date.Today;
    [Caption("Definicja nieobecności")] public string Definition { get; set; } = "Zwolnienie chorobowe";
    [Caption("Tylko podgląd")] public bool Preview { get; set; } = true;
}

public sealed class EzlaWorker
{
    [Context] public Session Session { get; set; } = null!;
    [Context] public EzlaParameters Parameters { get; set; } = null!;

    [Action("Biuro/e-ZLA — pobierz i sprawdź", Mode = ActionMode.SingleSession | ActionMode.ConfirmSave | ActionMode.Progress)]
    public string Execute()
    {
        try
        {
            var lines = EzlaJob.Run(Session, Parameters.Server, Parameters.Email, Parameters.Password,
                new DateTime(Parameters.From.Year, Parameters.From.Month, 1),
                new DateTime(Parameters.To.Year, Parameters.To.Month, 1), Parameters.Definition, Parameters.Preview);
            return string.Join(Environment.NewLine, lines.Select(l => "{0} / {1}: {2}".TranslateFormat(l.EmployeeCode, l.CertificateNumber, l.Decision)))
                + Environment.NewLine + "Dodane odcinki nieobecności: {0}.".TranslateFormat(lines.Sum(l => l.Added));
        }
        finally { Parameters.Password = ""; }
    }
}
