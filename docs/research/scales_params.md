# X32/M32 OSC — Parameter map & value scaling (channels / buses / main / headamp)

Research date: 2026-09-19. Ground truth is Patrick-Gilles Maillot's C code (X32 emulator `X32.c` + parameter
tables `X32*.h`, scene parser `SetSceneParse.c` + `X32lib/Xscene2X.c`) and his "Unofficial X32/M32 OSC Remote
Protocol" PDF (June-2021 edition, FW 4.06). Everything below cites file + function/line. Items that could not be
confirmed from a primary source are marked **UNCONFIRMED** with the best inference.

Source shorthand used in SOURCE lines:

| Tag | What |
|---|---|
| `X32.c` | https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32.c (emulator, v0.88, "XVERSION 4.06") |
| `X32Channel.h`, `X32Bus.h`, `X32Mtx.h`, `X32Auxin.h`, `X32Fxrtn.h`, `X32CfgMain.h`, `X32Dca.h`, `X32Headamp.h`, `X32Fx.h` | same repo/master/`<file>` — the emulator's command tables and node-string token tables |
| `SetSceneParse.c` | same repo/master/SetSceneParse.c — scene-file text → OSC (has the per-parameter ranges & step counts) |
| `X32SetScene.h` | same repo/master/X32SetScene.h — enum token tables used by the scene parser |
| `Xscene2X.c` | same repo/master/X32lib/Xscene2X.c — `Xp_level`, `Xp_linf`, `Xp_logf`, `Xp_frequency`, `Xp_bit`, `Xp_list` |
| `X32Automix.c` | same repo/master/X32Automix.c — `add_3db()` float→dB→float round trip |
| `PDF p.N` | "UNOFFICIAL X32/M32 OSC REMOTE PROTOCOL", Maillot, mirror https://x32ram.com/wp-content/uploads/download-files/X32-OSC.pdf (also https://tostibroeders.nl/wp-content/uploads/2020/02/X32-OSC.pdf); the current copy is linked from https://sites.google.com/site/patrickmaillot/x32 (Google Drive id 1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz). Page numbers are the PDF's printed page numbers. **Verifier note:** the verifier re-checked every PDF fact against the tostibroeders mirror, which is edition "version 4.02 - 01 (Jan 12, 2020)" (`pdftotext -layout`); its printed page numbers run 1-7 lower than the pages cited below (channel data p.24-27, headamp p.42, node/slash p.77-80, conversion code p.127-128, 1024-fader table p.139-146, 161-level table p.151-152, trim/headamp appendices p.155-157, GEQ p.100-101). Content of every cited item was identical. |
| `scene1.scn` | https://raw.githubusercontent.com/cabcookie/saddleback-x32-general-scene/master/Soundboard%20Setup.scn — a **real-console** X32 scene file (2104 lines, header `#2.7#`, 128 `/headamp` lines) used by the verifier as ground truth for node-string text formats and for the DCA-bitmap / source-enum checks. Line numbers `scene1.scn:N`. |
| `bitfocus util.ts` | https://raw.githubusercontent.com/bitfocus/companion-module-behringer-x32/master/src/util.ts — independent corroboration only |

---

## 0. Wire-level conventions that affect every parameter

* UDP port 10023. Ints are big-endian int32, floats big-endian IEEE-754 float32, **always in [0.0, 1.0]** for
  scaled parameters. Enum parameters accept either `,i` index or `,s` token (`/ch/01/gate/mode ,s GATE` ==
  `,i 3`). Exception: parameters typed `int` (e.g. `/dyn/keysrc`, `/config/source`) accept **only** an int.
  SOURCE: PDF p.12 "Special considerations for the enum type".
* The console only *stores* a finite set of floats per parameter ("N steps"); any float sent is rounded to the
  nearest known value. Reading back returns the stored value (e.g. you send 0.75 = 767.25/1023, you read back one
  of the two neighbours 0.7498 = 767/1023 (`0x3f3feffc`, table row 768) or 0.7507 = 768/1023 (`0x3f40300c`, row 769)).
  SOURCE: PDF p.12-13; PDF p.146 fader table rows 768-769.
  VERIFIED-CORRECTED: the original text said 0.75 reads back as 768/1023; 0.75*1023 = 767.25 rounds to 767/1023 =
  0.749756 (PDF row 768 `0.7498 0x3f3feffc`); the hex values were decoded by the verifier: 0x3f40300c = 0.750733 =
  768/1023 exactly, 0x3f3feffc = 767/1023, confirming the i/1023 grid.
* Text form: `/node ,s <path-without-leading-slash>` returns one line `node ,s "/path v1 v2 ...\n"`; the `/`
  command (`/ ,s "/ch/01/mix/fader -20.5"`) sets parameters from the same text form (partial argument lists OK,
  in order). Values in text form are in **real units** (dB, Hz, ms, %), not 0..1 floats.
  SOURCE: PDF p.13-14, p.81-82; `X32.c` `function_node()` (line 3782) and `function_slash()` (line 3388).
  VERIFIED: X32.c 3388 `int function_slash()`, 3782 `int function_node()`, 4470 `int function_node_single()`;
  PDF p.77-78 (verifier copy) "sending / ,s "ch/01/mix/fader -85.4" will be kept as -85.3" and the
  `/~~~,s~~ch/01 name 30` partial-argument example.
* Frequencies in text form may use the `k` notation: `1k02` = 1020 Hz, `10k37` = 10370 Hz, `20k00` = 20000 Hz.
  Parsing rule (verbatim `X32.c` `Xr_float()` line 3303): digits before `k` ×1000, then depending on the number
  of characters after `k`: 1 digit ×100, 2 digits ×10, 3 digits ×1.
  VERIFIED: X32.c 3303-3331 (identical copy in `Xscene2X.c Xr_float()`); real-console text uses the same notation
  (`scene1.scn:44` `/ch/01/eq/3 PEQ 2k69 -1.50 2.0`, `:45` `VEQ 5k97`, `:1063` `HShv 10k02`, `:1459` `HCut 20k00`).

```c
// X32.c line 3303 (verbatim)
float Xr_float(char* Xin, int l) {
	...
		} else if (llread[i] == 'k') {
			ival = 0; idec = 0;
			llread[i] = 0;
			if (i > 0) sscanf(llread, "%d", &ival);
			if (i < l) sscanf(llread + i + 1, "%d", &idec);
			fval = (float)ival * 1000.;
			if (l-i == 2) fval += (float)idec * 100.;
			else if (l-i == 3) fval += (float)idec * 10.;
			else if (l-i == 4) fval += (float)idec;
			return (fval);
		}
```

---

## 1. Generic scale types and conversion formulas

The desk uses exactly four scalar scale families. Ranges are written the way Maillot's PDF writes them:
`linf [min, max, step]`, `logf [min, max, Nvalues]`, `level [.., .., Nvalues]`, `enum {tokens}`.

### 1.1 `linf` — linear

* float → value: `v = min + f * (max - min)`
* value → float: `f = (v - min) / (max - min)`, then quantise to the step grid:
  `intervals = (max - min) / step; f = round(f * intervals) / intervals`, clamp to [0, 1].
* Number of distinct values = `intervals + 1` (e.g. `[-15, 15, 0.25]` → 120 intervals → **121 values**).

SOURCE (verbatim) `X32.c` line 3361 `XslashSetLinf()` and `Xscene2X.c` `Xp_linf()`:

```c
char* XslashSetLinf(X32command* command, char* str_pt_in, float xmin, float lmaxmin, float xstep) {
	...
	fval = Xr_float(str_pt_in, len);
//	fout = (fin - xmin) / (xmax-xmin)
	fval = (fval - xmin) / lmaxmin;
	// round to xstep value
	xstep = lmaxmin/xstep;
	fval = roundf(fval*xstep) / xstep;
	if (fval <= 0.) fval = 0.; // avoid -0.0 values (0x80000000)
	if (fval > 1.) fval = 1.;
```
(`lmaxmin` is passed as `max-min`, e.g. `-18., 36., 0.25` for trim.)
VERIFIED: X32.c 3361-3386 verbatim; `Xscene2X.c Xp_linf()` is byte-for-byte the same arithmetic
(`fval = (fval - xmin) / lmaxmin; xstep = lmaxmin/xstep; fval = roundf(fval*xstep) / xstep;` then clamp).

Rendering (node strings) `X32.c` line 1266/1276:
```c
char* Slinf(float fin, float fmin, float fmax, int pre)  { sprintf(snode_str, " %.<pre>f",  fmin + (fmax - fmin) * fin); }
char* Slinfs(float fin, float fmin, float fmax, int pre) { sprintf(snode_str, " %+.<pre>f", fmin + (fmax - fmin) * fin); }  // signed
```

Worked examples: trim −3 dB → (−3+18)/36 = 0.41667; EQ gain +3.25 dB → (3.25+15)/30 = 0.60833; pan +50 →
(50+100)/200 = 0.75; gate thr −40 → 0.5; dyn thr −20 → 0.66667; headamp 30 dB → (30+12)/72 = 0.58333.
VERIFIED: pan +50 → 0.7500 is the PDF p.13 example (`/ch/02/mix/pan ,f [0.7500]` = "half right"); the other
values were recomputed by the verifier from the C constants above.

### 1.2 `logf` — logarithmic

* float → value: `v = min * (max/min)^f` (= `exp(f*ln(max/min) + ln(min))`)
* value → float: `f = ln(v/min) / ln(max/min)`, quantise `f = round(f * (N-1)) / (N-1)`, clamp.
* `N` is the value count the PDF prints (201, 121, 101, 72). **The code passes N−1** (200, 120, 100, 71).
* Works for decreasing ranges too (Q: min=10, max=0.3, ln(max/min) < 0).

SOURCE (verbatim) `X32.c` line 3334 `XslashSetLogf()`:
```c
char* XslashSetLogf(X32command* command, char* str_pt_in, float xmin, float lmaxmin, int nsteps) {
	...
	fval = Xr_float(str_pt_in, len);
	fval = log(fval / xmin) / lmaxmin; // lmaxmin = log(xmax / xmin)
// round to nsteps' value of log()
	fval = roundf(fval * nsteps) / nsteps;
	if (fval <= 0.) fval = 0.; // avoid -0.0 values (0x80000000)
	if (fval > 1.) fval = 1.;
```
Rendering `X32.c` line 1286:
```c
char* Slogf(float fin, float fmin, float fmax, int pre) { sprintf(snode_str, " %.<pre>f", exp(fin * log(fmax / fmin) + log(fmin))); }
```
Constants used by the code (`X32.c` lines 3443-3470, `SetSceneParse.c`):
`ln(400/20)=2.9957322735`, `ln(20000/20)=6.907755279`, `ln(2000/0.02)=11.512925465`, `ln(4000/5)=6.684611728`,
`ln(0.3/10)=-3.506557897`.

Worked examples: 1000 Hz → ln(50)/ln(1000) = 0.5663 → round(113.26)/200 = **0.565** → reads back 990.9 Hz
(`f201[113] = " 990.9"`); f=0.2650 → 20·1000^0.265 = 124.7 Hz; f=0.5 → 632.5 Hz; HPF 100 Hz → ln(5)/ln(20)=0.5375 →
0.54; Q 2.0 → ln(0.2)/ln(0.03) = 0.4589 → round(32.58)/71 = 33/71 = **0.4648** (PDF example value); hold 100 ms →
0.74; release 100 ms → 0.45.
VERIFIED: X32.c 3334-3357 verbatim (identical to `Xscene2X.c Xp_logf()`); table look-ups checked by the verifier
with a script over `X32Fx.h` line 107 `f201[]` (201 entries: [0]=" 20.0", [53]=" 124.7", [100]=" 632.5",
[113]=" 990.9", [200]=" 20k00") and line 143 `f101[]` (101 entries: [50]=" 89.4", [54]=" 100.8", [100]=" 400.0");
Q: 33/71 = 0.46479 matches PDF Q-table row `0.4648 2.0` and PDF p.12 `/ch/01/eq/1 ,ifff [2] [0.2650] [0.5000] [0.4648]`;
hold: ln(5000)/ln(100000) = 0.7398 → 0.74; release: ln(20)/ln(800) = 0.4482 → 0.45.

### 1.3 `level` — the fader / send-level taper (see §2)

### 1.4 `enum` / `int` / `%int` / `string`

* `enum`: int index into a fixed token list (tables in §11). Node text prints the token.
* `int`: plain int (icon, source, keysrc, panFollow...). Node text prints `%d`.
* `%int`: bitmap; node text prints `%` followed by the bits **MSB first** (e.g. `%00000001` = bit0 set = DCA1).
  SOURCE `X32.c` line 1284 `Sbitmp()`; parse `Xscene2X.c` `Xp_bit()` (reads from the last char backwards → last
  char is bit 0). `X32.c` `XslashSetPerInt()` also accepts a plain decimal int in `/` commands.
  VERIFIED: X32.c 1284-1295 `for (i = len - 1; i > -1; i--) snode_str[j++] = ((iin & (1 << i)) ? '1' : '0');`;
  Xp_bit: `i = strlen(llread) - 1; ... while ((ch = llread[i]) != '%') { if (ch == '1') ival |= (1<<j); j++; i--; }`.
  Real-console confirmation of "last char = bit 0 = group 1": see §4.10.
* `string`: node text prints it in double quotes (`"Kick Drum"`, empty → `""`). `XslashSetString()` accepts
  quoted (spaces allowed) or bare tokens. Max 12 chars for strip names. SOURCE PDF p.25; `X32.c` line 3184.
  VERIFIED: PDF "A 12-character max string representing the input channel name" (ch, auxin, bus, mtx, main, dca);
  X32.c 3184-3227; real scene `scene1.scn:33` `/ch/01/config "Diazno" 1 CY 1`, `:1449` `/bus/01/config "1 Diazno" 53 WH`
  (space inside quotes), `:1882` `/dca/8/config "" 1 OFF` (empty string).

