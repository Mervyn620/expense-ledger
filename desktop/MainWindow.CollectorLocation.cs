using System;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml.Controls;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool syncingCollectorLocation, readingCollectorLocation, changingCollectorLocation, collectorLocalReady;
    string selectedCollectorMode = "";
    void InitializeCollectorLocation()
    {
        collectorLocation.SelectionChanged += async (_, _) => {
            if (syncingCollectorLocation || changingCollectorLocation || readingCollectorLocation || lastBackendBusy) return;
            if ((collectorLocation.SelectedItem as ComboBoxItem)?.Tag as string == "local" && selectedCollectorMode != "local")
                await UseLocalCollectorAsync();
        };
        collectorLocationRefresh.Click += async (_, _) => await RefreshCollectorLocationAsync();
        UpdateCollectorLocationControls();
    }
    void UpdateCollectorLocationControls()
    {
        collectorLocation.IsEnabled = collectorLocalReady && !readingCollectorLocation && !changingCollectorLocation && !lastBackendBusy;
        collectorLocationRefresh.IsEnabled = backendReady && !readingCollectorLocation && !changingCollectorLocation;
    }
    void RenderCollectorLocation(JsonElement status)
    {
        selectedCollectorMode = LedgerLabels.Text(status,"selected_mode","");
        var resolved = LedgerLabels.Text(status,"resolved_backend","");
        var local = status.GetProperty("backends").GetProperty("local");
        collectorLocalReady = local.GetProperty("selectable").GetBoolean();
        syncingCollectorLocation = true;
        try { collectorLocation.SelectedIndex = selectedCollectorMode == "local" ? 0 : selectedCollectorMode == "vps" ? 1 : resolved == "local" ? 0 : -1; }
        finally { syncingCollectorLocation = false; }
        collectorLocationStatus.Text = resolved == "local"
            ? collectorLocalReady ? "本地采集已就绪" : LedgerLabels.Text(local,"message","本地环境未就绪")
            : "当前运行环境未就绪 · " + LedgerLabels.Text(status,"message","请选择本地运行");
        UpdateCollectorLocationControls();
    }
    async Task RefreshCollectorLocationAsync()
    {
        if (readingCollectorLocation || changingCollectorLocation || backend == null || !backendReady || exiting) return;
        readingCollectorLocation = true; UpdateCollectorLocationControls();
        try { RenderCollectorLocation(await backend.CollectorBackendsAsync()); }
        catch (Exception error) {
            Log(error); collectorLocalReady = false;
            collectorLocationStatus.Text = "暂时无法读取运行位置，请刷新重试。";
        } finally { readingCollectorLocation = false; UpdateCollectorLocationControls(); }
    }
    async Task UseLocalCollectorAsync()
    {
        if (changingCollectorLocation || backend == null || !backendReady || exiting) return;
        changingCollectorLocation = true; UpdateCollectorLocationControls();
        collectorLocationStatus.Text = "正在切换到本地运行…";
        string? failure = null;
        try {
            RenderCollectorLocation(await backend.UseLocalCollectorAsync());
            if (pageReady && browser.CoreWebView2 != null) {
                ResumeWeb();
                await browser.CoreWebView2.ExecuteScriptAsync("typeof loadCollectorBackends === 'function' && loadCollectorBackends()");
            }
        } catch (Exception error) { Log(error); failure = error.Message; }
        finally { changingCollectorLocation = false; UpdateCollectorLocationControls(); }
        if (failure != null) {
            // Read back after a timeout/conflict so the displayed location remains truthful.
            await RefreshCollectorLocationAsync();
            NotifyNative(failure,true);
        }
    }
}
