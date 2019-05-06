#!/usr/bin/env python3
"""
The main file of the vulnerable voucher redeem web app.

It is used to create a web app on which users can redeem two types of vouchers in three ways. There are vouchers that are usable once and vouchers that can be used multiple times. Also, you can redeem a voucher securely (one DB transaction), insecurely (two DB transactions) or very insecurely (two DB transactions with a sleep in between).
"""

# --- All imports --- #
import datetime
import logging
import signal
import sys
import time
from multiprocessing import RLock

import pymysql.cursors
from flask import Flask, jsonify, render_template, request
import timeout_decorator

thismodule = sys.modules[__name__]

app = Flask(__name__)
pymysql.install_as_MySQLdb()

# --- Authorship information --- #
__author__ = "D. Keuper & R.J. van Emous @ Computest"
__license__ = "MIT License"
__version__ = "2019"
__email__ = "dkeuper@computest.nl & rvanemous@computest.nl"
__status__ = "Prototype"


# create logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# create console handler and set level to debug
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
# create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# add formatter to ch
ch.setFormatter(formatter)
# add ch to logger
logger.addHandler(ch)

# Connect to the database.
connection_settings = {
    'host': "localhost",
    'user': "voucher",
    'passwd': "HaiKooLePooxi9uway6oa8Ieroh1hoiw",
    'db': "voucher",
    'cursorclass': pymysql.cursors.DictCursor
}
thismodule.conn = pymysql.connect(**connection_settings)
thismodule.redo_connection_lock = RLock()

# add shutdown hooks
signal.signal(signal.SIGINT, thismodule.conn.close)
signal.signal(signal.SIGTERM, thismodule.conn.close)


def get_time_ns():
    if sys.version_info >= (3, 7):
        return time.time_ns()
    else:
        return time.time() * 1e9
    
    
@timeout_decorator.timeout(5, use_signals=False)
def close_connection():
     if thismodule.conn:
        try:
            thismodule.conn.close()
        except Exception:
            pass    
    
    
def reconnect_to_db(try_close=True):
    if try_close:
        try:
            close_connection()
        except Exception:
            pass
    with thismodule.redo_connection_lock:
        thismodule.conn = pymysql.connect(**connection_settings)
        
        
def jsonify_sc(item, statuscode):
    """
    Creates a JSON response with an embedded HTTP status code.

    Returns:
        A JSON response.
    """
    with app.app_context():
        if not item:
            item = {}
        response = jsonify(item)
        response.status_code = statuscode
        #logger.info("New response: " + str(response))
    return response
        
        
def do_reset():
    """
    Resets the database, by adding default coupon codes.

    """
    curr_time = str(datetime.datetime.fromtimestamp(get_time_ns() / 1e9))
    try:
        thismodule.conn.begin()
        with thismodule.conn.cursor() as c:
            c.execute("CREATE TABLE IF NOT EXISTS vouchers")
            c.execute("CREATE TABLE IF NOT EXISTS vouchers_multi")
            c.execute("TRUNCATE TABLE vouchers")
            c.execute("TRUNCATE TABLE vouchers_multi")
            c.execute("INSERT INTO vouchers (code) VALUES ('COUPON1')")
            c.execute("INSERT INTO vouchers_multi (code, count) VALUES ('COUPON2', '10')")
            c.execute("INSERT INTO vouchers_multi (code, count) VALUES ('COUPON3', '100')")
        thismodule.conn.commit()
    except Exception:
        reconnect_to_db()
        return jsonify_sc({'time', curr_time}, 500)
    else:
        return jsonify_sc({'time': curr_time}, 200)


@app.route("/reset", methods=['POST'])
def reset():
    """
    Resets the database, by adding default coupon codes.

    Returns:
        A JSON response (200).
    """
    return do_reset()

# Perform an initial reset at startup
do_reset()


@app.route("/post-some-data/", methods=['POST'])
def post_some_data():
    curr_time = str(datetime.datetime.fromtimestamp(get_time_ns() / 1e9))
    if not request.json:
        return jsonify_sc({'Error': "No JSON embedded!"}, 400)
    if 'data' not in request.json:
        return jsonify_sc({'Error': "No 'data' key in JSON body!"}, 400)
    return jsonify_sc({'time': curr_time}, 200)


