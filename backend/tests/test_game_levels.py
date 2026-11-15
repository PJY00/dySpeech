"""게임 단계표 점검 도구: 전사를 문장으로 나누기, 조각 찾기, 앱 판정 코드 연결(node), 요약을 확인한다. DB·S3는 쓰지 않는다.

앱 판정 코드를 실제로 부르는 테스트는 node와 frontend/node_modules가 있을 때만 돈다.
"""

import io
import json
import shutil
import zipfile
from functools import partial

import numpy as np
import pytest
import soundfile

from analysis import game_levels as tool
from fixtures.synthetic import generate

ANSWERS = json.loads(generate.ANSWERS.read_text(encoding="utf-8"))["files"]
LEVELS = [[1, 30, 0.6], [2, 25, 0.55], [3, 20, 0.5], [4, 15, 0.45], [5, 12, 0.4]]
TABLE = {"guide_levels": LEVELS, "up_at_pct": 80, "down_at_pct": 40}
SENTENCE = "우리는 저녁마다 노래를 부르며 마을을 걸었습니다"  # 21음절

needs_app_code = pytest.mark.skipif(
    shutil.which("node") is None or not (tool.FRONTEND / "node_modules" / "pitchy").exists(),
    reason="node 또는 frontend/node_modules가 없어 앱 판정 코드를 부를 수 없습니다",
)


