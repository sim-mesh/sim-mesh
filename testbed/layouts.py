#!/usr/bin/env python3
"""Layouts drawn rather than surveyed: a nodeset of a given shape, seeded.

    layouts.py hex NAME [--spacing-m M] [--radius-m M] [--jitter-m M] [COMMON]
    layouts.py disc NAME --n N [--radius-m M] [COMMON]
    layouts.py clusters NAME [--clusters K] [--per N] [--spread-m M]
                             [--apart-m M] [--bridge B] [COMMON]

    COMMON: [--seed S] [--height-m M] [--antenna TYPE] [--transport-share F]
            [--tag T ...] [--center LAT,LON] [--prefix P] [--force]

The layouts a sweep has run are few and one city's; a finding that holds
only on them says something about them. These are shapes a finding can be
checked across, at any density, by anyone:

- hex: every node `spacing` from each of its six neighbours, within a disc
  of `radius`, each then moved up to `jitter` in a random direction (the
  honeycomb of our mesh simulator's scenario generator);
- disc: `n` nodes at random in a disc of `radius`, evenly by area;
- clusters: `clusters` crowds of `per` nodes each, at random within `spread`
  of their centres, which stand `apart` from each other on a line, with
  `bridge` nodes spaced evenly between every two neighbouring crowds; two
  crowds and one bridge is the dumbbell a lone relay is tested on.

Positions are metres around `center`, by default (0, 0), synthetic ground's
own origin, where a degree is 111,320 m on both axes
(`geodata.synthetic_latlon`), so the metres are exact there. On a pack,
give the centre in degrees: a degree of longitude is then scaled by its
latitude's cosine, which is close enough for placing nodes.

Nodes are named `<prefix>001` onward, north to south and west to east, all
at `height` with `antenna` (by default the store's), a random
`transport-share` of them tagged `transport`, and all of them with every
--tag. The same arguments write
the same nodeset, and its file says what wrote it.
"""
import argparse
import math
import os
import random
import sys

import geodata
import nodeset as nodeset_module
import store

SHAPES = ("hex", "disc", "clusters")


def hex_points(spacing_m, radius_m, jitter_m, rng):
    """Every point of a hexagonal lattice of `spacing_m` within `radius_m`
    of the origin, each moved up to `jitter_m`."""
    r = spacing_m / math.sqrt(3.0)             # the cell's radius
    col_step, row_step = 1.5 * r, spacing_m
    cols = int(math.ceil(radius_m / col_step))
    rows = int(math.ceil(radius_m / row_step)) + 1
    points = []
    for col in range(-cols, cols + 1):
        for row in range(-rows, rows + 1):
            x = col * col_step
            y = row * row_step + (row_step / 2.0 if col % 2 else 0.0)
            if math.hypot(x, y) <= radius_m + 1e-6:
                points.append((x, y))
    return [moved(p, jitter_m, rng) for p in points]


def moved(point, jitter_m, rng):
    if not jitter_m:
        return point
    angle, dist = 2.0 * math.pi * rng.random(), jitter_m * math.sqrt(rng.random())
    return point[0] + dist * math.cos(angle), point[1] + dist * math.sin(angle)


def disc_points(n, radius_m, rng):
    """`n` points at random in a disc, evenly by area."""
    return [moved((0.0, 0.0), radius_m, rng) for _ in range(n)]


def cluster_points(clusters, per, spread_m, apart_m, bridge, rng):
    """Crowds on a line `apart_m` apart, `per` points each within `spread_m`
    of their centre, and `bridge` points evenly between every two."""
    centres = [((i - (clusters - 1) / 2.0) * apart_m, 0.0) for i in range(clusters)]
    points = []
    for cx, cy in centres:
        for _ in range(per):
            dx, dy = moved((0.0, 0.0), spread_m, rng)
            points.append((cx + dx, cy + dy))
    for (ax, _), (bx, _) in zip(centres, centres[1:]):
        for j in range(bridge):
            points.append((ax + (bx - ax) * (j + 1) / (bridge + 1), 0.0))
    return points


def to_degrees(x, y, center):
    """Metres east and north of `center` as (lat, lon)."""
    lat0, lon0 = center
    lat = lat0 + y / geodata.M_PER_DEGREE
    return lat, lon0 + x / (geodata.M_PER_DEGREE * math.cos(math.radians(lat0)))


