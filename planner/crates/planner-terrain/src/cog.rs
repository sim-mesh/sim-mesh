//! Windowed GeoTIFF/COG reading, pure Rust (`tiff` crate).
//!
//! Reads only the chunks (tiles or strips) a request touches and caches them,
//! which is what the radial-sweep coverage engine needs. v0 scope: single
//! band, full-resolution IFD only (overview selection comes with the pack
//! work), sample formats f32/f64/i16/u16/u8, any compression the `tiff`
//! crate decodes (LZW/Deflate/packbits/…).
//!
//! Georeferencing: ModelPixelScaleTag (33550) + ModelTiepointTag (33922),
//! with GTRasterTypeGeoKey (1025 in GeoKeyDirectoryTag 34735) deciding
//! whether the tiepoint names a pixel corner (Area, default) or a pixel
//! center (Point). `Grid`/`CogMeta` origins are always pixel CENTERS.

use crate::{Grid, TerrainError};
use planner_core::geo::Xy;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufReader, Read, Seek};
use std::path::Path;
use std::sync::Arc;
use tiff::decoder::{Decoder, DecodingResult};
use tiff::tags::Tag;

const TAG_MODEL_PIXEL_SCALE: u16 = 33550;
const TAG_MODEL_TIEPOINT: u16 = 33922;
const TAG_MODEL_TRANSFORMATION: u16 = 34264;
const TAG_GEO_KEY_DIRECTORY: u16 = 34735;
const GEOKEY_RASTER_TYPE: u16 = 1025;
const RASTER_TYPE_POINT: u16 = 2;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CogMeta {
    pub width: u32,
    pub height: u32,
    pub chunk_w: u32,
    pub chunk_h: u32,
    /// World coordinate of the CENTER of pixel (0, 0).
    pub origin: Xy,
    /// Signed pixel steps: `dx` per column (east+), `dy` per row (negative
    /// for north-up rasters).
    pub dx: f64,
    pub dy: f64,
}

impl CogMeta {
    /// Pixel box for a world bbox: `(c0, r0, width, height)`, rounded as
    /// every window read rounds it, so readers that share it read the same
    /// cells.
    pub fn window_box(&self, min: Xy, max: Xy) -> Result<(u32, u32, usize, usize), TerrainError> {
        let to_col = |x: f64| ((x - self.origin.x) / self.dx).round();
        let to_row = |y: f64| ((y - self.origin.y) / self.dy).round();
        let (ca, cb) = (to_col(min.x), to_col(max.x));
        let (ra, rb) = (to_row(min.y), to_row(max.y));
        let (c0, c1) = (ca.min(cb).max(0.0) as u32, ca.max(cb) as u32);
        let (r0, r1) = (ra.min(rb).max(0.0) as u32, ra.max(rb) as u32);
        if c1 >= self.width || r1 >= self.height {
            return Err(TerrainError::OutOfBounds(max.x, max.y));
        }
        Ok((c0, r0, (c1 - c0 + 1) as usize, (r1 - r0 + 1) as usize))
    }

    /// The centre of the first cell of a window starting at column `c0`, row
    /// `r0`: the origin every window read gives its grid.
    pub fn window_origin(&self, c0: u32, r0: u32) -> Xy {
        Xy { x: self.origin.x + c0 as f64 * self.dx, y: self.origin.y + r0 as f64 * self.dy }
    }
}

/// One decoded chunk: pixel values plus the buffer's actual row width
/// (edge chunks from the high-level decoder are clipped; raw palette tiles
/// stay padded to the full tile width per the TIFF spec).
#[derive(Clone)]
struct Chunk {
    data: Arc<Vec<f32>>,
    row_w: u32,
}

/// Raw access parameters for palette-color images the `tiff` crate refuses
/// to decode (e.g. ESA WorldCover): the palette INDICES are the data.
struct RawPalette {
    file: std::fs::File,
    offsets: Vec<u64>,
    counts: Vec<u64>,
    compression: u16,
    predictor: u16,
}

/// Direct row access for an UNCOMPRESSED strip image — the shape every pack
/// raster currently has.
///
/// Why this exists. `chunk_dimensions()` on a striped TIFF is a full-width
/// band: the pack's own rasters report 10363 x 256, which is 10.6 MB decoded
/// to f32. Going through `chunk()` therefore costs one 10.6 MB decode per
/// band TOUCHED, whatever the caller wanted from it. A 2 km map view is 400
/// columns by 250 rows -- 0.4 MB of actual data -- and spans one or two
/// bands in each of four layers, so the server decoded ~85 MB to answer it.
/// Measured: 8.4 s for a tile carrying 0.7 MB.
///
/// Because the data is uncompressed, the byte offset of any (row, col) is
/// arithmetic, so the rows a window actually wants can simply be read. The
/// difference is not a constant factor -- it is the difference between paying
/// for the window and paying for the bands the window happens to intersect.
struct RawRows {
    file: std::fs::File,
    /// Byte offset of the first row of each strip.
    offsets: Vec<u64>,
    rows_per_strip: u32,
    bytes_per_sample: u32,
    /// TIFF SampleFormat (1 = uint, 2 = int, 3 = IEEE float).
    sample_format: u16,
}

impl RawRows {
    /// Byte offset of row `r`, column `c0`. `None` if the row is not covered
    /// by the strip table, which means the file disagrees with its own tags.
    fn offset_of(&self, r: u32, c0: u32, width: u32) -> Option<u64> {
        let strip = (r / self.rows_per_strip) as usize;
        let within = (r % self.rows_per_strip) as u64;
        let bps = self.bytes_per_sample as u64;
        Some(*self.offsets.get(strip)? + (within * width as u64 + c0 as u64) * bps)
    }

    /// Row `r`'s samples from column `c0`, as many as `out` holds, read by a
    /// positioned read (`pread`) that needs no cursor and so no lock.
    /// `Ok(false)` where the strip table does not cover the row. `bytes` is
    /// the caller's scratch, `out.len()` samples long.
    #[cfg(unix)]
    fn read_row_at(
        &self,
        r: u32,
        c0: u32,
        width: u32,
        bytes: &mut [u8],
        out: &mut [f32],
    ) -> Result<bool, TerrainError> {
        use std::os::unix::fs::FileExt as _;
        let Some(off) = self.offset_of(r, c0, width) else {
            return Ok(false);
        };
        self.file
            .read_exact_at(bytes, off)
            .map_err(|e| TerrainError::Cog(format!("row {r}: {e}")))?;
        self.decode_into(bytes, out)?;
        Ok(true)
    }

    fn decode_into(&self, bytes: &[u8], out: &mut [f32]) -> Result<(), TerrainError> {
        // The layout is matched once a row, not once a sample: every cell of
        // every window passes through here, and matching per sample kept the
        // loop from being the plain conversion it is. Each arm converts a
        // sample exactly as the per-sample match did.
        fn each<const N: usize>(bytes: &[u8], out: &mut [f32], f: impl Fn([u8; N]) -> f32) {
            assert!(bytes.len() >= out.len() * N, "a row's bytes for every sample");
            for (v, b) in out.iter_mut().zip(bytes.chunks_exact(N)) {
                *v = f(b.try_into().expect("N bytes"));
            }
        }
        if out.is_empty() {
            return Ok(());
        }
        match (self.sample_format, self.bytes_per_sample as usize) {
            (3, 4) => each(bytes, out, f32::from_le_bytes),
            (3, 8) => each(bytes, out, |b| f64::from_le_bytes(b) as f32),
            (2, 2) => each(bytes, out, |b| i16::from_le_bytes(b) as f32),
            (2, 4) => each(bytes, out, |b| i32::from_le_bytes(b) as f32),
            (1, 1) => each(bytes, out, |b: [u8; 1]| b[0] as f32),
            (1, 2) => each(bytes, out, |b| u16::from_le_bytes(b) as f32),
            (1, 4) => each(bytes, out, |b| u32::from_le_bytes(b) as f32),
            (f, n) => {
                return Err(TerrainError::Cog(format!(
                    "direct row read: unsupported sample format {f} at {n} byte(s)"
                )))
            }
        }
        Ok(())
    }
}

pub struct CogReader<R: Read + Seek> {
    decoder: Decoder<R>,
    meta: CogMeta,
    chunks_across: u32,
    cache: HashMap<u32, Chunk>,
    cache_cap: usize,
    raw_palette: Option<RawPalette>,
    /// Set only for uncompressed strip images; see [`RawRows`].
    raw_rows: Option<RawRows>,
    /// Each chunk's stored bytes, once asked (`empty_chunk`).
    chunk_bytes: Option<Vec<u64>>,
}

