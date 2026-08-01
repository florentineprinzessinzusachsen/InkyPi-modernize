"""
/rain/bin request + response - the main forecast-fetch endpoint.

Reconstructed from p116r2/AbstractC1646s.java (request builder, "m3436a")
and p122s2/C1758g.java (response parser, "m3717b"/"m3718c"), both read in
full during the assessment.

Endpoint: POST https://<host>/rain/bin
Host list [VERIFIED, AbstractC1646s.java:41] - failover, app tries the next
one on UnknownHostException:
    regenonline.de, rainforecast.de, regen.online, regenvorschau.de

No authentication of any kind is sent (see the assessment report). The only
"protection" is the string/byte obfuscation in protocol.py, which is fully
reversible and adds no real confidentiality (TLS already provides that).
"""

from __future__ import annotations

import bz2
import struct
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .protocol import TLVWriter, TLVReader, encode_range, decode_range

REQUEST_MAGIC = bytes([28, 9, 82, 1])  # [VERIFIED] AbstractC1646s.java:350-353


def build_forecast_request(
    lat: float,
    lon: float,
    *,
    device_uuid: Optional[str] = None,
    app_version: str = "2.0.20",
    android_release: str = "14",
    device_manufacturer_model: str = "Google - Pixel 8",
    is_first_request_today: bool = True,
    request_source_type: int = 1,        # [INFERRED] 1=main app screen, 2=widget (AbstractServiceC0710a passes 2).
                                          # Empirically tag 3/4/5 (probabilities/intensities/map image) come back
                                          # with source=1 + include_field3/4 - source=2 (widget) is the leading
                                          # hypothesis for what unlocks tag 14 (temperature); UNCONFIRMED, try it.
    ads_ab_flag: bool = False,           # prefs "ab" boolean, purpose unclear [UNCERTAIN]
    minutes_since_last_fetch: int = 0,
    minutes_since_install: int = 0,
    include_field3: bool = True,         # [UNCERTAIN exact meaning, VERIFIED effect] "extended forecast?" - z4 in
                                          # the app. Empirically required (together with include_field4) to get
                                          # probabilities/intensities/map image back - a bare request without
                                          # these returns only a short status stub. Default True as of this
                                          # revision (was False, matching the plain main-screen call site, which
                                          # turned out to be the wrong thing to default to for a useful PoC).
    include_field4: bool = True,         # see include_field3 - same empirical finding, default True
    top_level_flag: bool = False,        # [UNCERTAIN] z6/tag 11 - true only for the "location name lookup" call path
    wind_query: Optional[tuple] = None,  # (wind_level, wind_area_id) -> optional field 10 [VERIFIED shape AND
                                          # semantics, corrected from an earlier wrong guess ("tile_x/tile_y") -
                                          # traced through C0668j1.java:369-376 + AsyncTaskC0695s1.java:82-83,130-131:
                                          #   - wind_level: preference key literally named "WindLevel"
                                          #     (AbstractC1636l0.f6281y decodes to "WindLevel"), default value is
                                          #     the STRING "1500" parsed to int on a fresh install - almost
                                          #     certainly an altitude/pressure level selector (e.g. wind at 1500m
                                          #     vs. surface), not a map tile coordinate.
                                          #   - wind_area_id: defaults to -1 on a fresh install (field2887v's
                                          #     declared default; only overridden by a 5-second-lived "last
                                          #     scroll position" cache - irrelevant for a fresh PoC request).
                                          #     Also separately used server-side as a cache-freshness token
                                          #     (AsyncTaskC0695s1.java:122, compared against response tag-11
                                          #     sub-field 3) - NOT relevant when there's no local cache, as here.
                                          # Use wind_query=(1500, -1) to match the app's own fresh-install request.
) -> bytes:
    """Port of AbstractC1646s.m3436a's request-building half (server-call
    retry/failover loop omitted - this returns one request body for one
    host attempt, matching the bytes actually placed on the wire).

    Wire order is (lat, lon). CORRECTED after a live-traffic test returned
    "Ungültige Position" (invalid position) - the original version of this
    function had it backwards. Full trace, this time verified against
    concrete call-site variable names rather than assumed from convention:
      - C1652y.java:17 constructor is C1652y(name, d6, d7); its ONLY call
        site (AbstractC1653z.m3442f) passes (name, jsonLongi, jsonLati) -
        so field f6316b=d6=LONGITUDE, field f6317c=d7=LATITUDE.
      - AbstractServiceC0710a.java:168 calls
        m1837w(ctx, i7, c1652y.f6316b, c1652y.f6317c, false) - i.e. forwards
        (longitude, latitude) positionally.
      - m1837w(service, i6, d6, d7, z4) (AbstractServiceC0710a.java:113)
        forwards d6, d7 UNCHANGED into m3436a(..., d6, d7, ...) - so in
        m3436a's own signature, d6=LONGITUDE, d7=LATITUDE.
      - Inside m3436a (AbstractC1646s.java:97-98): f6=(float)d7=LATITUDE,
        f7=(float)d6=LONGITUDE - and f6 is written to the wire BEFORE f7
        (AbstractC1646s.java:267-268) - so wire order is (lat, lon).
    """
    if device_uuid is None:
        device_uuid = str(uuid.uuid4())  # AbstractC1636l0.m3420a - persisted UUID.randomUUID()

    w = TLVWriter()
    # 4-byte header reserved now, overwritten with REQUEST_MAGIC after the
    # reverse+XOR transform below (AbstractC1646s.java:264, 350-353).
    w.raw_byte(0).raw_byte(0).raw_byte(0).raw_byte(0)

    # field 1: coordinates, wire order (lat, lon) [VERIFIED via call-site trace, see docstring]
    w.raw_float_pair(1, float(lat), float(lon))

    # field 11: top-level bool [UNCERTAIN semantics]
    w.byte_field(11, 1 if top_level_flag else 0)

    if include_field3:
        # [UNCERTAIN] subfields {1: 15, 2: 120} - possibly forecast
        # window/resolution parameters. AbstractC1646s.java:271-277.
        sub = TLVWriter()
        sub.byte_field(1, 15)
        sub.raw_byte(2).raw_byte(1).raw_byte(120)  # tag=2 len=1 value=120, hand-written
        w.submessage(3, sub)

    if include_field4:
        # [UNCERTAIN] constant flag bundle. AbstractC1646s.java:281-286.
        sub = TLVWriter()
        sub.byte_field(1, 1)
        sub.byte_field(2, 1)
        w.submessage(4, sub)

    if wind_query is not None:
        # field 10: {1: uint16 wind_level, 2: uint32 wind_area_id, 3: bool(true)}
        # [VERIFIED shape] AbstractC1646s.java:290-301, C0462k0 fields
        # f2076a (wind_level)/f2077b (wind_area_id) set in AsyncTaskC0695s1.java:129-131.
        tile_x, tile_y = wind_query
        sub = TLVWriter()
        sub.raw_byte(1).raw_byte(2).raw_byte((tile_x >> 8) & 0xFF).raw_byte(tile_x & 0xFF)
        sub.uint32_field(2, tile_y)
        sub.bool_field(3, True)
        w.submessage(10, sub)

    # field 5: device info submessage [VERIFIED shape] AbstractC1646s.java:305-317
    dev = TLVWriter()
    dev.bool_field(1, is_first_request_today)
    dev.string_ascii(2, device_uuid)
    dev.byte_field(3, 2)                     # constant
    dev.byte_field(4, 1)                     # constant
    dev.string_ascii(5, app_version)
    dev.string_ascii(6, android_release)
    dev.string_utf8(7, device_manufacturer_model)
    dev.byte_field(8, request_source_type)
    dev.bool_field(9, ads_ab_flag)
    dev.uint32_field(10, minutes_since_install)
    dev.uint32_field(11, minutes_since_last_fetch)
    w.submessage(5, dev)

    # field 6 (purchase info) and field 12 (retry-attempt number)
    # intentionally omitted here - they only appear for premium users /
    # retried requests respectively, and are not needed for a plain "fetch
    # by GPS coords" request. Field 10 is implemented above (wind_query).
    # See the assessment report for their documented layout.

    body = bytearray(w.get_bytes())
    length = len(body) - 4
    if length >= 2:
        encode_range(body, 4, length)
    body[0:4] = REQUEST_MAGIC
    return bytes(body)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

