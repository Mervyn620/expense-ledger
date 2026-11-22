using System;
using System.IO;
using System.Text;
using System.Text.Json;
using Forms = System.Windows.Forms;

namespace Expenses.Desktop;

public sealed partial class MainWindow
{
    bool chatgptClipboardVerificationStarted;

    void VerifyChatGptClipboard()
    {
        if (chatgptClipboardVerificationStarted
            || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_CHATGPT_CLIPBOARD") != "1") return;
        chatgptClipboardVerificationStarted = true;
        var folder = Path.Combine(root, "backend", ".app-runtime", "chatgpt-exports", "temporary");
        var state = Path.Combine(root, ".desktop-state", "chatgpt-clipboard-verification.json");
        Directory.CreateDirectory(folder);
        Directory.CreateDirectory(Path.GetDirectoryName(state)!);
        var file = Path.Combine(folder, "expenses-chatgpt-month-" + Guid.NewGuid().ToString("N")[..8] + ".md");
        Forms.IDataObject? previous = null;
        try {
            previous = Forms.Clipboard.GetDataObject();
            File.WriteAllText(file, "# Clipboard verification\nSynthetic data only.\n", Encoding.UTF8);
            CopyChatGptFileToClipboard(file);
            var copied = Forms.Clipboard.GetFileDropList();
            var passed = copied.Count == 1 && string.Equals(copied[0], file, StringComparison.OrdinalIgnoreCase);
            File.WriteAllText(state, JsonSerializer.Serialize(new { status = passed ? "passed" : "failed", file_name = Path.GetFileName(file) }));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(state, JsonSerializer.Serialize(new { status = "failed", error = error.ToString() }));
        } finally {
            try {
                if (previous != null) Forms.Clipboard.SetDataObject(previous, true);
                else Forms.Clipboard.Clear();
            } catch (System.Runtime.InteropServices.ExternalException) { }
            File.Delete(file);
        }
    }
}
