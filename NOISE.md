# Noise: measurements and estimates

The pad is set in percent, but what you hear is rpm. This page describes estimated noise at the listening position and the RPM mapping used by `llano.py`. The code is `noise.py`. The RPM and acoustic measurements can be changed in `~/.config/llano/noise.json`, using the same keys in lower case.

## Measurements

These are phone sound-meter readings at the user's ears, taken on 2026-10-09 (`research/noise_2026-10-09.txt`). The meter isn't calibrated, so the differences between readings matter more than the absolute values. With the pad off, the laptop alone measured 37–38 dB (CPU 43 °C, GPU 35 °C).

| rpm | dB at the ears | user's note |
|---|---|---|
| 0 (pad off) | 37.5 | the laptop alone |
| 300 | 40–41 | |
| 400 | 42–44 | bearable, enough for normal use |
| 500 | 45–46 | |
| 600 | 49–50 | a quiet everyday setting |
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

## RPM targets

`llano.py rpm N` sets the highest whole-percent speed whose RPM estimate is at or below `N`. Froonty's Quick Settings pad slider uses this command for continuous targets. Noise stages are estimates, not enforced limits.

## Not covered

A program that writes `/dev/hidraw*` itself bypasses the lock. The pad's roller or another program using llano can change the slider's speed.
