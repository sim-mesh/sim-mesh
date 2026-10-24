<template>
  <div ref="wrap" class="wmap">
    <canvas
      ref="canvas"
      :class="{ 'wmap-placing': placing, 'wmap-banding': banding, 'wmap-link': overLink }"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
      @pointerleave="onLeave"
      @wheel.prevent="onWheel"
      @contextmenu.prevent="onContext"
    />
    <div class="wmap-scale">
      <span class="wmap-scale-bar" :style="{ width: `${scale.px}px` }" />{{ scale.label }}
    </div>
    <div class="wmap-bottom">
      <div v-if="legend === 'coverage'" class="wmap-legend">
        <b v-if="coverageLabel">{{ coverageLabel }}</b>
        <span v-for="b in COVERAGE_BANDS" :key="b.name">
          <i :style="{ background: `rgba(${b.rgba.slice(0, 3).join(',')}, 0.9)` }" />{{ b.name }}
        </span>
      </div>
      <div v-else-if="legend === 'population'" class="wmap-legend">
        population <i class="wmap-ramp" /> dense
      </div>
      <div v-if="indexing" class="wmap-note wmap-index">
        building footprints: the planner is {{ indexing.phase }}{{ indexing.buildings
          ? `, ${indexing.buildings.toLocaleString()} so far` : '' }}
        <q-linear-progress :value="indexing.fraction ?? 0" :indeterminate="indexing.fraction === null"
                           color="primary" size="3px" class="q-mt-xs" />
      </div>
      <div v-if="note" class="wmap-note">{{ note }}</div>
    </div>
    <div v-if="credits.length" class="wmap-credits" :title="creditsOpen ? '' : 'the sources’ notices'"
         @click="creditsOpen = !creditsOpen">
      <template v-if="creditsOpen">
        <div v-for="l in ground.current?.licences ?? []" :key="l.source"><b>{{ l.source }}</b> {{ l.notice }}</div>
      </template>
      <template v-else>{{ credits.join(' · ') }} ⓘ</template>
    </div>
    <div v-if="busy" class="wmap-busy">loading ground…</div>
    <div v-else-if="coverageBusy && display.coverage" class="wmap-busy">redrawing coverage…</div>
  </div>
</template>

<script setup lang="ts">
/* The map: one canvas, drawn in the geodata's own metres (a pack's CRS, or
 * synthetic ground's metres around 0°, 0°), in layers from the
 * ground up:
 *
 *   base       a pack's ground, the server's bake of terrain or clutter
 *              height over tile.bin's terrain, made into an image off the
 *              main thread (lib/ground.worker.ts); synthetic ground's
 *              grid, in metres or degrees
 *   roads      roads.bin's ways as lines, by class, railways dashed
 *   buildings  footprints from buildings.bin: outlines under 9 km across,
 *              filled by height above the ground under 4 km
 *   population a pack's residents per cell as a heatmap (lib/planner)
 *   coverage   the best decoding margin at each point over the nodes asked
 *              about (stores/coverage), in COVERAGE_BANDS: green indoors
 *              too, yellow outdoors only, red the edge, nothing below 0
 *
 * Population and coverage are heatmaps, one at a time. While one is on, the
 * base, roads, buildings, offsets and rings are drawn grey (images from
 * grey copies made once per paint, lines through `tone`), so the only
 * colours on the map are the heatmap's, the nodes' and the selected node's
 * links.
 *   offsets    a nodeset's offsets as dashed lines with their dB
 *   links      for the one selected node, every other node coloured by the
 *              level it would be heard at: decodable, or only interfering,
 *              solid where the first Fresnel zone is clear
 *   others     nodes only shown (the Nodes tab's list of nodesets), hollow
 *              in their nodeset's colour, named on hover; a click on one is
 *              reported
 *   nodes      a dot each, a second ring round a node that carries others'
 *              traffic, its name and, smaller, its height and tags
 *   live       rings while a station transmits, flashes where a frame lands,
 *              status colours
 *
 * The ground is fetched when the view settles and drawn with drawImage from
 * then on, so panning and zooming cost nothing until the view leaves what was
 * fetched; roads, footprints and coverage are painted into surfaces of their
 * own once per settled view for the same reason. Everything above them is
 * cheap and drawn every frame something moves.
 *
 * The pointer: a click picks a node (Shift or Ctrl/Cmd toggles it into the
 * selection), and on one of the selected node's link lines opens that pair; Ctrl/Cmd and a drag draws a rectangle that picks what is in
 * it (with Shift added to the selection, with Alt taken out of it); a drag
 * on a node moves it, and every other selected node with it; any other drag
 * pans, and the wheel zooms about the cursor. */
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import { useGeodata } from '../stores/geodata'
import { useSim } from '../stores/sim'
import type { Display } from '../stores/display'
import { COVERAGE_BANDS, type CoverageSource } from '../stores/coverage'
import type { Offset } from '../stores/nodes'
import { M_PER_DEGREE } from '../lib/proj'
import { forwardingRole, type GroundPoint, type LinkMark, type MapNode, type OtherNode, type Pick } from '../lib/marks'
import {
  baseImage, buildings, greyed, inside, populationImage, roads,
  type BaseImage, type Box, type Footprint, type Footprints, type Heatmap, type Way,
} from '../lib/planner'

const props = withDefaults(defineProps<{
  nodes?: MapNode[]
  /** Nodes only shown, drawn hollow in their nodeset's colour. */
  others?: OtherNode[]
  offsets?: Offset[]
  selected?: string[]
  /** Nodes can be dragged. */
  editable?: boolean
  /** A simulation is shown: rings, flashes and status colours. */
  live?: boolean
  /** A click on the ground places something: the cursor says so. */
  placing?: boolean
  links?: LinkMark[] | null
  pair?: [string, string] | null
  coverage?: CoverageSource | null
  /** Whose coverage is shown, and how its computing is going, for the key. */
  coverageLabel?: string
  display: Display
  /** Where the view is remembered, per geodata and whatever else. */
  viewKey?: string
}>(), {
  nodes: () => [],
  others: () => [],
  offsets: () => [],
  selected: () => [],
  editable: false,
  live: false,
  placing: false,
  links: null,
  pair: null,
  coverage: null,
  viewKey: 'map',
})

const emit = defineEmits<{
  select: [name: string | null, how: Pick]
  /** The nodes inside a rectangle drawn with Ctrl/Cmd held. */
  pick: [names: string[], how: Pick]
  /** A click on the ground, in metres and degrees, with the footprint under it. */
  ground: [at: GroundPoint]
  move: [name: string, lat: number, lon: number, settle: boolean]
  /** A click on one of the selected node's link lines: its two ends. */
  link: [from: string, to: string]
  /** A right-click, with the node under it when there is one. */
  context: [at: GroundPoint, name: string | null]
  hover: [name: string | null]
  /** A click on a node only shown: its nodeset and its name. */
  other: [layer: string, name: string]
}>()

const ground = useGeodata()
const sim = useSim()
const wrap = ref<HTMLDivElement>()
const canvas = ref<HTMLCanvasElement>()

const NODE_R = 6
const HIT_R = 14
const LINK_HIT_PX = 6
const FLASH_MS = 400
const BLDG_MAX_VIEW_M = 9000
const BLDG_FILL_VIEW_M = 4000
const SETTLE_MS = 160
/* After the wheel, what the view needs is fetched later than its repaint,
 * as the planner's map waits: a wheel gesture delivers notches 100-300 ms
 * apart on many mice, and at less each notch aborted the fetch before it,
 * whose basemap the sidecar then rendered for nobody (measured there: six
 * notches put six renders in flight, two refused with 429, the rest 1.6-2.0
 * s each instead of 0.2 s). The map redraws from what it holds meanwhile.
 * A drag let go has nothing after it to wait for. */
const WHEEL_SETTLE_MS = 300
/* A ground update abandoned after this, so that one request that never
 * answers cannot hold the map: far above a warm update's ~0.3 s and a cold
 * tile from disk, a tripwire, not a budget (the planner's). */
const GROUND_DEADLINE_MS = 20_000
const DRAG_SEND_HZ = 6
const COVERAGE_PX = 4
/** Device pixels per CSS pixel at most, as the planner's map has it: past
 *  1.5 the canvas costs more to fill than the eye gains from it. */
const DPR_CAP = 1.5

/* ── the view: centre in ground metres, metres per CSS pixel ── */
const view = ref({ cx: 0, cy: 0, mpp: 20 })
const hovered = ref<string | null>(null)
const busy = ref(false)
const note = ref<string | null>(null)
/** The pointer is over one of the selected node's link lines: it opens the pair. */
const overLink = ref(false)
/** Which heatmap's key is shown, if either. */
/* The notices of the ground on show, which its sources' licences ask to be
 * seen where it is drawn: one short line, each source once (OpenStreetMap's
 * as its own attribution asks), opening to the notices in full. */
