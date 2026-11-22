using System.Net;
using System.Text.Json;
using Expenses.Desktop;

var checks = new List<string>();
void Check(bool success, string name) { if (!success) throw new Exception(name); checks.Add(name); }
async Task<T> Fails<T>(Func<Task> action) where T : Exception
{
    try { await action(); }
    catch (T error) { return error; }
    throw new Exception("Expected " + typeof(T).Name);
}
HttpClient Client(HttpStatusCode code, string body = "") => new(new FixtureHandler((_, _) => Task.FromResult(new HttpResponseMessage(code) { Content = new StringContent(body) })));
const string url = "http://127.0.0.1:8765";

Check(FinancePaths.Overview("month&override=all") == "/api/overview?month=month%26override%3Dall", "overview_month_is_encoded");
Check(FinancePaths.Cash("all&month=2026-09", 900).Contains("month=all%26month%3D2026-09&limit=500") && FinancePaths.Cash("all", 0).EndsWith("limit=1"), "cash_query_encoding_and_backend_limit");
Check(FinancePaths.ManualWechat("2026-10&month=all").Contains("month=2026-10%26month%3Dall"), "manual_wechat_month_is_encoded");
using (var client = Client(HttpStatusCode.OK, """{"selected_month":"2026-09","months":["2026-09"],"summary":{"total":300.29,"order_count":10,"average_order":30.03,"previous_total":null,"change_percent":null},"cards":[{"key":"Card 1234","label":"Card 1234","orders":3,"total":100.19}],"categories":[{"key":"dining","label":"餐饮","orders":2,"total":78.05}]}"""))
{
    var overview = await LedgerApi.ReadAsync<FinanceOverview>(client, url, FinancePaths.Overview("2026-09"));
    Check(overview.Summary.Total == 300.29m && overview.Cards[0].Total == 100.19m && overview.Categories[0].Total == 78.05m, "overview_decimal_totals_from_server");
    Check(overview.Summary.OrderCount == 10 && overview.Summary.PreviousTotal is null && overview.Summary.ChangePercent is null, "summary_preserves_counts_and_null_comparison");
    Check(overview.SelectedMonth == "2026-09" && overview.Months.SequenceEqual(new[] {"2026-09"}) && overview.Categories[0].Label == "餐饮", "overview_months_and_server_labels");
}
using (var client = Client(HttpStatusCode.OK, """{"selected_month":"all","summary":{"withdrawal_count":205,"total":1400.75,"noted_count":32},"rows":[{"bank_transaction_pk":77,"date":"2026-09-28","description":"ATM Withdrawal","amount":75.25,"currency":"USD","account_label":"Daily account","note":"Lunch cash","note_updated_at":null}]}"""))
{
    var page = await LedgerApi.ReadAsync<CashPage>(client, url, FinancePaths.Cash("all"));
    var row = page.Rows.Single();
    Check(page.Summary.WithdrawalCount == 205 && page.Summary.Total == 1400.75m && page.Summary.NotedCount == 32, "cash_summary_is_not_recomputed_from_limited_rows");
    Check(row.Value == 75.25m && row.Amount == "75.25 USD", "cash_uses_source_currency_and_decimal_amount");
    Check(row.Title == "ATM Withdrawal" && row.Metadata == "2026-09-28 · Daily account" && row.NoteSummary == "Lunch cash" && row.AccessibleName.Contains("取款 75.25 USD"), "cash_display_and_accessible_name");
}
var nullOverview = JsonSerializer.Deserialize<FinanceOverview>("""{"summary":null}""", LedgerLabels.JsonOptions)!;
var nullCash = JsonSerializer.Deserialize<CashPage>("""{"summary":null,"rows":[{"amount":0,"description":null,"note":null,"account_label":null,"currency":null}]}""", LedgerLabels.JsonOptions)!;
Check(nullOverview.Summary.Total == 0 && nullCash.Summary.WithdrawalCount == 0, "null_summary_is_safe");
// Since 0.14.0 CAD amounts display like the Web pages ($0.00); other currencies keep their code.
Check(nullCash.Rows[0].Amount == "$0.00" && nullCash.Rows[0].Title == "现金取款" && nullCash.Rows[0].NoteSummary == "未填写用途备注", "cash_null_labels_have_safe_fallbacks");

