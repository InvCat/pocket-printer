package com.tronic.pocketprintservice

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors

/**
 * Share-sheet target: preview shared image / PDF / text at print width, then print.
 */
class ShareReceiveActivity : AppCompatActivity() {
    private lateinit var previewImage: ImageView
    private lateinit var statusText: TextView
    private lateinit var printerText: TextView
    private lateinit var printButton: Button

    private val pages = mutableListOf<Bitmap>()
    private val worker = Executors.newSingleThreadExecutor()

    private val btPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
            val connectOk = result[Manifest.permission.BLUETOOTH_CONNECT] != false
            if (connectOk || Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
                startPrint()
            } else {
                toast("Bluetooth permission denied.")
                setStatus(getString(R.string.share_status_need_bt))
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_share_receive)

        previewImage = findViewById(R.id.sharePreviewImage)
        statusText = findViewById(R.id.shareStatusText)
        printerText = findViewById(R.id.sharePrinterText)
        printButton = findViewById(R.id.sharePrintButton)
        val setupButton = findViewById<Button>(R.id.shareOpenSetupButton)

        refreshPrinterLabel()
        printButton.setOnClickListener { ensureBtAndPrint() }
        setupButton.setOnClickListener {
            startActivity(Intent(this, MainActivity::class.java))
        }

