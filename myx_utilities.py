
import unicodedata
from thefuzz import fuzz
from pprint import pprint
import os, sys, subprocess, shlex, re
from glob import iglob, glob
import mimetypes
import csv
import json
import hashlib
import tempfile
from xml.sax.saxutils import escape as _xml_escape
from langcodes import *
import myx_classes
import myx_cache
import myx_jsonlog

##ffprobe
def probe_file(filename):
    #ffprobe -loglevel error -show_entries format_tags=artist,album,title,series,part,series-part,isbn,asin,audible_asin,composer -of default=noprint_wrappers=1:nokey=0 -print_format compact "$file")
    cmnd = ['ffprobe','-loglevel','error','-show_entries','format_tags:format=duration', '-show_format', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=0', '-print_format', 'json', self.fullPath]
    p = subprocess.Popen(cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err =  p.communicate()
    print(json.loads(out))
    return json.loads(out)

#Utilities
def getList(items, delimiter=",", encloser="", stripaccents=True):
    enclosedItems=[]
    for item in items:
        if type(item) == myx_classes.Contributor:
            enclosedItems.append(f"{encloser}{cleanseAuthor(item.name)}{encloser}")
        else:
            if type(item) == myx_classes.Series:
                enclosedItems.append(f"{encloser}{cleanseSeries(item.name)}{encloser}")
            else:
                enclosedItems.append(f"{encloser}{item.name}{encloser}")

    return delimiter.join(enclosedItems)

def cleanseAuthor(author):
    #remove some characters we don't want on the author name
    stdAuthor=strip_accents(author)

    #remove some characters we don't want on the author name
    for c in ["- editor", "- contributor", " - ", "'"]:
        stdAuthor=stdAuthor.replace(c,"")

    #replace . with space, and then make sure that there's only single space between words)
    stdAuthor=" ".join(stdAuthor.replace("."," ").split())
    return stdAuthor

def cleanseTitle(title="", stripaccents=True, stripUnabridged=False):
    #remove (Unabridged) and strip accents
    stdTitle=str(title)

    for w in [" (Unabridged)", "m4b", "mp3", ",", "- "]:
        stdTitle=stdTitle.replace(w," ")
    
    if stripaccents:
        stdTitle = strip_accents(stdTitle)

    #remove Book X
    stdTitle = re.sub (r"\bBook(\s)?(\d)+\b", "", stdTitle, flags=re.IGNORECASE)

    # remove any subtitle that goes after a :
    stdTitle = re.sub (r"(:(\s)?([a-zA-Z0-9_'\.\s]{2,})*)", "", stdTitle, flags=re.IGNORECASE)

    return stdTitle

def standardizeAuthors(mediaPath, dryRun=False):
    #get all authors from the source path
    for f in iglob(os.path.join(mediaPath,"*"), recursive=False):
        #ignore @eaDir
        if (f != os.path.join(mediaPath,"@eaDir")):
            oldAuthor=os.path.basename(f)
            newAuthor=cleanseAuthor(oldAuthor)
            if (oldAuthor != newAuthor):
                print(f"Renaming: {f} >> {os.path.join(os.path.dirname(f), newAuthor)}")
                if (not dryRun):
                    try:
                        os.path(f).rename(os.path.join(os.path.dirname(f), newAuthor))
                    except Exception as e:
                        print (f"Can't rename {f}: {e}")

def fuzzymatch(x:str, y:str):
    newX = x
    newY = y
    newZ = {"partial" : 0, "token_sort" : 0, "ratio" : 0}
    #remove .:_-, for fuzzymatch
    for c in [".", ":", "_", "-", "[", "]", "'"]:
        newX = newX.replace (c, "")
        newY = newY.replace (c, "")

    if (len(newX) and len(newY)):
        newZ["partial"]=fuzz.partial_ratio(newX, newY)
        newZ["token_sort"]=fuzz.token_sort_ratio(newX, newY)
        newZ["ratio"]=fuzz._ratio(newX, newY)

    return newZ
    
def optimizeKeys(cfg, keywords, delim=" "):
    #Config Variables
    kw_ignore = cfg.get("Config/tokens/kw_ignore")
    kw_ignore_words = cfg.get("Config/tokens/kw_ignore_words")

    #keywords is a list of stuff, we want to convert it in a comma delimited string
    kw=[]
    for k in keywords:
        for c in kw_ignore: #[".", ":", "_", "[", "]", "{", "}", ",", ";", "(", ")"]:
            k = k.replace(c, " ")

        #print(k)
        #parse this item "-"
        for i in k.split("-"):
            #parse again on spaces
            #print(i)
            for j in i.split():
                #print(j)
                #if it's numeric like 02, make it an actual digit
                if (len(j) > 1):
                    lcj = j.lower()
                    #if not an article, or a word in the ignore list
                    if lcj not in kw_ignore_words: #["the","and","m4b","mp3","series","audiobook","audiobooks", "book", "part", "track", "novel"]:
                        #if not CD or DISC XX"
                        if not (re.search (r"cd\s?\d+", j, re.IGNORECASE) or  re.search (r"disc\s?\d+", j, re.IGNORECASE)):
                            #if not a number
                            if not (re.search (r"\d+", j, re.IGNORECASE)):
                                #if it's not already in the list
                                if lcj not in kw:
                                    kw.append(j.lower())

    #now return comma delimited string
    return delim.join(kw)

def getParentFolder(file, source):
    #We normally assume that the file is in a folder, but some files are NOT in a subfolder
    # relPath = os.path.relpath(file, source).split("/")
    parent=os.path.dirname(file)
    #check if the parent folder matches the source folder
    if (parent == source):
        #this file is bad and has no parent folder, use the filename as the parent folder
        return os.path.basename(file)
    else:
        return (parent.split(os.sep)[-1])

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                    if unicodedata.category(c) != 'Mn')

def createHardLinks(bookFiles, targetFolder="", dryRun=False):
    #hard link all the books in the list
    for f in bookFiles:
        #use Audible metadata or ID3 metadata
        if f.isMatched:
            book=f.audibleMatch
        else:
            book=f.ffprobeBook

        #if there is a book
        if (book is not None):
            #if a book belongs to multiple series, hardlink them to tall series
            for p in f.getTargetPaths(book):
                prefix=""
                if (not dryRun):
                    f.hardlinkFile(f.sourcePath, os.path.join(targetFolder, p))
                else:
                    prefix = "[Dry Run] : "
                print (f"{prefix}Hardlinking {f.sourcePath} to {os.path.join(targetFolder,p)}")
            print("\n", 40 * "-", "\n")

def logBookRecords(logFilePath, bookFiles, cfg):

    write_headers = not os.path.exists(logFilePath)
    with open(logFilePath, mode="a", newline="", errors='ignore', encoding='utf-8') as csv_file:
        try:
            for f in bookFiles:
                #get book records to log
                if (f.isMatched):
                    row=f.getLogRecord(f.audibleMatch, cfg)
                else:
                    row=f.getLogRecord(f.ffprobeBook, cfg)
                #get fieldnames
                row["matches"]=len(f.audibleMatches)
                fields=row.keys()

                #create a writer
                writer = csv.DictWriter(csv_file, fieldnames=fields)
                if write_headers:
                    writer.writeheader()
                    write_headers=False
                writer.writerow(row)

        except csv.Error as e:
            print(f"file {logFilePath}: {e}")

def openLogForAppend(path):
    """Append handle that does not follow a symlink planted at the log path (same protection as the JSON log)."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o644), mode="a", newline="", errors='ignore', encoding='utf-8')


def logBooks(logFilePath, books, cfg):
    if len(books):
        write_headers = not os.path.exists(logFilePath)
        with openLogForAppend(logFilePath) as csv_file:
            try:
                fields=getLogHeaders()
                #pprint (fields)
                for book in books:
                    for file in book.files:
                        row=book.getLogRecord(file,cfg)
                        #pprint(row)
                        #create a writer
                        writer = csv.DictWriter(csv_file, fieldnames=fields)
                        if write_headers:
                            writer.writeheader()
                            write_headers=False
                        writer.writerow(row)

            except csv.Error as e:
                print(f"file {logFilePath}: {e}")

def logMyLibrary (cfg, logFilePath, books):
    if len(books):
        write_headers = not os.path.exists(logFilePath)
        with open(logFilePath, mode="a", newline="", errors='ignore', encoding='utf-8') as csv_file:
            try:
                fields=getLogHeaders()
                #pprint (fields)
                for book in books:
                    for file in book.files:
                        row=book.getLogRecord(file,cfg)
                        row["mamCount"] = len (book.mamIDs)
                        row["mam-asin"] = book.mamIDs
                        #pprint(row)
                        #create a writer
                        writer = csv.DictWriter(csv_file, fieldnames=fields)
                        if write_headers:
                            writer.writeheader()
                            write_headers=False
                        writer.writerow(row)

            except csv.Error as e:
                print(f"file {logFilePath}: {e}")

def isCollection (bookFile, source_path):
    #we assume that most books are formatted this way /Book/Files.m4b
    #we assume that this is a collection, if the file is 3 levels deep, /Book/Another Book or CD/Files.m4b

    relPath = os.path.relpath(bookFile, source_path).split(os.sep)
    return (len(relPath) > 2)

def printDivider (char="-", length=40):
    print("\n", length * char, "\n")
    
def removeGA (author:str):
    #remove Graphic Audio and special characters like ()[]
    cleanAuthor = author.replace("GraphicAudio","").replace("[","").replace("]","")
    return cleanAuthor.strip()

def pathComponent(p):
    """One folder name rendered from a target_path template. pathvalidate's sanitize_filename leaves "." and ".."
    alone, so a series or title of ".." from Audible/MAM (or a stray template) would walk out of the folder it
    belongs in. Such a name becomes "_"; a name that is only dots is treated the same."""
    p = p.strip()
    if p and p.strip(".") == "":
        return "_"
    return p

def assertUnderRoot(target, roots):
    """Raise ValueError unless `target` (which need not exist yet) resolves to a path inside one of `roots`.
    Both sides are resolved through symlinks, so a media root that is itself a symlink is fine and a folder
    already inside the library that links elsewhere is not. A root of "/" contains everything."""
    real = os.path.realpath(target)
    for root in roots:
        r = os.path.realpath(root)
        if os.path.commonpath([r, real]) == r:
            return target
    raise ValueError(f"target path {target!r} is outside the media root(s) {list(roots)!r}")

def cleanseSeries(series):
    #remove colons
    cleanSeries = series
    for c in [":", "'"]:
        cleanSeries = cleanSeries.replace (c, "")

    return cleanSeries.strip()

def readLog(logFilePath, books):
    if os.path.exists(logFilePath):
        with open(logFilePath, newline="", errors='ignore', encoding='utf-8',) as csv_file:
            try:
                reader = csv.reader(csv_file,)
                for row in reader:
                    ##Create a new Book
                    print(row)
                    print(reader.fieldnames)

            except csv.Error as e:
                print(f"file {logFilePath}: {e}")

def getLogHeaders():
    headers=['book', 'file', 'paths', 'isMatched', 'isHardLinked', 'mamCount', 'audibleMatchCount', 'metadatasource', 'id3-matchRate', 'id3-asin', 'id3-title', 'id3-subtitle', 'id3-publisher', 'id3-length', 'id3-duration', 'id3-series', 'id3-authors', 'id3-narrators', 'id3-seriesparts', 'id3-language', 'mam-matchRate', 'mam-asin', 'mam-title', 'mam-subtitle', 'mam-publisher', 'mam-length', 'mam-duration', 'mam-series', 'mam-authors', 'mam-narrators', 'mam-seriesparts', 'mam-language', 'adb-matchRate', 'adb-asin', 'adb-title', 'adb-subtitle', 'adb-publisher', 'adb-length', 'adb-duration', 'adb-series', 'adb-authors', 'adb-narrators', 'adb-seriesparts', 'adb-language', 'sourcePath', 'mediaPath']
                    
    return dict.fromkeys(headers)

# characters that XML 1.0 does not allow anywhere, even escaped (control chars other than tab/LF/CR, surrogates)
_XML_INVALID_CHARS = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def xmlText(value):
    """Return value as XML character data: &, <, > escaped and invalid control characters removed.

    Everything that ends up between OPF tags (title, publisher, creator names, ASIN, ...) must go through
    here; Audiobookshelf rejects the whole metadata.opf on the first bare '&' (e.g. 'Little, Brown & Company')."""
    return _xml_escape(_XML_INVALID_CHARS.sub("", "" if value is None else str(value)))


def xmlAttr(value):
    """Return value for use inside a quoted XML attribute (both quote styles escaped)."""
    return _xml_escape(_XML_INVALID_CHARS.sub("", "" if value is None else str(value)), {"'": "&apos;", '"': "&quot;"})


def xmlCData(value):
    """Return value for use inside a CDATA section: a literal ']]>' would terminate the section early."""
    return _XML_INVALID_CHARS.sub("", "" if value is None else str(value)).replace("]]>", "]]]]><![CDATA[>")


_OPF_TOKEN_RE = re.compile("__(AUTHORS|TITLE|SUBTITLE|DESCRIPTION|PUBLISHER|YEAR|NARRATORS|ASIN|SERIES|LANGUAGE|GENRES|TAGS)__")


def renderOPF(book):
    """Render the metadata.opf text for a book from templates/booktemplate.opf (pure: no I/O besides the template)."""
    opfTemplate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "booktemplate.opf")
    with open(opfTemplate, mode='r', encoding='utf-8') as file:
        template = file.read()

    # - Author -
    authors = ""
    for author in book.authors:
        authors += f"\t<dc:creator opf:role='aut'>{xmlText(author.name)}</dc:creator>\n"

    # - Narrator -
    narrators = ""
    for narrator in book.narrators:
        narrators += f"\t<dc:creator opf:role='nrt'>{xmlText(narrator.name)}</dc:creator>\n"

    # - Series -
    series = ""
    for s in book.series:
        series += f"\t<ns0:meta name='calibre:series' content='{xmlAttr(s.name)}' />\n"
        series += f"\t<ns0:meta name='calibre:series_index' content='{xmlAttr(s.part)}' />\n"

    # - Genres / Tags - (CDATA, as upstream did)
    genres = ""
    for g in set(book.genres):
        genres += f"\t<dc:subject><![CDATA[{xmlCData(g)}]]></dc:subject>\n"
    tags = ""
    for t in set(book.tags):
        tags += f"\t<dc:tag><![CDATA[{xmlCData(t)}]]></dc:tag>\n"

    # All tokens are substituted in ONE pass with a callable replacement: the inserted metadata is never
    # rescanned, so a value that itself contains a token (an author named "__DESCRIPTION__") cannot pull
    # another, differently-escaped field into its place; and a backslash sequence in the metadata (e.g.
    # '\1') is inserted literally instead of being read as a regex group reference, which used to raise
    # and leave the book with no OPF at all
    values = {
        "__AUTHORS__": authors,
        "__TITLE__": xmlText(book.title),
        "__SUBTITLE__": xmlText(book.subtitle),
        "__DESCRIPTION__": xmlCData(book.description),
        "__PUBLISHER__": xmlText(book.publisher),
        "__YEAR__": xmlText("" if book.publishYear is None else str(book.publishYear)[0:4]),
        "__NARRATORS__": narrators,
        "__ASIN__": xmlText(book.asin),
        "__SERIES__": series,
        "__LANGUAGE__": xmlText(book.language),
        "__GENRES__": genres,
        "__TAGS__": tags,
    }
    return _OPF_TOKEN_RE.sub(lambda m: values[m.group(0)], template)


def createOPF(book, path):
    try:
        # --- Generate .opf Metadata file ---
        template = renderOPF(book)

        opfFile=os.path.join(path, "metadata.opf")
        with open(opfFile, mode='w', encoding='utf-8') as file:
            file.write(template)
    except Exception as e:
        print (f"Error creating OPF file {path}: {e}")

    return

def getHash(key):
    #surrogateescape: a file name that is not valid UTF-8 (os.listdir keeps the raw bytes as surrogates) must not abort the run
    return hashlib.sha256(str(key).encode(encoding="utf-8", errors="surrogateescape")).hexdigest()

def isCached(key, category, cfg, refresh=False):
    """Is there a usable cache entry? Search caches (audible, mam) also have to be fresh (myx_cache TTLs);
    refresh=True bypasses the cache for this lookup (--refresh / hint "refresh")."""
    #Config
    verbose = bool(cfg.get("Config/flags/verbose"))
    no_cache = bool(cfg.get("Config/flags/no_cache"))
    
    if verbose:
        print (f"Checking cache: {category}/{key}...")
    
    #Check if this book's hashkey exists in the cache, if so - it's been processed
    bookFile = os.path.join(getCachePath(cfg), "__cache__", category, key)
    if no_cache or refresh:
        return False
    if not os.path.exists(bookFile):
        return False
    state, age, empty, ttl = myx_cache.entryState(bookFile, category, cfg)
    if state == "expired":
        print(f"Cache entry expired: {category}/{key} ({age:.0f}h old, {'empty' if empty else 'positive'} result, ttl {ttl:.0f}h)")
        return False
    return True
    
def cacheMe(key, category, content, cfg):
    #Config
    verbose = bool(cfg.get("Config/flags/verbose"))

    #create the cache file (unique temp name, then rename: a concurrent reader never sees a torn file and two
    #containers sharing the cache volume cannot collide on the temp name)
    bookFile = os.path.join(getCachePath(cfg), "__cache__", category, key)
    fd, tmpFile = tempfile.mkstemp(dir=os.path.dirname(bookFile), prefix=f"{key}.", suffix=".tmp")
    try:
        with os.fdopen(fd, mode="w", encoding='utf-8', errors='ignore') as file:
            file.write(json.dumps(content))
        os.replace(tmpFile, bookFile)
    except BaseException:
        try:
            os.unlink(tmpFile)
        except OSError:
            pass
        raise

    if verbose:
        print(f"Caching {key} in File: {bookFile}")
    return os.path.exists(bookFile)        

def loadFromCache(key, category, cfg):
    #return the content from the cache file; None when it cannot be read or parsed (callers treat that as a miss)
    bookFile = os.path.join(getCachePath(cfg), "__cache__", category, key)
    try:
        with open(bookFile, mode='r', encoding='utf-8') as file:
            f = file.read()
        return json.loads(f)
    except (OSError, ValueError):
        return None
    
#the disc-folder vocabulary must cover everything myx_names.DISC_FOLDER groups into one book (cd/disc/disk/part N),
#or two grouped discs with the same file names would be filed into one flat folder and the second silently skipped.
#cd/disc stay unanchored as upstream had them ("Title Disc 2" is a disc folder too); part needs a word boundary
#so that a title such as "Counterpart 2" is not mistaken for one.
MULTI_CD = re.compile(r"(?:cd|disc|disk)\s?\d+|\bpart\s?\d+", re.IGNORECASE)


def isMultiCD(parent):
    return MULTI_CD.search(parent) is not None

def isGraphicAudio(author):
    m = re.search(r"graphic[\s]?audio[\s]?(llc[.]?)*", author.lower())
    #print (f"Is {author} = 'Graphic Audio LLC.'? {m}")
    return (m is not None)

def isThisMyAuthorsBook (authors, book, cfg):
    #Config
    verbose = bool(cfg.get("Config/flags/verbose"))

    found=False
    for author in authors:
        if isGraphicAudio(author.name): 
            continue
        else:
            for bauthor in book.authors:
                if isGraphicAudio(bauthor.name): 
                    continue
                else:
                    if verbose:
                        print (f"Checking if {book.title} is {authors}'s book: {book.authors}")

                    #print (f"Author: {author.name} = {bauthor.name}? {(author.name.replace(' ', '') == bauthor.name.replace(' ', ''))}")
                    if (cleanseAuthor(author.name).replace(" ", "") == cleanseAuthor(bauthor.name).replace(" ", "")):
                        #print ("found\n")
                        found=True
                        break
        
        if found: break

    return found

def isThisMyBookTitle (title, book, cfg):
    #Config
    matchrate = int(cfg.get("Config/matchrate"))
    verbose = bool(cfg.get("Config/flags/verbose"))

    mytitle = cleanseTitle(title)
    thisTitle = cleanseTitle(book.title)
    thisSeriesTitle = thisTitle

    if len(book.series):
        thisSeries = cleanseSeries(book.series[0].name)
        thisSeriesTitle = " - ".join([thisSeries, thisTitle])
    
    matchname = fuzzymatch(mytitle, thisTitle)
    matchseriesname = fuzzymatch(mytitle, thisSeriesTitle)
    if verbose:
        print (f"Checking if {thisTitle} or {thisSeriesTitle} matches my book {mytitle}: {matchname} or {matchseriesname}")

    #see if any of the fuzzy match scores are within guidance
    match=False
    for k in matchname.keys():
        if matchname[k] >= matchrate:
            match=True
            break

    if not match:
        for k in matchseriesname.keys():
            if matchseriesname[k] >= matchrate:
                match=True
                break

    return match
    
_warnedTitlePatterns = False


def titlePatterns(cfg):
    """Config/tokens/title_patterns as usable regexes. In JSON, "\\bpart\\b" written as "\bpart\b" is the word
    "part" between two BACKSPACE characters, which never matches anything; upstream's template shipped exactly that,
    so a backspace in a pattern is repaired to the word boundary it was meant to be (once, with a note)."""
    global _warnedTitlePatterns
    patterns = cfg.get("Config/tokens/title_patterns") or []
    fixed = []
    for p in patterns:
        p = str(p)
        if "\b" in p:
            p = p.replace("\b", r"\b")
            if not _warnedTitlePatterns:
                _warnedTitlePatterns = True
                print('Note: Config/tokens/title_patterns contains "\\b" written as a JSON backspace; '
                      'reading it as a word boundary. Write it as "\\\\b" in the config file.')
        fixed.append(p)
    return fixed


def getAltTitle(parent, book, cfg):
    #Config
    verbose = bool(cfg.get("Config/flags/verbose"))
    patterns = titlePatterns(cfg)
    skipSeries = bool (cfg.get ("Config/tokens/skip_series"))

    stop = False
    words = []
    
    #start with title
    altTitle = cleanseTitle(book.title).lower()

    #if title is blank, use series?
    if (len(altTitle) == 0) and (len(book.series)):
        altTitle = cleanseTitle(book.series[0].name)
        if len(altTitle) : skipSeries = True

    print (f"Processing {altTitle}")
    while True:
        #remove authors name in title
        for a in book.authors:
            altTitle = re.sub(re.escape(a.name), " ", altTitle, flags=re.IGNORECASE)
            #print (f"remove {book.authors} >> {altTitle}")

        #remove series name in title
        if (not skipSeries):
            for s in book.series:
                altTitle = re.sub(re.escape(s.name), " ", altTitle, flags=re.IGNORECASE)
            #print (f"remove {book.series} >> {altTitle}")

        #remove the numbers
        altTitle = re.sub(r"\b\d*\b", "", altTitle, flags=re.IGNORECASE)
        #print (f"remove digits >> {altTitle}")

        #remove extra characters (there really should'nt be : here) 
        for c in patterns:
            #c = str.replace (c, "\\", "\")
            regx =re.compile(c, re.IGNORECASE)
            #print (f"{regx} = {altTitle}")
            altTitle = regx.sub(" ", altTitle)
            #altTitle = altTitle.replace (c, " ")

        for c in ["'", "-"]:
            altTitle = altTitle.replace (c, "")
            #print (f"remove symbols >> {altTitle}")

        for w in altTitle.split():
            if w not in words:
                words.append(w)

        #print (f"remove spaces >> {' '.join(words)}")
        if (len(words)) or (stop):
            altTitle = ' '.join(words)
            book.title = altTitle

            if verbose:
                print (f"Found alternative title: {altTitle}")
            break

        else:
            altTitle = cleanseTitle(parent).lower()
            stop = True

    #join the title back
    if len (words):
        return book.title
    else:
        return ""

def getLanguage(code):
    lang = "english"
    try: 
        lang = Language.get(code).display_name()

    except:
        print ("Unable to get display name for Language: {code}, defaulting to English")
    
    return lang.lower()

def isMultiBookCollection(filePath):
    #Is this MAM result a collection
    isMBC = False
    #if the # of paths from source path is 3 or more
    path, file = os.path.split(filePath)    
    #how deep is it from the source?
    filedepth = len(path.split(os.sep)) + 1
    #print (f"File depth of {filePath} is {filedepth}")
    # if the filedepth from source is 3 levels down, assume it's a multibook collection
    isMBC = (filedepth >= 3)
    return isMBC

def initMetadataJSON(book, path):
    print (f"Creating a metadata.json file in {path}")
    
    metadataTemplate=os.path.join(os.getcwd(), "templates/metadata.json") 
    if os.path.exists(metadataTemplate):
        with open(metadataTemplate, encoding='utf-8') as json_file:
            #Initialize the metadata with the template
            book.metadata = json.loads(json_file.read())

def getLogPath(cfg):
    #make sure log_path exists
    log_path=cfg.get("Config/log_path")

    if (log_path is None) or (len(log_path)==0):
        log_path=os.path.join(os.getcwd(),"logs")        

    if not os.path.exists(os.path.abspath(log_path)):
        os.makedirs(os.path.abspath(log_path), exist_ok=True)

    return log_path

def getCachePath(cfg):
    #make sure log_path exists
    cache_path=cfg.get("Config/cache_path")

    if (cache_path is None) or (len(cache_path)==0):
        cache_path=getLogPath(cfg)

    #build __cache__ folders if they don't exist
    os.makedirs(os.path.join(cache_path, "__cache__", "book"), exist_ok=True)
    os.makedirs(os.path.join(cache_path, "__cache__", "mam"), exist_ok=True)
    os.makedirs(os.path.join(cache_path, "__cache__", "audible"), exist_ok=True)
    os.makedirs(os.path.join(cache_path, "__cache__", "mylib"), exist_ok=True)

    return cache_path

def promptChoice (prompt, choices):
    while True:
        try:
            choice = int (input (f"{prompt}"))
            if choice in choices:
                return choice
            else:
                print ("Invalid choice, try again.")
        except ValueError:
            print ("This is not a valid choice.")

def getDuration (minutes):
    hours = minutes // 60
    minutes = minutes % 60

    duration = ""
    if hours > 0:
        duration = f"{hours} hours "

    if minutes > 0:
        duration += f"{minutes} minutes"

    return duration