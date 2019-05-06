#!/usr/bin/env python3
"""
The Batch class that is used for holding batch send and response info.
It is able to aggregate results and keep track of whether it is changed.
"""

# --- All imports --- #
import copy
import time
from datetime import datetime
import os
import pprint
from bs4 import BeautifulSoup

import src.utils as utils
from tqdm import tqdm


class Batch:
    # {(req_id, wait_time): (dup_v, dup_h)}
    # req_id = the id of the request that is used
    # wait_time = time in ms between condition.wait() and actual start,
    # dup_v = parallel duplicates, dup_h = sequential duplicates
    name = None
    allow_redirects = False
    small = False
    items = {}
    results = {}

    rendered_file_dir = None

    default_custom_comparing = {
        'ignore': ['Date', 'Expires', 'X-Debug-Token', 'X-Debug-Token-Link'],
        'compare': {}
    }

    def __init__(self, name, rendered_file_dir, allow_redirects=False, sync_last_byte=False, custom_comparing=None):
        self.name = name
        self.changed = False
        self.allow_redirects = allow_redirects
        self.sync_last_byte = sync_last_byte
        self.items = {}
        self.results = {}
        self.rendered_file_dir = rendered_file_dir
        if custom_comparing is None:
            self.custom_comparing = self.default_custom_comparing
        else:
            self.custom_comparing = custom_comparing

    @staticmethod
    def create_from_dict(a_dict, rendered_file_dir):
        if 'custom_comparing' not in a_dict:
            a_dict['custom_comparing'] = Batch.default_custom_comparing
        if 'sync_last_byte' not in a_dict:
            a_dict['sync_last_byte'] = False
        a_batch = Batch(name=a_dict['name'],
                        rendered_file_dir=rendered_file_dir,
                        allow_redirects=a_dict['allow_redirects'],
                        sync_last_byte=a_dict['sync_last_byte'],
                        custom_comparing=a_dict['custom_comparing'])
        a_batch.results = a_dict['results']
        # convert older saves of results
        if 'results' in a_batch.results:
            a_batch.results['contents'] = a_batch.results['results']
            del a_batch.results['results']
        # convert item key-values
        a_batch.items = {}
        for item in a_dict['items']:
            a_batch.items[tuple(item['key'])] = item['value']
        # the files might have been removed in the meantime, so re-create them
        a_batch.redo_all_grouping()
        return a_batch

    def get_as_dict(self):
        listed_items = []
        for key in self.items.keys():
            listed_items.append({'key': key, 'value': self.items[key]})
        return {'name': self.name,
                'allow_redirects': self.allow_redirects,
                'sync_last_byte': self.sync_last_byte,
                'custom_comparing': self.custom_comparing,
                'items': listed_items,
                'results': self.results}

    def set_name(self, name):
        self.changed = True
        self.name = name

    def set_allow_redirects(self, allow_redirects):
        self.changed = True
        self.allow_redirects = allow_redirects

    def set_sync_last_byte(self, sync_last_byte):
        self.changed = True
        self.sync_last_byte = sync_last_byte

    def get_ignored_fields(self):
        return self.custom_comparing['ignore']

    def add_ignored_field(self, field_name):
        if field_name in self.custom_comparing['ignore']:
            return -1
        self.custom_comparing['ignore'].append(field_name)
        self.redo_all_grouping(force=True)

    def reset_ignored_fields(self):
        if self.custom_comparing['ignore'] == self.default_custom_comparing['ignore']:
            return -1
        self.custom_comparing['ignore'] = self.default_custom_comparing['ignore']
        self.redo_all_grouping(force=True)

    def check_missing_files(self, specified_content_id=None):
        if self.has_results():
            for content_id in self.results['contents'].keys():
                if specified_content_id is not None and specified_content_id != content_id:
                    # we only check the files of a certain content id
                    continue
                if type(self.results['contents'][content_id]) is not dict:
                    continue
                for path in self.results['contents'][content_id]['files']:
                    if not os.path.exists(path):
                        return True
        return False

    def remove_the_files(self, specified_content_id=None):
        if not self.has_results():
            return
        for content_id in self.results['contents'].keys():
            if specified_content_id is not None and specified_content_id != content_id:
                # we only remove the files of a certain content id
                continue
            if type(self.results['contents'][content_id]) is not dict:
                continue
            for path in self.results['contents'][content_id]['files']:
                if os.path.exists(path):
                    self.changed = True
                    os.remove(path)

    # checks whether there are results
    # checks whether the request id is actually in the results, else it returns
    def clear_results(self, request_id=None, wait_time=None):
        if not self.has_results():
            # nothing to do
            return
        self.changed = True
        if request_id is None:
            # clears all results
            self.overwrite_results({})
            return
        if request_id not in self.results['contents']:
            # id has no results yet, nothing to do
            return
        if wait_time is None:
            # clears all results of one request_id
            # remove current HTML files if any
            self.remove_the_files(request_id)
            # remove the results
            self.results['contents'].pop(request_id, None)
        else:
            # clears all results of one request_id and wait_time
            # difficult to do by hand, so just undo grouping, remove these results and re-do grouping
            self.undo_grouping(request_id)
            for result in copy.copy(self.results['contents'][request_id]):
                if result['wait_time'] == wait_time:
                    self.results['contents'][request_id].remove(result)
            self.do_grouping(request_id, {'ignore': []})

    def overwrite_results(self, results, custom_comparing=None):
        self.changed = True

        # remove current HTML files if any
        self.remove_the_files()

        # overwrite old results
        self.results = results

        # recalculate groups and stats
        return self.redo_all_grouping(custom_comparing, True)

    def add(self, req_id, wait_time=0, dup_par=1, dup_seq=1, overwrite=False):
        if wait_time < 0:
            raise Exception(f"Add req wait_time must be zero or more, but is: {wait_time}")
        if dup_par <= 0:
            raise Exception(f"Add req dup_par must be positive, but is: {dup_par}")
        if dup_seq <= 0:
            raise Exception(f"Add req dup_seq must be positive, but is: {dup_seq}")
        if not overwrite and (req_id, wait_time) in self.items:
            raise Exception(f"Add req duplicate key: {(req_id, wait_time)}, " +
                            f"Curr: {self.items[(req_id, wait_time)]}, New: {(dup_par, dup_seq)}")
        self.changed = True
        self.items[(req_id, wait_time)] = [dup_par, dup_seq]

    def get(self, req_id, wait_time=None):
        if wait_time is None:
            result = {}
            for key in self.items.keys():
                if key[0] == req_id:
                    result[key[1]] = self.items[key]
            return result
        if (req_id, wait_time) in self.items:
            return self.items[(req_id, wait_time)]
        else:
            return None

    def get_reqs(self):
        reqs = []
        for item in self.items.keys():
            if item[0] not in reqs:
                reqs.append(item[0])
        return reqs

    def update_ids(self, old_new_ids):
        # check whether we need to do any updates
        matches = set(old_new_ids.keys()).intersection(set(self.get_reqs()))
        if not matches:
            # we do not need to change anything -> return
            return

        # update the items
        new_items = {}
        for res_id, wait_time in self.items.keys():
            if res_id in old_new_ids.keys():
                self.changed = True
                new_id = old_new_ids[res_id]
                new_items[(new_id, wait_time)] = self.items[(res_id, wait_time)]
            else:
                new_items[(res_id, wait_time)] = self.items[(res_id, wait_time)]
        self.items = new_items

        # update the results
        if self.has_results():
            new_results = {}
            for res_id in self.results['contents'].keys():
                if res_id in old_new_ids.keys():
                    self.changed = True
                    new_id = old_new_ids[res_id]
                    new_results[new_id] = self.results['contents'][res_id]
                else:
                    new_results[res_id] = self.results['contents'][res_id]
            self.results['contents'] = new_results

    def is_empty(self):
        return len(self.items) == 0

    # removes requests: exact match if req_id and wait_time is not None otherwise removes all matches
    def remove(self, req_id=None, wait_time=None):
        if req_id is None:
            # remove all results
            self.clear_results()
            # remove all requests
            num_removed = len(self.items)
            self.items = {}
        elif wait_time is None:
            # remove all requests with this req_id
            self.clear_results(req_id)
            # the remove from items
            num_removed = 0
            for key in copy.deepcopy(list(self.items.keys())):
                if key[0] == req_id:
                    del self.items[key]
                    num_removed += 1
            return num_removed
        elif (req_id, wait_time) in self.items:
            num_removed = 1
            # remove all requests with this req_id and wait_time
            self.clear_results(req_id, wait_time)
            del self.items[(req_id, wait_time)]
        else:
            raise Exception(f"Removal of unknown key: {(req_id, wait_time)}")
        self.changed = True
        return num_removed

    # used to quickly view the contents of an HTML response
    def create_temp_html(self, filename, contents, extension):
        if not os.path.exists(self.rendered_file_dir):
            os.mkdir(self.rendered_file_dir)

        # escape whole HTML page from Jinja interpretation
        if extension == 'html':
            contents = "{% raw %}\n" + str(contents) + "\n{% endraw %}\n"

        filename = filename.replace(" ", "_")
        rendered_file = f"{filename}.{extension}"
        rendered_path = self.rendered_file_dir + rendered_file
        with open(rendered_path, 'w') as file:
            file.write(str(contents))
        return f"http://127.0.0.1:8099/responses/{rendered_file}", rendered_path

    def has_results(self):
        return self.results is not None and 'contents' in self.results and len(self.results['contents']) > 0

    def get_last_results(self, get_tables=False, get_groups=False):
        return self.get_results(-1, get_tables, get_groups)

    def get_content_type(self, header_dict, body):
        content_length = ('Content-Length' in header_dict and int(header_dict['Content-Length']) > 0) or len(body) > 0
        if not content_length:
            return 'none'
        elif 'Content-Type' in header_dict:
            if header_dict['Content-Type'].startswith('text/html'):
                # try to parse it as html, else it is probably json
                if bool(BeautifulSoup(body, "html.parser").find()):
                    return 'html'
                else:
                    return 'json'
            elif header_dict['Content-Type'].startswith('application/json'):
                return 'json'
        # we do not know what it is.. probably some text
        return 'text'

    def get_inline_results(self, res_id):
        # todo should return all results per group as is.
        pass

    def compare_group_repr(self, group_nr_1, group_nr_2, content_id=None, print_matches=False):
        if not self.has_results():
            return -1, content_id
        if content_id is None:
            content_id = str(sorted([int(key) for key in self.results['contents'].keys()])[-1])
        if content_id not in self.results['contents']:
            raise Exception(f"Request id '{content_id}' cannot be found in results!")
        if group_nr_1 == group_nr_2:
            raise Exception(f"Cannot compare result group '{group_nr_1}' of request id '{content_id}' with itself!")
        groups = self.results['contents'][content_id]['groups']
        group_reprs = []
        for group_nr in [group_nr_1, group_nr_2]:
            if 0 <= group_nr < len(groups):
                group_repr = copy.deepcopy(groups[group_nr]['representative'])
                # use full html instead of link
                if type(group_repr['body']) is str and group_repr['body'].endswith(".html"):
                    group_repr['body_url'] = group_repr['body']
                    group_repr['body'] = groups[group_nr]['responses'][0]['body']
                group_reprs.append(group_repr)
            else:
                raise Exception(f"Result group '{group_nr}' of request id '{content_id}' does not exist!")

        # if one has min-max, the other needs it too
        for item in ['send_time', 'response_time']:
            for i in [0, 1]:
                if item + '_min' in group_reprs[i % 2] and item + '_min' not in group_reprs[(i + 1) % 2]:
                    group_reprs[(i + 1) % 2][item + '_min'] = group_reprs[(i + 1) % 2][item]
                    group_reprs[(i + 1) % 2][item + '_max'] = group_reprs[(i + 1) % 2][item]

        # compare them and show results
        comp = utils.compare_responses(group_reprs[0], group_reprs[1], None, False)
        if not print_matches:
            comp.pop('match', None)
        comp = utils.perform_string_compare_on_results(comp, 25)
        return comp, content_id

    def get_responses(self, content_id):
        if not self.has_results():
            # no results
            return []
        if type(self.results['contents'][content_id]) is not dict:
            # not yet grouped, just return list
            return self.results['contents'][content_id]
        # result are grouped, lets extract them
        responses = []
        for group in self.results['contents'][content_id]['groups']:
            responses.extend(group['responses'])
        responses = sorted(responses, key=lambda x: x['send_time'])
        return responses

    def undo_grouping(self, content_id):
        if type(self.results['contents'][content_id]) is dict:
            self.changed = True
            # remove the files
            self.remove_the_files(content_id)
            # undo grouping of this content_id, extract requests and re-sort
            self.results['contents'][content_id] = self.get_responses(content_id)

    def redo_all_grouping(self, custom_comparing=None, force=False):
        if not self.has_results():
            return -1
        time.sleep(0.1)
        for content_id in list(self.results['contents'].keys()):
            un_group = False
            re_group = False
            item = self.results['contents'][content_id]
            if type(item) is not dict:
                re_group = True
            elif 'groups' not in item or not item['groups']:
                # has no real results --> skip
                continue
            elif force or self.check_missing_files(content_id) or 'representative' not in item['groups'][0]:
                un_group = True
                re_group = True
            # act based on required actions
            if un_group:
                self.undo_grouping(content_id)
            if re_group:
                self.do_grouping(content_id, custom_comparing)
        time.sleep(0.1)

    def get_min_max_dates(self, list_of_dicts_in_group, fields):
        time_format = '%Y-%m-%d %H:%M:%S.%f'
        min_max = dict()
        # get min and max time of every field of interest
        for item_dict in list_of_dicts_in_group:
            overlap = set(fields).intersection(set(item_dict.keys()))
            for overlap_item in overlap:
                date_time_object = datetime.strptime(item_dict[overlap_item], time_format)
                if overlap_item not in min_max:
                    min_max[overlap_item] = {'min': date_time_object,
                                             'max': date_time_object}
                elif date_time_object < min_max[overlap_item]['min']:
                    min_max[overlap_item]['min'] = date_time_object
                elif date_time_object > min_max[overlap_item]['max']:
                    min_max[overlap_item]['max'] = date_time_object
        # convert min-max datetime objects back to time strings
        # add two keys if min-max is different, else only keep one
        for item in copy.deepcopy(list(min_max.keys())):
            if min_max[item]['min'] == min_max[item]['max']:
                min_max[item] = min_max[item]['min'].strftime(time_format)
            else:
                min_max[item + '_min'] = min_max[item]['min'].strftime(time_format)
                min_max[item + '_max'] = min_max[item]['max'].strftime(time_format)
                del min_max[item]
        return min_max

    def do_grouping(self, content_id, custom_comparing=None):
        self.changed = True

        # create groups with full content and comparison information
        if custom_comparing is None:
            custom_comparing = self.custom_comparing
        groups_and_stats = utils.get_grouped_responses(self.results['contents'][content_id], custom_comparing)

        # turn content into dict of groups, stats and created files (responses are now stored in the groups)
        self.results['contents'][content_id] = groups_and_stats
        self.results['contents'][content_id]['files'] = []

        # add a combined and enriched representative response for every group
        for i, group in enumerate(self.results['contents'][content_id]['groups']):
            representative = copy.deepcopy(group['responses'][0])
            representative['body_length'] = f"{len(representative['body'])} bytes"
            representative['headers_length'] = f"{len(utils.format_json(representative['headers']))} bytes"
            # get min-max send and response times
            keys_of_interest = ['send_time', 'response_time']
            min_max = self.get_min_max_dates(group['responses'], keys_of_interest)
            if len(min_max.keys()) > len(keys_of_interest):
                # there is at least one difference, update repr. keys
                for key in keys_of_interest:
                    if key + "_min" in min_max:
                        representative[key + "_min"] = min_max[key + "_min"]
                        representative[key + "_max"] = min_max[key + "_max"]
                        del representative[key]
            # parse body if the content is known (HTML or JSON)
            content_type = self.get_content_type(representative['headers'], representative['body'])
            if content_type == 'html':
                link, path = self.create_temp_html(f"{self.name}-req-{content_id}-group-{i}", representative['body'],
                                                   "html")
                self.results['contents'][content_id]['files'].append(path)
                representative['body'] = link
            elif content_type == 'json':
                # todo fix: it will break if the content is not json after all..
                representative['body'] = utils.read_json(representative['body'])

            self.results['contents'][content_id]['groups'][i]['representative'] = representative

    def get_results(self, res_id, get_tables=False, get_groups=False):
        # todo implement a history of multiple results
        # todo issues:  when requests are removed --> results invalid
        # todo          how to manage the growing list of historical results?
        if not self.has_results():
            # no results yet!
            return "\tNo results"
        if type(self.results['contents']) is not dict:
            self.redo_all_grouping(force=True)

        string = "Batch results: \n"
        string += f"\tSend time: {self.results['start_time']}:\n"
        string += f"\tEnd time:  {self.results['end_time']}:\n"

        for content_id in self.results['contents']:

            string += f"\n\tRequest id '{content_id}':\n\n"

            my_contents = self.results['contents'][content_id]
            if get_groups:
                for i, group in enumerate(my_contents['groups']):
                    string += f"\t\tGroup {i} - {len(group['responses'])} item(s):\n"
                    string += utils.tabbed_pprint_representative(group['representative'], 3, self.small) + "\n"
                string += f"\n\tRequest id (continued) '{content_id}':\n\n"

            if get_tables:
                responses = self.get_responses(content_id)
                string += utils.tabbed_string(utils.get_res_spec_tables(responses, {
                    "status_code": None,
                    "body": (lambda x: len(x), 'length', 'bytes'),
                    "headers": (lambda x: len(x.keys()), None, None)}
                                                                        ), 2)

                string += utils.tabbed_string(utils.get_res_spec_tables(responses, {
                    "headers": (lambda x: len(utils.format_json(x)), 'bytes', None)}
                                                                        ), 2)

            # always display the grouping information
            string += f"\t\tNumber of groups: {len(my_contents['groups'])}\n"
            if my_contents['stats']['ignored']:
                string += f"\t\tIgnored:      {my_contents['stats']['ignored']}\n"
            if my_contents['stats']['always_match']:
                string += f"\t\tAlways match: {my_contents['stats']['always_match']}\n"
            if my_contents['stats']['never_match']:
                string += f"\t\tNever match:  {my_contents['stats']['never_match']}\n"
            string += "\n"

        return string

    @staticmethod
    def __print_multi_box(request_id, offset, vert, hori):
        req_str = str(request_id)
        space = " " * offset
        beam_part = ("+" + "-" * len(req_str)) * hori + "+"
        number_part = ("|" + req_str) * hori + "|"
        out_str = ""
        for i in range(1, vert + 1):
            out_str += space + beam_part + "\n"
            out_str += space + number_part + "\n"
        out_str += space + beam_part
        return out_str

    def get_info(self, req_id, wait_time):
        if (req_id, wait_time) not in self.items:
            raise Exception(f"Request_id and/or wait_time not in batch: {(req_id, wait_time)}")
        return f"{(req_id, wait_time)} -> {self.items[(req_id, wait_time)]}"

    def get_summary(self, full_contents=False):
        string = f"name = '{self.name}'\n" \
                 f"allow_redirects = '{self.allow_redirects}'\n" \
                 f"sync_last_byte = '{self.sync_last_byte}'\n"
        if self.items:
            if not full_contents:
                string += "\n(request_id, wait_time) -> (parallel, sequential)\n"
                for key in self.items.keys():
                    string += f"{self.get_info(key[0], key[1])}\n"
            else:
                offsets = sorted(set([key[1] for key in self.items.keys()]))
                for key in sorted(self.items.keys(), key=lambda x: (x[0], x[1])):
                    values = self.items[key]
                    box = self.__print_multi_box(key[0], offsets.index(key[1]), values[0], values[1])
                    string += f"\nwait_time = {key[1]}\n{box}\n"
        else:
            string += "\tEmpty.\n"
        return string

    @staticmethod
    def get_mini_summary_header():
        return ["Name", "Items", "#Reqs", "Res?", "Sync?", "Redir?"]

    def get_mini_summary_string(self):
        return f"{self.name} - {len(list(self.items.keys()))} item(s)",

    def get_mini_summary_dict(self):
        reqs = self.get_reqs()
        re_str = ""
        if len(reqs) > 0:
            if len(reqs) <= 10:
                re_str += str(reqs)
            else:
                re_str += str(reqs[:10])[:-1] + f", {len(reqs[10:])} more ...]"
        return {'name': self.name,
                'items': re_str,
                'requests': sum([i * j for i, j in self.items.values()]),
                'has_results': self.has_results(),
                'is_synced': self.sync_last_byte,
                'is_redir': self.allow_redirects,
                }

    def __str__(self):
        return self.get_summary(False)


