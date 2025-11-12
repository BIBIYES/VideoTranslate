# coding: utf-8
"""
Streamlit界面：提供“视频转字幕”“字幕烧录”“AI 字幕翻译”三个工具。

运行:
    streamlit run streamlit_app.py
"""
import json
from pathlib import Path
import re
import tempfile
from typing import Callable, Dict, List, Sequence

from openai import OpenAI

import streamlit as st

from mian import (
    build_srt_content,
    burn_subtitles,
    extract_audio,
    transcribe_audio,
)


VIDEO_TYPES = ["mp4", "mov", "mkv", "avi"]
AUDIO_TYPES = ["mp3", "wav", "m4a"]

SRT_BLOCK_RE = re.compile(
    r"(?P<index>\d+)\s*\n"
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s-->\s(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s*\n"
    r"(?P<text>.*?)(?=\n{2,}|\Z)",
    re.DOTALL,
)


def create_logger(placeholder: st.delta_generator.DeltaGenerator) -> Callable[[str], None]:
    """在页面底部实时输出日志。"""
    log_lines: List[str] = []
    placeholder.info("日志将在任务开始后显示。")

    def log(message: str) -> None:
        log_lines.append(message)
        placeholder.code("\n".join(log_lines), language="bash")

    return log


def parse_timestamp_to_seconds(timestamp: str) -> float:
    hours, minutes, rest = timestamp.split(":")
    seconds, millis = rest.split(",")
    total = (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000.0
    )
    return total


def parse_srt_segments(srt_text: str) -> List[Dict[str, object]]:
    segments: List[Dict[str, object]] = []
    for match in SRT_BLOCK_RE.finditer(srt_text.strip()):
        index = int(match.group("index"))
        start = match.group("start")
        end = match.group("end")
        text = match.group("text").strip()
        segments.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "start_seconds": parse_timestamp_to_seconds(start),
                "end_seconds": parse_timestamp_to_seconds(end),
                "text": text,
            }
        )
    return segments


def chunk_sequence(items: Sequence[Dict[str, object]], chunk_size: int):
    for start in range(0, len(items), chunk_size):
        yield list(items[start : start + chunk_size])


def extract_json_array(text: str):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)


def translate_chunk_with_openai(
    client: OpenAI,
    model: str,
    target_language: str,
    chunk: Sequence[Dict[str, object]],
) -> Dict[int, str]:
    system_prompt = (
        "你是一名专业字幕翻译。"
        f"请将输入的字幕内容翻译成{target_language}，保持原意和语气，不要丢失数字或专有名词。"
        "只返回 JSON 数组，每个元素包含 index (数字) 和 translation (字符串)。"
    )
    formatted_lines = []
    for seg in chunk:
        text = " ".join(str(seg["text"]).split())
        formatted_lines.append(f"{seg['index']}|{text}")
    user_prompt = (
        "以下是需要翻译的字幕行，格式为 index|文本 ：\n"
        + "\n".join(formatted_lines)
        + "\n请翻译为目标语言，只返回 JSON 数组。"
    )

    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    data = extract_json_array(content)
    result: Dict[int, str] = {}
    for item in data:
        idx = int(item["index"])
        translation = str(item["translation"]).strip()
        result[idx] = translation
    return result


