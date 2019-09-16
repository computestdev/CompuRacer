#!/usr/bin/env python3
"""
The utility file contains all kinds of printing, formatting and comparison functions.
"""

# --- All imports --- #
from __future__ import print_function

import asyncio
import copy
import difflib
import json
import os
import pprint
import random
import re
import string
import sys
import time
from collections import defaultdict
from shutil import copyfile
from sys import platform

from lxml import etree
from tabulate import tabulate

d = difflib.Differ()


# -------------- Request & response help functions -------------- #
def sort_requests(the_requests, sort_key, sort_ascending):
    return sorted(the_requests.values(), key=sort_key, reverse=not sort_ascending)


def get_req_list(req, ignored_fields=None):
    res_list = []
    fields_to_print = ['id', 'timestamp', 'method', 'url', 'body']
    if ignored_fields:
        for item in ignored_fields:
            if item in fields_to_print:
                fields_to_print.remove(item)
    for field in fields_to_print:
        if field is not 'body':
            res_list.append(req[field])
        else:
            res_list.append(len(req[field]))
    return res_list


def get_req_string(req, basic=True, ignored_fields=None):
    if basic:
        fields_to_print = ['id', 'timestamp', 'method', 'url', 'body']
        if ignored_fields:
            for item in ignored_fields:
                if item in fields_to_print:
                    fields_to_print.remove(item)
        req_string = ""
        for field in fields_to_print:
            if field is not 'body':
                addition = ""
                if field is 'method' and len(req[field]) < 5:
                    addition = " " * (5 - len(req[field]))
                req_string += f"{req[field]}{addition}\t"
            elif 'body' in req and len(req['body']) > 0:
                the_body = str(req['body']).replace('\n', ' ').replace('\r', '')
                end = min(100, len(the_body))
                req_string += "body: {}".format(the_body[:end])
        return req_string
    else:
        return pprint.pformat(req)


def print_request_table(the_requests):
    if len(the_requests) == 0:
        return "Empty"
    contents = []
    for req_item in the_requests:
        contents.append(get_req_list(req_item))
    output = tabulate(contents, ["ID", "Timestamp", "Method", "URL", "Body Length"], showindex="always", tablefmt="simple") + "\n"
    return output


# used for sorting a combination of int codes and strings (Errors and Total)
def compare_table_fields(var):
    if var == "Total":
        return sys.maxsize
    if type(var[0]) is not int:
        return sys.maxsize - 1
    else:
        return var[0]


def get_res_spec_tables(results_of_one_request, aggr_dict):
    tables = ""
    for field_name in aggr_dict:
        # v  may be None, or (function, subname, units) i.e.: (lambda x: len(x), 'length', 'bytes')
        aggr = aggr_dict[field_name]
        tables += get_res_spec_table(results_of_one_request, field_name, aggr) + "\n"
    return tables


def get_res_spec_table(results_of_one_request, field_name, aggr=None):
    item_instances = defaultdict(int)
    for res in results_of_one_request:
        if type(res) is str:
            item_instances['Errors'] += 1
        else:
            if aggr is not None:
                key = aggr[0](res[field_name])
            else:
                key = res[field_name]
            item_instances[key] += 1
    if len(item_instances.items()) > 1:
        item_instances['Total'] = len(results_of_one_request)
    contents = [[item_instance, number] for item_instance, number in sorted(item_instances.items(), key=lambda x: compare_table_fields(x))]
    col_name = (field_name[0].upper() + field_name[1:]).replace("_", " ")
    if aggr is not None:
        if len(aggr) > 1 and aggr[1] is not None:
            col_name += f" {aggr[1]}"
        if len(aggr) > 2 and aggr[2] is not None:
            col_name += f" ({aggr[2]})"
    output = tabulate(contents, [col_name, "Amount"], tablefmt="simple", stralign='right') + "\n"
    return output


def side_by_side_tables(tables_string, num_rows):
    tables = tables_string.split("\t\n")


def key_value_or_none(dict, key):
    if key in dict:
        return dict[key]
    else:
        return None


