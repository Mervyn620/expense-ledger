using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Shapes;
using Microsoft.UI;
using Windows.Foundation;
using Windows.UI;

namespace Expenses.Desktop;

public sealed partial class MainWindow
{
    sealed class StarVisual
    {
        public required Ellipse Dot { get; init; }
        public required TranslateTransform Position { get; init; }
        public Ellipse? Halo { get; init; }
        public TranslateTransform? HaloPosition { get; init; }
        public double X, Y, Size;
        public bool Placed;
    }
    // Three shared star tones, retinted with the palette (see StarPalette); most stars use the first.
    readonly SolidColorBrush starMainBrush = new(Colors.White), starAccentBrush = new(Color.FromArgb(255, 155, 218, 255)), starAltBrush = new(Color.FromArgb(255, 188, 170, 255));
    Rectangle? meteorTrail; Ellipse? meteorHead;
    CompositeTransform? meteorTransform; TranslateTransform? meteorHeadPosition;
    GradientStop? meteorTail, meteorFront;
    const double HaloSize = 13;

    // Star tones per palette, matching the Frost colours (Rosé Pine, Kanagawa, Everforest, Tokyo Night).
    internal static (Color Main, Color Accent, Color Alt) StarPalette(string color) => color switch {
        "ink" => (Color.FromArgb(255, 242, 236, 220), Color.FromArgb(255, 230, 195, 132), Color.FromArgb(255, 127, 180, 202)),
        "jade" => (Color.FromArgb(255, 232, 244, 236), Color.FromArgb(255, 131, 192, 146), Color.FromArgb(255, 214, 153, 182)),
        "indigo" => (Color.FromArgb(255, 228, 232, 255), Color.FromArgb(255, 122, 162, 247), Color.FromArgb(255, 187, 154, 247)),
        _ => (Colors.White, Color.FromArgb(255, 155, 218, 255), Color.FromArgb(255, 188, 170, 255))
    };
    void ApplyStarPalette(string color)
    {
        var (main, accent, alt) = StarPalette(color);
        starMainBrush.Color = main; starAccentBrush.Color = accent; starAltBrush.Color = alt;
        if (meteorFront != null) meteorFront.Color = main;
        if (meteorTail != null) meteorTail.Color = Color.FromArgb(0, accent.R, accent.G, accent.B);
    }
    readonly List<StarVisual> visualParticles = new();
    readonly Stopwatch visualClock = new();
    DispatcherQueueTimer? visualTimer;
    bool startupActive, pointerInside, wasVisualEnabled, renderingStartup, startupFrameSeen;
    double previousVisualFrame;
    int visualIntervalMs = 66;
    double startupAt, pointerX, pointerY, pointerAt;

    bool WindowVisible => AppWindow.IsVisible && !(AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter && presenter.State == Microsoft.UI.Windowing.OverlappedPresenterState.Minimized);
    bool VisualEffectsEnabled => preferences.VisualMode == "particle" &&
        !preferences.Appearance.ReducedMotion && !appearanceHighContrast && appearanceEffectsEnabled;

