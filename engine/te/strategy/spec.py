"""How a strategy describes ITSELF — name, plain-English explanation, and
its tunable settings — so that nothing outside the strategy file needs
changing when a new one is added.

### Why this exists

Adding a strategy used to mean editing `te/strategy/registry.py`'s hand-kept
dict, and the dashboard had no way to learn that a strategy existed at all
(`/api/strategies` was a Phase-0 stub returning `[]`). That makes every new
rule a multi-file change and puts a ceiling on how many can realistically be
tried — which is the opposite of what this project needs, because the honest
conclusion so far is that we have not searched anywhere near widely enough.

A `StrategySpec` is a plain value: no I/O, no imports outside `te.domain`,
safe to serialise straight to the API. The dashboard renders a settings form
from `params` without knowing anything about the strategy.

### Why parameters are DECLARED rather than free-form

`params` is a tuple of `ParamSpec`, each with a type, a default, and bounds.
Three things depend on that being declarative rather than a loose dict:

* the dashboard can render an editable control with the right widget and
  sensible limits, for a strategy it has never seen;
* a sweep can enumerate the search space without a human writing it out
  again per strategy — and, critically, can COUNT the combinations it tried,
  which is what `te.ml.metrics.deflated_sharpe_ratio` needs to discount a
  result for how hard we looked;
* an out-of-range value is rejected at the boundary instead of producing a
  quietly nonsensical backtest.

This mirrors how Freqtrade declares `IntParameter`/`DecimalParameter` on the
strategy class: bounds live next to the rule they belong to, because that is
the only place anyone knows what a sane range actually is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: Broad grouping, used purely for presentation — the dashboard shows
#: strategies bucketed by this so a list of thirty stays readable.
#:
#: `control` is deliberately one of them. `random_entry` is not a strategy
#: anyone should trade; it is the yardstick every other strategy is measured
#: against, and giving it a family of its own stops it being read as a
#: recommendation while keeping it permanently visible.
StrategyFamily = Literal["breakout", "fade", "trend", "levels", "timing", "control"]

ParamType = Literal["int", "decimal", "bool", "choice"]


@dataclass(frozen=True)
class ParamSpec:
    """One tunable setting on a strategy.

    `low`/`high` are REQUIRED for numeric params and are not decoration: they
    are the search space a sweep enumerates and the limits the dashboard
    clamps to. A parameter with no stated range is one nobody has thought
    about, and it would silently widen every search that touches it.
    """

    name: str
    kind: ParamType
    default: Any
    #: Plain-English, shown in the UI. Written for a non-programmer, because
    #: the person adjusting these is not the person who wrote them.
    description: str
    low: float | None = None
    high: float | None = None
    #: Allowed values when `kind == "choice"`.
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind in ("int", "decimal") and (self.low is None or self.high is None):
            raise ValueError(f"numeric param {self.name!r} must declare low and high — an unbounded search space")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"choice param {self.name!r} must declare its choices")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"param {self.name!r} has low > high")

    def clamp(self, value: Any) -> Any:
        """The value, forced inside the declared range.

        Clamping rather than raising because this sits on the path from a UI
        slider and a config file, where an out-of-range number is a mistake
        to correct, not a reason to halt a trading engine.
        """
        if self.kind == "int":
            return int(min(max(float(value), float(self.low or 0)), float(self.high or 0)))
        if self.kind == "decimal":
            return float(min(max(float(value), float(self.low or 0)), float(self.high or 0)))
        if self.kind == "bool":
            return bool(value)
        return value if value in self.choices else self.default


@dataclass(frozen=True)
class StrategySpec:
    """Everything the rest of the system needs to know about a strategy
    without importing it."""

    #: Stable identifier. It is what gets written into `cycle_evaluations`
    #: and what a backtest is filed under, so renaming one orphans its
    #: recorded history — treat as permanent once it has run.
    name: str
    family: StrategyFamily
    #: One line, plain English, no jargon. This is what the user reads on
    #: the Strategies page to decide whether they understand what it does.
    summary: str
    params: tuple[ParamSpec, ...] = ()
    #: `False` keeps a strategy visible and backtestable but never live. The
    #: control belongs here: it must be measurable and must never trade.
    tradeable: bool = True

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def resolve(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Declared defaults with `overrides` applied and clamped.

        Unknown keys are IGNORED rather than raising: a stored config
        outlives the strategy version that wrote it, and a parameter removed
        in a later revision must not stop the strategy loading.
        """
        resolved = self.defaults()
        by_name = {p.name: p for p in self.params}
        for key, value in (overrides or {}).items():
            if key in by_name:
                resolved[key] = by_name[key].clamp(value)
        return resolved

    def search_space_size(self, *, steps: int = 3) -> int:
        """How many configurations a sweep of this strategy would try.

        Exists so the trial count fed to the deflated Sharpe ratio is
        derived from the search that was actually run, rather than typed in
        by hand and forgotten. Under-counting trials is the single easiest
        way to make an overfitted result look significant.
        """
        total = 1
        for param in self.params:
            if param.kind == "bool":
                total *= 2
            elif param.kind == "choice":
                total *= max(1, len(param.choices))
            else:
                total *= max(1, steps)
        return total
