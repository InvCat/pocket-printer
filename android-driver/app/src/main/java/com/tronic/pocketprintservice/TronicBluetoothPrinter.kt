package com.tronic.pocketprintservice

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.pdf.PdfRenderer
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.core.content.ContextCompat
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.roundToInt

private const val PRINT_WIDTH = 384
private const val BYTES_PER_ROW = PRINT_WIDTH / 8
private const val FEED_DOTS = 0x50

private val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805f9b34fb")
private val BLE_SERVICE_UUID: UUID = UUID.fromString("e7810a71-73ae-499d-8c15-faa9aef0c3f2")

private val CMD_ENABLE = byteArrayOf(0x10, 0xFF.toByte(), 0xF1.toByte(), 0x03)
private val CMD_WAKEUP = ByteArray(12)
private val CMD_STOP = byteArrayOf(0x10, 0xFF.toByte(), 0xF1.toByte(), 0x45)
private val CMD_FEED = byteArrayOf(0x1B, 0x4A, FEED_DOTS.toByte())

class TronicBluetoothPrinter(private val context: Context, private val address: String) {

    fun printPdf(pfd: ParcelFileDescriptor) {
        printBitmaps(renderPdfToBitmaps(pfd))
    }

    fun printBitmaps(bitmaps: List<Bitmap>) {
        if (bitmaps.isEmpty()) {
            throw IOException("Nothing to print.")
        }
        ensureConnectPermission()
        val adapter = BluetoothAdapter.getDefaultAdapter()
            ?: throw IOException("Bluetooth adapter is not available.")
        if (!adapter.isEnabled) {
            throw IOException("Bluetooth is disabled.")
        }

        val device = adapter.getRemoteDevice(address)
        // Best-effort: cancelDiscovery needs BLUETOOTH_SCAN on API 31+.
        try {
            adapter.cancelDiscovery()
        } catch (_: SecurityException) {
            // Continue — connect still works without cancelling discovery.
        }
        val chunks = buildPrintChunksFromBitmaps(bitmaps)

        val sppErr = runCatching { sendViaSpp(device, chunks) }.exceptionOrNull()
        if (sppErr == null) {
            return
        }

        val bleErr = runCatching { sendViaBle(device, chunks) }.exceptionOrNull()
        if (bleErr == null) {
            return
        }

        throw IOException(
            "SPP and BLE both failed. SPP: ${sppErr.message ?: sppErr.javaClass.simpleName}; " +
                "BLE: ${bleErr.message ?: bleErr.javaClass.simpleName}"
        )
    }

