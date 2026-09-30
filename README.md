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
| a **device** | one station build: its executable, its `/fixed` tree, its tools | `devices/latest/`, `devices/saved/`, `devices/local/<project>_<catalogue>.yaml` |
| an **antenna** | a kind of antenna and its radiation pattern | `testbed/antennas/` |
| **geodata** | the ground: a pack built from public sources, or synthetic ground at 0°, 0° | `testbed/geodata/<name>/`: `geodata.yaml`, and the pack's files beside it |
| a **nodeset** | which nodes stand where with what maximum power and antenna, their tags, the offsets and any stated links; its own setup script beside it | `testbed/nodesets/<name>.yaml`, `<name>.py` |
| a **loss table** | every ordered pair's path loss, derived from geodata and a nodeset | `testbed/losses/…`, a cache |
| a **script** | plain Python against the sim-mesh library, top to end: the time, what each node runs and is given at first boot, and what is done | `testbed/scripts/<name>.py` |
| a **run** | one simulation's output: its record, logs and state | `testbed/runs/<name>/` |
| a **snapshot** | a moment of a run, state and all, to start another from | `testbed/snapshots/<name>/` |

| Piece | Where |
|---|---|
| the launcher | `sim-mesh`, and its image, `Dockerfile` |
| the medium | [`ether/`](ether/README.md) |
| the chip model, as a C library a station links | `radio/` |
| the front, simd, the stores, the library, the page, the analysis tools | `testbed/` |
| what a station process is promised and owes | [STATION.md](STATION.md) |
| the device file, a prebuilt station | [NODE.md](NODE.md) |
| the loss table's file format | [LOSSTABLE.md](LOSSTABLE.md) |

It is the firmware under test, built for a different target — not an emulator.
[INTERNALS.md](INTERNALS.md) says how it works and why it is built this way.

## Getting started

sim-mesh needs no firmware tree: it runs prebuilt stations it fetches itself.

On **Linux** it runs natively, and needs `python3` with `aiohttp` and
`pyyaml` (Debian and Ubuntu: `python3-aiohttp python3-yaml`), `node` and
`npm` for the page, and `cmake` with a C and C++ compiler for the chip
library; `cargo` too for real ground (below), and Reticulum and LXMF
(`pip install rns lxmf`) for `standard_reticulum` stations. On a fresh
Debian, Ubuntu or Fedora, `sim-mesh install` puts all of it in place, as
sim-mesh's image holds it (step 2). **Anywhere else** it needs
only `docker`: `sim-mesh` builds its own small image on first use (a few
minutes, once) and runs itself inside it, with port 8800 published; `podman`
does as well where there is no `docker` (`SIM_MESH_RUNTIME` chooses).

**1. Clone it.** The directory you clone into is the one `sim-mesh` mounts
into its image, so a workspace put beside sim-mesh later is where a local
device expects it:

```sh
mkdir mesh && cd mesh
git clone https://github.com/sim-mesh/sim-mesh.git
```

**2. Build** the page, the chip library, the ether's conductor and the
planner (sim-mesh's own, in `planner/`; without cargo the last two are left
out, sim-mesh says so, synthetic ground works, and a virtual-time run is
refused until the conductor is built; real time needs none):

```sh
sim-mesh/sim-mesh build
```

On a fresh Linux, `sim-mesh/sim-mesh install` does this step with what it needs
first: the system's packages through apt or dnf (with sudo); a Node the
page's build takes (22.22 or later) where the system's is older, NodeSource's
22 on Debian and Ubuntu, which ship 18, and on Fedora, whose default is 22.21,
its own nodejs24; Rust through rustup; and Reticulum and LXMF in a Python
environment beside the clone, `sim-mesh/.venv`, which `sim-mesh` puts first on
the path for itself and every station it starts (Fedora's Node 24 is given
its plain names there). A step whose result is there already is left out;
`--dry-run` says what it would do. It is tried from a fresh clone on Debian
12, Ubuntu 24.04 and Fedora 41, each passing every suite after it.

**3. Start it:**

```sh
sim-mesh/sim-mesh
```

It builds whichever of the page, the chip library and the planner is not
built yet or is older than a file of its sources (hidden directories and
`__pycache__` aside), so a fresh clone or a pull may skip step 2, then
starts the front and opens
`http://localhost:8800/`. The terminal is the
testbed's: Ctrl-C there stops every simulation and everything they started.

**4. Stations.** A station is a device file: the firmware built for Linux
and a `node.yaml` saying how to run it, published in a build catalogue beside
the board images. On start the front looks through the web's catalogues and
every catalogue in a `builds/` directory beside sim-mesh for the newest build
of each project for this machine's architecture (`aarch64` or `x86_64`); the
Firmware tab lists them as `reticulous_dev_latest` and so on, and one is
fetched when a simulation first uses it.

**5. A first simulation.** No geodata and no nodesets come with sim-mesh:
both are your own, kept in `testbed/geodata/` and `testbed/nodesets/` and
never committed. Make ground on the **Geodata** tab, **Build from
sources…** over a rectangle of the map or **New synthetic…**, and click
it to choose it. On the **Nodes** tab place nodes on it, or **Import…**
them in the Layers panel from a public node map, and **Save nodes as
nodeset…**; a nodeset is offered on any geodata that holds one of its
nodes, and is chosen in the Layers panel. On the **Scripts** tab
click `lxmf-traffic` and **Run…** it: a new simulation of that nodeset on
that ground, the script saying what the nodes run, `firmware("all",
"reticulous_dev_latest")`, which is fetched now. The page goes over to the
simulation's live map, framed on its nodes, and its stations come up over
the next minute; rings on the map are frames on the air. For a simulation
to work with by hand, run `realtime`: every node on the dev build, on the
wall clock, set up by the startup script and left running.

