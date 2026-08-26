use std::fs::File;
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use image::RgbaImage;
use oxideav_core::{Demuxer, Error as OxideError, ExecutionContext, NullCodecResolver};
use oxideav_mkv::demux::{open_typed, AlphaMode};
use oxideav_vp9::Vp9SequenceDecoder;

const WEBM_ALPHA_BLOCK_ID: u64 = 1;
const DEFAULT_FRAME_INTERVAL: Duration = Duration::from_nanos(33_333_333);

pub enum IdleWebmMessage {
    Frame(RgbaImage),
    Failed(String),
}

pub struct IdleWebmPlayback {
    decoder: IdleWebmDecoder,
    frame_interval: Duration,
}

impl IdleWebmPlayback {
    pub fn open(path: &std::path::Path) -> Result<Self, String> {
        let file = File::open(path).map_err(|error| error.to_string())?;
        let mut demuxer = open_typed(Box::new(file), &NullCodecResolver)
            .map_err(|error| format_oxide_error(error))?;
        let video_stream = demuxer
            .streams()
            .iter()
            .find(|stream| stream.params.codec_id.as_str() == "vp9")
            .map(|stream| stream.index)
            .ok_or_else(|| "WebM has no VP9 video stream".to_string())?;
        if demuxer.video_alpha_mode(video_stream) != Some(AlphaMode::Present) {
            return Err("WebM VP9 video does not declare an alpha side-stream".into());
        }

        let stream_info = demuxer
            .streams()
            .iter()
            .find(|stream| stream.index == video_stream)
            .ok_or_else(|| "WebM video stream metadata is unavailable".to_string())?;
        let width = stream_info
            .params
            .width
            .ok_or_else(|| "WebM video width is unavailable".to_string())?;
        let height = stream_info
            .params
            .height
            .ok_or_else(|| "WebM video height is unavailable".to_string())?;
        if width == 0 || height == 0 || width > 4096 || height > 4096 {
            return Err(format!("unsupported WebM dimensions: {width}x{height}"));
        }

        let frame_interval = demuxer
            .track_timing(video_stream)
            .and_then(|timing| timing.default_duration())
            .map(Duration::from_nanos)
            .filter(|duration| !duration.is_zero())
            .unwrap_or(DEFAULT_FRAME_INTERVAL);
        let mut frames = Vec::new();
        loop {
            match demuxer.next_packet() {
                Ok(packet) if packet.stream_index == video_stream => {
                    let alpha = demuxer
                        .block_additions()
                        .iter()
                        .find(|addition| addition.block_add_id() == WEBM_ALPHA_BLOCK_ID)
                        .map(|addition| addition.data().to_vec())
                        .ok_or_else(|| {
                            format!(
                                "WebM VP9 frame {} has no alpha BlockAdditional",
                                frames.len()
                            )
                        })?;
                    frames.push((packet.data, alpha));
                }
                Ok(_) => {}
                Err(error) if error.is_eof() => break,
                Err(error) => return Err(format_oxide_error(error)),
            }
        }
        if frames.is_empty() {
            return Err("WebM contains no video frames".into());
        }

        Ok(Self {
            decoder: IdleWebmDecoder {
                frames,
                width,
                height,
                color: new_vp9_decoder(),
                alpha: new_vp9_decoder(),
                next_frame: 0,
            },
            frame_interval,
        })
    }

    pub fn spawn(
        mut self,
        host_hwnd: isize,
        stop: Arc<AtomicBool>,
        generation: u64,
        frame_message_pending: Arc<AtomicBool>,
    ) -> Receiver<IdleWebmMessage> {
        let (sender, receiver) = mpsc::sync_channel(2);
        thread::spawn(move || {
            self.run(
                sender,
                host_hwnd,
                stop,
                generation,
                frame_message_pending,
            )
        });
        receiver
    }

