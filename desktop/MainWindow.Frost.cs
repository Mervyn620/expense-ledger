using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace Expenses.Desktop;

// Lighter frosted glass for the static wallpapers: translucent cards that let the wallpaper through,
// hairline highlight edges instead of tinted borders, and one palette for text, accents and the pane.
// Runs after the legacy palette code, so particle mode and high contrast keep their own materials.
public sealed partial class MainWindow
{
    static void SetPill(string name, Color background, Color foreground)
    {
        ((SolidColorBrush)Application.Current.Resources[$"LedgerPill{name}BackgroundBrush"]).Color = background;
        ((SolidColorBrush)Application.Current.Resources[$"LedgerPill{name}ForegroundBrush"]).Color = foreground;
    }

    // Particle mode and high contrast keep the original paper-chip pills.
    static void ResetPillPalette()
    {
        SetPill("Warm", Color.FromArgb(255, 0xF4, 0xEE, 0xE8), Color.FromArgb(255, 0x80, 0x51, 0x26));
        SetPill("Cool", Color.FromArgb(255, 0xED, 0xF2, 0xF6), Color.FromArgb(255, 0x31, 0x54, 0x6C));
        SetPill("Warning", Color.FromArgb(255, 0xFF, 0xF3, 0xDF), Color.FromArgb(255, 0x8A, 0x56, 0x15));
        SetPill("Failed", Color.FromArgb(255, 0xFD, 0xE8, 0xE7), Color.FromArgb(255, 0xA3, 0x3B, 0x35));
    }

