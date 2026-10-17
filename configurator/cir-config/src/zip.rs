//! The part of Python's `zipfile` the scanner uses: `is_zipfile` and the
//! central-directory member names. Members are never read or extracted.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

const END_SIZE: u64 = 22;
const END64_SIZE: u64 = 56;
const END64_LOCATOR_SIZE: u64 = 20;
const CENTRAL_SIZE: usize = 46;

/// A malformed archive (`zipfile.BadZipFile`), or an archive Python refuses
/// with `NotImplementedError` (a RuntimeError).
#[derive(Debug)]
pub enum ZipError {
    Bad(String),
    NotImplemented(String),
    Io(io::Error),
}

impl From<io::Error> for ZipError {
    fn from(error: io::Error) -> Self {
        ZipError::Io(error)
    }
}

struct EndRecord {
    signature_is_64: bool,
    size: u64,
    offset: u64,
    location: u64,
}

fn u16_at(data: &[u8], offset: usize) -> u64 {
    u16::from_le_bytes([data[offset], data[offset + 1]]) as u64
}

fn u32_at(data: &[u8], offset: usize) -> u64 {
    u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) as u64
}

fn u64_at(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(data[offset..offset + 8].try_into().unwrap())
}

fn read_to_end_from(file: &mut File, position: u64) -> io::Result<Vec<u8>> {
    file.seek(SeekFrom::Start(position))?;
    let mut data = Vec::new();
    file.read_to_end(&mut data)?;
    Ok(data)
}

fn read_exact_at(file: &mut File, position: u64, length: u64) -> io::Result<Vec<u8>> {
    file.seek(SeekFrom::Start(position))?;
    let mut data = Vec::new();
    file.take(length).read_to_end(&mut data)?;
    Ok(data)
}

/// `_EndRecData64`: `end_offset` is the end record's position from the file end
/// (negative in Python).
fn end_record_64(file: &mut File, filesize: u64, end_position: u64, mut record: EndRecord) -> Result<EndRecord, ZipError> {
    if end_position < END64_LOCATOR_SIZE {
        return Ok(record);
    }
    let locator = read_exact_at(file, end_position - END64_LOCATOR_SIZE, END64_LOCATOR_SIZE)?;
    if locator.len() as u64 != END64_LOCATOR_SIZE || &locator[..4] != b"PK\x06\x07" {
        return Ok(record);
    }
    let disk = u32_at(&locator, 4);
    let disks = u32_at(&locator, 16);
    if disk != 0 || disks > 1 {
        return Err(ZipError::Bad("zipfiles that span multiple disks are not supported".into()));
    }
    if end_position < END64_LOCATOR_SIZE + END64_SIZE {
        // Python's seek before the start of the file fails with OSError here.
        return Err(ZipError::Io(io::Error::from_raw_os_error(22)));
    }
    let data = read_exact_at(file, end_position - END64_LOCATOR_SIZE - END64_SIZE, END64_SIZE)?;
    let _ = filesize;
    if data.len() as u64 != END64_SIZE || &data[..4] != b"PK\x06\x06" {
        return Ok(record);
    }
    record.signature_is_64 = true;
    record.size = u64_at(&data, 40);
    record.offset = u64_at(&data, 48);
    Ok(record)
}

/// `_EndRecData`: None when there is no end-of-central-directory record.
fn end_record(file: &mut File) -> Result<Option<EndRecord>, ZipError> {
    let filesize = file.seek(SeekFrom::End(0))?;
    if filesize < END_SIZE {
        return Ok(None);
    }
    let data = read_to_end_from(file, filesize - END_SIZE)?;
    if data.len() as u64 == END_SIZE && &data[..4] == b"PK\x05\x06" && data[20..22] == [0, 0] {
        let record = EndRecord {
            signature_is_64: false,
            size: u32_at(&data, 12),
            offset: u32_at(&data, 16),
            location: filesize - END_SIZE,
        };
        return end_record_64(file, filesize, filesize - END_SIZE, record).map(Some);
    }
    let start = filesize.saturating_sub((1 << 16) + END_SIZE);
    let data = read_to_end_from(file, start)?;
    let found = data.windows(4).rposition(|window| window == b"PK\x05\x06");
    let Some(index) = found else { return Ok(None) };
    if data.len() - index < END_SIZE as usize {
        return Ok(None);
    }
    let raw = &data[index..index + END_SIZE as usize];
    let record = EndRecord {
        signature_is_64: false,
        size: u32_at(raw, 12),
        offset: u32_at(raw, 16),
        location: start + index as u64,
    };
    end_record_64(file, filesize, start + index as u64, record).map(Some)
}

