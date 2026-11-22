using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Documents;
using Forms = System.Windows.Forms;

namespace Expenses.Desktop;

// Native side of the interface language. The XAML and code keep their Chinese source text;
// in English the visible text properties are swapped through Loc and the Chinese original is
// remembered, so switching back restores it. Text set later by code is caught by a property
// callback; elements added later are caught by a throttled walk after layout. Data-bound
// template text ({Binding}) is left alone; those labels are translated where they are built.
public sealed partial class MainWindow
{
    sealed class TextSlot { public string? Original, Shown; }
    // The remembered originals live on the element itself: unnamed XAML elements can get a new
    // .NET wrapper after a garbage collection, so a table keyed by the wrapper would lose them.
    static readonly DependencyProperty TextSlotsProperty =
        DependencyProperty.RegisterAttached("LedgerTextSlots", typeof(object), typeof(MainWindow), new PropertyMetadata(null));
    static Dictionary<DependencyProperty, TextSlot>? SlotsOf(DependencyObject element) => element.GetValue(TextSlotsProperty) as Dictionary<DependencyProperty, TextSlot>;
    static Dictionary<DependencyProperty, TextSlot> CreateSlots(DependencyObject element)
    {
        if (SlotsOf(element) is { } existing) return existing;
        var created = new Dictionary<DependencyProperty, TextSlot>();
        element.SetValue(TextSlotsProperty, created);
        return created;
    }
    bool applyingText, localizeQueued;
    DateTime lastLocalizeWalk;
    DispatcherQueueTimer? localizeTimer;
    Forms.ToolStripItem? trayOpenItem, trayExitItem;

    static readonly string[] ToggleOn = { "开启", "启用" };

    void InitializeLocalization()
    {
        try { Loc.Load(root); } catch (Exception error) { Log(error); }
        Loc.SetLanguage(preferences.Language);
        chineseLanguage.IsChecked = !Loc.English;
        englishLanguage.IsChecked = Loc.English;
        chineseLanguage.Checked += async (_, _) => await ChangeLanguageAsync("zh");
        englishLanguage.Checked += async (_, _) => await ChangeLanguageAsync("en");
        localizeTimer = DispatcherQueue.CreateTimer();
        localizeTimer.Interval = TimeSpan.FromMilliseconds(450);
        localizeTimer.IsRepeating = false;
        localizeTimer.Tick += (_, _) => { localizeQueued = false; LocalizeTree(layout); };
        // New elements (job rows, order details, notices) appear after a layout pass.
        layout.LayoutUpdated += (_, _) => QueueLocalize();
    }

    void QueueLocalize()
    {
        if (!Loc.English || localizeQueued || localizeTimer == null) return;
        localizeQueued = true;
        // At most about two walks a second, even while the starfield meteor relayouts every frame.
        var wait = TimeSpan.FromMilliseconds(450) - (DateTime.UtcNow - lastLocalizeWalk);
        localizeTimer.Interval = wait > TimeSpan.FromMilliseconds(60) ? wait : TimeSpan.FromMilliseconds(60);
        localizeTimer.Start();
    }

    // Window chrome, tray and the whole native tree in the current language.
    void ApplyNativeLanguage()
    {
        Title = Loc.T(lastBackendBusy ? "账本 · 桌面版 · 任务进行中" : "账本 · 桌面版");
        if (tray != null) tray.Text = Loc.T(lastBackendBusy ? "账本 · 任务进行中" : "账本 · 桌面版");
        if (trayOpenItem != null) trayOpenItem.Text = Loc.T("打开账本");
        if (trayExitItem != null) trayExitItem.Text = Loc.T("退出账本");
        LocalizeTree(layout);
        UpdateCompactNavigation();
        // A closed ComboBox keeps showing the text it had when the item was selected; reselect quietly.
        if (collectorLocation.SelectedIndex >= 0) {
            var selected = collectorLocation.SelectedIndex;
            syncingCollectorLocation = true;
            try { collectorLocation.SelectedIndex = -1; collectorLocation.SelectedIndex = selected; } finally { syncingCollectorLocation = false; }
        }
    }

    async Task ChangeLanguageAsync(string language)
    {
        if ((language == "en") == Loc.English && preferences.Language == language) return;
        preferences.Language = language;
        try { preferences.Save(root); } catch (Exception error) { Log(error); }
        Loc.SetLanguage(language);
        ApplyNativeLanguage();
        // Model-built labels (order status, platform, payment) are rebuilt by reloading the lists.
        InvalidateNativeLedger();
        orderFiltersStale = true;
        if (currentRoute == "orders") await LoadOrdersAsync(refreshFilters: true, preservePosition: true);
        if (currentRoute is "cash" or "sources") await LoadFinanceAsync();
        // New documents get the language from the bridge script; reload the open page now.
        await RegisterDesktopBridgeAsync();
        if (pageReady && browser.CoreWebView2 != null) browser.CoreWebView2.Reload();
        NotifyNative(language == "en" ? "界面语言已切换为英文。" : "界面语言已切换为中文。");
    }

