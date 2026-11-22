using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;

namespace Expenses.Desktop;

// Native replacement for the former WebView analysis page (/analysis). The backend API is unchanged.
public sealed partial class MainWindow
{
    sealed record NativeChatGptExport(string FilePath, string Sha256, string Message, bool Persistent);

    bool analysisLoaded, analysisBusy, deepseekConfigured;
    string deepseekModel = "正在连接";
    JsonElement analysisPeriods, chatgptPeriods;
    NativeChatGptExport? chatgptExport;
    readonly List<Dictionary<string, string>> analysisChatHistory = new();

    bool AnalysisUsesChatGpt => preferences.AnalysisProvider != "deepseek";

    void InitializeAnalysis()
    {
        for (var i = 0; i < 3; i++) analysisRanges.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        analysisPanel.SizeChanged += (_, _) => {
            analysisContent.Width = Math.Max(0, Math.Min(980, analysisPanel.ActualWidth - 48));
            var narrow = analysisPanel.ActualWidth < 720;
            var buttons = new[] { analysisMonth, analysisYear, analysisTrend };
            for (var i = 0; i < buttons.Length; i++) {
                Grid.SetColumn(buttons[i], narrow ? 0 : i);
                Grid.SetRow(buttons[i], narrow ? i : 0);
            }
            analysisRanges.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
            analysisRanges.ColumnDefinitions[2].Width = analysisRanges.ColumnDefinitions[1].Width;
        };
        analysisUseChatGpt.Click += (_, _) => SetAnalysisProvider("chatgpt");
        analysisUseDeepSeek.Click += (_, _) => SetAnalysisProvider("deepseek");
        foreach (var button in new[] { analysisMonth, analysisYear, analysisTrend })
            button.Click += async (sender, _) => await RunAnalysisRangeAsync((string)((Button)sender).Tag);
        analysisRetryAttach.Click += async (_, _) => await RunNativeChatGptAsync("chatgpt-handoff");
        analysisOpenFile.Click += async (_, _) => await RunNativeChatGptAsync("chatgpt-open-export");
        analysisCopyPath.Click += async (_, _) => await RunNativeChatGptAsync("chatgpt-copy-path");
        analysisCopyQuestion.Click += async (_, _) => await RunNativeChatGptAsync("chatgpt-copy-question");
        analysisDeleteTemp.Click += async (_, _) => await ConfirmDeleteChatGptTempAsync();
        analysisChatSend.Click += async (_, _) => await SendAnalysisChatAsync();
        var send = new KeyboardAccelerator { Key = VirtualKey.Enter, Modifiers = VirtualKeyModifiers.Control };
        send.Invoked += async (_, args) => { args.Handled = true; await SendAnalysisChatAsync(); };
        analysisChatInput.KeyboardAccelerators.Add(send);
        RenderAnalysisProvider();
    }

    async Task LoadAnalysisAsync()
    {
        RenderAnalysisProvider();
        if (backend == null) return;
        try {
            var status = await backend.LedgerAsync<JsonElement>("/api/analysis/status");
            deepseekConfigured = status.TryGetProperty("configured", out var configured) && configured.ValueKind == JsonValueKind.True;
            deepseekModel = deepseekConfigured ? LedgerLabels.Text(status, "model", "DeepSeek") : "DeepSeek 未配置";
            analysisPeriods = status.TryGetProperty("periods", out var periods) ? periods.Clone() : default;
            chatgptPeriods = status.TryGetProperty("chatgpt_periods", out var chatgpt) ? chatgpt.Clone() : default;
            analysisLoaded = true;
        } catch (Exception error) {
            Log(error);
            NotifyNative("暂时无法读取分析服务状态：" + error.Message, true);
        }
        RenderAnalysisProvider();
    }

    static string Period(JsonElement value, string key, string fallback) =>
        value.ValueKind == JsonValueKind.Object ? LedgerLabels.Text(value, key, fallback) : fallback;

