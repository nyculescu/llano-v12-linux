"""CPU temperature guard for the llano V12 Ultra pad: step the fan up while the CPU is too hot.

Leaves the pad alone until the smoothed CPU package temperature reaches --high. Then it takes
software control and raises the fan by --step every --step-every seconds while the temperature
is above --target, holding once it is at or below --target. When the temperature falls --cool-margin
below --target it steps back down, and below the speed it started from it restores that speed and
hands control back to the pad. On SIGTERM/SIGINT it does the same.

Don't run it alongside llanod: both rewrite the speed every poll.
research/cpu_fan_sweep_2026-10-08.txt found the pad barely moves the CPU temperature under an
all-core load, so expect the guard to reach --max with the CPU still above --target.

    python3 cpu_guard.py [--high 95] [--target 92] [--step 10] [--step-every 15] [--dry-run]
    systemd-run --user --unit=llano-guard python3 cpu_guard.py      # run detached
    systemctl --user stop llano-guard                                # stop and release
"""
import argparse
import logging
import signal
import sys
import time

from llano import LlanoError, LlanoLocked, Pad
from llanod import SMOOTHING, cpu_temp

log = logging.getLogger('cpu_guard')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--high', type=float, default=95, help='°C that engages the guard')
    ap.add_argument('--target', type=float, default=92, help='°C to bring the CPU down to')
    ap.add_argument('--cool-margin', type=float, default=5, help='°C below --target before stepping down')
    ap.add_argument('--step', type=int, default=10, help='fan %% per step')
    ap.add_argument('--step-every', type=float, default=15, help='seconds between steps')
    ap.add_argument('--max', type=int, default=100, help='highest fan %%')
    ap.add_argument('--interval', type=float, default=2.0, help='seconds between polls')
    ap.add_argument('--dry-run', action='store_true', help='log decisions without touching the pad')
    a = ap.parse_args()
    if not a.target < a.high:
        ap.error('--target must be below --high')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    pad = None
    speed = None      # fan % we hold while engaged; None = the pad is in control
    start = 0         # pad speed when we engaged; stepping below it releases
    last_step = 0.0
    smoothed = None
    locked = False

    def release(reason: str):
        nonlocal speed
        log.info('%s: fan back to %d%%, handing speed control back to the pad', reason, start)
        if pad and not a.dry_run and speed is not None:
            try:
                pad.set_speed(start)  # release keeps the current speed, so restore the starting one first
                pad.release()
            except LlanoLocked:
                log.info('pad locked off: leaving it as it is')
            except (LlanoError, OSError) as e:
                log.warning('release failed: %s', e)
        speed = None

    def stop(signum, _frame):
        release(f'signal {signum}')
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info('watching CPU: engage at %.0f°C, target %.0f°C, +%d%% every %.0fs up to %d%%',
             a.high, a.target, a.step, a.step_every, a.max)
    while True:
        raw = cpu_temp()
        smoothed = raw if smoothed is None else smoothed + SMOOTHING * (raw - smoothed)
        now = time.monotonic()
        try:
            if not a.dry_run:
                pad = pad or Pad()
            if speed is None:
                if smoothed >= a.high:
                    start = 0 if a.dry_run else pad.status()[1]
                    speed, last_step = min(a.max, start + a.step), now
                    log.info('cpu %.1f°C >= %.0f°C: engaging, fan %d%% -> %d%%', smoothed, a.high, start, speed)
            elif now - last_step >= a.step_every:
                if smoothed > a.target and speed < a.max:
                    speed, last_step = min(a.max, speed + a.step), now
                    log.info('cpu %.1f°C > %.0f°C: fan up to %d%%', smoothed, a.target, speed)
                elif smoothed > a.target:
                    last_step = now
                    log.warning('cpu %.1f°C still above %.0f°C at max fan %d%%', smoothed, a.target, speed)
                elif smoothed < a.target - a.cool_margin:
                    last_step = now
                    if speed - a.step < start:
                        release(f'cpu {smoothed:.1f}°C, back to the starting speed')
                    else:
                        speed -= a.step
                        log.info('cpu %.1f°C < %.0f°C: fan down to %d%%', smoothed, a.target - a.cool_margin, speed)
            if speed is not None and not a.dry_run:
                st = pad.status()
                if (st[0], st[1]) != (0x80, speed):
                    pad.set_speed(speed)
                if locked:
                    log.info('pad unlocked: guarding again')
                locked = False
        except LlanoLocked:
            # llano.py lock (the panic button): wait without touching it.
            if not locked:
                log.info('pad locked off: waiting for unlock')
            locked = True
        except (LlanoError, OSError) as e:
            log.warning('pad unavailable, retrying: %s', e)
            if pad:
                pad.close()
            pad = None
        time.sleep(a.interval)


if __name__ == '__main__':
    main()
