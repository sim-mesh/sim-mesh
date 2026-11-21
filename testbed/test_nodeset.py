"""Nodesets: the file, edits, the geometry hash, the extent, and the planner imports."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import boards  # noqa: E402
import nodeset  # noqa: E402
import store  # noqa: E402

SAMPLE = """\
nodes:
  gw-alex: { id: 1, lat: 52.5219, lon: 13.4132, height_m: 38, height_from: roof, max_dbm: 27, antenna: { type: panel_directional, azimuth_deg: 120, elevation_deg: -2 }, tags: [transport, gateway] }
  n017: { id: 2, lat: 52.5301, lon: 13.4018, height_m: 15, height_from: assumed, antenna: { type: whip_sma_quarter_wave }, tags: [rooftop, no-radio] }
offsets:
  - { between: [gw-alex, n017], db: 40, note: "wall, measured 2026-09-20" }
"""


@pytest.fixture
def nodesets_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "NODESETS_DIR", str(tmp_path))
    return tmp_path


def test_the_documented_example_reads_and_writes_back_unchanged(nodesets_dir):
    (nodesets_dir / "ex.yaml").write_text(SAMPLE)
    ns = nodeset.load("ex")
    alex = ns.node("gw-alex")
    assert alex["antenna"] == {"type": "panel_directional", "azimuth_deg": 120,
                               "elevation_deg": -2}
    # A role is a tag; a node without a radio says so with one.
    assert nodeset.tag_role(alex["tags"]) == "transport"
    assert nodeset.tag_role(ns.node("n017")["tags"]) is None
    assert nodeset.has_radio(alex) and not nodeset.has_radio(ns.node("n017"))
    # A node's own maximum; one that states none sends at most 22 dBm.
    assert boards.max_dbm(alex["max_dbm"]) == 27
    assert "max_dbm" not in ns.node("n017") and boards.max_dbm(None) == 22
    # What a station is told: flat, the node's maximum, and above 22 dBm the
    # GC1109 front end's figures.
    told = json.loads(boards.environment(alex["max_dbm"]))
    assert told["chip"] == "sx1262" and told["max_dbm"] == 27 and told["fem_part"] == "gc1109"
    assert told["fem_tx_cal"].startswith("gc1109 measured 1:7,")
    assert told["fem_rx_gain_db"] == 20 and told["fem_gain_db"] == 0
    assert json.loads(boards.environment(None)) == {"chip": "sx1262", "max_dbm": 22}
    assert json.loads(boards.environment(22)) == {"chip": "sx1262", "max_dbm": 22}
    assert json.loads(boards.environment(14)) == {"chip": "sx1262", "max_dbm": 14}
    assert json.loads(boards.environment(14, noise_rise_db=11.0))["noise_rise_db"] == 11.0
    assert "noise_rise_db" not in json.loads(boards.environment(14, noise_rise_db=0.0))
    assert json.loads(boards.environment(23))["fem_part"] == "gc1109"
    assert ns.offset("n017", "gw-alex") == 40
    assert nodeset.dump(ns.data) == SAMPLE
    assert ns.tags() == {"gateway": 1, "no-radio": 1, "rooftop": 1, "transport": 1}


def test_names_ids_and_heights_are_checked_and_nodes_declare_nothing(tmp_path):
    for text, match in (
            ("nodes:\n  Bad_Name: { id: 1, lat: 0, lon: 0 }\n", "usable node name"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0 }\n  b: { id: 1, lat: 0, lon: 0 }\n", "share id"),
            ("nodes:\n  a: { id: 0, lat: 0, lon: 0 }\n", "a node id is"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, height_from: guessed }\n", "height_from"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, role: transport }\n", "declares no role"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, radio: { sf: 8 } }\n", "declares no radio"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, device: dev }\n", r"\.firmware\(…\)"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, board: { type: heltec_v4 } }\n",
             "has no board.*max_dbm"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, max_dbm: 30 }\n", "max_dbm is -9 to 27"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, max_dbm: loud }\n", "max_dbm is a number"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, noise_rise_db: loud }\n",
             "noise_rise_db is a finite number"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, noise_rise_db: -3 }\n", "0 dB or more"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, antenna: { type: dish } }\n", "no antenna"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0, antenna: { gain_dbi: 2 } }\n",
             "antenna has no"),
            ("nodes:\n  a: { id: 1, lat: 0, lon: 0 }\noffsets:\n"
             "  - { between: [a, z], db: 3 }\n", "not a node")):
        path = tmp_path / "f.yaml"
        path.write_text(text)
        with pytest.raises(store.StoreError, match=match):
            nodeset.read(str(path))


def test_a_number_no_file_could_hold_is_refused_where_it_is_read(tmp_path):
    """NaN and infinity read as numbers, and store.scalar cannot write them:
    a nodeset holding one loaded, and could then not be saved."""
    node = ("nodes:\n  a: { id: 1, lat: %s, lon: %s, height_m: %s%s }\n"
            "  b: { id: 2, lat: 0, lon: 1 }\n")
    aimed = ", antenna: { type: panel_directional, azimuth_deg: .inf }"
    for text, match in (
            (node % (".nan", 0, 2, ""), "node a: lat is a finite number, not nan"),
            (node % (0, "-.inf", 2, ""), "node a: lon is a finite number, not -inf"),
            (node % (0, 0, ".inf", ""), "node a: height_m is a finite number, not inf"),
            (node % (0, 0, 2, aimed), "node a: an antenna's azimuth_deg is a finite number"),
            (node % (0, 0, 2, "") + "offsets:\n  - { between: [a, b], db: .nan }\n",
             "offset between a and b states db nan")):
        path = tmp_path / "f.yaml"
        path.write_text(text)
        with pytest.raises(store.StoreError, match=match):
            nodeset.read(str(path))


def test_a_node_figure_that_is_no_number_is_refused_by_its_key(tmp_path):
    """A word where a node's height goes raised float()'s ValueError, none a
    TypeError, and an infinite id int()'s OverflowError, where every other
    bad value is a StoreError."""
    node = "nodes:\n  a: { id: %s, lat: %s, lon: 0%s }\n"
    for text, match in (
            (node % (1, 0, ", height_m: tall"), "node a: height_m is a finite number, not 'tall'"),
            (node % (1, 0, ", height_m: null"), "node a: height_m is a finite number, not None"),
            (node % (".inf", 0, ""), "node a needs id, lat and lon"),
            (node % (1, "north", ""), "node a needs id, lat and lon"),
            (node % (1, 0, ", max_dbm: [27]"), "node a: max_dbm is a number")):
        path = tmp_path / "f.yaml"
        path.write_text(text)
        with pytest.raises(store.StoreError, match=match):
            nodeset.read(str(path))


def test_offsets_are_a_list_and_an_offsets_ends_a_list_of_nodes(tmp_path):
    # A string's letters were read as the ends: `ab` joined nodes a and b.
    head = "nodes:\n  a: { id: 1, lat: 0, lon: 0 }\n  b: { id: 2, lat: 0, lon: 0.01 }\n"
    for text, match in (("offsets:\n  - { between: ab, db: 3 }\n", "an offset is"),
                        ("offsets: 5\n", "offsets is a list")):
        path = tmp_path / "f.yaml"
        path.write_text(head + text)
        with pytest.raises(store.StoreError, match=match):
            nodeset.read(str(path))


def test_an_offset_is_between_two_nodes(tmp_path):
    """A third name, and any after it, was passed over without a word: the
    offset held between the first two. A link's is refused, and so is this."""
    head = ("nodes:\n  a: { id: 1, lat: 0, lon: 0 }\n  b: { id: 2, lat: 0, lon: 0.01 }\n"
            "  c: { id: 3, lat: 0, lon: 0.02 }\noffsets:\n  - { between: %s, db: 3 }\n")
    for ends in ("[a, b, c]", "[a, b, zz]", "[a]", "[]"):
        path = tmp_path / "f.yaml"
        path.write_text(head % ends)
        with pytest.raises(store.StoreError, match="an offset is { between: \\[a, b\\]"):
            nodeset.read(str(path))
    path.write_text(head % "[c, a]")
    assert nodeset.read(str(path))["offsets"] == [{"between": ["c", "a"], "db": 3.0}]


