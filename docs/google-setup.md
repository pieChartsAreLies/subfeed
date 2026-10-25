# Google sign-in setup (one time, about 10 minutes)

Subfeed reads your YouTube subscriptions (read-only). You make a Google Cloud project once. It is free; the YouTube Data API's default quota (10,000 units a day) is far more than one household uses.

## Google Cloud Console (console.cloud.google.com)

1. Project picker (top bar) > **New Project**. Name: `Subfeed`. Create, then select it.
2. **APIs & Services > Library**. Search **YouTube Data API v3** > **Enable**.
3. **APIs & Services > OAuth consent screen** (may appear as "Google Auth Platform").
   - User type: **External**. App name: `Subfeed`. Support and developer email: your Gmail.
   - **Scopes > Add or remove scopes**: add `.../auth/youtube.readonly` (YouTube Data API v3, "View your YouTube account"). Save.
   - **Publish app** (Audience / Publishing status): click **Publish app** and confirm, so status reads **In production**. Do not leave it in Testing: in Testing, Google expires the refresh token every 7 days and sign-in breaks weekly.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**.
   - Application type: **TVs and Limited Input devices**. Name: `Subfeed TV`. Create.
   - Copy the **Client ID** and **Client secret**.
5. Store the client ID and client secret in your password manager.

The service reads them from `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `/etc/subfeed/subfeed.env` (restart with `systemctl restart subfeed` after adding). See [self-hosting.md](self-hosting.md).

## On the TV

1. Subfeed > **Settings > Sign in**. The TV shows a code.
2. On your phone or laptop go to **google.com/device**, enter the code, pick your Google account.
3. Google shows **"Google hasn't verified this app"**. This is expected: the OAuth client is yours and has not gone through Google's verification, which is only required for apps used by many people. Click **Advanced > Go to Subfeed (unsafe)**, then **Continue** to allow read-only YouTube access. You only see this once.

The first sync starts right away. Channels in your real subscriptions keep their category and watch history. Seed channels you do not subscribe to are hidden (saved videos stay). Settings shows what was added, removed and kept. After that it re-syncs daily.
