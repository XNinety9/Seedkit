import pytest

from seedkit.releases import parse, same_work_key


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Bunker.2023.S03E07.Episode.Name.MULTi.AD.1080p.ATV.WEB.SDR.H265-GRP.mkv", ("bunker", 2023, 3, 7, 1080)),
        ("Coach Story - S04E03 - Some Title 2160p.DV.HDR.x265-GRP.mkv", ("coach story", None, 4, 3, 2160)),
        ("The.Lawyer.Show.S04.MULTI.VFF.2160p.WEBRip.x265-GRP", ("the lawyer show", None, 4, None, 2160)),
        ("Some.Movie.2015.MULTi.1080p.WEB.H265-GRP.mkv", ("some movie", 2015, None, None, 1080)),
        ("2001.A.Space.Odyssey.1968.1080p", ("2001 a space odyssey", 1968, None, None, 1080)),
        ("Les.Misérables.2012.FRENCH.720p", ("les miserables", 2012, None, None, 720)),
        ("Show.Saison.2.FRENCH.720p", ("show", None, 2, None, 720)),
        ("[Group] Show.S01E01E02.1080p", ("show", None, 1, 1, 1080)),
    ],
)
def test_parse(name, expected):
    r = parse(name)
    assert (r.title, r.year, r.season, r.episode, r.resolution) == expected


def test_same_work_key_ignores_series_year_and_quality():
    assert same_work_key(parse("Bunker.2023.S03E07.1080p")) == same_work_key(parse("Bunker.S03E07.MULTi.2160p"))
    assert same_work_key(parse("Movie.2015.1080p")) != same_work_key(parse("Movie.2016.1080p"))
    assert same_work_key(parse("Some Random Name")) is None  # not identifiable: no year nor season
