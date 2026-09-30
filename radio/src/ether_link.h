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
    /* A multi-SF receiver's spreading factors (SIM_MESH_MULTI_SF): `sf` and
     * the faster ones it also hears, ascending; sfCount 0 on a single-SF
     * receiver, which is every chip unless the station asks. */
    int         sfs[4];
    int         sfCount;
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
