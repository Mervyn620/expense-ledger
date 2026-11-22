using System;
using System.Collections.Specialized;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Forms = System.Windows.Forms;

namespace Expenses.Desktop;

public sealed partial class MainWindow
{
    bool chatGptHandoffBusy;
    static readonly Regex TemporaryChatGptName = new(
        @"^expenses-chatgpt-(month|year|question)-[0-9a-f]{8}\.md$",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);

    async Task HandleChatGptMessageAsync(string type, JsonElement data)
    {
        if (currentRoute != "analysis" || browser.Source?.AbsolutePath != "/analysis") return;
        var result = await RunChatGptActionAsync(type, LedgerLabels.Text(data, "filePath", ""),
            LedgerLabels.Text(data, "fileSha256", ""), LedgerLabels.Text(data, "question", ""));
        if (result is { } done) await ReportChatGptAsync(done.Message, done.Ok, done.Deleted);
    }

    // Shared by the native analysis page and the legacy Web page. Returns null when another action is running.
    async Task<(string Message, bool Ok, bool Deleted)?> RunChatGptActionAsync(string type, string filePath, string expectedHash, string question)
    {
        if (chatGptHandoffBusy) return null;
        chatGptHandoffBusy = true;
        try {
            var allowed = Path.GetFullPath(Path.Combine(root, "backend", ".app-runtime", "chatgpt-exports"));
            var full = Path.GetFullPath(filePath);
            question = question.Trim();
            if (!full.StartsWith(allowed + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || !full.EndsWith(".md", StringComparison.OrdinalIgnoreCase) || !File.Exists(full))
                throw new InvalidOperationException("导出文件不在客户端的专用目录中，请重新生成。");
            if (new FileInfo(full).Length > 8 * 1024 * 1024)
                throw new InvalidOperationException("导出文件过大，请重新生成。");
            var currentHash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(full)));
            if (!currentHash.Equals(expectedHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("文件内容已与预览不一致，请重新生成并核对。");

            if (type == "chatgpt-open-export") {
                Process.Start(new ProcessStartInfo(full) { UseShellExecute = true });
                return ("已打开本地文件，可检查完整内容。", true, false);
            }
            if (type == "chatgpt-copy-path") {
                Forms.Clipboard.SetText(full);
                return ("文件路径已复制。", true, false);
            }
            if (type is "chatgpt-copy-file" or "chatgpt-handoff") {
                var chrome = ChromeExecutable() ?? throw new InvalidOperationException("找不到 Chrome，请先安装并登录 Claude。");
                var attached = await ClaudeBrowserHandoff.AttachAsync(full, chrome, expectedHash);
                if (!attached)
                    return ("已执行粘贴，但未确认附件就绪。请在 Claude 核对附件；本机文件已保留，请勿重复点击造成重复附件。", false, false);
                var temporary = Path.Combine(allowed, "temporary") + Path.DirectorySeparatorChar;
                var deleted = false;
                if (full.StartsWith(temporary, StringComparison.OrdinalIgnoreCase) && TemporaryChatGptName.IsMatch(Path.GetFileName(full))) {
                    try { File.Delete(full); deleted = true; }
                    catch (IOException error) { Log(error); }
                    catch (UnauthorizedAccessException error) { Log(error); }
                }
                return ($"{Path.GetFileName(full)} 已附到 Claude 草稿，请自行点击发送。"
                    + (deleted ? "本机临时文件已清理。" : "本机文件已保留。"), true, deleted);
            }
            if (type == "chatgpt-copy-question") {
                if (question.Length is < 1 or > 5000) throw new InvalidOperationException("提问内容无效，请重新生成。");
                Forms.Clipboard.SetText(question);
                return ("提问已复制。请在确认附件出现后粘贴到 Claude，再由你点击发送。", true, false);
            }
            if (type == "chatgpt-delete-temp") {
                var temporary = Path.Combine(allowed, "temporary") + Path.DirectorySeparatorChar;
                if (!full.StartsWith(temporary, StringComparison.OrdinalIgnoreCase)
                    || !TemporaryChatGptName.IsMatch(Path.GetFileName(full)))
                    throw new InvalidOperationException("总览文件会由程序持续维护，不能作为临时文件删除。");
                File.Delete(full);
                try {
                    if (Forms.Clipboard.ContainsFileDropList()) {
                        var copied = Forms.Clipboard.GetFileDropList();
                        if (copied.Count == 1 && string.Equals(copied[0], full, StringComparison.OrdinalIgnoreCase))
                            Forms.Clipboard.Clear();
                    }
                } catch (System.Runtime.InteropServices.ExternalException) { }
                return ("本机临时文件已删除。已上传到 Claude 的副本需在 Claude 中单独管理。", true, true);
            }
            throw new InvalidOperationException("未知的 Claude 操作。");
        } catch (Exception error) {
            Log(error);
            return ("操作未完成：" + error.Message, false, false);
        } finally {
            chatGptHandoffBusy = false;
        }
    }

    static void CopyChatGptFileToClipboard(string full)
    {
        var files = new StringCollection { full };
        Forms.Clipboard.SetFileDropList(files);
        var copied = Forms.Clipboard.GetFileDropList();
        if (copied.Count != 1 || !string.Equals(copied[0], full, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("无法核实剪贴板中的文件，请重试。");
    }

    async Task ReportChatGptAsync(string message, bool ok, bool deleted = false)
    {
        if (browser.CoreWebView2 == null || browser.Source?.AbsolutePath != "/analysis") return;
        await browser.CoreWebView2.ExecuteScriptAsync(
            $"window.expenseChatGptHandoffResult?.({JsonSerializer.Serialize(message)}, {(ok ? "true" : "false")}, {(deleted ? "true" : "false")})");
    }

    static string? ChromeExecutable()
    {
        var candidates = new[] {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Google", "Chrome", "Application", "chrome.exe"),
        };
        return candidates.FirstOrDefault(File.Exists);
    }
}
