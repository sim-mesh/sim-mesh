"""The ether's record, `record.tsv`: one line per message, in the order the
ether took or sent them.

    <stamp> TAB <in|out> TAB <station id> TAB <the message as JSON>

The stamp is T in seconds in a virtual-time run and the wall clock's ISO
time in a real one. A line starting `#` is a comment (the ether writes one
each time it opens the file). `in` is what a station told the ether (`hello`,
`state`, `tx`), `out` what the ether told a station (`welcome`, `rx_begin`,
`rx_end`), and `note` the ether's own account, sent to no one (`rx_miss`: a
station that could hear a frame and never started receiving it, and why;
written once the frame's air is over, so its stamp can be later than the
frame's `t_end`, which it carries). The record knows a frame's carrier, air time and bytes, and
nothing about what the bytes mean.
"""

import json
import os
import re

RECORD_FILE = "record.tsv"


def path_in(run_dir):
    return os.path.join(run_dir, RECORD_FILE)


def parse_time(stamp):
    """Seconds out of a record stamp: T itself in a virtual-time run, the time
    of day in a real one (wrapping at midnight is the caller's business)."""
    if ":" not in stamp:
        return float(stamp)
    clock = stamp.split("T")[-1]
    hour, minute, second = clock.split("+")[0].split("Z")[0].split(":")
    return int(hour) * 3600 + int(minute) * 60 + float(second)


def lines(path, types=None):
    """(stamp, direction, station id, message) for every well-formed line.

    With `types`, only the lines whose message is of one of them, and only
    those are parsed: a tool after the 7,321 `tx` of a city run's 1.3 million
    lines passes the rest over unread, which is most of what reading the
    record cost it. A line is passed over only when its text cannot hold a
    `type` of those at all; one that can is parsed and its message's own
    `type` decides."""
    wanted = None
    if types is not None:
        wanted = re.compile(r'"type"\s*:\s*"(?:%s)"' % "|".join(re.escape(t) for t in types))
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            if wanted is not None and not wanted.search(line):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                continue
            stamp, direction, sid, blob = fields
            try:
                item = stamp, direction, int(sid), json.loads(blob)
            except ValueError:
                continue
            if wanted is not None and not (isinstance(item[3], dict)
                                           and item[3].get("type") in types):
                continue
            yield item


def first_stamp(path):
    """The stamp of the record's first well-formed line, the one `lines`
    gives first, which says whether the record is on T or the wall clock and
    where its wall clock starts; None when it has none."""
    for stamp, _direction, _sid, _msg in lines(path):
        return stamp
    return None


def epoch_of(path):
    """The wall-clock seconds T = 0 stands for in a virtual-time run, from the
    ether's first `welcome`; None when the record has none."""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if '"welcome"' in line:
                return json.loads(line.split("\t", 3)[3])["epoch"] / 1e6
    return None
