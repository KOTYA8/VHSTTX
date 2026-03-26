# VHSTT2

Original repository: [vhs-teletext](https://github.com/ali1234/vhs-teletext)

# Installation
The entire installation was performed on Ubuntu 24.04 LTS.
```
sudo apt update
sudo apt upgrade
sudo apt install python3
sudo apt install python3-pip
sudo apt install git
git clone https://github.com/KOTYA8/VHSTT2.git
cd VHSTT2
sudo apt install pipx
pipx install -e .[spellcheck,viewer]
cd
sudo apt install python3-venv
python3 -m venv myvenv
source myvenv/bin/activate
cd VHSTT2
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
# Functions
* **Ignore Line (record/deconvolve)** - Ignoring lines when writing to VBI and deconvolving to t42. 
`teletext record --ignore-line 1,2,20 test.vbi`
`teletext deconvolve --ignore-line 1,2,20 test.vbi > test.t42`
* **Line numbering (vbiview)** - Line numbering in VBI Viewer.


# Future Functions
* **Ignore Line (record/deconvolve)** - ✅ realized
* **Line numbering (vbiview)** - ✅ realized

# Changelog
All previous versions are available in the repository: [VHSTT2_VER](https://github.com/KOTYA8/VHSTT2_VER)  

### **Currently**  
* **V1** - Support **--ignore-line** for `record` and `deconvolve`. Numbering in `vbiview`. Templates: **fs200sp**, **fs200lp**, **hd630lp**, **hd630sp**, **grundig_2x4**, **hrs9700**, **hd630vdlp**, **hd630vdlp24**, **fs200vdsp**, **fs200vdlp**, **betacamsp**, **betamax**
