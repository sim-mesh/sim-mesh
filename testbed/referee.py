#!/usr/bin/env python3
"""The referee: what a run's record says each station did on the air, judged.

    referee.py --run RUN [--json] [--detail]

The ether decides who hears what and keeps no opinion about how a station
behaved; its record is there so that something else can form one. This reads
a finished run's `record.tsv` against the run's own medium (`RunView.medium()`,
asked through the ether's `level` and `audible`) and reports:

- **carrier sense**: every transmission that began while a frame on its
  carrier, one its sender could decode, was on the air. Whether the ether
  had told the sender's slot of that frame before it began: **told**, by an
  rx_begin at the frame's start; **told late**, by one mid-frame, which is
  what a slot coming to RX or CAD while the frame is on the air gets from an
  ether run with `--late-listeners`; or **never told**. And the window of
  that frame it began in: **blind**, its first four symbols, in which nothing
  has found it yet; **preamble**; or **payload**. An rx_begin marked `cad` is
  energy (the sense threshold crossed, or a frame that did not take the
  receiver); one without is a lock. Either is telling. Each comes with the
  mode the sender's slot had last stated when that frame went out;
- **frames nobody was told of**, by sender and channel: a station on a
  channel nobody listens to shows up here;
- **collisions**: every reception that ended `crc`, with each other
  transmission that shared its air and band and reached the receiver, split
  into senders **hidden** from each other, neither able to decode the
  other, and senders **in earshot**, one or both able to. A receiver that
  was itself sending lost the frame to half duplex, and a sender with two
  frames on the air at once (**same**) is neither; both are counted apart.

Duty cycle and power are `compliance.py`'s, against EN 300 220-2 annex B
per band entry, with e.r.p. and polite spectrum access. This tool's earlier
duty section put 863–865 MHz at 1 %, where the entry allows 0.1 %, and
ignored power; it is gone.

Whether a station sensed the channel before it talked is not in the record:
that its slot was in RX or CAD is, but an RSSI read or a CAD's result never
reaches the ether. What is judged is what the ether told the slot, which is
all its firmware had to go on.

A reception is tied to its transmission by the ether's number for the
frame, which rx_begin and rx_end carry and `tx` does not. A real-time ether
numbers transmissions in the order it records them. A virtual-time one
numbers each T's in the order its barrier takes them, station by station,
and there T is the record's own clock: an rx_begin names the instants its
frame's preamble, header and air end, which pin the frame, and frames alike
in all three take their numbers in the barrier's order. Levels are the
run's as it ended.
"""

import argparse
import bisect
import collections
import json
import os
import sys
from datetime import datetime

import store
from sim_mesh import record as record_module
from sim_mesh.view import RunView

sys.path.insert(0, os.path.join(store.SIM_DIR, "..", "ether"))
import ether as ether_module  # noqa: E402 - the path is set just above

# The start of a frame in which carrier sense cannot find it yet. Two boards
# that both found the channel clear started their frames within about 4 ms of
# each other: SF7 at 125 kHz, three boards, 150 trials a setting (reticulum
# tools/rncapture/README.md:37-39 and 69-78, at b2b4302). rnscale's replay of
# that bench agrees at 4 ms and not at 8 (tools/rnscale/src/medium.rs:29-37,
# same commit). 4 ms is four symbols at SF7 and 125 kHz; a receiver finds a
# preamble by its symbols, so the window is the frame's own four: 8.2 ms at
# SF8.
BLIND_SYMBOLS = 4

HOW = ("told", "told late", "never told")
WINDOWS = ("blind", "preamble", "payload")


def to_us(stamp):
    """A record stamp as microseconds on the record's clock: T in a
    virtual-time run, the wall clock in a real one, read with its date so a
    run across midnight does not wrap."""
    if ":" not in stamp:
        return round(float(stamp) * 1e6)
    return round(datetime.fromisoformat(stamp).timestamp() * 1e6)


# What one rx_begin said: where in the record, to which station, when it was
# told and when the frame ends, on the ether's clock, and the message.
Said = collections.namedtuple("Said", "line rsid t0 t_end msg")

# One rx_begin tied to its frame: where in the record, to which slot, whether
# it came mid-frame, and whether it was a lock.
Begin = collections.namedtuple("Begin", "line rsid slot late lock")


