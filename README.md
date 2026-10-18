# Coin price search

A small Django website that searches CoinGecko's public API for coins by name or ticker and displays matching prices in INR. It does not need exchange credentials.

The separate `/portfolio/` page requests Delta Exchange India wallet balances and open positions. It uses `DELTA_API_KEY` and `DELTA_API_SECRET` from `.env` and signs private requests with HMAC-SHA256.

The portfolio and spot trading pages require a signed-in account. Sign up at `/signup/` to create an account, or use Django's `createsuperuser` command for an administrator account. After installing dependencies, initialize the database:
```sh
python manage.py migrate
```
Sign in at `/login/`; use the **Log out** button to end the session. Coin search remains public.

The portfolio page also offers real Delta **spot** market and limit orders for live INR spot pairs returned by Delta. Both coin-quantity and INR-value sizing are available. Each order is previewed and must be explicitly confirmed before the server sends a POST request to Delta. Market INR sizing uses an indicative spot price; actual execution value can differ. A successful order response does not guarantee a fill, especially for a limit order. Refresh the portfolio after a fill to see updated wallet balances. For safety, order preview and confirmation endpoints accept requests only from loopback connections; keep the development server bound to `127.0.0.1` and do not expose it publicly.

Only markets listed as live spot products by Delta can be traded; this is not every coin shown by CoinGecko search. Delta's Trading API permission can also permit other trading actions on the account. The website restricts its order form to Delta spot markets and sends only the specific user-confirmed order. Use a dedicated, IP-restricted key and keep it in `.env`.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`.
3. In Delta Exchange India API Management, create a separate API key for this website. Delta's [official API documentation](https://docs.delta.exchange/) indicates account endpoints and order placement require **Trading** permission. This permission can also allow other trading actions on your account. Set the key's IP whitelist as required by Delta, and understand the permission before enabling it.
4. Add the Delta credentials to `.env`:
   ```env
   DELTA_API_KEY=your_delta_api_key
   DELTA_API_SECRET=your_delta_api_secret
   ```
   Never commit `.env` or paste these credentials into chat.
5. Initialize authentication with the migrate command above, then sign up in the browser or run `python manage.py createsuperuser`.
6. Start the site on port 9000: `python manage.py runserver`. To choose a different host or port, pass it explicitly, for example `python manage.py runserver 0.0.0.0:9000`.
7. Open `http://127.0.0.1:9000/` for coin search or sign in at `http://127.0.0.1:9000/login/` to access your portfolio.

Search for a coin on the home page, for example `bitcoin`, `BTC`, or `ethereum`. The server first searches `https://api.coingecko.com/api/v3/search`, then fetches INR prices for up to 10 matching CoinGecko IDs. Prices are public market quotes, not CoinSwitch account balances or guaranteed execution prices.

This starter is intended for local development. The trading form can submit real exchange orders after an explicit confirmation; do not expose it publicly without authentication, HTTPS, production security settings, and additional safeguards.

## Use Supabase Postgres

Django uses Supabase as its PostgreSQL database; a separate Supabase client or API key is not needed for ORM access.

1. In the Supabase project dashboard, open **Connect** and copy a PostgreSQL connection string. Use the direct connection when the host can reach it; otherwise choose a Supavisor pooler connection. Keep the database password private.
2. Add the connection string to the project `.env`:
   ```env
   SUPABASE_DB_URL=postgresql://postgres:<database-password>@<host>:5432/postgres
   ```
   Use the exact host, port, and database shown by Supabase; pooler connection strings may use a different username and port. Django requires SSL for this setting. Do not use a Supabase `anon` or `service_role` API key here.
3. Install dependencies and create the Django tables:
   ```sh
   pip install -r requirements.txt
   python manage.py migrate
   ```
   Start the application as usual. When `SUPABASE_DB_URL` is set, it takes precedence over `DATABASE_URL`.

For hosted deployments, configure `SUPABASE_DB_URL` as a secret environment variable on the web service instead of putting it in a committed file. Existing deployments using `DATABASE_URL` continue to work. With `DJANGO_DEBUG=false`, one of these database URL variables must be configured; Django will not silently use a local SQLite database.

## Deploy privately on Render

The following deployment keeps portfolio access behind a Django login and disables public sign-up. The trading endpoints remain loopback-only and therefore will not place orders from Render.

1. Push this project to a GitHub repository and create a Render PostgreSQL database and a Python web service connected to that repository.
2. Set the web service **Build Command** to:
   ```sh
   pip install -r requirements.txt && python manage.py collectstatic --no-input && python manage.py migrate
   ```
3. Set the **Start Command** to:
   ```sh
   gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
   ```
4. Add these environment variables to the Render web service:
   - `DJANGO_SECRET_KEY`: generate a long, random secret in Render.
   - `DJANGO_DEBUG`: `false`
   - `DJANGO_ALLOW_SIGNUPS`: `false`
   - `DATABASE_URL`: the Render PostgreSQL database's **internal** connection URL.
   - `DELTA_API_KEY` and `DELTA_API_SECRET`: only if you need private account data. Add them as secret environment values; never commit them.
5. Deploy, then open the Render Shell and create your private account:
   ```sh
   python manage.py createsuperuser
   ```
6. Open the `.onrender.com` site and sign in at `/login/`.

Render supplies `RENDER_EXTERNAL_HOSTNAME`; Django uses it for allowed-host and CSRF checks. PostgreSQL is used whenever `DATABASE_URL` is present, and WhiteNoise serves collected static files. Keep public registration disabled: all local accounts currently share the same configured Delta credentials. Do not configure Delta credentials or deploy account access publicly until the site's access model and exchange-key permissions are reviewed.
