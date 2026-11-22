using System;
using System.Globalization;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;

namespace Expenses.Desktop;

// Three interface sizes (small 90%, medium 100%, large 115%) for native pages and the WebView.
// WinUI has no LayoutTransform, so each native page is laid out at size / scale and scaled back up.
// The WebView's own free zoom is disabled; Web pages use CSS zoom at the same factor.
public sealed partial class MainWindow
{
    static readonly string[] UiScaleLevels = { "small", "medium", "large" };
    bool updatingUiScaleChoice;
    FrameworkElement[] NativeScaledPanels => new FrameworkElement[] { financePanel, ordersPanel, settingsPanel, maintenancePanel, analysisPanel };

    void InitializeUiScale()
    {
        SelectUiScaleChoice();
        uiScaleChoice.SelectionChanged += (_, _) => {
            if (!updatingUiScaleChoice && uiScaleChoice.SelectedIndex is >= 0 and < 3) SetUiScale(UiScaleLevels[uiScaleChoice.SelectedIndex]);
        };
        nativeContent.SizeChanged += (_, _) => ApplyNativeScale();
        foreach (var (key, step) in new[] {
            (VirtualKey.Add, 1), ((VirtualKey)187, 1), (VirtualKey.Subtract, -1), ((VirtualKey)189, -1),
            (VirtualKey.Number0, 0), (VirtualKey.NumberPad0, 0) }) {
            var accelerator = new KeyboardAccelerator { Key = key, Modifiers = VirtualKeyModifiers.Control };
            accelerator.Invoked += (_, args) => { args.Handled = true; StepUiScale(step); };
            layout.KeyboardAccelerators.Add(accelerator);
        }
        ApplyNativeScale();
    }

    void SelectUiScaleChoice()
    {
        updatingUiScaleChoice = true;
        uiScaleChoice.SelectedIndex = Math.Max(0, Array.IndexOf(UiScaleLevels, preferences.UiScale));
        updatingUiScaleChoice = false;
    }

    // +1 larger, -1 smaller, 0 back to medium. Stops at the ends instead of wrapping.
    void StepUiScale(int step)
    {
        var index = step == 0 ? 1 : Math.Clamp(Array.IndexOf(UiScaleLevels, preferences.UiScale) + Math.Sign(step), 0, UiScaleLevels.Length - 1);
        SetUiScale(UiScaleLevels[index]);
    }

    void SetUiScale(string level)
    {
        if (preferences.UiScale == level) { SelectUiScaleChoice(); return; }
        preferences.UiScale = level;
        try { preferences.Save(root); } catch (Exception error) { Log(error); }
        SelectUiScaleChoice();
        ApplyNativeScale();
        _ = ApplyWebScaleAsync();
    }

    void ApplyNativeScale()
    {
        var scale = preferences.UiScaleFactor;
        var width = nativeContent.ActualWidth;
        var height = nativeContent.ActualHeight;
        foreach (var panel in NativeScaledPanels) {
            if (Math.Abs(scale - 1) < 0.001 || width <= 0 || height <= 0) {
                panel.ClearValue(FrameworkElement.WidthProperty);
                panel.ClearValue(FrameworkElement.HeightProperty);
                panel.ClearValue(FrameworkElement.HorizontalAlignmentProperty);
                panel.ClearValue(FrameworkElement.VerticalAlignmentProperty);
                panel.RenderTransform = null;
                continue;
            }
            var margin = panel.Margin;
            panel.HorizontalAlignment = HorizontalAlignment.Left;
            panel.VerticalAlignment = VerticalAlignment.Top;
            panel.Width = Math.Max(0, (width - margin.Left - margin.Right) / scale);
            panel.Height = Math.Max(0, (height - margin.Top - margin.Bottom) / scale);
            panel.RenderTransform = new ScaleTransform { ScaleX = scale, ScaleY = scale };
        }
    }

    async Task ApplyWebScaleAsync()
    {
        await RegisterDesktopBridgeAsync(); // new documents start at the chosen size
        if (browser.CoreWebView2 == null) return;
        try {
            var value = preferences.UiScaleFactor.ToString(CultureInfo.InvariantCulture);
            await browser.CoreWebView2.ExecuteScriptAsync("window.__expenseDesktopZoom = " + value + "; window.expenseDesktopApplyZoom?.();");
            await SyncStarfieldFrameAsync();
        } catch (Exception error) { Log(error); }
    }
}
