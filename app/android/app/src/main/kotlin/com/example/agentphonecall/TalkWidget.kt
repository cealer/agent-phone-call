package com.example.agentphonecall

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

/** «Talk» widget: Call the coordinator agent, and three dictation buttons (Firstmate, Hermes, Note). */
class TalkWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val v = RemoteViews(context.packageName, R.layout.widget_talk)
        listOf(R.id.b_firstmate to "firstmate", R.id.b_hermes to "hermes", R.id.b_note to "note").forEachIndexed { i, (id, to) ->
            val open = Intent(context, TalkActivity::class.java).putExtra("to", to)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            v.setOnClickPendingIntent(id, PendingIntent.getActivity(
                context, 10 + i, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        }
        // «Call»: opens the app and calls the coordinator (the app reads this extra when it opens).
        val call = Intent(context, MainActivity::class.java).putExtra(EXTRA_CALL, "firstmate")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        v.setOnClickPendingIntent(R.id.b_call, PendingIntent.getActivity(
            context, 1, call, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        manager.updateAppWidget(ids, v)
    }

    companion object { const val EXTRA_CALL = "call" }
}
