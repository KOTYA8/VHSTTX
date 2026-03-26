# VHSTTX
VHS Teletext X - advanced features of [vhs-teletext](https://github.com/ali1234/vhs-teletext)   
   
Thanks **ali1234** for creating: [vhs-teletext](https://github.com/ali1234/vhs-teletext)

# Future Functions
* **Ignore Line (record/deconvolve)** - ✅ realized
* **Added Line (record/deconvolve)** - ✅ realized
* **Line numbering (vbiview)** - ✅ realized

# Functions
* **Ignore Line** (`record/deconvolve`) - Ignoring lines when writing to VBI and deconvolving to t42.   
```
teletext record --ignore-line 1,2,20 test.vbi
```
```
teletext deconvolve --ignore-line 1,2,20 test.vbi > test.t42
```

* **Used Line** (`record/deconvolve`) - Using only selected lines when writing to VBI and deconvolving to t42.   
```
teletext record --used-line 4,5 test.vbi
```
```
teletext deconvolve --used-line 4,5 test.vbi > test.t42
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
