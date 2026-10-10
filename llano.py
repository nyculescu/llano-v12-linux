"""Control the llano V12 Ultra laptop cooling pad (HOLTEK 374a:b101) over Linux hidraw.

Protocol (captured from MythCool in a Windows VM and read from GPP_USB_Center.exe's
SetLapFanParam builder): 8-byte feature reports, no report IDs, all 8 bytes
sum to 0xFF (byte 7 = ~sum of bytes 0-6).

  ctl speed on_off light 04 00 ff cks    set state
      ctl:   fan_mode_control; 0 = pad keeps its own speed, 1 = software sets speed
      speed: fan_speed 0-100 %
      on_off: 0 = on, 1 = the whole pad off, fan and lights (0 % alone leaves the fan turning)
      light: light_mode | light_off << 7; bytes 4-6 are the remaining light parameters
  80 00 00 00 00 00 00 7f                request status; GET_FEATURE then returns
  st speed on_off light 04 00 ff cks     status (st 0x88 under pad control, 0x80 under software)
  81 00 00 00 00 00 00 7e                request version; answered on the interrupt IN endpoint

  python3 llano.py status
  python3 llano.py speed 0-100     # take software control and set the fan speed (0: the pad off)
  python3 llano.py release         # hand speed control back to the pad's buttons
  python3 llano.py lock            # fan off; every Pad write is refused until unlock
  python3 llano.py unlock          # back to the speed and control from before the lock
  python3 llano.py last            # who changed the pad last (control.log)
  python3 llano.py loud            # lift the noise limits until quiet hours end (or for 2 h by day)
  python3 llano.py quiet           # back to the noise limits
  python3 llano.py noise           # the noise limit in force, as JSON
  python3 llano.py enforce         # lower a software-set speed above the limit (the timer runs this)

The lock and the control log live in $XDG_STATE_HOME/llano (~/.local/state/llano). The lock
binds every program that drives the pad through Pad (this CLI, llanod, cpu_guard, the
research scripts, Froonty's Fan tab); a program writing /dev/hidraw* itself is not stopped,
since the udev rule gives every program of the user access. The pad's roller still works.
Each write logs who made it: $LLANO_CLIENT, else the systemd unit, else the script.

Noise limits (noise.py, NOISE.md): every speed is capped to the limit in force (600 rpm in quiet
hours, weekdays 22:00-07:00; 2000 rpm by day), unless `llano.py loud` lifted it. A speed the pad
holds under its own control (the roller) is the user's and is left alone.
"""
import fcntl
import json
import os
import sys
import time
from pathlib import Path

import noise

VID, PID = 0x374A, 0xB101
CTL_PAD, CTL_SOFTWARE = 0x00, 0x01
# Byte 2. 0 % with the pad on still turns the fan at its slowest; only `off` stops it,
# and it puts the lights out with it (confirmed by eye and ear, 2026-10-10).
PAD_ON, PAD_OFF = 0x00, 0x01


class LlanoError(Exception):
    pass


class LlanoLocked(LlanoError):
    pass


LOG_KEEP = 200


def state_dir() -> Path:
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'llano'


def read_lock() -> dict | None:
    """The lock's record ({time, by, speed, ctl} from before it), or None when unlocked."""
    try:
        return json.loads((state_dir() / 'lock').read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}  # unreadable, still locked


def client() -> str:
    """Who is driving the pad, for the control log."""
    if os.environ.get('LLANO_CLIENT'):
        return os.environ['LLANO_CLIENT']
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            unit = line.rsplit('/', 1)[-1]
            if unit.endswith('.service') and '@' not in unit and not unit.startswith(('app-', 'vte-spawn')):
                return unit.removesuffix('.service')
    except OSError:
        pass
    script = Path(sys.argv[0]).name
    if script == 'llano.py':
        try:
            parent = Path(f'/proc/{os.getppid()}/comm').read_text().strip()
            return f'llano.py (from {parent})'
        except OSError:
            return 'llano.py'
    return script


def log_control(action: str, speed: int | None, requested: int | None = None) -> None:
    entry = {'time': round(time.time(), 1), 'by': client(), 'pid': os.getpid(), 'action': action, 'speed': speed}
    if requested is not None and requested != speed:
        entry['requested'] = requested
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        path = state_dir() / 'control.log'
        lines = path.read_text().splitlines()[-(LOG_KEEP - 1):] if path.exists() else []
        tmp = path.with_suffix('.tmp')
        tmp.write_text('\n'.join(lines + [json.dumps(entry)]) + '\n')
        tmp.replace(path)
    except OSError:
        pass  # the log is a courtesy; the pad command itself succeeded


