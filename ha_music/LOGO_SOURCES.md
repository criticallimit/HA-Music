# Radio logo assets

The HA Music container bundles broadcaster-provided station marks during the Docker build. The build downloads and validates the following original SVG files, then stores them under `/app/web`:

| Station | Source |
|---|---|
| 1LIVE | https://commons.wikimedia.org/wiki/File:WDR_1LIVE_Logo_2016.svg |
| WDR 2 | https://commons.wikimedia.org/wiki/File:WDR_2_logo_2012.svg |
| SWR3 | https://commons.wikimedia.org/wiki/File:SWR3_Logo.svg |

The downloaded marks replace the development placeholders in the container, and are subsequently served locally by HA Music. Unlike the original provisional implementation, no image host is consulted while the add-on runs. The build fails if one of the original files cannot be obtained or verified; a failed import must not silently display a substitute as an original logo.

The marks and names remain trademarks of their respective broadcasters. See the linked file pages for authorship and any applicable license terms and trademark notices.