        setStatus(getString(R.string.share_status_loading))
        worker.execute {
            try {
                val loaded = loadFromIntent(intent)
                runOnUiThread {
                    pages.clear()
                    pages.addAll(loaded)
                    if (pages.isEmpty()) {
                        setStatus(getString(R.string.share_status_empty))
                        printButton.isEnabled = false
                    } else {
                        previewImage.setImageBitmap(pages.first())
                        val more = if (pages.size > 1) " (+${pages.size - 1} page(s))" else ""
                        setStatus(getString(R.string.share_status_ready, pages.size) + more)
                        printButton.isEnabled = PrinterConfig.getPrinterAddress(this) != null
                        if (PrinterConfig.getPrinterAddress(this) == null) {
                            setStatus(getString(R.string.share_status_need_printer))
                        }
                    }
                }
            } catch (t: Throwable) {
                runOnUiThread {
                    setStatus(getString(R.string.share_status_error, t.message ?: t.javaClass.simpleName))
                    printButton.isEnabled = false
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refreshPrinterLabel()
        if (pages.isNotEmpty() && PrinterConfig.getPrinterAddress(this) != null) {
            printButton.isEnabled = true
            if (statusText.text.toString().contains("printer", ignoreCase = true)) {
                setStatus(getString(R.string.share_status_ready, pages.size))
            }
        }
    }

    override fun onDestroy() {
        worker.shutdownNow()
        pages.forEach { it.recycle() }
        pages.clear()
        super.onDestroy()
    }

    private fun refreshPrinterLabel() {
        printerText.text = getString(
            R.string.selected_printer,
            PrinterConfig.getPrinterLabel(this)
        )
    }

    private fun ensureBtAndPrint() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val need = mutableListOf<String>()
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED
            ) {
                need += Manifest.permission.BLUETOOTH_CONNECT
            }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_SCAN)
                != PackageManager.PERMISSION_GRANTED
            ) {
                need += Manifest.permission.BLUETOOTH_SCAN
            }
            if (need.isNotEmpty()) {
                btPermissionLauncher.launch(need.toTypedArray())
                return
            }
        }
        startPrint()
    }

    private fun startPrint() {
        val address = PrinterConfig.getPrinterAddress(this)
        if (address.isNullOrBlank()) {
            toast(getString(R.string.share_status_need_printer))
            return
        }
        if (pages.isEmpty()) {
            toast(getString(R.string.share_status_empty))
            return
        }
        printButton.isEnabled = false
        setStatus(getString(R.string.share_status_printing))
        // Copy bitmaps for background thread (UI may recycle on destroy)
        val copies = pages.map { it.copy(it.config ?: Bitmap.Config.ARGB_8888, false) }
        worker.execute {
            try {
                TronicBluetoothPrinter(applicationContext, address).printBitmaps(copies)
                runOnUiThread {
                    setStatus(getString(R.string.share_status_done))
                    printButton.isEnabled = true
                    toast("Printed.")
                }
            } catch (t: Throwable) {
                runOnUiThread {
                    setStatus(getString(R.string.share_status_error, t.message ?: t.javaClass.simpleName))
                    printButton.isEnabled = true
                    toast(t.message ?: "Print failed")
                }
            } finally {
                copies.forEach { it.recycle() }
            }
        }
    }

    private fun loadFromIntent(intent: Intent?): List<Bitmap> {
        if (intent == null) return emptyList()
        val action = intent.action
        val type = intent.type ?: "*/*"

        when (action) {
            Intent.ACTION_SEND -> {
                // Prefer a file/stream (screenshot, PDF) over plain text.
                val uri = streamUri(intent)
                if (uri != null) {
                    return loadUri(uri, type)
                }
                if (type.startsWith("text/") || intent.hasExtra(Intent.EXTRA_TEXT)) {
                    val text = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
                    if (text.isBlank()) return emptyList()
                    if (isWebLinkShare(text)) {
                        throw IllegalArgumentException(getString(R.string.share_error_url_only))
                    }
                    return listOf(
                        run {
                            val rendered = TronicBluetoothPrinter.renderTextToBitmap(text)
                            val mono = TronicBluetoothPrinter.preparePrintBitmap(rendered)
                            if (mono !== rendered) rendered.recycle()
                            mono
                        }
                    )
                }
                return emptyList()
            }
            Intent.ACTION_SEND_MULTIPLE -> {
                val uris = streamUriList(intent)
                if (uris.isEmpty()) return emptyList()
                val out = mutableListOf<Bitmap>()
                for (u in uris) {
                    out += loadUri(u, type)
                }
                return out
            }
            else -> return emptyList()
        }
    }

    /**
     * Chrome / browsers often "share" only the page URL (sometimes with a title line),
     * not a screenshot or PDF — printing that yields a useless one-line strip.
     */
    private fun isWebLinkShare(text: String): Boolean {
        val lines = text.replace("\r\n", "\n").lines().map { it.trim() }.filter { it.isNotEmpty() }
        if (lines.isEmpty()) return false
        val urlLine = lines.last()
        val looksUrl = urlLine.startsWith("http://", ignoreCase = true) ||
            urlLine.startsWith("https://", ignoreCase = true) ||
            urlLine.matches(Regex("^https?://\\S+$", RegexOption.IGNORE_CASE))
        if (!looksUrl) return false
        // Single URL, or "Title" + URL (typical browser share).
        return lines.size == 1 || lines.size == 2
    }

    private fun streamUri(intent: Intent): Uri? {
        return if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }
    }

    private fun streamUriList(intent: Intent): List<Uri> {
        return if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()
        }
    }

    private fun loadUri(uri: Uri, mimeHint: String): List<Bitmap> {
        val mime = contentResolver.getType(uri) ?: mimeHint
        return when {
            mime.equals("application/pdf", ignoreCase = true) ||
                (uri.toString().lowercase().endsWith(".pdf")) -> {
                contentResolver.openFileDescriptor(uri, "r")?.use { pfd ->
                    TronicBluetoothPrinter.renderPdfToBitmaps(pfd).map { page ->
                        val mono = TronicBluetoothPrinter.preparePrintBitmap(page)
                        if (mono !== page) page.recycle()
                        mono
                    }
                } ?: emptyList()
            }
                    mime.startsWith("image/") || mimeHint.startsWith("image/") -> {
                contentResolver.openInputStream(uri)?.use { input ->
                    val decoded = BitmapFactory.decodeStream(input)
                        ?: throw IllegalArgumentException("Could not decode image.")
                    val mono = TronicBluetoothPrinter.preparePrintBitmap(decoded)
                    if (mono !== decoded) {
                        decoded.recycle()
                    }
                    listOf(mono)
                } ?: emptyList()
            }
            mime.startsWith("text/") -> {
                contentResolver.openInputStream(uri)?.bufferedReader()?.use { reader ->
                    val text = reader.readText()
                    val rendered = TronicBluetoothPrinter.renderTextToBitmap(text)
                    val mono = TronicBluetoothPrinter.preparePrintBitmap(rendered)
                    if (mono !== rendered) rendered.recycle()
                    listOf(mono)
                } ?: emptyList()
            }
            else -> {
                // Best-effort: try image, then PDF
                contentResolver.openInputStream(uri)?.use { input ->
                    val bytes = input.readBytes()
                    val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (decoded != null) {
                        val mono = TronicBluetoothPrinter.preparePrintBitmap(decoded)
                        if (mono !== decoded) decoded.recycle()
                        return listOf(mono)
                    }
                }
                contentResolver.openFileDescriptor(uri, "r")?.use { pfd ->
                    return TronicBluetoothPrinter.renderPdfToBitmaps(pfd).map { page ->
                        val mono = TronicBluetoothPrinter.preparePrintBitmap(page)
                        if (mono !== page) page.recycle()
                        mono
                    }
                }
                emptyList()
            }
        }
    }

    private fun setStatus(msg: String) {
        statusText.text = msg
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
