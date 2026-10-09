"""Drivers, host side: a run's firmware resolved and each one's driver loaded.

```
script nodes().firmware("<base>_latest") ──► resolve_builds ──► firmware.resolve
run.yaml builds: {"<base>_latest": {firmware: <name>, …}}    what each name ran on
load(build) ──► import <firmware dir>/<driver>.py ──► DRIVER(firmware) ──► a station's driver
```

A run names firmware as scripts do, by name or `<base>_latest`; each is
resolved once, when a rule first names it, to an installed firmware
(firmware.py), and the run keeps what it resolved to in `run.yaml`'s
`builds`, so a paused run and a snapshot say which firmware wrote their
state. The driver is the firmware's own module (sim_mesh.driver); this
imports it from the firmware's directory under a module name of its own, so
two firmwares' drivers never meet.

`env` is the station contract's environment, which sim-mesh gives every
station before the firmware's `env` and its driver's: the node's identity,
directory, address and ether, its board, the virtual radio's library, and in
a virtual-time run the time shim.

Lines keep `{name}`, `{id}`, `{addr}` and `{addr:<node>}` macros until a
station is given them (`expand`).
"""

import importlib.util
import os
import re
import sys

import firmware as firmware_module
import stations as stations_module
from sim_mesh.driver import CommandError, Driver
from sim_mesh.meshcore.driver import MeshcoreDriver
from sim_mesh.meshtastic.driver import MeshtasticDriver
from sim_mesh.reticulum.driver import ReticulumDriver

HERE = os.path.dirname(os.path.abspath(__file__))
RADIO_BUILD = os.path.normpath(os.path.join(HERE, "..", "radio", "build"))
# The time shim every station of a virtual-time run is started with.
SHIM = os.path.join(RADIO_BUILD, "libsimclock.so")
CATEGORY_CLASSES = {"reticulum": ReticulumDriver, "meshcore": MeshcoreDriver,
                    "meshtastic": MeshtasticDriver}
MACRO_RE = re.compile(r"\{([a-z_]+)(?::([A-Za-z0-9_.-]+))?\}")
BUILD_KEYS = ("firmware", "base", "category", "radio", "title", "hardware", "version", "arch",
              "dir", "exec", "driver", "fixed", "env")


def radio_library(radio):
    """The virtual radio's shared library sim-mesh provides, by node.yaml's
    `radio` (`sx1262`)."""
    return os.path.join(RADIO_BUILD, "libsimradio-%s.so" % str(radio).lower())


# ---- macros --------------------------------------------------------------

def expand(line, name, node_id, ids=None, extra=None):
    """Fill `{name}`, `{id}` and `{addr}` in one line for one station, and
    `{addr:<node>}` with another node's address; `extra` is more of them by
    name (`{max_dbm}`, the node's maximum power).

    A macro this does not define, or a node `ids` does not have, is left
    exactly as written: a line is somebody's text and may legitimately
    contain braces."""
    values = {"name": name, "id": str(node_id), "addr": stations_module.bind_addr(node_id)}
    values.update({k: str(v) for k, v in (extra or {}).items()})

    def fill(m):
        key, node = m.group(1), m.group(2)
        if node is None:
            return values.get(key, m.group(0))
        if key == "addr" and node in (ids or {}):
            return stations_module.bind_addr(ids[node])
        return m.group(0)
    return MACRO_RE.sub(fill, line)


def expand_all(lines, name, node_id, ids=None, extra=None):
    """The lines a station is actually given: expanded, minus blanks and comments."""
    out = []
    for line in lines:
        line = expand(str(line), name, node_id, ids, extra).strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# ---- the contract's environment ----------------------------------------------

