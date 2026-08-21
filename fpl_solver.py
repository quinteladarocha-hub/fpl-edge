"""A one-file binary MILP abstraction with two free backends.

The squad problem is defined once, as coefficients, and translated to
whichever solver is installed:

  PuLP + CBC       primary, what GitHub Actions uses
  scipy + HiGHS    fallback, ships inside scipy so it needs no extra install

Both are open source and free. Having two backends means a broken CBC binary
on a runner does not stop the brief going out, and it lets the model be tested
in environments where PuLP is not present.
"""

from __future__ import annotations

import logging

log = logging.getLogger("fpl_edge.solver")

LE, GE, EQ = "<=", ">=", "=="


class BinaryProgram:
    """Maximise c.x subject to linear constraints, all variables binary."""

    def __init__(self, name: str = "program"):
        self.name = name
        self.var_names: list[str] = []
        self.objective: list[float] = []
        self.constraints: list[tuple[dict[int, float], str, float, str]] = []

    def add_var(self, name: str, obj: float = 0.0) -> int:
        self.var_names.append(name)
        self.objective.append(float(obj))
        return len(self.var_names) - 1

    def add_constraint(
        self, terms: dict[int, float], sense: str, rhs: float, label: str = ""
    ) -> None:
        self.constraints.append((dict(terms), sense, float(rhs), label))

    # ------------------------------------------------------------------ #

    def solve(self, time_limit: int = 120) -> tuple[list[int], str]:
        try:
            return self._solve_pulp(time_limit)
        except ImportError:
            log.warning("PuLP not installed, falling back to scipy/HiGHS")
        except Exception as exc:  # noqa: BLE001
            log.warning("PuLP solve failed (%s), falling back to scipy/HiGHS", exc)
        return self._solve_scipy(time_limit)

    def _solve_pulp(self, time_limit: int) -> tuple[list[int], str]:
        import pulp

        prob = pulp.LpProblem(self.name, pulp.LpMaximize)
        xs = [
            pulp.LpVariable(f"v{i}_{n}", cat="Binary")
            for i, n in enumerate(self.var_names)
        ]
        prob += pulp.lpSum(c * x for c, x in zip(self.objective, xs) if c)

        for terms, sense, rhs, label in self.constraints:
            expr = pulp.lpSum(coef * xs[i] for i, coef in terms.items())
            if sense == LE:
                prob += expr <= rhs, label or None
            elif sense == GE:
                prob += expr >= rhs, label or None
            else:
                prob += expr == rhs, label or None

        status = prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit))
        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(f"CBC returned {pulp.LpStatus[status]}")
        chosen = [i for i, x in enumerate(xs) if x.value() and x.value() > 0.5]
        return chosen, "PuLP/CBC"

    def _solve_scipy(self, time_limit: int) -> tuple[list[int], str]:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp

        n = len(self.var_names)
        rows, lower, upper = [], [], []
        for terms, sense, rhs, _ in self.constraints:
            row = np.zeros(n)
            for i, coef in terms.items():
                row[i] = coef
            rows.append(row)
            if sense == LE:
                lower.append(-np.inf)
                upper.append(rhs)
            elif sense == GE:
                lower.append(rhs)
                upper.append(np.inf)
            else:
                lower.append(rhs)
                upper.append(rhs)

        result = milp(
            c=-np.array(self.objective),  # milp minimises
            constraints=LinearConstraint(np.array(rows), lower, upper),
            integrality=np.ones(n),
            bounds=Bounds(0, 1),
            options={"time_limit": time_limit},
        )
        if not result.success:
            raise RuntimeError(f"HiGHS returned: {result.message}")
        chosen = [i for i, v in enumerate(result.x) if v > 0.5]
        return chosen, "scipy/HiGHS"
