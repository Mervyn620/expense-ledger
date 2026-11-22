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
    bool verifyingOnboarding;
    static bool OnboardingVerificationRequested => Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_ONBOARDING") == "1";

    // Run only in a fresh, isolated workspace: it completes setup, imports a sample statement and
    // writes config/setup.json, config/workflow.json and data/bank_statements there.
    async Task VerifyOnboardingAsync()
    {
        if (verifyingOnboarding || !OnboardingVerificationRequested || backend == null) return;
        verifyingOnboarding = true;
        var checks = new List<string>(); string? failure = null; ContentDialog? dialog = null;
        void Check(bool condition, string label) { if (!condition) throw new Exception(label); checks.Add(label); }
        try {
            var before = await backend.LedgerAsync<JsonElement>("/api/setup");
            Check(before.GetProperty("needs_onboarding").GetBoolean(), "fresh_workspace_needs_onboarding");
            var choice = await InitialChoiceAsync();
            Check(choice.Floor.Day == 1 && choice.Floor < DateTimeOffset.Now.AddMonths(-2), "suggested_floor_is_three_months_back");

            dialog = CreateOnboardingStep(0, choice, manual: false, out var commit0);
            var showing = dialog.ShowAsync(); await Task.Delay(400);
            var body0 = (StackPanel)((ScrollViewer)dialog.Content).Content;
            var options = body0.Children.OfType<RadioButtons>().First();
            Check(options.Items.Count == 3 && options.SelectedIndex == -1 && !dialog.IsPrimaryButtonEnabled, "bank_step_three_options_none_preselected");
            options.SelectedIndex = 1; await Task.Delay(200);
            var panel = body0.Children.OfType<StackPanel>().First();
            var institution = panel.Children.OfType<TextBox>().First();
            Check(panel.Visibility == Visibility.Visible && !dialog.IsPrimaryButtonEnabled, "other_bank_needs_a_name");
            institution.Text = "Test Bank"; ((RadioButtons)panel.Children[1]).SelectedIndex = 1; await Task.Delay(200);
            Check(dialog.IsPrimaryButtonEnabled, "other_bank_with_name_can_continue");
            Check(ReferenceEquals(dialog.Background, SurfaceGlass) && dialog.CloseButtonText == "跳过引导", "bank_step_uses_glass_and_can_skip");
            FocusLayoutVerificationWindow(); await Task.Delay(250); CaptureWindowPixels("onboarding-step1");
            commit0(); dialog.Hide(); await showing; dialog = null;
            Check(choice.BankMode == "statements" && choice.Institution == "Test Bank" && choice.StatementType == "credit_card", "bank_step_commits_choice");

            dialog = CreateOnboardingStep(1, choice, manual: false, out var commit1);
            showing = dialog.ShowAsync(); await Task.Delay(400);
            var boxes = FindChildren<CheckBox>(dialog.Content as DependencyObject).ToList();
            Check(boxes.Count == BillPlatformPreferences.All.Length && dialog.SecondaryButtonText == "上一步", "platform_step_lists_all_platforms_with_back");
            foreach (var box in boxes) box.IsChecked = (string)box.Tag is "walmart_ca" or "uber_eats";
            FocusLayoutVerificationWindow(); await Task.Delay(250); CaptureWindowPixels("onboarding-step2");
            commit1(); dialog.Hide(); await showing; dialog = null;
            Check(choice.Platforms.SequenceEqual(new[] { "walmart_ca", "uber_eats" }), "platform_step_commits_choice");

            dialog = CreateOnboardingStep(2, choice, manual: false, out var commit2);
            showing = dialog.ShowAsync(); await Task.Delay(400);
            var picker = FindChildren<CalendarDatePicker>(dialog.Content as DependencyObject).First();
            Check(picker.Date is not null && dialog.PrimaryButtonText == "完成", "date_step_prefilled_and_finishes");
            picker.Date = new DateTimeOffset(new DateTime(2026, 8, 1));   // local midnight, as the picker produces
            FocusLayoutVerificationWindow(); await Task.Delay(250); CaptureWindowPixels("onboarding-step3");
            commit2(); dialog.Hide(); await showing; dialog = null;

            var sample = Path.Combine(root, "verification", "onboarding-sample.qfx");
            File.WriteAllText(sample, "<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CURDEF>CAD</CURDEF><CCACCTFROM><ACCTID>4500111122223333</ACCTID></CCACCTFROM><BANKTRANLIST>"
                + "<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261110</DTPOSTED><TRNAMT>-48.10</TRNAMT><FITID>V1</FITID><NAME>GROCERY MART</NAME></STMTTRN>"
                + "<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20261112</DTPOSTED><TRNAMT>300.00</TRNAMT><FITID>V2</FITID><NAME>PAYMENT - THANK YOU</NAME></STMTTRN>"
                + "</BANKTRANLIST></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>");
            choice.StatementPath = sample;
            await CompleteOnboardingAsync(choice);

            var after = await backend.LedgerAsync<JsonElement>("/api/setup");
            Check(after.GetProperty("completed").GetBoolean() && !after.GetProperty("needs_onboarding").GetBoolean(), "setup_completed_and_guide_will_not_return");
            Check(LedgerLabels.Text(after, "bank_mode") == "statements" && LedgerLabels.Text(after, "collection_floor") == "2026-08-01", "bank_mode_and_floor_saved");
            Check(DesktopPreferences.Load(root).DefaultBillPlatforms.SequenceEqual(new[] { "walmart_ca", "uber_eats" }), "platforms_saved_as_default_update_platforms");
            var accounts = LedgerLabels.Rows(await backend.LedgerAsync<JsonElement>("/api/bank-statements"), "accounts").ToList();
            Check(accounts.Count == 1 && LedgerLabels.Text(accounts[0], "account_id") == "test_bank_credit_card" && LedgerLabels.Text(accounts[0], "rows") == "2"
                  && LedgerLabels.Text(accounts[0], "statement_type") == "credit_card", "statement_imported_for_new_account");

            for (var i = 0; i < 100 && await browser.CoreWebView2.ExecuteScriptAsync("window.expenseDesktopReady===true && !!document.getElementById('scotiaActionButton')") != "true"; i++) await Task.Delay(100);
            await Task.Delay(800);
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.getElementById('scotiaActionButton').hidden && getComputedStyle(document.getElementById('scotiaActionButton')).display==='none'") == "true", "web_hides_scotia_login_for_statement_mode");

            await NavigateAsync("native-settings"); await Task.Delay(600);
            Check(bankModeStatus.Text.Contains("其他银行", StringComparison.Ordinal) && bankModeStatus.Text.Contains("Test Bank", StringComparison.Ordinal), "settings_card_shows_mode_and_imported_account");
            Check(!collectorLocationStatus.Text.Contains("无法读取", StringComparison.Ordinal), "new_workspace_reads_collector_location");
            await CaptureNativePageAsync("settings", "onboarding");
        } catch (Exception error) { failure = error.Message; Log(error); }
        finally {
            dialog?.Hide();
            File.WriteAllText(Path.Combine(root, "verification", "onboarding-runtime.json"), JsonSerializer.Serialize(new {
                status = failure == null ? "passed" : "failed", failure, checks,
                scope = "Fresh isolated workspace: guide steps, saved setup, sample statement import, web and settings state. No collection or bank login.",
            }, new JsonSerializerOptions { WriteIndented = true }));
        }
    }

    static IEnumerable<T> FindChildren<T>(DependencyObject? parent) where T : DependencyObject
    {
        if (parent == null) yield break;
        if (parent is T match) yield return match;
        if (parent is ContentControl { Content: DependencyObject content }) foreach (var child in FindChildren<T>(content)) yield return child;
        if (parent is Panel panel) foreach (var item in panel.Children) foreach (var child in FindChildren<T>(item)) yield return child;
    }
}
