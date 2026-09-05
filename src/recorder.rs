use crate::config::Config;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use hound::WavSpec;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Instant;

pub struct Recorder {
    buf: Arc<Mutex<Vec<f32>>>,
    active: Arc<Mutex<bool>>,
    stream: Option<cpal::Stream>,
    sample_rate: u32,
    channels: u16,
    started: Option<Instant>,
}

impl Recorder {
    pub fn new() -> Self {
        Self {
            buf: Arc::new(Mutex::new(Vec::new())),
            active: Arc::new(Mutex::new(false)),
            stream: None,
            sample_rate: 0,
            channels: 0,
            started: None,
        }
    }

    pub fn is_recording(&self) -> bool {
        *self.active.lock().unwrap()
    }

    pub fn start(&mut self, cfg: &Config) -> Result<(), String> {
        let host = cpal::default_host();
        let device = host
            .default_input_device()
            .ok_or_else(|| "no default input device found".to_string())?;
        let supported = device
            .default_input_config()
            .map_err(|e| format!("default input config failed: {e}"))?;

        let sample_format = supported.sample_format();
        let mut stream_cfg: cpal::StreamConfig = supported.into();

        // Prefer a mono stream at the configured sample rate if the device supports it.
        let target = cfg.sample_rate;
        if let Ok(ranges) = device.supported_input_configs() {
            for range in ranges {
                if range.channels() == 1
                    && range.sample_format() == sample_format
                    && range.min_sample_rate() <= cpal::SampleRate(target)
                    && range.max_sample_rate() >= cpal::SampleRate(target)
                {
                    stream_cfg.channels = 1;
                    stream_cfg.sample_rate = cpal::SampleRate(target);
                    break;
                }
            }
        }

        let buf = Arc::clone(&self.buf);
        let active = Arc::clone(&self.active);
        *buf.lock().unwrap() = Vec::with_capacity(stream_cfg.sample_rate.0 as usize * 60);
        *active.lock().unwrap() = true;

        let rate = stream_cfg.sample_rate.0;
        let channels = stream_cfg.channels;
        let stream = build_stream_dispatch(&device, &stream_cfg, sample_format, buf, active)
            .map_err(|e| format!("failed to open input stream: {e}"))?;
        if let Err(e) = stream.play() {
            return Err(format!("failed to start stream: {e}"));
        }

        self.stream = Some(stream);
        self.sample_rate = rate;
        self.channels = channels;
        self.started = Some(Instant::now());
        log::info!("recording at {rate} Hz, {channels} ch ({sample_format:?})");
        Ok(())
    }

    /// Stops recording, writes a 16-bit PCM WAV to `out_dir/<stem>.wav`.
    /// Returns the path and duration in seconds, or None if nothing useful was captured.
    pub fn stop(&mut self, out_dir: &std::path::Path, stem: &str) -> Option<(PathBuf, f32)> {
        if let Some(stream) = self.stream.take() {
            drop(stream);
            // give the capture thread a moment to wind down
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        *self.active.lock().unwrap() = false;

        let started = self.started.take()?;
        let samples = std::mem::take(&mut *self.buf.lock().unwrap());
        if self.sample_rate == 0 || self.channels == 0 || samples.is_empty() {
            return None;
        }

        let dur = started.elapsed().as_secs_f32();
        if dur < 0.4 {
            log::info!("take too short ({dur:.2}s), discarding");
            return None;
        }

        // mixdown to mono + convert to i16
        let ch = self.channels as usize;
        let mut frames: Vec<i16> = Vec::with_capacity(samples.len() / ch);
        for frame in samples.chunks(ch) {
            if frame.len() == ch {
                let mono: f32 = frame.iter().sum::<f32>() / ch as f32;
                let v = (mono.clamp(-1.0, 1.0) * 32767.0).round();
                frames.push(v as i16);
            }
        }

        let spec = WavSpec {
            channels: 1,
            sample_rate: self.sample_rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let path = out_dir.join(format!("{stem}.wav"));
        let mut writer =
            hound::WavWriter::create(&path, spec).ok()?;
        for s in frames {
            let _ = writer.write_sample(s);
        }
        let _ = writer.finalize();
        log::info!("saved {} samples -> {}", path.display(), path.file_name().unwrap().to_string_lossy());
        Some((path, dur))
    }
}

fn build_stream<T>(
    device: &cpal::Device,
    cfg: &cpal::StreamConfig,
    buf: Arc<Mutex<Vec<f32>>>,
    active: Arc<Mutex<bool>>,
) -> Result<cpal::Stream, cpal::BuildStreamError>
where
    T: cpal::SizedSample,
    f32: cpal::FromSample<T>,
{
    device.build_input_stream(
        cfg,
        move |data: &[T], _| {
            if *active.lock().unwrap() {
                let mut out = buf.lock().unwrap();
                out.extend(data.iter().map(|s| s.to_sample::<f32>()));
            }
        },
        |e| log::error!("input stream error: {e}"),
        None,
    )
}

pub fn build_stream_dispatch(
    device: &cpal::Device,
    cfg: &cpal::StreamConfig,
    sample_format: cpal::SampleFormat,
    buf: Arc<Mutex<Vec<f32>>>,
    active: Arc<Mutex<bool>>,
) -> Result<cpal::Stream, cpal::BuildStreamError> {
    match sample_format {
        cpal::SampleFormat::F32 => build_stream::<f32>(device, cfg, buf, active),
        cpal::SampleFormat::F64 => build_stream::<f64>(device, cfg, buf, active),
        cpal::SampleFormat::I8 => build_stream::<i8>(device, cfg, buf, active),
        cpal::SampleFormat::I16 => build_stream::<i16>(device, cfg, buf, active),
        cpal::SampleFormat::I32 => build_stream::<i32>(device, cfg, buf, active),
        cpal::SampleFormat::I64 => build_stream::<i64>(device, cfg, buf, active),
        cpal::SampleFormat::U8 => build_stream::<u8>(device, cfg, buf, active),
        cpal::SampleFormat::U16 => build_stream::<u16>(device, cfg, buf, active),
        cpal::SampleFormat::U32 => build_stream::<u32>(device, cfg, buf, active),
        cpal::SampleFormat::U64 => build_stream::<u64>(device, cfg, buf, active),
        other => {
            log::warn!("unsupported input sample format {other:?}");
            Err(cpal::BuildStreamError::DeviceNotAvailable)
        }
    }
}