@pytest.fixture(scope="module")
def wavs(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    assert generate.main(["--out", str(out)]) == 0
    return {path.stem: path.read_bytes() for path in out.glob("*.wav")}


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def repeated(wavs, times=3, gap_s=2.0) -> bytes:
    """합성 낭독(음절 21개)을 gap_s초씩 쉬며 이어 붙인 녹음."""
    samples, rate = soundfile.read(io.BytesIO(wavs["syllable_train"]), dtype="float32")
    gap = np.tile(samples[: int(0.5 * rate)], int(gap_s / 0.5))  # 앞쪽 조용한 부분(배경 잡음)
    parts = [samples]
    for _ in range(times - 1):
        parts += [gap, samples]
    return wav_bytes(np.concatenate(parts), rate)


class FakeDevice:
    """앱 판정 코드 자리에 넣는 것. 받은 조각을 적어 두고 정해진 값을 돌려준다."""

    def __init__(self):
        self.calls = []

    def __call__(self, clips, rate, floor):
        self.calls.append(([len(clip) / rate for clip in clips], floor))
        measured = {"speech_s": 4.0, "cv": 0.3, "nuclei": 18, "lead_db": -60.0, "t0_s": 0.02, "hop_s": 0.02, "hz": [None, 140.0, 141.0]}
        return {**TABLE, "clips": [dict(measured) for _ in clips]}


# ---- 전사를 문장으로 나누기 ----


def test_transcript_is_split_at_sentence_marks_and_wide_gaps():
    assert tool.sentence_syllables("문 열어. 문 열어? 나무가 많아!") == [3, 3, 5]
    assert tool.sentence_syllables("어머니는 예산 출신이십니다  제 위로는 형이 있습니다.") == [12, 10]  # 마침표 없이 두 칸 띄움
    assert tool.sentence_syllables("") == []


def test_marks_are_removed_before_counting_each_sentence():
    assert tool.sentence_syllables("올라+ 올라옵니다. n/ 네.") == [7, 1]  # 반복은 말한 것이라 셈, n/은 지움
    assert tool.sentence_syllables("(1시)/(한 시)에 가요. 네.") == [5, 1]  # 이중 전사는 발음 쪽


def test_sentence_with_digits_or_latin_letters_cannot_be_counted():
    assert tool.sentence_syllables("문 열어. 설날은 1 월 1 일입니다.") is None


# ---- 덩어리 경계 ----


def test_piece_near_the_end_of_a_block_is_held_for_the_next_block():
    early, late = [(100, 400)], [(880, 950)]  # 프레임(10 ms). 덩어리는 1000프레임

    ready, keep = tool.cut_buffer([early, late], 1000, last=False)

    assert ready == [early]  # 끝에서 1.5초 안쪽에 걸친 조각은 미룸
    assert keep == 880 - 50  # 미룬 조각의 0.5초 앞부터 넘김


def test_block_without_a_held_piece_keeps_its_last_second_and_a_half():
    assert tool.cut_buffer([[(100, 400)]], 1000, last=False) == ([[(100, 400)]], 850)


def test_last_block_and_endless_speech_are_not_held():
    groups = [[(100, 400)], [(880, 950)]]
    assert tool.cut_buffer(groups, 1000, last=True) == (groups, None)

    endless = [[(100, 6990)]]  # 쉬지 않고 69초: 넘길 부분이 60초를 넘음
    assert tool.cut_buffer(endless, 7000, last=False) == (endless, None)


def test_recording_read_in_short_blocks_gives_the_same_pieces(wavs):
    data = repeated(wavs)

    whole_device, cut_device = FakeDevice(), FakeDevice()
    whole = tool.analyze_recording(io.BytesIO(data), whole_device)
    cut = tool.analyze_recording(io.BytesIO(data), cut_device, block_s=5.0)  # 경계가 발화 중간에 걸림

    assert len(whole["pieces"]) == 3 and whole["skipped_short"] == 0
    assert len(cut["pieces"]) == 3  # 경계에 걸친 조각이 둘로 나뉘지 않음
    assert [piece["t"] for piece in cut["pieces"]] == pytest.approx([piece["t"] for piece in whole["pieces"]], abs=0.05)
    assert [piece["speech_s"] for piece in cut["pieces"]] == pytest.approx([piece["speech_s"] for piece in whole["pieces"]], abs=0.05)
    assert len(cut_device.calls) > 1 and sum(len(lengths) for lengths, _ in cut_device.calls) == 3
    assert cut["duration_s"] == whole["duration_s"]


# ---- 조각 계산 ----


def test_pieces_carry_server_values_and_what_the_app_code_returned(wavs):
    answers = ANSWERS["syllable_train"]
    device = FakeDevice()

    found = tool.analyze_recording(io.BytesIO(repeated(wavs, times=2)), device)

    assert (found["sample_rate"], found["guide_levels"], found["up_at_pct"], found["down_at_pct"]) == (48000, LEVELS, 80, 40)
    first, second = found["pieces"]
    assert first["speech_s"] == pytest.approx(answers["total_s"]["value"], abs=0.2)
    assert abs(first["nuclei"] - 21) <= answers["detected_syllables"]["tolerance"]
    assert second["t"] - first["t"] == pytest.approx(len(soundfile.read(io.BytesIO(wavs["syllable_train"]))[0]) / 48000 + 2.0, abs=0.1)
    assert (first["d_speech_s"], first["d_cv"], first["d_nuclei"], first["d_lead_db"]) == (4.0, 0.3, 18, -60.0)
    assert "begin_s" not in first

    # 앱 코드에는 조각 앞뒤 0.5초를 붙여 주고, 잡음 바닥으로 말소리 주변 소음(배경 잡음 −60 dBFS)을 준다
    lengths, floor = device.calls[0]
    assert lengths == pytest.approx([first["speech_s"] + 1.0, second["speech_s"] + 1.0], abs=0.1)
    assert floor == pytest.approx(-60.0, abs=2.0)
    assert first["ambient_db"] == pytest.approx(-60.0, abs=2.0) and first["lead_db"] == pytest.approx(-60.0, abs=2.0)


def test_ambient_level_is_used_when_the_recording_is_silenced_between_utterances(wavs):
    samples, rate = soundfile.read(io.BytesIO(wavs["syllable_train"]), dtype="float32")
    silence = np.zeros(int(3.0 * rate), dtype=np.float32)  # 발화 사이를 완전한 무음으로 지운 녹음
    device = FakeDevice()

    found = tool.analyze_recording(io.BytesIO(wav_bytes(np.concatenate([silence, samples, silence, samples, silence]), rate)), device)

    first = found["pieces"][0]
    assert first["floor_db"] < -100  # 하위 10백분위는 지워진 무음
    assert first["ambient_db"] == pytest.approx(-60.0, abs=3.0)  # 말소리 주변에 남은 배경 잡음
    assert device.calls[0][1] == pytest.approx(-60.0, abs=3.0)


def test_silent_recording_has_no_pieces_but_still_reads_the_app_table():
    quiet = (1e-4 * np.random.default_rng(0).standard_normal(16000 * 3)).astype(np.float32)
    device = FakeDevice()

    found = tool.analyze_recording(io.BytesIO(wav_bytes(quiet, 16000)), device)

    assert found["pieces"] == [] and found["guide_levels"] == LEVELS
    assert device.calls == [([], 0.0)]


def test_device_pitch_is_compared_with_server_pitch_frame_by_frame():
    server_times = np.arange(10) * 0.01  # 0.00~0.09초
    server_hz = np.array([np.nan, 100, 100, 100, 100, 100, 100, np.nan, np.nan, 100.0])
    device_times = np.array([0.02, 0.04, 0.06, 0.08, 0.09, 0.30])
    device_hz = np.array([100.0, np.nan, 200.0, 150.0, 100.0, 100.0])  # 맞음, 못 잡음, 한 옥타브 위, 서버는 무성, 발화 밖, 짝 없음
    in_speech = np.array([True, True, True, True, False, True])

    found = tool.compare_pitch(server_times, server_hz, device_times, device_hz, in_speech)

    assert found == {"f0_ref": 3, "f0_both": 2, "f0_gross": 1, "f0_dev_only": 2}
    assert tool.compare_pitch(np.empty(0), np.empty(0), device_times, device_hz, in_speech) == {"f0_ref": 0, "f0_both": 0, "f0_gross": 0, "f0_dev_only": 0}


# ---- 앱 판정 코드(node) ----


@needs_app_code
def test_app_code_measures_a_synthetic_reading(wavs):
    answers = ANSWERS["syllable_train"]

    found = tool.analyze_recording(io.BytesIO(wavs["syllable_train"]), partial(tool.run_bridge, tool.FRONTEND))

    assert found["guide_levels"] == LEVELS and (found["up_at_pct"], found["down_at_pct"]) == (80, 40)  # 앱 코드의 단계표
    (piece,) = found["pieces"]
    assert piece["d_speech_s"] == pytest.approx(answers["total_s"]["value"], abs=0.2)  # 쉬지 않는 낭독
    assert piece["d_speech_s"] == pytest.approx(piece["speech_s"], abs=0.2)  # 서버 발화 시간과 같은 수준
    assert 14 <= piece["d_nuclei"] <= 21  # 기기 검출기는 음절을 다 찾지 못함
    assert 0.2 < piece["d_cv"] < 0.6
    assert piece["d_lead_db"] == pytest.approx(piece["lead_db"], abs=1.0)  # 같은 구간의 기기 음량과 서버 음량
    assert piece["f0_ref"] > 100 and piece["f0_both"] / piece["f0_ref"] > 0.6
    assert piece["f0_gross"] / piece["f0_both"] < 0.05


@needs_app_code
def test_app_code_returns_only_the_table_for_no_clips():
    assert tool.run_bridge(tool.FRONTEND, [], 48000, -60.0) == {**TABLE, "clips": []}


@needs_app_code
def test_app_code_failure_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match="앱 판정 코드 실행에 실패"):
        tool.run_bridge(tmp_path, [np.zeros(4800, dtype=np.float32)], 48000, -60.0)  # 앱 코드가 없는 폴더


