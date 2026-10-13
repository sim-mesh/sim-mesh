"""Physics v2 against the bench that measured it (physics-v2.yaml).

The rules are replayed in process on the bench's own trials and curves — two
senders, one receiver, the levels and starts the bench had — and held to what
the boards delivered within binomial bounds: the proof that the medium does
what the board did, not only that its tables were typed in. The capture trials
are bench/capture-trials-2026-10-13.csv and the inter-SF trials
bench/inter-sf-trials-2026-10-13.csv (the reticulum project's
docs/bench-results-2026-10-13, one trial a line); cells the bench adds to them
join the checks as they are. Each test is named for the rule it holds.
"""
import asyncio
import base64
import collections
import csv
import math
import os

import pytest

import ether as ether_module
from test_ether import Air, FREQ, POWER_DBM, name, radio

HERE = os.path.dirname(os.path.abspath(__file__))
TRIALS = os.path.join(HERE, "bench", "capture-trials-2026-10-13.csv")
INTER_SF_TRIALS = os.path.join(HERE, "bench", "inter-sf-trials-2026-10-13.csv")

# The bench's frames: 125 kHz, CR 4/5, 16-symbol preambles; SF7 with the
# earlier frame 120 bytes and the later 40 unless a trial says otherwise.
BW, PREAMBLE = 125_000, 16
LEVEL_DBM = -40.0           # where the bench's frames arrived: far over the floor
SNR_CEILING_DB = 14.0       # what either chip reports at most, however clean
LEVEL_STEP_DB = 0.5         # how well the bench knew a level from one run to the next
CHIP_OF_BOARD = {"R": "sx1262", "A": "lr2021", "B": "lr2021"}


def loss_for(level_dbm):
    """The loss that lands a frame sent at POWER_DBM at `level_dbm`."""
    return POWER_DBM - level_dbm - 20.0 * math.log10(FREQ / 868_000_000)


def timings(sf, payload_len):
    """A frame's stamps after its start, in µs, by the medium's own arithmetic
    (`frame_blocks`): the end of the preamble and sync word, of the header,
    and of the frame."""
    symbol_us = (1 << sf) / BW * 1e6
    first, blocks, per_block, _ = ether_module.frame_blocks(sf, BW, 5, payload_len, False, True)
    pre_us = (PREAMBLE + ether_module.SYNC_SYMBOLS) * symbol_us
    hdr_us = pre_us + first * symbol_us
    return int(pre_us), int(hdr_us), int(hdr_us + blocks * per_block * symbol_us)


def tables():
    return ether_module.read_physics_file(ether_module.PHYSICS_V2_FILE)


@pytest.fixture
def v2():
    """An ether in process under physics v2, on a loop of its own."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield fresh()
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def fresh():
    """A new ether under physics v2: every replay starts on clean air, so
    none can rewind the clock past what another left on it."""
    return ether_module.Ether(None, physics=ether_module.Physics(**tables()))


def send(air, sid, payload_len, t0, pre_us, hdr_us, span_us, power_dbm=POWER_DBM, **over):
    """A frame from `sid` with the stamps given; its ether number."""
    over.setdefault("sf", 7)
    over.setdefault("pre", PREAMBLE)
    air.medium.clock.t = t0
    air.medium.recv_tx(sid, air.ADDR, dict(
        {"type": "tx", "slot": 0, "id": 1, "t0": t0, "t_pre": t0 + pre_us,
         "t_hdr": t0 + hdr_us, "t_end": t0 + span_us, "power_dbm": power_dbm,
         "payload": base64.b64encode(bytes(payload_len)).decode()},
        **radio("TX", **over)))
    return air.medium.frames[-1].eid


def ends(air, sid):
    """Frame number -> (verdict, reported SNR), from this station's rx_ends."""
    return {m["id"]: (m["verdict"], m["snr"]) for m in air.told(sid, "rx_end")}


def binomial_p(k, n, p):
    """The exact two-sided chance of a count as unlikely as `k` of `n` at `p`."""
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    odds = [math.comb(n, j) * p ** j * (1.0 - p) ** (n - j) for j in range(n + 1)]
    return min(1.0, sum(o for o in odds if o <= odds[k] * (1.0 + 1e-9)))


def trials(identical="0"):
    """The bench's counted capture trials: of two different frames (the
    tables are fitted on these), or of identical ones (`identical` "1", an
    extra check: on the bench they behaved as different frames do)."""
    with open(TRIALS, newline="") as handle:
        return [r for r in csv.DictReader(handle)
                if r["part"] != "edge" and (r.get("identical") or "0") == identical]


