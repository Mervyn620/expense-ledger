using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Storage.Pickers;

namespace Expenses.Desktop;

// First-run guide: bank source, shopping platforms, ledger start date. Each step can be
// skipped; logins happen later, the first time a platform or bank is actually refreshed.
internal sealed class OnboardingChoice
{
    public string BankMode { get; set; } = "";               // scotia | statements | later
    public string Institution { get; set; } = "";
    public string StatementType { get; set; } = "bank_account"; // bank_account | credit_card
    public string? StatementPath { get; set; }
    public string[] Platforms { get; set; } = Array.Empty<string>();
    public DateTimeOffset Floor { get; set; }
}

public sealed partial class MainWindow
{
    bool onboardingChecked, onboardingActive;
    const long MaxStatementBytes = 5 * 1024 * 1024;

    bool loadingCollectionSettings;
    void InitializeOnboarding()
    {
        parallelCollection.Toggled += async (_, _) => await SaveParallelCollectionAsync();
        rerunOnboarding.Click += async (_, _) => await RunOnboardingAsync(manual: true);
        importBankStatement.Click += async (_, _) => await ImportStatementFromSettingsAsync();
    }

    static bool OtherVerificationRunning() =>
        Environment.GetEnvironmentVariables().Keys.Cast<object>().Select(key => key.ToString() ?? "")
            .Any(key => key.StartsWith("EXPENSE_DESKTOP_VERIFY_", StringComparison.Ordinal) && key != "EXPENSE_DESKTOP_VERIFY_ONBOARDING");

    async Task StartFirstRunAsync()
    {
        try { await MaybeChooseLanguageAsync(); } catch (Exception error) { Log(error); }
        await MaybeStartOnboardingAsync();
    }

    async Task MaybeStartOnboardingAsync()
    {
        if (onboardingChecked || exiting || backend == null) return;
        onboardingChecked = true;
        if (OnboardingVerificationRequested) { await VerifyOnboardingAsync(); return; }
        if (OtherVerificationRunning()) return;
        try {
            var status = await backend.LedgerAsync<JsonElement>("/api/setup");
            if (status.TryGetProperty("needs_onboarding", out var needed) && needed.GetBoolean())
                await RunOnboardingAsync(manual: false);
        } catch (Exception error) { Log(error); }
    }

    async Task RefreshCollectionSettingsAsync()
    {
        if (backend == null) return;
        loadingCollectionSettings = true;
        try {
            var settings = await backend.LedgerAsync<JsonElement>("/api/collection-settings");
            parallelCollection.IsOn = settings.TryGetProperty("parallel_collection", out var on) && on.GetBoolean();
            parallelCollection.IsEnabled = true;
        } catch (Exception error) { Log(error); parallelCollection.IsEnabled = false; parallelCollectionHint.Text = "暂时无法读取采集方式。"; }
        finally { loadingCollectionSettings = false; }
    }

    async Task SaveParallelCollectionAsync()
    {
        if (loadingCollectionSettings || backend == null) return;
        var wanted = parallelCollection.IsOn;
        parallelCollection.IsEnabled = false;
        try {
            var saved = await backend.PostJsonAsync("/api/collection-settings", new { parallel_collection = wanted });
            loadingCollectionSettings = true;
            parallelCollection.IsOn = saved.TryGetProperty("parallel_collection", out var on) && on.GetBoolean();
            NotifyNative(parallelCollection.IsOn ? "已开启并发采集，下一次更新生效。" : "已改回一个一个采集。");
        } catch (Exception error) {
            loadingCollectionSettings = true;
            parallelCollection.IsOn = !wanted;
            NotifyNative("采集方式未能保存：" + error.Message, true);
        } finally { loadingCollectionSettings = false; parallelCollection.IsEnabled = true; }
    }

