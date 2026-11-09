package com.example.agentphonecall

import android.app.Activity
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The Decisions widget's minimal confirmation, over the home screen: «Answer "<option>" to <title>?».
 * The answer is sent only once confirmed; if it fails it says so here and can be retried.
 * With `view` it opens the decision's detail in the app (the widget row cuts the text to two lines).
 */
class DecisionActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var heading: TextView
    private lateinit var body: TextView
    private lateinit var confirm: Button
    private lateinit var cancel: Button
    private var sending = false

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun button(label: String, fill: String, ink: String) = Button(this).apply {
        text = label; isAllCaps = false; setTextColor(Color.parseColor(ink))
        background = GradientDrawable().apply { cornerRadius = dp(14).toFloat(); setColor(Color.parseColor(fill)) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (intent.getBooleanExtra("view", false)) { view(intent.getStringExtra(DecisionsWidget.EXTRA_DECISION)); return }
        val id = intent.getStringExtra("id") ?: run { finish(); return }
        val decisionTitle = intent.getStringExtra("title") ?: ""
        // «Call me» comes through the same template of the widget list: no confirmation and no screen.
        if (intent.getBooleanExtra("callMe", false)) { callMe(id, decisionTitle); return }
        val option = intent.getStringExtra("option") ?: run { finish(); return }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(20), dp(22), dp(18))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(Color.parseColor("#F00A1017"), Color.parseColor("#F00B1822"))).apply {
                cornerRadius = dp(26).toFloat(); setStroke(dp(1), Color.parseColor("#66F2B84B"))
            }
        }
        heading = TextView(this).apply {
            text = "DECISION"; setTextColor(Color.parseColor("#F2B84B")); textSize = 12f; letterSpacing = 0.2f
            typeface = android.graphics.Typeface.create("sans-serif-condensed", android.graphics.Typeface.BOLD)
        }
        body = TextView(this).apply {
            text = "Answer \"$option\" to $decisionTitle?"
            setTextColor(Color.parseColor("#E8EEF3")); textSize = 17f; setPadding(0, dp(10), 0, dp(6))
        }
        confirm = button("Confirm", "#F2B84B", "#1A1305")
        cancel = button("Cancel", "#16222E", "#E8EEF3")
        confirm.setOnClickListener { send(id, decisionTitle, option) }
        cancel.setOnClickListener { finish() }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(cancel, LinearLayout.LayoutParams(0, dp(46), 1f).apply { marginEnd = dp(8) })
        row.addView(confirm, LinearLayout.LayoutParams(0, dp(46), 1f))
        box.addView(heading); box.addView(body)
        box.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) })
        setContentView(box)
        window.setGravity(Gravity.BOTTOM)
        window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.setBackgroundDrawableResource(android.R.color.transparent)
    }

    /** The decision's detail in the app (full text and actions); the Decisions list stays underneath. */
    private fun view(key: String?) {
        startActivity(android.content.Intent(this, MainActivity::class.java)
            .putExtra(DecisionsWidget.EXTRA_OPEN, true).putExtra(DecisionsWidget.EXTRA_DECISION, key)
            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }

    private fun callMe(id: String, decisionTitle: String) {
        val app = applicationContext
        android.widget.Toast.makeText(app, "Asking for a call…", android.widget.Toast.LENGTH_SHORT).show()
        Thread {
            val failure = DecisionSender.callMe(app, id, decisionTitle)
            Handler(Looper.getMainLooper()).post {
                android.widget.Toast.makeText(app, failure?.let { "Not sent: $it" } ?: "Requested: you will get a call", android.widget.Toast.LENGTH_LONG).show()
            }
        }.start()
        finish()
    }

    private fun send(id: String, decisionTitle: String, option: String) {
        if (sending) return
        sending = true
        confirm.isEnabled = false; cancel.isEnabled = false
        heading.text = "SENDING"; body.text = "Sending «$option»…"
        val app = applicationContext
        Thread {
            val failure = DecisionSender.answer(app, id, decisionTitle, option)
            main.post {
                if (isFinishing) return@post
                if (failure == null) {
                    heading.text = "ANSWERED"; body.text = "«$option» sent."
                    main.postDelayed({ if (!isFinishing) finish() }, 1200)
                } else {
                    heading.text = "NOT SENT"; body.text = "$failure The decision is still pending."
                    sending = false
                    confirm.text = "Retry"; confirm.isEnabled = true
                    cancel.text = "Close"; cancel.isEnabled = true
                }
            }
        }.start()
    }
}
