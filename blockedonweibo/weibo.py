from bs4 import BeautifulSoup
import urllib
import codecs
import time
import random
import sqlite3
from datetime import datetime
import pandas as pd
import os
import io
import requests
import base64  
import re
import ast
import sys
try:
    from urllib.parse import urlparse
except ImportError:
     from urlparse import urlparse
import rsa  
import json  
import binascii  
import math
try:
    from . import weibo_credentials
except ValueError:
    import weibo_credentials
### SETTINGS

CENSORSHIP_PHRASE = u'根据相关法律法规和政策'
CAPTCHA_PHRASE = u'你的行为有些异常'
NO_RESULTS_PHRASE = u'抱歉，未找到'

def has_censorship(keyword_encoded,
                cookie=None):

    """
    Function which actually looks up whether a search for the given keyword returns text
    which is displayed during censorship.
    Can handle unicode and strings
    Currently no CAPTCHA handling, though it is detected
    Returns string of 'censored','no_results','reset',or 'has_results'
    ('has_results' is actually not a garuantee; it's merely a lack of other censorship indicators)
    """
    url = (f'https://s.weibo.com/weibo?q={keyword_encoded}')
    cookies = {'required_cookie': cookie}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url,cookies=cookies, headers=headers).text
        i = 1
        while True:
            if CAPTCHA_PHRASE not in r:
                break
            else:
                print("CAPTCHA", keyword_encoded, "sleeping for %s seconds" % 300*i)
                time.sleep(300*i)
                i+=1
            if i==50:
                print("Can't break out of CAPTCHA, aborting")
                sys.exit(1)
    except IOError:
        wait_seconds = random.randint(90, 100)
        print(u"%s caused connection reset, waiting %s" % (keyword_encoded,wait_seconds))
        time.sleep(wait_seconds)
        return ("reset",None)

    num_results = re.findall(r'search_rese clearfix\\">\\n <span>\\u627e\\u5230(\d*)',r)
    if num_results:
        num_results = int(num_results[0])
    else:
        None
    if CENSORSHIP_PHRASE in r:
        return ("censored",None)
    elif NO_RESULTS_PHRASE in r:
        return ("no_results",None)
    else:
        return ("has_results",num_results)

def create_database(sqlite_file, overwrite=False):
    """
    Generating a sqlite file for storing results
    Multi-index primary key on id, date, and source
    Set new_database to True in order to remove any existing file
    """
    if overwrite and os.path.isfile(sqlite_file):
        os.remove(sqlite_file)
    if not os.path.isfile(sqlite_file):
        conn = sqlite3.connect(sqlite_file)
        c = conn.cursor()
        c.execute('''CREATE TABLE results (id int, date date, datetime_logged datetime, test_number int, keyword string, 
            censored bool, no_results bool, reset bool, is_canonical bool, result string, source string, orig_keyword string, 
            num_results int, notes string, PRIMARY KEY(date,source,test_number,keyword,orig_keyword))''')
        conn.commit()
        conn.close()


def insert_into_database(record_id,
                         keyword_encoded,
                         result,
                         date=datetime.now().date(),
                         source="default",
                         num_results=None,
                         notes=None,
                         sqlite_file=None,
                         test_number=1,
                         is_canonical=None,
                         orig_keyword=None):
    """
    Writing the results to the sqlite database file
    """
    conn = sqlite3.connect(sqlite_file)
    conn.text_factory = str
    conn.execute("PRAGMA busy_timeout = 5000")
    c = conn.cursor()
    
    dt_logged = datetime.now()
    if isinstance(notes, list):
        notes = str(notes)
    if isinstance(num_results, list):
        num_results = None
    
    if result == "censored":
        censored = True
    else:
        censored = False
        
    if result == "no_results":
        no_results = True
    else:
        no_results = False
        
    if result == "reset":
        reset = True
    else:
        reset = False

    query = u"""INSERT OR REPLACE INTO results (id, date, datetime_logged, test_number, keyword, censored, no_results, reset, is_canonical, result, source, orig_keyword, num_results, notes) 
               VALUES (
                    coalesce(
                        (select id from results where date=date('{date}') and keyword='{keyword}' and test_number={test_number} and source='{source}'),
                        ?),
                    ?,?,?,?,?,?,?,?,?,?,?,?,?
                    );""".format(date=date,keyword=keyword_encoded,test_number=test_number,source=source)
    c.execute(query, (record_id, date, dt_logged, test_number, keyword_encoded, censored, no_results, reset, is_canonical, result, source, orig_keyword, num_results, notes))

    conn.commit()
    conn.close()
    