impl CogReader<BufReader<File>> {
    pub fn open(path: &Path) -> Result<Self, TerrainError> {
        let file = File::open(path).map_err(|e| TerrainError::Cog(format!("{path:?}: {e}")))?;
        let mut this = Self::from_reader(BufReader::new(file))?;
        // Palette-color images (photometric = 3, e.g. ESA WorldCover): the
        // high-level decoder refuses them, but the palette INDICES are the
        // data — attach raw chunk access through a second file handle.
        // Memory note: chunks stream one at a time through the existing
        // bounded cache; nothing whole-image is ever allocated.
        let photometric = this
            .decoder
            .get_tag(Tag::PhotometricInterpretation)
            .ok()
            .and_then(|v| v.into_u16().ok());
        if photometric == Some(3) {
            let tiled = this.decoder.get_tag(Tag::TileWidth).is_ok();
            let (off_tag, cnt_tag) = if tiled {
                (Tag::TileOffsets, Tag::TileByteCounts)
            } else {
                (Tag::StripOffsets, Tag::StripByteCounts)
            };
            let offsets = tag_u64s(&mut this.decoder, off_tag)?;
            let counts = tag_u64s(&mut this.decoder, cnt_tag)?;
            let compression = this
                .decoder
                .get_tag(Tag::Compression)
                .ok()
                .and_then(|v| v.into_u16().ok())
                .unwrap_or(1);
            let predictor = this
                .decoder
                .get_tag(Tag::Predictor)
                .ok()
                .and_then(|v| v.into_u16().ok())
                .unwrap_or(1);
            let file =
                File::open(path).map_err(|e| TerrainError::Cog(format!("{path:?}: {e}")))?;
            this.raw_palette = Some(RawPalette { file, offsets, counts, compression, predictor });
        }
        if this.raw_palette.is_none() {
            this.raw_rows = detect_raw_rows(&mut this.decoder, path).unwrap_or(None);
        }
        Ok(this)
    }

    /// The image of a cloud-optimised GeoTIFF whose pixel suits `want_m`:
    /// the coarsest of its full-resolution image and its overviews whose
    /// pixel is no larger than `want_m`, else the full image. An overview
    /// carries no geo tags of its own: its georeferencing is the full
    /// image's, its pixel scaled by how much smaller it is, its outer edge
    /// the same.
    ///
    /// A file fetched as a window (only the chunks a rectangle needs, the
    /// rest a sparse hole) reads the same way: a chunk never fetched fails to
    /// decode, and the caller takes it as no data there.
    ///
    /// A transparency mask (NewSubfileType with bit 4 set: GDAL's internal
    /// masks, each after the image it masks and the same size) holds 0 or
    /// 255 rather than values, and is passed over.
    pub fn open_level(path: &Path, want_m: f64) -> Result<Self, TerrainError> {
        let mut this = Self::open(path)?;
        if this.raw_palette.is_some() {
            return Ok(this); // palette images are read whole, at full resolution
        }
        let full = this.meta;
        let mut best: Option<(usize, CogMeta)> = None;
        let mut index = 1usize;
        while this.decoder.seek_to_image(index).is_ok() {
            let subfile = this.decoder.get_tag(Tag::NewSubfileType).ok();
            if subfile
                .and_then(|v| v.into_u32().ok())
                .is_some_and(|t| t & 4 != 0)
            {
                index += 1;
                continue;
            }
            let Ok((w, h)) = this.decoder.dimensions() else { break };
            let (fx, fy) = (full.width as f64 / w as f64, full.height as f64 / h as f64);
            let (dx, dy) = (full.dx * fx, full.dy * fy);
            if dx.abs() > want_m + 1e-9 {
                break; // overviews only get coarser from here
            }
            // The outer corner stays where it is; the first pixel's centre moves in.
            let corner = Xy { x: full.origin.x - 0.5 * full.dx, y: full.origin.y - 0.5 * full.dy };
            let (cw, ch) = this.decoder.chunk_dimensions();
            best = Some((
                index,
                CogMeta {
                    width: w,
                    height: h,
                    chunk_w: cw,
                    chunk_h: ch,
                    origin: Xy { x: corner.x + 0.5 * dx, y: corner.y + 0.5 * dy },
                    dx,
                    dy,
                },
            ));
            index += 1;
        }
        let (index, meta) = best.unwrap_or((0, full));
        this.decoder
            .seek_to_image(index)
            .map_err(|e| TerrainError::Cog(format!("{path:?}: image {index}: {e}")))?;
        this.chunks_across = meta.width.div_ceil(meta.chunk_w);
        this.meta = meta;
        this.cache.clear();
        if index != 0 {
            this.raw_rows = None; // the direct row path is the full image's
        }
        Ok(this)
    }
}

/// Attach direct row access, but only when it is provably equivalent.
///
/// Every condition below is a way the byte arithmetic in [`RawRows`] would be
/// wrong, and a wrong fast path here is worse than a slow one: it would return
/// plausible terrain from the wrong offsets, and nothing downstream could tell.
/// So this returns `Ok(None)` -- fall back to the decoder -- for anything it
/// does not fully recognise, and the caller treats an error the same way.
fn detect_raw_rows(
    dec: &mut Decoder<BufReader<File>>,
    path: &Path,
) -> Result<Option<RawRows>, TerrainError> {
    let u16tag = |d: &mut Decoder<BufReader<File>>, t: Tag| {
        d.get_tag(t).ok().and_then(|v| v.into_u16().ok())
    };
    // Uncompressed only: with compression the offset of a row inside a strip
    // is not arithmetic at all.
    if u16tag(dec, Tag::Compression).unwrap_or(1) != 1 {
        return Ok(None);
    }
    // Strips, not tiles. A tiled image needs 2-D block arithmetic and none of
    // the packs are tiled today.
    if dec.get_tag(Tag::TileWidth).is_ok() {
        return Ok(None);
    }
    // Single band. With several samples per pixel the row stride carries
    // interleaved channels and `width * bps` is the wrong step.
    if u16tag(dec, Tag::SamplesPerPixel).unwrap_or(1) != 1 {
        return Ok(None);
    }
    let bits = u16tag(dec, Tag::BitsPerSample).unwrap_or(0);
    if bits == 0 || bits % 8 != 0 {
        return Ok(None); // sub-byte packing is not addressable per pixel
    }
    let sample_format = u16tag(dec, Tag::SampleFormat).unwrap_or(1);
    let rows_per_strip = dec
        .get_tag(Tag::RowsPerStrip)
        .ok()
        .and_then(|v| v.into_u32().ok())
        .unwrap_or(0);
    if rows_per_strip == 0 {
        return Ok(None);
    }
    let offsets = tag_u64s(dec, Tag::StripOffsets)?;
    if offsets.is_empty() {
        return Ok(None);
    }
    // The byte counts must match what the arithmetic assumes, or the tags and
    // the layout disagree and every offset after the first strip is wrong.
    let (width, height) = dec
        .dimensions()
        .map_err(|e| TerrainError::Cog(format!("dimensions: {e}")))?;
    let bps = bits as u64 / 8;
    let counts = tag_u64s(dec, Tag::StripByteCounts)?;
    if counts.len() != offsets.len() {
        return Ok(None);
    }
    for (i, &c) in counts.iter().enumerate() {
        let rows = rows_per_strip.min(height - (i as u32 * rows_per_strip).min(height));
        if c != rows as u64 * width as u64 * bps {
            return Ok(None);
        }
    }
    let file = File::open(path).map_err(|e| TerrainError::Cog(format!("{path:?}: {e}")))?;
    Ok(Some(RawRows {
        file,
        offsets,
        rows_per_strip,
        bytes_per_sample: bps as u32,
        sample_format,
    }))
}

fn tag_u64s<R: Read + Seek>(dec: &mut Decoder<R>, tag: Tag) -> Result<Vec<u64>, TerrainError> {
    dec.get_tag_u64_vec(tag)
        .map_err(|e| TerrainError::Cog(format!("tag {tag:?}: {e}")))
}