class Frame:
    """One transmission as its sender's `tx` put it on the air, on the
    record's clock, and what the ether said of it to other slots."""

    __slots__ = ("line", "sid", "slot", "freq", "bw", "sf", "power", "span",
                 "start", "pre", "hdr", "end", "batch", "eid", "begins", "ends")

    def __init__(self, line, at, sid, msg):
        # The ether's own reading of the stated timeline (ether.py recv_tx):
        # the span capped, the preamble's and header's ends inside it. One it
        # cannot read raises there before the frame is numbered, and here.
        t0 = int(msg.get("t0", 0))
        t_end, t_pre, t_hdr = (int(msg.get(key, t0)) for key in ("t_end", "t_pre", "t_hdr"))
        self.span = max(0, min(t_end - t0, ether_module.MAX_FRAME_US))
        pre = max(0, min(t_pre - t0, self.span))
        hdr = max(pre, min(t_hdr - t0, self.span))
        self.line = line                # its place among the record's lines
        self.sid = sid
        self.slot = msg.get("slot", 0)
        self.freq = msg.get("freq")
        self.bw = msg.get("bw")
        self.sf = msg.get("sf")
        self.power = msg.get("power_dbm", ether_module.DEFAULT_POWER_DBM)
        self.start = at
        self.pre = at + pre
        self.hdr = at + hdr
        self.end = at + self.span
        self.batch = 0                  # rx_begins recorded at its instant before it
        self.eid = None
        self.begins = []
        self.ends = []                  # (receiver, slot, verdict)

    def fits(self, said, virtual):
        """Whether an rx_begin can be about this frame: sent after it, to
        another station, inside its air; in virtual time, whose T is the
        record's clock, ending when the frame does."""
        if (self.line > said.line or self.sid == said.rsid
                or not 0 <= said.t_end - said.t0 <= self.span):
            return False
        return not virtual or said.t_end == self.end

    def blind_us(self):
        return BLIND_SYMBOLS * (1 << (self.sf or 7)) * 1e6 / float(self.bw or 125_000)


