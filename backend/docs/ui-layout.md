# Ledger UI layout

The implemented dashboard interaction design is recorded in
[trend-interaction-plan.md](trend-interaction-plan.md), including the reusable
UI UX Pro Max reference skill, multi-series selection and month navigation.
The skill is installed globally in Codex. Comparisons keep separate selections
for platforms and payment methods; clicking a month updates the dashboard.

## Reference

Layout reviewed against [shadcn/ui](https://github.com/shadcn-ui/ui), MIT license,
at commit `3ba91b1cc83e1bbe4ab35a422ff2a694849c5048` on 2026-09-11.
The sparse reference checkout is in `.app-runtime/ui-reference/shadcn-ui`.
Reviewed the dashboard example and `apps/v4/registry/new-york-v4/ui/item.tsx`:
bounded content areas, consistent gaps, and readable icon/title/description rows.
The ledger keeps its existing HTML/CSS implementation; no component framework or
third-party executable was installed. The checkout retains the upstream license.

## Proportions

- Shared main content: at most 1200px plus page gutters, centered within the
  workspace. Header gutters align with this content on wide screens.
- Update: centered 864px content area; four actions in two equal columns above
  1050px viewport width, one column below it. At 1280px, action buttons measure
  approximately 396 by 104px. Single-column actions have an 88px minimum height.
- Action hierarchy: 15px titles, 13px descriptions, 40px icon containers on
  Update. Text may wrap on narrow screens rather than clipping.
- Shared section titles: 19px; table headings/body: 12px/13px. Overview primary
  values: 30px desktop, 24px small mobile. Mobile navigation labels: 11px.
- Maintenance information stays in Maintenance. The primary Update action is
  distinguished by a subtle tint; no additional user-facing explanation is added.

## Button motion (2026-09-11)

`app/static/motion.css` is shared by the ledger, analysis and price-watch pages.
The subsequent wallpaper expansion adds the semantic theme layer described in
[appearance.md](appearance.md); it loads after the page and motion styles.
Timing tokens: press 90ms, control states 160ms, hover emphasis 200ms. Small
controls press to 97% scale; large action cards move down 1px. Refresh arrows
move right 3px only on a fine hover pointer. The hover shadow is a single shared
token overridden by the shared appearance layer. Main actions lift 2px on a fine
hover pointer and play one 640ms highlight sweep; keyboard focus also triggers
the sweep. The standalone `.glass-button` class reuses this styling.

Reduced motion removes transforms and animations, including button descendants;
disabled controls do not move. Existing loading indicators and task behavior
remain responsible for progress. Finite, interruptible animation frames now handle chart and numeric transitions;
no new network request or animation library is added. See the shared motion system
in [appearance.md](appearance.md). Wallpaper/theme selection is now implemented following the user's newer
request, with four supplied images, independent colors and local preferences.

## Layout validation

Browser checks covered Update at 1920, 1440, 1024, 768, 390 and 320px without
page or button horizontal overflow. Desktop/mobile screenshots were inspected,
along with overview and orders. No console errors were observed. The 55 existing
application and analysis tests passed. No live collection or AI request was run.
