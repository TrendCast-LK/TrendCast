"""Load shapes: how the number of virtual users changes over time.

locustfile.py exposes exactly one of these, chosen by LOAD_SHAPE. Every value is
overridable through the environment (defaults in parentheses).

  baseline   1 user for LOAD_BASELINE_SECONDS (120): single-user response times
  normal     ramp to LOAD_NORMAL_USERS (20) over 60 s, hold LOAD_NORMAL_MINUTES (10)
  ramp       +LOAD_RAMP_STEP (10) users every LOAD_RAMP_STEP_SECONDS (120) up to
             LOAD_RAMP_MAX (150): finds where latency or errors start
  peaks      regular peaks: LOAD_PEAK_CYCLES (3) x [low 60 s, climb 30 s, hold 60 s, fall 30 s] between
             LOAD_PEAK_LOW (10) and LOAD_PEAK_HIGH (80) users; LOAD_PEAK_PHASE_SCALE shrinks the phases
  spike      LOAD_SPIKE_LOW (10) users, jump to LOAD_SPIKE_HIGH (100) in ~10 s, hold, drop back, watch recovery
  soak       LOAD_SOAK_USERS (30) for LOAD_SOAK_MINUTES (30): leaks and slow degradation
"""

from __future__ import annotations

import os

from locust import LoadTestShape


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


class Baseline(LoadTestShape):
    def tick(self):
        if self.get_run_time() > _i("LOAD_BASELINE_SECONDS", 120):
            return None
        return 1, 1


class Normal(LoadTestShape):
    def tick(self):
        users = _i("LOAD_NORMAL_USERS", 20)
        ramp = 60
        if self.get_run_time() > ramp + _f("LOAD_NORMAL_MINUTES", 10) * 60:
            return None
        return users, users / ramp


class Ramp(LoadTestShape):
    def tick(self):
        step, seconds, maximum = _i("LOAD_RAMP_STEP", 10), _i("LOAD_RAMP_STEP_SECONDS", 120), _i("LOAD_RAMP_MAX", 150)
        t = self.get_run_time()
        steps = int(maximum / step)
        if t > steps * seconds:
            return None
        users = min(maximum, step * (int(t // seconds) + 1))
        return users, step


class Peaks(LoadTestShape):
    """low -> climb -> hold -> fall, repeated: regular, predictable peaks."""

    def tick(self):
        low, high = _i("LOAD_PEAK_LOW", 10), _i("LOAD_PEAK_HIGH", 80)
        scale = _f("LOAD_PEAK_PHASE_SCALE", 1.0)
        low_s, climb_s, hold_s, fall_s = 60 * scale, 30 * scale, 60 * scale, 30 * scale
        cycle = low_s + climb_s + hold_s + fall_s
        t = self.get_run_time()
        if t > _i("LOAD_PEAK_CYCLES", 3) * cycle:
            return None
        phase = t % cycle
        if phase < low_s:
            users = low
        elif phase < low_s + climb_s:
            users = low + (high - low) * (phase - low_s) / climb_s
        elif phase < low_s + climb_s + hold_s:
            users = high
        else:
            users = high - (high - low) * (phase - low_s - climb_s - hold_s) / fall_s
        return max(1, round(users)), 10


class Spike(LoadTestShape):
    """Steady low load, a near-instant jump, a hold, then back down to check recovery."""

    def tick(self):
        low, high = _i("LOAD_SPIKE_LOW", 10), _i("LOAD_SPIKE_HIGH", 100)
        warm, hold, recover = _i("LOAD_SPIKE_WARMUP", 60), _i("LOAD_SPIKE_HOLD", 120), _i("LOAD_SPIKE_RECOVERY", 180)
        t = self.get_run_time()
        if t < warm:
            return low, low
        if t < warm + hold:
            return high, max(1, (high - low) // 10)  # ~10 s to reach the peak
        if t < warm + hold + recover:
            return low, high  # drop immediately
        return None


class Soak(LoadTestShape):
    def tick(self):
        if self.get_run_time() > _f("LOAD_SOAK_MINUTES", 30) * 60:
            return None
        return _i("LOAD_SOAK_USERS", 30), 2


SHAPES = {"baseline": Baseline, "normal": Normal, "ramp": Ramp, "peaks": Peaks, "spike": Spike, "soak": Soak}
