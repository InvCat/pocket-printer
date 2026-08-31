# Tronic "Mini Pocket Printer" (Lidl IAN 508705_2507) — protokoll

Visszafejtve a gyári APK-ból (`com.printer.lidloffice`, versionCode 177) és
**egy valódi készüléken leellenőrizve**.

## Az eszköz azonossága

| | |
|---|---|
| Doboz / márkanév | Tronic, Model 2890, IAN 508705_2507 |
| Bluetooth név | `Mini Pocket Printer` |
| Belső modell (`10 FF 20 F0`) | **A2Y** |
| Firmware | V1.06LY |
| Bootloader | V3.02 |
| Gyártó | Xiamen Print Future Technology Co., Ltd. |
| SDK | LuckPrinter (`com.luckprinter.sdk_new`) |
| SDK eszközosztály | `device.normal.MiniPocketPrinter extends DP_D1 extends BaseNormalDevice` |
| Akkumulátor | 18500 Li-Ion 3,7 V |

A klasszikus BT MAC `55:55:xx:xx:xx:xx`, a BLE MAC `5E:55:xx:xx:xx:xx` — a két
cím az utolsó öt bájtban azonos.

## Hardver-paraméterek (mérésekkel igazolva)

| | |
|---|---|
| Nyomtatási szélesség | **384 pixel** = 48 mm = **48 bájt/sor** |
| Felbontás | **203 dpi** — vonalzóval hitelesített cm-skálával ellenőrizve |
| Eszközosztály (BT CoD) | `0x800604` (Imaging / Printer) |

A `BaseDevice.getPrintWidth()` 384-et ad vissza (300 dpi-s modelleknél lenne
576). Egy kalibráló ábra kinyomtatása megerősítette: a 0. és a 383. pixel is a
papíron van, nincs levágás egyik oldalon sem, és nincs soronkénti csúszás.

## Kapcsolat

A készülék három egyenértékű csatornán fogadja ugyanezeket a bájtokat:

| Csatorna | Megjegyzés |
|---|---|
| **Klasszikus BT SPP (RFCOMM, 1. csatorna)** | Ez a megbízható út, ezt használja a mellékelt kliens. Párosítás nem szükséges volt. |
| BLE GATT | Hirdeti az `e7810a71-73ae-499d-8c15-faa9aef0c3f2` szolgáltatást. BlueZ 5.55 alatt a kapcsolódás `org.bluez.Error.NotAvailable` hibával elszáll (a BlueZ a BR/EDR rekordot cache-eli erre a címre). |
| USB-C | Soros portként jelentkezik be. |

## Parancsok

Minden lekérdezés ASCII vagy nyers bájt választ ad vissza ugyanazon a csatornán.

### Lekérdezések

| Bájtok | Jelentés | Válasz a tesztelt gépen |
|---|---|---|
| `10 FF 20 F0` | modell | `A2Y` |
| `10 FF 20 F1` | firmware | `V1.06LY` |
| `10 FF 20 F2` | sorozatszám | `P25410070395` |
| `10 FF 20 EF` | bootloader | `V3.02` |
| `10 FF 50 F1` | akkumulátor | `00 64` → 100 % (az utolsó bájt a százalék) |
| `10 FF 20 A0` | sebesség (`getSpeedLuck`) | — |
| `10 FF 40 00` | állapot-bitmaszk | `00` = kész |
| `10 FF 11 00` | sűrűség | `01 0C 10` |
| `10 FF 13 00` | auto-kikapcsolás | `14` = 20 perc |
| `10 FF B0 00` | időformátum (`getTimeFormat`) | — |
| `10 FF 70 00` | minden infó | `Mini Pocket Printer\|55:55:...\|5E:55:...\|V1.06LY\|P25410070395\|100` |

> A gyári app a `40`, `11`, `13`, `B0`, `70` lekérdezéseket egy záró `00`
> bájttal küldi (a fenti táblázat korábban ezt elhagyta); a `20`-családú
> parancsok 4 bájtosak, az alparanccsal záródnak (`… 20 F2`, `… 20 A0`).

