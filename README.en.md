# Video Subtitle Toolbox Guide

This project bundles three main capabilities:

- **Video-to-SRT** using `faster-whisper`, supporting configurable model size, language, device, and compute precision options.
- **Subtitle burning** via `ffmpeg` with customizable style settings, fonts, and progress/log feedback.
- **AI subtitle translation** leveraging OpenAI-compatible APIs for batch translation of `.srt` cues.

## Streamlit UI preview

View the Streamlit interface for quick reference:

![Video-to-SRT Tab](assets/VideoToSrt.png)
![Subtitle Burning Tab](assets/SubtitleBurning.png)
![AI Subtitle Translation Tab](assets/AI-SubtitleTranslation.png)

## CLI usage (mian.py)

1. Create and activate the virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   or `pip install faster-whisper streamlit openai`
3. Run the CLI:
   ```bash
   python mian.py /path/to/video.mp4 \
       --model-size base \
       --language en \
       --device auto \
       --compute-type float32
   ```

## Streamlit Web app

Launch with `streamlit run streamlit_app.py`. Three tabs cover video transcription, subtitle burning, and AI translation. Configure OpenAI-compatible `API Base`, `API Key`, model name, and batching options on the translation tab.

## Assets

All sample screenshots live under `assets/` and can be reused in documentation or demos.