const creditsOpen = ref(false)
const credits = computed(() => [...new Set((ground.current?.licences ?? []).map(l =>
  l.source.startsWith('OpenStreetMap') ? '© OpenStreetMap contributors' : l.source))])
const legend = computed<'coverage' | 'population' | null>(() =>
  props.display.population && ground.isPack ? 'population'
  : props.display.coverage && props.coverage ? 'coverage' : null)
const banding = ref(false)
let size = { w: 0, h: 0, dpr: 1 }
let ctx: CanvasRenderingContext2D | null = null
let frame = 0
let redrawWanted = true

function toScreen(x: number, y: number): [number, number] {
  const v = view.value
  return [size.w / 2 + (x - v.cx) / v.mpp, size.h / 2 - (y - v.cy) / v.mpp]
}
function fromScreen(sx: number, sy: number): [number, number] {
  const v = view.value
  return [v.cx + (sx - size.w / 2) * v.mpp, v.cy - (sy - size.h / 2) * v.mpp]
}
function viewBox(pad = 0): Box {
  const v = view.value
  const hw = size.w / 2 * v.mpp * (1 + pad), hh = size.h / 2 * v.mpp * (1 + pad)
  return { minx: v.cx - hw, maxx: v.cx + hw, miny: v.cy - hh, maxy: v.cy + hh }
}
function viewWidthM() { return size.w * view.value.mpp }

/* ── node positions in metres, recomputed when the nodes or the ground change ── */
const positions = computed(() => {
  const f = ground.frame
  const out = new Map<string, [number, number]>()
  for (const d of props.nodes) out.set(d.name, f.toXY(d.lat, d.lon))
  return out
})
const chosen = computed(() => new Set(props.selected))
function otherKey(o: OtherNode) { return `${o.layer}\u0000${o.name}` }
const otherXY = computed(() => {
  const f = ground.frame
  return new Map(props.others.map(o => [otherKey(o), f.toXY(o.lat, o.lon)]))
})
const hoveredOther = ref<string | null>(null)
function posOf(name: string): [number, number] | null {
  const p = positions.value.get(name) ?? null
  if (p && drag?.moving && drag.shift && drag.group.has(name)) {
    return [p[0] + drag.shift[0], p[1] + drag.shift[1]]
  }
  return p
}

/* ── the view is remembered per geodata ──
 * Maps with the same viewKey share it (the Geodata and Nodes tabs do): each
 * keeps the last one in the geodata store as it moves, and one coming on
 * show, or on show when the other moved, takes it up. */
function storageKey() { return `sim-mesh.view.${ground.current?.name ?? '-'}.${props.viewKey}` }
function saveView() {
  ground.views[storageKey()] = { ...view.value }
  try { localStorage.setItem(storageKey(), JSON.stringify(view.value)) } catch { /* private window */ }
}
function adoptShared(): boolean {
  const held = ground.views[storageKey()]
  if (!held || (held.cx === view.value.cx && held.cy === view.value.cy && held.mpp === view.value.mpp)) return !!held
  view.value = { ...held }
  settled()
  return true
}
function restoreView() {
  if (adoptShared()) return
  try {
    const held = localStorage.getItem(storageKey())
    if (held) { view.value = JSON.parse(held); settled(); return }
  } catch { /* nothing kept */ }
  fitToNodes()
}

/** Every node on screen with a margin (the hollow ones when there are no
 *  filled ones); with none, the pack or the synthetic extent. */
function fitToNodes() {
  const points = positions.value.size ? [...positions.value.values()] : [...otherXY.value.values()]
  if (!points.length) {
    const pack = ground.pack
    const g = ground.current
    if (pack) {
      const e = pack.extent
      view.value = { cx: (e.minx + e.maxx) / 2, cy: (e.miny + e.maxy) / 2,
                     mpp: Math.max((e.maxx - e.minx) / Math.max(size.w, 200), 1) }
    } else {
      const extent = g?.extent_m ?? 20000
      view.value = { cx: 0, cy: 0, mpp: Math.max(extent / Math.max(size.w - 60, 200), 1) }
    }
    settled()
    return
  }
  const xs = points.map(p => p[0]), ys = points.map(p => p[1])
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const spanX = Math.max(maxX - minX, 300), spanY = Math.max(maxY - minY, 300)
  view.value = {
    cx: (minX + maxX) / 2, cy: (minY + maxY) / 2,
    mpp: Math.max(spanX / Math.max(size.w - 140, 200), spanY / Math.max(size.h - 140, 200)),
  }
  settled()
}

/** Centre the view on a point, at `span` metres across when given. */
function goTo(x: number, y: number, span?: number) {
  view.value = { cx: x, cy: y, mpp: span ? span / Math.max(size.w, 200) : view.value.mpp }
  pin = [x, y]
  saveView()
  settled()
}

/** Fit the view to the nodes when asked to, and keep it. */
function fitAndKeep() {
  fitToNodes()
  saveView()
}

/** The footprint at a ground point, from what is loaded. */
function footprintAt(x: number, y: number): Footprint | null {
  for (const f of footprintsIn({ minx: x, miny: y, maxx: x, maxy: y })) if (inside(f, x, y)) return f
  return null
}

/** Ground height at a ground point, from the loaded tile's terrain. */
function groundAt(x: number, y: number): number | null {
  const g = detail?.grid
  if (!g) return null
  const col = Math.round((x - g.ox) / Math.abs(g.rx)), row = Math.round((g.oy - y) / Math.abs(g.ry))
  if (col < 0 || row < 0 || col >= g.w || row >= g.h) return null
  const v = g.terrain[row * g.w + col]!
  return Number.isFinite(v) ? v : null
}

defineExpose({ fitToNodes: fitAndKeep, goTo, footprintAt, groundAt })

/* ── the ground, fetched when the view settles ── */
interface Held { image: BaseImage; grid: BaseImage['grid']; key: string; res: number
  /** The image in grey, made the first time a heatmap lies over it. */
  grey?: HTMLCanvasElement }
let overview: Held | null = null
let detail: Held | null = null
/* The population heatmap, fetched as the ground is while its layer is on. */
let population: { image: Heatmap; key: string; res: number } | null = null
let populationReq: AbortController | null = null
let groundReq: AbortController | null = null
let overviewReq: AbortController | null = null
/* Footprints come in squares of the ground, FOOT_TILE_M across on a grid
 * from the CRS origin, each fetched once and kept (the nearest
 * FOOT_TILES_KEPT of them). The sidecar caps a reply's vertices and fills
 * it in the pack's order, not the reply box's, so a reply that says it was
 * cut short is thrown away and its square asked for again as four. A
 * building across a square's edge comes in both, and is held and drawn once.
 *
 * What the squares hold is one cache of buildings by the sidecar's id, as
 * the planner's own map keeps them: a building already held keeps its
 * geometry, and the squares holding it are counted, so it goes when the
 * last of them does. A coarse index finds the buildings under a box:
 * buckets FOOT_BUCKET_M on a side, each building in every bucket its box
 * touches, so drawing a view visits the few dozen buckets under it rather
 * than every building held. */
const FOOT_TILE_M = 1000
const FOOT_TILE_MIN_M = 125
const FOOT_TILES_KEPT = 600
const FOOT_PARALLEL = 4
interface FootTile { size: number; ix: number; iy: number; state: 'queued' | 'loading' | 'done' | 'split'; list: Footprint[] }
const footTiles = new Map<string, FootTile>()
let footQueue: string[] = []
let footActive = 0
let footGeneration = 0
/** The pack has no footprints (or the sidecar is still indexing them). */
let footNone = false
let footPaintTimer: ReturnType<typeof setTimeout> | null = null
/** Squares landed since the overlay was last painted, in the order they landed. */
let footLanded: FootTile[] = []
let footprintTries = 0
const FOOTPRINT_TRIES = 30
/** The sidecar indexing the pack's buildings, as /buildings/status says it,
 *  while the footprints wait for it; null when they do not. */
const indexing = ref<{ phase: string; fraction: number | null; buildings: number } | null>(null)
let indexWatch = false
let indexPoll: ReturnType<typeof setTimeout> | null = null
const INDEX_POLL_MS = 500
const FOOT_BUCKET_M = 500
/** Every building held, by id, with how many held squares have it. */
const footHeld = new Map<number, { f: Footprint; squares: number }>()
/** Bucket key to the ids of the buildings in that bucket. */
const footIndex = new Map<number, Set<number>>()
const bucketKey = (bx: number, by: number) => bx * 1048576 + by

/** Each bucket a building's box touches. */
function forBuckets(b: Box, each: (key: number) => void) {
  const bx1 = Math.floor(b.maxx / FOOT_BUCKET_M), by1 = Math.floor(b.maxy / FOOT_BUCKET_M)
  for (let bx = Math.floor(b.minx / FOOT_BUCKET_M); bx <= bx1; bx++) {
    for (let by = Math.floor(b.miny / FOOT_BUCKET_M); by <= by1; by++) each(bucketKey(bx, by))
  }
}

