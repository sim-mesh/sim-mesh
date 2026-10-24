#!/usr/bin/env python3
"""Link geometry from a run's ether record, and power by distance.

    links.py RUN [--record PATH] [--from S] [--to S] [--min-clean N]
             [--calling HZ] [--all-carriers] [--power] [--json OUT]

A **usable link** is an ordered pair (transmitter, receiver) with at least
--min-clean (20) clean receptions inside [--from, --to), counted on the
calling channel (--calling; by default the carrier most of the run's
nodes declare) unless --all-carriers. For them:

- distance of every usable one-way link: mean, minimum, p10, median, p90,
  maximum and a histogram in 200 m steps, and the share of links usable in
  both directions;
- neighbours per station (the receivers of its usable links) with the
  nearest and farthest of them;
- hop diameter over usable links both ways, and through forwarding
  stations only (those carrying a role tag: transport, router, repeater);
- the run's own medium beside them (`model`): the pairs the ether would
  deliver on the calling channel by the run's loss table, links, shadowing,
  offsets and antenna gains, each transmitter at its declared power, SF and bandwidth, against the
  noise figure the run's ether had; their count, distances, the neighbour
  count and the diameter they give. A run with no table for the calling
  channel's band has no model.

--power adds, for frames off the calling channel (SUPE's traffic channels):
transmit power by frame and by airtime (minimum, quartiles, maximum, a
histogram in 2 dB steps), the exchanges with any frame below the maximum
(an exchange is frames on one carrier with under 1 s of silence between
them, as airtime.py counts it), the SF and bandwidth the frames flew at, and
power against the distance to the peer, binned by 200 m. A frame's peer is
the other station of its exchange when exactly two stations spoke in it,
else the one station that received it cleanly; a frame with neither is left
out of the distance table.

Positions are the run's nodeset as it ended, in the geodata's metres
(synthetic ground's metres, a pack's easting and northing);
distances are metres on that plane.
"""
import argparse
import collections
import json
import math
import sys

import referee
from sim_mesh import record as record_module
from sim_mesh.view import RunView

BIN_M = 200


def quantiles(values):
    v = sorted(values)
    if not v:
        return None
    q = lambda f: v[min(len(v) - 1, int(f * (len(v) - 1) + 0.5))]
    return {"n": len(v), "mean": sum(v) / len(v), "min": v[0], "p10": q(.1), "p25": q(.25),
            "median": q(.5), "p75": q(.75), "p90": q(.9), "max": v[-1]}


def dist(pos, a, b):
    return math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])


def bfs(adj, src, via=None):
    d = {src: 0}
    frontier = [src]
    while frontier:
        nxt = []
        for a in frontier:
            if via is not None and a != src and a not in via:
                continue
            for b in adj.get(a, ()):
                if b not in d:
                    d[b] = d[a] + 1
                    nxt.append(b)
        frontier = nxt
    return d


def diameter(adj, nodes, via=None):
    worst, unreached = 0, 0
    for s in nodes:
        d = bfs(adj, s, via)
        unreached += sum(1 for n in nodes if n not in d)
        worst = max([worst] + [d[n] for n in nodes if n in d])
    return {"hops": worst, "pairs_unreached": unreached}


def read(args, level_at=None):
    """Frames in the window: sender, start, span, carrier, power, sf, bw, clean receivers.

    The window is in seconds of T in a virtual-time run, of the wall clock
    since the record's first line in a real one. A reception is its frame's
    by the ether's own number for the frame, tied as the referee ties them
    (`referee.Record`, at the run's levels), not by when the frame went on
    the air and what it carried, which two frames can share."""
    lo = int(args.frm * 1e6) if args.frm is not None else None
    hi = int(args.to * 1e6) if args.to is not None else None
    record = referee.Record(args.record, level_at)
    tx_line = {eid: f.line for eid, f in record.by_eid.items()}   # ether frame id -> its `tx`
    frames, at_line = [], {}
    for line, (stamp, direction, sid, msg) in enumerate(record_module.lines(args.record)):
        if direction == "in" and msg.get("type") == "tx":
            t_us = referee.to_us(stamp) - record.origin
            if (lo is not None and t_us < lo) or (hi is not None and t_us >= hi):
                continue
            f = {"sid": sid, "t": t_us, "span": max(0, int(msg.get("t_end", 0)) - int(msg.get("t0", 0))),
                 "freq": msg.get("freq"), "power": msg.get("power_dbm"), "sf": msg.get("sf"),
                 "bw": msg.get("bw"), "clean": set(), "crc": 0}
            at_line[line] = f
            frames.append(f)
        elif direction == "out" and msg.get("type") == "rx_end":
            f = at_line.get(tx_line.get(msg.get("id")))
            if f is None:
                continue
            if msg.get("verdict") == "clean":
                f["clean"].add(sid)
            else:
                f["crc"] += 1
    return frames


