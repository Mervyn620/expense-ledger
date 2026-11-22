using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Expenses.Desktop;
internal sealed class DesktopPreferences
{
    // Retained for old preference files; manual brightness is no longer a setting.
    public string Theme { get; set; } = "system";
    [JsonIgnore]
    // 0.17: every palette is dark glass (the bright lake wallpaper and its light palette were replaced).
    public string UiTone => "dark";
    public LedgerAppearance Appearance { get; set; } = new();
    [JsonIgnore]
    public bool HasAppearanceCache { get; set; }
    public bool CompletionNotifications { get; set; } = true;
    public string VisualMode { get; set; } = "static";
    public bool GameModeEnabled { get; set; } = true; // Preserve the existing optional starfield game on upgrade.
    public string[] DefaultBillPlatforms { get; set; } = BillPlatformPreferences.Defaults();
    public string AnalysisProvider { get; set; } = "chatgpt";
    // Three interface sizes for native pages and the WebView: small 90%, medium 100%, large 115%.
    public string UiScale { get; set; } = "medium";
    // Interface language: "zh" or "en"; empty until chosen on first launch.
    public string Language { get; set; } = "";
    [JsonIgnore]
    public double UiScaleFactor => UiScale switch { "small" => 0.9, "large" => 1.15, _ => 1.0 };
    public static DesktopPreferences Load(string root)
    {
        try {
            var source = File.ReadAllText(Path.Combine(root, ".desktop-state", "preferences.json"));
            var value = JsonSerializer.Deserialize<DesktopPreferences>(source) ?? new();
            using var document = JsonDocument.Parse(source);
            value.HasAppearanceCache = document.RootElement.ValueKind == JsonValueKind.Object && document.RootElement.TryGetProperty("Appearance",out var cache) && cache.ValueKind == JsonValueKind.Object;
            value.Theme = "system";
            value.DefaultBillPlatforms = BillPlatformPreferences.Normalize(value.DefaultBillPlatforms);
            value.Appearance = (value.Appearance ?? new()).Normalize();
            if (value.VisualMode is not ("static" or "particle")) value.VisualMode = "static";
            if (value.AnalysisProvider is not ("chatgpt" or "deepseek")) value.AnalysisProvider = "chatgpt";
            if (value.UiScale is not ("small" or "medium" or "large")) value.UiScale = "medium";
            if (value.Language is not ("zh" or "en")) value.Language = "";
            return value;
        } catch (Exception error) when (error is IOException or JsonException or UnauthorizedAccessException) { return new(); }
    }
    public void Save(string root)
    {
        Theme = "system";
        DefaultBillPlatforms = BillPlatformPreferences.Normalize(DefaultBillPlatforms);
        var directory = Path.Combine(root, ".desktop-state");
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "preferences.json");
        var temp = path + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(this));
        File.Move(temp, path, true);
    }
}
