# Fix Tronic pocket printer paper size on Windows (run as Administrator).
# Microsoft IPP Class Driver often defaults to A4 even when the Pi only
# advertises 48 mm media -- this registers a real form and writes it into
# the printer's default DEVMODE so Notepad/Word pick it up.
#
#   Set-ExecutionPolicy -Scope Process Bypass
#   Right-click PowerShell -> Run as administrator
#   .\fix-windows-paper.ps1
#   .\fix-windows-paper.ps1 -PrinterName "Tronic Mini Pocket Printer @ raspberrypi"

param(
    [string]$PrinterName = "",
    [string]$FormName = "Tronic 48x80 mm",
    [double]$WidthMm = 48.0,
    [double]$HeightMm = 80.0
)

$ErrorActionPreference = "Stop"

# --- require admin ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$id
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script as Administrator (right-click PowerShell -> Run as administrator)."
}

$sig = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public class TronicPaperFix {
  public const int DM_ORIENTATION = 0x0001;
  public const int DM_PAPERSIZE   = 0x0002;
  public const int DM_PAPERLENGTH = 0x0004;
  public const int DM_PAPERWIDTH  = 0x0008;
  public const int DM_FORMNAME   = 0x10000;
  public const short DMPAPER_USER  = 256;
  public const short DMORIENT_PORTRAIT = 1;

  public const int DM_OUT_BUFFER = 2;
  public const int DM_IN_BUFFER  = 8;
  public const int DM_IN_PROMPT  = 4;

  public const uint PRINTER_ACCESS_ADMINISTER = 0x00000004;
  public const uint PRINTER_ACCESS_USE = 0x00000008;
  public const int PRINTER_ALL_ACCESS = 0x000F000C;

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct SIZE { public int cx; public int cy; }

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct RECT { public int left; public int top; public int right; public int bottom; }

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct FORM_INFO_1 {
    public uint Flags;
    public string pName;
    public SIZE Size;
    public RECT ImageableArea;
  }

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct PRINTER_DEFAULTS {
    public IntPtr pDatatype;
    public IntPtr pDevMode;
    public uint DesiredAccess;
  }

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
    public short dmSpecVersion;
    public short dmDriverVersion;
    public short dmSize;
    public short dmDriverExtra;
    public int dmFields;
    public short dmOrientation;
    public short dmPaperSize;
    public short dmPaperLength;
    public short dmPaperWidth;
    public short dmScale;
    public short dmCopies;
    public short dmDefaultSource;
    public short dmPrintQuality;
    public short dmColor;
    public short dmDuplex;
    public short dmYResolution;
    public short dmTTOption;
    public short dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
    public short dmLogPixels;
    public int dmBitsPerPel;
    public int dmPelsWidth;
    public int dmPelsHeight;
    public int dmDisplayFlags;
    public int dmDisplayFrequency;
    public int dmICMMethod;
    public int dmICMIntent;
    public int dmMediaType;
    public int dmDitherType;
    public int dmReserved1;
    public int dmReserved2;
    public int dmPanningWidth;
    public int dmPanningHeight;
  }

  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool OpenPrinter(string pPrinterName, out IntPtr phPrinter, IntPtr pDefault);

  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool OpenPrinter(string pPrinterName, out IntPtr phPrinter, ref PRINTER_DEFAULTS pDefault);

  [DllImport("winspool.drv", SetLastError=true)]
  public static extern bool ClosePrinter(IntPtr hPrinter);

  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool AddForm(IntPtr hPrinter, int Level, ref FORM_INFO_1 pForm);

  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool DeleteForm(IntPtr hPrinter, string pFormName);

  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern int DocumentProperties(IntPtr hwnd, IntPtr hPrinter, string pDeviceName,
      IntPtr pDevModeOutput, IntPtr pDevModeInput, int fMode);

  [DllImport("winspool.drv", SetLastError=true)]
  public static extern bool GetPrinter(IntPtr hPrinter, int dwLevel, IntPtr pPrinter, int cbBuf, out int pcbNeeded);

  [DllImport("winspool.drv", SetLastError=true)]
  public static extern bool SetPrinter(IntPtr hPrinter, int Level, IntPtr pPrinter, int Command);

  static void EnsureFormOnHandle(IntPtr h, string formName, int widthUmm, int heightUmm) {
    DeleteForm(h, formName);
    FORM_INFO_1 fi = new FORM_INFO_1();
    fi.Flags = 0;
    fi.pName = formName;
    fi.Size = new SIZE { cx = widthUmm, cy = heightUmm };
    fi.ImageableArea = new RECT { left = 0, top = 0, right = widthUmm, bottom = heightUmm };
    if (!AddForm(h, 1, ref fi)) {
      int err = Marshal.GetLastWin32Error();
      // 1802 = form already exists on some builds after failed delete
      if (err != 1802 && err != 0) {
        throw new System.ComponentModel.Win32Exception(err, "AddForm failed");
      }
    }
  }

  public static void EnsureForm(string formName, int widthUmm, int heightUmm) {
    IntPtr h;
    string[] targets = new string[] {
      null,
      ",XcvMonitor Local Port",
      "\\\\" + Environment.MachineName
    };
    Exception last = null;
    foreach (string t in targets) {
      try {
        if (!OpenPrinter(t, out h, IntPtr.Zero)) {
          last = new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenPrinter " + t);
          continue;
        }
        try {
          EnsureFormOnHandle(h, formName, widthUmm, heightUmm);
          return;
        } finally {
          ClosePrinter(h);
        }
      } catch (Exception ex) {
        last = ex;
      }
    }
    if (last != null) throw last;
    throw new Exception("AddForm failed on all targets");
  }

  public static void SetPrinterDefaultPaper(string printerName, string formName, double widthMm, double heightMm) {
    IntPtr h;
    PRINTER_DEFAULTS pd = new PRINTER_DEFAULTS();
    pd.DesiredAccess = PRINTER_ACCESS_ADMINISTER | PRINTER_ACCESS_USE;
    if (!OpenPrinter(printerName, out h, ref pd)) {
      if (!OpenPrinter(printerName, out h, IntPtr.Zero)) {
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenPrinter(printer)");
      }
    }
    try {
      // Also attach form to this printer object
      try { EnsureFormOnHandle(h, formName, (int)Math.Round(widthMm * 1000), (int)Math.Round(heightMm * 1000)); }
      catch { /* form may already exist server-wide */ }

      int sizeNeeded = DocumentProperties(IntPtr.Zero, h, printerName, IntPtr.Zero, IntPtr.Zero, 0);
      if (sizeNeeded <= 0) {
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "DocumentProperties size");
      }
      IntPtr pDevMode = Marshal.AllocHGlobal(sizeNeeded);
      try {
        int rc = DocumentProperties(IntPtr.Zero, h, printerName, pDevMode, IntPtr.Zero, DM_OUT_BUFFER);
        if (rc < 0) {
          throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "DocumentProperties get");
        }

        DEVMODE dm = (DEVMODE)Marshal.PtrToStructure(pDevMode, typeof(DEVMODE));
        // Tenths of a millimeter
        short w = (short)Math.Round(widthMm * 10.0);
        short l = (short)Math.Round(heightMm * 10.0);
        dm.dmFields |= DM_ORIENTATION | DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH | DM_FORMNAME;
        dm.dmOrientation = DMORIENT_PORTRAIT;
        dm.dmPaperSize = DMPAPER_USER; // custom
        dm.dmPaperWidth = w;
        dm.dmPaperLength = l;
        dm.dmFormName = formName;
        Marshal.StructureToPtr(dm, pDevMode, false);

        rc = DocumentProperties(IntPtr.Zero, h, printerName, pDevMode, pDevMode, DM_IN_BUFFER | DM_OUT_BUFFER);
        if (rc < 0) {
          throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "DocumentProperties set");
        }

        // Write into printer defaults via PRINTER_INFO_2
        int needed;
        GetPrinter(h, 2, IntPtr.Zero, 0, out needed);
        IntPtr pInfo = Marshal.AllocHGlobal(needed);
        try {
          if (!GetPrinter(h, 2, pInfo, needed, out needed)) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "GetPrinter");
          }
          // PRINTER_INFO_2.pDevMode is at a fixed offset after several pointers/ints;
          // safer: use DocumentProperties result with SetPrinter by patching pointer.
          // Offset of pDevMode in PRINTER_INFO_2 (Unicode) is typically after 7 pointer-sized fields + some ints.
          // Use Marshal to read PRINTER_INFO_2 structure properly.
          PRINTER_INFO_2 info = (PRINTER_INFO_2)Marshal.PtrToStructure(pInfo, typeof(PRINTER_INFO_2));
          info.pDevMode = pDevMode;
          // Clear security descriptor to avoid SetPrinter failures
          info.pSecurityDescriptor = IntPtr.Zero;
          Marshal.StructureToPtr(info, pInfo, false);
          if (!SetPrinter(h, 2, pInfo, 0)) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "SetPrinter");
          }
        } finally {
          Marshal.FreeHGlobal(pInfo);
        }
      } finally {
        Marshal.FreeHGlobal(pDevMode);
      }
    } finally {
      ClosePrinter(h);
    }
  }

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct PRINTER_INFO_2 {
    public string pServerName;
    public string pPrinterName;
    public string pShareName;
    public string pPortName;
    public string pDriverName;
    public string pComment;
    public string pLocation;
    public IntPtr pDevMode;
    public string pSepFile;
    public string pPrintProcessor;
    public string pDatatype;
    public string pParameters;
    public IntPtr pSecurityDescriptor;
    public uint Attributes;
    public uint Priority;
    public uint DefaultPriority;
    public uint StartTime;
    public uint UntilTime;
    public uint Status;
    public uint cJobs;
    public uint AveragePPM;
  }
}
'@

