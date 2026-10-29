// cesspool.lol verdict engine. Built into test_bitcoin from a Plumb tree so the
// verdicts come from the shipped policy code, not a reimplementation.
//
// Reads PLUMB_CHECK_IN: one transaction per line, "txid txhex spk:sats,..."
// (prevouts in input order). Writes PLUMB_CHECK_OUT: one JSON object per line
// with, for each of the core, knots and plumb profiles, every policy reason
// the transaction trips and the data bytes it counts, plus, under the plumb
// profile, the per-input and per-output data byte counts, the bytes the BPUB
// rule adds for the whole transaction ("data_bpub") and the bytes each
// data-counting rule is responsible for ("fc": the count with that rule
// alone turned off, taken from the full count; "knots#470" for the recount
// from verified keys). For a transaction the knots
// or plumb profile relays while the other side sees data in it, "fix" says
// what a node on that profile could set to refuse it (see Fix below). When
// Plumb's 42-byte -datacarriersize default changes its reasons, "p83" holds
// the reasons the plumb profile gives at Knots' 83. Under the plumb profiles
// the counts include knots#470's recount from the keys a signature verified
// against (see Count below); "script_error" is there when the policy script
// check fails, which no profile's reasons include.
#include <chainparams.h>
#include <coins.h>
#include <common/args.h>
#include <consensus/tx_verify.h>
#include <consensus/validation.h>
#include <core_io.h>
#include <deploymentstatus.h>
#include <init.h>
#include <kernel/mempool_options.h>
#include <node/mempool_args.h>
#include <policy/policy.h>
#include <policy/settings.h>
#include <primitives/transaction.h>
#include <script/interpreter.h>
#include <script/script.h>
#include <script/sigcache.h>
#include <sync.h>
#include <test/util/setup_common.h>
#include <tinyformat.h>
#include <univalue.h>
#include <util/strencodings.h>
#include <util/string.h>
#include <validation.h>

#include <boost/test/unit_test.hpp>

#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

// Defined in validation.cpp, which declares them in no header.
int64_t GetAccurateTransactionSigOpCost(const CTransaction& tx, const CCoinsViewCache& inputs, int flags);
bool CheckInputScripts(const CTransaction& tx, TxValidationState& state,
                       const CCoinsViewCache& inputs, unsigned int flags, bool cacheSigStore,
                       bool cacheFullScriptStore, PrecomputedTransactionData& txdata,
                       ValidationCache& validation_cache,
                       std::vector<CScriptCheck>* pvChecks,
                       const std::vector<unsigned int>& flags_per_input,
                       std::vector<VerifiedKeys>* verified_keys) EXCLUSIVE_LOCKS_REQUIRED(cs_main);

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
    bool reject_bpub;
    bool permit_ln_revoke_leaf;
    unsigned int bytes_per_sigop;
    unsigned int bytes_per_sigop_strict;
    // Whether the node carries knots#470 and recounts from the keys a signature verified against
    // (Plumb). Stock Knots 29.4.3 and Core do not.
    bool verified_key_credit;
};

struct Globals {
    unsigned int weight_per_data_byte{::g_weight_per_data_byte};
    unsigned int script_size_limit{::g_script_size_policy_limit};
    bool reject_dead_branches{::g_reject_dead_branches};
    bool reject_bare_envelopes{::g_reject_bare_envelopes};
    bool reject_fake_multisig{::g_reject_fake_multisig};
    bool reject_bpub{::g_reject_bpub};
    bool permit_ln_revoke_leaf{::g_permit_ln_revoke_leaf};
    unsigned int bytes_per_sigop{::nBytesPerSigOp};
    unsigned int bytes_per_sigop_strict{::nBytesPerSigOpStrict};
};

