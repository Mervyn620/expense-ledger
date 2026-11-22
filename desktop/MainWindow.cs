using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;
using Windows.Graphics;
using Forms = System.Windows.Forms;

namespace Expenses.Desktop;

public sealed partial class MainWindow : Window
{
    readonly string root;
    readonly DispatcherQueueTimer monitor;
    readonly Forms.NotifyIcon tray;
    readonly DesktopJobTracker jobTracker = new();
    BackendService? backend;
    bool starting, exiting, closingDialog, polling;
    bool pageReady;
    ulong webNavigationId;
    string? desktopBridgeScriptId, registeredVisualMode;
    bool? registeredGameMode, registeredGlassEnabled;
    double? registeredZoom;
    string? registeredUiTone, registeredAppearance, registeredBillPlatforms, registeredLanguage;
    readonly System.Threading.SemaphoreSlim bridgeRegistration = new(1,1);
    int pollFailures;
    public MainWindow(string root)
    {
        InitializeComponent();
        Program.Trace("Window XAML loaded");
        this.root = root;
        Title = "账本 · 桌面版";
        SystemBackdrop = new MicaBackdrop();
        layout.Background = new SolidColorBrush(ColorFromStartup());
        InitializeNativeShell();
        Title = Loc.T("账本 · 桌面版");
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var scale = WindowScale();
        AppWindow.Resize(new SizeInt32(Math.Min(area.Width, (int)(1320 * scale)), Math.Min(area.Height, (int)(880 * scale))));
        RestoreWindow();
        var icon = Path.Combine(root, "desktop", "Assets", "ledger.ico");
        if (File.Exists(icon)) AppWindow.SetIcon(icon);
        tray = new Forms.NotifyIcon {
            Text = Loc.T("账本 · 桌面版"),
            Icon = File.Exists(icon) ? new System.Drawing.Icon(icon) : System.Drawing.SystemIcons.Application,
            Visible = true
        };
        var menu = new Forms.ContextMenuStrip();
        trayOpenItem = menu.Items.Add(Loc.T("打开账本"), null, (_, _) => DispatcherQueue.TryEnqueue(ShowWindow));
        trayExitItem = menu.Items.Add(Loc.T("退出账本"), null, (_, _) => DispatcherQueue.TryEnqueue(() => _ = RequestCloseAsync()));
        tray.ContextMenuStrip = menu;
        tray.DoubleClick += (_, _) => DispatcherQueue.TryEnqueue(ShowWindow);
        retry.Click += (_, _) => _ = StartAsync();
        webRetry.Click += (_, _) => _ = StartWebAsync();
        webOpenOrders.Click += async (_, _) => await NavigateSafelyAsync("orders");
        logs.Click += (_, _) => Process.Start(new ProcessStartInfo(Path.Combine(root, ".desktop-state")) { UseShellExecute = true });
        AppWindow.Closing += (sender, e) => { if (!exiting) { e.Cancel = true; HideToTray(); } };
        Closed += (_, _) => {
            CancelOrderRequests(); CancelFinanceRequest();
            StopVisualEffects();
            DisposeAppearance();
            monitor?.Stop(); tray.Visible = false; tray.Dispose();
            backend?.Dispose();
        };
        monitor = DispatcherQueue.CreateTimer();
        monitor.Interval = TimeSpan.FromSeconds(3);
        monitor.Tick += (_, _) => _ = PollAsync();
        AppWindow.Changed += (sender, args) => { if (args.DidVisibilityChange || args.DidPresenterChange) { ApplyVisualMode(); _ = UpdateResourceModeAsync(); } };
        Activated += (sender, args) => { ApplyVisualMode(); _ = UpdateResourceModeAsync(); };
        BeginStartupEffect();
        layout.Loaded += async (_, _) => await VerifyStartupNativeAsync();
        layout.Loaded += async (_, _) => await VerifyWindowBehaviorAsync();
        browser.SizeChanged += async (_, _) => await SyncStarfieldFrameAsync();
    }

    static Windows.UI.Color ColorFromStartup() => Windows.UI.Color.FromArgb(255, 7, 16, 30);

