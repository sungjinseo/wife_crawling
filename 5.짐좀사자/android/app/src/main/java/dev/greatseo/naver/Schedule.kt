package dev.greatseo.naver

import android.content.Context
import androidx.work.*
import java.time.Duration
import java.time.LocalDateTime
import java.util.concurrent.TimeUnit

/** WorkManager 예약: 매일 정해진 시각(±수 분) + 지금 한 번 */
object Schedule {
    const val DAILY = "naver-daily"
    const val NOW = "naver-now"

    private fun constraints(p: Prefs) = Constraints.Builder()
        .setRequiredNetworkType(if (p.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
        .setRequiresBatteryNotLow(true)
        .build()

    fun applyDaily(ctx: Context) {
        val p = Prefs(ctx)
        val wm = WorkManager.getInstance(ctx)
        if (!p.scheduleOn) { wm.cancelUniqueWork(DAILY); return }
        val now = LocalDateTime.now()
        var next = now.withHour(p.hour).withMinute(p.minute).withSecond(0).withNano(0)
        if (!next.isAfter(now)) next = next.plusDays(1)
        val req = PeriodicWorkRequestBuilder<CollectWorker>(24, TimeUnit.HOURS)
            .setInitialDelay(Duration.between(now, next).toMinutes(), TimeUnit.MINUTES)
            .setConstraints(constraints(p))
            .setBackoffCriteria(BackoffPolicy.LINEAR, 40, TimeUnit.MINUTES)   // 막히면 40분 뒤 이어서
            .addTag("naver")
            .build()
        wm.enqueueUniquePeriodicWork(DAILY, ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE, req)
    }

    fun runNow(ctx: Context) {
        val req = OneTimeWorkRequestBuilder<CollectWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.LINEAR, 40, TimeUnit.MINUTES)
            .addTag("naver")
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(NOW, ExistingWorkPolicy.KEEP, req)
    }

    fun cancelAll(ctx: Context) {
        WorkManager.getInstance(ctx).cancelAllWorkByTag("naver")
    }
}
