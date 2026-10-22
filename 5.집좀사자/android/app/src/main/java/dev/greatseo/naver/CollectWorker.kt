package dev.greatseo.naver

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/** 수집 → 업로드. 오래 걸리므로 알림을 띄운 포그라운드 작업으로 실행 */
class CollectWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    companion object {
        const val CHANNEL = "collect"
        const val NOTI_ID = 1001
        fun dataDir(ctx: Context) = File(ctx.filesDir, "data")
        fun logFile(ctx: Context) = File(dataDir(ctx), "logs/naver_${LocalDate.now()}.log")
        fun py(ctx: Context): Python {
            if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
            return Python.getInstance()
        }
    }

    private fun notify(text: String, ongoing: Boolean = true) =
        NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("분당 매물 수집")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setOngoing(ongoing)
            .setOnlyAlertOnce(true)
            .build()

    private fun foreground(text: String): ForegroundInfo =
        if (Build.VERSION.SDK_INT >= 29)
            ForegroundInfo(NOTI_ID, notify(text), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else ForegroundInfo(NOTI_ID, notify(text))

    override suspend fun getForegroundInfo() = foreground("수집 준비 중")

    override suspend fun doWork(): Result = coroutineScope {
        val ctx = applicationContext
        val p = Prefs(ctx)
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "매물 수집", NotificationManager.IMPORTANCE_LOW))
        setForeground(foreground("수집 시작"))

        val log = logFile(ctx).apply { parentFile?.mkdirs() }
        val stamp = { LocalDateTime.now().format(DateTimeFormatter.ofPattern("MM-dd HH:mm")) }
        val py = py(ctx)
        val entry = py.getModule("android_entry")
        NaverWeb.init(ctx)

        // 수집은 백그라운드 스레드에서, 그동안 5초마다 로그 마지막 줄을 알림에 표시
        val job = async(Dispatchers.IO) { entry.callAttr("run", dataDir(ctx).absolutePath, log.absolutePath).toString() }
        while (job.isActive) {
            val last = entry.callAttr("tail", log.absolutePath, 1).toString().trim()
            if (last.isNotEmpty()) setForeground(foreground(last.take(120)))
            delay(5000)
        }
        val res = JSONObject(job.await())
        NaverWeb.destroy()
        when (res.getString("status")) {
            "busy" -> return@coroutineScope Result.success()        // 다른 수집이 이미 진행 중
            "blocked" -> {
                p.lastResult = "${stamp()} 네이버가 막아 중단 (${Net.label(ctx)}) → 40분 뒤 이어서 재시도"
                return@coroutineScope if (runAttemptCount < 3) Result.retry() else Result.failure()
            }
            "error" -> {
                p.lastResult = "${stamp()} 수집 실패: ${res.optString("msg")}"
                return@coroutineScope Result.failure()
            }
        }
        val file = res.getString("file")
        if (p.token.isBlank() || p.uploadUrl.isBlank()) {
            p.lastResult = "${stamp()} 수집 완료 (업로드 설정 없음): ${File(file).name}"
            return@coroutineScope Result.success()
        }
        setForeground(foreground("서버로 업로드 중"))
        var up = JSONObject()
        for (i in 0 until 3) {
            up = JSONObject(entry.callAttr("upload", p.uploadUrl, p.token, file).toString())
            if (up.optBoolean("ok")) break
            delay(60_000L * (i + 1))
        }
        p.lastResult = if (up.optBoolean("ok"))
            "${stamp()} 수집·업로드 완료: ${File(file).name} (${up.optInt("kb")}KB)"
        else "${stamp()} 업로드 실패 (${up.optInt("code")}): ${up.optString("body").take(120)}"
        nm.notify(NOTI_ID + 1, notify(p.lastResult, ongoing = false))
        // 오래된 로그 정리 (30일)
        File(dataDir(ctx), "logs").listFiles()?.filter { it.lastModified() < System.currentTimeMillis() - 30L * 86400_000 }?.forEach { it.delete() }
        if (up.optBoolean("ok")) Result.success() else Result.retry()
    }
}
