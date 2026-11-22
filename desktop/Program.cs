using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace Expenses.Desktop;

internal static class Program
{
    [DllImport("Microsoft.ui.xaml.dll")]
    static extern void XamlCheckProcessRequirements();
    internal static void Trace(string message) {
        var path = Path.Combine(FindRoot(), ".desktop-state"); Directory.CreateDirectory(path);
        File.AppendAllText(Path.Combine(path, "startup.log"), DateTimeOffset.Now + " " + message + Environment.NewLine);
    }
    [STAThread]
    static void Main()
    {
        try
        {
            var root = FindRoot();
            Trace("Main entered");
            var id = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(root.ToLowerInvariant())))[..16];
            using var mutex = new Mutex(true, @"Local\ExpensesDesktop-" + id, out bool first);
            using var wake = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\ExpensesDesktopWake-" + id);
            if (!first) { wake.Set(); return; }
            XamlCheckProcessRequirements(); Trace("Process requirements passed");
            WinRT.ComWrappersSupport.InitializeComWrappers(); Trace("ComWrappers initialized");
            Application.Start(args => {
                Trace("Application callback");
                var queue = DispatcherQueue.GetForCurrentThread();
                SynchronizationContext.SetSynchronizationContext(new DispatcherQueueSynchronizationContext(queue));
                _ = new DesktopApp(root, wake);
            });
        }
        catch (Exception ex)
        {
            Trace(ex.ToString());
            System.Windows.Forms.MessageBox.Show("账本桌面版未能启动。\n\n" + ex.Message, "账本 · 桌面版");
        }
    }
    static string FindRoot()
    {
        var configured = Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_ROOT");
        if (!string.IsNullOrEmpty(configured) && File.Exists(Path.Combine(configured, "expenses-desktop.json")))
            return Path.GetFullPath(configured);
        for (DirectoryInfo? d = new(AppContext.BaseDirectory); d != null; d = d.Parent)
            if (File.Exists(Path.Combine(d.FullName, "expenses-desktop.json"))) return d.FullName;
        throw new DirectoryNotFoundException("找不到独立桌面项目目录，请从桌面版快捷方式启动。");
    }
}

public sealed partial class DesktopApp : Application
{
    MainWindow? window;
    RegisteredWaitHandle? wakeRegistration;
    readonly string root;
    readonly EventWaitHandle wake;
    public DesktopApp(string root, EventWaitHandle wake)
    {
        this.root = root; this.wake = wake;
        Program.Trace("App constructed");
        InitializeComponent();
        Program.Trace("App resources loaded");
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        Program.Trace("OnLaunched");
        UnhandledException += (_, e) => {
            Directory.CreateDirectory(Path.Combine(root, ".desktop-state"));
            File.AppendAllText(Path.Combine(root, ".desktop-state", "shell.log"), e.Exception.ToString() + Environment.NewLine);
        };
        var queue = DispatcherQueue.GetForCurrentThread();
        window = new MainWindow(root);
        wakeRegistration = ThreadPool.RegisterWaitForSingleObject(wake, (_, _) => queue.TryEnqueue(() => window.ShowWindow()), null, -1, false);
        window.Closed += (_, _) => wakeRegistration.Unregister(null);
        window.Activate();
        _ = window.StartAsync();
    }
}
