using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Windowing;
using Windows.Graphics;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingGlass;
    async Task VerifyGlassAsync()
    {
        if (verifyingGlass || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_GLASS") != "1") return;
        verifyingGlass = true;
        var checks = new List<string>();
        var profiles = new List<object>();
        var originalMode = preferences.VisualMode;
        var originalAppearance = preferences.Appearance.Normalize();
        var originalGame = preferences.GameModeEnabled;
        var originalPosition = AppWindow.Position;
        string? originalWeb = null;
        void Check(bool value,string name) { if (!value) throw new Exception(name); checks.Add(name); }
        async Task<JsonElement> ProbeWeb()
        {
            ResumeWeb();
            var json = await browser.CoreWebView2.ExecuteScriptAsync("(() => { const r=document.documentElement, c=document.querySelector('.metric-card'),h=document.querySelector('.topbar'); const s=getComputedStyle(r), cs=getComputedStyle(c),hs=getComputedStyle(h); return {mode:r.dataset.desktopVisualMode,tone:r.dataset.desktopTone,theme:r.dataset.theme,glass:r.dataset.desktopGlass,alpha:Number(s.getPropertyValue('--glass-secondary')),font:getComputedStyle(document.body).fontWeight,text:cs.color,secondary:s.getPropertyValue('--text-soft').trim(),accent:s.getPropertyValue('--accent').trim(),fill:s.getPropertyValue('--glass-rgb').trim(),reflection:cs.backgroundImage,header:hs.backgroundImage,headerText:hs.color}; })()");
            return JsonSerializer.Deserialize<JsonElement>(json);
        }
        async Task Mode(string value)
        {
            if (value == "particle") particleVisualMode.IsChecked = true;
            else staticVisualMode.IsChecked = true;
            await SyncVisualModeToWebAsync();
            await SyncGlassToneToWebAsync();
        }
        async Task Choose(string kind,string value)
        {
            ResumeWeb();
            for(var i=0;i<100 && (!pageReady || browser.Source?.AbsolutePath!="/");i++) await Task.Delay(100);
            Check(pageReady && browser.Source?.AbsolutePath=="/","appearance_page_ready_"+kind+"_"+value+"_"+checks.Count);
            await browser.CoreWebView2.ExecuteScriptAsync($"document.querySelector('[data-{kind}-choice={value}]').click()");
            await SyncAppearanceFromWebAsync();
            await SyncGlassToneToWebAsync();
            await Task.Delay(200);
        }
        async Task Capture(string name)
        {
            ShowWindow(); await Task.Delay(350);
            CaptureWindowPixels(name);
        }
        try {
            ShowWindow();
            var workArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
            AppWindow.Move(new PointInt32(Math.Clamp(originalPosition.X,workArea.X,workArea.X+Math.Max(0,workArea.Width-AppWindow.Size.Width)), Math.Clamp(originalPosition.Y,workArea.Y,workArea.Y+Math.Max(0,workArea.Height-AppWindow.Size.Height))));
            for (var i=0;i<100;i++) {
                if (await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true && typeof window.LedgerAppearance?.current === 'function'") == "true") break;
                await Task.Delay(100);
            }
            originalWeb = await browser.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('expense-ledger-appearance-v1')");
            var initialAppearance=JsonSerializer.Deserialize<LedgerAppearance>(await browser.CoreWebView2.ExecuteScriptAsync("window.LedgerAppearance.current()"))!.Normalize();
            Check(JsonSerializer.Serialize(initialAppearance)==JsonSerializer.Serialize(originalAppearance),"initial_web_appearance_matches_native_cache");
            Check(FindGlassElement(settingsPanel,"nativeTheme")==null && preferences.Theme=="system","brightness_selector_removed_and_old_preference_migrated");
            var pane = FindGlassElement(navigation,"RootSplitView") as SplitView;
            Check(pane != null && ReferenceEquals(pane.PaneBackground,PaneGlass),"template_pane_uses_shared_acrylic");
            Check(FindGlassElement(navigation,"PaneContentGrid") is Grid paneGrid && ReferenceEquals(paneGrid.Background,layout.Resources["LedgerPaneReflectionBrush"]),"pane_reflection_is_in_actual_template");
            await NavigateAsync("overview");
            await Mode("static");
            await Choose("color","auto");
            var fills=new HashSet<string>();
            foreach (var wallpaper in new[]{"cloud","jade","ink","indigo"}) {
                await Choose("wallpaper",wallpaper);
                var web=await ProbeWeb();
                var expected = ElementTheme.Dark; // 0.17: all four palettes are dark glass
                Check(layout.ActualTheme==expected && nativeContent.RequestedTheme==expected && navigation.RequestedTheme==ElementTheme.Dark && web.GetProperty("tone").GetString()==preferences.UiTone,"automatic_matching_tone_"+wallpaper);
                Check(web.GetProperty("mode").GetString()=="static" && web.GetProperty("theme").GetString()==wallpaper && wallpaperImage.Visibility==Visibility.Visible,"static_wallpaper_and_palette_"+wallpaper);
                var staticFill=web.GetProperty("fill").GetString();
                Check(SurfaceGlass.TintColor!=PaneGlass.TintColor && web.GetProperty("header").GetString()!.Contains("linear-gradient"),"independent_pane_content_and_gradient_header_"+wallpaper);
                var accent=((SolidColorBrush)layout.Resources["LedgerAccentBrush"]).Color;
                Check(web.GetProperty("accent").GetString()==$"#{accent.R:x2}{accent.G:x2}{accent.B:x2}","web_and_native_share_static_accent_"+wallpaper);
                fills.Add(staticFill!);
                Check(web.GetProperty("alpha").GetDouble()<.85 && int.Parse(web.GetProperty("font").GetString()!)>=500 && web.GetProperty("reflection").GetString()!.Contains("gradient"),"static_readable_colored_glass_"+wallpaper);
                profiles.Add(new{wallpaper,mode="static",tone=preferences.UiTone,web,nativeTint=SurfaceGlass.TintColor.ToString(),nativeText=((SolidColorBrush)layout.Resources["LedgerTextBrush"]).Color.ToString(),nativeSecondary=((SolidColorBrush)layout.Resources["LedgerSecondaryTextBrush"]).Color.ToString(),paneTint=PaneGlass.TintColor.ToString(),paneText=((SolidColorBrush)layout.Resources["LedgerPaneTextBrush"]).Color.ToString(),paneSecondary=((SolidColorBrush)layout.Resources["LedgerPaneSecondaryBrush"]).Color.ToString()});
                await Capture("static-"+wallpaper+"-overview");
                await NavigateAsync("native-settings"); await Task.Delay(250);
                Check(nativePageSurface.Visibility==Visibility.Collapsed && nativePageReflection.Visibility==Visibility.Collapsed && nativeContent.RequestedTheme==expected,"static_native_settings_match_"+wallpaper);
                await Capture("static-"+wallpaper+"-settings");
                if (wallpaper is "cloud" or "jade") {
                    Check(staticVisualMode.Content as string == "原版静态" && particleVisualMode.Content as string == "星空粒子" && FindGlassElement(settingsPanel,"wallpaperSettingsHint")==null,"settings_copy_is_concise_"+wallpaper);
                    foreach(var helpButton in new[]{collectorInfo,notificationInfo,staticModeInfo,particleModeInfo,gameModeInfo,localLedgerInfo}) {
                        Check(helpButton.IsTabStop && helpButton.MinWidth>=44 && helpButton.MinHeight>=44 && !string.IsNullOrWhiteSpace(AutomationProperties.GetName(helpButton)) && helpButton.Flyout is Flyout,"accessible_clickable_help_"+wallpaper+"_"+helpButton.Name);
                    }
                    foreach(var helpButton in new[]{collectorInfo,notificationInfo,staticModeInfo,particleModeInfo,gameModeInfo,localLedgerInfo}) {
                    helpButton.StartBringIntoView(); await Task.Delay(250);
                    var help=helpButton.Flyout;
                    var opened=false; var closed=false;
                    void HelpOpened(object? sender,object args) => opened=true;
                    void HelpClosed(object? sender,object args) => closed=true;
                    help.Opened+=HelpOpened; help.Closed+=HelpClosed;
                    try {
                        ((IInvokeProvider)new ButtonAutomationPeer(helpButton).GetPattern(PatternInterface.Invoke)).Invoke(); await Task.Delay(250);
                        Check(opened,"native_help_flyout_opens_"+wallpaper+"_"+helpButton.Name);
                        if(helpButton==staticModeInfo) await Capture("static-"+wallpaper+"-help");
                        help.Hide(); await Task.Delay(200);
                        Check(closed,"native_help_flyout_dismisses_"+wallpaper+"_"+helpButton.Name);
                    } finally { help.Opened-=HelpOpened; help.Closed-=HelpClosed; help.Hide(); }
                    }
                    settingsPanel.ChangeView(null,0,null,true);
                }

                await NavigateAsync("overview");
                web=await ProbeWeb();
                Check(web.GetProperty("theme").GetString()==wallpaper && web.GetProperty("tone").GetString()==preferences.UiTone,"static_palette_survives_page_return_"+wallpaper);
                await Mode("particle"); web=await ProbeWeb();
                Check(layout.ActualTheme==ElementTheme.Dark && web.GetProperty("tone").GetString()=="dark" && web.GetProperty("alpha").GetDouble()<.6 && wallpaperImage.Visibility==Visibility.Collapsed,"particle_material_preserved_"+wallpaper);
                await Mode("static"); web=await ProbeWeb();
                Check(preferences.Appearance.Wallpaper==wallpaper && web.GetProperty("fill").GetString()==staticFill && web.GetProperty("tone").GetString()==preferences.UiTone,"static_material_restored_after_particle_"+wallpaper);
            }
            Check(fills.Count==4,"four_distinct_static_materials");
            await Choose("wallpaper","cloud"); await Mode("particle");
            var star=await ProbeWeb();
            Check(SurfaceGlass.TintOpacity<.5 && PaneGlass.TintOpacity<.5 && star.GetProperty("fill").GetString()=="26 49 77","existing_cloud_starfield_material_unchanged");
            profiles.Add(new{wallpaper="cloud",mode="particle",tone=preferences.UiTone,web=star});
            await Capture("static-particle-overview");
            foreach (var route in new[]{"orders","cash","sources","native-settings","native-maintenance"}) {
                await NavigateAsync(route); await Task.Delay(200);
                Check(nativePageSurface.Visibility==Visibility.Collapsed && nativePageReflection.Visibility==Visibility.Collapsed && nativeContent.RequestedTheme==navigation.RequestedTheme,"shared_native_material_"+route);
                await Capture("static-particle-"+route);
            }
            await NavigateAsync("overview"); await Mode("static");
            await browser.CoreWebView2.ExecuteScriptAsync("(() => {const input=document.getElementById('solidSurfaces');input.checked=true;input.dispatchEvent(new Event('change'));})()");
            await SyncAppearanceFromWebAsync(); var probe=await ProbeWeb();
            Check(SurfaceGlass.AlwaysUseFallback && PaneGlass.AlwaysUseFallback && probe.GetProperty("alpha").GetDouble()==1,"static_opaque_option_preserved");
            await browser.CoreWebView2.ExecuteScriptAsync("(() => {const input=document.getElementById('solidSurfaces');input.checked=false;input.dispatchEvent(new Event('change'));})()");
            await SyncAppearanceFromWebAsync();
            ApplyAppearanceVisuals(false,false); await SyncGlassToneToWebAsync(); probe=await ProbeWeb();
            Check(SurfaceGlass.AlwaysUseFallback && probe.GetProperty("glass").GetString()=="opaque" && probe.GetProperty("alpha").GetDouble()==1,"system_transparency_fallback_preserved");
            ApplyAppearanceVisuals(true,true); await SyncGlassToneToWebAsync();
            Check(SurfaceGlass.AlwaysUseFallback && PaneGlass.AlwaysUseFallback && navigation.RequestedTheme==ElementTheme.Default,"high_contrast_fallback_preserved");
            ApplyAppearanceVisuals(false,true);
            await Choose("wallpaper","ink");
            // Analysis is native since 0.14.0; another Web document checks the same appearance handoff.
            pageReady = false; browser.CoreWebView2.Navigate(backend!.Url + "/price-watch");
            for(var i=0;i<100 && await browser.CoreWebView2.ExecuteScriptAsync("!!window.LedgerAppearance")!="true";i++) await Task.Delay(100);
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.documentElement.dataset.desktopVisualMode === 'static' && document.documentElement.dataset.desktopTone === 'dark' && document.documentElement.dataset.theme === 'ink'")=="true","new_document_receives_automatic_static_profile");
            await NavigateAsync("overview");
            await Choose("wallpaper","jade"); await Choose("color","indigo"); probe=await ProbeWeb();
            Check(preferences.UiTone=="dark" && probe.GetProperty("theme").GetString()=="indigo" && preferences.Appearance.Wallpaper=="jade","explicit_palette_still_available_without_manual_brightness");
            Check(preferences.GameModeEnabled==originalGame,"game_preference_preserved");
            File.WriteAllText(Path.Combine(root,"verification","glass-runtime.json"),JsonSerializer.Serialize(new{status="passed",checks,profiles,capturedWindowFrames,skippedWindowFrames,visualCaptureComplete=skippedWindowFrames.Count==0,at=DateTimeOffset.Now}));
        } catch(Exception error) {
            Log(error);File.WriteAllText(Path.Combine(root,"verification","glass-runtime.json"),JsonSerializer.Serialize(new{status="failed",checks,profiles,capturedWindowFrames,skippedWindowFrames,error=error.ToString(),at=DateTimeOffset.Now}));
        } finally {
            ResumeWeb();
            await NavigateAsync("overview");
            if(originalWeb!=null) {
                await browser.CoreWebView2.ExecuteScriptAsync($"(() => {{const value={originalWeb};if(value===null)localStorage.removeItem('expense-ledger-appearance-v1');else localStorage.setItem('expense-ledger-appearance-v1',value);window.dispatchEvent(new StorageEvent('storage',{{key:'expense-ledger-appearance-v1'}}));}})()");
                await SyncAppearanceFromWebAsync();
            }
            preferences.Appearance=originalAppearance;
            await Mode(originalMode); ApplyNativeTheme(); preferences.Save(root);
            await RegisterDesktopBridgeAsync(); await SyncGlassToneToWebAsync();
            AppWindow.Move(originalPosition);
            await NavigateAsync("overview");WriteState("ready");
        }
    }
}
