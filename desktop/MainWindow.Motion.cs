using System;
using System.Collections.Generic;
using System.Numerics;
using Microsoft.UI.Composition;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;
using Windows.UI;

namespace Expenses.Desktop;

// Native counterpart of motion.css / motion.js / desktop-starfield.js, so the native pages
// (orders, cash, payments, analysis, settings, maintenance) move like the Web pages:
// page and card entrance, button hover lift and press, a pointer glint on card edges and,
// in particle mode, the elastic click bounce. Everything runs as Composition animations on
// the compositor thread, uses the Web timings, and is skipped when motion is reduced.
public sealed partial class MainWindow
{
    // motion.css: --motion-press 90, --motion-control 160, --motion-panel 220, --motion-enter 360.
    static readonly TimeSpan MotionPress = TimeSpan.FromMilliseconds(90), MotionControl = TimeSpan.FromMilliseconds(160),
        MotionPanel = TimeSpan.FromMilliseconds(220), MotionEnter = TimeSpan.FromMilliseconds(360), MotionBounce = TimeSpan.FromMilliseconds(620);
    Compositor? motionCompositor;
    CompositionEasingFunction? motionEase, motionEaseOut;
    ButtonBase? hoveredButton, pressedButton;
    Border? glintCard;
    Brush? glintCardEdge;
    RadialGradientBrush? glintBrush;
    object? lastMotionSource;
    Style? nativeCardStyle, pageHeaderStyle;

    // Only the app's own switch "减少动态效果" stops motion (user decision 2026-11-21). The Windows
    // animation setting is ignored here and, via --force-prefers-no-reduced-motion, in the WebView.
    bool MotionEnabled => !preferences.Appearance.ReducedMotion;

