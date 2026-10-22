package dev.greatseo.naver

import android.annotation.SuppressLint
import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicInteger

/**
 * 휴대폰 내장 브라우저 엔진(WebView)으로 네이버 API 를 부름.
 * 파이썬 통신 모듈은 휴대폰 데이터망에서 429 로 막히지만, 같은 연결에서도 브라우저 요청은 통과하므로
 * fin.land.naver.com 페이지를 화면 없이 열어 두고 그 안에서 fetch() 로 요청한다 (같은 사이트라 쿠키도 그대로).
 * 파이썬(android_entry.WebSession)에서 NaverWeb.INSTANCE.request(...) 로 호출.
 */
object NaverWeb {
    private const val HOME = "https://fin.land.naver.com/"
    private const val FALLBACK = "https://fin.land.naver.com/front-api/v1/search/autocomplete/legalDivisions?keyword=%EB%B6%84%EB%8B%B9%EA%B5%AC"

    class Res(private val status: Int, private val text: String) {
        fun getStatus() = status
        fun getText() = text
    }

    private lateinit var app: Context
    private var web: WebView? = null
    private val main = Handler(Looper.getMainLooper())
    private val pending = ConcurrentHashMap<Int, CompletableFuture<Res>>()
    private val seq = AtomicInteger()
    @Volatile private var ready = false
    @Volatile private var latch = CountDownLatch(1)

    fun init(ctx: Context) { app = ctx.applicationContext }

    private class Bridge {
        @JavascriptInterface
        fun done(id: Int, status: Int, text: String) { pending[id]?.complete(Res(status, text)) }
    }

    /** fin.land.naver.com 페이지를 열어 둠 (요청이 같은 사이트에서 나가도록). 메인 스레드가 아닌 곳에서 호출 */
    @SuppressLint("SetJavaScriptEnabled")
    fun ensureReady(timeoutMs: Int): Boolean {
        if (ready && web != null) return true
        latch = CountDownLatch(1)
        main.post {
            val w = web ?: WebView(app).also { w ->
                web = w
                w.settings.javaScriptEnabled = true
                w.settings.domStorageEnabled = true
                CookieManager.getInstance().setAcceptCookie(true)
                w.addJavascriptInterface(Bridge(), "NaverBridge")
                w.webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView, url: String) {
                        // 다른 주소로 넘어갔으면(모바일 사이트 등) API 주소를 직접 열어 출처를 fin.land 로 맞춤
                        if (Uri.parse(url).host != "fin.land.naver.com") { view.loadUrl(FALLBACK); return }
                        ready = true
                        latch.countDown()
                    }
                }
            }
            w.loadUrl(HOME)
        }
        latch.await(timeoutMs.toLong(), TimeUnit.MILLISECONDS)
        return ready
    }

    /** 결과: 상태 코드와 본문. -1 연결 오류, -2 시간 초과 */
    fun request(method: String, url: String, body: String?, timeoutMs: Int): Res {
        val id = seq.incrementAndGet()
        val f = CompletableFuture<Res>()
        pending[id] = f
        val q = { s: String -> JSONObject.quote(s) }
        val js = """(function(){var o={method:${q(method)},credentials:'include',headers:{'Accept':'application/json, text/plain, */*'}};
            ${if (body != null) "o.headers['Content-Type']='application/json';o.body=${q(body)};" else ""}
            fetch(${q(url)},o).then(function(r){return r.text().then(function(t){NaverBridge.done($id,r.status,t);});})
            .catch(function(e){NaverBridge.done($id,-1,String(e));});})();"""
        main.post { web?.evaluateJavascript(js, null) ?: f.complete(Res(-1, "WebView 없음")) }
        return try {
            f.get(timeoutMs.toLong(), TimeUnit.MILLISECONDS)
        } catch (e: TimeoutException) {
            Res(-2, "시간 초과")
        } finally {
            pending.remove(id)
        }
    }

    fun destroy() {
        main.post { web?.destroy(); web = null; ready = false }
    }
}