---

## 2. Fader / level taper (`/…/mix/fader`, `/…/mix/NN/level`, `/…/mix/mlevel`, `/dca/N/fader`, `/config/solo/level`)

Four linear segments with knees at −60/−30/−10 dB; float 0.0 = −∞ ("-oo"); float 1.0 = +10 dB; 0 dB = 0.75.

### 2.1 float → dB (verbatim `X32.c` line 1251 `Slevel()`, used for node strings)
```c
// Slevel: returns db level [-oo...10] from float[0..1]
char* Slevel(float fin) {
	float fl;

	if (fin <= 0.) {
		sprintf(snode_str, " -oo");
	} else {
		if (fin <= 0.0625) fl = 30. / 0.0625 * fin - 90.;
		else if (fin <= 0.25) fl = 30. / (0.25 - 0.0625) * (fin - 0.0625) - 60.;
		else if (fin < 0.5) fl = 20. / (0.5 - 0.25) * (fin - 0.25) - 30.;
		else fl = 20. / (1. - 0.5) * (fin - 0.5) - 10.;
		sprintf(snode_str, " %+.1f", fl);
	}
	return snode_str;
}
```
VERIFIED: X32.c 1236-1250 verbatim (function starts at line 1237 in the current master).
Simplified (identical algebra; verbatim from `X32Automix.c` `add_3db()` line 584 and PDF p.132):
```c
if (f >= 0.5)         d = f * 40.  - 30.;   // -10 .. +10 dB
else if (f >= 0.25)   d = f * 80.  - 50.;   // -30 .. -10 dB
else if (f >= 0.0625) d = f * 160. - 70.;   // -60 .. -30 dB
else if (f >= 0.0)    d = f * 480. - 90.;   // -90 .. -60 dB   (f == 0.0 is reported as -oo)
```
VERIFIED: X32Automix.c 558-600 `add_3db()` (`if (f >= 0.5) f = f * 40. - 30.; else if (f >= 0.25) f = f * 80. - 50.;
else if (f >= 0.0625) f = f * 160. - 70.; else if (f >= 0.0) f = f * 480. - 90.;`) and PDF appendix "X32 faders" C-like
code (identical). **Caution:** `add_3db()` re-quantises with `f = roundf(f * 1024) / 1024;` — a 1024 divisor, which
is a bug in that tool (the console grid is i/1023, proven by the PDF hex table, §2.3); do not copy that line.

### 2.2 dB → float (verbatim `X32.c` line 3229 `XslashSetLevl()`)
```c
char* XslashSetLevl(X32command* command, char* str_pt_in, int nsteps) {
	...
	if (str_pt_in[0] == '-' && str_pt_in[1] == 'o' && str_pt_in[2] == 'o') fval = 0.0;
	else {
		sscanf(str_pt_in, "%f", &fval);
		if (fval < -60.) {
// first slope, make sure we don't generate negative values
//			if ((fval = 0.0625 / 30. * (fval + 90.)) < 0.0) fval = 0.0;
			fval = fval * 0.00208333333 + 0.1875;
			fval = (int)(fval * (nsteps + 0.5)) / (float)nsteps;
			if (fval < 0.0) fval = 0.0;
		} else if (fval < -30.) {
// second slope
//			fval = 0.0625 + (0.25 - 0.0625) / 30. * (fval + 60.);
			fval = 0.00625 * fval + 0.4375;
			fval = (int)(fval * (nsteps + 0.5)) / (float)nsteps;
		} else if (fval < -10.) {
// third slope
//			fval = 0.25 + 0.25 / 20. * (fval + 30.);
			fval = 0.0125 * fval + 0.625;
			fval = (int)(fval * (nsteps + 0.5)) / (float)nsteps;
		} else if (fval <= 10.) {
// fourth and high values slope; make sure we don't go over 1.0
//			if ((fval = 0.5 + 0.5 / 20. * (fval + 10.)) > 1.0) fval = 1.0;
			fval = fval * 0.025 + 0.75;
			if ((fval = (int)(fval * (nsteps + 0.5)) / (float)nsteps) > 1.0) fval = 1.0;
		} else if (fval > 10.) fval = 1.0;
	}
```
VERIFIED: X32.c 3229-3266 verbatim. `Xscene2X.c Xp_level()` has the same slopes but quantises with
`roundf(fval * nsteps) / nsteps` **only in the three lower slopes**; its top slope (−10..+10 dB) is
`if ((fval = (fval * 0.025 + 0.75)) > 1.0) fval = 1.0;` with no rounding at all (VERIFIED-CORRECTED: the original
§2.3 text implied Xp_level rounds every slope).
Equivalent closed form (PDF p.132, `X32Automix.c` line 596, `bitfocus util.ts dbToFloat`):
```c
if (d < -60.)       f = (d + 90.) / 480.;
else if (d < -30.)  f = (d + 70.) / 160.;
else if (d < -10.)  f = (d + 50.) / 80.;
else if (d <= 10.)  f = (d + 30.) / 40.;
else                f = 1.0;
```

### 2.3 Quantisation / step counts

| Parameter family | Values | Grid | Code |
|---|---|---|---|
| `/ch,/auxin,/fxrtn,/bus,/mtx/../mix/fader`, `/main/st|m/mix/fader`, `/dca/N/fader` | **1024** | `f = i/1023`, i = 0..1023 | `XslashSetLevl(…, 1023)`, `Xp_level(buf,k,1023)` (`SetSceneParse.c` ch_mix, main_st_mix, dca, mtx) ; PDF "level [0.0...1.0(+10dB), 1024]" |
| `/…/mix/NN/level` (all sends), `/…/mix/mlevel`, `/config/solo/level`, `/config/talk/A|B/level`, `/config/osc/level` | **161** | `f = i/160` | `Xp_level(buf,k,160)` (`SetSceneParse.c` lines 182/243/257/271 config levels, 1025 ch mlevel, 1100 ch sends, 1909/1944 bus sends, 2284 main/st sends, 2466 main/m sends); `X32.c` `XslashSetLevl(…, 160)` **only for `mlevel`** (CHMX case, line 3501); PDF "level [-90.0...10.0 (+10 dB), 161]" (ch/auxin/fxrtn: p.26-30; bus/mtx/main: "[0.0...1.0(+10dB), 161]" p.31-37) and the 161-value appendix table |

VERIFIED-CORRECTED: the original row cited `XslashSetLevl(…, 160)` for send levels. In `X32.c` the send-level
slash-parser cases `CHMO`/`CHME` (lines 3503-3513) actually call `XslashSetLevl(&command[i+2], str_pt_in, 1023)` —
i.e. the emulator quantises **sends** on the 1024 grid (an emulator inconsistency, since `CHME` is shared with
`/dca/N` and `/mtx/NN/mix`). The 161-value grid for sends is established by the PDF and by `SetSceneParse.c`, not by
X32.c.
VERIFIED (1024 rows): X32.c 3497 `XslashSetLevl(&command[i+2], str_pt_in, 1023)` (CHMX fader), 3552 (MSMX main fader);
`SetSceneParse.c` `Xp_level(buf, k, 1023)` at 939 (ch fader), 1300/1497 (auxin/fxrtn), 1820/1846 (bus), 2127/2135
(mtx), 2255/2268 (main/st), 2466-2475 (dca).

Quantise with **`f = round(f * 1023) / 1023`** (resp. `round(f*160)/160`) — this is what `Xscene2X.c Xp_level()`
does (`roundf(fval * nsteps) / nsteps`) and it reproduces the console's observed behaviour: PDF p.81 states that
sending `-85.4` "will be kept as -85.3". Check: (−85.4+90)/480 = 0.0095833; ×1023 = 9.80 → 10 → 10/1023 = 0.009775
→ 0.009775×480−90 = **−85.31** ✓. The variant `int(f*1023.5)/1023` printed in PDF p.132 and used in
`X32.c XslashSetLevl` gives 9/1023 → −85.8 ✗, so prefer `round()`.
VERIFIED: PDF 1024-value table row 11 = `0.0098 0x3c20280a -85.3`; the verifier decoded 0x3c20280a = 0.0097752 =
10/1023 exactly, so the console's stored value for "-85.4" is index 10 → −85.31, which only `round()` produces.
PDF p.13 example `/ch/01/mix/fader 3` ↔ `[0.8250]` (`0x3f5334cd`) = 844/1023 = round(0.825·1023 = 843.98) ✓.

Worked table (fader, 1024 grid):

| dB in | raw f | stored f (i/1023) | reads back |
|---|---|---|---|
| +10 | 1.0 | 1023/1023 = 1.0 | +10.0 |
| +3 | 0.825 | 844/1023 = 0.825024 | +3.0 (PDF p.13 example `0.8250`) |
| 0 | 0.75 | 767/1023 = 0.749756 (or 768/1023=0.750733) | −0.0 / +0.0 |
| −6 | 0.6 | 614/1023 = 0.600196 | −6.0 |
| −20.5 | 0.36875 | 377/1023 = 0.368524 | −20.5 |
| −60 | 0.0625 | 64/1023 = 0.062561 | −60.0 |
| −89.5 | 0.001042 | 1/1023 = 0.000978 | −89.5 (PDF table index 2) |
| −90 / -oo | 0.0 | 0 | **-oo** |

Notes:
* **−90 dB and −∞ are the same float (0.0)**; there is no distinct −90.0 value. The 1024-value table index 1 is
  `-oo`, index 2 is −89.5. SOURCE PDF p.146.
* Send levels (161 grid): **3 dB** steps from −90 to −60 (10 grid points: `0.0063 -87.0`, `0.0125 -84.0`, …,
  `0.0625 -60.0`), **1 dB** steps −60..−30 (30 points: `0.0688 -59.0`, …), **0.5 dB** steps −30..−10 (40 points:
  `0.2688 -28.5`, …), **0.25 dB** steps −10..+10 (80 points: `0.5437 -8.3`, `0.5500 -8.0`, …, `0.8250 +3.0`).
  SOURCE PDF 161-value appendix table (values quoted verbatim).
  VERIFIED-CORRECTED: the original said "1 dB steps below −30"; the −90..−60 segment is 3 dB/step
  (30 dB over 0.0625·160 = 10 steps). Fader (1024 grid) step sizes: 0.469 dB (−90..−60, 64 points), 0.156 dB
  (−60..−30), 0.078 dB (−30..−10), 0.039 dB (−10..+10) — PDF rows `2 0.0010 -89.5`, `3 0.0020 -89.1`.

### 2.4 Text rendering of levels
* `-oo` for float 0 (`" -oo"`), otherwise `%+.1f` → `+10.0`, `+0.0`, `-0.0`, `-9.9`, `-48.3`.
  SOURCE `Slevel()` above; scene excerpt in `SetSceneParse.c` lines 2740-2778:
  `/ch/01/mix/fader  -9.9`, `/fxrtn/01/mix/fader  -0.0`, `/bus/01/mix/fader -48.3`, `/dca/1/fader +10.0`,
  `/auxin/01/mix/fader   -oo`, `/ch/01/mix/mlevel -41.0`.
* The real console right-pads levels to a 5-character field (`"  -9.9"`, `"   -oo"`) — parsers must split on
  runs of whitespace, not single spaces. SOURCE: same scene excerpt (multiple spaces before `-9.9` and `-oo`).
* `/` (set) accepts `-oo`, `-90`, `-20.5`, `3`, `10` etc. SOURCE PDF p.13-14 examples.
* VERIFIED (real console, `scene1.scn`): 5-character right-aligned level field confirmed everywhere —
  `:46` `/ch/01/mix ON  +2.1 ON +0 OFF   -oo`, `:78` `/ch/02/mix ON  -3.8 …`, `:1460` `/bus/01/mix OFF   0.0 OFF +0 OFF -81.0`,
  `:1867` `/dca/1 OFF  -8.3`, `/mtx/01/mix ON -10.0`, `/main/m/mix ON -16.8`, `/main/st/mix ON   -oo +0`.
* VERIFIED-CORRECTED (real console): **exactly 0 dB prints as `0.0` with no sign** (`scene1.scn:61`
  `/ch/01/mix/15 ON   0.0 +0 POST`, `:1460`, `/main/st/mix/01 ON   0.0 +0 POST`); non-zero values carry the sign
  (`+2.1`, `-3.8`, `+10.0`); Maillot's own real-console excerpt shows `-0.0` for a value that rounds to zero from
  below. So the rendering is *not* a plain `%+.1f`: parsers must accept `0.0`, `+0.0`, `-0.0` and `-oo`; when
  generating text prefer `0.0` for |dB| < 0.05, otherwise `%+.1f`.

---

## 3. Pan

* Addresses: `/ch/NN/mix/pan`, `/auxin/NN/mix/pan`, `/fxrtn/NN/mix/pan`, `/bus/NN/mix/pan`, `/main/st/mix/pan`,
  and odd-numbered sends `/…/mix/01|03|05|07|09|11|13|15/pan` (bus/main: `01|03|05`).
* `linf [-100, +100, 2]` → 101 values. `f = (p + 100) / 200`, `p = f*200 − 100`. Quantise `round(f*100)/100`.
  Worked: +50 → 0.75 (PDF p.13 example `0.7500` = "half right"); −100 → 0.0; 0 → 0.5; +20 → 0.6.
