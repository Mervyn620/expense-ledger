using System;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.Web.WebView2.Core;
using Windows.Storage.Streams;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool webFrameReady, usedFrameCaptureFallback;
    Task? startupReveal;
    double handoffStartedAt, handoffCompletedAt;
    async Task<bool> SyncStarfieldFrameAsync()
    {
        if (!pageReady || browser.CoreWebView2 == null || browser.CoreWebView2.IsSuspended || exiting || layout.ActualWidth < 1 || browser.ActualWidth < 1) return false;
        try {
            var origin = browser.TransformToVisual(layout).TransformPoint(new Windows.Foundation.Point(0,0));
            var stops = ((LinearGradientBrush)starfieldBackground.Background).GradientStops;
            string Hex(Windows.UI.Color c) => $"#{c.R:X2}{c.G:X2}{c.B:X2}";
            var frame = new {width=layout.ActualWidth,height=layout.ActualHeight,x=origin.X,y=origin.Y,webWidth=browser.ActualWidth,
                seconds=visualClock.Elapsed.TotalSeconds-startupAt,
                colors=new[]{Hex(stops[0].Color),Hex(stops[1].Color),Hex(stops[2].Color)}};
            var json = JsonSerializer.Serialize(frame);
            return await browser.CoreWebView2.ExecuteScriptAsync("window.__expenseDesktopLayoutFrame="+json+";window.expenseDesktopLayout?.setHostFrame(window.__expenseDesktopLayoutFrame);window.expenseDesktopEffects?.setHostFrame(window.__expenseDesktopLayoutFrame)") == "true";
        } catch(Exception error) { if(!exiting) Log(error); return false; }
    }
    async Task<bool> PrepareWebFrameAsync(ulong navigationId)
    {
        try {
            var aligned=false;
            for(var i=0;i<80&&!exiting&&navigationId==webNavigationId;i++) {
                if(await SyncStarfieldFrameAsync()) { aligned=true; break; }
                await Task.Delay(25);
            }
            if(!aligned || exiting || navigationId!=webNavigationId) return false;
            var marker = JsonSerializer.Serialize(navigationId.ToString());
            await browser.CoreWebView2.ExecuteScriptAsync("window.__expenseDesktopPaintReady=null;requestAnimationFrame(()=>requestAnimationFrame(()=>{window.expenseDesktopEffects?.repaint();window.__expenseDesktopPaintReady="+marker+";}))");
            for(var i=0;i<60&&!exiting&&navigationId==webNavigationId;i++) {
                if(await browser.CoreWebView2.ExecuteScriptAsync("window.__expenseDesktopPaintReady === "+marker)=="true") { webFrameReady=true; return true; }
                await Task.Delay(25);
            }
            if(exiting || navigationId!=webNavigationId) return false;
            // Chromium may throttle rAF behind another window. Force a renderer frame without
            // treating background throttling as a ledger failure or saving private pixels.
            using var frame = new InMemoryRandomAccessStream();
            await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,frame);
            usedFrameCaptureFallback=true;
            webFrameReady=frame.Size>0;
            return webFrameReady;
        } catch(Exception error) { if(!exiting) Log(error); return false; }
    }
    Task RevealStartupContentAsync()
    {
        if (!startupActive || exiting) return Task.CompletedTask;
        return startupReveal ??= FadeStartupCoverAsync();
    }
    async Task FadeStartupCoverAsync()
    {
        await SyncStarfieldFrameAsync();
        handoffStartedAt=visualClock.Elapsed.TotalSeconds-startupAt;
        loading.Visibility=Visibility.Collapsed;
        var completion=new TaskCompletionSource<bool>();
        var fade=new DoubleAnimation {From=1,To=0,Duration=new Duration(TimeSpan.FromMilliseconds(380)),EasingFunction=new CubicEase {EasingMode=EasingMode.EaseInOut}};
        Storyboard.SetTarget(fade,starfieldLayer); Storyboard.SetTargetProperty(fade,"Opacity");
        var storyboard=new Storyboard(); storyboard.Children.Add(fade);
        storyboard.Completed+=(_,_)=>completion.TrySetResult(true);
        storyboard.Begin();
        await completion.Task;
        storyboard.Stop();
        if (exiting) return;
        EndStartupEffect();
        handoffCompletedAt=visualClock.Elapsed.TotalSeconds-startupAt;
        ShowRoute();
    }
}
