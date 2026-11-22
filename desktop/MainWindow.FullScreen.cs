using System;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml.Input;
using Windows.Graphics;

namespace Expenses.Desktop;

public sealed partial class MainWindow
{
    OverlappedPresenter? windowedPresenter;
    RectInt32 windowedBounds;
    bool windowedWasMaximized;
    DateTimeOffset lastFullScreenToggle;

    void ToggleFullScreenInvoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        args.Handled = true;
        ToggleFullScreen();
    }

    void ToggleFullScreen()
    {
        // WebView and XAML can both see one Alt+Enter; handle it only once.
        var now = DateTimeOffset.UtcNow;
        if ((now - lastFullScreenToggle).TotalMilliseconds < 250) return;
        lastFullScreenToggle = now;

        try {
            if (AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen) {
                var presenter = windowedPresenter;
                AppWindow.SetPresenter(presenter ?? OverlappedPresenter.Create());
                if (presenter != null) {
                    if (windowedWasMaximized) presenter.Maximize();
                    else {
                        presenter.Restore();
                        AppWindow.MoveAndResize(windowedBounds);
                    }
                }
                windowedPresenter = null;
            } else if (AppWindow.Presenter is OverlappedPresenter presenter) {
                SaveWindow();
                windowedBounds = new RectInt32(AppWindow.Position.X, AppWindow.Position.Y, AppWindow.Size.Width, AppWindow.Size.Height);
                windowedWasMaximized = presenter.State == OverlappedPresenterState.Maximized;
                AppWindow.SetPresenter(AppWindowPresenterKind.FullScreen);
                windowedPresenter = presenter;
            }
        } catch (Exception error) { Log(error); }
    }
}
