using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    OrderFilter orderFilter = new();
    OrderPage? orderPage;
    CancellationTokenSource? listRequest, detailRequest;
    int listRevision, detailRevision;
    bool changingFilters, filtersLoaded, orderFiltersStale, ordersBusy, listValid;
    long? detailOrderId;

    void InitializeOrders()
    {
        foreach (var control in new[] {orderMonth, orderPlatform, orderCategory, orderCard})
            control.SelectionChanged += async (_, _) => { if (!changingFilters) await ApplyOrderFiltersAsync(); };
        orderSearchButton.Click += async (_, _) => await ApplyOrderFiltersAsync();
        orderSearch.KeyDown += async (_, e) => {
            if (e.Key == VirtualKey.Enter) { e.Handled = true; await ApplyOrderFiltersAsync(); }
        };
        orderClear.Click += async (_, _) => {
            orderFilter = new(); SetOrderFilters(); await LoadOrdersAsync();
        };
        ordersRefresh.Click += async (_, _) => await LoadOrdersAsync(true);
        ordersPrevious.Click += async (_, _) => {
            orderFilter = orderFilter with {Offset = Math.Max(0, orderFilter.Offset - OrderFilter.PageSize)};
            await LoadOrdersAsync();
        };
        ordersNext.Click += async (_, _) => {
            orderFilter = orderFilter with {Offset = orderFilter.Offset + OrderFilter.PageSize};
            await LoadOrdersAsync();
        };
        orderList.ItemClick += async (_, e) => {
            if (!ordersBusy && listValid && e.ClickedItem is LedgerOrder row) {
                orderList.SelectedItem = row; await OpenOrderDetailAsync(row.OrderPk);
            }
        };
        orderBack.Click += async (_, _) => await BackToOrdersAsync();
        orderFullRecord.Click += async (_, _) => await OpenFullOrderAsync();
        ordersPanel.KeyDown += async (_, e) => {
            if (e.Key == VirtualKey.Escape && detailOrderId != null) { e.Handled = true; await BackToOrdersAsync(); }
        };
    }
    static string SelectedFilter(ComboBox control, string fallback = "") => (control.SelectedItem as ComboBoxItem)?.Tag as string ?? fallback;
    async Task ApplyOrderFiltersAsync()
    {
        orderFilter = new(SelectedFilter(orderMonth, "all"), SelectedFilter(orderPlatform), SelectedFilter(orderCategory), SelectedFilter(orderCard), orderSearch.Text.Trim());
        await LoadOrdersAsync();
    }
    static void ChooseFilter(ComboBox control, string value)
    {
        control.SelectedItem = control.Items.OfType<ComboBoxItem>().FirstOrDefault(x => x.Tag as string == value) ?? control.Items[0];
    }
    void SetOrderFilters()
    {
        if (!filtersLoaded) return;
        changingFilters = true;
        try {
            ChooseFilter(orderMonth, orderFilter.Month); ChooseFilter(orderPlatform, orderFilter.Platform);
            ChooseFilter(orderCategory, orderFilter.Category); ChooseFilter(orderCard, orderFilter.CardId);
            orderSearch.Text = orderFilter.Search;
        } finally { changingFilters = false; }
    }
    static void FillFilter(ComboBox control, string all, string value, IEnumerable<(string Key, string Label)> values)
    {
        control.Items.Clear(); control.Items.Add(new ComboBoxItem {Content = Loc.T(all), Tag = value});
        foreach (var item in values.DistinctBy(x => x.Key))
            control.Items.Add(new ComboBoxItem {Content = Loc.T(item.Label), Tag = item.Key});
    }
    void PopulateOrderFilters(JsonElement filters)
    {
        changingFilters = true;
        try {
            static IEnumerable<string> Strings(JsonElement data, string name) => LedgerLabels.Rows(data, name)
                .Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!).Where(x => !string.IsNullOrWhiteSpace(x));
            FillFilter(orderMonth, "全部时间", "all", Strings(filters, "months").Select(x => (x, x)));
            FillFilter(orderPlatform, "全部平台", "", Strings(filters, "platforms").Select(x => (x, LedgerLabels.Platform(x))));
            FillFilter(orderCategory, "全部分类", "", Strings(filters, "categories").Select(x => (x, LedgerLabels.Category(x))));
            FillFilter(orderCard, "全部付款方式", "", LedgerLabels.Rows(filters, "cards")
                .Select(x => (LedgerLabels.Text(x, "card_id", ""), LedgerLabels.Card(LedgerLabels.Text(x, "display_name", "")))));
            filtersLoaded = true; SetOrderFilters();
            orderFilter = orderFilter with {Month = SelectedFilter(orderMonth, "all"), Platform = SelectedFilter(orderPlatform), Category = SelectedFilter(orderCategory), CardId = SelectedFilter(orderCard)};
        } finally { changingFilters = false; }
    }
    void CancelListRequest()
    {
        listRevision++; listRequest?.Cancel(); listRequest?.Dispose(); listRequest = null;
        ordersBusy = false; UpdateOrderLoading();
    }
    void CancelDetailRequest()
    {
        detailRevision++; detailRequest?.Cancel(); detailRequest?.Dispose(); detailRequest = null;
    }
    void CancelOrderRequests() { CancelListRequest(); CancelDetailRequest(); }
    void UpdateOrderLoading()
    {
        ordersLoading.IsActive = ordersBusy;
        orderList.IsEnabled = !ordersBusy && listValid;
        ordersPrevious.IsEnabled = !ordersBusy && listValid && orderFilter.Offset > 0;
        ordersNext.IsEnabled = !ordersBusy && listValid && orderPage != null && orderFilter.Offset + orderPage.Rows.Count < orderPage.TotalCount;
    }
    void OrderError(Exception error, Func<Task> retryAction)
    {
        ordersNotice.Title = "订单暂时无法读取";
        ordersNotice.Message = error is OperationCanceledException ? "读取超时，请重试。" : error is InvalidOperationException ? error.Message : "无法连接账本服务，请重试；持续失败时可到维护中心查看。";
        ordersNotice.Severity = InfoBarSeverity.Error;
        var button = new Button {Content = "重试"};
        button.Click += async (_, _) => await retryAction();
        ordersNotice.ActionButton = button; ordersNotice.IsOpen = true;
    }
    async Task LoadOrdersAsync(bool refreshFilters = false, bool preservePosition = false)
    {
        var selectedId = (orderList.SelectedItem as LedgerOrder)?.OrderPk;
        var scroll = preservePosition ? FindScrollViewer(orderList) : null;
        var offset = scroll?.VerticalOffset;
        CancelListRequest(); CancelDetailRequest(); detailOrderId = null;
        ordersListPanel.Visibility = Visibility.Visible; orderDetailPanel.Visibility = Visibility.Collapsed;
        var revision = listRevision;
        var source = listRequest = new CancellationTokenSource();
        var token = source.Token;
        ordersBusy = true; listValid = false; ordersEmpty.Visibility = Visibility.Collapsed;
        ordersNotice.IsOpen = false; ordersCount.Text = "正在读取订单…"; UpdateOrderLoading();
        try {
            if (backend?.Url == null) throw new HttpRequestException();
            if (!filtersLoaded || orderFiltersStale || refreshFilters) {
                var filters = await backend.LedgerAsync<JsonElement>("/api/filters", token);
                if (revision != listRevision || currentRoute != "orders") return;
                var searchDraft = orderSearch.Text;
                PopulateOrderFilters(filters);
                orderFiltersStale = false;
                if (preservePosition) orderSearch.Text = searchDraft;
            }
            var requested = orderFilter;
            var page = await backend.LedgerAsync<OrderPage>(requested.Path, token);
            if (revision != listRevision || currentRoute != "orders") return;
            // A deleted order or refund can reduce the result set while viewing a later page.
            if (requested.Offset > 0 && requested.Offset >= page.TotalCount) {
                requested = requested with {Offset = Math.Max(0, (page.TotalCount - 1) / OrderFilter.PageSize * OrderFilter.PageSize)};
                page = await backend.LedgerAsync<OrderPage>(requested.Path, token);
                if (revision != listRevision || currentRoute != "orders") return;
            }
            orderFilter = requested; orderPage = page; orderList.ItemsSource = page.Rows;
            listValid = true;
            if (preservePosition) {
                orderList.SelectedItem = page.Rows.FirstOrDefault(x => x.OrderPk == selectedId);
                orderList.UpdateLayout();
                if (offset is double y) scroll?.ChangeView(null, y, null, true);
            }
            ordersEmpty.Text = "暂无符合条件的订单，可更改筛选或重置。";
            ordersEmpty.Visibility = page.Rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            ordersCount.Text = page.TotalCount == 0 ? "共 0 笔" : $"共 {page.TotalCount} 笔 · {requested.Offset + 1}–{requested.Offset + page.Rows.Count} 笔";
        } catch (Exception error) {
            if (revision != listRevision || token.IsCancellationRequested || currentRoute != "orders") return;
            ordersCount.Text = orderPage == null ? "读取未完成" : "读取未完成 · 上次结果暂不可操作";
            OrderError(error, () => LoadOrdersAsync(refreshFilters));
        } finally {
            if (revision == listRevision) { ordersBusy = false; UpdateOrderLoading(); listRequest = null; }
            source.Dispose();
        }
    }
    async Task OpenOrderDetailAsync(long orderId, bool preservePosition = false)
    {
        var previousOffset = orderDetailScroll.VerticalOffset;
        CancelListRequest(); CancelDetailRequest();
        currentRoute = "orders"; ShowRoute(); detailOrderId = orderId;
        ordersListPanel.Visibility = Visibility.Collapsed; orderDetailPanel.Visibility = Visibility.Visible;
        ordersNotice.IsOpen = false; orderFullRecord.IsEnabled = false;
        orderDetailContent.Children.Clear(); orderDetailContent.Children.Add(DetailText("正在读取订单…", 20));
        if (!preservePosition) { orderDetailScroll.ChangeView(null, 0, null, true); orderBack.Focus(FocusState.Programmatic); }
        var revision = detailRevision;
        var source = detailRequest = new CancellationTokenSource(); var token = source.Token;
        try {
            if (backend?.Url == null) throw new HttpRequestException();
            var detail = await backend.LedgerAsync<JsonElement>("/api/orders/" + orderId, token);
            if (revision != detailRevision || currentRoute != "orders" || detailOrderId != orderId) return;
            RenderOrderDetail(detail); orderFullRecord.IsEnabled = true;
            if (!preservePosition) AnimateNativeEntrance(orderDetailContent);
            if (preservePosition) { orderDetailContent.UpdateLayout(); orderDetailScroll.ChangeView(null, previousOffset, null, true); }
        } catch (Exception error) {
            if (revision != detailRevision || token.IsCancellationRequested || currentRoute != "orders") return;
            orderDetailContent.Children.Clear(); orderDetailContent.Children.Add(DetailText("暂时无法显示这笔订单。可重试或返回列表。"));
            OrderError(error, () => OpenOrderDetailAsync(orderId));
        } finally {
            if (revision == detailRevision) detailRequest = null;
            source.Dispose();
        }
    }
    async Task BackToOrdersAsync()
    {
        CancelDetailRequest(); detailOrderId = null; ordersNotice.IsOpen = false;
        orderDetailPanel.Visibility = Visibility.Collapsed; ordersListPanel.Visibility = Visibility.Visible;
        AnimateNativeEntrance(ordersListPanel);
        if (!listValid) await LoadOrdersAsync();
        orderList.Focus(FocusState.Programmatic);
    }
    async Task OpenFullOrderAsync()
    {
        if (detailOrderId is not long orderId || backend?.Url == null || browser.CoreWebView2 == null) return;
        CancelOrderRequests(); currentRoute = "web-orders"; pageReady = false;
        // Reload the shared Web UI in explicit fallback mode. Older hosts keep their normal behavior.
        browser.CoreWebView2.Navigate(backend.Url + "/?order=" + orderId + "&nativeFallback=1");
        ShowRoute(); loading.Visibility = Visibility.Visible; heading.Text = "正在打开完整记录";
        message.Text = "可在此核对和关联退款。"; progress.IsActive = true; progress.Visibility = Visibility.Visible;
    }
    async Task OpenOrdersFromWebAsync(string? month, long? orderId = null)
    {
        CancelOrderRequests();
        orderFilter = new(Month: string.IsNullOrWhiteSpace(month) || month == "latest" ? "all" : month);
        listValid = false; SetOrderFilters();
        currentRoute = "orders"; ShowRoute();
        if (orderId is long id && id > 0) await OpenOrderDetailAsync(id);
        else await LoadOrdersAsync();
    }
    static TextBlock DetailText(string text, double size = 14) => new() {
        Text = text, FontSize = size, FontWeight = Microsoft.UI.Text.FontWeights.Medium, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true
    };
    // Detail cards use the same eyebrow + bold title as the other native pages.
    StackPanel DetailCard(string title, string eyebrow = "")
    {
        var panel = new StackPanel {Spacing = 12};
        var head = new StackPanel {Spacing = 2};
        if (!string.IsNullOrEmpty(eyebrow)) head.Children.Add(new TextBlock {Text = eyebrow, Style = (Style)layout.Resources["CardEyebrow"]});
        var heading = new TextBlock {Text = title, Style = (Style)layout.Resources["CardTitle"]};
        AutomationProperties.SetHeadingLevel(heading, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level2);
        head.Children.Add(heading); panel.Children.Add(head);
        orderDetailContent.Children.Add(new Border {Style = (Style)layout.Resources["NativeCard"], Child = panel});
        return panel;
    }
    static void DetailFact(StackPanel panel, string label, string value)
    {
        var row = new Grid {ColumnSpacing = 20};
        row.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(1, GridUnitType.Star)});
        row.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(2, GridUnitType.Star)});
        var caption = DetailText(label, 13); caption.Opacity = 0.75;
        var content = DetailText(value); content.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold; Grid.SetColumn(content, 1);
        row.Children.Add(caption); row.Children.Add(content); panel.Children.Add(row);
    }
    void RenderOrderDetail(JsonElement detail)
    {
        var order = detail.GetProperty("order");
        var currency = LedgerLabels.Text(order, "currency", "CAD");
        orderDetailContent.Children.Clear();
        var header = new StackPanel {Spacing = 2, VerticalAlignment = VerticalAlignment.Center};
        header.Children.Add(new TextBlock {Text = "订单详情 · " + LedgerLabels.Text(order, "date") + " · " + LedgerLabels.Platform(LedgerLabels.Text(order, "platform")),
            Style = (Style)layout.Resources["PageEyebrow"]});
        var title = new TextBlock {Text = LedgerLabels.Text(order, "merchant", "未命名商户"), Style = (Style)layout.Resources["PageTitle"],
            TextTrimming = TextTrimming.CharacterEllipsis, IsTextSelectionEnabled = true};
        AutomationProperties.SetHeadingLevel(title, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level1);
        header.Children.Add(title);
        orderDetailContent.Children.Add(new Border {Style = (Style)layout.Resources["PageHeader"], Child = header});
        var summary = DetailCard("实际支出", "金额与付款");
        summary.Children.Add(new TextBlock {Text = LedgerLabels.Amount(order, "total", currency), Style = (Style)layout.Resources["MetricValue"], IsTextSelectionEnabled = true});
        if (LedgerLabels.Number(order, "refund_total") > 0) {
            DetailFact(summary, "原消费额", LedgerLabels.Amount(order, "gross_total", currency));
            DetailFact(summary, "已退款", LedgerLabels.Amount(order, "refund_total", currency));
        }
        DetailFact(summary, "付款方式", LedgerLabels.Card(LedgerLabels.Text(order, "card_label", "")));
        DetailFact(summary, "付款状态", LedgerLabels.Payment(LedgerLabels.Text(order, "payment_status")));
        DetailFact(summary, "支付渠道", LedgerLabels.Channel(LedgerLabels.Text(order, "payment_channel")));
        DetailFact(summary, "分类", LedgerLabels.Category(LedgerLabels.Text(order, "category", "uncategorized")));
        DetailFact(summary, "订单状态", LedgerLabels.Fulfillment(LedgerLabels.Text(order, "fulfillment")));
        DetailFact(summary, "订单号", LedgerLabels.Text(order, "order_id"));
        var items = LedgerLabels.Rows(detail, "items").ToList();
        var products = DetailCard("商品明细", items.Count + " 项商品");
        var edge = (Microsoft.UI.Xaml.Media.Brush)layout.Resources["LedgerEdgeBrush"];
        for (var index = 0; index < items.Count; index++) {
            var item = items[index];
            var row = new Grid {ColumnSpacing = 16, Padding = new Thickness(0, 10, 0, 10), BorderBrush = edge,
                BorderThickness = new Thickness(0, 0, 0, index == items.Count - 1 ? 0 : 1)};
            row.ColumnDefinitions.Add(new ColumnDefinition {Width = new GridLength(1, GridUnitType.Star)});
            row.ColumnDefinitions.Add(new ColumnDefinition {Width = GridLength.Auto});
            var name = new StackPanel {Spacing = 3};
            var productName = DetailText(LedgerLabels.Text(item, "product_name", "未命名商品"), 15); productName.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
            name.Children.Add(productName);
            var quantity = DetailText("数量 " + LedgerLabels.Text(item, "quantity"), 12); quantity.Opacity = 0.75; name.Children.Add(quantity);
            var price = DetailText(LedgerLabels.Amount(item, "price", LedgerLabels.Text(item, "currency", currency)));
            price.FontWeight = Microsoft.UI.Text.FontWeights.Bold; price.VerticalAlignment = VerticalAlignment.Center; Grid.SetColumn(price, 1);
            row.Children.Add(name); row.Children.Add(price);
            products.Children.Add(row);
        }
        if (items.Count == 0) products.Children.Add(DetailText("此订单没有商品级明细。"));
        var records = new StackPanel {Spacing = 14};
        foreach (var refund in LedgerLabels.Rows(detail, "refunds"))
            records.Children.Add(DetailText("退款 · " + LedgerLabels.Text(refund, "date") + " · " + LedgerLabels.Amount(refund, "amount", currency) + "\n" + LedgerLabels.Text(refund, "note", "")));
        foreach (var match in LedgerLabels.Rows(detail, "bank_matches"))
            records.Children.Add(DetailText("银行记录 · " + LedgerLabels.Text(match, "date") + " · " + LedgerLabels.Amount(match, "amount", currency) + " · " + LedgerLabels.Payment(LedgerLabels.Text(match, "match_status"))));
        foreach (var evidence in LedgerLabels.Rows(detail, "evidence"))
            records.Children.Add(DetailText("付款证据 · " + LedgerLabels.Platform(LedgerLabels.Text(evidence, "source")) + " · " + LedgerLabels.Payment(LedgerLabels.Text(evidence, "status")) + "\n" + LedgerLabels.Text(evidence, "evidence_snippet")));
        if (records.Children.Count == 0) records.Children.Add(DetailText("暂无退款或付款核对记录。"));
        orderDetailContent.Children.Add(new Expander {Header = "退款与付款证据", Content = records, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch});
    }
}
