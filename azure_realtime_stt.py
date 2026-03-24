from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

try:
    import azure.cognitiveservices.speech as speechsdk
    _SPEECHSDK_IMPORT_ERROR = ""
except Exception as exc:
    speechsdk = None
    _SPEECHSDK_IMPORT_ERROR = str(exc)


SttEventEmitter = Callable[[str, Any], None]

_DEFAULT_REGION = "southeastasia"
_DEFAULT_LANGUAGE = "zh-CN"


class AzureRealtimeSttConfig:
    def __init__(
        self,
        key: str,
        region: str = _DEFAULT_REGION,
        endpoint: str = "",
        endpoint_id: str = "",
        language: str = _DEFAULT_LANGUAGE,
        auto_detect_languages: Tuple[str, ...] = (),
        language_id_mode: str = "AtStart",
        profanity: str = "Masked",
        output_format: str = "Simple",
        request_word_level_timestamps: bool = False,
        enable_dictation: bool = False,
        enable_audio_logging: bool = False,
        stable_partial_result_threshold: int = 0,
        segmentation_silence_timeout_ms: int = 0,
        initial_silence_timeout_ms: int = 0,
        segmentation_strategy: str = "Default",
        phrase_list: Tuple[str, ...] = (),
    ) -> None:
        self.key = key
        self.region = region
        self.endpoint = endpoint
        self.endpoint_id = endpoint_id
        self.language = language
        self.auto_detect_languages = auto_detect_languages
        self.language_id_mode = language_id_mode
        self.profanity = profanity
        self.output_format = output_format
        self.request_word_level_timestamps = request_word_level_timestamps
        self.enable_dictation = enable_dictation
        self.enable_audio_logging = enable_audio_logging
        self.stable_partial_result_threshold = stable_partial_result_threshold
        self.segmentation_silence_timeout_ms = segmentation_silence_timeout_ms
        self.initial_silence_timeout_ms = initial_silence_timeout_ms
        self.segmentation_strategy = segmentation_strategy
        self.phrase_list = phrase_list

    @property
    def auto_detect_enabled(self) -> bool:
        return bool(self.auto_detect_languages)

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        env: Optional[Mapping[str, str]] = None,
    ) -> "AzureRealtimeSttConfig":
        env = env or os.environ

        key = _first_non_empty(
            data.get("stt_key"),
            env.get("AZURE_SPEECH_KEY", ""),
        )
        if not key:
            raise ValueError(
                "缺少订阅 Key，请在设置页面填写或设置环境变量 AZURE_SPEECH_KEY。"
            )

        region = _first_non_empty(
            data.get("stt_region"),
            env.get("AZURE_SPEECH_REGION", _DEFAULT_REGION),
        ) or _DEFAULT_REGION
        endpoint = _first_non_empty(
            data.get("stt_endpoint"),
            env.get("AZURE_SPEECH_ENDPOINT", ""),
        )
        endpoint_id = _first_non_empty(data.get("stt_endpoint_id"), "")
        language = _first_non_empty(data.get("stt_language"), _DEFAULT_LANGUAGE)
        auto_detect_languages = tuple(
            _split_csv_items(data.get("stt_auto_detect_languages", ""))
        )
        language_id_mode = _normalize_choice(
            data.get("stt_language_id_mode"),
            default="AtStart",
            allowed=("AtStart", "Continuous"),
            field_name="语言识别模式",
        )
        profanity = _normalize_choice(
            data.get("stt_profanity"),
            default="Masked",
            allowed=("Masked", "Raw", "Removed"),
            field_name="脏词处理",
        )
        output_format = _normalize_choice(
            data.get("stt_output_format"),
            default="Simple",
            allowed=("Simple", "Detailed"),
            field_name="输出格式",
        )
        segmentation_strategy = _normalize_choice(
            data.get("stt_segmentation_strategy"),
            default="Default",
            allowed=("Default", "Semantic"),
            field_name="分段策略",
        )

        stable_partial_result_threshold = _parse_non_negative_int(
            data.get("stt_stable_partial_result_threshold", 0),
            "增量稳定阈值",
        )
        segmentation_silence_timeout_ms = _parse_non_negative_int(
            data.get("stt_segmentation_silence_timeout_ms", 0),
            "分段静音超时",
        )
        initial_silence_timeout_ms = _parse_non_negative_int(
            data.get("stt_initial_silence_timeout_ms", 0),
            "起始静音超时",
        )
        phrase_list = tuple(_split_csv_items(data.get("stt_phrase_list", "")))

        if auto_detect_languages:
            max_languages = 10 if language_id_mode == "Continuous" else 4
            if len(auto_detect_languages) > max_languages:
                raise ValueError(
                    f"{language_id_mode} 模式最多支持 {max_languages} 个候选语言。"
                )
            if not endpoint and language_id_mode == "Continuous":
                endpoint = _build_speech_v2_endpoint(region)

        return cls(
            key=key,
            region=region,
            endpoint=endpoint,
            endpoint_id=endpoint_id,
            language=language,
            auto_detect_languages=auto_detect_languages,
            language_id_mode=language_id_mode,
            profanity=profanity,
            output_format=output_format,
            request_word_level_timestamps=bool(
                data.get("stt_word_level_timestamps", False)
            ),
            enable_dictation=bool(data.get("stt_dictation_enabled", False)),
            enable_audio_logging=bool(data.get("stt_audio_logging_enabled", False)),
            stable_partial_result_threshold=stable_partial_result_threshold,
            segmentation_silence_timeout_ms=segmentation_silence_timeout_ms,
            initial_silence_timeout_ms=initial_silence_timeout_ms,
            segmentation_strategy=segmentation_strategy,
            phrase_list=phrase_list,
        )