def shape(row):
    """A trial's spreading factor and the two frames' lengths: SF7, 120 and
    40 bytes unless the row names them (`sf`, a column naming the earlier
    frame's length, one the later's)."""
    def length(which, default):
        for key, value in row.items():
            if which in key and ("len" in key or "bytes" in key) and value:
                return int(value)
        return default
    return int(row.get("sf") or 7), length("earlier", 120), length("later", 40)


# ---- the tables, and what they say ------------------------------------------

def test_physics_v1_is_untouched_without_the_tables():
    physics = ether_module.Physics()
    assert not physics.v2 and "version" not in physics.as_dict()
    assert physics.sense_factor("sx1262", 7, 6) == 0.0
    assert physics.chip_loss("sx1262", 7) == 0.0


def test_physics_v2_round_trips_its_tables():
    physics = ether_module.Physics(**tables())
    again = ether_module.Physics.from_dict(physics.as_dict())
    assert again.v2 and again.chips == physics.chips
    assert "physics v2" in physics.describe()
    with pytest.raises(ValueError):
        ether_module.Physics(version=2)


def test_the_chip_loss_puts_the_reference_frame_at_its_limit():
    """Under another spreading factor read as noise, the reference frame
    fails one time in ten at the chip's measured ratio."""
    physics = ether_module.Physics(**tables())
    ref = physics.reference
    for chip, limits in ((c, t["sinr_limit_db"]) for c, t in physics.chips.items()):
        for sf, limit in limits.items():
            snr = limit - ether_module.implementation_loss(sf) - physics.chip_loss(chip, sf)
            per = 1.0 - ether_module.frame_odds(sf, snr, ref["payload"], ref["cr"],
                                                ref["implicit"], ref["crc"], ref["bw"])
            # The curve is kept per SNR_STEP_DB, so the rate lands within a step's worth.
            assert per == pytest.approx(ref["per"], abs=0.01), (chip, sf)
    assert physics.chip_loss("sx1262", 9) == 0.0      # unmeasured: the curve's own limit


def test_the_capture_tables_read_as_the_bench_summarised_them():
    physics = ether_module.Physics(**tables())
    for chip in ("sx1262", "lr2021"):
        assert physics.capture_earlier(chip, 7, -3.0) > 0.97
        assert physics.capture_earlier(chip, 7, 3.0) < 0.08    # the logistic's tail: 7 % on the SX1262
        assert physics.capture_later(chip, 7, "payload", 12.0) == 0.0
        assert physics.capture_later(chip, 7, "preamble", 6.0) == 1.0
        assert physics.capture_later(chip, 7, "preamble", -5.0) == 0.0
    assert physics.capture_earlier("sx1262", 7, 1.4) == pytest.approx(0.5, abs=0.01)
    assert physics.capture_earlier("lr2021", 7, 1.0) == pytest.approx(0.5, abs=0.01)
    # SF6 has tables of its own; SF5 takes SF7's.
    assert physics.capture_earlier("sx1262", 6, 0.4) == pytest.approx(0.39, abs=0.03)
    assert physics.capture_later("lr2021", 6, "header", 3.0) == 1.0
    assert physics.capture(chip, 5) == physics.capture(chip, 7)


def test_the_sense_factor_is_one_for_its_own_sf_measured_for_a_neighbour_and_zero_beyond():
    physics = ether_module.Physics(**tables())
    assert physics.sense_factor("lr2021", 7, 7) == 1.0
    assert physics.sense_factor("lr2021", 6, 7) == pytest.approx(0.70)
    assert physics.sense_factor("sx1262", 7, 6) == pytest.approx(0.23)
    assert physics.sense_factor("sx1262", 7, 5) == 0.0
    assert physics.sense_factor("sx1262", None, 5) == 0.0


def test_the_false_busy_entry_names_every_chip_and_sf():
    spec = ether_module.cad_false_busy_spec(tables()["chips"])
    assert "lr2021.7:0.32" in spec.split(",") and "sx1262.5:0.07" in spec.split(",")
    assert ether_module.cad_false_busy_spec(None) == ""


