using Windows.UI;

namespace Expenses.Desktop;

// "Frost" palettes for the static wallpapers (0.16). Each one follows an open-source colour scheme
// (MIT licensed) chosen to sit on its wallpaper: Rosé Pine Moon on the dusk mountains, Kanagawa on
// the black sand, Everforest on the aurora coast (0.17, replaced the bright lake) and Tokyo Night on
// the indigo waves.
// The Web pages use the same values in appearance.css ("Frost palettes" block).
internal sealed record FrostPalette(
    bool Light,
    Color Base,      // canvas behind the wallpaper and solid-surface fill
    Color Glass,     // tint of cards, header and controls
    Color Pane,      // navigation pane (always dark)
    Color Text,
    Color Subtext,
    Color Accent,
    Color Alt,       // second accent: chart series 2, gradient sheen
    Color PaneText,
    Color PaneSubtext,
    Color PaneAccent)
{
    static Color C(byte r, byte g, byte b) => Color.FromArgb(255, r, g, b);

    internal static FrostPalette For(string id) => id switch {
        // Kanagawa (dragon): sumi ink, fuji white, carp yellow, wave aqua.
        "ink" => new(false, C(24,22,22), C(40,39,39), C(29,28,25), C(220,215,186), C(200,192,147),
            C(230,195,132), C(122,168,159), C(220,215,186), C(200,192,147), C(230,195,132)),
        // Everforest (dark) on the aurora coast: forest-grey glass, warm beige text, aurora aqua accent,
        // the sky's pink-purple as the second accent.
        "jade" => new(false, C(30,35,38), C(46,56,60), C(35,42,46), C(211,198,170), C(178,184,168),
            C(131,192,146), C(214,153,182), C(211,198,170), C(178,184,168), C(131,192,146)),
        // Tokyo Night (storm): night blue glass, periwinkle text, blue accent, magenta sheen.
        "indigo" => new(false, C(26,27,38), C(31,35,53), C(22,22,30), C(192,202,245), C(169,177,214),
            C(122,162,247), C(187,154,247), C(192,202,245), C(169,177,214), C(122,162,247)),
        // Rosé Pine Moon: dusk violet glass, rose accent (the sunset band), foam sheen.
        _ => new(false, C(35,33,54), C(42,39,63), C(35,33,54), C(224,222,244), C(196,192,222),
            C(234,154,151), C(156,207,216), C(224,222,244), C(196,192,222), C(234,154,151)),
    };
}
