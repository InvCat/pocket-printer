package com.tronic.pocketprintservice

import android.content.Context
import android.print.PrintAttributes
import android.print.PrinterCapabilitiesInfo
import android.print.PrinterId
import android.print.PrinterInfo
import android.printservice.PrintJob
import android.printservice.PrintService
import android.printservice.PrinterDiscoverySession

class TronicPrintService : PrintService() {
    override fun onCreatePrinterDiscoverySession(): PrinterDiscoverySession {
        return TronicDiscoverySession(this)
    }

    override fun onPrintJobQueued(printJob: PrintJob) {
        Thread {
            val address = PrinterConfig.getPrinterAddress(this)
            if (address.isNullOrBlank()) {
                printJob.fail("No printer selected. Open Tronic Print Service app and choose a paired device.")
                return@Thread
            }

            val data = printJob.document?.data
            if (data == null) {
                printJob.fail("No printable document payload.")
                return@Thread
            }

            try {
                if (printJob.isCancelled) {
                    printJob.cancel()
                    return@Thread
                }
                TronicBluetoothPrinter(this, address).printPdf(data)
                if (printJob.isCancelled) {
                    printJob.cancel()
                } else {
                    printJob.complete()
                }
            } catch (t: Throwable) {
                printJob.fail(t.message ?: "Print failed.")
            } finally {
                try {
                    data.close()
                } catch (_: Exception) {
                }
            }
        }.start()
    }

    override fun onRequestCancelPrintJob(printJob: PrintJob) {
        printJob.cancel()
    }
}

private class TronicDiscoverySession(
    private val service: PrintService
) : PrinterDiscoverySession() {

    override fun onStartPrinterDiscovery(priorityList: MutableList<PrinterId>) {
        publishPrinter(service)
    }

    override fun onStopPrinterDiscovery() {
        // No active discovery process; only paired/prior selected target is exposed.
    }

    override fun onValidatePrinters(printerIds: MutableList<PrinterId>) {
        publishPrinter(service)
    }

    override fun onStartPrinterStateTracking(printerId: PrinterId) {
        publishPrinter(service)
    }

    override fun onStopPrinterStateTracking(printerId: PrinterId) {
        // No dynamic tracking for quick implementation.
    }

    override fun onDestroy() {
        // Nothing to clean up.
    }

    private fun publishPrinter(context: Context) {
        val printerId = service.generatePrinterId("tronic-mini-pocket-printer")
        val hasTarget = !PrinterConfig.getPrinterAddress(context).isNullOrBlank()
        val description = if (hasTarget) {
            "Selected: ${PrinterConfig.getPrinterLabel(context)}"
        } else {
            "Open Tronic Print Service app and select paired printer."
        }
        val status = if (hasTarget) PrinterInfo.STATUS_IDLE else PrinterInfo.STATUS_UNAVAILABLE

        val media48 = PrintAttributes.MediaSize(
            "TRONIC_48MM",
            "48mm Roll",
            1890,
            7874
        )
        val caps = PrinterCapabilitiesInfo.Builder(printerId)
            .addMediaSize(media48, true)
            .addResolution(PrintAttributes.Resolution("R203", "203dpi", 203, 203), true)
            .setColorModes(
                PrintAttributes.COLOR_MODE_MONOCHROME,
                PrintAttributes.COLOR_MODE_MONOCHROME
            )
            .setMinMargins(PrintAttributes.Margins.NO_MARGINS)
            .build()

        val info = PrinterInfo.Builder(printerId, "Tronic Mini Pocket Printer", status)
            .setDescription(description)
            .setCapabilities(caps)
            .build()

        addPrinters(listOf(info))
    }
}