def test_interp_holds_flat_outside_its_points():
    points = [[1.0, 0.0], [3.0, 1.0]]
    assert ether_module.interp(points, 0.0) == 0.0
    assert ether_module.interp(points, 2.0) == pytest.approx(0.5)
    assert ether_module.interp(points, 9.0) == 1.0
    assert ether_module.side_preamble_miss(13) == pytest.approx((0.019 + 0.004) / 2)


# ---- (a) the capture trials replayed ----------------------------------------

def capture_air(chip, sf, seed=0):
    """Station 1 sends the earlier frame, 3 the later, 2 receives both at
    `sf`, at LEVEL_DBM when sent at POWER_DBM; the later's level is its power."""
    loss = loss_for(LEVEL_DBM)
    medium = fresh()
    air = Air(medium, {(1, 2): loss, (3, 2): loss}, seed=seed,
              states={sid: {"sf": sf, "pre": PREAMBLE} for sid in (1, 2, 3)})
    medium.set_chips({2: chip})
    return air


def one_trial(air, t0, sf, lengths, start_us, d_db):
    """The bench's trial: the later frame `d_db` over the earlier, starting
    `start_us` into it. Returns (E, L, N or B; the delivered frame's SNR;
    whether that frame's end lay under the other one)."""
    air.sent[:] = []
    earlier = send(air, 1, lengths[0], t0, *timings(sf, lengths[0]), sf=sf)
    later = send(air, 3, lengths[1], t0 + start_us, *timings(sf, lengths[1]),
                 power_dbm=POWER_DBM + d_db, sf=sf)
    air.run_to(t0 + timings(sf, lengths[0])[2] + timings(sf, lengths[1])[2] + 1_000)
    got = ends(air, 2)
    e, l = got.get(earlier, ("", 0))[0] == "clean", got.get(later, ("", 0))[0] == "clean"
    if e and l:
        return "B", None, None
    if e:
        return "E", got[earlier][1], covered(sf, lengths, start_us, "E")
    if l:
        return "L", got[later][1], covered(sf, lengths, start_us, "L")
    return "N", None, None


def covered(sf, lengths, start_us, delivered):
    """Whether the delivered frame's end lay under the other frame: the
    later one's when it ends inside the earlier, the earlier's when the
    later outlasts it."""
    later_end = start_us + timings(sf, lengths[1])[2]
    earlier_end = timings(sf, lengths[0])[2]
    return later_end < earlier_end if delivered == "L" else later_end > earlier_end


def replay(rows, replays, chip, seed=0):
    """Every row of the bench replayed `replays` times at distinct instants:
    (shape, part, d) -> Counter of outcomes, and (that, the frame delivered,
    whether its end was covered) -> the SNRs it reported."""
    cells = collections.defaultdict(collections.Counter)
    snrs = collections.defaultdict(list)
    for sf, lengths in sorted({(s, (le, la)) for s, le, la in map(shape, rows)}):
        air = capture_air(chip, sf, seed)
        t0 = 1_000_000
        for row in rows:
            if shape(row) != (sf, lengths[0], lengths[1]):
                continue
            d = float(row["later_minus_earlier_db"])
            start = int(round(float(row["start_ms"]) * 1000))
            for _ in range(replays):
                cell = ((sf,) + lengths, row["part"], round(d, 1))
                outcome, snr, under = one_trial(air, t0, sf, lengths, start, d)
                cells[cell][outcome] += 1
                if snr is not None:
                    snrs[(cell, outcome, under)].append(min(snr, SNR_CEILING_DB))
                t0 += 1_000_000
    return cells, snrs


def bench_cells(rows):
    cells = collections.defaultdict(collections.Counter)
    snrs = collections.defaultdict(list)
    for row in rows:
        sf, le, la = shape(row)
        cell = ((sf, le, la), row["part"], round(float(row["later_minus_earlier_db"]), 1))
        cells[cell][row["delivered"]] += 1
        if row["delivered"] in "EL" and row.get("snr_db"):
            start = int(round(float(row["start_ms"]) * 1000))
            under = covered(sf, (le, la), start, row["delivered"])
            snrs[(cell, row["delivered"], under)].append(float(row["snr_db"]))
    return cells, snrs


