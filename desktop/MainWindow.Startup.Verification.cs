using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    readonly List<double> startupFrameTimes = new();
    bool verifyingStartup;
    async Task VerifyStartupNativeAsync()
    {
        if (verifyingStartup || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_STARTUP") != "1") return;
        verifyingStartup=true;
        var checks=new List<string>();
        var originalSize=AppWindow.Size;
        var wasMaximized=AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter originalPresenter && originalPresenter.State==Microsoft.UI.Windowing.OverlappedPresenterState.Maximized;
        void Check(bool ok,string name) { if(!ok) throw new Exception(name); checks.Add(name); }
        async Task Until(Func<bool> condition) { for(var i=0;i<200&&!condition();i++) await Task.Delay(100); Check(condition(),"bounded_wait_"+checks.Count); }
        try {
            await Until(()=>startupFrameSeen);
            Check(starfieldBackground.Visibility==Visibility.Visible && layout.Children.IndexOf(starfieldLayer)>layout.Children.IndexOf(navigation) && browser.Visibility==Visibility.Visible,"first_frame_cover_keeps_webview_painting_underneath");
            await CaptureNativePageAsync("first-frame","startup-native");
            CaptureWindowPixels("startup-first-frame");
            await Until(()=>visualClock.Elapsed.TotalSeconds-startupAt>=2.3);
            await CaptureNativePageAsync("orbit","startup-native");
            await Until(()=>visualClock.Elapsed.TotalSeconds-startupAt>=4.7);
            await CaptureNativePageAsync("scatter","startup-native");
            await Until(()=>handoffStartedAt>0);
            CaptureWindowPixels("startup-handoff-begin");
            await Task.Delay(150);
            CaptureWindowPixels("startup-handoff-middle");
            await Until(()=>pageReady&&shellReady&&!startupActive);
            Check(webFrameReady && handoffStartedAt>=StarfieldTimeline.RevealAt && handoffCompletedAt-handoffStartedAt>=.3,"cover_releases_only_after_web_frame_and_animation");
            Check(layout.Children.IndexOf(starfieldLayer)<layout.Children.IndexOf(navigation) && navigation.IsHitTestVisible,"cover_returns_behind_content_after_fade");
            await VerifyStarGeometryAsync(checks);
            Check(!startupActive&&!renderingStartup,"startup_render_hook_is_released");
            Check(browser.DefaultBackgroundColor.A==255,"webview_uses_opaque_backing_for_reliable_composition");
            await VerifyOverviewPixelsAsync("initial",checks);
            await VerifyPaletteRegressionAsync(checks);
            Check(navigation.MenuItems.OfType<NavigationViewItem>().Count(item=>(item.Content as string)=="更新")==1 &&
                !navigation.MenuItems.OfType<NavigationViewItem>().Any(item=>item.Tag as string is "game" or "native-tasks"),"exactly_one_update_navigation");
            await NavigateAsync("operations");
            for(var i=0;i<40 && await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('#backendToggleLabel').textContent === '本地'")!="true";i++) await Task.Delay(100);
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('#backendToggleButton').closest('[data-panel=operations]') !== null && document.querySelector('#backendToggleLabel').textContent === '本地' && document.querySelector('#backendToggleButton').title.includes('暂停维护')")=="true","update_page_shows_local_run_location");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-panel=operations]').classList.contains('active') && !!window.expenseDesktopCosmosWallpaper")=="true","update_page_preserves_game_and_collection");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("matchMedia('(prefers-reduced-motion: reduce)').matches")=="false" &&
                MotionEnabled==!preferences.Appearance.ReducedMotion,"motion_follows_only_the_app_switch");
            await NavigateAsync("native-settings");
            Check(selectedCollectorMode=="local" && collectorLocalReady && collectorLocation.SelectedIndex==0,"native_settings_show_ready_local_collection");
            Check((collectorLocation.Items[1] as ComboBoxItem)?.IsEnabled==false,"vps_selection_is_paused");
            var location = await backend!.CollectorBackendsAsync();
            Check(LedgerLabels.Text(location,"resolved_backend","")=="local" && !location.GetProperty("backends").GetProperty("vps").GetProperty("selectable").GetBoolean(),"backend_resolves_local_without_vps");
            await CaptureNativePageAsync("local-settings","startup-native");
            await Until(()=>browser.CoreWebView2.IsSuspended);
            Check(!lastBackendBusy,"hidden_idle_webview_suspends");
            await NavigateAsync("overview");
            Check(!browser.CoreWebView2.IsSuspended&&browser.Visibility==Visibility.Visible,"webview_resumes_on_return");
            await VerifyOverviewPixelsAsync("returned",checks);
            ResumeWeb(); ShowWebError("验证网页故障","合成故障，不终止实际浏览器。");
            await NavigateAsync("orders");
            Check(listValid&&ordersPanel.Visibility==Visibility.Visible&&webErrorPanel.Visibility==Visibility.Collapsed,"native_orders_work_with_web_failure");
            await NavigateAsync("sources");
            Check(financeValid,"native_payments_work_with_web_failure");
            var originalBackend=backend;
            webNeedsRecreate=true;
            await StartWebAsync(); await Until(()=>pageReady);
            Check(ReferenceEquals(originalBackend,backend)&&backend!.Running,"webview_recreation_preserves_backend");
            await NavigateAsync("operations");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-panel=operations]').classList.contains('active')")=="true","update_survives_webview_recreation");
            await NavigateAsync("overview");
            await VerifyOverviewPixelsAsync("recreated",checks);
            AppWindow.Hide(); ApplyVisualMode(); await UpdateResourceModeAsync();
            await Until(()=>browser.CoreWebView2.IsSuspended);
            Check(visualTimer?.IsRunning!=true&&!renderingStartup&&monitor.Interval.TotalSeconds==20,"background_visuals_stop_and_polling_slows");
            ShowWindow(); await Until(()=>!browser.CoreWebView2.IsSuspended);
            Check(visualTimer?.IsRunning==true && browser.Visibility==Visibility.Visible,"foreground_stars_and_webview_resume");
            await VerifyOverviewPixelsAsync("restored",checks);
            for (var round=0;round<3;round++) {
                await NavigateAsync("orders"); await Task.Delay(500);
                await NavigateAsync("overview");
                await VerifyOverviewPixelsAsync("cycle-"+round,checks);
            }
            var gaps=startupFrameTimes.Zip(startupFrameTimes.Skip(1),(a,b)=>(b-a)*1000).Order().ToArray();
            File.WriteAllText(Path.Combine(root,"verification/startup-native-runtime.json"),JsonSerializer.Serialize(new{status="passed",checks,capturedWindowFrames,skippedWindowFrames,visual_capture_complete=skippedWindowFrames.Count==0,handoffStartedAt,handoffCompletedAt,usedFrameCaptureFallback,frames=gaps.Length,p95_frame_ms=gaps.Length>0?gaps[(int)(gaps.Length*.95)]:0,max_frame_ms=gaps.LastOrDefault(),at=DateTimeOffset.Now}));
        } catch(Exception error) {
            Log(error); File.WriteAllText(Path.Combine(root,"verification/startup-native-runtime.json"),JsonSerializer.Serialize(new{status="failed",checks,error=error.ToString(),at=DateTimeOffset.Now}));
        } finally {
            webFailed=false;
            AppWindow.Resize(originalSize); if(wasMaximized && AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter restored) restored.Maximize();
            AppWindow.Show(); ResumeWeb(); await NavigateAsync("overview"); ApplyVisualMode(); WriteState("ready");
        }
    }
}
