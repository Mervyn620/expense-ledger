using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml;
using Microsoft.UI.Windowing;
using Windows.Graphics;
using Windows.Graphics.Imaging;
using Windows.Storage;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingPhaseTwo;
    async Task VerifyPhaseTwoAsync()
    {
        if (verifyingPhaseTwo || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_PHASE2") != "1") return;
        verifyingPhaseTwo = true;
        var results = new List<string>();
        var originalNotifications = completionNotifications.IsOn;
        var originalSize = AppWindow.Size;
        var wasMaximized = AppWindow.Presenter is OverlappedPresenter initial && initial.State == OverlappedPresenterState.Maximized;
        try {
            for (int attempt = 0; attempt < 40; attempt++) {
                if (await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true") == "true") break;
                await Task.Delay(200);
            }
            if (await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true") != "true") throw new Exception("Ledger page did not initialize.");
            await NavigateAsync("operations");
            if (await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('.view-panel.active').dataset.panel") != "\"operations\"") throw new Exception("Native-to-web route failed.");
            results.Add("native_to_operations_passed");
            await browser.CoreWebView2.ExecuteScriptAsync("switchView('overview')");
            await Task.Delay(150);
            if (currentRoute != "overview") throw new Exception("Web-to-native route failed.");
            results.Add("web_to_native_passed");
            await NavigateAsync("native-settings");
            completionNotifications.IsOn = false;
            var persisted = DesktopPreferences.Load(root);
            if (persisted.Theme != "system" || persisted.CompletionNotifications) throw new Exception("Preferences were not persisted.");
            await Task.Delay(250);
            await CaptureNativePageAsync("settings-dark");
            results.Add("settings_persistence_passed");
            await Task.Delay(150);
            await CaptureNativePageAsync("settings-light");
            await NavigateAsync("native-maintenance");
            if (!nativeServiceStatus.Text.StartsWith("已连接")) throw new Exception("Native maintenance could not reach the backend.");
            await Task.Delay(150);
            await CaptureNativePageAsync("maintenance");
            results.Add("maintenance_api_passed");
            var scale = WindowScale();
            if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Restore();
            await Task.Delay(100);
            AppWindow.Resize(new SizeInt32((int)(760 * scale), (int)(700 * scale)));
            await Task.Delay(350);
            if (layout.ActualWidth > 850) throw new Exception("The verification window did not become narrow.");
            await CaptureNativePageAsync("maintenance-narrow");
            results.Add("narrow_window_rendered");
            using (var status = await backend!.StatusAsync()) {
                if (!status.RootElement.GetProperty("busy").GetBoolean()) {
                    await ValidateFromNativeAsync();
                    results.Add("native_validation_action_invoked");
                }
            }
            File.WriteAllText(Path.Combine(root,"verification","phase-two-runtime.json"), JsonSerializer.Serialize(new {status="passed", checks=results}));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root,"verification","phase-two-runtime.json"), JsonSerializer.Serialize(new {status="failed", checks=results, error=error.Message}));
        } finally {
            AppWindow.Resize(originalSize);
            if (wasMaximized && AppWindow.Presenter is OverlappedPresenter presenter) presenter.Maximize();
            completionNotifications.IsOn = originalNotifications;
            await NavigateAsync("overview");
        }
    }
    async Task CaptureNativePageAsync(string name, string prefix = "phase-two", FrameworkElement? target = null)
    {
        layout.UpdateLayout();
        var bitmap = new RenderTargetBitmap();
        await bitmap.RenderAsync(target ?? layout);
        var pixels = await bitmap.GetPixelsAsync();
        var folder = await StorageFolder.GetFolderFromPathAsync(Path.Combine(root,"verification"));
        var file = await folder.CreateFileAsync(prefix+"-"+name+".png", CreationCollisionOption.ReplaceExisting);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied,
            (uint)bitmap.PixelWidth, (uint)bitmap.PixelHeight, 96, 96, pixels.ToArray());
        await encoder.FlushAsync();
    }
}