def last_control() -> dict | None:
    try:
        return json.loads((state_dir() / 'control.log').read_text().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def HIDIOCSFEATURE(n: int) -> int:  # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, len)
    return (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06


def HIDIOCGFEATURE(n: int) -> int:  # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x07, len)
    return (3 << 30) | (n << 16) | (ord('H') << 8) | 0x07


def command(body7: bytes) -> bytes:
    return body7 + bytes([(0xFF - sum(body7)) & 0xFF])


def find_hidraw() -> Path:
    for node in sorted(Path('/sys/class/hidraw').iterdir()):
        if f'{VID:08X}:{PID:08X}' in (node / 'device/uevent').read_text():
            return Path('/dev') / node.name
    raise LlanoError('llano pad 374a:b101 not found')


class Pad:
    def __init__(self, path: Path | None = None):
        self.path = path or find_hidraw()
        self.fd = os.open(self.path, os.O_RDWR)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _set_feature(self, report8: bytes) -> None:
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(9), bytearray(b'\x00' + report8))  # leading report ID 0

    def status(self) -> bytes:
        self._set_feature(command(bytes([0x80, 0, 0, 0, 0, 0, 0])))
        time.sleep(0.02)
        buf = bytearray(9)
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(9), buf)
        rep = bytes(buf[1:])
        if rep[0] not in (0x80, 0x88) or sum(rep) & 0xFF != 0xFF:
            raise LlanoError(f'unexpected status report: {rep.hex(" ")}')
        return rep

    def _write_state(self, ctl: int, speed: int, action: str, *, unlocked: bool = False,
                     requested: int | None = None) -> None:
        if not unlocked and read_lock() is not None:
            raise LlanoLocked('the pad is locked off (llano.py unlock to start it again)')
        cur = self.status()  # keep the lighting as it is
        # Software at 0 % switches the pad off; any speed, or the roller, switches it on.
        power = PAD_OFF if ctl == CTL_SOFTWARE and speed == 0 else PAD_ON
        self._set_feature(command(bytes([ctl, speed, power, cur[3], cur[4], cur[5], cur[6]])))
        log_control(action, speed, requested)

    def set_speed(self, pct: int, action: str = 'speed') -> int:
        """Sets the speed, capped to the noise limit in force; returns the speed set."""
        if not 0 <= pct <= 100:
            raise LlanoError(f'speed must be 0-100, got {pct}')
        applied = min(pct, noise.limit()['max_percent'])
        self._write_state(CTL_SOFTWARE, applied, action, requested=pct)
        return applied

    def enforce(self) -> bool:
        """Lowers a software-set speed above the limit in force; True when it did."""
        if read_lock() is not None:
            return False
        st = self.status()
        if st[0] != 0x80:  # under the pad's own control: the roller is the user's choice
            return False
        if st[1] <= noise.limit()['max_percent']:
            return False
        self.set_speed(st[1], 'enforce')
        return True

    def release(self) -> None:
        self._write_state(CTL_PAD, self.status()[1], 'release')

    def lock(self) -> None:
        """Fan off under software control, and refuse every write until unlock()."""
        if read_lock() is not None:
            return
        cur = self.status()
        record = {'time': round(time.time(), 1), 'by': client(), 'speed': cur[1],
                  'ctl': 'software' if cur[0] == 0x80 else 'pad'}
        self._write_state(CTL_SOFTWARE, 0, 'lock', unlocked=True)
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / 'lock').write_text(json.dumps(record) + '\n')

    def unlock(self) -> None:
        """Back to the speed (and the pad's control, if it had it) from before the lock."""
        record = read_lock()
        if record is None:
            return
        (state_dir() / 'lock').unlink(missing_ok=True)
        speed = record.get('speed', 0) if isinstance(record.get('speed'), int) else 0
        self.set_speed(speed, 'unlock')
        if record.get('ctl') == 'pad':
            self.release()


def describe(rep: bytes) -> str:
    """The status line, then a `noise {json}` line: rpm and dB estimates and the limit in force."""
    ctl = 'software' if rep[0] == 0x80 else 'pad'
    lights = 'off' if rep[3] & 0x80 or rep[2] == PAD_OFF else 'on'
    power = '  pad off' if rep[2] == PAD_OFF else ''
    line = f'speed {rep[1]}% ({ctl} control){power}  lights {lights}  raw {rep.hex(" ")}'
    return line + '\nnoise ' + json.dumps(noise.describe(rep[1]))


def main():
    args = sys.argv[1:]
    if args == ['last']:
        entry = last_control()
        print(json.dumps(entry) if entry else 'no control logged yet')
        return
    if args == ['noise']:
        print(json.dumps(noise.limit()))
        return
    try:
        with Pad() as pad:
            if args == ['status']:
                pass
            elif len(args) == 2 and args[0] == 'speed':
                pad.set_speed(int(args[1]))
            elif args == ['release']:
                pad.release()
            elif args == ['lock']:
                pad.lock()
            elif args == ['unlock']:
                pad.unlock()
            elif args == ['enforce']:
                pad.enforce()
            elif args in (['loud'], ['quiet']):
                noise.set_override(args == ['loud'])
                log_control(args[0], None)
                if args == ['quiet']:
                    pad.enforce()
            else:
                sys.exit(__doc__)
            time.sleep(0.1)
            print(('LOCKED  ' if read_lock() is not None else '') + describe(pad.status()))
    except LlanoError as e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
