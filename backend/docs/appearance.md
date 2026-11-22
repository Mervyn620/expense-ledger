# Wallpaper and appearance system

Implemented 2026-09-11. The ledger, analysis and price-watch pages expose **外观**
in their header. The default is 云海晨曦 with its paired 暮蓝 theme. Settings apply
immediately, persist in this browser and synchronize across its open app pages.

## Visual direction

Restrained translucent cards over landscape photography and abstract textures, dark tinted navigation,
quiet borders and shallow shadows. Text and numbers use the local system font
stack (Segoe UI Variable / Segoe UI / PingFang SC / Microsoft YaHei UI); no font
download or animation dependency is required. Card radius is 16px and normal
control radius is 10px. Existing information hierarchy and maintenance placement
remain in place.

The design references [Apple's material guidance](https://developer.apple.com/design/human-interface-guidelines/materials)
for translucent layers and readable content, and [Material color roles](https://github.com/material-components/material-components-android/blob/master/docs/theming/Color.md)
for separate surface, foreground and accent tokens. These informed the approach;
the palettes below are custom choices for the supplied photographs, not copied
vendor palettes. The installed `ui-ux-pro-max` skill supplied glass, contrast,
keyboard and reduced-motion checks.

## Bundled choices

| Wallpaper | Paired theme | Accent | Original resolution | Original asset |
|---|---|---|---|---|
| 云海晨曦 | 暮蓝 | `#3f607a` | 7319 × 3910 | `pexels-dreamypixel-552784.jpg` |
| 黑沙纹理 | 石墨 | `#4b5159` | 6000 × 4000 | `pexels-adrien-olichon-1257089-2387819.jpg` |
| 山湖映翠 | 湖青 | `#246760` | 6000 × 4000 | `pexels-francesco-ungaro-1525041.jpg` |
| 蓝紫流光 | 靛紫 | `#555e93` | 7680 × 4320 | `pexels-steve-29586678.jpg` |

The user supplied these files from their local `Desktop/pics` folder. They are
byte-identical copies under `app/static/wallpapers/`; source files were not
modified. Their names are retained for provenance. They load locally, with no
remote image request. The 2026-09-12 replacement preserves the JPEG originals
without resizing or recompression. Legacy AVIF assets are no longer in the picker
or active CSS. No additional redistribution rights are inferred.

**跟随壁纸** chooses the paired palette; explicit colors stay independent when
the wallpaper changes. **纯色背景** removes the photograph. With automatic color,
plain background uses 暮蓝. Four compact thumbnails appear in a row on desktop
and two columns on mobile; the dialog scrolls on shorter screens.

## Reuse and extension

- `app/static/appearance.js`: load in the head without `defer` so saved appearance
  datasets apply before the body paints. It creates the dialog after DOM ready
  and binds buttons marked `data-open-appearance`.
- `app/static/appearance.css`: load after page CSS and `motion.css`. Reuse
  `--surface`, `--surface-muted`, `--glass-surface`, `--text`, `--text-soft`,
  `--accent`, `--accent-soft`, `--line` and `--line-strong` for new components.
- `app/static/motion.css`: shared timing and highlight effects. Use a native
  `<button type="button" class="glass-button">…</button>` for a new standalone
  control; existing action classes also receive the interaction style.
- `app/static/motion.js`: load with `defer` before page scripts. Reuse
  `LedgerMotion.metrics`, `bars`, `captureTrend`/`trend`, `view` and
  `openDialog`/`closeDialog` for bounded transitions. Write final content first;
  the engine adds presentation and settles immediately when motion is reduced.
- To add a wallpaper, add a local asset and a catalog entry in `appearance.js`,
  then matching wallpaper/theme/dot selectors in `appearance.css`. Keep identifiers
  stable because saved preferences reference them. Do not accept arbitrary URLs
  through browser preferences.

Preferences use `expense-ledger-appearance-v1` in localStorage with `wallpaper`,
`color`, `solid` and `reducedMotion`. Only whitelisted IDs and actual booleans are
accepted. Broken or unavailable storage falls back safely; failed writes display
an honest unsaved message. This is browser-local, not account synchronization.
The application database and ledger filters are separate from appearance state.
Legacy IDs migrate on read: `alpine → cloud`, `snow → indigo`, `lake → jade`,
`coast → ink`, for both wallpaper and independent color. Accessibility flags and
the automatic-color preference are retained. No storage-key reset is required.

## Motion and readability

The analysis page uses the same chart-tier material for results and secondary
tier for mode cards and chat, including the dashboard blur, edge and shadow.
Its initial result panel is content-sized (about 205px on desktop), with a
decorative chart icon, clear next step and an accurate data-use notice. Loading,
failure and results retain a selected mode with an accessible pressed state and
an immediate period title. The chat composer starts at one line, grows up to
176px, and supports Ctrl/Cmd+Enter without intercepting IME composition or normal
newlines. Response metadata stays hidden until a question is submitted.

The 2026-09-16 clarity adjustment uses 14px desktop / 12px mobile blur for
cloud, jade and indigo, with a 5–6% dark wallpaper wash in place of the former
12% pale tint. Jade glass is a neutral lake green with a deeper teal sidebar;
its accent color is unchanged. Graphite retains its original palette, 12% wash
and 28px desktop / 20px mobile blur. Colored glass uses brighter neutral bases;
solid and increased-contrast preferences still override the blur.

Main buttons lift 2px and play one 640ms soft sheen; presses use 90ms,
control feedback 160ms, panels/navigation 220ms, staggered entry 360ms and data
interpolation 480ms. `motion.js` reads shared duration/easing tokens from
`motion.css`. Native Web Animations and bounded requestAnimationFrame jobs are
interruptible; no animation library or continuous decorative loop is added.

First metric entry is staggered. Cards persist across month updates; unchanged
values keep their existing layer. Changed amounts crossfade in place between actual values
rather than displaying fabricated intermediate amounts. Progress bars grow from
zero initially and interpolate from their current values on updates, with one
soft gradient pass; accessible
values always expose the final data. Chart series reveal from left to right,
morph on a shared month axis and crossfade when series identities change. Stable
category colors/dashes remain independent of theme. A nearest-month vertical
guide and glass tooltip follow pointer or keyboard focus.

The sidebar indicator moves between views while the header stays mounted and
content enters over 220ms. Analysis/price-watch sections and the appearance dialog
share the same entrance language. Dialog closing retains native focus return.
A single pointer-following SVG adds a faint radial reflection and edge highlight
to the hovered card, with at most one update per animation frame on fine pointers.
It is removed on scroll, pointer leave, reduced motion and hidden documents.

Material tiers are 60% auxiliary cards and 66% charts/tables in colored themes,
with 84% primary metric and 92% floating panels. Graphite retains 62% / 72% panels
and its 56% light header. Colored headers use 66% dark glass, 10px blur and light
headings above light controls. Navigation uses 54% dark tint and a restrained
blue selection light. Panel blur follows the theme values above; it is never
animated. Lightweight cards use stronger text for readability.

The overview plot has a 308px base height, compact footer spacing and a subtle
selected-point glow. Distribution progress uses 5px theme-colored bars, fixed
percentage columns and neutral scroll thumbs. Recent orders have a tinted table
header, 15px vertical cell padding, soft row hover/focus feedback and a glass
"view all" button. Scrolling and data semantics are unchanged.

System reduced-motion and **减少动效** disable motion and finish any pending
interpolation immediately. Hidden/detached views release active frame jobs.
**提高卡片不透明度**, unsupported backdrop-filter and system higher contrast /
reduced transparency make every material tier opaque. Wallpaper remains static.

## Validation

- Fourteen Node tests passed: five appearance tests, five trend interaction
  regressions and four motion interruption/reduced-motion/lifecycle tests. The existing Python static asset
  check also passed.
- Browser inspection covered all four wallpaper choices, independent color,
  plain background, opaque cards, reduced motion, refresh persistence, cross-page
  synchronization and Escape focus return. The replacement JPEGs decoded at their full original dimensions; SHA-256 checks
  confirmed all four project copies match the supplied files byte for byte.
- The 2026-09-12 browser checks covered July/August switching, all three chart
  modes, settled navigation indicator geometry, pointer reflection coordinates,
  native dialog focus return, opaque material tiers and 390px layout.
- Computed button styles confirmed a single 640ms sweep and hover displacement;
  enabling reduced motion changed both animation and transform to `none`.
  Browser-emulated system reduced-motion and higher-contrast preferences also
  produced no sweep/transform and opaque cards with zero blur; emulation was
  cleared after checking.
- Desktop, tablet and narrow layouts were checked at 1440, 768, 390 and 320px
  across the affected pages. The 320px chart heading now gives controls a separate
  row; the mobile analysis status no longer truncates to a 70px badge. No browser
  warnings or errors appeared on the three app pages during checks.
- No live collector or external AI request was triggered for this UI validation.

Commands:

```powershell
node --test tests_js/appearance.test.mjs tests_js/trend-interaction.test.mjs tests_js/motion.test.mjs
.app-runtime/Scripts/python.exe -B -m unittest tests.test_expense_app.ExpenseAppStaticAssetsTest
```

## Shared selection controls (2026-09-12)

`select.js` and `select.css` enhance native single selects on every page after
DOMContentLoaded, including newly inserted form fields. Load them after the
appearance and motion assets. The hidden native select remains the source of
truth for FormData, required validation and input/change events. Do not locate a
form submit button using an unqualified `querySelector('button')`; the select
trigger is a separate type=button control.

The trigger is a combobox linked to a top-layer listbox, with selected checkmarks,
42px option rows, matching anchor width and 6px separation. Available viewport
space determines whether the menu opens below or above. Menus inside dialogs
stay within the dialog's accessible subtree. Arrow keys, Home/End, Enter/Space,
type-ahead, Escape and Tab are supported. Disabled items are skipped. Escape or
outside dismissal never commits a pending keyboard selection. Reduced motion
and opaque preferences reuse the shared tokens. Browsers without the Popover API
retain their original native controls.

When application code assigns `select.value` directly, call
`LedgerSelect.sync(select)` afterward. Option DOM mutations, user changes, resets
and disabled-state changes synchronize automatically. Removed fields release
associated popovers. The month selection and request-race logic remain intact.

Chart hit areas remain transparent even on hover/focus. The selected month is
represented by a 1px guide, filled 5.5px data points and a 650-weight axis label.
Keyboard focus underlines the date label instead of outlining an entire column.

An isolated form fixture is in `tests/fixtures/select.html`. With the app running
on port 8765, serve `tests/fixtures` on loopback port 8766 and open `/select.html`.
It exercises required-field focus, disabled items, FormData, resets, change-event
counts and dynamic field replacement without contacting ledger mutation APIs.

Validation for this update: the 14 existing appearance/trend/motion Node tests
and Python static-asset check pass. Browser checks confirmed month menu/chart
synchronization, transparent chart hit areas, keyboard selection/cancellation,
and the isolated fixture's required validation, disabled-item skipping, one
change event per new selection, FormData, reset synchronization, dynamic field
replacement and old-popover cleanup. No ledger mutation was used.

At 390px, the month anchor and menu both measured 202px wide with a 6px gap,
without horizontal overflow. The category list was constrained to 300px height;
End scrolled to the last item and Tab moved to the payment filter without
committing a selection. All order filter menus use the same component.

Pointer-focus follow-up: Chromium applied `outline: auto 5px` to SVG month groups
on mouse click even when `:focus-visible` did not match. Both `:focus` and
`:focus-visible` now suppress that native rectangle. Browser verification showed
no outline after clicking July; ArrowRight moved focus to August with only the
axis-label underline. Focus was retained and filtering logic was not changed.

Metric stability follow-up: comparison arrows previously used their intrinsic
24px height, changing card height from 132px to 137.9px when comparison notes
appeared. Numeric and note areas now reserve space; arrows are fixed at 16px.
The busy state retains aria-busy without dimming the cards. Numeric outgoing
and incoming layers use the same 220ms opacity curve and no vertical transform.
Desktop checks across September, May and all time held metric height at 144.5px
and the following chart position at 280.5px. The 16 Node tests and Python static
asset check passed, including cancellation and unchanged-value regressions.