    void InitializeNativeMotion()
    {
        nativeCardStyle = layout.Resources["NativeCard"] as Style;
        pageHeaderStyle = layout.Resources["PageHeader"] as Style;
        nativeContent.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(OnMotionPointerMoved), true);
        nativeContent.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler(OnMotionPointerPressed), true);
        nativeContent.AddHandler(UIElement.PointerReleasedEvent, new PointerEventHandler(OnMotionPointerReleased), true);
        nativeContent.AddHandler(UIElement.PointerCaptureLostEvent, new PointerEventHandler(OnMotionPointerReleased), true);
        nativeContent.AddHandler(UIElement.PointerWheelChangedEvent, new PointerEventHandler((_, _) => ClearGlint()), true);
        nativeContent.AddHandler(UIElement.KeyDownEvent, new KeyEventHandler(OnMotionKeyDown), true);
        nativeContent.PointerExited += (_, args) => {
            var point = args.GetCurrentPoint(nativeContent).Position;
            if (point.X <= 0 || point.Y <= 0 || point.X >= nativeContent.ActualWidth || point.Y >= nativeContent.ActualHeight) ClearNativeHover();
        };
    }

    Compositor MotionCompositor => motionCompositor ??= ElementCompositionPreview.GetElementVisual(layout).Compositor;
    CompositionEasingFunction MotionEase => motionEase ??= MotionCompositor.CreateCubicBezierEasingFunction(new Vector2(.2f, .8f), new Vector2(.2f, 1f));
    CompositionEasingFunction MotionEaseOut => motionEaseOut ??= MotionCompositor.CreateCubicBezierEasingFunction(new Vector2(0f, 0f), new Vector2(.58f, 1f));

    Visual MotionVisual(UIElement element)
    {
        ElementCompositionPreview.SetIsTranslationEnabled(element, true);
        return ElementCompositionPreview.GetElementVisual(element);
    }

    // --- Entrance: motion.js view() for the page, enter() with a 45 ms stagger for cards ---

    void AnimateNativeEntrance(FrameworkElement? panel)
    {
        if (panel == null || !MotionEnabled) return;
        Enter(panel, 7, MotionPanel, TimeSpan.Zero);
        // Cards are collected after the navigation handler has picked the page body (cash or payments),
        // while the page is still near transparent, so the stagger always runs on the visible cards.
        DispatcherQueue.TryEnqueue(() => {
            var index = 0;
            foreach (var card in NativeCards(panel)) {
                Enter(card, 10, MotionEnter, TimeSpan.FromMilliseconds(Math.Min(index, 4) * 45));
                if (++index >= 10) break;
            }
        });
    }

    void Enter(UIElement element, float rise, TimeSpan duration, TimeSpan delay)
    {
        var visual = MotionVisual(element);
        var fade = MotionCompositor.CreateScalarKeyFrameAnimation();
        fade.InsertKeyFrame(0, 0); fade.InsertKeyFrame(1, 1, MotionEase);
        fade.Duration = duration; fade.DelayTime = delay; fade.DelayBehavior = AnimationDelayBehavior.SetInitialValueBeforeDelay;
        var move = MotionCompositor.CreateVector3KeyFrameAnimation();
        move.InsertKeyFrame(0, new Vector3(0, rise, 0)); move.InsertKeyFrame(1, Vector3.Zero, MotionEase);
        move.Duration = duration; move.DelayTime = delay; move.DelayBehavior = AnimationDelayBehavior.SetInitialValueBeforeDelay;
        visual.StartAnimation("Opacity", fade);
        visual.StartAnimation("Translation", move);
    }

    // Walks the declared tree, not the visual tree, so pages that were never shown still list their cards.
    IEnumerable<Border> NativeCards(UIElement root)
    {
        var queue = new Queue<UIElement>();
        queue.Enqueue(root);
        while (queue.Count > 0) {
            var element = queue.Dequeue();
            if (element.Visibility == Visibility.Collapsed) continue;
            if (element is Border border && border != root && IsMotionCard(border)) { yield return border; continue; }
            switch (element) {
                case Panel panel: foreach (var child in panel.Children) queue.Enqueue(child); break;
                case Border { Child: { } child }: queue.Enqueue(child); break;
                case ScrollViewer { Content: UIElement content }: queue.Enqueue(content); break;
                case UserControl { Content: { } content }: queue.Enqueue(content); break;
            }
        }
    }

    bool IsMotionCard(Border border) => border.Style != null && (ReferenceEquals(border.Style, nativeCardStyle) || ReferenceEquals(border.Style, pageHeaderStyle));

    // --- Buttons: hover lift 2 px, press 0.97, particle bounce (motion.css + desktop-starfield.css) ---

    void OnMotionPointerMoved(object sender, PointerRoutedEventArgs args)
    {
        if (args.Pointer.PointerDeviceType == PointerDeviceType.Touch || !MotionEnabled) { ClearNativeHover(); return; }
        if (!ReferenceEquals(args.OriginalSource, lastMotionSource)) {
            lastMotionSource = args.OriginalSource;
            var source = args.OriginalSource as DependencyObject;
            SetHoveredButton(MotionButton(source));
            SetGlintCard(appearanceHighContrast ? null : MotionCard(source));
        }
        if (glintCard != null && glintBrush != null) {
            var point = args.GetCurrentPoint(glintCard).Position;
            glintBrush.Center = point; glintBrush.GradientOrigin = point;
        }
    }

    void OnMotionPointerPressed(object sender, PointerRoutedEventArgs args)
    {
        if (!MotionEnabled || MotionButton(args.OriginalSource as DependencyObject) is not { } button) return;
        pressedButton = button;
        ScaleTo(button, .97f, MotionPress);
    }

    void OnMotionPointerReleased(object sender, PointerRoutedEventArgs args)
    {
        if (pressedButton is not { } button) return;
        pressedButton = null;
        if (VisualEffectsEnabled) BounceControl(button);
        else ScaleTo(button, 1f, MotionControl);
    }

    void OnMotionKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key is not (VirtualKey.Enter or VirtualKey.Space) || !VisualEffectsEnabled) return;
        if (MotionButton(args.OriginalSource as DependencyObject) is { } button) BounceControl(button);
    }

    void SetHoveredButton(ButtonBase? button)
    {
        if (ReferenceEquals(button, hoveredButton)) return;
        if (hoveredButton != null) LiftTo(hoveredButton, 0);
        hoveredButton = button;
        if (button != null) LiftTo(button, -2);
    }

    void ClearNativeHover()
    {
        lastMotionSource = null;
        SetHoveredButton(null);
        ClearGlint();
    }

    void LiftTo(UIElement element, float y)
    {
        var move = MotionCompositor.CreateVector3KeyFrameAnimation();
        move.InsertKeyFrame(1, new Vector3(0, y, 0), MotionEase);
        move.Duration = MotionControl;
        MotionVisual(element).StartAnimation("Translation", move);
    }

    void ScaleTo(FrameworkElement element, float scale, TimeSpan duration)
    {
        var visual = MotionVisual(element);
        visual.CenterPoint = new Vector3((float)element.ActualWidth / 2, (float)element.ActualHeight / 2, 0);
        var animation = MotionCompositor.CreateVector3KeyFrameAnimation();
        animation.InsertKeyFrame(1, new Vector3(scale, scale, 1), MotionEase);
        animation.Duration = duration;
        visual.StartAnimation("Scale", animation);
    }

    // Same curve as @keyframes particle-control-bounce on the Web pages.
    void BounceControl(FrameworkElement element)
    {
        if (!VisualEffectsEnabled) return;
        var visual = MotionVisual(element);
        visual.CenterPoint = new Vector3((float)element.ActualWidth / 2, (float)element.ActualHeight / 2, 0);
        var animation = MotionCompositor.CreateVector3KeyFrameAnimation();
        foreach (var (at, scale) in new[] { (0f, 1f), (.16f, .84f), (.46f, 1.13f), (.72f, .965f), (1f, 1f) })
            animation.InsertKeyFrame(at, new Vector3(scale, scale, 1), MotionEaseOut);
        animation.Duration = MotionBounce;
        visual.StartAnimation("Scale", animation);
    }

    // Plain buttons only: check boxes, radio buttons, scroll bar arrows and buttons inside text fields stay still.
    ButtonBase? MotionButton(DependencyObject? source)
    {
        ButtonBase? found = null;
        for (var node = source; node != null && !ReferenceEquals(node, nativeContent); node = VisualTreeHelper.GetParent(node)) {
            if (node is TextBox or AutoSuggestBox or NumberBox or PasswordBox or ScrollBar or ComboBox or CalendarDatePicker or ListViewItem) return null;
            if (found == null && node is ButtonBase button && button is not (CheckBox or RadioButton or RepeatButton) && button.IsEnabled) found = button;
        }
        return found;
    }

    // --- Card edge glint: motion.js light(), the .glint-edge stroke following the pointer ---

    Border? MotionCard(DependencyObject? source)
    {
        for (var node = source; node != null && !ReferenceEquals(node, nativeContent); node = VisualTreeHelper.GetParent(node))
            if (node is Border border && IsMotionCard(border)) return border;
        return null;
    }

    void SetGlintCard(Border? card)
    {
        if (ReferenceEquals(card, glintCard)) return;
        ClearGlint();
        if (card == null) return;
        var edge = card.BorderBrush is SolidColorBrush solid ? solid.Color : Color.FromArgb(48, 255, 255, 255);
        glintBrush ??= new RadialGradientBrush {
            MappingMode = BrushMappingMode.Absolute, RadiusX = 180, RadiusY = 180,
            GradientStops = { new GradientStop { Offset = 0 }, new GradientStop { Offset = 1 } }
        };
        glintBrush.GradientStops[0].Color = Color.FromArgb(178, 255, 255, 255);
        glintBrush.GradientStops[1].Color = edge;
        // Cards normally take their edge from the style; keep a local brush only if one was set.
        glintCard = card;
        glintCardEdge = card.ReadLocalValue(Border.BorderBrushProperty) == DependencyProperty.UnsetValue ? null : card.BorderBrush;
        card.BorderBrush = glintBrush;
    }

    void ClearGlint()
    {
        if (glintCard == null) return;
        if (ReferenceEquals(glintCard.BorderBrush, glintBrush)) {
            if (glintCardEdge != null) glintCard.BorderBrush = glintCardEdge;
            else glintCard.ClearValue(Border.BorderBrushProperty);
        }
        glintCard = null; glintCardEdge = null;
    }
}
