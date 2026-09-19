# X32 OSC ground truth: FX rack, inserts, routing, mute groups/DCA, scenes/shows, -stat/-prefs/-action, panic

Research date: 2026-09-19. Target firmware: X32/M32 FW 4.x (doc version 4.06-09).

## 0. Sources, conventions, and how to read this file

### 0.1 Sources (priority order)

| Tag | Source | Notes |
|---|---|---|
| **[EMU]** | `pmaillot/X32-Behringer` `X32.c` (emulator, `XVERSION "4.06"`) + its per-block headers `X32Fx.h`, `X32Bus.h`, `X32Mtx.h`, `X32Dca.h`, `X32CfgMain.h`, `X32PrefStat.h`, `X32Show.h`, `X32Output.h`, `X32Misc.h`, `X32Channel.h`, `X32Libs.h` | Raw files fetched from `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/<file>`. Line numbers below refer to these master copies. |
| **[SCN]** | same repo: `X32SetScene.h`, `SetSceneParse.c`, `fxparse.c`, `fxparse1.c`, `fxparse5.c` (scene-file -> OSC converter) | Independent second copy of every enum table; used to cross-check the emulator. |
| **[TOOLS]** | same repo: `X32GEQ2cpy.c`, `X32CopyFX.c`, `GetSceneName.c`, `X32GetLib.c`, `X32SetLib.c`, `X32DeskSave.c`, `X32ds_node.h` | Real-desk usage of GEQ params, `/load`, `/save`, `/-show/prepos/current`, `/-prefs/show_control`. |
| **[DOC]** | Patrick-Gilles Maillot, *Unofficial X32/M32 OSC Remote Protocol*, "version 4.06 - 09 (Mar 17, 2022)" (PDF mirrored at `https://x32ram.com/wp-content/uploads/download-files/X32-OSC.pdf`; text extracted with `pdftotext -layout`; the 4.02 mirror at `https://tostibroeders.nl/wp-content/uploads/2020/02/X32-OSC.pdf` and the 2015 v0.0981 at `static.makemusic.tn` were used as fallbacks). Maillot's site `https://sites.google.com/site/patrickmaillot/x32` links the current PDF on Google Drive (`https://drive.google.com/file/d/1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz/view`). | Page numbers quoted as "DOC p.NN" are the printed page footers. |
| **[WEB]** | X32 user manual excerpts via ManualsLib (p.29, Mute system), drewbrashler.com "X32 Output Tap ... +M", behringer.world / soundforums insert threads | Only used to corroborate behaviour not visible in code. |

### 0.2 Value/type conventions used everywhere below

* OSC types: `i` = int32, `f` = float32 (always 0.0..1.0 on the wire for continuous parameters), `s` = string.
* `enum` = int index into a list. **The index is the position in the list, 0-based, exactly in the order printed.** In `/node` replies and scene files the enum is printed as its *token* (e.g. `POST`, `FX5L`).
* `%int` (bitmask) = int on the wire; printed in `/node` replies / scene files as `%` followed by the bits **MSB first** (leftmost char = highest bit). Emulator printer:
  ```c
  // X32.c:1284
  char* Sbitmp(int iin, int len) {
      int i, j;
      j = 0;
      snode_str[j++] = ' ';
      snode_str[j++] = '%';
      for (i = len - 1; i > -1; i--) {
          snode_str[j++] = ((iin & (1 << i)) ? '1' : '0');
      }
      snode_str[j] = 0;
      return snode_str;
  }
  ```
  Bit 0 is always the *first* item of the list (channel 1, DCA 1, mute group 1, ...). SOURCE: DOC p.46-48 (all bitmap descriptions "bit 0: channel 1 ... bit 31: channel 32").
* `linf [min, max, step]`: `f = (x - min) / (max - min)`; `x = min + f*(max-min)`. `logf [min, max, N]`: `f = ln(x/min) / ln(max/min)`; `x = min * (max/min)^f`. Verbatim from the emulator:
  ```c
  // X32.c:1306  (string -> float, used when parsing "/" or node text)
  fval = (fval - xmin) / lmaxmin;            // RLinf: lmaxmin = xmax - xmin
  // X32.c:1330
  fval = log(fval / xmin) / lmaxmin;        // RLogf: lmaxmin = log(xmax / xmin)
  // X32.c:1254  (float -> string for node replies)
  sprintf(snode_str, formt, fmin + (fmax - fmin) * fin);                 // Slinf
  sprintf(snode_str, formt, exp(fin * log(fmax / fmin) + log(fmin)));    // Slogf
  ```
* `/node ,s <path-without-leading-slash>` returns one text line: `node~~~~,s~~/<path> <v1> <v2> ...\n` (the reply address is `node` with no leading slash, terminated by `\n`). SOURCE: X32.c `function_node()` (3782 ff.), DOC p.52 "Note: The OSC data resulting from a /node command does not comply to OSC standard".
* `/xremote` (renew every <10 s) makes the desk push every change as a normal OSC message. SOURCE: DOC p.7-8.

---

## 1. FX rack (`/fx/1..8`)

### 1.1 Addresses

| Address | Type | Range / enum | Notes |
|---|---|---|---|
| `/fx/[1-4]/type` | `i` (enum) | 0..60, table 1.2 | Send/insert-capable slots |
| `/fx/[1-4]/source/l`, `/fx/[1-4]/source/r` | `i` (enum) | 0..17, table 1.4 | Only slots 1-4 have `source` |
| `/fx/[5-8]/type` | `i` (enum) | 0..33, table 1.3 | **Insert-only** slots: no `/source`, no FX return strip |
| `/fx/[1-8]/par/[01-64]` | `f` or `i` per parameter | see 1.5 | Two-digit zero-padded index; unused params exist but are ignored |

SOURCE: X32Fx.h:259-332 (`Xfx1[]`: `/fx/1/type {E32} Sfxtyp1`, `/fx/1/source/l|r {E32} Sfxsrc`, `/fx/1/par/01..64 {FX32}`); X32Fx.h:563-633 (`Xfx5[]`: `/fx/5/type {E32} Sfxtyp2`, `/fx/5/par/01..64`, **no source entries**); DOC p.39.

FX returns for slots 1-4 are the `/fxrtn/01..08` strips, named Fx1L, Fx1R, Fx2L, Fx2R, Fx3L, Fx3R, Fx4L, Fx4R (X32.c:410 `Sselidx[]` entries 40-47; DOC p.30 `/fxrtn/[01...08]/...`, p.61 `/-stat/selidx` "40-47: FxRtn 1-8"); slots 5-8 have no return strip (there is no `/fxrtn/09+`, and `/fx/5-8` have no `/source`), i.e. they are insert-only. A slot 1-4 effect is "send-based" when `source/l|r` = MIX n / M/C and the FX return strip is used; it is an insert when `source/l|r = INS (0)` and some strip's `insert/sel` points at `FXnL`/`FXnR`.

### 1.2 `/fx/[1-4]/type` enum (index -> token) — 61 entries

```
 0 HALL    1 AMBI    2 RPLT    3 ROOM    4 CHAM    5 PLAT    6 VREV    7 VRM
 8 GATE    9 RVRS   10 DLY    11 3TAP   12 4TAP   13 CRS    14 FLNG   15 PHAS
16 DIMC   17 FILT   18 ROTA   19 PAN    20 SUB    21 D/RV   22 CR/R   23 FL/R
24 D/CR   25 D/FL   26 MODD   27 GEQ2   28 GEQ    29 TEQ2   30 TEQ    31 DES2
32 DES    33 P1A    34 P1A2   35 PQ5    36 PQ5S   37 WAVD   38 LIM    39 CMB
40 CMB2   41 FAC    42 FAC1M  43 FAC2   44 LEC    45 LEC2   46 ULC    47 ULC2
48 ENH2   49 ENH    50 EXC2   51 EXC    52 IMG    53 EDI    54 SON    55 AMP2
56 AMP    57 DRV2   58 DRV    59 PIT2   60 PIT
```
Long names (DOC appendix p.166-167 "Effects enums, names and preset names table" — VERIFIED-CORRECTED: page is 166-167, not ~152; the `%xxxxxx` code is the `/-libs/fx/NNN/flags` type value): HALL Hall Reverb, AMBI Ambiance, RPLT Rich Plate Reverb, ROOM Room Reverb, CHAM Chamber Reverb, PLAT Plate Reverb, VREV Vintage Reverb, VRM Vintage Room, GATE Gated Reverb, RVRS Reverse Reverb, DLY Stereo Delay, 3TAP 3-Tap Delay, 4TAP Rhythm Delay, CRS Stereo Chorus, FLNG Stereo Flanger, PHAS Stereo Phaser, DIMC Dimension-C, FILT Mood Filter, ROTA Rotary Speaker, PAN Tremolo/Panner, SUB Suboctaver, D/RV Delay+Chamber, CR/R Chorus+Chamber, FL/R Flanger+Chamber, D/CR Delay+Chorus, D/FL Delay+Flanger, MODD Modulation Delay, GEQ2 Dual Graphic EQ, GEQ Stereo Graphic EQ, TEQ2 Dual TrueEQ, TEQ Stereo TrueEQ, DES2 Dual DeEsser, DES Stereo DeEsser, P1A Stereo Xtec EQ1, P1A2 Dual Xtec EQ1, PQ5 Stereo Xtec EQ5, PQ5S Dual Xtec EQ5, WAVD Wave Designer, LIM Precision Limiter, CMB Combinator, CMB2 Dual Combinator, FAC Fair Comp, FAC1M M/S Fair Comp, FAC2 Dual Fair Comp, LEC Leisure Comp, LEC2 Dual Leisure Comp, ULC Ultimo Comp, ULC2 Dual Ultimo Comp, ENH2 Dual Enhancer, ENH Stereo Enhancer, EXC2 Dual Exciter, EXC Stereo Exciter, IMG Stereo Imager, EDI Edison EX1, SON Sound Maxer, AMP2 Dual Guitar Amp, AMP Stereo Guitar Amp, DRV2 Dual Tube Stage, DRV Stereo Tube Stage, PIT2 Dual Pitch Shifter, PIT Stereo Pitch.

SOURCES (4 independent copies agree on this order):
* DOC p.39: `/fx/[1...4]/type enum int [0...60] representing {HALL, AMBI, RPLT, ROOM, CHAM, PLAT, VREV, VRM, GATE, RVRS, DLY, 3TAP, 4TAP, CRS, FLNG, PHAS, DIMC, FILT, ROTA, PAN, SUB, D/RV, CR/R, FL/R, D/CR, D/FL, MODD, GEQ2, GEQ, TEQ2, TEQ, DES2, DES, P1A, P1A2, PQ5, PQ5S, WAVD, LIM, CMB, CMB2, FAC, FAC1M, FAC2, LEC, LEC2, ULC, ULC2, ENH2, ENH, EXC2, EXC, IMG, EDI, SON, AMP2, AMP, DRV2, DRV, PIT2, PIT}`
* X32SetScene.h:5145 `char* Xfxtyp4[] = {"HALL", "AMBI", "RPLT", "ROOM", "CHAM", "PLAT", "VREV", "VRM", "GATE", "RVRS", "DLY", "3TAP", "4TAP", "CRS", "FLNG", "PHAS", "DIMC", "FILT", "ROTA", "PAN", "SUB", "D/RV", "CR/R", "FL/R", "D/CR", "D/FL", "MODD", "GEQ2", "GEQ", "TEQ2", "TEQ", "DES2", "DES", "P1A", "P1A2", "PQ5", "PQ5S", "WAVD", "LIM", "CMB", "CMB2", "FAC", "FAC1M", "FAC2", "LEC", "LEC2", "ULC", "ULC2", "ENH2", "ENH", "EXC2", "EXC", "IMG", "EDI", "SON", "AMP2", "AMP", "DRV2", "DRV", "PIT2", "PIT"};`
* fxparse1.c:74 `enum fxtyp4 {HALL = 0, AMBI, ... GEQ2, GEQ, TEQ2, TEQ, DES2, DES, P1A, P1A2, PQ5, PQ5S, WAVD, LIM, CMB, CMB2, ...}`; X32CopyFX.c:34 `FXref[]` (same order); X32Fx.h:155-218 `Sflookup[]` comments (same order).
* VERIFIED: re-read X32.c:355-374, X32SetScene.h:5145-5158, fxparse1.c:74-80, fxparse5.c:16-19, X32CopyFX.c:34, DOC p.39-40 + appendix p.166-167 (2026-09-19). All 61 tokens and indices above match; the swapped-pair quirk in the emulator string array is real (verified index by index).
* **Discrepancy (emulator bug, ignore):** X32.c:355 `Sfxtyp1[]` string array has adjacent pairs swapped at indices 28-30 (`" TEQ2", " GEQ", " TEQ"`), 33-36 (`" P1A2", " P1A", " PQ5S", " PQ5"`), 39-40 (`" CMB2", " CMB"`), 41-47 (`" FAC2", " FAC1M", " FAC", " LEC2", " LEC", " ULC2", " ULC"`). Its own `enum Sfxtyp1` (X32.c:363) even disagrees with that string array (`_1_CMB, _1_CMB2`). The doc + scene parser + CopyFX + fxparse are the truth.

### 1.3 `/fx/[5-8]/type` enum (index -> token) — 34 entries

```
 0 GEQ2   1 GEQ    2 TEQ2   3 TEQ    4 DES2   5 DES    6 P1A    7 P1A2   8 PQ5    9 PQ5S
10 WAVD  11 LIM   12 FAC   13 FAC1M 14 FAC2  15 LEC   16 LEC2  17 ULC   18 ULC2  19 ENH2
20 ENH   21 EXC2  22 EXC   23 IMG   24 EDI   25 SON   26 AMP2  27 AMP   28 DRV2  29 DRV
30 PHAS  31 FILT  32 PAN   33 SUB
```
SOURCES: DOC p.39 `/fx/[5...8]/type enum int [0...33] representing {GEQ2, GEQ, TEQ2, TEQ, DES2, DES, P1A, P1A2, PQ5, PQ5S, WAVD, LIM, FAC, FAC1M, FAC2, LEC, LEC2, ULC, ULC2, ENH2, ENH, EXC2, EXC, IMG, EDI, SON, AMP2, AMP, DRV2, DRV, PHAS, FILT, PAN, SUB}`; X32SetScene.h:5154 `Xfxtyp5[]`; fxparse5.c:17 `enum fxtyp5 {GEQ2 = 0, GEQ, TEQ2, TEQ, ...}`; X32.c:371 `Sfxtyp2[]` (emulator again has `" GEQ2", " TEQ2", " GEQ", " TEQ"` swapped at 1-2 — ignore). VERIFIED: X32SetScene.h:5154-5157 and fxparse5.c:16-19 both give exactly the 34-entry order printed above; DOC p.40 identical.

`/-libs/fx/NNN/flags` type codes (DOC appendix "FX5...FX8" table, clean rendering): GEQ2 `%011000` Dual Graphic EQ, GEQ `%010111` Stereo Graphic EQ, TEQ2 `%011010` Dual TrueEQ, TEQ `%011001` Stereo TrueEQ, DES2 `%101011`, DES `%101010`, P1A `%101100`, P1A2 `%101101`, PQ5 `%101110`, PQ5S `%101111`, WAVD `%011101`, LIM `%011110`, FAC `%110000`, FAC1M `%110001`, FAC2 `%110010`, LEC `%110011`, LEC2 `%110100`, ULC `%110101`, ULC2 `%110110`, ENH2 `%100000`, ENH `%011111`, EXC2 `%100010`, EXC `%100001`, IMG `%100111`, EDI `%111000`, SON `%110111`, AMP2 `%100100`, AMP `%100011`, DRV2 `%100110`, DRV `%100101`, PHAS `%011011`, FILT `%101001`, PAN `%101000`, SUB `%111001`. DOC p.48 warns "Note: int values do not match with FX enums!". VERIFIED: all 34 `%xxxxxx` codes above re-read from DOC appendix p.167 (FX5...FX8 block); the FX1...FX4 block (p.166-167) carries the same codes for the shared effects.