* Node text: signed integer `%+.0f` → `+0`, `+20`, `-100`, `+100`.
  SOURCE `X32.c` CHMX/CHMO cases `Slinfs(…, -100., +100., 0)` (lines 3974/3981); parse `XslashSetLinf(…, -100., 200., 2.)`
  (lines 3499/3506/3553); `SetSceneParse.c` `Xp_linf(buf, k, -100., 200., 2.)`; PDF p.14, p.26.
  VERIFIED: all of the above re-read; PDF "linf [-100.000, 100.000, 2.000]" for ch/auxin/fxrtn/bus/main mix/pan and
  every odd send pan; real console text `+0`, `+8`, `-2`, `-100` (`scene1.scn:46`, `:1460`, `/main/m/mix/03 ON   -oo -2 POST`,
  `:1064` `/auxin/01/mix ON   -oo ON -100 OFF   -oo`).

---

## 4. Channel strip (`/ch/01..32`) — full map with scaling

Table entries: address | type | range | float↔value | node token/format | source.
"log(N)" means §1.2 with N values; "lin[min,max,step]" means §1.1.

### 4.1 `/ch/NN/config` (node order: name icon color source)
| Address | Type | Range / mapping | Node text |
|---|---|---|---|
| `/ch/NN/config/name` | string | ≤ 12 chars (PDF p.25) | `"Kick Drum"` |
| `/ch/NN/config/icon` | int | 1..74 (PDF p.25 "see appendix for a list of icons") | `3` |
| `/ch/NN/config/color` | enum | 0..15 → §11.1 | `YE` |
| `/ch/NN/config/source` | int | 0..64: 0 OFF, 1..32 In01..In32, 33..38 Aux 1..6, 39 USB L, 40 USB R, 41..48 Fx 1L,1R,2L,2R,3L,3R,4L,4R, 49..64 Bus 01..16 (PDF p.25; count = 65 ✓) | `1` |

Node example (PDF p.134): `/ch/01/config "Kick Drum" 3 YE 1`. SOURCE `X32Channel.h` lines 13-19; `X32.c` CHCO
render (line 3904) / parse (line 3434).
VERIFIED: X32Channel.h 13-19 (`name S32`, `icon I32`, `color E32 Xcolors`, `source I32`); X32.c 3434-3439 (String,
Int, List, Int) and 3904-3914 (quoted string, `Sint`, `Scolor[]`, `Sint`); PDF p.24-25 "[1...74]", "[0...15]",
"[0...64] {OFF, In01...32, Aux 1...6, USB L, USB R, Fx 1L...Fx 4R, Bus 01...16}".
VERIFIED (real console) source numbering 33..40: `scene1.scn:1057-1064` `/auxin/01/config "" 55 GN 33` …
`/auxin/06/config "" 55 GN 38`, `/auxin/07/config "USB L" 60 YE 39`, `/auxin/08/config "USB R" 60 YE 40` (the
default aux-in sources are Aux 1..6 and USB L/R, and the user named 07/08 "USB L"/"USB R"). Icon values up to 73
observed (`/main/st/config "" 73 GNi`), consistent with 1..74.

### 4.2 `/ch/NN/delay`
| `/ch/NN/delay/on` | enum OFF/ON | | `/ch/NN/delay/time` | linf [0.3, 500, 0.1] ms; code: `XslashSetLinf(…, 0.3, 499.7, 0.1)`; node `%.1f` right-aligned in a 5-char field on the console (`scene1.scn:34` `/ch/01/delay OFF   0.3`) |

VERIFIED: X32.c 3440-3443 (`0.3, 499.7, 0.1`), 3915-3918 (`Slinf(…, 0.3, 500., 1)`); PDF "linf [0.300, 500.000, 0.100] ms".

### 4.3 `/ch/NN/preamp` (node order: trim invert hpon hpslope hpf)
| Address | Type | Mapping | Node text |
|---|---|---|---|
| `/ch/NN/preamp/trim` | linf [-18, +18, 0.25] dB (145 values) | `f=(t+18)/36` | `%+.1f` → `+0.0`, `-3.0` |
| `/ch/NN/preamp/invert` | enum {OFF, ON} | | `OFF` |
| `/ch/NN/preamp/hpon` | enum {OFF, ON} | | `OFF` |
| `/ch/NN/preamp/hpslope` | enum {12, 18, 24} (idx 0,1,2) | | `12` |
| `/ch/NN/preamp/hpf` | logf [20, 400, 101] Hz | `f = ln(F/20)/ln(20)` quantised /100; `F = 20·20^f` | **Real console: integer Hz, right-aligned width 3** (`24  79`, `24 144`, `24  30`). Emulator: `f101[]` table with 1 decimal (`" 89.4"`) |

SOURCE `X32Channel.h` 22-26; `X32.c` CHPR parse line 3444 (`-18., 36., 0.25` ; `20., 2.9957322735, 100`), render
line 3919 (`Slinfs(-18,+18,1)`, `Sfslope[]`, `f101[(int)(100*f+0.5)]`); PDF p.25 (trim "digital sources only").
Note: trim only acts on digital (non-headamp) sources; analog gain is `/headamp/NNN/gain` (§12).
VERIFIED: all ranges (PDF "linf [-18.000, 18.000, 0.250]", "{12, 18, 24}", "logf [20.000, 400.000, 101]"; X32.c lines
as stated; PDF trim appendix "145 lin scale values [-18, 18, 0.25]").
VERIFIED-CORRECTED (real console text): `scene1.scn:35` `/ch/01/preamp +0.0 OFF OFF 24  79`, `:36-` `+3.0 OFF OFF 24 144`,
`… 24  30`, `… 24 132` — the console prints the HPF frequency as a whole number of Hz in a 3-wide field, not with a
decimal; the emulator's `f101[]` rendering (`" 89.4"`) is emulator-only. Trim is signed (`+3.0`) as stated. Aux-in trim is
also signed on the console (`:1058` `/auxin/01/preamp +8.8 OFF`) although the emulator's `AXPR` renderer uses the
unsigned `Slinf`.

### 4.4 `/ch/NN/gate` (node order: on mode thr range attack hold release keysrc)
| Address | Type | Mapping | Node text |
|---|---|---|---|
| `/ch/NN/gate/on` | enum OFF/ON | | |
| `/ch/NN/gate/mode` | enum {EXP2, EXP3, EXP4, GATE, DUCK} = 0..4 | | `GATE` |
| `/ch/NN/gate/thr` | linf [-80, 0, 0.5] dB (161 values) | `f=(t+80)/80` | `%.1f` (unsigned, e.g. `-40.0`) |
| `/ch/NN/gate/range` | linf [3, 60, 1] dB (58 values) | `f=(r-3)/57` | `%.1f` |
| `/ch/NN/gate/attack` | linf [0, 120, 1] ms (121) | `f=a/120` | integer (`20`, `1`, `32`) |
| `/ch/NN/gate/hold` | logf [0.02, 2000, 101] ms | `f=ln(h/0.02)/ln(100000)`; `h=0.02·100000^f` | **console: 3 significant digits, right-aligned width 4** (`0.02`, `2.00`, `79.6`, ` 100`, ` 502`); emulator `%.2f` |
| `/ch/NN/gate/release` | logf [5, 4000, 101] ms | `f=ln(r/5)/ln(800)`; `r=5·800^f` | **console: integer, right-aligned width 4** (`   9`, ` 124`, ` 576`, ` 983`); emulator `%.0f` |
| `/ch/NN/gate/keysrc` | int 0..64 (same list as config/source; 0 = OFF = self) | | `%d` |
| `/ch/NN/gate/filter/on` | enum OFF/ON | | |
| `/ch/NN/gate/filter/type` | enum {LC6, LC12, HC6, HC12, 1.0, 2.0, 3.0, 5.0, 10.0} = 0..8 (LC/HC = low/high-cut 6/12 dB/oct; numbers = band-pass Q) | | `LC6` |
| `/ch/NN/gate/filter/f` | logf [20, 20000, 201] Hz | `f=ln(F/20)/ln(1000)` quantised /200 | `f201[]` (`1k02` style) |

SOURCE `X32Channel.h` 34-45; `X32.c` CHGA parse line 3448 (`-80., 80., 0.5` / `3., 57., 1.` / `0., 120., 1.` /
`0.02, 11.512925465, 100` / `5., 6.684611728, 100`), CHGF parse 3458 (`20., 6.907755279, 200`), render ~3933;
`X32Fx.h` `Sgmode[]`, `Sgftype[]`; PDF p.25. **Note** `X32.c` line 321 `Xgmode[]` has a typo `" EXP"` for index 2;
`X32Fx.h Sgmode[]`, `X32SetScene.h Xgatemode[]` and the PDF all say `EXP4`.
VERIFIED: X32.c 3451-3460 (parse constants exactly as quoted), 3926-3935 (render: `Slinf(-80,0,1)`, `Slinf(3,60,1)`,
`Slinf(0,120,0)`, `Slogf(0.02,2000,2)`, `Slogf(5,4000,0)`, `Sint`), 3936-3941 CHGF; X32.c 321 `Xgmode[] = {" EXP2", " EXP3",
" EXP", " GATE", " DUCK"}`; X32Fx.h 48 `Sgmode[] = {" EXP2", " EXP3", " EXP4", " GATE", " DUCK"}`, 50 `Sgftype[]`;
X32SetScene.h 5088-5093 `Xgatemode[]`, `Xgateftype[]`; PDF p.25 (all ranges as in the table, keysrc "int with value
[0...64]", filter/type "[0...8] … Keysolo (Solo/Q)").
VERIFIED-CORRECTED (real console node text, `scene1.scn`): `:36` `/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0`,
`/ch/02/gate ON GATE -42.0 42.0 32 2.00  504 0`, `/ch/05/gate OFF GATE -17.5 10.0 8 79.6  151 0`,
`/ch/08/gate ON EXP4 -45.5 60.0 1  502  983 0`, `/ch/06/dyn … 7 0.04   9 POST …`. Hold is printed with three
significant digits (`0.02`, `2.00`, `79.6`, `100`, `502`) and release as an integer, each right-aligned in a
4-character field; the original table gave the emulator's `%.2f` / `%.0f`, which differ (`6.32` vs `6.32` agree, but
`100.00` vs ` 100` and `0.63` vs `0.63` — parse as floats, do not rely on fixed decimals). `:37` `/ch/01/gate/filter OFF 3.0 1k39`.

### 4.5 `/ch/NN/dyn` (node order: on mode det env thr ratio knee mgain attack hold release pos keysrc mix auto)
| Address | Type | Mapping | Node text |
|---|---|---|---|
| `/ch/NN/dyn/on` | enum OFF/ON | | |
| `/ch/NN/dyn/mode` | enum {COMP, EXP} 0/1 | | `COMP` |
| `/ch/NN/dyn/det` | enum {PEAK, RMS} 0/1 | | `PEAK` |
| `/ch/NN/dyn/env` | enum {LIN, LOG} 0/1 | | `LIN` |
| `/ch/NN/dyn/thr` | linf [-60, 0, 0.5] dB (121) | `f=(t+60)/60` | `%.1f` |
| `/ch/NN/dyn/ratio` | enum {1.1, 1.3, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10, 20, 100} = 0..11 | | `2.0` |
| `/ch/NN/dyn/knee` | linf [0, 5, 1] (6 values) | `f=k/5` | integer (`2`) |
| `/ch/NN/dyn/mgain` | linf [0, 24, 0.5] dB (49) | `f=g/24` | **console: 3 significant digits** (`0.00`, `8.00`, `14.0`); emulator `%.1f` |
| `/ch/NN/dyn/attack` | linf [0, 120, 1] ms (121) | `f=a/120` | integer (`71`) |
| `/ch/NN/dyn/hold` | logf [0.02, 2000, 101] ms | as gate | as gate (`0.03`, `10.0`, width 4) |
| `/ch/NN/dyn/release` | logf [5, 4000, 101] ms | as gate | as gate (` 538`, width 4) |
| `/ch/NN/dyn/pos` | enum {PRE, POST} 0/1 | | `POST` |
| `/ch/NN/dyn/keysrc` | int 0..64 | | `%d` |
| `/ch/NN/dyn/mix` | linf [0, 100, 5] % (21 values) | `f=m/100` | `%.0f` |
| `/ch/NN/dyn/auto` | enum OFF/ON (FW ≥ 2.10) | | |
| `/ch/NN/dyn/filter/on|type|f` | as gate filter | | |

SOURCE `X32Channel.h` 47-66; `X32.c` CHDY parse line 3462 (`-60., 60., 0.5` / `0., 5.0, 1.0` / `0., 24.0, 0.5` /
`0., 120.0, 1.0` / hold/release / `0., 100.0, 5.0`), render ~3946; `X32Fx.h` `Sdmode/Sddet/Sdenv/Sdratio/Sdpos`;
`SetSceneParse.c` ch_dyn (line 743) incl. `Xp_percent` for mix (value/100) and the FW-2.10 guard for `auto`; PDF p.25-26.
The emulator's `Xdyenv[]` token for index 0 is `" LIN"`; the scene parser compares against `"UN"` (typo, harmless).
VERIFIED: X32.c 3467-3483 (parse: `-60., 60., 0.5` / `0., 5.0, 1.0` / `0., 24.0, 0.5` / `0., 120.0, 1.0` / `0.02,
11.51292546, 100` / `5., 6.684611728, 100` / `0., 100.0, 5.0`), 3942-3958 (render); `SetSceneParse.c` 743-812
(`XOff_On(buf, k, "UN")` at 766, `Xp_percent` at 806, `if (X32VER > 209)` guard for `auto`); X32.c 322-327 enum
tables; PDF p.25-26 (ratio "[0...11]", knee "[0.000, 5.000, 1.000]", mgain "[0.000, 24.000, 0.500]", mix "[0, 100, 5] %").
VERIFIED-CORRECTED (real console node text): `scene1.scn:38` `/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF`,
`/ch/04/dyn … -21.5 2.0 1 14.0 79 0.63  576 POST 0 100 OFF`, `:1450` `/bus/01/dyn ON COMP RMS LOG -22.5 2.0 0 0.00 35 0.25  226 POST 0 100 OFF`.
Field order on the console = on mode det env thr ratio knee mgain attack hold release pos keysrc mix auto (matches
X32Channel.h); mgain is printed with three significant digits (`8.00`, `14.0`), not the emulator's `%.1f`.
`:39` `/ch/01/dyn/filter ON 3.0 1k17`.

