// cesspool.lol verdict engine. Built into test_bitcoin from a Plumb tree so the
// verdicts come from the shipped policy code, not a reimplementation.
//
// Reads PLUMB_CHECK_IN: one transaction per line, "txid txhex spk:sats,..."
// (prevouts in input order). Writes PLUMB_CHECK_OUT: one JSON object per line
// with, for each of the core, knots and plumb profiles, every policy reason
// the transaction trips and the data bytes it counts, plus, under the plumb
// profile, the per-input and per-output data byte counts and the bytes each
// of Plumb's filters is responsible for ("fc": the count with that filter
// alone turned off, taken from the full count). For a transaction the knots
// or plumb profile relays while the other side sees data in it, "fix" says
// what a node on that profile could set to refuse it (see Fix below).
#include <chainparams.h>
#include <coins.h>
#include <common/args.h>
#include <core_io.h>
#include <init.h>
#include <kernel/mempool_options.h>
#include <node/mempool_args.h>
#include <policy/policy.h>
#include <policy/settings.h>
#include <primitives/transaction.h>
#include <script/script.h>
#include <test/util/setup_common.h>
#include <tinyformat.h>
#include <univalue.h>
#include <util/strencodings.h>
#include <util/string.h>

#include <boost/test/unit_test.hpp>

#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

