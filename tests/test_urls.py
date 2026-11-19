"""Link detection, normalisation and resource identity (``core/urls.py``).

These tests pin the three concerns apart, because their failure modes are
opposite: detection must never *invent* a link, normalisation must repair what
the user typed, and identity must never merge two different resources.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.urls import (
    MAX_FILENAME_UNITS,
    canonical_url,
    extract_urls,
    first_url,
    looks_like_filename,
    normalize_url,
    same_resource,
    sanitize_filename,
    trim_url,
    url_filename,
)
from core.utils import MAX_PATH_BUDGET, unique_filepath


class ExtractUrlsTest(unittest.TestCase):
    def test_plain_url(self):
        self.assertEqual(
            extract_urls("https://example.com/file.zip"),
            ["https://example.com/file.zip"],
        )

    def test_url_inside_prose(self):
        self.assertEqual(
            extract_urls("Download it from https://example.com/file.zip today"),
            ["https://example.com/file.zip"],
        )

    def test_several_urls_on_one_line(self):
        self.assertEqual(
            extract_urls(
                "a https://x.com/1.zip, b https://y.com/2.zip; c https://z.com/3.zip"
            ),
            ["https://x.com/1.zip", "https://y.com/2.zip", "https://z.com/3.zip"],
        )

    def test_wrapped_in_quotes_backticks_and_angles(self):
        for text in (
            '"https://x.com/a.zip"',
            "'https://x.com/a.zip'",
            "`https://x.com/a.zip`",
            "<https://x.com/a.zip>",
            "(https://x.com/a.zip)",
            "[https://x.com/a.zip]",
        ):
            with self.subTest(text=text):
                self.assertEqual(extract_urls(text), ["https://x.com/a.zip"])

    def test_trailing_sentence_punctuation(self):
        self.assertEqual(extract_urls("See https://x.com/a.zip."), ["https://x.com/a.zip"])
        self.assertEqual(extract_urls("Go to https://x.com/a.zip!"), ["https://x.com/a.zip"])
        self.assertEqual(extract_urls("Is it https://x.com/a.zip?"), ["https://x.com/a.zip"])

    def test_balanced_brackets_inside_a_url_survive(self):
        # The wiki-style path must keep its own ")" while losing the prose one.
        self.assertEqual(
            extract_urls("(https://en.wikipedia.org/wiki/Foo_(bar))"),
            ["https://en.wikipedia.org/wiki/Foo_(bar)"],
        )

    def test_html_attribute_with_escaped_query(self):
        # &amp; must be decoded, otherwise the signed parameter is corrupted.
        self.assertEqual(
            extract_urls('<a href="https://x.com/f.zip?token=1&amp;id=2">go</a>'),
            ["https://x.com/f.zip?token=1&id=2"],
        )

    def test_query_only_url_without_extension_is_kept(self):
        self.assertEqual(
            extract_urls("https://example.com/download?id=12345"),
            ["https://example.com/download?id=12345"],
        )
        self.assertEqual(
            extract_urls("https://cdn.example.com/file?token=abc123"),
            ["https://cdn.example.com/file?token=abc123"],
        )

    def test_percent_encoded_and_unicode_urls(self):
        self.assertEqual(
            extract_urls("https://x.com/a%20b.zip"), ["https://x.com/a%20b.zip"]
        )
        self.assertEqual(
            extract_urls("https://münchen.example.com/straße.zip"),
            ["https://münchen.example.com/straße.zip"],
        )

    def test_urls_differing_only_by_fragment_are_one_resource(self):
        text = "https://x.com/a.zip#one and https://x.com/a.zip#two"
        self.assertEqual(extract_urls(text), ["https://x.com/a.zip#one"])

    def test_never_invents_a_link_from_prose(self):
        # "file.zip" is a filename here, not a host — this is why bare domains
        # are opt-in.
        self.assertEqual(extract_urls("please unzip file.zip first"), [])
        self.assertEqual(extract_urls("version 2.0.1 is out"), [])
        self.assertEqual(extract_urls("mail me at someone@example.com"), [])

    def test_bare_domains_are_opt_in(self):
        self.assertEqual(extract_urls("grab example.com/a.zip"), [])
        self.assertEqual(
            extract_urls("grab example.com/a.zip", allow_bare_domains=True),
            ["https://example.com/a.zip"],
        )
        self.assertEqual(
            extract_urls("see www.example.com/a.zip", allow_bare_domains=True),
            ["https://www.example.com/a.zip"],
        )

    def test_non_http_schemes_ignored(self):
        for text in ("ftp://x.com/a.zip", "file:///etc/passwd", "javascript:alert(1)"):
            with self.subTest(text=text):
                self.assertEqual(extract_urls(text), [])

    def test_empty_inputs(self):
        self.assertEqual(extract_urls(None), [])
        self.assertEqual(extract_urls(""), [])
        self.assertEqual(extract_urls("   \n\t "), [])
        self.assertIsNone(first_url(""))
        self.assertIsNone(first_url("no links here"))

    def test_max_urls_truncates(self):
        text = "https://a.com/1 https://b.com/2 https://c.com/3"
        self.assertEqual(extract_urls(text, max_urls=2), ["https://a.com/1", "https://b.com/2"])
        self.assertEqual(first_url(text), "https://a.com/1")

    def test_trim_url_is_idempotent(self):
        for raw in ("https://x.com/a.zip).", "https://x.com/a.zip", "https://x.com/(a)"):
            once = trim_url(raw)
            self.assertEqual(trim_url(once), once, msg=raw)


class NormalizeUrlTest(unittest.TestCase):
    def test_strips_copy_paste_wrapping(self):
        self.assertEqual(normalize_url("  https://x.com/a.zip  "), "https://x.com/a.zip")
        self.assertEqual(normalize_url('"https://x.com/a.zip"'), "https://x.com/a.zip")
        self.assertEqual(normalize_url("<https://x.com/a.zip>"), "https://x.com/a.zip")

    def test_scheme_relative_becomes_https(self):
        self.assertEqual(normalize_url("//x.com/a.zip"), "https://x.com/a.zip")

    def test_bare_domain_gets_https(self):
        self.assertEqual(normalize_url("example.com/f.zip"), "https://example.com/f.zip")
        self.assertEqual(normalize_url("www.example.com/f.zip"), "https://www.example.com/f.zip")
        self.assertEqual(normalize_url("example.com:8080/f.zip"), "https://example.com:8080/f.zip")

    def test_existing_scheme_is_never_rewritten(self):
        # A signed URL must pass through byte-for-byte.
        signed = "https://cdn.example.com/f.zip?X-Amz-Signature=AbC%2F123&e=99"
        self.assertEqual(normalize_url(signed), signed)
        self.assertEqual(normalize_url("ftp://x.com/a.zip"), "ftp://x.com/a.zip")

    def test_empty(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url("   "), "")
        self.assertEqual(normalize_url(None), "")


class UrlFilenameTest(unittest.TestCase):
    def test_last_path_segment(self):
        self.assertEqual(url_filename("https://x.com/files/setup.exe"), "setup.exe")

    def test_percent_encoded_segment(self):
        self.assertEqual(url_filename("https://x.com/a%20b.zip"), "a b.zip")
        self.assertEqual(
            url_filename("https://x.com/%E6%96%87%E4%BB%B6.zip"), "文件.zip"
        )

    def test_no_path_falls_back_to_host(self):
        self.assertEqual(url_filename("https://example.com"), "example.com")
        self.assertEqual(url_filename("https://example.com/"), "example.com")

    def test_filename_from_query_string(self):
        # The query is consulted only when the path has no name of its own —
        # a named path segment is the more authoritative answer.
        self.assertEqual(url_filename("https://x.com/?file=setup.exe"), "setup.exe")
        self.assertEqual(url_filename("https://x.com/download?file=setup.exe"), "download")
        self.assertEqual(url_filename("https://x.com/download.php?filename=a.zip"), "download.php")

    def test_query_id_is_not_mistaken_for_a_filename(self):
        self.assertEqual(url_filename("https://x.com/download?id=12345"), "download")

    def test_path_traversal_is_neutralised(self):
        self.assertEqual(url_filename("https://x.com/../../etc/passwd"), "passwd")
        self.assertEqual(url_filename("https://x.com/?file=../../evil.zip"), "_.._evil.zip")

    def test_empty_url(self):
        self.assertEqual(url_filename(""), "")


class CanonicalIdentityTest(unittest.TestCase):
    def test_scheme_and_host_case_folded(self):
        self.assertEqual(
            canonical_url("HTTP://Example.COM/a/b?x=1#frag"), "http://example.com/a/b?x=1"
        )

    def test_default_port_removed(self):
        self.assertEqual(canonical_url("http://example.com:80/a"), "http://example.com/a")
        self.assertEqual(canonical_url("https://example.com:443/a"), "https://example.com/a")
        # A non-default port is part of the identity.
        self.assertEqual(canonical_url("https://example.com:8443/a"), "https://example.com:8443/a")

    def test_fragment_dropped_path_preserved(self):
        self.assertEqual(canonical_url("https://x.com/a#x"), "https://x.com/a")

    def test_query_is_never_normalised(self):
        # Two signed links are different resources even though only the token
        # differs — folding the query would silently merge them.
        a = canonical_url("https://x.com/f.zip?token=aaa")
        b = canonical_url("https://x.com/f.zip?token=bbb")
        self.assertNotEqual(a, b)
        self.assertFalse(same_resource("https://x.com/f.zip?token=aaa",
                                       "https://x.com/f.zip?token=bbb"))

    def test_same_resource_positive_cases(self):
        self.assertTrue(same_resource("HTTP://Example.com:80/a", "http://example.com/a"))
        self.assertTrue(same_resource("https://x.com/a#one", "https://x.com/a#two"))
        self.assertTrue(same_resource("https://x.com", "https://x.com/"))

    def test_same_resource_never_matches_empty(self):
        self.assertFalse(same_resource("", ""))
        self.assertFalse(same_resource("", "https://x.com/a"))

    def test_unparseable_url_compares_to_itself_only(self):
        raw = "not a url"
        self.assertEqual(canonical_url(raw), raw)
        self.assertTrue(same_resource(raw, raw))
        self.assertFalse(same_resource(raw, "https://x.com/a"))


class FilenameHygieneTest(unittest.TestCase):
    def test_sanitize_removes_reserved_characters(self):
        cleaned = sanitize_filename('bad<>:"/\\|?*name.mp4')
        self.assertTrue(cleaned.endswith(".mp4"))
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, cleaned)

    def test_sanitize_never_returns_empty(self):
        self.assertEqual(sanitize_filename(""), "downloaded_file")
        self.assertEqual(sanitize_filename("..."), "downloaded_file")

    def test_looks_like_filename(self):
        self.assertTrue(looks_like_filename("a.zip"))
        self.assertTrue(looks_like_filename("archive.tar.gz"))
        self.assertFalse(looks_like_filename("download"))
        self.assertFalse(looks_like_filename(""))
        self.assertFalse(looks_like_filename("."))
        self.assertFalse(looks_like_filename(".."))
        self.assertFalse(looks_like_filename(".hidden"))


class WindowsUnwritableNameTest(unittest.TestCase):
    """Names Windows cannot create as files, repaired before they reach disk.

    ``NUL`` resolves to the null device, so the write succeeds and the bytes
    are silently discarded; ``CON``/``COM1`` address devices too.  These are not
    cosmetic: a download that "completes" into ``NUL`` leaves no file at all.
    """

    def test_reserved_device_names_are_repaired(self):
        for name in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "com4"):
            fixed = sanitize_filename(name)
            self.assertNotEqual(fixed, name, msg=name)
            self.assertTrue(fixed.upper().startswith(name.upper()), msg=name)

    def test_reserved_name_with_extension_is_repaired(self):
        # ``CON.txt`` is equivalent to ``CON`` — the extension does not help.
        self.assertEqual(sanitize_filename("CON.txt"), "CON_.txt")
        self.assertEqual(sanitize_filename("nul.bin"), "nul_.bin")

    def test_similar_but_legal_names_are_untouched(self):
        for name in ("console.log", "com10.zip", "connection.json", "nullable"):
            self.assertEqual(sanitize_filename(name), name, msg=name)

    def test_over_long_component_is_truncated_but_keeps_extension(self):
        name = "a" * 400 + ".zip"
        fixed = sanitize_filename(name)
        self.assertLessEqual(len(fixed.encode("utf-16-le")) // 2, MAX_FILENAME_UNITS)
        self.assertTrue(fixed.endswith(".zip"))
        self.assertGreater(len(fixed), 100)

    def test_truncation_does_not_leave_a_trailing_dot(self):
        # Windows strips a trailing dot, so the stored name would differ from
        # the recorded one.  Truncation must not create that shape.
        fixed = sanitize_filename("a" * 300 + ".   b")
        self.assertFalse(fixed.endswith("."))
        self.assertFalse(fixed.endswith(" "))


class UniqueFilepathTest(unittest.TestCase):
    def test_fits_the_full_path_not_just_the_component(self):
        # A 240-unit name is legal on its own but overflows MAX_PATH inside a
        # real download directory; open() then fails with FileNotFoundError.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / ("deep" * 8)
            directory.mkdir(parents=True)
            path = unique_filepath(directory, "b" * 240 + ".mkv")
            self.assertLess(len(str(path)), MAX_PATH_BUDGET + 1)
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertEqual(path.stat().st_size, 1)

    def test_collision_suffix_still_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = unique_filepath(directory, "c" * 260 + ".zip")
            first.write_bytes(b"x")
            second = unique_filepath(directory, "c" * 260 + ".zip")
            self.assertNotEqual(first, second)
            self.assertIn("(1)", second.name)
            self.assertLess(len(str(second)), MAX_PATH_BUDGET + 1)

    def test_normal_names_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = unique_filepath(Path(tmp), "holiday-video.mp4")
            self.assertEqual(path.name, "holiday-video.mp4")


if __name__ == "__main__":
    unittest.main()