def sqlite_to_df(sqlite_file,
                query="select * from results where source!='_meta_' or source is NULL;"):
    conn = sqlite3.connect(sqlite_file)
    df = pd.read_sql_query(query, conn)
    return df

def verify_cookies_work(cookie,
                return_full_response_text=False):
    """
    Returns True if cookies return profile indicator
    If no cookie or bad cookie is passed, you get a generic login page which doesn't have the indicator
    """
    cookies = {'required_cookie': cookie}
    headers = {'User-Agent': 'Mozilla/5.0'}
    r = requests.get('https://s.weibo.com/weibo?q=test',cookies=cookies, headers=headers).text
    if return_full_response_text:
        return r
    if "onick" in r:
        return True
    else:
        return False

def load_cookies(cookie_file="_cookie.txt"):
    with open(cookie_file, 'r') as f:
        #cookie = ast.literal_eval(f.read())
        cookie = f.read()
    return cookie

def run(keywords,
                verbose='all',
                insert=True,
                sqlite_file=None,
                return_df=False,
                sleep=True,
                cookie=None,
                sleep_secs=15,
                continue_interruptions=True,
                date=datetime.now().strftime('%Y-%m-%d'),
                test_number=1,
                list_source="list",
                get_canonical=False
        ):
    """
    Iterating through the keyword list and testing one at a time
    Handles when script or connection breaks; will pick up where it left off
    Set return_df to append each new result to a df in memory, which is returned at end of function
    verbose = 'some','all',or 'none'(technically anything besides 'some' or 'all' will not show anything)
    sleep = time in seconds to sleep between searches
    """
    if sqlite_file:
        create_database(sqlite_file)

    count=0
    if return_df:
        results_df = pd.DataFrame()

    if isinstance(keywords, list):
        keywords = pd.DataFrame(keywords,columns=["keyword"])
        keywords['source'] = list_source

    test_keywords = keywords.copy()

    if "Index" not in test_keywords.columns:
        test_keywords['Index'] = test_keywords.index
    if "notes" not in test_keywords.columns:
        test_keywords['notes'] = None
    if "source" not in test_keywords.columns:
        test_keywords['source'] = None
    source=test_keywords['source'][0]

    for r in test_keywords.itertuples():
        if isinstance(r.keyword, str):
            keyword_encoded = r.keyword

        if sqlite_file:
            if r.Index < len(sqlite_to_df(sqlite_file).query("date=='%s' & source=='%s' & test_number==%s & is_canonical!=1" % (date,source,test_number))) and continue_interruptions:
                continue
            if len(sqlite_to_df(sqlite_file).query(u"date=='%s' & source=='%s' & test_number==%s & keyword=='%s' & is_canonical!=1" % (date,source,test_number,keyword_encoded)))>0 and continue_interruptions:
                continue
        result,num_results = has_censorship(keyword_encoded,cookie)
        if verbose=="all":
            print(r.Index,keyword_encoded, result)
        elif verbose=="some" and (count%10==0 or count==0):
            print(r.Index,keyword_encoded, result)

        min_str = None
        if get_canonical and result == "censored":
            if verbose=="some" or verbose=="all":
                print("Found censored search phrase; determining canonical censored keyword set")
            sleep_recursive = sleep_secs if sleep else 0
            potential_kws = split_search_query(keyword_encoded, cookie, sleep_recursive, res_rtn=[], known_blocked=True, verbose=verbose)
            if verbose=="all":
                print(potential_kws)

            for kw in potential_kws:
                test_list = [kw[:i] + kw[i + 1:] for i in range(len(kw))]
                min_str = ""
                for i in range(len(test_list)):
                    if kw[i].isspace():
                        continue
                    if verbose=="all":
                        print("Testing %d of %d: omitting character %s" %(i+1, len(test_list), kw[i]))
                    if sleep:
                        time.sleep(random.randint(math.ceil(sleep_secs * .90), math.ceil(sleep_secs * 1.10)))
                    if has_censorship(test_list[i], cookie)[0] != "censored":
                        min_str += (kw[i])
                result_min_str, num_results_min_str = has_censorship(min_str, cookie)
                if result_min_str == "censored":  # minStr found properly
                    if verbose=="all" or verbose=="some":
                        print("the minimum phrase from '%s' is: '%s'" % (kw, min_str))
                    if insert:
                        insert_into_database(len(sqlite_to_df(sqlite_file)), min_str, date=date, result=result_min_str,
                                             source=r.source, num_results=num_results_min_str, notes=r.notes,
                                             sqlite_file=sqlite_file, test_number=test_number, is_canonical=True,
                                             orig_keyword=keyword_encoded)
                else:
                    print("Failed to find canonical phrase")

        if insert:
            insert_into_database(len(sqlite_to_df(sqlite_file)), keyword_encoded, date=date, result=result, source=r.source,
                                 num_results=num_results, notes=r.notes, sqlite_file=sqlite_file, test_number=test_number,
                                 is_canonical=False)
        if return_df:
            results_df = pd.concat([results_df,
                                    pd.DataFrame([{"date":date,
                                                   "datetime":datetime.now(),
                                                   "keyword":min_str if min_str is not None else r.keyword,
                                                   "result":result,
                                                   "source":r.source,
                                                   "num_results":num_results,
                                                   "test_number":test_number,
                                                   "is_canonical": True if min_str is not None else False,
                                                   "orig_keyword":r.keyword if min_str is not None else None
                                                 }])
                                   ])
        count+=1
        if sleep:
            time.sleep(random.randint(math.ceil(sleep_secs*.90), math.ceil(sleep_secs*1.10)))
    if insert:
        insert_into_database(int(test_keywords.index.max())+1,None,date=date,result="finished",source="_meta_",sqlite_file=sqlite_file,test_number=test_number)
    if return_df:
        return results_df


