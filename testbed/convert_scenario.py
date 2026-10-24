#!/usr/bin/env python3
"""An old scenario as main's ground, nodeset and medium.

    convert_scenario.py OLD.yaml --out DIR --firmware KIND=FIRMWARE [--firmware ...]
                        [--name NAME] [--force]

OLD.yaml is a `scenario.yaml` of the format before geodata and nodesets: one
file with an origin, physics, kinds, first-boot lines, nodes, obstructions and
links (`-` reads it from standard input, and then --name is needed). It is
never written. The conversion goes to

    DIR/geodata/<name>.yaml      synthetic ground: the old exponent and shadowing
    DIR/nodesets/<name>.yaml     the nodes; walls and gains as offsets; links
    DIR/nodesets/<name>.py       the nodeset's own setup: each old kind's firmware
    DIR/medium.txt               the simd flags the old medium needs, one line
    DIR/conversion.txt           what went where, what did not, the distance check

laid out as testbed/ lays them out, so the three files copy straight in. Each
is read back through main's own reader before anything is written.

How each part goes over, and why:

- **Positions.** An old `pos` is [lat, lon] in degrees, and the old ether put
  a station on a plane in metres, equirectangular about the file's `origin`
  (`project` in its ether), and priced every pair by its distance there. The
  same projection is taken here, the points are centred on their centroid,
  and each is written at synthetic ground's degrees (111,320 m each on both
  axes), so every pair stands exactly as far apart as the old
  runs had it. `check_distances` holds every pair to that within
  CHECK_TOLERANCE_M, and also measures how far the old plane was from the
  great circle: the equirectangular error, about tan(latitude) times a node's
  latitude from the origin in radians, so at most 0.2 % in a city 20 km
  across at 50 degrees, 0.03 dB of loss. Both use the old ether's sphere; the
  WGS84 ellipsoid differs from it by up to 0.3 % at mid latitudes, in the old
  runs as here.
- **Heights.** The old format has none, so every node stands at main's
  default 2 m, marked assumed; a node's own `height_m`, which no old reader
  read, is carried where a file has one.
- **Antennas.** An old `gain_db` was isotropic, added at both ends of every
  pair in every direction. Every node gets `rubber_duck`: its 0 dBi peak
  points at the horizon, which is where every pair looks on flat ground with
  the antennas at one height, give or take the earth's curvature (under
  1e-4 dB a pair at 30 km), so it is the old 0 dB. Main's default whip would
  add 1.8 dB at each end. A gain that is not 0 has no antenna of its own (the
  catalogue's run from -4 to 11 dBi, each with a pattern), so it becomes
  offsets: -(G_a + G_b) on every pair a gain touches, which is what the old
  ether added, a stated link's pair included.
- **Walls.** An old obstruction is dB added to one pair both ways, on top of
  its distance loss or its stated link: main's offset exactly. One offset per
  pair carries the wall and the gains together, its note saying which.
- **Links.** An old link states a pair's loss outright, both ways, in place of
  its distance and its shadowing, with gains and walls still on it. It goes to
  the nodeset's `links:`, a layer over the loss table that states a pair's
  loss the same way, antennas and offsets still on it. It was flat in
  frequency; main moves a cell to the frame's carrier by 20*log10(f/f0),
  hundredths of a dB inside a band.
- **Kinds.** A node's old kind named its firmware, the kind's `elf`. The
  caller maps each kind to an installed firmware, by name or `<base>_latest`
  (--firmware KIND=FIRMWARE), and a node of a kind the mapping lacks is
  refused. A nodeset names no firmware, so each node is tagged with its kind,
  and the nodeset's own setup says each tag's firmware: scripts/startup.py
  includes it after a script's own .firmware(…), so its rules are the last to
  match.
- **First-boot lines**, each node's as the old testbed said them (the
  scenario's to its default kind, then the kind's, then the node's own), in
  the old kind's language: the role becomes the `transport` tag and the
  transmit power the node's `max_dbm`, which startup.py's radio sends at;
  main names a station and starts its radio itself. The other radio figures
  are scripts/globals.py's, one radio for every node, and a difference from
  it is warned of. Every other line goes to the setup as it was, to the same
  nodes in the same order, as on_first_boot() rules in the old kind's
  language: a kind is best mapped to a firmware of the project it was.
- **Ground.** Synthetic and flat, with the old exponent, over the square that
  holds every node. Shadowing (`shadowing_db`, `shadowing_seed`) goes to the
  geodata's keys of those names, which the shadowing layer over the loss
  table reads; the old ether drew none for a pair with a stated link.
- **Medium.** Main's medium figures are simd's flags: `--noise-figure`;
  `capture_model: bench` is `--bench-capture`, and the old default, a frame
  surviving each audible interferer by the capture margin, is `--pairwise`.
  What has no flag, a CRC band among them, is warned of.
"""

import argparse
import ast
import math
import os
import re
import sys

