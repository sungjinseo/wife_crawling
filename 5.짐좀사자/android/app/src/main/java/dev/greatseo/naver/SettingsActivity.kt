package dev.greatseo.naver

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.work.WorkInfo
import androidx.work.WorkManager
import org.json.JSONObject
import kotlin.concurrent.thread

/** 설정 화면: 지금 수집 · 진행 로그 · 서버 상태 · 업로드·예약·브리핑 로그인 설정 */
class SettingsActivity : AppCompatActivity() {
    private lateinit var p: Prefs
    private lateinit var stateTv: TextView
    private lateinit var serverTv: TextView
    private lateinit var logTv: TextView
    private val ui = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() { refreshLog(); ui.postDelayed(this, 3000) }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    @SuppressLint("SetTextI18n")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        p = Prefs(this)
        title = "설정 · 매물 수집"
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(14), dp(18), dp(24)) }
        fun label(t: String) = TextView(this).apply { text = t; textSize = 13f; setPadding(0, dp(12), 0, dp(2)); alpha = .7f }
        fun header(t: String) = TextView(this).apply { text = t; textSize = 17f; setTypeface(typeface, Typeface.BOLD); setPadding(0, dp(18), 0, dp(4)) }
        fun field(v: String, hint: String, type: Int = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI) =
            EditText(this).apply { setText(v); this.hint = hint; inputType = type; isSingleLine = true }
        fun button(t: String, onClick: () -> Unit) = Button(this).apply { text = t; isAllCaps = false; setOnClickListener { onClick() } }

        // 상태
        root.addView(header("상태"))
        stateTv = TextView(this).apply { textSize = 15f }
        root.addView(stateTv)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(button("지금 수집") { startNow() }, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(button("중지") { Schedule.cancelAll(this); Schedule.applyDaily(this); toast("진행 중인 수집을 멈췄습니다") }, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(row)

        // 서버
        root.addView(header("서버"))
        serverTv = TextView(this).apply { text = "확인 전"; textSize = 14f }
        root.addView(serverTv)
        val row2 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row2.addView(button("서버 상태 확인") { checkServer() }, LinearLayout.LayoutParams(0, -2, 1f))
        row2.addView(button("브리핑 보기") { finish() }, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(row2)

        // 설정
        root.addView(header("설정"))
        root.addView(label("업로드 주소"))
        val urlEt = field(p.uploadUrl, "https://home.greatseo.dev/upload-api/naver"); root.addView(urlEt)
        root.addView(label("업로드 토큰 (서버 .env 의 UPLOAD_TOKEN)"))
        val tokEt = field(p.token, "토큰", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD); root.addView(tokEt)
        root.addView(label("브리핑 주소"))
        val brEt = field(p.briefingUrl, "https://home.greatseo.dev/"); root.addView(brEt)
        root.addView(label("브리핑 로그인 아이디 (사이트 기본 인증)"))
        val idEt = field(p.authUser, "아이디", InputType.TYPE_CLASS_TEXT); root.addView(idEt)
        root.addView(label("브리핑 로그인 비밀번호"))
        val pwEt = field(p.authPass, "비밀번호", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD); root.addView(pwEt)
        root.addView(label("매일 실행 시각 (24시간, 예: 10:00)"))
        val timeEt = field("%02d:%02d".format(p.hour, p.minute), "10:00", InputType.TYPE_CLASS_DATETIME or InputType.TYPE_DATETIME_VARIATION_TIME); root.addView(timeEt)
        val schedSw = SwitchCompat(this).apply { text = "매일 자동 수집"; isChecked = p.scheduleOn; setPadding(0, dp(10), 0, 0) }
        root.addView(schedSw)
        val wifiSw = SwitchCompat(this).apply { text = "와이파이에서만 자동 수집 (권장: 데이터망은 자주 막힘)"; isChecked = p.wifiOnly }
        root.addView(wifiSw)
        root.addView(button("저장하고 예약 적용") {
            p.uploadUrl = urlEt.text.toString(); p.token = tokEt.text.toString(); p.briefingUrl = brEt.text.toString()
            p.authUser = idEt.text.toString(); p.authPass = pwEt.text.toString()
            val m = Regex("^(\\d{1,2}):(\\d{2})$").find(timeEt.text.toString().trim())
            if (m == null || m.groupValues[1].toInt() > 23 || m.groupValues[2].toInt() > 59) { toast("시각은 10:00 형식으로 입력하세요"); return@button }
            p.hour = m.groupValues[1].toInt(); p.minute = m.groupValues[2].toInt()
            p.scheduleOn = schedSw.isChecked; p.wifiOnly = wifiSw.isChecked
            Schedule.applyDaily(this)
            toast(if (p.scheduleOn) "저장했습니다. 매일 %02d:%02d 전후로 수집합니다".format(p.hour, p.minute) else "저장했습니다. 자동 수집 꺼짐")
            refreshState()
        })
        root.addView(button("배터리 최적화에서 제외 (자동 수집이 멈추지 않게)") { askBattery() })

        // 로그
        root.addView(header("오늘 로그"))
        logTv = TextView(this).apply { typeface = Typeface.MONOSPACE; textSize = 11f; setTextIsSelectable(true) }
        root.addView(logTv)

        setContentView(ScrollView(this).apply { addView(root) })

        WorkManager.getInstance(this).getWorkInfosByTagLiveData("naver").observe(this) { refreshState(it) }
        if (intent.getBooleanExtra("startNow", false) && savedInstanceState == null) startNow()   // 메뉴의 '지금 매물 수집'에서 들어온 경우
    }

    override fun onResume() { super.onResume(); ui.post(tick); refreshState() }
    override fun onPause() { super.onPause(); ui.removeCallbacks(tick) }

    @SuppressLint("SetTextI18n")
    private fun refreshState(infos: List<WorkInfo>? = null) {
        val running = infos?.any { it.state == WorkInfo.State.RUNNING } == true
        val waiting = infos?.any { it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount > 0 } == true
        val sched = if (p.scheduleOn) "매일 %02d:%02d 전후 자동 수집%s".format(p.hour, p.minute, if (p.wifiOnly) " (와이파이)" else "") else "자동 수집 꺼짐"
        val net = if (Net.isCellular(this)) "\n⚠ 지금 휴대폰 데이터 연결: 네이버가 막기 쉬워요 (와이파이 권장)" else ""
        stateTv.text = (if (running) "● 수집 중…\n" else if (waiting) "◌ 재시도 대기 중 (네이버 차단)\n" else "") +
            "최근: ${p.lastResult}\n$sched$net"
    }

    private fun refreshLog() {
        thread {
            val t = try {
                CollectWorker.py(this).getModule("android_entry")
                    .callAttr("tail", CollectWorker.logFile(this).absolutePath, 40).toString()
            } catch (e: Exception) { "" }
            ui.post { logTv.text = t.ifBlank { "(오늘 로그 없음)" } }
        }
    }

    @SuppressLint("SetTextI18n")
    private fun checkServer() {
        serverTv.text = "확인 중…"
        thread {
            val txt = try {
                val r = JSONObject(CollectWorker.py(this).getModule("android_entry")
                    .callAttr("status", p.statusUrl, p.token).toString())
                if (r.has("error")) "오류: ${r.getString("error")}"
                else "네이버 파일: ${r.optString("naver_file", "-")}\n업로드: ${r.optString("naver_uploaded", "-")}\n" +
                     "브리핑 갱신: ${r.optString("briefing_updated", "-")}\n공공데이터: ${r.optString("public_collected", "-")}"
            } catch (e: Exception) { "오류: ${e.message}" }
            ui.post { serverTv.text = txt }
        }
    }

    @SuppressLint("BatteryLife")
    private fun askBattery() {
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isIgnoringBatteryOptimizations(packageName)) { toast("이미 제외되어 있습니다"); return }
        startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
    }

    private fun startNow() {
        if (!Net.isCellular(this)) { Schedule.runNow(this); toast("수집을 시작합니다 (10~30분)"); return }
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle("휴대폰 데이터로 연결돼 있어요")
            .setMessage("네이버는 통신사 데이터망 주소를 자주 막아서 429 오류가 나기 쉽습니다. 집 와이파이에서 수집하는 걸 권장해요.\n\n그래도 지금 데이터로 수집할까요?")
            .setPositiveButton("데이터로 수집") { _, _ -> Schedule.runNow(this); toast("수집을 시작합니다") }
            .setNegativeButton("취소", null)
            .show()
    }

    override fun onSupportNavigateUp(): Boolean { finish(); return true }

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()
}
