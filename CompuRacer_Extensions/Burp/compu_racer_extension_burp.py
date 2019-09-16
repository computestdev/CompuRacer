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
#import http.client as client
#import urllib
import requests

from javax.swing import JMenu, JMenuItem, JCheckBoxMenuItem, JOptionPane, JLabel, ImageIcon
from burp import IBurpExtender, IHttpRequestResponse, IResponseInfo, IContextMenuFactory, IContextMenuInvocation, \
    IExtensionStateListener
from java.net import URL, URI

# --- Configuration --- #
compuRacer_ip = '127.0.0.1'
compuRacer_port = '8099'
add_request_path = 'add_requests'
alive_check_path = ''

racer_alive = False


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
        self.send_lock = threading.Lock()
        # self.load_icon()

    def createMenuItems(self, invoker):

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
        elif self.send_lock.locked():
            button_text += " (busy)"
        menu_send = JMenuItem(
            button_text, actionPerformed=self.start_request_transmitter)
        # self.set_icon(menu_send)
        menu_send.setEnabled(racer_alive and not self.send_lock.locked())
        return [menu_send]

    def start_request_transmitter(self, event):
        t = threading.Thread(name='Request transmitter', target=self.send_requests_batched_to_racer,
                             args=(self.messages,))
        t.start()

    def send_requests_batched_to_racer(self, requests):
        with self.send_lock:
            for i in range(0, len(requests), 50):
                end = min(i + 50, len(requests))
                self.send_requests_to_racer(requests[i:end])
            print("> Done sending {} request(s) to racer.".format(len(requests)))

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
    def send_requests_to_racer(the_requests):
        print("> Sending {} request(s) to racer..".format(len(the_requests)))
        # header for request to racer
        global compuRacer_ip, compuRacer_port, add_request_path
        request_headers = ["POST /{} HTTP/1.1".format(add_request_path),
                           "Host: {}:{}".format(compuRacer_ip, compuRacer_port),
                           "Accept: */*",
                           "Connection: close",
                           "Content-Type: application/json",
                           # "X-Requested-With: Burp extension"
                           ]  # it auto-generates the content-length header
        request_headers_dict = {header.split(": ")[0]: header.split(": ")[1] for header in request_headers[1:]}
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

        # This approach uses the SOCKS proxy:
        # request = Cb.helpers.buildHttpMessage(request_headers, total_body_bytes)
        print("> Sending requests: \n{}\n".format(request_headers[0]))
        try:
            #response_str = make_request(method="POST",
            #                            url="http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, add_request_path),
            #                            headers=request_headers_dict,
            #                            body=total_body_bytes)
            response_str = requests.post(url="http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, add_request_path),
                                         json=total_body,
                                         timeout=5
                                         ).status_code
            # This approach uses the SOCKS proxy:
            # response = Cb.callbacks.makeHttpRequest(
            #    Cb.helpers.buildHttpService(compuRacer_ip, int(compuRacer_port), False), request)
        except Exception as e:
            print("Oh noo:")
            print(e)
            return
        # This approach uses the SOCKS proxy:
        # response_str = Cb.helpers.bytesToString(response.getResponse())
        print("> Got response: \n{}\n".format(response_str))
        if not 200 <= int(response_str):  # Cb.helpers.analyzeResponse(response.getResponse()).getStatusCode() <= 299:
            print("> Failed to send the_requests: {}".format(total_body))
        else:
            print("> Done sending the_requests to racer!\n")


def make_request(method, url, headers, body, timeout):
    try:
        #conn = client.HTTPConnection(url.split("/")[0], timeout=timeout)
        response = requests.request(method=method,
                                    url="/".join(url.split("/")[1:]),
                                    headers=headers,
                                    body=body)
        #response = conn.getresponse()
    except Exception as e:
        print(e)
        return 400
    else:
        return response.status_code


class BurpExtender(IBurpExtender, IExtensionStateListener):
    ext_name = "RaceConditionTester"
    ext_version = '0.1'
    loaded = True
    t = None

    def registerExtenderCallbacks(self, callbacks):
        Cb(callbacks)
        Cb.callbacks.setExtensionName(self.ext_name)

        Cb.callbacks.registerContextMenuFactory(MenuFactory())
        callbacks.registerExtensionStateListener(self)

        self.start_alive_checker()

        Cb.callbacks.printOutput('%s v%s extension loaded' % (
            self.ext_name, self.ext_version))

    def start_alive_checker(self):
        self.t = threading.Thread(name='Alive checker', target=self.alive_checker)
        self.t.start()

    def alive_checker(self):
        global compuRacer_ip, compuRacer_port, alive_check_path, racer_alive
        request = Cb.helpers.buildHttpRequest(
            URL("http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, alive_check_path)))
        service = Cb.helpers.buildHttpService(compuRacer_ip, int(compuRacer_port), False)
        unloaded = False
        old_alive = racer_alive
        while not unloaded:
            try:
                # This approach uses the SOCKS proxy:
                # response = Cb.callbacks.makeHttpRequest(service, request)
                response = requests.get("http://{}:{}/{}".format(compuRacer_ip, compuRacer_port, alive_check_path),
                                        timeout=2)
                if response and response.status_code:  # response.getResponse():
                    racer_alive = response.status_code == 200  # Cb.helpers.analyzeResponse(response.getResponse()).getStatusCode() == 200
                else:
                    # not response --> failed
                    racer_alive = False
            except Exception as e:
                # it surely did not work
                racer_alive = False
            if racer_alive and not old_alive:
                print("Racer is now alive!")
                old_alive = True
            elif not racer_alive and old_alive:
                print("Racer became dead!")
                old_alive = False
            time.sleep(5)
            if not self.loaded:
                unloaded = True

    def extensionUnloaded(self):
        print("Unloading..")
        self.loaded = False
        self.t.join()
        print("Done.")
