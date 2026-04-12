import multiprocessing as mp

from teletext.gui.editor import main


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
