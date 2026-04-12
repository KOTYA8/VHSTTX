# VHSTTX
VHS Teletext X - advanced features of [vhs-teletext](https://github.com/ali1234/vhs-teletext)   
   
Thanks **ali1234** for creating: [vhs-teletext](https://github.com/ali1234/vhs-teletext)

# Future Functions
* **Ignore Line (record/deconvolve)** - ✅ realized
* **Added Line (record/deconvolve)** - ✅ realized
* **Line numbering (vbiview)** - ✅ realized

# Functions
* **Ignore Line** (`record/deconvolve/vbiview`) - Ignoring lines when writing to VBI, deconvolving to t42, and viewing VBI lines.   
```
teletext record --ignore-line 1,2,20 test.vbi
```
```
teletext deconvolve --ignore-line 1,2,20 test.vbi > test.t42
```

* **Used Line** (`record/deconvolve/vbiview`) - Using only selected lines when writing to VBI, deconvolving to t42, and viewing VBI lines.   
```
teletext record --used-line 4,5 test.vbi
```
```
teletext deconvolve --used-line 4,5 test.vbi > test.t42
```

* **Squash Modes** (`squash`) - The squasher now supports `v1`, `v3`, `auto`, `custom`, and `profile`. `v3` is the current subpage-code-based grouping. `v1` restores the old content-similarity grouping for broadcasts where subpage codes are wrong or every subpage is `0000/0001`. `auto` chooses per page, so healthy pages keep `v3` while suspicious pages can fall back to `v1`, and it can post-merge better individual rows from the alternate mode when that row scores higher. `custom` uses a weighted hybrid matcher, and `profile` loads the same matcher from JSON.   
  ```
  teletext squash --mode v1 test.t42 > squashed.t42
  ```
  ```
  teletext squash --mode auto test.t42 > squashed.t42
  ```
  ```
  teletext squash --mode custom --match-threshold 0.72 --header-weight 0.6 --footer-weight 0.35 test.t42 > squashed.t42
  ```
  Example profile JSON:
  ```json
  {
    "match_threshold": 0.74,
    "header_weight": 0.55,
    "body_weight": 1.0,
    "footer_weight": 0.45,
    "subcode_match_bonus": 0.12,
    "subcode_mismatch_penalty": 0.04,
    "iterations": 3
  }
  ```
  ```
  teletext squash --mode profile --profile squash-profile.json test.t42 > squashed.t42
  ```
  Built-in named profiles:
  `balanced`, `aggressive`, `conservative`, `broken-subcodes`
  ```
  teletext squash --mode profile --profile-name broken-subcodes test.t42 > squashed.t42
  ```
  Live tuning window for `.t42`:
  ```
  teletext squashtool test.t42
  ```

* **Signal Controls** (`record/deconvolve/vbiview`) - Software adjustment of VBI samples. `50` keeps the original signal unchanged for `Brightness`, `Sharpness`, `Gain`, `Contrast`. Extra cleanup controls `-if/--impulse-filter`, `-td/--temporal-denoise`, `-nr/--noise-reduction`, `-hm/--hum-removal`, and `-abl/--auto-black-level` are available too. Each control accepts either `VALUE` or `VALUE/COEFF`, for example `-sp 65/3` or `-nr 20/1.2`. `Line Quality` now uses only a plain value.   
```
teletext vbiview -bn 55 -sp 65/3 -gn 60 -ct 58 -nr 20/1.2 test.vbi
```
```
teletext deconvolve -bn 55 -sp 65 -gn 60 -ct 58 test.vbi > test.t42
```
```
teletext record -bn 55 -sp 65 -gn 60 -ct 58 test.vbi
```
```
teletext deconvolve -bn 55/40 -sp 65/4.5 -gn 60/0.75 -ct 58/1.25 test.vbi > test.t42
```
During `record` and `deconvolve`, press `P` in the terminal to pause/resume processing.

* **Timer** (`record/deconvolve`) - Stop processing after a chosen active time. Use `-tm/--timer` with one to three values such as `20s`, `1m 20s`, or `1h 2m 3s`. In `deconvolve`, `-t/--threads` remains unchanged.   
```
teletext record -tm 20s test.vbi
```
```
teletext deconvolve -tm 1m 20s test.vbi > test.t42
```

* **BT878 VBI Format** (`record/deconvolve/vbiview`) - On Linux raw `/dev/vbi*` capture you can request a different VBI start and line count directly on the active device handle, for example `-vs 7 320 -vc 17 17` for `17+17` lines. For `.vbi` files, the same options tell VHSTTX how many lines were recorded. VHSTTX restores the previous device format when live capture/view/deconvolution ends.   
```
teletext record -vs 7 320 -vc 17 17 test.vbi
```
Short form:
```
teletext record -vs 1 -vc 34 test.vbi
```
You can also omit either side and the BT878 default will be filled in automatically:
```
teletext record -vc 34 test.vbi
```
```
teletext record -vs 1 test.vbi
```
```
teletext deconvolve -vs 7 320 -vc 17 17 test.vbi > test.t42
```
```
teletext vbiview -vs 7 320 -vc 17 17 test.vbi
```
To restore the standard BT878 raw VBI layout later, use:
```
```

* **VBI Tuning Window** (`record/deconvolve/vbiview`) - Open a Qt tuning window with sliders, manual value fields, coefficient fields, signal cleanup controls, line checkboxes `1..32`, `All On/All Off`, `Auto Tune`, `Clock / Start Auto-Lock`, a `Diagnostics` section (`Show Quality`, `Show Rejects`, `Show Start/Clock`), and `Args` support for `Brightness`, `Sharpness`, `Gain`, `Contrast`, `Impulse Filter`, `Temporal Denoise`, `Noise Reduction`, `Hum Removal`, `Auto Black Level`, `Line Quality`, `Clock Lock`, `Start Lock`, `Adaptive Threshold`, `Dropout Repair`, `Wow/Flutter Compensation`, and `Ignore/Used Line`. The generated `Args` now use compact `VALUE` or `VALUE/COEFF` syntax. For decode/view workflows it also lets you change `Template`, `Extra Roll`, `Line Start Range`, and `Fix Capture Card`. `-vtn` tunes before start, `-vtnl` tunes live. A top-level `Menu` lets you hide individual functions from the tuner UI while temporarily falling back to defaults, `Ctrl + Left Click` on a function row resets just that row, and both local/file presets now remember this menu state. You can save named `Local Presets` for quick reuse in both modes, and still `Save Preset` / `Load Preset` to a file for sharing.
```
teletext record -vtn test.vbi
```
```
teletext deconvolve -vtn ort97.vbi -r 0
```
```
teletext deconvolve -vtnl ort97.vbi -p 100
```
```
teletext vbiview -vtnl test.vbi
```

* **VBI Tool** (`vbitool`) - Opens the standard raw VBI viewer together with a crop/edit control window. It now starts paused by default, has `Undo/Redo`, `Reset`, `VBI Tune Live`, frame-range selection, duration split into `Minutes` and `Seconds`, size estimates in `MB`, immediate multi-cut marking with `Delete Selection`, `.vbi` insertion after `Mark End` with its own timeline color, final save of the edited file, and supports the same `-vtn/-vtnl`, `-il/-ul`, compact `VALUE[/COEFF]` signal controls, templates, `--extra-roll`, `--line-start-range`, `--clock-lock`, `--start-lock`, `--dropout-repair`, `--wow-flutter-compensation`, `-fcc`, cleanup controls like `-hsm/-lls/-agc`, and diagnostics such as `--show-quality-meter`, `--show-histogram-graph`, and `--show-eye-pattern` as the VBI tools.
``` 
teletext vbitool -vtnl test.vbi
```

* **T42 Tool** (`t42tool`) - Opens a `.t42` editor that can also start as an empty project. It supports packet-range cuts, full-file inserts, page/subpage import or replacement from another `.t42`, page/subpage renaming, page/subpage deletion from the final output, source `.t42` browsing in a separate dialog, and saving the rebuilt service to a new `.t42`.
```bash
teletext t42tool test.t42
```
```bash
teletext t42tool
```

* **VBI Repair** (`vbirepair`) - Opens the standard raw VBI viewer together with a repair window focused on diagnostics instead of cutting. It starts paused by default, supports frame stepping/playback speed, `Save VBI`, `VBI Tune Live`, and a live diagnostics panel with `Packets`, `Row`, and rolling `Page` views so you can inspect decoded text while tuning a `.vbi` capture.
```bash
teletext vbirepair -vtnl test.vbi
```

* **Fix Capture Card** (`record/deconvolve/vbiview`) - Periodically runs `ffmpeg` on `/dev/video0` to keep some capture cards from drifting in brightness. `-fcc 2 3` means run for `2` seconds every `3` minutes. It starts immediately after being enabled, and the terminal now prints when the wake-up run starts and when the next one is scheduled. The same settings are available in `-vtn` and `-vtnl`.
```
teletext deconvolve -fcc 2 3 test.vbi > test.t42
```

* **urxvt Mode** (`deconvolve`) - `-u/--urxvt` re-runs the current `deconvolve` command inside `urxvt` with `-fg white -bg black -fn teletext -fb teletext`. Useful for page/title viewing in a dedicated teletext terminal.   
```
teletext deconvolve -u -p 100 test.vbi
```
   
* **Line numbering** (`vbiview`) - Line numbering in VBI Viewer.   
   
* **Templates** (`vbiview/deconvolve`)    
(`fs200sp`, `fs200lp`, `hd630lp`, `hd630sp`, `grundig_2x4`, `hrs9700`, `hd630vdlp`, `hd630vdlp24`, `fs200vdsp`, `fs200vdlp`, `betacamsp`, `betamax`) - Adding templates (VCRs) for deconvolution and VBI viewing.   
```
teletext vbiview -f hd630sp test.vbi   
```
```
teletext deconvolve -f hd630lp test.vbi > test.t42  
```

* **Standalone Viewer** (`ttviewer`) - Qt viewer for `.t42` files with page/subpage navigation, fastext buttons, auto subpage scrolling, screenshot saving, and a settings menu with single-height, single-width, no-flash, no-hex-pages and language override modes (`default`, `cyr`, `swe`, `ita`, `deu`, `fra`, `pol`, `nld`).  
```
ttviewer test.t42
```
Ubuntu desktop integration can be installed with:
```
ttviewer-install
```
Fallback files for manual setup are still in `misc/ubuntu`.

# Guide for Functions
[GUIDE](https://github.com/KOTYA8/VHSTTX/blob/main/examples/help-all.txt)

# Installation
### Installation VHSTTX
The entire installation was performed on Ubuntu 24.04 LTS.
```
sudo apt update
sudo apt upgrade
sudo apt install python3
sudo apt install python3-pip
sudo apt install git
git clone https://github.com/KOTYA8/VHSTTX.git
cd VHSTTX
sudo apt install pipx
pipx install -e .[spellcheck,viewer]
cd
sudo apt install python3-venv
python3 -m venv myvenv
source myvenv/bin/activate
cd VHSTTX
pip install setuptools
python3 setup.py install
pip install click
pip install matplotlib
pip install pyserial
pip install pyzmq
pip install scipy
pip install tqdm
pip install watchdog
pip install numpy==1.26.4
pip install pyopengl
sudo apt-get install libgl1-mesa-dev libglu1-mesa-dev freeglut3-dev
pip install pyopencl
pip install pyenchant
sudo apt install nvidia-driver-580
sudo apt install nvidia-cuda-toolkit nvidia-cuda-toolkit-gcc
pip install pycuda
pipx install -e .[CUDA,spellcheck,viewer] --force
```
For the standalone `.t42` viewer, install the Qt extra too:
```
pipx install -e .[qt] --force
```
Then register it in Ubuntu as a normal app and `.t42` file handler:
```
ttviewer-install
```
### Windows Offline Mode
On Windows, `vhsttx` is intended to work as an offline frontend for saved `.vbi` and `.t42` files. The launcher hides Linux-only live-capture parts such as raw `/dev/vbi0` recording, `v4l2` format control, `urxvt`, and capture-card keepalive helpers. The Windows profile is aimed at:
```
vhsttx
ttviewer
tteditor
teletext vbiview test.vbi
teletext vbitool test.vbi
teletext vbirepair test.vbi
teletext deconvolve test.vbi > test.t42
teletext squash test.t42 > squashed.t42
teletext html outdir/ test.t42
teletext spellcheck test.t42 > checked.t42
```
### Windows Portable Build
To build a portable Windows bundle with `VHSTTX.exe`, `teletext.exe`, `TTViewer.exe`, and `TTEditor.exe` in one folder:
```powershell
python -m pip install -e .[qt,viewer,spellcheck]
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File misc\windows\build-vhsttx.ps1
```
On Windows, `setup.py` now allows the current `numpy` chosen by `pip`, so you do not need to force `numpy==1.26.4`.
After build, run:
```powershell
dist\VHSTTX-Windows\VHSTTX.exe
```
Optional direct entry points:
```powershell
dist\VHSTTX-Windows\teletext.exe
dist\VHSTTX-Windows\TTViewer.exe
dist\VHSTTX-Windows\TTEditor.exe
```
### Preparing BT878
1. Installing the QV4L2 Control Panel:
```
sudo apt install qv4l2
```
2. Setting up the card model:
[BTTV Card List](https://docs.kernel.org/admin-guide/media/bttv-cardlist.html)   
```
sudo rmmod bttv
sudo modprobe -v bttv card=16 tuner=0 radio=0
sudo touch /etc/modprobe.d/bttv.conf
```
3. In a folder `/etc/modprobe.d/bttv.conf`, we write `options bttv card=16 tuner=0 radio=0`

# Additional features
### Fixing self-brightness on Capture Card
1. Installing ffmpeg
```
sudo apt install ffmpeg
```
2. Run the script in the terminal
```
while true ; do ffmpeg -y -f video4linux2 -i /dev/video0 -t 0:02 -f null - ; sleep 3m ; done ; loop
```
**Every 3 minutes (within 2 seconds), the capture card will be launched.**

# Changelog
All previous versions are available in the repository: [VHSTTX_VER](https://github.com/KOTYA8/VHSTTX_VER)  

### **Currently**  
* **V1** - Support **--ignore-line** and **--used-line** for `record` and `deconvolve`. Numbering in `vbiview`. Templates: **fs200sp**, **fs200lp**, **hd630lp**, **hd630sp**, **grundig_2x4**, **hrs9700**, **hd630vdlp**, **hd630vdlp24**, **fs200vdsp**, **fs200vdlp**, **betacamsp**, **betamax**
