package com.example.agentphonecall

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

/** Widget «Hablar»: Llamar al firstmate y tres botones de dictado (Firstmate, Hermes, Nota). */
class HablarWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val v = RemoteViews(context.packageName, R.layout.widget_hablar)
        listOf(R.id.b_firstmate to "firstmate", R.id.b_hermes to "hermes", R.id.b_nota to "nota").forEachIndexed { i, (id, quien) ->
            val abrir = Intent(context, HablarActivity::class.java).putExtra("quien", quien)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            v.setOnClickPendingIntent(id, PendingIntent.getActivity(
                context, 10 + i, abrir, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        }
        // «Llamar»: abre la app y llama al firstmate (la app lee este dato al abrirse).
        val llamar = Intent(context, MainActivity::class.java).putExtra(EXTRA_LLAMAR, "firstmate")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        v.setOnClickPendingIntent(R.id.b_llamar, PendingIntent.getActivity(
            context, 1, llamar, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        manager.updateAppWidget(ids, v)
    }

    companion object { const val EXTRA_LLAMAR = "llamar" }
}
