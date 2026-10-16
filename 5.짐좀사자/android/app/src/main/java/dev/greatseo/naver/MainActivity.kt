package dev.greatseo.naver

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.webkit.*
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.work.WorkInfo
import androidx.work.WorkManager

/** 첫 화면: 브리핑 웹을 바로 보여줌. 수집·설정은 메뉴 → 설정 */
class MainActivity : AppCompatActivity() {
    private lateinit var p: Prefs
    private lateinit var web: WebView
    private var loadedUrl = ""
    private var authTried = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        p = Prefs(this)
        title = "분당 브리핑"

        web = WebView(this)
        setContentView(web)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // 필터·표시 설정·챗봇 대화 번호를 브라우저 저장소에 기억
            databaseEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
        }
        CookieManager.getInstance().setAcceptCookie(true)
        web.webChromeClient = WebChromeClient()
        web.webViewClient = object : WebViewClient() {
            // 사이트 기본 인증: 저장된 아이디·비밀번호로 한 번 시도, 틀리면 물어봄
            override fun onReceivedHttpAuthRequest(view: WebView, handler: HttpAuthHandler, host: String, realm: String) {
                if (!authTried && p.authUser.isNotBlank()) { authTried = true; handler.proceed(p.authUser, p.authPass) }
                else askLogin(handler)
            }
            override fun onPageFinished(view: WebView, url: String) { authTried = false }
            // 브리핑 밖 링크(네이버부동산·지도 등)는 휴대폰 브라우저나 해당 앱으로
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val home = Uri.parse(p.briefingUrl).host
                if (req.url.host == home) return false
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, req.url)) }
                return true
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web.canGoBack()) web.goBack() else finish() }
        })
        if (savedInstanceState != null) web.restoreState(savedInstanceState) else load()

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        Schedule.applyDaily(this)
        // 수집 중이면 제목 아래에 작게 표시
        WorkManager.getInstance(this).getWorkInfosByTagLiveData("naver").observe(this) { infos ->
            supportActionBar?.subtitle = when {
                infos.any { it.state == WorkInfo.State.RUNNING } -> "매물 수집 중…"
                infos.any { it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount > 0 } -> "수집 재시도 대기"
                else -> null
            }
        }
    }

    private fun load() { loadedUrl = p.briefingUrl; web.loadUrl(loadedUrl) }

    override fun onResume() {
        super.onResume()
        if (p.briefingUrl != loadedUrl) load()          // 설정에서 주소를 바꿨으면 다시 열기
    }

    override fun onSaveInstanceState(outState: Bundle) { super.onSaveInstanceState(outState); web.saveState(outState) }

    private fun askLogin(handler: HttpAuthHandler) {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 16, 48, 0) }
        val id = EditText(this).apply { hint = "아이디"; setText(p.authUser) }
        val pw = EditText(this).apply { hint = "비밀번호"; inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD }
        val keep = CheckBox(this).apply { text = "이 휴대폰에 기억하기"; isChecked = true }
        box.addView(id); box.addView(pw); box.addView(keep)
        AlertDialog.Builder(this).setTitle("브리핑 로그인").setView(box).setCancelable(false)
            .setPositiveButton("로그인") { _, _ ->
                if (keep.isChecked) { p.authUser = id.text.toString(); p.authPass = pw.text.toString() }
                authTried = true
                handler.proceed(id.text.toString().trim(), pw.text.toString())
            }
            .setNegativeButton("취소") { _, _ -> handler.cancel() }
            .show()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "새로고침").setIcon(android.R.drawable.ic_popup_sync).setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
        menu.add(0, 2, 1, "지금 매물 수집")
        menu.add(0, 3, 2, "설정 · 수집 상태")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> web.reload()
            2 -> startActivity(Intent(this, SettingsActivity::class.java).putExtra("startNow", true))
            3 -> startActivity(Intent(this, SettingsActivity::class.java))
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }
}