@dataclass
class ForecastResponse:
    error_message: Optional[str] = None          # tag 1  [VERIFIED shape]
    reference_time_minutes: Optional[int] = None   # tag 2  [VERIFIED - Bundle key "Rmpf"->"Time" in
                                                     # ShowForecastActivity.java:315; used mod 1440
                                                     # (minutes/day) in AbstractServiceC0711b.java:264]
    interval: Optional[int] = None                 # tag 3 sub 1 [VERIFIED - Bundle key "Grwfpqci"->"Interval"]
    intensities: Optional[list] = None              # tag 3 sub 3, int16[] [VERIFIED - Bundle key "Grwflnkqmav"->"Intensities"]
    probabilities: Optional[list] = None            # tag 3 sub 4, byte[] decoded as unsigned ints 0-255
                                                     # [VERIFIED field - Bundle key "Nvrc_]kimplct"->"Probabilities";
                                                     # INFERRED unit - observed values (e.g. 2, 1, 0) are consistent
                                                     # with a direct 0-100 percentage per byte, one per Interval-sized
                                                     # time step, same indexing as `intensities`, but no explicit
                                                     # "these are percent" label was found in the decompiled code]
    location_xy: Optional[tuple] = None            # tag 4 sub 1: (uint16,uint16), pixel position of the requested
                                                     # location in the rain-image's native pixel space [VERIFIED,
                                                     # p138u5/AbstractC1859b.java:226-229]
    location_uv: Optional[tuple] = None            # tag 4 sub 2: (float,float), wind (U,V) AT that exact point
                                                     # [VERIFIED, p138u5/AbstractC1859b.java:230-232]
    payload_blob: Optional[bytes] = None           # tag 5, XOR+reversed          [VERIFIED transform AND content:
                                                     # BitmapFactory.decodeByteArray(blob,1,len-1) in
                                                     # MapWidgetProvider.java:114 / AsyncTaskC0698u.java:103
                                                     # -> this is a standard image file (1 leading marker byte,
                                                     # then PNG/JPEG bytes). See extract_map_image() below.]
    aux_string: Optional[str] = None               # tag 7  [INFERRED - seen elsewhere as a server-supplied host]
    wind: Optional["WindGrid"] = None               # tag 11 [VERIFIED field-for-field against
                                                     # AsyncTaskC0695s1.java:150-164 + WindView.java + C1644q.java]
    location_candidates: Optional[list] = None      # tag 12 sub 1/2/3: 3x int32[] [INFERRED - candidate
                                                     # (lat*1e6, lon*1e6, id) triples for place-name search]
    temperature_c: Optional[float] = None           # tag 14 sub 1  [VERIFIED - AbstractServiceC0711b.java:273
                                                     # formats this as "%.0f °C"]
    server_time_minutes_v1: Optional[int] = None    # tag 6, uint32 [INFERRED PURELY EMPIRICALLY - C1758g.java's
                                                     # dispatch has NO case for tag 6, the app never reads this.
                                                     # Decoded as minutes-since-2010-01-01, two captures ~15 min
                                                     # apart showed an exact +15 delta landing precisely on both
                                                     # capture times - see server_time_minutes_v1_datetime().
    server_time_ms: Optional[int] = None            # tag 13, uint64 [INFERRED PURELY EMPIRICALLY - also has no
                                                     # dispatch case. Decoded as milliseconds-since-Unix-epoch,
                                                     # lands within ~7 minutes of the tag-6-derived time on the
                                                     # same captures - see server_time_ms_datetime(). Most likely
                                                     # both are just response-generation timestamps at different
                                                     # precision/epoch, unused by the current app version - not
                                                     # part of the weather data, not another protection layer.
    raw_top_level_fields: dict = field(default_factory=dict)  # anything else, tag -> raw bytes

    def server_time_minutes_v1_datetime(self):
        import datetime
        if self.server_time_minutes_v1 is None:
            return None
        return datetime.datetime(2010, 1, 1) + datetime.timedelta(minutes=self.server_time_minutes_v1)

    def server_time_ms_datetime(self):
        import datetime
        if self.server_time_ms is None:
            return None
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=self.server_time_ms)


