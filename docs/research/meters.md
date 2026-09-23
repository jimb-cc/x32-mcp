# X32/M32 OSC — Metering (`/meters`) and RTA — ground-truth research

Research date: 2026-09-19. Area: `/meters/0…16`, blob wire format, RTA (`/meters/15`), dynamics/automix (`/meters/16`), `/-prefs/rta/*`, `/-stat/rta*`, `/-action/setrtasrc`, subscription lifetime/renewal.

## Sources actually read (priority order)

| Tag | Source |
|---|---|
| **[X32.c]** | `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32.c` — the X32 **emulator**. Meter code: `Xmeters[]` table (lines 531–551), `Xprepmeter()` (lines ~4818–4861), `function_meters()` (lines ~4864–4940), `function_renew()` (~4959), enum tables `PRpos/PRmode/PRdet/Prtavis/Prtaph` (lines ~402–416), `/-prefs/rta` node formatting (lines ~3644–3655 parse, ~4131–4141 print). |
| **[Xdump.c]** | `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32lib/Xdump.c` — Maillot's OSC packet dumper used by X32_Command; the **only C code in the repo that decodes real console meter blobs**, incl. `/meters/15` and `/meters/16` special cases (functions `Xdump()`, `Xsdump()`). Header comment: "Updated Sep. 1 2016 to support /meters/16 type (X32 fw 3.04)". |
| **[PrefStat.h]** | `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32PrefStat.h` — emulator parameter tree for `/-prefs/rta/*` (lines 142–152), `/-stat/rta*` (307–311), `/-action/setrtasrc` (478). |
| **[Automix.c]** | `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32Automix.c` — a real client that subscribes `/meters/1` against a real console and reads the floats (lines 140, 436–438, 613–635). |
| **[DOC 4.09]** | Maillot, *Unofficial X32/M32 OSC Remote Protocol*, **version 4.09-02 (Nov 30 2025)**, "Applies to console FW ver 4.0 and later". Downloaded from the Google-Drive link on `https://sites.google.com/site/patrickmaillot/x32` (`https://drive.google.com/file/d/1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz`), text extracted with pypdf. Meter chapter = PDF pages 17–20 ("Meter requests", "List of all Meter IDs"); RTA prefs = pages 59–60; `/-stat/rta*` = page 63; subscriptions = pages 77–79; RTA decay appendix = page 159. Older copies (v4.02-01 Jan 2020 at `tostibroeders.nl/.../X32-OSC.pdf`, v4.06-09 Mar 2022 at `x32ram.com/.../X32-OSC.pdf`) were diffed: the meter chapter is **identical** in all three. |
| **[x32-osc.ts]** | `https://raw.githubusercontent.com/markschwartzkopf/x32-osc/master/index.ts` — community TypeScript client (request form + float decode). Corroboration only. |
| **[x32-physical #36]** | `https://github.com/anbergem/x32-physical/issues/36` — empirical survey on a real X32, **fw 4.06**, 2026-08-30. Corroboration only. |
| **[xair-osc]** | `https://github.com/notameadow/xair-osc` README — X-Air (XR18) differences. Corroboration only. |
| **[issue #8]** | `https://github.com/pmaillot/X32-Behringer/issues/8` — X-Air vs X32 blob data type. |
| **[M32 manual]** | Midas M32 user manual p.31 (manualslib) — RTA source / solo priority semantics. |
| **[X32 manual]** | Behringer *X32 Digital Mixer Preliminary User Manual*, §7.3 "Meters Screen", item 5 "RTA" — full-size manual p.50 (`https://media.vsl-uk.com/manuals/sound/Behringer_X32_User_Manual.pdf`) and X32 Compact manual p.51 (`https://www.adorama.com/col/productManuals/BEX32COMPACT.pdf`), text extracted with pypdf by the verifier. Names the **six RTA check boxes** that are the six bits of `/-prefs/rta/options`. Added by verifier. |
| **[XAir_Command.c]** | `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/XAir_Command.c` — X-Air variant with its own `Xdump()` ("meters are different (more consistent) than on X32"). Added by verifier. |
| **[DeskRestore.c]** | `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32DeskRestore.c` (commented-out leaf list, lines 864–878, 1074–1112) and `X32ds_node.h` line 29 (the node list actually used by X32DeskSave/Restore). Added by verifier. |

**Page-number convention (verifier note):** the PDF's printed page number = PDF page index − 1 (e.g. the meter chapter is PDF page 17, printed "16"). The colleague's citations mix the two: "p.16" (meter chapter) and "p.8" (summary) are *printed* numbers; "pp.59–60" (RTA prefs), "p.63" (`/-stat/rta*`), "p.65" (METER page), "pp.77–79" (subscriptions) and "p.159" (decay appendix) are *PDF* page indices (printed 58–59, 62, 64, 76–78, 158). All quoted text was re-located and re-read by the verifier in the same 4.09-02 PDF (`pypdf` text dump); VERIFIED.

Not reachable: `behringerwiki.musictribe.com` (DNS), `behringer.world` forum thread t=1545 (HTTP 403), docplayer (DNS). Nothing below depends on them. (Verifier re-tried both behringer.world pages with a browser User-Agent via curl and via WebFetch: still 403; behringerwiki still ENOTFOUND.)

---

## 1. Request forms

### 1.1 General form

**SOURCE [DOC 4.09] p.16, verbatim:**

> The typical format for /meters is as follows:
> `/meters ,siii <meter request and parameters (see below)> [time_factor]`
> The highlighted sii tags are used for the meter request, comprising a string and two ints depending on the meter request type. The command is active for about 10s. … The last int of the command is used to control the number of times the requestor will receive meter values.

Summary table in the same doc (p.8) lists the arguments as: `/meters <string> <optional int: chn_meter_id> <optional int: grp_meter_id> <optional int: priority>` — "priority" there is the same thing the detailed chapter calls `time_factor`.

Accepted concrete forms (all confirmed). VERIFIED: X32.c `function_meters()`/`Xprepmeter()` lines 4819–4931 re-read by verifier; DOC PDF p.17 hex example re-decoded.

| Form | Typetag | Meaning | Source |
|---|---|---|---|
| `/meters ,s /meters/N` | `,s` | subscribe meter N, default 50 ms interval, 10 s | [DOC 4.09] p.8 example `/meters ,s meters/1`; [X32.c] `Xprepmeter`: `if (n == 1) { XDeltaMeters[i] = 50000; …}` |
| `/meters ,si /meters/N <tf>` | `,si` | subscribe with time factor | [DOC 4.09] p.16 hex example `/meters~,si~/meters/6~~~16` |
| `/meters ,sii /meters/6 <channel_id> <tf>` | `,sii` | meters/6 needs one int argument | [DOC 4.09] p.18; [X32.c] `case 6: Xprepmeter(6, 4, "/meters/6\0\0\0,b\0\0", n, 8);` (tf read at byte 24+8=32, i.e. the **2nd** int) |
| `/meters ,siii /meters/5 <chn_meter_id> <grp_meter_id> <tf>` | `,siii` | meters/5 needs two int arguments | [DOC 4.09] p.18 and p.78 example `/batchsubscribe ,ssiii /rr /meters/5 3 1 40`; [X32.c] `case 5: Xprepmeter(5, 27, …, n, 12);` (tf read at byte 24+12=36, i.e. the **3rd** int) |
| `/meters ,siii /meters/N 0 0 <tf>` | `,siii` | generic form; unused ints are 0 | [Automix.c] line 140: `char Meters[] = "/meters\0,siii\0\0\0/meters/1\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0";` with `Meters[39] = (char)i;` (tf in last int; 40-byte datagram: 8 + 8 + 12 + 3×4) sent with `sendto(Xfd, Meters, 40, …)` (line 619) — works on real hardware; [x32-osc.ts] always sends `['/meters/N', arg1, arg2, tf]` as `,siii` (its `_x32UpdateFrequency` defaults to 10 = 500 ms, clamped to 1…99). VERIFIED: Automix.c lines 140, 438, 619, 630 re-read. Automix derives tf from a millisecond delay: `i = X32i_delay / 50; … Meters[39] = (char)i;` (lines 436–438) — real-hardware confirmation that **tf = interval_ms / 50**. |

Wire bytes of the documented `,si` example (**SOURCE [DOC 4.09] p.16, verbatim hex**):

```
2f6d6574657273002c7369002f6d65746572732f3600000000000010
 / m e t e r s ~ , s i ~ / m e t e r s / 6 ~ ~ ~[    16]
```
i.e. `"/meters\0"` (8) + `",si\0"` (4) + `"/meters/6\0\0\0"` (12) + int32 **big-endian** 16 (4) = 28 bytes. Request-side ints are normal OSC big-endian.

Emulator address matching (**[X32.c] `function_meters()`**, verbatim):
```c
n = strlen(r_buf + 9);			// counting the number of parameters
i = 0;
while (i < Xmeters_max) {
    if (strcmp(r_buf + ((9 + n + 4) & ~3), Xmeters[i].command) == 0) { break; }
    i += 1;
}
```
`Xmeters[i].command` are the literal strings `"/meters/0"` … `"/meters/16"` (with leading slash) — VERIFIED: X32.c lines 531–551. `n` is the typetag length after the comma (`,s`→1, `,si`→2, `,sii`→3, `,siii`→4) and `(9+n+4)&~3` yields 12 for `,s`/`,si` and 16 for `,sii`/`,siii`, i.e. the address string always starts right after the padded typetag — VERIFIED by arithmetic. The DOC's short examples write `meters/1` without the slash; the DOC's byte-level example uses `/meters/6` **with** the slash. **Use the leading-slash form** — it is the one proven by the hex dump and by [Automix.c]/[x32-osc.ts]. (Whether the real console also accepts the slash-less spelling is UNCONFIRMED; the emulator does not.)

### 1.2 Time factor semantics

**SOURCE [DOC 4.09] p.16, verbatim:**
> Update cycle frequency for meter data is 50 ms, and may be variable according to console's ability to fulfill requests. Timeout is 10 seconds.
> time_factor is a value between 1 and 99 setting the interval between two successive meters messages to 50ms * time_factor. Any value of time_factor outside or [1, 99] is equivalent to 1. For a timespan of 10s, the number of updates can be calculated based on the value of time_factor as below:
> time_factor: <2 or >99 → 200 updates; 2 → 100 updates; […] 40 → 5 updates; 80 to 99 → 3 updates

**SOURCE [X32.c] `Xprepmeter()`, verbatim:**
```c
gettimeofday (&XTimerMeters[i], NULL);		// get time
XInterMeters[i] = XTimerMeters[i];			// keep initial time for inter-timers
XTimerMeters[i].tv_sec += 10;				// keep valid for 10s
XActiveMeters |= (1 << i);					// set meter to active
XClientMeters[i] = *Client_ip_pt;			// remember requesting IP client
if (n == 1) {								// special case of a single shot for meters/i
    XDeltaMeters[i] = 50000;				// set meter interval at 50ms
    return;
} else {
    // get time factor at end of command /meters ,si /meters/i [tf]
    // manage values < 1 and > 99 by setting interval to 50ms
    // k represents an index afetr 24 where to find the time factor data
    endian.cc[3] = r_buf[k + 24]; endian.cc[2] = r_buf[k + 25];
    endian.cc[1] = r_buf[k + 26]; endian.cc[0] = r_buf[k + 27];
    if ((endian.ii < 1) || (endian.ii > 99)) endian.ii = 1;
    XDeltaMeters[i] = 50000 * endian.ii;	// set meter interval at 50ms x time factor
    return;
}
```
VERIFIED: X32.c lines 4838–4857 and DOC PDF p.17 re-read. Formula: `interval_ms = 50 * clamp_to_1_if_outside(tf, 1..99)`; frames in the 10 s window ≈ `10000 / (50*tf)` → tf=1: 200, tf=2: 100, tf=10: 20, tf=20: 10, tf=40: 5, tf=80…99: 2–3. Nominal frame rate at tf=1 is 20 Hz (50 ms). The DOC (p.77) gives the same table for `/subscribe` but says "0 → 200 updates … values outside [0…99] are considered 0" — for `/meters` treat 0 and 1 identically (both = 50 ms).

Note (emulator only): the emulator reads tf at byte offset `k+24`; for meters other than 5/6 this only lines up with the `,si` form — the `,siii … 0 0 tf` form is proven on real hardware by [Automix.c], not by the emulator. (Verifier: with `,siii` the address string occupies bytes 16–27, so the emulator reads bytes 24–27 = `"1` + three NULs = 0x31000000, which is > 99 and is clamped to 1 → 50 ms. Harmless, but it means the emulator ignores tf for `,siii` requests to ids other than 5/6.) The DOC's own `/formatsubscribe … 80` capture is annotated "Each response from X32 above is spaced by about 3 seconds" (PDF p.77), i.e. slightly faster than 50 ms × 80 = 4 s — treat the interval as approximate at large tf.

### 1.3 Subscription lifetime and renewal

- Lifetime **10 s** from the last `/meters` request ([X32.c] `tv_sec += 10`; [DOC 4.09] "Timeout is 10 seconds"). VERIFIED: X32.c line 4840; emulator main loop lines 1066–1080 sends `Xbuf_meters[i]` every `XDeltaMeters[i]` µs while `XTimerMeters[i] > now`, then clears the active bit.
- Re-sending the same `/meters …` command restarts the 10 s window (emulator re-arms `XTimerMeters[i]` and sets the bit again — no duplicate stream). Real-world clients simply resend: [Automix.c] resends `/xremote` and the `/meters` message every `XTIMEOUT = 9` s (`#define XTIMEOUT 9 // time-out set to 9 seconds`, line 55; `RunAutomix()` lines 615–621: `if (X32Rmte_now > X32Rmte_bfr) { sendto(xremote,12); sendto(Meters,40); X32Rmte_bfr = X32Rmte_now + XTIMEOUT; }`) — VERIFIED; [xair-osc]: "Subscriptions expire at ~10 s; renew every 5 s". Recommendation: resend every 5 s (tolerates one lost datagram).
- `/renew`: **SOURCE [DOC 4.09] p.8 & p.79, verbatim:**
  > `/renew <string>` Requests renewing of data described in <string>, e.g. `/renew~~,s~~meters/5~~~~`, `/renew~~,s~~hidden/states~~~`
  > … subscriptions have to be renewed with the /renew command. The command takes one optional argument, a string type to specify the subscription to renew. This will be either the name of the actual command or the name of the command alias for renewing /formatsubscribe and /batchsubscribe commands. It is possible to renew all active subscriptions by not providing any name to the /renew command.
  > If not renewed within 10 seconds, the /subscribe, /formatsubscribe, /batchsubscribe commands names and attributes are forgotten and lost. Indeed, an attempt to renew one of the above commands received past the 10s delay will have no effect.
  VERIFIED: DOC PDF p.9 and p.78 re-read (both quotes present verbatim). So: `/renew` (no args) renews everything; `/renew ,s /meters/1` renews one. After expiry `/renew` is a no-op — you must re-issue `/meters`. (The emulator's `function_renew()` is a stub: `// Ignored for now / Todo … return S_SND;` — so only the DOC/real hardware confirms `/renew`.)
- `/unsubscribe [string]` stops one or (no arg) all subscriptions ([DOC 4.09] p.79).
- Alias form: `/batchsubscribe ,ssiii <alias> /meters/N <i0> <i1> <tf>` — same blob, but the reply's address is `<alias>` (start it with `/`). Examples from [DOC 4.09] p.8/p.78: `/batchsubscribe ,ssiii /x_meters_0 /meters/0 0 69 1` → 70 floats; `/batchsubscribe ,ssiii /yy /meters/6 0 0 40` → `/yy ,b 4 flts: 000.00 001.00 001.00 000.00`; `/batchsubscribe ,ssiii /rr /meters/5 3 1 40` → 27 floats. For plain `/meters` requests the reply address is `/meters/N`.
- Multiple different meter ids can be active at once; replies interleave ([DOC 4.09] p.79 "the X32/M32 can manage multiple subscriptions. Data from different subscriptions will be mixed"). Replies go to the requesting IP:port. (Emulator keeps one client per meter id — `TODO: not the right approach if several clients request meters` — an emulator limitation only.)

---

## 2. Reply message: wire layout

**SOURCE [DOC 4.09] p.16, verbatim:**
> The data returned by the X32/M32 server for /meters is an OSC-blob … The format of a returned blob is as follows:
> `<meter id> ,b~~<int1><int2><nativefloat>…<nativefloat>`
> `<meter id>`: see possible values below (padded with null bytes)
> `,b~~`: indicates a blob format, padded with null bytes
> `<int1>`: the length of the blob in bytes, 32 bits big-endian coded
> `<int2>`: the number of <nativefloats>, 32 bits little-endian coded
> `<nativefloat>`: data or meter value(s), 32 bits floats, little-endian coded

**SOURCE [X32.c] `Xprepmeter()` lines 4821–4836, verbatim code — the `// e.g. …`, `// big-endian` and `// little-endian (native x86 byte order)` comments below were ADDED by the colleague, they are not in the source (VERIFIED-CORRECTED: labelled as annotations):**
```c
ZMemory(&Xbuf_meters[i][0], 512);			// Prepare buffer (set to all 0's)
memcpy(&Xbuf_meters[i][0], buf, 16);		// e.g. "/meters/1\0\0\0,b\0\0"  or "/meters/10\0\0,b\0\0"
endian.ii = (l + 1) * 4;		// actual blob content length (in bytes)
Xbuf_meters[i][16] = endian.cc[3];			// big-endian
Xbuf_meters[i][17] = endian.cc[2];
Xbuf_meters[i][18] = endian.cc[1];
Xbuf_meters[i][19] = endian.cc[0];
Lbuf_meters[i] = endian.ii + 20;		// length of the whole message (in bytes)
endian.ii = l; // number of floats (32-bit)
Xbuf_meters[i][20] = endian.cc[0];			// little-endian (native x86 byte order)
Xbuf_meters[i][21] = endian.cc[1];
Xbuf_meters[i][22] = endian.cc[2];
Xbuf_meters[i][23] = endian.cc[3];
```

**SOURCE [Xdump.c] `Xdump()`, `case 'b'` — verbatim code; the `// big-endian read` / `// little-endian read` comments were added by the colleague (the original comments are "Get the number of bytes" and "Get the number of data (floats or ints ???) in little-endian format"). VERIFIED against the file by the verifier. Note Xdump first checks `if (n == endian.i1)` (blob size == LE count) to detect *string* blobs from `/formatsubscribe`; meter blobs never satisfy that because size = 4 + 4·count.**
```c
case 'b':
    // Get the number of bytes
    for (k = 4; k > 0; endian.c1[--k] = buf[data++]);          // big-endian read
    n = endian.i1;
    // Get the number of data (floats or ints ???) in little-endian format
    for (k = 0; k < 4; endian.c1[k++] = buf[data++]);          // little-endian read
    …
        } else {
            n = endian.i1;
            printf("%d flts: ", n);
            for (j = 0; j < n; j++) {
                //floats are little-endian format
                for (k = 0; k < 4; endian.c1[k++] = buf[data++]);
                printf("%06.2f ", endian.f1);
            }
        }
```
Community corroboration: [x32-osc.ts] `let meterBuf = args[0].data.slice(4); … meterData.push(meterBuf.readFloatLE(i));` (skip the 4-byte LE count inside the blob, then float32 LE). [Automix.c] reads channel `ch` directly as `ff = (float *)(r_buf + 24 + 4*ch);` (native LE float at datagram offset 24 + 4*index).

### 2.1 Byte offsets (datagram from the console)

| Offset | Size | Content | Endianness |
|---|---|---|---|
| 0 | 12 | OSC address `"/meters/N"` NUL-padded to 12 bytes (`"/meters/1\0\0\0"`, `"/meters/10\0\0"` — every N in 0…16 pads to exactly 12) | ASCII |
| 12 | 4 | typetag `",b\0\0"` | ASCII |
| 16 | 4 | **int1** = OSC blob size in bytes = `4 + 4*count` | **big-endian** |
| 20 | 4 | **int2** = `count` = number of 32-bit words that follow | **little-endian** |
| 24 | 4*count | payload words: float32 LE (meters 0–14), or 2×int16 LE per word (meters 15, 16) | **little-endian** |

Total datagram length = `24 + 4*count` bytes (emulator: `Lbuf = (l+1)*4 + 20`). VERIFIED: all 17 sizes recomputed by verifier; cross-check with the DOC captures: `/rr ,b` (alias 4 B) for `/meters/5` = 124 B and `/yy ,b` for `/meters/6` = 32 B, exactly `len(alias_padded) + 4 + 4 + 4 + 4*count`. Sizes: /meters/0 → 304 B; /1 → 408 B; /2 → 220 B; /3 → 112 B; /4 → 352 B; /5 → 132 B; /6 → 40 B; /7 → 88; /8 → 48; /9 → 152; /10 → 152; /11 → 44; /12 → 40; /13 → 216; /14 → 344; /15 → 224; /16 → 216. All well under a UDP MTU.

### 2.2 Worked example (`/meters/6`, channel 17, from the DOC)

**SOURCE [DOC 4.09] p.16, verbatim hex:**
```
2f6d65746572732f360000002c6200000000001404000000fd1d2137fdff7f3f0000803f6ebbd534
 / m e t e r s / 6 ~ ~ ~ , b ~ ~[ int1 ][ int2 ][nfloat][nfloat][nfloat][nfloat]
```
Decoded (verified with Python `struct`; re-run independently by the verifier — VERIFIED, all four values and the int1/int2 fields match):

| Bytes | Hex | Value |
|---|---|---|
| 0–11 | `2f 6d 65 74 65 72 73 2f 36 00 00 00` | `"/meters/6"` + 3 NUL |
| 12–15 | `2c 62 00 00` | `",b"` + 2 NUL |
| 16–19 | `00 00 00 14` | int1 = 20 (BE) = 4 + 4×4 ✓ |
| 20–23 | `04 00 00 00` | int2 = 4 (LE) |
| 24–27 | `fd 1d 21 37` | float LE 0x37211DFD = 9.60e-6 → 20·log10 = **−100.35 dBFS** (pre-fade level) |
| 28–31 | `fd ff 7f 3f` | 0x3F7FFFFD = 0.99999982 → **−0.0000016 dB** (gate gain: no reduction) |
| 32–35 | `00 00 80 3f` | 1.0 → **0.0 dB** (dyn gain: no reduction) |
| 36–39 | `6e bb d5 34` | 0x34D5BB6E = 3.98e-7 → **−128.00 dBFS** (post-fade level; muted/fader down) |

Note the post-fade value is *exactly* 10^(−128/20): the console's level meters bottom out at a **−128 dBFS floor**, not at 0.0 (matches the RTA floor of −128 dB, §4). The pre-fade value −100 dB is noise floor with no input.

### 2.3 Python decode (drop-in)

```python
import struct, math

def parse_meter_datagram(dgram: bytes):
    """Return (address, count, payload_bytes). Works for '/meters/N' and batchsubscribe aliases."""
    addr_end = dgram.index(b'\0')
    addr = dgram[:addr_end].decode()
    p = (addr_end + 4) & ~3                 # start of typetag
    assert dgram[p:p+2] == b',b'
    p = (p + 2 + 4) & ~3                    # ",b\0\0" -> start of blob size  (for '/meters/N' this is 16)
    (blob_len,) = struct.unpack_from('>i', dgram, p)      # big-endian OSC blob size
    (count,)    = struct.unpack_from('<i', dgram, p + 4)  # little-endian word count
    payload = dgram[p + 8 : p + 4 + blob_len]
    return addr, count, payload

def meter_floats(payload, count):          # /meters/0..14
    return list(struct.unpack_from('<%df' % count, payload, 0))

def meter_rta_db(payload, count):          # /meters/15 -> 100 dB values, -128.0 .. 0.0
    return [s / 256.0 for s in struct.unpack_from('<%dh' % (2*count), payload, 0)]

def meter_16(payload, count):              # /meters/16 -> (88 linear gains, 8 automix gains)
    s = struct.unpack_from('<%dh' % (2*count), payload, 0)   # 96 shorts
    lin  = [v / 32767.0 for v in s[:-8]]
    amix = [2.0 ** (v / 256.0) for v in s[-8:]]              # doc: short = log2(gain)*256
    return lin, amix

def lin_to_db(v, floor=-128.0):
    return max(floor, 20*math.log10(v)) if v > 0 else floor
```

---

## 3. Complete meter-ID table

VERIFIED: counts re-read from X32.c lines 4879–4927 and DOC PDF pp.18–20 by the verifier; all 17 match. Counts are triple-confirmed: [DOC 4.09] pp.17–19, the emulator's `Xprepmeter(id, count, …)` calls in [X32.c] `function_meters()` (0:70, 1:96, 2:49, 3:22, 4:82, 5:27, 6:4, 7:16, 8:6, 9:32, 10:32, 11:5, 12:4, 13:48, 14:80, 15:50, 16:48), and the fw-4.06 survey in [x32-physical #36] (0:70, 1:96, 2:49, 3:22, 4:82, 5:27, 6:4, 7:16, 13:48 measured). Contents/order are from the DOC (verbatim wording condensed); index ranges are 0-based positions in the payload.

| ID | count (32-bit words) | Encoding | Contents, in order (0-based index) | Extra request args |
|---|---|---|---|---|
| `/meters/0` | 70 | float LE | METERS page ("not used for X32-Edit"): [0–31] 32 input channels; [32–39] 8 aux returns; [40–47] 4×2 stereo FX returns (FX1L,FX1R,…,FX4R); [48–63] 16 bus masters; [64–69] 6 matrixes | none |
| `/meters/1` | 96 | float LE | METERS/channel page: [0–31] 32 input channels (level); [32–63] 32 gate gain reductions; [64–95] 32 dynamics gain reductions | none |
| `/meters/2` | 49 | float LE | METERS/mix-bus page: [0–15] 16 bus masters; [16–21] 6 matrixes; [22–23] main L, R; [24] mono M/C; [25–40] 16 bus dyn GR; [41–46] 6 matrix dyn GR; [47] main LR dyn GR; [48] M/C dyn GR | none |
| `/meters/3` | 22 | float LE | METERS/aux-fx page: [0–5] 6 aux sends (aux outs); [6–13] 8 aux returns (aux in); [14–21] 4×2 st FX returns | none |
| `/meters/4` | 82 | float LE | METERS/in-out page: [0–31] 32 input channels; [32–39] 8 aux returns; [40–55] 16 outputs (XLR out 1–16); [56–71] 16 P16/Ultranet outputs; [72–77] 6 aux sends (aux out); [78–79] 2 digital AES/EBU out; [80–81] 2 monitor outputs | none |
| `/meters/5` | 27 | float LE | Console-surface VU meters: [0–15] 16 channel-strip meters selected by `chn_meter_id` (0: ch 1–16; 1: ch 17–32; 2: aux/fx returns; 3: bus masters); [16–23] 8 group meters selected by `grp_meter_id` (0: DCA 1–8; 1: mix bus 1–8; 2: mix bus 9–16; 3: matrixes); [24–25] main L, R; [26] mono M/C | `,siii /meters/5 <chn_meter_id 0..3> <grp_meter_id 0..3> <tf>` |
| `/meters/6` | 4 | float LE | Channel-strip meters of one channel: [0] post gain/trim (pre-fade) level; [1] gate gain reduction; [2] dyn gain reduction; [3] post-fade level | `,sii /meters/6 <channel_id 0..71> <tf>` — channel_id: 0–31 Ch01–32, 32–39 Aux1–8, 40–47 FX1L…FX4R, 48–63 Bus01–16, 64–69 Mtx1–6, 70 Main LR, 71 M/C (same numbering as `/-stat/selidx`; DOC example "channel 17" = 16) |
| `/meters/7` | 16 | float LE | 16 bus-send meters (bus sends 1–16) | none |
| `/meters/8` | 6 | float LE | 6 matrix-send meters (1–6) | none |
| `/meters/9` | 32 | float LE | FX send/return meters: 4 per FX slot, slot-major ("2 effects send and 2 effects return meters for each FX slot … 4 x FX1, 4 x FX2, … 4 x FX8"). The within-slot order [4k] send L, [4k+1] send R, [4k+2] return L, [4k+3] return R is an **inference** (the DOC only says "2 sends and 2 returns" per slot) — UNCONFIRMED; verify with a tone on one FX send. (VERIFIED-CORRECTED: previously stated as fact.) | none |
| `/meters/10` | 32 | float LE | "Used for some Effects, for example Dual DeEsser, Stereo DeEsser, Stereo Fair Compressor" (effect-internal meters; layout effect-specific, UNCONFIRMED) | none |
| `/meters/11` | 5 | float LE | Monitor page: [0] Mon L, [1] Mon R, [2] Talk A/B level, [3] Threshold/GR, [4] Osc tone level | none |
| `/meters/12` | 4 | float LE | Recorder page: [0] RecInput L, [1] RecInput R, [2] Playback L, [3] Playback R | none |
| `/meters/13` | 48 | float LE | METERS page: [0–31] 32 input channels; [32–39] 8 aux returns; [40–47] 4×2 st FX returns | none |
| `/meters/14` | 80 | float LE | "Used for some Effects, for example Precision Limiter, Combinator, Stereo Fair Compressor" (layout effect-specific, UNCONFIRMED) | none |
| `/meters/15` | 50 | **100 × int16 LE** | RTA: 100 band levels in dB×256 (see §4). "Used for RTA and some Effects, for example Dual GEQ, Stereo GEQ" | none (no source argument — see §5) |
| `/meters/16` | 48 | **96 × int16 LE** | Dynamics + automix (fw ≥ 3.04): shorts [0–31] 32 channel gate gains; [32–63] 32 channel comp gains; [64–79] 16 bus comp gains; [80–85] 6 matrix comp gains; [86] LR comp gain; [87] M/C comp gain (all `/32767.0` → linear 0..1); shorts [88–95] automix gains ch 01–08 coded `log2(gain)*256` (see §4.3) | none |

Gain-reduction semantics for the float meters (1, 2, 6, and the GR entries): value is the **linear gain applied**, 1.0 = no reduction, e.g. 0.5 = −6.02 dB of gain reduction; `GR_dB = 20*log10(v)` (≤ 0). Evidence: DOC p.78 example `/meters/6 0 0 40` → `4 flts: 000.00 001.00 001.00 000.00` (gate=1.0, dyn=1.0 with no signal), the p.16 hex example (0.99999982 / 1.0), and [x32-physical #36] on fw 4.06: "/meters/1 … tail indices 32–95 pinned at ~1.000 with occasional dips — unity-with-dips is the signature of gain-reduction / gate-state values, not levels".

Third-party observation, not verified: [x32-physical #36] found `/meters/7` "Replies, all zeros — Likely P16/Ultranet (not XLR outputs)" on their rig; the DOC says it is the 16 **bus-send** meters. Their venue may simply have had no bus sends in use. Treat the DOC as authoritative; UNCONFIRMED either way.

Emulator note: the emulator returns all-zero payloads for every meter (`ZMemory`) — it validates only shape, never values.

---

## 4. Value encoding

### 4.1 Float meters (`/meters/0` … `/meters/14`)

**SOURCE [DOC 4.09] p.16, verbatim:**
> Meter values are returned as floats in the range [0.0, 1.0], representing the linear audio level (digital 0 – full-scale; internal headroom allows for values up to 8.0 (+18 dBfs)).

- Type: IEEE-754 float32, **little-endian** (see §2).
- Range: 0.0 … 1.0 nominal (0 dBFS = 1.0), can exceed 1.0 up to 8.0 (= +18.06 dBFS) for internal headroom.
- dB conversion: `dBFS = 20 * log10(v)`. Examples: 1.0 → 0 dB; 0.5 → −6.02; 0.1 → −20; 0.01 → −40; 0.001 → −60; 3.98e-7 → −128.0 (observed floor, §2.2); 8.0 → +18.06.
- Silence is **not** 0.0: the DOC example shows 9.6e-6 (−100 dB) on an idle input and 3.98e-7 (−128 dB) on a muted post-fade path. Clamp to a floor (−128 or your display minimum) and guard `log10(0)`.
- [Automix.c] uses the linear value directly: `if (*ff > X32sensitivity)` with default `X32sensitivity = 0.005` (= −46 dBFS).

### 4.2 `/meters/15` (RTA) — int16 pairs

**SOURCE [DOC 4.09] p.19, verbatim:**
> /meters/15 — Used for RTA and some Effects, for example Dual GEQ, Stereo GEQ — returns 50 32bits values as a single OSC blob.
> The 32bits values returned are representing 100 successive little endian coded short ints, in the range [0x8000, 0x0000]; each short int value provides a floating-point RTA dB level in the range [-128.0, 0.0], by dividing the short int (converted to float) by 256.0.
> For example, a 32bit value of 008000c0 will represent two values, the first one being 0x8000 (or -128.0 after conversion), and the second one being 0xc000 (or -64.0 after conversion). Similarly, a 32bits value of 40e0ffff will represent two successive RTA values of -31.75db and -0.004db, respectively.
> Note: a short int value of 0x0000 (or 0.0db) means signal clipping occurred.

**SOURCE [Xdump.c], verbatim:**
```c
if(strncmp(buf, "/meters/15", 10) == 0) {
    n = endian.i1 * 2;                       // count (50) * 2 = 100 shorts
    printf("%d rta: \n", n);
    for (j = 0; j < n; j++) {
        //data as short ints, little-endian format
        for (k = 0; k < 2; endian.c1[k++] = buf[data++]);
        endian.f1 = (float)endian.si[0] / 256.0;
        printf("[%d] %07.2f ", j, endian.f1);
    }
}
```
Also stated in [issue #8]: "/meters/15 (RTA output) is the one case where the X32 uses short little endian integers in a meter subscription."

- Blob: int2 = **50**; payload = 200 bytes = **100 × int16 little-endian**, band 0 first (lowest frequency). Band i occupies payload bytes `[2i, 2i+1]` (datagram offset `24 + 2i`). Word w holds bands 2w (low half) and 2w+1 (high half).
- Formula: `dB = int16 / 256.0` → resolution 1/256 dB = 0.0039 dB; range −128.0 (0x8000 = −32768, the floor / "no signal") … 0.0 (0x0000 = clipping). Positive values do not occur (range is [0x8000, 0x0000]).
- Worked examples (verified): bytes `00 80 00 c0` → shorts −32768, −16384 → −128.0 dB, −64.0 dB. Bytes `40 e0 ff ff` → shorts −8128, −1 → −31.75 dB, −0.0039 dB. Short −6144 (0xE800) → −24.0 dB.
- Linear (for drawing on the same scale as other meters): `lin = 10 ** (dB/20)`.
- MEASURED (2026-09-22/23, Verification log): the stream is the raw analysis. `gain` 0 vs 18, `autogain` ON, `peakhold` 2 — no change at all; the −40 dB oscillator reads −40.2/−39 in its band regardless. Only `det` (floor −97 RMS / −128 PEAK, levels otherwise equal) and `decay` (release ≈ 66 dB/s at 0.25) reach `/meters/15`. The server's pinning of gain/autogain/peak-hold at arm is harmless housekeeping for the engineer's screen, nothing more.

**Band centre frequencies — SOURCE [DOC 4.09] p.19, verbatim table (Hz), 100 entries, index 0 … 99 row-major:**
```
20 21 22 24 26 28 30 32 34 36
39 42 45 48 52 55 59 63 68 73
78 84 90 96 103 110 118 127 136 146
156 167 179 192 206 221 237 254 272 292
313 335 359 385 412 442 474 508 544 583
625 670 718 769 825 884 947 1.02K 1.09K 1.17K
1.25K 1.34K 1.44K 1.54K 1.65K 1.77K 1.89K 2.03K 2.18K 2.33K
2.50K 2.68K 2.87K 3.08K 3.30K 3.54K 3.79K 4.06K 4.35K 4.67K
5.00K 5.36K 5.74K 6.16K 6.60K 7.07K 7.58K 8.12K 8.71K 9.33K
10.00K 10.72K 11.49K 12.31K 13.20K 14.14K 15.16K 16.25K 17.41K 18.66K
```
Formula (inferred — reproduces every table entry to its printed rounding; VERIFIED by the verifier with a script over all 100 entries: the only non-exact case is band 40 = 312.5 Hz, which rounds half-up to the table's "313"): **1/10-octave spacing anchored at 10 kHz = band 90**:
`f(i) = 10000 * 2 ** ((i - 90) / 10)  Hz  ≈ 19.53 * 2 ** (i/10)`, i = 0…99.
Checks: i=0 → 19.5 (table "20"); i=5 → 27.6 ("28"); i=10 → 39.06 ("39"); i=20 → 78.1 ("78"); i=40 → 312.5 ("313"); i=45 → 441.9 ("442"); i=55 → 883.9 ("884"); i=63 → 1538.9 ("1.54K"); i=90 → 10000; i=91 → 10717.7 ("10.72K"); i=99 → 18660.7 ("18.66K"). It is *not* `20*2^(i/10)` (that gives 40 at i=10 and 20.48 kHz at i=100) and not ISO 1/3-octave. Use the verbatim table for labels and the formula for interpolation.

### 4.3 `/meters/16` (gate/comp gains + automix) — int16 pairs

**SOURCE [DOC 4.09] p.19, verbatim:**
> /meters/16 — Used for comp and automix — returns 48 32bits values as a single OSC blob.
> The first 44 values are 32bits values returned are representing: 32 channel gate gains, 32 channel comp gains, 16 bus comp gains, 6 matrix comp gains, 2 (L/R and Mono) comp gains
> All data sent as little endian coded short ints; each short int value represents a floating-point level in the range [0, 1.0], by dividing the short int (converted to float) by 32767.0.
> The 4 last floats represent 8 automix (channel 01...08) gains coded on successive shorts as Log2(value) * 256.

**SOURCE [Xdump.c], verbatim:**
```c
} else if(strncmp(buf, "/meters/16", 10) == 0) {
    n = endian.i1 * 2;                     // 48*2 = 96 shorts
    printf("M/16: %d shorts\n", n);
    for (j = 0; j < n - 8; j++) {          // first 88 shorts
        for (k = 0; k < 2; endian.c1[k++] = buf[data++]);
        endian.f1 = (float)endian.si[0] / 32767.0;
        if (j < 32) printf("[%d: G %07.2f] ", j, endian.f1);   // gate gains
        if (j < 64) printf("[%d: C %07.2f] ", j, endian.f1);   // comp gains
        if (j < 80) printf("[%d: B %07.2f] ", j, endian.f1);   // bus comp
        if (j < 86) printf("[%d: M %07.2f] ", j, endian.f1);   // matrix comp
        if (j == 86) printf("[%d:LR %07.2f] ", j, endian.f1);
        if (j == 87) printf("[%d:MC %07.2f] ", j, endian.f1);
    }
    for (; j < n; j++) {                   // last 8 shorts: automix
        for (k = 0; k < 2; endian.c1[k++] = buf[data++]);
        endian.f1 = (float)endian.si[0] / 256.0;
        printf("[%d: A %07.2f] ", j, endian.f1);
    }
}
```
(The cascading `if`s without `else` mislabel in the printout, but the intended ranges are clear and match the DOC.)

- Shorts 0–87: `gain = int16 / 32767.0` (0..1 linear; 32767 = unity = 0 dB GR); `GR_dB = 20*log10(gain)`.
- Shorts 88–95: automix gain for channels 1–8: `short = log2(gain)*256` ⇒ `gain = 2 ** (short/256)`; in dB: `dB = 6.0206 * short/256`. Examples: 0 → gain 1.0 (0 dB); −256 → 0.5 (−6.02 dB); −1024 → 0.0625 (−24.08 dB). Xdump prints `short/256` (= log2 units, i.e. "octaves" of gain), not dB.
- Availability: firmware **3.04+** ([Xdump.c] header comment "Updated Sep. 1 2016 to support /meters/16 type (X32 fw 3.04)"). VERIFIED: comment present. Corroboration (web-search snippets of the Behringer X32 firmware release notes, not fetched in full): fw 3.0 "adds automixing with Dugan-style gain sharing on two independent groups to the input channel 1-8 processing"; fw 3.04 (2016-08-24) "correcting automix gain reduction meter scaling" — consistent with the automix shorts appearing/being fixed in `/meters/16` at 3.04.

---

## 5. RTA source and options

### 5.1 `/-prefs/rta/*`

Parameter table — **SOURCE [PrefStat.h] lines 142–152 (emulator), verbatim (VERIFIED by verifier):**
```c
{"/-prefs/rta",						{PRTA}, F_FND, {0}, NULL},
{"/-prefs/rta/visibility",		{E32}, F_XET, {0}, Prtavis},
{"/-prefs/rta/gain",			{F32}, F_XET, {0}, NULL},
{"/-prefs/rta/autogain",		{E32}, F_XET, {0}, OffOn},
{"/-prefs/rta/source",			{I32}, F_XET, {0}, NULL},
{"/-prefs/rta/pos",				{E32}, F_XET, {0}, PRpos},
{"/-prefs/rta/mode",			{E32}, F_XET, {0}, PRmode},
{"/-prefs/rta/options",			{P32}, F_XET, {0}, NULL},
{"/-prefs/rta/det",				{E32}, F_XET, {0}, PRdet},
{"/-prefs/rta/decay",			{F32}, F_XET, {0}, NULL},
{"/-prefs/rta/peakhold",		{E32}, F_XET, {0}, Prtaph},
```
Enum string tables — **SOURCE [X32.c] lines 394–396 and 414–416, verbatim (VERIFIED; note the emulator's inconsistent leading spaces are as in the source):**
```c
char*   PRpos[] = {" PRE", " POST", ""};
char*  PRmode[] = {" BAR", " SPEC", ""};
char*   PRdet[] = {" RMS", " PEAK", ""};
char* Prtavis[] = {" OFF", " 25%", " 30%", " 35%", " 40%", "45%", "50%", " 55%", " 60%", "65%", "70%", "75%", "80%", ""};
char*  Prtaph[] = {" OFF", " 1", " 2", " 3", " 4", "5", "6", " 7", "8", ""};
```
Ranges/mappings — **SOURCE [DOC 4.09] pp.59–60, verbatim (condensed to the table):**

| Address | OSC type | Values / mapping | Units |
|---|---|---|---|
| `/-prefs/rta/visibility` | enum int 0…12 (E32) | `{OFF, 25%, 30%, 35%, 40%, 45%, 50%, 55%, 60%, 65%, 70%, 75%, 80%}` — RTA overlay opacity over EQ curves | % |
| `/-prefs/rta/gain` | float linf [0.0, 60.0, 6] | `dB = f * 60`, in 6 dB steps (11 values: 0,6,…,60). f=0.5 → 30 dB. Emulator: `XslashSetLinf(…, 0., 60., 6.)`, printed `Slinf(ff, 0., 60., 0)` | dB |
| `/-prefs/rta/autogain` | enum {OFF, ON} = int 0/1 | 0 disabled, 1 enabled | — |
| `/-prefs/rta/source` | int | **0: none; 1: Monitor; 2…33: Ch01…Ch32; 34…41: Aux1…Aux8; 42…49: FX1L…FX4R; 50…65: Bus01…Bus16; 66…71: Mtx1…Mtx6; 72: Main; 73: Mono** ("see also /-stat/rtasource") | index |
| `/-prefs/rta/pos` | enum {PRE, POST} = int 0/1 | 0: Pre EQ; 1: Post EQ ("RTA chain position") | — |
| `/-prefs/rta/mode` | enum {BAR, SPEC} = int 0/1 | 0: Bar[graph]; 1: Spec[trograph] | — |
| `/-prefs/rta/options` | %int bitmap (P32, 6 bits) | "bit 0 = Pre EQ; bit 1 = Spectrograph; bit 2 = Use RTA source; bit 3 = Post GEQ; bit 4 = Spectrograph; bit 5 = Solo Priority. e.g. <%int> = 0x0021: solo priority and Pre EQ are set". (bits 3/4 relate to the GEQ-effect RTA view; the DOC repeats "Spectrograph" for bit 4 — read as GEQ spectrograph, UNCONFIRMED.) Emulator prints it as `Sbitmp(v, 6)` = `%xxxxxx`. | bits |
| `/-prefs/rta/det` | enum {RMS, PEAK} = int 0/1 | detector | — |
| `/-prefs/rta/decay` | float logf [0.25, 16, 19] | 19 log steps: `value = 0.25 * 64 ** f` (emulator: `XslashSetLogf(…, 0.25, 4.158883083, 19)` where 4.158883083 = ln(16/0.25)). Appendix table (DOC p.159): 0.0000→0.25, 0.0556→0.31, 0.1111→0.40, 0.1667→0.50, 0.2222→0.63, 0.2778→0.79, 0.3333→1.00, 0.3889→1.26, 0.4444→1.59, 0.5000→2.00, 0.5556→2.52, 0.6111→3.17, 0.6667→4.00, 0.7222→5.04, 0.7778→6.35, 0.8333→8.00, 0.8889→10.08, 0.9444→12.70, 1.0000→16.00 | unit not stated (UNCONFIRMED; presumably seconds) |
| `/-prefs/rta/peakhold` | enum {OFF, 1…8} = int 0…8 | peak-hold setting. (The DOC line reads "int with value 0 or 1" — a copy-paste typo; the enum has 9 members and the emulator's `Prtaph[]` has 9 entries. VERIFIED.) | — |

`/node ,s -prefs/rta` reply string order — **SOURCE [X32.c] lines 4131–4141 (VERIFIED):** `/-prefs/rta <visibility> <gain dB int> <autogain OFF/ON> <source int> <PRE/POST> <BAR/SPEC> <%options 6-bit> <RMS/PEAK> <decay 2-dec> <peakhold>`, e.g. `/-prefs/rta 50% 24 ON 72 POST BAR %000100 RMS 1.00 OFF`. (Older X32DeskRestore source also lists `/-prefs/rta/option` (singular) — the current DOC/emulator use `options`; the DOC node list (PDF p.~99, text line "-prefs/rta/option") still shows `-prefs/rta/option`. VERIFIED by verifier: [DeskRestore.c] line 1099 `//s_len = Xsprint(s_buf, 0, 's', "/-prefs/rta/option");` — but that whole block is **commented out**, and the same commented block (line 874) uses `/-stat/rtaeqpost` where the DOC/emulator have `/-stat/rtageqpost`. Maillot's *live* desk-save code (`X32ds_node.h` line 29) requests the group node `"/-prefs/rta"` only, so his working tools never exercise either leaf spelling. Try `options` first; UNCONFIRMED which the console actually accepts as a leaf — the `/node ,s -prefs/rta` group form avoids the question and is what X32DeskSave uses. Emulator quirk, not console behaviour: the PRTA printer emits `"POST"` without the leading space (`command[i + 5].value.ii ? "POST" : " PRE"`, line 4135), so the emulator's `/node` reply reads `… 72POST BAR …`; do not pattern-match on that.)

### 5.2 `/-stat/rta*` and `/-action/setrtasrc`

**SOURCE [PrefStat.h] lines 307–311, 478:**
```c
{"/-stat/rtamodeeq",		{E32}, F_XET, {0}, PRmode},
{"/-stat/rtamodegeq",		{E32}, F_XET, {0}, PRmode},
{"/-stat/rtaeqpre",			{E32}, F_XET, {0}, OffOn},
{"/-stat/rtageqpost",		{E32}, F_XET, {0}, OffOn},
{"/-stat/rtasource",		{I32}, F_XET, {0}, NULL},
…
{"/-action/setrtasrc",		{I32}, F_XET, {0}, NULL},
```
**SOURCE [DOC 4.09] PDF p.63 (verbatim, condensed; VERIFIED by verifier — all value ranges, the `[sic]` typos and the reset warning are present as quoted):**

| Address | Values |
|---|---|
| `/-stat/rtamodeeq` | enum {BAR, SPEC} 0/1 — "RTA display mode for channel EQ" |
| `/-stat/rtamodegeq` | enum {BAR, SPEC} 0/1 — "RTA display mode for GEQ, Dual EQ, True EQ effect" |
| `/-stat/rtaeqpre` | enum {OFF, ON} 0/1 — "RTA chain position for channel EQ" |
| `/-stat/rtageqpost` | enum {OFF, ON} 0/1 — "RTA chain position for GEQ, Dual EQ, TrueEQ effect" (0: Pre, 1: Post) |
| `/-stat/rtasource` | int: **0…31 Channel 01…32 PRE-EQ; 32…39 Aux 01…08 PRE-EQ; 40…47 Fxrtn 1L…4R PRE-EQ; 48…63 Bus 01…16 PRE-EQ; 64…69 Matrix 01…06 PRE-EQ; 70 L/R PRE-EQ; 71 Mono PRE-EQ; 72 Monitor PRE-EQ; … 98…129 Channel 01…32 POST-EQ; 130…137 Aux POST-EQ; 138…145 Fxrtn POST-EQ; 146…161 Bus 01…16 POST-EQ; 162…167 Matrix POST-EQ; 168 L/R POST-EQ; 169 Mono POST-EQ; 170 Monitor POST-EQ.** "!! after Console Reset, the value of RTA source may not reflect the METERS/RTA screen settings (see also /-prefs/rta/source)". So `post = pre + 98`; values 73…97 undocumented (UNCONFIRMED). |
| `/-action/setrtasrc` | int "Selects the source used for RTA display: <int> represents the channel #: 0-31: Ch 1-32; 32-63: Ch 33-64 [sic]; 64-47: Aux in/USB [sic]; 48-63: Bus master; 64-69: Matrix 1-6; 70: L/R; 71: Mono/Center; 72: Monitor". The two [sic] lines are typos in the DOC; by the `/-stat/rtasource` convention they should read 32–39 Aux in, 40–47 FX returns. Whether values ≥ 98 (post-EQ) are accepted here is UNCONFIRMED. |

Related: `/-stat/screen/METER/page` enum: 0 Channel [CHANNEL], 1 Mixbus [MIXBUS], 2 Aux/fx [AUX/FX], 3 In/out [IN/OUT], **4 Rta [RTA]**, 5 Automix [AMIX] ([DOC 4.09] PDF p.65 — VERIFIED; the emulator's `Smetl[] = {" CHANNEL", " MIXBUS", " AUX/FX", " IN/OUT", " RTA", ""}` (X32.c line 432) has only 5 entries, i.e. it predates the fw-3.0 Automix page — emulator limitation). The DOC's MIDI/screen appendix likewise lists Meter "z": 0 Channel … 4 RTA only. `/-stat/selidx` uses the same 0–71 channel numbering as `/meters/6`'s `channel_id`.

### 5.3 What actually decides what `/meters/15` carries

- `/meters/15` takes **no source argument** (DOC lists none; emulator `case 15: Xprepmeter(15, 50, …, n, 0)` reads only a time factor). VERIFIED. The stream is simply *the console's RTA*, whatever it is currently analysing.
- **VERIFIED-CORRECTED (verifier, from [X32 manual] §7.3 item 5 "RTA", p.50 / Compact p.51):** the console's RTA settings are one screen with six encoders, and encoder 1 cycles through **six check boxes** that are exactly the six bits of `/-prefs/rta/options` (same order as the DOC's bit list):

  | bit | check box (manual wording) | applies to | manual text (verbatim) |
  |---|---|---|---|
  | 0 | "Pre EQ" | **channel PEQ overlay** | "The channel parametric EQ defaults to post-EQ … Select the 'Pre EQ' box to monitor the RTA independent of channel adjustments." |
  | 1 | "Spectrograph" | channel PEQ overlay | "Select the 'Spectrograph' box to view in spectrograph form instead of the default bargraph." |
  | 2 | "Use RTA Source" | **31-band GEQ (FX-slot) overlay only** | "Per default, the 31-band EQ will display the RTA information at the bus in which the EQ is inserted (for example, Main LR). Select the 'Use RTA Source' box to display the RTA source (see encoder 6)." |
  | 3 | "Post GEQ" | GEQ overlay | "To view the frequencies as they are adjusted by the EQ, select the 'Post GEQ' box." |
  | 4 | "Spectrograph" | GEQ overlay | (second Spectrograph box — so the DOC's duplicated "bit 4 = Spectrograph" is **not** a typo) |
  | 5 | "Solo Priority" | RTA source (RTA tab **and** GEQ view) | "When the 'Solo Priority' box is selected, the RTA source will be replaced with the monitor solo bus whenever a channel solo is active … This affects both the GEQ RTA view and the dedicated RTA tab view." |

  Remaining encoders (same manual page): encoder 2 = RTA-tab Bar/Spectrograph (`/-prefs/rta/mode`; "Note that this does not affect the channel EQ or 31-band EQ view. The spectrograph view displays frequencies over a 10 second window"); encoder 4 = Auto Gain / manual gain (`/-prefs/rta/autogain`, `/-prefs/rta/gain`; "recommended for most situations to ensure meaningful readings. However, when disabled, the gain can be manually adjusted … for comparing absolute frequency band levels between different channels"); encoder 5 = EQ overlay transparency (`/-prefs/rta/visibility`; "When set to 0%, the RTA will not be visible on the EQ screens") and *press* = "set the source as Pre or Post EQ" (`/-prefs/rta/pos`); encoder 6 = RTA source (`/-prefs/rta/source`; "This can be dynamic if you would like to monitor the currently selected channel or monitor source, or a fixed channel can be selected (for use with a dedicated measurement mic, for example)"). The manual (fw-2.x era) does not mention decay / peak hold / detector; those (`/-prefs/rta/decay`, `peakhold`, `det`) were added in later firmware (UNCONFIRMED which version).
- Consequences for OSC (this supersedes the colleague's earlier reading that bit 2 toggles "follow selected channel vs fixed source" — it does not; bit 2 only changes what the **GEQ overlay** shows):
  - The **dedicated RTA tab** (and therefore, by the most reasonable reading of "Used for RTA…", `/meters/15` while the RTA tab or nothing more specific is displayed) analyses **`/-prefs/rta/source`**: a fixed strip (2…73 per the DOC table), `1` = Monitor (dynamic, follows the monitor/solo source) or `0` = "none", which by elimination against the manual's three choices ("currently selected channel", "monitor source", "fixed channel") must be the **dynamic selected-channel** mode (UNCONFIRMED label; inference — Maillot's "none" = "no fixed source").
  - `/-prefs/rta/pos` (0 PRE / 1 POST) selects the EQ tap of that source. `/-stat/rtasource` reports the resolved source *including* the +98 post-EQ offset; `/-stat/rtaeqpre` / `/-stat/rtageqpost` mirror the per-view Pre-EQ (bit 0) and Post-GEQ (bit 3) states; `/-stat/rtamodeeq` / `rtamodegeq` mirror bits 1 and 4.
  - **Solo Priority** (bit 5) overrides everything while any solo is active.
  - The channel **PEQ overlay** always shows the *selected* channel (bit 0 pre/post) — it has no source option at all; the **GEQ overlay** shows the GEQ's own insert bus unless bit 2 is set.
  - `/-action/setrtasrc ,i N` is the imperative "set the RTA source now" action (0–72 numbering, same as `/-stat/rtasource` pre-EQ half). Whether it also rewrites `/-prefs/rta/source` (which uses the +2 numbering) is UNCONFIRMED; read `/-stat/rtasource` back to confirm.
  - Which of the three analyses `/meters/15` carries when a PEQ or GEQ page is displayed on the console (or on a remote client) remains UNCONFIRMED (the DOC says the stream is "Used for RTA and some Effects, for example Dual GEQ, Stereo GEQ"). Safe practice: put the console on the METERS/RTA tab (`/-stat/screen/screen ,i 1` = Meter, then `/-stat/screen/METER/page ,i 4`, addresses per the DOC screen section) or at least do not leave an FX GEQ page showing.
- **Recipe — RTA on Bus N, post-EQ:**
  1. `/-prefs/rta/source ,i (49+N)` (Bus01 = 50 … Bus16 = 65)
  2. `/-prefs/rta/pos ,i 1` (POST)
  3. (optional) `/-prefs/rta/options`: leave bits 0–4 as they are — they only affect the EQ/GEQ overlay views; **clear bit 5** (Solo Priority) unless you want solos to hijack the analyser. (VERIFIED-CORRECTED: the earlier recipe set bit 2 "use RTA source" here; that bit only matters for the GEQ overlay.)
  4. subscribe `/meters ,si /meters/15 <tf>` and resend every ≤ 5 s.
  Alternative: `/-action/setrtasrc ,i (47+N)` (Bus01 = 48) then step 4. Verify with `/-stat/rtasource` (expect `146+N-1` for Bus N post-EQ, `48+N-1` pre-EQ).
- **Recipe — RTA on Main LR:** `/-prefs/rta/source ,i 72` (Main) [+ pos as above]; or `/-action/setrtasrc ,i 70`; expect `/-stat/rtasource` = 70 (pre) / 168 (post). Mono/centre: prefs 73 / action 71.
- The "GEQ" RTA (`/-stat/rtamodegeq`, `/-stat/rtageqpost`, options bits 2/3/4) is the RTA overlay drawn on an FX-slot GEQ/TEQ page; by default it analyses the bus the GEQ is inserted on. UNCONFIRMED whether it replaces the RTA-tab analysis in `/meters/15` only while the FX page is displayed.

---

## 6. Gotchas

1. **Two length fields, two endiannesses.** The OSC blob size (offset 16) is big-endian per OSC; the console's own count (offset 20, first 4 bytes *inside* the blob) and all payload words are little-endian. Any OSC library will hand you the blob as bytes — you must then skip 4 bytes and decode LE yourself ([x32-osc.ts] `data.slice(4)` + `readFloatLE`; [Xdump.c] comments "Get the number of data … in little-endian format", "floats are little-endian format"). Parsers that assume all numeric data is BE produce garbage.
2. **`/meters/15` and `/meters/16` are int16, not float.** Decoding them as float32 yields nonsense (xair-osc: "Float interpretation produces nonsense values like -1e29"). `/meters/15` = dB×256 (floor −128 dB, 0 = clip); `/meters/16` = mixed (×1/32767 linear for 88 shorts, log2×256 for the last 8).
3. **Silence is not zero** for the float meters: expect ~1e-5 … 4e-7 (−100 … −128 dBFS). Clamp and never `log10(0)`. RTA silence = 0x8000 = −128.0 dB exactly.
4. **Values above 1.0** are legal (headroom to 8.0 = +18 dBFS). Don't assert ≤ 1.0.
5. **Gain-reduction entries are gains, not reductions**: 1.0 = no GR. Display `−20*log10(v)` dB of reduction.
6. **10-second lease, no keep-alive from the console.** Re-send the identical `/meters` request (or `/renew`) every ~5 s; `/renew` after expiry is ignored. Each meter id is a separate lease; `/renew` with no argument renews all of them and all `/subscribe`-family subscriptions at once.
7. **Frame rate is nominal.** 50 ms × tf, "may be variable according to console's ability to fulfill requests". Don't derive timing from packet arrival; timestamp locally.
8. **Address padding is 12 bytes for every id** (`/meters/9\0\0\0` and `/meters/10\0\0` are both 12) so the blob-size field is always at datagram offset 16 for plain requests — but with `/batchsubscribe` the address is your alias, so compute offsets from the address length (the Python in §2.3 does).
9. **Reply address vs request:** a plain `/meters ,s /meters/1` reply is addressed `/meters/1`; a `/batchsubscribe` reply is addressed by its alias. `/xremote` is unrelated — it does not deliver meters.
10. **Extra ints are harmless**: sending `,siii /meters/N 0 0 tf` for ids that take no arguments is accepted on real hardware ([Automix.c], [x32-osc.ts]); the ints for `/meters/5` (chn_meter_id, grp_meter_id) and `/meters/6` (channel_id) are positional, with tf last.
11. **`/meters/5` and `/meters/6` are parameterised per subscription** — one subscription = one channel/bank. To meter all 72 strips use `/meters/0`/`/1`/`/2`/`/13` instead of 72 × `/meters/6`.
12. **Multiple clients** each get their own streams (sent to the requesting IP:port); the emulator can only track one client per meter id (its `TODO` comment) — don't debug multi-client behaviour against the emulator. The emulator also sends all-zero payloads.
13. **Firmware history:** the 100-band RTA (bar + spectrograph) arrived with **fw 2.0** (2014; behringerwiki "V2.0 Firmware: RTA and EQ Functions" and Behringer product copy: "high-resolution 100-band Real Time Analyzer (RTA), with full Bar and Spectrograph views … capturing a sonogram window of a full 10 seconds" — VERIFIED-CORRECTED: the earlier quote "100 bands from 20 Hz to 20 kHz" could not be located in any source and was replaced); `/meters/16` arrived with **fw 3.04** (Sept 2016, per [Xdump.c]); the current DOC (4.09-02, Nov 2025) "applies to console FW ver 4.0 and later" and its meter chapter is unchanged since at least the Jan-2020 4.02 edition; [x32-physical #36] re-measured the counts on fw **4.06** (2026). No 2.x-vs-4.x differences in the meter *formats* are documented; 2.x lacks `/meters/16` (and `/meters/15` before 2.0). Automix (the last 8 shorts of `/meters/16`) needs fw ≥ 3.0.
14. **X-Air is different**: on XR12/16/18 *every* `/meters/N` payload is int16 LE dB×256 (not float) and the port is 10024 ([issue #8], [xair-osc]). VERIFIED and extended from [XAir_Command.c] lines 139–222 (its own `Xdump()`: "specific to XR series: xdump and xfdump are copied/included here as meters are different (more consistent) than on X32"): for `strncmp(buf, "/meters/", 8) == 0` it loops `for (j = 0; j < n; j++)` over **n = the little-endian count** reading 2 bytes each — i.e. on X-Air the LE count is the number of **shorts**, whereas on X32 it is the number of 32-bit words (and `Xdump.c` doubles it only for ids 15/16). Don't reuse an X-Air decoder for X32 float meters or vice versa.
15. **`/-stat/rtasource` may be stale after a console reset** ([DOC 4.09] p.63 warning) — set the source explicitly rather than trusting the reported value at startup.
16. **Doc typos to ignore:** `/-action/setrtasrc` lines "32-63: Ch 33-64" and "64-47: Aux in/USB" (should be 32–39 aux-in, 40–47 FX rtn); `/-prefs/rta/peakhold` "int with value 0 or 1" (it is 0…8). **Not** a typo: `/-prefs/rta/options` listing "Spectrograph" for both bit 1 and bit 4 — they are the channel-EQ and GEQ spectrograph boxes respectively ([X32 manual] p.50; VERIFIED-CORRECTED).
17. **`options` bit 2 "Use RTA Source" is a GEQ-overlay option, not the RTA-tab source switch** ([X32 manual] p.50). Setting it does nothing for `/meters/15` unless a GEQ page is being viewed; the analyser source for the RTA tab is `/-prefs/rta/source` (0 = dynamic/selected, 1 = Monitor, 2–73 fixed).

---

## 7. Quick reference (copy into the implementation)

```
Request : "/meters" ,si  "/meters/N" <tf>                 (N = 0..16, tf 1..99 → 50 ms × tf; resend every 5 s)
          "/meters" ,sii "/meters/6" <channel_id 0..71> <tf>
          "/meters" ,siii "/meters/5" <chn_meter_id 0..3> <grp_meter_id 0..3> <tf>
Renew   : "/renew"  (all)   or  "/renew" ,s "/meters/N"    (must arrive < 10 s after the last request)
Reply   : "/meters/N\0.." (12 B) ",b\0\0" (4 B) size:int32BE (=4+4*count) count:int32LE  words[count] (LE)
Words   : N=0..14 → float32 linear, 0..1 (+headroom to 8.0), dBFS = 20*log10(v), floor ≈ -128 dB
          N=15    → 2×int16 per word, 100 bands, dB = s/256, [-128, 0], 0 = clip; f(i)=10000*2^((i-90)/10) Hz
          N=16    → 2×int16 per word: 88 × (s/32767 = linear gain) + 8 × (2^(s/256) = automix gain ch1-8)
Counts  : 0:70 1:96 2:49 3:22 4:82 5:27 6:4 7:16 8:6 9:32 10:32 11:5 12:4 13:48 14:80 15:50 16:48
RTA src : /-prefs/rta/source ,i {0 none,1 Monitor,2-33 Ch,34-41 Aux,42-49 FX,50-65 Bus,66-71 Mtx,72 Main,73 Mono}
          /-prefs/rta/pos ,i {0 PRE,1 POST}   /-prefs/rta/options ,i bits{0 PEQ-preEQ,1 PEQ-spec,2 GEQ-useRTAsource,3 GEQ-post,4 GEQ-spec,5 soloPrio}
          (bits 0-4 only change the EQ/GEQ overlay views; only source/pos/bit5 matter for the RTA tab & /meters/15)
          /-action/setrtasrc ,i {0-31 Ch,32-39 Aux,40-47 FX,48-63 Bus,64-69 Mtx,70 LR,71 M/C,72 Mon}
          /-stat/rtasource → same numbering, +98 when POST-EQ
```

---

## Verification log

Verifier: independent re-derivation on 2026-09-19 from freshly downloaded copies of `X32.c`, `X32PrefStat.h`, `X32Automix.c`, `X32lib/Xdump.c`, `X32_Command.c`, `XAir_Command.c`, `X32DeskRestore.c`, `X32DeskSave.c`, `X32ds_node.h` (raw.githubusercontent.com, master), the OSC protocol PDF 4.09-02 (Google-Drive id `1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz`, 180 pages, text via pypdf 5.9), the Behringer X32 / X32 Compact user manuals (pypdf), and re-fetches of x32-osc `index.ts`, x32-physical issue #36, pmaillot issue #8 and the xair-osc README. All numeric checks (hex example, RTA short examples, band-centre formula over 100 entries, decay table over 19 steps, gain steps, datagram sizes for all 17 ids, request/reply offsets for `,s`/`,si`/`,sii`/`,siii`) were recomputed with Python.

Corrections made (marked VERIFIED-CORRECTED in place):

1. **§5.3 / §6 / §7 — `options` bit 2 semantics.** The colleague inferred that bit 2 "Use RTA source" toggles between "RTA follows the selected channel" and "RTA uses the fixed `/-prefs/rta/source`", and the Bus-N recipe set that bit. The X32 user manual (§7.3 item 5, p.50 / Compact p.51) shows the six option bits are the six check boxes of the RTA settings screen, and "Use RTA Source" belongs to the **31-band GEQ overlay** ("Per default, the 31-band EQ will display the RTA information at the bus in which the EQ is inserted … Select the 'Use RTA Source' box to display the RTA source"). The dedicated RTA tab always uses `/-prefs/rta/source` (encoder 6: dynamic selected-channel / monitor, or a fixed strip). §5.3 rewritten, recipe step 3 corrected, gotcha 17 added, quick-reference line corrected.
2. **§5.1 / §6.16 — duplicated "Spectrograph" bits.** Listed as a DOC typo; it is not: bit 1 is the channel-EQ spectrograph box, bit 4 the GEQ spectrograph box (same manual page).
3. **§3 `/meters/9` within-slot order** (send L, send R, return L, return R) was presented as fact; the DOC only says "2 effects send and 2 effects return meters for each FX slot". Re-labelled as inference / UNCONFIRMED.
4. **§6.13 — unverifiable quote.** The "Behringer 2.0 release notes: '100 bands from 20 Hz to 20 kHz'" quote does not appear in any fetched source; replaced with the verifiable behringerwiki / product-copy wording.
5. **§2 — "verbatim" C snippets** for `Xprepmeter()` and `Xdump()` contained comments added by the colleague (`// big-endian`, `// little-endian (native x86 byte order)`, `// e.g. "/meters/1…"`, `// big-endian read`, `// little-endian read`). Code is byte-for-byte correct; the snippets are now labelled as annotated.
6. **Sources table — page numbering.** Citations mixed printed page numbers and PDF page indices (offset 1). Added an explicit convention note rather than rewriting every citation.
7. **§5.1 — `/-prefs/rta/peakhold`** DOC typo ("int with value 0 or 1") noted; enum is 0…8.

Confirmed without change (VERIFIED tags added in place): request forms and typetags; tf clamp and 50 ms × tf (plus real-hardware `tf = delay_ms/50` in Automix.c); 10 s lease and 9 s / 5 s renewal practice; `/renew` semantics; reply layout (12-byte address, `,b` + 2 NULs, BE blob size = 4+4·count, LE count, LE payload), all 17 counts and datagram sizes; the `/meters/6` hex example (−100.35 dB, ≈0 dB, 0 dB, −128.00 dB); `/meters/15` = 100 × int16 LE, dB = s/256, [−128, 0], 0 = clip, band table and the fitted 1/10-octave formula anchored at 10 kHz = band 90; `/meters/16` = 88 × s/32767 + 8 automix shorts as log2(gain)·256, fw 3.04+; every `/-prefs/rta/*`, `/-stat/rta*`, `/-action/setrtasrc` address, type, range and enum table; `Prtavis` (13 entries) and `Prtaph` (9 entries); decay `0.25·64^f` against the 19-row appendix; gain `linf 0…60 step 6`.

Material facts added: Automix.c derives tf from milliseconds (`X32i_delay / 50`) and sends a 40-byte `,siii` request every 9 s; x32-osc default tf = 10 (500 ms), clamped 1…99; X-Air `Xdump` treats the LE count as a number of **shorts** (XAir_Command.c), unlike X32; `X32DeskRestore.c`'s commented-out leaf list uses `/-prefs/rta/option` **and** `/-stat/rtaeqpost` (vs DOC/emulator `options` / `rtageqpost`), while Maillot's live desk-save node list (`X32ds_node.h`) only requests the `/-prefs/rta` group node; the emulator's `Smetl[]` lacks the fw-3.0 Automix meter page (5) that the DOC lists; the emulator prints `POST` without a leading space in `/node ,s -prefs/rta` replies; the emulator ignores tf for `,siii` requests to ids other than 5/6 (reads `"1"` + NULs → clamp to 1); the DOC's own tf = 80 capture shows ≈3 s spacing rather than 4 s; the six RTA check boxes and encoder assignments from the X32 manual; fw 3.0 / 3.04 release-note snippets corroborating the automix meters.

Still UNCONFIRMED after verification (see inline markers): whether `/meters/15` values include `/-prefs/rta/gain`/autogain; the exact stream content while a PEQ/GEQ page is displayed; whether `/-action/setrtasrc` accepts ≥ 98 or updates `/-prefs/rta/source`; the meaning of `/-stat/rtasource` 73…97 (25 undocumented values between the pre-EQ block ending at 72 and the post-EQ block starting at 98); the unit of `/-prefs/rta/decay` (DOC calls it "adjustable decay time", so a time unit — presumably seconds — but no source states "s"); whether the console accepts the slash-less `meters/1` spelling; which leaf spelling (`options`/`option`) the console accepts; `/meters/10` and `/meters/14` layouts; the `/meters/7` all-zeros report; the `/meters/9` within-slot order; the label of `/-prefs/rta/source` = 0 ("none" ≈ dynamic selected channel, by elimination); which firmware added decay/peakhold/det. behringer.world (403 even with a browser UA) and behringerwiki (DNS) remain unreachable.

### 2026-09-22 — measured on X32RACK-Jim (FW 4.13), studio, console oscillator, 20 minutes

Method: console oscillator (Monitor → Oscillator tab) sine 2 kHz at −40 dB; `get_rta("main.st")` (server forces autogain OFF,
gain 0, decay 0.25, peak-hold OFF, POST); Main LR EQ band 4 set by hand; readings are 20-frame averages of `/meters/15`.

1. **Oscillator into a mix bus is invisible to that bus's meter and RTA tap.** With Destination = MixBus 08 every bus meter read
   −90 and the bus-8 RTA sat at the −97 floor; the Oscillator tab says "replaces destination signal" and it does so at the bus
   OUTPUT, downstream of the tap. Into Main L+R the tone enters *upstream* of the main EQ (the EQ cut it, audibly and at the tap).
   Consequence: the oscillator self-tests in DETECTOR.md §6 / PEQ_ACTUATOR_DESIGN.md §10 cannot use a bus as the source; use
   Main (quietly) or feed a tone into a channel.
2. **At `/-prefs/rta/gain` 0 the stream reads true dBFS**: a −40 dB oscillator on Main read −40.2 in its band (post-EQ tap, main
   fader irrelevant — the tap is pre-fader). The desk's pref had been sitting at **+18 dB** (autogain OFF) since before M7, so
   every absolute level in the M7 log (`min_level_db −45`, the 25 dB override datum) was taken 18 dB above true dBFS *if* the
   pref offsets the stream — the +18 comparison itself was not run (Edit has no RTA-gain control; it is on the Rack's Meters →
   RTA page). Still UNCONFIRMED whether the pref offsets `/meters/15`; what is confirmed is that gain 0 = dBFS.
3. **Band skirts are far steeper than the corpus default.** Tone at −40.2 dB: neighbouring bands −79.2 / −82.3 (≈ −40 dB at
   ±0.1 oct), ±2 bands at −90 and the −97 floor. That is the `skirt_order` 5 end of the CORPUS.md §5 sweep (60 dB prominence
   ceiling), not the N = 3 default (36 dB). The M7 "60 dB-prominent line" was real.
4. **The band centres appear offset by roughly half a band from `10000·2^((i−90)/10)`.** A ≈1993 Hz tone (fitted from the three
   notch readings below) read −40.2 in band 66 (nominal 1894.7 Hz, 0.073 oct below the tone) and −45.5 in band 67 (nominal
   2030.6 Hz, 0.027 oct above it) — the *farther* nominal centre was 5 dB louder. Either the real centres sit ~0.05 oct above the
   nominal formula or the bands are asymmetric. This moves the detector's interpolated `freq_hz` (and therefore a PEQ notch) by up
   to half a band; a 3-minute sweep (tone at 1900/1950/2000/2050/2100 Hz, read bands 65–68) settles it. UNCONFIRMED until then.
5. **The Main LR PEQ is the RBJ prototype, Q defined at the half-gain points.** Band 4 PEQ at 2.04 kHz on the tone:
   −12 dB Q 6.1 → both tone bands dropped **10.9 dB**; −6 dB Q 6.1 → **5.5 dB**; −12 dB Q 10 → **9.6 dB**. All three fit one RBJ
   bell with the tone 0.034 oct below the centre (RBJ predicts 11.1 / 5.6 / 9.9 at 0.03 oct; a −3 dB-bandwidth Q would have
   given ~7.4 for the Q 10 case). `peq_q_scale_min/max` can be 1.0; the post-EQ RTA tap sees the main EQ.
6. Neighbour bands moved with the notch as the bell predicts (−79 → −93 at +0.12 oct for −12 dB), and the RTA's own floor is
   −97 (display), not −128.

### 2026-09-23 — measured on X32RACK-Jim (FW 4.13), studio, console oscillator into Main L+R, ~35 minutes

Method as on 2026-09-22 plus `scripts/log_rta_frames.py` (every `/meters/15` frame to JSONL with local receive time;
20.0 frames/s, inter-frame jitter 48–52 ms) and `scripts/analyse_rta_rise.py`. Raw frames:
`docs/research/data/rta_rise_gated_tones_2026-09-23.jsonl.gz`, `rta_peakhold_det_2026-09-23.jsonl.gz`.

1. **`/-prefs/rta/gain` does NOT reach `/meters/15`**: 2 kHz tone at −40, bands 66/67 read −39.0/−44.7 at gain 0 and at gain
   18 (pref read back 18). Neither does **`autogain`** (ON for 30 s: identical) nor **`peakhold`** (set to 2, tone gated off: the
   band fell at the normal −3.5 dB/frame, no hold). Of the RTA prefs only `det` and `decay` change the stream (below). The
   server's arm-time forcing of gain/autogain/peak-hold is therefore harmless but not load-bearing; `PEAK_HOLD_SUSPECTED`
   cannot trigger from the stream.
2. **`det` sets the floor and shows the true skirts**: levels identical within 0.7 dB (RMS −39.0/−44.7 → PEAK −39.1/−45.4:
   no crest-factor offset), but the floor is **−97 under RMS and −128 under PEAK**, so the prominence ceiling of a −40 dB
   tone is 57 dB (RMS) or 60+ dB (PEAK). Under PEAK the skirts of a 2 kHz tone: ±1 band −45/(see 4), ±2 −82/−87,
   ±3 −108/−97, ±4 −122/−113; the desk's own noise in the tone's band with the tone off ≈ −105 dBFS.
3. **Release law, scripted sweep (`scripts/measure_rta_release.py`, data `rta_release_decay_sweep_2026-09-23.jsonl.gz`):**
   linear in dB at **≈ 20 / decay_s dB/s** — decay 0.25 → 4.0 dB/frame (80 dB/s, floor in 0.65 s); 1 → 0.98 dB/frame
   (20 dB/s, 2.75 s); 4 → 0.25 dB/frame (5 dB/s); 16 → 0.06 dB/frame (1.2 dB/s). The corpus's law (60 / decay) is exactly
   3× too fast. RMS and PEAK alike. At 0.25 VERIFY sees 6 dB in 2 frames; a killed ring's display still lingers ~0.65 s.
   **`decay` also slows the ATTACK**: the same 2 kHz tone reaches its plateau in 1 frame at 0.25, in 3–4 frames at 1
   (−52 → −47 → −45 → −43) and had not reached full level after 4 s at 16 — it is an averaging time constant, not a
   release-only setting. Forcing 0.25 at arm is therefore the one RTA-pref write that changes what the detector sees;
   at Jim's previous setting of 1 even HF lines "grew" for three frames.
4. **Rise time of a gated tone, frame by frame** (plateau −37.7; increments in dB/frame):
   | tone | frames to −3 dB / −1 dB of plateau | increments | settle rule ⌈1.5k/(Δf·T)−½⌉, k = 1 |
   |---|---|---|---|
   | 78 Hz | 4 / 5 | 13.1, 5.2, 6.4, 1.9, 1.5 (and 16.6, 7.0, 3.0, 3.1, 0.8) | 6 |
   | 156 Hz | 2–3 / 3 | 18.1, 9.0, 2.5, 0.6 (and 9.8, 6.2, 1.2) | 3 |
   | 947 Hz | 1 / 2 | 4.1, 0.9, 0.4 | 1 |
   | 1.77 kHz, 7.6 kHz | 1 / 2 | 8.7, 2.1, 0.5 / 3.7, 2.1, 0.3 | 1 |
   A steady 78 Hz tone renders as a 5-frame decelerating ramp at 100–260 dB/s — the M7 40/80 Hz "growth" mechanism, measured.
   k = 1.0 is right (the simulator's own τ_a = 0.5/Δf would predict 3 frames at 78 Hz). 40 Hz did not appear in the log
   (the oscillator's lowest step that evening reached the tap was 78 Hz); 400 Hz was skipped.
5. **Band centres: five-point semitone sweep** (oscillator steps 1k78 / 1k88 / 2k00 / 2k11 / 2k24; readings dB):
   | tone | band 64 | 65 | 66 | 67 | 68 | 69 | 70 |
   |---|---|---|---|---|---|---|---|
   | 1.78 kHz | −61.2 | **−37.7** | −74.6 | — | — | — | — |
   | 1.88 kHz | — | −51.1 | **−37.7** | −78.7 | — | — | — |
   | 2.00 kHz | — | −86.6 | **−39.0** | −44.7 | −82.0 | — | — |
   | 2.11 kHz | — | — | −82.5 | **−37.7** | −55.2 | −84.4 | — |
   | 2.24 kHz | — | — | — | −77.9 | **−37.6** | −62.7 | −91.1 |
   | 8.00 kHz | 85: −84.6 | 86: **−37.6** | 87: −49.4 | 88: −83.3 | | | |
   Nominal centres (`10000·2^((i−90)/10)`): 64 1649, 65 1768, 66 1895, 67 2031, 68 2176, 69 2333, 86 7579, 87 8123. A tone
   0.03–0.06 oct *above* a nominal centre reads full in that band; the next band up (0.04–0.07 oct away) reads −13 to −25;
   the band below (0.13–0.16 oct) reads −37 to −45. Best single-parameter fit: **real centres ≈ nominal × 2^0.05 (+3.5 %)**,
   i.e. the formula is half a band low — 2.00 kHz sits between "66" (≈1962) and "67" (≈2103), hence −39/−44.7. Provisional:
   a one-sided skirt would look similar; a finer sweep (the oscillator only steps in semitones) or a pink-noise
   measurement settles it. The detector's centroid interpolation and every "band → Hz" label inherit the offset.
6. **Main LR PEQ at 8 kHz**: band 5 PEQ 7.87 kHz Q 6.1 −12 on an 8.00 kHz tone (0.024 oct above the centre): **11.7 dB**
   (RBJ prototype 11.4). No bilinear warping visible at this offset; with the 2 kHz readings the PEQ = RBJ, `q_scale` 1.0.
7. **The Dual Graphic EQ (GEQ2, FX 5 side A, Main LR insert PRE, RTA post-EQ) realises about a third of its slider depth
   at an isolated band**: 2k slider −6 → **2.6 dB** at 2.00 kHz; −12 → **4.1 dB** at 2.00 kHz, 3.8 at 1.88, 3.6 at 2.11 — a
   broad, shallow dip (±0.09 oct within 0.5 dB of the centre). Nominal depths are what NotchController writes and what the
   corpus's closed loop applies; the desk delivers ~0.35× at −12 and ~0.43× at −6. M7's −3/−6/−9 on the 5 kHz howl were
   therefore ~1.3/2.6/3.9 dB of real attenuation, which is why it took "−9" to die. Dual TruEQ (the band-interaction-
   corrected type) was not measured. The insert-on-Main + oscillator combination read total silence once, but the tone was
   off at the time: no conclusion about the injection point relative to the insert.
