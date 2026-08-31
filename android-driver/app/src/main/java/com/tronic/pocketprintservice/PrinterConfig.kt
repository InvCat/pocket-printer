package com.tronic.pocketprintservice

import android.content.Context

object PrinterConfig {
    private const val PREF_NAME = "tronic_print_service"
    private const val KEY_BT_ADDRESS = "bt_address"
    private const val KEY_BT_NAME = "bt_name"

    fun setPrinter(context: Context, name: String, address: String) {
        context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_BT_NAME, name)
            .putString(KEY_BT_ADDRESS, address)
            .apply()
    }

    fun clearPrinter(context: Context) {
        context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_BT_NAME)
            .remove(KEY_BT_ADDRESS)
            .apply()
    }

    fun getPrinterAddress(context: Context): String? {
        return context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .getString(KEY_BT_ADDRESS, null)
    }

    fun getPrinterLabel(context: Context): String {
        val prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        val name = prefs.getString(KEY_BT_NAME, null)
        val address = prefs.getString(KEY_BT_ADDRESS, null)
        return when {
            name.isNullOrBlank() && address.isNullOrBlank() -> "none"
            name.isNullOrBlank() -> address ?: "none"
            address.isNullOrBlank() -> name
            else -> "$name ($address)"
        }
    }
}
