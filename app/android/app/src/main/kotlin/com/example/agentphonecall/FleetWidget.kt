package com.example.agentphonecall

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.View
import android.widget.RemoteViews
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/** «Fleet» widget: Claude Code, Codex and Grok quotas and pending work, read from the server's GET /fleet. */
class FleetWidget : AppWidgetProvider() {

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) = refresh(context)

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACTION_REFRESH) refresh(context) else super.onReceive(context, intent)
    }

    private fun refresh(context: Context) {
        val manager = AppWidgetManager.getInstance(context)
        val ids = manager.getAppWidgetIds(ComponentName(context, FleetWidget::class.java))
        if (ids.isEmpty()) return
        paint(context, manager, ids, "Updating…", null)
        Thread {
            val data = try { read(context) } catch (e: Exception) { null }
            paint(context, manager, ids, if (data == null) "offline" else null, data)
        }.start()
    }

    private fun read(context: Context): JSONObject? {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val base = prefs.getString("url", null) ?: return null
        val token = prefs.getString("token", null) ?: return null
        val connection = URL("$base/fleet").openConnection() as HttpURLConnection
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.connectTimeout = 8000
        connection.readTimeout = 8000
        return try { JSONObject(connection.inputStream.bufferedReader().readText()) } finally { connection.disconnect() }
    }

    private fun paint(context: Context, manager: AppWidgetManager, ids: IntArray, notice: String?, d: JSONObject?) {
        val v = RemoteViews(context.packageName, R.layout.widget_fleet)
        val tap = PendingIntent.getBroadcast(
            context, 0, Intent(context, FleetWidget::class.java).setAction(ACTION_REFRESH),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        v.setOnClickPendingIntent(R.id.root, tap)
        if (d != null) {
            val quotas = d.optJSONArray("quotas")
            val rows = listOf(
                intArrayOf(R.id.q1_name, R.id.q1_bar, R.id.q1_pct, R.id.q1_resets),
                intArrayOf(R.id.q2_name, R.id.q2_bar, R.id.q2_pct, R.id.q2_resets),
                intArrayOf(R.id.q3_name, R.id.q3_bar, R.id.q3_pct, R.id.q3_resets))
            for ((i, f) in rows.withIndex()) {
                val q = quotas?.optJSONObject(i)
                v.setTextViewText(f[0], (q?.optString("name") ?: "").uppercase(Locale.getDefault()))
                if (q != null && q.optString("state") == "ok") {
                    val r = q.optInt("remaining")
                    v.setProgressBar(f[1], 100, r, false)
                    v.setTextViewText(f[2], "$r%")
                    v.setTextViewText(f[3], renews(q.optString("resets")))
                    // Low quota: the bar and the number turn amber or red.
                    val alert = if (r < 15) Color.parseColor("#F0675A") else if (r < 35) Color.parseColor("#F2B84B") else null
                    if (alert != null) {
                        v.setTextColor(f[2], alert)
                        v.setColorStateList(f[1], "setProgressTintList", ColorStateList.valueOf(alert))
                    }
                } else {
                    v.setProgressBar(f[1], 100, 0, false)
                    v.setTextViewText(f[2], "—")
                    v.setTextColor(f[2], Color.parseColor("#6F8396"))
                    v.setTextViewText(f[3], "no reading")
                }
            }
            val fl = d.optJSONObject("fleet")
            val waiting = fl?.optInt("waiting") ?: 0
            v.setTextViewText(R.id.c_running_n, (fl?.optInt("running") ?: 0).toString())
            v.setTextViewText(R.id.c_decision_n, waiting.toString())
            v.setTextViewText(R.id.c_queued_n, (fl?.optInt("queued") ?: 0).toString())
            if (waiting > 0) {
                v.setInt(R.id.c_decision, "setBackgroundResource", R.drawable.chip_alert)
                v.setTextColor(R.id.c_decision_n, Color.parseColor("#F2B84B"))
            }
            v.setTextViewText(R.id.time, time(d.optString("updated")))
        } else if (notice != null) {
            v.setTextViewText(R.id.time, notice)
        }
        paintMainTasks(context, v)
        manager.updateAppWidget(ids, v)
    }

    /**
     * With a snapshot from the main coordinator (the one that arrives over FCM) the counters and the list come from it,
     * not from this Mac's backlog; past 2 hours the note says so. Without a snapshot nothing changes.
     */
    private fun paintMainTasks(context: Context, v: RemoteViews) {
        val t = MainTasks.read(context)
        if (t == null) {
            v.setViewVisibility(R.id.tasks_note, View.GONE)
            for (id in LINES) v.setViewVisibility(id, View.GONE)
            return
        }
        v.setTextViewText(R.id.c_running_n, t.running.size.toString())
        v.setTextViewText(R.id.c_decision_n, t.waiting.size.toString())
        v.setTextViewText(R.id.c_queued_n, t.queued.size.toString())
        val alert = Color.parseColor("#F2B84B")
        v.setTextColor(R.id.c_decision_n, if (t.waiting.isNotEmpty()) alert else Color.parseColor("#E8EEF3"))
        v.setInt(R.id.c_decision, "setBackgroundResource", if (t.waiting.isNotEmpty()) R.drawable.chip_alert else R.drawable.chip_background)
        val expired = t.expired()
        v.setViewVisibility(R.id.tasks_note, View.VISIBLE)
        v.setTextViewText(R.id.tasks_note, if (expired) "Main · ${t.label()} · may be out of date" else "Main · ${t.label()}")
        v.setTextColor(R.id.tasks_note, if (expired) alert else Color.parseColor("#6F8396"))
        // One line per TextView: each with its own ellipsis, never wrapping or spilling out of the widget.
        val lines = t.fleetLines()
        for ((i, id) in LINES.withIndex()) {
            val l = lines.getOrNull(i)
            v.setViewVisibility(id, if (l == null) View.GONE else View.VISIBLE)
            if (l == null) continue
            val text = SpannableStringBuilder()
            if (l.mark.isNotEmpty()) {
                text.append(l.mark).append(" ")
                val color = if (l.mark == "▶") "#22D3EE" else "#6F8396"
                text.setSpan(ForegroundColorSpan(Color.parseColor(color)), 0, l.mark.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            text.append(l.text)
            v.setTextViewText(id, text)
            v.setTextColor(id, Color.parseColor(if (expired || l.mark.isEmpty()) "#6F8396" else "#E8EEF3"))
        }
    }

    /** «renews in 5 d» / «in 7 h» from the quota's ISO renewal date. */
    private fun renews(iso: String): String = try {
        val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
        val hours = (f.parse(iso.take(19))!!.time - System.currentTimeMillis()) / 3600000
        if (hours >= 48) "renews in ${hours / 24} d" else if (hours >= 1) "renews in $hours h" else "renews soon"
    } catch (e: Exception) { "" }

    private fun time(iso: String): String = try {
        val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
        SimpleDateFormat("HH:mm", Locale.getDefault()).format(f.parse(iso)!!)
    } catch (e: Exception) { "—" }

    companion object {
        const val PREFS = "phonecall_widget"
        const val ACTION_REFRESH = "com.example.agentphonecall.FLEET_REFRESH"
        private val LINES = listOf(R.id.tasks_l1, R.id.tasks_l2, R.id.tasks_l3)
    }
}
