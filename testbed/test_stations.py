"""A station's pty, read on the pty thread, against a stand-in process."""

import asyncio
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rpc  # noqa: E402
import stations  # noqa: E402
from sim_mesh.driver import Driver  # noqa: E402
from sim_mesh.reticulum.driver import EVENT, ID_EVENT, ReticulumDriver  # noqa: E402


class StubKind(Driver):
    """A driver whose firmware is a shell script, and which hears every
    console line."""

    def __init__(self, elf):
        super().__init__({"firmware": "stub", "exec": elf, "env": {}})
        self.heard = []

    def configured(self, station):
        return False

    def console_line(self, station, line):
        self.heard.append(line)


def script(tmp_path, body):
    path = tmp_path / "station.sh"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def octal(data):
    return "".join("\\%03o" % b for b in data)


def test_text_frames_and_the_marker_reach_their_owners_in_order(tmp_path):
    reply = rpc.frame(0x41, b"s.net.hostname = alpha\n")
    lines = [b"booting\n", b"I [serial] framed rpc v1\n", b"after the marker\n"]
    elf = script(tmp_path, "printf '%s'\nprintf '%s'\nprintf '%s'\nsleep 0.3\n" % (
        octal(lines[0] + lines[1][:9]), octal(lines[1][9:] + reply), octal(lines[2])))

    driver = StubKind(elf)

    async def main():
        seen = []
        station = stations.Station("alpha", 1, str(tmp_path / "alpha"), driver,
                                   "", on_output=lambda st, text: seen.append(text))
        station.watchers = 1
        station.run()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if station.rpc is not None and station.rpc.late:
                break
        client = station.rpc
        assert client.marker.is_set() and client.available
        assert client.late == {0x41: "s.net.hostname = alpha\n"}
        await station.stop()
        await asyncio.sleep(0.1)            # the pty thread closes the log
        return seen

    seen = asyncio.run(main())
    log = (tmp_path / "alpha" / "log").read_bytes()
    assert log.endswith(b"".join(lines))
    assert rpc.MAGIC not in log
    assert b"".join(seen) == b"".join(lines)
    # The driver hears each line whole, a reply frame taken out of it.
    assert driver.heard == ["booting", "I [serial] framed rpc v1", "after the marker"]


def test_no_console_bytes_cross_while_nobody_watches(tmp_path):
    elf = script(tmp_path, "printf 'quiet\\n'\nsleep 0.2\n")

    async def main():
        seen = []
        station = stations.Station("bravo", 2, str(tmp_path / "bravo"), StubKind(elf),
                                   "", on_output=lambda st, text: seen.append(text))
        station.run()
        await asyncio.sleep(0.4)
        await station.stop()
        await asyncio.sleep(0.1)
        return seen

    assert asyncio.run(main()) == []
    assert (tmp_path / "bravo" / "log").read_bytes().endswith(b"quiet\n")


def test_what_a_station_wrote_before_it_exited_is_all_in_the_log(tmp_path):
    body = b"".join(b"line %04d\n" % i for i in range(2000))
    elf = script(tmp_path, "printf '%s'\n" % octal(body))

    async def main():
        station = stations.Station("charlie", 3, str(tmp_path / "charlie"), StubKind(elf), "")
        station.run()
        for _ in range(200):
            await asyncio.sleep(0.02)
            if station.status == stations.RESTARTING or station.drain is None:
                break
        await station.stop()
        await asyncio.sleep(0.1)

    asyncio.run(main())
    log = (tmp_path / "charlie" / "log").read_bytes()
    assert body in log


class TimedKind(StubKind):
    """A driver whose firmware prints its node time first on a line, and
    which reports every line it hears."""

    def line_time(self, line):
        head = line.split(" ", 1)[0]
        return int(head) if head.isdigit() else None

    def console_line(self, station, line):
        super().console_line(station, line)
        station.report("seen", line=line)


def test_what_a_line_reports_is_put_at_the_time_its_station_wrote_it(tmp_path):
    events = []

    def station(clock):
        return stations.Station("alpha", 1, str(tmp_path / "alpha"), TimedKind("unused"), "",
                                clock=clock, on_event=lambda st, event, fields: events.append((event, fields)))

    virtual = station(object())
    virtual.console_text("1500 one\nno time here\n")
    virtual.report("by hand")
    virtual.report("at a time", at=7)
    station(None).console_text("1500 in real time\n")
    assert events == [("seen", {"line": "1500 one", "t": 1500}),
                      ("seen", {"line": "no time here"}),
                      ("by hand", {}),
                      ("at a time", {"t": 7}),
                      ("seen", {"line": "1500 in real time"})]


def test_a_status_held_until_its_id_is_coupled_keeps_the_time_of_its_line(tmp_path):
    events = []
    driver = ReticulumDriver({"firmware": "stub", "exec": "unused", "env": {}})
    station = stations.Station("alpha", 1, str(tmp_path / "alpha"), driver, "", clock=object(),
                               on_event=lambda st, event, fields: events.append((event, fields)))
    station.line_t = 1500
    driver.lxmf_native(station, "f1", "sent")
    driver.lxmf_message_id(station, "f1", "m1")
    station.line_t = None
    driver.lxmf_couple(station, "s1", "f1")
    assert events == [(EVENT, {"mid": "s1", "status": "sent", "t": 1500}),
                      (ID_EVENT, {"mid": "s1", "message_id": "m1", "t": 1500})]