import yaml

import antennas as antennas_module
import boards as boards_module
import firmware as firmware_module
import geodata as geodata_module
import nodeset as nodeset_module
import script as script_module
import store

EARTH_RADIUS_M = 6371008.8          # the old ether's sphere, the mean radius
ANTENNA = "rubber_duck"
CHECK_TOLERANCE_M = 0.001           # the file's 9 decimals of a degree are 0.1 mm
EXTENT_STEP_M = 1000.0              # the ground's square, in whole kilometres,
EXTENT_MARGIN_M = 1000.0            # with this much beyond the farthest node
FAR_FROM_GREAT_CIRCLE = 0.01        # the old plane this far off it is worth a warning

# The old format's defaults (testbed/scenario.py at demo/integration): a file
# omitting a figure had these, and one naming no kinds had simd's `reticulous`.
OLD_PHYSICS = {"exponent": 2.7, "noise_figure_db": 6.0, "capture_db": 6.0,
               "shadowing_db": 0.0, "shadowing_seed": 0, "capture_model": "margin",
               "sf_orthogonality": "none", "crc_band_db": 0.0}
OLD_DEFAULT_KIND = "reticulous"
OLD_TOP_KEYS = ("origin", "physics", "kinds", "setup", "nodes", "obstructions", "links")
OLD_NODE_KEYS = ("id", "kind", "pos", "gain_db", "setup", "height_m")
OLD_KIND_KEYS = ("type", "elf", "fixed", "tools", "env", "setup")
MAIN_SAME_SF_DB = 6.0               # main's capture margin, fixed (ether.py)

# Where the shadowing layer reads its figures: keys at the geodata file's top,
# beside `synthetic:` or `pack:`, since it lies over either ground's table.
SHADOWING_KEYS = ("shadowing_db", "shadowing_seed")
LINK_NOTE = "stated by the old scenario"

TRANSPORT = "transport"
RESERVED_TAGS = nodeset_module.ROLE_TAGS + (nodeset_module.NO_RADIO,)


class ConvertError(Exception):
    """An old scenario that cannot be converted as asked."""


# ---- the old file ----------------------------------------------------------

