# booktree Configuration

A copy of default_config.cfg can be found under the /templates folder.  It is recommended that you create a config file for each scenario that you want to run.  I personally have 4:  audiobooks, ebooks, log mode and multi-book

### Default Config File
~~~
{
    "Config": {
        "metadata": "mam-audible",
        "matchrate": 60,
        "fuzzy_match": "token_sort",
        "log_path": "/logs",    
        "cache_path": "/config",    
        "session": "",
        "paths": [{
            "files": ["**/*.m4b", "**/*.mp3", "**/*.m4a"],
            "source_path": "/path/to/downloads",
            "media_path": "/path/to/media/library"
        }],
        "flags": {
            "dry_run": 0,
            "verbose": 1,
            "multibook": 0,
            "ebooks": 0,
            "no_opf": 0,
            "no_cache": 0,
            "fixid3": 0,
            "add_narrators": 0,
            "interactive": 1,
            "hardlink": 1 
        },
        "target_path": {
            "multi_author": "{first_author}",
            "in_series": "{author}/{series}/{series} #{part} - {title}",
            "no_series": "{author}/{title}",
            "disc_folder": "{title} {disc}"
        },
        "tokens":{
            "skip_series": 0,
            "kw_ignore": [".", ":", "_", "[", "]", "{", "}", ",", ";", "(", ")"],
            "kw_ignore_words": ["the","and","m4b","mp3","series","audiobook","audiobooks", "book", "part", "track", "novel", "disc"],
            "title_patterns": ["-end", "\bpart\b", "\btrack\b", "\bof\b",  "\bbook\b", "m4b", "\\(", "\\)", "_", "\\[", "\\]", "\\.", "\\s?-\\s?"]
        }  
    }
}
~~~

| Config Path | Subpath | Description   | Suggested Value |
| ----------- | ------- | -----------   | --------------- |
| metadata    |         | Source of the metada: (audible, mam, mam-audible) | mam-audible    |
| matchrate   |         | Minimum acceptable fuzzy match rate. <br/>To increase match rate, check the matchrate values from the log, and lower this value | 60     |
| fuzzy_match |         | Fuzzy match algorithm: (partial, token_sort, ratio) | token_sort |
| log_path    |         | Where your log files will be saved. If not set, will default to "logs" | /logs (for docker), logs (for local)   |
| cache_path  |         | Where your log files will be saved. If not set, will default to "logs" | /config   |
| session     |         | MAM Session ID (can be removed once one has been saved) |    |
| hints_file  |         | Path of a hints file (see below); same as `--hints` |    |
| flags/parse_names | | Parse the release folder/file name (`Author - Title`, `Title - Author`, `Title by Author`, `Series NN - Title`, `Title [ASIN]`, `(Unabridged)` noise) and use it for the search where the id3 tags are empty or junk (`AudioTrack 01`, `unknown artist`, or a title that just repeats the file name). `--legacy-names` turns it off. | 1 |
| pin_max_runtime_delta_min | | Refuse a pinned ASIN whose runtime differs from the expected duration by more than this many minutes (0 = never refuse) | 0 |
| cache/audible_positive_hours | | How long a non-empty Audible answer is reused | 720 |
| cache/audible_empty_hours | | How long an empty Audible answer (or a per-ASIN answer without a title) is reused before the query is retried | 6 |
| cache/mam_positive_hours | | How long a MAM answer with a snatched entry is reused | 168 |
| cache/mam_empty_hours | | How long an empty MAM answer (or one without a snatched entry) is reused | 24 |
| mam/min_interval_seconds | | Minimum spacing between HTTP requests to MAM (cache hits are free) | 6 |
| mam/max_queries_per_run | | Runaway guard: maximum MAM searches in one run (one container invocation); further searches are skipped with a message and those releases stay unmatched until the next run (0 = unlimited). The 6-second spacing is the real safety net; this only stops a loop | 3000 |
| json_log | | Path of a JSON-lines run log, or `true` for `booktree_log_<timestamp>.jsonl` next to the CSV; same as `--json-log` | |
| refresh | | List of releases (name or path) to re-process ignoring cached answers and the processed marker; same as `--refresh` | |
| paths       |         | This is a *list* of folders and files to be processed              |
| | files               | File patterns to be searched | ["\*\*/\*.m4b", "\*\*/\*.mp3", "\*\*/\*.m4a"]    |
| | source_path         | Unorganized folder location | /path/to/downloads   |
| | media_path          | Organized media folder location | /path/to/ABS/library |
| flags       |         | These flags can be overriden via command line arguments |     |
| | dry_run             | --dry-run, Dry run only, files are not actually hardlinked  | 0    |
| | verbose             | --verbose, Display info and debug information on the screen | 1    |
| | multibook           | --multibook, If true, assumes each file is a book | 0    |
| | ebooks              | --ebooks, If true, bypasses audible search | 0    |
| | no_opf              | --no-opf, If true, skips OPF file creation | 0   |
| | no_cache            | --no-cache, If true, processes the files even if they've been processed before | 0    |
| | fixid3              | --fixid3, If true, overrides and fixes the id3 title data | 0    |
| | add_narrators       | --add-narrators, If true, includes the narrators in the Audible search | 1   |
| | interactive         | --interactive, If true, prompts user to choose if search returns multiple rows | 0   |
| | hardlink            | --hardlink, If true, creates hardlinks. If false, creates copy | 1   |
| target_path  |        | |     |
| | multi_author        | How to handle the Author folder for multi-author books: first_author, authors, "", "Static folder name" | first_author   |
| | in_series           | Format of the generated tree for books in a series | {author}/{series}/{series} #{part} - {title}   |
| | no_series           | Format of the generated tree for books that are NOT in a series | {author}/{title}    |
| | disc_folder         | Format of the folder name for multi-disc books | {title} {disc}    |
| tokens      |         | |    |
| | skip_series         | Used when fixid3 is true and an alt Title is generated from the id3-series data | 0   |
| | kw_ignore           | Characters ignored when generating keywords for search | | [".", ":", "_", "[", "]", "{", "}", ",", ";", "(", ")"]    |
| | kw_ignore_words     | Characters ignored when optimizing keywords for search| ["the","and","m4b","mp3","series","audiobook","audiobooks", "book", "part", "track", "novel", "disc"]    |
| | title_patterns      | ignores these patterns when alt Title is generated from bad id3 data | ["-end", "\bpart\b", "\btrack\b", "\bof\b",  "\bbook\b", "m4b", "\\(", "\\)", "_", "\\[", "\\]", "\\.", "\\s?-\\s?"]    |


