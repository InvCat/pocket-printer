package com.tronic.pocketprintservice

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {
    private lateinit var selectedPrinterText: TextView
    private lateinit var manualAddressEdit: EditText
    private lateinit var scanButton: Button

    private enum class PendingBtAction { NONE, PICK_PAIRED, SCAN }
    private var pendingBtAction = PendingBtAction.NONE

    private var scanning = false
    private var discoveryStarted = false
    private var scanProgressDialog: AlertDialog? = null
    private val scannedByAddress = LinkedHashMap<String, BluetoothDevice>()
    private val allFoundByAddress = LinkedHashMap<String, BluetoothDevice>()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val scanTimeoutRunnable = Runnable {
        if (scanning) {
            stopDiscoveryQuietly()
            finishPrinterScan(cancelled = false)
        }
    }
    private val startDiscoveryRunnable = Runnable {
        if (!scanning || isFinishing || isDestroyed) return@Runnable
        val adapter = BluetoothAdapter.getDefaultAdapter() ?: return@Runnable
        val started = try {
            adapter.startDiscovery()
        } catch (_: SecurityException) {
            false
        }
        if (!started) {
            unregisterDiscoveryReceiver()
            scanning = false
            discoveryStarted = false
            scanButton.isEnabled = true
            dismissScanProgress()
            toast(getString(R.string.toast_scan_failed))
            return@Runnable
        }
        discoveryStarted = true
        mainHandler.removeCallbacks(scanTimeoutRunnable)
        mainHandler.postDelayed(scanTimeoutRunnable, SCAN_TIMEOUT_MS)
    }

    private val btPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
            val connectOk = result[Manifest.permission.BLUETOOTH_CONNECT] != false
            val scanOk = result[Manifest.permission.BLUETOOTH_SCAN] != false
            val locationOk = result[Manifest.permission.ACCESS_FINE_LOCATION] != false
            when (pendingBtAction) {
                PendingBtAction.PICK_PAIRED -> {
                    pendingBtAction = PendingBtAction.NONE
                    if (connectOk || Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
                        showPairedDevicesDialog()
                    } else {
                        toast(getString(R.string.toast_bt_permission_denied))
                    }
                }
                PendingBtAction.SCAN -> {
                    pendingBtAction = PendingBtAction.NONE
                    val canScan = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                        scanOk && connectOk && locationOk
                    } else {
                        locationOk
                    }
                    if (canScan) {
                        startPrinterScan()
                    } else {
                        toast(getString(R.string.toast_bt_permission_denied))
                    }
                }
                PendingBtAction.NONE -> Unit
            }
        }

    private val discoveryReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                BluetoothDevice.ACTION_FOUND -> {
                    val device = if (Build.VERSION.SDK_INT >= 33) {
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                    } else {
                        @Suppress("DEPRECATION")
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                    } ?: return
                    val key = device.address.uppercase()
                    allFoundByAddress[key] = device
                    if (looksLikeTronicPrinter(device)) {
                        scannedByAddress[key] = device
                    }
                    updateScanProgressMessage()
                }
                BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> {
                    // Ignore leftover FINISHED from cancelDiscovery before our inquiry starts.
                    if (discoveryStarted) {
                        finishPrinterScan(cancelled = false)
                    }
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        selectedPrinterText = findViewById(R.id.selectedPrinterText)
        manualAddressEdit = findViewById(R.id.manualAddressEdit)
        scanButton = findViewById(R.id.scanPrinterButton)
        val pickButton = findViewById<Button>(R.id.pickPrinterButton)
        val clearButton = findViewById<Button>(R.id.clearPrinterButton)
        val saveManualButton = findViewById<Button>(R.id.saveManualButton)

        pickButton.setOnClickListener {
            ensureBluetoothPermission(PendingBtAction.PICK_PAIRED)
        }
        scanButton.setOnClickListener {
            ensureBluetoothPermission(PendingBtAction.SCAN)
        }
        saveManualButton.setOnClickListener {
            saveManualAddress()
        }
        clearButton.setOnClickListener {
            PrinterConfig.clearPrinter(this)
            refreshSelectedPrinterText()
            toast(getString(R.string.toast_printer_cleared))
            manualAddressEdit.setText("")
        }
    }

    override fun onResume() {
        super.onResume()
        refreshSelectedPrinterText()
    }

    override fun onDestroy() {
        mainHandler.removeCallbacks(scanTimeoutRunnable)
        mainHandler.removeCallbacks(startDiscoveryRunnable)
        stopDiscoveryQuietly()
        dismissScanProgress()
        super.onDestroy()
    }

    private fun refreshSelectedPrinterText() {
        val label = PrinterConfig.getPrinterLabel(this)
        selectedPrinterText.text = getString(R.string.selected_printer, label)
        val address = PrinterConfig.getPrinterAddress(this).orEmpty()
        if (address.isNotBlank() && manualAddressEdit.text.toString().isBlank()) {
            manualAddressEdit.setText(address)
        }
    }

    private fun saveManualAddress() {
        val input = manualAddressEdit.text.toString().trim().uppercase()
        if (!isMacAddress(input)) {
            toast(getString(R.string.toast_invalid_mac))
            return
        }
        PrinterConfig.setPrinter(this, "Manual address", input)
        refreshSelectedPrinterText()
        toast(getString(R.string.toast_manual_saved))
    }

    private fun isMacAddress(value: String): Boolean {
        return Regex("^([0-9A-F]{2}:){5}[0-9A-F]{2}$").matches(value)
    }

    private fun ensureBluetoothPermission(action: PendingBtAction) {
        pendingBtAction = action
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED
            ) {
                need += Manifest.permission.BLUETOOTH_CONNECT
            }
            if (action == PendingBtAction.SCAN || action == PendingBtAction.PICK_PAIRED) {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_SCAN)
                    != PackageManager.PERMISSION_GRANTED
                ) {
                    need += Manifest.permission.BLUETOOTH_SCAN
                }
            }
            // Classic inquiry is more reliable with location on many OEMs (even on API 31+).
            if (action == PendingBtAction.SCAN) {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                    != PackageManager.PERMISSION_GRANTED
                ) {
                    need += Manifest.permission.ACCESS_FINE_LOCATION
                }
            }
        } else if (action == PendingBtAction.SCAN) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED
            ) {
                need += Manifest.permission.ACCESS_FINE_LOCATION
            }
        }
        if (need.isNotEmpty()) {
            btPermissionLauncher.launch(need.toTypedArray())
            return
        }
        pendingBtAction = PendingBtAction.NONE
        when (action) {
            PendingBtAction.PICK_PAIRED -> showPairedDevicesDialog()
            PendingBtAction.SCAN -> startPrinterScan()
            PendingBtAction.NONE -> Unit
        }
    }

    private fun showPairedDevicesDialog() {
        val adapter = bluetoothAdapterOrNull() ?: return
        val devices = try {
            adapter.bondedDevices.orEmpty().sortedWith(
                compareByDescending<BluetoothDevice> { looksLikeTronicPrinter(it) }
                    .thenBy { it.name ?: "" }
            )
        } catch (_: SecurityException) {
            toast(getString(R.string.toast_bt_permission_denied))
            return
        }

        if (devices.isEmpty()) {
            toast(getString(R.string.toast_no_paired))
            return
        }

        val items = devices.map { formatDeviceLine(it) }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_select_paired)
            .setItems(items) { _, which ->
                val chosen = devices[which]
                val chosenName = chosen.name ?: "Mini Pocket Printer"
                PrinterConfig.setPrinter(this, chosenName, chosen.address)
                manualAddressEdit.setText(chosen.address.uppercase())
                refreshSelectedPrinterText()
                toast(getString(R.string.toast_selected, chosenName))
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun startPrinterScan() {
        if (scanning) {
            toast(getString(R.string.toast_scan_in_progress))
            return
        }
        val adapter = bluetoothAdapterOrNull() ?: return

        scannedByAddress.clear()
        allFoundByAddress.clear()
        // Seed with already-paired Tronic printers so they always appear.
        try {
            for (d in adapter.bondedDevices.orEmpty()) {
                val key = d.address.uppercase()
                allFoundByAddress[key] = d
                if (looksLikeTronicPrinter(d)) {
                    scannedByAddress[key] = d
                }
            }
        } catch (_: SecurityException) {
            // Continue with inquiry-only results.
        }

        try {
            if (adapter.isDiscovering) {
                adapter.cancelDiscovery()
            }
        } catch (_: SecurityException) {
            toast(getString(R.string.toast_bt_permission_denied))
            return
        }

        // System BT broadcasts must use EXPORTED — NOT_EXPORTED never receives ACTION_FOUND /
        // DISCOVERY_FINISHED, so the dialog would hang until Cancel.
        val filter = IntentFilter().apply {
            addAction(BluetoothDevice.ACTION_FOUND)
            addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
        }
        ContextCompat.registerReceiver(
            this,
            discoveryReceiver,
            filter,
            ContextCompat.RECEIVER_EXPORTED
        )

        discoveryStarted = false
        mainHandler.removeCallbacks(startDiscoveryRunnable)
        mainHandler.postDelayed(startDiscoveryRunnable, 400)

        scanning = true
        scanButton.isEnabled = false
        scanProgressDialog = AlertDialog.Builder(this)
            .setTitle(R.string.dialog_scanning_title)
            .setMessage(scanProgressText())
            .setNegativeButton(android.R.string.cancel) { _, _ ->
                mainHandler.removeCallbacks(scanTimeoutRunnable)
                mainHandler.removeCallbacks(startDiscoveryRunnable)
                stopDiscoveryQuietly()
                finishPrinterScan(cancelled = true)
            }
            .setCancelable(false)
            .show()
    }

    private fun updateScanProgressMessage() {
        scanProgressDialog?.setMessage(scanProgressText())
    }

    private fun scanProgressText(): String {
        val tronic = scannedByAddress.size
        val all = allFoundByAddress.size
        return getString(R.string.dialog_scanning_message) +
            "\n\n" + getString(R.string.dialog_scanning_counts, tronic, all)
    }

    private fun finishPrinterScan(cancelled: Boolean = false) {
        if (!scanning && scanProgressDialog == null) return
        mainHandler.removeCallbacks(scanTimeoutRunnable)
        mainHandler.removeCallbacks(startDiscoveryRunnable)
        scanning = false
        discoveryStarted = false
        scanButton.isEnabled = true
        unregisterDiscoveryReceiver()
        dismissScanProgress()

        if (cancelled) {
            toast(getString(R.string.toast_scan_cancelled))
            return
        }

        var devices = scannedByAddress.values.toList().sortedWith(
            compareByDescending<BluetoothDevice> {
                (it.name ?: "").contains("Mini Pocket", ignoreCase = true)
            }.thenBy { it.name ?: it.address }
        )
        var titleRes = R.string.dialog_scan_results_title

        if (devices.isEmpty() && allFoundByAddress.isNotEmpty()) {
            // No name/MAC heuristic match — still let the user pick from what was seen.
            devices = allFoundByAddress.values.toList().sortedBy { it.name ?: it.address }
            titleRes = R.string.dialog_scan_all_title
        }

        if (devices.isEmpty()) {
            AlertDialog.Builder(this)
                .setTitle(R.string.dialog_scan_empty_title)
                .setMessage(R.string.dialog_scan_empty_message)
                .setPositiveButton(android.R.string.ok, null)
                .show()
            return
        }

        val items = devices.map { formatDeviceLine(it) }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle(titleRes)
            .setItems(items) { _, which ->
                val chosen = devices[which]
                manualAddressEdit.setText(chosen.address.uppercase())
                toast(getString(R.string.toast_mac_filled, chosen.address.uppercase()))
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun stopDiscoveryQuietly() {
        try {
            BluetoothAdapter.getDefaultAdapter()?.let { adapter ->
                if (adapter.isDiscovering) {
                    adapter.cancelDiscovery()
                }
            }
        } catch (_: SecurityException) {
            // ignore
        } catch (_: Exception) {
            // ignore
        }
        unregisterDiscoveryReceiver()
    }

    private fun unregisterDiscoveryReceiver() {
        try {
            unregisterReceiver(discoveryReceiver)
        } catch (_: IllegalArgumentException) {
            // Not registered.
        }
    }

    private fun dismissScanProgress() {
        scanProgressDialog?.dismiss()
        scanProgressDialog = null
    }

    private fun bluetoothAdapterOrNull(): BluetoothAdapter? {
        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null) {
            toast(getString(R.string.toast_no_adapter))
            return null
        }
        if (!adapter.isEnabled) {
            toast(getString(R.string.toast_bt_disabled))
            return null
        }
        return adapter
    }

    private fun looksLikeTronicPrinter(device: BluetoothDevice): Boolean {
        val name = try {
            device.name.orEmpty()
        } catch (_: SecurityException) {
            ""
        }
        val address = device.address.orEmpty().uppercase()
        return name.contains("Mini Pocket", ignoreCase = true) ||
            name.contains("Pocket Printer", ignoreCase = true) ||
            name.contains("Tronic", ignoreCase = true) ||
            address.startsWith("55:55:")
    }

    private fun formatDeviceLine(device: BluetoothDevice): String {
        val name = try {
            device.name
        } catch (_: SecurityException) {
            null
        } ?: getString(R.string.unknown_device)
        return "$name\n${device.address.uppercase()}"
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }

    companion object {
        private const val SCAN_TIMEOUT_MS = 14_000L
    }
}