    void RenderAnalysisProvider()
    {
        var chatgpt = AnalysisUsesChatGpt;
        analysisUseChatGpt.IsChecked = chatgpt;
        analysisUseDeepSeek.IsChecked = !chatgpt;
        analysisUseChatGpt.IsEnabled = analysisUseDeepSeek.IsEnabled = !analysisBusy;
        analysisProviderHint.Text = chatgpt
            ? "点击范围即生成账本文件，并附到 Chrome 的 Claude 草稿，由你自己发送。"
            : "点击范围，在客户端内查看 DeepSeek 分析；也可以在下方自由询问。";
        analysisServiceStatus.Text = chatgpt ? "Chrome · Ledger User" : deepseekModel;
        analysisChatGptCard.Visibility = chatgpt ? Visibility.Visible : Visibility.Collapsed;
        analysisDeepSeekCard.Visibility = analysisChatCard.Visibility = chatgpt ? Visibility.Collapsed : Visibility.Visible;
        analysisMonthPeriod.Text = chatgpt
            ? DateTime.Now.ToString("yyyy-MM", CultureInfo.InvariantCulture) + " · 本月"
            : Period(analysisPeriods, "latest_month", "暂无月份");
        analysisYearPeriod.Text = chatgpt
            ? Period(chatgptPeriods, "latest_year", Period(analysisPeriods, "latest_year", "最近有记录年份"))
            : Period(analysisPeriods, "latest_year", "暂无年份");
        analysisTrendPeriod.Text = Period(analysisPeriods, "range_label", "全部账本");
        analysisMonth.IsEnabled = analysisYear.IsEnabled = analysisTrend.IsEnabled = !analysisBusy && (chatgpt || deepseekConfigured);
        analysisChatInput.IsEnabled = analysisChatSend.IsEnabled = !analysisBusy && deepseekConfigured;
        foreach (var button in new[] { analysisRetryAttach, analysisOpenFile, analysisCopyPath, analysisCopyQuestion, analysisDeleteTemp })
            button.IsEnabled = !analysisBusy;
        analysisDeleteTemp.Visibility = chatgptExport is { Persistent: false } ? Visibility.Visible : Visibility.Collapsed;
        analysisChatGptProgress.Visibility = analysisBusy && chatgpt ? Visibility.Visible : Visibility.Collapsed;
        if (!chatgpt && analysisLoaded && !deepseekConfigured && analysisResultBody.Children.Count == 0) {
            analysisResultTitle.Text = "需要 DeepSeek API 密钥";
            analysisResultMeta.Text = "运行 backend/scripts/configure-deepseek.ps1 设置密钥，然后重新启动账本。";
        }
    }

    void SetAnalysisBusy(bool value)
    {
        analysisBusy = value;
        RenderAnalysisProvider();
    }

    void SetAnalysisProvider(string provider)
    {
        if (analysisBusy) { RenderAnalysisProvider(); return; }
        preferences.AnalysisProvider = provider;
        try { preferences.Save(root); } catch (Exception error) { Log(error); }
        RenderAnalysisProvider();
    }

    async Task RunAnalysisRangeAsync(string mode)
    {
        if (analysisBusy || backend == null) return;
        if (AnalysisUsesChatGpt) await PrepareChatGptAsync(mode);
        else await RunDeepSeekAsync(mode);
    }

