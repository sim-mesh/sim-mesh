"""T185: the online map of stationary nodes, the user-path builder, and the
CMD_LXMF_SEND_ROUTE driver verb's encoding — against a stand-in simulation
(sim_mesh.library, sim_mesh.traffic), and the wire-level pieces on their own.
"""

import asyncio
import contextlib
import io
import json
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sim_mesh  # noqa: E402
from sim_mesh import library  # noqa: E402
from sim_mesh import traffic as rtraffic  # noqa: E402
from sim_mesh.reticulum import delivery  # noqa: E402
from sim_mesh.reticulum import driver as rdriver  # noqa: E402


# ---- the wire: KISS framing and CMD_LXMF_SEND_ROUTE's payload -------------

def test_route_send_payload_matches_the_engines_own_test_vector():
    # fw/sim-mesh/driver.py's own unit test, a_station_accepts_cmd_lxmf_send_route:
    # dest(16, 0xAA) || n_routes(1)=1 || n_hops(1)=1 || tag(4, 0xB2) || b"hi".
    payload = rdriver.route_send_payload(bytes([0xAA] * 16), [[bytes([0xB2] * 4)]], b"hi")
    assert payload == bytes([0xAA] * 16) + bytes([1, 1, 0xB2, 0xB2, 0xB2, 0xB2]) + b"hi"


def test_route_send_payload_matches_reticulum_nodes_scenario_test():
    # reticulum-node/tests/scenarios.rs::send_route_payload, the two-relay
    # chain (A -> R1 -> R2 -> B): dest || n_routes=1 || n_hops=2 || tag(R1)
    # || tag(R2) || content.
    dest = bytes(range(16))
    tag_r1, tag_r2 = bytes([0x11] * 4), bytes([0x22] * 4)
    payload = rdriver.route_send_payload(dest, [[tag_r1, tag_r2]], b"via the stored chain")
    assert payload == dest + bytes([1, 2]) + tag_r1 + tag_r2 + b"via the stored chain"


def test_route_send_payload_several_alternative_routes():
    dest = bytes(16)
    routes = [[bytes([1] * 4)], [bytes([2] * 4), bytes([3] * 4)], [], [bytes([4] * 4)]]
    payload = rdriver.route_send_payload(dest, routes, b"x")
    assert payload == (dest + bytes([4])
                       + bytes([1]) + bytes([1] * 4)
                       + bytes([2]) + bytes([2] * 4) + bytes([3] * 4)
                       + bytes([0])
                       + bytes([1]) + bytes([4] * 4)
                       + b"x")


def test_route_send_payload_refuses_what_the_engine_would():
    with pytest.raises(ValueError, match="16 bytes"):
        rdriver.route_send_payload(bytes(15), [], b"")
    with pytest.raises(ValueError, match="ROUTE_ALTS_MAX"):
        rdriver.route_send_payload(bytes(16), [[bytes(4)]] * (rdriver.ROUTE_ALTS_MAX + 1), b"")
    with pytest.raises(ValueError, match="CHAIN_HOPS_MAX"):
        rdriver.route_send_payload(bytes(16), [[bytes(4)] * (rdriver.CHAIN_HOPS_MAX + 1)], b"")
    with pytest.raises(ValueError, match="4 bytes"):
        rdriver.route_send_payload(bytes(16), [[bytes(3)]], b"")


def test_kiss_encode_decode_round_trip_escapes_fend_and_fesc():
    payload = bytes([rdriver.FEND, rdriver.FESC, 1, 2, 3])
    frame = rdriver.kiss_encode(0xC6, payload)
    assert frame[0] == rdriver.FEND and frame[1] == 0xC6 and frame[-1] == rdriver.FEND
    # the stuffed bytes never contain a bare FEND until the closing one
    assert rdriver.FEND not in frame[2:-1]
    assert rdriver.kiss_decode(frame) == (0xC6, payload)


def test_kiss_cmd_byte_is_never_unescaped_even_when_it_is_fesc_valued():
    # A cmd byte that happens to equal FESC (0xDB) must not be eaten by the
    # payload's own unescape state machine.
    frame = rdriver.kiss_encode(0xDB, bytes([7]))
    assert rdriver.kiss_decode(frame) == (0xDB, bytes([7]))


def test_take_kiss_frame_splits_two_frames_glued_together():
    buf = rdriver.kiss_encode(0x99, bytes([rdriver.FEND, rdriver.FEND, rdriver.FESC])) \
        + rdriver.kiss_encode(0x88, b"x")
    first, rest = rdriver.take_kiss_frame(buf)
    second, rest2 = rdriver.take_kiss_frame(rest)
    assert first == (0x99, bytes([rdriver.FEND, rdriver.FEND, rdriver.FESC]))
    assert second == (0x88, b"x")
    assert rest2 == b""