if __name__ == '__main__':
    # todo maybe rewrite as a unit test?
    my_requests = {
        "1": {
            "body": "password=232323&username=safe123&",
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,nl;q=0.7",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": "JSESSIONID=6731A59A338A1A6104DEF9E879296BF1",
                "Origin": "http://127.0.0.1:8090",
                "Referer": "http://127.0.0.1:8090/WebGoat/login",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.102 Safari/537.36"
            },
            "method": "POST",
            "parallel": 10,
            "timestamp": 1543315415.7996092,
            "url": "http://127.0.0.1:8090/WebGoat/login",
            "id": 2
        },
        "2": {
            "body": "agree=agree&matchingPassword=121212&password=121212&username=bad123&",
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,nl;q=0.7",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": "JSESSIONID=174CEC7378A51918F7114B3B275E0234",
                "Origin": "http://127.0.0.1:8090",
                "Referer": "http://127.0.0.1:8090/WebGoat/registration",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.102 Safari/537.36"
            },
            "method": "POST",
            "parallel": 10,
            "timestamp": 1543314627.112285,
            "url": "http://127.0.0.1:8090/WebGoat/register.mvc",
            "id": 1
        }}
    batch = Batch()
    # for the_time in [0, 100, 200, 300, 400, 500]:
    #    batch.add("1", the_time, 2, 1)
    batch.add("1", 0, 4, 5)
    batch.add("1", 100, 3, 2)
    batch.add("2", 0, 100, 1)
    # batch.add("2", 0, 2, 1)
    print(batch)
    results = batch.execute(my_requests, True)
    print(pprint.pformat(results))
    print("done")

