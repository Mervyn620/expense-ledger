using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingLanguage;
    // EXPENSE_DESKTOP_VERIFY_LANGUAGE: switch to English from Settings, check native and Web text,
    // capture both, switch back and check that the Chinese originals return.
    async Task VerifyLanguageAsync()
    {
        if (verifyingLanguage || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_LANGUAGE") != "1") return;
        verifyingLanguage = true;
        var checks = new List<string>();
        var original = preferences.Language;
        void Check(bool value, string name) { if (!value) throw new Exception(name); checks.Add(name); }
        async Task Until(Func<bool> condition, string name) { for (var i = 0; i < 150 && !condition(); i++) await Task.Delay(100); Check(condition(), condition() ? name : $"{name} (nav={Nav("orders")}/{Nav("native-settings")}, settings={string.Join("|", Texts(settingsContent).Take(6))}, english={Loc.English})"); }
        async Task<bool> Web(string expression) => await browser.CoreWebView2.ExecuteScriptAsync(expression) == "true";
        async Task UntilWeb(string expression, string name) { for (var i = 0; i < 150 && !(pageReady && await Web(expression)); i++) await Task.Delay(100); Check(pageReady && await Web(expression), name); }
        string Nav(string tag) => navigation.MenuItems.Concat(navigation.FooterMenuItems).OfType<NavigationViewItem>().First(item => (item.Tag as string) == tag).Content as string ?? "";
        bool SettingsShows(string text) => Texts(settingsContent).Contains(text);
        try {
            Check(string.IsNullOrEmpty(original) || original is "zh" or "en", "language_preference_is_valid");
            Check(!Loc.English && Nav("orders") == "订单", "starts_in_chinese");
            await NavigateAsync("native-settings");
            englishLanguage.IsChecked = true;
            await Until(() => Loc.English && preferences.Language == "en" && DesktopPreferences.Load(root).Language == "en", "english_choice_is_saved");
            await Until(() => Nav("orders") == "Orders" && Nav("native-settings") == "Desktop settings", "native_navigation_in_english");
            await Until(() => SettingsShows("Desktop settings") && SettingsShows("Language") && SettingsShows("Order collection"), "native_settings_in_english");
            Check(gameMode.OffContent as string == "Off" && gameMode.OnContent as string == "On", "toggle_states_in_english");
            Check(uiScaleChoice.Items.OfType<string>().SequenceEqual(new[] { "Small", "Medium", "Large" }) && uiScaleChoice.SelectedIndex >= 0, "size_choices_in_english_keep_selection");
            Check(Title.StartsWith("Ledger", StringComparison.Ordinal), "window_title_in_english");
            Check(Loc.T("餐饮") == "餐饮" && Loc.T("Walmart") == "Walmart", "ledger_data_and_categories_unchanged");
            await CaptureNativePageAsync("en", "language-settings");
            await NavigateAsync("operations");
            await UntilWeb("document.documentElement.lang === 'en' && window.LedgerI18n?.language === 'en' && document.querySelector('.update-surface h2')?.textContent.trim() === 'Update bills'", "web_update_page_in_english");
            Check(await Web("[...document.querySelectorAll('.topbar a')].some(link => link.textContent.trim() === 'Deals') && document.querySelector('.topbar .price-watch-link').title === 'View Walmart deals'"), "web_topbar_in_english");
            await SaveWebPreviewAsync("language-web-update-en.png");
            await NavigateAsync("overview");
            await UntilWeb("document.querySelector('[data-panel=overview].active') !== null", "web_overview_open");
            await Task.Delay(600);
            await SaveWebPreviewAsync("language-web-overview-en.png");
            await NavigateAsync("orders");
            await Task.Delay(1500);
            await CaptureNativePageAsync("en", "language-orders");

            await NavigateAsync("native-settings");
            chineseLanguage.IsChecked = true;
            await Until(() => !Loc.English && preferences.Language == "zh" && DesktopPreferences.Load(root).Language == "zh", "chinese_choice_is_saved");
            await Until(() => Nav("orders") == "订单" && SettingsShows("桌面设置") && SettingsShows("订单采集"), "native_text_restored_in_chinese");
            Check(gameMode.OffContent as string == "关闭" && uiScaleChoice.Items.OfType<string>().SequenceEqual(new[] { "小", "中", "大" }), "toggle_and_size_restored");
            await NavigateAsync("operations");
            await UntilWeb("document.documentElement.lang !== 'en' && window.LedgerI18n?.language === 'zh' && document.querySelector('.update-surface h2')?.textContent.trim() === '更新账单'", "web_restored_in_chinese");
            File.WriteAllText(Path.Combine(root, "verification", "language-runtime.json"), JsonSerializer.Serialize(new { status = "passed", checks, at = DateTimeOffset.Now }));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root, "verification", "language-runtime.json"), JsonSerializer.Serialize(new { status = "failed", checks, error = error.ToString(), at = DateTimeOffset.Now }));
        } finally {
            preferences.Language = original;
            try { preferences.Save(root); } catch (Exception error) { Log(error); }
            Loc.SetLanguage(original);
            ApplyNativeLanguage();
            await RegisterDesktopBridgeAsync();
            await NavigateAsync("native-settings");
            WriteState("ready");
        }
    }

    async Task SaveWebPreviewAsync(string name)
    {
        var path = Path.Combine(root, "verification", name);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, Array.Empty<byte>());
        var file = await Windows.Storage.StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenAsync(Windows.Storage.FileAccessMode.ReadWrite);
        await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
    }

    static List<string> Texts(DependencyObject root)
    {
        var found = new List<string>();
        void Visit(object? node)
        {
            switch (node) {
                case TextBlock text: found.Add(text.Text); break;
                case Panel panel: foreach (var child in panel.Children) Visit(child); break;
                case Border border: Visit(border.Child); break;
                case ContentControl content: if (content.Content is string label) found.Add(label); else Visit(content.Content); break;
            }
        }
        Visit(root);
        return found;
    }
}