    fn run(
        &mut self,
        sender: SyncSender<IdleWebmMessage>,
        host_hwnd: isize,
        stop: Arc<AtomicBool>,
        generation: u64,
        frame_message_pending: Arc<AtomicBool>,
    ) {
        crate::log_line(format!(
            "avatar idle WebM worker started: generation={}, thread={:?}",
            generation,
            thread::current().id()
        ));
        let mut deadline = Instant::now();
        loop {
            if stop.load(std::sync::atomic::Ordering::Acquire) {
                crate::log_line(format!(
                    "avatar idle WebM worker stopped: reason=stop-requested, generation={}, thread={:?}",
                    generation,
                    thread::current().id()
                ));
                return;
            }
            match self.decoder.next_frame() {
                Ok(frame) => match sender.try_send(IdleWebmMessage::Frame(frame)) {
                    Ok(()) => notify_frame_message(
                        host_hwnd,
                        generation,
                        &frame_message_pending,
                    ),
                    Err(TrySendError::Full(_)) => notify_frame_message(
                        host_hwnd,
                        generation,
                        &frame_message_pending,
                    ),
                    Err(TrySendError::Disconnected(_)) => {
                        crate::log_line(format!(
                            "avatar idle WebM worker stopped: reason=receiver-disconnected, generation={}, thread={:?}",
                            generation,
                            thread::current().id()
                        ));
                        return;
                    }
                },
                Err(error) => {
                    crate::log_line(format!(
                        "avatar idle WebM worker decode failed: generation={}, error={}",
                        generation, error
                    ));
                    let _ = sender.try_send(IdleWebmMessage::Failed(error));
                    notify_frame_message(
                        host_hwnd,
                        generation,
                        &frame_message_pending,
                    );
                    return;
                }
            }
            deadline += self.frame_interval;
            if let Some(remaining) = deadline.checked_duration_since(Instant::now()) {
                thread::sleep(remaining);
            } else {
                deadline = Instant::now();
            }
        }
    }
}

struct IdleWebmDecoder {
    frames: Vec<(Vec<u8>, Vec<u8>)>,
    width: u32,
    height: u32,
    color: Vp9SequenceDecoder,
    alpha: Vp9SequenceDecoder,
    next_frame: usize,
}

impl IdleWebmDecoder {
    fn next_frame(&mut self) -> Result<RgbaImage, String> {
        if self.next_frame == self.frames.len() {
            self.color = new_vp9_decoder();
            self.alpha = new_vp9_decoder();
            self.next_frame = 0;
        }
        let (color_packet, alpha_packet) = &self.frames[self.next_frame];
        self.next_frame += 1;
        let color = self
            .color
            .push_frame(color_packet)
            .map_err(|error| {
                format!(
                    "VP9 colour decode failed at frame {}: {error:?}",
                    self.next_frame - 1
                )
            })?
            .ok_or_else(|| format!("VP9 colour frame {} is hidden", self.next_frame - 1))?;
        let alpha = self
            .alpha
            .push_frame(alpha_packet)
            .map_err(|error| {
                format!(
                    "VP9 alpha decode failed at frame {}: {error:?}",
                    self.next_frame - 1
                )
            })?
            .ok_or_else(|| format!("VP9 alpha frame {} is hidden", self.next_frame - 1))?;
        if color.width != self.width || color.height != self.height {
            return Err(format!(
                "VP9 colour dimensions changed to {}x{}",
                color.width, color.height
            ));
        }
        if alpha.width != self.width || alpha.height != self.height {
            return Err(format!(
                "VP9 alpha dimensions changed to {}x{}",
                alpha.width, alpha.height
            ));
        }
        Ok(yuv420_with_alpha(&color, &alpha))
    }
}

