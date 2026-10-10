"""Noise model and limits for the llano V12 Ultra pad (NOISE.md).

The pad speaks percent; people hear rpm. PERCENT_RPM maps one to the other (an estimate until
measured against the pad's display), EAR_DB the rpm to the noise at the user's ears (measured
with a phone app, research/noise_2026-10-09.txt). The limits: a cap during quiet hours
(weekdays 22:00-07:00, 600 rpm) and a daytime cap (2000 rpm). `llano.py loud` lifts them until
the end of the current quiet hours (or for DAY_OVERRIDE_H hours by day).

Every value can be overridden in $XDG_CONFIG_HOME/llano/noise.json (same keys, lower case).
"""
import datetime
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
    # Monday = 0. A moment is quiet when its own weekday is listed and its time is at or after
    # `from` or before `to`.
    'quiet_days': [0, 1, 2, 3, 4],
    'quiet_from': '22:00',
    'quiet_to': '07:00',
    'quiet_max_rpm': 600,
    'day_max_rpm': 2000,
    'day_override_h': 2,
    # Bands for warnings: the highest rpm of each, then their names (NOISE.md).
    'bands': [[400, 'quiet'], [600, 'night'], [1000, 'audible'], [2000, 'loud'], [100000, 'very loud']],
}


def state_dir() -> Path:
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'llano'


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


def _hm(text):
    h, m = text.split(':')
    return datetime.time(int(h), int(m))


def quiet_window(now: datetime.datetime, p: dict | None = None):
    """(quiet now, when the current quiet or day stretch ends)."""
    p = p or policy()
    start, end = _hm(p['quiet_from']), _hm(p['quiet_to'])

    def quiet(t):
        return t.weekday() in p['quiet_days'] and (t.time() >= start or t.time() < end)

    is_quiet = quiet(now)
    t = now.replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60 // 15):  # the next change, in 15-minute steps (times are on the quarter)
        t += datetime.timedelta(minutes=15 - t.minute % 15)
        if quiet(t) != is_quiet:
            return is_quiet, t
    return is_quiet, None


def override_until() -> float | None:
    try:
        until = float((state_dir() / 'loud-until').read_text())
    except (OSError, ValueError):
        return None
    return until if until > datetime.datetime.now().timestamp() else None


def set_override(on: bool) -> float | None:
    path = state_dir() / 'loud-until'
    if not on:
        path.unlink(missing_ok=True)
        return None
    p = policy()
    now = datetime.datetime.now()
    is_quiet, change = quiet_window(now, p)
    until = change if is_quiet and change else now + datetime.timedelta(hours=p['day_override_h'])
    state_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(f'{until.timestamp():.0f}\n')
    return until.timestamp()


def limit(now: datetime.datetime | None = None) -> dict:
    """The cap in force: {quiet, until (ISO), max_rpm, max_percent, override_until}."""
    p = policy()
    now = now or datetime.datetime.now()
    is_quiet, change = quiet_window(now, p)
    over = override_until()
    max_rpm = None if over else (p['quiet_max_rpm'] if is_quiet else p['day_max_rpm'])
    return {
        'quiet': is_quiet,
        'until': change.isoformat(timespec='minutes') if change else None,
        'max_rpm': max_rpm,
        'max_percent': 100 if max_rpm is None else percent(max_rpm, p),
        'override_until': over,
        'mapping_measured': p['percent_rpm_measured'],
    }


def describe(speed_percent: int, off: bool = False) -> dict:
    """rpm, dB at the ears and band for a speed; off (the pad switched off) is 0 rpm."""
    p = policy()
    r = 0 if off else rpm(speed_percent, p)
    return {'rpm': r, 'ear_db': ear_db(r, p), 'band': band(r, p), 'limit': limit()}
