import os
import sys

from src.gui import RequestsGUI


class MainGUI:
    def __init__(self, racer):
        super().__init__()
        self.racer = racer

    def show_requests_gui(racer, app, state, cmdprocessor):
        requests_gui = RequestsGUI(racer, state, cmdprocessor)

        requests_gui.show()
        sys.exit(app.exec_())