if (-not ("TronicPaperFix" -as [type])) {
    Add-Type -TypeDefinition $sig -ErrorAction Stop
}

$widthUmm = [int][math]::Round($WidthMm * 1000)
$heightUmm = [int][math]::Round($HeightMm * 1000)

Write-Host "Creating paper form '$FormName' (${WidthMm} x ${HeightMm} mm)..."
[TronicPaperFix]::EnsureForm($FormName, $widthUmm, $heightUmm)
Write-Host "Form OK."

# Resolve printer name if not given
if ([string]::IsNullOrWhiteSpace($PrinterName)) {
    $candidates = @(Get-Printer | Where-Object {
        $_.Name -match 'Tronic|Pocket|raspberrypi|TronicPocket' -or
        $_.DriverName -match 'IPP Class'
    })
    if ($candidates.Count -eq 0) {
        Write-Host "Installed printers:"
        Get-Printer | Format-Table Name, DriverName, PortName -AutoSize
        throw "No Tronic/IPP printer found. Pass -PrinterName 'exact name from list'."
    }
    if ($candidates.Count -gt 1) {
        Write-Host "Multiple matches -- using the first. Pass -PrinterName to pick another:"
        $candidates | Format-Table Name, DriverName, PortName -AutoSize
    }
    $PrinterName = $candidates[0].Name
}

