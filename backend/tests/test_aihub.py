"""AI Hub 분석 도구: 합성 녹음으로 만든 작은 zip으로 읽기, 전사 정리, 녹음 계산, 요약을 확인한다. DB·S3는 쓰지 않는다."""

import io
import json
import zipfile

import numpy as np
import pytest
import soundfile

from analysis import aihub
from fixtures.synthetic import generate

ANSWERS = json.loads(generate.ANSWERS.read_text(encoding="utf-8"))["files"]
RATE = 16000


@pytest.fixture(scope="module")
def wavs(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    assert generate.main(["--out", str(out)]) == 0
    return {path.stem: path.read_bytes() for path in out.glob("*.wav")}


def tone(seconds=1.0, rate=RATE, hz=220.0, channels=1) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    x = (0.4 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return x if channels == 1 else np.stack([x, 0.5 * x], axis=1)


def wav_bytes(samples: np.ndarray, rate: int, subtype: str) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format="WAV", subtype=subtype)
    return buffer.getvalue()


def read_all(data: bytes, block_s: float = aihub.BLOCK_S):
    info, blocks = aihub.read_blocks(io.BytesIO(data), block_s)
    return info, list(blocks)


# ---- 전사 정리 ----


def test_transcript_marks_are_counted_and_removed():
    found = aihub.clean_transcript("넋이+ 넋이 나갔다. 짠*+짜게 끓인 n/ 찌개")

    assert found == aihub.Transcript(syllables=14, repeats=2, unclear=1, tags=1, countable=True)


def test_dual_transcription_keeps_the_spoken_side():
    assert aihub.clean_transcript("특정 출연자가 (헤드폰을)/(핸드폰을) 쓴 채").syllables == 12
    # 철자 쪽에 숫자나 영문이 있어도 발음 쪽만 남으므로 셀 수 있다.
    found = aihub.clean_transcript("(3)/(삼) 월 (SNS)/(에스엔에스)로")
    assert (found.syllables, found.countable) == (8, True)


@pytest.mark.parametrize("text", ["설날은 1 월 1 일입니다", "에스엔에스(SNS)로 연락해요", "저는 000 띠입니다"])
def test_digits_or_latin_letters_make_the_count_unreliable(text):
    assert aihub.clean_transcript(text).countable is False


def test_noise_tags_are_not_letters():
    found = aihub.clean_transcript("b/ 숨 u/ l/ o/")

    assert (found.syllables, found.tags, found.countable) == (1, 4, True)


def test_speaker_comes_from_the_recording_name_and_patient_information():
    assert aihub.speaker_of("ID-01-11-N-AMG-02-02-M-56-KK", "M", "56") == "AMG/M/56"
    assert aihub.speaker_of("이름이-짧음", "F", "70") == "이름이-짧음"


# ---- WAV 읽기 ----


@pytest.mark.parametrize(
    ("subtype", "channels", "tolerance"),
    [("PCM_16", 1, 1e-6), ("PCM_16", 2, 1e-6), ("PCM_24", 1, 1e-6), ("PCM_32", 2, 1e-6), ("PCM_U8", 1, 1e-6), ("ULAW", 1, 1e-6), ("FLOAT", 1, 1e-6)],
)
def test_every_wav_format_in_the_data_is_decoded_like_soundfile(subtype, channels, tolerance):
    data = wav_bytes(tone(0.5, channels=channels), RATE, subtype)
    expected, _ = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)

    info, blocks = read_all(data)

    assert (info.channels, info.sample_rate) == (channels, RATE)
    np.testing.assert_allclose(np.concatenate(blocks), expected.mean(axis=1), atol=tolerance)


def test_unsupported_format_is_an_error():
    data = wav_bytes(tone(0.2), RATE, "ALAW")

    _, blocks = aihub.read_blocks(io.BytesIO(data))

    with pytest.raises(ValueError, match="받지 않는 WAV 형식"):
        list(blocks)