def test_edits_mark_it_dirty_and_ids_are_the_lowest_free(nodesets_dir):
    ns = nodeset.create("new")
    assert ns.add_node("a", 0.0, 0.0)["id"] == 1
    assert ns.add_node("b", 0.1, 0.1, height_m=10, tags=["transport"])["id"] == 2
    assert ns.add_node("c", 0.2, 0.2,
                       antenna={"type": "yagi_directional", "azimuth_deg": 370})["id"] == 3
    assert ns.node("a")["antenna"] == {"type": "whip_sma_quarter_wave"}
    assert ns.node("c")["antenna"] == {"type": "yagi_directional", "azimuth_deg": 10.0,
                                       "elevation_deg": 0.0}
    ns.remove_node("b")
    assert ns.next_id() == 2
    assert ns.dirty
    with pytest.raises(store.StoreError, match="already"):
        ns.add_node("a", 0, 0)
    with pytest.raises(store.StoreError, match="already c's"):
        ns.set_node("a", id=3)
    assert ns.set_node("a", id=7) is True
    assert ns.set_node("a", id=7) is False
    ns.set_node("c", tags=["x", "y", "x"])
    assert ns.node("c")["tags"] == ["x", "y"]
    # A maximum power stays until a null clears it.
    ns.set_node("c", max_dbm=24)
    ns.set_node("c", height_m=3)
    assert ns.node("c")["max_dbm"] == 24
    ns.set_node("c", max_dbm=None)
    assert "max_dbm" not in ns.node("c")
    ns.set_offset("a", "c", 12, "a wall")
    ns.rename_node("c", "charlie")
    assert ns.offset("a", "charlie") == 12
    ns.save()
    assert not ns.dirty
    again = nodeset.load("new")
    assert again.node("a")["id"] == 7 and "charlie" in again.nodes
    assert again.offsets[0]["note"] == "a wall"
    with pytest.raises(store.StoreError):
        ns.save_as("new")
    ns.save_as("other")
    assert nodeset.names() == ["new", "other"]


