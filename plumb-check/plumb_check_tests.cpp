// cesspool.lol verdict engine. Built into test_bitcoin from a Plumb tree so the
// verdicts come from the shipped policy code, not a reimplementation.
//
// Reads PLUMB_CHECK_IN: one transaction per line, "txid txhex spk:sats,..."
// (prevouts in input order). Writes PLUMB_CHECK_OUT: one JSON object per line
// with, for each of the core, knots and plumb profiles, every policy reason
// the transaction trips, plus the per-input and per-output data byte counts
// under the plumb profile.
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
#include <univalue.h>
#include <util/strencodings.h>

#include <boost/test/unit_test.hpp>

#include <cstdlib>
#include <fstream>
#include <sstream>

namespace {

struct Profile {
    std::string name;
    kernel::MemPoolOptions opts;
    unsigned int weight_per_data_byte;
    unsigned int script_size_limit;
    bool reject_dead_branches;
    unsigned int bytes_per_sigop;
    unsigned int bytes_per_sigop_strict;
};

struct Globals {
    unsigned int weight_per_data_byte{::g_weight_per_data_byte};
    unsigned int script_size_limit{::g_script_size_policy_limit};
    bool reject_dead_branches{::g_reject_dead_branches};
    unsigned int bytes_per_sigop{::nBytesPerSigOp};
    unsigned int bytes_per_sigop_strict{::nBytesPerSigOpStrict};
};

// Same steps init.cpp takes: the -corepolicy soft-sets, then the mempool
// options and the policy globals.
Profile MakeProfile(const std::string& name, const std::vector<std::pair<std::string, std::string>>& set, const Globals& defaults)
{
    ArgsManager args;
    for (const auto& [k, v] : set) args.ForceSetArg(k, v);
    InitParameterInteraction(args);
    Profile p{name, kernel::MemPoolOptions{}, defaults.weight_per_data_byte, defaults.script_size_limit,
              defaults.reject_dead_branches, defaults.bytes_per_sigop, defaults.bytes_per_sigop_strict};
    BOOST_REQUIRE(ApplyArgsManOptions(args, Params(), p.opts));
    if (auto parsed = args.GetFixedPointArg("-datacarriercost", 2)) {
        p.weight_per_data_byte = ((*parsed * WITNESS_SCALE_FACTOR) + 99) / 100;
    }
    p.script_size_limit = args.GetIntArg("-maxscriptsize", p.script_size_limit);
    p.reject_dead_branches = args.GetBoolArg("-rejectdeadbranches", p.reject_dead_branches);
    p.bytes_per_sigop = args.GetIntArg("-bytespersigop", p.bytes_per_sigop);
    p.bytes_per_sigop_strict = args.GetIntArg("-bytespersigopstrict", p.bytes_per_sigop_strict);
    return p;
}

void Activate(const Profile& p)
{
    ::g_weight_per_data_byte = p.weight_per_data_byte;
    ::g_script_size_policy_limit = p.script_size_limit;
    ::g_reject_dead_branches = p.reject_dead_branches;
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
    profiles.push_back(MakeProfile("core", {{"-corepolicy", "1"}}, defaults));
    // Stock Knots 29.4.2: Plumb's two filters do not exist there.
    profiles.push_back(MakeProfile("knots", {{"-rejectfakeoutputs", "0"}, {"-rejectdeadbranches", "0"}}, defaults));
    profiles.push_back(MakeProfile("plumb", {}, defaults));

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
        for (const auto& p : profiles) {
            Activate(p);
            std::vector<std::string> reasons;
            Collect([&](std::string& r, const ignore_rejects_type& ig) { return IsStandardTx(tx, p.opts, r, ig); }, reasons);
            Collect([&](std::string& r, const ignore_rejects_type& ig) { return AreInputsStandard(tx, view, p.opts, "bad-txns-input-", r, ig); }, reasons);
            std::pair<size_t, size_t> dcb;
            DatacarrierReasons(tx, view, p.opts, reasons, dcb);
            if (tx.HasWitness()) {
                Collect([&](std::string& r, const ignore_rejects_type& ig) { return IsWitnessStandard(tx, view, "bad-witness-", r, ig); }, reasons);
            }
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
                const auto d = script.DatacarrierBytes(0, &txin.scriptWitness, ::g_reject_dead_branches);
                ins.push_back(uint64_t(d.first + d.second));
                total += d.first + d.second;
            }
            UniValue outs{UniValue::VARR};
            for (size_t o{0}; o < tx.vout.size(); ++o) {
                const auto d = tx.vout[o].scriptPubKey.DatacarrierBytes(tx.vout.size() - o);
                const size_t n{d.first + std::max(d.second, data_outputs[o])};
                outs.push_back(uint64_t(n));
                total += n;
            }
            BOOST_CHECK_EQUAL(total, dcb.first + dcb.second);
            row.pushKV("data_in", ins);
            row.pushKV("data_out", outs);
        }
        Activate({"", {}, defaults.weight_per_data_byte, defaults.script_size_limit, defaults.reject_dead_branches,
                  defaults.bytes_per_sigop, defaults.bytes_per_sigop_strict});
        row.pushKV("v", verdicts);
        out << row.write() << "\n";
    }
}

BOOST_AUTO_TEST_SUITE_END()
