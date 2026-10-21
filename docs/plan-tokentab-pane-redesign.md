# Plan: the TokenTab pane redesign (Figma "TokenTab UI")

Source: [Figma, Playground › TokenTab UI](https://www.figma.com/design/VmeVm787V3Aei0NjFfBeCI/Playground?node-id=869-1680),
read 2026-10-21. Scope and decisions agreed with Jannes the same day. Status: ready to
phases 0 to 2 built on branch `feature/pane-redesign` the same day (see "Built" below).
Phase 3 waits for four state frames in Figma.

## Scope

Three tabs replace today's two (live, options):

| Tab | Figma frame | Content |
|---|---|---|
| **Spend** (default) | "TokenTab / Budget / A hero" (860:3) | SESSION SPEND label with an `Over · 181%` badge, the session cost as a hero figure, a bar against the session limit, `532,533 of 90M tokens · warn only, warns at 70%`. DETAILS: SESSION with message count, a stacked bar by rate, the table (input, cache write 1h, cache read, output text, thinking: tokens, cost, %), total. ALL SESSIONS: Today, This month |
| **Budget** | "TokenTab / Limits" (858:65) | Today's Options tab restyled: sections, an Input with "save" per limit, on/off buttons. The "pane" section with the auto-open switch stays first, above "this session" |
| **Tools** | "TokenTab / Tools" (858:162) | TOOLS with tools and calls in the header, the cost note, then per tool its name, calls, cost, a stacked bar (ask / ctx / re-read) and the line of three figures; legend at the end |

The Tools and Limits frames still show the old five-tab row; the tab names follow A:
Spend · Budget · Tools. Out of the pane: the Session, Requests and By call source
views, the Context and Per request rows (gone from A in the latest version), and the
small Sankey. "open in browser" stays as a button at the end of the tab row. The
header (title and ×) is the host's pane chrome.

Out of this job: the browser status page. It gets the same visual language later, as a
separate job. The views dropped from the pane (Session, Requests, By call source, the
Sankey, the chart) live there.

## Decisions (2026-10-21)

| Question | Decision |
|---|---|
| Overview layout | Variant A, as updated (no Context and Per request rows) |
| Tabs | Spend · Budget · Tools; hotkeys `s`, `b`, `t`, and `o` for open in browser |
| Sankey, open in browser | The Sankey leaves the pane; the button stays in the tab row |
| Auto-open switch | First section of Budget, as today |
| Desktop and VS Code fidelity | Native `Box`/`Text`/`Button`/`Input` for layout and labels; small `Svg` images for the hero figure, the bars and the mono tables |
| Terminal | The same three tabs, approximated: block-character bars, theme colours, no hero size |
| Colours | Theme keys for text and status (`error`, `success`, `warning`, `subtle`); the Figma series palette for bars with a dark variant each, in `Svg` via `prefers-color-scheme` |
| This month | Calendar month, local time, every session, from the ledger day folders |
| States | All four need a Figma frame first: under budget and warning, job budget active, hard stop, context and cold cache |
| Browser page | Later, as a separate job |

## Constraints that shape the build

- **Remote surfaces have no font size and no monospace `Text`.** Desktop and VS Code
  draw `Text` proportional, which is why the Live tab is an `Svg` today (`svgOf`). So the
  hero figure and the right-aligned mono columns are `Svg`, following `svgOf`: no
  background of their own, light and dark through `prefers-color-scheme`, at most 131,072
  characters each.
- **There is no progress component.** Bars are `Svg` on Desktop and VS Code, coloured
  block characters (`█▌`) in the terminal, and a percentage as text on mobile.
- **Only `Button` takes a press.** Tabs and "open in browser" are Buttons; an `Svg` is a
  leaf.
- **Width varies.** Figma is 380 px. Each `Svg` gets a `viewBox` and scales to the pane's
  width; the native rows wrap. The terminal pane starts at 110 columns.
- **The badge background.** `Over · 181%` sits on a pink background. `Text` takes
  `backgroundColor`; whether it draws with padding on Desktop is spike item one.
  Otherwise the badge goes into the hero `Svg`.

## Architecture

**Data.** Add `tokentab.py --report`: the `report()` dict `R` as JSON (the same
function as the live view and the status line, so the figures agree), plus a new
`month` total. One helper run per redraw feeds all three tabs; the module caches it
like `live` today. `--once` stays for the terminal live view and the spend skill.

- Spend: `R.budget.lines` (session row: cost, limit, tokens, percent), `R.budget.warn_at`
  and `hard_stop`, `R.rates`, `R.total`, `R.totals.today`, the new `month`
- Tools: `R.tools` with `TOOL_DRAWS` (ask, ctx, re-read) and the context tokens
- Budget: `budget.json`, unchanged path through `budget.py set`

**The pane kit, in `register.tsx`.** Each piece below resolves per surface:

- `Section(title, meta)`, `Row(label, sub, value)`, `Legend(items)`
- `Hero(label, value, badge)`: an `Svg` on remote surfaces; bold `Text` with the badge
  as coloured text in the terminal
- `Bar(value, max, tone)` and `StackedBar(parts)`: an `Svg` on remote surfaces, block
  characters in the terminal, a percentage on mobile
- `Table(columns, rows, total)`: one `Svg` on remote surfaces (mono, right-aligned); in
  the terminal, padded `Text` (monospace there)

Tabs become a `TABS` list of three, each a render function over `R`. The `live` and
`flow` state and `svgOf`'s use in the pane go.

**Tokens.** A `tokentab/tokens.json` holds the series palette (rates: input grey,
cache write purple, cache read teal, output orange, thinking pink; tools: ask purple,
ctx teal, re-read orange), each colour light and dark, and the status tones. Both
`register.tsx` and, in the later job, `sankey.py` read it.

## Phases

0. **Spike, half a day.** In Desktop and the terminal, check:
   - `Text backgroundColor` for the badge
   - an `Svg` bar and an `Svg` table at pane width, light and dark
   - whether an `Svg` scales with the pane

   Note the results here.
1. **Data, half a day.** Add `--report` and `month`, and extend
   `tests/test_accounting.py`. "This month" reads every day folder of the month on each
   redraw, so time it on a full ledger. If it is slow, cache the closed days' totals in
   `state/`.
2. **Kit and tabs, two days.** Build the components, then Spend, Budget (restyle of
   `optionsTab`) and Tools; add hotkeys and the loading and error lines. Extend
   `register.test.tsx` per tab. Check the result in a 160-column tmux and in Desktop,
   light and dark.
3. **States, one day, after the Figma frames.** Under budget and warning, job budget,
   hard stop, context and cold cache.
4. **Docs.** Update the README's pane section (three tabs, hotkeys, no Sankey in the
   pane) and the module's header comment.