## Hints file (`--hints <json>` or `Config/hints_file`)

A hints file lets something that knows more than the id3 tags (a request tracker, a review UI, you) steer the
Audible match per release without touching the files. It is a JSON object keyed by the release as booktree names
it (the folder under `source_path`, or the file name for a loose file), by the full path of the release folder, or
by the full path of any file in it:

~~~
{
  "Megan Fate Marshman - Relaxed.m4b":           {"asin": "B0C5Q9XJ1K"},
  "/data/downloads/complete/audio/Some Folder":  {"candidates": ["B0ABC12345", "B0DEF67890"], "duration_min": 541},
  "Brad Thor - Takedown Unabridged - Complete":  {"title": "Takedown", "authors": ["Brad Thor"], "duration_min": 612}
}
~~~

| field | meaning |
|---|---|
| `asin` | Pinned ASIN. Authoritative: the Audible product is accepted without the title/author check (a runtime mismatch is only warned about). If Audible has no usable product for it, the normal search runs. |
| `candidates` | Up to 10 ASINs, chosen by the caller (no title/author check). Each is fetched by ASIN; among those whose runtime is within 2 minutes of the expected duration the highest fuzzy score wins, then the closest runtime; if none is within 2 minutes, the best fuzzy score above `matchrate`. A candidate with the right runtime is accepted at any score, so only list candidates you would accept. |
| `duration_min` | Expected runtime in minutes. Defaults to the total duration of the release's files. |
| `title`, `authors` | Used for the Audible search instead of the id3 title/artist (at most 300 characters, 10 authors). A hinted `title` is used as-is, also with `--fixid3`. The logged `id3-*` columns still show the file's own tags. |

An invalid hints file (or one over 16 MiB) stops the run before anything is processed. Path keys are matched
against files under `source_path` only; a key such as `/data` never matches. In `log` mode (`fix.csv`) a non-empty
`id3-asin` column is treated as a pinned ASIN; each row is processed on its own, so fill the column on every row of
a multi-file release, and blank the `paths` column when re-pinning a row that was previously matched to the wrong
book (otherwise the old target folder is reused).

For a pinned match the `adb-matchRate` column holds the informational fuzzy score of the Audible product against the
file's own tags, which may be well below `matchrate`; `isMatched` is `True` regardless. `Config/pin_max_runtime_delta_min`
(default 0 = off) refuses a pin whose runtime differs from the expected duration by more than that many minutes and
falls back to the normal search.

Duration is evidence in every search: among results that pass the title/author check and the `matchrate`
threshold, a result whose Audible runtime is within 2 minutes of the expected duration is preferred over one that
is not; results with equal scores prefer the closer runtime. With no runtime information (files report no duration)
the behaviour is unchanged: the first highest score wins.

## Release-name parsing (`flags/parse_names`, default on; `--legacy-names` to disable)

Upstream searched Audible with the whole file name as the title when the id3 tags were missing, so
`Megan Fate Marshman - Relaxed.m4b` became `title: megan fate marshman relaxed` and found nothing. booktree now
parses the release name (the folder under `source_path`, or the file name for a loose file; for `cd1/` layouts the
release folder) into title, author(s), series/part and ASIN, recognising `Author - Title`, `Title - Author`,
`Title by Author`, `Author-Title`, `Author - Series NN - Title`, `Series NN - Title - Author`, `Title, Series Book N`,
`Title [ASIN]`, `Title [ISBN10]`, `Title [Series NN]`, `Last, First` author order, leading track numbers, and
strips `(Unabridged)`, `[M4B]`, bitrates, trailing years and "X narrator" fragments.