def test_recording_is_read_in_blocks_without_losing_samples():
    samples = tone(2.5)
    data = wav_bytes(samples, RATE, "PCM_16")

    _, blocks = read_all(data, block_s=1.0)

    assert [len(block) for block in blocks] == [RATE, RATE, RATE // 2]
    np.testing.assert_allclose(np.concatenate(blocks), samples, atol=1e-4)


def test_other_sample_rates_become_16_khz():
    info, blocks = read_all(wav_bytes(tone(2.0, rate=44100), 44100, "PCM_16"), block_s=1.0)

    assert info.sample_rate == 44100
    assert [len(block) for block in blocks] == [RATE, RATE]
    # 220 Hz 소리가 그대로 220 Hz다(1초에 220번 위로 지나감).
    crossings = int(((blocks[0][:-1] < 0) & (blocks[0][1:] >= 0)).sum())
    assert abs(crossings - 220) <= 1


def test_recording_inside_a_compressed_zip_is_read_as_a_stream(tmp_path):
    samples = tone(3.0)
    path = tmp_path / "audio.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("11.중풍/a.wav", wav_bytes(samples, RATE, "PCM_16"))

    with zipfile.ZipFile(path) as archive, archive.open("11.중풍/a.wav") as stream:
        _, blocks = aihub.read_blocks(stream, block_s=1.0)
        joined = np.concatenate(list(blocks))

    np.testing.assert_allclose(joined, samples, atol=1e-4)


# ---- 녹음 하나 계산 ----


def test_reading_with_pauses_is_one_utterance_and_matches_the_known_answers(wavs):
    answers = ANSWERS["paused_speech"]

    found = aihub.analyze_recording(io.BytesIO(wavs["paused_speech"]))

    # 0.6초·0.5초 휴지는 1초보다 짧으므로 한 조각이다.
    assert (found["utterances"], found["skipped_short"], found["nuclei_errors"]) == (1, 0, 0)
    assert found["pause_s"] == pytest.approx(answers["pause_s"]["value"], abs=0.1)
    assert found["speech_s"] == pytest.approx(answers["total_s"]["value"] - answers["pause_s"]["value"], abs=0.15)
    assert abs(found["detected_syllables"] - answers["detected_syllables"]["value"]) <= answers["detected_syllables"]["tolerance"]
    assert found["f0_median_hz"] == pytest.approx(answers["f0_median"]["value"], abs=answers["f0_median"]["tolerance"])
    assert found["voiced_frames"] / found["speech_frames"] > 0.5 and found["low_reliability_utterances"] == 0
    assert found["below_default_floor"] == 0
    assert (found["sample_rate"], found["channels"], found["bits"], found["format_code"]) == (48000, 1, 16, 1)


def long_recording(wavs, gap_s=2.0) -> bytes:
    """합성 낭독(음절 21개)을 2초씩 쉬며 세 번 이어 붙인 녹음."""
    samples, rate = soundfile.read(io.BytesIO(wavs["syllable_train"]), dtype="float32")
    quiet = samples[: int(0.5 * rate)]  # 앞쪽 조용한 부분(배경 잡음)
    gap = np.tile(quiet, int(gap_s / 0.5))
    return wav_bytes(np.concatenate([samples, gap, samples, gap, samples]), rate, "PCM_16")


def test_long_recording_is_split_into_utterances_at_long_pauses(wavs):
    answers = ANSWERS["syllable_train"]
    data = long_recording(wavs)

    found = aihub.analyze_recording(io.BytesIO(data))

    assert found["utterances"] == 3
    assert found["speech_s"] == pytest.approx(3 * answers["total_s"]["value"], abs=0.5)
    assert abs(found["detected_syllables"] - 3 * answers["detected_syllables"]["value"]) <= 3 * answers["detected_syllables"]["tolerance"]
    assert found["duration_s"] == pytest.approx(len(soundfile.read(io.BytesIO(data))[0]) / 48000, abs=0.01)

    # 짧게 잘라 읽으면 경계에 걸친 발화가 나뉘지만, 합계는 거의 같다.
    cut = aihub.analyze_recording(io.BytesIO(data), block_s=5.0)
    assert cut["utterances"] > 3
    assert cut["speech_s"] == pytest.approx(found["speech_s"], rel=0.1)


def test_silence_gives_no_utterances_and_no_pitch():
    quiet = (1e-4 * np.random.default_rng(0).standard_normal(RATE * 3)).astype(np.float32)

    found = aihub.analyze_recording(io.BytesIO(wav_bytes(quiet, RATE, "PCM_16")))

    assert (found["utterances"], found["speech_frames"], found["detected_syllables"]) == (0, 0, 0)
    assert found["f0_median_hz"] is None and found["f0_p5_hz_low_floor"] is None


# ---- zip에서 여러 녹음 계산 ----


def label(transcript: str, sex="M", age="60") -> str:
    return json.dumps({"Transcript": transcript, "Patient_info": {"Sex": sex, "Age": age}}, ensure_ascii=False)


@pytest.fixture
def dataset(tmp_path, wavs):
    """AI Hub와 같은 구조의 작은 zip 두 개. 중풍 2개, 뇌부상 1개, 짝이 없는 음성 1개와 라벨 1개."""
    sentence = "우리는 저녁마다 노래를 부르며 마을을 걸었습니다"  # 21음절
    audio, labels = tmp_path / "audio.zip", tmp_path / "labels.zip"
    with zipfile.ZipFile(audio, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-01-M-60-KK.wav", wavs["syllable_train"])
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-02-M-60-KK.wav", wavs["paused_speech"])
        archive.writestr("12.뇌부상/ID-01-12-N-BBB-01-01-F-71-SU.wav", wavs["syllable_train"])
        archive.writestr("12.뇌부상/ID-01-12-N-CCC-01-01-F-50-SU.wav", wavs["syllable_train"])
    with zipfile.ZipFile(labels, "w") as archive:
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-01-M-60-KK.json", label(sentence))
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-02-M-60-KK.json", label(sentence + " n/ 걸었+ 2 번"))
        archive.writestr("12.뇌부상/ID-01-12-N-BBB-01-01-F-71-SU.json", label(sentence, "F", "71"))
        archive.writestr("12.뇌부상/ID-01-12-N-DDD-01-01-F-50-SU.json", label(sentence, "F", "50"))
    return audio, labels, tmp_path / "out" / "results.jsonl"


def run(argv):
    lines = []
    code = aihub.main([str(value) for value in argv], out=lines.append)
    return code, "\n".join(lines)


def test_recordings_with_both_audio_and_label_are_computed_and_saved(dataset):
    audio, labels, out = dataset

    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out])

    assert code == 0
    assert "짝이 맞는 녹음 3개(음성만 1개, 라벨만 1개). 이미 계산한 것 0개, 이번에 계산할 것 3개" in text
    assert "실패 0개" in text
    rows = {row["id"]: row for row in aihub.read_results(out)}
    assert sorted(rows) == ["ID-01-11-N-AAA-01-01-M-60-KK", "ID-01-11-N-AAA-01-02-M-60-KK", "ID-01-12-N-BBB-01-01-F-71-SU"]
    first = rows["ID-01-11-N-AAA-01-01-M-60-KK"]
    assert (first["group"], first["speaker"], first["sex"], first["age"]) == ("11.중풍", "AAA/M/60", "M", "60")
    assert (first["expected_syllables"], first["countable"], first["error"]) == (21, True, None)
    assert abs(first["detected_syllables"] - 21) <= 6 and first["utterances"] == 1
    second = rows["ID-01-11-N-AAA-01-02-M-60-KK"]
    assert (second["countable"], second["repeats"], second["tags"]) == (False, 1, 1)

    # 다시 돌리면 이미 있는 녹음은 건너뛴다.
    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out])
    assert code == 0 and "이미 계산한 것 3개, 이번에 계산할 것 0개" in text
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_group_and_limit_choose_what_to_compute(dataset):
    audio, labels, out = dataset

    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out, "--group", "11", "--limit", "1"])

    assert code == 0 and "짝이 맞는 녹음 2개" in text and "이번에 계산할 것 1개" in text
    assert [row["id"] for row in aihub.read_results(out)] == ["ID-01-11-N-AAA-01-01-M-60-KK"]

    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out, "--group", "11"])
    assert "이미 계산한 것 1개, 이번에 계산할 것 1개" in text and len(aihub.read_results(out)) == 2


