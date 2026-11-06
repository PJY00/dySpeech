from dataclasses import dataclass

from app.schemas.common import DesiredDirection, Metric, Unit


@dataclass(frozen=True)
class MetricSpec:
    unit: Unit
    desired_direction: DesiredDirection
    used_for_progress: bool = True


# 바람직한 방향은 초기값이다. 팀과 확인한 뒤 확정한다.
METRICS: dict[Metric, MetricSpec] = {
    Metric.max_phonation_time: MetricSpec(Unit.seconds, DesiredDirection.up),
    # 기울기는 보통 음수이고, 0에 가까울수록 좋다.
    Metric.loudness_decay_slope: MetricSpec(Unit.db_per_second, DesiredDirection.up),
    Metric.mean_level_change: MetricSpec(Unit.db, DesiredDirection.none),
    Metric.articulation_rate: MetricSpec(Unit.syllables_per_second, DesiredDirection.none),
    Metric.speech_rate: MetricSpec(Unit.syllables_per_second, DesiredDirection.none),
    Metric.pause_ratio: MetricSpec(Unit.percent, DesiredDirection.none),
    Metric.f0_sd_semitones: MetricSpec(Unit.semitones, DesiredDirection.up),
    Metric.pitch_range_semitones: MetricSpec(Unit.semitones, DesiredDirection.up),
    # 보조 지표. 진전도에는 쓰지 않는다.
    Metric.sustained_level: MetricSpec(Unit.dbfs, DesiredDirection.none, used_for_progress=False),
    Metric.f0_median: MetricSpec(Unit.hertz, DesiredDirection.none, used_for_progress=False),
    Metric.noise_floor: MetricSpec(Unit.dbfs, DesiredDirection.none, used_for_progress=False),
}

# 진전도 응답에 항상 이 순서로 모두 넣는다.
PROGRESS_METRICS: list[Metric] = [metric for metric, spec in METRICS.items() if spec.used_for_progress]
