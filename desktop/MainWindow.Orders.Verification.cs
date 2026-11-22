using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.Graphics;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingPhaseThree;
    async Task VerifyPhaseThreeAsync()
    {
        if (verifyingPhaseThree || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_PHASE3") != "1") return;
        verifyingPhaseThree = true;
        var checks = new List<string>();
        var originalTheme = layout.RequestedTheme;
        var originalSize = AppWindow.Size;
        var maximized = AppWindow.Presenter is OverlappedPresenter initial && initial.State == OverlappedPresenterState.Maximized;
        void Check(bool condition, string name) { if (!condition) throw new Exception(name); checks.Add(name); }
        async Task WaitFor(Func<Task<bool>> condition) {
            for (var i = 0; i < 100; i++) { if (await condition()) return; await Task.Delay(100); }
            throw new Exception("Timed out waiting for Web/native state.");
        }
        try {
            await WaitFor(async () => await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true") == "true");
            await NavigateAsync("orders");
            var initialMonth = JsonSerializer.Deserialize<string>(await browser.CoreWebView2.ExecuteScriptAsync("state.month"));
            Check(orderFilter.Month == initialMonth, "sidebar_initial_month");
            await OpenOrdersFromWebAsync("all");
            Check(listValid && !ordersBusy && orderPage != null, "list_loaded");
            Check(orderPage!.Rows.Count <= 50 && orderList.IsEnabled, "bounded_virtualized_list");
            var allCount = orderPage.TotalCount;
            if (allCount > 50) {
                orderFilter = orderFilter with {Offset = 50}; await LoadOrdersAsync();
                Check(orderFilter.Offset == 50 && ordersPrevious.IsEnabled, "second_page");
                orderFilter = orderFilter with {Offset = int.MaxValue}; await LoadOrdersAsync();
                Check(orderFilter.Offset < allCount && orderPage!.Rows.Count > 0 && !ordersNext.IsEnabled, "out_of_range_page_recovered");
            }
            orderFilter = new(); SetOrderFilters(); await LoadOrdersAsync();
            var first = orderPage!.Rows.FirstOrDefault();
            if (first != null) {
                orderList.SelectedItem = first;
                var detailLoad = OpenOrderDetailAsync(first.OrderPk);
                await NavigateAsync("orders"); await detailLoad;
                Check(orderFullRecord.IsEnabled, "same_navigation_keeps_detail_request");
                Check(detailOrderId == first.OrderPk && orderFullRecord.IsEnabled && orderDetailContent.Children.Count > 3, "detail_loaded");
                await NavigateAsync("native-settings"); await NavigateAsync("orders");
                Check(!listValid && detailOrderId == first.OrderPk && orderFullRecord.IsEnabled, "reentry_invalidates_cached_list");
                await BackToOrdersAsync();
                first = orderPage!.Rows.First(x => x.OrderPk == first.OrderPk);
                orderList.SelectedItem = first; await OpenOrderDetailAsync(first.OrderPk);
                await CaptureNativePageAsync("detail", "phase-three");
                await BackToOrdersAsync();
                Check(ReferenceEquals(orderList.SelectedItem, first) && listValid, "back_preserves_selection");
                orderSearch.Text = first.Title; await ApplyOrderFiltersAsync();
                Check(listValid && orderFilter.Search == first.Title && orderPage!.Rows.Any(x => x.OrderPk == first.OrderPk), "merchant_search");
            }
            var month = orderMonth.Items.OfType<Microsoft.UI.Xaml.Controls.ComboBoxItem>().Select(x => x.Tag as string).FirstOrDefault(x => x != "all");
            if (month != null) {
                await OpenOrdersFromWebAsync(month);
                Check(orderFilter.Month == month && orderPage!.Rows.All(x => x.Date?.StartsWith(month) == true), "month_filter");
            }
            orderFilter = new(Search: "__desktop_verification_no_such_order_72fb7cc__"); SetOrderFilters();
            await LoadOrdersAsync();
            Check(listValid && orderPage!.TotalCount == 0 && ordersEmpty.Visibility == Visibility.Visible && !ordersNext.IsEnabled, "empty_state");
            orderFilter = new(Search: "__obsolete_search__");
            var obsolete = LoadOrdersAsync();
            orderFilter = new(); SetOrderFilters();
            var latest = LoadOrdersAsync(); await Task.WhenAll(obsolete, latest);
            Check(listValid && orderFilter.Search == "" && orderPage!.TotalCount == allCount, "latest_list_request_wins");
            if (first != null) {
                var obsoleteDetail = OpenOrderDetailAsync(first.OrderPk);
                await BackToOrdersAsync(); await obsoleteDetail;
                Check(detailOrderId == null && orderDetailPanel.Visibility == Visibility.Collapsed, "back_cancels_detail");
                await OpenOrderDetailAsync(long.MaxValue);
                Check(ordersNotice.IsOpen && ordersNotice.Message.Contains("不存在") && !orderFullRecord.IsEnabled, "missing_order_recoverable");
                await BackToOrdersAsync();
            }
            layout.RequestedTheme = ElementTheme.Light; await Task.Delay(200);
            await CaptureNativePageAsync("list-light", "phase-three");
            layout.RequestedTheme = ElementTheme.Dark; await Task.Delay(200);
            await CaptureNativePageAsync("list-dark", "phase-three");
            if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Restore();
            var scale = WindowScale(); AppWindow.Resize(new SizeInt32((int)(760 * scale), (int)(760 * scale)));
            await Task.Delay(350);
            Check(layout.ActualWidth <= 850 && orderList.ActualHeight > 120, "narrow_list_keeps_scroll_area");
            await CaptureNativePageAsync("list-narrow", "phase-three");
            if (first != null) {
                await NavigateAsync("overview");
                await browser.CoreWebView2.ExecuteScriptAsync($"openOrder({first.OrderPk})");
                await WaitFor(() => Task.FromResult(currentRoute == "orders" && orderFullRecord.IsEnabled));
                Check(detailOrderId == first.OrderPk, "overview_to_native_detail");
                await OpenFullOrderAsync();
                await WaitFor(async () => pageReady && await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady === true && document.getElementById('detailDrawer').getAttribute('aria-hidden') === 'false' && document.getElementById('drawerMerchant').textContent !== '加载中'") == "true");
                Check(currentRoute == "web-orders", "refund_fallback_no_loop");
                await browser.CoreWebView2.ExecuteScriptAsync("closeDrawer()");
                await WaitFor(() => Task.FromResult(currentRoute == "orders" && orderFullRecord.IsEnabled));
                Check(detailOrderId == first.OrderPk && !listValid, "fallback_returns_refreshed_detail");
                // A delayed completion message from the hidden fallback may refresh data, not navigation.
                await browser.CoreWebView2.ExecuteScriptAsync($"window.chrome.webview.postMessage({{type:'order-updated',orderPk:{first.OrderPk}}})");
                await WaitFor(() => Task.FromResult(detailRequest == null && orderFullRecord.IsEnabled));
                await NavigateAsync("overview");
                await browser.CoreWebView2.ExecuteScriptAsync($"window.chrome.webview.postMessage({{type:'order-updated',orderPk:{first.OrderPk}}})");
                await Task.Delay(100);
                Check(currentRoute == "overview" && !listValid, "delayed_mutation_preserves_navigation");
                await NavigateAsync("orders"); await BackToOrdersAsync();
            }
            await NavigateAsync("overview");
            await browser.CoreWebView2.ExecuteScriptAsync("switchView('orders')");
            await WaitFor(() => Task.FromResult(currentRoute == "orders" && listValid && !ordersBusy));
            var webMonth = JsonSerializer.Deserialize<string>(await browser.CoreWebView2.ExecuteScriptAsync("state.month"));
            Check(orderFilter.Month == webMonth, "overview_list_preserves_month");
            var request = LoadOrdersAsync(); await NavigateAsync("native-settings"); await request;
            Check(currentRoute == "native-settings" && !ordersBusy && ordersPanel.Visibility == Visibility.Collapsed, "leaving_cancels_request");
            // Synthetic detail only: verifies refund/currency/null presentation without ledger writes.
            var fixture = JsonSerializer.Deserialize<JsonElement>("""
                {"order":{"id":1,"merchant":"测试商户 · 退款与空值","total":75.25,"gross_total":100.25,"refund_total":25,"currency":"USD","payment_status":"needs_review","category":null},"items":[{"product_name":"测试商品","quantity":null,"price":null,"currency":"USD"}],"evidence":[],"refunds":[],"bank_matches":[]}
                """);
            currentRoute = "orders"; ShowRoute(); ordersListPanel.Visibility = Visibility.Collapsed; orderDetailPanel.Visibility = Visibility.Visible;
            RenderOrderDetail(fixture);
            Check(LedgerLabels.Amount(fixture.GetProperty("order"), "total", "USD") == "75.25 USD" && LedgerLabels.Number(fixture.GetProperty("order"), "category") == null, "refund_currency_null_values");
            await Task.Delay(150); await CaptureNativePageAsync("refund-fixture-narrow", "phase-three");
            File.WriteAllText(Path.Combine(root,"verification","phase-three-runtime.json"), JsonSerializer.Serialize(new {status="passed", checks, at=DateTimeOffset.Now}));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root,"verification","phase-three-runtime.json"), JsonSerializer.Serialize(new {status="failed", checks, error=error.Message, at=DateTimeOffset.Now}));
        } finally {
            CancelOrderRequests(); detailOrderId = null; layout.RequestedTheme = originalTheme;
            AppWindow.Resize(originalSize);
            if (maximized && AppWindow.Presenter is OverlappedPresenter presenter) presenter.Maximize();
            await NavigateAsync("overview");
            WriteState("ready");
        }
    }
}