/** A square's buildings into the cache; those held already keep their geometry. */
function holdSquare(t: FootTile) {
  for (const f of t.list) {
    const held = footHeld.get(f.id)
    if (held) { held.squares++; continue }
    footHeld.set(f.id, { f, squares: 1 })
    forBuckets(f.box, (k) => {
      let ids = footIndex.get(k)
      if (!ids) { ids = new Set(); footIndex.set(k, ids) }
      ids.add(f.id)
    })
  }
}

/** A square let go: its buildings that no other held square has go with it. */
function dropSquare(t: FootTile) {
  for (const f of t.list) {
    const held = footHeld.get(f.id)
    if (!held || --held.squares > 0) continue
    footHeld.delete(f.id)
    forBuckets(held.f.box, (k) => {
      const ids = footIndex.get(k)
      if (ids) { ids.delete(f.id); if (!ids.size) footIndex.delete(k) }
    })
  }
}

function footKey(size: number, ix: number, iy: number) { return `${size}:${ix}:${iy}` }
function footBox(t: { size: number; ix: number; iy: number }): Box {
  return { minx: t.ix * t.size, miny: t.iy * t.size, maxx: (t.ix + 1) * t.size, maxy: (t.iy + 1) * t.size }
}
function meets(a: Box, b: Box) {
  return a.minx < b.maxx && a.maxx > b.minx && a.miny < b.maxy && a.maxy > b.miny
}

/** Every footprint held whose box meets `box`, each once. */
function footprintsIn(box: Box): Footprint[] {
  const out: Footprint[] = [], seen = new Set<number>()
  forBuckets(box, (k) => {
    for (const id of footIndex.get(k) ?? []) {
      if (seen.has(id)) continue
      seen.add(id)
      const f = footHeld.get(id)!.f
      if (f.box.maxx >= box.minx && f.box.minx <= box.maxx && f.box.maxy >= box.miny && f.box.miny <= box.maxy) out.push(f)
    }
  })
  return out
}
let pin: [number, number] | null = null

let ways: Way[] | null = null
let wayBox: Box | null = null
let wayReq: AbortController | null = null

function groundKey() { return `${ground.current?.name}|${props.display.base}` }

function covers(outer: Box, inner: Box) {
  return outer.minx <= inner.minx && outer.maxx >= inner.maxx
      && outer.miny <= inner.miny && outer.maxy >= inner.maxy
}

let settleTimer: ReturnType<typeof setTimeout> | null = null
let fetchTimer: ReturnType<typeof setTimeout> | null = null
/** The view has moved: redraw now, paint what it holds once it stops, and
 *  fetch what it needs then, or, after the wheel, a little later. */
function settled(wheel = false) {
  redrawWanted = true
  if (settleTimer) clearTimeout(settleTimer)
  if (fetchTimer) { clearTimeout(fetchTimer); fetchTimer = null }
  // Asked for before the overlay is painted, which would hold the requests back.
  settleTimer = setTimeout(() => { settleTimer = null; if (!wheel) fetchView(); paintOverlay() }, SETTLE_MS)
  if (wheel) fetchTimer = setTimeout(() => { fetchTimer = null; fetchView() }, WHEEL_SETTLE_MS)
}

/** What the view needs, asked for. */
function fetchView() {
  fetchGround(); fetchFootprints(); fetchRoads(); void paintCoverage()
  void fetchPopulation()
}

/** Whether a heatmap is on show: population, or coverage with something painted. */
function heatmap(): boolean {
  return (props.display.population && ground.isPack) || (props.display.coverage && coverageSurface.on)
}

async function fetchPopulation() {
  const base = ground.sidecar, pack = ground.pack
  if (!base || !pack || !props.display.population || size.w < 40) return
  const want = viewBox()
  const wantRes = view.value.mpp * 2
  const key = ground.current?.name ?? ''
  if (population && population.key === key && covers(population.image.bounds, want)
      && wantRes > population.res / 1.6 && wantRes < population.res * 1.6) return
  populationReq?.abort()
  const ctrl = new AbortController()
  populationReq = ctrl
  const box = viewBox(0.6)
  // Half the screen's resolution: the raster is coarser than that close in.
  const w = Math.round(size.w * 0.8), h = Math.round(size.h * 0.8)
  try {
    const image = await populationImage(base, box, w, h, ctrl.signal)
    if (ctrl.signal.aborted) return
    if (!image) { note.value = 'this pack has no population layer'; return }
    population = { image, key, res: (box.maxx - box.minx) / image.bitmap.width }
    redrawWanted = true
  } catch (e) {
    if ((e as Error).name !== 'AbortError') note.value = `population: ${(e as Error).message}`
  } finally {
    if (populationReq === ctrl) populationReq = null
  }
}

/* A map not on show, the other tab's, asks for nothing, as for the detail
 * tile: both tabs' maps are mounted, and the hidden one's overview, roads
 * and footprints came to 1.3 MB of every pack opened. It asks when it
 * comes on show. */
async function fetchOverview() {
  const base = ground.sidecar, pack = ground.pack
  overviewReq?.abort()
  overview = null
  if (!base || !pack || size.w < 40) return
  const ctrl = new AbortController()
  overviewReq = ctrl
  const key = groundKey()
  const e = pack.extent
  const w = 480, h = Math.max(64, Math.round(480 * (e.maxy - e.miny) / (e.maxx - e.minx)))
  try {
    const image = await baseImage(base, e, w, h, props.display.base, ctrl.signal)
    if (ctrl.signal.aborted) return
    overview = { image, grid: image.grid, key, res: (e.maxx - e.minx) / w }
    redrawWanted = true
  } catch { /* no overview: the detail tile still comes */ }
}

async function fetchGround() {
  const base = ground.sidecar, pack = ground.pack
  if (!base || !pack || size.w < 40) return
  const want = viewBox()
  const wantRes = view.value.mpp
  const key = groundKey()
  if (detail && detail.key === key && covers(detail.image.bounds, want)) {
    const canRefine = detail.res > (pack.res_m || 0) * 1.01
    if (!((canRefine && wantRes < detail.res / 1.35) || wantRes > detail.res * 1.6)) return
  }
  groundReq?.abort()
  const ctrl = new AbortController()
  groundReq = ctrl
  const deadline = setTimeout(() => ctrl.abort(), GROUND_DEADLINE_MS)
  const box = viewBox(0.6)
  const w = Math.round(size.w * 1.6), h = Math.round(size.h * 1.6)
  busy.value = true
  try {
    const image = await baseImage(base, box, w, h, props.display.base, ctrl.signal)
    if (ctrl.signal.aborted) return
    detail = { image, grid: image.grid, key, res: (box.maxx - box.minx) / image.bitmap.width }
    redrawWanted = true
    // Filled footprints are coloured by height over this tile's terrain.
    if (props.display.buildings && viewWidthM() <= BLDG_FILL_VIEW_M) paintOverlay()
  } catch (e) {
    if ((e as Error).name !== 'AbortError') note.value = `ground: ${(e as Error).message}`
  } finally {
    clearTimeout(deadline)
    if (groundReq === ctrl) { groundReq = null; busy.value = false }
  }
}

async function fetchFootprints() {
  const base = ground.sidecar
  if (size.w < 40) return
  if (!base || !props.display.buildings || viewWidthM() > BLDG_MAX_VIEW_M) {
    note.value = base && props.display.buildings && viewWidthM() > BLDG_MAX_VIEW_M
      ? 'zoom in to see buildings' : ground.problem
    return
  }
  if (footNone) return
  if (note.value === 'zoom in to see buildings') note.value = null
  // The view and a margin, nearest squares first so the middle fills first.
  const want = viewBox(0.3)
  const s = FOOT_TILE_M
  const cx = view.value.cx, cy = view.value.cy
  const keys: [number, string][] = []
  for (let ix = Math.floor(want.minx / s); ix * s < want.maxx; ix++) {
    for (let iy = Math.floor(want.miny / s); iy * s < want.maxy; iy++) {
      wantTile(s, ix, iy, want, keys, cx, cy)
    }
  }
  keys.sort((a, b) => a[0] - b[0])
  footQueue = keys.map(k => k[1]).concat(footQueue.filter(k => !keys.some(x => x[1] === k)))
  evictFootTiles(cx, cy)
  pumpFootprints()
}

/** A square wanted: queued when it is new; its quarters when it was split. */
function wantTile(size: number, ix: number, iy: number, want: Box, out: [number, string][], cx: number, cy: number) {
  const t = { size, ix, iy }
  if (!meets(footBox(t), want)) return
  const key = footKey(size, ix, iy)
  const held = footTiles.get(key)
  if (held?.state === 'split') {
    for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]] as const) {
      wantTile(size / 2, ix * 2 + dx, iy * 2 + dy, want, out, cx, cy)
    }
    return
  }
  if (held) {
    if (held.state === 'queued') out.push([Math.hypot((ix + 0.5) * size - cx, (iy + 0.5) * size - cy), key])
    return
  }
  footTiles.set(key, { ...t, state: 'queued', list: [] })
  out.push([Math.hypot((ix + 0.5) * size - cx, (iy + 0.5) * size - cy), key])
}