    public void ShowWindow()
    {
        AppWindow.Show();
        ApplyVisualMode();
        if (AppWindow.Presenter is OverlappedPresenter p && p.State == OverlappedPresenterState.Minimized) p.Restore();
        Activate();
        if (shellReady) ShowRoute();
        _ = PollAsync();
        _ = UpdateResourceModeAsync();
    }

    bool trayHintShown;
    void HideToTray()
    {
        SaveWindow();
        AppWindow.Hide();
        StopVisualEffects();
        _ = UpdateResourceModeAsync();
        WriteState("background");
        if (!trayHintShown) {
            trayHintShown = true;
            tray.ShowBalloonTip(3000, Loc.T("账本仍在运行"), Loc.T("双击托盘图标可重新打开；右键可退出账本。"), Forms.ToolTipIcon.Info);
        }
    }

    public async Task StartAsync()
    {
        if (starting || exiting) return;
        starting = true; pageReady = false; backendReady = false; shellReady = false; CancelOrderRequests(); CancelFinanceRequest();
        monitor.Stop();
        connectionFailed = false;
        loading.Visibility = Visibility.Visible; browser.Visibility = startupActive ? Visibility.Visible : Visibility.Collapsed;
        heading.Text = "正在打开账本"; message.Text = "连接本地账本，马上就好。";
        progress.IsActive = true; progress.Visibility = Visibility.Visible;
        if (!startupActive) BeginStartupEffect();
        retry.Visibility = logs.Visibility = Visibility.Collapsed;
        try {
            if (backend == null || !backend.Running) {
                backend?.Dispose(); backend = new BackendService(root);
                await backend.StartAsync();
            } else { using var status = await backend.StatusAsync(); }
            if (exiting) return;
            backendReady = true;
            _ = StartWebAsync();
            _ = RevealNativeShellAsync();
            pollFailures = 0;
            monitor.Start();
            WriteState("loading");
        } catch (Exception ex) {
            if (exiting) return;
            if (backend?.Running != true) { backend?.Dispose(); backend = null; }
            Log(ex);
            ShowError("暂时无法打开账本", ex.Message);
        } finally { starting = false; if (backendReady) await PollAsync(); }
    }

