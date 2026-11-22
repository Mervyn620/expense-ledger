using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;

namespace Expenses.Desktop;

internal sealed class BackendService : IDisposable
{
    readonly string root;
    readonly string token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
    readonly string instance = Guid.NewGuid().ToString("N");
    readonly HttpClient client = new() { Timeout = TimeSpan.FromSeconds(4) };
    // DeepSeek analysis and export preparation can take far longer than ordinary ledger reads.
    readonly HttpClient slowClient = new() { Timeout = TimeSpan.FromSeconds(200) };
    Process? process;
    OwnedProcessJob? job;
    string? readyPath;
    public string? Url { get; private set; }
    public bool Running => process != null && !process.HasExited;
    public BackendService(string root) => this.root = root;

    // A crash or forced stop skips Dispose, leaving ready files behind. Remove only files whose
    // service process no longer exists, so another running instance keeps its own file.
    static void RemoveStaleReadyFiles(string state)
    {
        foreach (var file in Directory.EnumerateFiles(state, "ready-*.json")) {
            try {
                using var ready = JsonDocument.Parse(File.ReadAllText(file));
                var pid = ready.RootElement.TryGetProperty("pid", out var value) && value.TryGetInt32(out var number) ? number : 0;
                var alive = false;
                if (pid > 0) { try { using var process = Process.GetProcessById(pid); alive = !process.HasExited; } catch (ArgumentException) { } }
                if (!alive) File.Delete(file);
            } catch (Exception error) when (error is IOException or JsonException or UnauthorizedAccessException or InvalidOperationException) { }
        }
    }
    public async Task StartAsync()
    {
        var backend = Path.Combine(root, "backend");
        var runtime = EmbeddedRuntime.Find(root);
        var python = runtime?.Python ?? Path.Combine(backend, ".app-runtime", "Scripts", "python.exe");
        if (!File.Exists(python)) throw new FileNotFoundException("桌面版运行环境缺失，请运行项目中的 tools/setup-runtime.ps1。");
        var state = Path.Combine(root, ".desktop-state");
        Directory.CreateDirectory(state);
        RemoveStaleReadyFiles(state);
        readyPath = Path.Combine(state, "ready-" + instance + ".json");
        var start = new ProcessStartInfo(python) {
            WorkingDirectory = backend, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        foreach (var arg in new[] { "-m", "app.desktop_host", "--ready", readyPath }) start.ArgumentList.Add(arg);
        start.Environment["EXPENSE_DESKTOP_TOKEN"] = token;
        start.Environment["EXPENSE_DESKTOP_INSTANCE"] = instance;
        start.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        runtime?.Apply(start.Environment);
        job = new OwnedProcessJob();
        process = Process.Start(start) ?? throw new Exception("无法启动账本服务。");
        try { job.Assign(process); } catch { process.Kill(true); throw; }
        process.OutputDataReceived += (_, e) => Log(e.Data);
        process.ErrorDataReceived += (_, e) => Log(e.Data);
        process.BeginOutputReadLine(); process.BeginErrorReadLine();
        client.DefaultRequestHeaders.Add("X-Desktop-Token", token);
        slowClient.DefaultRequestHeaders.Add("X-Desktop-Token", token);
        var timer = Stopwatch.StartNew();
        while (timer.Elapsed < TimeSpan.FromSeconds(45))
        {
            if (process.HasExited) throw new Exception("账本服务提前退出，请查看维护日志。");
            if (File.Exists(readyPath))
            {
                try
                {
                    using var ready = JsonDocument.Parse(await File.ReadAllTextAsync(readyPath));
                    if (ready.RootElement.GetProperty("instance").GetString() == instance)
                    {
                        var url = ready.RootElement.GetProperty("url").GetString()!;
                        var uri = new Uri(url);
                        if (uri.Scheme != "http" || uri.Host != "127.0.0.1")
                            throw new InvalidDataException("服务地址校验失败。");
                        Url = url;
                        using var status = await StatusAsync();
                        if (status.RootElement.GetProperty("instance").GetString() == instance &&
                            string.Equals(status.RootElement.GetProperty("workspace").GetString(), backend, StringComparison.OrdinalIgnoreCase))
                            return;
                    }
                }
                catch (HttpRequestException) { }
                catch (IOException) { }
                catch (JsonException) { }
                catch (TaskCanceledException) { }
            }
            await Task.Delay(150);
        }
        throw new TimeoutException("账本服务启动超时，可重试或打开日志检查。");
    }
    void Log(string? message)
    {
        if (string.IsNullOrWhiteSpace(message)) return;
        try { lock (client) File.AppendAllText(Path.Combine(root, ".desktop-state", "backend.log"), message + Environment.NewLine); }
        catch (IOException) { }
    }
    public Task<T> LedgerAsync<T>(string path, System.Threading.CancellationToken cancellation = default) =>
        LedgerApi.ReadAsync<T>(client, Url ?? throw new InvalidOperationException("账本服务尚未连接。"), path, cancellation);
    public Task<CashNoteResult> UpdateCashNoteAsync(long bankTransactionPk, string note, System.Threading.CancellationToken cancellation = default) =>
        LedgerApi.UpdateCashNoteAsync<CashNoteResult>(client, Url ?? throw new InvalidOperationException("账本服务尚未连接。"), bankTransactionPk, note, cancellation);
    public Task<ManualWechatPayment> SaveManualWechatAsync(long? id, ManualWechatDraft draft) =>
        LedgerApi.WriteManualWechatAsync<ManualWechatPayment>(client, Url ?? throw new InvalidOperationException("账本服务尚未连接。"), id is null ? HttpMethod.Post : HttpMethod.Put, id, draft);
    public Task<JsonElement> DeleteManualWechatAsync(long id) =>
        LedgerApi.WriteManualWechatAsync<JsonElement>(client, Url ?? throw new InvalidOperationException("账本服务尚未连接。"), HttpMethod.Delete, id, null);
    public async Task<JsonElement> PostJsonAsync(string path, object body, System.Threading.CancellationToken cancellation = default)
    {
        var url = Url ?? throw new InvalidOperationException("账本服务尚未连接。");
        using var request = new HttpRequestMessage(HttpMethod.Post, url + path);
        request.Headers.Add("Origin", url);
        request.Content = new StringContent(JsonSerializer.Serialize(body), System.Text.Encoding.UTF8, "application/json");
        try {
            using var response = await slowClient.SendAsync(request, cancellation);
            var text = await response.Content.ReadAsStringAsync(cancellation);
            JsonElement payload = default;
            try { using var document = JsonDocument.Parse(text); payload = document.RootElement.Clone(); } catch (JsonException) { }
            if (!response.IsSuccessStatusCode)
                throw new InvalidOperationException(payload.ValueKind == JsonValueKind.Object
                    ? LedgerLabels.Text(payload, "detail", $"请求未完成（HTTP {(int)response.StatusCode}），请稍后重试。")
                    : $"请求未完成（HTTP {(int)response.StatusCode}），请稍后重试。");
            if (payload.ValueKind != JsonValueKind.Object) throw new InvalidDataException("账本返回了无法识别的结果，请重试。");
            return payload;
        } catch (TaskCanceledException) when (!cancellation.IsCancellationRequested) {
            throw new TimeoutException("请求超时，请稍后重试。");
        }
    }
    public async Task<JsonDocument> StatusAsync() =>
        JsonDocument.Parse(await client.GetStringAsync(Url + "/api/desktop/status"));
    public Task<JsonElement> CollectorBackendsAsync() => LedgerAsync<JsonElement>("/api/collector-backends");
    public async Task<JsonElement> UseLocalCollectorAsync()
    {
        using var request = new HttpRequestMessage(HttpMethod.Patch, Url + "/api/collector-backends");
        request.Headers.Add("Origin", Url);
        request.Content = new StringContent("{\"mode\":\"local\"}", System.Text.Encoding.UTF8, "application/json");
        using var response = await client.SendAsync(request);
        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException(LedgerLabels.Text(body.RootElement,"detail","运行位置暂时无法切换，请稍后重试。"));
        return body.RootElement.Clone();
    }
    public async Task<JsonDocument> ValidateAsync()
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, Url + "/api/jobs/validate");
        request.Headers.Add("Origin", Url);
        using var response = await client.SendAsync(request);
        if (response.StatusCode == HttpStatusCode.Conflict) throw new InvalidOperationException("已有任务正在运行，请完成后再校验。");
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(await response.Content.ReadAsStringAsync());
    }
    public async Task<bool> StopAsync(bool force)
    {
        if (!Running) return true;
        if (Url == null) { Dispose(); return true; }
        try {
            using var response = await client.PostAsync(Url + "/api/desktop/shutdown?force=" + force.ToString().ToLowerInvariant(), null);
            if (response.StatusCode == HttpStatusCode.Conflict) return false;
            response.EnsureSuccessStatusCode();
        } catch when (force) { }
        if (process != null) await Task.WhenAny(process.WaitForExitAsync(), Task.Delay(15000));
        Dispose();
        return true;
    }
    public void Dispose()
    {
        job?.Dispose(); job = null;
        process?.Dispose(); process = null;
        client.Dispose();
        slowClient.Dispose();
        if (readyPath != null) { try { File.Delete(readyPath); } catch (IOException) { } }
    }
}

