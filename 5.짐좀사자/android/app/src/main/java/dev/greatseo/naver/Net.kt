package dev.greatseo.naver

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/** 지금 연결이 와이파이인지 휴대폰 데이터인지 (네이버는 통신사 데이터망 IP를 훨씬 자주 막음) */
object Net {
    fun isCellular(ctx: Context): Boolean {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) && !caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }
    fun label(ctx: Context) = if (isCellular(ctx)) "휴대폰 데이터" else "와이파이"
}
