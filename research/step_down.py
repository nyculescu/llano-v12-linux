"""Sustained load stepped down 100 % -> 0 % in 10 % steps (CPU, GPU, or both at once), pad left as it is.

CPU load: whole busy threads (100 % = every logical CPU, 90 % = 90 % of them, ...), which holds
steadier than a duty cycle. GPU load: research/gpu_stress at that duty cycle. Each step holds
HOLD_S, discards the first SETTLE_S and averages the rest: CPU package and GPU temperatures,
clocks, utilization, GPU power, CPU throttling, and each temperature's drift over the averaged
part (°C per minute: near 0 means it had settled). Writes research/step_down_DATE.csv.
Does not touch the pad (it records its state); refuses to start while the CPU is busy. The GPU
is not required to be idle (an editor rendering on it is normal use): its baseline is printed.

    python3 research/step_down.py [cpu] [gpu] [both]     # both: CPU and GPU at the same level together
"""
import csv
import datetime
import multiprocessing as mp
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from llano import Pad  # noqa: E402
from llanod import cpu_temp  # noqa: E402
from lut_sweep import GPU_ABORT_C, GPU_STRESS, THROTTLE_MS, cpu_times, cpu_util, gpu_sample, mean_clock_mhz  # noqa: E402

LEVELS = list(range(100, -1, -10))
HOLD_S, SETTLE_S, SAMPLE_S = 45, 30, 1.5
FIELDS = ['device', 'load', 'cpu_c', 'gpu_c', 'cpu_drift_c_min', 'gpu_drift_c_min', 'cpu_util', 'gpu_util',
          'cpu_mhz', 'gpu_mhz', 'gpu_w', 'cpu_throttle_pct', 'pad']


def spin(stop):
    x = 0
    while not stop.is_set():
        for _ in range(100000):
            x = (x * 1103515245 + 12345) & 0xFFFFFFFF


def slope(ts, ys):
    """Least-squares slope, per minute."""
    mt, my = statistics.mean(ts), statistics.mean(ys)
    den = sum((t - mt) ** 2 for t in ts)
    return 60 * sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / den if den else 0.0


def hold(device, level, pad_state):
    t0 = time.monotonic()
    while time.monotonic() - t0 < SETTLE_S:
        if device == 'gpu' and gpu_sample()[0] >= GPU_ABORT_C:
            raise RuntimeError('GPU too hot, aborting')
        time.sleep(SAMPLE_S)
    rows, cpu0, thr0, t1 = [], cpu_times(), int(THROTTLE_MS.read_text()), time.monotonic()
    while time.monotonic() - t0 < HOLD_S:
        g = gpu_sample()
        if g[0] >= GPU_ABORT_C:
            raise RuntimeError('GPU too hot, aborting')
        rows.append((time.monotonic(), cpu_temp(), g[0], g[1], g[2], g[3], mean_clock_mhz()))
        time.sleep(SAMPLE_S)
    elapsed = time.monotonic() - t1
    ts = [r[0] for r in rows]
    m = lambda i: statistics.mean(r[i] for r in rows)
    return [device, level, round(m(1), 1), round(m(2), 1),
            round(slope(ts, [r[1] for r in rows]), 2), round(slope(ts, [r[2] for r in rows]), 2),
            round(cpu_util(cpu0, cpu_times()), 1), round(m(3), 1), round(m(6)), round(m(5)), round(m(4), 1),
            round(100 * (int(THROTTLE_MS.read_text()) - thr0) / (elapsed * 1000), 1), pad_state]


def main():
    devices = sys.argv[1:] or ['cpu', 'gpu']
    before = cpu_times()
    gpus = []
    for _ in range(5):
        gpus.append(gpu_sample())
        time.sleep(1)
    if cpu_util(before, cpu_times()) > 10:
        sys.exit('the CPU is busy; stop other work first')
    print(f'GPU baseline: {statistics.mean(g[1] for g in gpus):.0f} % busy, '
          f'{statistics.mean(g[2] for g in gpus):.1f} W, {statistics.mean(g[0] for g in gpus):.0f} °C', flush=True)
    with Pad() as pad:
        st = pad.status()
    pad_state = f'{st[1]}% {"software" if st[0] == 0x80 else "pad"}'
    out = HERE / f'step_down_{datetime.date.today()}_{"_".join(devices)}.csv'
    print(f'pad left at {pad_state}; {len(devices) * len(LEVELS) * HOLD_S / 60:.0f} min', flush=True)
    with open(out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for device in devices:
            procs, stop, gpu = [], mp.Event(), None
            try:
                for level in LEVELS:
                    if device in ('cpu', 'both'):
                        want = round(os.cpu_count() * level / 100)
                        while len(procs) > want:
                            procs.pop().terminate()
                        while len(procs) < want:
                            p = mp.Process(target=spin, args=(stop,), daemon=True)
                            p.start()
                            procs.append(p)
                    if device in ('gpu', 'both'):
                        if gpu:
                            gpu.terminate()
                            gpu.wait(10)
                        gpu = subprocess.Popen([str(GPU_STRESS), str(level)], stdout=subprocess.DEVNULL) if level else None
                    row = hold(device, level, pad_state)
                    w.writerow(row)
                    f.flush()
                    print(' '.join(f'{k}={v}' for k, v in zip(FIELDS, row)), flush=True)
            finally:
                stop.set()
                for p in procs:
                    p.terminate()
                if gpu:
                    gpu.terminate()
                    gpu.wait(10)
    print(f'wrote {out}', flush=True)


if __name__ == '__main__':
    main()
