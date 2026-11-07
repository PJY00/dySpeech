"""올라온 WAV가 녹음 규격(모노, 16비트 PCM, 허용 샘플레이트)에 맞는지 앞부분만 읽어 확인한다."""

import struct
from dataclasses import dataclass
from typing import get_args

from app.schemas.common import SampleRate
from app.storage.s3 import RecordingStorage

# fmt 청크는 보통 첫 100바이트 안에 있다. 앞에 다른 청크가 있어도 찾도록 넉넉히 읽는다.
WAV_HEADER_BYTES = 64 * 1024
ALLOWED_SAMPLE_RATES = get_args(SampleRate)
PCM = 1
EXTENSIBLE = 0xFFFE
FORMAT_NAMES = {3: "부동소수점", 6: "A-law", 7: "μ-law"}


class WavHeaderError(ValueError):
    pass


@dataclass(frozen=True)
class WavFormat:
    # 확장 형식(0xFFFE)이면 SubFormat의 형식 코드
    format_code: int
    channels: int
    sample_rate: int
    bits_per_sample: int


def parse_wav_header(data: bytes) -> WavFormat:
    """
    WAV 앞부분에서 fmt 청크를 찾아 형식 정보를 읽음
    입력: data (WAV 파일 앞부분, 바이트)
    출력: 형식 정보(format_code 형식 코드, channels 채널 수, sample_rate Hz, bits_per_sample 표본당 비트). 확장 형식이면 format_code는 SubFormat의 형식 코드. 읽지 못하면 WavHeaderError를 일으킴
    알고리즘
    1. 앞 12바이트가 RIFF…WAVE가 아니면 "WAV 파일이 아닙니다(RIFF/WAVE 머리글이 없음)" 오류를 일으킴
    2. 12바이트 위치부터 청크를 차례로 봄(청크 이름 4바이트 + 크기 4바이트, 리틀 엔디언)
    3. fmt 청크면 형식 코드·채널 수·샘플레이트·표본당 비트를 읽음. 크기가 16바이트보다 작거나 데이터가 잘려 있으면 "fmt 정보가 잘려 있습니다" 오류
    4. 형식 코드가 확장 형식(0xFFFE)이면 SubFormat(GUID)의 앞 2바이트(fmt 본문 24바이트 위치)를 실제 형식 코드로 씀. fmt 크기나 데이터가 40바이트보다 짧으면 "확장 fmt 정보가 잘려 있습니다" 오류
    5. 다른 청크면 크기만큼 건너뜀. 청크는 2바이트 단위로 맞춰져 있어 크기가 홀수면 1바이트 더 건너뜀
    6. 끝까지 fmt 청크가 없으면 "앞 …바이트 안에서 fmt 정보를 찾지 못했습니다" 오류를 일으킴
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavHeaderError("WAV 파일이 아닙니다(RIFF/WAVE 머리글이 없음)")
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        (size,) = struct.unpack_from("<I", data, offset + 4)
        body = offset + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise WavHeaderError("fmt 정보가 잘려 있습니다")
            format_code, channels, sample_rate, _, _, bits = struct.unpack_from("<HHIIHH", data, body)
            if format_code == EXTENSIBLE:
                # 확장 형식은 SubFormat(GUID)의 앞 2바이트가 실제 형식 코드다.
                if size < 40 or body + 40 > len(data):
                    raise WavHeaderError("확장 fmt 정보가 잘려 있습니다")
                (format_code,) = struct.unpack_from("<H", data, body + 24)
            return WavFormat(format_code, channels, sample_rate, bits)
        # 청크는 2바이트 단위로 맞춰져 있다.
        offset = body + size + size % 2
    raise WavHeaderError(f"앞 {len(data)}바이트 안에서 fmt 정보를 찾지 못했습니다")


def format_problems(fmt: WavFormat, declared_sample_rate: int | None = None) -> list[str]:
    """
    WAV 형식 정보가 녹음 규격과 어긋나는 점을 모두 모음
    입력: fmt (WAV 형식 정보), declared_sample_rate (업로드 URL 요청 때 알린 샘플레이트 Hz, 없으면 비교하지 않음)
    출력: 어긋나는 이유 목록(사람이 읽는 문장). 맞으면 빈 목록
    알고리즘
    1. 형식 코드가 PCM(1)이 아니면 이유를 넣음. 3은 부동소수점, 6은 A-law, 7은 μ-law로 이름을 쓰고, 그 밖은 코드 번호를 씀
    2. 채널이 1개가 아니면, 표본당 비트가 16이 아니면 각각 이유를 넣음
    3. 샘플레이트가 16000, 44100, 48000 Hz 중 하나가 아니면 이유를 넣음. 허용 값인데 알린 샘플레이트와 다르면 그 차이를 이유로 넣음
    """
    problems = []
    if fmt.format_code != PCM:
        name = FORMAT_NAMES.get(fmt.format_code, f"형식 코드 {fmt.format_code}")
        problems.append(f"PCM이 아닙니다({name}). 16비트 PCM이어야 합니다")
    if fmt.channels != 1:
        problems.append(f"채널이 {fmt.channels}개입니다. 모노(1개)여야 합니다")
    if fmt.bits_per_sample != 16:
        problems.append(f"{fmt.bits_per_sample}비트입니다. 16비트여야 합니다")
    if fmt.sample_rate not in ALLOWED_SAMPLE_RATES:
        allowed = ", ".join(str(rate) for rate in ALLOWED_SAMPLE_RATES)
        problems.append(f"샘플레이트 {fmt.sample_rate} Hz는 받지 않습니다. {allowed} 중 하나여야 합니다")
    elif declared_sample_rate is not None and fmt.sample_rate != declared_sample_rate:
        problems.append(
            f"업로드 URL 요청 때 알린 샘플레이트({declared_sample_rate} Hz)와 파일의 샘플레이트({fmt.sample_rate} Hz)가 다릅니다"
        )
    return problems


def check_wav(data: bytes, declared_sample_rate: int | None = None) -> str | None:
    """
    WAV 앞부분이 녹음 규격(모노, 16비트 PCM, 허용 샘플레이트)에 맞는지 검사함
    입력: data (WAV 파일 앞부분, 바이트), declared_sample_rate (업로드 URL 요청 때 알린 샘플레이트 Hz, 선택)
    출력: 규격에 맞으면 None, 아니면 이유. 문제가 여럿이면 " / "로 이음
    알고리즘
    1. parse_wav_header로 형식을 읽음. 머리글 오류면 그 메시지를 이유로 돌려줌
    2. format_problems의 이유를 " / "로 이어 돌려주고, 이유가 없으면 None을 돌려줌
    """
    try:
        fmt = parse_wav_header(data)
    except WavHeaderError as error:
        return str(error)
    return " / ".join(format_problems(fmt, declared_sample_rate)) or None


def check_uploaded_wav(storage: RecordingStorage, key: str, declared_sample_rate: int | None = None) -> str | None:
    """
    S3에 올라온 WAV의 앞부분만 읽어 녹음 규격을 검사함
    입력: storage (녹음 저장소), key (S3 키), declared_sample_rate (업로드 URL 요청 때 알린 샘플레이트 Hz, 선택)
    출력: 규격에 맞으면 None, 아니면 이유(check_wav와 같음)
    알고리즘
    1. 앞 65,536바이트(64 KB)를 읽음. fmt 청크는 보통 첫 100바이트 안에 있지만, 앞에 다른 청크가 있어도 찾도록 넉넉히 읽음
    2. check_wav로 검사함
    """
    return check_wav(storage.read_start(key, WAV_HEADER_BYTES), declared_sample_rate)