def test_broken_recording_is_recorded_as_failed_and_tried_again(tmp_path, wavs):
    audio, labels, out = tmp_path / "audio.zip", tmp_path / "labels.zip", tmp_path / "results.jsonl"
    with zipfile.ZipFile(audio, "w") as archive:
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-01-M-60-KK.wav", b"not a wav file")
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-02-M-60-KK.wav", wavs["syllable_train"])
    with zipfile.ZipFile(labels, "w") as archive:
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-01-M-60-KK.json", label("가나다"))
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-02-M-60-KK.json", label("가나다"))

    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out])

    assert code == 1 and "실패(WavHeaderError" in text and "실패 1개" in text
    rows = {row["id"]: row for row in aihub.read_results(out)}
    assert rows["ID-01-11-N-AAA-01-01-M-60-KK"]["error"].startswith("WavHeaderError")
    assert rows["ID-01-11-N-AAA-01-02-M-60-KK"]["error"] is None
    # 실패한 녹음만 다시 계산한다.
    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out])
    assert code == 1 and "이미 계산한 것 1개, 이번에 계산할 것 1개" in text

    report = aihub.build_report(aihub.read_results(out))
    assert (report["failed"], report["all"]["recordings"]) == (1, 1)


def test_several_processes_give_the_same_rows(dataset):
    audio, labels, out = dataset
    alone = out.with_name("alone.jsonl")

    assert run(["run", "--audio", audio, "--labels", labels, "--out", alone])[0] == 0
    assert run(["run", "--audio", audio, "--labels", labels, "--out", out, "--workers", "2"])[0] == 0

    def comparable(path):
        return sorted(({k: v for k, v in row.items() if k != "seconds"} for row in aihub.read_results(path)), key=lambda row: row["id"])

    assert comparable(out) == comparable(alone)


