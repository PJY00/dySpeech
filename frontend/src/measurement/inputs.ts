// 입력 장치 판단(순수 함수). 녹음기(recorder.ts)가 쓴다.

/**
 * 입력 장치 종류 이름(라이브러리 parseDeviceCategory)으로 유선 헤드셋인지 정한다
 * - "Wired Headset": 3.5 mm 헤드셋 → wired_headset
 * - "Other (22)", "Other (11)": 안드로이드 TYPE_USB_HEADSET·TYPE_USB_DEVICE(USB-C 헤드셋) → usb_headset
 * - "Bluetooth SCO" → bluetooth, "Built-in Mic" → built_in_mic
 */
export function classifyInputs(categories: string[]): { inputDevice: string; wiredHeadset: boolean } {
  const has = (test: (c: string) => boolean) => categories.some(test);
  if (has((c) => c === 'Wired Headset')) return { inputDevice: 'wired_headset', wiredHeadset: true };
  if (has((c) => /^Other \((22|11)\)$/.test(c))) return { inputDevice: 'usb_headset', wiredHeadset: true };
  if (has((c) => c.startsWith('Bluetooth'))) return { inputDevice: 'bluetooth', wiredHeadset: false };
  if (has((c) => c === 'Built-in Mic')) return { inputDevice: 'built_in_mic', wiredHeadset: false };
  return { inputDevice: (categories[0] ?? 'unknown').slice(0, 100), wiredHeadset: false };
}
