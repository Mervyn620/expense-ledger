using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingManualWechat;

    async Task VerifyManualWechatAsync()
    {
        if (verifyingManualWechat || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_MANUAL_WECHAT") != "1") return;
        verifyingManualWechat = true;
        var checks = new List<string>();
        try {
            await NavigateAsync("sources");
            if (!financeValid || manualWechatPage == null) throw new Exception("Manual payment page did not load.");
            var originalCount = manualWechatPage.TotalCount;
            var fixture = new ManualWechatPage {
                ByCurrency = new() {
                    ["CNY"] = new() {Count = 1, AmountMinor = 8825},
                    ["CAD"] = new() {Count = 1, AmountMinor = 1230}
                },
                Rows = new() {
                    new() {Id = 987654321, PaidOn = "2026-10-28", Merchant = "测试商户", AmountMinor = 8825, Currency = "CNY", Note = "仅界面示例"},
                    new() {Id = 987654322, PaidOn = "2026-10-27", Merchant = "测试商户", AmountMinor = 1230, Currency = "CAD"}
                }, TotalCount = 2
            };
            RenderManualWechat(fixture);
            if (!manualWechatSummary.Text.Contains("88.25 CNY") || !manualWechatSummary.Text.Contains("12.30 CAD"))
                throw new Exception("Currency totals were mixed.");
            checks.Add("currency_totals_separate");
            await CaptureNativePageAsync("list-fixture", "manual-wechat-0.13.6");
            var editor = OpenManualWechatEditorAsync();
            for (var attempt = 0; attempt < 50 && manualWechatDialog?.IsLoaded != true; attempt++) await Task.Delay(100);
            if (manualWechatDialog?.IsLoaded != true) throw new Exception("Entry dialog did not open.");
            checks.Add("entry_dialog_opened");
            await CaptureNativePageAsync("entry-dialog", "manual-wechat-0.13.6", manualWechatDialog);
            manualWechatDialog.Hide();
            await editor;
            await LoadFinanceAsync();
            if (!financeValid || manualWechatPage?.TotalCount != originalCount)
                throw new Exception("Fixture altered real records.");
            checks.Add("fixture_did_not_write_ledger");
            File.WriteAllText(Path.Combine(root, "verification", "manual-wechat-0.13.6-runtime.json"),
                JsonSerializer.Serialize(new {status = "passed", checks, at = DateTimeOffset.Now}));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(Path.Combine(root, "verification", "manual-wechat-0.13.6-runtime.json"),
                JsonSerializer.Serialize(new {status = "failed", checks, error = error.ToString(), at = DateTimeOffset.Now}));
        } finally {
            manualWechatDialog?.Hide();
            await NavigateAsync("overview");
            WriteState("ready");
        }
    }
}