# checks the values of the key in two dicts.
# Note: the key will be present in at least one of the dicts (or it is skipped),
#       so the compare function must be able to handle None values
def compare_two_items(comparison, key, dict_1, dict_2, custom_comparing=None):
    key_1 = key_value_or_none(dict_1, key)
    key_2 = key_value_or_none(dict_2, key)
    if key_1 is None and key_2 is None:
        # the key is not in either of the dicts --> skip
        pass
    elif custom_comparing and key in custom_comparing['ignore']:
        # the key is ignored --> always match
        comparison['match']['ignore'][key] = [key_1, key_2]
    elif custom_comparing and key in custom_comparing['compare']:
        # custom comparing applies --> run this function
        if custom_comparing['compare'][key](key_1, key_2):
            comparison['match']['custom'][key] = [key_1, key_2]
        else:
            comparison['fail']['custom'][key] = [key_1, key_2]
    elif key_1 is not None and key_2 is not None:
        if key_1 == key_2:
            comparison['match']['normal'][key] = [key_1, key_2]
        else:
            comparison['fail']['normal'][key] = [key_1, key_2]
    else:
        # key is not present in one of the dicts and no custom comparing applies --> never match
        comparison['fail']['missing'][key] = [key_1, key_2]


def compare_headers(comparison, header_1, header_2, custom_comparing=None):
    # compare all headers
    for key in set(header_1.keys()).union(set(header_2.keys())):
        compare_two_items(comparison, key, header_1, header_2, custom_comparing)
    return comparison


# all keys must be in the dict
def merge_subdicts(dict, keys):
    merged = {}
    for key in keys:
        for sub_key in dict[key].keys():
            merged[sub_key] = dict[key][sub_key]
    return merged


# ignores timestamp and id - fields
def compare_requests(req_1, req_2, custom_comparing=None, add_totals=True):
    # create comparison dict
    comparison = dict()
    comparison['match'] = {'normal': {}, 'custom': {}, 'ignore': {}}
    comparison['fail'] = {'normal': {}, 'custom': {}, 'missing': {}}

    # compare the request parts
    for key in ['url', 'method', 'body']:
        compare_two_items(comparison, key, req_1, req_2, custom_comparing)

    # compare the headers
    compare_headers(comparison, req_1['headers'], req_2['headers'], custom_comparing)

    # compute totals
    if add_totals:
        comparison['match']['total'] = merge_subdicts(comparison['match'], comparison['match'].keys())
        comparison['fail']['total'] = merge_subdicts(comparison['fail'], comparison['fail'].keys())
        comparison['total'] = {**comparison['match']['total'], **comparison['fail']['total']}
    return comparison


# ignores wait_time, index, send_time and response_time - fields
def compare_responses(res_1, res_2, custom_comparing=None, add_totals=True):
    # create comparison dict
    comparison = dict()
    comparison['match'] = {'normal': {}, 'custom': {}, 'ignore': {}}
    comparison['fail'] = {'normal': {}, 'custom': {}, 'missing': {}}

    # compare the request parts
    for key in res_1.keys(): #['status_code', 'body', 'body_length', 'headers_length']:
        if key in ['wait_time', 'index', 'send_time', 'response_time', 'send_index', 'wait_time', 'headers']:
            continue  # skip these keys
        compare_two_items(comparison, key, res_1, res_2, custom_comparing)

    # compare the headers
    compare_headers(comparison, res_1['headers'], res_2['headers'], custom_comparing)

    # compute totals
    if add_totals:
        comparison['match']['total'] = merge_subdicts(comparison['match'], comparison['match'].keys())
        comparison['fail']['total'] = merge_subdicts(comparison['fail'], comparison['fail'].keys())
        comparison['total'] = {**comparison['match']['total'], **comparison['fail']['total']}
    return comparison


