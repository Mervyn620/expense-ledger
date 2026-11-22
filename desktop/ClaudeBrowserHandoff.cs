using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Automation;
using Forms = System.Windows.Forms;

namespace Expenses.Desktop;

// Attaches a ledger file to a Claude draft in the user's own Chrome window. Only Windows accessibility
// and the OS clipboard are used: Chrome keeps its own claude.ai sign-in, no cookie is read or copied,
// and nothing is ever sent. The user reviews the draft and presses Send.
internal static class ClaudeBrowserHandoff
{
    internal const string Home = "https://claude.ai/new";
    static IntPtr rememberedWindow;
    // Chrome exposes aria-label as the UIA name and the HTML class as ClassName. Claude's composer is a
    // ProseMirror editor; its label differs between the English and Chinese interface.
    static readonly string[] ComposerNames = {
        "Write your prompt to Claude", "Talk with Claude", "Message Claude", "向 Claude 发送消息", "给 Claude 写消息",
    };
    static readonly string[] SendNames = { "Send message", "Send Message", "Send", "发送消息", "发送" };
    static readonly string[] RemovePrefixes = { "Remove ", "移除 ", "删除 " };
    static readonly string[] ProfileNames = { "Ledger", "Ledger User" };
    static readonly Condition AttachmentParts = new OrCondition(
        new[] { ControlType.Text, ControlType.Button, ControlType.Group, ControlType.ListItem, ControlType.Image,
                ControlType.Custom, ControlType.Hyperlink }
            .Select(type => (Condition)new PropertyCondition(AutomationElement.ControlTypeProperty, type)).ToArray());

    internal static Task<bool> AttachAsync(string path, string chrome, string expectedHash)
    {
        var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() => {
            try { completion.SetResult(Attach(path, chrome, expectedHash)); }
            catch (Exception error) { completion.SetException(error); }
        }) { IsBackground = true, Name = "Claude file handoff" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        return completion.Task;
    }

    static AutomationElement[] Elements(AutomationElement root, ControlType type) => root.FindAll(
        TreeScope.Descendants, new PropertyCondition(AutomationElement.ControlTypeProperty, type))
        .Cast<AutomationElement>().ToArray();

    internal static bool IsClaudeUrl(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.Host.Equals("claude.ai", StringComparison.OrdinalIgnoreCase);

    static AutomationElement? Document(AutomationElement window) => Elements(window, ControlType.Document)
        .FirstOrDefault(item => !item.Current.IsOffscreen
            && item.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern)
            && IsClaudeUrl(((ValuePattern)pattern).Current.Value));

    static bool IsChosenProfile(AutomationElement window) => Elements(window, ControlType.Button)
        .Any(item => ProfileNames.Contains(item.Current.Name, StringComparer.OrdinalIgnoreCase));

    static bool IsClaudeTab(AutomationElement tab) => tab.Current.Name.Contains("Claude", StringComparison.OrdinalIgnoreCase);

    static List<IntPtr> ChromeWindows()
    {
        var windows = new List<IntPtr>();
        EnumWindows((handle, _) => {
            if (!IsWindowVisible(handle)) return true;
            GetWindowThreadProcessId(handle, out var pid);
            try {
                using var process = Process.GetProcessById((int)pid);
                if (process.ProcessName.Equals("chrome", StringComparison.OrdinalIgnoreCase)) windows.Add(handle);
            } catch (ArgumentException) { }
            return true;
        }, IntPtr.Zero);
        return windows;
    }

    static IntPtr FindWindow()
    {
        var foreground = GetForegroundWindow();
        var candidates = ChromeWindows().OrderByDescending(h => h == rememberedWindow)
            .ThenByDescending(h => h == foreground).ToArray();
        IntPtr fallback = IntPtr.Zero;
        foreach (var handle in candidates) {
            try {
                var window = AutomationElement.FromHandle(handle);
                if (!IsChosenProfile(window)) continue;
                if (Document(window) != null || Elements(window, ControlType.TabItem).Any(IsClaudeTab)) return handle;
                if (fallback == IntPtr.Zero) fallback = handle;
            } catch (ElementNotAvailableException) { }
        }
        return fallback;
    }

    static void Foreground(IntPtr handle)
    {
        if (IsIconic(handle)) ShowWindow(handle, 9);
        SetForegroundWindow(handle);
        for (var i = 0; i < 10 && GetForegroundWindow() != handle; i++) Thread.Sleep(50);
        RequireForeground(handle);
    }

