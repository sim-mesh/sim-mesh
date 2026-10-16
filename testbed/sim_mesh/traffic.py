"""The LXMF traffic driver: a whole run, on the run's own clock, on any
firmware of category `reticulum` (sim_mesh.reticulum.driver).

    from sim_mesh import *
    from sim_mesh import traffic

    firmware = script_input("firmware", type=Firmware, category="reticulum")
    sim_speed("max")
    nodes().firmware(firmware)
    nodes().on_first_boot(Node.reticulum.lxmf.create())
    traffic.run(OPTIONS)                    # into the run directory as traffic.json
    sim_pause()

Phases, each skipped when its option says so:

  up        wait until every station is up.
  identity  each station's LXMF delivery address (the first the
            `lxmf.identities` verb lists), asked until every station has one.
  warm      rounds of announces across the stations (the `lxmf.announce` verb;
            warm_rounds, each spread over warm_spread seconds with warm_gap
            between rounds), then, on the stations whose firmware can list
            its paths (the `path` verb), a sample every settle_every seconds
            until their total does not rise between two samples (or
            settle_max has passed). With no such station, two settle_every
            waits. warm_rounds 0 skips the phase.
  snapshot  a snapshot, warm_snapshot, when given.
  traffic   one message every `every` seconds for `traffic` seconds (the
            `lxmf.send` verb), the sender, recipient, size class
            and text all drawn from `seed`, so two runs with one seed and
            one set of station names send the same messages at the same
            instants after the traffic starts. `arrivals` "poisson", `pairs`
            and `hub` vary when the messages go and between whom, keeping
            the messages themselves (`schedule`). Each send is preceded by
            the `path` verb at the sender, the route length at send time
            where the firmware can say it. Size classes: short 20-55
            characters, two-frame 115-300, and 420-650 (over 500 B on the
            wire: a link and a resource), weighted 167 : 76 : 39. Every text
            starts with its marker, `marker` and a four-digit number.

            T185: `routes`, a mapping {(src, dst): routes} or a function
            `(src, dst) -> routes` (`library.build_routes`'s, through
            `library.route_tags`), sends that pair by `lxmf.send_route`
            instead, its routes recorded on the send as `routes`. A pair
            `routes` says nothing for (no entry, or the function answers
            `None`) sends by plain `lxmf.send`, as every pair already did.
  drain     `drain` seconds after the last send slot.
  gather    with `gather`, every station's `diagnostics`.
  snapshot  end_snapshot, when given.

Once every station is up the driver says the run's phases (warm-up,
traffic and drain, each with the T it ends at), and again whenever warm-up
runs longer, so the page can say which phase the run is in and when it will
be done. The result holds the phases (wall and T at each boundary), every
clock message, the warm-up samples, every send with its route, message id
and reply, and the gathered output. How much of it was delivered is counted
afterwards from the run's events, what each sender's driver reported of
each message (sim_mesh.reticulum.delivery, `report`).
"""

import asyncio
import json
import os
import random
import re
import time

from sim_mesh import library
from sim_mesh.sim import SimError

OPTIONS = {"warm_rounds": 3, "warm_spread": 300.0, "warm_gap": 120.0,
           "settle_every": 180.0, "settle_max": 3600.0, "warm_snapshot": None,
           "traffic": 3600.0, "every": 5.0, "seed": 17, "marker": "G", "drain": 600.0,
           "gather": True, "end_snapshot": None,
           "arrivals": "even", "hub": None, "hub_share": 0.5, "pairs": 0, "routes": None}
ARRIVALS = ("even", "poisson")

WORDS = ("mesh relay gateway lora packet announce proof link path hop station "
         "field city river bridge north south east west signal").split()
CLASSES = ["short"] * 167 + ["two"] * 76 + ["big"] * 39
RESULT_FILE = "traffic.json"


def body(rng, cls, marker):
    n = {"short": rng.randint(20, 55), "two": rng.randint(115, 300)}.get(cls) or rng.randint(420, 650)
    text = marker
    while len(text) < n:
        text += "-" + rng.choice(WORDS)
    return text[:n]


