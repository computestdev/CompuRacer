#!/usr/bin/env python3
"""
The main file of the CompuRacer core.
It is used to create a CompuRacer instance and start it.
The main threat is also responsible for shoing any filepicker dialogs.
"""

# --- All imports --- #
import argparse
import queue
import os, sys
import time
from multiprocessing import Queue

# Check Python version >= 3.7
if not sys.version_info >= (3, 7):
    sys.stderr.write("CompuRacer requires Python version >= 3.7\n\t"
                     "On Linux use: sudo apt-get install python3.7\n\t"
                     "              python3.7 main.py\n")
    exit(1)

# check whether external libs (from requirements.txt) are installed
# # by trying to import some of them
try:
    from tqdm import tqdm
    from tabulate import tabulate
    from bs4 import BeautifulSoup
except ModuleNotFoundError:
    sys.stderr.write("Could not find external dependencies, please run:\n\t"
                     "python3.7 -m pip install -r requirements.txt\n")
    exit(1)

# check whether the system has a display
use_tkinter = False
if 'DISPLAY' in os.environ:
    # check whether tkinter system lib is installed
    try:
        from tkinter import filedialog, Tk
    except ModuleNotFoundError as e:
        print("tkinter lib not found on system\n\t"
              "On Linux use: sudo apt-get install python3-tk\n")
        exit(1)
    use_tkinter = True

from src.compu_racer_core import CompuRacer

root = None
if use_tkinter:
    root = Tk()
    root.withdraw()

# --- Authorship information --- #
__author__ = "R.J. van Emous @ Computest, B. van Wijk @ Computest"
__license__ = "MIT License"
__version__ = "v1.0.0 2023"
__email__ = "rvanemous@computest.nl, bvanwijk@computest.nl"
__status__ = "Production v1"

# --- Checking for arguments --- #
parser = argparse.ArgumentParser()
parser.add_argument("--port",
                    nargs='?',
                    help="Sets the port number of the REST server",
                    type=int,
                    default=None)
parser.add_argument("--proxy",
                    nargs='?',
                    help="Sets the SOCKS proxy <IP-adress>:<port> to use",
                    type=str,
                    default=None)
parser.add_argument("--cli",
                    action="store_true",
                    help="Disables the standard ui",
                    )

args = parser.parse_args()

# --- if --cli is used, this will be true --- #
use_only_cli = args.cli

# -------------- main client program & server -------------- #
if __name__ == '__main__':
    if use_tkinter:
        dialog_queue = Queue()
    else:
        # cannot display filepicker -> import by batch name
        dialog_queue = None

    # initialize the racer
    if args.proxy:
        racer = CompuRacer(args.port, f"socks5://{args.proxy}", dialog_queue, use_only_cli)
    else:
        racer = CompuRacer(args.port, None, dialog_queue, use_only_cli)

    # start the racer
    racer.start(use_only_cli)

    # listen for dialogs or wait
    while not racer.is_shutdown:
        if use_tkinter:
            try:
                new_dialog_req = dialog_queue.get(timeout=2)
            except queue.Empty:
                pass
            except Exception as e:
                print(e)
            else:
                dialog_queue.put(filedialog.askopenfilename(
                    filetypes=new_dialog_req['filetypes'],
                    title=new_dialog_req['title'],
                    initialdir=new_dialog_req['start_dir'],
                    multiple=True
                ))
                root.update()
        else:
            time.sleep(0.5)

    # exit normally
    exit(0)