    // Claude: the backend writes the export, then the host attaches it to the user's Chrome draft.
    async Task PrepareChatGptAsync(string mode)
    {
        chatgptExport = null;
        analysisChatGptPrepared.Visibility = Visibility.Collapsed;
        analysisChatGptStatus.Text = "正在整理账本文件…";
        SetAnalysisBusy(true);
        try {
            var payload = await backend!.PostJsonAsync("/api/analysis/chatgpt/prepare", new { mode });
            var meta = payload.TryGetProperty("meta", out var value) ? value : default;
            chatgptExport = new NativeChatGptExport(
                LedgerLabels.Text(payload, "file_path", ""), LedgerLabels.Text(payload, "file_sha256", ""),
                LedgerLabels.Text(payload, "message", ""),
                payload.TryGetProperty("persistent", out var persistent) && persistent.ValueKind == JsonValueKind.True);
            var orders = LedgerLabels.Text(meta, "orders", "0");
            var parts = new List<string> {
                LedgerLabels.Text(meta, "period", ""),
                $"自动订单 {orders} 笔",
                "CAD " + (LedgerLabels.Number(meta, "total_cad") ?? 0m).ToString("N2", CultureInfo.InvariantCulture),
                $"明细 {LedgerLabels.Text(meta, "included_orders", "0")}/{orders} 笔",
            };
            if (meta.ValueKind == JsonValueKind.Object && meta.TryGetProperty("manual_by_currency", out var manual) && manual.ValueKind == JsonValueKind.Object)
                parts.AddRange(manual.EnumerateObject().Select(item => $"{item.Name} 手填 {LedgerLabels.Text(item.Value, "count", "0")} 笔"));
            if (meta.ValueKind == JsonValueKind.Object && meta.TryGetProperty("truncated", out var truncated) && truncated.ValueKind == JsonValueKind.True)
                parts.Add("明细已截断，汇总仍为全范围");
            analysisChatGptScope.Text = string.Join(" · ", parts.Where(part => part.Length > 0));
            analysisChatGptFile.Text = "本地文件：" + chatgptExport.FilePath;
            analysisChatGptPrepared.Visibility = Visibility.Visible;
        } catch (Exception error) {
            Log(error);
            analysisChatGptStatus.Text = error.Message;
            SetAnalysisBusy(false);
            return;
        }
        SetAnalysisBusy(false);
        await RunNativeChatGptAsync("chatgpt-handoff");
    }

    async Task RunNativeChatGptAsync(string type)
    {
        if (chatgptExport is not { } export || analysisBusy) return;
        SetAnalysisBusy(true);
        analysisChatGptStatus.Text = type switch {
            "chatgpt-handoff" => "正在复用 Chrome 并附加文件，请不要切换窗口…",
            "chatgpt-copy-question" => "正在复制提问…",
            "chatgpt-delete-temp" => "正在删除本机临时文件…",
            "chatgpt-open-export" => "正在打开本地文件…",
            _ => "正在处理…",
        };
        try {
            var result = await RunChatGptActionAsync(type, export.FilePath, export.Sha256, export.Message);
            if (result is not { } done) { analysisChatGptStatus.Text = "另一项 Claude 操作仍在进行，请稍候。"; return; }
            analysisChatGptStatus.Text = done.Message;
            if (done.Deleted) { chatgptExport = null; analysisChatGptPrepared.Visibility = Visibility.Collapsed; }
            if (!done.Ok) NotifyNative(done.Message, true);
        } finally {
            SetAnalysisBusy(false);
        }
    }

    async Task ConfirmDeleteChatGptTempAsync()
    {
        if (chatgptExport == null || analysisBusy) return;
        var dialog = new ContentDialog {
            XamlRoot = layout.XamlRoot,
            Title = "删除本机临时文件？",
            Content = "请先在 Claude 确认附件已经出现。已上传到 Claude 的副本不受影响。",
            PrimaryButtonText = "删除", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close,
        };
        ConfigureLedgerDialog(dialog);
        if (await dialog.ShowAsync() == ContentDialogResult.Primary) await RunNativeChatGptAsync("chatgpt-delete-temp");
    }

    // DeepSeek: structured analysis rendered with native text blocks.
    static string AnalysisModeLabel(string mode) => mode switch { "month" => "月度花销", "year" => "年度花销", _ => "总结与趋势" };