Write-Host "Setting default paper on printer: '$PrinterName' ..."

# IPP Class Driver ignores custom forms (A4/Letter only). Switch to Imagesetter.
$info = Get-Printer -Name $PrinterName
$targetDriver = "MS Publisher Imagesetter"
if ($info.DriverName -match 'IPP Class') {
    Write-Host "Driver is '$($info.DriverName)' (no custom 48 mm UI). Switching to '$targetDriver'..."
    $drv = Get-PrinterDriver -Name $targetDriver -ErrorAction SilentlyContinue
    if (-not $drv) {
        Add-PrinterDriver -Name $targetDriver
    }
    Set-Printer -Name $PrinterName -DriverName $targetDriver
    Write-Host "Driver switched."
}

[TronicPaperFix]::SetPrinterDefaultPaper($PrinterName, $FormName, $WidthMm, $HeightMm)

# Also try cmdlet if available
try {
    Get-PrintConfiguration -PrinterName $PrinterName | Out-Host
} catch {}

Write-Host ""
Write-Host "Done."
Write-Host "  Printer : $PrinterName"
Write-Host "  Driver  : $((Get-Printer -Name $PrinterName).DriverName)"
Write-Host ("  Paper   : {0} ({1}x{2} mm) set as default" -f $FormName, $WidthMm, $HeightMm)
Write-Host ""
Write-Host "Close Notepad/Word completely and reopen, then print."
Write-Host "Paper size in the dialog should be '$FormName' -- not A4."
