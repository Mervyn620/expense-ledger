using System;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool lastBackendBusy, updatingResources;
    long resourceRevision;
    async Task UpdateResourceModeAsync()
    {
        var revision = ++resourceRevision;
        if (monitor != null) monitor.Interval = TimeSpan.FromSeconds(lastBackendBusy ? 3 : WindowVisible ? 10 : 20);
        if (browser.CoreWebView2 == null || !pageReady || starting || exiting) return;
        bool hidden = !WindowVisible || IsNativeRoute || connectionFailed;
        if (!hidden || lastBackendBusy) {
            ResumeWeb();
            return;
        }
        if (updatingResources) return;
        updatingResources = true;
        try {
            await Task.Delay(350);
            if (revision != resourceRevision || exiting || !pageReady || lastBackendBusy || WindowVisible && !IsNativeRoute && !connectionFailed) return;
            browser.Visibility = Visibility.Collapsed;
            await browser.CoreWebView2.TrySuspendAsync();
            // Navigation can race the asynchronous suspension request.
            if (!exiting && (lastBackendBusy || WindowVisible && !IsNativeRoute && !connectionFailed)) ResumeWeb();
        } catch (Exception error) { Log(error); }
        finally { updatingResources = false; if (revision != resourceRevision && !exiting) _ = UpdateResourceModeAsync(); }
    }
    void ResumeWeb()
    {
        resourceRevision++;
        if (browser.CoreWebView2?.IsSuspended == true) browser.CoreWebView2.Resume();
        if (WindowVisible && !IsNativeRoute && !connectionFailed && pageReady && shellReady && !webFailed)
            browser.Visibility = Visibility.Visible;
    }
}
