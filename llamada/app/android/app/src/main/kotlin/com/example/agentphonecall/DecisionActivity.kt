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
 * Confirmación mínima del widget Decisiones, sobre la pantalla de inicio: «¿Responder "<opción>" a <título>?».
 * Solo al confirmar se envía la respuesta; si falla se dice aquí y se puede reintentar.
 * Con `ver` abre en la app el detalle de la decisión (la fila del widget corta el texto a dos líneas).
 */
class DecisionActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var titulo: TextView
    private lateinit var cuerpo: TextView
    private lateinit var confirmar: Button
    private lateinit var cancelar: Button
    private var enviando = false

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun boton(texto: String, fondo: String, tinta: String) = Button(this).apply {
        text = texto; isAllCaps = false; setTextColor(Color.parseColor(tinta))
        background = GradientDrawable().apply { cornerRadius = dp(14).toFloat(); setColor(Color.parseColor(fondo)) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (intent.getBooleanExtra("ver", false)) { ver(intent.getStringExtra(DecisionesWidget.EXTRA_DECISION)); return }
        val id = intent.getStringExtra("id") ?: run { finish(); return }
        val tituloDecision = intent.getStringExtra("titulo") ?: ""
        // «Llámame» llega por la misma plantilla de la lista del widget: sin confirmación y sin pantalla.
        if (intent.getBooleanExtra("llamame", false)) { llamame(id, tituloDecision); return }
        val opcion = intent.getStringExtra("opcion") ?: run { finish(); return }
        val caja = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(20), dp(22), dp(18))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(Color.parseColor("#F00A1017"), Color.parseColor("#F00B1822"))).apply {
                cornerRadius = dp(26).toFloat(); setStroke(dp(1), Color.parseColor("#66F2B84B"))
            }
        }
        titulo = TextView(this).apply {
            text = "DECISIÓN"; setTextColor(Color.parseColor("#F2B84B")); textSize = 12f; letterSpacing = 0.2f
            typeface = android.graphics.Typeface.create("sans-serif-condensed", android.graphics.Typeface.BOLD)
        }
        cuerpo = TextView(this).apply {
            text = "¿Responder \"$opcion\" a $tituloDecision?"
            setTextColor(Color.parseColor("#E8EEF3")); textSize = 17f; setPadding(0, dp(10), 0, dp(6))
        }
        confirmar = boton("Confirmar", "#F2B84B", "#1A1305")
        cancelar = boton("Cancelar", "#16222E", "#E8EEF3")
        confirmar.setOnClickListener { enviar(id, tituloDecision, opcion) }
        cancelar.setOnClickListener { finish() }
        val fila = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fila.addView(cancelar, LinearLayout.LayoutParams(0, dp(46), 1f).apply { marginEnd = dp(8) })
        fila.addView(confirmar, LinearLayout.LayoutParams(0, dp(46), 1f))
        caja.addView(titulo); caja.addView(cuerpo)
        caja.addView(fila, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) })
        setContentView(caja)
        window.setGravity(Gravity.BOTTOM)
        window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.setBackgroundDrawableResource(android.R.color.transparent)
    }

    /** Detalle de la decisión en la app (texto completo y acciones); debajo queda la lista Decisiones. */
    private fun ver(clave: String?) {
        startActivity(android.content.Intent(this, MainActivity::class.java)
            .putExtra(DecisionesWidget.EXTRA_ABRIR, true).putExtra(DecisionesWidget.EXTRA_DECISION, clave)
            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }

    private fun llamame(id: String, tituloDecision: String) {
        val app = applicationContext
        android.widget.Toast.makeText(app, "Pidiendo que te llamen…", android.widget.Toast.LENGTH_SHORT).show()
        Thread {
            val fallo = DecisionEnvio.llamame(app, id, tituloDecision)
            Handler(Looper.getMainLooper()).post {
                android.widget.Toast.makeText(app, fallo?.let { "No se envió: $it" } ?: "Pedido: te llamarán", android.widget.Toast.LENGTH_LONG).show()
            }
        }.start()
        finish()
    }

    private fun enviar(id: String, tituloDecision: String, opcion: String) {
        if (enviando) return
        enviando = true
        confirmar.isEnabled = false; cancelar.isEnabled = false
        titulo.text = "ENVIANDO"; cuerpo.text = "Enviando «$opcion»…"
        val app = applicationContext
        Thread {
            val fallo = DecisionEnvio.responder(app, id, tituloDecision, opcion)
            main.post {
                if (isFinishing) return@post
                if (fallo == null) {
                    titulo.text = "RESPONDIDO"; cuerpo.text = "«$opcion» enviado."
                    main.postDelayed({ if (!isFinishing) finish() }, 1200)
                } else {
                    titulo.text = "NO SE ENVIÓ"; cuerpo.text = "$fallo La decisión sigue pendiente."
                    enviando = false
                    confirmar.text = "Reintentar"; confirmar.isEnabled = true
                    cancelar.text = "Cerrar"; cancelar.isEnabled = true
                }
            }
        }.start()
    }
}