def test_geometry_hash_follows_positions_and_heights_only(nodesets_dir):
    (nodesets_dir / "ex.yaml").write_text(SAMPLE)
    ns = nodeset.load("ex")
    base = ns.geometry_hash()
    ns.set_node("n017", id=9, antenna={"type": "rubber_duck"}, tags=["x"], height_from="measured",
                max_dbm=27)
    ns.set_offset("gw-alex", "n017", 3)
    assert ns.geometry_hash() == base
    for change in (lambda n: n.move_node("n017", 52.5302, 13.4018),
                   lambda n: n.set_node("n017", height_m=16),
                   lambda n: n.add_node("n018", 52.53, 13.40),
                   lambda n: n.rename_node("n017", "n017b")):
        other = nodeset.load("ex")
        change(other)
        assert other.geometry_hash() != base


# ---- links ---------------------------------------------------------------

LINKED = """\
nodes:
  hub: { id: 1, lat: 0.01, lon: 0.02, height_m: 12, height_from: roof, antenna: { type: whip_sma_quarter_wave }, tags: [transport] }
  n017: { id: 2, lat: 0.011, lon: 0.021, height_m: 2, height_from: assumed, antenna: { type: whip_sma_quarter_wave }, tags: [] }
offsets: []
links:
  - { between: [hub, n017], loss_db: 131.5, back_db: 133, note: measured }
"""


