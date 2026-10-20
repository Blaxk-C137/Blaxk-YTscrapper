#!/usr/bin/env python3
"""Tests for the pure helpers in youtube_creator_finder.py.

These cover the parsing that decides what lands in your CSV — a regex that
over-matches here puts junk addresses or a wrong channel's stats in your list.

Run:  python3 -m unittest test_youtube_creator_finder -v
"""

import unittest

from youtube_creator_finder import (
    build_channel_url,
    channel_ids_from,
    clean_country,
    extract_emails,
    extract_socials,
    flat,
    normalise_handle,
    parse_about_stats,
    parse_joined_year,
)

# Captured verbatim from a real About page (MKBHD), including the tabs and
# blank lines, so the parser is tested against what YouTube actually emits.
REAL_ABOUT_TEXT = """
More info
\tSign in to see email address

\twww.youtube.com/@mkbhd

\tUnited States

\tJoined Mar 21, 2008

\t21.3M subscribers

\t1,852 videos

\t5,687,525,589 views
Share channel
"""

# A channel with no country line — the parser must not grab the URL above it.
NO_COUNTRY_ABOUT_TEXT = """
More info
\twww.youtube.com/@somebody

\tJoined Jun 4, 2015

\t12.4K subscribers

\t88 videos

\t1,203,441 views
"""


class TestExtractEmails(unittest.TestCase):
    def test_finds_plain_address(self):
        self.assertEqual(extract_emails("contact me at bob@gmail.com"),
                         ["bob@gmail.com"])

    def test_lowercases_for_stable_dedupe(self):
        self.assertEqual(extract_emails("Bob@Gmail.COM"), ["bob@gmail.com"])

    def test_dedupes_repeated_address(self):
        self.assertEqual(extract_emails("a@x.com and a@x.com and b@x.com"),
                         ["a@x.com", "b@x.com"])

    def test_strips_trailing_period(self):
        self.assertEqual(extract_emails("reach me at bob@gmail.com."),
                         ["bob@gmail.com"])

    def test_drops_platform_denylist(self):
        self.assertEqual(extract_emails("write to support@youtube.com"), [])

    def test_finds_multiple_in_business_blurb(self):
        text = "Business: bookings@creator.com | Press: press@creator.com"
        self.assertEqual(extract_emails(text),
                         ["bookings@creator.com", "press@creator.com"])

    def test_empty_and_none_are_safe(self):
        self.assertEqual(extract_emails(""), [])
        self.assertEqual(extract_emails(None), [])

    def test_ignores_non_addresses(self):
        self.assertEqual(extract_emails("no email here, just @handle and a.b"), [])


class TestExtractSocials(unittest.TestCase):
    def test_finds_instagram_handle(self):
        self.assertEqual(extract_socials("instagram.com/creator_one"),
                         {"instagram": ["creator_one"]})

    def test_finds_twitter_and_x(self):
        got = extract_socials("twitter.com/old_handle x.com/new_handle")
        self.assertEqual(got["twitter"], ["new_handle", "old_handle"])

    def test_finds_tiktok_handle(self):
        self.assertEqual(extract_socials("tiktok.com/@creator"),
                         {"tiktok": ["creator"]})

    def test_finds_linkedin(self):
        self.assertEqual(extract_socials("linkedin.com/in/some-creator"),
                         {"linkedin": ["some-creator"]})

    def test_multiple_platforms_at_once(self):
        text = "instagram.com/ig1 and tiktok.com/@tt1"
        self.assertEqual(extract_socials(text),
                         {"instagram": ["ig1"], "tiktok": ["tt1"]})

    def test_drops_platform_plumbing_handles(self):
        self.assertEqual(extract_socials("instagram.com/share"), {})

    def test_no_socials_returns_empty_dict(self):
        self.assertEqual(extract_socials("just some text"), {})
        self.assertEqual(extract_socials(None), {})


class TestBuildChannelUrl(unittest.TestCase):
    def test_handle_becomes_about_page(self):
        self.assertEqual(build_channel_url("@mkbhd"),
                         "https://www.youtube.com/@mkbhd/about")

    def test_channel_id_becomes_about_page(self):
        self.assertEqual(build_channel_url("UCBJycsmduvYEL83R_U4JriQ"),
                         "https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ/about")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(build_channel_url("  @mkbhd  "),
                         "https://www.youtube.com/@mkbhd/about")


