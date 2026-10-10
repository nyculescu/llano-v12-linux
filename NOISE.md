# Noise: measurements, warnings and limits

The pad is set in percent, but what you hear is rpm. This page says how loud each speed is at the listening position, which speeds count as quiet, audible or loud, and the limits that `llano.py` applies by itself. The code is `noise.py`. Every value below can be changed in `~/.config/llano/noise.json`, using the same keys in lower case.

## Measurements

These are phone sound-meter readings at the user's ears, taken on 2026-10-09 (`research/noise_2026-10-09.txt`). The meter isn't calibrated, so the differences between readings matter more than the absolute values. With the pad off, the laptop alone measured 37–38 dB (CPU 43 °C, GPU 35 °C).

| rpm | dB at the ears | user's note |
|---|---|---|
| 0 (pad off) | 37.5 | the laptop alone |
| 300 | 40–41 | |
| 400 | 42–44 | bearable, enough for normal use |
| 500 | 45–46 | |
| 600 | 49–50 | **the most that may run at night** |
| 700 | 51–52 | |
| 800 | 53–54 | at low activity, more speed changes nothing |
| 900 | 55–56 | |
| 1000 | 57 | loud from here on |
| 1175 | 62 | |
| 1500 | | like a small vacuum cleaner |
| 2000 | | sometimes needed to bring a temperature down |
| 2800 | | unbearable |

At low activity, the speed made no difference to the temperatures between 500 and 800 rpm (CPU 40–41 °C, GPU 32–33 °C).

**Percent to rpm:** rpm ≈ 300 + 25 × percent from 2 % up, and 0 % and 1 % are the pad's lowest level, 300 rpm (only `llano.py off` stops the fan, and the lights with it). Measured on the pad's display: 0 % and 1 % → 300 rpm, 2 % → 350 rpm, 12 % → 600 rpm. 2800 rpm at 100 % is an assumption. The line agrees with an older observation of 50 % ≈ 1550 rpm. More readings from the display would firm it up.

| percent | 4 | 12 | 28 | 35 | 48 | 68 | 100 |
|---|---|---|---|---|---|---|---|
| rpm | 400 | 600 | 1000 | 1175 | 1500 | 2000 | 2800 |

## Warning bands

Froonty's Fan tab colours the estimated rpm and dB by band.

| band | up to | at the ears | colour |
|---|---|---|---|
| quiet | 400 rpm | ≤ 44 dB | green |
| night level | 600 rpm | ≤ 50 dB | lime |
| audible | 1000 rpm | ≤ 57 dB | yellow |
| loud | 2000 rpm | ≈ 69 dB (estimated) | orange |
| very loud | above 2000 rpm | | red |

## Limits

`llano.py` caps every speed it is asked to set. That covers the CLI, `llanod`, `cpu_guard`, the research scripts and the Fan tab. A capped write is logged with the speed that was asked for.

- **Quiet hours: weekdays 22:00–07:00, 600 rpm (12 %).** A moment counts as quiet when its own day is Monday to Friday and the time is at or after 22:00 or before 07:00. So Friday 22:00–24:00 is quiet, but the hours after midnight on Saturday are not. Likewise Sunday 22:00–24:00 is not quiet, but Monday 00:00–07:00 is. Change `quiet_days`, `quiet_from` and `quiet_to` to change this.
- **Daytime: 2000 rpm (68 %).** 2800 rpm is never needed for cooling (`fan_lut.json`: full speed was at most 1 °C cooler than 60 %) and is unbearable.
- **Manual overrides:**
  - `llano.py loud`, or **Loud until 07:00** in the Fan tab, lifts the limits until the current quiet hours end. By day it lifts them for 2 hours. `llano.py quiet` puts them back and lowers the speed at once.
  - Turning the **roller** is manual too. While the pad reports its own control, nothing lowers its speed.
  - The **lock** (`llano.py lock`, the Fan lock panic button) turns the fan off whatever the limits say.
- **Enforcement:** the `llano-enforce` user timer runs `llano.py enforce` every 5 minutes, and again at 22:00 on weekdays. It lowers a software-set speed that is above the limit in force, so a fast speed set in the evening does not run all night. Install it with `contrib/install-enforcer.sh`. Remove it with `systemctl --user disable --now llano-enforce.timer`.

## Not covered

A program that writes `/dev/hidraw*` itself bypasses the limits, as it bypasses the lock. Only an always-running daemon could catch the pad being changed between two timer runs; the 5-minute timer is the compromise.
