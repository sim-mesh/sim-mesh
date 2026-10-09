"""The source kinds past the starting set: RD New, an index of footprints
(GeoJSON, FlatGeobuf and an ArcGIS layer), a window of a cloud-optimised
GeoTIFF fetched into a sparse copy, a GeoPackage grid as the compiler's CSV,
members of remote zips fetched alone, templates of WCS boxes and offset
grids, and hosts that refuse HEAD."""

import asyncio
import json
import os
import sqlite3
import struct
import sys
import zipfile

import aiohttp
import pytest
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cogwindow  # noqa: E402
import crs  # noqa: E402
import fgb  # noqa: E402
import sourcefile  # noqa: E402
import sources  # noqa: E402

SHIPPED = sourcefile.load((sourcefile.SHIPPED,))


def test_rd_new_both_ways_and_its_origin():
    rd = crs.RdNew()
    assert rd.inverse(155000, 463000) == pytest.approx((52.1551744, 5.38720621), abs=1e-9)
    x, y = rd.forward(52.0116, 4.3571)             # Delft, the Markt
    assert 84000 < x < 85500 and 446500 < y < 448000
    assert rd.inverse(x, y) == pytest.approx((52.0116, 4.3571), abs=1e-6)
    box = crs.box_in("EPSG:28992", [4.345, 52.003, 4.39, 52.021], margin=10)
    assert box[0] < x < box[2] and box[1] < y < box[3]
    assert crs.proj("EPSG:28992").startswith("+proj=sterea") and crs.known("EPSG:7415")
    assert not crs.known("EPSG:2154")


# ---- FlatGeobuf, written here --------------------------------------------------

class Flat:
    """Just enough of a flatbuffer writer for a FlatGeobuf file: tables laid
    out forward, each child after its parent."""

    def __init__(self):
        self.buf = bytearray()

    def table(self, fields):
        """fields: [(slot, kind, value)], kind one of u8, u16, u64, str,
        bytes, f64s, u32s, table, tables. Its position."""
        slots = max(s for s, _k, _v in fields) + 1
        vt = len(self.buf)
        self.buf += struct.pack("<HH", 4 + 2 * slots, 0) + b"\0\0" * slots
        pos = len(self.buf)
        self.buf += struct.pack("<i", pos - vt)
        refs = []
        for slot, kind, value in fields:
            at = len(self.buf) - pos
            struct.pack_into("<H", self.buf, vt + 4 + 2 * slot, at)
            if kind in ("u8", "u16", "u64"):
                self.buf += struct.pack({"u8": "<B", "u16": "<H", "u64": "<Q"}[kind], value)
            else:
                refs.append((len(self.buf), kind, value))
                self.buf += b"\0\0\0\0"
        struct.pack_into("<H", self.buf, vt + 2, len(self.buf) - pos)
        for field, kind, value in refs:
            target = self._child(kind, value)
            struct.pack_into("<I", self.buf, field, target - field)
        return pos

    def _child(self, kind, value):
        if kind == "table":
            return self.table(value)
        at = len(self.buf)
        if kind == "str":
            raw = value.encode()
            self.buf += struct.pack("<I", len(raw)) + raw + b"\0"
        elif kind == "bytes":
            self.buf += struct.pack("<I", len(value)) + value
        elif kind == "f64s":
            self.buf += struct.pack("<I%dd" % len(value), len(value), *value)
        elif kind == "u32s":
            self.buf += struct.pack("<I%dI" % len(value), len(value), *value)
        elif kind == "tables":
            self.buf += struct.pack("<I", len(value)) + b"\0\0\0\0" * len(value)
            for i, fields in enumerate(value):
                slot = at + 4 + 4 * i
                struct.pack_into("<I", self.buf, slot, self.table(fields) - slot)
        return at

    def root(self, fields):
        self.buf += b"\0\0\0\0"
        struct.pack_into("<I", self.buf, 0, self.table(fields))
        return bytes(self.buf)


