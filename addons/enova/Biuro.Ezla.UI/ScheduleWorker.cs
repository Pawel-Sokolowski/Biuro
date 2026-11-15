using Biuro.Ezla;
using Biuro.Ezla.UI;
using Soneta.Business;
using Soneta.Kadry;
using Soneta.Langs;
using Soneta.Types;

[assembly: Worker(typeof(ScheduleWorker), typeof(Pracownicy))]
namespace Biuro.Ezla.UI;

public sealed class ScheduleParameters : ContextBase
{
    public ScheduleParameters(Context context) : base(context) { }
    [Caption("Instancja Harmonogramu Zadań")] public string Instance { get; set; } = "";
    [Caption("Codziennie o godzinie")] public Time Time { get; set; } = new(6, 0);
}
public sealed class ScheduleWorker
{
    [Context] public Session Session { get; set; } = null!;
    [Context] public ScheduleParameters Parameters { get; set; } = null!;
    [Action("Biuro/e-ZLA — zainstaluj harmonogram", Mode = ActionMode.SingleSession | ActionMode.ConfirmSave)]
    public string Install()
    {
        SchedulerInstaller.Install(Session, Parameters.Instance, Parameters.Time);
        return "Utworzono wyłączony harmonogram Biuro e-ZLA. Skonfiguruj lokalne dane konta usługi, sprawdź podgląd i włącz harmonogram w Opcjach.".Translate();
    }
}
