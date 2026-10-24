"""The board: the radio hardware every node is, between its firmware and its antenna.

There is one board, an SX1262. A node's `max_dbm` in its nodeset is its
**maximum power**, in dBm at the antenna connector: 22 when it states none,
at most 27. At 22 dBm or below the node is a bare SX1262, the chip putting
out its maximum itself. Above 22 dBm it is an SX1262 behind a GC1109
front-end module (FEM), as a Heltec WiFi LoRa 32 V4 is: the chip drives the
front end, whose measured transmit curve takes it to at most 27 dBm at the
connector, and which adds 20 dB of receive gain ahead of the chip.

A node's maximum power is what a station sends at: coverage and links draw
every node at it, the startup script's radio sets it (`tx_dbm="max"`), a
power a rule asks above it is held to it, a script's
`.radio(tx_dbm="max")` sets it, and a station's first-boot lines have it as
`{max_dbm}`.

A station is told its board at start, `SIM_MESH_BOARD` in its environment (one
flat JSON object: the chip, the node's maximum, and the front end's figures
when it has one). The chip model applies the front end's curve and receive
gain from it, and firmware that models a front end at run time takes the
same figures.
"""

import json

import store

CHIP = "sx1262"
CHIP_DBM = (-9, 22)             # what an SX1262's power register takes
CHIP_MAX_DBM = CHIP_DBM[1]      # a node's maximum when it states none
FEM_MAX_DBM = 27                # the most the GC1109 front end puts on the connector
# The GC1109 as a Heltec V4 carries it: its transmit curve (chip register ->
# connector dBm, the CONFIG_LORA0_TX_CAL form) and its receive gain.
FEM = {"part": "gc1109",
       "tx_cal": "gc1109 measured 1:7,5:12,10:20,12:23,14:24,16:25,18:27,20:28,22:27",
       "gain_db": 0, "rx_gain_db": 20}


def check(value, where):
    """A node's `max_dbm` checked: None for none stated, else a number of
    dBm from the chip's lowest to the front end's highest."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as err:
        raise store.StoreError("%s: max_dbm is a number of dBm" % where) from err
    if not CHIP_DBM[0] <= out <= FEM_MAX_DBM:
        raise store.StoreError("%s: max_dbm is %d to %d dBm, not %g"
                               % (where, CHIP_DBM[0], FEM_MAX_DBM, out))
    return out


def max_dbm(own):
    """A node's maximum power at the connector: its own `max_dbm`, else the
    chip's 22 dBm."""
    return float(CHIP_MAX_DBM if own is None else own)


def has_fem(own):
    """Whether a node of this `max_dbm` has the GC1109 front end."""
    return max_dbm(own) > CHIP_MAX_DBM


def environment(own, busy_us="", busy_tcxo=False, host_us=0, cad_false_busy="", noise_rise_db=None):
    """What a station of this `max_dbm` is told of its board: SIM_MESH_BOARD's
    JSON, one flat object (the chip model and hw-linux read it with a flat
    reader): chip, max_dbm, and fem_part, fem_tx_cal, fem_gain_db and
    fem_rx_gain_db when it has the front end; and, when a run asks for them,
    the chip's BUSY per command, `busy_us`, `busy_tcxo` 1 to charge the
    TCXO's start, `host_us`, the board's own time per transaction
    (radio/src/model.cpp, `busyFigures`), and `cad_false_busy`, how often a
    channel check reads a clear channel busy, per chip and spreading factor
    (physics v2's, ether.py's `cad_false_busy_spec`; radio/src/busy.h); and
    `noise_rise_db`, how far the node's place lifts its floor, when it has one
    (the nodeset's, measured), so its RSSI reads show it (radio/src/ether_link.h,
    `airReadingDbm`)."""
    out = {"chip": CHIP, "max_dbm": max_dbm(own)}
    if has_fem(own):
        out.update(fem_part=FEM["part"], fem_tx_cal=FEM["tx_cal"],
                   fem_gain_db=FEM["gain_db"], fem_rx_gain_db=FEM["rx_gain_db"])
    if busy_us:
        out["busy_us"] = busy_us
    if busy_tcxo:
        out["busy_tcxo"] = 1
    if host_us:
        out["host_us"] = host_us
    if cad_false_busy:
        out["cad_false_busy"] = cad_false_busy
    if noise_rise_db:
        out["noise_rise_db"] = noise_rise_db
    return json.dumps(out, sort_keys=True)


# ---- transmit power through the front end ------------------------------------

def _curve(env):
    """The front end's transmit curve from SIM_MESH_BOARD's JSON, as the chip
    model reads it: [(chip, connector), …] for the entry naming `fem_part`,
    or None for none."""
    part = env.get("fem_part") or ""
    if not part:
        return None
    for entry in str(env.get("fem_tx_cal") or "").split(";"):
        words = entry.split(None, 2)
        if len(words) < 3 or words[0] != part:
            continue
        points = []
        for pair in words[2].split(","):
            try:
                chip, ant = (int(x) for x in pair.split(":"))
            except ValueError:
                break
            if points and chip <= points[-1][0]:
                return None
            points.append((chip, ant))
        return points if len(points) >= 2 else None
    return None


def connector_dbm(board_env, chip_dbm):
    """What reaches the connector for a chip power on the board SIM_MESH_BOARD
    describes (`board_env`, its JSON): the front end's curve, straight lines
    between its points and flat outside them, rounded as the chip model
    rounds; else its flat gain; else the chip's own."""
    env = json.loads(board_env) if isinstance(board_env, str) else dict(board_env or {})
    points = _curve(env)
    if not points:
        return chip_dbm + int(env.get("fem_gain_db") or 0)
    if chip_dbm <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if chip_dbm <= x1:
            num, den = (y1 - y0) * (chip_dbm - x0), x1 - x0
            return y0 + ((num + den // 2) // den if num >= 0 else -((-num + den // 2) // den))
    return points[-1][1]


def chip_dbm(board_env, connector):
    """The chip power that puts `connector` dBm at the connector: the lowest
    setting that reaches it, else the one that comes nearest, as a firmware
    that converts through its front end's curve does."""
    best, best_ant = CHIP_DBM[0], connector_dbm(board_env, CHIP_DBM[0])
    for chip in range(CHIP_DBM[0], CHIP_DBM[1] + 1):
        ant = connector_dbm(board_env, chip)
        if ant >= connector:
            return chip
        if ant > best_ant:
            best, best_ant = chip, ant
    return best