// Same steps init.cpp takes: the -corepolicy soft-sets, then the mempool
// options and the policy globals.
Profile MakeProfile(const std::string& name, const Settings& set, const Globals& defaults, bool verified_key_credit)
{
    ArgsManager args;
    for (const auto& [k, v] : set) args.ForceSetArg(k, v);
    InitParameterInteraction(args);
    Profile p{name, set, kernel::MemPoolOptions{}, defaults.weight_per_data_byte, defaults.script_size_limit,
              defaults.reject_dead_branches, defaults.reject_bare_envelopes, defaults.reject_fake_multisig, defaults.reject_bpub,
              defaults.permit_ln_revoke_leaf, defaults.bytes_per_sigop, defaults.bytes_per_sigop_strict, verified_key_credit};
    BOOST_REQUIRE(ApplyArgsManOptions(args, Params(), p.opts));
    if (auto parsed = args.GetFixedPointArg("-datacarriercost", 2)) {
        p.weight_per_data_byte = ((*parsed * WITNESS_SCALE_FACTOR) + 99) / 100;
    }
    p.script_size_limit = args.GetIntArg("-maxscriptsize", p.script_size_limit);
    p.reject_dead_branches = args.GetBoolArg("-rejectdeadbranches", p.reject_dead_branches);
    p.reject_bare_envelopes = args.GetBoolArg("-rejectbareenvelopes", p.reject_bare_envelopes);
    p.reject_fake_multisig = args.GetBoolArg("-rejectfakemultisig", p.reject_fake_multisig);
    p.reject_bpub = args.GetBoolArg("-rejectbpub", p.reject_bpub);
    p.permit_ln_revoke_leaf = args.GetBoolArg("-permitlnrevokeleaf", p.permit_ln_revoke_leaf);
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
    ::g_reject_bpub = p.reject_bpub;
    ::g_permit_ln_revoke_leaf = p.permit_ln_revoke_leaf;
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

// Outputs whose hash or key is data, as MemPoolAccept::PreChecks finds them for the datacarrier
// checks and the extra weight.
std::vector<size_t> DataOutputs(const CTransaction& tx, const CCoinsViewCache& view, const kernel::MemPoolOptions& o)
{
    std::vector<size_t> data_outputs(tx.vout.size(), 0);
    if (o.reject_fake_outputs && (o.datacarrier_fullcount || !o.accept_non_std_datacarrier || ::g_weight_per_data_byte > WITNESS_SCALE_FACTOR)) {
        data_outputs = DataOutputBytes(tx, &view);
    }
    return data_outputs;
}

// The keys a signature verified against in each input, recorded the way
// MemPoolAccept::PolicyScriptChecks records them for knots#470: CheckInputScripts with the policy
// script flags and one set per input, which wraps the signature checker in
// KeyRecordingSignatureChecker. The flags do not depend on any profile's settings, so the
// scripts of a transaction run once, the first time a count needs them.
class ScriptRun
{
    const CTransaction& m_tx;
    const CCoinsViewCache& m_view;
    ValidationCache& m_cache;
    bool m_ran{false};
    bool m_passed{false};
    std::vector<VerifiedKeys> m_keys;
    std::string m_error;

public:
    ScriptRun(const CTransaction& tx, const CCoinsViewCache& view, ValidationCache& cache) : m_tx{tx}, m_view{view}, m_cache{cache} {}

    // One set per input, or nullptr when a script fails: the node then refuses the transaction
    // before its recount, for a script reason none of the profiles' reasons include.
    const std::vector<VerifiedKeys>* Keys()
    {
        if (!m_ran) {
            m_ran = true;
            // PolicyScriptChecks' flags: STANDARD_SCRIPT_VERIFY_FLAGS, which PolicyScriptVerifyFlags
            // returns with no ignore_rejects (it sits in validation.cpp's anonymous namespace), and
            // what UnifiedSighashMempoolFlag adds wherever the fork is scheduled, as it is on mainnet.
            unsigned int flags{STANDARD_SCRIPT_VERIFY_FLAGS};
            if (DeploymentEnabled(Params().GetConsensus(), Consensus::DEPLOYMENT_BLAKE2B)) flags |= SCRIPT_VERIFY_UNIFIED_SIGHASH;
            TxValidationState state;
            PrecomputedTransactionData txdata;
            LOCK(cs_main);
            m_passed = CheckInputScripts(m_tx, state, m_view, flags, /*cacheSigStore=*/true, /*cacheFullScriptStore=*/false, txdata, m_cache, nullptr, {}, &m_keys);
            if (!m_passed) m_error = state.GetRejectReason();
        }
        return m_passed && m_keys.size() == m_tx.vin.size() ? &m_keys : nullptr;
    }

    // The policy script check's reject reason, when it ran and failed.
    const std::string& Error() const { return m_error; }
};

// The datacarrier bytes the node on the active profile ends with. MemPoolAccept::PreChecks counts
// from the scripts alone. With knots#470, which a Plumb profile carries, PolicyScriptChecks counts
// again once the scripts have run, with -rejectfakemultisig on and -datacarrierfullcount, crediting
// a key only when a signature verified against it, and that count is never the lower one.
// keys_used is the sets the count was taken with, or nullptr.
std::pair<size_t, size_t> Count(const Profile& p, const CTransaction& tx, const CCoinsViewCache& view, const std::vector<size_t>& data_outputs, ScriptRun& scripts, const std::vector<VerifiedKeys>*& keys_used)
{
    keys_used = nullptr;
    const auto dcb{DatacarrierBytes(tx, view, data_outputs)};
    if (!p.verified_key_credit || !::g_reject_fake_multisig || !p.opts.datacarrier_fullcount) return dcb;
    keys_used = scripts.Keys();
    return keys_used ? DatacarrierBytes(tx, view, data_outputs, keys_used) : dcb;
}

// The datacarrier part of MemPoolAccept::PreChecks, same conditions, with the count PolicyScriptChecks
// recounts where it does (Count above). The recount moves no bytes into the non-standard member,
// so the nonstandard reason stays PreChecks' own.
void DatacarrierReasons(const Profile& p, const CTransaction& tx, const CCoinsViewCache& view, ScriptRun& scripts, std::vector<std::string>& reasons, std::pair<size_t, size_t>& dcb_out, const std::vector<VerifiedKeys>*& keys_used)
{
    const kernel::MemPoolOptions& o{p.opts};
    const auto dcb = Count(p, tx, view, DataOutputs(tx, view, o), scripts, keys_used);
    dcb_out = dcb;
    if (o.datacarrier_fullcount || !o.accept_non_std_datacarrier) {
        if (dcb.second > 0 && !o.accept_non_std_datacarrier) reasons.emplace_back("txn-datacarrier-nonstandard");
        if (o.datacarrier_fullcount && dcb.first + dcb.second > o.max_datacarrier_bytes.value_or(0)) reasons.emplace_back("txn-datacarrier-exceeded");
    }
}

// The sigops part of MemPoolAccept::PreChecks, same conditions. The size is the mempool entry's:
// the weight plus the extra weight -datacarriercost puts on data, raised for sigops at
// -bytespersigop. -bytespersigopstrict refuses a transaction with fewer than that many weight
// units for each sigop, counting a multisig's keys and leaving out its outputs.
void SigopReasons(const CTransaction& tx, const CCoinsViewCache& view, const kernel::MemPoolOptions& o, std::vector<std::string>& reasons)
{
    const int64_t sigops{GetTransactionSigOpCost(tx, view, STANDARD_SCRIPT_VERIFY_FLAGS)};
    const int32_t extra_weight{CalculateExtraTxWeight(tx, view, ::g_weight_per_data_byte, DataOutputs(tx, view, o))};
    const int64_t vsize{GetVirtualTransactionSize(GetTransactionWeight(tx) + extra_weight, sigops, ::nBytesPerSigOp)};
    if (sigops > MAX_STANDARD_TX_SIGOPS_COST || (::nBytesPerSigOpStrict && GetAccurateTransactionSigOpCost(tx, view, STANDARD_SCRIPT_VERIFY_FLAGS) > vsize * WITNESS_SCALE_FACTOR / ::nBytesPerSigOpStrict)) {
        reasons.emplace_back("bad-txns-too-many-sigops");
    }
}

// Every reason the profile's policy gives against the transaction, same checks as the node.
std::vector<std::string> Reasons(const Profile& p, const CTransaction& tx, const CCoinsViewCache& view, ScriptRun& scripts, std::pair<size_t, size_t>& dcb)
{
    Activate(p);
    std::vector<std::string> reasons;
    Collect([&](std::string& r, const ignore_rejects_type& ig) { return IsStandardTx(tx, p.opts, r, ig); }, reasons);
    Collect([&](std::string& r, const ignore_rejects_type& ig) { return AreInputsStandard(tx, view, p.opts, "bad-txns-input-", r, ig); }, reasons);
    const std::vector<VerifiedKeys>* unused;
    DatacarrierReasons(p, tx, view, scripts, reasons, dcb, unused);
    if (tx.HasWitness()) {
        Collect([&](std::string& r, const ignore_rejects_type& ig) { return IsWitnessStandard(tx, view, "bad-witness-", r, ig); }, reasons);
    }
    SigopReasons(tx, view, p.opts, reasons);
    if (p.name == "core") std::erase(reasons, std::string{"multi-op-return"});
    return reasons;
}

// Below this, a -maxscriptsize refuses ordinary spends too (a 3-of-5 P2WSH witness is about
// 400 bytes), so a value under it is not offered as a way to refuse one transaction.
constexpr unsigned int MIN_USEFUL_SCRIPT_LIMIT{520};
// Above this, -bytespersigopstrict starts refusing segwit 2-of-15 spends (a 2-of-15 P2WSH input has
// about 55.2 weight units a sigop; more keys or fewer signatures fall lower). Not offered above it.
constexpr unsigned int MAX_USEFUL_SIGOP_STRICT{55};
// -dustrelayfee is searched up to this, in sat/kvB (the default is 3000).
constexpr int MAX_DUST_RATE{100'000};

// What a node on profile p could set to refuse a transaction p relays, each value found by
// rerunning the same checks with that one setting changed:
//   dcs   the largest -datacarriersize that refuses it (0: -datacarrier=0 does the same)
//   mss   the largest -maxscriptsize that refuses it, when that is MIN_USEFUL_SCRIPT_LIMIT or more
//   bpss  the smallest -bytespersigopstrict that refuses it, up to MAX_USEFUL_SIGOP_STRICT
//   dust  the smallest -dustrelayfee, in sat/kvB, that refuses it, up to MAX_DUST_RATE, and
//   dusti the first output that rate makes dust and the rate below does not, so the page can name it
// A key is left out when no value of that setting in its range refuses the transaction.
UniValue Fix(const Profile& p, const CTransaction& tx, const CCoinsViewCache& view, ScriptRun& scripts, size_t counted, const Globals& defaults)
{
    const auto refuses = [&](const std::string& key, const std::string& value) {
        Settings set{p.set};
        set.emplace_back(key, value);
        std::pair<size_t, size_t> unused;
        return !Reasons(MakeProfile(p.name, set, defaults, p.verified_key_credit), tx, view, scripts, unused).empty();
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
    // Monotone the other way: a higher -bytespersigopstrict asks more weight of each sigop.
    // Find the smallest refusing value in the useful range.
    if (p.bytes_per_sigop_strict < MAX_USEFUL_SIGOP_STRICT && refuses("-bytespersigopstrict", util::ToString(MAX_USEFUL_SIGOP_STRICT))) {
        unsigned int lo{p.bytes_per_sigop_strict + 1}, hi{MAX_USEFUL_SIGOP_STRICT};
        while (lo < hi) {
            const unsigned int mid{lo + (hi - lo) / 2};
            if (refuses("-bytespersigopstrict", util::ToString(mid))) hi = mid; else lo = mid + 1;
        }
        fix.pushKV("bpss", uint64_t(lo));
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
    // Core has no knots#487 either: -permitlnrevokeleaf=0 keeps the 34 bytes of LND's revocation
    // leaf where 29.4.3 books them. -corepolicy skips the data carrier check, so no verdict moves.
    profiles.push_back(MakeProfile("core", {{"-corepolicy", "1"}, {"-maxtxlegacysigops", "2500"}, {"-permitlnrevokeleaf", "0"}}, defaults, false));
    // Stock Knots 29.4.3: it carries the dead-branch, bare-envelope, unproven-key and BPUB rules
    // itself (knots#400, #319, #422, #463), so only Plumb's two own filters are off, and
    // -datacarriersize defaults to 83 where Plumb's is 42. It has none of knots#470, #487 and
    // #489: no recount from verified keys (the count is PreChecks' alone, which a null set of
    // verified keys leaves as 29.4.3 has it), LND's revocation leaf refused as non-standard data
    // (-permitlnrevokeleaf=0), and -rejecttokens without the JSON and Omni checks
    // (-rejecttokenmessages=0).
    profiles.push_back(MakeProfile("knots", {{"-rejectfakeoutputs", "0"}, {"-rejecttokenmessages", "0"}, {"-permitlnrevokeleaf", "0"}, {"-datacarriersize", "83"}}, defaults, false));
    profiles.push_back(MakeProfile("plumb", {}, defaults, true));
    // Plumb with Knots' -datacarriersize, to tell what the 42-byte default refuses on its own.
    const Profile plumb83{MakeProfile("plumb", {{"-datacarriersize", "83"}}, defaults, true)};
    // Plumb with one data-counting rule off at a time, to measure what each adds to the
    // count. -rejecttokenmessages needs no measurement: its reasons name it. -permitlnrevokeleaf
    // moves the leaf's bytes between the two members and changes no count.
    std::vector<Profile> plumb_without;
    for (const char* option : {"-rejectfakeoutputs", "-rejectdeadbranches", "-rejectbareenvelopes", "-rejectfakemultisig", "-rejectbpub"}) {
        plumb_without.push_back(MakeProfile(option, {{option, "0"}}, defaults, true));
    }
    // And knots#470's recount left out alone, -rejectfakemultisig still on: the bytes Plumb counts
    // from the verified keys beyond what PreChecks counted, which Knots 29.4.3 never counts. The
    // node has no switch for the recount alone, so "fc" names it by its pull request.
    plumb_without.push_back(MakeProfile("knots#470", {}, defaults, false));
    const Profile restore{"", {}, {}, defaults.weight_per_data_byte, defaults.script_size_limit, defaults.reject_dead_branches,
                          defaults.reject_bare_envelopes, defaults.reject_fake_multisig, defaults.reject_bpub, defaults.permit_ln_revoke_leaf,
                          defaults.bytes_per_sigop, defaults.bytes_per_sigop_strict, false};
    // Signatures and scripts for ScriptRun, sized like the node's defaults.
    ValidationCache validation_cache{DEFAULT_SCRIPT_EXECUTION_CACHE_BYTES, DEFAULT_SIGNATURE_CACHE_BYTES};

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

        ScriptRun scripts{tx, view, validation_cache};
        UniValue verdicts{UniValue::VOBJ};
        std::map<std::string, std::vector<std::string>> reasons_of;
        std::map<std::string, size_t> counted_of;
        for (const auto& p : profiles) {
            std::pair<size_t, size_t> dcb;
            const auto reasons{Reasons(p, tx, view, scripts, dcb)};
            reasons_of[p.name] = reasons;
            counted_of[p.name] = dcb.first + dcb.second;
            UniValue v{UniValue::VOBJ};
            v.pushKV("reasons", Strings(reasons));
            v.pushKV("data", uint64_t(dcb.first));
            v.pushKV("data_nonstd", uint64_t(dcb.second));
            verdicts.pushKV(p.name, v);

            if (p.name != "plumb") continue;
            // Where the plumb profile finds the data, same calls as DatacarrierBytes, with the
            // verified keys its count was taken with.
            const auto data_outputs{DataOutputBytes(tx, &view)};
            const std::vector<VerifiedKeys>* keys;
            Count(p, tx, view, data_outputs, scripts, keys);
            UniValue ins{UniValue::VARR};
            size_t total{0};
            for (size_t n{0}; n < tx.vin.size(); ++n) {
                const CTxIn& txin{tx.vin[n]};
                const CTxOut& utxo = view.AccessCoin(txin.prevout).out;
                auto [script, wpb] = GetScriptForTransactionInput(utxo.scriptPubKey, txin);
                // The node skips the dead-branch analysis on a witness over the size limit
                const bool dead_branches{::g_reject_dead_branches && GetSerializeSize(txin.scriptWitness.stack) <= ::g_script_size_policy_limit};
                // It recounts from the verified keys only an input whose script came from the witness
                const VerifiedKeys* input_keys{(keys && wpb == 1) ? &(*keys)[n] : nullptr};
                const auto d = script.DatacarrierBytes(0, &txin.scriptWitness, dead_branches, input_keys, ::g_reject_bare_envelopes, ::g_reject_fake_multisig, ::g_permit_ln_revoke_leaf);
                ins.push_back(uint64_t(d.first + d.second));
                total += d.first + d.second;
            }
            UniValue outs{UniValue::VARR};
            for (size_t o{0}; o < tx.vout.size(); ++o) {
                const auto d = tx.vout[o].scriptPubKey.DatacarrierBytes(tx.vout.size() - o, nullptr, false, nullptr, ::g_reject_bare_envelopes, ::g_reject_fake_multisig);
                const size_t n{d.first + std::max(d.second, data_outputs[o])};
                outs.push_back(uint64_t(n));
                total += n;
            }
            // The BPUB rule counts the witness scripts of a file's inputs for the whole transaction,
            // outside the per-script counts (policy.cpp DatacarrierBytes), so it is its own item.
            const size_t bpub{::g_reject_bpub ? BPUBBytes(tx) : 0};
            total += bpub;
            BOOST_CHECK_EQUAL(total, dcb.first + dcb.second);
            row.pushKV("data_in", ins);
            row.pushKV("data_out", outs);
            if (bpub) row.pushKV("data_bpub", uint64_t(bpub));
            // Bytes each filter is responsible for; a filter that changes nothing is left out.
            UniValue fc{UniValue::VOBJ};
            for (const auto& q : plumb_without) {
                Activate(q);
                std::vector<std::string> unused;
                std::pair<size_t, size_t> without;
                const std::vector<VerifiedKeys>* unused_keys;
                DatacarrierReasons(q, tx, view, scripts, unused, without, unused_keys);
                const int64_t diff{int64_t(total) - int64_t(without.first + without.second)};
                if (diff) fc.pushKV(q.name, diff);
            }
            row.pushKV("fc", fc);
            if (!reasons.empty()) {
                std::pair<size_t, size_t> unused;
                const auto at83{Reasons(plumb83, tx, view, scripts, unused)};
                if (at83 != reasons) row.pushKV("p83", Strings(at83));
            }
        }
        // Settings that would refuse what a profile relays, for the transactions the site
        // shows: anything some profile refuses or counts data in. Clean transactions skip it.
        const bool shown{!reasons_of["plumb"].empty() || counted_of["plumb"] > 0 || counted_of["knots"] > 0};
        if (shown) {
            UniValue fix{UniValue::VOBJ};
            for (const auto& p : profiles) {
                if (p.name == "core" || !reasons_of[p.name].empty()) continue;
                fix.pushKV(p.name, Fix(p, tx, view, scripts, counted_of[p.name], defaults));
            }
            if (!fix.empty()) row.pushKV("fix", fix);
        }
        Activate(restore);
        if (!scripts.Error().empty()) row.pushKV("script_error", scripts.Error());
        row.pushKV("v", verdicts);
        out << row.write() << "\n";
    }
}

BOOST_AUTO_TEST_SUITE_END()