def test_missing_files_are_input_errors(tmp_path, capsys):
    assert run(["run", "--audio", tmp_path / "a.zip", "--labels", tmp_path / "b.zip", "--out", tmp_path / "r.jsonl"])[0] == 2
    assert run(["report", tmp_path / "r.jsonl"])[0] == 2
    assert "파일이 없습니다" in capsys.readouterr().err


# ---- 요약 ----


def row(recording_id, group="11.중풍", speaker="AAA/M/60", sex="M", expected=100, detected=100, speech_s=20.0, **changes) -> dict:
    values = {
        "id": recording_id, "group": group, "speaker": speaker, "sex": sex, "age": "60", "expected_syllables": expected,
        "countable": True, "repeats": 0, "unclear": 0, "tags": 0, "algorithm_version": "1.0.0", "error": None,
        "duration_s": 600.0, "sample_rate": 44100, "channels": 1, "bits": 16, "format_code": 1, "utterances": 10,
        "skipped_short": 0, "speech_s": speech_s, "pause_s": 0.0, "detected_syllables": detected, "nuclei_errors": 0,
        "speech_frames": 2000, "voiced_frames": 1400, "low_reliability_utterances": 1, "speech_frames_low_floor": 2000,
        "voiced_frames_low_floor": 1500, "below_default_floor": 0, "f0_median_hz": 130.0, "f0_p5_hz_low_floor": 100.0,
        "f0_median_hz_low_floor": 130.0, "seconds": 1.0,
    }
    return {**values, **changes}


def test_summary_numbers_per_group():
    rows = [
        row("a", detected=80),  # 검출 ÷ 전사 0.80, 오차율 0.20
        row("b", detected=60, voiced_frames=800, low_reliability_utterances=6),  # 0.60, 0.40. 유성 비율 0.40
        row("c", detected=110, speech_s=5.0),  # 1.10, 0.10. 조음속도 20음절/초(이상값)
        row("d", detected=10, countable=False),  # 숫자·영문이 든 전사: 음절 비교에서 뺌
        row("e", group="12.뇌부상", speaker="BBB/F/71", sex="F", below_default_floor=300),  # 75 Hz 아래 20%
        row("f", error="WavHeaderError: 머리글"),
    ]

    report = aihub.build_report(rows)

    assert (report["failed"], report["algorithm_versions"]) == (1, ["1.0.0"])
    stroke = report["by_group"]["11.중풍"]
    assert (stroke["recordings"], stroke["speakers"], stroke["syllable_recordings"]) == (4, 1, 3)
    assert stroke["hours"] == pytest.approx(4 * 600 / 3600)
    assert stroke["ratio_median"] == pytest.approx(0.80)
    assert (stroke["error_median"], stroke["error_q1"], stroke["error_q3"]) == pytest.approx((0.20, 0.15, 0.30))
    assert stroke["over_reading_error_pct"] == pytest.approx(100 / 3)  # 0.40만 30% 이상
    assert stroke["rate_median"] == pytest.approx(5.0)
    assert stroke["implausible_rate_pct"] == pytest.approx(100 / 3)
    assert stroke["voiced_median"] == pytest.approx(0.70)
    assert stroke["low_reliability_utterance_pct"] == pytest.approx(100 * 9 / 40)
    assert stroke["low_voiced_recording_pct"] == pytest.approx(25.0)
    assert stroke["voiced_median_low_floor"] == pytest.approx(0.75)
    assert (stroke["speakers_below_floor"], stroke["male_below_floor"], stroke["male_speakers"]) == (0, 0, 1)
    injury = report["by_group"]["12.뇌부상"]
    assert (injury["speakers_below_floor"], injury["male_below_floor"], injury["male_speakers"]) == (1, 0, 0)
    assert report["all"]["recordings"] == 5 and report["formats"] == {"44100 Hz, 1채널, 16비트": 5}