def cells_off(rows, chip, replays=10):
    """The bench's cells (the frames' shape, the part the later frame
    started in, the level difference) whose delivered counts fall outside
    the binomial bounds of what the medium delivers there, and the medium's
    cells themselves."""
    bench, _ = bench_cells(rows)
    sim, _ = replay(rows, replays, chip)
    off = []
    for cell, counts in bench.items():
        n_bench, n_sim = sum(counts.values()), sum(sim[cell].values())
        if n_bench < 5:
            continue            # a cell too small to hold anything to
        for what in "EL":
            # A table that says never or always is held to one trial in the
            # cell's worth: the bench's lone exceptions (a frame 6 dB down lost
            # once in 60) are not the medium's to reproduce.
            p_sim = min(max(sim[cell][what] / n_sim, 1.0 / n_bench), 1.0 - 1.0 / n_bench)
            p = binomial_p(counts[what], n_bench, p_sim)
            if p < 0.01:
                off.append((cell, what, counts[what], n_bench, round(p_sim, 2), round(p, 4)))
    return off, sim


@pytest.mark.parametrize("board", ["R", "A"])
def test_the_capture_trials_replayed_deliver_what_the_boards_did(v2, board):
    """Every cell of the bench (chip, the frames' shape, the part the later
    frame started in, the level difference): what the boards delivered is
    within the binomial bounds of what the medium delivers there, no trial
    delivers both, and a later frame starting in the payload is never
    received, whether or not it outlasts the earlier one."""
    if not os.path.exists(TRIALS):
        pytest.skip("no capture trials beside this file")
    rows = [r for r in trials() if r["rx"] == board]
    off, sim = cells_off(rows, CHIP_OF_BOARD[board])
    assert all(c["B"] == 0 for c in sim.values())
    assert all(c["L"] == 0 for (_, part, _), c in sim.items() if part == "payload")
    # One curve by the level difference alone for the earlier frame, and one
    # table per part for the later one, serve both lengths at a spreading
    # factor; the cells next to the capture threshold sit either side of them
    # (INTERNALS.md, "Physics v2"): a few cells per shape outside the bound,
    # never many.
    shapes = {cell[0] for cell, *_ in off}
    assert all(sum(1 for cell, *_ in off if cell[0] == s) <= 4 for s in shapes), off


def test_identical_frames_meet_as_different_ones_do(v2):
    """The bench's trials of identical frames (T157): the tables fitted on
    different frames hold for them too, cell by cell."""
    rows = [r for r in trials("1") if r["rx"] in CHIP_OF_BOARD]
    if not rows:
        pytest.skip("no identical-frame trials in the file yet")
    for board in sorted({r["rx"] for r in rows}):
        off, sim = cells_off([r for r in rows if r["rx"] == board], CHIP_OF_BOARD[board])
        assert all(c["B"] == 0 for c in sim.values())
        shapes = {cell[0] for cell, *_ in off}
        assert all(sum(1 for cell, *_ in off if cell[0] == s) <= 4 for s in shapes), (board, off)


@pytest.mark.parametrize("board", ["R", "A"])
def test_a_delivered_frame_reports_the_snr_of_its_last_symbol(v2, board):
    """Where the bench reported a delivered frame's SNR and that frame's end
    lay under the other one (cells of five or more, by the frame delivered):
    the medium's mean, its lead over the other frame at its last symbol,
    sits within 2.5 dB of the board's in every cell but a few. A frame whose
    end is in the clear reads the chip's ceiling here; the boards read
    below it when most of the frame lay under the other (SF6, 40 bytes
    against 40: 7.5 to 11 dB), which the rule does not give."""
    if not os.path.exists(TRIALS):
        pytest.skip("no capture trials beside this file")
    rows = [r for r in trials() if r["rx"] == board]
    _, bench = bench_cells(rows)
    _, sim = replay(rows, 3, CHIP_OF_BOARD[board])
    off, checked = [], 0
    for key, got in bench.items():
        if not key[2] or len(got) < 5 or len(sim[key]) < 3:
            continue
        checked += 1
        want = sum(got) / len(got)
        have = sum(sim[key]) / len(sim[key])
        if abs(have - want) > 2.5:
            off.append((key, round(want, 1), round(have, 1)))
    assert checked >= 5
    assert len(off) <= 2, off


def test_the_capture_replay_is_the_seeds_and_the_channels_alone(v2):
    if not os.path.exists(TRIALS):
        pytest.skip("no capture trials beside this file")
    rows = [r for r in trials()
            if r["rx"] == "R" and 0.0 < float(r["later_minus_earlier_db"]) < 3.5][:60]
    once = replay(rows, 2, "sx1262", seed=5)
    again = replay(rows, 2, "sx1262", seed=5)
    assert once == again
    assert replay(rows, 2, "sx1262", seed=6) != once


# ---- (b) another spreading factor as noise -----------------------------------