impl<R: Read + Seek> CogReader<R> {
    pub fn from_reader(r: R) -> Result<Self, TerrainError> {
        let mut decoder =
            Decoder::new(r).map_err(|e| TerrainError::Cog(format!("tiff open: {e}")))?;
        let (width, height) = decoder
            .dimensions()
            .map_err(|e| TerrainError::Cog(format!("dimensions: {e}")))?;

        // Pixel scale and a tiepoint, or (GeoServer's and some ArcGIS
        // coverage services' answers) a model transformation, which north-up
        // and unrotated says the same: x = a·i + d, y = e·j + h.
        let scale = decoder.get_tag_f64_vec(Tag::Unknown(TAG_MODEL_PIXEL_SCALE));
        let tie = decoder.get_tag_f64_vec(Tag::Unknown(TAG_MODEL_TIEPOINT));
        let (ti, tj, tx, ty, dx, dy) = match (scale, tie) {
            (Ok(scale), Ok(tie)) => {
                if scale.len() < 2 || tie.len() < 6 {
                    return Err(TerrainError::Cog("geo tags too short".into()));
                }
                // Tiepoint: raster (i, j) ↔ world (x, y); north-up ⇒ dy negative.
                (tie[0], tie[1], tie[3], tie[4], scale[0], -scale[1])
            }
            (scale, _) => {
                let m = decoder
                    .get_tag_f64_vec(Tag::Unknown(TAG_MODEL_TRANSFORMATION))
                    .map_err(|e| {
                        TerrainError::Cog(format!(
                            "no ModelPixelScaleTag ({}) and no ModelTransformationTag: {e}",
                            scale.err().map_or_else(String::new, |e| e.to_string())
                        ))
                    })?;
                if m.len() < 16 {
                    return Err(TerrainError::Cog("ModelTransformationTag too short".into()));
                }
                if m[1] != 0.0 || m[4] != 0.0 || m[0] <= 0.0 || m[5] >= 0.0 {
                    return Err(TerrainError::Cog(
                        "ModelTransformationTag rotates or flips the image".into(),
                    ));
                }
                (0.0, 0.0, m[3], m[7], m[0], m[5])
            }
        };

        // GTRasterTypeGeoKey: Area (corner, default) vs Point (center).
        let pixel_is_point = decoder
            .get_tag_u16_vec(Tag::Unknown(TAG_GEO_KEY_DIRECTORY))
            .ok()
            .and_then(|keys| {
                keys.chunks_exact(4)
                    .skip(1) // header entry
                    .find(|e| e[0] == GEOKEY_RASTER_TYPE)
                    .map(|e| e[3] == RASTER_TYPE_POINT)
            })
            .unwrap_or(false);

        // World coordinate of the center of pixel (0,0).
        let (mut ox, mut oy) = (tx - ti * dx, ty - tj * dy);
        if !pixel_is_point {
            ox += 0.5 * dx;
            oy += 0.5 * dy;
        }

        let (chunk_w, chunk_h) = decoder.chunk_dimensions();
        let chunks_across = width.div_ceil(chunk_w);
        Ok(Self {
            decoder,
            meta: CogMeta {
                width,
                height,
                chunk_w,
                chunk_h,
                origin: Xy { x: ox, y: oy },
                dx,
                dy,
            },
            chunks_across,
            cache: HashMap::new(),
            cache_cap: 64,
            raw_palette: None,
            raw_rows: None,
            chunk_bytes: None,
        })
    }

    pub fn meta(&self) -> &CogMeta {
        &self.meta
    }

    /// Cap on cached decoded chunks (default 64; a 512×512 f32 tile is 1 MB).
    pub fn set_cache_cap(&mut self, cap: usize) {
        self.cache_cap = cap.max(1);
    }

    /// Size the cache for a horizontal row sweep: one band of chunks across
    /// the image plus margin. Keeps many-worker builds at MBs per reader
    /// without thrashing the band's working set.
    pub fn tune_for_row_sweep(&mut self) {
        self.cache_cap = (self.chunks_across as usize + 2).clamp(8, 64);
    }

    fn chunk(&mut self, idx: u32) -> Result<Chunk, TerrainError> {
        if let Some(c) = self.cache.get(&idx) {
            return Ok(c.clone());
        }
        let chunk = if self.raw_palette.is_some() {
            self.read_raw_chunk(idx)?
        } else if self.empty_chunk(idx) {
            // A chunk the file stores no bytes for (Norway's coverage service
            // leaves those of a box with no data empty): no data. Read, it
            // would decode whatever lies at offset 0, the file's header.
            let cx = idx % self.chunks_across;
            let cy = idx / self.chunks_across;
            let this_w = self.meta.chunk_w.min(self.meta.width - cx * self.meta.chunk_w);
            let this_h = self.meta.chunk_h.min(self.meta.height - cy * self.meta.chunk_h);
            Chunk { data: Arc::new(vec![f32::NAN; (this_w * this_h) as usize]), row_w: this_w }
        } else {
            let cx = idx % self.chunks_across;
            let this_w = self.meta.chunk_w.min(self.meta.width - cx * self.meta.chunk_w);
            let res = self
                .decoder
                .read_chunk(idx)
                .map_err(|e| TerrainError::Cog(format!("read_chunk {idx}: {e}")))?;
            let data: Vec<f32> = match res {
                DecodingResult::F32(v) => v,
                DecodingResult::F64(v) => v.into_iter().map(|x| x as f32).collect(),
                DecodingResult::I16(v) => v.into_iter().map(|x| x as f32).collect(),
                DecodingResult::U16(v) => v.into_iter().map(|x| x as f32).collect(),
                DecodingResult::U8(v) => v.into_iter().map(|x| x as f32).collect(),
                DecodingResult::I32(v) => v.into_iter().map(|x| x as f32).collect(),
                DecodingResult::U32(v) => v.into_iter().map(|x| x as f32).collect(),
                _ => return Err(TerrainError::Cog("unsupported sample format".into())),
            };
            Chunk { data: Arc::new(data), row_w: this_w }
        };
        if self.cache.len() >= self.cache_cap {
            // Evict a single arbitrary entry — keeps the hot set warm
            // (full flushes measurably thrashed row sweeps).
            if let Some(&k) = self.cache.keys().next() {
                self.cache.remove(&k);
            }
        }
        self.cache.insert(idx, chunk.clone());
        Ok(chunk)
    }

    /// Whether the image stores no bytes for a chunk: its byte count 0, read
    /// once per reader, from the image it reads (an overview's own).
    fn empty_chunk(&mut self, idx: u32) -> bool {
        if self.chunk_bytes.is_none() {
            let tag = if self.decoder.get_tag(Tag::TileWidth).is_ok() {
                Tag::TileByteCounts
            } else {
                Tag::StripByteCounts
            };
            self.chunk_bytes = Some(self.decoder.get_tag_u64_vec(tag).unwrap_or_default());
        }
        self.chunk_bytes.as_ref().and_then(|c| c.get(idx as usize)) == Some(&0)
    }

    /// Raw path for palette images: seek + read + inflate one chunk.
    /// Tiles stay PADDED to the full tile size (TIFF spec) → row_w = chunk_w.
    fn read_raw_chunk(&mut self, idx: u32) -> Result<Chunk, TerrainError> {
        use std::io::{Seek as _, SeekFrom};
        let raw = self.raw_palette.as_mut().expect("caller checked");
        let i = idx as usize;
        if i >= raw.offsets.len() {
            return Err(TerrainError::Cog(format!("chunk {idx} out of range")));
        }
        let mut buf = vec![0u8; raw.counts[i] as usize];
        raw.file
            .seek(SeekFrom::Start(raw.offsets[i]))
            .and_then(|_| std::io::Read::read_exact(&mut raw.file, &mut buf))
            .map_err(|e| TerrainError::Cog(format!("raw chunk {idx}: {e}")))?;

        let tiled = self.decoder.get_tag(Tag::TileWidth).is_ok();
        let (cw, ch) = (self.meta.chunk_w, self.meta.chunk_h);
        let (row_w, rows) = if tiled {
            (cw, ch) // padded
        } else {
            let cy = idx / self.chunks_across;
            (self.meta.width, ch.min(self.meta.height - cy * ch))
        };
        let expected = (row_w * rows) as usize;

        let mut out = match raw.compression {
            1 => buf,
            5 => weezl::decode::Decoder::with_tiff_size_switch(weezl::BitOrder::Msb, 8)
                .decode(&buf)
                .map_err(|e| TerrainError::Cog(format!("lzw chunk {idx}: {e}")))?,
            8 | 32946 => {
                let mut v = Vec::with_capacity(expected);
                std::io::Read::read_to_end(
                    &mut flate2::read::ZlibDecoder::new(&buf[..]),
                    &mut v,
                )
                .map_err(|e| TerrainError::Cog(format!("deflate chunk {idx}: {e}")))?;
                v
            }
            other => {
                return Err(TerrainError::Cog(format!(
                    "palette raw path: unsupported compression {other}"
                )))
            }
        };
        if out.len() < expected {
            return Err(TerrainError::Cog(format!(
                "chunk {idx}: {} bytes decoded, expected {expected}",
                out.len()
            )));
        }
        out.truncate(expected);
        if raw.predictor == 2 {
            for r in 0..rows as usize {
                let row = &mut out[r * row_w as usize..(r + 1) * row_w as usize];
                for c in 1..row.len() {
                    row[c] = row[c].wrapping_add(row[c - 1]);
                }
            }
        } else if raw.predictor != 1 {
            return Err(TerrainError::Cog(format!(
                "palette raw path: unsupported predictor {}",
                raw.predictor
            )));
        }
        Ok(Chunk { data: Arc::new(out.into_iter().map(|b| b as f32).collect()), row_w })
    }

