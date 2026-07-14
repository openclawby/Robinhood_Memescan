"""Verified Robinhood-chain launchpad factories + token-creation events.

Reverse-engineered and live-validated 2026-07-13. For each platform we watch
`factory` for logs matching `topic0`, then decode the new token address per
`decode`:
    topic1 / topic3 -> token is that indexed topic (last 20 bytes)
    data0  / data1  -> token is the Nth 32-byte word of the log `data`
"""

PLATFORMS = [
    {"name": "hood.fun / ape.store",
     "factory": "0x6e4910ea5a04376032f6564da9a9e4e88b7a87c1",
     "topic0": "0xb378e89b40ac5bbe0e2241b596fbe1adc3cf1fb7c982aa1b4560165cf264ee93",
     "decode": "topic1", "enabled": True},
    {"name": "flap",
     "factory": "0x26605f322f7ff986f381bb9a6e3f5dab0beaeb09",
     "topic0": "0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603",
     "decode": "data1", "enabled": True},
    {"name": "trench.today",
     "factory": "0x77dc6f6361b7b99456fc3761ce5b7dda80d83f9d",
     "topic0": "0xe2eb7016a2fc7f0aec441cc8bc9a7ecd75d29d94478782bab1cfa9c5b0dbdf1b",
     "decode": "topic3", "enabled": True},
    {"name": "virtuals",
     "factory": "0xd4ccbfa37e2f35611b3042e4096ad7a3459bd007",
     "topic0": "0xb9ee8aa6d909a3efd0bf1b0bc2bde7f998f7ad30178b0d45f9227f5382cebc8f",
     "decode": "topic1", "enabled": True},
    {"name": "bankr",
     "factory": "0xeb7c034704ef8dcd2d32324c1545f62fb4ad0862",
     "topic0": "0x68ff1cfcdcf76864161555fc0de1878d8f83ec6949bf351df74d8a4a1a2679ab",
     "decode": "data0", "enabled": True},
    {"name": "bags",
     "factory": "0xe8cc4431adf8b5a847c113ef0c6af9043219cb37",
     "topic0": "0x643b3b606052cbadac2f906ad0b462da99eda2a1d4f824d315d7f6edd3e4cced",
     "decode": "topic1", "enabled": True},   # TokenCreated(address token,...); traced 2026-07-14
    # noxa.fi's factory is currently dormant (no launches since ~block 6.88M) -> off by default.
    {"name": "noxa.fi",
     "factory": "0xd9ec2db5f3d1b236843925949fe5bd8a3836fccb",
     "topic0": "0x1461370115e1c2be79cb529f8cfcbd11316e789d9c6099fc83417b0b4c48c62a",
     "decode": "topic1", "enabled": False},
]


def _word(data_hex, idx):
    s = data_hex[2:] if data_hex.startswith("0x") else data_hex
    chunk = s[idx * 64:(idx + 1) * 64]
    if len(chunk) < 40:
        return None
    return "0x" + chunk[-40:]


def decode_token(rule, log):
    """Extract the new token address from a factory log per the decode rule."""
    topics = log.get("topics") or []
    data = log.get("data") or "0x"
    if rule == "topic1":
        return "0x" + topics[1][-40:] if len(topics) > 1 else None
    if rule == "topic3":
        return "0x" + topics[3][-40:] if len(topics) > 3 else None
    if rule == "data0":
        return _word(data, 0)
    if rule == "data1":
        return _word(data, 1)
    return None
