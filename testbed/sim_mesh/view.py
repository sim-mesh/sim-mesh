"""A run directory as the analysis tools see it.

Everything a tool needs beyond the record comes from the run, never from
the nodeset or geodata files as they stand now: the run's own nodeset copy
(with the edits made during the run), its geodata copy, the builds it
resolved, and the loss tables the ether read.

- **Names.** The record knows stations by id; the nodeset maps id to node
  name (`names`) and back (`ids`).
- **Positions** are the geodata's metres (`geodata.to_xy`): synthetic
  ground's nautical-mile metres, or a pack's easting and northing. A pack's
  copy points at its pack, which has to be where it points.
- **Radio, role.** A node's radio is the run's `globals.py` at the node's
  maximum power, unless it is tagged `no-radio`; its role is its role tag,
  a node with none a `client`. What a script set beyond them is not seen
  here.
- **Category.** A node's category is its firmware's, as the run resolved it;
  what its frames mean belongs to that category's protocol
  (`sim_mesh.protocol_for`).
- **Levels** are the medium's own: an `Ether` holding the run's tables with
  the nodeset's links, the geodata's shadowing, the nodeset's antennas (over
  the grounds the run kept) and offsets on them (`losses.medium_tables`),
  the names, and the noise figure from
  the run's `physics` (the ether's default when the run names none), asked
  through the ether's own `level` and `audible`. Nothing here recomputes a
  loss from positions.
"""

import collections
import math
import os
import sys

import boards as boards_module
import losses as losses_module
import nodeset as nodeset_module
import runs
import store

sys.path.insert(0, os.path.join(store.SIM_DIR, "..", "ether"))
import ether as ether_module  # noqa: E402 - the path is set just above
import slt  # noqa: E402

import sim_mesh  # noqa: E402
from sim_mesh import record as record_module  # noqa: E402


class RunView:
    """One run, opened for analysis."""

    def __init__(self, directory):
        self.run = runs.open_run(directory)
        self.dir = self.run.dir
        self.nodeset = self.run.nodeset()
        self.nodes = self.nodeset.nodes
        self.builds = self.run.meta.get("builds") or {}
        self.names = {int(n["id"]): name for name, n in self.nodes.items()}
        self.ids = {name: int(n["id"]) for name, n in self.nodes.items()}
        self._geodata = None
        self._positions = None
        self._medium = None
        self._shared = None

    @property
    def record_path(self):
        return record_module.path_in(self.dir)

    def geodata(self):
        if self._geodata is None:
            self._geodata = self.run.geodata()
        return self._geodata

    # ---- geometry --------------------------------------------------------

    def positions(self):
        """Station id -> (x, y) in the geodata's metres."""
        if self._positions is None:
            gd = self.geodata()
            self._positions = {int(n["id"]): gd.to_xy(n["lat"], n["lon"])
                               for n in self.nodes.values()}
        return self._positions

    def distance(self, a, b):
        """Metres between two stations, by id, in the geodata's plane."""
        pos = self.positions()
        return math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])

    # ---- what each node is ----------------------------------------------

    def build(self, name):
        """What the node's firmware name resolved to (drivers.build_of), or {}."""
        ref = (self.run.meta.get("firmware") or {}).get(name)
        return self.builds.get(ref) or {}

    def category(self, name):
        return self.build(name).get("category")

    def base(self, name):
        """The firmware's base: the project it is, whatever its build."""
        return self.build(name).get("base")

    def protocol(self, name):
        return sim_mesh.protocol_for(self.category(name))

    def protocols(self):
        """The protocol modules any node of the run is read by."""
        found = []
        for name in self.nodes:
            module = self.protocol(name)
            if module is not None and module not in found:
                found.append(module)
        return found

    def shared_radio(self):
        """The run's globals.py radio (runs.Run.radio)."""
        if self._shared is None:
            self._shared = self.run.radio()
        return self._shared

    def radio(self, name):
        """Slot 0 of a node as the startup script sets it: freq_hz, sf,
        bw_hz from the run's globals.py, power_dbm the node's maximum
        (boards.max_dbm)."""
        shared = self.shared_radio()
        return {"freq_hz": int(round(shared["freq_mhz"] * 1e6)), "sf": shared["sf"],
                "bw_hz": int(round(shared["bw_khz"] * 1e3)),
                "power_dbm": boards_module.max_dbm(self.nodes[name].get("max_dbm"))}

    def has_radio(self, name):
        """False for a node tagged `no-radio`."""
        return nodeset_module.has_radio(self.nodes[name])

    def roles(self):
        """Station id -> role, for every node: its role tag, else client."""
        return {sid: nodeset_module.tag_role(self.nodes[name]["tags"]) or "client"
                for name, sid in self.ids.items()}

    def forwarders(self):
        """The station ids whose role forwards for others."""
        return {sid for sid, role in self.roles().items() if role in sim_mesh.FORWARDING}

    def calling_hz(self):
        """The carrier globals.py sets: the calling channel."""
        return int(round(self.shared_radio()["freq_mhz"] * 1e6))

    # ---- the medium ------------------------------------------------------

    def medium(self):
        """An `Ether` with the run's tables with their layers, names and noise
        figure, for its `level` and `audible`; it carries no traffic."""
        if self._medium is None:
            e = ether_module.Ether.__new__(ether_module.Ether)
            e.physics = ether_module.Physics.from_dict(self.run.meta.get("physics"))
            e.stations = {}
            tables = {band: slt.Table.read(self.run.table_path(band))
                      for band in self.run.bands()}
            e.set_losses(losses_module.medium_tables(tables, self.geodata(), self.nodeset,
                                                     self.run.meta.get("grounds")), self.ids)
            e.set_chips({})     # the run records no chips: every receiver reads the default chip's floor
            e.set_noise_rises({int(n["id"]): n["noise_rise_db"] for n in self.nodes.values()
                               if "noise_rise_db" in n})
            self._medium = e
        return self._medium

    def has_table(self, freq_hz):
        return slt.band_for(freq_hz) in self.run.bands()

    def audible(self, a, b, freq_hz=None):
        """True when station b decodes station a on this carrier (a's own by
        default) at a's power, SF and bandwidth (`radio`)."""
        r = self.radio(self.names[a])
        e = self.medium()
        level = e.level(a, b, freq_hz or r["freq_hz"], r["power_dbm"])
        return e.audible(level, r["bw_hz"], r["sf"], b)

    def radio_graph(self, freq_hz=None):
        """Station id -> the ids that decode it, per `audible`; a node
        without a radio is in it as neither."""
        adj = collections.defaultdict(set)
        radios = [sid for sid, name in self.names.items() if self.has_radio(name)]
        for a in radios:
            for b in radios:
                if a != b and self.audible(a, b, freq_hz):
                    adj[a].add(b)
        return adj