def old_metres(origin, lat, lon):
    """Degrees to metres east and north of `origin`, as the old ether placed
    a station: equirectangular, scaled by the origin's own cosine."""
    lat0, lon0 = origin
    return (math.radians(lon - lon0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M,
            math.radians(lat - lat0) * EARTH_RADIUS_M)


def great_circle_m(a, b):
    """Metres between two (lat, lon) along the old ether's sphere."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def read_old(data, where):
    """An old scenario's mapping filled out as its own reader did: defaults
    for what it omits, the node's kind its file's first when it names none,
    a link or wall naming a node the file lacks left out, and a pair's last
    link and last wall the ones that stand (the old ether kept a pair once)."""
    if not isinstance(data, dict):
        raise ConvertError("%s: not a scenario" % where)
    notes = []
    extra = sorted(str(k) for k in data if k not in OLD_TOP_KEYS)
    if extra:
        notes.append("top-level keys no old reader read, left out: %s" % ", ".join(extra))
    physics = dict(OLD_PHYSICS, **(data.get("physics") or {}))
    unknown = sorted(k for k in physics if k not in OLD_PHYSICS)
    if unknown:
        notes.append("physics the old ether did not read, left out: %s" % ", ".join(unknown))
    if physics["capture_model"] not in ("margin", "bench"):
        raise ConvertError("%s: capture_model is margin or bench, not %r"
                           % (where, physics["capture_model"]))
    if physics["sf_orthogonality"] not in ("none", "croce"):
        raise ConvertError("%s: sf_orthogonality is none or croce, not %r"
                           % (where, physics["sf_orthogonality"]))
    kinds = data.get("kinds") or {OLD_DEFAULT_KIND: {}}
    if not isinstance(kinds, dict):
        raise ConvertError("%s: `kinds:` is a mapping of kind name to its spec" % where)
    kinds = {str(k): dict(v or {}) for k, v in kinds.items()}
    default_kind = next(iter(kinds))
    for kind, spec in kinds.items():
        odd = sorted(k for k in spec if k not in OLD_KIND_KEYS)
        if odd:
            notes.append("kind %s: keys left out: %s" % (kind, ", ".join(odd)))
        if spec.get("env"):
            notes.append("kind %s: its env (%s) belongs to the firmware now, and is not carried"
                         % (kind, ", ".join(sorted(spec["env"]))))
    nodes = {}
    ids = {}
    odd_keys = {}
    no_pos = 0
    for name, node in (data.get("nodes") or {}).items():
        name = str(name)
        if not isinstance(node, dict):
            raise ConvertError("%s: node %s is not a mapping" % (where, name))
        kind = str(node.get("kind") or default_kind)
        if kind not in kinds:
            raise ConvertError("%s: node %s is of kind %r, which `kinds:` does not name"
                               % (where, name, kind))
        try:
            node_id = int(node["id"])
            pos = [float(v) for v in (node.get("pos") or [0.0, 0.0])[:2]]
            gain = float(node.get("gain_db", 0.0) or 0.0)
            height = node.get("height_m")
            height = None if height is None else float(height)
        except (KeyError, TypeError, ValueError) as err:
            raise ConvertError("%s: node %s needs an id, and numbers for pos and gain_db"
                               % (where, name)) from err
        if node_id in ids:
            raise ConvertError("%s: nodes %s and %s share id %d"
                               % (where, ids[node_id], name, node_id))
        ids[node_id] = name
        no_pos += "pos" not in node
        for key in node:
            if key not in OLD_NODE_KEYS:
                odd_keys[key] = odd_keys.get(key, 0) + 1
        nodes[name] = {"id": node_id, "kind": kind, "pos": pos, "gain_db": gain,
                       "height_m": height, "setup": [str(l) for l in node.get("setup") or []]}
    if not nodes:
        raise ConvertError("%s: no nodes" % where)
    if odd_keys:
        notes.append("node keys no old reader read, left out: %s"
                     % ", ".join("%s (%d)" % kv for kv in sorted(odd_keys.items())))
    if no_pos:
        notes.append("%d node(s) have no pos and stand at 0 deg, 0 deg, as the old reader "
                     "put them" % no_pos)
    walls, skipped_walls, again_walls = _pairs(data.get("obstructions"), nodes, "db", where,
                                               "an obstruction")
    links, skipped_links, again_links = _pairs(data.get("links"), nodes, "loss_db", where,
                                               "a link")
    walls = {pair: value for pair, value in walls.items() if value[1]}
    for count, what in ((skipped_walls, "obstruction"), (skipped_links, "link")):
        if count:
            notes.append("%d %s(s) name a node the file lacks, or one node twice: left out, "
                         "as the old testbed left them" % (count, what))
    for count, what in ((again_walls, "obstruction"), (again_links, "link")):
        if count:
            notes.append("%d %s(s) restate a pair: the last stands, as in the old ether"
                         % (count, what))
    origin = [float(v) for v in (data.get("origin") or [0.0, 0.0])[:2]]
    return {"origin": origin, "physics": physics, "kinds": kinds, "default_kind": default_kind,
            "setup": [str(l) for l in data.get("setup") or []], "nodes": nodes,
            "walls": walls, "links": links, "notes": notes}


def _pairs(items, nodes, key, where, what):
    """{frozenset pair: ([a, b] as first written, value)}, a pair's last
    value standing, with how many entries were left out and how many
    restated a pair."""
    out, skipped, again = {}, 0, 0
    for item in items or []:
        try:
            a, b = (str(n) for n in list(item["between"])[:2])
            value = float(item.get(key, 0) if key == "db" else item[key])
        except (KeyError, TypeError, ValueError) as err:
            raise ConvertError("%s: %s is { between: [a, b], %s: <dB> }"
                               % (where, what, key)) from err
        if a not in nodes or b not in nodes or a == b:
            skipped += 1
            continue
        pair = frozenset((a, b))
        again += pair in out
        out[pair] = ((out[pair][0] if pair in out else [a, b]), value)
    return out, skipped, again


# ---- first-boot lines ------------------------------------------------------

def _radio(figure, text):
    """One radio figure from its old spelling, in the intent's units."""
    if figure in ("sf", "cr", "preamble"):
        return int(text)
    if figure == "sync":
        return int(text, 0)
    return float(text)


RETICULOUS_RADIO = {"freq": "freq_mhz", "sf": "sf", "bw": "bw_khz", "cr": "cr",
                    "txp": "tx_dbm", "sync": "sync", "preamble": "preamble"}
RNCFG_RADIO = {"--freq-hz": ("freq_mhz", 1e6), "--sf": ("sf", 1), "--bw-hz": ("bw_khz", 1e3),
               "--cr": ("cr", 1), "--txpower-dbm": ("tx_dbm", 1)}
ROLE_WORDS = {"1": TRANSPORT, "true": TRANSPORT, "on": TRANSPORT,
              "0": "client", "false": "client", "off": "client"}


def reticulous_line(line):
    """What one reticulous CLI line said, as [(what, value)]: ("said", intent)
    for a line main says itself, ("role", role), ("radio", {figure: value}),
    or ("other", line)."""
    words = line.split()
    if words == ["hostname", "{name}"]:
        return [("said", "name")]
    if words == ["lora", "up"]:
        return [("said", "radio_up")]
    try:
        if len(words) == 4 and words[:2] == ["lora", "0"] and words[2] in RETICULOUS_RADIO:
            figure = RETICULOUS_RADIO[words[2]]
            return [("radio", {figure: _radio(figure, words[3])})]
        if len(words) == 3 and words[:2] == ["set", "s.lora.0.tx_power"]:
            return [("radio", {"tx_dbm": float(words[2])})]
    except ValueError:
        return [("other", line)]
    if len(words) == 3 and words[:2] == ["set", "s.rnsd.transport_enabled"] \
            and words[2].lower() in ROLE_WORDS:
        return [("role", ROLE_WORDS[words[2].lower()])]
    return [("other", line)]


def rncfg_line(line):
    """What one `rncfg` line of the old berlinmesh kind said, as
    reticulous_line does; a `set` with flags of its own besides the radio's
    keeps them as a line of their own."""
    words = line.split()
    if words == ["name", "set", "{name}"]:
        return [("said", "name")]
    if len(words) == 2 and words[0] == TRANSPORT and words[1] in ("on", "off"):
        return [("role", ROLE_WORDS[words[1]])]
    if words[:1] == ["set"] and len(words) > 1:
        radio, rest = {}, []
        i = 1
        while i < len(words):
            flag = words[i]
            if flag in RNCFG_RADIO and i + 1 < len(words):
                figure, scale = RNCFG_RADIO[flag]
                try:
                    value = float(words[i + 1]) / scale
                except ValueError:
                    return [("other", line)]
                radio[figure] = int(round(value)) if figure in ("sf", "cr") else round(value, 6)
                i += 2
            else:
                rest.append(flag)
                i += 1
        out = [("radio", radio)] if radio else []
        if rest:
            out.append(("other", "set " + " ".join(rest)))
        return out
    return [("other", line)]


# The old kinds' types and the language their lines are in.
DIALECTS = {"reticulous": reticulous_line, "berlinmesh": rncfg_line}
# What each old type is in main: its firmware's category.
MAIN_CATEGORY_OF = {"reticulous": "reticulum", "berlinmesh": "reticulum"}


def kind_type(old, kind):
    """An old kind's firmware type: its `type:`, else its own name."""
    return str(old["kinds"][kind].get("type") or kind)


def read_lines(old):
    """Every node's first-boot lines as the old testbed said them, read:
    {node: (role or None, {figure: value})}, and the lines no reading
    takes, in the order said, as [("kind" or "node", its name, [line])]: a
    kind's go to its every node, the scenario's to its default kind's."""
    said = {}

    def read(kind, lines):
        dialect = DIALECTS.get(kind_type(old, kind))
        out = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out += dialect(line) if dialect else [("other", line)]
        return out

    default = old["default_kind"]
    used = {node["kind"] for node in old["nodes"].values()}
    scenario = read(default, old["setup"])
    kinds = {kind: read(kind, spec.get("setup") or []) for kind, spec in old["kinds"].items()}
    own = {name: read(node["kind"], node["setup"]) for name, node in old["nodes"].items()}
    left = []
    for kind, items in [(default, scenario)] + list(kinds.items()):
        if kind in used and any(w == "other" for w, _ in items):
            left.append(("kind", kind, [v for w, v in items if w == "other"]))
    for name, node in old["nodes"].items():
        items = (scenario if node["kind"] == default else []) + kinds[node["kind"]] + own[name]
        role, radio = None, {}
        for what, value in items:
            if what == "role":
                role = value
            elif what == "radio":
                radio.update(value)
        said[name] = (role, radio)
        if any(w == "other" for w, _ in own[name]):
            left.append(("node", name, [v for w, v in own[name] if w == "other"]))
    return said, left


# ---- the conversion --------------------------------------------------------

def kind_tag(kind):
    """The tag a node of an old kind carries: the kind's own name where it
    can be one and means nothing else, else `kind-` and its slug."""
    if store.NAME_RE.match(kind) and kind not in RESERVED_TAGS:
        return kind
    return store.slug("kind-" + kind, "kind")


def check_firmware(ref):
    """(what a firmware name resolves to here, or None; a warning, or None)
    for a name main takes as a firmware, and ConvertError for one it does
    not. A firmware not installed here yet is no reason to refuse: it may be
    added later."""
    ref = str(ref).strip()
    if firmware_module.latest_base(ref) is None and firmware_module.parse_name(ref) is None:
        raise ConvertError("firmware %s: a firmware is <base>_<arch>_<version> or "
                           "<base>_latest" % ref)
    try:
        return firmware_module.resolve(ref), None
    except firmware_module.FirmwareError as err:
        why = re.sub(r"^firmware %s: " % re.escape(ref), "", str(err))
        return None, "firmware %s is not here yet (%s)" % (ref, why)


def globals_radio():
    """scripts/globals.py's radio, as startup.py sets it: the shared figures
    and SYNC, read without running it."""
    radio = script_module.shared_radio()
    try:
        with open(script_module.globals_path(), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and getattr(node.targets[0], "id", None) == "SYNC":
                radio["sync"] = ast.literal_eval(node.value)
    except (OSError, SyntaxError, ValueError):
        pass
    return radio


def medium_flags(physics, links, warnings):
    """The simd flags for the old physics, and a warning for each figure no
    flag carries."""
    flags = ["--noise-figure", store.scalar(float(physics["noise_figure_db"]))]
    bench = physics["capture_model"] == "bench"
    if bench:
        flags.append("--bench-capture")
    else:
        flags.append("--pairwise")
        if float(physics["capture_db"]) != MAIN_SAME_SF_DB:
            warnings.append("capture_db %s: main's capture margin is a fixed %g dB, and no "
                            "flag sets it"
                            % (store.scalar(float(physics["capture_db"])), MAIN_SAME_SF_DB))
    if float(physics["crc_band_db"]):
        warnings.append("crc_band_db %s: main's frames fail against noise by an error curve "
                        "anchored at the datasheet's sensitivity, and no flag sets a CRC band"
                        % store.scalar(float(physics["crc_band_db"])))
    if physics["sf_orthogonality"] == "croce" and not bench:
        warnings.append("sf_orthogonality croce: main's pairwise rule rejects no other "
                        "spreading factor by Croce's table; only its summed rule does")
    elif physics["sf_orthogonality"] == "none" and bench:
        warnings.append("sf_orthogonality none: the old ether ruled on a frame at another "
                        "spreading factor as on one at the same; main always takes Croce's "
                        "table, and no flag turns it off")
    if bench:
        warnings.append("capture_model bench: the old ether took the bench's table against "
                        "each interferer it could hear, --bench-capture against the summed "
                        "interference, frames too weak to hear included: the same for two "
                        "frames above the threshold")
    if float(physics["shadowing_db"]):
        warnings.append("shadowing: the old ether drew a pair's from the seed and its two "
                        "station ids%s; drawn from anything else, the same seed is other ground "
                        "of the same spread" % (", and none for the %d pair(s) with a stated "
                                                "link" % len(links) if links else ""))
    return flags


def convert(data, firmwares, name, where="scenario"):
    """An old scenario's mapping as main's files, in memory: {name, geodata,
    nodeset, setup, medium, report, warnings, summary, check}, the first
    four the files' text. `firmwares` maps an old kind's name to a firmware."""
    store.check_name(name, "nodeset")
    old = read_old(data, where)
    warnings = list(old["notes"])
    used = {}
    for node in old["nodes"].values():
        used[node["kind"]] = used.get(node["kind"], 0) + 1
    missing = [kind for kind in used if kind not in firmwares]
    if missing:
        raise ConvertError("%s: no firmware for kind %s (%s); give --firmware KIND=FIRMWARE "
                           "for each" % (where, ", ".join(missing),
                                         ", ".join("%d node(s) of %s" % (used[k], k)
                                                   for k in missing)))
    for kind in used:
        got, warning = check_firmware(firmwares[kind])
        if warning:
            warnings.append(warning)
        want = MAIN_CATEGORY_OF.get(kind_type(old, kind))
        if got is not None and want and got["category"] != want:
            warnings.append("kind %s (%s) goes to %s, a %s firmware, not a %s one"
                            % (kind, kind_type(old, kind), firmwares[kind], got["category"],
                               want))

    if antennas_module.gain({"type": ANTENNA}, 0.0, 0.0) != 0.0:
        raise ConvertError("the catalogue's %s is no longer 0 dBi at the horizon, which the "
                           "conversion's antennas rest on" % ANTENNA)
    said, left = read_lines(old)
    nodes, xy = positions(old)
    records, powers, roles = {}, {}, {}
    for node_name, node in old["nodes"].items():
        role, radio = said[node_name]
        roles[role] = roles.get(role, 0) + 1
        max_dbm = radio.get("tx_dbm")
        if max_dbm is not None:
            lo, hi = boards_module.CHIP_DBM[0], boards_module.FEM_MAX_DBM
            if not lo <= max_dbm <= hi:
                warnings.append("node %s sent at %g dBm, which main holds to %g"
                                % (node_name, max_dbm, min(max(max_dbm, lo), hi)))
                max_dbm = min(max(max_dbm, lo), hi)
            powers[max_dbm] = powers.get(max_dbm, 0) + 1
        tags = [kind_tag(node["kind"])] + ([TRANSPORT] if role == TRANSPORT else [])
        height = node["height_m"] if node["height_m"] is not None else \
            nodeset_module.DEFAULT_HEIGHT_M
        records[node_name] = nodeset_module.node_record(
            node["id"], nodes[node_name][0], nodes[node_name][1], height, "assumed",
            {"type": ANTENNA}, nodeset_module.check_tags(tags), max_dbm)
    unstated = sum(1 for role, radio in said.values() if radio.get("tx_dbm") is None)
    if unstated:
        warnings.append("%d node(s) state no transmit power; main gives them %d dBm"
                        % (unstated, boards_module.CHIP_MAX_DBM))
    fem = sum(n for dbm, n in powers.items() if boards_module.has_fem(dbm))
    if fem:
        warnings.append("%d node(s) send above %d dBm, so main gives them a front end, "
                        "with its 20 dB of receive gain" % (fem, boards_module.CHIP_MAX_DBM))
    radio_warnings(said, warnings)

    offsets = offsets_of(old)
    links = [{"between": list(between), "loss_db": value, "note": LINK_NOTE}
             for between, value in old["links"].values()]
    flags = medium_flags(old["physics"], links, warnings)
    ground = ground_of(old["physics"], xy)

    comment = ("Converted from the old scenario %s by convert_scenario.py: positions keep\n"
               "the old ether's distances, every antenna is %s (the old 0 dB), gains\n"
               "and walls are offsets, links are as stated." % (name, ANTENNA))
    nodeset_text = nodeset_module.dump({"nodes": records, "offsets": offsets}, comment)
    if links:
        nodeset_text += "links:\n" + "".join(
            "  - { between: [%s, %s], loss_db: %s, note: %s }\n"
            % (store.name_scalar(link["between"][0]), store.name_scalar(link["between"][1]),
               store.scalar(link["loss_db"]), store.scalar(link["note"])) for link in links)
    geodata_text = dump_ground(name, ground)
    setup_text = setup_of(name, old, firmwares, used, left)

    # Read back through main's own readers, and every distance checked on
    # what they read.
    written = nodeset_module.parse(yaml.safe_load(nodeset_text), "%s (nodeset)" % where)
    geodata_module.parse(yaml.safe_load(geodata_text), "%s (geodata)" % where)
    script_module.parse(setup_text, name + ".py")
    check = check_distances(old, xy, written["nodes"])
    if not check["ok"]:
        raise ConvertError("%s: pair %s-%s is %.4f m off its old distance (at most %g m)"
                           % (where, check["worst_pair"][0], check["worst_pair"][1],
                              check["worst_m"], CHECK_TOLERANCE_M))
    if check["great_circle_worst"] > FAR_FROM_GREAT_CIRCLE:
        warnings.append("the old ether's plane was up to %.2f %% off the great circle (its "
                        "origin lies far from the nodes); the old runs' distances are kept"
                        % (100 * check["great_circle_worst"]))

    gain_nodes = sum(1 for node in old["nodes"].values() if node["gain_db"])
    summary = {
        "nodes": len(records), "kinds": dict(used),
        "firmwares": {kind: firmwares[kind] for kind in used},
        "transport": roles.get(TRANSPORT, 0), "client": roles.get("client", 0),
        "role_unstated": roles.get(None, 0), "max_dbm": dict(sorted(powers.items())),
        "power_unstated": unstated, "gain_nodes": gain_nodes,
        "walls": len(old["walls"]), "offsets": len(offsets), "links": len(links),
        "lines_carried": sum(len(lines) for _, _, lines in left),
        "exponent": ground["exponent"], "extent_m": ground["extent_m"],
        "shadowing": SHADOWING_KEYS[0] in ground, "medium": flags,
    }
    out = {"name": name, "geodata": geodata_text, "nodeset": nodeset_text, "setup": setup_text,
           "medium": " ".join(flags) + "\n", "warnings": warnings, "summary": summary,
           "check": check}
    out["report"] = report(out, old, where)
    return out


def positions(old):
    """{node: (lat, lon)} on synthetic ground, and {node: (x, y)} the old
    ether's metres: its own projection, centred on the nodes' centroid."""
    xy = {name: old_metres(old["origin"], *node["pos"]) for name, node in old["nodes"].items()}
    cx = sum(x for x, _ in xy.values()) / len(xy)
    cy = sum(y for _, y in xy.values()) / len(xy)
    return ({name: geodata_module.synthetic_latlon(x - cx, y - cy) for name, (x, y) in xy.items()},
            {name: (x - cx, y - cy) for name, (x, y) in xy.items()})


def offsets_of(old):
    """One offset per pair a wall or a gain touches: the wall's dB, and
    -(G_a + G_b) for the ends' old gains, in id order."""
    nodes = old["nodes"]
    names = sorted(nodes, key=lambda n: nodes[n]["id"])
    walls = {pair: value for pair, (_, value) in old["walls"].items()}
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ga, gb = nodes[a]["gain_db"], nodes[b]["gain_db"]
            wall = walls.get(frozenset((a, b)), 0.0)
            if not (wall or ga or gb):
                continue
            db = wall - ga - gb
            parts = ["wall %s dB" % store.scalar(wall)] if wall else []
            if ga or gb:
                parts.append("old gain_db %s and %s" % (store.scalar(ga), store.scalar(gb)))
            if db:
                out.append({"between": [a, b], "db": db, "note": "; ".join(parts)})
    return out


def ground_of(physics, xy):
    """The synthetic ground: the old exponent, over the square that holds
    every node, and the old shadowing when it had any."""
    half = max(max(abs(x), abs(y)) for x, y in xy.values()) + EXTENT_MARGIN_M
    extent = max(geodata_module.DEFAULT_EXTENT_M,
                 math.ceil(2 * half / EXTENT_STEP_M) * EXTENT_STEP_M)
    out = {"terrain": "flat", "exponent": float(physics["exponent"]), "extent_m": extent}
    if float(physics["shadowing_db"]):
        out["shadowing_db"] = float(physics["shadowing_db"])
        out["shadowing_seed"] = int(physics["shadowing_seed"])
    return out


def dump_ground(name, ground):
    """The geodata file: main's own spelling of the ground, the shadowing
    keys after it."""
    text = geodata_module.dump({geodata_module.SYNTHETIC: {
        k: ground[k] for k in ("terrain", "exponent", "extent_m")}})
    text += "".join("%s: %s\n" % (key, store.scalar(ground[key]))
                    for key in SHADOWING_KEYS if key in ground)
    return ("# Converted from the old scenario %s by convert_scenario.py: its exponent%s.\n"
            % (name, " and its shadowing" if SHADOWING_KEYS[0] in ground else "") + text)


def radio_warnings(said, warnings):
    """A warning where the nodes' old radios part from each other, or from
    scripts/globals.py's, which main gives every node."""
    figures = ("freq_mhz", "sf", "bw_khz", "cr", "sync")
    values = {f: sorted({radio[f] for _, radio in said.values() if f in radio})
              for f in figures + ("preamble",)}
    split = [f for f in figures if len(values[f]) > 1]
    if split:
        warnings.append("the old radios differ among the nodes in %s; main gives every node "
                        "scripts/globals.py's" % ", ".join(split))
    main = globals_radio()
    apart = [f for f in figures if main.get(f) is not None and any(
        not math.isclose(float(v), float(main[f]), abs_tol=1e-6) for v in values[f])]
    if apart:
        warnings.append("the old radio differs from scripts/globals.py in %s: set them there "
                        "for a faithful run" % ", ".join(apart))
    if values["preamble"]:
        warnings.append("preamble %s: startup.py leaves the preamble to the firmware"
                        % ", ".join(str(p) for p in values["preamble"]))


def setup_of(name, old, firmwares, used, left):
    """The nodeset's own setup: each old kind's firmware by its tag, then the
    old first-boot lines nothing else here says, to the same nodes."""
    out = ['"""%s\'s own setup: each node on its old kind\'s firmware, with its old lines."""'
           % name,
           "from sim_mesh import *",
           "",
           "# Converted from an old scenario by convert_scenario.py. A script says its",
           "# .firmware(…) before it includes scripts/startup.py, which includes this, so",
           "# these rules are the last to match a node: a script that means another",
           "# firmware says so after the include."]
    for kind in old["kinds"]:
        if kind in used:
            out.append("nodes(tag=%s).firmware(%s)" % (_quote(kind_tag(kind)),
                                                      _quote(firmwares[kind])))
    if left:
        out += ["",
                "# The old first-boot lines main does not say itself, as the old scenario",
                "# gave them, in its kinds' own languages. startup.py says the role, the",
                "# radio and its power first, and starts the radio after these."]
        for what, which, lines in left:
            target = "nodes(tag=%s)" % _quote(kind_tag(which)) if what == "kind" \
                else "node(%s)" % _quote(which)
            out.append('%s.on_first_boot("""' % target)
            out += ["    %s" % line.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
                    for line in lines]
            out.append('""")')
    return "\n".join(out) + "\n"


def _quote(text):
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"')


def check_distances(old, xy, written):
    """Every pair's distance on synthetic ground, as main reads the written
    nodeset, against the old ether's: the worst difference and its pair,
    and how far the old plane itself was from the great circle."""
    names = sorted(written)
    got = {n: geodata_module.synthetic_xy(written[n]["lat"], written[n]["lon"]) for n in names}
    worst, worst_pair, circle, pairs = 0.0, None, 0.0, 0
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs += 1
            before = math.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1])
            after = math.hypot(got[a][0] - got[b][0], got[a][1] - got[b][1])
            if abs(after - before) >= worst:
                worst, worst_pair = abs(after - before), (a, b)
            true = great_circle_m(old["nodes"][a]["pos"], old["nodes"][b]["pos"])
            if true >= 1.0:
                circle = max(circle, abs(before - true) / true)
    return {"pairs": pairs, "worst_m": worst, "worst_pair": worst_pair,
            "tolerance_m": CHECK_TOLERANCE_M, "ok": worst <= CHECK_TOLERANCE_M,
            "great_circle_worst": circle}