using (var client = Client(HttpStatusCode.OK, """{"selected_month":"all","months":["2026-10"],"total_count":2,"by_currency":{"CNY":{"count":1,"amount_minor":8825},"CAD":{"count":1,"amount_minor":1230}},"rows":[{"id":7,"paid_on":"2026-10-28","merchant":"Test","amount_minor":8825,"currency":"CNY","note":"manual"}]}"""))
{
    var manual = await LedgerApi.ReadAsync<ManualWechatPage>(client, url, FinancePaths.ManualWechat("all"));
    Check(manual.TotalCount == 2 && manual.Rows.Single().Amount == "88.25 CNY", "manual_wechat_keeps_original_minor_units");
    Check(manual.Summary.Contains("88.25 CNY") && manual.Summary.Contains("12.30 CAD") && manual.Months.SequenceEqual(new[]{"2026-10"}), "manual_wechat_separates_currency_totals");
}
using (var client = new HttpClient(new FixtureHandler(async (request, cancellation) =>
{
    Check(request.Method == HttpMethod.Post && request.RequestUri!.AbsoluteUri == url + "/api/manual-wechat-payments", "manual_wechat_post_endpoint");
    Check(request.Headers.GetValues("Origin").Single() == url, "manual_wechat_same_origin");
    using var payload = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
    Check(payload.RootElement.GetProperty("amount").GetDecimal() == 88.25m && payload.RootElement.GetProperty("currency").GetString() == "CNY" && payload.RootElement.GetProperty("request_id").GetString() == "11111111-1111-1111-1111-111111111111", "manual_wechat_posts_exact_currency_and_idempotency_key");
    return new HttpResponseMessage(HttpStatusCode.Created) {Content = new StringContent("""{"id":9,"paid_on":"2026-10-28","merchant":"Test","amount_minor":8825,"currency":"CNY","note":""}""")};
})))
{
    var saved = await LedgerApi.WriteManualWechatAsync<ManualWechatPayment>(client, url, HttpMethod.Post, null,
        new ManualWechatDraft {PaidOn="2026-10-28", Merchant="Test", Amount=88.25m, Currency="CNY", RequestId="11111111-1111-1111-1111-111111111111"});
    Check(saved.Amount == "88.25 CNY", "manual_wechat_uses_server_confirmed_result");
}

var writeCount = 0;
using (var client = new HttpClient(new FixtureHandler(async (request, cancellation) =>
{
    writeCount++;
    Check(request.Method == HttpMethod.Patch && request.RequestUri!.AbsoluteUri == url + "/api/cash-withdrawals/77", "patch_verb_and_cash_endpoint");
    Check(request.Headers.GetValues("Origin").Single() == url && request.Headers.GetValues("X-Desktop-Token").Single() == "fixture-token", "patch_origin_and_shared_client_auth");
    Check(request.Content!.Headers.ContentType!.MediaType == "application/json", "patch_json_media_type");
    using var content = JsonDocument.Parse(await request.Content.ReadAsStringAsync(cancellation));
    Check(content.RootElement.GetProperty("note").GetString() == "  用途\n  coffee  ", "patch_preserves_user_text_for_server_normalization");
    return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("""{"bank_transaction_pk":77,"note":"用途 coffee"}""") };
})))
{
    client.DefaultRequestHeaders.Add("X-Desktop-Token", "fixture-token");
    var result = await LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "  用途\n  coffee  ");
    Check(result.BankTransactionPk == 77 && result.Note == "用途 coffee" && writeCount == 1, "patch_returns_canonical_server_note_once");
}
using (var client = new HttpClient(new FixtureHandler(async (request, cancellation) =>
{
    using var content = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
    Check(content.RootElement.GetProperty("note").GetString() == "", "clearing_note_sends_empty_string");
    return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("""{"bank_transaction_pk":77,"note":""}""") };
})))
{
    var result = await LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "");
    Check(result.Note == "", "clear_note_uses_canonical_empty_result");
}
using (var client = new HttpClient(new FixtureHandler((_, _) => throw new Exception("Invalid input reached the network"))))
{
    var invalidLength = await Fails<InvalidOperationException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, new string('a', 301)));
    Check(invalidLength.Message.Contains("300"), "oversized_note_rejected_before_request");
}
using (var client = Client(HttpStatusCode.NotFound, "private response text"))
{
    var error = await Fails<InvalidOperationException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "memo"));
    Check(error.Message.Contains("现金取款") && !error.Message.Contains("订单") && !error.Message.Contains("private"), "patch_404_is_sanitized_and_cash_specific");
    var readError = await Fails<InvalidOperationException>(() => LedgerApi.ReadAsync<CashPage>(client, url, FinancePaths.Cash("all")));
    Check(readError.Message.Contains("现金取款") && !readError.Message.Contains("订单"), "cash_get_404_is_not_an_order_error");
}
foreach (var code in new[] {HttpStatusCode.BadRequest, HttpStatusCode.UnprocessableEntity})
{
    using var client = Client(code, "private validation data");
    var error = await Fails<InvalidOperationException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "memo"));
    Check(error.Message.Contains("校验") && !error.Message.Contains("private"), "patch_validation_" + (int)code + "_is_sanitized");
}
using (var client = Client(HttpStatusCode.InternalServerError, "private exception detail"))
{
    var error = await Fails<InvalidOperationException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "memo"));
    Check(error.Message.Contains("HTTP 500") && !error.Message.Contains("private"), "patch_status_error_is_sanitized");
}
using (var client = Client(HttpStatusCode.OK, "null"))
{
    var error = await Fails<InvalidDataException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "memo"));
    Check(error.Message.Contains("尚未确认"), "patch_empty_success_is_not_reported_as_saved");
}
using (var client = new HttpClient(new FixtureHandler(async (_, token) => { await Task.Delay(Timeout.Infinite, token); return new HttpResponseMessage(); })) { Timeout = TimeSpan.FromMilliseconds(80) })
{
    var error = await Fails<TimeoutException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "memo"));
    Check(error.Message.Contains("尚未确认"), "patch_timeout_reports_unconfirmed_result");
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();
    await Fails<OperationCanceledException>(() => LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, url, 77, "memo", cancellation.Token));
    checks.Add("patch_caller_cancellation_propagates");
}
Console.WriteLine(JsonSerializer.Serialize(new {status = "passed", count = checks.Count, checks}));

sealed class FixtureHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request, cancellationToken);
}
