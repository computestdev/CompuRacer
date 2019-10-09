#!/usr/bin/env python3
"""
The RestServer class is used to receive new requests of interest from extensions.
It is also used to render HTTP responses.
"""

# --- All imports --- #
import datetime
import logging
import os
import queue
import threading
import time
from multiprocessing import Queue

import src.utils as utils
from flask import Flask, jsonify, request, abort, render_template

BATCHES_RENDERED_FILE_DIR = 'rendered_files/'

# apparently, flask uses the folder of the file as the starting folder..
app = Flask(__name__, template_folder="../" + BATCHES_RENDERED_FILE_DIR)
log = logging.getLogger('werkzeug')
log.disabled = True
app.logger.disabled = True

server_queue = Queue()
immediate_data = {'mode': 'off', 'settings': [10, 1, False, False, 20], 'results': None}


class RestServer:

    timeout = 10000
    host = "127.0.0.1"
    port = "8099"

    log_file = "rest_server.log"

    allowed_hosts = [f"{host}:{port}", f"localhost:{port}"]
    allowed_x_req_with = ['Burp extension', 'Browser extension']

    server_process = None
    message_receiver = None

    message_queue = None

    def __init__(self, the_immediate_mode, message_queue, host=None, port=None, log_file=None):
        global immediate_data
        immediate_data['mode'] = the_immediate_mode
        self.message_queue = message_queue
        if host:
            self.host = host
        if port:
            self.port = port
        if log_file:
            self.log_file = log_file
        self.allowed_hosts = [f"{host}:{port}", f"localhost:{port}"]

    def start(self, racer):
        racer.print_formatted("Starting REST server..", utils.QType.INFORMATION, True)

        self.server_process = threading.Thread(name="REST server",
                                               target=self.__run_server,
                                               args=(self.host, self.port, self.log_file))
        self.message_receiver = threading.Thread(name="Core message receiver",
                                                 target=self.__core_message_fetcher,
                                                 args=(self.message_queue,))
        self.server_process.setDaemon(True)
        self.message_receiver.setDaemon(True)
        self.server_process.start()
        self.message_receiver.start()

        # wait for the rest server to startup and produce output
        time.sleep(1)

        racer.print_formatted("Done.", utils.QType.INFORMATION, True)
        global server_queue
        return server_queue

    @staticmethod
    def __core_message_fetcher(message_queue):
        max_diff = 2
        while True:
            try:
                new_item = message_queue.get(timeout=max_diff)
                global immediate_data
                if new_item['type'] == 'mode':
                    immediate_data['mode'] = new_item['content']
                elif new_item['type'] == 'settings':
                    immediate_data['settings'] = new_item['content']
                elif new_item['type'] == 'results':
                    immediate_data['results'] = new_item['content']
            except queue.Empty:
                pass
            except Exception as e:
                print(e)

    @staticmethod
    def __run_server(the_host, the_port, the_log_file):
        print(f"Server at {the_host}:{the_port}")
        # sys.stdout = open(the_log_file, "w")
        # sys.stderr = open(the_log_file, "w")
        try:
            app.run(the_host, the_port, debug=False)
        except OSError as e:
            print(e)
            print("There already seems to be a (Flask) server running on this port!")
            print("Please kill it or change the REST server port, and restart.")


def parse_a_request(a_request):
    if type(a_request) is str:
        a_request = utils.read_json(a_request)
    a_request['timestamp'] = str(datetime.datetime.now())
    return utils.format_json(a_request)


@app.before_request
def check_host_header():
    # Protect against DNS rebinding attacks
    if request.headers.get('host') not in RestServer.allowed_hosts:
        return abort(404)
    if request.method != "GET" and \
            request.headers.get('Content-Type') != 'application/json':
        return abort(404)


@app.route("/", methods=['GET'])
def say_hello():
    return jsonify_sc({'response': 'success'}, 200)


@app.route("/ignore", methods=['GET'])
def get_ignore():
    return jsonify_sc({'urls': '[]'}, 200)


@app.route("/add_request", methods=['POST'])
def add_request():
    if not request.json:
        return jsonify_sc("No JSON embedded!", 400)
    global server_queue, immediate_data
    json_dict = request.json
    server_queue.put({'type': 'request', 'content': parse_a_request(json_dict)})
    immediate_data['results'] = None
    return jsonify_sc("Success", 200)


@app.route("/add_requests", methods=['POST'])
def add_requests():
    if not request.json:
        return jsonify_sc("No JSON embedded!", 400)
    if 'requests' not in request.json:
        return jsonify_sc("No 'requests' key in JSON body!", 400)
    global server_queue, immediate_data
    json_dict = request.json
    for a_request in json_dict['requests']:
        server_queue.put({'type': 'request', 'content': parse_a_request(a_request)})
    immediate_data['results'] = None
    return jsonify_sc("Success", 200)


@app.route("/immediate_data", methods=['GET', 'POST'])
def immediate_config():
    global immediate_data
    if request.method == "GET":
        return jsonify_sc({'mode': immediate_data['mode'], 'settings': immediate_data['settings']}, 200)
    elif request.method == "POST":
        if not request.json:
            return jsonify_sc("No JSON embedded!", 400)
        global server_queue
        if 'mode' in request.json:
            immediate_data['mode'] = request.json['mode']
            server_queue.put({'type': 'mode', 'content': immediate_data['mode']})
        if 'settings' in request.json:
            # check whether all settings elements are of the right type and the duplication amount is not too high
            if not matching_types(request.json['settings'], [int, int, bool, bool, int]) \
                    or request.json['settings'][0] * request.json['settings'][1] > 1000:
                return jsonify_sc("Invalid settings", 400)
            immediate_data['settings'] = request.json['settings']
            server_queue.put({'type': 'settings', 'content': immediate_data['settings']})
        return jsonify_sc("Success", 200)
    return jsonify_sc("Not found", 404)


def matching_types(data, types):
    if types is None or data is None or len(types) != len(data):
        return False
    match = True
    for i, item in enumerate(data):
        if type(item) != types[i]:
            match = False
    return match


@app.route("/immediate_results", methods=['GET'])
def access_mode():
    global immediate_data
    return jsonify_sc({'results': immediate_data['results']}, 200)


@app.route("/responses/<string:filename>", methods=['GET'])
def responses(filename):
    if not filename:
        return jsonify_sc("No JSON embedded!", 400)

    # Joining the base and the requested path
    abs_base = os.path.abspath(BATCHES_RENDERED_FILE_DIR)
    abs_path = os.path.abspath(os.path.join(abs_base, filename))

    # Return 404 if path doesn't exist, checks for path traversal attacks
    if not abs_path.startswith(abs_base) or not os.path.exists(abs_path):
        return abort(404)
    return render_template(filename)


def jsonify_sc(item, statuscode):
    """
    Creates a JSON response with an embedded HTTP status code.

    Returns:
        A JSON response.
    """
    if not item:
        item = {}
    response = jsonify(item)
    response.status_code = statuscode
    return response
