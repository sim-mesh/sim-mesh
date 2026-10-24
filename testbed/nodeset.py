"""A nodeset: which nodes stand where, with what antenna, and what they are.

    testbed/nodesets/<name>.yaml
    testbed/nodesets/<name>.py        its own setup, when it has one (script.py)
    testbed/nodesets/.origin/<name>.yaml   the index it came from, when it did (indexes.py)

    nodes:
      gw-alex: { id: 1, lat: 52.5219, lon: 13.4132, height_m: 38, height_from: roof,
                 max_dbm: 27,
                 antenna: { type: panel_directional, azimuth_deg: 120, elevation_deg: -2 },
                 tags: [transport, gateway] }
      n017: { id: 2, lat: 52.5301, lon: 13.4018, height_m: 15, height_from: assumed,
              antenna: { type: whip_sma_quarter_wave }, tags: [rooftop] }
    offsets:
      - { between: [gw-alex, n017], db: 40, note: "wall, measured 2026-09-20" }

A node is its position, its antenna's height above the ground under it and
where that figure came from (`measured`, `roof`, `raster`, `assumed`), its
maximum power (`max_dbm`, dBm at the antenna connector, 22 when absent and
at most 27; above 22 the node has a front end, boards.py), its antenna
(antennas.py: a type of the catalogue, and a directional one's aim), and its
tags. A node has no board: every node is the one board boards.py
describes. Nothing in it is said to a station: scripts say everything, in
the node's terms, keyed off its tags.
Two tags mean something to the page and the analysis as well:

- a role's name (`transport`, `router`, `repeater`) is the node's role
  (`tag_role`), which the startup script tells it and the map rings;
- `no-radio` is a node with no radio: the startup script sets up every
  other node's, to globals.py's settings, and the page draws coverage and
  links for those alone.

What a node runs is not the nodeset's either: a script says it
(`.firmware(…)`), so one nodeset is run on any firmware.

A nodeset names no geodata. It is offered on every geodata whose extent holds
one of its nodes (`inside`); synthetic ground lies at 0°, 0°.

A node's name is how everything refers to it: scripts, offsets, links,
snapshots and the page. Its id is its network identity, stored and
editable: the station's loopback address and MAC follow from it
(`stations.bind_addr`), so changing it moves the station, and anything
already set up with its old `{id}` or `{addr}` is stale. A new node takes
the lowest id not in use.

**Offsets** are dB added to the computed loss of one pair, both ways, on any
geodata: where a measurement says the model is wrong, and by how much. They
are a layer over the loss table, applied when the medium is given it, so the
table itself (`geometry_hash`: which nodes, where, how high) is cached
without them and an offset never forces a recompute.

**Links** state a pair's loss outright, where a better figure than the
model's is known, measured or worked out elsewhere:

    links:
      - { between: [a, b], loss_db: 131.5, back_db: 133, note: measured }

a→b is `loss_db` and b→a `back_db`, `loss_db` again when it has none, the
same figure in every band with no correction between bands. A link is a
layer too (`losses.with_links`): it stands in for the model's loss and the
geodata's shadowing, and the antennas and any offset still go on top of it.
A file without links has no `links:` key, and is written back without one.
"""

import copy
import csv
import hashlib
import json
import math
import os
from array import array

import yaml

import antennas as antennas_module
import boards as boards_module
import stations as stations_module
import store

HEIGHT_FROM = ("measured", "roof", "raster", "assumed")
# The tags that are a role: a node carrying one forwards for the others.
ROLE_TAGS = ("transport", "router", "repeater")
NO_RADIO = "no-radio"
DEFAULT_HEIGHT_M = 2.0
MERGE_WITHIN_M = 5.0                # two layers' nodes this close are one node
EARTH_RADIUS_M = 6371008.8
KEEP = object()                     # set_node: a fact not given, left as it is
LINK_KEYS = ("between", "loss_db", "back_db", "note")
LINK_FORM = "{ between: [a, b], loss_db, back_db?, note? }"
ORIGINS = ".origin"                 # nodesets/.origin/<name>.yaml: the index it came from


def nodeset_path(name):
    return os.path.join(store.NODESETS_DIR, store.check_name(name, "nodeset") + ".yaml")


def setup_path(name):
    """A nodeset's own setup script, which it may not have."""
    return os.path.splitext(nodeset_path(name))[0] + ".py"


def origin_path(name):
    """Where an index's nodeset says where it came from (indexes.py)."""
    return os.path.join(store.NODESETS_DIR, ORIGINS, store.check_name(name, "nodeset") + ".yaml")


def disk_bytes(name):
    """What a nodeset takes on disk: its file and its own setup script."""
    return store.disk_bytes(nodeset_path(name), setup_path(name))


def names():
    """Every nodeset on disk, by name."""
    return store.listing(store.NODESETS_DIR)


def blank():
    return {"nodes": {}, "offsets": []}


