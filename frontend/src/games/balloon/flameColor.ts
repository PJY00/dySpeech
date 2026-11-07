// 불꽃 색: heat(0~1) → 실제 불꽃 온도(흑체 복사)에 가까운 색 (순수 함수)
// 온도가 오를수록 어두운 빨강 → 주황 → 노랑 → 흰색 → 푸른 흰색으로 변한다.

type Stop = { heat: number; kelvin: number; rgb: [number, number, number] };

// 색은 해당 온도의 흑체 복사 색을 화면용으로 근사한 값
const STOPS: Stop[] = [
  { heat: 0.0, kelvin: 800, rgb: [139, 26, 0] }, //   어두운 빨강 (겨우 붉게 빛남)
  { heat: 0.25, kelvin: 1000, rgb: [232, 64, 12] }, // 빨강-주황 (촛불 가장자리)
  { heat: 0.5, kelvin: 1400, rgb: [255, 154, 31] }, // 주황 (일반적인 촛불)
  { heat: 0.7, kelvin: 1800, rgb: [255, 210, 74] }, // 노랑
  { heat: 0.85, kelvin: 3000, rgb: [255, 243, 196] }, // 흰빛 도는 노랑
  { heat: 1.0, kelvin: 6500, rgb: [188, 217, 255] }, // 푸른 흰색 (가장 뜨거운 불꽃)
];

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x));
}

function toHex(rgb: number[]): string {
  return (
    '#' +
    rgb
      .map((v) => Math.round(v).toString(16).padStart(2, '0'))
      .join('')
  );
}

function segment(heat: number): { a: Stop; b: Stop; t: number } {
  const h = clamp01(heat);
  for (let i = 0; i < STOPS.length - 1; i++) {
    const a = STOPS[i];
    const b = STOPS[i + 1];
    if (h <= b.heat) {
      return { a, b, t: (h - a.heat) / (b.heat - a.heat) };
    }
  }
  return { a: STOPS[STOPS.length - 2], b: STOPS[STOPS.length - 1], t: 1 };
}

/** heat(0~1) → 불꽃 겉부분 색 (#rrggbb) */
export function flameColor(heat: number): string {
  const { a, b, t } = segment(heat);
  return toHex(a.rgb.map((v, i) => lerp(v, b.rgb[i], t)));
}

/** heat(0~1) → 불꽃 속(심지 쪽) 색: 겉 색을 흰색 쪽으로 밝게 */
export function flameCoreColor(heat: number): string {
  const { a, b, t } = segment(heat);
  return toHex(
    a.rgb.map((v, i) => {
      const outer = lerp(v, b.rgb[i], t);
      return lerp(outer, 255, 0.55);
    }),
  );
}

/** heat(0~1) → 표시용 불꽃 온도(K) */
export function flameKelvin(heat: number): number {
  const { a, b, t } = segment(heat);
  return Math.round(lerp(a.kelvin, b.kelvin, t));
}