    static void RequireForeground(IntPtr handle)
    {
        if (GetForegroundWindow() != handle)
            throw new InvalidOperationException("Chrome 未在前台，已停止粘贴。请保持 Claude 窗口可见后重试。");
    }

    static void Click(IntPtr handle, AutomationElement element)
    {
        RequireForeground(handle);
        var point = element.GetClickablePoint();
        if (!SetCursorPos((int)point.X, (int)point.Y)) throw new InvalidOperationException("无法定位 Claude 输入框。");
        var down = new Input { Type = 0, Data = new InputUnion { Mouse = new MouseInput { Flags = 0x0002 } } };
        var up = new Input { Type = 0, Data = new InputUnion { Mouse = new MouseInput { Flags = 0x0004 } } };
        if (SendInput(2, new[] { down, up }, Marshal.SizeOf<Input>()) != 2)
            throw new InvalidOperationException("无法聚焦 Claude 输入框。");
    }

    static AutomationElement RequireDocument(IntPtr handle)
    {
        RequireForeground(handle);
        return Document(AutomationElement.FromHandle(handle))
            ?? throw new InvalidOperationException("当前页面不是已核实的 claude.ai，已停止粘贴。");
    }

    // Exact labels first, then Claude's ProseMirror editor, then any editor whose label names Claude.
    internal static int ComposerRank(string name, string className)
    {
        if (ComposerNames.Contains(name, StringComparer.OrdinalIgnoreCase)) return 0;
        if (className.Contains("ProseMirror", StringComparison.Ordinal)) return 1;
        if (name.Contains("Claude", StringComparison.OrdinalIgnoreCase)) return 2;
        return -1;
    }

    static AutomationElement? Composer(AutomationElement doc) => Elements(doc, ControlType.Edit)
        .Where(item => item.Current.IsEnabled && !item.Current.IsOffscreen)
        .Select(item => (item, rank: ComposerRank(item.Current.Name ?? "", item.Current.ClassName ?? "")))
        .Where(pair => pair.rank >= 0).OrderBy(pair => pair.rank).Select(pair => pair.item).FirstOrDefault();

    internal static bool NamesAttachment(string elementName, string fileName) =>
        elementName.Contains(Path.GetFileNameWithoutExtension(fileName), StringComparison.OrdinalIgnoreCase);

    static bool HasAttachment(AutomationElement doc, string name) => doc.FindAll(TreeScope.Descendants, AttachmentParts)
        .Cast<AutomationElement>().Any(item => NamesAttachment(item.Current.Name ?? "", name));

    static AutomationElement? RemoveButton(AutomationElement doc, string name) => Elements(doc, ControlType.Button)
        .FirstOrDefault(item => RemovePrefixes.Any(prefix => item.Current.Name == prefix + name));

    internal static bool IsSendName(string name) => SendNames.Contains(name, StringComparer.OrdinalIgnoreCase);

    static bool EditorHasFocus(AutomationElement editor)
    {
        var focused = AutomationElement.FocusedElement;
        for (var i = 0; focused != null && i < 8; i++) {
            if (Automation.Compare(editor, focused)) return true;
            focused = TreeWalker.ControlViewWalker.GetParent(focused);
        }
        return false;
    }

    static bool Attach(string path, string chrome, string expectedHash)
    {
        var handle = FindWindow();
        if (handle == IntPtr.Zero) {
            // Never launch another window while an existing Chrome needs a profile selection.
            if (ChromeWindows().Count != 0)
                throw new InvalidOperationException("请在现有 Chrome 打开 Ledger User 个人资料，然后重试；没有新开重复窗口。");
            Process.Start(new ProcessStartInfo(chrome, Home) { UseShellExecute = true });
            for (var i = 0; i < 30 && handle == IntPtr.Zero; i++) {
                Thread.Sleep(500);
                handle = FindWindow();
            }
            if (handle == IntPtr.Zero)
                throw new InvalidOperationException("Chrome 已打开。请选择 Ledger User 并登录 Claude 后重试。");
        }
        rememberedWindow = handle;
        Foreground(handle);
        var window = AutomationElement.FromHandle(handle);
        if (Document(window) == null) {
            var tab = Elements(window, ControlType.TabItem).FirstOrDefault(IsClaudeTab);
            if (tab != null) {
                if (tab.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var pattern))
                    ((SelectionItemPattern)pattern).Select();
                else Click(handle, tab);
            } else {
                RequireForeground(handle);
                Forms.SendKeys.SendWait("^t");
                Thread.Sleep(150);
                RequireForeground(handle);
                var address = AutomationElement.FocusedElement;
                if (address?.Current.ControlType != ControlType.Edit || address.Current.Name is not
                    ("地址和搜索栏" or "Address and search bar" or "Address bar"))
                    throw new InvalidOperationException("未确认 Chrome 地址栏焦点，请手动打开 claude.ai 后重试。");
                Forms.Clipboard.SetText(Home);
                Forms.SendKeys.SendWait("^v");
                RequireForeground(handle);
                if (!Automation.Compare(address, AutomationElement.FocusedElement))
                    throw new InvalidOperationException("Chrome 焦点发生变化，已停止打开页面。");
                Forms.SendKeys.SendWait("{ENTER}"); // Address bar navigation only. Never submitted in a composer.
            }
        }