def is_azure_speech_sdk_available() -> bool:
    return speechsdk is not None


def build_speech_config(config: AzureRealtimeSttConfig):
    _ensure_sdk()

    if config.endpoint:
        speech_config = speechsdk.SpeechConfig(
            subscription=config.key,
            endpoint=config.endpoint,
        )
    else:
        speech_config = speechsdk.SpeechConfig(
            subscription=config.key,
            region=config.region,
        )

    if config.endpoint_id:
        speech_config.endpoint_id = config.endpoint_id

    if not config.auto_detect_enabled:
        speech_config.speech_recognition_language = config.language

    speech_config.output_format = getattr(
        speechsdk.OutputFormat,
        config.output_format,
    )
    speech_config.set_profanity(
        getattr(speechsdk.ProfanityOption, config.profanity)
    )

    if config.request_word_level_timestamps:
        speech_config.request_word_level_timestamps()
    if config.enable_dictation:
        speech_config.enable_dictation()
    if config.enable_audio_logging:
        speech_config.enable_audio_logging()

    _set_optional_property(
        speech_config,
        speechsdk.PropertyId.SpeechServiceResponse_StablePartialResultThreshold,
        config.stable_partial_result_threshold,
    )
    _set_optional_property(
        speech_config,
        speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs,
        config.segmentation_silence_timeout_ms,
    )
    _set_optional_property(
        speech_config,
        speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs,
        config.initial_silence_timeout_ms,
    )

    if config.segmentation_strategy != "Default":
        speech_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationStrategy,
            config.segmentation_strategy,
        )

    if config.auto_detect_enabled:
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode,
            config.language_id_mode,
        )

    return speech_config


def build_speech_recognizer(config: AzureRealtimeSttConfig, audio_config=None):
    _ensure_sdk()

    if audio_config is None:
        audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)

    recognizer_kwargs = {
        "speech_config": build_speech_config(config),
        "audio_config": audio_config,
    }

    if config.auto_detect_enabled:
        recognizer_kwargs["auto_detect_source_language_config"] = (
            speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=list(config.auto_detect_languages)
            )
        )

    recognizer = speechsdk.SpeechRecognizer(**recognizer_kwargs)

    if config.phrase_list:
        phrase_list = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
        for phrase in config.phrase_list:
            phrase_list.addPhrase(phrase)

    return recognizer


def create_realtime_stt_worker(
    config: AzureRealtimeSttConfig,
    stop_event: threading.Event,
    emit: SttEventEmitter,
) -> Callable[[], None]:
    def worker() -> None:
        recognizer = None
        finished_event = threading.Event()

        try:
            recognizer = build_speech_recognizer(config)

            def on_recognizing(evt) -> None:
                result = evt.result
                if result.reason != speechsdk.ResultReason.RecognizingSpeech:
                    return
                payload = _build_result_payload(
                    result,
                    auto_detect_enabled=config.auto_detect_enabled,
                    include_details=False,
                )
                if payload["text"]:
                    emit("partial", payload)

            def on_recognized(evt) -> None:
                result = evt.result
                if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    payload = _build_result_payload(
                        result,
                        auto_detect_enabled=config.auto_detect_enabled,
                        include_details=True,
                    )
                    if payload["text"]:
                        emit("final", payload)
                elif result.reason == speechsdk.ResultReason.NoMatch:
                    emit("status", "未匹配到语音。")

            def on_canceled(evt) -> None:
                finished_event.set()
                if evt.reason == speechsdk.CancellationReason.Error:
                    error_code = getattr(evt, "error_code", "")
                    error_details = getattr(evt, "error_details", "") or "未知错误"
                    emit(
                        "status",
                        f"字幕取消（错误 {error_code}）：{error_details}",
                    )
                else:
                    emit("status", f"字幕会话取消：{evt.reason}")

            def on_session_stopped(_) -> None:
                finished_event.set()

            recognizer.recognizing.connect(on_recognizing)
            recognizer.recognized.connect(on_recognized)
            recognizer.canceled.connect(on_canceled)
            recognizer.session_stopped.connect(on_session_stopped)

            emit("status", _build_runtime_summary(config))
            recognizer.start_continuous_recognition()

            while not stop_event.wait(0.2):
                if finished_event.is_set():
                    break

        except Exception as exc:
            emit("status", f"实时字幕线程异常：{exc}")
        finally:
            if recognizer is not None:
                try:
                    recognizer.stop_continuous_recognition()
                    finished_event.wait(timeout=3)
                except Exception as exc:
                    emit("status", f"停止实时字幕失败：{exc}")
            emit("stopped", "")

    return worker


