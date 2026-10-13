"""Noise model and limits for the llano V12 Ultra pad (NOISE.md).

The pad speaks percent; people hear rpm. PERCENT_RPM maps one to the other (an estimate until
measured against the pad's display), EAR_DB the rpm to the noise at the user's ears (measured
with a phone app, research/noise_2026-10-09.txt). Froonty's Quick Settings slider displays these
estimates while setting the pad speed directly; there is no clock-based cap.

Every value can be overridden in $XDG_CONFIG_HOME/llano/noise.json (same keys, lower case).
"""
import json
import os
from pathlib import Path

DEFAULTS = {
    # [percent, rpm]: rpm ~ 300 + 25 x percent from 2 % up; 0 % and 1 % are the pad's lowest
    # level, 300 rpm (only `llano.py off` stops the fan). Measured on the pad's display: 0 % and
    # 1 % 300 rpm, 2 % 350 rpm, 12 % 600 rpm (2026-10-09/10).
    # Assumed: 100 % is the pad's top level, 2800 rpm (consistent with the README's 50 % ~ 1550 rpm).
    'percent_rpm': [[0, 300], [1, 300], [2, 350], [12, 600], [100, 2800]],
    'percent_rpm_measured': 'partly',
    # [rpm, dB at the user's ears], 2026-10-09; the laptop alone was 37.5 dB.
    'ear_db': [[0, 37.5], [300, 40.5], [400, 43.0], [500, 45.5], [600, 49.5], [700, 51.5],
               [800, 53.5], [900, 55.5], [1000, 57.0], [1175, 62.0]],
    # Bands for warnings: the highest rpm of each, then their names (NOISE.md).
    'bands': [[400, 'quiet'], [600, 'night'], [1000, 'audible'], [2000, 'loud'], [100000, 'very loud']],
}


def policy() -> dict:
    p = dict(DEFAULTS)
    path = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'llano/noise.json'
    try:
        p.update(json.loads(path.read_text()))
    except (OSError, ValueError):
        pass
    return p


def _interp(points, x):
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
    (x0, y0), (x1, y1) = points[-2], points[-1]
    return y1 + (y1 - y0) * (x - x1) / (x1 - x0) if x1 > x0 else y1  # beyond the last: extended


def rpm(percent: float, p: dict | None = None) -> int:
    return round(_interp((p or policy())['percent_rpm'], percent))


def percent(rpm_value: float, p: dict | None = None) -> int:
    """The highest whole percent whose rpm stays at or under rpm_value."""
    p = p or policy()
    return max((pct for pct in range(101) if rpm(pct, p) <= rpm_value), default=0)


def ear_db(rpm_value: float, p: dict | None = None) -> float:
    """dB at the ears; above the last reading, +30 dB per tenfold rpm (research/noise_2026-10-09.txt)."""
    import math
    points = (p or policy())['ear_db']
    if rpm_value <= points[-1][0]:
        return round(_interp(points, rpm_value), 1)
    top_rpm, top_db = points[-1]
    return round(top_db + 30 * math.log10(rpm_value / top_rpm), 1)


def band(rpm_value: float, p: dict | None = None) -> str:
    return next(name for top, name in (p or policy())['bands'] if rpm_value <= top)


def limit() -> dict:
    """Compatibility shape for noise status; the speed is selected directly."""
    p = policy()
    return {
        'quiet': False,
        'until': None,
        'max_rpm': None,
        'max_percent': 100,
        'override_until': None,
        'mapping_measured': p['percent_rpm_measured'],
    }


def describe(speed_percent: int, off: bool = False) -> dict:
    """rpm, dB at the ears and band for a speed; off (the pad switched off) is 0 rpm."""
    p = policy()
    r = 0 if off else rpm(speed_percent, p)
    return {'rpm': r, 'ear_db': ear_db(r, p), 'band': band(r, p), 'limit': limit()}
