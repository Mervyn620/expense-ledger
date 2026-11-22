using System;
using System.Globalization;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    ContentDialog? manualWechatDialog;

    void RenderManualWechat(ManualWechatPage page)
    {
        manualWechatRows.Children.Clear();
        manualWechatSummary.Text = "本期手填：" + page.Summary;
        if (page.Rows.Count < page.TotalCount)
            manualWechatSummary.Text += $" · 当前显示最近 {page.Rows.Count} 笔，请按月份缩小范围";
        if (page.Rows.Count == 0) {
            manualWechatRows.Children.Add(DetailText("本期尚无手填微信付款。"));
            return;
        }
        foreach (var item in page.Rows) {
            var row = new Grid {ColumnSpacing = 12, RowSpacing = 6};
            row.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(1, GridUnitType.Star)});
            row.ColumnDefinitions.Add(new ColumnDefinition {Width = GridLength.Auto});
            row.RowDefinitions.Add(new RowDefinition {Height = GridLength.Auto});
            row.RowDefinitions.Add(new RowDefinition {Height = GridLength.Auto});
            var title = DetailText(item.Merchant + " · " + item.Amount, 16);
            title.TextWrapping = TextWrapping.Wrap;
            row.Children.Add(title);
            var edit = new Button {Content = "编辑", MinHeight = 40};
            edit.Click += async (_, _) => await OpenManualWechatEditorAsync(item);
            Grid.SetColumn(edit, 1); row.Children.Add(edit);
            var detail = DetailText(item.PaidOn + " · 手填微信付款" +
                (string.IsNullOrWhiteSpace(item.Note) ? "" : " · " + item.Note));
            detail.TextWrapping = TextWrapping.Wrap; detail.Opacity = 0.75;
            Grid.SetRow(detail, 1); row.Children.Add(detail);
            var remove = new Button {Content = "删除", MinHeight = 40};
            remove.Click += async (_, _) => await DeleteManualWechatAsync(item);
            Grid.SetColumn(remove, 1); Grid.SetRow(remove, 1); row.Children.Add(remove);
            manualWechatRows.Children.Add(row);
        }
    }

    async Task OpenManualWechatEditorAsync(ManualWechatPayment? existing = null)
    {
        if (manualWechatDialog != null || backend?.Url == null) return;
        var paidOn = new DatePicker {Header = "付款日期", Date = existing == null
            ? DateTimeOffset.Now
            : DateTimeOffset.Parse(existing.PaidOn, CultureInfo.InvariantCulture)};
        var merchant = new TextBox {Header = "收款方或用途", Text = existing?.Merchant ?? "",
            MaxLength = 120};
        var amount = new TextBox {Header = "原币金额（最多两位小数）",
            Text = existing == null ? "" : (existing.AmountMinor / 100m).ToString("0.00", CultureInfo.InvariantCulture)};
        var currency = new ComboBox {Header = "付款币种（不换算）", MinHeight = 40};
        currency.Items.Add(new ComboBoxItem {Content = "人民币 CNY", Tag = "CNY"});
        currency.Items.Add(new ComboBoxItem {Content = "加币 CAD", Tag = "CAD"});
        currency.SelectedIndex = existing?.Currency == "CAD" ? 1 : 0;
        var note = new TextBox {Header = "备注（可选）", Text = existing?.Note ?? "",
            MaxLength = 300, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap};
        var error = new InfoBar {IsOpen = false, IsClosable = false};
        ConfigureLedgerNotice(error);
        var content = new StackPanel {Spacing = 12};
        content.Children.Add(DetailText("这笔记录单独保存。人民币和加币分别显示，不做换算，也不改动已有订单。"));
        content.Children.Add(paidOn); content.Children.Add(merchant); content.Children.Add(amount);
        content.Children.Add(currency); content.Children.Add(note); content.Children.Add(error);
        var dialog = manualWechatDialog = new ContentDialog {
            XamlRoot = layout.XamlRoot, RequestedTheme = layout.ActualTheme,
            Title = existing == null ? "添加微信付款" : "编辑微信付款",
            Content = content, PrimaryButtonText = "保存", CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Primary,
            MaxHeight = Math.Max(340, Math.Min(720, layout.ActualHeight - 48))
        };
        ConfigureLedgerDialog(dialog);
        var requestId = Guid.NewGuid().ToString();
        var savedMonth = "";
        dialog.PrimaryButtonClick += async (_, args) => {
            var deferral = args.GetDeferral();
            args.Cancel = true;
            dialog.IsPrimaryButtonEnabled = false;
            error.IsOpen = false;
            try {
                if (!decimal.TryParse(amount.Text.Trim(), NumberStyles.AllowDecimalPoint,
                        CultureInfo.InvariantCulture, out var value) ||
                    value <= 0 || value > 99999999.99m || value * 100 != decimal.Truncate(value * 100))
                    throw new InvalidOperationException("请输入大于零的金额，最多两位小数；小数点请使用英文句点。");
                var payee = merchant.Text.Trim();
                if (payee.Length == 0) throw new InvalidOperationException("请填写收款方或用途。");
                var date = paidOn.Date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
                var draft = new ManualWechatDraft {
                    PaidOn = date, Merchant = payee, Amount = value,
                    Currency = (currency.SelectedItem as ComboBoxItem)?.Tag as string ?? "CNY",
                    Note = note.Text.Trim(), RequestId = existing == null ? requestId : null
                };
                await backend!.SaveManualWechatAsync(existing?.Id, draft);
                savedMonth = date[..7];
                args.Cancel = false;
            } catch (Exception exception) {
                error.Title = "付款尚未确认保存";
                error.Message = exception is InvalidOperationException or TimeoutException
                    ? exception.Message : exception is HttpRequestException or JsonException
                    ? "暂时无法连接账本。请刷新列表核实后重试；原输入仍在这里。"
                    : "保存失败，请检查输入后重试。";
                error.Severity = InfoBarSeverity.Error; error.IsOpen = true;
                dialog.IsPrimaryButtonEnabled = true;
            } finally { deferral.Complete(); }
        };
        try { await dialog.ShowAsync(); }
        finally { manualWechatDialog = null; }
        if (savedMonth.Length == 0 || currentRoute != "sources") return;
        SetFinanceMonth("sources", savedMonth);
        await LoadFinanceAsync();
        if (financeValid) {
            financeNotice.Title = existing == null ? "微信付款已添加" : "微信付款已更新";
            financeNotice.Message = "已按原币保存，人民币与加币分开统计。";
            financeNotice.ActionButton = null;
            financeNotice.Severity = InfoBarSeverity.Success;
            financeNotice.IsOpen = true;
        }
    }

    async Task DeleteManualWechatAsync(ManualWechatPayment item)
    {
        if (manualWechatDialog != null || backend?.Url == null) return;
        var confirmation = new ContentDialog {
            XamlRoot = layout.XamlRoot, RequestedTheme = layout.ActualTheme,
            Title = "删除手填微信付款？",
            Content = DetailText(item.PaidOn + " · " + item.Merchant + " · " + item.Amount),
            PrimaryButtonText = "删除", CloseButtonText = "取消"
        };
        ConfigureLedgerDialog(confirmation);
        manualWechatDialog = confirmation;
        ContentDialogResult choice;
        try { choice = await confirmation.ShowAsync(); }
        finally { manualWechatDialog = null; }
        if (choice != ContentDialogResult.Primary) return;
        try {
            await backend.DeleteManualWechatAsync(item.Id);
            await LoadFinanceAsync();
            if (financeValid) {
                financeNotice.Title = "手填付款已删除";
                financeNotice.Message = "已从手填记录中移除。";
                financeNotice.ActionButton = null;
                financeNotice.Severity = InfoBarSeverity.Success;
                financeNotice.IsOpen = true;
            }
        } catch (Exception) {
            financeNotice.Title = "删除结果尚未确认";
            financeNotice.Message = "请先刷新列表核实；记录仍显示时再重试。";
            financeNotice.ActionButton = null;
            financeNotice.Severity = InfoBarSeverity.Error;
            financeNotice.IsOpen = true;
        }
    }
}