    async Task RefreshBankModeAsync()
    {
        if (backend == null) return;
        try {
            var status = await backend.LedgerAsync<JsonElement>("/api/setup");
            var statements = await backend.LedgerAsync<JsonElement>("/api/bank-statements");
            var accounts = LedgerLabels.Rows(statements, "accounts").Select(a =>
                $"{LedgerLabels.Text(a, "institution", "银行")}（{(LedgerLabels.Text(a, "statement_type", "") == "credit_card" ? "信用卡" : "账户")}，{LedgerLabels.Text(a, "rows", "0")} 笔）").ToArray();
            var mode = LedgerLabels.Text(status, "bank_mode", "later");
            var head = mode switch {
                "scotia" => "Scotiabank：登录网银自动采集流水。",
                "statements" => "其他银行：导入网银下载的 OFX、QFX 或 CSV 文件。",
                _ => "尚未选择银行流水来源。",
            };
            bankModeStatus.Text = head + (accounts.Length > 0 ? " 已导入：" + string.Join("、", accounts) + "。" : "");
        } catch (Exception error) { Log(error); bankModeStatus.Text = "暂时无法读取银行设置。"; }
    }

    async Task<OnboardingChoice> InitialChoiceAsync()
    {
        var choice = new OnboardingChoice { Platforms = preferences.DefaultBillPlatforms.ToArray() };
        var floor = DateTimeOffset.Now.AddMonths(-3);
        try {
            var status = await backend!.LedgerAsync<JsonElement>("/api/setup");
            if (DateTimeOffset.TryParseExact(LedgerLabels.Text(status, "suggested_floor", ""), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var suggested))
                floor = suggested;
            var mode = LedgerLabels.Text(status, "bank_mode", "");
            var completed = status.TryGetProperty("completed", out var done) && done.GetBoolean();
            if (completed && mode is "scotia" or "statements") choice.BankMode = mode;
        } catch (Exception error) { Log(error); }
        choice.Floor = floor;
        return choice;
    }

    async Task RunOnboardingAsync(bool manual)
    {
        if (onboardingActive || exiting || backend == null) return;
        onboardingActive = true;
        try {
            var choice = await InitialChoiceAsync();
            var step = 0;
            while (step is >= 0 and < 3) {
                var dialog = CreateOnboardingStep(step, choice, manual, out var commit);
                var result = await dialog.ShowAsync();
                if (result == ContentDialogResult.Primary) { commit(); step++; }
                else if (result == ContentDialogResult.Secondary) { commit(); step--; }
                else {
                    // Skipping the first-run guide records "later" so it does not return on every launch.
                    if (!manual) await SaveOnboardingAsync(new OnboardingChoice { BankMode = "later", Platforms = preferences.DefaultBillPlatforms });
                    return;
                }
            }
            if (step == 3) await CompleteOnboardingAsync(choice);
        } catch (Exception error) { Log(error); NotifyNative("首次设置未能完成：" + error.Message, true); }
        finally { onboardingActive = false; }
    }

