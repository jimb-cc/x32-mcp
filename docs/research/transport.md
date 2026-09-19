# X32 OSC transport and core messages — research notes

Scope: wire format, UDP behaviour, `/info` `/xinfo` `/status`, `/xremote`, get/set semantics, `/node` and `/`, the subscribe family (`/subscribe`, `/formatsubscribe`, `/batchsubscribe`, `/renew`, `/unsubscribe`), `/meters` framing, quirks, rate/size limits, and the X32 emulator.

Notation used below (same as Maillot's documents): `~` = one NUL byte (`\0`). `[1.0000]` = a big-endian float32 argument, `[ 3]` = a big-endian int32 argument.

## Sources (priority order) and how they were read

| Tag | Source | Notes |
|---|---|---|
| **EMU** | `X32.c` v0.88 (X32 emulator), raw from `https://raw.githubusercontent.com/pmaillot/X32-Behringer/master/X32.c` (5661 lines) plus its tables `X32Channel.h`, `X32Bus.h`, `X32CfgMain.h`, `X32Headamp.h`, `X32Fx.h`, `X32PrefStat.h`, `X32Show.h`, `X32Misc.h`, `X32Libs.h`, `X32Dca.h` | C read in full for every function cited; line numbers below refer to the master-branch file as fetched 2026-09-19 |
| **LIB** | `X32lib/Xsprint.c`, `X32lib/Xdump.c`, `X32lib/Xcparse.c`, `X32lib/X32Connect.c`, `X32lib/X32logf.c` | the encoder/decoder every Maillot tool uses |
| **CMD** | `X32_Command.c` v1.46, `X32GetScene.c`, `X32SetScene.c`, `X32Automix.c`, `X32ReaperW.c`, `X32Fade.c`, `X32Replay.c`, `X32SsaverGW.c`, `X32Midi2OSC.c` | client-side behaviour against a *real* desk |
| **README** | repo `README.md` (67 KB) | contains real-desk and emulator dialog captures |
| **DOC** | *Unofficial X32/M32 OSC Remote Protocol*, Patrick-Gilles Maillot, PDF mirror `https://tostibroeders.nl/wp-content/uploads/2020/02/X32-OSC.pdf` (174 pages, text-extracted with `pdftotext -layout`; line numbers below are lines of that text dump). The canonical current copy (updated 2021-06-29) is linked from `https://sites.google.com/site/patrickmaillot/x32` → `https://drive.google.com/file/d/1Yt_S1mpPt3CAzeq3Dnpe_IqctQ-1GlTz/view` |
| **SITE** | `https://sites.google.com/site/patrickmaillot/x32` (fetched; rendered text) | download links for the doc, emulator binaries, X32_Command |

Everything marked **CONFIRMED** is backed by verbatim C or by a captured dialog with a real desk. **UNCONFIRMED** items are my inference and say so.

---

## 1. OSC wire format as the X32 uses it

### 1.1 Message layout — CONFIRMED (DOC + LIB)

DOC lines 537–557:

> With very few exceptions ... the X32/M32 follow the guidelines as set by the Open Sound Control (OSC) 1.06, implementing the 4 basic OSC type tags for int32, float32, string, and blob.
> - all parameters must be big-endian and 4-byte aligned/padded, as per OSC specification.
> - padding is done with null bytes.
> - float parameters must be in range 0.0 – 1.0 [for normal parameter addresses]
> - integer and float parameters are signed 32-bit values.
> - strings must be null-terminated.
> - enum parameters can be sent as strings or integers
> - boolean parameters will map to enum type {OFF, ON} (or OSC integer {0, 1})
>
> An OSC command typically consists in a 4-byte padded OSC message [address], followed by a 4-byte padded type tag string, and if a non-empty type tag string is present, one or more 4-byte aligned/padded arguments.

The encoder every Maillot tool uses — `X32lib/Xsprint.c` (verbatim):

```c
int Xsprint(char *bd, int index, char format, void *bs)
{
	int i;
	switch (format) {
	case 's':
	// string : copy characters one at a time until a 0 is found
		if (bs) {
			strcpy (bd+index, bs);
			index += (int)strlen(bs) + 1;
		} else {
			bd[index++] = 0;
		}
	// align to 4 bytes boundary if needed
		while (index & 3) bd[index++] = 0;
		break;
	case 'f':
	case 'i':
	// float or int : copy the 4 bytes of float or int in big-endian order
		i = 4;
		while (i > 0)
			bd[index++] = (char)(((char*)bs)[--i]);
		break;
	default:
		break;
	}
	return index;
}

int Xfprint(char *bd, int index, char* text, char format, void *bs)
{
	strcpy (bd+index, text);
	index += (int)strlen(text) + 1;
	while (index & 3) bd[index++] = 0;
	bd[index++] = ',';
	bd[index++] = format;
	bd[index++] = 0;
	bd[index++] = 0;
	return Xsprint(bd, index, format, bs);
}
```

Rules that follow from this:
- Address string: bytes + at least one NUL, padded with NUL to a multiple of 4. `strlen+1` rounded up: a 16-char address like `/ch/01/mix/fader` occupies 20 bytes (`/ch/01/mix/fader~~~~`).
- Type-tag string: `,` + tags + NUL, padded to a multiple of 4. `,f` → `,f~~` (4 bytes); `,ssss` → `,ssss~~~` (8 bytes); `,siii` → `,siii~~~`.
- int32 / float32: 4 bytes big-endian (network order). Worked examples (DOC 544–547 and appendix lines 9576–9596): `0.0 → 00000000`, `0.5 → 3f000000`, `1.0 → 3f800000`, `0.9 → 3f666668`, `0.4648 → 3eedfa44`; int 16 → `00000010`, int 3 → `00000003`. VERIFIED: DOC text dump 544–547, 9576–9596. VERIFIED-CORRECTED (clarification): the DOC's appendix values `0.7 → 3f333334`, `0.8 → 3f4cccce`, `0.9 → 3f666668` are **not** the IEEE-754 round-to-nearest encodings (those are `3f333333`, `3f4ccccd`, `3f666666`); they are 2 ULP high and are what the desk actually exchanged. Never compare desk floats bit-exactly — use a tolerance (>= 1e-6) or map through the step tables.
- string argument: same padding rule as the address.
- blob argument: 4-byte big-endian length, then that many bytes, then NUL-padded to a multiple of 4 (see §8 for the X32-specific *inside* of the blob, which is little-endian).

Full hex examples from DOC 604–655 (real desk):

```
/ch/01/eq/1/q~~~,f~~[0.4648]
2f63682f30312f65712f312f710000002c6600003eedfa44

/fx/4/par/23~~~~,~~~                                (request, empty type tag)
/fx/4/par/23~~~~,f~~[float 0.5]                     (reply, 24 bytes)
2f66782f342f7061722f3233000000002c6600003f000000

/ch/01/gate/mode~~~~,s~~GATE~~~~                     (enum as string)
2f63682f30312f676174652f6d6f6465000000002c7300004741544500000000
/ch/01/gate/mode~~~~,i~~[3]                          (same enum as int)
2f63682f30312f676174652f6d6f6465000000002c69000000000003
```

SOURCE: DOC lines 537–655; `X32lib/Xsprint.c` (whole file). VERIFIED: `Xsprint.c` re-read 2026-09-19 — the listing above is verbatim (only the comment lines `// check format` / `// don't copy anything` / `// first copy text` etc. were dropped).

### 1.2 Empty / missing type-tag string — CONFIRMED (DOC + README + EMU)

DOC 559–571:

> The OSC 1.0 specification mentions that older implementations of OSC may omit the OSC type tag string, and OSC implementations should be robust in the case of a missing OSC type tag string, which is the case of X32/M32 systems.
> `/info~~~,~~~`  correct format (OSC 1.0 compliant) command
> `/info~~~`      non OSC 1.0 compliant command, but accepted as older form of OSC

README (X32_Command section): *"Per OSC spec ... OSC Type Tag String is 'mandatory' ... This is nevertheless not necessary for X32 as the system accepts 'older' notations where empty OSC Type Tag Strings are not present."*

So: **the desk accepts a GET request that is only the padded address** (`/ch/01/mix/fader~~~~`, 20 bytes) and also accepts the compliant form with an empty tag string (`/ch/01/mix/fader~~~~,~~~`, 24 bytes). Every Maillot tool sends the bare form (`Xsprint(buf,0,'s',"/xremote")` = 12 bytes; `"/info"` = 8 bytes; `/xremote` literal `char xremote[12] = "/xremote"`).

**The desk always sends a type-tag string in what it emits** — every captured reply in DOC/README carries `,f`, `,i`, `,s`, `,ssss`, `,sss`, `,b`. The emulator's `funct_params` mirrors this (`Xsprint(s_buf, s_len, 's', ",i")` etc., X32.c 2995–3020). UNCONFIRMED whether any desk-originated packet ever omits it; none observed.

Emulator detection of "has args" (X32.c 2908–2913): it computes `f_len = ((c_len + 4) & ~3) + 1` (index of the first tag char after `,`) and treats the message as a SET only when `(r_len - 4 > c_len) && (r_buf[f_len] != 0)` — i.e. a `,` followed by at least one tag character. An empty `,` alone is treated as a GET. Same semantics as the desk per DOC. VERIFIED: X32.c 2911–2913 verbatim (`f_len = (((c_len + 4) & ~3) + 1); if ((r_len - 4 > c_len) && (r_buf[f_len] != 0))`); DOC 559–571; README 127–128.

### 1.3 Bundles — UNCONFIRMED (no support observed)

The emulator dispatches on the first 4 bytes of the packet against a table of address prefixes (`Xheader[]`, X32.c 490–528 — VERIFIED; the table actually begins with `"/shu"` (the emulator-only `/shutdown`), then `"/inf"`, `"/xin"`, `"/sta"`, `"/xre"`, `"/nod"`, `"/\0\0\0"`, `"/con"`, `"/mai"`, `"/-pr"`, `"/-st"`, `"/-ur"`, `"/ch/"`, `"/aux"`, `"/fxr"`, `"/bus"`, `"/mtx"`, `"/dca"`, `"/fx/"`, `"/out"`, `"/hea"`, `"/met"`, `"/-ha"`, `"/ins"`, `"/-sh"`, `"/ren"`, `"/cop"`, `"/add"`, `"/loa"`, `"/sav"`, `"/del"`, `"/uns"`, `"/-us"`, `"/und"`, `"/-ac"`, `"/-li"`, `"/sho"`). `#bundle` is not there, so the emulator silently drops bundles. The DOC never mentions `#bundle`, timetags, or bundles; no Maillot tool sends them. VERIFIED: a grep of the whole 9610-line DOC text dump for `bundle` returns nothing; the emulator compares the first 4 bytes as a single int (`Xheader[i].header.icom == (int) *((int*) v_buf)`, X32.c 1046), so `#bun` matches no entry. Treat the desk as **one message per UDP datagram, no bundles**. Do not rely on bundles.

### 1.4 Multiple arguments (multi-tag SET) — CONFIRMED (DOC 604–616)

```
/ch/01/eq/1 ,ifff [2] [0.2650] [0.5000] [0.4648]
```
> This is equivalent to the following 4 simpler commands: `/ch/01/eq/1/t ,i 2` / `/ch/01/eq/1/f ,f 0.2650` / `/ch/01/eq/1/g ,f 0.5000` / `/ch/01/eq/1/q ,f 0.4648`

Caveat (DOC footnote 7, lines 649–652): this only applies to combinations of `,i`/`,f`; a **string** argument sent to a *node* address (e.g. `/ch/01/config ,siii name 1 3 1`) is interpreted differently (reserved for X32-Edit) and **does not work on X32/M32** (works on XAir). Emulator comment at X32.c 2901–2905 says the same. Use `/ ,s "ch/01/config ..."` (see §6) or individual leaf addresses instead.

---

## 2. UDP transport — CONFIRMED

| Fact | Value | Source |
|---|---|---|
| Port | UDP **10023** (XAir: 10024) | DOC 259, 266, 591; `X32.c` 929–930 `strcpy(Xport_str, "10023")`; `X32_Command.c` `strcpy(Xport_str, "10023")`; `X32Connect.c` `htons(atoi("10023"))` |
| Reply routing | "replies are sent back to the requester's IP/port" / "The server replies on the UDP port used by the client when establishing communication." | DOC 259–260, 266–267 |
| Consequence | Use **one socket** for send and receive; the desk answers to the *source* IP:port of the datagram it received. Every Maillot client does `sendto()`/`recvfrom()` on the same fd. Push updates (`/xremote`), subscription blobs and `/meters` blobs also go to that same source port. | `X32_Command.c` (single `Xfd`), `X32.c` `Xsend()` 1156–1191: `sendto(Xfd, s_buf, s_len, 0, Client_ip_pt, Client_ip_len)` where `Client_ip_pt` was filled by `recvfrom` (X32.c 1008). VERIFIED |
| Client identity | The emulator identifies a client by `sockaddr.sa_data` (IP + port). A client that changes source port is a *different* client. | X32.c 1175, 3114, 3097 |
| Emulator client cap | `MAX_CLIENTS 4` xremote clients | X32.c 79 (VERIFIED-CORRECTED: line 79, not 80; `XREMOTE_TIME 11` is line 81, `BSIZE 512` line 71, `XVERSION "4.06"` line 70) |
| Real desk client cap | DOC: "Triggers X32 to send all parameter changes to **maximum four active clients**" (row for `/xremote`) | DOC 446–455 |
| Packet loss | Plain UDP, no ACK, no error report. DOC warns that burst `/node` or `/showdump` traffic over 54 Mb/s Wi-Fi loses datagrams silently. | DOC 269–274, 2900–2907 |

---

## 3. `/info`, `/xinfo`, `/status`

### 3.1 `/info` — CONFIRMED

Request: `/info~~~` (8 bytes) or `/info~~~,~~~` (12 bytes). No arguments.

Reply typetag `,ssss`, argument order:

1. `server_version` — string, e.g. `V2.05` (this is the OSC-server version, **not** firmware). VERIFIED-CORRECTED: every DOC capture (FW 2.08, 2.10, 2.12; DOC 483, 575–589, 630) shows `V2.05`, but there is **no** capture from a FW 3.x/4.x desk anywhere in DOC or README, so the earlier "stayed V2.05 across FW 2.x–4.x" claim was unsupported. The emulator (`v0.88`, emulating FW `4.06`) deliberately answers `V2.07` (X32.c 3053), and the M32 firmware change-log lists an osc-server bump 2.05->2.07 (2014), so a current desk most likely reports `V2.07`. UNCONFIRMED which exact string a FW 4.x desk returns — never key on this field.
2. `server_name` — string, always `osc-server` on a real desk
3. `console_model` — string: `X32`, `X32RACK`, `X32C` (Compact), `X32P` (Producer), `X32CORE`, `M32`, `M32C`, `M32R`
4. `console_version` — firmware string, e.g. `2.12`, `3.04`, `4.06`

Verbatim real-desk replies (DOC 575–589, 627–630):

```
X32 Standard: /info~~~,ssss~~~V2.05~~~osc-server~~X32~2.12~~~~        (48 bytes for "2.10" firmware capture)
X32 Rack:     /info~~~,ssss~~~V2.05~~~osc-server~~X32RACK~2.12~~~~
X32 Compact:  /info~~~,ssss~~~V2.05~~~osc-server~~X32C~~2.12~~~~
X32 Producer: /info~~~,ssss~~~V2.05~~~osc-server~~X32P~2.12~~~~
X32 Core:     /info~~~,ssss~~~V2.05~~~osc-server~~X32CORE~2.12~~~~
M32 Standard: /info~~~,ssss~~~V2.05~~~osc-server~~M32~2.12~~~~
XR18 (port 10024): /info~~~,ssss~~~V0.04~~~XR18-1D-DA-B4~~~XR18~~~~1.12~~~~
```

Byte accounting for the 48-byte X32 reply: `/info~~~`(8) + `,ssss~~~`(8) + `V2.05~~~`(8) + `osc-server~~`(12) + `X32~`(4) + `2.10~~~~`(8) = 48.

Emulator (X32.c 3050–3059, verbatim — VERIFIED):

```c
int function_info() {
	s_len = Xsprint(s_buf, 0, 's', "/info");
	s_len = Xsprint(s_buf, s_len, 's', ",ssss");
	s_len = Xsprint(s_buf, s_len, 's', "V2.07");
	if (Xprefs[X32NAME].value.str) s_len = Xsprint(s_buf, s_len, 's', Xprefs[X32NAME].value.str);
	else                           s_len = Xsprint(s_buf, s_len, 's', "X32 Emulator");
	s_len = Xsprint(s_buf, s_len, 's', "X32");
	s_len = Xsprint(s_buf, s_len, 's', XVERSION);   // "4.06"
	return S_SND; // send reply only to requesting client
}
```
Note the emulator puts the console *name* (`/-prefs/name`) in slot 2 where a real desk says `osc-server`; and reports `V2.07`. Do not key on slot 2 to detect a real desk.

Used as the connection probe by `X32Connect.c`: send `/info`, `select()` with 100 ms timeout, check reply starts with `/info`.

### 3.2 `/xinfo` — CONFIRMED

Request: `/xinfo~~` (8 bytes). Reply typetag `,ssss`:

1. `network address` — the desk's own IPv4 as a string, e.g. `192.168.1.62`
2. `network name` — the console name from `/-prefs/name`, default of the form `X32-02-4A-53` (last three MAC octets)
3. `console_model` — e.g. `X32`
4. `console_version` — firmware, e.g. `3.04`

Verbatim (DOC 487–488): `/xinfo~~,ssss~~~192.168.1.62~~~~X32-02-4A-53~~~~X32~3.04~~~~`

DOC 3033 (`/-prefs/name` row): "The name is also reported by the /xinfo command."

Emulator (X32.c 3063–3072) emits `/xinfo`, `,ssss`, `Xip_str`, prefs-name-or-"X32 Emulator", `"X32"`, `XVERSION`.

**Broadcast discovery — CONFIRMED (CMD).** `X32_Command.c` v1.40+ "Enable autoconnect if no IP data is provided": when no `-i`, it takes its own IP, sets the last octet to `0xff` (`Xip.sin_addr.S_un.S_un_b.s_b4 = 0xff` / `s_addr |= 0xff000000`), sets `SO_BROADCAST`, then sends `/xinfo` up to 5 times with a 500 ms `select()` each, and on a reply whose address is `/xinfo` copies the desk IP from the first string argument: `strcpy(Xip_str, r_buf+16)` (offset 16 = 8 for `/xinfo~~` + 8 for `,ssss~~~`). Then it talks unicast to that IP. So **the desk answers `/xinfo` sent to the /24 directed broadcast, and the reply's first argument is the desk IP**. VERIFIED: X32_Command.c 325–376 (`s_b4 = 0xff` / `s_addr |= 0xff000000`, `SO_BROADCAST`, `timeout.tv_usec = 500000`, `for (keep_on = 0; keep_on < 5; keep_on++)`, `if (strcmp(r_buf, "/xinfo") == 0) strcpy(Xip_str, r_buf+16)`); emulator `function_xinfo` X32.c 3063–3072. (Whether `255.255.255.255` limited broadcast also works: UNCONFIRMED; Maillot uses the subnet-directed broadcast.)

### 3.3 `/status` — CONFIRMED

Request: `/status~` (8 bytes). Reply typetag `,sss`:

1. `state` — string `active`
2. `IP_address` — desk IPv4 string
3. `server_name` — `osc-server`

Verbatim real desk (DOC 632–635, 52 bytes): `/status~,sss~~~~active~~192.168.0.64~~~~osc-server~~`

Emulator (X32.c 3076–3087) returns `/status`, `,sss`, `active`, `Xip_str`, prefs-name-or-"X32 Emulator" (again name instead of `osc-server`). VERIFIED: X32.c 3076–3087; DOC 489–491, 632–635 (52 bytes = `/status~`8 + `,sss~~~~`8 + `active~~`8 + `192.168.0.64~~~~`16 + `osc-server~~`12).

---

## 4. `/xremote` — CONFIRMED

### 4.1 Semantics and lifetime

DOC 458–470:

> After receiving an /xremote command, the X32 will update the client with changes taking place in the X32, such as fader movements, bank change requests, and screen updates. Some changes or user actions will not be reported as they do not directly affect the connected clients ...
> Registering for desk updates with a /xremote command maintains updates for **10 seconds**, after which a new /xremote command should be issued by the client to keep the updating process alive.

DOC 499–505 (server-initiated row):

> If /xremote is active, the X32 console echoes the value of a console parameter in response to a set command from another client or X32 parameter change, e.g.
> `/-stat/solosw/01~~~~,i~~[1]`
> `/-stat/solo~,i~~[1]`
> `/ch/01/mix/01/pan~~~,f~~[1.0000]`

Request format: `/xremote~~~~` — 12 bytes, no type tag. Every Maillot client: `char xremote[12] = "/xremote";` sent as 12 bytes. No reply/ack is sent for `/xremote` itself (emulator `function_xremote` returns 0 = nothing to send; no ack observed in any real-desk capture).

Renewal cadence used by Maillot's tools against real desks: **every 9 s** (`XREMOTE_TIMEOUT 9` in `X32_Command.c`, `X32Tap.c`, `X32TCP.c`, `X32SsaverGW.c`; `if (now > before + 9)` in `X32ReaperW.c` 378 and `X32Replay.c` 236; `X32Fade.c` comment "sent every 9..10 seconds"; `X32Midi2OSC.c` "every 10s"). Recommend 8–9 s. VERIFIED: `#define XREMOTE_TIMEOUT 9` at X32_Command.c 77, X32Tap.c 54, X32TCP.c 48, X32SsaverGW.c 23; `if (now > before + 9)` at X32ReaperW.c 378 and X32Replay.c 236; X32Fade.c 135 (`sent every 9..10 seconds`); X32Midi2OSC.c 266 (`every 10s`).

Emulator implementation (X32.c 81, 3108–3136) — VERIFIED verbatim; `Xsend` (X32.c 1156–1191) also verified: pushes go only to clients with `xrem > now` **and** `strcmp(X32Client[i].sock.sa_data, Client_ip_pt->sa_data) != 0`:

```c
#define XREMOTE_TIME 11	// xremote max time before abandon of client updating
...
int function_xremote() {
	int k;
	for (k = 0; k < MAX_CLIENTS; k++) {
		if (X32Client[k].vlid) {
			if (strcmp(X32Client[k].sock.sa_data, Client_ip_pt->sa_data) == 0) {
				X32Client[k].xrem = time(NULL) + XREMOTE_TIME; //update existing client
				return 0;
			}
		}
	}
	// attempt to register a new client... if room available
	for (k = 0; k < MAX_CLIENTS; k++) {
		if (X32Client[k].vlid == 0) { // create new record
			X32Client[k].sock = *Client_ip_pt;
			X32Client[k].vlid = 1;
			X32Client[k].xrem = time(NULL) + XREMOTE_TIME;
			return 0;
		} else if (X32Client[k].xrem < time(NULL)) { // replace outdated record
			X32Client[k].sock = *Client_ip_pt;
			X32Client[k].xrem = time(NULL) + XREMOTE_TIME;
			return 0;
		}
	}
	return 0; // no room for new clients! (todo; another return status?)
}
```

### 4.2 What gets pushed, and to whom

Emulator `Xsend()` (X32.c 1153–1191): after any handler that returns `S_REM`, the current `s_buf` is sent to every valid client whose `xrem` deadline is in the future **and whose address differs from the sender** (`strcmp(X32Client[i].sock.sa_data, Client_ip_pt->sa_data) != 0`). So **the originating client does not get its own SET echoed** (emulator behaviour; DOC wording "in response to a set command from *another* client" agrees).

Pushed message format = exactly the GET-reply format: `address ,tag value`. For a SET arriving with multiple tags the emulator forwards the received datagram verbatim (`memcpy(s_buf, r_buf, r_len)` at X32.c 2973–2976), so a subscriber could see `/ch/01/eq/1 ,ifff ...`. Real desk: all captured pushes are single-parameter (`/ch/09/mix/on~~~,i~~[1]`, `/config/buslink/1-2~,i~~[0]`, DOC 4396–4420). For `/` (node-style) writes the desk echoes the `/ ,s "..."` line back (DOC 4591–4593), and individual leaf updates are pushed to xremote clients (emulator `RLinf`/`XslashSetString` call `Xfprint(...)` + `Xsend(S_REM)`).

Emulator only pushes when a value actually **changed** (`update` flag in `funct_params`, EPSILON compare for floats; header comment v0.71: "if a data is not changed, it will not be sent to remote clients").

### 4.3 Do reads work without `/xremote`?

Yes. GET/SET replies go to the requester regardless of `/xremote` (`S_SND` path in `Xsend`, independent of `X32Client[]`). `/xremote` only adds unsolicited pushes. CONFIRMED by every Maillot tool that does one-shot reads (`X32GetScene.c` never sends `/xremote`).

### 4.4 `/unsubscribe` and `/xremote`

Emulator `function_unsubscribe()` (X32.c 3090–3104, VERIFIED) removes the requesting client from the xremote table ("only simple/single unsubscribe command is recognized to remove possible xremote client"). `X32ReaperW.c` 824 sends `/unsubscribe` (no args) when signing off. DOC says `/unsubscribe` with no argument stops *all* active subscriptions (§7). Whether the real desk also cancels `/xremote` on a bare `/unsubscribe`: UNCONFIRMED (emulator does).

---

## 5. Reading and writing a parameter

### 5.1 GET — CONFIRMED

Send the padded address only (optionally with `,~~~`). Reply = same address + typetag + current value.

README real-dialog capture (emulator, but format identical to desk):

```
/ch/01/mix/fader
->X,   20 B: /ch/01/mix/fader~~~~
X->,   28 B: /ch/01/mix/fader~~~~,f~~[1.0000]
```

Emulator GET path, X32.c 2985–3029 (verbatim core — VERIFIED; note the `,f` value is emitted with `Xsprint(..., 'i', &command[i].value.ff)`, i.e. the float's 4 bytes byte-reversed, which is the same big-endian copy as `'f'`):

```c
		if (command[i].flags & F_GET) { // if the command is part of the GET family
			s_len = Xsprint(s_buf, 0, 's', command[i].command);
			c_type = command[i].format.typ;
			if (c_type == FX32) {
				c_type = FXc_lookup(command, i); // the function returns I32, F32, S32,...
			}
			if (c_type == I32 || c_type == E32 || c_type == P32) {
				s_len = Xsprint(s_buf, s_len, 's', ",i");
				s_len = Xsprint(s_buf, s_len, 'i', &command[i].value.ii);
			} else if (c_type == F32) {
				s_len = Xsprint(s_buf, s_len, 's', ",f");
				s_len = Xsprint(s_buf, s_len, 'i', &command[i].value.ff);
			} else if (c_type == S32) {
				s_len = Xsprint(s_buf, s_len, 's', ",s");
				if (command[i].value.str) s_len = Xsprint(s_buf, s_len, 's', command[i].value.str);
				else s_len = Xsprint(s_buf, s_len, 's', &zero); // return nil chars if no string
			} ...
			p_status = S_SND;
```

Type of the reply: enums (`E32`) and bitmaps (`P32`, e.g. `/ch/01/grp/dca`, `/-prefs/haflags`) come back as **`,i`**; floats `,f`; names `,s`. Empty string → `,s` with a single NUL padded to 4 (`~~~~`).

### 5.2 SET — CONFIRMED

Send address + typetag + value. Emulator SET path (X32.c 2914–2980) reads each tag char, converts big-endian, stores, and returns `S_REM` (push to other xremote clients) — **no reply to the sender**. DOC 340–347: "Sets the value of a console parameter ... If it exists and value is in range, the new value takes place in the X32." No ack. If you need confirmation, GET it back or hold `/xremote` on a *second* socket (the same socket won't see its own echo, see §4.2).

Enum SET accepts `,s "GATE"` or `,i 3` (DOC 640–647). Bool = enum `{OFF,ON}` = `,i 0/1`.

Float SET: the desk snaps to its nearest "known" step (DOC 658–664: EQ frequency has 201 steps; "An OSC floating point value outside of the known values will be rounded to the nearest known value").

### 5.3 Unknown address / errors — CONFIRMED for emulator, UNCONFIRMED for desk

Emulator: every `function_*` ends with `return 0;` when no table entry matches; `Xsend(0)` sends nothing. `function_node` comment: `return 0; // no reply if error detected`. **No error replies exist anywhere in the protocol** — DOC defines none, no Maillot client parses any. Practical rule: an unknown address, wrong index, or malformed packet is silently ignored; treat a missing reply within your timeout as "not supported / lost".

### 5.4 Reply latency expectations

- `X32Connect.c` waits **100 ms** for the `/info` probe, then drops the socket timeout to a caller-supplied value (comment: "5ms").
- `X32_Command.c` uses 500 ms for the `/xinfo` broadcast probe, then **1 ms** polls (`timeout.tv_usec = 1000 //Set timeout for non blocking recvfrom(): 1ms`) and reads whatever has arrived; `-s` file mode uses 10 ms per line.
- `X32GetScene.c` waits up to 100 ms per `/node` request; `X32TCP.c` reports "no data" after 10 ms.
- Emulator main loop polls every 10 ms (`timeout.tv_usec = 10000`).
- VERIFIED: X32Connect.c (`timeout.tv_usec = 100000`, then `timeout.tv_usec = btime; // ...5ms`), X32_Command.c 346/381/417, X32GetScene.c 118, X32ReaperW.c 854/926 (100 ms probe, then 1 ms), X32Replay.c 183/230 (500 ms, then 10 ms), X32SsaverGW.c 315 (50 ms).

UNCONFIRMED numeric latency of a real desk; the above implies typical replies well under 10 ms on wired LAN. Recommend a 50–100 ms per-request timeout with one retry.

---

## 6. `/node` and `/` (X32node text format)

### 6.1 `/node` request — CONFIRMED

Format: address `/node`, typetag `,s`, one string = node path **without** leading `/`.

```
/node~~~,s~~ch/01/config~~~~          (28 bytes; README capture)
/node~~~,s~~headamp/124~              (DOC 4618: "!! note: no `/' before the request string")
/node~~~,s~~-prefs/iQ/01~~~~          (DOC 368)
```
`X32GetScene.c` builds it as `Xsprint("/node")`, `Xsprint(",s")`, `Xsprint(l_read + 1)` (strips the `/` from a scene-file line). VERIFIED: X32GetScene.c 134–138, 152; X32.c 3792 (`str_pt_in = r_buf + 12`), 4446–4450 (leading-`/` tolerance). The emulator's `function_node` reads the string at byte offset 12 (`str_pt_in = r_buf + 12`) and also tolerates a leading `/` in the single-parameter path (`if (*(r_buf + 12) == '/')`). UNCONFIRMED whether a real desk tolerates a leading `/` in `/node`; DOC says omit it. For the `/` write command the leading slash is optional (DOC 4571–4573).

### 6.2 `/node` reply — CONFIRMED

- Address is **`node`** (no leading slash!), padded: `node~~~~` (8 bytes).
- Typetag `,s~~`.
- One string: `/` + node path + one space-separated value per parameter, terminated by **`\n`**, then NUL, padded to 4.

Verbatim captures:

```
README (emulator, 40 B):  node~~~~,s~~/ch/01/config "" 0 OFF 0~~~~   (string is 25 chars incl. the \n, +NUL, padded to 28; 8+4+28 = 40)
README (emulator, 24 B):  node~~~~,s~~/fx/1 GEQ2~~                  (README 390–393; request /node~~~,s~~fx/1~~~~ = 20 B; `/fx/2` -> `TEQ2`)
DOC 4621 (desk):          node~~~~,s~~/headamp/124 +0.0 OFF\n~~~~
DOC 376 (desk):           node~~~,s~~/-prefs/iQ/01 none Linear 0\n~~~~
DOC 2916–2974 (desk, /showdump replies, same format):
  node~~~~,s~~/-show/showfile/show "MyShow" 0 0 0 0 0 0 0 0 0 0 "2.08"
  node~~~~,s~~/-show/showfile/scene/001 "AAA" "aaa" %111111110 1
  node~~~~,s~~/-show/showfile/cue/000 100 "CCC" 0 -1 -1 0 1 0 0
  node~~~~,s~~/-show/showfile/snippet/000 "Aaa" 1 1 0 32768 1
```

Emulator reply assembly (X32.c 3804–3806 and 4430–4434, verbatim — VERIFIED):

```c
				s_len = Xsprint(s_buf, 0, 's', "node");
				s_len = Xsprint(s_buf, s_len, 's', ",s");
				s_buf[s_len] = 0;
				...
				strcat(s_buf + s_len, command[i].command);      // "/ch/01/config" with leading '/'
				... per-node strcat of " value" tokens ...
				s_len += strlen(s_buf + s_len);
				s_buf[s_len++] = '\n';
				s_buf[s_len++] = 0;
				while (s_len & 3) s_buf[s_len++] = 0;
				return S_SND; // send reply only to requesting client
```

`X32GetScene.c` prints the reply from the first `/` onward with `printf("%s", b_rec + i)` — the `\n` in the string produces the scene-file line break. A parser should: skip 12 bytes (`node~~~~,s~~`), read the NUL-terminated string, strip the trailing `\n`, split on whitespace (respecting `"..."`).

Single-parameter node requests (emulator ≥0.70, X32.c 4418–4466 and `function_node_single` 4470–4520): `/node ,s -prefs/rta/visibility` or `/node ,s headamp/006/phantom` reply `node ,s /-prefs/rta/visibility 25%\n` — same envelope, one token. Formatting in `function_node_single`: enum → its token from the enum table; `I32` → `" %d"`; `F32` → `" %f"` (**emulator prints raw 0..1 float here — differs from real desk which prints the engineering value; UNCONFIRMED exact desk format for single-leaf node reads**); `S32` → the string (unquoted in emulator); `P32` → `" %" + binary digits` (minimal width in emulator; desk uses fixed width, e.g. `%000000000`).

### 6.3 Token formatting rules in node replies — CONFIRMED (EMU formatter) with desk observations

Emulator helper functions (X32.c 1237–1303, verbatim):

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
// Slinf: returns linear float [min..max] in different formats  -> " %.<pre>f" of fmin + (fmax-fmin)*fin
// Slinfs: same with sign                                        -> " %+.<pre>f"
// Slogf: log float                                              -> " %.<pre>f" of exp(fin*log(fmax/fmin)+log(fmin))
// Sbitmp: " %" followed by <len> binary digits, MSB first
// Sint:   " %d"
```

Worked examples: fader `0.75` → Slevel: `20/(0.5)*(0.75-0.5)-10 = 0.0` → `+0.0`; `0.5` → `-10.0`; `0.0` → `-oo`; `1.0` → `+10.0`; `0.25` → `-30.0`. Pan `0.5` with `Slinfs(x,-100,+100,0)` → `+0`; `0.75` → `+50`. HPF `0.0` with log(20..400) → `20`; gate hold `Slogf(x,0.02,2000,2)`.

Tokens:
- Enum tokens are **names**, not indices: `OFF`/`ON`, colours `OFF RD GN YE BL MG CY WH OFFi RDi GNi YEi BLi MGi CYi WHi`, gate modes `EXP2 EXP3 EXP4 GATE DUCK`, dyn ratio `1.1 1.3 1.5 2.0 2.5 3.0 4.0 5.0 7.0 10 20 100`, EQ types `LCut LShv PEQ VEQ HShv HCut` (+ `BU6 BU12 BS12 LR12 BU18 BU24 BS24 LR24` on bus/main/mtx), send types `IN/LC <-EQ EQ-> PRE POST GRP`, filter types `LC6 LC12 HC6 HC12 1.0 2.0 3.0 5.0 10.0`, insert `OFF FX1L ... FX8R AUX1 ... AUX6`, RTA visibility `OFF 25% 30% ... 80%` (X32.c 314–441, X32Fx.h 12–104). VERIFIED against X32.c 314–441 on 2026-09-19. Emulator table slips found there: `Prtavis[]` entries `45% 50% 65% 70% 75% 80%` and `Prtaph[]` entries `5 6 8` lack the leading space, so emulator output glues them to the previous token (e.g. `... 045%`); `Xgmode[]` is `EXP2 EXP3 EXP GATE DUCK` (index 2 misnamed, desk = `EXP4`); `Pcmado[]` `1-32` lacks its space; `XRtaea[]` `AN17-24` too. Desk output is unaffected by any of these.
- Strings are wrapped in double quotes; empty string is `""` (`" \"\""`), and some nodes emit `" "` for absent AES50/session names (SAES/STAPE/PKEY cases in `function_node`).
- Bitmaps are `%` + fixed-width binary (`Sbitmp(v, 8)` for `grp/dca` → `%00000000`; `Sbitmp(v,6)` for `grp/mute`; `%000000000` 9 bits for scene safes; `%1111` etc.).
- Frequencies ≥ 1 kHz use **`k` notation with the `k` as decimal point**: `1k02`, `2k50`, `10k02`, `20k00` (table `f201[]`/`f121[]`/`f101[]`, X32Fx.h 107–160: `" 990.9", " 1k02", " 1k06" ... " 19k32", " 20k00"`). Parsers must map `NkMM` → `N.MM × 1000`.
- Levels use `-oo` for −∞ and a signed `%+.1f` otherwise (`+0.0`, `-10.0`, `+10.0`); pan is signed integer (`+0`, `-50`).
- **Real desk pads numeric columns with spaces** — README scene lines captured from a desk: `/ch/01/delay OFF   0.3`, `/ch/01/mix OFF   0 ON +0 OFF -oo`. The emulator emits exactly one space. Always split on *runs* of whitespace; never assume single spaces or fixed offsets.
  VERIFIED (resolves the padding question) from a console-saved scene file (`#2.7# "General 1.2.2" "" %000000000 1` header, i.e. written by an X32 at FW 2.x; `cabcookie/saddleback-x32-general-scene/Soundboard Setup.scn`, raw file fetched 2026-09-19 — scene files are the desk's own node strings) and from the real-desk excerpt in the comment block at the end of Maillot's `SetSceneParse.c` (lines 2740–2797):
  - **Level/dB fields** (`fader`, `mlevel`, send `level`, dca fader, `/config/solo` level, delay time) are **right-aligned in a 5-character field** after the single separator space: `ON  +2.1`, `OFF   -oo`, `OFF   0.0`, `OFF -81.0`, `OFF  -8.3`, `/ch/01/mix/fader  -9.9`, `/bus/01/mix/fader -48.3`, `/dca/1/fader +10.0`, `/ch/01/delay OFF   0.3`. The desk can print **`-0.0`** (`/fxrtn/01/mix/fader  -0.0`).
  - **Pan** is not padded: `+0`, `+20`, `-100`.
  - **Gate/dyn hold and release**: adaptive precision and right-aligned width 4: `0.03`, ` 100`, ` 538`, ` 576`; **mgain has two decimals** (`8.00`); attack/knee/mix are plain ints.
  - **HPF frequency** in `/ch/xx/preamp` is an **integer right-aligned in width 3** (`24  79`), unlike the emulator's `f101[]` (`79.3`).
  - **EQ bands** are not padded: `PEQ 164.4 -3.75 1.8`, `PEQ 463.5 -6.50 2.8`.
  - **kHz tokens** can have one fractional digit (`11k9` in `/fx/1/par`), not always two — parse `<int>k<digits>` generically.
  - Verbatim lines: `/ch/01/config "Diazno" 1 CY 1`, `/ch/01/delay OFF   0.3`, `/ch/01/preamp +0.0 OFF OFF 24  79`, `/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0`, `/ch/01/gate/filter OFF 3.0 1k39`, `/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF`, `/ch/01/insert ON PRE FX4L`, `/ch/01/eq ON`, `/ch/01/eq/1 PEQ 164.4 -3.75 1.8`, `/ch/01/mix ON  +2.1 ON +0 OFF   -oo`, `/ch/01/mix/01 ON   -oo +0 PRE`, `/ch/01/mix/02 ON   -oo`, `/ch/01/grp %00000001 %000000`, `/ch/01/automix OFF -12.0`, `/bus/01/mix OFF   0.0 OFF +0 OFF -81.0`, `/bus/01/mix/01 ON   -oo +0 POST`, `/main/st/mix ON   -oo +0`, `/dca/1 OFF  -8.3`, `/fx/1 VREV`, `/fx/1/source MIX15 MIX15`, `/outputs/main/01 4 PRE OFF`, `/headamp/000 +0.0 OFF`, `/config/solo   0.0 AUX78 0.0 PFL PFL PFL ON OFF ON -22 OFF OFF OFF   0.3 OFF OFF OFF`, `/config/osc -40.0 100.2 1k00 F1 PINK 17`.
  - Widths may differ on newer firmware (this file is FW 2.x); treat them as evidence that padding exists, not as a fixed-column contract.
- Integer vs float ambiguity: node text does not distinguish; the meaning comes from the node definition (e.g. `/ch/01/config "" 1 YE 1` → name, icon(int), color(enum), source(int)). `hold`/`release` may print with decimals (`Slogf(...,2)` → `5.06`) while `attack`/`range` print as integers (`Slinf(...,0)`). VERIFIED: real desk prints `hold` as `0.03` or ` 100` and `release` as ` 538` (see padding bullet) — decimals are value-dependent, so always parse numerics as free-form floats.
- **Token count is firmware-dependent**: the FW 2.x scene file has `/ch/01/mix/01 ON   -oo +0 PRE` (4 tokens, no `panFollow`), while DOC 1486–1494 (FW >= 3) and the emulator add a 5th `panFollow` token to odd-numbered sends. Parse positionally but tolerate missing trailing tokens.
- No `%`-escaping/URL-encoding exists. Quotes inside names: no escape mechanism observed (UNCONFIRMED how the desk emits a name containing `"`; avoid).
- Max name length: DOC 1300 says `/ch/xx/config/name` is "A 12-character max string" (same for bus/mtx/dca); DOC footnote 50/52 says X-Live session names are documented as 19 chars but "only 16 or 17 characters can be displayed". Emulator copies names into 64-byte locals (`loc_str[64]`, X32.c 2899) — sending longer strings to the *emulator* overflows; the *desk* presumably truncates (UNCONFIRMED).

### 6.4 Verbatim example node replies

Legend: **[README]** = captured; **[DOC]** = captured from a desk; **[EMU]** = constructed by applying the emulator's `function_node` case for that node to the stated example values (token order, enum names and number formats are exactly what X32.c produces; spacing on a real desk may contain extra padding, see §6.3).

1. `/node ,s ch/01/config` → **[README]** `node~~~~,s~~/ch/01/config "" 0 OFF 0~~~~` (40 B; name="", icon 0, color OFF, source 0). Case `CHCO` (X32.c 3904–3915, VERIFIED): `"name"`, `Sint(icon)`, `Scolor[color]`, `Sint(source)`. Real desk: `/ch/01/config "Diazno" 1 CY 1`. Scene-file form from a real desk (README GetScene sample): `/ch/01/config "" 1 YE 1`.

2. `/node ,s ch/01/mix` → **[EMU]** case `CHMX` (3970–3977, VERIFIED; real desk: `/ch/01/mix ON  +2.1 ON +0 OFF   -oo`): `on`, `Slevel(fader)`, `st`, `Slinfs(pan,-100,100,0)`, `mono`, `Slevel(mlevel)`:
   `node~~~~,s~~/ch/01/mix ON +0.0 ON +0 OFF -oo\n~~` — real-desk spacing per README: `/ch/01/mix OFF   0 ON +0 OFF -oo`.

3. `/node ,s ch/01/mix/01` → **[EMU]** case `CHMO` (3978–3984, VERIFIED; `Sctype` = `Xmtype` `IN/LC <-EQ EQ-> PRE POST GRP`; real desk FW 2.x: `/ch/01/mix/01 ON   -oo +0 PRE` — no `panFollow` token on that firmware): `on`, `Slevel(level)`, `Slinfs(pan,-100,100,0)`, `Sctype[type]`, `Sint(panFollow)`:
   `node~~~~,s~~/ch/01/mix/01 OFF -oo +0 EQ-> 0\n~` (odd-numbered sends carry pan/type/panFollow; even sends `mix/02` are case `CHME`: `node ,s /ch/01/mix/02 OFF -oo\n`).

4. `/node ,s ch/01/eq/1` → **[EMU]** case `CHEQ` (3964–3969): `Setype[type]`, `f201[(int)(200*f+0.5)]`, `Slinfs(g,-15,15,2)`, `Slogf(q,10,0.315,1)`:
   `node~~~~,s~~/ch/01/eq/1 PEQ 100.0 +0.00 2.0\n~~~` (e.g. type index 2, f=0.2650 → `(int)(200*0.265+0.5)` = 53 → f201[53] = `124.7`; f=0.25 → index 50 → `112.5`; q=0.4648 → 10·(0.0315)^0.4648 ≈ `2.0`). For f = 0.7 → index 140 → `2k51`. VERIFIED-CORRECTED: the previous draft said `120.5` for f=0.25 and `4k37` for f=0.7; recomputed by script from the `f201[]` table in X32Fx.h (201 entries: f201[0]=` 20.0`, f201[50]=` 112.5`, f201[53]=` 124.7`, f201[100]=` 632.5`, f201[140]=` 2k51`, f201[200]=` 20k00`; `4k37` is index 146). Real desk: `/ch/01/eq/1 PEQ 164.4 -3.75 1.8`, `/ch/01/eq/2 PEQ 463.5 -6.50 2.8` — same token shapes as the emulator, no padding. Case `CHEQ` at X32.c 3964–3969 VERIFIED.

5. `/node ,s ch/01/dyn` → **[EMU]** case `CHDY` (3941–3957): `on`, `Sdmode`, `Sddet`, `Sdenv`, `Slinf(thr,-60,0,1)`, `Sdratio`, `Slinf(knee,0,5,0)`, `Slinf(mgain,0,24,1)`, `Slinf(attack,0,120,0)`, `Slogf(hold,0.02,2000,2)`, `Slogf(release,5,4000,0)`, `Sdpos`, `Sint(keysrc)`, `Slinf(mix,0,100,0)`, `auto`:
   `node~~~~,s~~/ch/01/dyn OFF COMP RMS LOG 0.0 3.0 0 0.0 0 0.02 5 POST 0 100 OFF\n` (defaults thr=1.0→`0.0`, ratio idx 5 → `3.0`, hold 0→`0.02`, release 0→`5`). VERIFIED: X32.c 3942–3958 (`Sdmode`=`Xdymode` COMP/EXP, `Sddet`=`Xdydet` PEAK/RMS, `Sdenv`=`Xdyenv` LIN/LOG, `Sdratio`=`Xdyrat`, `Sdpos`=`Xdyppos` PRE/POST). Real desk: `/ch/01/dyn ON COMP PEAK LIN -26.0 3.0 2 8.00 71 0.03  538 POST 0 100 OFF` — the desk prints mgain with **two** decimals (`8.00`; emulator one) and hold/release with adaptive precision (`0.03`, ` 538`).

6. `/node ,s ch/01/gate` → **[EMU]** case `CHGA` (3931–3940): `on`, `Sgmode`, `Slinf(thr,-80,0,1)`, `Slinf(range,3,60,1)`, `Slinf(attack,0,120,0)`, `Slogf(hold,0.02,2000,2)`, `Slogf(release,5,4000,0)`, `Sint(keysrc)`:
   `node~~~~,s~~/ch/01/gate OFF EXP2 -80.0 3.0 0 0.02 5 0\n`. (VERIFIED-CORRECTED: `Xgmode[]` at X32.c 321 is the 5-entry list `EXP2 EXP3 EXP GATE DUCK` — index 2 is misnamed `EXP`; it is *not* a 6-entry list as the previous draft said; DOC 642 gives `{EXP2, EXP3, EXP4, GATE, DUCK}` — use the DOC list.) Case `CHGA` X32.c 3926–3935 VERIFIED. Real desk: `/ch/01/gate ON EXP4 -46.5 27.0 20  100  576 0`, `/ch/01/gate/filter OFF 3.0 1k39`.

7. `/node ,s ch/01/preamp` → **[EMU]** case `CHPR` (3924–3930): `Slinfs(trim,-18,18,1)`, `invert`, `hpon`, `Sfslope[hpslope]`, `f101[(int)(100*hpf+0.5)]`:
   `node~~~~,s~~/ch/01/preamp +0.0 OFF OFF 24 20.0\n` (trim 0.5 → `+0.0`; slope idx 2 → `24`; hpf 0 → `20.0`, hpf 1.0 → `400.0`). VERIFIED: X32.c 3919–3925, `Sfslope`=`Xhslop` {12,18,24}, f101[0]=` 20.0`, f101[50]=` 89.4`, f101[100]=` 400.0`. Real desk: `/ch/01/preamp +0.0 OFF OFF 24  79` — the desk prints the HPF frequency as an **integer right-aligned in width 3**, not with a decimal as the emulator's `f101[]` does.

8. `/node ,s bus/01/mix` → **[EMU]** `Xbus01` uses the same `CHMX` node type (X32Bus.h 74–80): `node~~~~,s~~/bus/01/mix ON +0.0 ON +0 OFF -oo\n`. (`bus/01/mix/01` is `CHMO` → `OFF -oo +0 ... 0`, but note X32Bus.h 85 wires `type` to `OffOn` — an emulator table slip; desk emits `IN/LC <-EQ EQ-> PRE POST GRP` tokens as for channels.) VERIFIED: X32Bus.h 74–90 re-read. Real desk: `/bus/01/mix OFF   0.0 OFF +0 OFF -81.0`, `/bus/01/mix/01 ON   -oo +0 POST` — confirms the send-type token on bus->matrix sends.

9. `/node ,s main/st/mix` → **[EMU]** case `MSMX` (4032–4036): `on`, `Slevel(fader)`, `Slinfs(pan,-100,100,0)`:
   `node~~~~,s~~/main/st/mix ON +0.0 +0\n` (X32CfgMain.h 421–424: `/main/st/mix/on`, `/fader`, `/pan`). VERIFIED-CORRECTED (emulator bug, not desk): case `MSMX` (X32.c 4030–4034) reads the pan from `command[i + 4]`, but `/main/st/mix/pan` is `command[i + 3]`; `i + 4` is the `/main/st/mix/01` header whose float is 0, so the **emulator always prints `-100`** for main pan. The intended/desk format is `on fader pan`; real desk: `/main/st/mix ON   -oo +0`.

10. `/node ,s headamp/000` → **[DOC-shaped]** case `HAMP` (X32.c 4070–4073, VERIFIED: `Slinf(gain, -12., 60., 1)` then `ON`/`OFF`; real desk scene file: `/headamp/000 +0.0 OFF`): `Slinf(gain,-12,60,1)`, `phantom`:
    `node~~~~,s~~/headamp/000 +0.0 OFF\n~~~~` — desk capture for 124: `node~~~~,s~~/headamp/124 +0.0 OFF\n~~~~` (gain formatted `%+.1f` on the desk; emulator's `Slinf` with pre=1 prints `0.0` without sign — the desk sample shows `+0.0`, so parse a signed float).

11. `/node ,s fx/1` → **[EMU]** case `FXTYP1` (X32.c 4035–4037, VERIFIED; `FXSRC` 4038–4041, `FXPAR1` 4042–4043 -> `GetFxPar1` at 2128). Real desk: `/fx/1 VREV`, `/fx/1/source MIX15 MIX15`, `/fx/1/par 40 2.4 100 OFF FRONT 0.0 76 11k9 1.12 0.72 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0` (always 64 tokens, unused ones `0`; note `11k9`). README capture: `node~~~~,s~~/fx/1 GEQ2~~` (24 B). Example: `node~~~~,s~~/fx/1 HALL\n~~~`. `/node ,s fx/1/source` (case `FXSRC`): `node ,s /fx/1/source INS INS\n`. `/node ,s fx/1/par` (case `FXPAR1` → `GetFxPar1`, X32.c 2128+): one engineering-unit token per parameter, count and format depending on the FX type, e.g. HALL (12 params): `node ,s /fx/1/par 0 0.2 2 1000.0 1 -12 10.0 200.0 0.5 0 0 0\n` (predelay `Slinf 0..200 %.0f`, decay `Slogf 0.2..5 %.1f`, size `2..100`, damping `Slogf 1000..20000 %.1f`, diffuse `1..30`, level `-12..+12`, lo cut `Slogf 10..500`, hi cut `Slogf 200..20000`, bass mult `0.5..2`, spread `0..50`, shape `0..250`, mod `0..100`). README: "`/node ,s fx/01/par` - retrieve the 64 parameters of effect at FX slot 1" (note README writes `fx/01`, the tables use `fx/1`; the emulator's `Xnode` prefix table has `"fx/1"`, so use single-digit).

12. `/node ,s -show/prepos` → **[EMU]** `/-show/prepos/current` is a plain `I32` leaf (X32Show.h 13) and `-show/prepos` is not an `F_FND` header in the emulator, so the emulator's answer goes through the single-parameter path only for the full leaf: `/node ,s -show/prepos/current` → `node~~~~,s~~/-show/prepos/current 0\n`. DOC 2441: "`/-show/prepos/current` int — Scene page cue, scene or snippet slot highlighted". DOC node list includes `-show/prepos` and `-show/prepos/current` (DOC 4749–4750). Expected desk reply for `/node ,s -show/prepos`: `node ,s /-show/prepos/current <n>\n` — UNCONFIRMED whether the desk answers the parent path. VERIFIED: X32Show.h 13 (`/-show/prepos/current` `I32`), DOC 4741–4743 node list (`-show`, `-show/prepos`, `-show/prepos/current`), DOC 2441 (`int = [1-099]`).

13. `/node ,s -prefs/rta` → **[EMU]** case `PRTA` (4118–4130): `Prtavis[visibility]`, `Slinf(gain,0,60,0)`, `autogain`, `Sint(source)`, `POST`/` PRE` (pos), `SPEC`/`BAR` (mode), `Sbitmp(options,6)`, `PEAK`/`RMS` (det), `Slogf(decay,0.25,16,2)`, `Prtaph[peakhold]`:
    `node~~~~,s~~/-prefs/rta OFF 0 OFF 0 PRE BAR %000000 RMS 0.25 OFF\n`. (Emulator bug: the `pos` token for POST is emitted without a leading space — `command[i + 5].value.ii ? "POST" : " PRE"` — so `PRE`/`POST` may be glued to the previous number in emulator output only.) VERIFIED: X32.c 4131–4142 (token order and formats exactly as listed; `Prtavis`/`Prtaph` glue slips see §6.3). `-stat/rta` is **not** a node on the desk; the RTA-related `/-stat` leaves are `rtamodeeq rtamodegeq rtaeqpre rtageqpost rtasource` (X32PrefStat.h 307–311, DOC 3379–3383) and belong to the big `/node ,s -stat` reply (case `STAT`).

14. `/node ,s -stat` → **[EMU]** case `STAT` (4157–4181): `Sselidx[selidx]`, `chfaderbank`, `grpfaderbank`, `sendsonfader`, `bussendbank`, `eqband`, `solo`, `keysolo`, `userbank`, `autosave`, `lock`, `usbmounted`, `remote`, `rtamodeeq`(BAR/SPEC), `rtamodegeq`, `rtaeqpre`, `rtageqpost`, `rtasource`, `xcardtype`, `xcardsync`, `geqonfdr`, `geqpos`, `dcaspill`:
    `node~~~~,s~~/-stat Ch01 0 0 OFF 0 0 OFF OFF 0 OFF OFF OFF OFF BAR BAR OFF OFF 0 0 OFF OFF 0 0\n`. VERIFIED-CORRECTED: token 11 (`/-stat/lock`) is printed by the emulator as `ON`/`OFF` (X32.c 4177: `command[i + 11].value.ii ? " ON" : " OFF"`) even though the leaf is `I32` (X32PrefStat.h 304; DOC 3376 `int`, 0/1/2 — `,i 2` doubles as the emulator's shutdown command, X32.c 29/4586); the previous draft showed `0`. Token order verified against X32PrefStat.h 293–316 and X32.c 4166–4191 (23 tokens after the path: selidx, chfaderbank, grpfaderbank, sendsonfader, bussendbank, eqband, solo, keysolo, userbank, autosave, lock, usbmounted, remote, rtamodeeq, rtamodegeq, rtaeqpre, rtageqpost, rtasource, xcardtype, xcardsync, geqonfdr, geqpos, dcaspill).

15. `/node ,s -action` → emulator maps `-action` to `Xdummy` (empty) — no reply. DOC lists `-action` and all `-action/*` leaves as node-addressable (DOC 4966–4996) but they are write-only triggers (`/-action/gocue ,i n`, etc.); reading them is not useful. UNCONFIRMED what a desk returns for `/node ,s -action`.

### 6.5 Which node paths exist for `-show`, `-stat`, `-prefs`, `-action`, `-libs`, `-usb`, `-urec`, `-ha`, `-insert` — CONFIRMED (DOC 4618–5010 list; emulator `Xnode[]` X32.c 588–731)

DOC's "List of accepted/known X32node parameters":

- `-insert`
- `-show`, `-show/prepos`, `-show/prepos/current`, `-show/showfile`, `-show/showfile/{inputs,mxsends,mxbuses,console,chan16,chan32,return,buses,lrmtxdca,effects}` (emulator has these under `-show/showfile/show/...`, e.g. `/-show/showfile/show/name`), `-show/showfile/cue`, `-show/showfile/cue/[000..099]` + `/numb /name /skip /scene /bit /miditype /midichan /midipara1 /midipara2`, `-show/showfile/scene`, `-show/showfile/scene/[000..099]` + `/name /notes /safes /hasdata`, `-show/showfile/snippet`, `-show/showfile/snippet/[000..099]` + `/name /eventtyp /channels /auxbuses /maingrps /hasdata`
- `-libs/ch`, `-libs/fx`, `-libs/r`, `-libs/mon`, each `/[001-100]` + `/pos /name /flags /hasdata` (emulator adds `/type`)
- `-prefs` (whole node: style bright lcdcont ledbright lamp lampon clockrate clocksource confirm_general confirm_overwrite confirm_sceneload viewrtn selfollowsbank scene_advance safe_masterlevels haflags autosel show_control clockmode hardmute dcamute invertmutes name rec_control [fastFaders]), `-prefs/ip` (+`/dhcp /addr /mask /gateway`, each addr node = 4 ints), `-prefs/remote` (+`/enable /protocol /port /ioenable`), `-prefs/card` (+13 leaves), `-prefs/rta` (+`/visibility /gain /autogain /source /pos /mode /options /det /decay /peakhold`), `-prefs/iQ/[01-16]` (+`/iQmodel /iQeqset /iQsound`), `-prefs/key`, `-prefs/key/layout`, `-prefs/key/[00..99]`
- `-usb`, `-usb/path`, `-usb/title`, `-usb/dir`, `-usb/dirpos`, `-usb/maxpos`, `-usb/dir/[000..999]`, `-usb/dir/[000..999]/name`
- `-stat` (whole node), `-stat/selidx ... -stat/geqpos` leaves, `-stat/screen` (+`/screen /mutegrp /utils`), `-stat/screen/{CHAN,METER,ROUTE,SETUP,LIB,FX,MON,USB,SCENE,ASSIGN}` (each `/page`), `-stat/aes50` (+`/state`, `/stats/[A..B]`; emulator has `/A /B /state`), `-stat/solosw`, `-stat/solosw/[01..80]`, `-stat/talk`, `-stat/talk/[A..B]`, `-stat/osc`, `-stat/osc/on`, `-stat/tape` (+`/state /file /etime /rtime`), `-stat/urec/{state,etime,rtime}`
- `-action` and leaves: `setip setclock initall initlib initshow savestate undopt doundo playtrack newscreen clearsolo setprebus setsrate setrtasrc recselect gocue goscene gosnippet selsession delsession selmarker delmarker savemarker addmarker selposition clearalert formatcard`
- `-urec/{sessionmax,markermax,sessionlen,sessionpos,markerpos,...}` (emulator Xurec: also `batterystate srate tracks sessionspan sessionoffs sd1state sd2state sd1info sd2info errormessage errorcode`, `/session/[001-100]/name`)
- `-ha/[00..39]/index` (emulator Xmisc; DOC lists `/-ha` under headamp management)

Emulator `Xnode[]` prefix table (order matters, longest first): `conf main -pre -sta ch/01..ch/32 ch auxin/01..08 auxin fxrtn/01..08 fxrtn fx/1..8 fx bus/01..16 bus mtx/01..06 mtx dca outputs/main/01 outputs/main outputs headamp/000..127 -ha -usb undo -action -show/showfile/snippet -show/showfile/scene -show -urec -libs/fx -libs/r -libs`.

### 6.6 `/` — node-style write — CONFIRMED (DOC 4560–4593; README; EMU `function_slash`)

Format: address `/` (padded `/~~~`), typetag `,s~~`, one string `"<path> <v1> <v2> ..."`. Leading `/` in the path optional. Partial argument lists allowed (only listed values change, in node order). Values are engineering units / enum names / quoted strings exactly as in node replies:

```
/~~~,s~~ch/01/mix/fader 3~~            (fader to +3 dB; DOC 709 — VERIFIED-CORRECTED line ref, was 693)
/~~~,s~~/ch/01/mix/fader -20.5~~
/~~~,s~~/ch/02/mix/pan 50~~~           (pan right 50%)
/~~~,s~~ch/01 newname 10 CY 1          (DOC 4585: sets /ch/01/config name, icon, color, source)
/ ,s 'ch/01/config "" 1 YE 1'          (README: erase name, icon 1, yellow, source IN01)
/~~~,s~~-prefs/iQ/01 none Linear 0~~
```
The desk **echoes the `/` command back** to the sender (DOC 4591–4593: "enabling a better control of the flow of data and helping avoid overruns by ensuring an application does read the UDP buffer before sending the next command") — use that echo as flow control. Emulator: `memcpy(s_buf, w_buf, w_len); return(S_SND)` (X32.c 3769–3771, VERIFIED) echoes the whole received datagram; leaf changes are separately pushed to xremote clients (`XslashSetString` X32.c 3184–3227 VERIFIED: `Xfprint(...); Xsend(S_REM)` only when the value changed).

Strings in `/`: quoted with `"` if they contain spaces (`XslashSetString`, X32.c 3184–3227: reads to the closing `"`, otherwise to the next space/newline); `""` = empty. Values out of the known step set are snapped: DOC 4566–4568 "sending `/ ,s "ch/01/mix/fader -85.4"` will be kept as -85.3".

---

## 7. Subscriptions: `/subscribe`, `/formatsubscribe`, `/batchsubscribe`, `/renew`, `/unsubscribe`

All CONFIRMED from DOC chapter "Subscribing to X32/M32 Updates" (lines 4298–4560) with real-desk captures — VERIFIED 2026-09-19 against the DOC text dump (4309–4311 lifetime, 4319–4324 tf table, 4326–4345 formatsubscribe, 4362–4368 blob layout, 4426–4430 batchsubscribe, 4466–4469 renew, 4518–4520 unsubscribe, 4547 32-byte strings, footnote 53 at 4409); the emulator does **not** implement them (`function_renew` returns `S_SND` with an empty buffer = no-op, X32.c 4954–4959; `/bat`/`/for`/`/sub` have no `Xheader` entry and are dropped — only the verbose-echo switches `-b -f -r` exist).

Common lifetime: **10 s**. DOC 4309–4311: "If not renewed within 10 seconds, the /subscribe, /formatsubscribe, /batchsubscribe commands names and attributes are forgotten and lost. Indeed, an attempt to renew one of the above commands received past the 10s delay will have no effect."

Time factor `tf` (int, last argument everywhere): interval = 50 ms × tf; updates over 10 s: `tf 0/1 → 200`, `2 → 100`, `40 → 5`, `80..99 → 3`; values outside 0..99 count as 0 (DOC 4319–4324). (For `/meters` the DOC says <2 or >99 → 200 updates.)

### 7.1 `/subscribe` — single address

`/subscribe ,s <address>` or `/subscribe ,si <address> <tf>`.
```
/subscribe ,s /-stat/solosw/01
/subscribe ,si /-stat/solosw/01 1      → ~200 updates over 10 s
/subscribe ,si /-stat/solosw/01 50     → ~4 updates
/subscribe ,si /ch/01/mix/on 2         → captured replies: /ch/01/mix/on~~~,i~~[1]  every ~100 ms
```
Replies use the normal parameter format (address + `,i`/`,f`/`,s`). Renew with `/renew ,s <address>` (the "name of the actual command").

### 7.2 `/formatsubscribe` — alias + one or more addresses with wildcards → blob

`/formatsubscribe ,ss[s...]iii <alias> <address> [<address>...] <i0> <i1> <tf>`
- `<alias>`: string used as the OSC address of the replies; **start it with `/`** (DOC footnote 53).
- `<address>`: parameter addresses; digit ranges replaced by `*` (`/dca/*/on`, `/ch/**/mix/on`).
- `<i0>`,`<i1>`: start/end of the wildcard range (inclusive). Use `0 0` when no wildcard.
- `<tf>`: time factor.

```
/formatsubscribe ,ssiii /testme /ch/**/mix/on 6 9 80
X->, 36 B: 2f746573746d65002c620000000000141400000001000000010000000100000001000000
            / t e s t m e ~ , b ~ ~ [00000014] [14000000] [01000000] [01000000] [01000000] [01000000]
```
Reply = `<alias>` padded + `,b~~` + **big-endian** blob length (20) + blob. Inside the blob (DOC 4362–4368): first int32 **little-endian** = blob length in bytes (20 → (20/4)−1 = 4 values follow), then the values, **little-endian int32** (or float32 for float parameters, e.g. `/AA` capture with `/ch/01/mix/fader` shows `12 chrs: ... ?` = 4-byte header + int + float). Strings inside these blobs are **32-byte fixed length, NUL padded** (DOC 4547).

Multi-address example: `/formatsubscribe ,sssiii /www /config/buslink/1-2 /ch/**/mix/on 10 12 20` → `/www~~~~,b~~` 20-byte blob (1 + 3 values), one every ~1 s; meanwhile `/xremote` pushes (`/config/buslink/1-2~,i~~[0]`, `/ch/09/mix/on~~~,i~~[1]`) interleave (DOC 4383–4420).

### 7.3 `/batchsubscribe` — alias + one meter id → blob

`/batchsubscribe ,ssiii <alias> <meterid> <arg0> <arg1> <tf>` — "a command to display meter data only [TBV]" (DOC 4426). `<arg0> <arg1>` are the meter's own arguments (0 0 when the meter takes none; for `/meters/5` they are `chn_meter_id grp_meter_id`; for `/meters/6` the channel id, then 0).

```
/batchsubscribe ,ssiii /yy /meters/6 0 0 40
X->, 32 B: /yy~,b~~4 flts: 000.00 001.00 001.00 000.00      (every ~2 s, for 10 s)

/batchsubscribe ,ssiii /rr /meters/5 3 1 40
->X, 52 B: /batchsubscribe~,ssiii~~/rr~/meters/5~~~[     3][  1][ 40]
X->, 124 B: /rr~,b~~27 flts: ...

/batchsubscribe ,ssiii /x_meters_0 /meters/0 0 69 1     → blob of 70 floats ~every 50 ms
/batchsubscribe ,ssiii /x_meters_8 /meters/8 0 5 1      → 6 floats
/batchsubscribe ,ssiii /mfm_a /mix/on 0 63 8            → 276-byte blob (DOC 439–441; non-meter address also accepted here)
```
Blob layout is the `/meters` layout (§8): `,b~~` + BE length + LE count + LE native floats. 124 B for 27 floats = 4 (`/rr~`) + 4 (`,b~~`) + 4 (BE len=112) + 4 (LE count=27) + 108. VERIFIED arithmetic: `/yy` 32 B = 4+4+4+4+16; `/testme` 36 B = 8+4+4+20; `/AA` 24 B = 4+4+4+12 (LE len 12, int, float).

### 7.4 `/renew`

`/renew~~,s~~<name>~` where `<name>` is the address given to `/subscribe`, or the alias of a `/formatsubscribe`/`/batchsubscribe`. `/renew~~` with **no argument renews all** active subscriptions (DOC 4466–4469). Must arrive within the 10 s window. Captured examples (DOC 446–448): `/renew~~,s~~meters/5~~~~`, `/renew~~,s~~hidden/states~~~`. (Second one is what X32-Edit sends; "hidden/states" is an alias name.)

### 7.5 `/unsubscribe`

`/unsubscribe ,s <name-or-alias>` stops one; **`/unsubscribe` with no argument stops all** active subscriptions (DOC 4518–4520). `X32ReaperW.c` sends the bare 16-byte `/unsubscribe~~~~` at sign-off. Emulator treats it as "forget this xremote client".

---

## 8. `/meters` request and blob framing — CONFIRMED (DOC 783–1000; EMU `Xprepmeter`; CMD Automix). VERIFIED: X32.c 4819–4862 (`Xprepmeter`), X32Automix.c 140 (`Meters[]`), 436–438 (`Meters[39] = (char)(X32i_delay / 50)`), 630 (`ff = (float *)(r_buf + 24 + 4*ch)`), Xdump.c 85–115, DOC 780–970.

Request: `/meters ,s <meterid>` / `/meters ,si <meterid> <tf>` / `/meters ,sii <meterid> <a0> <a1>` / `/meters ,siii <meterid> <a0> <a1> <tf>`. Lifetime 10 s; interval 50 ms × tf.

```
/meters~,si~/meters/6~~~[16]       2f6d6574657273002c7369002f6d65746572732f3600000000000010   (channel 17 strip)
X32Automix.c: char Meters[] = "/meters\0,siii\0\0\0/meters/1\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0";  sent as 40 bytes,
              Meters[39] = (char)(X32i_delay / 50);   // tf in the last byte of the last BE int
```

Reply blob layout (DOC 812–834, verbatim):

> `<meter id> ,b~~<int1><int2><nativefloat>...<nativefloat>`
> `<int1>`: the length of the blob in bytes, 32 bits **big-endian** coded
> `<int2>`: the number of `<nativefloats>`, 32 bits **little-endian** coded
> `<nativefloat>`: data or meter value(s), 32 bits floats, **little-endian** coded

Capture (DOC 831): `2f6d65746572732f360000002c6200000000001404000000fd1d2137fdff7f3f0000803f6ebbd534` = `/meters/6~~~` `,b~~` `[00000014]`(BE 20) `[04000000]`(LE 4) then 4 LE floats. Note `<int1>` counts the count-int plus the floats: 20 = 4 + 4×4.

Emulator `Xprepmeter` (X32.c 4819–4862) reproduces this exactly: `endian.ii = (l + 1) * 4` stored big-endian at bytes 16–19, `l` stored little-endian at 20–23, message length `= endian.ii + 20`; interval `XDeltaMeters[i] = 50000 * tf` µs, active for 10 s, sent only to the requesting client. `X32Automix.c` reads channel levels as `ff = (float *)(r_buf + 24 + 4*ch)` (native little-endian on x86).

Sizes (floats → total bytes = 12 or 16 header + 8 + 4n): `/meters/0` 70 (304 B), `/1` 96 (408 B — VERIFIED-CORRECTED: `/meters/1~~~`12 + `,b~~`4 + 4 + 4 + 384 = 408, not 412), `/2` 49, `/3` 22, `/4` 82, `/5` 27, `/6` 4, `/7` 16, `/8` 6, `/9` 32, `/10` 32, `/11` 5, `/12` 4, `/13` 48, `/14` 80, `/15` 50 (RTA: 100 LE int16, dB = v/256, 0 = clip), `/16` 48 (44 words of 2× LE int16 /32767 = gate/comp gains, last 4 words = 8 automix gains as `log2(v)*256`). `Xdump.c` decodes `/meters/15` and `/16` exactly this way (VERIFIED: Xdump.c 85–115 — `/15`: `n = i1*2` shorts, each `/256.0`; `/16`: first `n-8` shorts `/32767.0` labelled G(0–31) C(32–63) B(64–79) M(80–85) LR(86) MC(87), last 8 shorts `/256.0` — Xdump does not undo the `log2`). Float counts VERIFIED against DOC 846–968.

---

## 9. Quirks checklist

1. Reply address for `/node` is `node` (no slash); for `/showdump` also `node`. Everything else echoes the request address.
2. Strings with spaces in node replies are `"..."`-quoted; empty = `""`; some absent strings render as `" "`. Split on whitespace runs, honour quotes. No escaping of embedded quotes (UNCONFIRMED).
3. Numbers: `-oo`, signed `%+.1f` levels, `k` decimal marker for kHz (`1k02`, also `11k9`), `%`-prefixed fixed-width binary bitmaps, integer-vs-float only by position. Real desk pads columns with extra spaces (levels right-aligned in a 5-char field, hold/release width 4, HPF width 3 — see §6.3) and can emit `-0.0`.
4. Enums are **names** in node text but **`,i` indices** in parameter GET replies; SET accepts either `,i` or `,s`.
5. Floats on parameter addresses are always 0.0..1.0; engineering values only exist in node text and in `/` writes.
6. Node addresses reject `,s` multi-tag SETs (X32-Edit reserved); use `/ ,s` or leaf addresses.
7. No error replies, no acks for SET/`/xremote`/`/renew`; silence = ignored or lost.
8. The sender of a SET does not receive its own xremote echo; other clients do.
9. String length: 12 chars for channel/bus/mtx/dca names per DOC; `/-prefs/name` ≤ 16–17 visible; emulator local buffers are 64 bytes (`loc_str[64]`) and `BSIZE 512` — keep any datagram ≤ 512 bytes when talking to the emulator.
10. No `%`-encoding anywhere; `%` in node text always introduces a bitmap.
11. `/xinfo` (not `/info`) carries the console name and IP; `/info` slot 2 is the constant `osc-server`.
12. FX param count/type depends on the FX type; `/fx/n/par/NN` GET replies `,i` or `,f` per type (`FXc_lookup`/`Sflookup[]` tables in X32Fx.h).

## 10. Rate limits, packet sizes

- DOC never states a hard message-rate limit. Evidence of safe rates: `X32SetScene.c` default `Xdelay = 1` ms between commands; `X32_Command.c` default `-t 10` ms between batch lines; `X32ReaperW.c` uses a configurable `Xdelayg`; DOC recommends using the `/` echo as flow control (4591–4593). Meter cadence is fixed at 50 ms × tf; the desk itself says it "may be variable according to console's ability to fulfill requests" (DOC 786–787).
- Overrun risk is on the **receive** side: `/showdump` and bulk `/node` bursts over 54 Mb/s Wi-Fi drop datagrams (DOC 2900–2907, 269–274). Use a large socket receive buffer and drain it continuously.
- Observed datagram sizes: requests ≤ ~60 B; replies: parameter 20–32 B, `/info` 48 B, `/status` 52 B, node lines typically 40–200 B (an `fx/n/par` node with 64 tokens can approach 400 B), `/meters/1` 408 B, `/batchsubscribe ,ssiii /x_meters_0 /meters/0 0 69 1` reply = 12 (`/x_meters_0~`) + 4 + 4 + 4 + 280 = 304 B (VERIFIED-CORRECTED: the alias is 11 chars -> 12 bytes; the previous draft assumed a 4-byte alias and wrote 296). Emulator hard limit `BSIZE 512`. Practical assumption: **desk datagrams stay under ~512 B**; UNCONFIRMED theoretical maximum (a 100-line `/showdump` is many datagrams, one node per datagram).
- UDP payload MTU is not an issue at these sizes; no fragmentation observed.

## 11. The X32 emulator (`X32.c`)

- What it is: README "X32 is... an X32 emulator. This tool parses and manages X32 commands (as a real X32 would), keeps up to 4 xremote clients updated ... not all X32 commands are supported". Header version `v0.88`, `#define XVERSION "4.06"` (reports FW 4.06), `V2.07` in `/info`.
- Build (root `Makefile`, by Javier Iglesias): `make X32lib` builds `X32lib/libX32.a` from `X32lib/*.c` (`ar rcs`); then `make X32` = `gcc -O0 -g3 -Wall -IX32lib -o build/X32 X32.c -LX32lib -lm -lX32`. `make all` builds `X32lib X32Reaper X32_Command X32Wav_Xlive X32Xlive_Wav X32`; `make lots` adds `GetSceneName X32DeskRestore X32DeskSave X32GEQ2cpy X32GetScene X32Replay X32SetScene X32Tap X32USB XAirGetScene XAirSetScene XAir_Command`. VERIFIED: root `Makefile` read in full (`CFLAGS=-O0 -g3 -Wall -IX32lib`, `LIBS := m X32`, output in `build/`). On Windows Maillot uses MinGW-32 + Eclipse; link `-lws2_32` for Winsock (`#ifdef __WIN32__` paths use `WSAStartup`). Linux note in `getmyIP()`: the interface name is hard-coded `"en0"` (comment: replace by `eth0`/`wlan0`), so pass `-i <ip>` on Linux.
- Run: `X32 [-i <IP address>] [-d 0/1] [-v 0/1] [-x 0/1 /xremote echo] [-b 0/1 /batchsubscribe echo] [-f 0/1 /formatsubscribe echo] [-r 0/1 /renew echo] [-m 0/1 /meters echo]`. Binds UDP 10023 on the chosen IP. State file `.X32res.rc` in the CWD is written by the **non-Behringer** command `/shutdown` (12 bytes `/shutdown~~~`) or `/-stat/lock ,i 2`, and read at start (`X32Init`, X32.c 5539–5543). VERIFIED: X32.c 29, 491, 916, 980, 1116–1117 (`en0`), 4586, 5371, 5400, 5543. README: run once, send `/shutdown` from a client to create it.
- Supported: `/info /xinfo /status /xremote` (4 clients, 11 s), `/node` for all tables listed in §6.5 (multi-value and single-leaf), `/` node-style writes with value snapping (`XslashSet*` at 3140–3387), GET/SET for ~10 k addresses (`/config /main /-prefs /-stat /-urec /ch /auxin /fxrtn /bus /mtx /dca /fx /outputs /headamp /-ha /insert /-show /-usb /-action /-libs(no-op)`), `/copy` (libchan only), `/add /load /save /delete` (partial), `/showdump` (emits a single `/-show/showfile/show` node line), `/meters/0..16` (fake constant blobs with correct framing, 50 ms × tf, 10 s), `/unsubscribe` (drops xremote client). Not supported: `/subscribe`, `/formatsubscribe`, `/batchsubscribe` (silently dropped), `/renew` (no-op), MIDI, audio, bundles.
- Emulator-vs-desk differences to remember when testing: `/info`/`/status` slot 2 is the prefs name not `osc-server`; single-leaf `/node` float prints raw `%f`; node numbers have single-space separators; `PRE`/`POST` glued in `-prefs/rta`; 64-byte string buffers; ignores enum tokens it doesn't know.
- Binaries (SITE, verbatim links): Windows `https://drive.google.com/file/d/1kxs9ZanmZfrubBQ2rGJ3xVN7UKZ2ObT0/view?usp=sharing`; macOS `https://drive.google.com/file/d/1-3h2gqjbpyQAsQS03_vVorETw6plrdG7/view?usp=drive_link`; X32_Command `https://drive.google.com/file/d/1L5dPMqjwh-dhBTrFR1UX3wYjITaP8uHl/view?usp=sharing` (mirror `https://x32ram.com/download/x32_command/`). Site blurb (verbatim, VERIFIED 2026-09-19): "Note [been asked this several times]: X32 emulator only manages OSC commands, no MIDI, no Audio, no USB." All four Drive links above were re-checked against the live page on 2026-09-19 (doc "updated June 29, 2021"; macOS build credited to Rouven Schandl).

## 12. Minimal client recipe (derived)

1. One UDP socket, bind any port, `SO_RCVBUF` large. Discover: send `/xinfo~~` to `<subnet>.255` (SO_BROADCAST), parse first `,ssss` string as desk IP. Or send `/info~~~` unicast and expect `/info` within 100 ms.
2. Read: send padded address; wait ≤100 ms for a datagram whose address equals the request; parse by typetag. Write: address + `,i`/`,f`/`,s` + BE value; no ack.
3. Bulk read: `/node ,s <path>`; reply address `node`, strip 12 bytes, take string, drop trailing `\n`, tokenise with quote-awareness and `k`-notation.
4. Live updates: send `/xremote~~~~` every 8–9 s on the same socket; pushes arrive as normal parameter messages from the desk IP:10023. Use a second socket if you need to see your own writes echoed.
5. Meters: `/meters ,si /meters/N tf`, renew every ≤9 s; parse `,b` blob: BE len, LE count, LE floats.
6. Never send OSC bundles; keep one message per datagram; pace bulk writes ~1–10 ms apart and drain the receive buffer between bursts.

---

## Verification log (skeptical re-derivation, 2026-09-19)

Method: re-fetched `X32.c` (5661 lines, v0.88), `X32Fx.h`, `X32Bus.h`, `X32CfgMain.h`, `X32Show.h`, `X32PrefStat.h`, `X32Channel.h`, `X32lib/Xsprint.c`, `X32lib/X32Connect.c`, `X32lib/Xdump.c`, `X32_Command.c` (v1.46), `X32GetScene.c`, `X32SetScene.c`, `SetSceneParse.c`, `X32Automix.c`, `X32ReaperW.c`, `X32Replay.c`, `X32Fade.c`, `X32Tap.c`, `X32TCP.c`, `X32SsaverGW.c`, `X32Midi2OSC.c`, `README.md`, root `Makefile` from `raw.githubusercontent.com/pmaillot/X32-Behringer/master/`; re-extracted the DOC PDF (`tostibroeders.nl` mirror) with `pdftotext -layout` (9610 lines); re-fetched Maillot's Google site; fetched a console-saved X32 scene file (`cabcookie/saddleback-x32-general-scene`, `Soundboard Setup.scn`, header `#2.7#`). Every numeric value, formula, enum list, byte layout and line reference in this file was checked against those; the `f201`/`f101` index examples and the `Slevel`/`Slogf` worked examples were recomputed by script.

Corrections made (each is also marked inline with `VERIFIED-CORRECTED`):

1. §6.4 item 4 — `f201[]` examples were wrong: f=0.25 → index 50 → `112.5` (not `120.5`); f=0.7 → index 140 → `2k51` (not `4k37`, which is index 146).
2. §6.4 item 14 — `/-stat` example token 11 (`lock`) is `OFF`/`ON` in emulator output (X32.c 4177), not `0`.
3. §6.4 item 9 — emulator `MSMX` case reads pan from `command[i + 4]` (the `/main/st/mix/01` header) instead of `i + 3`; the emulator prints `-100` for main pan. Desk format `on fader pan` confirmed from a real scene file.
4. §6.4 item 6 — `Xgmode[]` is the 5-entry list `EXP2 EXP3 EXP GATE DUCK` (index 2 misnamed), not a 6-entry list.
5. §8 — `/meters/1` reply is 408 B, not 412 B; §10 — `/batchsubscribe /x_meters_0 /meters/0` reply is 304 B, not 296 B (12-byte alias).
6. §3.1 — the claim that `/info` slot 1 "stayed V2.05 across FW 2.x–4.x" was unsupported; DOC captures are FW 2.08–2.12 only, and the emulator emulating FW 4.06 answers `V2.07`. Now marked UNCONFIRMED with that evidence.
7. §2 — `MAX_CLIENTS 4` is X32.c line 79 (not 80); §1.3 — `Xheader[]` begins with `"/shu"` (omitted in the list); §6.6 — the `/ ,s ch/01/mix/fader 3` example is DOC line 709 (not 693); minor line-number drifts fixed (3050–3059, 3076–3087, 1156–1191, 4070–4073, 4035–4037, 4030–4034, 3804–3806, 4430–4434).
8. §1.1 — clarified that the DOC's appendix hex for 0.7/0.8/0.9 is 2 ULP above the IEEE-754 encodings (never compare desk floats bit-exactly).
9. §6.3 — added emulator table slips that glue tokens (`Prtavis` 45%/50%/65%/70%/75%/80%, `Prtaph` 5/6/8, `Pcmado` 1-32, `XRtaea` AN17-24).

Unconfirmed items resolved or narrowed:

- **Real-desk column padding** — RESOLVED from a console-saved scene file: level fields right-aligned in a 5-char field (`ON  +2.1`, `OFF   -oo`, `OFF -81.0`), hold/release width 4 with adaptive precision (`0.03`, ` 538`), HPF integer width 3 (`24  79`), EQ/pan unpadded, `-0.0` possible, `11k9` one-digit kHz fraction, mgain two decimals (`8.00`). FW 2.x file; widths on FW 4.x not re-checked.
- **Bus send `type` token** — RESOLVED: real desk emits `POST` etc. for `/bus/01/mix/01` (emulator `OffOn` wiring confirmed as a table slip).
- **`/ch/xx/mix/NN` token count** — NEW: firmware-dependent (4 tokens on FW 2.x, 5 with `panFollow` on FW >= 3 per DOC 1486–1494).
- **`/info` server_version** — narrowed: `V2.05` on FW 2.08–2.12; likely `V2.07` on FW 4.x (emulator + M32 changelog); exact string UNCONFIRMED.
- **Bundles** — DOC confirmed silent (no occurrence of "bundle" in 9610 lines); emulator drops them; desk behaviour still UNCONFIRMED.
- **`-show/prepos` parent, `-action` node reads, `/xinfo` on 255.255.255.255, bare `/unsubscribe` vs `/xremote` on a desk, `"` inside names, >12-char name truncation, absolute latency, hard UDP size limit, single-leaf `/node` float format on a desk, whether a desk ever omits the type-tag string** — still UNCONFIRMED; nothing in the C sources, DOC, README or the scene file settles them.

Facts confirmed verbatim (marked inline `VERIFIED`): `Xsprint`/`Xfprint` encoder; empty/missing type-tag handling (X32.c 2911–2913, DOC 559–571); `Xheader[]` dispatch; UDP 10023 and reply-to-source-port (`Xsend`, X32Connect.c); `/info`, `/xinfo`, `/status` reply layouts and byte counts (DOC 483–491, 575–635; X32.c 3050–3087); `/xremote` 12-byte form, 10 s desk / 11 s emulator lifetime, 4-client cap, sender-excluded pushes, 9 s renewal in all Maillot clients; `/xinfo` /24 broadcast discovery (X32_Command.c 325–376, 5 tries x 500 ms, `r_buf+16`); GET/SET emulator paths; `/node` request/reply envelope (`node` without slash, `,s`, trailing `\n`, X32.c 3792, 3804–3806, 4430–4434); `Slevel`/`Slinf`/`Slinfs`/`Slogf`/`Sbitmp`/`Sint` (X32.c 1237–1302); all enum tables (X32.c 314–441); `f201`/`f121`/`f101` tables (X32Fx.h 107–160); `CHCO/CHPR/CHGA/CHDY/CHEQ/CHMX/CHMO/CHME/MSMX/FXTYP1/FXSRC/HAMP/PRTA/STAT` case formats; `function_node_single` (raw `%f`, unquoted string, minimal-width `%` bitmap); `/` echo and `XslashSetString`; the whole subscribe family per DOC 4298–4560 incl. blob layouts and 32-byte strings; `/meters` framing (`Xprepmeter`, DOC 812–834) and per-meter float counts (DOC 846–968); `Xdump` decoding of `/meters/15`,`/16`; emulator build/run/state-file details; site download links and emulator blurb.
