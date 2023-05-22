import json
import os
from typing import List, Any

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import QPushButton, QMainWindow, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QTabWidget, \
    QWidget, QLineEdit, QHBoxLayout, QApplication, QHeaderView, QTableView

from src.batch import Batch


def load_json_batches(directory) -> List[Any]:
    file_names = []
    for filename in os.listdir(directory):
        if filename.endswith(".json"):
            with open(os.path.join(directory, filename), "r") as file:
                data = json.load(file)
                if "name" in data:
                    file_names.append(data["name"])
    return file_names


class MainGUI(QMainWindow):
    def __init__(self, racer, state, command_processor) -> None:
        super().__init__()

        self.request_window = None
        self.general_window = None
        self.batch_window = None
        self.current_batch = None
        self.data_requests = None
        self.table_widget = None
        self.table_widget_requests = None
        self.table_widget_batches = None
        self.file_names = None
        self.directory = None

        self.command_processor = command_processor
        self.racer = racer
        self.state = state

        self.batch_buttons = []
        self.request_buttons = []

        self.load_json_requests()

        self.init_ui()

    def init_ui(self) -> None:
        self.showFullScreen()
        self.setWindowTitle("CompuRacer GUI")

        self.directory = "state/batches"
        self.file_names = load_json_batches(self.directory)

        tabs = QTabWidget()
        general_tab = QWidget()
        logs_tab = QWidget()

        tabs.addTab(general_tab, "General")
        tabs.addTab(logs_tab, "Logs")

        vbox_general = QVBoxLayout()
        vbox_logs = QVBoxLayout()

        # --- Create and load in GUI --- #
        self.create_request_widget(vbox_general, general_tab)
        self.create_batch_widget(vbox_general, general_tab)
        self.create_logs_widget(vbox_logs, logs_tab)

        self.setCentralWidget(tabs)

        return None

    def create_request_widget(self, vbox, requests_tab) -> None:
        vbox.addWidget(QLabel("Requests Information"))

        # --- Creating Table --- #
        self.table_widget_requests = QTableWidget()
        self.table_widget_requests.setColumnCount(8)
        self.table_widget_requests.setColumnWidth(0, 20)
        self.table_widget_requests.setColumnWidth(1, 500)
        self.table_widget_requests.setColumnWidth(3, 200)
        self.table_widget_requests.setHorizontalHeaderLabels(["ID", "URL", "Method", "Timestamp", "Host", "Add To Batch", "Open", "Remove"])
        vbox.addWidget(self.table_widget_requests)

        requests_tab.setLayout(vbox)
        self.table_widget_requests.show()

        self.load_requests()

        return None

    def create_batch_widget(self, vbox, batches_tab) -> None:
        vbox.addWidget(QLabel("Batches Information"))

        # --- Creating table --- #
        self.table_widget_batches = QTableWidget()
        self.table_widget_batches.setColumnCount(6)
        self.table_widget_batches.setColumnWidth(0, 400)
        self.table_widget_batches.setHorizontalHeaderLabels(["Name", "Allow Redirects", "Sync Last Byte", "Send Timeout", "Set Current Batch", "Open Batch"])
        vbox.addWidget(self.table_widget_batches)

        # --- Add new batch --- #
        add_batch_field = QLineEdit()
        add_batch_field_button = QPushButton("Add Batch", self)
        add_batch_field_button.clicked.connect(lambda _, input_field=add_batch_field: self.create_new_batch(input_field))

        # --- Create add batch button and field --- #
        hbox = QHBoxLayout()
        hbox.addWidget(add_batch_field)
        hbox.addWidget(add_batch_field_button)
        vbox.addLayout(hbox)

        # --- Add other important buttons --- #
        quit_button = QPushButton("Quit", self)
        quit_button.clicked.connect(QApplication.quit)
        vbox.addWidget(quit_button)

        self.load_batches()

        batches_tab.setLayout(vbox)

        return None

    def update_json(self) -> None:
        self.save_data()
        self.load_requests()
        self.load_batches()

        return None

    def create_logs_widget(self, vbox, logs_tab) -> None:
        vbox.addWidget(QLabel("Logs"))

        # --- Create table --- #
        self.table_widget = QTableWidget(len(self.data_requests["cp_history"]), 1)
        self.table_widget.setColumnWidth(0, 400)
        self.table_widget.setHorizontalHeaderLabels(["Commands"])
        vbox.addWidget(self.table_widget)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_data)
        vbox.addWidget(save_button)

        self.load_logs()

        logs_tab.setLayout(vbox)

        return None

    def create_requests_button_widget(self, request, row) -> None:
        add_request_button = QPushButton("Add", self)
        window_button = QPushButton("Open", self)
        remove_button = QPushButton("Remove", self)

        add_request_button.clicked.connect(lambda _, request_id=str(request): self.add_request_to_batch(request_id))
        window_button.clicked.connect(lambda _, request_id=request: self.new_request_window(request_id))
        remove_button.clicked.connect(lambda _, request_id=str(request): self.remove_request(request_id))

        self.request_buttons.append((add_request_button, window_button, remove_button))

        self.table_widget_requests.setCellWidget(row, 5, add_request_button)
        self.table_widget_requests.setCellWidget(row, 6, window_button)
        self.table_widget_requests.setCellWidget(row, 7, remove_button)

        return None

    def load_requests(self) -> None:
        self.load_json_requests()

        rows_to_delete = []

        for row in range(self.table_widget_requests.rowCount()):
            request_id = self.table_widget_requests.item(row, 0).text()
            if request_id not in self.data_requests["requests"]:
                rows_to_delete.append(row)

        for row in reversed(rows_to_delete):
            self.table_widget_requests.removeRow(row)

        for request_id, request_data in self.data_requests["requests"].items():
            existing_row = None
            for row in range(self.table_widget_requests.rowCount()):
                if self.table_widget_requests.item(row, 0).text() == request_id:
                    existing_row = row
                    break

            if existing_row is not None:
                self.table_widget_requests.setItem(existing_row, 1, QTableWidgetItem(str(request_data["url"])))
                self.table_widget_requests.setItem(existing_row, 2, QTableWidgetItem(str(request_data["method"])))
                self.table_widget_requests.setItem(existing_row, 3, QTableWidgetItem(str(request_data["timestamp"])))
                headers = request_data.get("headers", {})
                host = headers.get("Host", "")
                self.table_widget_requests.setItem(existing_row, 4, QTableWidgetItem(str(host)))
            else:
                row = self.table_widget_requests.rowCount()
                self.table_widget_requests.insertRow(row)
                self.table_widget_requests.setItem(row, 0, QTableWidgetItem(str(request_id)))
                self.table_widget_requests.setItem(row, 1, QTableWidgetItem(str(request_data["url"])))
                self.table_widget_requests.setItem(row, 2, QTableWidgetItem(str(request_data["method"])))
                self.table_widget_requests.setItem(row, 3, QTableWidgetItem(str(request_data["timestamp"])))
                headers = request_data.get("headers", {})
                host = headers.get("Host", "")
                self.table_widget_requests.setItem(row, 4, QTableWidgetItem(str(host)))

                self.create_requests_button_widget(request_id, row)

        return None

    def load_batches(self) -> callable([]):
        self.directory = "state/batches"
        self.file_names = load_json_batches(self.directory)

        self.batch_buttons.clear()

        current_batch = self.data_requests["current_batch"]

        # remove existing rows
        for row in reversed(range(self.table_widget_batches.rowCount())):
            self.table_widget_batches.removeRow(row)

        def load_table():
            for idx, name in enumerate(self.file_names):
                # --- Create command-buttons --- #
                current_button = QPushButton("Set Current", self)
                window_button = QPushButton("Open", self)

                current_button.clicked.connect(lambda _, batch_name=name: self.set_current_batch(batch_name))
                window_button.clicked.connect(lambda _, batch_name=name: self.new_batch_window(batch_name))

                self.batch_buttons.append((current_button, window_button))

                # --- Insert row number {forloopnumber} --- #
                row = self.table_widget_batches.rowCount()
                self.table_widget_batches.insertRow(row)
                self.table_widget_batches.setItem(row, 0, QTableWidgetItem(str(name)))
                self.table_widget_batches.setCellWidget(row, 4, current_button)
                self.table_widget_batches.setCellWidget(row, 5, window_button)

                self.check_current_batch(name, row, current_button, window_button, current_batch)

                data = self.get_json_data(name)

                for col, col_name in enumerate(["Allow Redirects", "Sync Last Byte", "Send Timeout"]):
                    value = data.get(col_name.lower().replace(" ", "_"))
                    self.table_widget_batches.setItem(row, col + 1, QTableWidgetItem(str(value)))

                    if name == current_batch:
                        item = self.table_widget_batches.item(row, col + 1)
                        if item is not None:
                            item.setBackground(Qt.gray)

        load_table()

        return load_table

    def load_logs(self) -> None:
        for command in enumerate(self.data_requests["cp_history"]):
            row = self.table_widget.rowCount()
            self.table_widget.insertRow(row)
            self.table_widget.setItem(row, 0, QTableWidgetItem(str(command)))

        return None

    def add_request_to_batch(self, request_id) -> None:
        self.racer.comm_curr_add(self.racer, request_id)

        self.update_json()

        return None

    def get_json_data(self, name) -> dict:
        with open(os.path.join(self.directory, name + ".json"), "r") as file:
            data = json.load(file)

        return data

    def check_current_batch(self, name, row, button1, button2, current_batch) -> None:
        if name == current_batch:
            for col in range(self.table_widget_batches.columnCount()):
                item = self.table_widget_batches.item(row, col)
                if item is not None:
                    item.setBackground(Qt.gray)
            button1.setEnabled(False)
            button2.setEnabled(True)
        else:
            button2.setEnabled(False)

        if name == "Imm":
            button1.setEnabled(False)
            button2.setEnabled(False)

        return None

    def load_json_requests(self) -> None:
        with open('state/state.json', 'r') as f:
            self.data_requests = json.load(f)

        return None

    def save_data(self) -> None:
        self.racer.comm_general_save()

        return None

    def set_current_batch(self, batch_name) -> None:
        self.racer.set_curr_batch_by_name(self.racer, batch_name)
        self.current_batch = batch_name

        self.update_json()

        return None

    def remove_request(self, request_id) -> None:
        self.racer.comm_requests_remove(self.racer, request_id, None, False)

        self.update_json()

        return None

    def new_batch_window(self, batch_name) -> None:
        self.save_data()
        self.batch_window = BatchWindow(batch_name, self.racer, self.state, self.command_processor)
        self.batch_window.show()
        self.hide()

        return None

    def new_request_window(self, request_id) -> None:
        self.update_json()
        self.request_window = RequestWindow(request_id, self.racer, self.state, self.command_processor)
        self.request_window.show()
        self.hide()

        return None

    def create_new_batch(self, batch_name) -> None:
        batch_name = batch_name.text()
        self.racer.comm_batches_create_new(self.racer, batch_name)

        self.update_json()

        return None