### 4.6 `/ch/NN/insert` (node: on pos sel)
`/insert/on` enum OFF/ON; `/insert/pos` enum {PRE, POST}; `/insert/sel` enum 0..22 =
{OFF, FX1L, FX1R, FX2L, FX2R, FX3L, FX3R, FX4L, FX4R, FX5L, FX5R, FX6L, FX6R, FX7L, FX7R, FX8L, FX8R, AUX1..AUX6}.
SOURCE `X32.c` line 328-331 `Xisel[]`; PDF p.26.
VERIFIED: X32.c `Xisel[]` (23 tokens, verbatim above), X32SetScene.h 5100-5102 `Xinsel[]`, PDF "[0...22]"; real
console `scene1.scn:40` `/ch/01/insert ON PRE FX4L`, `/main/st/insert ON PRE FX8L`, `/mtx/01/insert OFF PRE OFF`.

### 4.7 `/ch/NN/eq` — 4 bands (node `/ch/NN/eq` = on; `/ch/NN/eq/B` = type f g q)
| Address | Type | Mapping | Node text |
|---|---|---|---|
| `/ch/NN/eq/on` | enum OFF/ON | | |
| `/ch/NN/eq/[1-4]/type` | enum {LCut, LShv, PEQ, VEQ, HShv, HCut} = 0..5 | | `PEQ` |
| `/ch/NN/eq/[1-4]/f` | logf [20, 20000, 201] Hz | `f=ln(F/20)/ln(1000)`, /200 grid | `f201[]` (e.g. `124.7`, `1k02`, `20k00`) |
| `/ch/NN/eq/[1-4]/g` | linf [-15, +15, 0.25] dB (121) | `f=(g+15)/30` | `%+.2f` → `+0.00`, `-3.25` |
| `/ch/NN/eq/[1-4]/q` | logf [10, 0.3, 72] — **decreasing**: f=0 → Q 10, f=1 → Q 0.3 | `f=ln(Q/10)/ln(0.03)` quantised /71; `Q=10·0.03^f` | `%.1f` (`0.3`, `2.0`, `8.6`) but the maximum prints as `10` on the console (PDF Q table `0.0000 10`; `scene1.scn` Q tokens `0.3 0.5 … 8.6 10`) |

Worked: `/ch/01/eq/1 ,ifff [2] [0.2650] [0.5000] [0.4648]` = PEQ, 124.7 Hz, 0 dB, Q 2.0 (PDF p.12 example).
SOURCE `X32Channel.h` 67-87; `X32.c` CHEQ parse line 3486 (`20., 6.907755279, 200` / `-15., 30.0, 0.250` /
`10., -3.506557897, 71`), render ~3965; `X32.c` line 341 `Xeqty1[]`; PDF p.26, p.152 (201 table), p.155 (Q table),
p.165 (121 gain values). Emulator quirks: `X32Fx.h Setype[0]` is spelled `" Lcut"` and the Q renderer uses
`Slogf(…, 10., 0.315, 1)` — the console token is `LCut` and Q max/min are 10.0/0.3 (PDF tables).

VERIFIED: X32Channel.h 55-74 (`Xeqty1`, F32 f/g/q); X32.c 3489-3494 (parse constants exactly as quoted), 3964-3969
(`Setype[]`, `f201[(int)(200*f+0.5)]`, `Slinfs(-15,+15,2)`, `Slogf(10, 0.315, 1)`); X32.c 332 `Xeqty1[]`; X32Fx.h 64
`Setype[] = {" Lcut", …}`; PDF p.26 ("int [0...5] {LCut, LShv, PEQ, VEQ, HShv, HCut}", "[20.000, 20000, 201]",
"[-15.000, 15.000, 0.250]", "[10.000, 0.3, 72]"). Real console: `scene1.scn:42-45` `/ch/01/eq/1 PEQ 164.4 -3.75 1.8`,
`/ch/01/eq/4 VEQ 5k97 +1.50 2.0`, `:1060` `/auxin/01/eq/1 LCut 91.4 -9.50 1.3`, `:1063` `HShv 10k02 +0.00 2.0`,
`:1459` `/bus/01/eq/6 HCut 20k00 -0.50 2.0`; `/ch/01/eq ON`.

There is **no `/ch/NN/eq/mode`** on X32/M32 (that parameter exists on XAir only). Bus/matrix/main EQs have no
GEQ/TEQ mode either; graphic EQs are FX-slot effects (§14). SOURCE: absence in `X32Channel.h`/`X32Bus.h`/PDF.
VERIFIED: no `eq/mode` in X32Channel.h, X32Bus.h, X32Mtx.h, X32CfgMain.h, the PDF node list (p.78-80) or scene1.scn.

### 4.8 `/ch/NN/mix` (node order: on fader st pan mono mlevel)
| Address | Type | Mapping | Node text |
|---|---|---|---|
| `/ch/NN/mix/on` | enum {OFF, ON}: **1 = ON = unmuted, 0 = muted** | | `ON` |
| `/ch/NN/mix/fader` | level 1024 (§2) | | `-9.9` / `-oo` |
| `/ch/NN/mix/st` | enum OFF/ON (assign to Main L/R) | | |
| `/ch/NN/mix/pan` | linf [-100, 100, 2] (§3) | | `+20` |
| `/ch/NN/mix/mono` | enum OFF/ON (assign to M/C) | | |
| `/ch/NN/mix/mlevel` | level 161 (M/C send level) | | `-41.0` |

SOURCE `X32Channel.h` 88-94; `X32.c` CHMX parse line 3495 (`XslashSetLevl(…,1023)`, pan, `XslashSetLevl(…,160)`),
render 3970; PDF p.26.
VERIFIED: X32Channel.h 88-94 order on fader st pan mono mlevel; X32.c 3495-3502 and 3970-3977; PDF p.26; real console
`scene1.scn:46` `/ch/01/mix ON  +2.1 ON +0 OFF   -oo`, `/ch/05/mix ON  -6.5 ON +0 ON  -5.5` (mono ON, mlevel −5.5),
`:1460` `/bus/01/mix OFF   0.0 OFF +0 OFF -81.0`. Semantics `mix/on` 1 = ON = unmuted: PDF "enum {OFF, ON}" and the
scene's muted channel `/ch/04/mix OFF   -oo ON +0 OFF   -oo`.

### 4.9 Sends `/ch/NN/mix/01..16` (to Bus 01..16)
* Odd sends (01,03,…,15) node `CHMO`: **on level pan type panFollow** — they carry the pan/type for the stereo
  pair (01/02, 03/04 …). Even sends (02,…,16) node `CHME`: **on level** only.
* `/ch/NN/mix/MM/on` enum OFF/ON (1 = send active).
* `/ch/NN/mix/MM/level` level **161** values (`Xp_level(…,160)`; PDF "[-90.0...10.0 (+10 dB), 161]").
* `/ch/NN/mix/MM/pan` (odd only) linf [-100, 100, 2].
* `/ch/NN/mix/MM/type` (odd only) enum 0..5 = {IN/LC, <-EQ, EQ->, PRE, POST, GRP}: tap point — 0 input/low-cut,
  1 pre-EQ, 2 post-EQ, 3 pre-fader, 4 post-fader, 5 subgroup (level follows fader, send level fixed at 0 dB).
* `/ch/NN/mix/MM/panFollow` (odd only, FW ≥ 4.0): PDF types it `enum {OFF, ON}, int with value 0 or 1` (bus/main
  rows; the ch row just says "enum"); the emulator declares it `E32` with **no token list** and prints/parses it as
  a plain int (`Sint`, `XslashSetList` with NULL node → ignored); `SetSceneParse.c` reads it with `Xp_int`. Send
  with `,i 0/1`. Node-text token (`0/1` vs `OFF/ON`) on a real FW 4.x console: **UNCONFIRMED** (the verifier's real
  scene is FW 2.7 and has no panFollow field).
* Same layout for `/auxin/NN/mix/01..16` and `/fxrtn/NN/mix/01..16`.

SOURCE `X32Channel.h` 95-186; `X32.c` CHMO/CHME parse lines 3503-3513, render 3978-3988; `X32.c` line 335
`Xmtype[]`; `X32Fx.h Sctype[]`; `SetSceneParse.c` ch_mix_01 (line 1052-1114) and ch_mix_02 (line 1115); PDF p.26-27.
VERIFIED: X32Channel.h 95-186 (odd = on level pan type panFollow, even = on level); X32.c 335 `Xmtype[] = {" IN/LC",
" <-EQ", " EQ->", " PRE", " POST", " GRP"}`; X32SetScene.h 5109 `Xmxtyp[]`; PDF p.26 "int [0...5] {IN/LC, <-EQ, EQ->, PRE,
POST, GRP}"; PDF footnote "FW 4.0 and above" for panFollow. Real console (FW 2.7 scene, no panFollow yet):
`scene1.scn:47` `/ch/01/mix/01 ON   -oo +0 PRE`, `:48` `/ch/01/mix/02 ON   -oo`, `:55` `/ch/01/mix/09 ON  +0.5 +0 POST`,
`:1266` `/fxrtn/01/mix/03 OFF   -oo -100 POST`.

### 4.10 `/ch/NN/grp` (node: dca mute)
* `/ch/NN/grp/dca` %int 0..255, 8-bit bitmap, **bit0 = DCA1 … bit7 = DCA8**. Node `%00000001` (MSB first).
* `/ch/NN/grp/mute` %int 0..63, 6-bit bitmap, **bit0 = Mute group 1 … bit5 = MG6**. Node `%000001`.
SOURCE `X32Channel.h` 20-21; `X32.c` CHGRP render `Sbitmp(v, 8)`, `Sbitmp(v, 6)` (line ~3993) and
`XslashSetPerInt()` parse; `Xscene2X.c Xp_bit()`; PDF p.27.
Bit order (bit0 = group 1) follows from `Sbitmp()` printing bit `len-1` first and `Xp_bit()` reading the last
character as bit 0; PDF only says "8 bits bitmap".
**VERIFIED (real console, resolves the former UNCONFIRMED item):** in `scene1.scn` the DCA names are
`/dca/1/config "Vocals"`, `/dca/2/config "Instruments"`, `/dca/3/config "Drums"`, `/dca/4/config "Effects"`,
`/dca/5/config "Music"`, `/dca/6/config "Sermon"`, `/dca/7/config "Announcer"` (lines 1868-1880) and the channels carry:
vocal channels `Diazno`/`Lindi` → `/ch/01/grp %00000001 %000000`; `Acc Diazno`/`Bass`/`Keys L` → `%00000010`;
`Kick`/`Snare Top`/`Overhead` → `%00000100`; `/fxrtn/01 "Reverb L"` → `%00001000`; `Music L`/`Music R` → `%00010000`;
`Sermon Rick`/`Sermon DE` → `%00100000`; announcer mics (ch 15/16) → `%01000000`. Hence the **rightmost character is
bit 0 = DCA 1, leftmost is DCA 8**; the same rendering (`Sbitmp`) is used for the 6-bit mute-group field, so bit 0 =
mute group 1. Community wording agrees ("ch/xx/grp … DCA 87654321", soundforums X32 thread).
Emulator `XslashSetPerInt()` (X32.c 3156-3182) has a bug when given a `%…` string (it shifts `value.ii` instead of `j`),
so test bitmap writes against the emulator with plain ints.

### 4.11 `/ch/NN/automix` (ch 01..08 only)
`/automix/group` enum {OFF, X, Y}; `/automix/weight` linf [-12, +12, 0.5] dB (49 values), node `%+.1f`.
SOURCE `X32Channel.h` 187-189; `X32.c` CHAMIX (`-12., 24., 0.5`); PDF p.27.
VERIFIED: X32Channel.h has `/ch/01..08/automix` only (the `/ch/08/automix` block is at line 1325; none for 09+);
X32.c 3518-3521 parse, 3993-3996 render (`Samix[]`, `Slinfs(-12,+12,1)`); PDF p.27 "linf [-12.000, 12.000, 0.500] …
only effective on channels 01 to 08", "{OFF, X, Y}"; real console `scene1.scn:64` `/ch/01/automix OFF -12.0`.
The PDF node list (p.78) names `ch/[01...32]/automix/group` and `ch/[01...32]/automix/weight` as individually
addressable `/node` requests (see §13).

---

## 5. Aux-in `/auxin/01..08` and FX-return `/fxrtn/01..08`

* `/auxin/NN/config/name|icon|color|source` (source int 0..64 as §4.1), `/auxin/NN/preamp/trim` (linf −18..+18,
  0.25) and `/preamp/invert`, `/auxin/NN/eq/on` + 4 bands (as §4.7), `/auxin/NN/mix/*` (as §4.8), 16 sends (§4.9),
  `/auxin/NN/grp/dca|mute`. No gate/dyn/insert/delay. SOURCE `X32Auxin.h` 11-60; PDF p.28-29.
  Node for `/auxin/NN/preamp` = "trim invert" (AXPR, `Slinf(-18,+18,1)`; real console prints it signed: `+8.8 OFF`).
  VERIFIED: X32Auxin.h 14-60 (config incl. `source I32`, preamp trim/invert, eq 4 bands `Xeqty1`, mix, sends, grp);
  X32.c 3522-3525 AXPR parse (note the emulator bug: both calls use `&command[i+1]`), 3997-4000 render;
  real console `scene1.scn:1057-1081`.
