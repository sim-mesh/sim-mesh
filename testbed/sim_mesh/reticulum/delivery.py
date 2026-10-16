"""Delivery of LXMF messages, from what the senders' drivers reported.

T185's self-check (`routed_sends`, `chain_relayed`) is here too: whether a
route-carrying send (traffic.py's `routes` option, or a script's own
`.reticulum.lxmf.send_route()`) was actually carried by the chain it named,
read from the run's own record, rather than trusting that a chain was
merely accepted.

A message is delivered when its sender's driver reports it so, the event
`lxmf.message.status` with `status: delivered` in the run's `events.jsonl`,
for the id simd gave the `lxmf.send` (sim_mesh.reticulum.driver). `analyse` counts a
traffic driver's sends by:

- route hops at send time: what the sender's `path` verb answered just
  before the send (`no path` where it held none);
- radio hops: the shortest path in the run's radio graph (the pairs the
  ether delivers at each sender's declared carrier, SF, bandwidth and
  power, by the run's loss tables) whose intermediate stations forward;
- size class (the driver's short, two-frame, over 500 B);
- latency: from the instant the send was answered to the delivered event.

An event's `t` is the run's T, in microseconds, when simd took it in.
Undelivered messages are tallied by the last status the sender's driver
reported for them.
"""

import collections
import json
import os
import re

EVENTS_FILE = "events.jsonl"
STATUS_EVENT = "lxmf.message.status"


