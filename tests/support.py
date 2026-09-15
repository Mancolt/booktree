"""Shared test doubles: a config object and an offline Audible client."""
import json
import os


class FakeConfig:
    """Mimics myx_args.Config.get('a/b/c', default) over a plain dict."""

    def __init__(self, tmpdir, **overrides):
        self.data = {"Config": {
            "metadata": "audible", "matchrate": 60, "fuzzy_match": "token_sort",
            "log_path": tmpdir, "cache_path": tmpdir, "session": "",
            "flags": {"dry_run": 0, "verbose": 1, "multibook": 0, "ebooks": 0, "no_opf": 0, "no_cache": 0,
                      "fixid3": 0, "add_narrators": 0, "interactive": 0, "hardlink": 1},
            "target_path": {"multi_author": "{first_author}", "in_series": "{author}/{series}/{series} #{part} - {title}",
                            "no_series": "{author}/{title}", "disc_folder": "{title} {disc}"},
            "tokens": {"skip_series": 0,
                       "kw_ignore": [".", ":", "_", "[", "]", "{", "}", ",", ";", "(", ")"],
                       "kw_ignore_words": ["the", "and", "m4b", "mp3", "series", "audiobook", "audiobooks", "book", "part",
                                           "track", "novel", "disc"],
                       "title_patterns": ["-end", r"\bpart\b", r"\btrack\b", r"\bof\b", r"\bbook\b", "m4b", r"\(", r"\)", "_",
                                          r"\[", r"\]", r"\.", r"\s?-\s?"]},
        }}
        for path, value in overrides.items():
            node = self.data
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def get(self, path=None, default=None):
        node = self.data
        for part in path.split("/"):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def product(asin, title, authors=(), runtime_min=0, language="english", **extra):
    p = {"asin": asin, "language": language, "content_type": "Product"}
    if title is not None:
        p["title"] = title
        p["authors"] = [{"name": a} for a in authors]
        p["runtime_length_min"] = runtime_min
        p["publisher_name"] = "Test House"
    p.update(extra)
    return p


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeAudible:
    """Serves canned catalog answers. by_asin: {asin: product}; search: list of products for any query."""

    def __init__(self, by_asin=None, search=None):
        self.by_asin = by_asin or {}
        self.search = search or []
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/catalog/products"):
            return _Response({"products": list(self.search), "total_results": len(self.search)})
        asin = url.rsplit("/", 1)[1]
        if asin in self.by_asin:
            return _Response({"product": self.by_asin[asin]})
        return _Response({"product": {"asin": asin, "asset_details": [], "is_vvab": False}})   # Audible's skeleton answer


def write_json(tmpdir, name, data):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path
