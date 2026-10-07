package com.sohayb.n13download.domain.model

/**
 * Where a finished file is written.
 *
 * Android cannot hand out arbitrary filesystem paths, so the destination is a
 * strategy rather than a plain string.  The working directory for `.part` files
 * is always app-private, and the finished file is published to the chosen
 * destination.
 */
enum class DestinationKind(val storageValue: String) {
    /** App-specific external storage. Needs no permission on any supported API. */
    APP("app"),

    /** The public Downloads collection via MediaStore (API 29+). */
    MEDIA_STORE("media"),

    /** A folder the user granted through the Storage Access Framework. */
    TREE("tree"),
    ;

    companion object {
        fun fromStorage(value: String?): DestinationKind =
            entries.firstOrNull { it.storageValue == value } ?: APP
    }
}

/** What to do when a file with the target name already exists. */
enum class DuplicatePolicy(val storageValue: String) {
    /** Ask the user (default). */
    ASK("ask"),
    ALLOW("allow"),
    RENAME("rename"),
    REPLACE("replace"),
    ;

    companion object {
        fun fromStorage(value: String?): DuplicatePolicy =
            entries.firstOrNull { it.storageValue == value } ?: ASK
    }
}

/** Connection strategy, mirroring N13's `connection_mode`. */
enum class ConnectionMode(val storageValue: String) {
    /** Size-aware initial selection with safe adaptive scaling. */
    SMART("smart"),

    /** Fixed `numThreads` connections. */
    MANUAL("manual"),
    ;

    companion object {
        fun fromStorage(value: String?): ConnectionMode =
            entries.firstOrNull { it.storageValue == value } ?: SMART
    }
}
