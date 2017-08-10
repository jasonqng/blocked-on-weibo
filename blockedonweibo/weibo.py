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
try:
    from urllib.parse import urlparse
except ImportError:
     from urlparse import urlparse
import rsa  
import json  
import binascii  
from . import weibo_credentials
### SETTINGS

### SETTINGS

sqlite_file = 'results.sqlite' # name of sqlite file to read from/write to
new_database = False # erases any existing sqlite file and generates an empty one to write to
verbose = "some" # 'none',some','all'
load_cookies = True # load cookies from disk (can load cookies without having to do fresh_log_in if cookies already exist)
fresh_log_in = False # perform a log in
write_cookies = False # save cookies and overwrite any existing cookies during log in
cookie_file = weibo_credentials.Creds().username + "_cookie.txt" # name of cookie file in case you want to specify

censorship_phrase_utf8 = '根据相关法律法规和政策'
censorship_phrase_decoded = codecs.encode(censorship_phrase_utf8.decode('utf8'), 'unicode_escape')

captcha_phrase_utf8 = '你的行为有些异常'
captcha_phrase_decoded = codecs.encode(captcha_phrase_utf8.decode('utf8'), 'unicode_escape')

no_results_phrase_utf8 = '抱歉，未找到'
no_results_phrase_decoded = codecs.encode(no_results_phrase_utf8.decode('utf8'), 'unicode_escape')

class Userlogin:  
    """
    Logs a user into Weibo and generates a cookie
    Returns a Requests session with cookie object
    Pulls username and password from weibo_credentials.py
    code from https://www.zhihu.com/question/29666539 with minor modifications
    """
    def userlogin(self,username,password,write_cookie=True):  
        session = requests.Session()  
        url_prelogin = 'http://login.sina.com.cn/sso/prelogin.php?entry=weibo&callback=sinaSSOController.preloginCallBack&su=&rsakt=mod&client=ssologin.js(v1.4.5)&_=1364875106625'  
        url_login = 'http://login.sina.com.cn/sso/login.php?client=ssologin.js(v1.4.5)'  
  
        #get servertime,nonce, pubkey,rsakv  
        resp = session.get(url_prelogin)  
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
        respo = session.get(login_url[0])  
        uid = re.findall('"uniqueid":"(\d+)",',respo.text)[0]  
        url = "http://weibo.com/u/"+uid  
        respo = session.get(url)
        if write_cookie:
            cookie_dict = session.cookies.get_dict()
            with open(username + "_cookie.txt", 'w') as f:
                f.write(cookie)
        return session

def has_censorship(keyword,cookies=None):
    """
    Function which actually looks up whether a search for the given keyword returns text
    which is displayed during censorship.
    Can handle unicode and strings
    Currently no CAPTCHA handling, though it is detected
    Returns string of 'censored','no_results','reset',or 'has_results'
    ('has_results' is actually not a garuantee; it's merely a lack of other censorship indicators)
    """
    if isinstance(keyword, str):
        url = 'http://s.weibo.com/weibo/%s&Refer=index' % keyword
    elif isinstance(keyword, unicode):
        url = ('http://s.weibo.com/weibo/%s&Refer=index' % keyword).encode('utf-8')    
    
    try:
        r = requests.get(url,cookies=cookie).text
        i = 1
        while True:
            if captcha_phrase_decoded not in r:
                break
            else:
                print "CAPTCHA", keyword
                time.sleep(300*i)
                i+=1
    except IOError:
        wait_seconds = random.randint(90, 100)
        print "connection reset, waiting %s" % wait_seconds
        time.sleep(wait_seconds)
        return "reset"

    num_results = re.findall(r'search_rese clearfix\\">\\n <span>\\u627e\\u5230(\d*)',r)
    if num_results:
        num_results = int(num_results[0])
    else:
        None
    
    if censorship_phrase_decoded in r:
        return ("censored",None)
    elif no_results_phrase_decoded in r:
        return ("no_results",None)
    else:
        return ("has_results",num_results)