def node_record(node_id, lat, lon, height_m=DEFAULT_HEIGHT_M, height_from="assumed",
                antenna=None, tags=(), max_dbm=None, noise_rise_db=None):
    """One node, in the shape the file and every caller use: `max_dbm` and
    `noise_rise_db` are there only when the node states them. `noise_rise_db`
    is how far the node's place lifts its receiver's noise floor over its
    chip's own (a device beside it, its building): measured, never below 0."""
    out = {"id": int(node_id), "lat": float(lat), "lon": float(lon),
           "height_m": float(height_m), "height_from": str(height_from),
           "antenna": antennas_module.check(antenna, "a node"),
           "tags": list(tags)}
    max_dbm = boards_module.check(max_dbm, "a node")
    if max_dbm is not None:
        out["max_dbm"] = max_dbm
    if noise_rise_db is not None:
        rise = float(noise_rise_db)
        if not math.isfinite(rise) or rise < 0:
            raise store.StoreError("a node's noise_rise_db is a finite rise in dB, 0 or more, not %r"
                                   % (noise_rise_db,))
        out["noise_rise_db"] = rise
    return out


def tag_role(tags):
    """The role a node's tags give it, or None: the first role tag it carries."""
    return next((tag for tag in tags if tag in ROLE_TAGS), None)


def has_radio(node):
    return NO_RADIO not in node["tags"]


# ---- the file ------------------------------------------------------------

def check_tags(tags):
    tags = [str(tag) for tag in (tags or [])]
    for tag in tags:
        store.check_name(tag, "tag")
    return list(dict.fromkeys(tags))


