# llano V12 Ultra cooling pad on Linux

Fan control for the **llano V12 Ultra** laptop cooling pad (USB `374a:b101`, "HOLTEK USB-HID AP") from Linux, without the vendor's Windows-only Myth.Cool app.

- `llano.py`: read status and set the fan speed from the command line.
- `llanod.py`: a fan-curve daemon that drives the pad from CPU and GPU temperatures.
- `cpu_guard.py`: steps the fan up while the CPU is above a threshold, for other tools or scripts to start during heavy jobs.

Neither needs any dependencies beyond Python 3.10+ and access to the pad's `/dev/hidraw*` node.

## Quick start

```bash
python3 llano.py status          # speed 60% (pad control)  lights on  raw 88 3c 00 03 04 00 ff 35
python3 llano.py speed 100       # software control, fan at 100 %
python3 llano.py speed 0         # the pad off (fan and lights)
python3 llano.py release         # hand speed control back to the pad's roller
python3 llano.py lock            # panic: fan off, and every program using Pad is refused
python3 llano.py unlock          # back to the speed and control from before the lock
python3 llano.py last            # who changed the pad last
python3 llano.py loud            # lift the noise limits until quiet hours end
python3 llano.py quiet           # back to the noise limits
```

**Noise limits** ([NOISE.md](NOISE.md)): every speed is capped to 600 rpm (12 %) in quiet hours (weekdays 22:00–07:00) and 2000 rpm (68 %) by day, unless lifted with `loud`. `contrib/install-enforcer.sh` installs a user timer that lowers a software-set speed above the limit every 5 minutes; the roller is left alone. The status output's second line, `noise {json}`, gives the estimated rpm and dB at the ears and the limit in force.

