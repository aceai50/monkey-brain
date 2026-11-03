"""Deterministic RNG built on SHA-256 (NOT Python's hash(), which is salted per process, and not
random.Random, whose helper algorithms are not guaranteed stable across Python versions).
Same seed parts -> same sequence, on every machine, forever."""
import hashlib


def stable_key(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


class HashRng:
    def __init__(self, *parts):
        self._seed = ":".join(str(p) for p in parts)
        self._n = 0

    def _next(self) -> int:
        d = hashlib.sha256(f"{self._seed}#{self._n}".encode()).digest()
        self._n += 1
        return int.from_bytes(d, "big")

    def randint(self, a: int, b: int) -> int:
        return a + self._next() % (b - a + 1)

    def random(self) -> float:
        return (self._next() % (1 << 53)) / (1 << 53)

    def choice(self, seq):
        return seq[self._next() % len(seq)]

    def shuffle(self, items: list) -> list:
        for i in range(len(items) - 1, 0, -1):
            j = self._next() % (i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    def weighted(self, pairs):
        """pairs: [(value, weight)]"""
        total = sum(w for _, w in pairs)
        x = self._next() % total
        for v, w in pairs:
            if x < w:
                return v
            x -= w
        return pairs[-1][0]
