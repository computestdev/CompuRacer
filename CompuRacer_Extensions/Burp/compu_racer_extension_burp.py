'''
MIT License

Copyright (c) 2019

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

import base64
import threading
import time
import json
import traceback

import requests

from java.lang import RuntimeException, Object
from java.awt import Dimension, GridLayout, GridBagLayout, GridBagConstraints, BorderLayout, FlowLayout, Insets
from javax.swing import JFrame, JPanel, JTabbedPane, JScrollPane, JSplitPane, JMenu, JMenuItem, JCheckBoxMenuItem, JOptionPane, JLabel, ImageIcon, JCheckBox, JTextField, JComboBox, JButton
from burp import ITab, IMessageEditorController, IBurpExtender, IHttpRequestResponse, IResponseInfo, IContextMenuFactory, IContextMenuInvocation, \
    IExtensionStateListener
from org.python.core import PyException

# --- Configuration --- #
compuRacer_ip = '127.0.0.1'
compuRacer_port = '8099'
add_request_path = 'add_requests'
immediate_data_path = 'immediate_data'
immediate_results_path = 'immediate_results'
alive_check_path = ''

racer_alive = False
DEFAULT_RESULTS = '> No results yet, so please send a request! :)'
ADDITIONAL_SEND_TIMEOUT_WAIT = 5
immediate_data = {'mode': 'off', 'settings': [10, 1, False, False, 20], 'results': DEFAULT_RESULTS}
immediate_data_ui_elements = {'parallel_requests': None, 'allow_redirects': None, 'sync_last_byte': None, 'send_timeout': None}

_textEditors = []
_requestViewers = []
_requestPane = None
_storedRequests = []

compuracer_communication_lock = threading.Lock()

class Cb:
    callbacks = None
    helpers = None

    def __init__(self, callbacks):
        Cb.callbacks = callbacks
        Cb.helpers = callbacks.getHelpers()


class MenuFactory(IContextMenuFactory):

    def __init__(self):
        self.invoker = None
        self.messages = None
        # self.load_icon()

    def createMenuItems(self, invoker):
        global immediate_data, compuracer_communication_lock
        self.invoker = invoker

        if not (invoker.getInvocationContext() == IContextMenuInvocation.CONTEXT_MESSAGE_EDITOR_REQUEST or
                invoker.getInvocationContext() == IContextMenuInvocation.CONTEXT_MESSAGE_VIEWER_REQUEST or
                invoker.getInvocationContext() == IContextMenuInvocation.CONTEXT_TARGET_SITE_MAP_TABLE or
                invoker.getInvocationContext() == IContextMenuInvocation.CONTEXT_PROXY_HISTORY
        ):
            return None

        self.messages = self.invoker.getSelectedMessages()
        if self.messages is None:
            return None
        if len(self.messages) > 1:
            button_text = "Send {} requests to CompuRacer".format(len(self.messages))
        else:
            button_text = "Send request to CompuRacer"
        global racer_alive
        if not racer_alive:
            button_text += " (offline)"
        elif compuracer_communication_lock.locked():
            button_text += " (busy)"
        send_menu = JMenuItem(button_text, actionPerformed=self.start_request_transmitter)
        option_menu = JCheckBoxMenuItem("Immediate mode", actionPerformed=self.mode_changed)
        option_menu.setSelected(immediate_data['mode'] == 'on')
        # self.set_icon(menu_send)
        send_menu.setEnabled(racer_alive and not compuracer_communication_lock.locked())
        option_menu.setEnabled(racer_alive and not compuracer_communication_lock.locked())
        return [send_menu, option_menu]

    def start_request_transmitter(self, event):
        t = threading.Thread(name='Request transmitter', target=self.send_requests_batched_to_racer,
                             args=(self.messages,))
        t.start()

    @staticmethod
    def mode_changed(event):
        global immediate_data, compuracer_communication_lock
        is_selected = MenuFactory.button_selected(event)
        if is_selected != (immediate_data['mode'] == 'on'):
            with compuracer_communication_lock:
                if is_selected:
                    new_mode = 'on'
                else:
                    new_mode = 'off'
                if MenuFactory.set_immediate_mode_settings({'mode': new_mode}):
                    immediate_data['mode'] = new_mode
                    print("> Immediate mode enabled = {}".format(is_selected))
                else:
                    print("> Failed to enable immediate mode!")

    @staticmethod
    def set_same_result_messages(message, add_newline=True):
        global _textEditors
        if add_newline:
            message = "\n" + message
        for _textEditor in _textEditors:
            _textEditor.setText(str.encode(message))

    @staticmethod
    def set_result_messages(messages, add_newline=True):
        global _textEditors
        addition = ""
        if add_newline:
            addition = "\n"
        for i, _textEditor in enumerate(_textEditors):
            _textEditor.setText(str.encode(addition + messages[i]))

    @staticmethod
    def set_state_of_all_buttons(enabled):
        immediate_data_ui_elements["parallel_requests"].setEnabled(enabled)
        immediate_data_ui_elements["allow_redirects"].setEnabled(enabled)
        immediate_data_ui_elements["sync_last_byte"].setEnabled(enabled)
        immediate_data_ui_elements["send_timeout"].setEnabled(enabled)
        immediate_data_ui_elements["resend_batch"].setEnabled(enabled)

    @staticmethod
    def reset_request_tabs(the_requests):
        global _requestViewers, _requestPane
        _requestPane.removeAll()
        for i, request in enumerate(the_requests):
            _requestViewers.append(Cb.callbacks.createMessageEditor(None, False))
            _requestPane.addTab("Request {}".format(i), _requestViewers[-1].getComponent())
            _requestViewers[-1].setMessage(request.getRequest(), True)

    @staticmethod
    def start_request_transmitter_button(event):
        t = threading.Thread(name='Request transmitter', target=MenuFactory.send_stored_requests_batched_to_racer,
                             args=(event,))
        t.start()

    @staticmethod
    def send_stored_requests_batched_to_racer(event):
        global _storedRequests
        if _storedRequests is not None and _storedRequests:
            MenuFactory.send_requests_batched_to_racer(_storedRequests, True)

    @staticmethod
    def send_requests_batched_to_racer(the_requests, resend=False):
        global _requestViewers, _textEditors, _storedRequests, \
            ADDITIONAL_SEND_TIMEOUT_WAIT, compuracer_communication_lock

        print("1")
        MenuFactory.set_state_of_all_buttons(False)
        if not resend:
            try:
                MenuFactory.reset_request_tabs(the_requests)
                _storedRequests = the_requests
            except Exception as e:
                print(e)
        print("2")
        with compuracer_communication_lock:
            MenuFactory.set_same_result_messages("> Sending request(s) to CompuRacer..")
            for i in range(0, len(the_requests), 50):
                end = min(i + 50, len(the_requests))
                MenuFactory.send_requests_to_racer(the_requests[i:end])
            print("> Done sending {} request(s) to racer.".format(len(the_requests)))
            if immediate_data['mode'] == 'on':
                time.sleep(3)
                print("> Fetching results..")
                MenuFactory.set_same_result_messages(
                    "> Fetching result(s) from CompuRacer.. (takes up to {} seconds)".format(
                        immediate_data['settings'][4] + ADDITIONAL_SEND_TIMEOUT_WAIT))
                got_results = False
                # wait the send timeout + ADDITIONAL_SEND_TIMEOUT_WAIT seconds
                end_time = time.time() + immediate_data['settings'][4] + ADDITIONAL_SEND_TIMEOUT_WAIT
                try:
                    while not got_results and time.time() < end_time:
                        success, results = MenuFactory.get_immediate_mode_results()
                        if success and results is not None and 'No results' not in results[0]:
                            # set summary, full results and config
                            MenuFactory.set_result_messages(results)
                            print("4")
                            got_results = True

                        time.sleep(1)
                except Exception as e:
                    print(e)
                if not got_results:
                    MenuFactory.set_same_result_messages("> No results due to a timeout."
                                                         "\n> Please increase the send timeout and try again.")

            else:
                MenuFactory.set_same_result_messages("> The request is not send, so no results can be shown.\n"
                                                     "> Enable the immediate mode and send it again.")
        MenuFactory.set_state_of_all_buttons(True)


    # does not work..
    def load_icon(self):
        with open("icon_arrow--green.jpg", "rb") as in_file:
            pic = in_file.read()
        print(pic)
        try:
            self.icon = ImageIcon(pic, "CompuRacerIcon")
        except Exception as e:
            print(e)
            print("Cannot load image!")
        print(self.icon)
        print(self.icon.getImageLoadStatus())
        print(self.icon.getImageObserver())

    # does not work..
    def set_icon(self, menu_send):
        try:
            menu_send.setIcon(self.icon)
        except Exception as e:
            print("Failed! {}".format(e))

    @staticmethod
    def button_selected(event):
        button = event.getSource()
        return bool(button.getModel().isSelected())

    @staticmethod
    def item_selected(event):
        button = event.getSource()
        return int(str(button.getSelectedItem()))

    @staticmethod
    def get_immediate_mode_results():
        success = False
        results = None
        try:
            response = requests.get(url="http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, immediate_results_path),
                                    timeout=5)
        except Exception as e:
            print("Oh noo:")
            print(e)
        else:
            # print("> Got response: {}\n".format(response.status_code))
            if int(response.status_code) < 300:
                if 'results' in response.json():
                    if response.json()[u'results'] is not None:
                        results = []
                        for item in response.json()[u'results']:
                            results.append(item.encode('ascii'))
                    else:
                        results = None
                    success = True
            else:
                print("> Failed to get immediate results!\n")
        return success, results

    @staticmethod
    def get_immediate_mode_settings():
        success = False
        mode = None
        settings = None
        try:
            response = requests.get(url="http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, immediate_data_path),
                                    timeout=5)
        except Exception as e:
            print("Oh noo:")
            print(e)
        else:
            # print("> Got response: {}\n".format(response.status_code))
            if int(response.status_code) < 300:
                mode = response.json()[u'mode']
                settings = response.json()[u'settings']
                success = True
            else:
                print("> Failed to get immediate data!\n")
        return success, mode, settings


    @staticmethod
    def set_immediate_mode_settings(immediate_data):
        print("> Setting immediate data: {}..".format(immediate_data))
        success = False
        try:
            response_str = requests.post(url="http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, immediate_data_path),
                                         json=immediate_data,
                                         timeout=5
                                         ).status_code
        except Exception as e:
            print("Oh noo:")
            print(e)
        else:
            print("> Got response: {}\n".format(response_str))
            if int(response_str) < 300:
                print("> Success in settings immediate data.")
                success = True
            else:
                print("> Failed to set immediate data")
        return success


    @staticmethod
    def send_requests_to_racer(the_requests):
        print("> Sending {} request(s) to racer..".format(len(the_requests)))
        # header for request to racer
        global compuRacer_ip, compuRacer_port, add_request_path
        request_headers = ["POST /{} HTTP/1.1".format(add_request_path),
                           "Host: {}:{}".format(compuRacer_ip, compuRacer_port),
                           "Accept: */*",
                           "Connection: close",
                           "Content-Type: application/json",
                           ]  # it auto-generates the content-length header
        print(request_headers)
        # build header and json body of sub-request
        total_body = {'requests': []}
        for i, request in enumerate(the_requests):
            details = Cb.helpers.analyzeRequest(request)
            headers_list = list(details.getHeaders())
            try:
                protocol = str(details.getUrl()).split(":")[0]
                url_end = headers_list[0].split(" ")[1]
                url_start = headers_list[1].split(": ")[1]
                print("Whole URL:", protocol, url_end, url_start)
                url = protocol + "://" + url_start + url_end
            except Exception as e:
                print("Header parsing failed! Skipping: {}".format(details.getHeaders()))
                continue
            headers_list = headers_list[1:]
            try:
                headers = {str(header[0]): str(header[1]) for header in [header.split(": ") for header in headers_list]}
            except IndexError:
                print("Header parsing failed! Skipping: {}".format(headers_list))
                continue
            req_bytes = request.getRequest()
            body = ""
            if req_bytes and 0 < details.getBodyOffset() < len(req_bytes):
                try:
                    if 'Content-Type' in headers and headers['Content-Type'].startswith("multipart/form-data"):
                        body = "BASE64=" + base64.b64encode(req_bytes[details.getBodyOffset():])
                    else:
                        body = Cb.helpers.bytesToString(req_bytes[details.getBodyOffset():])
                except Exception as e:
                    print("Error:", e)
            else:
                body = ""
            total_body['requests'].append(json.dumps({"url": str(url),
                                                      "method": str(details.getMethod()),
                                                      "headers": headers,
                                                      "body": body}))

        total_body_bytes = Cb.helpers.stringToBytes(json.dumps(total_body))
        print('Requests: ', len(total_body['requests']), ' body: ', len(total_body_bytes), 'bytes')

        print("> Sending requests: \n{}\n".format(request_headers[0]))
        try:
            response_str = requests.post(url="http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, add_request_path),
                                         json=total_body,
                                         timeout=5
                                         ).status_code
        except Exception as e:
            print("Oh noo:")
            print(e)
        else:
            print("> Got response: \n{}\n".format(response_str))
            if int(response_str) >= 300:
                print("> Failed to send the_requests: {}".format(total_body))
            else:
                print("> Done sending the_requests to racer!\n")


    @staticmethod
    def make_request(method, url, headers, body, timeout):
        try:
            response = requests.request(method=method,
                                        url="/".join(url.split("/")[1:]),
                                        headers=headers,
                                        body=body,
                                        timeout=timeout)
        except Exception as e:
            print(e)
            return 400
        else:
            return response.status_code


class Item(Object):
    def __init__(self, item):
        self.key = item["key"]
        self.name = item["name"]

    def toString(self):
        return self.name


class BurpExtender(IBurpExtender, IExtensionStateListener, ITab):
    ext_name = "CompuRacerExtension"
    ext_version = '1.2'
    loaded = True
    t = None

    def registerExtenderCallbacks(self, callbacks):
        Cb(callbacks)
        Cb.callbacks.setExtensionName(self.ext_name)

        try:
            global compuracer_communication_lock

            # option picker item objects (for Java compatibility)
            item1 = {'key': 'item1', 'name': '2'}
            item2 = {'key': 'item2', 'name': '3'}
            item3 = {'key': 'item3', 'name': '4'}
            item4 = {'key': 'item4', 'name': '5'}
            item5 = {'key': 'item5', 'name': '10'}
            item6 = {'key': 'item6', 'name': '15'}
            item7 = {'key': 'item7', 'name': '20'}
            item8 = {'key': 'item8', 'name': '25'}
            item9 = {'key': 'item9', 'name': '50'}
            item10 = {'key': 'item10', 'name': '100'}

            # main splitted pane + top pane
            self._main_splitpane = JSplitPane(JSplitPane.VERTICAL_SPLIT)
            self._outer_settings_pane = JPanel(BorderLayout())
            self._settings_pane = JPanel(GridBagLayout())
            c = GridBagConstraints()

            self.label_1 = JLabel("Number of parallel requests:")
            c.fill = GridBagConstraints.NONE
            c.gridx = 0
            c.gridy = 0
            c.insets = Insets(0, 5, 0, 10)
            c.anchor = GridBagConstraints.LINE_START
            self._settings_pane.add(self.label_1, c)

            self.input_parallel_requests = JComboBox([Item(item1), Item(item2), Item(item3), Item(item4), Item(item5), Item(item6),
                                   Item(item7), Item(item8), Item(item9), Item(item10)])
            self.input_parallel_requests.setSelectedIndex(4)
            self.input_parallel_requests.setToolTipText("Select the number of parallel requests that will be sent")
            self.input_parallel_requests.addActionListener(self.change_parallel_requests)
            c.gridx = 1
            c.gridy = 0
            c.insets = Insets(0, 5, 0, 10)
            self._settings_pane.add(self.input_parallel_requests, c)

            self.option_allow_redirects = JCheckBox("Allow redirects", actionPerformed=self.check_allow_redirects)
            self.option_allow_redirects.setToolTipText("Select whether redirect responses are followed")
            c.gridx = 2
            c.gridy = 0
            c.insets = Insets(0, 20, 0, 10)
            self._settings_pane.add(self.option_allow_redirects, c)

            self.option_sync_last_byte = JCheckBox("Sync last byte", actionPerformed=self.check_sync_last_byte)
            self.option_sync_last_byte.setToolTipText("Select whether last byte synchronisation is enabled")
            c.gridx = 2
            c.gridy = 1
            c.insets = Insets(0, 20, 0, 0)
            self._settings_pane.add(self.option_sync_last_byte, c)

            self.label_2 = JLabel("Send timeout in seconds:")
            c.gridx = 0
            c.gridy = 1
            c.insets = Insets(0, 5, 0, 0)
            self._settings_pane.add(self.label_2, c)

            self.input_send_timeout = JComboBox([Item(item2), Item(item4), Item(item5), Item(item7), Item(item9), Item(item10)])
            self.input_send_timeout.setSelectedIndex(3)
            self.input_send_timeout.setToolTipText("Select the wait-for-response timeout after sending the request(s)")
            self.input_send_timeout.addActionListener(self.change_send_timeout)
            c.gridx = 1
            c.gridy = 1
            c.insets = Insets(0, 5, 0, 0)
            self._settings_pane.add(self.input_send_timeout, c)

            self.button_resend_batch = JButton("Resend requests")
            self.button_resend_batch.setToolTipText("Resend all requests with the current configuration")
            self.button_resend_batch.setEnabled(False)
            self.button_resend_batch.addActionListener(MenuFactory.start_request_transmitter_button)
            c.gridx = 3
            c.gridy = 0
            c.insets = Insets(0, 20, 0, 10)
            self._settings_pane.add(self.button_resend_batch, c)

            immediate_data_ui_elements["parallel_requests"] = self.input_parallel_requests
            immediate_data_ui_elements["allow_redirects"] = self.option_allow_redirects
            immediate_data_ui_elements["sync_last_byte"] = self.option_sync_last_byte
            immediate_data_ui_elements["send_timeout"] = self.input_send_timeout
            immediate_data_ui_elements["resend_batch"] = self.button_resend_batch

            c = GridBagConstraints()
            c.anchor = GridBagConstraints.WEST
            self._outer_settings_pane.add(self._settings_pane, BorderLayout.WEST)
            self._main_splitpane.setTopComponent(self._outer_settings_pane)

            self._results_splitpane = JSplitPane(JSplitPane.HORIZONTAL_SPLIT)
            self._main_splitpane.setBottomComponent(self._results_splitpane)

            # table of log entries
            self.tabs_right = JTabbedPane()
            global _textEditors, DEFAULT_RESULTS
            for i in range(3):
                _textEditors.append(Cb.callbacks.createTextEditor())
                _textEditors[-1].setText(str.encode("\n" + DEFAULT_RESULTS))

            self.tabs_right.add("Summary", _textEditors[0].getComponent())
            self.tabs_right.add("Full result", _textEditors[1].getComponent())
            self.tabs_right.add("Config", _textEditors[2].getComponent())
            self._results_splitpane.setRightComponent(self.tabs_right)

            # tabs with request/response viewers
            global _requestViewers, _requestPane
            _requestPane = JTabbedPane()
            _requestViewers.append(Cb.callbacks.createMessageEditor(None, False))
            _requestPane.addTab("Request", _requestViewers[-1].getComponent())
            self._results_splitpane.setLeftComponent(_requestPane)

            # customize our UI components
            Cb.callbacks.customizeUiComponent(self._settings_pane)
            Cb.callbacks.customizeUiComponent(self.tabs_right)
            Cb.callbacks.customizeUiComponent(_requestPane)

            # add the custom tab to Burp's UI
            Cb.callbacks.addSuiteTab(self)

        except RuntimeException as e:
            callbacks.printError(traceback.format_exc())
            e = PyException(e)
            print("10")
            print(str(self))
            print("{}\t{}\n{}\n".format(e.type, e.value, e.traceback))
        Cb.callbacks.registerContextMenuFactory(MenuFactory())
        callbacks.registerExtensionStateListener(self)

        self.start_alive_checker()

        Cb.callbacks.printOutput('%s v%s extension loaded\n' % (
            self.ext_name, self.ext_version))

    def change_parallel_requests(self, event):
        global immediate_data
        try:
            num_parallel = MenuFactory.item_selected(event)
            if num_parallel != immediate_data['settings'][0]:
                self.update_setting(0, num_parallel, "number of parallel requests")
        except Exception as e:
            print(e)

    def change_send_timeout(self, event):
        global immediate_data
        try:
            send_timeout = MenuFactory.item_selected(event)
            if send_timeout != immediate_data['settings'][4]:
                self.update_setting(4, send_timeout, "send timeout")
        except Exception as e:
            print(e)

    def check_allow_redirects(self, event):
        global immediate_data
        is_selected = MenuFactory.button_selected(event)
        if is_selected != immediate_data['settings'][2]:
            self.update_setting(2, is_selected, "allow redirects")

    def check_sync_last_byte(self, event):
        global immediate_data
        is_selected = MenuFactory.button_selected(event)
        if is_selected != immediate_data['settings'][3]:
            self.update_setting(3, is_selected, "allow redirects")

    def resend_batches(self, event):
        global _storedRequests
        if _storedRequests is not None:
            self.sen

    # helper method for two methods above
    def update_setting(self, index, new_value, text):
        global immediate_data
        success = True
        print("> Updating {}..".format(text))
        old_value = immediate_data['settings'][index]
        immediate_data['settings'][index] = new_value
        if MenuFactory.set_immediate_mode_settings({'settings': immediate_data['settings']}):
            print("> Success!")
        else:
            print("> Failed!")
            immediate_data['settings'][index] = old_value
            success = False
        return success

    # for ITab
    def getTabCaption(self):
        return "CompuRacer"

    # for ITab
    def getUiComponent(self):
        return self._main_splitpane

    # def getHttpService(self):
    #     global _storedRequest
    #     return _storedRequest.getHttpService()
    #
    # def getRequest(self):
    #     global _storedRequest
    #     return _storedRequest.getRequest()
    #
    # def getResponse(self):
    #     global _storedRequest
    #     return _storedRequest.getResponse()

    def start_alive_checker(self):
        self.t = threading.Thread(name='Alive checker', target=self.alive_checker)
        self.t.start()

    def closest_match(self, number, list_of_numbers):
        return min(list(zip(list_of_numbers, range(len(list_of_numbers)))),
                   key=lambda item: (abs(item[0] - number), item[1]))

    def alive_checker(self):
        global compuRacer_ip, compuRacer_port, alive_check_path, racer_alive, immediate_mode, compuracer_communication_lock
        unloaded = False
        old_alive = racer_alive
        parallel_req_options = [2, 3, 4, 5, 10, 15, 20, 25, 50, 100]
        send_time_options = [3, 5, 10, 20, 50, 100]
        while not unloaded:
            try:
                with compuracer_communication_lock:
                    response = requests.get("http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, alive_check_path),
                                            timeout=2)
                    racer_alive = response and response.status_code and response.status_code == 200
                    success, mode, settings = MenuFactory.get_immediate_mode_settings()
                    if success:
                        immediate_data['mode'] = mode
                        immediate_data['settings'] = settings

                        # update UI button states
                        immediate_data_ui_elements["parallel_requests"].setSelectedIndex(
                            self.closest_match(immediate_data['settings'][0], parallel_req_options)[1])
                        immediate_data_ui_elements["allow_redirects"].setSelected(bool(immediate_data['settings'][2]))
                        immediate_data_ui_elements["sync_last_byte"].setSelected(bool(immediate_data['settings'][3]))
                        immediate_data_ui_elements["send_timeout"].setSelectedIndex(
                            self.closest_match(immediate_data['settings'][4], send_time_options)[1])

            except Exception as e:
                # it surely did not work
                racer_alive = False
                print(e)
            if racer_alive and not old_alive:
                print("> Racer is now alive!")
                MenuFactory.set_state_of_all_buttons(True)
                old_alive = True
            elif not racer_alive and old_alive:
                print("> Racer became dead!")
                MenuFactory.set_state_of_all_buttons(False)
                old_alive = False
            time.sleep(5)
            if not self.loaded:
                unloaded = True

    def extensionUnloaded(self):
        print("\n> Unloading..")
        self.loaded = False
        self.t.join()
        print("> Done.")