    /// Pixel value at (col, row); decodes the owning chunk on demand.
    pub fn pixel(&mut self, col: u32, row: u32) -> Result<f32, TerrainError> {
        if col >= self.meta.width || row >= self.meta.height {
            return Err(TerrainError::OutOfBounds(col as f64, row as f64));
        }
        let (cw, ch) = (self.meta.chunk_w, self.meta.chunk_h);
        let (cx, cy) = (col / cw, row / ch);
        let idx = cy * self.chunks_across + cx;
        let (lc, lr) = (col - cx * cw, row - cy * ch);
        let chunk = self.chunk(idx)?;
        Ok(chunk.data[(lr * chunk.row_w + lc) as usize])
    }

    /// Bilinear sample at a world coordinate; Ok(None) outside the raster.
    /// The valid domain extends HALF A PIXEL beyond the outer pixel centers
    /// (pixel-area semantics, clamped) so adjacent tiles meet without a
    /// one-column seam gap.
    pub fn sample(&mut self, p: Xy) -> Result<Option<f32>, TerrainError> {
        let fx = (p.x - self.meta.origin.x) / self.meta.dx;
        let fy = (p.y - self.meta.origin.y) / self.meta.dy;
        let eps = 1e-9;
        let (w, h) = (self.meta.width as f64, self.meta.height as f64);
        if fx < -0.5 - eps || fy < -0.5 - eps || fx > w - 0.5 + eps || fy > h - 0.5 + eps {
            return Ok(None);
        }
        let fx = fx.clamp(0.0, w - 1.0);
        let fy = fy.clamp(0.0, h - 1.0);
        let c0 = fx.floor() as u32;
        let r0 = fy.floor() as u32;
        let c1 = (c0 + 1).min(self.meta.width - 1);
        let r1 = (r0 + 1).min(self.meta.height - 1);
        let tx = (fx - c0 as f64) as f32;
        let ty = (fy - r0 as f64) as f32;
        let top = self.pixel(c0, r0)? * (1.0 - tx) + self.pixel(c1, r0)? * tx;
        let bot = self.pixel(c0, r1)? * (1.0 - tx) + self.pixel(c1, r1)? * tx;
        Ok(Some(top * (1.0 - ty) + bot * ty))
    }

    /// Fill `out` (h rows of w columns, starting at col `c0`, row `r0`) by
    /// seeking straight to those rows. `Ok(false)` = no direct path on this
    /// file, caller should fall back.
    ///
    /// Reads ONE row per seek rather than one buffer for the whole box: the
    /// rows of a window are not contiguous on disk (a full image row is
    /// 10 363 samples wide where a window row may be 400), so a single large
    /// read would pull in every intervening column.
    fn read_rows_direct(
        &mut self,
        c0: u32,
        r0: u32,
        w: usize,
        h: usize,
        out: &mut [f32],
    ) -> Result<bool, TerrainError> {
        use std::io::{Read as _, Seek as _, SeekFrom};
        let width = self.meta.width;
        let Some(raw) = self.raw_rows.as_mut() else {
            return Ok(false);
        };
        let bps = raw.bytes_per_sample as usize;
        let mut bytes = vec![0u8; w * bps];
        for r in 0..h {
            let Some(off) = raw.offset_of(r0 + r as u32, c0, width) else {
                // The strip table does not cover this row. Not an error worth
                // failing the request over -- the decoder path can still read
                // it -- so hand the whole window back to the caller rather
                // than returning a half-filled buffer.
                return Ok(false);
            };
            raw.file
                .seek(SeekFrom::Start(off))
                .and_then(|_| raw.file.read_exact(&mut bytes))
                .map_err(|e| TerrainError::Cog(format!("row {}: {e}", r0 + r as u32)))?;
            raw.decode_into(&bytes, &mut out[r * w..(r + 1) * w])?;
        }
        Ok(true)
    }

    /// Materialize the sub-grid covering a bbox at no more than `max_w` ×
    /// `max_h` cells, by reading only every n-th row and column.
    ///
    /// WHY THIS EXISTS. `window` reads the box at FULL resolution whatever the
    /// caller intends to do with it, and the tile path then resamples that
    /// down to at most 2048 cells across. On the Berlin 5 m pack a zoomed-out
    /// request covers the whole 10 742 × 9 127 layer, so serving one screen
    /// of map allocated a 392 MB f32 grid per layer — four layers, 1.57 GB —
    /// to produce a 3.2 M-cell tile. That is the whole 2 GB deployment target
    /// spent on a single pan, and it is spent before the first byte is sent.
    ///
    /// Striding is not a quality trade here. The caller already point-samples
    /// this grid once per OUTPUT cell, and at a 5:1 ratio those points are
    /// five source cells apart — every intermediate cell was read, allocated
    /// and then never looked at. Reading at the stride reads the cells the
    /// answer is actually built from.
    ///
    /// The returned grid carries the STRIDED step in `dx_m`/`dy_m`, so its
    /// samplers place every cell where it really is and callers need no
    /// knowledge that a stride happened.
    pub fn window_max(
        &mut self,
        min: Xy,
        max: Xy,
        max_w: usize,
        max_h: usize,
    ) -> Result<Grid, TerrainError> {
        let (c0, r0, w, h) = self.meta.window_box(min, max)?;
        let sc = w.div_ceil(max_w.max(1)).max(1);
        let sr = h.div_ceil(max_h.max(1)).max(1);
        if sc == 1 && sr == 1 {
            return self.window(min, max);
        }
        let (ow, oh) = (w.div_ceil(sc), h.div_ceil(sr));
        let mut data = vec![0f32; ow * oh];
        if !self.read_rows_strided(c0, r0, w, ow, oh, sc, sr, &mut data)? {
            for r in 0..oh {
                for c in 0..ow {
                    data[r * ow + c] =
                        self.pixel(c0 + (c * sc) as u32, r0 + (r * sr) as u32)?;
                }
            }
        }
        let origin = self.meta.window_origin(c0, r0);
        Grid::with_axes(
            origin,
            self.meta.dx * sc as f64,
            self.meta.dy * sr as f64,
            ow,
            oh,
            data,
        )
    }

    /// As `read_rows_direct`, but seeking only to every `sr`-th row and
    /// keeping every `sc`-th sample of it.
    ///
    /// The row's bytes are read whole and decimated after: they are contiguous
    /// on disk, so a strided read would cost the same seeks for less data.
    /// The saving that matters is the ROWS never visited at all.
    #[allow(clippy::too_many_arguments)]
    fn read_rows_strided(
        &mut self,
        c0: u32,
        r0: u32,
        w: usize,
        ow: usize,
        oh: usize,
        sc: usize,
        sr: usize,
        out: &mut [f32],
    ) -> Result<bool, TerrainError> {
        use std::io::{Read as _, Seek as _, SeekFrom};
        let width = self.meta.width;
        let Some(raw) = self.raw_rows.as_mut() else {
            return Ok(false);
        };
        let bps = raw.bytes_per_sample as usize;
        let mut bytes = vec![0u8; w * bps];
        let mut row_f = vec![0f32; w];
        for r in 0..oh {
            let src_row = r0 + (r * sr) as u32;
            let Some(off) = raw.offset_of(src_row, c0, width) else {
                return Ok(false);
            };
            raw.file
                .seek(SeekFrom::Start(off))
                .and_then(|_| raw.file.read_exact(&mut bytes))
                .map_err(|e| TerrainError::Cog(format!("row {src_row}: {e}")))?;
            raw.decode_into(&bytes, &mut row_f)?;
            for c in 0..ow {
                out[r * ow + c] = row_f[c * sc];
            }
        }
        Ok(true)
    }

    /// Materialize the sub-grid covering the world-space bbox (min/max in
    /// both axes, pixel-center coordinates), decoding only the chunks it
    /// touches.
    pub fn window(&mut self, min: Xy, max: Xy) -> Result<Grid, TerrainError> {
        let (c0, r0, w, h) = self.meta.window_box(min, max)?;
        let mut data = vec![0f32; w * h];
        // Read exactly the rows asked for when the layout allows it. The
        // fallback below is correct but pays for whole 10 MB bands it barely
        // touches; see `RawRows`.
        if self.read_rows_direct(c0, r0, w, h, &mut data)? {
            // read_rows_direct filled `data`.
        } else {
            for r in 0..h as u32 {
                for c in 0..w as u32 {
                    data[(r as usize) * w + c as usize] = self.pixel(c0 + c, r0 + r)?;
                }
            }
        }
        let origin = self.meta.window_origin(c0, r0);
        Grid::with_axes(origin, self.meta.dx, self.meta.dy, w, h, data)
    }
}

