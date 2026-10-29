"""The field guide: every shape the classifier names.

`filter` says what is supposed to stop it. The site checks that against the
verdicts it actually recorded and shows the measured catch rate beside it, so
a wrong entry here shows up as a contradiction on the page.
"""

KNOTS = "Knots default"
PLUMB = "Plumb"
KNOTS_SINCE = "29.4.3"  # the Knots release that took in knots#319, #400, #422 and #463; "knots_since" marks the kinds it made Knots defaults
NONE = "No filter yet"
ALLOWED = "Allowed"
NOT_SPAM = "Not spam"
DATACARRIER = "-datacarriersize=42"  # Plumb's default since plumb7; Knots' is 83

TYPES = {
    "fake-multisig": {
        "name": "Fake multisig keys",
        "what": "Spends a multisig script with eight or more public keys that no signature needs. Only the signing keys are real; the rest are file bytes dressed as keys.",
        "how": "Each unused 33-byte key in the revealed witness script is payload. One reveal can carry hundreds of inputs.",
        "filter": KNOTS, "knots_since": KNOTS_SINCE, "option": "-rejectfakemultisig",
        "prs": [("knots#422", "https://github.com/bitcoinknots/bitcoin/pull/422")],
        "note": "Knots 29.4.3 and Plumb count each unsigned key past ten in a script as data, and Knots 29.4.3 also refuses the BPUB file format outright (knots#463). A reveal with ten or fewer unsigned keys in each script gets through. Plumb keeps the switch; Knots has none. Since plumb9 Plumb also counts a key in a witness script as signed only when a signature in the spend verified against it (knots#470), so a signature check that never runs, or fails on an empty signature, proves nothing; Knots 29.4.3 credits a key when a signature check sits beside it, up to the number of signatures the witness carries.",
    },
    "p2wsh-run": {
        "name": "Dust P2WSH run",
        "what": "Pays 546 sats or less to each output in a run of P2WSH outputs. Each output commits to a witness script that is revealed when the output is spent.",
        "how": "The payload is not in this transaction: it lands on chain when the outputs are spent, in the scripts revealed then, mostly as unused multisig keys. Plumb counts each output in the run as data and refuses the transaction; Knots relays it.",
        "staged": True,
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "olga": {
        "name": "OLGA / Stamps in P2WSH",
        "what": "The Stamps OLGA framing: a length prefix and the `stamp:` tag, then the payload in consecutive P2WSH output hashes.",
        "how": "Knots refuses the `stamp:` tag with `-rejecttokens`, and its data-carrier count books the framed outputs as data outside OP_RETURN, so either rule alone stops it. Plumb inherits both.",
        "filter": KNOTS, "option": "-rejecttokens",
    },
    "olga-acme": {
        "name": "ACME in P2WSH",
        "what": "The OLGA framing with the tag changed to `ACME`: a length prefix, then the payload in consecutive P2WSH output hashes.",
        "how": "Four changed bytes walk past Knots' `stamp:` check, so stock Knots relays it. Plumb's output filter counts the outputs: three or more P2WSH outputs sharing one dust value, or a hash that reads as data, mark the whole run.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "swap-memo-hash": {
        "name": "Swap memo in P2WPKH hashes",
        "what": "A cross-chain swap instruction too long for OP_RETURN, continued as text inside 20-byte P2WPKH output hashes.",
        "how": "The hash fields read as ASCII. The outputs are unspendable.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "fake-hash": {
        "name": "Data in P2WPKH hashes",
        "what": "P2WPKH outputs whose 20-byte hashes are payload rather than a key hash.",
        "how": "Unspendable outputs that persist in the UTXO set.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "taproot-text": {
        "name": "Text in taproot keys",
        "what": "Taproot outputs whose 32-byte keys are readable text, such as the \"DECODE WITNESS\" series.",
        "how": "A taproot key has to be a point on the curve. Text almost never is, so the coins are burned.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "taproot-offcurve": {
        "name": "Off-curve taproot keys",
        "what": "Taproot outputs whose keys are not valid curve points.",
        "how": "Nobody can ever spend them; the 32 bytes are payload.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "taproot-data": {
        "name": "Data in taproot keys",
        "what": "Taproot output keys Plumb's statistical tests mark as payload.",
        "how": "Byte runs and repeats that a real key shows about once in ten billion.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "fake-output": {
        "name": "Data in output scripts",
        "what": "An output script Plumb counts as data that the field guide has no name for yet.",
        "how": "Unnamed shape. If you know what it is, open an issue.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "dead-branch": {
        "name": "Dead-branch payload",
        "what": "A tapscript with a branch that a constant makes unreachable, holding pushes that never run. The JXL-n-hide encoder uses it.",
        "how": "The node never executes the branch, so the pushes are payload riding in the witness at the witness discount.",
        "filter": KNOTS, "knots_since": KNOTS_SINCE, "option": "-rejectdeadbranches",
        "prs": [("knots#400", "https://github.com/bitcoinknots/bitcoin/pull/400")],
    },
    "bare-inscription": {
        "name": "Inscription, bare envelope",
        "what": "An `ord` inscription whose pushes are dropped again with OP_2DROP instead of wrapped in OP_FALSE OP_IF.",
        "how": "The shape inscriptions moved to for when OP_IF is not available. Knots before 29.4.3 counted only the last push before a drop, so the payload walked past it; 29.4.3 counts the whole run.",
        "filter": KNOTS, "knots_since": KNOTS_SINCE, "option": "-rejectbareenvelopes",
        "prs": [("knots#319", "https://github.com/bitcoinknots/bitcoin/pull/319")],
    },
    "bare-envelope": {
        "name": "Bare data envelope",
        "what": "A run of data pushes in a script, dropped again with OP_DROP or OP_2DROP.",
        "how": "The same shape as a bare inscription without the `ord` tag. The pushes never affect the spend.",
        "filter": KNOTS, "knots_since": KNOTS_SINCE, "option": "-rejectbareenvelopes",
        "prs": [("knots#319", "https://github.com/bitcoinknots/bitcoin/pull/319")],
    },
    "envelope": {
        "name": "OP_FALSE OP_IF envelope",
        "what": "Pushes wrapped in a branch that can never run.",
        "how": "The classic witness envelope. Knots counts it as data by default.",
        "filter": KNOTS,
    },
    "inscription": {
        "name": "Ordinals inscription",
        "what": "An `ord` envelope in a tapscript.",
        "how": "Knots counts the envelope as data by default.",
        "filter": KNOTS,
    },
    "witness-data": {
        "name": "Witness data, unnamed",
        "what": "Input data the policy code counts that the field guide has no name for yet.",
        "how": "Unnamed shape. If you know what it is, open an issue.",
        "filter": KNOTS,
    },
    "runes": {
        "name": "Runestone",
        "what": "A Runes token message: OP_RETURN OP_13 followed by the encoded etch, mint or transfer.",
        "how": "Token bookkeeping in an OP_RETURN. Knots rejects it with `-rejecttokens`, on by default.",
        "filter": KNOTS, "option": "-rejecttokens",
    },
    "counterparty": {
        "name": "Counterparty",
        "what": "A Counterparty token message, RC4-encrypted under the first input's txid so it reads as noise.",
        "how": "Knots decrypts the first bytes and rejects it with `-rejecttokens`.",
        "filter": KNOTS, "option": "-rejecttokens",
    },
    "cat21": {
        "name": "CAT-21 mint",
        "what": "A transaction minted with nLockTime 21 so an indexer counts it as a collectible.",
        "how": "Knots rejects it with `-rejectparasites`.",
        "filter": KNOTS, "option": "-rejectparasites",
    },
    "bare-multisig": {
        "name": "Bare multisig data",
        "what": "Bare multisig outputs whose keys are file chunks: classic Stamps.",
        "how": "Knots rejects bare multisig by default.",
        "filter": KNOTS, "option": "-permitbaremultisig=0",
    },
    "token-json": {
        "name": "JSON token message",
        "what": "A token operation written as a JSON object in OP_RETURN: the BRC-20 format that ico-20, crc-20 and others copy, with a `\"p\"` field naming the protocol and `\"op\"` the operation.",
        "how": "The message itself fits Knots' default 83-byte OP_RETURN allowance, so stock Knots relays it unless the transaction carries other data too. Plumb reads the object and refuses it with `-rejecttokenmessages`, its switch over the JSON and Omni checks knots#489 adds to `-rejecttokens` (Plumb's own plumb#2 before plumb9).",
        "filter": PLUMB, "option": "-rejecttokenmessages",
        "prs": [("knots#489", "https://github.com/bitcoinknots/bitcoin/pull/489")],
    },
    "omni": {
        "name": "Omni Layer",
        "what": "An Omni token message in OP_RETURN.",
        "how": "Small enough for Knots' default 83-byte OP_RETURN allowance, so stock Knots relays it. Plumb refuses it with `-rejecttokenmessages`, its switch over the JSON and Omni checks knots#489 adds to `-rejecttokens`.",
        "filter": PLUMB, "option": "-rejecttokenmessages",
        "prs": [("knots#489", "https://github.com/bitcoinknots/bitcoin/pull/489")],
    },
    "stacks": {
        "name": "Stacks commitment",
        "what": "A Stacks block commitment in OP_RETURN, usually beside a burn to the all-zero hash.",
        "how": "Plumb exempts the all-zero burn on purpose; it is a burn, not a payload. The commitment fits Knots' default 83-byte allowance, so stock Knots relays it; Plumb's default of 42 refuses every one.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "swap-memo": {
        "name": "Swap memo",
        "what": "A cross-chain swap instruction in OP_RETURN, in one of two forms: a THORChain or Maya memo such as `=:BTC.BTC:<address>`, or a swap service's order, `to:USDT(BSC):<address>` behind a short order id.",
        "how": "It fits Knots' default 83-byte OP_RETURN allowance, so stock Knots relays it. Plumb's default of 42 refuses one that counts more than 42 bytes, which makes it sewage here; a shorter one stays gray.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "ln-anchor-sweep": {
        "name": "Lightning anchor sweep",
        "what": "Spends Lightning anchors nobody claimed and pays every sat to the miner. A force close of an anchor channel leaves two 330-sat anchor outputs, and 16 blocks later anyone can spend one without a signature.",
        "how": "The only output is an OP_RETURN, because a transaction needs an output and OP_RETURN is the one that takes no value. It carries nothing, or 3 bytes in a sweep of one anchor: a one-input transaction with an empty OP_RETURN is 61 non-witness bytes, and relay refuses anything under 65. Knots refuses a transaction whose only output is an OP_RETURN, and Plumb inherits that. cesspool does not count it as sewage: the bytes are padding, and the sweep takes coins out of the UTXO set.",
        "filter": NOT_SPAM, "option": "-permitbaredatacarrier",
    },
    "p2a-sweep": {
        "name": "Pay-to-anchor sweep",
        "what": "Spends pay-to-anchor (P2A) outputs, the keyless anchors used for fee bumping, and pays them to the miner.",
        "how": "The only output is an OP_RETURN. A sweep of one P2A output pads it with 4 bytes of filler, such as the text \"burn\", to reach the 65-byte minimum. Knots refuses a transaction whose only output is an OP_RETURN, and Plumb inherits that. cesspool does not count it as sewage: the bytes are padding.",
        "filter": NOT_SPAM, "option": "-permitbaredatacarrier",
    },
    "dust-sweep": {
        "name": "Dust sweep",
        "what": "Spends small outputs into fees, with nothing but an OP_RETURN of 4 bytes or less.",
        "how": "A one-input sweep needs 3 bytes of padding to reach the 65-byte minimum, and sweepers often use a 4-byte filler, so the rule allows 4. Knots refuses a transaction whose only output is an OP_RETURN, and Plumb inherits that. cesspool does not count it as sewage. One that writes more than 4 bytes stays sewage: a transaction that only writes bytes is what that Knots rule is for.",
        "filter": NOT_SPAM, "option": "-permitbaredatacarrier",
    },
    "powpeg-flyover": {
        "name": "Rootstock PowPeg, Flyover deposit",
        "what": "The Rootstock federation moving bitcoin out of a Flyover deposit. A Flyover deposit script is the PowPeg script with a 32-byte value in front that the script drops unused, which makes each deposit address different.",
        "how": "Knots counts a push followed by OP_DROP as data outside OP_RETURN and refuses it, and Plumb inherits that. cesspool does not count it as sewage: the 32 bytes are a commitment, smaller than the OP_RETURN notes it leaves gray, and only the keys in the PowPeg script can spend the coins. It is recognized by the exact script every PowPeg spend since the fork reveals, 13 keys and all, each spend signed by 5 of the 9; any other script stays sewage. The script is not checked against an address Rootstock publishes.",
        "filter": NOT_SPAM, "option": "-acceptnonstddatacarrier",
    },
    "self-pay": {
        "name": "Payment back to the spender",
        "what": "Pays the address that one of its own inputs spends from with a signature. The ones since the fork pay a vanity address whose hash starts with six zero bytes.",
        "how": "Plumb 6's `-rejectfakeoutputs` read the run of zeros as data, and Knots relays it. An output that pays the script of a coin the transaction spends adds no chosen bytes: the spend proved the key or hash in it is real, and fitting chosen data into one would take at least 2^128 tries. cesspool does not count it as sewage. Plumb 7 ships the fix, so these grade clean now.",
        "filter": NOT_SPAM, "option": "-rejectfakeoutputs",
    },
    "liquid-emergency": {
        "name": "Liquid-layout emergency spend",
        "what": "Spends the Liquid federation script by its emergency branch: the 11-of-15 check fails on purpose, and a 2-of-3 signs after 4,032 blocks.",
        "how": "Plumb's `-rejectfakemultisig` counts the federation keys the branch leaves unsigned. This is very likely Liquid's own recovery and not spam, but cesspool still counts it: every Liquid deposit tweaks the keys, so the layout cannot prove they are real, and a layout anyone can copy cannot be exempt.",
        "filter": KNOTS, "knots_since": KNOTS_SINCE, "option": "-rejectfakemultisig",
        "prs": [("knots#422", "https://github.com/bitcoinknots/bitcoin/pull/422")],
    },
    "babylon": {
        "name": "Babylon staking tag",
        "what": "Pays bitcoin to a taproot output and adds an OP_RETURN starting with `bbn1`, the tag Babylon uses to mark staking transactions for its proof-of-stake chain. Two layouts appear, 63 and 78 bytes.",
        "how": "The payment is ordinary script. The OP_RETURN is a record for another chain's indexer. It fits Knots' default 83-byte allowance, so stock Knots relays it; Plumb's default of 42 refuses every one.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "coredao": {
        "name": "Core DAO staking tag",
        "what": "Pays bitcoin to a P2WSH output and adds an OP_RETURN starting with `SAT+`, the tag Core DAO uses to mark bitcoin staking for its chain. Two layouts appear, 52 and 80 bytes.",
        "how": "The payment is ordinary script. The OP_RETURN is a record for another chain's indexer. It fits Knots' default 83-byte allowance, so stock Knots relays it; Plumb's default of 42 refuses every one.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "bip47": {
        "name": "BIP47 notification",
        "what": "The one-time notification that sets up a BIP47 reusable payment code: an 80-byte OP_RETURN holding the sender's payment code, blinded for the recipient's wallet.",
        "how": "It fits Knots' default 83-byte allowance, so stock Knots relays it; Plumb's default of 42 refuses it. Silent payments (BIP352) do the same job without writing any data.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "commitment": {
        "name": "32-byte commitment",
        "what": "A single 32-byte OP_RETURN, the form OpenTimestamps, proof-of-reserves and RGB commitments take: one hash standing for data kept elsewhere.",
        "how": "Gray when it rides a payment: it fits Plumb's default of 42. One that is a transaction's only output stays sewage: Knots refuses a transaction that only writes bytes. The length is matched, not the content: nearly any 32 bytes that are not text land here.",
        "filter": ALLOWED,
    },
    "opreturn-text": {
        "name": "OP_RETURN text",
        "what": "A short text note in OP_RETURN: bridge tags, protocol markers, messages.",
        "how": "It fits Knots' default 83-byte OP_RETURN allowance, so stock Knots relays it. Plumb's default of 42 refuses one that counts more than 42 bytes, which makes it sewage here; a shorter one stays gray.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "opreturn-data": {
        "name": "OP_RETURN data",
        "what": "Short binary data in OP_RETURN: commitments, staking markers, bridge tags.",
        "how": "It fits Knots' default 83-byte OP_RETURN allowance, so stock Knots relays it. Plumb's default of 42 refuses one that counts more than 42 bytes, which makes it sewage here; a shorter one stays gray.",
        "filter": PLUMB, "option": DATACARRIER,
    },
    "opreturn-empty": {
        "name": "Empty OP_RETURN",
        "what": "An OP_RETURN output with nothing after it.",
        "how": "Carries no bytes.",
        "filter": ALLOWED,
    },
    "runes-empty": {
        "name": "Empty runestone",
        "what": "OP_RETURN OP_13 with nothing after it. The Runes indexer reads it as \"send everything to the first output\".",
        "how": "Carries no payload bytes, so Knots lets it through.",
        "filter": ALLOWED,
    },
}


# What a protocol's defenders say, and why it still fails the bar on /about/#bar. Shown in the field guide.
CASES = {
    "fake-multisig": "Nobody defends this one in public. The inputs were checked, in blocks 961640 to 976951: 319,602 of 319,604 hold 1,000 sats or less, and 319,478 were funded by dust P2WSH runs. No wallet keeps coins that way.",
    "inscription": "Its defenders say it pays its fees and uses witness space the way segwit allows. It fails the bar because the envelope never runs: it is storage.",
    "bare-inscription": "Its defenders say it pays its fees and uses witness space the way segwit allows. It fails the bar because the pushes are dropped unused: it is storage.",
    "envelope": "Its defenders say it pays its fees and uses witness space the way segwit allows. It fails the bar because the envelope never runs: it is storage.",
    "runes": "Its defenders say it is tidier than BRC-20 because balances follow coins. It fails the bar because the runestone is a ledger entry for a token indexer.",
    "token-json": "Its defenders say it is open, simple and traded. It fails the bar because the JSON exists for an indexer.",
    "counterparty": "Its defenders say it is one of the oldest protocols on Bitcoin. It fails the bar because it is a ledger for another system.",
    "omni": "Its defenders say it carried USDT for years. It fails the bar for the same reason as Counterparty: a ledger for another system.",
    "olga": "Its defenders say permanence is the point. It fails the bar worst of all: the outputs can never be spent, so they stay in every node's UTXO set.",
    "olga-acme": "Its defenders say permanence is the point. It fails the bar worst of all: the outputs can never be spent, so they stay in every node's UTXO set.",
    "stacks": "Its defenders say Stacks miners pay real bitcoin to stackers. It fails the bar because the commitment is Stacks block data; the bitcoin moved here is small.",
    "babylon": "Its defenders say it secures a proof-of-stake chain with bitcoin that never leaves its owner. The payment passes the bar; the tag does not, because it serves Babylon's indexer, not the spend.",
    "coredao": "Its defenders say it secures a chain with bitcoin that never leaves its owner. The payment passes the bar; the tag does not, because it serves Core DAO's indexer, not the spend.",
    "bip47": "Its defenders say it is a payment setup for a person, not an indexer. That is true, and it is the closest of these to passing. It still fails the bar: nothing in the spend uses the 80 bytes.",
    "swap-memo": "Its defenders say it routes a real swap. It fails the bar because the memo is an instruction for another chain's router.",
}
for _t, _c in CASES.items():
    TYPES[_t]["case"] = _c

# Protocols recognized by their exact scripts or tags (classify.known), for the transaction page.
# The grade never depends on these; a transaction that also carries data is graded on the data.
KNOWN = {
    "ln-commitment": ("Lightning force close", "Publishes a channel's latest commitment: it spends the 2-of-2 funding output, with the channel's obscured commitment number in its lock time and sequence."),
    "ln-penalty": ("Lightning penalty", "Takes a cheating peer's delayed output by the revocation key, after the peer published an old commitment."),
    "ln-htlc": ("Lightning HTLC claim or timeout", "Spends an HTLC output of a force close, with the payment preimage or after the timeout."),
    "ln-to-local": ("Lightning delayed sweep", "Spends the delayed output of a force close after its CSV delay."),
    "ln-anchor": ("Lightning anchor fee bump", "Spends a channel's 330-sat anchor with the owner's signature, to raise the fee of a force close."),
    "ln-to-remote": ("Lightning channel sweep", "Spends the other side's output of a force close after its one-block delay."),
    "two-of-two": ("2-of-2 multisig spend", "Spends the script Lightning uses for channel funding: a cooperative close, a splice, or any other 2-of-2 contract. The script alone does not say which."),
    "ln-anchor-sweep": ("Lightning anchor sweep", "Spends anchors nobody claimed into fees, removing them from the UTXO set."),
    "p2a-sweep": ("Pay-to-anchor sweep", "Spends keyless anchor outputs into fees."),
    "dust-sweep": ("Dust sweep", "Spends small outputs into fees."),
    "powpeg": ("Rootstock PowPeg spend", "The Rootstock federation moving pegged bitcoin, signed by 5 of its 9 keys."),
    "powpeg-flyover": ("Rootstock PowPeg, Flyover deposit", "The Rootstock federation moving bitcoin out of a Flyover deposit."),
    "liquid": ("Liquid federation spend", "Spends the Liquid federation script, signed by 11 of its 15 keys."),
    "liquid-emergency": ("Liquid-layout emergency spend", "Spends the Liquid federation script by its timelocked 2-of-3 emergency branch."),
    "babylon-covenant": ("Babylon unbonding or slashing", "Spends a Babylon staking output by a path that needs the staker and the covenant committee."),
    "babylon": ("Babylon staking", "A payment with Babylon's staking tag beside it."),
    "coredao": ("Core DAO staking", "A payment with Core DAO's staking tag beside it."),
    "stacks": ("Stacks block commit", "A Stacks miner's commitment, paying bitcoin to stackers or burning it."),
    "bip47": ("BIP47 notification", "Sets up a reusable payment code for one recipient."),
    "commitment": ("32-byte commitment", "One hash in OP_RETURN, the form timestamping and proof-of-reserves take."),
    "swap-memo": ("Swap memo", "An instruction for a cross-chain swap router."),
}


def info(t):
    return TYPES.get(t, {"name": t, "what": "", "how": "", "filter": NONE})
