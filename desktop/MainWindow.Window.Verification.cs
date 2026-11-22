using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Windows.Graphics;

namespace Expenses.Desktop;

public sealed partial class MainWindow
{
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    async Task VerifyWindowBehaviorAsync()
    {
        if (Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_WINDOW") != "1") return;
        var checks = new System.Collections.Generic.List<string>();
        void Check(bool condition, string name)
        {
            if (!condition) throw new InvalidOperationException(name);
            checks.Add(name);
        }

        // A saved maximized window starts maximized; begin the normal-window path from a restored window.
        if (AppWindow.Presenter is OverlappedPresenter initial && initial.State == OverlappedPresenterState.Maximized) {
            initial.Restore();
            await Task.Delay(300);
        }
        var bounds = new RectInt32(AppWindow.Position.X, AppWindow.Position.Y, AppWindow.Size.Width, AppWindow.Size.Height);
        var output = Path.Combine(root, ".desktop-state", "window-verification.json");
        try {
            await Task.Delay(300);
            Check(AppWindow.Presenter is OverlappedPresenter, "starts_windowed");
            ToggleFullScreen();
            Check(AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen, "normal_to_fullscreen");
            await Task.Delay(300);
            ToggleFullScreen();
            Check(AppWindow.Presenter is OverlappedPresenter normal && normal.State == OverlappedPresenterState.Restored,
                "fullscreen_to_normal");
            Check(AppWindow.Position.X == bounds.X && AppWindow.Position.Y == bounds.Y &&
                AppWindow.Size.Width == bounds.Width && AppWindow.Size.Height == bounds.Height,
                "normal_bounds_restored");

            var presenter = (OverlappedPresenter)AppWindow.Presenter;
            presenter.Maximize();
            await Task.Delay(300);
            ToggleFullScreen();
            Check(AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen, "maximized_to_fullscreen");
            await Task.Delay(300);
            ToggleFullScreen();
            Check(AppWindow.Presenter is OverlappedPresenter maximized && maximized.State == OverlappedPresenterState.Maximized,
                "fullscreen_to_maximized");
            presenter.Restore();
            AppWindow.MoveAndResize(bounds);

            Check(PostMessage(WinRT.Interop.WindowNative.GetWindowHandle(this), 0x0010, IntPtr.Zero, IntPtr.Zero),
                "close_message_posted");
            await Task.Delay(300);
            Check(!AppWindow.IsVisible && !exiting && tray.Visible, "close_hides_to_tray_without_exit");
            ShowWindow();
            Check(AppWindow.IsVisible, "tray_restore_shows_window");
            File.WriteAllText(output, JsonSerializer.Serialize(new {status = "passed", checks}));
        } catch (Exception error) {
            Log(error);
            File.WriteAllText(output, JsonSerializer.Serialize(new {status = "failed", checks, error = error.ToString()}));
        }
    }
}
