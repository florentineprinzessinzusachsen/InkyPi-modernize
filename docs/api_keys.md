
# Storing API Keys

Certain plugins require API credentials to function. There are two ways to store them, both ending up in the same `.env` file at the project root:

- **Web UI (recommended):** `/settings/api-keys` — the six fixed providers below get a dedicated card; anything else (e.g. `IMMICH_KEY`) goes in the "Custom secrets" section. Fixed and custom keys are both saved via one form; validation only accepts names matching `^[A-Za-z_][A-Za-z0-9_]*$`.
- **Manually on the device:** SSH in, edit `.env` at the project root (`vi .env` / `nano .env`), one `KEY=value` line per key, save.

## OpenAI Key

Required for the AI Image and AI Text plugins (OpenAI provider option).

- Create an account on the [OpenAI developer platform](https://platform.openai.com/docs/overview)
- Create a secret key from the API Keys tab in Settings
    - Set up Auto recharge (Billing tab) so the key doesn't silently stop working
    - Optionally set a Budget Limit in the Limits tab
- Store as `OPEN_AI_SECRET`

## Google AI Key

Required for the AI Image and AI Text plugins (Google provider option — Imagen / Gemini).

- Create a key in [Google AI Studio](https://aistudio.google.com/apikey)
- Store as `GOOGLE_AI_SECRET`

## OpenWeatherMap Key

Required for the Weather plugin.

- Create an account on [OpenWeatherMap](https://home.openweathermap.org/users/sign_in) and verify your email
- The plugin uses the [One Call API 3.0](https://openweathermap.org/price), which needs its own subscription — free for up to 1,000 calls/day
    - Subscribe at [One Call API 3.0 Subscription](https://home.openweathermap.org/subscriptions/billing_info/onecall_30/base?key=base&service=onecall_30)
    - In [Your Subscriptions](https://home.openweathermap.org/subscriptions), cap "Calls per day (no more than)" at 1,000 to stay on the free tier
- Store as `OPEN_WEATHER_MAP_SECRET`

## NASA APOD Key

Required for the APOD plugin.

- Request a key at [NASA APIs](https://api.nasa.gov/) (name + email) — free for up to 1,000 requests/hour
- Store as `NASA_SECRET`

## Unsplash Key

Required for the Unsplash plugin.

- Register at https://unsplash.com/developers, then create an app at https://unsplash.com/oauth/applications
- The key is listed as `Access Key`
- Store as `UNSPLASH_ACCESS_KEY`

## GitHub Key

Required for the GitHub plugin.

- On your [GitHub profile](https://github.com/settings/profile) → Developer Settings, create a Personal access token (classic) with the `read:user` scope
- Store as `GITHUB_SECRET`

## Immich Key (custom secret)

Required for the Image Album plugin's Immich provider. Unlike the keys above, Immich isn't one of the six fixed provider cards — add it under "Custom secrets" on `/settings/api-keys`, or set it directly in `.env`.

- In your Immich instance, under Account Settings → API Keys, create a key with the `asset.read`, `asset.download`, and `album.read` permissions
- Store as `IMMICH_KEY`