    string AnalysisTitle(string mode)
    {
        var month = Period(analysisPeriods, "latest_month", "");
        var year = Period(analysisPeriods, "latest_year", "");
        if (mode == "month" && month.Length == 7 && int.TryParse(month[5..], out var number)) return $"{month[..4]}年{number}月 · 月度花销分析";
        if (mode == "year" && year.Length > 0) return $"{year}年 · 年度花销分析";
        return mode == "trend" ? "总结与趋势分析" : AnalysisModeLabel(mode) + "分析";
    }

    async Task RunDeepSeekAsync(string mode)
    {
        SetAnalysisBusy(true);
        analysisResultBody.Children.Clear();
        analysisResultTitle.Text = AnalysisTitle(mode);
        analysisResultMeta.Text = "正在整理商品、商户、金额和付款方式…";
        analysisResultLoading.IsActive = true;
        try {
            RenderAnalysisResult(await backend!.PostJsonAsync("/api/analysis/run", new { mode }));
        } catch (Exception error) {
            Log(error);
            analysisResultTitle.Text = "分析未完成";
            analysisResultMeta.Text = error.Message;
            NotifyNative(error.Message, true);
        } finally {
            analysisResultLoading.IsActive = false;
            SetAnalysisBusy(false);
        }
    }

    void RenderAnalysisResult(JsonElement payload)
    {
        var result = payload.TryGetProperty("result", out var r) ? r : default;
        var meta = payload.TryGetProperty("meta", out var m) ? m : default;
        analysisResultTitle.Text = LedgerLabels.Text(result, "title", analysisResultTitle.Text);
        var parts = new List<string> {
            LedgerLabels.Text(meta, "period", ""),
            $"{LedgerLabels.Text(meta, "orders", "0")} 笔",
            "CAD " + (LedgerLabels.Number(meta, "total") ?? 0m).ToString("N2", CultureInfo.InvariantCulture),
        };
        var asOf = LedgerLabels.Text(meta, "as_of_date", "");
        if (asOf.Length > 0) parts.Add("最近消费 " + asOf);
        if (meta.ValueKind == JsonValueKind.Object && meta.TryGetProperty("latest_month_complete", out var complete) && complete.ValueKind == JsonValueKind.False)
            parts.Add("含未结束月份");
        if (LedgerLabels.Number(meta, "item_detail_orders") is decimal detail && LedgerLabels.Number(meta, "quality_scope_orders") is decimal scope && scope > 0)
            parts.Add($"商品明细 {detail:0}/{scope:0} 笔");
        if (payload.TryGetProperty("cached", out var cached) && cached.ValueKind == JsonValueKind.True) parts.Add("已复用");
        analysisResultMeta.Text = string.Join(" · ", parts.Where(part => part.Length > 0));

        analysisResultBody.Children.Clear();
        var summary = LedgerLabels.Text(result, "summary", "");
        if (summary.Length > 0) analysisResultBody.Children.Add(new TextBlock { Text = summary, TextWrapping = TextWrapping.Wrap, FontSize = 16, IsTextSelectionEnabled = true });
        foreach (var (key, title) in new[] { ("highlights", "重点发现"), ("trends", "变化趋势"), ("forecast", "轻量预测"), ("suggestions", "可执行建议"), ("limitations", "数据限制") }) {
            var items = LedgerLabels.Rows(result, key).Select(item => item.ToString()).Where(item => item.Length > 0).ToList();
            if (items.Count == 0) continue;
            var section = new StackPanel { Spacing = 6 };
            section.Children.Add(new TextBlock { Text = title, FontWeight = FontWeights.SemiBold, FontSize = 16 });
            foreach (var item in items) section.Children.Add(MarkdownLine("• " + item));
            analysisResultBody.Children.Add(section);
        }
        if (Evidence(payload) is { } evidence) analysisResultBody.Children.Add(evidence);
    }

