package com.sohayb.n13download.ui.util

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.sohayb.n13download.R

/**
 * The display label for a category.
 *
 * Categories are **stored** in the database under their English keys
 * ("Videos", "Archives", …) — they are identifiers, not prose, and a download
 * categorised in English must still resolve after the user switches language.
 * This maps the stored key to a translated label and leaves anything unknown
 * untouched, so an old or hand-edited value is never lost.
 */
@Composable
fun categoryLabel(category: String): String = when (category) {
    "General" -> stringResource(R.string.category_general)
    "Archives" -> stringResource(R.string.category_archives)
    "Videos" -> stringResource(R.string.category_videos)
    "Music" -> stringResource(R.string.category_music)
    "Documents" -> stringResource(R.string.category_documents)
    "Programs" -> stringResource(R.string.category_programs)
    "Images" -> stringResource(R.string.category_images)
    "Other" -> stringResource(R.string.category_other)
    else -> category
}
