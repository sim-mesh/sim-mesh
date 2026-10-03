# sim-mesh

A LoRa testbed, run in real time or in virtual time. Stations are real
firmware, built as Linux processes, each on its own loopback address; below
their radio driver sits a model of an SX1262 on a virtual SPI (serial
peripheral interface) bus, and between the models sits one medium, the
ether, that decides who hears what from a table of every pair's path loss.
A map in the browser places the nodes on real ground or on synthetic ground
and shows every frame on the air.

```
browser ── localhost:8800 ──► front.py ─┬─► simd (lora) ─┬─ ether        (UDP, in-process)
                                        │                ├─ stations     (firmware processes, one pty each)
                                        │                └─ proxy        <station>.lora.sim.localhost ─► the station's :80
                                        ├─► simd (supe)  …
                                        ├─► sim_mesh.runner  a script's main, driving one simulation
                                        ├─► planner-web  one per ground-data pack in use
                                        └─► planner-job  a pack built from its sources, a node map imported
```

One simulation is one simd; the front runs several behind port 8800, keeps
their registry, computes their loss tables, runs scripts against them,
starts the planner that supplies real ground and builds that ground from
public sources. Around them are the things
sim-mesh keeps, each on its own because each changes on its own:

| | What it is | Where |
|---|---|---|
| a **firmware** | one station build, installed from its zip: the executable, whatever it needs beside it, and its driver | `firmware/<base>_<arch>_<version>/` |
| an **antenna** | a kind of antenna and its radiation pattern | `testbed/antennas/` |
| **geodata** | the ground: a pack built from public sources, or synthetic ground at 0°, 0° | `testbed/geodata/<name>/`: `geodata.yaml`, and the pack's files beside it |
| a **nodeset** | which nodes stand where with what maximum power and antenna, their tags, the offsets and any stated links; its own setup script beside it | `testbed/nodesets/<name>.yaml`, `<name>.py` |
| a **loss table** | every ordered pair's path loss, derived from geodata and a nodeset | `testbed/losses/…`, a cache |
| a **script** | plain Python against the sim-mesh library, top to end: the time, what each node runs and is given at first boot, and what is done | `testbed/scripts/<name>.py` |
| a **run** | one simulation's output: its record, logs and state | `testbed/runs/<name>/` |
| a **snapshot** | a moment of a run, state and all, to start another from | `testbed/snapshots/<name>/` |

| Piece | Where |
|---|---|
| the launcher | `sim`, and its image, `Dockerfile` |
| the medium | [`ether/`](ether/README.md) |
| the virtual radios, shared libraries sim-mesh provides a station, and the time shim | `radio/` |
| the front, simd, the stores, the library, the page, the analysis tools | `testbed/` |
| the firmware contract: the zip, its node.yaml, its driver, the environment, the ether's protocol and the virtual radio | [The firmware contract](#the-firmware-contract), below |
| the loss table's file format | [LOSSTABLE.md](LOSSTABLE.md) |

It is the firmware under test, built for a different target — not an emulator.
sim-mesh itself knows no firmware project: each firmware arrives as a zip
that carries its own driver, the Python that tells sim-mesh how to talk to
it. [INTERNALS.md](INTERNALS.md) says how it works and why it is built this
way.

## Getting started

sim-mesh needs no firmware tree: stations are firmware zips, added from a
file or from the pre-built ones on [sim-mesh.net](https://sim-mesh.net/firmware/).

It needs **Docker or Podman** and nothing else, on Linux, a Mac or Windows:
`sim` builds its own image on first use (a few minutes, once) and runs
everything inside it, with port 8800 published. The image is the system a
firmware runs on, the same everywhere. `SIM_MESH_NATIVE=1` runs it on the
machine instead, for working on sim-mesh itself on Linux: that needs
`python3` with `aiohttp` and `pyyaml`, `node` and `npm`, `cmake` with a C
and C++ compiler, and `cargo` for real ground and the ether's conductor.

On a fresh Debian, Ubuntu or Fedora, `sim-mesh/sim install` puts that in
place as the image holds it: the system's packages through apt or dnf (with
sudo); a Node the page's build takes (22.22 or later) where the system's is
older, NodeSource's 22 on Debian and Ubuntu, which ship 18, and on Fedora,
whose default is 22.21, its own nodejs24, given its plain names in a Python
environment beside the clone, `sim-mesh/.venv`, which `sim` puts first on
the path; and Rust through rustup. A step whose result is there already is
left out; `--dry-run` says what it would do.

**1. Clone it.** The directory you clone into is the one `sim` mounts
into its image, so a firmware tree put beside sim-mesh later can build
against its radio:

```sh
mkdir mesh && cd mesh
git clone https://github.com/sim-mesh/sim-mesh.git
```

**2. Start it:**

```sh
sim-mesh/sim
```

It builds whichever of the page, the virtual radios, the ether's conductor
and the planner (sim-mesh's own, in `planner/`; without cargo the last two
are left out, sim says so, synthetic ground works and the ether's Python
conductor runs virtual time) is not
built yet or is older than a file of its sources (hidden directories and
`__pycache__` aside), so a fresh clone or a pull needs nothing more, then
starts the front and opens
`http://localhost:8800/`. The terminal is the
testbed's: Ctrl-C there stops every simulation and everything they started.

**3. Firmware.** A station runs an installed firmware. On the **Firmware**
tab, **Add from pre-built…** lists what sim-mesh.net offers for this
machine's architecture (`aarch64` or `x86_64`) and installs one with a
click; **Add from zip…** installs a zip of your own. From a shell,
`sim firmware add <zip or URL>` does the same.

**4. A first simulation.** No geodata and no nodesets come with sim-mesh:
both are your own, kept in `testbed/geodata/` and `testbed/nodesets/` and
never committed. Make ground on the **Geodata** tab, **Build from
sources…** over a rectangle of the map or **New synthetic…**, and click
it to choose it. On the **Nodes** tab place nodes on it, or **Import…**
them in the Layers panel from a public node map, and **Save nodes as
nodeset…**; a nodeset is offered on any geodata that holds one of its
nodes, and is chosen in the Layers panel. On the **Scripts** tab open
`lxmf-traffic`, choose the firmware its nodes run in **Firmware for nodes
not otherwise configured** above the script, and **Run…** it: a new
simulation of that nodeset on that ground. The page goes over to the
simulation's live map, framed on its nodes, and its stations come up over
the next minute; rings on the map are frames on the air. For a simulation
to work with by hand, run `realtime`: every node on the chosen firmware,
on the wall clock, set up by the startup script and left running.

**5. Play with it.**

- **Click a station** for the editor: how it is doing, its **Console** (its
  serial console) and **Web UI** (its own web interface, for a firmware that
  serves one), each in a window over the map with **−** and **+** for its
  zoom (the console's font; the web UI's page, as the browser's zoom, 75 %
  to start), and every setting it has.
- **Right-click the map ▸ New node here** puts down a station. It comes up
  and is set up on its own.
- **Drag a station** and its links change once its row of the loss table is
  recomputed.
- **Right-click a station ▸ Run command…** types one line at the selected
  stations, in their firmware's own language.
- **⋯ ▸ Save snapshot as…** on the simulation's row of the Simulations tab
  keeps the network and everything its stations have become, and **⋯ ▸ Load
  snapshot into it…** brings one back.