def generate(shape, seed=1, spacing_m=500.0, radius_m=3000.0, jitter_m=0.0, n=50,
             clusters=2, per=12, spread_m=300.0, apart_m=3000.0, bridge=1,
             height_m=nodeset_module.DEFAULT_HEIGHT_M, antenna=None, transport_share=1.0,
             tags=(), center=(0.0, 0.0), prefix="n"):
    """The nodeset (its data) of a drawn layout."""
    if shape not in SHAPES:
        raise store.StoreError("a layout is one of %s, not %r" % (", ".join(SHAPES), shape))
    if not 0.0 <= transport_share <= 1.0:
        raise store.StoreError("a transport share is between 0 and 1, not %g" % transport_share)
    rng = random.Random(seed)
    if shape == "hex":
        if spacing_m <= 0:
            raise store.StoreError("a hexagonal layout needs a spacing above 0 m")
        points = hex_points(spacing_m, radius_m, jitter_m, rng)
    elif shape == "disc":
        points = disc_points(int(n), radius_m, rng)
    else:
        points = cluster_points(int(clusters), int(per), spread_m, apart_m, int(bridge), rng)
    points.sort(key=lambda p: (-round(p[1], 6), round(p[0], 6)))
    count = len(points)
    if not count:
        raise store.StoreError("that layout has no nodes")
    transport = set(rng.sample(range(count), int(round(transport_share * count))))
    extra = nodeset_module.check_tags(tags)
    width = max(3, len(str(count)))
    data = nodeset_module.blank()
    for index, (x, y) in enumerate(points):
        lat, lon = to_degrees(x, y, center)
        node_tags = (["transport"] if index in transport else []) + [
            t for t in extra if t != "transport"]
        name = "%s%0*d" % (prefix, width, index + 1)
        data["nodes"][store.check_name(name, "node")] = nodeset_module.node_record(
            index + 1, lat, lon, height_m, "assumed", antenna=antenna, tags=node_tags)
    return data


def describe(args):
    share = ("all" if args.transport_share == 1 else "none" if args.transport_share == 0
             else "%g %%" % (100 * args.transport_share))
    common = "seed %d, %g m high, %s of them transport" % (args.seed, args.height_m, share)
    if args.shape == "hex":
        shape = "hex, %g m apart within %g m, jitter %g m" % (
            args.spacing_m, args.radius_m, args.jitter_m)
    elif args.shape == "disc":
        shape = "disc, %d nodes within %g m" % (args.n, args.radius_m)
    else:
        shape = "clusters, %d of %d within %g m, %g m apart, %d between" % (
            args.clusters, args.per, args.spread_m, args.apart_m, args.bridge)
    return "Generated by layouts.py: %s; %s." % (shape, common)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shape", choices=SHAPES)
    ap.add_argument("name", help="the nodeset to write")
    ap.add_argument("--spacing-m", type=float, default=500.0)
    ap.add_argument("--radius-m", type=float, default=3000.0)
    ap.add_argument("--jitter-m", type=float, default=0.0)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--clusters", type=int, default=2)
    ap.add_argument("--per", type=int, default=12)
    ap.add_argument("--spread-m", type=float, default=300.0)
    ap.add_argument("--apart-m", type=float, default=3000.0)
    ap.add_argument("--bridge", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--height-m", type=float, default=nodeset_module.DEFAULT_HEIGHT_M)
    ap.add_argument("--antenna", help="every node's antenna type (default: the store's)")
    ap.add_argument("--transport-share", type=float, default=1.0)
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--center", default="0,0", help="LAT,LON in degrees (default 0,0)")
    ap.add_argument("--prefix", default="n")
    ap.add_argument("--force", action="store_true", help="write over a nodeset of that name")
    args = ap.parse_args(argv)
    try:
        center = tuple(float(v) for v in args.center.split(","))
        if len(center) != 2:
            raise ValueError
    except ValueError:
        ap.error("--center is LAT,LON")
    try:
        data = generate(args.shape, args.seed, args.spacing_m, args.radius_m, args.jitter_m,
                        args.n, args.clusters, args.per, args.spread_m, args.apart_m,
                        args.bridge, args.height_m, {"type": args.antenna} if args.antenna
                        else None, args.transport_share, args.tag, center, args.prefix)
        path = nodeset_module.nodeset_path(store.check_name(args.name, "nodeset"))
        if not args.force and os.path.exists(path):
            raise store.StoreError("there is already a nodeset called %r (--force)" % args.name)
        nodeset_module.write(path, data, describe(args))
    except store.StoreError as err:
        ap.error(str(err))
    nodes = data["nodes"]
    print("%s: %d nodes, %d transport" % (
        path, len(nodes), sum(1 for v in nodes.values() if "transport" in v["tags"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