def schedule(names, seed, duration, every, marker, arrivals="even", hub=None,
             hub_share=0.5, pairs=0):
    """The traffic: (n, at, src, dst, cls, text), at in seconds after its start.

    By default one message every `every` seconds, its sender, recipient, size
    class and text drawn from `seed`, in that order, from one stream. The
    variants draw from streams of their own, so the messages stay the ones
    the default draws, the same texts of the same classes in the same order,
    and only when they go and between whom changes:

    - arrivals "poisson": the gaps between sends drawn exponential with mean
      `every`, the first at the start, as many as fall within `duration`;
    - pairs > 0: every message between one of `pairs` sender-recipient pairs
      drawn once, taken in turn, like that many conversations at once;
    - hub: a share `hub_share` of the messages whose sender is not `hub` go to
      it instead, as to a gateway or a dispatcher.
    """
    if arrivals not in ARRIVALS:
        raise ValueError("arrivals are %s, not %r" % (" or ".join(ARRIVALS), arrivals))
    names = sorted(names)
    if hub is not None and hub not in names:
        raise ValueError("the hub %r is not one of the senders" % hub)
    if not 0.0 <= float(hub_share) <= 1.0:
        raise ValueError("a hub share is between 0 and 1, not %r" % hub_share)
    if int(pairs) < 0:
        raise ValueError("pairs is a count, not %r" % pairs)
    rng = random.Random(seed)
    times = random.Random("%s:arrivals" % seed)
    ends = random.Random("%s:endpoints" % seed)
    fixed = []
    for _ in range(int(pairs)):
        src = ends.choice(names)
        fixed.append((src, ends.choice([s for s in names if s != src])))
    out = []
    n = 0
    at = 0.0
    while True:
        if arrivals == "even":
            at = n * every
        elif n:
            at += times.expovariate(1.0 / every)
        if at >= duration:
            break
        src = rng.choice(names)
        dst = rng.choice([s for s in names if s != src])
        cls = rng.choice(CLASSES)
        text = body(rng, cls, "%s%04d" % (marker, n + 1))
        if fixed:
            src, dst = fixed[n % len(fixed)]
        if hub is not None and src != hub and ends.random() < float(hub_share):
            dst = hub
        out.append((n + 1, at, src, dst, cls, text))
        n += 1
    return out


class Options:
    """The phase options, `OPTIONS` with whatever a script gives over them."""

    def __init__(self, **given):
        unknown = set(given) - set(OPTIONS)
        if unknown:
            raise ValueError("no traffic option %s" % ", ".join(sorted(unknown)))
        self.__dict__.update(OPTIONS, **given)


def run(opts=None, out_path=None):
    """The whole run on the script's simulation (sim_mesh.library): the
    result, written to `out_path` (the run directory's traffic.json by
    default) as it grows."""
    sim = library.runtime.held()
    out_path = out_path or os.path.join(sim.run_dir, RESULT_FILE)
    return library.runtime.call(run_on(sim, opts or {}, out_path))


async def run_on(sim, opts, out_path):
    """The whole run on a held `sim_mesh.Sim`. `opts` is an Options (or a
    mapping of them); the result is written to `out_path` as it grows, and
    returned."""
    if isinstance(opts, dict):
        opts = Options(**opts)
    result = sim.result
    args = dict(vars(opts))
    # `routes` is a mapping (its keys pairs, not strings) or a function,
    # neither JSON-safe: `args` is written as this run's own record of what
    # it was asked for, so it keeps a plain summary instead. Every send's
    # own `routes` (below) still carries the real ones, in full.
    if callable(args.get("routes")):
        args["routes"] = "function"
    elif args.get("routes"):
        args["routes"] = "%d pair(s)" % len(args["routes"])
    result.update({"args": args, "phases": [], "wall_start": time.time()})

    def phase(name):
        result["phases"].append([name, round(time.time(), 3), sim.t])
        print("%s: run %.1f s, wall %.1f s" % (name, sim.run_s, time.time() - result["wall_start"]),
              flush=True)

    def dump():
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)

    await sim.all_up()
    result["up"] = sim.up
    phase("all_up")
    await run_phases(opts, sim, result, phase, dump)
    result["wall_end"] = time.time()
    result["t_end"] = sim.t
    dump()
    print("done", flush=True)
    return result