def test_links_are_written_only_when_there_are_some_and_round_trip(nodesets_dir):
    (nodesets_dir / "ex.yaml").write_text(SAMPLE)
    ns = nodeset.load("ex")
    # Without links nothing is new: not the data, not what the page is told,
    # not the file.
    assert ns.links == [] and "links" not in ns.data and "links" not in ns.as_dict()
    assert nodeset.dump(ns.data) == SAMPLE
    (nodesets_dir / "ex.yaml").write_text(LINKED)
    ns = nodeset.load("ex")
    assert ns.links == [{"between": ["hub", "n017"], "loss_db": 131.5, "back_db": 133,
                         "note": "measured"}]
    assert nodeset.dump(ns.data) == LINKED
    assert ns.as_dict()["links"] == ns.links
    ns.save_as("again")
    assert nodeset.load("again").data == ns.data


def test_removing_a_node_drops_its_links_and_a_rename_carries_them(nodesets_dir):
    (nodesets_dir / "ex.yaml").write_text(LINKED)
    ns = nodeset.load("ex")
    ns.rename_node("n017", "n018")
    assert ns.links[0]["between"] == ["hub", "n018"]
    nodeset.parse(ns.data, "renamed")               # still names only nodes it has
    ns.remove_node("n018")
    assert ns.links == [] and "links" not in ns.data
    assert "links" not in nodeset.dump(ns.data)


def test_a_link_without_a_figure_is_refused(tmp_path):
    head = "nodes:\n  a: { id: 1, lat: 0, lon: 0 }\n  b: { id: 2, lat: 0, lon: 0.01 }\nlinks:"
    for text, match in (
            ("\n  - { between: [a, b] }\n", "a link is"),
            ("\n  - { between: [a, b], loss_db: loud }\n", "a link is"),
            ("\n  - { between: [a], loss_db: 110 }\n", "a link is"),
            ("\n  - { between: [a, b, c], loss_db: 110 }\n", "a link is"),
            ("\n  - { between: ab, loss_db: 110 }\n", "a link is"),
            ("\n  - between\n", "a link is"),
            (" 110\n", "links is a list"),
            ("\n  - { between: [a, z], loss_db: 110 }\n", "not a node"),
            ("\n  - { between: [a, a], loss_db: 110 }\n", "itself"),
            # A misspelt back_db would make the link the same both ways.
            ("\n  - { between: [a, b], loss_db: 110, back_dB: 130 }\n", "has no back_dB"),
            ("\n  - { between: [a, b], loss_db: .inf }\n", "finite"),
            ("\n  - { between: [a, b], loss_db: 110, back_db: .nan }\n", "finite"),
            ("\n  - { between: [a, b], loss_db: -3 }\n", "0 or more"),
            ("\n  - { between: [a, b], loss_db: 1e39 }\n", "finite"),      # past float32
            ("\n  - { between: [a, b], loss_db: 110 }\n  - { between: [b, a], loss_db: 120 }\n",
             "stated twice")):
        path = tmp_path / "f.yaml"
        path.write_text(head + text)
        with pytest.raises(store.StoreError, match=match):
            nodeset.read(str(path))


def test_a_name_yaml_would_read_as_something_else_is_quoted_and_reads_back(tmp_path):
    """A name may be a YAML word or number. Bare, `no` and `on` read back as
    booleans and `null` as nothing, none of which is a name, and `010` as
    the number 8; as nodes, offsets and links they are quoted."""
    data = nodeset.blank()
    for i, name in enumerate(("no", "on", "null", "010", "0x1f", "123", "2026-09-29"), 1):
        data["nodes"][name] = nodeset.node_record(i, i / 100, 0.02)
    data["offsets"] = [{"between": ["no", "010"], "db": 3.0}]
    data["links"] = [{"between": ["on", "null"], "loss_db": 120.0}]
    path = tmp_path / "words.yaml"
    nodeset.write(str(path), data)
    assert nodeset.read(str(path)) == data
    text = path.read_text()
    assert '\n  "no": { id: 1,' in text and '{ between: ["no", "010"], db: 3 }' in text


