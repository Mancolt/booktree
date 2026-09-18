"""Parse a release name (folder or file name) into title / author / series / ASIN before searching.

Upstream searched Audible with the whole file name as the title when the id3 tags were missing or junk
("Megan Fate Marshman - Relaxed.m4b" -> title:"megan fate marshman relaxed" -> 0 results).  Release names
follow a handful of conventions; this module recognises them:

    Author - Title                      Title - Author                     Title by Author
    Author-Title (no spaces)            Author - Series NN - Title         Series NN - Title - Author
    Title (Unabridged)                  Title [ASIN]  /  Title [ISBN10]    Title [Series NN]
    Title, Series Book N                Author, Author - Title             01 Title (leading track number)
    noise: [M4B] (MP3) 64K 2023 Unabridged Complete Retail Audiobook "Scott Brick narrator"

The result is a best effort with a confidence flag; the caller decides whether to prefer it over the id3 tags
(it should when those are empty or look like "AudioTrack 01" / "unknown artist").
"""
import os
import re
from thefuzz import fuzz

AUDIO_EXT = re.compile(r"\.(m4b|mp3|m4a|flac|ogg|opus|aac|wma)$", re.IGNORECASE)
ASIN_RE = re.compile(r"^(?:B0[A-Z0-9]{8}|\d{9}[\dX])$", re.IGNORECASE)      # Audible ASIN or ISBN-10, never a word
NOISE_WORD = re.compile(
    r"\b(unabridged|abridged|dramati[sz]ed|audiobooks?|audio ?book|m4b|mp3|m4a|complete|retail|split|epub|pdf|collection|"
    r"\d{2,3}\s?k(bps)?|\d{2,3}\s?kbps|graphic ?audio)\b", re.IGNORECASE)
NARRATOR_TAIL = re.compile(r",?\s*(?:narrated\s+by\s+)?[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3}\s+narrator\b", re.IGNORECASE)
NARRATED_BY = re.compile(r"\b(?:narrated|read)\s+by\b.*$", re.IGNORECASE)
# a trailing release year (audiobooks: 1990 onward); older years are part of the title ("Catastrophe 1914")
YEAR_TAIL = re.compile(r"[\s(\[]+((?:199|20\d)\d)[)\]]*\s*$")
# a leading track/volume number: zero-padded ("01 Title"), decimal ("22.5 Title") or followed by a separator ("3 - Title");
# a bare "12 Rules for Life" keeps its number
TRACK_PREFIX = re.compile(r"^\s*(?:(0\d{1,2}|\d{1,3}\.\d)\s+|(\d{1,3})\s*[-_.)]\s*)(?=\D)")
SERIES_ABBREV = re.compile(r"^[A-Z]{1,4}\s?\d{1,3}(?:[.\-]\d)?$")
SUBTITLE_NOISE = re.compile(r"^an?\s+(?:\w+\s+){0,3}(?:novel|thriller|mystery|story|memoir|novella|series)$", re.IGNORECASE)
TITLE_TAIL = re.compile(r"[\s:,]+an?\s+(?:novel|thriller|mystery|memoir|novella)$", re.IGNORECASE)
DISC_FOLDER = re.compile(r"^(cd|disc|disk|part)\s*\d+$", re.IGNORECASE)
FORMAT_FOLDER = re.compile(r"^(mp3|m4b|m4a|flac|ogg|opus|aac|wma|mp4)$", re.IGNORECASE)
# series parts run 1..99 (with an optional .5); "Fahrenheit 451" / "Apollo 13"-style titles are not series
SERIES_NUM = re.compile(r"^(?P<series>[^\d,]+?)\s+(?:#\s*|book\s+|vol\.?\s*|volume\s+)?(?P<part>\d{1,2}(?:\.\d)?)$", re.IGNORECASE)
BOOK_N_TAIL = re.compile(r"^(?P<rest>.+?)[,\s]+(?:book|vol\.?|volume)\s+(?P<part>\d{1,3}(?:\.\d)?)$", re.IGNORECASE)
SERIES_N_TAIL = re.compile(r"^(?P<rest>.+?)\s+series\s+(?P<part>\d{1,3}(?:\.\d)?)$", re.IGNORECASE)
SPLIT_RE = re.compile(r"\s+[-–—‑]\s*|\s*[-–—‑]\s+|\s*;\s+|\s+\|\s+")
DASHES = "-–—‑"
BY_RE = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<author>[A-Z][^-–—]+)$")
# applied to stripped text: no adjacent optional whitespace quantifiers (they backtrack in O(n^3) on long tags)
# junk: "AudioTrack 01", "Chapter 3", "cd1", "01", "007"; but "1984" or "2666" are titles
JUNK_TITLE = re.compile(r"^(?:(?:(?:audio)?track|chapter|ch|part|cd|disc|disk|untitled|unknown|title)\s*\d*|0\d*|\d{1,2})$", re.IGNORECASE)
JUNK_AUTHOR = re.compile(r"^(?:unknown(?: artist)?|various(?: artists)?|artist|author|\d+|)$", re.IGNORECASE)
MAX_NAME_LEN = 512          # file names are <= 255 bytes; anything longer comes from a log file and is truncated
STOPWORDS = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "it", "my", "your", "his", "her",
             "how", "what", "why", "when", "who", "all", "no", "not", "with", "from", "into", "one", "two", "three"}
