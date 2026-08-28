"""Deterministic verification of the Markov predictor's math.
Run: .venv/bin/python tests/test_markov.py"""
import math
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim.markov import (_Chain, TrafficMarkov, expm, stationary,  # noqa
                        classify, N_STATES)


def main():
    assert classify(14, 14) == 0 and classify(8, 14) == 1
    assert classify(3.5, 14) == 2 and classify(1, 14) == 3

    c = _Chain()
    for i, s in enumerate([0, 0, 1, 1, 0]):
        c.observe(s, i * 30.0)
    assert c.C[0][0] == 1 and c.C[0][1] == 1
    assert c.T[0] == 60.0 and c.J[0][1] == 1
    assert all(abs(sum(row) - 1) < 1e-12 for row in c.P())

    a_rate, b_rate = 1 / 120.0, 1 / 60.0
    c2 = _Chain()
    c2.T = [1200.0, 600.0, 1e9, 1e9]
    c2.J[0][1] = a_rate * 1200.0
    c2.J[1][0] = b_rate * 600.0
    q = c2.Q()
    for t in (0.0, 30.0, 73.0, 300.0, 3000.0):
        got = expm(q, t)[0][0]
        s = a_rate + b_rate
        want = b_rate / s + (a_rate / s) * math.exp(-s * t)
        assert abs(got - want) < 1e-6, (t, got, want)

    ident = expm(q, 0.0)
    assert all(abs(ident[i][i] - 1) < 1e-9 for i in range(N_STATES))

    p2 = [[0.9, 0.1, 0, 0], [0.3, 0.6, 0.1, 0],
          [0, 0.4, 0.5, 0.1], [0, 0, 0.7, 0.3]]
    pi = stationary(p2)
    pi2 = [sum(pi[i] * p2[i][j] for i in range(4)) for j in range(4)]
    assert all(abs(pi[j] - pi2[j]) < 1e-9 for j in range(4))

    c3 = _Chain.from_json(c.to_json())
    assert c3.C == c.C and c3.T == c.T and c3.n == c.n

    # ---- forecast cache: exact within a tick, invalidated on the tick ----
    m = TrafficMarkov.__new__(TrafficMarkov)
    m.cfg = types.SimpleNamespace(markov_min_obs=1)
    c2.n = 10                       # enough history for _chain_for
    m.chains = {"e1": c2}
    m._edge_class = {}
    m.state_now = {"e1": 0}
    m._fc_cache = {}
    m._q_cache = {}
    m.monitored = []
    m._next_sample = 0.0
    m.period = 30.0
    m._pending = []
    # cached forecast equals the uncached expm at the bucket-aligned horizon
    row = m.forecast("e1", 73.0)
    want = expm(c2.Q(), 60.0)[0]                 # 73 s -> 60 s bucket
    assert all(abs(row[j] - want[j]) < 1e-12 for j in range(N_STATES))
    # two horizons in one 15 s bucket return the identical cached row
    assert m.forecast("e1", 61.0) is row
    # the sample tick clears the cache, so a mutated chain is re-read
    c2.J[0][1] *= 3.0
    m.update(1.0)                                # past the gate; no traci
    row2 = m.forecast("e1", 73.0)
    want2 = expm(c2.Q(), 60.0)[0]
    assert row2 is not row
    assert all(abs(row2[j] - want2[j]) < 1e-12 for j in range(N_STATES))
    print("ALL MARKOV MATH TESTS PASSED")


if __name__ == "__main__":
    main()
