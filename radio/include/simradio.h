/**
 * simradio — a virtual radio chip on a virtual SPI bus, and the station's link
 * to the ether. This is the SX1262's: the shared library libsimradio-sx1262.so.
 *
 * A firmware is compiled against this header and linked with
 * `-lsimradio-sx1262`, and its zip does not carry the library: sim-mesh
 * provides it when it starts the station (on LD_LIBRARY_PATH, and its path in
 * SIM_MESH_RADIO_LIB), so a firmware keeps working when the chip model or the
 * ether's protocol changes. Its driver hands each SPI frame to
 * `simradio_transfer` exactly as it would put it on a bus, and reads the reply
 * the datasheet describes; the model times every frame on the air, raises the
 * IRQ bits a real chip raises, and tells the ether what it is doing. The ether
 * decides who hears what and hands the frames that reach this antenna back.
 *
 * Thread-safe throughout. Nothing here blocks the caller beyond the model's
 * own short critical section.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
typedef struct simradio simradio_t;

/* ---- The host's services ----
 *
 * What the library needs of the process it runs in: a clock, timers, a lock,
 * a UDP socket and a thread to read it, and a log. A plain POSIX process needs
 * nothing: the library's own services are threads, and are used until a host
 * hands it others. A host with its own scheduler (FreeRTOS on the Linux
 * target) hands its own with `simradio_set_services`, before any other call.
 *
 * The rules every host's services keep, because the model relies on them:
 *
 * - the lock is recursive: a timer callback that took it can call into code
 *   that takes it again;
 * - a timer callback runs with the lock NOT held; the model takes it itself;
 * - `timer_start_once` on a timer that is already running restarts it from
 *   now, and `timer_stop` on one that is not running does nothing;
 * - `now_us` is monotonic, zero near the process's start;
 * - `udp_open` returns a connected, non-blocking socket bound to `bind_addr`,
 *   or -1; `spawn_reader` calls `on_datagram` with each datagram it reads. */
enum { SIMRADIO_LOG_ERROR = 1, SIMRADIO_LOG_WARN = 2, SIMRADIO_LOG_INFO = 3 };

struct simradio_services {
    int64_t (*now_us)(void);
    void*   (*timer_create)(void (*cb)(void*), void* arg, const char* name);
    void    (*timer_start_once)(void* timer, int64_t delay_us);
    void    (*timer_stop)(void* timer);
    void    (*lock)(void);
    void    (*unlock)(void);
    int     (*udp_open)(const char* bind_addr, const char* dest_host_port);
    int     (*spawn_reader)(int fd, void (*on_datagram)(const char*, size_t));
    void    (*log)(int level, const char* fmt, ...);
};

void simradio_set_services(const struct simradio_services* services);

enum { SIMRADIO_PIN_DIO1 = 1, SIMRADIO_PIN_BUSY = 2 };

/* The station's one link to the ether. Idempotent. An empty `ether_addr`
 * means "no ether": models exist, transmissions go nowhere. In a virtual-time
 * run it returns once the ether's welcome has set T (or after a minute
 * without one), so the host starts at the instant it joins. */
int simradio_station_open(int sid, const char* bind_addr, const char* ether_addr);

/* One chip per radio slot. `on_pin(ctx, pin, level)` runs on whatever
 * thread moved the line: the bus caller, the timer thread, or the reader. */
simradio_t* simradio_open(int slot, void (*on_pin)(void*, int, int), void* ctx);

/* One complete SPI frame, NSS low to NSS high. out[0] is the opcode; `in`
 * is the status byte until the data starts, then the data. Thread-safe. */
void simradio_transfer(simradio_t*, const uint8_t* out, size_t len, uint8_t* in);

/* The RST line's rising edge. */
void simradio_reset(simradio_t*);

/* What the model drives on a line right now. */
int simradio_pin(simradio_t*, int pin);

/* Microseconds on the model's clock, for a host that wants to log against it.
 * In a virtual-time run this is conductor time, T. */
int64_t simradio_now_us(void);

/* Virtual time: how long, in µs of node time from now, nothing a driver can
 * read of this chip will change — while it transmits, until TX_DONE lands,
 * since a transmitting chip takes in nothing from the air — or -1 when it may
 * change at any moment, and always in real time. A driver polling the chip
 * may sleep that long in one go and see what its polls would have seen. */
int64_t simradio_quiet_for_us(simradio_t*);

void simradio_close(simradio_t*);

/* ---- The station's clock ----
 *
 * A run keeps real or virtual time, for every station alike; the ether says
 * which in its welcome, and SIM_MESH_TIME=virtual in the environment says it
 * before the station can reach the ether. In virtual time the ether owns
 * conductor time T and moves it only when every station is idle. The host
 * reads node time, f(T) — its own crystal — and tells the library when it
 * next needs to run; the library tells the ether. */

/* 1 in a virtual-time run. */
int simradio_virtual(void);

/* Node time, µs: f(T) in a virtual run, the host's monotonic clock in a real one. */
int64_t simradio_node_us(void);

/* 1 once the ether's welcome has set T: before it, node time is 0. */
int simradio_joined(void);

/* Node time at the welcome: what a host's own clock counts from. */
int64_t simradio_node_at_join(void);

/* The wall-clock µs that node time 0 stands for. */
int64_t simradio_epoch_us(void);

/* f⁻¹: the conductor time at which node time reaches `node_us`. */
int64_t simradio_node_to_conductor(int64_t node_us);

/* A wake the host owns: `due(arg)` runs when a grant reaches the node time it
 * is set to, on the thread that received the grant, with no lock held. */
int  simradio_wake_create(void (*due)(void* arg), void* arg);
void simradio_wake_at(int wake, int64_t node_us);      /* INT64_MAX clears it */

/* The host has nothing to do before its wakes. The first call after each
 * grant tells the ether; later ones only when the next wake has moved closer. */
void simradio_idle(void);

/* The station's host door changed hands (virtual time; nothing in real time):
 * `station` nonzero once the station has read what a host wrote to it, zero
 * once it has answered. A testbed tool talks to a station on the wall clock;
 * the ether keeps T still while the tool has the floor, and lets it run while
 * the station has, so the station reads each line and answers it at a T the
 * run decides. Said before the bytes are handed on, and after the answer is
 * written. The station owes an idle after it, as after anything it says.
 * With `station` it returns once the ether has brought the station to the
 * run's T (a `run` marked `floor`), which an idle station is behind. */
void simradio_host_floor(int station);

/* How many runs marked `floor` have been applied: the one a station's floor
 * asks for is counted in the datagram that brings it, before any thread
 * that grant wakes runs. A host thread that hands the station bytes tagged
 * with the count before taking the floor lets the station take them exactly
 * at the first look that sees the count past the tag, at a T the run decides,
 * whichever thread ran first on the host. */
uint64_t simradio_floor_runs(void);

/* A host whose own clock is a function of node time — a kernel tick counted
 * from it — learns of every move: `moved()` runs each time a grant moves T, on
 * the thread that received it, with no lock held, before any wake or timer
 * due at the new T. */
void simradio_on_advance(void (*moved)(void));
#ifdef __cplusplus
}
#endif
