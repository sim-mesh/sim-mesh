/**
 * busy — BUSY as a board's chip spends it, from SIM_MESH_BOARD, read the same
 * way by every chip model (model.cpp, model_lr2021.cpp).
 *
 *   busy_us    `<command>:<µs>` entries separated by commas: the commands that
 *              keep BUSY high, and for how long, each named as its chip's
 *              datasheet names it. An entry for the board's own chip (`chip`,
 *              the SX1262 when absent) is written bare; one for another chip
 *              is qualified with that chip's name, `lr2021.CalibFE:10500`, so
 *              one run gives each chip of a mixed network its own figures.
 *   busy_tcxo  1: BUSY held through a TCXO's start-up (the SX1262's model).
 *   host_us    the board's own time per SPI transaction, its bus and its
 *              driver around it, held as BUSY after every transaction.
 *
 * busy_tcxo and host_us are the board's, measured with its own chip, so a
 * chip of another board, named only in qualified entries, takes neither:
 * what a RAK board's host spends per transaction is not the LR2021 board's.
 *
 * Unset, BUSY is never busy, on any chip.
 */
#ifndef SIMRADIO_BUSY_H
#define SIMRADIO_BUSY_H

#include <cstdint>
#include <string>
#include <vector>

struct BusyEntry {
    std::string command;    /* as the chip's datasheet names it */
    int32_t     us = 0;
};

struct BoardBusy {
    std::vector<BusyEntry> entries;   /* this chip's, in the order given */
    bool    tcxo = false;
    int32_t hostUs = 0;
};

/** SIM_MESH_BOARD's BUSY figures for `chip` ("sx1262", "lr2021"): the entries
 *  that are that chip's, whatever commands they name (its model checks them
 *  against its own), and none of another chip's; the TCXO's and the host's
 *  only if `chip` is the board's own. An entry that is not
 *  `[<chip>.]<command>:<µs>` is said on stderr and left out. */
BoardBusy readBoardBusy(const char* chip);

/** How often a channel check reads a clear channel busy, per look, by
 *  spreading factor: SIM_MESH_BOARD's `cad_false_busy`, `[<chip>.]<sf>:<share>`
 *  entries separated by commas, qualified as busy_us's are. The figures are
 *  physics v2's (ether/physics-v2.yaml), which testbed/boards.py writes here.
 *  Unset, a clear channel never reads busy. */
struct BoardFalseBusy {
    std::vector<std::pair<int, double>> bySf;
    double at(int sf) const;    /* 0 for a spreading factor without a figure */
};

BoardFalseBusy readBoardFalseBusy(const char* chip);

/** Whether one look at `sf` reads a clear channel busy: a draw that is a
 *  function of the run's seed (SIM_MESH_SEED), the station (SIM_MESH_NODE_ID),
 *  the slot and the look's start alone, so a run repeats, and the firmware's
 *  own randomness (the shim's) is not touched. */
bool falseBusyLook(const BoardFalseBusy& b, int sf, int slot, int64_t startUs);

#endif
