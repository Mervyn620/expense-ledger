using System.Net;
using System.Text.Json;
using Expenses.Desktop;

var checks = new List<string>();
void Check(bool success, string name) { if (!success) throw new Exception(name); checks.Add(name); }
const string url = "http://127.0.0.1";
var filter = new OrderFilter("2026-09", "shop&all", "中文", "a+b", "tea & coffee/果", -4);
Check(filter.Path.Contains("platform=shop%26all") && filter.Path.Contains("card_id=a%2Bb") && filter.Path.Contains("offset=0") && filter.Path.Contains("limit=50"), "encoded_filters");
using var json = JsonDocument.Parse("""{"zero":0,"missing":null,"string":"5","object":{}}""");
Check(LedgerLabels.Number(json.RootElement, "zero") == 0 && LedgerLabels.Number(json.RootElement, "missing") == null && LedgerLabels.Number(json.RootElement, "string") == null && LedgerLabels.Number(json.RootElement, "object") == null, "nullable_numbers");
Check(LedgerLabels.Payment("needs_review") == "待核对" && LedgerLabels.Card("Needs Review") == "待核对", "review_labels");
using var client = new HttpClient(new FixtureHandler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) {Content = new StringContent("""{"rows":[{"order_pk":8,"total":75.25,"gross_total":100.25,"refund_total":25,"currency":"USD","payment_status":"needs_review"}],"total_count":1}""")})));
var page = await LedgerApi.ReadAsync<OrderPage>(client, url, filter.Path);
Check(page.TotalCount == 1 && page.Rows[0].Amount == "75.25 USD" && page.Rows[0].Status.Contains("退款"), "api_net_amount_and_currency");
using var notFound = new HttpClient(new FixtureHandler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound))));
try { await LedgerApi.ReadAsync<JsonElement>(notFound, url, "/api/orders/8"); throw new Exception("404 was accepted"); }
catch (InvalidOperationException error) { Check(error.Message.Contains("不存在"), "missing_order"); }
using var malformed = new HttpClient(new FixtureHandler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) {Content = new StringContent("not-json")})));
try { await LedgerApi.ReadAsync<OrderPage>(malformed, url, "/api/orders"); throw new Exception("Invalid JSON was accepted"); }
catch (JsonException) { checks.Add("invalid_response_rejected"); }
using var timeout = new HttpClient(new FixtureHandler(async (_, token) => { await Task.Delay(Timeout.Infinite, token); return new HttpResponseMessage(); })) {Timeout = TimeSpan.FromMilliseconds(80)};
try { await LedgerApi.ReadAsync<OrderPage>(timeout, url, "/api/orders"); throw new Exception("Timeout was ignored"); }
catch (OperationCanceledException) { checks.Add("timeout_propagates"); }
using var cancellation = new CancellationTokenSource(); cancellation.Cancel();
try { await LedgerApi.ReadAsync<OrderPage>(timeout, url, "/api/orders", cancellation.Token); throw new Exception("Cancellation was ignored"); }
catch (OperationCanceledException) { checks.Add("caller_cancellation_propagates"); }
// Interface language: the shared dictionary translates interface text and leaves ledger data alone.
var repo = new DirectoryInfo(AppContext.BaseDirectory);
while (repo != null && !File.Exists(Path.Combine(repo.FullName, Loc.DictionaryPath))) repo = repo.Parent;
Check(repo != null, "dictionary_found");
Loc.Load(repo!.FullName);
Check(Loc.T("订单") == "订单", "chinese_is_the_default");
Loc.SetLanguage("en");
Check(Loc.T("订单") == "Orders" && Loc.T("  桌面设置 ") == "  Desktop settings ", "exact_keys_keep_spacing");
Check(Loc.T("共 12 笔 · 3–4 笔") == "12 total · 3–4", "pattern_with_values");
Check(Loc.T("2026年11月 · 月度花销分析") == "11/2026 · Monthly spending analysis", "pattern_reorders_values");
Check(Loc.T("已选择：rbc.csv") == "Selected: rbc.csv", "colon_prefix_pattern");
Check(Loc.T("已送达 · 含退款") == "Delivered · includes refund", "composite_label_by_parts");
Check(Loc.T("正在进行：更新账单") == "In progress: Update bills", "captured_value_is_translated");
Check(Loc.T("餐饮") == "餐饮" && Loc.T("Walmart Supercentre") == "Walmart Supercentre" && Loc.T("给妈妈的礼物") == "给妈妈的礼物", "ledger_data_unchanged");
page.Rows[0].Fulfillment = "delivered";
Check(page.Rows[0].FulfillmentLabel == "Completed · includes refund" && page.Rows[0].Summary == "View order details", "order_labels_follow_language");
Loc.SetLanguage("zh");
Check(page.Rows[0].Summary == "查看订单详情" && Loc.T("订单") == "订单", "back_to_chinese");
Console.WriteLine(JsonSerializer.Serialize(new {status="passed", checks}));
sealed class FixtureHandler(Func<HttpRequestMessage,CancellationToken,Task<HttpResponseMessage>> respond) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request, cancellationToken);
}