def meta(verb, category=None, **args):
    """A verb as simd takes it: a category's (only on its stations), or with
    none, every firmware's."""
    out = {"type": "meta", "verb": verb, "args": args}
    if category is not None:
        out["category"] = category
    return out


def routes_for(routes_opt, src, dst):
    """What `opts.routes` says for this (`src`, `dst`): a list of routes
    (`library.route_tags()`'s own shape), or `None` for an ordinary,
    un-routed send. `routes_opt` is a {(src, dst): routes} mapping, a
    function `(src, dst) -> routes` (`None` where it says nothing), or
    `None` for no routed sends at all."""
    if routes_opt is None:
        return None
    if callable(routes_opt):
        return routes_opt(src, dst)
    return routes_opt.get((src, dst))


async def run_phases(opts, sim, result, phase, dump):
    stations = sorted(n for n, s in sim.stations.items() if s.firmware)
    # A station is up before its first-boot lines have all landed, so its
    # identity may not exist yet: ask until every station has one.
    dests = {}
    answers = {}
    for attempt in range(60):
        got = await sim.ask(meta("lxmf.identities", "reticulum"),
                            [n for n in stations if n not in dests],
                            after=2.0 if attempt else 0.0)
        for name, held in got.items():
            answers[name] = held
            # The identity it sends from, which is first.
            addr = held[0][1] if isinstance(held, list) and held else None
            if isinstance(addr, str) and re.fullmatch(r"[0-9a-f]{32}", addr):
                dests[name] = addr
        if len(dests) >= len(stations):
            break
    result["dests"] = dests
    # What a station with no destination last said, and what it runs: what
    # the report tells the reader of why.
    result["no_dest"] = {n: {"firmware": sim.stations[n].firmware,
                             "answer": str(answers.get(n, "nothing"))[:200]}
                         for n in stations if n not in dests}
    # Every station, whether or not it has shown its identity: the schedule
    # is drawn over them, so one seed sends the same messages.
    senders = stations
    missing = sorted(set(senders) - set(dests))
    if missing:
        print("no lxmf destination from: %s" % " ".join(missing), flush=True)
    phase("identities")

    async def send_plan(warm_end):
        """The phases left, from where warm-up is expected to end: sent at
        the start and again whenever that moves."""
        phases = [("warm-up", warm_end)] if opts.warm_rounds > 0 else []
        if opts.traffic > 0:
            phases += [("traffic", warm_end + 1.0 + opts.traffic),
                       ("drain", warm_end + 1.0 + opts.traffic + opts.drain)]
        if phases:
            await sim.plan(*phases)

    rounds = opts.warm_rounds
    await send_plan(sim.run_s + rounds * opts.warm_spread
                    + max(0, rounds - 1) * opts.warm_gap + 2 * opts.settle_every
                    if rounds > 0 else sim.run_s)

    if opts.warm_rounds > 0:
        result["warm"] = {"rounds": [], "samples": []}
        for r in range(opts.warm_rounds):
            await sim.ask(meta("lxmf.announce", "reticulum"), senders, spread=opts.warm_spread,
                          after=opts.warm_gap if r else 0.0)
            result["warm"]["rounds"].append([time.time(), sim.t])
            phase("warm_round_%d" % (r + 1))
        counted = list(senders)
        settle_from = sim.run_s
        prev = None
        while True:
            if not counted:
                await sim.sleep(2 * opts.settle_every)
                break
            reply = await sim.ask(meta("path", "reticulum"), counted, after=opts.settle_every)
            per = {n: len(v) for n, v in reply.items() if isinstance(v, list)}
            # Only the stations whose firmware can list its paths are asked again.
            counted = sorted(per)
            if not counted:
                continue
            total = sum(per.values())
            result["warm"]["samples"].append({"t": sim.t, "wall": time.time(),
                                              "total": total, "per": per})
            print("  paths %d at run %.0f s" % (total, sim.run_s), flush=True)
            dump()
            if prev is not None and total <= prev:
                break
            if sim.run_s - settle_from >= opts.settle_max:
                print("  settle-max reached, still rising", flush=True)
                break
            prev = total
            await send_plan(sim.run_s + opts.settle_every)   # one more sample at least
        phase("warm_settled")
    if opts.warm_snapshot:
        await sim.snapshot(opts.warm_snapshot)
        phase("warm_snapshot")

    if opts.traffic > 0:
        plan = schedule(senders, opts.seed, opts.traffic, opts.every, opts.marker,
                        opts.arrivals, opts.hub, opts.hub_share, opts.pairs)
        start = sim.run_s + 1.0
        result["traffic_start_run_s"] = start
        await send_plan(start - 1.0)
        sends = result["sends"] = []

        async def one(n, at, src, dst, cls, text):
            rec = {"n": n, "marker": "%s%04d" % (opts.marker, n), "src": src,
                   "dst": dst, "cls": cls, "len": len(text), "at": at}
            sends.append(rec)
            if dst not in dests:
                rec["error"] = "recipient has no destination"
                return
            # T185: this pair's routes, if `opts.routes` says any — a send
            # with routes goes by `lxmf.send_route` instead of `lxmf.send`,
            # the only difference from an ordinary send below.
            routes = routes_for(opts.routes, src, dst)
            send_step = meta("lxmf.send_route", "reticulum", to=dst, routes=routes, text=text) \
                if routes else meta("lxmf.send", "reticulum", to=dst, text=text)
            if routes:
                rec["routes"] = routes
            # The route and the send as one sequence, so both happen at the
            # T the message is due, with no round trip to here between them.
            # The `after` (never less than a millisecond) makes it a task of
            # simd's own: a message without one is handled inside the
            # socket's reader, which holds every later message behind it.
            try:
                results, t = await sim.sequence(
                    [meta("path", "reticulum", to=dst), send_step], [src],
                    after=max(0.001, start + at - sim.run_s))
            except SimError as err:
                # This message, not the run: the rest go on at their instants.
                rec["error"] = str(err)[:300]
                return
            path_out, out = (r.get(src) for r in results)
            rec["t_sent"] = rec["t_path"] = t if t is not None else sim.t
            rec["hops"] = path_out[0].get("hops") if isinstance(path_out, list) and path_out \
                else None
            if not isinstance(path_out, list):
                rec["path_reply"] = str(path_out)[:200]
            good = isinstance(out, str) and out and not out.startswith("! ")
            rec["mid"] = out if good else None
            if not good:
                rec["reply"] = str(out)[:300]

        phase("traffic_start")
        jobs = [asyncio.ensure_future(one(*p)) for p in plan]
        print("scheduled %d messages over %.0f s" % (len(plan), opts.traffic), flush=True)
        t_end = start + opts.traffic

        async def progress():
            # What has been sent so far, on the wall clock's beat: it asks
            # simd nothing, so it is no turn of ours.
            while True:
                await asyncio.sleep(5)
                dump()
        keeping = asyncio.ensure_future(progress())
        try:
            await sim.until(t_end)
            await asyncio.gather(*jobs)
        finally:
            keeping.cancel()
        phase("traffic_end")
        await sim.until(t_end + opts.drain)
        phase("drain_end")

    if opts.gather:
        reply = await sim.ask(meta("diagnostics"), stations)
        result["gathered"] = {"t": sim.t, "results": reply}
    phase("gathered")
    if opts.end_snapshot:
        await sim.snapshot(opts.end_snapshot)
        phase("end_snapshot")


