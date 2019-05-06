#!/usr/bin/env python3
"""
The batch async sender file contains functions for sending a Batch asynchronously and very quickly.
It will encode the requests that are send and also read and decode the results.
"""

# --- All imports --- #
import asyncio
import base64
import binascii
import copy
import datetime
import json
import pprint
import random
import sys
import time
import urllib
from collections import defaultdict

from async_timeout import timeout as async_timeout

import src.aiohttp as aiohttp
import chardet
import uvloop
from src.aiohttp import ClientSession

import src.utils as utils
from tqdm import tqdm

from .batch import Batch

uvloop.install()
progress_bar_width = 100


# todo move to utils
def get_time_ns():
    if sys.version_info >= (3, 6):
        return time.time_ns()
    else:
        return time.time() * 1e9


def __decode_response(response):
    # decode headers
    response['headers'] = {}
    if response['headers_temp'] and len(response['headers_temp']) > 0:
        encoding = chardet.detect(list(response['headers_temp'].keys())[0])['encoding']
        for key in response['headers_temp'].keys():
            response['headers'][key.decode(encoding)] = response['headers_temp'][key].decode(encoding)
    del response['headers_temp']
    # decode body
    response['body'] = {}
    if response['body_temp']:
        encoding = chardet.detect(response['body_temp'])['encoding']
        if encoding is None:
            # cannot decode it --> just past it in as is
            response['body'] = response['body_temp']
        else:
            response['body'] = response['body_temp'].decode(encoding)
    del response['body_temp']
    return response


async def __read_response(response, send_time, response_time):
    result = dict({'send_time': send_time, 'response_time': response_time})
    result['status_code'] = response.status
    # read headers
    result['headers_temp'] = dict(response.raw_headers)
    # read body
    result['body_temp'] = await response.content.read(-1)
    return result


async def __my_own_sleep(wait_until):
    # get sleep time minus 20 ms
    sleep_time = wait_until - get_time_ns() / 1e6 - 20
    # wait longest part async
    if sleep_time > 0:
        await asyncio.sleep(sleep_time / 1000)
    # wait last 20 ms or less synchronously for more accuracy
    while wait_until - get_time_ns() / 1e6 > 0:
        pass


async def __a_sup_request(request_id, a_prepared_request, wait_time, wait_until, duplication, timeout, session):
    responses = []
    await __my_own_sleep(wait_until)
    for dup in range(duplication):
        # run dups sequentially
        try:
            async with async_timeout(timeout) as cm:
                send_time = str(datetime.datetime.now())
                async with session.request(**a_prepared_request) as response:
                    responses.append(await __read_response(response, send_time, str(datetime.datetime.now())))
            if cm.expired:
                raise Exception(f"Timeout of {timeout} seconds reached!")
        except aiohttp.client_exceptions.ClientConnectorError as e:
            return [(request_id, wait_time), e]
        except asyncio.TimeoutError as e:
            return [(request_id, wait_time), e]
        except Exception as e:
            return [(request_id, wait_time), e]

    return [(request_id, wait_time), responses]  # are not decoded yet


