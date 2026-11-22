using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    DesktopPreferences preferences = new();
    string currentRoute = "overview", jobsSignature = "";
    bool savingPreferences, maintenanceRefreshing, validationStarting, pendingAppearance;
    bool IsNativeRoute => currentRoute is "native-settings" or "native-maintenance" or "orders" or "cash" or "sources" or "analysis";

    void InitializeNativeShell()
    {
        settingsPanel.SizeChanged += (_, _) => settingsContent.Width = Math.Max(0, Math.Min(880, settingsPanel.ActualWidth - 48));
        maintenancePanel.SizeChanged += (_, _) => maintenanceContent.Width = Math.Max(0, Math.Min(880, maintenancePanel.ActualWidth - 48));
        InitializeCollectorLocation();
        InitializeBillPlatforms();
        InitializeOnboarding();
        InitializeFeedback();
        InitializeOrders();
        InitializeFinance();
        preferences = DesktopPreferences.Load(root);
        try { preferences.Save(root); } catch (Exception error) { Log(error); }
        InitializeAnalysis();
        appVersionText.Text = "账本桌面版 " + (typeof(MainWindow).Assembly.GetName().Version?.ToString(3) ?? "");
        completionNotifications.IsOn = preferences.CompletionNotifications;
        gameMode.IsOn = preferences.GameModeEnabled;
        UpdateGameModeControls();
        staticVisualMode.IsChecked = preferences.VisualMode == "static";
        particleVisualMode.IsChecked = preferences.VisualMode == "particle";
        InitializeAppearance();
        ApplyNativeTheme();
        ApplyVisualMode();
        workspaceLocation.Text = Path.Combine(root, "backend", "data");
        navigation.SelectedItem = navigation.MenuItems[0];
        navigation.ItemInvoked += async (_, args) => {
            if (args.InvokedItemContainer is NavigationViewItem item && item.Tag is string route) {
                BounceControl(item);
                await NavigateSafelyAsync(route);
                navigation.IsPaneOpen = false;
            }
        };
        completionNotifications.Toggled += (_, _) => SavePreferences();
        gameMode.Toggled += (_, _) => SavePreferences();
        openGame.Click += async (_, _) => await NavigateSafelyAsync("game");
        staticVisualMode.Checked += (_, _) => SavePreferences();
        particleVisualMode.Checked += (_, _) => SavePreferences();
        nativeOpenUpdates.Click += async (_, _) => await NavigateSafelyAsync("operations");
        nativeRefresh.Click += async (_, _) => await RefreshMaintenanceAsync();
        nativeValidate.Click += async (_, _) => await ValidateFromNativeAsync();
        advancedMaintenance.Click += async (_, _) => await NavigateSafelyAsync("maintenance");
        nativeLogs.Click += (_, _) => OpenFolder(Path.Combine(root, ".desktop-state"));
        openBackups.Click += (_, _) => OpenFolder(Path.Combine(root, "backend", "backups"));
        appearanceSettings.Click += async (_, _) => {
            pendingAppearance = true;
            await NavigateAsync("overview");
            await OpenPendingAppearanceAsync();
        };
        InitializeVisualInteractions();
        InitializeWallpaperLayout();
        InitializeUiScale();
        InitializeLocalization();
        ApplyNativeLanguage();
    }
    async Task OpenPendingAppearanceAsync()
    {
        if (!pendingAppearance || !pageReady || browser.CoreWebView2 == null || browser.Source?.AbsolutePath != "/") return;
        var opened = await browser.CoreWebView2.ExecuteScriptAsync("(() => {const button=document.querySelector('[data-open-appearance]'); if(!button)return false; button.click(); return true;})()");
        if (opened == "true") pendingAppearance = false;
    }
    void OpenFolder(string path)
    {
        try { Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo(path) {UseShellExecute = true}); }
        catch (Exception error) { NotifyNative(error.Message, true); }
    }
    void ApplyNativeTheme()
    {
        layout.RequestedTheme = preferences.UiTone == "light" ? ElementTheme.Light : ElementTheme.Dark;
        ApplyAppearanceVisuals();
    }
    void SavePreferences()
    {
        if (savingPreferences) return;
        savingPreferences = true;
        try {
            preferences.CompletionNotifications = completionNotifications.IsOn;
            preferences.GameModeEnabled = gameMode.IsOn;
            UpdateGameModeControls();
            preferences.VisualMode = particleVisualMode.IsChecked == true ? "particle" : "static";
            preferences.Save(root); ApplyNativeTheme(); ApplyVisualMode();
            _ = SyncVisualModeToWebAsync();
            settingsNotice.IsOpen = false;
        } catch (Exception error) {
            settingsNotice.Title = "设置未能保存"; settingsNotice.Message = error.Message;
            settingsNotice.Severity = InfoBarSeverity.Error; settingsNotice.IsOpen = true;
        } finally { savingPreferences = false; }
    }
    void UpdateGameModeControls()
    {
        updateNavigation.Visibility = Visibility.Visible;
        openGame.IsEnabled = preferences.GameModeEnabled;
    }
    void SelectNativeNavigation(string route)
    {
        var tag = route == "maintenance" ? "native-maintenance" : route == "web-orders" ? "orders" : route;
        var match = navigation.MenuItems.Concat(navigation.FooterMenuItems)
            .OfType<NavigationViewItem>().FirstOrDefault(item => item.Tag as string == tag);
        if (match != null) navigation.SelectedItem = match;
    }
    void ShowRoute()
    {
        if (connectionFailed) {
            loading.Visibility = Visibility.Visible; browser.Visibility = Visibility.Collapsed;
            financePanel.Visibility = ordersPanel.Visibility = settingsPanel.Visibility = maintenancePanel.Visibility = analysisPanel.Visibility = Visibility.Collapsed;
            webErrorPanel.Visibility = nativePageSurface.Visibility = nativePageReflection.Visibility = Visibility.Collapsed;
            return;
        }
        nativePageSurface.Visibility = webFailed && !IsNativeRoute ? Visibility.Visible : Visibility.Collapsed;
        nativePageReflection.Visibility = nativePageSurface.Visibility;
        financePanel.Visibility = currentRoute is "cash" or "sources" ? Visibility.Visible : Visibility.Collapsed;
        ordersPanel.Visibility = currentRoute == "orders" ? Visibility.Visible : Visibility.Collapsed;
        settingsPanel.Visibility = currentRoute == "native-settings" ? Visibility.Visible : Visibility.Collapsed;
        maintenancePanel.Visibility = currentRoute == "native-maintenance" ? Visibility.Visible : Visibility.Collapsed;
        analysisPanel.Visibility = currentRoute == "analysis" ? Visibility.Visible : Visibility.Collapsed;
        browser.Visibility = !IsNativeRoute && !webFailed && (pageReady && shellReady || startupActive) ? Visibility.Visible : Visibility.Collapsed;
        webErrorPanel.Visibility = !IsNativeRoute && webFailed && shellReady ? Visibility.Visible : Visibility.Collapsed;
        loading.Visibility = IsNativeRoute || pageReady && shellReady || webFailed && shellReady ? Visibility.Collapsed : Visibility.Visible;
        _ = UpdateResourceModeAsync();
        SelectNativeNavigation(currentRoute);
    }
    async Task NavigateSafelyAsync(string route)
    {
        try { await NavigateAsync(route); }
        catch (Exception error) { Log(error); NotifyNative("页面暂时无法打开，请稍后重试。", true); }
    }
    async Task NavigateAsync(string route)
    {
        if (route == "game") {
            if (!preferences.GameModeEnabled) return;
            if (preferences.VisualMode != "particle") particleVisualMode.IsChecked = true;
            route = "operations";
        }
        if (route is not ("overview" or "orders" or "cash" or "sources" or "operations" or "analysis" or "maintenance" or "native-settings" or "native-maintenance")) return;
        // Re-invoking the selected nav item must not cancel its in-flight detail request.
        if (cashNoteDialog != null) return;
        if (route == currentRoute && route is "orders" or "cash" or "sources") return;
        var previous = currentRoute;
        ResumeWeb();
        CancelOrderRequests(); CancelFinanceRequest();
        currentRoute = route; ShowRoute();
        if (route != previous) BeginRouteTransition();
        if (route is "cash" or "sources") {
            if (FinanceMonthValue == "latest" && pageReady && browser.CoreWebView2 != null) {
                var value = await browser.CoreWebView2.ExecuteScriptAsync("typeof state !== 'undefined' ? state.month : 'latest'");
                if (currentRoute != route) return;
                SetFinanceMonth(route, JsonSerializer.Deserialize<string>(value) ?? "latest");
            }
            await LoadFinanceAsync(); return;
        }
        if (route == "orders") {
            listValid = false; // Returning from another page may follow a completed sync/refund.
            if (!filtersLoaded && previous == "overview" && pageReady && browser.CoreWebView2 != null) {
                var monthJson = await browser.CoreWebView2.ExecuteScriptAsync("typeof state !== 'undefined' ? state.month : 'all'");
                if (currentRoute != "orders") return;
                var month = JsonSerializer.Deserialize<string>(monthJson);
                if (!string.IsNullOrWhiteSpace(month) && month != "latest") orderFilter = orderFilter with {Month = month};
            }
            if (previous == "web-orders") { listValid = false; if (detailOrderId is long id) { await OpenOrderDetailAsync(id); return; } }
            if (detailOrderId is long selected && previous != "orders") { await OpenOrderDetailAsync(selected); return; }
            if (previous != "orders" || !listValid) await LoadOrdersAsync();
            return;
        }
        if (route == "native-maintenance") { await RefreshMaintenanceAsync(); return; }
        if (route == "native-settings") { await RefreshCollectorLocationAsync(); await RefreshCollectionSettingsAsync(); await RefreshBankModeAsync(); return; }
        if (route == "analysis") { await LoadAnalysisAsync(); return; }
        if (IsNativeRoute) return;
        if (backend == null) return;
        if (webFailed || browser.CoreWebView2 == null) { await StartWebAsync(); return; }
        var path = browser.Source?.AbsolutePath;
        if (path != "/") {
            pageReady = false; browser.CoreWebView2.Navigate(backend.Url + "/");
        } else { await SyncVisualModeToWebAsync(); await SendWebRouteAsync(); }
    }
    async Task SendWebRouteAsync()
    {
        if (browser.CoreWebView2 == null || IsNativeRoute || currentRoute == "analysis") return;
        ResumeWeb();
        await browser.CoreWebView2.ExecuteScriptAsync($"window.expenseDesktopNavigate?.({JsonSerializer.Serialize(currentRoute == "web-orders" ? "orders" : currentRoute)}, {{ordersFallback:{(currentRoute == "web-orders" ? "true" : "false")}}})");
    }
    async void ReceiveWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        // Hidden WebView messages cannot replace the user's current native page.
        if (!IsLocal(args.Source)) return;
        try {
            using var message = JsonDocument.Parse(args.WebMessageAsJson);
            var data = message.RootElement;
            var type = LedgerLabels.Text(data, "type", "");
            if (type == "toggle-fullscreen") { ToggleFullScreen(); return; }
            if (type == "ui-scale-step" && data.TryGetProperty("step", out var step) && step.TryGetInt32(out var direction)) {
                StepUiScale(direction); return;
            }
            if (type is "chatgpt-handoff" or "chatgpt-copy-file" or "chatgpt-copy-question" or "chatgpt-delete-temp" or "chatgpt-open-export" or "chatgpt-copy-path") {
                await HandleChatGptMessageAsync(type, data);
                return;
            }
            if (type == "appearance" && data.TryGetProperty("appearance", out var appearance)) {
                ApplySharedAppearance(appearance); return;
            }
            if (type == "order-updated" && data.TryGetProperty("orderPk", out var updatedKey) && updatedKey.ValueKind == JsonValueKind.Number && updatedKey.TryGetInt64(out var updatedId) && updatedId > 0) {
                listValid = false;
                if (currentRoute == "orders") {
                    if (detailOrderId == updatedId) await OpenOrderDetailAsync(updatedId);
                    else if (detailOrderId == null) await LoadOrdersAsync();
                } else if (currentRoute == "sources") await LoadFinanceAsync();
                return;
            }
            if (IsNativeRoute) return;
            if (type == "native-finance") {
                var view = LedgerLabels.Text(data, "view", "");
                await OpenFinanceAsync(view, LedgerLabels.Text(data, "month", "all")); return;
            }
            if (type == "native-orders") {
                await OpenOrdersFromWebAsync(LedgerLabels.Text(data, "month", "all")); return;
            }
            if (type == "native-order" && data.TryGetProperty("orderPk", out var key) && key.TryGetInt64(out var id) && id > 0) {
                await OpenOrdersFromWebAsync(LedgerLabels.Text(data, "month", "all"), id); return;
            }
            if (type == "order-fallback-closed" && currentRoute == "web-orders") {
                listValid = false;
                if (detailOrderId is long selected) await OpenOrderDetailAsync(selected);
                else await NavigateAsync("orders");
                return;
            }
            if (type != "view") return;
            var route = LedgerLabels.Text(data, "view", "");
            // New hosts receive explicit native messages for migrated views. Ignore stale Web echoes.
            if (route is not ("overview" or "operations" or "maintenance")) return;
            currentRoute = route; ShowRoute();
        } catch (Exception error) { Log(error); }
    }
    void NotifyNative(string message, bool error = false)
    {
        var notice = currentRoute is "cash" or "sources" ? financeNotice : currentRoute == "orders" ? ordersNotice : currentRoute == "native-settings" ? settingsNotice : currentRoute == "analysis" ? analysisNotice : maintenanceNotice;
        notice.Title = Loc.T(error ? "暂时未能完成" : "账本"); notice.Message = Loc.T(message);
        notice.Severity = error ? InfoBarSeverity.Error : InfoBarSeverity.Informational; notice.IsOpen = true;
    }
    void RenderNativeStatus(JsonElement status)
    {
        bool busy = status.GetProperty("busy").GetBoolean();
        nativeServiceStatus.Text = "已连接 · 本地账本服务正常";
        var active = status.GetProperty("active_job");
        nativeActiveJob.Text = active.ValueKind == JsonValueKind.Object
            ? "正在进行：" + active.GetProperty("label").GetString()
            : busy ? "正在处理请求" : "当前没有运行中的任务";
        nativeValidate.IsEnabled = !busy && !validationStarting;
        RenderCollectionStatus(status);
        var jobs = status.GetProperty("jobs");
        var signature = jobs.GetRawText();
        if (signature == jobsSignature) return;
        jobsSignature = signature; nativeJobList.Children.Clear();
        foreach (var job in jobs.EnumerateArray()) {
            var label = job.GetProperty("label").GetString() ?? "账本任务";
            var state = job.GetProperty("status").GetString();
            var description = state switch {"passed" => "已完成", "partial" => "部分完成", "failed" => "未完成", "queued" => "等待中", "running" => "进行中", _ => "未知状态"};
            var row = new StackPanel {Spacing = 3};
            row.Children.Add(new TextBlock {Text = label, TextWrapping = TextWrapping.Wrap, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold});
            var started = JobTime(job, "started_at");
            var elapsed = JobDuration(job);
            row.Children.Add(new TextBlock {Text = string.Join(" · ", new[] {description, started, elapsed}.Where(x => x.Length > 0)), FontSize = 13, Opacity = 0.72, TextWrapping = TextWrapping.Wrap});
            nativeJobList.Children.Add(row);
        }
        if (nativeJobList.Children.Count == 0) nativeJobList.Children.Add(new TextBlock {Text="还没有运行记录"});
    }
    string collectionSignature = "";
    void RenderCollectionStatus(JsonElement status)
    {
        if (!status.TryGetProperty("collection", out var collection) || collection.ValueKind != JsonValueKind.Object) {
            collectionSignature = "";
            nativeCollectionSummary.Text = "还没有可显示的采集记录";
            nativeCollectionTime.Text = nativeCollectionAdvice.Text = "";
            nativeCollectionDetails.Children.Clear();
            return;
        }
        var signature = collection.GetRawText();
        if (signature == collectionSignature) return;
        collectionSignature = signature;
        string Seconds(JsonElement item, string field) => LedgerLabels.Number(item, field) is decimal value ? value.ToString("0.#") + " 秒" : "耗时未记录";
        string Status(string value) => value switch {"passed" => "完成", "partial" => "部分完成", "failed" => "失败", "login_required" => "需要登录", "blocked" => "需要人工处理", "cooldown" => "等待后再试", "skipped" => "已跳过", "running" => "进行中", _ => "未知"};
        nativeCollectionSummary.Text = LedgerLabels.Text(collection, "backend", "") + " · " + Status(LedgerLabels.Text(collection, "status", "")) + " · " + Seconds(collection, "duration_seconds");
        var started = JobTime(collection, "started_at");
        nativeCollectionTime.Text = started.Length > 0 ? "开始于 " + started : "";
        nativeCollectionAdvice.Text = "";
        nativeCollectionDetails.Children.Clear();
        foreach (var component in LedgerLabels.Rows(collection, "components")) {
            var id = LedgerLabels.Text(component, "id", "");
            var payment = id.EndsWith("_payment", StringComparison.Ordinal);
            var platform = payment ? id[..^8] : id;
            var componentStatus = LedgerLabels.Text(component, "status", "");
            if (componentStatus == "login_required") nativeCollectionAdvice.Text = "部分商店需要重新登录。打开更新账单可查看处理入口；未更新的平台保留原有账本数据。";
            else if (componentStatus is "blocked" or "cooldown" && nativeCollectionAdvice.Text.Length == 0) nativeCollectionAdvice.Text = "部分商店需要人工处理或等待后再试，可打开更新账单查看详情。";
            var cached = component.TryGetProperty("cached", out var cache) && cache.ValueKind == JsonValueKind.True;
            nativeCollectionDetails.Children.Add(DetailText(LedgerLabels.Platform(platform) + (payment ? " · 付款信息" : " · 订单") + "：" + Status(LedgerLabels.Text(component, "status", "")) + " · " + Seconds(component, "duration_seconds") + (cached ? " · 保留上次数据" : "")));
        }
        var timings = LedgerLabels.Rows(collection, "timings").ToList();
        if (timings.Count > 0) {
            var content = new StackPanel {Spacing = 8};
            foreach (var timing in timings) content.Children.Add(DetailText(LedgerLabels.Text(timing, "label", "") + "：" + Seconds(timing, "seconds")));
            nativeCollectionDetails.Children.Add(new Expander {Header="查看耗时明细", Content=content, HorizontalAlignment=HorizontalAlignment.Stretch});
        }
    }
    static string JobTime(JsonElement job, string field) =>
        DateTimeOffset.TryParse(LedgerLabels.Text(job, field, ""), out var time) ? time.ToLocalTime().ToString("MM-dd HH:mm") : "";
    static string JobDuration(JsonElement job)
    {
        if (!DateTimeOffset.TryParse(LedgerLabels.Text(job, "started_at", ""), out var start) ||
            !DateTimeOffset.TryParse(LedgerLabels.Text(job, "completed_at", ""), out var end) || end < start) return "";
        var span = end - start;
        return span.TotalMinutes >= 1 ? $"{(int)span.TotalMinutes} 分 {span.Seconds} 秒" : $"{span.TotalSeconds:0.#} 秒";
    }
    async Task RefreshMaintenanceAsync()
    {
        if (maintenanceRefreshing || backend == null) return;
        maintenanceRefreshing = true; nativeRefresh.IsEnabled = false;
        try { using var status = await backend.StatusAsync(); RenderNativeStatus(status.RootElement); }
        catch (Exception error) { nativeServiceStatus.Text = "连接暂时不可用"; nativeValidate.IsEnabled = false; NotifyNative(error.Message, true); }
        finally { maintenanceRefreshing = false; nativeRefresh.IsEnabled = true; }
    }
    async Task ValidateFromNativeAsync()
    {
        if (validationStarting || backend == null) return;
        validationStarting = true; nativeValidate.IsEnabled = false;
        try {
            using var job = await backend.ValidateAsync();
            NotifyNative("校验已开始，完成后会显示在最近任务中。");
            await RefreshMaintenanceAsync();
        } catch (Exception error) { NotifyNative(error.Message, true); }
        finally { validationStarting = false; }
    }
}
