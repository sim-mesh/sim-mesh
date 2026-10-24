/* The geodata's coordinate frame, for the map: metres east and north, and
 * the way to and from latitude and longitude.
 *
 * A pack is in its pack's CRS, which is a UTM zone (EPSG 326zz north or
 * 327zz south on WGS84, 258zz on ETRS89, whose ellipsoid differs from
 * WGS84's by a tenth of a millimetre in the semi-minor axis and is treated as
 * the same here). Tiles, footprints, roads and link.json all speak those
 * metres, and the nodeset speaks degrees, so the map converts every node
 * position once through Krüger's series to the sixth order in n, which is
 * good to well under a millimetre inside a zone.
 *
 * Synthetic ground has no CRS: it lies at 0°, 0° and its degrees are metres
 * by one fixed rule on both axes, the equator's degree in metres, as
 * geodata.py has it, so a pixel on the map and a metre in the medium agree
 * by construction. */

export interface Frame {
  /** EPSG code, or null for synthetic ground's metres. */
  epsg: number | null
  toXY(lat: number, lon: number): [number, number]
  toLatLon(x: number, y: number): [number, number]
}

const RAD = Math.PI / 180
/** Metres to a degree on synthetic ground: the equator's (geodata.py's M_PER_DEGREE). */
export const M_PER_DEGREE = 111320

/** Synthetic ground's frame: its metres from 0°, 0°. */
export const syntheticFrame: Frame = {
  epsg: null,
  toXY: (lat, lon) => [lon * M_PER_DEGREE, lat * M_PER_DEGREE],
  toLatLon: (x, y) => [y / M_PER_DEGREE, x / M_PER_DEGREE],
}

/** The zone and hemisphere an EPSG code names, or null for one that is not UTM. */
export function utmZone(epsg: number): { zone: number; south: boolean } | null {
  if (epsg > 32600 && epsg <= 32660) return { zone: epsg - 32600, south: false }
  if (epsg > 32700 && epsg <= 32760) return { zone: epsg - 32700, south: true }
  if (epsg >= 25828 && epsg <= 25838) return { zone: epsg - 25800, south: false }
  return null
}

/** A pack's frame, or null when its CRS is not one this page projects. */
export function packFrame(epsg: number): Frame | null {
  const utm = utmZone(epsg)
  if (!utm) return null
  const lon0 = (utm.zone * 6 - 183) * RAD
  const falseNorthing = utm.south ? 10_000_000 : 0
  return {
    epsg,
    toXY: (lat, lon) => {
      const [x, y] = tmForward(lat * RAD, lon * RAD - lon0)
      return [x + 500_000, y + falseNorthing]
    },
    toLatLon: (x, y) => {
      const [lat, dlon] = tmInverse(x - 500_000, y - falseNorthing)
      return [lat / RAD, (dlon + lon0) / RAD]
    },
  }
}

/* ── Transverse Mercator, Krüger's series (Karney 2011, eqs. 35 and 36) ── */
const A = 6378137
const F = 1 / 298.257223563
const K0 = 0.9996
const N = F / (2 - F)
const E = Math.sqrt(F * (2 - F))
const N2 = N * N, N3 = N2 * N, N4 = N3 * N, N5 = N4 * N, N6 = N5 * N
const RECTIFYING = A / (1 + N) * (1 + N2 / 4 + N4 / 64 + N6 / 256)

const ALPHA = [
  N / 2 - 2 * N2 / 3 + 5 * N3 / 16 + 41 * N4 / 180 - 127 * N5 / 288 + 7891 * N6 / 37800,
  13 * N2 / 48 - 3 * N3 / 5 + 557 * N4 / 1440 + 281 * N5 / 630 - 1983433 * N6 / 1935360,
  61 * N3 / 240 - 103 * N4 / 140 + 15061 * N5 / 26880 + 167603 * N6 / 181440,
  49561 * N4 / 161280 - 179 * N5 / 168 + 6601661 * N6 / 7257600,
  34729 * N5 / 80640 - 3418889 * N6 / 1995840,
  212378941 * N6 / 319334400,
]
const BETA = [
  N / 2 - 2 * N2 / 3 + 37 * N3 / 96 - N4 / 360 - 81 * N5 / 512 + 96199 * N6 / 604800,
  N2 / 48 + N3 / 15 - 437 * N4 / 1440 + 46 * N5 / 105 - 1118711 * N6 / 3870720,
  17 * N3 / 480 - 37 * N4 / 840 - 209 * N5 / 4480 + 5569 * N6 / 90720,
  4397 * N4 / 161280 - 11 * N5 / 504 - 830251 * N6 / 7257600,
  4583 * N5 / 161280 - 108847 * N6 / 3991680,
  20648693 * N6 / 638668800,
]

function tmForward(phi: number, lambda: number): [number, number] {
  const tau = Math.tan(phi)
  const sigma = Math.sinh(E * Math.atanh(E * tau / Math.sqrt(1 + tau * tau)))
  const tauP = tau * Math.sqrt(1 + sigma * sigma) - sigma * Math.sqrt(1 + tau * tau)
  const xiP = Math.atan2(tauP, Math.cos(lambda))
  const etaP = Math.asinh(Math.sin(lambda) / Math.sqrt(tauP * tauP + Math.cos(lambda) ** 2))
  let xi = xiP, eta = etaP
  for (let j = 1; j <= 6; j++) {
    const a = ALPHA[j - 1]!
    xi += a * Math.sin(2 * j * xiP) * Math.cosh(2 * j * etaP)
    eta += a * Math.cos(2 * j * xiP) * Math.sinh(2 * j * etaP)
  }
  return [K0 * RECTIFYING * eta, K0 * RECTIFYING * xi]
}

function tmInverse(x: number, y: number): [number, number] {
  const xi = y / (K0 * RECTIFYING)
  const eta = x / (K0 * RECTIFYING)
  let xiP = xi, etaP = eta
  for (let j = 1; j <= 6; j++) {
    const b = BETA[j - 1]!
    xiP -= b * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta)
    etaP -= b * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta)
  }
  const tauP = Math.sin(xiP) / Math.sqrt(Math.sinh(etaP) ** 2 + Math.cos(xiP) ** 2)
  const lambda = Math.atan2(Math.sinh(etaP), Math.cos(xiP))
  /* τ from τ′ by Newton's method; three steps reach double precision. */
  let tau = tauP
  for (let i = 0; i < 5; i++) {
    const sigma = Math.sinh(E * Math.atanh(E * tau / Math.sqrt(1 + tau * tau)))
    const tauI = tau * Math.sqrt(1 + sigma * sigma) - sigma * Math.sqrt(1 + tau * tau)
    const d = (tauP - tauI) / Math.sqrt(1 + tauI * tauI)
      * (1 + (1 - E * E) * tau * tau) / ((1 - E * E) * Math.sqrt(1 + tau * tau))
    tau += d
    if (Math.abs(d) < 1e-12) break
  }
  return [Math.atan(tau), lambda]
}