        AutomationElement? doc = null;
        AutomationElement? composer = null;
        for (var i = 0; i < 40; i++) {
            RequireForeground(handle);
            doc = Document(AutomationElement.FromHandle(handle));
            composer = doc == null ? null : Composer(doc);
            if (composer != null) break;
            Thread.Sleep(250);
        }
        if (composer == null || doc == null)
            throw new InvalidOperationException("已复用 Chrome，但尚未找到 Claude 输入框。请完成登录或关闭弹窗后重试。");

        var name = Path.GetFileName(path);
        // Replace this exact managed filename so a maintained overview cannot silently stay stale.
        var previous = RemoveButton(doc, name);
        if (previous != null) {
            RequireDocument(handle);
            if (previous.TryGetCurrentPattern(InvokePattern.Pattern, out var remove)) ((InvokePattern)remove).Invoke();
            else Click(handle, previous);
            Thread.Sleep(200);
            doc = RequireDocument(handle);
            if (RemoveButton(doc, name) != null) throw new InvalidOperationException("旧的同名附件未移除，请核对草稿后重试。");
            composer = Composer(doc) ?? throw new InvalidOperationException("Claude 输入框暂不可用。");
        }
        var alreadyAttached = HasAttachment(doc, name);
        Click(handle, composer);
        RequireDocument(handle);
        // UIA exposes Chrome's contenteditable editor as an Edit. Do not select/replace its draft text.
        composer.SetFocus();
        for (var i = 0; i < 10 && !EditorHasFocus(composer); i++) Thread.Sleep(50);
        if (!EditorHasFocus(composer)) throw new InvalidOperationException("无法核实 Claude 输入框焦点，未执行粘贴。");
        RequireForeground(handle);
        if (!Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(path)))
            .Equals(expectedHash, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("账本资料已更新，请重新点击对应范围生成文件。");
        Forms.Clipboard.SetFileDropList(new System.Collections.Specialized.StringCollection { path });
        var copied = Forms.Clipboard.GetFileDropList();
        if (copied.Count != 1 || !string.Equals(copied[0], path, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("文件剪贴板核对失败，已停止粘贴。");
        Forms.SendKeys.SendWait("^v");

        // Wait for the attachment card and an enabled Send button before deleting any local file.
        // If a same-name card was already in the draft, it cannot prove this paste, so report "not confirmed".
        if (alreadyAttached) { Thread.Sleep(1500); return false; }
        var readyCount = 0;
        for (var i = 0; i < 90; i++) {
            Thread.Sleep(500);
            doc = Document(AutomationElement.FromHandle(handle));
            if (doc == null) break;
            var buttons = Elements(doc, ControlType.Button);
            var ready = HasAttachment(doc, name)
                && buttons.Any(button => IsSendName(button.Current.Name) && button.Current.IsEnabled)
                && !buttons.Any(button => button.Current.Name is "重试上传" or "重试" or "Retry upload" or "Retry");
            readyCount = ready ? readyCount + 1 : 0;
            if (readyCount >= 2) return true;
        }
        return false; // Keep file for manual verification; never retry paste automatically.
    }

    [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public InputUnion Data; }
    [StructLayout(LayoutKind.Explicit)] struct InputUnion { [FieldOffset(0)] public MouseInput Mouse; }
    [StructLayout(LayoutKind.Sequential)] struct MouseInput {
        public int X, Y; public uint MouseData, Flags, Time; public UIntPtr ExtraInfo;
    }
    delegate bool EnumWindowsProc(IntPtr handle, IntPtr param);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc callback, IntPtr param);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr handle);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr handle, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr handle, out uint pid);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] static extern uint SendInput(uint count, Input[] inputs, int size);
}
