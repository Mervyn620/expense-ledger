using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace Expenses.Desktop;
internal static class LedgerApi
{
    internal static async Task<T> ReadAsync<T>(HttpClient client, string url, string path, CancellationToken cancellation = default)
    {
        using var response = await client.GetAsync(url + path, cancellation);
        if (response.StatusCode == HttpStatusCode.NotFound)
        {
            var message = path.StartsWith("/api/orders/", StringComparison.Ordinal) ? "这笔订单已不存在，请刷新列表。"
                : path.StartsWith("/api/cash-withdrawals", StringComparison.Ordinal) ? "现金取款记录暂时无法读取，请刷新后重试。"
                : "账本页面暂时无法读取，请刷新后重试。";
            throw new InvalidOperationException(message);
        }
        response.EnsureSuccessStatusCode();
        using var stream = await response.Content.ReadAsStreamAsync(cancellation);
        return await JsonSerializer.DeserializeAsync<T>(stream, LedgerLabels.JsonOptions, cancellation)
            ?? throw new InvalidDataException("账本返回了空数据，请重试。");
    }

    internal static async Task<T> UpdateCashNoteAsync<T>(HttpClient client, string url, long bankTransactionPk, string note, CancellationToken cancellation = default)
    {
        if (bankTransactionPk <= 0) throw new ArgumentOutOfRangeException(nameof(bankTransactionPk));
        if (note == null) throw new ArgumentNullException(nameof(note));
        if (note.Length > 300) throw new InvalidOperationException("用途备注最多 300 个字符，请缩短后重试。");
        using var request = new HttpRequestMessage(HttpMethod.Patch, url + "/api/cash-withdrawals/" + bankTransactionPk.ToString(System.Globalization.CultureInfo.InvariantCulture));
        request.Headers.Add("Origin", url);
        request.Content = new StringContent(JsonSerializer.Serialize(new { note }), Encoding.UTF8, "application/json");
        try
        {
            using var response = await client.SendAsync(request, cancellation);
            if (response.StatusCode == HttpStatusCode.NotFound) throw new InvalidOperationException("这笔现金取款已不存在，请刷新列表。");
            if (response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.UnprocessableEntity)
                throw new InvalidOperationException("用途备注未通过校验，请确认不超过 300 个字符后重试。");
            if (!response.IsSuccessStatusCode)
                throw new InvalidOperationException($"保存用途备注失败（HTTP {(int)response.StatusCode}），请稍后重试。");
            using var stream = await response.Content.ReadAsStreamAsync(cancellation);
            return await JsonSerializer.DeserializeAsync<T>(stream, LedgerLabels.JsonOptions, cancellation)
                ?? throw new InvalidDataException("保存结果尚未确认，请刷新记录核实。");
        }
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested)
        {
            throw new TimeoutException("保存备注超时，结果尚未确认。请刷新记录核实后再试。");
        }
    }

    internal static async Task<T> WriteManualWechatAsync<T>(HttpClient client, string url, HttpMethod method, long? paymentId, ManualWechatDraft? draft, CancellationToken cancellation = default)
    {
        var path = "/api/manual-wechat-payments" + (paymentId is long id ? "/" + id.ToString(System.Globalization.CultureInfo.InvariantCulture) : "");
        using var request = new HttpRequestMessage(method, url + path);
        request.Headers.Add("Origin", url);
        if (draft != null) request.Content = new StringContent(JsonSerializer.Serialize(draft, LedgerLabels.JsonOptions), Encoding.UTF8, "application/json");
        try {
            using var response = await client.SendAsync(request, cancellation);
            if (!response.IsSuccessStatusCode) {
                if (response.StatusCode == HttpStatusCode.NotFound) throw new InvalidOperationException("这笔手填付款已不存在，请刷新列表。");
                if (response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.UnprocessableEntity)
                    throw new InvalidOperationException("付款未通过校验。请确认日期、原币金额、币种和收款方后重试。");
                throw new InvalidOperationException($"保存手填付款失败（HTTP {(int)response.StatusCode}），请稍后重试。");
            }
            using var stream = await response.Content.ReadAsStreamAsync(cancellation);
            return await JsonSerializer.DeserializeAsync<T>(stream, LedgerLabels.JsonOptions, cancellation)
                ?? throw new InvalidDataException("结果尚未确认，请刷新列表核实。");
        } catch (OperationCanceledException) when (!cancellation.IsCancellationRequested) {
            throw new TimeoutException("请求超时，结果尚未确认。请先刷新列表核实；重复保存会使用同一请求编号。");
        }
    }
}
