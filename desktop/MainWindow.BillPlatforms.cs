using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool choosingBillPlatforms;
    void InitializeBillPlatforms() => defaultBillPlatforms.Click += async (_, _) => await ChooseDefaultBillPlatformsAsync();
    ContentDialog CreateBillPlatformsDialog(out List<CheckBox> choices)
    {
        choices = new List<CheckBox>();
        var list = new StackPanel { Spacing = 8 };
        foreach (var id in BillPlatformPreferences.All) {
            var box = new CheckBox { Content = LedgerLabels.Platform(id), Tag = id, IsChecked = preferences.DefaultBillPlatforms.Contains(id), MinHeight = 48, HorizontalAlignment = HorizontalAlignment.Stretch, Foreground = (Brush)layout.Resources["LedgerTextBrush"] };
            choices.Add(box); list.Children.Add(box);
        }
        var content = new ScrollViewer { Content = list, MaxHeight = Math.Max(80,Math.Min(260,layout.ActualHeight-200)), HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalContentAlignment = HorizontalAlignment.Stretch };
        var dialog = new ContentDialog { XamlRoot = layout.XamlRoot, RequestedTheme = layout.ActualTheme, Title = "默认更新平台", PrimaryButtonText = "保存", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary, Content = content, Background = SurfaceGlass, Foreground = (Brush)layout.Resources["LedgerTextBrush"], BorderBrush = (Brush)layout.Resources["LedgerEdgeBrush"], MaxHeight = Math.Max(180,Math.Min(480,layout.ActualHeight-48)) };
        ConfigureLedgerDialog(dialog);
        return dialog;
    }
    async Task ChooseDefaultBillPlatformsAsync()
    {
        if (choosingBillPlatforms || exiting) return;
        choosingBillPlatforms = true;
        try {
            var dialog = CreateBillPlatformsDialog(out var choices);
            dialog.PrimaryButtonClick += (_, args) => {
                var before = preferences.DefaultBillPlatforms;
                preferences.DefaultBillPlatforms = BillPlatformPreferences.Normalize(choices.Where(box => box.IsChecked == true).Select(box => (string)box.Tag));
                try { preferences.Save(root); settingsNotice.IsOpen = false; }
                catch (Exception error) {
                    preferences.DefaultBillPlatforms = before; args.Cancel = true;
                    settingsNotice.Title = "默认平台未能保存"; settingsNotice.Message = error.Message;
                    settingsNotice.Severity = InfoBarSeverity.Error; settingsNotice.IsOpen = true;
                }
            };
            if (await dialog.ShowAsync() == ContentDialogResult.Primary) await SyncBillPlatformsToWebAsync();
        } catch (Exception error) { Log(error); NotifyNative("暂时无法打开默认平台设置。",true); }
        finally { choosingBillPlatforms = false; }
    }
    async Task SyncBillPlatformsToWebAsync()
    {
        await RegisterDesktopBridgeAsync();
        if (pageReady && browser.CoreWebView2 != null) {
            ResumeWeb();
            await browser.CoreWebView2.ExecuteScriptAsync("window.__expenseDesktopDefaultBillPlatforms = " + JsonSerializer.Serialize(preferences.DefaultBillPlatforms) + ";");
        }
    }
}