fn yuv420_with_alpha(
    color: &oxideav_vp9::Vp9DecodedFrame,
    alpha: &oxideav_vp9::Vp9DecodedFrame,
) -> RgbaImage {
    let width = color.width as usize;
    let height = color.height as usize;
    let chroma_width =
        (width + usize::from(color.subsampling_x)) >> usize::from(color.subsampling_x);
    let mut pixels = vec![0u8; width * height * 4];
    for y in 0..height {
        for x in 0..width {
            let luma_index = y * width + x;
            let chroma_index = (y >> usize::from(color.subsampling_y)) * chroma_width
                + (x >> usize::from(color.subsampling_x));
            let y_sample = sample_u8(color.y[luma_index], color.bit_depth);
            let u_sample = sample_u8(
                color.u[chroma_index.min(color.u.len() - 1)],
                color.bit_depth,
            );
            let v_sample = sample_u8(
                color.v[chroma_index.min(color.v.len() - 1)],
                color.bit_depth,
            );
            let alpha_sample = sample_u8(alpha.y[luma_index], alpha.bit_depth);
            let c = i32::from(y_sample) - 16;
            let d = i32::from(u_sample) - 128;
            let e = i32::from(v_sample) - 128;
            let red = ((298 * c + 459 * e + 128) >> 8).clamp(0, 255) as u8;
            let green = ((298 * c - 55 * d - 136 * e + 128) >> 8).clamp(0, 255) as u8;
            let blue = ((298 * c + 541 * d + 128) >> 8).clamp(0, 255) as u8;
            let offset = (y * width + x) * 4;
            pixels[offset..offset + 4].copy_from_slice(&[red, green, blue, alpha_sample]);
        }
    }
    RgbaImage::from_raw(color.width, color.height, pixels)
        .expect("validated VP9 dimensions must match RGBA buffer")
}

fn sample_u8(sample: u16, bit_depth: u8) -> u8 {
    if bit_depth <= 8 {
        sample.min(255) as u8
    } else {
        let max = (1u32 << bit_depth) - 1;
        ((u32::from(sample) * 255 + max / 2) / max).min(255) as u8
    }
}

fn new_vp9_decoder() -> Vp9SequenceDecoder {
    let mut decoder = Vp9SequenceDecoder::new();
    let execution = ExecutionContext::auto();
    decoder.set_execution_context(&execution);
    decoder
}

fn format_oxide_error(error: OxideError) -> String {
    error.to_string()
}

fn notify_frame_message(
    host_hwnd: isize,
    generation: u64,
    pending: &Arc<AtomicBool>,
) {
    if pending.swap(true, Ordering::AcqRel) {
        return;
    }
    unsafe {
        if let Err(error) = windows::Win32::UI::WindowsAndMessaging::PostMessageW(
            Some(windows::Win32::Foundation::HWND(
                host_hwnd as *mut std::ffi::c_void,
            )),
            crate::IDLE_WEBM_FRAME_MESSAGE,
            windows::Win32::Foundation::WPARAM(generation as usize),
            windows::Win32::Foundation::LPARAM(0),
        ) {
            pending.store(false, Ordering::Release);
            crate::log_line(format!(
                "avatar idle WebM frame notification post failed: generation={}, error={}",
                generation, error
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn idle_webm_decodes_a_real_alpha_frame() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("assets/avatar/ariadne_idle_small.webm");
        let playback = IdleWebmPlayback::open(&path).expect("idle WebM should validate");
        assert_eq!(playback.decoder.width, 168);
        assert_eq!(playback.decoder.height, 300);
        assert_eq!(playback.decoder.frames.len(), 450);
        let mut decoder = playback.decoder;
        for index in 0..450 {
            let frame = decoder
                .next_frame()
                .unwrap_or_else(|error| panic!("frame {index} should decode: {error}"));
            assert_eq!(frame.dimensions(), (168, 300));
            if index == 0 || index == 449 {
                let transparent = frame.pixels().filter(|pixel| pixel[3] == 0).count();
                let opaque = frame.pixels().filter(|pixel| pixel[3] == 255).count();
                assert!(transparent > 0);
                assert!(opaque > 0);
            }
        }
        let loop_frame = decoder.next_frame().expect("WebM should loop cleanly");
        assert_eq!(loop_frame.dimensions(), (168, 300));
    }
}
