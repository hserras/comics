"""
Testes dos parsers de HTML do scraper com páginas reais guardadas em fixtures/ (reduzidas ao
título e ao conteúdo do artigo). Se o site mudar de formato, estes testes falham sem pedidos
à rede. Cada fixture cobre um formato que já nos partiu o extrator.
"""
import unittest
from pathlib import Path

from scraper import parse_download_links, parse_pack_issues, parse_packs, resolve_issue_download_links
from titles import parse_title

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def issues_of(name: str, pack_url: str) -> list[dict]:
    return parse_pack_issues(load(name), pack_url)


class TagListingTests(unittest.TestCase):
    def test_lists_packs_with_title_and_url(self):
        packs = parse_packs(load("tag_listing.html"))
        self.assertEqual(len(packs), 12)
        self.assertEqual(packs[0]["title"], "2026.11.18 Weekly Pack")
        self.assertEqual(packs[0]["url"], "https://getcomics.org/other-comics/2026-11-18-weekly-pack/")
        self.assertEqual(len({p["url"] for p in packs}), 12)

    def test_only_packs_keeps_weekly_packs(self):
        self.assertEqual(len(parse_packs(load("tag_listing.html"), only_packs=True)), 12)


class PackFormatTests(unittest.TestCase):
    def test_recent_pack_reads_only_the_marvel_section(self):
        issues = issues_of("pack_recent_weekly.html", "https://getcomics.org/other-comics/2026-11-18-weekly-pack/")
        self.assertEqual(len(issues), 15)
        self.assertEqual(issues[0]["title"], "Black Panther – Namor – Doomed #3")
        self.assertEqual(issues[0]["article_url"], "https://getcomics.org/marvel/black-panther-namor-doomed-3-2026/")
        self.assertEqual(issues[-1]["title"], "Uncle Scrooge – A Christmas Carol #1")
        self.assertTrue(all("/marvel/" in i["article_url"] for i in issues))
        self.assertTrue(all(parse_title(i["title"]) for i in issues))

    def test_2020_pack_with_other_publishers_stops_at_next_section(self):
        # O HTML tem <p> aninhados e secções não-Marvel depois da Marvel
        issues = issues_of("pack_2020_weekly_sections.html", "https://getcomics.org/other-comics/2020-05-20-weekly-pack/")
        self.assertEqual(len(issues), 9)
        self.assertEqual(issues[0]["title"], "2020 Ironheart #1")
        self.assertEqual(issues[-1]["title"], "X-Men Weddings")
        self.assertTrue(all("/marvel/" in i["article_url"] for i in issues))
        self.assertFalse(any("download" in i["title"].lower() for i in issues))

    def test_2020_pack_with_a_single_marvel_issue(self):
        issues = issues_of("pack_2020_weekly_single_marvel.html", "https://getcomics.org/other-comics/2020-04-15-weekly-pack/")
        self.assertEqual([i["title"] for i in issues], ["League of Legends – Zed 06 (of 06)"])

    def test_2020_marvel_week_pack(self):
        issues = issues_of("pack_2020_marvel_week.html", "https://getcomics.org/marvel/2020-03-04-marvel-week/")
        self.assertEqual(len(issues), 20)
        self.assertEqual(issues[0]["title"], "2020 Rescue #1 (Of 2)")
        self.assertEqual(issues[-1]["title"], "Sword Master #9")

    def test_2016_pack_has_no_marvel_header_and_lists_every_issue(self):
        issues = issues_of("pack_2016_marvel_week.html", "https://getcomics.org/marvel/2016-12-28-marvel-week/")
        self.assertEqual(len(issues), 30)
        self.assertEqual(issues[0]["title"], "Black Panther #09 (2017)")
        self.assertEqual(issues[-1]["title"], "X-Men 92 #010 (2017)")
        self.assertTrue(all("sh.st" not in i["article_url"] for i in issues))

    def test_2016_pack_with_shortener_links_is_listed_for_manual_download(self):
        issues = issues_of("pack_2016_shst.html", "https://getcomics.org/marvel/2016-01-20-marvel-week/")
        self.assertEqual(len(issues), 20)
        self.assertEqual(issues[0]["title"], "Astonishing Ant-Man #4")
        self.assertEqual(issues[0]["article_url"], "https://sh.st/n1qHZ")
        self.assertTrue(all("sh.st" in i["article_url"] for i in issues))

    def test_single_file_pack_falls_back_to_the_pack_itself(self):
        url = "https://getcomics.org/marvel/2015-05-13-marvel-now/"
        self.assertEqual(
            issues_of("pack_2015_single_file.html", url),
            [{"title": "2015.05.13 Marvel NOW+", "article_url": url}],
        )


class IssuePageTests(unittest.TestCase):
    URL = "https://getcomics.org/marvel/black-panther-namor-doomed-3-2026/"

    def test_download_links_prefer_the_direct_ones(self):
        links = parse_download_links(load("issue_page.html"), self.URL)
        self.assertEqual(
            [link["label"] for link in links],
            ["DOWNLOAD NOW", "PIXELDRAIN", "TERABOX", "VIKINGFILE", "DATANODES"],
        )
        self.assertEqual([link["is_direct_hint"] for link in links], [True, True, False, False, False])
        self.assertTrue(links[0]["url"].startswith("https://getcomics.org/dls/"))

    def test_shortener_links_are_not_resolved(self):
        self.assertEqual(resolve_issue_download_links("https://sh.st/n1qHZ"), [])


if __name__ == "__main__":
    unittest.main()
