import { test, expect, mock } from 'claude-code/testing'
import type { On, RenderElement, RenderNode } from 'claude-code'
import { at, barCells, barSvg, int, liveLines, pct, SERIES, short, shown, stripAnsi, toneOf, usd } from './register'
import type { Report } from './register'

// The world beneath the plugin: `$.fs` reaches nothing under `claude plugin test`, and
// nothing answers `telemetry.log`, `session.id`, the pane, the command, the store, the
// config menu or a process, so the test stands in for them. Files are matched by their
// tail, since the plugin's root is the engine's to know. `ui` records what the plugin
// asked the surface for, and `placed` says what `$.ui.panes()` reports for the pane.
type Ui = {
  opens: number; commands: string[]; status: (string | undefined)[]; invalidates: number
  runs: string[][]; toasts: string[]; config: [string, unknown][]; store: Map<string, unknown>
}
function world(on: On, initial: Record<string, string>, placed = { is: true }, report: { value: Report } = { value: REPORT }) {
  const fs = new Map(Object.entries(initial))
  const find = (path: string) => [...fs.keys()].find(k => k === path || path.endsWith(k))
  on('fs.read', ($, e) => { const k = find(e.path); return k ? { value: fs.get(k)! } : { deny: `ENOENT: ${e.path}` } })
  on('fs.write', ($, e) => { fs.set(e.path, e.text); return { value: undefined } })
  on('fs.exists', ($, e) => ({ value: find(e.path) !== undefined }))
  on('session.id', () => ({ value: SID }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('telemetry.log', { to: 'collector' }, () => ({ value: undefined }))
  const ui: Ui = { opens: 0, commands: [], status: [], invalidates: 0, runs: [], toasts: [], config: [], store: new Map() }
  on('ui.open', () => { ui.opens++; return { value: placed.is ? { isPlaced: true } : { isPlaced: false, reason: 'narrower than 144 columns' } } })
  on('ui.panes', () => ({ value: [{ id: 'tokentab', title: 'TokenTab', isShown: placed.is, isFocused: false, isPlaced: placed.is }] }))
  on('ui.status', ($, e) => { ui.status.push(e.text); return { value: undefined } })
  on('ui.invalidate', () => { ui.invalidates++; return { value: undefined } })
  on('ui.toast', ($, e) => { ui.toasts.push(e.text); return { value: undefined } })
  on('command.register', ($, e) => { ui.commands.push(e.name); return { value: { command: e.name } } })
  on('store.get', ($, e) => ({ value: ui.store.get(e.key) }))
  on('store.set', ($, e) => { ui.store.set(e.key, e.value); return { value: undefined } })
  on('config.set', ($, e) => { ui.config.push([e.key, e.value]); return { value: e.value } })
  on('process.run', ($, e) => {
    const argv = [...e.argv]
    ui.runs.push(argv)
    const script = (argv[1] ?? '').split('/').pop()
    let exitCode = 0, stdout = '', stderr = ''
    if (script === 'tokentab.py') stdout = argv.includes('--status') ? STATUS + '\n' : argv.includes('--report') ? JSON.stringify(report.value) + '\n' : argv.includes('--once') ? ONCE : ''
    else if (script === 'sankey.py') stdout = argv.includes('--out') ? argv[argv.indexOf('--out') + 1] + '\n' : ''
    else if (script === 'budget.py') {
      if (argv[argv.length - 1] === 'bad') { exitCode = 1; stderr = `${argv[3]}: a number is needed, not 'bad'\n` }
      else stdout = JSON.stringify(argv[argv.length - 1]) + '\n'
    }
    return { value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return { fs, ui }
}

// What `tokentab.py --once` prints (cut down): the header, a dim line, a bold total, a
// green budget bar, the blank line and the `ctrl-c to quit` line a pane has no use for.
const ONCE = [
  '\x1b[1mTokenTab\x1b[0m \x1b[2m· live\x1b[0m\x1b[2m        12:43:54\x1b[0m',
  '',
  '  \x1b[2mdata: ledger (every API call)\x1b[0m',
  '    \x1b[1mtotal                  960,195     $2.0228\x1b[0m   \x1b[2m11 msgs\x1b[0m',
  '    session   \x1b[32m██\x1b[2m····\x1b[0m     8%   \x1b[2m$2.0228 of $25.00\x1b[0m',
  '',
  '  \x1b[2mctrl-c to quit\x1b[0m',
  '',
].join('\n')
const STATUS = 'session $2.0228 · today $7.9406 · job $17.98 left (a pizza ($20.00))'
// What `tokentab.py --report` prints, the figures of Figma's frames: a session over its
// $2.00 limit (181%), warn only, and three tools.
const REPORT: Report = {
  sid: 'a0b1c2d3-session', prices_verified: true, today: 5.94, month: 28.38, overhead: 0.0039,
  session: { title: 'Token type sidebar', cost: 3.613, tokens: 532533, msgs: 47, limit_usd: 2, limit_tokens: 90000000, frac: 1.8065, warn_at: 70, hard_stop: false },
  rates: [
    { label: 'input', key: 'input', tokens: 5134, cost: 0.0205, share: 0.0057 },
    { label: 'cache write 1h', key: 'cache_write_1h', tokens: 226784, cost: 1.8143, share: 0.5022 },
    { label: 'cache read', key: 'cache_read', tokens: 5293202, cost: 1.0586, share: 0.293 },
    { label: 'output text', key: 'output', tokens: 23918, cost: 0.4784, share: 0.1324 },
    { label: 'thinking', key: 'thinking', tokens: 12061, cost: 0.2412, share: 0.0668 },
  ],
  total: { tokens: 5561099, cost: 3.613 }, unmatched: 125,
  tools: [
    { name: 'use_figma', calls: 12, cost: 0.5457, ask: 0.3332, ctx: 0.1498, read: 0.0627, ctx_tokens: 18721 },
    { name: 'Bash', calls: 5, cost: 0.0373, ask: 0.0183, ctx: 0.0148, read: 0.0042, ctx_tokens: 1853 },
    { name: 'computer', calls: 1, cost: 0.0072, ask: 0.0017, ctx: 0.004, read: 0.0015, ctx_tokens: 503 },
  ],
}
const BUDGET = JSON.stringify({
  _comment: 'kept', session_limit_usd: 25, session_limit_tokens: 90000000, turn_limit_usd: 2, turn_limit_tokens: null,
  daily_limit_usd: 40, daily_limit_tokens: 40000000, warn_at_percent: 70, hard_stop: false,
  job: { ask: true, ask_over_usd: 1.0, hard_stop: false },
}, null, 2)

const PANE_PROPS = { title: 'TokenTab', isFocused: false, bodyColumns: 80, placement: 'dock' as const, scroll: { offset: 0, bodyRows: 40 }, view: {} }

/** The `source` of every element of one type (Svg, Code) in a tree. */
function sources(node: RenderNode, type: string): string[] {
  if (typeof node === 'string') return []
  const el = node as RenderElement & { children?: RenderNode[]; props?: { source?: string } }
  const below = (el.children ?? []).flatMap(c => sources(c, type))
  return el.type === type ? [el.props?.source ?? '', ...below] : below
}

/** Every element of one type in a tree, with its props. */
function elements(node: RenderNode, type: string): Record<string, unknown>[] {
  if (typeof node === 'string') return []
  const el = node as RenderElement & { children?: RenderNode[]; props?: Record<string, unknown> }
  const below = (el.children ?? []).flatMap(c => elements(c, type))
  return el.type === type ? [(el.props ?? {}) as Record<string, unknown>, ...below] : below
}

/** Every Text in a tree, each as its own string plus its style props. */
function texts(node: RenderNode): { text: string; props: Record<string, unknown> }[] {
  if (typeof node === 'string') return []
  const el = node as RenderElement & { children?: RenderNode[] }
  const own = (el.children ?? []).filter((c): c is string => typeof c === 'string').join('')
  const below = (el.children ?? []).flatMap(texts)
  return el.type === 'Text' ? [{ text: own, props: (el.props ?? {}) as Record<string, unknown> }, ...below] : below
}

const SID = 'a0b1c2d3-session'
const PRICES = {
  _verified: true,
  models: { 'claude-fable-5-1': { input: 10, output: 50, cache_write_5m: 12.5, cache_write_1h: 20, cache_read: 0.25 } },
  default: { input: 4, output: 20, cache_write_5m: 5, cache_write_1h: 8, cache_read: 0.2 },
}
const DATA = '/home/t/.claude/plugins/data/tokentab-supertab'
const T0 = Date.UTC(2026, 9, 15, 14, 3, 11, 412)
const FILES = { '/tokentab/prices.json': JSON.stringify(PRICES), '/tokentab/budget.json': BUDGET }
const press = (key: string, surface?: 'terminal' | 'desktop') => ({ plugin: 'tokentab', key, requestId: 'tokentab', ...(surface ? { surface } : {}) })

function request(attributes: Record<string, string | number>) {
  return {
    to: 'collector' as const, event: 'api_request', loggedAt: '2026-10-15T14:03:11.412Z',
    attributes: {
      model: 'claude-fable-5-1', input_tokens: 12, output_tokens: 480, cache_read_tokens: 71000,
      cache_creation_tokens: 2100, query_source: 'main_loop', request_id: 'req_1', duration_ms: 2530, ...attributes,
    },
  }
}

test('session.start writes the loaded marker and a first budget.json', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  mock.clock(on, { now: T0 })
  const { fs } = world(on, { '/tokentab/budget.json': '{"session_limit_usd": 25}\n' })
  await $.session.start({ cwd: '/w', surface: null, isInteractive: false })
  expect(fs.get(`${DATA}/state/loaded/${SID}`)).toBe('2026-10-15T14:03:11.412Z\n')
  expect(fs.get(`${DATA}/budget.json`)).toBe('{"session_limit_usd": 25}\n')
  fs.set(`${DATA}/budget.json`, 'edited by the user')
  await $.session.start({ cwd: '/w', surface: null, isInteractive: false })
  expect(fs.get(`${DATA}/budget.json`)).toBe('edited by the user')
})

test('with CLAUDE_CONFIG_DIR set, the data folder is under that config folder', async ($, on) => {
  mock.env(on, { HOME: '/home/t', CLAUDE_CONFIG_DIR: '/home/t/.claude-work' })
  mock.clock(on, { now: T0 })
  const { fs } = world(on, { '/tokentab/budget.json': '{"session_limit_usd": 25}\n' })
  await $.session.start({ cwd: '/w', surface: null, isInteractive: false })
  const data = '/home/t/.claude-work/plugins/data/tokentab-supertab'
  expect(fs.get(`${data}/state/loaded/${SID}`)).toBe('2026-10-15T14:03:11.412Z\n')
  expect(fs.get(`${data}/budget.json`)).toBe('{"session_limit_usd": 25}\n')
  expect(fs.has(`${DATA}/budget.json`)).toBe(false)
})

test('an api_request record becomes one priced ledger line per request', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  mock.clock(on, { now: T0 })
  const { fs } = world(on, { '/tokentab/prices.json': JSON.stringify(PRICES) })
  const path = `${DATA}/ledger/2026-10-15/${SID}.jsonl`

  // tokens: 12 in, 480 out, 71000 cache read, 2100 cache write (at the 1h rate)
  const tokens = (12 * 10 + 480 * 50 + 71000 * 0.25 + 2100 * 20) / 1e6   // 0.08387
  await $.telemetry.log(request({ cost_usd: tokens + 0.01 }))              // one web search on top
  const lines = fs.get(path)!.trimEnd().split('\n').map(l => JSON.parse(l))
  expect(lines.length).toBe(1)
  expect(lines[0]).toEqual({
    ts: '2026-10-15T14:03:11.412Z', source: 'mod', session_id: SID, model: 'claude-fable-5-1',
    tokens: { input: 12, output: 480, cache_read: 71000, cache_write: 2100 },
    cost_usd: 0.08387, cost_usd_cc: tokens + 0.01, fees_usd: 0.01, duration_ms: 2530,
    query_source: 'main_loop', request_id: 'req_1', prices_verified: true,
  })

  // a second request appends; an unknown model is priced at the default rates, and a
  // difference under half a tenth of a cent is not a fee
  await $.telemetry.log(request({ model: 'claude-new', request_id: 'req_2', cost_usd: 0.0, query_source: 'sdk' }))
  const two = fs.get(path)!.trimEnd().split('\n').map(l => JSON.parse(l))
  expect(two.length).toBe(2)
  expect(two[0].request_id).toBe('req_1')
  expect(two[1].request_id).toBe('req_2')
  expect(two[1].cost_usd).toBe(Math.round((12 * 4 + 480 * 20 + 71000 * 0.2 + 2100 * 8) / 1e6 * 1e6) / 1e6)
  expect(two[1].fees_usd).toBe(0)
  expect(two[1].cost_usd_cc).toBe(0)

  // a crash in the middle of the in-place write leaves a cut last line: the next append
  // drops that tail and keeps the rest; no `.tmp` twin is written beside the file
  fs.set(path, fs.get(path)!.slice(0, -20))
  await $.telemetry.log(request({ request_id: 'req_3' }))
  const three = fs.get(path)!.trimEnd().split('\n').map(l => JSON.parse(l))
  expect(three.map(l => l.request_id)).toEqual(['req_1', 'req_3'])
  expect([...fs.keys()].some(k => k.endsWith('.tmp'))).toBe(false)
})

test('records that are not api_request, and a failure in the module, leave the record alone', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  mock.clock(on, { now: T0 })
  const { fs } = world(on, {})   // no prices.json: the module's own read fails
  await $.telemetry.log({ to: 'collector', event: 'tool_result', attributes: { tool_name: 'Bash' }, loggedAt: '2026-10-15T14:03:11.412Z' })
  await expect($.telemetry.log(request({}))).resolves.toEqual({ value: undefined })
  expect([...fs.keys()].filter(k => k.includes('/ledger/')).length).toBe(0)
})

test('the figures as the design writes them, and the bars in Svg and in cells', async () => {
  expect([usd(3.613), usd(3.613, 4), usd(1234.5), int(5561099)]).toEqual(['$3.61', '$3.6130', '$1,234.50', '5,561,099'])
  expect([short(90000000), short(100000), short(1500000), short(512)]).toEqual(['90M', '100k', '1.5M', '512'])
  expect(pct(1.8065)).toBe('181%')
  expect([toneOf(1.81, 70), toneOf(0.75, 70), toneOf(0.1, 70)]).toEqual(['error', 'warning', 'success'])
  // a track the full width, the parts on it; past the end a part is cut at the track's end;
  // light and dark fills by class, no ground of its own
  const svg = barSvg([{ series: 'ask', frac: 0.5 }, { series: 'ctx', frac: 0.8 }])
  expect(svg).toContain('<rect class="track" x="0" y="0" width="1000" height="6"/>')
  expect(svg).toContain('<rect class="ask" x="0.0" y="0" width="497.0" height="6"/>')
  expect(svg).toContain('<rect class="ctx" x="500.0" y="0" width="500.0" height="6"/>')
  expect(svg).toContain('@media(prefers-color-scheme:dark)')
  expect(barCells([{ series: 'ask', frac: 0.5 }, { series: 'ctx', frac: 0.2 }], 10)).toEqual([
    { text: '█████', series: 'ask' }, { text: '██', series: 'ctx' }, { text: '···' },
  ])
  expect(barCells([{ series: 'error', frac: 1.8 }], 4)).toEqual([{ text: '████', series: 'error' }])
  // the ctrl-c line and the blank lines after the view are dropped (the figures for Claude)
  expect(liveLines(ONCE).map(stripAnsi).slice(-1)).toEqual(['    session   ██····     8%   $2.0228 of $25.00'])
  // the budget fields: dotted keys, "off" for a limit switched off, grouped tokens
  const cfg = JSON.parse(BUDGET)
  expect(at(cfg, 'job.ask_over_usd')).toBe(1)
  expect(at(cfg, 'job.missing')).toBe(undefined)
  expect(shown(at(cfg, 'turn_limit_tokens'))).toBe('off')
  expect(shown(at(cfg, 'daily_limit_tokens'))).toBe('40,000,000')
  expect(shown(at(cfg, 'turn_limit_usd'))).toBe('2')
})

test('an interactive session opens the pane and draws the Spend tab', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES)
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  await clock.settle()                                        // the redraw session.start left running
  expect(ui.opens).toBe(1)
  expect(ui.commands).toEqual([])                             // nothing registered: the skill is the command
  // placed: the report was fetched once, with the session pinned, and the status line cleared
  expect(ui.runs.map(a => a.slice(2))).toEqual([['--report', '--session', SID]])
  expect(ui.status).toEqual([undefined])

  const tree = await $.ui.render({ surface: 'terminal', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  // the tab row: Spend is the primary one, then Budget, Tools and the browser button
  expect(elements(tree, 'Button').map(b => [b.key, b.variant, b.hotkey])).toEqual([
    ['tab-spend', 'primary', 's'], ['tab-budget', 'secondary', 'b'], ['tab-tools', 'secondary', 't'], ['open-browser', undefined, 'o'],
  ])
  const t = texts(tree)
  const find = (s: string) => t.find(x => x.text === s)
  // the hero: the figure at the title size, the badge in the error tone, the line under the bar
  expect(find('SESSION SPEND')?.props).toEqual({ size: 'footnote', dimColor: true })
  expect(find('$3.61')?.props).toEqual({ size: 'title', weight: 'bold', tabular: true })
  expect(find('Over · 181%')?.props).toEqual({ size: 'footnote', weight: 'semibold', color: 'error' })
  expect(find('of $2.00 · 532,533 of 90M tokens · warn only, warns at 70%')).toBeDefined()
  // the breakdown: a row per rate with its dot in the series colour, the total, the note
  expect(t.filter(x => x.text === '● ').map(x => x.props.color)).toEqual(
    ['input', 'cache_write_1h', 'cache_read', 'output', 'thinking'].map(k => SERIES[k]![0]))
  for (const s of ['cache write 1h', '226,784', '$1.8143', '50%', '  total', '5,561,099', '$3.6130']) expect(find(s)).toBeDefined()
  expect(t.some(x => x.text.startsWith('125 output tok have no transcript line'))).toBe(true)
  // every session, today and this month
  expect([find('Today'), find('$5.94'), find('This month'), find('$28.38')].every(x => x !== undefined)).toBe(true)
  // the terminal has no Svg: the bars are block characters, the session's in the error tone
  expect(sources(tree, 'Svg')).toEqual([])
  expect(t.find(x => x.props.color === SERIES.error![0])?.text).toMatch(/^█+$/)

  // a remote surface: the same tree with the two bars as Svg, the session's and the rates'
  for (const surface of ['desktop', 'vscode', 'mobile'] as const) {
    const remote = await $.ui.render({ surface, component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
    const svgs = elements(remote, 'Svg')
    expect(svgs.length).toBe(2)
    expect(svgs[0]!.source).toBe(barSvg([{ series: 'error', frac: 1.8065 }], 8))
    expect(svgs[0]!.alt).toBe('181% of the session limit')
    expect(svgs[1]!.alt).toBe('this session by rate: input 1%, cache write 1h 50%, cache read 29%, output text 13%, thinking 7%')
    expect(texts(remote).find(x => x.text === '$3.61')?.props.size).toBe('title')
  }

  // a ledger write redraws: one report run, one invalidate
  await clock.settle()
  const runs = ui.runs.length, invalidates = ui.invalidates
  await $.telemetry.log(request({}))
  await clock.settle()
  expect(ui.runs.slice(runs).map(a => a.slice(2))).toEqual([['--report', '--session', SID]])
  expect(ui.invalidates).toBe(invalidates + 1)
})

test('Spend without a limit has no bar and no badge; before the first call it says so', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const report: { value: Report } = { value: { ...REPORT, session: { ...REPORT.session!, limit_usd: null, limit_tokens: null, frac: null } } }
  world(on, FILES, { is: true }, report)
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  await clock.settle()
  const tree = await $.ui.render({ surface: 'desktop', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  expect(elements(tree, 'Svg').map(s => s.alt)).toEqual([expect.stringMatching(/^this session by rate/)])
  expect(texts(tree).some(x => x.text.startsWith('Over') || x.text.endsWith('used'))).toBe(false)
  expect(texts(tree).some(x => x.text === '532,533 tokens · no session limit set')).toBe(true)

  report.value = { ...REPORT, session: null, tools: [] }
  await $.telemetry.log(request({}))
  await clock.settle()
  const none = await $.ui.render({ surface: 'desktop', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  expect(texts(none).some(x => x.text.startsWith('No API call in this session yet'))).toBe(true)
  expect(texts(none).some(x => x.text === '$28.38')).toBe(true)
  await $.ui.press(press('tab-tools', 'desktop'))
  await clock.settle()
  const tools = await $.ui.render({ surface: 'desktop', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  expect(texts(tools).some(x => x.text === 'No tool calls in this session yet.')).toBe(true)
})

test('the Tools tab: per tool its calls, cost, a bar against the costliest and its three figures', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES)
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  await clock.settle()
  await $.ui.render({ surface: 'desktop', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  const runs = ui.runs.length
  await $.ui.press(press('tab-tools', 'desktop'))
  await clock.settle()
  expect(ui.runs.slice(runs).map(a => a.slice(2))).toEqual([['--report', '--session', SID]])
  const tree = await $.ui.render({ surface: 'desktop', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  expect(elements(tree, 'Button').map(b => [b.key, b.variant]).slice(0, 3)).toEqual([
    ['tab-spend', 'secondary'], ['tab-budget', 'secondary'], ['tab-tools', 'primary'],
  ])
  const t = texts(tree)
  for (const s of ['TOOLS', '  3 tools · 18 calls', 'use_figma', '12 calls', '$0.5457', 'computer', '1 call',
    'ask $0.3332 · ctx $0.1498 (18,721 tok) · re-read $0.0627', 'TokenTab overhead $0.0039: its hook text in context']) {
    expect(t.some(x => x.text === s)).toBe(true)
  }
  // one bar per tool, the costliest filling its track
  const svgs = elements(tree, 'Svg')
  expect(svgs.length).toBe(3)
  const r = REPORT.tools[0]!
  expect(svgs[0]!.source).toBe(barSvg([{ series: 'ask', frac: r.ask / r.cost }, { series: 'ctx', frac: r.ctx / r.cost }, { series: 'read', frac: r.read / r.cost }], 4))
  // the legend
  expect(t.filter(x => x.text === '● ').map(x => x.props.color)).toEqual(['ask', 'ctx', 'read'].map(k => SERIES[k]![0]))
})

test('while the pane is not placed, the status line carries the numbers', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES, { is: false })
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  await clock.settle()
  expect(ui.opens).toBe(1)
  expect(ui.runs.map(a => a.slice(2))).toEqual([['--status', '--session', SID]])
  expect(ui.status).toEqual([STATUS])
})

test('under claude -p, or with no surface, nothing visual happens and the ledger is written', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { fs, ui } = world(on, FILES)
  await $.session.start({ cwd: '/w', surface: null, isInteractive: false })
  await $.telemetry.log(request({}))
  await clock.settle()
  expect(ui.opens).toBe(0)
  expect(ui.runs).toEqual([])
  expect(ui.status).toEqual([])
  expect(fs.get(`${DATA}/ledger/2026-10-15/${SID}.jsonl`)!.trimEnd().split('\n').length).toBe(1)
})

test('with autoOpen off, an interactive session does not open the pane; the status line still runs', { options: { autoOpen: false } }, async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES, { is: false })
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  await clock.settle()
  expect(ui.opens).toBe(0)
  expect(ui.status).toEqual([STATUS])
})

test('/tokentab:status (the skill) is answered by the hook: the pane opens, the figures go to the person and to Claude', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES)
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  const opens = ui.opens
  const r = await $.command.run({ command: 'tokentab:status', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 200 } })
  expect(ui.opens).toBe(opens + 1)
  expect(r.text).toBe(`${STATUS}\nThe TokenTab pane is open beside the chat; "open in browser" there writes the status page.`)
  expect(r.context).toEqual([`TokenTab, this session's figures as of now:\n${liveLines(ONCE).map(stripAnsi).join('\n')}`])
})

test('the open-in-browser button writes the page for this session, opens it, and keeps it live', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES)
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  await clock.settle()
  await $.ui.render({ surface: 'terminal', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  const runs = ui.runs.length
  await $.ui.press(press('open-browser'))
  const page = `${DATA}/status/${SID}.html`
  const named = (a: string[]) => a[0] === 'python3' ? ['python3', a[1]!.split('/').pop()!, ...a.slice(2)] : a
  expect(ui.runs.slice(runs).map(named)).toEqual([
    ['python3', 'sankey.py', '--session', SID, '--out', page],
    ['open', page],
  ])
  expect(ui.toasts).toEqual([`TokenTab: status page opened (${page}); it stays live for 30 minutes`])
  expect(ui.store.get(`pageOpenedAt:${SID}`)).toBe(T0)

  // from now on a redraw writes the page's data file beside it too, in a second run
  const before = ui.runs.length
  await $.telemetry.log(request({}))
  await clock.settle()
  expect(ui.runs.slice(before).map(named)).toEqual([
    ['python3', 'tokentab.py', '--report', '--session', SID],
    ['python3', 'sankey.py', '--session', SID, '--push', `${DATA}/status/${SID}.js`],
  ])
})

test('the Budget tab: auto-open through the config menu, the limits through the validator', async ($, on) => {
  mock.env(on, { HOME: '/home/t' })
  const clock = mock.clock(on, { now: T0 })
  const { ui } = world(on, FILES)
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  await clock.settle()
  await $.ui.render({ surface: 'terminal', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  await $.ui.press(press('tab-budget'))
  const tree = await $.ui.render({ surface: 'terminal', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  const t = texts(tree)
  expect(t.some(x => x.text === 'open at session start')).toBe(true)
  expect(t.filter(x => x.props.bold).map(x => x.text)).toEqual(['pane', 'this session', 'today', 'per request', 'at the limit', 'jobs'])
  expect(elements(tree, 'Button').find(b => b.key === 'auto-open')?.label).toBe('on')
  // the fields hold budget.json's values, "off" for null, grouped tokens
  const inputs = elements(tree, 'Input')
  expect(inputs.map(i => i.key)).toEqual(['b-session_limit_usd', 'b-session_limit_tokens', 'b-daily_limit_usd', 'b-daily_limit_tokens',
    'b-turn_limit_usd', 'b-turn_limit_tokens', 'b-warn_at_percent', 'b-job.ask_over_usd'])
  expect(inputs.find(i => i.key === 'b-daily_limit_usd')?.value).toBe('40')
  expect(inputs.find(i => i.key === 'b-turn_limit_tokens')?.value).toBe('off')
  expect(inputs.find(i => i.key === 'b-daily_limit_tokens')?.value).toBe('40,000,000')
  expect(elements(tree, 'Button').find(b => b.key === 'b-hard_stop')?.label).toBe('off')
  expect(elements(tree, 'Button').find(b => b.key === 'b-job.hard_stop')?.label).toBe('off')

  // the switch is the manifest's userConfig field, written as the menu would
  await $.ui.press(press('auto-open'))
  expect(ui.config).toEqual([['tokentab.autoOpen', false]])

  // a limit goes through budget.py; a refused value is shown under its field
  await $.ui.input({ plugin: 'tokentab', key: 'b-daily_limit_usd', text: '30' })
  expect(ui.runs[ui.runs.length - 1]!.slice(1).map((s, i) => i === 0 ? s.split('/').pop() : s)).toEqual(['budget.py', 'set', 'daily_limit_usd', '30'])
  await $.ui.input({ plugin: 'tokentab', key: 'b-turn_limit_usd', text: 'bad' })
  const after = await $.ui.render({ surface: 'terminal', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  expect(texts(after).find(x => x.props.color === 'error')?.text.trim()).toBe("turn_limit_usd: a number is needed, not 'bad'")
  await $.ui.press(press('b-hard_stop'))
  expect(ui.runs[ui.runs.length - 1]!.slice(2)).toEqual(['set', 'hard_stop', 'on'])

  // mobile has no Input: the values are shown, with where to edit them
  const mobile = await $.ui.render({ surface: 'mobile', component: 'Pane', requestId: 'tokentab', props: PANE_PROPS })
  expect(elements(mobile, 'Input')).toEqual([])
  expect(texts(mobile).some(x => x.text === '40')).toBe(true)
  expect(texts(mobile).some(x => x.text.includes('/tokentab:budget'))).toBe(true)
})