    companion object {
        fun renderPdfToBitmaps(pfd: ParcelFileDescriptor): List<Bitmap> {
            val pages = mutableListOf<Bitmap>()
            PdfRenderer(pfd).use { renderer ->
                if (renderer.pageCount == 0) {
                    throw IOException("PDF has no pages.")
                }
                for (pageIndex in 0 until renderer.pageCount) {
                    renderer.openPage(pageIndex).use { page ->
                        pages += renderPdfPageForPrint(page)
                    }
                }
            }
            return pages
        }

        fun fitBitmapToPrintWidth(source: Bitmap): Bitmap {
            val src = if (source.config == Bitmap.Config.ARGB_8888) {
                source
            } else {
                source.copy(Bitmap.Config.ARGB_8888, false) ?: source
            }
            if (src.width == PRINT_WIDTH) {
                return src
            }
            val targetHeight = max(1, (src.height.toFloat() / src.width.toFloat() * PRINT_WIDTH).roundToInt())
            return Bitmap.createScaledBitmap(src, PRINT_WIDTH, targetHeight, true)
        }

        /**
         * Crop near-white margins (Chrome/web print often pads the 48 mm page).
         * Returns [source] unchanged if there is nothing to crop.
         */
        fun trimWhitespace(
            source: Bitmap,
            threshold: Int = 245,
            marginPx: Int = 4,
            marginBottomPx: Int = 8
        ): Bitmap {
            val width = source.width
            val height = source.height
            if (width < 8 || height < 8) {
                return source
            }
            val pixels = IntArray(width * height)
            source.getPixels(pixels, 0, width, 0, 0, width, height)

            var minX = width
            var minY = height
            var maxX = -1
            var maxY = -1
            for (y in 0 until height) {
                val row = y * width
                for (x in 0 until width) {
                    val c = pixels[row + x]
                    val luma = if (Color.alpha(c) < 128) {
                        255
                    } else {
                        (Color.red(c) * 299 + Color.green(c) * 587 + Color.blue(c) * 114) / 1000
                    }
                    if (luma < threshold) {
                        if (x < minX) minX = x
                        if (x > maxX) maxX = x
                        if (y < minY) minY = y
                        if (y > maxY) maxY = y
                    }
                }
            }
            if (maxX < 0 || maxY < 0) {
                val stubH = minOf(height, max(32, marginPx + marginBottomPx + 8))
                return Bitmap.createBitmap(source, 0, 0, width, stubH)
            }
            val left = max(0, minX - marginPx)
            val top = max(0, minY - marginPx)
            val right = minOf(width, maxX + 1 + marginPx)
            val bottom = minOf(height, maxY + 1 + marginBottomPx)
            if (left == 0 && top == 0 && right == width && bottom == height) {
                return source
            }
            return Bitmap.createBitmap(source, left, top, right - left, bottom - top)
        }

        /** Rotate clockwise by 90/180/270°. Returns [source] if degrees is a multiple of 360. */
        fun rotateBitmap(source: Bitmap, degrees: Int): Bitmap {
            val d = ((degrees % 360) + 360) % 360
            if (d == 0) {
                return source
            }
            val matrix = Matrix().apply { postRotate(d.toFloat()) }
            return Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
        }

        /**
         * Scale to 384 px width and Floyd–Steinberg dither to pure black/white
         * (same approach as [tronic_printer.py] `Image.convert("1")`).
         * Preview and print should both use this so WYSIWYG matches the head.
         */
        fun preparePrintBitmap(source: Bitmap, rotateDegrees: Int = 0): Bitmap {
            val rotated = rotateBitmap(source, rotateDegrees)
            val fitted = fitBitmapToPrintWidth(rotated)
            val mono = floydSteinbergMono(fitted)
            if (fitted !== rotated && fitted !== mono) {
                fitted.recycle()
            }
            if (rotated !== source && rotated !== mono) {
                rotated.recycle()
            }
            return mono
        }

        /** Floyd–Steinberg error diffusion → ARGB black / white. */
        fun floydSteinbergMono(source: Bitmap): Bitmap {
            val src = if (source.config == Bitmap.Config.ARGB_8888) {
                source
            } else {
                source.copy(Bitmap.Config.ARGB_8888, false)
                    ?: throw IOException("Could not convert bitmap to ARGB_8888.")
            }
            val width = src.width
            val height = src.height
            val pixels = IntArray(width * height)
            src.getPixels(pixels, 0, width, 0, 0, width, height)

            val gray = FloatArray(width * height)
            for (i in pixels.indices) {
                val c = pixels[i]
                // Transparent / near-transparent → paper white
                if (Color.alpha(c) < 128) {
                    gray[i] = 255f
                } else {
                    gray[i] = Color.red(c) * 0.299f + Color.green(c) * 0.587f + Color.blue(c) * 0.114f
                }
            }

            val out = IntArray(width * height)
            for (y in 0 until height) {
                for (x in 0 until width) {
                    val i = y * width + x
                    val old = gray[i].coerceIn(0f, 255f)
                    val newVal = if (old < 128f) 0f else 255f
                    out[i] = if (newVal < 128f) Color.BLACK else Color.WHITE
                    val err = old - newVal
                    if (x + 1 < width) {
                        gray[i + 1] += err * 7f / 16f
                    }
                    if (y + 1 < height) {
                        if (x > 0) {
                            gray[i + width - 1] += err * 3f / 16f
                        }
                        gray[i + width] += err * 5f / 16f
                        if (x + 1 < width) {
                            gray[i + width + 1] += err * 1f / 16f
                        }
                    }
                }
            }

            val result = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            result.setPixels(out, 0, width, 0, 0, width, height)
            return result
        }

        fun renderTextToBitmap(text: String, fontSizePx: Float = 28f): Bitmap {
            val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.BLACK
                textSize = fontSizePx
                typeface = android.graphics.Typeface.MONOSPACE
            }
            val margin = 8
            val maxWidth = PRINT_WIDTH - 2 * margin
            val lines = wrapText(text.replace("\r\n", "\n"), paint, maxWidth.toFloat())
            val lineHeight = (paint.fontSpacing).roundToInt().coerceAtLeast(fontSizePx.roundToInt() + 6)
            val height = max(lineHeight + 2 * margin, lines.size * lineHeight + 2 * margin)
            val bitmap = Bitmap.createBitmap(PRINT_WIDTH, height, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            canvas.drawColor(Color.WHITE)
            var y = margin - paint.ascent()
            for (line in lines) {
                canvas.drawText(line, margin.toFloat(), y, paint)
                y += lineHeight
            }
            return bitmap
        }

        private fun wrapText(text: String, paint: android.graphics.Paint, maxWidth: Float): List<String> {
            val out = mutableListOf<String>()
            for (para in text.split("\n")) {
                if (para.isEmpty()) {
                    out += ""
                    continue
                }
                var current = ""
                for (word in para.split(" ")) {
                    val trial = if (current.isEmpty()) word else "$current $word"
                    if (paint.measureText(trial) <= maxWidth || current.isEmpty()) {
                        current = trial
                    } else {
                        out += current
                        current = word
                    }
                }
                out += current
            }
            if (out.isEmpty()) out += ""
            return out
        }

        /**
         * Rasterize a PDF page at ~203 dpi, crop browser/print margins, fit to 384 px.
         */
        private fun renderPdfPageForPrint(page: PdfRenderer.Page): Bitmap {
            val ptW = max(1, page.width)
            val ptH = max(1, page.height)
            // PdfRenderer page size is in points (1/72"); thermal head is 203 dpi.
            val scale = 203f / 72f
            val renderW = (ptW * scale).roundToInt().coerceIn(PRINT_WIDTH, 2400)
            val renderH = max(1, (ptH.toFloat() / ptW.toFloat() * renderW).roundToInt().coerceAtMost(20000))

            val rendered = Bitmap.createBitmap(renderW, renderH, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(rendered)
            canvas.drawColor(Color.WHITE)
            val matrix = Matrix().apply {
                setScale(renderW.toFloat() / ptW.toFloat(), renderH.toFloat() / ptH.toFloat())
            }
            page.render(rendered, null, matrix, PdfRenderer.Page.RENDER_MODE_FOR_PRINT)

            val trimmed = trimWhitespace(rendered)
            if (trimmed !== rendered) {
                rendered.recycle()
            }
            val fitted = fitBitmapToPrintWidth(trimmed)
            if (fitted !== trimmed) {
                trimmed.recycle()
            }
            return fitted
        }
    }

    private fun ensureConnectPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val connect = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.BLUETOOTH_CONNECT
            ) == PackageManager.PERMISSION_GRANTED
            if (!connect) {
                throw SecurityException("BLUETOOTH_CONNECT permission is missing.")
            }
        }
    }

    private fun connectSpp(device: BluetoothDevice): BluetoothSocket {
        // Experimental pairing-free mode has the best chance with insecure RFCOMM first.
        try {
            return device.createInsecureRfcommSocketToServiceRecord(SPP_UUID).apply { connect() }
        } catch (_: Exception) {
            // Continue to secure fallback.
        }
        try {
            return device.createRfcommSocketToServiceRecord(SPP_UUID).apply { connect() }
        } catch (_: Exception) {
            // Continue to reflection fallback.
        }
        val method = device.javaClass.getMethod("createRfcommSocket", Int::class.javaPrimitiveType)
        val socket = method.invoke(device, 1) as BluetoothSocket
        socket.connect()
        return socket
    }

    private fun buildPrintChunksFromBitmaps(bitmaps: List<Bitmap>): List<ByteArray> {
        val chunks = mutableListOf<ByteArray>()
        chunks += CMD_ENABLE
        chunks += CMD_WAKEUP
        for (raw in bitmaps) {
            val bitmap = preparePrintBitmap(raw)
            val raster = bitmapToRaster(bitmap)
            chunks += rasterBlockChunks(raster, bitmap.height)
            chunks += CMD_FEED
            if (bitmap !== raw) {
                bitmap.recycle()
            }
        }
        chunks += CMD_STOP
        return chunks
    }

    private fun sendViaSpp(device: BluetoothDevice, chunks: List<ByteArray>) {
        val socket = connectSpp(device)
        socket.use { btSocket ->
            val out = btSocket.outputStream
            val input = btSocket.inputStream

            for (chunk in chunks) {
                out.write(chunk)
                out.flush()
                when {
                    chunk === CMD_ENABLE || chunk === CMD_WAKEUP -> sleepMs(150)
                    chunk.contentEquals(CMD_FEED) -> sleepMs(180)
                    chunk.contentEquals(CMD_STOP) -> Unit
                    else -> sleepMs(2)
                }
            }
            waitForAck(input)
        }
    }

    private fun sendViaBle(device: BluetoothDevice, chunks: List<ByteArray>) {
        val session = BleSession(context, device)
        session.connect()
        try {
            for (chunk in chunks) {
                session.write(chunk)
                when {
                    chunk === CMD_ENABLE || chunk === CMD_WAKEUP -> sleepMs(150)
                    chunk.contentEquals(CMD_FEED) -> sleepMs(180)
                    else -> sleepMs(10)
                }
            }
        } finally {
            session.close()
        }
    }

    private fun bitmapToRaster(bitmap: Bitmap): ByteArray {
        val width = bitmap.width
        val height = bitmap.height
        if (width != PRINT_WIDTH) {
            throw IOException("Rendered width must be $PRINT_WIDTH px, got $width.")
        }

        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        val out = ByteArray(BYTES_PER_ROW * height)
        var outIdx = 0

        for (y in 0 until height) {
            for (xb in 0 until BYTES_PER_ROW) {
                var packed = 0
                for (bit in 0 until 8) {
                    val x = xb * 8 + bit
                    val color = pixels[y * width + x]
                    val r = Color.red(color)
                    val g = Color.green(color)
                    val b = Color.blue(color)
                    // Expect preparePrintBitmap() output (0/255); 128 threshold is safe either way.
                    val luma = (r * 299 + g * 587 + b * 114) / 1000
                    if (luma < 128) {
                        packed = packed or (0x80 shr bit)
                    }
                }
                out[outIdx++] = packed.toByte()
            }
        }
        return out
    }

    private fun rasterBlockChunks(raster: ByteArray, height: Int): List<ByteArray> {
        val chunks = mutableListOf<ByteArray>()
        val header = byteArrayOf(
            0x1D,
            0x76,
            0x30,
            0x00,
            BYTES_PER_ROW.toByte(),
            0x00,
            (height and 0xFF).toByte(),
            ((height shr 8) and 0xFF).toByte()
        )
        chunks += header
        var offset = 0
        // ~128 rows per burst (row = 48 bytes) — reduces Bluetooth underrun banding
        val chunkSize = BYTES_PER_ROW * 128
        while (offset < raster.size) {
            val end = minOf(raster.size, offset + chunkSize)
            chunks += raster.copyOfRange(offset, end)
            offset = end
        }
        return chunks
    }

    private fun waitForAck(input: InputStream) {
        // The printer usually returns 0xAA after CMD_STOP.
        // Some stacks swallow responses; timeout is tolerated.
        try {
            val start = System.currentTimeMillis()
            while (System.currentTimeMillis() - start < 30_000) {
                if (input.available() > 0) {
                    val b = input.read()
                    if (b == 0xAA || b == 'O'.code) {
                        return
                    }
                }
                sleepMs(50)
            }
        } catch (_: Exception) {
            // Ignore ack failures in quick driver mode.
        }
    }

    private fun sleepMs(ms: Long) {
        try {
            Thread.sleep(ms)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }
}

