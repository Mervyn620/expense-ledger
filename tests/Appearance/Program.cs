using System.Text.Json;
using System.Text.RegularExpressions;
using Expenses.Desktop;

var checks = new List<string>();
void Check(bool condition, string name)
{
    if (!condition) throw new Exception(name);
    checks.Add(name);
}
Check(Enumerable.Range(0,160).All(i => new[] {StarfieldTimeline.GatherEnd,StarfieldTimeline.OrbitEnd,StarfieldTimeline.ScatterEnd}.All(t => {
    const double h=.0001;
    var left=StarfieldTimeline.Sample(i,160,1300,850,t-h); var mid=StarfieldTimeline.Sample(i,160,1300,850,t); var right=StarfieldTimeline.Sample(i,160,1300,850,t+h);
    return Math.Abs((mid.X-left.X)/h-(right.X-mid.X)/h)<.5 && Math.Abs((mid.Y-left.Y)/h-(right.Y-mid.Y)/h)<.5;
})), "launch_velocity_is_continuous_at_each_stage");
LedgerAppearance Read(string json) => JsonSerializer.Deserialize<LedgerAppearance>(json)!.Normalize();
var defaults = new LedgerAppearance();
Check(BillPlatformPreferences.Defaults().SequenceEqual(new[]{"walmart_ca","fantuan"}), "bill_defaults_are_walmart_fantuan");
Check(BillPlatformPreferences.Normalize(new[]{"tnt_ca","unknown","fantuan","fantuan"}).SequenceEqual(new[]{"fantuan","tnt_ca"}), "bill_preferences_allowlist_and_deduplicate");
Check(BillPlatformPreferences.Normalize(Array.Empty<string>()).Length == 0, "empty_bill_defaults_are_preserved");
var billDirectory = Directory.CreateTempSubdirectory("expenses-bill-preferences-");
try {
    var prefs = new DesktopPreferences { DefaultBillPlatforms = new[]{"tnt_ca","uber_eats"}, GameModeEnabled = false, Appearance = new LedgerAppearance{Wallpaper="ink"} };
    prefs.Save(billDirectory.FullName);
    var reloaded = DesktopPreferences.Load(billDirectory.FullName);
    Check(reloaded.DefaultBillPlatforms.SequenceEqual(new[]{"uber_eats","tnt_ca"}) && !reloaded.GameModeEnabled && reloaded.Appearance.Wallpaper=="ink", "bill_defaults_persist_without_changing_appearance_or_game");
} finally { billDirectory.Delete(true); }
var timelineStages = new[] {StarfieldTimeline.GatherEnd, StarfieldTimeline.OrbitEnd, StarfieldTimeline.ScatterEnd};
Check(timelineStages.Zip(timelineStages.Skip(1), (a, b) => a < b).All(value => value) && StarfieldTimeline.RevealAt > StarfieldTimeline.ScatterEnd, "launch_stages_run_in_order_before_reveal");
Check(Enumerable.Range(0, 160).All(index => timelineStages.All(stage => {
    var before = StarfieldTimeline.Sample(index, 160, 1300, 850, stage - 0.0001);
    var after = StarfieldTimeline.Sample(index, 160, 1300, 850, stage + 0.0001);
    return Math.Abs(before.X - after.X) < 2 && Math.Abs(before.Y - after.Y) < 2 && Math.Abs(before.Opacity - after.Opacity) < 0.3;
})), "launch_stage_boundaries_do_not_jump_or_flash");
// 0.18.0 sky: drift and meteors. Reference values come from desktop-starfield.js, so the Web and native skies stay in step.
bool Near(double a, double b, double tolerance = 1e-6) => Math.Abs(a - b) < tolerance;
Check(Enumerable.Range(0, 160).All(i => StarfieldTimeline.Drift(i, StarfieldTimeline.RevealAt) == (0, 0)), "drift_starts_at_zero_after_launch");
Check(Near(StarfieldTimeline.Drift(0, 36.25).X, 8.632093666488737) && Near(StarfieldTimeline.Drift(0, 36.25).Y, -8.199476902833313)
    && Near(StarfieldTimeline.Drift(7, 100).X, 11.638759111409547) && Near(StarfieldTimeline.Drift(50, 1000).Y, 0.4010832804121651), "native_drift_matches_web_drift");