namespace {

using Settings = std::vector<std::pair<std::string, std::string>>;

struct Profile {
    std::string name;
    Settings set;
    kernel::MemPoolOptions opts;
    unsigned int weight_per_data_byte;
    unsigned int script_size_limit;
    bool reject_dead_branches;
    bool reject_bare_envelopes;
    bool reject_fake_multisig;
    unsigned int bytes_per_sigop;
    unsigned int bytes_per_sigop_strict;
};

struct Globals {
    unsigned int weight_per_data_byte{::g_weight_per_data_byte};
    unsigned int script_size_limit{::g_script_size_policy_limit};
    bool reject_dead_branches{::g_reject_dead_branches};
    bool reject_bare_envelopes{::g_reject_bare_envelopes};
    bool reject_fake_multisig{::g_reject_fake_multisig};
    unsigned int bytes_per_sigop{::nBytesPerSigOp};
    unsigned int bytes_per_sigop_strict{::nBytesPerSigOpStrict};
};

// Same steps init.cpp takes: the -corepolicy soft-sets, then the mempool
// options and the policy globals.
Profile MakeProfile(const std::string& name, const Settings& set, const Globals& defaults)
{
    ArgsManager args;
    for (const auto& [k, v] : set) args.ForceSetArg(k, v);
    InitParameterInteraction(args);
    Profile p{name, set, kernel::MemPoolOptions{}, defaults.weight_per_data_byte, defaults.script_size_limit,
              defaults.reject_dead_branches, defaults.reject_bare_envelopes, defaults.reject_fake_multisig, defaults.bytes_per_sigop, defaults.bytes_per_sigop_strict};
    BOOST_REQUIRE(ApplyArgsManOptions(args, Params(), p.opts));
    if (auto parsed = args.GetFixedPointArg("-datacarriercost", 2)) {
        p.weight_per_data_byte = ((*parsed * WITNESS_SCALE_FACTOR) + 99) / 100;
    }
    p.script_size_limit = args.GetIntArg("-maxscriptsize", p.script_size_limit);
    p.reject_dead_branches = args.GetBoolArg("-rejectdeadbranches", p.reject_dead_branches);
    p.reject_bare_envelopes = args.GetBoolArg("-rejectbareenvelopes", p.reject_bare_envelopes);
    p.reject_fake_multisig = args.GetBoolArg("-rejectfakemultisig", p.reject_fake_multisig);
    p.bytes_per_sigop = args.GetIntArg("-bytespersigop", p.bytes_per_sigop);
    p.bytes_per_sigop_strict = args.GetIntArg("-bytespersigopstrict", p.bytes_per_sigop_strict);
    return p;
}

void Activate(const Profile& p)
{
    ::g_weight_per_data_byte = p.weight_per_data_byte;
    ::g_script_size_policy_limit = p.script_size_limit;
    ::g_reject_dead_branches = p.reject_dead_branches;
    ::g_reject_bare_envelopes = p.reject_bare_envelopes;
    ::g_reject_fake_multisig = p.reject_fake_multisig;
    ::nBytesPerSigOp = p.bytes_per_sigop;
    ::nBytesPerSigOpStrict = p.bytes_per_sigop_strict;
}

// Every reason a check trips: rerun it with each reason found so far ignored.
template <typename F>
void Collect(F check, std::vector<std::string>& reasons)
{
    ignore_rejects_type ignore;
    for (int i{0}; i < 64; ++i) {
        std::string reason;
        if (check(reason, ignore)) return;
        if (reason.empty() || ignore.count(reason)) {
            if (!reason.empty()) reasons.push_back(reason);
            return;
        }
        reasons.push_back(reason);
        ignore.insert(reason);
    }
}

// The datacarrier part of MemPoolAccept::PreChecks, same conditions.
void DatacarrierReasons(const CTransaction& tx, const CCoinsViewCache& view, const kernel::MemPoolOptions& o, std::vector<std::string>& reasons, std::pair<size_t, size_t>& dcb_out)
{
    std::vector<size_t> data_outputs(tx.vout.size(), 0);
    if (o.reject_fake_outputs && (o.datacarrier_fullcount || !o.accept_non_std_datacarrier || ::g_weight_per_data_byte > WITNESS_SCALE_FACTOR)) {
        data_outputs = DataOutputBytes(tx);
    }
    const auto dcb = DatacarrierBytes(tx, view, data_outputs);
    dcb_out = dcb;
    if (o.datacarrier_fullcount || !o.accept_non_std_datacarrier) {
        if (dcb.second > 0 && !o.accept_non_std_datacarrier) reasons.emplace_back("txn-datacarrier-nonstandard");
        if (o.datacarrier_fullcount && dcb.first + dcb.second > o.max_datacarrier_bytes.value_or(0)) reasons.emplace_back("txn-datacarrier-exceeded");
    }
}

// Every reason the profile's policy gives against the transaction, same checks as the node.
std::vector<std::string> Reasons(const Profile& p, const CTransaction& tx, const CCoinsViewCache& view, std::pair<size_t, size_t>& dcb)
{
    Activate(p);
    std::vector<std::string> reasons;
    Collect([&](std::string& r, const ignore_rejects_type& ig) { return IsStandardTx(tx, p.opts, r, ig); }, reasons);
    Collect([&](std::string& r, const ignore_rejects_type& ig) { return AreInputsStandard(tx, view, p.opts, "bad-txns-input-", r, ig); }, reasons);
    DatacarrierReasons(tx, view, p.opts, reasons, dcb);
    if (tx.HasWitness()) {
        Collect([&](std::string& r, const ignore_rejects_type& ig) { return IsWitnessStandard(tx, view, "bad-witness-", r, ig); }, reasons);
    }
    if (p.name == "core") std::erase(reasons, std::string{"multi-op-return"});
    return reasons;
}

// Below this, a -maxscriptsize refuses ordinary spends too (a 3-of-5 P2WSH witness is about
// 400 bytes), so a value under it is not offered as a way to refuse one transaction.
constexpr unsigned int MIN_USEFUL_SCRIPT_LIMIT{520};
// -dustrelayfee is searched up to this, in sat/kvB (the default is 3000).
constexpr int MAX_DUST_RATE{100'000};

// What a node on profile p could set to refuse a transaction p relays, each value found by
// rerunning the same checks with that one setting changed:
//   dcs   the largest -datacarriersize that refuses it (0: -datacarrier=0 does the same)
//   mss   the largest -maxscriptsize that refuses it, when that is MIN_USEFUL_SCRIPT_LIMIT or more
//   dust  the smallest -dustrelayfee, in sat/kvB, that refuses it, up to MAX_DUST_RATE, and
//   dusti the first output that rate makes dust and the rate below does not, so the page can name it
// A key is left out when no value of that setting in its range refuses the transaction.
UniValue Fix(const Profile& p, const CTransaction& tx, const CCoinsViewCache& view, size_t counted, const Globals& defaults)
{
    const auto refuses = [&](const std::string& key, const std::string& value) {
        Settings set{p.set};
        set.emplace_back(key, value);
        std::pair<size_t, size_t> unused;
        return !Reasons(MakeProfile(p.name, set, defaults), tx, view, unused).empty();
    };
    UniValue fix{UniValue::VOBJ};
    if (counted > 0 && refuses("-datacarriersize", util::ToString(counted - 1))) {
        fix.pushKV("dcs", uint64_t(counted - 1));
    }
    // Refusal is monotone in the limit: true below the largest script or witness size the
    // policy measures, false from it up. Find the largest refusing limit in the useful range.
    if (p.script_size_limit > MIN_USEFUL_SCRIPT_LIMIT && refuses("-maxscriptsize", util::ToString(MIN_USEFUL_SCRIPT_LIMIT))) {
        unsigned int lo{MIN_USEFUL_SCRIPT_LIMIT}, hi{p.script_size_limit - 1};
        while (lo < hi) {
            const unsigned int mid{lo + (hi - lo + 1) / 2};
            if (refuses("-maxscriptsize", util::ToString(mid))) lo = mid; else hi = mid - 1;
        }
        fix.pushKV("mss", uint64_t(lo));
    }
    // Monotone the other way: a higher dust rate refuses more. Find the smallest refusing rate.
    const auto rate = [](int sat_per_kvb) { return strprintf("0.%08d", sat_per_kvb); };
    const int base_rate{int(p.opts.dust_relay_feerate.GetFeePerK())};
    if (base_rate < MAX_DUST_RATE && refuses("-dustrelayfee", rate(MAX_DUST_RATE))) {
        int lo{base_rate + 1}, hi{MAX_DUST_RATE};
        while (lo < hi) {
            const int mid{lo + (hi - lo) / 2};
            if (refuses("-dustrelayfee", rate(mid))) hi = mid; else lo = mid + 1;
        }
        fix.pushKV("dust", lo);
        // The output that rate catches: dust at it and not one step below. A zero-value anchor
        // is dust at every rate and permitted, so the first dust output alone could name it.
        const CFeeRate at{CAmount{lo}}, below{CAmount{lo - 1}};
        for (size_t o{0}; o < tx.vout.size(); ++o) {
            if (IsDust(tx.vout[o], at) && !IsDust(tx.vout[o], below)) {
                fix.pushKV("dusti", uint64_t(o));
                break;
            }
        }
    }
    return fix;
}

UniValue Strings(const std::vector<std::string>& v)
{
    UniValue a{UniValue::VARR};
    for (const auto& s : v) a.push_back(s);
    return a;
}

} // namespace

BOOST_FIXTURE_TEST_SUITE(plumb_check_tests, BasicTestingSetup)

BOOST_AUTO_TEST_CASE(plumb_check)
{
    const char* in_path{std::getenv("PLUMB_CHECK_IN")};
    const char* out_path{std::getenv("PLUMB_CHECK_OUT")};
    if (!in_path || !out_path) return;

    const Globals defaults;
    std::vector<Profile> profiles;
    // Bitcoin Core 31 defaults. -corepolicy resets Knots to Core 29's policy; since then Core
    // enforces 2,500 legacy sigops a transaction (30) and relays more than one OP_RETURN output
    // (30), which Knots has no option for, so that reason is dropped below. Core 30's larger
    // datacarriersize cannot matter here: BIP110 caps OP_RETURN outputs at 83 bytes by consensus.
    profiles.push_back(MakeProfile("core", {{"-corepolicy", "1"}, {"-maxtxlegacysigops", "2500"}}, defaults));
    // Stock Knots 29.4.2: none of Plumb's filters exist there.
    profiles.push_back(MakeProfile("knots", {{"-rejectfakeoutputs", "0"}, {"-rejectdeadbranches", "0"}, {"-rejectbareenvelopes", "0"}, {"-rejectfakemultisig", "0"}, {"-rejecttokenmessages", "0"}}, defaults));
    profiles.push_back(MakeProfile("plumb", {}, defaults));
    // Plumb with one data-counting filter off at a time, to measure what each adds to the
    // count. -rejecttokenmessages needs no measurement: its reasons name it.
    std::vector<Profile> plumb_without;
    for (const char* option : {"-rejectfakeoutputs", "-rejectdeadbranches", "-rejectbareenvelopes", "-rejectfakemultisig"}) {
        plumb_without.push_back(MakeProfile(option, {{option, "0"}}, defaults));
    }
    const Profile restore{"", {}, {}, defaults.weight_per_data_byte, defaults.script_size_limit, defaults.reject_dead_branches,
                          defaults.reject_bare_envelopes, defaults.reject_fake_multisig, defaults.bytes_per_sigop, defaults.bytes_per_sigop_strict};

    std::ifstream in{in_path};
    std::ofstream out{out_path};
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream fields{line};
        std::string txid, hex, prevs;
        fields >> txid >> hex >> prevs;
        UniValue row{UniValue::VOBJ};
        row.pushKV("txid", txid);
        CMutableTransaction mtx;
        if (!DecodeHexTx(mtx, hex)) {
            row.pushKV("error", "decode");
            out << row.write() << "\n";
            continue;
        }
        const CTransaction tx{mtx};
        CCoinsView dummy;
        CCoinsViewCache view{&dummy};
        std::istringstream prev_stream{prevs};
        std::string prev;
        size_t i{0};
        while (std::getline(prev_stream, prev, ',') && i < tx.vin.size()) {
            const auto colon{prev.find(':')};
            const auto spk{ParseHex(prev.substr(0, colon))};
            const CAmount sats{std::stoll(prev.substr(colon + 1))};
            view.AddCoin(tx.vin[i].prevout, Coin{CTxOut{sats, CScript(spk.begin(), spk.end())}, 1, false}, true);
            ++i;
        }
        if (i != tx.vin.size()) {
            row.pushKV("error", "prevouts");
            out << row.write() << "\n";
            continue;
        }

        UniValue verdicts{UniValue::VOBJ};
        std::map<std::string, std::vector<std::string>> reasons_of;
        std::map<std::string, size_t> counted_of;
        for (const auto& p : profiles) {
            std::pair<size_t, size_t> dcb;
            const auto reasons{Reasons(p, tx, view, dcb)};
            reasons_of[p.name] = reasons;
            counted_of[p.name] = dcb.first + dcb.second;
            UniValue v{UniValue::VOBJ};
            v.pushKV("reasons", Strings(reasons));
            v.pushKV("data", uint64_t(dcb.first));
            v.pushKV("data_nonstd", uint64_t(dcb.second));
            verdicts.pushKV(p.name, v);

            if (p.name != "plumb") continue;
            // Where the plumb profile finds the data, same calls as DatacarrierBytes.
            const auto data_outputs{DataOutputBytes(tx)};
            UniValue ins{UniValue::VARR};
            size_t total{0};
            for (const CTxIn& txin : tx.vin) {
                const CTxOut& utxo = view.AccessCoin(txin.prevout).out;
                auto [script, wpb] = GetScriptForTransactionInput(utxo.scriptPubKey, txin);
                // The node skips the dead-branch analysis on a witness over the size limit
                const bool dead_branches{::g_reject_dead_branches && GetSerializeSize(txin.scriptWitness.stack) <= ::g_script_size_policy_limit};
                const auto d = script.DatacarrierBytes(0, &txin.scriptWitness, dead_branches, ::g_reject_bare_envelopes, ::g_reject_fake_multisig);
                ins.push_back(uint64_t(d.first + d.second));
                total += d.first + d.second;
            }
            UniValue outs{UniValue::VARR};
            for (size_t o{0}; o < tx.vout.size(); ++o) {
                const auto d = tx.vout[o].scriptPubKey.DatacarrierBytes(tx.vout.size() - o, nullptr, false, ::g_reject_bare_envelopes, ::g_reject_fake_multisig);
                const size_t n{d.first + std::max(d.second, data_outputs[o])};
                outs.push_back(uint64_t(n));
                total += n;
            }
            BOOST_CHECK_EQUAL(total, dcb.first + dcb.second);
            row.pushKV("data_in", ins);
            row.pushKV("data_out", outs);
            // Bytes each filter is responsible for; a filter that changes nothing is left out.
            UniValue fc{UniValue::VOBJ};
            for (const auto& q : plumb_without) {
                Activate(q);
                std::vector<std::string> unused;
                std::pair<size_t, size_t> without;
                DatacarrierReasons(tx, view, q.opts, unused, without);
                const int64_t diff{int64_t(total) - int64_t(without.first + without.second)};
                if (diff) fc.pushKV(q.name, diff);
            }
            row.pushKV("fc", fc);
        }
        // Settings that would refuse what a profile relays, for the transactions the site
        // shows: anything some profile refuses or counts data in. Clean transactions skip it.
        const bool shown{!reasons_of["plumb"].empty() || counted_of["plumb"] > 0 || counted_of["knots"] > 0};
        if (shown) {
            UniValue fix{UniValue::VOBJ};
            for (const auto& p : profiles) {
                if (p.name == "core" || !reasons_of[p.name].empty()) continue;
                fix.pushKV(p.name, Fix(p, tx, view, counted_of[p.name], defaults));
            }
            if (!fix.empty()) row.pushKV("fix", fix);
        }
        Activate(restore);
        row.pushKV("v", verdicts);
        out << row.write() << "\n";
    }
}

BOOST_AUTO_TEST_SUITE_END()
