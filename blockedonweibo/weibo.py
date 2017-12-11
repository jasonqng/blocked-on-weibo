#!/usr/bin/env python
# -*- coding: utf-8 -*-

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
from df2gspread import gspread2df as g2d
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

CENSORSHIP_PHRASE_UTF8 = '根据相关法律法规和政策'
CAPTCHA_PHRASE_UTF8 = '你的行为有些异常'
NO_RESULTS_PHRASE_UTF8 = '抱歉，未找到'
CENSORSHIP_PHRASE_DECODED = codecs.encode(CENSORSHIP_PHRASE_UTF8.decode('utf8'), 'unicode_escape')
CAPTCHA_PHRASE_DECODED = codecs.encode(CAPTCHA_PHRASE_UTF8.decode('utf8'), 'unicode_escape')
NO_RESULTS_PHRASE_DECODED = codecs.encode(NO_RESULTS_PHRASE_UTF8.decode('utf8'), 'unicode_escape')

def user_login(username=weibo_credentials.Creds().username,
                password=weibo_credentials.Creds().password,
                write_cookie=True):
    """
    Logs a user into Weibo and generates a cookie
    Returns a Requests session with cookie object
    Pulls username and password from weibo_credentials.py
    code from https://www.zhihu.com/question/29666539 with minor modifications
    """
    session = requests.Session()  
    url_prelogin = 'http://login.sina.com.cn/sso/prelogin.php?entry=weibo&callback=sinaSSOController.preloginCallBack&su=&rsakt=mod&client=ssologin.js(v1.4.5)&_=1364875106625'  
    url_login = 'http://login.sina.com.cn/sso/login.php?client=ssologin.js(v1.4.5)'  

    #get servertime,nonce, pubkey,rsakv  
    resp       = session.get(url_prelogin)  
    json_data  = re.findall(r'(?<=\().*(?=\))', resp.text)[0]
    data       = json.loads(json_data)  


    servertime = data['servertime']  
    nonce      = data['nonce']  
    pubkey     = data['pubkey']  
    rsakv      = data['rsakv']  

    # calculate su  
    su  = base64.b64encode(username.encode(encoding="utf-8"))  

    #calculate sp  
    rsaPublickey= int(pubkey,16)  
    key = rsa.PublicKey(rsaPublickey,65537)  
    message = str(servertime) +'\t' + str(nonce) + '\n' + str(password)  
    sp = binascii.b2a_hex(rsa.encrypt(message.encode(encoding="utf-8"),key))  
    postdata = {  
                        'entry': 'weibo',  
                        'gateway': '1',  
                        'from': '',  
                        'savestate': '7',  
                        'userticket': '1',  
                        'ssosimplelogin': '1',  
                        'vsnf': '1',  
                        'vsnval': '',  
                        'su': su,  
                        'service': 'miniblog',  
                        'servertime': servertime,  
                        'nonce': nonce,  
                        'pwencode': 'rsa2',  
                        'sp': sp,  
                        'encoding': 'UTF-8',  
                        'url': 'http://weibo.com/ajaxlogin.php?framelogin=1&callback=parent.sinaSSOController.feedBackUrlCallBack',  
                        'returntype': 'META',  
                        'rsakv' : rsakv,  
                        }
    resp = session.post(url_login,data=postdata)
    # print resp.headers
    #print(resp.content)
    login_url = re.findall(r'http://weibo.*&retcode=0',resp.text)
    #print(login_url)
    try:
        respo = session.get(login_url[0])
    except IndexError:
        raise AttributeError("Couldn't login. Check that your credentials are valid.")
    uid = re.findall('"uniqueid":"(\d+)",',respo.text)[0]
    url = "http://weibo.com/u/"+uid
    respo = session.get(url)
    if write_cookie:
        cookie_dict = session.cookies.get_dict()
        json.dump(cookie_dict, open(username + "_cookie.txt",'w'))
    return session

def has_censorship(keyword_encoded,
                cookies=None):

    """
    Function which actually looks up whether a search for the given keyword returns text
    which is displayed during censorship.
    Can handle unicode and strings
    Currently no CAPTCHA handling, though it is detected
    Returns string of 'censored','no_results','reset',or 'has_results'
    ('has_results' is actually not a garuantee; it's merely a lack of other censorship indicators)
    """
    url = ('http://s.weibo.com/weibo/%s&Refer=index' % keyword_encoded).encode('utf-8')    
    
    try:
        r = requests.get(url,cookies=cookies).text
        i = 1
        while True:
            if CAPTCHA_PHRASE_DECODED not in r:
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
    
    if CENSORSHIP_PHRASE_DECODED in r:
        return ("censored",None)
    elif NO_RESULTS_PHRASE_DECODED in r:
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
    
    if result is "censored":
        censored = True
    else:
        censored = False
        
    if result is "no_results":
        no_results = True
    else:
        no_results = False
        
    if result is "reset":
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