BARE = """\
nodes:
  1st-floor: { id: 1, lat: 0.01, lon: 0.02, height_m: 2, height_from: assumed, antenna: { type: whip_sma_quarter_wave }, tags: [] }
  08: { id: 2, lat: 0.011, lon: 0.021, height_m: 2, height_from: assumed, antenna: { type: whip_sma_quarter_wave }, tags: [] }
  nord: { id: 3, lat: 0.012, lon: 0.022, height_m: 2, height_from: assumed, antenna: { type: whip_sma_quarter_wave }, tags: [] }
offsets:
  - { between: [1st-floor, 08], db: 3 }
links:
  - { between: [nord, "08"], loss_db: 120 }
"""


def test_a_name_yaml_reads_as_itself_is_written_as_before(tmp_path):
    """Bare, as it always was, however it starts: only a name YAML would
    read as something else is quoted. (A link's ends were always written as
    any other string.)"""
    path = tmp_path / "bare.yaml"
    path.write_text(BARE)
    assert nodeset.dump(nodeset.read(str(path))) == BARE


def test_a_nodeset_is_inside_an_extent_when_one_node_is(nodesets_dir):
    (nodesets_dir / "ex.yaml").write_text(SAMPLE)
    ns = nodeset.load("ex")
    assert ns.inside([13.3, 52.4, 13.5, 52.6])
    assert not ns.inside([-0.1, -0.1, 0.1, 0.1])
    summary = nodeset.summary("ex")
    assert summary["nodes"] == 2 and summary["bbox"] == [13.4018, 52.5219, 13.4132, 52.5301]


def test_sites_csv_import(tmp_path):
    path = tmp_path / "sites.csv"
    path.write_text("# lat,lon,tx_power_dbm,surface_masl,cs_neighbours\n"
                    "52.520000,13.405000,14,40,3\n52.530000,13.415000,20,45,2\n")
    data = nodeset.import_sites_csv(str(path), height_m=12)
    assert list(data["nodes"]) == ["site-001", "site-002"]
    node = data["nodes"]["site-002"]
    assert (node["id"], node["lat"], node["height_m"], node["height_from"]) == (2, 52.53, 12, "assumed")
    # The file's transmit power is the node's maximum.
    assert node["max_dbm"] == 20
    nodeset.parse(data, "import")


def test_nodes_csv_import(tmp_path):
    path = tmp_path / "nodes.csv"
    path.write_text(
        "id,name,kind,lat,lon,height_agl_m,tx_power_dbm,last_seen_unix\n"
        "02be91,B Fhain | rePeaterParke,repeater,52.524699,13.448100,,,1790333077\n"
        "02d4aa,B Fhain | rePeaterParke,repeater,52.534400,13.403800,22,17,1790300671\n"
        "0367ab,☀,repeater,52.456598,13.512300,,,1790532335\n"
        "0400ff,nowhere,repeater,,,,,1\n")
    data = nodeset.import_nodes_csv(str(path), height_m=15)
    names = list(data["nodes"])
    assert names == ["b-fhain-repeaterparke", "b-fhain-repeaterparke-2", "n003"]
    first, second = (data["nodes"][n] for n in names[:2])
    assert (first["height_m"], first["height_from"]) == (15, "assumed")
    assert (second["height_m"], second["height_from"]) == (22, "measured")
    assert first["tags"] == ["repeater"] and first["antenna"]["type"] == "whip_sma_quarter_wave"
    assert "max_dbm" not in first
    assert second["max_dbm"] == 17
    nodeset.parse(data, "import")