class Record:
    """A run's record read into frames, each with what the ether told every
    slot of it; each slot's stated modes; and how many rx messages named a
    frame that could not be found."""

    def __init__(self, path, level_at=None):
        self.frames = []
        self.modes = collections.defaultdict(list)     # (sid, slot) -> [(line, mode)]
        self.virtual = None
        self.origin = self.last = None
        self.untied = 0
        numbered = []               # real time: the ether's numbering, from 1
        begins = {}                 # the ether's number -> what its rx_begins said
        ends = []
        instant, begun = None, 0    # rx_begins recorded so far at this instant
        for line, (stamp, direction, sid, msg) in enumerate(record_module.lines(path)):
            at = to_us(stamp)
            if self.virtual is None:
                self.virtual = ":" not in stamp
                self.origin = 0 if self.virtual else at
            self.last = at
            if at != instant:
                instant, begun = at, 0
            kind = msg.get("type")
            if direction == "in" and kind == "tx":
                try:
                    frame = Frame(line, at, sid, msg)
                except (TypeError, ValueError):
                    continue
                frame.batch = begun
                self.frames.append(frame)
                if isinstance(msg.get("sid"), int):
                    numbered.append(frame)  # the ether takes a `tx` from a numbered station
            elif direction == "in" and kind == "state":
                self.modes[(sid, msg.get("slot", 0))].append((line, msg.get("mode")))
            elif direction == "out" and kind == "rx_begin":
                begun += 1
                try:
                    said = Said(line, sid, int(msg["t0"]), int(msg["t_end"]), msg)
                except (KeyError, TypeError, ValueError):
                    self.untied += 1
                    continue
                begins.setdefault(msg.get("id"), []).append(said)
            elif direction == "out" and kind == "rx_end":
                ends.append((sid, msg))
        if self.virtual:
            self.by_eid = self.tie(begins, level_at)
        else:
            # ether.py datagram_received: each `tx` recorded, then taken and
            # numbered, before the next datagram is read.
            self.by_eid = {eid: numbered[eid - 1] for eid in begins
                           if isinstance(eid, int) and 1 <= eid <= len(numbered)}
        for eid, said in begins.items():
            frame = self.by_eid.get(eid)
            for s in said:
                if frame is None or not frame.fits(s, self.virtual):
                    self.untied += 1
                    continue
                # Its t0 is when the slot was told, on the ether's clock, and
                # its t_end the frame's end there: told after the frame's
                # start when what is left of the frame is shorter than all of it.
                frame.begins.append(Begin(s.line, s.rsid, s.msg.get("slot", 0),
                                          s.t_end - s.t0 < frame.span, not s.msg.get("cad")))
        for rsid, msg in ends:
            frame = self.by_eid.get(msg.get("id"))
            if frame is None:
                self.untied += 1
            else:
                frame.ends.append((rsid, msg.get("slot", 0), msg.get("verdict")))

    def tie(self, begins, level_at):
        """Virtual time: the frame each of the ether's numbers is.

        A number's frame ends at the T its rx_begins name, and the preamble's
        and header's ends they carry, on the ether's clock, pin its start.
        Frames alike in all of that take their numbers in the order the
        barrier took them, station by station within one batch (ether.py
        take_held), each the first of them every rx_begin of its number
        fits, at the level each names where any does.
        """
        alike = collections.defaultdict(list)       # (pre, hdr, end) -> frames
        ending = collections.defaultdict(list)      # end -> frames
        for f in self.frames:
            alike[(f.pre, f.hdr, f.end)].append(f)
            ending[f.end].append(f)
        groups = collections.defaultdict(list)
        for eid, said in begins.items():
            if isinstance(eid, int):
                key = (said[0].msg.get("t_pre"), said[0].msg.get("t_hdr"), said[0].t_end)
                groups[key if key in alike else said[0].t_end].append(eid)

        def heard(frame, s):
            level = level_at(frame, s.rsid) if level_at is not None else None
            return s.msg.get("level") is None or (level is not None
                                                  and round(level) == s.msg["level"])

        by_eid = {}
        # Whole timelines first; numbers whose rx_begins pin no frame's
        # preamble and header then take a frame by its end alone.
        for key in sorted(groups, key=lambda k: not isinstance(k, tuple)):
            frames = sorted((f for f in (alike[key] if isinstance(key, tuple) else ending[key])
                             if f.eid is None), key=lambda f: (f.start, f.batch, f.sid, f.line))
            at = 0
            for eid in sorted(groups[key]):
                said = begins[eid]
                fitting = [i for i in range(at, len(frames))
                           if all(frames[i].fits(s, True) for s in said)]
                chosen = next((i for i in fitting if all(heard(frames[i], s) for s in said)),
                              fitting[0] if fitting else None)
                if chosen is not None:
                    frames[chosen].eid = eid
                    by_eid[eid] = frames[chosen]
                    at = chosen + 1
        return by_eid

    def seconds(self, at):
        """Run seconds: T, or the wall clock since the record's first line."""
        return (at - self.origin) / 1e6

    def mode_at(self, sid, slot, line):
        """The mode a slot had last stated before this line of the record."""
        stated = self.modes.get((sid, slot))
        if not stated:
            return None
        i = bisect.bisect_left(stated, (line,)) - 1
        return stated[i][1] if i >= 0 else None


class Air:
    """The run's medium asked about frames: the level one reaches a station
    at, and whether that station decodes it."""

    def __init__(self, medium):
        self.medium = medium
        self.levels = {}

    def level(self, frame, rsid):
        key = (frame.sid, rsid, frame.freq, frame.power)
        if key not in self.levels:
            self.levels[key] = (None if frame.freq is None else
                                self.medium.level(frame.sid, rsid, frame.freq, frame.power))
        return self.levels[key]

    def hears(self, rsid, frame):
        return self.medium.audible(self.level(frame, rsid), frame.bw, frame.sf)


def shared_air(frames):
    """Every two transmissions whose air overlapped, the one that began first
    first; frames that began at one instant in the record's order."""
    on_air = []
    for f in sorted(frames, key=lambda f: (f.start, f.line)):
        on_air = [g for g in on_air if g.end > f.start]
        for g in on_air:
            if f.end > g.start:
                yield g, f
        on_air.append(f)