Check(Enumerable.Range(0, 160).All(i => Enumerable.Range(0, 200).All(s => {
    var (x, y) = StarfieldTimeline.Drift(i, StarfieldTimeline.RevealAt + s * 3.1);
    return Math.Abs(x) <= 20 && Math.Abs(y) <= 14;
})), "drift_stays_small");
var meteor = StarfieldTimeline.Meteor(1200, 800, 26.36);
Check(meteor is { } m && Near(m.HeadX, 836.1452271804325, 1e-4) && Near(m.HeadY, 174.69362326845982, 1e-4) && Near(m.Length, 169.46501308593116, 1e-4) && Near(m.Opacity, 0.8034019095631955, 1e-4), "native_meteor_matches_web_meteor");
Check(Enumerable.Range(0, 83).All(i => StarfieldTimeline.Meteor(1200, 800, i * 0.1) == null), "no_meteor_during_launch");
var meteorFrames = Enumerable.Range(0, 2200).Count(i => StarfieldTimeline.Meteor(1200, 800, 8.3 + i * 0.1) != null);
Check(meteorFrames > 0 && meteorFrames < 2200 * 0.15, "meteors_are_brief_and_occasional");
var preferenceFolder = Directory.CreateTempSubdirectory("expenses-visual-");
try {
    var preferenceRoot = preferenceFolder.FullName;
    Check(DesktopPreferences.Load(preferenceRoot).VisualMode == "static", "legacy_settings_default_to_static_mode");
    Check(DesktopPreferences.Load(preferenceRoot).GameModeEnabled, "legacy_preferences_keep_existing_game_available");
    var disabledGame = new DesktopPreferences {GameModeEnabled = false, VisualMode = "particle", Appearance = new LedgerAppearance {Wallpaper = "jade"}};
    Directory.CreateDirectory(Path.Combine(preferenceRoot,".desktop-state"));
    File.WriteAllText(Path.Combine(preferenceRoot,".desktop-state","preferences.json"),"{\"Theme\":\"dark\"}");
    Check(!DesktopPreferences.Load(preferenceRoot).HasAppearanceCache,"legacy_preferences_without_appearance_keep_web_migration");
    disabledGame.Save(preferenceRoot);
    Check(DesktopPreferences.Load(preferenceRoot).HasAppearanceCache,"saved_native_appearance_is_authoritative_after_restart");
    Check(DesktopPreferences.Load(preferenceRoot) is {GameModeEnabled: false, VisualMode: "particle", Appearance.Wallpaper: "jade"}, "game_opt_out_survives_restart_without_changing_wallpaper");
    var preference = new DesktopPreferences {VisualMode = "particle", Appearance = new LedgerAppearance {Wallpaper = "jade"}};
    preference.Save(preferenceRoot);
    Check(DesktopPreferences.Load(preferenceRoot) is {GameModeEnabled: true, VisualMode: "particle", Appearance.Wallpaper: "jade"}, "particle_mode_persists_without_replacing_wallpaper");
    foreach (var legacyTheme in new[]{"light","dark","system"}) {
        File.WriteAllText(Path.Combine(preferenceRoot,".desktop-state","preferences.json"),
            JsonSerializer.Serialize(new DesktopPreferences {Theme=legacyTheme, VisualMode="static", GameModeEnabled=false, Appearance=new LedgerAppearance {Wallpaper="jade", Solid=true}}));
        var loaded=DesktopPreferences.Load(preferenceRoot);
        Check(loaded.Theme=="system" && loaded.UiTone=="dark" && !loaded.GameModeEnabled && loaded.Appearance.Solid && loaded.Appearance.Wallpaper=="jade", "old_brightness_becomes_automatic_without_reset_"+legacyTheme);
    }
    var automatic=new DesktopPreferences();
    automatic.Appearance.Wallpaper="ink";
    Check(automatic.UiTone=="dark", "static_dark_wallpaper_automatically_uses_light_text");
    automatic.Appearance.Wallpaper="cloud";
    Check(automatic.UiTone=="dark", "dawn_uses_dusk_material_without_manual_brightness");
    automatic.Appearance.Wallpaper="jade";
    // 0.17: the bright lake was replaced by the aurora coast; every palette is dark glass.
    Check(automatic.UiTone=="dark", "aurora_wallpaper_uses_dark_glass");
    automatic.VisualMode="particle";
    Check(automatic.UiTone=="dark" && automatic.Appearance.Wallpaper=="jade", "particle_tone_does_not_replace_static_wallpaper");
    automatic.VisualMode="static";
    Check(automatic.UiTone=="dark", "return_to_static_restores_automatic_tone");
    // Interface size: three levels, persisted, unknown values fall back to medium.
    Check(new DesktopPreferences().UiScale=="medium" && new DesktopPreferences().UiScaleFactor==1.0, "ui_scale_defaults_to_medium");
    foreach (var (level, factor) in new[]{("small",0.9),("medium",1.0),("large",1.15)}) {
        var scaled = new DesktopPreferences {UiScale=level}; scaled.Save(preferenceRoot);
        var reloaded = DesktopPreferences.Load(preferenceRoot);
        Check(reloaded.UiScale==level && reloaded.UiScaleFactor==factor, "ui_scale_persists_"+level);
    }
    File.WriteAllText(Path.Combine(preferenceRoot,".desktop-state","preferences.json"), "{\"UiScale\":\"huge\"}");
    Check(DesktopPreferences.Load(preferenceRoot).UiScale=="medium", "unknown_ui_scale_falls_back_to_medium");
    automatic.Appearance.Color="indigo";
    Check(automatic.UiTone=="dark", "explicit_palette_remains_available_with_automatic_brightness");
    File.WriteAllText(Path.Combine(preferenceRoot, ".desktop-state", "preferences.json"), "{\"VisualMode\":\"unknown\"}");
    Check(DesktopPreferences.Load(preferenceRoot).VisualMode == "static", "unknown_mode_falls_back_to_static");
} finally { preferenceFolder.Delete(true); }
Check(defaults.Wallpaper == "cloud" && defaults.Color == "auto" && !defaults.Solid && !defaults.ReducedMotion && defaults.EffectiveColor == "cloud", "default_cloud_auto_glass_full_motion");
var decoded = Read("""{"wallpaper":"jade","color":"ink","solid":true,"reducedMotion":true,"unknown":"ignored"}""");
Check(decoded.Wallpaper == "jade" && decoded.Color == "ink" && decoded.Solid && decoded.ReducedMotion && decoded.EffectiveColor == "ink", "camel_case_json_and_unknown_fields");
using (var serialized = JsonDocument.Parse(JsonSerializer.Serialize(decoded)))
{
    Check(serialized.RootElement.EnumerateObject().Select(property => property.Name).Order().SequenceEqual(new[] {"color", "reducedMotion", "solid", "wallpaper"}), "serialization_has_only_four_camel_case_preferences");
}
var roundTrip = Read(JsonSerializer.Serialize(decoded));
Check(roundTrip.Wallpaper == decoded.Wallpaper && roundTrip.Color == decoded.Color && roundTrip.Solid == decoded.Solid && roundTrip.ReducedMotion == decoded.ReducedMotion, "json_round_trip_preserves_preferences");
Check(Read("{}").Wallpaper == "cloud" && Read("{}").Color == "auto", "missing_fields_keep_defaults");
var empty = Read("""{"wallpaper":null,"color":null}""");
Check(empty.Wallpaper == "cloud" && empty.Color == "auto", "null_choices_restore_defaults");
Check(new[] {"", "missing", "Cloud", " jade "}.All(value =>
{
    var appearance = new LedgerAppearance { Wallpaper = value, Color = value }.Normalize();
    return appearance.Wallpaper == "cloud" && appearance.Color == "auto";
}), "unknown_and_noncanonical_choices_match_web_defaults");
var legacy = new Dictionary<string, string> { ["alpine"] = "cloud", ["snow"] = "indigo", ["lake"] = "jade", ["coast"] = "ink" };
Check(legacy.All(pair =>
{
    var appearance = new LedgerAppearance { Wallpaper = pair.Key, Color = pair.Key }.Normalize();
    return appearance.Wallpaper == pair.Value && appearance.Color == pair.Value && appearance.EffectiveColor == pair.Value;
}), "all_legacy_ids_migrate_for_wallpaper_and_color");
var ids = new[] { "cloud", "ink", "jade", "indigo" };
Check(ids.All(id =>
{
    var appearance = new LedgerAppearance { Wallpaper = id }.Normalize();
    return appearance.Wallpaper == id && appearance.Color == "auto" && appearance.EffectiveColor == id;
}), "all_wallpaper_ids_support_automatic_color");
Check(ids.All(id => new LedgerAppearance { Wallpaper = "none", Color = id }.EffectiveColor == id), "explicit_palette_overrides_wallpaper");
var plain = new LedgerAppearance { Wallpaper = "none" }.Normalize();
Check(plain.Wallpaper == "none" && plain.WallpaperFileName is null && plain.EffectiveColor == "cloud", "plain_background_has_no_file_and_auto_cloud_palette");
Check(new LedgerAppearance { Wallpaper = "auto", Color = "none" }.Normalize() is { Wallpaper: "cloud", Color: "auto" }, "none_is_wallpaper_only_and_auto_is_color_only");
var solid = new LedgerAppearance { Wallpaper = "jade", Solid = true, ReducedMotion = true }.Normalize();
// 0.17: the jade slot shows the aurora coast photo (the bright lake was replaced).
Check(solid.Solid && solid.ReducedMotion && solid.WallpaperFileName == "pexels-evelin-magnus-34540965.jpg", "solid_surfaces_and_reduced_motion_preserve_selected_wallpaper");
var original = new LedgerAppearance { Wallpaper = "snow", Color = "coast", Solid = true };
var normalized = original.Normalize();
Check(original.Wallpaper == "snow" && original.Color == "coast" && normalized.Wallpaper == "indigo" && normalized.Color == "ink", "normalization_returns_new_value_without_mutating_source");
Check(JsonSerializer.Serialize(normalized.Normalize()) == JsonSerializer.Serialize(normalized), "normalization_is_idempotent");
var paths = new[] { "../secret.jpg", "..\\secret.jpg", "C:\\private\\picture.jpg", "file:///C:/picture.jpg", "https://example.com/a.jpg", "/static/wallpapers/else.jpg", "pexels-dreamypixel-552784.jpg", "cloud\0evil", "../jade" };
Check(paths.All(value =>
{
    var appearance = new LedgerAppearance { Wallpaper = value, Color = value };
    return appearance.WallpaperFileName == defaults.WallpaperFileName && appearance.EffectiveColor == "cloud";
}), "arbitrary_paths_urls_and_filenames_never_become_wallpapers");
var malformed = new[] { "{", "[]", """{"wallpaper":7}""", """{"color":{}}""", """{"solid":"true"}""", """{"reducedMotion":1}""" };
Check(malformed.All(json =>
{
    try { JsonSerializer.Deserialize<LedgerAppearance>(json); return false; }
    catch (JsonException) { return true; }
}), "malformed_json_and_wrong_property_types_are_rejected");

