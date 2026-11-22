using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;

namespace Expenses.Desktop;

public sealed record OrderFilter(string Month = "all", string Platform = "", string Category = "", string CardId = "", string Search = "", int Offset = 0)
{
    public const int PageSize = 50;
    public string Path => "/api/orders?" + string.Join("&", new Dictionary<string,string> {
        ["month"] = Month, ["platform"] = Platform, ["category"] = Category,
        ["card_id"] = CardId, ["q"] = Search.Trim(), ["offset"] = Math.Max(0,Offset).ToString(CultureInfo.InvariantCulture),
        ["limit"] = PageSize.ToString(CultureInfo.InvariantCulture)
    }.Select(pair => pair.Key + "=" + Uri.EscapeDataString(pair.Value)));
}
public sealed class OrderPage
{
    public List<LedgerOrder> Rows { get; set; } = new();
    public int TotalCount { get; set; }
}
// WinUI-only members (pill brushes) live in LedgerOrders.Pills.cs so plain .NET tests can link this file.
public sealed partial class LedgerOrder
{
    public long OrderPk { get; set; }
    public string? Date { get; set; }
    public string? Platform { get; set; }
    public string? Merchant { get; set; }
    public string? ItemSummary { get; set; }
    public string? CardLabel { get; set; }
    public string? Currency { get; set; }
    public decimal Total { get; set; }
    public decimal RefundTotal { get; set; }
    public string? PaymentStatus { get; set; }
    public string? Category { get; set; }
    public string? Fulfillment { get; set; }
    public string Title => string.IsNullOrWhiteSpace(Merchant) ? LedgerLabels.Platform(Platform) : Merchant;
    public string Summary => string.IsNullOrWhiteSpace(ItemSummary) ? Loc.T("查看订单详情") : ItemSummary;
    public string Metadata => string.Join(" · ", new[] {Date, Loc.T(LedgerLabels.Platform(Platform)), Loc.T(LedgerLabels.Card(CardLabel))}.Where(s => !string.IsNullOrWhiteSpace(s)));
    public string Amount => LedgerLabels.Price(Total, Currency);
    public string Status => (RefundTotal > 0 ? "含退款 · " : "") + LedgerLabels.Payment(PaymentStatus);
    // Table cells mirror the Web order table: short date, category, source, fulfillment and payment.
    public string ShortDate => Date is { Length: >= 10 } ? Date[5..10] : Date ?? "";
    public string CategoryLabel => LedgerLabels.Category(Category);
    public string PlatformLabel => Loc.T(LedgerLabels.Platform(Platform));
    // "订阅" is also a spending category, which stays as recorded, so the status is named here.
    public string FulfillmentLabel => (Loc.English && Fulfillment == "subscription" ? "Subscription" : Loc.T(LedgerLabels.FulfillmentShort(Fulfillment))) + (RefundTotal > 0 ? Loc.T(" · 含退款") : "");
    public string PaymentLabel => Loc.T(LedgerLabels.Card(CardLabel)) + (PaymentStatus == "inferred" ? Loc.T("（推断）") : "");
    public string AccessibleName => Loc.English ? $"{Title}, {Metadata}, actual spend {Amount}, {Loc.T(Status)}" : $"{Title}，{Metadata}，实际支出 {Amount}，{Status}";
    // ListViewItem automation falls back to ToString(); style-setter bindings do not apply in WinUI.
    public override string ToString() => AccessibleName;
}
public static class LedgerLabels
{
    public static readonly JsonSerializerOptions JsonOptions = new() {PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower};
    public static string Money(decimal amount, string? currency) => amount.ToString("N2", CultureInfo.GetCultureInfo("en-CA")) + " " + (string.IsNullOrWhiteSpace(currency) ? "CAD" : currency);
    // Display amounts the way the Web pages do ($12.34 for CAD). Other currencies keep their code so
    // CNY is never mistaken for dollars.
    public static string Price(decimal amount, string? currency) => string.IsNullOrWhiteSpace(currency) || currency == "CAD"
        ? (amount < 0 ? "-$" : "$") + Math.Abs(amount).ToString("N2", CultureInfo.GetCultureInfo("en-CA"))
        : Money(amount, currency);
    public static string Platform(string? value) => value switch {
        "walmart_ca" => "Walmart", "amazon_ca" => "Amazon", "the_ordinary_ca" => "The Ordinary", "uber_eats" => "Uber Eats",
        "fantuan" => "饭团", "tnt_ca" => "大统华", "scotia" or "scotiabank" => "Scotiabank", "bank" => "其他银行", _ => value ?? "其他"
    };
    public static string Category(string? value) => value switch {
        "kitchenware" => "餐厨用品", "household" => "家居日用", "unallocated" => "未分配差额",
        "personal_care" => "个护美容", "food_delivery" => "外卖", "mixed" => "综合购物", "dining" => "餐饮", "telecom" => "通讯",
        "groceries" => "食品杂货", "coffee_supplies" => "咖啡", "general_retail" => "日常零售", "uncategorized" => "未分类",
        "software_subscription" => "软件订阅", "bank_fees" => "银行费用", "grocery_delivery" => "杂货配送",
        "digital_entertainment" => "数字娱乐", "delivery_membership" => "外卖会员", "retail_membership" => "购物会员",
        "digital_services" => "数字服务", "transportation" => "出行", _ => value ?? "未分类"
    };
    public static string Payment(string? value) => value switch {
        "confirmed" or "matched" or "verified" => "已确认", "needs_review" or "unknown" or "unresolved" or "unverified" => "待核对",
        "inferred" => "推断", "pending" => "待处理", "bank_reconciled" => "银行对账", _ => "待核对"
    };
    public static string Card(string? value) => string.IsNullOrWhiteSpace(value) || value == "Needs Review" ? "待核对" : value;
    public static string Text(JsonElement value, string key, string fallback = "—") => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var item) && item.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined) ? item.ToString() : fallback;
    public static decimal? Number(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.Number && item.TryGetDecimal(out var result) ? result : null;
    public static string Amount(JsonElement value, string key, string? currency) => Number(value, key) is decimal number ? Price(number, currency) : "—";
    public static IEnumerable<JsonElement> Rows(JsonElement value, string key) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out var rows) && rows.ValueKind == JsonValueKind.Array ? rows.EnumerateArray() : Enumerable.Empty<JsonElement>();
    public static string Channel(string value) => value switch { "online" => "线上", "in_store" => "线下", "account_debit" => "账户扣款", _ => "未识别" };
    // Same wording and colour groups as fulfillmentLabel / fulfillmentTone in the Web app.
    public static string FulfillmentShort(string? value) => value switch {
        "delivered" => "已完成", "pending" => "未送达", "pending_acceptance" => "待商家接单", "preparing" => "备餐中",
        "in_transit" => "配送中", "scheduled" => "预约订单", "processing" => "处理中", "refunded" => "已退款",
        "shipped" => "已发货", "cancelled" or "canceled" => "已取消", "bank_reconciled" => "银行记录",
        "subscription" => "订阅", "delivery" => "配送订单", "completed" => "已完成", _ => "—"
    };
    public static string FulfillmentTone(string? value) => value switch {
        "delivered" or "completed" => "passed",
        "pending" or "shipped" or "pending_acceptance" or "preparing" or "in_transit" or "scheduled" => "warning",
        "cancelled" or "canceled" => "failed", _ => ""
    };
    public static string Fulfillment(string value) => value switch {
        "delivered" => "已送达", "completed" => "已完成", "shipped" => "已发货", "processing" => "处理中", "pending_acceptance" => "待商家接单", "preparing" => "备餐中", "in_transit" => "配送中", "scheduled" => "预约订单", "cancelled" or "canceled" => "已取消", "bank_reconciled" => "银行账单记录", "refunded" => "已退款", "pending" => "未送达", "subscription" => "订阅", "delivery" => "配送订单", _ => "未标记"
    };
}