PARTICLES = {"de", "van", "von", "der", "den", "la", "le", "du", "da", "di", "del", "della", "mc", "mac", "st", "y", "e", "jr", "sr", "ii", "iii"}
PERSON_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\+)\s*", re.IGNORECASE)


def isJunkTitle(title, release_name=None):
    """Empty, 'AudioTrack 01'-style, or a title that is just the release/file name (tags written by a ripper)."""
    t0 = "" if title is None else str(title).strip()
    if not t0 or (len(t0) <= MAX_NAME_LEN and JUNK_TITLE.match(t0)):
        return True
    if release_name:
        stem = AUDIO_EXT.sub("", str(release_name)).lower().strip()
        t = str(title).lower().strip()
        if t == stem or fuzz.ratio(t, stem) >= 95:
            return True
    return False


def isJunkAuthors(authors):
    """authors: iterable of names (str) or objects with .name"""
    names = [(a if isinstance(a, str) else getattr(a, "name", "")) for a in (authors or [])]
    return all(JUNK_AUTHOR.match((n or "").strip()[:MAX_NAME_LEN]) for n in names)


def authorsOverlap(names_a, names_b, threshold=85):
    """True when any name in names_a fuzzy-matches any name in names_b (surname/first-name order tolerant)."""
    for a in names_a or []:
        for b in names_b or []:
            if not (a and b):
                continue
            a_l, b_l = str(a).lower(), str(b).lower()
            # a one-word tag ("Child") is a token subset of any "Lee Child": require the plain ratio there
            score = fuzz.token_set_ratio(a_l, b_l) if (len(a_l.split()) > 1 and len(b_l.split()) > 1) else fuzz.ratio(a_l, b_l)
            if score >= threshold:
                return True
    return False


def nameLikeness(segment):
    """0..1 score for 'this segment is one or more people's names' (capitalised words, no digits/stopwords)."""
    seg = segment.strip()
    if not seg or any(ch.isdigit() for ch in seg) or ":" in seg:
        return 0.0
    people = [p for p in PERSON_SPLIT.split(seg) if p.strip()]
    if not people or len(people) > 4:
        return 0.0
    if len(people) == 2 and "," in seg and "&" not in seg and all(len(p.split()) == 1 for p in people):
        people = [f"{people[1]} {people[0]}"]          # "Thor, Brad" is one person
    score = 0.0
    for person in people:
        words = person.split()
        if not 1 <= len(words) <= 4:
            return 0.0
        for w in words:
            lw = w.lower().strip(".'")
            if lw in STOPWORDS:
                return 0.0
            if not (w[0].isupper() or lw in PARTICLES):
                return 0.0
            if len(w) >= 3 and w.isupper():
                return 0.0
        # two or three capitalised words is the classic shape; a single word is only weakly a name
        score += {1: 0.35, 2: 1.0, 3: 0.9, 4: 0.6}[len(words)]
    score = score / len(people)
    if len(people) >= 2:
        score += 0.1                                  # "Vince Flynn & Kyle Mills": several names is very author-like
    return min(1.1, score)


def splitPeople(segment):
    people = [p.strip() for p in PERSON_SPLIT.split(segment) if p.strip()]
    if len(people) == 2 and "," in segment and "&" not in segment and all(len(p.split()) == 1 for p in people):
        return [f"{people[1]} {people[0]}"]          # "Thor, Brad" -> "Brad Thor"
    return people


def _clean(text):
    text = NARRATOR_TAIL.sub("", text)
    text = NARRATED_BY.sub("", text)
    text = NOISE_WORD.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—,;:_")
    return text