def write_fgb(features):
    """A FlatGeobuf file of polygons with a url and a tile column, no index."""
    header = Flat().root([(0, "str", "tiles"), (2, "u8", 3),
                          (7, "tables", [[(0, "str", "tile_id"), (1, "u8", fgb.STRING)],
                                         [(0, "str", "url"), (1, "u8", fgb.STRING)]]),
                          (8, "u64", len(features)), (9, "u16", 0)])
    out = fgb.MAGIC + b"fgb\0" + struct.pack("<I", len(header)) + header
    for tile, url, ring in features:
        props = b""
        for col, text in ((0, tile), (1, url)):
            raw = text.encode()
            props += struct.pack("<HI", col, len(raw)) + raw
        xy = [v for p in ring for v in p]
        geom = [(1, "f64s", xy), (6, "u8", 3)]
        feature = Flat().root([(0, "table", geom), (1, "bytes", props)])
        out += struct.pack("<I", len(feature)) + feature
    return out


def test_a_flatgeobuf_index_reads_its_footprints_and_properties():
    ring = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    raw = write_fgb([("a", "https://x/a.json.gz", ring),
                     ("b", "https://x/b.json.gz", [(x + 20, y) for x, y in ring])])
    got = fgb.read(raw)
    assert [p["tile_id"] for p, _r in got] == ["a", "b"]
    assert got[1][0]["url"] == "https://x/b.json.gz" and got[1][1][0][1] == (30, 0)
    with pytest.raises(fgb.FgbError):
        fgb.read(b"not fgb at all")


def test_an_index_gives_the_files_whose_footprint_meets_the_rectangle():
    dtm = SHIPPED["ahn-dtm"]
    rd = crs.RdNew()
    x, y = rd.forward(52.0116, 4.3571)
    near = [[(x - 100, y - 100), (x + 100, y - 100), (x + 100, y + 100), (x - 100, y + 100),
             (x - 100, y - 100)]]
    far = [[(px + 50000, py) for px, py in near[0]]]
    features = [({"url": "https://h/d/M_NEAR.tif"}, near), ({"url": "https://h/d/M_FAR.tif"}, far)]
    got = sources.index_files(dtm, features, [4.35, 52.0, 4.36, 52.02], res_m=10)
    assert [f.name for f in got] == ["M_NEAR.tif"]
    assert got[0].window["want"] == 2.5 and got[0].window["box"][0] < x


# ---- a window of a tiled GeoTIFF ------------------------------------------------

def write_tiled_tiff(path):
    """An 8 × 8 float image in 4 × 4 tiles with a 4 × 4 overview of one
    tile, a 1 m pixel, its corner at (1000, 2000): every value its tile's
    number, the overview's 9."""
    def ifd_bytes(at, width, tile, offsets, counts, geo):
        entries = [(256, 3, 1, width), (257, 3, 1, width), (258, 3, 1, 32), (259, 3, 1, 1),
                   (322, 3, 1, tile), (323, 3, 1, tile), (339, 3, 1, 3)]
        values = b""
        n = len(entries) + 2 + (2 if geo else 0)
        val_at = at + 2 + 12 * n + 4

        def out_of_line(raw):
            nonlocal values
            if len(raw) <= 4:                       # it fits in the entry itself
                return struct.unpack("<I", raw.ljust(4, b"\0"))[0]
            off = val_at + len(values)
            values += raw
            return off
        rows = [struct.pack("<HHII", t, typ, c, v) for t, typ, c, v in entries]
        rows.append(struct.pack("<HHII", 324, 4, len(offsets), out_of_line(struct.pack("<%dI" % len(offsets), *offsets))))
        rows.append(struct.pack("<HHII", 325, 4, len(counts), out_of_line(struct.pack("<%dI" % len(counts), *counts))))
        if geo:
            rows.append(struct.pack("<HHII", 33550, 12, 3, out_of_line(struct.pack("<3d", 1.0, 1.0, 0.0))))
            rows.append(struct.pack("<HHII", 33922, 12, 6, out_of_line(struct.pack("<6d", 0, 0, 0, 1000.0, 2000.0, 0))))
        return rows, values

    # The chunks lie past the head a fetch reads first, further apart than
    # the gap two ranges are merged across, so each one is fetched alone.
    tile_bytes = 4 * 4 * 4
    header_room = cogwindow.HEAD_BYTES + cogwindow.MERGE_GAP
    stride = 2 * cogwindow.MERGE_GAP
    full_offsets = [header_room + i * stride for i in range(4)]
    over_offsets = [header_room + 4 * stride]
    ifd0_at = 8
    rows0, values0 = ifd_bytes(ifd0_at, 8, 4, full_offsets, [tile_bytes] * 4, True)
    ifd1_at = ifd0_at + 2 + 12 * len(rows0) + 4 + len(values0)
    rows1, values1 = ifd_bytes(ifd1_at, 4, 4, over_offsets, [tile_bytes], False)
    out = bytearray(b"II*\0" + struct.pack("<I", ifd0_at))
    out += struct.pack("<H", len(rows0)) + b"".join(rows0) + struct.pack("<I", ifd1_at) + values0
    out += struct.pack("<H", len(rows1)) + b"".join(rows1) + struct.pack("<I", 0) + values1
    out += b"\0" * (header_room - len(out))
    for k, value in enumerate([0.0, 1.0, 2.0, 3.0, 9.0]):
        out += b"\0" * (header_room + k * stride - len(out))
        out += struct.pack("<16f", *([value] * 16))
    with open(path, "wb") as handle:
        handle.write(out)
    return full_offsets, over_offsets


