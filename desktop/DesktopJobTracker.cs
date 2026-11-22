using System;
using System.Collections.Generic;
using System.Text.Json;

namespace Expenses.Desktop;
internal sealed record CompletedDesktopJob(string Id, string Kind, string Label, string Status)
{
    public bool ChangesLedger => Kind.StartsWith("refresh:", StringComparison.Ordinal) && Kind != "refresh:scotia_login";
}
internal sealed class DesktopJobTracker
{
    readonly HashSet<string> completed = new();
    bool seeded;
    public IReadOnlyList<CompletedDesktopJob> Observe(JsonElement jobs)
    {
        var result = new List<CompletedDesktopJob>();
        foreach (var job in jobs.EnumerateArray()) {
            var state = LedgerLabels.Text(job, "status", "");
            var id = LedgerLabels.Text(job, "id", "");
            if (id.Length == 0 || state is not ("passed" or "partial" or "failed")) continue;
            if (completed.Add(id) && seeded)
                result.Add(new(id, LedgerLabels.Text(job, "kind", ""), LedgerLabels.Text(job, "label", "账本任务"), state));
        }
        seeded = true;
        return result;
    }
}