/// A layer whose rows are read directly (an uncompressed single-band strip
/// image, [`RawRows`]), read by any number of threads at once.
///
/// `CogReader` seeks one shared file handle, so it is `&mut` and a server
/// holds it behind a lock; every request that reads a window then waits for
/// the one before it. Here each row is a positioned read of its own
/// (`pread`), which needs no cursor: the same rows, decoded by the same
/// code, into the same box as [`CogReader::window`] computes, with no lock.
#[cfg(unix)]
pub struct SharedRows {
    meta: CogMeta,
    raw: RawRows,
}

#[cfg(unix)]
impl<R: Read + Seek> CogReader<R> {
    /// A [`SharedRows`] on this layer's file, when its rows are directly
    /// readable; `None` for any other layout, which keeps `window`.
    pub fn shared_rows(&self) -> Option<SharedRows> {
        let raw = self.raw_rows.as_ref()?;
        Some(SharedRows {
            meta: self.meta,
            raw: RawRows {
                file: raw.file.try_clone().ok()?,
                offsets: raw.offsets.clone(),
                rows_per_strip: raw.rows_per_strip,
                bytes_per_sample: raw.bytes_per_sample,
                sample_format: raw.sample_format,
            },
        })
    }
}

#[cfg(unix)]
impl SharedRows {
    pub fn meta(&self) -> &CogMeta {
        &self.meta
    }

    /// [`CogReader::window`] of this layer: the same box, rounded the same
    /// way, read row by row. `Ok(None)` when the strip table does not cover a
    /// row of it, where `CogReader` falls back to its decoder: the caller
    /// asks `CogReader` then.
    pub fn window(&self, min: Xy, max: Xy) -> Result<Option<Grid>, TerrainError> {
        let (c0, r0, w, h) = self.meta.window_box(min, max)?;
        let mut data = vec![0f32; w * h];
        let mut bytes = vec![0u8; w * self.raw.bytes_per_sample as usize];
        for r in 0..h {
            let out = &mut data[r * w..(r + 1) * w];
            if !self.raw.read_row_at(r0 + r as u32, c0, self.meta.width, &mut bytes, out)? {
                return Ok(None);
            }
        }
        let origin = self.meta.window_origin(c0, r0);
        Grid::with_axes(origin, self.meta.dx, self.meta.dy, w, h, data).map(Some)
    }

    /// [`CogReader::window_max`] of this layer: the same box at the same
    /// stride, each kept row read whole and decimated as it decimates it.
    /// `Ok(None)` where [`SharedRows::window`] gives it.
    pub fn window_max(
        &self,
        min: Xy,
        max: Xy,
        max_w: usize,
        max_h: usize,
    ) -> Result<Option<Grid>, TerrainError> {
        let (c0, r0, w, h) = self.meta.window_box(min, max)?;
        let sc = w.div_ceil(max_w.max(1)).max(1);
        let sr = h.div_ceil(max_h.max(1)).max(1);
        if sc == 1 && sr == 1 {
            return self.window(min, max);
        }
        let (ow, oh) = (w.div_ceil(sc), h.div_ceil(sr));
        let mut data = vec![0f32; ow * oh];
        let mut bytes = vec![0u8; w * self.raw.bytes_per_sample as usize];
        let mut row_f = vec![0f32; w];
        for r in 0..oh {
            let src_row = r0 + (r * sr) as u32;
            if !self.raw.read_row_at(src_row, c0, self.meta.width, &mut bytes, &mut row_f)? {
                return Ok(None);
            }
            for c in 0..ow {
                data[r * ow + c] = row_f[c * sc];
            }
        }
        let origin = self.meta.window_origin(c0, r0);
        Grid::with_axes(origin, self.meta.dx * sc as f64, self.meta.dy * sr as f64, ow, oh, data)
            .map(Some)
    }
}