/// `zipfile.is_zipfile(path)`.
pub fn is_zipfile(path: &Path) -> Result<bool, ZipError> {
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(_) => return Ok(false),
    };
    match end_record(&mut file) {
        Ok(record) => Ok(record.is_some()),
        Err(ZipError::Io(_)) => Ok(false),
        Err(other) => Err(other),
    }
}

/// A central-directory member: its name as `zipfile` decodes it (non-ASCII
/// CP437 bytes become U+FFFD; they never form ASCII text) and `is_dir()`.
pub struct Member {
    pub filename: String,
    pub is_dir: bool,
}

fn decode_extra(extra: &[u8], file_size: u64, compress_size: u64, header_offset: u64) -> Result<(), ZipError> {
    let mut extra = extra;
    while extra.len() >= 4 {
        let kind = u16_at(extra, 0);
        let length = u16_at(extra, 2) as usize;
        if length + 4 > extra.len() {
            return Err(ZipError::Bad(format!("Corrupt extra field {kind:04x} (size={length})")));
        }
        if kind == 1 {
            let mut data = &extra[4..length + 4];
            let mut take = |field: &str| -> Result<(), ZipError> {
                if data.len() < 8 {
                    return Err(ZipError::Bad(format!("Corrupt zip64 extra field. {field} not found.")));
                }
                data = &data[8..];
                Ok(())
            };
            if file_size == 0xFFFF_FFFF {
                take("File size")?;
            }
            if compress_size == 0xFFFF_FFFF {
                take("Compress size")?;
            }
            if header_offset == 0xFFFF_FFFF {
                take("Header offset")?;
            }
        }
        extra = &extra[length + 4..];
    }
    Ok(())
}

/// `ZipFile(path).infolist()` names, with `_RealGetContents`' checks.
pub fn members(path: &Path) -> Result<Vec<Member>, ZipError> {
    let mut file = File::open(path)?;
    let record = match end_record(&mut file) {
        Ok(Some(record)) => record,
        Ok(None) | Err(ZipError::Io(_)) => return Err(ZipError::Bad("File is not a zip file".into())),
        Err(other) => return Err(other),
    };
    let mut concat = record.location as i128 - record.size as i128 - record.offset as i128;
    if record.signature_is_64 {
        concat -= (END64_SIZE + END64_LOCATOR_SIZE) as i128;
    }
    let start = record.offset as i128 + concat;
    if start < 0 {
        return Err(ZipError::Bad("Bad offset for central directory".into()));
    }
    let data = read_exact_at(&mut file, start as u64, record.size)?;
    let mut position = 0usize;
    let mut total = 0u64;
    let mut result = Vec::new();
    while total < record.size {
        if data.len() < position + CENTRAL_SIZE {
            return Err(ZipError::Bad("Truncated central directory".into()));
        }
        let header = &data[position..position + CENTRAL_SIZE];
        if &header[..4] != b"PK\x01\x02" {
            return Err(ZipError::Bad("Bad magic number for central directory".into()));
        }
        let extract_version = header[6] as u64;
        let flags = u16_at(header, 8);
        let compress_size = u32_at(header, 20);
        let file_size = u32_at(header, 24);
        let name_length = u16_at(header, 28) as usize;
        let extra_length = u16_at(header, 30) as usize;
        let comment_length = u16_at(header, 32) as usize;
        let header_offset = u32_at(header, 42);
        position += CENTRAL_SIZE;
        let take = |from: usize, length: usize| &data[from.min(data.len())..(from + length).min(data.len())];
        let raw_name = take(position, name_length);
        position += name_length;
        let mut filename = if flags & 0x800 != 0 {
            match std::str::from_utf8(raw_name) {
                Ok(text) => text.to_string(),
                Err(_) => return Err(ZipError::Bad("A member name is not valid UTF-8".into())),
            }
        } else {
            raw_name.iter().map(|&byte| if byte < 0x80 { byte as char } else { '\u{FFFD}' }).collect()
        };
        let extra = take(position, extra_length).to_vec();
        position += extra_length + comment_length;
        if extract_version > 63 {
            return Err(ZipError::NotImplemented(format!(
                "zip file version {:.1}",
                extract_version as f64 / 10.0
            )));
        }
        decode_extra(&extra, file_size, compress_size, header_offset)?;
        if let Some(null) = filename.find('\0') {
            filename.truncate(null);
        }
        let filename = filename.replace('\\', "/");
        let is_dir = filename.ends_with('/');
        result.push(Member { filename, is_dir });
        total += (CENTRAL_SIZE + name_length + extra_length + comment_length) as u64;
    }
    Ok(result)
}
