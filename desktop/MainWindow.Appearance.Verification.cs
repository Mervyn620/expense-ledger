using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingAppearance;
    async Task VerifyAppearanceAsync()
    {
        if (verifyingAppearance || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_APPEARANCE") != "1") return;
        verifyingAppearance = true;
        var checks = new List<string>();
        var originalTheme = layout.RequestedTheme; var originalSize = AppWindow.Size;
        var maximized = AppWindow.Presenter is OverlappedPresenter initial && initial.State == OverlappedPresenterState.Maximized;
        string? originalWeb = null;
        void Check(bool value, string name) { if (!value) throw new Exception(name); checks.Add(name); }
        async Task WaitFor(Func<bool> condition) {
            for (var i=0;i<100;i++) { if (condition()) return; await Task.Delay(100); }
            throw new Exception("Appearance did not settle.");
        }
        async Task Select(string kind, string value) {
            await browser.CoreWebView2.ExecuteScriptAsync($"document.querySelector('[data-{kind}-choice=\"{value}\"]').click()");
            await WaitFor(() => kind == "wallpaper" ? preferences.Appearance.Wallpaper == value : preferences.Appearance.Color == value);
            await Task.Delay(150);
        }
        try {
            for (var i=0;i<100;i++) {
                if (await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true && typeof window.LedgerAppearance?.current === 'function'") == "true") break;
                await Task.Delay(100);
            }
            originalWeb = await browser.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('expense-ledger-appearance-v1')");
            await SyncAppearanceFromWebAsync();
            Check(preferences.Appearance.Wallpaper == JsonSerializer.Deserialize<LedgerAppearance>(await browser.CoreWebView2.ExecuteScriptAsync("window.LedgerAppearance.current()"))!.Wallpaper, "initial_web_appearance_sync");
            await NavigateAsync("sources");
            Check(currentRoute == "sources" && financeValid && browser.Visibility == Visibility.Collapsed, "payment_remains_native");
            await Select("wallpaper","jade"); await Select("color","auto");
            await WaitFor(() => wallpaperImage.Source is BitmapImage bitmap && bitmap.PixelWidth > 0);
            Check(preferences.Appearance.EffectiveColor == "jade" && currentRoute == "sources", "hidden_web_theme_updates_without_navigation");
            Check(DesktopPreferences.Load(root).Appearance.Wallpaper == "jade", "appearance_cache_persisted");
            Check(ReferenceEquals(navigation.Resources["NavigationViewExpandedPaneBackground"],PaneGlass), "navigation_uses_shared_glass");
            Check(((SolidColorBrush)navigation.Resources["NavigationViewContentBackground"]).Color.A == 0, "navigation_content_preserves_wallpaper");
            layout.RequestedTheme = ElementTheme.Light; ApplyAppearanceVisuals();
            Check(nativeContent.RequestedTheme == ElementTheme.Light && navigation.RequestedTheme == ElementTheme.Dark, "light_content_with_dark_navigation");
            await Task.Delay(300); await CaptureNativePageAsync("payments-light-jade","native-theme");
            await Select("color","indigo");
            Check(preferences.Appearance.Wallpaper == "jade" && preferences.Appearance.EffectiveColor == "indigo", "independent_color_preserves_wallpaper");
            layout.RequestedTheme = ElementTheme.Dark; ApplyAppearanceVisuals();
            Check(nativeContent.RequestedTheme == ElementTheme.Dark, "dark_content_theme");
            await NavigateAsync("orders");
            Check(listValid && orderList.Items.Count > 0 && currentRoute == "orders", "order_list_remains_native");
            await Task.Delay(200); await CaptureNativePageAsync("orders-dark-indigo","native-theme");
            await NavigateAsync("cash");
            Check(financeValid && currentRoute == "cash" && cashBody.Visibility == Visibility.Visible, "cash_remains_native");
            await CaptureNativePageAsync("cash-dark-indigo","native-theme");
            await browser.CoreWebView2.ExecuteScriptAsync("(() => { const input=document.getElementById('solidSurfaces'); input.checked=true; input.dispatchEvent(new Event('change')); })()");
            await WaitFor(() => preferences.Appearance.Solid);
            Check(SurfaceGlass.AlwaysUseFallback && PaneGlass.AlwaysUseFallback, "solid_setting_reaches_all_surfaces");
            await Select("wallpaper","none");
            Check(wallpaperImage.Visibility == Visibility.Collapsed && wallpaperImage.Source == null, "plain_background_hides_image");
            await Select("wallpaper","cloud");
            await browser.CoreWebView2.ExecuteScriptAsync("(() => { const input=document.getElementById('solidSurfaces'); input.checked=false; input.dispatchEvent(new Event('change')); })()");
            await WaitFor(() => !preferences.Appearance.Solid);
            ApplyAppearanceVisuals(false,false);
            Check(SurfaceGlass.AlwaysUseFallback && PaneGlass.AlwaysUseFallback && wallpaperImage.Visibility == Visibility.Collapsed, "system_transparency_fallback");
            ApplyAppearanceVisuals(true,true);
            Check(SurfaceGlass.AlwaysUseFallback && wallpaperImage.Visibility == Visibility.Collapsed && navigation.RequestedTheme == ElementTheme.Default, "high_contrast_fallback");
            ApplyAppearanceVisuals(false,true);
            Check(!SurfaceGlass.AlwaysUseFallback && wallpaperImage.Visibility == Visibility.Visible, "glass_returns_after_fallback");
            await browser.CoreWebView2.ExecuteScriptAsync("(() => { const input=document.getElementById('reduceAppearanceMotion'); input.checked=true; input.dispatchEvent(new Event('change')); })()");
            await WaitFor(() => preferences.Appearance.ReducedMotion);
            Check(preferences.Appearance.ReducedMotion, "reduced_motion_preference_preserved");
            await NavigateAsync("native-maintenance");
            Check(maintenancePanel.Visibility == Visibility.Visible && nativePageSurface.Visibility == Visibility.Collapsed, "maintenance_uses_native_glass");
            layout.RequestedTheme = ElementTheme.Light; ApplyAppearanceVisuals();
            await Task.Delay(200); await CaptureNativePageAsync("maintenance-light-cloud","native-theme");
            if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Restore();
            var scale = WindowScale(); AppWindow.Resize(new SizeInt32((int)(760*scale),(int)(760*scale)));
            await NavigateAsync("sources"); await Task.Delay(250);
            Check(Grid.GetRow(sourceCategoriesBorder) == (sourcesBody.ActualWidth < 850 ? 2 : 1) && sourcesBody.ActualHeight > 150, "glass_preserves_narrow_finance_layout");
            await CaptureNativePageAsync("payments-narrow","native-theme");
            await NavigateAsync("overview");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("getComputedStyle(document.querySelector('.sidebar')).display === 'none'") == "true", "single_navigation_preserved");
            File.WriteAllText(Path.Combine(root,"verification","native-theme-runtime.json"),JsonSerializer.Serialize(new {status="passed",checks,at=DateTimeOffset.Now}));
        } catch(Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root,"verification","native-theme-runtime.json"),JsonSerializer.Serialize(new {status="failed",checks,error=error.ToString(),at=DateTimeOffset.Now}));
        } finally {
            if (originalWeb != null) {
                await browser.CoreWebView2.ExecuteScriptAsync($"(() => {{const original={originalWeb}; if(original===null)localStorage.removeItem('expense-ledger-appearance-v1'); else localStorage.setItem('expense-ledger-appearance-v1',original); window.dispatchEvent(new StorageEvent('storage',{{key:'expense-ledger-appearance-v1'}}));}})()");
                await SyncAppearanceFromWebAsync();
            }
            layout.RequestedTheme = originalTheme; ApplyAppearanceVisuals();
            AppWindow.Resize(originalSize); if (maximized && AppWindow.Presenter is OverlappedPresenter presenter) presenter.Maximize();
            await NavigateAsync("overview"); WriteState("ready");
        }
    }
}
