using System.Text.Json.Serialization;

namespace Expenses.Desktop;

public sealed class LedgerAppearance
{
    [JsonPropertyName("wallpaper")] public string Wallpaper { get; set; } = "cloud";
    [JsonPropertyName("color")] public string Color { get; set; } = "auto";
    [JsonPropertyName("solid")] public bool Solid { get; set; }
    [JsonPropertyName("reducedMotion")] public bool ReducedMotion { get; set; }

    public LedgerAppearance Normalize() => new()
    {
        Wallpaper = NormalizeId(Wallpaper) is "cloud" or "ink" or "jade" or "indigo" or "none" ? NormalizeId(Wallpaper)! : "cloud",
        Color = NormalizeId(Color) is "cloud" or "ink" or "jade" or "indigo" or "auto" ? NormalizeId(Color)! : "auto",
        Solid = Solid,
        ReducedMotion = ReducedMotion,
    };

    // A selected wallpaper never becomes a caller-supplied filesystem path.
    // Solid controls surface transparency; only "none" removes the wallpaper.
    [JsonIgnore]
    public string? WallpaperFileName => Normalize().Wallpaper switch
    {
        "cloud" => "pexels-dreamypixel-552784.jpg",
        "ink" => "pexels-adrien-olichon-1257089-2387819.jpg",
        "jade" => "pexels-evelin-magnus-34540965.jpg",
        "indigo" => "pexels-steve-29586678.jpg",
        _ => null,
    };

    [JsonIgnore]
    public string EffectiveColor
    {
        get
        {
            var appearance = Normalize();
            return appearance.Color == "auto"
                ? appearance.Wallpaper == "none" ? "cloud" : appearance.Wallpaper
                : appearance.Color;
        }
    }

    static string? NormalizeId(string? value) => value switch
    {
        "alpine" => "cloud",
        "snow" => "indigo",
        "lake" => "jade",
        "coast" => "ink",
        _ => value,
    };
}
