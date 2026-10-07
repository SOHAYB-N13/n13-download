package com.sohayb.n13download

import android.content.res.Configuration
import android.view.View
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.LayoutDirection
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sohayb.n13download.core.AppLocale
import com.sohayb.n13download.domain.model.AppLanguage
import com.sohayb.n13download.ui.components.N13Ltr
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Focused checks for the Persian localization.
 *
 * Deliberately small: it proves the resources are packaged and actually
 * translated, that the locale machinery produces a right-to-left context for
 * Persian, and that the LTR helper pins technical values back. It does not try to
 * re-verify the app's behaviour in both languages.
 */
@RunWith(AndroidJUnit4::class)
class PersianLocalizationDeviceTest {

    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @After
    fun restoreDefaultLanguage() {
        AppLocale.seed(AppLanguage.DEFAULT)
    }

    /**
     * Every string key must have a Persian value that differs from the English
     * one — otherwise a screen would silently stay English.
     */
    @Test
    fun persianResources_translateEveryStringKey() {
        AppLocale.seed(AppLanguage.PERSIAN)
        val persian = AppLocale.wrap(context)

        val untranslated = mutableListOf<String>()
        for (field in R.string::class.java.declaredFields) {
            val id = field.getInt(null)
            val english = context.getString(id)
            val translated = persian.getString(id)
            if (english == translated && field.name !in INTENTIONALLY_IDENTICAL) {
                untranslated += field.name
            }
        }

        assertTrue(
            "These keys have no Persian translation: $untranslated",
            untranslated.isEmpty(),
        )
    }

    /** Persian must resolve to a right-to-left configuration. */
    @Test
    fun persianLocale_producesARightToLeftConfiguration() {
        AppLocale.seed(AppLanguage.PERSIAN)
        val wrapped = AppLocale.wrap(context)

        assertEquals("fa", wrapped.resources.configuration.locales[0].language)
        assertEquals(
            View.LAYOUT_DIRECTION_RTL,
            wrapped.resources.configuration.layoutDirection,
        )
    }

    /** English must stay left-to-right, exactly as before this feature. */
    @Test
    fun englishLocale_staysLeftToRight() {
        AppLocale.seed(AppLanguage.ENGLISH)
        val wrapped = AppLocale.wrap(context)

        assertEquals("en", wrapped.resources.configuration.locales[0].language)
        assertEquals(
            View.LAYOUT_DIRECTION_LTR,
            wrapped.resources.configuration.layoutDirection,
        )
    }

    /** A few representative strings, one per screen area. */
    @Test
    fun representativeScreens_areTranslated() {
        AppLocale.seed(AppLanguage.PERSIAN)
        val persian = AppLocale.wrap(context)

        // Downloads, History, Settings, Add Download, notifications, errors.
        assertNotEquals(
            context.getString(R.string.downloads_title),
            persian.getString(R.string.downloads_title),
        )
        assertNotEquals(
            context.getString(R.string.history_title),
            persian.getString(R.string.history_title),
        )
        assertNotEquals(
            context.getString(R.string.settings_title),
            persian.getString(R.string.settings_title),
        )
        assertNotEquals(
            context.getString(R.string.add_title),
            persian.getString(R.string.add_title),
        )
        assertNotEquals(
            context.getString(R.string.notif_complete_title),
            persian.getString(R.string.notif_complete_title),
        )
        assertNotEquals(
            context.getString(R.string.err_not_enough_space),
            persian.getString(R.string.err_not_enough_space),
        )
    }

    /** The language choice has to survive being written and read back. */
    @Test
    fun appLanguage_storageRoundTrips() {
        AppLanguage.entries.forEach { language ->
            assertEquals(language, AppLanguage.fromStorage(language.storageValue))
        }
        // Unknown or missing values fall back rather than crashing.
        assertEquals(AppLanguage.DEFAULT, AppLanguage.fromStorage(null))
        assertEquals(AppLanguage.DEFAULT, AppLanguage.fromStorage("klingon"))
    }

    /**
     * The LTR helper must pin a subtree back to left-to-right even when the
     * surrounding UI is right-to-left — this is what keeps URLs, paths and
     * `1.5 MB/s` readable in Persian.
     */
    @Test
    fun ltrHelper_overridesASurroundingRightToLeftContext() {
        var inside: LayoutDirection? = null
        var outside: LayoutDirection? = null

        compose.setContent {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                outside = LocalLayoutDirection.current
                N13Ltr {
                    inside = LocalLayoutDirection.current
                }
            }
        }
        compose.waitForIdle()

        assertEquals(LayoutDirection.Rtl, outside)
        assertEquals(LayoutDirection.Ltr, inside)
    }

    private companion object {
        /**
         * Keys whose value is the same in both languages on purpose: the example
         * URL is shown verbatim in the link field, and a URL is not translated.
         */
        val INTENTIONALLY_IDENTICAL = setOf("add_link_placeholder")
    }
}