def events(run_dir):
    """Every event the run's drivers reported, in order."""
    path = os.path.join(run_dir, EVENTS_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    got = json.loads(line)
                except ValueError:
                    continue
                if isinstance(got, dict):
                    yield got
    except FileNotFoundError:
        return


def read_messages(run_dir):
    """(sender, mid) -> {'delivered': T in seconds or None, 'last': its
    last reported status, with why when there is one}."""
    out = {}
    for ev in events(run_dir):
        if ev.get("event") != STATUS_EVENT or not ev.get("mid"):
            continue
        rec = out.setdefault((ev.get("node"), str(ev["mid"])), {"delivered": None, "last": None})
        status = str(ev.get("status") or "")
        rec["last"] = status + (": %s" % ev["why"] if ev.get("why") else "")
        if status == "delivered" and rec["delivered"] is None:
            rec["delivered"] = (ev.get("t") or 0) / 1e6
    return out


def event_deliveries(run_dir):
    """Each sender's delivered instants, in seconds of T: name -> [s, ...]."""
    out = {}
    for (name, _), rec in read_messages(run_dir).items():
        if rec["delivered"] is not None:
            out.setdefault(name, []).append(rec["delivered"])
    return {name: sorted(times) for name, times in out.items()}


def radio_hops(adj, forwarders, src, dst):
    """Fewest hops from src to dst over `adj` (name -> names it reaches),
    passing only through `forwarders`; None when there is no such path."""
    frontier, seen, d = {src}, {src}, 0
    while frontier:
        d += 1
        nxt = set()
        for n in frontier:
            for m in adj.get(n, ()):
                if m == dst:
                    return d
                if m not in seen and m in forwarders:
                    seen.add(m)
                    nxt.add(m)
        frontier = nxt
    return None


def pct(b):
    return "%d/%d (%.1f%%)" % (b[0], b[1], 100.0 * b[0] / b[1]) if b[1] else "0/0"


def quantiles(v):
    v = sorted(v)
    if not v:
        return None
    q = lambda f: v[min(len(v) - 1, int(f * (len(v) - 1) + 0.5))]
    return {"n": len(v), "min": v[0], "p25": q(.25), "median": q(.5), "p75": q(.75),
            "p90": q(.9), "max": v[-1], "mean": sum(v) / len(v)}


def analyse(drive, logs, adj, forwarders):
    """The figures, and one row per message.

    `drive` is the traffic driver's result, `logs` what `read_messages`
    found, `adj` the radio graph by name, `forwarders` the names that forward.
    """
    total = [0, 0]
    by_route = collections.defaultdict(lambda: [0, 0])
    by_radio = collections.defaultdict(lambda: [0, 0])
    by_cls = collections.defaultdict(lambda: [0, 0])
    lat, lat_cls = [], collections.defaultdict(list)
    lat_route = collections.defaultdict(list)
    last_words = collections.Counter()
    no_mid = 0
    rows = []
    for s in drive.get("sends", []):
        if not s.get("mid"):
            no_mid += 1
        rec = logs.get((s["src"], s.get("mid")), {}) if s.get("mid") else {}
        t_sent = (s.get("t_sent") or 0) / 1e6
        ok = rec.get("delivered") is not None
        rh = s.get("hops")
        route = "no path" if rh is None else (str(rh) if rh <= 6 else "7+")
        gh = radio_hops(adj, forwarders, s["src"], s["dst"])
        radio = "none" if gh is None else (str(gh) if gh <= 6 else "7+")
        for b in (total, by_route[route], by_radio[radio], by_cls[s["cls"]]):
            b[0] += ok
            b[1] += 1
        if ok:
            d = rec["delivered"] - t_sent
            lat.append(d)
            lat_cls[s["cls"]].append(d)
            lat_route[route].append(d)
        else:
            w = rec.get("last") or ("no mid" if not s.get("mid") else "nothing reported")
            w = re.sub(r"\b(o_\S+|[0-9a-f]{8,}|lxmf\.id\d\.\S+)", "…", w)
            w = re.sub(r"\d+", "N", w)
            last_words[w] += 1
        rows.append({"marker": s["marker"], "src": s["src"], "dst": s["dst"], "cls": s["cls"],
                     "route_hops": rh, "radio_hops": gh, "delivered": ok,
                     "latency_s": (rec["delivered"] - t_sent) if ok else None})
    order = lambda k: (k in ("no path", "none"), k == "7+", k)
    out = {"sent": total[1], "delivered": total[0], "no_mid": no_mid,
           "overall": pct(total),
           "by_route_hops": {k: pct(by_route[k]) for k in sorted(by_route, key=order)},
           "by_radio_hops": {k: pct(by_radio[k]) for k in sorted(by_radio, key=order)},
           "by_class": {k: pct(by_cls[k]) for k in ("short", "two", "big") if k in by_cls},
           "latency_s": quantiles(lat),
           "latency_by_class": {k: quantiles(v) for k, v in lat_cls.items()},
           "latency_by_route_hops": {k: quantiles(lat_route[k]) for k in sorted(lat_route, key=order)},
           "undelivered_last_word": last_words.most_common(25)}
    return out, rows


def analyse_run(traffic_path, run_dir):
    """A traffic run's figures: (the driver's result, figures, rows), counted
    against the run's own events and radio graph."""
    from sim_mesh.view import RunView

    with open(traffic_path, encoding="utf-8") as handle:
        drive = json.load(handle)
    view = RunView(run_dir)
    logs = read_messages(view.dir)
    graph = view.radio_graph(view.calling_hz())
    adj = {view.names[a]: {view.names[b] for b in bs} for a, bs in graph.items()}
    forwarders = {view.names[s] for s in view.forwarders()}
    out, rows = analyse(drive, logs, adj, forwarders)
    return drive, out, rows


# ---- T185: did a route-carrying send travel the chain it named? ----------

def routed_sends(drive):
    """Every (send, hop names) of `drive` (traffic.json) whose send carried
    routes: one entry per route (a send may carry up to ROUTE_ALTS_MAX),
    hop names found from the run's own `dests` ({name: lxmf.delivery
    address}, built once at the run's "identities" phase), inverted by a
    tag's own truncation (`online_map()`'s, the first 8 hex digits) — the
    same way the sim already learns a station's destination hashes, read
    backwards. A tag with no station behind it (an address never
    resolved, or two stations sharing a truncated prefix — unlikely at a
    sim's scale, never silently assumed) is `None` in its place."""
    by_tag = collections.defaultdict(list)
    for name, addr in (drive.get("dests") or {}).items():
        by_tag[addr[:8]].append(name)
    out = []
    for s in drive.get("sends", []):
        for route in s.get("routes") or ():
            hops = [names[0] if len(names) == 1 else None for names in
                   (by_tag.get(tag, []) for tag in route)]
            out.append((s, hops))
    return out


def chain_relayed(run_dir, drive, window_s=120.0):
    """T185's self-check: for every route-carrying send, whether each named
    hop transmitted an ordinary (non-SUPE, non-announce) frame on the air
    within `window_s` after the send went out — evidence the chain was
    actually relayed, not merely accepted. {marker: [[(hop name, seen?),
    ...], ...]}, one inner list per route the send carried (most sends
    carry one), hops within it in order; a hop of `None` (`routed_sends`,
    a tag that did not resolve to one station) is never "seen".

    Read from the run's own record (`sim_mesh.record`,
    `sim_mesh.reticulum.frames.kind_of`) rather than trusted from the send
    alone: `routed_sends` says WHO should have relayed it; this says
    whether the ether actually carries their transmission."""
    import base64

    from sim_mesh import record as record_module
    from sim_mesh.reticulum import frames as rframes
    from sim_mesh.view import RunView

    view = RunView(run_dir)
    tx_at = collections.defaultdict(list)       # station id -> [T seconds, ...] it transmitted at
    for stamp, _direction, sid, msg in record_module.lines(view.record_path, types=("tx",)):
        payload = base64.b64decode(msg.get("payload") or "")
        kind = rframes.kind_of(payload, 0)
        if kind and not rframes.is_announce_kind(kind) and not kind.startswith("SUPE") \
                and kind not in ("power request", "path request"):
            tx_at[sid].append(record_module.parse_time(stamp))

    out = {}
    for s, hops in routed_sends(drive):
        t_sent = (s.get("t_sent") or 0) / 1e6
        seen = []
        for name in hops:
            sid = view.ids.get(name) if name else None
            ok = sid is not None and any(t_sent <= t <= t_sent + window_s
                                         for t in tx_at.get(sid, ()))
            seen.append((name, ok))
        out.setdefault(s["marker"], []).append(seen)
    return out
