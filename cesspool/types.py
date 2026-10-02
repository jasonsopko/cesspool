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
        "what": "Spends a script that asks for one signature out of a dozen or more public keys. Only the signing key is real; the rest are file bytes dressed as keys.",
        "how": "Each unused 33-byte key in the revealed witness script is payload. One reveal can carry hundreds of inputs.",
        "filter": NONE,
        "prs": [("knots#422", "https://github.com/bitcoinknots/bitcoin/pull/422"),
                ("knots#435", "https://github.com/bitcoinknots/bitcoin/pull/435")],
        "note": "Its fake-output filter catches the funding transaction that sets them up, not the reveal. Both PRs are in review; Plumb ships one once the false-positive work is done.",
    },
    "p2wsh-run": {
        "name": "Dust P2WSH run",
        "what": "Pays the same few hundred sats to a run of P2WSH outputs whose 32-byte hashes are not hashes of any script.",
        "how": "Each output hash is 32 bytes of payload. The coins are unspendable and stay in every node's UTXO set for good.",
        "filter": PLUMB, "option": "-rejectfakeoutputs",
        "prs": [("knots#389", "https://github.com/bitcoinknots/bitcoin/pull/389")],
    },
    "olga": {
        "name": "OLGA / Stamps in P2WSH",
        "what": "The Stamps OLGA framing: a length prefix and a magic tag, then the payload in consecutive P2WSH output hashes.",
        "how": "Knots catches the original `stamp:` tag. The `ACME` variant changed four bytes and walked past it until Plumb's output filter.",
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
    "omni": {
        "name": "Omni Layer",
        "what": "An Omni token message in OP_RETURN.",
        "how": "Small enough to fit the default OP_RETURN allowance.",
        "filter": ALLOWED,
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
