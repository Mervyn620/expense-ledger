using Microsoft.UI.Xaml.Media;

namespace Expenses.Desktop;

// Desktop-only half of LedgerOrder: brushes for the fulfillment pill. Kept apart from LedgerOrders.cs,
// which the plain .NET test projects link without WinUI.
public sealed partial class LedgerOrder
{
    public Brush FulfillmentBackground => LedgerPills.Background(LedgerLabels.FulfillmentTone(Fulfillment));
    public Brush FulfillmentForeground => LedgerPills.Foreground(LedgerLabels.FulfillmentTone(Fulfillment));
}

// Pill colours copied from the Web stylesheet (.source-pill, .category-pill, .fulfillment-pill).
// Brushes are created lazily by bindings, which run on the UI thread.
public static class LedgerPills
{
    // Shared brushes in App.xaml; the appearance palette retints them, so rows follow the theme.
    static Microsoft.UI.Xaml.Media.Brush Resource(string key) => (Microsoft.UI.Xaml.Media.Brush)Microsoft.UI.Xaml.Application.Current.Resources[key];
    public static Microsoft.UI.Xaml.Media.Brush Background(string tone) => tone switch {
        "passed" => Resource("LedgerPillAccentBackgroundBrush"),
        "warning" => Resource("LedgerPillWarningBackgroundBrush"),
        "failed" => Resource("LedgerPillFailedBackgroundBrush"),
        _ => Resource("LedgerPillCoolBackgroundBrush")
    };
    public static Microsoft.UI.Xaml.Media.Brush Foreground(string tone) => tone switch {
        "passed" => Resource("LedgerPillAccentForegroundBrush"),
        "warning" => Resource("LedgerPillWarningForegroundBrush"),
        "failed" => Resource("LedgerPillFailedForegroundBrush"),
        _ => Resource("LedgerPillCoolForegroundBrush")
    };
}
