package com.sohayb.n13download.domain.update

/**
 * Reads the published releases.
 *
 * Returns null — never throws — when the network or the API is unavailable, so an
 * update check can never break the app or surface an alarming error.
 */
interface UpdateRepository {

    /** The newest published Android release, or null when it cannot be determined. */
    suspend fun latestAndroidRelease(): RemoteRelease?
}