The lock and a log of who drove the pad (`control.log`, last 200 writes: `$LLANO_CLIENT`, else the systemd unit, else the script) live in `~/.local/state/llano`. The lock binds everything that goes through `Pad` (this CLI, `llanod`, `cpu_guard`, the research scripts, Froonty's Fan tab); `llanod` and `cpu_guard` wait while it holds. It cannot stop a program that writes `/dev/hidraw*` itself, since the udev rule gives all of the user's programs access: a lock no user program can lift needs a root helper. The pad's roller always works.

If you get `Permission denied`, install the udev rule, which gives the logged-in user access to the pad:

```bash
sudo cp contrib/70-llano.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

### Fan-curve daemon

```bash
python3 llanod.py --dry-run      # log what it would do
cp contrib/llanod.service ~/.config/systemd/user/   # edit the path if the repo lives elsewhere
systemctl --user enable --now llanod
```

The curve in `llanod.py` maps the NVIDIA GPU temperature (`nvidia-smi`) to fan speed:

| GPU °C | ≤65 | 75 | 80 | ≥84 |
|---|---|---|---|---|
| fan % | 30 | 40 | 60 | 80 |

It tops out at 80 % because more doesn't help. On an RTX 3080 Ti Laptop GPU training at about 128 W, holding the pad at fixed speeds gave these results (`research/fan_sweep_2026-10-07.txt`):

| fan % | GPU °C | SM clock (MHz) | throttled |
|---|---|---|---|
| 100 | 73.0 | 1612 | no |
| 80 | 73.2 | 1621 | no |
| 60 | 76.8 | 1593 | no |
| 40 | 78.0 | 1590 | no |

The CPU doesn't drive the fan at all, because the pad does almost nothing for it. With all 20 threads busy (`research/cpu_fan_sweep_2026-10-08.txt`), the package held about 90 °C and throttled about 65 % of the time at every fan speed:

| fan % | CPU °C | mean clock (MHz) | time throttled |
|---|---|---|---|
| 100 | 89.7 | 2305 | 66 % |
| 80 | 89.7 | 2295 | 66 % |
| 60 | 89.8 | 2262 | 66 % |
| 40 | 89.5 | 2235 | 65 % |

The extra cooling shows up only as about 3 % more clock, which isn't worth the noise.

The temperature is smoothed, and speed rises immediately but drops only after a 3 °C fall. Each poll the daemon reads the pad's status and rewrites the speed if the pad isn't already there under software control. So while it runs, the pad's roller is overridden within a couple of seconds. Stopping the service (`systemctl --user stop llanod`) hands control back to the roller. If the pad disappears, the daemon logs it and reconnects when it comes back.

### CPU guard

```bash
systemd-run --user --unit=llano-guard /usr/bin/python3 $PWD/cpu_guard.py   # engage at 95 °C, aim for 92 °C
journalctl --user -u llano-guard -f                                        # watch it
systemctl --user stop llano-guard                                          # restore the starting speed and release
```

It leaves the pad alone until the smoothed CPU package temperature reaches `--high` (95 °C). Then it adds `--step` (10 %) every `--step-every` (15 s) while the CPU is above `--target` (92 °C), and holds once the CPU is at or below it. Once the CPU is `--cool-margin` (5 °C) below the target, it steps back down, restores the starting speed and releases. Don't run it alongside `llanod`, since both rewrite the speed every poll. Given the CPU sweep below, expect it to reach 100 % with the CPU still above the target under an all-core load.

### Fan recommendation table

`fan_lut.json` gives a fan speed for the CPU and the GPU temperatures; Froonty's Fan tab shows it with an Apply button. It comes from a load × fan sweep on this laptop (i9-13980HX, RTX 4080 Laptop GPU): each device from full load down to idle (100/70/40/20/0 %, idle under 5 % utilization), the pad at 100/60/30/0 % at each load, 60 s to settle and 45 s measured (`research/lut_sweep_2026-10-09.csv`, 82 min). Its 0 % predates `speed 0` switching the pad off: the fan turned at its slowest then, it was not stopped.

```bash
nvcc -O3 -Xcompiler -U_GNU_SOURCE -o research/gpu_stress research/gpu_stress.cu   # CUDA load generator
python3 research/lut_sweep.py gpu cpu                                              # stop llanod / llano-guard first
python3 research/build_lut.py research/lut_sweep_DATE.csv                          # writes fan_lut.json
```

For each load, the recommended speed is the slowest one within 1.5 °C of the coolest measured, with at most 2 points more throttling. It is keyed by the temperature the device runs at with that speed; rows are made non-decreasing, interpolated between and clamped at the ends. The higher of the CPU's and the GPU's speeds wins.

| GPU °C | ≤42 | 47–64 |
|---|---|---|
| fan % | 30 | 60 |

| CPU °C | ≤58 | 70 | ≥84 |
|---|---|---|---|
| fan % | 30 | 62 | 100 |

- **GPU:** 100 % fan was never more than 1 °C cooler than 60 % (full load: 63.0 vs 64.0 °C; 0 % fan: 71.7 °C), and nothing throttled.
- **CPU at high load:** the pad matters here. At full load, 0 % fan throttled 85 % of the time (0 % at 100 % fan); at 70 % load, throttling went 3 → 30 → 65 → 72 % as the fan slowed.
- **GPU load heats the CPU:** at full GPU load the CPU sat at 79 °C with the fan at 100 % and 93 °C at 0 %, so under GPU load the CPU's row usually leads.
- **Low confidence between 58 and 84 °C (CPU):** the duty-cycled 20 % and 40 % CPU loads gave readings that did not fall with more fan (at 20 % load, 100 % fan read hottest). Bursts at boost clocks, and probably the laptop's own fans, outweighed the pad there. A sweep with whole threads busy instead of a duty cycle, measured for longer, would pin it down.
- These numbers differ from the earlier sweeps above, which name an RTX 3080 Ti and 20 threads; those may come from another machine.

## Protocol

The device has one vendor-defined HID interface (usage page `0xFF00`) with no report IDs: a 64-byte input report, a 64-byte output report and an 8-byte feature report. Everything that matters goes through the **feature report**. Every report's 8 bytes sum to `0xFF`, so byte 7 is the bitwise NOT of the sum of bytes 0–6.

| Direction | Bytes | Meaning |
|---|---|---|
| SET_FEATURE | `ctl speed on_off light 04 00 ff cks` | Set state |
| SET_FEATURE | `80 00 00 00 00 00 00 7f` | Request status; a GET_FEATURE then returns it |
| GET_FEATURE | `st speed on_off light 04 00 ff cks` | Status |
| SET_FEATURE | `81 00 00 00 00 00 00 7e` | Request version; the answer (ASCII, e.g. `V1626154887`) arrives on interrupt IN endpoint 3 |

Set-state fields, from the vendor's `SetLapFanParam` builder:

| Byte | Field | Notes |
|---|---|---|
| 0 | `fan_mode_control` | `0` = the pad keeps its own speed and ignores byte 1; `1` = byte 1 sets the speed |
| 1 | `fan_speed` | 0–100 % |
| 2 | `on_off` | `0` on; `1` switches the whole pad off, fan and lights (any non-zero acts as `1`; there is no fan off with the lights on). At 0 % with the pad on, the fan still turns at its slowest, so `llano.py speed 0` (and `lock`) also switch it off. `1` sent to a pad already off starts its fan with the lights dark, and the status byte reads `1` either way, so off from anything but on is sent as on at 0 %, then off half a second later |
| 3 | `light_mode \| light_off << 7` | e.g. `03` = colour chase, `83` = same mode with the lights off |
| 4–6 | lighting parameters | the app sends `04 00 ff` (brightness 255 is byte 6) |
| 7 | checksum | `~sum(bytes 0–6) & 0xff` |

The status report has the same layout. Byte 0 is `0x88` while the pad is in charge and `0x80` while software is. After `release` (`fan_mode_control = 0`), the pad's own speed roller works again. Status byte 0 stays at `0x80` until the roller is next used, then goes back to `0x88`.

On Linux the hidraw feature ioctls need a leading report-ID byte of `0`, which the kernel strips because the device has no report IDs. So `llano.py` passes 9-byte buffers for 8-byte reports.

## How it was worked out

1. **Blind probing from Linux.** The status feature report was easy to read and followed the pad's own buttons. Writing it back in the same layout (`88 speed …`) was accepted, and status then showed the new value, but the fan didn't change. Sweeping all 256 first-byte values over the 64-byte output report didn't work either. In hindsight, the probes used the status opcode `0x88`, and the real control byte is `fan_mode_control`. The status copy is only a mirror, so a status change didn't prove the hardware had changed.
2. **The vendor app.** Myth.Cool is a white-labelled GamePP Electron app. The `.gpk` files are renamed ASAR archives, and the JavaScript gives away the JSON command names (`SetLapFanParam {fan_speed, fan_mode_control, light_*…}`). The native module that talks USB, `GPP_USB_Center.exe`, isn't in the installer; the app downloads it when it runs. Under Wine the app stalled before reaching that step.
3. **A Windows VM with the pad passed through and USB capture on the host.** QEMU's `usb-host` device can write a usbmon-format pcap of everything that crosses the passed-through device (`-device usb-host,…,pcap=FILE`), so no capture software is needed in the guest and no `usbmon` module on the host. Under libvirt it's set with a `<qemu:override>` on the hostdev's alias. AppArmor only lets QEMU write the pcap inside a directory that is also shared into the guest with `<filesystem>`. The capture showed the 8-byte feature-report framing, the checksum and the `80`/`81` commands, plus a lighting command (`00 3c 00 83 04 00 ff 3d`).
4. **Disassembly.** In the VM the app downloaded `GPP_USB_Center.exe`, a 32-bit PE. Following references to the `"SetLapFanParam"` and `"fan_mode_control"` strings led to the function that packs the JSON fields into the 8 report bytes (table above). Byte 0 is `fan_mode_control`, which the app had sent as `0` for the lighting change. Setting it to `1` made the fan obey byte 1. That was confirmed by ear with a 0–100 % sweep.

`tools/pcapdump.py` decodes the captures in `captures/`. Note that QEMU's pcap writer gives control transfers URB id 0 and often no completion record. `research/` holds the earlier probe scripts and the ASAR extractor.

## Open questions

- **Speed scale.** The pad's roller steps the fan in 100 rpm increments from wherever it currently is. After software set 50 %, the roller gave 1550 rpm rather than round hundreds. How percent maps to rpm hasn't been measured.
- **Lighting.** Bytes 3–6 are only partly decoded. The app's other light fields (`light_color_mode`, `light_speed`, `light_power`) map onto them, but nothing here sets them.
- Tested on one pad, reporting version `V1626154887`.

This is an independent project, not affiliated with or endorsed by llano. No vendor software is included in this repository.
