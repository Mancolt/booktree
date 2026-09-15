import requests
import json
import os
import pickle
import time
from pprint import pprint
import myx_classes
import myx_utilities
import myx_jsonlog


#MAM traffic rules: MAM sessions are IP/ASN-locked and rate-sensitive, so every HTTP request to MAM is spaced
#(Config/mam/min_interval_seconds, default 6) and the number of searches per run is bounded
#(Config/mam/max_queries_per_run, default 60). Cache hits cost nothing.
_lastMamRequest = 0.0
_mamQueriesThisRun = 0
_cookieTestedThisRun = False
_warnedKnobs = set()
_sleep = time.sleep
_now = time.time
DEFAULT_INTERVAL = 6.0
DEFAULT_BUDGET = 60


def _knob(cfg, key, default, lo, hi, cast):
    """A validated numeric config value: invalid or out of range falls back to the default with one warning."""
    raw = cfg.get(f"Config/mam/{key}")
    if raw is None or raw == "":
        return default
    try:
        value = cast(float(raw))
        if value != value or not (lo <= value <= hi):
            raise ValueError("out of range")
        return value
    except (TypeError, ValueError, OverflowError):
        if key not in _warnedKnobs:
            _warnedKnobs.add(key)
            print(f"Ignoring invalid Config/mam/{key}={raw!r}, using {default}")
        return default


def _throttle(cfg):
    """Keep at least min_interval_seconds between consecutive HTTP requests to MAM."""
    global _lastMamRequest
    interval = _knob(cfg, "min_interval_seconds", DEFAULT_INTERVAL, 0.0, 3600.0, float)
    wait = _lastMamRequest + interval - _now()
    if wait > 0:
        _sleep(wait)
    _lastMamRequest = _now()


def _budgetLeft(cfg):
    limit = _knob(cfg, "max_queries_per_run", DEFAULT_BUDGET, 0, 100000, int)
    return limit <= 0 or _mamQueriesThisRun < limit


def resetRunCounters():
    global _mamQueriesThisRun, _lastMamRequest, _cookieTestedThisRun
    _mamQueriesThisRun = 0
    _lastMamRequest = 0.0
    _cookieTestedThisRun = False


#MAM Functions
def searchMAM(cfg, titleFilename, authors, extension, refresh=False):
    global _mamQueriesThisRun
    #Config
    session = cfg.get("Config/session")
    log_path = cfg.get("Config/log_path")
    verbose = bool(cfg.get("Config/flags/verbose"))

    ebook = bool(cfg.get("Config/flags/ebooks"))
    audiobook = not (ebook)
    
    #put paren around authors and titleFilename
    if len(authors):
        authors = f'({authors})'

    if len(titleFilename):
        titleFilename = f'("{escape_string(titleFilename)}")'

    search = f'{authors} {titleFilename} {extension} @dummy mamDummy'

    #cache results for this search string
    cacheKey=myx_utilities.getHash(search)
    
    cachedResults = None
    if myx_utilities.isCached(cacheKey, "mam", cfg, refresh=refresh):
        #this search has been done before, load results from cache
        cachedResults = myx_utilities.loadFromCache(cacheKey, "mam", cfg)
        if not isinstance(cachedResults, dict):
            print(f"Ignoring malformed MAM cache entry {cacheKey}, searching again")
            cachedResults = None

    if cachedResults is not None:
        data = cachedResults.get("data")
        myx_jsonlog.noteQuery("mam", cacheKey, True, len(data or []), text=search)
        return data

    elif not _budgetLeft(cfg):
        print(f"MAM query budget for this run exhausted ({_mamQueriesThisRun} searches): skipping MAM search")
        myx_jsonlog.noteQuery("mam", cacheKey, False, 0, text=search, skipped="budget")
        return None

    else:
        #save cookie for future use
        cookies_filepath = os.path.join(log_path, 'cookies.pkl')
        sess = requests.Session()

        #a cookie file exists, use that
        if os.path.exists(cookies_filepath):
            cookies = pickle.load(open(cookies_filepath, 'rb'))
            sess.cookies = cookies
        else:
            #assume a session ID is passed as a parameter
            sess.headers.update({"cookie": f"mam_id={session}"})

        #test session and cookie (once per run: every request to MAM costs a throttle slot)
        global _cookieTestedThisRun
        try:
            if not _cookieTestedThisRun:
                _throttle(cfg)
                r = sess.get('https://www.myanonamouse.net/jsonLoad.php', timeout=5)  # test cookie
                if r.status_code != 200:
                    raise Exception(f'Error communicating with API. status code {r.status_code} {r.text}')
                _cookieTestedThisRun = True
                # save cookies for later
                with open(cookies_filepath, 'wb') as f:
                    pickle.dump(sess.cookies, f)
                    print (f"Cookie updated...")

            mam_categories = []
            if audiobook:
                mam_categories.append(13) #audiobooks
                mam_categories.append(16) #radio
            if ebook:
                mam_categories.append(14)
            if not mam_categories:
                return None
            
            params = {
                "tor": {
                    "text": search,  # The search string.
                    "srchIn": {
                        "title": "true",
                        "author": "true",
                        "fileTypes": "true",
                        "filenames": "true"
                    },
                    "main_cat": mam_categories
                },
                "perpage":50
            }

            if (verbose):
                print(f'Search: {search}')

            try:
                _throttle(cfg)
                _mamQueriesThisRun += 1
                r = sess.post('https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php', json=params, timeout=20)
                if r.text == '{"error":"Nothing returned, out of 0"}':
                    #an empty answer is a real answer: cache it under the short "empty" TTL (myx_cache)
                    myx_utilities.cacheMe(cacheKey, "mam", {"data": [], "total": 0, "found": 0, "perpage": 50, "start": 0}, cfg)
                    myx_jsonlog.noteQuery("mam", cacheKey, False, 0, text=search)
                    return None
                if r.status_code != 200:
                    raise Exception(f'search failed with status {r.status_code}')

                results = r.json()
                data = results.get("data") if isinstance(results, dict) else None
                if data is None:
                    raise Exception(f'unexpected MAM answer: {str(results)[:120]}')

                #cache every successful answer; myx_cache gives answers without a snatched entry the short TTL
                myx_utilities.cacheMe(cacheKey, "mam", results, cfg)
                myx_jsonlog.noteQuery("mam", cacheKey, False, len(data), text=search)
                return data
            
            except Exception as e:
                print(f'error searching MAM {e}')
        except Exception as e:
            print(f'error searching MAM {e}')
            
    return None