    bool backendReady, shellReady, webFailed, webStarting, webNeedsRecreate;
    async Task RevealNativeShellAsync()
    {
        await WaitForStartupEffectAsync();
        if (exiting || !backendReady) return;
        shellReady = true;
        ShowRoute();
        if (webFailed || IsNativeRoute || webFrameReady) await RevealStartupContentAsync();
    }
    async Task StartWebAsync()
    {
        if (webStarting || !backendReady || exiting) return;
        webStarting = true; webFailed = false; pageReady = false;
        try {
            ResumeWeb();
            if (webNeedsRecreate) {
                var index = nativeContent.Children.IndexOf(browser);
                browser.Close(); nativeContent.Children.Remove(browser);
                browser = new Microsoft.UI.Xaml.Controls.WebView2 {DefaultBackgroundColor=ColorFromStartup(), Visibility=Visibility.Collapsed};
                nativeContent.Children.Insert(index, browser);
                browser.SizeChanged += async (_, _) => await SyncStarfieldFrameAsync();
                desktopBridgeScriptId = null; webNeedsRecreate = false;
            }
            // Page motion follows the app's "减少动态效果" switch (data-motion), not the Windows animation
            // setting, so CSS and JS prefers-reduced-motion never match inside the desktop host.
            var env = await CoreWebView2Environment.CreateWithOptionsAsync(null, Path.Combine(root, ".desktop-state", "webview"),
                new CoreWebView2EnvironmentOptions { AdditionalBrowserArguments = "--force-prefers-no-reduced-motion" });
            if (browser.CoreWebView2 == null) {
                await browser.EnsureCoreWebView2Async(env);
                await RegisterDesktopBridgeAsync();
                browser.CoreWebView2!.WebMessageReceived += ReceiveWebMessage;
                browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
                // Free browser zoom (25%-500%) is replaced by the three interface sizes.
                browser.CoreWebView2.Settings.IsZoomControlEnabled = false;
                browser.CoreWebView2.Settings.IsStatusBarEnabled = false;
                browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                browser.CoreWebView2.Settings.IsGeneralAutofillEnabled = false;
                browser.CoreWebView2.Settings.IsPasswordAutosaveEnabled = false;
                browser.CoreWebView2.NavigationStarting += (sender, e) => {
                    if (!IsLocal(e.Uri)) { e.Cancel = true; if (e.IsUserInitiated) OpenExternal(e.Uri); }
                    // The analysis page is native now; old links into /analysis open it instead of the Web copy.
                    else if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var target) && target.AbsolutePath == "/analysis") {
                        e.Cancel = true; DispatcherQueue.TryEnqueue(() => _ = NavigateSafelyAsync("analysis"));
                    }
                    else { webNavigationId = e.NavigationId; webFrameReady = false; }
                };
                browser.CoreWebView2.NewWindowRequested += (sender, e) => {
                    e.Handled = true;
                    if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var target) && IsLocal(e.Uri) && target.AbsolutePath == "/analysis") _ = NavigateSafelyAsync("analysis");
                    else if (IsLocal(e.Uri)) browser.CoreWebView2.Navigate(e.Uri);
                    else if (e.IsUserInitiated) OpenExternal(e.Uri);
                };
                browser.CoreWebView2.PermissionRequested += (_, e) => e.State = CoreWebView2PermissionState.Deny;
                browser.CoreWebView2.DownloadStarting += (_, e) => e.Cancel = true;
                browser.CoreWebView2.ProcessFailed += (_, _) => {
                    webNeedsRecreate = true;
                    ShowWebError("网页显示暂时不可用", "订单、现金和付款页面仍可使用。可以单独重新加载网页。");
                };
                browser.CoreWebView2.NavigationCompleted += async (sender, e) => {
                    if (exiting || e.WebErrorStatus == CoreWebView2WebErrorStatus.OperationCanceled) return;
                    if (e.IsSuccess) {
                        if (exiting || e.NavigationId != webNavigationId) return;
                        var contentReady = browser.Source?.AbsolutePath != "/" || await WaitForWebContentAsync(e.NavigationId);
                        if (exiting || e.NavigationId != webNavigationId) return;
                        if (!contentReady) {
                            ShowWebError("总览暂时未能加载", "请重新加载网页；原生订单和其他页面仍可使用。"); return;
                        }
                        pageReady = true; webFailed = false;
                        var path = browser.Source?.AbsolutePath;
                        if (!IsNativeRoute) {
                            if (path == "/analysis") currentRoute = "analysis";
                            else if (path == "/" && currentRoute == "analysis") currentRoute = "overview";
                        }
                        await SyncAppearanceFromWebAsync();
                        await SyncVisualModeToWebAsync();
                        if (!startupActive) shellReady = true;
                        ShowRoute();
                        if (currentRoute == "orders" && !listValid) await LoadOrdersAsync();
                        if (currentRoute is "cash" or "sources" && !financeValid) await LoadFinanceAsync();
                        if (path == "/") { await SendWebRouteAsync(); await OpenPendingAppearanceAsync(); }
                        if (startupActive) {
                            if (!await PrepareWebFrameAsync(e.NavigationId)) {
                                if (!exiting && e.NavigationId == webNavigationId) ShowWebError("页面暂时未能显示", "请重新加载网页，或继续使用原生页面。");
                                return;
                            }
                            await WaitForStartupEffectAsync();
                            if (exiting || e.NavigationId != webNavigationId) return;
                            shellReady = true; ShowRoute();
                            await RevealStartupContentAsync();
                        }
                        if (!IsNativeRoute) browser.Focus(FocusState.Programmatic);
                        _ = UpdateResourceModeAsync();
                        WriteState("ready");
                        _ = StartFirstRunAsync();
                        _ = CaptureDiagnosticsAsync();
                        VerifyChatGptClipboard();
                        _ = VerifyPhaseTwoAsync();
                        _ = VerifyPhaseThreeAsync();
                        _ = VerifyPhaseFourAsync();
                        _ = VerifyManualWechatAsync();
                        _ = VerifyAppearanceAsync();
                        _ = VerifyGlassAsync();
                        _ = VerifyWallpaperLayoutAsync();
                        _ = VerifyBillPlatformsAsync();
                        _ = VerifyFeedbackAsync();
                        _ = VerifyOptimizationAsync();
                        _ = VerifyGameModeAsync();
                        _ = VerifyLanguageAsync();
                    } else ShowWebError("页面未能打开", "可以重新加载网页，或继续使用原生页面。");
                };
            }
            browser.Source = new Uri(backend!.Url + "/");

        } catch (Exception error) { Log(error); ShowWebError("网页暂时未能打开", "原生页面仍可使用。可以稍后重新加载网页。"); }
        finally { webStarting = false; }
    }
    async Task<bool> WaitForWebContentAsync(ulong navigationId)
    {
        // Document load precedes ledger rendering. A stale navigation cannot reveal a new page.
        try {
            for (var attempt = 0; attempt < 150 && !exiting && navigationId == webNavigationId; attempt++) {
                if (await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true && !!document.querySelector('[data-panel=overview] .metric-card')") == "true") return true;
                await Task.Delay(100);
            }
        } catch (Exception error) { if (!exiting) Log(error); }
        return false;
    }
    void ShowWebError(string title, string detail)
    {
        pageReady = false; webFailed = true;
        webErrorTitle.Text = title; webErrorMessage.Text = detail;
        if (shellReady) { ShowRoute(); _ = RevealStartupContentAsync(); }
        WriteState("web-error");
    }

    async Task RegisterDesktopBridgeAsync()
    {
        await bridgeRegistration.WaitAsync();
        try {
            var core = browser.CoreWebView2;
            var mode = preferences.VisualMode; var tone = preferences.UiTone;
            var appearance = preferences.HasAppearanceCache ? JsonSerializer.Serialize(preferences.Appearance.Normalize()) : "null";
            var billPlatforms = JsonSerializer.Serialize(preferences.DefaultBillPlatforms);
            var game = preferences.GameModeEnabled; var glass = !appearanceHighContrast && appearanceEffectsEnabled; var zoom = preferences.UiScaleFactor;
            if (core == null || desktopBridgeScriptId != null && registeredVisualMode == mode && registeredGameMode == game && registeredUiTone == tone && registeredGlassEnabled == glass && registeredAppearance == appearance && registeredBillPlatforms == billPlatforms && registeredZoom == zoom && registeredLanguage == Loc.Language) return;
            if (desktopBridgeScriptId != null) core.RemoveScriptToExecuteOnDocumentCreated(desktopBridgeScriptId);
            var script = "window.__expenseDesktopChatGptAutoPaste = true; window.__expenseDesktopWallpaperLayout = true; window.__expenseDesktopNativeOrders = true; window.__expenseDesktopNativeFinance = true; window.__expenseDesktopNativeAppearance = true; window.__expenseDesktopAutomaticTone = true; window.__expenseDesktopVisualMode = " +
                JsonSerializer.Serialize(mode) + "; window.__expenseDesktopAppearanceSnapshot = " + appearance + "; window.__expenseDesktopUiTone = " + JsonSerializer.Serialize(tone) + "; window.__expenseDesktopGlassEnabled = " + JsonSerializer.Serialize(glass) + "; window.__expenseDesktopGameMode = " + JsonSerializer.Serialize(game) + "; window.__expenseDesktopDefaultBillPlatforms = " + billPlatforms + "; window.__expenseDesktopZoom = " + zoom.ToString(System.Globalization.CultureInfo.InvariantCulture) + ";\n" +
                WebLanguageScript() +
                File.ReadAllText(Path.Combine(root, "backend", "app", "static", "desktop-bridge.js"));
            desktopBridgeScriptId = await core.AddScriptToExecuteOnDocumentCreatedAsync(script);
            registeredVisualMode = mode;
            registeredGameMode = game;
            registeredUiTone = tone;
            registeredAppearance = appearance;
            registeredBillPlatforms = billPlatforms;
            registeredGlassEnabled = glass;
            registeredZoom = zoom;
            registeredLanguage = Loc.Language;
        } finally { bridgeRegistration.Release(); }
    }

    async Task CaptureDiagnosticsAsync()
    {
        if (Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY") != "1") return;
        try {
            await Task.Delay(1500);
            WriteState("ready");
            var metrics = await browser.CoreWebView2.ExecuteScriptAsync("JSON.stringify({width:innerWidth,height:innerHeight,ready:document.readyState,title:document.title,bodyWidth:document.body.getBoundingClientRect().width})");
            File.WriteAllText(Path.Combine(root, ".desktop-state", "render-metrics.json"), metrics);
            var file = await Windows.Storage.StorageFile.GetFileFromPathAsync(CreateCaptureFile());
            using var stream = await file.OpenAsync(Windows.Storage.FileAccessMode.ReadWrite);
            await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
        } catch (Exception ex) { Log(ex); }
    }
    string CreateCaptureFile()
    {
        var file = Path.Combine(root, ".desktop-state", "render-preview.png");
        File.WriteAllBytes(file, Array.Empty<byte>());
        return file;
    }

    bool IsLocal(string address)
    {
        return Uri.TryCreate(address, UriKind.Absolute, out var uri) &&
               backend?.Url != null && uri.GetLeftPart(UriPartial.Authority) == backend.Url;
    }
    static void OpenExternal(string address)
    {
        if (Uri.TryCreate(address, UriKind.Absolute, out var uri) && (uri.Scheme == "https" || (uri.Scheme == "http" && uri.IsLoopback)))
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    }
    bool connectionFailed;
    void ShowError(string title, string detail)
    {
        connectionFailed = true;
        CancelOrderRequests(); CancelFinanceRequest();
        loading.Visibility = Visibility.Visible; browser.Visibility = Visibility.Collapsed;
        nativePageSurface.Visibility = Visibility.Collapsed;
        financePanel.Visibility = ordersPanel.Visibility = settingsPanel.Visibility = maintenancePanel.Visibility = Visibility.Collapsed;
        heading.Text = title; message.Text = detail;
        loadingCard.Background = SurfaceGlass; loadingCard.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)layout.Resources["LedgerEdgeBrush"];
        loadingCard.BorderThickness = new Thickness(1); loadingCard.Padding = new Thickness(24);
        progress.IsActive = false; progress.Visibility = Visibility.Collapsed;
        AbortStartupEffect();
        ApplyVisualMode();
        retry.Visibility = logs.Visibility = Visibility.Visible;
        WriteState("error");
    }
    async Task PollAsync()
    {
        if (polling || starting || exiting || backend == null) return;
        polling = true;
        try {
            using var status = await backend.StatusAsync();
            pollFailures = 0;
            RenderNativeStatus(status.RootElement);
            bool busy = status.RootElement.GetProperty("busy").GetBoolean();
            lastBackendBusy = busy;
            UpdateCollectorLocationControls();
            _ = UpdateResourceModeAsync();
            Title = Loc.T(busy ? "账本 · 桌面版 · 任务进行中" : "账本 · 桌面版");
            tray.Text = Loc.T(busy ? "账本 · 任务进行中" : "账本 · 桌面版");
            foreach (var completed in jobTracker.Observe(status.RootElement.GetProperty("jobs"))) {
                if (completed.ChangesLedger) InvalidateNativeLedger();
                if (preferences.CompletionNotifications)
                    tray.ShowBalloonTip(4000, Loc.T(completed.Label), Loc.T(completed.Status == "passed" ? "已完成" : completed.Status == "partial" ? "部分完成，打开更新页面查看。" : "未完成，打开更新页面查看。"),
                        completed.Status == "passed" ? Forms.ToolTipIcon.Info : Forms.ToolTipIcon.Warning);
            }
            await RefreshChangedLedgerAsync();
        } catch (Exception ex) {
            if (exiting) return;
            pollFailures++;
            if (pollFailures >= 3 && !starting) {
                monitor.Stop(); Log(ex);
                ShowError("账本连接已断开", "重新连接后可继续使用；原有账本数据仍保存在本地。");
            }
        } finally { polling = false; }
    }

    async Task RequestCloseAsync()
    {
        if (exiting || closingDialog) return;
        if (cashNoteDialog != null) { ShowWindow(); return; }
        closingDialog = true;
        try {
            if (starting) { exiting = true; backend?.Dispose(); Close(); return; }
            bool busy = false, uncertain = false;
            if (backend?.Running == true) {
                try { using var status = await backend.StatusAsync(); busy = status.RootElement.GetProperty("busy").GetBoolean(); }
                catch { uncertain = true; }
            }
            bool force = false;
            if (busy || uncertain) {
                ShowWindow();
                var dialog = new ContentDialog {
                    XamlRoot = layout.XamlRoot,
                    Title = busy ? "还有任务正在进行" : "暂时无法确认任务状态",
                    Content = busy ? "你可以关闭窗口并在后台继续。完成后，账本会提示你。" : "可以暂时保留后台服务，或退出并停止本次桌面版的任务。",
                    PrimaryButtonText = "后台继续", SecondaryButtonText = "退出并停止任务",
                    CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary
                };
                ConfigureLedgerDialog(dialog);
                var choice = await dialog.ShowAsync();
                if (choice == ContentDialogResult.Primary) { HideToTray(); return; }
                if (choice != ContentDialogResult.Secondary) return;
                force = true;
            }
            if (backend?.Running == true && !await backend.StopAsync(force)) return;
            SaveWindow(); exiting = true; WriteState("closed"); Close();
        } catch (Exception ex) {
            Log(ex); ShowError("暂时无法退出", "任务状态尚未确认，请稍后重试。");
        } finally { closingDialog = false; }
    }

    [DllImport("user32.dll")]
    static extern uint GetDpiForWindow(IntPtr window);
    double WindowScale() => Math.Max(1.0, GetDpiForWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)) / 96.0);

    void SaveWindow()
    {
        try {
            if (AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen) return;
            var maximized = AppWindow.Presenter is OverlappedPresenter p && p.State == OverlappedPresenterState.Maximized;
            var size = AppWindow.Size;
            var scale = WindowScale();
            var width = (int)(size.Width / scale); var height = (int)(size.Height / scale);
            // A temporary verification/transition size must not become the next startup size.
            if (width < 900 || height < 600) return;
            File.WriteAllText(Path.Combine(root, ".desktop-state", "window.json"),
                JsonSerializer.Serialize(new { format = 2, width, height, maximized }));
        } catch (IOException) { }
    }
    void RestoreWindow()
    {
        try {
            using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, ".desktop-state", "window.json")));
            var d = doc.RootElement;
            if (!d.TryGetProperty("format", out var format) || format.GetInt32() != 2) return;
            var scale = WindowScale();
            var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
            var w = Math.Clamp((int)(d.GetProperty("width").GetInt32() * scale), Math.Min((int)(900 * scale), area.Width), area.Width);
            var h = Math.Clamp((int)(d.GetProperty("height").GetInt32() * scale), Math.Min((int)(600 * scale), area.Height), area.Height);
            AppWindow.Resize(new SizeInt32(w, h));
            if (d.GetProperty("maximized").GetBoolean() && AppWindow.Presenter is OverlappedPresenter p) p.Maximize();
        } catch (Exception ex) when (ex is IOException or JsonException or KeyNotFoundException) { }
    }
    void Log(Exception ex)
    {
        try { File.AppendAllText(Path.Combine(root, ".desktop-state", "shell.log"), DateTimeOffset.Now + " " + ex + Environment.NewLine); } catch (IOException) { }
    }
    void WriteState(string phase)
    {
        try {
            var state = new { phase, pid = Environment.ProcessId, backend = backend?.Url, page_ready = pageReady, content_attached = Content == layout, root_width = layout.ActualWidth, root_height = layout.ActualHeight, web_width = browser.ActualWidth, web_height = browser.ActualHeight, at = DateTimeOffset.Now };
            File.WriteAllText(Path.Combine(root, ".desktop-state", "shell-state.json"), JsonSerializer.Serialize(state));
        } catch (IOException) { }
    }
}