/// Write a `Grid` as a minimal striped, uncompressed Float32 GeoTIFF
/// (little-endian classic TIFF, RasterType=Point, tiepoint = center of pixel
/// (0,0)). Round-trips through `CogReader`; the pack compiler's v0 layer
/// writer. Compression comes later — pack layers are small enough for now.
pub fn write_geotiff_f32(path: &Path, grid: &Grid) -> Result<(), TerrainError> {
    let (w, h) = (grid.width as u32, grid.height as u32);
    let rows_per_strip: u32 = 256;
    let mut out: Vec<u8> = Vec::with_capacity(grid.data.len() * 4 + 1024);
    out.extend_from_slice(&[0x49, 0x49, 42, 0]);
    out.extend_from_slice(&[0u8; 4]);

    let n_strips = h.div_ceil(rows_per_strip);
    let mut strip_offsets = Vec::new();
    let mut strip_counts = Vec::new();
    for s in 0..n_strips {
        strip_offsets.push(out.len() as u32);
        let rows = rows_per_strip.min(h - s * rows_per_strip);
        for r in 0..rows {
            let row = (s * rows_per_strip + r) as usize;
            for c in 0..w as usize {
                out.extend_from_slice(&grid.data[row * grid.width + c].to_le_bytes());
            }
        }
        strip_counts.push(rows * w * 4);
    }

    let put_u32s = |out: &mut Vec<u8>, v: &[u32]| -> u32 {
        let off = out.len() as u32;
        for x in v {
            out.extend_from_slice(&x.to_le_bytes());
        }
        off
    };
    let put_f64s = |out: &mut Vec<u8>, v: &[f64]| -> u32 {
        let off = out.len() as u32;
        for x in v {
            out.extend_from_slice(&x.to_le_bytes());
        }
        off
    };
    // Offset arrays only when they don't fit inline (n_strips > 1).
    let so = if n_strips > 1 { put_u32s(&mut out, &strip_offsets) } else { strip_offsets[0] };
    let sc = if n_strips > 1 { put_u32s(&mut out, &strip_counts) } else { strip_counts[0] };
    let scale_off = put_f64s(&mut out, &[grid.dx_m, -grid.dy_m, 0.0]);
    let tie_off = put_f64s(&mut out, &[0.0, 0.0, 0.0, grid.origin.x, grid.origin.y, 0.0]);
    let geokeys: [u16; 8] = [1, 1, 0, 1, GEOKEY_RASTER_TYPE, 0, 1, RASTER_TYPE_POINT];
    let gk_off = {
        let off = out.len() as u32;
        for x in geokeys {
            out.extend_from_slice(&x.to_le_bytes());
        }
        off
    };

    let ifd_off = out.len() as u32;
    let mut entries: Vec<(u16, u16, u32, u32)> = vec![
        (256, 4, 1, w),
        (257, 4, 1, h),
        (258, 3, 1, 32),
        (259, 3, 1, 1),
        (262, 3, 1, 1),
        (273, 4, n_strips, so),
        (277, 3, 1, 1),
        (278, 4, 1, rows_per_strip),
        (279, 4, n_strips, sc),
        (339, 3, 1, 3),
        (TAG_MODEL_PIXEL_SCALE, 12, 3, scale_off),
        (TAG_MODEL_TIEPOINT, 12, 6, tie_off),
        (TAG_GEO_KEY_DIRECTORY, 3, 8, gk_off),
    ];
    entries.sort_by_key(|e| e.0);
    let mut ifd = Vec::new();
    ifd.extend_from_slice(&(entries.len() as u16).to_le_bytes());
    for (tag, typ, count, value) in entries {
        ifd.extend_from_slice(&tag.to_le_bytes());
        ifd.extend_from_slice(&typ.to_le_bytes());
        ifd.extend_from_slice(&count.to_le_bytes());
        ifd.extend_from_slice(&value.to_le_bytes());
    }
    ifd.extend_from_slice(&0u32.to_le_bytes());
    out.extend_from_slice(&ifd);
    let ifd_bytes = ifd_off.to_le_bytes();
    out[4..8].copy_from_slice(&ifd_bytes);
    std::fs::write(path, &out).map_err(|e| TerrainError::Cog(format!("write {path:?}: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    /// Hand-built minimal little-endian classic TIFF: single band Float32,
    /// striped (rows_per_strip = 4), with ModelPixelScale/Tiepoint (+optional
    /// GeoKeyDirectory) — deterministic, no encoder-API dependency.
    fn build_tiff(
        width: u32,
        height: u32,
        rows_per_strip: u32,
        value: impl Fn(u32, u32) -> f32,
        pixel_is_point: bool,
    ) -> Vec<u8> {
        build_tiff_with(width, height, rows_per_strip, value, pixel_is_point, None, false)
    }

    /// As `build_tiff`, one strip stored as no bytes at offset 0 when
    /// `empty` names it, and georeferenced by a ModelTransformationTag in
    /// place of scale and tiepoint when `transformation`.
    fn build_tiff_with(
        width: u32,
        height: u32,
        rows_per_strip: u32,
        value: impl Fn(u32, u32) -> f32,
        pixel_is_point: bool,
        empty: Option<u32>,
        transformation: bool,
    ) -> Vec<u8> {
        let mut out: Vec<u8> = Vec::new();
        out.extend_from_slice(&[0x49, 0x49, 42, 0]); // II, magic
        out.extend_from_slice(&[0u8; 4]); // IFD offset patched later

        // Pixel data, strip by strip.
        let n_strips = height.div_ceil(rows_per_strip);
        let mut strip_offsets = Vec::new();
        let mut strip_counts = Vec::new();
        for s in 0..n_strips {
            if empty == Some(s) {
                strip_offsets.push(0);
                strip_counts.push(0);
                continue;
            }
            strip_offsets.push(out.len() as u32);
            let rows = rows_per_strip.min(height - s * rows_per_strip);
            for r in 0..rows {
                for c in 0..width {
                    out.extend_from_slice(&value(c, s * rows_per_strip + r).to_le_bytes());
                }
            }
            strip_counts.push(rows * width * 4);
        }

        // Auxiliary arrays (dwords aligned).
        let put_u32s = |out: &mut Vec<u8>, v: &[u32]| -> u32 {
            let off = out.len() as u32;
            for x in v {
                out.extend_from_slice(&x.to_le_bytes());
            }
            off
        };
        let put_f64s = |out: &mut Vec<u8>, v: &[f64]| -> u32 {
            let off = out.len() as u32;
            for x in v {
                out.extend_from_slice(&x.to_le_bytes());
            }
            off
        };
        let so_off = put_u32s(&mut out, &strip_offsets);
        let sc_off = put_u32s(&mut out, &strip_counts);
        // 10 m pixels, tiepoint at raster (0,0) ↔ world (1000, 2000).
        let scale_off = put_f64s(&mut out, &[10.0, 10.0, 0.0]);
        let tie_off = put_f64s(&mut out, &[0.0, 0.0, 0.0, 1000.0, 2000.0, 0.0]);
        let geokeys: [u16; 8] = [1, 1, 0, 1, GEOKEY_RASTER_TYPE, 0, 1, if pixel_is_point { 2 } else { 1 }];
        let gk_off = {
            let off = out.len() as u32;
            for x in geokeys {
                out.extend_from_slice(&x.to_le_bytes());
            }
            off
        };

        // The same 10 m pixels from (1000, 2000), as a matrix.
        let m_off = put_f64s(&mut out, &[
            10.0, 0.0, 0.0, 1000.0, 0.0, -10.0, 0.0, 2000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            1.0,
        ]);

        // IFD.
        let ifd_off = out.len() as u32;
        let mut entries: Vec<(u16, u16, u32, u32)> = vec![
            (256, 4, 1, width),                 // ImageWidth LONG
            (257, 4, 1, height),                // ImageLength LONG
            (258, 3, 1, 32),                    // BitsPerSample SHORT
            (259, 3, 1, 1),                     // Compression: none
            (262, 3, 1, 1),                     // Photometric: BlackIsZero
            (273, 4, n_strips, so_off),         // StripOffsets
            (277, 3, 1, 1),                     // SamplesPerPixel
            (278, 4, 1, rows_per_strip),        // RowsPerStrip
            (279, 4, n_strips, sc_off),         // StripByteCounts
            (339, 3, 1, 3),                     // SampleFormat: IEEE float
            (TAG_MODEL_PIXEL_SCALE, 12, 3, scale_off),
            (TAG_MODEL_TIEPOINT, 12, 6, tie_off),
            (TAG_GEO_KEY_DIRECTORY, 3, 8, gk_off),
        ];
        if transformation {
            entries.retain(|e| e.0 != TAG_MODEL_PIXEL_SCALE && e.0 != TAG_MODEL_TIEPOINT);
            entries.push((TAG_MODEL_TRANSFORMATION, 12, 16, m_off));
        }
        entries.sort_by_key(|e| e.0);
        let mut ifd = Vec::new();
        ifd.extend_from_slice(&(entries.len() as u16).to_le_bytes());
        for (tag, typ, count, value) in entries {
            ifd.extend_from_slice(&tag.to_le_bytes());
            ifd.extend_from_slice(&typ.to_le_bytes());
            ifd.extend_from_slice(&count.to_le_bytes());
            // Single-strip images inline the offset/count values directly.
            ifd.extend_from_slice(&value.to_le_bytes());
        }
        ifd.extend_from_slice(&0u32.to_le_bytes()); // next IFD = none
        out.extend_from_slice(&ifd);
        out[4..8].copy_from_slice(&ifd_off.to_le_bytes());
        out
    }

    fn ramp(c: u32, r: u32) -> f32 {
        (c * 100 + r) as f32
    }

    #[test]
    fn a_chunk_stored_as_no_bytes_is_no_data() {
        let bytes = build_tiff_with(6, 8, 4, ramp, false, Some(1), false);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        assert_eq!(cog.pixel(2, 1).unwrap(), ramp(2, 1));
        assert!(cog.pixel(2, 5).unwrap().is_nan());
    }

    #[test]
    fn a_model_transformation_places_the_image_as_scale_and_tiepoint_do() {
        let plain = *CogReader::from_reader(Cursor::new(build_tiff(6, 8, 4, ramp, false)))
            .unwrap()
            .meta();
        let bytes = build_tiff_with(6, 8, 4, ramp, false, None, true);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        assert_eq!(*cog.meta(), plain);
        assert_eq!(cog.pixel(3, 2).unwrap(), ramp(3, 2));
    }

    #[test]
    fn meta_and_pixels_across_strips() {
        let bytes = build_tiff(6, 8, 4, ramp, false);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        let m = *cog.meta();
        assert_eq!((m.width, m.height), (6, 8));
        // Area convention: center of pixel (0,0) = corner + half pixel;
        // dy negative (north-up).
        assert_eq!(m.dx, 10.0);
        assert_eq!(m.dy, -10.0);
        assert!((m.origin.x - 1005.0).abs() < 1e-9);
        assert!((m.origin.y - 1995.0).abs() < 1e-9);
        // Pixels from both strips (row 2 in strip 0, row 6 in strip 1).
        assert_eq!(cog.pixel(3, 2).unwrap(), 302.0);
        assert_eq!(cog.pixel(5, 6).unwrap(), 506.0);
        assert!(cog.pixel(6, 0).is_err());
    }

    #[test]
    fn point_convention_keeps_tiepoint_as_center() {
        let bytes = build_tiff(4, 4, 4, ramp, true);
        let cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        assert!((cog.meta().origin.x - 1000.0).abs() < 1e-9);
        assert!((cog.meta().origin.y - 2000.0).abs() < 1e-9);
    }

    #[test]
    fn world_sampling_is_bilinear() {
        let bytes = build_tiff(6, 8, 4, ramp, true);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        // Exactly at pixel (1, 1): world (1010, 1990) → 101.
        let v = cog.sample(Xy { x: 1010.0, y: 1990.0 }).unwrap().unwrap();
        assert!((v - 101.0).abs() < 1e-4);
        // Halfway between columns 1 and 2 at row 1 → 151.
        let v = cog.sample(Xy { x: 1015.0, y: 1990.0 }).unwrap().unwrap();
        assert!((v - 151.0).abs() < 1e-4);
        // Outside.
        assert!(cog.sample(Xy { x: 0.0, y: 0.0 }).unwrap().is_none());
    }

    #[test]
    fn writer_roundtrips_through_reader() {
        let g = Grid::with_axes(
            Xy { x: 500.0, y: 900.0 },
            30.0,
            -30.0,
            300,
            300,
            (0..300 * 300).map(|i| (i % 977) as f32 * 0.25).collect(),
        )
        .unwrap();
        let dir = std::env::temp_dir().join("planner_cog_roundtrip");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("rt.tif");
        write_geotiff_f32(&path, &g).unwrap();
        let mut cog = CogReader::open(&path).unwrap();
        let m = *cog.meta();
        assert_eq!((m.width, m.height), (300, 300));
        assert_eq!((m.dx, m.dy), (30.0, -30.0));
        assert!((m.origin.x - 500.0).abs() < 1e-9 && (m.origin.y - 900.0).abs() < 1e-9);
        // Spot pixels across both strips (rows_per_strip = 256).
        assert_eq!(cog.pixel(0, 0).unwrap(), g.data[0]);
        assert_eq!(cog.pixel(299, 299).unwrap(), g.data[300 * 300 - 1]);
        assert_eq!(cog.pixel(7, 260).unwrap(), g.data[260 * 300 + 7]);
    }

    /// The direct row reader must return BYTE-IDENTICAL values to the decoder.
    ///
    /// This is the only thing standing between a 200x speed-up and silently
    /// planning against terrain read from the wrong file offsets. A window is
    /// checked cell by cell, not by a summary statistic, and specifically one
    /// that STRADDLES a strip boundary and does not start at column zero --
    /// the two ways the offset arithmetic can be wrong while still producing
    /// entirely plausible-looking ground.
    #[test]
    fn the_direct_row_reader_agrees_with_the_decoder_cell_for_cell() {
        // A value that is unique per cell, so a row or column shift cannot
        // coincidentally match.
        let g = Grid::with_axes(
            Xy { x: 500.0, y: 900.0 },
            10.0,
            -10.0,
            97,
            140,
            (0..97 * 140).map(|i| i as f32 * 0.5 - 1000.0).collect(),
        )
        .unwrap();
        let dir = std::env::temp_dir().join("planner_cog_directrows");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("dr.tif");
        write_geotiff_f32(&path, &g).unwrap();

        let mut fast = CogReader::open(&path).unwrap();
        assert!(
            fast.raw_rows.is_some(),
            "an uncompressed single-band strip TIFF is exactly the case the \
             direct reader exists for; if detection rejects it the fast path \
             is dead code and the speed-up is imaginary"
        );
        // Same file, direct path disabled: this is the reference.
        let mut slow = CogReader::open(&path).unwrap();
        slow.raw_rows = None;

        // A box that starts mid-row and spans the rows_per_strip = 256
        // boundary is impossible here (140 rows), so also walk every row of
        // the image through the two paths at a non-zero start column.
        let m = *fast.meta();
        let corner = |c: f64, r: f64| Xy {
            x: m.origin.x + c * m.dx,
            y: m.origin.y + r * m.dy,
        };
        for (c0, r0, c1, r1) in
            [(0.0, 0.0, 96.0, 139.0), (13.0, 5.0, 61.0, 122.0), (96.0, 139.0, 96.0, 139.0)]
        {
            let a = fast.window(corner(c0, r0), corner(c1, r1)).unwrap();
            let b = slow.window(corner(c0, r0), corner(c1, r1)).unwrap();
            assert_eq!((a.width, a.height), (b.width, b.height));
            assert_eq!(a.data, b.data, "window ({c0},{r0})-({c1},{r1}) differs");
            // And against the source grid, so a shared bug in both paths
            // cannot pass.
            for r in 0..a.height {
                for c in 0..a.width {
                    let (sc, sr) = (c0 as usize + c, r0 as usize + r);
                    assert_eq!(
                        a.data[r * a.width + c],
                        g.data[sr * 97 + sc],
                        "cell ({sc},{sr})"
                    );
                }
            }
        }
    }

    /// Detection must REFUSE anything whose byte arithmetic it cannot prove.
    ///
    /// A palette image is the case that already had its own raw path, and it
    /// must not also acquire this one: the two would fight over the same file
    /// and the palette indices are not samples.
    #[test]
    fn the_direct_row_reader_is_not_attached_to_images_it_cannot_address() {
        let g = Grid::with_axes(
            Xy { x: 0.0, y: 0.0 },
            1.0,
            -1.0,
            4,
            4,
            (0..16).map(|i| i as f32).collect(),
        )
        .unwrap();
        let dir = std::env::temp_dir().join("planner_cog_directrows_neg");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("ok.tif");
        write_geotiff_f32(&path, &g).unwrap();
        // The writer emits exactly the recognised shape, so this one attaches.
        assert!(CogReader::open(&path).unwrap().raw_rows.is_some());

        // Truncating the file leaves the tags claiming strips that are not
        // there. Detection cannot see that, but the read must fail loudly
        // rather than return whatever bytes follow.
        let mut bytes = std::fs::read(&path).unwrap();
        let short = dir.join("short.tif");
        bytes.truncate(bytes.len() / 2);
        std::fs::write(&short, &bytes).unwrap();
        if let Ok(mut c) = CogReader::open(&short) {
            let m = *c.meta();
            let r = c.window(m.origin, Xy { x: m.origin.x + 3.0, y: m.origin.y - 3.0 });
            assert!(r.is_err(), "a truncated file must not read as valid terrain");
        }
    }

    #[test]
    fn window_materializes_subgrid_and_feeds_profiles() {
        let bytes = build_tiff(6, 8, 4, |c, r| (c + r) as f32, true);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        // Columns 1..=4, rows 1..=6 (y from 1990 down to 1940).
        let g = cog
            .window(Xy { x: 1010.0, y: 1990.0 }, Xy { x: 1040.0, y: 1940.0 })
            .unwrap();
        assert_eq!((g.width, g.height), (4, 6));
        assert_eq!(g.dy_m, -10.0);
        // g[0,0] = pixel (1,1) = 2; g[3,5] = pixel (4,6) = 10.
        assert_eq!(g.data[0], 2.0);
        assert_eq!(g.data[5 * 4 + 3], 10.0);
        // Profile across the window in world coordinates.
        let p = crate::extract_profile(
            &g,
            None,
            Xy { x: 1010.0, y: 1970.0 },
            Xy { x: 1040.0, y: 1970.0 },
            10.0,
        )
        .unwrap();
        // Along row 3 (y=1970): values (c + 3) for c = 1..=4.
        let hs: Vec<f32> = p.points.iter().map(|pt| pt.h_terrain_m).collect();
        assert_eq!(hs, vec![4.0, 5.0, 6.0, 7.0]);
    }

    /// A strided window must land on the SAME ground as the full one.
    ///
    /// This is the only thing standing between never allocating 1.57 GB to
    /// serve one screen and quietly shifting the whole base map by a cell or
    /// two — which on a hillshade looks like terrain, not like a bug. So the
    /// check is world-coordinate for world-coordinate: every cell of the
    /// strided grid must equal the full-resolution pixel at the position the
    /// strided grid CLAIMS that cell is at.
    #[test]
    fn a_strided_window_reads_the_same_ground_the_full_one_does() {
        // Unique per cell, so a row or column shift cannot coincidentally
        // match: 41 is coprime with both dimensions and both strides.
        let bytes = build_tiff(41, 37, 4, |c, r| (r * 41 + c) as f32, true);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        let (lo, hi) = (Xy { x: 1010.0, y: 1990.0 }, Xy { x: 1390.0, y: 1650.0 });
        let full = cog.window(lo, hi).unwrap();
        for (mw, mh) in [(9usize, 9usize), (13, 5), (40, 40)] {
            let s = cog.window_max(lo, hi, mw, mh).unwrap();
            assert!(s.width <= mw && s.height <= mh, "{}x{} exceeds {mw}x{mh}", s.width, s.height);
            // The declared step must BE the stride, or every sampler downstream
            // places these cells at the unstrided spacing and the layer
            // silently shrinks toward the top-left corner of the view.
            let sc = (s.dx_m / full.dx_m).round() as usize;
            let sr = (s.dy_m / full.dy_m).round() as usize;
            assert!(sc >= 1 && sr >= 1);
            assert_eq!(s.width, full.width.div_ceil(sc));
            assert_eq!(s.height, full.height.div_ceil(sr));
            for row in 0..s.height {
                for col in 0..s.width {
                    let want = full.data[(row * sr) * full.width + col * sc];
                    let got = s.data[row * s.width + col];
                    assert_eq!(got, want, "stride {sc}x{sr} at {col},{row}");
                    // And the same point in WORLD space, which is what every
                    // caller actually asks for.
                    let p = Xy {
                        x: s.origin.x + col as f64 * s.dx_m,
                        y: s.origin.y + row as f64 * s.dy_m,
                    };
                    assert_eq!(full.sample_nearest(p), Some(want), "world lookup at {col},{row}");
                }
            }
        }
    }

    /// Asking for at least as many cells as the box holds returns the box.
    ///
    /// The zoomed-IN case is the common one and must not pay a stride, nor
    /// take a different code path that could drift from `window`.
    #[test]
    fn a_window_that_fits_is_not_strided_at_all() {
        let bytes = build_tiff(6, 8, 4, |c, r| (c + r) as f32, true);
        let mut cog = CogReader::from_reader(Cursor::new(bytes)).unwrap();
        let (lo, hi) = (Xy { x: 1010.0, y: 1990.0 }, Xy { x: 1040.0, y: 1940.0 });
        let full = cog.window(lo, hi).unwrap();
        let s = cog.window_max(lo, hi, 4, 6).unwrap();
        assert_eq!((s.width, s.height), (full.width, full.height));
        assert_eq!(s.dx_m, full.dx_m);
        assert_eq!(s.dy_m, full.dy_m);
        assert_eq!(s.data, full.data);
    }

    /// The row decoder converts every layout it takes exactly as the
    /// per-sample match it replaced did, bit for bit, NaNs included.
    #[test]
    fn decode_into_converts_as_the_per_sample_match_did() {
        fn per_sample(format: u16, bps: usize, bytes: &[u8], out: &mut [f32]) {
            for (i, v) in out.iter_mut().enumerate() {
                let b = &bytes[i * bps..(i + 1) * bps];
                *v = match (format, bps) {
                    (3, 4) => f32::from_le_bytes([b[0], b[1], b[2], b[3]]),
                    (3, 8) => f64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]])
                        as f32,
                    (2, 2) => i16::from_le_bytes([b[0], b[1]]) as f32,
                    (2, 4) => i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32,
                    (1, 1) => b[0] as f32,
                    (1, 2) => u16::from_le_bytes([b[0], b[1]]) as f32,
                    (1, 4) => u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32,
                    _ => unreachable!(),
                };
            }
        }
        // Every byte value in every position, and patterns that make NaNs,
        // infinities and subnormals in the float layouts.
        let bytes: Vec<u8> = (0..4096u32).map(|i| (i.wrapping_mul(2_654_435_761) >> 13) as u8).collect();
        for (format, bps) in [(3, 4), (3, 8), (2, 2), (2, 4), (1, 1), (1, 2), (1, 4)] {
            let raw = RawRows {
                file: File::open(std::env::current_exe().unwrap()).unwrap(),
                offsets: vec![0],
                rows_per_strip: 1,
                bytes_per_sample: bps as u32,
                sample_format: format,
            };
            let n = bytes.len() / bps;
            let (mut want, mut got) = (vec![0f32; n], vec![0f32; n]);
            per_sample(format, bps, &bytes, &mut want);
            raw.decode_into(&bytes, &mut got).unwrap();
            let bits = |v: &[f32]| v.iter().map(|x| x.to_bits()).collect::<Vec<_>>();
            assert_eq!(bits(&got), bits(&want), "format {format} at {bps} byte(s)");
        }
        let odd = RawRows {
            file: File::open(std::env::current_exe().unwrap()).unwrap(),
            offsets: vec![0],
            rows_per_strip: 1,
            bytes_per_sample: 3,
            sample_format: 1,
        };
        assert!(odd.decode_into(&bytes, &mut [0f32; 4]).is_err());
    }

    /// The shared reader gives `window`'s grid, cell for cell, box for box,
    /// strip boundaries included: a server may read without the lock only if
    /// what it reads is what it read with it.
    #[cfg(unix)]
    #[test]
    fn shared_rows_read_what_window_reads() {
        // 300 rows, so a box can span the writer's 256-row strips.
        let g = Grid::with_axes(
            Xy { x: 399_000.0, y: 5_800_000.0 },
            5.0,
            -5.0,
            97,
            300,
            (0..97 * 300).map(|i| (i as f32).sin() * 50.0 + 30.0).collect(),
        )
        .unwrap();
        let dir = std::env::temp_dir().join("planner_cog_sharedrows");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("sr.tif");
        write_geotiff_f32(&path, &g).unwrap();

        let mut locked = CogReader::open(&path).unwrap();
        let shared = locked.shared_rows().expect("a strip TIFF is directly readable");
        let mut slow = CogReader::open(&path).unwrap();
        slow.raw_rows = None;
        let m = *locked.meta();
        assert_eq!(*shared.meta(), m);
        let at = |c: f64, r: f64| Xy { x: m.origin.x + c * m.dx, y: m.origin.y + r * m.dy };
        for (c0, r0, c1, r1) in [
            (0.0, 0.0, 96.0, 299.0),
            (13.2, 5.7, 61.4, 122.1),
            (40.0, 250.0, 70.0, 262.0), // across the strip boundary at row 256
            (96.0, 299.0, 96.0, 299.0),
            (61.4, 122.1, 13.2, 5.7), // corners given the other way round
        ] {
            let want = locked.window(at(c0, r0), at(c1, r1)).unwrap();
            let got = shared.window(at(c0, r0), at(c1, r1)).unwrap().expect("covered");
            let reference = slow.window(at(c0, r0), at(c1, r1)).unwrap();
            for other in [&got, &reference] {
                assert_eq!((other.width, other.height), (want.width, want.height));
                assert_eq!((other.origin, other.dx_m, other.dy_m), (want.origin, want.dx_m, want.dy_m));
                assert_eq!(other.data, want.data);
            }
        }
        assert!(shared.window(at(0.0, 0.0), at(97.0, 10.0)).is_err(), "past the edge, as window");
        assert!(locked.window(at(0.0, 0.0), at(97.0, 10.0)).is_err());
    }

    /// The shared reader's strided window is `window_max`'s, cell for cell
    /// and stride for stride, whether or not a stride applies: the tiles read
    /// through it, and a tile must not move by a cell for having skipped the
    /// lock.
    #[cfg(unix)]
    #[test]
    fn shared_rows_read_what_window_max_reads() {
        let g = Grid::with_axes(
            Xy { x: 399_000.0, y: 5_800_000.0 },
            5.0,
            -5.0,
            97,
            300,
            (0..97 * 300).map(|i| (i as f32 * 0.37).cos() * 40.0 + 20.0).collect(),
        )
        .unwrap();
        let dir = std::env::temp_dir().join("planner_cog_sharedrows_max");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("srm.tif");
        write_geotiff_f32(&path, &g).unwrap();

        let mut locked = CogReader::open(&path).unwrap();
        let shared = locked.shared_rows().expect("a strip TIFF is directly readable");
        let m = *locked.meta();
        let at = |c: f64, r: f64| Xy { x: m.origin.x + c * m.dx, y: m.origin.y + r * m.dy };
        for (c0, r0, c1, r1) in [
            (0.0, 0.0, 96.0, 299.0),
            (13.2, 5.7, 61.4, 122.1),
            (40.0, 250.0, 70.0, 262.0), // across the strip boundary at row 256
            (61.4, 299.0, 13.2, 5.7),   // corners given the other way round
        ] {
            for (mw, mh) in [(9usize, 9usize), (13, 40), (40, 13), (1, 1), (4096, 4096)] {
                let want = locked.window_max(at(c0, r0), at(c1, r1), mw, mh).unwrap();
                let got =
                    shared.window_max(at(c0, r0), at(c1, r1), mw, mh).unwrap().expect("covered");
                assert_eq!((got.width, got.height), (want.width, want.height));
                assert_eq!((got.origin, got.dx_m, got.dy_m), (want.origin, want.dx_m, want.dy_m));
                assert_eq!(got.data, want.data, "box ({c0},{r0})-({c1},{r1}) at {mw}x{mh}");
            }
        }
        assert!(shared.window_max(at(0.0, 0.0), at(97.0, 10.0), 9, 9).is_err(), "past the edge");
    }

    /// GDAL's internal mask follows the image it masks, the same size, with
    /// NewSubfileType 4: a reader choosing a level must not take it for one.
    #[test]
    fn a_mask_is_never_taken_for_a_level() {
        use tiff::encoder::{colortype, TiffEncoder};
        let path = std::env::temp_dir().join(format!("planner_masked_{}.tif", std::process::id()));
        {
            let mut enc = TiffEncoder::new(File::create(&path).unwrap()).unwrap();
            // The image: 4 × 4 pixels of 1 m, each 10.
            let mut image = enc.new_image::<colortype::Gray32Float>(4, 4).unwrap();
            let scale = [1.0f64, 1.0, 0.0];
            let tie = [0.0f64, 0.0, 0.0, 1000.0, 2000.0, 0.0];
            image
                .encoder()
                .write_tag(Tag::Unknown(TAG_MODEL_PIXEL_SCALE), &scale[..])
                .unwrap();
            image
                .encoder()
                .write_tag(Tag::Unknown(TAG_MODEL_TIEPOINT), &tie[..])
                .unwrap();
            image.write_data(&[10.0f32; 16]).unwrap();
            // Its mask, 255 where the image is valid.
            let mut mask = enc.new_image::<colortype::Gray8>(4, 4).unwrap();
            mask.encoder().write_tag(Tag::NewSubfileType, 4u32).unwrap();
            mask.write_data(&[255u8; 16]).unwrap();
            // An overview at 2 m, each pixel 20.
            let mut overview = enc.new_image::<colortype::Gray32Float>(2, 2).unwrap();
            overview
                .encoder()
                .write_tag(Tag::NewSubfileType, 1u32)
                .unwrap();
            overview.write_data(&[20.0f32; 4]).unwrap();
        }
        // Finer than the overview: the image itself, not its mask.
        let mut r = CogReader::open_level(&path, 1.5).unwrap();
        assert_eq!((r.meta().width, r.pixel(0, 0).unwrap()), (4, 10.0));
        // Where the overview suits, the overview.
        let mut r = CogReader::open_level(&path, 2.0).unwrap();
        assert_eq!((r.meta().width, r.pixel(0, 0).unwrap()), (2, 20.0));
        std::fs::remove_file(&path).unwrap();
    }
}
