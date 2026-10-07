package com.sohayb.n13download.core

import android.app.Activity
import android.content.Context
import android.content.res.Configuration
import com.sohayb.n13download.domain.model.AppLanguage

/**
 * Applies the user's language to the whole app.
 *
 * Android resolves strings from the [Context]'s [Configuration], so the only
 * reliable way to switch language is to hand every Activity a context whose
 * configuration carries the chosen locale.  That is what [wrap] does, and
 * [MainActivity] calls it from `attachBaseContext`.
 *
 * Two deliberate choices:
 *
 *  - **No global `Locale.setDefault`.**  Setting the JVM default would also drag
 *    number, date and byte-size formatting into the new locale, which would turn
 *    `1.5 MB/s` into `۱.۵ MB/s` and break the rule that technical values stay
 *    LTR and ASCII.  Only the resource lookup is localized.
 *
 *  - **The current language is held in memory.**  `attachBaseContext` runs before
 *    anything else and cannot wait on DataStore, so the persisted value is read
 *    once at process start ([seed]) and kept here.
 */
object AppLocale {

    @Volatile
    private var current: AppLanguage = AppLanguage.DEFAULT

    /** The language the process is currently running in. */
    val language: AppLanguage get() = current

    /**
     * Records the persisted language for this process.
     *
     * Called once from `Application.onCreate` before any Activity exists.
     */
    fun seed(language: AppLanguage) {
        current = language
    }

    /**
     * Returns [base] with the app language applied.
     *
     * `setLayoutDirection` is what makes the configuration right-to-left for
     * Persian, so Compose's own layout direction follows without anything being
     * forced globally.
     */
    fun wrap(base: Context): Context {
        val locale = current.locale
        val configuration = Configuration(base.resources.configuration).apply {
            setLocale(locale)
            setLayoutDirection(locale)
        }
        return base.createConfigurationContext(configuration)
    }

    /**
     * Switches language and rebuilds the UI.
     *
     * Persisting the choice is the caller's job; this only changes what the app
     * is rendering, and recreating is what re-runs `attachBaseContext` so the
     * new strings and direction take effect everywhere at once.
     */
    fun switchTo(activity: Activity, language: AppLanguage) {
        if (language == current) return
        current = language
        activity.recreate()
    }
}