    // First launch (and the first launch after this feature arrived): pick the interface language.
    async Task MaybeChooseLanguageAsync()
    {
        if (!string.IsNullOrEmpty(preferences.Language) || OtherVerificationRunning() || OnboardingVerificationRequested || layout.XamlRoot == null) return;
        var body = new StackPanel { Spacing = 10, MaxWidth = 420 };
        body.Children.Add(new TextBlock { Text = "界面文字会切换为所选语言；账本里的商品名、商户名、分类和备注保持原样。", TextWrapping = TextWrapping.Wrap });
        body.Children.Add(new TextBlock { Text = "Interface text switches to the chosen language; item names, merchants, categories and notes in the ledger stay as recorded.", TextWrapping = TextWrapping.Wrap });
        body.Children.Add(new TextBlock { Text = "可以随时在 设置 → 使用体验 → 语言 中更改。 · You can change this anytime in Settings → Experience → Language.", TextWrapping = TextWrapping.Wrap, Opacity = .78 });
        var dialog = new ContentDialog {
            XamlRoot = layout.XamlRoot, Title = "选择语言 · Choose language", Content = body,
            PrimaryButtonText = "中文", SecondaryButtonText = "English", DefaultButton = ContentDialogButton.Primary,
        };
        ConfigureLedgerDialog(dialog, localize: false);
        dialog.Tag = "language-choice";
        var result = await dialog.ShowAsync();
        var language = result == ContentDialogResult.Secondary ? "en" : "zh";
        chineseLanguage.IsChecked = language == "zh";
        englishLanguage.IsChecked = language == "en";
        if (preferences.Language != language) await ChangeLanguageAsync(language);
    }

    void LocalizeDialog(ContentDialog dialog)
    {
        foreach (var property in new[] { ContentDialog.TitleProperty, ContentDialog.PrimaryButtonTextProperty, ContentDialog.SecondaryButtonTextProperty, ContentDialog.CloseButtonTextProperty, ContentControl.ContentProperty })
            LocalizeText(dialog, property);
        if (dialog.Content is UIElement content) LocalizeTree(content);
        dialog.Opened += (_, _) => { if (dialog.Content is UIElement opened) LocalizeTree(opened); };
    }

    void LocalizeTree(object? node)
    {
        lastLocalizeWalk = DateTime.UtcNow;
        Walk(node, 0);
    }

    void Walk(object? node, int depth)
    {
        if (node is not DependencyObject element || depth > 80) return;
        // Bound list rows show ledger data; their labels are translated where they are built.
        if (element is ListViewItem or WebView2) return;
        LocalizeElement(element);
        switch (element) {
            case Panel panel: foreach (var child in panel.Children) Walk(child, depth + 1); break;
            case Border border: Walk(border.Child, depth + 1); break;
            case Viewbox box: Walk(box.Child, depth + 1); break;
            case UserControl control: Walk(control.Content, depth + 1); break;
            case NavigationView navigation:
                foreach (var item in navigation.MenuItems.Concat(navigation.FooterMenuItems)) Walk(item, depth + 1);
                Walk(navigation.PaneHeader, depth + 1); Walk(navigation.PaneFooter, depth + 1); Walk(navigation.Content, depth + 1);
                break;
            case Expander expander: Walk(expander.Header, depth + 1); Walk(expander.Content, depth + 1); break;
            case ComboBox combo: foreach (var item in combo.Items) Walk(item, depth + 1); break;
            case RadioButtons group: LocalizeRadioItems(group); break;
            case ContentControl content:
                Walk(content.Content, depth + 1);
                if (content is Button { Flyout: Flyout flyout }) Walk(flyout.Content, depth + 1);
                break;
            case TextBlock text:
                foreach (var inline in text.Inlines) if (inline is Run run) LocalizeRun(run);
                break;
        }
        if (element is FrameworkElement { ContextFlyout: Flyout contextFlyout }) Walk(contextFlyout.Content, depth + 1);
    }

