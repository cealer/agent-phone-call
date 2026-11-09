package com.example.agentphonecall

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.view.View
import android.widget.RemoteViews
import android.widget.RemoteViewsService

/** The Decisions widget's scrollable list: one row per decision in the main agent's snapshot, no cap. */
class DecisionsService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory = Factory(applicationContext)

    private class Factory(private val context: Context) : RemoteViewsFactory {
        private var items: List<MainTasks.Decision> = emptyList()

        override fun onCreate() {}
        override fun onDestroy() {}

        override fun onDataSetChanged() {
            val main = MainTasks.read(context)
            if (main != null) DecisionStore.prune(context, main)
            items = main?.decisions
                ?: DecisionsWidget.fallback.map { MainTasks.Decision(null, it, emptyList()) }
        }

        override fun getCount() = items.size
        override fun getViewTypeCount() = 1
        override fun getItemId(position: Int) = (items.getOrNull(position)?.let { it.id ?: it.title } ?: "").hashCode().toLong()
        override fun hasStableIds() = true
        override fun getLoadingView(): RemoteViews? = null

        override fun getViewAt(position: Int): RemoteViews {
            val v = RemoteViews(context.packageName, R.layout.item_decision)
            val dec = items.getOrNull(position) ?: return v
            v.setTextViewText(R.id.it_title, dec.title)
            // The row shows the title on two lines; tapping it opens the detail in the app, with the full text and the actions.
            v.setOnClickFillInIntent(R.id.it_head, Intent().putExtra("view", true).putExtra(DecisionsWidget.EXTRA_DECISION, dec.id ?: dec.text))
            val id = dec.id
            if (id == null) {
                v.setViewVisibility(R.id.it_state, View.GONE); v.setViewVisibility(R.id.it_row, View.GONE)
                return v
            }
            val answered = DecisionStore.answered(context, id)
            val error = DecisionStore.error(context, id)
            val callMe = DecisionStore.callMe(context, id)
            when {
                answered != null -> { v.setTextViewText(R.id.it_state, "Answered: ${answered.option}"); v.setTextColor(R.id.it_state, Color.parseColor("#34D399")) }
                error != null -> { v.setTextViewText(R.id.it_state, "Not sent: $error Tap to retry."); v.setTextColor(R.id.it_state, Color.parseColor("#F87171")) }
                callMe -> { v.setTextViewText(R.id.it_state, "Requested: you will get a call"); v.setTextColor(R.id.it_state, Color.parseColor("#22D3EE")) }
            }
            v.setViewVisibility(R.id.it_state, if (answered != null || error != null || callMe) View.VISIBLE else View.GONE)
            // Answered: no buttons, so it is not answered again by mistake. After an error they stay, to retry.
            v.setViewVisibility(R.id.it_row, if (answered != null) View.GONE else View.VISIBLE)
            for ((i, b) in OPTIONS.withIndex()) {
                val o = dec.options.getOrNull(i)
                v.setViewVisibility(b, if (o != null) View.VISIBLE else View.GONE)
                if (o == null) continue
                v.setTextViewText(b, o)
                v.setOnClickFillInIntent(b, Intent().putExtra("id", id).putExtra("title", dec.text).putExtra("option", o))
            }
            v.setOnClickFillInIntent(R.id.it_callme, Intent().putExtra("id", id).putExtra("title", dec.text).putExtra("callMe", true))
            return v
        }

        private companion object { val OPTIONS = listOf(R.id.it_o1, R.id.it_o2, R.id.it_o3) }
    }
}
