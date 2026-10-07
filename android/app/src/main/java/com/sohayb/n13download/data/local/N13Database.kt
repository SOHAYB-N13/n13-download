package com.sohayb.n13download.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * The N13 download database.
 *
 * `exportSchema` is off: the app ships a single install target and schema
 * history is not consumed by anyone.  Destructive migration is deliberately not
 * enabled — losing a user's download queue silently would be worse than a
 * visible failure.
 */
@Database(
    entities = [DownloadTaskEntity::class],
    version = 1,
    exportSchema = false,
)
abstract class N13Database : RoomDatabase() {

    abstract fun downloadTaskDao(): DownloadTaskDao

    companion object {
        private const val NAME = "n13-download.db"

        @Volatile
        private var instance: N13Database? = null

        fun get(context: Context): N13Database = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                N13Database::class.java,
                NAME,
            ).build().also { instance = it }
        }
    }
}