About four days, plus the wait for the state frames.

## Built (2026-10-21, branch `feature/pane-redesign`)

- `tokentab.py --report`: `pane_report()` over the same `report()`, plus `month_total()`.
  Covered by `pane_check` in `tests/test_accounting.py`.
- `hooks/register.tsx`: three tabs, Spend · Budget · Tools (hotkeys `s`, `b`, `t`, `o`
  for open in browser), drawn from one `--report` run per redraw. The Live tab, the
  small Sankey, `svgOf` and the ANSI-to-spans code are gone; `liveLines` stays for the
  figures `/tokentab:status` hands Claude.
- Spike result, better than planned: the 2.1.304 API has `Text size` (caption to
  title), `weight`, `mono` and `tabular`, and `Box backgroundColor` and `borderStyle`.
  The hero figure, the mono tables and the columns are native `Text`; only the bars are
  `Svg` (`barSvg`, light and dark by `prefers-color-scheme`), block characters in the
  terminal (`barCells`).
- **2.1.292 refuses those props** (and `gap` step names like `"xs"`) and draws its own
  pane instead. The plugin now needs 2.1.304 (README, `scripts/loaded-check.sh`).
- `hooks/register.test.tsx`: 14 tests, all passing on 2.1.304; `tsc` clean against the
  2.1.304 types; `claude plugin validate` passes.
- Checked in a 170-column tmux terminal: the tabs and the Spend empty state draw, with
  today and this month from the real ledger. Not yet seen: a filled Spend tab and the
  Tools and Budget tabs on a real surface (the tmux CLI was not logged in), and
  anything in Claude Desktop.

Small deviations from Figma: the badge is coloured text without its pink pill (no
light/dark-safe background key); the line under the hero bar adds the dollar limit
(`of $25.00 · …`), which A leaves out; Tools lists at most 12 tools, the rest in the
browser view.

## Still open

- **The four state frames** (Figma, design). They block phase 3 only.
- **Precision.** I'll follow Figma: the hero shows 2 decimals (`$3.61`), the table and
  tools 4 (`$3.6130`), the tokens line full digits with the limit short
  (`532,533 of 90M`), and Today and This month 2 decimals. Say if you want a different
  rule.
- **Sample data.** The Figma sample doesn't add up: a $2 session limit beside a 90M
  token limit. That doesn't matter for building it, but it's worth fixing before the
  frames are shown around.