def test_the_planners_own_deployed_network_csv_imports():
    path = os.path.join(HERE, "..", "..", "sergey", "planner", ".cache", "nodes", "berlin.csv")
    if not os.path.isfile(path):
        pytest.skip("no planner nodes CSV beside sim-mesh")
    data = nodeset.import_nodes_csv(path)
    assert len(data["nodes"]) > 100
    nodeset.parse(data, "import")


def test_the_repositorys_nodesets_all_read():
    for name in nodeset.names():
        nodeset.load(name)


def layer(*nodes, offsets=()):
    return {"nodes": {name: {"id": node_id, "lat": lat, "lon": lon, "tags": tags}
                      for name, node_id, lat, lon, tags in nodes},
            "offsets": [{"between": list(pair), "db": db} for pair, db in offsets]}


def test_one_nodeset_merges_as_itself():
    got = nodeset.merge([("town", layer(("a", 3, 52.5, 13.4, ["x"])))])
    assert list(got["nodes"]) == ["a"] and got["nodes"]["a"]["tags"] == ["x"]
    assert got["nodes"]["a"]["id"] == 3


def test_nodesets_merge_earliest_first():
    town = layer(("gw", 1, 52.5000, 13.4000, ["lxmf"]), ("hill", 2, 52.5100, 13.4100, []),
                 offsets=[(("gw", "hill"), 12)])
    # 3 m north of gw, another name for the same mast: the town's gw it is.
    meshcore = layer(("mast", 1, 52.500027, 13.4000, ["repeater"]),
                     ("gw", 2, 52.5200, 13.4200, []), ("far", 7, 52.5300, 13.4300, []),
                     offsets=[(("mast", "far"), 3), (("gw", "far"), 5)])
    got = nodeset.merge([("town", town), ("meshcore", meshcore)])
    nodes = got["nodes"]
    assert sorted(nodes) == ["far", "gw", "gw-meshcore", "hill"]
    assert nodes["gw"]["tags"] == ["lxmf", "town"]
    assert nodes["gw-meshcore"]["tags"] == ["meshcore"] and nodes["gw-meshcore"]["lat"] == 52.52
    # Ids 1 and 2 were the town's: meshcore's gw takes the lowest free, far keeps its 7.
    assert nodes["gw-meshcore"]["id"] == 3 and nodes["far"]["id"] == 7
    assert sorted(n["id"] for n in nodes.values()) == [1, 2, 3, 7]
    # The mast's offset went with the mast; the others came along, renamed.
    assert {(tuple(o["between"]), o["db"]) for o in got["offsets"]} == {
        (("gw", "hill"), 12), (("gw-meshcore", "far"), 5)}
    # Two nodes of one layer stay two, however close.
    same = layer(("a", 1, 52.5, 13.4, []), ("b", 2, 52.50001, 13.4, []))
    assert sorted(nodeset.merge([("one", same), ("two", layer())])["nodes"]) == ["a", "b"]
    # A name taken twice over gets a number too.
    got = nodeset.merge([("a", layer(("n", 1, 1, 1, []), ("n-b", 2, 2, 2, []))),
                         ("b", layer(("n", 1, 3, 3, [])))])
    assert sorted(got["nodes"]) == ["n", "n-b", "n-b-2"]


def test_a_link_comes_along_where_both_its_ends_do():
    town = dict(layer(("gw", 1, 0.0, 0.0, []), ("hill", 2, 0.01, 0.01, [])),
                links=[{"between": ["gw", "hill"], "loss_db": 120}])
    meshcore = dict(layer(("mast", 1, 0.000027, 0.0, []), ("gw", 2, 0.02, 0.02, []),
                          ("far", 7, 0.03, 0.03, [])),
                    links=[{"between": ["mast", "far"], "loss_db": 130},
                           {"between": ["gw", "far"], "loss_db": 125, "back_db": 127}])
    got = nodeset.merge([("town", town), ("meshcore", meshcore)])
    # The mast is the town's gw, so its link stays behind; the other is renamed.
    assert got["links"] == [{"between": ["gw", "hill"], "loss_db": 120},
                            {"between": ["gw-meshcore", "far"], "loss_db": 125, "back_db": 127}]
    nodeset.parse(got, "merged")
    plain = nodeset.merge([("a", layer(("n", 1, 1, 1, []))), ("b", layer(("m", 2, 2, 2, [])))])
    assert "links" not in plain