class TestParseJoinedYear(unittest.TestCase):
    def test_full_date(self):
        self.assertEqual(parse_joined_year("Joined Mar 12, 2014", now_year=2026), 2014)

    def test_relative_years_ago(self):
        self.assertEqual(parse_joined_year("Joined 3 years ago", now_year=2026), 2023)

    def test_relative_takes_precedence_over_digits(self):
        # "6 years ago" must not be read as the year 6.
        self.assertEqual(parse_joined_year("Joined 6 years ago", now_year=2026), 2020)

    def test_empty_returns_none(self):
        self.assertIsNone(parse_joined_year(""))
        self.assertIsNone(parse_joined_year(None))

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_joined_year("Joined recently"))


class TestParseAboutStats(unittest.TestCase):
    def test_extracts_subscribers(self):
        self.assertEqual(parse_about_stats(REAL_ABOUT_TEXT)["subscribers"], "21.3M")

    def test_extracts_video_count(self):
        self.assertEqual(parse_about_stats(REAL_ABOUT_TEXT)["videos"], "1,852")

    def test_extracts_total_views(self):
        self.assertEqual(parse_about_stats(REAL_ABOUT_TEXT)["views"],
                         "5,687,525,589")

    def test_extracts_country(self):
        self.assertEqual(parse_about_stats(REAL_ABOUT_TEXT)["country"],
                         "United States")

    def test_extracts_joined_and_year(self):
        got = parse_about_stats(REAL_ABOUT_TEXT)
        self.assertEqual(got["joined"], "Joined Mar 21, 2008")
        self.assertEqual(got["account_year"], 2008)

    def test_extracts_handle(self):
        self.assertEqual(parse_about_stats(REAL_ABOUT_TEXT)["handle"], "@mkbhd")

    def test_country_absent_is_omitted_not_url(self):
        # With no country line, the URL above "Joined" must not become country.
        got = parse_about_stats(NO_COUNTRY_ABOUT_TEXT)
        self.assertNotIn("country", got)
        self.assertEqual(got["subscribers"], "12.4K")

    def test_description_line_is_not_mistaken_for_country(self):
        # Real case: an email blurb sat directly above "Joined" and was being
        # written into the CSV as the channel's country.
        text = """
More info
\tSign in to see email address

\twww.youtube.com/@someone

\tFor enquires someone@gmail.com

\tJoined May 2, 2019

\t8.64K subscribers

\t627 videos
"""
        got = parse_about_stats(text)
        self.assertNotIn("country", got)
        self.assertEqual(got["subscribers"], "8.64K")
        self.assertEqual(got["account_year"], 2019)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(parse_about_stats(""), {})
        self.assertEqual(parse_about_stats(None), {})

    def test_video_count_not_confused_with_subscribers(self):
        # Both use the same K/M/B suffix shape; they must not cross-contaminate.
        got = parse_about_stats(REAL_ABOUT_TEXT)
        self.assertEqual(got["videos"], "1,852")
        self.assertNotEqual(got["videos"], got["subscribers"])


class TestCountryIsValidated(unittest.TestCase):
    """
    The line above "Joined" is only sometimes a country. On a signed-in
    profile the reveal button reads "View email address" (not "Sign in to
    see email address"), and a channel with no country set leaves whatever
    the description blurb ends with sitting directly above "Joined".
    Every case below was taken from live output — all four were written
    into the CSV's country column before this validation existed.
    """

    def test_signed_in_reveal_button_is_not_a_country(self):
        text = """
More info
\tView email address

\twww.youtube.com/@TselmegVlog

\tJoined 13 Jan 2025

\t293K subscribers

\t79 videos

\t39,339,914 views
Share channel
"""
        self.assertNotIn("country", parse_about_stats(text))

    def test_emoji_description_tail_is_not_a_country(self):
        text = """
More info
\tView email address

\twww.youtube.com/@AsyDarlynVlogs

\tEnjoy😊😊

\tJoined 11 Jul 2022

\t30.4K subscribers
"""
        self.assertNotIn("country", parse_about_stats(text))

    def test_shouted_socials_line_is_not_a_country(self):
        text = """
More info
\tView email address

\twww.youtube.com/@SHARIKAVLOGS

\tFOLLOW ME ON  ; YOUTUBE,INSTAGRAM

\tJoined 14 Oct 2020

\t136K subscribers
"""
        self.assertNotIn("country", parse_about_stats(text))

    def test_sponsorship_blurb_is_not_a_country(self):
        text = """
More info
\tView email address

\twww.youtube.com/@Peculiars_Lifestyle0

\tFor sponsorships/ collaborations email me

\tJoined 27 Jan 2026

\t2.1K subscribers
"""
        self.assertNotIn("country", parse_about_stats(text))

    def test_real_countries_still_parse(self):
        for country in ("Nigeria", "Vietnam", "United States", "India",
                        "Bangladesh", "United Kingdom", "South Korea"):
            text = ("More info\n\tView email address\n\n"
                    "\twww.youtube.com/@someone\n\n"
                    "\t%s\n\n\tJoined 2 Aug 2019\n\n\t11.3K subscribers\n"
                    % country)
            self.assertEqual(parse_about_stats(text).get("country"), country,
                             "%s should still be recognised" % country)

    def test_country_match_is_case_insensitive_but_returns_canonical(self):
        text = ("More info\n\twww.youtube.com/@someone\n\n"
                "\tunited states\n\n\tJoined 2 Aug 2019\n")
        self.assertEqual(parse_about_stats(text).get("country"),
                         "United States")