# ---- the report ----------------------------------------------------------

def t_text(us):
    """T as `hh:mm:ss`, or `Nd + hh:mm` from a day on."""
    s = int((us or 0) // 1_000_000)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return "%dd + %02d:%02d" % (d, h, m) if d else "%02d:%02d:%02d" % (h, m, s)


def report(run_dir, traffic_path=None):
    """A traffic run's report, as Markdown: what ran, the phases, and the
    delivery counted from the run's own logs (`delivery.analyse_run`)."""
    from sim_mesh.reticulum import delivery

    traffic_path = traffic_path or os.path.join(run_dir, RESULT_FILE)
    name = os.path.basename(os.path.normpath(run_dir))
    lines = ["# LXMF traffic: %s" % name, ""]
    if not os.path.isfile(traffic_path):
        return "\n".join(lines + ["No %s: the driver did not get as far as writing it."
                                  % os.path.basename(traffic_path), ""])
    try:
        drive, out, _ = delivery.analyse_run(traffic_path, run_dir)
    except ValueError as err:
        with open(traffic_path, encoding="utf-8") as handle:
            drive, out = json.load(handle), None
        problem = str(err)
    dests = drive.get("dests") or {}
    stations = sorted(set(drive.get("up") or {}) | set(dests))
    args = drive.get("args") or {}
    lines += ["| | |", "|---|---|",
              "| ended at | T %s |" % t_text(drive.get("t_end")),
              "| wall | %.0f s |" % ((drive.get("wall_end") or 0) - (drive.get("wall_start") or 0)),
              "| sends | one every %s s for %s s, seed %s |"
              % (args.get("every"), args.get("traffic"), args.get("seed")),
              "| LXMF identities | %d%s |" % (len(dests), " of %d stations" % len(stations)
                                             if stations else ""),
              ""]
    if not dests:
        lines += ["**No station showed an LXMF identity**, so nothing could be sent.", ""]
    no_dest = drive.get("no_dest") or {}
    if no_dest:
        lines += ["Stations with no LXMF identity send nothing and receive nothing. An "
                  "identity is made at first boot (`Node.reticulum.lxmf.create()`); a "
                  "firmware with no LXMF destination of its own, a transport-only node "
                  "such as microReticulum, has none to show, and takes part only by "
                  "forwarding among stations that do.", "",
                  "| station | firmware | its answer |", "|---|---|---|"]
        lines += ["| %s | %s | `%s` |" % (n, row.get("firmware") or "?", row.get("answer"))
                  for n, row in sorted(no_dest.items())]
        lines.append("")
    lines += ["## Phases", "", "| phase | T |", "|---|---|"]
    lines += ["| %s | %s |" % (p[0], t_text(p[2])) for p in drive.get("phases") or []]
    lines.append("")
    if out is None:
        lines += ["## Delivery", "", "Not counted: %s." % problem, ""]
        return "\n".join(lines)

    def table(title, figures):
        rows = ["| %s | delivered |" % title, "|---|---|"]
        return rows + ["| %s | %s |" % (k, v) for k, v in figures.items()] + [""]

    lines += ["## Delivery", "", "**%s** delivered." % out["overall"], ""]
    lines += table("route hops at send", out["by_route_hops"])
    lines += table("radio hops", out["by_radio_hops"])
    lines += table("size", out["by_class"])
    lat = out.get("latency_s")
    if lat:
        lines += ["Latency: median %.1f s, 90%% within %.1f s, longest %.1f s (%d messages)."
                  % (lat["median"], lat["p90"], lat["max"], lat["n"]), ""]
    if out.get("undelivered_last_word"):
        lines += ["## Undelivered, by the sender's last word", "", "| last line | messages |",
                  "|---|---|"]
        lines += ["| `%s` | %d |" % (w.replace("|", "\\|"), n)
                  for w, n in out["undelivered_last_word"][:10]]
        lines.append("")
    return "\n".join(lines)
