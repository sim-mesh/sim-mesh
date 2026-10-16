"""The `reticulum` category: what every Reticulum firmware's driver answers.

```
script ── node(n).reticulum.lxmf.send(to, text) ──► simd: mid ──► lxmf.send(dest, text, mid) ──► station
station console ── its own id and its fate ──► driver.console_line ──► lxmf_status(mid, …)
traffic report ◄── events.jsonl: lxmf.message.status ◄── simd
```

A firmware of category `reticulum` ships a driver (`sim_mesh.driver`) whose
DRIVER subclasses `ReticulumDriver` and implements these verbs, beside every
firmware's (`Driver`: name, radio, radio_up, tx_power, diagnostics). Each
takes the station first; a verb a firmware cannot do raises `CommandError`
(`self.cannot(verb)` makes one), and the default here does exactly that. A
verb with a dot is the method with an underscore: `lxmf.send` is
`lxmf_send`. A script reaches them as `<selection>.reticulum.<verb>`.

    role(station, role)                 "transport" (forwards for others) or "client"
    path(station, dest=None, iface=None)
                                        its path table: [{dest, next_hop, iface,
                                        hops}], the entries for `dest` (32 hex
                                        digits) and on `iface` (as the firmware
                                        names it) when given, all without
    peer_tcp(station, addr, port)       a TCP link to another station
    current_role(station)               what it does now, one of ROLES, or None

and LXMF's, where an identity is named by its display name (the name its
announces carry), which a run keeps unique:

    lxmf.create(station, name)          one more LXMF identity, named `name`,
                                        unless the station has one by that
                                        name; returns its delivery address,
                                        or None while it has none yet. Never
                                        refuses, so a script may say it every
                                        time: a firmware with one identity,
                                        there from its start and named after
                                        the node, gives it `name` instead, and
                                        once it has another name does nothing
                                        and returns None.
    lxmf.identities(station)            [(name, delivery address)], the one it
                                        sends from unless told otherwise
                                        first; [] while it has none
    lxmf.announce(station, name=None)   announce the delivery destination of
                                        its identity `name` (None: the first)
    lxmf.send(station, dest, text, mid, sender=None)
                                        an LXMF message to `dest` (32 hex
                                        digits) from its identity `sender`
                                        (None: the first); `mid` is the
                                        message's id, sim-mesh's
    lxmf.send_route(station, dest, routes, text, mid, sender=None)
                                        T185: `lxmf.send`, along up to
                                        ROUTE_ALTS_MAX ordered concrete
                                        routes (`routes`: a list of routes,
                                        each a list of 4-byte hop tags as 8
                                        hex digits — CMD_LXMF_SEND_ROUTE
                                        0xC6, reticulum-node's T184 branch).
                                        Answered and reported exactly as
                                        `lxmf.send` is; a firmware with no
                                        way to send one this way raises
                                        `self.cannot("lxmf.send_route")`,
                                        the default here.

**What became of a message** is reported against `mid`, from `console_line`
or however else the driver learns it:

    self.lxmf_status(station, mid, "pending" | "sent" | "delivered" | "failed",
                     why=<text>)

which is `station.report("lxmf.message.status", mid=, status=, why=)`; the
last status a message reached is what a traffic report shows for one that
never arrived. A firmware has ids of its own, which it may give a message
only once it is built (after a path has been found, say): a driver couples
one to sim-mesh's when it learns it (`lxmf_couple`), and reports what the
station says of its own id with `lxmf_native`, which holds what comes before
the coupling and reports it once it is made. What goes wrong before the
firmware has an id is reported against `mid` directly.
"""

from collections import OrderedDict

from sim_mesh.driver import CommandError, Driver  # noqa: F401 - a driver's imports

ROLES = ("transport", "client")
STATUSES = ("pending", "sent", "delivered", "failed")
EVENT = "lxmf.message.status"
ID_EVENT = "lxmf.message.id"
RECEIVED_EVENT = "lxmf.message.received"
ORPHANS_MAX = 256           # a station's statuses for ids not coupled yet, kept

