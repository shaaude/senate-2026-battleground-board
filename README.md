# Senate 2026 Battleground Board

A one-page dashboard comparing **prediction markets** and **polling averages** for six 2026 U.S. Senate races heading into Election Day (Tuesday, November 3, 2026):

| Race | Democrat | Republican |
|---|---|---|
| Texas | James Talarico | Ken Paxton |
| Ohio (special) | Sherrod Brown | Jon Husted (incumbent) |
| Michigan (open, D-held) | Abdul El-Sayed | Mike Rogers |
| Maine | Troy Jackson | Susan Collins (incumbent) |
| South Carolina | Annie Andrews | Darline Graham Nordone (incumbent, appointed) |
| Minnesota (open, D-held) | Peggy Flanagan | Michele Tafoya |

**Live page:** https://shaaude.github.io/senate-2026-battleground-board/ (GitHub Pages, served from the `main` branch).

## What's on the page

- **Race cards**: Kalshi and Polymarket win probabilities, the RealClearPolitics average and its window, the PollCheck average, and a 14-day all-polls average computed on the page. Every figure shows the date it was taken.
- **Where the polls sit**: every poll from the last 30 days on one Republican-lead ↔ Democratic-lead axis, with the three averages marked.
- **Markets**: Kalshi vs. Polymarket by race, poll lead vs. market price, and Kalshi price movement since October 14.
- **All current polling**: a filterable timeline (race, likely voters only, hide party-linked pollsters) and a sortable, searchable table of every poll with a source link.

## About FiveThirtyEight

ABC News shut down FiveThirtyEight (538) in March 2025, so there is no 538 average to show. Its place is taken by [PollCheck](https://www.pollcheck.co.uk/us-midterms-2026)'s weighted averages plus the page's own 14-day plain mean of every poll listed. No independent average was found for South Carolina.

## Data

All figures are in [`data/`](data) as plain JSON, and each value carries a link to where it was found.

| File | Contents |
|---|---|
| `data/races.json` | One row per race: RCP average (`rcp_dem`, `rcp_rep`, `rcp_margin`, `rcp_window`), PollCheck average (`ind_*`), Kalshi and Polymarket Democratic win price in whole percent (`kalshi_dem`, `poly_dem`) with `*_as_of` dates and `*_url` sources, 2024 presidential margin (`pres24`, + = Harris), and `pulled_at`. |
| `data/polls.json` | One row per poll: `pollster`, `date` (field end or release), `dates`, `sample`, `population` (LV/RV), `dem`, `rep`, `margin` (Dem − Rep), `partisan` (declared lean of pollster or sponsor), `url`. |
| `data/kalshi_history.json` | Dated Kalshi Democratic win prices per race (hourly from Oct 31). |
| `data/status.json` | Time of the last automatic price check and any per-venue errors. |

Margins are always Democrat minus Republican. Market prices are traded contract prices read as a percent chance; they are not polls and not a forecast by this site.

## How the data updates

- **Market prices, every 15 minutes:** the GitHub Actions workflow [`.github/workflows/markets.yml`](.github/workflows/markets.yml) runs [`scripts/update_markets.py`](scripts/update_markets.py), which reads Kalshi's and Polymarket's public market-data feeds (no keys), updates `kalshi_*`/`poly_*` in `data/races.json` with the time each price was read, keeps one Kalshi point per race per hour in `data/kalshi_history.json`, and records the check time and any feed errors in `data/status.json`. A feed that can't be read keeps its last price and timestamp. GitHub may start scheduled runs a few minutes late.
- **Polls and averages:** gathered by a separate research pull (time shown as "Polls and averages pulled").
- **On the page:** the header shows when market prices were last checked and how long ago; the page re-reads the data files every 5 minutes, and **Check for new data** does so immediately.

## Running it locally

It is a single static page with no build step:

```sh
python3 -m http.server 8000
# open http://localhost:8000
```

It loads [d3](https://d3js.org/) v7 from cdnjs and fonts from Google Fonts.

## Disclaimer

An independent, non-partisan compilation of publicly reported figures. Not affiliated with RealClearPolitics, PollCheck, Kalshi or Polymarket. Figures can lag their sources; follow the links for the latest. Nothing here is financial advice.