# ---- zip에서 계산 ----


def label(transcript: str) -> str:
    return json.dumps({"Transcript": transcript, "Patient_info": {"Sex": "M", "Age": "60"}}, ensure_ascii=False)


@pytest.fixture
def dataset(tmp_path, wavs):
    """AI Hub와 같은 구조의 작은 zip 두 개. 문장 읽기 2개와 낱말 읽기 1개."""
    audio, labels = tmp_path / "audio.zip", tmp_path / "labels.zip"
    with zipfile.ZipFile(audio, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("11.중풍/ID-01-11-N-AAA-02-01-M-60-KK.wav", repeated(wavs, times=2))
        archive.writestr("11.중풍/ID-01-11-N-AAA-04-M-60-KK.wav", wavs["paused_speech"])
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-03-M-60-KK.wav", wavs["syllable_train"])
    with zipfile.ZipFile(labels, "w") as archive:
        archive.writestr("11.중풍/ID-01-11-N-AAA-02-01-M-60-KK.json", label(f"{SENTENCE}. {SENTENCE}."))
        archive.writestr("11.중풍/ID-01-11-N-AAA-04-M-60-KK.json", label(f"{SENTENCE} 2 번"))
        archive.writestr("11.중풍/ID-01-11-N-AAA-01-03-M-60-KK.json", label("새 해 배 개"))
    return audio, labels, tmp_path / "out" / "results.jsonl"


def run(argv):
    lines = []
    code = tool.main([str(value) for value in argv], out=lines.append)
    return code, "\n".join(lines)


@needs_app_code
def test_only_sentence_and_paragraph_recordings_are_computed_and_saved(dataset):
    audio, labels, out = dataset

    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out])

    assert code == 0 and "이번에 계산할 것 2개" in text  # 낱말 읽기는 대상이 아님
    rows = {row["id"]: row for row in tool.read_results(out)}
    assert set(rows) == {"ID-01-11-N-AAA-02-01-M-60-KK", "ID-01-11-N-AAA-04-M-60-KK"}
    sentences = rows["ID-01-11-N-AAA-02-01-M-60-KK"]
    assert (sentences["task"], sentences["sentences"], sentences["speaker"], sentences["error"]) == ("문장 읽기", [21, 21], "AAA/M/60", None)
    assert len(sentences["pieces"]) == 2 and sentences["pieces"][0]["d_nuclei"] >= 14
    paragraph = rows["ID-01-11-N-AAA-04-M-60-KK"]
    assert (paragraph["task"], paragraph["sentences"]) == ("문단 읽기", None)  # 숫자가 든 전사
    assert len(paragraph["pieces"]) == 1 and paragraph["pieces"][0]["pause_s"] > 1.0  # 1초보다 짧은 휴지는 같은 조각

    code, text = run(["run", "--audio", audio, "--labels", labels, "--out", out])
    assert code == 0 and "이미 계산한 것 2개, 이번에 계산할 것 0개" in text

    code, text = run(["report", out])
    assert code == 0 and "녹음 2개, 화자 1명" in text and "1단계 ±30%·변동계수 0.6 이하" in text
    assert "| 중풍 | 3 |" in text  # 조각 3개