# ---- T185: CMD_LXMF_SEND_ROUTE's wire frame -----------------------------
#
# A firmware driver that can reach its station's door directly (no tool of
# its own builds this frame yet) uses these: the same KISS byte-stuffing
# every rncfg invocation already speaks (reticulum-hdlc::encode_frame_raw),
# and the payload reticulum-rnode-proto::CMD_LXMF_SEND_ROUTE documents
# (mirrored by reticulum-node's own test, tests/scenarios.rs::
# send_route_payload): `dest(16) ‖ n_routes(1) ‖ routes ‖ content`, each
# route `n_hops(1) ‖ tags(n_hops × 4B)`. Kept here, the category's own
# driver interface, rather than in one firmware's driver, so any reticulum
# firmware's driver may reuse it rather than re-deriving it.
FEND, FESC, FEND_T, FESC_T = 0xC0, 0xDB, 0xDC, 0xDD
CMD_LXMF_SEND_ROUTE = 0xC6
CHAIN_HOPS_MAX = 8          # reticulum-node::route::CHAIN_HOPS_MAX
ROUTE_ALTS_MAX = 4          # reticulum-node::ROUTE_ALTS_MAX


def kiss_encode(cmd, payload):
    """One KISS frame: `FEND cmd payload(byte-stuffed) FEND`, `cmd` a whole
    byte (an RNode opcode, not a TNC command/port nibble pair)."""
    out = bytearray((FEND, cmd))
    for b in payload:
        if b == FEND:
            out += bytes((FESC, FEND_T))
        elif b == FESC:
            out += bytes((FESC, FESC_T))
        else:
            out.append(b)
    out.append(FEND)
    return bytes(out)


def kiss_decode(frame):
    """(cmd, payload) out of one complete `FEND … FEND` frame, its framing
    bytes included; None for one too short to hold a command byte. Only
    `payload` is unescaped: `cmd` is the byte right after the opening
    FEND, written raw by `kiss_encode` (and by reticulum-hdlc's own
    `encode_frame_raw`), never run through byte-stuffing itself."""
    body = frame[1:-1] if frame[:1] == bytes((FEND,)) and frame[-1:] == bytes((FEND,)) \
        else frame
    if not body:
        return None
    cmd, rest = body[0], body[1:]
    out = bytearray()
    esc = False
    for b in rest:
        if esc:
            out.append(FEND if b == FEND_T else FESC if b == FESC_T else b)
            esc = False
        elif b == FESC:
            esc = True
        else:
            out.append(b)
    return cmd, bytes(out)


def take_kiss_frame(buf):
    """The first complete `FEND … FEND` frame in `buf`, decoded, and what is
    left of `buf` after it: (`None`, `buf`) while none is complete yet.
    Bytes before a frame opens (there should be none, here) are dropped
    with it, as a station's own reader tolerates them too."""
    start = buf.find(bytes((FEND,)))
    if start < 0:
        return None, buf
    end = buf.find(bytes((FEND,)), start + 1)
    if end < 0:
        return None, buf[start:]
    return kiss_decode(bytes(buf[start:end + 1])), buf[end + 1:]


def route_send_payload(dest, routes, content):
    """`CMD_LXMF_SEND_ROUTE`'s payload: `dest(16) ‖ n_routes(1) ‖ routes ‖
    content`, each route `n_hops(1) ‖ tags(n_hops × 4B)`. `dest` and every
    tag are raw bytes (16 and 4 of them), `content` raw bytes too.
    `ValueError` for more routes than [`ROUTE_ALTS_MAX`] or more hops in one
    than [`CHAIN_HOPS_MAX`], which the engine would refuse anyway
    (`ERR_BAD_VALUE`) — caught here instead of on the air."""
    if len(dest) != 16:
        raise ValueError("a destination hash is 16 bytes, not %d" % len(dest))
    if len(routes) > ROUTE_ALTS_MAX:
        raise ValueError("%d routes is more than ROUTE_ALTS_MAX (%d)"
                         % (len(routes), ROUTE_ALTS_MAX))
    out = bytearray(dest)
    out.append(len(routes))
    for route in routes:
        if len(route) > CHAIN_HOPS_MAX:
            raise ValueError("%d hops is more than CHAIN_HOPS_MAX (%d)"
                             % (len(route), CHAIN_HOPS_MAX))
        out.append(len(route))
        for tag in route:
            if len(tag) != 4:
                raise ValueError("a hop tag is 4 bytes, not %d" % len(tag))
            out += bytes(tag)
    out += bytes(content)
    return bytes(out)