def split_search_query(query, cookie, sleep_secs=0, res_rtn=[], known_blocked=False, verbose=""):
    """
    Recursively halves a query and returns portions with blocked keywords as a list of strings.
    :param res_rtn: internal list holding found min keywords during recursive search, DO NOT SPECIFY.
    :param known_blocked: set to True to skip a redundant first-check if you know your query is blocked.
    :return: a list of one or more shortened keyword segments that trigger censorship
    """
    if len(query) <= 1:
        return [-1]
    if sleep_secs:
        time.sleep(random.randint(math.ceil(sleep_secs * .90), math.ceil(sleep_secs * 1.10)))
    if (not known_blocked) and verbose=='all':
        print('Recursively shortening... testing query: "%s"' %(query))
    if (not known_blocked) and has_censorship(query, cookie)[0] != "censored":  # known_blocked=True skips 1st check
        return [-1]
    else:
        mid = len(query) // 2
        left_half = query[:mid]
        right_half = query[mid:]
        left_res = split_search_query(left_half, cookie, sleep_secs, res_rtn, False, verbose)
        right_res = split_search_query(right_half, cookie, sleep_secs, res_rtn, False, verbose)
        if (left_res[0] == -1) and (right_res[0] == -1):
            res_rtn.append(query)
    return res_rtn