def __prepare_request(the_request, allow_redirects, final_byte_time=None):
    a_request = copy.deepcopy(the_request)
    request_content = {'method': a_request['method'],
                       'url': a_request['url'].replace("http://localhost", "http://127.0.0.1"),
                       'headers': a_request['headers'],
                       'allow_redirects': allow_redirects
                       }
    # decode cookie header if necessary
    if 'Cookie' in a_request['headers']:
        request_content['headers']['Cookie'] = urllib.parse.unquote(a_request['headers']['Cookie'])
    # decode and restore content if necessary
    if 'Content-Type' in a_request['headers']:
        if "json" in a_request['headers']['Content-Type'].lower() and type(a_request['body']) is str:
            request_content['json'] = utils.read_json(a_request['body'])
        else:
            if type(a_request['body']) is dict:
                new_body = ""
                for key in a_request['body'].keys():
                    new_body += f"{key}={a_request['body'][key]}&"
                a_request['body'] = new_body
            if a_request['headers']['Content-Type'].startswith("multipart/form-data"):
                if a_request['body'].startswith("BASE64="):
                    # it came from the Burp plugin (base 64 encoded)
                    try:
                        body = base64.b64decode(str(a_request['body'].replace("BASE64=", "")))
                    except binascii.Error:
                        # conversion failed, is probably just string data
                        body = a_request['body']
                else:
                    # it came from the Chrome plugin (url encoded)
                    parts = [item.split("=") for item in a_request['body'].split("&")][:-1]
                    separator = "--" + a_request['headers']['Content-Type'].split("=")[1]
                    body = ""
                    for part in parts:
                        body += separator + "\r\n"
                        body += f"Content-Disposition: form-data; name=\"{urllib.parse.unquote(part[0])}\"\r\n\r\n"
                        body += urllib.parse.unquote(part[1]) + "\r\n"
                    body += separator + "--" + "\r\n"
                    body = str.encode(body)
            elif a_request['headers']['Content-Type'].startswith("application/x-www-form-urlencoded"):
                body = urllib.parse.unquote(a_request['body'])
            else:
                body = a_request['body']
            request_content['data'] = body
    # re-calculate content length
    if 'Content-Length' in request_content['headers']:
        len_data = 0
        if 'data' in request_content:
            len_data = len(request_content['data'])
        elif 'json' in request_content:
            len_data = len(json.dumps(request_content['json']))
        if len_data != int(request_content['headers']['Content-Length']):
            request_content['headers']['Content-Length'] = str(len_data)
    # add final byte time
    if final_byte_time is not None:
        request_content['final_byte_time'] = final_byte_time
    return request_content


# added shuffle to avoid sending all dups of one request before the other
# todo does this work well enough?
def prepare_sending_order(items):
    send_order = list(items.keys())
    full_send_order = []
    for key in send_order:
        for i in range(items[key][0]):
            full_send_order.append(key)
    # randomly shuffle the list
    random.shuffle(full_send_order)
    return full_send_order


async def run(batch, requests):
    # Create client session that will ensure we don't open a new connection per each request.
    # todo It is synced on the whole second part of the wall clock time to make testing in Wireshark easier.
    # todo This results in at most 1.5 seconds delay and can be removed later on
    wait_always = 1000  # msec, to ensure all async tasks (also with wait_time = 0) are able to make this deadline
    wait_final_byte = 5000  # this is how long we wait until the final byte is sent
    ns = get_time_ns()
    start_time = round(ns / 1e9) * 1e3 + wait_always
    start_time_str = str(datetime.datetime.fromtimestamp(start_time / 1000))
    print(f"Start sending time: {start_time_str}", end="")

    # prepare requests
    prepared_requests = {}
    req_ids = batch.get_reqs()
    for req_id in req_ids:
        if batch.sync_last_byte:
            last_byte_time = start_time + wait_final_byte
            print("\tlast byte time: " + str(datetime.datetime.fromtimestamp(last_byte_time / 1000)))
        else:
            last_byte_time = None
            print()
        prepared_requests[req_id] = __prepare_request(requests[req_id], batch.allow_redirects, last_byte_time)

    tasks = []
    async with ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
        send_order = prepare_sending_order(batch.items)
        for key in send_order:
            wait_time = key[1]
            wait_until = start_time + wait_time
            values = batch.items[key]
            a_prepared_request = copy.deepcopy(prepared_requests[key[0]])
            # add wait_time to final_byte_time
            if 'final_byte_time' in a_prepared_request:
                a_prepared_request['final_byte_time'] += wait_time
            # resolve url to ip
            # todo a_request['url'] = await resolve_all_to_ip(loop, [f"{a_request['url'].split('//')[0]}//{a_request['url'].split('//')[1].split('/')[0]}"])
            # send request
            # print(f"Sending ({values[1]}x): {utils.get_req_string(requests[key[0]], True, ['timestamp'])}")
            tasks.append(asyncio.ensure_future(__a_sup_request(key[0], a_prepared_request, wait_time,
                                                               wait_until, values[1], 20, session)))
        # results = await asyncio.gather(*tasks)
        results = [await f for f in tqdm(asyncio.as_completed(tasks),
                                         total=len(tasks),
                                         desc="Receiving",
                                         ncols=progress_bar_width)]

    # decode all responses
    responses_decoded = {'start_time': start_time_str,
                         'end_time': str(datetime.datetime.fromtimestamp(round(get_time_ns() / 1e9))),
                         'contents': defaultdict(list)}

    for i, result in enumerate(tqdm(results,
                                    desc="Processing",
                                    ncols=progress_bar_width)):
        if isinstance(result[1], Exception):
            print(f"Error in sending request {i} :\n{utils.tabbed_pprint_string(result, 1)}")
            continue
        for j, response in enumerate(result[1]):
            response_decoded = __decode_response(response)
            response_decoded['wait_time'] = result[0][1]
            response_decoded['send_index'] = j
            responses_decoded['contents'][result[0][0]].append(copy.deepcopy(response_decoded))
    # sort lists to send_time
    for request_id in responses_decoded['contents'].keys():
        responses_decoded['contents'][request_id] = sorted(responses_decoded['contents'][request_id],
                                                           key=lambda x: x['send_time'])
    return responses_decoded


