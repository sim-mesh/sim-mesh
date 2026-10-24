# The loss table (SLT1)

A loss table holds the path loss of every ordered pair of nodes of one
nodeset on one geodata, in one band.

```
"SLT1" | u32 header_len | header (header_len bytes, UTF-8 JSON) | u32 n
       | f32 loss_db[n·n]      row-major, [from][to]
       | u8  flags[n·n]        row-major, [from][to]
       | u16 samples[n·n]      row-major, [from][to]
```

Every integer and float is little-endian. `n` is the number of nodes, and
the three matrices follow each other with no padding, so a file is exactly
`4 + 4 + header_len + 4 + 7·n²` bytes. A reader refuses a file that is
shorter, or whose header names a different number of nodes than `n`.

## The matrices

Cell `i·n + j` of each matrix is the pair **from** node `i` **to** node
`j`, the nodes numbered in the order the header lists them. A row is the
node transmitting and a column the node receiving. The diagonal is
unused.

**`loss_db`** is the path loss in dB from the transmitting antenna to the
receiving one, at the header's `f0_hz`, excluding both antennas' gains. A
non-finite value is **never heard**: no frame from that node reaches that
one at any power, and it adds nothing to the receiver's interference. A
writer writes never heard as +∞.

The two directions of a pair are separate cells and need not be equal. A
table computed by a model that is reciprocal holds equal cells; one computed
by a model that is not (P.1812 treats the two terminals' clutter and heights
differently), or measured, holds each direction as it was found.

**`flags`**, one byte per cell:

| Bit | Name | Set when |
|---|---|---|
| 0 | near field | the loss is free space, with at most a knife edge, rather than the full model: the nodes are too close for the model's profile |
| 1 | off pack | an end of the path lies outside the ground data; the cell is never heard |
| 2 | beyond radius | the nodes are farther apart than the table's compute radius, or than the propagation model will take a path; the cell is never heard |
| 3 | line of sight clear | the first Fresnel zone of the path is clear |
| 4 | measured | the loss comes from measurements of a running network, not from the model |

Bits 5 to 7 are zero. A reader ignores a bit it does not know.

**`samples`** is the number of measurements a measured cell was made from,
and 0 for a modelled cell.

## Bands, and the carrier correction

A table is for one band, and is computed at one frequency in it, `f0_hz`. A
frame on carrier `f` in that band sees the cell's loss plus the within-band
correction:

```
loss(f) = loss_db + 20·log10(f / f0_hz)
```

Only the free-space term of a path loss scales that way; diffraction,
clutter and the rest carry the frequency differently. The correction is
therefore applied within a band and never across one, and each band has its
own table.

| Band | Carriers (Hz) | `f0_hz` of a modelled table |
|---|---|---|
| `433` | 410 000 000 – 525 000 000 | 433 920 000 |
| `868` | 850 000 000 – 880 000 000 | 868 000 000, or the frequency the model judged at |
| `915` | 902 000 000 – 930 000 000 | 915 000 000 |

These three are the bands. A carrier in none of them, or in a band with no
table, is never heard.

## The header

A JSON object. Readers ignore keys they do not know.

| Key | Value |
|---|---|
| `geodata` | the geodata's name |
| `geodata_hash` | an opaque string identifying the geodata's content (its file, and for ground data its manifest); a table whose `geodata_hash` differs from the geodata's current one is stale |
| `pack_manifest_hash` | an opaque string identifying the ground data's manifest, or null for geodata with none |
| `nodeset_geometry` | an opaque string identifying what of the nodeset the losses depend on: the set of nodes by name, their positions and heights. Ids, gains, devices, radios, tags, offsets and links are not part of it |
| `band` | the band's name, as in the table above |
| `f0_hz` | the frequency the losses are for, in Hz |
| `model` | `"P.1812-8"` (ITU-R Recommendation P.1812-8 over the ground data) or `"log-distance"` (flat synthetic ground) |
| `p_time_pct`, `p_loc_pct` | the time and location percentages P.1812 was judged at; null for `log-distance`. A table at 50 % of locations is a median, the one to lay shadowing over |
| `exponent` | `log-distance` only: the path-loss exponent `n` in `FSPL(1 m, f0) + 10·n·log10(d)` |
| `radius_m` | the compute radius in metres: pairs farther apart are never heard, flagged beyond radius. Absent when there is none |
| `planner_version` | the version of the propagation planner that computed the table, or null |
| `computed_at` | when the table was computed, ISO 8601 with its UTC offset |
| `nodes` | the nodes in matrix order, each `{name, id, lat, lon, height_m, height_from}` |

A node's `name` is unique in the table and is how a reader finds its row
and column; `id` is its station id, `lat` and `lon` its position in degrees
(WGS84 on ground data; on synthetic ground, degrees at 111,320 m each on both
axes), `height_m` its antenna's height above the ground
in metres, and `height_from` where that figure came from: `measured`, `roof`,
`raster` or `assumed`. Since a reader finds a node by its name, the name is
part of `nodeset_geometry`: a renamed node is one the table does not have.

The losses follow from `geodata_hash` and `nodeset_geometry` alone, so two
tables agreeing on both and on `band` are interchangeable.

```json
{"band":"868","computed_at":"2026-09-25T21:04:11+00:00",
 "nodes":[{"height_from":"assumed","height_m":38,"id":1,"lat":52.5219,"lon":13.4132,"name":"internet"},
          {"height_from":"assumed","height_m":30,"id":2,"lat":52.5208,"lon":13.4094,"name":"gw02"}],
 "f0_hz":869525000,"geodata":"berlin-city","geodata_hash":"4b1d0c77a3e2f590",
 "model":"P.1812-8","nodeset_geometry":"1c0e5a2f9b7d4e61",
 "p_loc_pct":90.0,"p_time_pct":50.0,"pack_manifest_hash":"9f2c…","planner_version":null,
 "radius_m":30000.0}
```
