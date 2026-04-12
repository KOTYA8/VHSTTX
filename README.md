# VHSTTX
VHS Teletext X - advanced features of [vhs-teletext](https://github.com/ali1234/vhs-teletext)   
   
Thanks **ali1234** for creating: [vhs-teletext](https://github.com/ali1234/vhs-teletext)

# Install
## Ubuntu (Installation without libraries)
Runs on Ubuntu 24.04 LTS
```
source myvenv/bin/activate
git clone https://github.com/KOTYA8/VHSTTX.git
cd VHSTTX
python3 setup.py install
```
### Install library (for Apps)
```
pip install PyQt5
pipx install -e .[qt] --force
```
### Update
```
cd VHSTTX
git pull
python3 setup.py install
```
### Uninstalling the old version
In a folder `myvenv/lib/python3.12/site-packages`, we delete `teletext` and `teletext-1-py3.12.egg-info` folders. 

## Windows
Runs on latest Python 3.14.3
```
python -m pip install -e .[qt,viewer,spellcheck]
```
### Launch Apps
```
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File misc\windows\build-vhsttx.ps1   

dist\VHSTTX-Windows\VHSTTX.exe
dist\VHSTTX-Windows\teletext.exe
dist\VHSTTX-Windows\TTViewer.exe
dist\VHSTTX-Windows\TTEditor.exe
```

# Future Apps
## **TeleText Viewer** - ✅ realized
* Opening T42 and HTML files and folders - ✅ realized
* Split individual pages in HTML and T42 - ✅ realized
* Opening from .t42 file - ✅ realized
* Language support - ✅ realized
* FasText Buttons - ✅ realized
* Screenshot (copy/file) - ✅ realized
* Display of teletext subpages and flags - ✅ realized
* List of all pages - ✅ realized
* Full screen - ✅ realized
* Hotkeys - ✅ realized
* Teletext service information - ⚠️bugs
* Opening T42 and HTML files and folders in HTML Viewer - ✅ realized
* Comparison of two teletexts - ✅ realized

## **VBI Tune/VBI Tune Live** - ✅ realized
* Signal Controls - ✅ realized
* Signal Cleanup - ✅ realized
* Decoder Tuning - ✅ realized
* Diagnostics - ✅ realized
* Tools - ✅ realized
* Line Selection - ✅ realized
* Fix Capture Card - ✅ realized
* Arguments and Presets - ✅ realized

## **VBI Tool** - ✅ realized
* Frame-by-frame viewing - ✅ realized
* Frame marks - ✅ realized
* Cutting frames from VBI - ✅ realized
* Adding VBI Files - ✅ realized
* Checking for errors in VBI - ✅ realized
* Changing VBI strings and inserting strings from any VBI - ✅ realized
* Real-time VBI diagnostics with Teletext Monitor - ✅ realized

## **T42 Tool** - ✅ realized
* Frame-by-frame viewing - ✅ realized
* Frame marks - ✅ realized
* Cutting frames from T42 - ✅ realized
* Adding T42 Files - ✅ realized
* Deleting pages and subpages - ✅ realized
* Checking the first line by frame/page/subpage - ✅ realized
* Adding/replacing pages/subpages from a .t42 file - ✅ realized
* View teletext on a page/subpage - ✅ realized
* Inserting lines from any teletext into your - ✅ realized
* Managing hidden subpages - ✅ realized

## **VBI Repair** - ✅ realized
* Frame-by-frame viewing - ✅ realized
* Frame marks - ✅ realized
* Saving with VBI Tune Live - ✅ realized
* Saving to VBI/T42 file - ✅ realized
* Real-time VBI diagnostics with Teletext Monitor - ✅ realized
* Stabilize VBI - ⚠️bugs

## **VHSTTX (GUI)** - ✅ realized
* Quick launch of teletext commands - ✅ realized
* Checking for updates - ✅ realized

## **TeleText Editor** - ✅ realized
* Split individual pages in HTML and T42 - ✅ realized
* Setting up pages, 8/30 service, Fastext, adding pictures, managing strings and control codes - ✅ realized
* Changing pages in different languages - ✅ realized
* Adding and delete pages/subpages - ✅ realized
* Managing hidden subpages - ✅ realized

## **Squash Tool** - ✅ realized
* Selecting methods, selecting subpages for squash - ✅ realized
* Choice for each page, different squash method - ✅ realized

# Apps
* **TeleText Viewer** (`ttviewer`) - Application for viewing teletext. Supports arrow switching. Shows subpages. Can be opened via .t42 file. Customize pages (remove blinking, double height and width). Language selection. 
* **VBI Tune** for **record/deconvolve** (`-vtn/--vbi-tune`) - VBI Tune: simplifies VBI setup before recording.   
**VBI Tune Live** for **deconvolve/vbiview** (`-vtnl/--vbi-tune-live`) - VBI Tune Live: selects real-time value for VBI.    
```
teletext record -vtn test.vbi
teletext deconvolve -vtn test.vbi > test.t42
```
```
teletext deconvolve -vtnl test.vbi > test.t42
teletext vbiview -vtnl test.vbi
``` 
* **VBI Tool** - Control panel for trimming VBI file.
```
teletext vbitool test.vbi
```
* **T42 Tool** - Control panel for trimming T42 file.
```
teletext t42tool test.vbi
```
* **VBI Repair** - Restoring VBI and strings.
```
teletext vbirepair test.vbi
```
* **VHSTTX (GUI)** (`vhsttx`) - Running all commands through the visual shell.
* **TeleText Editor** (`tteditor`) - Teletext editing application. Supports changing pages/subpages/hidden subpages, strings, characters, 8/30 service. Overlaying images and importing from other T42.
* **Squash Tool** - Visual editing squash methods for pages.
```
teletext squashtool test.t42
```

# Future Functions
* **Ignore Line (record/deconvolve/vbiview)** - ✅ realized
* **Used Line (record/deconvolve/vbiview)** - ✅ realized
* **Line numbering (vbiview)** - ✅ realized
* **Adds Templates (deconvolve/vbiview)** - ✅ realized
* **Fix Capture Card (record)** - ✅ realized
* **URXVT Terminal (deconvolve)** - ✅ realized
* **Pause for (record/deconvolve)** - ✅ realized
* **Timer for (record)** - ✅ realized
* **Capture сard settings: move down/increase frames/reset (record/deconvolve/vbiview)** - ✅ realized (❗support only bttv - BT8x8)
* **Mode: V1|V3|auto|custom|profile for (squash)** - ✅ realized
* **Spellcheck** - ⚠️bugs

# Functions
* **Ignore Line** for **record**/**deconvolve**/**vbiview** (`-il/--ignore-line`) - Ignoring lines when writing to VBI and deconvolving to t42.   
```
teletext record -il 1,2,20 test.vbi
```
```
teletext deconvolve -il 1,2,20 test.vbi > test.t42
```
```
teletext vbiview -il 4,5 test.vbi > test.t42
```

* **Used Line** for **record**/**deconvolve**/**vbiview** (`-ul/--used-line`) - Using only selected lines when writing to VBI and deconvolving to t42.   
```
teletext record -ul 4,5 test.vbi
```
```
teletext deconvolve -ul 4,5 test.vbi > test.t42
```
```
teletext vbiview -ul 4,5 test.vbi > test.t42
```
   
* **Line numbering** for **vbiview** - Line numbering in VBI Viewer.   
   
* **Templates** for **vbiview/deconvolve** (`-f`)   
(`fs200sp`, `fs200lp`, `hd630lp`, `hd630sp`, `grundig_2x4`, `hrs9700`, `hd630vdlp`, `hd630vdlp24`, `fs200vdsp`, `fs200vdlp`, `betacamsp`, `betamax`) - Adding templates (VCRs) for deconvolution and VBI viewing.   
```
teletext vbiview -f hd630sp test.vbi   
```
```
teletext deconvolve -f hd630lp test.vbi > test.t42  
```

* **Fix Capture Card** for **record** (`-fcc/--fix-capture-card`) - Fixes bug with increasing brightness in vbi0, runs through ffmpeg. How long does it work in seconds and after how long will it turn on in minutes: (`-fcc 2 3`) - runs **2 seconds** every **3 minutes** 
```
teletext record -fcc 2 3 test.vbi
```
```
teletext deconvolve -fcc 2 3 test.vbi > test.t42
```
```
teletext vbiview -fcc 2 3 test.vbi
```

* **URXVT Terminal** for **deconvolve** (`-u/--urxvt`) - Urxvt terminal for **viewing individual teletext pages and filters in real time**.
```
teletext deconvolve test.vbi -u -p 100
teletext deconvolve test.vbi -u -r 0
```

* **Pause** for **record**/**deconvolve** (`P button`) - **Pauses** while recording or deconvolving.

* **Timer** for **record** (`-tm/--timer`) (`XXh XXm XXs`)
```
teletext record test.vbi -tm 5m
```

* **Capture Card Settings** for **record**/**deconvolve**/**vbiview** (`-vs/--vbi-start` `-vc/--vbi-count` `-vt/--vbi-terminate-reset`) (`-vs value: how many lines down`/`-vc value: how many lines in total`/`-vt reset to default`)
```
teletext record test.vbi -vs 1 -vc 32
```
```
teletext deconvolve test.vbi > test.t42 -vs 1 -vc 32
```
```
teletext vbiview test.vbi -vs 1 -vc 32
```

* **Mode**: V1|V3|auto|custom|profile for **squash** (`-md/--mode`). Custom: `--match_threshold`, `--header_weight`, `--body_weight`, `--footer_weight`, `--subcode_match_bonus`, `--subcode_mismatch_penalty`, `--iterations`. Profile: JSON or `--profile-name` aggressive, balanced, broken-subcodes.
```
teletext squash test.t42 > tests.t42 -md V1
```
```
teletext squash test.t42 > tests.t42 -md custom --header-weight 0.7
```
```
teletext squash test.t42 > tests.t42 -md profile --profile-name aggressive
```
```
teletext squash test.t42 > tests.t42 -md custom --profile-name aggressive --header-weight 0.7
```

# Functions VBI Tune/VBI Tune Live (**deconvolve**/**vbiview**)
## Signal Controls (VBI)
### Value/Coeff - Adjusting Values ​​for VBI from **0** to **100** (**50** - no change) / Coefficients for values from **0.00** to **100**. *Example: -bn 25/1*
* **Brightness/Sharpness/Gain/Contrast** (`-bn/--brightness`/`-sp/--sharpness`/`-gn/--gain`/`-ct/--contrast`) 

## Signal Cleanup (VBI)
### Value/Coeff - Adjusting Values ​​for VBI from **0** to **100** (**0** - no change) + Coefficients for values from **0.00** to **100**. *Example: -nr 25/1*
* **Noise Reduction/Hum Removal/Auto Black Level/Impulse Filter/Temporal Denoise/Head Switching Mask/Line-to-Line Stabilization/Auto Gain / Auto Contrast** (`-nr/--noise-reduction`/`-hm/--hum-removal`/`-abl/--auto-black-level`/`-if/--impulse-filter`/`-td/--temporal-denoise`/`-hsm/--head-switching-mask`/`-lls/--line-to-line-stabilization`/`-agc/--auto-gain-contrast`)

## Decoder Tuning (Deconvolve)
### Value - Adjusting Values ​​for deconvolve from **0** to **100** (**50** - no change): Line Quality, Clock Lock, Start Lock. *Example: -lq 25*   
### (**0** - no change): Adaptive Threshold, Dropout Repair, Wow/Flutter Compensation, Auto Line Align.   
### Template (file), Extra Roll (value), Line Start Range (startline endline), Per-Line Shift (line:shift).⠀⠀⠀⠀⠀⠀⠀*Example: -f vhs/--extra roll 1/--line-start-range 60 130/-pls 1:+1*
* **Template/Extra Roll/Line Start Range/Line Quality/Clock Lock/Start Lock/Adaptive Threshold/Dropout Repair/Wow / Flutter Compensation/Auto Line Align/Per-Line Shift** (`-f/--file`/`--extra-roll`/`--line-start-range`/`-lq/--line-quality`/`-cl/--clock-lock`/`-sl/--start-lock`/`-at/--adaptive-threshold`/`-dr/--dropout-repair`/`-wf/--wow-flutter-compensation`/`-ala/--auto-line-align`/`-pls/--per-line-shift`)

## Diagnostics (Viewer)
* **Show Quality/Show Rejects/Show Start/Clock / Show Clock Visuals/Show Alignment Visuals/Histogram / Black Level Graph/Eye Pattern / Clock Preview/Quality Meter**

## Line Selection (Deconvolve) - deleting lines from VBI

## Tools (Analysis) - automatic configuration for VBI - ⚠️ bugs
* **Auto Tune/Clock / Start Auto-Lock**

## Tools (deconvolve)
* **Duplicate Consensus/Per-Line Confidence/Best-of-N Page Rebuild** (`--duplicate-consensus/--no-duplicate-consensus`/`--per-line-confidence/--no-per-line-confidence`/`--best-of-n-page-rebuild`)

# Guide for Functions
[GUIDE](https://github.com/KOTYA8/VHSTTX/blob/main/examples/help-all.txt)

# Installation
### Installation VHSTTX
```
sudo apt update
sudo apt upgrade
sudo apt install python3
sudo apt install python3-pip
sudo apt install git
git clone https://github.com/KOTYA8/VHSTTX.git
cd VHSTTX
sudo apt install pipx
pipx install -e .[spellcheck,viewer,qt]
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
pip install PyQt5
```
## Installation Apps
### Install Library 
```
pip install PyQt5
pipx install -e .[qt] --force
```
### Install Apps 
```
ttviewer-install
vhsttx-install
tteditor-install
```
### Delete Apps
```
ttviewer-uninstall
vhsttx-uninstall
tteditor-uninstall
```
## Preparing BT878
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
### Install Terminal for Teletext (*new Teletext Viewer made*)
```
sudo apt-get install tv-fonts rxvt-unicode
cd /etc/fonts/conf.d
sudo rm 70-no-bitmaps.conf
sudo ln -s ../conf.avail/70-yes-bitmaps.conf .
xset fp rehash
```
Launch the terminal and view teletext   
```
urxvt -fg white -bg black -fn teletext -fb teletext -geometry 41x25 +sb &
teletext service test.t42 | teletext interactive
```

# Additional features
### Fixing self-brightness on Capture Card (*made in the version 2*)
1. Installing ffmpeg
```
sudo apt install ffmpeg
```
2. Run the script in the terminal
```
while true ; do ffmpeg -y -f video4linux2 -i /dev/video0 -t 0:02 -f null - ; sleep 3m ; done ; loop
```
**Every 3 minutes (within 2 seconds), the capture card will be launched.**
