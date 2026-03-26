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
git clone https://github.com/KOTYA/VHSTT2.git
cd vhs-teletext
sudo apt install pipx
pipx install -e .[spellcheck,viewer]
cd
sudo apt install python3-venv
python3 -m venv myvenv
source myvenv/bin/activate
cd vhs-teletext
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

# Future Functions
