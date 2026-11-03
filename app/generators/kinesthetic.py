"""Trial schedules for the reflex pillar. Timing is MEASURED on the client, so the server only
sanity-checks it (see scoring.score_kinesthetic)."""
from app.core.hashrng import HashRng

BENCHMARK_MS = 250
# bucket -> (reaction delay range ms, target size px, target time-to-live ms, gap range ms)
PARAMS = [((2000, 4500), 76, 2200, (500, 1200)),
          ((1800, 4200), 60, 1700, (400, 1000)),
          ((1500, 4000), 48, 1300, (300, 900)),
          ((1200, 3500), 38, 1000, (250, 800))]


def generate(bucket: int, count: int, seed: str) -> list[dict]:
    rng = HashRng("kin", bucket, seed)
    (dlo, dhi), size, ttl, (glo, ghi) = PARAMS[bucket]
    out = []
    for k in range(count):
        if k % 4 == 0:
            t = {"type": "reaction", "delay_ms": rng.randint(dlo, dhi)}
        else:
            t = {"type": "target", "x": rng.randint(8, 92), "y": rng.randint(12, 88),
                 "size": size, "ttl_ms": ttl, "delay_ms": rng.randint(glo, ghi)}
        t["id"] = f"ki{bucket}-{k}"
        t["answer"] = None
        out.append(t)
    return out
