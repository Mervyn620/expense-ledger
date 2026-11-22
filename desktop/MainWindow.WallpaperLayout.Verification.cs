using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Windowing;
using Windows.Graphics;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingWallpaperLayout;
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint currentThread, uint foregroundThread, bool attach);
    void FocusLayoutVerificationWindow()
    {
        // Called only by the explicit opt-in foreground visual acceptance, never normal startup.
        var handle=WinRT.Interop.WindowNative.GetWindowHandle(this);
        var current=GetCurrentThreadId();var foreground=GetWindowThreadProcessId(GetForegroundWindow(),out _);
        var attached=foreground!=0 && foreground!=current && AttachThreadInput(current,foreground,true);
        try { ShowWindow();SetForegroundWindow(handle); }
        finally { if(attached)AttachThreadInput(current,foreground,false); }
    }
    async Task VerifyWallpaperLayoutAsync()
    {
        if(verifyingWallpaperLayout || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_LAYOUT")!="1") return;
        verifyingWallpaperLayout=true;
        var checks=new List<string>(); var samples=new List<object>();
        var originalAppearance=preferences.Appearance.Normalize(); var originalMode=preferences.VisualMode;
        var originalSize=AppWindow.Size; var originalPosition=AppWindow.Position;

        string? originalWeb=null; string? failure=null;
        var captureStart=capturedWindowFrames.Count; var skipStart=skippedWindowFrames.Count;
        void Check(bool value,string name) { if(!value) throw new Exception(name);checks.Add(name); }
        async Task WaitWeb()
        {
            ResumeWeb();
            for(var i=0;i<100;i++) {
                if(pageReady && browser.Source?.AbsolutePath=="/" && await browser.CoreWebView2.ExecuteScriptAsync("!!window.expenseDesktopLayout && !!document.getElementById('desktopDistributionTab0')")=="true") return;
                await Task.Delay(100);
            }
            throw new Exception("layout_page_not_ready");
        }
        async Task<JsonElement> Probe()
        {
            await SyncStarfieldFrameAsync(); await Task.Delay(160);
            var json=await browser.CoreWebView2.ExecuteScriptAsync("(() => {const r=document.documentElement,p=getComputedStyle(document.body,'::before'),rect=e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,bottom:b.bottom};};return {layout:r.dataset.desktopLayout,aligned:r.dataset.desktopWallpaperFrame,viewport:{width:innerWidth,height:innerHeight},overflow:document.documentElement.scrollWidth-innerWidth,background:{width:parseFloat(p.width),height:parseFloat(p.height),left:parseFloat(p.left),top:parseFloat(p.top)},header:rect(document.querySelector('.topbar')),metrics:[...document.querySelectorAll('.metric-card')].map(rect),gap:rect(document.getElementById('desktopScenicGap')),chart:rect(document.getElementById('trendChart')),selected:document.querySelector('[role=tab][aria-selected=true]')?.textContent,panels:[...document.querySelectorAll('.desktop-distribution-panel')].map(e=>({hidden:e.hidden,width:e.getBoundingClientRect().width})),tabs:[...document.querySelectorAll('[role=tab]')].map(e=>({id:e.id,tabIndex:e.tabIndex,selected:e.getAttribute('aria-selected')})),recent:document.querySelector('#recentOrders')?.children.length};})()");
            using var doc=JsonDocument.Parse(json);return doc.RootElement.Clone();
        }
        void CheckFrame(JsonElement probe,string name)
        {
            var scale=probe.GetProperty("viewport").GetProperty("width").GetDouble()/browser.ActualWidth;
            var origin=browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0));
            var bg=probe.GetProperty("background");
            Check(Math.Abs(bg.GetProperty("width").GetDouble()-layout.ActualWidth*scale)<1 && Math.Abs(bg.GetProperty("height").GetDouble()-layout.ActualHeight*scale)<1
                && Math.Abs(bg.GetProperty("left").GetDouble()+origin.X*scale)<1 && Math.Abs(bg.GetProperty("top").GetDouble()+origin.Y*scale)<1,"same_native_wallpaper_crop_"+name);
            Check(probe.GetProperty("overflow").GetDouble()<=1,"no_horizontal_overflow_"+name);
        }
        async Task Capture(string name)
        {
            var frameName="wallpaper-layout-0.12.9-"+name;
            var previousCaptures=capturedWindowFrames.Count;
            for(var attempt=0;attempt<6;attempt++) {
                FocusLayoutVerificationWindow();await Task.Delay(attempt==0?750:500);
                CaptureWindowPixels(frameName);
                if(capturedWindowFrames.Count>previousCaptures)return;
                skippedWindowFrames.Remove(frameName);
                if(attempt==1) { AppWindow.Hide();await Task.Delay(100);ShowWindow(); }
            }
            skippedWindowFrames.Add(frameName);
        }
        async Task Choose(string name)
        {
            await NavigateAsync("overview");await WaitWeb();
            await browser.CoreWebView2.ExecuteScriptAsync($"document.querySelector('[data-wallpaper-choice={name}]').click();document.querySelector('[data-color-choice=auto]').click()");
            await SyncAppearanceFromWebAsync();await SyncVisualModeToWebAsync();await Task.Delay(300);
        }
        bool HasCard(DependencyObject node)
        {
            if(node is Border card && ReferenceEquals(card.Background,SurfaceGlass))return true;
            for(var i=0;i<VisualTreeHelper.GetChildrenCount(node);i++)if(HasCard(VisualTreeHelper.GetChild(node,i)))return true;
            return false;
        }
        try {
            ShowWindow();await WaitWeb();
            originalWeb=await browser.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('expense-ledger-appearance-v1')");
            preferences.VisualMode="static";ApplyVisualMode();ApplyAppearanceVisuals();await SyncVisualModeToWebAsync();
            var area=DisplayArea.GetFromWindowId(AppWindow.Id,DisplayAreaFallback.Primary).WorkArea;
            AppWindow.Move(new PointInt32(area.X,area.Y));
            navigation.IsPaneOpen=false;await Task.Delay(350);UpdateCompactNavigation();
            Check(navigation.CompactPaneLength==88 && navigation.PaneDisplayMode==NavigationViewPaneDisplayMode.LeftCompact,"compact_88_dip_native_rail");
            foreach(var item in navigation.MenuItems.Concat(navigation.FooterMenuItems).OfType<NavigationViewItem>()) {
                Check(FindGlassElement(item,"DesktopCompactCaption") is TextBlock {Visibility:Visibility.Visible},"compact_visible_caption_"+item.Tag);
                item.UpdateLayout();
                Check(item.ActualHeight>=44,"native_navigation_hit_target_"+item.Tag);
                Check(FindGlassElement(item,"ContentPresenter") is FrameworkElement {Visibility:Visibility.Collapsed},"no_duplicate_compact_label_"+item.Tag);
                samples.Add(new{navigation=item.Tag,height=item.ActualHeight,width=item.ActualWidth});
            }
            navigation.UpdateLayout();
            var footerViewport=(ScrollViewer)FindGlassElement(navigation,"FooterItemsScrollViewer")!;
            foreach(var item in navigation.FooterMenuItems.OfType<NavigationViewItem>()) {
                var location=item.TransformToVisual(footerViewport).TransformPoint(new Windows.Foundation.Point(0,0));
                Check(location.Y>=-1 && location.Y+item.ActualHeight<=footerViewport.ActualHeight+1,"footer_navigation_fully_visible_"+item.Tag);
            }
            foreach(var wallpaper in new[]{"cloud","jade","ink","indigo"}) {
                await Choose(wallpaper);var probe=await Probe();CheckFrame(probe,wallpaper);
                Check(probe.GetProperty("layout").GetString()=="wallpaper" && probe.GetProperty("aligned").GetString()=="aligned","new_native_layout_enabled_"+wallpaper);
                var metrics=probe.GetProperty("metrics").EnumerateArray().ToArray();
                Check(metrics.Length==4 && metrics.All(m=>Math.Abs(m.GetProperty("y").GetDouble()-metrics[0].GetProperty("y").GetDouble())<1),"four_metrics_in_one_row_"+wallpaper);
                Check(probe.GetProperty("header").GetProperty("x").GetDouble()>=20 && probe.GetProperty("header").GetProperty("y").GetDouble()>=16,"floating_header_has_wallpaper_gutters_"+wallpaper);
                Check(probe.GetProperty("gap").GetProperty("height").GetDouble()>=64,"continuous_scenic_gap_"+wallpaper);
                Check(probe.GetProperty("chart").GetProperty("height").GetDouble()<308,"compact_trend_keeps_chart_available_"+wallpaper);
                Check(probe.GetProperty("panels").EnumerateArray().Count(p=>!p.GetProperty("hidden").GetBoolean())==1,"one_statistics_panel_visible_"+wallpaper);
                samples.Add(new{wallpaper,probe});await Capture(wallpaper+"-overview");
                var origin=browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0));
                navigation.IsPaneOpen=true;await Task.Delay(400);var open=await Probe();CheckFrame(open,wallpaper+"-open");
                var openOrigin=browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0));
                Check(Math.Abs(openOrigin.X-origin.X)<.1,"expanded_navigation_overlays_without_recrop_"+wallpaper);
                if(wallpaper=="cloud")await Capture("expanded-navigation");
                navigation.IsPaneOpen=false;await Task.Delay(350);
                await NavigateAsync("native-settings");await Task.Delay(250);
                Check(nativePageSurface.Visibility==Visibility.Collapsed && nativePageReflection.Visibility==Visibility.Collapsed && HasCard(settingsPanel),"settings_only_actual_cards_use_glass_"+wallpaper);
                await Capture(wallpaper+"-settings");
                await NavigateAsync("orders");for(var i=0;i<80&&!listValid;i++)await Task.Delay(100);
                Check(ordersPanel.Visibility==Visibility.Visible && nativePageSurface.Visibility==Visibility.Collapsed && HasCard(ordersPanel),"native_orders_retain_readable_cards_"+wallpaper);
                await Capture(wallpaper+"-orders");
            }
            await NavigateAsync("orders");await LoadOrdersAsync();
            var originalFilter=orderFilter;var firstPage=orderPage!;
            var listScroll=FindScrollViewer(orderList)!;
            Check(firstPage.Rows.Count>0 && listScroll.ScrollableHeight>0,"long_native_order_list_scrolls");
            listScroll.ChangeView(null,listScroll.ScrollableHeight,null,true);await Task.Delay(250);
            Check(listScroll.VerticalOffset>0,"native_order_list_scroll_reaches_later_rows");
            await Capture("orders-scrolled");
            if(firstPage.TotalCount>OrderFilter.PageSize) {
                orderFilter=orderFilter with {Offset=OrderFilter.PageSize};await LoadOrdersAsync();
                Check(orderPage!.Rows.Count>0 && ordersPrevious.IsEnabled && orderPage.Rows.All(row=>firstPage.Rows.All(first=>row.OrderPk!=first.OrderPk)),"native_order_pagination_loads_distinct_rows");
            }
            await OpenOrderDetailAsync(orderPage!.Rows[0].OrderPk);await Task.Delay(250);
            Check(orderFullRecord.IsEnabled && orderDetailPanel.Visibility==Visibility.Visible && HasCard(orderDetailContent),"native_order_detail_remains_readable_and_available");
            await Capture("order-detail");
            orderFilter=originalFilter;await LoadOrdersAsync();listScroll.ChangeView(null,0,null,true);
            await NavigateAsync("overview");await WaitWeb();
            await browser.CoreWebView2.ExecuteScriptAsync("document.getElementById('desktopDistributionTab0').focus();document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.activeElement.id==='desktopDistributionTab1' && document.activeElement.getAttribute('aria-selected')==='true' && document.getElementById('desktopDistributionTab0').tabIndex===-1")=="true","statistics_tabs_arrow_key_focus_and_selection");
            await browser.CoreWebView2.ExecuteScriptAsync("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}))");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.activeElement.id==='desktopDistributionTab2' && !document.getElementById('desktopDistributionPanel2').hidden && document.getElementById('desktopCategoryBreakdown').children.length>0")=="true","category_tab_end_key_and_current_data");
            await Capture("category-statistics");
            await browser.CoreWebView2.ExecuteScriptAsync("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}))");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.activeElement.id==='desktopDistributionTab0'")=="true","statistics_tabs_home_key");
            await browser.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setDeviceMetricsOverride",JsonSerializer.Serialize(new{width=(int)(browser.ActualWidth/2),height=(int)(browser.ActualHeight/2),deviceScaleFactor=0,mobile=false,scale=2,dontSetVisibleSize=true}));await Task.Delay(450);var zoom=await Probe();CheckFrame(zoom,"scale-200-emulation");
            Check(Math.Abs(zoom.GetProperty("viewport").GetProperty("width").GetDouble()*2-browser.ActualWidth)<2,"double_scale_emulated_css_viewport");
            samples.Add(new{mode="scale-200-emulation",probe=zoom});await Capture("scale-200-emulation");
            await browser.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.clearDeviceMetricsOverride","{}");
            var dpi=layout.XamlRoot.RasterizationScale;var chrome=AppWindow.Size.Height-layout.ActualHeight*dpi;
            AppWindow.Resize(new SizeInt32((int)(700*dpi),(int)(800*dpi+chrome)));await Task.Delay(500);
            var narrow=await Probe();CheckFrame(narrow,"narrow");
            var smallMetrics=narrow.GetProperty("metrics").EnumerateArray().ToArray();
            Check(smallMetrics[2].GetProperty("y").GetDouble()>smallMetrics[0].GetProperty("y").GetDouble(),"narrow_metrics_use_two_rows");
            samples.Add(new{mode="narrow",probe=narrow});await Capture("narrow-overview");
            await NavigateAsync("native-settings");await Task.Delay(250);Check(settingsContent.ActualWidth<=settingsPanel.ActualWidth,"narrow_settings_fit_window");await Capture("narrow-settings");
            AppWindow.Resize(new SizeInt32((int)(540*dpi),(int)(800*dpi+chrome)));await Task.Delay(400);
            Check(navigation.PaneDisplayMode==NavigationViewPaneDisplayMode.LeftMinimal,"very_narrow_window_uses_overlay_navigation");
            await NavigateAsync("overview");await WaitWeb();var minimal=await Probe();CheckFrame(minimal,"very-narrow");
            var closedOrigin=browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0));
            navigation.IsPaneOpen=true;await Task.Delay(250);
            Check(Math.Abs(browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0)).X-closedOrigin.X)<.1,"very_narrow_open_navigation_does_not_move_wallpaper");
            Check(navigation.MenuItems.OfType<NavigationViewItem>().First().Focus(FocusState.Keyboard),"native_navigation_accepts_keyboard_focus");
            await Capture("very-narrow-navigation");navigation.IsPaneOpen=false;
            AppWindow.Resize(originalSize);await Task.Delay(400);
            foreach(var route in new[]{"cash","sources","native-maintenance"}) {
                await NavigateAsync(route);await Task.Delay(350);
                Check(nativePageSurface.Visibility==Visibility.Collapsed,"no_full_page_blur_"+route);
                await Capture(route);
            }
            ApplyAppearanceVisuals(true,false);Check(SurfaceGlass.AlwaysUseFallback && PaneGlass.AlwaysUseFallback,"opaque_accessibility_fallback_preserved");
            ApplyAppearanceVisuals(false,true);
            await NavigateAsync("overview");await WaitWeb();preferences.VisualMode="particle";ApplyVisualMode();ApplyAppearanceVisuals();await SyncVisualModeToWebAsync();
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.documentElement.dataset.desktopVisualMode==='particle' && document.querySelector('#expense-desktop-stars').width>0")=="true","particle_background_still_available");
            await Capture("particle-overview");
            await NavigateAsync("operations");await Task.Delay(250);
            Check(currentRoute=="operations" && preferences.GameModeEnabled==gameMode.IsOn,"update_and_game_preference_preserved");
        } catch(Exception error) { failure=error.ToString();Log(error); }
        finally {
            try {
            await browser.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.clearDeviceMetricsOverride","{}");AppWindow.Resize(originalSize);AppWindow.Move(originalPosition);
            await NavigateAsync("overview");await WaitWeb();
            if(originalWeb!=null)await browser.CoreWebView2.ExecuteScriptAsync($"(() => {{const v={originalWeb};if(v===null)localStorage.removeItem('expense-ledger-appearance-v1');else localStorage.setItem('expense-ledger-appearance-v1',v);window.dispatchEvent(new StorageEvent('storage',{{key:'expense-ledger-appearance-v1'}}));}})()");
            preferences.Appearance=originalAppearance;preferences.VisualMode=originalMode;
            staticVisualMode.IsChecked=originalMode=="static";particleVisualMode.IsChecked=originalMode=="particle";
            ApplyNativeTheme();ApplyVisualMode();await SyncVisualModeToWebAsync();preferences.Save(root);navigation.IsPaneOpen=false;
            WriteState("ready");
            } catch(Exception restoreError) { failure ??= restoreError.ToString(); Log(restoreError); }
            File.WriteAllText(Path.Combine(root,"verification","wallpaper-layout-0.12.9-runtime.json"),JsonSerializer.Serialize(new{status=failure==null?"passed":"failed",checks,samples,error=failure,captured=capturedWindowFrames.Skip(captureStart).ToArray(),skipped=skippedWindowFrames.Skip(skipStart).ToArray()},new JsonSerializerOptions{WriteIndented=true}));
        }
    }
}