def carrier_sense(record, air, pairs):
    """Every transmission that began over a frame on its carrier that its
    sender could decode, one event per such frame; and every two that began
    at the same instant on one carrier where either sender could decode the
    other, which no carrier sense can prevent (`together`)."""
    events, together = [], []
    for g, f in pairs:
        if g.sid == f.sid:
            continue
        if g.start == f.start:
            # Neither was on the air when the other began: both were in each
            # other's blind window, at its very start.
            if (ether_module.same_carrier(g.freq, f.freq, max(g.bw or 0, f.bw or 0))
                    and (air.hears(f.sid, g) or air.hears(g.sid, f))):
                together.append({"sid": g.sid, "other": f.sid,
                                 "at": round(record.seconds(g.start), 6)})
            continue
        if not ether_module.same_carrier(g.freq, f.freq, max(g.bw or 0, f.bw or 0)):
            continue
        if not air.hears(f.sid, g):
            continue
        told = [b for b in g.begins if b.rsid == f.sid and b.slot == f.slot and b.line < f.line]
        on_time = [b for b in told if not b.late]
        how = "told" if on_time else "told late" if told else "never told"
        age = f.start - g.start
        window = ("blind" if age < g.blind_us() else "preamble" if f.start < g.pre
                  else "payload")
        events.append({
            "sid": f.sid, "over": g.sid, "at": round(record.seconds(f.start), 6),
            "on_air_ms": round(age / 1000.0, 3), "level_dbm": round(air.level(g, f.sid), 1),
            "told": how == "told", "told_late": how == "told late",
            "lock": any(b.lock for b in on_time), "window": window,
            "mode": record.mode_at(f.sid, f.slot, g.line)})
    return events, together


def how_told(event):
    return "told" if event["told"] else "told late" if event["told_late"] else "never told"


def collisions(record, air, pairs):
    """Every reception that ended `crc`, with the transmissions that shared
    its air and band as the ether counts them (`Frame.shares_air`) and
    reached its receiver."""
    shared = collections.defaultdict(list)
    for g, f in pairs:
        if ether_module.in_band(g.freq, g.bw, f.freq, f.bw):
            shared[g].append(f)
            shared[f].append(g)
    receptions = []
    for g in record.frames:
        for rsid, _slot, verdict in g.ends:
            if verdict != "crc":
                continue
            sending, over = False, []
            for h in shared[g]:
                if h.sid == rsid:
                    sending = True
                elif air.level(h, rsid) is not None:
                    # A station's own two frames at once (two slots, or one
                    # that talked over itself) are neither hidden nor heard.
                    senders = ("same" if h.sid == g.sid else "in earshot"
                               if air.hears(g.sid, h) or air.hears(h.sid, g) else "hidden")
                    over.append({"sid": h.sid, "senders": senders})
            receptions.append({"sid": g.sid, "rsid": rsid, "at": round(record.seconds(g.start), 6),
                               "receiver_sending": sending, "overlaps": over})
    overlaps = collections.Counter(o["senders"] for r in receptions for o in r["overlaps"])
    by_receiver = collections.Counter(r["rsid"] for r in receptions)
    return {"spoiled": len(receptions),
            "by_receiver": dict(sorted(by_receiver.items())),
            "overlaps": {key: overlaps[key] for key in ("hidden", "in earshot", "same")},
            "receiver_sending": sum(1 for r in receptions if r["receiver_sending"]),
            "no_overlap": sum(1 for r in receptions
                              if not r["overlaps"] and not r["receiver_sending"]),
            "receptions": receptions}


def judge(view, record_path=None):
    """The report on a run opened as a RunView, as a dict (what --json
    prints); None when it has no record."""
    path = record_path or view.record_path
    if not os.path.isfile(path):
        return None
    air = Air(view.medium())
    record = Record(path, air.level)
    pairs = list(shared_air(record.frames))
    events, together = carrier_sense(record, air, pairs)
    counts = {how: {window: 0 for window in WINDOWS} for how in HOW}
    for event in events:
        counts[how_told(event)][event["window"]] += 1
    silent = collections.Counter((f.sid, f.freq, f.sf, f.bw)
                                 for f in record.frames if not f.begins)
    return {
        "virtual": bool(record.virtual),
        "run_s": round(record.seconds(record.last), 6) if record.last is not None else 0.0,
        "frames": len(record.frames),
        "stations": len({f.sid for f in record.frames}),
        "untied": record.untied,
        "carrier_sense": events,
        "carrier_sense_counts": counts,
        "began_together": together,
        "unheard": [{"sid": s, "freq": fr, "sf": sf, "bw": bw, "frames": n}
                    for (s, fr, sf, bw), n in sorted(silent.items(), key=lambda kv: tuple(
                        -1 if x is None else x for x in kv[0]))],
        "collisions": collisions(record, air, pairs),
    }


