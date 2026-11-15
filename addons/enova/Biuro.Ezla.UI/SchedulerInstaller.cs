using Soneta.Business;
using Soneta.Business.Db;
using Soneta.Core;
using Soneta.Core.Schedule;
using Soneta.Langs;
using Soneta.Tools;
using Soneta.Types;

namespace Biuro.Ezla.UI;

public static class SchedulerInstaller
{
    public const string JobName = "Biuro — import e-ZLA";
    public static void Install(Session owner, string hzInstance, Time time)
    {
        if (string.IsNullOrWhiteSpace(hzInstance)) throw new ArgumentException("Podaj nazwę jednej instancji Harmonogramu Zadań.".Translate());
        using var config = owner.Login.CreateSession(false, true, "Biuro e-ZLA — harmonogram");
        var view = config.GetBusiness().TaskDefs.CreateView();
        view.Condition = new FieldCondition.Equal("Name", JobName);
        if (view.Cast<TaskDefinition>().Any()) throw new InvalidOperationException("Zadanie Biuro e-ZLA już istnieje. Edytuj jego harmonogram w Opcjach.".Translate());
        using (var transaction = config.Logout(true))
        {
            var task = config.AddRow(new TaskDefinition("Pracownicy"));
            task.Name = JobName;
            task.IsVisibleInScheduler = true;
            // Standard HZ global automatic action. Contains no passwords, tokens or database keys.
            task.OtherMethods = """
                [Soneta.Business.AutoAction("Import e-ZLA z Biuro", true)]
                public void BiuroEzlaImport()
                {
                    Biuro.Ezla.EzlaJob.Run(Session);
                }
                """;
            var schedule = config.AddRow(new ScheduleDefinition());
            schedule.Name = JobName;
            schedule.TaskDefinition = task;
            schedule.ScheduleType = ScheduleTypeEnum.UseCycleDefinition;
            // Select the named custom method, rather than the platform's default
            // workflow-start action (this task does not require a WF process).
            schedule.DefaultAutoAction = false;
            schedule.SetAutoActionName("BiuroEzlaImport");
            schedule.StartDate = Date.Today.AddDays(1);
            schedule.CycleDefinition.Typ = DefinicjaCykluTyp.Dzienny;
            schedule.CycleDefinition.Czas = time;
            schedule.HzInstance = hzInstance.Trim();
            schedule.Locked = true; // Enable only after configuring the service identity and reviewing a preview.
            transaction.CommitUI();
        }
        config.Save();
    }
}