def test_missing_files_are_input_errors(tmp_path, dataset, capsys):
    audio, labels, out = dataset
    assert run(["run", "--audio", tmp_path / "a.zip", "--labels", labels, "--out", out])[0] == 2
    assert run(["run", "--audio", audio, "--labels", labels, "--out", out, "--frontend", tmp_path])[0] == 2  # 앱 코드가 없는 폴더
    assert run(["report", tmp_path / "r.jsonl"])[0] == 2
    assert "파일이 없습니다" in capsys.readouterr().err


# ---- 요약 ----


def piece(speech_s=3.0, nuclei=12, d_speech_s=3.0, d_cv=0.3, d_nuclei=8, **changes) -> dict:
    values = {
        "t": 1.0, "speech_s": speech_s, "pause_s": 0.0, "nuclei": nuclei, "floor_db": -90.0, "ambient_db": -50.0, "lead_db": -50.0,
        "d_speech_s": d_speech_s, "d_cv": d_cv, "d_nuclei": d_nuclei, "d_lead_db": -50.2,
        "f0_ref": 100, "f0_both": 60, "f0_gross": 6, "f0_dev_only": 15,
    }
    return {**values, **changes}


def recording(recording_id, pieces, group="11.중풍", speaker="AAA/M/60", sentences=None, **changes) -> dict:
    values = {
        "id": recording_id, "group": group, "speaker": speaker, "sex": "M", "age": "60", "task": "문장 읽기", "sentences": sentences,
        "algorithm_version": "1.0.0", "error": None, "duration_s": 600.0, "sample_rate": 44100, "skipped_short": 1, "pieces": pieces, **TABLE, "seconds": 1.0,
    }
    return {**values, **changes}


def test_pieces_are_paired_with_sentences_only_when_counts_and_lengths_agree():
    lengths = [1.0, 2.0, 3.0, 4.0, 5.0]
    pieces = [piece(speech_s=length) for length in lengths]

    assert tool.matched_syllables(recording("a", pieces, sentences=[5, 10, 15, 20, 25])) == [5, 10, 15, 20, 25]
    assert tool.matched_syllables(recording("b", pieces, sentences=[5, 10, 15, 20])) is None  # 수가 다름
    assert tool.matched_syllables(recording("c", pieces, sentences=[25, 5, 20, 10, 15])) is None  # 길이와 맞지 않음
    assert tool.matched_syllables(recording("d", pieces[:4], sentences=[5, 10, 15, 20])) is None  # 문장 5개 미만
    assert tool.matched_syllables(recording("e", pieces, sentences=None)) is None  # 셀 수 없는 전사
    assert tool.matched_syllables(recording("f", pieces, sentences=[10] * 5)) is None  # 음절 수가 모두 같아 견줄 수 없음


