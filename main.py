import re
import json
from typing import List
import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

wp_client = httpx.Client(
    timeout=10, base_url="https://public-api.wordpress.com/rest/v1.1/sites/liftblog.com"
)
gm_client = httpx.Client(
    timeout=10,
    headers={
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36"
    },
)
KNOWN_COLUMNS = list(
    map(
        lambda x: str(x).lower(),
        [
            "Status",
            "Lift Name",
            "Type",
            "Manufacturer",
            "Years of Operation",
            "Capacity",
            "Vertical Rise",
            "Length",
            "Horsepower",
            "Line Speed",
            "Chairs",
            "Towers",
            "Drive",
            "Tension",
            "Ride Time",
            "Notes",
        ],
    ),
)

from enum import Enum


class FeatureStatus(Enum):
    UNKNOWN = 0
    OPERATING = 1
    REMOVED = 2
    CONSTRUCTION = 3


class FeatureType(Enum):
    UNKNOWN = 0
    CHAIR = 1
    CHAIR_HISPEED = 2
    BAR = 3
    PLATTER = 4
    CARPET = 5
    TRAM = 6  # (Cablecar)
    GONDOLA = 7
    CHONDOLA = 8  # Chair/Gondola combo
    BIG_GONDOLA = 9  # "3S" Gondola from Doppelmayr
    CABRIOLET = 10  # Open-air gondola
    FUNITEL = 11
    HANDLE_TOW = 12


