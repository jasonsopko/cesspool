"""The field guide: every shape the classifier names.

`filter` says what is supposed to stop it. The site checks that against the
verdicts it actually recorded and shows the measured catch rate beside it, so
a wrong entry here shows up as a contradiction on the page.
"""

KNOTS = "Knots default"
PLUMB = "Plumb"
NONE = "No filter yet"
ALLOWED = "Allowed"

TYPES = {
    "fake-multisig": {
        "name": "Fake multisig keys",
        "what": "Spends a multisig script with eight or more public keys that no signature needs. Only the signing keys are real; the rest are file bytes dressed as keys.",
        "how": "Each unused 33-byte key in the revealed witness script is payload. One reveal can carry hundreds of inputs.",
        "filter": PLUMB, "option": "-rejectfakemultisig",
        "prs": [("knots#422", "https://github.com/bitcoinknots/bitcoin/pull/422")],
        "note": "Plumb counts each unsigned key past ten in a script as data. A reveal with ten or fewer unsigned keys in each script gets through.",
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
        "filter": PLUMB, "option": "-rejectdeadbranches",
        "prs": [("knots#400", "https://github.com/bitcoinknots/bitcoin/pull/400")],
    },
    "bare-inscription": {
        "name": "Inscription, bare envelope",
        "what": "An `ord` inscription whose pushes are dropped again with OP_2DROP instead of wrapped in OP_FALSE OP_IF.",
        "how": "The shape inscriptions moved to for when OP_IF is not available. Knots counts only the last push before a drop, so the payload walks past it.",
        "filter": PLUMB, "option": "-rejectbareenvelopes",
        "prs": [("knots#319", "https://github.com/bitcoinknots/bitcoin/pull/319")],
    },
    "bare-envelope": {
        "name": "Bare data envelope",
        "what": "A run of data pushes in a script, dropped again with OP_DROP or OP_2DROP.",
        "how": "The same shape as a bare inscription without the `ord` tag. The pushes never affect the spend.",
        "filter": PLUMB, "option": "-rejectbareenvelopes",
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
        "how": "The message itself fits the default 83-byte OP_RETURN allowance, so stock Knots relays it unless the transaction carries other data too. Plumb reads the object and refuses it with `-rejecttokenmessages`.",
        "filter": PLUMB, "option": "-rejecttokenmessages",
        "prs": [("plumb#2", "https://github.com/plumb-node/plumb/pull/2")],
    },
    "omni": {
        "name": "Omni Layer",
        "what": "An Omni token message in OP_RETURN.",
        "how": "Small enough for the default 83-byte OP_RETURN allowance, so stock Knots relays it. Plumb refuses it with `-rejecttokenmessages`.",
        "filter": PLUMB, "option": "-rejecttokenmessages",
        "prs": [("plumb#2", "https://github.com/plumb-node/plumb/pull/2")],
    },
    "stacks": {
        "name": "Stacks commitment",
        "what": "A Stacks block commitment in OP_RETURN, usually beside a burn to the all-zero hash.",
        "how": "Plumb exempts the all-zero burn on purpose; it is a burn, not a payload.",
        "filter": ALLOWED,
    },
    "swap-memo": {
        "name": "Swap memo",
        "what": "A cross-chain swap instruction for a bridge or DEX router, in OP_RETURN.",
        "how": "Fits the default 83-byte OP_RETURN allowance.",
        "filter": ALLOWED,
    },
    "opreturn-text": {
        "name": "OP_RETURN text",
        "what": "A short text note in OP_RETURN: bridge tags, protocol markers, messages.",
        "how": "Fits the default 83-byte OP_RETURN allowance. Shown as gray water, not sewage.",
        "filter": ALLOWED,
    },
    "opreturn-data": {
        "name": "OP_RETURN data",
        "what": "Short binary data in OP_RETURN: commitments, staking markers, bridge tags.",
        "how": "Fits the default 83-byte OP_RETURN allowance. Shown as gray water, not sewage.",
        "filter": ALLOWED,
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


def info(t):
    return TYPES.get(t, {"name": t, "what": "", "how": "", "filter": NONE})