@dataclass
class WindGrid:
    """Port of the C1757f -> WindsManager$WindData mapping, verified
    field-for-field against AsyncTaskC0695s1.java:150-164. Wind components
    are SIGNED bytes / scale (can be negative - u/v vector components);
    gust is an UNSIGNED byte / scale (magnitude only) - see WindView.java's
    differing use of the raw byte (`bArr[i]` vs `bArr[i] & 255`) confirming
    which is signed vs unsigned.

    CORRECTED: u_component/v_component/gust are BZip2-COMPRESSED on the
    wire, not raw per-cell bytes. Missed on first pass - the response field
    was structurally correct (right tag, right sub-tags) but the byte
    payload itself needs decompressing before use. Found by
    WindsManager$WindData.m1770c(int, byte[]) (called on every one of these
    three fields before use, in AsyncTaskC0695s1.java:160-164), which wraps
    the bytes in p112q5/C1599b - an InputStream that checks for the literal
    'B','Z','h' magic bytes and implements the full BZip2 block-decoding
    algorithm (Huffman + MTF + BWT inverse + RLE), i.e. exactly BZip2.
    Standard format - decompressed here with Python's stdlib `bz2`, no need
    to port the custom decompressor. `parse_forecast_response()` now
    decompresses these fields at parse time, so `u_component`/`v_component`/
    `gust` on this dataclass are already the plain per-cell bytes;
    `coverage()` should read 100% for a successfully decompressed grid."""
    width: int
    height: int
    cell_scale: int
    u_component: bytes    # signed bytes (post-decompression); value(i) = int8(u_component[i]) / wind_scale, m/s
    v_component: bytes    # signed bytes (post-decompression); value(i) = int8(v_component[i]) / wind_scale, m/s
    wind_scale: float
    gust: bytes            # unsigned bytes (post-decompression); value(i) = (gust[i] & 0xFF) / gust_scale, m/s
    gust_scale: float

    def coverage(self) -> tuple:
        """(cells_declared, cells_actually_present). Should be 100% after
        decompression (m1770c enforces exactly width*height bytes or
        returns empty) - a value less than that most likely means
        decompression failed (corrupt/truncated capture, or a compression
        variant this stdlib call doesn't handle) rather than genuine sparse
        geographic coverage."""
        return self.width * self.height, min(len(self.u_component), len(self.v_component))

    @staticmethod
    def decompress_field(raw: bytes, expected_len: int) -> bytes:
        """Port of WindsManager$WindData.m1770c: BZip2-decompress, and
        require EXACTLY expected_len bytes out (the app throws EOFException
        and discards the field on any mismatch - reproduced here as
        returning b"" on any failure, same as the app's catch-all)."""
        try:
            out = bz2.decompress(raw)
        except Exception:
            return b""
        if len(out) != expected_len:
            return b""
        return out

    def nearest_populated_cell(self, x: int, y: int):
        """Nearest actually-covered cell to (x, y), by real 2D grid distance
        (not by position in the flat byte array). Use this when the grid is
        sparse and the exact target cell has no data. Returns None if the
        arrays are empty. Ties broken by scan order (smaller y, then x)."""
        n = min(len(self.u_component), len(self.v_component))
        if n == 0 or self.width == 0:
            return None
        best = None
        best_dist2 = None
        for i in range(n):
            cx, cy = i % self.width, i // self.width
            dist2 = (cx - x) ** 2 + (cy - y) ** 2
            if best_dist2 is None or dist2 < best_dist2:
                best, best_dist2 = (cx, cy), dist2
        return best

    def cell_for_coords(self, lat: float, lon: float) -> tuple:
        """Direct lat/lon -> (grid_x, grid_y), derived from WindView.java's
        own math (not guessed): WindView.m1769c() projects (lon, lat) to a
        screen pixel position (to draw a "wind at your location" label)
        using a fixed geographic projection (piecewise-linear per longitude
        band - these are the app's actual calibrated projection
        coefficients, not something reverse engineered by trial and error).
        WindView.m1766d() separately converts a screen pixel position back
        to a grid cell, using the current view's pan/zoom state (scale,
        offset). Substituting one into the other, the view-dependent
        scale/offset terms cancel out algebraically, leaving a direct
        geo->grid formula with no dependency on any client-side view state:

            jRound2 = round(20.282986936*lat + (-2.020678158*lat + 176.386305317)*lon - 1325.222361859)
            jRound  = round(A*lat + B), where (A, B) depend on which longitude band `lon` falls in
            grid_x  = round(jRound2 / 1.125 / cell_scale)
            grid_y  = round((jRound - 6) / 1.125 / cell_scale)

        This is an approximation: the real app computes this via two
        separate integer-rounding steps (geo->screen pixel, then screen
        pixel->grid cell), which can introduce +/-1 cell of rounding
        difference versus this single combined computation. Close enough to
        identify "the cell for this location", not guaranteed bit-identical
        to what WindView would compute on an actual device.
        """
        jround2 = round(20.282986936 * lat + (-2.020678158 * lat + 176.386305317) * lon - 1325.222361859)
        bands = [
            (7.587776, -116.369520756, 6492.217861582),
            (8.495436, -115.636075636, 6459.810519731),
            (9.550306, -116.809213873, 6521.296023812),
            (10.765572, -115.742380411, 6465.794788426),
            (11.33214, -116.505916582, 6506.531200921),
            (12.230873, -116.779134481, 6519.489833334),
            (13.23668, -116.970916273, 6525.900441581),
        ]
        a, b = -115.698098051, 6455.358785977  # else branch (lon >= 13.23668)
        for threshold, ba, bb in bands:
            if lon < threshold:
                a, b = ba, bb
                break
        jround = round(a * lat + b)
        grid_x = round(jround2 / 1.125 / self.cell_scale)
        grid_y = round((jround - 6) / 1.125 / self.cell_scale)
        return grid_x, grid_y

    def wind_at(self, x: int, y: int):
        """Returns (speed_m_s, direction_deg_from_north, gust_m_s) for grid
        cell (x,y), replicating WindView.m1766d's math verbatim - INCLUDING
        its own bounds check (WindView.java:146: "i11 >= c1644q.f6294a.length"),
        which is the app's own explicit acknowledgement that the U/V arrays
        can be shorter than width*height (a partial/sparse grid, not a bug
        or a missed compression step - the real app just silently skips
        cells outside the array). Returns None for such an out-of-coverage
        cell, exactly like the app's WindView returns an empty label."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return None
        i = y * self.width + x
        if i >= len(self.u_component) or i >= len(self.v_component):
            return None
        u = struct.unpack("b", self.u_component[i:i + 1])[0] / self.wind_scale
        v = struct.unpack("b", self.v_component[i:i + 1])[0] / self.wind_scale
        speed = (u * u + v * v) ** 0.5
        gust = (self.gust[i] & 0xFF) / self.gust_scale if i < len(self.gust) else 0.0
        import math
        degrees = math.degrees(math.acos(v / speed)) if speed > 1e-5 else 0.0
        if u >= 0.0:
            degrees = 360.0 - degrees
        return speed, degrees, max(gust, speed)


def extract_map_image(payload_blob: bytes) -> bytes:
    """Port of MapWidgetProvider.java:105-114 / AsyncTaskC0698u.java:94-103:
    the already-decoded tag-5 payload has a 1-byte marker at index 0 that the
    app skips before handing the rest straight to BitmapFactory. Returns the
    raw image bytes (write directly to a .png/.jpg/whatever file, or open
    with PIL.Image.open(io.BytesIO(...)) - the app doesn't care which codec,
    it just delegates to Android's stock decoder, so it's whatever standard
    format Android's BitmapFactory supports)."""
    if len(payload_blob) < 2:
        raise ValueError("payload blob too short to contain an image")
    return payload_blob[1:]


def parse_forecast_response(data: bytes) -> ForecastResponse:
    """Faithful port of C1758g.m3716a / m3718c / m3717b for the top-level
    dispatch (the CONFIRMED-correct parsing path - see protocol.py
    docstring). Tags not explicitly modeled below are captured raw in
    raw_top_level_fields for manual inspection.

    IMPORTANT: this has NOT been validated against a real captured response
    (no live traffic was used in this assessment). The transform for tag 5
    (XOR+reverse) is verified against the decompiled code with high
    confidence; the *interpretation* of tags 3/4/7/12/14 is best-effort and
    should be confirmed by capturing one real response (e.g. via an
    authorized on-device proxy capture) and diffing against this parser's
    raw_top_level_fields output.

    HEADER: AbstractC1646s.java:372-376 shows the response handled via
        c1758g2.m3716a(bArrM3428a, 4, count - 4)
    i.e. TLV parsing starts at byte offset 4, not 0 - the first 4 bytes are
    a header the client reads but the decompiled validation condition
    around it (line 374: "count < 4 && (uint32-of-first-4-bytes) == count")
    doesn't hold together logically (compare the cleaner, structurally
    IDENTICAL duplicate block at lines 144-153 of the same file: "if
    (count < 4) { skip } else { parse }", with no such equality check) -
    this smells like a decompiler control-flow reconstruction artifact
    (this method has multiple "JADX WARN: Code duplicated" markers). Skip
    4 bytes unconditionally here; if that still doesn't produce a sane
    parse against real captured traffic, the equality check may be real
    and worth re-deriving from a live capture instead of guessing further.
    """
    if len(data) < 4:
        return ForecastResponse(error_message="response shorter than the 4-byte header")
    header_value = int.from_bytes(data[0:4], "big")
    body = data[4:]
    r = TLVReader(body, 0, len(body))
    out = ForecastResponse()
    out.raw_top_level_fields["_header_uint32"] = data[0:4]
    out.raw_top_level_fields["_header_matches_total_len"] = (header_value == len(data))
    out.raw_top_level_fields["_header_matches_body_len"] = (header_value == len(body))

    tag = r.read_byte()
    while tag > -1:
        length = r.read_length()
        start = r.pos
        end = min(start + length, len(body))
        field_reader = TLVReader(body, start, end - start)

        if tag == 1:
            out.error_message = field_reader.read_string("utf-8")
        elif tag == 2:
            out.reference_time_minutes = field_reader.read_uint16()
        elif tag == 5:
            blob = bytearray(field_reader.read_remaining_bytes())
            if len(blob) >= 2:
                decode_range(blob, 0, len(blob))
            elif len(blob) == 1:
                pass  # too short to meaningfully transform
            out.payload_blob = bytes(blob)
        elif tag == 7:
            out.aux_string = field_reader.read_string("utf-8")
        elif tag == 3:
            # C0145d0: sub 1 = Interval (byte), sub 3 = Intensities (int16[]),
            # sub 4 = Probabilities (byte[]). NONE of this is XOR/reverse
            # transformed - it's plain TLV, protected only by TLS in transit.
            st = field_reader.read_byte()
            while st > -1:
                slen = field_reader.read_length()
                sstart = field_reader.pos
                send = min(sstart + slen, len(body))
                sub = TLVReader(body, sstart, send - sstart)
                if st == 1:
                    out.interval = sub.read_byte()
                elif st == 3:
                    out.intensities = [sub.read_uint16() for _ in range(sub.remaining() // 2)]
                elif st == 4:
                    out.probabilities = list(sub.read_remaining_bytes())
                field_reader.pos = send
                st = field_reader.read_byte()
        elif tag == 11:
            # C1757f -> WindGrid, verified against AsyncTaskC0695s1.java:150-164
            vals = {}
            st = field_reader.read_byte()
            while st > -1:
                slen = field_reader.read_length()
                sstart = field_reader.pos
                send = min(sstart + slen, len(body))
                sub = TLVReader(body, sstart, send - sstart)
                if st == 4:
                    vals["width"] = sub.read_uint16()
                elif st == 5:
                    vals["height"] = sub.read_uint16()
                elif st == 6:
                    vals["cell_scale"] = sub.read_uint16()
                elif st == 7:
                    vals["u"] = sub.read_remaining_bytes()
                elif st == 8:
                    vals["v"] = sub.read_remaining_bytes()
                elif st == 9:
                    vals["wind_scale"] = sub.read_float()
                elif st == 10:
                    vals["gust"] = sub.read_remaining_bytes()
                elif st == 11:
                    vals["gust_scale"] = sub.read_float()
                field_reader.pos = send
                st = field_reader.read_byte()
            if {"width", "height", "u", "v", "wind_scale"} <= vals.keys():
                cell_count = vals["width"] * vals["height"]
                out.wind = WindGrid(
                    width=vals["width"], height=vals["height"],
                    cell_scale=vals.get("cell_scale", 0),
                    u_component=WindGrid.decompress_field(vals["u"], cell_count),
                    v_component=WindGrid.decompress_field(vals["v"], cell_count),
                    wind_scale=vals["wind_scale"],
                    gust=WindGrid.decompress_field(vals.get("gust", b""), cell_count) if vals.get("gust") else b"",
                    gust_scale=vals.get("gust_scale", 1.0),
                )
        elif tag == 14:
            st = field_reader.read_byte()
            while st > -1:
                slen = field_reader.read_length()
                sstart = field_reader.pos
                send = min(sstart + slen, len(body))
                sub = TLVReader(body, sstart, send - sstart)
                if st == 1:
                    out.temperature_c = sub.read_float()
                field_reader.pos = send
                st = field_reader.read_byte()
        elif tag == 6:
            # [INFERRED PURELY EMPIRICALLY - see ForecastResponse.server_time_minutes_v1 docstring]
            out.server_time_minutes_v1 = field_reader.read_uint32()
        elif tag == 13:
            # [INFERRED PURELY EMPIRICALLY - see ForecastResponse.server_time_ms docstring]
            hi = field_reader.read_uint32()
            lo = field_reader.read_uint32()
            out.server_time_ms = (hi << 32) | lo
        elif tag == 4:
            # nested [VERIFIED against p138u5/AbstractC1859b.java:226-231, the
            # location-marker/trajectory drawing code - CORRECTED, previously
            # mislabeled as "grid dims"/"grid origin"]:
            #   sub 1 (2x uint16) = pixel (x,y) of the requested location, in
            #     the rain-image's own native pixel space - server-computed,
            #     not something the client derives.
            #   sub 2 (2x float) = (U,V) wind vector AT that exact point,
            #     also server-computed - NOT a scale/origin value.
            loc_xy = None
            loc_uv = None
            st = field_reader.read_byte()
            while st > -1:
                slen = field_reader.read_length()
                sstart = field_reader.pos
                send = min(sstart + slen, len(body))
                sub = TLVReader(body, sstart, send - sstart)
                if st == 1:
                    loc_xy = (sub.read_uint16(), sub.read_uint16())
                elif st == 2:
                    loc_uv = (sub.read_float(), sub.read_float())
                field_reader.pos = send
                st = field_reader.read_byte()
            out.location_xy = loc_xy
            out.location_uv = loc_uv
        else:
            out.raw_top_level_fields[tag] = field_reader.read_remaining_bytes()

        r.pos = end
        tag = r.read_byte()

    return out