function pumpFootprints() {
  const base = ground.sidecar
  if (!base) return
  const near = viewBox(0.6)
  while (footActive < FOOT_PARALLEL && footQueue.length) {
    const key = footQueue.shift()!
    const tile = footTiles.get(key)
    if (!tile || tile.state !== 'queued') continue
    // Panned away before its turn: forget it, to be wanted again if it comes back.
    if (!meets(footBox(tile), near)) { footTiles.delete(key); continue }
    tile.state = 'loading'
    footActive++
    void loadTile(base, tile, key, footGeneration)
  }
}

async function loadTile(base: string, tile: FootTile, key: string, generation: number) {
  try {
    const got = await buildings(base, footBox(tile))
    if (generation !== footGeneration) return
    if (got === null) {
      footTiles.delete(key)
      // The sidecar answers the same while it is still indexing the
      // footprints as when the pack has none: its status says which.
      if (!indexWatch) { indexWatch = true; void watchIndex(base, generation) }
      return
    }
    if (got.truncated && tile.size > FOOT_TILE_MIN_M) {
      tile.state = 'split'
      fetchFootprints()
      return
    }
    tile.state = 'done'
    tile.list = got.list
    holdSquare(tile)
    schedulePaint(tile)
  } catch (e) {
    if (generation !== footGeneration) return
    footTiles.delete(key)
    note.value = `footprints: ${(e as Error).message}`
  } finally {
    if (generation === footGeneration) { footActive--; pumpFootprints() }
  }
}

/* How far the sidecar has got indexing the buildings, every INDEX_POLL_MS
 * until it is ready (the squares are asked for again) or says the pack has
 * none. A sidecar that does not answer the status is asked for the squares
 * again every two seconds for a while, as before it had one. */
async function watchIndex(base: string, generation: number) {
  indexPoll = null
  let status: { state: string; phase: string; bytes_read: number; bytes_total: number; buildings: number } | null = null
  try {
    const r = await fetch(`${base}/buildings/status`)
    if (r.ok) status = await r.json()
  } catch { /* asked again below */ }
  if (generation !== footGeneration) return
  if (status?.state === 'loading') {
    indexing.value = { phase: status.phase, buildings: status.buildings,
                       fraction: status.bytes_total ? Math.min(1, status.bytes_read / status.bytes_total) : null }
    indexPoll = setTimeout(() => void watchIndex(base, generation), INDEX_POLL_MS)
    return
  }
  indexing.value = null
  indexWatch = false
  if (status?.state === 'absent' || (!status && footprintTries++ >= FOOTPRINT_TRIES)) {
    footNone = true
    note.value = 'this pack has no building footprints (built without --lod2-geometry)'
  } else if (status) {
    fetchFootprints()
  } else {
    indexPoll = setTimeout(() => { indexPoll = null; fetchFootprints() }, 2000)
  }
}

/** Squares land a few at a time: each burst is painted onto the overlay at once. */
function schedulePaint(tile: FootTile) {
  footLanded.push(tile)
  if (footPaintTimer) return
  footPaintTimer = setTimeout(() => { footPaintTimer = null; paintLanded() }, 80)
}

/** Keep the FOOT_TILES_KEPT squares nearest the view. */
function evictFootTiles(cx: number, cy: number) {
  if (footTiles.size <= FOOT_TILES_KEPT) return
  const done = [...footTiles.entries()].filter(([, t]) => t.state === 'done' || t.state === 'split')
  done.sort((a, b) => Math.hypot((b[1].ix + 0.5) * b[1].size - cx, (b[1].iy + 0.5) * b[1].size - cy)
                    - Math.hypot((a[1].ix + 0.5) * a[1].size - cx, (a[1].iy + 0.5) * a[1].size - cy))
  for (const [key, t] of done.slice(0, footTiles.size - FOOT_TILES_KEPT)) {
    if (t.state === 'done') dropSquare(t)
    footTiles.delete(key)
  }
}

/** Forget every square: different ground, or the layer turned off. */
function resetFootprints() {
  footGeneration++
  if (indexPoll) { clearTimeout(indexPoll); indexPoll = null }
  indexWatch = false
  indexing.value = null
  footTiles.clear()
  footHeld.clear()
  footIndex.clear()
  footLanded = []
  footQueue = []
  footActive = 0
  footNone = false
  footprintTries = 0
}

async function fetchRoads() {
  const base = ground.sidecar
  if (!base || !props.display.roads || size.w < 40) return
  const want = viewBox()
  // A held set is kept while it covers the view and was fetched at about
  // this scale: the sidecar's byte budget makes a wide view's set partial.
  if (ways && wayBox && covers(wayBox, want) && (wayBox.maxx - wayBox.minx) < viewWidthM() * 4) return
  wayReq?.abort()
  const ctrl = new AbortController()
  wayReq = ctrl
  const box = viewBox(0.6)
  try {
    const got = await roads(base, box, ctrl.signal)
    if (ctrl.signal.aborted) return
    ways = got
    wayBox = box
    paintOverlay()
  } catch { /* no roads is survivable */ } finally {
    if (wayReq === ctrl) wayReq = null
  }
}

/* A surface painted at the view it was painted for; between settles it is
 * drawn scaled and moved. Roads and footprints share one, coverage has its own. */
interface Surface { canvas: HTMLCanvasElement; view: { cx: number; cy: number; mpp: number }; w: number; h: number; on: boolean
  /** Its grey copy, for under a heatmap: made when first wanted after each paint. */
  grey: HTMLCanvasElement | null }
function surface(): Surface {
  return { canvas: document.createElement('canvas'), view: { cx: 0, cy: 0, mpp: 1 }, w: 0, h: 0, on: false, grey: null }
}
const overlay = surface()
const coverageSurface = surface()
let coverageVersion = ''

/* Road classes: motorway, trunk, primary, secondary, rail. */
const ROAD_WIDTH = [3, 2.6, 2.2, 1.6, 1.2]
const ROAD_COLOUR = ['#f5d98b', '#f0d38a', '#e9e3cf', '#d8d3c4', '#9ca3af']

function heightColour(m: number): string {
  // 0 m slate, 20 m sand, 60 m and up a warm red: the eye finds the tall ones.
  const t = Math.min(1, Math.max(0, m / 60))
  const r = Math.round(70 + t * 170), g = Math.round(80 + (t < 0.5 ? t * 2 * 90 : (1 - t) * 2 * 90)), b = Math.round(105 - t * 60)
  return `rgba(${r}, ${g}, ${b}, 0.75)`
}

/* What the overlay holds of the footprints: whether it has them, filled or
 * as outlines, the box they were kept to, and the buildings on it. A square
 * that lands after the overlay was painted is painted onto it as it is (at
 * the view it is of, only the buildings it does not hold), so a burst of
 * squares costs their own footprints, not every one in view again; once the
 * last square wanted is in, the overlay is painted whole once more, so what
 * it shows does not depend on the order the squares landed in. */
const overlayFoot = { on: false, fill: false, box: { minx: 0, miny: 0, maxx: 0, maxy: 0 } as Box, ids: new Set<number>() }

/** The overlay, whole, at the view as it is now. */
function paintOverlay() {
  overlay.on = false
  overlayFoot.on = false
  footLanded = []
  redrawWanted = true
  const showWays = ground.isPack && props.display.roads && ways
  const showFootprints = ground.isPack && props.display.buildings && footTiles.size > 0
    && viewWidthM() <= BLDG_MAX_VIEW_M
  if ((!showWays && !showFootprints) || !size.w) return
  const c = beginSurface(overlay, 1)
  const box = viewBox(0.05)
  if (showWays) paintWays(c, box)
  if (showFootprints) {
    Object.assign(overlayFoot, { on: true, fill: viewWidthM() <= BLDG_FILL_VIEW_M, box, ids: new Set() })
    paintFootprints(c, footprintsIn(box))
  }
  overlay.on = true
}

/** The squares landed since the overlay was painted, onto it; or, once the
 *  last square wanted is in, the overlay whole. */
function paintLanded() {
  const landed = footLanded
  footLanded = []
  if (!overlay.on || !overlayFoot.on) return
  if (!footQueue.length && !footActive) { paintOverlay(); return }
  const list = landed.filter(t => t.state === 'done' && footTiles.get(footKey(t.size, t.ix, t.iy)) === t)
    .flatMap(t => t.list)
  paintFootprints(overlay.canvas.getContext('2d')!, list)
  overlay.grey = null
  redrawWanted = true
}

