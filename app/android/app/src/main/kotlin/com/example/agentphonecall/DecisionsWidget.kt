package com.example.agentphonecall

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.view.View
import android.widget.RemoteViews
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * «Decisions» widget: what waits for the user's answer. The list is a scrollable collection ([DecisionsService]);
 * the header and the snapshot mark stay fixed inside the widget.
 */
class DecisionsWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        for (id in ids) manager.updateAppWidget(id, whole(context, id))
        refresh(context)
    }

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION -> refresh(context)
            else -> super.onReceive(context, intent)
        }
    }

    companion object {
        const val ACTION = "com.example.agentphonecall.DECISIONS_REFRESH"
        /** MainActivity extra: open the app's Decisions section. */
        const val EXTRA_OPEN = "open_decisions"
        /** With [EXTRA_OPEN]: the decision to open in detail (its id, or its text without an id). */
        const val EXTRA_DECISION = "decision"

        /** Titles from /fleet while there is no snapshot from the main agent yet. */
        @Volatile var fallback: List<String> = emptyList()

        private val IMMUTABLE = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE

        /** Updates the header and asks the list to reload, without recreating the widget (keeps the scroll position). */
        fun refresh(context: Context) {
            val app = context.applicationContext
            val manager = AppWidgetManager.getInstance(app)
            val ids = manager.getAppWidgetIds(ComponentName(app, DecisionsWidget::class.java))
            if (ids.isEmpty()) return
            paintHeader(app, manager, ids)
            Thread {
                if (MainTasks.read(app) == null) {
                    fallback = try { readFallback(app) } catch (e: Exception) { fallback }
                    paintHeader(app, manager, ids)
                }
            }.start()
        }

        private fun paintHeader(context: Context, manager: AppWidgetManager, ids: IntArray) {
            val v = RemoteViews(context.packageName, R.layout.widget_decisions)
            // The main coordinator (VPS) knows what waits for the user: its snapshot wins over this Mac's backlog.
            val main = MainTasks.read(context)
            if (main != null) DecisionStore.prune(context, main)
            v.setTextViewText(R.id.dec_n, (main?.decisions?.size ?: fallback.size).toString())
            v.setViewVisibility(R.id.dec_note, if (main == null) View.GONE else View.VISIBLE)
            if (main != null) {
                v.setTextViewText(R.id.dec_note, if (main.expired()) "Main · ${main.label()} · may be out of date" else "Main · ${main.label()}")
                v.setTextColor(R.id.dec_note, Color.parseColor(if (main.expired()) "#F2B84B" else "#6F8396"))
            }
            manager.partiallyUpdateAppWidget(ids, v)
            manager.notifyAppWidgetViewDataChanged(ids, R.id.dec_list)
        }

        private fun readFallback(context: Context): List<String> {
            val prefs = context.getSharedPreferences(FleetWidget.PREFS, Context.MODE_PRIVATE)
            val base = prefs.getString("url", null) ?: return emptyList()
            val token = prefs.getString("token", null) ?: return emptyList()
            val c = URL("$base/fleet").openConnection() as HttpURLConnection
            c.setRequestProperty("Authorization", "Bearer $token"); c.connectTimeout = 8000; c.readTimeout = 8000
            val j = try { JSONObject(c.inputStream.bufferedReader().readText()) } finally { c.disconnect() }
            val a = j.optJSONObject("fleet")?.optJSONArray("waiting_titles") ?: return emptyList()
            return (0 until a.length()).map { a.optString(it, "") }.filter { it.isNotEmpty() }
        }

        /** The whole widget: header, the list with its tap template, and the fixed taps. */
        private fun whole(context: Context, id: Int): RemoteViews {
            val v = RemoteViews(context.packageName, R.layout.widget_decisions)
            val service = Intent(context, DecisionsService::class.java)
                .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id)
                .setData(Uri.parse("phonecall://decisions/$id"))
            v.setRemoteAdapter(R.id.dec_list, service)
            v.setEmptyView(R.id.dec_list, R.id.dec_empty)
            // Each row's text and buttons fill in this template (view, or id, full text and option or callMe); it lands in DecisionActivity.
            val template = Intent(context, DecisionActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            v.setPendingIntentTemplate(R.id.dec_list, PendingIntent.getActivity(
                context, 21, template, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE))
            // Tapping the title opens the app's Decisions section; tapping the snapshot mark refreshes.
            val open = Intent(context, MainActivity::class.java).putExtra(EXTRA_OPEN, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            v.setOnClickPendingIntent(R.id.dec_header, PendingIntent.getActivity(context, 22, open, IMMUTABLE))
            v.setOnClickPendingIntent(R.id.dec_note, PendingIntent.getBroadcast(
                context, 20, Intent(context, DecisionsWidget::class.java).setAction(ACTION), IMMUTABLE))
            return v
        }
    }
}