class ReticulumDriver(Driver):
    category = "reticulum"
    VERBS = Driver.VERBS + ("role", "path", "peer_tcp", "current_role",
                            "lxmf.create", "lxmf.identities", "lxmf.announce", "lxmf.send",
                            "lxmf.send_route")

    def __init__(self, firmware):
        super().__init__(firmware)
        self.coupled = {}       # station name -> {firmware's id: mid}
        self.orphans = {}       # station name -> OrderedDict(firmware's id -> [(status, why)])

    async def role(self, station, role):
        raise self.cannot("role")

    async def path(self, station, dest=None, iface=None):
        raise self.cannot("path")

    async def peer_tcp(self, station, addr, port=4965):
        raise self.cannot("peer_tcp")

    async def current_role(self, station):
        return None

    async def lxmf_create(self, station, name):
        raise self.cannot("lxmf.create")

    async def lxmf_identities(self, station):
        raise self.cannot("lxmf.identities")

    async def lxmf_announce(self, station, name=None):
        raise self.cannot("lxmf.announce")

    async def lxmf_send(self, station, dest, text, mid, sender=None):
        raise self.cannot("lxmf.send")

    async def lxmf_send_route(self, station, dest, routes, text, mid, sender=None):
        raise self.cannot("lxmf.send_route")

    # ---- what became of a message ----------------------------------------

    def lxmf_status(self, station, mid, status, why=None):
        """Report a message's status under sim-mesh's id."""
        fields = {"mid": mid, "status": status}
        if why:
            fields["why"] = why
        station.report(EVENT, **fields)

    def lxmf_couple(self, station, mid, native):
        """The firmware's id `native` is sim-mesh's `mid`: what the station
        said of it before now is reported."""
        self.coupled.setdefault(station.name, {})[native] = mid
        for status, why in self.orphans.get(station.name, {}).pop(native, []):
            if status == ID_EVENT:
                station.report(ID_EVENT, mid=mid, message_id=why)
            else:
                self.lxmf_status(station, mid, status, why)

    def lxmf_coupled(self, station, native):
        """sim-mesh's id for the firmware's `native`, or None."""
        return self.coupled.get(station.name, {}).get(native)

    def lxmf_native(self, station, native, status, why=None):
        """A status the station gave under its own id: reported, or held
        until the id is coupled. True when it was reported."""
        mid = self.lxmf_coupled(station, native)
        if mid is not None:
            self.lxmf_status(station, mid, status, why)
            return True
        held = self.orphans.setdefault(station.name, OrderedDict())
        held.setdefault(native, []).append((status, why))
        while len(held) > ORPHANS_MAX:
            held.popitem(last=False)
        return False

    def lxmf_message_id(self, station, native, message_id):
        """The LXMF message id of the station's message `native`: reported
        as `lxmf.message.id` under sim-mesh's id, or held until the coupling."""
        mid = self.lxmf_coupled(station, native)
        if mid is not None:
            station.report(ID_EVENT, mid=mid, message_id=message_id)
            return
        held = self.orphans.setdefault(station.name, OrderedDict())
        held.setdefault(native, []).append((ID_EVENT, message_id))
        while len(held) > ORPHANS_MAX:
            held.popitem(last=False)

    def lxmf_received(self, station, message_id, sender=None, refused=None):
        """The station received the LXMF message `message_id`; with
        `refused`, why it did not take it (a full mailbox, say)."""
        fields = {"message_id": message_id}
        if sender:
            fields["sender"] = sender
        if refused:
            fields["refused"] = refused
        station.report(RECEIVED_EVENT, **fields)

    def lxmf_uncoupled(self, station):
        """The firmware's ids the station has spoken of that are not coupled
        yet, oldest first."""
        return list(self.orphans.get(station.name, {}))
