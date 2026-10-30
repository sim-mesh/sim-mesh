#!/usr/bin/env python3
"""One firmware process, its pty, its log and its supervisor.

A station is a whole firmware built for Linux and run as an ordinary process,
keeping the station contract; its firmware's driver (sim_mesh.driver) says
how to start it and how to talk to it. It gets:

- a **directory** in the run, `runs/<run>/nodes/<name>/`, its cwd, with its
  state under `state/`;
- a **pty**, because its stdin and stdout are its serial console — the
  supervisor holds the master end and reads it on the pty thread (`Ptys`),
  which takes framed-RPC replies out of what the station writes (rpc.py) and
  hands them to the station's client, appends everything else to `log`, and
  passes the same bytes to whoever is watching the console; or, for a firmware
  whose console need not be a terminal (`console_tty`), a pipe each way,
  read the same way, which holds none of the host's ptys;
- a **loopback address** its id fixes in the testbed's network (`bind_addr`),
  where its sockets bind;
- a **supervisor** that starts it again when it exits, because a restart on
  this target is a process exit.

Status is what the map draws: `stopped` before anything is started and after
it is told to stop, `starting` from the fork until its driver says it is up, `setup`
while its setup lines are going in, `up` once it is answering, `restarting`
in the gap after an unasked-for exit. Nothing here decides when `setup`
happens — the caller does that, through `on_status`.
"""

import asyncio
import functools
import ipaddress
import os
import pty
import select
import sys
import threading
import tty

import drivers as drivers_module
import rpc as rpc_module

RESTART_DELAY = 0.5         # seconds before a station that exited comes back

# Stations take their addresses from one network, `simd --net`, filled one /24
# at a time with hosts 5 to 254: node 1 is the first network's .5, node 250
# its .254, node 251 the next network's .5. A /22 is four of those, 1000
# stations. Every station binds its own address, so two testbeds on one host
# take two networks; two stations with one address are one port taken twice.
NET = "127.0.0.0/22"
HOST_FIRST = 5
HOST_LAST = 254
HOSTS_PER_NET = HOST_LAST - HOST_FIRST + 1

STOPPED, STARTING, SETUP, UP, RESTARTING = (
    "stopped", "starting", "setup", "up", "restarting")


def log(msg):
    sys.stderr.write("sim: %s\n" % msg)
    sys.stderr.flush()


def set_net(cidr):
    """Take the station network from `simd --net`; a /24 or wider."""
    global NET
    net = ipaddress.ip_network(cidr, strict=False)
    if net.version != 4 or net.prefixlen > 24:
        raise ValueError("the station network must be an IPv4 /24 or wider, not %s" % cidr)
    NET = str(net)


def max_node_id():
    """How many stations the network holds: 250 per /24 in it."""
    net = ipaddress.ip_network(NET)
    return HOSTS_PER_NET * (1 << (24 - net.prefixlen))


_cpus = None
_cpu_next = 0


def next_cpu():
    """The CPU for the next station, taken in turn from those simd may use."""
    global _cpus, _cpu_next
    if _cpus is None:
        _cpus = sorted(os.sched_getaffinity(0))
    cpu = _cpus[_cpu_next % len(_cpus)]
    _cpu_next += 1
    return cpu


def bind_addr(node_id):
    """The station's own loopback address."""
    net = ipaddress.ip_network(NET)
    index = node_id - 1
    if not 0 <= index < max_node_id():
        raise ValueError("node id %d is outside the %d the network %s holds"
                         % (node_id, max_node_id(), NET))
    subnet, host = divmod(index, HOSTS_PER_NET)
    return str(net.network_address + subnet * 256 + HOST_FIRST + host)


class Ptys:
    """The stations' console ptys, read on a thread of their own.

    Everything a station prints is read, split into text and framed-RPC
    replies (rpc.FrameDemux) and appended to its log here, on an event loop of
    this thread's, so that however much a nodeset prints, none of it is work for
    the loop that runs the medium. That loop is handed only what it acts on,
    with call_soon_threadsafe, in the order it was read: a reply frame, the
    capability marker, console bytes while a console window is open, the end
    of the stream. The log file is this thread's alone once a station has
    started, so nothing another thread writes can land between two reads.
    """

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="ptys",
                                       daemon=True)
        self.thread.start()

    def call(self, fn, *args):
        """Run fn(*args) on the pty thread, after whatever is queued there."""
        self.loop.call_soon_threadsafe(fn, *args)


