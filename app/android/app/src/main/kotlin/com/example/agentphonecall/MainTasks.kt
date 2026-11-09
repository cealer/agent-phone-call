package com.example.agentphonecall

import android.content.Context
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * The latest tasks snapshot of the main coordinator agent (on the VPS) that the app kept when the FCM `tasks` message
 * arrived. Dart stores it with home_widget under the `tasks` key of HomeWidgetPreferences.
 */
class MainTasks(
    val generated: Long,
    val running: List<String>,
    val decisions: List<Decision>,
    val queued: List<String>,
) {
    /** Titles of what waits for the user (the Fleet widget uses them too). */
    val waiting: List<String> get() = decisions.map { it.title }

    /** One line of the Fleet widget's Main section: [mark] «▶» (running), «·» (queued) or empty. */
    class Line(val mark: String, val text: String)

    /**
     * What the Fleet widget shows of the main agent: up to 2 running (or «Nothing running») and the next queued one.
     * Not the decisions: they have their own widget and Fleet only counts them.
     */
    fun fleetLines(): List<Line> =
        running.take(2).map { Line("▶", it) }.ifEmpty { listOf(Line("", "Nothing running")) } +
            queued.take(1).map { Line("·", it) }

    fun expired(now: Long = System.currentTimeMillis()) = now - generated > EXPIRES_MS

    /** «11:30», or «11:30 · 3 h ago» once it is no longer current. */
    fun label(now: Long = System.currentTimeMillis()): String {
        val time = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(generated))
        if (!expired(now)) return time
        val hours = (now - generated) / 3600000
        return if (hours >= 48) "$time · ${hours / 24} d ago" else "$time · $hours h ago"
    }

    /**
     * One `waiting` item: with an [id] it can be answered; without one it is only shown. Same as `Decision` in
     * lib/tasks.dart. [detail] is the optional full text (the sender may shorten [title]); [text] is what is shown in
     * full and sent.
     */
    class Decision(val id: String?, val title: String, val options: List<String>, val detail: String? = null) {
        val text: String get() = detail ?: title
    }

    companion object {
        private const val EXPIRES_MS = 2 * 3600000L

        fun read(context: Context): MainTasks? = try {
            val text = context.getSharedPreferences("HomeWidgetPreferences", Context.MODE_PRIVATE)
                .getString("tasks", null)
            if (text == null) null else parse(text)
        } catch (e: Exception) { null }

        fun parse(text: String): MainTasks {
            val j = JSONObject(text)
            val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
            return MainTasks(
                generated = f.parse(j.getString("generated").take(19))!!.time,
                running = list(j, "running"), decisions = decisions(j), queued = list(j, "queued"))
        }

        private fun list(j: JSONObject, key: String): List<String> {
            val a = j.optJSONArray(key) ?: return emptyList()
            return (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() }
        }

        private fun decisions(j: JSONObject): List<Decision> {
            val a = j.optJSONArray("waiting") ?: return emptyList()
            return (0 until a.length()).mapNotNull { i ->
                when (val e = a.opt(i)) {
                    is String -> e.trim().takeIf { it.isNotEmpty() }?.let { Decision(null, it, emptyList()) }
                    is JSONObject -> {
                        val title = e.optString("title").trim()
                        val id = e.optString("id").trim()
                        val detail = (e.opt("detail") as? String)?.trim()?.takeIf { it.isNotEmpty() }
                        if (title.isEmpty()) null
                        else if (id.isEmpty()) Decision(null, title, emptyList(), detail)
                        else {
                            val o = e.optJSONArray("options")
                            val options = if (o == null) emptyList() else (0 until o.length()).mapNotNull { (o.opt(it) as? String)?.trim()?.takeIf { t -> t.isNotEmpty() } }
                            Decision(id, title, options.take(3).ifEmpty { listOf("Yes", "No") }, detail)
                        }
                    }
                    else -> null
                }
            }
        }
    }
}