def report(conv, old, where):
    """conversion.txt: what went where, the check, and every warning."""
    s, check = conv["summary"], conv["check"]
    lines = ["The old scenario %s (%s), converted by convert_scenario.py." % (conv["name"], where),
             ""]
    lines.append("nodes      %d: %s" % (s["nodes"], ", ".join(
        "%d %s -> %s" % (n, kind, s["firmwares"][kind]) for kind, n in s["kinds"].items())))
    for kind in s["kinds"]:
        spec = old["kinds"][kind]
        lines.append("           kind %s (%s) ran %s" % (kind, kind_type(old, kind),
                                                         spec.get("elf") or "simd's --elf"))
    lines.append("roles      %d transport (tagged), %d client, %d unstated"
                 % (s["transport"], s["client"], s["role_unstated"]))
    lines.append("power      max_dbm from their lines: %s; %d unstated" % (
        ", ".join("%d at %s dBm" % (n, store.scalar(dbm)) for dbm, n in s["max_dbm"].items())
        or "none", s["power_unstated"]))
    lines.append("antennas   %s on every node; gain_db on %d node(s)" % (ANTENNA, s["gain_nodes"]))
    lines.append("offsets    %d (walls %d, and the gains)" % (s["offsets"], s["walls"]))
    lines.append("links      %d" % s["links"])
    lines.append("first boot %d line(s) carried to the setup as they were, in the old "
                 "kinds' languages" % s["lines_carried"])
    shadowing = ""
    if s["shadowing"]:
        shadowing = ", shadowing %s dB seed %s" % (
            store.scalar(float(old["physics"]["shadowing_db"])), old["physics"]["shadowing_seed"])
    lines.append("ground     synthetic, exponent %s, extent %s m%s"
                 % (store.scalar(s["exponent"]), store.scalar(s["extent_m"]), shadowing))
    lines.append("medium     %s" % " ".join(s["medium"]))
    lines.append("distances  %d pair(s): the old ether's to within %.2g m (at most %g m); "
                 "its plane %.3f %% off the great circle at worst"
                 % (check["pairs"], check["worst_m"], CHECK_TOLERANCE_M,
                    100 * check["great_circle_worst"]))
    if conv["warnings"]:
        lines += ["", "Warnings: what has no equivalent, is not the same, or is not here:"]
        lines += ["- %s" % w for w in conv["warnings"]]
    return "\n".join(lines) + "\n"