def catch_up(drains, main, done):
    """On the pty thread: every one of `drains` reads all its station has
    written, then `done` runs on `main`, behind everything they handed it."""
    for drain in drains:
        drain.catch_up()
    main.call_soon_threadsafe(done)


def printed(drains):
    """Of `drains`, the ones with output the testbed may not have taken in:
    bytes on the pty, or bytes the pty thread has read since its last
    `catch_up` and handed to the main loop. On the main thread, before T moves.

    Most barriers find none, and then cost no hand-off to the pty thread and
    back. The ptys are polled first — a poll of a pty master that finds
    nothing waits for the kernel to carry across what the station wrote, as
    a read does — and the pty thread's marks after, so bytes it took off a
    pty in between are still counted: `taking` is up before it reads, and
    `taken` moves once what it read has been handed on. An ether with its
    conductor in the core asks the same of the consoles it watches itself
    (ether_core's State::printed), and asks this only when one shows some."""
    live = {d.master: d for d in drains if d.marks.reading}
    if not live:
        return []
    poller = select.poll()
    for fd in live:
        poller.register(fd, select.POLLIN)
    hit = {fd for fd, _ in poller.poll(0)}
    return [d for fd, d in live.items()
            if fd in hit or d.marks.taking or d.marks.taken != d.marks.caught]


class Marks:
    """How far a Drain has got, for `printed`: written on the pty thread,
    read on the main one. An ether with a core gives its own instead
    (`console_marks`), which it reads without asking this process's loop."""

    def __init__(self, fd):
        self.master = fd
        self.reading = False
        self.taking = False     # inside a read and the handing on of what it got
        self.taken = 0          # reads handed on, or that found the end
        self.caught = 0         # `taken` when the last catch_up had read everything


_ptys = None


def ptys():
    """The process's pty thread, started on first use."""
    global _ptys
    if _ptys is None:
        _ptys = Ptys()
    return _ptys


class Drain:
    """One start of one station, read on the pty thread.

    `main` is the loop the station belongs to; `rpc` and `station` are only
    ever touched there.
    """

    def __init__(self, station, master, rpc, main, marks=None):
        self.station = station
        self.master = master
        self.log_file = station.log_file
        self.rpc = rpc
        self.main = main
        self.demux = rpc_module.FrameDemux()
        self.marker = rpc_module.MarkerWatch()
        self.resync = None
        # Written on the pty thread, read on the main one (`printed`).
        self.marks = marks if marks is not None else Marks(master)

    # ---- on the pty thread -------------------------------------------------

    def attach(self):
        asyncio.get_running_loop().add_reader(self.master, self.readable)
        self.marks.reading = True

    def readable(self):
        self.read_once()

    def catch_up(self):
        """Read until the pty has nothing more.

        A read that finds a pty master empty has first waited for the kernel
        to carry across whatever the station had written, so when this returns
        everything the station wrote before it was called has been read and
        handed on."""
        while self.marks.reading and self.read_once():
            pass
        self.marks.caught = self.marks.taken

    def read_once(self):
        """One read of the pty and what it delivers; False when it had
        nothing."""
        marks = self.marks
        marks.taking = True
        try:
            try:
                data = os.read(self.master, 65536)
            except BlockingIOError:
                return False
            except OSError:
                data = b""          # the station let go of the far end
            if not data:
                self.stop_reading()
                self.main.call_soon_threadsafe(self.station.pty_closed, self)
                marks.taken += 1
                return False
            loop = asyncio.get_running_loop()
            text, frames = self.demux.feed(data, loop.time())
            self.deliver(text, frames)
            if self.demux.pending and self.resync is None:
                self.resync = loop.call_later(rpc_module.RESYNC_S, self.resync_due)
            marks.taken += 1
            return True
        finally:
            marks.taking = False

    def resync_due(self):
        self.resync = None
        if not self.marks.reading:
            return
        loop = asyncio.get_running_loop()
        text, frames = self.demux.expire(loop.time())
        self.deliver(text, frames)
        if self.demux.pending:
            self.resync = loop.call_later(rpc_module.RESYNC_S, self.resync_due)

    def deliver(self, text, frames):
        if text:
            self.log_file.write(text)
            if self.marker.feed(text):
                self.main.call_soon_threadsafe(self.rpc.on_marker)
            if self.station.watchers:
                self.main.call_soon_threadsafe(self.station.console_out, text)
            if self.station.hears_lines:
                self.main.call_soon_threadsafe(self.station.console_text, text)
        for frame_id, payload in frames:
            self.main.call_soon_threadsafe(self.rpc.on_frame, frame_id, payload)

    def stop_reading(self):
        if self.resync is not None:
            self.resync.cancel()
            self.resync = None
        if self.marks.reading:
            self.marks.reading = False
            asyncio.get_running_loop().remove_reader(self.master)

    def close(self):
        """What the station wrote before it went is still read, then the pty
        is let go."""
        while self.marks.reading:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                data = b""
            if not data:
                break
            text, frames = self.demux.feed(data, asyncio.get_running_loop().time())
            self.deliver(text, frames)
        text, frames = self.demux.expire(float("inf"))
        self.deliver(text, frames)
        self.stop_reading()
        try:
            os.close(self.master)
        except OSError:
            pass


