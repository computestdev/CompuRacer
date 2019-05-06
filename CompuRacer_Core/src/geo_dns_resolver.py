#!/usr/bin/env python3
"""
The GeoDNSResolver class can be used to get most of the IP addresses and locations that a hostname resolves to.
It has a list of open DNS servers from all around to world to gather this information.
It will use the IP-API alongside a cache and rate limiter (to avoid getting banned) to obtain the locations of the IPs.
It is currently not actively used in the CompuRacer toolset, but could easily be added to the async batch send file.
"""

# --- All imports --- #
import asyncio
import copy
import pprint
import struct
import uvloop
from _socket import inet_aton
from threading import RLock

import aiodns
from IPy import IP
from aiohttp import ClientSession
from async_timeout import timeout
from cachetools import cached, TTLCache
from ratelimiter import RateLimiter

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


class GeoDNSResolver:

    # rate limiter + cache to avoid over-using GEO API
    cache = TTLCache(maxsize=10000, ttl=3600) # every entry lasts for an hour
    rate_limiter = RateLimiter(max_calls=100, period=60)
    lock = RLock()

    nameservers = ['103.243.179.145', '209.244.0.3', '64.6.64.6',
                   '9.9.9.9', '84.200.69.80', '8.26.56.26',
                   '208.67.222.222', '195.46.39.39', '81.218.119.11',
                   '198.206.14.241', '216.146.35.35', '208.76.50.50',
                   '45.33.97.5', '198.101.242.72', '77.88.8.8',
                   '91.239.100.100', '74.82.42.42', '109.69.8.51',
                   '156.154.70.1', '1.1.1.1', '45.77.165.194',
                   '185.228.168.9', '8.26.56.26', '84.200.70.40',
                   '208.67.220.220', '199.85.126.10', '8.8.8.8']

    @staticmethod
    async def resolve_to_ip(resolver, url):
        resolved = [url, None]
        try:
            new_url = copy.copy(url)
            if "//" in new_url:
                new_url = new_url.split("//")[1]
            if ":" in new_url:
                new_url = new_url.split(":")[0]
            IP(new_url)
        except ValueError:
            try:
                async with timeout(3):
                    results = await resolver.query(url, 'A')
                    resolved[1] = []
                    for result in results:
                        resolved[1].append(result.host)
            except aiodns.error.DNSError as e:
                print(e)
            except asyncio.TimeoutError as e:
                print("Timeout!")
        print(f"Resolved! {resolver.nameservers}: {resolved}")
        return resolved

    async def resolve_all_to_ip(self, list_of_urls, my_loop=None, nameservers=None):
        my_loop, new_loop = self.get_loop(my_loop)
        resolver = aiodns.DNSResolver(loop=my_loop, nameservers=[nameservers])
        tasks = []
        for url in list_of_urls:
            tasks.append(self.resolve_to_ip(resolver, url))
        results = await asyncio.gather(*tasks)
        processed_results = {}
        for result in results:
            if result[1]:
                processed_results[result[0]] = result[1]
        if new_loop:
            self.stop_loop(my_loop)
        return processed_results

    async def resolve_all_to_ip_mns(self, list_of_urls, my_loop=None, nameservers=None, do_collect=True):
        my_loop, new_loop = self.get_loop(my_loop)
        tasks = []
        for nameserver in nameservers:
            tasks.append(self.resolve_all_to_ip(list_of_urls, my_loop, nameserver))
        results = await asyncio.gather(*tasks)
        if do_collect:
            results = self.collect_unique_per_ip(results)
        print(pprint.pformat(results))
        if new_loop:
            self.stop_loop(my_loop)
        return results

    def get_ips_and_locations(self, hostnames):
        my_loop, _ = self.get_loop()
        future = asyncio.ensure_future(self.resolve_all_to_ip_mns(hostnames, my_loop, self.nameservers, True))
        res_parsed = my_loop.run_until_complete(future)

        geo_results = {}
        for hostname in res_parsed.keys():
            if res_parsed[hostname]:
                future = asyncio.ensure_future(self.get_lat_lons(res_parsed[hostname]))
                geo_results[hostname] = my_loop.run_until_complete(future)
            else:
                geo_results[hostname] = None
        return geo_results

    @cached(cache, lock=lock)
    async def get_lat_lon(self, ip_address):
        req_url = f"http://ip-api.com/json/{ip_address}?fields=lat,lon,status"
        async with self.rate_limiter:
            async with ClientSession() as session:
                async with session.request(method="GET", url=req_url) as response:
                    json_data = await response.json()
                    if json_data and 'status' in json_data and json_data['status'] == "success":
                        return [ip_address, {'lat': json_data['lat'], 'lon': json_data['lon']}]
                    else:
                        return [ip_address, None]

    async def get_lat_lons(self, ip_addresses):
        results = {}
        for ip_address in ip_addresses:
            res = await self.get_lat_lon(ip_address)
            results[res[0]] = res[1]
        return results

    # todo move to utils
    @staticmethod
    def get_loop(my_loop=None):
        new_loop = not my_loop
        if not my_loop:
            # start loop
            my_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(my_loop)
        return my_loop, new_loop

    # todo move to utils
    @staticmethod
    def stop_loop(my_loop):
        # shutdown eventloop
        my_loop.stop()
        my_loop.close()

    # todo move to utils
    @staticmethod
    def collect_unique_per_ip(*dicts):
        result = {}
        for key in set.union(*(set(d) for d in dicts[0])):
            result[key] = sorted(set(sum((d.get(key, []) for d in dicts[0]), [])),
                                 key=lambda ip: struct.unpack("!L", inet_aton(ip))[0])
        return result


if __name__ == '__main__':
    gdnsr = GeoDNSResolver()
    print(pprint.pformat(gdnsr.get_ips_and_locations(["facebook.com"])))
