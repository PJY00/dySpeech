"""WAV 헤더 검사를 형식을 아는 합성 파일로 확인한다."""

import io
import struct
import wave

import pytest

from app.storage.wav import WAV_HEADER_BYTES, WavFormat, check_uploaded_wav, check_wav, parse_wav_header


def wav_bytes(channels: int = 1, sample_width: int = 2, rate: int = 48000, seconds: float = 0.01) -> bytes:
    """표준 wave 모듈로 만든 정수 PCM WAV"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(sample_width)
        file.setframerate(rate)
        file.writeframes(bytes(round(rate * seconds) * channels * sample_width))
    return buffer.getvalue()


def raw_wav(format_code: int, channels: int, rate: int, bits: int, extensible_sub: int | None = None, before: bytes = b"") -> bytes:
    """fmt 청크를 직접 만든 WAV. extensible_sub를 주면 확장 형식(0xFFFE)으로 만든다."""
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", format_code if extensible_sub is None else 0xFFFE, channels, rate, rate * block, block, bits)
    if extensible_sub is not None:
        guid_tail = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        fmt += struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", extensible_sub) + guid_tail
    chunks = before + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 4) + bytes(4)
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def list_chunk(payload: bytes) -> bytes:
    return b"LIST" + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_mono_16_bit_pcm_passes(rate):
    assert parse_wav_header(wav_bytes(rate=rate)) == WavFormat(1, 1, rate, 16)
    assert check_wav(wav_bytes(rate=rate), declared_sample_rate=rate) is None


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (wav_bytes(channels=2), "채널이 2개입니다. 모노(1개)여야 합니다"),
        (wav_bytes(rate=22050), "샘플레이트 22050 Hz는 받지 않습니다. 16000, 44100, 48000 중 하나여야 합니다"),
        (wav_bytes(sample_width=1), "8비트입니다. 16비트여야 합니다"),
        (wav_bytes(sample_width=3), "24비트입니다. 16비트여야 합니다"),
        (raw_wav(3, 1, 48000, 32), "PCM이 아닙니다(부동소수점). 16비트 PCM이어야 합니다 / 32비트입니다. 16비트여야 합니다"),
        (b"ID3\x04\x00" + bytes(100), "WAV 파일이 아닙니다(RIFF/WAVE 머리글이 없음)"),
    ],
)
def test_format_problems_are_explained(data, reason):
    assert check_wav(data) == reason


def test_declared_sample_rate_must_match_file():
    assert check_wav(wav_bytes(rate=44100), declared_sample_rate=48000) == (
        "업로드 URL 요청 때 알린 샘플레이트(48000 Hz)와 파일의 샘플레이트(44100 Hz)가 다릅니다"
    )


def test_several_problems_are_joined():
    assert check_wav(wav_bytes(channels=2, rate=22050)) == (
        "채널이 2개입니다. 모노(1개)여야 합니다 / 샘플레이트 22050 Hz는 받지 않습니다. 16000, 44100, 48000 중 하나여야 합니다"
    )


def test_extensible_format_uses_sub_format():
    assert check_wav(raw_wav(1, 1, 48000, 16, extensible_sub=1)) is None
    assert check_wav(raw_wav(1, 1, 48000, 32, extensible_sub=3)).startswith("PCM이 아닙니다(부동소수점)")


def test_other_chunks_before_fmt_are_skipped_with_padding():
    data = raw_wav(1, 1, 48000, 16, before=list_chunk(b"INFOodd") + list_chunk(b"x" * 1000))

    assert parse_wav_header(data) == WavFormat(1, 1, 48000, 16)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (wav_bytes()[:30], "fmt 정보가 잘려 있습니다"),
        (raw_wav(1, 1, 48000, 16, extensible_sub=1)[:50], "확장 fmt 정보가 잘려 있습니다"),
        (raw_wav(1, 1, 48000, 16, before=list_chunk(b"x" * 100))[:80], "앞 80바이트 안에서 fmt 정보를 찾지 못했습니다"),
    ],
)
def test_cut_headers_are_explained(data, reason):
    assert check_wav(data) == reason


def test_check_uploaded_wav_reads_only_the_start(storage):
    long_mono = wav_bytes(seconds=2.0)
    storage.client.put_object(Bucket=storage.bucket, Key="users/a/ok.wav", Body=long_mono)
    storage.client.put_object(Bucket=storage.bucket, Key="users/a/stereo.wav", Body=wav_bytes(channels=2))

    assert len(long_mono) > WAV_HEADER_BYTES
    assert check_uploaded_wav(storage, "users/a/ok.wav", declared_sample_rate=48000) is None
    assert check_uploaded_wav(storage, "users/a/stereo.wav") == "채널이 2개입니다. 모노(1개)여야 합니다"