def test_take_kiss_frame_says_none_for_an_incomplete_frame():
    partial = bytes((rdriver.FEND, 0xC6, 1, 2))
    frame, rest = rdriver.take_kiss_frame(partial)
    assert frame is None and rest == partial


# ---- the user-path builder -------------------------------------------------

BERLIN_SENDER = (52.5000, 13.0000)
BERLIN_DEST = (52.5040, 13.0000)
RELAYS = [
    {"name": "a", "lat": 52.5010, "lon": 13.0000, "height_m": 10.0, "tag": "aaaaaaaa"},
    {"name": "b", "lat": 52.5020, "lon": 13.0000, "height_m": 10.0, "tag": "bbbbbbbb"},
    {"name": "c", "lat": 52.5030, "lon": 13.0000, "height_m": 10.0, "tag": "cccccccc"},
    {"name": "d", "lat": 52.4990, "lon": 13.0000, "height_m": 10.0, "tag": "dddddddd"},
]


def test_build_routes_greedy_relays_toward_the_destination():
    routes = library.build_routes(BERLIN_SENDER, BERLIN_DEST, RELAYS, range_m=250,
                                  alternatives=2)
    names = [[hop["name"] for hop in route] for route in routes]
    # route 0: the single nearest-to-dest relay in range reaches dest from there.
    assert names[0] == ["b"]
    # route 1: the next-best alternative at the first hop, then greedy from there.
    assert names[1] == ["a", "b"]
    assert library.route_tags(routes) == [["bbbbbbbb"], ["aaaaaaaa", "bbbbbbbb"]]


def test_build_routes_never_reuses_a_hop_on_its_own_route():
    routes = library.build_routes(BERLIN_SENDER, BERLIN_DEST, RELAYS, range_m=250,
                                  alternatives=4, max_routes=4)
    for route in routes:
        names = [hop["name"] for hop in route]
        assert len(names) == len(set(names))


def test_build_routes_stops_short_when_no_relay_is_in_range():
    # Too short a range to reach any relay at all: an empty route, for the
    # engine's own fallback to ordinary routing — never a crash or a loop.
    routes = library.build_routes(BERLIN_SENDER, BERLIN_DEST, RELAYS, range_m=10,
                                  alternatives=2)
    assert routes == [[], []]


def test_build_routes_caps_routes_at_alternatives_not_just_max_routes():
    routes = library.build_routes(BERLIN_SENDER, BERLIN_DEST, RELAYS, range_m=250,
                                  alternatives=1, max_routes=4)
    assert len(routes) == 1


def test_build_routes_direct_reach_needs_no_hop():
    close_dest = (52.5001, 13.0000)
    routes = library.build_routes(BERLIN_SENDER, close_dest, RELAYS, range_m=250,
                                  alternatives=2)
    assert routes == [[], []]


# ---- a stand-in simulation (sim_mesh.attach's own protocol) ----------------

class FakeSim:
    """Just enough of simd's control socket: a snapshot of configurable
    nodes (lat, lon, tags), and `lxmf.identities` / `lxmf.send_route` /
    `lxmf.send` / `path` answered from a verb table."""

    def __init__(self, nodes, identities=None):
        self.nodes = nodes                      # [{name, id, tags, lat, lon, height_m}, ...]
        self.identities = identities or {}       # name -> [(name, address), ...]
        self.got = []

    def reply(self, m, name):
        verb = m.get("verb")
        if verb == "lxmf.identities":
            return self.identities.get(name, [])
        if verb in ("lxmf.send", "lxmf.send_route"):
            return "%032x" % (hash((name, verb)) & (2**128 - 1))
        if verb == "path":
            return []
        if verb == "lxmf.announce":
            return None
        return None

    async def handle(self, request):
        from aiohttp import web
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "snapshot", "clock": {"t": 1_000_000},
                            "run": {"dir": "/runs/fake"},
                            "nodes": [dict({"status": "up", "firmware": "alpha_latest",
                                            "category": "reticulum", "base": "alpha"}, **n)
                                     for n in self.nodes]})
        async for msg in ws:
            m = json.loads(msg.data)
            if m["type"] in ("drive", "yield"):
                continue
            self.got.append(m)
            if m["type"] == "wait":
                await ws.send_json({"type": "command_result", "id": m.get("id"), "results": {},
                                    "t": 2_000_000})
            elif m["type"] in ("firmware", "first_boot"):
                await ws.send_json({"type": "command_result", "id": m.get("id"), "results": {},
                                    "t": 2_000_000})
            elif m["type"] == "sequence":
                who = m.get("names") or [n["name"] for n in self.nodes]
                await ws.send_json({"type": "command_result", "id": m.get("id"), "t": 2_000_000,
                                    "results": [{n: [] for n in who},
                                                {n: self.reply(m["steps"][1], n) for n in who}]})
            elif m["type"] in ("command", "meta"):
                who = [m["name"]] if m.get("name") else m.get("names") or \
                    [n["name"] for n in self.nodes]
                results = {n: self.reply(m, n) for n in who}
                await ws.send_json({"type": "command_result", "id": m.get("id"),
                                    "name": m.get("name"), "t": 2_000_000, "results": results})
        return ws