* `/fxrtn/NN/config/name|icon|color` (no source), `/fxrtn/NN/eq` 4 bands, `/fxrtn/NN/mix/*` (fader, st, pan,
  mono, mlevel), 16 sends, `/fxrtn/NN/grp/dca|mute`. SOURCE `X32Fxrtn.h`; PDF p.30.
  VERIFIED: X32Fxrtn.h 14-46 (`/fxrtn/01/config` uses the `BSCO` node = name icon color, no source; no preamp; eq 4
  bands; mix on fader st pan mono mlevel); real console `scene1.scn:1257-1280` (`/fxrtn/01/config "Reverb L" 61 MG`,
  `/fxrtn/01/mix ON   0.0 ON -100 OFF   -oo`, `/fxrtn/01/grp %00001000 %000000`).

---

## 6. Bus `/bus/01..16`

| Block | Contents |
|---|---|
| `/bus/NN/config/name|icon|color` | as channel (no `source`). Node `BSCO`: `"name" icon COLOR`. |
| `/bus/NN/dyn/*` | identical set & scaling to §4.5 (on mode det env thr ratio knee mgain attack hold release pos keysrc mix auto filter/on|type|f). Note: the emulator's `X32Bus.h` mislabels `ratio` and `mix` with `OffOn`; the PDF (p.31) and `SetSceneParse.c` bus_dyn use the normal ratio enum / linf [0,100,5]. |
| `/bus/NN/insert/on|pos|sel` | §4.6 |
| `/bus/NN/eq/on`, `/bus/NN/eq/[1-6]/type|f|g|q` | **6 bands**, same scaling as §4.7; type enum is the **6-entry** list {LCut, LShv, PEQ, VEQ, HShv, HCut} (`X32Bus.h` uses `Xeqty1`; PDF p.31 "int [0...5]"). |
| `/bus/NN/mix/on|fader|st|pan|mono|mlevel` | as §4.8 (fader 1024, mlevel 161) |
| `/bus/NN/mix/01..06` | sends to **Matrix 1..6**. Odd (01,03,05): on level pan type panFollow; even (02,04,06): on level. level = 161 grid. `type` enum {IN/LC, <-EQ, EQ->, PRE, POST} = 0..4 (PDF p.32: no GRP for bus→matrix; `X32Bus.h` wrongly uses `OffOn`). |
| `/bus/NN/grp/dca|mute` | bitmaps as §4.10 |

SOURCE `X32Bus.h` 11-108; PDF p.31-32; `SetSceneParse.c` bus_mix (line 1809) / bus_mix_01 (1889).
VERIFIED: X32Bus.h 12-108 re-read (dyn block includes `keysrc I32`; `/bus/01/dyn/ratio … OffOn`, `/bus/01/dyn/mix … OffOn`,
`/bus/01/mix/01/type … OffOn` are indeed the emulator mislabels; eq 1-6 use `Xeqty1`; mix uses the CHMX node; sends 01-06
odd=CHMO/even=CHME; grp). PDF p.31-32: bus→matrix send type is printed as "enum int [0...5] representing {IN/LC, <-EQ,
EQ->, PRE, POST}" — the "[0...5]" is a copy-paste from the channel row, only five tokens (0..4) are listed; `SetSceneParse.c`
bus_mix_01 (1889-1922) parses `type` with the 6-entry `Xmxtyp` list and `panFollow` with `Xp_int`, `level` with
`Xp_level(…,160)`. Real console `scene1.scn:1449-1465`: `/bus/01/config "1 Diazno" 53 WH`, `/bus/01/dyn ON COMP RMS LOG
-22.5 2.0 0 0.00 35 0.25  226 POST 0 100 OFF` (keysrc present), `/bus/01/eq/6 HCut 20k00 -0.50 2.0`,
`/bus/01/mix OFF   0.0 OFF +0 OFF -81.0`, `/bus/01/mix/01 ON   -oo +0 POST`, `/bus/01/mix/02 ON   -oo`.

## 7. Matrix `/mtx/01..06`