    ContentDialog CreateOnboardingStep(int step, OnboardingChoice choice, bool manual, out Action commit)
    {
        var text = (Brush)layout.Resources["LedgerTextBrush"];
        var secondary = (Brush)layout.Resources["LedgerSecondaryTextBrush"];
        TextBlock Paragraph(string value, bool muted = false) => new() { Text = value, TextWrapping = TextWrapping.Wrap, Foreground = muted ? secondary : text };
        var body = new StackPanel { Spacing = 12, MaxWidth = 460 };
        var prefix = manual ? "首次设置" : "欢迎使用账本";
        string title; string primary = step == 2 ? "完成" : "下一步";
        Action save = () => { };
        Func<bool>? canContinue = null;
        ContentDialog? dialogRef = null;

        if (step == 0) {
            title = $"{prefix} · 1/3 银行流水";
            body.Children.Add(Paragraph("账本会把购物平台的订单和银行流水逐笔对账。先选你的银行流水从哪里来："));
            var options = new RadioButtons { MaxColumns = 1 };
            var labels = new (string Id, string Label)[] {
                ("scotia", "Scotiabank — 登录网银后自动采集流水"),
                ("statements", "其他银行 — 导入网银下载的 OFX、QFX 或 CSV 文件"),
                ("later", "稍后设置"),
            };
            foreach (var (id, label) in labels) options.Items.Add(new RadioButton { Content = label, Tag = id, Foreground = text, MinHeight = 40 });
            options.SelectedIndex = Array.FindIndex(labels, entry => entry.Id == choice.BankMode);
            body.Children.Add(options);

            var scotiaHint = Paragraph("第一次对账时会打开 Scotia 登录页，登录完成后自动采集并对账。", true);
            var statementPanel = new StackPanel { Spacing = 10 };
            var institution = new TextBox { Header = "银行名称", PlaceholderText = "例如 RBC、TD、BMO", Text = choice.Institution, MaxLength = 40 };
            var accountType = new RadioButtons { Header = "账户类型", MaxColumns = 2 };
            accountType.Items.Add(new RadioButton { Content = "借记 / 储蓄账户", Tag = "bank_account", Foreground = text });
            accountType.Items.Add(new RadioButton { Content = "信用卡", Tag = "credit_card", Foreground = text });
            accountType.SelectedIndex = choice.StatementType == "credit_card" ? 1 : 0;
            var fileName = Paragraph(choice.StatementPath is { } path ? "已选择：" + Path.GetFileName(path) : "可以现在选择文件，也可以以后在 设置 → 银行账户 导入。", true);
            var pick = new Button { Content = "选择流水文件（可选）…", MinHeight = 40 };
            pick.Click += async (_, _) => {
                var picked = await PickStatementFileAsync();
                if (picked == null) return;
                choice.StatementPath = picked;
                fileName.Text = "已选择：" + Path.GetFileName(picked);
            };
            statementPanel.Children.Add(institution);
            statementPanel.Children.Add(accountType);
            statementPanel.Children.Add(pick);
            statementPanel.Children.Add(fileName);
            body.Children.Add(scotiaHint);
            body.Children.Add(statementPanel);

            void Sync()
            {
                var selected = (options.SelectedItem as RadioButton)?.Tag as string ?? "";
                scotiaHint.Visibility = selected == "scotia" ? Visibility.Visible : Visibility.Collapsed;
                statementPanel.Visibility = selected == "statements" ? Visibility.Visible : Visibility.Collapsed;
                if (dialogRef != null) dialogRef.IsPrimaryButtonEnabled = canContinue!();
            }
            canContinue = () => (options.SelectedItem as RadioButton)?.Tag is string id && (id != "statements" || institution.Text.Trim().Length > 0);
            options.SelectionChanged += (_, _) => Sync();
            institution.TextChanged += (_, _) => Sync();
            save = () => {
                choice.BankMode = (options.SelectedItem as RadioButton)?.Tag as string ?? "";
                choice.Institution = institution.Text.Trim();
                choice.StatementType = (accountType.SelectedItem as RadioButton)?.Tag as string ?? "bank_account";
            };
            DispatcherQueue.TryEnqueue(Sync);
        } else if (step == 1) {
            title = $"{prefix} · 2/3 购物平台";
            body.Children.Add(Paragraph("你在哪些平台购物或点外卖？现在只需打勾，第一次更新某个平台时再登录它。"));
            var boxes = BillPlatformPreferences.All.Select(id => new CheckBox {
                Content = LedgerLabels.Platform(id), Tag = id, IsChecked = choice.Platforms.Contains(id), MinHeight = 40, Foreground = text,
            }).ToList();
            var list = new StackPanel { Spacing = 4 };
            foreach (var box in boxes) list.Children.Add(box);
            body.Children.Add(new ScrollViewer { Content = list, MaxHeight = Math.Max(120, Math.Min(300, layout.ActualHeight - 260)), VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
            body.Children.Add(Paragraph("以后可以在 设置 → 默认更新平台 修改。", true));
            save = () => choice.Platforms = BillPlatformPreferences.Normalize(boxes.Where(box => box.IsChecked == true).Select(box => (string)box.Tag));
        } else {
            title = $"{prefix} · 3/3 起始日期";
            body.Children.Add(Paragraph("从哪天开始记账？更早的订单和流水不会被采集。"));
            var picker = new CalendarDatePicker {
                Date = choice.Floor, MaxDate = DateTimeOffset.Now, MinDate = new DateTimeOffset(2000, 1, 1, 0, 0, 0, TimeSpan.Zero),
                DateFormat = "{year.full}-{month.integer(2)}-{day.integer(2)}", Header = "起始日期", MinHeight = 44,
            };
            body.Children.Add(picker);
            body.Children.Add(Paragraph("默认是三个月前。日期越早，第一次采集越久。", true));
            canContinue = () => picker.Date != null;
            picker.DateChanged += (_, _) => { if (dialogRef != null) dialogRef.IsPrimaryButtonEnabled = canContinue(); };
            save = () => { if (picker.Date is DateTimeOffset date) choice.Floor = date; };
        }

        var dialog = new ContentDialog {
            XamlRoot = layout.XamlRoot, Title = title, Content = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto },
            PrimaryButtonText = primary, SecondaryButtonText = step > 0 ? "上一步" : "", CloseButtonText = manual ? "取消" : "跳过引导",
            DefaultButton = ContentDialogButton.Primary, MaxHeight = Math.Max(240, Math.Min(620, layout.ActualHeight - 40)),
        };
        ConfigureLedgerDialog(dialog);
        dialogRef = dialog;
        if (canContinue != null) dialog.IsPrimaryButtonEnabled = canContinue();
        dialog.Tag = $"onboarding-step-{step}";
        commit = save;
        return dialog;
    }

    async Task<string?> PickStatementFileAsync()
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.Downloads };
        foreach (var type in new[] { ".ofx", ".qfx", ".qbo", ".csv" }) picker.FileTypeFilter.Add(type);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        var file = await picker.PickSingleFileAsync();
        return file?.Path;
    }

    static string StatementAccountId(string institution, string statementType)
    {
        var slug = Regex.Replace(institution.ToLowerInvariant(), "[^a-z0-9]+", "_").Trim('_');
        if (slug.Length == 0) slug = "bank";
        if (slug.Length > 40) slug = slug[..40];
        return slug + (statementType == "credit_card" ? "_credit_card" : "_account");
    }

    static string ReadStatementText(string path)
    {
        var info = new FileInfo(path);
        if (info.Length > MaxStatementBytes) throw new InvalidOperationException("流水文件超过 5 MB。");
        var bytes = File.ReadAllBytes(path);
        var offset = bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF ? 3 : 0;
        return new UTF8Encoding(false, false).GetString(bytes, offset, bytes.Length - offset);
    }

    async Task<JsonElement> ImportStatementAsync(string path, string institution, string statementType)
    {
        return await backend!.PostJsonAsync("/api/bank-statements/import", new {
            account_id = StatementAccountId(institution, statementType),
            filename = Path.GetFileName(path),
            content = ReadStatementText(path),
            institution,
            statement_type = statementType,
        });
    }

    async Task SaveOnboardingAsync(OnboardingChoice choice)
    {
        await backend!.PostJsonAsync("/api/setup", new {
            bank_mode = choice.BankMode is "scotia" or "statements" ? choice.BankMode : "later",
            platforms = choice.Platforms,
            // The picker shows the local calendar day; a UTC rendering could fall on the previous day.
            collection_floor = choice.Floor == default ? null : choice.Floor.ToLocalTime().ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
        });
    }

    async Task CompleteOnboardingAsync(OnboardingChoice choice)
    {
        await SaveOnboardingAsync(choice);
        preferences.DefaultBillPlatforms = BillPlatformPreferences.Normalize(choice.Platforms);
        preferences.Save(root);
        await SyncBillPlatformsToWebAsync();
        var message = "首次设置已保存。第一次更新时会提示登录所选平台。";
        if (choice.BankMode == "statements" && choice.StatementPath is { } path) {
            try {
                var summary = await ImportStatementAsync(path, choice.Institution, choice.StatementType);
                message = $"首次设置已保存，已导入 {LedgerLabels.Text(summary, "added", "0")} 笔流水。下一次更新时会完成对账。";
            } catch (Exception error) {
                Log(error);
                message = "首次设置已保存，但流水文件未能导入：" + error.Message + " 可以在 设置 → 银行账户 重新导入。";
            }
        }
        if (pageReady && browser.CoreWebView2 != null) browser.CoreWebView2.Reload();
        await RefreshBankModeAsync();
        NotifyNative(message, message.Contains("未能导入", StringComparison.Ordinal));
    }

    async Task ImportStatementFromSettingsAsync()
    {
        if (onboardingActive || backend == null) return;
        onboardingActive = true;
        try {
            var text = (Brush)layout.Resources["LedgerTextBrush"];
            var institution = new TextBox { Header = "银行名称", PlaceholderText = "例如 RBC、TD、BMO", MaxLength = 40 };
            var accountType = new RadioButtons { Header = "账户类型", MaxColumns = 2 };
            accountType.Items.Add(new RadioButton { Content = "借记 / 储蓄账户", Tag = "bank_account", Foreground = text });
            accountType.Items.Add(new RadioButton { Content = "信用卡", Tag = "credit_card", Foreground = text });
            accountType.SelectedIndex = 0;
            string? path = null;
            var fileName = new TextBlock { Text = "支持 OFX、QFX 和 CSV。同一份文件重复导入不会重复记账。", TextWrapping = TextWrapping.Wrap, Foreground = (Brush)layout.Resources["LedgerSecondaryTextBrush"] };
            var pick = new Button { Content = "选择流水文件…", MinHeight = 40 };
            var body = new StackPanel { Spacing = 12, MaxWidth = 440 };
            body.Children.Add(institution); body.Children.Add(accountType); body.Children.Add(pick); body.Children.Add(fileName);
            var dialog = new ContentDialog {
                XamlRoot = layout.XamlRoot, Title = "导入银行流水", Content = body,
                PrimaryButtonText = "导入", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary, IsPrimaryButtonEnabled = false,
            };
            ConfigureLedgerDialog(dialog);
            void Sync() => dialog.IsPrimaryButtonEnabled = path != null && institution.Text.Trim().Length > 0;
            institution.TextChanged += (_, _) => Sync();
            pick.Click += async (_, _) => {
                var picked = await PickStatementFileAsync();
                if (picked == null) return;
                path = picked; fileName.Text = "已选择：" + Path.GetFileName(picked); Sync();
            };
            JsonElement? summary = null;
            dialog.PrimaryButtonClick += async (_, args) => {
                var deferral = args.GetDeferral();
                try {
                    summary = await ImportStatementAsync(path!, institution.Text.Trim(), (accountType.SelectedItem as RadioButton)?.Tag as string ?? "bank_account");
                } catch (Exception error) {
                    args.Cancel = true; fileName.Text = "未能导入：" + error.Message;
                } finally { deferral.Complete(); }
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary || summary is not JsonElement result) return;
            var added = LedgerLabels.Text(result, "added", "0");
            var present = LedgerLabels.Text(result, "already_present", "0");
            try {
                await backend.PostJsonAsync("/api/jobs/refresh", new { mode = "local" });
                NotifyNative($"已导入 {added} 笔新流水（{present} 笔已存在），正在重建账本并对账。");
            } catch (Exception error) {
                NotifyNative($"已导入 {added} 笔新流水（{present} 笔已存在）。对账未能自动开始：{error.Message} 请稍后在 更新 页重建本地报表。", true);
            }
            await RefreshBankModeAsync();
        } catch (Exception error) { Log(error); NotifyNative("暂时无法导入流水：" + error.Message, true); }
        finally { onboardingActive = false; }
    }
}
