/**
 * ether_link — the station's one link to the ether.
 *
 * One UDP socket and one reader. Outbound: everything a chip model does that
 * anyone else could observe — the mode and carrier it sits on, and each frame
 * it transmits. Inbound: the frames that reach its antenna, applied to the
 * addressed slot's model.
 *
 * The messages are JSON, payloads base64, times in microseconds on the
 * sender's own clock and meaningful only against each other. A station sends
 * one per datagram; the ether sends one, or, to a station that says `lines`
 * in its hello, every message of an instant in one datagram, a line each,
 * which the station applies as one (ether_link.cpp, handleDatagram). A
 * station ignores a message it does not understand.
 * sim-mesh/ether/README.md is the wire.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

/** The most side detectors a radio has: an LR2021's three. */
constexpr int kMaxSideDetectors = 3;

/** What a receiver needs to know about this radio to decide whether a frame
 *  reaches it. */
struct EtherState {
    int         slot;
    const char* mode;        /* SLEEP | STDBY_RC | STDBY_XOSC | FS | TX | RX | CAD */
    int64_t     readyAt;     /* when the mode transition completes, µs */
    uint32_t    freqHz;
    uint32_t    bwHz;
    int         sf;
    int         cr;          /* the 4/N denominator, 5..8 */
    int         syncWord;
    bool        hdrImplicit;
    bool        crc;
    int         preamble;
    bool        iqInverted;  /* SetPacketParams' InvertIQ: the chirps run down */
    /* Side detectors, listening beside the main one on its carrier and
     * bandwidth (an LR2021's SetLoraSideDetConfig and SetLoraSideDetSyncword):
     * each its own spreading factor, sync word and IQ. An SX1262 has none.
     * A multi-SF receiver (SIM_MESH_MULTI_SF, model.cpp's multiSfSet(), or
     * model_lr2021.cpp's own side detectors) states the faster SFs it also
     * hears here, one per side detector. */
    int         nSide;
    struct { int sf; int syncWord; bool iqInverted; } side[kMaxSideDetectors];
};

/** A frame leaving this radio. */
struct EtherTxFrame {
    EtherState     state;
    int            id;
    int64_t        t0, tPre, tHdr, tEnd;
    int            powerDbm;
    const uint8_t* payload;
    size_t         len;
};

void etherPublishState(const EtherState& s);
void etherPublishTx(const EtherTxFrame& f);

/** Who has the floor on the station's host door: the station, once it has
 *  read what a host sent (`station`), or the host again once it has been
 *  answered. The ether keeps T still while a testbed tool has the floor. */
void etherPublishFloor(bool station);
/* Floor runs applied so far (`simradio_floor_runs`). */
uint64_t etherFloorRuns();