function beginSurface(s: Surface, scale: number): CanvasRenderingContext2D {
  const dpr = size.dpr
  s.grey = null
  s.canvas.width = Math.round(size.w * dpr / scale)
  s.canvas.height = Math.round(size.h * dpr / scale)
  s.w = size.w
  s.h = size.h
  s.view = { ...view.value }
  const c = s.canvas.getContext('2d')!
  c.setTransform(dpr / scale, 0, 0, dpr / scale, 0, 0)
  c.clearRect(0, 0, size.w * scale, size.h * scale)
  return c
}

function paintWays(c: CanvasRenderingContext2D, box: Box) {
  // Wider as the view closes in, to about a street's width at a few metres a pixel.
  const zoom = Math.min(2.5, Math.max(0.6, Math.sqrt(8 / view.value.mpp)))
  c.lineCap = 'round'
  c.lineJoin = 'round'
  // Minor classes first, so the major ones lie on top where they cross.
  for (let cls = 4; cls >= 0; cls--) {
    c.strokeStyle = ROAD_COLOUR[cls] ?? '#d8d3c4'
    c.globalAlpha = cls === 4 ? 0.7 : 0.8
    c.lineWidth = (ROAD_WIDTH[cls] ?? 1.5) * zoom
    c.setLineDash(cls === 4 ? [6, 4] : [])
    c.beginPath()
    for (const way of ways!) {
      if (way.cls !== cls) continue
      const b = way.box
      if (b.maxx < box.minx || b.minx > box.maxx || b.maxy < box.miny || b.miny > box.maxy) continue
      const n = way.pts.length / 2
      for (let k = 0; k < n; k++) {
        const [sx, sy] = toScreen(way.pts[k * 2]!, way.pts[k * 2 + 1]!)
        if (k === 0) c.moveTo(sx, sy); else c.lineTo(sx, sy)
      }
    }
    c.stroke()
  }
  c.setLineDash([])
  c.globalAlpha = 1
}

/* Footprints as the planner's map draws them: all the outlines one path,
 * stroked once, and the fills one path a colour, under them. A ring is
 * closed by a line back to its first point, not by closePath(), which in
 * Chrome costs as many steps as the path has rings, so that closing n rings
 * costs n² (measured by the planner on a 3 km view of Berlin, 12 589 rings:
 * 2 566 ms with closePath(), 5.7 ms without). */
function paintFootprints(c: CanvasRenderingContext2D, all: Footprint[]) {
  const { fill, box, ids } = overlayFoot
  const list = all.filter(f => !ids.has(f.id) && f.box.maxx >= box.minx && f.box.minx <= box.maxx
    && f.box.maxy >= box.miny && f.box.miny <= box.maxy)
  for (const f of list) ids.add(f.id)
  // toScreen's arithmetic at the view the overlay is of, without an array a point.
  const { cx, cy, mpp } = overlay.view, hw = overlay.w / 2, hh = overlay.h / 2
  const path = (f: Footprint) => {
    for (const ring of f.rings) {
      const n = ring.length / 2
      if (n < 2) continue
      const x0 = hw + (ring[0]! - cx) / mpp, y0 = hh - (ring[1]! - cy) / mpp
      c.moveTo(x0, y0)
      for (let k = 1; k < n; k++) c.lineTo(hw + (ring[k * 2]! - cx) / mpp, hh - (ring[k * 2 + 1]! - cy) / mpp)
      c.lineTo(x0, y0)
    }
  }
  if (fill) {
    const byColour = new Map<string, Footprint[]>()
    for (const f of list) {
      const g = groundAt((f.box.minx + f.box.maxx) / 2, (f.box.miny + f.box.maxy) / 2)
      const colour = g === null ? 'rgba(120, 128, 140, 0.55)' : heightColour(f.top - g)
      const same = byColour.get(colour)
      if (same) same.push(f); else byColour.set(colour, [f])
    }
    for (const [colour, fs] of byColour) {
      c.beginPath()
      for (const f of fs) path(f)
      c.fillStyle = colour
      c.fill('evenodd')
    }
  }
  c.beginPath()
  for (const f of list) path(f)
  c.lineWidth = 0.8
  c.strokeStyle = 'rgba(210, 214, 222, 0.55)'
  c.stroke()
}

/* Coverage: the band of every COVERAGE_PX square of the view, as the
 * source gives it (COVERAGE_BANDS: green indoors too, yellow outdoors only,
 * red the edge), nothing where it does not decode. A paint is a job: the
 * bands asked for the view as it is, and drawn into a canvas of its own
 * that replaces the one on show only when it is whole. A newer paint (an
 * aim, a move of the view) aborts the older one; meanwhile the last
 * coverage stays drawn and `coverageBusy` puts up "redrawing coverage". */
let coverageReq: AbortController | null = null
const coverageBusy = ref(false)

async function paintCoverage() {
  coverageReq?.abort()
  coverageReq = null
  const src = props.coverage
  coverageVersion = src?.version ?? ''
  if (!src || !props.display.coverage || !size.w) {
    coverageSurface.on = false
    coverageBusy.value = false
    redrawWanted = true
    return
  }
  const ctrl = new AbortController()
  coverageReq = ctrl
  coverageBusy.value = true
  // The view as it is now: the image is of it, wherever the view goes meanwhile.
  const at = { ...view.value }, w = size.w, h = size.h
  let bands: Uint8Array
  try {
    bands = await src.bands(at, w, h, COVERAGE_PX, ctrl.signal)
  } catch (e) {
    if (coverageReq !== ctrl) return
    coverageReq = null
    coverageBusy.value = false
    if ((e as Error).name !== 'AbortError') note.value = `coverage: ${(e as Error).message}`
    return
  }
  if (coverageReq !== ctrl) return
  coverageReq = null
  const cols = Math.ceil(w / COVERAGE_PX), rows = Math.ceil(h / COVERAGE_PX)
  const canvas = document.createElement('canvas')
  canvas.width = cols
  canvas.height = rows
  const c = canvas.getContext('2d')!
  const img = c.createImageData(cols, rows)
  for (let i = 0; i < cols * rows; i++) {
    const band = COVERAGE_BANDS[bands[i]!]
    if (band) img.data.set(band.rgba, i * 4)
  }
  c.putImageData(img, 0, 0)
  Object.assign(coverageSurface, { canvas, w, h, view: at, on: true, grey: null })
  coverageBusy.value = false
  redrawWanted = true
}

/* ── drawing ── */
const STATUS_COLOUR: Record<string, string> = {
  stopped: '#6b7280', starting: '#f59e0b', setup: '#f59e0b', restarting: '#ef4444', up: '#e5e7eb',
}

function draw() {
  if (!ctx) return
  const { w, h } = size
  ctx.fillStyle = '#121417'
  ctx.fillRect(0, 0, w, h)
  // Under a heatmap everything else is grey, so its colours are the only ones
  // that mean anything; the nodes keep theirs, drawn on top.
  mono = heatmap()
  if (ground.isPack) drawGround()
  else drawGrid()
  if (overlay.on) {
    if (mono && !overlay.grey) overlay.grey = greyed(overlay.canvas)
    drawSurface(overlay, mono)
  }
  if (props.display.population && population && population.key === (ground.current?.name ?? '')) {
    ctx.imageSmoothingEnabled = true
    drawHeatmap(population.image)
  }
  if (coverageSurface.on && props.display.coverage) {
    ctx.imageSmoothingEnabled = true
    drawSurface(coverageSurface)
  }
  if (props.display.offsets) drawOffsets()
  drawPair()
  drawLinks()
  if (props.live) drawPulses()
  drawPin()
  drawOthers()
  drawNodes()
  drawBand()
}

/** Whether this frame is drawn grey under a heatmap. */
let mono = false

function drawImageAt(image: HTMLCanvasElement | ImageBitmap, b: Box) {
  const [x0, y0] = toScreen(b.minx, b.maxy)
  const [x1, y1] = toScreen(b.maxx, b.miny)
  ctx!.drawImage(image, x0, y0, x1 - x0, y1 - y0)
}

function drawHeld(held: Held) {
  if (mono && !held.grey) held.grey = greyed(held.image.bitmap)
  drawImageAt(mono ? held.grey! : held.image.bitmap, held.image.bounds)
}

function drawHeatmap(img: Heatmap) { drawImageAt(img.bitmap, img.bounds) }

function drawGround() {
  if (!ctx) return
  ctx.imageSmoothingEnabled = true
  const key = groundKey()
  // The pack's own edge: beyond it there is no ground and no loss, and the
  // planner's images there are its no-data colour, so they are cut off at it.
  const e = ground.pack?.extent
  ctx.save()
  if (e) {
    const [x0, y0] = toScreen(e.minx, e.maxy)
    const [x1, y1] = toScreen(e.maxx, e.miny)
    ctx.beginPath(); ctx.rect(x0, y0, x1 - x0, y1 - y0); ctx.clip()
  }
  if (overview && overview.key === key) drawHeld(overview)
  if (detail && detail.key === key) drawHeld(detail)
  ctx.restore()
  if (e) {
    const [x0, y0] = toScreen(e.minx, e.maxy)
    const [x1, y1] = toScreen(e.maxx, e.miny)
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.5)'
    ctx.setLineDash([6, 4])
    ctx.lineWidth = 1
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
    ctx.setLineDash([])
  }
  // Base colours are the planner's light palette; dim them under the marks.
  ctx.fillStyle = 'rgba(18, 20, 23, 0.28)'
  ctx.fillRect(0, 0, size.w, size.h)
}

