package com.sohayb.n13download.domain.download

import android.net.Uri
import com.sohayb.n13download.domain.model.DestinationKind
import java.io.OutputStream

/**
 * Where a finished file is published.
 *
 * Android has no single writable "download folder" any more, so the destination
 * is a strategy instead of a path.  The engine only ever needs a **sequential**
 * output stream (the merge step writes parts in order), which keeps every
 * backend — plain file, MediaStore, SAF document tree — implementable behind the
 * same small interface.
 */
interface DownloadDestination {

    val kind: DestinationKind

    /** What the user sees, e.g. `Download/N13` or `Downloads`. */
    val displayPath: String

    /** Value persisted on the task so the destination can be rebuilt later. */
    val reference: String

    /** False when the folder/URI is gone (unmounted SD card, revoked grant). */
    suspend fun isAvailable(): Boolean

    /** Names already present, used for duplicate detection and unique naming. */
    suspend fun listNames(): Set<String>

    suspend fun exists(name: String): Boolean

    /** Deletes an existing entry. Returns false when it could not be removed. */
    suspend fun delete(name: String): Boolean

    /**
     * Opens a sequential writer for [name], creating the entry.
     * Implementations must not silently overwrite an unrelated user file.
     */
    suspend fun openOutput(name: String): OutputStream

    /** Called after a successful write so the entry becomes visible. */
    suspend fun finalize(name: String)

    /** Called after a failed write so no half-written entry is left behind. */
    suspend fun abort(name: String)

    /** URI other apps can use for open/share intents. */
    fun contentUri(name: String): Uri

    /** The on-disk path when one exists, for display and logs. */
    fun filePath(name: String): String?
}