    void ApplyFrostPalette(FrostPalette p, bool opaque)
    {
        var light = p.Light;
        var white = Color.FromArgb(255, 255, 255, 255);
        var solidFill = light ? p.Base : p.Glass;

        SurfaceGlass.TintColor = p.Glass;
        SurfaceGlass.FallbackColor = solidFill;
        SurfaceGlass.TintOpacity = light ? 0.50 : 0.52;
        SurfaceGlass.TintLuminosityOpacity = light ? 0.72 : 0.38;
        SurfaceGlass.AlwaysUseFallback = opaque;
        PaneGlass.TintColor = p.Pane;
        PaneGlass.FallbackColor = p.Pane;
        PaneGlass.TintOpacity = 0.58;
        PaneGlass.TintLuminosityOpacity = 0.46;
        PaneGlass.AlwaysUseFallback = opaque;

        SetAppearanceColor("LedgerCanvasBrush", p.Base);
        // A faint sheen from the top-left corner; the glass itself does the work.
        var card = (LinearGradientBrush)layout.Resources["LedgerCardBrush"];
        var reflection = (LinearGradientBrush)layout.Resources["LedgerReflectionBrush"];
        for (var i = 0; i < card.GradientStops.Count; i++) {
            card.GradientStops[i].Color = opaque ? solidFill : i switch {
                0 => WithAlpha(white, (byte)(light ? 70 : 20)), 1 => WithAlpha(p.Glass, 0), _ => WithAlpha(p.Alt, (byte)(light ? 14 : 12))
            };
            reflection.GradientStops[i].Color = opaque ? WithAlpha(white, 0) : i switch {
                0 => WithAlpha(white, (byte)(light ? 60 : 16)), 1 => WithAlpha(white, 0), _ => WithAlpha(p.Alt, 8)
            };
        }
        var paneReflection = (LinearGradientBrush)layout.Resources["LedgerPaneReflectionBrush"];
        for (var i = 0; i < paneReflection.GradientStops.Count; i++)
            paneReflection.GradientStops[i].Color = opaque ? WithAlpha(p.Pane, 0) : i switch {
                0 => WithAlpha(white, 14), 1 => WithAlpha(p.Pane, 0), _ => WithAlpha(p.PaneAccent, 10)
            };

        SetAppearanceColor("LedgerNoticeBackgroundBrush", solidFill);
        SetAppearanceColor("LedgerTextBrush", p.Text);
        SetAppearanceColor("LedgerSecondaryTextBrush", p.Subtext);
        // Controls are a light veil on the glass rather than another dark slab.
        SetAppearanceColor("LedgerControlBrush", opaque ? solidFill : light ? WithAlpha(white, 150) : WithAlpha(p.Text, 16));
        SetAppearanceColor("LedgerControlHoverBrush", opaque ? solidFill : light ? WithAlpha(white, 210) : WithAlpha(p.Text, 30));
        SetAppearanceColor("LedgerControlPressedBrush", opaque ? solidFill : light ? WithAlpha(white, 120) : WithAlpha(p.Text, 10));
        SetAppearanceColor("LedgerSelectionBrush", WithAlpha(p.Accent, (byte)(light ? 40 : 46)));
        SetAppearanceColor("LedgerEdgeBrush", light ? WithAlpha(white, 190) : WithAlpha(p.Text, 34));

        SetAppearanceColor("LedgerPaneTextBrush", p.PaneText);
        SetAppearanceColor("LedgerPaneSecondaryBrush", p.PaneSubtext);
        SetAppearanceColor("LedgerPaneControlBrush", opaque ? p.Pane : WithAlpha(p.PaneText, 16));
        SetAppearanceColor("LedgerPaneHoverBrush", opaque ? p.Pane : WithAlpha(p.PaneText, 26));
        SetAppearanceColor("LedgerPaneAccentBrush", p.PaneAccent);

        var titleBar = AppWindow.TitleBar;
        titleBar.BackgroundColor = titleBar.InactiveBackgroundColor = p.Pane;
        titleBar.ButtonBackgroundColor = titleBar.ButtonInactiveBackgroundColor = p.Pane;
        titleBar.ForegroundColor = titleBar.ButtonForegroundColor = p.PaneText;
        titleBar.InactiveForegroundColor = titleBar.ButtonInactiveForegroundColor = p.PaneSubtext;
        titleBar.ButtonHoverBackgroundColor = Mix(p.Pane, p.PaneText, .12, 255);
        titleBar.ButtonPressedBackgroundColor = Mix(p.Pane, p.PaneText, .06, 255);

        ((SolidColorBrush)Application.Current.Resources["LedgerControlAccentBrush"]).Color = p.Accent;
        SetAppearanceColor("LedgerAccentBrush", p.Accent);
        ((SolidColorBrush)Application.Current.Resources["LedgerPillAccentForegroundBrush"]).Color = p.Accent;
        ((SolidColorBrush)Application.Current.Resources["LedgerPillAccentBackgroundBrush"]).Color = WithAlpha(p.Accent, (byte)(light ? 36 : 44));
        SetAppearanceColor("LedgerAccentSoftBrush", WithAlpha(p.Accent, (byte)(light ? 32 : 36)));

        // Header: the same glass as the cards with a whisper of the two accents, no colour slab.
        var header = (LinearGradientBrush)layout.Resources["LedgerHeaderBrush"];
        if (opaque) {
            foreach (var stop in header.GradientStops) stop.Color = solidFill;
        } else {
            header.GradientStops[0].Color = Mix(p.Glass, p.Accent, .14, (byte)(light ? 150 : 128));
            header.GradientStops[1].Color = WithAlpha(p.Glass, (byte)(light ? 130 : 104));
            header.GradientStops[2].Color = Mix(p.Glass, p.Alt, .12, (byte)(light ? 145 : 120));
        }
        // Pills: translucent tints of the palette instead of opaque paper chips.
        var warning = light ? Color.FromArgb(255, 223, 142, 29) : Color.FromArgb(255, 246, 193, 119);
        var failed = light ? Color.FromArgb(255, 210, 15, 57) : Color.FromArgb(255, 235, 111, 146);
        SetPill("Warm", WithAlpha(p.Accent, (byte)(light ? 34 : 40)), light ? Mix(p.Accent, p.Text, .55, 255) : p.Accent);
        SetPill("Cool", WithAlpha(p.Alt, (byte)(light ? 34 : 36)), light ? Mix(p.Alt, p.Text, .55, 255) : p.Alt);
        SetPill("Warning", WithAlpha(warning, (byte)(light ? 40 : 40)), light ? Mix(warning, p.Text, .45, 255) : warning);
        SetPill("Failed", WithAlpha(failed, (byte)(light ? 34 : 40)), light ? Mix(failed, p.Text, .30, 255) : failed);
        // A light veil keeps text legible on busy photos without dimming the whole wallpaper.
        wallpaperWash.Background = new SolidColorBrush(light ? WithAlpha(white, 18) : WithAlpha(p.Base, 46));
    }
}