**While `dev` has no `hw-sim-mesh` build** for your architecture, the
simulation says it cannot fetch one and its stations run nothing. Change the
script's `firmware()` line to a build the Firmware tab lists (or one of your
own, [The developer loop](#the-developer-loop-with-a-workspace)) and start
again.

**6. Play with it.**

- **Click a station** for the editor: how it is doing, its **Console** (its
  serial console) and **Web UI** (its own web interface, as a board serves
  it, with no login: a Reticulous station is built without credentials),
  each in a window over the map with **−** and **+** for its zoom (the
  console's font; the web UI's page, as the browser's zoom, 75 % to start),
  and every setting it has.
- **Right-click the map ▸ New node here** puts down a station. It comes up
  and is set up on its own.
- **Drag a station** and its links change once its row of the loss table is
  recomputed.
- **Right-click a station ▸ Run command…** types one line at the selected
  stations: `lora 0 a` makes them announce, `rnpath -s` says what paths each
  knows.
- **⋯ ▸ Save snapshot as…** on the simulation's row of the Simulations tab
  keeps the network and everything its stations have become, and **⋯ ▸ Load
  snapshot into it…** brings one back.

What to read next: [Firmware](#firmware), [Antennas](#antennas),
[Geodata](#geodata), [Nodesets](#nodesets) and [Scripts](#scripts) for
making your own network,
[The page](#the-page) for doing it on the map, [Running
simulations](#running-simulations) for the time modes.

## Station kinds

Stations of different firmwares share one ether and one map. Each firmware is
a **kind**: how the testbed talks to it (`testbed/kinds/`). A device's
`node.yaml` names its kind. Four exist:

| Kind | The firmware | Up when | A line is | Web UI |
|---|---|---|---|---|
| `reticulous` | Reticulous, built for `spangap/hw-linux` | it answers a framed RPC (remote procedure call) frame on its console (after printing the marker, or to one blind probe), and `s.sys.reset_reason` reads, which its boot writes once every service has initialised | a CLI (command line) command, one framed RPC frame over its console pty (pseudo-terminal) | port 80 |
| `sergeyculum` | "Sergeyculum", the Rust Reticulum stack at [git.emcomm.cc/berlinmesh/reticulum](https://git.emcomm.cc/berlinmesh/reticulum), as its `fw/sim-mesh` target | its `kiss` pty answers `rncfg detect` | `rncfg` without program and port: `name set {name}` runs `rncfg name <dir>/kiss set <name>` | none |
| `microreticulum` | attermann's [microReticulum_Firmware](https://github.com/attermann/microReticulum_Firmware), the RNode firmware with the microReticulum stack in it, as its Portduino Linux daemon built with `[env:sim-mesh]`: a LoRa transport node and nothing else | its log shows `RNS Transport is READY!` since its latest start | an edit to its `rnoded.conf`, `set <key> <value>`, `unset <key>` or `txp <dBm at the connector>`; a flush restarts it when a line changed the file | none |
| `standard_reticulum` | a standard Reticulum node: the RNode firmware with Reticulum's Python reference implementation and an LXMF router behind it, as rnsd runs on a computer with an RNode on its USB port (`stations/standard_reticulum/station.py`, over microReticulum_Firmware's Linux daemon built with `[env:sim-mesh-rnode]`, whose own stack is never started) | it answers a framed RPC frame, and its `status` says `state: up` | one of `station.py`'s commands, one framed RPC frame; a flush restarts it when a setting is pending | none |

Sergeyculum is a working name; the project calls itself `reticulum` and the
kind is named after its repository.

A kind reports each station's **role** in the mesh, read live from the
station: `transport`, `router` or `repeater` for one that carries others'
traffic, `client` for one that does not.

A kind also says **intents** in its own lines, so that a script means the
same thing on any firmware:

| Intent | `reticulous` | `sergeyculum` | `microreticulum` | `standard_reticulum` |
|---|---|---|---|---|
| `name` | `hostname {name}` | `name set {name}` | nothing to say | `set name {name}`, its LXMF display name |
| `role` | `set s.rnsd.transport_enabled 1` or `0` | `transport on` or `off` (kept in RAM only, so said again at every boot) | `transport` says nothing, since it always is one; `client` is refused | `set transport 1` or `0` |
| `radio` | `lora 0 freq`, `sf`, `bw`, `cr`, `txp`, `sync`, `preamble` for each figure given | `set --freq-hz --sf --bw-hz --cr --txpower-dbm` for the figures given; sync word and preamble are the firmware's own | `set lora_freq_hz`, `lora_bw_hz`, `lora_sf`, `lora_cr`, and `txp`, for the figures given; sync word (0x12) and preamble are the firmware's own | `set freq_hz`, `bw_hz`, `sf`, `cr`, and `txp`, for the figures given; sync word (0x12) and preamble are the RNode's own |
| `radio_up` | `lora up` | nothing to say: the radio runs from the start | nothing to say: the radio runs from the start | nothing to say: Reticulum brings its RNode up when it starts |
| `tx_power` | `lora 0 txp <dBm>` | `set --txpower-dbm <dBm>` | `txp <dBm>` | `txp <dBm>` |
| `announce` | `lora 0 a` | `announce now` | — | `announce` |
| `message` | `lxmf send <dest> <text>` | `send <dest> <text>` | — | `send <dest> <text>` |
| `path` | `rnpath -j <dest>` | — | — | `path <dest>` |
| `peer_tcp` | `tcp peer add <addr>:<port>` | — | — | — |

An intent a kind has no line for is refused, naming the kind and the verb. A
station's LXMF delivery address, which `message` and `path` need of the
other end, is its `lxmf` listing's on `reticulous`, the `lxmf.delivery`
line of `rncfg addr` on `sergeyculum` and of `addr` on `standard_reticulum`;
a `microreticulum` station has none.

A `microreticulum` station's `lora_txp` is the SX1262's own power, which the
chip model carries through the node's board to the connector, so its
intents say `txp <dBm>`: the power at the connector, which the kind turns
into the lowest chip setting that reaches it on the node's board
(`boards.chip_dbm`, the same arithmetic Reticulous's `rfCalChip` does), as a
board build of the firmware converts through its amplifier's table. On a
node whose maximum is above 22 dBm, which has the GC1109 front end, 14 dBm
at the connector is `lora_txp = 6`. It reads its radio figures from
`rnoded.conf` into its EEPROM image only when it has none, so a flush after
one of them changed removes `state/eeprom` first. Its first boot runs on the
firmware's default radio until setup is done and simd's flush restarts it,
and may put an announce on air there, on a channel no other node shares. A
Reticulous node in the same nodeset declares `sync: 0x12` to hear it.

A `standard_reticulum` station is two processes, each a station of the
ether's in a virtual-time run:

```
station.py ── starts ──► rnode          the RNode firmware, the node's own id: its radio
station.py ── RNodeInterface, KISS on tcp://<its address>:7633 ──► rnode
testbed ── framed RPC on the console ──► station.py     the node's id + 1000000, no radio
```

`station.py` is Reticulum (`rns`) and LXMF (`lxmf`) in a python3 that has
them, which sim-mesh's image does; natively, install both. The RNode is
attermann's firmware with its own stack compiled in but never started
(`STANDARD_RNODE`) and its EEPROM provisioned with no radio configuration,
so it stays out of TNC mode and Reticulum sets its radio over KISS, as it
does an RNode on USB. Its settings (`state/settings.json`: name, radio,
transport) are read when `station.py` starts, so a line that changes one
leaves it pending and the kind's flush restarts the station. Its `txp` is
the power at the connector, turned into the chip's as on `microreticulum`.
A send waits up to a minute for the recipient's identity, asking for a path,
then hands LXMF a DIRECT message; the station logs `lxmf: queued mid=…`
and then `lxmf: DIRECT delivered mid=…` (`DIRECT resource delivered`) or
`lxmf: failed mid=…`, which is what the traffic report counts. Its device
is the compiled build `standard-reticulum_local_latest`: the RNode as last
compiled with `pio run -e sim-mesh-rnode` in the clone under `competition/`
beside sim-mesh.

## Firmware

A device is one station build for one architecture
([NODE.md](NODE.md) is the file's spec): a zip holding its executable,
whatever else it needs, and a `node.yaml` saying what those are, what the
page calls it (`name`), what hardware it plays (`virtual_hardware`, shown
as "virtual ESP32-S3") and what radio chip it drives (`virtual_radio`, shown beneath as
"virtual SX1262"). Build catalogues publish them beside the board images as
`<project>_hw-sim-mesh-<arch>_<stamp>.zip`.

**A device is called** `<project>_<catalogue>_<stamp>`, one name for one
build (`reticulous_dev_20260927140352`), or `<project>_<catalogue>_latest`
for whichever is newest in that catalogue when it is used. The project is
the catalogue filename's, the catalogue where it was published. A script
names devices this way ([Scripts](#scripts)), and so do `sim-mesh new
--build` and `simd --build`, which also take a package directory or a
workspace's `build.linux`.

**Latest builds.** The front surveys the catalogues when it starts, fetching
nothing: the ones the web's index lists (`$SIM_MESH_CATALOGUES`, by default
`https://reticulous.net/builds/`), then every catalogue directory (one
holding an `index.html`) in `builds/` beside sim-mesh, which is where a
workspace's `make-builds` leaves them. A local catalogue joins the web's of
its name, the newer build winning, so a fresh local `dev` build is
`reticulous_dev_latest` and one in `builds/local` is `reticulous_local_latest`.
Each new build's `node.yaml` is read from its zip as it is surveyed (on the
web by HTTP range requests, a few kilobytes rather than the build), so the
Firmware tab says what it plays and its kind before it is fetched. A
`_latest` build is downloaded into `devices/latest/` when a simulation first
uses it, and removed as soon as a survey sees a newer one in its catalogue,
fetched or not; a simulation that uses it again then fetches the newer one.
The `builds/` directories are surveyed again whenever the Firmware tab lists,
and **Refresh** there does the web too.

**Saved builds** stay until deleted: **Save** on a latest build copies it
(fetching it first) into `devices/saved/<project>_<catalogue>_<stamp>/`.
**Import** on the Firmware tab, or `sim-mesh devices import`, takes a zip of
any name into the saved builds as `<project>_imported_<stamp>`, named from
its `node.yaml`; it must be built for this machine.

**Compiled builds** stand in for a project that publishes no sim-mesh build
yet: `devices/local/<project>_<catalogue>.yaml`, a `node.yaml` that is not
in an archive, its `elf`, `fixed` and `tools` paths relative to the file and
free to point anywhere, run in place from wherever it was last compiled, and
the latest of its catalogue. These come with sim-mesh:
`sergeyculum_local_latest`, Sergeyculum's `fw/sim-mesh` with its `rncfg` as
last compiled in `sergey/reticulum`; `microreticulum_local_latest` and
`microreticulum-jrl290_local_latest`, attermann's firmware and a stand-in
for jrl290's as last compiled under `competition/`; and
`standard-reticulum_local_latest`, `stations/standard_reticulum/station.py`
over the RNode from the same clone. Saved, one is gathered into a package
like any other.

From a shell:

```sh
sim-mesh devices refresh                          # survey the web's catalogues, then builds/
sim-mesh devices refresh ../builds/rop            # one catalogue: a name, a URL or a directory
sim-mesh devices list                             # the latest and the saved builds
sim-mesh devices fetch reticulous_dev_latest      # download it now
sim-mesh devices save reticulous_dev_latest       # keep the one there is now
sim-mesh devices delete reticulous_dev_20260927140352
sim-mesh devices import mine.zip                  # any device zip, saved
sim-mesh devices resolve reticulous_dev_latest    # what a name runs, as JSON
```

A device's executable is native code linked against its builder's C
library, C++ runtime, zlib and libbsd, so it runs on a machine of its
architecture with those present; sim-mesh's image is Ubuntu 24.04 of the
host's own architecture for that reason.

A build given to a simulation runs in place of every firmware of that
build's kind; a simulation records the firmware each node runs and where
each build was, and so does a snapshot. A run whose `_latest` build has since
been replaced fetches the newer one when it is resumed.

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
compiler and the node-map importer, run once per job. `sim-mesh build planner`
builds both with cargo (in sim-mesh's image on a machine that is not Linux).
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
change it with `max_tx_pwr(which)` or `{max_dbm}` in its lines. A station
is told its board at start (`SIM_MESH_BOARD` in its environment,
STATION.md).

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
"""A study: the dev build everywhere, Sergeyculum where tagged."""
from sim_mesh import *

time("max")                                               # declarations first
firmware("all", "reticulous_dev_latest")
firmware(nodes(tag="sergeyculum"), "sergeyculum_local_latest")
include("scripts/startup.py")                             # roles, radios, each nodeset's own
on_first_boot(nodes(kind="reticulous"), """
    lxmf create {name}
""")

up("all")                                                 # the first thing done starts it
exec(nodes(tag="transport") & nodes(kind="reticulous"), "lora 0 a", pause=5)
wait(600)
send_msg("gw02", "internet", "hello")
pause()
```

**Declarations** come first and are collected until the script first does
something, which starts its simulation with them:

- `time(mode)`: `"real"` (the default), `"max"`, virtual time as fast as
  the stations allow, or a number, virtual time paced at that many times
  the wall. It is the simulation's for its whole life.
- `firmware(which, device)`: what nodes run, a device as the Firmware tab
  names it, `reticulous_dev_latest` or `reticulous_dev_20260927140352`
  ([Firmware](#firmware)). Each node runs the firmware of the last rule that
  matches it, a `_latest` build fetched when it is not here yet; a node no
  rule matches runs nothing, grey on the map, until one does.
- `on_first_boot(which, cmds)`: what a station is given the first time it
  boots with no state (every station of a new simulation, a node placed
  later, a station after a factory reset), after its name, the rules in
  order. `cmds` is lines in the station's own language, or **intents**,
  which each kind says in its own lines, or a list of both:
  `radio(freq_mhz, sf, bw_khz, cr, tx_dbm, sync, preamble)` slot 0's
  settings (`tx_dbm="max"`, each node's own maximum), `radio_up()` the
  radio started, `role(name)` its role. A station with state is left
  alone, so `lxmf create {name}`, which run twice makes two identities, is
  safe here.
- `include(path, missing_ok=False)`: another file under `testbed/` run
  here, as if written at this point; `nodesets()`, the names of the
  nodesets the world is made of.

**Every script starts from the startup script.** `include("scripts/startup.py")`
says, in this order: `role("transport")` to the nodes tagged transport;
`globals.py`'s radio at each node's maximum power to every node not tagged
`no-radio`; each nodeset's own setup (`for nodeset in nodesets():
include("nodesets/%s.py" % nodeset, missing_ok=True)`); then `radio_up()`,
last because a radio reads its settings, SUPE among them, when it starts.
A script's own first-boot lines after the include come after that. The
Scripts tab opens `startup.py`, `globals.py` and the world's nodeset setups
as tabs beside the script, so everything a station is told is on screen.

`testbed/scripts/globals.py` holds what every script shares: the channel
Berlin's Reticulum LoRa nodes announce on [rmap.world](https://rmap.world/)
(869.475 MHz, 125 kHz, SF7, CR 4/5), and `SYNC`, 0x12, the sync word every
RNode-based Reticulum firmware has fixed, so Reticulous hears them. Its
`FREQ_MHZ`, `SF`, `BW_KHZ` and `CR` are also read, as written and without
running it, by the page (coverage, links) and the loss tables' band, and a
run keeps a copy of it for its analysis; they stay plain numbers.

Both kinds of rule are kept with the run, so a node placed later, or
retagged, gets what they give it. Said after the simulation has started,
they apply to it at once: a node whose firmware changes is restarted on it,
its state kept. `time()` cannot change once the simulation runs. No script
code runs inside the simulation: the rules travel to it as data.

**Which nodes.** Wherever the library asks which nodes, it takes `"all"`, a
node's name, a list of names, or a selection, `nodes(field=value, …)`, over
each node's `name`, `id`, `tag`, `firmware`, `kind`, `role`, `antenna`,
`max_dbm`, `lat`, `lon`, `height_m` and `status`. Selections are
Python's own set algebra:

| | |
|---|---|
| `nodes(tag="lora", role="transport")` | both: keywords in one call are AND |
| `nodes(tag=("lora", "tcp-peer"))` | either: a tuple, list or set is any of |
| `nodes(tag="lora") \| nodes(name="internet")` | OR |
| `nodes(tag="lora") & nodes(kind="reticulous")` | AND |
| `nodes(tag="lora") - nodes(role="client")` | AND NOT |
| `~nodes(kind="sergeyculum")`, `a ^ b` | NOT, either but not both |
| `nodes(height_m=lambda h: h > 20)` | a function of the value, not in a rule |
| `nodes()` | every node |

A selection is a condition, not a list: written at a script's top, before
there is a simulation, it picks whatever matches when it is used. A rule
travels to the simulation, so its selection must be plain values, never a
function.

**What is done**, each call waiting for the nodes it names to be up first (a
node with no firmware refuses):

- `exec(which, cmds, pause=0)` types lines at each node in its own
  language, macros filled in: `cmds` is one line, several in one string
  (indented as the code around it, blank lines and `#` comments left out),
  or a list. Each node gets its lines in order; the nodes go together, or
  one after another `pause` seconds of the run's clock apart. It answers
  `{node: what it printed}`.
- The **meta commands** say one thing on any firmware, each kind in its own
  lines: `announce(which, spread=0)`, `max_tx_pwr(which, dbm=None)` the
  transmit power (each node's own maximum when no figure is given),
  `send_msg(from_node, to_node, text)` an LXMF message.
- `reset(which)`, `factory_reset(which)`; `up(which)`; `wait(seconds)`,
  `until(seconds)`, `now()`, the run's clock in seconds since the script's
  simulation began; `phase((name, until), …)` for the page's estimate;
  `snapshot(name)`, `move(node, lat, lon)`, `pause()`, `stop()`;
  `stations()` and `station(name)`, each node's facts.

`exec` and `send_msg` take `after=` (seconds of the run's clock) and
`wait=False`, which returns at once with a future whose `result()` is the
answer: how a driver puts many things on the clock at once. Every time is
the run's, so a real-time and a virtual-time run act at the same instants
of the run.

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
runs it on a simulation already running, which then takes its rules. The
script is a process of its own (`python3 -m sim_mesh.runner`) whose output is
kept and shown beside the editor; from a shell:

```sh
sim-mesh run lxmf-traffic --geodata berlin-city --nodeset mitte7
sim-mesh run ./my-study.py --geodata stralsund --nodeset fachhochschule --nodeset more
sim-mesh run lxmf-traffic --sim mitte7
```

A simulation keeps a copy of a script of `scripts/` (a file from elsewhere
only drives it), and a snapshot keeps that copy. The simulation runs on when
the script ends; one the script paused (`pause()`) stays on the Simulations
tab to be resumed ([Pausing](#pausing-and-resuming)).

**`report(run_dir)`**, `def` or `async def`, runs after the script has run
to its end, and returns the run's report as Markdown. The runner writes it
to the run as `report.md` with an **ETSI compliance** section after it
(`compliance.py`, [Reading a run](#reading-a-run)), and the page's
**Report** buttons, on the script run and on the simulation's row, show it.
`sim-mesh run SCRIPT --report RUN_DIR` writes it again for a run that has
ended, reading the script's definitions without running it.

**The editor** has a tab for the script and one for every file it imports:
another script, edited as the script is, or the library's own module, to
read.

`scripts/lxmf-traffic.py` is a whole LXMF (Lightweight Extensible Message
Format) run, on virtual time, through `sim_mesh.traffic`: announce warm-up
until paths stop growing, a seeded hour of messages, a drain, a gather, with
its record written to the run as `traffic.json`, and the simulation paused.
Its messages are the `send_msg` meta command, its identities the `address`
one and its warm-up the `announce` one, so it runs on any firmware that says
them. Its first-boot lines give each station what taking part needs, an
LXMF identity. Its report is the delivery `delivery.py` counts from the
senders' logs, each sender by its own station's (`reticulous` and
`standard_reticulum` stations by the message id its send answered; the
reticulum project's station, configured with rncfg, by the first message its
log shows to that recipient once the send was due, and the proof that closes
it): overall, by route and radio hops and by size, the latency, and the
undelivered by the sender's last line; it says so plainly when no station
had an LXMF identity to send from.

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

**Firmware** has two lists ([Firmware](#firmware)). **Latest builds**: each
project's newest in each catalogue, by the name a script gives it
(`reticulous_dev_latest`), with what it plays and its kind from its `node.yaml`,
when it was built, and whether it has been fetched; **Save** keeps it.
**Saved builds**: the ones kept, by their stamped names, each with a trash
can. **Import…** takes a device zip into the saved ones; **Refresh** surveys
the web's catalogues and `builds/` beside sim-mesh again.

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
it, or loads one into it; **Stop** ends it. A paused one has **Resume**; a
paused or ended one has a trash can, which deletes its run directory. A row
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
from a script of a line, `firmware("all", "reticulous_dev_latest")`. A
simulation runs the nodeset's file, so the nodeset the Nodes tab is editing
has its unsaved changes saved first. `sim-mesh new` from a shell picks
geodata, a nodeset and a script, or a snapshot, then a time mode and a
build. The front checks them, computes or reuses the loss tables, lays out
the run directory and starts a simd on it, which settles each node's
firmware, fetching what it names, before its stations start.

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

`sim-mesh` runs the front, `testbed/front.py`, on the published port 8800. It
starts one simd per simulation behind it, each with its own loopback control
port (from 9100), ether port (from 7100), station network (a /22 from
`127.16.0.0` that no socket on the host is bound in and no other front holds
the lock of, so two fronts on one host never share one) and run directory
(`testbed/runs/<name>/`, with the simd's own log in `simd.log`). Arguments
after `--` go to every simd as they stand: `sim-mesh -- --pairwise --stagger
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
**Resume** loads that into a new run directory under the same name, as a
snapshot load would, and starts it: the same network as it ended, booting
again, with T from 0 on a new ether; the paused run is then listed as ended.
Its trash can deletes the run directory, and with it the pause. A script's run pauses the simulation it started when
`main` ends; **⋯ ▸ Pause**, `sim-mesh pause`, and `sim.pause()` pause any running
one.

From a shell, `sim-mesh`'s simulation verbs do the same (`sim-mesh help` lists
every verb); `new` starts the front in the background (logging to
`testbed/runs/front.log`) when nothing answers on the port. Under Docker
they run inside the front's container, `sim-mesh-front`, so the front is
started first with `sim-mesh`.

```sh
sim-mesh run lxmf-traffic --geodata berlin-city --nodeset mitte7 --name lora
                                                    # a script's own simulation (Scripts)
sim-mesh new pw --geodata berlin-city --nodeset mitte7 --time max --pairwise
                                                    # a bare one, its ether on the pairwise rule;
                                                    # prints its control address, ether, network and run as JSON
sim-mesh new --snapshot mitte7-warm --time 2x --build reticulous_dev_latest
                                                    # named after what it loads: mitte7-warm
sim-mesh list
sim-mesh plan lora warm-up=+600 traffic=+4200       # T each phase ends at; +N is N s from now
sim-mesh pause lora                                 # stopped, its state kept; listed as paused
sim-mesh resume lora                                # started again as it ended, in a new run
sim-mesh stop lora
sim-mesh run lxmf-traffic --sim lora                # a script's main: one of scripts/ by name, or a path
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
started from one with `sim-mesh new --snapshot`) gets its nodeset, script and tables
back exactly as they were, without a recompute and without the planner, and
every station its store: identities, keys, paths and message history, as the
firmware keeps them. The snapshot keeps its script and its firmware and
first-boot rules because first boot comes only to a station with no state:
a factory reset after the load sets a station up as the first run did.

What comes back is what the firmware reloads at boot. A `reticulous` station
keeps its identities, keys and message history, but its paths only when
`s.rnsd.dir.persist_routes` is `1` (the default `0` drops restored routes and
relearns them on demand), and only as of its last directory write, every
`s.rnsd.dir.persist_s` seconds (default 900): a snapshot is a copy of the
store, and the store does not hold the path table in between.

Stations keep running across a snapshot: they are flushed first and the copy
is taken while they run, which for a store that commits whole files is the
same guarantee a power cut gives a board. Logs and the record are an account
of one run and are never copied into a snapshot. Runs, snapshots, the loss
and coverage caches and the fetched devices are not committed.

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
selected stations, and lists what each one said. A
line is one kind's language, so the stations must be of one kind; the dialog
asks which when the simulation has more than one. The macros are expanded per
station, so `lora 0 freq 869.475` retunes every `reticulous` station and
`announce now` at the `sergeyculum` kind makes every one of those announce.
**Right-click ▸ Announce** is the `announce` intent, in each station's own
lines.

Beside the line is **spread**, in seconds. Left at 0 every station is asked at
once, which is what a question wants — nothing goes on the air to answer
`show s.net.hostname`. Anything that *transmits* wants a spread: two dozen
stations running `lora 0 a` in the same instant is a collision storm rather
than an announcement, and what comes back describes the storm. Thirty or sixty
seconds across the network is enough.

### The control websocket

```
page/driver → simd   command {line, name? | names? | tag?, kind?, stagger?, after?, id?}
page/driver → simd   meta {verb, args?, name? | names? | tag?, kind?, stagger?, after?, id?}
simd → all pages     command_result {id, line | verb, name, results: {<station>: <reply>}, t}
driver → simd        plan {phases: [{name, until}]}
simd → all pages     clock {mode, rate, t, observed, barriers, slow_idles, plan}   once a wall second
```

Everything the page does is one JSON message on `ws://<simd>/ws`, and a
driver speaks the same messages; `testbed/simd.py`'s docstring lists them all
(`sim_load`, `snapshot_load`, `snapshot_save_as`, the `nodeset_*` edits by
node name, `levels`, the resets). `command` goes to the stations that are up
(or in setup) among those named by `name`, `names` or `tag`, or to all; they
must be of one kind or `kind` must narrow them. `meta` is an intent (the
[kinds' table](#station-kinds), and `address`), said to each chosen station
in its own lines; `message` and `path` take `to`, a node whose address is
asked of it first, and `peer_tcp` takes `to` and `port`. `stagger` spreads
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
| `device_list`, `device_refresh {sources?}` | the latest and saved builds, and a survey of the catalogues |
| `device_save {ref}`, `device_delete {ref}` | a latest build kept, a saved one removed |
| `antenna_list` | the antenna catalogue, each with its picture |
| `geodata_list`, `geodata_open`, `geodata_close`, `geodata_new`, `geodata_save`, `geodata_save_as` | geodata, each listed with how many nodesets have a node on it; opening a pack holds its sidecar for the socket |
| `geodata_rename {name, to}`, `geodata_delete {name}` | another name, or gone, with its own pack; refused while a running simulation stands on it; a delete says what keeps the pack (`kept_by`) |
| `nodeset_list {geodata?}`, `nodeset_open`, `nodeset_new`, `nodeset_save`, `nodeset_save_as`, `nodeset_delete` | nodesets; with `geodata`, each row says how many of its nodes stand on it (`inside`); a delete takes the nodeset's own setup script too |
| `nodeset_import {name, source, …}` | a new nodeset from the MeshCore map, a PotatoMesh instance, `sites.csv` or a deployed-network CSV |
| `nodeset_merge {name, layers}` | Save visible as: the shown layers, top first, as they stand, as one new nodeset |
| `script_list`, `script_open`, `script_new`, `script_save`, `script_save_as` | scripts, checked to parse |
| `script_run {name, sim \| geodata, nodeset, time?}`, `script_stop {run}`, `script_log {run}` | a script's `main` as a process, and its output |
| `snapshot_list`, `losses_compute` | the snapshots, and a nodeset's tables |
| `coverage {geodata, nodes}` | each node's pack raster: cached ones at once, the rest as `coverage_tile` messages as they land |

and its HTTP side: `POST /api/devices/import?name=<zip name>` and `POST
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
`testbed/sim_mesh/<protocol>/`, found by each node's firmware kind; the roles are
what `airtime.py --roles` and the hop counts through forwarding stations use.

| Tool | Says |
|---|---|
| `seq.py RUN` | the record as a sequence diagram (below) |
| `compare.py RUN_A RUN_B` | two runs side by side: milestones, per-station counts, paths; one run's figures alone |
| `airtime.py RUN` | airtime per station, per carrier and per frame kind; transmit power; exchanges at reduced power; CRC losses; with `--busy` the calling channel's occupancy where each station stands, with `--roles` airtime per role |
| `links.py RUN` | link geometry: distance of every usable one-way link, neighbours, hop diameter, beside what the run's loss table says would decode; with `--power`, traffic-channel power against distance |
| `delivery.py TRAFFIC.json RUN` | an LXMF traffic run's delivery (the `traffic.json` `scripts/lxmf-traffic.py` writes) from the senders' logs: by route hops, radio hops (by the run's loss table), size, latency |
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
Reticulous station, a frame reads as its length and carrier.

## A station's own doors

A `reticulous` station serves its web UI on port 80 of its own loopback
address, which is invisible outside the machine or image sim-mesh runs in; the
proxy on port 8800 routes by hostname, the simulation being the second
label (a station of a kind with no web UI is refused with a sentence saying
so, and has no **Web UI** button):

    http://alpha.lora.sim.localhost:8800/    by name
    http://1.lora.sim.localhost:8800/        by id

A bare `alpha.sim.localhost` reaches the one running simulation while there
is exactly one; with more it is refused with their names. Chrome and Firefox
resolve any `.localhost` name to loopback with no configuration. Safari does
not, and needs entries in `/etc/hosts` on the Mac:

    127.0.0.1  alpha.lora.sim.localhost bravo.lora.sim.localhost

Port 8800 is the testbed's own: where `sim-mesh` runs sim-mesh in its image, the
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

- **Console** — its serial console, in a terminal window over a websocket.
  For `reticulous`, first-run setup and every CLI command, exactly as a board
  on a cable; for `sergeyculum` and `microreticulum`, its log lines.
- Its kind's own door. simd sets up and asks a `reticulous` station over
  **framed RPC** on its console pty (below), and a `sergeyculum` one with
  `rncfg <verb> runs/<sim>/nodes/<name>/kiss`, which talks KISS (the serial
  framing radio modems speak) to it exactly as over USB; a `microreticulum`
  one is set up through `runs/<sim>/nodes/<name>/rnoded.conf` and a
  restart, and has no door to ask it anything. A `reticulous`
  station's TCP CLI is closed, as on a board, until `set s.net.cli_port 8081`
  opens it (a script line does that for a whole nodeset); then `nc <addr>
  8081` from a shell on the same machine is its command line.
- `tail -f runs/<sim>/nodes/<name>/log` — everything it has printed, across
  restarts.

### Framed RPC on the console

```
station → simd   "… serial] framed rpc v1"                 once, early in boot, as log text
simd → station   F5 53 47 01 <id> <len:2> show s.net.hostname
station → simd   F5 53 47 01 <id> <len:2> s.net.hostname = alpha
simd → station   F5 53 47 01 <id'> <len:2> show s.sys.reset_reason   until it reads: boot is done
simd → station   F5 53 47 01 <id''> <len:2> lora up          one frame per setup line
station → simd   F5 53 47 01 <id''> <len:2> enabled 1 radio(s)
```

The firmware multiplexes a framed side channel onto its serial console
([`spangap-core/docs/framed-rpc.md`](../spangap-core/docs/framed-rpc.md)), and
a station's console here is its pty. A frame is never echoed, never enters the
line editor and never turns the log into a CLI session, so simd asks a station
things while a person types at its **Console**. The pty drain takes each reply
frame out of the stream and passes every other byte on unchanged, so the log
and the console window never see one.

A station answers its door once it has printed the marker since it last
started and answered `show s.net.hostname` with something. The command line
answers from early in boot, before the firmware's services have initialised
their settings, and a setting typed then can be undone by that init (a
radio's frequency is); the boot writes `s.sys.reset_reason` once they all
have, and a first boot, which is when a station is set up, has none until
then. So a station is `up` once that reads and it has been set up, if it had
to be. A station that prints no marker within 20 seconds of wall time is sent
the probe once, blind, and a Ctrl-C after it if it does not answer; that
undoes it on firmware that does not speak frames, and the station stays
`starting`.

The device runs one frame at a time, bounds each at five seconds, and cuts a
reply that outgrows its buffer at the last complete line without saying so.
So simd keeps one frame in flight per station and asks for one key at a time,
and a setup line whose effect lands after its reply is followed until it has:
`lora up` by `show s.lora.0.enable` until it reads `1`, and any line whose
reply came back at the five-second bound by `show s.net.hostname` until the
command line answers again. The id is a hash of the command, so a retry
carries the one it had, and a reply that arrives after its query gave up
answers the retry.

A command line of up to 4096 bytes runs over a frame, so an `lxmf send`
carrying a text that needs a link and a resource goes the same way as any
other line; a longer one is refused with `rpc: command over 4096 bytes`.

A station that exits is started again, because a restart on this target is a
process exit: a station rebooting itself comes back on the same address with
the same directory.

## The developer loop, with a workspace

To run firmware of your own, put sim-mesh in a [spangap](https://github.com/spangap/spangap)
workspace — the directory it was cloned into, made one with `spangap init` —
and build the station there, for the `hw-linux` board, a Linux process
rather than a chip image:

```sh
spangap build reticulous/reticulous --with spangap/hw-linux \
    --with reticulous/netgraph \
    -x reticulous/rnsh -x reticulous/iface-auto -x reticulous/iface-ble \
    -x reticulous/rnode-ble -x reticulous/nomad -x reticulous/maps \
    -x spangap/viewer -x spangap/acme -x spangap/duckdns -x spangap/sshd \
    -x spangap/upnp -x spangap/wg
```

The excluded straddles are the ones not built for this target; netgraph is
there because a nodeset whose stations share a community
(`s.netgraph.community`) needs it, the community's membership announce being
what carries each node's gateway distance. The result is
`reticulous/esp-idf/build.linux/`: `reticulous.elf` and its `/fixed` tree in
`data_merged/`. Every target builds in its own `build.<target>/`, so a chip
build and this one never touch each other's files.

That directory can be run as it is, by path (`sim-mesh new … --build
reticulous/esp-idf/build.linux`), but what was last built there is anyone's
guess. The way to run a build of one's own is to make it a device file in
the catalogue it will be published in (below): `make-builds` into
`builds/dev` stamps it, and since a local catalogue and the web's of the same
name are one catalogue, the newest of either winning, a script's
`reticulous_dev_latest` runs it at once, and keeps running the newest
whether it was published since or not.

A `sergeyculum` station and its tool are built in the Sergeyculum tree
(`sergey/reticulum` in the workspace), with Rust. Sergeyculum publishes no
sim-mesh build yet, so whatever was last compiled there is the compiled build
`sergeyculum_local_latest`:

```sh
cd sergey/reticulum/fw/sim-mesh && cargo build --release    # fw/sim-mesh/target/release/sim-mesh
cd sergey/reticulum && cargo build --release -p rncfg       # target/release/rncfg
```

A `microreticulum` station is attermann's firmware in its clone under
`competition/`, built with PlatformIO after `sim-mesh build radio`, since it
links `radio/build/libsimradio.a` through the Portduino backend
(`radio/portduino/`). Portduino's core needs libuv's and i2c-tools'
headers (`libuv1-dev`, `libi2c-dev` on Debian). Two builds of it are
compiled builds: `microreticulum_local_latest`, the firmware as it is, and
`microreticulum-jrl290_local_latest`, a stand-in for jrl290's RTNode-HeltecV4
with that firmware's path-request rules on the stack (the branch
`jrl290-rules` of `competition/attermann_microReticulum`, checked out as the
worktree `competition/attermann_microReticulum_jrl290`), its LoRa interface in
full mode:

```sh
cd competition/attermann_microReticulum_Firmware
pio run -e sim-mesh           # .pio/build/sim-mesh/rnode_firmware_native
pio run -e sim-mesh-jrl290    # .pio/build/sim-mesh-jrl290/rnode_firmware_native
```

Device files are produced by the catalogue build: a `builds.yaml` entry with
`target: linux`, `arch:`, `virtual_hardware:` and `virtual_radio:` becomes `hw-sim-mesh-<arch>` in its
catalogue when `spangap make-builds` runs on a machine of that architecture.
The front sees it in the workspace's `builds/<catalogue>/` by itself, as that
catalogue's latest, and fetches it when a simulation uses it
([Firmware](#firmware)).

## After a restart

Stations are processes, not a service: stopping `sim-mesh` stops them. Their
state is not in the process though — `runs/`, `snapshots/`, `devices/` and a
workspace's build are all in the directory sim-mesh was cloned into, which the
image mounts from the host. A simulation's stations keep their state in its
run directory, but a new simulation starts factory-fresh; to carry a network
across a restart, save a snapshot before stopping, then after `sim-mesh` load
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

## The chip library

`radio/` is the SX1262 model and the station's link to the ether, behind a
C ABI (application binary interface, `radio/include/simradio.h`): open the
link, open a chip per radio slot, hand it SPI frames, pulse its reset, read
its lines, and, in a virtual-time run, read node time, set wakes, learn
every move of T and say the station is idle. A station of any language
links it in place of a radio, below an unchanged driver. Beside it,
`radio/shim/simclock.c` builds `libsimclock.so`, the preloaded library that
answers the C library's clocks and waits in node time, draws the station's
randomness from the run's seed, counts the console and TCP bytes the ether
makes into instants of T, and keeps the busy watchdog off a station that is
computing ([INTERNALS.md](INTERNALS.md#time)).

The model reaches its host through six services — a clock, one-shot timers, a
recursive lock, a UDP socket, a reader, a log — and two backends supply them:

| Backend | For |
|---|---|
| `radio/backend/posix/` | a plain process: `std::thread`, `CLOCK_MONOTONIC`, a `std::recursive_mutex` |
| `radio/backend/esp-idf/` | an ESP-IDF (Espressif's development framework) firmware built for the Linux host target: esp_timer, a FreeRTOS critical section and task; an IDF component |

`sim-mesh build radio` builds it (`radio/build/`: `libsimradio.a`,
`libsimradio.so`, `libsimclock.so`); a virtual run preloads the shim from
there. The ESP-IDF backend is proved by a throwaway project that links it
against the IDF host port and sends one frame (`radio/tests/esp-idf-link/`;
the commands are at the top of its `CMakeLists.txt`).

## Tests

None needs firmware, a planner or a network:

```sh
cd sim-mesh/testbed && python3 -m pytest -q      # the stores, the devices, the front, simd, the kinds, the library, the tools
cd sim-mesh/ether   && python3 -m pytest -q      # the medium and the conductor (built first), over real UDP and in-process
cd sim-mesh/radio   && python3 -m pytest -q tests  # the chip model, the conductor, the time shim
cd sim-mesh/testbed/ui && npx vue-tsc --noEmit && npx quasar build
```

The model's tests load `libsimradio.so` with ctypes, drive it frame by frame
the way a driver does, and play the ether on a UDP socket of their own; the
conductor's tests do the same in virtual time, and the shim's run a small C
stand-in station (`radio/tests/standin.c`) under `libsimclock.so`. The
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

Two halves. The **host port** is what makes a firmware build and run as a
process at all; the **simulation** is what gives it a radio and a medium.

### The host port of reticulous

It lives with that firmware, not here:

| Where | What |
|---|---|
| [`spangap/build-system`](../spangap/build-system/README.md) | a board straddle's `target:`, exported as `IDF_TARGET`; on `linux`, no flashable image, and a device file in a catalogue |
| [`spangap/hw-linux`](../hw-linux/README.md) | the board: station identity and directory, the GPIO (general-purpose input/output) shim, esp_timer, descriptor waits, the tickless tick |
| `spangap-core/esp-idf/src/host/` | no power manager, no USB transport, and deflate over the system zlib |
| `spangap-net/esp-idf/src/net_relay.cpp` | the socket relay, shared with the chip: the event bus, the listen sockets, the byte proxy |
| `spangap-net/esp-idf/src/host/` | the link backend — loopback, up from the first instant — in place of the WiFi state machine |
| `spangap-web/esp-idf/src/host/` | the WebRTC port: the addresses to advertise, the address to bind, a CRC32; the rest of the DataChannel is the chip's source |

Everywhere else the rule is the same: chip-only code sits behind
`#if !CONFIG_IDF_TARGET_LINUX` or drops out of the source list, and host-only
code lives in that component's `src/host/`.

### The simulation

| Where | What |
|---|---|
| `sim-mesh` | the one command: the front natively or in sim-mesh's image, `new`, `stop`, `list`, `plan`, `run`, `devices`, `build` |
| `Dockerfile` | sim-mesh's image, for a machine that is not Linux |
| `devices/local/` | the compiled builds, run in place, named `<project>_<catalogue>` |
| `stations/standard_reticulum/` | a standard Reticulum node's station program: the RNode started, Reticulum and LXMF behind it, its console |
| `radio/` | the chip and the station's UDP link to the ether, as a C library |
| `radio/src/conductor.cpp` | the station's side of virtual time: T, node time, wakes, the idle |
| `radio/shim/simclock.c` | `libsimclock.so`, the C library's time in node time (its sleeps, descriptor waits, condition and semaphore waits), the seeded randomness, the console and TCP counts, the listening sockets and the watchdog's hold; `radio/include/simclock.h` is what it is handed |
| [`radio/portduino/`](radio/portduino/README.md) | the chip library for a Portduino firmware: a PlatformIO library standing in for spidev and libgpiod, and the firmware's idle wait |
| `iface-lora/esp-idf/src/host/virtual_hal.*` | RadioLib's HAL (hardware abstraction layer) over the GPIO shim and `radio/`, in place of the SPI bus |
| [`ether/`](ether/README.md) | the medium: the loss tables, who hears a frame and how it comes out; `slt.py` reads and writes a table |
| `testbed/front.py` | several simulations behind one port: the registry, the editors' verbs, the imports, the planner sidecars, the loss tables before a start, one simd per simulation, script runs, coverage, station hostnames by simulation, the WebRTC relay one level up, the finish estimate |
| `testbed/simd.py` | one simulation: the ether, the stations and their setup, the proxy, the control server, commands and intents on chosen stations, a moved node's row |
| `testbed/simctl.py` | behind `sim-mesh new`, `stop`, `list` and `plan`: the front from a shell; starts the front when none answers |
| `testbed/store.py` | where geodata, nodesets, scripts, tables, coverage, runs and snapshots live, and what a name may be |
| `testbed/devices.py` | devices: the survey of the catalogues, a latest build fetched when used, saving, importing, compiled builds, and what a device name runs |
| `testbed/antennas.py`, `testbed/antennas/` | the antenna catalogue and pictures, a pattern's gain by direction, a pair's gain in three dimensions |
| `testbed/geodata.py` | geodata: packs and synthetic ground, the projections, the extent, a sim-mesh geodata pack's export and import |
| `testbed/sources.py` | a build's sources: what a rectangle needs of each, the download cache and its fetches |
| `testbed/packbuild.py` | one pack built from its sources: fetch, `planner-job pack-build`, the pack into place |
| `testbed/nodeset.py` | nodesets: nodes, their maximum powers, antennas and tags (a role tag, `no-radio`), offsets, links, edits, the geometry hash, the merge of shown layers, the imports |
| `planner/` | the Rust workspace: `planner-web` (the sidecar), `planner-job` (a pack's build, a node map's import), `planner-pack` (the compiler, OpenStreetMap from a PBF extract), `planner-buildings`, `planner-import`, and the ground, propagation and coverage crates |
| `testbed/script.py` | scripts: listing, checking, loading, the `firmware()` rules at a script's top |
| `testbed/sim_mesh/library.py`, `testbed/sim_mesh/select.py` | the script library, `firmware`, `exec`, `max_tx_pwr`, `send_msg`, and `nodes()` selections |
| `testbed/losses.py` | a loss table, on synthetic ground or through the sidecar; the cache; links, shadowing, antennas and offsets as layers, the ground under each node; one node's row |
| `testbed/coverage.py` | a node's coverage raster on a pack, through the sidecar, cached |
| `testbed/runs.py` | a run directory, and snapshots taken from and loaded into one |
| `testbed/stations.py` | one firmware process, its pty, its log, its supervisor; the thread every station's pty is read on |
| `testbed/kinds/` | one class per firmware: its environment, when it is up, how it is set up and asked things, its role, its intents |
| `testbed/rpc.py` | framed RPC on a station's console pty: the demultiplexer in the drain, and the client its kind speaks |
| `testbed/proxy.py` | the hostname proxy |
| `testbed/webrtc.py` | the WebRTC relay: the signalling rewritten, and one UDP port in front of every station's DataChannel |
| `testbed/ui/` | the page (Quasar 2 on Vue 3; Pinia stores `catalog`, `geodata`, `nodes`, `sim`, `coverage`, `display`, `socket`); `vendor/planner-wasm` is the planner's built planner-wasm, copied in by `vendor/update-planner-wasm.mjs` so the page builds with no planner beside it |
| `testbed/seq.py`, `compare.py`, `airtime.py`, `links.py`, `delivery.py`, `compliance.py`, `referee.py` | the analysis tools ([Reading a run](#reading-a-run)) |
| `testbed/sim_mesh/` | the library: `library` (what a script says, synchronously), `select` (`nodes()`), `traffic` (the LXMF traffic driver), `sim` (the async hold on a simulation the library runs on), `runner` (a script run, its simulation started, its report), `view` (a run opened for analysis), `record`; `sim_mesh/reticulum/` holds Reticulum's parts: frame reading (Reticulum packets, SUPE), delivery analysis |
| `testbed/boards.py` | the one board, an SX1262 with a GC1109 front end above 22 dBm; a node's maximum power; what a station is told of it |
| `testbed/scripts/` | the scripts: `realtime.py`, `lxmf-traffic.py`; `startup.py`, which every script includes; `globals.py`, the settings they share and the page reads |
| `testbed/geodata/`, `testbed/nodesets/` | your geodata and nodesets (not committed) |
| `testbed/testdata/` | what the tests stand on: the four stations `four.yaml` on the synthetic ground `plain-27.yaml` |

Nothing above the bus is aware of any of it: the LoRa driver, its CSMA and
airtime accounting, Reticulum, LXMF and the web UI are the same code that runs
on a board. The same holds for a `sergeyculum` station: its SX1262 driver,
`LoRaIface` and engine are Sergeyculum's own, unchanged, over an embedded-hal
bus that ends in `radio/`.

## License

sim-mesh is released under the Apache License, Version 2.0; see [LICENSE](LICENSE).