Állapot-bitmaszk: `0x01` nyomtat · `0x02` fedél nyitva · `0x04` nincs papír ·
`0x08` gyenge akku · `0x10`/`0x40` túlmelegedett · `0x20` tölt.

### Beállítások

| Bájtok | Jelentés |
|---|---|
| `10 FF 10 00 n` | sűrűség: 0 = világos, 1 = közepes, 2 = sötét |
| `10 FF 12 hi lo` | auto-kikapcsolás percben (big-endian) |
| `10 FF 15 lo hi` | nyomtatási szélesség (`printerSetWidth`) |
| `1F 80 t p` | papírtípus (`doSetPaperType`); a címkés út `1F 80 01 20`-at küld |
| `1F 11 n` | pozíció automatikus igazítása (`adjustPositionAuto`) |
| `10 FF 04 00` | gyári visszaállítás (`setRecoveryLuck`) |

### Nyomtatás

A `BaseNormalDevice.printOnce()` pontos sorrendje — **ez a működő szekvencia**:

```
1.  10 FF F1 03                    enablePrinterLuck()  (a mode alapértéke 3)
2.  00 × 12                        printerWakeupLuck()
3.  1D 76 30 00 30 00 hLo hHi      ESC/POS raszterkép fejléc + képadat
4.  1B 4A 50                       printLineDotsLuck(endLineDot=80)
5.  10 FF F1 45                    stopPrintJobLuck()  → válasz: AA 0D 0A
```

A `0xAA` a "nyomtatási feladat kész" nyugta; erre érdemes megvárni (akár 30 s).

Címkés úton (`DP_D1.printTagOnce`) a 3. lépés előtt még `1F 80 01 20` megy, a
4. lépés helyett pedig `1D 0C` (form feed).

#### Raszterkép formátum

`1D 76 30 m xL xH yL yH` + képadat — a szabványos ESC/POS `GS v 0`.

- `m` = 0 (normál méret)
- `xL xH` = sor hossza bájtban, **little-endian** → `30 00` (48 bájt = 384 px)
- `yL yH` = sorok száma, little-endian
- képadat: 1 bit/pixel, **MSB = bal szélső pixel**, 1 = fekete

#### Tömörítés — figyelem

Az SDK-ban a `MiniPocketPrinter.initAfterConnect()` lekérdezi a modellt, és ha
az `A2Y`, bekapcsolja a `setCompress(true)`-t. Ekkor a gyári app **nem** a fenti
ESC/POS parancsot küldi, hanem egy tömörített változatot:

```
1F 10 wHi wLo hHi hLo len0 len1 len2 len3  +  Compress.codeESC(raszter)
```

(itt a méretek **big-endian**, a `len` a tömörített adat hossza 4 bájton; a
`codeESC` a `libPrinterNative.so`-ban van, `compressWay=0` esetén ez fut, a
`codeLihu` csak `compressWay=1`-nél).

**A gyakorlatban erre nincs szükség**: a firmware a tömörítetlen `1D 76 30`
parancsot is elfogadja és helyesen nyomtatja — ezt élőben leteszteltem. A
tömörítés csak sávszélességet spórol BLE-n. Így a `codeESC` visszafejtése
elmaradt.

## Nem dokumentált parancs — óvatosan

A parancstér vak feltérképezése közben a `10 FF 20 F5` `OK`-val válaszolt, és
**ezután a sorozatszám lekérdezés (`10 FF 20 F2`) üresen tér vissza**, és a
`10 FF 70` összefoglalóból is eltűnt a sorozatszám mező (eredetileg
`P25410070395`). Ez a parancs nem szerepel a gyári SDK-ban (az APK mind a
három dexében csak a `10 FF 20` + `EF/A0/F0/F1/F2` alparancsok fordulnak elő),
tehát gyári/szerviz funkció.

**Ne küldj ismeretlen `10 FF 20 xx` parancsokat** — a tartomány írási
műveleteket is tartalmaz, némán is.

#### A teljes parancskészlet visszafejtve (2. független átvizsgálás)

A `base.apk` mindhárom dexét androguarddal újra végigelemezve **az app által
küldött összes `10 FF …` parancs** (a `fill-array-data` payloadokból kinyerve):

