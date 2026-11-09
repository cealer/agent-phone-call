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
 * Widget «Decisiones»: lo que espera la respuesta del capitán. La lista es una colección desplazable
 * ([DecisionesService]); la cabecera y la marca de instantánea quedan fijas dentro del widget.
 */
class DecisionesWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        for (id in ids) manager.updateAppWidget(id, completo(context, id))
        refrescar(context)
    }

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACCION -> refrescar(context)
            else -> super.onReceive(context, intent)
        }
    }

    companion object {
        const val ACCION = "com.example.agentphonecall.DECISIONES_REFRESCAR"
        /** Extra de MainActivity: abrir la sección Decisiones de la app. */
        const val EXTRA_ABRIR = "abrir_decisiones"
        /** Con [EXTRA_ABRIR]: la decisión que abrir en detalle (su id, o su texto si no tiene). */
        const val EXTRA_DECISION = "decision"

        /** Títulos de /flota cuando aún no hay instantánea del principal. */
        @Volatile var respaldo: List<String> = emptyList()

        private val INMUTABLE = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE

        /** Actualiza la cabecera y pide a la lista que se relea, sin recrear el widget (conserva el desplazamiento). */
        fun refrescar(context: Context) {
            val app = context.applicationContext
            val manager = AppWidgetManager.getInstance(app)
            val ids = manager.getAppWidgetIds(ComponentName(app, DecisionesWidget::class.java))
            if (ids.isEmpty()) return
            pintarCabecera(app, manager, ids)
            Thread {
                if (TareasPrincipal.leer(app) == null) {
                    respaldo = try { leerRespaldo(app) } catch (e: Exception) { respaldo }
                    pintarCabecera(app, manager, ids)
                }
            }.start()
        }

        private fun pintarCabecera(context: Context, manager: AppWidgetManager, ids: IntArray) {
            val v = RemoteViews(context.packageName, R.layout.widget_decisiones)
            // Lo que espera al capitán lo sabe el firstmate principal (VPS): su instantánea manda sobre el backlog de esta Mac.
            val principal = TareasPrincipal.leer(context)
            if (principal != null) DecisionesEstado.depurar(context, principal)
            v.setTextViewText(R.id.dec_n, (principal?.decisiones?.size ?: respaldo.size).toString())
            v.setViewVisibility(R.id.dec_nota, if (principal == null) View.GONE else View.VISIBLE)
            if (principal != null) {
                v.setTextViewText(R.id.dec_nota, if (principal.caducada()) "Principal · ${principal.etiqueta()} · puede no estar al día" else "Principal · ${principal.etiqueta()}")
                v.setTextColor(R.id.dec_nota, Color.parseColor(if (principal.caducada()) "#F2B84B" else "#6F8396"))
            }
            manager.partiallyUpdateAppWidget(ids, v)
            manager.notifyAppWidgetViewDataChanged(ids, R.id.dec_lista)
        }

        private fun leerRespaldo(context: Context): List<String> {
            val prefs = context.getSharedPreferences(FlotaWidget.PREFS, Context.MODE_PRIVATE)
            val base = prefs.getString("url", null) ?: return emptyList()
            val token = prefs.getString("token", null) ?: return emptyList()
            val c = URL("$base/flota").openConnection() as HttpURLConnection
            c.setRequestProperty("Authorization", "Bearer $token"); c.connectTimeout = 8000; c.readTimeout = 8000
            val j = try { JSONObject(c.inputStream.bufferedReader().readText()) } finally { c.disconnect() }
            val a = j.optJSONObject("flota")?.optJSONArray("espera_titulos") ?: return emptyList()
            return (0 until a.length()).map { a.optString(it, "") }.filter { it.isNotEmpty() }
        }

        /** El widget entero: cabecera, lista con su plantilla de toques y los toques fijos. */
        private fun completo(context: Context, id: Int): RemoteViews {
            val v = RemoteViews(context.packageName, R.layout.widget_decisiones)
            val servicio = Intent(context, DecisionesService::class.java)
                .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id)
                .setData(Uri.parse("llamada://decisiones/$id"))
            v.setRemoteAdapter(R.id.dec_lista, servicio)
            v.setEmptyView(R.id.dec_lista, R.id.dec_vacio)
            // El texto y los botones de cada fila rellenan esta plantilla (ver, o id, texto completo y opción o llamame); cae en DecisionActivity.
            val plantilla = Intent(context, DecisionActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            v.setPendingIntentTemplate(R.id.dec_lista, PendingIntent.getActivity(
                context, 21, plantilla, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE))
            // Tocar el título abre la sección Decisiones de la app; tocar la marca de instantánea refresca.
            val abrir = Intent(context, MainActivity::class.java).putExtra(EXTRA_ABRIR, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            v.setOnClickPendingIntent(R.id.dec_cabecera, PendingIntent.getActivity(context, 22, abrir, INMUTABLE))
            v.setOnClickPendingIntent(R.id.dec_nota, PendingIntent.getBroadcast(
                context, 20, Intent(context, DecisionesWidget::class.java).setAction(ACCION), INMUTABLE))
            return v
        }
    }
}