def geometry(frames, pos, names, forwarders, args):
    tol = 125_000 // 4
    on_call = lambda f: abs(f["freq"] - args.calling) <= tol
    clean = collections.Counter()
    for f in frames:
        if args.all_carriers or on_call(f):
            for r in f["clean"]:
                clean[(f["sid"], r)] += 1
    links = sorted(p for p, n in clean.items() if n >= args.min_clean and p[0] in pos and p[1] in pos)
    lset = set(links)
    d = [dist(pos, a, b) for a, b in links]
    hist = collections.Counter(int(x // BIN_M) for x in d)
    out = {"usable_links_one_way": len(links),
           "both_ways": sum(1 for a, b in links if (b, a) in lset) // 2,
           "distance_m": quantiles(d),
           "histogram_m": {"%d-%d" % (k * BIN_M, (k + 1) * BIN_M): hist[k]
                           for k in range(0, max(hist) + 1 if hist else 0)}}
    heard_by = collections.defaultdict(set)      # receiver -> transmitters it hears
    for a, b in links:
        heard_by[b].add(a)
    nodes = sorted(pos)
    counts = [len(heard_by[n]) for n in nodes]
    near = [min(dist(pos, n, m) for m in heard_by[n]) for n in nodes if heard_by[n]]
    far = [max(dist(pos, n, m) for m in heard_by[n]) for n in nodes if heard_by[n]]
    out["neighbours_per_station"] = quantiles(counts)
    out["stations_hearing_nobody"] = [names[n] for n in nodes if not heard_by[n]]
    out["nearest_neighbour_m"] = quantiles(near)
    out["farthest_neighbour_m"] = quantiles(far)
    both = collections.defaultdict(set)
    for a, b in links:
        if (b, a) in lset:
            both[a].add(b)
    out["diameter_both_ways"] = diameter(both, nodes)
    out["diameter_both_ways_through_forwarders"] = diameter(both, nodes, forwarders)
    out["per_station"] = {names[n]: {"neighbours": len(heard_by[n]),
                                     "nearest_m": round(min(dist(pos, n, m) for m in heard_by[n]))
                                     if heard_by[n] else None,
                                     "farthest_m": round(max(dist(pos, n, m) for m in heard_by[n]))
                                     if heard_by[n] else None}
                          for n in nodes}
    return out


def model(view, nodes, forwarders, args):
    """The run's medium's own view of the same map: who would decode whom on
    the calling channel, by the run's loss table. None without a table."""
    if not view.has_table(args.calling):
        return None
    pos = view.positions()
    heard = view.radio_graph(args.calling)          # transmitter -> its receivers
    pairs = [(a, b) for a in nodes for b in heard.get(a, ()) if b in pos]
    radios = collections.Counter((r["sf"], r["bw_hz"], r["power_dbm"])
                                 for r in (view.radio(view.names[n]) for n in nodes))
    sf, bw, power = radios.most_common(1)[0][0] if radios else (None, None, None)
    e = view.medium()
    return {"sf": sf, "bw": bw, "power_dbm": power,
            "noise_figure_db": e.physics.noise_figure_db,
            "fading_db": e.physics.fading_db,
            "coherence_s": e.physics.coherence_s,
            "rician_k": e.physics.rician_k,
            "threshold_dbm": e.noise(bw) + e.sensitivity(sf) if sf else None,
            "links_one_way": len(pairs),
            "distance_m": quantiles([dist(pos, a, b) for a, b in pairs]),
            "neighbours_per_station": quantiles([
                sum(1 for a in nodes if n in heard.get(a, ())) for n in nodes]),
            "diameter": diameter(heard, nodes),
            "diameter_through_forwarders": diameter(heard, nodes, forwarders)}


def power(frames, pos, args):
    tol = 125_000 // 4
    other = sorted((f for f in frames if abs(f["freq"] - args.calling) > tol and f["power"] is not None),
                   key=lambda f: (f["freq"], f["t"]))
    if not other:
        return None
    top = max(f["power"] for f in other)
    ex, cur, last = [], None, None
    for f in other:
        if cur is None or f["freq"] != cur["freq"] or f["t"] - last > 1_000_000:
            cur = {"freq": f["freq"], "frames": [], "stations": set()}
            ex.append(cur)
        cur["frames"].append(f)
        cur["stations"].add(f["sid"])
        last = f["t"] + f["span"]
    for e in ex:
        two = sorted(e["stations"]) if len(e["stations"]) == 2 else None
        for f in e["frames"]:
            if two:
                f["peer"] = two[1] if f["sid"] == two[0] else two[0]
            elif len(f["clean"]) == 1:
                f["peer"] = next(iter(f["clean"]))
            else:
                f["peer"] = None
    pw = [f["power"] for f in other]
    air = collections.Counter()
    hist = collections.Counter()
    for f in other:
        b = int(math.floor(f["power"] / 2) * 2)
        hist[b] += 1
        air[b] += f["span"]
    total_air = sum(f["span"] for f in other)
    wq = []                       # airtime-weighted quartiles, over frames sorted by power
    acc = 0
    fs = sorted(other, key=lambda f: f["power"])
    for q in (0.0, 0.25, 0.5, 0.75, 1.0):
        target = q * total_air
        acc = 0
        for f in fs:
            acc += f["span"]
            if acc >= target:
                wq.append(f["power"])
                break
    rates = collections.Counter((f["sf"], f["bw"]) for f in other)
    rate_air = collections.Counter()
    for f in other:
        rate_air[(f["sf"], f["bw"])] += f["span"]
    bins = collections.defaultdict(list)
    for f in other:
        if f.get("peer") in pos and f["sid"] in pos:
            bins[int(dist(pos, f["sid"], f["peer"]) // BIN_M)].append(f["power"])
    ex_below = sum(1 for e in ex if any(f["power"] < top for f in e["frames"]))
    return {
        "frames": len(other), "max_dbm": top,
        "by_frame": quantiles(pw),
        "by_airtime_min_q1_median_q3_max": wq,
        "mean_by_airtime": sum(f["power"] * f["span"] for f in other) / total_air if total_air else None,
        "below_max_frames": sum(1 for p in pw if p < top),
        "histogram_2db": {"%d..%d" % (b, b + 1):{"frames": hist[b], "airtime_s": air[b] / 1e6}
                          for b in sorted(hist)},
        "exchanges": len(ex), "exchanges_any_below_max": ex_below,
        "rate_steps": {"SF%s/%dk" % (k[0], (k[1] or 0) // 1000): {"frames": v, "airtime_s": rate_air[k] / 1e6}
                       for k, v in sorted(rates.items(), key=lambda x: -x[1])},
        "frames_with_peer": sum(len(v) for v in bins.values()),
        "by_distance_m": {"%d-%d" % (k * BIN_M, (k + 1) * BIN_M): dict(
            quantiles(v), below_max=sum(1 for p in v if p < top)) for k, v in sorted(bins.items())},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run", help="the run directory")
    ap.add_argument("--record", help="the record to read (default: the run's record.tsv)")
    ap.add_argument("--from", dest="frm", type=float)
    ap.add_argument("--to", type=float)
    ap.add_argument("--min-clean", type=int, default=20)
    ap.add_argument("--calling", type=int,
                    help="the calling channel in Hz (default: the run's most declared carrier)")
    ap.add_argument("--all-carriers", action="store_true")
    ap.add_argument("--power", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    view = RunView(args.run)
    args.record = args.record or view.record_path
    if args.calling is None:
        args.calling = view.calling_hz()
    pos = view.positions()
    forwarders = view.forwarders()
    frames = read(args, referee.Air(view.medium()).level)
    out = geometry(frames, pos, view.names, forwarders, args)
    out["model"] = model(view, sorted(pos), forwarders, args)
    if args.power:
        out["power_traffic_channels"] = power(frames, pos, args)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1)
    brief = {k: v for k, v in out.items() if k != "per_station"}
    print(json.dumps(brief, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
