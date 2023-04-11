#!/usr/bin/env python3
"""
The CompuRacer class is the heart of the application that manages requests, batches, storage and sending/receiving.
"""

# --- All imports --- #
import copy
import os
import queue
import signal
import threading
import time
import urllib
from enum import Enum
from functools import partial
from multiprocessing import Queue

try:
    from tkinter import *
    from tkinter import filedialog
except ModuleNotFoundError as e:
    # this only happens if the system has no display
    # and then we will not use this lib anyway
    # look at the check in ../main.py
    pass

from tqdm import tqdm
from tabulate import tabulate

import src.batch_sender_async as sender

from src import utils
from .batch import Batch
from .command_processor import CommandProcessor
from .rest_server import RestServer

root = None
try:
    root = Tk()
    root.withdraw()
except Exception as e:
    # this only happens if the system has no display
    # and then we will not use this anyway
    # look at the check in ../main.py
    pass

# --- Authorship information --- #
__author__ = "R.J. van Emous @ Computest"
__license__ = "MIT License"
__version__ = "2019"
__email__ = "rvanemous@computest.nl"
__status__ = "Prototype"


class SortOrder(Enum):
    """
    An enum to hold three types of sort-orders
    """
    INDEX = partial(lambda x: int(x['id']))
    TIME = partial(lambda x: x['timestamp'])
    METHOD = partial(lambda x: x['method'])
    URL = partial(lambda x: x['url'])

    def __str__(self):
        return f"SortOrder.{self.name}"