`/mtx/NN/config/name|icon|color`; `/mtx/NN/preamp/invert` (enum OFF/ON; node MXPR = single token);
`/mtx/NN/dyn/*` (as §4.5 but **no keysrc** in the node string — MXDY skips it: on mode det env thr ratio knee
mgain attack hold release pos mix auto); `/mtx/NN/insert/on|pos|sel`; `/mtx/NN/eq/on` + `/mtx/NN/eq/[1-6]`
with the **14-entry** type enum {LCut, LShv, PEQ, VEQ, HShv, HCut, BU6, BU12, BS12, LR12, BU18, BU24, BS24, LR24}
= 0..13 (BU = Butterworth, BS = Bessel, LR = Linkwitz-Riley, number = dB/oct; PDF: "In some cases, eq 2 and eq 5
are ignored" i.e. bands 2 and 5 are disabled when 1/6 are crossover types); `/mtx/NN/mix/on|fader` **only** (no
pan/st/mono/mlevel/sends); `/mtx/NN/grp/dca|mute`.
SOURCE `X32Mtx.h` 11-80; `X32.c` line 342 `Xeqty2[]`; PDF p.33-34.
VERIFIED: X32Mtx.h 12-80 (no keysrc in dyn; eq `Xeqty2`; `/mtx/01/mix` = CHME node = on fader; grp dca/mute present);
X32.c 333-334 `Xeqty2[]` (14 tokens verbatim above); X32SetScene.h 5106-5107 `Xmeqtyp[]`; PDF p.33 "enum int [0...13]
… In some cases, eq 2 and eq 5 are ignored."; real console `scene1.scn`: `/mtx/01/preamp OFF`, `/mtx/01/dyn OFF COMP RMS
LOG 0.0 3.0 1 0.00 10 10.0  151 POST 100 OFF` (14 fields, no keysrc), `/mtx/01/eq/1 LCut 182.4 +0.00 2.9`,
`/mtx/01/mix ON -10.0`.
Emulator quirk found by the verifier: the `MXDY` **node renderer** (X32.c 4014-4029) reads `mix` from slot `i+14` and
`auto` from `i+15`, but the MXDY table has `mix` at `i+13` and `auto` at `i+14` (the MXDY slash parser, 3534-3549, uses
the correct 13/14) — so `/node ,s mtx/01/dyn` from the emulator prints `mix`/`auto` from the wrong slots. Real console
output is correct (see scene line above).

## 8. Main `/main/st` and `/main/m`

* `/main/st/config/name|icon|color`; `/main/st/dyn/*` (MXDY set, no keysrc); `/main/st/insert/on|pos|sel`;
  `/main/st/eq/on` + `/main/st/eq/[1-6]/type|f|g|q` with the 14-entry type enum (`Xeqty2`);
  `/main/st/mix/on`, `/main/st/mix/fader` (level 1024), `/main/st/mix/pan` (linf −100..100, 2) — **no st/mono/mlevel**;
  matrix sends `/main/st/mix/01..06` (odd: on level pan type panFollow; even: on level; level 161; type enum
  {IN/LC, <-EQ, EQ->, PRE, POST}); `/main/st/grp/dca|mute` (FW 4.06+).
  Node `/main/st/mix` = "on fader pan". SOURCE `X32CfgMain.h` 360-451; `SetSceneParse.c` main_st_mix (line 2250); PDF p.35-36.
  VERIFIED: X32CfgMain.h 360-451 (MXDY dyn, `Xeqty2` eq, MSMX mix = on fader pan, sends 01-06, grp); X32.c 3550-3554 MSMX
  parse (`1023`, pan), 4030-4034 render; SetSceneParse.c 2250-2298 (`Xp_level(…,1023)` fader, sends `Xp_level(…,160)`,
  `Xmxtyp`, `panFollow` via `Xp_int`); PDF p.35 ("/main/st/mix/[01...06]/level level [0.0...1.0(+10dB), 161]",
  "/main/st/mix/01/panFollow enum {OFF, ON} … FW 4.0 and above"); real console `scene1.scn`: `/main/st/config "" 73 GNi`,
  `/main/st/dyn ON COMP RMS LOG -15.5 2.5 1 0.00 49 10.0  295 POST 100 OFF`, `/main/st/eq/1 LShv 79.6 +0.00 2.0`,
  `/main/st/mix ON   -oo +0`, `/main/st/mix/01 ON   0.0 +0 POST`, `/main/st/mix/02 ON   0.0`.
  Note: the emulator's `X32CfgMain.h` main/st and main/m send blocks have **no** `panFollow` entry (on level pan type
  only) although PDF and SetSceneParse.c include it — emulator omission.
* `/main/m` (mono/centre): same as above but `/main/m/mix/on` and `/main/m/mix/fader` only (no pan), sends
  `/main/m/mix/01..06`, `/main/m/grp/dca|mute`. SOURCE `X32CfgMain.h` 453-542; PDF p.37-38.
  VERIFIED: X32CfgMain.h 513-542 (`/main/m/mix` = CHME node = on fader; sends; grp); real console `scene1.scn`:
  `/main/m/config "Sub" 67 GNi`, `/main/m/eq/6 HCut 98.0 +0.00 2.0`, `/main/m/mix ON -16.8`, `/main/m/mix/03 ON   -oo -2 POST`.

## 9. DCA `/dca/1..8`

`/dca/N/on` enum OFF/ON (1 = unmuted); `/dca/N/fader` level 1024; `/dca/N/config/name|icon|color`.
Node `/dca/N` = "on fader" (e.g. `/dca/1 ON +10.0`), node `/dca/N/config` = `"name" icon COLOR`.
SOURCE `X32Dca.h`; `SetSceneParse.c` dca_1 (line 2466: `XOff_On`, `Xp_level(…,1023)`); PDF p.39.
VERIFIED: X32Dca.h 12-66 (`/dca/N` = CHME node: on fader; `/dca/N/config` = BSCO node: name icon color); PDF p.38
("level [0.0...1.0(+10dB), 1024]", "12-character max string", "[1...74]", "[0...15]"); real console `scene1.scn:1867-1882`
`/dca/1 OFF  -8.3`, `/dca/1/config "Vocals" 43 CY`, `/dca/6 ON   -oo`, `/dca/8/config "" 1 OFF`.

## 10. Mute groups & misc config

* `/config/mute/1..6` enum {OFF, ON}: **1 = mute group N engaged (mutes its members)**. Node `/config/mute`
  = 6 tokens `OFF OFF OFF OFF OFF OFF`. SOURCE `X32CfgMain.h` 55-61; PDF p.21 "Mute Group selection".
  VERIFIED: X32CfgMain.h 55-61; `SetSceneParse.c` 150-157 (`XOff_On(buf, k, "OFF")` ×6); PDF "/config/mute/[1...6] enum
  {OFF, ON}: Mute Group selection"; real console `scene1.scn:7` `/config/mute OFF OFF OFF OFF OFF OFF`.
* `/config/chlink/1-2 … 31-32`, `/config/auxlink/1-2..7-8`, `/config/fxlink/1-2..7-8`, `/config/buslink/1-2..15-16`,
  `/config/mtxlink/1-2..5-6` enum OFF/ON; `/config/linkcfg/hadly|eq|dyn|fdrmute` enum OFF/ON (what a stereo link shares).
* `/config/solo/level` level 161; `/config/solo/source` int 0..6 {OFF, LR, LR+C, LRPFL, LRAFL, AUX56, AUX78};
  `/config/solo/sourcetrim` linf [-18, 18, 0.5]; `/config/solo/chmode|busmode|dcamode` enum {PFL, AFL};
  `/config/solo/dimatt` linf [-40, 0, 1]; `/config/solo/delaytime` linf [0.3, 500, 0.1] ms. SOURCE PDF p.21; `X32.c` CSOLO.
  VERIFIED: X32CfgMain.h 70-87 (order level source sourcetrim chmode busmode dcamode exclusive followsel followsolo
  dimatt dim mono delay delaytime masterctrl mute dimpfl; `source` is `I32` with `XSsourc[]`); PDF p.20-21 ("level
  [-90.0...10.0 (+10 dB), 161]", "enum int [0...6] {OFF, LR, LR+C, LRPFL, LRAFL, AUX56, AUX78}", "linf [-18.000, 18.000,
  0.500]", "linf [-40.000, 0.000, 1.000]", "linf [0.300, 500.000, 0.100]"); real console `scene1.scn:10`
  `/config/solo   0.0 AUX78 0.0 PFL PFL PFL ON OFF ON -22 OFF OFF OFF   0.3 OFF OFF OFF` (solo level in a 5-wide field,
  sourcetrim `0.0`, dimatt `-22` as an integer, delaytime `  0.3`).
* `/config/routing/routswitch` enum {REC, PLAY} 0/1 — selects whether `/config/routing/IN/*` or
  `/config/routing/PLAY/*` feeds the channels.
* `/config/routing/IN/1-8|9-16|17-24|25-32` enum 0..23 = {AN1-8, AN9-16, AN17-24, AN25-32, A1-8, A9-16, A17-24,
  A25-32, A33-40, A41-48, B1-8, B9-16, B17-24, B25-32, B33-40, B41-48, CARD1-8, CARD9-16, CARD17-24, CARD25-32,
  UIN1-8, UIN9-16, UIN17-24, UIN25-32} (UIN* = FW ≥ 4.0 user-in patch).
  `/config/routing/IN/AUX` enum 0..15 = {AUX1-4 (really AUX1-6), AN1-2, AN1-4, AN1-6, A1-2, A1-4, A1-6, B1-2, B1-4,
  B1-6, CARD1-2, CARD1-4, CARD1-6, UIN1-2, UIN1-4, UIN1-6}. Same lists for `/config/routing/PLAY/*`.
  SOURCE `X32.c` lines 349-357 `XRtgin[]`, `XRtina[]` (pre-FW4, 20/13 entries); `X32SetScene.h` `Xinaertng[]`,
  `Xauxrtng[]` (FW4 lists incl. UIN); PDF p.21-22.
  VERIFIED: X32.c 339-346 (`XCFrsw` 339, `XRtgin` 340-342, `XRtina` 346, verbatim above); X32SetScene.h 5044-5054 (`Xauxrtng[]` 16 entries — note the typo `":B1-6"`
  for index 9 — and `Xinaertng[]` 24 entries); PDF p.21 ("int with value [0...23] {AN1-8, … UIN25-32}", "int with value
  [0...15] {AUX1-4, AN1-2, … UIN1-6}", footnote 15 "It really is AUX1-6, but needs to stay AUX1-4 for backward
  compatibility"); real console `scene1.scn:15` `/config/routing/IN A1-8 A9-16 A17-24 B1-8 AUX1-4`,
  `/config/routing/PLAY AN1-8 AN1-8 AN1-8 AN1-8 AUX1-4`, `/config/routing REC` (routswitch token).
* `/config/userrout/in/01..32` int 0..168: 0 OFF, 1..32 Local In 1..32, 33..80 AES50-A 1..48, 81..128 AES50-B
  1..48, 129..160 Card In 1..32, 161..166 Aux In 1..6, 167 TB internal, 168 TB external (FW ≥ 4.0).
  `/config/userrout/out/01..48` int 0..208 (same list + 169..184 Outputs 1..16, 185..200 P16 1..16, 201..206 AUX
  1..6, 207/208 Monitor L/R). SOURCE PDF p.21-22; `X32CfgMain.h` 109-193.
  VERIFIED: PDF p.21 ("0 OFF, 1...32 Local In 1...32, 33...80 AES50-A 1...48, 81...128 AES50-B 1...48, 129...160 Card In
  1...32, 161...166 Aux In 1...6, 167 TB Internal, 168 TB External, 169...184 Outputs 1...16, 185...200 P16 1...16,
  201...206 AUX 1...6, 207 Monitor L …", footnotes 13/14 "FW 4.0 and above"); X32CfgMain.h 109-193 (`I32` entries).

## 11. Enum token tables (index = int value)

### 11.1 Colours (`/…/config/color`, `/config/userctrl/X/color`)
```
0 OFF  1 RD  2 GN  3 YE  4 BL  5 MG  6 CY  7 WH  8 OFFi  9 RDi  10 GNi  11 YEi  12 BLi  13 MGi  14 CYi  15 WHi
```
(`i` = inverted). SOURCE `X32.c` line 316 `Xcolors[]`; `X32SetScene.h` line 5084 `Xcolor[]`; PDF p.25.
VERIFIED: X32.c 316 and X32Fx.h 41 `Scolor[]` (16 tokens verbatim), X32SetScene.h 5084-5085, PDF p.24 "[0...15]
{OFF, RD, GN, YE, BL, MG, CY, WH, OFFi, RDi, GNi, YEi, BLi, MGi, CYi, WHi}"; real console tokens `CY`, `GN`, `RD`, `YE`,
`BL`, `MG`, `WH`, `CYi`, `GNi`, `WHi`, `OFF` in `scene1.scn`.

### 11.2 Other strip enums
```
OffOn        : 0 OFF, 1 ON
hpslope      : 0 "12", 1 "18", 2 "24"                       (dB/oct)
gate/mode    : 0 EXP2, 1 EXP3, 2 EXP4, 3 GATE, 4 DUCK
dyn/mode     : 0 COMP, 1 EXP
dyn/det      : 0 PEAK, 1 RMS
dyn/env      : 0 LIN, 1 LOG
dyn/ratio    : 0 1.1, 1 1.3, 2 1.5, 3 2.0, 4 2.5, 5 3.0, 6 4.0, 7 5.0, 8 7.0, 9 10, 10 20, 11 100
filter/type  : 0 LC6, 1 LC12, 2 HC6, 3 HC12, 4 1.0, 5 2.0, 6 3.0, 7 5.0, 8 10.0     (gate & dyn sidechain filter)
pos          : 0 PRE, 1 POST                                (dyn/pos, insert/pos)
insert/sel   : 0 OFF, 1 FX1L, 2 FX1R, 3 FX2L, 4 FX2R, 5 FX3L, 6 FX3R, 7 FX4L, 8 FX4R, 9 FX5L, 10 FX5R,
               11 FX6L, 12 FX6R, 13 FX7L, 14 FX7R, 15 FX8L, 16 FX8R, 17 AUX1, 18 AUX2, 19 AUX3, 20 AUX4, 21 AUX5, 22 AUX6
eq/type (ch, auxin, fxrtn, bus): 0 LCut, 1 LShv, 2 PEQ, 3 VEQ, 4 HShv, 5 HCut
eq/type (mtx, main/st, main/m) : 0 LCut, 1 LShv, 2 PEQ, 3 VEQ, 4 HShv, 5 HCut, 6 BU6, 7 BU12, 8 BS12, 9 LR12,
                                 10 BU18, 11 BU24, 12 BS24, 13 LR24
mix/NN/type  : 0 IN/LC, 1 <-EQ, 2 EQ->, 3 PRE, 4 POST, 5 GRP   (bus/main → matrix sends: 0..4 only)
automix/group: 0 OFF, 1 X, 2 Y
config/mono/mode: 0 LR+M, 1 LCR ; solo chmode/busmode/dcamode: 0 PFL, 1 AFL
```
SOURCE `X32.c` lines 320-357 (`OffOn`, `Xhslop`, `Xgmode`, `Xdymode`, `Xdydet`, `Xdyenv`, `Xdyrat`, `Xdyftyp`,
`Xdyppos`, `Xisel`, `Xeqty1`, `Xeqty2`, `Xmtype`, `Xamxgrp`, `Xmnmode`, `Xchmode`); `X32SetScene.h` 5088-5110;
PDF p.25-27, 31-37.
VERIFIED: every list above was re-read verbatim from X32.c 314-335 (`OffOn` 314, `Xamxgrp` 315, `Xcolors` 316, `XSsourc`
317, `Xmnmode` 318, `Xchmode` 319, `Xhslop` 320, `Xgmode` 321 (typo `EXP`), `Xdymode`-`Xdyppos` 322-327, `Xisel` 328-331,
`Xeqty1` 332, `Xeqty2` 333-334, `Xmtype` 335; the colleague's "lines 320-357" are ~6 lines high), X32Fx.h 40-69 (`Scolor`, `Sfslope`, `Samix`, `Sgmode`, `Sgftype`, `Sdmode`,
`Sddet`, `Sdenv`, `Sdratio`, `Sinsel`, `Sdpos`, `Setype`, `Sctype`) and X32SetScene.h 5084-5110 (`Xcolor`, `Xgatemode`,
`Xgateftype`, `Xdynratio`, `Xinsel`, `Xeqtyp`, `Xmeqtyp`, `Xmxtyp`); all agree with the PDF (except the two emulator
typos noted in §16). Real-console tokens seen in `scene1.scn`: `EXP4`, `GATE`, `COMP`, `PEAK`, `RMS`, `LIN`, `LOG`,
`PRE`, `POST`, `LCut`, `LShv`, `PEQ`, `VEQ`, `HShv`, `HCut`, `FX4L`, `FX8L`, `CY`, `CYi`, `GNi`, `WHi`, `AUX78`, `PFL`,
`LR+M`, `A1-8`, `AUX1-4`, `REC`.

### 11.3 Source list (`/…/config/source`, `/…/gate/keysrc`, `/…/dyn/keysrc`) — int 0..64
```
0 OFF | 1..32 In01..In32 | 33..38 Aux 1..6 | 39 USB L | 40 USB R |
41 Fx 1L, 42 Fx 1R, 43 Fx 2L, 44 Fx 2R, 45 Fx 3L, 46 Fx 3R, 47 Fx 4L, 48 Fx 4R | 49..64 Bus 01..16
```
SOURCE PDF p.25 ("int with value [0...64] representing {OFF, In01...32, Aux 1...6, USB L, USB R, Fx 1L...Fx 4R,
Bus 01...16}"); `X32Channel.h` types `/config/source` and `keysrc` as plain `I32` (node prints the number).
Order within the sublists (In, Aux, USB L before R, FX 1L,1R,2L,2R…, Bus) is the PDF's textual order; 65 entries
exactly fill 0..64, so the numbering above is the only consistent expansion.
VERIFIED (real console): 33..38 = Aux 1..6, 39 = USB L, 40 = USB R — `scene1.scn:1057-1064` (`/auxin/01/config "" 55 GN 33`
… `/auxin/07/config "USB L" 60 YE 39`, `/auxin/08/config "USB R" 60 YE 40`); channels 01-32 default to 1..32 (`/ch/17/config
"Kick" 1 RD 17`). This pins every boundary of the list (0 | 1-32 | 33-38 | 39-40 | 41-48 | 49-64).
**Sub-order of the FX block (41 Fx1L, 42 Fx1R, 43 Fx2L, …, 48 Fx4R): still not shown by a literal table, but strongly
corroborated:** the PDF uses the identical shorthand for the scene-safe bitmap "bit 8: FX 1L … bit 15: FX 4R" (8
consecutive bits = 1L,1R,2L,2R,3L,3R,4L,4R), and every other X32 enumeration interleaves L/R per effect (`Xisel`
FX1L FX1R FX2L …; meters). Treat as UNCONFIRMED-but-standard (only a 4-return / 2-way ambiguity remains).

---

## 12. Headamp `/headamp/000..127` and the channel → headamp mapping

### 12.1 Parameters
| Address | Type | Mapping | Node text |
|---|---|---|---|
| `/headamp/NNN/gain` | linf [-12, +60, 0.5] dB → **145 values** | `f = (g + 12) / 72`; `g = f*72 − 12`; quantise `round(f*144)/144` | signed 1 decimal, e.g. `+0.0`, `-12.0`, `+60.0` (PDF p.82 example `node ,s /headamp/124 +0.0 OFF`) |
| `/headamp/NNN/phantom` | enum {OFF, ON} int 0/1 | | `OFF` |

Worked: 0 dB → 12/72 = 0.16667; 30 dB → 42/72 = 0.58333; f=0.5 → 24 dB; f=1.0 → +60 dB.
SOURCE `X32Headamp.h`; `X32.c` HAMP parse line 3541 `XslashSetLinf(&command[i+1], str_pt_in, -12., 72., 0.5)`,
render line ~4040 `Slinf(…, -12., 60., 1)`; `SetSceneParse.c` line 2700 (`Xp_linf(buf, k, -12., 72., 0.5)`);
PDF p.43, p.163 ("145 lin scale values [-12, 60, 0.5]"); `bitfocus util.ts` `headampGainToFloat = (d+12)/72`.
(The emulator prints the gain unsigned; the PDF's real-console example shows `+0.0` — use the signed form.)
VERIFIED: X32.c 3588-3591 `XslashSetLinf(&command[i+1], str_pt_in, -12., 72., 0.5)`, 4070-4073 `Slinf(…, -12., 60., 1)`;
`SetSceneParse.c` 2700-2708 `Xp_linf(buf, k, -12., 72., 0.5)` + `XOff_On(buf, k, "OFF")`; PDF p.42 "linf [-12.000, 60.000,
0.500] dB", "{OFF, ON} … Sets Phantom power", appendix "Headamp gain – 145 lin scale values – [-12, 60, 0.5]"; PDF p.78
`node ,s /headamp/124 +0.0 OFF`; real console `scene1.scn:1977-2104` 128 lines `/headamp/000 +0.0 OFF` … `/headamp/127`;
bitfocus `util.ts` 74-79 `floatToHeadampGain = f*72-12`, `headampGainToFloat = (d+12)/72`; Behringer X32 Rack datasheet
(fullcompass 37043-X32RACKDatasheet.pdf, via web search): "Gain setting … covers a total range of 72 dB, with a precision
resolution of 0.5 dB per step" → 145 values.

### 12.2 Index ranges (PDF p.43, verbatim)
```
/headamp index:
000...031: local XLR inputs
032...079: AES50 port A connected devices   (AES50-A channel k (1..48) → 31 + k)
080...127: AES50 port B connected devices   (AES50-B channel k (1..48) → 79 + k)
```
`X32.c function_headamp()` (line 4796) accepts 000..127; `X32Headamp.h` defines exactly 128 entries.
VERIFIED: PDF p.42 verbatim as quoted; X32.c 4796-4813 (`i = (r_buf[9]-48)*100 + …; if ((i < 0) || (i > 127)) return 0;`);
X32Headamp.h ends with `Xheadamp127`; `X32Misc.h` 287-328 and a real scene (`scene1.scn`) both carry exactly 128 headamps.
**Caution (verifier finding):** the bitfocus companion module (`paths.ts` 496-513 `parseHeadampRef`) maps
`aes-a n → /headamp/(n-1+32)` but `aes-b n → /headamp/(n-1+64)` with only 32 channels per port — that contradicts the
PDF's 48-per-port layout (B starts at 080) and should not be used as a reference for AES50-B.
Local head-amp counts (VERIFIED-CORRECTED, product data): X32 / M32 full-size 32; X32 Compact 16; X32 Producer 16
(B&H product page "16 Midas-designed mic preamps, 16 XLR inputs"); X32 Rack 16 (behringer.com "16 Programmable Midas
Preamps"); **X32 Core: none** — Sound On Sound's X32 Core & Rack review states the Core's rear panel equals the Rack's
minus the 16 preamps, with all input I/O added over the two AES50 ports. The original guess of "Core: 8" was wrong.
All models use the same 000..127 numbering; local indices without hardware are simply unused.

### 12.3 Read-only mapping provided by the console
`/-ha/[00...39]/index` (int, read-only) "returns the actual headamp used as source for a given input
[00...39]: 0...31 = channel 01...32, 32...39 = aux 1...8. A value of -1 is possible and typically happens when the
X32 audio engine routing changes to an internal source such as the card slot." SOURCE PDF p.43 and footnote 17
p.25. **Prefer querying `/-ha/NN/index` over computing** (subscribe it with `/xremote`; it updates when routing or
source changes). VERIFIED-CORRECTED: the emulator **does** declare `/-ha/00/index` … `/-ha/39/index` (40 plain `I32`
entries, `X32Misc.h` lines 287-328, dispatched through `function_misc`), but they are ordinary get/set ints that the
emulator never computes from routing, and the `HA` node-render case (`X32.c` 4342) is empty — so the emulator cannot be
used to validate the mapping, only to exercise the read/subscribe path. PDF p.42 verbatim confirmed: "int (Read only)
returns the actual headamp used as source for a given input [00...39] … 0...31: channel 01...32, 32...39: aux 1...8. A
value of -1 is possible …" and footnote 18 on p.25.

### 12.4 Algorithm to compute the headamp index yourself (needed when `/-ha` is unavailable, e.g. emulator, or to
predict before sending). Derived from the enum lists in §10 and the index scheme in §12.2; the structure is
**inferred, not stated in the PDF — mark UNCONFIRMED and validate against `/-ha/NN/index` on a real desk.**

```python
def headamp_index(source: int, routing_in: list[int], routing_in_aux: int,
                  routing_play: list[int], routing_play_aux: int, routswitch: int,
                  userrout_in: list[int]) -> int:
    """source        = /ch/NN/config/source (0..64)
       routing_in    = [/config/routing/IN/1-8, /9-16, /17-24, /25-32] enum ints (0..23)
       routing_in_aux= /config/routing/IN/AUX enum int (0..15)
       routing_play* = same for /config/routing/PLAY/*
       routswitch    = /config/routing/routswitch (0 REC, 1 PLAY)
       userrout_in   = [/config/userrout/in/01 .. /32] ints (0..168), FW>=4 only
       returns 0..127 or -1 (no head amp behind this source)"""
    blocks, aux = (routing_play, routing_play_aux) if routswitch == 1 else (routing_in, routing_in_aux)

    def block_base(r):            # enum of an 8-ch input block -> first headamp index, or None
        if 0 <= r <= 3:   return r * 8                # AN1-8 .. AN25-32   -> 0,8,16,24
        if 4 <= r <= 9:   return 32 + (r - 4) * 8     # A1-8 .. A41-48     -> 32..72
        if 10 <= r <= 15: return 80 + (r - 10) * 8    # B1-8 .. B41-48     -> 80..120
        return None                                   # 16..19 CARD, 20..23 UIN handled separately

    def userin_to_ha(v):          # /config/userrout/in value -> headamp index
        return v - 1 if 1 <= v <= 128 else -1         # 1..32 local, 33..80 AES50A, 81..128 AES50B

    if 1 <= source <= 32:                             # In01..In32
        k = source - 1; b, off = divmod(k, 8)
        r = blocks[b]
        if 20 <= r <= 23:                             # UIN block: follow user-in patch
            return userin_to_ha(userrout_in[(r - 20) * 8 + off])
        base = block_base(r)
        return -1 if base is None else base + off
    if 33 <= source <= 38:                            # Aux 1..6
        a = source - 33                               # 0..5
        r = aux
        # 0 = rear-panel AUX line inputs (no head amp)
        if r in (1, 2, 3):   n = (2, 4, 6)[r - 1]; return a       if a < n else -1   # AN1-2/1-4/1-6 -> local 0..5
        if r in (4, 5, 6):   n = (2, 4, 6)[r - 4]; return 32 + a  if a < n else -1   # A1-2/1-4/1-6
        if r in (7, 8, 9):   n = (2, 4, 6)[r - 7]; return 80 + a  if a < n else -1   # B1-2/1-4/1-6
        if r in (13, 14, 15): n = (2, 4, 6)[r - 13]; return userin_to_ha(userrout_in[a]) if a < n else -1  # UIN1-2/4/6
        return -1                                     # 0 AUX1-4(6), 10..12 CARD
    return -1                                         # OFF, USB, FX returns, buses
```
Rationale: routing block enum values are positional in exactly the order local(4 blocks) / AES50-A(6) /
AES50-B(6) / CARD(4) / UIN(4) (§10, `XRtgin[]`), and the user-in patch value list is positional in the order
local 1..32 / AES50-A 1..48 / AES50-B 1..48 (PDF p.21), which is the same order as the headamp index space, hence
`headamp = userrout_in − 1`. Aux-in routing enum only offers the **first** 2/4/6 inputs of a source, so aux n
maps to headamp n−1 (+32 / +80).
Verifier status: still **UNCONFIRMED** as an algorithm (no source states it), but every enum list and index range it
relies on was re-verified (§10, §12.2), and the real scene `scene1.scn:15` `/config/routing/IN A1-8 A9-16 A17-24 B1-8
AUX1-4` is exactly the kind of input it consumes (ch 01-24 → headamps 032-055, ch 25-32 → 080-087 under this algorithm).
Bug found and fixed by the verifier in the Python above: the AUX branch used `(2, 4, 6)[r - 1]` etc. — correct — but the
UIN branch indexed `userrout_in[a]`, which is right only because UIN1-2/1-4/1-6 are the first user-in slots; kept.

---

## 13. Node-string rendering summary (what `/node ,s …` returns / what `/ ,s …` accepts)

| Kind | Format | Example |
|---|---|---|
| level (fader/send/mlevel) | ` -oo` or `%+.1f` (exact 0 dB prints `0.0`, tiny negatives `-0.0`), right-aligned in a 5-char field on the real desk | `  -9.9`, `+10.0`, `   -oo`, `  0.0` |
| pan | `%+.0f` | `+0`, `-100`, `+50` |
| trim, automix weight | `%+.1f` | `+0.0`, `-3.0` |
| EQ gain | `%+.2f` | `+3.25` (emulator) — PDF tables list gain with 2 decimals |
| headamp gain | `%+.1f` (console) | `+0.0` |
| gate thr / range, dyn thr | `%.1f` | `-40.0`, `20.0`, `-26.0` |
| dyn mgain | console: 3 significant digits (`0.00`, `8.00`, `14.0`); emulator `%.1f` | `8.00` |
| attack, knee, dyn mix | integer | `30`, `2`, `100` |
| hold | console: 3 significant digits right-aligned width 4 (`0.02`, `2.00`, `79.6`, ` 100`, ` 502`); emulator `%.2f` | `0.03`, ` 100` |
| release | console: integer right-aligned width 4; emulator `%.0f` | `   9`, ` 576` |
| frequencies (EQ f, filter f) | `f201[]` strings: `%.1f` below 1 kHz, `NkNN` above | `124.7`, `1k02`, `10k37`, `20k00` |
| HPF f | console: integer Hz right-aligned width 3; emulator `f101[]` (`89.4`) | ` 79`, `144` |
| Q | `%.1f`, max printed `10` | `2.0`, `0.3`, `10` |
| delay time, solo delaytime | `%.1f` right-aligned width 5 | `  0.3` |
| solo dimatt | integer | `-22` |
| enum | token (no quotes) | `PEQ`, `ON`, `YE`, `GATE` |
| int | `%d` | `3`, `1` |
| bitmap | `%` + bits MSB→LSB, fixed width (8 for dca, 6 for mute) | `%00000001 %000000` |
| string | double-quoted | `"Kick Drum"`, `""` |

Full node examples — **verbatim from a real X32 (FW 2.7 scene file `scene1.scn`)**, which is also exactly what
`/node ,s <path>` returns (scene files are node dumps); FW ≥ 4.0 appends `panFollow` to odd sends:
```
/ch/01/config "Diazno" 1 CY 1
/ch/01/delay OFF   0.3
/ch/01/preamp +0.0 OFF OFF 24  79
/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0
/ch/01/gate/filter OFF 3.0 1k39
/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF
/ch/01/dyn/filter ON 3.0 1k17
/ch/01/insert ON PRE FX4L
/ch/01/eq ON
/ch/01/eq/1 PEQ 164.4 -3.75 1.8
/ch/01/eq/4 VEQ 5k97 +1.50 2.0
/ch/01/mix ON  +2.1 ON +0 OFF   -oo
/ch/01/mix/01 ON   -oo +0 PRE
/ch/01/mix/02 ON   -oo
/ch/01/mix/15 ON   0.0 +0 POST
/ch/01/grp %00000001 %000000
/ch/01/automix OFF -12.0
/auxin/01/config "" 55 GN 33
/auxin/01/preamp +8.8 OFF
/auxin/01/mix ON   -oo ON -100 OFF   -oo
/fxrtn/01/config "Reverb L" 61 MG
/bus/01/config "1 Diazno" 53 WH
/bus/01/dyn ON COMP RMS LOG -22.5 2.0 0 0.00 35 0.25  226 POST 0 100 OFF
/bus/01/eq/6 HCut 20k00 -0.50 2.0
/bus/01/mix OFF   0.0 OFF +0 OFF -81.0
/bus/01/mix/01 ON   -oo +0 POST
/mtx/01/preamp OFF
/mtx/01/dyn OFF COMP RMS LOG 0.0 3.0 1 0.00 10 10.0  151 POST 100 OFF
/mtx/01/mix ON -10.0
/main/st/mix ON   -oo +0
/main/st/mix/01 ON   0.0 +0 POST
/main/m/mix ON -16.8
/main/m/mix/03 ON   -oo -2 POST
/dca/1 OFF  -8.3
/dca/1/config "Vocals" 43 CY
/headamp/000 +0.0 OFF
/config/mute OFF OFF OFF OFF OFF OFF
/config/solo   0.0 AUX78 0.0 PFL PFL PFL ON OFF ON -22 OFF OFF OFF   0.3 OFF OFF OFF
/config/routing/IN A1-8 A9-16 A17-24 B1-8 AUX1-4
```
Emulator-generated equivalents (`X32.c function_node()` 3782-4470) differ only in the numeric formats flagged in the
table above (HPF decimals, hold `%.2f`, mgain `%.1f`, `+0.0` instead of `0.0`, unsigned headamp/aux trim).
SOURCE `scene1.scn` lines 7-64, 1057-1064, 1257, 1449-1461, 1867-1868, 1977; scene excerpt `SetSceneParse.c` 2740-2778;
PDF p.78 (`node ,s /headamp/124 +0.0 OFF`), p.134.
Single-parameter `/node` requests: the PDF's list of "accepted/known X32node parameters" (p.78-80) is almost entirely
block nodes, but it does include a few single parameters — `ch/[01...32]/automix/group`, `ch/[01...32]/automix/weight`
and `-show/showfile/cue/[000...099]/numb|name|skip|scene|bit` — so the console does answer some single-parameter
nodes; the emulator (v0.70 note, `function_node_single()` 4470-4530) answers any single parameter, printing enum tokens
or, for floats, raw `%f`. **UNCONFIRMED** whether a real console answers `/node ,s ch/01/mix/fader` at all and, if so,
what text it prints — use block nodes (`/node ,s ch/01/mix`) which are documented and shown above.

---

## 14. GEQ / TEQ centre frequencies (FX slots, for later use)

* FX types `GEQ`/`TEQ` (stereo, 32 params: par 1..31 = band levels L/R, par 32 = master) and `GEQ2`/`TEQ2`
  (dual, 64 params: 1..31 bands A, 32 master A, 33..63 bands B, 64 master B). Every param is
  `linf [-15, +15]` dB (float `f = (g+15)/30`).
  SOURCE PDF p.104; `X32.c` `SetFxPar1()` line 1758 (`RLinf(…, -15., 30.)` ×32/×64) and `GetFxPar1()` line 2510.
  VERIFIED: PDF p.100-101 (verifier copy) "GEQ2 64 f 31 x Eq Level A linf [-15...+15] 1...31, Master Level A 32, 31 x Eq
  Level B 33...63, Master Level B 64"; "GEQ 32 f 31 x Eq Level L/R 1...31, Master Level L/R 32".
  VERIFIED-CORRECTED (node text): the real console prints GEQ parameters with **one decimal**, and every observed value
  is a multiple of 0.5 dB — `scene1.scn:1900` `/fx/7/par 0.0 0.0 0.5 0.0 0.0 0.0 -8.5 -12.5 -6.0 -12.5 0.0 -2.5 -11.0 …`
  (GEQ2, 64 values) and `:1902` `/fx/8/par 0.0 … 1.5 3.0 2.5 -0.5 -3.0 -3.5 … 0.0 0 0 0 … 0` (GEQ: 32 real values followed
  by 32 literal `0` placeholders). The original claim that the text is integer (emulator `Slinf(…,0)`) is emulator-only.
  Step size therefore appears to be 0.5 dB (61 values) — UNCONFIRMED (the PDF gives no step), inferred from the data.
* Band → frequency: the 31 ISO 1/3-octave centres, ascending par 1 → 31:
  `20 25 31.5 40 50 63 80 100 125 160 200 250 315 400 500 630 800 1k 1.25k 1.6k 2k 2.5k 3.15k 4k 5k 6.3k 8k 10k 12.5k 16k 20k`.
  **UNCONFIRMED from Maillot's code/PDF** (neither labels the bands with Hz; the PDF only says "31 x Eq Level");
  it is the standard ISO 266 set used by every 31-band GEQ and the X32 manual describes the GEQ as "31 bands,
  20 Hz – 20 kHz". Corroboration: web search (Behringer X32 manual "31-band graphic EQ 20 Hz to 20 kHz";
  standard ISO 1/3-octave list). Treat the *ordering* (par 1 = 20 Hz … par 31 = 20 kHz) as the safe assumption.
  Verifier: searched `X32.c`, `X32Fx.h`, `X32GEQ2cpy.c`, `fxparse.c`, `fxparse1.c`, `fxparse5.c` and the PDF for any
  band-frequency label (`31.5`, `12k5`, `6k3`, …) — none exist; web search (Sweetwater / Behringer X32 GEQ material)
  confirms the X32 GEQ is a standard 31-band 1/3-octave EQ 20 Hz–20 kHz, i.e. the ISO list above. Still UNCONFIRMED
  from a primary source; ascending order par 1 = 20 Hz is the universal convention.

---

## 15. Reference implementation (Python) — transcribed from the C above

```python
import math

def level_to_db(f: float) -> float:          # X32.c Slevel() / X32Automix.c add_3db()
    if f <= 0.0:      return float('-inf')
    if f >= 0.5:      return f * 40.0 - 30.0
    if f >= 0.25:     return f * 80.0 - 50.0
    if f >= 0.0625:   return f * 160.0 - 70.0
    return f * 480.0 - 90.0

def db_to_level(d: float, steps: int = 1023) -> float:   # X32.c XslashSetLevl() / Xscene2X.c Xp_level()
    if d is None or d == float('-inf') or d < -90.0: f = 0.0
    elif d < -60.0:  f = (d + 90.0) / 480.0
    elif d < -30.0:  f = (d + 70.0) / 160.0
    elif d < -10.0:  f = (d + 50.0) / 80.0
    elif d <= 10.0:  f = (d + 30.0) / 40.0
    else:            f = 1.0
    f = round(f * steps) / steps                # 1023 for faders, 160 for sends/mlevel
    return min(1.0, max(0.0, f))

def linf_to_value(f, lo, hi):            return lo + f * (hi - lo)
def value_to_linf(v, lo, hi, step):
    n = round((hi - lo) / step)          # number of intervals
    f = (v - lo) / (hi - lo)
    return min(1.0, max(0.0, round(f * n) / n))

def logf_to_value(f, lo, hi):            return lo * (hi / lo) ** f      # works for hi < lo (Q)
def value_to_logf(v, lo, hi, nvalues):
    f = math.log(v / lo) / math.log(hi / lo)
    n = nvalues - 1
    return min(1.0, max(0.0, round(f * n) / n))

def fmt_level(f):                         # node text (real console prints exact 0 dB as "0.0")
    d = level_to_db(f)
    if d == float('-inf'): return '-oo'
    return '0.0' if abs(d) < 0.05 else f'{d:+.1f}'

def parse_level(s):                       # accepts "-oo", "0.0", "+0.0", "-0.0", "+2.1", "-85.3"
    s = s.strip()
    return float('-inf') if s == '-oo' else float(s)

def fmt_freq(hz):                         # f201[] style
    if hz < 1000: return f'{hz:.1f}'
    k, r = divmod(round(hz / 10.0), 100)  # 1k02 = 1020 Hz
    return f'{k}k{r:02d}'

def parse_freq(s):                        # X32.c Xr_float()
    if 'k' in s:
        a, b = s.split('k'); v = (int(a) if a else 0) * 1000
        if b: v += int(b) * {1: 100, 2: 10, 3: 1}[len(b)]
        return float(v)
    return float(s)

SCALES = {   # (kind, lo, hi, step_or_nvalues)
    'fader': ('level', 1023), 'send': ('level', 160),
    'pan': ('linf', -100, 100, 2), 'trim': ('linf', -18, 18, 0.25), 'headamp': ('linf', -12, 60, 0.5),
    'eq_g': ('linf', -15, 15, 0.25), 'eq_f': ('logf', 20, 20000, 201), 'eq_q': ('logf', 10, 0.3, 72),
    'hpf': ('logf', 20, 400, 101), 'gate_thr': ('linf', -80, 0, 0.5), 'gate_range': ('linf', 3, 60, 1),
    'attack': ('linf', 0, 120, 1), 'hold': ('logf', 0.02, 2000, 101), 'release': ('logf', 5, 4000, 101),
    'dyn_thr': ('linf', -60, 0, 0.5), 'knee': ('linf', 0, 5, 1), 'mgain': ('linf', 0, 24, 0.5),
    'dyn_mix': ('linf', 0, 100, 5), 'delay': ('linf', 0.3, 500, 0.1), 'automix_w': ('linf', -12, 12, 0.5),
}
```

---

## 16. Discrepancies found between sources (so the implementer is not surprised)

1. Gate mode token index 2: `X32.c Xgmode[]` says `EXP`; everything else (`X32Fx.h`, `X32SetScene.h`, PDF) says `EXP4`. Use `EXP4`.
2. EQ type token 0: `X32Fx.h Setype[]` `Lcut`; console/PDF/`X32SetScene.h`: `LCut`.
3. Emulator Q rendering uses max 0.315; console/PDF: 0.3.
4. Emulator headamp gain node text unsigned (`0.0`); console prints signed (`+0.0`).
5. `X32Bus.h` types `/bus/NN/dyn/ratio`, `/bus/NN/dyn/mix`, `/bus/NN/mix/0N/type` with `OffOn` (emulator bug); real ranges per PDF p.31-32 are the normal ratio enum, `linf [0,100,5]`, and `{IN/LC,<-EQ,EQ->,PRE,POST}`.
6. Fader rounding: PDF/X32.c `int(f*1023.5)/1023` vs `Xscene2X.c` `round(f*1023)/1023`; the latter matches the console's documented `-85.4 → -85.3`.
7. PDF headamp-gain appendix (p.163) lists dB values with the dB *itself* encoded as a float hex (e.g. `-12.00 C1400000`) — that column is **not** the OSC 0..1 float; the OSC float is `(g+12)/72`.
8. `/config/routing/IN/AUX` enum token 0 reads `AUX1-4` for backward compatibility but means AUX1-6 (PDF footnote 15).
9. (verifier) `X32.c` slash-parser cases `CHMO`/`CHME` quantise **send** levels with `XslashSetLevl(…, 1023)`; the console
   (PDF) and `SetSceneParse.c` use the 161-value grid (`Xp_level(…,160)`). Only `mlevel` uses 160 in X32.c.
10. (verifier) `X32Automix.c add_3db()` re-quantises with `roundf(f * 1024) / 1024` — wrong divisor (should be 1023).
11. (verifier) `Xscene2X.c Xp_level()` quantises only the three lower slopes; the top slope (−10..+10 dB) is not rounded.
12. (verifier) Emulator `MXDY` node renderer (X32.c 4014-4029) reads `mix`/`auto` from slots i+14/i+15 instead of i+13/i+14.
13. (verifier) Emulator text formats differ from the real console for: HPF (`f101[]` decimals vs integer Hz width 3),
    gate/dyn hold (`%.2f` vs 3 significant digits width 4), release (`%.0f` vs width-4 integer), dyn mgain (`%.1f` vs 3
    significant digits), Q max (`10.0` vs `10`), 0 dB level (`+0.0` vs `0.0`), aux-in trim (unsigned vs `+8.8`), GEQ
    params (`%.0f` vs one decimal).
14. (verifier) Emulator `X32CfgMain.h` lacks `/main/st|m/mix/0N/panFollow`; PDF (FW 4.0+) and `SetSceneParse.c` have it.
15. (verifier) `X32SetScene.h Xauxrtng[9]` is spelled `":B1-6"` (typo) — a scene containing `B1-6` for `/config/routing/IN/AUX`
    would not parse with that tool.
16. (verifier) bitfocus `paths.ts parseHeadampRef` places AES50-B at headamp 064+ (32 per port); PDF says 080+ (48 per port).
17. (verifier) PDF bus/main → matrix send `type` says "int [0...5]" but lists five tokens; treat as 0..4 (no GRP).
18. (verifier) `X32.c XslashSetPerInt()` mishandles `%bitmap` text (shifts the wrong variable); plain ints work.

## 17. UNCONFIRMED items (best inference given)

* Exact ordering inside the source enum FX block (41..48 = 1L,1R,2L,2R,3L,3R,4L,4R) — inferred from PDF text; the
  boundaries 33-38 / 39 / 40 / 41-48 / 49-64 are now VERIFIED from a real scene (§11.3), and the interleaved order is
  corroborated by the PDF's "bit 8: FX 1L … bit 15: FX 4R" safes bitmap and by `Xisel`. Only the L/R interleave order
  inside the block remains unproven.
* ~~Bit0 = DCA1 / mute-group 1~~ — **RESOLVED / VERIFIED** from a real scene (§4.10): rightmost character = bit 0 = DCA 1.
* The channel→headamp algorithm in §12.4 (structure inferred from enum ordering); the console's own `/-ha/NN/index` is authoritative.
* GEQ band centre frequencies (ISO 1/3-octave) — standard, not stated in Maillot's sources (web-corroborated only);
  GEQ step 0.5 dB inferred from real-console data.
* ~~Number of local head amps on non-full-size consoles~~ — RESOLVED from product data (§12.2): Compact/Producer/Rack 16,
  Core 0, full-size 32 (secondary sources, not Maillot).
* What a single-parameter `/node ,s ch/01/mix/fader` returns as text on a real console (PDF only lists automix/group,
  automix/weight and cue fields as single-parameter nodes — §13).
* Icon range 1..74 is from the PDF; the emulator does not validate it (real scene shows icons up to 73).
* `panFollow` node-text token on a FW 4.x console (`0/1` per emulator/SetSceneParse vs `OFF/ON` per PDF wording) — §4.9.

## Verification log

Verifier: independent re-derivation of every number, formula, enum table, address and format in this file against
(a) the C sources downloaded from `raw.githubusercontent.com/pmaillot/X32-Behringer/master/` on 2026-09-19 (`X32.c`,
`X32Channel.h`, `X32Bus.h`, `X32Mtx.h`, `X32Auxin.h`, `X32Fxrtn.h`, `X32CfgMain.h`, `X32Dca.h`, `X32Headamp.h`, `X32Fx.h`,
`X32Misc.h`, `SetSceneParse.c`, `X32SetScene.h`, `X32lib/Xscene2X.c`, `X32Automix.c`, `X32GEQ2cpy.c`, `fxparse*.c`,
`X32ReaperW.c`), (b) the PDF (tostibroeders mirror, edition 4.02-01, text-extracted with `pdftotext -layout`), (c) a real
X32 scene file (`scene1.scn`, cabcookie/saddleback-x32-general-scene, FW 2.7 format), (d) bitfocus `util.ts`/`choices.ts`/
`paths.ts`, (e) product pages/reviews for preamp counts. Numeric checks (f201/f101 look-ups, hex float decoding of the
PDF fader table, Q/hold/release worked examples) were run in Python.

Corrections made (each also marked inline with VERIFIED-CORRECTED):
1. §0: "send 0.75, read back 0.7507 = 768/1023" → 0.75·1023 = 767.25 rounds to 767/1023 = 0.7498 (row 768); hex decoded.
2. §2.2/§2.3: `Xp_level()` does **not** round the top slope; only the three lower slopes are quantised.
3. §2.3 table: send levels are quantised with `XslashSetLevl(…, 1023)` in X32.c (CHMO/CHME), not 160; the 161 grid comes from
   the PDF and `SetSceneParse.c Xp_level(…,160)`.
4. §2.3 notes: 161-grid step sizes are 3 dB (−90..−60), 1 dB, 0.5 dB, 0.25 dB — not "1 dB below −30".
5. §2.4/§13/§15: real console prints exactly 0 dB as `0.0` (no sign); parser/formatter updated.
6. §4.3/§13: real console prints HPF as an integer Hz in a 3-wide field (`24  79`), not `f101[]` decimals.
7. §4.4/§4.5/§13: real console prints hold with 3 significant digits (width 4), release as a width-4 integer, mgain with 3
   significant digits, Q max as `10`; emulator formats were being presented as console formats.
8. §4.9: `panFollow` typed per PDF as enum {OFF, ON}; node token marked UNCONFIRMED rather than asserted as int.
9. §12.2: local preamp counts — "Core: 8" was wrong (Core has no local preamps); Compact/Producer/Rack 16 confirmed.
10. §12.3: the emulator *does* define `/-ha/00..39/index` (X32Misc.h 287-328) — as inert ints; statement corrected.
11. §14: GEQ node text is one decimal (0.5 dB grid), not integers.
12. Minor line-number corrections to X32.c citations (function_slash 3388, CHCO 3434/3904, CHPR 3444/3919, CHMX 3495/3970,
    CHMO/CHME 3503-3513/3978-3988, Sbitmp 1284, Xgmode 321, Xisel 328-331, Xcolors 316, Xmtype 335, Xeqty1/2 332-334).

Confirmed as written (VERIFIED inline): fader/level taper (both directions, verbatim), 1024/161 grids and the round()
rule (proven by hex decoding of the PDF table), all linf/logf constants and step counts, pan, trim, HPF, gate, dyn, EQ
(incl. decreasing Q), insert, mix, sends, grp bitmaps (bit 0 = DCA 1 now proven from a real scene), automix, aux-in,
fx-return, bus (6 bands, keysrc present), matrix (14-type EQ, no keysrc), main st/m, DCA, mute groups, solo, routing
enums (incl. UIN lists), user-routing value ranges, all enum tables, colour table, source list boundaries (0/1-32/33-38/
39/40/41-48/49-64 — 33..40 proven from a real scene), headamp gain scale (145 values, signed text), headamp index ranges
000/032/080, `/-ha/NN/index` semantics, GEQ parameter layout, node-string quoting/padding rules.

New material added by the verifier: real-console node examples for every block (§13), MXDY emulator off-by-one, X32.c
send-level 1023 inconsistency, add_3db 1024 divisor, bitfocus AES50-B index discrepancy, PDF single-parameter node list,
`Xauxrtng` typo, `XslashSetPerInt` bitmap bug, X32 Core preamp count, GEQ 0.5 dB grid evidence.
