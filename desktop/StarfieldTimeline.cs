using System;

namespace Expenses.Desktop;

internal readonly record struct StarSample(double X, double Y, double Opacity);
internal readonly record struct MeteorSample(double HeadX, double HeadY, double DirX, double DirY, double Length, double Opacity);

// Deterministic positions let the launch sequence become the settled wallpaper.
internal static class StarfieldTimeline
{
    public const double GatherEnd = 1.9;
    public const double OrbitEnd = 3.9;
    public const double ScatterEnd = 5.8;
    public const double RevealAt = 6.25;

    public static StarSample Sample(int index, int count, double width, double height, double seconds)
    {
        var x = (0.035 + Halton(index + 1, 2) * 0.93) * width;
        var y = (0.035 + Halton(index + 1, 3) * 0.93) * height;
        var centerX = width * 0.5;
        var centerY = height * 0.5;
        var radius = Math.Min(width, height) * (0.105 + index % 7 * 0.0018);
        var angle = index * Math.PI * 2 / count;
        var gatherAngle = angle + 3.1;
        var orbitAngle = gatherAngle + Math.PI * 3;
        var ringAlpha = 0.30 + 0.64 * Math.Pow((1 + Math.Cos(angle * 2 - 0.7)) * 0.5, 3);

        if (seconds < GatherEnd) {
            var p = Ease(seconds / GatherEnd);
            var movingAngle = angle + 3.1 * p;
            return new StarSample(
                Lerp(x, centerX + Math.Cos(movingAngle) * radius, p),
                Lerp(y, centerY + Math.Sin(movingAngle) * radius, p),
                Lerp(0.48, ringAlpha, p));
        }
        if (seconds < OrbitEnd) {
            var p = Ease((seconds - GatherEnd) / (OrbitEnd - GatherEnd));
            var movingAngle = gatherAngle + Math.PI * 3 * p;
            return new StarSample(centerX + Math.Cos(movingAngle) * radius,
                centerY + Math.Sin(movingAngle) * radius, ringAlpha);
        }
        if (seconds < ScatterEnd) {
            var p = Ease((seconds - OrbitEnd) / (ScatterEnd - OrbitEnd));
            var ringX = centerX + Math.Cos(orbitAngle) * radius;
            var ringY = centerY + Math.Sin(orbitAngle) * radius;
            var wave = Math.Pow(Math.Sin(Math.PI * p), 2) * 18;
            return new StarSample(Lerp(ringX, x, p) + Math.Cos(orbitAngle) * wave,
                Lerp(ringY, y, p) + Math.Sin(orbitAngle) * wave, Lerp(ringAlpha, 0.57, p));
        }
        var twinkle = 0.47 + 0.19 * Math.Sin(seconds * 1.12 + index * 2.17);
        var settle = Ease((seconds - ScatterEnd) / (RevealAt - ScatterEnd));
        var (dx, dy) = Drift(index, seconds);
        return new StarSample(x + dx, y + dy, Lerp(0.57, twinkle, settle));
    }

    // Settled stars drift on slow ellipses, nearer (larger) stars further, so the sky has depth.
    // Zero at RevealAt, so the launch sequence still lands exactly on the wallpaper positions.
    // desktop-starfield.js uses the same formula, so Web and native stars stay aligned.
    public static (double X, double Y) Drift(int index, double seconds)
    {
        var t = Math.Max(0, seconds - RevealAt);
        var depth = Depth(index);
        var amplitude = 10 * depth;
        var speed = (0.07 + index % 11 * 0.005) * (0.7 + 0.3 * depth);
        var phaseX = index * 1.618;
        var phaseY = index * 2.414;
        return (amplitude * (Math.Sin(t * speed + phaseX) - Math.Sin(phaseX)),
            amplitude * 0.7 * (Math.Cos(t * speed * 0.83 + phaseY) - Math.Cos(phaseY)));
    }

    public static double Depth(int index) => index % 19 == 0 ? 1.0 : index % 7 == 0 ? 0.62 : 0.36;

    // An occasional shooting star after the launch: at most one per 11-second window, about
    // seven windows in ten, starting at a hashed time and place. Same schedule on the Web.
    public const double MeteorPeriod = 11, MeteorDuration = 1.15;
    public static MeteorSample? Meteor(double width, double height, double seconds)
    {
        var t = seconds - RevealAt - 2;
        if (t < 0 || width < 1 || height < 1) return null;
        var window = Math.Floor(t / MeteorPeriod);
        if (Hash(window, 0) < 0.3) return null;
        var start = window * MeteorPeriod + 1 + Hash(window, 1) * (MeteorPeriod - MeteorDuration - 2);
        var p = (t - start) / MeteorDuration;
        if (p < 0 || p > 1) return null;
        var fromRight = Hash(window, 2) < 0.5;
        var angle = (22 + Hash(window, 3) * 14) * Math.PI / 180;
        var dirX = (fromRight ? -1 : 1) * Math.Cos(angle);
        var dirY = Math.Sin(angle);
        var startX = width * (fromRight ? 0.45 + 0.5 * Hash(window, 4) : 0.05 + 0.5 * Hash(window, 4));
        var startY = height * (0.04 + 0.30 * Hash(window, 5));
        var travel = Math.Min(width, height) * 0.42 + 160;
        var moved = travel * (1 - (1 - p) * (1 - p));
        var length = 60 + 120 * Math.Sin(Math.PI * Math.Min(1, p * 1.4));
        return new MeteorSample(startX + dirX * moved, startY + dirY * moved, dirX, dirY, length, Math.Pow(Math.Sin(Math.PI * p), 0.7));
    }

    static double Hash(double value, int salt)
    {
        var v = Math.Sin(value * 12.9898 + salt * 78.233) * 43758.5453;
        return v - Math.Floor(v);
    }

    static double Ease(double value)
    {
        var t = Math.Clamp(value, 0, 1);
        return t * t * t * (t * (t * 6 - 15) + 10);
    }
    static double Lerp(double from, double to, double t) => from + (to - from) * t;
    static double Halton(int index, int basis)
    {
        var result = 0.0;
        var fraction = 1.0 / basis;
        while (index > 0) {
            result += fraction * (index % basis);
            index /= basis;
            fraction /= basis;
        }
        return result;
    }
}
