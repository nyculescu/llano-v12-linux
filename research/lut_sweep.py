"""Load x fan sweep for the fan recommendation LUT (fan_lut.json, see build_lut.py).

For each device asked for (gpu, cpu): start at full load, then step the load down to idle
(utilization under 5 %). At each load, hold the pad at each fan speed, from the fastest
down, discard the first SETTLE_S and average the next MEASURE_S: the device's temperature,
the other device's, utilization, power, clocks and thermal throttling.

GPU load: research/gpu_stress (CUDA, a duty cycle of an FMA kernel). CPU load: one
duty-cycled busy loop per logical CPU. Refuses to start while either is already busy.
Writes research/lut_sweep_DATE.csv row by row as .partial and renames it when done.
Always stops the load, puts the fan back to its starting speed and releases the pad. Stops if the GPU reaches GPU_ABORT_C.
Stop llanod and llano-guard first (they would override the fixed speeds).

    python3 research/lut_sweep.py [gpu] [cpu] [--fans 0,7,14] [--loads 100,70] [--tag fine]

Under GPU load the CPU's throttling is recorded too (other_throttle_pct): the GPU heats the
CPU through the shared heatpipes, and that is often what needs the fan.
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

LOADS = [100, 70, 40, 20, 0]
FANS = [100, 60, 30, 0]
SETTLE_S, MEASURE_S, SAMPLE_S, PREHEAT_S = 60, 45, 3, 120
# Extra settling after each load change: the first fan speed of a lower load otherwise
# measures the cool-down from the one before (idle at 100 % fan read hotter than at 0 %).
LOAD_SETTLE_S = 60
if os.environ.get('LUT_SWEEP_QUICK'):  # a smoke test of the whole run, about a minute per device
    LOADS, FANS = [100, 0], [100, 0]
    SETTLE_S, MEASURE_S, SAMPLE_S, PREHEAT_S, LOAD_SETTLE_S = 3, 6, 2, 3, 3
GPU_ABORT_C = 92
GPU_STRESS = HERE / 'gpu_stress'
THROTTLE_MS = Path('/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_total_time_ms')
FIELDS = ['device', 'load', 'fan', 'temp', 'other_temp', 'util', 'power_w', 'clock_mhz', 'throttle_pct',
          'other_throttle_pct']


def gpu_sample():
    out = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,power.draw,clocks.sm,'
                          'clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_thermal_slowdown',
                          '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True).stdout
    t, util, pw, clk, sw, hw = [x.strip() for x in out.split(',')]
    return float(t), float(util), float(pw), float(clk), sw == 'Active' or hw == 'Active'


def cpu_times():
    fields = [int(x) for x in Path('/proc/stat').read_text().split('\n')[0].split()[1:]]
    return sum(fields), fields[3] + fields[4]  # total, idle + iowait


def cpu_util(before, after) -> float:
    total, idle = after[0] - before[0], after[1] - before[1]
    return 100 * (1 - idle / total) if total else 0.0


def mean_clock_mhz() -> float:
    return statistics.mean(int(p.read_text()) / 1000 for p in
                           Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq'))


def burn(duty, stop):
    """Busy for duty ms of every 100 ms."""
    x = 0
    while not stop.is_set():
        start = time.monotonic()
        while time.monotonic() - start < duty.value / 1000:
            for _ in range(2000):
                x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        rest = 0.1 - (time.monotonic() - start)
        if rest > 0:
            time.sleep(rest)


class CpuLoad:
    def __init__(self):
        self.duty, self.stop = mp.Value('i', 0), mp.Event()
        self.procs = [mp.Process(target=burn, args=(self.duty, self.stop), daemon=True)
                      for _ in range(os.cpu_count())]

    def set(self, load):
        if load and not self.procs[0].is_alive():
            for p in self.procs:
                p.start()
        self.duty.value = load

    def close(self):
        self.stop.set()
        for p in self.procs:
            if p.is_alive():
                p.join(5)


class GpuLoad:
    def __init__(self):
        self.proc = None

    def set(self, load):
        self.close()
        if load:
            self.proc = subprocess.Popen([str(GPU_STRESS), str(load)], stdout=subprocess.DEVNULL)

    def close(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)
            self.proc = None


def check_idle():
    before, gpus = cpu_times(), []
    for _ in range(3):  # the median: one reading can catch the GPU waking up
        gpus.append(gpu_sample()[1])
        time.sleep(1)
    util = cpu_util(before, cpu_times())
    gpu = statistics.median(gpus)
    if util > 10 or gpu > 10:
        sys.exit(f'not idle: CPU {util:.0f} %, GPU {gpu:.0f} %; stop other work first')


def measure(device, seconds):
    temps, others, utils, powers, clocks, throttled = [], [], [], [], [], 0
    t0, cpu0, thr0 = time.monotonic(), cpu_times(), int(THROTTLE_MS.read_text())
    while time.monotonic() - t0 < seconds:
        if device == 'gpu':
            t, util, pw, clk, thr = gpu_sample()
            if t >= GPU_ABORT_C:
                raise RuntimeError(f'GPU at {t:.0f} °C, aborting')
            temps.append(t), utils.append(util), powers.append(pw), clocks.append(clk)
            others.append(cpu_temp())
            throttled += thr
        else:
            temps.append(cpu_temp()), clocks.append(mean_clock_mhz())
        time.sleep(SAMPLE_S)
    elapsed = time.monotonic() - t0
    cpu_throttle = 100 * (int(THROTTLE_MS.read_text()) - thr0) / (elapsed * 1000)
    if device == 'gpu':
        util, power, throttle = statistics.mean(utils), statistics.mean(powers), 100 * throttled / len(temps)
        other, other_throttle = statistics.mean(others), cpu_throttle
    else:
        util, power, other, other_throttle = cpu_util(cpu0, cpu_times()), '', '', ''
        throttle = cpu_throttle
    r = lambda v: round(v, 1) if v != '' else ''
    return [r(statistics.mean(temps)), r(other), r(util), r(power), round(statistics.mean(clocks)), r(throttle),
            r(other_throttle)]


def option(args, name, default):
    if name in args:
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


def main():
    global LOADS, FANS
    args = sys.argv[1:]
    try:
        FANS = [int(v) for v in option(args, '--fans', ','.join(map(str, FANS))).split(',')]
        LOADS = [int(v) for v in option(args, '--loads', ','.join(map(str, LOADS))).split(',')]
        tag = option(args, '--tag', '')
    except (IndexError, ValueError):
        sys.exit(__doc__)
    devices = args or ['gpu', 'cpu']
    if any(d not in ('gpu', 'cpu') for d in devices):
        sys.exit(__doc__)
    if 'gpu' in devices and not GPU_STRESS.exists():
        sys.exit(f'build {GPU_STRESS} first (see gpu_stress.cu)')
    check_idle()
    out = HERE / f'lut_sweep_{datetime.date.today()}{"_" + tag if tag else ""}.csv'
    partial = out.with_suffix('.csv.partial')
    cells = len(devices) * len(LOADS) * len(FANS)
    per_device = PREHEAT_S + (len(LOADS) - 1) * LOAD_SETTLE_S
    print(f'{cells} cells, about {(cells * (SETTLE_S + MEASURE_S) + len(devices) * per_device) / 60:.0f} min',
          flush=True)
    with Pad() as pad, open(partial, 'w', newline='') as f:
        start_speed = pad.status()[1]
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        try:
            for device in devices:
                load = GpuLoad() if device == 'gpu' else CpuLoad()
                try:
                    load.set(LOADS[0])
                    pad.set_speed(FANS[0])
                    print(f'{device}: preheating {PREHEAT_S} s', flush=True)
                    time.sleep(PREHEAT_S)
                    for level in LOADS:
                        load.set(level)
                        if level != LOADS[0]:
                            pad.set_speed(FANS[0])
                            time.sleep(LOAD_SETTLE_S)
                        for fan in FANS:
                            pad.set_speed(fan)
                            time.sleep(SETTLE_S)
                            row = [device, level, fan, *measure(device, MEASURE_S)]
                            writer.writerow(row)
                            f.flush()
                            print(' '.join(f'{k}={v}' for k, v in zip(FIELDS, row)), flush=True)
                finally:
                    load.close()
        finally:
            pad.set_speed(start_speed)  # release keeps the current speed
            pad.release()
    if os.environ.get('LUT_SWEEP_QUICK'):
        out = out.with_name(out.stem + '_quick.csv')
    partial.rename(out)
    print(f'wrote {out}', flush=True)


if __name__ == '__main__':
    main()
