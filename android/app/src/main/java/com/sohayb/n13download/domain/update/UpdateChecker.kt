package com.sohayb.n13download.domain.update

/**
 * Decides which published release is the Android one, and whether it is newer.
 *
 * Pure and dependency-free so it can be unit-tested without a network, and so the
 * policy is in one readable place rather than spread through the HTTP layer.
 *
 * ## Identifying the Android release
 *
 * The repository hosts **two** products and publishes both from the same
 * Releases feed: the Windows desktop app under `v1.x` tags, and the Android
 * client under `android-v1.x.y`. The tag prefix is the structured, stable
 * discriminator — matching on a title or an asset name would be guesswork, and
 * matching on "any release" would offer a Windows installer to an Android user.
 * The Android asset check is applied as a second, independent signal.
 *
 * ## Comparing versions
 *
 * `versionCode` is authoritative: it is the monotonic integer Android itself uses
 * to decide whether one build supersedes another, and unlike `versionName` it
 * cannot be ambiguous (is `1.1.10` newer than `1.1.9`?).  GitHub releases have no
 * `versionCode` field, so the published value is carried in the release notes
 * (`… versionCode 3`) and parsed from there.  If a future release omits it, the
 * check falls back to comparing `versionName` semantically rather than going
 * silent.
 */
object UpdateChecker {

    /** Marks a release as the Android client. See the class docs. */
    const val ANDROID_TAG_PREFIX = "android-v"

    /** `… versionCode 3` in the release notes. Tolerates markdown between the two. */
    private val VERSION_CODE_IN_BODY = Regex("""\bversionCode\D{0,12}(\d+)""")

    /** True when this release is a published Android build. */
    fun isAndroidRelease(release: RemoteRelease): Boolean =
        !release.isDraft &&
            !release.isPrerelease &&
            release.tag.startsWith(ANDROID_TAG_PREFIX)

    /** `android-v1.1.1` -> `1.1.1`, or null when the tag is not a version. */
    fun versionNameOf(release: RemoteRelease): String? {
        val candidate = release.tag.removePrefix(ANDROID_TAG_PREFIX)
        return candidate.takeIf { parseVersionName(it) != null }
    }

    /** The `versionCode` published in the release notes, or null when absent. */
    fun versionCodeOf(release: RemoteRelease): Int? =
        VERSION_CODE_IN_BODY.find(release.body)?.groupValues?.get(1)?.toIntOrNull()

    /**
     * The newest published Android release, or null when there is none.
     *
     * Ordered by `versionName`, which the tag always carries and which the
     * project increments monotonically.
     */
    fun latestAndroidRelease(releases: List<RemoteRelease>): RemoteRelease? =
        releases
            .filter(::isAndroidRelease)
            .mapNotNull { release ->
                versionNameOf(release)?.let { parseVersionName(it) }?.let { release to it }
            }
            .maxWithOrNull(Comparator { a, b -> compareVersions(a.second, b.second) })
            ?.first

    /**
     * Whether [latest] should be offered to a user running the installed build.
     *
     * `versionCode` is authoritative; `versionName` is only used when the
     * published `versionCode` is missing.
     */
    fun isUpdateAvailable(
        installedVersionCode: Int,
        installedVersionName: String,
        latest: RemoteRelease,
    ): Boolean {
        versionCodeOf(latest)?.let { return it > installedVersionCode }

        val remote = versionNameOf(latest)?.let(::parseVersionName) ?: return false
        val installed = parseVersionName(installedVersionName) ?: return false
        return compareVersions(remote, installed) > 0
    }

    /** `1.1.1` -> `[1, 1, 1]`, or null when it is not a dotted numeric version. */
    fun parseVersionName(value: String): List<Int>? {
        val trimmed = value.trim()
        if (trimmed.isEmpty()) return null
        val parts = trimmed.split('.')
        val numbers = parts.map { it.toIntOrNull() ?: return null }
        return numbers.takeIf { it.isNotEmpty() }
    }

    /** Compares dotted versions, padding the shorter one with zeros. */
    fun compareVersions(a: List<Int>, b: List<Int>): Int {
        val size = maxOf(a.size, b.size)
        for (index in 0 until size) {
            val left = a.getOrElse(index) { 0 }
            val right = b.getOrElse(index) { 0 }
            if (left != right) return left.compareTo(right)
        }
        return 0
    }
}
