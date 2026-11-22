using System;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage;
using Windows.UI;
using Windows.UI.ViewManagement;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    readonly UISettings appearanceSystem = new();
    bool appearanceDisposed, appearanceInitialized, applyingAppearance;
    bool appearanceHighContrast, appearanceEffectsEnabled = true;
    int wallpaperRevision;
    string? loadedWallpaper;
    AcrylicBrush SurfaceGlass => (AcrylicBrush)layout.Resources["LedgerSurfaceBrush"];
    AcrylicBrush PaneGlass => (AcrylicBrush)layout.Resources["LedgerPaneBrush"];
    static Color Rgb(byte r, byte g, byte b, byte a = 255) => Color.FromArgb(a,r,g,b);
    static Color WithAlpha(Color color, byte alpha) => Color.FromArgb(alpha,color.R,color.G,color.B);
    void InitializeAppearance()
    {
        appearanceInitialized = true;
        navigation.Loaded += NavigationGlassLoaded;
        layout.ActualThemeChanged += AppearanceThemeChanged;
        SystemEvents.UserPreferenceChanged += AppearancePreferenceChanged;
        try { appearanceSystem.AdvancedEffectsEnabledChanged += AppearanceEffectsChanged; }
        catch (System.Runtime.InteropServices.COMException error) { Log(error); }
        ApplyAppearanceVisuals();
    }
    static FrameworkElement? FindGlassElement(DependencyObject parent, string name)
    {
        if (parent is FrameworkElement element && element.Name == name) return element;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++) {
            var found = FindGlassElement(VisualTreeHelper.GetChild(parent,i),name);
            if (found != null) return found;
        }
        return null;
    }
    void NavigationGlassLoaded(object sender, RoutedEventArgs args)
    {
        if (FindGlassElement(navigation,"PaneContentGrid") is Microsoft.UI.Xaml.Controls.Grid pane)
            pane.Background = (LinearGradientBrush)layout.Resources["LedgerPaneReflectionBrush"];
        UpdateCompactNavigation();
    }
    void SettingsInfoLoaded(object sender, RoutedEventArgs args)
    {
        if (sender is not Microsoft.UI.Xaml.Controls.Button button || button.Flyout is not Microsoft.UI.Xaml.Controls.Flyout flyout) return;
        flyout.XamlRoot = button.XamlRoot;
        flyout.Opening -= SettingsInfoOpening;
        flyout.Opening += SettingsInfoOpening;
    }
    void SettingsInfoOpening(object? sender, object args)
    {
        if (sender is Microsoft.UI.Xaml.Controls.Flyout {Content: FrameworkElement content})
            content.RequestedTheme = layout.ActualTheme;
    }
    void AppearanceThemeChanged(FrameworkElement sender, object args) => ApplyAppearanceVisuals();
    void AppearancePreferenceChanged(object sender, UserPreferenceChangedEventArgs args) => QueueAppearanceUpdate();
    void AppearanceEffectsChanged(UISettings sender, object args) => QueueAppearanceUpdate();
    void QueueAppearanceUpdate() => DispatcherQueue.TryEnqueue(() => { if (!appearanceDisposed) ApplyAppearanceVisuals(); });
    void DisposeAppearance()
    {
        appearanceDisposed = true; wallpaperRevision++;
        layout.ActualThemeChanged -= AppearanceThemeChanged;
        navigation.Loaded -= NavigationGlassLoaded;
        SystemEvents.UserPreferenceChanged -= AppearancePreferenceChanged;
        try { appearanceSystem.AdvancedEffectsEnabledChanged -= AppearanceEffectsChanged; }
        catch (System.Runtime.InteropServices.COMException) { }
    }
    void SetAppearanceColor(string key, Color color) => ((SolidColorBrush)layout.Resources[key]).Color = color;
    static Color Mix(Color from, Color to, double amount, byte alpha) => Color.FromArgb(alpha,
        (byte)(from.R + (to.R - from.R) * amount), (byte)(from.G + (to.G - from.G) * amount), (byte)(from.B + (to.B - from.B) * amount));
    // Page header bar: same three-stop material as the Web --header-surface for each palette.
    void ApplyHeaderSurface(string color, bool particle, bool opaque, Color fill, Color glassTint, Color glint, Color prism)
    {
        var header = (LinearGradientBrush)layout.Resources["LedgerHeaderBrush"];
        Color a, b, c;
        if (opaque) a = b = c = fill;
        else if (particle) (a, b, c) = (Mix(glassTint, glint, .10, 150), WithAlpha(glassTint, 140), Mix(glassTint, prism, .06, 150));
        else (a, b, c) = color switch {
            "jade" => (Rgb(216,224,192,224), Rgb(175,205,185,214), Rgb(168,209,207,214)),
            "ink" => (Rgb(74,66,55,224), Rgb(49,47,45,219), Rgb(34,36,40,230)),
            "indigo" => (Rgb(66,52,99,224), Rgb(44,62,103,219), Rgb(26,42,76,230)),
            _ => (Rgb(91,67,80,224), Rgb(54,77,105,219), Rgb(29,46,69,230))
        };
        header.GradientStops[0].Color = a; header.GradientStops[1].Color = b; header.GradientStops[2].Color = c;
    }
    void ApplyAppearanceVisuals(bool? highContrastOverride = null, bool? effectsOverride = null)
    {
        if (!appearanceInitialized || appearanceDisposed || applyingAppearance) return;
        applyingAppearance = true;
        try {
            var appearance = preferences.Appearance.Normalize();
            appearanceHighContrast = highContrastOverride ?? Forms.SystemInformation.HighContrast;
            try { appearanceEffectsEnabled = effectsOverride ?? appearanceSystem.AdvancedEffectsEnabled; }
            catch (System.Runtime.InteropServices.COMException) { appearanceEffectsEnabled = true; }
            bool dark = layout.ActualTheme == ElementTheme.Dark;
            nativeContent.RequestedTheme = layout.ActualTheme;
            navigation.RequestedTheme = appearanceHighContrast ? ElementTheme.Default : ElementTheme.Dark;
            var (canvas, surface, rail, glassTint, glint, prism) = appearance.EffectiveColor switch {
                "ink" => (Rgb(232,234,236), Rgb(195,207,215), Rgb(21,27,36), Rgb(45,57,71), Rgb(168,191,210), Rgb(123,162,189)),
                "jade" => (Rgb(230,239,234), Rgb(170,213,207), Rgb(8,31,33), Rgb(18,57,60), Rgb(96,212,198), Rgb(105,155,229)),
                "indigo" => (Rgb(231,234,243), Rgb(193,192,229), Rgb(24,21,49), Rgb(48,43,83), Rgb(173,149,245), Rgb(83,181,214)),
                _ => (Rgb(229,236,241), Rgb(187,215,231), Rgb(9,23,41), Rgb(26,49,77), Rgb(93,205,231), Rgb(151,139,219))
            };
            var particle = preferences.VisualMode == "particle";
            if (!particle) (canvas, surface, rail, glassTint, glint, prism) = appearance.EffectiveColor switch {
                "ink" => (Rgb(24,25,27), Rgb(204,203,199), Rgb(24,25,27), Rgb(43,44,46), Rgb(179,184,187), Rgb(157,143,121)),
                "jade" => (Rgb(222,235,226), Rgb(182,214,202), Rgb(20,45,37), Rgb(32,69,56), Rgb(117,190,161), Rgb(196,173,111)),
                "indigo" => (Rgb(16,23,49), Rgb(201,206,236), Rgb(16,23,49), Rgb(32,42,79), Rgb(161,171,237), Rgb(116,141,218)),
                _ => (Rgb(27,42,63), Rgb(204,215,226), Rgb(27,42,63), Rgb(47,70,93), Rgb(152,188,211), Rgb(229,168,145))
            };
            var (starStart, starMiddle, starEnd) = appearance.EffectiveColor switch {
                "jade" => (Rgb(7,26,25), Rgb(18,59,56), Rgb(9,28,41)),
                "indigo" => (Rgb(16,17,39), Rgb(41,39,75), Rgb(16,14,38)),
                "ink" => (Rgb(18,22,28), Rgb(42,48,57), Rgb(18,23,33)),
                _ => (Rgb(7,16,30), Rgb(16,38,66), Rgb(8,15,35))
            };
            var gradient = (LinearGradientBrush)starfieldBackground.Background;
            gradient.GradientStops[0].Color = starStart;
            gradient.GradientStops[1].Color = starMiddle;
            gradient.GradientStops[2].Color = starEnd;
            ApplyStarPalette(appearance.EffectiveColor);
            if (preferences.VisualMode == "particle" && !appearanceHighContrast) canvas = starStart;
            var darkSurface = glassTint;
            var systemBackground = appearanceSystem.GetColorValue(UIColorType.Background);
            var systemForeground = appearanceSystem.GetColorValue(UIColorType.Foreground);
            var fill = appearanceHighContrast ? systemBackground : dark ? darkSurface : surface;
            bool opaque = appearance.Solid || appearanceHighContrast || !appearanceEffectsEnabled;
            SurfaceGlass.TintColor = fill; SurfaceGlass.FallbackColor = fill;
            SurfaceGlass.TintOpacity = dark ? particle ? 0.40 : 0.78 : 0.82;
            SurfaceGlass.TintLuminosityOpacity = dark ? particle ? 0.10 : appearance.EffectiveColor == "cloud" ? 0.90 : 0.75 : 0.70;
            SurfaceGlass.AlwaysUseFallback = opaque;
            var (paneTint,paneGlint,panePrism) = particle ? (glassTint,glint,prism) : appearance.EffectiveColor switch {
                "jade" => (Rgb(20,53,45), Rgb(74,123,97), Rgb(35,80,73)),
                "ink" => (Rgb(26,27,29), Rgb(74,72,67), Rgb(35,34,32)),
                "indigo" => (Rgb(18,27,58), Rgb(47,63,113), Rgb(61,43,90)),
                _ => (Rgb(22,39,59), Rgb(62,88,120), Rgb(49,53,83))
            };
            PaneGlass.TintColor = appearanceHighContrast ? systemBackground : paneTint;
            PaneGlass.FallbackColor = PaneGlass.TintColor;
            PaneGlass.TintOpacity = particle ? SurfaceGlass.TintOpacity : 0.82;
            PaneGlass.TintLuminosityOpacity = particle ? SurfaceGlass.TintLuminosityOpacity : 0.80;
            PaneGlass.AlwaysUseFallback = opaque;
            SetAppearanceColor("LedgerCanvasBrush", appearanceHighContrast ? systemBackground : dark ? rail : canvas);
            var card = (LinearGradientBrush)layout.Resources["LedgerCardBrush"];
            var reflection = (LinearGradientBrush)layout.Resources["LedgerReflectionBrush"];
            for (var i = 0; i < card.GradientStops.Count; i++) {
                card.GradientStops[i].Color = opaque ? fill : i switch {
                    0 => WithAlpha(glint,(byte)(dark ? 30 : 24)),
                    1 => WithAlpha(glassTint,(byte)(dark ? 56 : 5)),
                    _ => WithAlpha(prism,(byte)(dark ? 22 : 16))
                };
                reflection.GradientStops[i].Color = opaque ? WithAlpha(glint,0) : i switch {
                    0 => WithAlpha(glint,22), 1 => WithAlpha(glint,0), _ => WithAlpha(prism,12)
                };
            }
            var paneReflection = (LinearGradientBrush)layout.Resources["LedgerPaneReflectionBrush"];
            paneReflection.EndPoint = new Windows.Foundation.Point(particle ? 1 : .45,1);
            for(var i=0;i<paneReflection.GradientStops.Count;i++) {
                paneReflection.GradientStops[i].Color = particle ? reflection.GradientStops[i].Color : opaque ? WithAlpha(paneTint,0) : i switch {
                    0 => WithAlpha(paneGlint,115), 1 => WithAlpha(paneTint,20), _ => WithAlpha(panePrism,100)
                };
            }
            var text = appearanceHighContrast ? systemForeground : dark ? Rgb(228,238,247) : Rgb(12,30,44);
            var secondaryText = appearanceHighContrast ? systemForeground : dark ? Rgb(183,201,221) : Rgb(12,30,44);
            if (!particle && !appearanceHighContrast) {
                (text, secondaryText) = appearance.EffectiveColor switch {
                    "ink" => (Rgb(240,238,234), Rgb(203,199,192)),
                    "jade" => (Rgb(12,38,32), Rgb(12,38,32)),
                    "indigo" => (Rgb(237,240,255), Rgb(191,201,232)),
                    _ => (Rgb(235,240,246), Rgb(220,230,240))
                };
            }
            SetAppearanceColor("LedgerNoticeBackgroundBrush", fill);
            SetAppearanceColor("LedgerTextBrush", text);
            SetAppearanceColor("LedgerSecondaryTextBrush", secondaryText);
            SetAppearanceColor("LedgerControlBrush", opaque ? fill : dark ? WithAlpha(glassTint,170) : WithAlpha(surface,160));
            SetAppearanceColor("LedgerControlHoverBrush", opaque ? fill : dark ? WithAlpha(glint,36) : WithAlpha(surface,200));
            SetAppearanceColor("LedgerControlPressedBrush", opaque ? fill : dark ? WithAlpha(glassTint,230) : WithAlpha(surface,140));
            SetAppearanceColor("LedgerSelectionBrush", appearanceHighContrast ? systemBackground : WithAlpha(glint,(byte)(dark ? 40 : 58)));
            SetAppearanceColor("LedgerEdgeBrush", appearanceHighContrast ? systemForeground : WithAlpha(glint,(byte)(dark ? 70 : 110)));
            var paneText = appearanceHighContrast ? systemForeground : particle ? text : Rgb(233,241,248);
            var paneSecondary = appearanceHighContrast ? systemForeground : particle ? secondaryText : Rgb(201,217,230);
            SetAppearanceColor("LedgerPaneTextBrush",paneText);
            SetAppearanceColor("LedgerPaneSecondaryBrush",paneSecondary);
            SetAppearanceColor("LedgerPaneControlBrush", appearanceHighContrast ? systemBackground : particle ? ((SolidColorBrush)layout.Resources["LedgerControlBrush"]).Color : WithAlpha(paneTint,160));
            SetAppearanceColor("LedgerPaneHoverBrush", appearanceHighContrast ? systemBackground : particle ? ((SolidColorBrush)layout.Resources["LedgerControlHoverBrush"]).Color : WithAlpha(glint,40));
            var titleBar = AppWindow.TitleBar;
            var titleFill = appearanceHighContrast ? systemBackground : particle ? rail : paneTint;
            titleBar.BackgroundColor = titleFill; titleBar.InactiveBackgroundColor = titleFill;
            titleBar.ForegroundColor = paneText; titleBar.InactiveForegroundColor = paneSecondary;
            titleBar.ButtonBackgroundColor = titleFill; titleBar.ButtonInactiveBackgroundColor = titleFill;
            titleBar.ButtonForegroundColor = paneText; titleBar.ButtonInactiveForegroundColor = paneSecondary;
            titleBar.ButtonHoverBackgroundColor = paneTint; titleBar.ButtonPressedBackgroundColor = paneTint;
            var (accent, lightAccent) = appearance.EffectiveColor switch {
                "jade" => (Rgb(36,103,96), Rgb(116,224,199)),
                "indigo" => (Rgb(85,94,147), Rgb(190,175,250)),
                "ink" => (Rgb(75,81,89), Rgb(176,204,226)),
                _ => (Rgb(63,96,122), Rgb(112,213,232))
            };
            if (!particle) (accent, lightAccent) = appearance.EffectiveColor switch {
                "ink" => (Rgb(91,82,66), Rgb(209,190,160)),
                "jade" => (Rgb(34,102,83), Rgb(156,212,184)),
                "indigo" => (Rgb(73,86,152), Rgb(181,191,246)),
                _ => (Rgb(117,78,64), Rgb(237,189,162))
            };
            ((SolidColorBrush)Application.Current.Resources["LedgerControlAccentBrush"]).Color = appearanceHighContrast
                ? Rgb(System.Drawing.SystemColors.Highlight.R, System.Drawing.SystemColors.Highlight.G, System.Drawing.SystemColors.Highlight.B) : dark ? lightAccent : accent;
            SetAppearanceColor("LedgerAccentBrush", appearanceHighContrast ? systemForeground : dark ? lightAccent : accent);
            SetAppearanceColor("LedgerPaneAccentBrush", appearanceHighContrast ? systemForeground : lightAccent);
            var pageAccent = appearanceHighContrast ? systemForeground : dark ? lightAccent : accent;
            ((SolidColorBrush)Application.Current.Resources["LedgerPillAccentForegroundBrush"]).Color = pageAccent;
            ((SolidColorBrush)Application.Current.Resources["LedgerPillAccentBackgroundBrush"]).Color =
                appearanceHighContrast ? systemBackground : WithAlpha(pageAccent, (byte)(dark ? 72 : 48));
            SetAppearanceColor("LedgerAccentSoftBrush", appearanceHighContrast ? systemBackground : WithAlpha(pageAccent, (byte)(dark ? 36 : 40)));
            ApplyHeaderSurface(appearance.EffectiveColor, particle, opaque, fill, glassTint, glint, prism);
            wallpaperImage.Visibility = preferences.VisualMode == "particle" || appearanceHighContrast || !appearanceEffectsEnabled || appearance.Wallpaper == "none" ? Visibility.Collapsed : Visibility.Visible;
            wallpaperWash.Background = new SolidColorBrush(appearance.Wallpaper switch {
                "ink" => Rgb(8,9,10,20), "jade" => Rgb(16,49,39,8), "indigo" => Rgb(12,20,46,10), _ => Rgb(25,45,60,8)
            });
            wallpaperWash.Visibility = wallpaperImage.Visibility;
            ResetPillPalette();
            if (!particle && !appearanceHighContrast) ApplyFrostPalette(FrostPalette.For(appearance.EffectiveColor), opaque);
            if (preferences.VisualMode != "particle") _ = LoadAppearanceWallpaperAsync(appearance.WallpaperFileName);
            ApplyVisualMode();
            _ = SyncStarfieldFrameAsync();
            _ = SyncGlassToneToWebAsync();
        } finally { applyingAppearance = false; }
    }
    async Task SyncGlassToneToWebAsync()
    {
        if (browser.CoreWebView2 == null) return;
        try {
            var tone = layout.ActualTheme == ElementTheme.Dark ? "dark" : "light";
            var enabled = !appearanceHighContrast && appearanceEffectsEnabled;
            await browser.CoreWebView2.ExecuteScriptAsync("window.__expenseDesktopUiTone = " + JsonSerializer.Serialize(tone)
                + "; window.__expenseDesktopGlassEnabled = " + JsonSerializer.Serialize(enabled)
                + "; document.documentElement.dataset.desktopTone = window.__expenseDesktopUiTone; document.documentElement.dataset.desktopGlass = window.__expenseDesktopGlassEnabled ? 'glass' : 'opaque';");
        } catch (Exception error) { Log(error); }
    }
    // Decode for the largest screen in physical pixels. The image fills with UniformToFill, so a wide
    // photo on a 3:2 screen is height-bound and needs about twice the screen height in width.
    // 1920 was blurry on high-DPI screens (a 3000 px wide window stretched it ~1.6x).
    static int WallpaperDecodeWidth()
    {
        var width = 1920; var height = 1080;
        try {
            foreach (var screen in Forms.Screen.AllScreens) {
                width = Math.Max(width, screen.Bounds.Width);
                height = Math.Max(height, screen.Bounds.Height);
            }
        } catch (Exception) { }
        return Math.Clamp(Math.Max(width, height * 2), 1920, 7680);
    }
    async Task LoadAppearanceWallpaperAsync(string? filename)
    {
        if (filename == loadedWallpaper) return;
        loadedWallpaper = filename;
        var revision = ++wallpaperRevision;
        if (filename == null) { wallpaperImage.Source = null; return; }
        try {
            // The filename comes only from LedgerAppearance's fixed local allowlist.
            var file = await StorageFile.GetFileFromPathAsync(Path.Combine(root,"backend","app","static","wallpapers",filename));
            using var stream = await file.OpenReadAsync();
            var bitmap = new BitmapImage {DecodePixelWidth = WallpaperDecodeWidth()};
            await bitmap.SetSourceAsync(stream);
            if (revision == wallpaperRevision && !appearanceDisposed) wallpaperImage.Source = bitmap;
        } catch (Exception error) {
            if (revision == wallpaperRevision && !appearanceDisposed) { wallpaperImage.Source = null; loadedWallpaper = null; }
            Log(error); // A missing wallpaper never blocks ledger pages or their controls.
        }
    }
    void ApplySharedAppearance(JsonElement data)
    {
        if (data.ValueKind != JsonValueKind.Object) return;
        var value = JsonSerializer.Deserialize<LedgerAppearance>(data.GetRawText())?.Normalize();
        if (value == null) return;
        var changed = JsonSerializer.Serialize(value) != JsonSerializer.Serialize(preferences.Appearance);
        preferences.Appearance = value;
        preferences.HasAppearanceCache = true;
        ApplyNativeTheme();
        _ = RegisterDesktopBridgeAsync();
        if (changed) {
            try { preferences.Save(root); }
            catch (Exception error) { Log(error); }
        }
    }
    async Task SyncAppearanceFromWebAsync()
    {
        if (browser.CoreWebView2 == null) return;
        try {
            var json = await browser.CoreWebView2.ExecuteScriptAsync("window.LedgerAppearance?.current?.() ?? null");
            using var data = JsonDocument.Parse(json);
            ApplySharedAppearance(data.RootElement);
        } catch (Exception error) { Log(error); }
    }
}
