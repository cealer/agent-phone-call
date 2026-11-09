package com.example.agentphonecall

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.view.View
import android.widget.RemoteViews
import android.widget.RemoteViewsService

/** Lista desplazable del widget Decisiones: una fila por decisión de la instantánea del principal, sin tope. */
class DecisionesService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory = Fabrica(applicationContext)

    private class Fabrica(private val context: Context) : RemoteViewsFactory {
        private var items: List<TareasPrincipal.Decision> = emptyList()

        override fun onCreate() {}
        override fun onDestroy() {}

        override fun onDataSetChanged() {
            val principal = TareasPrincipal.leer(context)
            if (principal != null) DecisionesEstado.depurar(context, principal)
            items = principal?.decisiones
                ?: DecisionesWidget.respaldo.map { TareasPrincipal.Decision(null, it, emptyList()) }
        }

        override fun getCount() = items.size
        override fun getViewTypeCount() = 1
        override fun getItemId(position: Int) = (items.getOrNull(position)?.let { it.id ?: it.titulo } ?: "").hashCode().toLong()
        override fun hasStableIds() = true
        override fun getLoadingView(): RemoteViews? = null

        override fun getViewAt(position: Int): RemoteViews {
            val v = RemoteViews(context.packageName, R.layout.item_decision)
            val dec = items.getOrNull(position) ?: return v
            v.setTextViewText(R.id.it_t, dec.titulo)
            // La fila muestra el título en dos líneas; tocarlo abre en la app el detalle, con el texto completo y las acciones.
            v.setOnClickFillInIntent(R.id.it_cab, Intent().putExtra("ver", true).putExtra(DecisionesWidget.EXTRA_DECISION, dec.id ?: dec.texto))
            val id = dec.id
            if (id == null) {
                v.setViewVisibility(R.id.it_e, View.GONE); v.setViewVisibility(R.id.it_fila, View.GONE)
                return v
            }
            val respondida = DecisionesEstado.respondida(context, id)
            val error = DecisionesEstado.error(context, id)
            val llamame = DecisionesEstado.llamame(context, id)
            when {
                respondida != null -> { v.setTextViewText(R.id.it_e, "Respondido: ${respondida.opcion}"); v.setTextColor(R.id.it_e, Color.parseColor("#34D399")) }
                error != null -> { v.setTextViewText(R.id.it_e, "No se envió: $error Toca para reintentar."); v.setTextColor(R.id.it_e, Color.parseColor("#F87171")) }
                llamame -> { v.setTextViewText(R.id.it_e, "Pedido: te llamarán"); v.setTextColor(R.id.it_e, Color.parseColor("#22D3EE")) }
            }
            v.setViewVisibility(R.id.it_e, if (respondida != null || error != null || llamame) View.VISIBLE else View.GONE)
            // Respondida: sin botones, no se vuelve a responder por error. Con error siguen para reintentar.
            v.setViewVisibility(R.id.it_fila, if (respondida != null) View.GONE else View.VISIBLE)
            for ((i, b) in OPCIONES.withIndex()) {
                val o = dec.opciones.getOrNull(i)
                v.setViewVisibility(b, if (o != null) View.VISIBLE else View.GONE)
                if (o == null) continue
                v.setTextViewText(b, o)
                v.setOnClickFillInIntent(b, Intent().putExtra("id", id).putExtra("titulo", dec.texto).putExtra("opcion", o))
            }
            v.setOnClickFillInIntent(R.id.it_ll, Intent().putExtra("id", id).putExtra("titulo", dec.texto).putExtra("llamame", true))
            return v
        }

        private companion object { val OPCIONES = listOf(R.id.it_o1, R.id.it_o2, R.id.it_o3) }
    }
}