class Station:
    """One firmware process, its pty and its log; to its driver, the
    station surface `sim_mesh.driver` names."""

    def __init__(self, name, node_id, directory, driver, ether_addr,
                 on_status=None, on_output=None, clock=None, on_event=None):
        self.name = name
        self.node_id = node_id
        self.dir = directory
        self.driver = driver            # its firmware's driver: how to start it and talk to it
        self.ether_addr = ether_addr
        self.on_event = on_event        # (station, event, fields): what its driver reports
        self.board = None
        self.line_tail = ""             # console text after the last newline
        self.line_t = None              # the node time the line being handled was written at (line_time)
        # Whether the driver wants every console line: only then does the pty
        # thread hand console text to this loop when no window is open.
        self.hears_lines = (type(driver).console_line
                            is not drivers_module.Driver.console_line)
        # The ether, in a virtual-time run: it waits for a station it is told
        # is starting, and stops waiting for one that has gone.
        self.clock = clock
        self.on_status = on_status      # (station, status)
        self.on_output = on_output      # (station, bytes)
        self.master = None
        self.proc = None
        self.log_file = None
        self.status = STOPPED
        self.role = None                # what it does for the mesh, as its driver last read it
        # Whether this station had been through a first boot when it was last
        # started. Sampled at the fork, because the station writes `state/boot`
        # moments later and the answer the setup step needs is the one from
        # before it ran.
        self.was_configured = False
        self.stopping = False
        self.supervisor = None
        self.watchers = 0               # console windows open on it; read on the pty thread
        self.drain = None               # this start's reader, on the pty thread
        self.rpc = None                 # framed RPC on the pty, one per start
        self.outbox = bytearray()       # console bytes the pty has not taken yet
        self.typed = 0                  # console bytes typed at it this start
        self.syncing = False            # waiting for the ether before typing them
        self.starts = 0                 # how many times the process has been started
        self.cpu = None                 # the one CPU all its threads run on, kept across restarts

    # ---- identity --------------------------------------------------------

    @property
    def addr(self):
        return bind_addr(self.node_id)

    @property
    def log_path(self):
        return os.path.join(self.dir, "log")

    @property
    def state_dir(self):
        return os.path.join(self.dir, "state")

    @property
    def virtual(self):
        return self.clock is not None

    async def sleep(self, seconds):
        """Wait on the run's clock: T in a virtual-time run."""
        if self.clock is not None:
            await self.clock.sleep(seconds)
        else:
            await asyncio.sleep(seconds)

    def report(self, event, at=None, **fields):
        """An event from the station's driver, for the run's record. In a
        virtual-time run it is put at `at`, the node time it happened at in
        microseconds when the driver knows it, else, reported from a console
        line, at the T the station wrote the line when its driver can read it
        (`line_time`): the T the pty thread read it at depends on when the
        host ran that thread."""
        if self.on_event is not None:
            t = self.line_t if at is None else at
            if t is not None and self.clock is not None:
                fields["t"] = t
            self.on_event(self, str(event), fields)

    def console_text(self, text):
        """Console text the pty thread read, as whole lines to the driver."""
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        lines = (self.line_tail + text).split("\n")
        self.line_tail = lines.pop()
        for line in lines:
            line = line.rstrip("\r")
            try:
                self.line_t = self.driver.line_time(line)
                self.driver.console_line(self, line)
            except Exception as err:     # noqa: BLE001 - a firmware's callback, reported
                log("station %s: its driver's console_line failed: %s: %s"
                    % (self.name, type(err).__name__, err))
            finally:
                self.line_t = None

    @property
    def configured(self):
        """True when this station has been through its first boot already.

        What marks that is the driver's business — a file the firmware writes
        once it has run — and a directory without it is a station that has
        never been set up, which is what decides whether the setup lines go in.
        """
        return self.driver.configured(self)

    def set_status(self, status):
        if status == self.status:
            return
        self.status = status
        if self.on_status is not None:
            self.on_status(self, status)

    # ---- the process -----------------------------------------------------

    def env(self):
        env = dict(os.environ)
        env.update(drivers_module.env(self, self.driver.firmware))
        env.update(self.driver.env(self))
        return env

    def expect(self):
        """Tell the ether every process of this station's that joins the run
        is starting (Driver.sids)."""
        for sid in self.driver.sids(self):
            self.clock.expect(sid)

    def leave(self):
        for sid in self.driver.sids(self):
            self.clock.leave(sid)

    async def start(self):
        self.was_configured = self.configured
        os.makedirs(self.state_dir, exist_ok=True)
        if self.log_file is None:
            self.log_file = open(self.log_path, "ab", buffering=0)
        ptys().call(self.log_file.write, b"\n--- station %s starting ---\n"
                    % self.name.encode("utf-8"))

        if self.driver.console_tty:
            master, slave = pty.openpty()
            tty.setraw(slave)       # a serial line has no echo and no translation
            reader, child_in, child_out = master, slave, slave
        else:
            reader, child_out = os.pipe()
            child_in, master = os.pipe()
        self.master = master        # what is typed at it goes here
        if self.rpc is not None:
            self.rpc.close()
        self.rpc = rpc_module.RpcClient(
            self.write, self.clock.sleep if self.clock is not None else None)
        self.outbox.clear()
        self.typed = 0
        self.syncing = False
        self.line_tail = ""
        self.starts += 1
        self.set_status(STARTING)
        if self.clock is not None:
            self.expect()
        if self.cpu is None:
            self.cpu = next_cpu()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.driver.argv(self), cwd=self.dir, env=self.env(),
                stdin=child_in, stdout=child_out, stderr=child_out,
                preexec_fn=functools.partial(os.sched_setaffinity, 0, (self.cpu,)))
        except OSError:
            if self.clock is not None:
                self.leave()
            raise
        finally:
            for fd in {child_in, child_out}:
                os.close(fd)
        os.set_blocking(master, False)
        os.set_blocking(reader, False)
        marks = self.clock.console_marks(reader) if self.clock is not None else None
        self.drain = Drain(self, reader, self.rpc, asyncio.get_running_loop(), marks)
        if self.clock is not None and self.driver.console_acted_on:
            self.clock.watch(self.node_id, self.drain.marks)
        ptys().call(self.drain.attach)
        log("station %s (%d, %s) up as pid %d on %s" % (
            self.name, self.node_id, self.driver.firmware.get("firmware"), self.proc.pid,
            self.addr))

    def console_out(self, text):
        """Console bytes the pty thread read, for whoever has the window open."""
        if self.on_output is not None:
            self.on_output(self, text)

    def pty_closed(self, drain):
        """The pty thread read the end of this start's stream."""
        if drain is self.drain:
            self.detach_reader()

    def detach_reader(self):
        if self.rpc is not None:
            self.rpc.close()
        if self.master is None:
            return
        try:
            asyncio.get_running_loop().remove_writer(self.master)
        except (OSError, ValueError):
            pass
        # The pty thread reads what is left, then closes the pty: from here on
        # this loop neither reads nor writes it. A console of pipes has an
        # input end of its own, closed here.
        if self.drain is not None:
            if self.clock is not None:
                self.clock.unwatch(self.node_id, self.drain.marks)
            if self.drain.master != self.master:
                os.close(self.master)
            ptys().call(self.drain.close)
        else:
            os.close(self.master)
        self.drain = None
        self.master = None
        self.outbox.clear()

    def write(self, data):
        """Type into the station's console.

        What the pty will not take now waits for it, in order: a frame cut
        short would read as a corrupt one.
        """
        if self.master is None or not data:
            return
        self.outbox += data
        if self.clock is not None:
            # A virtual run: the ether holds T from now until the station has
            # read these bytes, and they go out once the station has the T
            # the run has, so a line typed at an instant is read at it.
            self.typed += len(data)
            self.clock.typed(self.driver.console_sid(self), self.typed)
            if not self.syncing:
                self.syncing = True
                start = self.starts
                self.clock.sync(self.driver.console_sid(self), lambda: self.synced(start))
            return
        self.writable()

    def synced(self, start):
        if start != self.starts:
            return          # a start that has ended
        self.syncing = False
        self.writable()

    def writable(self):
        if self.master is None:
            return
        try:
            sent = os.write(self.master, bytes(self.outbox))
        except BlockingIOError:
            sent = 0
        except OSError:
            sent = len(self.outbox)
        del self.outbox[:sent]
        loop = asyncio.get_running_loop()
        if self.outbox:
            loop.add_writer(self.master, self.writable)
        else:
            loop.remove_writer(self.master)

    def resize(self, cols, rows):
        """Tell the station's console how wide its terminal is."""
        if self.master is None:
            return
        import fcntl
        import struct
        import termios
        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    # ---- the supervisor --------------------------------------------------

    async def supervise(self, after_start=None):
        """Keep the station running until it is told to stop.

        `after_start` is awaited once per start, with the station, and is
        where the caller waits for the CLI and sends the setup lines. It is
        cancelled when the process exits under it.
        """
        while not self.stopping:
            await self.start()
            watcher = None
            if after_start is not None:
                watcher = asyncio.ensure_future(after_start(self))
            try:
                code = await self.proc.wait()
            finally:
                # Stopped or exited, this start is over, and so is its setup:
                # a watcher left running would set up whatever comes next.
                if watcher is not None:
                    watcher.cancel()
                if self.clock is not None:
                    self.leave()
            self.detach_reader()
            if self.stopping:
                return
            self.set_status(RESTARTING)
            log("station %s exited (%s), restarting" % (self.name, code))
            if self.clock is not None:
                await self.clock.sleep(RESTART_DELAY)
            else:
                await asyncio.sleep(RESTART_DELAY)

    def run(self, after_start=None):
        """Start the supervisor as a task and keep hold of it."""
        self.stopping = False
        if self.clock is not None:
            # Now, not when the supervisor gets to it: T must not move on
            # without a station that is about to exist.
            self.expect()
        self.supervisor = asyncio.ensure_future(self.supervise(after_start))
        return self.supervisor

    async def stop(self):
        """Stop the process and the supervisor, and wait for both to go."""
        self.stopping = True
        if self.clock is not None:
            self.leave()
        if self.supervisor is not None:
            self.supervisor.cancel()
            try:
                await self.supervisor
            except asyncio.CancelledError:
                pass
            self.supervisor = None
        self.detach_reader()
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                log("station %s would not stop; killing it" % self.name)
                self.proc.kill()
                await self.proc.wait()
        self.proc = None
        if self.log_file is not None:
            ptys().call(self.log_file.close)
            self.log_file = None
        self.set_status(STOPPED)

    async def restart(self):
        """Stop the process and let the supervisor bring it back."""
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
