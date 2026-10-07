package com.sohayb.n13download.ui.components

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import com.sohayb.n13download.R
import com.sohayb.n13download.domain.model.TaskStatus
import com.sohayb.n13download.ui.theme.LocalN13Colors

/**
 * How a [TaskStatus] is presented.
 *
 * Labels are resolved from string resources so they follow the app language; the
 * colour mapping follows the Windows status badges (active = accent red,
 * paused = amber, complete = green, failed = red, queued/cancelled = grey).
 */
data class StatusVisual(
    val label: String,
    val color: Color,
    val icon: ImageVector,
    /** Active states blink their dot in the Windows UI; kept as a flag here. */
    val pulsing: Boolean = false,
)

@Composable
fun TaskStatus.visual(): StatusVisual {
    val n13 = LocalN13Colors.current
    return when (this) {
        TaskStatus.QUEUED ->
            StatusVisual(stringResource(R.string.status_queued), n13.text3, N13Icons.Clock)

        TaskStatus.ANALYZING ->
            StatusVisual(stringResource(R.string.status_analyzing), n13.accent, N13Icons.Search, pulsing = true)

        TaskStatus.STARTING ->
            StatusVisual(stringResource(R.string.status_starting), n13.accent, N13Icons.Play, pulsing = true)

        TaskStatus.DOWNLOADING ->
            StatusVisual(stringResource(R.string.status_downloading), n13.accent, N13Icons.Download, pulsing = true)

        TaskStatus.PAUSED ->
            StatusVisual(stringResource(R.string.status_paused), n13.warning, N13Icons.Pause)

        TaskStatus.MERGING ->
            StatusVisual(stringResource(R.string.status_merging), n13.accent, N13Icons.Batch, pulsing = true)

        TaskStatus.VERIFYING ->
            StatusVisual(stringResource(R.string.status_verifying), n13.accent, N13Icons.Shield, pulsing = true)

        TaskStatus.COMPLETED ->
            StatusVisual(stringResource(R.string.status_completed), n13.success, N13Icons.Check)

        TaskStatus.FAILED ->
            StatusVisual(stringResource(R.string.status_failed), n13.danger, N13Icons.Alert)

        TaskStatus.CANCELLED ->
            StatusVisual(stringResource(R.string.status_cancelled), n13.text3, N13Icons.XCircle)

        TaskStatus.REMOVED ->
            StatusVisual(stringResource(R.string.status_removed), n13.text3, N13Icons.XCircle)
    }
}

/** File-type presentation, matching the Windows `.dl-ico[data-type]` tints. */
data class FileTypeVisual(
    val icon: ImageVector,
    val color: Color,
)

/**
 * The N13 file-type accent mapping:
 * archive = amber, video = violet, audio = blue, image = green,
 * document = red, app = red, everything else = muted grey.
 */
@Composable
fun fileTypeVisual(filename: String, contentType: String = ""): FileTypeVisual {
    val n13 = LocalN13Colors.current
    val extension = filename.substringAfterLast('.', "").lowercase()
    val mime = contentType.substringBefore(';').trim().lowercase()

    return when {
        extension in ARCHIVE_EXTENSIONS || mime in ARCHIVE_MIME ->
            FileTypeVisual(N13Icons.Archive, n13.warning)

        extension in VIDEO_EXTENSIONS || mime.startsWith("video/") ->
            FileTypeVisual(N13Icons.Video, n13.violet)

        extension in AUDIO_EXTENSIONS || mime.startsWith("audio/") ->
            FileTypeVisual(N13Icons.Audio, n13.info)

        extension in IMAGE_EXTENSIONS || mime.startsWith("image/") ->
            FileTypeVisual(N13Icons.Image, n13.success)

        extension in DOCUMENT_EXTENSIONS || mime.startsWith("text/") || mime == "application/pdf" ->
            FileTypeVisual(N13Icons.Document, n13.accent)

        extension in PROGRAM_EXTENSIONS || mime == "application/vnd.android.package-archive" ->
            FileTypeVisual(N13Icons.App, n13.danger)

        else -> FileTypeVisual(N13Icons.File, n13.text2)
    }
}

/** Icon used by the category chips and the category strip. */
fun categoryIcon(category: String): ImageVector = when (category) {
    "Videos" -> N13Icons.Video
    "Music" -> N13Icons.Audio
    "Images" -> N13Icons.Image
    "Documents" -> N13Icons.Document
    "Archives" -> N13Icons.Archive
    "Programs" -> N13Icons.App
    "Other" -> N13Icons.Disc
    else -> N13Icons.File
}

private val ARCHIVE_EXTENSIONS = setOf("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "tgz", "zst", "cab")
private val VIDEO_EXTENSIONS = setOf("mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts", "mpeg", "mpg", "3gp", "wmv", "m2ts")
private val AUDIO_EXTENSIONS = setOf("mp3", "flac", "wav", "ogg", "m4a", "aac", "opus", "wma", "mid")
private val IMAGE_EXTENSIONS = setOf("jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tiff", "heic", "avif", "jfif")
private val DOCUMENT_EXTENSIONS = setOf("pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx", "ppt", "pptx", "csv", "md", "epub")
private val PROGRAM_EXTENSIONS = setOf("exe", "msi", "apk", "dmg", "deb", "rpm", "jar", "pkg", "appimage", "sh", "bat")

private val ARCHIVE_MIME = setOf(
    "application/zip",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-bzip2",
    "application/x-xz",
)
