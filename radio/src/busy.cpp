/**
 * busy — SIM_MESH_BOARD's BUSY figures for one chip, and its channel check's
 * false busy (busy.h).
 */
#include "busy.h"

#include "json.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>

/* The entries of SIM_MESH_BOARD's `key` that are `chip`'s, each `<name>:<value>`:
 * a bare name is the board's own chip's (`own`), a qualified one the chip it
 * names. `take` reads one and says whether its value was what `form` promises;
 * an entry that is not is said on stderr and left out. */
static void ownEntries(const simradio_json::Object& board, const char* key,
                       const std::string& own, const char* chip, const char* form,
                       const std::function<bool(const std::string&, const char*)>& take)
{
    const std::string spec = board.str(key, "");
    size_t at = 0;
    while (at < spec.size()) {
        size_t end = spec.find(',', at);
        if (end == std::string::npos) end = spec.size();
        const std::string entry = spec.substr(at, end - at);
        at = end + 1;
        char name[48] = "", value[32] = "";
        bool ok = sscanf(entry.c_str(), " %47[^: ] : %31s", name, value) == 2;
        std::string what = name;
        std::string whose = own;
        const size_t dot = what.find('.');
        if (ok && dot != std::string::npos) {
            whose = what.substr(0, dot);
            what = what.substr(dot + 1);
        }
        if (ok && whose != chip) continue;
        if (!ok || !take(what, value)) {
            fprintf(stderr, "simradio: SIM_MESH_BOARD %s entry \"%s\" is not %s; ignored\n",
                    key, entry.c_str(), form);
        }
    }
}

static bool readBoard(simradio_json::Object& board, std::string& own)
{
    const char* env = getenv("SIM_MESH_BOARD");
    if (!env || !board.parse(env, strlen(env))) return false;
    own = board.str("chip", "sx1262");
    return true;
}

BoardBusy readBoardBusy(const char* chip)
{
    BoardBusy b;
    simradio_json::Object board;
    std::string own;
    if (!readBoard(board, own)) return b;
    if (own == chip) {           /* the board's own, measured with its chip */
        b.tcxo = board.num("busy_tcxo", 0) != 0;
        b.hostUs = (int32_t)std::max<int64_t>(0, board.num("host_us", 0));
    }
    ownEntries(board, "busy_us", own, chip, "[<chip>.]<command>:<µs>",
               [&](const std::string& command, const char* value) {
                   char* end = nullptr;
                   const long us = strtol(value, &end, 10);
                   if (end == value || *end || us < 0) return false;
                   b.entries.push_back({command, (int32_t)us});
                   return true;
               });
    return b;
}

double BoardFalseBusy::at(int sf) const
{
    for (const auto& entry : bySf)
        if (entry.first == sf) return entry.second;
    return 0.0;
}

BoardFalseBusy readBoardFalseBusy(const char* chip)
{
    BoardFalseBusy b;
    simradio_json::Object board;
    std::string own;
    if (!readBoard(board, own)) return b;
    ownEntries(board, "cad_false_busy", own, chip, "[<chip>.]<sf>:<share>",
               [&](const std::string& sf, const char* value) {
                   char* end = nullptr;
                   const long which = strtol(sf.c_str(), &end, 10);
                   if (end == sf.c_str() || *end || which < 5 || which > 12) return false;
                   const double share = strtod(value, &end);
                   if (end == value || *end || share < 0.0 || share > 1.0) return false;
                   b.bySf.push_back({(int)which, share});
                   return true;
               });
    return b;
}

/* FNV-1a over a string, then a mix of the high bits (splitmix64's finalizer):
 * keys that differ in their last digits must not draw alike. */
static uint64_t fnv(uint64_t h, const char* s)
{
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211ULL; }
    return h;
}

static uint64_t mixed(uint64_t h)
{
    h ^= h >> 33; h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33; h *= 0xc4ceb9fe1a85ec53ULL;
    return h ^ (h >> 33);
}

bool falseBusyLook(const BoardFalseBusy& b, int sf, int slot, int64_t startUs)
{
    const double share = b.at(sf);
    if (share <= 0.0) return false;
    const char* seed = getenv("SIM_MESH_SEED");
    const char* id = getenv("SIM_MESH_NODE_ID");
    char look[64];
    snprintf(look, sizeof look, "/%d/%lld", slot, (long long)startUs);
    uint64_t h = fnv(1469598103934665603ULL, seed ? seed : "");
    h = fnv(fnv(h, "/"), id ? id : "");
    h = mixed(fnv(h, look));
    return (double)(h >> 11) / 9007199254740992.0 < share;
}