def create_table(sqlite_file):
    """
    Generating a sqlite file for storing results
    Multi-index primary key on id, date, and source
    Set new_database to True in order to remove any existing file
    """
    conn = sqlite3.connect(sqlite_file)
    c = conn.cursor()
    c.execute('CREATE TABLE results (id int, date date, datetime datetime, keyword string, censored bool, no_results bool, reset bool, result string, source string, num_results int, notes string, PRIMARY KEY(id,date,source))')
    conn.commit()
    conn.close()
    

    

def insert_into_table(record_id,keyword,result,source,notes=None,num_results=None):
    """
    Writing the results to the sqlite database file
    """
    conn = sqlite3.connect(sqlite_file)
    conn.text_factory = str
    c = conn.cursor()
    
    dt = datetime.now()
    d = dt.date()
    
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

    query = """INSERT INTO results (id, date, datetime, keyword, censored, no_results, reset, result, source, num_results, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?);"""
    if isinstance(notes, list):
        notes = str(notes)
    c.execute(query,(record_id, d, dt, keyword, censored, no_results, reset, result, source, num_results, notes))

    conn.commit()
    conn.close()

def get_keywords_from_source(location,keyword_col_name,source_name,lxb_categories=None):
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
    if lxb_categories:
        mask = test_df.category.isin(lxb_categories)
        test_df = test_df[mask]
    if '.csv' in location:
        test_keywords['category'] = test_df.category
    test_keywords['keyword'] = test_df[keyword_col_name]
    test_keywords['source'] = source_name
    test_keywords['notes'] = None
    return test_keywords
    
def sqlite_to_df(sqlite_file):
    conn = sqlite3.connect(sqlite_file)
    df = pd.read_sql_query("select * from results where source!='_meta_';", conn)
    return df

def sqlite_to_df(sqlite_file):
    conn = sqlite3.connect(sqlite_file)
    df = pd.read_sql_query("select * from results;", conn)
    return df

def verify_cookies_work(cookie,return_full_response=False):
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

def run(test_keywords,verbose='some',insert=True,return_df=False,sleep=True):
    """
    Iterating through the keyword list and testing one at a time
    Handles when script or connection breaks; will pick up where it left off
    Set return_df to append each new result to a df in memory, which is returned at end of function
    verbose = 'some','all',or 'none'(technically anything besides 'some' or 'all' will not show anything)
    """
    count=0
    if return_df:
        results_df = pd.DataFrame()
    
    for r in test_keywords.itertuples():
        if insert and r.Index < len(sqlite_to_df(sqlite_file)):
            continue
        result,num_results = has_censorship(r.keyword)
        if verbose=="all":
            print r.Index,r.keyword, result
        elif verbose=="some" and (count%10==0 or count==0):
            print r.Index,r.keyword, result
        if insert:
            insert_into_table(len(sqlite_to_df(sqlite_file)),r.keyword,result,r.source,num_results,r.notes)
        if return_df:
            results_df = pd.concat([results_df,
                                    pd.DataFrame([{"date":datetime.now().date(),
                                                   "datetime":datetime.now(),
                                                   "keyword":r.keyword,
                                                   "result":result,
                                                   "source":r.source,
                                                   'num_results':num_results
                                                 }])
                                   ])
        count+=1
        if sleep:
            time.sleep(random.randint(13, 16))
    if insert:
        insert_into_table(int(test_keywords.index.max())+1,None,"finished","_meta_")
    if return_df:
        return results_df

"""
if fresh_log_in:
    session = Userlogin().userlogin(weibo_credentials.Creds().username,weibo_credentials.Creds().password)

if load_cookies:
    with open(cookie_file, 'r') as f:
        cookie = ast.literal_eval(f.read())
else:
    cookie = None
if new_database and os.path.isfile(sqlite_file):
    os.remove(sqlite_file)
if not os.path.isfile(sqlite_file):
    create_table(sqlite_file)

sample_keywords = pd.DataFrame(
    [{'keyword':'hello','Index':0,'source':'test'},
     {'keyword':'lxb','Index':1,'source':'test'},
     {'keyword':u'习胞子','Index':2,'source':'unicode'},
     {'keyword':'自由亚洲电台','Index':3,'source':'should reset'},
     {'keyword':'刘晓波','Index':4,'source':'string'},
     {'keyword':'dhfjkdashfjkasdhfsadsf87sadfhjfasdnf'}])

run(sample_keywords,verbose='none',insert=False,return_df=True)
"""