class TestHandleNormalisation(unittest.TestCase):
    def test_percent_encoded_vanity_handle_is_decoded(self):
        # Live case: a Vietnamese channel came through as the URL-encoded
        # vanity path "L%C3%A2mVlog" instead of "@LâmVlog".
        self.assertEqual(normalise_handle("L%C3%A2mVlog"), "@LâmVlog")

    def test_bare_handle_gets_at_prefix(self):
        self.assertEqual(normalise_handle("mkbhd"), "@mkbhd")

    def test_existing_at_prefix_is_kept(self):
        self.assertEqual(normalise_handle("@mkbhd"), "@mkbhd")

    def test_empty_and_none_are_safe(self):
        self.assertEqual(normalise_handle(""), "")
        self.assertEqual(normalise_handle(None), "")


class TestCleanCountry(unittest.TestCase):
    def test_known_country_is_canonicalised(self):
        self.assertEqual(clean_country("united states"), "United States")
        self.assertEqual(clean_country("  Nigeria  "), "Nigeria")

    def test_trailing_colon_is_tolerated(self):
        self.assertEqual(clean_country("India:"), "India")

    def test_non_country_values_are_dropped(self):
        for junk in ("View email address", "Enjoy😊😊",
                     "FOLLOW ME ON  ; YOUTUBE,INSTAGRAM",
                     "For sponsorships/ collaborations", ""):
            self.assertEqual(clean_country(junk), "",
                             "%r should not survive as a country" % junk)

    def test_none_is_safe(self):
        self.assertEqual(clean_country(None), "")


class TestFlat(unittest.TestCase):
    def test_newlines_become_a_separator(self):
        # Regression: raw newlines in the description made naive CSV viewers
        # break rows apart, so every channel below looked misnamed.
        self.assertEqual(flat("line one\nline two"), "line one | line two")

    def test_blank_lines_collapse(self):
        self.assertEqual(flat("a\n\n\nb"), "a | b")

    def test_truncates_on_a_word_boundary(self):
        got = flat("alpha beta gamma delta", 12)
        self.assertTrue(got.endswith("…"))
        self.assertFalse(got.rstrip("…").endswith(" "))

    def test_empty_and_none_are_safe(self):
        self.assertEqual(flat(""), "")
        self.assertEqual(flat(None), "")


class TestChannelIdsFrom(unittest.TestCase):
    def test_finds_browse_id_in_parsed_response(self):
        # Regression: re-serializing a parsed dict with json.dumps defaults
        # produced '"browseId": "UC..."' (with a space), which the pattern
        # missed — so search silently returned zero channels.
        data = {"contents": [{"channelRenderer": {"navigationEndpoint": {
            "browseEndpoint": {"browseId": "UCBJycsmduvYEL83R_U4JriQ"}}}}]}
        self.assertEqual(channel_ids_from(data), ["UCBJycsmduvYEL83R_U4JriQ"])

    def test_dedupes_preserving_order(self):
        data = {"a": {"browseId": "UC" + "a" * 22},
                "b": {"browseId": "UC" + "b" * 22},
                "c": {"browseId": "UC" + "a" * 22}}
        self.assertEqual(channel_ids_from(data),
                         ["UC" + "a" * 22, "UC" + "b" * 22])

    def test_ignores_non_channel_browse_ids(self):
        data = {"browseId": "FEwhat_to_watch"}
        self.assertEqual(channel_ids_from(data), [])

    def test_safe_on_junk(self):
        self.assertEqual(channel_ids_from(None), [])
        self.assertEqual(channel_ids_from("nope"), [])


if __name__ == '__main__':
    unittest.main()
