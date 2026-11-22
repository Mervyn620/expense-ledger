using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    readonly List<string> capturedWindowFrames = new(), skippedWindowFrames = new();
    [StructLayout(LayoutKind.Sequential)] struct ScreenRect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr handle, out ScreenRect rect);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr handle, int attribute, out ScreenRect rect, int size);
    async Task VerifyOverviewPixelsAsync(string name,List<string> checks)
    {
        await Task.Delay(500);
        if (browser.Visibility!=Visibility.Visible || browser.CoreWebView2.IsSuspended)
            throw new Exception("Overview is not visible: "+name);
        var ready=await browser.CoreWebView2.ExecuteScriptAsync("(() => { const panel=document.querySelector('[data-panel=overview]'); const card=panel?.querySelector('.metric-card'); return window.expenseDesktopReady === true && panel.classList.contains('active') && card?.getBoundingClientRect().width > 100 && getComputedStyle(document.body,'::before').backgroundImage.includes('gradient') && document.querySelector('#expense-desktop-stars')?.width > 0; })()");
        if (ready!="true") throw new Exception("Overview data or wallpaper missing: "+name);
        var path=Path.Combine(root,"verification","rendering-"+name+"-web.png");
        File.WriteAllBytes(path,Array.Empty<byte>());
        var file=await Windows.Storage.StorageFile.GetFileFromPathAsync(path);
        using (var stream=await file.OpenAsync(Windows.Storage.FileAccessMode.ReadWrite))
            await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream);
        CaptureWindowPixels(name);
        checks.Add("overview_content_wallpaper_and_visibility_"+name);
    }
    void CaptureWindowPixels(string name)
    {
        // Only called by explicit verification. Capture the app's actual compositor output,
        // since RenderTargetBitmap excludes WebView2 and cannot detect this regression.
        var handle=WinRT.Interop.WindowNative.GetWindowHandle(this);
        if(GetForegroundWindow()!=handle || !GetWindowRect(handle,out var rect)) {
            skippedWindowFrames.Add(name); return;
        }
        // Exclude the invisible resize border so a narrow-window capture contains only this app.
        if(DwmGetWindowAttribute(handle,9,out var visible,Marshal.SizeOf<ScreenRect>())==0 && visible.Right>visible.Left && visible.Bottom>visible.Top)
            rect=visible;
        using var bitmap=new System.Drawing.Bitmap(rect.Right-rect.Left,rect.Bottom-rect.Top);
        using var graphics=System.Drawing.Graphics.FromImage(bitmap);
        graphics.CopyFromScreen(rect.Left,rect.Top,0,0,bitmap.Size);
        // A foreground HWND can precede the compositor switch from a fullscreen app.
        // Never accept or persist that other app's pixels as ledger visual evidence.
        if(AppWindow.TitleBar.BackgroundColor is Windows.UI.Color titleColor) {
            var matches=0;
            foreach(var fraction in new[]{.25,.5,.75}) {
                var pixel=bitmap.GetPixel((int)(bitmap.Width*fraction),8);
                if(Math.Abs(pixel.R-titleColor.R)<=6 && Math.Abs(pixel.G-titleColor.G)<=6 && Math.Abs(pixel.B-titleColor.B)<=6)matches++;
            }
            if(matches<2 || GetForegroundWindow()!=handle) { skippedWindowFrames.Add(name);return; }
        }
        bitmap.Save(Path.Combine(root,"verification","rendering-"+name+"-screen.png"));
        capturedWindowFrames.Add(name);
    }
    async Task VerifyStarGeometryAsync(System.Collections.Generic.List<string> checks)
    {
        await browser.CoreWebView2.ExecuteScriptAsync("(() => { const ctx=document.querySelector('#expense-desktop-stars').getContext('2d');window.__starProbe={ctx,arc:ctx.arc,first:null};ctx.arc=function(x,y,r,...rest){window.__starProbe.first ??= {x,y,r};return window.__starProbe.arc.call(this,x,y,r,...rest);}; })()");
        // Settled stars drift slowly (StarfieldTimeline.Drift), so compare at the shared clock.
        var seconds=visualClock.Elapsed.TotalSeconds-startupAt;
        await SyncStarfieldFrameAsync();
        var sample=await browser.CoreWebView2.ExecuteScriptAsync("(() => {const probe=window.__starProbe;probe.ctx.arc=probe.arc;delete window.__starProbe;return probe.first;})()");
        using var doc=JsonDocument.Parse(sample);
        var origin=browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0));
        var expected=StarfieldTimeline.Sample(0,160,layout.ActualWidth,layout.ActualHeight,Math.Max(seconds,StarfieldTimeline.RevealAt));
        if(Math.Abs(doc.RootElement.GetProperty("x").GetDouble()-(expected.X-origin.X+2.1))>.6 ||
            Math.Abs(doc.RootElement.GetProperty("y").GetDouble()-(expected.Y-origin.Y+2.1))>.6)
            throw new Exception("Web star coordinates do not align with native startup stars");
        checks.Add("web_star_position_matches_native_settled_position");
    }
    async Task VerifyPaletteRegressionAsync(List<string> checks)
    {
        var original=await browser.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('expense-ledger-appearance-v1')");
        try {
            foreach(var color in new[]{"jade","indigo","ink","cloud"}) {
                await browser.CoreWebView2.ExecuteScriptAsync($"document.querySelector('[data-color-choice={color}]').click()");
                await Task.Delay(150);
                if(preferences.Appearance.EffectiveColor!=color) throw new Exception("Palette did not reach host: "+color);
                var visible=await browser.CoreWebView2.ExecuteScriptAsync("getComputedStyle(document.querySelector('[data-color-choice=jade]').closest('fieldset')).display !== 'none'");
                if(visible!="true") throw new Exception("Particle mode hides theme colors");
                await NavigateAsync("native-settings");
                // This is the actual control resource, not just a stored preference.
                var brush=(SolidColorBrush)progress.Foreground;
                if(brush.Color!=((SolidColorBrush)Application.Current.Resources["LedgerControlAccentBrush"]).Color)
                    throw new Exception("Native progress control still uses the Windows accent");
                await Task.Delay(150); CaptureWindowPixels("native-"+color);
                checks.Add("native_controls_and_particle_palette_"+color);
                await NavigateAsync("overview");
            }
        } finally {
            ResumeWeb();
            await browser.CoreWebView2.ExecuteScriptAsync($"(() => {{const value={original};if(value===null)localStorage.removeItem('expense-ledger-appearance-v1');else localStorage.setItem('expense-ledger-appearance-v1',value);window.dispatchEvent(new StorageEvent('storage',{{key:'expense-ledger-appearance-v1'}}));}})()");
            await SyncAppearanceFromWebAsync();
        }
    }
}