What to read next: [Firmware](#firmware), [Antennas](#antennas),
[Geodata](#geodata), [Nodesets](#nodesets) and [Scripts](#scripts) for
making your own network,
[The page](#the-page) for doing it on the map, [Running
simulations](#running-simulations) for the time modes.

## Firmware

A station runs a **firmware**: an executable built for Linux, whatever it
needs beside it, and a Python module, its **driver**, that tells sim-mesh how
to talk to it. A firmware arrives as a zip and is installed under
`firmware/`, unpacked into a directory of the zip's own name. sim-mesh holds
nothing of any one firmware project: what it knows of one is its driver.
[The firmware contract](#the-firmware-contract) specifies the zip,
its `node.yaml`, the driver interface, what a station is given when it
starts, the ether's protocol and the virtual radio a firmware is built
against.

**A firmware is called** `<base>_<arch>_<version>`
(`relay-sx1262_aarch64_20260930163128`): its base, lower-case
letters, digits, `-` and `.`; the architecture it runs on, as `uname -m`
spells it; and a build stamp (`YYYYMMDDhhmmss`, UTC) or a semantic version
(`1.2.3`). The zip has the same name, `.zip` after it. Two builds that
differ in anything sim-mesh does not read — the radio they drive among it —
are two bases, and by custom the base names the radio. **`<base>_latest`**
names the newest installed firmware of exactly that base for this machine,
newest by stamp or by semantic version; a base keeps to one of the two.

**A category** says what kind of mesh a firmware's stations make, and which
driver interface its driver implements: `reticulum` (the one there is),
`meshcore` and `meshtastic` to come. A script's verbs are its category's
(below), and the traffic and delivery analyses are the `reticulum`
category's own.

**Adding** one: **Add from zip…** or **Add from pre-built…** on the Firmware
tab, or

```sh
sim firmware add relay-sx1262_aarch64_20260930163128.zip
sim firmware add https://sim-mesh.net/firmware/<name>.zip
sim firmware prebuilt                     # what sim-mesh.net offers this machine
sim firmware list [SUBSTRING]             # what is installed, and what holds it
sim firmware delete [-f] SUBSTRING        # lists what it would delete, then asks
sim firmware resolve relay-sx1262_latest
```

A zip is refused when it is built for another architecture, when its base
already holds firmware versioned the other way, when a firmware of its name
is installed already, and when its `node.yaml` or anything it names is
missing. **Deleting** is refused for a firmware a paused run or a snapshot
holds: their state can only be resumed on the firmware that wrote it, so the
run or snapshot goes first.

**Pre-built firmware** is listed on [sim-mesh.net/firmware](https://sim-mesh.net/firmware/),
whose `index.html` carries each zip's facts, so the page can say what each
is without fetching it. Projects put theirs there with `tools/deploy-firmware`
([Publishing firmware](#publishing-firmware)).

### Drivers and the `reticulum` verbs

A script means the same thing on any firmware of a category: it asks for a
verb, and each station's driver does it its own way — a line typed at its
console, a framed RPC (remote procedure call) frame, a settings file and a
restart, a tool run against it. Every firmware's verbs (`sim_mesh.driver`),
then the `reticulum` category's (`sim_mesh.reticulum.driver`), which a
script reaches as `<selection>.reticulum.…`:

| Verb | Means | Returns |
|---|---|---|
| `name(name)` | the node's name | |
| `radio(freq_mhz, sf, bw_khz, cr, tx_dbm, sync, preamble)` | slot 0's LoRa settings, those given; `tx_dbm` is at the antenna connector | |
| `radio_up()` | the radio started, for a firmware whose radio waits for it | |
| `tx_power(dbm)` | transmit power at the connector | |
| `diagnostics()` | | what a run keeps of it at the end |
| `role(role)` | `transport`, which forwards others' traffic, or `client` | |
| `path(dest, iface)` | | its path table, `[{dest, next_hop, iface, hops}]`, for `dest` and on `iface` when given |
| `peer_tcp(addr, port)` | a TCP link to another station | |
| `current_role()` | | what it does now, `transport` or `client` |
| `lxmf.create(name)` | one more LXMF identity, `name` its display name, unless the node has one by that name; never refused | its delivery address |
| `lxmf.identities()` | | `[(name, address)]`, the one it sends from first |
| `lxmf.announce(name)` | an announce of that identity's delivery destination (none named: the first) | |
| `lxmf.send(dest, text, mid, sender)` | an LXMF message from its identity `sender` (none named: the first), `mid` sim-mesh's id for it | |

A verb with a dot is grouped, and its driver method has an underscore
(`lxmf.send` is `lxmf_send`). An LXMF identity's name is how a script names
a message's sender or recipient, so a run keeps it unique: no two
identities, and no identity and another node, share one. A node's own name
stands for the identity it sends from.

A verb a firmware cannot do is refused, naming the firmware and the verb, and
the page shows it as `! …` for that station. A driver reads the station's
console line by line and **reports** what happened, an event at the run's T:
for every message sent, `lxmf.message.status` under sim-mesh's id (`pending`,
`sent`, `delivered`, `failed`, with why), into the run's `events.jsonl`,
which is what a traffic report counts. A firmware's own ids for a message
are coupled to sim-mesh's as the driver learns them; what goes wrong before
the firmware has one is reported under sim-mesh's id directly.

A station's **role** is read live through `current_role`; one the driver
cannot read shows the node's role tag.

### What a firmware may count on

A firmware's executable is native code; it may count on the C library and
the C++ runtime of sim-mesh's image (Ubuntu 24.04, of the host's own
architecture) and brings any other shared library in its zip, found through
its node.yaml's `env` (`LD_LIBRARY_PATH: ./lib`). sim-mesh provides the rest
when it starts a station: the **virtual radio** its node.yaml names
(`radio: sx1262`), a shared library the firmware is linked with by name and
never carries, so it keeps working when the chip model or the ether's
protocol changes; the **time shim** in a virtual-time run; and a Python for
its driver. Anything else a station runs — a Python with Reticulum and LXMF
for a station that is a Python program — is in its zip.

A firmware given to a simulation (`sim new --build`, `simd --build`)
runs in place of every firmware of its base. A simulation records the
firmware each name resolved to, and so does a snapshot; a run resumed after
that firmware was deleted resolves the name again.

## Antennas

A node carries one antenna, a type from the catalogue in
`testbed/antennas/catalogue.yaml`: generic representatives of what an
868 MHz node carries, from a bare quarter-wave wire and a spring helical to a
fibreglass collinear, a panel and a yagi, each with its description and a
picture (`testbed/antennas/<type>.svg`). The **Antennas** tab shows them and
draws each one's radiation pattern in the horizontal and the vertical plane.

A pattern is five figures: the peak gain (the kind's typical efficiency in
it), the vertical beamwidth, how far the main lobe is tilted above the
horizon (a small or drooping ground plane lifts it), the horizontal
beamwidth of a directional one, and the floor, the most it falls below its
peak. The loss below the peak is `12·((el − tilt)/vbw)²`, plus
`12·(az/hbw)²` for a directional one, never more than the floor: 3 dB at
half a beamwidth, as a half-power beamwidth says. A directional antenna is
aimed by its node, azimuth clockwise from grid north and elevation above the
horizon; an omni is the same all round.

**Between two nodes** the direction is the line between their antennas'
tips, in three dimensions: the azimuth from one to the other, and the
elevation from one tip (the ground under the node plus its height) to the
other, with the earth's curvature at the effective radius (k = 4/3) taking
its drop off. Each end's gain toward the other is added to the pair, so a
low node close under a high collinear's narrow beam hears less of it than
one on the horizon, and a yagi hears what it faces.

## Geodata

Geodata is the ground nodes stand on, one directory each,
`testbed/geodata/<name>/`, holding everything it is: renaming or deleting
the directory renames or deletes the geodata. Its `geodata.yaml` says which
of two kinds it is:

```yaml
# testbed/geodata/plain-27/geodata.yaml: synthetic ground, a flat plane at 0°, 0°
synthetic:
  terrain: flat
  exponent: 2.7                     # log-distance path-loss exponent
  extent_m: 150000                  # the square it covers, centred on 0°, 0°
```

```yaml
# testbed/geodata/berlin/geodata.yaml: a pack, whose files are this directory's
pack: .
```

**Synthetic ground** lies at 0°, 0°, and its degrees are metres by one fixed
rule on both axes, a nautical mile to the minute of arc: `x = lon · 60 · 1852
m`, `y = lat · 60 · 1852 m`. So a node's position is still latitude and
longitude, the map shows either, and a nodeset made on one synthetic ground
stands on any other. A pair's loss on flat terrain is log-distance,
`FSPL(1 m, f) + 10·n·log10(d)` (FSPL: free-space path loss); `n` is 2 for free
space, 2.7 suburban, and higher numbers bring the neighbourhoods in closer.
**New synthetic…** on the Geodata tab makes one.

**A pack** is ground data compiled from public sources: terrain, clutter,
buildings, roads and places in a UTM (Universal Transverse Mercator) zone.
A pack's files sit beside its `geodata.yaml` (not committed), which says
nothing the pack does not: its extent, projection and layers come from the
pack's `manifest.json`. On a pack, a pair's loss is ITU-R (International
Telecommunication Union, radio sector) Recommendation P.1812-8 over the real
profile, as sim-mesh's planner computes it, and the map draws the pack's
ground, roads and buildings, with the notices of the sources it was built
from in its bottom corner, as OpenStreetMap's Open Database Licence (ODbL)
and Copernicus's terms ask.

**Shadowing** is two keys beside either kind, both absent by default:

```yaml
# testbed/geodata/plain-27-rough.yaml: the same ground, every pair shadowed
synthetic:
  terrain: flat
  exponent: 2.7
  extent_m: 150000
shadowing_db: 7                     # the spread of each pair's draw, dB
shadowing_seed: 3                   # which draws; 0 when absent
```

Every pair of nodes then gets a static draw of its own on top of its loss:
one standard normal per unordered pair, hashed from the seed and the two
node names, times the spread, the same both ways, in every band and for the
whole run. Two pairs at one distance need not hear each other alike, which
is what makes a hidden node or a lucky long link. It is a layer over the
tables, like a nodeset's offsets, so a new spread or seed recomputes
nothing; a pair never heard, a measured cell and a pair a nodeset states a
link for are left as they are.

**`loc_pct`**, on a pack only, is the percentage of locations its tables
are asked for, 1 to 99, and the planner's own 90 when absent. P.1812's
figure at 90 % of locations already holds the spread of losses between
locations, and shadowing over it counts that spread twice, so a pack with
shadowing wants `loc_pct: 50`, the median; the simulation's log warns of
one left at 90, or at anything but 50. The percentage is the table's own:
it is sent with every request and kept in the table's header, and a table
at one percentage is cached apart from, and never used for, another. The
coverage rasters stay the planner's own sweep at 90 %.

There are three ways of getting ground, one button each on the Geodata tab:
**New synthetic…**, **Build from sources…** and **Import zip…**. Nothing else
makes or moves geodata.

### A sim-mesh geodata pack

How geodata goes from one machine to another: a zip holding `geodata.yaml`
at the top, its first line `# geodata <name>`, and for a pack the pack itself
under `pack/`, which the yaml's `pack:` names. Synthetic ground is the yaml
alone. **Export zip** on an open geodata's toolbar writes one; **Import
zip…** takes one, or a bare planner pack (a `manifest.json` at the top or
inside one directory), which becomes pack geodata, by a name the dialog asks
(the zip's own by default).

**Nodes are never part of the ground.** A planner pack may carry a `Nodes`
layer, a deployed network baked in; the compiler never makes one, export and
import leave out its file, its manifest entry and its notice, and the page is
never told of it. Nodes belong to nodesets, which stand on any ground that
holds them.

A pack holds only data that may be passed on. The ITU-R P.1812 maps stay in
the cache: the manifest carries the two numbers taken from them (ΔN and N0),
and each source's notice.

### Build from sources…

```
page ── GET /osm/<z>/<x>/<y>.png ─────────────────► front ── (cache miss) ──► tile.openstreetmap.org
page ── GET /api/geodata/sources?bbox=&res_m= ─────► front         the grid, the sources chosen, the downloads
page ── POST /api/geodata/build {name, bbox, res_m} ► front
front ── fetch into geodata/.cache/<source>/ ──────► the sources' hosts (below)
front ── planner-job pack-build, JSON on stdin ────► planner-job
planner-job ── one JSON line per step ─────────────► front ── geodata_progress ──► every page
planner-job ── geodata/.part-<name>/ ──────────────► front: geodata/<name>/, with its geodata.yaml
```

A view of its own, with **‹ Back**. The map is OpenStreetMap's standard
tiles, fetched through the front and kept under `testbed/osmtiles/` a week
at least, as the tile usage policy asks; the packs there are outlined
with their names, and the areas of the sources that do not cover the world
(Berlin's own data, Germany's census grid) are tinted. A drag pans, the
wheel zooms, Ctrl or Cmd and a drag draws the rectangle, and the place
search asks Nominatim, OpenStreetMap's geocoder, once per search (on Enter),
as its policy allows.

The side panel under the rectangle has its name and resolution (30 m or
10 m), the grid's size in cells and its UTM zone, the zone of the
rectangle's centre; a rectangle wider than its zone, or a grid of more than
25 million cells, is refused with the sentence saying why.

The sources are not a choice: the build takes the best one for each part of
the rectangle, and the lesser one for the rest.

- **terrain and clutter**: Berlin's DGM1 and bDOM (1 m terrain and surface)
  where the rectangle touches Berlin, Copernicus GLO-30 everywhere else;
- **buildings**: Berlin's LoD2 models where the rectangle touches Berlin,
  OpenStreetMap's everywhere else (an OpenStreetMap building on a LoD2
  tile is left out);
- **population**: the Zensus 2022 grid where the rectangle touches Germany,
  none elsewhere;

and always ESA WorldCover land cover, OpenStreetMap roads and places, and
the ITU maps. The panel lists the sources chosen, each with what it is used
for ("buildings inside Berlin", "buildings outside Berlin"), what is still
to fetch (what is in the cache costs nothing) and its licence. **Build**
says the same in a dialog, which sources go into the pack and for which
part, and goes back to the list, where the build's row shows its step and
progress and has **Cancel**; when it ends the row is geodata like any
other, or says why it failed. One
build runs at a time, as a child process the front never waits on; the
compiler's diagnostics go to `testbed/geodata/.cache/logs/<name>.log`.

**The sources** are fetched into `testbed/geodata/.cache/<source>/`, shared by every
build, so a second region beside the first fetches only what is new; a file
is fetched once, resumed where it stopped, and one its host does not have
(GLO-30 over open sea) is not asked for again:

| Source | Covers | From |
|---|---|---|
| GLO-30 surface model, 1° tiles | the world | `copernicus-dem-30m.s3.amazonaws.com` |
| WorldCover 2021, 3° tiles | the world | `esa-worldcover.s3.eu-central-1.amazonaws.com` |
| OpenStreetMap extract (roads, places, sites, buildings) | the world | Geofabrik: the smallest extract whose outline holds the rectangle |
| P.1812-8 ΔN and N0 maps | the world | `itu.int`, kept here, never packed |
| DGM1, bDOM, LoD2 | Berlin | `gdi.berlin.de`'s ATOM feeds: only the tiles meeting the rectangle |
| Zensus 2022 100 m grid | Germany | `destatis.de` |

Geofabrik's index, Berlin's feeds and the MeshCore node list are kept in
`testbed/geodata/.cache/meta/` and asked again when a week old. A Berlin tile's name
is its south-west corner in kilometres of EPSG:25833, so a district costs
megabytes rather than the city's gigabytes. OpenStreetMap is read from one
protocol buffer file (PBF) extract, not from Overpass: one file serves roads,
places, peaks and masts and buildings alike.

**Buildings from OpenStreetMap** are closed `building` ways and building
multipolygons, courtyards kept as holes, written to the pack's
`buildings.jsonl` as LoD2's are, and merged into the clutter height,
`building_top` and `built_fraction` as LoD2's are. A building's height is its
`height` tag; else `building:levels` × 3 m, plus 2 m of roof unless
`roof:shape=flat`; else a default by its `building` value (3 m for a shed
or garage, 8 m for a house, 15 m for apartments, 20 m for a church, 9 m
otherwise). Each line says where its height came from (`source`: `lod2`,
`osm_height`, `osm_levels`, `default`), and the pack's `DataQuality` layer
says, per cell, whether its clutter rests on lidar, LoD2, tagged OSM heights,
OSM defaults, or GLO-30 alone.

A node's height is always above the ground under it, so the same nodes on
other ground rise and fall with it.

**The planner.** A pack needs `planner-web`, sim-mesh's own ground and
propagation server, built from the Rust workspace in `sim-mesh/planner/` (the
crates came from Sergey's planner, and keep their names). sim-mesh runs it as
a **sidecar**: one per pack in use, started by the front on a free loopback
port when the first simulation or page opens geodata on that pack, and
stopped when the last one lets go. The page reaches it as
`/planner/<geodata>/…` on port 8800. Beside it, `planner-job` is the
compiler and the node-map importer, run once per job. `sim` builds both
with cargo as it starts (in sim-mesh's image on a machine that is not Linux).
Not built, a pack is refused with the sentence saying so, and synthetic
ground works.

Geodata names no node, and a nodeset names no geodata. The medium's own
physics (the noise figure, the interference figures) belongs to the ether,
not to the ground.

## Nodesets

A nodeset is which nodes stand where, with what maximum power and antenna,
and tagged how:

```yaml
# testbed/nodesets/mitte7.yaml, abridged
nodes:
  internet: { id: 1, lat: 52.5219, lon: 13.4132, height_m: 38, height_from: assumed,
              antenna: { type: fiberglass_collinear }, tags: [transport, no-radio] }
  gw02:     { id: 2, lat: 52.5208, lon: 13.4094, height_m: 30, height_from: assumed,
              max_dbm: 27,
              antenna: { type: panel_directional, azimuth_deg: 250, elevation_deg: -3 },
              tags: [transport, tcp-peer, lxmf] }
offsets:                              # dB added to one pair's computed loss, both ways
  - { between: [internet, gw02], db: 40, note: "wall, measured 2026-09-20" }
```

A node is its position in degrees, its antenna's height above the ground and
where that figure came from (`measured`, `roof`, `raster`, `assumed`), its
**maximum power** (`max_dbm`, below), its
antenna ([Antennas](#antennas): a type, and a directional one's
`azimuth_deg` and `elevation_deg`; a node that names none carries a
quarter-wave SMA whip), and its tags. Nothing in a nodeset is said to a
station: scripts say everything, keyed off the tags ([Scripts](#scripts)).
Two tags also mean something to the page:

- a role's name, `transport` (or `router`, `repeater`): the startup script
  makes the node one, and the map draws its second ring;
- `no-radio`: the node has no radio. Every other node's radio is the one
  in `testbed/scripts/globals.py`, which the startup script sets and the
  page reads for the coverage, the links and the bands a simulation
  computes tables for.

**A node's maximum power** is its `max_dbm`, in dBm at the antenna
connector: 22 when it states none, at most 27. Every node is one board, an
SX1262. At 22 dBm or below it is a bare SX1262, whose chip puts out the
maximum itself. Above 22 dBm it is an SX1262 behind a GC1109 front-end
module, as a Heltec WiFi LoRa 32 V4 is: the chip drives the front end
through its measured transmit curve, which reaches 27 dBm at the connector,
and the front end adds 20 dB of receive gain ahead of the chip. A node
names no board: a nodeset whose node has a `board:` key is refused.
It is what a node sends at: coverage and links draw it there, and the
startup script's radio sets it (`tx_dbm="max"`); a script can still
change it with `.radio(tx_dbm=…)` or `{max_dbm}` in its lines. A station
is told its board at start (`SIM_MESH_BOARD` in its environment,
[the firmware contract](#3-what-a-station-is-given)).

**A nodeset's own setup**, `testbed/nodesets/<name>.py` beside its YAML,
is a script of declarations for what only its nodes need: mitte7's makes
`internet` a TCP gateway and connects the `tcp-peer` nodes to it. The
startup script includes it for every nodeset of the world; **Edit setup**
on the Nodes tab opens it.

**What a node runs is not the nodeset's**: a script says it
(`firmware()`, [Scripts](#scripts)), so one nodeset is run on any firmware,
or on a mix of them by tag.

**A node's name is how everything refers to it**: scripts, offsets, links,
snapshots, the map, and the proxy's hostnames. **Its id is its network
identity**: it fixes the station's loopback address in the simulation's
network (node 1 is `127.16.0.5` in the front's first network) and the MAC
(media access control) address the station derives, so two nodes never
share one. A new node takes the lowest free id. Changing an id moves the
station: anything it was set up with from its old `{id}` or `{addr}` is
stale, so a station that has state is restarted, and the page says so.

**Offsets** are dB added to one pair's computed loss, both ways, on any
geodata: where a measurement says the model is wrong, and by how much, with a
note of where the figure came from. They are a layer over the loss table,
applied when the medium is given it, so the table stays the model's own and
an offset never forces a recompute.

**Links** state one pair's loss outright, where a better figure than the
model's is known:

```yaml
links:                                # optional
  - { between: [a, b], loss_db: 131.5, back_db: 133, note: measured }
```

`loss_db` is from the first node to the second, `back_db` the other way
(`loss_db` again when absent), the same in every band. A link stands in for
the model's loss and the geodata's shadowing; the antennas and any offset
still go on top, so a pair with both has the offset added to the stated
figure. It is a layer like the offsets, and a nodeset without links has no
`links:` key and is written back without one.

A nodeset is offered on every geodata whose extent holds one of its nodes,
as a layer of the Nodes tab ([The Nodes tab](#the-nodes-tab)).

**Importing** makes a new nodeset of the nodes inside the geodata's extent,
from one of four sources:

- **the MeshCore map**, the node list at `map.meshcore.io/api/v1/nodes`,
  fetched by the front at most once a week into the cache under a user agent
  naming sim-mesh; repeaters and room servers, and companions when asked. An
  advert older than a year, or dated more than a week ahead, is left out:
  both are clocks never set;
- **a PotatoMesh instance**'s `/api/nodes`, by its address: Meshtastic and
  MeshCore nodes both, a position whose precision was cut on purpose left out;
- **planner sites** (`sites.csv`) and **a deployed-network CSV**.

The public Meshtastic maps are not a source: their positions are truncated on
purpose, and PotatoMesh carries the Meshtastic nodes there are. The two node
maps are read by `planner-job nodes-import`, `planner-import`'s parsers. Every
imported node stands at the height the dialog asks
(marked assumed) where the source gives none, has the default antenna and the default radio (at a
CSV's transmit power where it states one), and is tagged with its source, its
kind (`repeater`, `room-server`, `companion`, `router`, `client`, …) and how
good its position is (`position-gps`, `position-fixed`, `position-truncated`,
`position-unknown`). Measurements (range tests, neighbour reports) are not
nodes, and are not imported.

## Scripts

A script is plain Python against sim-mesh's own library, run from its top to
its end, each call doing what it says and returning when it has:

```python
"""A study: one firmware everywhere, another where tagged."""
from sim_mesh import *

firmware = script_input("firmware", type=Firmware, category="reticulum",
                        label="Firmware for nodes not otherwise configured")
other = script_input("other", type=Firmware, category="reticulum",
                     label="Firmware for the nodes tagged other")
sim_speed("max")                                          # declarations first
nodes().firmware(firmware)
nodes(tag="other").firmware(other)
script_include("scripts/startup.py")                      # roles, radios, each nodeset's own
nodes().on_first_boot(Node.reticulum.lxmf.create())

nodes().up()                                              # the first thing done starts it
nodes(tag="transport").reticulum.lxmf.announce(spread=60)
sim_wait(600)
node("gw02").reticulum.lxmf.send("internet", "hello")
sim_pause()
```

The library's names come in three kinds: `script_…`, about the script
itself; `sim_…`, about its simulation; and **selections** of nodes,
`nodes(…)` and `node(name)`, with what is done to them as their methods.

**Inputs** are what a script asks for before it runs: `script_input(name,
type=, label=, default=, category=)` at its top level, its arguments
literals so the page can read them without running the script. Its `type`
is `int`, `float`, `str` (the default), `bool`, `Firmware`, an installed
firmware offered on the Scripts tab as a dropdown above the script of
those of its `category` (`<base>_latest` for the newest of a base, or one
by name), or `Run`, a run's name. From a shell it is `--set
firmware=<name>`. `script_input` returns the value, as its type; an input
with no value stops the script, naming it. The run keeps the values.

**Declarations** come first and are collected until the script first does
something, which starts its simulation with them:

- `sim_speed(speed)`: `"real"` (the default), `"max"`, virtual time as fast
  as the stations allow, or a number, virtual time paced at that many times
  the wall. It is the simulation's for its whole life.
- `<selection>.firmware(name)`: what those nodes run, an installed firmware
  by name or the newest of a base, `<base>_latest` ([Firmware](#firmware));
  usually an input's value. Each node runs the firmware of the last one
  said for it; a node none is said for runs nothing, grey on the map, until
  one is.
- `<selection>.on_first_boot(*rules)`: what each station is given the first
  time it boots with no state (every station of a new simulation, a node
  placed later, a station after a factory reset), after its name, the rules
  in order. A rule is lines in the station's own language, or a firmware's
  command said on the class `Node` instead of a selection, which then is a
  rule rather than done: `Node.radio(freq_mhz, sf, bw_khz, cr, tx_dbm,
  sync, preamble)` slot 0's settings (`tx_dbm="max"`, each node's own
  maximum), `Node.radio_up()` the radio started,
  `Node.reticulum.role("transport")` its role,
  `Node.reticulum.lxmf.create(name=None)` an LXMF identity named after the
  node unless `name` says otherwise, nothing when it has one by that name.
  A rule under `Node.reticulum` is nothing to a station of another category.
- `script_include(path, missing_ok=False)`: another file under `testbed/`
  run here, in a namespace of its own, every time it is included;
  `sim_nodesets()`, the names of the nodesets the world is made of.

**Every script starts from the startup script.** `script_include("scripts/startup.py")`
says, in this order: `Node.reticulum.role("transport")` to the nodes tagged
transport; `globals.py`'s radio at each node's maximum power to every node
not tagged `no-radio`; each nodeset's own setup (`for nodeset in
sim_nodesets(): script_include("nodesets/%s.py" % nodeset,
missing_ok=True)`); then `Node.radio_up()`, last because a radio reads its
settings, SUPE among them, when it starts.
A script's own first-boot lines after the include come after that. The
Scripts tab opens `startup.py`, `globals.py` and the world's nodeset setups
as tabs beside the script, so everything a station is told is on screen.

`testbed/scripts/globals.py` holds what every script shares: the channel
Berlin's Reticulum LoRa nodes announce on [rmap.world](https://rmap.world/)
(869.475 MHz, 125 kHz, SF7, CR 4/5), and `SYNC`, 0x12, the sync word every
RNode-based Reticulum firmware has fixed, so every station hears every
other. Its
`FREQ_MHZ`, `SF`, `BW_KHZ` and `CR` are also read, as written and without
running it, by the page (coverage, links) and the loss tables' band, and a
run keeps a copy of it for its analysis; they stay plain numbers.

Both kinds of rule are kept with the run, so a node placed later, or
retagged, gets what they give it. Said after the simulation has started,
they apply to it at once: a node whose firmware changes is restarted on it,
its state kept. `sim_speed()` cannot change once the simulation runs. No script
code runs inside the simulation: the rules travel to it as data.

**Selections.** `nodes(field=value, …)` selects over each node's `name`,
`id`, `tag`, `firmware`, `base`, `category`, `role`, `antenna`, `max_dbm`,
`lat`, `lon`, `height_m` and `status`, and `node(name)` is one node.
Selections are Python's own set algebra, and a combined one keeps its
methods:

| | |
|---|---|
| `nodes(tag="lora", role="transport")` | both: keywords in one call are AND |
| `nodes(tag=("lora", "tcp-peer"))` | either: a tuple, list or set is any of |
| `nodes(tag="lora") \| nodes(name="internet")` | OR |
| `nodes(tag="lora") & nodes(category="reticulum")` | AND |
| `nodes(tag="lora") - nodes(role="client")` | AND NOT |
| `~nodes(base="relay-sx1262")`, `a ^ b` | NOT, either but not both |
| `nodes(height_m=lambda h: h > 20)` | a function of the value, not in a rule |
| `nodes()` | every node |

A selection is a condition, not a list: written at a script's top, before
there is a simulation, it picks whatever matches when it is used. A rule
travels to the simulation, so its selection must be plain values, never a
function.

**What is done to a selection**, each to every node of it, waiting for them
to be up first (a node with no firmware refuses), and answering
`{node: reply}`:

- `.exec(lines, pause=0)` types lines at each node in its own language,
  macros filled in: one line, several in one string (indented as the code
  around it, blank lines and `#` comments left out), or a list. Each node
  gets its lines in order; the nodes go together, or one after another
  `pause` seconds of the run's clock apart; each answers what it printed.
- `.reset()`, `.factory_reset()`; `.up()`; `.move(lat, lon, height_m=None)`
  (one node); `.facts()`, each node's facts.
- A firmware's own commands, which each driver does its own way: `.radio(…)`
  and `.radio_up()`, every firmware's; and under `.reticulum`, for a
  selection's Reticulum nodes and nothing to the others (a selection with
  none of them refuses): `.reticulum.role(role)`,
  `.reticulum.path(to=None, dest_hash=None, iface=None)` each node's path
  table, `[{dest, next_hop, iface, hops}]`, to a node or LXMF identity, a
  destination, on an interface, or all of it; and LXMF's,
  `.reticulum.lxmf.create(name=None)`, `.identities()` each node's
  `[(name, address)]`, `.announce(name=None)`, and `.send(to, text,
  sender=None)`, a message to a node or an LXMF identity by name, answering
  its id. These take `spread=`, the nodes spread over that many seconds.

**The simulation**: `sim_wait(seconds)`, `sim_until(seconds)`, `sim_now()`,
the run's clock in seconds since the script's simulation began;
`sim_phase((name, until), …)` for the page's estimate; `sim_snapshot(name)`,
`sim_pause()`, `sim_stop()`.

Every command that acts takes `after=` (seconds of the run's clock) and
`wait=False`, which returns at once with a future; `script_results(futures)`
is what they came to: how a script puts many things on the clock at once.
Every time is the run's, so a real-time and a virtual-time run act at the
same instants of the run.

**The scripts' log.** What a script prints and its errors go to the run's
`scripts.log` as well as to the page, each line with the run's T, the wall
clock and the script's name. `script_loglevel("commands")` adds every
command the script gives with each node's answer, and `"debug"` everything
that goes to and from the simulation; `sim_script_loglevel(level)` sets it
for every script on the simulation that says none of its own.

Lines keep these macros until a station is given them:

| Macro | Becomes |
|---|---|
| `{name}` | the node's name — `alpha` |
| `{id}` | its station id — `1` |
| `{addr}` | its loopback address in the simulation's network — `127.16.0.5` |
| `{addr:<node>}` | another node's loopback address, by name — `tcp peer add {addr:internet}:4965` |
| `{max_dbm}` | the node's maximum power at the connector ([Nodesets](#nodesets)) — `lora 0 txp {max_dbm}` |

Anything else in braces is left exactly as written. Its name and the
first-boot rules are the whole of what a station is told at its first boot:
nothing is added behind your back.

**Running one.** The Scripts tab's **Run…** starts a new simulation of the
script's own on the Nodes tab's geodata and shown nodesets (several merged
as **Save visible as** merges them), and goes over to its live map; or
runs it on a simulation already running, which then takes its rules, or on a
paused one, which it resumes. The script is a process of its own (`python3
-m sim_mesh.runner`) whose output is shown beside the editor and kept in the
run's `scripts.log`; from a shell:

```sh
sim run lxmf-traffic --geodata berlin-city --nodeset mitte7 --set firmware=relay-sx1262_latest
sim run ./my-study.py --geodata stralsund --nodeset fachhochschule --nodeset more \
    --set firmware=relay-sx1262_latest --set other=relay-big-sx1262_latest
sim run lxmf-traffic --sim mitte7 --set firmware=relay-sx1262_latest
sim run lxmf-traffic --resume mitte7 --set firmware=relay-sx1262_latest
```

A simulation keeps a copy of a script of `scripts/` (a file from elsewhere
only drives it), and a snapshot keeps that copy. The simulation runs on when
the script ends; one the script paused (`sim_pause()`) stays on the Simulations
tab to be resumed ([Pausing](#pausing-and-resuming)). The other way round,
a script whose simulation is stopped or paused under it, or ends on its
own, stops with "simulation … went away" the next time it waits on it or
asks it anything, and exits 1. A script that stops or pauses its simulation
itself and then waits on nothing more is not stopped by it.

**`report(run_dir)`**, `def` or `async def`, runs after the script has run
to its end, and returns the run's report as Markdown. The runner writes it
to the run as `report.md` with an **ETSI compliance** section after it
(`compliance.py`, [Reading a run](#reading-a-run)), and the page's
**Report** buttons, on the script run and on the simulation's row, show it.
`sim run SCRIPT --report RUN_DIR` writes it again for a run that has
ended, reading the script's definitions without running it.

**The editor** has a tab for the script and one for every file it imports:
another script, edited as the script is, or the library's own module, to
read.

`scripts/lxmf-traffic.py` is a whole LXMF (Lightweight Extensible Message
Format) run, on virtual time, through `sim_mesh.traffic`: announce warm-up
until paths stop growing, a seeded hour of messages, a drain, a gather, with
its record written to the run as `traffic.json`, and the simulation paused.
Its messages are the `lxmf.send` verb, its identities the `lxmf.identities`
one and its warm-up `lxmf.announce`, so it runs on any firmware of category
`reticulum`, the one its input asks for. Its first-boot lines give each
station what taking part needs, an LXMF identity. Its report is the delivery
`delivery.py` counts from what the senders' drivers reported of each message
(the run's `events.jsonl`): overall, by route and radio hops and by size,
the latency, and the undelivered by the last state reported; it says so
plainly when no station had an LXMF identity to send from.

## Loss tables

A loss table is every ordered pair's path loss for one nodeset on one
geodata, in one band (433, 868 or 915 MHz), computed at one frequency in the
band; the ether adds `20·log10(f/f0)` per frame to move it to the frame's own
carrier. It is derived, never edited, and cached under
`testbed/losses/<geodata>/<nodeset geometry>/<band>.bin` (`<band>-loc50.bin`
for a pack at `loc_pct: 50`), keyed by what it depends on: the geodata's
content and the nodeset's nodes by name, their positions and heights. A
table finds a node by its name, so a renamed node's row and column are
computed again, every other pair coming from the table cached before. Ids,
antennas, firmware, radios, tags, offsets, links and a geodata's shadowing
leave it alone: they are layers put on it when the medium is given it.
[LOSSTABLE.md](LOSSTABLE.md) is the file format. A simulation computes a
table for the band `globals.py`'s carrier falls in.

- **Synthetic ground's table** is computed in-process, log-distance, in
  moments.
- **A pack's table** is computed through the sidecar's `/link.json`, one
  request per ordered pair: P.1812 where the path allows, a near-field
  model where the two are too close for it, with the model used and whether
  the first Fresnel zone is clear kept per cell. The planner judges at
  869.525 MHz, 50 % of time and 90 % of locations (or the geodata's
  `loc_pct`), so a pack has an 868 table only. The sidecar answers one
  request at a time, a few milliseconds each: 169 nodes is a few minutes.
- **Every pair is computed**, not only pairs strong enough to carry a frame,
  because a pair far too weak to decode still adds to a receiver's
  interference. Pairs more than 30 km apart, or with an end off the pack,
  are never heard.

The front computes a simulation's tables before its stations start, with
progress on the page, and copies them into the run. A nodeset the cache
has no table for starts from the cached table of the same geodata and band
that has the most of its nodes where they are now, and only the pairs
touching the others are computed: moving one of 169 nodes costs its 168
pairs, not all 14,196. A node moved during a
run has its row and column recomputed into the run's copy, never into the
cache; until it lands the ether keeps the old row. The antenna layer needs
the ground under each node, 0 on synthetic ground and the pack's terrain
there, which the simulation asks of the sidecar once per node and keeps in
the run. From a shell:

```sh
python3 testbed/losses.py --geodata berlin-city --nodeset mitte7 --band 868 --sidecar http://127.0.0.1:<port>
```

prints progress as JSON lines and the cached table's path.

## Who can hear whom

Where a node stands, on its geodata, is the whole of it unless the nodeset
states a pair's loss as a link, which then stands in for the table's: the
level a frame arrives at is

```
L = P_tx + G_tx + G_rx − loss(tx → rx) − offset − 20·log10(f / f0)
```

with `P_tx` the power the frame went out at, `G_tx` and `G_rx` each antenna's
gain toward the other end in three dimensions ([Antennas](#antennas)), the
loss the table's (or the link's), with the geodata's shadowing draw on it
when there is one, and the offset the nodeset's. A frame that arrives below
the signal-to-noise ratio its spreading factor needs — −7.5 dB at SF7, down
to −20 dB at SF12 — is not delivered at all, and that is what "out of range"
means here. So a link is in range only if it is one the modem could actually
hold, and moving to a slower spreading factor really does reach further.

Every transmission whose channel overlaps a receiver's is interference
there, whatever its spreading factor, and each receiver rules for itself: a
frame survives where it clears thermal noise by its spreading factor's
threshold and each spreading factor's summed interference by that class's
rejection figure, over every stretch of its air.
[`ether/README.md`](ether/README.md) has the whole of what the medium
decides, and [INTERNALS.md](INTERNALS.md#reception-at-the-receiver) why.

## The page

Its tabs are **Firmware**, **Antennas**, **Geodata**, **Nodes**, **Scripts**
and **Simulations**, and a status line under them all says how many
simulations and scripts are running (a simulation's name there puts the
Nodes tab on it). Served by a simd on its own, the page is that one
simulation's Nodes tab. Served by the front, it opens on the Geodata tab,
and a page that finds the front serving another build of it (the front's
`hello` names its entry script) reloads itself.

**Firmware** lists the installed firmware ([Firmware](#firmware)), each by
its name with its title, its category, the hardware and radio it plays and
its version, the paused runs and snapshots that hold it, and a trash can
(off for one that is held). **Add from zip…** uploads a zip; **Add from
pre-built…** lists what sim-mesh.net offers this machine, each installed
with one click.

**Antennas** lists the antenna catalogue, each with its picture and what it
is; clicking one shows its figures and its radiation pattern in the
horizontal plane (azimuth, at the horizon) and in the vertical plane
(elevation) ([Antennas](#antennas)).

**Geodata** lists the geodata with its kind, its extent and how many
nodesets have a node inside it, and a pack being built with its step, its
progress and **Cancel** (or why it failed). Nothing is chosen when the page
starts, and the world is empty. Clicking a row **chooses** that geodata:
it is shown on its own, with no nodes, scrollable and zoomable, with
**Export zip** on its toolbar, and it is the ground the Nodes tab works on,
the view shared between the two; **‹ Back** returns to the list, where the
chosen row is marked. Choosing another asks first when the nodeset being
edited has unsaved changes: save them, discard them, or stay. Each row
renames its geodata or deletes it, its pack included; neither is allowed
while a run or a snapshot stands on its pack, whose copy of the geodata
names the pack's directory, and the page says which. **New synthetic…**
makes flat synthetic ground at an exponent and an extent, **Build from
sources…** opens the build view, and **Import zip…** takes a sim-mesh geodata
pack or a bare planner pack ([Geodata](#geodata)).

**Scripts** lists the scripts with their first docstring line, edits one
(saved through the front, which checks it parses), and **Run…** starts a
new simulation of its own on the Nodes tab's geodata and shown nodesets
(which the dialog shows, not changes) and goes over to its live map, framed
on its nodes; a script with a `main` can drive a running simulation
instead. Its output is beside the editor as it comes, with **Stop** while
it runs. A new one is paused when `main` ends.

**Simulations** is the registry of **simulation runs** ([Running
simulations](#running-simulations)): every simulation, running, paused, or ended
(every run directory under `testbed/runs/` that is neither, by its name).
Clicking a running simulation's row opens it: its live map, in place of the
list, with **‹ Simulations** (or the tab itself) back to the list; it stays
open while other tabs are on show, and going to the Nodes tab closes it. Its **⋯** menu
pauses it, resets or factory-resets all its stations, saves a snapshot of
it, or loads one into it; **Stop** ends it. A paused one has **Resume**, and
**Stop**, which deletes the state it was paused with and leaves it an ended
run, ended where it paused; a paused or ended one has a trash can, which
deletes its run directory. A row
whose run has a report, as its script's `report` wrote it, has **Report**.
Each row shows its simulated time T and the real time it has been running
(for a stopped one, from its start to its end; T from its pause, or from the
last line of its record), and how fast T runs; a real-time run's T is its
real time, so it shows only that. An open simulation shows the same in
large figures over the top of its map. Both read as a clock,
`01:33:24`, and from a day on as `2d + 03:12`.

### The Nodes tab

It is always the map, on the geodata chosen on the Geodata tab, and empty
until one is. Every nodeset with a node inside that geodata is a **layer**,
a row of the Layers panel on the left, above the tags; a nodeset with none
there is not listed:

```
Layers                               [New] [Import…] [Save visible as…]
 ● 👁 ● town-core          42      •  ▲ 🗑   active: edited, selected, saved
 ○ 👁 ◯ meshcore-2026-09   318/402    ▲ 🗑   shown, drawn hollow in its colour
 ○ ·  ◯ potatomesh         77         ▲ 🗑   hidden
```

The eye shows or hides a layer; the ring beside it is the colour its nodes
are drawn in. The count is its nodes inside the geodata, and where some are
outside, of how many (amber): those are not loaded, nor drawn, and a Save
writes them back as they were. **One layer is active**: clicking a node,
the selection, the tags, the editor, the coverage and **Save** are the
active layer's. Every other shown layer is drawn in its own colour, hollow,
and names its nodes on hover; clicking one of its nodes, or its name in the
panel, makes that layer active. ▲ moves a layer up the panel; the bin
deletes the nodeset, with its own setup script, after a warning that this
cannot be undone.

Whatever leaves the nodeset being edited — another layer, **New**,
**Import…**, another geodata, another tab — asks first when it has unsaved
changes: save them (a new one is asked a name), discard them, or stay.

- **New** asks a name and adds an empty layer, active.
- **Import…** makes a new layer from a source ([Nodesets](#nodesets)), active.
- **Save visible as…** asks a name and writes one new nodeset from every
  shown layer as it stands, unsaved edits included, top of the panel first.
  With one layer shown it is that layer's Save as; with several it is the
  merge: nodes keep their tags and gain their layer's name as a tag; a name
  an earlier layer took gets the layer's name appended; an id taken gets the
  lowest free one; two nodes within 5 m of each other are one node, the
  earlier layer's; offsets and links come along where both ends do. The new
  layer is active and the layers it came from are hidden.

**Save** writes the active layer back to its own file (amber while there is
something to save; it keeps the file's leading comment); nodes placed with no
layer active make an unnamed one, asked a name at its first Save. The Nodes
tab edits nodesets and nothing else. The same map, opened from a running
simulation's row on the Simulations tab, is that run's live map, with no
layers: the same edits go to the run's own copy of its nodeset (never to
`nodesets/`), and **Save nodes as nodeset…** keeps them.

**The map** is one canvas, drawn in the geodata's own metres. From the
ground up:

- **ground**: for a pack, the planner's base map shaded by terrain or by
  clutter height, composed by planner-wasm from the sidecar's `tile.bin` and its
  server-side bake; on synthetic ground, a grid in metres or degrees at 1, 2
  or 5 times a power of ten, the brighter cross at 0°, 0°, and the extent's
  square;
- **roads and railways** from the sidecar's `roads.bin`, drawn as lines;
- **buildings** from `buildings.bin`: outlines under 9 km across (wider,
  the map says to zoom in to see them), filled by height above the ground
  under 4 km (slate low, sand about 20 m, red 60 m and up); a pack with no
  footprints in its `buildings.jsonl` has none. They are fetched in 1 km squares
  (smaller where one reply would be cut short by the sidecar's vertex cap),
  nearest the middle first, and kept, so every part of the view fills in
  and panning back costs nothing;
- **population**, a pack's residents per cell from its population layer, as
  a heatmap from a faint violet trace to a dense orange-yellow;
- **coverage**: the best margin over the decoding threshold at each point
  from any node on show but those tagged `no-radio`, at each node's maximum
  power, `globals.py`'s spreading factor and bandwidth, and its antenna's gain toward the point in three dimensions, in
  bands of what it is good for: **green** 21 dB and
  more (reception indoors too: 15 dB of walls on top of the edge's 6),
  **yellow** 6 to 21 dB (outdoors only), **red** 0 to 6 dB (the edge, where
  fading decides), nothing below 0 (no chance);
- **offsets** as dashed lines with their dB;
- **links**, for the one selected node and no other: every other node it
  reaches, coloured by the level it would be heard at, green to amber where
  it decodes, red where it only interferes, dashed where the first Fresnel
  zone is not clear, by the table's own figures without the nodeset's
  offsets and links or the geodata's shadowing (attached, the levels the
  ether reports for the nodes that decode stand in for those); always drawn
  while one node is selected: from the run's table, or standalone from that
  node's row and column, which the front computes from the nodes as they
  stand, saved or not, keeping each pair both ways by where its ends stand,
  so selecting the next node or moving one asks only for the pairs that are
  new (`links`);
- **the other shown layers**' nodes, hollow rings in each layer's colour,
  named on hover;
- **the nodes**: a dot each with its name, its antenna's height above the
  ground (`20 m up`) and, smaller, its tags; a second, black ring round a
  node whose role carries others' traffic;
- **links** and **the pair inspector** take each antenna's gain toward the
  other end, as the medium does;
- **live**, attached: a ring from a transmitter for as long as the frame
  occupies the air, a flash at each receiver (green clean, red CRC (cyclic
  redundancy check) failure), the dot's colour its status (grey stopped,
  amber starting or in setup, white up, red restarting), and a dashed amber
  ring round a moved station whose row has not landed.

In its bottom corner, the sources of the ground on show, each once; a click
opens their notices in full.

**Selecting.** A click picks a node; Shift-click toggles one in or out.
Ctrl or Cmd and a drag draws a rectangle that picks what is in it, with Shift
added to the selection, with Alt taken out of it. A plain drag on a node moves
it, and every other selected node with it; any other drag pans, and the
wheel zooms about the cursor. The **tags panel** lists every tag with how many
nodes carry it, and adds the nodes carrying one to the selection, or takes
them out.

**The editor** is on the selection, one node or many. Every field shows the
value the nodes share, or `<multiple values>` where they differ, and a field
changed there changes on every selected node and no other field with it:
antenna, position (one node), height, and tags. The **antenna** is picked
from the catalogue, each with its picture and description; a directional one
has its **azimuth** (clockwise from north) and **elevation** (above the
horizon) beneath it. What a node runs is not set here: a script's
`firmware()` says it, and attached, the editor shows it, or that no rule
names the node. A node's Reticulum
role is not set here: the double ring on the map says a node carries
others' traffic, and attached, the editor shows the role the station
reports; a role tag declares it, and a script's first-boot lines can set
it. The **max power** is the node's maximum power in dBm at the antenna
connector, what it sends at when nothing declares otherwise: empty is
22 dBm, a bare SX1262; above 22 dBm the node has a GC1109 front end as a
Heltec V4 does, up to 27 ([Nodesets](#nodesets)). The **height** is the antenna's, in metres above
sea level, with its height above the ground under it beneath the box; to
its right, in small print, the **terrain** there and, when the node stands
inside a building's footprint, that building's **roof**, both above sea
level, and a click on either puts the antenna there. (The nodeset keeps the
height above the ground; on synthetic ground the terrain is 0 m.) The trash can in
its header removes the selected nodes, after asking. A node's radio is not edited here: a script's
first-boot lines set it, from `testbed/scripts/globals.py`. A tag on all of
them is filled and on some is outlined with how many; clicking it puts it on
every one, its × takes it off every one, and **add a tag** puts a new one on
all, the other tags untouched. For one node it also lists the **extra
losses** (offsets) it has, each opening its pair and removable with its ×.
**Clicking a link line** opens the pair's inspector: the planner's own
`link.json` for the two, with the antennas' heights (and an indoor end's
entry loss), the terrain profile, the table's cell beside it, and the pair's
**extra loss**, set there with a note of why. Attached,
one node's editor shows its status, live role and radio and who hears it at
what level, with **Console**, **Web UI**, **Reset** and **Factory reset**.

**Right-click** on a node: attached, **Console**, **Web UI**, **Reset**,
**Factory reset**, **Announce** and **Run command…**; on a pack, **Estimate
heights from the pack**; and **Remove this node…** (or these). The estimate
is the planner's for a node whose height nobody measured, such as an
imported map's: a roof within reach of an imprecise position with a mast on
it (`roof`), else the clutter or land class around it (`raster`); it
replaces only an `assumed` height, and a node it finds nothing for keeps its
own. On the ground: **New node here**, **New node on this roof**,
select all or none, and fit the view to the nodes. A new node takes the
lowest free id, the default antenna (a quarter-wave SMA whip) and the
default radio (869.525 MHz, SF8, 125 kHz, 14 dBm); attached, it runs what
the simulation's firmware rules give it.

**Coverage** is a heatmap, on by default: the selected nodes' coverage while
any are selected, the whole network's while none are, and the key at the
map's foot says whose. It is computed only while the tab is on show and the
heatmap is coverage. On synthetic
ground it is the log-distance formula, worked out on the page. On a pack each
node's raster is the planner's point-to-area sweep to a receiver 2 m above
the ground within 10 km, which the front has the sidecar compute one node at a
time and caches by the node's position and height (`testbed/coverage/`), so
changing power, radio or antenna redraws at once and moving a node sweeps
only that one; rasters in the cache draw at once and the rest as they land.
Either way the antenna's gain is taken toward each point from its tip to a
receiver 2 m over the ground there, the pack's terrain under each cell
fetched with its raster, so a high collinear's narrow beam passes over the
ground close under it and an aimed panel covers what it faces.

**The display menu** (☰) says how the map is shown: what the ground is
shaded by and its roads and buildings, or the grid's units; the heatmap,
none, population or (on the Nodes tab) coverage, one at a time, with its
key at the map's foot, while everything under it but the nodes and the
selected node's links is drawn grey; and on the Nodes tab the offsets layer and whether nodes
show their names, heights and tags. The Geodata tab has the same menu without
the node entries. Each tab
keeps its own choices, per browser, and the view is remembered per geodata,
the same on the Geodata and the Nodes tab: going from one to the other keeps
the place and the zoom.

**Attached**, a panel over the map shows the simulated time T and the real
time in large figures (a real-time run only the one), with the pace and the
phase under them, and the toolbar how many stations are up; what is done to stations is on the right-click menu of the
selection (Console, Web UI and Reset for one; Factory reset, Announce and Run
command… for all selected), and what is done to the whole simulation is on
its row of the Simulations tab. Every edit is logged in the run with its T.

## Running simulations

A simulation is always a script's: **Run…** on the Scripts tab starts the
Nodes tab's shown nodesets on its geodata, the script's `firmware()` rules
saying what the nodes run, so even working with the stations by hand starts
from a script, `realtime`, whose one input is the firmware. A simulation
runs the nodeset's file, so the nodeset the Nodes tab is editing has its
unsaved changes saved first. `sim new` from a shell picks geodata, a
nodeset and a script, or a snapshot, then a time mode and a firmware. The
front checks them, computes or reuses the loss tables, lays out the run
directory and starts a simd on it, which resolves each node's firmware to
an installed one and loads its driver before its stations start.

**The time** is how the run keeps it, for the ether and every station alike:

| | |
|---|---|
| `real` | the wall clock; a person is in the loop at the pace of a real network |
| `max` | virtual time as fast as the stations allow |
| `<k>x` (paced) | virtual time paced at k times the wall clock: `10x` is ten minutes of the network per wall minute, `0.5x` half speed |

In virtual time the ether owns the run's clock, T, and moves it only when
every station has nothing left to do at the T it has, so what a network does
in an hour takes the stations' own work to run and not an hour, and a loaded
host slows a run down instead of changing it. A frame occupies T for its
time on the air, a station's timers and sleeps run on T, the record and
`seq.py` are stamped with T, and simd logs the T at which a run loaded and
each station came up. How is in [INTERNALS.md](INTERNALS.md#time) and the
wire in [`ether/README.md`](ether/README.md#virtual-time). The Nodes tab,
attached, shows the mode, the pace (for `max`, the pace lately reached) and
T, and a transmission's ring lasts its time on the air at the run's pace.

In a virtual-time run what reaches a station from outside the air lands at
an instant of T, and T waits while it is dealt with: a staggered start, a
line or framed RPC query typed at a console (and the next one, typed when
the reply to it is read), a TCP (Transmission Control Protocol) write from
one station to another, and work a station spends more than a tick of the
host's time on. So with the same `--seed` and `--epoch`, two runs of the same
geodata, nodeset and script put the same frames on the air at the same T. A
station's TCP CLI or web UI reached from outside the run, and whatever a
person does, still arrives at whatever T the run has reached; a driver that
means to act at an instant of the run asks simd to wait on T first (`after`,
[below](#the-control-websocket)).

**Stations start spread over a minute**, not all at once. Two dozen firmware
processes forking in the same instant is a thundering herd against one host,
and the network it makes is worse than the load: stations that boot together
announce together, so the opening minute is a collision storm no network
powered up by hand would ever have. `--stagger <seconds>` changes the
spread; `0` starts them together. The map fills in as they come up.

### The front, and the simulation verbs

```
browser ── localhost:8800 ─────────────────────► front.py ─┬─► simd (lora)  127.0.0.1:9100
browser ── alpha.lora.sim.localhost:8800 ──────►           ├─► simd (supe)  127.0.0.1:9101
driver  ── localhost:8800/ws?sim=lora ─────────►           └─► …
```

`sim` runs the front, `testbed/front.py`, on the published port 8800. It
starts one simd per simulation behind it, each with its own loopback control
port (from 9100), ether port (from 7100), station network (a /22 from
`127.16.0.0` that no socket on the host is bound in and no other front holds
the lock of, so two fronts on one host never share one) and run directory
(`testbed/runs/<name>/`, with the simd's own log in `simd.log`). Arguments
after `--` go to every simd as they stand: `sim -- --pairwise --stagger
30`.

**Simulations** lists each one: its nodeset, geodata and script, its pace
and T, the phase its driver says it is in with a bar of the plan, when it
should be done, and how many of its stations are up; a simulation that exited
says so with its last lines, and a paused one where T stood when it paused.
Clicking a running one's row opens its live map.

### Pausing and resuming

```
runner ── sim_pause {name} ──► front     main ended, on a simulation started for it
front: stop its simd (stations flushed), runs/<run>/paused/ ← the run as a snapshot
front ── sims {…, {name, state: paused, t}} ──► every page
page ── sim_resume {name} ──► front: runs/<run>/paused/ → runs/<name>-N/, a simd on it
```

A **paused** simulation is stopped with everything a snapshot keeps (its
nodeset, script, tables and every station's state) kept inside its own run
directory, as `paused/`, instead of among the snapshots. It stays in the
registry, across a restart of the front too, until it is resumed or deleted.
**Resume** (▶) loads that into a new run directory under the same name, as a
snapshot load would, and starts it in real time, so a person can be at its
nodes: the same network as it ended, booting again, with T from 0 on a new
ether; the paused run is then listed as ended. A script run **on a paused
one** (the Scripts tab's choice, or `sim run SCRIPT --resume <name>`)
resumes it in the script's own time instead, real unless its `time(…)` says
otherwise, and its firmware and first-boot lines apply to it as to a running
one.
**Stop** (or `sim stop <name>`) deletes `paused/` and lists the run as
ended where it paused, so it no longer holds its firmware; its trash can
deletes the run directory, and with it the pause. A script's run pauses the simulation it started when
`main` ends; **⋯ ▸ Pause**, `sim pause`, and `sim.pause()` pause any running
one.

From a shell, `sim`'s simulation verbs do the same (`sim help` lists
every verb); they run inside the front's container, `sim-mesh-front`.
`run` and `new` start the front in the background when nothing answers on
the port (natively, its log is `testbed/runs/front.log`). `sim run` then
opens the page on the run's live map; and a script that lacks an input
with no default, or a simulation to run on, does not run: the page opens on
the Scripts tab with the script open on what was given, its inputs filled
in, to choose the rest and Run. `--no-browser` opens nothing, and only says
the page (`page: <url>`).

```sh
sim run lxmf-traffic --geodata berlin-city --nodeset mitte7 --name lora \
    --set firmware=relay-sx1262_latest         # a script's own simulation (Scripts)
sim new pw --geodata berlin-city --nodeset mitte7 --time max --pairwise
                                               # a bare one, its ether on the pairwise rule;
                                               # prints its control address, ether, network and run as JSON
sim new --snapshot mitte7-warm --time 2x --build relay-sx1262_latest
                                               # named after what it loads: mitte7-warm
sim list
sim plan lora warm-up=+600 traffic=+4200       # T each phase ends at; +N is N s from now
sim pause lora                                 # stopped, its state kept; listed as paused
sim resume lora                                # started again as it ended, in a new run, real time
sim resume lora --time max                     # or as fast as it goes
sim run my-study --resume lora                 # or a script on it, in the script's time
sim stop lora                                  # a paused one: its state deleted, it ended
sim run lxmf-traffic --sim lora --set firmware=relay-sx1262_latest
                                               # a script's main: one of scripts/ by name, or a path
```

A driver never picks a port or a network for a simulation: it asks the front
for one and drives it through `ws://127.0.0.1:8800/ws?sim=<name>`.

### One simd by hand

On Linux or in sim-mesh's image, one simulation is one process:

```sh
cd sim-mesh/testbed && python3 simd.py
```

That starts the ether, the stations, the proxy and the control page, on
`http://localhost:9011/`, and loads the run its run directory holds, if it
holds one; otherwise it waits for a driver to send `sim_load`. It runs in the
foreground: Ctrl-C stops it, and everything it started. It takes `--bind`
(default `0.0.0.0:9011`), `--ether` (default `127.0.0.1:7000`), `--run` (the
run directory, default `testbed/runs/simd/`; a run loaded later that would
land on one there goes beside it as `-2`, `-3`…), `--build`, `--sidecar` (the
planner-web a pack's moved rows are recomputed through), `--stagger`,
`--net`, `--time`, `--noise-figure`, `--pairwise` or `--bench-capture`,
`--crc-margin-db` (the ether's receivers, its rule and its CRC band), and
`--no-interference` (an oracle: every frame judged against noise alone),
`--clock-ppm` (in virtual time, each station's crystal off by a draw within
that many parts per million), and `--seed` and `--epoch` (the seed the ether's welcome
carries, which in a virtual-time run also keys every station's randomness,
and the wall clock T 0 stands for; two runs of one network given both draw the
same random bytes and the same timestamps).

simd places each station on one CPU, in turn over the CPUs it may use, and
every station talks to the ether hundreds of thousands of times a run. On a
machine whose cores do not all share one last-level cache (an AMD EPYC or a
Ryzen with several core complexes), starting simd under `taskset` to CPUs that
share one keeps that talk inside the cache: a 173-station run took a fifth
less time and a seventh less CPU so, record for record the same. Whether a
run's load fits in those CPUs is the operator's to judge, so simd does not
choose them.

A simd started beside the front, or beside another, needs its own port,
ether, station addresses and run directory, because every station binds its
own address, two stations on one address are one port taken twice, and two
testbeds in one run directory would write over each other:

```sh
python3 simd.py --bind 0.0.0.0:9012 --ether 127.0.0.1:7001 --net 127.0.4.0/22 --run /tmp/run2
```

takes its addresses from `127.0.4.0/22` instead of `127.0.0.0/22`. A network
is filled one /24 at a time with hosts 5 to 254: node 1 is the network's
first `.5`, node 250 its `.254`, node 251 the next /24's `.5`. The default
/22 holds 1000 stations; a wider network holds more. The front's children
take their networks from `127.16.0.0/22` up and their ports from 9100 and
7100 up, stepping around any port already taken, so a network below
`127.16.0.0` is one no child will be given.

## Runs and snapshots

A **run** is one simulation's output, `testbed/runs/<name>/`, and the
directory its stations run in:

| Path | What it is |
|---|---|
| `run.yaml` | the geodata, nodeset and script names, the time mode, the firmware rules and what each node runs, where each firmware's build was, the ground under each node, when it started, and the snapshot it started from |
| `geodata.yaml`, `nodeset.yaml` | the geodata as it was, and the run's own copy of the nodeset: edits during the run go here, never to `nodesets/` |
| `script.py` | the script it was started with, as it was, when it had one |
| `globals.py` | `scripts/globals.py` as it was: the radio the run's analysis takes its stations to have |
| `losses/<band>.bin` | the loss tables the ether reads, the run's own copies |
| `nodeset-edits.jsonl` | every nodeset edit during the run, with its T |
| `nodes/<name>/state/` | the station's state store |
| `nodes/<name>/log` | everything the station wrote to its console, across restarts |
| `record.tsv` | every message in and out of the ether |
| `simd.log` | the simd's own log |

A **snapshot** is a moment of a run, taken with **⋯ ▸ Save snapshot as…** on
the simulation's row (or `snapshot_save_as` on the control websocket, or
`sim.snapshot`),
`testbed/snapshots/<name>/`:

| Path | What it is |
|---|---|
| `snapshot.yaml` | taken at which T, from which run, which builds |
| `geodata.yaml`, `nodeset.yaml` | the geodata and the nodeset, as the run had them |
| `script.py` | the run's script, when it had one |
| `losses/<band>.bin` | the run's tables |
| `nodes/<name>/state/` | every station's store |

A simulation given a snapshot (**⋯ ▸ Load snapshot into it…** on its row, or
started from one with `sim new --snapshot`) gets its nodeset, script and tables
back exactly as they were, without a recompute and without the planner, and
every station its store: identities, keys, paths and message history, as the
firmware keeps them. The snapshot keeps its script and its firmware and
first-boot rules because first boot comes only to a station with no state:
a factory reset after the load sets a station up as the first run did.

What comes back is what the firmware reloads at boot: a snapshot is a copy
of each station's store, so whatever a firmware keeps only in memory, or
writes to its store only now and then (a path table, say), comes back as of
its last write.

Stations keep running across a snapshot: they are flushed first and the copy
is taken while they run, which for a store that commits whole files is the
same guarantee a power cut gives a board. Logs and the record are an account
of one run and are never copied into a snapshot. A snapshot holds the
firmware that wrote its state, which cannot be deleted while it does. Runs,
snapshots, the loss and coverage caches and the installed firmware are not
committed.

## The verbs

**On one station** (the editor, or the right-click menu) **and on the whole
simulation** (the ⋯ menu on its Simulations row):

| | |
|---|---|
| **Reset** | presses reset. The process exits and comes straight back; its state is untouched, so it is the same station it was. |
| **Factory reset** | wipes its state, restarts it, and it is set up again on the empty store. Identities, keys, paths and message history go; the nodeset and the script do not. |

Across the whole simulation both are spread over the same `--stagger`
window the start uses, and for the same reason.

**Right-click ▸ Run command…** on the Nodes tab types one line at the
selected stations, and lists what each one said. A line is one firmware's
language, so the stations must all run one base; the dialog asks which when
the simulation has more than one. The macros are expanded per station.
**Right-click ▸ Announce** is the `lxmf.announce` verb, each driver doing it its
own way.

Beside the line is **spread**, in seconds. Left at 0 every station is asked at
once, which is what a question wants. Anything that *transmits* wants a
spread: two dozen stations announcing in the same instant is a collision
storm rather than an announcement, and what comes back describes the storm.
Thirty or sixty seconds across the network is enough.

### The control websocket

```
page/driver → simd   command {line, name? | names? | tag?, base?, stagger?, after?, id?}
page/driver → simd   meta {verb, args?, name? | names? | tag?, base?, stagger?, after?, id?}
simd → all pages     command_result {id, line | verb, name, results: {<station>: <reply>}, t}
driver → simd        plan {phases: [{name, until}]}
simd → all pages     clock {mode, rate, t, observed, barriers, slow_idles, plan}   once a wall second
```

Everything the page does is one JSON message on `ws://<simd>/ws`, and a
driver speaks the same messages; `testbed/simd.py`'s docstring lists them all
(`sim_load`, `snapshot_load`, `snapshot_save_as`, the `nodeset_*` edits by
node name, `levels`, the resets). `command` goes to the stations that are up
(or in setup) among those named by `name`, `names` or `tag`, or to all; they
must all run one firmware base or `base` must narrow them. `meta` is a verb
of the stations' category ([the `reticulum` verbs](#drivers-and-the-reticulum-verbs)),
each chosen station's driver doing it its own way, its reply what the verb
returned; `lxmf.send` and `path` take `to`, a node or an LXMF identity whose
address is asked of its station first, `lxmf.send` may take `from`, its
sender the same way, in place of a choice of stations, and answers each
message's id, which simd gives it; `peer_tcp` takes `to` and `port`. `stagger` spreads
the stations over that many seconds; `after` holds the whole back that many
seconds on the run's clock, so in a virtual-time run it lands at an instant
of T. Its answer is one `command_result`, broadcast to every page: the
asker's `id`, each station's reply by name (a failure as `! why`), and `t`,
the run's clock when the last reply came. `clock` says how the run keeps
time — `rate` the pace asked for (null: as fast as it goes), `observed` the
pace of the last wall second, `t` the run's clock in microseconds,
`barriers` how often T has moved and `slow_idles` how many idles have arrived
at the busy watchdog's pace ([INTERNALS.md](INTERNALS.md#time)); the
`snapshot` a page gets on connecting carries one too.

`plan` is a driver saying what the run is for: its phases in order, each
with the T it ends at in microseconds. simd keeps it until the next `plan` or
the next load, and puts it in every `clock` as `{t, phases}`, `t` being when
it was given; `phases: []` clears it. From it the page shows the phase, how
far into it the run is, and when it will be done at the pace of the last two
minutes. The LXMF traffic driver sends one once every station is up and again
whenever its warm-up runs on.

`ws://<simd>/ws?quiet=1` is a socket without `tx`, `rx`, `radio` and `levels`
— everything only a map draws, and nearly all the traffic on a busy network.
A driver wants it.

Through the front the same messages pass both ways, plus the front's own:

```
page/driver → front   sims                                       the registry, as it stands
page/driver → front   sim_new {name?, geodata, nodeset, script? | snapshot, time?, stagger?, build?, pairwise?}
front → asker         sim_new {ok, name, control, ether, net, run, time, geodata, nodeset, script, snapshot, builds}
                              or {ok: false, error}
front → every socket  losses_progress {sim, band, done, total}  while its tables are computed
page/driver → front   sim_stop {name}                            stop it, or forget one that exited
front → asker         sim_stop {ok, name} or {ok: false, error}
page → front          select {sim}                               the simulation this socket is on
front → all sockets   hello {front: true, port}                  on connecting
front → all sockets   sims {port, sims: [...], script_runs: [...], geodata_names, nodesets, scripts, snapshots}
                                                                 on a change and once a wall second
front → all sockets   script_output {run, line} · script_exit {run, code}
anything else         → the simulation named by `sim`, or the one selected
simd → socket         the simulation's own message, with `sim` added
```

`ws://<front>/ws?sim=<name>` selects a simulation from the start and
`&quiet=1` asks for its quiet stream. A row of `sims` carries the
simulation's `state` (`starting`, `running`, `stopping`, `exited` with its
`code` and last lines in `tail`), the `geodata`, `nodeset`, `script` or
`snapshot` it loaded, `time`, `mode`, `rate`, `t`, `pace` (T per wall second
over the last two minutes), station `counts` by status, its `plan`, the
current `phase` `{name, from, until, eta}` and `eta`, the wall time the last
phase should end, and where it is: `port`, `ether`, `net`, `run`. `GET
/api/sims` is the same as JSON.

The front's editor verbs are answered to the asking socket as `{type, ok,
…}`, and need no simulation running:

| Verbs | |
|---|---|
| `firmware_list` | the installed firmware, each with what holds it |
| `firmware_prebuilt`, `firmware_add {url}` | what sim-mesh.net offers this machine, and one of those installed |
| `firmware_delete {names}` | firmware removed, refused for any a paused run or a snapshot holds |
| `antenna_list` | the antenna catalogue, each with its picture |
| `geodata_list`, `geodata_open`, `geodata_close`, `geodata_new`, `geodata_save`, `geodata_save_as` | geodata, each listed with how many nodesets have a node on it; opening a pack holds its sidecar for the socket |
| `geodata_rename {name, to}`, `geodata_delete {name}` | another name, or gone, with its own pack; refused while a running simulation stands on it; a delete says what keeps the pack (`kept_by`) |
| `nodeset_list {geodata?}`, `nodeset_open`, `nodeset_new`, `nodeset_save`, `nodeset_save_as`, `nodeset_delete` | nodesets; with `geodata`, each row says how many of its nodes stand on it (`inside`); a delete takes the nodeset's own setup script too |
| `nodeset_import {name, source, …}` | a new nodeset from the MeshCore map, a PotatoMesh instance, `sites.csv` or a deployed-network CSV |
| `nodeset_merge {name, layers}` | Save visible as: the shown layers, top first, as they stand, as one new nodeset |
| `script_list`, `script_open`, `script_new`, `script_save`, `script_save_as` | scripts, checked to parse, each with its inputs |
| `script_run {name, sim \| geodata, nodeset, inputs?}`, `script_stop {run}`, `script_log {run}` | a script's `main` as a process, its inputs given, and its output |
| `snapshot_list`, `losses_compute` | the snapshots, and a nodeset's tables |
| `coverage {geodata, nodes}` | each node's pack raster: cached ones at once, the rest as `coverage_tile` messages as they land |

and its HTTP side: `POST /api/firmware/add?name=<zip name>` and `POST
/api/geodata/import?name=<geodata>` take a zip as the body, and `GET
/api/geodata/export?name=` sends one; `GET /api/geodata/sources?bbox=&res_m=`
says what a build takes and which sources it chose, `POST /api/geodata/build` starts one and `DELETE` on it
cancels it, its progress going to every page as `geodata_progress`; `GET
/osm/<z>/<x>/<y>.png` and `/api/nominatim?q=` are OpenStreetMap's tiles and
geocoder through the front; `GET /api/table?path=` serves a loss table and `GET
/api/coverage?geodata=&key=` a coverage raster; `/planner/<geodata>/…` is the
pack's sidecar. `front.py`'s docstring has every field.

## Reading a run

Every analysis tool reads a run directory, and takes names, positions, radio
settings and levels from it — the run's nodeset (its tags: a role tag the
node's role, none a `client`, and `no-radio`), the run's copy of
`globals.py` (every other node's radio, at its maximum power), its geodata, the firmware each
node ran and its own loss tables with the links, shadowing, antennas and offsets on them —
never the files as they stand now. What a frame means is a protocol's, under
`testbed/sim_mesh/<protocol>/`, found by each node's firmware category; the roles are
what `airtime.py --roles` and the hop counts through forwarding stations use.

| Tool | Says |
|---|---|
| `seq.py RUN` | the record as a sequence diagram (below) |
| `compare.py RUN_A RUN_B` | two runs side by side: milestones, per-station counts, paths; one run's figures alone |
| `airtime.py RUN` | airtime per station, per carrier and per frame kind; transmit power; exchanges at reduced power; CRC losses; with `--busy` the calling channel's occupancy where each station stands, with `--roles` airtime per role |
| `links.py RUN` | link geometry: distance of every usable one-way link, neighbours, hop diameter, beside what the run's loss table says would decode; with `--power`, traffic-channel power against distance |
| `delivery.py TRAFFIC.json RUN` | an LXMF traffic run's delivery (the `traffic.json` `scripts/lxmf-traffic.py` writes) from what the senders' drivers reported (`events.jsonl`): by route hops, radio hops (by the run's loss table), size, latency |
| `compliance.py RUN` | which nodes went over their EN 300 220 budgets: per node and band entry, seconds on the air in the worst sliding hour against the entry's duty cycle (or polite spectrum access where the entry permits it: 100 s an hour per 200 kHz, frames up to 1 s, 100 ms apart on one carrier), and highest e.r.p. (power + antenna gain − 2.15 dB) against its limit; frames on spectrum no entry allows. The section every report ends with; `--json` for the figures |
| `referee.py --run RUN` | how the stations behaved on the air: every transmission that began over a frame on its carrier its sender could decode, whether the ether had told the sender of that frame (at its start, mid-frame, never) and in which window of it (its first four symbols, its preamble, its payload); the frames nobody was told of, by sender and channel; every reception that ended `crc`, each overlap split by whether the two senders could hear each other. Run by hand, not part of `report.md`; `--detail` for every event, `--json` for all of it |

`seq.py` draws a run's `record.tsv` — one lifeline per station, one arrow
per station that heard a frame, the verdict at each arrow head, and the
Reticulum packet read out on the right:

```
$ python3 seq.py runs/lora --tail 4
   t (s)   delta    india    kilo     mike     papa    sierra
   0.118     │        ◀────────┼────────┼────────┤        │  ANNOUNCE  single/4e3874cc  of rnstransport.probe  hops=0  167B
             │        │        │        │        ├────────▶
   0.250     ✗────────┼────────┼────────┼────────┼────────┤  ANNOUNCE  single/edec275b  of rnstransport.probe  hops=0  167B
             │        │        │        │        ✗────────┤
```

One transmission heard by two stations is two arrows on two rows, sharing the
timestamp and the reading: every arrow has one end at the station that
transmitted, so nothing in the picture can be read as a frame travelling
between two stations that cannot hear each other. `--only <words>` keeps the
rows whose reading matches, and `--record <file>` reads a record on its own
(its lifelines are then ids, or `--names 1=alpha,2=bravo`). In a run with no
Reticulum station, a frame reads as its length and carrier.

## A station's own doors

A station whose firmware serves a web UI serves it on the port its driver
names (`web_port`), on its own loopback address, which is invisible outside
the machine or image sim-mesh runs in; the proxy on port 8800 routes by
hostname, the simulation being the second label (a station whose firmware
has no web UI is refused with a sentence saying so, and has no **Web UI**
button):

    http://alpha.lora.sim.localhost:8800/    by name
    http://1.lora.sim.localhost:8800/        by id

A bare `alpha.sim.localhost` reaches the one running simulation while there
is exactly one; with more it is refused with their names. Chrome and Firefox
resolve any `.localhost` name to loopback with no configuration. Safari does
not, and needs entries in `/etc/hosts` on the Mac:

    127.0.0.1  alpha.lora.sim.localhost bravo.lora.sim.localhost

Port 8800 is the testbed's own: where `sim` runs sim-mesh in its image, the
container publishes it at the same number on the host. It is clear of the
ports spangap holds (9000–9011), the planner's 8787, and the per-simulation
control and ether ports the front counts up from 9100 and 7100, so a testbed,
a flashmon and a dev server can all run at once.

A station's web UI is the **whole** UI, not a static shell: it speaks the same
WebRTC DataChannel to the browser that a board does, from the same firmware
source, so the live panes — settings, the log, the CLI, Activity — all work.

```
browser ──ws  alpha.lora.sim.localhost:8800/webrtc──► front ──ws──► simd ──ws──► alpha   signalling
browser ──udp localhost:8800─────────────────────────► front ──udp─► simd ──udp─► alpha   the channel
```

The DataChannel is UDP and the station's own address is out of the
browser's reach, so the signalling passes through the front and the simd,
each of which points the station's SDP (session description) answer at
itself, and each relays the UDP behind it, picking the station out of each
packet by the ICE (interactive connectivity establishment) ufrag it saw in
that answer. Port 8800 is published on **UDP as well as TCP** for it
([INTERNALS.md](INTERNALS.md#the-webrtc-relay-and-why-it-is-a-relay-rather-than-a-second-transport)).
Neither end knows. The station is answering ICE from a peer that happens to
be a relay, and the browser is talking to a station that happens to be
simulated — which is the point: the code under test is the shipping code,
on both sides.

Besides the map, a station is reachable three other ways:

- **Console** — its serial console, in a terminal window over a websocket:
  whatever the firmware prints, and whatever a person types at it, as on a
  board on a cable.
- Its firmware's own door, which its driver uses: a framed RPC channel on
  the console (below), a tool run against a port the station makes, a
  settings file and a restart — whatever the firmware has.
- `tail -f runs/<sim>/nodes/<name>/log` — everything it has printed, across
  restarts.

### Framed RPC on the console

```
station → simd   "… serial] framed rpc v1"                 once, early in boot, as log text
simd → station   F5 53 47 01 <id> <len:2> show s.net.hostname
station → simd   F5 53 47 01 <id> <len:2> s.net.hostname = alpha
simd → station   F5 53 47 01 <id'> <len:2> <a command line>    one frame per question
station → simd   F5 53 47 01 <id'> <len:2> <what it printed>
```

A firmware may multiplex a framed side channel onto its serial console
([`spangap-core/docs/framed-rpc.md`](../spangap-core/docs/framed-rpc.md)), and
a station's console here is its pty. A frame is never echoed, never enters the
line editor and never turns the log into a CLI session, so a driver asks a
station things while a person types at its **Console**. The pty drain takes
each reply frame out of the stream and passes every other byte on unchanged,
so the log and the console window never see one. A driver speaks it through
`rpc_ready` and `rpc_query` (`sim_mesh.driver`).

A station answers framed RPC once it has printed the marker since it last
started and answered the probe with something. A station that prints no
marker within the driver's wait is sent the probe once, blind, and a Ctrl-C
after it if it does not answer; that undoes it on firmware that does not
speak frames.

The firmware runs one frame at a time, bounds each at five seconds, and cuts a
reply that outgrows its buffer at the last complete line without saying so.
So one frame is in flight per station. The id is a hash of the command, so a
retry carries the one it had, and a reply that arrives after its query gave
up answers the retry. A command line of up to 4096 bytes runs over a frame;
a longer one is refused with `rpc: command over 4096 bytes`.

A station that exits is started again, because a restart on this target is a
process exit: a station rebooting itself comes back on the same address with
the same directory.

## Building a firmware zip

A firmware project makes its own zip: its station built for Linux, against
the virtual radio's header (`radio/include/simradio.h`) and linked with the
radio's shared library by name (`-lsimradio-sx1262`, in `radio/build/` once
`sim` has started, or a copy built from `radio/` with the firmware's own
compiler when it is built for another architecture), never carrying the
library itself; its driver, a Python module
subclassing its category's driver class; anything else it runs, and the
shared libraries beyond the C library and C++ runtime, under `lib/`; and a
`node.yaml` naming them. [The firmware contract](#the-firmware-contract)
is the whole of what a zip must be and what a station is given, and how to
build against the virtual radio. A firmware built on Portduino links
[`radio/portduino/`](radio/portduino/README.md), which puts the virtual radio
under it in place of spidev and libgpiod.

`sim firmware add` installs the zip, and `<base>_latest` runs the newest
one added, so a project's loop is: build, make the zip, add it, run.

### Publishing firmware

Pre-built firmware is the `firmware` release of sim-mesh's own repository,
which sim-mesh.net's site serves under `/firmware/` with an `index.html` that
lists every zip with its `node.yaml`'s facts:

```sh
tools/deploy-firmware ZIP…                  # add or replace these, then redeploy the site
tools/deploy-firmware --delete NAME…        # take these off it
tools/deploy-firmware --dry-run ZIP…        # say what it would do
```

It needs `gh`, logged in with the right to upload to sim-mesh/sim-mesh.

## The firmware contract

This is the whole of what passes between sim-mesh and a firmware. A firmware
that keeps it runs on sim-mesh with nothing of it in sim-mesh; sim-mesh that
keeps it runs any such firmware. **Must**, **must not** and **may** are
requirements; everything else describes.

```
project ── its own script ──► <base>_<arch>_<version>.zip ──► sim firmware add ──► firmware/<name>/
script  ── nodes().firmware("<base>_latest") ──► simd: resolve, import the zip's driver.py, DRIVER(firmware)
simd    ── start the executable: SIM_MESH_* env, LD_LIBRARY_PATH (the radio), LD_PRELOAD (the time shim)
station ── libsimradio-sx1262.so ── UDP, JSON ──► the ether          hello, state, tx, idle …
simd    ── driver.wait_up / verbs / flush ──► the station          over its console, a tool, a file …
station ── console lines ──► driver.console_line ──► station.report(event) ──► run/events.jsonl
```

### 1. The firmware zip

A firmware is one station build for one architecture: a zip archive named

```
<base>_<arch>_<version>.zip
```

- **base**: lower-case letters `a`–`z`, digits, `-` and `.`, starting with
  a letter or digit; never `_`. Two builds that differ in anything sim-mesh
  does not read, the virtual radio they drive among it, are two bases; by
  custom the base ends with the radio (`relay-sx1262`).
- **arch**: the architecture it runs on, as `uname -m` spells it on Linux:
  `aarch64`, `x86_64`.
- **version**: either a build stamp, the UTC time of the build as
  `YYYYMMDDhhmmss` (fourteen digits), or a semantic version
  `MAJOR.MINOR.PATCH` with an optional `-pre-release`. A base **must** keep
  to one of the two.

The name splits at its first and its last `_`. Installed, the firmware is the
directory `firmware/<base>_<arch>_<version>/`, the zip unpacked, so a zip and
its installed directory have one name. `<base>_latest` means the newest
installed firmware of exactly that base for the machine: by stamp, or by
semantic version (a pre-release before its release).

Members sit at the archive's root, with forward-slash paths that **must**
stay inside it (no absolute path, no `..`). It holds `node.yaml`, the
executable, the driver, and whatever else the station runs. A member's Unix
permission bits, where the archive records them, are the file's; the
executable is made executable whatever the archive says.

A zip is refused when it is built for another architecture than the
machine's, when its base already holds firmware versioned the other way,
when a firmware of its name is installed already, and when its `node.yaml`,
or anything it names, is missing.

### 2. node.yaml

A YAML mapping at the archive's root.

| Key | Required | Value |
|---|---|---|
| `base` | yes | as in the name |
| `arch` | yes | as in the name |
| `version` | yes | as in the name, a string |
| `category` | yes | the driver interface its driver implements, and so the verbs it answers: `reticulum` (§7); `meshcore`, `meshtastic` to come |
| `exec` | yes | the executable's path in the archive |
| `driver` | yes | the driver's path in the archive, a Python file (§6) |
| `radio` | no | the virtual radio it is linked with, by the library's name: `sx1262` is `libsimradio-sx1262.so` (§9). Absent: sim-mesh provides none, and the station speaks the ether's protocol itself (§8) or has no radio |
| `fixed` | no | the path of a read-only data tree in the archive, which its driver may hand the station |
| `env` | no | a mapping of environment variable to value, given to the station after sim-mesh's own (§3); a value starting `./` or `../` is a path in the installed firmware. `LD_LIBRARY_PATH` here is put before sim-mesh's, not instead of it |
| `title` | no | what the page calls it (`Relay 2.1 (dev)`) |
| `hardware` | no | the hardware it plays (`ESP32-S3`), shown as “virtual ESP32-S3” |

Other keys are kept and not read.

### 3. What a station is given

sim-mesh starts the executable with its argument list from the driver (§6),
its working directory its own (below), its stdin and stdout one pty (§4),
and this environment: the host's, then these, then node.yaml's `env`, then
the driver's `env`.

| Variable | Meaning |
|---|---|
| `SIM_MESH_NODE_ID` | a small integer, unique on the host: the station's id in the ether (its `sid`), and the last bytes of any MAC address it makes |
| `SIM_MESH_NODE_DIR` | its directory and working directory; its state lives under `state/` |
| `SIM_MESH_BIND_ADDR` | its own loopback address, fixed by its id; every socket it opens **must** bind here |
| `SIM_MESH_ETHER` | `host:port` of the ether |
| `SIM_MESH_RADIO_LIB` | the path of the virtual radio its node.yaml names; its directory is first on `LD_LIBRARY_PATH` |
| `SIM_MESH_BOARD` | the board its node is, one flat JSON object: `chip`, `max_dbm` (the most at the antenna connector), and above 22 dBm a GC1109 front end's `fem_part`, `fem_tx_cal` (chip register → connector dBm), `fem_gain_db` and `fem_rx_gain_db`. The virtual radio applies the front end from it; a firmware that drives a front end takes its figures from here |
| `SIM_MESH_TIME` | `virtual` in a virtual-time run, absent in a real-time one |
| `SIM_MESH_EPOCH_US` | virtual time: the wall-clock microseconds T 0 stands for |
| `SIM_MESH_SEED` | virtual time: the run's seed; the time shim keys the station's `getentropy`/`getrandom` by it and the node id |
| `LD_PRELOAD` | virtual time: the time shim, `libsimclock.so` (§5) |
| `SIM_MESH_IDLE` | set by a driver whose station does not call `simradio_idle()` itself: `threads`, and the shim says the station is idle when every thread is blocked |
| `SIM_MESH_MULTI_SF` | optional, from node.yaml's `env`: set (and not `0`), the chip model's receiver also hears the faster spreading factors below its own on its bandwidth, as an LR2021's multi-SF receive does (its side detectors, by that chip's rule: SF7 at 125 kHz hears SF5 to SF7), and states them to the ether (`sfs`). An SX1262 has no such thing: it stands in for the LR2021, which the model does not have, where firmware is judged against it |
| `SIM_MESH_CLOCK_PROFILE` | optional, from node.yaml's `env`, or from simd's `--clock-ppm` (a crystal off by a draw within that many parts per million, per station), node.yaml's winning: node time as a function of T, `T:node,T:node,…` in microseconds, both increasing, slope 1 outside the points; absent, node time is T |

A station reads its identity from these and from nowhere else, so two
stations on one host never collide. **A station never learns where it
stands**: its position is the ether's and the loss table's alone.

**What a station may count on.** sim-mesh runs everything in its own image,
the same on every machine: Ubuntu 24.04 of the host's own architecture. An
executable **may** count on its C library and C++ runtime (`libc`, `libm`,
`libstdc++`, `libgcc_s`), on the radio library node.yaml names, on the time
shim, and on the image's `python3`, CPython 3.12 with its standard library
(its driver runs in it, with `aiohttp` and `pyyaml` beside `sim_mesh`).
Anything else it loads **must** be in its zip: a shared library under `lib/`
with `env: {LD_LIBRARY_PATH: ./lib}`, a Python package under a directory it
puts on `PYTHONPATH`, built for CPython 3.12; a firmware written in Python
may use the image's interpreter and need bring none. A zip **must not**
carry a radio library.

### 4. The process

- **stdin and stdout are the console**: a pty, text, shown in the map's
  console window and appended to `log` in its directory, and handed to its
  driver line by line (`console_line`). The one binary thing that may cross
  it is a framed RPC frame, which sim-mesh takes out of the stream before
  anything else sees it (§6). A driver may ask for a pipe each way instead
  (`console_tty`).
- **Exit to reboot.** sim-mesh starts the executable again, on the same
  directory and address, half a second later (of T, in a virtual-time run).
- **State is its directory.** A new simulation starts it with an empty
  `state/`, or with one a snapshot kept; a factory reset empties it; a reset
  leaves it.
- **Ports are its own**, on its own address. A web UI is on the port its
  driver names (`web_port`), reached through sim-mesh's proxy as
  `<node>.<simulation>.sim.localhost:8800`.
- **It may be several processes.** A process it starts that waits on time
  joins a virtual-time run as a station of its own, under the id its driver
  names for it (`sids`), with the same address, the time shim and
  `SIM_MESH_IDLE=threads`: it opens its link (`simradio_station_open`) and,
  with no radio, no chip. Which of them reads the console is the driver's to
  name (`console_sid`).

### 5. Time

In a real-time run a station keeps the host's time. In a virtual-time run the
ether owns conductor time T and moves it only when every station is idle
(§8), and a station keeps four promises:

- **It reads time only through the C library or the radio library.** The
  time shim answers `clock_gettime`, `gettimeofday`, `time`, the sleeps,
  `setitimer` and the timeouts of `poll`, `select`, `epoll_wait`,
  `pthread_cond_timedwait` and `sem_timedwait`/`sem_clockwait` in node time.
  A raw `rdtsc`, a `clock_gettime` made by system call, or a wait none of
  those is does not move with the run.
- **It opens its link early**, before anything in it waits on time:
  `simradio_station_open` is where its clock starts and the shim attaches;
  until the ether's welcome the monotonic clocks read 0 and the wall clocks
  the run's epoch.
- **It says when it is idle, and until when.** Idle is every thread blocked;
  the `until` it reports is the earliest wake anything in it holds
  (`simradio_wake_at`). Either the host calls `simradio_idle()` itself, a
  scheduler's idle with a wake at its next due tick and timer, or its driver
  sets `SIM_MESH_IDLE=threads` and the shim keeps a census of its threads. A
  station that says neither is reported idle by the radio library's
  watchdog, 20 ms of wall time after every message once none of its threads
  is on the CPU: it runs, but crawls.
- **It reads its console and talks TCP to other stations through the C
  library**: `read` on descriptor 0, and `read`/`recv`/`send`/`write` on its
  sockets. The shim counts those bytes for the ether, which holds T until a
  station has read what it was sent. Input taken another way holds T a
  second of wall time each time.

In return, nothing the ether says reaches the host piecemeal. The radio
library applies a datagram whole, the chip's own timers running as T moves,
before the host is told of any of it: the host's waits that fall due,
`simradio_on_advance` and DIO1 come after, so a thread woken at T finds all
of T.

### 6. The driver

The driver is a Python module in the zip, imported by sim-mesh from the
installed firmware's directory under a module name of its own. It **must**
define `DRIVER`, a subclass of its category's driver class (§7), which
subclasses `sim_mesh.driver.Driver`. It **must** import from sim-mesh only
`sim_mesh.driver` and its category's module; anything else in sim-mesh may
change under it. sim-mesh makes one `DRIVER(firmware)` per installed
firmware a run uses, shared by its stations.

**What a driver is given.** `self.firmware`: `dir`, `exec`, `fixed`, `env`
(paths made absolute), `driver`, `name`/`firmware`, `base`, `arch`,
`version`, `category`, `radio`, `title`, `hardware`. Every call names a
`station`, which offers:

| | |
|---|---|
| `name`, `node_id` | the node's name and its id |
| `dir`, `addr`, `ether_addr` | its directory, loopback address, the ether |
| `board` | `SIM_MESH_BOARD`'s JSON, or None |
| `log_path` | the file its console output is appended to |
| `status` | `stopped`, `starting`, `setup`, `up`, `restarting` |
| `starts` | how many times its process has been started |
| `virtual` | True in a virtual-time run |
| `rpc` | framed RPC on its console, or None before its process starts |
| `await sleep(s)` | a wait on the run's clock (T in virtual time) |
| `await restart()` | its process stopped, for the supervisor to start again |
| `report(event, **fields)` | an event into the run's `events.jsonl` at the run's T |

And from `sim_mesh.driver`: `CommandError` (anything that could not be done;
its text is shown as it is), `run_tool(argv, timeout, env=None)` (a helper
program to completion, `env` added to sim-mesh's environment),
`chip_dbm(board, connector_dbm)` (the chip power that puts that much at the
connector through the board's front end), `parse_setting`, `UP`, and the
framed RPC constants.

**What a driver implements.**

| Method | |
|---|---|
| `configured(station)` | **required**: True when the station's directory shows it has been set up. sim-mesh samples it at the fork; a station not set up is set up once it is up |
| `async wait_up(station, timeout)` | **required**: True once the station has booted and every service is up; False after `timeout` seconds of the run's clock |
| `argv(station)` | the command line; `[exec]` |
| `env(station)` | what it adds to the environment; nothing |
| `sids(station)`, `console_sid(station)` | the ether ids of its processes, and of the one reading the console; the node's own |
| `async run(station, line)` | a line in the firmware's own language (a script's `exec`, **Run command**); what it said back |
| `async flush(station)` | make what it was told durable, or apply it; asked after setup, before a stop, a reset or a snapshot |
| `web_port()` | its web UI's port; None |
| `console_line(station, line)` | a callback: each line the station prints, in order |
| `role_volatile` | True for a firmware that forgets its role at a restart: the role its first-boot rules gave it is said again at every boot |
| `console_tty` | False for a firmware whose console need not be a terminal: it gets a pipe each way instead of a pty, and its output must reach the pipe line by line; True |
| `console_acted_on` | False for a firmware whose console is log lines alone, nothing sim-mesh acts on (no framed RPC): its console is read as it comes rather than before T moves on, which a large run is much faster for. `console_line` is called either way; True |

Its helpers: `await self.pause(station, s)`, a wait on the run's clock;
`await self.joined(station, timeout)`, True once the station has joined the
ether, at the T of its hello (at once in a real-time run), so what the
driver does next it does at that T; `with self.tool_turn(station):` around a
tool run against the station's host door, during which T stands while the
tool has the floor and runs while the station works on what it read;
`await self.rpc_ready(station, timeout, marker_wait_s)` and
`await self.rpc_query(station, line, timeout=None)`, framed RPC.

**Framed RPC on the console.**

```
station → sim-mesh   "… framed rpc v1"                        once, early in boot, as log text
sim-mesh → station   F5 53 47 01 <id> <len:2> <a command line>
station → sim-mesh   F5 53 47 01 <id> <len:2> <what it printed>
```

A firmware **may** multiplex a framed side channel onto its console: a frame
is never echoed and never enters its line editor, and sim-mesh takes each
reply frame out of the stream, so the log and the console window never see
one. One frame is in flight per station; a command line is at most 4096
bytes; the id is a hash of the command, so a retry carries the one it had
([Framed RPC on the console](#framed-rpc-on-the-console)).

### 7. The `reticulum` category

A firmware of category `reticulum` is a Reticulum node. Its `DRIVER`
subclasses `sim_mesh.reticulum.driver.ReticulumDriver` and implements these
verbs, each taking the station first: every firmware's (`name`, `radio`,
`radio_up`, `tx_power`, `diagnostics`, from `sim_mesh.driver.Driver`), then
the category's. A verb it cannot do raises `CommandError`
(`self.cannot(verb)`), which the default does. A script reaches the
category's as `<selection>.reticulum.<verb>`, and they are nothing to a
station of another category.

| Verb | Means | Returns |
|---|---|---|
| `name(name)` | the node's name | |
| `role(role)` | `transport` (forwards others' traffic) or `client` | |
| `radio(freq_mhz=, sf=, bw_khz=, cr=, tx_dbm=, sync=, preamble=)` | slot 0's LoRa settings, only those given; `tx_dbm` at the antenna connector | |
| `radio_up()` | the radio started, for a firmware whose radio waits for it; nothing otherwise (default) | |
| `tx_power(dbm)` | transmit power at the connector | |
| `path(dest=None, iface=None)` | | its path table, `[{dest, next_hop, iface, hops}]`: the entries for `dest` (32 hex digits) and on `iface` (as the firmware names it) when given, all without |
| `peer_tcp(addr, port)` | a TCP link to another station | |
| `current_role()` | | `transport`, `client`, or None (default) |
| `diagnostics()` | | {label: text}, what a traffic run keeps of it; {} (default) |
| `lxmf.create(name)` | one more LXMF identity, `name` its display name, unless the station has one by that name. **Must not** refuse: a firmware with one identity, named after the node from its start, gives it `name`, and once it has another name does nothing | its delivery address, or None while it has none |
| `lxmf.identities()` | | `[(name, delivery address)]`, the one it sends from unless told otherwise first; `[]` while it has none |
| `lxmf.announce(name=None)` | an announce of that identity's delivery destination (None: the first) | |
| `lxmf.send(dest, text, mid, sender=None)` | an LXMF message to `dest` (32 hex digits) from its identity `sender` (None: the first); `mid` is sim-mesh's id for it | |

A verb with a dot is the method with an underscore: `lxmf.send` is
`lxmf_send`.

**Events.** For every message `lxmf.send` was given, the driver **must**
report how it ended, under `mid`, from `console_line` or however else it
learns it:

```
self.lxmf_status(station, mid, "delivered")
self.lxmf_status(station, mid, "failed", why=<text>)
```

and **may** report the statuses between (`pending`, `sent`); the last one
reported is what a traffic report shows for a message that never arrived.
Each is the event `lxmf.message.status` with `mid`, `status` and `why`. A
firmware's own id for a message is coupled to `mid` with
`self.lxmf_couple(station, mid, its_id)` once the driver learns it, and what
the station says under its own id is given to
`self.lxmf_native(station, its_id, status, why)`, which reports it under
`mid`, holding it until the coupling is made; what goes wrong before the
firmware has an id is reported under `mid` directly. sim-mesh writes each
event as a line of the run's `events.jsonl`:
`{"t": <T in µs>, "node": …, "event": …, …fields}`.

### 8. The ether's protocol

The ether is the medium between stations: it decides who hears each frame,
from the run's loss tables, and in a virtual-time run it owns time. A
station speaks to it over UDP, one JSON object per datagram, from a socket
bound to its own address; payloads are base64, times in microseconds. The
virtual radio (§9) speaks it for a firmware; a firmware **may** speak it
itself ([`ether/README.md`](ether/README.md) has the whole of it).

A real-time run:

```
station → ether   hello {sid, slots}
ether → station   welcome {t, mode: "real", rate: 1, epoch, seed}
station → ether   state {slot, mode, mod, freq, bw, sf, cr, sync, hdr, crc, pre}   on every change
station → ether   tx {slot, id, t0, t_pre, t_hdr, t_end, power_dbm, mod, freq, bw, sf, …, payload}
ether → station   rx_begin {slot, id, t0, t_pre, t_hdr, t_end, level[, cad]}    each receiver it reaches
ether → station   rx_end {slot, id, verdict, payload, rssi, snr}               at the frame's end
```

A virtual-time run is the same conversation with the ether as conductor:

```
station → ether   hello {sid, slots}
ether → station   welcome {t, mode: "virtual", rate, epoch, seed, seq: 1}
station → ether   idle {seq: 1, until: 25000}            nothing to do before T 25 000
ether → station   run {t: 25000, seq: 2}                 every station idle; T moves to 25 000
station → ether   state {…}  tx {t0: 25000, …}           what it did at 25 000
station → ether   idle {seq: 2, until: 30000}
ether → station   rx_begin {t: 25000, seq: 7, …}         to each receiver, at the same T
receiver → ether  idle {seq: 7, until: …}
```

| Station → ether | Says |
|---|---|
| `hello` | this station exists, and which radio slots it has; `"lines": 1`, it takes several messages to a datagram: in a virtual-time run it is sent every message the barrier has for it at one go |
| `state` | a slot's mode (`RX`, `TX`, `CAD`, `STDBY_RC`, `SLEEP` …), its modulation `mod` and its carrier: the ether matches receivers on these |
| `tx` | a transmission: its modulation and carrier, its power at the connector, its instants (start, end of preamble, of header, end), its payload |
| `idle` | virtual time: done with everything message `seq` gave it; next needs to run at T `until` (null: not on its own) |
| `read`, `wrote`, `listen` | virtual time: bytes taken from its console or from another station over TCP, about to be written to one (with `go`: waits for the ether's `go`), and a TCP endpoint it listens on; the time shim sends these |

| Ether → station | Says |
|---|---|
| `welcome` | joined: `t`, `mode` (`real` or `virtual`), `rate`, `epoch`, `seed` |
| `rx_begin` | a frame is arriving: its instants and its level; `cad: true` when it is energy to this station, not a frame to demodulate |
| `rx_end` | that frame is over: the verdict (`clean`, `crc`), the payload, RSSI and SNR |
| `run` | virtual time: T has reached what this station asked for, or it has input to work on |
| `go` | virtual time: a TCP write asked for may go ahead |

- **`mod`** is the modulation; a receiver hears only a frame in its own. The
  ether models `lora` (with `bw`, `sf`, `cr`, `sync`, `hdr`, `crc`, `pre`);
  a `state` or `tx` naming another is refused. It does not model preamble
  length.
- The `id` in a `tx` is the transmitter's own count; in `rx_begin` and
  `rx_end` it is the ether's, unique across stations.
- **Virtual time.** Every message the ether sends carries `t` and `seq`; the
  station applies it at that T and answers with an `idle` for that `seq`,
  and messages are applied strictly in `seq` order, never twice. T moves
  only when every station is idle, to the earliest of their `until`s and the
  air's next instant. An idle is said again every 250 ms of wall time until
  something comes back; the ether answers an idle for an older `seq` said
  twice by sending what came after it again. A station that says `hello`
  again has restarted.
- Unknown message types are ignored, on both sides.

### 9. The virtual radio

A virtual radio is a model of one radio chip on a virtual SPI bus, and the
station's link to the ether, as a shared library sim-mesh provides:
`libsimradio-sx1262.so` is the SX1262, `libsimradio-lr2021.so` the LR2021
(LoRa only). A firmware is compiled against its
header, [`radio/include/simradio.h`](radio/include/simradio.h), and linked
with it **by name** (`-lsimradio-sx1262`), and **must not** carry it:
sim-mesh puts its own on the station's `LD_LIBRARY_PATH`, so a firmware
keeps working when the model or the ether's protocol changes, and only a
change to `simradio.h` itself needs it rebuilt. It makes the simulation and
the hardware do the same thing: the firmware's own driver talks SPI to it
frame by frame, exactly as to the chip on a board.

```c
int  simradio_station_open(int sid, const char *bind_addr, const char *ether_addr);  /* once, early */
simradio_t *simradio_open(int slot, void (*on_pin)(void *ctx, int pin, int level), void *ctx);
void simradio_transfer(simradio_t *, const uint8_t *out, size_t len, uint8_t *in);    /* one NSS cycle */
void simradio_reset(simradio_t *);                                                    /* RST's rising edge */
int  simradio_pin(simradio_t *, int pin);                    /* SIMRADIO_PIN_DIO1, SIMRADIO_PIN_BUSY */
void simradio_set_services(const struct simradio_services *); /* a host's own, before anything else */

int     simradio_virtual(void);          int64_t simradio_node_us(void);
int     simradio_joined(void);           int64_t simradio_node_at_join(void);
int64_t simradio_epoch_us(void);         int64_t simradio_node_to_conductor(int64_t node_us);
int     simradio_wake_create(void (*due)(void *), void *arg);
void    simradio_wake_at(int wake, int64_t node_us);           /* INT64_MAX clears it */
void    simradio_idle(void);
void    simradio_on_advance(void (*moved)(void));
```

- **Open the link once, early**: `simradio_station_open(SIM_MESH_NODE_ID,
  SIM_MESH_BIND_ADDR, SIM_MESH_ETHER)`, before anything waits on time (§5);
  then `simradio_open(slot, …)` per radio.
- **One whole frame per NSS cycle.** A bus adapter hands the model
  everything the driver put on the bus between NSS going low and going high
  as one frame: writes inside a frame are appended, never passed on one by
  one; a frame containing a read is complete at the read; the adapter never
  adds a NOP of its own. A reassembly one byte off makes `GetIrqStatus` read
  the status byte as the IRQ word's high byte, so every flag looks set.
- **Interrupts.** `on_pin` runs on whatever thread moved the line. A pin
  shim whose interrupt is level-triggered **must** fire at once when the
  interrupt is enabled while the line is asserted: a driver that disables
  its interrupt, drains, and re-enables relies on a line still high
  re-firing.
- **The host's services.** The library needs a clock, one-shot timers, a
  recursive lock, a UDP socket and a thread to read it, and a log
  (`struct simradio_services` in the header). Its own are a plain process's
  threads; a host with a scheduler of its own (FreeRTOS on ESP-IDF's Linux
  target) **must** hand it its own with `simradio_set_services` before any
  other call (a constructor is the place), so model callbacks run as that
  host's tasks.
- **The front end** is the library's: what the chip radiates goes through
  `SIM_MESH_BOARD`'s transmit curve before the ether is told its power, and
  every level the chip reads is the front end's receive gain above the
  connector's. A firmware that drives a front end converts with the same
  figures.

### 10. Building against it

Start sim-mesh once first (`sim`, which builds its radio and leaves
`radio/build/libsimradio-sx1262.so`), then build the station for Linux:

```sh
# C or C++
cc station.c -I sim-mesh/radio/include -L sim-mesh/radio/build -lsimradio-sx1262 -lpthread -o station

# Rust: link it by name from a build script
println!("cargo:rustc-link-search=native={}/build", radio_dir);
println!("cargo:rustc-link-lib=dylib=simradio-sx1262");

# PlatformIO on Portduino: sim-mesh's radio/portduino library, in lib_deps as
#   symlink://<path to sim-mesh>/radio/portduino
# links the radio by name and stands in for spidev and libgpiod
```

Then make the zip: the executable, the driver, anything else it runs, the
shared libraries beyond the C library and C++ runtime under `lib/`, and
`node.yaml`. Check what the executable loads with `ldd`: everything but
`libc`, `libm`, `libstdc++`, `libgcc_s`, the loader and `libsimradio-*`
belongs in `lib/`. Then `sim firmware add` it, and run a script with it. A
firmware that publishes its zips puts them on the pre-built list with
`tools/deploy-firmware` ([Publishing firmware](#publishing-firmware)).

### 11. A whole example

`node.yaml`:

```yaml
base: relay-sx1262
arch: aarch64
version: '20261001120000'
category: reticulum
radio: sx1262
title: Relay (dev)
hardware: ESP32-S3
exec: relay
driver: driver.py
fixed: fixed
env:
  LD_LIBRARY_PATH: ./lib
```

`driver.py`, for a firmware that answers framed RPC on its console:

```python
import os
import re

from sim_mesh.reticulum.driver import ReticulumDriver

QUEUED = re.compile(r"queued (\S+)")
MID = re.compile(r"mid=(\S+)")


class Relay(ReticulumDriver):
    def configured(self, station):
        return os.path.exists(os.path.join(station.dir, "state", "boot"))

    async def wait_up(self, station, timeout):
        return await self.rpc_ready(station, timeout, 20.0)

    async def run(self, station, line, timeout=None):
        return await self.rpc_query(station, line, timeout)

    async def name(self, station, name):
        await self.run(station, "hostname %s" % name)

    async def lxmf_create(self, station, name):
        return None                 # one identity, there from its start

    async def lxmf_identities(self, station):
        address = (await self.run(station, "address")).strip()
        return [(station.name, address)] if address else []

    async def lxmf_announce(self, station, name=None):
        await self.run(station, "announce")

    async def lxmf_send(self, station, dest, text, mid, sender=None):
        found = QUEUED.search(await self.run(station, "send %s %s" % (dest, text)))
        if not found:
            self.lxmf_status(station, mid, "failed", "not queued")
            return
        self.lxmf_couple(station, mid, found.group(1))

    def console_line(self, station, line):
        found = MID.search(line)
        if found and "delivered" in line:
            self.lxmf_native(station, found.group(1), "delivered")
        elif found and "failed" in line:
            self.lxmf_native(station, found.group(1), "failed", line)


DRIVER = Relay
```

## After a restart

Stations are processes, not a service: stopping `sim` stops them. Their
state is not in the process though — `runs/`, `snapshots/` and `firmware/`
are all in the directory sim-mesh was cloned into, which the image mounts
from the host. A simulation's stations keep their state in its
run directory, but a new simulation starts factory-fresh; to carry a network
across a restart, save a snapshot before stopping, then after `sim` load
it into a new simulation (⋯ ▸ Load snapshot into it…), and it comes back with its names, radio
settings, identities and message history as they were.

## The pieces on their own

The medium is its own program with its own docs:
[`ether/README.md`](ether/README.md) for what it does and the wire it
speaks, [`ether/INTERNALS.md`](ether/INTERNALS.md) for how. It runs alone
against hand-written stations, taking its nodes from a nodeset and its
losses from a directory of tables:

```sh
python3 ether/ether.py --bind 127.0.0.1:7000 --record record.tsv \
        --geodata testbed/geodata/<geodata>.yaml --nodeset testbed/nodesets/<nodeset>.yaml \
        --losses <dir holding 868.bin>
```

The proxy also runs on its own, for a station set started some other way; alone
it routes by station id only, since names are the nodeset's:

```sh
python3 testbed/proxy.py --bind 0.0.0.0:8800
```

## The virtual radios

`radio/` is the virtual SX1262 and the station's link to the ether, the
shared library `libsimradio-sx1262.so` behind a C ABI (application binary
interface, `radio/include/simradio.h`): open the link, open a chip per radio
slot, hand it SPI frames, pulse its reset, read its lines, and, in a
virtual-time run, read node time, set wakes, learn every move of T and say
the station is idle. A station of any language links it by name in place of
a radio, below an unchanged driver, and sim-mesh provides it when it starts
the station. The radio is one way of speaking the ether's protocol, which a
firmware may also speak itself; another radio is another library, named
after its chip, as the LR2021's is. Beside it,
`radio/shim/simclock.c` builds `libsimclock.so`, the preloaded library that
answers the C library's clocks and waits in node time, draws the station's
randomness from the run's seed, counts the console and TCP bytes the ether
makes into instants of T, and keeps the busy watchdog off a station that is
computing ([INTERNALS.md](INTERNALS.md#time)).

The model reaches its host through services — a clock, one-shot timers, a
recursive lock, a UDP socket, a reader, a log. The library's own are a plain
process's (`radio/backend/posix/`: `std::thread`, `CLOCK_MONOTONIC`, a
`std::recursive_mutex`); a host with a scheduler of its own (FreeRTOS on
ESP-IDF's Linux host target) hands it its own with `simradio_set_services`
before anything else, from its own code.

`sim` builds them as it starts (`radio/build/`: `libsimradio-sx1262.so`,
`libsimradio-lr2021.so`, `libsimclock.so`); a station is given the radio from there, and a virtual
run preloads the shim from there.

## Working on the page

The page is built when `sim` starts, so an edit to it shows only after a
restart. `sim dev` instead runs the page's development server (`quasar dev`)
inside, beside the front, and the front passes the page through from it on
the same port, 8800, live-reload included: each edit is in the browser at
once. The front's own Python is read when it starts, so a change to it still
wants a restart. In a container the server looks for edits itself, since
edits made outside it arrive as no file events.

## Tests

None needs firmware, a planner or a network:

```sh
cd sim-mesh/testbed && python3 -m pytest -q      # the stores, firmware, the front, simd, the drivers, the library, the tools
cd sim-mesh/ether   && python3 -m pytest -q      # the medium and both conductors, over real UDP and in-process
cd sim-mesh/radio   && python3 -m pytest -q tests  # the chip model, the conductor, the time shim
cd sim-mesh/testbed/ui && npx vue-tsc --noEmit && npx quasar build
```

The testbed's tests run stand-in firmware (`testbed/stub_firmware.py`): a
shell script for a station and a driver that writes down what it is asked.
The models' tests load `libsimradio-sx1262.so` and `libsimradio-lr2021.so`
with ctypes, drive them frame by frame the way a driver does, and play the ether on a UDP socket of their
own; the conductor's tests do the same in virtual time, and the shim's run a
small C stand-in station (`radio/tests/standin.c`), linked with the radio by
name as a firmware is, under `libsimclock.so`. The
testbed's pack tests run against a real `planner-web` when it is built in
`planner/` and there is `berlin-city` geodata, and are skipped otherwise.
The builds from sources and the node-map imports are tested against a host and
a `planner-job` of the tests' own; the planner's tests of real data read the
download cache, `testbed/geodata/.cache/`, and skip what is not there, and two real builds
of a few square kilometres of Berlin run through the binary with

```sh
cd sim-mesh/planner && cargo test --release -p planner-job -- --ignored
```

`SIM_MESH_MESHCORE_SNAPSHOT` names a saved MeshCore node list for the importer's
tests, in place of `testbed/geodata/.cache/meshcore/nodes.json`.

## Where the code lives

| Where | What |
|---|---|
| `sim` | the one command: the front natively or in sim-mesh's image (building what is stale first), `new`, `stop`, `pause`, `resume`, `list`, `plan`, `run`, `firmware` |
| `Dockerfile` | sim-mesh's image, which everything runs in: the system a firmware may count on |
| `tools/deploy-firmware` | pre-built firmware onto the `firmware` release, and the site redeployed |
| `radio/` | the virtual SX1262 and the station's UDP link to the ether, as a shared library |
| `radio/src/conductor.cpp` | the station's side of virtual time: T, node time, wakes, the idle |
| `radio/shim/simclock.c` | `libsimclock.so`, the C library's time in node time (its sleeps, descriptor waits, condition and semaphore waits), the seeded randomness, the console and TCP counts, the listening sockets and the watchdog's hold; `radio/include/simclock.h` is what it is handed |
| [`radio/portduino/`](radio/portduino/README.md) | the virtual radio for a Portduino firmware: a PlatformIO library standing in for spidev and libgpiod, and the firmware's idle wait |
| [`ether/`](ether/README.md) | the medium: the loss tables, who hears a frame and how it comes out; `slt.py` reads and writes a table |
| `testbed/front.py` | several simulations behind one port: the registry, the editors' verbs, the imports, the planner sidecars, the loss tables before a start, one simd per simulation, script runs, coverage, station hostnames by simulation, the WebRTC relay one level up, the finish estimate |
| `testbed/simd.py` | one simulation: the ether, the stations and their setup, the proxy, the control server, commands and verbs on chosen stations, a moved node's row |
| `testbed/simctl.py` | behind `sim new`, `stop`, `pause`, `resume`, `list` and `plan`: the front from a shell; starts the front when none answers |
| `testbed/store.py` | where geodata, nodesets, scripts, tables, coverage, runs and snapshots live, and what a name may be |
| `testbed/firmware.py` | installed firmware: names and `_latest`, adding a zip, what holds one, deleting, the pre-built index; `sim firmware` |
| `testbed/drivers.py` | a run's firmware resolved, each one's driver imported from its own directory, and the contract's environment |
| `testbed/antennas.py`, `testbed/antennas/` | the antenna catalogue and pictures, a pattern's gain by direction, a pair's gain in three dimensions |
| `testbed/geodata.py` | geodata: packs and synthetic ground, the projections, the extent, a sim-mesh geodata pack's export and import |
| `testbed/sources.py` | a build's sources: what a rectangle needs of each, the download cache and its fetches |
| `testbed/packbuild.py` | one pack built from its sources: fetch, `planner-job pack-build`, the pack into place |
| `testbed/nodeset.py` | nodesets: nodes, their maximum powers, antennas and tags (a role tag, `no-radio`), offsets, links, edits, the geometry hash, the merge of shown layers, the imports |
| `planner/` | the Rust workspace: `planner-web` (the sidecar), `planner-job` (a pack's build, a node map's import), `planner-pack` (the compiler, OpenStreetMap from a PBF extract), `planner-buildings`, `planner-import`, and the ground, propagation and coverage crates |
| `testbed/script.py` | scripts: listing, checking, loading, a script's inputs read without running it |
| `testbed/sim_mesh/library.py`, `testbed/sim_mesh/select.py` | the script library: `script_…`, `sim_…`, and `nodes()`/`node()` selections with what is done to them (`.firmware`, `.on_first_boot`, `.exec`, `.radio`, `.reticulum…`), `Node` for first-boot rules, and `scripts.log` |
| `testbed/losses.py` | a loss table, on synthetic ground or through the sidecar; the cache; links, shadowing, antennas and offsets as layers, the ground under each node; one node's row |
| `testbed/coverage.py` | a node's coverage raster on a pack, through the sidecar, cached |
| `testbed/runs.py` | a run directory, and snapshots taken from and loaded into one |
| `testbed/stations.py` | one firmware process, its pty, its log, its supervisor; the thread every station's pty is read on; its console's lines to its driver |
| `testbed/rpc.py` | framed RPC on a station's console pty: the demultiplexer in the drain, and the client a driver speaks |
| `testbed/proxy.py` | the hostname proxy |
| `testbed/webrtc.py` | the WebRTC relay: the signalling rewritten, and one UDP port in front of every station's DataChannel |
| `testbed/ui/` | the page (Quasar 2 on Vue 3; Pinia stores `catalog`, `geodata`, `nodes`, `sim`, `coverage`, `display`, `socket`); `vendor/planner-wasm` is the planner's built planner-wasm, copied in by `vendor/update-planner-wasm.mjs` so the page builds with no planner beside it |
| `testbed/seq.py`, `compare.py`, `airtime.py`, `links.py`, `delivery.py`, `compliance.py`, `referee.py` | the analysis tools ([Reading a run](#reading-a-run)) |
| `testbed/sim_mesh/` | the library: `library` (what a script says, synchronously), `select` (`nodes()`), `driver` (what a firmware's driver is, and what sim-mesh hands it), `traffic` (the LXMF traffic driver), `sim` (the async hold on a simulation the library runs on), `runner` (a script run, its simulation started, its report), `view` (a run opened for analysis), `record`; `sim_mesh/reticulum/` holds Reticulum's parts: the category's driver interface, frame reading (Reticulum packets, SUPE), delivery analysis |
| `testbed/boards.py` | the one board, an SX1262 with a GC1109 front end above 22 dBm; a node's maximum power; what a station is told of it |
| `testbed/scripts/` | the scripts: `realtime.py`, `lxmf-traffic.py`; `startup.py`, which every script includes; `globals.py`, the settings they share and the page reads |
| `testbed/geodata/`, `testbed/nodesets/` | your geodata and nodesets (not committed) |
| `testbed/testdata/` | what the tests stand on: the four stations `four.yaml` on the synthetic ground `plain-27.yaml` |

Nothing above the bus is aware of any of it: a firmware's LoRa driver, its
channel access and airtime accounting, its protocol stack and its web UI are
the same code that runs on a board, over a bus that ends in the virtual
radio.

## License

sim-mesh is released under the Apache License, Version 2.0; see [LICENSE](LICENSE).