'''
    Unused - methods to send a request via requests library (1000 times slower than via aiohttp + uvloop)

    # do not call outside the class
    # transaction url for example: /blocks (include the slash)
    def __perform_single_server_request(self, a_request, timeout):
        if a_request['headers']['Content-Type'] and "json" in a_request['headers']['Content-Type'].lower():
            return requests.request(a_request['method'], a_request['url'],
                                    json=utils.format_json(a_request['body']),
                                    headers=a_request['headers'],
                                    allow_redirects=False,
                                    timeout=timeout)
        else:
            return requests.request(a_request['method'], a_request['url'],
                                    data=a_request['body'],
                                    headers=a_request['headers'],
                                    allow_redirects=False,
                                    timeout=timeout)

    def __read_response(self, response):
        result = dict()
        result['status_code'] = response.status_code
        result['headers'] = dict(response.headers)
        result['body'] = response.content.decode(response.encoding)
        result['other'] = {}
        result['other']['is_redirect'] = response.is_redirect
        result['other']['is_permanent_redirect'] = response.is_permanent_redirect
        return result

    def __sleep_uninterruped(self, the_time):
        start_time = time.time()
        while time.time() - start_time < the_time:
            try:
                time.sleep(the_time - (time.time() - start_time))
            except KeyboardInterrupt as e:
                pass

    def __execute_sequence(self, request, wait_condition, wait_time, dup_h, result_queue):
        name = multiprocessing.current_process().name
        print(f"Started, waiting for condition: {name}")
        try:
            with wait_condition:
                wait_condition.wait()
        except KeyboardInterrupt as e:
            print(f"Interrupted! {name}")
            return

        #print(f"Waiting for wait_time: {name}")
        #self.__sleep_uninterruped(wait_time / 1000)
        time.sleep(wait_time / 1000)

        #print(f"Running: {name}")
        responses = []
        for index in range(0, dup_h):
            try:
                response = self.__perform_single_server_request(request, 5000)
            except KeyboardInterrupt as e:
                responses.append(["Timeout"])
            else:
                if response is not None:
                    responses.append(self.__read_response(response))
                else:
                    responses.append(["No result"])
        result_queue.put(responses)
        print(f"Done: {name}")


    # assumes all requests are present in the_requests argument
    # set multiprocess to False to allow for debugging
    def execute(self, the_requests, multi_process=True):
        if self.is_empty():
            return None

        start_time = time.time()  # time it
        processes = []
        queues = {}
        wait_condition = multiprocessing.Condition()
        # create all processes
        for index, key in enumerate(self.items.keys()):
            value = self.items[key]
            the_request = the_requests[key[0]]
            queues[key[0]] = []
            for sub_index in range(0, value[0]):
                a_queue = Queue()
                queues[key[0]].append(a_queue)
                if multi_process:
                    processes.append(multiprocessing.Process(
                        name=f"Req-{key[0]}-{sub_index}",
                        target=self.__execute_sequence,
                        args=(the_request, wait_condition, key[1], value[1], a_queue))
                    )
                else:
                    processes.append(threading.Thread(
                        name=f"Req-{key[0]}-{sub_index}",
                        target=self.__execute_sequence,
                        args=(the_request, wait_condition, key[1], value[1], a_queue))
                    )
        # startup all processes
        [process.start() for process in processes]
        time.sleep(1)
        # actually start all processes
        with wait_condition:
            wait_condition.notify_all()
        # read the output of process queues
        results = {}
        for index, key in enumerate(queues.keys()):
            results[key] = []
            for queue in queues[key]:
                # todo skip if it takes too long
                got_item = None
                while not got_item:
                    try:
                        got_item = queue.get()
                    except KeyboardInterrupt as e:
                        pass
                results[key].append(got_item)
        # wait until all processes are done
        # todo terminate them if it takes too long
        [process.join() for process in processes]
        self.results = results
        return results

'''
