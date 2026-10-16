"""Life-contingency mathematics on a Gompertz-Makeham mortality law (commutation-function method).

ILLUSTRATIVE ONLY: default parameters are a plausible shape, not a regulatory or experience table.
For production, load a real table (e.g. IALM 2012-14 for India) via `MortalityTable.from_qx`.
"""
from dataclasses import dataclass

import numpy as np

OMEGA = 120


@dataclass
class MortalityTable:
    qx: np.ndarray                       # one-year death probabilities, ages 0..OMEGA (qx[OMEGA] == 1)

    @classmethod
    def gompertz_makeham(cls, A=0.0007, B=0.00005, c=1.10, multiple=1.0, flat_extra=0.0):
        """mu_x = A + B c^x ; q_x = 1 - exp(-(A + B c^x (c-1)/ln c)). `multiple` scales mu (substandard rating)."""
        x = np.arange(OMEGA + 1)
        H = (A + B * c ** x * (c - 1) / np.log(c)) * multiple
        q = 1 - np.exp(-H) + flat_extra
        q = np.clip(q, 0, 1)
        q[OMEGA] = 1.0
        return cls(q)

    @classmethod
    def from_qx(cls, qx):
        q = np.array(qx, dtype=float)
        if len(q) != OMEGA + 1:
            raise ValueError(f"need {OMEGA + 1} q_x values (ages 0..{OMEGA})")
        q[-1] = 1.0
        return cls(q)

    def lx(self, radix=100_000.0):
        l = np.empty(OMEGA + 2)
        l[0] = radix
        for x in range(OMEGA + 1):
            l[x + 1] = l[x] * (1 - self.qx[x])
        return l

    def life_expectancy(self, x):
        l = self.lx()
        return float(l[x + 1:OMEGA + 1].sum() / l[x] + 0.5)  # curtate + 1/2


class Commutation:
    """D, N, C, M functions at a fixed interest rate i (annual effective)."""

    def __init__(self, table: MortalityTable, i: float):
        self.t, self.i = table, i
        self.v, self.d = 1 / (1 + i), i / (1 + i)
        l = table.lx()
        ages = np.arange(OMEGA + 1)
        dx = l[:-1] - l[1:]
        self.D = self.v ** ages * l[:-1]
        self.C = self.v ** (ages + 1) * dx
        self.N = np.concatenate([np.cumsum(self.D[::-1])[::-1], [0.0]])
        self.M = np.concatenate([np.cumsum(self.C[::-1])[::-1], [0.0]])

    # present values per unit sum assured / per unit annual annuity
    def A(self, x):                       # whole life assurance, paid at end of year of death
        return self.M[x] / self.D[x]

    def a_due(self, x):                   # whole life annuity-due
        return self.N[x] / self.D[x]

    def A_term(self, x, n):               # n-year term assurance
        n = min(n, OMEGA + 1 - x)
        return (self.M[x] - self.M[x + n]) / self.D[x]

    def a_due_temp(self, x, n):           # n-year temporary annuity-due
        n = min(n, OMEGA + 1 - x)
        return (self.N[x] - self.N[x + n]) / self.D[x]

    def A_endow(self, x, n):              # n-year pure endowment
        return self.D[x + n] / self.D[x] if x + n <= OMEGA else 0.0


def term_net_premium(cm: Commutation, x, n, sum_assured=1.0):
    """Equivalence principle: level annual premium P with P * a_due_temp = SA * A_term."""
    return sum_assured * cm.A_term(x, n) / cm.a_due_temp(x, n)


def term_gross_premium(cm: Commutation, x, n, sum_assured, initial_expense=0.0, renewal_expense=0.0,
                       commission=0.0):
    """G * a * (1 - c) = SA * A + e0 + e1 * (a - 1)  (commission c on every premium, fixed expenses)."""
    a = cm.a_due_temp(x, n)
    return (sum_assured * cm.A_term(x, n) + initial_expense + renewal_expense * (a - 1)) / ((1 - commission) * a)


def term_reserve(cm: Commutation, x, n, t, sum_assured, premium):
    """Prospective net-premium reserve at duration t (0 <= t <= n) for an n-year term policy issued at age x."""
    if t >= n:
        return 0.0
    return sum_assured * cm.A_term(x + t, n - t) - premium * cm.a_due_temp(x + t, n - t)