def finite(value, key, where):
    """A nodeset's number as a float, or a StoreError naming its key when it
    is none: a word, or a NaN or an infinity, which YAML and JSON read as
    numbers but no file could be written with (store.scalar)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = math.nan
    if not math.isfinite(out):
        raise store.StoreError("%s: %s is a finite number, not %r" % (where, key, value))
    return out


def parse(data, where):
    """A nodeset file's mapping, checked and filled out.

    Two nodes under one id would be two processes answering the ether as
    one station and two sockets on one address, so that is refused here,
    as is an offset naming a node the nodeset does not have. So is a link
    that is no figure: one without a loss, naming a node there is not,
    from a node to itself, or stating a pair a second time, which leaves
    the pair's loss to whichever came last.
    """
    if not isinstance(data, dict):
        raise store.StoreError("%s: not a nodeset" % where)
    out = blank()
    ids = {}
    for name, node in (data.get("nodes") or {}).items():
        name = str(name)
        store.check_name(name, "node")
        if not isinstance(node, dict):
            raise store.StoreError("%s: node %s is not a mapping" % (where, name))
        try:
            node_id = int(node["id"])
            lat, lon = float(node["lat"]), float(node["lon"])
        except (KeyError, TypeError, ValueError, OverflowError) as err:
            raise store.StoreError("%s: node %s needs id, lat and lon" % (where, name)) from err
        if node_id in ids:
            raise store.StoreError("%s: nodes %s and %s share id %d"
                                   % (where, ids[node_id], name, node_id))
        check_id(node_id)
        ids[node_id] = name
        height_from = str(node.get("height_from") or "assumed")
        if height_from not in HEIGHT_FROM:
            raise store.StoreError("%s: node %s: height_from is one of %s, not %r"
                                   % (where, name, ", ".join(HEIGHT_FROM), height_from))
        here = "%s: node %s" % (where, name)
        if "device" in node:
            raise store.StoreError("%s: a node names no device: a script's .firmware(…) says "
                                   "what each node runs" % here)
        for key in ("radio", "role"):
            if key in node:
                raise store.StoreError(
                    "%s: a node declares no %s: scripts/globals.py sets every radio, a "
                    "`no-radio` tag marks a node without one, and a role is a tag "
                    "(transport, router, repeater)" % (here, key))
        if "board" in node:
            raise store.StoreError("%s: a node has no board: every node is an SX1262, and "
                                   "`max_dbm` is its maximum power" % here)
        out["nodes"][name] = node_record(
            node_id, finite(lat, "lat", here), finite(lon, "lon", here),
            finite(node.get("height_m", DEFAULT_HEIGHT_M), "height_m", here), height_from,
            antennas_module.check(node.get("antenna"), here), check_tags(node.get("tags")),
            boards_module.check(node.get("max_dbm"), here), node.get("noise_rise_db"))
    offsets = data.get("offsets") or []
    if not isinstance(offsets, list):
        raise store.StoreError("%s: offsets is a list, each { between: [a, b], db, note? }"
                               % where)
    for offset in offsets:
        try:
            ends = offset["between"]
            if not isinstance(ends, (list, tuple)):
                raise TypeError("not a list of nodes")      # a string's letters are no nodes
            if len(ends) != 2:
                raise ValueError("not two nodes")
            a, b = (str(n) for n in ends)
            db = float(offset.get("db", 0))
        except (KeyError, TypeError, ValueError) as err:
            raise store.StoreError("%s: an offset is { between: [a, b], db, note? }" % where) from err
        for end in (a, b):
            if end not in out["nodes"]:
                raise store.StoreError("%s: offset names %s, which is not a node" % (where, end))
        if not math.isfinite(db):
            raise store.StoreError("%s: the offset between %s and %s states db %s: an offset "
                                   "is a finite number of dB" % (where, a, b, db))
        entry = {"between": [a, b], "db": db}
        if offset.get("note"):
            entry["note"] = str(offset["note"])
        out["offsets"].append(entry)
    stated = data.get("links") or []
    if not isinstance(stated, list):
        raise store.StoreError("%s: links is a list, each %s" % (where, LINK_FORM))
    links = [check_link(link, out["nodes"], where) for link in stated]
    pairs = set()
    for link in links:
        pair = frozenset(link["between"])
        if pair in pairs:
            raise store.StoreError("%s: the link between %s and %s is stated twice"
                                   % ((where,) + tuple(link["between"])))
        pairs.add(pair)
    if links:
        out["links"] = links
    return out


def check_link(link, nodes, where):
    """One link, checked: its two ends, its loss one way and, when it
    states one, the other.

    A key it does not know is refused rather than passed over: a
    misspelt `back_db` would otherwise make the link the same both ways,
    a wrong number with nothing to say so. A loss is 0 dB or more, and
    one a table's float32 cell can hold, since a figure too large for it
    would silently become never heard.
    """
    if not isinstance(link, dict):
        raise store.StoreError("%s: a link is %s" % (where, LINK_FORM))
    unknown = sorted(str(key) for key in link if key not in LINK_KEYS)
    if unknown:
        raise store.StoreError("%s: a link has no %s: it is %s"
                               % (where, ", ".join(unknown), LINK_FORM))
    try:
        ends = link["between"]
        if not isinstance(ends, (list, tuple)) or len(ends) != 2:
            raise ValueError("not two nodes")
        a, b = (str(n) for n in ends)
        figures = {"loss_db": float(link["loss_db"])}
        if "back_db" in link:
            figures["back_db"] = float(link["back_db"])
    except (KeyError, TypeError, ValueError) as err:
        raise store.StoreError("%s: a link is %s" % (where, LINK_FORM)) from err
    for end in (a, b):
        if end not in nodes:
            raise store.StoreError("%s: link names %s, which is not a node" % (where, end))
    if a == b:
        raise store.StoreError("%s: a link joins two nodes, not %s to itself" % (where, a))
    for key, value in figures.items():
        if not (value >= 0 and math.isfinite(array("f", [value])[0])):
            raise store.StoreError("%s: the link between %s and %s states %s %s: a loss is "
                                   "a finite number of dB, 0 or more" % (where, a, b, key, value))
    entry = {"between": [a, b], **figures}
    if link.get("note"):
        entry["note"] = str(link["note"])
    return entry


def check_id(node_id):
    limit = stations_module.max_node_id()
    if not 1 <= node_id <= limit:
        raise store.StoreError("a node id is 1 to %d on the network %s, not %d"
                               % (limit, stations_module.NET, node_id))


def read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = store.load_yaml(handle) or {}
    except (OSError, yaml.YAMLError) as err:
        raise store.StoreError("%s: %s" % (path, err)) from err
    return parse(data, path)


def dump_antenna(antenna):
    parts = ["type: %s" % antenna["type"]]
    for key in ("azimuth_deg", "elevation_deg"):
        if key in antenna:
            parts.append("%s: %s" % (key, store.scalar(antenna[key])))
    return "{ %s }" % ", ".join(parts)


def dump_node(name, node):
    """One node as one line, its keys in the order the format documents."""
    parts = ["id: %d" % node["id"], "lat: %s" % store.scalar(node["lat"]),
             "lon: %s" % store.scalar(node["lon"]),
             "height_m: %s" % store.scalar(node["height_m"]),
             "height_from: %s" % node["height_from"]]
    if node.get("max_dbm") is not None:
        parts.append("max_dbm: %s" % store.scalar(node["max_dbm"]))
    parts.append("antenna: %s" % dump_antenna(node["antenna"]))
    parts.append("tags: %s" % store.flow(node["tags"]))
    return "  %s: { %s }" % (store.name_scalar(name), ", ".join(parts))


def dump(data, comment=None):
    """The nodeset as the file, in the shape the format documents.

    Hand-composed rather than emitted by the YAML writer so that a node
    stays one readable line, in id order: the file is meant to be opened and
    edited, not only round-tripped. `comment` lines go first, each behind a `#`.
    """
    out = ["# %s" % line for line in (comment or "").splitlines()]
    nodes = data.get("nodes") or {}
    out.append("nodes:" if nodes else "nodes: {}")
    out += [dump_node(name, node)
            for name, node in sorted(nodes.items(), key=lambda kv: kv[1]["id"])]
    offsets = data.get("offsets") or []
    out.append("offsets:" if offsets else "offsets: []")
    for offset in offsets:
        note = ", note: %s" % store.scalar(offset["note"]) if offset.get("note") else ""
        out.append("  - { between: [%s, %s], db: %s%s }"
                   % (store.name_scalar(offset["between"][0]),
                      store.name_scalar(offset["between"][1]), store.scalar(offset["db"]), note))
    links = data.get("links") or []
    if links:
        out.append("links:")
    for link in links:
        parts = ["between: %s" % store.flow(link["between"]),
                 "loss_db: %s" % store.scalar(link["loss_db"])]
        if "back_db" in link:
            parts.append("back_db: %s" % store.scalar(link["back_db"]))
        if link.get("note"):
            parts.append("note: %s" % store.scalar(link["note"]))
        out.append("  - { %s }" % ", ".join(parts))
    return "\n".join(out) + "\n"


def write(path, data, comment=None):
    store.write_text(path, dump(data, comment))


def comment_of(path):
    """The comment lines a nodeset file starts with, without their `#`: what
    a save over it keeps, since the page edits the nodes and not the prose."""
    lines = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("#"):
                    break
                text = line.rstrip("\n")[1:]
                lines.append(text[1:] if text.startswith(" ") else text)
    except OSError:
        return None
    return "\n".join(lines) or None


def geometry_hash(data):
    """What the loss table depends on, hashed: which nodes, where and how
    high. Sixteen hex digits of SHA-256 over a canonical spelling of those
    alone, so the same geometry always has the same key."""
    nodes = sorted(
        (name, round(node["lat"], 7), round(node["lon"], 7), round(node["height_m"], 3))
        for name, node in (data.get("nodes") or {}).items())
    text = json.dumps({"nodes": nodes}, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---- the loaded nodeset --------------------------------------------------

class Nodeset:
    """One nodeset being looked at or edited, and whether it is dirty.

    Everything that changes the nodeset goes through a method here, and
    every one of them sets `dirty`, which is the whole of the unsaved-changes
    rule. `path` is where Save writes; a nodeset copied into a run has its
    run's path and no name of its own until saved as one.
    """

    def __init__(self, name, data, path=None):
        self.name = name
        self.data = data
        self.path = path or (nodeset_path(name) if name else None)
        self.dirty = False

    @property
    def nodes(self):
        """Node name -> its record."""
        return self.data["nodes"]

    @property
    def offsets(self):
        return self.data["offsets"]

    @property
    def links(self):
        """The pairs whose loss is stated, [] when the file states none."""
        return self.data.get("links") or []

    def node(self, name):
        node = self.nodes.get(name)
        if node is None:
            raise store.StoreError("no node called %r" % name)
        return node

    def by_id(self, node_id):
        """The name of the node with this id, or None."""
        return next((name for name, node in self.nodes.items() if node["id"] == node_id), None)

    def geometry_hash(self):
        return geometry_hash(self.data)

    def offset(self, a, b):
        """The dB added between two nodes, 0 when there is none."""
        pair = {a, b}
        return sum(o["db"] for o in self.offsets if set(o["between"]) == pair)

    def inside(self, bbox):
        """How many nodes stand inside [lon0, lat0, lon1, lat1]."""
        lon0, lat0, lon1, lat1 = bbox
        return sum(1 for n in self.nodes.values()
                   if lat0 <= n["lat"] <= lat1 and lon0 <= n["lon"] <= lon1)

    def tags(self):
        """Every tag a node carries, with how many carry it."""
        count = {}
        for node in self.nodes.values():
            for tag in node["tags"]:
                count[tag] = count.get(tag, 0) + 1
        return dict(sorted(count.items()))

    def next_id(self):
        """The lowest station id this nodeset is not already using.

        A removed node's id comes back, because nothing is left that answers
        to it; a node keeps its id for as long as it exists, so its address
        and directory stay put.
        """
        taken = {node["id"] for node in self.nodes.values()}
        limit = stations_module.max_node_id()
        for candidate in range(1, limit + 1):
            if candidate not in taken:
                return candidate
        raise store.StoreError("a nodeset holds at most %d nodes on the network %s"
                               % (limit, stations_module.NET))

    # ---- edits -----------------------------------------------------------

    def add_node(self, name, lat, lon, **fields):
        """Place a new node; `fields` are any of `set_node`'s, and a node
        given no id takes the lowest free one."""
        store.check_name(name, "node")
        if name in self.nodes:
            raise store.StoreError("there is already a node called %r" % name)
        where = "node %s" % name
        self.nodes[name] = node_record(self.next_id(), finite(lat, "lat", where),
                                       finite(lon, "lon", where))
        try:
            self.set_node(name, **fields)
        except store.StoreError:
            del self.nodes[name]
            raise
        self.dirty = True
        return self.nodes[name]

    def remove_node(self, name):
        self.node(name)
        del self.nodes[name]
        self.data["offsets"] = [o for o in self.offsets if name not in o["between"]]
        if "links" in self.data:
            links = [link for link in self.links if name not in link["between"]]
            if links:
                self.data["links"] = links
            else:
                del self.data["links"]
        self.dirty = True

    def rename_node(self, name, new):
        """Give a node another name, and carry its offsets and links over."""
        node = self.node(name)
        store.check_name(new, "node")
        if new in self.nodes:
            raise store.StoreError("there is already a node called %r" % new)
        self.data["nodes"] = {(new if key == name else key): value
                              for key, value in self.nodes.items()}
        for pair in self.offsets + self.links:
            pair["between"] = [new if end == name else end for end in pair["between"]]
        self.dirty = True
        return node

    def move_node(self, name, lat, lon):
        node = self.node(name)
        where = "node %s" % name
        node["lat"], node["lon"] = finite(lat, "lat", where), finite(lon, "lon", where)
        self.dirty = True

    def set_node(self, name, id=None, lat=None, lon=None, height_m=None, height_from=None,
                 antenna=None, tags=None, max_dbm=KEEP):
        """Change any of a node's facts; None leaves one as it is, except
        `max_dbm`, which None clears (the node then sends at most 22 dBm)
        and which is left as it is when not given.

        Returns True when the id changed: the station then has a new address,
        and one with state must be restarted for it to take.
        """
        node = self.node(name)
        where = "node %s" % name
        changed_id = False
        node_id = None if id is None else int(finite(id, "id", where))
        if node_id is not None and node_id != node["id"]:
            check_id(node_id)
            other = self.by_id(node_id)
            if other is not None:
                raise store.StoreError("id %d is already %s's" % (node_id, other))
            node["id"] = node_id
            changed_id = True
        if lat is not None:
            node["lat"] = finite(lat, "lat", where)
        if lon is not None:
            node["lon"] = finite(lon, "lon", where)
        if height_m is not None:
            node["height_m"] = finite(height_m, "height_m", where)
        if height_from is not None:
            if height_from not in HEIGHT_FROM:
                raise store.StoreError("height_from is one of %s" % ", ".join(HEIGHT_FROM))
            node["height_from"] = height_from
        if antenna is not None:
            node["antenna"] = antennas_module.check(antenna, where)
        if max_dbm is not KEEP:
            max_dbm = boards_module.check(max_dbm, where)
            if max_dbm is None:
                node.pop("max_dbm", None)
            else:
                node["max_dbm"] = max_dbm
        if tags is not None:
            node["tags"] = check_tags(tags)
        self.dirty = True
        return changed_id

    def set_offset(self, a, b, db, note=None):
        """Set the dB added between two nodes; 0 removes it."""
        self.node(a), self.node(b)
        db = finite(db, "db", "the offset between %s and %s" % (a, b))
        pair = {a, b}
        self.data["offsets"] = [o for o in self.offsets if set(o["between"]) != pair]
        if db:
            entry = {"between": [a, b], "db": db}
            if note:
                entry["note"] = str(note)
            self.offsets.append(entry)
        self.dirty = True

    # ---- saving ----------------------------------------------------------

    def save(self):
        if not self.path:
            raise store.StoreError("this nodeset has no file yet: save it as a name")
        write(self.path, self.data)
        self.dirty = False

    def save_as(self, name):
        """Write the nodeset under a new name, which becomes this one's."""
        path = nodeset_path(name)
        if os.path.exists(path):
            raise store.StoreError("there is already a nodeset called %r" % name)
        self.name, self.path = name, path
        self.save()

    def copy(self, path=None):
        """An independent copy, for a run to edit without touching this one."""
        return Nodeset(self.name, copy.deepcopy(self.data), path or self.path)

    def as_dict(self):
        """What the page is told about the nodeset. Its links, when it has
        any, go along for the page to hand back on a save; it edits none."""
        out = {"name": self.name, "dirty": self.dirty,
               "geometry_hash": self.geometry_hash(),
               "nodes": copy.deepcopy(self.nodes),
               "offsets": copy.deepcopy(self.offsets)}
        if self.links:
            out["links"] = copy.deepcopy(self.links)
        return out


