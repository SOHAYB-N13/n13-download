package com.sohayb.n13download.ui.components

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/**
 * The N13 icon set.
 *
 * Generated from the Windows N13 frontend (`ui/frontend/js/utils.js`, the
 * `Utils.icons` map) so the Android build draws the same glyphs as the desktop
 * application: 24x24, 1.8 stroke, round caps and joins, `currentColor` replaced
 * by the Compose tint.
 *
 * Regenerate with `.n13ref/make_android_icons.py` rather than editing by hand.
 */
object N13Icons {

    /** `dashboard` */
    val Dashboard: ImageVector by lazy {
        n13Icon("Dashboard",
            strokes = listOf(
                "M5.3,3.5 H8.7 A1.8,1.8 0 0 1 10.5,5.3 V8.7 A1.8,1.8 0 0 1 8.7,10.5 H5.3 A1.8,1.8 0 0 1 3.5,8.7 V5.3 A1.8,1.8 0 0 1 5.3,3.5 Z",
                "M15.3,3.5 H18.7 A1.8,1.8 0 0 1 20.5,5.3 V8.7 A1.8,1.8 0 0 1 18.7,10.5 H15.3 A1.8,1.8 0 0 1 13.5,8.7 V5.3 A1.8,1.8 0 0 1 15.3,3.5 Z",
                "M5.3,13.5 H8.7 A1.8,1.8 0 0 1 10.5,15.3 V18.7 A1.8,1.8 0 0 1 8.7,20.5 H5.3 A1.8,1.8 0 0 1 3.5,18.7 V15.3 A1.8,1.8 0 0 1 5.3,13.5 Z",
                "M15.3,13.5 H18.7 A1.8,1.8 0 0 1 20.5,15.3 V18.7 A1.8,1.8 0 0 1 18.7,20.5 H15.3 A1.8,1.8 0 0 1 13.5,18.7 V15.3 A1.8,1.8 0 0 1 15.3,13.5 Z",
            ),
        )
    }

    /** `download` */
    val Download: ImageVector by lazy {
        n13Icon("Download",
            strokes = listOf(
                "M12 3.5 v11",
                "m7.5 10 4.5 4.5 L16.5 10",
                "M4.5 20.5 h15",
            ),
        )
    }

    /** `history` */
    val History: ImageVector by lazy {
        n13Icon("History",
            strokes = listOf(
                "M3.6 12 a8.4 8.4 0 1 0 2.4-5.9 L3.5 8.5",
                "M3.5 3.5 v5 h5",
                "M12 7.5 V12 l3.2 1.9",
            ),
        )
    }

    /** `batch` */
    val Batch: ImageVector by lazy {
        n13Icon("Batch",
            strokes = listOf(
                "m12 3.2 8.5 4.6-8.5 4.6 L3.5 7.8 12 3.2 Z",
                "m4.5 12.2 7.5 4 7.5-4",
                "m4.5 16.2 7.5 4 7.5-4",
            ),
        )
    }

