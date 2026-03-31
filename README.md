# VHSTTX
VHS Teletext X - advanced features of [vhs-teletext](https://github.com/ali1234/vhs-teletext)   
   
Thanks **ali1234** for creating: [vhs-teletext](https://github.com/ali1234/vhs-teletext)

# Transition from vhs-teletext and update VHSTTX
```
source myvenv/bin/activate
git clone https://github.com/KOTYA8/VHSTTX.git
cd VHSTTX
python3 setup.py install
```
### Update
```
python3 setup.py install
```
### Uninstalling the old version
In a folder `myvenv/lib/python3.12/site-packages`, we delete `teletext` and `teletext-1-py3.12.egg-info` folders. 

# Future Apps
## **Teletext Viewer** - ✅ realized
* Opening T42 and HTML files and folders - ✅ realized
* Split individual pages in HTML and T42 - ✅ realized
* Opening from .t42 file - ✅ realized
* Language support - ✅ realized
* FasText Buttons - ✅ realized
* Screenshot - ✅ realized
* Display of teletext subpages and flags - ✅ realized
* List of all pages - ✅ realized
* Full screen - ✅ realized
* Teletext service information - ⚠️ bugs

## **VBI Tune/VBI Tune Live** - ✅ realized
* Signal Controls (Brightness/Sharpness/Gain/Contrast) - ✅ realized
* Decoder Tuning (Template/Extra Roll/Line Start Range) - ✅ realized
* Line Selection - ✅ realized
* Fix Capture Card - ✅ realized
* Arguments - ✅ realized

## **VBI Crop** - ✅ realized
* Frame-by-frame viewing - ✅ realized
* Frame marks - ✅ realized
* Saving with VBI Tune Live - ✅ realized

# Future Functions
* **Ignore Line (record/deconvolve)** - ✅ realized
* **Used Line (record/deconvolve)** - ✅ realized
* **Line numbering (vbiview)** - ✅ realized
* **Spellcheck** - ⚠️ bugs

# Apps
* **Teletext Viewer** - Application for viewing teletext. Supports arrow switching. Shows subpages. Can be opened via .t42 file. Customize pages (remove blinking, double height and width). Language selection.   
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
* **VBI Crop** - Control panel for trimming VBI file.
```
teletext vbicrop test.vbi
```

# Functions
* **Ignore Line** for **record**/**deconvolve**/**vbiview** (`-il/--ignore-line`) - Ignoring lines when writing to VBI and deconvolving to t42.   
```
teletext record --ignore-line 1,2,20 test.vbi
```
```
teletext deconvolve --ignore-line 1,2,20 test.vbi > test.t42
```

* **Used Line** for **record**/**deconvolve**/**vbiview** (`-ul/--used-line`) - Using only selected lines when writing to VBI and deconvolving to t42.   
```
teletext record --used-line 4,5 test.vbi
```
```
teletext deconvolve --used-line 4,5 test.vbi > test.t42
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
* **Brightness/Sharpness/Gain/Contrast** for **record**/**deconvolve**/**vbiview** (`-bn/--brightness`/`-sp/--sharpness`/`-gn/--gain`/`-ct/--contrast`) - Adjusting Values ​​for VBI from **0** to **100**.   
```
teletext record -bn 25 -sp 30 -gn 50 -ct 0 test.vbi
```
```
teletext deconvolve -bn 25 -sp 30 -gn 50 -ct 0 test.vbi > test.t42
```
```
teletext vbiview -bn 25 -sp 30 -gn 50 -ct 0 test.vbi
```
* **Brightness/Sharpness/Gain/Contrast Coefficients** for **record**/**deconvolve**/**vbiview** (`-bncf/--brightness-coeff`/`-spcf/--sharpness-coeff`/`-gncf/--gain-coeff`/`-ctcf/--contrast-coeff`) - Increasing coefficients for values from **0.00** to **100**.   
```
teletext record -bn 25 -sp 30 -gn 50 -ct 0 -bncf 0.5 -spcf 0.5 -gncf 0.5 -ctcf 0.5 test.vbi
```
```
teletext deconvolve -bn 25 -sp 30 -gn 50 -ct 0 -bncf 0.5 -spcf 0.5 -gncf 0.5 -ctcf 0.5 test.vbi > test.t42
```
```
teletext vbiview -bn 25 -sp 30 -gn 50 -ct 0 -bncf 0.5 -spcf 0.5 -gncf 0.5 -ctcf 0.5 test.vbi
```
* **Fix Capture Card** for **record**/**deconvolve**/**vbiview** (`-fcc/--fix-capture-card`) - Fixes bug with increasing brightness in vbi0, runs through ffmpeg. How long does it work in seconds and after how long will it turn on in minutes: (`-fcc 2 3`) - runs **2 seconds** every **3 minutes** 
```
teletext record -fcc 2 3 test.vbi
```
```
teletext deconvolve -fcc 2 3 test.vbi > test.t42
```
```
teletext vbiview -fcc 2 3 test.vbi
```
* **URXVT Terminal** for **deconvolve** (`-u/--urxvt`) - Urxvt terminal for viewing teletext in real time.
```
teletext deconvolve test.vbi -u -p 100
teletext deconvolve test.vbi -u -r 0
```

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
### Installation Teletext Viewer
1. Install PyQt5 and QT   
```
pip install PyQt5
```
```
pipx install -e .[qt] --force
```
2. Install   
```
ttviewer-install
```
### Installation Apps
1. Install QT
```
pipx install -e .[qt] --force
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
### Install Terminal for Teletext
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
* **V2** - Support for adjusting **brightness**, **sharpness**, **gain** and **contrast** and coefficients. Simplification of opening **urxvt terminal** for `deconvolve`. Fixed auto-brightness on vbi0 (`-fcc`). Added: **VBI Tune**, **VBI Tune Live**, **VBI Crop** application. Fixed (**Teletext Viewer**): opening **folders from HTML/T42 files**, added **page scrolling speed**, added **All Symbols** and **No Subpages** flag, **HTML viewer**, **HTML fonts to Split**.
