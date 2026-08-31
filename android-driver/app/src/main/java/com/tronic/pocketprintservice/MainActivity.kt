package com.tronic.pocketprintservice

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
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

    private val btPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                showPairedDevicesDialog()
            } else {
                toast("Bluetooth permission denied.")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        selectedPrinterText = findViewById(R.id.selectedPrinterText)
        manualAddressEdit = findViewById(R.id.manualAddressEdit)
        val pickButton = findViewById<Button>(R.id.pickPrinterButton)
        val clearButton = findViewById<Button>(R.id.clearPrinterButton)
        val saveManualButton = findViewById<Button>(R.id.saveManualButton)

        pickButton.setOnClickListener {
            ensureBluetoothPermissionAndPick()
        }
        saveManualButton.setOnClickListener {
            saveManualAddress()
        }
        clearButton.setOnClickListener {
            PrinterConfig.clearPrinter(this)
            refreshSelectedPrinterText()
            toast("Printer selection cleared.")
            manualAddressEdit.setText("")
        }
    }

    override fun onResume() {
        super.onResume()
        refreshSelectedPrinterText()
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
            toast("Invalid MAC format. Example: 55:55:09:10:98:B6")
            return
        }
        PrinterConfig.setPrinter(this, "Manual address", input)
        refreshSelectedPrinterText()
        toast("Manual printer address saved.")
    }

    private fun isMacAddress(value: String): Boolean {
        return Regex("^([0-9A-F]{2}:){5}[0-9A-F]{2}$").matches(value)
    }

    private fun ensureBluetoothPermissionAndPick() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.BLUETOOTH_CONNECT
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                btPermissionLauncher.launch(Manifest.permission.BLUETOOTH_CONNECT)
                return
            }
        }
        showPairedDevicesDialog()
    }

    private fun showPairedDevicesDialog() {
        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null) {
            toast("Bluetooth adapter not available.")
            return
        }
        if (!adapter.isEnabled) {
            toast("Enable Bluetooth first, then try again.")
            return
        }

        val devices = try {
            adapter.bondedDevices.orEmpty().sortedWith(
                compareByDescending<BluetoothDevice> {
                    (it.name ?: "").contains("Mini Pocket Printer", ignoreCase = true)
                }.thenBy { it.name ?: "" }
            )
        } catch (_: SecurityException) {
            toast("Missing Bluetooth permission.")
            return
        }

        if (devices.isEmpty()) {
            toast("No paired Bluetooth devices found.")
            return
        }

        val items = devices.map { "${it.name ?: "Unknown"}\n${it.address}" }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("Select paired printer")
            .setItems(items) { _, which ->
                val chosen = devices[which]
                val chosenName = chosen.name ?: "Mini Pocket Printer"
                PrinterConfig.setPrinter(this, chosenName, chosen.address)
                refreshSelectedPrinterText()
                toast("Selected: $chosenName")
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