    void InitializeVisualInteractions()
    {
        // Every native button now gets hover, press and bounce from MainWindow.Motion.cs.
        InitializeNativeMotion();
        layout.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(OnStarPointerMoved), true);
        layout.PointerExited += (_, _) => pointerInside = false;
        ambientParticles.SizeChanged += (_, _) => {
            if (preferences.VisualMode == "particle" && !VisualEffectsEnabled && !appearanceHighContrast)
                DrawStillStars();
        };
    }

    void OnStarPointerMoved(object sender, PointerRoutedEventArgs args)
    {
        if (!VisualEffectsEnabled || startupActive) return;
        var point = args.GetCurrentPoint(layout).Position;
        pointerX = point.X; pointerY = point.Y;
        pointerInside = true;
        pointerAt = visualClock.Elapsed.TotalSeconds;
        SetVisualTimerInterval(33);
    }

    void BeginStartupEffect()
    {
        startupActive = VisualEffectsEnabled;
        startupFrameSeen = false;
        startupReveal = null; webFrameReady = false; usedFrameCaptureFallback = false;
        handoffStartedAt = handoffCompletedAt = 0;
        previousVisualFrame = 0;
        if (!visualClock.IsRunning) visualClock.Start();
        startupAt = visualClock.Elapsed.TotalSeconds;
        ApplyVisualMode();
        if (startupActive) {
            // Lay out and paint the WebView under an opaque native cover from the start.
            layout.Children.Remove(starfieldLayer);
            // The loading panel sits inside loadingCard; insert the cover just below that card.
            layout.Children.Insert(layout.Children.IndexOf(loadingCard), starfieldLayer);
            starfieldLayer.Opacity = 1;
            navigation.Visibility = Visibility.Visible;
            navigation.IsHitTestVisible = false;
            loading.Opacity = 0;
            loading.RenderTransform = new TranslateTransform {Y = 145};
            staticLoadingMark.Visibility = Visibility.Collapsed;
            particleLoadingMark.Visibility = Visibility.Collapsed;
            progress.Visibility = Visibility.Collapsed;
            heading.Foreground = new SolidColorBrush(Colors.White);
            message.Foreground = new SolidColorBrush(Color.FromArgb(255, 190, 215, 235));
            visualTimer?.Stop();
            StartStartupRendering();
            DrawStillStars();
        }
    }

    void StartStartupRendering()
    {
        if (renderingStartup) return;
        renderingStartup = true;
        CompositionTarget.Rendering += RenderStartupFrame;
    }
    void RenderStartupFrame(object? sender, object args)
    {
        if (startupActive && WindowVisible) {
            if (Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_STARTUP") == "1") startupFrameTimes.Add(visualClock.Elapsed.TotalSeconds);
            UpdateVisualEffects();
        }
    }
    void StopStartupRendering()
    {
        if (!renderingStartup) return;
        CompositionTarget.Rendering -= RenderStartupFrame;
        renderingStartup = false;
    }

    async Task WaitForStartupEffectAsync()
    {
        if (!startupActive || !VisualEffectsEnabled) return;
        var remaining = StarfieldTimeline.RevealAt - (visualClock.Elapsed.TotalSeconds - startupAt);
        while (startupActive && VisualEffectsEnabled && !exiting && remaining > 0) {
            await Task.Delay(TimeSpan.FromMilliseconds(Math.Min(remaining * 1000, 100)));
            remaining = StarfieldTimeline.RevealAt - (visualClock.Elapsed.TotalSeconds - startupAt);
        }
    }

    void ApplyVisualMode()
    {
        var enabled = VisualEffectsEnabled && (WindowVisible || startupActive);
        var starMode = preferences.VisualMode == "particle" && !appearanceHighContrast;
        starfieldBackground.Visibility = starMode ? Visibility.Visible : Visibility.Collapsed;
        ambientParticles.Visibility = starMode ? Visibility.Visible : Visibility.Collapsed;
        appearanceSettings.Content = "外观";
        if (loading.Visibility == Visibility.Visible && !startupActive) {
            staticLoadingMark.Visibility = Visibility.Visible;
            particleLoadingMark.Visibility = Visibility.Collapsed;
            heading.Foreground = (Microsoft.UI.Xaml.Media.Brush)layout.Resources["LedgerTextBrush"];
            message.Foreground = (Microsoft.UI.Xaml.Media.Brush)layout.Resources["LedgerTextBrush"];
            if (progress.IsActive) progress.Visibility = Visibility.Visible;
        }
        if (!enabled) {
            if (startupActive && WindowVisible) AbortStartupEffect();
            StopVisualEffects(); navigation.Visibility = Visibility.Visible;
            if (starMode) { EnsureStarVisuals(); DrawStillStars(); }
            wasVisualEnabled = false;
            return;
        }
        if (!startupActive && !pageReady && !starting) return;
        if (!wasVisualEnabled && !startupActive)
            startupAt = visualClock.Elapsed.TotalSeconds - StarfieldTimeline.RevealAt;
        EnsureStarVisuals();
        if (!visualClock.IsRunning) visualClock.Start();
        wasVisualEnabled = true;
        if (startupActive) { visualTimer!.Stop(); StartStartupRendering(); }
        else { StopStartupRendering(); visualTimer!.Start(); }
        UpdateVisualEffects();
    }

    void EnsureStarVisuals()
    {
        if (visualTimer != null) return;
        for (var i = 0; i < 160; i++) {
            var size = i % 19 == 0 ? 4.2 : i % 7 == 0 ? 2.8 : 1.7;
            var tone = i % 9 == 0 ? starAltBrush : i % 4 == 0 ? starAccentBrush : starMainBrush;
            Ellipse? halo = null; TranslateTransform? haloPosition = null;
            if (i % 19 == 0) {
                // Nearest stars get a soft glow, like the halo drawn on the Web canvas.
                haloPosition = new TranslateTransform();
                halo = new Ellipse {
                    Width = HaloSize, Height = HaloSize, Fill = tone, RenderTransform = haloPosition, IsHitTestVisible = false
                };
                ambientParticles.Children.Add(halo);
            }
            var position = new TranslateTransform();
            var dot = new Ellipse {
                Width = size, Height = size,
                Fill = tone,
                RenderTransform = position,
                IsHitTestVisible = false
            };
            ambientParticles.Children.Add(dot);
            visualParticles.Add(new StarVisual {Dot = dot, Position = position, Halo = halo, HaloPosition = haloPosition, Size = size});
        }
        meteorTail = new GradientStop {Offset = 0, Color = Colors.Transparent};
        meteorFront = new GradientStop {Offset = 1, Color = Colors.White};
        meteorTransform = new CompositeTransform();
        meteorTrail = new Rectangle {
            Height = 1.8, Width = 1, RadiusX = 0.9, RadiusY = 0.9, Opacity = 0, IsHitTestVisible = false,
            RenderTransform = meteorTransform, RenderTransformOrigin = new Point(0, 0.5),
            Fill = new LinearGradientBrush {StartPoint = new Point(0, 0.5), EndPoint = new Point(1, 0.5), GradientStops = {meteorTail, meteorFront}}
        };
        meteorHeadPosition = new TranslateTransform();
        meteorHead = new Ellipse {Width = 3.4, Height = 3.4, Fill = starMainBrush, Opacity = 0, IsHitTestVisible = false, RenderTransform = meteorHeadPosition};
        ambientParticles.Children.Add(meteorTrail);
        ambientParticles.Children.Add(meteorHead);
        ApplyStarPalette(preferences.Appearance.EffectiveColor);
        visualTimer = DispatcherQueue.CreateTimer();
        visualTimer.Interval = TimeSpan.FromMilliseconds(visualIntervalMs);
        visualTimer.Tick += (_, _) => UpdateVisualEffects();
    }

    void DrawStillStars()
    {
        var width = ambientParticles.ActualWidth;
        var height = ambientParticles.ActualHeight;
        if (width < 1 || height < 1) return;
        for (var i = 0; i < visualParticles.Count; i++) {
            var star = visualParticles[i];
            var frame = StarfieldTimeline.Sample(i, visualParticles.Count, width, height, StarfieldTimeline.RevealAt);
            star.X = frame.X; star.Y = frame.Y; star.Placed = true;
            PlaceStar(star, frame.Opacity);
        }
        UpdateMeteor(width, height, 0);
    }

    static void PlaceStar(StarVisual star, double opacity)
    {
        star.Position.X = star.X; star.Position.Y = star.Y;
        star.Dot.Opacity = opacity;
        if (star.Halo == null || star.HaloPosition == null) return;
        var offset = (star.Size - HaloSize) / 2;
        star.HaloPosition.X = star.X + offset; star.HaloPosition.Y = star.Y + offset;
        star.Halo.Opacity = opacity * 0.16;
    }

    // Returns true while a shooting star is crossing, so the timer can run at 30 fps for it.
    bool UpdateMeteor(double width, double height, double time)
    {
        if (meteorTrail == null || meteorHead == null || meteorTransform == null || meteorHeadPosition == null) return false;
        var meteor = startupActive || !VisualEffectsEnabled ? null : StarfieldTimeline.Meteor(width, height, time);
        if (meteor is not { } m) { meteorTrail.Opacity = meteorHead.Opacity = 0; return false; }
        meteorTrail.Width = m.Length;
        meteorTransform.TranslateX = m.HeadX - m.DirX * m.Length;
        meteorTransform.TranslateY = m.HeadY - m.DirY * m.Length - meteorTrail.Height / 2;
        meteorTransform.Rotation = Math.Atan2(m.DirY, m.DirX) * 180 / Math.PI;
        meteorHeadPosition.X = m.HeadX - meteorHead.Width / 2; meteorHeadPosition.Y = m.HeadY - meteorHead.Height / 2;
        meteorTrail.Opacity = m.Opacity * 0.85; meteorHead.Opacity = m.Opacity;
        return true;
    }

    void StopVisualEffects() { visualTimer?.Stop(); StopStartupRendering(); }

    void SetVisualTimerInterval(int milliseconds)
    {
        if (visualIntervalMs == milliseconds) return;
        visualIntervalMs = milliseconds;
        if (visualTimer != null) visualTimer.Interval = TimeSpan.FromMilliseconds(milliseconds);
    }

    void EndStartupEffect()
    {
        if (!startupActive) return;
        startupActive = false;
        StopStartupRendering();
        RestoreStarfieldLayer();
        navigation.Visibility = Visibility.Visible;
        loading.Opacity = 1;
        loading.RenderTransform = null;
        SetVisualTimerInterval(66);
        if (VisualEffectsEnabled && WindowVisible) visualTimer?.Start();
    }

    void AbortStartupEffect()
    {
        startupActive = false;
        StopStartupRendering();
        RestoreStarfieldLayer();
        navigation.Visibility = Visibility.Visible;
        loading.Opacity = 1;
        loading.RenderTransform = null;
    }

    void RestoreStarfieldLayer()
    {
        layout.Children.Remove(starfieldLayer);
        layout.Children.Insert(layout.Children.IndexOf(navigation), starfieldLayer);
        starfieldLayer.Opacity = 1;
        navigation.IsHitTestVisible = true;
    }

    void BeginRouteTransition()
    {
        // WebView opacity changes can flicker on some Windows graphics stacks, so Web routes
        // switch instantly over the persistent starfield and animate inside the page (motion.js).
        // Native pages get the same entrance as the Web panels.
        ClearNativeHover();
        AnimateNativeEntrance(currentRoute switch {
            "orders" => ordersPanel, "cash" or "sources" => financePanel, "analysis" => analysisPanel,
            "native-settings" => settingsPanel, "native-maintenance" => maintenancePanel, _ => null
        });
    }

    void UpdateVisualEffects()
    {
        if (!VisualEffectsEnabled || !WindowVisible) { StopVisualEffects(); return; }
        var width = ambientParticles.ActualWidth;
        var height = ambientParticles.ActualHeight;
        if (width < 1 || height < 1) return;
        var now = visualClock.Elapsed.TotalSeconds;
        if (startupActive && !startupFrameSeen) { startupFrameSeen = true; startupAt = now; }
        var delta = Math.Clamp(now - previousVisualFrame, 0.001, 0.1);
        previousVisualFrame = now;
        var time = now - startupAt;
        var pointerActive = pointerInside && !startupActive;
        var meteorActive = UpdateMeteor(width, height, time);
        SetVisualTimerInterval(startupActive || meteorActive || pointerActive && now - pointerAt < 0.8 ? 33 : 66);
        for (var i = 0; i < visualParticles.Count; i++) {
            var star = visualParticles[i];
            var frame = StarfieldTimeline.Sample(i, visualParticles.Count, width, height, time);
            var targetX = frame.X;
            var targetY = frame.Y;
            if (pointerActive) {
                var dx = targetX - pointerX;
                var dy = targetY - pointerY;
                var distance = Math.Sqrt(dx * dx + dy * dy);
                const double clearance = 115;
                if (distance < clearance) {
                    if (distance < 0.01) { dx = 1; dy = 0; distance = 1; }
                    var push = clearance - distance;
                    targetX += dx / distance * push;
                    targetY += dy / distance * push;
                }
            }
            if (!star.Placed || startupActive) {
                star.X = targetX; star.Y = targetY; star.Placed = true;
            } else {
                var smoothing = 1 - Math.Exp(-(pointerActive ? 17 : 10) * delta);
                star.X += (targetX - star.X) * smoothing;
                star.Y += (targetY - star.Y) * smoothing;
            }
            PlaceStar(star, frame.Opacity);
        }
        if (startupActive) {
            loading.Opacity = Math.Clamp((time - 4.3) / 0.65, 0, 0.92);
            if (time >= StarfieldTimeline.ScatterEnd)
                message.Text = "星空已就绪，正在打开账本…";
        }
    }

    async Task SyncVisualModeToWebAsync()
    {
        if (browser.CoreWebView2 == null || browser.CoreWebView2.IsSuspended) return;
        try {
            await RegisterDesktopBridgeAsync();
            if (!pageReady) return;
            await browser.CoreWebView2.ExecuteScriptAsync(
                "window.__expenseDesktopVisualMode = " + JsonSerializer.Serialize(preferences.VisualMode) +
                "; document.documentElement.dataset.desktopVisualMode = window.__expenseDesktopVisualMode; window.expenseDesktopEffects?.setMode(window.__expenseDesktopVisualMode); window.__expenseDesktopGameMode = " + JsonSerializer.Serialize(preferences.GameModeEnabled) + "; window.expenseDesktopCosmosWallpaper?.setEnabled(window.__expenseDesktopGameMode)");
            await SyncStarfieldFrameAsync();
        } catch (Exception error) { Log(error); }
    }
}