def test_a_delete_takes_the_nodesets_own_setup_with_it(nodesets_dir):
    (nodesets_dir / "ex.yaml").write_text(SAMPLE)
    (nodesets_dir / "ex.py").write_text("# its own setup\n")
    assert nodeset.load("ex").inside([13.3, 52.4, 13.5, 52.6]) == len(nodeset.load("ex").nodes)
    nodeset.delete("ex")
    assert nodeset.names() == [] and not (nodesets_dir / "ex.py").exists()
    with pytest.raises(store.StoreError, match="no nodeset"):
        nodeset.delete("ex")


def test_an_estimate_replaces_only_an_assumed_height_and_says_what_it_rested_on():
    data = nodeset.blank()
    for i, source in enumerate(("assumed", "assumed", "assumed", "measured", "roof", "assumed")):
        data["nodes"]["n%d" % i] = nodeset.node_record(i + 1, 52.5, 13.4 + i / 1000, 15.0, source)
    reply = {"h_agl_m": 24.04, "low_m": 22.0, "high_m": 26.0}
    estimates = {"n0": dict(reply, basis="lod2-building"),
                 "n1": dict(reply, basis="clutter-neighbourhood"),
                 "n2": dict(reply, basis="no-evidence"),
                 "n3": dict(reply, basis="lod2-building"),
                 "n4": dict(reply, basis="class-typical")}
    assert nodeset.with_estimated_heights(data, estimates) == ["n0", "n1"]
    nodes = data["nodes"]
    assert (nodes["n0"]["height_m"], nodes["n0"]["height_from"]) == (24.0, "roof")
    assert (nodes["n1"]["height_m"], nodes["n1"]["height_from"]) == (24.0, "raster")
    # Nothing to go on, a measured or a roof height, and no estimate at all:
    # each keeps its own.
    for name, source in (("n2", "assumed"), ("n3", "measured"), ("n4", "roof"), ("n5", "assumed")):
        assert (nodes[name]["height_m"], nodes[name]["height_from"]) == (15.0, source)


def test_any_csv_imports_by_the_columns_named():
    text = "#Label;Breite;Laenge;Mast;Leistung;Art\nTor A;52.5;13.4;12;30;router,gate\n" \
           "kein Ort;;;;;\nTor A;52.6;13.5;;;\n"
    data = nodeset.import_points("csv", text, 8.0, {"lat": "Breite", "lon": "Laenge",
                                                    "name": "Label", "height": "Mast",
                                                    "power": "Leistung", "tags": "Art"})
    assert list(data["nodes"]) == ["tor-a", "tor-a-2"]
    first, second = data["nodes"]["tor-a"], data["nodes"]["tor-a-2"]
    assert (first["height_m"], first["height_from"]) == (12.0, "measured")
    assert first["max_dbm"] == boards.FEM_MAX_DBM
    assert first["tags"] == ["csv", "router", "gate"]
    assert (second["height_m"], second["height_from"]) == (8.0, "assumed")
    with pytest.raises(store.StoreError, match="longitude"):
        nodeset.import_points("csv", text, 8.0, {"lat": "Breite"})
    with pytest.raises(store.StoreError, match="no column"):
        nodeset.import_points("csv", text, 8.0, {"lat": "Breite", "lon": "Lon"})


