
# coding: utf-8
"""
Small helper CLI that extracts audio from a video file on macOS and uses
faster-whisper to generate SRT subtitles.

Setup:
    pip install faster-whisper streamlit openai
    brew install ffmpeg  # or ensure ffmpeg is on PATH

CLI:
    python mian.py /path/to/video.mp4 --model-size medium --language zh

UI:
    streamlit run streamlit_app.py
"""
import argparse
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from faster_whisper import WhisperModel


def extract_audio(video_path: Path, target_path: Path, sample_rate: int = 16000) -> None:
    """Use ffmpeg to convert the video's audio track into a mono wav file."""
    # ffmpeg命令：输入视频，去掉视频流，只保留单声道音频
    cmd = [
        "ffmpeg",
        "-y",  # overwrite temporary file
        "-i",
        str(video_path),
        "-vn",  # drop the video stream
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(target_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to extract audio.\n\n"
            f"STDOUT:\n{proc.stdout.decode(errors='ignore')}\n\n"
            f"STDERR:\n{proc.stderr.decode(errors='ignore')}"
        )


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert fractional seconds to the SRT time format."""
    delta = timedelta(seconds=float(seconds))
    total_seconds = int(delta.total_seconds())
    millis = int((delta.total_seconds() - total_seconds) * 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def build_srt_content(segments: Iterable[Tuple[int, float, float, str]]) -> str:
    """将识别片段转换成标准的SRT文本."""
    lines: List[str] = []
    for index, start, end, text in segments:
        lines.append(str(index))
        lines.append(
            f"{seconds_to_srt_timestamp(start)} --> {seconds_to_srt_timestamp(end)}"
        )
        lines.append(text.strip())
        lines.append("")  # 空行分隔
    return "\n".join(lines).strip() + "\n"


def write_srt(segments: Iterable[Tuple[int, float, float, str]], output_path: Path) -> None:
    """Persist transcription segments to an SRT file."""
    content = build_srt_content(segments)
    output_path.write_text(content, encoding="utf-8")


def transcribe_audio(
    audio_path: Path,
    model_size: str,
    language: str,
    device: str,
    compute_type: str,
    vad_filter: bool,
) -> Iterable[Tuple[int, float, float, str]]:
    """
    Run faster-whisper on the provided audio and yield numbered segments.
    """
    # 加载faster-whisper模型
    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
    )
    segments, _ = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=vad_filter,
    )
    for idx, segment in enumerate(segments, start=1):
        yield idx, segment.start, segment.end, segment.text


def transcribe_video_file(
    video_path: Path,
    model_size: str,
    language: str,
    device: str,
    compute_type: str,
    vad_filter: bool,
) -> List[Tuple[int, float, float, str]]:
    """高阶工具：直接把视频转成分段结果列表."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_audio:
        tmp_audio_path = Path(tmp_audio.name)

    try:
        extract_audio(video_path, tmp_audio_path)
        segments = list(
            transcribe_audio(
                tmp_audio_path,
                model_size,
                language,
                device,
                compute_type,
                vad_filter,
            )
        )
        return segments
    finally:
        tmp_audio_path.unlink(missing_ok=True)


def _escape_ffmpeg_path(path: Path) -> str:
    """Escape路径，用于ffmpeg subtitles过滤器."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    fonts_dir: Optional[Path] = None,
    force_style: Optional[str] = None,
) -> None:
    """使用ffmpeg将SRT字幕烧录进视频."""
    filter_parts = [f"subtitles='{_escape_ffmpeg_path(subtitle_path)}'"]
    if fonts_dir:
        filter_parts.append(f"fontsdir='{_escape_ffmpeg_path(fonts_dir)}'")
    if force_style:
        filter_parts.append(f"force_style='{force_style}'")
    filter_arg = ":".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        filter_arg,
        str(output_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to burn subtitles.\n\n"
            f"STDOUT:\n{proc.stdout.decode(errors='ignore')}\n\n"
            f"STDERR:\n{proc.stderr.decode(errors='ignore')}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract subtitles from a video using faster-whisper."
    )
    parser.add_argument("video", type=Path, help="Path to the video file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Destination SRT path (defaults to <video>.srt).",
    )
    parser.add_argument(
        "--model-size",
        default="base",
        help="faster-whisper model size or path (default: base).",
    )
    parser.add_argument(
        "--language",
        default="zh",
        help="Language code for decoding (default: zh).",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Device for inference, e.g. cpu, cuda, auto (default: auto).",
    )
    parser.add_argument(
        "--compute-type",
        default="int8_float16",
        help="Compute type passed to faster-whisper (default: int8_float16).",
    )
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="Disable the built-in VAD filter.",
    )
    return parser


def main(argv: Iterable[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    video_path: Path = args.video.expanduser().resolve()
    if not video_path.exists():
        parser.error(f"Video file does not exist: {video_path}")

    # 默认输出为同名SRT文件
    output_path: Path = (
        args.output.expanduser().resolve()
        if args.output
        else video_path.with_suffix(".srt")
    )

    # 提取音频 + 识别
    segments = transcribe_video_file(
        video_path,
        args.model_size,
        args.language,
        args.device,
        args.compute_type,
        not args.no_vad,
    )
    # 写入SRT字幕
    write_srt(segments, output_path)
    print(f"SRT saved to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
