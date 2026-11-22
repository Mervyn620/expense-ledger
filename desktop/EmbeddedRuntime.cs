using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Expenses.Desktop;

// Python and Node copied into <root>\runtime by tools/setup-runtime.ps1. When present, the backend and
// every job it starts (refresh scripts, collectors) use them instead of whatever is installed on the PC.
internal sealed record EmbeddedRuntime(string Root, string Python, string? Node)
{
    internal static EmbeddedRuntime? Find(string projectRoot)
    {
        var configured = Environment.GetEnvironmentVariable("EXPENSE_RUNTIME_DIR");
        var folder = Path.GetFullPath(string.IsNullOrWhiteSpace(configured) ? Path.Combine(projectRoot, "runtime") : configured);
        var python = Path.Combine(folder, "python", "python.exe");
        if (!File.Exists(python)) return null;
        var node = Path.Combine(folder, "node", "node.exe");
        return new EmbeddedRuntime(folder, python, File.Exists(node) ? node : null);
    }

    internal void Apply(IDictionary<string, string?> environment)
    {
        var first = new List<string> { Path.GetDirectoryName(Python)!, Path.Combine(Path.GetDirectoryName(Python)!, "Scripts") };
        if (Node != null) first.Insert(0, Path.GetDirectoryName(Node)!);
        environment.TryGetValue("PATH", out var current);
        var rest = (current ?? Environment.GetEnvironmentVariable("PATH") ?? "")
            .Split(';', StringSplitOptions.RemoveEmptyEntries)
            .Where(item => !first.Contains(item.TrimEnd('\\'), StringComparer.OrdinalIgnoreCase));
        environment["PATH"] = string.Join(';', first.Concat(rest));
        environment["EXPENSE_RUNTIME_PYTHON"] = Python;
        if (Node != null) environment["EXPENSE_RUNTIME_NODE"] = Node;
        // Ignore per-user site-packages and any PYTHONHOME/PYTHONPATH from the PC's own Python.
        environment["PYTHONNOUSERSITE"] = "1";
        environment.Remove("PYTHONHOME");
        environment.Remove("PYTHONPATH");
    }
}