def getMAMBook(cfg, titleFilename="", authors="", extension="", refresh=False):
    books=[]
    mamBook=searchMAM(cfg, titleFilename, authors, extension, refresh=refresh)
    if (mamBook is not None):
        for b in mamBook:
            #pprint(b)
            book=myx_classes.Book()
            book.init()
            if 'asin' in b: 
                book.asin=str(b["asin"])
            if 'title' in b: 
                book.title=str(b["title"])
            if 'author_info'in b:
                #format {id:author, id:author}
                if len(b["author_info"]):
                    authors = json.loads(b["author_info"])
                    for author in authors.values():
                        book.authors.append(myx_classes.Contributor(str(author)))
            if 'narrator_info'in b:
                #format {id:narrator, id:narrator}
                if ((not b["narrator_info"] is None) and len(b["narrator_info"])):
                    narrators = json.loads(b["narrator_info"])
                    for narrator in narrators.values():
                        book.narrators.append(myx_classes.Contributor(str(narrator)))
            if 'series_info'in b:
                #format {"35598": ["Kat Dubois", "5"]}
                if ((not b["series_info"] is None) and len(b["series_info"])):
                    series_info = json.loads(b["series_info"])
                    for series in series_info.values():
                        s=list(series)
                        seriesName = str(s[0])
                        seriesName = seriesName.replace("&#039;", "'")
                        book.series.append(myx_classes.Series(seriesName, s[1]))
            if 'lang_code' in b:
                book.language=myx_utilities.getLanguage((b["lang_code"]))
            if 'my_snatched' in b:
                book.snatched=bool((b["my_snatched"])) 
            
            if book.snatched:
                books.append(book)

    return books

def testSessionCookie(mySession, cfg=None):
    isSessionCookieValid = False

    #test session and cookie
    #print (f"Cookie: {mySession}")
    try:
        #Hit cookieCheck API, https://www.myanonamouse.net/json/checkCookie.php
        _throttle(cfg) if cfg is not None else None
        r = mySession.get('https://www.myanonamouse.net/json/checkCookie.php', timeout=5)  # test cookie    

        if r.status_code != 200:
            print(f'Error communicating with API. status code {r.status_code} {r.text}')
        else:
            print (f"Successfully checked cookie: {r.text}")
            isSessionCookieValid = True

    except Exception as e:
        print(f'Checking MAM Cookie {e}')

    return isSessionCookieValid

def checkMAMCookie(cfg):
    #Config
    session = cfg.get("Config/session")
    log_path = cfg.get("Config/log_path")

    isCookieValid = False
    useConfigSession = False
    cookies_filepath = os.path.join(log_path, 'cookies.pkl')
    sess = requests.Session()

    #Check if a cookie file exists
    if os.path.exists(cookies_filepath):
        print (f"Checking if current cookie file is still valid...")
        #If it does, create a session, using this cookie
        cookies = pickle.load(open(cookies_filepath, 'rb'))
        sess.cookies = cookies
        isCookieValid = testSessionCookie (sess, cfg)

        #if the session cookie is NOT Valid
        if (not isCookieValid):
            #delete the file
            os.remove(cookies_filepath)
            
            print (f"Found an existing cookie file, but it was invalid. Checking session ID from config...")
            useConfigSession = True
    else:
        #Cookie File not found, use the session ID from Config
        print (f"Cookie file not found, checking session ID from config...")
        useConfigSession = True

    if (useConfigSession):
        if (session is not None) and len(session):
            sess.headers.update({"cookie": f"mam_id={session}"})
            isCookieValid = testSessionCookie (sess, cfg)

        else:
            print (f"No session ID found in the config... Please go to MAM Preferences > Security to create a new session")
        

    return isCookieValid

def escape_string(input_string):  
    """Escapes special characters in a string by prefixing them with a backslash.

    Args:  
        input_string: The string to escape.

    Returns:  
        A new string with the special characters escaped.  
    """  
    escaped_string = ""  
    special_chars = ['!', '"', '$', "'", '(', ')', '-', '/', '<', '@', '\\', '^', '|', '~']  
    for char in input_string:  
        if char in special_chars:  
            escaped_string += "\\" + char  
        else:  
            escaped_string += char  

    return escaped_string  

