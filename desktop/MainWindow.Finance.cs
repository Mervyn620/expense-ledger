using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    CancellationTokenSource? financeRequest;
    int financeRevision;
    bool changingFinanceMonth, financeBusy, financeValid;
    string sourceMonthValue = "latest", cashMonthValue = "latest";
    FinanceOverview? sourcePage;
    ManualWechatPage? manualWechatPage;
    CashPage? cashPage;
    ContentDialog? cashNoteDialog;
    TextBox? cashNoteDraft;
    InfoBar? cashNoteError;
    bool cashNoteSaving, cashNoteSaved;
    readonly Dictionary<long,string> uncertainCashNotes = new();

    void InitializeFinance()
    {
        financeMonth.SelectionChanged += async (_, _) => {
            if (changingFinanceMonth || currentRoute is not ("cash" or "sources")) return;
            var month = SelectedFilter(financeMonth, "all");
            SetFinanceMonth(currentRoute, month); await LoadFinanceAsync();
        };
        financeRefresh.Click += async (_, _) => await LoadFinanceAsync();
        addManualWechat.Click += async (_, _) => await OpenManualWechatEditorAsync();
        cashList.ItemClick += async (_, e) => {
            if (financeValid && !financeBusy && e.ClickedItem is CashWithdrawal row) {
                cashList.SelectedItem = row; await EditCashNoteAsync(row);
            }
        };
        sourcesBody.SizeChanged += (_, _) => {
            var narrow = sourcesBody.ActualWidth < 850;
            sourcesColumns.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
            Grid.SetColumn(sourceCategoriesBorder, narrow ? 0 : 1);
            Grid.SetRow(sourceCategoriesBorder, narrow ? 2 : 1);
        };
    }
    void SetFinanceMonth(string route, string month)
    {
        if (route == "cash") cashMonthValue = month; else sourceMonthValue = month;
    }
    string FinanceMonthValue => currentRoute == "cash" ? cashMonthValue : sourceMonthValue;
    void CancelFinanceRequest()
    {
        financeRevision++; financeRequest?.Cancel(); financeRequest?.Dispose(); financeRequest = null;
        financeBusy = false; financeLoading.IsActive = false;
    }
    async Task OpenFinanceAsync(string route, string? month = null)
    {
        if (route is not ("cash" or "sources")) return;
        CancelOrderRequests(); CancelFinanceRequest();
        currentRoute = route;
        if (!string.IsNullOrWhiteSpace(month)) SetFinanceMonth(route, month);
        ShowRoute(); await LoadFinanceAsync();
    }
    void SetFinanceOptions(JsonElement filters, string route, string selected)
    {
        IEnumerable<string> Values(string key) => LedgerLabels.Rows(filters, key)
            .Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!).Where(x => !string.IsNullOrWhiteSpace(x));
        var months = Values("months");
        if (route == "cash") months = months.Concat(Values("cash_months"));
        else if (manualWechatPage != null) months = months.Concat(manualWechatPage.Months);
        if (selected != "all") months = months.Append(selected);
        changingFinanceMonth = true;
        try {
            FillFilter(financeMonth, "全部时间", "all", months.Distinct().OrderDescending().Select(x => (x, x)));
            ChooseFilter(financeMonth, selected);
        } finally { changingFinanceMonth = false; }
    }
    async Task LoadFinanceAsync()
    {
        if (currentRoute is not ("cash" or "sources")) return;
        CancelFinanceRequest(); var revision = financeRevision;
        var source = financeRequest = new CancellationTokenSource(); var token = source.Token;
        var route = currentRoute; var month = FinanceMonthValue;
        financeValid = false; financeBusy = true; financeLoading.IsActive = true;
        cashList.IsEnabled = sourcesBody.IsEnabled = false;
        financeNotice.IsOpen = false; financeEmpty.Visibility = Visibility.Collapsed;
        financeCount.Text = "正在读取…";
        financeTitle.Text = route == "cash" ? "现金" : "付款";
        financeEyebrow.Text = route == "cash" ? "取现记录" : "资金来源";
        financeSummaryLabel.Text = route == "cash" ? "本期取现" : "本期支出";
        financeDescription.Text = route == "cash"
            ? "只含 ATM 取现；备注仅作备忘，不计入总览支出。"
            : "按付款归属与支出分类统计";
        SetFinanceMetrics(route == "cash" ? "取现" : "订单", "—", "", route == "cash" ? "已备注" : "待处理", "—", "");
        sourcesBody.Visibility = route == "sources" ? Visibility.Visible : Visibility.Collapsed;
        cashBody.Visibility = route == "cash" ? Visibility.Visible : Visibility.Collapsed;
        financeSummary.Text = "正在读取本期记录…";
        try {
            if (backend?.Url == null) throw new HttpRequestException();
            var filters = await backend.LedgerAsync<JsonElement>("/api/filters", token);
            if (route == "cash") {
                var page = await backend.LedgerAsync<CashPage>(FinancePaths.Cash(month), token);
                if (revision != financeRevision || route != currentRoute) return;
                foreach (var item in page.Rows) {
                    if (uncertainCashNotes.TryGetValue(item.BankTransactionPk, out var pending) && NormalizeCashNote(pending) == item.Note)
                        uncertainCashNotes.Remove(item.BankTransactionPk);
                }
                cashPage = page; SetFinanceMonth(route, page.SelectedMonth); SetFinanceOptions(filters, route, page.SelectedMonth);
                cashList.ItemsSource = page.Rows;
                financeSummary.Text = LedgerLabels.Price(page.Summary.Total, "CAD");
                SetFinanceMetrics("取现", $"{page.Summary.WithdrawalCount} 笔", "ATM 取现流水",
                    "已备注", $"{page.Summary.NotedCount} 笔", page.Summary.WithdrawalCount > page.Summary.NotedCount
                        ? $"还有 {page.Summary.WithdrawalCount - page.Summary.NotedCount} 笔未写用途" : "用途都已备注");
                financeCount.Text = $"共 {page.Summary.WithdrawalCount} 笔 · 已备注 {page.Summary.NotedCount} 笔";
                if (page.Rows.Count < page.Summary.WithdrawalCount) financeCount.Text += $" · 当前显示最近 {page.Rows.Count} 笔，请按月份缩小范围";
                financeEmpty.Text = "本期未检测到 ATM 取现。完成 Scotia 对账后会自动显示在这里。";
                financeEmpty.Visibility = page.Rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            } else {
                var page = await backend.LedgerAsync<FinanceOverview>(FinancePaths.Overview(month), token);
                var manual = await backend.LedgerAsync<ManualWechatPage>(FinancePaths.ManualWechat(page.SelectedMonth), token);
                if (revision != financeRevision || route != currentRoute) return;
                sourcePage = page; manualWechatPage = manual;
                SetFinanceMonth(route, page.SelectedMonth); SetFinanceOptions(filters, route, page.SelectedMonth);
                financeSummary.Text = LedgerLabels.Price(page.Summary.Total, "CAD");
                if (page.Summary.ChangePercent is decimal change)
                    financeDescription.Text = (change < 0 ? "↘ " : "↗ ") + Math.Abs(change).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + "% 较上月";
                SetFinanceMetrics("订单", $"{page.Summary.OrderCount} 笔", $"{page.Summary.ItemCount} 条明细",
                    "待处理", $"{page.Summary.ReviewCount} 笔", page.Summary.ReviewCount == 0 ? "付款来源完整" : "需要确认付款来源");
                financeCount.Text = $"共 {page.Summary.OrderCount} 笔订单 · {page.Summary.ReviewCount} 笔待核对 · {manual.TotalCount} 笔手填微信付款";
                RenderFinanceGroups(sourceCardGroups, page.Cards, true);
                RenderFinanceGroups(sourceCategoryGroups, page.Categories, false);
                RenderManualWechat(manual);
            }
            financeValid = true;
        } catch (Exception error) {
            if (revision != financeRevision || token.IsCancellationRequested || route != currentRoute) return;
            financeCount.Text = "读取未完成 · 先前结果暂不可操作";
            financeSummary.Text = "—"; financeDescription.Text = "本期记录暂时无法读取";
            financeNotice.Title = "暂时无法读取";
            financeNotice.Message = error is OperationCanceledException ? "读取超时，请重试。" : error is InvalidOperationException ? error.Message : "无法连接账本服务，请重试；持续失败时可到维护中心查看。";
            var retryButton = new Button {Content = "重试"}; retryButton.Click += async (_, _) => await LoadFinanceAsync();
            financeNotice.ActionButton = retryButton; financeNotice.Severity = InfoBarSeverity.Error; financeNotice.IsOpen = true;
        } finally {
            if (revision == financeRevision) {
                financeBusy = false; financeLoading.IsActive = false;
                cashList.IsEnabled = sourcesBody.IsEnabled = financeValid; financeRequest = null;
            }
            source.Dispose();
        }
    }
    void SetFinanceMetrics(string twoLabel, string twoValue, string twoCaption, string threeLabel, string threeValue, string threeCaption)
    {
        financeMetricTwoLabel.Text = twoLabel; financeMetricTwoValue.Text = twoValue; financeMetricTwoCaption.Text = twoCaption;
        financeMetricThreeLabel.Text = threeLabel; financeMetricThreeValue.Text = threeValue; financeMetricThreeCaption.Text = threeCaption;
    }
    // Same composition as the Web payment page: card rows with an icon tile, and category rows with a share bar.
    void RenderFinanceGroups(StackPanel panel, IEnumerable<FinanceGroup> groups, bool cards)
    {
        panel.Children.Clear();
        var list = groups.ToList();
        var sum = list.Sum(group => Math.Max(0, group.Total));
        var text = (Brush)layout.Resources["LedgerTextBrush"];
        var soft = (Brush)layout.Resources["LedgerSecondaryTextBrush"];
        var accent = (Brush)layout.Resources["LedgerAccentBrush"];
        var accentSoft = (Brush)layout.Resources["LedgerAccentSoftBrush"];
        var edge = (Brush)layout.Resources["LedgerEdgeBrush"];
        for (var index = 0; index < list.Count; index++) {
            var group = list[index];
            var name = cards ? LedgerLabels.Card(group.Label) : LedgerLabels.Category(group.Key);
            if (cards) {
                var row = new Grid {ColumnSpacing = 14, Padding = new Thickness(0, 14, 0, 14),
                    BorderBrush = edge, BorderThickness = new Thickness(0, 0, 0, index == list.Count - 1 ? 0 : 1)};
                row.ColumnDefinitions.Add(new ColumnDefinition {Width = GridLength.Auto});
                row.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(1, GridUnitType.Star)});
                row.ColumnDefinitions.Add(new ColumnDefinition {Width = GridLength.Auto});
                row.Children.Add(new Border {Width = 40, Height = 40, CornerRadius = new CornerRadius(10), Background = accentSoft,
                    VerticalAlignment = VerticalAlignment.Center,
                    Child = new FontIcon {Glyph = "\uE8C7", FontSize = 16, Foreground = accent}});
                var label = new StackPanel {Spacing = 3, VerticalAlignment = VerticalAlignment.Center};
                label.Children.Add(new TextBlock {Text = name, FontWeight = FontWeights.Bold, Foreground = text, TextTrimming = TextTrimming.CharacterEllipsis});
                label.Children.Add(new TextBlock {Text = group.Orders + " 笔订单", FontSize = 12, Foreground = soft});
                Grid.SetColumn(label, 1); row.Children.Add(label);
                var total = new TextBlock {Text = group.Amount, FontWeight = FontWeights.Bold, Foreground = text, VerticalAlignment = VerticalAlignment.Center};
                Grid.SetColumn(total, 2); row.Children.Add(total);
                AutomationProperties.SetName(row, $"{name}，{group.Orders} 笔订单，{group.Amount}");
                panel.Children.Add(row);
            } else {
                var share = sum > 0 ? (double)(Math.Max(0, group.Total) / sum) : 0;
                var row = new StackPanel {Spacing = 8};
                var line = new Grid {ColumnSpacing = 12};
                line.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(1, GridUnitType.Star)});
                line.ColumnDefinitions.Add(new ColumnDefinition {Width = GridLength.Auto});
                line.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(52)});
                line.Children.Add(new TextBlock {Text = name, Foreground = text, TextTrimming = TextTrimming.CharacterEllipsis});
                var amount = new TextBlock {Text = group.Amount, FontWeight = FontWeights.Bold, Foreground = text};
                Grid.SetColumn(amount, 1); line.Children.Add(amount);
                var percent = new TextBlock {Text = (share * 100).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + "%",
                    FontSize = 12, Foreground = soft, HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Center};
                Grid.SetColumn(percent, 2); line.Children.Add(percent);
                row.Children.Add(line);
                var bar = new Grid {Height = 6};
                bar.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(Math.Max(share, 0.001), GridUnitType.Star)});
                bar.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(Math.Max(1 - share, 0.001), GridUnitType.Star)});
                var track = new Border {CornerRadius = new CornerRadius(3), Background = accentSoft};
                Grid.SetColumnSpan(track, 2); bar.Children.Add(track);
                bar.Children.Add(new Border {CornerRadius = new CornerRadius(3), Background = accent});
                row.Children.Add(bar);
                AutomationProperties.SetName(row, $"{name}，{group.Amount}，占 {percent.Text}");
                panel.Children.Add(row);
            }
        }
        if (panel.Children.Count == 0) panel.Children.Add(new TextBlock {Text = "本期暂无记录。", Foreground = soft});
    }
    async Task<bool> CommitCashNoteAsync(CashWithdrawal row, Func<long,string,Task<CashNoteResult>> save)
    {
        if (cashNoteSaving || cashNoteDialog == null || cashNoteDraft == null || cashNoteError == null) return false;
        cashNoteSaving = true; cashNoteSaved = false;
        cashNoteDialog.IsPrimaryButtonEnabled = false; cashNoteDraft.IsEnabled = false; cashNoteError.IsOpen = false;
        try {
            var draft = uncertainCashNotes.TryGetValue(row.BankTransactionPk, out var pending) ? pending : cashNoteDraft.Text;
            var saved = await save(row.BankTransactionPk, draft);
            // Only the server-confirmed, normalized note becomes the displayed value.
            row.Note = saved.Note; uncertainCashNotes.Remove(row.BankTransactionPk); cashNoteSaved = true; return true;
        } catch (Exception error) {
            if (error is TimeoutException or OperationCanceledException or HttpRequestException or JsonException or IOException)
                uncertainCashNotes.TryAdd(row.BankTransactionPk, cashNoteDraft.Text);
            cashNoteError.Title = "备注未能确认保存";
            cashNoteError.Message = error is InvalidOperationException or TimeoutException ? error.Message : error is OperationCanceledException ? "保存结果尚未确认，请稍后刷新核实。" : "暂时无法保存，请重试。你输入的内容仍保留在这里。";
            cashNoteError.Severity = InfoBarSeverity.Error; cashNoteError.IsOpen = true; return false;
        } finally {
            cashNoteSaving = false;
            ConfigureCashNotePendingUI(row);
        }
    }
    static string NormalizeCashNote(string value) => Regex.Replace(value, @"\s+", " ").Trim();
    void ConfigureCashNotePendingUI(CashWithdrawal row)
    {
        if (cashNoteDialog == null || cashNoteDraft == null || cashNoteError == null) return;
        var uncertain = uncertainCashNotes.ContainsKey(row.BankTransactionPk);
        cashNoteDialog.IsPrimaryButtonEnabled = !cashNoteSaving;
        cashNoteDialog.PrimaryButtonText = uncertain ? "重新保存原备注" : "保存";
        cashNoteDraft.IsEnabled = !cashNoteSaving && !uncertain;
        cashNoteError.ActionButton = null;
        if (uncertain) {
            cashNoteError.IsOpen = true; cashNoteError.Title = "保存结果尚未确认";
            cashNoteError.Message = "可核实保存结果，或重新保存同一条备注。确认前保留原输入，避免覆盖仍在处理的更改。";
            var verify = new Button {Content = "核实保存结果", IsEnabled = !cashNoteSaving};
            verify.Click += async (_, _) => { if (await VerifyPendingCashNoteAsync(row)) cashNoteDialog?.Hide(); };
            cashNoteError.ActionButton = verify;
        }
    }
    async Task<bool> VerifyPendingCashNoteAsync(CashWithdrawal row, Func<Task<string?>>? fixtureRead = null)
    {
        if (cashNoteSaving || !uncertainCashNotes.TryGetValue(row.BankTransactionPk, out var draft) || cashNoteDialog == null) return false;
        cashNoteSaving = true; ConfigureCashNotePendingUI(row);
        bool confirmed = false;
        try {
            string? actual;
            if (fixtureRead != null) actual = await fixtureRead();
            else {
                var month = row.Date?.Length >= 7 ? row.Date[..7] : "all";
                var fresh = await backend!.LedgerAsync<CashPage>(FinancePaths.Cash(month, 500));
                actual = fresh.Rows.FirstOrDefault(item => item.BankTransactionPk == row.BankTransactionPk)?.Note;
            }
            if (actual == NormalizeCashNote(draft)) {
                row.Note = actual; uncertainCashNotes.Remove(row.BankTransactionPk); cashNoteSaved = confirmed = true;
            }
        } catch { /* Unconfirmed reads leave the original draft protected. */ }
        finally { cashNoteSaving = false; ConfigureCashNotePendingUI(row); }
        if (!confirmed && cashNoteError != null) cashNoteError.Message = "暂未核实到这条备注。可稍后再核实，或重新保存原备注；取消后也会刷新列表。";
        return confirmed;
    }
    async Task EditCashNoteAsync(CashWithdrawal row, Func<long,string,Task<CashNoteResult>>? fixtureSave = null)
    {
        if (cashNoteDialog != null || backend == null) return;
        cashNoteSaved = false;
        cashNoteDraft = new TextBox {Header = "用途备注", Text = uncertainCashNotes.GetValueOrDefault(row.BankTransactionPk, row.Note ?? ""), MaxLength = 300, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 100};
        cashNoteError = new InfoBar {IsOpen = false, IsClosable = false};
        ConfigureLedgerNotice(cashNoteError);
        var content = new StackPanel {Spacing = 14};
        content.Children.Add(DetailText(row.Metadata + " · " + row.Amount));
        content.Children.Add(cashNoteDraft);
        content.Children.Add(DetailText("最多 300 字。留空并保存可清除备注；备注不计入支出。"));
        content.Children.Add(cashNoteError);
        var dialog = cashNoteDialog = new ContentDialog {XamlRoot = layout.XamlRoot, RequestedTheme = layout.ActualTheme,
            Title = "取现用途备注", Content = content, PrimaryButtonText = "保存", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary};
        ConfigureLedgerDialog(dialog);
        var save = fixtureSave ?? ((long id, string note) => backend.UpdateCashNoteAsync(id, note));
        dialog.PrimaryButtonClick += async (_, args) => {
            var deferral = args.GetDeferral();
            try { args.Cancel = !await CommitCashNoteAsync(row, save); }
            finally { deferral.Complete(); }
        };
        dialog.Closing += (_, args) => { if (cashNoteSaving) args.Cancel = true; };
        ConfigureCashNotePendingUI(row);
        try {
            await dialog.ShowAsync();
            if ((cashNoteSaved || uncertainCashNotes.ContainsKey(row.BankTransactionPk)) && fixtureSave == null && currentRoute == "cash") {
                await LoadFinanceAsync();
                if (financeValid && uncertainCashNotes.ContainsKey(row.BankTransactionPk)) {
                    financeNotice.Title = "备注保存仍待确认";
                    financeNotice.Message = "列表已刷新。再次打开这笔记录可核实结果，原输入仍保留。";
                    financeNotice.ActionButton = null; financeNotice.Severity = InfoBarSeverity.Warning; financeNotice.IsOpen = true;
                } else if (financeValid) {
                    financeNotice.Title = string.IsNullOrEmpty(row.Note) ? "备注已清除" : "备注已保存";
                    financeNotice.Message = "仅更新这笔取现的用途备注。";
                    financeNotice.ActionButton = null; financeNotice.Severity = InfoBarSeverity.Success; financeNotice.IsOpen = true;
                } else financeNotice.Message = cashNoteSaved ? "备注已保存，但列表刷新未完成。可点击重试确认最新记录。" : "保存结果尚未确认，列表刷新也未完成。请重试读取后核实。";
            }
        } finally { cashNoteDialog = null; cashNoteDraft = null; cashNoteError = null; cashNoteSaving = false; }
    }
}