function drawSurface(s: Surface, grey = false) {
  if (!ctx) return
  const v = view.value, at = s.view
  const k = at.mpp / v.mpp
  // The surface's centre, where it now lands, and its size at this zoom.
  const [cx, cy] = toScreen(at.cx, at.cy)
  ctx.drawImage(grey && s.grey ? s.grey : s.canvas, cx - s.w / 2 * k, cy - s.h / 2 * k, s.w * k, s.h * k)
}

/** A colour as is, or its grey under a heatmap. */
function tone(r: number, g: number, b: number, a = 1): string {
  if (mono) { const l = Math.round(0.299 * r + 0.587 * g + 0.114 * b); r = g = b = l }
  return `rgba(${r}, ${g}, ${b}, ${a})`
}

/* The grid: 1, 2 or 5 times a power of ten, whichever lands 60 to 150 px apart. */
function gridStep(): number {
  const target = 105 * view.value.mpp
  const decade = Math.pow(10, Math.floor(Math.log10(target)))
  for (const m of [1, 2, 5, 10]) if (m * decade / view.value.mpp >= 60) return m * decade
  return 10 * decade
}

function gridLabel(v: number) {
  if (props.display.units === 'degrees') {
    const deg = v / M_PER_DEGREE
    const places = Math.max(0, Math.min(6, Math.ceil(-Math.log10(Math.max(gridStep() / M_PER_DEGREE, 1e-9)))))
    return `${deg.toFixed(places)}°`
  }
  const a = Math.abs(v)
  return a >= 1000 ? `${(v / 1000).toFixed(a >= 10000 ? 0 : 1)}k` : `${Math.round(v)}`
}

function drawGrid() {
  if (!ctx) return
  const { w, h } = size
  const step = gridStep()
  const b = viewBox()
  ctx.lineWidth = 1
  ctx.font = '11px ui-monospace, monospace'
  ctx.textBaseline = 'top'
  ctx.textAlign = 'left'
  for (let x = Math.ceil(b.minx / step) * step; x <= b.maxx; x += step) {
    const sx = Math.round(toScreen(x, 0)[0]) + 0.5
    // The axes through 0°, 0°.
    ctx.strokeStyle = Math.abs(x) < step / 2 ? '#3b4351' : '#1e232b'
    ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, h); ctx.stroke()
    ctx.fillStyle = '#4b5563'
    ctx.fillText(gridLabel(x), sx + 3, 3)
  }
  for (let y = Math.ceil(b.miny / step) * step; y <= b.maxy; y += step) {
    const sy = Math.round(toScreen(0, y)[1]) + 0.5
    ctx.strokeStyle = Math.abs(y) < step / 2 ? '#3b4351' : '#1e232b'
    ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(w, sy); ctx.stroke()
    ctx.fillStyle = '#4b5563'
    ctx.fillText(gridLabel(y), 3, sy + 3)
  }
  // Synthetic ground's own square.
  const extent = ground.current?.extent_m
  if (extent) {
    const [x0, y0] = toScreen(-extent / 2, extent / 2)
    const [x1, y1] = toScreen(extent / 2, -extent / 2)
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.4)'
    ctx.setLineDash([6, 4])
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
    ctx.setLineDash([])
  }
}

function drawOffsets() {
  if (!ctx) return
  for (const o of props.offsets) {
    const a = posOf(o.between[0]), b = posOf(o.between[1])
    if (!a || !b) continue
    const [ax, ay] = toScreen(a[0], a[1]), [bx, by] = toScreen(b[0], b[1])
    ctx.strokeStyle = o.db > 0 ? tone(185, 28, 28) : tone(13, 148, 136)
    ctx.lineWidth = 1.5
    ctx.setLineDash([5, 4])
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
    ctx.setLineDash([])
    ctx.font = '11px ui-monospace, monospace'
    ctx.textAlign = 'left'
    text(`${o.db > 0 ? '+' : ''}${o.db} dB`, (ax + bx) / 2 + 4, (ay + by) / 2 - 12,
         o.db > 0 ? tone(248, 113, 113) : tone(94, 234, 212))
  }
}

function drawPair() {
  if (!ctx || !props.pair) return
  const a = posOf(props.pair[0]), b = posOf(props.pair[1])
  if (!a || !b) return
  const [ax, ay] = toScreen(a[0], a[1]), [bx, by] = toScreen(b[0], b[1])
  ctx.strokeStyle = tone(167, 139, 250)
  ctx.lineWidth = 2.5
  ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
}

/** Decodable green through amber by margin; interfering only, red and thin. */
/* Kept in colour under a heatmap, as the nodes are: what a link's colour
 * says is its own, and it is the selection's, not the ground's. */
function linkColour(m: LinkMark, alpha: number): string {
  if (!m.decodable) return `rgba(239, 68, 68, ${alpha * 0.7})`
  const t = Math.min(1, Math.max(0, (m.level + 130) / 40))
  return `rgba(${Math.round(250 - t * 216)}, ${Math.round(204 - t * 7)}, ${Math.round(21 + t * 73)}, ${alpha})`
}

function drawLinks() {
  if (!ctx || !props.links || props.selected.length !== 1) return
  const from = posOf(props.selected[0]!)
  if (!from) return
  const [fx, fy] = toScreen(from[0], from[1])
  ctx.font = '11px ui-monospace, monospace'
  ctx.textAlign = 'left'
  for (const m of props.links) {
    if (!Number.isFinite(m.level)) continue
    const to = posOf(m.name)
    if (!to) continue
    const [tx, ty] = toScreen(to[0], to[1])
    ctx.strokeStyle = linkColour(m, 0.85)
    ctx.lineWidth = m.decodable ? 3.6 : 2
    ctx.setLineDash(m.los === false ? [4, 4] : [])
    ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(tx, ty); ctx.stroke()
    ctx.setLineDash([])
    text(`${m.level.toFixed(0)}`, tx + 9, ty - 13, linkColour(m, 1))
  }
}

function drawPulses() {
  if (!ctx) return
  const now = performance.now()
  for (const pulse of sim.pulses) {
    const p = posOf(pulse.name)
    if (!p) continue
    const age = (now - pulse.start) / pulse.duration
    if (age < 0 || age > 1) continue
    const [sx, sy] = toScreen(p[0], p[1])
    // The ring is the frame occupying the air, not its range: it grows for as
    // long as the transmission lasts and fades as it ends.
    ctx.strokeStyle = tone(250, 204, 21, 0.6 * (1 - age))
    ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(sx, sy, NODE_R + age * 70, 0, Math.PI * 2); ctx.stroke()
  }
}

function drawPin() {
  if (!ctx || !pin) return
  const [sx, sy] = toScreen(pin[0], pin[1])
  ctx.strokeStyle = '#f472b6'
  ctx.lineWidth = 2
  ctx.beginPath(); ctx.moveTo(sx - 7, sy - 7); ctx.lineTo(sx + 7, sy + 7)
  ctx.moveTo(sx + 7, sy - 7); ctx.lineTo(sx - 7, sy + 7); ctx.stroke()
}