    async Task SendAnalysisChatAsync()
    {
        if (analysisBusy || backend == null || !deepseekConfigured) return;
        var question = analysisChatInput.Text.Trim();
        if (question.Length == 0) { NotifyNative("请输入要询问的问题。"); analysisChatInput.Focus(FocusState.Programmatic); return; }
        SetAnalysisBusy(true);
        analysisChatLog.Children.Add(ChatMessage("你", Markdown(question)));
        var pending = ChatMessage("DeepSeek", new TextBlock { Text = "正在读取全部账本…", Foreground = SecondaryText() });
        analysisChatLog.Children.Add(pending);
        try {
            var payload = await backend.PostJsonAsync("/api/analysis/chat", new { question, history = analysisChatHistory.TakeLast(10).ToArray() });
            analysisChatLog.Children.Remove(pending);
            var meta = payload.TryGetProperty("meta", out var m) ? m : default;
            var source = LedgerLabels.Text(meta, "source", "");
            var answer = LedgerLabels.Text(payload, "answer", "");
            var body = new StackPanel { Spacing = 8 };
            body.Children.Add(Markdown(answer));
            var notes = new List<string> { LedgerLabels.Text(meta, "period", "") };
            if (source == "computed") notes.Add("本地计算");
            if (source == "computed_fallback") notes.Add("本地摘要 · AI 解读未通过校验");
            if (payload.TryGetProperty("cached", out var cached) && cached.ValueKind == JsonValueKind.True) notes.Add("已复用");
            if (notes.Any(note => note.Length > 0))
                body.Children.Add(new TextBlock { Text = string.Join(" · ", notes.Where(note => note.Length > 0)), Foreground = SecondaryText(), TextWrapping = TextWrapping.Wrap });
            if (Evidence(payload) is { } evidence) body.Children.Add(evidence);
            analysisChatLog.Children.Add(ChatMessage(source == "computed" ? "账本计算" : "DeepSeek", body));
            analysisChatHistory.Add(new() { ["role"] = "user", ["content"] = Clip(question) });
            analysisChatHistory.Add(new() { ["role"] = "assistant", ["content"] = Clip(answer.Length > 0 ? answer : "（无回答）") });
            while (analysisChatHistory.Count > 10) analysisChatHistory.RemoveAt(0);
            analysisChatInput.Text = "";
        } catch (Exception error) {
            Log(error);
            analysisChatLog.Children.Remove(pending);
            analysisChatLog.Children.Add(ChatMessage("DeepSeek", new TextBlock { Text = "发送未完成，可重试：" + error.Message, TextWrapping = TextWrapping.Wrap }));
        } finally {
            SetAnalysisBusy(false);
            analysisChatInput.Focus(FocusState.Programmatic);
        }
    }

    static string Clip(string value) => value.Length <= 1000 ? value : value[..1000];

    // Uses the ledger palette so appearance changes recolor generated text as well.
    Brush SecondaryText() => (Brush)layout.Resources["LedgerSecondaryTextBrush"];

    UIElement ChatMessage(string label, UIElement body)
    {
        var panel = new StackPanel { Spacing = 4 };
        panel.Children.Add(new TextBlock { Text = label, FontWeight = FontWeights.SemiBold, Foreground = SecondaryText() });
        panel.Children.Add(body);
        return panel;
    }

