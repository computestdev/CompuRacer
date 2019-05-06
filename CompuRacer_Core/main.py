#!/usr/bin/env python3
"""
The main file of the CompuRacer core.
It is used to create a CompuRacer instance and start it.
The main threat is also responsible for shoing any filepicker dialogs.
"""

# --- All imports --- #
import queue
from multiprocessing import Queue
from tkinter import filedialog, Tk

from src.compu_racer_core import CompuRacer

root = Tk()
root.withdraw()

# --- Authorship information --- #
__author__ = "R.J. van Emous @ Computest"
__license__ = "MIT License"
__version__ = "2019"
__email__ = "rvanemous@computest.nl"
__status__ = "Prototype"

# -------------- main client program & server -------------- #
if __name__ == '__main__':

    dialog_queue = Queue()

    # initialize the racer
    racer = CompuRacer(dialog_queue)

    # start the racer
    racer.start()

    # listen for dialogs
    while not racer.is_shutdown:
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

    # exit normally
    exit(0)
