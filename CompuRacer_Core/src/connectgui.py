import os
import sys

from src.gui import MainGUI


class ConnectGUI:
    def __init__(self, racer):
        super().__init__()
        self.racer = racer

    def show_requests_gui(racer, app, state, cmdprocessor):
        main_gui = MainGUI(racer, state, cmdprocessor)

        main_gui.show()
        sys.exit(app.exec_())