function drawNodes() {
  if (!ctx) return
  const now = performance.now()
  const flashOf = new Map<string, { verdict: string; age: number }>()
  if (props.live) for (const f of sim.flashes) {
    const age = (now - f.start) / FLASH_MS
    if (age >= 0 && age <= 1) flashOf.set(f.name, { verdict: f.verdict, age })
  }
  const detailed = view.value.mpp < 8
  const d = props.display
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  for (const n of props.nodes) {
    const p = posOf(n.name)
    if (!p) continue
    const [sx, sy] = toScreen(p[0], p[1])
    if (sx < -60 || sy < -60 || sx > size.w + 60 || sy > size.h + 60) continue
    const flash = flashOf.get(n.name)
    if (flash) {
      ctx.fillStyle = flash.verdict === 'clean'
        ? `rgba(34, 197, 94, ${0.6 * (1 - flash.age)})` : `rgba(239, 68, 68, ${0.6 * (1 - flash.age)})`
      ctx.beginPath(); ctx.arc(sx, sy, NODE_R + 9, 0, Math.PI * 2); ctx.fill()
    }
    if (forwardingRole(n)) {
      // A node that carries others' traffic: its second ring is the whole of
      // that difference, read at a glance across a big map.
      // Black, so it stands out on the light ground and on a heatmap alike.
      ctx.strokeStyle = '#000000'
      ctx.lineWidth = 1
      ctx.beginPath(); ctx.arc(sx, sy, NODE_R + 4, 0, Math.PI * 2); ctx.stroke()
    }
    ctx.fillStyle = props.live ? STATUS_COLOUR[n.status ?? 'stopped'] ?? '#6b7280' : '#e5e7eb'
    ctx.globalAlpha = n.stale ? 0.45 : 1
    ctx.beginPath(); ctx.arc(sx, sy, NODE_R, 0, Math.PI * 2); ctx.fill()
    ctx.globalAlpha = 1
    ctx.strokeStyle = '#0b0d10'
    ctx.lineWidth = 1
    ctx.stroke()
    if (n.stale) {
      // Moved, and its row of the loss table still the old one.
      ctx.strokeStyle = '#f59e0b'
      ctx.setLineDash([3, 3])
      ctx.lineWidth = 1.5
      ctx.beginPath(); ctx.arc(sx, sy, NODE_R + 7, 0, Math.PI * 2); ctx.stroke()
      ctx.setLineDash([])
    }
    const picked = chosen.value.has(n.name)
    if (picked || hovered.value === n.name || drag?.node === n.name) {
      ctx.strokeStyle = picked ? '#a78bfa' : '#ffffff'
      ctx.lineWidth = 2
      ctx.beginPath(); ctx.arc(sx, sy, NODE_R + 2, 0, Math.PI * 2); ctx.stroke()
    }
    if (picked) {
      ctx.strokeStyle = '#a78bfa'
      ctx.lineWidth = 1.5
      ctx.strokeRect(sx - NODE_R - 7, sy - NODE_R - 7, (NODE_R + 7) * 2, (NODE_R + 7) * 2)
    }
    // Name, then the height and where it came from, then the tags, smaller.
    let below = sy + NODE_R + 5
    if (d.labels) {
      ctx.font = '12px ui-sans-serif, system-ui, sans-serif'
      text(n.name, sx, below, !props.live || n.status === 'up' ? '#f3f4f6' : '#9ca3af')
      below += 14
    }
    if (d.heights && (detailed || picked)) {
      ctx.font = '10px ui-monospace, monospace'
      // The antenna above the ground under it, as the display menu's
      // "antenna heights" says.
      text(`${Math.round(n.height_m * 10) / 10} m up`, sx, below, '#93c5fd')
      below += 12
    }
    if (d.tags && n.tags.length && (detailed || picked || view.value.mpp < 25)) {
      ctx.font = '9px ui-sans-serif, system-ui, sans-serif'
      text(n.tags.slice(0, 4).join(' ') + (n.tags.length > 4 ? ' …' : ''), sx, below, '#c4b5fd')
    }
  }
  ctx.textAlign = 'left'
}

/* The nodes only shown: hollow, in their nodeset's colour, named only while
 * the pointer is on one. */
function drawOthers() {
  if (!ctx || !props.others.length) return
  ctx.lineWidth = 2
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  for (const o of props.others) {
    const [x, y] = otherXY.value.get(otherKey(o))!
    const [sx, sy] = toScreen(x, y)
    if (sx < -20 || sy < -20 || sx > size.w + 20 || sy > size.h + 20) continue
    ctx.strokeStyle = o.colour
    ctx.beginPath(); ctx.arc(sx, sy, NODE_R - 1, 0, Math.PI * 2); ctx.stroke()
    if (hoveredOther.value === otherKey(o)) {
      ctx.font = '12px ui-sans-serif, system-ui, sans-serif'
      text(`${o.name} · ${o.layer}`, sx, sy + NODE_R + 4, o.colour)
    }
  }
  ctx.textAlign = 'left'
}

function drawBand() {
  if (!ctx || !drag?.band || !drag.moving) return
  const { from, to } = drag.band
  ctx.strokeStyle = '#a78bfa'
  ctx.fillStyle = 'rgba(167, 139, 250, 0.12)'
  ctx.lineWidth = 1
  ctx.setLineDash([4, 3])
  const x = Math.min(from.x, to.x), y = Math.min(from.y, to.y)
  ctx.fillRect(x, y, Math.abs(to.x - from.x), Math.abs(to.y - from.y))
  ctx.strokeRect(x, y, Math.abs(to.x - from.x), Math.abs(to.y - from.y))
  ctx.setLineDash([])
}

/** A label with a dark halo, legible over any ground. */
function text(s: string, x: number, y: number, colour: string) {
  if (!ctx) return
  ctx.lineJoin = 'round'
  ctx.lineWidth = 3
  ctx.strokeStyle = 'rgba(10, 12, 15, 0.85)'
  ctx.strokeText(s, x, y)
  ctx.fillStyle = colour
  ctx.fillText(s, x, y)
}

/* ── the frame loop: draw when something moved, and every frame while anything animates ── */
function tick() {
  const now = performance.now()
  if (props.live) sim.expire(now)
  if (redrawWanted || (props.live && (sim.pulses.length || sim.flashes.length))) {
    redrawWanted = false
    draw()
  }
  frame = requestAnimationFrame(tick)
}

/* ── the pointer ── */
interface Drag {
  /** The node pressed on, when the press moves nodes. */
  node?: string
  /** Every node that moves with it: the selection, when it is in it. */
  group: Set<string>
  shift?: [number, number]
  band?: { from: { x: number; y: number }; to: { x: number; y: number }; how: Pick }
  lastSend: number
  from: { x: number; y: number }
  start: { cx: number; cy: number }
  moving: boolean
}
let drag: Drag | null = null

/** The selected node's link line under a screen point, as its two ends, or null. */
function linkAt(sx: number, sy: number): [string, string] | null {
  let best: [string, string] | null = null
  let bestD = LINK_HIT_PX
  const consider = (a: string, b: string) => {
    const pa = posOf(a), pb = posOf(b)
    if (!pa || !pb) return
    const [fx, fy] = toScreen(pa[0], pa[1])
    const [tx, ty] = toScreen(pb[0], pb[1])
    // Distance from the point to the segment, the ends included.
    const vx = tx - fx, vy = ty - fy
    const len2 = vx * vx + vy * vy
    const t = len2 ? Math.max(0, Math.min(1, ((sx - fx) * vx + (sy - fy) * vy) / len2)) : 0
    const d = Math.hypot(sx - (fx + t * vx), sy - (fy + t * vy))
    if (d <= bestD) { best = [a, b]; bestD = d }
  }
  if (props.links && props.selected.length === 1) {
    const from = props.selected[0]!
    for (const m of props.links) if (Number.isFinite(m.level)) consider(from, m.name)
  }
  return best
}

function otherAt(sx: number, sy: number): OtherNode | null {
  let best: OtherNode | null = null
  let bestD = HIT_R
  for (const o of props.others) {
    const [x, y] = otherXY.value.get(otherKey(o))!
    const [nx, ny] = toScreen(x, y)
    const dist = Math.hypot(nx - sx, ny - sy)
    if (dist <= bestD) { best = o; bestD = dist }
  }
  return best
}

function nodeAt(sx: number, sy: number): string | null {
  let best: string | null = null
  let bestD = HIT_R
  for (const n of props.nodes) {
    const p = posOf(n.name)
    if (!p) continue
    const [nx, ny] = toScreen(p[0], p[1])
    const dist = Math.hypot(nx - sx, ny - sy)
    if (dist <= bestD) { best = n.name; bestD = dist }
  }
  return best
}

function pointer(event: MouseEvent) {
  const box = canvas.value!.getBoundingClientRect()
  return { x: event.clientX - box.left, y: event.clientY - box.top }
}

function groundPoint(sx: number, sy: number): GroundPoint {
  const [x, y] = fromScreen(sx, sy)
  const [lat, lon] = ground.frame.toLatLon(x, y)
  return { x, y, lat, lon, footprint: footprintAt(x, y), ground: groundAt(x, y) }
}

function onDown(event: PointerEvent) {
  if (event.button !== 0) return
  const at = pointer(event)
  canvas.value?.setPointerCapture(event.pointerId)
  const base = { lastSend: 0, from: at, start: { cx: view.value.cx, cy: view.value.cy }, moving: false,
                 group: new Set<string>() }
  if (event.ctrlKey || event.metaKey) {
    const how: Pick = event.altKey ? 'remove' : event.shiftKey ? 'add' : 'replace'
    drag = { ...base, band: { from: at, to: at, how } }
    banding.value = true
    return
  }
  const name = nodeAt(at.x, at.y)
  // Only an editable map moves nodes; elsewhere a press on one is a pan.
  if (props.editable && name && !event.shiftKey) {
    const group = chosen.value.has(name) ? new Set(props.selected) : new Set([name])
    drag = { ...base, node: name, group }
  } else {
    drag = base
  }
}