def env(station, build):
    """The station's environment: the contract, then node.yaml's `env`."""
    out = {"SIM_MESH_NODE_ID": str(station.node_id),
           "SIM_MESH_NODE_DIR": station.dir,
           "SIM_MESH_BIND_ADDR": station.addr,
           "SIM_MESH_ETHER": station.ether_addr}
    if build.get("radio"):
        lib = radio_library(build["radio"])
        out["SIM_MESH_RADIO_LIB"] = lib
        here = os.environ.get("LD_LIBRARY_PATH")
        out["LD_LIBRARY_PATH"] = os.path.dirname(lib) + (":" + here if here else "")
    if station.clock is not None:
        out.update(SIM_MESH_TIME="virtual",
                   SIM_MESH_EPOCH_US=str(station.clock.epoch),
                   SIM_MESH_SEED=str(station.clock.seed),
                   LD_PRELOAD=SHIM)
    if getattr(station, "board", None):
        out["SIM_MESH_BOARD"] = station.board
    profile = getattr(station, "clock_profile", None)
    if station.clock is not None and profile:
        out["SIM_MESH_CLOCK_PROFILE"] = profile
    short_waits = getattr(station, "short_waits", None)
    if station.clock is not None and short_waits:
        out["SIM_MESH_SHORT_WAITS"] = short_waits
    for key, value in (build.get("env") or {}).items():
        if key == "LD_LIBRARY_PATH" and out.get(key):
            # The firmware's own libraries first, the radio's still found.
            value = value + ":" + out[key]
        out[key] = value
    return out


# ---- resolving and loading ---------------------------------------------------

def resolve_builds(refs, firmware_dir=None):
    """Where each firmware name a run uses runs from: {name: build}, which a
    run keeps in run.yaml as its `builds`. A name that resolves to nothing
    installed raises CommandError naming it."""
    try:
        return {ref: build_of(firmware_module.resolve(ref, firmware_dir))
                for ref in sorted(set(refs))}
    except firmware_module.FirmwareError as err:
        raise CommandError(str(err)) from err


def build_of(got):
    """What a run keeps of a resolved firmware."""
    out = {k: got.get(k) for k in BUILD_KEYS if k != "firmware"}
    out["firmware"] = got["name"]
    return out


def build_present(build):
    """Whether a kept build's firmware is still installed."""
    return bool(build) and bool(build.get("exec")) and os.path.isfile(build["exec"]) \
        and bool(build.get("driver")) and os.path.isfile(build["driver"])


def load(build):
    """A firmware's driver, from its own module."""
    path = build["driver"]
    category = CATEGORY_CLASSES.get(build.get("category"))
    if category is None:
        raise CommandError("firmware %s: category %r has no driver interface here (there "
                           "are %s)" % (build["firmware"], build.get("category"),
                                        ", ".join(sorted(CATEGORY_CLASSES))))
    module_name = "sim_mesh_firmware_%s" % re.sub(r"\W", "_", build["firmware"])
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:
        raise CommandError("firmware %s: its driver %s cannot be imported"
                           % (build["firmware"], path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as err:     # noqa: BLE001 - the firmware's code, reported as it failed
        sys.modules.pop(module_name, None)
        raise CommandError("firmware %s: its driver failed to load: %s: %s"
                           % (build["firmware"], type(err).__name__, err)) from err
    cls = getattr(module, "DRIVER", None)
    if not (isinstance(cls, type) and issubclass(cls, category)):
        raise CommandError("firmware %s: its driver's DRIVER is not a %s"
                           % (build["firmware"], category.__name__))
    return cls(build)


def load_all(builds):
    """A driver per firmware name, from a run's `builds`."""
    return {ref: load(build) for ref, build in (builds or {}).items()}


def label(build):
    """What the page calls a firmware: its title, and the hardware it plays."""
    title = build.get("title") or build.get("firmware")
    return "%s (virtual %s)" % (title, build["hardware"]) if build.get("hardware") else title


def verbs(category=None):
    """A category's verbs; with none, those every firmware has."""
    if category is None:
        return Driver.VERBS
    cls = CATEGORY_CLASSES.get(category)
    return cls.VERBS if cls else ()


__all__ = ["CommandError", "Driver", "expand", "expand_all", "env", "resolve_builds",
           "build_present", "load", "load_all", "label", "verbs", "CATEGORY_CLASSES"]
