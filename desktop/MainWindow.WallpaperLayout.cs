using System;
using System.Linq;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    void InitializeWallpaperLayout()
    {
        navigation.PaneOpened += (_, _) => UpdateCompactNavigation();
        navigation.PaneClosed += (_, _) => UpdateCompactNavigation();
        navigation.DisplayModeChanged += (_, _) => UpdateCompactNavigation();
        foreach (var item in navigation.MenuItems.Concat(navigation.FooterMenuItems).OfType<NavigationViewItem>())
            item.Loaded += (_, _) => UpdateCompactNavigation();
        layout.SizeChanged += async (_, _) => {
            var mode = layout.ActualWidth < 600 ? NavigationViewPaneDisplayMode.LeftMinimal : NavigationViewPaneDisplayMode.LeftCompact;
            if (navigation.PaneDisplayMode != mode) navigation.PaneDisplayMode = mode;
            await SyncStarfieldFrameAsync();
        };
    }
    void UpdateCompactNavigation()
    {
        var compact = !navigation.IsPaneOpen && navigation.DisplayMode == NavigationViewDisplayMode.Compact;
        foreach (var item in navigation.MenuItems.Concat(navigation.FooterMenuItems).OfType<NavigationViewItem>()) {
            if (FindGlassElement(item,"PresenterContentRootGrid") is not Grid content) continue;
            var caption = content.Children.OfType<TextBlock>().FirstOrDefault(text => text.Name == "DesktopCompactCaption");
            if (caption == null) {
                caption = new TextBlock { Name="DesktopCompactCaption", FontSize=12, FontWeight=Microsoft.UI.Text.FontWeights.SemiBold,
                    HorizontalAlignment=HorizontalAlignment.Center, VerticalAlignment=VerticalAlignment.Bottom,
                    Margin=new Thickness(0,0,0,7), Foreground=(SolidColorBrush)layout.Resources["LedgerPaneTextBrush"], IsHitTestVisible=false };
                AutomationProperties.SetAccessibilityView(caption,AccessibilityView.Raw);
                content.Children.Add(caption);
            }
            caption.Text = Loc.T((item.Tag as string) switch { "native-settings" => "设置", "native-maintenance" => "维护", _ => item.Content as string ?? "" });
            caption.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
            if (FindGlassElement(item,"ContentPresenter") is FrameworkElement label)
                label.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
            item.MinHeight = compact ? 64 : 44;
            if (FindGlassElement(item,"LayoutRoot") is FrameworkElement itemRoot) itemRoot.MinHeight=item.MinHeight;
            if (FindGlassElement(item,"ContentGrid") is Grid row) { row.MinHeight=item.MinHeight; row.Margin=new Thickness(0,0,compact?0:14,0); }
            if (FindGlassElement(item,"IconColumn") is Border column) column.Width=compact?72:40;
            if (FindGlassElement(item,"IconBox") is FrameworkElement icon) icon.Margin=new Thickness(0,compact?-20:0,0,0);
            AutomationProperties.SetName(item,item.Content as string ?? caption.Text);
        }
        // The native footer caches a shorter viewport before caption rows finish loading.
        // Reserve the actual item heights so both footer icons and labels remain visible.
        if (FindGlassElement(navigation,"FooterItemsScrollViewer") is ScrollViewer footer) {
            footer.MinHeight=navigation.FooterMenuItems.Count*(compact?68:48)+8;
            footer.ChangeView(null,0,null,true);
        }
    }
}