function onMove(event: PointerEvent) {
  const at = pointer(event)
  if (!drag) {
    const name = nodeAt(at.x, at.y)
    overLink.value = !name && linkAt(at.x, at.y) !== null
    if (name !== hovered.value) {
      hovered.value = name
      emit('hover', name)
      redrawWanted = true
    }
    const other = name ? null : otherAt(at.x, at.y)
    const key = other ? otherKey(other) : null
    if (key !== hoveredOther.value) { hoveredOther.value = key; redrawWanted = true }
    return
  }
  const dx = at.x - drag.from.x, dy = at.y - drag.from.y
  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moving = true
  if (!drag.moving) return
  if (drag.band) {
    drag.band.to = at
  } else if (drag.node) {
    drag.shift = [dx * view.value.mpp, -dy * view.value.mpp]
    const now = performance.now()
    if (now - drag.lastSend > 1000 / DRAG_SEND_HZ) {
      drag.lastSend = now
      sendMoves(false)
    }
  } else {
    view.value.cx = drag.start.cx - dx * view.value.mpp
    view.value.cy = drag.start.cy + dy * view.value.mpp
    settled()
  }
  redrawWanted = true
}

function sendMoves(settle: boolean) {
  if (!drag?.shift) return
  for (const name of drag.group) {
    const p = positions.value.get(name)
    if (!p) continue
    const [lat, lon] = ground.frame.toLatLon(p[0] + drag.shift[0], p[1] + drag.shift[1])
    emit('move', name, lat, lon, settle)
  }
}

function onUp(event: PointerEvent) {
  if (!drag) return
  const at = pointer(event)
  const held = drag
  if (canvas.value?.hasPointerCapture(event.pointerId)) canvas.value.releasePointerCapture(event.pointerId)
  banding.value = false
  if (held.band) {
    if (held.moving) {
      const x0 = Math.min(held.band.from.x, at.x), x1 = Math.max(held.band.from.x, at.x)
      const y0 = Math.min(held.band.from.y, at.y), y1 = Math.max(held.band.from.y, at.y)
      const names = props.nodes.filter((n) => {
        const p = posOf(n.name)
        if (!p) return false
        const [sx, sy] = toScreen(p[0], p[1])
        return sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1
      }).map(n => n.name)
      emit('pick', names, held.band.how)
    } else {
      const name = nodeAt(at.x, at.y)
      if (name) emit('select', name, 'toggle')
    }
    drag = null
  } else if (held.moving && held.node) {
    sendMoves(true)
    drag = null
  } else {
    drag = null
    if (held.moving) {
      saveView()
    } else {
      const name = nodeAt(at.x, at.y)
      const other = name ? null : linkAt(at.x, at.y)
      const layered = name || other ? null : otherAt(at.x, at.y)
      if (name) emit('select', name, event.shiftKey ? 'toggle' : 'replace')
      else if (other) emit('link', other[0], other[1])
      else if (layered) emit('other', layered.layer, layered.name)
      else {
        if (!event.shiftKey) emit('select', null, 'replace')
        emit('ground', groundPoint(at.x, at.y))
      }
    }
  }
  redrawWanted = true
}

function onLeave() {
  if (!drag && hovered.value) { hovered.value = null; emit('hover', null); redrawWanted = true }
}

function onWheel(event: WheelEvent) {
  const at = pointer(event)
  const [bx, by] = fromScreen(at.x, at.y)
  const factor = Math.exp(event.deltaY * 0.0015)
  view.value.mpp = Math.min(5000, Math.max(0.05, view.value.mpp * factor))
  // Zoom about the cursor: the point under it stays under it.
  view.value.cx = bx - (at.x - size.w / 2) * view.value.mpp
  view.value.cy = by + (at.y - size.h / 2) * view.value.mpp
  saveView()
  settled(true)
}

function onContext(event: MouseEvent) {
  const at = pointer(event)
  emit('context', groundPoint(at.x, at.y), nodeAt(at.x, at.y))
}

/* ── the scale bar: a round length about 100 px long ── */
const scale = computed(() => {
  const target = 100 * view.value.mpp
  const decade = Math.pow(10, Math.floor(Math.log10(target)))
  const m = [5, 2, 1].map(k => k * decade).find(v => v <= target) ?? decade
  return { px: m / view.value.mpp, label: m >= 1000 ? `${m / 1000} km` : `${m} m` }
})

/* ── sizing ── */
function resize() {
  const element = wrap.value, surface = canvas.value
  if (!element || !surface) return
  const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP)
  const shown = size.w === 0 && element.clientWidth > 0
  size = { w: element.clientWidth, h: element.clientHeight, dpr }
  // Coming on show (its tab chosen): where the other map sharing the view
  // left it, and the overview it did not ask for while hidden.
  if (shown) { adoptShared(); if (!overview) void fetchOverview() }
  surface.width = Math.round(size.w * dpr)
  surface.height = Math.round(size.h * dpr)
  surface.style.width = `${size.w}px`
  surface.style.height = `${size.h}px`
  ctx = surface.getContext('2d')
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0)
  settled()
}

let observer: ResizeObserver | null = null
onMounted(async () => {
  await nextTick()
  resize()
  observer = new ResizeObserver(resize)
  if (wrap.value) observer.observe(wrap.value)
  restoreView()
  frame = requestAnimationFrame(tick)
})
onUnmounted(() => {
  cancelAnimationFrame(frame)
  observer?.disconnect()
  groundReq?.abort(); overviewReq?.abort(); wayReq?.abort(); populationReq?.abort()
  resetFootprints()
  if (settleTimer) clearTimeout(settleTimer)
  if (fetchTimer) clearTimeout(fetchTimer)
  coverageReq?.abort()
  saveView()
})

/* Different geodata is different ground: forget the old one's and come back
 * to where this one was left. */
watch(() => [ground.current?.name, ground.pack?.name], () => {
  detail = null; overview = null; overlay.on = false; pin = null
  resetFootprints()
  ways = null; wayBox = null; coverageSurface.on = false; population = null
  coverageReq?.abort(); coverageReq = null; coverageBusy.value = false
  note.value = ground.problem
  restoreView()
  void fetchOverview()
})
watch(() => props.display.base, () => { void fetchOverview(); fetchGround() })
watch(() => props.display.population, (on) => {
  if (on) void fetchPopulation()
  else if (note.value?.startsWith('this pack has no population')) note.value = null
  redrawWanted = true
})
watch(() => props.display.roads, () => { fetchRoads(); paintOverlay() })
watch(() => props.display.buildings, () => { resetFootprints(); fetchFootprints(); paintOverlay() })
watch(() => [props.display.coverage, props.coverage?.version], () => {
  if ((props.coverage?.version ?? '') !== coverageVersion || !coverageSurface.on) void paintCoverage()
})
watch(() => ground.problem, (p) => { if (p) note.value = p })
watch(() => [props.nodes, props.others, props.offsets, props.selected, props.links, props.pair,
             props.display, sim.levels],
      () => { redrawWanted = true }, { deep: true })
</script>

<style scoped>
.wmap { position: relative; width: 100%; height: 100%; overflow: hidden; background: #121417; }
.wmap canvas { display: block; cursor: grab; touch-action: none; }
.wmap canvas:active { cursor: grabbing; }
.wmap canvas.wmap-placing { cursor: crosshair; }
.wmap canvas.wmap-banding { cursor: crosshair; }
.wmap canvas.wmap-link { cursor: pointer; }
.wmap-scale {
  position: absolute; right: 10px; bottom: 8px; display: flex; align-items: center; gap: 6px;
  font: 11px ui-monospace, monospace; color: #9ca3af; pointer-events: none;
}
.wmap-scale-bar { display: inline-block; height: 5px; border: 1px solid #9ca3af; border-top: none; }
.wmap-bottom {
  position: absolute; left: 10px; bottom: 8px; display: flex; flex-direction: column;
  align-items: flex-start; gap: 4px; pointer-events: none;
}
.wmap-note, .wmap-legend {
  font-size: 11px; color: #9ca3af;
  background: rgba(18, 20, 23, 0.8); padding: 2px 6px; border-radius: 3px;
}
.wmap-legend { display: flex; align-items: center; gap: 10px; color: #d1d5db; }
.wmap-index { min-width: 260px; }
.wmap-legend span { display: flex; align-items: center; gap: 4px; }
.wmap-legend b { font-weight: 500; color: #e5e7eb; }
.wmap-legend i { display: inline-block; width: 12px; height: 10px; border-radius: 2px; }
.wmap-legend i.wmap-ramp {
  width: 70px; background: linear-gradient(90deg, rgba(120, 40, 170, 0.4), rgb(188, 125, 105), rgb(255, 210, 40));
}
.wmap-credits {
  position: absolute; right: 10px; bottom: 26px; max-width: min(560px, 60%);
  font-size: 10px; line-height: 1.4; color: var(--q-primary); text-align: right; cursor: pointer;
  background: rgba(18, 20, 23, 0.8); padding: 2px 6px; border-radius: 3px;
}
.wmap-credits div { text-align: left; margin: 2px 0; }
.wmap-credits b { font-weight: 500; color: #d1d5db; }
.wmap-busy {
  position: absolute; right: 10px; top: 8px; font-size: 11px; color: #9ca3af;
  background: rgba(18, 20, 23, 0.8); padding: 2px 6px; border-radius: 3px; pointer-events: none;
}
</style>