```
10 FF 20 A0   sebesség        10 FF 11 00   sűrűség (get)
10 FF 20 EF   bootloader      10 FF 13 00   auto-kikapcsolás (get)
10 FF 20 F0   modell          10 FF B0 00   időformátum
10 FF 20 F1   firmware        10 FF 70 00   minden infó
10 FF 20 F2   sorozatszám     10 FF 04 00   gyári visszaállítás
10 FF 40 00   állapot         10 FF F1 45   nyomtatás vége (stop)
10 FF 50 F1   akku            10 FF E0 AA AA 00  firmware-frissítés belépő
```

(a `FE 45` / `FE 01` és a `E0 AA AA 00` a más családok — AiYin/YX/HanYin —
osztályaiban van, nem az A2Y-hoz.) A `MiniPocketPrinter` osztály egyedül az
`initAfterConnect`-et írja felül (a `setCompress(true)` kapcsoló), a `DP_D1`
pedig csak a `printTagOnce`-t adja hozzá — **egyik sem tartalmaz
sorozatszám-írás parancsot.**

Következtetés: a fogyasztói appban **nincs semmilyen SN-író opkód**. A törlést
végző `10 FF 20 F5` gyári/szerviz parancs, aminek az író párja (feltehetően
egy szomszédos `10 FF 20 xx`) nem szerepel az APK-ban, így nem
rekonstruálható belőle. A visszaírás csak a gyártó szerviz-eszközével
(vagy firmware-szintű hozzáféréssel) lehetséges — szoftveresen, ebből az
app-ból nem. Ezt a második, független átvizsgálás megerősítette.

#### Az egyetlen fennmaradó — de nem járható — elméleti út

A `10 FF E0 AA AA 00` az SDK **firmware-frissítő belépő parancsa**. Elvben egy
reflash a sorozatszámot tároló nem-felejtő régiót is felülírhatná, de ez a mi
esetünkben **nem használható**:

- A frissítő logika csak az `UpdatePrinterESC` / `YXFirmwareUpdater` /
  `HYFirmwareUpdater` osztályokban van (AiYin / YX / HanYin családok); az A2Y
  (`MiniPocketPrinter`) ághoz **nincs** frissítő kód és **nincs** firmware-kép
  az APK-ban.
- A per-unit sorozatszám jellemzően a firmware-képtől külön, kalibrációs/OTP
  régióban van — egy sima reflash nem is írná felül, viszont téglásíthatja a
  készüléket.

Ezt az utat tehát **nem próbáltam meg és nem is ajánlott** — csak a
teljesség kedvéért dokumentálva.

### Visszaállítási kísérletek — mindegyik eredménytelen

Az alábbiakat végigpróbáltuk, mindegyiket teljes olvasási backuppal
előtte és utána. Egyik sem változtatott semmit:

| Próba | Eredmény |
|---|---|
| `10 FF 20 F5` + ASCII | nincs válasz, nincs változás |
| `10 FF 20 F5` + hossz + ASCII | nincs válasz, nincs változás |
| `10 FF 20 F5` + ASCII + `00` | nincs válasz, nincs változás |
| `10 FF 20 F5`, majd külön csomagban az adat | nincs válasz, nincs változás |
| `10 FF 04 00` (`setRecoveryLuck`, gyári visszaállítás) | `OK`, de a sorozatszám nem tért vissza |

Két további megfigyelés:

- A csupasz `10 FF 20 F5` **második alkalommal már nem ad `OK`-t** — egyszer
  lefutó parancs, nem ismételhető meg és nem fordítható vissza.
- A `10 FF 04 00` gyári visszaállítás **nem teljes reset**: a sűrűség és az
  auto-kikapcsolási idő is változatlan maradt utána. Közvetlenül a parancs
  után a `10 FF 70` néhány másodpercig üresen válaszol, majd magától
  helyreáll — ez csak a reset utáni ébredés, nem hiba.

### A törlés véglegesnek tűnik

A készülék **öntesztlapján** (dupla kattintás a bekapcsológombra) is üres az
`S/N:` mező. Tehát az érték a nem-felejtő tárból törlődött, nem csak a
lekérdezés-válasz sérült. Szoftveres visszaírásra a fentiek alapján nincs
ismert mód.