def test_flattened_pieces_know_their_syllables_and_whether_they_are_like_game_sentences():
    rows = [
        recording("a", [piece(speech_s=length) for length in (1.0, 2.0, 3.0, 4.0, 5.0)], sentences=[5, 10, 15, 20, 25]),
        recording("b", [piece(nuclei=12), piece(nuclei=None), piece(speech_s=0.8), piece(d_speech_s=0.0)]),
    ]

    pieces = tool.flatten(rows)

    assert [(item["true"], item["count"], item["usable"]) for item in pieces[:5]] == [
        (5, 5, False),  # 실제 음절 수를 앎. 6음절 미만
        (10, 10, True),
        (15, 15, True),
        (20, 20, True),
        (25, 25, False),  # 20음절 초과
    ]
    assert [(item["true"], item["count"], item["usable"]) for item in pieces[5:]] == [
        (None, 12, True),  # 모르면 서버 검출 음절 수
        (None, None, False),
        (None, 12, False),  # 발화 1초 미만
        (None, 12, False),  # 앱 코드가 발화를 못 찾음
    ]
    assert {item["id"] for item in pieces[5:]} == {"b"} and pieces[0]["speaker"] == "AAA/M/60"


def test_cv_limits_are_counted_with_the_game_rule():
    values = [0.30, 0.42, 0.47, 0.52, 0.58, 0.70, None, None, 0.35, 0.35]
    pieces = tool.flatten([recording("a", [piece(d_cv=value) for value in values])])

    found = tool.cv_summary(pieces, LEVELS, 80, 40)

    assert (found["pieces"], found["speakers"]) == (10, 1)
    assert found["no_cv_pct"] == pytest.approx(20.0)
    assert found["cv_median"] == pytest.approx(np.median([0.30, 0.42, 0.47, 0.52, 0.58, 0.70, 0.35, 0.35]))
    assert [level["pass_pct"] for level in found["levels"]] == pytest.approx([90, 80, 70, 60, 50])  # 구하지 못한 2개는 통과
    assert [level["measured_pass_pct"] for level in found["levels"]] == pytest.approx([87.5, 75, 62.5, 50, 37.5])
    first, last = found["levels"][0], found["levels"][-1]
    assert (first["speakers"], first["share_median"], first["up_pct"], first["down_pct"]) == (1, pytest.approx(90.0), 100.0, 0.0)
    assert (last["share_median"], last["up_pct"], last["down_pct"]) == (pytest.approx(50.0), 0.0, 0.0)


def test_speakers_with_few_pieces_are_left_out_of_speaker_shares():
    pieces = tool.flatten(
        [
            recording("a", [piece(d_cv=0.3)] * 10, speaker="많음/M/60"),
            recording("b", [piece(d_cv=0.9)] * 9, speaker="적음/M/60"),
            recording("c", [piece(d_cv=0.9)] * 10, speaker="못함/F/70"),
        ]
    )

    found = tool.speaker_shares(pieces, lambda item: item["d_cv"] <= 0.6, 80, 40)

    assert found["speakers"] == 2  # 조각 9개인 화자는 뺌
    assert (found["share_median"], found["up_pct"], found["down_pct"]) == (pytest.approx(50.0), 50.0, 50.0)


def test_cv_is_grouped_by_how_many_syllables_the_device_found():
    pieces = tool.flatten(
        [recording("a", [piece(nuclei=10, d_nuclei=5, d_cv=0.7), piece(nuclei=10, d_nuclei=7, d_cv=0.5), piece(nuclei=10, d_nuclei=9, d_cv=0.3), piece(nuclei=10, d_nuclei=12, d_cv=None)])]
    )

    found = tool.detection_summary(pieces, LEVELS)

    assert [(item["low"], item["high"], item["pieces"]) for item in found] == [(0.0, 0.6, 1), (0.6, 0.8, 1), (0.8, 1.0, 1), (1.0, None, 0)]
    assert [item["cv_median"] for item in found] == [0.7, 0.5, 0.3, None]
    assert [(item["first_pass_pct"], item["last_pass_pct"]) for item in found[:3]] == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]