def test_a_window_fetches_the_directories_and_only_its_chunks_into_a_sparse_copy(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    full, over = write_tiled_tiff(served / "m.tif")
    original = (served / "m.tif").read_bytes()

    async def check(base, session, calls):
        cache = sources.Cache(session, str(tmp_path / "cache"))
        # The top-left tile only: x 1000…1003, y 1996…1999, read at 1 m.
        f = sources.File("ahn-dtm", base + "/m.tif", "m.tif",
                         window={"box": [1000.5, 1996.5, 1002.5, 1998.5], "want": 1.0})
        assert cache.have(f) is None
        missing = (await cache.sizes_of([f]))[f]
        assert missing == sum(n for _s, n in f.needs)
        assert (full[0], 64) in f.needs and (full[1], 64) not in f.needs
        path = await cache.fetch(f)
        copy = open(path, "rb").read()
        assert len(copy) == len(original)
        assert copy[full[0]:full[0] + 64] == original[full[0]:full[0] + 64]
        assert copy[full[1]:full[1] + 64] == b"\0" * 64          # never fetched: a hole
        assert cache.have(f) is True
        before = len(calls)
        await cache.fetch(f)
        assert len(calls) == before                                # held already
        # At 2 m the overview is the level, and its one chunk is fetched beside the first.
        g = sources.File("ahn-dtm", base + "/m.tif", "m.tif",
                         window={"box": [1000.5, 1996.5, 1002.5, 1998.5], "want": 2.0})
        await cache.fetch(g)
        copy = open(path, "rb").read()
        assert copy[over[0]:over[0] + 64] == original[over[0]:over[0] + 64]
        assert copy[full[1]:full[1] + 64] == b"\0" * 64
    import test_sources
    test_sources.running_host(served, check)


def test_the_level_is_the_coarsest_no_larger_than_wanted(tmp_path):
    write_tiled_tiff(tmp_path / "m.tif")
    data = cogwindow.Bytes(cogwindow.local_reader(str(tmp_path / "m.tif")))
    st = asyncio.run(cogwindow.structure(data))
    assert [im["width"] for im in st["images"]] == [8, 4]
    assert cogwindow.level_for(st, 0.5) == 0 and cogwindow.level_for(st, 1.9) == 0
    assert cogwindow.level_for(st, 2.0) == 1 and cogwindow.level_for(st, 8) == 1
    assert len(cogwindow.chunks_for(st, 0, [1000, 1992, 1008, 2000])) == 4
    assert cogwindow.chunks_for(st, 0, [2000, 3000, 2001, 3001]) == []


# ---- a GeoPackage grid ------------------------------------------------------------

def gpkg_cell(x0, y0, size):
    """A GeoPackage geometry: its header with an envelope, then a WKB polygon."""
    ring = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0)]
    wkb = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring)) + b"".join(
        struct.pack("<2d", *p) for p in ring)
    return b"GP\0\x03" + struct.pack("<i", 28992) + struct.pack("<4d", x0, x0 + size, y0, y0 + size) + wkb


