"""
Azure Speech 实时听录测试脚本（麦克风输入）。

安全说明：
- 不要在代码中硬编码密钥。
- 通过环境变量注入认证信息：
  - AZURE_SPEECH_KEY      必填，Speech 资源密钥
  - AZURE_SPEECH_REGION   选填，默认 southeastasia
  - AZURE_SPEECH_ENDPOINT 选填，自定义终结点 URL
"""

import os
import sys
import threading

import azure.cognitiveservices.speech as speechsdk


def build_speech_config() -> speechsdk.SpeechConfig:
    key = os.getenv("AZURE_SPEECH_KEY")
    if not key:
        raise ValueError("缺少环境变量 AZURE_SPEECH_KEY。")

    region = os.getenv("AZURE_SPEECH_REGION", "southeastasia")
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT", "").strip()

    if endpoint:
        config = speechsdk.SpeechConfig(subscription=key, endpoint=endpoint)
    else:
        config = speechsdk.SpeechConfig(subscription=key, region=region)

    # 识别语言可按需修改，如 en-US、ja-JP 等
    config.speech_recognition_language = "zh-CN"
    return config


def main() -> int:
    try:
        speech_config = build_speech_config()
    except ValueError as err:
        print(f"[配置错误] {err}")
        print("请先设置环境变量后再运行。")
        return 1

    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    done = threading.Event()

    def on_recognizing(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        text = (evt.result.text or "").strip()
        if text:
            print(f"[识别中] {text}")

    def on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        result = evt.result
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = (result.text or "").strip()
            if text:
                print(f"[最终结果] {text}")
        elif result.reason == speechsdk.ResultReason.NoMatch:
            print("[未匹配到语音]")

    def on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
        details = evt
        print(f"[已取消] reason={details.reason}")
        if details.reason == speechsdk.CancellationReason.Error:
            print(f"[错误码] {details.error_code}")
            print(f"[错误详情] {details.error_details}")
        done.set()

    def on_session_stopped(_: speechsdk.SessionEventArgs) -> None:
        print("[会话结束]")
        done.set()

    recognizer.recognizing.connect(on_recognizing)
    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_session_stopped)

    print("开始实时听录（麦克风）。按 Enter 停止。")
    recognizer.start_continuous_recognition()

    try:
        input()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止...")
    finally:
        recognizer.stop_continuous_recognition()
        done.wait(timeout=5)

    print("已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