def analyse(run_dir, record_path=None):
    """The report on the run in `run_dir`, for a script or another tool."""
    return judge(RunView(run_dir), record_path)


def render(report, view, detail=False):
    """The report as text, stations by name."""
    def who(sid):
        name = view.names.get(sid)
        if name is None:
            return "station %s" % sid
        kind = view.kind_type(name)
        return "%s (%s)" % (name, kind) if kind else name

    out = ["run: %.1f s of %s time, %d frames from %d stations" % (
        report["run_s"], "virtual" if report["virtual"] else "real", report["frames"],
        report["stations"])]
    if report["untied"]:
        out.append("  %d rx messages named a frame not found in the record; "
                   "that frame is judged without them" % report["untied"])

    events = report["carrier_sense"]
    what = "a transmission began over a frame on its carrier that its sender could decode"
    out += ["", "carrier sense: " + (
        "no " + what[2:] if not events else
        "%s, %s" % (what, "once" if len(events) == 1 else "%d times" % len(events)))]
    together = report.get("began_together") or []
    if together:
        out.append("  and %d time%s two transmissions began at one instant on one carrier, "
                   "a sender able to decode the other: no carrier sense can see that" % (
                       len(together), "" if len(together) == 1 else "s"))
    if events:
        counts = report["carrier_sense_counts"]
        out.append("  %-12s%s" % ("", "".join("%10s" % w for w in WINDOWS)))
        for how in HOW:
            out.append("  %-12s%s" % (how, "".join("%10d" % counts[how][w] for w in WINDOWS)))
    by = collections.Counter((e["sid"], how_told(e), e["window"]) for e in events)
    for (sid, how, window), n in sorted(by.items(), key=lambda kv: (
            kv[0][0], HOW.index(kv[0][1]), WINDOWS.index(kv[0][2]))):
        out.append("  %-24s %5d  over a frame in its %-8s  %s" % (who(sid), n, window, how))
    if detail:
        for e in events:
            out.append("    %.6f s  %s over %s's frame, %.1f ms into it, at %.1f dBm: %s; %s" % (
                e["at"], who(e["sid"]), who(e["over"]), e["on_air_ms"], e["level_dbm"],
                how_told(e) + (" (a lock)" if e["lock"] else " (energy)" if e["told"] else ""),
                "the slot was %s when that frame went out" % e["mode"] if e["mode"]
                else "the slot had stated no mode yet"))

    out += ["", "frames nobody was told of:"]
    for u in report["unheard"]:
        out.append("  %-24s %5d  on %s MHz, SF%s, %s kHz" % (
            who(u["sid"]), u["frames"], "?" if u["freq"] is None else "%.3f" % (u["freq"] / 1e6),
            u["sf"], "?" if u["bw"] is None else "%g" % (u["bw"] / 1e3)))
    if not report["unheard"]:
        out.append("  none")

    col = report["collisions"]
    out += ["", "collisions: %d receptions ended crc" % col["spoiled"]]
    if col["spoiled"]:
        out.append("  overlapping transmissions that reached the receiver: %d from a sender "
                   "hidden from the frame's, %d from one in earshot%s" % (
                       col["overlaps"]["hidden"], col["overlaps"]["in earshot"],
                       ", %d from the frame's own sender" % col["overlaps"]["same"]
                       if col["overlaps"]["same"] else ""))
        out.append("  spoiled while the receiver itself was sending: %d; with nothing else "
                   "on the air there: %d" % (col["receiver_sending"], col["no_overlap"]))
    for rsid, n in col["by_receiver"].items():
        out.append("  %-24s %5d  receptions spoiled" % (who(rsid), n))
    if detail:
        for r in col["receptions"]:
            over = ", ".join("%s's (%s)" % (who(o["sid"]), o["senders"]) for o in r["overlaps"])
            out.append("    %.6f s  %s lost %s's frame%s%s" % (
                r["at"], who(r["rsid"]), who(r["sid"]), " under " + over if over else "",
                ", sending itself" if r["receiver_sending"] else ""))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="the run directory")
    ap.add_argument("--json", action="store_true", help="the whole report as JSON")
    ap.add_argument("--detail", action="store_true",
                    help="every carrier-sense event and spoiled reception, not only the counts")
    args = ap.parse_args(argv)
    view = RunView(args.run)
    report = judge(view)
    if report is None:
        print("%s has no record: nothing to judge" % args.run, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(render(report, view, args.detail), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