    // Evidence rows open the native order detail instead of a browser tab.
    UIElement? Evidence(JsonElement payload)
    {
        if (!payload.TryGetProperty("evidence", out var evidence) || evidence.ValueKind != JsonValueKind.Object) return null;
        var rows = LedgerLabels.Rows(evidence, "rows").ToList();
        var truncated = evidence.TryGetProperty("truncated", out var t) && t.ValueKind == JsonValueKind.True;
        var content = new StackPanel { Spacing = 4 };
        content.Children.Add(new TextBlock { Text = LedgerLabels.Text(evidence, "basis", "本次分析可用的订单记录"), TextWrapping = TextWrapping.Wrap, Foreground = SecondaryText() });
        foreach (var row in rows) {
            if (!row.TryGetProperty("order_pk", out var key) || !key.TryGetInt64(out var id) || id <= 0) continue;
            var link = new HyperlinkButton {
                Content = $"{LedgerLabels.Text(row, "date", "")} · {LedgerLabels.Text(row, "merchant", LedgerLabels.Text(row, "platform", ""))} · CAD {(LedgerLabels.Number(row, "total") ?? 0m).ToString("N2", CultureInfo.InvariantCulture)}",
                Padding = new Thickness(0, 2, 0, 2),
            };
            link.Click += async (_, _) => await OpenOrdersFromWebAsync("all", id);
            content.Children.Add(link);
        }
        foreach (var row in LedgerLabels.Rows(payload, "data_status")) {
            var coverage = LedgerLabels.Text(row, "coverage", "") == "bounded" ? "有限范围检查" : "覆盖范围未知";
            content.Children.Add(new TextBlock {
                Text = $"{LedgerLabels.Text(row, "label", "")}：最近成功检查 {LedgerLabels.Text(row, "last_success_at", "尚无记录")}；最近消费 {LedgerLabels.Text(row, "last_order_date", "无记录")}；{coverage}",
                TextWrapping = TextWrapping.Wrap, Foreground = SecondaryText(),
            });
        }
        return new Expander {
            Header = $"查看账本依据 · {rows.Count} 笔{(truncated ? "（仅显示部分）" : "")}",
            Content = content, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch,
        };
    }

    // Minimal Markdown: headings, bullets, **bold** and pipe tables, which is what the analysis answers use.
    UIElement Markdown(string markdown)
    {
        var panel = new StackPanel { Spacing = 6 };
        var lines = (markdown ?? "").Replace("\r\n", "\n").Split('\n');
        for (var i = 0; i < lines.Length; i++) {
            var line = lines[i].TrimEnd();
            if (line.TrimStart().StartsWith('|')) {
                var table = new List<string>();
                while (i < lines.Length && lines[i].TrimStart().StartsWith('|')) table.Add(lines[i++].Trim());
                i--;
                panel.Children.Add(MarkdownTable(table));
            } else if (line.Length == 0) {
                continue;
            } else if (line.StartsWith('#')) {
                var text = line.TrimStart('#').Trim();
                panel.Children.Add(new TextBlock { Text = text.Replace("**", ""), FontWeight = FontWeights.SemiBold, FontSize = 16, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
            } else {
                var trimmed = line.TrimStart();
                panel.Children.Add(MarkdownLine(trimmed.StartsWith("- ") || trimmed.StartsWith("* ") ? "• " + trimmed[2..] : line));
            }
        }
        return panel;
    }

    static TextBlock MarkdownLine(string text)
    {
        var block = new TextBlock { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        var bold = false;
        foreach (var part in text.Split("**")) {
            if (part.Length > 0) {
                var run = new Run { Text = part };
                if (bold) run.FontWeight = FontWeights.SemiBold;
                block.Inlines.Add(run);
            }
            bold = !bold;
        }
        return block;
    }

    UIElement MarkdownTable(List<string> lines)
    {
        static string[] Cells(string line) => line.Trim().Trim('|').Split('|').Select(cell => cell.Trim()).ToArray();
        var rows = lines.Where(line => !Cells(line).All(cell => cell.Length > 0 && cell.Trim(':', '-').Length == 0)).Select(Cells).ToList();
        var columns = rows.Count == 0 ? 0 : rows.Max(row => row.Length);
        var grid = new Grid { ColumnSpacing = 16, RowSpacing = 4 };
        for (var c = 0; c < columns; c++) grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        for (var r = 0; r < rows.Count; r++) {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            for (var c = 0; c < rows[r].Length; c++) {
                var cell = MarkdownLine(rows[r][c]);
                if (r == 0) cell.FontWeight = FontWeights.SemiBold;
                Grid.SetRow(cell, r); Grid.SetColumn(cell, c);
                grid.Children.Add(cell);
            }
        }
        return new ScrollViewer { Content = grid, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollMode = ScrollMode.Auto, VerticalScrollMode = ScrollMode.Disabled };
    }
}