def test_geojson_kml_and_gpx_points_import():
    geojson = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.4, 52.5, 40]},
         "properties": {"name": "Mast 1", "height_m": 20, "tags": "hill, solar", "role": "repeater"}},
        {"type": "Feature", "geometry": {"type": "MultiPoint",
                                         "coordinates": [[13.41, 52.51], [13.42, 52.52]]},
         "properties": {}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}]})
    data = nodeset.import_points("geojson", geojson)
    assert list(data["nodes"]) == ["mast-1", "geojson-2", "geojson-3"]
    mast = data["nodes"]["mast-1"]
    # The third coordinate is above sea level, not the ground: the property is the height.
    assert (mast["height_m"], mast["height_from"]) == (20.0, "measured")
    assert mast["tags"] == ["geojson", "hill", "solar", "repeater"]

    kml = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>Roof</name><Point><altitudeMode>relativeToGround</altitudeMode>
        <coordinates>13.4,52.5,18</coordinates></Point></Placemark>
      <Placemark><name>Hill</name><Point><coordinates>13.5,52.6,90</coordinates></Point></Placemark>
    </Document></kml>"""
    data = nodeset.import_points("kml", kml, 5.0)
    assert (data["nodes"]["roof"]["height_m"], data["nodes"]["roof"]["height_from"]) == (18.0, "measured")
    assert (data["nodes"]["hill"]["height_m"], data["nodes"]["hill"]["height_from"]) == (5.0, "assumed")

    gpx = """<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
      <wpt lat="52.5" lon="13.4"><ele>60</ele><name>Base</name><type>Gateway</type></wpt></gpx>"""
    data = nodeset.import_points("gpx", gpx, 5.0)
    assert data["nodes"]["base"]["tags"] == ["gpx", "gateway"]
    assert data["nodes"]["base"]["height_from"] == "assumed"


def test_a_meshtastic_node_list_imports_from_the_info_text_or_the_json():
    nodes = {"!a1": {"user": {"longName": "Router Hill", "role": "ROUTER"},
                     "position": {"latitudeI": 525000000, "longitudeI": 134000000}},
             "!b2": {"user": {"shortName": "MOB"}, "position": {"latitude": 52.51, "longitude": 13.41}},
             "!c3": {"user": {"longName": "no fix"}, "position": {"latitudeI": 0, "longitudeI": 0}},
             "!d4": {"user": {"longName": "silent"}}}
    info = "Owner: me\nNodes in mesh: %s\n\nPreferences: {}\n" % json.dumps(nodes, indent=2)
    for text in (info, json.dumps(nodes), json.dumps(list(nodes.values()))):
        data = nodeset.import_points("meshtastic", text)
        assert list(data["nodes"]) == ["router-hill", "mob"]
        assert data["nodes"]["router-hill"]["lat"] == pytest.approx(52.5)
        assert data["nodes"]["router-hill"]["tags"] == ["meshtastic", "router"]
    with pytest.raises(store.StoreError, match="no point"):
        nodeset.import_points("meshtastic", json.dumps({"!c3": nodes["!c3"]}))


def test_a_measured_noise_rise_is_kept_and_none_is_no_key(tmp_path):
    """`noise_rise_db`: a node's place over its chip's floor, kept as read;
    a node that states none has no key, so its record is as it always was."""
    path = tmp_path / "f.yaml"
    path.write_text("nodes:\n  a: { id: 1, lat: 0, lon: 0, noise_rise_db: 11 }\n"
                    "  b: { id: 2, lat: 0, lon: 0.01 }\n")
    nodes = nodeset.read(str(path))["nodes"]
    assert nodes["a"]["noise_rise_db"] == 11.0
    assert "noise_rise_db" not in nodes["b"]
    # Written back, as a run's own copy is (runs.py), and read again: still there.
    again = tmp_path / "again.yaml"
    nodeset.write(str(again), nodeset.read(str(path)))
    assert nodeset.read(str(again))["nodes"]["a"]["noise_rise_db"] == 11.0
    assert "noise_rise_db" not in nodeset.read(str(again))["nodes"]["b"]
