using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingOptimization;
    async Task VerifyOptimizationAsync()
    {
        if (verifyingOptimization || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_OPTIMIZATION") != "1") return;
        verifyingOptimization = true;
        var checks = new List<string>();
        void Check(bool ok, string name) { if (!ok) throw new Exception(name); checks.Add(name); }
        try {
            for (var i = 0; i < 100 && await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true") != "true"; i++) await Task.Delay(100);
            await OpenOrdersFromWebAsync("all");
            Check(listValid && orderPage!.Rows.Count > 0, "orders_loaded");
            if (orderPage!.TotalCount > OrderFilter.PageSize) {
                orderFilter = orderFilter with {Offset=OrderFilter.PageSize}; await LoadOrdersAsync();
            }
            var filter = orderFilter;
            var selected = orderPage.Rows.First(); orderList.SelectedItem = selected;
            orderSearch.Text = "未提交的输入";
            InvalidateNativeLedger(); await RefreshChangedLedgerAsync();
            Check(listValid && orderFilter == filter && !orderFiltersStale, "completion_preserves_filters_and_page");
            Check((orderList.SelectedItem as LedgerOrder)?.OrderPk == selected.OrderPk, "completion_preserves_selection");
            Check(orderSearch.Text == "未提交的输入", "completion_preserves_search_draft");
            await OpenOrderDetailAsync(selected.OrderPk);
            var detailBefore = detailRevision;
            InvalidateNativeLedger(); await RefreshChangedLedgerAsync();
            Check(detailOrderId == selected.OrderPk && detailRevision > detailBefore && orderFullRecord.IsEnabled, "completion_refreshes_same_detail");
            await BackToOrdersAsync();
            Check(listValid && !orderFiltersStale, "return_from_detail_refreshes_stale_filters");
            await OpenFinanceAsync("sources", "all");
            var financeBefore = financeRevision;
            InvalidateNativeLedger(); await RefreshChangedLedgerAsync();
            Check(financeValid && financeRevision > financeBefore && sourceMonthValue == "all", "completion_refreshes_payments_and_keeps_month");
            await OpenFinanceAsync("cash", "all");
            var fixture = new CashWithdrawal {BankTransactionPk=987654321,Date="2026-09-29",Description="测试取现",Value=50,Currency="CAD",Note="fixture"};
            var editor = EditCashNoteAsync(fixture, (_,_) => throw new Exception("Verification must not write a real note"));
            for(var i=0;i<50 && cashNoteDialog?.IsLoaded != true;i++) await Task.Delay(100);
            Check(cashNoteDialog?.IsLoaded == true, "fixture_editor_open");
            cashNoteDraft!.Text = "正在输入的备注";
            financeBefore = financeRevision;
            InvalidateNativeLedger(); await RefreshChangedLedgerAsync();
            Check(pendingLedgerRefresh && financeRevision == financeBefore && cashNoteDraft.Text == "正在输入的备注", "completion_defers_while_editing_note");
            cashNoteDialog!.Hide(); await editor; await RefreshChangedLedgerAsync();
            Check(!pendingLedgerRefresh && financeValid && financeRevision > financeBefore && cashMonthValue == "all", "deferred_refresh_resumes_after_editor");
            await NavigateAsync("native-settings");
            InvalidateNativeLedger(); await RefreshChangedLedgerAsync();
            Check(pendingLedgerRefresh && currentRoute == "native-settings", "completion_does_not_steal_navigation");
            await NavigateAsync("orders"); await RefreshChangedLedgerAsync();
            Check(listValid && !orderFiltersStale && !pendingLedgerRefresh, "returning_page_receives_latest_filters");
            await NavigateAsync("native-maintenance");
            Check(nativeCollectionSummary.Text.Contains("秒") && nativeCollectionDetails.Children.Count > 0, "maintenance_shows_platform_outcomes");
            await CaptureNativePageAsync("maintenance", "optimization");
            File.WriteAllText(Path.Combine(root,"verification/optimization-runtime.json"),JsonSerializer.Serialize(new {status="passed",checks,at=DateTimeOffset.Now}));
        } catch(Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root,"verification/optimization-runtime.json"),JsonSerializer.Serialize(new {status="failed",checks,error=error.ToString(),at=DateTimeOffset.Now}));
        } finally {
            cashNoteSaving=false; cashNoteDialog?.Hide(); pendingLedgerRefresh=false;
            await NavigateAsync("overview"); WriteState("ready");
        }
    }
}
