"""Sensirion gas index algorithm (VOC / NOx index, 1..500) and the derived
``<node>.voc-index`` metric that puts every VOC sensor on the same scale.

The new sensors report a VOC index natively: 100 is "the usual air in this
room", 500 is as bad as it gets, and the scale adapts to each sensor's own
baseline over about 12 h. The older Airthings devices report VOC in ppb. To
chart them together, their ppb series is run through the very same algorithm
here, so the whole fleet shares one relative scale.

``GasIndexAlgorithm`` is a line-for-line port of Sensirion's reference C
implementation (gas-index-algorithm 3.2.0, BSD-3-Clause, see
tests/fixtures/sensirion/). Only the per-step sampling interval differs: the
reference fixes it at construction, this port recomputes the interval-derived
coefficients whenever the time since the previous sample changes, which is
identical when samples arrive at a constant rate.

The reference takes raw MOx sensor ticks, which fall as VOC rises. A ppb series
is fed in as ``SRAW_BASE - ppb``: a falling "tick" signal in the reference's
working range, so the VOC branch (including its sign flip) runs unchanged. The
std bonus of 220 then applies in ppb, which on the Airthings series (12 h std
70..90 ppb) maps a +300 ppb excursion to roughly index 260 and the weekly
maxima to about 450.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

ALGORITHM_TYPE_VOC = 0
ALGORITHM_TYPE_NOX = 1

DEFAULT_SAMPLING_INTERVAL = 1.0
INITIAL_BLACKOUT = 45.0
INDEX_GAIN = 230.0
SRAW_STD_INITIAL = 50.0
SRAW_STD_BONUS_VOC = 220.0
SRAW_STD_NOX = 2000.0
TAU_MEAN_HOURS = 12.0
TAU_VARIANCE_HOURS = 12.0
TAU_INITIAL_MEAN_VOC = 20.0
TAU_INITIAL_MEAN_NOX = 1200.0
INIT_DURATION_MEAN_VOC = 3600.0 * 0.75
INIT_DURATION_MEAN_NOX = 3600.0 * 4.75
INIT_TRANSITION_MEAN = 0.01
TAU_INITIAL_VARIANCE = 2500.0
INIT_DURATION_VARIANCE_VOC = 3600.0 * 1.45
INIT_DURATION_VARIANCE_NOX = 3600.0 * 5.70
INIT_TRANSITION_VARIANCE = 0.01
GATING_THRESHOLD_VOC = 340.0
GATING_THRESHOLD_NOX = 30.0
GATING_THRESHOLD_INITIAL = 510.0
GATING_THRESHOLD_TRANSITION = 0.09
GATING_VOC_MAX_DURATION_MINUTES = 60.0 * 3.0
GATING_NOX_MAX_DURATION_MINUTES = 60.0 * 12.0
GATING_MAX_RATIO = 0.3
SIGMOID_L = 500.0
SIGMOID_K_VOC = -0.0065
SIGMOID_X0_VOC = 213.0
SIGMOID_K_NOX = -0.0101
SIGMOID_X0_NOX = 614.0
VOC_INDEX_OFFSET_DEFAULT = 100.0
NOX_INDEX_OFFSET_DEFAULT = 1.0
LP_TAU_FAST = 20.0
LP_TAU_SLOW = 500.0
LP_ALPHA = -0.2
VOC_SRAW_MINIMUM = 20000
NOX_SRAW_MINIMUM = 10000
PERSISTENCE_UPTIME_GAMMA = 3.0 * 3600.0
GAMMA_SCALING = 64.0
ADDITIONAL_GAMMA_MEAN_SCALING = 8.0
FIX16_MAX = 32767.0


class GasIndexAlgorithm:
    """Port of GasIndexAlgorithmParams + GasIndexAlgorithm_* from the reference."""

    def __init__(self, algorithm_type: int = ALGORITHM_TYPE_VOC,
                 sampling_interval: float = DEFAULT_SAMPLING_INTERVAL) -> None:
        self.algorithm_type = algorithm_type
        if algorithm_type == ALGORITHM_TYPE_NOX:
            self.index_offset = NOX_INDEX_OFFSET_DEFAULT
            self.sraw_minimum = NOX_SRAW_MINIMUM
            self.gating_max_duration_minutes = GATING_NOX_MAX_DURATION_MINUTES
            self.init_duration_mean = INIT_DURATION_MEAN_NOX
            self.init_duration_variance = INIT_DURATION_VARIANCE_NOX
            self.gating_threshold = GATING_THRESHOLD_NOX
        else:
            self.index_offset = VOC_INDEX_OFFSET_DEFAULT
            self.sraw_minimum = VOC_SRAW_MINIMUM
            self.gating_max_duration_minutes = GATING_VOC_MAX_DURATION_MINUTES
            self.init_duration_mean = INIT_DURATION_MEAN_VOC
            self.init_duration_variance = INIT_DURATION_VARIANCE_VOC
            self.gating_threshold = GATING_THRESHOLD_VOC
        self.index_gain = INDEX_GAIN
        self.tau_mean_hours = TAU_MEAN_HOURS
        self.tau_variance_hours = TAU_VARIANCE_HOURS
        self.sraw_std_initial = SRAW_STD_INITIAL
        self.sampling_interval = sampling_interval
        self.reset()

    # -- lifecycle (GasIndexAlgorithm_reset / __init_instances) --------------
    def reset(self) -> None:
        self.uptime = 0.0
        self.sraw = 0.0
        self.gas_index = 0.0
        self._init_instances()

    def _init_instances(self) -> None:
        self._mve_set_parameters()
        self._mox_set_parameters(self._mve_get_std(), self._mve_get_mean())
        if self.algorithm_type == ALGORITHM_TYPE_NOX:
            self._sigmoid_scaled_set_parameters(SIGMOID_X0_NOX, SIGMOID_K_NOX, NOX_INDEX_OFFSET_DEFAULT)
        else:
            self._sigmoid_scaled_set_parameters(SIGMOID_X0_VOC, SIGMOID_K_VOC, VOC_INDEX_OFFSET_DEFAULT)
        self._lowpass_set_parameters()

    def _set_interval(self, sampling_interval: float) -> None:
        """Re-derive every coefficient that depends on the sampling interval.

        Mirrors what the reference computes once in ``init_with_sampling_interval``;
        the estimator's state (mean, std, uptimes, gating) is left untouched.
        """
        self.sampling_interval = sampling_interval
        self._mve_set_gammas()
        self._lowpass_set_coefficients()

    def get_states(self) -> Tuple[float, float]:
        return self._mve_get_mean(), self._mve_get_std()

    def set_states(self, mean: float, std: float) -> None:
        self._mve_set_states(mean, std, PERSISTENCE_UPTIME_GAMMA)
        self._mox_set_parameters(self._mve_get_std(), self._mve_get_mean())
        self.sraw = mean

    # -- GasIndexAlgorithm_process ------------------------------------------
    def process(self, sraw: int, sampling_interval: Optional[float] = None) -> int:
        """One raw sample in, the index (0 during the initial blackout) out."""
        if sampling_interval is not None and sampling_interval != self.sampling_interval:
            self._set_interval(sampling_interval)
        if self.uptime <= INITIAL_BLACKOUT:
            self.uptime = self.uptime + self.sampling_interval
        else:
            if 0 < sraw < 65000:
                if sraw < self.sraw_minimum + 1:
                    sraw = self.sraw_minimum + 1
                elif sraw > self.sraw_minimum + 32767:
                    sraw = self.sraw_minimum + 32767
                self.sraw = float(sraw - self.sraw_minimum)
            if self.algorithm_type == ALGORITHM_TYPE_VOC or self._mve_is_initialized():
                self.gas_index = self._mox_process(self.sraw)
                self.gas_index = self._sigmoid_scaled_process(self.gas_index)
            else:
                self.gas_index = self.index_offset
            self.gas_index = self._lowpass_process(self.gas_index)
            if self.gas_index < 0.5:
                self.gas_index = 0.5
            if self.sraw > 0.0:
                self._mve_process(self.sraw)
                self._mox_set_parameters(self._mve_get_std(), self._mve_get_mean())
        return int(self.gas_index + 0.5)

    # -- mean/variance estimator --------------------------------------------
    def _mve_set_parameters(self) -> None:
        self._mve_initialized = False
        self._mve_mean = 0.0
        self._mve_sraw_offset = 0.0
        self._mve_std = self.sraw_std_initial
        self._mve_set_gammas()
        self._mve_gamma_mean_eff = 0.0
        self._mve_gamma_variance_eff = 0.0
        self._mve_uptime_gamma = 0.0
        self._mve_uptime_gating = 0.0
        self._mve_gating_duration_minutes = 0.0

    def _mve_set_gammas(self) -> None:
        si = self.sampling_interval
        self._mve_gamma_mean = ((ADDITIONAL_GAMMA_MEAN_SCALING * GAMMA_SCALING) * (si / 3600.0)) / \
            (self.tau_mean_hours + (si / 3600.0))
        self._mve_gamma_variance = (GAMMA_SCALING * (si / 3600.0)) / (self.tau_variance_hours + (si / 3600.0))
        tau_initial_mean = TAU_INITIAL_MEAN_NOX if self.algorithm_type == ALGORITHM_TYPE_NOX else TAU_INITIAL_MEAN_VOC
        self._mve_gamma_initial_mean = ((ADDITIONAL_GAMMA_MEAN_SCALING * GAMMA_SCALING) * si) / (tau_initial_mean + si)
        self._mve_gamma_initial_variance = (GAMMA_SCALING * si) / (TAU_INITIAL_VARIANCE + si)

    def _mve_set_states(self, mean: float, std: float, uptime_gamma: float) -> None:
        self._mve_mean = mean
        self._mve_std = std
        self._mve_uptime_gamma = uptime_gamma
        self._mve_initialized = True

    def _mve_get_std(self) -> float:
        return self._mve_std

    def _mve_get_mean(self) -> float:
        return self._mve_mean + self._mve_sraw_offset

    def _mve_is_initialized(self) -> bool:
        return self._mve_initialized

    @staticmethod
    def _sigmoid(sample: float, x0: float, k: float) -> float:
        x = k * (sample - x0)
        if x < -50.0:
            return 1.0
        if x > 50.0:
            return 0.0
        return 1.0 / (1.0 + math.exp(x))

    def _mve_calculate_gamma(self) -> None:
        si = self.sampling_interval
        uptime_limit = FIX16_MAX - si
        if self._mve_uptime_gamma < uptime_limit:
            self._mve_uptime_gamma = self._mve_uptime_gamma + si
        if self._mve_uptime_gating < uptime_limit:
            self._mve_uptime_gating = self._mve_uptime_gating + si
        sigmoid_gamma_mean = self._sigmoid(self._mve_uptime_gamma, self.init_duration_mean, INIT_TRANSITION_MEAN)
        gamma_mean = self._mve_gamma_mean + ((self._mve_gamma_initial_mean - self._mve_gamma_mean) * sigmoid_gamma_mean)
        gating_threshold_mean = self.gating_threshold + (
            (GATING_THRESHOLD_INITIAL - self.gating_threshold)
            * self._sigmoid(self._mve_uptime_gating, self.init_duration_mean, INIT_TRANSITION_MEAN))
        sigmoid_gating_mean = self._sigmoid(self.gas_index, gating_threshold_mean, GATING_THRESHOLD_TRANSITION)
        self._mve_gamma_mean_eff = sigmoid_gating_mean * gamma_mean
        sigmoid_gamma_variance = self._sigmoid(self._mve_uptime_gamma, self.init_duration_variance,
                                               INIT_TRANSITION_VARIANCE)
        gamma_variance = self._mve_gamma_variance + (
            (self._mve_gamma_initial_variance - self._mve_gamma_variance) * (sigmoid_gamma_variance - sigmoid_gamma_mean))
        gating_threshold_variance = self.gating_threshold + (
            (GATING_THRESHOLD_INITIAL - self.gating_threshold)
            * self._sigmoid(self._mve_uptime_gating, self.init_duration_variance, INIT_TRANSITION_VARIANCE))
        sigmoid_gating_variance = self._sigmoid(self.gas_index, gating_threshold_variance, GATING_THRESHOLD_TRANSITION)
        self._mve_gamma_variance_eff = sigmoid_gating_variance * gamma_variance
        self._mve_gating_duration_minutes = self._mve_gating_duration_minutes + (
            (si / 60.0) * (((1.0 - sigmoid_gating_mean) * (1.0 + GATING_MAX_RATIO)) - GATING_MAX_RATIO))
        if self._mve_gating_duration_minutes < 0.0:
            self._mve_gating_duration_minutes = 0.0
        if self._mve_gating_duration_minutes > self.gating_max_duration_minutes:
            self._mve_uptime_gating = 0.0

    def _mve_process(self, sraw: float) -> None:
        if not self._mve_initialized:
            self._mve_initialized = True
            self._mve_sraw_offset = sraw
            self._mve_mean = 0.0
            return
        if self._mve_mean >= 100.0 or self._mve_mean <= -100.0:
            self._mve_sraw_offset = self._mve_sraw_offset + self._mve_mean
            self._mve_mean = 0.0
        sraw = sraw - self._mve_sraw_offset
        self._mve_calculate_gamma()
        delta_sgp = (sraw - self._mve_mean) / GAMMA_SCALING
        c = self._mve_std - delta_sgp if delta_sgp < 0.0 else self._mve_std + delta_sgp
        additional_scaling = 1.0
        if c > 1440.0:
            additional_scaling = (c / 1440.0) * (c / 1440.0)
        self._mve_std = math.sqrt(additional_scaling * (GAMMA_SCALING - self._mve_gamma_variance_eff)) * math.sqrt(
            (self._mve_std * (self._mve_std / (GAMMA_SCALING * additional_scaling)))
            + (((self._mve_gamma_variance_eff * delta_sgp) / additional_scaling) * delta_sgp))
        self._mve_mean = self._mve_mean + ((self._mve_gamma_mean_eff * delta_sgp) / ADDITIONAL_GAMMA_MEAN_SCALING)

    # -- MOx model -----------------------------------------------------------
    def _mox_set_parameters(self, sraw_std: float, sraw_mean: float) -> None:
        self._mox_sraw_std = sraw_std
        self._mox_sraw_mean = sraw_mean

    def _mox_process(self, sraw: float) -> float:
        if self.algorithm_type == ALGORITHM_TYPE_NOX:
            return ((sraw - self._mox_sraw_mean) / SRAW_STD_NOX) * self.index_gain
        return ((sraw - self._mox_sraw_mean) / (-1.0 * (self._mox_sraw_std + SRAW_STD_BONUS_VOC))) * self.index_gain

    # -- scaled sigmoid ------------------------------------------------------
    def _sigmoid_scaled_set_parameters(self, x0: float, k: float, offset_default: float) -> None:
        self._ss_k = k
        self._ss_x0 = x0
        self._ss_offset_default = offset_default

    def _sigmoid_scaled_process(self, sample: float) -> float:
        x = self._ss_k * (sample - self._ss_x0)
        if x < -50.0:
            return SIGMOID_L
        if x > 50.0:
            return 0.0
        if sample >= 0.0:
            if self._ss_offset_default == 1.0:
                shift = (500.0 / 499.0) * (1.0 - self.index_offset)
            else:
                shift = (SIGMOID_L - (5.0 * self.index_offset)) / 4.0
            return ((SIGMOID_L + shift) / (1.0 + math.exp(x))) - shift
        return (self.index_offset / self._ss_offset_default) * (SIGMOID_L / (1.0 + math.exp(x)))

    # -- adaptive lowpass ----------------------------------------------------
    def _lowpass_set_parameters(self) -> None:
        self._lowpass_set_coefficients()
        self._lp_initialized = False

    def _lowpass_set_coefficients(self) -> None:
        si = self.sampling_interval
        self._lp_a1 = si / (LP_TAU_FAST + si)
        self._lp_a2 = si / (LP_TAU_SLOW + si)

    def _lowpass_process(self, sample: float) -> float:
        if not self._lp_initialized:
            self._lp_x1 = sample
            self._lp_x2 = sample
            self._lp_x3 = sample
            self._lp_initialized = True
        self._lp_x1 = ((1.0 - self._lp_a1) * self._lp_x1) + (self._lp_a1 * sample)
        self._lp_x2 = ((1.0 - self._lp_a2) * self._lp_x2) + (self._lp_a2 * sample)
        abs_delta = abs(self._lp_x1 - self._lp_x2)
        f1 = math.exp(LP_ALPHA * abs_delta)
        tau_a = ((LP_TAU_SLOW - LP_TAU_FAST) * f1) + LP_TAU_FAST
        a3 = self.sampling_interval / (self.sampling_interval + tau_a)
        self._lp_x3 = ((1.0 - a3) * self._lp_x3) + (a3 * sample)
        return self._lp_x3


# -- the derived voc-index metric ----------------------------------------------

DERIVED_SENSOR = "voc-index"
SRAW_BASE = 50000          # ppb p enters the VOC branch as raw ticks SRAW_BASE - p
MIN_STEP_SEC = 1.0
MAX_STEP_SEC = 600.0       # a long silence counts as ten minutes of adaptation, not hours
DEFAULT_STEP_SEC = 60.0    # the archive's cadence: used for a node's very first sample
PRIME_HOURS = 48           # history replayed on startup so the estimator is settled


def is_ppb_voc(sensor: str, units_display: Optional[str]) -> bool:
    """A ``voc`` sensor is ppb unless the device says its unit is an index."""
    return sensor.lower() == "voc" and "index" not in (units_display or "").lower()


def sraw_from_ppb(ppb: float) -> int:
    return int(round(SRAW_BASE - max(0.0, ppb)))


def derived_meta(node: str) -> dict:
    return {"node": node, "sensor": DERIVED_SENSOR, "unitsDisplay": "VOC index, derived from ppb",
            "valueType": "other", "derived": True}


@dataclass
class _NodeState:
    engine: GasIndexAlgorithm = field(default_factory=GasIndexAlgorithm)
    last_ms: Optional[int] = None


class VocIndexer:
    """Keeps one running algorithm per node and adds ``<node>.voc-index`` to a sample.

    Samples must be applied oldest first; anything not newer than the node's last
    sample is ignored (a duplicate or a late backfill row, which ``reindex`` covers).
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, _NodeState] = {}

    def apply(self, time_ms: int, metrics: Dict[str, float], metrics_meta: Optional[dict] = None) -> Dict[str, float]:
        """Add the derived metrics to ``metrics`` in place; returns what was added."""
        added: Dict[str, float] = {}
        meta = metrics_meta or {}
        for key, value in list(metrics.items()):
            dot = key.find(".")
            if dot <= 0 or not isinstance(value, (int, float)):
                continue
            node, sensor = key[:dot], key[dot + 1:]
            if not is_ppb_voc(sensor, (meta.get(key) or {}).get("unitsDisplay")):
                continue
            state = self.nodes.setdefault(node, _NodeState())
            if state.last_ms is not None and time_ms <= state.last_ms:
                continue
            step = DEFAULT_STEP_SEC if state.last_ms is None else (time_ms - state.last_ms) / 1000.0
            step = min(MAX_STEP_SEC, max(MIN_STEP_SEC, step))
            state.last_ms = time_ms
            index = state.engine.process(sraw_from_ppb(float(value)), step)
            if index > 0:  # 0 = still in the initial blackout
                metrics[f"{node}.{DERIVED_SENSOR}"] = index
                added[f"{node}.{DERIVED_SENSOR}"] = index
        return added

    def replay(self, rows: Iterable[Tuple[int, Dict[str, float]]], metrics_meta: Optional[dict] = None) -> None:
        for t, metrics in rows:
            self.apply(t, dict(metrics), metrics_meta)

    def prime(self, db, now_ms: int, metrics_meta: Optional[dict] = None, hours: int = PRIME_HOURS) -> None:
        self.replay(db.iter_rows(now_ms - hours * 3600_000, now_ms), metrics_meta)


def reindex(db, from_ms: int, to_ms: int, metrics_meta: Optional[dict] = None) -> int:
    """Recompute ``voc-index`` for every stored row in [from_ms, to_ms], oldest first.

    The estimator is primed on the PRIME_HOURS before ``from_ms`` so the first rows
    of the range are not in the learning phase. Returns the number of rows changed.
    """
    indexer = VocIndexer()
    indexer.prime(db, from_ms - 1, metrics_meta)
    changed: List[Tuple[int, Dict[str, float]]] = []
    for t, metrics in db.iter_rows(from_ms, to_ms):
        before = {k: v for k, v in metrics.items() if k.endswith("." + DERIVED_SENSOR)}
        for k in before:
            del metrics[k]
        indexer.apply(t, metrics, metrics_meta)
        after = {k: v for k, v in metrics.items() if k.endswith("." + DERIVED_SENSOR)}
        if after != before:
            changed.append((t, metrics))
    if changed:
        db.update_metrics(changed)
    return len(changed)