def test_methods_are_compared_per_piece_and_pitch_frames_are_summed():
    pieces = tool.flatten([recording("a", [piece(speech_s=2.0, d_speech_s=1.8, nuclei=10, d_nuclei=4), piece(speech_s=4.0, d_speech_s=4.4, nuclei=None, floor_db=-55.0)])])

    found = tool.method_summary(pieces)

    assert found["pieces"] == 2
    assert found["speech_ratio_median"] == pytest.approx(1.0)  # 0.9와 1.1
    assert found["nuclei_ratio_median"] == pytest.approx(0.4)  # 서버 검출이 없는 조각은 뺌
    assert found["level_gap_median"] == pytest.approx(-0.2)
    assert found["ambient_gap_median"] == pytest.approx(22.5) and found["gated_pct"] == pytest.approx(50.0)  # 40 dB와 5 dB
    assert found["f0_tracked_pct"] == pytest.approx(60.0)
    assert found["f0_gross_pct"] == pytest.approx(10.0)
    assert found["f0_device_only_pct"] == pytest.approx(20.0)


def test_pitch_tracking_is_summarized_per_speaker():
    pieces = tool.flatten(
        [
            recording("a", [piece(f0_ref=100, f0_both=80), piece(f0_ref=100, f0_both=60)], speaker="가/M/60"),
            recording("b", [piece(f0_ref=200, f0_both=60)], speaker="나/F/70"),
            recording("c", [piece(f0_ref=0, f0_both=0)], speaker="다/F/70"),
        ]
    )

    found = tool.tracking_summary(pieces)

    assert found["speakers"] == 2  # 유성 프레임이 없는 화자는 뺌
    assert found["tracked_median"] == pytest.approx(50.0)  # 70%와 30%
    assert found["below_coverage_pct"] == pytest.approx(50.0)


def known_pieces(rates, speaker="AAA/M/60", cv=0.3, nuclei=10) -> list[dict]:
    """실제 음절 수 10개, 기기 발화 시간 = 10 ÷ 말속도인 조각들."""
    items = [piece(d_speech_s=10 / rate, d_cv=cv, nuclei=nuclei) for rate in rates]
    return [{**item, "id": "a", "group": "11.중풍", "speaker": speaker, "true": 10, "count": 10, "usable": True} for item in items]


def test_rate_tolerance_uses_true_syllables_and_the_speakers_own_median():
    rates = [4.0] * 6 + [4.4, 4.6, 4.9, 5.4]  # 중앙값 4.0. 상대 편차 0, 10, 15, 22.5, 35%
    pieces = known_pieces(rates) + known_pieces([5.0] * 9, speaker="적음/F/70")

    found = tool.rate_summary(pieces, LEVELS, 80, 40)

    assert (found["speakers"], found["pieces"], found["enough"]) == (1, 10, False)  # 조각 9개인 화자는 뺌. 화자 30명 미만
    assert found["rate_median"] == pytest.approx(4.0) and found["spread_median"] == pytest.approx(0.0)
    assert [level["median_pass_pct"] for level in found["levels"]] == pytest.approx([90, 90, 80, 80, 70])
    assert [level["both_pass_pct"] for level in found["levels"]] == pytest.approx([90, 90, 80, 80, 70])  # 변동계수 0.3은 모든 단계에서 통과
    assert found["levels"][0]["up_pct"] == 100.0 and found["levels"][-1]["up_pct"] == 0.0
    assert found["server_error_median"] == pytest.approx(0.0) and found["device_error_median"] == pytest.approx(0.2)  # 기기 음절 핵 8개
    assert found["server_error_30_pct"] == pytest.approx(0.0)


def test_rate_tolerance_against_a_single_other_reading_is_stricter():
    pieces = known_pieces([4.0] * 5 + [5.0] * 5)  # 중앙값 4.5: 모두 ±12% 안. 서로를 기준으로 두면 25%·20% 벗어나는 쌍이 있음

    found = tool.rate_summary(pieces, LEVELS, 80, 40)

    assert [level["median_pass_pct"] for level in found["levels"]] == pytest.approx([100] * 5)
    # 90쌍 가운데 같은 속도끼리 40쌍은 늘 통과. 4.0을 5.0 기준으로 보면 −20%, 5.0을 4.0 기준으로 보면 +25%
    assert [level["single_pass_pct"] for level in found["levels"]] == pytest.approx([100, 100, 100 * 65 / 90, 100 * 40 / 90, 100 * 40 / 90])


