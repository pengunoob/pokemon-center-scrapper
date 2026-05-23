# Pokemon Center TCG Stock Scraper

This small Python program checks the Pokemon Center TCG Cards listing and prints
products that are not visibly marked as sold out, out of stock, or unavailable.

It only reads public listing pages. It does not try to bypass Pokemon Center's
queue, bot protection, checkout flow, purchase limits, or account controls.

## Setup

The scraper uses only Python's standard library. If Python 3 is installed on
your machine, no package install is needed.

## Run

```powershell
python pokemon_center_stock_scraper.py --pages 20 --csv pokemon_center_tcg_stock.csv
```

If the `python` command is not available on this Codex machine, use the bundled
Python runtime:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" pokemon_center_stock_scraper.py --pages 20 --csv pokemon_center_tcg_stock.csv
```

Useful options:

```powershell
python pokemon_center_stock_scraper.py --pages 5 --verbose
python pokemon_center_stock_scraper.py --keyword "Elite Trainer Box" --json etbs.json
python pokemon_center_stock_scraper.py --url "https://www.pokemoncenter.com/category/tcg-cards" --pages 20
```

## Notes

- The default URL is `https://www.pokemoncenter.com/category/tcg-cards`.
- The scraper treats products as candidates when the listing card is not marked
  with labels like `SOLD OUT`, `OUT OF STOCK`, or `UNAVAILABLE`.
- Pokemon Center sometimes shows a waiting room or bot-protection page. If that
  happens, the script exits with an explanation instead of trying to bypass it.
- Stock can change between seeing a product on the listing page and opening the
  product page.