def parseReleaseName(name, known_authors=(), file_name=None):
    """Return {'title', 'subtitle', 'authors', 'series', 'part', 'asin', 'confidence', 'source'}.

    name: the release folder or file name booktree uses as the book key. known_authors: id3 author names
    (used only to recognise which segment is the author). file_name: the first file's name, consulted when
    `name` is a disc folder such as 'cd1'."""
    src = (name or "")[:MAX_NAME_LEN]
    if DISC_FOLDER.match(src.strip()) and file_name:
        src = str(file_name)[:MAX_NAME_LEN]
    text = AUDIO_EXT.sub("", src.strip())
    out = {"title": "", "subtitle": "", "authors": [], "series": "", "part": "", "asin": "", "confidence": 0.0, "source": src}

    # bracketed / parenthesised groups: ASIN, series index, or noise
    def bracket(m):
        inner = m.group(1).strip()
        if NOISE_WORD.sub("", inner).strip(" .,-") == "" or re.fullmatch(r"(19|20)\d{2}", inner):
            return " "
        if ASIN_RE.match(inner):
            out["asin"] = inner.upper()
            return " "
        pm = re.fullmatch(r"(?:book|vol\.?|volume|part)\s*(\d{1,3}(?:\.\d)?)", inner, re.IGNORECASE)
        if pm:
            out["part"] = out["part"] or pm.group(1)
            return " "
        sm = SERIES_NUM.match(inner)
        if sm and not out["series"]:
            out["series"], out["part"] = sm.group("series").strip(), sm.group("part")
            return " "
        return " " + inner + " "      # anything else stays as title text
    text = re.sub(r"[\[(]([^\[\]()]*)[\])]", bracket, text)

    # bare ASIN token (B0... or 10 alphanumerics with digits) anywhere
    for tok in re.findall(r"\b(B0[A-Z0-9]{8})\b", text):
        out["asin"] = out["asin"] or tok.upper()
        text = text.replace(tok, " ")

    text = _clean(text)
    m = TRACK_PREFIX.match(text)
    if m:
        num = m.group(1) or m.group(2)
        out["part"] = out["part"] or num.lstrip("0") or "0"
        text = text[m.end():]
    text = _clean(text)
    if not text:
        return out

    # "Title by Author"
    bm = BY_RE.match(text)
    if bm and nameLikeness(bm.group("author")) >= 0.6:
        out["title"], out["authors"] = _title(bm.group("title"), out), splitPeople(bm.group("author"))
        out["confidence"] = 0.9
        return out

    segments = [s.strip() for s in SPLIT_RE.split(text) if s.strip()]
    if len(segments) == 1 and any(d in text for d in DASHES):
        left, _, right = re.split(f"([{DASHES}])", text, maxsplit=1)
        if nameLikeness(left) >= 0.9 and len(right.split()) >= 2:
            segments = [left.strip(), right.strip()]
    # a trailing year is release noise ("Dead Med 2024") unless the segment is only that year ("George Orwell - 1984")
    stripped = []
    for seg in segments:
        ym = YEAR_TAIL.search(seg)
        if ym and len(seg[:ym.start()].split()) >= 1:          # 0 words before it: the segment IS the year (1984)
            seg = _clean(seg[:ym.start()])
        if seg:
            stripped.append(seg)
    segments = stripped
    if not segments:
        return out

    # pull series segments ("Jack Reacher 28", "Scot Harvath 02", "Book 3") out of the list
    rest = []
    forcedAuthor = None
    for seg in segments:
        sm = SERIES_NUM.match(seg)
        if sm and len(segments) > 1 and len(sm.group("series").split()) <= 4 and not out["series"]:
            out["series"], out["part"] = sm.group("series").strip(), sm.group("part")
            continue
        if re.fullmatch(r"(book|vol\.?|volume|part)\s*\d{1,3}", seg, re.IGNORECASE) and len(segments) > 1:
            out["part"] = out["part"] or re.sub(r"\D", "", seg)
            continue
        if SERIES_ABBREV.match(seg) and len(segments) > 1:          # "SH11.5", "PL15"
            out["part"] = out["part"] or re.sub(r"^[A-Z]+\s?", "", seg)
            continue
        if SUBTITLE_NOISE.match(seg) and len(segments) > 1:         # "A Jack Reacher Novel"
            continue
        bym = re.match(r"^by\s+(.+)$", seg, re.IGNORECASE)
        if bym and nameLikeness(bym.group(1)) >= 0.6:
            forcedAuthor = bym.group(1)
            continue
        rest.append(seg)
    segments = rest or segments

    known = [k for k in (known_authors or []) if k and not JUNK_AUTHOR.match(k)]

    def authorScore(seg):
        s = nameLikeness(seg)
        if known and max(fuzz.token_set_ratio(seg.lower(), k.lower()) for k in known) >= 85:
            s = max(s, 1.0) + 0.5
        return s

    if forcedAuthor:
        out["authors"] = splitPeople(forcedAuthor)
        out["title"] = _title(segments[0], out)
        if len(segments) > 1:
            out["subtitle"] = " - ".join(segments[1:])
        out["confidence"] = 0.9
        return out

    if len(segments) == 1:
        out["title"] = _title(segments[0], out)
        out["confidence"] = 0.5
        return out

    scores = [authorScore(s) for s in segments]
    best = max(range(len(segments)), key=lambda i: scores[i])
    # the author segment is the most name-like one, but only if it is clearly a name and there is a title left
    if scores[best] >= 0.6 and any(i != best for i in range(len(segments))):
        out["authors"] = splitPeople(segments[best])
        titleSegs = [s for i, s in enumerate(segments) if i != best]
        out["confidence"] = 0.85 if scores[best] >= 0.9 else 0.7
        # "Mad Mabel - Sally Hepworth": both halves look like names; offer the swapped reading for a retry
        ties = [i for i in range(len(segments)) if i != best and scores[i] >= 0.6 and abs(scores[i] - scores[best]) < 0.15]
        if ties and len(segments) == 2 and not known:
            out["confidence"] = 0.6
            out["alternative"] = {"title": _title(segments[best], dict(out)), "authors": splitPeople(segments[ties[0]])}
    else:
        titleSegs = segments
        out["confidence"] = 0.45
    out["title"] = _title(titleSegs[0], out)
    if len(titleSegs) > 1:
        out["subtitle"] = " - ".join(titleSegs[1:])
    return out