private class BleSession(
    private val context: Context,
    private val device: BluetoothDevice
) {
    private var gatt: BluetoothGatt? = null
    private var writeCharacteristic: BluetoothGattCharacteristic? = null
    private var mtu: Int = 23

    private val connectedLatch = CountDownLatch(1)
    private val servicesLatch = CountDownLatch(1)
    private var mtuLatch: CountDownLatch? = null
    private var writeLatch: CountDownLatch? = null

    @Volatile
    private var connectError: String? = null
    @Volatile
    private var writeError: String? = null
    @Volatile
    private var lastWriteStatus: Int = BluetoothGatt.GATT_FAILURE

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                connectError = "BLE connect status=$status"
                connectedLatch.countDown()
                return
            }
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                g.discoverServices()
                connectedLatch.countDown()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                connectError = connectError ?: "BLE disconnected"
                connectedLatch.countDown()
                servicesLatch.countDown()
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                connectError = "BLE service discovery failed: $status"
                servicesLatch.countDown()
                return
            }
            writeCharacteristic = pickWriteCharacteristic(g)
            if (writeCharacteristic == null) {
                connectError = "No writable BLE characteristic found."
            }
            servicesLatch.countDown()
        }

        override fun onMtuChanged(gatt: BluetoothGatt, mtu: Int, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS && mtu > 23) {
                this@BleSession.mtu = mtu
            }
            mtuLatch?.countDown()
        }

        override fun onCharacteristicWrite(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            lastWriteStatus = status
            if (status != BluetoothGatt.GATT_SUCCESS) {
                writeError = "BLE write failed: $status"
            }
            writeLatch?.countDown()
        }
    }

    fun connect() {
        gatt = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE)
        } else {
            device.connectGatt(context, false, callback)
        }

        if (!connectedLatch.await(8, TimeUnit.SECONDS)) {
            throw IOException("BLE connect timeout.")
        }
        connectError?.let { throw IOException(it) }

        if (!servicesLatch.await(8, TimeUnit.SECONDS)) {
            throw IOException("BLE service discovery timeout.")
        }
        connectError?.let { throw IOException(it) }

        requestMtuBestEffort()
    }

    fun write(data: ByteArray) {
        val g = gatt ?: throw IOException("BLE session not connected.")
        val ch = writeCharacteristic ?: throw IOException("No writable BLE characteristic.")

        val payloadMax = max(20, mtu - 3)
        var offset = 0
        while (offset < data.size) {
            val end = minOf(data.size, offset + payloadMax)
            val part = data.copyOfRange(offset, end)
            writeChunk(g, ch, part)
            offset = end
        }
    }

    fun close() {
        try {
            gatt?.disconnect()
        } catch (_: Exception) {
        }
        try {
            gatt?.close()
        } catch (_: Exception) {
        }
        gatt = null
    }

    private fun requestMtuBestEffort() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) {
            return
        }
        val g = gatt ?: return
        mtuLatch = CountDownLatch(1)
        try {
            if (g.requestMtu(247)) {
                mtuLatch?.await(2, TimeUnit.SECONDS)
            }
        } catch (_: Exception) {
            // Keep default MTU.
        }
    }

    @Suppress("DEPRECATION")
    private fun writeChunk(
        gatt: BluetoothGatt,
        characteristic: BluetoothGattCharacteristic,
        payload: ByteArray
    ) {
        val props = characteristic.properties
        val canWrite = props and BluetoothGattCharacteristic.PROPERTY_WRITE != 0
        val canWriteNoResp = props and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0

        characteristic.writeType = if (canWriteNoResp && !canWrite) {
            BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        } else {
            BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        }
        characteristic.value = payload
        writeError = null
        lastWriteStatus = BluetoothGatt.GATT_FAILURE

        if (characteristic.writeType == BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) {
            writeLatch = CountDownLatch(1)
            val started = gatt.writeCharacteristic(characteristic)
            if (!started) {
                throw IOException("BLE write could not start.")
            }
            if (!writeLatch!!.await(4, TimeUnit.SECONDS)) {
                throw IOException("BLE write timeout.")
            }
            if (lastWriteStatus != BluetoothGatt.GATT_SUCCESS) {
                throw IOException(writeError ?: "BLE write failed.")
            }
        } else {
            val started = gatt.writeCharacteristic(characteristic)
            if (!started) {
                throw IOException("BLE write-no-response could not start.")
            }
            Thread.sleep(7)
        }
    }

    private fun pickWriteCharacteristic(gatt: BluetoothGatt): BluetoothGattCharacteristic? {
        val preferredService = gatt.getService(BLE_SERVICE_UUID)
        val preferredChar = preferredService?.characteristics?.firstOrNull { c ->
            val p = c.properties
            p and BluetoothGattCharacteristic.PROPERTY_WRITE != 0 ||
                p and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0
        }
        if (preferredChar != null) {
            return preferredChar
        }
        for (service in gatt.services.orEmpty()) {
            for (ch in service.characteristics.orEmpty()) {
                val p = ch.properties
                if (p and BluetoothGattCharacteristic.PROPERTY_WRITE != 0 ||
                    p and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0
                ) {
                    return ch
                }
            }
        }
        return null
    }
}