class BatchWindow(QMainWindow):
    def __init__(self, batch_name, racer, state, command_processor) -> None:
        super().__init__()

        self.table_widget_propperty = None
        self.showFullScreen()
        self.setWindowTitle("Batch: " + batch_name)

        self.general_window = None
        self.table_widget = QTableWidget()

        self.racer = racer
        self.batch_name = batch_name
        self.state = state
        self.command_processor = command_processor

        self.batch_requests = []

        self.init_ui()

    def init_ui(self) -> None:
        vbox = QVBoxLayout()
        batch_tab = QWidget()

        batch_tab.setLayout(vbox)
        batch_tab.layout().addWidget(self.table_widget)

        tabs = QTabWidget()
        tabs.addTab(batch_tab, "Batch")

        vbox.addStretch()

        self.add_batch_propperty_widget(vbox)

        self.add_button_widget(vbox)
        self.setCentralWidget(tabs)
        self.add_request_table(vbox)

        vbox.insertWidget(0, self.table_widget)

        tabs.addTab(batch_tab, "Batch")

        return None

    def load_json(self, filepath) -> List[Any]:
        with open(filepath, 'r') as file:
            data = json.load(file)

        return data

    def update_json(self) -> None:
        self.save_data()
        self.load_requests()
        self.load_propperties()

        return None

    def add_batch_propperty_widget(self, vbox) -> None:
        self.table_widget_propperty = QTableWidget()
        self.table_widget_propperty.setColumnCount(4)
        self.table_widget_propperty.setHorizontalHeaderLabels(["Allow Redirects", "Sync Last Byte", "Send Timeout", "Batch Sent"])
        vbox.addWidget(self.table_widget_propperty)

        self.add_propperty_widget(vbox)
        self.load_propperties()

        return None

    def add_propperty_widget(self, vbox) -> None:
        change_allow_redirects_button = QPushButton("Change Allow Redirects", self)
        change_allow_redirects_button.clicked.connect(lambda _: self.change_allow_redirects())

        change_sync_last_byte_button = QPushButton("Change Sync Last Byte", self)
        change_sync_last_byte_button.clicked.connect(lambda _: self.change_sync_last_byte())

        change_send_timeout_field = QLineEdit()
        change_send_timeout_button = QPushButton("Change Send Timeout", self)
        change_send_timeout_button.clicked.connect(lambda _, input_field=change_send_timeout_field: self.change_send_timeout(input_field))

        hbox = QHBoxLayout()
        hbox.addWidget(change_allow_redirects_button)
        hbox.addWidget(change_sync_last_byte_button)

        hbox.addWidget(change_send_timeout_field)
        hbox.addWidget(change_send_timeout_button)

        vbox.addLayout(hbox)

        return None

    def load_propperties(self) -> None:
        properties = self.load_json("state/batches/" + self.batch_name + ".json")
        allow_redirects = properties["allow_redirects"]
        sync_last_byte = properties["sync_last_byte"]
        send_timeout = properties["send_timeout"]
        sent_batch = properties["results"]

        self.table_widget_propperty.setRowCount(1)

        self.table_widget_propperty.setItem(0, 0, QTableWidgetItem(str(allow_redirects)))
        self.table_widget_propperty.setItem(0, 1, QTableWidgetItem(str(sync_last_byte)))
        self.table_widget_propperty.setItem(0, 2, QTableWidgetItem(str(send_timeout)))
        if len(sent_batch) == 0:
            self.table_widget_propperty.setItem(0, 3, QTableWidgetItem("False"))
        else:
            self.table_widget_propperty.setItem(0, 3, QTableWidgetItem("True"))

        return None

    def add_button_widget(self, vbox) -> None:
        send_batch_button = QPushButton("Send Batch")
        go_back_button = QPushButton("Go Back")
        quit_button = QPushButton("Quit")

        send_batch_button.clicked.connect(self.send_batch)
        go_back_button.clicked.connect(self.go_back)
        quit_button.clicked.connect(QApplication.quit)

        vbox.addWidget(send_batch_button, alignment=Qt.AlignBottom)
        vbox.addWidget(go_back_button, alignment=Qt.AlignBottom)
        vbox.addWidget(quit_button, alignment=Qt.AlignBottom)

        return None

    def add_request_table(self, vbox) -> None:
        self.table_widget.setColumnCount(8)
        self.table_widget.setHorizontalHeaderLabels(["ID", "Method", "URL", "Host", "Delay Time", "Num Parallel", "Num Sequential", "Remove"])
        self.table_widget.setColumnWidth(0, 50)
        self.table_widget.setColumnWidth(1, 50)
        self.table_widget.setColumnWidth(2, 600)
        self.table_widget.setColumnWidth(3, 100)
        self.table_widget.setColumnWidth(4, 100)

        vbox.addWidget(self.table_widget)

        self.load_requests()

        return None

    def change_allow_redirects(self) -> None:
        allow_redirects = self.load_json("state/batches/" + self.batch_name + ".json")["allow_redirects"]

        if allow_redirects is True:
            self.racer.comm_curr_change_redirects(self.racer, False)
        else:
            self.racer.comm_curr_change_redirects(self.racer, True)

        self.update_json()

        return None

    def change_sync_last_byte(self):
        sync_last_byte = self.load_json("state/batches/" + self.batch_name + ".json")["sync_last_byte"]

        if sync_last_byte is True:
            self.racer.comm_curr_change_sync(self.racer, False)
        else:
            self.racer.comm_curr_change_sync(self.racer, True)

        self.update_json()

        return None

    def change_send_timeout(self, value):
        if value.text().isdigit():
            send_timeout = int(value.text())

            self.racer.comm_curr_change_timeout(self.racer, send_timeout)
            self.update_json()

        return None

    def load_requests(self) -> None:
        items = self.load_json("state/batches/" + self.batch_name + ".json")["items"]
        requests = self.load_json("state/state.json")["requests"]
        remove_button = QPushButton("Remove", self)

        self.table_widget.setRowCount(len(items))

        for i, item in enumerate(items):
            request_id = item["key"][0]
            delay_time = item["key"][1]
            num_parallel = item["value"][0]
            num_sequential = item["value"][1]

            request = requests[request_id]

            method = request["method"]
            url = request["url"]
            host = request["headers"]["Host"]

            self.table_widget.setItem(i, 0, QTableWidgetItem(request_id))
            self.table_widget.setItem(i, 1, QTableWidgetItem(method))
            self.table_widget.setItem(i, 2, QTableWidgetItem(url))
            self.table_widget.setItem(i, 3, QTableWidgetItem(host))
            self.table_widget.setItem(i, 4, QTableWidgetItem(str(delay_time)))
            self.table_widget.setItem(i, 5, QTableWidgetItem(str(num_parallel)))
            self.table_widget.setItem(i, 6, QTableWidgetItem(str(num_sequential)))
            self.table_widget.setCellWidget(i, 7, remove_button)

            remove_button.clicked.connect(lambda _, request_id=str(request_id): self.remove_request(request_id))

    def send_batch(self) -> None:
        self.save_data()
        self.racer.comm_batches_send(self.racer)

        self.update_json()

        return None

    def go_back(self) -> None:
        self.general_window = MainGUI(self.racer, self.state, self.command_processor)
        self.general_window.show()
        self.hide()

        return None

    def save_data(self) -> None:
        self.racer.comm_general_save(True)

        return None

    def remove_request(self, request_id) -> None:
        self.racer.comm_requests_remove(self.racer, request_id, None, False)

        self.update_json()

        return None