### 1.4 `/fx/[1-4]/source/l|r` enum — 18 entries

```
0 INS  1 MIX1  2 MIX2  3 MIX3  4 MIX4  5 MIX5  6 MIX6  7 MIX7  8 MIX8  9 MIX9
10 MIX10 11 MIX11 12 MIX12 13 MIX13 14 MIX14 15 MIX15 16 MIX16 17 M/C
```
`INS` = the slot side is fed by an insert point (see §3). `MIXn` = fed by mix bus n (send effect). `M/C` = mono/centre bus.
SOURCES: X32.c:381 `char* Sfxsrc[] = {" INS", " MIX1", ... " MIX16", " M/C", ""};`; X32SetScene.h:5112 `Xfxsrc4[]`; DOC p.39 `enum int with value [0...17] representing {INS, MIX1 ... MIX16, M/C}`. VERIFIED: all three re-read (X32.c:381-383, X32SetScene.h:5112-5113, DOC p.40 lines for source/l and source/r).

### 1.5 Parameter typing per effect (`f` vs `i`)

The emulator resolves the OSC type of `/fx/N/par/PP` from the slot's current type with these lookup strings (`f` = float 0..1, `i` = int/enum, `-` = unused). Verbatim, X32Fx.h:155-218 (`Sflookup[]`, indexed by the **fx 1-4** type) and X32Fx.h:220-256 (`Sflookup2[]`, indexed by the **fx 5-8** type):

```c
char* Sflookup[] = {
 "ffffffffffff----------------------------------------------------", /* HALL */
 "ffffffffff------------------------------------------------------", /* AMBI */
 "ffffffffffffffff------------------------------------------------", /* RPLT */
 "ffffffffffffffff------------------------------------------------", /* ROOM */
 "ffffffffffffffff------------------------------------------------", /* CHAM */
 "ffffffffffff----------------------------------------------------", /* PLAT */
 "fffiifffff------------------------------------------------------", /* VREV */
 "ffffffffffffi---------------------------------------------------", /* VRM */
 "ffffffffff------------------------------------------------------", /* GATE */
 "fffffffff-------------------------------------------------------", /* RVRS */
 "ffiiifffffff----------------------------------------------------", /* DLY */
 "ffffffiffiffiii-------------------------------------------------", /* _3TAP */
 "ffffffifififiii-------------------------------------------------", /* _4TAP */
 "fffffffffff-----------------------------------------------------", /* CRS */
 "ffffffffffff----------------------------------------------------", /* FLNG */
 "ffffffffffff----------------------------------------------------", /* PHAS */
 "iiiiiii---------------------------------------------------------", /* DIMC */
 "ffffififffffii--------------------------------------------------", /* FILT */
 "ffffffii--------------------------------------------------------", /* ROTA */
 "fffffffff-------------------------------------------------------", /* PAN */
 "iifffiifff------------------------------------------------------", /* SUB */
 "fiffffffffff----------------------------------------------------", /* D_RV */
 "ffffffffffff----------------------------------------------------", /* CR_R */
 "ffffffffffff----------------------------------------------------", /* FL_R */
 "fiffffffffff----------------------------------------------------", /* D_CR */
 "fiffffffffff----------------------------------------------------", /* D_FL */
 "fifffffiiffff---------------------------------------------------", /* MODD */
 "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", /* GEQ2 */
 "ffffffffffffffffffffffffffffffff--------------------------------", /* GEQ */
 "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", /* TEQ2 */
 "ffffffffffffffffffffffffffffffff--------------------------------", /* TEQ */
 "ffffii----------------------------------------------------------", /* DES2 */
 "ffffii----------------------------------------------------------", /* DES */
 "iffifffifii-----------------------------------------------------", /* P1A */
 "iffifffifiiiffifffifii------------------------------------------", /* P1A2 */
 "ififififi-------------------------------------------------------", /* PQ5 */
 "ififififiififififi----------------------------------------------", /* PQ5S */
 "ffffff----------------------------------------------------------", /* WAVD */
 "ffffffii--------------------------------------------------------", /* LIM */
 "iifffififiiffffiffiffiffiffii-----------------------------------", /* CMB */
 "iifffififiiffffiffiffiffiffiiiifffififiiffffiffiffiffiffii------", /* CMB2 */
 "iffffff---------------------------------------------------------", /* FAC */
 "iffffffiffffff--------------------------------------------------", /* FAC1M */
 "iffffffiffffff--------------------------------------------------", /* FAC2 */
 "iffif-----------------------------------------------------------", /* LEC */
 "iffififfif------------------------------------------------------", /* LEC2 */
 "iffffi----------------------------------------------------------", /* ULC */
 "iffffiiffffi----------------------------------------------------", /* ULC2 */
 "ffffffffiffffffffi----------------------------------------------", /* ENH2 */
 "ffffffffi-------------------------------------------------------", /* ENH */
 "ffffffiffffffi--------------------------------------------------", /* EXC2 */
 "ffffffi---------------------------------------------------------", /* EXC */
 "fffffff---------------------------------------------------------", /* IMG */
 "iiifffff--------------------------------------------------------", /* EDI */
 "ifffifff--------------------------------------------------------", /* SON */
 "ffffffffiffffffffi----------------------------------------------", /* AMP2 */
 "ffffffffi-------------------------------------------------------", /* AMP */
 "ffffffffffffffffffff--------------------------------------------", /* DRV2 */
 "ffffffffff------------------------------------------------------", /* DRV */
 "ffffffffffff----------------------------------------------------", /* PIT2 */
 "ffffff----------------------------------------------------------", /* PIT */
};
char* Sflookup2[] = {  /* fx 5-8, same rows in the order GEQ2, GEQ, TEQ2, TEQ, DES2, DES, P1A, P1A2, PQ5, PQ5S, WAVD, LIM, FAC, FAC1M, FAC2, LEC, LEC2, ULC, ULC2, ENH2, ENH, EXC2, EXC, IMG, EDI, SON, AMP2, AMP, DRV2, DRV, PHAS, FILT, PAN, SUB */ };
```
```c
// X32.c:1197  FXc_lookup: find the parameter type of an FX parameter
ipar = (Xfx[index].command[10] - 48) * 10 + Xfx[index].command[11] - 48 - 1;
if (Xfx[index].command[4] < 53) {                       // '/fx/1'..'/fx/4'
    ityp = Xfx[index - ipar - 5].value.ii;  ctyp = *(Sflookup[ityp] + ipar);
} else {                                                // '/fx/5'..'/fx/8'
    ityp = Xfx[index - ipar - 2].value.ii;  ctyp = *(Sflookup2[ityp] + ipar);
}
```
VERIFIED: the 61 `Sflookup[]` rows above were diffed programmatically against X32Fx.h:155-217 (0 differences) and the 34 `Sflookup2[]` rows (X32Fx.h:219-254) were checked to equal the same-named `Sflookup` row (0 differences). `FXc_lookup` snippet re-read at X32.c:1196-1214 (it additionally returns NIL for ipar outside 0..63).

Implementation rule: read `/fx/N/type` first, then use the row to know whether each `par/PP` is an `f` (send float 0..1) or `i` (send int enum index). Sending the wrong tag is ignored by the desk. Note fx 5-8 must use the fx-5 numbering (e.g. `GEQ2 = 0`).

### 1.6 Node string forms for FX

`/node ,s fx/1` -> `node ,s /fx/1 HALL\n` (type token only); `/node ,s fx/1/source` -> `/fx/1/source INS INS`; `/node ,s fx/5` -> `/fx/5 GEQ2`; `/node ,s fx/5/par` -> `/fx/5/par v1 v2 ... v64` where each value is printed in **display units** (dB for GEQ) not 0..1 floats. Scene files use the same three line kinds (`/fx/N`, `/fx/N/source`, `/fx/N/par`).
SOURCE: X32.c:4035-4050 (node printer: `case FXTYP1: strcat(..., Sfxtyp1[...])`, `case FXSRC:` two `Sfxsrc` tokens, `case FXPAR1/FXPAR2: GetFxPar1(...)`); SetSceneParse.c:2510-2550 (`case fx_1: ... "/fx/%c/type"` + `Xp_fxlist(Xfxtyp4)`, `case fx_1_source:` two `Xp_list(Xfxsrc4)`, `case fx_1_par: fxparse1()`; `case fx_5:` uses `Xfxtyp5`, `case fx_5_par: fxparse5()`). VERIFIED (X32.c:4035-4050, SetSceneParse.c:2510-2549 re-read).

**Missed fact (added by verifier) — whole-block write:** the real desk accepts the entire parameter block as ONE OSC message addressed to `/fx/N/par` (no `/PP`), with 32 or 64 float arguments: X32SetScene (a real-desk tool) builds `sprintf(tmp, "/fx/%c/par", c1); k = Xsprint(buf, 0, 's', tmp); k = fxparse5(buf, k, ...)` (SetSceneParse.c:2528-2534, 2543-2549) and `fxparse.c:119-132` appends the tag string `,ffff...` (64 `f` for GEQ2/TEQ2, 32 `f` for GEQ/TEQ) followed by the floats `(dB+15)/30`. So a GEQ2 can be set in a single packet: `/fx/5/par ,ffff…(64) v1 … v64`. Per-parameter `/fx/5/par/07 ,f 0.5` also works (X32GEQ2cpy.c:189-217). VERIFIED (SetSceneParse.c + fxparse.c + fxparse5.c:59-66).

---

## 2. Graphic EQ effects (GEQ2 / GEQ / TEQ2 / TEQ)

### 2.1 Parameter layout (confirmed by DOC + tool + emulator + scene parser)

DOC appendix p.104 (VERIFIED-CORRECTED: printed page is 104, not 101), verbatim:
```
Dual Graphic Equalizer / True Dual Graphic Equalizer
Effect Name   Parameters   Parameter Name      Type & Range      Par #
GEQ2          64 f         31 x Eq Level A     linf [-15...+15]  1...31
(dual graphic eq)          Master Level A      linf [-15...+15]  32
TEQ2                       31 x Eq Level B     linf [-15...+15]  33...63
(true dual graphic eq)     Master Level B      linf [-15...+15]  64

Graphic Equalizer / True Graphic Equalizer
GEQ           32 f         31 x Eq Level L/R   linf [-15...+15]  1...31
(stereo graphic eq)        Master Level L/R    linf [-15...+15]  32
TEQ (true stereo graphic eq)
```

| Effect | `/fx/N/par/01..31` | `par/32` | `par/33..63` | `par/64` |
|---|---|---|---|---|
| GEQ2 / TEQ2 (dual) | bands 1..31, side **A** (= L input) | Master level A | bands 1..31, side **B** (= R input) | Master level B |
| GEQ / TEQ (stereo) | bands 1..31, L/R linked | Master level L/R | unused | unused |

All 64 (GEQ2/TEQ2) or 32 (GEQ/TEQ) parameters are type `f`. `X32GEQ2cpy.c` (real-desk tool) copies A->B as `par/01..31 -> par/33..63` and master `par/32 -> par/64` (X32GEQ2cpy.c: `for (i = 1; i < 32; i++) ... sprintf(b_rec+10, "%02d", i+32);` and `"/fx/%d/par/32"` -> `64`); its reset writes `0.5` to every band and master (`float dot5 = 0.5;`). VERIFIED: X32GEQ2cpy.c:72 (`dot5`), :188-197 (A->B: reads `/fx/N/par/01..31`, rewrites the reply with `sprintf(b_rec+10, "%02d", i+32)` -> par 33..63), :199-208 (B->A, `i-32`), :210-221 (reset loop writes par/00..31 and par/32..63 with `'f' 0.5`; par/00 is a harmless invalid address), :246-288 (masters 32 <-> 64). DOC p.104 table re-read verbatim.

### 2.2 Value scaling (band gain and master)