def load(name):
    path = nodeset_path(name)
    if not os.path.isfile(path):
        raise store.StoreError("no nodeset called %r" % name)
    return Nodeset(name, read(path), path)


def open_path(path, name=None):
    """A nodeset file anywhere (a run's or a snapshot's copy)."""
    return Nodeset(name, read(path), path)


def create(name):
    """An empty nodeset, written at once so it has a file."""
    path = nodeset_path(name)
    if os.path.exists(path):
        raise store.StoreError("there is already a nodeset called %r" % name)
    ns = Nodeset(name, blank(), path)
    ns.save()
    return ns


def delete(name):
    """A nodeset gone, with its own setup script (`nodesets/<name>.py`) and
    the note of the index it came from."""
    path = nodeset_path(name)
    if not os.path.isfile(path):
        raise store.StoreError("no nodeset called %r" % name)
    os.remove(path)
    for other in (setup_path(name), origin_path(name)):
        if os.path.isfile(other):
            os.remove(other)


def summary(name):
    """What a listing says of one nodeset: its node count, extent and tags."""
    ns = load(name)
    lats = [n["lat"] for n in ns.nodes.values()]
    lons = [n["lon"] for n in ns.nodes.values()]
    return {"name": name, "nodes": len(ns.nodes),
            "bbox": [min(lons), min(lats), max(lons), max(lats)] if lats else None,
            "tags": ns.tags()}