# custom comparing should contain an 'ignore' key and a 'compare' key
def get_grouped_responses(responses, custom_comparing=None):
    results = {'groups': [], 'stats': {}}
    for response in responses:
        comparisons = []
        matched = False
        for i, group in enumerate(results['groups']):
            # compare to first in the group. Match --> add to this group
            comparisons.append(compare_responses(group['responses'][0], response, custom_comparing))
            if not comparisons[-1]['fail']['total']:
                # it is a match!
                matched = True
                break
        if not matched:
            # create new group
            results['groups'].append({'responses': [response], 'comparisons': [comparisons]})
        else:
            # add to existing group
            results['groups'][len(comparisons) - 1]['responses'].append(response)
            results['groups'][len(comparisons) - 1]['comparisons'].append(comparisons)

    # calculate stats
    results['stats']['never_match'] = []
    results['stats']['always_match'] = []
    if len(results['groups']) > 1:
        results['stats']['never_match'] = [comparison[0]['fail']['total'].keys() for comparison in [group['comparisons'][0] for group in results['groups'][1:]]]
        if results['stats']['never_match']:
            results['stats']['never_match'] = list(set.intersection(*map(set, results['stats']['never_match'])))

        results['stats']['always_match'] = [comparison[0]['match']['total'].keys() for comparison in [group['comparisons'][0] for group in results['groups'][1:]]]
        if results['stats']['always_match']:
            results['stats']['always_match'] = list(set.intersection(*map(set, results['stats']['always_match'])))

    if len(results['groups']) > 1:
        results['stats']['ignored'] = list(set.intersection(
            *map(set, [results['stats']['always_match'], custom_comparing['ignore']])
        ))
        # recalculate the always match to exclude the ignored fields
        results['stats']['always_match'] = list(set.difference(
            *map(set, [results['stats']['always_match'], results['stats']['ignored']])
        ))
    else:
        results['stats']['ignored'] = custom_comparing['ignore']

    # order groups by number of differences from first group
    # the responses in the groups have the same ordering as the given response-order
    diffs = []
    for i, group in enumerate(results['groups']):
        if group['comparisons'][0]:
            diffs.append(len(group['comparisons'][0][0]['fail']['total']))
            # only keeps the differences from the group with the first group for print coloring later
            results['groups'][i]['comparisons'] = list(group['comparisons'][0][0]['fail']['normal'].keys()) + \
                                                  list(group['comparisons'][0][0]['fail']['missing'].keys())
        else:
            diffs.append(0)
            results['groups'][i]['comparisons'] = []
    results['groups'] = [item[1] for item in sorted(list(zip(diffs, results['groups'])), key=lambda x: (x[0], len(x[1]['responses'][0]['body'])))]

    return results


# NOTE: if a result contains more than 25 lines, it is split in 25 line parts for readability
def perform_string_compare_on_items(key, tuple_of_values, split_up_size=0):
    items_copy = [tuple_of_values[0], tuple_of_values[1]]
    if type(items_copy[0]) is dict:
        items_copy[0] = format_json(items_copy[0])
    if type(items_copy[1]) is dict:
        items_copy[1] = format_json(items_copy[1])

    # compare it using difflib
    parts_0 = str(items_copy[0]).split('\n')
    parts_1 = str(items_copy[1]).split('\n')
    diff = d.compare(parts_0, parts_1)
    new_items = '\n'.join(diff)
    # remove excessive newlines
    changed = True
    while changed:
        newer_items = new_items.replace("\n\n", "\n")
        changed = new_items != newer_items
        new_items = newer_items

    if split_up_size > 0:
        # split it up when multiple lines are found
        item_lines = new_items.split("\n")
        results = {}
        if len(item_lines) > split_up_size:
            for i in range(0, len(item_lines), split_up_size):
                the_max = min(len(item_lines), i + split_up_size)
                part = tabbed_string("\n" + "\n".join(item_lines[i:the_max]), 2)
                # only use part when it contains differences
                has_differences = re.search(re.compile(r"\n\t*\-"), part) or re.search(re.compile(r"\n\t*\+"), part)
                if has_differences:
                    results[f"{key}_lines_{i + 1}_to_{the_max}"] = part
            results[key] = "See splitted parts"
            return copy.deepcopy(results)
    return {key: new_items}


# it only checks the differences of multiline results in fail normal and fail custom.
# ither parts are equal or missing so cannot be compared
def perform_string_compare_on_results(compare_results, split_up_size=0):
    if 'fail' not in compare_results:
        return compare_results
    if 'normal' in compare_results['fail']:
        for key in copy.copy(list(compare_results['fail']['normal'].keys())):
            compare_results['fail']['normal'] = {**compare_results['fail']['normal'],
                                                 **perform_string_compare_on_items(key, compare_results['fail']['normal'][key], split_up_size)}
    if 'custom' in compare_results['fail']:
        for key in copy.copy(list(compare_results['fail']['custom'].keys())):
            compare_results['fail']['custom'] = {**compare_results['fail']['custom'],
                                                 **perform_string_compare_on_items(key, compare_results['fail']['custom'][key], split_up_size)}
    return compare_results


# -------------- General request functions -------------- #