def groupingName(fullPath, sourcePath, fallback):
    """The folder that identifies a book: the release under `sourcePath`, walking past cd/disc/disk/part N
    parents. Two discs of one release become one book; two releases that both use `cd1/` stay separate.
    Author/Title layouts still key on the immediate (non-disc) parent. A format folder under a disc
    (`cd1/MP3/`) is skipped the same way. Falls back to `fallback` for a loose file or a path outside
    the source."""
    try:
        rel = os.path.relpath(fullPath, sourcePath) if sourcePath and fullPath else ""
    except ValueError:
        rel = ""
    if not rel or rel.startswith(".."):
        return fallback
    parts = [p for p in rel.split(os.sep) if p and p != "."]
    if len(parts) < 2:
        return fallback
    for i in range(len(parts) - 2, -1, -1):
        name = parts[i].strip()
        # cd1/ itself, and a codec folder sitting under it (cd1/MP3/), are not the release
        parent_is_disc = i > 0 and bool(DISC_FOLDER.match(parts[i - 1].strip()))
        if DISC_FOLDER.match(name) or (parent_is_disc and FORMAT_FOLDER.match(name)):
            continue
        return parts[i]
    return parts[0]


def releaseNameForBook(files, sourcePath, name):
    """The best name to parse for a book: the first path component under the source path (the release folder,
    or the loose file name). Falls back to `name` (booktree's grouping key)."""
    for f in files:
        full = getattr(f, "fullPath", None) or ""
        try:
            rel = os.path.relpath(full, sourcePath) if sourcePath and full else ""
        except ValueError:
            rel = ""
        if rel and not rel.startswith(".."):
            return rel.split(os.sep)[0]
    return name


def _title(seg, out):
    """Strip ', Series Book N' / 'Series N' tails from a title segment, recording series/part."""
    seg = seg.strip(" ,-–—;:")
    m = BOOK_N_TAIL.match(seg)
    if m:
        rest, part = m.group("rest").strip(" ,"), m.group("part")
        out["part"] = out["part"] or part
        # "Insurgent, Tier One Book 10" -> title Insurgent, series Tier One
        if "," in rest:
            head, tail = rest.rsplit(",", 1)
            if 1 <= len(tail.split()) <= 4:
                out["series"] = out["series"] or tail.strip()
                rest = head.strip()
        elif not out["series"] and len(rest.split()) <= 3:
            out["series"] = rest
        seg = rest
    m = SERIES_N_TAIL.match(seg)
    if m:
        out["part"] = out["part"] or m.group("part")
        seg = m.group("rest")
    seg = TITLE_TAIL.sub("", seg)                 # "The Athena Project A Thriller" -> "The Athena Project"
    return seg.strip(" ,-–—;:")