def test_rate_from_detected_syllables_shows_what_detection_error_costs():
    pieces = known_pieces([4.0] * 10)  # 실제 말속도는 모두 같음
    for index, item in enumerate(pieces):
        item["nuclei"] = 10 if index < 6 else 6  # 검출 음절 수가 4개 조각에서 40% 모자람

    found = tool.rate_summary(pieces, LEVELS, 80, 40)

    assert [level["median_pass_pct"] for level in found["levels"]] == pytest.approx([100] * 5)
    assert [level["detected_pass_pct"] for level in found["levels"]] == pytest.approx([60] * 5)
    assert found["server_error_median"] == pytest.approx(0.0) and found["server_error_30_pct"] == pytest.approx(40.0)


def test_cv_limit_is_applied_together_with_the_rate_tolerance():
    pieces = known_pieces([4.0] * 10, cv=0.52)

    found = tool.rate_summary(pieces, LEVELS, 80, 40)

    assert [level["both_pass_pct"] for level in found["levels"]] == pytest.approx([100, 100, 0, 0, 0])  # 0.52는 3단계(0.5)부터 넘지 못함


def test_report_counts_recordings_pieces_and_matching():
    lengths = [1.0, 2.0, 3.0, 4.0, 5.0]
    rows = [
        recording("a", [piece(speech_s=length, d_speech_s=length) for length in lengths], sentences=[4, 8, 12, 16, 20]),
        recording("b", [piece()] * 3, group="12.뇌부상", speaker="BBB/F/71", sentences=[10, 10]),
        recording("c", [], sentences=None),
        recording("d", [], error="WavHeaderError: 머리글"),
    ]

    report = tool.build_report(rows)

    assert (report["failed"], report["recordings"], report["speakers"], report["pieces"], report["skipped_short"]) == (1, 3, 2, 8, 3)
    assert report["hours"] == pytest.approx(0.5) and report["usable_pieces"] == 7  # 4음절 조각만 빠짐
    assert report["matching"] == {"candidates": 2, "same_count": 1, "matched": 1, "speakers": 1, "pieces": 5}
    assert list(report["cv"]) == ["중풍", "뇌부상", "전체"] and report["cv"]["전체"]["pieces"] == 7
    assert report["methods"]["뇌부상"]["pieces"] == 3 and report["tracking"]["전체"]["speakers"] == 2
    assert (report["rate"]["speakers"], report["rate"]["enough"]) == (0, False)  # 실제 음절 수를 아는 조각이 화자당 10개 미만


def test_report_is_a_markdown_table_or_json(tmp_path):
    path = tmp_path / "results.jsonl"
    rows = [recording("a", [piece(d_cv=value) for value in (0.3, 0.5, None, 0.7)])]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    code, text = run(["report", path])

    assert code == 0
    assert "녹음 1개, 화자 1명, 0.2시간" in text and "발화 조각 4개(짧아서 버린 조각 1개)" in text
    assert "5단계 ±12%·변동계수 0.4 이하" in text and "80% 이상이면 단계를 올리고 40% 이하이면 내림" in text
    assert "| 중풍 | 4 | 1.00 (1.00~1.00) | 0.67 | -0.20 | 40.0 | 100% | 60% | 10.0% | 20% |" in text
    assert "| 중풍 | 4 | 1 | 25% | 0.50 (0.40~0.60) | 75% | 75% | 75% | 50% | 50% |" in text
    assert "이 자료로는 말속도 허용 폭을 정할 수 없음" in text
    assert "| 전체 | 1 | 60% (60~60) | 60% | 0% |" in text

    code, text = run(["report", path, "--json"])
    assert code == 0 and json.loads(text)["cv"]["중풍"]["levels"][0]["cv_max"] == 0.6

    path.write_text("", encoding="utf-8")
    assert run(["report", path]) == (0, "결과가 없습니다.")


def test_results_from_different_app_tables_are_not_mixed():
    rows = [recording("a", [piece()]), recording("b", [piece()], guide_levels=[[1, 40, 0.7]])]
    with pytest.raises(ValueError, match="단계표가 다릅니다"):
        tool.build_report(rows)