def main() -> None:
    st.set_page_config(
        page_title="视频字幕工具箱",
        page_icon="🎬",
        layout="centered",
    )
    st.title("🎬 视频字幕工具箱")
    tab_transcribe, tab_burn, tab_translate = st.tabs(
        ["🎯 视频转字幕", "🔥 字幕烧录", "🧠 AI 字幕翻译"]
    )

    with tab_transcribe:
        st.subheader("视频 / 音频 → SRT 字幕")
        st.write(
            "上传视频或音频，选择识别参数后生成 SRT 字幕文件。默认支持中文，同时也可识别英文 (en) 和日语 (ja)。"
            "首次使用某个模型会自动下载。"
        )

        upload = st.file_uploader(
            "上传视频或音频文件",
            type=VIDEO_TYPES + AUDIO_TYPES,
            key="transcribe_upload",
        )
        if upload:
            st.caption(f"已选择：{upload.name}（约 {upload.size / (1024 * 1024):.2f} MB）")

        col_left, col_right = st.columns(2)
        with col_left:
            model_options = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
            model_choice = st.selectbox(
                "模型大小/名称",
                model_options,
                index=1,
                key="model_choice",
                help="模型越大识别越准、越慢；tiny/base 更快、medium/large 更准确。",
            )
            custom_model = st.text_input(
                "自定义模型/本地路径（可选）",
                key="custom_model",
                help="留空使用上方列表，也可以填写本地模型目录或 HuggingFace 仓库名。",
            )
            language_choice = st.selectbox(
                "常用语言",
                [
                    ("自动检测", ""),
                    ("中文 (zh)", "zh"),
                    ("英文 (en)", "en"),
                    ("日语 (ja)", "ja"),
                ],
                format_func=lambda item: item[0],
                key="language_choice",
                help="选择常用语言可快速设置，也可以在下方自定义其它语言代码。",
            )
            language_custom = st.text_input(
                "自定义语言代码（可选）",
                value="",
                key="lang_input",
                help="填写 ISO 639-1 代码，如 zh/en/ja。如果留空则使用上方的常用语言或自动检测。",
            )
            language = language_custom.strip() or language_choice[1]
            model_size = custom_model.strip() or model_choice
        with col_right:
            device = st.selectbox(
                "推理设备",
                ["auto", "cpu", "cuda", "metal"],
                index=0,
                key="device_select",
                help="auto 会自动选择可用 GPU；如果识别失败，可手动切换为 cpu。",
            )
            compute_type = st.selectbox(
                "计算精度",
                ["int8_float16", "int8", "float16", "float32"],
                index=0,
                key="compute_select",
                help="int8/float16 更节省显存，float32 最兼容。如出错请改为 float32。",
            )
            vad_filter = st.checkbox(
                "启用 VAD 端点检测",
                value=True,
                key="vad_checkbox",
                help="开启后会自动剔除静音片段，字幕更干净。若识别不到声音可关闭。",
            )

        st.markdown("#### 执行日志")
        transcribe_log_placeholder = st.empty()

        if st.button("开始生成字幕", type="primary", key="start_transcribe"):
            if upload is None:
                st.warning("请先上传文件。")
            else:
                log = create_logger(transcribe_log_placeholder)
                suffix = Path(upload.name).suffix or ".mp4"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_video:
                    tmp_video.write(upload.getbuffer())
                    tmp_video_path = Path(tmp_video.name)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_audio:
                    tmp_audio_path = Path(tmp_audio.name)

                try:
                    log("1) 提取音频中...")
                    extract_audio(tmp_video_path, tmp_audio_path)
                    log("2) 开始语音识别...")
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
                    log(f"完成！共输出 {len(segments)} 条字幕。")
                except Exception as exc:  # pragma: no cover - UI异常展示
                    log(f"出错：{exc}")
                    st.error(f"生成失败：{exc}")
                    return
                finally:
                    tmp_video_path.unlink(missing_ok=True)
                    tmp_audio_path.unlink(missing_ok=True)

                srt_text = build_srt_content(segments)
                default_name = Path(upload.name).with_suffix(".srt").name

                st.success("字幕生成完成 ✅")
                st.download_button(
                    label="下载 SRT 字幕",
                    data=srt_text.encode("utf-8"),
                    file_name=default_name,
                    mime="application/x-subrip",
                )
                st.text_area("字幕预览", srt_text, height=320, key="srt_preview")

    with tab_burn:
        st.subheader("字幕烧录（SRT → 视频）")
        st.write(
            "将已有的 SRT 字幕烧录进视频画面，适合发布需要内嵌字幕的平台。"
            "支持调整字体大小或通过 force_style 设置更复杂样式。"
        )

        burn_video = st.file_uploader(
            "上传需要烧录字幕的视频",
            type=VIDEO_TYPES,
            key="burn_video_upload",
        )
        burn_srt = st.file_uploader(
            "上传字幕文件（SRT）",
            type=["srt"],
            key="burn_srt_upload",
        )

        col_font, col_style = st.columns(2)
        with col_font:
            font_size = st.slider(
                "字幕字体大小",
                min_value=16,
                max_value=48,
                value=28,
                step=1,
                key="font_slider",
                help="仅对默认样式生效，单位为点。可配合右侧 force_style 使用。",
            )
        with col_style:
            custom_style = st.text_input(
                "force_style 高级参数（可选）",
                help="留空则仅控制字体大小。示例：FontName=Arial,PrimaryColour=&HFFFFFF&",
                key="custom_force_style",
            )

        st.markdown("#### 执行日志")
        burn_log_placeholder = st.empty()

        if st.button("开始烧录字幕", type="secondary", key="start_burn"):
            if burn_video is None or burn_srt is None:
                st.warning("请同时上传视频和 SRT 字幕文件。")
            else:
                log = create_logger(burn_log_placeholder)
                video_suffix = Path(burn_video.name).suffix or ".mp4"
                with tempfile.NamedTemporaryFile(delete=False, suffix=video_suffix) as tmp_video:
                    tmp_video.write(burn_video.getbuffer())
                    tmp_video_path = Path(tmp_video.name)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".srt") as tmp_srt:
                    tmp_srt.write(burn_srt.getbuffer())
                    tmp_srt_path = Path(tmp_srt.name)
                with tempfile.NamedTemporaryFile(delete=False, suffix=video_suffix) as tmp_output:
                    tmp_output_path = Path(tmp_output.name)

                force_style = (
                    custom_style.strip()
                    if custom_style.strip()
                    else f"Fontsize={font_size}"
                )

                try:
                    log("1) 调用 ffmpeg 烧录字幕...")
                    burn_subtitles(
                        tmp_video_path,
                        tmp_srt_path,
                        tmp_output_path,
                        force_style=force_style,
                    )
                    log("2) 烧录完成，准备提供下载。")
                    burned_bytes = tmp_output_path.read_bytes()
                except Exception as exc:  # pragma: no cover - UI异常展示
                    log(f"出错：{exc}")
                    st.error(f"烧录失败：{exc}")
                    return
                finally:
                    tmp_video_path.unlink(missing_ok=True)
                    tmp_srt_path.unlink(missing_ok=True)
                    tmp_output_path.unlink(missing_ok=True)

                video_stem = Path(burn_video.name).stem
                output_name = f"{video_stem}_sub{video_suffix}"

                st.success("字幕烧录完成 ✅")
                st.download_button(
                    label="下载烧录后的视频",
                    data=burned_bytes,
                    file_name=output_name,
                    mime="video/mp4",
                )

    with tab_translate:
        st.subheader("AI 字幕翻译（SRT → SRT）")
        st.write(
            "上传 SRT 字幕文件，系统会按小段批量调用 OpenAI 接口翻译。"
            "需要填写 API Base 与 API Key（兼容 OpenAI 统一格式，例如官方/第三方兼容服务）。"
        )

        api_base = st.text_input(
            "API Base",
            value="https://api.openai.com/v1",
            help="OpenAI 统一格式的接口地址，结尾通常为 /v1。",
            key="translate_api_base",
        )
        api_key = st.text_input(
            "API Key",
            value="",
            type="password",
            help="不会被保存，仅在本次会话内使用。",
            key="translate_api_key",
        )
        model_name = st.text_input(
            "模型名称",
            value="gpt-4o-mini",
            help="请输入可用的聊天/多模态模型名称，例如 gpt-4o-mini、gpt-4o-mini-translation。",
            key="translate_model",
        )
        target_lang = st.selectbox(
            "目标语言",
            ["中文", "英文", "日语", "韩语", "西班牙语"],
            help="翻译将输出该语言，系统会自动描述给模型。",
            key="translate_target",
        )
        chunk_size = st.slider(
            "每批翻译的字幕条数",
            min_value=3,
            max_value=20,
            value=6,
            help="一次请求处理的字幕条数。数值越大速度越快但回答越长，建议 3-10。",
            key="translate_chunk_size",
        )

        translate_upload = st.file_uploader(
            "上传需要翻译的 SRT 字幕",
            type=["srt"],
            key="translate_upload",
        )

        st.markdown("#### 执行日志")
        translate_log_placeholder = st.empty()

        if st.button("开始翻译字幕", type="primary", key="start_translate"):
            if translate_upload is None:
                st.warning("请先上传 SRT 文件。")
                return
            if not api_base.strip() or not api_key.strip():
                st.warning("请填写 API Base 与 API Key。")
                return
            log = create_logger(translate_log_placeholder)
            try:
                srt_text = translate_upload.getvalue().decode("utf-8")
            except UnicodeDecodeError:
                st.error("文件不是 UTF-8 编码，请转换后再试。")
                return

            segments = parse_srt_segments(srt_text)
            if not segments:
                st.warning("未解析到任何字幕段，请确认 SRT 格式。")
                return

            log(f"已解析 {len(segments)} 条字幕，开始分批翻译...")
            client = OpenAI(api_key=api_key.strip(), base_url=api_base.strip())

            translated_map: Dict[int, str] = {}
            total_batches = (len(segments) + chunk_size - 1) // chunk_size

            for batch_idx, chunk in enumerate(chunk_sequence(segments, chunk_size), start=1):
                log(f"第 {batch_idx}/{total_batches} 批：调用模型翻译 {len(chunk)} 条字幕...")
                try:
                    chunk_result = translate_chunk_with_openai(
                        client=client,
                        model=model_name.strip(),
                        target_language=target_lang,
                        chunk=chunk,
                    )
                except Exception as exc:  # pragma: no cover - 网络异常展示
                    log(f"出错：{exc}")
                    st.error(f"翻译失败：{exc}")
                    return
                translated_map.update(chunk_result)
                preview_lines = []
                for seg in chunk:
                    translated_text = chunk_result.get(seg["index"])
                    if translated_text:
                        original = " ".join(str(seg["text"]).splitlines())
                        preview_lines.append(
                            f'{seg["index"]}: "{original}" -> "{translated_text}"'
                        )
                if preview_lines:
                    log("结果预览：\n" + "\n".join(preview_lines))

            translated_segments = [
                (
                    seg["index"],
                    seg["start_seconds"],
                    seg["end_seconds"],
                    translated_map.get(seg["index"], seg["text"]),
                )
                for seg in segments
            ]
            translated_srt = build_srt_content(translated_segments)

            st.success("字幕翻译完成 ✅")
            trans_stem = Path(translate_upload.name).stem
            translated_name = f"{trans_stem}_{target_lang}.srt"
            st.download_button(
                label="下载翻译后的 SRT",
                data=translated_srt.encode("utf-8"),
                file_name=translated_name,
                mime="application/x-subrip",
            )
            st.text_area("翻译结果预览", translated_srt, height=300, key="translated_preview")


if __name__ == "__main__":
    main()
