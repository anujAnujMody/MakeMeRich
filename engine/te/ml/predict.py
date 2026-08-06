"""`ShadowMLHook` — the concrete `te.ml.gates.MLHook` implementation that
owns everything `te.engine.cycle` must never see directly: the trained
`MetaModel`, the `FeatureSpec`, the `BarStore`, and the raw probability `p`
`MetaModel.predict_proba()` returns. `cycle.py` only ever calls
`.evaluate()`, which returns an `MLInfluence` — `p` never leaves this
module's stack frame.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from te.data.barstore import BarStore
from te.ml.dataset import build_training_set
from te.ml.featurespec import FeatureSpec
from te.ml.gates import MaturityGate, MLInfluence, log_prediction
from te.ml.model import MetaModel


@dataclass
class ShadowMLHook:
    """Builds the inference-time feature row via
    `te.ml.dataset.build_training_set(..., for_inference=True)` — the SAME
    function training calls — predicts, gates, logs, and returns only the
    resulting `MLInfluence`."""

    model: MetaModel
    spec: FeatureSpec
    store: BarStore
    gate: MaturityGate
    session_factory: sessionmaker[Session]

    def evaluate(self, *, instrument: str, as_of: dt.datetime, cycle_id: int) -> MLInfluence:
        features = build_training_set(
            as_of, self.spec, self.store, self.session_factory, instrument=instrument, for_inference=True
        )
        p = self.model.predict_proba(features)
        # Read the stage ONCE — both the gating decision and the prediction
        # log below need it.
        stage = self.gate.current_stage()
        influence = self.gate.influence(p, stage=stage)
        log_prediction(
            self.session_factory,
            cycle_id=cycle_id,
            instrument=instrument,
            feature_spec_name=self.spec.name,
            feature_spec_version=self.spec.version,
            p=p,
            stage=stage,
            displayed=influence.displayed_verdict is not None,
            ts=as_of,
        )
        return influence
