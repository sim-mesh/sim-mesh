'''t185-smoke: an online map of stationary nodes, a greedy path through it,
and an LXMF message sent along the resolved routes (CMD_LXMF_SEND_ROUTE),
self-checked against the run's own record that the named relays carried it.

    sim run t185-smoke --geodata t185-smoke --nodeset t185-smoke \\
        --set firmware=<name>
'''
from sim_mesh import *

firmware = script_input("firmware", type=Firmware, category="reticulum",
                        label="Firmware for every node (t184/route-trailer)")
sim_speed("max")
nodes().firmware(firmware)
script_include("scripts/startup.py")
nodes().on_first_boot(Node.reticulum.lxmf.create())

nodes().up()
nodes().reticulum.lxmf.announce(spread=5)
sim_wait(45)                      # the chain's own announces propagate hop by hop

SENDER, DEST = "n01", "n05"
facts = nodes().facts()
sender_pos = (facts[SENDER]["lat"], facts[SENDER]["lon"])
dest_pos = (facts[DEST]["lat"], facts[DEST]["lon"])

the_map = online_map(for_station=SENDER)
print("online map for %s: %r" % (SENDER, the_map))
if the_map is None:
    raise ScriptError("%s may not read the online map (not tagged gateway/internet, "
                      "not .reticulum.user_configured())" % SENDER)

routes = build_routes(sender_pos, dest_pos, the_map, range_m=2000.0, alternatives=2)
print("routes by name: %r" % [[hop["name"] for hop in route] for route in routes])
tags = route_tags(routes)

sent = node(SENDER).reticulum.lxmf.send_route(DEST, tags, "hello via the stored chain")
print("send_route: %r" % sent)

sim_wait(60)                      # the relays transmit, the proof comes back
sim_snapshot("t185-smoke-end")
sim_stop()
