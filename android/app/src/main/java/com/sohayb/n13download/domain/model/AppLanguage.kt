package com.sohayb.n13download.domain.model

import java.util.Locale

/**
 * The app language the user picked.
 *
 * Deliberately just the two languages N13 ships: English and Persian (Farsi),
 * matching the Windows product's `language` setting.  [tag] is the BCP-47 tag
 * handed to [java.util.Locale], and [storageValue] is what is persisted, so
 * renaming an entry can never invalidate a stored preference.
 */
enum class AppLanguage(
    val storageValue: String,
    val tag: String,
    /** The language's own name, shown in the picker. */
    val nativeName: String,
) {
    ENGLISH("en", "en", "English"),
    PERSIAN("fa", "fa", "فارسی"),
    ;

    val locale: Locale get() = Locale(tag)

    /** True when this language reads right to left. */
    val isRtl: Boolean get() = tag == "fa"

    companion object {
        val DEFAULT: AppLanguage = ENGLISH

        fun fromStorage(value: String?): AppLanguage =
            entries.firstOrNull { it.storageValue == value } ?: DEFAULT
    }
}