### Mi a gyakorlati következménye — semmi

Az elveszett érték: **`P25410070395`** (a törlés előtti olvasásból).

A gyári app a sorozatszámot kizárólag a `DeviceInfoActivity`-ben használja,
azaz az "Eszköz információ" képernyőn jeleníti meg. Nincs rá építve
licencelés, szerveroldali ellenőrzés vagy funkciókorlátozás — az egyetlen
látható hatás, hogy ott és az öntesztlapon üres marad.

A készülék minden más funkciója hibátlan: modell, firmware, bootloader,
mindkét MAC-cím, akku, állapot és a nyomtatás is (a gyári visszaállítás után
is leellenőrizve).

## Visszafejtési módszertan (reprodukálható)

A fenti eredmények így állnak elő újra, tisztán az APK-ból (fizikai eszköz
nélkül — a 2. átvizsgálás konténerben, Bluetooth-vezérlő nélkül készült, ezért
élő küldés nem történt, csak statikus elemzés):

1. **A `.apks` bundle** három részből áll: `base.apk` (a kód + a legtöbb
   erőforrás), `split_config.arm64_v8a.apk` (natív libek), és a
   `split_config.xxhdpi.apk` (képek). A kód mind a `base.apk` három dexében
   van: `classes.dex`, `classes2.dex`, `classes3.dex`.

   ```bash
   unzip -o "Pocket Printer ….apks" base.apk split_config.arm64_v8a.apk
   unzip -o base.apk classes.dex classes2.dex classes3.dex
   ```

2. **Dex-elemzés androguarddal** (`pip install androguard`, 4.1.4). Az összes
   `10 FF …` parancsot úgy nyertük ki, hogy a metódusok `fill-array-data`
   payloadjait végignéztük — a parancsbájtok konstans byte-tömbként vannak
   beégetve:

   ```python
   from androguard.misc import AnalyzeDex
   for f in ["classes.dex","classes2.dex","classes3.dex"]:
       _, _, dx = AnalyzeDex(f)
       for ma in dx.get_methods():
           for ins in ma.get_method().get_instructions():
               if ins.get_name() == "fill-array-data-payload":
                   print(ins.get_output())   # tartalmazza a nyers bájtokat
   ```

   Fontos buktató: ne nevezd a szkriptet `enum.py`-nak — elfedi a stdlib
   `enum` modult, és az androguard körkörös importtal elszáll.

3. **A parancsok osztály/metódus szerinti visszakeresése**: a
   `dx.get_methods()` metódusnevein szűrve (`printerSNLuck`,
   `setRecoveryLuck`, `printerModelLuck`, …) derül ki, melyik opkód mit
   csinál. Az A2Y ág osztályhierarchiája:
   `MiniPocketPrinter → DP_D1 → BaseNormalDevice → BaseDevice`. A
   parancsokat mind a `BaseNormalDevice` építi; a `MiniPocketPrinter` csak az
   `initAfterConnect`-et (compress kapcsoló az `A2Y` modellre), a `DP_D1` csak
   a `printTagOnce`-t adja hozzá.

4. **A natív lib** (`lib/arm64-v8a/libPrinterNative.so`, ~85 KB) kizárólag a
   tömörítést tartalmazza (`codeESC` / `codeLihu`, a
   `com/print/base/utils/NativeUtil`-ból hívva). Sorozatszámmal vagy
   készülék-paranccsal kapcsolatos szimbólum **nincs** benne — a
   protokoll-logika teljes egésze a Java/dex oldalon van, a firmware pedig a
   készüléken. (A `libopencv_java4.so` és a `libc++_shared.so` a
   képfeldolgozáshoz kell, nem releváns.)

## Források

Ugyanennek az SDK-nak más eszközosztályai (a mienktől eltérő geometriával és
enable/stop parancsokkal), hasznos háttérként:

- [atctwo — L13 (Lidl Silvercrest, 96 px címkenyomtató)](https://github.com/atctwo/reverse-engineering/tree/main/l13-thermal-printer)
- [0xMH — fichero-printer (AiYin D11s)](https://github.com/0xMH/fichero-printer)
