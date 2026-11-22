using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingPhaseFour;
    async Task VerifyPhaseFourAsync()
    {
        if (verifyingPhaseFour || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_PHASE4") != "1") return;
        verifyingPhaseFour = true;
        var artifactPrefix = Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_PHASE4_PREFIX") == "native-theme" ? "native-theme-regression" : "phase-four";
        var checks = new List<string>();
        var originalTheme = layout.RequestedTheme; var originalSize = AppWindow.Size;
        var maximized = AppWindow.Presenter is OverlappedPresenter initial && initial.State == OverlappedPresenterState.Maximized;
        void Check(bool condition, string name) { if (!condition) throw new Exception(name); checks.Add(name); }
        async Task WaitFor(Func<Task<bool>> condition) {
            for (var i = 0; i < 100; i++) { if (await condition()) return; await Task.Delay(100); }
            throw new Exception("Timed out waiting for finance UI state.");
        }
        try {
            await WaitFor(async () => await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true") == "true");
            await WaitFor(async () => await browser.CoreWebView2.ExecuteScriptAsync("getComputedStyle(document.querySelector('.sidebar')).display === 'none'") == "true");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('.sidebar').getBoundingClientRect().width === 0 && document.querySelector('.workspace').getBoundingClientRect().left < 1") == "true", "single_navigation_no_web_gutter");
            // The same page keeps its web navigation when it is not embedded.
            Check(await browser.CoreWebView2.ExecuteScriptAsync("(() => {document.documentElement.classList.remove('expense-native'); const visible = getComputedStyle(document.querySelector('.sidebar')).display !== 'none'; document.documentElement.classList.add('expense-native'); return visible;})()") == "true", "standalone_web_navigation_preserved");
            var webCapture = Path.Combine(root, "verification", "phase-four-overview-web.png");
            File.WriteAllBytes(webCapture, Array.Empty<byte>());
            var webFile = await Windows.Storage.StorageFile.GetFileFromPathAsync(webCapture);
            using (var webStream = await webFile.OpenAsync(Windows.Storage.FileAccessMode.ReadWrite))
                await browser.CoreWebView2.CapturePreviewAsync(Microsoft.Web.WebView2.Core.CoreWebView2CapturePreviewImageFormat.Png, webStream);
            await NavigateAsync("sources");
            var webMonth = JsonSerializer.Deserialize<string>(await browser.CoreWebView2.ExecuteScriptAsync("state.month"));
            Check(financeValid && sourcePage != null && sourceMonthValue == webMonth, "payment_initial_month");
            Check(sourceCardGroups.Children.Count > 0 && sourceCategoryGroups.Children.Count > 0, "payment_breakdowns_rendered");
            var api = await backend!.LedgerAsync<JsonElement>(FinancePaths.Overview(sourceMonthValue));
            Check(sourcePage!.Summary.Total == LedgerLabels.Number(api.GetProperty("summary"), "total"), "payment_uses_backend_total");
            layout.RequestedTheme = ElementTheme.Light; await Task.Delay(200);
            await CaptureNativePageAsync("payments-light", artifactPrefix);
            layout.RequestedTheme = ElementTheme.Dark; await Task.Delay(200);
            await CaptureNativePageAsync("payments-dark", artifactPrefix);
            var obsolete = OpenFinanceAsync("sources", "1900-01");
            var newest = OpenFinanceAsync("sources", "all"); await Task.WhenAll(obsolete,newest);
            Check(financeValid && sourceMonthValue == "all" && sourcePage.SelectedMonth == "all", "latest_finance_request_wins");
            await OpenFinanceAsync("sources", "1900-01");
            Check(financeValid && sourcePage.Summary.OrderCount == 0, "empty_payment_month");
            await OpenFinanceAsync("cash", "all");
            Check(financeValid && cashPage != null && cashList.Items.Count == cashPage.Rows.Count, "cash_list_loaded");
            Check(financeDescription.Text.Contains("不计入"), "cash_not_expense_explained");
            await OpenFinanceAsync("cash", "1900-01");
            Check(financeValid && cashPage!.Summary.WithdrawalCount == 0 && financeEmpty.Visibility == Visibility.Visible, "empty_cash_month");
            var filters = JsonSerializer.Deserialize<JsonElement>("""{"months":["2026-05"],"cash_months":["2026-08"]}""");
            SetFinanceOptions(filters, "cash", "2026-08");
            Check(SelectedFilter(financeMonth) == "2026-08", "cash_only_month_selectable");
            await NavigateAsync("overview");
            await browser.CoreWebView2.ExecuteScriptAsync("switchView('cash')");
            await WaitFor(() => Task.FromResult(currentRoute == "cash" && financeValid && !financeBusy));
            Check(cashMonthValue == webMonth, "web_cash_preserves_month");
            var load = LoadFinanceAsync(); await NavigateAsync("native-settings"); await load;
            Check(!financeBusy && financePanel.Visibility == Visibility.Collapsed && currentRoute == "native-settings", "leaving_cancels_finance");
            await NavigateAsync("overview"); await browser.CoreWebView2.ExecuteScriptAsync("switchView('sources')");
            await WaitFor(() => Task.FromResult(currentRoute == "sources" && financeValid && !financeBusy));
            Check(sourceMonthValue == webMonth, "web_payment_preserves_month");
            var beforeRefund = financeRevision;
            await browser.CoreWebView2.ExecuteScriptAsync("window.chrome.webview.postMessage({type:'order-updated',orderPk:1})");
            await WaitFor(() => Task.FromResult(financeRevision > beforeRefund && financeValid && !financeBusy));
            Check(currentRoute == "sources" && sourceMonthValue == webMonth, "refund_updates_active_payment_totals");
            await NavigateAsync("orders");
            Check(listValid && currentRoute == "orders" && orderList.Items.Count > 0, "native_orders_still_load");
            await NavigateAsync("operations");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("state.view") == "\"operations\"", "web_updates_still_route");
            await OpenFinanceAsync("sources", "all");
            if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Restore();
            var scale = WindowScale(); AppWindow.Resize(new SizeInt32((int)(760*scale),(int)(760*scale)));
            await Task.Delay(300);
            Check(Grid.GetRow(sourceCategoriesBorder) == (sourcesBody.ActualWidth < 850 ? 2 : 1) && sourcesBody.ActualHeight > 150, "narrow_payment_stacks");
            await CaptureNativePageAsync("payments-narrow", artifactPrefix);
            await OpenFinanceAsync("cash", "all");
            var fixture = new CashWithdrawal {BankTransactionPk = 987654321, Date = "2026-09-01", Description = "ATM 取现 · 测试记录", AccountLabel = "测试账户", Value = 80, Currency = "CAD", Note = "原备注"};
            cashList.ItemsSource = new[] {fixture}; financeEmpty.Visibility = Visibility.Collapsed;
            financeSummary.Text = "本期取现 80.00 CAD"; financeCount.Text = "测试记录 · 不写入真实账本";
            await Task.Delay(150); await CaptureNativePageAsync("cash-fixture-narrow", artifactPrefix);
            var editor = EditCashNoteAsync(fixture, (_, _) => Task.FromResult(new CashNoteResult()));
            await WaitFor(() => Task.FromResult(cashNoteDialog?.IsLoaded == true));
            cashNoteDraft!.Text = "  理发   现金  ";
            var saved = await CommitCashNoteAsync(fixture, (_, _) => throw new InvalidOperationException("测试保存失败，请重试。"));
            Check(!saved && cashNoteError!.IsOpen && cashNoteDraft.Text.Contains("理发") && fixture.Note == "原备注", "save_error_keeps_draft");
            await CaptureNativePageAsync("note-error", artifactPrefix, cashNoteDialog);
            saved = await CommitCashNoteAsync(fixture, (_, _) => throw new TimeoutException("测试超时"));
            Check(!saved && uncertainCashNotes.ContainsKey(fixture.BankTransactionPk) && !cashNoteDraft.IsEnabled && cashNoteError!.ActionButton != null, "uncertain_save_protects_draft");
            await CaptureNativePageAsync("note-unconfirmed", artifactPrefix, cashNoteDialog);
            cashNoteDialog!.Hide(); await editor;
            editor = EditCashNoteAsync(fixture, (_, _) => Task.FromResult(new CashNoteResult()));
            await WaitFor(() => Task.FromResult(cashNoteDialog?.IsLoaded == true));
            Check(cashNoteDraft!.Text.Contains("理发") && !cashNoteDraft.IsEnabled, "uncertain_draft_survives_cancel_and_reopen");
            Check(!await VerifyPendingCashNoteAsync(fixture, () => Task.FromResult<string?>("原备注")) && !cashNoteDraft.IsEnabled, "unconfirmed_read_keeps_draft_protected");
            Check(await VerifyPendingCashNoteAsync(fixture, () => Task.FromResult<string?>("理发 现金")) && !uncertainCashNotes.ContainsKey(fixture.BankTransactionPk), "confirmed_read_recovers_save");
            var completion = new TaskCompletionSource<CashNoteResult>();
            var saving = CommitCashNoteAsync(fixture, (_, _) => completion.Task);
            Check(cashNoteSaving && !cashNoteDialog!.IsPrimaryButtonEnabled && !cashNoteDraft.IsEnabled, "save_disables_duplicate_submit");
            await RequestCloseAsync();
            Check(!exiting && cashNoteDialog != null, "editor_blocks_window_exit");
            cashNoteDialog!.Hide(); await Task.Delay(100);
            Check(!editor.IsCompleted, "pending_save_prevents_dialog_close");
            completion.SetResult(new CashNoteResult {BankTransactionPk = fixture.BankTransactionPk, Note = "理发 现金"});
            Check(await saving && fixture.Note == "理发 现金", "canonical_note_applied");
            cashNoteDialog.Hide(); await editor;
            Check(cashNoteDialog == null && !cashNoteSaving, "editor_cleans_up");
            var cancelEditor = EditCashNoteAsync(fixture, (_, _) => throw new Exception("Cancelled editor must not save"));
            await WaitFor(() => Task.FromResult(cashNoteDialog?.IsLoaded == true));
            cashNoteDraft!.Text = "放弃修改"; cashNoteDialog!.Hide(); await cancelEditor;
            Check(fixture.Note == "理发 现金", "cancel_keeps_saved_note");
            await LoadFinanceAsync();
            Check(financeValid && !cashList.Items.OfType<CashWithdrawal>().Any(x => x.BankTransactionPk == fixture.BankTransactionPk), "fixture_removed_after_reload");
            File.WriteAllText(Path.Combine(root,"verification",artifactPrefix + "-runtime.json"), JsonSerializer.Serialize(new {status="passed",checks,at=DateTimeOffset.Now}));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root,"verification",artifactPrefix + "-runtime.json"), JsonSerializer.Serialize(new {status="failed",checks,error=error.ToString(),at=DateTimeOffset.Now}));
        } finally {
            cashNoteSaving = false; cashNoteDialog?.Hide(); CancelFinanceRequest(); layout.RequestedTheme = originalTheme;
            AppWindow.Resize(originalSize); if (maximized && AppWindow.Presenter is OverlappedPresenter presenter) presenter.Maximize();
            await NavigateAsync("overview"); WriteState("ready");
        }
    }
}