def write(conv, out_dir, force=False):
    """The conversion's files under `out_dir`, laid out as testbed/ lays
    them out; the paths written."""
    paths = {os.path.join(out_dir, "geodata", conv["name"] + ".yaml"): conv["geodata"],
             os.path.join(out_dir, "nodesets", conv["name"] + ".yaml"): conv["nodeset"],
             os.path.join(out_dir, "nodesets", conv["name"] + ".py"): conv["setup"],
             os.path.join(out_dir, "medium.txt"): conv["medium"],
             os.path.join(out_dir, "conversion.txt"): conv["report"]}
    if not force:
        there = [p for p in list(paths)[:3] if os.path.exists(p)]
        if there:
            raise ConvertError("%s already there (--force writes over it)" % ", ".join(there))
    for path, text in paths.items():
        store.write_text(path, text)
    return list(paths)


def convert_file(path, firmwares, out_dir, name=None, force=False):
    """Read an old scenario file (`-`: standard input), convert it, write it."""
    if path == "-":
        if not name:
            raise ConvertError("a scenario on standard input needs --name")
        text, where = sys.stdin.read(), "<stdin>"
    else:
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as err:
            raise ConvertError("%s: %s" % (path, err.strerror)) from err
        where = os.path.basename(path)
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as err:
        raise ConvertError("%s: %s" % (where, err)) from err
    name = name or re.sub(r"\.ya?ml$", "", os.path.basename(path))
    conv = convert(data, firmwares, name, where)
    write(conv, out_dir, force)
    return conv


def firmware_mapping(pairs):
    """--firmware's KIND=FIRMWARE pairs as {kind: firmware}."""
    out = {}
    for pair in pairs or []:
        kind, sep, firmware = (part.strip() for part in pair.partition("="))
        if not sep or not kind or not firmware:
            raise ConvertError("--firmware is KIND=FIRMWARE, not %r" % pair)
        out[kind] = firmware
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario", help="the old scenario.yaml, or - for standard input")
    ap.add_argument("--out", required=True, help="where the files go")
    ap.add_argument("--firmware", action="append", metavar="KIND=FIRMWARE",
                    help="the firmware a node of this old kind runs, by name or "
                         "<base>_latest; one per kind")
    ap.add_argument("--name", help="the nodeset's and geodata's name (default: the file's)")
    ap.add_argument("--force", action="store_true", help="write over a conversion there")
    args = ap.parse_args(argv)
    try:
        conv = convert_file(args.scenario, firmware_mapping(args.firmware), args.out, args.name,
                            args.force)
    except (ConvertError, store.StoreError) as err:
        print("convert_scenario: %s" % err, file=sys.stderr)
        return 1
    sys.stdout.write(conv["report"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
