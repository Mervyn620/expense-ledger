using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Serialization;

namespace Expenses.Desktop;

public static class FinancePaths
{
    public static string Overview(string month) => "/api/overview?month=" + Uri.EscapeDataString(month);
    public static string Cash(string month, int limit = 200) => "/api/cash-withdrawals?month=" + Uri.EscapeDataString(month) + "&limit=" + Math.Clamp(limit, 1, 500).ToString(CultureInfo.InvariantCulture);
    public static string ManualWechat(string month, int limit = 200) => "/api/manual-wechat-payments?month=" + Uri.EscapeDataString(month) + "&limit=" + Math.Clamp(limit, 1, 500).ToString(CultureInfo.InvariantCulture);
}

public sealed class ManualWechatPage
{
    public string SelectedMonth { get; set; } = "all";
    public List<string> Months { get; set; } = new();
    public List<ManualWechatPayment> Rows { get; set; } = new();
    public Dictionary<string, ManualWechatCurrencyTotal> ByCurrency { get; set; } = new();
    public int TotalCount { get; set; }
    public string Summary => string.Join(" · ", new[] {"CNY", "CAD"}.Select(currency =>
        $"{LedgerLabels.Money(ByCurrency.GetValueOrDefault(currency)?.AmountMinor / 100m ?? 0, currency)}（{ByCurrency.GetValueOrDefault(currency)?.Count ?? 0} 笔）"));
}

public sealed class ManualWechatCurrencyTotal
{
    public int Count { get; set; }
    public long AmountMinor { get; set; }
}

public sealed class ManualWechatPayment
{
    public long Id { get; set; }
    public string PaidOn { get; set; } = "";
    public string Merchant { get; set; } = "";
    public long AmountMinor { get; set; }
    public string Currency { get; set; } = "CNY";
    public string Note { get; set; } = "";
    [JsonIgnore] public string Amount => LedgerLabels.Money(AmountMinor / 100m, Currency);
}

public sealed class ManualWechatDraft
{
    public string PaidOn { get; set; } = "";
    public string Merchant { get; set; } = "";
    public decimal Amount { get; set; }
    public string Currency { get; set; } = "CNY";
    public string Note { get; set; } = "";
    public string? RequestId { get; set; }
}

public sealed class FinanceOverview
{
    FinanceSummary summary = new();
    public string SelectedMonth { get; set; } = "all";
    public List<string> Months { get; set; } = new();
    public FinanceSummary Summary { get => summary; set => summary = value ?? new(); }
    public List<FinanceGroup> Cards { get; set; } = new();
    public List<FinanceGroup> Categories { get; set; } = new();
}

public sealed class FinanceSummary
{
    public int OrderCount { get; set; }
    public int ItemCount { get; set; }
    public int ReviewCount { get; set; }
    public decimal Total { get; set; }
    public decimal AverageOrder { get; set; }
    public decimal? PreviousTotal { get; set; }
    public decimal? ChangePercent { get; set; }
}

public sealed class FinanceGroup
{
    public string Key { get; set; } = "";
    public string Label { get; set; } = "";
    public int Orders { get; set; }
    public decimal Total { get; set; }
    [JsonIgnore] public string Amount => LedgerLabels.Price(Total, "CAD");
}

public sealed class CashPage
{
    CashSummary summary = new();
    public string SelectedMonth { get; set; } = "all";
    public CashSummary Summary { get => summary; set => summary = value ?? new(); }
    public List<CashWithdrawal> Rows { get; set; } = new();
}

public sealed class CashSummary
{
    public int WithdrawalCount { get; set; }
    public int NotedCount { get; set; }
    public decimal Total { get; set; }
}

public sealed class CashWithdrawal
{
    public long BankTransactionPk { get; set; }
    public string? Date { get; set; }
    public string? Description { get; set; }
    [JsonPropertyName("amount")] public decimal Value { get; set; }
    public string? Currency { get; set; }
    public string? AccountLabel { get; set; }
    public string? Note { get; set; }
    public string? NoteUpdatedAt { get; set; }
    [JsonIgnore] public string Title => string.IsNullOrWhiteSpace(Description) ? Loc.T("现金取款") : Description;
    [JsonIgnore] public string Metadata => string.Join(" · ", new[] { Date, LedgerLabels.Card(AccountLabel) }.Where(value => !string.IsNullOrWhiteSpace(value)));
    [JsonIgnore] public string Amount => LedgerLabels.Price(Value, Currency);
    [JsonIgnore] public string NoteSummary => string.IsNullOrWhiteSpace(Note) ? Loc.T("未填写用途备注") : Note;
    [JsonIgnore] public string AccessibleName => Loc.English ? $"{Title}, {Metadata}, withdrawal {Amount}, {NoteSummary}" : $"{Title}，{Metadata}，取款 {Amount}，{NoteSummary}";
    public override string ToString() => AccessibleName;
}

public sealed class CashNoteResult
{
    public long BankTransactionPk { get; set; }
    public string Note { get; set; } = "";
}