`linf [-15, +15]`, so:
```
f  = (g_dB + 15) / 30            g_dB = 30 * f - 15
```
Worked examples: -15 dB -> 0.0; -6 dB -> 0.3; 0 dB -> 0.5; +2.5 dB -> 0.58333; +6 dB -> 0.7; +15 dB -> 1.0.
SOURCES:
* VERIFIED: fxparse.c:74-77 `#define afine2float(a,b) do { fval = ((Xr_float() - a) / b); k = Xsprint(buf, k, 'f', &fval); } while (0)`, :119-132 `XGEQ2_TEQ2` / `XGEQ_TEQ` call `afine2float(-15.,30.)` 64x / 32x; X32.c:1758-1764 (`RLinf(..., -15., 30.)` x64, note RLinf clamps to [0,1]) and :2510-2516 (`Slinf(..., -15., 15., 0)`).
* fxparse.c:120-136 (scene dB -> OSC float): `k = Xsprint(buf, k, 's', ",ffffffff...64 f");  for (i = 0; i < 64; i++) afine2float(-15.,30.); // EQ values [-15, 15]` with `#define afine2float(a,b) fval = ((Xr_float() - a) / b)`; GEQ/TEQ same with 32 `f`.
* X32.c (emulator SetFxPar1, ~line 1758): `case _1_GEQ2: case _1_TEQ2: case _2_GEQ2: case _2_TEQ2: for (int j = 0; j < 64; j++) RLinf(&command[ipar++], str_pt_in, -15., 30.);` and GetFxPar1 (~2510): `for (i = 0; i < 64; i++) strcat(buf, Slinf(command[ipar++].value.ff, -15., 15., 0));`.
* Step size: not documented in DOC ("linf [-15...+15]" with no step). UNCONFIRMED: X32-Edit / the desk encoders step the GEQ in 0.5 dB increments; the wire value is a continuous float and the desk will quantize on display. The channel PEQ gain for comparison is `linf [-15, 15, 0.25]` (DOC p.31).
* Node/scene text formatting of GEQ values: the desk prints dB with one decimal (Maillot's real-desk default table X32CopyFX.c:118 `FXdef[27]` for GEQ2 is `"0.0 0.0 0.0 ... "`); the emulator prints `%.0f`. Parse as float; do not rely on the number of decimals. UNCONFIRMED whether the desk prints a leading `+` for positive gains (the channel EQ printer `Slinfs` uses `%+.2f`; GEQ printer in the emulator does not). Verifier note: X32CopyFX.c:99-130 `FXdef[]` are "real default values" captured from a desk (header v1.20); the GEQ2 (index 27) and GEQ (28) rows are `0.0 0.0 ...` (one decimal, no sign at zero) while other rows contain signed tokens such as `+60`, `+0`, so the desk does emit `+` on at least some signed parameters — parse with a sign-tolerant float parser either way.

### 2.3 Band centre frequencies (band k = `par/k`, k = 1..31)

Standard ISO 1/3-octave series, 20 Hz .. 20 kHz:
```
 1:20   2:25   3:31.5  4:40   5:50   6:63   7:80   8:100  9:125  10:160  11:200
12:250 13:315 14:400  15:500 16:630 17:800 18:1k  19:1.25k 20:1.6k 21:2k 22:2.5k
23:3.15k 24:4k 25:5k  26:6.3k 27:8k 28:10k 29:12.5k 30:16k 31:20k
```
Evidence: DOC p.63 `/-stat/geqpos` example: value `0x00000105` "Means EQ on faders for effect slot #1, fader window starting at fader 5, covering bands 50...250Hz" — an 8-fader window starting at band 5 spans bands 5..12 = 50, 63, 80, 100, 125, 160, 200, 250 Hz, which matches the ISO list exactly. DOC/marketing describe both GEQ and TEQ as "31-band 1/3-octave". The exact list is not printed anywhere in the C sources; treat the table above as CONFIRMED-by-inference (ISO series + geqpos example). VERIFIED (corroborated): the DOC p.63 geqpos example re-read verbatim ("fader window starting at fader 5, covering bands 50...250Hz"); web corroboration that the X32 GEQ is the standard ISO 1/3-octave 31-band set 20 Hz..20 kHz (Behringer product page "31-band graphic EQ"; the list above is the ISO 266 series). Still not printed in any C source. CAUTION on the geqpos example itself: the DOC gives the start-position field range as `0x00...0x17` (0..23, which only fits a 0-based 8-band window over 31 bands), yet its example maps value 5 to 50 Hz, which is 1-based band 5 (0-based index 4 = 50 Hz, index 5 = 63 Hz). Which of the two the desk really uses is UNCONFIRMED; the band list is unaffected.

### 2.4 Related status parameters

* `/-stat/geqonfdr ,i 0|1` — "EQ on faders" mode on/off (DOC p.63).
* `/-stat/geqpos ,i` — `(<FX#> << 8) | <start band 0..23>`: `0x100..0x800 | 0x00..0x17`; e.g. `0x105` = FX1, window starts at band 5 (DOC p.63). VERIFIED (DOC p.63 re-read; X32PrefStat.h:314 `{I32}`). UNCONFIRMED whether the start value is 0-based (range 0..23 suggests so) or 1-based (the DOC's "5 -> 50 Hz" example suggests so) — see §2.3.
* `/-stat/rtamodegeq ,i` {0 BAR, 1 SPEC}; `/-stat/rtageqpost ,i` {0 pre, 1 post} — RTA overlay for GEQ screens (DOC p.62).
* `/-prefs/rta/source` / `/-stat/rtasource` select the RTA source (see §7).

### 2.5 How X32-Edit shows it (general knowledge; not from code)

X32-Edit's FX page for GEQ2/TEQ2 shows two tabs/columns **A** and **B** with 31 sliders labelled by the frequencies in §2.3, each ±15 dB, plus a "Gain"/master slider (par 32 / 64); GEQ/TEQ shows a single 31-slider set with one master. UNCONFIRMED detail; the OSC mapping above is what matters.

### 2.6 Other EQ-type effects (for completeness)

P1A has **11** parameters (VERIFIED-CORRECTED: the earlier text said 10 with P1A2 = par 11..20; DOC p.106 and `Sflookup` row `iffifffifii` (11 chars) / P1A2 `iffifffifiiiffifffifii` (22 chars) give 11 and 22): par 1 Active (enum OFF/ON), 2 Gain linf [-12,+12], 3 Lo Boost linf [0,10], 4 Lo Freq enum {20,30,60,100}, 5 Lo Att linf [0,10], 6 Hi Width linf [0,10], 7 Hi Boost linf [0,10], 8 Hi Freq enum {3k,4k,5k,8k,10k,12k,16k}, 9 Alt Sel/Hi Att linf [0,10], 10 Hi Freq(att) enum {5k,10k,20k}, 11 Transformer enum OFF/ON; P1A2 = side A par 1..11, side B par 12..22 (same order); PQ5 (par 1..9: Active, Gain ±12, Lo Freq enum{200,300,500,700,1000}, Lo Boost, Mid Freq enum{200,300,500,700,1k,1k5,2k,3k,4k,5k,7k}, Mid Boost, Hi Freq enum{1k5,2k,3k,4k,5k}, Hi Boost, Transformer) and PQ5S = two copies. SOURCE: DOC p.106-108 (VERIFIED-CORRECTED: pages 106-108, not 103-104); X32.c:453-458 R10-R15 lists (`R10[] = {"1k5","2k","3k","4k","5k"}`, `R11[] = {"200","300","500","700","1k","1k5","2k","3k","4k","5k","7k"}`, `R12[] = {"200","300","500","700","1000"}`, `R13[] = {"5k","10k","20k"}`, `R14[] = {"3k","4k","5k","8k","10k","12k","16k"}`, `R15[] = {"20","30","60","100"}`). VERIFIED-CORRECTED: the DOC's P1A Lo Freq list is `enum [20, 30, 60, 100]` (p.106), the same as the emulator's `R15`; it is only the scene parser X32SetScene.h:5184 `l_plfreq[] = {"0", "30", "60", "100"}` that prints the first token as "0" (scene-file spelling). The index (0..3) is what you send either way. PQ5 lists VERIFIED against DOC p.108 and R10/R11/R12.

---

## 3. Inserts

### 3.1 Addresses (identical block on every strip that has an insert)

| Strip | Addresses |
|---|---|
| `/ch/[01-32]` | `/ch/NN/insert/on` (`i` 0/1), `/ch/NN/insert/pos` (`i` enum {0 PRE, 1 POST}), `/ch/NN/insert/sel` (`i` enum 0..22) |
| `/bus/[01-16]` | `/bus/NN/insert/on|pos|sel` |
| `/mtx/[01-06]` | `/mtx/NN/insert/on|pos|sel` |
| `/main/st` | `/main/st/insert/on|pos|sel` |
| `/main/m` | `/main/m/insert/on|pos|sel` |
| `/auxin`, `/fxrtn`, `/dca` | **no insert** |

`pos`: PRE = insert before the EQ/dynamics chain point "pre", POST = after (DOC: `{PRE, POST}, int with value 0 or 1`). SOURCES: X32Channel.h:30-33; X32Bus.h:38-41; X32Mtx.h:39-42; X32CfgMain.h Xmain (`/main/st/insert/*`, `/main/m/insert/*`); DOC p.25, 33, 35, 36, 38.

### 3.2 `insert/sel` enum (23 entries)

```
 0 OFF
 1 FX1L   2 FX1R   3 FX2L   4 FX2R   5 FX3L   6 FX3R   7 FX4L   8 FX4R
 9 FX5L  10 FX5R  11 FX6L  12 FX6R  13 FX7L  14 FX7R  15 FX8L  16 FX8R
17 AUX1  18 AUX2  19 AUX3  20 AUX4  21 AUX5  22 AUX6
```
Formula: `FX n side L -> 2n-1`, `FX n side R -> 2n`; `AUX k -> 16+k`. Examples: FX5L = 9, FX8R = 16, AUX1 = 17.
SOURCES: X32.c:328 `char* Xisel[] = {" OFF", " FX1L", " FX1R", " FX2L", " FX2R", " FX3L", " FX3R", " FX4L", " FX4R", " FX5L", " FX5R", " FX6L", " FX6R", " FX7L", " FX7R", " FX8L", " FX8R", " AUX1", " AUX2", " AUX3", " AUX4", " AUX5", " AUX6", ""};`; X32SetScene.h:5097 `Xinsel[]` identical; DOC p.25 `/ch/[01...32]/insert/sel enum int with value [0...22] representing {OFF, FX1L, FX1R, ... FX8R, AUX1, ..., AUX6}` (same on bus/mtx/main). VERIFIED: X32.c:328-331 `Xisel[]`, X32Fx.h:58-60 `Sinsel[]` (printer copy), X32SetScene.h:5097-5099 `Xinsel[]`, DOC p.25 (ch), p.33 (bus), p.35 (mtx), p.36/38 (main/st, main/m — the DOC's main/st column layout is garbled but the three insert addresses are present) all re-read and identical.

`AUXn` = the physical rear-panel AUX IN/OUT pair n used as an external analog insert loop (send on AUX OUT n, return on AUX IN n).

### 3.3 Node string form

`/node ,s ch/01/insert` -> `/ch/01/insert OFF PRE OFF` (tokens: on, pos, sel). SOURCE: X32.c:3959-3963 `case CHIN: " ON"/" OFF"; Sdpos[pos]; Sinsel[sel]`; SetSceneParse.c:837-855 parses the same three tokens. VERIFIED (both re-read; `Sdpos[] = {" PRE"," POST"}` X32Fx.h:62).

### 3.4 One insert point per user; conflicts

* Read-back of where each insert point currently lives: `/-insert/fx[1-8]L`, `/-insert/fx[1-8]R`, `/-insert/aux[1-6]` — `i`: "Channel the FX L input [1...8] is inserted into", "Channel the Aux input [1...6] is inserted into". `/node ,s -insert` "reports 22 inserts in the following order: fx1L, fx1R, fx2L, ..., fx8R, aux1, ..., aux6". SOURCE: DOC p.43 + footnote 36 (VERIFIED-CORRECTED: printed page 43, not 42; wording re-read verbatim: "Channel the FX L input[1...8] is inserted into", "/node ,s -insert reports 22 inserts in the following order: fx1L, fx1R, fx2L, ..., fx8R, aux1, ..., aux6"). The emulator only models an older `/insert/aux/1..6 {I32}` (X32Misc.h:280-285, VERIFIED-CORRECTED line numbers) and provides no value table; the dispatcher routes `/ins` to `function_misc` (X32.c:513). UNCONFIRMED: the encoding of the "channel" int (most likely the `/-stat/selidx`-style strip index 0..71: 0-31 ch, 32-39 aux, 40-47 fxrtn, 48-63 bus, 64-69 mtx, 70 LR, 71 M/C, or -1/none when unused). Read it once on a real desk before relying on it.
* Constraint: an insert point (FXnL, FXnR, AUXn) can be inserted in **one** strip only. User manual (via ManualsLib/forums): "Sending more than one channel through the same insert effect is prohibited, and there will be a warning when you try to insert an effect slot that has already been used as an insert on any other channel." Both sides of a dual effect (GEQ2, DES2, P1A2, PQ5S, FAC2, LEC2, ULC2, ENH2, EXC2, AMP2, DRV2, CMB2, PIT2, TEQ2) can be inserted into two different strips (A side = `FXnL`, B side = `FXnR`); a stereo effect (GEQ, TEQ, ...) needs both `FXnL` and `FXnR` inserted (e.g. main L/R uses the pair automatically when `/main/st/insert/sel = FXnL`; on the desk selecting FX5L for a stereo strip inserts the stereo pair — UNCONFIRMED whether `sel` then reads FXnL).
* What the desk does over OSC if a second strip selects an already-used insert point: UNCONFIRMED. Best inference (from the "prohibited/warning" UI behaviour and the fact that `/-insert/*` is a single int per point): the last write wins and the previous owner's `insert/sel` is reset to 0 (OFF) or its insert is simply overridden; the desk will push the resulting changes via `/xremote`. Implementation: after writing `insert/sel`, re-read `/-insert/...` (or all `*/insert/sel`) and reconcile.
* FX 1-4 used as inserts must also have `/fx/N/source/l|r = 0 (INS)`; FX 5-8 are always inserts.

---

## 4. Routing

### 4.1 Overview of the signal path (what to resolve)

```
physical input --(/config/routing/IN|PLAY blocks, or userrout/in)--> "In 1..32" --(/ch/NN/config/source)--> channel NN
bus/main/mtx/direct-out --(/outputs/main/NN/src + pos)--> "OUT 1..16" tap --(/config/routing/OUT blocks)--> XLR out 1..16
                                                        "OUT 1..16" tap --(/config/routing/AES50A|B|CARD blocks = OUT1-8 / OUT9-16)--> AES50/Card channels
/outputs/aux/NN/src --> "AUX 1..6" taps (rear TRS aux outs; also block "AUX1-6/Mon" on AES50/Card)
/outputs/p16/NN/src --> "P16 1..16" taps (Ultranet; also blocks "P161-8"/"P169-16")
/outputs/aes/01|02/src --> AES/EBU out;  /outputs/rec/01|02/src --> USB recorder
/config/userrout/out/NN (FW4) --> "UOUT 1..48" blocks; /config/userrout/in/NN --> "UIN 1..32" blocks
```

### 4.2 `/config/routing/routswitch` — `i` enum {0 REC, 1 PLAY}

Selects whether the `IN/*` (REC) or `PLAY/*` block set is active for the channel inputs. Default 0. The X-Live card flips it to 1 during playback and the desk then pushes `/config/routing/PLAY/*` values (DOC p.67-68 example). SOURCE: X32CfgMain.h:195 `{"/config/routing/routswitch", {E32}, F_XET, {0}, XCFrsw}` with X32.c:339 `XCFrsw[] = {" REC", " PLAY"}`; DOC p.21 ("0: Rec [default value] 1: Playback ... automatically select the /config/routing/IN or the /config/routing/PLAY parameter blocks"). VERIFIED. Emulator quirk: its node printer `case CROUTSW` (X32.c:3860) prints `Sroutin[value]` ("AN1-8"/"AN9-16") instead of REC/PLAY — ignore.

### 4.3 `/config/routing/IN/{1-8,9-16,17-24,25-32}` and `/config/routing/PLAY/{1-8,...}` — `i` enum 0..23

```
 0 AN1-8    1 AN9-16    2 AN17-24   3 AN25-32          (local XLR inputs)
 4 A1-8     5 A9-16     6 A17-24    7 A25-32   8 A33-40   9 A41-48   (AES50 A)
10 B1-8    11 B9-16    12 B17-24   13 B25-32  14 B33-40  15 B41-48   (AES50 B)
16 CARD1-8 17 CARD9-16 18 CARD17-24 19 CARD25-32                      (expansion card)
20 UIN1-8  21 UIN9-16  22 UIN17-24 23 UIN25-32                        (FW 4.0+ user-in blocks)
```
SOURCES: DOC p.21 `int with value [0...23] representing {AN1-8, AN9-16, AN17-24, AN25-32, A1-8, A9-16, A17-24, A25-32, A33-40, A41-48, B1-8, B9-16, B17-24, B25-32, B33-40, B41-48, CARD1-8, CARD9-16, CARD17-24, CARD25-32, UIN1-8, UIN9-16, UIN17-24, UIN25-32}`; X32SetScene.h:5049 `Xinaertng[]` identical; X32.c:340-342 `XRtgin[]` (pre-FW4, 0..19 only). VERIFIED (DOC p.21-22 list re-read for IN and PLAY, both identical; X32CfgMain.h:196-201, 227-232 bind IN/* and PLAY/* to `XRtgin`, IN/AUX and PLAY/AUX to `XRtina`).

### 4.4 `/config/routing/IN/AUX` and `/config/routing/PLAY/AUX` — `i` enum 0..15

```
0 AUX1-4 (really the 6 local AUX inputs; name kept for compatibility, DOC fn.15)
1 AN1-2  2 AN1-4  3 AN1-6   4 A1-2  5 A1-4  6 A1-6   7 B1-2  8 B1-4  9 B1-6
10 CARD1-2 11 CARD1-4 12 CARD1-6   13 UIN1-2 14 UIN1-4 15 UIN1-6
```
SOURCES: DOC p.21-22; X32SetScene.h:5044 `Xauxrtng[]`; X32.c:346-347 `XRtina[]` (0..12). VERIFIED (DOC list `{AUX1-4, AN1-2, AN1-4, AN1-6, A1-2, A1-4, A1-6, B1-2, B1-4, B1-6, CARD1-2, CARD1-4, CARD1-6, UIN1-2, UIN1-4, UIN1-6}` re-read; footnote 15 "It really is AUX1-6, but needs to stay AUX1-4 for backward compatibility").

### 4.5 `/config/routing/AES50A/{1-8,9-16,17-24,25-32,33-40,41-48}`, `/config/routing/AES50B/...`, `/config/routing/CARD/{1-8,9-16,17-24,25-32}` — `i` enum 0..35 (each selects an 8-channel block for those 8 output channels)

```
 0 AN1-8    1 AN9-16   2 AN17-24  3 AN25-32
 4 A1-8     5 A9-16    6 A17-24   7 A25-32   8 A33-40   9 A41-48
10 B1-8    11 B9-16   12 B17-24  13 B25-32  14 B33-40  15 B41-48
16 CARD1-8 17 CARD9-16 18 CARD17-24 19 CARD25-32
20 OUT1-8  21 OUT9-16                    <- the 16 "output taps" defined by /outputs/main/NN
22 P161-8  23 P169-16                    <- the 16 Ultranet taps defined by /outputs/p16/NN
24 AUX1-6/Mon                            <- aux-out taps 1-6 (/outputs/aux/NN) + Monitor L, Monitor R
25 AuxIN1-6/TB                           <- aux inputs 1-6 + TB internal + TB external
26 UOUT1-8 27 UOUT9-16 28 UOUT17-24 29 UOUT25-32 30 UOUT33-40 31 UOUT41-48   (FW4 user-out)
32 UIN1-8  33 UIN9-16  34 UIN17-24 35 UIN25-32                                 (FW4 user-in)
```
SOURCES: DOC p.21-22 `enum int with value [0...35] representing {AN1-8, ..., CARD25-32, OUT1-8, OUT9-16, P161-8, P169-16, AUX1-6/Mon, AuxIN1-6/TB, UOUT1-8, ..., UOUT41-48, UIN1-8, ..., UIN25-32}`; X32SetScene.h:5056 `Xiaesrtng[]` identical; X32.c:343-345 `XRtaea[]` (0..25). VERIFIED (DOC p.21-22 re-read token by token; X32CfgMain.h:202-221 binds all 16 AES50A/AES50B/CARD blocks to `XRtaea`).

### 4.6 `/config/routing/OUT/{1-4,9-12}` — `i` enum 0..35 (list A) and `/config/routing/OUT/{5-8,13-16}` — list B (which physical XLR outs 1-16 carry)

List A (for `OUT/1-4` and `OUT/9-12`):
```
 0 AN1-4   1 AN9-12   2 AN17-20  3 AN25-28   4 A1-4   5 A9-12   6 A17-20   7 A25-28   8 A33-36   9 A41-44
10 B1-4   11 B9-12   12 B17-20  13 B25-28  14 B33-36 15 B41-44 16 CARD1-4 17 CARD9-12 18 CARD17-20 19 CARD25-28
20 OUT1-4 21 OUT9-12 22 P161-4  23 P169-12 24 AUX/CR 25 AUX/TB
26 UOUT1-4 27 UOUT9-12 28 UOUT17-20 29 UOUT25-28 30 UOUT33-36 31 UOUT41-44 32 UIN1-4 33 UIN9-12 34 UIN17-20 35 UIN25-28
```
List B (for `OUT/5-8` and `OUT/13-16`):
```
 0 AN5-8   1 AN13-16  2 AN21-24  3 AN29-32   4 A5-8   5 A13-16  6 A21-24   7 A29-32   8 A37-40   9 A45-48
10 B5-8   11 B13-16  12 B21-24  13 B29-32  14 B37-40 15 B45-48 16 CARD5-8 17 CARD13-16 18 CARD21-24 19 CARD29-32
20 OUT5-8 21 OUT13-16 22 P165-8 23 P1613-16 24 AUX/CR 25 AUX/TB
26 UOUT5-8 27 UOUT13-16 28 UOUT21-24 29 UOUT29-32 30 UOUT37-40 31 UOUT45-48 32 UIN5-8 33 UIN13-16 34 UIN21-24 35 UIN29-32
```
Defaults (factory): `OUT/1-4 = 20 (OUT1-4)`, `OUT/5-8 = 20 (OUT5-8)`, `OUT/9-12 = 21 (OUT9-12)`, `OUT/13-16 = 21 (OUT13-16)`, i.e. XLR out k carries tap k. `AUX/CR` = aux taps 1-4 / control-room (monitor) L,R depending on sub-block; `AUX/TB` = aux in / talkback. UNCONFIRMED: exact 4-channel content of `AUX/CR` and `AUX/TB` blocks for each of the two lists (DOC does not expand them).
SOURCES: DOC p.22; X32SetScene.h:5066 `Xo14rtng[]` and 5075 `Xo58rtng[]`; X32.c:348 `XRout1[]`, 351 `XRout5[]`; X32CfgMain.h:
```c
{"/config/routing/OUT/1-4",   {E32}, F_XET, {0}, XRout1},
{"/config/routing/OUT/9-12",  {E32}, F_XET, {0}, XRout1},
{"/config/routing/OUT/5-8",   {E32}, F_XET, {0}, XRout5},
{"/config/routing/OUT/13-16", {E32}, F_XET, {0}, XRout5},
```
Node form: `/node ,s config/routing/OUT` prints 4 tokens; the emulator prints them in the order 1-4, 9-12, 5-8, 13-16 (X32.c:3876-3881 `case CROUTOT:` `Srouto1[i+1] Srouto1[i+2] Srouto2[i+3] Srouto2[i+4]` following the Xconfig entry order above) while SetSceneParse.c:441-457 parses scene lines in the order 1-4 (`Xo14rtng`), 5-8 (`Xo58rtng`), 9-12 (`Xo14rtng`), 13-16 (`Xo58rtng`). VERIFIED-CORRECTED (resolves former UNCONFIRMED item 7): `.scn` scene files are verbatim node-line dumps produced by the real desk (X32GetScene.c writes the desk's `/node` replies; X32SetScene.c re-parses real desk files), and the parser expects **1-4, 5-8, 9-12, 13-16**, so that is the real desk's token order; the emulator's 1-4, 9-12, 5-8, 13-16 is an emulator quirk. Reading the four individual addresses remains the safest implementation. Both lists VERIFIED against DOC p.22 (list A and list B re-read token by token, both `[0...35]`), X32.c:348-353 `XRout1[]/XRout5[]` (0..25, pre-FW4), X32Fx.h:30-40 `Srouto1[]/Srouto2[]` (note the emulator printer copy has a typo `B33-46` for `B33-36` at index 14 of `Srouto1`).

### 4.7 `/outputs/<group>/NN/src` — `i` enum 0..76 (the same list for main/aux/p16/aes/rec)

```
 0 OFF
 1 Main L     2 Main R     3 M/C
 4..19  MixBus 01..16          (bus n -> 3 + n)
20..25  Matrix 1..6            (mtx n -> 19 + n)
26..57  DirectOut Ch 01..32    (ch n -> 25 + n)
58..65  DirectOut Aux 1..8     (auxin n -> 57 + n)
66..73  DirectOut FX 1L,1R,2L,2R,3L,3R,4L,4R  (fxrtn n -> 65 + n)
74 Monitor L   75 Monitor R   76 Talkback
```
Groups: `/outputs/main/[01-16]` (XLR "OUT 1-16" taps; also have `/delay/on` `i` and `/delay/time` `f` linf [0.3, 500, 0.1] ms), `/outputs/aux/[01-06]`, `/outputs/p16/[01-16]` (+ `/iQ/group|speaker|eq|model`), `/outputs/aes/[01-02]`, `/outputs/rec/[01-02]`. Each has `src` (`i`), `pos` (`i` enum 0..8), `invert` (`i` 0/1; not on rec).
SOURCES: DOC p.40-42 `/outputs/main/[01...16]/src int value [0...76] representing {OFF, Main L, Main R, M/C, MixBus 01...16, Matrix 1...6, DirectOut Ch 01...32, DirectOut Aux 1...8, DirectOut FX 1L...4R, Monitor L, Monitor R, Talkback}`; X32Output.h:14-323 (`/outputs/main/01/src {I32}`, `/pos {E32} Xotpos`, `/invert {E32} OffOn`, `/delay/on`, `/delay/time {F32}`; aux 01-06; p16 01-16; aes 01-02; rec 01-02). X32SetScene.h:5116 has an older commented-out `Xoutsel[]` that differs — ignore it; the doc list is FW-current. VERIFIED: DOC p.40-42 src list `{OFF, Main L, Main R, M/C, MixBus 01...16, Matrix 1...6, DirectOut Ch 01...32, DirectOut Aux 1...8, DirectOut FX 1L...4R, Monitor L, Monitor R, Talkback}` re-read for main/aux/p16/aes/rec (count: 1+3+16+6+32+8+8+3 = 77 entries = 0..76); X32Output.h:14-22 (main: src I32, pos E32 Xotpos, invert E32, delay/on, delay/time F32), :130-133 (aux: src/pos/invert), :157 (p16), :303-306 (aes: src/pos/invert), :313 (rec uses node type `OMAIN2` = src + pos only, no invert — X32.c:4056-4059).

`pos` enum (tap point) 0..8:
```
0 IN/LC   1 IN/LC+M   2 <-EQ   3 <-EQ+M   4 EQ->   5 EQ->+M   6 PRE   7 PRE+M   8 POST
```
IN/LC = after input/low-cut, `<-EQ` = pre-EQ, `EQ->` = post-EQ, PRE = pre-fader, POST = post-fader. **Taps without `+M` are not affected by the strip's mute; `+M` variants and POST are muted with the strip.** SOURCES: X32.c:385 `Xotpos[] = {"IN/LC", "IN/LC+M", "<-EQ", "<-EQ+M", "EQ->", "EQ->+M", "PRE", "PRE+M", "POST"}`; DOC p.40; mute semantics from drewbrashler.com "X32 Output Tap - Pre Fader & Pre Fader+M" ("Taps that are IN/LC, PreEQ, PostEQ, and PreFdr are not affected by the mute button ... so the +M designation allows any of these taps to be muted"). VERIFIED: X32.c:385 `Xotpos[]` and X32Fx.h:70 `Smpos[]` both list the 9 tokens in this order; DOC p.40-42 `int [0...8] representing {IN/LC, IN/LC+M, <-EQ, <-EQ+M, EQ->, EQ->+M, PRE, PRE+M, POST}` on every group.

Node form: `/node ,s outputs/main/01` -> `/outputs/main/01 4 POST OFF` (src as int, pos token, invert token); the delay sub-node is `/outputs/main/01/delay OFF 0.3`. SOURCE: X32.c:4051-4055 `case OMAIN: Sint(src) Smpos[pos] " ON"/" OFF"`; 4066-4069 `case OMAIND: ON/OFF, Slinf(time, 0.3, 500., 1)`. VERIFIED.

### 4.8 User routing (FW 4.0+): `/config/userrout/out/[01-48]` (`i` 0..208) and `/config/userrout/in/[01-32]` (`i` 0..168)

```
  0 OFF
  1..32   Local In 1..32
 33..80   AES50-A 1..48
 81..128  AES50-B 1..48
129..160  Card In 1..32
161..166  Aux In 1..6
167       TB Internal
168       TB External           <- /config/userrout/in stops here (max 168)
169..184  Outputs 1..16 (the /outputs/main taps)
185..200  P16 1..16
201..206  AUX 1..6 (aux-out taps)
207       Monitor L
208       Monitor R
```
SOURCES: DOC p.21 (`/config/userrout/out/01...48 int [0...208] representing OFF to Mon R`, `/config/userrout/in/01...32 int [0...168] representing OFF to TB ext`); X32CfgMain.h:105-190 (`/config/userrout/out/01..48 {I32}`, `/config/userrout/in/01..32 {I32}`). Node form: `/node ,s config/userrout/out` prints 48 ints (X32.c:4416-4419 `case UROUO: for (j = 1; j < 49; j++)`), `.../in` 32 ints (X32.c:4421-4424 `UROUI`, `j < 33`). VERIFIED: DOC p.21 out list (0 OFF, 1-32 Local In, 33-80 AES50-A, 81-128 AES50-B, 129-160 Card In, 161-166 Aux In, 167 TB Internal, 168 TB External, 169-184 Outputs 1-16, 185-200 P16 1-16, 201-206 AUX 1-6, 207 Monitor L, 208 Monitor R) and in list (0..168, same prefix) re-read verbatim; X32CfgMain.h:111 `/config/userrout/out/01 {I32}`, :161 `/config/userrout/in/01 {I32}`.

### 4.9 Channel input source: `/ch/[01-32]/config/source` — `i` enum 0..64

`{0 OFF, 1..32 In01..In32, 33..38 Aux 1..6, 39 USB L, 40 USB R, 41..48 Fx 1L,1R,2L,2R,3L,3R,4L,4R, 49..64 Bus 01..16}`. SOURCE: DOC p.24 `int with value [0...64] representing {OFF, In01...32, Aux 1...6, USB L, USB R, Fx 1L...Fx 4R, Bus 01...16}`. Also `/-ha/[00-39]/index ,i` (read-only) returns the headamp index actually feeding channel 00-31 / aux 32-39 (0..31 local XLR, 32..79 AES50A, 80..127 AES50B, -1 when the source is not a headamp, e.g. card) — DOC p.42-43, X32Misc.h:287-328 (`/-ha/00/index` ... `/-ha/39/index`, all `{I32}`). VERIFIED (DOC p.24 source list and p.43 `-ha` text re-read; X32Channel.h:19 `/ch/01/config/source {I32}`).

### 4.10 Resolution recipes (with worked examples)

**Channel -> physical input**
```
s = /ch/NN/config/source
if 1 <= s <= 32:                      # "In s"
    k = s
    blk = (k-1) // 8                  # 0..3 -> IN/1-8, IN/9-16, IN/17-24, IN/25-32
    set = PLAY if /config/routing/routswitch == 1 else IN
    v = /config/routing/<set>/<blk>   # 0..23
    o = (k-1) % 8
    if   0 <= v <= 3:  local XLR  8*v + o + 1
    elif 4 <= v <= 9:  AES50A ch  8*(v-4) + o + 1
    elif 10<= v <=15:  AES50B ch  8*(v-10) + o + 1
    elif 16<= v <=19:  Card ch    8*(v-16) + o + 1
    elif 20<= v <=23:  User In    8*(v-20) + o + 1  -> then /config/userrout/in/<that> (table 4.8)
elif 33 <= s <= 38:  aux in (s-32) -> /config/routing/IN/AUX (table 4.4)
```
Example: `/ch/20/config/source = 20`, `/config/routing/IN/17-24 = 6 (A17-24)`: blk 2, o = 3 -> AES50A channel 8*(6-4)+3+1 = **20** (e.g. S16/SD16 #1 input 20 if it occupies AES50A 1-32). Example 2: `/ch/05/config/source = 5`, `/config/routing/IN/1-8 = 0 (AN1-8)` -> local XLR **5**.

**Bus -> physical output (XLR / AES50 / card)**
```
target = 3 + bus                       # /outputs src value for MixBus (bus 3 -> 6)
taps  = [NN for NN in 1..16 if /outputs/main/NN/src == target]     # "OUT NN" taps
for each tap NN:
   XLR out NN if /config/routing/OUT/<block of NN> == 20 (blocks 1-4,5-8) or 21 (blocks 9-12,13-16)
   AES50A ch c for every block b in {1-8,9-16,...,41-48} with value 20 (OUT1-8)  -> c = base(b) + NN-1  (NN 1..8)
                                                              or value 21 (OUT9-16) -> c = base(b) + NN-9  (NN 9..16)
   same for AES50B and CARD blocks
```
Example: bus 3 on SD16 output 11 over AES50A: `/outputs/main/11/src = 6` (MixBus 03), `/config/routing/AES50A/9-16 = 21 (OUT9-16)` -> AES50A channel 9 + (11-9) = **11**. Also XLR out 11 carries it if `/config/routing/OUT/9-12 = 21`. If `/outputs/main/11/pos` is 6 (PRE) the feed ignores the bus mute; use 8 (POST) or 7 (PRE+M) for a mutable feed.

Also check P16 (`/outputs/p16/NN/src == target`, blocks 22/23 on AES50/Card), aux outs (`/outputs/aux/NN/src`, block 24), AES/EBU (`/outputs/aes/0N/src`), recorder (`/outputs/rec/0N/src`) and user-out (`/config/userrout/out/NN == 168+tap` with UOUT blocks 26..31).

Node strings for the routing blocks: `/config/routing/IN AN1-8 AN9-16 AN17-24 AN25-32 AUX1-4`, `/config/routing/AES50A OUT1-8 OUT9-16 ...` (6 tokens), `/config/routing/CARD ...` (4), `/config/routing/PLAY ...` (5), `/config/routing/routswitch REC`. SOURCE: X32.c:3860-3881 (`CROUTSW`, `CROUTIN/CROUTPLAY` 4x`Sroutin`+`Sroutax`, `CROUTAC` loops over `command[i].value.ii` = 6 or 4 tokens), X32ds_node.h:71-77 lists these node names. VERIFIED (both re-read; X32CfgMain.h:194-232 gives the container counts {5},{6},{6},{4}).

Routing presets: `/-libs/r/[001-100]/{pos,name,type,flags,hasdata}`, saved/loaded with `/save ,sis librout idx name` and `/load ,si librout idx` (DOC p.49-51).

---

## 5. Mute groups and DCAs

### 5.1 Mute groups

* `/config/mute/[1-6]` — `i` enum {0 OFF, 1 ON}: mute group master switch. Node `/node ,s config/mute` -> `/config/mute OFF OFF OFF OFF OFF OFF`. SOURCE: X32CfgMain.h:55-61 (`/config/mute {OFFON} F_FND {6}`, `/config/mute/1..6 {E32} OffOn`); DOC p.20 `/config/mute/[1...6] enum {OFF, ON}: Mute Group selection`. VERIFIED.
* Membership bitmask `grp/mute` — `%int`, 6 bits, **bit 0 = mute group 1 ... bit 5 = mute group 6**; on: `/ch/[01-32]/grp/mute`, `/auxin/[01-08]/grp/mute`, `/fxrtn/[01-08]/grp/mute`, `/bus/[01-16]/grp/mute`, `/mtx/[01-06]/grp/mute`, `/main/st/grp/mute`, `/main/m/grp/mute`. Send as int (`,i 5` = groups 1 and 3). SOURCE: DOC p.28 `/ch/[01...32]/grp/mute %int [0, 63] (6 bits bitmap)` (same wording on p.30, 31, 34, 35, 36, 38); X32Channel.h:21-22 `{"/ch/01/grp/dca", {P32}}`, `{"/ch/01/grp/mute", {P32}}`; X32Bus.h:109-110, X32Mtx.h:79-80, X32CfgMain.h:450-451 (`/main/st/grp/dca|mute`), DOC p.28 (`%int [0, 255] (8 bits bitmap)` / `[0, 63] (6 bits bitmap)`), p.37 (`/main/st/grp/*`), p.38 (`/main/m/grp/*`). VERIFIED.
* Node/scene form: `/ch/01/grp %00000000 %000000` (dca 8 bits then mute 6 bits, MSB first). SOURCE: X32.c:3989-3992 `case CHGRP: Sbitmp(dca, 8); Sbitmp(mute, 6)`; SetSceneParse.c:1199-1203 (`"/ch/%02d/grp/dca"`, then `"/ch/%02d/grp/mute"`), 1428-1432 (auxin); text parser accepts `%bits` or a decimal int (X32.c:3156 `XslashSetPerInt`). VERIFIED (X32.c:1284-1296 `Sbitmp` prints `len-1` down to 0, i.e. MSB first; the real-desk `/showdump` examples in DOC p.54-55 confirm the same MSB-first printing: scene safes "Routing IO and Output Patch" print as `%110000000` = bits 8 and 7, "Talkback" prints as `%000000010` = bit 1).
* Semantics knobs: `/-prefs/hardmute ,i 0|1` ("Hard Mutes" in Config->Mute System), `/-prefs/dcamute ,i 0|1` ("DCA groups" in Config->Mute System), `/-prefs/invertmutes ,i` {0 NORM, 1 INV} ("Invert LEDs"). SOURCE: X32PrefStat.h:34-36; DOC p.56. Meaning (X32 user manual p.29 via ManualsLib, and drewbrashler.com): with Hard Mutes on, a channel muted by its own MUTE stays muted even if a mute group containing it is un-muted, and the mute applies to all its sends "mixer-wide"; with DCA groups on, a DCA mute mutes member channels everywhere (including bus sends), otherwise a DCA mute only mutes the members' contribution to the main mix. UNCONFIRMED beyond these summaries (verifier: corroborated by two further secondary sources — foell.org "X32 Rack DCA Mutes 3-ways" and behringer.world topic 798 "Muting Groups": "if Hard Mute is checked it will stay muted regardless of what a mute group does ... if DCA mute is enabled all channels that belong to that DCA will be muted everywhere — FOH and buses. If not enabled (default) the DCA mute only mutes those member channels for FOH"; X32PrefStat.h:34-36 names VERIFIED, DOC p.57 labels VERIFIED).
* `/-stat/screen/mutegrp ,i 0|1` shows/hides the mute-group overlay on the desk LCD (DOC p.63).

### 5.2 DCA

* `/dca/[1-8]/on` (`i` 0/1), `/dca/[1-8]/fader` (`f` level 0..1, 1024 steps, same fader law as channels: `Slevel` in X32.c:1237 — `f<=0.0625: 30/0.0625*f-90; f<=0.25: ...-60; f<0.5: ...-30; else 20/0.5*(f-0.5)-10` dB), `/dca/[1-8]/config/name` (`s`, <=12 chars), `/dca/[1-8]/config/icon` (`i` 1..74), `/dca/[1-8]/config/color` (`i` 0..15 {OFF, RD, GN, YE, BL, MG, CY, WH, OFFi, RDi, GNi, YEi, BLi, MGi, CYi, WHi}). Note there is **no** `/dca/N/mix/...` — `on`/`fader` sit directly under `/dca/N`. SOURCE: X32Dca.h:11-69; DOC p.38. VERIFIED (X32Dca.h:12-18 `/dca/1/on {E32} OffOn`, `/dca/1/fader {F32}`, `/dca/1/config/name {S32}`, `/icon {I32}`, `/color {E32} Xcolors`; DOC p.38 `level [0.0...1.0(+10dB), 1024]`, icon `[1...74]`, colour list `{OFF, RD, GN, YE, BL, MG, CY, WH, OFFi, RDi, GNi, YEi, BLi, MGi, CYi, WHi}`; X32.c:1237-1249 `Slevel` law re-read).
* Membership: `.../grp/dca` — `%int` 8 bits, **bit 0 = DCA 1 ... bit 7 = DCA 8**, on the same strips as `grp/mute` (ch, auxin, fxrtn, bus, mtx, main/st, main/m). `/ch/01/grp/dca ,i 5` = DCA1 + DCA3. SOURCE: DOC p.28 `/ch/[01...32]/grp/dca %int [0, 255] (8 bits bitmap)`. VERIFIED (and the DOC p.55 `/showdump` example "selecting Main/Matrix/Group parameter DCA 8" prints `maingrps` = 32768 = bit 15, consistent with bit 8..15 = DCA 1..8 in the same bit convention).
* `/-stat/dcaspill ,i 0..8` — DCA spill in progress (0 = none) (DOC p.63, FW 4.06).
* `/-stat/solosw/73..80` = DCA 1..8 solo switches (see §7).

---

## 6. Shows, cues, scenes, snippets

### 6.1 Model

One show at a time; up to 500 cue slots (`/-show/showfile/cue/000..499`, DOC says "100 distinct cues" but the tree has 500 slots), 100 scenes (`scene/000..099`), 100 snippets (`snippet/000..099`). Scene 0 always exists and has no safes. SOURCE: DOC p.44-45; X32Show.h:13 (`/-show/prepos/current {I32}`), 17-28 (show fields), 31-6089 (cue 000-499), 6096-6604 (scene 000-099), 6606-7320 (snippet 000-099). VERIFIED (DOC addresses read `/-show/showfile/cue/[000-499]/...` while the descriptions say "saved at position [000-099]").

### 6.2 `/-show/prepos/current` — `i`

"Scene page cue, scene or snippet slot highlighted line/index is <int> value" (DOC p.44). Which list it indexes depends on `/-prefs/show_control` (`i` {0 CUES, 1 SCENES, 2 SNIPPETS}, DOC p.56; X32PrefStat.h:31 `PSCont[] = {" CUES", " SCENES", " SNIPPETS"}`). With show_control = SCENES the int is the **scene number 0..99** exactly as displayed (GetSceneName.c:209-214 does `for (i = 0; i < 4; i++) endian.cc[i] = r_buf[31-i]; sprintf(s_buf, "/-show/showfile/scene/%03d", endian.ii)` from the received value, i.e. the int is used directly as the 0-based scene slot). VERIFIED. DOC prints the range as "[1-099]" — treat as 0..99 (scene 0 is valid). It is read/write: X32GetLib.c/X32SetLib.c save it, `/save`+`/load` scene 99, then restore with `/-show/prepos/current ,i <saved>` (X32GetLib.c:905) — evidence that `/load` (and a desk-side GO) moves the pointer.

**Detecting a scene change**: keep `/xremote` alive; set `/-prefs/show_control ,i 1`; the desk then pushes `/-show/prepos/current ,i N` whenever the highlighted/loaded scene changes; then query `/-show/showfile/scene/NNN/name` (or `/node ,s -show/showfile/scene/NNN`). Every parameter that changed on recall is also pushed as its normal OSC message. SOURCE: GetSceneName.c:42 (`XREMOTE_TIMEOUT 9`), :67-78 (macro sends `/xremote` and `xshwctl`), :103 (`char xshwctl[32] = "/-prefs/show_control\0\0\0\0,i\0\0\0\0\0\1"` = `/-prefs/show_control ,i 1`), :205-220 (reacts to `/-show/prepos/current`, then sends the bare container address `/-show/showfile/scene/NNN` (not `/node`) and prints `r_buf+36` = the name from the desk's typed reply `,ssii`-style). VERIFIED. X32GetLib.c:782-792 reads `/-show/prepos/current` back, :905 restores it with `Xfprint(... 'i', &prepos_cur)`; X32SetLib.c:763-773, 908 (`/-prefs/show_control` restored) — VERIFIED.

### 6.3 Show/scene/snippet/cue fields

| Address | Type | Meaning |
|---|---|---|
| `/-show/showfile/show/name` | `s` | current show name |
| `/-show/showfile/show/inputs` | `i` bitmap 8 | Param-safe "Input channels": b0 Preamp, b1 Config, b2 EQ, b3 Gate&Comp, b4 Insert, b5 Groups, b6 Faders/Pan, b7 Mute |
| `/-show/showfile/show/mxsends` | `i` bitmap 16 | Param-safe mix sends: b0 Mix1 ... b15 Mix16 |
| `/-show/showfile/show/mxbuses` | `i` bitmap 8 | Param-safe mix buses: b0 Mix Sends, b1 Config, b2 EQ, b3 Comp, b4 Insert, b5 Groups, b6 Faders/Pan, b7 Mute |
| `/-show/showfile/show/console` | `i` bitmap 7 | Param-safe console: b0 Configuration, b1 Solo, b2 Routing, b3 Outpatch, b4 User In, b5 User Out, b6 Surface State |
| `/-show/showfile/show/chan16` / `chan32` | `i` bitmap 16 | Chan-safe ch 1-16 / ch 17-32 |
| `/-show/showfile/show/return` | `i` bitmap 16 | Chan-safe b0-7 Aux1-8, b8-15 FX1L-4R |
| `/-show/showfile/show/buses` | `i` bitmap 16 | Chan-safe Mix 1-16 |
| `/-show/showfile/show/lrmtxdca` | `i` bitmap 16 | b0-5 Mtx1-6, b6 L/R, b7 M/C, b8-15 DCA1-8 |
| `/-show/showfile/show/effects` | `i` bitmap 8 | b0-7 FX1-8 |
| `/-show/showfile/cue/NNN/numb` | `i` | cue number xxx.x.x encoded as int, e.g. 10327 = 103.2.7, 100 = 1.0.0 |
| `/-show/showfile/cue/NNN/name` | `s` | |
| `/-show/showfile/cue/NNN/skip` | `i` 0/1 | |
| `/-show/showfile/cue/NNN/scene` | `i` | scene index or -1 |
| `/-show/showfile/cue/NNN/bit` | `i` | snippet index or -1 |
| `/-show/showfile/cue/NNN/miditype` | `i` | 0 none, 1 program change, 2 control change, 3 note |
| `/-show/showfile/cue/NNN/midichan`, `/midipara1`, `/midipara2` | `i` | |
| `/-show/showfile/scene/NNN/name` | `s` | |
| `/-show/showfile/scene/NNN/notes` | `s` | |
| `/-show/showfile/scene/NNN/safes` | `%int` 9 bits | b1 Talkback, b2 Effects, b3 Mix Buses, b4 Chan Process, b5 Configuration, b6 Preamp(HA), b7 Output Patch, b8 Routing I/O (b0 unused); e.g. 0x106 = Routing I/O + Talkback + Effects safe |
| `/-show/showfile/scene/NNN/hasdata` | `i` 0/1 | slot occupied |
| `/-show/showfile/snippet/NNN/name` | `s` | |
| `/-show/showfile/snippet/NNN/eventtyp` | `%int` 25 bits | b0 Preamp HA, b1 Config, b2 EQ, b3 Gate&Comp, b4 Insert, b5 Groups, b6 Fader/Pan, b7 Mute, b8 Send1-8, b9 Send9-12, b10 Send13-16, b11 Send M/C+LR_SW, b12 Send Matrix, b13-20 FX1-8, b21 Config, b22 Solo, b23 Routing, b24 Out Patch |
| `/-show/showfile/snippet/NNN/channels` | `%int` 32 bits | b0 ch1 ... b31 ch32 |
| `/-show/showfile/snippet/NNN/auxbuses` | `%int` 32 bits | b0-7 Aux1-8, b8-15 FX1L-4R, b16-31 Mix1-16 |
| `/-show/showfile/snippet/NNN/maingrps` | `%int` 16 bits | b0-5 Mtx1-6, b6 L/R, b7 M/C, b8-15 DCA1-8 (UNCONFIRMED exact order of b6/b7; DOC says "bit 0: Matrix 1 ... bit 15: DCA Group 8") |
| `/-show/showfile/snippet/NNN/hasdata` | `i` 0/1 | |

SOURCES: DOC p.45-48 (all bitmaps above re-read verbatim, VERIFIED; the DOC's `/-show/showfile/show/lrmtxdca` text is `bit 0: Mtx 1 ... bit 5: Mtx 6, bit 6: L/R, bit 7: Mono/Center, bit 8: DCA group 1 ... bit 15: DCA group 8`; the snippet `maingrps` text only says `bit 0: Matrix 1 ... bit 15: DCA Group 8`); X32Show.h (`Xshow[]`, `Xscene[]` with `{S32 name, S32 notes, P32 safes, I32 hasdata}`, `Xsnippet[]` with `{S32 name, P32 eventtyp, P32 channels, P32 auxbuses, P32 maingrps, I32 hasdata}`, cue with `{I32 numb, S32 name, I32 skip, I32 scene, I32 bit, I32 miditype, I32 midichan, I32 midipara1, I32 midipara2}`).

Node forms (from real desk, DOC p.54 `/showdump` examples):
```
node~~~~,s~~/-show/showfile/show "MyShow" 0 0 0 0 0 0 0 0 0 0 "2.08"
node~~~~,s~~/-show/showfile/cue/000 100 "CCC" 0 -1 -1 0 1 0 0
node~~~~,s~~/-show/showfile/scene/001 "AAA" "aaa" %111111110 1
node~~~~,s~~/-show/showfile/snippet/000 "Aaa" 1 1 0 32768 1
```
(show line = name, inputs, mxsends, mxbuses, console, chan16, chan32, return, buses, lrmtxdca, effects, firmware string; cue = numb name skip scene bit miditype midichan midipara1 midipara2; scene = name notes safes hasdata; snippet = name eventtyp channels auxbuses maingrps hasdata — the snippet bitmaps are printed as plain ints here). `/showdump` (no args) makes the desk send all of these lines (one UDP packet each; large shows may overrun — DOC p.54). VERIFIED: DOC p.54-56 examples re-read (`scene/001 "AAA" "aaa" %110000000 1` for "Routing IO and Output Patch" safes, `%111111110` for all safes, `%000000010` for Talkback; `snippet/000 "Aaa" 1 1 0 32768 1`; `cue/000 100 "CCC" 0 -1 -1 0 1 0 0`, `cue/001 110 "Ccc" 1 1 -1 0 1 0 0` = cue 1.1.0, skip 1, scene 1, snippet -1); emulator printers X32.c:4276-4295 (SNAM), 4297-4311 (SCUE), 4313-4327 (SSCN, `Sbitmp(safes, 9)`), 4329-4341 (SSNP, plain ints).

### 6.4 Recall / store commands (argument order and types)

| Command | Tags | Args | Reply | Notes |
|---|---|---|---|---|
| `/-action/goscene` | `,i` | scene 0..99 | UNCONFIRMED; best inference from DOC p.70-71: the desk answers other `/-action/*` commands with the same address and value 0 once processed ("This command is replied with a [0], indicating the command has been processed" for selsession/delsession/selmarker/savemarker; `/-action/formatcard ,i 0` echoed before the `-urec` updates), so expect `/-action/goscene ,i 0` plus the `/xremote` burst | "Loading a saved scene; the Scene number to load is given as an int parameter ranging from 0 to 99" (DOC p.66). Equivalent to highlighting + GO on the desk; honours scene safes. |
| `/-action/gocue` | `,i` | cue slot 0..99 | | loads the cue (its scene + snippet + MIDI). |
| `/-action/gosnippet` | `,i` | snippet 0..99 | | |
| `/load` | `,si` | `"scene"`, idx 0..99 | `/load ,si scene 1` (1 = ok, 0 = fail) — VERIFIED DOC p.52 example `->X: /load ,si scene 99` / `X->: /load~,si~scene~~~[1]` | used by X32GetLib.c:855-870 (`/load ,si scene 99`). Also `"snippet"`, `"librout"`, `"libmon"` with `,si`; `"libfx"` with `,sii` (idx, fx slot 0..7); `"libchan"` with `,siii` (idx, channel 0..71, scope bits b0 HA, b1 Config, b2 Gate, b3 Comp, b4 EQ, b5 Sends). |
| `/save` | `,siss` | `"scene"`, idx, name, notes | `/save ,si scene 1` | DOC p.50 example `/save ,siss scene 45 test note`. X32.c changelog 0.30: "/save for 'scene' is a ,siss type function, not ,sissi". X32GetLib.c:801-808 (and X32SetLib.c:783-790) nevertheless send `,sissi scene 99 "PG Maillot" "X32GetLib" 0` (extra trailing int) and it works on real desks — both forms accepted (UNCONFIRMED meaning of the 5th arg; DOC p.50 shows the generic signature as `string, int, [int | string, ...]`). VERIFIED: DOC p.50-51 `/save ,siss scene 45 test note` -> `/save~~~,si~scene~~~[1]`; libchan example X32SetLib.c:906-912 `,sisi libchan <idx-1> name 0` (0-based index, channel 0 = ch01). Snippet: `,sis` (idx, name) — saved per the snippet's eventtyp/channels/auxbuses/maingrps filters; libchan `,sisi` (idx, name, channel 0-based); libfx `,sisi` (idx, name, fx slot 0..7); librout / libmon `,sis`. |
| `/delete` | `,si` | type, idx | `/delete ,si scene 1` | types: scene, snippet, libchan, libfx, librout, libmon. VERIFIED DOC p.53 `->X: /delete ,si scene 99` / `X->: /delete~,si~scene~~~[1]` |
| `/rename` | `,sis` | type, idx, new name | `/rename ,si scene 1` | VERIFIED DOC p.53 `->X: /rename ,sis scene 99 myScene` / `X->: /rename~,si~scene~~~[1]` |
| `/copy` | `,sii` | type, src idx, dst idx | `/copy ,si libchan 1` | types: scene, libchan, libfx, librout, libmon (not snippet — DOC fn.38). VERIFIED DOC p.50 example `->X: /copy ,sii libchan 45 48` / `X->: /copy~~~,si~libchan~[1]`, "Index values start at 0" |
| `/add` | `,sis` | `"cue"`, cue number int (1.0.0 -> 100, 12.5.2 -> 1252), name | `/add ,si cue 1` (UNCONFIRMED — the DOC p.50 gives no reply example for `/add`; inferred from the other commands' pattern) | captures current skip/scene/snippet/midi values (VERIFIED DOC p.50 wording) |
| `/showdump` | none | | many `node` lines | |

"The load/save/copy operation is not necessarily fully completed when the status is returned" (DOC fn.39-41). After `/load scene`, expect a burst of `/xremote` updates and a new `/-show/prepos/current`. `/-prefs/confirm_sceneload` (`i`) only affects the desk's pop-up. `/-prefs/scene_advance` (`i`) = "Scene Go Next". Undo: `/-action/undopt ,i 1` creates a checkpoint (time stored in `/-undo/time ,s "hh:mm:ss"`, empty = nothing to undo), `/-action/doundo ,i 1` performs the (single) undo (DOC p.68).

Emulator behaviour for reference (X32.c:5136-5253 `function_save`, VERIFIED: `if (strcmp(r_buf + 16, "scene") == 0) { i = 24; ... }` reads the int at byte 24, then name, then notes at the next 4-byte boundary; `function_delete` X32.c:5260-5290; `function_load` X32.c:5120-5132 is a stub that always replies `/load ,si libchan 1`): parses `,siss scene` as int idx at byte 24, then name, then notes, updates `/-show/showfile/scene/NNN/name|notes|hasdata` and pushes them to xremote clients, replies `/save ,si scene 1`; `function_delete` (5260) clears name/notes and sets hasdata 0.

---

## 7. `/-stat` and `/-prefs`

### 7.1 `/-stat` (selected; full table X32PrefStat.h:292-460, DOC p.61-68)

| Address | Type | Values |
|---|---|---|
| `/-stat/selidx` | `i` 0..71 | 0-31 Ch01-32, 32-39 Aux1-8, 40-47 Fx1L..Fx4R, 48-63 Bus1-16, 64-69 Mtx1-6, 70 LR, 71 M/C (X32.c:410 `Sselidx[]`) |
| `/-stat/chfaderbank` | `i` 0..3 | 0 CH1-16, 1 CH17-32, 2 Aux/USB/FX rtn, 3 Bus masters |
| `/-stat/grpfaderbank` | `i` 0..5 | 0 DCA1-8, 1 BUS1-8, 2 BUS9-16, 3 MTX1-6+M/C, 5 Ch1-16 (compact/producer) |
| `/-stat/sendsonfader` | `i` 0/1 | |
| `/-stat/bussendbank` | `i` 0..3 | rotaries -> bus 1-4 / 5-8 / 9-12 / 13-16 |
| `/-stat/eqband` | `i` 0..5 | |
| `/-stat/solo` | `i` 0/1 | read-only: 1 = at least one solo active (state of CLEAR SOLO LED) |
| `/-stat/keysolo` | `i` 0/1 | |
| `/-stat/solosw/[01-80]` | `i` 0/1 | per-strip solo: 01-32 ch, 33-40 auxin, 41-48 fxrtn, 49-64 bus, 65-70 mtx, 71 L/R, 72 M/C, 73-80 DCA1-8 |
| `/-stat/userbank` | `i` 0..2 | |
| `/-stat/autosave` | `i` 0/1 | |
| `/-stat/lock` | `i` | 0 unlocked, 1 locked, **2 = shutdown the console** (X32.c:4586 treats `/-stat/lock ,i 2` as shutdown) |
| `/-stat/usbmounted` | `i` 0/1 | |
| `/-stat/remote` | `i` 0/1 | 1 = DAW remote mode |
| `/-stat/rtamodeeq`, `/-stat/rtamodegeq` | `i` | 0 BAR, 1 SPEC |
| `/-stat/rtaeqpre`, `/-stat/rtageqpost` | `i` 0/1 | |
| `/-stat/rtasource` | `i` 0..170 | 0-31 ch pre-EQ, 32-39 aux, 40-47 fxrtn, 48-63 bus, 64-69 mtx, 70 L/R, 71 Mono, 72 Monitor; 98-129 ch post-EQ, 130-137 aux, 138-145 fxrtn, 146-161 bus, 162-167 mtx, 168 L/R, 169 Mono, 170 Monitor |
| `/-stat/xcardtype` | `i` 0..11 | 0 None, 1 X-UF, 2 X-USB, 3 X-DANTE, 4 X-ADAT, 5 X-MADI, 6 DN32-USB, 7 DN32-DANTE, 8 DN32-ADAT, 9 DN32-MADI, 10 X-Live, 11 X-WSG |
| `/-stat/xcardsync` | `i` 0/1 | |
| `/-stat/geqonfdr`, `/-stat/geqpos`, `/-stat/dcaspill` | `i` | see §2.4, §5.2 |
| `/-stat/screen/screen` | `i` 0..10 | CHAN(HOME), METERS, ROUTE, SETUP, LIB, FX, MON, USB, SCENE, ASSIGN, LOCK(get only) |
| `/-stat/screen/mutegrp`, `/-stat/screen/utils` | `i` 0/1 | |
| `/-stat/screen/{CHAN,METER,ROUTE,SETUP,LIB,FX,MON,USB,SCENE,ASSIGN}/page` | `i` | page lists X32.c:420-432 (e.g. SCENE: 0 HOME(cues), 1 SCENES, 2 BITS(snippets), 3 PARSAFE, 4 CHNSAFE, 5 MIDI; FX: 0 HOME, 1-8 FX1-8) |
| `/-stat/aes50/state` | `%int` | b0 A audio err, b1 B audio err, b2 A aux err, b3 B aux err, b4 lock |
| `/-stat/aes50/A`, `/-stat/aes50/B` | `s` | 4-char chain letters (A=S16, B=X32C, C=X32, ..., P=SD16, Q=SD16B, R=SD8, ...) + 6 preamp-type chars |
| `/-stat/tape/state` | `i` 0..6 | STOP, PPAUSE, PLAY, RPAUSE, RECORD, FF, REW |
| `/-stat/tape/file` (`s`), `/-stat/tape/etime`, `/-stat/tape/rtime` (`i` s) | | USB recorder |
| `/-stat/osc/on` | `i` 0/1 | oscillator |
| `/-stat/talk/A`, `/-stat/talk/B` | `i` 0/1 | talkback A/B active (write to key talkback) |
| `/-stat/urec/state` | `i` 0..3 | STOP, PPAUSE, PLAY, REC (X-Live) |
| `/-stat/urec/etime`, `/-stat/urec/rtime` | `i` ms | 0..86399999 |
| `/-stat/userpar/[id]/value` | `i` | user-assign encoder/button values (ids 1-24 buttons, 25-36 encoders) |

FW 4.06 quirk: `/node ,s -stat` output is malformed (ends after `geqpos`, then a second `/-stat <dcaspill>` fragment) — DOC fn.48. VERIFIED: the whole §7.1 table re-read against DOC p.61-66 (selidx, chfaderbank, grpfaderbank (DOC also lists `4: TBD`), sendsonfader, bussendbank, eqband, solo, keysolo, userbank, autosave, lock {0 Unlocked, 1 Locked, 2 Shutdown}, usbmounted, remote, rtamodeeq/geq, rtaeqpre, rtageqpost, rtasource 0..170, xcardtype 0..11, xcardsync, geqonfdr, geqpos, dcaspill, screen/* pages, aes50/state bits + chain letters, solosw 01-80, talk/A|B, osc/on, tape/state 0..6, tape/file|etime|rtime, urec/state 0..3, userpar ids) and X32PrefStat.h:292-316 (`/-stat/lock {I32}`, `/-stat/rtasource {I32}`, `/-stat/xcardtype {I32}`, `/-stat/geqpos {I32}`, `/-stat/dcaspill {I32}`), X32.c:4586 (lock 2 = shutdown), X32.c:410-420 `Sselidx[]`, :421-433 screen page lists.

### 7.2 `/-prefs` (selected; full table X32PrefStat.h:13-289, DOC p.56-60)

| Address | Type | Values |
|---|---|---|
| `/-prefs/name` | `s` | console name (default e.g. "X32-02-4A-53"); also reported by `/xinfo` |
| `/-prefs/style` | `s` | prefs name |
| `/-prefs/bright`, `/lcdcont`, `/ledbright`, `/lamp` | `f` | linf [10,100,5] / [0,100,2] / [10,100,5] / [10,100,10] |
| `/-prefs/lampon` | `i` 0/1 | |
| `/-prefs/clockrate` | `i` | 0 48K, 1 44K1 |
| `/-prefs/clocksource` | `i` | 0 INT, 1 AES50A, 2 AES50B, 3 Exp Card |
| `/-prefs/confirm_general`, `/confirm_overwrite`, `/confirm_sceneload` | `i` 0/1 | |
| `/-prefs/viewrtn`, `/selfollowsbank`, `/scene_advance`, `/safe_masterlevels`, `/autosel` | `i` 0/1 | scene_advance = "Scene Go Next"; safe_masterlevels = "Safe Main Levels" |
| `/-prefs/haflags` | `%int` | b0 Lock stagebox, b1 X32 HA gain split, b2 AES50A HA split, b3 AES50B HA split |
| `/-prefs/show_control` | `i` | 0 CUES, 1 SCENES, 2 SNIPPETS |
| `/-prefs/clockmode` | `i` | 0 24h, 1 12h |
| `/-prefs/hardmute`, `/-prefs/dcamute` | `i` 0/1 | Mute System (see §5.1) |
| `/-prefs/invertmutes` | `i` | 0 NORM, 1 INV |
| `/-prefs/rec_control` | `i` | 0 USB, 1 XLIVE |
| `/-prefs/remote/enable` | `i` 0/1 | DAW remote |
| `/-prefs/remote/protocol` | `i` | 0 MC, 1 HUI, 2 CC |
| `/-prefs/remote/port` | `i` | 0 MIDI, 1 CARD, 2 RTP |
| `/-prefs/remote/ioenable` | `%int` | b0 MIDI in/out, b1 Card MIDI, b2 RTP MIDI, b3 Rx PC, b4 Tx PC, b5 Rx fader CC, b6 Tx fader CC, b7 Rx mute CC, b8 Tx mute CC, b9 Rx pan CC, b10 Tx pan CC, b11 OSC over MIDI sysex, b12 XTouch over MIDI, b13 XTouch over Ethernet |
| `/-prefs/card/UFifc` (`i` 0 FW, 1 USB), `/UFmode` (0 32/32, 1 16/16, 2 32/8, 3 8/32), `/USBmode` (+4 8/8, 5 2/2), `/ADATwc` (0 IN, 1 OUT), `/ADATsync` (0 WC, 1-4 ADAT1-4), `/MADImode` (0 56, 1 64), `/MADIin` (0 1-32 .. 4 33-64), `/MADIout` (0 OFF, 1 1-32 .. 5 33-64), `/MADIsrc` (0 OFF, 1 OPT, 2 COAX, 3 BOTH), `/URECtracks` (0 32Ch, 1 16Ch, 2 8Ch), `/URECplayb` (0 SD, 1 USB), `/URECrout` (0 REC, 1 PLAY, 2 AUTO), `/URECsdsel` (0 SD1, 1 SD2) | `i` | |
| `/-prefs/rta/visibility` | `i` 0..12 | OFF, 25%, 30%, 35%, 40%, 45%, 50%, 55%, 60%, 65%, 70%, 75%, 80% |
| `/-prefs/rta/gain` | `f` | linf [0, 60] dB in 6 dB steps |
| `/-prefs/rta/autogain` | `i` 0/1 | |
| `/-prefs/rta/source` | `i` 0..73 | 0 none, 1 Monitor, 2-33 Ch01-32, 34-41 Aux1-8, 42-49 FX1L-4R, 50-65 Bus1-16, 66-71 Mtx1-6, 72 Main, 73 Mono |
| `/-prefs/rta/pos` | `i` | 0 PRE, 1 POST |
| `/-prefs/rta/mode` | `i` | 0 BAR, 1 SPEC |
| `/-prefs/rta/options` | `%int` | b0 Pre EQ, b1 Spectrograph, b2 Use RTA source, b3 Post GEQ, b4 Spectrograph, b5 Solo priority |
| `/-prefs/rta/det` | `i` | 0 RMS, 1 PEAK |
| `/-prefs/rta/decay` | `f` | logf [0.25, 16, 19] |
| `/-prefs/rta/peakhold` | `i` 0..8 | OFF, 1..8 |
| `/-prefs/ip/dhcp` (`i`), `/-prefs/ip/addr/[0-3]`, `/mask/[0-3]`, `/gateway/[0-3]` (`i`) | | "Use with caution" |
| `/-prefs/iQ/[01-16]/iQmodel|iQeqset|iQsound` | `i` | Turbosound iQ |
| `/-prefs/key/layout` (`i` 0 QWERTY, 1 QWERTZ, 2 AZERTY, 3 ABCDEF), `/-prefs/key/00..99` (`s`) | | FW 4.0+ |
| `/-prefs/fastFaders` | `i` 0/1 | (added by verifier) DOC p.57: "MR32[R] only? Int [0,1] representing the update of faders at startup"; not in the emulator |

VERIFIED: §7.2 re-read against DOC p.56-60 (style/bright/lcdcont/ledbright/lamp ranges `[10,100,5] [0,100,2] [10,100,5] [10,100,10]`, lampon, clockrate {48K,44K1}, clocksource {INT,AES50A,AES50B,Exp Card}, confirm_*, viewrtn, selfollowsbank, scene_advance "Scene Go Next", safe_masterlevels "Safe Main Levels", haflags bits, autosel, show_control {CUES,SCENES,SNIPPETS}, clockmode {24h,12h}, hardmute/dcamute/invertmutes labels, name default "X32-02-4A-53" also in `/xinfo`, rec_control {USB,XLIVE}, remote/* incl. ioenable bits 0-13, card/* lists, rta/* incl. visibility 13 entries, gain `[0,60,6]`, source 0..73, options bits, det, decay `[0.25,16,19]`, peakhold OFF+1..8, ip/*, iQ/*, key/*) and X32PrefStat.h:13-44 (order style, bright, lcdcont, ledbright, lamp, lampon, clockrate `PRrate`, clocksource `Psource`, ..., show_control `PSCont`, clockmode `Pclkmod`, hardmute, dcamute, invertmutes `Pinvmut`, name, rec_control `Purrctl`, remote/*), X32.c:389-409 (the `P*` token lists).

---

## 8. `/-action` (full list; all args `,i` unless noted)

| Address | Arg | Effect |
|---|---|---|
| `/-action/setip` | `i` | 1 = reset network parameters (dangerous) |
| `/-action/setclock` | `s` | set clock |
| `/-action/initall` | `i` | 1 = initialise console (dangerous) |
| `/-action/initlib` | `i` | 1 = init libraries |
| `/-action/initshow` | `i` | 1 = init show data |
| `/-action/savestate` | `i` | 1 = save state (before power off) |
| `/-action/undopt` | `i` | 1 = create undo checkpoint (sets `/-undo/time`) |
| `/-action/doundo` | `i` | 1 = undo |
| `/-action/playtrack` | `i` | USB recorder: -1 previous, 0 not playing, 1 next |
| `/-action/newscreen` | `i` | >0 = refresh LCD |
| `/-action/clearsolo` | `i` | 1 = clear all solos (CLEAR SOLO button) |
| `/-action/setprebus` | `i` | 0 |
| `/-action/setsrate` | `i` | 0 48 kHz, 1 44.1 kHz |
| `/-action/setrtasrc` | `i` | RTA source channel # (0-31 ch, 32-39 aux, 40-47 fxrtn, 48-63 bus, 64-69 mtx, 70 L/R, 71 M/C, 72 Monitor) |
| `/-action/recselect` | `i` | select/execute USB record n (1..n) |
| `/-action/gocue` | `i` 0..99 | load cue |
| `/-action/goscene` | `i` 0..99 | load scene |
| `/-action/gosnippet` | `i` 0..99 | load snippet |
| `/-action/selsession`, `/delsession` | `i` 1..100 | X-Live sessions |
| `/-action/selmarker`, `/delmarker`, `/savemarker` | `i` 1..100 | X-Live markers |
| `/-action/addmarker` | `i` 0/1 | |
| `/-action/setposition` | `i` ms | X-Live position 0..86399999 (emulator names it `selposition`) |
| `/-action/clearalert` | `i` 0/1 | |
| `/-action/formatcard` | `i` | format active SD card (dangerous) |

SOURCES: X32PrefStat.h:463-497 `Xaction[]` (all `{I32}` except `setclock {S32}`; the emulator table misspells `platrack` for `playtrack`, names `selposition` for the DOC's `setposition`, and lists `newscreen`/`undopt` twice); DOC p.69-71. VERIFIED (DOC p.69-71 re-read: setrtasrc's printed ranges are garbled in the PDF ("32-63: Ch 33-64, 64-47: Aux in/USB") — the table above uses the consistent `/-stat/selidx` mapping; formatcard example shows the desk echoing `/-action/formatcard ,i 0` then `/-urec/sd1state ,i 3`, `/-urec/sd1info ,s "Formatting SD Card.."`, `sd1state 1`, `sd1info "4 GB - 44m, 1s"`).

---

## 9. Talkback, solo, monitor (brief)

* Talkback: `/config/talk/enable` (`i`), `/config/talk/source` (`i` 0 INT, 1 EXT), `/config/talk/A|B/level` (`f` **`level [-90, +10, 161]` dB, the same 161-step pseudo-log fader/level law as `/config/solo/level` and `/config/osc/level`** — VERIFIED-CORRECTED: the earlier text said `linf [-40, 0, 1]`, which is actually `/config/solo/dimatt` (DOC p.20-21 column layout misread; DOC appendix p.166 "Solo Dim Att — 41 lin scale values — [-40, 0, 1.0]"); the emulator prints talk level with `Slevel()` X32.c:3846-3851 and stores it as plain `{F32}` X32CfgMain.h:92/97), `/config/talk/A|B/dim` (`i`), `/config/talk/A|B/latch` (`i`), `/config/talk/A|B/destmap` (`%int [0, 262143]` 18 bits: b0-15 Mix1-16, b16 L/R, b17 M/C — UNCONFIRMED order, DOC p.21 only says "(18 bits bitmap)"). Key TB: `/-stat/talk/A ,i 1` / `/-stat/talk/B ,i 1`. SOURCE: X32CfgMain.h:88-100; DOC p.20-21; X32.c:3842-3851 node printer (`CTALK`: ON/OFF, INT/EXT; `CTALKAB`: `Slevel(level)`, dim, latch, `Sbitmp(destmap, 18)`). VERIFIED.
* Solo/monitor: `/config/solo/level` (`f` level), `/config/solo/source` (`i` 0 OFF, 1 LR, 2 LR+C, 3 LRPFL, 4 LRAFL, 5 AUX56, 6 AUX78), `/config/solo/sourcetrim` (`f` linf [-18, 18, 0.5]), `/config/solo/chmode|busmode|dcamode` (`i` 0 PFL, 1 AFL), `/config/solo/exclusive|followsel|followsolo|dim|mono|delay|masterctrl|mute|dimpfl` (`i`), `/config/solo/dimatt` (`f` linf [-40, 0]), `/config/solo/delaytime` (`f` linf [0.3, 500, 0.1] ms). `/config/mono/mode` (`i` 0 LR+M, 1 LCR), `/config/mono/link` (`i`). Per-strip solo: `/-stat/solosw/NN`; any-solo flag `/-stat/solo`; clear: `/-action/clearsolo ,i 1`. SOURCE: X32CfgMain.h:66-88; DOC p.20; X32.c:3823-3841 node printer (`Slevel(level)`, `Ssource[source]`, `Slinf(sourcetrim,-18,18,1)`, 3x PFL/AFL, 3x ON/OFF, `Slinf(dimatt,-40,0,0)`, 3x ON/OFF, `Slinf(delaytime,0.3,500,1)`, 3x ON/OFF — 17 values in the order level, source, sourcetrim, chmode, busmode, dcamode, exclusive, followsel, followsolo, dimatt, dim, mono, delay, delaytime, masterctrl, mute, dimpfl). VERIFIED (DOC p.20: solo/level `level [-90.0...10.0 (+10 dB), 161]`, source `[0...6] {OFF, LR, LR+C, LRPFL, LRAFL, AUX56, AUX78}`, sourcetrim `linf [-18, 18, 0.5]`, dimatt `linf [-40, 0, 1]`, delaytime `linf [0.3, 500, 0.1]`; X32Fx.h:10 `Ssource[]`).
* Oscillator: `/config/osc/level` (`f`), `/f1`, `/f2` (`f` logf [20, 20000, 121]), `/fsel` (`i` 0 F1, 1 F2), `/type` (`i` 0 SINE, 1 PINK, 2 WHITE), `/dest` (`i` 0..25: MixBus1-16, L, R, L+R, M/C, Matrix1-6); on/off `/-stat/osc/on`. VERIFIED (DOC p.21: level `level [-90..10, 161]`, f1/f2 `logf [20, 20000, 121]`, fsel {F1,F2}, type {SINE,PINK,WHITE}, dest `[0...25] {MixBus1...16, L, R, L+R, M/C, Matrix1...6}`; X32.c:3852-3859 `COSC` printer; X32Fx.h:11 `Sosct[]`).

---

## 10. Card / USB / X-Live (brief)

* Card identity: `/-stat/xcardtype` (§7.1), `/-stat/xcardsync`, `/-prefs/card/*` (§7.2).
* USB stick recorder: `/-stat/tape/state|file|etime|rtime`, `/config/tape/gainL|gainR` (`f` linf [-6, 24, 0.5] dB), `/config/tape/autoplay` (`i`), `/-action/playtrack`, `/-action/recselect`; browsing: `/-usb/path` (`s`), `/-usb/title` (`s`), `/-usb/dir/maxpos`, `/-usb/dir/dirpos` (`i`), `/-usb/dir/NNN/name` (`s`), `/-usb/dir/NNN/type` (`s`). SOURCE: X32Misc.h:13-270; DOC p.60. VERIFIED against DOC p.60 (addresses `/-usb/path`, `/-usb/title`, `/-usb/dir/dirpos`, `/-usb/dir/maxpos`, `/-usb/dir/001...999/type|name`; note the emulator spells the two counters `/-usb/maxpos` and `/-usb/dirpos` (X32Misc.h:16-17) — treat the DOC spelling as the real-desk one) and DOC p.66 tape state list; `/config/tape/gainL|R linf [-6, 24, 0.5]` and `autoplay` VERIFIED DOC p.23.
* X-Live: `/-stat/urec/state` (0 STOP, 1 PPAUSE, 2 PLAY, 3 REC — write to transport), `/-stat/urec/etime|rtime` (ms), `/-urec/sessionmax`, `/markermax`, `/sessionlen`, `/sessionpos`, `/markerpos` (`i`), `/-urec/batterystate` (0 NONE, 1 GOOD, 2 LOW), `/-urec/srate` (0 44.1k, 1 48k), `/-urec/tracks` (0/8/16/32), `/-urec/sessionspan`, `/sessionoffs`, `/-urec/sd1state|sd2state` (0 NONE, 1 READY, 2 PROTECT, 3 ERROR), `/-urec/sd1info|sd2info` (`s`), `/-urec/errormessage` (`s`), `/-urec/errorcode` (`i`), `/-urec/session/NNN/name` (`s`), `/-urec/marker/NNN/time` (`i`). Playback flips `/config/routing/routswitch` to 1 and pushes `PLAY/*` values. SOURCE: X32PrefStat.h:499-518 (`batterystate {E32} Ubat`, `sd1state|sd2state {E32} Usdc`, rest `{I32}`/`{S32}`), X32.c:406-407 (`Ubat`, `Usdc`); DOC p.72-73. VERIFIED (DOC p.72-73 re-read: sessionmax/markermax 0..100 with 0 = none, sessionlen ms `[0...86399999]`, batterystate {NONE,GOOD,LOW}, srate 0 = 44.1k / 1 = 48k, tracks {0,8,16,32}, sessionspan 0..3 {No spanning, Span 1 of 2, Span 2 of 2, Jump to 2/2}, sessionoffs ms, sd1state/sd2state {NONE,READY,PROTECT,ERROR}, sd1info/sd2info 32-char strings; `/-stat/urec/state` {STOP,PPAUSE,PLAY,REC} 0..3 with ~80 ms etime/rtime updates while recording).

---

## 11. Panic / "silence everything"

There is **no single "mute all" OSC control** on the X32 (confirmed by absence in every command table: X32CfgMain.h, X32PrefStat.h, DOC). VERIFIED (re-grepped X32CfgMain.h, X32PrefStat.h Xaction[] and the DOC's command index / node list for any `mute`/`muteall`/`panic` control: only `/config/mute/1-6`, per-strip `mix/on`, `grp/mute` and the DCA `on` exist). Practical options, fastest first:

1. **Main + mono + all buses + all matrices masters** (26 messages, all `,i 0`):
   `/main/st/mix/on 0`, `/main/m/mix/on 0`, `/bus/01..16/mix/on 0`, `/mtx/01..06/mix/on 0`. This silences every mix output **that is tapped POST or `+M`** (see §4.7). It does **not** silence outputs whose `/outputs/*/NN/pos` is IN/LC, <-EQ, EQ-> or PRE (no `+M`), nor **DirectOut** taps (`src` 26..73) which bypass the bus masters entirely, nor AES50/Card blocks routed from inputs (AN/A/B/CARD/UIN blocks) or `AuxIN1-6/TB`.
2. **Mute groups** (6 messages): pre-assign every input strip and the mix masters to one mute group (`/ch/NN/grp/mute`, `/auxin/NN/grp/mute`, `/fxrtn/NN/grp/mute`, `/bus/NN/grp/mute`, `/mtx/NN/grp/mute`, `/main/st/grp/mute`, `/main/m/grp/mute` with the same bit set), then `/config/mute/K ,i 1`. Fewest messages at panic time, but requires the assignment to already exist and to survive scene recalls (Groups are covered by the "Groups" param-safe bit / Configuration safes). Mute groups mute post-fader sends only unless `/-prefs/hardmute = 1`.
3. **Belt and braces**: also send `/ch/01..32/mix/on 0` and `/auxin/01..08/mix/on 0`, `/fxrtn/01..08/mix/on 0` (48 more messages) to kill DirectOut and PRE taps that carry channels, and `/-action/clearsolo ,i 1` (solo bus feeds monitor/AUX 5-8 when `/config/solo/source` is AUX56/AUX78).
4. Nuclear (not recommended live): set `/outputs/main/NN/src ,i 0` (OFF) on all 16, plus aux/p16/aes/rec, plus AES50/CARD blocks; slower to restore.
5. DCA fader/`/dca/N/on 0` only affects members' main contribution unless `/-prefs/dcamute = 1`.

Bus -> AES50 path reminder (does muting the bus mute the AES50 channel?): yes **iff** the tap carrying it (`/outputs/main/NN/src = 3+bus`) has `pos` = 8 POST or a `+M` variant (1, 3, 5, 7); the AES50A/B/CARD block then just copies OUT1-8/OUT9-16 (block values 20/21) — there is no separate mute on the AES50 side. Default factory tap position for the OUT taps is POST — UNCONFIRMED default value (read `/outputs/main/NN/pos`; the emulator initialises every `pos` to 0 = IN/LC, which is just its zero-init, not the desk default; general knowledge says factory Out 1-16 = MixBus 1-14 + Main L/R, all POST, but no source in this research states it).

Bulk sending: the desk accepts many UDP messages back to back (Maillot's tools insert short sleeps between bursts, e.g. `microsleep(100)` in X32GEQ2cpy.c:25/35, i.e. `usleep(100)` = 100 us on POSIX / `Sleep(100/100)` = 1 ms on Windows). OSC bundles (`#bundle`): VERIFIED absent — `grep -i bundle` over the DOC 4.06 text returns nothing, and the emulator's dispatcher X32.c:490-528 `Xheader[]` (keyed on the first 4 address bytes: `/shu /inf /xin /sta /xre /nod /\0 /con /mai /-pr /-st /-ur /ch/ /aux /fxr /bus /mtx /dca /fx/ /out /hea /met /-ha /ins /-sh /ren /cop /add /loa /sav /del /uns /-us /und /-ac /-li /sho`) has no `#bun` entry; still UNCONFIRMED for the real desk — assume unsupported and send one message per UDP packet. Where a multi-value write exists (e.g. `/fx/N/par ,ffff…` §1.6) use it instead of a bundle.

---

## 12. Quick reference: node-string printers for this area (emulator, X32.c function_node)

```c
case CHIN:   " ON"/" OFF", Sdpos[pos] {" PRE"," POST"}, Sinsel[sel]           // /ch/NN/insert, /bus/NN/insert, ...
case CHGRP:  Sbitmp(dca, 8), Sbitmp(mute, 6)                                 // /ch/NN/grp -> "%00000000 %000000"
case FXTYP1: Sfxtyp1[type]                                                   // /fx/1 -> "HALL"
case FXSRC:  Sfxsrc[l], Sfxsrc[r]                                            // /fx/1/source -> "INS INS"
case FXPAR1: GetFxPar1(...)  / case FXPAR2: GetFxPar1(..., type + _1_PIT + 2) // /fx/N/par -> display-unit tokens
case FXTYP2: Sfxtyp2[type]                                                   // /fx/5 -> "GEQ2"
case OMAIN:  Sint(src), Smpos[pos], " ON"/" OFF"                             // /outputs/main/NN -> "4 POST OFF"
case OMAIND: " ON"/" OFF", Slinf(time, 0.3, 500., 1)                         // /outputs/main/NN/delay
case CROUTSW/CROUTIN/CROUTPLAY/CROUTAC/CROUTOT: routing tokens (see §4)
case SSCN:   "\"name\" \"notes\" %safes(9) hasdata"                          // /-show/showfile/scene/NNN
case SCUE:   numb "\"name\"" skip scene bit miditype midichan midipara1 midipara2
case SSNP:   "\"name\"" eventtyp channels auxbuses maingrps hasdata (ints)
case SNAM:   "\"show\"" inputs mxsends mxbuses console chan16 chan32 return buses lrmtxdca effects "\"4.06\""
case STAT:   Sselidx[selidx] chfaderbank grpfaderbank ON/OFF bussendbank eqband ON/OFF ON/OFF userbank ...
case UROUO:  48 ints / case UROUI: 32 ints                                   // /config/userrout/out|in
```
(X32.c:3959, 3989, 4035-4050, 4051-4074, 3860-3880, 4276-4345, 4166-4190, 4416-4425.)

---

## 13. Consolidated list of UNCONFIRMED items (with best inference)

1. GEQ band-gain quantization on the desk (likely 0.5 dB display steps; wire value is a free float).
2. Exact text formatting of GEQ values in `/node fx/N/par` replies (desk: one decimal dB like `0.0`, `-3.5`; sign prefix unknown).
3. The 31 GEQ centre frequencies are inferred from the ISO 1/3-octave series + the `/-stat/geqpos` "fader 5 = 50 Hz ... 250 Hz" example; not printed in any C source (web-corroborated as the standard ISO 31-band set; see §2.3). NEW: whether the `geqpos` start field is 0-based (range 0x00..0x17) or 1-based (DOC example) is UNCONFIRMED.
4. Encoding of the int returned by `/-insert/fxNL|R` and `/-insert/auxN` (likely 0..71 strip index, or -1/none).
5. Desk behaviour over OSC when a second strip selects an insert point already in use (UI refuses/warns; OSC likely last-write-wins with the previous owner reset) — verify by re-reading `/-insert/*` after a write.
6. Whether selecting `FXnL` on a stereo strip (main/st, stereo-linked bus) auto-inserts the `FXnR` side and what `sel` reads back.
7. RESOLVED (see §4.6): the real desk's `/node ,s config/routing/OUT` token order is 1-4, 5-8, 9-12, 13-16 (order of real `.scn` files parsed by SetSceneParse.c:441-457); the emulator's 1-4, 9-12, 5-8, 13-16 is an emulator quirk. Reading the four addresses individually remains the safe choice.
8. Contents of the `AUX/CR` and `AUX/TB` 4-channel blocks in the OUT lists.
9. Meaning of the optional 5th `/save ,sissi scene` int used by X32GetLib.c (`0`); `,siss` per DOC is sufficient.
10. Reply to `/-action/goscene` on a real desk (emulator echoes the int); functional difference between `/-action/goscene` and `/load ,si scene` (both recall with safes; `/load` returns a 0/1 status and is what Maillot's tools use).
11. `/-show/prepos/current` documented range "[1-099]" vs observed 0-based scene numbers (tools use it 0-based; scene 0 exists).
12. Exact bit order of `/config/talk/A|B/destmap` (18 bits: 16 buses + L/R + M/C) and of `snippet/NNN/maingrps` bits 6/7.
13. Factory default `/outputs/main/NN/pos` (assumed POST).
14. Precise semantics of `/-prefs/hardmute` and `/-prefs/dcamute` (summarised from the user manual via secondary sources; now corroborated by two more, see §5.1 — still not verifiable from code).
15. (added) Reply to `/add ,sis cue ...` — the DOC gives no example; assume the `/add ,si cue 1` pattern.
16. (added) `/-stat/geqpos` start-band base (0- or 1-based), see §2.3/§2.4.

---

## Verification log

Verifier pass 2026-09-19. Method: fresh shallow clone of `pmaillot/X32-Behringer` (master) and a fresh `pdftotext -layout` of the 4.06-09 PDF (x32ram.com mirror, fetched with a browser UA; the plain fetch returns 403). Every enum table, formula, bit layout and address in this file was re-derived from those copies; the `Sflookup`/`Sflookup2` tables were diffed programmatically (0 differences). Line numbers quoted as "VERIFIED" refer to that clone.

### Corrections (VERIFIED-CORRECTED)

1. **§9 `/config/talk/A|B/level`** — was `linf [-40, 0, 1] dB "— DOC"`. Wrong: that range is `/config/solo/dimatt` (DOC p.20-21 columns misread; DOC appendix p.166 "Solo Dim Att [-40, 0, 1.0]"). Talk level is `level [-90, +10, 161]` (161-step fader/level law): DOC p.21 right-hand column, emulator X32.c:3846-3851 `case CTALKAB: Slevel(...)`, X32CfgMain.h:92/97 `{F32}`.
2. **§2.6 P1A / P1A2 parameter count and DOC list** — was "P1A par 1..10 ... P1A2 = two copies (par 11..20)" and "DOC's P1A Lo Freq list is [0, 30, 60, 100]". P1A has 11 parameters (DOC p.106 table 1..11; `Sflookup` row `iffifffifii` is 11 chars), P1A2 has 22 (B side = par 12..22; `iffifffifiiiffifffifii`). The DOC's Lo Freq list is `[20, 30, 60, 100]` (only X32SetScene.h:5184 `l_plfreq[]` spells the first token "0"). Parameter names corrected to the DOC's (Active, Gain, Lo Boost, Lo Freq, Lo Att, Hi Width, Hi Boost, Hi Freq, Alt Sel, Hi Freq, Transformer).
3. **§4.6 `/node config/routing/OUT` token order** — was UNCONFIRMED. Resolved: real-desk `.scn` files (verbatim node dumps) are parsed by SetSceneParse.c:441-457 in the order 1-4, 5-8, 9-12, 13-16, so that is the desk's order; the emulator's 1-4, 9-12, 5-8, 13-16 (X32.c:3876-3881) is an emulator quirk.
4. **Page references** — GEQ2/GEQ appendix table is DOC p.104 (was p.101); P1A/PQ5 tables p.106-108 (was p.103-104); FX long-name/flags table p.166-167 (was "p.~152"); `/-insert` is p.43 (was p.42); `/-action` is p.69-71 (was p.66-68); `-urec` p.72-73 (was p.69-70); show fields p.45-48 (was p.44-48).
5. **Line references** — `/insert/aux/1..6` is X32Misc.h:280-285 (was 283-288); `/-ha/NN/index` X32Misc.h:287-328 (was 290-330); `X32GetLib.c` `/save ,sissi` is at 801-808 (was 855); `/config/mute` X32CfgMain.h:55-61 (was 56-62); GetSceneName handler 209-214 (was 211-215).
6. **§6.4 `/add` reply** — the reply `/add ,si cue 1` was stated as fact; the DOC gives no example, so it is now marked UNCONFIRMED (inferred pattern).

### Material facts added by the verifier

* §1.6: the real desk accepts the whole FX parameter block in ONE message `/fx/N/par ,fff…(32|64) v1…vN` (SetSceneParse.c:2528-2549 + fxparse.c:119-132, used by X32SetScene on real desks). Useful for GEQ writes and as the bundle substitute.
* §2.3/§2.4: the DOC's `/-stat/geqpos` description is internally inconsistent (start range 0x00..0x17 = 0-based vs. example "5 -> 50 Hz" = 1-based); flagged as new UNCONFIRMED item 16.
* §6.4: DOC p.70-71 documents that several `/-action/*` commands are answered with the same address and value 0 once processed — best inference for `/-action/goscene`'s reply.
* §7.2: `/-prefs/fastFaders` (DOC p.57) added.
* §8: emulator `Xaction[]` typos (`platrack`, `selposition`, duplicate `newscreen`/`undopt`) noted so they are not mistaken for desk addresses.
* §4.2: emulator `case CROUTSW` prints the wrong token list (AN1-8/AN9-16 instead of REC/PLAY) — emulator quirk.
* §4.6: emulator printer copy `Srouto1[]` has a typo `B33-46` at index 14 (X32Fx.h:32).
* §10: emulator spells the USB counters `/-usb/maxpos`, `/-usb/dirpos`; DOC (real desk) `/-usb/dir/maxpos`, `/-usb/dir/dirpos`.
* §5.1/§5.2: real-desk `/showdump` examples (DOC p.54-56) independently confirm MSB-first `%bitmap` printing and the bit assignments for scene `safes` (bit 8 Routing I/O, bit 7 Out Patch, bit 1 Talkback) and snippet `maingrps` (bit 15 = DCA 8).
* §11: "bundle" confirmed absent from the DOC text (grep) and from the emulator dispatcher table (X32.c:490-528, full key list quoted).

### Confirmed unchanged (VERIFIED, spot list)

`/fx/[1-4]/type` 0..60 and `/fx/[5-8]/type` 0..33 token order (4 independent copies + DOC); `/fx/[1-4]/source/l|r` 0..17; all 61+34 `Sflookup` rows; GEQ2/GEQ par layout 1-31/32/33-63/64 and `f = (dB+15)/30`; `insert/sel` 0..22 and `insert/pos` {PRE, POST}; `/-insert/*` semantics; routing IN/PLAY 0..23, IN/AUX 0..15, AES50A/B/CARD 0..35, OUT lists A/B 0..35; `/outputs/*/NN/src` 0..76 and `pos` 0..8; userrout out 0..208 / in 0..168; `/ch/NN/config/source` 0..64; `/config/mute/1-6`, `grp/mute` 6-bit, `grp/dca` 8-bit, `Sbitmp` MSB-first; DCA addresses; show/cue/scene/snippet fields and bitmaps; `/-show/prepos/current` usage; `/load ,si`, `/save ,siss`, `/copy ,sii`, `/rename ,sis`, `/delete ,si` with their `,si <type> [1]` replies; full `/-stat`, `/-prefs`, `/-action`, `/-urec`, `/-usb` tables; solo/osc/tape/routswitch enums; `/-stat/lock ,i 2` = shutdown; no mute-all control.

### Still UNCONFIRMED after this pass

See §13 items 1, 2, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16 (items 3 and 14 are corroborated by secondary web sources; item 7 is resolved). The musictribe forum archives that might have settled the `/-insert` encoding and the `goscene` reply are no longer online (DNS failure on 2026-09-19).