# limits the race of access of the callback function to avoid overuse.
class RateLimiter:
    rate = None # messages
    per = None # seconds
    callback = None

    allowance = None  # unit: messages
    last_check = None # floating-point, e.g. usec accuracy. Unit: seconds

    lock = asyncio.Lock()

    def __init__(self, rate=100.0, per=60.0, callback=None):
        self.rate = rate
        self.per = per
        self.callback = callback

        self.allowance = rate
        self.last_check = time.time()

    # not thread safe!
    def __rate_limiter(self):
        current = time.time()
        time_passed = current - self.last_check
        self.last_check = current
        self.allowance += time_passed * (self.rate / self.per)
        if self.allowance > self.rate:
            self.allowance = self.rate  # throttle
        if self.allowance < 1.0:
            return False
        else:
            self.allowance -= 1.0
            return True

    def __rate_limiter_thread_safe(self):
        with self.lock:
            return self.__rate_limiter()

    async def __rate_limiter_async_thread_safe(self):
        async with self.lock:
            return self.__rate_limiter()

    # wait until a message can be send
    def send_message_blocking(self, message):
        while not self.__rate_limiter():
            pass
        return self.callback(message)

    # returns False if it is blocked
    async def send_message(self, message):
        if not await self.__rate_limiter_async_thread_safe():
            return False
        return self.callback(message)


def header_check(header, field, value, exact_match):
    if field not in header:
        return False
    if exact_match:
        return header[field] == value
    else:
        return value in header[field]


def body_check(body, xpath, num_matches=1):
    tree = etree.HTML(body)
    result = tree.xpath(xpath)
    return result and len(result) >= num_matches


def randomword(length):
   return ''.join(random.choice(string.ascii_lowercase) for i in range(length))


def random_user_credentials(num_credentials, min_length):
    assert num_credentials > 0
    assert min_length > 0
    credentials = []
    for i in range(num_credentials):
        rnd_part = randomword(min_length)
        credentials.append({'username': f"USER-{i}-{rnd_part}", 'password': f"PASS-{i}-{rnd_part}"})
    return credentials


# -------------- Print and file storage helper functions -------------- #

def tabbed_string(a_string, num_tabs=0, no_start_tabs=False):
    tabs = "".join(["\t"] * num_tabs)
    a_tabbed_string = ""
    if not no_start_tabs:
        a_tabbed_string = tabs
    if a_string.endswith("\n"):
        a_tabbed_string += a_string.replace("\n", "\n" + tabs)[:-len(tabs)]
    else:
        a_tabbed_string += a_string.replace("\n", "\n" + tabs)
    return a_tabbed_string


def tabbed_pprint_string(an_object, num_tabs=0, no_start_tabs=False):
    return tabbed_string(pprint.pformat(an_object), num_tabs, no_start_tabs)


def use_items(a_dict, item_names):
    items = list()
    for item_name in item_names:
        if item_name in a_dict and a_dict[item_name]:
            items.append({item_name: a_dict[item_name]})
    return items


def tabbed_pprint_request(a_request, num_tabs=0):
    items = use_items(a_request, ['id',
                                  'timestamp',
                                  'method',
                                  'url',
                                  'headers',
                                  'body'])
    return "\n".join([tabbed_pprint_string(item, num_tabs) for item in items])


def tabbed_pprint_representative(a_representative, num_tabs, items_to_skip, small=False):
    if not small:
        items = use_items(a_representative, ['send_time',
                                            'send_time_min',
                                            'send_time_max',
                                            'response_time',
                                            'response_time_min',
                                            'response_time_max',
                                            'status_code',
                                            'headers_length',
                                            'headers',
                                            'body_length',
                                            'body'])
    else:
        items = use_items(a_representative, ['status_code',
                                             'body'])
    return "\n".join([tabbed_pprint_string(item, num_tabs) for item in items])

def tabbed_pprint_response(a_response, num_tabs):
    items = use_items(a_response, ['wait_time',
                                   'send_index',
                                   'send_time',
                                   'response_time',
                                   'status_code',
                                   'headers_length',
                                   'headers',
                                   'body_length',
                                   'body'])
    return "\n".join([tabbed_pprint_string(item, num_tabs) for item in items])


class Color:
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    DARKCYAN = '\033[36m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


class QType:
    NONE = ""
    INFORMATION = "INFO: "
    WARNING = "WARN: "
    ERROR = "ERROR: "

    PURPLE = Color.PURPLE
    CYAN = Color.CYAN
    DARKCYAN = Color.DARKCYAN
    BLUE = Color.BLUE
    GREEN = Color.GREEN
    YELLOW = Color.YELLOW
    RED = Color.RED
    BOLD = Color.BOLD
    UNDERLINE = Color.UNDERLINE
    END = Color.END


def is_msg_type(type_string):
    return type_string == QType.NONE or type_string == QType.INFORMATION or type_string == QType.WARNING or type_string == QType.ERROR


