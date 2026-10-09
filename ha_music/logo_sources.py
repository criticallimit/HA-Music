"""Install verified broadcaster SVG marks into the local web asset folder at build time.

No external image requests are needed after the add-on image is built.
"""
from hashlib import md5
from pathlib import Path
from urllib.request import urlopen, Request
import xml.etree.ElementTree as ET

ASSETS = {
    "1live": "WDR_1LIVE_Logo_2016.svg",
    "wdr2": "WDR_2_logo_2012.svg",
    "swr3": "SWR3_Logo.svg",
}
TARGET = Path(__file__).resolve().parent / "web"

def install():
    for station, filename in ASSETS.items():
        digest = md5(filename.replace(" ", "_").encode("utf-8")).hexdigest()
        url = "https://upload.wikimedia.org/wikipedia/commons/" + digest[0] + "/" + digest[:2] + "/" + filename
        req = Request(url, headers={"User-Agent": "HA-Music (logo asset import)"})
        with urlopen(req, timeout=25) as response:
            content = response.read(512001)
        if not 100 < len(content) <= 512000:
            raise ValueError(f"Invalid logo size: {station}")
        root = ET.fromstring(content)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError(f"Not an SVG: {station}")
        # Reject active SVG content before serving in the Ingress frontend.
        bad = {"script", "foreignObject"}
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] in bad:
                raise ValueError(f"Unexpected executable SVG element: {station}")
            for k, v in node.attrib.items():
                attribute = k.rsplit("}", 1)[-1].lower()
                if attribute.startswith("on"):
                    raise ValueError(f"Unexpected SVG event handler: {station}")
                if attribute == "href" and v.strip() and not v.strip().startswith("#"):
                    raise ValueError(f"External SVG reference: {station}")
        (TARGET / f"{station}.svg").write_bytes(content)
        print(f"Bundled original station logo: {station} ({len(content)} bytes)")

if __name__ == "__main__":
    install()