    /** `browser` */
    val Browser: ImageVector by lazy {
        n13Icon("Browser",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "M3.5 12 h17",
                "M12 3.5 c2.3 2.2 3.5 5.2 3.5 8.5 s-1.2 6.3-3.5 8.5 c-2.3-2.2-3.5-5.2-3.5-8.5 s1.2-6.3 3.5-8.5 Z",
            ),
        )
    }

    /** `settings` */
    val Settings: ImageVector by lazy {
        n13Icon("Settings",
            strokes = listOf(
                "M8.8,12.0 a3.2,3.2 0 1 0 6.4,0 a3.2,3.2 0 1 0 -6.4,0 Z",
                "M12 2.8 c.6 0 1 .4 1.1 1 l.3 1.5 c.5.2 1 .5 1.5.8 l1.4-.6 c.6-.2 1.2 0 1.5.5 l1 1.7 c.3.5.2 1.2-.2 1.6 l-1.2 1.1 c.1.5.1.9.1 1.4 s0 1-.1 1.4 l1.2 1.1 c.4.4.5 1 .2 1.6 l-1 1.7 c-.3.5-1 .7-1.5.5 l-1.4-.6 c-.5.3-1 .6-1.5.8 l-.3 1.5 c-.1.6-.6 1-1.1 1 h-2 c-.6 0-1-.4-1.1-1 l-.3-1.5 a7 7 0 0 1-1.5-.8 l-1.4.6 c-.6.2-1.2 0-1.5-.5 l-1-1.7 a1.2 1.2 0 0 1 .2-1.6 l1.2-1.1 a7.3 7.3 0 0 1 0-2.8 L5.2 9.3 a1.2 1.2 0 0 1-.2-1.6 l1-1.7 c.3-.5 1-.7 1.5-.5 l1.4.6 c.5-.3 1-.6 1.5-.8 l.3-1.5 c.1-.6.6-1 1.1-1 h2 Z",
            ),
        )
    }

    /** `logs` */
    val Logs: ImageVector by lazy {
        n13Icon("Logs",
            strokes = listOf(
                "m5 7.5 4.5 4.5 L5 16.5",
                "M11.5 17 h7",
                "M5.5,4.0 H18.5 A2.5,2.5 0 0 1 21.0,6.5 V17.5 A2.5,2.5 0 0 1 18.5,20.0 H5.5 A2.5,2.5 0 0 1 3.0,17.5 V6.5 A2.5,2.5 0 0 1 5.5,4.0 Z",
            ),
        )
    }

    /** `search` */
    val Search: ImageVector by lazy {
        n13Icon("Search",
            strokes = listOf(
                "M4.5,11.0 a6.5,6.5 0 1 0 13.0,0 a6.5,6.5 0 1 0 -13.0,0 Z",
                "m20.5 20.5-4.6-4.6",
            ),
        )
    }

    /** `plus` */
    val Plus: ImageVector by lazy {
        n13Icon("Plus",
            strokes = listOf(
                "M12 5 v14 M5 12 h14",
            ),
        )
    }

    /** `paste` */
    val Paste: ImageVector by lazy {
        n13Icon("Paste",
            strokes = listOf(
                "M9.0,2.5 H15.0 A1.0,1.0 0 0 1 16.0,3.5 V5.0 A1.0,1.0 0 0 1 15.0,6.0 H9.0 A1.0,1.0 0 0 1 8.0,5.0 V3.5 A1.0,1.0 0 0 1 9.0,2.5 Z",
                "M16 4.5 h2.5 A1.5 1.5 0 0 1 20 6 v14 a1.5 1.5 0 0 1-1.5 1.5 h-13 A1.5 1.5 0 0 1 4 20 V6 a1.5 1.5 0 0 1 1.5-1.5 H8",
            ),
        )
    }

    /** `pause` */
    val Pause: ImageVector by lazy {
        n13Icon("Pause",
            strokes = listOf(
                "M7.7,4.5 H8.9 A1.2,1.2 0 0 1 10.1,5.7 V18.3 A1.2,1.2 0 0 1 8.9,19.5 H7.7 A1.2,1.2 0 0 1 6.5,18.3 V5.7 A1.2,1.2 0 0 1 7.7,4.5 Z",
                "M15.1,4.5 H16.3 A1.2,1.2 0 0 1 17.5,5.7 V18.3 A1.2,1.2 0 0 1 16.3,19.5 H15.1 A1.2,1.2 0 0 1 13.9,18.3 V5.7 A1.2,1.2 0 0 1 15.1,4.5 Z",
            ),
        )
    }

    /** `play` */
    val Play: ImageVector by lazy {
        n13Icon("Play",
            strokes = listOf(
                "M7.5 4.8 v14.4 c0 .8.9 1.3 1.6.9 l11.2-7.2 a1 1 0 0 0 0-1.7 L9.1 4 a1 1 0 0 0-1.6.8 Z",
            ),
        )
    }

    /** `x` */
    val X: ImageVector by lazy {
        n13Icon("X",
            strokes = listOf(
                "M18 6 6 18 M6 6 l12 12",
            ),
        )
    }

    /** `check` */
    val Check: ImageVector by lazy {
        n13Icon("Check",
            strokes = listOf(
                "m4.5 12.5 5 5 L19.5 7",
            ),
        )
    }

    /** `alert` */
    val Alert: ImageVector by lazy {
        n13Icon("Alert",
            strokes = listOf(
                "M12 8.5 V13",
                "M12 16.8 h.01",
                "M10.2 3.6 2.4 17.3 a2 2 0 0 0 1.7 3 h15.8 a2 2 0 0 0 1.7-3 L13.8 3.6 a2 2 0 0 0-3.6 0 Z",
            ),
        )
    }

    /** `info` */
    val Info: ImageVector by lazy {
        n13Icon("Info",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "M12 11 v5.5",
                "M12 7.5 h.01",
            ),
        )
    }

    /** `folder` */
    val Folder: ImageVector by lazy {
        n13Icon("Folder",
            strokes = listOf(
                "M3.5 7.5 a2 2 0 0 1 2-2 h4 l2 2.5 h7 a2 2 0 0 1 2 2 v8 a2 2 0 0 1-2 2 h-13 a2 2 0 0 1-2-2 v-10.5 Z",
            ),
        )
    }

    /** `folderOpen` */
    val FolderOpen: ImageVector by lazy {
        n13Icon("FolderOpen",
            strokes = listOf(
                "M3.5 7.5 a2 2 0 0 1 2-2 h4 l2 2.5 h7 a2 2 0 0 1 2 2 v1",
                "M3.5 8.5 h15.4 a2 2 0 0 1 1.9 2.6 l-1.6 5.4 a2 2 0 0 1-1.9 1.4 H5.5 a2 2 0 0 1-2-2 V8.5 Z",
            ),
        )
    }

    /** `trash` */
    val Trash: ImageVector by lazy {
        n13Icon("Trash",
            strokes = listOf(
                "M4 6.5 h16",
                "M9 6.5 v-2 a1 1 0 0 1 1-1 h4 a1 1 0 0 1 1 1 v2",
                "M19 6.5 18.1 19 a2 2 0 0 1-2 1.9 H7.9 a2 2 0 0 1-2-1.9 L5 6.5",
                "M10 11 v5.5 M14 11 v5.5",
            ),
        )
    }

    /** `copy` */
    val Copy: ImageVector by lazy {
        n13Icon("Copy",
            strokes = listOf(
                "M11.0,9.0 H18.5 A2.0,2.0 0 0 1 20.5,11.0 V18.5 A2.0,2.0 0 0 1 18.5,20.5 H11.0 A2.0,2.0 0 0 1 9.0,18.5 V11.0 A2.0,2.0 0 0 1 11.0,9.0 Z",
                "M5.5 15 h-1 a2 2 0 0 1-2-2 V5.5 a2 2 0 0 1 2-2 H12 a2 2 0 0 1 2 2 v1",
            ),
        )
    }

    /** `more` */
    val More: ImageVector by lazy {
        n13Icon("More",
            strokes = listOf(
            ),
            fills = listOf(
                "M3.5,12.0 a1.5,1.5 0 1 0 3.0,0 a1.5,1.5 0 1 0 -3.0,0 Z",
                "M10.5,12.0 a1.5,1.5 0 1 0 3.0,0 a1.5,1.5 0 1 0 -3.0,0 Z",
                "M17.5,12.0 a1.5,1.5 0 1 0 3.0,0 a1.5,1.5 0 1 0 -3.0,0 Z",
            ),
        )
    }

    /** `sun` */
    val Sun: ImageVector by lazy {
        n13Icon("Sun",
            strokes = listOf(
                "M8.0,12.0 a4.0,4.0 0 1 0 8.0,0 a4.0,4.0 0 1 0 -8.0,0 Z",
                "M12 2.5 V4 M12 20 v1.5 M4.6 4.6 l1 1 M18.4 18.4 l1 1 M2.5 12 H4 M20 12 h1.5 M4.6 19.4 l1-1 M18.4 5.6 l1-1",
            ),
        )
    }

    /** `moon` */
    val Moon: ImageVector by lazy {
        n13Icon("Moon",
            strokes = listOf(
                "M20.5 13.2 A8.5 8.5 0 1 1 10.8 3.5 a7 7 0 0 0 9.7 9.7 Z",
            ),
        )
    }

    /** `chevronDown` */
    val ChevronDown: ImageVector by lazy {
        n13Icon("ChevronDown",
            strokes = listOf(
                "m6 9.5 6 6 6-6",
            ),
        )
    }

    /** `chevronRight` */
    val ChevronRight: ImageVector by lazy {
        n13Icon("ChevronRight",
            strokes = listOf(
                "m9.5 6 6 6-6 6",
            ),
        )
    }

    /** `chevronLeft` */
    val ChevronLeft: ImageVector by lazy {
        n13Icon("ChevronLeft",
            strokes = listOf(
                "m14.5 6-6 6 6 6",
            ),
        )
    }

    /** `arrowUp` */
    val ArrowUp: ImageVector by lazy {
        n13Icon("ArrowUp",
            strokes = listOf(
                "M12 19 V5",
                "m5.5 11.5 6.5-6.5 6.5 6.5",
            ),
        )
    }

    /** `arrowDown` */
    val ArrowDown: ImageVector by lazy {
        n13Icon("ArrowDown",
            strokes = listOf(
                "M12 5 v14",
                "m5.5 12.5 6.5 6.5 6.5-6.5",
            ),
        )
    }

    /** `gauge` */
    val Gauge: ImageVector by lazy {
        n13Icon("Gauge",
            strokes = listOf(
                "m12 13.5 3.5-3.5",
                "M3.8 19.3 a9 9 0 1 1 16.4 0",
            ),
        )
    }

    /** `activity` */
    val Activity: ImageVector by lazy {
        n13Icon("Activity",
            strokes = listOf(
                "M21.5 12 h-3.8 l-2.7 8 L9.3 4 l-2.7 8 H2.5",
            ),
        )
    }

    /** `disk` */
    val Disk: ImageVector by lazy {
        n13Icon("Disk",
            strokes = listOf(
                "M4.5 6.5 a2 2 0 0 1 2-2 h11 a2 2 0 0 1 2 2 v11 a2 2 0 0 1-2 2 h-11 a2 2 0 0 1-2-2 v-11 Z",
                "M9.2,11.5 a2.8,2.8 0 1 0 5.6,0 a2.8,2.8 0 1 0 -5.6,0 Z",
                "M8 17.5 h8",
            ),
        )
    }

    /** `calendar` */
    val Calendar: ImageVector by lazy {
        n13Icon("Calendar",
            strokes = listOf(
                "M5.5,5.0 H18.5 A2.0,2.0 0 0 1 20.5,7.0 V19.0 A2.0,2.0 0 0 1 18.5,21.0 H5.5 A2.0,2.0 0 0 1 3.5,19.0 V7.0 A2.0,2.0 0 0 1 5.5,5.0 Z",
                "M8 3 v4 M16 3 v4 M3.5 10.5 h17",
            ),
        )
    }

    /** `file` */
    val File: ImageVector by lazy {
        n13Icon("File",
            strokes = listOf(
                "M13.5 3 H7 a2 2 0 0 0-2 2 v14 a2 2 0 0 0 2 2 h10 a2 2 0 0 0 2-2 V8.5 L13.5 3 Z",
                "M13.5 3 v5.5 H19",
            ),
        )
    }

    /** `archive` */
    val Archive: ImageVector by lazy {
        n13Icon("Archive",
            strokes = listOf(
                "M4.7,4.0 H19.3 A1.2,1.2 0 0 1 20.5,5.2 V7.3 A1.2,1.2 0 0 1 19.3,8.5 H4.7 A1.2,1.2 0 0 1 3.5,7.3 V5.2 A1.2,1.2 0 0 1 4.7,4.0 Z",
                "M5 8.5 V19 a1.5 1.5 0 0 0 1.5 1.5 h11 A1.5 1.5 0 0 0 19 19 V8.5",
                "M10 12.5 h4",
            ),
        )
    }

    /** `video` */
    val Video: ImageVector by lazy {
        n13Icon("Video",
            strokes = listOf(
                "M4.7,5.5 H13.8 A2.2,2.2 0 0 1 16.0,7.7 V16.3 A2.2,2.2 0 0 1 13.8,18.5 H4.7 A2.2,2.2 0 0 1 2.5,16.3 V7.7 A2.2,2.2 0 0 1 4.7,5.5 Z",
                "m16 10.5 5.5-3.2 v9.4 L16 13.5",
            ),
        )
    }

    /** `audio` */
    val Audio: ImageVector by lazy {
        n13Icon("Audio",
            strokes = listOf(
                "M9.5 18.5 V6.8 L20 4.5 v12",
                "M4.1,18.5 a2.7,2.7 0 1 0 5.4,0 a2.7,2.7 0 1 0 -5.4,0 Z",
                "M14.600000000000001,16.5 a2.7,2.7 0 1 0 5.4,0 a2.7,2.7 0 1 0 -5.4,0 Z",
            ),
        )
    }

    /** `image` */
    val Image: ImageVector by lazy {
        n13Icon("Image",
            strokes = listOf(
                "M5.7,4.5 H18.3 A2.2,2.2 0 0 1 20.5,6.7 V17.3 A2.2,2.2 0 0 1 18.3,19.5 H5.7 A2.2,2.2 0 0 1 3.5,17.3 V6.7 A2.2,2.2 0 0 1 5.7,4.5 Z",
                "M7.4,10.0 a1.6,1.6 0 1 0 3.2,0 a1.6,1.6 0 1 0 -3.2,0 Z",
                "m4.8 17.8 4.7-4.8 3 3 3.4-3.5 4.3 4.4",
            ),
        )
    }

    /** `document` */
    val Document: ImageVector by lazy {
        n13Icon("Document",
            strokes = listOf(
                "M13.5 3 H7 a2 2 0 0 0-2 2 v14 a2 2 0 0 0 2 2 h10 a2 2 0 0 0 2-2 V8.5 L13.5 3 Z",
                "M13.5 3 v5.5 H19",
                "M9 13 h6 M9 16.5 h6",
            ),
        )
    }

    /** `app` */
    val App: ImageVector by lazy {
        n13Icon("App",
            strokes = listOf(
                "m5 8 4 4-4 4",
                "M12 16.5 h7",
                "M5.5,4.0 H18.5 A2.5,2.5 0 0 1 21.0,6.5 V17.5 A2.5,2.5 0 0 1 18.5,20.0 H5.5 A2.5,2.5 0 0 1 3.0,17.5 V6.5 A2.5,2.5 0 0 1 5.5,4.0 Z",
            ),
        )
    }

    /** `disc` */
    val Disc: ImageVector by lazy {
        n13Icon("Disc",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "M9.5,12.0 a2.5,2.5 0 1 0 5.0,0 a2.5,2.5 0 1 0 -5.0,0 Z",
            ),
        )
    }

    /** `link` */
    val Link: ImageVector by lazy {
        n13Icon("Link",
            strokes = listOf(
                "M10 13.5 a5 5 0 0 0 7.5.5 l2.8-2.8 a5 5 0 0 0-7-7 l-1.6 1.5",
                "M14 10.5 a5 5 0 0 0-7.5-.5 l-2.8 2.8 a5 5 0 0 0 7 7 l1.6-1.5",
            ),
        )
    }

    /** `retry` */
    val Retry: ImageVector by lazy {
        n13Icon("Retry",
            strokes = listOf(
                "M20.5 12 a8.5 8.5 0 1 1-2.5-6 L20.5 8",
                "M20.5 3.5 V8 H16",
            ),
        )
    }

    /** `refresh` */
    val Refresh: ImageVector by lazy {
        n13Icon("Refresh",
            strokes = listOf(
                "M21.5 12 a9.5 9.5 0 1 1-2.8-6.7 L20.5 7",
                "M20.5 3.5 V7 H17",
            ),
        )
    }

    /** `bolt` */
    val Bolt: ImageVector by lazy {
        n13Icon("Bolt",
            strokes = listOf(
                "M13 2.5 4.5 13.5 H11 l-1 8 8.5-11 H12 l1-8 Z",
            ),
        )
    }

    /** `menu` */
    val Menu: ImageVector by lazy {
        n13Icon("Menu",
            strokes = listOf(
                "M4 6.5 h16 M4 12 h16 M4 17.5 h16",
            ),
        )
    }

    /** `panelLeft` */
    val PanelLeft: ImageVector by lazy {
        n13Icon("PanelLeft",
            strokes = listOf(
                "M5.7,4.5 H18.3 A2.2,2.2 0 0 1 20.5,6.7 V17.3 A2.2,2.2 0 0 1 18.3,19.5 H5.7 A2.2,2.2 0 0 1 3.5,17.3 V6.7 A2.2,2.2 0 0 1 5.7,4.5 Z",
                "M9.5 4.5 v15",
            ),
        )
    }

    /** `min` */
    val Min: ImageVector by lazy {
        n13Icon("Min",
            strokes = listOf(
                "M5.5 12 h13",
            ),
        )
    }

    /** `max` */
    val Max: ImageVector by lazy {
        n13Icon("Max",
            strokes = listOf(
                "M7.6,6.0 H16.4 A1.6,1.6 0 0 1 18.0,7.6 V16.4 A1.6,1.6 0 0 1 16.4,18.0 H7.6 A1.6,1.6 0 0 1 6.0,16.4 V7.6 A1.6,1.6 0 0 1 7.6,6.0 Z",
            ),
        )
    }

    /** `restore` */
    val Restore: ImageVector by lazy {
        n13Icon("Restore",
            strokes = listOf(
                "M10.1,8.5 H17.4 A1.6,1.6 0 0 1 19.0,10.1 V17.4 A1.6,1.6 0 0 1 17.4,19.0 H10.1 A1.6,1.6 0 0 1 8.5,17.4 V10.1 A1.6,1.6 0 0 1 10.1,8.5 Z",
                "M5.5 15.5 v-9 a1 1 0 0 1 1-1 h9",
            ),
        )
    }

    /** `sortAsc` */
    val SortAsc: ImageVector by lazy {
        n13Icon("SortAsc",
            strokes = listOf(
                "M7 4.5 v15",
                "m3.5 8 3.5-3.5 L10.5 8",
                "M17 19.5 v-15",
                "m13.5 16 3.5 3.5 3.5-3.5",
            ),
        )
    }

    /** `external` */
    val External: ImageVector by lazy {
        n13Icon("External",
            strokes = listOf(
                "M14 4.5 h5.5 V10",
                "M19.5 4.5 10.5 13.5",
                "M19.5 14 v5 a1.5 1.5 0 0 1-1.5 1.5 H6 A1.5 1.5 0 0 1 4.5 19 V6 A1.5 1.5 0 0 1 6 4.5 h5",
            ),
        )
    }

    /** `clock` */
    val Clock: ImageVector by lazy {
        n13Icon("Clock",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "M12 7.5 V12 l3 2",
            ),
        )
    }

    /** `xCircle` */
    val XCircle: ImageVector by lazy {
        n13Icon("XCircle",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "m9 9 6 6 M15 9 l-6 6",
            ),
        )
    }

    /** `power` */
    val Power: ImageVector by lazy {
        n13Icon("Power",
            strokes = listOf(
                "M12 3 v8",
                "M6.3 6.2 a8 8 0 1 0 11.4 0",
            ),
        )
    }

    /** `server` */
    val Server: ImageVector by lazy {
        n13Icon("Server",
            strokes = listOf(
                "M5.3,4.0 H18.7 A1.8,1.8 0 0 1 20.5,5.8 V8.7 A1.8,1.8 0 0 1 18.7,10.5 H5.3 A1.8,1.8 0 0 1 3.5,8.7 V5.8 A1.8,1.8 0 0 1 5.3,4.0 Z",
                "M5.3,13.5 H18.7 A1.8,1.8 0 0 1 20.5,15.3 V18.2 A1.8,1.8 0 0 1 18.7,20.0 H5.3 A1.8,1.8 0 0 1 3.5,18.2 V15.3 A1.8,1.8 0 0 1 5.3,13.5 Z",
                "M7 7.2 h.01 M7 16.7 h.01",
            ),
        )
    }

    /** `cpu` */
    val Cpu: ImageVector by lazy {
        n13Icon("Cpu",
            strokes = listOf(
                "M8.0,6.0 H16.0 A2.0,2.0 0 0 1 18.0,8.0 V16.0 A2.0,2.0 0 0 1 16.0,18.0 H8.0 A2.0,2.0 0 0 1 6.0,16.0 V8.0 A2.0,2.0 0 0 1 8.0,6.0 Z",
                "M10.0,10.0 H14.0 V14.0 H10.0 Z",
                "M12 2.5 V6 M12 18 v3.5 M2.5 12 H6 M18 12 h3.5 M5 5 l1.5 1.5 M17.5 17.5 19 19 M19 5 l-1.5 1.5 M6.5 17.5 5 19",
            ),
        )
    }

    /** `wifi` */
    val Wifi: ImageVector by lazy {
        n13Icon("Wifi",
            strokes = listOf(
                "M2.5 9 a14 14 0 0 1 19 0",
                "M5.5 12.5 a9.5 9.5 0 0 1 13 0",
                "M8.5 16 a5 5 0 0 1 7 0",
                "M12 19.5 h.01",
            ),
        )
    }

    /** `clearAll` */
    val ClearAll: ImageVector by lazy {
        n13Icon("ClearAll",
            strokes = listOf(
                "m4 7 5-5 5 5",
                "M9 2 v12",
                "M4 17 h16 M4 21 h10",
            ),
        )
    }

    /** `stop` */
    val Stop: ImageVector by lazy {
        n13Icon("Stop",
            strokes = listOf(
                "M8.3,6.5 H15.7 A1.8,1.8 0 0 1 17.5,8.3 V15.7 A1.8,1.8 0 0 1 15.7,17.5 H8.3 A1.8,1.8 0 0 1 6.5,15.7 V8.3 A1.8,1.8 0 0 1 8.3,6.5 Z",
            ),
        )
    }

    /** `filter` */
    val Filter: ImageVector by lazy {
        n13Icon("Filter",
            strokes = listOf(
                "M4 5.5 h16 l-6.2 7.2 v5.6 l-3.6 2.2 v-7.8 L4 5.5 Z",
            ),
        )
    }

    /** `keyboard` */
    val Keyboard: ImageVector by lazy {
        n13Icon("Keyboard",
            strokes = listOf(
                "M4.5,6.0 H19.5 A2.0,2.0 0 0 1 21.5,8.0 V16.0 A2.0,2.0 0 0 1 19.5,18.0 H4.5 A2.0,2.0 0 0 1 2.5,16.0 V8.0 A2.0,2.0 0 0 1 4.5,6.0 Z",
                "M6 10 h.01 M10 10 h.01 M14 10 h.01 M18 10 h.01 M6 14 h.01 M18 14 h.01 M9 14 h6",
            ),
        )
    }

    /** `edit` */
    val Edit: ImageVector by lazy {
        n13Icon("Edit",
            strokes = listOf(
                "M16.5 3.8 a2.2 2.2 0 0 1 3.1 3.1 L7.4 19.1 3.5 20.5 l1.4-3.9 L16.5 3.8 Z",
                "m14.8 5.5 3.7 3.7",
            ),
        )
    }

    /** `sliders` */
    val Sliders: ImageVector by lazy {
        n13Icon("Sliders",
            strokes = listOf(
                "M4 7 h9 M17 7 h3 M4 17 h3 M11 17 h9",
                "M12.8,7.0 a2.2,2.2 0 1 0 4.4,0 a2.2,2.2 0 1 0 -4.4,0 Z",
                "M6.8,17.0 a2.2,2.2 0 1 0 4.4,0 a2.2,2.2 0 1 0 -4.4,0 Z",
            ),
        )
    }

    /** `flag` */
    val Flag: ImageVector by lazy {
        n13Icon("Flag",
            strokes = listOf(
                "M5.5 21 V4.2",
                "M5.5 5.2 h11.4 l-2 4 2 4 H5.5",
            ),
        )
    }

    /** `bell` */
    val Bell: ImageVector by lazy {
        n13Icon("Bell",
            strokes = listOf(
                "M18 9 a6 6 0 1 0-12 0 c0 5-2 6.5-2 6.5 h16 S18 14 18 9 Z",
                "M13.7 19 a2 2 0 0 1-3.4 0",
            ),
        )
    }

    /** `code` */
    val Code: ImageVector by lazy {
        n13Icon("Code",
            strokes = listOf(
                "m8.5 8-4.5 4 4.5 4",
                "m15.5 8 4.5 4-4.5 4",
                "m13.5 4-3 16",
            ),
        )
    }

    /** `clock2` */
    val Clock2: ImageVector by lazy {
        n13Icon("Clock2",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "M12 7.5 V12 l3 2",
            ),
        )
    }

    /** `globe` */
    val Globe: ImageVector by lazy {
        n13Icon("Globe",
            strokes = listOf(
                "M3.5,12.0 a8.5,8.5 0 1 0 17.0,0 a8.5,8.5 0 1 0 -17.0,0 Z",
                "M3.5 12 h17",
                "M12 3.5 c2.3 2.2 3.5 5.2 3.5 8.5 s-1.2 6.3-3.5 8.5 c-2.3-2.2-3.5-5.2-3.5-8.5 s1.2-6.3 3.5-8.5 Z",
            ),
        )
    }

    /** `list` */
    val List: ImageVector by lazy {
        n13Icon("List",
            strokes = listOf(
                "M8.5 6.5 h12 M8.5 12 h12 M8.5 17.5 h12",
                "M4 6.5 h.01 M4 12 h.01 M4 17.5 h.01",
            ),
        )
    }

    /** `tag` */
    val Tag: ImageVector by lazy {
        n13Icon("Tag",
            strokes = listOf(
                "M11.5 3.5 H20 v8.5 l-8.5 8.5 L3 12 l8.5-8.5 Z",
                "M16 8 h.01",
            ),
        )
    }

    /** `shield` */
    val Shield: ImageVector by lazy {
        n13Icon("Shield",
            strokes = listOf(
                "M12 3.2 4.5 6 v6 c0 4.5 3.2 7.5 7.5 8.8 4.3-1.3 7.5-4.3 7.5-8.8 V6 L12 3.2 Z",
                "m9 12 2 2 4-4",
            ),
        )
    }
}

/** Builds one 24x24 glyph. Strokes are the default; [fills] are solid sub-paths. */
private fun n13Icon(
    name: String,
    strokes: List<String>,
    fills: List<String> = emptyList(),
): ImageVector {
    val builder = ImageVector.Builder(
        name = name,
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    )
    strokes.forEach { data ->
        builder.addPath(
            pathData = addPathNodes(data),
            fill = null,
            stroke = SolidColor(Color.Black),
            strokeLineWidth = 1.8f,
            strokeLineCap = StrokeCap.Round,
            strokeLineJoin = StrokeJoin.Round,
        )
    }
    fills.forEach { data ->
        builder.addPath(
            pathData = addPathNodes(data),
            fill = SolidColor(Color.Black),
            stroke = null,
        )
    }
    return builder.build()
}