def get_keywords_from_source(location,
                keyword_col_name,
                source_name):
    """
    Generating the keyword lists to search on
    """
    test_keywords = pd.DataFrame()
    if '.csv' in location:
        s=requests.get(location).content
        test_df=pd.read_csv(io.StringIO(s.decode('utf-8')))
    elif "recommendation" in location:
        pass
    else:
        test_df = g2d.download(location,wks_name='Sheet2',col_names=True)
    if '.csv' in location:
        test_keywords['category'] = test_df.category
    test_keywords['keyword'] = test_df[keyword_col_name]
    test_keywords['source'] = source_name
    test_keywords['notes'] = None
    return test_keywords
    
def sqlite_to_df(sqlite_file,
                query="select * from results where source!='_meta_' or source is NULL;"):
    conn = sqlite3.connect(sqlite_file)
    df = pd.read_sql_query(query, conn)
    return df

def verify_cookies_work(cookie,
                return_full_response=False):
    """
    Returns True if cookies return profile indicator
    If no cookie or bad cookie is passed, you get a generic login page which doesn't have the indicator
    """
    if return_full_response:
        r = requests.get('http://s.weibo.com/weibo/%25E9%25AB%2598%25E8%2587%25AA%25E8%2581%2594&Refer=index',cookies=cookie).content
        return r
    r = requests.get('http://level.account.weibo.com/level/mylevel?from=profile1',cookies=cookie).text
    if "W_face_radius" in r:
        return True
    else:
        return False

def load_cookies(cookie_file=weibo_credentials.Creds().username + "_cookie.txt"):
    with open(cookie_file, 'r') as f:
        #cookie = ast.literal_eval(f.read())
        cookie = json.load(f)
    return cookie

def run(keywords,
                verbose='all',
                insert=True,
                sqlite_file=None,
                return_df=False,
                sleep=True,
                cookies=None,
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
            keyword_encoded = r.keyword.decode('utf-8')
        else:
            keyword_encoded = r.keyword

        if sqlite_file:
            if r.Index < len(sqlite_to_df(sqlite_file).query("date=='%s' & source=='%s' & test_number==%s & is_canonical!=1" % (date,source,test_number))) and continue_interruptions:
                continue
            if len(sqlite_to_df(sqlite_file).query(u"date=='%s' & source=='%s' & test_number==%s & keyword=='%s' & is_canonical!=1" % (date,source,test_number,keyword_encoded)))>0 and continue_interruptions:
                continue
        result,num_results = has_censorship(keyword_encoded,cookies)
        if verbose=="all":
            print(r.Index,keyword_encoded, result)
        elif verbose=="some" and (count%10==0 or count==0):
            print(r.Index,keyword_encoded, result)

        min_str = None
        if get_canonical and result == "censored":
            if verbose=="some" or verbose=="all":
                print("Found censored search phrase; determining canonical censored keyword set")
            sleep_recursive = sleep_secs if sleep is True else 0
            potential_kws = split_search_query(keyword_encoded, cookies, sleep_recursive, res_rtn=[], known_blocked=True, verbose=verbose)
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
                    if has_censorship(test_list[i], cookies)[0] != "censored":
                        min_str += (kw[i])
                result_min_str, num_results_min_str = has_censorship(min_str, cookies)
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


def split_search_query(query, cookies, sleep_secs=0, res_rtn=[], known_blocked=False, verbose=""):
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
    if (not known_blocked) and has_censorship(query, cookies)[0] != "censored":  # known_blocked=True skips 1st check
        return [-1]
    else:
        mid = len(query) // 2
        left_half = query[:mid]
        right_half = query[mid:]
        left_res = split_search_query(left_half, cookies, sleep_secs, res_rtn, False, verbose)
        right_res = split_search_query(right_half, cookies, sleep_secs, res_rtn, False, verbose)
        if (left_res[0] == -1) and (right_res[0] == -1):
            res_rtn.append(query)
    return res_rtn