def inter_sf_curve(v2, chip, wanted_sf, other_sf, sirs, trials_per_point=200):
    """A 40-byte frame at `wanted_sf` wholly inside a frame at `other_sf`
    `sir` dB under it at the receiver: the packet error rate per SIR."""
    loss = loss_for(LEVEL_DBM)
    medium = fresh()
    air = Air(medium, {(1, 2): loss, (3, 2): loss}, seed=0,
              states={1: {"sf": wanted_sf}, 2: {"sf": wanted_sf}, 3: {"sf": other_sf}})
    medium.set_chips({2: chip})
    pre, hdr, span = timings(wanted_sf, 40)
    t0, per = 1_000_000, {}
    for sir in sirs:
        lost = 0
        for _ in range(trials_per_point):
            air.sent[:] = []
            send(air, 3, 120, t0, 20_000, 30_000, 600_000, power_dbm=POWER_DBM - sir, sf=other_sf)
            wanted = send(air, 1, 40, t0 + 50_000, pre, hdr, span, sf=wanted_sf)
            air.run_to(t0 + 700_000)
            lost += ends(air, 2).get(wanted, ("", 0))[0] != "clean"
            t0 += 1_000_000
        per[sir] = lost / trials_per_point
    return per


def crossing(per, at):
    """Where the error rate falls through `at`, by a straight line."""
    sirs = sorted(per)
    for lo, hi in zip(sirs, sirs[1:]):
        if per[lo] >= at > per[hi]:
            return lo + (per[lo] - at) / (per[lo] - per[hi]) * (hi - lo)
    return None


# B5's eight curves (13 October 2026, normal images, the LR2021's side
# detectors off): (chip, the wanted SF, the interfering SF) -> the SIR at
# which the wanted 40-byte frame was lost one time in ten, and one time in a
# hundred. SF7's table figure is the mean of its two pairs.
B5 = {("sx1262", 5, 7): (-2.2, -1.2), ("sx1262", 6, 7): (-5.3, -3.1),
      ("sx1262", 7, 5): (-8.3, -7.5), ("sx1262", 7, 6): (-7.4, -6.3),
      ("lr2021", 5, 7): (-3.8, -2.9), ("lr2021", 6, 7): (-5.6, -3.7),
      ("lr2021", 7, 5): (-8.9, -8.0), ("lr2021", 7, 6): (-8.1, -7.2)}


@pytest.mark.parametrize("chip,wanted_sf,other_sf", sorted(B5))
def test_another_sf_is_noise_and_the_curve_sits_where_the_bench_measured(v2, chip, wanted_sf, other_sf):
    """The bench's eight curves: the error rate is 10 % within 0.6 dB of the
    pair's measured ratio (SF7's two pairs sit either side of their mean),
    it falls from 10 % to 1 % over 0.8 to 2.2 dB as the bench's do, and from
    90 % to 10 % over 1.5 to 3.5 dB, whichever spreading factor interferes."""
    at_10_bench, at_1_bench = B5[(chip, wanted_sf, other_sf)]
    limit = v2.physics.chip(chip)["sinr_limit_db"][wanted_sf]
    sirs = [round(limit - 3.0 + 0.5 * i, 1) for i in range(13)]
    per = inter_sf_curve(v2, chip, wanted_sf, other_sf, sirs, trials_per_point=400)
    at_10, at_1, at_90 = crossing(per, 0.10), crossing(per, 0.01), crossing(per, 0.90)
    assert at_10 is not None and at_10 == pytest.approx(at_10_bench, abs=0.6), per
    assert at_1 is not None and 0.8 <= at_1 - at_10 <= 2.2, (per, at_10, at_1)
    assert at_90 is not None and 1.5 <= at_10 - at_90 <= 3.5, per
    assert per[sirs[-1]] <= 0.01 and per[sirs[0]] >= 0.99, per
    assert 0.8 <= at_1_bench - at_10_bench <= 2.2    # the bench's own span, as stated