def with_fake_sim(fake, drive):
    from aiohttp import web

    async def go():
        app = web.Application()
        app.router.add_get("/ws", fake.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        sim = await sim_mesh.attach("fake", port)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return await drive(sim)
        finally:
            await sim.close()
            await sim.session.close()
            await runner.cleanup()
    return asyncio.run(go())


@pytest.fixture
def fresh_runtime(monkeypatch):
    """A script's own hold on a simulation, fresh for one test (as
    test_analysis.py's test_a_script_says_it_synchronously sets one up),
    serving `fake` over a background thread's event loop."""
    def start(fake):
        from aiohttp import web

        loop = asyncio.new_event_loop()
        started = threading.Event()
        port = []

        async def serve():
            app = web.Application()
            app.router.add_get("/ws", fake.handle)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port.append(site._server.sockets[0].getsockname()[1])
            started.set()

        server = threading.Thread(target=lambda: (loop.run_until_complete(serve()),
                                                   loop.run_forever()), daemon=True)
        server.start()
        started.wait(10)
        fresh = library.Runtime()
        monkeypatch.setattr(library, "runtime", fresh)
        fresh.configure(script_name="t185-test", sim="fake", port=port[0])
        return fresh, loop

    created = []

    def factory(fake):
        fresh, loop = start(fake)
        created.append((fresh, loop))
        return fresh

    yield factory
    for fresh, loop in created:
        fresh.close()
        loop.call_soon_threadsafe(loop.stop)


ONLINE_NODES = [
    {"name": "n01", "id": 1, "tags": ["online"], "lat": 52.50, "lon": 13.00, "height_m": 5.0},
    {"name": "n02", "id": 2, "tags": ["gateway"], "lat": 52.51, "lon": 13.01, "height_m": 5.0},
    {"name": "n03", "id": 3, "tags": [], "lat": 52.52, "lon": 13.02, "height_m": 5.0},
    {"name": "n04", "id": 4, "tags": [], "lat": 52.53, "lon": 13.03, "height_m": 5.0},
]
IDENTITIES = {"n01": [("n01", "1" * 32)], "n02": [("n02", "2" * 32)]}


def test_online_map_reads_tagged_nodes_and_truncates_their_address(fresh_runtime):
    fake = FakeSim(ONLINE_NODES, IDENTITIES)
    fresh_runtime(fake)
    with contextlib.redirect_stdout(io.StringIO()):
        result = library.online_map()
    assert result == [{"name": "n01", "lat": 52.50, "lon": 13.00, "height_m": 5.0,
                       "tag": "1" * 8}]


def test_online_map_also_reads_a_runtime_marked_node(fresh_runtime):
    fake = FakeSim(ONLINE_NODES, IDENTITIES)
    fresh_runtime(fake)
    with contextlib.redirect_stdout(io.StringIO()):
        library.node("n02").reticulum.online()
        result = library.online_map()
    names = sorted(e["name"] for e in result)
    assert names == ["n01", "n02"]
    assert next(e for e in result if e["name"] == "n02")["tag"] == "2" * 8


def test_online_map_skips_a_node_with_no_identity_yet(fresh_runtime):
    fake = FakeSim(ONLINE_NODES, {"n01": []})
    fresh_runtime(fake)
    with contextlib.redirect_stdout(io.StringIO()):
        result = library.online_map()
    assert result == []


def test_online_map_reader_eligibility_by_tag_and_by_mark(fresh_runtime):
    fake = FakeSim(ONLINE_NODES, IDENTITIES)
    fresh_runtime(fake)
    with contextlib.redirect_stdout(io.StringIO()):
        assert library.online_map(for_station="n03") is None     # no tag, not marked
        assert library.online_map(for_station="n02") is not None  # tag="gateway"
        library.node("n03").reticulum.user_configured()
        assert library.online_map(for_station="n03") is not None  # now marked


def test_online_mark_is_not_a_first_boot_rule(fresh_runtime):
    fake = FakeSim(ONLINE_NODES, IDENTITIES)
    fresh_runtime(fake)
    with pytest.raises(library.ScriptError, match="not a first-boot rule"):
        library.Node.reticulum.online()


def test_send_route_verb_reaches_simd_with_hex_tags(fresh_runtime):
    fake = FakeSim(ONLINE_NODES, IDENTITIES)
    fresh_runtime(fake)
    with contextlib.redirect_stdout(io.StringIO()):
        routes = [["aaaaaaaa", "bbbbbbbb"], ["cccccccc"]]
        result = library.node("n01").reticulum.lxmf.send_route("n02", routes, "hello")
    assert "n01" in result
    sent = [m for m in fake.got if m.get("type") == "meta" and m.get("verb") == "lxmf.send_route"]
    assert len(sent) == 1
    assert sent[0]["args"] == {"to": "n02", "routes": routes, "text": "hello"}
    assert sent[0]["names"] == ["n01"] and sent[0]["category"] == "reticulum"


# ---- traffic.py: routed sends ----------------------------------------------

def test_traffic_driver_sends_by_route_when_routes_names_the_pair(tmp_path):
    fake = FakeSim([{"name": "n01", "id": 1, "tags": [], "lat": 0, "lon": 0, "height_m": 0},
                    {"name": "n02", "id": 2, "tags": [], "lat": 0, "lon": 0, "height_m": 0}],
                   {"n01": [("n01", "1" * 32)], "n02": [("n02", "2" * 32)]})
    out = tmp_path / "out.json"
    # Only two stations, so the schedule's one draw is one of these two
    # (src, dst) orders; both get the same routes, so either draw proves
    # the plumbing the same way.
    opts = {"warm_rounds": 0, "settle_every": 1, "traffic": 6, "every": 5, "drain": 0,
           "routes": {("n01", "n02"): [["cafefeed"]], ("n02", "n01"): [["cafefeed"]]}}
    result = with_fake_sim(fake, lambda sim: rtraffic.run_on(sim, opts, str(out)))
    sends = result["sends"]
    assert len(sends) >= 1 and all(s["routes"] == [["cafefeed"]] for s in sends)
    steps = next(m["steps"] for m in fake.got if m["type"] == "sequence")
    assert steps[1]["verb"] == "lxmf.send_route" and steps[1]["args"]["routes"] == [["cafefeed"]]


def test_traffic_driver_sends_plainly_when_routes_says_nothing_for_the_pair(tmp_path):
    fake = FakeSim([{"name": "n01", "id": 1, "tags": [], "lat": 0, "lon": 0, "height_m": 0},
                    {"name": "n02", "id": 2, "tags": [], "lat": 0, "lon": 0, "height_m": 0}],
                   {"n01": [("n01", "1" * 32)], "n02": [("n02", "2" * 32)]})
    out = tmp_path / "out.json"
    opts = {"warm_rounds": 0, "settle_every": 1, "traffic": 6, "every": 5, "drain": 0,
           "routes": lambda src, dst: None}
    result = with_fake_sim(fake, lambda sim: rtraffic.run_on(sim, opts, str(out)))
    assert all("routes" not in s for s in result["sends"])
    steps = next(m["steps"] for m in fake.got if m["type"] == "sequence")
    assert steps[1]["verb"] == "lxmf.send"


# ---- the self-check: routed_sends -----------------------------------------

def test_routed_sends_inverts_dests_by_the_tags_own_truncation():
    drive = {"dests": {"relay1": "11112222333344445555666677778888",
                       "relay2": "aaaabbbbccccddddeeeeffff00001111"},
            "sends": [{"marker": "G0001", "src": "s", "dst": "d",
                       "routes": [["11112222", "aaaabbbb"]]}]}
    out = delivery.routed_sends(drive)
    assert len(out) == 1
    send, hops = out[0]
    assert send["marker"] == "G0001"
    assert hops == ["relay1", "relay2"]


def test_routed_sends_is_none_for_a_tag_with_no_station_or_an_ambiguous_one():
    drive = {"dests": {"same1": "11112222" + "0" * 24, "same2": "11112222" + "1" * 24},
            "sends": [{"marker": "G0001", "src": "s", "dst": "d",
                       "routes": [["11112222", "deadbeef"]]}]}
    send, hops = delivery.routed_sends(drive)[0]
    assert hops == [None, None]       # "11112222" is ambiguous, "deadbeef" unknown


def test_routed_sends_skips_sends_with_no_routes():
    drive = {"dests": {}, "sends": [{"marker": "G0001", "src": "s", "dst": "d"}]}
    assert delivery.routed_sends(drive) == []
