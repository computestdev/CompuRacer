#!/usr/bin/env python3
"""
The CommandProcessor provides the Command Line Interface to the toolset.
It contains some help commands out of the box and supports adding additional commands and functions.
It will keep track of historical commands.
It is thread safe when all printing of the application is routed through this class.
"""

# --- All imports --- #
import sys
import threading
import time
import traceback
from enum import Enum
from queue import Queue

import src.utils as utils


class CommandProcessor:

    commands = {}
    welcome_function = None
    welcome_function_class = None

    executor_class_instance = None
    config = None
    changed = False
    cli_prompt = "processor"

    processor_thread = None
    printer_thread = None
    shutdown_processor = True

    processing_input = False
    printing_queued = False

    last_print_time = -1
    print_timeout_max = 10

    last_execution = None

    # while processing input
    print_queue = Queue()

    def __init__(self, config):
        self.config = config
        # add built-in help commands
        self.add_command(["help"], self.func_help, "Displays this help text, or all matching commands when a search query is provided", self,
                         arg_spec_opt=[("Search for a command", str, "* all help commands *"),
                                       ("Find exact match", bool, False),
                                       ("Add command descriptions in search", bool, False)]
                         )
        self.add_command(["h", "hist"], self.func_history, "Gets the command history since startup. Only unique and valid commands are logged.", self)
        self.add_command(["hc"], self.func_exec_history, "Executes the picked history command", self,
                         arg_spec_opt=[("The command number", int, "* the last command *")]
                         )
        self.add_command([""], self.func_exec_last, "Executes the last command again (even it is invalid)", self)
        # todo remove ;)
        self.add_command(["🤔"], lambda x: print("🤔"), "Repeats the thinking smiley", self)

    def set_config(self, config):
        self.config = config

    # this is the string before the prompt 'string'>
    def set_cli_prompt(self, cli_prompt):
        self.cli_prompt = cli_prompt

    # called when processor is started to display some welcome text
    def set_welcome_function(self, welcome_function, welcome_function_class):
        self.welcome_function = welcome_function
        self.welcome_function_class = welcome_function_class

    def is_changed(self):
        return self.changed

    def set_changed(self, changed):
        self.changed = changed

    def add_command(self, keys, a_function, help_text, calling_class=None, arg_spec=None, arg_spec_opt=None):
        """
        Adds a command to the processor.
        :param keys: list of strings it listens to
        :param a_function: the function that will be called when the command is executed. It must have defaults set for the optional arguments.
        :param help_text: the description of the command
        :param calling_class: the class reference used in calling the function (or None)
        :param arg_spec: the arguments the command requires (or None). This is a list of {'arg_description': <argument type>). Supports bool, int, float, string and Enum
        :param arg_spec_opt: the optional arguments for command (or None). Limitation: The N-th optional argument can only be given when the (N-1)-th optional argment is also provided. This is a list of ['arg_description', 'argument type', 'default value'). Supports bool, int, float, string and Enum
        """
        for key in keys:
            if key in self.commands:
                raise Exception(f"Command with key '{key}', new text: '{help_text}' is already in list of commands with current text: '{self.commands[key][2]}'")
            self.commands[key] = [calling_class, a_function, help_text, arg_spec, arg_spec_opt]

    def start(self):
        if 'cp_history' in self.config and self.config['cp_history']:
            self.last_execution = self.config['cp_history'][-1]
        self.shutdown_processor = False

        # start cli and processor
        self.processor_thread = threading.Thread(name='Command processor interpreter', target=self.command_interpreter)
        self.processor_thread.setDaemon(True)
        self.processor_thread.start()

        # start intervalled printer
        self.printer_thread = threading.Thread(name='Command processor printer', target=self.intervalled_printer)
        self.printer_thread.setDaemon(True)
        self.printer_thread.start()

    def command_interpreter(self):
        self.welcome_function(self.welcome_function_class)
        while not self.shutdown_processor:
            try:
                answer = self.accept_answer("")
                return_code = self.process_answer(answer)
                # is the answer is not empty, write command to last_executed variable
                if answer:
                    self.last_execution = answer
                # if no command-type error (-1) and unique, also add command to history
                # NOTE: also ignore if it is a history execution command: we do not want to recursive calling issues
                if return_code != -1 and answer not in self.config['cp_history'] \
                        and answer.split(" ")[0] not in ["", "hc", 'h', 'hist']:
                    if len(self.config['cp_history']) > 15:
                        del self.config['cp_history'][0]
                    self.config['cp_history'].append(answer)
                    self.set_changed(True)
            except EOFError as e:
                pass
                # program is killed, this is fine.
            time.sleep(0.2)

    def intervalled_printer(self):
        while not self.shutdown_processor:
            time.sleep(0.1)
            if self.print_queue.qsize() < 1:
                continue  # nothing to print
            if self.last_print_time > 0 and time.time() - self.last_print_time < self.print_timeout_max:
                continue  # wait till timeout passes
            if self.processing_input:
                continue  # already processing input
            time.sleep(0.1)
            # take time slot!
            self.printing_queued = True
            print("")
            for i in range(self.print_queue.qsize()):
                try:
                    print_item = self.print_queue.get()
                    self.print_formatted(print_item[0], print_item[1])
                except Queue.Empty as e:
                    pass  # we are done (prematurely?)
            self.print_prompt()  # restore overwritten cli
            self.printing_queued = False
            self.last_print_time = time.time()

        # print last items
        while self.processing_input:
            pass # wait for processor to stop
        for i in range(self.print_queue.qsize()):
            try:
                print_item = self.print_queue.get()
                self.print_formatted(print_item[0], print_item[1])
            except Queue.Empty as e:
                pass # we are done (prematurely?)

    def shutdown(self, do_print=True):
        if do_print:
            self.print_formatted("Stopping command processor..", utils.QType.INFORMATION)
        self.shutdown_processor = True
        if self.processing_input:
            # called by own handler so join would take forever!
            time.sleep(1)
            return
        if self.processor_thread:
            self.processor_thread.join(2)
        if self.printer_thread:
            self.printer_thread.join(2)
        if do_print:
            self.print_formatted("Done.", utils.QType.INFORMATION)

    # commands can contain at most one space
    def process_answer(self, answer):
        # dibs the printing flag
        while self.printing_queued:
            # wait for printing to be completed
            time.sleep(0.05)
        self.processing_input = True
        found = False
        success = False
        try:
            # go for it, first extract (quoted) arguments
            splitted_quoted = answer.strip().split("\"")
            if len(splitted_quoted) % 2 == 0:
                # not an even amount of quotes --> error
                raise AttributeError("Missing a closing quote!")
            splitted = []
            if len(splitted_quoted) > 1:
                for item in splitted_quoted:
                    if item.startswith(" ") or item.endswith(" "):
                        splitted.extend(item.strip().split(" "))
                    elif len(item) > 0:
                        splitted.append(item)
            else:
                splitted.extend(splitted_quoted[0].strip().split(" "))
            if not splitted:
                splitted.append("")

            found, success = False, False
            # first use all words as command words up to one word as command word and the others as arguments
            for i in range(len(splitted), 0, -1):
                # try i command words and the other words as arguments
                command = " ".join(splitted[:i])
                args = None
                if len(splitted) > i:
                    args = splitted[i:]
                found, success = self.process_command_and_args(command, args)
                if found:
                    break
            if not found:
                self.print_formatted(f"Unknown command: '{answer}'\n"
                                     f"\tType 'help' for a list of available commands",
                                     string_type=utils.QType.ERROR)
                command_matches = self.get_command_matches(self, splitted)
                if command_matches:
                    self.print_formatted(f"\tMaybe you meant one of the following commands: {command_matches}",
                                         string_type=utils.QType.BLUE)
        except Exception as e:
            # the process has thrown an uncaught errror! Show to user
            self.print_formatted("Command interpreter got exception: {}".format(e.__str__()), utils.QType.ERROR)
            traceback.print_tb(e.__traceback__)
        self.processing_input = False

        return_code = -1
        if found and success:
            return_code = None
        elif found:
            return_code = -2
        return return_code

    def process_command_and_args(self, command, args=None):
        found, success = False, True
        if command in self.commands:
            found = True
            item = self.commands[command]
            # if it required arguments verify that they are provided and are of the right type
            try:
                parsed_args = self.parse_args(args, item[3], item[4], command)
            except (AttributeError, TypeError) as e:
                e = str(e)
                if len(e) > 0:
                    self.print_formatted(str(e), string_type=utils.QType.ERROR)
                success = False
            else:
                if self.__len(args) > (self.__len(item[3]) + self.__len(item[4])):
                    self.print_formatted("Extraneous argument(s) ignored.", string_type=utils.QType.WARNING)
                # run with class reference if necessary
                if item[0]:
                    if parsed_args:
                        return_code = item[1](item[0], *parsed_args)
                    else:
                        return_code = item[1](item[0])
                else:
                    if parsed_args:
                        return_code = item[1](*parsed_args)
                    else:
                        return_code = item[1]()
                if return_code:
                    success = False
        else:
            success = False

        return found, success

    def raise_error_with_help(self, error_class, error_string, no_command_additions, command=None):
        if command is None:
            raise error_class(error_string + no_command_additions)
        else:
            self.print_formatted(error_string + "\n", utils.QType.ERROR)
            self.func_help(self, command, True)
            raise error_class()

    # supports: bool, int, float, string and enums
    def parse_args(self, args, arg_spec, arg_spec_opt, command=None):
        # count max and min number of required arguments
        min_args = self.__len(arg_spec)
        max_args = min_args + self.__len(arg_spec_opt)
        # if no arguments are required and no optional argument are provided, we return None
        if max_args == 0 or (min_args == 0 and not args):
            return None
        # if not enough arguments given, we return an error
        if min_args > 0 and self.__len(args) < min_args:
            error_string = f"Not enough arguments! \n\tOnly {self.__len(args)} of {min_args} provided."
            no_command_additions = ""
            if arg_spec:
                no_command_additions += f"\n\tRequired args: {len(arg_spec)} of type(s): {arg_spec}"
            if arg_spec_opt:
                no_command_additions += f"\n\tOptional args: {len(arg_spec_opt)} of type(s): {arg_spec_opt}"
            self.raise_error_with_help(AttributeError, error_string, no_command_additions, command)

        # arguments not required or given, return None as well
        if not args:
            return None

        parsed_args = []
        if not arg_spec:
            arg_spec = []
        if not arg_spec_opt:
            arg_spec_opt = []
        # parse required arguments
        for i, spec in enumerate(arg_spec):
            # check argument type and try to parse
            parse_failed, parsed_arg = self.parse_arg(args[i], spec)
            if parse_failed:
                error_string = f"Argument '{spec[0]}' index {i} of wrong or not-supported type! \n" \
                               f"\tGiven is '{args[i]}', but should be of type '{spec[1]}'"
                self.raise_error_with_help(TypeError, error_string, "", command)
            parsed_args.append(parsed_arg)

        # parse optional arguments and add to total list
        # NOTE: despite being optional, when provided, they must still be parse-able.
        i = len(parsed_args)
        for spec_opt in arg_spec_opt:
            if len(args) <= i:
                # no more optional arguments to parse
                break
            # check argument type and try to parse
            parse_failed, parsed_arg = self.parse_arg(args[i], spec_opt)
            if parse_failed:
                error_string = f"Argument '{spec_opt[0]}' index {i} of wrong or not-supported type! \n" \
                               f"\tGiven is '{args[i]}', but should be of type '{spec_opt[1]}'"
                self.raise_error_with_help(TypeError, error_string, "", command)
            parsed_args.append(parsed_arg)
            i += 1
        if len(parsed_args) == 0:
            parsed_args = None
        return parsed_args

    # supports: bool, int, float, string and enums
    @staticmethod
    def parse_arg(arg, spec):
        parsed_arg = None
        parse_failed = False
        if spec[1] == bool:
            if arg.lower() in ['true', 'false', 't', 'f']:
                parsed_arg = arg.lower() in ['true', 't']
            else:
                parse_failed = True
        elif spec[1] == int:
            try:
                parsed_arg = int(arg)
            except ValueError as e:
                parse_failed = True
        elif spec[1] == float:
            try:
                parsed_arg = float(arg)
            except ValueError as e:
                parse_failed = True
        elif spec[1] == str:
            parsed_arg = arg
        elif issubclass(spec[1], Enum):
            try:
                parsed_arg = spec[1][arg.upper()].value
            except KeyError as e:
                parse_failed = True
            # raise TypeError("Argument '{}' index {} of a non-supported specifier type '{}'!".format(spec[0], i, spec[1]))
        return parse_failed, parsed_arg

    @staticmethod
    def __len(item):
        if item:
            return len(item)
        else:
            return 0

    # only use internally!
    def _get_cli_string(self):
        return utils.color_string(self.cli_prompt + "> ", utils.Color.GREEN, self.config['colored_output'])

    # print prompt, newline, go prompt-length to the right and one line up
    def print_prompt(self):
        print(f"{self._get_cli_string()}\n\033[1A\033[{len(self.cli_prompt)+2}C", end='')

    # prompts for a question and returns the result (only call from a command handler function)
    def accept_answer(self, question=None, string_type=utils.QType.NONE):
        if question:
            self.print_formatted(question, string_type)
        self.print_prompt()
        return sys.stdin.readline().strip()

    # prompts for a question and returns the result of given type (only call from a command handler function)
    def accept_of_type(self, question, answer_type, string_type=utils.QType.NONE):
        valid = False
        answer = None
        while not valid:
            answer = self.accept_answer(f"{question} [{answer_type}]", string_type)
            try:
                answer = answer_type(answer)
                valid = True
            except TypeError as e:
                self.print_formatted("Invalid answer '{}', must be of type '{}'".format(answer, answer_type), utils.QType.ERROR)
        return answer

    # prompts for a yes / no question and returns the result bool (only call from a command handler function)
    def accept_yes_no(self, question, string_type=utils.QType.NONE):
        valid = False
        answer = None
        while not valid:
            answer = self.accept_answer(question + " [y(es)/n(o)]", string_type).lower()
            if answer == "y" or answer == "yes":
                valid = True
                answer = True
            elif answer == "n" or answer == "no":
                valid = True
                answer = False
            else:
                self.print_formatted("Invalid answer '{}'".format(answer), utils.QType.ERROR)
        return answer

    @staticmethod
    def print_single_help_command(self, message, keys):
        comms = sorted([item[0] for item in keys], key=lambda x: len(x))
        args = keys[0][1]
        args_opt = keys[0][2]
        self.print_formatted(f"{comms}:", utils.QType.BOLD)
        self.print_formatted(f"\tDescription:    {utils.tabbed_string(message, 5, True)}")
        if args:
            if len(args) == 1:
                self.print_formatted("\tRequired arg:   {}".format(str(args[0])), utils.QType.RED)
            else:
                self.print_formatted(f"\tRequired args:  {utils.tabbed_pprint_string(args, 5, True)}",
                                     utils.QType.RED)
        if args_opt:
            if len(args_opt) == 1:
                self.print_formatted("\tOptional arg:   {}".format(str(args_opt[0])), utils.QType.BLUE)
            else:
                self.print_formatted(f"\tOptional args:  {utils.tabbed_pprint_string(args_opt, 5, True)}",
                                     utils.QType.BLUE)

    @staticmethod
    def get_command_matches(self, search_queries, exact_match=False, text_search=False):
        search_matches = []
        # search for a key which contains the query or the other way around
        # only when the search is at most one character, a single or empty command can be matched
        # if exact_match is true, text_search is ignored and only up to one matching command is returned
        # if text_search is true (and exact_match is false), the description of commands is also searched
        for key in self.commands.keys():
            for query in search_queries:
                if query in search_matches:
                    continue
                if exact_match and key == query:
                    search_matches.append(key)
                elif (len(key) > 1 or len(query) <= 1) and key in query or query in key:
                    search_matches.append(key)
                elif text_search and len(query) > 2 and query in self.commands[key][2]:
                    search_matches.append(key)
        return search_matches

    @staticmethod
    def func_help(self, search_query=None, exact_match=False, text_search=False):
        search_matches = []
        if search_query is not None:
            if exact_match:
                search_matches = [search_query]
                self.print_formatted("The matching help command:", utils.QType.INFORMATION)
            else:
                search_matches = self.get_command_matches(self, search_query.split(" "), exact_match, text_search)
                if search_matches:
                    self.print_formatted("List of all matching commands:", utils.QType.INFORMATION)
                else:
                    self.print_formatted("No matches to help search query.\n\tUse 'help' to list all commands.", utils.QType.INFORMATION)
        if not search_matches:
            if search_query:
                return
            self.print_formatted("List of all available commands:", utils.QType.INFORMATION)
        seen_messages = {}
        for key, value in sorted(self.commands.items(), key=lambda x: (x[1][2], x[0])):
            if search_matches:
                if key not in search_matches:
                    # skip it
                    continue
            if value[2] in seen_messages:
                seen_messages[value[2]].append((key, value[3], value[4]))
            else:
                seen_messages[value[2]] = [(key, value[3], value[4])]
        last_first_char = ""
        for message, keys in sorted(seen_messages.items(), key=lambda x: (x[1][0][0], x[0])):
            first_key_part = keys[0][0].split(" ")[0]
            if first_key_part != last_first_char:
                last_first_char = first_key_part
                self.print_formatted("")
            self.print_single_help_command(self, message, keys)

    @staticmethod
    def func_history(self):
        self.print_formatted("All history items:", utils.QType.INFORMATION)
        if not self.config['cp_history']:
            self.print_formatted("Empty.", utils.QType.BOLD)
        for i, item in enumerate(self.config['cp_history']):
            self.print_formatted(f"\t{i}:\t{item}", utils.QType.BOLD)

    @staticmethod
    def func_exec_history(self, history_id=None):
        if history_id is None:
            if not self.config['cp_history']:
                self.print_formatted(f"No command to execute: history is empty.", utils.QType.ERROR)
                return -1
            history_id = -1
        if history_id < -len(self.config['cp_history']) or history_id > len(self.config['cp_history']) - 1:
            self.print_formatted(f"Invalid history item index: {history_id}", utils.QType.ERROR)
            return -1
        comm = self.config['cp_history'][history_id]
        self.print_formatted(f"Executing command: '{comm}'", utils.QType.INFORMATION)
        return self.process_answer(comm)

    @staticmethod
    def func_exec_last(self):
        if not self.last_execution:
            self.print_formatted(f"No last command to re-execute: this is the first command since startup.", utils.QType.ERROR)
            return -1
        self.print_formatted(f"Executing command: '{self.last_execution}'", utils.QType.INFORMATION)
        return self.process_answer(self.last_execution)

    def print_queued(self, text, string_type=utils.QType.NONE):
        # if not started or stopped, we can just print it
        if self.shutdown_processor:
            self.print_formatted(text, string_type)
        else:
            self.print_queue.put((text, string_type))

    # Not to be called outside class! (use print_queued() instead)
    # If it is called outside the class, make sure no other thread is printing anything
    def print_formatted(self, text, string_type=utils.QType.NONE):
        utils.print_formatted(text, string_type, colored_output=self.config['colored_output'])