def test_a_geopackage_grid_becomes_the_compilers_csv(tmp_path):
    gpkg = tmp_path / "grid.gpkg"
    db = sqlite3.connect(gpkg)
    db.execute("create table gpkg_geometry_columns (table_name, column_name)")
    db.execute("insert into gpkg_geometry_columns values ('vk', 'geom')")
    db.execute("create table vk (geom blob, aantal_inwoners integer)")
    db.execute("insert into vk values (?, 40)", (gpkg_cell(84000, 447000, 100),))
    db.execute("insert into vk values (?, -99997)", (gpkg_cell(84100, 447000, 100),))
    db.commit()
    db.close()
    (tmp_path / "cbs-population").mkdir()
    with zipfile.ZipFile(tmp_path / "cbs-population" / "grid.zip", "w") as zf:
        zf.write(gpkg, "grid.gpkg")
    cache = sources.Cache(None, str(tmp_path))
    f = sources.single_file(SHIPPED["cbs-population"])
    f.name = "grid.zip"
    csv = cache.grid_csv(f, "aantal_inwoners")
    assert open(csv).read() == "x,y,value\n84050.000,447050.000,40\n84150.000,447050.000,-99997\n"
    assert cache.grid_csv(f, "aantal_inwoners") == csv          # written once


# ---- members of remote zips ---------------------------------------------------------

def zip_source(**find):
    entry = {"id": "zz", "title": "Zipped tiles", "licence": "x", "notice": "x",
             "redistributable": True, "layers": {"terrain": 100}, "coverage": "outline",
             "find": dict({"method": "zip", "unit_m": 1000, "size_m": 1000,
                           "crs": "EPSG:25832"}, **find),
             "read": "whole", "format": {"type": "geotiff", "band": 1, "crs": "EPSG:25832"}}
    return sourcefile.Source(entry, "own.yaml", "europe", "DE", "Germany")