var directory = new DirectoryInfo(AppContext.BaseDirectory);
while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "backend", "app", "static", "appearance.js"))) directory = directory.Parent;
if (directory is null) throw new Exception("Project appearance.js fixture was not found");
var staticDirectory = Path.Combine(directory.FullName, "backend", "app", "static");
var web = File.ReadAllText(Path.Combine(staticDirectory, "appearance.js"));
var webDefaults = Regex.Match(web, """const\s+defaults\s*=\s*\{\s*wallpaper:\s*"(?<wallpaper>[^"]+)",\s*color:\s*"(?<color>[^"]+)",\s*solid:\s*(?<solid>true|false),\s*reducedMotion:\s*(?<motion>true|false)\s*\}""");
Check(webDefaults.Success && defaults.Wallpaper == webDefaults.Groups["wallpaper"].Value && defaults.Color == webDefaults.Groups["color"].Value && defaults.Solid == bool.Parse(webDefaults.Groups["solid"].Value) && defaults.ReducedMotion == bool.Parse(webDefaults.Groups["motion"].Value), "native_defaults_match_existing_web_preferences");
var wallpapers = Regex.Matches(web, """\{\s*id:\s*"(?<id>[^"]+)"[^{}]*file:\s*"(?<file>[^"]+)"\s*\}""");
Check(wallpapers.Count == 4 && wallpapers.All(match => new LedgerAppearance { Wallpaper = match.Groups["id"].Value }.WallpaperFileName == match.Groups["file"].Value), "four_fixed_filenames_match_existing_web_wallpapers");
Check(ids.All(id =>
{
    var file = new LedgerAppearance { Wallpaper = id }.WallpaperFileName!;
    return Path.GetFileName(file) == file && File.Exists(Path.Combine(staticDirectory, "wallpapers", file));
}), "every_whitelisted_wallpaper_is_an_existing_bare_filename");
if (Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_RENDER_STARS") == "1") {
    var moments = new (double Time, string Name)[] {
        (0, "散布"), (StarfieldTimeline.GatherEnd, "聚拢成环"),
        (2.9, "旋转"), (4.8, "扩散"),
        (StarfieldTimeline.ScatterEnd, "星空成形"), (StarfieldTimeline.RevealAt, "内容显示")
    };
    var svg = new System.Text.StringBuilder("<svg xmlns='http://www.w3.org/2000/svg' width='1040' height='1008' viewBox='0 0 1040 1008'>");
    for (var panel = 0; panel < moments.Length; panel++) {
        var offsetX = panel % 2 * 520;
        var offsetY = panel / 2 * 336;
        svg.Append($"<g transform='translate({offsetX},{offsetY})'><rect width='520' height='336' fill='#0a1830'/><text x='22' y='27' fill='#e4f3ff' font-size='16'>{moments[panel].Name} · {moments[panel].Time:0.0}s</text>");
        for (var index = 0; index < 160; index++) {
            var star = StarfieldTimeline.Sample(index, 160, 520, 300, moments[panel].Time);
            var x = star.X.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
            var y = (star.Y + 36).ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
            var alpha = star.Opacity.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
            var radius = index % 19 == 0 ? 2.1 : index % 7 == 0 ? 1.4 : 0.9;
            svg.Append($"<circle cx='{x}' cy='{y}' r='{radius.ToString(System.Globalization.CultureInfo.InvariantCulture)}' fill='#e5f6ff' opacity='{alpha}'/>");
        }
        svg.Append("</g>");
    }
    svg.Append("</svg>");
    File.WriteAllText(Path.Combine(directory.FullName, "verification", "starfield-timeline.svg"), svg.ToString());
}
Console.WriteLine(JsonSerializer.Serialize(new { status = "passed", count = checks.Count, checks }));