The parse is used only where the tags are junk: an empty or `AudioTrack 01`-style title, or one that merely repeats
the file name, is replaced by the parsed title; `unknown artist`/empty authors by the parsed authors; a missing ASIN
by a bracketed one. Good tags are never overridden, hints take precedence, and the logged `id3-*` columns always
show the file's own tags. Title and author are sent to Audible as separate fields, and a result must then agree
with the parsed title (a same-author book with another title is rejected). When an attempt finds no acceptable
match the next one runs, bounded and cached like any other query: the swapped reading when `Title - Author` and
`Author - Title` are equally plausible, then the title alone (pen names, translators), and finally the search
exactly as upstream performed it with the file's own tags, so a wrong parse can add a match but never lose one.
The MAM ranking uses the same parsed values; the MAM query string is unchanged.


## Caching, MAM traffic, `--refresh`

Search answers are cached under `cache_path/__cache__/{audible,mam}/<sha256 of the query>` in the API's own JSON
(same files and names as upstream; tools that read them keep working). Upstream kept every Audible answer forever,
so one transient or malformed query became a permanent miss, and never cached empty MAM answers, so every
unmatched release queried MAM again on every run. Entries now expire by age and kind (`cache/*_hours` above);
errors and HTTP failures are never cached; an Audible per-ASIN answer without a title and a MAM answer without a
snatched entry count as empty. Every HTTP request to MAM is spaced `mam/min_interval_seconds` apart, which is the
protection that matters; `mam/max_queries_per_run` (3000) exists only to stop a runaway process. An unmatched
release costs two searches (the initial one and the widened retry), so 3000 covers a first run over about 1,500
unmatched releases.

Note for existing installations: cache entries older than their TTL are retried the next time a release that
uses them is processed. Releases already matched and hardlinked are never re-searched (their processed marker does
not expire), so the first run after upgrading re-queries only the releases that are still unmatched, 6 seconds
apart, and afterwards once per TTL.

`--refresh RELEASE` (repeatable; or `"refresh": true` in a hint) re-processes one release: its cached answers and
its "already processed" marker are ignored while everything else is served from cache. `--no-cache` still does this
for the whole run.

## JSON run log (`--json-log [PATH]`)

In addition to the CSV, one JSON object per line: a `book` record per processed release and a final `run` record.
The CSV columns, file name and stdout are unchanged; the JSON log is the structured replacement for scraping them.

~~~
{"type": "book", "schema_version": 1, "run": "20260915031500", "release": "Megan Fate Marshman - Relaxed.m4b",
 "release_path": "/data/downloads/complete/audio", "files": [{"path": "...", "duration_s": 14670.2, "hardlinked": true}],
 "id3": {"asin": "", "title": "", "authors": [], "narrators": [], "duration_s": 14670.2},
 "parsed_name": {"title": "Relaxed", "authors": ["Megan Fate Marshman"], "series": "", "part": "", "asin": ""},
 "hint": null, "pinned_asin": "", "matched": true, "metadata_source": "audible",
 "match": {"asin": "B0C5Q9XJ1K", "title": "Relaxed", "authors": ["Megan Fate Marshman"], "narrators": ["..."],
           "series": "", "part": "", "runtime_min": 244, "match_rate": 100, "attempt": "parsed"},
 "expected_duration_min": 244.0, "runtime_delta_min": 0.0, "target_path": "/data/Audiobooks/Megan Fate Marshman/Relaxed",
 "hardlinked": true, "mam_count": 1, "audible_count": 1,
 "queries": [{"kind": "mam", "cache_key": "<sha256>", "cached": false, "expired": false, "results": 1, "text": "..."},
             {"kind": "audible", "cache_key": "<sha256>", "cached": false, "expired": false, "results": 1,
              "asin": "", "title": "Relaxed", "authors": "\"Megan Fate Marshman\"", "keywords": "relaxed megan fate marshman"}]}
{"type": "run", "schema_version": 1, "run": "20260915031500", "started_utc": "...", "finished_utc": "...",
 "metadata": "mam-audible", "books": 3, "matched": 2, "unmatched": 1, "hardlinked_files": 4,
 "mam_queries": 2, "audible_queries": 3, "csv": "/logs/booktree_log_20260915031500.csv", "exit_code": 0}
~~~

`match.attempt` says what produced the match: `pinned`, `candidates`, `parsed`, `parsed-authors`, `swapped`,
`title-only`, `legacy` (upstream's own search), `mam`, or `log` (taken from the input log in `log` mode).
`queries[].cache_key` is the hash printed in `Checking cache: <kind>/<hash>`; a query that failed carries `error`,
one skipped by the MAM budget carries `skipped`. `id3.duration_s` is the first file's duration while
`expected_duration_min` is the whole release. One `run` record is written per `paths` entry. Give the path as
`--json-log=PATH`, or put `--json-log PATH` after the config file (argparse would otherwise take the config file
as the path).