def test_members_of_a_remote_zip_and_of_a_zip_stored_in_it_are_fetched_alone(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    tile = bytes(range(256)) * 400
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Tiles/LoD2_32_466_5894_2_HB.gml", b"<CityModel/>" * 50)
    with zipfile.ZipFile(served / "state.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("DGM1_1/", b"")
        zf.writestr("DGM1_1/dgm1_32_606_5760_2_st.tif", tile)
        zf.writestr("DGM1_1/dgm1_32_606_5760_2_st.meta", b"meta")
        zf.write(inner, "nested.zip", compress_type=zipfile.ZIP_STORED)

    async def check(base, session, calls):
        cache = sources.Cache(session, str(tmp_path / "cache"))
        members = await cache.zip_members(base + "/state.zip")
        assert [m["name"] for m in members] == [
            "DGM1_1/dgm1_32_606_5760_2_st.tif", "DGM1_1/dgm1_32_606_5760_2_st.meta", "nested.zip"]
        source = zip_source(archives=[base + "/state.zip"],
                            name="dgm1_32_(?P<x>\\d{3})_(?P<y>\\d{4})_2_st\\.tif$", size_m=2000)
        assert [(x, y) for _m, x, y in sources.zip_tiles(source, members)] == [(606, 5760)]
        [f] = sources.zip_files(source, members, [10.55, 51.98, 10.56, 51.99])
        assert f.name == "dgm1_32_606_5760_2_st.tif" and f.url == base + "/state.zip"
        assert await cache.size(f) == f.member["csize"] < len(tile)
        path = await cache.fetch(f)
        assert open(path, "rb").read() == tile and cache.have(f) is True
        assert not sources.zip_files(source, members, [13.0, 52.0, 13.1, 52.1])
        # A tile two archives hold is fetched from the first.
        twice = members + [dict(members[0], archive=base + "/other.zip")]
        [f] = sources.zip_files(source, twice, [10.55, 51.98, 10.56, 51.99])
        assert f.url == base + "/state.zip"
        # A zip stored in the zip is read in place, its members by the outer's offsets.
        nested = await cache.zip_members(base + "/state.zip!nested.zip")
        assert [m["name"] for m in nested] == ["Tiles/LoD2_32_466_5894_2_HB.gml"]
        g = sources.File("zz", nested[0]["archive"], "LoD2_32_466_5894_2_HB.gml",
                         member=nested[0])
        assert open(await cache.fetch(g), "rb").read() == b"<CityModel/>" * 50
        with pytest.raises(sources.SourceError, match="holds no"):
            await cache.zip_members(base + "/state.zip!other.zip")
    import test_sources
    test_sources.running_host(served, check)


def test_a_zip_sources_listing_is_kept_and_served_when_an_archive_fails(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    with zipfile.ZipFile(served / "a.zip", "w") as zf:
        zf.writestr("dgm1_32_606_5760_2_st.tif", b"t")

    async def check(base, session, calls):
        cache = sources.Cache(session, str(tmp_path / "cache"))
        source = zip_source(archives=[base + "/a.zip"], name="(?P<x>\\d{3})_(?P<y>\\d{4})",
                            refresh_days=0)
        assert len(await cache.zip_listing(source)) == 1
        os.remove(served / "a.zip")
        assert len(await cache.zip_listing(source)) == 1
    import test_sources
    test_sources.running_host(served, check)


# ---- an ArcGIS feature layer as an index ------------------------------------------

def test_an_arcgis_layer_is_read_a_page_at_a_time_keeping_the_newest_of_each_tile(
        tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ARCGIS_PAGE", 2)
    square = lambda x, y: {"type": "Polygon", "coordinates": [  # noqa: E731
        [[x, y], [x + 1000, y], [x + 1000, y + 1000], [x, y + 1000], [x, y]]]}
    features = [
        {"geometry": square(550000, 5800000), "properties": {"dgm1": "https://s/L1603/a_2016.tif",
                                                              "tile_id": 1, "Aktualitaet": 10}},
        {"geometry": square(550000, 5800000), "properties": {"dgm1": "https://s/L2502/a_2025.tif",
                                                              "tile_id": 1, "Aktualitaet": 20}},
        {"geometry": square(551000, 5800000), "properties": {"dgm1": "https://s/L2502/b_2025.tif",
                                                              "tile_id": 2, "Aktualitaet": 20}},
    ]
    asked = []

    async def query(request):
        asked.append(dict(request.query))
        start, n = int(request.query["resultOffset"]), int(request.query["resultRecordCount"])
        page = [dict(f, type="Feature") for f in features[start:start + n]]
        return web.json_response({"type": "FeatureCollection", "features": page,
                                  "properties": {"exceededTransferLimit":
                                                 start + n < len(features)}})

    async def go():
        app = web.Application()
        app.router.add_get("/FeatureServer/0/query", query)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = "http://127.0.0.1:%d" % runner.addresses[0][1]
        source = sourcefile.Source(
            dict(SHIPPED["ni-dgm1"].entry, find=dict(SHIPPED["ni-dgm1"].find,
                                                     index=base + "/FeatureServer/0")),
            "own.yaml", "europe", "DE", "Germany")
        try:
            async with aiohttp.ClientSession() as session:
                cache = sources.Cache(session, str(tmp_path / "cache"))
                got = await cache.index_features(source)
        finally:
            await runner.cleanup()
        return got
    got = asyncio.run(go())
    assert [q["resultOffset"] for q in asked] == ["0", "2"]
    assert asked[0]["outSR"] == "25832" and asked[0]["f"] == "geojson"
    assert sorted(p["dgm1"] for p, _r in got) == ["https://s/L2502/a_2025.tif",
                                                    "https://s/L2502/b_2025.tif"]


def test_an_index_keeps_a_file_named_in_its_query_by_that_name():
    sh = SHIPPED["sh-lod2"]
    url = ("https://geodaten.schleswig-holstein.de/gaialight-sh/_apps/dladownload/massen.php"
           "?file=LoD2_32_426_6004_1_SH.xml&id=4&live=2024&km=32420_6000")
    ring = [[(426000, 6004000), (427000, 6004000), (427000, 6005000), (426000, 6005000),
             (426000, 6004000)]]
    [f] = sources.index_files(sh, [({"data_link": url}, ring)], [7.87, 54.18, 7.88, 54.19])
    assert f.name == "LoD2_32_426_6004_1_SH.xml" and f.url == url


# ---- templates in metres: an offset grid, a WCS box --------------------------------

def test_a_template_grid_starts_at_its_origin():
    bw = SHIPPED["bw-dgm1"]
    hull = [9.17, 48.77, 9.19, 48.782]                       # Stuttgart, 513 km E
    names = [f.name for f in sources.template_files(bw, hull)]
    assert names and all(int(n.split("_")[2]) % 2 == 1 for n in names), names
    assert all(int(n.split("_")[3]) % 2 == 0 for n in names), names
    assert "dgm1_32_513_5402_2_bw.zip" in names


def test_a_template_names_its_far_corner_and_keeps_its_file_under_its_own_name():
    he = SHIPPED["he-dgm1"]
    [f] = sources.template_files(he, [8.6801, 50.1101, 8.6802, 50.1102])
    x = int(f.name.split("_")[2])
    assert f.name == "dgm1_32_%d_%d_he.tif" % (x, int(f.name.split("_")[3]))
    assert x % 1000 == 0
    assert "SUBSET=E(%d,%d)&SUBSET=N(" % (x, x + 1000) in f.url
    m = sources.template_pattern(he).match(f.name)
    assert m and sources.template_corner(he, m)[0] == x


# ---- a host that refuses HEAD -------------------------------------------------------

def test_a_host_that_refuses_head_is_asked_for_two_bytes(tmp_path):
    body = b"z" * 5000
    asked = []

    async def tile(request):
        asked.append((request.method, request.headers.get("Range")))
        if request.method == "HEAD":
            raise web.HTTPUnauthorized()
        return web.Response(status=206, body=body[0:2],
                            headers={"Content-Range": "bytes 0-1/%d" % len(body)})

    async def go():
        app = web.Application()
        app.router.add_route("*", "/t.zip", tile)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = "http://127.0.0.1:%d" % runner.addresses[0][1]
        try:
            async with aiohttp.ClientSession() as session:
                cache = sources.Cache(session, str(tmp_path / "cache"))
                return await cache.size(sources.File("sn-dgm1", base + "/t.zip", "t.zip"))
        finally:
            await runner.cleanup()
    assert asyncio.run(go()) == 5000
    assert asked == [("HEAD", None), ("GET", "bytes=0-1")]


def test_a_coverage_services_exception_page_is_no_data_there(tmp_path):
    async def ows(request):
        if request.query.get("box") == "in":
            return web.Response(body=b"II*\0tiff", content_type="image/tiff")
        if request.query.get("box") == "json":
            return web.Response(body=b'{"error":{"code":400}}', content_type="image/tiff")
        status = 400 if request.query.get("box") == "out400" else 200
        return web.Response(status=status, text="<ows:ExceptionReport/>",
                            content_type="text/xml")

    async def go():
        app = web.Application()
        app.router.add_get("/ows", ows)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = "http://127.0.0.1:%d" % runner.addresses[0][1]
        got = []
        try:
            async with aiohttp.ClientSession() as session:
                cache = sources.Cache(session, str(tmp_path / "cache"))
                for box in ("in", "out200", "out400", "json"):
                    f = sources.File("no-nhm-dtm", base + "/ows?box=" + box, box + ".tif")
                    f.raster = True
                    got.append(await cache.fetch(f))
        finally:
            await runner.cleanup()
        return got
    got = asyncio.run(go())
    assert got[0] and open(got[0], "rb").read() == b"II*\0tiff"
    assert got[1:] == [None, None, None]
    # Asked of a template, a GeoTIFF source's file is one.
    [f] = sources.template_files(SHIPPED["no-nhm-dtm"], [5.31, 60.39, 5.311, 60.391])
    assert f.raster