def _build_result_payload(
    result,
    auto_detect_enabled: bool,
    include_details: bool,
) -> Dict[str, Any]:
    payload = {
        "text": (result.text or "").strip(),
        "result_id": getattr(result, "result_id", ""),
        "offset_ticks": int(getattr(result, "offset", 0) or 0),
        "duration_ticks": int(getattr(result, "duration", 0) or 0),
    }

    if auto_detect_enabled:
        detected_language = _extract_detected_language(result)
        if detected_language:
            payload["detected_language"] = detected_language

    if include_details:
        details = _parse_result_details(result)
        if details:
            payload["details"] = details

            nbest = details.get("NBest")
            if isinstance(nbest, list) and nbest:
                best = nbest[0] or {}
                confidence = best.get("Confidence")
                if confidence is not None:
                    payload["confidence"] = confidence
                words = best.get("Words")
                if isinstance(words, list):
                    payload["words"] = words

    return payload


def _extract_detected_language(result) -> str:
    try:
        detected = speechsdk.AutoDetectSourceLanguageResult(result)
        return (detected.language or "").strip()
    except Exception:
        return ""


def _parse_result_details(result) -> Dict[str, Any]:
    raw_json = getattr(result, "json", "") or ""
    if not raw_json:
        return {}
    try:
        return json.loads(raw_json)
    except Exception:
        return {}


def _build_runtime_summary(config: AzureRealtimeSttConfig) -> str:
    parts = []

    if config.auto_detect_enabled:
        parts.append(
            "语言自动识别="
            + ",".join(config.auto_detect_languages)
            + f" ({config.language_id_mode})"
        )
    else:
        parts.append(f"识别语言={config.language}")

    parts.append(f"输出={config.output_format}")
    parts.append(f"脏词={config.profanity}")

    if config.segmentation_strategy != "Default":
        parts.append(f"分段={config.segmentation_strategy}")
    if config.stable_partial_result_threshold > 0:
        parts.append(f"增量稳定阈值={config.stable_partial_result_threshold}")
    if config.segmentation_silence_timeout_ms > 0:
        parts.append(f"分段静音={config.segmentation_silence_timeout_ms}ms")
    if config.initial_silence_timeout_ms > 0:
        parts.append(f"起始静音={config.initial_silence_timeout_ms}ms")
    if config.phrase_list:
        parts.append(f"短语提示={len(config.phrase_list)}项")

    return "实时字幕线程已启动（" + "；".join(parts) + "）"


def _ensure_sdk() -> None:
    if speechsdk is None:
        detail = _SPEECHSDK_IMPORT_ERROR.strip()
        if detail:
            detail = f"（导入错误：{detail}）"
        raise RuntimeError(
            "未安装或无法加载 azure-cognitiveservices-speech，无法启动实时字幕。"
            + detail
        )


def _set_optional_property(speech_config, property_id, value: int) -> None:
    if value > 0:
        speech_config.set_property(property_id, str(value))


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _split_csv_items(value: Any) -> Iterable[str]:
    raw_text = str(value or "")
    normalized = (
        raw_text.replace("，", ",")
        .replace(";", ",")
        .replace("；", ",")
        .replace("\r", ",")
        .replace("\n", ",")
    )

    seen = set()
    items = []
    for item in normalized.split(","):
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _normalize_choice(
    value: Any,
    default: str,
    allowed: Tuple[str, ...],
    field_name: str,
) -> str:
    mapping = {item.lower(): item for item in allowed}
    text = str(value or default).strip()
    normalized = mapping.get(text.lower())
    if normalized is None:
        raise ValueError(f"{field_name} 仅支持：{', '.join(allowed)}。")
    return normalized


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0

    try:
        parsed = int(text)
    except ValueError:
        raise ValueError(f"{field_name} 必须是非负整数。")

    if parsed < 0:
        raise ValueError(f"{field_name} 必须是非负整数。")
    return parsed


def _build_speech_v2_endpoint(region: str) -> str:
    return "wss://{region}.stt.speech.microsoft.com/speech/universal/v2".format(
        region=region
    )