def is_color(type_string):
    return not is_msg_type(type_string)


def clear_output(new_line=True):
    if platform == 'Windows':
        os.system('cls')
    else:
        os.system('clear')
    if new_line:
        print()


# only to be used in utils class (not from outside!) use format_string instead
def color_string(text, a_color=Color.END, colored_output=False):
    if colored_output and a_color != Color.END:
        return a_color + text + Color.END
    else:
        return text


def format_string(text, string_type=QType.NONE, colored_output=False):
    if not text:
        text = ""
    if is_msg_type(string_type):
        if string_type == QType.NONE:
            color_pick = Color.END
        elif string_type == QType.INFORMATION:
            color_pick = Color.CYAN
        elif string_type == QType.WARNING:
            color_pick = Color.YELLOW
        elif string_type == QType.ERROR:
            color_pick = Color.RED
        else:
            raise TypeError("String type unknown: " + string_type)
        return color_string(string_type + text, color_pick, colored_output)
    else:
        return color_string(text, string_type, colored_output)


def get_arg(string_args, index):
    index = str(index)
    if index in string_args:
        return string_args[index]
    else:
        return None


def print_formatted(text, string_type=QType.NONE, colored_output=False):
    print(format_string(text, string_type, colored_output))


# not used..
def print_colored(text, a_color=None):
    if not a_color:
        strn = color_string(text, colored_output=True)
    else:
        strn = color_string(text, a_color, colored_output=True)
    print(strn)


# Python dict to JSON string
def format_json(data):
    # If we don't sort keys, we can't guarantee repeatability!
    return json.dumps(data, indent=4, sort_keys=True)


# JSON string to Python dict
def read_json(data):
    return json.loads(data)


# do not use
def load_json_file_cp(command_processor, path, backup_path=None, msg=None, password=None):
    if msg:
        command_processor.print_queued(msg, QType.INFORMATION)
    if password:
        '''
        cipher = AESCipher(password)
        with open(path, "r") as json_file:
            try:
                json_file = json.loads(cipher.decrypt(json_file.readline()))
            except (ValueError, TypeError) as err:
                # password is invalid or file is malformed
                return None
        '''
        raise NotImplementedError("Not implemented!")
    else:
        try:
            json_file = json.load(open(path))
        except ValueError as err:
            # The file is malformed or encrypted
            return None
    if msg:
        command_processor.print_queued("Done.", QType.INFORMATION)
    return json_file


def load_json_file(path, backup_path=None, msg=None, colored_output=False):
    # backup still here, must have crashed last time: restore it.
    if backup_path and os.path.exists(backup_path):
        print_formatted("Restoring state from corruption..", QType.WARNING, colored_output)
        if os.path.exists(path):
            os.remove(path)
        copyfile(backup_path, path)
        os.remove(backup_path)
    if msg:
        print_formatted(msg, QType.INFORMATION, colored_output)
    try:
        json_file = json.load(open(path))
    except ValueError as err:
        # The file is malformed or encrypted
        print_formatted(f"Corrupted settings file!\n{err}", QType.ERROR, colored_output)
        return None
    if msg:
        print_formatted("Done.", QType.INFORMATION, colored_output)
    return json_file


# do not use
def store_json_file_cp(command_processor, json_path, json_data, backup_path=None, msg=None, password=None):
    if msg:
        command_processor.print_queued(msg, QType.INFORMATION)
    # load file
    with open(json_path, "w") as json_file:
        if password:
            '''
            cipher = AESCipher(password)
            encrypted = cipher.encrypt(json.dumps(json_data))
            json_file.write(encrypted)
            '''
            raise NotImplementedError("Not implemented!")
        else:
            json.dump(json_data, json_file, indent=4)
    if msg:
        command_processor.print_queued("Done.", QType.INFORMATION)


def store_json_file(json_path, json_data, backup_path=None, msg=None, colored_output=False):
    # create a copy in case json serialization fails and the file gets corrupted
    if backup_path and os.path.exists(json_path):
        copyfile(json_path, backup_path)
    # store json to file
    if msg:
        print_formatted(msg, QType.INFORMATION, colored_output)
    with open(json_path, "w") as json_file:
        json_string = json.dumps(json_data, indent=4)
        json_file.write(json_string)
    if msg:
        print_formatted("Done.", QType.INFORMATION, colored_output)
    # storing successful, remove the backup
    if backup_path and os.path.exists(backup_path):
        os.remove(backup_path)