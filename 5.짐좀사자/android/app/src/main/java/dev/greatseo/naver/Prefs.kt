package dev.greatseo.naver

import android.content.Context

/** 앱 설정 (업로드 주소·토큰·예약 시각 등) */
class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var uploadUrl: String
        get() = p.getString("uploadUrl", "https://home.greatseo.dev/upload-api/naver")!!
        set(v) = p.edit().putString("uploadUrl", v.trim()).apply()
    var token: String
        get() = p.getString("token", "")!!
        set(v) = p.edit().putString("token", v.trim()).apply()
    var briefingUrl: String
        get() = p.getString("briefingUrl", "https://home.greatseo.dev/")!!
        set(v) = p.edit().putString("briefingUrl", v.trim()).apply()
    var hour: Int
        get() = p.getInt("hour", 10)
        set(v) = p.edit().putInt("hour", v).apply()
    var minute: Int
        get() = p.getInt("minute", 0)
        set(v) = p.edit().putInt("minute", v).apply()
    var wifiOnly: Boolean
        get() = p.getBoolean("wifiOnly", true)
        set(v) = p.edit().putBoolean("wifiOnly", v).apply()
    var scheduleOn: Boolean
        get() = p.getBoolean("scheduleOn", true)
        set(v) = p.edit().putBoolean("scheduleOn", v).apply()
    var authUser: String
        get() = p.getString("authUser", "")!!
        set(v) = p.edit().putString("authUser", v.trim()).apply()
    var authPass: String
        get() = p.getString("authPass", "")!!
        set(v) = p.edit().putString("authPass", v).apply()
    var lastResult: String
        get() = p.getString("lastResult", "아직 수집한 적 없음")!!
        set(v) = p.edit().putString("lastResult", v).apply()

    /** 업로드 주소 …/naver → …/status */
    val statusUrl: String get() = uploadUrl.replace(Regex("/naver/?$"), "/status")
}