def redeem_voucher(code, is_one_transaction=False, is_single=True, sleep_time=0):
    ns_time = get_time_ns()
    curr_time = str(datetime.datetime.fromtimestamp(ns_time / 1e9))
    logger.debug(ns_time / 1e3)
    if is_single:
        check_query = "SELECT * FROM vouchers WHERE code = %s"
        if is_one_transaction:
            check_query += " FOR UPDATE"
        action_query = "DELETE FROM vouchers WHERE code = %s"

    else:
        check_query = "SELECT count FROM vouchers_multi WHERE code = %s"
        if is_one_transaction:
            check_query += " FOR UPDATE"
        action_query = "UPDATE vouchers_multi SET count = %s WHERE code = %s"
    try:
        success = False
        count = 1
        if is_one_transaction:
            thismodule.conn.begin()
        with thismodule.conn.cursor() as c:
            c.execute(check_query, (code, ))
            result = c.fetchone()
            if result:
                # We sleep for x seconds, to make the race easier to win.
                if sleep_time > 0:
                    time.sleep(sleep_time)
                if is_single:
                    success = True
                else:
                    count = int(result['count'])
                    if count > 0:
                        success = True
        if not success:
            if is_one_transaction:
                thismodule.conn.commit()
            return jsonify_sc({'count': 0, 'time': curr_time}, 404)
        with thismodule.conn.cursor() as c:
            if is_single:
                c.execute(action_query, (code, ))
            else:
                c.execute(action_query, (count - 1, code))
        if is_one_transaction:
            thismodule.conn.commit()
    except Exception:
        reconnect_to_db()
        return jsonify_sc({'time': curr_time}, 500)
    else:
        return jsonify_sc({'count': count, 'time': curr_time}, 200)


# ----------------- single-use voucher redeem ----------------- #
@app.route("/redeem/very_insecure/<code>", methods=['POST'])
def redeem_very_insecure(code):
    """
    Redeems a code in such a way that it should be very easy to exploit if you
    can make two requests at roughly the same time. This is the same as 
    redeem_insecure(), but we added an extra time.sleep(3), to make the race
    even easier.

    Args:
        code: The code to redeem.

    Returns:
        A JSON counter, with the value 0 on success and -1 if no coupon was redeemed at all.
    """
    return redeem_voucher(code, is_one_transaction=False, is_single=True, sleep_time=3)


@app.route("/redeem/insecure/<code>", methods=['POST'])
def redeem_insecure(code):
    """
    Redeems a code in such a way that it should be possible to exploit if you
    can make two requests at roughly the same time.

    Args:
        code: The code to redeem.

    Returns:
        A JSON counter, with the value 0 on success and -1 if no coupon was redeemed at all.
    """
    return redeem_voucher(code, is_one_transaction=False, is_single=True, sleep_time=0)



@app.route("/redeem/secure/<code>", methods=['POST'])
def redeem_secure(code):
    """
    Redeem a coupon in such a way that race conditions should not be possible.

    Args:
        code: The code to redeem.

    Returns:
        A JSON counter, with the value 0 on success and -1 if no coupon was redeemed at all.
    """
    return redeem_voucher(code, is_one_transaction=True, is_single=True, sleep_time=3)

# ----------------- multi-use voucher redeem ----------------- #


@app.route("/redeem_multi/very_insecure/<code>", methods=['POST'])
def redeem_multi_very_insecure(code):
    """
    Redeems a multi code in such a way that it should be very easy to exploit if you
    can make two requests at roughly the same time. This is the same as
    redeem_insecure(), but we added an extra time.sleep(3), to make the race
    even easier.

    Args:
        code: The code to redeem.

    Returns:
        A JSON counter, indicating how much coupons are left or -1 if no coupon was redeemed at all.
    """
    return redeem_voucher(code, is_one_transaction=False, is_single=False, sleep_time=3)


@app.route("/redeem_multi/insecure/<code>", methods=['POST'])
def redeem_multi_insecure(code):
    """
    Redeems a multi code in such a way that it should be possible to exploit if you
    can make two requests at roughly the same time.

    Args:
        code: The code to redeem.

    Returns:
        A JSON counter, indicating how much coupons are left or -1 if no coupon was redeemed at all.
    """
    return redeem_voucher(code, is_one_transaction=False, is_single=False, sleep_time=0)


@app.route("/redeem_multi/secure/<code>", methods=['POST'])
def redeem_multi_secure(code):
    """
    Redeem a multi coupon in such a way that race conditions should not be possible.

    Args:
        code: The code to redeem.

    Returns:
        A JSON counter, indicating how much coupons are left or -1 if no coupon was redeemed at all.
    """
    return redeem_voucher(code, is_one_transaction=True, is_single=False, sleep_time=3)
    

@app.route("/", methods=['GET'])
def index():
    """
    Show the main website. Before this we reset the database, such that every
    refresh will give us a fresh coupon.

    Returns:
        An HTTP text/html response.
    """

    reset()
    return render_template('index.html')