def inter_sf_points():
    """The bench's overlap trials by curve: (board, wanted SF, interfering SF,
    curve) -> {SIR: (n, delivered)}."""
    out = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    with open(INTER_SF_TRIALS, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["kind"] != "overlap":
                continue
            key = (row["rx"], int(row["sf_wanted"]), int(row["sf_interferer"]), row["curve"])
            cell = out[key][round(float(row["sir_db"]), 1)]
            cell[0] += 1
            cell[1] += int(row["delivered"])
    return out


def per_at(per, sir):
    """The error rate at `sir`, a straight line between the points measured."""
    sirs = sorted(per)
    if sir <= sirs[0]:
        return per[sirs[0]]
    for lo, hi in zip(sirs, sirs[1:]):
        if sir <= hi:
            return per[lo] + (per[hi] - per[lo]) * (sir - lo) / (hi - lo)
    return per[sirs[-1]]


@pytest.mark.parametrize("key", sorted(inter_sf_points()) if os.path.exists(INTER_SF_TRIALS) else [])
def test_the_inter_sf_trials_replayed_point_by_point(v2, key):
    """Every measured point of a B5 curve (12 to 112 trials each): what the
    board delivered is within the binomial bounds of what the medium delivers
    at that ratio, the ratio taken as the bench knew it, to half a decibel.
    The medium's curves fall from 90 % to 10 % over 2 dB where the boards'
    fell over 1 to 3 dB, so a point or two on a curve's slope can sit outside."""
    board, wanted_sf, other_sf, _ = key
    bench = inter_sf_points()[key]
    sirs = sorted(bench)
    grid = [round(sirs[0] - 1.0 + 0.25 * i, 2)
            for i in range(int((sirs[-1] - sirs[0] + 2.0) / 0.25) + 1)]
    sim = inter_sf_curve(v2, CHIP_OF_BOARD[board], wanted_sf, other_sf, grid, trials_per_point=200)
    off = []
    for sir, (n, delivered) in bench.items():
        if n < 10:
            continue
        best = 0.0
        for shift in (-LEVEL_STEP_DB, 0.0, LEVEL_STEP_DB):
            p_sim = min(max(1.0 - per_at(sim, sir + shift), 1.0 / n), 1.0 - 1.0 / n)
            best = max(best, binomial_p(delivered, n, p_sim))
        if best < 0.01:
            off.append((sir, delivered, n, round(1.0 - per_at(sim, sir), 2), round(best, 4)))
    assert len(off) <= 2, off


def test_without_the_other_frame_the_wanted_frame_always_arrives(v2):
    air = Air(v2, {(1, 2): loss_for(LEVEL_DBM)}, seed=0, states={1: {"sf": 5}, 2: {"sf": 5}})
    for n in range(50):
        air.sent[:] = []
        eid = send(air, 1, 40, 1_000_000 * (n + 1), 5_000, 7_000, 25_000, sf=5)
        air.run_to(1_000_000 * (n + 1) + 30_000)
        assert ends(air, 2)[eid][0] == "clean"


# ---- (c) a channel check across spreading factors ----------------------------

def looks(v2, chip, slot_sf, frame_sf, n=400):
    """A slot in CAD at `slot_sf`, a strong frame at `frame_sf` on the air:
    how many of `n` looks are told of it."""
    medium = fresh()
    air = Air(medium, {(1, 2): loss_for(LEVEL_DBM)}, seed=0,
              states={1: {"sf": frame_sf}, 2: {"mode": "CAD", "sf": slot_sf}})
    medium.set_chips({2: chip})
    told = 0
    for i in range(n):
        air.sent[:] = []
        send(air, 1, 40, 1_000_000 * (i + 1), 20_000, 30_000, 100_000, sf=frame_sf)
        told += any(m.get("cad") for m in air.told(2, "rx_begin"))
    return told


@pytest.mark.parametrize("chip,share", [("sx1262", 0.23), ("lr2021", 0.60)])
def test_a_cad_hears_a_neighbouring_sf_as_often_as_the_chip_does(v2, chip, share):
    n = 400
    told = looks(v2, chip, 7, 6, n)
    assert abs(told - share * n) <= 3.0 * math.sqrt(n * share * (1 - share)), told
    assert looks(v2, chip, 7, 5, n) == 0
    assert looks(v2, chip, 7, 7, n) == n


def test_the_energy_threshold_still_serves_an_rssi_read_in_rx(v2):
    """An RX slot is told the energy of a strong frame at another spreading
    factor (what an instantaneous RSSI reads); a CAD slot two spreading
    factors away is not."""
    air = Air(v2, {(1, 2): loss_for(-60.0)}, seed=0, states={1: {"sf": 5}, 2: {"sf": 7}})
    send(air, 1, 40, 1_000_000, 5_000, 7_000, 25_000, sf=5)
    assert [m.get("cad") for m in air.told(2, "rx_begin")] == [True]