    void LocalizeElement(DependencyObject element)
    {
        switch (element) {
            case TextBlock: LocalizeText(element, TextBlock.TextProperty); break;
            case ToggleSwitch:
                LocalizeText(element, ToggleSwitch.HeaderProperty);
                LocalizeText(element, ToggleSwitch.OnContentProperty);
                LocalizeText(element, ToggleSwitch.OffContentProperty);
                break;
            case TextBox:
                LocalizeText(element, TextBox.HeaderProperty);
                LocalizeText(element, TextBox.PlaceholderTextProperty);
                break;
            case ComboBox:
                LocalizeText(element, ComboBox.HeaderProperty);
                LocalizeText(element, ComboBox.PlaceholderTextProperty);
                break;
            case RadioButtons: LocalizeText(element, RadioButtons.HeaderProperty); break;
            case CalendarDatePicker:
                LocalizeText(element, CalendarDatePicker.HeaderProperty);
                LocalizeText(element, CalendarDatePicker.PlaceholderTextProperty);
                break;
            case InfoBar:
                LocalizeText(element, InfoBar.TitleProperty);
                LocalizeText(element, InfoBar.MessageProperty);
                break;
            case Expander: LocalizeText(element, Expander.HeaderProperty); break;
            case NavigationView: LocalizeText(element, NavigationView.PaneTitleProperty); break;
            case ContentControl: LocalizeText(element, ContentControl.ContentProperty); break;
        }
        if (element is UIElement) {
            LocalizeText(element, AutomationProperties.NameProperty);
            LocalizeText(element, ToolTipService.ToolTipProperty);
        }
    }

    // Plain string items (界面大小 小/中/大) are replaced in place, keeping the selection.
    void LocalizeRadioItems(RadioButtons group)
    {
        for (var index = 0; index < group.Items.Count; index++) {
            if (group.Items[index] is not string current) continue;
            if (!radioOriginals.TryGetValue((group, index), out var slot)) {
                if (!HasChinese(current)) continue;
                slot = new TextSlot(); radioOriginals[(group, index)] = slot;
            }
            if (slot.Original == null || current != slot.Shown) slot.Original = current;
            var wanted = Loc.English ? Loc.T(slot.Original) : slot.Original;
            slot.Shown = wanted;
            if (wanted == current) continue;
            var selected = group.SelectedIndex;
            applyingText = true;
            try { group.Items[index] = wanted; group.SelectedIndex = selected; } finally { applyingText = false; }
        }
    }
    readonly Dictionary<(RadioButtons, int), TextSlot> radioOriginals = new();

    void LocalizeText(DependencyObject element, DependencyProperty property)
    {
        if (element.ReadLocalValue(property) is BindingExpression) return;
        var slots = SlotsOf(element);
        if (slots == null || !slots.TryGetValue(property, out var slot)) {
            // Nothing to remember until the property holds Chinese text.
            if (element.GetValue(property) is not string first || !HasChinese(first)) return;
            slot = new TextSlot();
            CreateSlots(element)[property] = slot;
            element.RegisterPropertyChangedCallback(property, (sender, _) => {
                if (!applyingText) Apply(sender, property, slot);
            });
        }
        Apply(element, property, slot);
    }

    void Apply(DependencyObject element, DependencyProperty property, TextSlot slot)
    {
        if (element.GetValue(property) is not string current) return;
        // Text that is not what we last showed came from the app: it is the new original.
        if (slot.Original == null || current != slot.Shown) slot.Original = current;
        var original = slot.Original;
        var wanted = !Loc.English ? original :
            property == ToggleSwitch.OffContentProperty && original == "关闭" ? "Off" :
            property == ToggleSwitch.OnContentProperty && ToggleOn.Contains(original) ? "On" : Loc.T(original);
        slot.Shown = wanted;
        if (wanted == current) return;
        applyingText = true;
        try { element.SetValue(property, wanted); } finally { applyingText = false; }
    }

    static bool HasChinese(string text) => text.Any(c => c >= '㐀' && c <= '鿿');

    // Run.Text is not a dependency property, so runs are checked on each walk instead of by callback.
    void LocalizeRun(Run run)
    {
        var current = run.Text ?? "";
        var slots = SlotsOf(run);
        if (slots == null || !slots.TryGetValue(TextSlotsProperty, out var slot)) {
            if (!HasChinese(current)) return;
            slot = new TextSlot();
            CreateSlots(run)[TextSlotsProperty] = slot;
        }
        if (slot.Original == null || current != slot.Shown) slot.Original = current;
        var wanted = Loc.English ? Loc.T(slot.Original) : slot.Original;
        slot.Shown = wanted;
        if (wanted != current) run.Text = wanted;
    }

    // Runs before every WebView document: the language, the shared dictionary and the page translator.
    string WebLanguageScript()
    {
        var language = System.Text.Json.JsonSerializer.Serialize(Loc.Language);
        try {
            // Chinese pages get the translator too; it only exposes LedgerI18n and changes nothing.
            var translator = System.IO.File.ReadAllText(System.IO.Path.Combine(root, "backend", "app", "static", "desktop-i18n.js"));
            var dictionary = Loc.English ? System.IO.File.ReadAllText(System.IO.Path.Combine(root, Loc.DictionaryPath)) : "null";
            return "window.__expenseDesktopLanguage = " + language + "; window.__expenseDesktopI18n = " + dictionary + ";\n" + translator + "\n";
        } catch (Exception error) {
            Log(error);
            return "window.__expenseDesktopLanguage = \"zh\";\n";
        }
    }
}
