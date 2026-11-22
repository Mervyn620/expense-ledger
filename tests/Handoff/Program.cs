using System.IO;
using System.Text.Json;
using Expenses.Desktop;
using Path = System.IO.Path;

var checks = new List<string>();
void Check(bool success, string name) { if (!success) throw new Exception(name); checks.Add(name); }

Check(ClaudeBrowserHandoff.IsClaudeUrl("https://claude.ai/new") && ClaudeBrowserHandoff.IsClaudeUrl("https://CLAUDE.ai/chat/abc"), "claude_host_accepted");
Check(!ClaudeBrowserHandoff.IsClaudeUrl("http://claude.ai/new") && !ClaudeBrowserHandoff.IsClaudeUrl("https://claude.ai.example.com/")
    && !ClaudeBrowserHandoff.IsClaudeUrl("https://chatgpt.com/") && !ClaudeBrowserHandoff.IsClaudeUrl("not a url"), "other_hosts_rejected");
Check(ClaudeBrowserHandoff.ComposerRank("Write your prompt to Claude", "") == 0, "composer_exact_label");
Check(ClaudeBrowserHandoff.ComposerRank("", "tiptap ProseMirror break-words") == 1, "composer_prosemirror_class");
Check(ClaudeBrowserHandoff.ComposerRank("与 Claude 对话", "") == 2, "composer_label_names_claude");
Check(ClaudeBrowserHandoff.ComposerRank("搜索", "search-input") == -1, "unrelated_edit_ignored");
Check(ClaudeBrowserHandoff.NamesAttachment("expenses-chatgpt-month-1a2b3c4d MD 12 KB", "expenses-chatgpt-month-1a2b3c4d.md")
    && ClaudeBrowserHandoff.NamesAttachment("expenses-chatgpt-overview.md", "expenses-chatgpt-overview.md"), "attachment_card_matched");
Check(!ClaudeBrowserHandoff.NamesAttachment("expenses-chatgpt-month-ffffffff", "expenses-chatgpt-month-1a2b3c4d.md"), "other_attachment_ignored");
Check(ClaudeBrowserHandoff.IsSendName("Send message") && ClaudeBrowserHandoff.IsSendName("发送消息") && !ClaudeBrowserHandoff.IsSendName("Stop"), "send_button_names");

var root = Directory.CreateTempSubdirectory("expenses-runtime-");
try {
    Check(EmbeddedRuntime.Find(root.FullName) == null, "missing_runtime_falls_back");
    var nodeDir = Path.Combine(root.FullName, "runtime", "node");
    var pythonDir = Path.Combine(root.FullName, "runtime", "python");
    Directory.CreateDirectory(pythonDir);
    Directory.CreateDirectory(nodeDir);
    File.WriteAllText(Path.Combine(pythonDir, "python.exe"), "");
    File.WriteAllText(Path.Combine(nodeDir, "node.exe"), "");
    var runtime = EmbeddedRuntime.Find(root.FullName) ?? throw new Exception("runtime_not_found");
    var env = new Dictionary<string, string?> {
        ["PATH"] = @"C:\Other;" + nodeDir + @"\;C:\Windows", ["PYTHONHOME"] = @"C:\Py", ["PYTHONPATH"] = "x",
    };
    runtime.Apply(env);
    var parts = env["PATH"]!.Split(';');
    Check(parts[0] == nodeDir && parts[1] == pythonDir, "runtime_first_on_path");
    Check(parts.Count(p => string.Equals(p.TrimEnd('\\'), nodeDir, StringComparison.OrdinalIgnoreCase)) == 1
        && parts.Contains(@"C:\Other") && parts.Contains(@"C:\Windows"), "path_deduplicated_and_kept");
    Check(env["EXPENSE_RUNTIME_PYTHON"] == runtime.Python && env["EXPENSE_RUNTIME_NODE"] == runtime.Node && env["PYTHONNOUSERSITE"] == "1"
        && !env.ContainsKey("PYTHONHOME") && !env.ContainsKey("PYTHONPATH"), "python_isolated");
} finally { root.Delete(true); }
Console.WriteLine(JsonSerializer.Serialize(new { status = "passed", checks }));