# todo move to utils
def get_loop(my_loop=None):
    new_loop = not my_loop
    if not my_loop:
        # start loop
        my_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(my_loop)
    return my_loop, new_loop


# todo move to utils
def stop_loop(my_loop):
    # shutdown eventloop
    my_loop.stop()
    my_loop.close()


def send_batch(batch, the_requests, my_loop=None):
    my_loop, new_loop = get_loop(my_loop)

    future = asyncio.ensure_future(run(batch, the_requests))
    res_parsed = my_loop.run_until_complete(future)

    if new_loop:
        stop_loop(my_loop)

    return res_parsed


def send_batches(batches, the_requests, my_loop=None):
    my_loop, new_loop = get_loop(my_loop)
    results = []
    for batch in batches:
        results.append(send_batch(batch, the_requests, my_loop))
    if new_loop:
        stop_loop(my_loop)
    return results


# todo move to dedicated attack class?
def attack_session_puzzling(create_account_req, login_req):
    print("sessions puzzling attack stated..")
    # define two random accounts
    creds = utils.random_user_credentials(2, 10)
    # create requests
    requests = dict({"c1": None, "c2": None, "l1": None, "l2": None})
    requests['c1'] = copy.deepcopy(create_account_req)
    requests['c2'] = copy.deepcopy(create_account_req)
    requests['c1']['body'] = create_account_req['body'].format(creds[0]['username'], creds[0]['password'],
                                                               creds[0]['password'])
    requests['c2']['body'] = create_account_req['body'].format(creds[1]['username'], creds[1]['password'],
                                                               creds[1]['password'])
    requests['l1'] = copy.deepcopy(login_req)
    requests['l2'] = copy.deepcopy(login_req)
    requests['l1']['body'] = login_req['body'].format(creds[0]['username'], creds[0]['password'])
    requests['l2']['body'] = login_req['body'].format(creds[1]['username'], creds[1]['password'])
    # create batches
    batches = list()
    batches.append(Batch("create_accounts"))
    batches[-1].add('c1', 0, 1, 1)
    batches[-1].add('c2', 100, 1, 1)
    batches.append(Batch("login_and_check", allow_redirects=True))
    batches[-1].add('l1', 0, 10, 1)
    batches[-1].add('l2', 0, 10, 1)
    # start attack
    pprint.pformat(f"Sending attack payload..")
    results = send_batches(batches, requests)
    # show results
    print(pprint.pformat(f"Results:\n{results}"), )
    return results


if __name__ == "__main__":
    my_requests = {
        "1": {
            "body": "username={}&password={}&",
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
            "timestamp": 1543315415.7996092,
            "url": "http://127.0.0.1:8090/WebGoat/login",
            "id": 2
        },
        "2": {
            "body": "agree=agree&username={}&password={}&matchingPassword={}&",
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,nl;q=0.7",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": "JSESSIONID=2639A17BBAF4BAA4DE0258F80C0F82E4",
                "Origin": "http://127.0.0.1:8090",
                "Referer": "http://127.0.0.1:8090/WebGoat/registration",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.110 Safari/537.36"
            },
            "method": "POST",
            "timestamp": 1543314627.112285,
            "url": "http://127.0.0.1:8090/WebGoat/register.mvc",
            "id": 1
        }
    }

    # add a single request
    # batch = Batch("lol")
    # batch.add("2", 0, 20, 1)
    # send_batch(batch, my_requests)
    # results = attack_session_puzzling(my_requests["2"], my_requests["1"])
