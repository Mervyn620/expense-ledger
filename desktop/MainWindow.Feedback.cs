using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    void InitializeFeedback()
    {
        foreach(var notice in new[]{financeNotice,ordersNotice,settingsNotice,maintenanceNotice}) ConfigureLedgerNotice(notice);
    }
    void ConfigureLedgerNotice(InfoBar notice)
    {
        notice.Foreground=(Brush)layout.Resources["LedgerTextBrush"];
        notice.Resources["InfoBarTitleForeground"]=layout.Resources["LedgerTextBrush"];
        notice.Resources["InfoBarMessageForeground"]=layout.Resources["LedgerTextBrush"];
        notice.Resources["InfoBarBorderBrush"]=layout.Resources["LedgerEdgeBrush"];
        foreach(var severity in new[]{"Error","Warning","Success","Informational"})
            notice.Resources["InfoBar"+severity+"SeverityBackgroundBrush"]=layout.Resources["LedgerNoticeBackgroundBrush"];
        // Popup controls do not always inherit the content pane's resource overrides.
        notice.Resources["TextFillColorPrimaryBrush"]=layout.Resources["LedgerTextBrush"];
        notice.Resources["TextFillColorSecondaryBrush"]=layout.Resources["LedgerSecondaryTextBrush"];
    }
    void ConfigureLedgerDialog(ContentDialog dialog, bool localize = true)
    {
        if (localize && Loc.English) LocalizeDialog(dialog);
        dialog.RequestedTheme=layout.ActualTheme;
        dialog.Background=SurfaceGlass;dialog.Foreground=(Brush)layout.Resources["LedgerTextBrush"];
        dialog.BorderBrush=(Brush)layout.Resources["LedgerEdgeBrush"];
        dialog.Resources["ContentDialogBackground"]=SurfaceGlass;
        dialog.Resources["ContentDialogForeground"]=layout.Resources["LedgerTextBrush"];
        dialog.Resources["ContentDialogBorderBrush"]=layout.Resources["LedgerEdgeBrush"];
        dialog.Resources["ContentDialogTopOverlay"]=layout.Resources["LedgerNoticeBackgroundBrush"];
        dialog.Resources["TextFillColorPrimaryBrush"]=layout.Resources["LedgerTextBrush"];
        dialog.Resources["TextFillColorSecondaryBrush"]=layout.Resources["LedgerSecondaryTextBrush"];
        dialog.Resources["ButtonForeground"]=layout.Resources["LedgerTextBrush"];
        dialog.Resources["ButtonBackground"]=layout.Resources["LedgerControlBrush"];
        dialog.Resources["ButtonBackgroundPointerOver"]=layout.Resources["LedgerControlHoverBrush"];
        dialog.Resources["ButtonBackgroundPressed"]=layout.Resources["LedgerControlPressedBrush"];
        dialog.Resources["TextControlBackground"]=layout.Resources["LedgerNoticeBackgroundBrush"];
        dialog.Resources["TextControlBackgroundFocused"]=layout.Resources["LedgerNoticeBackgroundBrush"];
        dialog.Resources["AccentFillColorDefaultBrush"]=layout.Resources["LedgerAccentBrush"];
    }
}
