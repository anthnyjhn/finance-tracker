# Finance Tracker

A self-hosted personal finance website, built from `Financial_Calculator.xlsx`. It has every
calculation from the spreadsheet, plus extra features for tracking how your finances grow.
It runs in Docker on port **8004**.

On the first start it loads the data from the spreadsheet, so your current situation is there
right away.

## Features

| Page | What it does | From the spreadsheet |
|---|---|---|
| **Dashboard** | Net worth, spendable money, monthly surplus, money lent, investment gain/loss, emergency-fund cover, goal progress, and charts | — |
| **Net worth** | Accounts and assets with editable balances, a breakdown by category, spendable (all-in) total, assets and liabilities | *Networth Calculator* |
| **Pay budget** | Takes fixed amounts out of each pay, then splits the rest by percentage. Shows per pay, per month and per year, and checks that everything is allocated | *Fortnight Budget* |
| **Transactions** *(new)* | Income and spending log, monthly cash flow, savings rate, spending by category, and bank CSV/OFX/QIF import | — |
| **Money lent** | Each loan's sources, repayment log, amount still owed, periods left, payoff date, actual repayment pace, and how each repayment splits back to its source funds | *Van Log*, *Outlander Log* |
| **Money parked** | Money held by family. Tracks deposits, interest and withdrawals, interest earned and yield | *MoneyParking*, *MoneyParking2* |
| **Investments** | Holdings (e.g. Bitcoin) with a purchase log, current value, profit/loss, breakdown by platform and average cost | *Crypto* |
| **Goals** *(extended)* | The net worth and spendable goals from the sheet, plus your own goals. A goal can track an account automatically. Shows ETA and how much you need to save each month | *Networth Calculator* goals |
| **Forecast** | Net worth and cash projection for 1–N years using your income and fixed expenses, with optional investment growth | *Networth Forecast* |
| **Tax return** *(rebuilt)* | Australian tax estimate with ATO rates for 2025-26 and 2026-27. Covers residents, foreign residents and working holiday makers, the low income tax offset, Medicare levy (including the low-income reduction), Medicare levy surcharge, HELP/study loan repayments and the $1,000 standard deduction (2026-27). Work expenses take a work-use % and are depreciated when they're equipment over $300. It also flags family-loan interest you haven't declared | *Tax Return Calculator* |
| **History** *(new)* | Saves a net-worth snapshot each day you use the app. Shows growth over the last 30 days, year to date, last 12 months and the monthly average | *Networth* table |
| **Import / export** | CSV, OFX, QIF, Excel and a full JSON backup. Also restore and reset | — |

### Export formats (moving to other finance apps)

- **CSV**: works with almost any app (YNAB, Actual Budget, Firefly III, Monarch, Google Sheets, Excel).
  Columns: `Date, Account, Payee, Category, Memo, Amount, Inflow, Outflow`. Dates are ISO (`YYYY-MM-DD`).
- **OFX 2.2**: the standard bank-statement format (GnuCash, Moneydance, Quicken, Banktivity, …).
  There is one statement per account, with stable transaction IDs so the same row isn't imported twice.
- **QIF**: Quicken Interchange Format (GnuCash, KMyMoney, HomeBank, Quicken, …). It holds several accounts, with dates as MM/DD/YYYY.
- **Excel (.xlsx)**: a summary sheet, every table and the full ledger.
- **JSON backup**: everything, including settings. Restore it with *Import / export → Restore a backup*.

All the money-movement exports include every transaction: the money you lent and the repayments,
money parked, and investment purchases. They also add an opening balance for each account,
so the other app ends up with the same balances.

---

## Running it on your server

### 1. Install Docker (skip if you have it)

On Ubuntu/Debian:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

```bash
sudo usermod -aG docker $USER
```

Log out and back in so the group change applies. Check that it worked with `docker compose version`.
You need Compose v2.24 or newer.

### 2. Get the code onto the server

```bash
git clone https://github.com/anthnyjhn/finance-tracker.git
```

```bash
cd finance-tracker
```

The repository is private, so git will ask you to log in. Use a GitHub personal access token as
the password, or set up an SSH key. You can also copy the folder over with `scp`.

### 3. (Recommended) Set a login password

```bash
cp .env.example .env
```

```bash
nano .env
```

Set `APP_USERNAME` and `APP_PASSWORD`. Your browser will ask for them when you open the site.
Leave `APP_PASSWORD` empty to turn off the login prompt. Only do that if the server can't be
reached from the internet.

### 4. Start it

```bash
docker compose up -d --build
```

Open **http://YOUR-SERVER-IP:8004**. Your data is stored in the Docker volume `finance-data`,
so it survives restarts, rebuilds and updates.

If you can't reach it, allow the port through your firewall. On Ubuntu with ufw:

```bash
sudo ufw allow 8004/tcp
```

### Everyday commands

| Task | Command |
|---|---|
| See logs | `docker compose logs -f` |
| Stop | `docker compose down` (your data is kept) |
| Update after pulling new code | `git pull && docker compose up -d --build` |
| Back up the database file | `docker compose cp finance-tracker:/data/finance.db ./finance-backup.db` |

You can also download a **JSON backup** from *Import / export* at any time. Doing this regularly is the easiest way to back up.

### Using it outside your home network

The app uses plain HTTP. To reach it over the internet, don't open port 8004 directly. Use one of these:

- **Tailscale** or another VPN: install it on the server and your phone, then open `http://server-name:8004`.
- **A reverse proxy with HTTPS**: Caddy, Nginx Proxy Manager or Traefik, in front of `localhost:8004`.

Keep `APP_PASSWORD` set in both cases.

### Starting fresh

*Import / export → Start over* has two options. One resets everything to the spreadsheet data, and the other erases everything.
Set `SEED_ON_FIRST_RUN=0` in `.env` to start with an empty database on a new install.

---

## Notes on the spreadsheet conversion

- **Savings** is stored as its own balance ($5,111.32). The sheet worked it out as bank total minus
  investments. Update it on the *Net worth* page.
- **Crypto**: the *Crypto* sheet's balance ($10,308.25) is used everywhere. The *Networth Calculator*
  sheet used a different figure ($9,535.02), so net worth here is $120,836.81 rather than $120,063.58.
- **Van loan**: the sheet divided what's still owed by $221.60 and called it "weeks left", but the log
  records $55.40 a week ($221.60 a month). The app treats it as $221.60 **monthly**, which gives the same
  "12" the sheet showed. Change it on the *Money lent* page if that's wrong.
- **Forecast**: uses the sheet's formula. Each year adds (income − fixed expenses) × 12, and
  year 1 also adds monthly income × 12.5% (super). You can set a growth rate as well.
- **Tax**: the sheet's bracket maths was correct. The rebuilt calculator adds the low income tax offset, works out the
  Medicare levy instead of using a typed-in figure, and rounds taxable income down to whole dollars as the ATO does.
  With your sheet's figures the refund estimate goes from $479.93 to $484.02, before any of the flagged items are changed.
- **Goal time left**: uses "expected savings per month", which is $3,980 (the sheet's 1,990 × 2). You can change this.

## Development

```bash
pip install -r requirements.txt pytest httpx
```

```bash
python -m pytest -q
```

```bash
uvicorn app.main:app --reload --port 8004
```

The tests check that the app reproduces the spreadsheet's numbers. The API is documented at `/api/docs`.
Everything is stored in SQLite: the database is `/data/finance.db` in Docker, and `./data/finance.db` when running locally.