@pytest.mark.parametrize(
    ("recording_id", "kind"),
    [
        ("ID-01-11-N-KNH-01-03-M-57-KK", "낱말 읽기"),
        ("ID-01-11-N-AMG-02-02-M-56-KK", "문장 읽기"),
        ("ID-01-11-N-AMG-03-M-56-KK", "자기소개 문장"),
        ("ID-01-15-N-NHJ-04-1000-M-67-KK", "문단 읽기"),
        ("ID-01-12-N-ABC-06-01-F-40-KK", "대화·명령 문장"),
        ("ID-01-11-N-JBJ-07-F-75-KK", "긴 글 읽기"),
        ("ID-01-11-N-JBJ-99-F-75-KK", "그 밖"),
        ("a", "그 밖"),
    ],
)
def test_task_kind_comes_from_the_task_number_in_the_recording_name(recording_id, kind):
    assert aihub.task_kind(recording_id) == kind


def test_report_is_also_split_by_task_kind_with_the_share_of_dropped_short_utterances():
    rows = [
        row("ID-01-11-N-AAA-02-01-M-60-KK", detected=90),
        row("ID-01-11-N-AAA-02-03-M-60-KK", detected=110, skipped_short=10),  # 쓴 조각 20개, 버린 조각 10개
        row("ID-01-11-N-AAA-04-M-60-KK", detected=100),
        row("ID-01-11-N-AAA-01-03-M-60-KK", detected=40, utterances=5, skipped_short=15),  # 낱말: 20개 중 15개를 버림
    ]

    report = aihub.build_report(rows)

    assert list(report["by_task"]) == ["낱말 읽기", "문장 읽기", "문단 읽기"]  # 과제 번호 순서
    assert [item["recordings"] for item in report["by_task"].values()] == [1, 2, 1]
    assert report["by_task"]["문장 읽기"]["short_utterance_pct"] == pytest.approx(100 * 10 / 30)
    assert report["by_task"]["낱말 읽기"]["short_utterance_pct"] == pytest.approx(75.0)
    assert report["by_task"]["낱말 읽기"]["error_median"] == pytest.approx(0.60)
    assert report["by_task"]["문단 읽기"]["short_utterance_pct"] == pytest.approx(0.0)
    text = aihub.render(report)
    assert "| 낱말 읽기 | 1 | 1 | 0.2 | 1 | 0.40 | 0.60 (0.60~0.60) | 100% | 5.00 | 0% | 75% |" in text


def test_speakers_are_grouped_by_how_often_the_transcript_is_marked():
    rows = [
        row("a", speaker="없음/M/60"),
        row("b", speaker="적음/M/60", repeats=1),  # 100음절에 1개
        row("c", speaker="적음/M/60", unclear=1, expected=300),  # 합쳐서 400음절에 2개
        row("d", speaker="많음/F/70", sex="F", repeats=5, unclear=5),
    ]

    levels = aihub.mark_levels(rows)

    assert levels == {"없음/M/60": "표시 없음", "적음/M/60": "표시 적음", "많음/F/70": "표시 많음"}
    report = aihub.build_report(rows)
    assert list(report["by_mark_level"]) == ["표시 없음", "표시 적음", "표시 많음"]
    assert [item["recordings"] for item in report["by_mark_level"].values()] == [1, 2, 1]


def test_report_is_a_markdown_table_or_json(tmp_path):
    path = tmp_path / "results.jsonl"
    rows = [row("a", detected=80), row("b", group="12.뇌부상", speaker="BBB/F/71", sex="F", detected=120)]
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + "\n", encoding="utf-8")

    code, text = run(["report", path])

    assert code == 0
    assert "알고리즘 버전 1.0.0" in text and "녹음 2개, 화자 2명, 0.3시간" in text
    assert "| 중풍 | 1 | 1 | 0.2 | 1 | 0.80 | 0.20 (0.20~0.20) | 0% | 5.00 | 0% | 0% |" in text
    assert "| 전체 | 2 | 2 | 0.3 | 2 | 1.00 | 0.20 (0.20~0.20) | 0% | 5.00 | 0% | 0% |" in text
    assert "| 그 밖 | 2 | 2 |" in text and "문장 읽기와 문단 읽기임" in text
    assert "| 뇌부상 | 0.70 | 10% | 0% | 0.75 | 0 | 0 / 0 |" in text
    assert "| 표시 없음 | 2 | 2 |" in text and "중증도가 아니라" in text
    assert "WAV 형식별 녹음 수: 44100 Hz, 1채널, 16비트 2개" in text

    code, text = run(["report", path, "--json"])
    assert code == 0 and json.loads(text)["by_group"]["11.중풍"]["ratio_median"] == pytest.approx(0.8)

    path.write_text("", encoding="utf-8")
    assert run(["report", path]) == (0, "결과가 없습니다.")