class CompuRacer:
    """
    The Core of the race condition testing application
    """

    # -------------- String constants -------------- #
    CLIENT_VERSION = "1.2"
    CLIENT_FILE_lOC = "state/"
    CLIENT_CONFIG = CLIENT_FILE_lOC + "state.json"
    CLIENT_BACKUP = CLIENT_FILE_lOC + "state.json.backup"
    CLIENT_BATCHES_LOC = CLIENT_FILE_lOC + "batches/"
    BATCHES_RENDERED_FILE_DIR = 'rendered_files/'
    BATCHES_EXP_FILE_DIR = 'exp_files/'

    CLI_PROMPT = "racer"

    # -------------- Globals -------------- #
    # general
    shutdown_client = False
    is_shutdown = False
    requests_list_lock = threading.RLock()

    # CompuRacer specific
    command_processor = None
    rest_interface_thread = None

    proxy = None
    server_queue = None
    server_send_queue = None
    dialog_queue = None

    state = None
    immediate_batch_name = "Imm"
    progress_bar_width = 100

    def __init__(self, port, proxy, queue, use_only_cli):
        """
        Creates a new CompuRacer instance
        :param queue: the queue to be used when we want to display a filepicker dialog to the user
        """
        self.proxy = proxy

        # if the queue is None, we cannot and will not show dialogs
        self.dialog_queue = queue

        self.use_only_cli = use_only_cli

        # add shutdown hooks
        signal.signal(signal.SIGINT, self.force_shutdown)
        signal.signal(signal.SIGTERM, self.force_shutdown)

        # initialize command processor (just for printing)
        self.command_processor = CommandProcessor(self.state)
        self.command_processor.set_cli_prompt(self.CLI_PROMPT)

        # load main client settings and requests
        if os.path.exists(self.CLIENT_FILE_lOC) and \
                (os.path.exists(self.CLIENT_CONFIG) or os.path.exists(self.CLIENT_BACKUP)):
            utils.clear_output()
            self.state = self.get_default_state()
            self.command_processor.set_config(self.state)
            self.state = self.__load_json(self.CLIENT_CONFIG, self.CLIENT_BACKUP, "Loading current configuration..")
            # compatibility with v1.0 and v1.1 state file
            self.patch_state_to_v12()

        if self.state is None:
            # set temp config in command processor
            self.state = self.get_default_state()
            self.command_processor.set_config(self.state)
            self.start_new_state_setup()

        # set config in command processor
        self.command_processor.set_config(self.state)

        # load all batches into state
        if not os.path.exists(self.CLIENT_BATCHES_LOC):
            os.mkdir(self.CLIENT_BATCHES_LOC)
        else:
            time.sleep(0.25)
            for file in tqdm(os.listdir(self.CLIENT_BATCHES_LOC), desc="Loading batches",
                             ncols=self.progress_bar_width):
                self.imp_batch_without_requests_by_name(self, self.CLIENT_BATCHES_LOC + file)
            time.sleep(0.25)
        self.print_formatted("Done.", utils.QType.INFORMATION)

        # initialize command processor (fully)
        self.command_processor.set_welcome_function(self.display_welcome, self)
        self.add_all_commands()

        # initialize the REST server
        self.server_send_queue = Queue()
        self.rest_server = RestServer(self.state['immediate_mode'], self.server_send_queue, port=port)

    def patch_state_to_v12(self):
        if 'immediate_settings' in self.state and len(self.state['immediate_settings']) != 5:
            self.state['immediate_settings'] = [15, 1, False, True, 20]
        elif 'immediate_dup_par_sec' in self.state:
            if len(self.state['immediate_dup_par_sec']) == 2:
                self.state['immediate_settings'] = [self.state['immediate_dup_par_sec'][0],
                                                    self.state['immediate_dup_par_sec'][1], False, True, 20]
            else:
                self.state['immediate_settings'] = [15, 1, False, True, 20]
            del self.state['immediate_dup_par_sec']

    def __str__(self):
        """
        A string representation of the CompuRacer
        :return: the string
        """
        return f"CompuRacer = [state = {utils.format_json(self.jsonify_batches(self.state))}]"

    def is_changed(self):
        """
        Checks whether the current CompuRacer state is changed
        :return: True if changed
        """
        if not self.state:
            return False
        if 'changed' in self.state and self.state['changed']:
            return True
        if self.command_processor.is_changed():
            return True
        return False

    def set_unchanged(self):
        """
        Sets the current CompuRacer state to unchanged
        :return: True if changed
        """
        if not self.state:
            return
        if 'changed' in self.state:
            self.state['changed'] = False
        if self.command_processor.is_changed():
            self.command_processor.set_changed(False)

    def start(self, use_only_cli):
        """
        Starts the CompuRacer
        """
        # indicate whether we use an upstream SOCKS proxy
        if self.proxy:
            self.print_formatted(f"Using upstream SOCKS proxy: '{self.proxy}'", utils.QType.INFORMATION)

        # start the REST server
        self.server_queue = self.rest_server.start(self)

        # start rest server interfacer (also takes care of immediate thread creation)
        self.print_formatted("Starting REST server interface thread..", utils.QType.INFORMATION)
        self.rest_interface_thread = threading.Thread(name='REST server interfacer',
                                                      target=self.run_rest_server_interfacer,
                                                      args=(self, self.server_queue))
        self.rest_interface_thread.start()
        self.print_formatted("Done..", utils.QType.INFORMATION)

        # start client interpreter
        self.print_formatted("Starting command processor..", utils.QType.INFORMATION)
        time.sleep(0.25)
        utils.clear_output()
        self.command_processor.start(use_only_cli, self, self.state)

    def comm_general_save(self, do_print=True):
        """
        Stores the current CompuRacer state when changed.
        The main settings and requests will be saved in one file and all batches will be saved in one file each
        :param do_print: if True, prints the progress
        """
        saved_anything = False
        # store general state of racer and the requests
        if self.is_changed():
            saved_anything = True
            store_string = None
            if do_print:
                store_string = "Storing current state.."
            state_to_save = copy.deepcopy(self.state)
            state_to_save['batches'] = {}
            self.set_unchanged()
            self.__store_json(self.CLIENT_CONFIG, state_to_save, self.CLIENT_BACKUP, store_string)
            time.sleep(0.25)
        # store individual batches
        if not os.path.exists(self.CLIENT_BATCHES_LOC):
            os.mkdir(self.CLIENT_BATCHES_LOC)
        if 'batches' in self.state:
            for batch_name in tqdm(list(self.state['batches'].keys()), desc="Storing batches",
                                   ncols=self.progress_bar_width):
                if self.state['batches'][batch_name].changed:
                    saved_anything = True
                    self.state['batches'][batch_name].changed = False
                    self.exp_batch_without_requests_by_name(self, self.CLIENT_BATCHES_LOC, batch_name)
            time.sleep(0.25)
        # print whether it is changed
        if not saved_anything and do_print:
            self.print_formatted("State not changed.", string_type=utils.QType.INFORMATION)
        elif do_print:
            self.print_formatted("Done.", string_type=utils.QType.INFORMATION)

    def comm_general_shutdown(self, args=None):
        """
        Shuts down the CompuRacer normally
        :param args: required for shutdown hook, not used
        """
        print()
        self.print_formatted("Shutting down client..", string_type=utils.QType.INFORMATION)
        self.shutdown_client = True
        if self.rest_interface_thread:
            self.print_formatted("Stopping rest interface thread..", utils.QType.INFORMATION)
            self.rest_interface_thread.join()
            self.print_formatted("Done.", utils.QType.INFORMATION)
        if self.command_processor:
            self.command_processor.shutdown()
        self.comm_general_save(True)
        self.print_formatted("Done.", string_type=utils.QType.INFORMATION)
        self.is_shutdown = True

    # only to be called by os signal exit (like after CTRL-C)
    def force_shutdown(self, arg=None, arg2=None):
        """
        Shuts down the CompuRacer immediatelly
        :param arg: required for shutdown hook, not used
        :param arg2: ditto
        """
        # shutdown initiated, stop rest server, command processor, save state and exit
        print()
        self.shutdown_client = True
        if self.rest_interface_thread:
            self.rest_interface_thread.join()
        if self.command_processor:
            self.command_processor.shutdown(False)
        self.comm_general_save(False)
        self.is_shutdown = True

    def start_new_state_setup(self):
        """
        Client setup wizard used on first install
        """
        utils.clear_output()
        self.print_formatted("# ------- CompuRacer v{} -- setup state ------- #\n".format(self.CLIENT_VERSION),
                             utils.QType.GREEN)
        if not self.command_processor.accept_yes_no("No client state file detected in '{}'. "
                                                    "Do you want to setup a new client?".format(self.CLIENT_FILE_lOC),
                                                    string_type=utils.QType.WARNING):
            self.print_formatted("Please create or import a new state file into: '{}'. \n"
                                 "\tExiting..".format(self.CLIENT_CONFIG), utils.QType.INFORMATION)
            exit(0)
        else:
            self.print_formatted("Creating a new client setup..", utils.QType.INFORMATION)
            if not os.path.exists(self.CLIENT_FILE_lOC):
                os.mkdir(self.CLIENT_FILE_lOC)

            utils.print_colored("This is some RED text.", utils.Color.RED)
            colored_output = self.command_processor.accept_yes_no("Is the line above colored red?")
            self.print_formatted(f"Colored output enabled is set to: '{colored_output}'", utils.QType.INFORMATION)

            self.state = self.create_new_state(colored_output)

            self.__store_json(self.CLIENT_CONFIG, self.objectify_batches(self, copy.deepcopy(self.state)),
                              "Storing current configuration..")

            # create batches folder
            if not os.path.exists(self.CLIENT_BATCHES_LOC):
                os.mkdir(self.CLIENT_BATCHES_LOC)

        self.print_formatted("# ------- state setup finished ------- #\n".format(self.CLIENT_VERSION),
                             utils.QType.GREEN)
        time.sleep(0.5)

    @staticmethod
    def get_default_state():
        """
        Gets the default state dictionary
        :return: the dict
        """
        return {
            "display_welcome": True,
            "colored_output": False,
            "current_batch": None,
            "project_name": "",
            "batches": {},
            "requests": {},
            "concepts": None,
            "immediate_mode": "off",
            "immediate_print": True,
            "immediate_settings": [1, 1, False, False, 20],
            "cp_history": [],
            "changed": True
        }

    @staticmethod
    def create_new_state(colored_output, the_requests=None, project_name="", batches=None, concepts=None,
                         current_batch=None):
        """
        Gets the a new state dictionary
        :param colored_output: if True, to colors the output
        :param the_requests: the dict of requests
        :param project_name: the default project name prefix
        :param batches: the dict of batches
        :param concepts: not used
        :param current_batch: the current batch name
        :return: the dict
        """
        if not the_requests:
            the_requests = {}
        if not batches:
            batches = {}
        return {
            "display_welcome": True,
            "colored_output": colored_output,
            "current_batch": current_batch,
            "project_name": project_name,
            "batches": batches,
            "requests": the_requests,
            "concepts": concepts,
            "immediate_mode": "off",
            "immediate_print": True,
            "immediate_settings": [1, 1, False, False, 20],
            "cp_history": [],
            "changed": True
        }

    @staticmethod
    def display_welcome(self):
        """
        Displays the welcome string when the application is started
        :param self: reference to the CompuRacer
        """
        print()
        self.print_formatted("CompuRacer v{} started".format(self.CLIENT_VERSION), utils.QType.GREEN)

    @staticmethod
    def run_rest_server_interfacer(racer, rest_server_queue):
        """
        The method that is used in communicating with the REST server
        :param racer: a reference to the CompuRacer
        :param rest_server_queue: the queue where the REST server sends received requests through
        """
        # listen for requests to the REST server and send them to the racer
        # it also triggers immediate batch sending if necessary
        bunch_start_time = time.time()
        immediate_batch_unsent = False
        max_diff = 2
        while not racer.shutdown_client:
            try:
                new_item = rest_server_queue.get(timeout=max_diff)
                if new_item['type'] == 'request':
                    new_request = new_item['content']
                    bunch_start_time = time.time()  # restart wait time
                    immediate_batch_unsent = True
                    racer.add_request_from_json(new_request)
                elif new_item['type'] == 'mode':
                    racer.comm_mode_change(racer, new_item['content'], False)
                elif new_item['type'] == 'settings':
                    racer.comm_mode_set_settings(racer, new_item['content'][0], new_item['content'][1],
                                                 new_item['content'][2], new_item['content'][3],
                                                 new_item['content'][4], False)
            except queue.Empty:
                pass
            except Exception as e:
                print(e)
            if immediate_batch_unsent and time.time() - bunch_start_time > max_diff:
                racer.trigger_immediate()
                immediate_batch_unsent = False

    # -------------- Command loading for processor  -------------- #
    def add_all_commands(self):
        """
        Adds all command types to the command processor
        """
        self.add_commands_general()
        self.add_commands_mode()
        self.add_commands_requests()
        self.add_commands_batches()
        self.add_commands_current_batch()

    def add_commands_general(self):
        """
        Adds all commands that are related to the general workings of the application
        """
        self.command_processor.add_command(["wel"], self.comm_general_change_welcome,
                                           "Enables or disables the welcome screen on startup.\n"
                                           "Note: Enables when no arguments are provided", self,
                                           arg_spec_opt=[("Enable welcome", bool, True)]
                                           )
        self.command_processor.add_command(["col"], self.comm_general_change_color_output,
                                           "Enables or disables the colored output (disable this if you see odd characters).\n"
                                           "Note: Enables when no arguments are provided", self,
                                           arg_spec_opt=[("Enable color", bool, True)]
                                           )
        self.command_processor.add_command(["s", "save"], self.comm_general_save, "Saves the current state.", self)
        self.command_processor.add_command(["q", "quit"], self.comm_general_shutdown,
                                           "Saves the current state and shuts down the racer.", self)

    def add_commands_mode(self):
        """
        Adds all commands that are related to the immediate mode of the application
        """
        self.command_processor.add_command(["mode"], self.comm_mode_change,
                                           "Sets the mode to add incoming requests to: the current batch, a new batch (and send it after 2 secs) or not at all.",
                                           self,
                                           arg_spec_opt=[("Change immediate mode: 'off', 'curr' or 'on'", str,
                                                          "* on: new batch and send mode *")]
                                           )
        self.command_processor.add_command(["set mode"], self.comm_mode_set_settings,
                                           "Updates mode settings for parallel and sequential duplication.", self,
                                           arg_spec_opt=[("Parallel duplicates > 0", int, 1),
                                                         ("Sequential duplicates > 0", int, 1),
                                                         ("Allow redirects", bool, False),
                                                         ("Sync last byte", bool, True),
                                                         ("Send timeout >= 1", int, True)]
                                           )
        self.command_processor.add_command(["print mode"], self.comm_mode_change_printing,
                                           "Enables or disables immediate-mode results printing.\n"
                                           "Note: Enables when no arguments are provided", self,
                                           arg_spec_opt=[("Enable immediate mode printing", bool, True)]
                                           )

    def add_commands_requests(self):
        """
        Adds all commands that are related to viewing, comparing and removing requests
        """
        self.command_processor.add_command(["reqs"], self.comm_requests_get,
                                           "Gets a sorted table of basic request info.", self,
                                           arg_spec_opt=[("First request ID", str, "* all requests *"),
                                                         ("Last request ID", str, "* only first request *"),
                                                         (
                                                             f"Sort order {[str(order) for order in SortOrder]}",
                                                             SortOrder,
                                                             "SortOrder.INDEX"),
                                                         ("Sort in ascending order", bool, True)]
                                           )
        self.command_processor.add_command(["req"], self.comm_requests_get_one,
                                           "Gets a specific request by ID. Getting newest request when no argments are provided.",
                                           self,
                                           arg_spec_opt=[("Request ID", str, "* the most recently added request *")]
                                           )
        self.command_processor.add_command(["comp reqs"], self.comm_requests_comp,
                                           "Compares the contents of two requests.", self,
                                           arg_spec=[("First request ID", str), ("Second request ID", str)],
                                           arg_spec_opt=[("Also print matches of comparison", bool, False)]
                                           )
        self.command_processor.add_command(["rm reqs"], self.comm_requests_remove,
                                           "Deletes the request(s) by ID from the general list.", self,
                                           arg_spec_opt=[("First request ID", str, "* all requests *"),
                                                         ("Last request ID", str, "* only first request *")]
                                           )
        self.command_processor.add_command(["lower reqs"], self.comm_requests_lower_ids,
                                           "Re-creates the ids of all requests so that it is a sequencial list starting at 1.\n"
                                           "Note: Also updates the ids in all batches. Could take some time", self)

    def add_commands_batches(self):
        """
        Adds all commands that are related to creating, viewing, updating, copying, comparing importing/exporting
        and removing of batches
        """
        self.command_processor.add_command(["go"], self.comm_batches_send,
                                           "Sends a batch by index according to its configuration.", self,
                                           arg_spec_opt=[("Index of the batch", int, "* the current batch *"),
                                                         ("Print result summary", bool, True)]
                                           )
        self.command_processor.add_command(["add bs", "add batch"], self.comm_batches_create_new,
                                           "Creates a new batch by name and sets it as current batch (must be unique)",
                                           self,
                                           arg_spec=[("Name of the batch", str)],
                                           arg_spec_opt=[(
                                               "If true, set new batch as current batch, else it keeps the current value",
                                               bool, True)]
                                           )
        self.command_processor.add_command(["get proj", "get project"], self.comm_batches_get_project,
                                           "Gets the project name prefix with which all new batch names will begin.",
                                           self
                                           )
        self.command_processor.add_command(["set proj", "set project"], self.comm_batches_set_project,
                                           "Sets the project name prefix with which all new batch names will now begin: 'project_name'_<batch_name>",
                                           self,
                                           arg_spec_opt=[("Name of the project", str, "* Empty string *")]
                                           )
        self.command_processor.add_command(["bss", "ls", "dir", "batches"], self.comm_batches_info,
                                           "Gets a table of info of all batches", self)
        self.command_processor.add_command(["set curr"], self.comm_batches_set_current,
                                           "Sets the current batch by index", self,
                                           arg_spec=[("Index of the batch", int)]
                                           )
        self.command_processor.add_command(["get cont"], self.comm_batches_get_contents,
                                           "Get batch by index and print batch contents summary", self,
                                           arg_spec=[("Index of the batch", int)],
                                           arg_spec_opt=[(
                                               "If true, get full batch contents (including ASCII timing-representation) else get summary",
                                               bool, False)]
                                           )
        self.command_processor.add_command(["get res"], self.comm_batches_get_results,
                                           "Get batch by index and print last results summary", self,
                                           arg_spec=[("Index of the batch", int)],
                                           arg_spec_opt=[("If true, get aggregate tables", bool, False),
                                                         ("If true, get groups contents", bool, False)]
                                           )
        self.command_processor.add_command(["comp res"], self.comm_batches_comp_resp_groups,
                                           "Compares two response groups within the request result in a batch", self,
                                           arg_spec=[("Index of the batch", int),
                                                     ("First group number (0 <= number < num_groups)", int),
                                                     ("Second group number", int)],
                                           arg_spec_opt=[
                                               ("Request ID", str, "* last request in batch (alphabetical) *")]
                                           )

        self.command_processor.add_command(["rn bs", "rn batch"], self.comm_batches_rename,
                                           "Rename the batch to the new name. If old name not provided, it will rename the current batch",
                                           self,
                                           arg_spec=[("New name of the batch", str)],
                                           arg_spec_opt=[("Index of the batch", int, "* the current batch *")]
                                           )
        self.command_processor.add_command(["cp bs", "cp batch"], self.comm_batches_copy,
                                           "Copy the batch and give it a new name. If name not provided, it will copy the current batch",
                                           self,
                                           arg_spec=[("Name of the new batch", str)],
                                           arg_spec_opt=[("Index of the batch", int, "* the current batch *")]
                                           )
        self.command_processor.add_command(["rm bss", "rm batches"], self.comm_batches_remove,
                                           "Remove the batch(es) including the used requests and results", self,
                                           arg_spec_opt=[("Index of the first batch", int, "* the current batch *"),
                                                         ("Index of the last batch", int, "* only the first batch *")]
                                           )

        self.command_processor.add_command(["exp bss", "exp batches"], self.comm_batches_export,
                                           "Export the batch(es) including the used requests and results", self,
                                           arg_spec_opt=[("Index of the first batch", int, "* the current batch *"),
                                                         ("Index of the last batch", int, "* only the first batch *")]
                                           )
        self.command_processor.add_command(["imp bss ls", "imp batches list"], self.comm_batches_import_list,
                                           "Lists the batches (with indices) that can be imported.", self
                                           )
        self.command_processor.add_command(["imp bss", "imp batches"], self.comm_batches_import,
                                           "Import a previously exported batch by number or using a file picker (if no arguments).\n"
                                           "If the system does not support showing a dialog, it will show an error message"
                                           "Duplicates will be renamed.", self,
                                           arg_spec_opt=[
                                               ("Index of the first batch", int, "* opens file picker dialog *"),
                                               ("Index of the last batch", int, "* only the first batch *")]
                                           ),
        self.command_processor.add_command(["reg bss", "regroup batches"], self.comm_batches_regroup,
                                           "For all batches, force regroup the results. Useful when grouping code is updated.\n"
                                           "Note: Takes some time.", self)

    def add_commands_current_batch(self):
        """
        Adds all commands that are related to changing, viewing, comparing and removing the current batch
        """
        self.command_processor.add_command(["red", "redir"], self.comm_curr_change_redirects,
                                           "Enables or disables whether the current batch allows redirects.\n"
                                           "Enables when no arguments are provided", self,
                                           arg_spec_opt=[("Enable redirects", bool, True)]
                                           )
        self.command_processor.add_command(["sync"], self.comm_curr_change_sync,
                                           "Enables or disables whether the current batch syncs the last byte of the request content (if any).\n"
                                           "Enables when no arguments are provided", self,
                                           arg_spec_opt=[("Enable last byte sync", bool, True)]
                                           )
        self.command_processor.add_command(["timeout"], self.comm_curr_change_timeout,
                                           "Sets the current batch send timout (default 20 seconds).", self,
                                           arg_spec_opt=[("send timeout >= 1", int, 20)]
                                           )
        self.command_processor.add_command(["add"], self.comm_curr_add,
                                           "Adds a request to the current batch by ID, wait_time, parallel and sequential duplicates",
                                           self,
                                           arg_spec=[("Request ID", str)],
                                           arg_spec_opt=[("Wait time >= 0", int, 0),
                                                         ("Parallel duplicates > 0", int, 1),
                                                         ("Sequential duplicates > 0", int, 1)]
                                           )
        self.command_processor.add_command(["upd", "update"], self.comm_curr_update,
                                           "Updates the wait_time, parallel and/or sequential duplication of the request in the current batch.",
                                           self,
                                           arg_spec=[("Request ID", str), ("Wait time >= 0", int, 0)],
                                           arg_spec_opt=[("Parallel duplicates > 0", int, 1),
                                                         ("Sequential duplicates > 0", int, 1)]
                                           )

        self.command_processor.add_command(["get ign", "get ignore"], self.comm_curr_get_ignore,
                                           "Gets the ignore fields in grouping of the current batch", self)
        self.command_processor.add_command(["add ign", "add ignore"], self.comm_curr_add_ignore,
                                           "Adds a field to the ignore fields in grouping of the current batch", self,
                                           arg_spec=[("Field name (case sensitive)", str)]
                                           )
        self.command_processor.add_command(["res ign", "reset ignore"], self.comm_curr_reset_ignore,
                                           "Reset the ignore fields in grouping of the current batch to the default values",
                                           self)

        self.command_processor.add_command(["cont"], self.comm_curr_get_contents,
                                           "Print current batch contents summary", self,
                                           arg_spec_opt=[(
                                               "If true, get full batch contents (including ASCII timing-representation) else get summary",
                                               bool, False)]
                                           )
        self.command_processor.add_command(["res"], self.comm_curr_get_results,
                                           "Print current batch last results", self,
                                           arg_spec_opt=[("If true, get aggregate tables", bool, False),
                                                         ("If true, get groups contents", bool, False)]
                                           )
        self.command_processor.add_command(["comp"], self.comm_curr_compare_groups,
                                           "Compares two response groups within the request result in current batch",
                                           self,
                                           arg_spec=[("First group number (0 <= number < num_groups)", int),
                                                     ("Second group number", int)],
                                           arg_spec_opt=[
                                               ("Request ID", str, "* last request in batch (alphabetical) *")]
                                           )
        self.command_processor.add_command(["rm"], self.comm_curr_remove,
                                           "Removes (a) request(s) from the current batch by ID and wait_time", self,
                                           arg_spec_opt=[("Request ID", str, "* all IDs *"),
                                                         ("Wait time >= 0", int, "* all wait times *")]
                                           )

    # ----------------------------------------------------------------------------------------------------- #
    # ------------------------------------- General command functions ------------------------------------- #
    # ----------------------------------------------------------------------------------------------------- #

    @staticmethod
    def comm_general_change_welcome(self, do_display=True):
        """
        Changes whether the welcome string is displayed when the application is started
        :param self: reference to the CompuRacer
        :param do_display: if True, it displays the welcome string
        """
        self.__change_state('display_welcome', do_display)
        self.print_formatted("Welcome display -enabled is set to: '{}'".format(self.state["display_welcome"]),
                             utils.QType.INFORMATION)

    @staticmethod
    def comm_general_change_color_output(self, do_colored_output=True):
        """
        Changes whether colored output is used in the command line interface
        :param self: reference to the CompuRacer
        :param do_colored_output: if True, it uses the colored output
        """
        self.__change_state('colored_output', do_colored_output)
        self.print_formatted("Colored output -enabled is set to: '{}'".format(self.state["colored_output"]),
                             utils.QType.INFORMATION)

    @staticmethod
    def comm_mode_change(self, immediate_mode='off', from_ui=True):
        """
        Changes the mode of the CompuRacer when receiving a new request via the REST server
        :param self: reference to the CompuRacer
        :param immediate_mode: If 'on', it creates a new batch with this request and sends it. If 'curr', it adds the request to the current batch If 'off', it does nothing
        :param from_ui: If the request is from the REST server, do not add this update to the REST server queue.
        """
        immediate_mode = immediate_mode.lower()
        if immediate_mode not in ['on', 'off', 'curr']:
            # invalid mode selected
            self.print_formatted(f"Invalid immediate mode selected: '{immediate_mode}'!"
                                 f"\n\tValue must be 'on', 'off' or 'curr'.",
                                 utils.QType.ERROR, not from_ui)
            return -1
        if self.state['immediate_mode'] == immediate_mode:
            # nothing changed
            self.print_formatted(f"Immediate-mode not changed, it is still: '{immediate_mode}'",
                                 utils.QType.WARNING, not from_ui)
            return
        # warn user if an immediate batch is still being created (and not yet send)
        if self.state['immediate_mode'] == 'on' and \
                self.immediate_batch_name in self.state['batches'] and \
                not self.state['batches'][self.immediate_batch_name].has_results():
            if from_ui:
                if not self.command_processor.accept_yes_no(
                        "Are you sure you want to change the immediate mode while the immediate batch is not yet sent?",
                        utils.QType.WARNING):
                    self.print_formatted("Immediate-mode change is cancelled.", utils.QType.INFORMATION, not from_ui)
                    return
            else:
                self.print_formatted("Immediate-mode change is cancelled.", utils.QType.INFORMATION, not from_ui)
                return

        self.__change_state('immediate_mode', immediate_mode)
        if from_ui:
            self.server_send_queue.put({'type': 'mode', 'content': immediate_mode})
        self.print_formatted(f"Immediate-mode is set to: '{self.state['immediate_mode']}'",
                             utils.QType.INFORMATION, not from_ui)

    @staticmethod
    def comm_mode_set_settings(self, parallel_dup=1, sequential_dup=1, allow_redirects=False, sync_last_byte=True,
                               send_timeout=20, from_ui=True):
        """
        When the mode is 'on' or 'curr', it will add requests with these settings to a batch
        :param self: reference to the CompuRacer
        :param parallel_dup: the parallel duplication amount
        :param sequential_dup: the sequential duplication amount
        :return: 0 on success and -1 on error
        """
        if parallel_dup <= 0:
            self.print_formatted(f"Immediate-mode parallel_dup must be positive, but is: {parallel_dup}",
                                 utils.QType.ERROR)
            return -1
        if sequential_dup <= 0:
            self.print_formatted(f"Immediate-mode sequential_dup must be positive, but is: {sequential_dup}",
                                 utils.QType.ERROR)
            return -1
        if send_timeout < 1:
            self.print_formatted(f"Immediate-mode send_timeout must be >= 1, but is: {send_timeout}", utils.QType.ERROR)
            return -1
        self.__change_state('immediate_settings',
                            [parallel_dup, sequential_dup, allow_redirects, sync_last_byte, send_timeout])
        if from_ui:
            self.server_send_queue.put({'type': 'settings',
                                        'content': [parallel_dup, sequential_dup, allow_redirects, sync_last_byte,
                                                    send_timeout]})
        self.print_formatted(f"Immediate-mode settings are set to: '{self.state['immediate_settings']}'",
                             utils.QType.INFORMATION, not from_ui)

    @staticmethod
    def comm_mode_change_printing(self, immediate_print=True):
        """
        Changes whether sending in mode 'on' prints the results on the command line
        :param self: reference to the CompuRacer
        :param immediate_print: if True, it will print
        """
        self.__change_state('immediate_print', immediate_print)
        self.print_formatted("Immediate-mode result printing is set to: '{}'".format(self.state["immediate_print"]),
                             utils.QType.INFORMATION)

    # ----------------------------------------------------------------------------------------------------- #
    # ------------------------------------- Request command functions ------------------------------------- #
    # ----------------------------------------------------------------------------------------------------- #

    @staticmethod  # internal usage
    def comm_requests_get_one(self, request_id=None):
        with self.requests_list_lock:
            if not request_id:
                if not self.state['requests']:
                    self.print_formatted(f"Cannot get newest request: The total request list is empty!",
                                         utils.QType.ERROR)
                    return -1
                request_id = str(sorted([int(key) for key in self.state['requests'].keys()])[-1])
            if request_id not in self.state['requests']:
                self.print_formatted(
                    f"Cannot get request: The request with id '{request_id}' is not in the total request list!",
                    utils.QType.ERROR)
                return -1
            the_request = copy.deepcopy(self.state['requests'][request_id])
            # format the body if it is form key-value data
            try:
                the_request = self.format_request_form_body(the_request)
            except ValueError as e:
                self.print_formatted(f"Request body is invalid!\n\t{e}", utils.QType.WARNING)
            self.print_formatted(f"Request '{request_id}':", utils.QType.INFORMATION)
            self.print_formatted(utils.tabbed_pprint_request(the_request, 1), utils.QType.NONE)

    @staticmethod  # internal usage
    def comm_requests_get(self, request_id_first=None, request_id_last=None, sort_order=SortOrder.INDEX.value,
                          sort_ascending=True):
        with self.requests_list_lock:
            reqs_used = {}
            message = ""
            if request_id_first is None:
                # return all requests
                reqs_used = self.state['requests']
                message = "All stored requests:"
            elif request_id_last is None:
                # return only one request
                if request_id_first not in self.state['requests']:
                    self.print_formatted(
                        f"Cannot get request: The request with id '{request_id_first}' is not in the total request list!",
                        utils.QType.ERROR)
                    return -1
                reqs_used[request_id_first] = self.state['requests'][request_id_first]
                message = "The matching request:"
            else:
                # return a range of requests (missing items are skipped)
                for i, request_id in enumerate(self.state['requests'].keys()):
                    if request_id_first <= request_id <= request_id_last:
                        reqs_used[request_id] = self.state['requests'][request_id]
                message = "The matching request(s):"

            req_list = utils.sort_requests(reqs_used, sort_order, sort_ascending)
            self.print_formatted(message, utils.QType.INFORMATION)
            self.print_formatted(f"{utils.print_request_table(req_list)}\nTotal number: {len(reqs_used.keys())}",
                                 utils.QType.NONE)

    @staticmethod  # internal usage
    def comm_requests_comp(self, request_id_1, request_id_2, print_matches=False):
        if request_id_1 not in self.state['requests']:
            self.print_formatted(
                f"Cannot compare first request: The request with id '{request_id_1}' is not in the total request list!",
                utils.QType.ERROR)
            return -1
        if request_id_2 not in self.state['requests']:
            self.print_formatted(
                f"Cannot compare second request: The request with id '{request_id_2}' is not in the total request list!",
                utils.QType.ERROR)
            return -1

        requests = copy.deepcopy([self.state['requests'][request_id_1], self.state['requests'][request_id_2]])
        # format the body if it is form key-value data
        try:
            requests = [self.format_request_form_body(requests[0]),
                        self.format_request_form_body(requests[1])]
        except ValueError as e:
            self.print_formatted(f"Either of the request bodies is invalid!\n\t{e}", utils.QType.WARNING)
            return

        comp = utils.compare_requests(requests[0], requests[1], None, False)
        if not print_matches:
            comp.pop('match', None)
        comp = utils.perform_string_compare_on_results(comp, 25)

        self.print_formatted(
            f"Comparison of requests with id '{request_id_1}' and '{request_id_2}':",
            utils.QType.INFORMATION)
        self.colorprint_comp_results(self, comp)

    @staticmethod  # do not add requests to this list in any other way
    def comm_requests_remove(self, request_id_first=None, request_id_last=None, ask_confirmation=True):
        with self.requests_list_lock:
            if not self.state['requests']:
                self.print_formatted(f"There is no request to delete: The total request list is empty!",
                                     utils.QType.ERROR)
                return -1
            failed_requests = []
            success_requests = []
            if request_id_first is None:
                # remove all requests
                if not ask_confirmation or self.command_processor.accept_yes_no(
                        "Are you sure you want to remove all requests?",
                        utils.QType.WARNING):
                    for i, request_id in enumerate(copy.deepcopy(list(self.state['requests'].keys()))):
                        if self.rem_request(self, request_id, False) == -1:
                            failed_requests.append(request_id)
                        else:
                            success_requests.append(request_id)
                else:
                    self.print_formatted(f"Removal of all requests cancelled.", utils.QType.INFORMATION)
                    return
            elif request_id_last is not None:
                if not ask_confirmation or self.command_processor.accept_yes_no(
                        f"Are you sure you want to remove requests with id between and including {request_id_first} and {request_id_last}?",
                        utils.QType.WARNING):
                    # remove a range of requests
                    for i, request_id in enumerate(copy.deepcopy(list(self.state['requests'].keys()))):
                        if request_id_first <= request_id <= request_id_last:
                            if self.rem_request(self, request_id, False) == -1:
                                failed_requests.append(request_id)
                            else:
                                success_requests.append(request_id)
                    else:
                        self.print_formatted(f"Removal of range of requests cancelled.", utils.QType.INFORMATION)
                        return
            else:
                # remove one request
                if self.rem_request(self, request_id_first, True) == -1:
                    failed_requests.append(request_id_first)
                else:
                    success_requests.append(request_id_first)
            if success_requests:
                self.print_formatted(f"Removal of {len(success_requests)} request(s) successful.",
                                     utils.QType.INFORMATION)
            if failed_requests:
                self.print_formatted(f"Removal of the following request(s) failed:\n\t{failed_requests} ",
                                     utils.QType.WARNING)

    @staticmethod
    def comm_requests_lower_ids(self):
        if not self.command_processor.accept_yes_no(
                "Are you sure you want to lower and make sequential all request ids?\n\tThis can take some time.",
                utils.QType.WARNING):
            self.print_formatted(f"Lowering of request ids is cancelled.", utils.QType.INFORMATION)
            return
        if not self.state['requests']:
            # noting to do
            self.print_formatted(f"Nothing is changed: The total request list is empty.", utils.QType.WARNING)
            return

        if int(sorted([int(key) for key in self.state['requests'].keys()])[-1]) + 1 == len(
                self.state['requests'].keys()):
            self.print_formatted(f"Nothing is changed: The request ids are already as low as they can be.",
                                 utils.QType.INFORMATION)
            return

        # remove all requests
        requests = self.state['requests']
        self.state['requests'] = dict()

        # re-add them one by one and save the old-new id mapping
        updated_ids = {}
        for req_id, req in tqdm(requests.items(), desc="Adding requests", ncols=self.progress_bar_width):
            # remove the old id and fetch new
            old_id = req_id
            del req['id']
            _, new_id = self.add_request(self, req, used_from_interface=True, print_information=False)
            # the update key-method assumes the add_request method avoids collisions
            if old_id != new_id:
                updated_ids[old_id] = new_id

        if not updated_ids:
            self.print_formatted(f"Nothing is changed: The request ids are already as low as they can be.",
                                 utils.QType.INFORMATION)
            return

        # update renewed ids in batches
        for batch in self.state['batches'].values():
            batch.update_ids(updated_ids)

        self.print_formatted(f"Successfully lowered all request ids.", utils.QType.INFORMATION)

    # ------------------------------------- Request command helpers ------------------------------------- #

    @staticmethod  # internal usage
    def get_specific_requests(self, request_ids, sort_order=SortOrder.INDEX.value, sort_ascending=True, get_str=False):
        with self.requests_list_lock:
            reqs_used = {}
            # return a range of requests (missing items are skipped)
            for request_id in request_ids:
                if request_id not in self.state['requests']:
                    self.print_formatted(
                        f"Cannot get request: The request with id '{request_id}' is not in the total request list!",
                        utils.QType.WARNING)
                else:
                    reqs_used[request_id] = self.state['requests'][request_id]
            req_list = utils.sort_requests(reqs_used, sort_order, sort_ascending)
            if get_str:
                ret_str = "The matching request(s):\n"
                ret_str += f"{utils.print_request_table(req_list)}\nTotal number: {len(self.state['requests'].keys())}"
                return ret_str
            else:
                self.print_formatted("The matching request(s):", utils.QType.INFORMATION)
                self.print_formatted(
                    f"{utils.print_request_table(req_list)}\nTotal number: {len(self.state['requests'].keys())}",
                    utils.QType.NONE)

    @staticmethod
    def add_request(self, a_request, used_from_interface=False, print_information=True):
        # requires a lock as both the UI and the REST server can add requests in parallel
        # will not create an immediate batch from requests that came from the interface (or imports)
        with self.requests_list_lock:
            # add a body if there is none
            if 'body' not in a_request:
                a_request['body'] = ""
            # check if the request is unique - O(n)
            duplicate_id = None
            for req in self.state['requests'].values():
                diff = utils.compare_requests(req, a_request)
                if not diff['fail']['total']:
                    if print_information:
                        self.print_formatted(
                            f"New request is not added, it already exists: \n{utils.tabbed_string(utils.print_request_table([req]), 1)}",
                            utils.QType.WARNING, not used_from_interface)
                    duplicate_id = req['id']
                    break
            if duplicate_id is None:
                # yay! it is new, create a new req_id
                req_id = str(max([int(req_id) for req_id in self.state['requests']] + [-1]) + 1)
                a_request['id'] = req_id
                # add to requests
                self.__change_state('requests', a_request, req_id)
                if print_information:
                    try:
                        a_request = self.format_request_form_body(a_request)
                    except ValueError as e:
                        self.print_formatted(f"Request body is invalid!\n\t{e}", utils.QType.WARNING)
                    if used_from_interface:
                        self.print_formatted(
                            f"Added new request:\n{utils.tabbed_pprint_request(a_request, 1)}\nTotal number: {len(self.state['requests'].keys())}",
                            utils.QType.INFORMATION, not used_from_interface)
                    else:
                        self.print_formatted(
                            f"Added new request:\n{utils.tabbed_string(utils.print_request_table([a_request]), 1)}\nTotal number: {len(self.state['requests'].keys())}",
                            utils.QType.INFORMATION, not used_from_interface)
            else:
                req_id = duplicate_id

            # perform mode-specific actions with new or duplicate request
            if not used_from_interface and self.state['immediate_mode'] != "off":
                # note that the last two settings are only used in immediate mode
                par, seq, allow_redirects, sync_last_byte, send_timeout = self.state['immediate_settings']
                if self.state['immediate_mode'] == "curr":
                    # add to current batch
                    if not self.state['current_batch']:
                        self.print_formatted(f"Cannot add a request to current batch: there is no current batch",
                                             utils.QType.ERROR,
                                             not used_from_interface)
                        return -1, req_id
                    current_batch = self.state['batches'][self.state['current_batch']]
                    try:
                        current_batch.add(req_id, 0, par, seq, False)
                    except Exception as e:
                        self.print_formatted(f"Cannot add a request to current batch:\n\t{e}", utils.QType.ERROR,
                                             not used_from_interface)
                        return -1, req_id
                    else:
                        self.print_formatted(f"Request {req_id} added to current batch.", utils.QType.INFORMATION)
                else:
                    # add to immediate batch
                    if self.immediate_batch_name in self.state['batches']:
                        if self.state['batches'][self.immediate_batch_name].has_results():
                            # already sent, can be overwritten
                            self.rem_batch_by_name(self, self.immediate_batch_name, True)
                    if self.immediate_batch_name not in self.state['batches']:
                        # create new immediate batch
                        return self.comm_batches_create_new(self, self.immediate_batch_name, False,
                                                                    not used_from_interface,
                                                                    allow_redirects, sync_last_byte, send_timeout)

                    immediate_batch = self.state['batches'][self.immediate_batch_name]
                    try:
                        immediate_batch.add(req_id, 0, par, seq, False)
                    except Exception as e:
                        # this should not be possible
                        self.print_formatted(f"Cannot add a request to immediate batch:\n\t{e}", utils.QType.ERROR,
                                             not used_from_interface)
                        return -1, req_id
                    else:
                        self.print_formatted(f"Request {req_id} added to immediate batch.", utils.QType.INFORMATION)
        return None, req_id

    @staticmethod
    def colorprint_comp_results(self, results):
        string = ""
        for key in results.keys():
            string += f"\n{key}: {{\n"
            for sub_key in results[key]:
                string += f"\t{sub_key}: {{\n"
                for sub_sub_key in results[key][sub_key]:
                    string += f"\t\t{sub_sub_key}: {{\n"
                    if type(results[key][sub_key][sub_sub_key]) is list:
                        string += utils.tabbed_string(str(results[key][sub_key][sub_sub_key]), 3) + "\n"
                    else:
                        string += utils.tabbed_string(str(results[key][sub_key][sub_sub_key]), 1) + "\n"
                    string += "\t\t}\n"
                string += "\t}\n"
            string += "}\n"

        self.print_formatted_multi(
            utils.tabbed_string(string, 1),
            utils.QType.NONE,
            {re.compile(r"^\t*-"): utils.QType.RED, re.compile(r"^\t*\+"): utils.QType.GREEN})

    # Note: only to be used internally with a valid request-id!
    @staticmethod
    def request_used_in(self, request_id):
        used_in = []
        for batch_name in self.state['batches']:
            if self.state['batches'][batch_name].get(request_id):
                used_in.append(batch_name)
        return used_in

    def gui_remove_request(self, request_id):
        curr_batch = self.state['batches'][self.state['current_batch']]
        curr_batch.remove(request_id, None)

    @staticmethod  # do not add requests to this list in any other way
    def rem_request(self, request_id, ask_confirmation=True):
        with self.requests_list_lock:
            if request_id not in self.state['requests']:
                self.print_formatted(f"Cannot remove request:\n\t"
                                     f"The request with id '{request_id}' is not in the total request list!",
                                     utils.QType.ERROR)
                return -1
            used_in = self.request_used_in(self, request_id)
            if used_in:
                if self.immediate_batch_name in used_in:
                    self.print_formatted(f"Not allowed to remove request:\n\t"
                                         f"The request with id '{request_id}' is (also) used by the immediate batch!",
                                         utils.QType.ERROR)
                    return -1
                if not ask_confirmation:
                    self.print_formatted(f"The request with id '{request_id}' is used by batches: "
                                         f"{used_in}. It must be removed individually.",
                                         utils.QType.ERROR)
                    return -1
                # remove request from the batches
                if ask_confirmation:
                    if not self.command_processor.accept_yes_no(f"The request with id '{request_id}' is used by batches: "
                                                                f"{used_in}, continue?\n\tIt will be removed from these batches and their results are cleared!!",
                                                                utils.QType.WARNING):
                        return -1
                for batch_name in used_in:
                    self.state['batches'][batch_name].remove(request_id)
                ask_confirmation = False

            self.__change_state('requests', sub_search=request_id, do_delete=True)
            self.print_formatted(f"Request with id '{request_id}' is removed", utils.QType.INFORMATION)

    # --------------------------------------------------------------------------------------------------- #
    # ------------------------------------- Batch command functions ------------------------------------- #
    # --------------------------------------------------------------------------------------------------- #
    @staticmethod
    def get_batch_result_formatting():
        return {re.compile(r".*?\t\s{10}[12]\d\d\s\s.*?"): utils.QType.GREEN,
                re.compile(r".*?\t\s{10}[3]\d\d\s\s.*?"): utils.QType.YELLOW,
                re.compile(r".*?\t\s{10}[4]\d\d\s\s.*?"): utils.QType.RED,
                re.compile(r".*?\t\s{10}[5]\d\d\s\s.*?"): utils.QType.BLUE,
                re.compile(r"'status_code': [12].."): utils.QType.GREEN,
                re.compile(r"'status_code': 3.."): utils.QType.YELLOW,
                re.compile(r"'status_code': 4.."): utils.QType.RED,
                re.compile(r"'status_code': 5.."): utils.QType.BLUE}

    @staticmethod
    def comm_batches_send(self, index=None, print_results=True, immediate_allowed=False):
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        if not immediate_allowed and name == self.immediate_batch_name:
            self.print_formatted(f"Not allowed to send immediate batch from interface!", utils.QType.ERROR)
            return -1
        batch = self.state['batches'][name]
        if batch.is_empty():
            self.print_formatted(f"Cannot send the batch: The batch is empty!", utils.QType.ERROR)
            return -1
        self.print_formatted(f"Sending the batch with name '{name}'..", utils.QType.INFORMATION)

        if batch.has_results() and not self.command_processor.accept_yes_no("Batch already has results, overwrite?",
                                                                            utils.QType.WARNING):
            self.print_formatted(f"Batch sending cancelled.", utils.QType.INFORMATION)
            return -1
        batch.overwrite_results(sender.send_batch(batch, self.state['requests'], self.proxy))

        self.print_formatted("The batch is sent.", utils.QType.INFORMATION)
        if print_results:
            res_summary = batch.get_last_results(True, False)
            res_full = batch.get_last_results(True, True)
            res_config = self.comm_batches_get_contents(self, 0, False, True)
            if immediate_allowed:
                self.server_send_queue.put({'type': 'results', 'content': [res_summary, res_full, res_config]})
            self.print_formatted_multi(f"Results:\n{res_full}",
                                       default_type=utils.QType.NONE,
                                       special_types=self.get_batch_result_formatting()
                                       )

    @staticmethod
    def comm_batches_info(self):
        self.print_formatted(f"Table of batches info:", utils.QType.INFORMATION)
        dicts = [batch.get_mini_summary_dict() for batch in self.state['batches'].values()]
        if dicts:
            contents = [[item['name'], item['items'], item['requests'],
                         item['has_results'], item['is_synced'], item['is_redir']] for item in
                        sorted(dicts, key=lambda x: x['name'])]
        else:
            contents = []
        col_names = Batch.get_mini_summary_header()
        output = tabulate(contents, col_names, showindex="always", tablefmt="simple") + "\n"
        if self.state['current_batch']:
            self.print_formatted_multi(output, utils.QType.NONE,
                                       {f" {re.escape(self.state['current_batch'])} ": utils.QType.BLUE})
        else:
            self.print_formatted(output, utils.QType.NONE)

    @staticmethod
    def comm_batches_set_current(self, index, immediate_allowed=False):
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        return self.set_curr_batch_by_name(self, name, immediate_allowed)

    @staticmethod
    def add_prefix(self, name):
        if name is None or 'project_name' not in self.state or self.state['project_name'] == "":
            return name
        else:
            return self.state['project_name'] + name

    @staticmethod
    def comm_batches_create_new(self, name, set_current_batch=True, immediate_allowed=False,
                                       allow_redirects=False, sync_last_byte=False, send_timeout=20):
        if name != self.immediate_batch_name:
            name = self.add_prefix(self, name)
        if not immediate_allowed and name == self.immediate_batch_name:
            self.print_formatted(f"Not allowed to create immediate batch from interface!", utils.QType.ERROR)
            return -1
        if name in self.state['batches']:
            self.print_formatted(f"Cannot create batch: Batch name is already used!", utils.QType.ERROR)
            return -1
        new_batch = Batch(name, self.BATCHES_RENDERED_FILE_DIR, allow_redirects, sync_last_byte, send_timeout)
        self.__change_state('batches', new_batch, sub_search=name)
        self.print_formatted(f"Created a new batch:", utils.QType.INFORMATION)
        self.print_formatted(new_batch.get_summary(), utils.QType.BLUE)
        if set_current_batch:
            return self.set_curr_batch_by_name(self, name)

    @staticmethod
    def comm_batches_get_project(self):
        if 'project_name' not in self.state or self.state['project_name'] == "":
            self.print_formatted(f"The current project name prefix is empty.", utils.QType.INFORMATION)
            return -1
        self.print_formatted(f"Current project name prefix: '{self.state['project_name']}'", utils.QType.INFORMATION)

    @staticmethod
    def comm_batches_set_project(self, name=None):
        if name is None:
            self.__change_state('project_name', "")
        else:
            self.__change_state('project_name', name + "_")
        self.print_formatted(f"Current project name prefix: '{self.state['project_name']}'", utils.QType.INFORMATION)

    @staticmethod
    def comm_batches_get_contents(self, index, full_contents=False, get_string=False):
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        if get_string:
            ret_str = "Batch contents:\n"
            ret_str += self.state['batches'][name].get_summary(full_contents) + "\n"
            ret_str += self.get_specific_requests(self, self.state['batches'][name].get_reqs(), get_str=True)
            return ret_str
        else:
            self.print_formatted(f"Batch contents:", utils.QType.INFORMATION)
            self.print_formatted(f"{self.state['batches'][name].get_summary(full_contents)}", utils.QType.NONE)
            self.get_specific_requests(self, self.state['batches'][name].get_reqs())

    @staticmethod
    def comm_batches_get_results(self, index, get_tables=False, get_groups=False):
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        self.print_formatted(f"Batch results:", utils.QType.INFORMATION)
        results = self.state['batches'][name].get_last_results(get_tables, get_groups)
        if not results:
            self.print_formatted(f"No results yet.", utils.QType.NONE)
        else:
            self.print_formatted_multi(f"Results:\n{results}",
                                       default_type=utils.QType.NONE,
                                       special_types=self.get_batch_result_formatting()
                                       )

    @staticmethod  # internal usage
    def comm_batches_comp_resp_groups(self, index, group_nr_1, group_nr_2, request_id=None):
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        return self.comp_batch_resp_groups_by_name(self, name, group_nr_1, group_nr_2, request_id)

    @staticmethod
    def comm_batches_rename(self, name_new, index=None):
        name_new = self.add_prefix(self, name_new)
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        if name_new in self.state['batches']:
            self.print_formatted(f"Cannot rename batch '{name}' to '{name_new}': New batch name already exists!",
                                 utils.QType.ERROR)
            return -1

        if not self.remove_batch_file(self, name):
            self.print_formatted(f"Cannot remove old batch file from state, please make sure it is gone.",
                                 utils.QType.WARNING)
        self.__change_state('batches', value=self.state['batches'].pop(name, None), sub_search=name_new)
        self.state['batches'][name_new].set_name(name_new)
        if name == self.state['current_batch']:
            self.__change_state('current_batch', name_new)

        self.print_formatted(f"Batch successfully renamed: '{name}' to '{name_new}'.",
                             utils.QType.INFORMATION)

    @staticmethod
    def comm_batches_copy(self, name_new, index=None):
        name_new = self.add_prefix(self, name_new)
        name = self.batch_index_to_name(self, index)
        if name == -1:
            return -1
        if name_new in self.state['batches']:
            self.print_formatted(f"Cannot copy batch '{name}' to '{name_new}': New batch name already exists!",
                                 utils.QType.ERROR)
            return -1
        self.__change_state('batches', copy.deepcopy(self.state['batches'][name]), sub_search=name_new)
        self.state['batches'][name_new].set_name(name_new)
        if name == self.state['current_batch']:
            self.__change_state('current_batch', name_new)

        self.print_formatted(f"Batch successfully copied: '{name}' to '{name_new}'.",
                             utils.QType.INFORMATION)

    @staticmethod
    def comm_batches_export(self, first_index=None, last_index=None):
        names = self.batch_indices_to_names(self, first_index, last_index)
        if names == -1:
            return -1
        for name in names:
            self.exp_batch_by_name(self, name)

    @staticmethod
    def comm_batches_import_list(self):
        self.print_formatted(f"Table of batches that can be imported:", utils.QType.INFORMATION)
        output = tabulate([[name.split(".")[0]] for name in self.list_exp_batch_files(self)],
                          ["Name"], showindex="always", tablefmt="simple") + "\n"
        self.print_formatted(output, utils.QType.NONE)

    @staticmethod
    def comm_batches_import(self, first_index=None, last_index=None):

        if first_index is None and last_index is None and self.dialog_queue is None:
            self.print_formatted(f"Importing via dialog failed: the system does not support it!",
                                 utils.QType.ERROR)
            return -1

        filenames = self.imp_batch_indices_to_names(self, first_index, last_index)

        # check if dialog must be shown
        if filenames == -1:
            # select one or more batch files to import
            filenames = None
            while not filenames:
                filenames = self.select_files(self, "Select one or more batch export files", ".json")
                if not filenames and not self.command_processor.accept_yes_no("Selected files are not valid, retry?",
                                                                              utils.QType.WARNING):
                    return -1

        success_batches = []
        failed_batches = []
        for filename in filenames:
            if self.imp_batch_by_name(self, filename) == -1:
                failed_batches.append(filename)
            else:
                success_batches.append(filename)

        if success_batches:
            self.print_formatted(f"Importing of {len(success_batches)} batches(s) successful.", utils.QType.INFORMATION)
        if failed_batches:
            self.print_formatted(f"Importing of the following batches(s) failed:\n\t{failed_batches} ",
                                 utils.QType.WARNING)

    @staticmethod
    def comm_batches_regroup(self):
        self.print_formatted(f"Regrouping all batches..", utils.QType.INFORMATION)
        time.sleep(0.25)
        for batch_name in tqdm(list(self.state['batches'].keys()), desc="Grouping batches",
                               ncols=self.progress_bar_width):
            batch = self.state['batches'][batch_name]
            batch.redo_all_grouping(force=True)
        time.sleep(0.25)
        self.print_formatted(f"Done.", utils.QType.INFORMATION)

    # ------------------------------------- Batch command helpers ------------------------------------- #

    # looks up all corresponding !importable! batch names
    # will fail if one lookup fails
    @staticmethod
    def imp_batch_indices_to_names(self, start_index=None, end_index=None):
        if start_index is None or end_index is None:
            name = self.imp_batch_index_to_name(self, start_index)
            if name == -1:
                return -1
            return [name]
        names = []
        indices = self.list_exp_batch_files(self)
        for i in range(start_index, end_index + 1):
            names.append(self.imp_batch_index_to_name(self, i, indices))
        if -1 in names:
            return -1
        return names

    # looks up all corresponding batch names
    # will fail if one lookup fails
    @staticmethod
    def batch_indices_to_names(self, start_index, end_index):
        if start_index is None or end_index is None:
            name = self.batch_index_to_name(self, start_index)
            if name == -1:
                return -1
            return [name]
        names = []
        indices = self.get_batch_indices()
        for i in range(start_index, end_index + 1):
            names.append(self.batch_index_to_name(self, i, indices))
        if -1 in names:
            return -1
        return names

    @staticmethod
    def imp_batch_index_to_name(self, index, indices=None):
        if index is None:
            return -1
        if not type(index) is int:
            self.print_formatted(f"Batch index must be an integer! This is not the case: '{index}'", utils.QType.ERROR)
            return -1
        if not indices:
            indices = self.list_exp_batch_files(self)
        if index < 0 or index >= len(indices):
            self.print_formatted(f"Batch index '{index}' does not exist!", utils.QType.ERROR)
            return -1
        return os.path.abspath(self.BATCHES_EXP_FILE_DIR + indices[index])

    @staticmethod
    def batch_index_to_name(self, index, indices=None):
        if index is None:
            if not self.state['current_batch']:
                self.print_formatted(f"Cannot select current batch: There is no current batch!", utils.QType.ERROR)
                return -1
            return self.state["current_batch"]
        if not type(index) is int:
            self.print_formatted(f"Batch index must be an integer! This is not the case: '{index}'", utils.QType.ERROR)
            return -1
        if not indices:
            indices = self.get_batch_indices()
        if index < 0 or index >= len(indices):
            self.print_formatted(f"Batch index '{index}' does not exist!", utils.QType.ERROR)
            return -1
        return indices[index]

    @staticmethod
    def set_curr_batch_by_name(self, name, immediate_allowed=False):
        if not immediate_allowed and name == self.immediate_batch_name:
            self.print_formatted(f"Not allowed to set immediate batch as current batch from interface!",
                                 utils.QType.ERROR)
            return -1
        self.__change_state('current_batch', name)
        self.print_formatted(f"Set current batch to batch with name '{name}'.", utils.QType.INFORMATION)

    @staticmethod  # internal usage
    def comp_batch_resp_groups_by_name(self, batch_name, group_nr_1, group_nr_2, request_id=None):
        try:
            results, request_id = self.state['batches'][batch_name].compare_group_repr(group_nr_1, group_nr_2,
                                                                                       request_id)
            if not results or results == -1:
                self.print_formatted(f"No results yet.", utils.QType.NONE)
            else:
                self.print_formatted(
                    f"Comparison of result groups {group_nr_1} and {group_nr_2} in request '{request_id}' of batch '{batch_name}':",
                    utils.QType.INFORMATION)
                self.colorprint_comp_results(self, results)
        except Exception as e:
            self.print_formatted(f"{e}", utils.QType.ERROR)
            return -1

    @staticmethod  # internal usage
    def exp_batch_without_requests_by_name(self, folder, name):
        the_batch = self.state['batches'][name]

        # get batch as json-compatible dict
        js_batch = self.jsonify_batch(the_batch)

        exp_file = f"{name}.json"
        exp_path = folder + exp_file
        if os.path.exists(exp_path):
            os.remove(exp_path)

        with open(exp_path, 'w') as file:
            utils.store_json_file(exp_path, js_batch)

    @staticmethod  # internal usage
    def exp_batch_by_name(self, name):
        self.print_formatted(f"Exporting batch '{name}'..", utils.QType.INFORMATION)

        the_batch = self.state['batches'][name]
        # get all required reqs
        reqs = the_batch.get_reqs()
        full_reqs = {}
        for req in reqs:
            full_reqs[req] = self.state['requests'][req]

        # get batch as json-compatible dict
        js_batch = self.jsonify_batch(the_batch)
        js_batch['requests'] = full_reqs

        if not os.path.exists(self.BATCHES_EXP_FILE_DIR):
            os.mkdir(self.BATCHES_EXP_FILE_DIR)

        exp_file = f"{name}.json"
        if os.path.exists(self.BATCHES_EXP_FILE_DIR + exp_file):
            randomness = utils.randomword(5)
            self.print_formatted(f"Batch already exported once: adding randomness.", utils.QType.INFORMATION)
            exp_file = exp_file.replace(".json", "") + "_random_" + randomness + ".json"

        exp_path = self.BATCHES_EXP_FILE_DIR + exp_file
        with open(exp_path, 'w') as file:
            utils.store_json_file(exp_path, js_batch)
        self.print_formatted(f"Batch exported successfully to '{exp_path}'", utils.QType.INFORMATION)

    # open a dialog to pick a json file
    @staticmethod
    def select_files(self, title, extension):
        if not os.path.exists(self.BATCHES_EXP_FILE_DIR):
            os.mkdir(self.BATCHES_EXP_FILE_DIR)
        start_dir = os.path.abspath(self.BATCHES_EXP_FILE_DIR)
        filetypes = ("Target files", "*" + extension), ("all files", "*.*")
        title = title + "(." + extension + ")"

        self.dialog_queue.put({'title': title, 'filetypes': filetypes, 'start_dir': start_dir})
        return self.dialog_queue.get()

    @staticmethod
    def imp_batch_without_requests_by_name(self, filename):
        # load selected batch from file
        batch_import = utils.load_json_file(filename)
        if not batch_import:
            self.print_formatted(
                f"Cannot import batch: {filename}",
                utils.QType.ERROR)
            return -1
        batch = self.objectify_batch(self, batch_import)
        # adding the new batch
        self.state['changed'] = True
        if 'batches' not in self.state:
            self.state['batches'] = {}
        self.state['batches'][batch.name] = batch
        # self.__change_state('batches', batch, sub_search=batch.name)

    @staticmethod
    def imp_batch_by_name(self, filename):
        self.print_formatted(f"Importing batch file '{filename}'..", utils.QType.INFORMATION)
        # load selected batch from file
        batch_import = utils.load_json_file(filename)
        batch = self.objectify_batch(self, batch_import)
        if 'requests' not in batch_import:
            self.print_formatted(
                f"Cannot import batch: The file is missing the matching requests.\n\tIs this a state file maybe?",
                utils.QType.WARNING)
            return -1
        requests = batch_import['requests']
        # add the requests if necessary
        self.print_formatted(f"Importing requests..", utils.QType.INFORMATION)
        updated_ids = {}
        for req_id, req in requests.items():
            # remove the old id and fetch new
            old_id = req_id
            del req['id']
            _, new_id = self.add_request(self, req, used_from_interface=True)
            # the update key-method assumes the add_request method avoids collisions
            if old_id != new_id:
                updated_ids[old_id] = new_id
        # update renewed ids in batch
        if updated_ids:
            batch.update_ids(updated_ids)
        # add batch itself
        was_current = False
        while True:
            if batch.name == self.immediate_batch_name:
                self.print_formatted(f"You are not allowed to use the immediate batch name 'Imm'.", utils.QType.WARNING)
            elif batch.name in self.state['batches']:
                if self.command_processor.accept_yes_no(f"The batch name '{batch.name}' already exists, overwrite it?",
                                                        utils.QType.WARNING):
                    # removing current batch
                    if batch.name == self.state['current_batch']:
                        was_current = True
                    self.rem_batch_by_name(self, batch.name, True)
                    break
            elif len(batch.name) > 0:
                # a valid name!
                break
            batch.name = self.command_processor.accept_of_type("Please provide a new name for the batch:", str,
                                                               utils.QType.INFORMATION)
        # adding the new batch
        self.__change_state('batches', batch, sub_search=batch.name)
        if was_current:
            self.__change_state('current_batch', batch.name)
        self.print_formatted(f"Batch '{batch.name}' with {len(requests.keys())} requests imported successfully.",
                             utils.QType.INFORMATION)

    @staticmethod
    def comm_batches_remove(self, first_index=None, last_index=None, immediate_allowed=False):
        names = self.batch_indices_to_names(self, first_index, last_index)
        if names == -1:
            return -1
        for name in names:
            self.rem_batch_by_name(self, name, immediate_allowed)

    @staticmethod
    def rem_batch_by_name(self, name=None, immediate_allowed=False):
        """
        Removes the batch with this name from the state
        :param self: reference to the CompuRacer
        :param name: name of the batch
        :param immediate_allowed:
        :return: 0 on success and -1 on error
        """
        if name == -1:
            return -1
        if not immediate_allowed and name == self.immediate_batch_name:
            self.print_formatted(f"Not allowed to remove immediate batch from interface!", utils.QType.ERROR)
            return -1
        is_current_batch = self.state['current_batch'] is not None and name == self.state['current_batch']
        if immediate_allowed:
            self.state['batches'][name].remove_the_files()
            self.__change_state('batches', sub_search=name, do_delete=True)
            if is_current_batch:
                self.__change_state('current_batch', [])
        else:
            warning_string = f"Are you sure you want to remove the batch with name '{name}'?"
            if is_current_batch:
                warning_string += "\nThis is the current batch!"
            if self.command_processor.accept_yes_no(warning_string, utils.QType.WARNING):
                self.state['batches'][name].remove_the_files()
                if not self.remove_batch_file(self, name):
                    self.print_formatted(f"Cannot remove old batch file from state, please make sure it is gone.",
                                         utils.QType.WARNING)
                self.__change_state('batches', sub_search=name, do_delete=True)
                if is_current_batch:
                    self.__change_state('current_batch', [])
                self.print_formatted(f"Batch with name '{name}' is removed.", utils.QType.INFORMATION)
            else:
                self.print_formatted(f"Removal of batch cancelled.", utils.QType.INFORMATION)

    @staticmethod
    def format_request_form_body(the_request):
        the_request = copy.deepcopy(the_request)
        # sort and decode cookies
        if 'Cookie' in the_request['headers']:
            cookies = urllib.parse.unquote(the_request['headers']['Cookie']).split("; ")
            cookies = sorted(cookies)
            the_request['headers']['Cookie'] = "; ".join(cookies)
        # format body
        if 'body' in the_request and len(the_request['body']) > 0:
            if type(the_request['body']) is dict:
                return the_request
            if 'Content-Type' in the_request['headers'] and \
                    (the_request['headers']['Content-Type'].startswith("multipart/form-data") or
                     the_request['headers']['Content-Type'].startswith("application/x-www-form-urlencoded")):

                key_values = [key_values.split("=") for key_values in the_request['body'].split("&")]
                new_body = {}
                for key_value in key_values:
                    if len(key_value) < 2:
                        # skip invalid keys
                        continue
                    new_body[urllib.parse.unquote(key_value[0])] = urllib.parse.unquote(key_value[1])
                the_request['body'] = new_body
        return the_request

    @staticmethod
    def list_exp_batch_files(self):
        return sorted(list(os.listdir(self.BATCHES_EXP_FILE_DIR)))

    @staticmethod
    def remove_batch_file(self, name):
        for file in os.listdir(self.CLIENT_BATCHES_LOC):
            if file == name + ".json":
                os.remove(self.CLIENT_BATCHES_LOC + file)
                return True
        return False

    # -------------------------------------------------------------------------------------------------------- #
    # ------------------------------------- Curr batch command functions ------------------------------------- #
    # -------------------------------------------------------------------------------------------------------- #
    @staticmethod
    def comm_curr_get_contents(self, full_contents=False):
        """
        Gets the requests and their settings of the current batch
        :param self: reference to the CompuRacer
        :param full_contents: if True, shows the parallel and sequntial settings visually
        :return: 0 on success and -1 on error
        """
        return self.comm_batches_get_contents(self, None, full_contents)

    @staticmethod
    def comm_curr_change_redirects(self, enable_redirects=True):
        """
        Changes whether the current batch follows redirects when sending requests
        :param self: reference to the CompuRacer
        :param enable_redirects: if True, it follows redirects
        :return: 0 on success and -1 on error
        """
        if not self.state['current_batch']:
            self.print_formatted(f"Cannot add change redirects of current batch: There is no current batch!",
                                 utils.QType.ERROR)
            return -1
        self.state['batches'][self.state['current_batch']].set_allow_redirects(enable_redirects)
        self.print_formatted(f"Set follow redirects of current batch to: '{enable_redirects}'", utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_change_sync(self, enable_sync=True):
        """
        Changes whether the current batch syncs the last byte of the request content (if any)
        :param self: reference to the CompuRacer
        :param enable_sync: if True, it syncs the last byte
        :return: 0 on success and -1 on error
        """
        if not self.state['current_batch']:
            self.print_formatted(f"Cannot add change last byte sync of current batch: There is no current batch!",
                                 utils.QType.ERROR)
            return -1
        self.state['batches'][self.state['current_batch']].set_sync_last_byte(enable_sync)
        self.print_formatted(f"Set last byte sync of current batch to: '{enable_sync}'", utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_change_timeout(self, send_timeout=20):
        """
        Sets the current batch send timout (default 20 seconds).
        :param self: reference to the CompuRacer
        :param send_timeout: the send timeout
        :return: 0 on success and -1 on error
        """
        if not self.state['current_batch']:
            self.print_formatted(f"Cannot add change send timeout of current batch: There is no current batch!",
                                 utils.QType.ERROR)
            return -1
        if send_timeout < 1:
            self.print_formatted(f"The send timeout must be >= 1! Input: {send_timeout} seconds", utils.QType.ERROR)
            return -1
        self.state['batches'][self.state['current_batch']].set_send_timeout(send_timeout)
        self.print_formatted(f"Set send timeout of current batch to: '{send_timeout}'", utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_get_results(self, get_tables=False, get_groups=False):
        """
        Get the latest results of the current batch
        :param self: reference to the CompuRacer
        :param get_tables: whether to include summary tables about the results
        :param get_groups: whether to show the group representatives
        :return: 0 on success and -1 on error
        """
        return self.comm_batches_get_results(self, None, get_tables, get_groups)

    @staticmethod
    def comm_curr_compare_groups(self, group_nr_1, group_nr_2, request_id=None):
        """
        Within the current batch, it compares the result group representatives selected
        :param self: reference to the CompuRacer
        :param group_nr_1: the first group id to compare
        :param group_nr_2: the second group id to compare
        :param request_id: the request id, or if None, the first request of the batch (alphabetically)
        :return: 0 on success and -1 on error
        """
        return self.comm_batches_comp_resp_groups(self, None, group_nr_1, group_nr_2, request_id)

    # NOTE: it does not overwrite an item with the same id & wait_time.
    @staticmethod
    def comm_curr_add(self, request_id, wait_time=0, dup_par=1, dup_seq=1):
        """
        Adds the request with this wait time and the parallel and sequential values to the current batch
        :param self: reference to the CompuRacer
        :param request_id: the id of the request
        :param wait_time: the wait time of the request before sending it
        :param dup_par: the parallel duplication
        :param dup_seq: the parallel sequential
        :return: 0 on success and -1 on error
        :return:
        """
        if request_id not in self.state['requests']:
            self.print_formatted(
                f"Cannot add a request to current batch: The request with id '{request_id}' is not in the request list!",
                utils.QType.ERROR)
            return -1
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot add a request to current batch: There is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]
        try:
            curr_batch.add(request_id, wait_time, dup_par, dup_seq, False)
        except Exception as e:
            self.print_formatted(f"Cannot add a request to current batch:\n\t{e}", utils.QType.ERROR)
            return -1
        self.print_formatted(f"The request was added to the current batch:\n"
                             f"{curr_batch.get_info(request_id, wait_time)}",
                             utils.QType.INFORMATION)

    # NOTE: it does not overwrite an item with the same id & wait_time.
    @staticmethod
    def comm_curr_update(self, request_id, wait_time=0, dup_par=1, dup_seq=1):
        """
        Updates the parallel and sequential values of the request with this wait_time in the current batch
        :param self: reference to the CompuRacer
        :param request_id: the id of the request
        :param wait_time: the wait time of the request before sending it
        :param dup_par: the parallel duplication
        :param dup_seq: the parallel sequential
        :return: 0 on success and -1 on error
        """
        if request_id not in self.state['requests']:
            self.print_formatted(
                f"Cannot update a request in the current batch: The request with id '{request_id}' is not in this batch!",
                utils.QType.ERROR)
            return -1
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot update a request in the current batch: There is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]

        try:
            old_state = curr_batch.get_info(request_id, wait_time)
            curr_batch.add(request_id, wait_time, dup_par, dup_seq, True)
        except Exception as e:
            self.print_formatted(f"Cannot update request in current batch:\n\t{e}", utils.QType.ERROR)
            return -1
        self.print_formatted(f"The request was updated in the current batch:\n"
                             f"Old: {old_state}\n"
                             f"New: {curr_batch.get_info(request_id, wait_time)}\n",
                             utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_get_ignore(self):
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot get ignored fields in grouping of results in the current batch:\n"
                f"\tThere is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]
        fields = curr_batch.get_ignored_fields()
        self.print_formatted(f"The ignored fields in grouping of results in the current batch:\n\t"
                             f"{fields}\n", utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_add_ignore(self, field_name):
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot add field to ignored fields in grouping of results in the current batch:\n"
                f"\tThere is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]
        if curr_batch.add_ignored_field(field_name) == -1:
            self.print_formatted(
                f"Cannot add field to ignored fields in grouping of results in the current batch:\n"
                f"\tThe field is already ignored!",
                utils.QType.WARNING)
            return -1
        self.print_formatted(f"Successfully added the ignored field '{field_name}' "
                             f"in grouping of results in the current batch", utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_reset_ignore(self):
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot add field to ignored fields in grouping of results in the current batch:\n"
                f"\tThere is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]
        if curr_batch.reset_ignored_fields() == -1:
            self.print_formatted(
                f"Cannot reset ignored fields in grouping of results in the current batch:\n"
                f"\tThey are already the default values.",
                utils.QType.WARNING)
            return -1
        fields = curr_batch.get_ignored_fields()
        self.print_formatted(
            f"Successfully resetted the ignored fields in grouping of results in the current batch:\n\t"
            f"{fields}\n", utils.QType.INFORMATION)

    @staticmethod
    def comm_curr_remove(self, request_id=None, wait_time=None):
        """
        Removes requests from the current batch
        :param self: reference to the CompuRacer
        :param request_id: the request to remove, or if None, all requests
        :param wait_time: the wait_time of the request to remove, or if None, all regardless of wait_time
        :return: 0 on success and -1 on error
        """
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot remove a request from current batch: There is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]
        if curr_batch.is_empty():
            self.print_formatted(f"Cannot remove a request from current batch: The current batch is empty!",
                                 utils.QType.ERROR)
            return -1
        if request_id is None:
            # remove all items from the batch
            question = "Are you sure you want to remove all requests from the current batch?"
        elif wait_time is None:
            # remove all items with a certain ID from the batch
            question = f"Are you sure you want to remove all requests with id '{request_id}' from the current batch?"
        else:
            # remove a specific item with a certain ID and wait_time from the batch
            question = f"Are you sure you want to remove the request with id '{request_id}' and wait_time '{wait_time}' from the current batch?"
        if self.command_processor.accept_yes_no(question, utils.QType.WARNING):
            num_removed = curr_batch.remove(request_id, wait_time)
            self.print_formatted(f"All matching requests are removed from the current batch.\nNumber: {num_removed}",
                                 utils.QType.INFORMATION)
        else:
            self.print_formatted(f"Removal of current batch requests cancelled.", utils.QType.INFORMATION)

    def gui_comm_curr_remove(self, request_id=None, wait_time=None):
        """
        Removes requests from the current batch
        :param self: reference to the CompuRacer
        :param request_id: the request to remove, or if None, all requests
        :param wait_time: the wait_time of the request to remove, or if None, all regardless of wait_time
        :return: 0 on success and -1 on error
        """
        if not self.state['current_batch']:
            self.print_formatted(
                f"Cannot remove a request from current batch: There is no current batch! First, select a current batch.",
                utils.QType.ERROR)
            return -1
        curr_batch = self.state['batches'][self.state['current_batch']]
        if curr_batch.is_empty():
            self.print_formatted(f"Cannot remove a request from current batch: The current batch is empty!",
                                 utils.QType.ERROR)
            return -1
        if request_id is None:
            # remove all items from the batch
            question = "Are you sure you want to remove all requests from the current batch?"
        self.print_formatted("Dit is een andere test voor request nummer : " + request_id)

        curr_batch.remove(request_id, wait_time)

    # ------------------------------------------------------------------------------------------------- #
    # ------------------------------------- Main helper functions ------------------------------------- #
    # ------------------------------------------------------------------------------------------------- #

    # used by REST server
    def add_request_from_json(self, a_json_request):
        self.add_request(self, utils.read_json(a_json_request))

    # used by REST server
    def trigger_immediate(self):
        if self.immediate_batch_name not in self.state['batches']:
            return -1
        if self.state['immediate_mode'] == "on":
            # send the immediate batch
            return self.comm_batches_send(self, self.get_index_by_name(self.immediate_batch_name),
                                          self.state['immediate_print'], True)

    def get_batch_indices(self):
        return sorted(self.state['batches'].keys())

    def get_index_by_name(self, name):
        if name in self.state['batches']:
            return self.get_batch_indices().index(name)
        else:
            return -1

    # ------------------------------------------------------------------------------------------------ #
    # --------------------------- Print, convert, load and store functions --------------------------- #
    # ------------------------------------------------------------------------------------------------ #

    @staticmethod
    def jsonify_batches(the_state):
        """
        Makes sure the batch content in the state can be saved as JSON.
        It will change a dict with tuple-keys to an array of key and value tuples
        :param the_state: reference to the CompuRacer state
        :return: the updated state
        """
        if not the_state['batches']:
            return the_state
        for name in the_state['batches'].keys():
            the_state['batches'][name] = the_state['batches'][name].get_as_dict()
        return the_state

    @staticmethod
    def jsonify_batch(the_batch):
        """
        Makes sure the content of this batch can be saved as JSON.
        It will change a dict with tuple-keys to an array of key and value tuples
        :param the_batch: the batch to update
        :return: the updated batch
        """
        return the_batch.get_as_dict()

    @staticmethod
    def objectify_batches(self, the_state):
        """
        Undoes the jsonify_batches function.
        :param the_state: reference to the CompuRacer state
        :return: the updated state
        """
        if not the_state['batches']:
            return the_state
        for name in the_state['batches'].keys():
            the_state['batches'][name] = self.objectify_batch(self, the_state['batches'][name])
        return the_state

    @staticmethod
    def objectify_batch(self, the_batch):
        """
        Undoes the jsonify_batch function.
        :param the_batch: the batch to update
        :return: the updated batch
        """
        return Batch.create_from_dict(the_batch, self.BATCHES_RENDERED_FILE_DIR)

    # should only be called by command handlers with print_buffered=False
    def print_formatted_multi(self, text, default_type=utils.QType.NONE, special_types=None, print_buffered=False):
        if special_types is None:
            special_types = dict()
        for line in text.split("\n"):
            string_type = default_type
            for matcher in special_types.keys():
                if re.search(matcher, line):
                    string_type = special_types[matcher]
            if print_buffered:
                self.command_processor.print_queued(line, string_type)
            else:
                self.command_processor.print_formatted(line, string_type)

    # should only be called by command handlers with print_buffered=False
    def print_formatted(self, text, string_type=utils.QType.NONE, print_buffered=False):
        if print_buffered:
            self.command_processor.print_queued(text, string_type)
        else:
            self.command_processor.print_formatted(text, string_type)

    # should only be called by command handlers!
    def __load_json(self, path, backup_path, msg=None):
        return utils.load_json_file(path, backup_path, msg, self.state['colored_output'])

    # should only be called by command handlers!
    def __store_json(self, json_path, json_data, backup_path=None, msg=None):
        utils.store_json_file(json_path, json_data, backup_path, msg, self.state['colored_output'])

    # access function for state is used to avoid storing an unchanged state
    def __change_state(self, variable, value=None, sub_search=None, do_delete=False):
        self.state['changed'] = True
        if sub_search is not None:
            if do_delete and self.state[variable] is not None and sub_search in self.state[variable]:
                del self.state[variable][sub_search]
            else:
                if variable not in self.state or self.state[variable] is None:
                    self.state[variable] = {}
                self.state[variable][sub_search] = value
        else:
            if do_delete and self.state is not None and variable in self.state:
                del self.state[variable]
            else:
                self.state[variable] = value


def check_state(racer):
    while not racer.is_shutdown:
        if racer.state and 'batches' in racer.state and racer.state['batches']:
            if not type(list(racer.state['batches'].values())[0]) is Batch:
                print("Batches dictified!!!")
                break
        time.sleep(0.1)


# -------------- main client program & server -------------- #
if __name__ == '__main__':

    dialog_queue = Queue()

    # initialize the racer
    racer = CompuRacer(dialog_queue)

    threading.Thread(target=check_state, args=(racer,)).start()

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