class RequestWindow(QMainWindow):
    def __init__(self, request_id, racer, state, command_processor):
        super().__init__()

        self.request_id = request_id
        self.racer = racer
        self.state = state
        self.command_processor = command_processor

        self.general_window = None

        self.table_widget = QWidget()

        self.init_ui()

    def init_ui(self):
        vbox = QVBoxLayout()

        request_tab = QWidget()
        request_tab.setLayout(vbox)
        request_tab.layout().addWidget(self.table_widget)

        tabs = QTabWidget()
        tabs.addTab(request_tab, "Request")

        vbox.addStretch()

        self.add_button_widget(vbox)
        self.setCentralWidget(tabs)
        self.load_request()

        vbox.insertWidget(0, self.table_widget)

    def load_request(self) -> None:
        requests_data = self.load_json("state/state.json")["requests"]
        request_data = requests_data.get(str(self.request_id))

        if not request_data:
            return

        # --- Ready the data --- #
        body = request_data.get("body", "")
        headers = request_data.get("headers", {})
        method = request_data.get("method", "")
        timestamp = request_data.get("timestamp", "")
        url = request_data.get("url", "")
        request_id = request_data.get("id", "")

        # --- Create model and add headers --- #
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Field", "Value"])

        # --- Insert data into rows --- #
        model.appendRow([QStandardItem("Request ID"), QStandardItem(str(request_id))])
        model.appendRow([QStandardItem("URL"), QStandardItem(url)])
        model.appendRow([QStandardItem("Method"), QStandardItem(method)])
        model.appendRow([QStandardItem("Timestamp"), QStandardItem(str(timestamp))])
        model.appendRow([QStandardItem("Body"), QStandardItem(body)])
        for key, value in headers.items():
            model.appendRow([QStandardItem(key), QStandardItem(value)])

        table_view = QTableView()
        table_view.setModel(model)

        table_view.horizontalHeader().setStretchLastSection(True)
        table_view.verticalHeader().setVisible(False)
        table_view.setShowGrid(True)
        table_view.setEditTriggers(QTableView.NoEditTriggers)

        # Set grid color to background color
        table_view.setStyleSheet(
            "QTableView::item {border-bottom: 1px solid black;} QTableView {background-color: white;}")
        table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table_view.setColumnWidth(0, 300)

        self.table_widget = table_view
        self.layout().addWidget(self.table_widget)

        return None

    def add_button_widget(self, vbox) -> None:
        quit_button = QPushButton("Quit")
        go_back_button = QPushButton("Go Back")

        quit_button.clicked.connect(QApplication.quit)
        go_back_button.clicked.connect(self.go_back)

        vbox.addWidget(quit_button, alignment=Qt.AlignBottom)
        vbox.addWidget(go_back_button, alignment=Qt.AlignBottom)

        return None

    def load_json(self, filepath):
        with open(filepath, 'r') as file:
            data = json.load(file)

        return data

    def go_back(self) -> None:
        self.general_window = MainGUI(self.racer, self.state, self.command_processor)
        self.general_window.show()
        self.deleteLater()

        return None

    def save_data(self) -> None:
        self.racer.comm_general_save(True)

        return None
