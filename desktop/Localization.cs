using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Expenses.Desktop;

// Interface language. Chinese is the source text everywhere; English comes from one shared
// dictionary (backend/app/static/i18n-en.json) that the WebView pages use as well. Keys are
// exact Chinese strings; keys with {0}, {1}... are patterns whose captures are translated in
// turn. Ledger data (merchant and item names, notes, categories) is never in the dictionary,
// so it always shows as recorded.
internal static class Loc
{
    public const string DictionaryPath = "backend/app/static/i18n-en.json";
    static readonly Regex Cjk = new(@"[㐀-鿿]", RegexOptions.Compiled);
    static readonly Regex Slot = new(@"\{(\d+)\}", RegexOptions.Compiled);
    static Dictionary<string, string> exact = new(StringComparer.Ordinal);
    static List<(Regex Pattern, string English, int[] Order)> patterns = new();
    static readonly Dictionary<string, string> cache = new(StringComparer.Ordinal);
    static readonly (string Split, string Join)[] Separators = { ("\n", "\n"), (" · ", " · "), ("；", "; "), ("、", ", ") };

    public static string Language { get; private set; } = "zh";
    public static bool English => Language == "en";
    public static event Action? Changed;

    public static void Load(string root) => LoadJson(File.ReadAllText(Path.Combine(root, DictionaryPath)));

    public static void LoadJson(string json)
    {
        var map = JsonSerializer.Deserialize<Dictionary<string, string>>(json) ?? new();
        exact = new(StringComparer.Ordinal);
        var built = new List<(Regex, string, int[])>();
        foreach (var (zh, en) in map) {
            if (zh.StartsWith("//", StringComparison.Ordinal) || string.IsNullOrEmpty(en)) continue;
            if (Slot.IsMatch(zh)) built.Add(Compile(zh, en));
            else {
                exact[zh] = en;
                // "已选择：" also covers "已选择：file.csv".
                if (zh.EndsWith('：')) built.Add(Compile(zh + "{0}", en.TrimEnd() + " {0}"));
            }
        }
        // Longer literal text first, so the most specific pattern wins.
        patterns = built.OrderByDescending(entry => Slot.Replace(entry.Item1.ToString(), "").Length).ToList();
        lock (cache) cache.Clear();
    }

    static (Regex, string, int[]) Compile(string zh, string en)
    {
        var expression = new StringBuilder("^");
        var order = new List<int>();
        var last = 0;
        foreach (Match slot in Slot.Matches(zh)) {
            expression.Append(Regex.Escape(zh[last..slot.Index])).Append("(.+?)");
            order.Add(int.Parse(slot.Groups[1].Value));
            last = slot.Index + slot.Length;
        }
        expression.Append(Regex.Escape(zh[last..])).Append('$');
        return (new Regex(expression.ToString(), RegexOptions.Singleline | RegexOptions.CultureInvariant), en, order.ToArray());
    }

    public static void SetLanguage(string? language)
    {
        var next = language == "en" ? "en" : "zh";
        if (next == Language) return;
        Language = next;
        lock (cache) cache.Clear();
        Changed?.Invoke();
    }

    // Translate interface text; anything not in the dictionary (ledger data) is returned unchanged.
    public static string T(string? text)
    {
        if (string.IsNullOrEmpty(text) || !English || !Cjk.IsMatch(text)) return text ?? "";
        lock (cache) if (cache.TryGetValue(text, out var known)) return known;
        var result = Translate(text, 0);
        lock (cache) { if (cache.Count > 4000) cache.Clear(); cache[text] = result; }
        return result;
    }

    static string Translate(string text, int depth)
    {
        if (depth > 4 || !Cjk.IsMatch(text)) return text;
        var trimmed = text.Trim();
        if (trimmed.Length == 0) return text;
        var lead = text[..text.IndexOf(trimmed, StringComparison.Ordinal)];
        var trail = text[(lead.Length + trimmed.Length)..];
        if (exact.TryGetValue(trimmed, out var direct)) return lead + direct + trail;
        foreach (var (pattern, english, order) in patterns) {
            var match = pattern.Match(trimmed);
            if (!match.Success) continue;
            var values = new string[order.Length == 0 ? 0 : order.Max() + 1];
            for (var i = 0; i < order.Length; i++) values[order[i]] = Translate(match.Groups[i + 1].Value, depth + 1);
            return lead + Slot.Replace(english, slot => int.Parse(slot.Groups[1].Value) is var index && index < values.Length ? values[index] ?? "" : slot.Value) + trail;
        }
        // Composite labels ("A · B", lists, several lines) are translated part by part.
        foreach (var (separator, joiner) in Separators) {
            if (!trimmed.Contains(separator, StringComparison.Ordinal)) continue;
            var parts = trimmed.Split(separator);
            var translated = parts.Select(part => Translate(part, depth + 1)).ToArray();
            if (!translated.SequenceEqual(parts)) return lead + string.Join(joiner, translated) + trail;
        }
        return text;
    }
}
