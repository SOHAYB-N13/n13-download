package com.sohayb.n13download.data.update

import com.sohayb.n13download.data.engine.OkHttpClients
import com.sohayb.n13download.domain.download.SettingsProvider
import com.sohayb.n13download.domain.update.RemoteRelease
import com.sohayb.n13download.domain.update.UpdateChecker
import com.sohayb.n13download.domain.update.UpdateRepository
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONArray

/**
 * Reads N13's published releases from the GitHub Releases API.
 *
 * Uses the structured API rather than the website, so the tag, the notes and the
 * release page come from documented fields instead of being scraped out of HTML.
 *
 * Two deliberate choices:
 *
 *  - **The check is always made over a verified connection.**  The app's probe
 *    transport honours the user's "verify SSL" setting, and disabling it would
 *    let a tampered response point the user at an attacker's download page. The
 *    update check therefore asks for the verifying client regardless of that
 *    setting. It only ever opens a browser page — it never installs anything —
 *    but a wrong page is still the wrong outcome.
 *
 *  - **Failures return null.**  Offline, DNS failure, rate limiting or a
 *    malformed payload are all "no answer", never an exception the UI has to
 *    handle. Cancellation is rethrown so it is not swallowed.
 */
class GitHubUpdateRepository(
    private val settingsProvider: SettingsProvider,
) : UpdateRepository {

    override suspend fun latestAndroidRelease(): RemoteRelease? = withContext(Dispatchers.IO) {
        try {
            fetch()
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            // Offline or the API is unavailable: skip the check silently.
            null
        }
    }

    private suspend fun fetch(): RemoteRelease? {
        val settings = settingsProvider.current()

        val request = Request.Builder()
            .url(RELEASES_URL)
            .header("Accept", ACCEPT_HEADER)
            // GitHub rejects requests without a User-Agent.
            .header("User-Agent", settings.userAgent)
            .get()
            .build()

        // Force the verifying transport; see the class docs.
        val client = OkHttpClients.probe(settings.copy(verifySsl = true))

        return client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return null
            UpdateChecker.latestAndroidRelease(parseReleases(response.body.string()))
        }
    }

    private fun parseReleases(payload: String): List<RemoteRelease> {
        val array = JSONArray(payload)
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val htmlUrl = item.optString("html_url")

                // Only ever hand the user a github.com release page.
                if (!htmlUrl.startsWith(TRUSTED_URL_PREFIX)) continue

                add(
                    RemoteRelease(
                        tag = item.optString("tag_name"),
                        title = item.optString("name"),
                        body = item.optString("body"),
                        htmlUrl = htmlUrl,
                        isDraft = item.optBoolean("draft"),
                        isPrerelease = item.optBoolean("prerelease"),
                        assetNames = item.optJSONArray("assets")
                            ?.let { assets ->
                                (0 until assets.length()).mapNotNull { position ->
                                    assets.optJSONObject(position)?.optString("name")
                                }
                            }
                            .orEmpty(),
                    ),
                )
            }
        }
    }

    private companion object {
        const val RELEASES_URL =
            "https://api.github.com/repos/SOHAYB-N13/n13-download/releases?per_page=30"

        const val ACCEPT_HEADER = "application/vnd.github+json"

        const val TRUSTED_URL_PREFIX = "https://github.com/"
    }
}