def unique_name(base, taken):
    """`base`, or `base-2`, `base-3`… cut to a name's length, whichever
    `taken` does not hold."""
    name = base[:32].rstrip("-")
    suffix = 2
    while name in taken:
        tail = "-%d" % suffix
        name = base[:32 - len(tail)].rstrip("-") + tail
        suffix += 1
    return name


def lowest_free_id(taken):
    node_id = 1
    while node_id in taken:
        node_id += 1
    return node_id


def distance_m(a, b):
    """Metres between two nodes, on a sphere of the Earth's mean radius:
    at the few metres merging asks about, the ellipsoid changes nothing."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (a["lat"], a["lon"], b["lat"], b["lon"]))
    return EARTH_RADIUS_M * math.hypot((lon2 - lon1) * math.cos((lat1 + lat2) / 2), lat2 - lat1)


def merge(layers):
    """Several nodesets as one, as the Nodes tab's Save selection as writes it.

    `layers` is [(nodeset name, file mapping)], the earliest in the Nodes
    tab's list first. One layer is its own mapping unchanged. Of several, every node
    keeps its tags and gains its layer's name as one; a node within
    MERGE_WITHIN_M of a node of an earlier layer is that node, the earlier
    layer's, and is left out; a name an earlier layer took gets the layer's
    name appended (and a number, should that be taken too); an id taken gets
    the lowest free one. An offset or a link comes along where both its ends
    do.
    """
    layers = [(layer, parse(data, "layer %s" % layer)) for layer, data in layers]
    if len(layers) == 1:
        return layers[0][1]
    out = blank()
    ids = set()
    for layer, data in layers:
        earlier = list(out["nodes"].values())
        renamed = {}
        for name, node in sorted(data["nodes"].items(), key=lambda item: item[1]["id"]):
            if any(distance_m(node, other) <= MERGE_WITHIN_M for other in earlier):
                continue
            new = name if name not in out["nodes"] else unique_name("%s-%s" % (name, layer),
                                                                    out["nodes"])
            node = copy.deepcopy(node)
            if node["id"] in ids:
                node["id"] = lowest_free_id(ids)
            node["tags"] = check_tags(node["tags"] + [layer])
            out["nodes"][new] = node
            ids.add(node["id"])
            renamed[name] = new
        for offset in data["offsets"]:
            a, b = offset["between"]
            if a in renamed and b in renamed:
                out["offsets"].append(dict(copy.deepcopy(offset), between=[renamed[a], renamed[b]]))
        for link in data.get("links") or []:
            a, b = link["between"]
            if a in renamed and b in renamed:
                out.setdefault("links", []).append(
                    dict(copy.deepcopy(link), between=[renamed[a], renamed[b]]))
    return out


# ---- imports -------------------------------------------------------------

def _rows(path):
    """A planner CSV's rows as dicts. A header written as a `#` comment
    (sites.csv's) is a header all the same."""
    with open(path, encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip()]
    if lines and lines[0].startswith("#"):
        lines[0] = lines[0].lstrip("#").strip() + "\n"
    reader = csv.DictReader(lines)
    reader.fieldnames = [field.strip() for field in reader.fieldnames or ()]
    return [{key: (value or "").strip() for key, value in row.items() if key} for row in reader]


def _number(text):
    """A finite number from a cell or a parsed value, else None."""
    if text in (None, "") or isinstance(text, bool):
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _board_at(power):
    """The maximum power of a node whose CSV row states a transmit power:
    that power, held to the range the board has."""
    return None if power is None else \
        max(float(boards_module.CHIP_DBM[0]), min(float(power), float(boards_module.FEM_MAX_DBM)))


def import_sites_csv(path, height_m=DEFAULT_HEIGHT_M, prefix="site"):
    """The planner optimiser's `sites.csv` as a nodeset.

    Columns `lat, lon, tx_power_dbm, surface_masl, cs_neighbours`. The file
    has no antenna height, so every site gets `height_m`, marked assumed.
    A transmit power the file states is the node's maximum power.
    """
    data = blank()
    for index, row in enumerate(_rows(path), 1):
        lat, lon = _number(row.get("lat")), _number(row.get("lon"))
        if lat is None or lon is None:
            continue
        name = "%s-%03d" % (prefix, index)
        data["nodes"][name] = node_record(
            len(data["nodes"]) + 1, lat, lon, height_m, "assumed",
            max_dbm=_board_at(_number(row.get("tx_power_dbm"))))
    return data


def from_imported(rows, source, height_m=15.0):
    """Nodes `planner-job nodes-import` read from a public node map, as a
    nodeset.

    Each row is {label, lat, lon, kind, position, height_m?}. A node is named
    after its label, made usable and unique, else `<source>-<n>`; it
    stands at the row's height where the source gives one (measured) and
    else at `height_m` (assumed), and its one tag is its kind.
    """
    data = blank()
    for row in rows:
        node_id = len(data["nodes"]) + 1
        name = unique_name(store.slug(row.get("label"), "%s-%d" % (source, node_id)), data["nodes"])
        height = row.get("height_m")
        kind = store.slug(row.get("kind"), "")
        data["nodes"][name] = node_record(
            node_id, row["lat"], row["lon"], height if height is not None else height_m,
            "measured" if height is not None else "assumed",
            tags=[kind] if kind else [])
    return data


# What planner's height estimate rested on (`/height.json`'s `basis`), as a
# node's `height_from`: a roof under it, or the rasters around it. An estimate
# on no evidence is no better than the height already assumed.
HEIGHT_FROM_BASIS = {"lod2-building": "roof", "clutter-neighbourhood": "raster",
                     "class-typical": "raster"}


def with_estimated_heights(data, estimates):
    """In a nodeset file mapping, every node whose height is assumed given the
    height planner's estimator found for it (`estimates`: {name: a
    `/height.json` reply}), to the decimetre, and `roof` or `raster` for what
    it rested on. A measured, roof or raster height is kept, and so is a node
    with no estimate or one on no evidence. Returns the names changed."""
    changed = []
    for name, node in data["nodes"].items():
        got = estimates.get(name)
        if node.get("height_from", "assumed") != "assumed" or not got:
            continue
        source = HEIGHT_FROM_BASIS.get(got.get("basis"))
        if source is None:
            continue
        node["height_m"] = round(float(got["h_agl_m"]), 1)
        node["height_from"] = source
        changed.append(name)
    return changed


def import_nodes_csv(path, height_m=15.0):
    """The deployed-network CSV `planner nodes import` writes, as a nodeset.

    Header `id, name, kind, lat, lon, height_agl_m, tx_power_dbm,
    last_seen_unix`. A node is named after its own label, made usable (lower
    case, hyphens) and made unique; its kind becomes a tag. `height_agl_m`
    where the operator gave one is taken as measured; else `height_m`,
    marked assumed. A transmit power the file states is the node's maximum
    power.
    """
    data = blank()
    for row in _rows(path):
        lat, lon = _number(row.get("lat")), _number(row.get("lon"))
        if lat is None or lon is None:
            continue
        node_id = len(data["nodes"]) + 1
        name = unique_name(store.slug(row.get("name"), "n%03d" % node_id), data["nodes"])
        height = _number(row.get("height_agl_m"))
        kind = store.slug(row.get("kind"), "")
        data["nodes"][name] = node_record(
            node_id, lat, lon, height if height is not None else height_m,
            "measured" if height is not None else "assumed",
            max_dbm=_board_at(_number(row.get("tx_power_dbm"))), tags=[kind] if kind else [])
    return data


# ---- points from other formats ---------------------------------------------
#
# Each reader gives points, {label, lat, lon, height_m?, max_dbm?, tags},
# and `from_points` makes the nodeset: a node named after its label made
# usable and unique (else `<source>-<n>`), at the point's height above the
# ground where the format gives one (measured) and else at `height_m`
# (assumed), tagged with the format and the point's own tags. A height a
# format gives above sea level (GeoJSON's third coordinate, GPX's `ele`, a
# Meshtastic altitude) is not a height above the ground, and is passed over.

POINT_FORMATS = ("csv", "geojson", "kml", "gpx", "meshtastic")
CSV_COLUMNS = ("lat", "lon", "name", "height", "power", "tags")


def from_points(points, source, height_m=15.0):
    """Points as a nodeset; one off the globe is left out, and none at all
    is refused."""
    data = blank()
    for point in points:
        lat, lon = point.get("lat"), point.get("lon")
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        node_id = len(data["nodes"]) + 1
        name = unique_name(store.slug(point.get("label"), "%s-%d" % (source, node_id)),
                           data["nodes"])
        height = point.get("height_m")
        tags = [source] + [store.slug(t, "") for t in point.get("tags") or ()]
        data["nodes"][name] = node_record(
            node_id, lat, lon, height if height is not None else height_m,
            "measured" if height is not None else "assumed",
            max_dbm=_board_at(point.get("max_dbm")),
            tags=check_tags(t for t in tags if t))
    if not data["nodes"]:
        raise store.StoreError("the %s file has no point with a position" % source)
    return data


def _tag_list(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [t for t in str(value or "").replace(";", ",").split(",") if t.strip()]


def csv_points(text, columns):
    """Any CSV's rows as points, its columns named by `columns`: {lat, lon,
    name?, height?, power?, tags?}, each a header of the file. The delimiter
    is whichever of `,`, `;` and tab the header has most of; a header written
    as a `#` comment is a header all the same; tags are split at `,` or `;`."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise store.StoreError("the CSV is empty")
    lines[0] = lines[0].lstrip("#").strip()
    delimiter = max((",", ";", "\t"), key=lines[0].count)
    reader = csv.DictReader(lines, delimiter=delimiter)
    header = [field.strip() for field in reader.fieldnames or ()]
    reader.fieldnames = header
    columns = {k: v for k, v in (columns or {}).items() if k in CSV_COLUMNS and v}
    for key in ("lat", "lon"):
        if key not in columns:
            raise store.StoreError("name the CSV's %s column" % ("latitude" if key == "lat"
                                                                 else "longitude"))
    missing = [v for v in columns.values() if v not in header]
    if missing:
        raise store.StoreError("the CSV has no column %s" % ", ".join(missing))
    points = []
    for row in reader:
        def cell(key):
            return (row.get(columns[key]) or "").strip() if key in columns else ""
        points.append({"label": cell("name"), "lat": _number(cell("lat")),
                       "lon": _number(cell("lon")), "height_m": _number(cell("height")),
                       "max_dbm": _number(cell("power")), "tags": _tag_list(cell("tags"))})
    return points


def geojson_points(text):
    """A GeoJSON FeatureCollection's, Feature's or geometry's points (Point
    and MultiPoint). A feature's `name`, `title` or `label` is the label; its
    `height_m` or `height_agl_m` the height; its `tags` (a list, or text split
    at commas) and `role` are tags."""
    try:
        data = json.loads(text)
    except ValueError as err:
        raise store.StoreError("not GeoJSON: %s" % err) from err
    features = []
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif isinstance(data, dict) and data.get("type") == "Feature":
        features = [data]
    elif isinstance(data, dict) and data.get("coordinates") is not None:
        features = [{"geometry": data, "properties": {}}]
    points = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        coords = geometry.get("coordinates")
        spots = [coords] if geometry.get("type") == "Point" else \
            coords if geometry.get("type") == "MultiPoint" else []
        for spot in spots or ():
            if not isinstance(spot, (list, tuple)) or len(spot) < 2:
                continue
            points.append({
                "label": props.get("name") or props.get("title") or props.get("label"),
                "lon": _number(spot[0]), "lat": _number(spot[1]),
                "height_m": _number(props.get("height_m", props.get("height_agl_m"))),
                "max_dbm": _number(props.get("max_dbm", props.get("tx_power_dbm"))),
                "tags": _tag_list(props.get("tags")) + ([str(props["role"])] if props.get("role")
                                                        else [])})
    return points


def _xml(text, what):
    import xml.etree.ElementTree as ET
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as err:
        raise store.StoreError("not %s: %s" % (what, err)) from err


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(element, name):
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def kml_points(text):
    """A KML file's placemarks with a point. A point whose altitudeMode is
    relativeToGround gives its altitude as the height above the ground;
    absolute and clamped altitudes do not."""
    root = _xml(text, "KML")
    points = []
    for mark in root.iter():
        if _local(mark.tag) != "Placemark":
            continue
        for point in mark.iter():
            if _local(point.tag) != "Point":
                continue
            parts = _child_text(point, "coordinates").replace("\n", " ").split()
            if not parts:
                continue
            values = [_number(v) for v in parts[0].split(",")]
            if len(values) < 2:
                continue
            relative = _child_text(point, "altitudeMode") == "relativeToGround"
            points.append({"label": _child_text(mark, "name"), "lon": values[0], "lat": values[1],
                           "height_m": values[2] if relative and len(values) > 2 else None,
                           "tags": []})
    return points


def gpx_points(text):
    """A GPX file's waypoints: each `wpt` with its name, and its `type` as a
    tag."""
    root = _xml(text, "GPX")
    points = []
    for wpt in root.iter():
        if _local(wpt.tag) != "wpt":
            continue
        kind = _child_text(wpt, "type")
        points.append({"label": _child_text(wpt, "name"), "lat": _number(wpt.get("lat")),
                       "lon": _number(wpt.get("lon")), "tags": [kind] if kind else []})
    return points


def meshtastic_points(text):
    """A Meshtastic node list: `meshtastic --info`'s "Nodes in mesh:" JSON,
    or that JSON alone, a mapping by node id or a list. A node is named after
    its long name, else its short name or id, and tagged with its role; one
    without a position (or at 0°, 0°, a fix never had) is left out."""
    data = None
    start = text.find("Nodes in mesh:")
    try:
        if start >= 0:
            brace = text.index("{", start)
            data, _ = json.JSONDecoder().raw_decode(text[brace:])
        else:
            data = json.loads(text)
    except ValueError as err:
        raise store.StoreError("not a Meshtastic node list: %s" % err) from err
    nodes = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    points = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        user = node.get("user") or {}
        pos = node.get("position") or {}
        lat, lon = pos.get("latitude"), pos.get("longitude")
        if lat is None and pos.get("latitudeI") is not None:
            lat = pos["latitudeI"] * 1e-7
        if lon is None and pos.get("longitudeI") is not None:
            lon = pos["longitudeI"] * 1e-7
        lat, lon = _number(lat), _number(lon)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        role = user.get("role")
        points.append({"label": user.get("longName") or user.get("shortName") or user.get("id"),
                       "lat": lat, "lon": lon, "tags": [str(role)] if role else []})
    return points


def import_points(fmt, text, height_m=15.0, columns=None):
    """A file of one of POINT_FORMATS as a nodeset."""
    if fmt == "csv":
        points = csv_points(text, columns)
    elif fmt == "geojson":
        points = geojson_points(text)
    elif fmt == "kml":
        points = kml_points(text)
    elif fmt == "gpx":
        points = gpx_points(text)
    elif fmt == "meshtastic":
        points = meshtastic_points(text)
    else:
        raise store.StoreError("a file of points is one of %s" % ", ".join(POINT_FORMATS))
    return from_points(points, fmt, height_m)
