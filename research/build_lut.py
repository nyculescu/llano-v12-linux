"""Fan recommendation LUT from a load x fan sweep (research/lut_sweep.py's CSV).

For each device and load: the recommended fan speed is the slowest one that cools within
MARGIN_C of the coolest speed measured at that load, without more than THROTTLE_PCT points
of extra thermal throttling. Its row is the temperature the device runs at with that speed
(so the LUT is read with the temperature as it is) and that speed. Rows are sorted by
temperature and made non-decreasing in speed. Between rows the speed is interpolated;
outside them it is the nearest row's (see recommend()).

The recommendation for the machine is the higher of the CPU's and the GPU's, so whichever
device is further up its own curve leads.

    python3 research/build_lut.py research/lut_sweep_DATE.csv [fan_lut.json]
"""
import csv
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

MARGIN_C = 1.5
THROTTLE_PCT = 2.0


def device_rows(cells):
    """[(temp, fan)] for one device's cells, monotone; and the per-load reasoning."""
    by_load = defaultdict(list)
    for c in cells:
        by_load[c['load']].append(c)
    rows, notes = [], []
    for load in sorted(by_load, reverse=True):
        runs = by_load[load]
        best = min(runs, key=lambda c: (c['temp'], -c['fan']))
        good = [c for c in runs if c['temp'] <= best['temp'] + MARGIN_C
                and c['throttle_pct'] <= best['throttle_pct'] + THROTTLE_PCT]
        pick = min(good, key=lambda c: c['fan'])
        rows.append((pick['temp'], pick['fan']))
        spread = max(c['temp'] for c in runs) - best['temp']
        notes.append({'load': load, 'fan': pick['fan'], 'temp': pick['temp'],
                      'coolest': best['temp'], 'fan_spread_c': round(spread, 1)})
    rows.sort()
    monotone, top = [], 0
    for temp, fan in rows:
        top = max(top, fan)
        monotone.append([temp, top])
    return monotone, notes


def recommend(lut, temps):
    """{'fan', 'device'} for {device: temperature}: the highest of the devices' speeds."""
    best = None
    for device, temp in temps.items():
        rows = lut['devices'].get(device, {}).get('rows')
        if not rows or temp is None:
            continue
        if temp <= rows[0][0]:
            fan = rows[0][1]
        elif temp >= rows[-1][0]:
            fan = rows[-1][1]
        else:
            for (t0, f0), (t1, f1) in zip(rows, rows[1:]):
                if t0 <= temp <= t1:
                    fan = f0 + (f1 - f0) * (temp - t0) / (t1 - t0) if t1 > t0 else max(f0, f1)
                    break
        if best is None or fan > best['fan']:
            best = {'fan': round(fan), 'device': device}
    return best


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / 'fan_lut.json'
    cells = defaultdict(list)
    with open(source) as f:
        for row in csv.DictReader(f):
            cells[row['device']].append({
                'load': int(row['load']), 'fan': int(row['fan']), 'temp': float(row['temp']),
                'throttle_pct': float(row['throttle_pct'] or 0),
            })
    lut = {
        'source': source.name,
        'built': datetime.date.today().isoformat(),
        'rule': f'slowest fan within {MARGIN_C} °C of the coolest at each load, '
                f'at most {THROTTLE_PCT} points more throttling; the higher of the devices leads',
        'devices': {},
    }
    for device, device_cells in cells.items():
        rows, notes = device_rows(device_cells)
        lut['devices'][device] = {'rows': rows, 'loads': notes}
    target.write_text(json.dumps(lut, indent=1) + '\n')
    print(json.dumps(lut, indent=1))
    print(f'wrote {target}')


if __name__ == '__main__':
    main()