COUNTRY_SLUGS = ["united-states", "canada"]  # Roots of liftblog to crawl
for country in COUNTRY_SLUGS:
    print("Country:", country)
    r = wp_client.get("/posts/slug:" + country)
    r.raise_for_status()
    bs = BeautifulSoup(r.json()["content"], "html.parser")
    for li in bs.find_all("li"):
        # Find all territories
        territory: Tag = li.find("a")
        territory_name = territory.get_text()
        print("Territory:", territory_name)
        # temporary
        if (
            territory_name.startswith("A")
            or territory_name.startswith("C")
            or territory_name.startswith("F")
            or territory_name.startswith("G")
        ):
            continue

        territory_lb_link = str(territory.attrs["href"]).replace("http://", "https://")
        assert territory_lb_link.startswith("https://liftblog.com/")
        territory_slug = territory_lb_link.removeprefix(
            "https://liftblog.com/"
        ).removesuffix("/")

        # Get territory page
        r = wp_client.get("/posts/slug:" + territory_slug)
        r.raise_for_status()
        tbs = BeautifulSoup(r.json()["content"], "html.parser")
        territory_map_frame = tbs.find("iframe")  # Google my maps iframe
        territory_map_frame_url = (
            territory_map_frame.attrs["src"]
            .replace("/u/0", "")
            .replace("http://", "https://")
            .replace("/embed?", "/view?")
        )
        assert territory_map_frame_url.startswith("https://www.google.com/maps")

        # Get google my map
        rg = gm_client.get(territory_map_frame_url)
        rg.raise_for_status()
        pagedata_match = re.findall(r'var _pageData = "(.*)";', rg.text)
        assert len(pagedata_match) == 1
        pagedata = json.loads(str(pagedata_match[0]).replace('\\"', '"'))
        assert len(pagedata) == 2
        map_data = pagedata[1]
        assert map_data[0] == "mf.map"

        # Log all points on the map as ski areas
        skiarea_locations = []
        datapoints = map_data[6][0][4]
        assert len(datapoints) > 0
        for datapoint in datapoints:
            assert str(datapoint[0][0]).endswith("/1411-rec-winter-skilift.png")
            assert len(datapoint[4][0][1]) == 2
            skiarea_locations.append(
                {"name": str(datapoint[5][0][0]).strip(), "latlong": datapoint[4][0][1]}
            )

        # Go through territory's ski areas
        for li in tbs.find_all("li"):
            skiarea: Tag = li.find("a")
            skiarea_name = skiarea.get_text().strip()
            print("Ski Area:", skiarea_name)
            skiarea_lb_link = str(skiarea.attrs["href"]).replace("http://", "https://")
            assert skiarea_lb_link.startswith("https://liftblog.com/")
            skiarea_slug = skiarea_lb_link.removeprefix(
                "https://liftblog.com/"
            ).removesuffix("/")

            # Associate google my map entry
            skiarea_latlong = None
            for loc in skiarea_locations:
                if loc["name"].replace("’", "'").replace(
                    "–", "-"
                ) == skiarea_name.replace("’", "'").replace("–", "-"):
                    skiarea_latlong = loc["latlong"]
            if skiarea_latlong is None:
                for loc in skiarea_locations:
                    if (
                        loc["name"]
                        .replace("’", "'")
                        .replace("–", "-")
                        .startswith(skiarea_name.replace("’", "'").replace("–", "-"))
                    ):
                        skiarea_latlong = loc["latlong"]
            assert skiarea_latlong is not None

            # Get skiarea page
            r = wp_client.get("/posts/slug:" + skiarea_slug)
            r.raise_for_status()
            sbs = BeautifulSoup(r.json()["content"], "html.parser")
            google_sheet_url = str(sbs.find("iframe").attrs["src"]).replace(
                "&amp;", "&"
            )
            if "gid=" not in google_sheet_url:
                # Need to get sheet gid
                r = gm_client.get(google_sheet_url)
                r.raise_for_status()
                google_sheet_url += re.search(r"&gid=\d+", r.text).group(0)
            google_sheet_url = google_sheet_url.replace("/pubhtml", "/pubhtml/sheet")

            assert google_sheet_url.startswith("https://docs.google.com/spreadsheets")
            r = gm_client.get(google_sheet_url)
            r.raise_for_status()
            dbs = BeautifulSoup(r.text, "html.parser")
            table_data = dbs.find("tbody")
            assert table_data is not None
            is_header = True
            headers: List[str] = []
            skiarea_data = {
                "name": skiarea_name,
                "latlong": skiarea_latlong,
                "features": [],
            }
            for row in table_data.find_all("tr"):
                raw_columns: Tag = row.find_all("td")
                i = 0
                feature_data = {
                    "name": None,  # Lift name
                    "status": FeatureStatus.UNKNOWN,  # Lift status
                    "accomodates": [
                        0
                    ],  # How many an item on the line accomodates in humans
                    "type": FeatureType.UNKNOWN,  # Type(s) of lift
                    "pulse": False,
                    "notes": "",
                }
                for col in raw_columns:
                    txt = col.get_text().strip()
                    if is_header:
                        txt = txt.lower()
                        assert txt in KNOWN_COLUMNS
                        headers.append(txt)
                    else:
                        # Data row
                        ch = headers[i]  # Corresponding header
                        if ch == "status":  # Lift status
                            txt = txt.lower()
                            if txt == "operating":
                                feature_data["status"] = FeatureStatus.OPERATING
                            elif txt == "removed":
                                feature_data["status"] = FeatureStatus.REMOVED
                            elif txt == "construction":
                                feature_data["status"] = FeatureStatus.CONSTRUCTION
                        elif ch == "lift name":
                            feature_data["name"] = txt
                        elif ch == "type":
                            txt = txt.lower()
                            if txt == "double":
                                feature_data["accomodates"] = [2]
                                feature_data["type"] = FeatureType.CHAIR
                            elif txt == "quad":
                                feature_data["accomodates"] = [4]
                                feature_data["type"] = FeatureType.CHAIR
                            elif txt == "high speed quad":
                                feature_data["accomodates"] = [4]
                                feature_data["type"] = FeatureType.CHAIR_HISPEED
                            elif txt == "high speed six":
                                feature_data["accomodates"] = [6]
                                feature_data["type"] = FeatureType.CHAIR_HISPEED
                            elif txt == "high speed eight":
                                feature_data["accomodates"] = [8]
                                feature_data["type"] = FeatureType.CHAIR_HISPEED
                            elif txt == "high speed triple":
                                feature_data["accomodates"] = [3]
                                feature_data["type"] = FeatureType.CHAIR_HISPEED
                            elif txt == "triple":
                                feature_data["accomodates"] = [3]
                                feature_data["type"] = FeatureType.CHAIR
                            elif txt == "t-bar":
                                feature_data["accomodates"] = [2]
                                feature_data["type"] = FeatureType.BAR
                            elif txt == "j-bar":
                                feature_data["accomodates"] = [1]
                                feature_data["type"] = FeatureType.BAR
                            elif txt == "platter":
                                feature_data["accomodates"] = [1]
                                feature_data["type"] = FeatureType.PLATTER
                            elif txt == "handle tow":
                                feature_data["accomodates"] = [1]
                                feature_data["type"] = FeatureType.HANDLE_TOW
                            elif txt == "single":
                                feature_data["accomodates"] = [1]
                                feature_data["type"] = FeatureType.CHAIR
                            elif txt.startswith("gondola "):
                                feature_data["accomodates"] = [int(txt.split(" ")[1])]
                                feature_data["type"] = FeatureType.GONDOLA
                            elif txt.startswith("cabriolet "):
                                feature_data["accomodates"] = [int(txt.split(" ")[1])]
                                feature_data["type"] = FeatureType.CABRIOLET
                            elif txt.startswith("funitel "):
                                feature_data["accomodates"] = [int(txt.split(" ")[1])]
                                feature_data["type"] = FeatureType.FUNITEL
                            elif txt.startswith("tram "):
                                feature_data["accomodates"] = [int(txt.split(" ")[1])]
                                feature_data["type"] = FeatureType.TRAM
                            elif txt == "pulse double":
                                feature_data["accomodates"] = [2]
                                feature_data["type"] = FeatureType.CHAIR
                                feature_data["pulse"] = True
                            elif txt == "pulse quad":
                                feature_data["accomodates"] = [4]
                                feature_data["type"] = FeatureType.CHAIR
                                feature_data["pulse"] = True
                            elif txt == "pulse gondola":  # TODO FIX THESE!
                                feature_data["type"] = FeatureType.GONDOLA
                                feature_data["pulse"] = True
                            elif txt.startswith("pulse gondola "):
                                feature_data["accomodates"] = [int(txt.split(" ")[2])]
                                feature_data["type"] = FeatureType.GONDOLA
                                feature_data["pulse"] = True
                            elif txt.startswith("3s gondola "):
                                feature_data["accomodates"] = [int(txt.split(" ")[2])]
                                feature_data["type"] = FeatureType.BIG_GONDOLA
                            elif txt.startswith("chondola "):
                                acc = txt.split(" ")[1].split("/")
                                feature_data["accomodates"] = [int(acc[0]), int(acc[1])]
                                feature_data["type"] = FeatureType.CHONDOLA
                            elif txt.startswith("pulse chondola "):
                                acc = txt.split(" ")[2].split("/")
                                feature_data["accomodates"] = [int(acc[0]), int(acc[1])]
                                feature_data["type"] = FeatureType.CHONDOLA
                                feature_data["pulse"] = True
                            elif txt == "double/t-bar":
                                # TODO what do we do with this?
                                pass
                            elif txt == "":
                                feature_data["type"] = FeatureType.UNKNOWN
                            else:
                                raise Exception("unknown lift type " + txt)
                        elif ch == "manufacturer":
                            pass
                        elif ch == "years of operation":
                            pass
                        elif ch == "capacity":
                            pass
                        elif ch == "vertical rise":
                            pass
                        elif ch == "length":
                            pass
                        elif ch == "horsepower":
                            pass
                        elif ch == "line speed":
                            pass
                        elif ch == "chairs":
                            pass
                        elif ch == "towers":
                            pass
                        elif ch == "drive":
                            pass
                        elif ch == "tension":
                            pass
                        elif ch == "ride time":
                            pass
                        elif ch == "notes":
                            pass
                        else:
                            raise Exception("unknown corresponding header " + ch)

                    i += 1

                if is_header:
                    is_header = False
                else:
                    skiarea_data["features"].append(feature_data)

            print(skiarea_data)

            # Post-process table data


wp_client.close()
gm_client.close()
